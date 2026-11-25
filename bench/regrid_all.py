"""Find the beats again (Beat This!) for every analysed song under out/ (audio only, no transcription).

    .venv/bin/python bench/regrid_all.py               # everything except out/ref (pass --ref to include)
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sheetswap import pipeline  # noqa: E402
from sheetswap.model import Analysis  # noqa: E402

include_ref = "--ref" in sys.argv
dirs = sorted(p.parent for p in (ROOT / "out").glob("**/analysis.json"))
for d in dirs:
    if "ref" in d.parts and not include_ref:
        continue
    if Analysis.load(d / "analysis.json").kind == "midi" or not (d / "audio.wav").exists():
        continue
    t = time.time()
    an = pipeline.regrid(d)
    for arr in (d / "arr").glob("*"):                  # renders made on the old grid
        for f in arr.glob("*"):
            f.unlink()
        arr.rmdir()
    g = an.grid
    print(f"{d.relative_to(ROOT / 'out')}: {g.beats_per_bar} beats/bar, {g.bpm():.0f} BPM, "
          f"{g.tracker} ({time.time() - t:.1f}s)", flush=True)
