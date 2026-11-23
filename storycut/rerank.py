"""Optional LLM re-rank. OFF unless you set environment variables:

    STORYCUT_LLM=gemini
    GEMINI_API_KEY=...            (never commit this; read from env only)
    STORYCUT_GEMINI_MODEL=...     (optional, default below)

The LLM sees only the transcript of each candidate window and returns a
0-10 "is this a complete, understandable story with a payoff" score. The
final score is a blend: 0.6 * signal score + 0.4 * LLM score.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import List

from .models import Window

PROMPT = (
    "You judge short-form video clips cut from a livestream. For each candidate you get the "
    "spoken transcript. Score 0-10 how good it is as a standalone Short: is there a clear setup, "
    "a build, and a payoff/reaction a cold viewer understands? Reply ONLY with JSON: "
    '[{"id": <id>, "score": <0-10>, "title_idea": "<max 8 words>", "why": "<max 15 words>"}].\n\n'
)


def enabled() -> bool:
    return os.environ.get("STORYCUT_LLM", "").lower() == "gemini" and bool(os.environ.get("GEMINI_API_KEY"))


def rerank(windows: List[Window], blend: float = 0.4, timeout: int = 60) -> List[Window]:
    if not enabled() or not windows:
        return windows
    model = os.environ.get("STORYCUT_GEMINI_MODEL", "gemini-2.0-flash")
    body = PROMPT + "\n".join(f"[id {i}] {w.text[:1500]}" for i, w in enumerate(windows))
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": body}]}]}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        arr = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
    except Exception as e:  # network/model errors must never break the run
        for w in windows:
            w.llm = {"error": str(e)[:200]}
        return windows
    by_id = {int(x.get("id", -1)): x for x in arr if isinstance(x, dict)}
    for i, w in enumerate(windows):
        x = by_id.get(i)
        if not x:
            continue
        s = float(x.get("score", 0))
        w.llm = {"score": s, "title_idea": x.get("title_idea", ""), "why": x.get("why", "")}
        w.score = round((1 - blend) * w.score + blend * s, 1)
    return sorted(windows, key=lambda w: -w.score)
