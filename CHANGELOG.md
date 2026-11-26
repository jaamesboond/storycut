# Changelog

## 0.2.1 (2026-11-27)

* Dashboard: fixed horizontal overflow on phones (grid columns can shrink,
  long job names and details now truncate instead of widening the page).

## 0.2.0 (2026-11-23)

* New **dashboard**: `storycut ui` starts a local neon web app (standard library
  only): new job form, live progress, excitement-curve chart with story windows,
  score badges, setup/build/peak/payoff timeline, story detail with reasons,
  transcript, clip preview and cut / SRT / 9:16 render buttons, settings.
* Installable **PWA** (manifest, service worker, app icons); mobile-first layout;
  `--host 0.0.0.0` shows the LAN link so a phone can use a PC as the engine.
* New **logo** (`storycut/ui/static/logo.svg`) and app icons
  (`storycut ui --export-icons DIR`).
* `find` now saves `curve.json` (downsampled excitement curve) and reports
  progress to a callback.
* Bundled demo job (sanitised results from the 30-minute test run).
* Tests for the dashboard helpers and an in-process server test (33 tests total).
* README: quick start, dashboard, Android/Termux notes, honest comparison.

## 0.1.0 (2026-11-23)

* First release: `find` (faster-whisper transcript, loudness, onsets, reaction
  cues incl. Hindi/Urdu, optional chat and scene changes), story-window
  expansion, 0–10 scoring with reasons, SRT export, `--cut`, optional Gemini
  re-rank, and `render` for 1440x2560 split-screen Shorts with beeped profanity.
