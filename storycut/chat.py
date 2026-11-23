"""Chat replay parsing -> messages per second.

Supported inputs (auto-detected):
  * yt-dlp YouTube live chat replay  (<id>.live_chat.json, one JSON object per line)
  * Twitch chat JSON exported by TwitchDownloader (`comments[].content_offset_seconds`)
  * yt-dlp Twitch rechat JSON (list of comments with `content_offset_seconds`)
  * plain text / CSV with one timestamp (seconds or HH:MM:SS) per line, first column

If no chat is available the tool simply runs without this signal.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, List, Optional

import numpy as np

_HMS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d+))?$")


def parse_ts(s: str) -> Optional[float]:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _HMS.match(s)
    if not m:
        return None
    h, mi, se, frac = m.groups()
    t = int(h or 0) * 3600 + int(mi) * 60 + int(se)
    if frac:
        t += float("0." + frac)
    return float(t)


def _yt_offsets(lines: Iterable[str]) -> List[float]:
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        r = obj.get("replayChatItemAction") or {}
        off = r.get("videoOffsetTimeMsec")
        if off is None:
            continue
        out.append(int(off) / 1000.0)
    return out


def load_chat_offsets(path: str) -> List[float]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    head = text.lstrip()[:1]
    if head == "{" and "replayChatItemAction" in text[:5000]:
        return _yt_offsets(text.splitlines())
    if head in ("{", "["):
        try:
            data = json.loads(text)
            comments = data.get("comments", []) if isinstance(data, dict) else data
            return [float(c["content_offset_seconds"]) for c in comments if "content_offset_seconds" in c]
        except (json.JSONDecodeError, TypeError, KeyError):
            return _yt_offsets(text.splitlines())
    out = []
    for line in text.splitlines():
        first = re.split(r"[,\t;]", line, maxsplit=1)[0]
        t = parse_ts(first)
        if t is not None:
            out.append(t)
    return out


def chat_curve(offsets: Iterable[float], duration_s: int, start: float = 0.0) -> np.ndarray:
    """Messages per second on the chunk timeline (offsets are absolute; start shifts them)."""
    c = np.zeros(max(0, int(duration_s)))
    for t in offsets:
        j = int(t - start)
        if 0 <= j < len(c):
            c[j] += 1
    return c
