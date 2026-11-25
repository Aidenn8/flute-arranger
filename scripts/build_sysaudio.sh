#!/usr/bin/env bash
# Build the system-audio recorder (tools/sysaudio/main.swift) into tools/bin/sysaudio.
# Needs Xcode or the Command Line Tools (xcrun swiftc). Runs on macOS 13 or newer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/tools/sysaudio/main.swift"
OUT="$ROOT/tools/bin/sysaudio"
ARCH="$(uname -m)" # arm64 or x86_64

mkdir -p "$ROOT/tools/bin"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

xcrun swiftc -O -target "$ARCH-apple-macos13.0" \
  -framework ScreenCaptureKit -framework AVFoundation -framework CoreMedia -framework CoreGraphics \
  -o "$TMP/sysaudio" "$SRC"

# Replace the old binary in one step (overwriting a binary in place can get it killed by macOS).
mv -f "$TMP/sysaudio" "$OUT"
echo "built $OUT"
