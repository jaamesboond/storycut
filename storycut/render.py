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
from .report import caption_chunks
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


def build_ass(words: Sequence[Word], hook: Optional[str] = None, hook_secs: float = 3.0,
              caption_y: int = TOP_H, font: str = "Poppins ExtraBold", size: int = 86,
              end_text: Optional[str] = None, clip_len: Optional[float] = None, beep_label: str = "[beep]") -> str:
    """ASS subtitle file: white bold text with a black outline, no background box."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,7,0,8,60,60,{caption_y - size // 2}
Style: Hook,{font},{int(size * 1.15)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,0,8,60,60,{caption_y - size}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev: List[str] = []
    start_caps = 0.0
    if hook:
        lines = _ass_escape(hook).replace("|", "\\N")
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(hook_secs)},Hook,,0,0,0,,{lines}")
        start_caps = hook_secs
    shown = [Word(w.start, w.end, beep_label if is_profane(w.text) else w.text, w.prob)
             for w in words if w.end > start_caps]
    for a, b, text in caption_chunks(shown, max_words=3):
        a = max(a, start_caps)
        ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(max(b, a + 0.35))},Cap,,0,0,0,,{_ass_escape(text).upper()}")
    if end_text and clip_len:
        ev.append(f"Dialogue: 2,{_ass_time(max(0, clip_len - 2))},{_ass_time(clip_len)},Hook,,0,0,0,,"
                  f"{_ass_escape(end_text).replace('|', chr(92) + 'N')}")
    return head + "\n".join(ev) + "\n"


def beep_ranges(words: Sequence[Word], pad: float = 0.05) -> List[tuple]:
    return [(max(0.0, w.start - pad), w.end + pad) for w in words if is_profane(w.text)]


def audio_filter(ranges: Sequence[tuple], beep_db: float = -8.0) -> str:
    """Mute profane words and lay a 1 kHz tone over them. Returns a filter_complex fragment
    that reads [0:a] and writes [aout]."""
    if not ranges:
        return "[0:a]anull[aout]"
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)
    gain = 10 ** (beep_db / 20)
    return (f"[0:a]volume='if({cond},0,1)':eval=frame[am];"
            f"sine=f=1000:sample_rate=48000,volume='if({cond},{gain:.3f},0)':eval=frame[bp];"
            f"[am][bp]amix=inputs=2:duration=first:normalize=0[aout]")


def _crop(spec: str) -> str:
    w, h, x, y = (int(float(v)) for v in spec.split(":"))
    return f"crop={w}:{h}:{x}:{y}"


def render(src: str, start: float, end: float, words_abs: Sequence[Word], out: str, *,
           facecam: Optional[str] = None, gameplay: Optional[str] = None, hook: Optional[str] = None,
           end_text: Optional[str] = None, watermark: Optional[str] = None, crf: int = 23,
           preset: str = "veryfast", font: str = "Poppins ExtraBold") -> str:
    """Render [start, end] of src into a vertical Short. words_abs use the source timeline."""
    clip_len = end - start
    rel = [Word(w.start - start, w.end - start, w.text, w.prob)
           for w in words_abs if w.start >= start and w.end <= end]
    out = os.path.abspath(out)
    work = os.path.dirname(out) or "."
    ass_name = os.path.basename(out) + ".ass"
    with open(os.path.join(work, ass_name), "w", encoding="utf-8") as f:
        f.write(build_ass(rel, hook=hook, end_text=end_text, clip_len=clip_len, font=font))

    gp = _crop(gameplay) + "," if gameplay else ""
    if facecam:
        v = (f"[0:v]{_crop(facecam)},scale={W}:{TOP_H}:force_original_aspect_ratio=increase,"
             f"crop={W}:{TOP_H},setsar=1[top];"
             f"[0:v]{gp}scale={W}:{BOT_H}:force_original_aspect_ratio=increase,crop={W}:{BOT_H},setsar=1[bot];"
             f"[top][bot]vstack=inputs=2,fps=30[st];[st]ass={ass_name}[v1]")
    else:
        v = (f"[0:v]{gp}scale={W}:-2,setsar=1[g];color=c=0x101418:s={W}x{H}:r=30[bg];"
             f"[bg][g]overlay=(W-w)/2:(H-h)/2:shortest=1,fps=30[st];[st]ass={ass_name}[v1]")
    inputs = []
    pre = max(0.0, start - 30.0)  # fast seek near, then accurate seek the rest
    inputs += ["-ss", f"{pre:.3f}", "-i", os.path.abspath(src)]
    if watermark:
        inputs += ["-i", os.path.abspath(watermark)]
        v += (f";[1:v]scale=200:-1,format=rgba,colorchannelmixer=aa=0.85[wm];"
              f"[v1][wm]overlay=W-w-40:H-h-60[vout]")
    else:
        v += ";[v1]null[vout]"
    graph = v + ";" + audio_filter(beep_ranges(rel)).replace("[0:a]", f"[0:a]atrim={start - pre:.3f}:{end - pre:.3f},asetpts=PTS-STARTPTS,")
    graph = graph.replace("[0:v]", f"[0:v]trim={start - pre:.3f}:{end - pre:.3f},setpts=PTS-STARTPTS,", 2) \
        if facecam else graph.replace("[0:v]", f"[0:v]trim={start - pre:.3f}:{end - pre:.3f},setpts=PTS-STARTPTS,", 1)
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           "-t", f"{clip_len:.3f}", "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-preset", preset, "-crf", str(crf), "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, cwd=work)
    return out


def load_words(path: str) -> tuple:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    off = float(d.get("offset", 0.0))
    words = [Word(w["start"] + off, w["end"] + off, w["text"], w.get("prob", 1.0)) for w in d["words"]]
    return words, off
