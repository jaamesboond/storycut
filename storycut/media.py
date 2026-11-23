"""ffmpeg / ffprobe / yt-dlp helpers. Requires ffmpeg on PATH (yt-dlp only for URLs)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np


def _run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s))


def probe_duration(path: str) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]).stdout
    return float(json.loads(out)["format"]["duration"])


def has_video(path: str) -> bool:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", path]).stdout
    return "video" in out


def download(url: str, out_dir: str, with_chat: bool = True, fmt: Optional[str] = None) -> dict:
    """Download a VOD with yt-dlp (video+audio in one file) and, if available, its chat replay."""
    if not have("yt-dlp"):
        raise RuntimeError("yt-dlp not found on PATH; install it or pass a local file")
    os.makedirs(out_dir, exist_ok=True)
    tmpl = os.path.join(out_dir, "%(id)s.%(ext)s")
    cmd = ["yt-dlp", "-o", tmpl, "-f", fmt or "bv*[height<=1080]+ba/b", "--merge-output-format", "mp4",
           "--no-playlist", "--print", "after_move:filepath"]
    if with_chat:
        cmd += ["--write-subs", "--sub-langs", "live_chat,rechat"]
    cmd.append(url)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {res.stderr.strip()[-500:]}")
    media = res.stdout.strip().splitlines()[-1]
    chat = None
    stem = Path(media).with_suffix("")
    for ext in (".live_chat.json", ".rechat.json"):
        p = str(stem) + ext
        if os.path.exists(p):
            chat = p
    return {"media": media, "chat": chat}


def extract_audio(path: str, out_wav: str, start: float = 0.0, duration: Optional[float] = None,
                  sr: int = 16000) -> str:
    """Decode to 16 kHz mono PCM WAV (accurate seek: -ss after -i)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", path]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", out_wav]
    _run(cmd)
    return out_wav


def read_wav(path: str) -> tuple:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def scene_cuts(path: str, start: float = 0.0, duration: Optional[float] = None, threshold: float = 12.0,
               fps: float = 2.0) -> List[float]:
    """Scene-change times (seconds, relative to start) using ffmpeg scdet on a tiny low-fps proxy."""
    cmd = ["ffmpeg", "-v", "info", "-hide_banner", "-i", path]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-an", "-vf", f"fps={fps},scale=160:-2,scdet=threshold={threshold}", "-f", "null", "-"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    times = []
    for m in re.finditer(r"lavfi\.scd\.time:\s*([0-9.]+)", res.stderr):
        times.append(float(m.group(1)))
    return times


def cut_clip(src: str, out: str, start: float, end: float, reencode: bool = True) -> str:
    """Accurate cut (decode then seek). Re-encodes to H.264/AAC so cuts are frame-exact."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
    if reencode:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    else:
        cmd += ["-c", "copy"]
    cmd.append(out)
    _run(cmd)
    return out


def concat_pieces(src: str, out: str, pieces: List[List[float]]) -> str:
    """Cut several [start, end] pieces from src and join them (dead-air trimmed clip)."""
    v_parts, a_parts, chains = [], [], []
    for i, (a, b) in enumerate(pieces):
        chains.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}]")
        chains.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        v_parts.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + ";" + "".join(v_parts) + f"concat=n={len(pieces)}:v=1:a=1[v][a]"
    _run(["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out])
    return out
