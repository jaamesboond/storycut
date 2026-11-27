"""Frame analysis for the 9:16 render: facecam auto-detect, face tracking, letterbox
detection and a thumbnail picker.

Everything runs on the CPU on a handful of small frames (480 px wide), so it costs a
few seconds per clip. numpy is required. OpenCV (``pip install opencv-python-headless``)
is optional: with it, faces are found with OpenCV's bundled Haar cascade; without it
storycut falls back to a skin-colour heuristic, which works for most webcams but is
rougher (see README).

Coordinates returned to callers are in SOURCE pixels as (x, y, w, h).
"""

from __future__ import annotations

import json
import subprocess
from typing import List, Optional, Sequence, Tuple

try:  # numpy is a hard requirement for this module only
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

Box = Tuple[int, int, int, int]  # x, y, w, h

_CV2 = None


def cv2_module():
    """OpenCV if importable, else None (cached)."""
    global _CV2
    if _CV2 is None:
        try:
            import cv2  # type: ignore
            _CV2 = cv2
        except Exception:
            _CV2 = False
    return _CV2 or None


# --------------------------------------------------------------------------- sampling

def probe_size(src: str) -> Tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", src], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def sample_frames(src: str, start: float, dur: float, fps: float = 1.0, width: int = 480):
    """Decode a few RGB frames. Returns (frames[N,h,w,3] uint8, times[N], scale) where
    scale converts analysis pixels to source pixels."""
    sw, sh = probe_size(src)
    w = width
    h = int(round(sh * w / sw / 2)) * 2
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", src,
           "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (w * h * 3)
    frames = np.frombuffer(raw[: n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)
    times = np.arange(n) / fps
    return frames, times, sw / w


def sample_seek(src: str, start: float, dur: float, n: int = 12, width: int = 960):
    """n frames spread over the window, each decoded with its own fast seek (much cheaper
    than decoding the whole window for a handful of frames)."""
    sw, sh = probe_size(src)
    h = int(round(sh * width / sw / 2)) * 2
    out, times = [], []
    for i in range(n):
        t = (i + 0.5) * dur / n
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start + t):.3f}", "-i", src,
                              "-frames:v", "1", "-vf", f"scale={width}:{h}", "-f", "rawvideo", "-pix_fmt",
                              "rgb24", "-"], capture_output=True, check=True).stdout
        if len(raw) >= width * h * 3:
            out.append(np.frombuffer(raw[: width * h * 3], dtype=np.uint8).reshape(h, width, 3))
            times.append(t)
    return np.stack(out), np.array(times), sw / width


def gray(frames):
    f = frames.astype(np.float32)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


# --------------------------------------------------------------------------- letterbox

def content_box(frames, thr: float = 20.0) -> Box:
    """Area that is not letterbox/pillarbox bars (bright-row scan). Analysis pixels."""
    g = gray(frames)
    # median over frames: a row counts as a bar when it is dark in most samples, so a
    # flash or a short full-frame shot does not hide the bars of a letterboxed stream
    rows = np.median(g.mean(axis=2), axis=0)
    cols = np.median(g.mean(axis=1), axis=0)
    ys = np.where(rows > thr)[0]
    xs = np.where(cols > thr)[0]
    h, w = g.shape[1:]
    if len(ys) == 0 or len(xs) == 0:
        return 0, 0, w, h
    return int(xs[0]), int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1)


def trim_dark(frames, box: Box, thr: float = 22.0, max_frac: float = 0.15) -> Box:
    """Shrink a box while its outer row/column is a dark or flat strip, so the crop sits
    on the TRUE content edges (no thin black/credit strips)."""
    g = gray(frames)
    x, y, w, h = box

    def flat(line):  # line: [N, L] over frames
        m = line.mean(axis=0)
        return m.mean() < thr or m.std() < 4.0

    lim_x, lim_y = int(w * max_frac), int(h * max_frac)
    for _ in range(lim_x):
        if w > 8 and flat(g[:, y:y + h, x]):
            x += 1; w -= 1
        else:
            break
    for _ in range(lim_x):
        if w > 8 and flat(g[:, y:y + h, x + w - 1]):
            w -= 1
        else:
            break
    for _ in range(lim_y):
        if h > 8 and flat(g[:, y, x:x + w]):
            y += 1; h -= 1
        else:
            break
    for _ in range(lim_y):
        if h > 8 and flat(g[:, y + h - 1, x:x + w]):
            h -= 1
        else:
            break
    return x, y, w, h


