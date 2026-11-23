"""Pure logic behind the dashboard: job ids, option parsing, timelines, paths.

Nothing here touches the network or runs ffmpeg, so it is easy to unit-test.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from ..chat import parse_ts
from ..signals import is_profane

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MODELS = ("tiny", "base", "small", "medium", "large-v3")

DEFAULT_SETTINGS = {
    "model": "small",
    "language": "",
    "top": 5,
    "target": 60,
    "max_len": 180,
    "min_gap": 90,
    "scenes": False,
    "trim": True,
    "facecam": "",
    "gameplay": "",
    "hook": "",
    "end_text": "",
    "watermark": "",
    "font": "Poppins ExtraBold",
    "crf": 23,
    "mask_profanity": True,
}

MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4a": "audio/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".srt": "application/x-subrip", ".json": "application/json",
    ".md": "text/markdown; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
}


def safe_job_id(s: str) -> Optional[str]:
    """Return s if it is a safe job id (folder name), else None."""
    return s if isinstance(s, str) and JOB_ID_RE.match(s) else None


def new_job_id(source: str, now: Optional[float] = None) -> str:
    """Readable, unique-enough id: <slug-of-input>-<yyyymmdd-hhmmss>."""
    base = re.split(r"[\\/?#=&]", source.rstrip("/"))[-1] or "job"
    base = os.path.splitext(base)[0]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower()[:32] or "job"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    return f"{slug}-{stamp}"


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def parse_job_request(body: dict, settings: Optional[dict] = None) -> Tuple[Optional[dict], Optional[str]]:
    """Validate a 'new job' request. Returns (options, error)."""
    s = dict(DEFAULT_SETTINGS, **(settings or {}))
    src = (body or {}).get("input", "")
    src = src.strip() if isinstance(src, str) else ""
    if not src:
        return None, "input is required (a file path or a VOD URL)"
    is_url = bool(re.match(r"^https?://", src))
    if not is_url and not os.path.exists(os.path.expanduser(src)):
        return None, f"file not found: {src}"
    model = body.get("model") or s["model"]
    if model not in MODELS:
        return None, f"unknown model {model!r}; use one of {', '.join(MODELS)}"
    start = parse_ts(str(body.get("start") or "0"))
    if start is None:
        return None, "bad start time (use seconds or HH:MM:SS)"
    duration = None
    if body.get("duration"):
        duration = parse_ts(str(body["duration"]))
        if duration is None or duration <= 0:
            return None, "bad duration (use seconds or HH:MM:SS)"
    target = _num(body.get("target", s["target"]), 15, 180, 60)
    max_len = _num(body.get("max_len", s["max_len"]), target, 600, 180)
    opts = {
        "input": src if is_url else os.path.abspath(os.path.expanduser(src)),
        "is_url": is_url,
        "start": start,
        "duration": duration,
        "model": model,
        "language": (body.get("language") or s["language"] or None),
        "chat": (body.get("chat") or None),
        "top": int(_num(body.get("top", s["top"]), 1, 20, 5)),
        "target": target,
        "max_len": max_len,
        "min_gap": _num(body.get("min_gap", s["min_gap"]), 10, 600, 90),
        "scenes": bool(body.get("scenes", s["scenes"])),
        "trim": bool(body.get("trim", s["trim"])),
        "cut": bool(body.get("cut", False)),
    }
    if opts["chat"] and not os.path.exists(os.path.expanduser(opts["chat"])):
        return None, f"chat file not found: {opts['chat']}"
    return opts, None


def merge_settings(current: dict, patch: dict) -> dict:
    """Apply a settings patch, keeping only known keys with the right types."""
    out = dict(DEFAULT_SETTINGS, **(current or {}))
    for k, v in (patch or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        d = DEFAULT_SETTINGS[k]
        if isinstance(d, bool):
            out[k] = bool(v)
        elif isinstance(d, int):
            try:
                out[k] = int(float(v))
            except (TypeError, ValueError):
                pass
        else:
            out[k] = "" if v is None else str(v)
    if out["model"] not in MODELS:
        out["model"] = DEFAULT_SETTINGS["model"]
    return out


def timeline(c: dict) -> List[dict]:
    """Split a candidate window into setup / build / peak / payoff parts for the timeline bar.

    Fractions are relative to the window length. Dead-air pieces (removed by the
    trim step) are returned as kind='cut'.
    """
    a, b, p = float(c["start"]), float(c["end"]), float(c.get("peak", c["start"]))
    length = max(b - a, 1e-6)
    p = min(max(p, a), b)
    pk0, pk1 = max(a, p - 2.0), min(b, p + 2.0)
    setup_end = a + 0.4 * (pk0 - a)
    raw = [("setup", a, setup_end), ("build", setup_end, pk0), ("peak", pk0, pk1), ("payoff", pk1, b)]
    segs = [{"kind": k, "start": round(x, 2), "end": round(y, 2),
             "from": round((x - a) / length, 4), "to": round((y - a) / length, 4)}
            for k, x, y in raw if y - x > 0.05]
    keep = c.get("keep_pieces") or []
    prev = a
    for x, y in keep:
        if x - prev > 0.3:
            segs.append({"kind": "cut", "start": round(prev, 2), "end": round(x, 2),
                         "from": round((prev - a) / length, 4), "to": round((x - a) / length, 4)})
        prev = y
    if keep and b - prev > 0.3:
        segs.append({"kind": "cut", "start": round(prev, 2), "end": round(b, 2),
                     "from": round((prev - a) / length, 4), "to": 1.0})
    return segs


def heat(score: float) -> str:
    """Badge colour class for a 0-10 score."""
    return "hot" if score >= 8 else "good" if score >= 6.5 else "ok" if score >= 5 else "low"


def mask_text(text: str) -> str:
    """Replace profane words with [beep] (same list the renderer beeps)."""
    out = []
    for tok in (text or "").split():
        core = re.sub(r"[^\w']", "", tok)
        out.append(tok.replace(core, "[beep]") if core and is_profane(core) else tok)
    return " ".join(out)


def safe_file(job_dir: str, name: str) -> Optional[str]:
    """Absolute path of a file inside job_dir, or None if unsafe / missing."""
    if not FILE_RE.match(name or ""):
        return None
    path = os.path.realpath(os.path.join(job_dir, name))
    root = os.path.realpath(job_dir)
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return path


def parse_range(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """Parse a single 'bytes=a-b' Range header. Returns inclusive (start, end) or None."""
    if not header or size <= 0:
        return None
    m = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    if m.group(1) == "":
        n = int(m.group(2))
        if n == 0:
            return None
        return max(0, size - n), size - 1
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else size - 1
    if a >= size or b < a:
        return None
    return a, min(b, size - 1)


def clip_files(job_dir: str, rank: int) -> Dict[str, str]:
    """Names of the outputs that exist for a rank (cut clip, srt, render)."""
    found = {}
    for kind, names in (("clip", (f"clip{rank:02d}.mp4", f"clip{rank:02d}.m4a")),
                        ("srt", (f"clip{rank:02d}.srt",)),
                        ("short", (f"short_rank{rank}.mp4",))):
        for n in names:
            if os.path.isfile(os.path.join(job_dir, n)):
                found[kind] = n
                break
    return found


def load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
