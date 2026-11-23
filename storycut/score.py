"""Explainable 0-10 score for a story window.

  peak strength     0-4   how far the excitement peak stands above the stream's normal
  crowd / chat      0-2   chat spike at the peak (falls back to cue density when no chat)
  payoff clarity    0-3   is there a reaction AFTER the peak (cues, laughter, talk)?
  self-explanatory  0-1   is there spoken setup so a cold viewer understands?

The weights are deliberately simple so a human can read the reasons and
disagree. An optional LLM re-rank (see rerank.py) can adjust the final order.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from .models import Window, Word


def _seg(curve: Optional[np.ndarray], a: float, b: float) -> np.ndarray:
    if curve is None or len(curve) == 0:
        return np.zeros(0)
    ia, ib = max(0, int(a)), min(len(curve), int(np.ceil(b)))
    return curve[ia:ib] if ib > ia else np.zeros(0)


def score_window(w: Window, combined: np.ndarray, parts: dict, words: Sequence[Word],
                 cue_events: Sequence[tuple]) -> Window:
    reasons = []
    comp = {}
    t = w.peak.t

    # 1) peak strength (combined curve is ~z units)
    pv = float(w.peak.value)
    peak_pts = float(np.clip(pv / 2.5 * 4.0, 0.0, 4.0))
    comp["peak"] = round(peak_pts, 2)
    reasons.append(f"peak {pv:.2f} above normal -> {peak_pts:.1f}/4")

    # 2) chat spike or cue density
    chat = parts.get("chat")
    if chat is not None and len(chat):
        cz = float(np.max(_seg(chat, t - 10, t + 15)) if len(_seg(chat, t - 10, t + 15)) else 0.0)
        chat_pts = float(np.clip(cz / 3.0 * 2.0, 0.0, 2.0))
        reasons.append(f"chat spike z={cz:.1f} -> {chat_pts:.1f}/2")
    else:
        n_cues = sum(1 for e in cue_events if t - 10 <= e[0] <= t + 15)
        chat_pts = float(np.clip(n_cues / 4.0 * 2.0, 0.0, 2.0))
        reasons.append(f"no chat data; {n_cues} reaction cues near peak -> {chat_pts:.1f}/2")
    comp["crowd"] = round(chat_pts, 2)

    # 3) payoff: reaction after the peak
    after = [e for e in cue_events if t < e[0] <= w.end]
    before = [e for e in cue_events if w.start <= e[0] <= t]
    after_w = sum(e[1] for e in after)
    talk_after = sum(1 for x in words if t < x.start <= w.end)
    tail = max(1.0, w.end - t)
    payoff_pts = 0.0
    payoff_pts += min(1.8, after_w / 4.0)
    payoff_pts += min(0.8, talk_after / tail / 2.5 * 0.8)
    if any(e[2] in ("laugh",) for e in after):
        payoff_pts += 0.4
    payoff_pts = float(np.clip(payoff_pts, 0.0, 3.0))
    comp["payoff"] = round(payoff_pts, 2)
    labels = sorted({e[2] for e in after})[:5]
    reasons.append(f"reaction after peak: {len(after)} cues {labels}, {talk_after} words -> {payoff_pts:.1f}/3")

    # 4) self-explanatory: spoken setup before the peak
    setup_len = max(1.0, t - w.start)
    talk_before = sum(1 for x in words if w.start <= x.start <= t)
    density = talk_before / setup_len
    selfx = 0.0
    if setup_len >= 8:
        selfx += 0.5
    if density >= 1.0:
        selfx += 0.5
    comp["self_explanatory"] = round(selfx, 2)
    reasons.append(f"setup {setup_len:.0f}s with {talk_before} words ({density:.1f}/s) -> {selfx:.1f}/1")

    total = peak_pts + chat_pts + payoff_pts + selfx
    # light penalty for windows that are far too long for a Short
    if w.duration > 120:
        total -= 0.5
        reasons.append("long window (>120s): -0.5")
    w.score = round(float(np.clip(total, 0.0, 10.0)), 1)
    w.components = comp
    w.reasons = reasons
    return w
