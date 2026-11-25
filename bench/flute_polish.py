"""How playable and well placed is the flute part made from a score? Measured.

    .venv/bin/python -I bench/flute_polish.py

For each score (the piano and violin pieces of samples/ref and samples/piano_tune, read
from their MIDI files, and piano and song scores of the music21 corpus, read from
MusicXML with their dynamics), the flute part the app writes with default options
(score_input.arrange_score), measured against docs/notation_rules.md:
- low: share of the part's note time in the low register below G4, soft and easily
  covered by a piano (R-FLUTE-1);
- top: share at A6 and above (hard to play softly, R-FLUTE-1/2);
- loud-low / soft-high: notes marked f or louder below G4, or p or softer at A6 and
  above (R-FLUTE-2);
- leaps: fast notes (an 8th or shorter) leaping more than an octave (R-FLUTE-5);
- moves: phrases moved by an octave against the phrase before (a cost of the above);
- breath: the longest stretch without a rest or breath mark, in seconds (R-BREATH-2),
  and breath marks per minute.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from sheetswap import score_input  # noqa: E402
from sheetswap.model import ArrangeOptions  # noqa: E402

CORPUS = ["joplin/maple_leaf_rag.mxl", "schumann_clara/polonaise_op1n1.mxl", "cpebach/h186.mxl",
          "schubert/Lindenbaum.xml", "handel/rinaldo/Lascia_chio_pianga.mxl",
          "schumann_robert/dichterliebe_no2.xml", "beach/prayer_of_a_tired_child.musicxml"]
LOUD = {"f", "ff", "fff", "ffff", "sf", "sfz", "fz"}
SOFT = {"p", "pp", "ppp", "pppp"}


def sources():
    import piano_tune as pt
    out = []
    for slug in pt.OLD + list(pt.PIECES):
        out.append((slug, pt.midi_path(slug)))
    for slug in ["menuet_bwv1006", "gavotte_bwv1006", "allemanda_bwv1004", "air_bwv1068"]:
        p = ROOT / "samples" / "ref" / slug / "ref.json"
        if p.exists():
            ref = json.loads(p.read_text())
            out.append((slug, p.parent / ref["source"].split(", tracks ")[0]))
    return out


def corpus_sources():
    from music21 import corpus
    out = []
    folder = ROOT / "samples" / "flute_polish"
    folder.mkdir(parents=True, exist_ok=True)
    for name in CORPUS:
        f = folder / (name.replace("/", "_").rsplit(".", 1)[0] + ".musicxml")
        if not f.exists():
            corpus.parse(name).write("musicxml", fp=str(f))
        out.append((name.split("/")[-1].split(".")[0], f))
    return out


def measure(arr, src) -> dict:
    line = sorted(arr.flute, key=lambda q: q.start)
    total = sum(q.dur for q in line) or 1
    dyn = sorted((m.start, m.value) for m in arr.expression if m.kind == "dynamic")

    def level(t):
        cur = ""
        for s, v in dyn:
            if s <= t:
                cur = v
        return cur

    low = sum(q.dur for q in line if q.pitch < 67) / total
    top = sum(q.dur for q in line if q.pitch >= 93) / total
    loud_low = sum(1 for q in line if q.pitch < 67 and level(q.start) in LOUD)
    soft_high = sum(1 for q in line if q.pitch >= 93 and level(q.start) in SOFT)
    leaps = sum(1 for a, b in zip(line, line[1:])
                if b.start - a.end < 3 and max(a.dur, b.dur) <= 6 and abs(b.pitch - a.pitch) > 12)
    sec = 60.0 / arr.bpm / arr.beat_ticks          # seconds a tick
    longest, run_start, breaths = 0.0, None, 0
    prev_end = None
    for q in line:
        if run_start is None or (prev_end is not None and q.start - prev_end >= 3):
            run_start = q.start
        longest = max(longest, (q.end - run_start) * sec)
        if q.breath_after:
            breaths += 1
            run_start = None
        prev_end = q.end
    minutes = max(1e-6, (line[-1].end - line[0].start) * sec / 60) if line else 1
    return {"low": low, "top": top, "loud_low": loud_low, "soft_high": soft_high, "leaps": leaps,
            "breath_s": longest, "breaths_per_min": breaths / minutes, "notes": len(line),
            "moves": _octave_changes(line, src)}


def _octave_changes(line, src) -> int:
    """Places where the flute's octave against the original changes between notes."""
    part = src.parts[src.auto_part]
    orig = {q.start: q.pitch for q in part.melody}
    offs = [(q.pitch - orig[q.start]) // 12 for q in line if q.start in orig]
    return sum(a != b for a, b in zip(offs, offs[1:]))


def main() -> None:
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name, path in sources() + corpus_sources():
            src = score_input.load_score(path)
            arr, _, _ = score_input.arrange_score(src, ArrangeOptions())
            r = measure(arr, src)
            r["piece"] = name
            rows.append(r)
            print(f"{name:22s} low {r['low']:.2f} top {r['top']:.2f} loud-low {r['loud_low']:3d} "
                  f"soft-high {r['soft_high']:3d} leaps {r['leaps']:3d} moves {r['moves']:2d} "
                  f"longest {r['breath_s']:5.1f} s  breaths/min {r['breaths_per_min']:.1f}")
    n = len(rows)
    print(f"mean low {sum(r['low'] for r in rows) / n:.3f}  top {sum(r['top'] for r in rows) / n:.3f}  "
          f"loud-low {sum(r['loud_low'] for r in rows)}  soft-high {sum(r['soft_high'] for r in rows)}  "
          f"leaps {sum(r['leaps'] for r in rows)}  moves {sum(r['moves'] for r in rows)}  "
          f"over 10 s {sum(r['breath_s'] > 10 for r in rows)}/{n}")


if __name__ == "__main__":
    main()
