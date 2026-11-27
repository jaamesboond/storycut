"""Live mode: follow a live stream (or a growing / local file) and emit FULL-STORY
clip candidates while the stream is still running.

    storycut live https://www.twitch.tv/<channel>        record + analyse a live stream
    storycut live recording.ts --follow                   follow a file another recorder is writing
    storycut live vod.mp4 --speed 4                       replay a file as if it were live (testing)

How it works (CPU-light, made for a small VPS):

  1. A recorder writes the stream as short MPEG-TS pieces (ffmpeg segment muxer).
     If the stream drops it re-resolves the URL and starts a new part; the story
     timeline just continues. Everything lives in the output folder, so after a
     machine restart `storycut live` with the same -o resumes where it stopped.
  2. Each new piece is decoded to 16 kHz mono once: loudness + onset per second.
  3. Speech is transcribed with faster-whisper (tiny/base by default). In `auto`
     mode storycut measures how fast whisper runs; if it can keep up it
     transcribes everything, otherwise it only transcribes the minutes around a
     spike (the setup before it and the payoff after it).
  4. A spike opens a *pending story*. storycut waits until the payoff has
     actually closed (excitement back to normal, a pause in talk, or a time cap)
     and only then grows the window back to the setup, snaps it to sentence
     ends, scores it 0-10 with reasons and emits it. Nothing is cut mid-reaction.
  5. Optional: cut each emitted story to clipNN.mp4 (+ .srt) and render a 9:16
     split-screen Short straight away.

Outputs in the folder: candidates.json (same shape as `find`, plus title,
phases and latency), live.jsonl (event log), words.json, state.json, report.md.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import signal as _signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional

import numpy as np

from . import report, signals, story
from .models import Peak, Window, Word
from .score import score_window

SR = 16000
YTDLP_CANDIDATES = ("yt-dlp", os.path.expanduser("~/.local/bin/yt-dlp"))


# ---------------------------------------------------------------------------
# options + small helpers
# ---------------------------------------------------------------------------

@dataclass
class LiveOptions:
    out_dir: str = "storycut_live"
    model: str = "base"
    language: Optional[str] = None
    transcribe: str = "auto"          # auto | all | gated
    threads: int = 0
    segment: float = 6.0              # recorder piece length (s)
    step: float = 5.0                 # analysis tick (s)
    context_min: float = 15.0         # baseline: last N minutes define "normal"
    keep_min: float = 45.0            # delete recorder pieces older than this (0 = keep all)
    trigger: float = 1.1              # combined-curve value that opens a pending story
    soft_trigger: float = 0.4         # gated mode: transcribe bumps above this so speech cues can count
    close_level: float = 0.35         # payoff closed when excitement stays below this ...
    close_quiet: float = 6.0          # ... for this many seconds
    payoff_min: float = 6.0           # always wait at least this long after the peak
    payoff_max: float = 45.0          # stop waiting for the payoff after this
    target: float = 60.0
    min_len: float = 20.0
    max_len: float = 180.0
    min_gap: float = 60.0             # seconds between emitted peaks
    min_score: float = 5.5            # below this a story is logged as "dropped", not emitted
    cut: bool = False
    render: bool = False
    captions: str = "clean"
    facecam: Optional[str] = None
    gameplay: Optional[str] = None
    hook: Optional[str] = None
    chat: str = "auto"                # auto (Twitch IRC for twitch URLs) | off | path to a growing chat file
    follow: bool = False              # input is a file another program is still writing
    speed: float = 1.0                # replay speed for local files (testing)
    wait_live: float = 0.0            # if the channel is offline, keep checking this many minutes
    max_minutes: float = 0.0          # stop after this much stream time (0 = until it ends)
    log: bool = True
    on_event: Optional[Callable[[dict], None]] = None

    def public(self) -> dict:
        d = asdict(self)
        d.pop("on_event", None)
        return d


def find_ytdlp() -> Optional[str]:
    for c in YTDLP_CANDIDATES:
        p = shutil.which(c) or (c if os.path.isfile(c) and os.access(c, os.X_OK) else None)
        if p:
            return p
    return None


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s or ""))


def twitch_channel(url: str) -> Optional[str]:
    m = re.match(r"^https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,25})/?(?:\?.*)?$", url or "")
    return m.group(1).lower() if m and m.group(1).lower() not in ("videos", "directory") else None


def short_title(text: str, n: int = 60) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


CATEGORY_BY_CUE = [
    ("funny", {"laugh", "laughing", "i'm dead", "lmao"}),
    ("clutch", {"clutch", "let's go", "lets go", "bach gaya", "bach gaye", "insane"}),
    ("shock", {"no way", "what the", "what just happened", "oh my god", "i can't believe", "bro what",
               "are you serious", "plot twist", "kya hua", "oh bhai"}),
    ("scary", {"jumpscare", "scream", "scared", "help", "run", "bhago"}),
    ("sad", {"i'm crying", "crying", "sad", "rip", "goodbye"}),
    ("fail", {"wasted", "he's dead", "mar gaya", "mar gaye", "nooo", "noo", "no no no", "nahi nahi"}),
]


def categorise(labels) -> str:
    best, hits = "moment", 0
    for cat, keys in CATEGORY_BY_CUE:
        h = sum(1 for x in labels if x in keys)
        if h > hits:
            best, hits = cat, h
    return best


def story_title(w: Window, words: List[Word], cues: List[tuple], offset: float = 0.0) -> dict:
    """A human-readable label built from what was actually said: the strongest
    reaction phrase at the peak plus the line that set it up. No AI needed."""
    near = [e for e in cues if w.peak.t - 6 <= e[0] <= w.peak.t + 12]
    after = [e for e in cues if w.peak.t - 6 <= e[0] <= w.end]
    labels = [e[2] for e in after]
    cat = categorise(labels)
    shout = ""
    strong = sorted((e for e in near if e[2] not in ("swear",)), key=lambda e: -e[1])
    if strong:
        shout = strong[0][2]
    # the setup line: last full sentence that ends before the peak
    sents = story.sentences_from_words([x for x in words if w.start <= x.start <= w.peak.t])
    setup_line = ""
    for s in reversed(sents):
        if len(s.text.split()) >= 4:
            setup_line = s.text
            break
    if shout and setup_line:
        title = f'"{shout.upper()}" after: {short_title(setup_line, 48)}'
    elif shout:
        title = f'"{shout.upper()}" ({cat})'
    elif setup_line:
        title = short_title(setup_line, 60)
    else:
        title = f"{cat.capitalize()} at {report.fmt_hms(w.peak.t + offset)}"
    return {"title": title, "category": cat, "shout": shout, "setup_line": setup_line}


def story_phases(w: Window, combined: np.ndarray, level: float = 0.2) -> dict:
    """setup -> build -> peak -> payoff boundaries (stream seconds)."""
    t = int(w.peak.t)
    a = int(w.start)
    build = a
    for i in range(min(t, len(combined) - 1), a, -1):
        if combined[i] < level:
            build = i
            break
    build = max(float(w.start), min(float(build), w.peak.t - 1.0))
    return {"setup": [w.start, build], "build": [build, max(float(build), w.peak.t - 1.0)],
            "peak": [max(w.start, w.peak.t - 1.0), min(w.end, w.peak.t + 2.0)],
            "payoff": [min(w.end, w.peak.t + 2.0), w.end]}


# ---------------------------------------------------------------------------
# audio decoding of one piece
# ---------------------------------------------------------------------------

def decode_audio(src: str, start: Optional[float] = None, duration: Optional[float] = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0 and not r.stdout:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[-300:] or "ffmpeg decode failed")
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def probe_duration(path: str) -> Optional[float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# recorder: stream / replay -> numbered MPEG-TS pieces
# ---------------------------------------------------------------------------

class NotLive(Exception):
    pass


def resolve_stream(url: str) -> dict:
    """Ask yt-dlp for the live stream's media URLs. Raises NotLive when offline."""
    exe = find_ytdlp()
    if not exe:
        raise RuntimeError("yt-dlp not found (pip install yt-dlp)")
    r = subprocess.run([exe, "-f", "best/bestvideo+bestaudio", "-g", "--no-playlist", "--no-warnings", url],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    urls = [x for x in r.stdout.splitlines() if x.startswith("http")]
    if r.returncode != 0 or not urls:
        err = (r.stderr or "").strip()
        if re.search(r"not (currently )?live|offline|is not live|will begin|no video formats", err, re.I):
            raise NotLive(err[-200:])
        raise RuntimeError(err[-300:] or "yt-dlp could not resolve the stream")
    return {"urls": urls[:2]}


class Recorder(threading.Thread):
    """Runs ffmpeg's segment muxer, restarting on drops. Each run is a new 'part'."""

    def __init__(self, session: "LiveSession", source: str, opts: LiveOptions, start_offset: float = 0.0):
        super().__init__(daemon=True)
        self.s, self.source, self.o = session, source, opts
        self.start_offset = start_offset    # for local replays after a resume
        self.proc: Optional[subprocess.Popen] = None
        self.stop_ev = threading.Event()
        self.finished = False               # source ended for good
        self.error: Optional[str] = None

    def _cmd(self, part: int) -> List[str]:
        d = self.s.seg_dir
        out = os.path.join(d, f"p{part:03d}_%05d.ts")
        lst = os.path.join(d, f"p{part:03d}.csv")
        base = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
        if is_url(self.source):
            info = resolve_stream(self.source)
            inp = []
            for u in info["urls"]:
                inp += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
                        "-rw_timeout", "20000000", "-i", u]
            maps = ["-map", "0:v:0?", "-map", f"{len(info['urls']) - 1}:a:0?"]
        else:
            inp = []
            if self.o.speed and self.o.speed > 0:
                inp += ["-readrate", f"{self.o.speed:g}"]
            if self.start_offset > 0:
                inp += ["-ss", f"{self.start_offset:.3f}"]
            inp += ["-i", self.source]
            maps = ["-map", "0:v:0?", "-map", "0:a:0?"]
        return base + inp + maps + ["-c", "copy", "-f", "segment", "-segment_time", f"{self.o.segment:g}",
                                    "-reset_timestamps", "1", "-segment_format", "mpegts",
                                    "-segment_list", lst, "-segment_list_type", "csv", out]

    def run(self):
        waited = 0.0
        while not self.stop_ev.is_set():
            part = self.s.next_part()
            try:
                cmd = self._cmd(part)
            except NotLive as e:
                if waited >= self.o.wait_live * 60:
                    self.error = "channel is not live"
                    self.s.event("offline", detail=str(e)[:200])
                    self.finished = True
                    return
                self.s.event("waiting", detail="not live yet, checking again in 60 s")
                self.stop_ev.wait(60)
                waited += 60
                continue
            except Exception as e:  # resolve error: retry a few times
                self.s.event("error", detail=str(e)[:300])
                self.stop_ev.wait(30)
                waited += 30
                if waited > max(600, self.o.wait_live * 60):
                    self.error = str(e)[:300]
                    self.finished = True
                    return
                continue
            self.s.event("recording", part=part)
            with open(os.path.join(self.s.dir, "ffmpeg.log"), "ab") as log:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                self.proc.wait()
            rc = self.proc.returncode
            if self.stop_ev.is_set():
                return
            if not is_url(self.source):
                self.finished = True
                if rc not in (0, None, 255):
                    self.error = f"ffmpeg exited with {rc}"
                return
            # live stream dropped (ad break, network, end of stream): try again
            self.s.event("dropped", part=part, detail=f"ffmpeg exited with {rc}; reconnecting")
            self.stop_ev.wait(5)

    def stop(self):
        self.stop_ev.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.send_signal(_signal.SIGINT)   # lets ffmpeg close the last piece cleanly
                self.proc.wait(10)
            except Exception:
                self.proc.kill()