# --------------------------------------------------------------------------- faces

def detect_faces(frame_rgb) -> List[Box]:
    """Faces in one RGB frame (analysis pixels). OpenCV Haar if available, else skin blobs."""
    cv2 = cv2_module()
    if cv2 is not None:
        det = _haar()
        g = cv2.cvtColor(np.ascontiguousarray(frame_rgb), cv2.COLOR_RGB2GRAY)
        g = cv2.equalizeHist(g)
        found = det.detectMultiScale(g, scaleFactor=1.08, minNeighbors=4, minSize=(18, 18))
        return [tuple(int(v) for v in f) for f in found]
    return skin_faces(frame_rgb)


_HAAR = None


def _haar():
    global _HAAR
    if _HAAR is None:
        cv2 = cv2_module()
        _HAAR = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _HAAR


def skin_mask(frame_rgb):
    f = frame_rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    cr = 0.5 * r - 0.4187 * g - 0.0813 * b + 128
    cb = -0.1687 * r - 0.3313 * g + 0.5 * b + 128
    return (cr > 138) & (cr < 175) & (cb > 85) & (cb < 128) & (r > 60)


def skin_faces(frame_rgb, min_px: int = 120, max_w_frac: float = 0.22) -> List[Box]:
    """Fallback face finder: the biggest compact skin blob (no OpenCV needed)."""
    m = skin_mask(frame_rgb)
    blobs = _blobs(m, min_px)
    out = []
    for (x, y, w, h, n) in blobs[:3]:
        if 0.5 < w / max(h, 1) < 1.6 and n > 0.35 * w * h and w < max_w_frac * m.shape[1]:
            out.append((x, y, w, h))
    return out


def _blobs(mask, min_px: int):
    """Connected components on a coarse 4 px grid (pure numpy + python, small images only)."""
    hh, ww = mask.shape
    s = 4
    gm = mask[: hh // s * s, : ww // s * s].reshape(hh // s, s, ww // s, s).mean(axis=(1, 3)) > 0.5
    seen = np.zeros_like(gm, dtype=bool)
    res = []
    for yy, xx in zip(*np.nonzero(gm)):
        if seen[yy, xx]:
            continue
        stack = [(yy, xx)]
        seen[yy, xx] = True
        pts = []
        while stack:
            a, b = stack.pop()
            pts.append((a, b))
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                na, nb = a + da, b + db
                if 0 <= na < gm.shape[0] and 0 <= nb < gm.shape[1] and gm[na, nb] and not seen[na, nb]:
                    seen[na, nb] = True
                    stack.append((na, nb))
        n = len(pts) * s * s
        if n < min_px:
            continue
        ys = [p[0] for p in pts]; xs = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs) * s, (max(xs) + 1) * s, min(ys) * s, (max(ys) + 1) * s
        res.append((x0, y0, x1 - x0, y1 - y0, n))
    res.sort(key=lambda r: -r[4])
    return res


