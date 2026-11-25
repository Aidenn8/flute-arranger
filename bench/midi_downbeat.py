"""Where does bar 1 start in a MIDI file written by a notation program? Measured.

    .venv/bin/python -I bench/midi_downbeat.py            # all sources
    .venv/bin/python -I bench/midi_downbeat.py --show     # also list the misses

A MIDI file has no bar lines: score_input._read_midi infers the first downbeat (and so
the pickup) from where the notes fall. Wrong guesses shift every bar line of the flute
part (a half bar in 4/4: notes tied across every bar line, the cadences on beat 3).

Sources, each with the true pickup:
- the piano and violin pieces of samples/ref and samples/piano_tune (Mutopia MIDI files,
  written by LilyPond; their pickups from the LilyPond source, bench/make_refs.py and
  bench/piano_tune.py);
- MusicXML scores of the music21 corpus (Bach chorales, Haydn and Mozart quartets, piano pieces,
  songs) written to MIDI by music21, which starts the file at the pickup; the truth is
  the first bar's length when it is shorter than a full bar. Written once into
  samples/midi_downbeat/ (git-ignored), with index.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from sheetswap import score_input  # noqa: E402

def mutopia_cases() -> list[tuple[str, Path, float]]:
    import piano_tune as pt
    out = []
    for slug in pt.OLD + list(pt.PIECES):
        ref = pt.load_ref(slug)
        out.append((slug, pt.midi_path(slug), float(ref["pickup_beats"])))
    for slug in ["menuet_bwv1006", "gavotte_bwv1006", "allemanda_bwv1004", "air_bwv1068"]:
        p = ROOT / "samples" / "ref" / slug / "ref.json"
        if p.exists():
            ref = json.loads(p.read_text())
            path = p.parent / ref["source"].split(", tracks ")[0]
            out.append((slug, path, float(ref["pickup_beats"])))
    return out


CACHE = ROOT / "samples" / "midi_downbeat"      # the corpus scores written to MIDI, kept


def corpus_cases(n_chorales: int, work: Path = CACHE) -> list[tuple[str, Path, float]]:
    from music21 import corpus, stream
    index = work / "index.json"
    if index.exists():
        rows = json.loads(index.read_text())
        return [(n, work / f, t) for n, f, t in rows][: len(rows)]
    work.mkdir(parents=True, exist_ok=True)
    names = [p for p in corpus.getComposer("bach") if str(p).endswith(".mxl")][:n_chorales]
    for comp in ("haydn", "mozart", "joplin", "schumann_clara", "schumann_robert",
                 "schubert", "handel", "verdi", "beach", "liliuokalani", "johnson_j_r", "cpebach",
                 "corelli", "weber"):
        names += [p for p in corpus.getComposer(comp) if str(p).endswith((".mxl", ".xml", ".musicxml"))]
    out = []
    for p in names:
        if Path(str(p)).stat().st_size > 200_000:          # the big quartet movements: slow to parse
            continue
        try:
            sc = corpus.parse(p)
        except Exception:                       # music21 raises many kinds
            continue
        if not isinstance(sc, stream.Score) or not sc.parts:
            continue
        sigs = list(sc.recurse().getElementsByClass("TimeSignature"))
        if not sigs:
            continue
        bar = sigs[0].barDuration.quarterLength
        ms = list(sc.parts[0].getElementsByClass(stream.Measure))
        if len(ms) < 4:
            continue
        first = ms[0].duration.quarterLength
        pickup = float(first) if first < bar else 0.0
        name = "_".join(Path(str(p)).parts[-2:]).replace(".", "_")
        f = work / f"{name}.mid"
        try:
            sc.write("midi", fp=str(f))
        except Exception:                       # music21 raises many kinds
            continue
        out.append((name, f, pickup))
    index.write_text(json.dumps([(n, f.name, t) for n, f, t in out], indent=0))
    return out


def inferred_pickup(path: Path) -> tuple[float | None, float]:
    """(our pickup in quarters, bar length in quarters), from the file's first note."""
    import pretty_midi
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        src = score_input.load_notated_midi(path) or score_input.load_score(path)
        pm = pretty_midi.PrettyMIDI(str(path))
    first = min(pm.time_to_tick(n.start) for i in pm.instruments for n in i.notes if not i.is_drum)
    first_q = first / pm.resolution
    ours = min(q.start for p in src.parts for q in p.notes) / 12      # our tick of that note
    bar = src.beats_per_bar * src.beat_ticks / 12
    return first_q - ours, bar


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chorales", type=int, default=120)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mutopia = mutopia_cases()
        cases = mutopia + corpus_cases(a.chorales)
    from_mutopia = {name for name, _, _ in mutopia}
    groups: dict[str, list[bool]] = {}
    misses = []
    for name, path, truth in cases:
        try:
            got, bar = inferred_pickup(path)
        except Exception as e:                  # music21 wrote a file pretty_midi can't read: left out
            misses.append((name, truth, f"left out: {e!r}"))
            continue
        ok = abs((got - truth) % bar) < 1e-6 or abs((got - truth) % bar - bar) < 1e-6
        group = ("Mutopia (LilyPond)" if name in from_mutopia
                 else "Bach chorales" if name.startswith("bach_") else "other corpus")
        groups.setdefault(group, []).append(ok)
        if not ok:
            misses.append((name, truth, got))
    total = [x for v in groups.values() for x in v]
    for g, v in sorted(groups.items()):
        print(f"{g:20s} {sum(v):4d}/{len(v):4d} right")
    print(f"{'all':20s} {sum(total):4d}/{len(total):4d} right ({sum(total) / len(total):.1%})")
    if a.show:
        for m in misses:
            print("  miss:", m)


if __name__ == "__main__":
    main()