# ---------------------------------------------------------------------------
# optional live chat (Twitch IRC, anonymous, read-only) or a growing text file
# ---------------------------------------------------------------------------

class TwitchChat(threading.Thread):
    def __init__(self, channel: str, clock: Callable[[], float]):
        super().__init__(daemon=True)
        self.channel, self.clock = channel, clock
        self.counts: Dict[int, int] = {}
        self.clip_calls: List[float] = []
        self.stop_ev = threading.Event()
        self.ok = False

    def run(self):
        while not self.stop_ev.is_set():
            try:
                sock = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=15)
                sock.sendall(f"NICK justinfan{int(time.time()) % 90000 + 10000}\r\nJOIN #{self.channel}\r\n".encode())
                buf = b""
                self.ok = True
                while not self.stop_ev.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for ln in lines:
                        if ln.startswith(b"PING"):
                            sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG " in ln:
                            t = self.clock()
                            self.counts[int(t)] = self.counts.get(int(t), 0) + 1
                            msg = ln.split(b" :", 1)[-1].decode("utf-8", "replace").lower()
                            if re.search(r"\bclip (it|that|this)\b|^clip\b", msg):
                                self.clip_calls.append(t)
                sock.close()
            except OSError:
                self.ok = False
            self.stop_ev.wait(10)


