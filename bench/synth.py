"""The synth lives in sheetswap/synth.py; this keeps `from bench.synth import ...` working."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sheetswap.synth import render_midi, synthesize, write_wav  # noqa: E402,F401
