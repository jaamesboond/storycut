"""End-to-end: media -> signals -> peaks -> story windows -> scores -> outputs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

from . import chat as chatmod
from . import media, report, rerank, signals, story
from .models import Window
from .score import score_window
from .transcribe import transcribe


@dataclass
class FindOptions:
    out_dir: str = "storycut_out"
    start: float = 0.0
    duration: Optional[float] = None
    model: str = "small"
    language: Optional[str] = None
    chat: Optional[str] = None
    top: int = 5
    candidates: int = 12
    target: float = 60.0
    max_len: float = 180.0
    min_len: float = 20.0
    min_gap: float = 90.0
    scenes: bool = False
    trim: bool = True
    cut: bool = False
    srt: bool = True
    llm: bool = False
    weights: dict = field(default_factory=dict)
    log: bool = True


def _log(opts: FindOptions, msg: str) -> None:
    if opts.log:
        print(f"[storycut] {msg}", flush=True)


def find(input_path: str, opts: FindOptions) -> dict:
    os.makedirs(opts.out_dir, exist_ok=True)
    chat_path = opts.chat
    if media.is_url(input_path):
        _log(opts, "downloading with yt-dlp ...")
        got = media.download(input_path, os.path.join(opts.out_dir, "download"))
        input_path, chat_path = got["media"], chat_path or got["chat"]

    total = media.probe_duration(input_path)
    dur = opts.duration if opts.duration else max(0.0, total - opts.start)
    dur = min(dur, total - opts.start)
    tag = f"{Path(input_path).stem}_{int(opts.start)}_{int(dur)}"
    wav = os.path.join(opts.out_dir, f"{tag}.wav")
    if not os.path.exists(wav):
        _log(opts, f"extracting audio {opts.start:.0f}s + {dur:.0f}s ...")
        media.extract_audio(input_path, wav, start=opts.start, duration=dur)
    samples, sr = media.read_wav(wav)
    n = int(len(samples) // sr)

    _log(opts, f"transcribing with faster-whisper ({opts.model}) ...")
    words = transcribe(wav, model_size=opts.model, language=opts.language,
                       cache_path=os.path.join(opts.out_dir, f"{tag}.words.json"))
    _log(opts, f"{len(words)} words")

    loud = signals.rms_per_second(samples, sr)
    onset = signals.onset_per_second(samples, sr)
    cues = signals.cue_events(words)
    curves = {"loud": loud, "onset": onset, "cues": signals.events_to_curve(cues, n),
              "speech": signals.speech_curve(words, n)}
    used = ["loudness", "onsets", "transcript cues", "speech density"]

    if chat_path and os.path.exists(chat_path):
        offs = chatmod.load_chat_offsets(chat_path)
        c = chatmod.chat_curve(offs, n, start=opts.start)
        if c.sum() > 0:
            curves["chat"] = c
            used.append(f"chat ({int(c.sum())} msgs)")

    cuts: List[float] = []
    if opts.scenes and media.has_video(input_path):
        _log(opts, "detecting scene changes ...")
        cuts = media.scene_cuts(input_path, start=opts.start, duration=dur)
        curves["scene"] = signals.events_to_curve([(t, 1.0) for t in cuts], n, spread=1)
        used.append(f"scene cuts ({len(cuts)})")

    curves = {k: (np.asarray(v)[:n] if v is not None else None) for k, v in curves.items()}
    combined, parts = signals.combine(curves, opts.weights)
    peaks = signals.find_peaks(combined, top_k=opts.candidates, min_gap=opts.min_gap, parts=parts)
    _log(opts, f"{len(peaks)} peaks")

    sentences = story.sentences_from_words(words)
    windows: List[Window] = []
    for p in peaks:
        w = story.expand_window(p, sentences, target=opts.target, max_len=opts.max_len, min_len=opts.min_len,
                                media_end=float(n), scene_cuts=cuts)
        w.text = story.window_text(w, words)
        if opts.trim:
            w.keep = story.trim_dead_air(w, words)
        score_window(w, combined, parts, words, cues)
        windows.append(w)

    windows = story.merge_overlaps(windows)
    windows.sort(key=lambda w: -w.score)
    if opts.llm:
        windows = rerank.rerank(windows)
    windows = windows[: opts.top]

    meta = {"input_name": Path(input_path).name, "offset": opts.start, "analysed_seconds": n,
            "signals": used, "model": opts.model, "target": opts.target, "max_len": opts.max_len,
            "tool": "storycut"}
    data = report.to_json(windows, meta, offset=opts.start)
    report.write_json(os.path.join(opts.out_dir, "candidates.json"), data)
    with open(os.path.join(opts.out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(report.to_markdown(data))

    for rank, w in enumerate(windows, 1):
        if opts.srt:
            with open(os.path.join(opts.out_dir, f"clip{rank:02d}.srt"), "w", encoding="utf-8") as f:
                f.write(report.window_srt(w, words))
        if opts.cut:
            out = os.path.join(opts.out_dir, f"clip{rank:02d}.mp4")
            a, b = w.start + opts.start, w.end + opts.start
            if media.has_video(input_path):
                media.cut_clip(input_path, out, a, b)
            else:
                out = out[:-4] + ".m4a"
                media._run(["ffmpeg", "-y", "-v", "error", "-i", input_path, "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                            "-c:a", "aac", "-b:a", "160k", out])
    # words for the render step (relative to chunk start)
    report.write_json(os.path.join(opts.out_dir, "words.json"),
                      {"offset": opts.start, "words": [w.to_dict() for w in words]})
    _log(opts, f"done -> {os.path.join(opts.out_dir, 'report.md')}")
    return data