class FileChat:
    """A growing file with one timestamp (stream seconds) per line, optionally
    followed by the message. Lets any chat logger feed storycut."""

    def __init__(self, path: str):
        self.path, self.pos = path, 0
        self.counts: Dict[int, int] = {}
        self.clip_calls: List[float] = []
        self.ok = os.path.exists(path)

    def poll(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            for ln in f:
                parts = re.split(r"[,\t ]", ln.strip(), maxsplit=1)
                try:
                    t = float(parts[0])
                except (ValueError, IndexError):
                    continue
                self.counts[int(t)] = self.counts.get(int(t), 0) + 1
                if len(parts) > 1 and re.search(r"\bclip (it|that|this)\b", parts[1].lower()):
                    self.clip_calls.append(t)
            self.pos = f.tell()
        self.ok = True


# ---------------------------------------------------------------------------
# whisper wrapper (model loaded once, measures its own speed)
# ---------------------------------------------------------------------------

class Transcriber:
    def __init__(self, model: str, language: Optional[str], threads: int = 0):
        self.model_name, self.language, self.threads = model, language, threads
        self._m = None
        self.rtf = None   # processing seconds per audio second (lower = faster)

    def _model(self):
        if self._m is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("faster-whisper is not installed: pip install faster-whisper") from e
            self._m = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=self.threads or 0)
        return self._m

    def __call__(self, audio: np.ndarray, offset: float) -> List[Word]:
        if len(audio) < SR // 2:
            return []
        t0 = time.time()
        segs, _info = self._model().transcribe(audio, language=self.language, word_timestamps=True,
                                               vad_filter=True, beam_size=1, condition_on_previous_text=False)
        out = []
        for seg in segs:
            for w in (seg.words or []):
                if w.word.strip():
                    out.append(Word(start=round(w.start + offset, 2), end=round(w.end + offset, 2),
                                    text=w.word.strip(), prob=round(float(w.probability), 3)))
        dt = time.time() - t0
        r = dt / max(1.0, len(audio) / SR)
        self.rtf = r if self.rtf is None else 0.7 * self.rtf + 0.3 * r
        return out


