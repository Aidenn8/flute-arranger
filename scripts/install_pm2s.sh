#!/usr/bin/env bash
# Install PM2S (Performance MIDI-to-Score, Liu et al., ISMIR 2022; MIT) into .venv and
# its five checkpoints (~390 MB, Zenodo record 10520196) into models/pm2s/.
# Upstream (github.com/cheriell/PM2S) has no packaging, so a minimal pyproject is
# written next to a copy of its pm2s/ folder and installed from there.
# Safe to run again: skips what is already there.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
COMMIT="9586f91cd16aaa50dbb82f720f6a34a3e0186f47"     # main, 2024-10-10
MODELS="$ROOT/models/pm2s"
ZENODO="https://zenodo.org/records/10520196/files"

if "$PY" -c "import pm2s" 2>/dev/null; then
  echo "pm2s already installed"
else
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL -o "$TMP/pm2s.zip" "https://github.com/cheriell/PM2S/archive/$COMMIT.zip"
  mkdir "$TMP/src" "$TMP/build"
  (cd "$TMP/src" && unzip -q ../pm2s.zip)
  cp -R "$TMP/src/PM2S-$COMMIT/pm2s" "$TMP/build/"
  cp "$TMP/src/PM2S-$COMMIT/LICENSE" "$TMP/build/"
  cat > "$TMP/build/pyproject.toml" <<'TOML'
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "pm2s"
version = "1.1"
description = "Performance MIDI-to-Score conversion (Liu, Kong, Morfi, Benetos, ISMIR 2022), github.com/cheriell/PM2S"
license = {text = "MIT"}
requires-python = ">=3.8"
dependencies = ["torch", "numpy", "pretty_midi", "mido", "pandas"]

[tool.setuptools]
packages = ["pm2s", "pm2s.features", "pm2s.io", "pm2s.models"]
TOML
  uv pip install --python "$PY" "$TMP/build"
fi

mkdir -p "$MODELS"
for name in RNNJointBeatModel RNNJointQuantisationModel RNNHandPartModel RNNKeySignatureModel \
            CNNTimeSignatureModel; do
  if [[ -s "$MODELS/$name.pth" ]]; then
    echo "already present: models/pm2s/$name.pth"
  else
    echo "downloading $name.pth ..."
    curl -fL --retry 3 -o "$MODELS/$name.pth.part" "$ZENODO/$name.pth?download=1"
    mv "$MODELS/$name.pth.part" "$MODELS/$name.pth"
  fi
done
