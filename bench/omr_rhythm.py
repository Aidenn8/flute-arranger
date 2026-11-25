"""Screenshots of piano music -> notes: pitch and rhythm accuracy, before and after the
rhythm repair (omr.repair_rhythm). Slow: oemer reads each page once (~20-60 s), cached.

    heat run .venv/bin/python -I bench/omr_rhythm.py            # render, read, score
    .venv/bin/python -I bench/omr_rhythm.py --score-only           # score the cached reads

Pages: the first bars of real piano scores, engraved by Verovio (sheetswap.render) and
turned into pictures two ways, as a user would bring them:
- "page": Quick Look's 2000 px picture of the engraved page (a clean scan);
- "shot": the same page shrunk to 1100 px wide and saved as JPEG (a screenshot).

Scores (16th and 32nd runs, triplets, dotted rhythms, a pickup):
  maple_leaf   Joplin, Maple Leaf Rag (music21 corpus), bars 1-12, 2/4, 16ths
  polonaise    Clara Schumann, Polonaise Op. 1 No. 1 (music21 corpus), bars 1-10, 3/4,
               16th triplets and 32nds
  cpe_bach     C. P. E. Bach, Sonata H. 186 (music21 corpus), bars 1-6, 4/4, 32nds
  fur_elise    Beethoven, Für Elise (Mutopia MIDI, samples/ref), bars 0-16, 3/8, pickup
  k545         Mozart, K. 545 i (Mutopia MIDI, samples/piano_tune), bars 1-12, 16th scales

Truth: the notes of the score that was engraved. Scored on the right hand's top line
(what the flute plays) after reading the OMR MusicXML with score_input.load_score:
- pitch: share of the truth's notes found, in order (edit-distance alignment);
- length: of those found, the share with the right written length;
- place: of those found, the share at the right place in the bar (onset modulo the bar);
- bars: share of the read bars whose notes add up to the time signature (every voice).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import warnings
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "samples" / "omr_rhythm"

PAGES = {
    "maple_leaf": ("corpus", "joplin/maple_leaf_rag.mxl", 1, 12),
    "polonaise": ("corpus", "schumann_clara/polonaise_op1n1.mxl", 1, 10),
    "cpe_bach": ("corpus", "cpebach/h186.mxl", 1, 6),
    "fur_elise": ("midi", "samples/ref/fur_elise/score/fur_Elise_WoO59.mid", 0, 16),
    "k545": ("midi", "samples/piano_tune/k545_1/score/K545-1.mid", 1, 12),
}
PICKUP = {"fur_elise": 0.5}          # quarters before bar 1 in the MIDI file


# ---------------------------------------------------------------- the engraved pages

def _midi_score(path: Path, pickup_q: float):
    """A Mutopia MIDI file as a two-staff piano score: each track one staff, notes
    struck together one chord, each held to the next onset at most (so one voice a
    staff), lengths on the 16th / 8th-triplet grid, and the pickup as a short first bar."""
    import pretty_midi
    from music21 import chord, clef, layout, meter, note, stream
    pm = pretty_midi.PrettyMIDI(str(path))
    res = pm.resolution
    ts = pm.time_signature_changes[0] if pm.time_signature_changes else None
    num, den = (ts.numerator, ts.denominator) if ts else (4, 4)
    bar = Fraction(4 * num, den)
    shift = (bar - Fraction(pickup_q).limit_denominator(48)) % bar
    staves = []
    for k, inst in enumerate(pm.instruments[:2]):
        onsets: dict[Fraction, list] = {}
        for n in inst.notes:
            s0 = Fraction(round(pm.time_to_tick(n.start) * 12 / res), 12)
            e0 = Fraction(round(pm.time_to_tick(n.end) * 12 / res), 12)
            if e0 - s0 < Fraction(1, 6):                   # grace notes: left out
                continue
            onsets.setdefault(s0, []).append((n.pitch, e0))
        ps = stream.PartStaff(id=f"P1-Staff{k + 1}")
        ps.append(clef.TrebleClef() if k == 0 else clef.BassClef())
        ps.append(meter.TimeSignature(f"{num}/{den}"))
        times = sorted(onsets)
        cursor = Fraction(0)
        for i, t in enumerate(times):
            t2 = t + shift
            if t2 > cursor:
                ps.append(note.Rest(quarterLength=t2 - cursor))
            nxt = times[i + 1] + shift if i + 1 < len(times) else None
            end = max(e for _, e in onsets[t]) + shift
            if nxt is not None:
                end = min(end, nxt)
            pitches = sorted({p for p, _ in onsets[t]})
            el = note.Note(pitches[0]) if len(pitches) == 1 else chord.Chord(pitches)
            el.quarterLength = end - t2
            ps.append(el)
            cursor = end
        ps.makeMeasures(inPlace=True)
        if shift:
            m1 = ps.getElementsByClass(stream.Measure)[0]
            lead = [r for r in m1.notesAndRests if r.offset < shift]
            for r in lead:
                m1.remove(r)
            for el in m1.notesAndRests:
                el.offset -= shift
            m1.paddingLeft = shift
        ps.makeNotation(inPlace=True)
        for m in ps.getElementsByClass(stream.Measure):     # only the naturals a reader needs
            altered: set[str] = set()
            for el in m.notesAndRests:
                for p in getattr(el, "pitches", ()):
                    acc = p.accidental
                    if acc is not None and acc.name == "natural" and p.step not in altered:
                        p.accidental = None
                    elif acc is not None and acc.name != "natural":
                        altered.add(p.step)
        staves.append(ps)
    out = stream.Score()
    for ps in staves:
        out.insert(0, ps)
    out.insert(0, layout.StaffGroup(staves, symbol="brace"))
    return out


def source_excerpt(name: str, folder: Path) -> Path:
    from music21 import corpus, converter
    kind, where, a, b = PAGES[name]
    xml = folder / "source.musicxml"
    if xml.exists():
        return xml
    folder.mkdir(parents=True, exist_ok=True)
    if kind == "corpus":
        sc = corpus.parse(where)
    else:
        mid = ROOT / where
        tmp = folder / "whole.musicxml"
        _midi_score(mid, PICKUP.get(name, 0.0)).write("musicxml", fp=str(tmp))
        sc = converter.parse(str(tmp))
    ex = sc.measures(a, b)
    ex.write("musicxml", fp=str(xml))
    return xml


def engrave(xml: Path, folder: Path) -> tuple[Path, Path]:
    """(2000 px PNG of the first page, 1100 px JPEG of it)."""
    from PIL import Image
    from sheetswap.render import render_svg
    png, jpg = folder / "page.png", folder / "shot.jpg"
    if png.exists() and jpg.exists():
        return png, jpg
    svg = render_svg(xml)[0]
    m = re.match(r'<svg width="(\d+)px" height="(\d+)px"', svg)
    w, h = int(m.group(1)), int(m.group(2))
    side = max(w, h)                    # Quick Look draws a square: the page at its top left
    svg = svg.replace(m.group(0), f'<svg width="700px" height="700px" '
                                  f'viewBox="0 0 {side} {side}"', 1)
    (folder / "page.svg").write_text(svg)
    subprocess.run(["qlmanage", "-t", "-s", "2000", "-o", str(folder), str(folder / "page.svg")],
                   check=True, capture_output=True, timeout=120)
    shutil.move(str(folder / "page.svg.png"), png)
    im = Image.open(png).convert("RGB")
    im.resize((1100, round(1100 * im.height / im.width)), Image.LANCZOS).save(jpg, quality=85)
    return png, jpg


def read_page(image: Path, out: Path) -> Path | None:
    """oemer's MusicXML for one picture, before any rhythm repair (cached)."""
    from sheetswap import omr
    raw = out / "raw.musicxml"
    if raw.exists():
        return raw
    out.mkdir(parents=True, exist_ok=True)
    try:
        made = omr.images_to_musicxml([image], out, name="raw.musicxml", repair=False)
    except ValueError as e:
        print(f"  {image.name}: {e}")
        return None
    return made


