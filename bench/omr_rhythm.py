"""Screenshots of piano music -> notes: pitch and rhythm accuracy, before and after the
rhythm repair (omr.repair_rhythm), for each sheet-music reader (omr.ENGINES). Slow: each
page is read once per reader (homr ~10 s, Audiveris ~15 s, oemer ~40 s), cached.

Measured 2026-11-25 (M2 CPU, means after repair; 10 = the page/shot images, 14 = all cases):
                   pitch  precision length place  bars  meter  s/page
  homr       10    0.99   0.97      0.94   0.90   0.97  1.00   11
             14    0.99   0.92      0.94   0.91   0.98  1.00   11   (chorale: 4 staves as 1 line)
  audiveris  10    0.92   1.00      0.90   0.83   0.98  1.00   15
             14    0.86   0.97      0.84   0.78   0.98  1.00   16   (photo: pitch 0.10)
  oemer      10    0.88   0.93      0.72   0.63   0.90  0.90   38
             14    0.87   0.83      0.73   0.65   0.92  0.86   58   (quartet, chorale: one line)
  chain      10    0.99   0.97      0.94   0.90   0.97  1.00   11   homr, else Audiveris, else oemer
             14    0.99   0.97      0.95   0.93   0.98  1.00   15   (chorale -> Audiveris: all 1.00)

    heat run .venv/bin/python -I bench/omr_rhythm.py                    # the default chain
    heat run .venv/bin/python -I bench/omr_rhythm.py --engine oemer     # one reader only
    .venv/bin/python -I bench/omr_rhythm.py --engine homr --score-only  # score cached reads

Pages: the first bars of real piano scores, engraved by Verovio (sheetswap.render) and
turned into pictures the ways a user would bring them:
- "page": Quick Look's 2000 px picture of the engraved page (a clean scan);
- "shot": the same page shrunk to 1100 px wide and saved as JPEG (a screenshot);
- "pdf": a 2-page PDF, Maple Leaf Rag bars 1-12 on page 1 and 13-24 on page 2;
- "photo": K. 545's page as a phone photo (on a desk, tilted and in perspective,
  unevenly lit, a little blurred and noisy, JPEG).

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
- precision: share of the notes read that are the truth's (a choir's four staves read
  one after another as one line find every soprano note, but 4x the notes);
- length: of those found, the share with the right written length;
- place: of those found, the share at the right place in the bar (onset modulo the bar);
- bars: share of the read bars whose notes add up to the time signature (every voice);
- meter: whether the time signature read is the score's;
- secs: seconds the reader took (the whole picture or PDF, page clean-up included).
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
TWO_PAGES = ("maple_leaf", 12, 24)   # the PDF case: bars 1-12 on page 1, 13-24 on page 2
PHOTO = "k545"                       # the phone-photo case
# more than two staves a system (scored on the top part: violin I, the soprano)
MANY_STAVES = {
    "quartet": ("corpus", "haydn/opus74no1/movement3.mxl", 1, 12),   # string quartet, 4 staves
    "chorale": ("corpus", "bach/bwv66.6.mxl", 1, 8),                 # SATB on 4 staves
}
SOURCES = {**PAGES, **MANY_STAVES}
BASE = [(name, kind) for name in PAGES for kind in ("page", "shot")]     # "the 10 images"
CASES = BASE + [(TWO_PAGES[0], "pdf"), (PHOTO, "photo")] + [(n, "page") for n in MANY_STAVES]


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


def source_excerpt(name: str, folder: Path, bars: tuple[int, int] | None = None) -> Path:
    from music21 import corpus, converter
    kind, where, a, b = SOURCES[name]
    a, b = bars or (a, b)
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


def two_page_pdf() -> tuple[Path, Path]:
    """(truth for bars 1-24, a PDF of two engraved pages: bars 1-12 and 13-24)."""
    from PIL import Image
    name, split, last = TWO_PAGES
    folder = WORK / f"{name}_2pages"
    truth = source_excerpt(name, folder, (PAGES[name][2], last))
    pdf = folder / "score.pdf"
    if pdf.exists():
        return truth, pdf
    first = engrave(source_excerpt(name, WORK / name), WORK / name)[0]
    second_dir = folder / "page2"
    second = engrave(source_excerpt(name, second_dir, (split + 1, last)), second_dir)[0]
    pages = [Image.open(p).convert("RGB") for p in (first, second)]
    pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=200)
    return truth, pdf


def phone_photo(png: Path, out: Path, seed: int = 7) -> Path:
    """The engraved page as a phone photographs it: the paper on a darker desk, turned
    2.5 degrees and in perspective, lit unevenly, a little out of focus, with sensor
    noise and a warm cast, 1600 px, JPEG."""
    import cv2
    import numpy as np
    if out.exists():
        return out
    rng = np.random.default_rng(seed)
    page = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    ys, xs = np.nonzero(page < 200)
    m = 90
    paper = page[max(0, ys.min() - m):ys.max() + m, max(0, xs.min() - m):xs.max() + m]
    h, w = paper.shape
    H, W = int(h * 1.35), int(w * 1.25)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    jitter = rng.uniform(-0.035, 0.035, (4, 2)) * [w, h]
    dst = src + [(W - w) / 2, (H - h) / 2] + jitter
    a = np.deg2rad(2.5)
    centre = dst.mean(axis=0)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    dst = ((dst - centre) @ rot.T + centre).astype(np.float32)
    warp = cv2.getPerspectiveTransform(src, dst)
    img = cv2.warpPerspective(paper, warp, (W, H), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=95).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    light = 1.0 - 0.38 * (xx / W) * (0.4 + 0.6 * yy / H)          # darker towards the lower right
    light *= 1.0 - 0.18 * (((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2) * 4
    img = cv2.GaussianBlur(img * light, (0, 0), 1.1)
    img += rng.normal(0, 5, img.shape)
    bgr = np.stack([img * 0.86, img * 0.95, img * 1.0], axis=-1)    # warm indoor light
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    scale = 1600 / max(H, W)
    bgr = cv2.resize(bgr, (round(W * scale), round(H * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out


def read_page(image: Path, out: Path, engine: str | None = None) -> Path | None:
    """The reader's MusicXML for one picture or PDF, before any rhythm repair (cached),
    and the seconds it took in out/seconds.txt."""
    import time
    from sheetswap import omr
    raw = out / "raw.musicxml"
    if raw.exists():
        return raw
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        made = omr.images_to_musicxml([image], out, name="raw.musicxml", repair=False,
                                      engine=engine)
    except ValueError as e:
        print(f"  {image.name}: {e}")
        return None
    (out / "seconds.txt").write_text(f"{time.time() - t0:.1f}\n")
    return made


# ---------------------------------------------------------------- scoring

def _truth(xml: Path) -> tuple[list[tuple[int, Fraction, Fraction]], Fraction, str]:
    from sheetswap import score_input
    from sheetswap.config import TICKS_PER_BEAT as T
    src = score_input.load_score(xml)
    rh = next(p for p in src.parts if p.staff in (0, 1))
    bar = Fraction(src.beats_per_bar * src.beat_ticks, T)
    return [(q.pitch, Fraction(q.start, T), Fraction(q.dur, T)) for q in rh.melody], bar, src.meter


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
    from sheetswap.config import TICKS_PER_BEAT as T
    truth, bar, meter = _truth(truth_xml)
    src = score_input.load_score(read_xml)
    rh = next((p for p in src.parts if p.staff in (0, 1)), src.parts[0])
    rbar = Fraction(src.beats_per_bar * src.beat_ticks, T)
    got = [(q.pitch, Fraction(q.start, T), Fraction(q.dur, T)) for q in rh.melody]
    pairs = _align([p for p, _, _ in truth], [p for p, _, _ in got])
    n = len(pairs) or 1
    length = sum(truth[i][2] == got[j][2] for i, j in pairs) / n
    place = sum(truth[i][1] % bar == got[j][1] % rbar for i, j in pairs) / n
    return {"pitch": len(pairs) / len(truth), "precision": len(pairs) / max(1, len(got)),
            "length": length, "place": place, "bars": bars_that_add_up(read_xml), "meter": src.meter,
            "meter_ok": float(src.meter == meter), "truth_notes": len(truth),
            "read_notes": len(got)}


FAILED = {"pitch": 0.0, "precision": 0.0, "length": 0.0, "place": 0.0, "bars": 0.0, "meter": "-",
          "meter_ok": 0.0}
KEYS = ("pitch", "precision", "length", "place", "bars", "meter_ok")


def case_input(name: str, kind: str) -> tuple[Path, Path]:
    """(truth MusicXML, the picture or PDF to read) for one case."""
    folder = WORK / name
    xml = source_excerpt(name, folder)
    png, jpg = engrave(xml, folder)
    if kind == "pdf":
        return two_page_pdf()
    if kind == "photo":
        return xml, phone_photo(png, folder / "photo.jpg")
    return xml, png if kind == "page" else jpg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*", help="page names (all if none)")
    ap.add_argument("--engine", default=None,
                    help="one reader (audiveris, homr, oemer); default: omr.py's chain")
    ap.add_argument("--kinds", default="page,shot,pdf,photo")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--variant", default="", help="read into read_<kind>_<engine><variant>/")
    a = ap.parse_args()
    from sheetswap import omr
    kinds = a.kinds.split(",")
    tag = a.engine or "auto"
    rows = []
    for name, kind in CASES:
        if (a.pages and name not in a.pages) or kind not in kinds:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            xml, image = case_input(name, kind)
            out = image.parent / f"read_{kind}_{tag}{a.variant}"
            raw = (out / "raw.musicxml") if a.score_only else read_page(image, out, a.engine)
            secs = float((out / "seconds.txt").read_text()) if (out / "seconds.txt").exists() else 0.0
            if raw is None or not raw.exists():
                rows.append({"page": name, "kind": kind, "before": FAILED, "after": FAILED,
                             "secs": secs, "failed": True})
                print(f"{name:11s} {kind:5s}: no reading", flush=True)
                continue
            fixed = out / "repaired.musicxml"
            shutil.copy(raw, fixed)
            omr.repair_rhythm(fixed)
            before = score_read(xml, raw)
            after = score_read(xml, fixed)
        rows.append({"page": name, "kind": kind, "before": before, "after": after, "secs": secs})
        print(f"{name:11s} {kind:5s}: pitch {before['pitch']:.2f}->{after['pitch']:.2f}  "
              f"prec {before['precision']:.2f}->{after['precision']:.2f}  "
              f"length {before['length']:.2f}->{after['length']:.2f}  "
              f"place {before['place']:.2f}->{after['place']:.2f}  "
              f"bars {before['bars']:.2f}->{after['bars']:.2f}  "
              f"meter {before['meter']}->{after['meter']}  {secs:.0f} s", flush=True)
    base = [r for r in rows if (r["page"], r["kind"]) in BASE]
    for label, some in ((f"{tag}, the {len(base)} page/shot images", base),
                        (f"{tag}, all {len(rows)} cases", rows)):
        if not some or (some is rows and len(rows) == len(base)):
            continue
        print(f"{label} (a picture it couldn't read counts 0):")
        for key in KEYS:
            b = sum(r["before"][key] for r in some) / len(some)
            f = sum(r["after"][key] for r in some) / len(some)
            print(f"  mean {key}: {b:.3f} -> {f:.3f}")
        print(f"  mean secs: {sum(r['secs'] for r in some) / len(some):.1f}")
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
