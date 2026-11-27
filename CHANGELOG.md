# Changelog

## 0.3.0 (2026-11-27)

* New **live mode**: `storycut live <url|file>` records Twitch / YouTube / Kick
  (yt-dlp + ffmpeg, 6 s pieces, auto-reconnect) or follows a growing file, and
  posts full-story candidates (setup → build → peak → payoff, 0–10 with reasons)
  a few seconds after each payoff closes. Cheap loudness/onset/chat signals run
  on everything; whisper runs on everything only if the CPU keeps up, otherwise
  only around spikes (`--transcribe auto|all|gated`, `--model tiny|base`).
  Twitch chat spikes and "clip it" calls are read anonymously. Optional `--cut`
  and `--render` per story. Resumes after a crash or restart from its folder.
  Readable titles from the quote + setup line, category guess, latency.
* Dashboard **Live** tab: start/stop sessions, status, pending spike, stories as
  they appear with preview, Cut clip / Render 9:16, activity log. Live sessions
  resume when the dashboard restarts.
* **Minimal redesign**: neutral light and dark themes (follows the system or
  pick one), one parrot-green accent, no glow; new logo and app icons; README visuals
  and social preview redrawn.
* **Faster render**: one pass straight to an upload-ready file (input seek, fps
  drop before scaling, single decode, `-preset superfast -crf 23 -maxrate 7M
  -bufsize 14M`), new `--preset`, `--maxrate`, `--threads` options. See README
  for the benchmark.
* **Facecam auto + face tracking**: `--facecam auto` finds the streamer camera
  (stable, alive face on a still overlay near an edge; OpenCV face detector if
  installed, numpy skin-colour finder otherwise), crops it to its true edges,
  and follows the face with median + exponential smoothing and a dead zone;
  `--gameplay auto` cuts letterbox bars. New `storycut layout` command prints
  what was found. `--no-track`, `--max-zoom`. Crops also accept `x,y,w,h`.
* **Caption presets**: `--captions clean|box|karaoke|pop|minimal` and
  `--caption-pos seam|top|middle|lower-third`; karaoke highlights the spoken word
  in parrot green. Picker with previews in dashboard Settings.
* `--loudnorm` (-14 LUFS), `--thumb` (most expressive face frame as a JPG),
  `<out>.layout.json` with the detected layout.
* Hindi/Urdu/Nepali swear list (Roman, Devanagari, Urdu script, whole-word
  match) for auto-beep; Hindi words no longer prefix-match (no more "salad"
  beeps). Exported SRT files now mask swears as `[beep]` by default.
* Credit: by **Ty Yb** ([Instagram](https://www.instagram.com/tayyabtufail)),
  in the README, dashboard footer and About, and package metadata.
* 74 tests.

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