# ---------------------------------------------------------------- scoring

def _truth(xml: Path) -> tuple[list[tuple[int, Fraction, Fraction]], Fraction]:
    from sheetswap import score_input
    src = score_input.load_score(xml)
    rh = next(p for p in src.parts if p.staff in (0, 1))
    bar = Fraction(src.beats_per_bar * src.beat_ticks, 12)
    return [(q.pitch, Fraction(q.start, 12), Fraction(q.dur, 12)) for q in rh.melody], bar


def _align(truth: list[int], got: list[int]) -> list[tuple[int, int]]:
    """Index pairs of equal pitches on an edit-distance alignment."""
    n, m = len(truth), len(got)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (truth[i - 1] != got[j - 1]))
    pairs = []
    i, j = n, m
    while i and j:
        same = truth[i - 1] == got[j - 1]
        if d[i][j] == d[i - 1][j - 1] + (not same):
            if same:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def bars_that_add_up(xml: Path) -> float:
    """Share of the bars whose every voice fills the time signature exactly."""
    from music21 import converter, meter, stream
    sc = converter.parse(str(xml))
    ok = total = 0
    for part in sc.parts:
        ts = None
        ms = list(part.getElementsByClass(stream.Measure))
        for k, m in enumerate(ms):
            t = m.getElementsByClass(meter.TimeSignature)
            ts = t[0] if t else ts
            if ts is None or k in (0, len(ms) - 1):      # a pickup and a last bar may be short
                continue
            want = Fraction(ts.barDuration.quarterLength).limit_denominator(1000)
            voices = list(m.voices) or [m]
            fills = []
            for v in voices:
                end = Fraction(0)
                for n in v.notesAndRests:
                    end = max(end, Fraction(n.offset).limit_denominator(1000)
                              + Fraction(n.duration.quarterLength).limit_denominator(1000))
                if end > 0:
                    fills.append(end == want)
            total += 1
            ok += bool(fills) and all(fills)
    return ok / total if total else 0.0


