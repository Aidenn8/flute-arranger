#!/bin/sh
# Arrange every real recording in samples/real as flute + piano (needs the models).
cd "$(dirname "$0")/.."
for f in samples/real/*/*.*; do
  case "$f" in *.json) continue ;; esac
  echo "=== $f"
  .venv/bin/python -m sheetswap.cli "$f" --mode duet 2>&1 | grep -v -E "WARNING:root|warn"
done
