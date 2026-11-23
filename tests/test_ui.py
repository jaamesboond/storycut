import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from storycut.ui import core


class TestUiCore(unittest.TestCase):
    def test_job_ids(self):
        self.assertEqual(core.safe_job_id("abc-12_x"), "abc-12_x")
        for bad in ("../etc", "a/b", "", ".hidden", "x" * 80, None):
            self.assertIsNone(core.safe_job_id(bad))
        jid = core.new_job_id("https://www.youtube.com/watch?v=AbC_123", now=0)
        self.assertTrue(core.safe_job_id(jid), jid)
        self.assertTrue(core.new_job_id("/videos/My Stream (Final).mp4", now=0).startswith("my-stream-final-"))

    def test_parse_job_request(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            opts, err = core.parse_job_request({"input": f.name, "start": "1:00", "duration": "25:00", "top": 99,
                                                "target": 5, "model": "small"})
            self.assertIsNone(err)
            self.assertEqual(opts["start"], 60.0)
            self.assertEqual(opts["duration"], 1500.0)
            self.assertEqual(opts["top"], 20)       # clamped
            self.assertEqual(opts["target"], 15)    # clamped
            self.assertGreaterEqual(opts["max_len"], opts["target"])
            _, err = core.parse_job_request({"input": f.name, "model": "huge"})
            self.assertIn("unknown model", err)
            _, err = core.parse_job_request({"input": f.name, "start": "abc"})
            self.assertIn("start", err)
        self.assertIn("required", core.parse_job_request({})[1])
        self.assertIn("not found", core.parse_job_request({"input": "/no/such/file.mp4"})[1])
        opts, err = core.parse_job_request({"input": "https://example.com/vod"})
        self.assertIsNone(err)
        self.assertTrue(opts["is_url"])

    def test_settings_merge(self):
        s = core.merge_settings({}, {"top": "7", "scenes": 1, "bogus": 5, "model": "nope", "hook": None})
        self.assertEqual(s["top"], 7)
        self.assertTrue(s["scenes"])
        self.assertNotIn("bogus", s)
        self.assertEqual(s["model"], "small")
        self.assertEqual(s["hook"], "")

    def test_timeline(self):
        c = {"start": 100.0, "end": 160.0, "peak": 120.0, "keep_pieces": [[100.0, 130.0], [134.0, 160.0]]}
        segs = core.timeline(c)
        kinds = [s["kind"] for s in segs]
        self.assertEqual(kinds[:4], ["setup", "build", "peak", "payoff"])
        self.assertIn("cut", kinds)
        main = [s for s in segs if s["kind"] != "cut"]
        self.assertAlmostEqual(main[0]["from"], 0.0)
        self.assertAlmostEqual(main[-1]["to"], 1.0)
        for a, b in zip(main, main[1:]):
            self.assertAlmostEqual(a["to"], b["from"], places=3)
        cut = next(s for s in segs if s["kind"] == "cut")
        self.assertEqual((cut["start"], cut["end"]), (130.0, 134.0))

    def test_heat_and_mask(self):
        self.assertEqual([core.heat(x) for x in (9, 7, 5.5, 2)], ["hot", "good", "ok", "low"])
        self.assertEqual(core.mask_text("what the fuck, bro"), "what the [beep], bro")
        self.assertEqual(core.mask_text("no way bro"), "no way bro")

    def test_safe_file_and_range(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "clip01.mp4"), "wb") as fh:
                fh.write(b"x" * 100)
            self.assertTrue(core.safe_file(d, "clip01.mp4"))
            for bad in ("../x", "/etc/passwd", ".env", "missing.mp4", "a/b.mp4"):
                self.assertIsNone(core.safe_file(d, bad))
            self.assertEqual(core.clip_files(d, 1), {"clip": "clip01.mp4"})
        self.assertEqual(core.parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(core.parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(core.parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(core.parse_range("bytes=50-500", 100), (50, 99))
        for bad in ("bytes=200-", "bytes=-", "items=0-1", None, "bytes=5-2"):
            self.assertIsNone(core.parse_range(bad, 100))


class TestServer(unittest.TestCase):
    """Starts the real HTTP server on a free port with the bundled demo job."""

    @classmethod
    def setUpClass(cls):
        from storycut.ui import server
        cls.tmp = tempfile.TemporaryDirectory()
        store = server.Store(cls.tmp.name)
        server.install_demo(store.root)
        server.Handler.store = store
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, data=None):
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_pages_and_pwa(self):
        code, h, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"manifest.webmanifest", body)
        code, _, body = self.get("/manifest.webmanifest")
        self.assertEqual(json.loads(body)["display"], "standalone")
        code, h, body = self.get("/static/icon-192.png")
        self.assertEqual((code, h["Content-Type"]), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(self.get("/sw.js")[0], 200)
        self.assertEqual(self.get("/static/../server.py")[0], 404)

    def test_demo_results(self):
        code, _, body = self.get("/api/jobs")
        jobs = json.loads(body)["jobs"]
        self.assertTrue(any(j["id"] == "demo-kai-finale" for j in jobs))
        code, _, body = self.get("/api/jobs/demo-kai-finale/results")
        self.assertEqual(code, 200)
        r = json.loads(body)
        top = r["candidates"][0]
        self.assertEqual(top["rank"], 1)
        self.assertIn("timeline", top)
        self.assertTrue(r["curve"]["combined"])
        self.assertNotIn("fuck", top["transcript"].lower())

    def test_actions_and_errors(self):
        code, _, body = self.get("/api/jobs/demo-kai-finale/cut/1", data=b"")
        self.assertEqual(code, 409)  # demo has no source media
        code, _, body = self.get("/api/jobs/demo-kai-finale/srt/1", data=b"")
        self.assertEqual(code, 200)
        code, _, body = self.get("/api/jobs", data=json.dumps({"input": "/no/such.mp4"}).encode())
        self.assertEqual(code, 400)
        self.assertEqual(self.get("/api/jobs/..%2Fx")[0], 404)

    def test_settings_roundtrip(self):
        code, _, body = self.get("/api/settings", data=json.dumps({"top": 3, "hook": "NO WAY|BRO"}).encode())
        self.assertEqual(json.loads(body)["top"], 3)
        code, _, body = self.get("/api/settings")
        self.assertEqual(json.loads(body)["hook"], "NO WAY|BRO")


if __name__ == "__main__":
    unittest.main()
