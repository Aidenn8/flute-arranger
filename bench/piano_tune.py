"""Which notes of a piano score are the tune? Measured against hand-checked melodies.

    .venv/bin/python -I bench/piano_tune.py               # fetch what's missing, write refs, score
    .venv/bin/python -I bench/piano_tune.py --no-fetch    # score only
    .venv/bin/python -I bench/piano_tune.py --show happy_farmer [--bars 24]

Two sets of pieces, each read the way the app reads an uploaded MIDI file written by a
notation program (score_input.load_score), and the tune taken from the part the app
picks by default ("Which part is the tune?" left on auto):

- the five piano pieces of bench/make_refs.py (samples/ref/<piece>/ref.json, the right
  hand's top line with hand fixes, repeats as the performer plays them);
- seven more piano pieces from the Mutopia Project (samples/piano_tune/<piece>/), picked
  because the tune is somewhere other than the right hand's top line for part of the
  piece, or as controls where it is the right hand's top line throughout. Their
  references are written out below, per stretch of the score, as which staff carries
  the tune, checked against the LilyPond source (voice by voice) and the printed score:

  happy_farmer   Schumann Op. 68 No. 10: tune in the left hand under right-hand
                 after-beat chords (bars 1-8, 13-14, 19-20), in the right hand's top
                 voice in bars 9-12 and 15-18 (the left hand doubles it a 10th lower).
  wild_rider     Schumann Op. 68 No. 8: a broken-chord tune in the right hand
                 (bars 1-16, the repeat written out, and 25-32), in the left hand under
                 right-hand chords in bars 17-24.
  chopin_prelude_6  Op. 28 No. 6: the "cello" tune in the left hand under repeated
                 right-hand 8ths and chords; the right hand answers in bar 7.
  moonlight_1    Beethoven Op. 27 No. 2, i (bars 1-59): the tune is the separate top
                 voice above the right hand's triplets (LilyPond's "topmain"); bars 1-4
                 and 32-36 have no tune; bar 31's tune notes are in the left hand.
  k545_1         Mozart K. 545, i, exposition (bars 1-28): right hand (scales included)
                 over Alberti and broken-chord left hand; bar 13 is the left hand's
                 one-bar lead-in (no tune).
  old_french_song  Tchaikovsky Op. 39 No. 16: right hand throughout (control).
  chopin_prelude_4  Op. 28 No. 4: right hand over repeated left-hand chords (control).

Score per piece: notes of the reference found with the same pitch and the same onset
(recall), notes picked that are in the reference (precision), and their F1; and the
same with octaves ignored (pitch class), since the flute part moves octaves anyway.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
import warnings
import zipfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sheetswap import score_input  # noqa: E402

REF = ROOT / "samples" / "ref"
OWN = ROOT / "samples" / "piano_tune"
MUTOPIA = "https://www.mutopiaproject.org/ftp/"
MAX_BYTES = 5 * 1024 * 1024
GRID = 48
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

OLD = ["fur_elise", "gymnopedie_1", "nocturne_op9_2", "traumerei", "clair_de_lune"]

# tune: (from, to, track) in quarter notes from the MIDI file's start: the tune is the top
# line of that track's notes starting in [from, to) (to None = the end).
# keep_dur: only notes of these lengths (quarters, +-0.02) are tune notes; skip_dur: none
# of these. add: extra (pitch, onset, length) tune notes. upto: the reference stops here.
PIECES: dict[str, dict] = {
    "happy_farmer": dict(
        title="Schumann: Fröhlicher Landmann, Op. 68 No. 10", meter=(4, 4), pickup=0.5,
        files=["SchumannR/O68/schumann-op68-10-gai-laboureur/schumann-op68-10-gai-laboureur.mid",
               "SchumannR/O68/schumann-op68-10-gai-laboureur/schumann-op68-10-gai-laboureur.ly"],
        # each hand's phrase starts with its own 8th upbeat (bar 8 beat 4.5: the right
        # hand's C4 under the slur into bar 9; bar 12 beat 4.5: the left hand's A3)
        tune=[(0, 32.0, 1), (32.0, 48.0, 0), (48.0, 56.0, 1), (56.0, 72.0, 0), (72.0, None, 1)]),
    "wild_rider": dict(
        title="Schumann: Wilder Reiter, Op. 68 No. 8", meter=(6, 8), pickup=0.5,
        files=["SchumannR/O68/schumann-op68-08-cavalier-sauvage/schumann-op68-08-cavalier-sauvage.mid",
               "SchumannR/O68/schumann-op68-08-cavalier-sauvage/schumann-op68-08-cavalier-sauvage.ly"],
        # the MIDI plays bars 1-8 twice; the left hand's upbeat C3 at bar 16 beat 2.5
        tune=[(0, 48.0, 0), (48.0, 72.0, 1), (72.0, None, 0)]),
    "chopin_prelude_6": dict(
        title="Chopin: Prelude in B minor, Op. 28 No. 6", meter=(3, 4), pickup=0.0,
        files=["ChopinFF/O28/Chop-28-6/Chop-28-6.mid", "ChopinFF/O28/Chop-28-6/Chop-28-6-lys.zip"],
        tune=[(0, 18.0, 1), (18.0, 21.0, 0), (21.0, None, 1)]),
    "moonlight_1": dict(
        title="Beethoven: Sonata Op. 27 No. 2, i (bars 1-59)", meter=(2, 2), pickup=0.0,
        files=["BeethovenLv/O27/moonlight/moonlight-mids.zip", "BeethovenLv/O27/moonlight/moonlight-lys.zip"],
        midi="moonlight1.mid",
        # the triplet 8ths (1/3 quarter) are the accompaniment; bar 31 (quarters 120-124)
        # has the tune's C#4 E4 C#4 in the left-hand staff, bar 23 its F#3; bar 37's A3 is a
        # triplet value
        tune=[(0, 120.0, 0), (120.0, 124.0, 1), (124.0, None, 0)],
        skip_dur=[0.331], keep_from_1=[1.0], add=[(54, 88.0, 1.0), (57, 146.0, 1 / 3)], upto=236.0),
    "k545_1": dict(
        title="Mozart: Sonata K. 545, i (bars 1-28)", meter=(4, 4), pickup=0.0,
        files=["MozartWA/KV545/K545-1/K545-1.mid", "MozartWA/KV545/K545-1/K545-1.ly"],
        tune=[(0, 112.0, 0)], upto=112.0),
    "old_french_song": dict(
        title="Tchaikovsky: Old French Song, Op. 39 No. 16", meter=(2, 4), pickup=0.5,
        files=["TchaikovskyPI/O39/16OldFrenchSong/16OldFrenchSong.mid",
               "TchaikovskyPI/O39/16OldFrenchSong/16OldFrenchSong.ly"],
        tune=[(0, None, 0)]),
    "chopin_prelude_4": dict(
        title="Chopin: Prelude in E minor, Op. 28 No. 4", meter=(2, 2), pickup=1.0,
        files=["ChopinFF/O28/Chop-28-4/Chop-28-4.mid", "ChopinFF/O28/Chop-28-4/Chop-28-4.ly"],
        tune=[(0, None, 0)]),
}


# ---------------------------------------------------------------- fetching (data only)

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sheet-music-bench/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{url}: too large")
    return data


def fetch(slug: str, spec: dict) -> None:
    folder = OWN / slug / "score"
    if (folder / "source.json").exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for path in spec["files"]:
        data = _get(MUTOPIA + path)
        time.sleep(1.0)
        if path.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = Path(info.filename).name          # flat: never a path from the zip
                    if info.is_dir() or not name.endswith((".mid", ".ly", ".ily")):
                        continue
                    if info.file_size > MAX_BYTES:
                        continue
                    (folder / name).write_bytes(z.read(info))
                    names.append(name)
        else:
            (folder / Path(path).name).write_bytes(data)
            names.append(Path(path).name)
    (folder / "source.json").write_text(json.dumps(
        {"site": "Mutopia Project", "files": spec["files"], "saved": names,
         "license": "see the LilyPond header (Mutopia: public domain or Creative Commons)"},
        indent=1, ensure_ascii=False))


def midi_path(slug: str) -> Path:
    if slug in OLD:
        ref = json.loads((REF / slug / "ref.json").read_text())
        return REF / slug / ref["source"].split(", tracks ")[0]
    folder = OWN / slug / "score"
    name = PIECES[slug].get("midi")
    return folder / name if name else sorted(folder.glob("*.mid"))[0]


# ---------------------------------------------------------------- the references

def _midi_rows(path: Path) -> list[list]:
    """[pitch, onset, end, track] in quarters from the file's start, graces left out
    (as bench/make_refs.py: off the 48-a-quarter grid and shorter than 0.12)."""
    import pretty_midi
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pm = pretty_midi.PrettyMIDI(str(path))
    res = pm.resolution
    rows = []
    for k, inst in enumerate(pm.instruments):
        for n in inst.notes:
            s = pm.time_to_tick(n.start) / res
            e = pm.time_to_tick(n.end) / res
            on = abs(s * GRID - round(s * GRID)) < 0.02
            if not on and e - s < 0.12:
                continue
            rows.append([n.pitch, s, e, k])
    rows.sort(key=lambda r: (r[1], -r[0]))
    return rows


def _skyline(rows: list[list]) -> list[list]:
    out = []
    i = 0
    while i < len(rows):
        t = rows[i][1]
        group = [r for r in rows[i:] if abs(r[1] - t) < 1e-6]
        i += len(group)
        top = max(group, key=lambda r: r[0])
        if not any(r[1] < t - 1e-6 and r[2] > t + 1e-6 and r[0] >= top[0] for r in rows):
            out.append(list(top))
    for a, b in zip(out, out[1:]):
        a[2] = min(a[2], b[1])
    return out


def build_ref(slug: str) -> dict:
    spec = PIECES[slug]
    rows = _midi_rows(midi_path(slug))
    picked = []
    for a, b, track in spec["tune"]:
        b = 1e9 if b is None else b
        mine = [r for r in rows if r[3] == track and a - 1e-6 <= r[1] < b - 1e-6]
        if "skip_dur" in spec:
            keep = spec.get("keep_from_1") if track == 1 else None
            if keep:
                mine = [r for r in mine if any(abs(r[2] - r[1] - d) < 0.02 for d in keep) and r[0] >= 60]
            else:
                mine = [r for r in mine if not any(abs(r[2] - r[1] - d) < 0.02 for d in spec["skip_dur"])]
        picked += mine
    for p, s, d in spec.get("add", []):
        picked.append([p, s, s + d, -1])
    picked.sort(key=lambda r: (r[1], -r[0]))
    line = _skyline(picked)
    upto = spec.get("upto")
    if upto is not None:
        line = [r for r in line if r[1] < upto - 1e-6]
    pick = spec["pickup"]
    num, den = spec["meter"]
    return {"piece": slug, "title": spec["title"], "meter": [num, den], "bar_beats": num * 4 / den,
            "pickup_beats": pick, "form": [[0.0, None]], "upto": upto,
            "source": f"score/{midi_path(slug).name}",
            "notes": [{"pitch": int(p), "onset_beats": round(s - pick, 4), "dur_beats": round(e - s, 4)}
                      for p, s, e, _ in line if e > s]}


def load_ref(slug: str) -> dict:
    folder = REF if slug in OLD else OWN
    return json.loads((folder / slug / "ref.json").read_text())


# ---------------------------------------------------------------- what the app picks

def app_melody(slug: str) -> tuple[list[tuple[int, Fraction, Fraction]], str]:
    """The app's default tune as (pitch, onset, end) in quarters from the MIDI file's
    start, and the name of the part it came from."""
    path = midi_path(slug)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        src = score_input.load_score(path)
    rows = _midi_rows(path)
    first = Fraction(min(r[1] for r in rows)).limit_denominator(GRID * 10)
    ours = min(q.start for p in src.parts for q in p.notes)
    d0 = first - Fraction(ours, 12)
    part = src.parts[src.auto_part]
    return [(q.pitch, Fraction(q.start, 12) + d0, Fraction(q.end, 12) + d0) for q in part.melody], part.name


def unfold(line, form, pickup):
    end_all = max((e for _, _, e in line), default=Fraction(0))
    out, cursor = [], Fraction(0)
    for a, b in form:
        a = Fraction(a).limit_denominator(1000)
        b = end_all if b is None else Fraction(b).limit_denominator(1000)
        out += [(p, cursor + s - a, cursor + min(e, b) - a) for p, s, e in line if a <= s < b]
        cursor += b - a
    pick = Fraction(pickup).limit_denominator(1000)
    return [(p, s - pick, e - pick) for p, s, e in out]


def score_piece(slug: str) -> dict:
    ref = load_ref(slug)
    line, part = app_melody(slug)
    got = unfold(line, ref["form"], ref["pickup_beats"])
    upto = ref.get("upto")
    if upto is not None:
        got = [g for g in got if g[1] + Fraction(ref["pickup_beats"]).limit_denominator(1000) < upto - 1e-6]
    want = {(n["pitch"], round(n["onset_beats"] * GRID)) for n in ref["notes"]}
    have = {(p, round(float(s) * GRID)) for p, s, _ in got}
    want_pc = {(p % 12, t) for p, t in want}
    have_pc = {(p % 12, t) for p, t in have}

    def f1(w, h):
        hit = len(w & h)
        r = hit / len(w) if w else 0.0
        pr = hit / len(h) if h else 0.0
        return r, pr, (2 * r * pr / (r + pr) if r + pr else 0.0)

    r, pr, f = f1(want, have)
    _, _, fpc = f1(want_pc, have_pc)
    return {"piece": slug, "part": part, "ref_notes": len(want), "picked": len(have),
            "recall": r, "precision": pr, "f1": f, "f1_pc": fpc}


# ---------------------------------------------------------------- printing

def show(ref: dict, bars: int, start: int = 1) -> None:
    bar = ref["bar_beats"]
    rows: dict[int, list[str]] = {}
    for n in ref["notes"]:
        b = int((n["onset_beats"] + 1e-6) // bar) + 1
        if start <= b < start + bars:
            name = f"{NAMES[n['pitch'] % 12]}{n['pitch'] // 12 - 1}"
            rows.setdefault(b, []).append(f"{name}@{n['onset_beats'] - (b - 1) * bar:g}+{n['dur_beats']:g}")
    print(f"{ref['piece']}: {ref['meter'][0]}/{ref['meter'][1]}, {len(ref['notes'])} notes")
    for b in sorted(rows):
        print(f"  bar {b:3d}: " + " ".join(rows[b]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pieces", nargs="*")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--bars", type=int, default=16)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    todo = a.pieces or (OLD + list(PIECES))
    for slug in todo:
        if slug in PIECES:
            if not a.no_fetch:
                fetch(slug, PIECES[slug])
            ref = build_ref(slug)
            (OWN / slug / "ref.json").write_text(json.dumps(ref, indent=1, ensure_ascii=False))
    if a.show:
        for slug in todo:
            show(load_ref(slug), a.bars, a.start)
        return
    results = [score_piece(slug) for slug in todo]
    print(f"{'piece':18s} {'part':32s} {'ref':>5s} {'got':>5s} {'recall':>7s} {'prec':>6s} {'F1':>6s} {'F1 pc':>6s}")
    for r in results:
        print(f"{r['piece']:18s} {r['part'][:32]:32s} {r['ref_notes']:5d} {r['picked']:5d} "
              f"{r['recall']:7.3f} {r['precision']:6.3f} {r['f1']:6.3f} {r['f1_pc']:6.3f}")
    mean = sum(r["f1"] for r in results) / len(results)
    print(f"mean F1 {mean:.3f}")
    if a.json:
        a.json.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
