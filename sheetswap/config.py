"""Constants shared by every step."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"
MODELS_DIR = ROOT / "models"
SOUNDFONT = MODELS_DIR / "MuseScore_General.sf2"

TICKS_PER_BEAT = 12          # 16th = 3, 8th triplet = 4, 8th = 6
FLUTE_HARD = (60, 96)        # C4..C7, never outside
FLUTE_COMFY = (62, 93)       # D4..A6, where it sounds best
FLUTE_PROGRAM = 73           # General MIDI flute
PIANO_PROGRAM = 0
MAX_SECONDS = 720            # longest upload we accept
PORT = 5230