def face_clusters(frames, min_share: float = 0.3) -> List[Tuple[Box, int]]:
    """Faces that stay in about the same place across the samples, as (median box, hits),
    most frequent first. A game character can also stand still for a while, so callers
    should rank these with persistent_score() too."""
    boxes = []
    for f in frames:
        boxes += detect_faces(f)
    if not boxes:
        return []
    arr = np.array(boxes, dtype=np.float32)
    cx = arr[:, 0] + arr[:, 2] / 2
    cy = arr[:, 1] + arr[:, 3] / 2
    left = np.ones(len(arr), dtype=bool)
    out = []
    while left.any():
        best, best_n = None, 0
        for i in np.nonzero(left)[0]:
            d = np.hypot(cx - cx[i], cy - cy[i])
            sel = left & (d < max(arr[i, 2], 12) * 0.8)
            if sel.sum() > best_n:
                best_n, best = int(sel.sum()), sel
        left &= ~best
        if best_n < max(2, min_share * len(frames)):
            break
        m = np.median(arr[best], axis=0)
        out.append(((int(m[0]), int(m[1]), int(m[2]), int(m[3])), best_n))
    return out


def persistent_score(frames, box: Box, edge_thr: float = 7.0) -> float:
    """Share of pixels around a face whose edges do not move between samples. High for a
    streamer camera overlay, low for a game character (the scene behind them changes)."""
    gx, gy = _persistent_edges(frames)
    e = np.maximum(gx[:-1, :], gy[:, :-1])
    x, y, w, h = box
    x0, y0 = max(0, x - w // 2), max(0, y - h // 2)
    reg = e[y0:y + h + h // 2, x0:x + w + w // 2]
    return float((reg > edge_thr).mean()) if reg.size else 0.0


def motion_score(frames, box: Box) -> float:
    """Mean temporal std inside a box. A live face moves a little (talking, blinking);
    a face painted on a sign or a frozen scene does not."""
    g = gray(frames)
    x, y, w, h = box
    reg = g[:, max(y, 0):y + h, max(x, 0):x + w]
    return float(reg.std(axis=0).mean()) if reg.size else 0.0


def edge_prior(frames, box: Box) -> float:
    """Streamer cams sit near an edge or corner of the picture: 1.0 there, 0.4 mid-screen."""
    h, w = frames.shape[1:3]
    x, y, bw, bh = box
    cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
    dx, dy = min(cx, 1 - cx), min(cy, 1 - cy)
    near = min(dx, dy) < 0.15 or (dx < 0.3 and dy < 0.3)
    return 1.0 if near else 0.4


def stable_face(frames, min_share: float = 0.15, min_persist: float = 0.05, min_motion: float = 1.5) -> Optional[Box]:
    """The streamer's face: frequent, alive (moves a little), on a still overlay, near an
    edge of the picture. None if no face qualifies."""
    best, best_s = None, 0.0
    for b, n in face_clusters(frames, min_share):
        per, mot = persistent_score(frames, b), motion_score(frames, b)
        if per < min_persist or mot < min_motion:
            continue
        sc = n * per * edge_prior(frames, b)
        if sc > best_s:
            best, best_s = b, sc
    return best


# --------------------------------------------------------------------------- facecam box

def _persistent_edges(frames):
    """Edge strength that holds across samples (median over frames): overlay borders and
    a streamer sitting in front of a camera stay put while game edges move around."""
    g = gray(frames)
    gx = np.median(np.abs(np.diff(g, axis=2)), axis=0)  # [h, w-1] edge between x and x+1
    gy = np.median(np.abs(np.diff(g, axis=1)), axis=0)  # [h-1, w]
    return gx, gy


def facecam_rect(frames, face: Box, content: Optional[Box] = None, edge_thr: float = 7.0,
                 cell: int = 8, density: float = 0.2, reach: float = 5.0) -> Box:
    """Box around the streamer's camera, whatever its shape.

    Edges that stay put across frames (while the game keeps moving) belong to overlays.
    We flood-fill the patch of persistent edges that contains the face: for a framed
    webcam that patch is the frame and everything inside it, for a green-screen cut-out
    it is the person's outline. Its bounding box is the camera. Analysis pixels."""
    h, w = frames.shape[1:3]
    cx0, cy0, cw, ch = content or (0, 0, w, h)
    gx, gy = _persistent_edges(frames)
    e = np.zeros((h, w), dtype=bool)
    e[:, :-1] |= gx > edge_thr
    e[:-1, :] |= gy > edge_thr
    gh, gw = h // cell, w // cell
    dens = e[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell).mean(axis=(1, 3))
    # camera cells: persistent edges, or a flat still backdrop (green screen, wall)
    sd = gray(frames).std(axis=0)[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell).mean(axis=(1, 3))
    on = (dens > density) | (sd < 4.0)
    fx, fy, fw, fh = face
    # face cells always count (a smooth face has few edges)
    on[max(fy // cell, 0):(fy + fh) // cell + 1, max(fx // cell, 0):(fx + fw) // cell + 1] = True
    lim = (max(0, int((fx - reach * fw) // cell)), max(0, int((fy - reach * fh) // cell)),
           min(gw, int((fx + fw + reach * fw) // cell) + 1), min(gh, int((fy + fh + reach * fh) // cell) + 1))
    seed = (min((fy + fh // 2) // cell, gh - 1), min((fx + fw // 2) // cell, gw - 1))
    seen = np.zeros_like(on)
    stack, ys, xs = [seed], [], []
    seen[seed] = True
    while stack:
        yy, xx = stack.pop()
        ys.append(yy); xs.append(xx)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = yy + dy, xx + dx
                if lim[1] <= ny < lim[3] and lim[0] <= nx < lim[2] and on[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
    x0, x1 = min(xs) * cell, (max(xs) + 1) * cell
    y0, y1 = min(ys) * cell, (max(ys) + 1) * cell
    x0, y0 = max(x0, cx0), max(y0, cy0)
    x1, y1 = min(x1, cx0 + cw), min(y1, cy0 + ch)
    return trim_dark(frames, (x0, y0, x1 - x0, y1 - y0))


def detect_layout(src: str, start: float, dur: float, samples: int = 24) -> dict:
    """Find the facecam overlay and the game area. Returns source-pixel boxes:
    {'facecam': (x,y,w,h) or None, 'gameplay': (x,y,w,h), 'face': (x,y,w,h) or None,
     'method': 'opencv'|'skin'}"""
    frames, _, scale = sample_seek(src, start, dur, n=samples, width=960)
    content = trim_dark(frames, content_box(frames), max_frac=0.05)
    face = stable_face(frames)
    res = {"method": "opencv" if cv2_module() else "skin", "face": None, "facecam": None}
    gx, gy, gw, gh = content
    if face is not None:
        rect = facecam_rect(frames, face, content)
        fr = rect[2] * rect[3] / float(gw * gh)
        # a "facecam" covering most of the picture is a just-chatting / IRL layout: no split
        if fr < 0.6:
            res["facecam"] = to_src(rect, scale)
        res["face"] = to_src(face, scale)
    res["gameplay"] = to_src(content, scale)
    return res


def to_src(b: Box, scale: float) -> Box:
    x, y, w, h = b
    return (int(round(x * scale)), int(round(y * scale)), int(round(w * scale)) // 2 * 2,
            int(round(h * scale)) // 2 * 2)


# --------------------------------------------------------------------------- tracking

def fit_window(box: Box, aspect: float, face_w: Optional[float] = None, face_share: float = 0.32,
               max_zoom: float = 1.5, min_w: int = 480) -> Tuple[int, int]:
    """Largest (w, h) of the target aspect inside box; zoomed in (up to max_zoom) when the
    face is small, so it fills about face_share of the width. Never zooms below min_w
    source pixels: upscaling a tiny window only makes the face blurry."""
    bx, by, bw, bh = box
    w = min(bw, bh * aspect)
    if face_w and w > min_w:
        want = face_w / face_share
        w = max(min(w, want), w / max_zoom, min_w)
    h = w / aspect
    return int(w) // 2 * 2, int(h) // 2 * 2


def smooth(values: Sequence[float], k: int = 3, alpha: float = 0.4, deadzone: float = 0.0) -> List[float]:
    """Median filter (drops one-frame detector glitches), exponential smoothing with a
    dead zone (no jitter on tiny moves), then the same smoothing run backwards so the
    result has no lag: we know the whole clip, so the crop can start moving with the
    streamer instead of a second after."""
    v = list(values)
    if not v:
        return v
    half = k // 2
    med = []
    for i in range(len(v)):
        win = sorted(v[max(0, i - half): i + half + 1])
        med.append(win[len(win) // 2])

    def ema(seq, dz):
        out = [seq[0]]
        for x in seq[1:]:
            cur = out[-1]
            out.append(cur if abs(x - cur) <= dz else cur + alpha * (x - cur))
        return out

    fwd = ema(med, deadzone)
    return ema(fwd[::-1], 0.0)[::-1]


def sample_region(src: str, start: float, dur: float, box: Box, fps: float = 2.0, max_w: int = 480):
    """Frames of just one region at (close to) source resolution: cheap and sharp."""
    x, y, w, h = box
    ow = min(w, max_w) // 2 * 2
    oh = int(round(h * ow / w / 2)) * 2
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", src, "-vf",
           f"fps={fps},crop={w}:{h}:{x}:{y},scale={ow}:{oh}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (ow * oh * 3)
    frames = np.frombuffer(raw[: n * ow * oh * 3], dtype=np.uint8).reshape(n, oh, ow, 3)
    return frames, np.arange(n) / fps, w / ow


def track_face(src: str, start: float, dur: float, cam: Box, aspect: float, fps: float = 3.0,
               max_zoom: float = 1.5) -> dict:
    """Follow the face inside the facecam box. Returns {'w','h','keys': [(t, x, y)]} in
    source pixels, with t relative to start. Falls back to a centred static window."""
    cx, cy, cw, ch = cam
    frames, times, scale = sample_region(src, start, dur, cam, fps=fps)
    centres, sizes = [], []
    for f in frames:
        faces = detect_faces(f) if cv2_module() else skin_faces(f, min_px=60, max_w_frac=0.8)
        if faces:
            big = max(b[2] * b[3] for b in faces)
            if cv2_module():
                x, y, w, h = max(faces, key=lambda b: b[2] * b[3])
            else:  # skin blobs: hands and necks are skin too; in a webcam the face is the top one
                x, y, w, h = min((b for b in faces if b[2] * b[3] >= 0.3 * big), key=lambda b: b[1])
            centres.append((cx + (x + w / 2) * scale, cy + (y + h / 2) * scale))
            sizes.append(w * scale)
        else:
            centres.append(None)
    found = [c for c in centres if c]
    face_w = float(np.median(sizes)) if sizes else None
    ww, wh = fit_window(cam, aspect, face_w, max_zoom=max_zoom)
    base = {"w": ww, "h": wh, "found": len(found), "samples": len(centres), "face_w": face_w}
    if not found:
        return dict(base, keys=[(0.0, cx + (cw - ww) // 2, cy + (ch - wh) // 2)])
    last = found[0]
    filled = []
    for c in centres:  # hold the last known position through misses
        last = c or last
        filled.append(last)
    # face slightly above centre (headroom looks natural)
    xs = smooth([c[0] - ww / 2 for c in filled], deadzone=0.05 * ww)
    ys = smooth([c[1] - wh * 0.45 for c in filled], deadzone=0.05 * wh)
    keys = []
    for t, x, y in zip(times, xs, ys):
        keys.append((float(t), int(min(max(x, cx), cx + cw - ww)), int(min(max(y, cy), cy + ch - wh))))
    return dict(base, keys=simplify(keys))


def simplify(keys: Sequence[tuple], tol: float = 6.0) -> List[tuple]:
    """Drop keyframes that lie on the line between neighbours (keeps the ffmpeg expression short)."""
    keys = list(keys)
    if len(keys) <= 2:
        return keys
    out = [keys[0]]
    for i in range(1, len(keys) - 1):
        a, b, c = out[-1], keys[i], keys[i + 1]
        f = (b[0] - a[0]) / max(c[0] - a[0], 1e-6)
        px, py = a[1] + f * (c[1] - a[1]), a[2] + f * (c[2] - a[2])
        if abs(px - b[1]) > tol or abs(py - b[2]) > tol:
            out.append(b)
    out.append(keys[-1])
    return out


def lerp_expr(keys: Sequence[tuple], idx: int) -> str:
    """ffmpeg expression in t: piecewise-linear through keyframes (flat sum, no deep nesting)."""
    if len(keys) == 1:
        return str(int(keys[0][idx]))
    parts = [f"lt(t,{keys[0][0]:.2f})*{int(keys[0][idx])}"]
    for (t0, *a), (t1, *b) in zip(keys, keys[1:]):
        v0, v1 = a[idx - 1], b[idx - 1]
        if t1 - t0 <= 1e-6:
            continue
        slope = (v1 - v0) / (t1 - t0)
        parts.append(f"gte(t,{t0:.2f})*lt(t,{t1:.2f})*({v0}+{slope:.3f}*(t-{t0:.2f}))")
    parts.append(f"gte(t,{keys[-1][0]:.2f})*{int(keys[-1][idx])}")
    return "+".join(parts)


def tracked_crop(track: dict) -> str:
    """crop filter that follows the face (commas escaped for a filtergraph)."""
    k = track["keys"]
    x = lerp_expr(k, 1).replace(",", "\\,")
    y = lerp_expr(k, 2).replace(",", "\\,")
    return f"crop={track['w']}:{track['h']}:{x}:{y}"


# --------------------------------------------------------------------------- thumbnail

def _sharpness(g2d):
    lap = (-4 * g2d[1:-1, 1:-1] + g2d[:-2, 1:-1] + g2d[2:, 1:-1] + g2d[1:-1, :-2] + g2d[1:-1, 2:])
    return float(lap.var())


def pick_thumbnail(src: str, start: float, end: float, out_jpg: str, fps: float = 2.0,
                   region: Optional[Box] = None) -> dict:
    """Pick the most expressive face frame: a sharp frame whose face looks most unlike
    the clip's average face (open mouth, wide eyes, big reaction). Falls back to the
    sharpest frame. region (source px) limits where to look for the face."""
    frames, times, scale = sample_frames(src, start, end - start, fps=fps)
    g = gray(frames)
    patches, scores = [], []
    rx, ry, rw, rh = (int(v / scale) for v in region) if region else (0, 0, g.shape[2], g.shape[1])
    for i, f in enumerate(frames):
        sub = f[ry:ry + rh, rx:rx + rw]
        faces = detect_faces(sub) if sub.size else []
        if faces:
            x, y, w, h = max(faces, key=lambda b: b[2] * b[3])
            p = g[i, ry + y: ry + y + h, rx + x: rx + x + w]
            patches.append((i, _resize(p, 32)))
        scores.append(_sharpness(g[i]))
    sharp = np.array(scores)
    sharp = sharp / max(sharp.max(), 1e-6)
    best_i, how = int(np.argmax(sharp)), "sharpest frame"
    if len(patches) >= 3:
        mean = np.mean([p for _, p in patches], axis=0)
        exp = np.array([np.abs(p - mean).mean() for _, p in patches])
        exp = exp / max(exp.max(), 1e-6)
        total = [(0.65 * exp[j] + 0.35 * sharp[i], i) for j, (i, _) in enumerate(patches)]
        best_i, how = max(total)[1], "most expressive face"
    t = start + float(times[best_i])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-q:v", "2",
                    out_jpg], check=True)
    return {"time": round(t, 2), "how": how, "path": out_jpg}


def _resize(p, n: int):
    h, w = p.shape
    ys = (np.arange(n) * h / n).astype(int)
    xs = (np.arange(n) * w / n).astype(int)
    return p[ys][:, xs]
