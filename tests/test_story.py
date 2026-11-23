import unittest

from storycut import story
from storycut.models import Peak, Window, Word


def sent_words(chunks):
    """chunks: list of (start_time, "text of sentence.") -> words spaced 0.4s."""
    out = []
    for t, text in chunks:
        for tok in text.split():
            out.append(Word(t, t + 0.3, tok))
            t += 0.4
    return out


class TestSentences(unittest.TestCase):
    def test_split_on_punctuation_and_pause(self):
        ws = sent_words([(0, "we go in here."), (2, "okay"), (5, "no way bro")])
        ss = story.sentences_from_words(ws, gap=0.8)
        self.assertEqual([s.text for s in ss], ["we go in here.", "okay", "no way bro"])

    def test_silence_gaps(self):
        ws = sent_words([(0, "a b."), (10, "c d.")])
        gaps = story.silence_gaps(story.sentences_from_words(ws), min_gap=4)
        self.assertEqual(len(gaps), 1)


class TestExpand(unittest.TestCase):
    def setUp(self):
        # a story: unrelated talk, long pause (scene break), setup, build, peak, reaction, then later talk
        chunks = [(0, "random intro talk here."), (5, "more random talk.")]
        chunks += [(40, "okay lets drive into the base."), (44, "there is a jet over there."),
                   (48, "i am taking it."), (52, "five stars now."), (56, "go go go."),
                   (60, "NO WAY WE ARE FLYING."), (63, "hahaha bro."), (66, "that was insane."),
                   (69, "chat did you see that.")]
        chunks += [(140, "anyway back to the menu.")]
        self.words = sent_words(chunks)
        self.sents = story.sentences_from_words(self.words)

    def test_story_window_contains_setup_and_payoff(self):
        w = story.expand_window(Peak(t=60.5, value=3.0), self.sents, target=60, max_len=180)
        self.assertLessEqual(w.start, 44.0)        # includes the setup about the base / jet
        self.assertGreaterEqual(w.start, 20.0)     # but stops at the long pause, not the intro
        self.assertGreaterEqual(w.end, 66.0)       # includes the reaction / payoff
        self.assertLess(w.end, 140.0)              # does not run into unrelated later talk
        self.assertLessEqual(w.duration, 180)

    def test_max_len_respected(self):
        w = story.expand_window(Peak(t=60.5, value=3.0), self.sents, target=300, max_len=30)
        self.assertLessEqual(w.duration, 30.01)
        self.assertTrue(w.start <= 60.5 <= w.end)

    def test_no_sentences_fallback(self):
        w = story.expand_window(Peak(t=100, value=1.0), [], target=60, media_end=120)
        self.assertLessEqual(w.end, 120)
        self.assertTrue(w.start < 100 < w.end)

    def test_trim_dead_air_keeps_peak(self):
        w = story.expand_window(Peak(t=60.5, value=3.0), self.sents, target=60)
        pieces = story.trim_dead_air(w, self.words, max_gap=2.5)
        self.assertTrue(any(a <= 60.5 <= b for a, b in pieces))
        self.assertLessEqual(sum(b - a for a, b in pieces), w.duration + 1e-6)


class TestMerge(unittest.TestCase):
    def test_overlap_keeps_best(self):
        a = Window(0, 60, Peak(30, 1)); a.score = 8
        b = Window(10, 70, Peak(40, 1)); b.score = 6
        c = Window(200, 260, Peak(230, 1)); c.score = 5
        kept = story.merge_overlaps([b, a, c])
        self.assertEqual([k.score for k in kept], [8, 5])


if __name__ == "__main__":
    unittest.main()
