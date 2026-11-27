import json
import os
import tempfile
import unittest

import numpy as np

from storycut import live
from storycut.models import Peak, Window, Word


def make_session(tmp, **kw):
    opts = live.LiveOptions(out_dir=tmp, log=False, transcribe="gated", **kw)
    s = live.LiveSession("dummy.mp4", opts)
    # no whisper in unit tests
    s.transcriber = lambda audio, off: []
    s.transcriber.rtf = None
    return s


class FakeTranscriber:
    rtf = 0.1

    def __init__(self, words):
        self.words = words
        self.calls = []

    def __call__(self, audio, off):
        self.calls.append((off, len(audio) / live.SR))
        end = off + len(audio) / live.SR
        return [w for w in self.words if off <= w.start < end]


class IntervalTests(unittest.TestCase):
    def test_add_and_missing(self):
        iv = live.add_interval([], 0, 10)
        iv = live.add_interval(iv, 20, 30)
        iv = live.add_interval(iv, 9, 21)
        self.assertEqual(iv, [[0, 30]])
        self.assertEqual(live.missing([[0, 10], [20, 30]], 0, 40), [[10, 20], [30, 40]])
        self.assertEqual(live.missing([[0, 40]], 5, 30), [])


class HelperTests(unittest.TestCase):
    def test_twitch_channel(self):
        self.assertEqual(live.twitch_channel("https://www.twitch.tv/Kaicenat"), "kaicenat")
        self.assertIsNone(live.twitch_channel("https://www.youtube.com/@x/live"))
        self.assertIsNone(live.twitch_channel("https://www.twitch.tv/videos/123"))

    def test_categorise(self):
        self.assertEqual(live.categorise(["laugh", "laugh", "no way"]), "funny")
        self.assertEqual(live.categorise(["no way", "what the"]), "shock")
        self.assertEqual(live.categorise([]), "moment")

    def test_title_uses_quote_and_setup(self):
        words = [Word(i * 0.5, i * 0.5 + 0.4, t) for i, t in enumerate(
            "okay we finally reached the top of the tower.".split())]
        w = Window(start=0, end=20, peak=Peak(t=6, value=3))
        t = live.story_title(w, words, [(6.5, 2.0, "no way")])
        self.assertIn("NO WAY", t["title"])
        self.assertIn("tower", t["title"])
        self.assertEqual(t["category"], "shock")

    def test_title_fallback(self):
        w = Window(start=0, end=20, peak=Peak(t=6, value=3))
        t = live.story_title(w, [], [], offset=60)
        self.assertEqual(t["title"], "Moment at 00:01:06")

    def test_phases_ordered(self):
        comb = np.zeros(100)
        comb[40:50] = np.linspace(0.5, 3, 10)
        w = Window(start=10, end=80, peak=Peak(t=49, value=3))
        ph = live.story_phases(w, comb)
        self.assertEqual(ph["setup"][0], 10)
        self.assertLessEqual(ph["setup"][1], ph["build"][0] + 1e-9)
        self.assertGreaterEqual(ph["build"][0], 39)
        self.assertLessEqual(ph["peak"][1], ph["payoff"][0] + 1e-9)
        self.assertEqual(ph["payoff"][1], 80)

    def test_filechat(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "chat.txt")
            with open(p, "w") as f:
                f.write("10.2 hello\n10.9 CLIP IT\n12 x\n")
            c = live.FileChat(p)
            c.poll()
            self.assertEqual(c.counts, {10: 2, 12: 1})
            self.assertEqual(c.clip_calls, [10.9])
            with open(p, "a") as f:
                f.write("13 more\n")
            c.poll()
            self.assertEqual(c.counts[13], 1)


