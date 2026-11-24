#!/usr/bin/env bash
# Download the soundfont used to render audio (MuseScore_General.sf2, MIT license)
# into models/. Safe to run again: skips files that are already there.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODELS="$ROOT/models"
BASE="https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General"
SF2="$MODELS/MuseScore_General.sf2"
MIN_BYTES=200000000   # the real file is ~215 MB; anything much smaller is a broken download

mkdir -p "$MODELS"

file_size() { wc -c < "$1" | tr -d ' '; }

if [[ -f "$SF2" && "$(file_size "$SF2")" -ge "$MIN_BYTES" ]]; then
  echo "soundfont already present: $SF2"
else
  echo "downloading MuseScore_General.sf2 (~215 MB) ..."
  curl -fL --retry 3 -o "$SF2.part" "$BASE/MuseScore_General.sf2"
  size="$(file_size "$SF2.part")"
  if [[ "$size" -lt "$MIN_BYTES" ]]; then
    echo "error: download is only $size bytes, expected >= $MIN_BYTES" >&2
    rm -f "$SF2.part"
    exit 1
  fi
  if [[ "$(head -c 4 "$SF2.part")" != "RIFF" ]]; then
    echo "error: downloaded file is not a RIFF soundfont" >&2
    rm -f "$SF2.part"
    exit 1
  fi
  mv "$SF2.part" "$SF2"
  echo "saved $SF2 ($size bytes)"
fi

# Keep the license next to the soundfont.
if [[ ! -f "$MODELS/MuseScore_General_License.md" ]]; then
  curl -fsL -o "$MODELS/MuseScore_General_License.md" "$BASE/MuseScore_General_License.md" \
    || echo "warning: could not fetch the license file (soundfont is still fine)" >&2
fi
