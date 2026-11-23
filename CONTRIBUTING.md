# Contributing to storycut

Thanks for helping! storycut is small on purpose: plain Python, two runtime
dependencies (faster-whisper, numpy), a standard-library web dashboard and no
build step. Please keep it that way.

## Setup

```bash
git clone https://github.com/jaamesboond/storycut
cd storycut
pip install -e .            # or: pip install -r requirements.txt
python -m unittest discover -s tests -v
```

You need `ffmpeg` and `ffprobe` on your PATH for real runs (the tests do not).

## Good first contributions

* **Test it on a real stream** and open an issue with: the VOD (if public),
  the windows storycut picked, and the moments a human would pick. These
  comparisons are the most useful thing right now.
* **Reaction cues for a new language** in `storycut/signals.py` (keep them
  lowercase, romanised where people type that way, with a test).
* Better detection of **quiet moments** (the known weak spot).
* Dashboard polish in `storycut/ui/static/` (vanilla JS/CSS, mobile first).

## Rules

* Add or update a unit test for any logic change; tests must not need media files.
* Keep the CPU path fast; heavy features should be opt-in flags.
* No API keys, tokens or personal paths in code, tests or examples.
* Only use streams you are allowed to clip for examples, and credit creators.
* Be kind in issues and reviews.

## Releasing (maintainers)

Bump `version` in `pyproject.toml` and `storycut/__init__.py`, add a
CHANGELOG entry, tag `vX.Y.Z`.
