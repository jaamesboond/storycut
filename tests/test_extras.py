"""Tests for the 0.3.0 render extras: caption presets, layout parsing, facecam/letterbox
detection on synthetic frames, face-track smoothing, swear lists and SRT masking."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from storycut import frame, render, report, signals
from storycut.models import Peak, Window, Word


WORDS = [Word(3.5, 3.9, "what"), Word(4.0, 4.4, "the"), Word(4.5, 4.9, "fuck"), Word(5.0, 5.3, "bro")]


def ass_lines(ass, prefix):
    return [line for line in ass.splitlines() if line.startswith(prefix)]


class TestCaptionPresets(unittest.TestCase):
    def test_every_preset_beeps_and_positions(self):
        for p in render.CAPTION_PRESETS:
            ass = render.build_ass(WORDS, hook="BIG|HOOK", preset=p, position="seam")
            caps = ass_lines(ass, "Dialogue: 0,")
            self.assertTrue(caps, p)
            self.assertTrue(all("\\pos(720,1040)" in c for c in caps), p)
            self.assertNotIn("FUCK", ass.upper().replace("[BEEP]", ""), p)
            self.assertIn("BEEP", ass.upper(), p)

    def test_clean_has_no_box_and_box_has_box(self):
        clean = ass_lines(render.build_ass(WORDS, preset="clean"), "Style: Cap")[0]
        box = ass_lines(render.build_ass(WORDS, preset="box"), "Style: Cap")[0]
        self.assertIn(",1,7,0,8,", clean)          # BorderStyle 1 = outline only
        self.assertIn(",3,16,0,8,", box)           # BorderStyle 3 = opaque box
        self.assertIn("&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF", box)  # black text, white box

    def test_karaoke_highlights_each_word_in_parrot_green(self):
        caps = ass_lines(render.build_ass(WORDS, preset="karaoke"), "Dialogue: 0,")
        self.assertEqual(len(caps), 4)              # one event per spoken word
        for c in caps:
            self.assertEqual(c.count("&H52DE4A&"), 1)  # #4ade52 in ASS BGR order
        self.assertIn("{\\c&H52DE4A&\\fscx108\\fscy108}WHAT{\\r}", caps[0])
        ends = [c.split(",")[2] for c in caps]
        starts = [c.split(",")[1] for c in caps]
        self.assertTrue(all(e <= s for e, s in zip(ends, starts[1:])))  # no two lines on screen at once

    def test_pop_is_short_and_animated(self):
        caps = ass_lines(render.build_ass(WORDS, preset="pop"), "Dialogue: 0,")
        for c in caps:
            text = c.split("}")[-1]
            self.assertLessEqual(len(text.split()), 2)
            self.assertIn("\\t(0,90,", c)

    def test_minimal_is_lowercase(self):
        ass = render.build_ass(WORDS, preset="minimal")
        caps = ass_lines(ass, "Dialogue: 0,")
        self.assertIn("what the [beep]", caps[0])

    def test_lines_never_too_long(self):
        long = [Word(3.5 + i * 0.3, 3.7 + i * 0.3, t) for i, t in
                enumerate("chocolate, blueberry, vanilla mango strawberry milkshake".split())]
        for p in render.CAPTION_PRESETS:
            for c in ass_lines(render.build_ass(long, preset=p), "Dialogue: 0,"):
                plain = re.sub(r"\{[^}]*\}", "", c.split(",,0,0,0,,", 1)[1])
                self.assertLessEqual(len(plain), 34, (p, plain))

    def test_positions(self):
        self.assertEqual(render.caption_y("seam"), 1040)
        self.assertEqual(render.caption_y("seam", split=False), 1850)
        self.assertEqual(render.caption_y("top"), 420)
        self.assertEqual(render.caption_y("middle"), 1280)
        ass = render.build_ass(WORDS, preset="clean", position="lower-third")
        self.assertIn("\\pos(720,1850)", ass)

    def test_unknown_preset(self):
        with self.assertRaises(ValueError):
            render.build_ass(WORDS, preset="neon")

    def test_hook_hides_captions_under_it(self):
        ass = render.build_ass([Word(1.0, 1.4, "early")] + WORDS, hook="HOOK", preset="clean")
        self.assertNotIn("EARLY", ass)


class TestLayoutParsing(unittest.TestCase):
    def test_parse_box(self):
        self.assertEqual(render.parse_box("320:260:0:540"), (0, 540, 320, 260))
        self.assertEqual(render.parse_box("0,540,320,260"), (0, 540, 320, 260))
        self.assertEqual(render.parse_box("auto"), "auto")
        for off in ("", "none", None, "off"):
            self.assertIsNone(render.parse_box(off))
        self.assertEqual(render._crop("0,540,320,260"), "crop=320:260:0:540")

    def test_loudnorm(self):
        self.assertIn("loudnorm=I=-14", render.audio_filter([], loudnorm=True))
        self.assertIn("loudnorm=I=-14", render.audio_filter([(1, 2)], loudnorm=True))
        self.assertNotIn("loudnorm", render.audio_filter([(1, 2)]))


def synthetic(n=8, seed=0):
    """Letterboxed 'stream': bars of 20 px top/bottom, a moving noisy game, and a still
    framed camera (bright border) at bottom-left with a skin-coloured face that moves a little."""
    rng = np.random.default_rng(seed)
    h, w = 270, 480
    frames = np.zeros((n, h, w, 3), dtype=np.uint8)
    room = rng.integers(20, 60, size=(9, 10, 3), dtype=np.uint8)
    room[..., 2] += 50  # blue-grey wall, never skin-coloured
    room = np.kron(room, np.ones((10, 10, 1), dtype=np.uint8))
    for i in range(n):
        blocks = rng.integers(40, 200, size=(23, 48, 3), dtype=np.uint8)  # game: big shapes, new every frame
        game = np.kron(blocks, np.ones((10, 10, 1), dtype=np.uint8))
        frames[i, 20:250] = np.roll(game, (int(rng.integers(0, 10)), int(rng.integers(0, 10))), axis=(0, 1))
        frames[i, 150:240, 10:110] = room                    # camera background: a still room
        frames[i, 150:152, 10:110] = frames[i, 238:240, 10:110] = 230   # camera border lines
        frames[i, 150:240, 10:12] = frames[i, 150:240, 108:110] = 230
        fx = 45 + 3 * (i % 3)
        frames[i, 165:205, fx:fx + 32] = (210, 150, 120)     # face
    return frames


class TestFrameAnalysis(unittest.TestCase):
    def test_content_box_finds_letterbox(self):
        x, y, w, h = frame.content_box(synthetic())
        self.assertEqual((x, w), (0, 480))
        self.assertTrue(18 <= y <= 21, y)
        self.assertTrue(228 <= h <= 232, h)

    def test_trim_dark_removes_black_strip(self):
        f = synthetic()
        f[:, :, 0:6] = 0
        self.assertEqual(frame.trim_dark(f, (0, 20, 480, 230))[0], 6)

    def test_skin_face_found(self):
        faces = frame.skin_faces(synthetic()[0])
        self.assertTrue(faces)
        x, y, w, h = faces[0]
        self.assertTrue(40 <= x <= 52 and 160 <= y <= 170, faces[0])

    def test_facecam_rect_hugs_the_camera(self):
        f = synthetic()
        face = frame.skin_faces(f[0])[0]
        x, y, w, h = frame.facecam_rect(f, face, frame.content_box(f))
        self.assertTrue(abs(x - 10) <= 8 and abs(y - 150) <= 8, (x, y, w, h))
        self.assertTrue(abs(x + w - 110) <= 8 and abs(y + h - 240) <= 8, (x, y, w, h))

    def test_stable_face_ignores_moving_game_faces(self):
        f = synthetic()
        rng = np.random.default_rng(3)
        for i in range(len(f)):  # a 'game character' that is somewhere else in every frame
            x, y = int(rng.integers(200, 420)), int(rng.integers(30, 180))
            f[i, y:y + 40, x:x + 32] = (210, 150, 120)
        face = frame.stable_face(f)
        self.assertIsNotNone(face)
        self.assertLess(face[0], 120)

    def test_smooth_kills_jitter_and_follows_moves(self):
        jitter = [100, 103, 99, 101, 102, 98, 100]
        out = frame.smooth(jitter, deadzone=5)
        self.assertEqual(len(set(out)), 1)  # one steady value, no wobble
        self.assertLessEqual(abs(out[0] - 100), 3)
        moved = frame.smooth([100] * 5 + [200] * 12, deadzone=5)
        self.assertGreater(moved[-1], 190)
        self.assertTrue(all(b >= a for a, b in zip(moved, moved[1:])))  # no overshoot / bounce

    def test_fit_window_never_zooms_into_tiny_cams(self):
        self.assertEqual(frame.fit_window((0, 0, 336, 284), 1440 / 1040, face_w=70), (336, 242))
        w, h = frame.fit_window((0, 0, 1280, 720), 1440 / 1040, face_w=120, max_zoom=1.5)
        self.assertEqual(w, 664)  # 997 px fits, 1.5x max zoom stops at 664
        self.assertAlmostEqual(w / h, 1440 / 1040, delta=0.02)

    def test_crop_expression(self):
        tr = {"w": 400, "h": 290, "keys": [(0.0, 10, 20), (2.0, 30, 20), (4.0, 30, 40)]}
        c = frame.tracked_crop(tr)
        self.assertTrue(c.startswith("crop=400:290:"))
        self.assertNotIn(",", c.replace("\\,", ""))  # every comma escaped for the filtergraph
        expr = frame.lerp_expr(tr["keys"], 1)
        val = eval(expr.replace("lt(", "_lt(").replace("gte(", "_gte("),  # noqa: S307 (test only)
                   {"_lt": lambda a, b: float(a < b), "_gte": lambda a, b: float(a >= b), "t": 1.0})
        self.assertAlmostEqual(val, 20.0)

    def test_simplify_drops_collinear(self):
        keys = [(float(t), 10 * t, 0) for t in range(6)]
        self.assertEqual(len(frame.simplify(keys)), 2)


class TestProfanityAndSrt(unittest.TestCase):
    def test_hindi_urdu_swears(self):
        for w in ("bhosdike", "Chutiye!", "मादरचोद", "حرامی", "gaand", "bsdk"):
            self.assertTrue(signals.is_profane(w), w)

    def test_no_false_hits_on_normal_words(self):
        for w in ("chodo", "class", "hello", "gandhi", "lunch", "kuttu", "salad"):
            self.assertFalse(signals.is_profane(w), w)

    def test_srt_masked_by_default(self):
        w = Window(0, 10, Peak(5, 1))
        words = [Word(1.0, 1.3, "oh"), Word(1.4, 1.8, "fuck"), Word(2.0, 2.3, "chutiya")]
        srt = report.window_srt(w, words)
        self.assertIn("oh [beep] [beep]", srt)
        self.assertIn("fuck", report.window_srt(w, words, mask=False))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class TestRenderSmoke(unittest.TestCase):
    """A tiny real render (2 s, small test pattern) through the whole filter graph."""

    def test_tracked_render_with_karaoke(self):
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                            "-f", "lavfi", "-i", "sine=f=300:d=2", "-shortest", "-c:v", "libx264", "-preset",
                            "ultrafast", "-c:a", "aac", src], check=True)
            out = os.path.join(d, "out.mp4")
            words = [Word(0.2, 0.6, "hello"), Word(0.7, 1.0, "fuck"), Word(1.1, 1.5, "there")]
            orig = render.resolve_layout
            render.resolve_layout = lambda *a, **k: {
                "facecam": (0, 0, 320, 180), "gameplay": (0, 0, 640, 360), "notes": [],
                "track": {"w": 248, "h": 180, "keys": [(0.0, 0, 0), (1.5, 60, 0)], "found": 3, "samples": 4}}
            try:
                render.render(src, 0, 2, words, out, facecam="auto", captions="karaoke", loudnorm=True)
            finally:
                render.resolve_layout = orig
            p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name",
                                "-of", "csv=p=0", out], capture_output=True, text=True, check=True).stdout
            self.assertIn("h264,1440,2560", p)
            self.assertTrue(os.path.isfile(out + ".layout.json"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
