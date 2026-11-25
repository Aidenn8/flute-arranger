# Listen to this computer: design

Date: 2026-11-24. Approved by the user ("do your pick, don't do live, just do what would be the most accurate and best").

## Why

Instead of finding a file, the user plays a song on the Mac (Spotify, YouTube in Chrome, anything) and gets the same sheet music.

## What

- Home page: "Listen to this computer" with a title box, the same "What is it?" and "Make" choices as an upload, and a **Start listening** button.
- Listening page (refreshes every 2 s, old-web style): elapsed time, whether sound is coming in, **Stop and make the sheet music**, **Cancel**.
- Stop: leading and trailing silence trimmed, saved as the song's `input.wav`, then queued exactly like an upload. Nothing heard: a plain message, no song made.
- Stops by itself after 12 minutes (the upload limit).
- One recording at a time.

Not live: notes are found after the recording, from the whole song, which is more accurate (non-goal: real-time notation).

## How

- `tools/sysaudio/main.swift`: a small command-line recorder on Apple's ScreenCaptureKit (macOS 13+). It captures everything the Mac plays, whatever the output device (headphones included), excluding its own process. Built locally with `xcrun swiftc` (Xcode is installed) by `scripts/build_sysaudio.sh` into `tools/bin/sysaudio` (git-ignored binary).
  - `sysaudio check`: prints `OK` or `NO` (Screen Recording permission).
  - `sysaudio request`: asks macOS for the permission (shows the system prompt).
  - `sysaudio record OUT.wav [--max-seconds N]`: prints `READY` when capturing, `LEVEL <rms dBFS>` about twice a second, and `DONE <seconds>` after stopping; stops on SIGINT/SIGTERM, on "stop" or EOF on stdin, or at the limit. Exit 2 with `PERMISSION` on stdout when not allowed.
- Web app: `sheetswap/listen.py` runs one recorder subprocess and reads its stdout on a thread; routes `/listen` (GET page, POST start/stop/cancel).
- Permission: macOS asks once. The page explains: System Settings → Privacy & Security → Screen & System Audio Recording → allow the app Flute Arranger was started from (Terminal), then reopen it.

Alternative considered: the BlackHole driver that's already installed. It needs the Mac's output switched to a Multi-Output Device every time, which loses volume keys and drifts with Bluetooth headphones, so it was rejected.

## Musical quality pass (same request)

Audit real recordings and test music for accuracy against known melodies, flute-and-piano harmony (clashes, chord fit, bass), rhythm readability, playability and how the preview sounds compared with the original. Fix the biggest problems with music theory, and re-measure.
