import unittest

import numpy as np

from storycut import signals
from storycut.models import Word


def words_from(text, start=0.0, step=0.4):
    out, t = [], start
    for tok in text.split():
        out.append(Word(t, t + step * 0.8, tok))
        t += step
    return out


class TestCues(unittest.TestCase):
    def test_phrase_and_laugh_and_swear(self):
        ws = words_from("bro no way what just happened hahaha that was shit")
        labels = [e[2] for e in signals.cue_events(ws)]
        self.assertIn("no way", labels)
        self.assertIn("what just happened", labels)
        self.assertIn("laugh", labels)
        self.assertIn("swear", labels)

    def test_urdu_hindi_cues(self):
        ws = words_from("arey bhai mar gaya ya allah")
        labels = [e[2] for e in signals.cue_events(ws)]
        self.assertIn("mar gaya", labels)
        self.assertIn("ya allah", labels)

    def test_profanity_does_not_flag_clean_words(self):
        self.assertFalse(signals.is_profane("class"))
        self.assertFalse(signals.is_profane("hello"))
        self.assertTrue(signals.is_profane("Fucking!"))


class TestCurves(unittest.TestCase):
    def test_robust_z_flat(self):
        self.assertTrue(np.allclose(signals.robust_z([1, 1, 1, 1]), 0))

    def test_rms_per_second(self):
        sr = 1000
        quiet = np.zeros(sr) + 1e-4
        loud = np.sin(np.linspace(0, 200 * np.pi, sr)) * 0.5
        db = signals.rms_per_second(np.concatenate([quiet, loud]).astype(np.float32), sr)
        self.assertEqual(len(db), 2)
        self.assertGreater(db[1], db[0] + 30)

    def test_combine_and_peaks(self):
        n = 600
        loud = np.random.RandomState(0).normal(0, 1, n)
        loud[300:305] += 25
        combined, parts = signals.combine({"loud": loud, "chat": None})
        self.assertNotIn("chat", parts)
        peaks = signals.find_peaks(combined, top_k=3, min_gap=90, min_value=0.5, parts=parts)
        self.assertTrue(peaks)
        self.assertTrue(295 <= peaks[0].t <= 310)
        for a in peaks:
            for b in peaks:
                if a is not b:
                    self.assertGreaterEqual(abs(a.t - b.t), 90)

    def test_onset_length_matches_seconds(self):
        sr = 16000
        x = np.random.RandomState(1).normal(0, 0.1, sr * 30).astype(np.float32)
        self.assertEqual(len(signals.onset_per_second(x, sr)), 30)

    def test_events_to_curve(self):
        c = signals.events_to_curve([(5, 2.0, "x")], 10, spread=1)
        self.assertEqual(int(np.argmax(c)), 5)
        self.assertGreater(c[4], 0)


if __name__ == "__main__":
    unittest.main()
