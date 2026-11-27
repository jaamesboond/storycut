"""Tiny local web dashboard for storycut (stdlib only: http.server + threads).

    storycut ui [--jobs DIR] [--host 127.0.0.1] [--port 8770] [--demo]

Jobs are folders under the jobs dir. Any folder that already holds a
`candidates.json` (from `storycut find`) shows up too. Heavy work (find, cut,
render) runs one at a time in a background worker so a small machine stays
responsive.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import shutil
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import unquote, urlparse

from .. import __version__
from . import core

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo")


def lan_addresses() -> list:
    """Best-effort list of this machine's LAN IPv4 addresses (for 'open on your phone')."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent for UDP connect
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


class LiveManager:
    """Live sessions: folders under <jobs>/live/<id>. Each running session is a thread.
    Sessions that were still running when the server stopped are resumed on start."""

    def __init__(self, root: str):
        self.root = os.path.join(root, "live")
        os.makedirs(self.root, exist_ok=True)
        self.running = {}            # id -> (LiveSession, Thread)
        self.heavy = threading.Lock()  # one cut/render at a time

    def _dir(self, sid: str) -> Optional[str]:
        sid = core.safe_job_id(sid)
        d = os.path.join(self.root, sid) if sid else None
        return d if d and os.path.isdir(d) else None

    def _opts(self, d: str, o: dict):
        from ..live import LiveOptions
        return LiveOptions(out_dir=d, model=o.get("model") or "base", language=o.get("language") or None,
                           transcribe=o.get("transcribe") or "auto", min_score=float(o.get("min_score") or 5.5),
                           follow=bool(o.get("follow")), cut=bool(o.get("cut")) or bool(o.get("render")),
                           render=bool(o.get("render")), facecam=o.get("facecam") or None,
                           gameplay=o.get("gameplay") or None, hook=o.get("hook") or None,
                           captions=o.get("captions") or "clean", log=False,
                           speed=max(1.0, min(8.0, float(o.get("speed") or 1.0))))

    def start(self, body: dict, settings: dict) -> tuple:
        src = str(body.get("source") or "").strip()
        if not src:
            return None, "give a stream URL or a file path"
        from ..live import is_url
        if not is_url(src) and not os.path.isfile(os.path.expanduser(src)):
            return None, "not a URL and no such file on this machine"
        if not is_url(src):
            src = os.path.abspath(os.path.expanduser(src))
        sid = "live-" + core.new_job_id(src)
        while os.path.exists(os.path.join(self.root, sid)):
            sid += "x"
        d = os.path.join(self.root, sid)
        os.makedirs(d)
        o = {k: body.get(k) for k in ("model", "language", "transcribe", "min_score", "follow", "cut", "render", "speed")}
        if o.get("render"):
            o.update(facecam=settings.get("facecam"), gameplay=settings.get("gameplay"), hook=settings.get("hook"),
                     captions=settings.get("captions") or "clean")
        meta = {"id": sid, "source": src, "options": o, "created": time.time(), "stopped": False}
        core_write(os.path.join(d, "session.json"), meta)
        self._launch(sid, d, meta)
        return sid, None

    def _launch(self, sid: str, d: str, meta: dict):
        from ..live import LiveSession
        sess = LiveSession(meta["source"], self._opts(d, meta.get("options") or {}))
        t = threading.Thread(target=sess.run, daemon=True)
        self.running[sid] = (sess, t)
        t.start()

    def resume_all(self):
        for sid in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, sid)
            meta = core.load_json(os.path.join(d, "session.json"))
            st = core.load_json(os.path.join(d, "state.json")) or {}
            if meta and not meta.get("stopped") and st.get("status") not in ("ended", "error"):
                try:
                    self._launch(sid, d, meta)
                except Exception:
                    traceback.print_exc()

    def is_running(self, sid: str) -> bool:
        r = self.running.get(sid)
        return bool(r and r[1].is_alive())

    def info(self, sid: str, mask: bool = False) -> Optional[dict]:
        d = self._dir(sid)
        if not d:
            return None
        meta = core.load_json(os.path.join(d, "session.json")) or {}
        cands = (core.load_json(os.path.join(d, "candidates.json")) or {}).get("candidates", [])
        run = self.is_running(sid)
        if run:
            state = self.running[sid][0].snapshot()
        else:
            st = core.load_json(os.path.join(d, "state.json")) or {}
            from ..report import fmt_hms
            state = {"status": st.get("status", "ended") if not meta.get("stopped") else "stopped",
                     "stream_seconds": st.get("tl_end", 0), "stream_hms": fmt_hms(st.get("tl_end", 0)),
                     "stories": len(cands), "dropped": len(st.get("dropped", [])),
                     "mode": (st.get("options") or {}).get("transcribe"), "events": _tail_events(d)}
        for c in cands:
            c["timeline"] = core.timeline(c)
            c["heat"] = core.heat(c["score"])
            c["files"] = core.clip_files(d, c["rank"])
            if mask:
                for k in ("title", "transcript", "setup_line", "shout"):
                    if c.get(k):
                        c[k] = core.mask_text(c[k])
        if mask:
            state = dict(state, events=[dict(e, detail=core.mask_text(e.get("detail", "")),
                                             title=core.mask_text(e.get("title", ""))) for e in state.get("events", [])])
        return {"id": sid, "source": meta.get("source"), "running": run, "status": state.get("status"),
                "state": state, "candidates": cands, "created": meta.get("created")}

    def list(self) -> list:
        out = []
        for sid in os.listdir(self.root):
            i = self.info(sid)
            if i:
                out.append({"id": sid, "source": i["source"], "running": i["running"], "status": i["status"],
                            "stories": len(i["candidates"]), "stream_hms": i["state"].get("stream_hms"),
                            "best": max((c["score"] for c in i["candidates"]), default=None),
                            "created": i["created"]})
        out.sort(key=lambda x: -(x.get("created") or 0))
        return out

    def stop(self, sid: str) -> Optional[str]:
        d = self._dir(sid)
        if not d:
            return "session not found"
        meta = core.load_json(os.path.join(d, "session.json")) or {}
        meta["stopped"] = True
        core_write(os.path.join(d, "session.json"), meta)
        r = self.running.get(sid)
        if r:
            r[0].stop()
        return None

    def action(self, sid: str, kind: str, rank: int) -> Optional[str]:
        d = self._dir(sid)
        if not d:
            return "session not found"
        cands = (core.load_json(os.path.join(d, "candidates.json")) or {}).get("candidates", [])
        if not any(c["rank"] == rank for c in cands):
            return f"story {rank} not in this session"

        def job():
            with self.heavy:
                try:
                    if self.is_running(sid):
                        sess = self.running[sid][0]
                    else:
                        from ..live import LiveSession
                        meta = core.load_json(os.path.join(d, "session.json")) or {}
                        sess = LiveSession(meta.get("source", ""), self._opts(d, meta.get("options") or {}))
                    if kind == "render":
                        s = core.load_json(os.path.join(os.path.dirname(self.root), "settings.json"), {}) or {}
                        sess.o.facecam = s.get("facecam") or sess.o.facecam
                        sess.o.gameplay = s.get("gameplay") or sess.o.gameplay
                        sess.o.hook = s.get("hook") or sess.o.hook
                        sess.o.captions = s.get("captions") or sess.o.captions
                    sess.cut(rank, render=(kind == "render"))
                    sess.save_state()
                except Exception:
                    traceback.print_exc()
        threading.Thread(target=job, daemon=True).start()
        return None


