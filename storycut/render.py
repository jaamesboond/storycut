"""Optional render helper: vertical split-screen Short with transparent word-timed
captions and beeped profanity.

Layout (1440x2560):
    +-----------------------+
    |   facecam  1440x1040  |   <- crop of the streamer's camera
    +-----------------------+   <- captions float on this seam (no box)
    |                       |
    |  gameplay  1440x1520  |   <- crop of the game area (no letterbox bars)
    |                       |
    +-----------------------+

Without --facecam the gameplay is scaled to the full width and centred on a
dark background. Output: H.264 High, yuv420p, AAC, +faststart.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import List, Optional, Sequence

from .models import Word
from .signals import is_profane

W, H = 1440, 2560
TOP_H = 1040
BOT_H = H - TOP_H


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


CAPTION_PRESETS = ("clean", "box", "karaoke", "pop", "minimal")
CAPTION_POSITIONS = ("seam", "top", "middle", "lower-third")
ACCENT_BGR = "52DE4A"  # parrot green #4ade52 in ASS byte order (BBGGRR)


def caption_y(pos: str, split: bool = True) -> int:
    """Vertical centre of the caption line for a named position (1440x2560 canvas)."""
    if pos == "seam":
        return TOP_H if split else 1850
    return {"top": 420, "middle": H // 2, "lower-third": 1850}.get(pos, TOP_H if split else 1850)


def _style(name, font, size, bold, primary, outline_col, back, border, outline, shadow):
    return (f"Style: {name},{font},{size},&H00{primary},&H00{primary},&H00{outline_col},&H{back},{bold},0,0,0,"
            f"100,100,0,0,{border},{outline},{shadow},8,60,60,0")


def build_ass(words: Sequence[Word], hook: Optional[str] = None, hook_secs: float = 3.0,
              caption_y: Optional[int] = None, font: str = "Poppins ExtraBold", size: int = 86,
              end_text: Optional[str] = None, clip_len: Optional[float] = None, beep_label: str = "[beep]",
              preset: str = "clean", position: str = "seam", split: bool = True) -> str:
    """ASS subtitles for the Short.

    Presets (all word-timed from the transcript, swears shown as [beep]):
      clean   - white bold, black outline, no background (the ClipHunt look)
      box     - black text on a white box
      karaoke - a short line where the word being spoken lights up in parrot green
      pop     - one or two big words at a time that pop in
      minimal - small lowercase text with a soft outline
    """
    if preset not in CAPTION_PRESETS:
        raise ValueError(f"unknown caption preset {preset!r}; use one of {', '.join(CAPTION_PRESETS)}")
    y = caption_y if caption_y is not None else globals()["caption_y"](position, split)
    W_, B_ = "FFFFFF", "000000"
    styles = {
        "clean": _style("Cap", font, size, -1, W_, B_, "00000000", 1, 7, 0),
        "box": _style("Cap", font, int(size * 0.85), -1, B_, W_, "00FFFFFF", 3, 16, 0),
        "karaoke": _style("Cap", font, size, -1, W_, B_, "00000000", 1, 7, 0),
        "pop": _style("Cap", font, int(size * 1.45), -1, W_, B_, "00000000", 1, 9, 0),
        "minimal": _style("Cap", font, int(size * 0.66), 0, W_, B_, "80000000", 1, 3, 2),
    }
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles[preset]}
{_style("Hook", font, int(size * 1.15), -1, W_, B_, "00000000", 1, 8, 0)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    at = f"{{\\an5\\pos({W // 2},{y})}}"
    ev: List[str] = []
    start_caps = 0.0
    if hook:
        lines = _ass_escape(hook).replace("|", "\\N")
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(hook_secs)},Hook,,0,0,0,,{at}{lines}")
        start_caps = hook_secs
    shown = [Word(w.start, w.end, beep_label if is_profane(w.text) else w.text, w.prob)
             for w in words if w.end > start_caps]

    def case(t: str) -> str:
        return t.lower() if preset == "minimal" else t.upper()

    def line(a, b, text, layer=0, min_len=0.3):
        a = max(a, start_caps)
        if b <= a:
            return
        ev.append(f"Dialogue: {layer},{_ass_time(a)},{_ass_time(max(b, a + min_len))},Cap,,0,0,0,,{at}{text}")

    if preset == "karaoke":
        groups = _groups(shown, 4)
        for g in groups:
            for i, w in enumerate(g):
                nxt = g[i + 1].start if i + 1 < len(g) else w.end
                parts = []
                for j, x in enumerate(g):
                    t = _ass_escape(case(x.text))
                    parts.append(f"{{\\c&H{ACCENT_BGR}&\\fscx108\\fscy108}}{t}{{\\r}}" if j == i else t)
                last = i + 1 == len(g)  # inside a line, each word lasts until the next one starts
                line(w.start if i else g[0].start, w.end if last else nxt, " ".join(parts),
                     min_len=0.3 if last else 0.0)
    elif preset == "pop":
        for g in _groups(shown, 2, max_chars=14):
            t = _ass_escape(case(" ".join(x.text for x in g)))
            line(g[0].start, g[-1].end, "{\\fscx55\\fscy55\\t(0,90,\\fscx108\\fscy108)\\t(90,150,\\fscx100\\fscy100)}" + t)
    else:
        for g in _groups(shown, 3, max_chars=26 if preset != "minimal" else 34):
            line(g[0].start, g[-1].end, _ass_escape(case(" ".join(x.text for x in g))))
    if end_text and clip_len:
        ev.append(f"Dialogue: 2,{_ass_time(max(0, clip_len - 2))},{_ass_time(clip_len)},Hook,,0,0,0,,{at}"
                  f"{_ass_escape(end_text).replace('|', chr(92) + 'N')}")
    return head + "\n".join(ev) + "\n"


def _groups(words: Sequence[Word], n: int, max_gap: float = 0.6, max_chars: int = 20) -> List[List[Word]]:
    """Consecutive words in groups of up to n that fit on one line (max_chars)."""
    out: List[List[Word]] = []
    for w in words:
        if (out and len(out[-1]) < n and w.start - out[-1][-1].end <= max_gap
                and len(" ".join(x.text for x in out[-1] + [w])) <= max_chars):
            out[-1].append(w)
        else:
            out.append([w])
    return out


def beep_ranges(words: Sequence[Word], pad: float = 0.05) -> List[tuple]:
    return [(max(0.0, w.start - pad), w.end + pad) for w in words if is_profane(w.text)]


LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"  # Shorts/TikTok loudness target


def audio_filter(ranges: Sequence[tuple], beep_db: float = -8.0, loudnorm: bool = False) -> str:
    """Mute profane words and lay a 1 kHz tone over them; optionally normalise loudness to
    -14 LUFS. Returns a filter_complex fragment that reads [0:a] and writes [aout]."""
    tail = f",{LOUDNORM},aresample=48000" if loudnorm else ""
    if not ranges:
        return f"[0:a]{LOUDNORM},aresample=48000[aout]" if loudnorm else "[0:a]anull[aout]"
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)
    gain = 10 ** (beep_db / 20)
    return (f"[0:a]volume='if({cond},0,1)':eval=frame[am];"
            f"sine=f=1000:sample_rate=48000,volume='if({cond},{gain:.3f},0)':eval=frame[bp];"
            f"[am][bp]amix=inputs=2:duration=first:normalize=0{tail}[aout]")


DEFAULT_PRESET = "superfast"


def encode_args(preset: str = DEFAULT_PRESET, crf: int = 23, maxrate: Optional[str] = "7M", threads: int = 0) -> list:
    """One-pass encode straight to an upload-ready file (no ultrafast master + second re-encode).
    CRF keeps quality constant; maxrate/bufsize cap busy scenes so a 60 s Short stays near 50 MB."""
    a = ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-preset", preset, "-crf", str(crf)]
    if maxrate:
        rate = float(maxrate.rstrip("Mm"))
        a += ["-maxrate", maxrate, "-bufsize", f"{rate * 2:g}M"]
    if threads:
        a += ["-threads", str(threads)]
    return a + ["-g", "60", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart"]


def parse_box(spec: Optional[str]):
    """'W:H:X:Y' (ffmpeg crop order) or 'x,y,w,h' -> (x, y, w, h). 'auto'/'none'/'' pass through."""
    if spec is None:
        return None
    spec = str(spec).strip().lower()
    if spec in ("", "none", "off", "no"):
        return None
    if spec == "auto":
        return "auto"
    if "," in spec:
        x, y, w, h = (int(float(v)) for v in spec.split(","))
    else:
        w, h, x, y = (int(float(v)) for v in spec.split(":"))
    return x, y, w, h


def _crop(spec) -> str:
    x, y, w, h = parse_box(spec) if isinstance(spec, str) else spec
    return f"crop={w}:{h}:{x}:{y}"


def resolve_layout(src: str, start: float, end: float, facecam=None, gameplay=None, track: bool = True,
                   max_zoom: float = 1.5) -> dict:
    """Turn facecam/gameplay options into concrete crops. 'auto' runs the frame analysis
    (storycut.frame: needs numpy; OpenCV optional). Returns
    {'facecam': box|None, 'gameplay': box|None, 'track': dict|None, 'notes': [...]}"""
    cam, game = parse_box(facecam), parse_box(gameplay)
    info = {"facecam": None, "gameplay": None, "track": None, "notes": []}
    need = cam == "auto" or game == "auto" or (track and cam not in (None,))
    fr = None
    if need:
        try:
            from . import frame as fr  # numpy needed
            if fr.np is None:
                raise ImportError("numpy")
        except ImportError:
            fr = None
            info["notes"].append("numpy missing: auto layout and face tracking are off")
    if cam == "auto" or game == "auto":
        if fr is None:
            cam = None if cam == "auto" else cam
            game = None if game == "auto" else game
        else:
            # the layout does not change within a stream, so look at a wider window: a long
            # cutscene inside a short clip would otherwise hide the camera's movement
            a0 = max(0.0, start - 20.0)
            lay = fr.detect_layout(src, a0, max(end - a0 + 20.0, 60.0), samples=24)
            info["notes"].append(f"auto layout via {lay['method']}")
            if cam == "auto":
                cam = lay["facecam"]
                if cam is None:
                    info["notes"].append("no streamer camera found: full-width gameplay layout")
            if game == "auto" or (game is None and parse_box(facecam) == "auto"):
                game = lay["gameplay"]
    info["facecam"], info["gameplay"] = cam, game
    if cam and track and fr is not None:
        tr = fr.track_face(src, start, end - start, cam, W / TOP_H, max_zoom=max_zoom)
        if tr["found"]:
            info["track"] = tr
            info["notes"].append(f"face tracked in {tr['found']}/{tr['samples']} samples, "
                                 f"{len(tr['keys'])} keyframes")
        else:
            info["notes"].append("face not found inside the camera box: static crop")
    return info


def render(src: str, start: float, end: float, words_abs: Sequence[Word], out: str, *,
           facecam: Optional[str] = None, gameplay: Optional[str] = None, hook: Optional[str] = None,
           end_text: Optional[str] = None, watermark: Optional[str] = None, crf: int = 23,
           preset: str = DEFAULT_PRESET, font: str = "Poppins ExtraBold", maxrate: Optional[str] = "7M",
           threads: int = 0, scale_flags: Optional[str] = None, captions: str = "clean",
           caption_pos: str = "seam", track: bool = True, loudnorm: bool = False,
           thumbnail: bool = False, max_zoom: float = 1.5) -> str:
    """Render [start, end] of src into a vertical Short. words_abs use the source timeline.

    facecam / gameplay: 'W:H:X:Y', 'x,y,w,h', 'auto' or None. With a facecam the face is
    tracked (smoothly) inside it unless track=False. Writes <out>.layout.json with what was
    detected, and <out>.jpg (most expressive face frame) when thumbnail=True."""
    clip_len = end - start
    rel = sorted((Word(w.start - start, w.end - start, w.text, w.prob)
                  for w in words_abs if w.start >= start and w.end <= end), key=lambda w: w.start)
    out = os.path.abspath(out)
    work = os.path.dirname(out) or "."
    lay = resolve_layout(src, start, end, facecam, gameplay, track=track, max_zoom=max_zoom)
    cam, game, tr = lay["facecam"], lay["gameplay"], lay["track"]
    ass_name = os.path.basename(out) + ".ass"
    with open(os.path.join(work, ass_name), "w", encoding="utf-8") as f:
        f.write(build_ass(rel, hook=hook, end_text=end_text, clip_len=clip_len, font=font, preset=captions,
                          position=caption_pos, split=bool(cam)))

    gp = _crop(game) + "," if game else ""
    sf = f":flags={scale_flags}" if scale_flags else ""
    # Speed: the input is seeked (-ss before -i) so only the needed seconds are decoded, and the frame
    # rate is dropped to 30 BEFORE cropping/scaling so 60 fps sources are not scaled twice as often.
    if cam:
        if tr:
            from .frame import tracked_crop
            top = f"[fa]{tracked_crop(tr)},scale={W}:{TOP_H}{sf}[top];"
        else:
            top = f"[fa]{_crop(cam)},scale={W}:{TOP_H}:force_original_aspect_ratio=increase{sf},crop={W}:{TOP_H}[top];"
        v = (f"[0:v]fps=30,split=2[fa][fb];" + top +
             f"[fb]{gp}scale={W}:{BOT_H}:force_original_aspect_ratio=increase{sf},crop={W}:{BOT_H}[bot];"
             f"[top][bot]vstack=inputs=2,setsar=1,ass={ass_name}[v1]")
    else:
        v = (f"[0:v]fps=30,{gp}scale={W}:-2{sf},setsar=1[g];color=c=0x101418:s={W}x{H}:r=30[bg];"
             f"[bg][g]overlay=(W-w)/2:(H-h)/2:shortest=1,ass={ass_name}[v1]")
    inputs = ["-ss", f"{max(0.0, start):.3f}", "-t", f"{clip_len:.3f}", "-i", os.path.abspath(src)]
    if watermark:
        inputs += ["-i", os.path.abspath(watermark)]
        v += (f";[1:v]scale=200:-1,format=rgba,colorchannelmixer=aa=0.85[wm];"
              f"[v1][wm]overlay=W-w-40:H-h-60[vout]")
    else:
        v += ";[v1]null[vout]"
    graph = v + ";" + audio_filter(beep_ranges(rel), loudnorm=loudnorm)
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           "-t", f"{clip_len:.3f}", *encode_args(preset=preset, crf=crf, maxrate=maxrate, threads=threads),
           out]
    subprocess.run(cmd, check=True, cwd=work)
    meta = {"facecam": cam, "gameplay": game, "captions": captions, "caption_pos": caption_pos,
            "loudnorm": loudnorm, "notes": lay["notes"],
            "track": {k: tr[k] for k in ("w", "h", "keys", "found", "samples")} if tr else None}
    if thumbnail:
        try:
            from .frame import pick_thumbnail
            meta["thumbnail"] = pick_thumbnail(out, 0.0, clip_len, out[:-4] + ".jpg",
                                               region=(0, 0, W, TOP_H) if cam else None)
        except Exception as e:  # numpy missing or decode error: the Short itself is fine
            meta["notes"].append(f"thumbnail skipped: {e}")
    with open(out + ".layout.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return out


def load_words(path: str) -> tuple:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    off = float(d.get("offset", 0.0))
    words = [Word(w["start"] + off, w["end"] + off, w["text"], w.get("prob", 1.0)) for w in d["words"]]
    return words, off