# ---------------------------------------------------------------------------
# interval helpers for "which seconds are transcribed"
# ---------------------------------------------------------------------------

def add_interval(iv: List[List[float]], a: float, b: float) -> List[List[float]]:
    out = sorted(iv + [[a, b]])
    merged: List[List[float]] = []
    for s, e in out:
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def missing(iv: List[List[float]], a: float, b: float) -> List[List[float]]:
    gaps, cur = [], a
    for s, e in sorted(iv):
        if e <= cur:
            continue
        if s >= b:
            break
        if s > cur:
            gaps.append([cur, min(s, b)])
        cur = max(cur, e)
    if cur < b:
        gaps.append([cur, b])
    return [g for g in gaps if g[1] - g[0] >= 0.5]


# ---------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------

class LiveSession:
    def __init__(self, source: str, opts: LiveOptions):
        self.source, self.o = source, opts
        self.dir = os.path.abspath(opts.out_dir)
        self.seg_dir = os.path.join(self.dir, "pieces")
        os.makedirs(self.seg_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.pieces: List[dict] = []        # {file, src, src_off, tl, dur, part}
        self.seen_files = set()
        self.tl_end = 0.0                   # stream seconds decoded so far
        self.loud: List[float] = []
        self.onset: List[float] = []
        self.audio = np.zeros(0, dtype=np.float32)   # rolling audio buffer
        self.audio_t0 = 0.0                 # stream time of audio[0]
        self._carry = np.zeros(0, dtype=np.float32)  # < 1 s left over between pieces
        self.words: List[Word] = []
        self.done_iv: List[List[float]] = []
        self.pending: Optional[dict] = None
        self.scanned_to = 0.0               # stream time the spike scan has reached
        self.emitted: List[dict] = []
        self.dropped: List[dict] = []
        self.events: List[dict] = []
        self.part = -1
        self.status = "starting"
        self.started = time.time()
        self.recorder: Optional[Recorder] = None
        self.chat = None
        self.transcriber = Transcriber(opts.model, opts.language, opts.threads)
        self.mode = "all" if opts.transcribe == "all" else ("gated" if opts.transcribe == "gated" else "auto")
        self.stop_ev = threading.Event()
        self._load_state()

    # ---- persistence -----------------------------------------------------
    @property
    def state_path(self) -> str:
        return os.path.join(self.dir, "state.json")

    def _load_state(self):
        st = _load_json(self.state_path)
        if not st:
            return
        self.pieces = st.get("pieces", [])
        self.seen_files = {p["file"] for p in self.pieces if p.get("file")}
        self.tl_end = st.get("tl_end", 0.0)
        self.scanned_to = self.tl_end
        self.loud, self.onset = st.get("loud", []), st.get("onset", [])
        self.done_iv = st.get("done_iv", [])
        self.part = st.get("part", -1)
        self.emitted = st.get("emitted", [])
        self.dropped = st.get("dropped", [])
        wj = _load_json(os.path.join(self.dir, "words.json")) or {}
        self.words = [Word(**w) for w in wj.get("words", [])]
        self.audio_t0 = self.tl_end
        self.event("resumed", detail=f"continuing at {report.fmt_hms(self.tl_end)} with "
                                     f"{len(self.emitted)} stories already found")

    def save_state(self):
        with self.lock:
            st = {"source": self.source, "options": self.o.public(), "pieces": self.pieces, "tl_end": self.tl_end,
                  "loud": [round(x, 2) for x in self.loud], "onset": [round(x, 5) for x in self.onset],
                  "done_iv": self.done_iv, "part": self.part, "emitted": self.emitted, "dropped": self.dropped[-50:],
                  "status": self.status, "updated": time.time()}
            _write_json(self.state_path, st)
            _write_json(os.path.join(self.dir, "words.json"), {"offset": 0.0, "words": [w.to_dict() for w in self.words]})

    def next_part(self) -> int:
        with self.lock:
            self.part += 1
            return self.part

    def event(self, kind: str, **kw):
        ev = {"t": round(time.time(), 2), "kind": kind, "stream_time": round(self.tl_end, 1), **kw}
        self.events.append(ev)
        del self.events[:-300]
        with open(os.path.join(self.dir, "live.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if self.o.log:
            msg = kw.get("detail") or kw.get("title") or ""
            extra = f" #{kw['rank']} {kw.get('score')}/10" if kind == "story" else ""
            print(f"[storycut live {report.fmt_hms(self.tl_end)}] {kind}{extra} {msg}".rstrip(), flush=True)
        if self.o.on_event:
            try:
                self.o.on_event(ev)
            except Exception:
                pass

    # ---- ingest -----------------------------------------------------------
    def _listed_pieces(self) -> List[tuple]:
        """Pieces ffmpeg has finished (it lists them in pNNN.csv only when closed)."""
        out = []
        for name in sorted(os.listdir(self.seg_dir)):
            if not name.endswith(".csv"):
                continue
            part = int(name[1:4])
            with open(os.path.join(self.seg_dir, name), newline="", encoding="utf-8") as f:
                for row in csv.reader(f):
                    if len(row) >= 3:
                        try:
                            out.append((part, row[0], float(row[1]), float(row[2])))
                        except ValueError:
                            continue
        return out

    def ingest(self) -> int:
        """Decode new pieces. Returns how many seconds were added."""
        added = 0.0
        if self.o.follow:
            added = self._ingest_follow()
        else:
            for part, name, a, b in self._listed_pieces():
                if name in self.seen_files:
                    continue
                path = os.path.join(self.seg_dir, name)
                if not os.path.exists(path):
                    self.seen_files.add(name)
                    continue
                try:
                    audio = decode_audio(path)
                except RuntimeError as e:
                    if "does not contain any stream" not in str(e):   # empty last piece on exit: harmless
                        self.event("error", detail=f"could not decode {name}: {e}")
                    self.seen_files.add(name)
                    continue
                dur = len(audio) / SR if len(audio) else max(0.0, b - a)
                self._append(audio, {"file": name, "src": path, "src_off": 0.0, "part": part, "dur": round(dur, 3)})
                self.seen_files.add(name)
                added += dur
        if added:
            self._prune()
        return added

    def _ingest_follow(self) -> float:
        d = probe_duration(self.source)
        if d is None:
            return 0.0
        added = 0.0
        # stay a few seconds behind the writer so we never read a half-written frame
        while d - self.tl_end >= self.o.segment + 2:
            a = self.tl_end
            audio = decode_audio(self.source, start=a, duration=self.o.segment)
            if len(audio) == 0:
                break
            dur = len(audio) / SR
            self._append(audio, {"file": None, "src": os.path.abspath(self.source), "src_off": round(a, 3),
                                 "part": 0, "dur": round(dur, 3)})
            added += dur
        return added

    def _append(self, audio: np.ndarray, piece: dict):
        with self.lock:
            piece["tl"] = round(self.tl_end, 3)
            self.pieces.append(piece)
            if len(self.audio) == 0:
                self.audio_t0 = self.tl_end
            self.audio = np.concatenate([self.audio, audio])
            whole = np.concatenate([self._carry, audio])
            n = len(whole) // SR
            if n:
                sec = whole[: n * SR]
                self.loud += [float(x) for x in signals.rms_per_second(sec, SR)]
                self.onset += [float(x) for x in signals.onset_per_second(sec, SR)]
            self._carry = whole[n * SR:]
            self.tl_end += len(audio) / SR

    def _prune(self):
        """Keep ~context + setup minutes of audio in RAM and keep_min of pieces on disk."""
        keep_s = max(self.o.context_min * 60, self.o.max_len + self.o.payoff_max + 60)
        with self.lock:
            drop = int(max(0.0, (self.tl_end - self.audio_t0) - keep_s) * SR)
            if drop > 0:
                self.audio = self.audio[drop:]
                self.audio_t0 += drop / SR
        if self.o.keep_min > 0 and not self.o.follow:
            limit = self.tl_end - self.o.keep_min * 60
            protect = [(c["start"], c["end"]) for c in self.emitted if not c.get("files", {}).get("clip")] \
                if self.o.cut else []
            for p in self.pieces:
                if p.get("file") and p["tl"] + p["dur"] < limit and not p.get("deleted") \
                        and not any(p["tl"] < e and p["tl"] + p["dur"] > s for s, e in protect):
                    try:
                        os.remove(p["src"])
                    except OSError:
                        pass
                    p["deleted"] = True

    def audio_slice(self, a: float, b: float) -> np.ndarray:
        with self.lock:
            ia = int(max(0.0, a - self.audio_t0) * SR)
            ib = int(max(0.0, b - self.audio_t0) * SR)
            return self.audio[ia:ib].copy()

    # ---- transcription ----------------------------------------------------
    def ensure_transcript(self, a: float, b: float, max_chunk: float = 30.0):
        a = max(a, self.audio_t0)
        b = min(b, self.tl_end)
        for ga, gb in missing(self.done_iv, a, b):
            t = ga
            while t < gb - 0.5:
                e = min(gb, t + max_chunk)
                if e < gb:
                    e = self._quiet_cut(t + max_chunk * 0.6, e)
                words = self.transcriber(self.audio_slice(t, e), t)
                with self.lock:
                    fresh = [w for w in words if t - 0.05 <= w.start < e]
                    self.words = sorted(self.words + fresh, key=lambda w: w.start)
                    self.done_iv = add_interval(self.done_iv, t, e)
                t = e

    def _quiet_cut(self, lo: float, hi: float) -> float:
        """Cut at the quietest second between lo and hi so no word is split."""
        a, b = int(lo), int(hi)
        if b - a < 1 or b > len(self.loud):
            return hi
        seg = self.loud[a:b]
        return float(a + int(np.argmin(seg)) + 0.5)

    def _decide_mode(self) -> str:
        if self.mode != "auto":
            return self.mode
        r = self.transcriber.rtf
        if r is None:
            return "all"          # measure on the first chunks
        # replays run faster than real time, so whisper has proportionally less time
        load = r * max(1.0, self.o.speed if not is_url(self.source) and not self.o.follow else 1.0)
        return "all" if load < 0.7 else "gated"

    # ---- detection --------------------------------------------------------
    def curves(self):
        n = int(self.tl_end)
        lo = max(0, n - int(self.o.context_min * 60))
        sub = lambda arr: np.asarray(arr[lo:n], dtype=float)
        words = [w for w in self.words if w.start >= lo]
        rel = [Word(w.start - lo, w.end - lo, w.text, w.prob) for w in words]
        cues_rel = signals.cue_events(rel)
        curves = {"loud": sub(self.loud), "onset": sub(self.onset),
                  "cues": signals.events_to_curve(cues_rel, n - lo),
                  "speech": signals.speech_curve(rel, n - lo)}
        if self.chat is not None and getattr(self.chat, "counts", None):
            c = np.zeros(n - lo)
            for t, k in list(self.chat.counts.items()):
                if lo <= t < n:
                    c[t - lo] += k
            for t in list(self.chat.clip_calls):
                if lo <= int(t) < n:
                    c[int(t) - lo] += 8      # chat literally asking for a clip
            if c.sum() > 0:
                curves["chat"] = c
        combined, parts = signals.combine(curves)
        return lo, combined, parts

    def tick(self):
        if self.tl_end < 90:          # warm-up: need a baseline of "normal" first
            return
        mode = self._decide_mode()
        now = self.tl_end
        if mode == "all":
            self.ensure_transcript(max(self.audio_t0, now - self.o.context_min * 60), now - 1.0)
        lo, comb, parts = self.curves()
        if len(comb) < 20:
            return
        newest = len(comb) - 3          # ignore the last seconds (smoothing edge)
        last_peak = max((c["peak"] for c in self.emitted), default=-1e9)
        if self.pending is None:
            look_from = max(0, int(self.scanned_to - lo) - 3)
            seg = comb[look_from:newest]
            if mode == "gated" and len(seg) and float(seg.max()) >= self.o.soft_trigger:
                # something is building: transcribe it so speech cues can confirm (or not) the moment
                i = int(np.argmax(seg)) + look_from
                if missing(self.done_iv, lo + i - 25, now - 1.0):
                    self.ensure_transcript(lo + i - 25, now - 1.0)
                    lo, comb, parts = self.curves()
                    newest = len(comb) - 3
                    look_from = max(0, int(self.scanned_to - lo) - 3)
                    seg = comb[look_from:newest]
            self.scanned_to = float(lo + newest)
            if len(seg):
                i = int(np.argmax(seg)) + look_from
                t = lo + i
                if comb[i] >= self.o.trigger and t - last_peak >= self.o.min_gap:
                    self.pending = {"peak": float(t), "value": float(comb[i]), "opened": time.time(),
                                    "opened_at": now}
                    self.event("spike", detail=f"peak {comb[i]:.2f} at {report.fmt_hms(t)}, waiting for the payoff")
                    if mode == "gated":   # get the setup words while we wait
                        self.ensure_transcript(t - self.o.target * 0.6 - 20, now)
            return
        p = self.pending
        pi = int(p["peak"] - lo)
        # a stronger peak a few seconds later is the real peak of the same story
        later = comb[max(0, pi):newest]
        if len(later):
            j = int(np.argmax(later)) + max(0, pi)
            if comb[j] > p["value"] * 1.1 and (lo + j) - p["peak"] <= 20:
                p["peak"], p["value"] = float(lo + j), float(comb[j])
                pi = j
        since = now - p["peak"]
        tail = comb[pi + 1:newest]
        quiet = 0
        for v in tail[::-1]:
            if v < self.o.close_level:
                quiet += 1
            else:
                break
        closed = since >= self.o.payoff_min and (quiet >= self.o.close_quiet or since >= self.o.payoff_max)
        if closed:
            reason = "excitement back to normal" if quiet >= self.o.close_quiet else "payoff time cap"
            self._finish(p, lo, comb, parts, reason)
            self.pending = None
            self.scanned_to = float(lo + newest)

    def _finish(self, p: dict, lo: int, comb: np.ndarray, parts: dict, why: str):
        now = self.tl_end
        setup_from = p["peak"] - self.o.max_len * 0.6 - 10
        self.ensure_transcript(setup_from, now)
        words = [w for w in self.words if w.start >= lo]
        rel_words = [Word(w.start - lo, w.end - lo, w.text, w.prob) for w in words]
        lo2, comb2, parts2 = self.curves()
        if lo2 != lo:
            lo, comb, parts = lo2, comb2, parts2
            rel_words = [Word(w.start - lo, w.end - lo, w.text, w.prob) for w in self.words if w.start >= lo]
        cues = signals.cue_events(rel_words)
        pk_rel = p["peak"] - lo
        idx = int(min(len(comb) - 1, max(0, pk_rel)))
        src = {k: round(float(v[idx]), 2) for k, v in parts.items() if idx < len(v)}
        peak = Peak(t=pk_rel, value=round(float(comb[idx]), 3), sources=src)
        sents = story.sentences_from_words(rel_words)
        w = story.expand_window(peak, sents, target=self.o.target, max_len=self.o.max_len, min_len=self.o.min_len,
                                media_end=float(now - lo))
        w.start = max(w.start, self.audio_t0 - lo)
        # never overlap a story that was already posted: start after it (at a sentence start), or merge into it
        prev_end = max([c["end"] for c in self.emitted], default=-1e9) - lo
        if w.start < prev_end:
            if pk_rel <= prev_end + 2:
                self.event("merged", detail=f"spike at {report.fmt_hms(p['peak'])} belongs to the story already posted")
                self.save_state()
                return
            starts = [x.start for x in sents if prev_end <= x.start < pk_rel]
            w.start = starts[0] if starts else prev_end
            if w.end - w.start < self.o.min_len:
                w.end = min(float(now - lo), w.start + self.o.min_len)
        w.text = story.window_text(w, rel_words)
        w.keep = story.trim_dead_air(w, rel_words)
        score_window(w, comb, parts, rel_words, cues)
        t = story_title(w, rel_words, cues, offset=lo)
        ph = story_phases(w, comb)
        item = report.to_json([w], {}, offset=lo)["candidates"][0]
        item.update(t)
        item["phases"] = {k: [round(a + lo, 2), round(b + lo, 2)] for k, (a, b) in ph.items()}
        item["closed_because"] = why
        item["found_at_stream_time"] = round(now, 1)
        item["latency_after_payoff"] = round(max(0.0, now - item["end"]), 1)
        item["found_at"] = time.time()
        item["files"] = {}
        if w.score < self.o.min_score:
            item["rank"] = None
            self.dropped.append(item)
            self.event("dropped", detail=f"{item['title']} scored {w.score}/10 (< {self.o.min_score})")
            self.save_state()
            return
        item["rank"] = len(self.emitted) + 1
        self.emitted.append(item)
        self.write_outputs()
        self.event("story", rank=item["rank"], score=item["score"], title=item["title"],
                   detail=f"{item['start_hms']} -> {item['end_hms']} ({item['duration']:.0f}s) {item['title']}")
        if self.o.cut or self.o.render:
            try:
                self.cut(item["rank"], render=self.o.render)
            except Exception as e:
                self.event("error", detail=f"cut #{item['rank']} failed: {e}")
        self.save_state()

    # ---- outputs ----------------------------------------------------------
    def write_outputs(self):
        meta = {"input_name": self.source, "offset": 0.0, "analysed_seconds": int(self.tl_end),
                "signals": ["loudness", "onsets", "transcript cues", "speech density"] +
                           (["live chat"] if self.chat is not None and getattr(self.chat, "ok", False) else []),
                "model": self.o.model, "target": self.o.target, "max_len": self.o.max_len, "tool": "storycut live",
                "live": True, "status": self.status}
        ranked = sorted(self.emitted, key=lambda c: -c["score"])
        data = {"meta": meta, "candidates": self.emitted, "by_score": [c["rank"] for c in ranked]}
        _write_json(os.path.join(self.dir, "candidates.json"), data)
        with open(os.path.join(self.dir, "report.md"), "w", encoding="utf-8") as f:
            f.write(report.to_markdown({"meta": meta, "candidates": self.emitted}))
        n = int(self.tl_end)
        lo = 0
        if n > 2:
            from .pipeline import curve_payload
            lo2, comb, parts = self.curves()
            _write_json(os.path.join(self.dir, "curve.json"), curve_payload(comb, parts, float(lo2)))

    def cut(self, rank: int, render: bool = False) -> dict:
        c = next(x for x in self.emitted if x["rank"] == rank)
        a, b = c["start"], c["end"]
        out = os.path.join(self.dir, f"clip{rank:02d}.mp4")
        cover = [p for p in self.pieces if p["tl"] < b and p["tl"] + p["dur"] > a and not p.get("deleted")]
        if not cover:
            raise RuntimeError("the recorded pieces for this story were already deleted")
        srcs = {p["src"] for p in cover}
        if len(srcs) == 1 and self.o.follow:
            p0 = cover[0]
            s = p0["src_off"] + (a - p0["tl"])
            _cut(p0["src"], out, s, s + (b - a))
        else:
            lst = os.path.join(self.dir, f".concat{rank:02d}.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for p in cover:
                    f.write("file '" + p["src"].replace("'", "'\\''") + "'\n")
            joined = os.path.join(self.dir, f".joined{rank:02d}.ts")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                            joined], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _cut(joined, out, a - cover[0]["tl"], b - cover[0]["tl"])
            for x in (lst, joined):
                try:
                    os.remove(x)
                except OSError:
                    pass
        c["files"]["clip"] = os.path.basename(out)
        w = Window(start=a, end=b, peak=Peak(t=c["peak"], value=0))
        with open(os.path.join(self.dir, f"clip{rank:02d}.srt"), "w", encoding="utf-8") as f:
            f.write(report.window_srt(w, self.words))
        c["files"]["srt"] = f"clip{rank:02d}.srt"
        if render:
            from .render import render as do_render
            rel = [Word(x.start - a, x.end - a, x.text, x.prob) for x in self.words if a <= x.start <= b]
            short = os.path.join(self.dir, f"short_rank{rank}.mp4")
            do_render(out, 0.0, b - a, rel, short, facecam=self.o.facecam, gameplay=self.o.gameplay,
                      hook=self.o.hook, captions=getattr(self.o, "captions", "clean") or "clean")
            c["files"]["short"] = os.path.basename(short)
        self.write_outputs()
        self.event("cut", rank=rank, detail=os.path.basename(out))
        return c

    def snapshot(self) -> dict:
        return {"status": self.status, "source": self.source, "stream_seconds": round(self.tl_end, 1),
                "stream_hms": report.fmt_hms(self.tl_end), "mode": self._decide_mode(),
                "whisper_speed": None if self.transcriber.rtf is None else round(1 / max(self.transcriber.rtf, 1e-3), 1),
                "pending": self.pending, "stories": len(self.emitted), "dropped": len(self.dropped),
                "parts": self.part + 1, "chat": bool(self.chat is not None and getattr(self.chat, "ok", False)),
                "events": self.events[-30:]}

    # ---- main loop --------------------------------------------------------
    def run(self) -> dict:
        self.status = "recording"
        if is_url(self.source) and self.o.chat == "auto" and twitch_channel(self.source):
            self.chat = TwitchChat(twitch_channel(self.source), lambda: self.tl_end)
            self.chat.start()
        elif self.o.chat not in ("auto", "off", "", None):
            self.chat = FileChat(self.o.chat)
        if not self.o.follow:
            self.recorder = Recorder(self, self.source, self.o,
                                     start_offset=self.tl_end if not is_url(self.source) else 0.0)
            self.recorder.start()
        self.event("started", detail=f"{self.source} -> {self.dir} (whisper {self.o.model}, mode {self.mode})")
        idle = 0.0
        last_save = time.time()
        try:
            while not self.stop_ev.is_set():
                if isinstance(self.chat, FileChat):
                    self.chat.poll()
                added = self.ingest()
                if added:
                    idle = 0.0
                    self.tick()
                else:
                    idle += self.o.step
                if time.time() - last_save > 30:
                    self.save_state()
                    self.write_outputs()
                    last_save = time.time()
                if self.o.max_minutes and self.tl_end >= self.o.max_minutes * 60:
                    self.event("limit", detail="max minutes reached")
                    break
                rec_done = self.recorder is not None and self.recorder.finished
                if rec_done and not added:
                    # pick up the last pieces ffmpeg closed on exit
                    if not self.ingest():
                        break
                if self.o.follow and idle >= max(120.0, self.o.segment * 10):
                    self.event("ended", detail="file stopped growing")
                    break
                if not added:
                    self.stop_ev.wait(self.o.step)
        except KeyboardInterrupt:
            self.event("stopped", detail="Ctrl+C")
        finally:
            self.shutdown()
        return {"stories": self.emitted, "dropped": self.dropped, "dir": self.dir}

    def shutdown(self):
        if self.recorder:
            self.recorder.stop()
        if self.chat is not None and hasattr(self.chat, "stop_ev"):
            self.chat.stop_ev.set()
        # close a story that was still waiting for its payoff
        if self.pending is not None and self.tl_end - self.pending["peak"] >= 3:
            lo, comb, parts = self.curves()
            if len(comb) > 5:
                self._finish(self.pending, lo, comb, parts, "stream ended")
            self.pending = None
        self.status = "ended" if not (self.recorder and self.recorder.error) else "error"
        if self.recorder and self.recorder.error:
            self.event("error", detail=self.recorder.error)
        self.write_outputs()
        self.save_state()
        self.event("finished", detail=f"{len(self.emitted)} stories, {len(self.dropped)} dropped")

    def stop(self):
        self.stop_ev.set()


def _cut(src: str, out: str, a: float, b: float):
    """Accurate cut; video is re-encoded fast so a clip is ready seconds after the payoff."""
    a = max(0.0, a)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, a - 5):.3f}", "-i", src, "-ss",
           f"{a - max(0.0, a - 5):.3f}", "-t", f"{b - a:.3f}", "-map", "0:v:0?", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run_live(source: str, opts: LiveOptions) -> dict:
    s = LiveSession(source, opts)
    return s.run()
