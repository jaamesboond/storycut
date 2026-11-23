"""Small data classes shared across modules. Pure Python, no heavy deps."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


@dataclass
class Peak:
    t: float          # time of the peak in seconds (absolute, source timeline)
    value: float      # combined excitement value at the peak
    sources: dict = field(default_factory=dict)  # per-signal contribution at the peak


@dataclass
class Window:
    start: float
    end: float
    peak: Peak
    setup_start: float = 0.0
    payoff_end: float = 0.0
    keep: List[List[float]] = field(default_factory=list)  # optional [start, end] pieces after dead-air trim
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    text: str = ""
    llm: Optional[dict] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def trimmed_duration(self) -> float:
        if not self.keep:
            return self.duration
        return sum(max(0.0, b - a) for a, b in self.keep)
