import unittest

from storycut.render import encode_args


class EncodeArgsTest(unittest.TestCase):
    def test_single_pass_upload_ready(self):
        a = encode_args()
        s = " ".join(a)
        for part in ("-c:v libx264", "-profile:v high", "-pix_fmt yuv420p", "-preset superfast", "-crf 23",
                     "-maxrate 7M", "-bufsize 14M", "-c:a aac", "-movflags +faststart"):
            self.assertIn(part, s)
        self.assertNotIn("-threads", a)

    def test_no_cap_and_threads(self):
        a = encode_args(preset="faster", crf=21, maxrate=None, threads=2)
        self.assertNotIn("-maxrate", a)
        self.assertEqual(a[a.index("-threads") + 1], "2")
        self.assertEqual(a[a.index("-preset") + 1], "faster")
        self.assertEqual(a[a.index("-crf") + 1], "21")


if __name__ == "__main__":
    unittest.main()