def core_write(path: str, data) -> None:
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def _tail_events(d: str, n: int = 30) -> list:
    try:
        with open(os.path.join(d, "live.jsonl"), "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        return []


class Store:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(self.root, exist_ok=True)
        self.lock = threading.Lock()
        self.q: "queue.Queue" = queue.Queue()
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()
        self.live = LiveManager(self.root)

    # ---- settings
    @property
    def settings_path(self) -> str:
        return os.path.join(self.root, "settings.json")

    def settings(self) -> dict:
        return core.merge_settings(core.load_json(self.settings_path, {}) or {}, {})

    def save_settings(self, patch: dict) -> dict:
        s = core.merge_settings(self.settings(), patch)
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        return s

    # ---- jobs
    def job_dir(self, jid: str) -> Optional[str]:
        jid = core.safe_job_id(jid)
        if not jid:
            return None
        d = os.path.join(self.root, jid)
        return d if os.path.isdir(d) else None

    def meta(self, jid: str) -> Optional[dict]:
        d = self.job_dir(jid)
        if not d:
            return None
        m = core.load_json(os.path.join(d, "job.json"))
        cands = core.load_json(os.path.join(d, "candidates.json"))
        if m is None:
            if cands is None:
                return None
            m = {"id": jid, "status": "done", "imported": True,
                 "input": (cands.get("meta") or {}).get("input_name", jid),
                 "created": os.path.getmtime(os.path.join(d, "candidates.json"))}
        m["id"] = jid
        if cands:
            m["best"] = max((c["score"] for c in cands.get("candidates", [])), default=None)
            m["count"] = len(cands.get("candidates", []))
        return m

    def _write_meta(self, jid: str, **kw) -> dict:
        d = os.path.join(self.root, jid)
        with self.lock:
            m = core.load_json(os.path.join(d, "job.json"), {}) or {}
            log = kw.pop("log_line", None)
            m.update(kw)
            if log:
                m.setdefault("log", []).append(f"{time.strftime('%H:%M:%S')}  {log}")
                m["log"] = m["log"][-200:]
            with open(os.path.join(d, "job.json.tmp"), "w", encoding="utf-8") as f:
                json.dump(m, f, indent=2)
            os.replace(os.path.join(d, "job.json.tmp"), os.path.join(d, "job.json"))
        return m

    def list(self) -> list:
        out = []
        for name in os.listdir(self.root):
            if name != "live" and core.safe_job_id(name) and os.path.isdir(os.path.join(self.root, name)):
                m = self.meta(name)
                if m:
                    out.append(m)
        out.sort(key=lambda m: -(m.get("created") or 0))
        return out

    def create(self, opts: dict) -> dict:
        jid = core.new_job_id(opts["input"])
        while os.path.exists(os.path.join(self.root, jid)):
            jid += "x"
        os.makedirs(os.path.join(self.root, jid))
        self._write_meta(jid, id=jid, input=opts["input"], options=opts, status="queued", stage="queued",
                         progress=0.0, created=time.time(), log_line="queued")
        self.q.put(("find", jid, None))
        return self.meta(jid)

    def action(self, jid: str, kind: str, rank: int) -> Optional[str]:
        m = self.meta(jid)
        if not m:
            return "job not found"
        src = (m.get("options") or {}).get("input") or m.get("source")
        local = (m.get("options") or {}).get("local_media") or src
        if kind != "srt" and (not local or not os.path.isfile(str(local))):
            return "the source media for this job is not on this machine, so it cannot be cut or rendered"
        cands = core.load_json(os.path.join(self.job_dir(jid), "candidates.json"), {}) or {}
        if not any(c["rank"] == rank for c in cands.get("candidates", [])):
            return f"rank {rank} not in this job"
        self._write_meta(jid, busy=f"{kind} #{rank}", log_line=f"{kind} #{rank} queued")
        self.q.put((kind, jid, rank))
        return None

    # ---- background worker (one heavy task at a time)
    def _work(self):
        while True:
            kind, jid, rank = self.q.get()
            try:
                if kind == "find":
                    self._run_find(jid)
                else:
                    self._run_action(jid, kind, rank)
            except Exception as e:  # keep the worker alive
                self._write_meta(jid, status="error" if kind == "find" else None, error=str(e), busy=None,
                                 log_line=f"ERROR {kind}: {e}")
                traceback.print_exc()
            finally:
                self.q.task_done()

    def _run_find(self, jid: str):
        from ..pipeline import FindOptions, find
        d = os.path.join(self.root, jid)
        o = core.load_json(os.path.join(d, "job.json"))["options"]
        self._write_meta(jid, status="running", stage="starting", progress=0.01, log_line="started")

        def prog(msg, frac):
            kw = {"stage": msg, "log_line": msg}
            if frac is not None and frac >= 0:
                kw["progress"] = round(frac, 3)
            self._write_meta(jid, **kw)

        fo = FindOptions(out_dir=d, start=o["start"], duration=o["duration"], model=o["model"],
                         language=o["language"], chat=o["chat"], top=o["top"], target=o["target"],
                         max_len=o["max_len"], min_gap=o["min_gap"], scenes=o["scenes"], trim=o["trim"],
                         cut=o["cut"], log=False, progress=prog)
        find(o["input"], fo)
        local = o["input"]
        if o.get("is_url"):
            dl = os.path.join(d, "download")
            vids = [os.path.join(dl, f) for f in os.listdir(dl)] if os.path.isdir(dl) else []
            vids = [v for v in vids if not v.endswith(".json")]
            local = vids[0] if vids else None
        o["local_media"] = local
        self._write_meta(jid, options=o, status="done", stage="done", progress=1.0, finished=time.time(),
                         log_line="finished")

    def _run_action(self, jid: str, kind: str, rank: int):
        from .. import media, report
        from ..models import Peak, Window
        from ..render import load_words, render
        d = os.path.join(self.root, jid)
        m = core.load_json(os.path.join(d, "job.json"), {}) or {}
        o = m.get("options") or {}
        src = o.get("local_media") or o.get("input")
        c = next(x for x in core.load_json(os.path.join(d, "candidates.json"))["candidates"] if x["rank"] == rank)
        self._write_meta(jid, busy=f"{kind} #{rank}", log_line=f"{kind} #{rank} running")
        if kind == "cut":
            out = os.path.join(d, f"clip{rank:02d}.mp4")
            if media.has_video(src):
                media.cut_clip(src, out, c["start"], c["end"])
            else:
                media._run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ss", f"{c['start']:.3f}", "-to",
                            f"{c['end']:.3f}", "-c:a", "aac", "-b:a", "160k", out[:-4] + ".m4a"])
        elif kind == "render":
            s = self.settings()
            words, _ = load_words(os.path.join(d, "words.json"))
            render(src, c["start"], c["end"], words, os.path.join(d, f"short_rank{rank}.mp4"),
                   facecam=s["facecam"] or None, gameplay=s["gameplay"] or None, hook=s["hook"] or None,
                   end_text=s["end_text"] or None, watermark=s["watermark"] or None, crf=int(s["crf"]),
                   font=s["font"], captions=s["captions"], caption_pos=s["caption_pos"], track=bool(s["track"]),
                   loudnorm=bool(s["loudnorm"]), thumbnail=bool(s["thumb"]))
        elif kind == "srt":
            words, off = load_words(os.path.join(d, "words.json"))
            w = Window(start=c["start"], end=c["end"], peak=Peak(t=c["peak"], value=0))
            with open(os.path.join(d, f"clip{rank:02d}.srt"), "w", encoding="utf-8") as f:
                f.write(report.window_srt(w, words))
        self._write_meta(jid, busy=None, log_line=f"{kind} #{rank} done")

    def results(self, jid: str, mask: bool) -> Optional[dict]:
        d = self.job_dir(jid)
        if not d:
            return None
        data = core.load_json(os.path.join(d, "candidates.json"))
        if data is None:
            return None
        for c in data["candidates"]:
            c["timeline"] = core.timeline(c)
            c["heat"] = core.heat(c["score"])
            c["files"] = core.clip_files(d, c["rank"])
            if mask:
                c["transcript"] = core.mask_text(c.get("transcript", ""))
        data["curve"] = core.load_json(os.path.join(d, "curve.json"))
        return data