class SessionTests(unittest.TestCase):
    def _feed(self, s, loud_db, chunk=6):
        """Feed synthetic audio whose per-second loudness follows loud_db."""
        rng = np.random.default_rng(0)
        for a in range(0, len(loud_db), chunk):
            secs = []
            for db in loud_db[a:a + chunk]:
                amp = 10 ** (db / 20.0)
                secs.append((rng.standard_normal(live.SR) * amp).astype(np.float32))
            audio = np.concatenate(secs)
            s._append(audio, {"file": f"x{a}.ts", "src": f"/nope/x{a}.ts", "src_off": 0.0, "part": 0,
                              "dur": len(audio) / live.SR})
            s.tick()

    def test_spike_opens_and_closes_story(self):
        with tempfile.TemporaryDirectory() as d:
            s = make_session(d, min_score=0.0)
            words = [Word(t, t + 0.4, "talk") for t in np.arange(0, 400, 0.6)]
            words += [Word(301.0, 301.5, "no"), Word(301.5, 302.0, "way"), Word(302.2, 302.6, "bro"),
                      Word(302.6, 303.0, "what")]
            s.transcriber = FakeTranscriber(sorted(words, key=lambda w: w.start))
            loud = [-35.0] * 300 + [-6.0] * 8 + [-35.0] * 60
            self._feed(s, loud)
            self.assertEqual(len(s.emitted), 1, [e["kind"] for e in s.events])
            c = s.emitted[0]
            self.assertTrue(295 <= c["peak"] <= 312, c["peak"])
            self.assertLess(c["start"], c["peak"])
            self.assertGreater(c["end"], c["peak"])
            self.assertIn("phases", c)
            self.assertIn("latency_after_payoff", c)
            self.assertEqual(c["closed_because"], "excitement back to normal")
            # gated mode transcribed only around the spike, not the whole stream
            covered = sum(b - a for a, b in s.done_iv)
            self.assertLess(covered, 300)
            with open(os.path.join(d, "candidates.json")) as f:
                out = json.load(f)
            self.assertEqual(len(out["candidates"]), 1)

    def test_stories_never_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            s = make_session(d, min_score=0.0, min_gap=20.0)
            words = [Word(t, t + 0.4, "talk.") for t in np.arange(0, 600, 3.0)]
            s.transcriber = FakeTranscriber(words)
            loud = [-35.0] * 300 + [-6.0] * 8 + [-35.0] * 30 + [-5.0] * 8 + [-35.0] * 80
            self._feed(s, loud)
            self.assertGreaterEqual(len(s.emitted) + sum(e["kind"] == "merged" for e in s.events), 2)
            for a, b in zip(s.emitted, s.emitted[1:]):
                self.assertGreaterEqual(b["start"], a["end"] - 0.01, (a["start"], a["end"], b["start"], b["end"]))

    def test_no_spike_no_story(self):
        with tempfile.TemporaryDirectory() as d:
            s = make_session(d, min_score=0.0)
            self._feed(s, [-30.0] * 300)
            self.assertEqual(s.emitted, [])
            self.assertIsNone(s.pending)

    def test_low_score_is_dropped_not_emitted(self):
        with tempfile.TemporaryDirectory() as d:
            s = make_session(d, min_score=9.9)
            self._feed(s, [-35.0] * 300 + [-6.0] * 8 + [-35.0] * 60)
            self.assertEqual(s.emitted, [])
            self.assertEqual(len(s.dropped), 1)

    def test_state_resume(self):
        with tempfile.TemporaryDirectory() as d:
            s = make_session(d, min_score=0.0)
            self._feed(s, [-35.0] * 300 + [-6.0] * 8 + [-35.0] * 60)
            s.save_state()
            n, tl = len(s.emitted), s.tl_end
            s2 = make_session(d, min_score=0.0)
            self.assertEqual(len(s2.emitted), n)
            self.assertAlmostEqual(s2.tl_end, tl, places=2)
            self.assertEqual(len(s2.loud), len(s.loud))
            self.assertEqual(s2.next_part(), s.part + 1)

    def test_listed_pieces_reads_csv(self):
        with tempfile.TemporaryDirectory() as d:
            s = make_session(d)
            with open(os.path.join(s.seg_dir, "p002.csv"), "w") as f:
                f.write("p002_00000.ts,0.000000,6.000000\np002_00001.ts,6.000000,12.000000\n")
            got = s._listed_pieces()
            self.assertEqual([g[1] for g in got], ["p002_00000.ts", "p002_00001.ts"])
            self.assertEqual(got[0][0], 2)


if __name__ == "__main__":
    unittest.main()
