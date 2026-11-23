"""Signal processing: turn loudness, chat, keyword cues and scene cuts into one
per-second "excitement" curve, then pick peaks.

Everything here is pure Python + numpy so it is easy to test.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .models import Peak, Word

# ---------------------------------------------------------------------------
# Keyword / emotion cues. Weight ~ how strongly the phrase signals a moment.
# Includes common Hindi/Urdu (romanised) exclamations used by South Asian
# streamers. Matching is done on lowercased words with punctuation stripped.
# ---------------------------------------------------------------------------
CUE_PHRASES: Dict[str, float] = {
    # shock / disbelief
    "no way": 2.5, "what the": 2.0, "oh my god": 2.5, "oh my gosh": 2.0, "omg": 2.0,
    "are you serious": 2.0, "are you kidding": 2.0, "bro what": 2.0, "what just happened": 3.0,
    "i can't believe": 2.5, "yo": 0.8, "bro": 0.6, "wait": 0.8, "hold on": 1.0,
    "let's go": 2.5, "lets go": 2.5, "clutch": 2.0, "insane": 1.5, "crazy": 1.2,
    "nah": 1.0, "noo": 2.0, "nooo": 2.5, "no no no": 2.5, "why": 0.7, "help": 1.2,
    "run": 0.8, "jump": 0.8, "he's dead": 2.0, "i'm dead": 1.5, "wasted": 1.5,
    "it was you": 2.0, "the whole time": 1.2, "plot twist": 2.5, "ending": 1.0,
    # emotion
    "i'm crying": 2.5, "crying": 1.5, "sad": 1.0, "rip": 1.2, "goodbye": 1.0,
    "laughing": 1.0,
    "scream": 1.5, "scared": 1.5, "jumpscare": 2.5,
    # Hindi / Urdu (romanised)
    "arey": 1.2, "arre": 1.2, "yaar": 0.6, "bhai": 0.6, "kya": 0.5, "kya hua": 1.5,
    "kya baat hai": 2.0, "oh bhai": 2.0, "bhai bhai": 1.5, "mar gaya": 2.0, "mar gaye": 2.0,
    "bach gaya": 2.0, "bach gaye": 2.0, "chalo": 0.6, "bas": 0.5, "nahi": 0.8,
    "nahi nahi": 2.0, "ruko": 1.0, "bhago": 1.5, "allah": 1.0, "ya allah": 2.0,
    "haan": 0.3, "sach mein": 1.5, "pagal": 1.2, "khatam": 1.2,
}

# Profanity is a strong excitement cue in streams (and is also what the render
# helper beeps). Kept short and generic on purpose.
PROFANITY: List[str] = [
    "fuck", "fucking", "fucked", "motherfucker", "motherfuck", "shit", "bitch", "bastard", "damn",
    "asshole", "dick", "nigga", "wtf",
    "bc", "mc", "bsdk", "bhenchod", "behenchod", "madarchod", "chutiya", "gandu", "harami",
    "kutta", "kamina", "saala", "sala",
]

LAUGH_RE = re.compile(r"^(ha){2,}h?$|^(he){2,}$|^l+o+l+$|^lmao+$")
_PUNCT_RE = re.compile(r"[^\w\s']+", re.UNICODE)


def normalize(text: str) -> str:
    """Lowercase and strip punctuation (keeps apostrophes and unicode letters)."""
    return re.sub(r"\s+", " ", _PUNCT_RE.sub(" ", text.lower())).strip()


def is_profane(token: str) -> bool:
    t = normalize(token).replace("'", "")
    if not t:
        return False
    for p in PROFANITY:
        if t == p or (len(p) >= 4 and t.startswith(p)):
            return True
    return False


def cue_events(words: Sequence[Word], max_ngram: int = 4) -> List[tuple]:
    """Return a list of (time, weight, label) for keyword cues in the transcript.

    Repeated words (e.g. "no no no") and shouted short words are caught by the
    phrase table; laughter tokens and profanity get their own labels.
    """
    toks = [normalize(w.text) for w in words]
    events: List[tuple] = []
    n = len(toks)
    i = 0
    while i < n:
        matched = False
        for k in range(min(max_ngram, n - i), 0, -1):
            phrase = " ".join(t for t in toks[i:i + k] if t)
            if phrase in CUE_PHRASES:
                events.append((words[i].start, CUE_PHRASES[phrase], phrase))
                i += k
                matched = True
                break
        if matched:
            continue
        tok = toks[i]
        if tok and LAUGH_RE.match(tok.replace(" ", "")):
            events.append((words[i].start, 2.0, "laugh"))
        elif is_profane(tok):
            events.append((words[i].start, 1.2, "swear"))
        i += 1
    return events


# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------

def robust_z(x: Sequence[float]) -> np.ndarray:
    """Robust z-score using median and MAD; flat input returns zeros."""
    a = np.asarray(x, dtype=float)
    if a.size == 0:
        return a
    med = np.median(a)
    mad = np.median(np.abs(a - med)) * 1.4826
    if mad < 1e-9:
        sd = a.std()
        if sd < 1e-9:
            return np.zeros_like(a)
        return (a - a.mean()) / sd
    return (a - med) / mad


def smooth(x: Sequence[float], win: int = 3) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if win <= 1 or a.size == 0:
        return a
    k = np.ones(win) / win
    return np.convolve(a, k, mode="same")


def rms_per_second(samples: np.ndarray, sr: int) -> np.ndarray:
    """RMS loudness in dBFS for each whole second of mono float samples."""
    n = len(samples) // sr
    if n == 0:
        return np.zeros(0)
    s = samples[: n * sr].reshape(n, sr).astype(np.float64)
    rms = np.sqrt(np.mean(s * s, axis=1)) + 1e-9
    return 20 * np.log10(rms)


def onset_per_second(samples: np.ndarray, sr: int, hop: int = 1024) -> np.ndarray:
    """Spectral-flux-like onset strength summed per whole second (cheap)."""
    n_sec = len(samples) // max(sr, 1)
    n = len(samples) // hop
    out = np.zeros(n_sec)
    if n < 2 or n_sec == 0:
        return out
    frames = samples[: n * hop].reshape(n, hop)
    energy = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    flux = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    sec_idx = (np.arange(n) * hop) // sr
    ok = sec_idx < n_sec
    np.add.at(out, sec_idx[ok], flux[ok])
    return out


def events_to_curve(events: Iterable[tuple], duration_s: int, spread: int = 2) -> np.ndarray:
    """Place weighted (time, weight, ...) events on a per-second curve with a small spread."""
    c = np.zeros(max(0, int(duration_s)))
    for ev in events:
        t, w = int(ev[0]), float(ev[1])
        for d in range(-spread, spread + 1):
            j = t + d
            if 0 <= j < len(c):
                c[j] += w * (1.0 - abs(d) / (spread + 1))
    return c


def speech_curve(words: Sequence[Word], duration_s: int) -> np.ndarray:
    """Words per second (talk density)."""
    c = np.zeros(max(0, int(duration_s)))
    for w in words:
        j = int(w.start)
        if 0 <= j < len(c):
            c[j] += 1
    return c


DEFAULT_WEIGHTS = {"loud": 1.0, "onset": 0.5, "cues": 1.0, "chat": 1.2, "scene": 0.3, "speech": 0.3}


def combine(curves: Dict[str, Optional[Sequence[float]]], weights: Optional[Dict[str, float]] = None,
            smooth_win: int = 5) -> tuple:
    """Combine named per-second curves into one excitement curve.

    Each curve is robust-z normalised and clipped to [-1, 6] so one signal
    cannot dominate. Missing (None/empty) curves are skipped and the weights
    of the remaining ones are used as-is. Returns (combined, normalised_parts).
    """
    weights = dict(DEFAULT_WEIGHTS, **(weights or {}))
    present = {k: np.asarray(v, dtype=float) for k, v in curves.items() if v is not None and len(v) > 0}
    if not present:
        return np.zeros(0), {}
    n = max(len(v) for v in present.values())
    parts = {}
    total = np.zeros(n)
    for k, v in present.items():
        vv = np.zeros(n)
        vv[: len(v)] = v
        z = np.clip(robust_z(smooth(vv, 3)), -1.0, 6.0)
        parts[k] = z
        total += weights.get(k, 0.5) * z
    wsum = sum(weights.get(k, 0.5) for k in present)
    total = total / max(wsum, 1e-9)
    return smooth(total, smooth_win), parts


def find_peaks(curve: Sequence[float], top_k: int = 10, min_gap: float = 90.0,
               min_value: float = 0.5, parts: Optional[dict] = None, offset: float = 0.0) -> List[Peak]:
    """Greedy non-maximum suppression: highest seconds first, at least min_gap apart."""
    c = np.asarray(curve, dtype=float)
    order = np.argsort(-c)
    chosen: List[Peak] = []
    for idx in order:
        v = float(c[idx])
        if v < min_value or len(chosen) >= top_k:
            break
        if all(abs(idx - (p.t - offset)) >= min_gap for p in chosen):
            src = {k: round(float(p[idx]), 2) for k, p in (parts or {}).items() if idx < len(p)}
            chosen.append(Peak(t=float(idx) + offset, value=round(v, 3), sources=src))
    return chosen