def score_read(truth_xml: Path, read_xml: Path) -> dict:
    from sheetswap import score_input
    truth, bar = _truth(truth_xml)
    src = score_input.load_score(read_xml)
    rh = next((p for p in src.parts if p.staff in (0, 1)), src.parts[0])
    rbar = Fraction(src.beats_per_bar * src.beat_ticks, 12)
    got = [(q.pitch, Fraction(q.start, 12), Fraction(q.dur, 12)) for q in rh.melody]
    pairs = _align([p for p, _, _ in truth], [p for p, _, _ in got])
    n = len(pairs) or 1
    length = sum(truth[i][2] == got[j][2] for i, j in pairs) / n
    place = sum(truth[i][1] % bar == got[j][1] % rbar for i, j in pairs) / n
    return {"pitch": len(pairs) / len(truth), "length": length, "place": place,
            "bars": bars_that_add_up(read_xml), "meter": src.meter, "truth_notes": len(truth),
            "read_notes": len(got)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--variant", default="", help="read into read_<kind><variant>/ (a second reading)")
    a = ap.parse_args()
    from sheetswap import omr
    rows = []
    for name in a.pages or list(PAGES):
        folder = WORK / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            xml = source_excerpt(name, folder)
            png, jpg = engrave(xml, folder)
            for kind, image in (("page", png), ("shot", jpg)):
                out = folder / f"read_{kind}{a.variant}"
                raw = (out / "raw.musicxml") if a.score_only else read_page(image, out)
                if raw is None or not raw.exists():
                    continue
                fixed = out / "repaired.musicxml"
                shutil.copy(raw, fixed)
                omr.repair_rhythm(fixed)
                before = score_read(xml, raw)
                after = score_read(xml, fixed)
                rows.append({"page": name, "kind": kind, "before": before, "after": after})
                print(f"{name:11s} {kind}: pitch {before['pitch']:.2f}->{after['pitch']:.2f}  "
                      f"length {before['length']:.2f}->{after['length']:.2f}  "
                      f"place {before['place']:.2f}->{after['place']:.2f}  "
                      f"bars {before['bars']:.2f}->{after['bars']:.2f}  "
                      f"meter {before['meter']}->{after['meter']}", flush=True)
    if rows:
        for key in ("pitch", "length", "place", "bars"):
            b = sum(r["before"][key] for r in rows) / len(rows)
            f = sum(r["after"][key] for r in rows) / len(rows)
            print(f"mean {key}: {b:.3f} -> {f:.3f}")
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
