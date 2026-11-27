"""Command line interface.

    storycut find  INPUT  [options]     find ranked story windows
    storycut render SRC --from-report out/candidates.json --rank 1 [layout options]
    storycut live URL|FILE [options]    follow a live stream and emit full stories as they close
    storycut layout SRC [--start S --duration D]  show the detected camera / face / game area
    storycut ui [--host 0.0.0.0] [--port 8770]   web dashboard (phone-friendly PWA)
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
    r.add_argument("--facecam", default=None,
                   help="auto (find the streamer camera), none, or a crop W:H:X:Y / x,y,w,h in source pixels")
    r.add_argument("--gameplay", default=None, help="auto (cut letterbox bars) or a crop W:H:X:Y / x,y,w,h")
    r.add_argument("--captions", default="clean", choices=("clean", "box", "karaoke", "pop", "minimal"),
                   help="caption style (default clean = white bold, black outline, no box)")
    r.add_argument("--caption-pos", default="seam", choices=("seam", "top", "middle", "lower-third"),
                   help="where captions sit (default seam = on the camera/gameplay join)")
    r.add_argument("--no-track", action="store_true", help="do not follow the face inside the camera box")
    r.add_argument("--max-zoom", type=float, default=1.5, help="how far face tracking may zoom in (default 1.5)")
    r.add_argument("--loudnorm", action="store_true", help="normalise loudness to -14 LUFS (Shorts/TikTok level)")
    r.add_argument("--thumb", action="store_true", help="also save the most expressive face frame as <out>.jpg")
    r.add_argument("--hook", default=None, help="hook text for the first 3 s; use | for line breaks")
    r.add_argument("--end-text", default=None, help="question shown in the last 2 s; use | for line breaks")
    r.add_argument("--watermark", default=None, help="PNG logo for the bottom-right corner")
    r.add_argument("--font", default="Poppins ExtraBold")
    r.add_argument("--crf", type=int, default=23)
    r.add_argument("--preset", default="superfast", help="x264 preset (default superfast: fastest that kept quality in our benchmark)")
    r.add_argument("--maxrate", default="7M", help="peak bitrate cap so a 60 s Short stays ~50 MB ('' = no cap)")
    r.add_argument("--threads", type=int, default=0, help="encoder threads (0 = auto)")
    r.add_argument("-o", "--out", default=None)

    lv = sub.add_parser("live", help="follow a live stream (Twitch/YouTube/Kick) or a growing file in real time")
    lv.add_argument("input", help="live channel/stream URL, a file another recorder is writing (--follow), "
                                  "or a local file to replay as if live (testing)")
    lv.add_argument("-o", "--out", default="storycut_live", help="output folder; reuse it to resume after a restart")
    lv.add_argument("--model", default="base", help="faster-whisper model (default base; tiny for weak CPUs)")
    lv.add_argument("--language", default=None, help="force language code, e.g. en, hi, ur")
    lv.add_argument("--transcribe", choices=("auto", "all", "gated"), default="auto",
                    help="all = transcribe everything; gated = only around spikes; auto = decide from CPU speed")
    lv.add_argument("--threads", type=int, default=0, help="whisper CPU threads (0 = all)")
    lv.add_argument("--target", type=float, default=60.0, help="target story length in seconds")
    lv.add_argument("--max-len", type=float, default=180.0)
    lv.add_argument("--min-score", type=float, default=5.5, help="emit stories scoring at least this (default 5.5)")
    lv.add_argument("--min-gap", type=float, default=60.0, help="seconds between two emitted peaks")
    lv.add_argument("--chat", default="auto", help="auto (Twitch IRC for twitch URLs), off, or a growing chat file")
    lv.add_argument("--follow", action="store_true", help="input is a .ts/.mkv/.flv file still being written")
    lv.add_argument("--speed", type=float, default=1.0, help="replay speed for local files (0 = as fast as possible)")
    lv.add_argument("--wait", type=float, default=0.0, help="minutes to keep checking an offline channel")
    lv.add_argument("--max-minutes", type=float, default=0.0, help="stop after this much stream time")
    lv.add_argument("--keep-min", type=float, default=45.0, help="minutes of recorded pieces kept on disk (0 = all)")
    lv.add_argument("--cut", action="store_true", help="cut each story to clipNN.mp4 + .srt as soon as it closes")
    lv.add_argument("--render", action="store_true", help="also render a 9:16 split-screen Short for each story")
    lv.add_argument("--facecam", default=None, help="auto, none or a crop W:H:X:Y for --render")
    lv.add_argument("--gameplay", default=None, help="auto or a crop W:H:X:Y for --render")
    lv.add_argument("--captions", default="clean", choices=("clean", "box", "karaoke", "pop", "minimal"),
                    help="caption style for --render")
    lv.add_argument("--hook", default=None, help="hook text for --render")
    lv.add_argument("-q", "--quiet", action="store_true")

    ly = sub.add_parser("layout", help="find the streamer camera, the face and the game area in a video")
    ly.add_argument("src")
    ly.add_argument("--start", type=_ts, default=0.0)
    ly.add_argument("--duration", type=_ts, default=60.0)

    u = sub.add_parser("ui", help="open the web dashboard (works on phones too)")
    u.add_argument("--jobs", default="storycut_jobs", help="folder for jobs and results (default storycut_jobs)")
    u.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open it from your phone on the same Wi-Fi")
    u.add_argument("--port", type=int, default=8770)
    u.add_argument("--no-demo", action="store_true", help="do not add the bundled demo job")
    u.add_argument("--export-icons", metavar="DIR", default=None, help="write the logo + PNG app icons to DIR and exit")

    a = p.parse_args(argv)

    if a.cmd == "ui":
        from .ui.server import export_icons, serve
        if a.export_icons:
            for n in export_icons(a.export_icons):
                print(os.path.join(a.export_icons, n))
            return 0
        serve(jobs=a.jobs, host=a.host, port=a.port, demo=not a.no_demo)
        return 0

    if a.cmd == "layout":
        from .frame import cv2_module, detect_layout
        res = detect_layout(a.src, a.start, a.duration)
        fmt = lambda b: "-" if not b else f"{b[2]}:{b[3]}:{b[0]}:{b[1]}"  # noqa: E731  (W:H:X:Y like --facecam)
        print(f"face finder : {'OpenCV' if cv2_module() else 'skin-colour fallback (pip install opencv-python-headless for better results)'}")
        print(f"facecam     : {fmt(res['facecam'])}")
        print(f"face        : {fmt(res['face'])}")
        print(f"gameplay    : {fmt(res['gameplay'])}")
        return 0

    if a.cmd == "live":
        from .live import LiveOptions, run_live
        lo = LiveOptions(out_dir=a.out, model=a.model, language=a.language, transcribe=a.transcribe,
                         threads=a.threads, target=a.target, max_len=a.max_len, min_score=a.min_score,
                         min_gap=a.min_gap, chat=a.chat, follow=a.follow, speed=a.speed, wait_live=a.wait,
                         max_minutes=a.max_minutes, keep_min=a.keep_min, cut=a.cut or a.render, render=a.render,
                         facecam=a.facecam, gameplay=a.gameplay, hook=a.hook, captions=a.captions,
                         log=not a.quiet)
        res = run_live(a.input, lo)
        for c in res["stories"]:
            print(f"#{c['rank']}  {c['score']:>4}/10  {c['start_hms']} -> {c['end_hms']}  ({c['duration']:.0f}s)  "
                  f"{c['title']}")
        return 0

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
               end_text=a.end_text, watermark=a.watermark, crf=a.crf, font=a.font,
               preset=a.preset, maxrate=a.maxrate or None, threads=a.threads, captions=a.captions,
               caption_pos=a.caption_pos, track=not a.no_track, loudnorm=a.loudnorm, thumbnail=a.thumb,
               max_zoom=a.max_zoom)
        print(out)
        try:
            with open(out + ".layout.json", encoding="utf-8") as fh:
                for n in json.load(fh).get("notes", []):
                    print("  " + n)
        except OSError:
            pass
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
