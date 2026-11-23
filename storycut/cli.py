"""Command line interface.

    storycut find  INPUT  [options]     find ranked story windows
    storycut render SRC --from-report out/candidates.json --rank 1 [layout options]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .chat import parse_ts


def _ts(s: str) -> float:
    t = parse_ts(s)
    if t is None:
        raise argparse.ArgumentTypeError(f"bad time: {s} (use seconds or HH:MM:SS)")
    return t


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="storycut", description="Find full-story clip windows in long streams.")
    p.add_argument("--version", action="version", version=f"storycut {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="analyse a file or VOD URL and rank story windows")
    f.add_argument("input", help="local video/audio file, or a YouTube/Twitch/Kick VOD URL")
    f.add_argument("-o", "--out", default="storycut_out", help="output folder (default storycut_out)")
    f.add_argument("--start", type=_ts, default=0.0, help="analyse from this time (s or HH:MM:SS)")
    f.add_argument("--duration", type=_ts, default=None, help="analyse this long (s or HH:MM:SS)")
    f.add_argument("--model", default="small", help="faster-whisper model: tiny/base/small/medium (default small)")
    f.add_argument("--language", default=None, help="force language code, e.g. en, hi, ur (default auto)")
    f.add_argument("--chat", default=None, help="chat replay file (yt-dlp live_chat.json, Twitch JSON, or timestamps)")
    f.add_argument("--top", type=int, default=5, help="how many windows to output")
    f.add_argument("--target", type=float, default=60.0, help="target story length in seconds (default 60)")
    f.add_argument("--max-len", type=float, default=180.0, help="hard max story length (default 180)")
    f.add_argument("--min-gap", type=float, default=90.0, help="min seconds between peaks")
    f.add_argument("--scenes", action="store_true", help="also use ffmpeg scene-change detection (slower)")
    f.add_argument("--no-trim", action="store_true", help="do not compute dead-air trimmed pieces")
    f.add_argument("--cut", action="store_true", help="also cut each window to clipNN.mp4")
    f.add_argument("--no-srt", action="store_true", help="do not write clipNN.srt captions")
    f.add_argument("--llm", action="store_true", help="LLM re-rank (needs STORYCUT_LLM=gemini and GEMINI_API_KEY)")
    f.add_argument("-q", "--quiet", action="store_true")

    r = sub.add_parser("render", help="render one window as a vertical split-screen Short")
    r.add_argument("src", help="source video file (same file that was analysed)")
    r.add_argument("--from-report", required=True, help="candidates.json written by `find`")
    r.add_argument("--rank", type=int, default=1)
    r.add_argument("--words", default=None, help="words.json (default: next to the report)")
    r.add_argument("--start", type=_ts, default=None, help="override window start (source time)")
    r.add_argument("--end", type=_ts, default=None, help="override window end (source time)")
    r.add_argument("--facecam", default=None, help="facecam crop W:H:X:Y in source pixels")
    r.add_argument("--gameplay", default=None, help="gameplay crop W:H:X:Y in source pixels")
    r.add_argument("--hook", default=None, help="hook text for the first 3 s; use | for line breaks")
    r.add_argument("--end-text", default=None, help="question shown in the last 2 s; use | for line breaks")
    r.add_argument("--watermark", default=None, help="PNG logo for the bottom-right corner")
    r.add_argument("--font", default="Poppins ExtraBold")
    r.add_argument("--crf", type=int, default=23)
    r.add_argument("-o", "--out", default=None)

    a = p.parse_args(argv)

    if a.cmd == "find":
        from .pipeline import FindOptions, find
        opts = FindOptions(out_dir=a.out, start=a.start, duration=a.duration, model=a.model,
                           language=a.language, chat=a.chat, top=a.top, target=a.target, max_len=a.max_len,
                           min_gap=a.min_gap, scenes=a.scenes, trim=not a.no_trim, cut=a.cut,
                           srt=not a.no_srt, llm=a.llm, log=not a.quiet)
        data = find(a.input, opts)
        for c in data["candidates"]:
            print(f"#{c['rank']}  {c['score']:>4}/10  {c['start_hms']} -> {c['end_hms']}  ({c['duration']:.0f}s)  "
                  f"peak {c['peak_hms']}")
        return 0

    if a.cmd == "render":
        from .render import load_words, render
        with open(a.from_report, "r", encoding="utf-8") as fh:
            rep = json.load(fh)
        cand = next((c for c in rep["candidates"] if c["rank"] == a.rank), None)
        if cand is None:
            print(f"rank {a.rank} not in report", file=sys.stderr)
            return 2
        words_path = a.words or os.path.join(os.path.dirname(os.path.abspath(a.from_report)), "words.json")
        words, _ = load_words(words_path)
        s = a.start if a.start is not None else cand["start"]
        e = a.end if a.end is not None else cand["end"]
        out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.from_report)), f"short_rank{a.rank}.mp4")
        render(a.src, s, e, words, out, facecam=a.facecam, gameplay=a.gameplay, hook=a.hook,
               end_text=a.end_text, watermark=a.watermark, crf=a.crf, font=a.font)
        print(out)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
