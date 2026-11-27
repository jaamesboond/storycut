"""Outputs: ranked JSON, a readable Markdown report, and per-window SRT captions."""

from __future__ import annotations

import json
from typing import List, Sequence

from .models import Window, Word


def fmt_hms(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def srt_time(t: float) -> str:
    t = max(0.0, float(t))
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_chunks(words: Sequence[Word], max_words: int = 4, max_gap: float = 0.6) -> List[tuple]:
    """Group words into short caption lines: (start, end, text)."""
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > max_gap):
            out.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        out.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
    return out


def window_srt(window: Window, words: Sequence[Word], max_words: int = 4, mask: bool = True) -> str:
    """SRT with times relative to the window start (ready to burn into the cut clip).
    mask=True writes [beep] in place of swear words (same list the render beeps)."""
    from .signals import is_profane
    inside = [Word(w.start - window.start, w.end - window.start,
                   "[beep]" if mask and is_profane(w.text) else w.text, w.prob)
              for w in words if w.start >= window.start and w.end <= window.end]
    lines = []
    for i, (a, b, text) in enumerate(caption_chunks(inside, max_words=max_words), 1):
        lines.append(f"{i}\n{srt_time(a)} --> {srt_time(max(b, a + 0.3))}\n{text}\n")
    return "\n".join(lines)


def to_json(windows: Sequence[Window], meta: dict, offset: float = 0.0) -> dict:
    items = []
    for rank, w in enumerate(windows, 1):
        items.append({
            "rank": rank,
            "score": w.score,
            "start": round(w.start + offset, 2),
            "end": round(w.end + offset, 2),
            "start_hms": fmt_hms(w.start + offset),
            "end_hms": fmt_hms(w.end + offset),
            "duration": round(w.duration, 1),
            "trimmed_duration": round(w.trimmed_duration, 1),
            "peak": round(w.peak.t + offset, 2),
            "peak_hms": fmt_hms(w.peak.t + offset),
            "peak_signals": w.peak.sources,
            "components": w.components,
            "reasons": w.reasons,
            "keep_pieces": [[round(a + offset, 2), round(b + offset, 2)] for a, b in w.keep],
            "llm": w.llm,
            "transcript": w.text,
        })
    return {"meta": meta, "candidates": items}


def to_markdown(data: dict) -> str:
    m = data["meta"]
    out = [f"# storycut report: {m.get('input_name', '')}", "",
           f"Analysed {fmt_hms(m.get('offset', 0))} to {fmt_hms(m.get('offset', 0) + m.get('analysed_seconds', 0))} "
           f"({m.get('analysed_seconds', 0) / 60:.1f} min). Signals: {', '.join(m.get('signals', []))}.", ""]
    if not data["candidates"]:
        out.append("No candidate moments passed the threshold.")
    for c in data["candidates"]:
        out += [f"## #{c['rank']}  score {c['score']}/10  ({c['start_hms']} to {c['end_hms']}, {c['duration']:.0f}s)",
                f"Peak at {c['peak_hms']}. Trimmed length {c['trimmed_duration']:.0f}s.", ""]
        out += [f"- {r}" for r in c["reasons"]]
        if c.get("llm"):
            out.append(f"- LLM: {c['llm']}")
        txt = c["transcript"]
        if len(txt) > 700:
            txt = txt[:700] + " ..."
        out += ["", f"> {txt}", ""]
    return "\n".join(out) + "\n"


def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
