"""STORY-CUT logic: grow each excitement peak into a full story window.

A good clip is not the loudest second, it is setup -> build -> payoff:
  * walk BACK from the peak, sentence by sentence, to where the situation
    starts (stop at a long silence / scene break, or when the setup budget
    is used up);
  * walk FORWARD to include the reaction and the payoff (stop at a long
    silence after the reaction, or when the window reaches its target);
  * snap both ends to sentence boundaries so nobody is cut mid-word;
  * optionally squeeze dead air inside the window.

Pure Python, unit-tested.
"""

from __future__ import annotations

import bisect
import re
from typing import List, Optional, Sequence

from .models import Peak, Sentence, Window, Word

_END_RE = re.compile(r"[.!?…]+[\"')\]]*$")


def sentences_from_words(words: Sequence[Word], gap: float = 0.8, max_len: float = 15.0) -> List[Sentence]:
    """Group words into sentences on end punctuation, pauses >= gap, or length > max_len."""
    out: List[Sentence] = []
    cur: List[Word] = []
    for i, w in enumerate(words):
        if cur:
            pause = w.start - cur[-1].end
            too_long = (w.end - cur[0].start) > max_len
            if pause >= gap or too_long:
                out.append(_mk(cur))
                cur = []
        cur.append(w)
        if _END_RE.search(w.text.strip()):
            out.append(_mk(cur))
            cur = []
    if cur:
        out.append(_mk(cur))
    return out


def _mk(ws: List[Word]) -> Sentence:
    return Sentence(start=ws[0].start, end=ws[-1].end,
                    text=" ".join(w.text.strip() for w in ws).strip(), words=list(ws))


def silence_gaps(sentences: Sequence[Sentence], min_gap: float = 4.0) -> List[tuple]:
    """(start, end) of speech-free gaps of at least min_gap seconds between sentences."""
    gaps = []
    for a, b in zip(sentences, sentences[1:]):
        if b.start - a.end >= min_gap:
            gaps.append((a.end, b.start))
    return gaps


def expand_window(peak: Peak, sentences: Sequence[Sentence], *, target: float = 60.0,
                  max_len: float = 180.0, min_len: float = 20.0, setup_share: float = 0.6,
                  break_gap: float = 6.0, payoff_min: float = 6.0, media_end: Optional[float] = None,
                  scene_cuts: Sequence[float] = ()) -> Window:
    """Grow a peak into a story window snapped to sentence boundaries.

    setup_share: fraction of the target spent BEFORE the peak (setup + build).
    break_gap:   a speech-free gap this long before the peak is treated as the
                 start of the situation (scene break) once min setup is reached.
    payoff_min:  always keep at least this many seconds after the peak (reaction).
    """
    t = peak.t
    if not sentences:
        s = max(0.0, t - target * setup_share)
        e = t + target * (1 - setup_share)
        if media_end is not None:
            e = min(e, media_end)
        return Window(start=s, end=e, peak=peak, setup_start=s, payoff_end=e)

    starts = [s.start for s in sentences]
    # sentence containing (or just before) the peak
    i = max(0, bisect.bisect_right(starts, t) - 1)

    setup_budget = target * setup_share
    min_setup = min(10.0, setup_budget)

    # --- walk back (setup) ---
    j = i
    while j > 0:
        prev, cur = sentences[j - 1], sentences[j]
        gap = cur.start - prev.end
        used = t - cur.start
        if used >= setup_budget:
            break
        if gap >= break_gap and used >= min_setup:
            break  # the situation starts after this pause
        if _crosses_cut(prev.start, cur.start, scene_cuts) and used >= min_setup:
            break
        if t - prev.start > max_len * setup_share:
            break
        j -= 1
    start = sentences[j].start

    # --- walk forward (payoff) ---
    k = i
    end = max(sentences[i].end, t + 1.0)
    n = len(sentences)
    while k + 1 < n:
        nxt = sentences[k + 1]
        gap = nxt.start - sentences[k].end
        after_peak = sentences[k].end - t
        length = nxt.end - start
        if length > max_len:
            break
        if after_peak >= payoff_min:
            if length > target:
                break
            if gap >= break_gap:
                break
        k += 1
        end = max(end, sentences[k].end)

    # guarantee a minimum reaction after the peak even if nobody talks
    end = max(end, t + payoff_min)

    # pad short windows symmetrically-ish
    if end - start < min_len:
        need = min_len - (end - start)
        start = max(0.0, start - need * 0.5)
        end = end + need * 0.5

    start = max(0.0, start - 0.3)   # tiny lead-in so the first word is not clipped
    end = end + 0.5
    if media_end is not None:
        end = min(end, media_end)
    if end - start > max_len:
        # keep the peak, trim the setup first
        start = max(start, end - max_len)
    return Window(start=round(start, 2), end=round(end, 2), peak=peak,
                  setup_start=round(start, 2), payoff_end=round(end, 2))


def _crosses_cut(a: float, b: float, cuts: Sequence[float]) -> bool:
    return any(a < c < b for c in cuts)


def trim_dead_air(window: Window, words: Sequence[Word], max_gap: float = 2.5, pad: float = 0.4) -> List[List[float]]:
    """Split the window into keep-pieces, dropping speech-free stretches longer than max_gap.

    The peak second is always kept (action can happen without speech), so a
    silent stretch that contains the peak is kept whole.
    """
    inside = [w for w in words if w.end > window.start and w.start < window.end]
    if not inside:
        return [[window.start, window.end]]
    pieces: List[List[float]] = []
    cur_s = window.start
    last_end = window.start
    for w in inside:
        if w.start - last_end > max_gap and not (last_end <= window.peak.t <= w.start):
            pieces.append([cur_s, min(window.end, last_end + pad)])
            cur_s = max(window.start, w.start - pad)
        last_end = max(last_end, w.end)
    pieces.append([cur_s, window.end])
    # drop zero/negative pieces and round
    return [[round(a, 2), round(b, 2)] for a, b in pieces if b - a > 0.3]


def merge_overlaps(windows: List[Window], max_overlap: float = 0.3) -> List[Window]:
    """Drop windows that overlap a better-scored window by more than max_overlap of the shorter one."""
    kept: List[Window] = []
    for w in sorted(windows, key=lambda x: -x.score):
        ok = True
        for k in kept:
            ov = min(w.end, k.end) - max(w.start, k.start)
            if ov > 0 and ov / max(1e-9, min(w.duration, k.duration)) > max_overlap:
                ok = False
                break
        if ok:
            kept.append(w)
    return kept


def window_text(window: Window, words: Sequence[Word]) -> str:
    return " ".join(w.text.strip() for w in words if w.start >= window.start and w.end <= window.end).strip()
