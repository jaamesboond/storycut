"""Speech-to-text with word timestamps via faster-whisper (runs on CPU, int8)."""

from __future__ import annotations

import json
import os
from typing import List, Optional

from .models import Word


def transcribe(wav_path: str, model_size: str = "small", language: Optional[str] = None,
               cache_path: Optional[str] = None, threads: int = 0, beam_size: int = 1) -> List[Word]:
    """Return a list of Word objects. Results are cached as JSON next to cache_path."""
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return [Word(**w) for w in json.load(f)["words"]]
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("faster-whisper is not installed: pip install faster-whisper") from e

    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=threads or 0)
    segments, info = model.transcribe(wav_path, language=language, word_timestamps=True, vad_filter=True,
                                      beam_size=beam_size, condition_on_previous_text=False)
    words: List[Word] = []
    for seg in segments:
        for w in (seg.words or []):
            if w.word.strip():
                words.append(Word(start=round(w.start, 2), end=round(w.end, 2), text=w.word.strip(),
                                  prob=round(float(w.probability), 3)))
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"language": getattr(info, "language", language), "model": model_size,
                       "words": [w.to_dict() for w in words]}, f, ensure_ascii=False)
    return words
