import json
import os
import tempfile
import unittest

import numpy as np

from storycut import chat, report, render
from storycut.models import Peak, Window, Word
from storycut.score import score_window


class TestChat(unittest.TestCase):
    def test_parse_ts(self):
        self.assertEqual(chat.parse_ts("90"), 90.0)
        self.assertEqual(chat.parse_ts("1:02:03"), 3723.0)
        self.assertEqual(chat.parse_ts("02:03"), 123.0)
        self.assertIsNone(chat.parse_ts("abc"))

    def test_youtube_live_chat_lines(self):
        lines = [json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms)}}) for ms in (1000, 1500, 9000)]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("\n".join(lines))
        try:
            offs = chat.load_chat_offsets(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(offs, [1.0, 1.5, 9.0])
        c = chat.chat_curve(offs, 10)
        self.assertEqual(c[1], 2)
        self.assertEqual(c[9], 1)

    def test_twitch_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"comments": [{"content_offset_seconds": 3.2}, {"content_offset_seconds": 4}]}, f)
        try:
            self.assertEqual(chat.load_chat_offsets(f.name), [3.2, 4.0])
        finally:
            os.unlink(f.name)


class TestReport(unittest.TestCase):
    def test_srt_relative_times(self):
        w = Window(100, 110, Peak(105, 2))
        words = [Word(101.0, 101.4, "hello"), Word(101.5, 101.9, "there"), Word(120, 121, "outside")]
        srt = report.window_srt(w, words)
        self.assertIn("00:00:01,000 --> ", srt)
        self.assertIn("hello there", srt)
        self.assertNotIn("outside", srt)

    def test_fmt(self):
        self.assertEqual(report.fmt_hms(3725.9), "01:02:05")
        self.assertEqual(report.srt_time(1.5), "00:00:01,500")


class TestScore(unittest.TestCase):
    def test_score_range_and_reasons(self):
        n = 120
        combined = np.zeros(n); combined[60] = 3.0
        w = Window(30, 90, Peak(60, 3.0))
        words = [Word(t, t + 0.3, "word") for t in np.arange(30, 90, 0.5)]
        cues = [(61, 2.5, "no way"), (63, 2.0, "laugh")]
        score_window(w, combined, {}, words, cues)
        self.assertTrue(0 <= w.score <= 10)
        self.assertGreater(w.score, 5)
        self.assertEqual(len(w.reasons), 4)


class TestRenderHelpers(unittest.TestCase):
    def test_ass_has_hook_beep_and_no_box(self):
        words = [Word(0.5, 0.9, "intro"), Word(3.5, 3.9, "what"), Word(4.0, 4.4, "the"), Word(4.5, 4.9, "fuck")]
        ass = render.build_ass(words, hook="BIG|HOOK", end_text="WHO WINS?", clip_len=10)
        self.assertIn("BIG\\NHOOK", ass)
        self.assertIn("[BEEP]", ass)
        self.assertNotIn("FUCK", ass)
        self.assertIn(",1,7,0,8,", ass)  # BorderStyle 1 = outline only, no background box

    def test_beep_filter(self):
        f = render.audio_filter([(1.0, 1.5)])
        self.assertIn("sine=f=1000", f)
        self.assertIn("between(t,1.000,1.500)", f)
        self.assertEqual(render.audio_filter([]), "[0:a]anull[aout]")


if __name__ == "__main__":
    unittest.main()
