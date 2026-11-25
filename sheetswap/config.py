"""Constants shared by every step."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"
MODELS_DIR = ROOT / "models"
SOUNDFONT = MODELS_DIR / "MuseScore_General.sf2"

TICKS_PER_BEAT = 48          # a quarter: 8th = 24, 16th = 12, 32nd = 6,
                             # 8th triplet = 16, 16th triplet = 8, 32nd triplet = 4
OLD_TICKS_PER_BEAT = 12      # the grid of caches written before 2026-11-25 (analysis.json, score.json)
FLUTE_HARD = (60, 96)        # C4..C7, never outside
FLUTE_COMFY = (62, 93)       # D4..A6, where it sounds best
FLUTE_PROGRAM = 73           # General MIDI flute
PIANO_PROGRAM = 0
MAX_SECONDS = 720            # longest upload we accept
PORT = 5230