_ICONS = None


def icon_bytes(name: str) -> Optional[bytes]:
    """PNG/ICO app icons are stored base64 in static/icons.json (keeps the repo text-only)."""
    global _ICONS
    if _ICONS is None:
        _ICONS = core.load_json(os.path.join(STATIC, "icons.json"), {}) or {}
    b = _ICONS.get(name)
    return base64.b64decode(b) if b else None


def export_icons(out_dir: str) -> list:
    """Write the bundled PNG icons (512/192/128/64/32, maskable, favicon) to out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    icon_bytes("x")
    written = []
    for name in sorted(_ICONS):
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(base64.b64decode(_ICONS[name]))
        written.append(name)
    shutil.copy(os.path.join(STATIC, "logo.svg"), os.path.join(out_dir, "logo.svg"))
    return written + ["logo.svg"]


def install_demo(root: str) -> None:
    """Copy the bundled demo job into the jobs dir (once)."""
    dst = os.path.join(root, "demo-kai-finale")
    if os.path.isdir(DEMO) and not os.path.exists(dst):
        shutil.copytree(DEMO, dst)


class Handler(BaseHTTPRequestHandler):
    store: Store = None  # set by serve()
    server_version = f"storycut/{__version__}"

    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("STORYCUT_UI_DEBUG"):
            super().log_message(fmt, *args)

    # -- helpers
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return {}

    def _bytes(self, data: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, download_name=None, cache=False):
        size = os.path.getsize(path)
        ctype = core.MEDIA_TYPES.get(os.path.splitext(path)[1].lower()) or \
            mimetypes.guess_type(path)[0] or "application/octet-stream"
        rng = core.parse_range(self.headers.get("Range"), size)
        a, b = rng if rng else (0, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    # -- routes
    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        parts = [p for p in path.split("/") if p]
        st = self.store
        if path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"))
        if path in ("/manifest.webmanifest", "/sw.js"):
            return self._file(os.path.join(STATIC, path[1:]))
        if path == "/favicon.ico":
            parts = ["static", "favicon.ico"]
        if parts and parts[0] == "static" and len(parts) == 2:
            p = core.safe_file(STATIC, parts[1])
            if p:
                return self._file(p, cache=True)
            blob = icon_bytes(parts[1])
            return self._bytes(blob, "image/png" if parts[1].endswith(".png") else "image/x-icon") if blob \
                else self._err("not found", 404)
        if parts[:2] == ["api", "info"]:
            port = self.server.server_address[1]
            host = self.server.server_address[0]
            lan = [f"http://{ip}:{port}" for ip in lan_addresses()] if host in ("0.0.0.0", "") else []
            return self._json({"version": __version__, "jobs_dir": st.root, "host": host, "port": port,
                               "lan_urls": lan, "llm": bool(os.environ.get("STORYCUT_LLM")),
                               "queue": st.q.qsize()})
        if parts[:2] == ["api", "settings"]:
            return self._json(st.settings())
        if parts[:2] == ["api", "live"]:
            if len(parts) == 2:
                return self._json({"sessions": st.live.list()})
            i = st.live.info(parts[2], mask=st.settings()["mask_profanity"])
            if not i:
                return self._err("session not found", 404)
            if len(parts) == 3:
                return self._json(i)
            if parts[3] == "file" and len(parts) == 5:
                p = core.safe_file(st.live._dir(parts[2]), parts[4])
                if not p or not p.endswith((".mp4", ".m4a", ".srt", ".md", ".json")):
                    return self._err("not found", 404)
                return self._file(p, download_name=parts[4] if p.endswith((".srt", ".md", ".json")) else None)
        if parts[:2] == ["api", "jobs"]:
            if len(parts) == 2:
                return self._json({"jobs": st.list()})
            jid = parts[2]
            if len(parts) == 3:
                m = st.meta(jid)
                return self._json(m) if m else self._err("job not found", 404)
            d = st.job_dir(jid)
            if not d:
                return self._err("job not found", 404)
            if parts[3] == "results":
                r = st.results(jid, mask=st.settings()["mask_profanity"])
                return self._json(r) if r else self._err("no results yet", 404)
            if parts[3] == "file" and len(parts) == 5:
                p = core.safe_file(d, parts[4])
                if not p or p.endswith(".json") and os.path.basename(p) == "job.json":
                    return self._err("not found", 404)
                dl = parts[4] if parts[4].endswith((".srt", ".md", ".json")) else None
                return self._file(p, download_name=dl)
        return self._err("not found", 404)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        parts = [p for p in path.split("/") if p]
        st = self.store
        body = self._body()
        if parts == ["api", "settings"]:
            return self._json(st.save_settings(body))
        if parts == ["api", "jobs"]:
            opts, err = core.parse_job_request(body, st.settings())
            if err:
                return self._err(err)
            return self._json(st.create(opts), 201)
        if parts == ["api", "live"]:
            sid, err = st.live.start(body, st.settings())
            return self._err(err) if err else self._json(st.live.info(sid), 201)
        if len(parts) == 4 and parts[:2] == ["api", "live"] and parts[3] == "stop":
            err = st.live.stop(parts[2])
            return self._err(err, 404) if err else self._json({"ok": True})
        if len(parts) == 5 and parts[:2] == ["api", "live"] and parts[3] in ("cut", "render"):
            try:
                rank = int(parts[4])
            except ValueError:
                return self._err("bad rank")
            err = st.live.action(parts[2], parts[3], rank)
            return self._err(err, 409) if err else self._json({"ok": True, "queued": parts[3], "rank": rank})
        if len(parts) == 5 and parts[:2] == ["api", "jobs"] and parts[3] in ("cut", "render", "srt"):
            try:
                rank = int(parts[4])
            except ValueError:
                return self._err("bad rank")
            err = st.action(parts[2], parts[3], rank)
            return self._err(err, 409) if err else self._json({"ok": True, "queued": parts[3], "rank": rank})
        return self._err("not found", 404)


def serve(jobs: str = "storycut_jobs", host: str = "127.0.0.1", port: int = 8770, demo: bool = True) -> None:
    store = Store(jobs)
    if demo:
        install_demo(store.root)
    Handler.store = store
    store.live.resume_all()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"storycut dashboard on http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
    if host == "0.0.0.0":
        for ip in lan_addresses():
            print(f"  on your phone (same Wi-Fi): http://{ip}:{port}")
    print(f"  jobs folder: {store.root}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
