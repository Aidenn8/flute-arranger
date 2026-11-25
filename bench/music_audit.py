"""Musical audit: is the flute (and flute + piano) sheet music accurate, and does it sound good?

    heat run .venv/bin/python bench/music_audit.py              # real recordings + test music
    heat run .venv/bin/python bench/music_audit.py --real       # only the 5 real recordings
    heat run .venv/bin/python bench/music_audit.py --no-sound   # skip previews and audio comparison
    heat run .venv/bin/python bench/music_audit.py --look       # also rasterize page 1 of a few scores

Inputs are the cached analyses: out/<id>/analysis.json for the real recordings and
out/bench/<set>_<piece>/analysis.json for the test music (bench/run_bench.py makes those).
Arrangements are rendered fresh into out/audit/ (git-ignored), never into the song folders.

Per song and option set it measures:
1. Accuracy: the flute part against the published tune (real recordings, encoded
   below from the printed music) or the true soprano (test music, truth.json).
   Semi-global alignment on pitch classes (any octave; real songs may be in any key),
   then: tune notes recovered in order, intervals exactly right (catches octave
   jumps), the same written rhythm after one global tempo factor, and where the
   bar lines fall compared with the real music.
2. Harmony: flute notes on strong beats that are chord tones of the piano, harsh
   clashes (semitone / major 7th / minor 9th against a sounding piano note, tritone
   against the bass) weighted by length and beat, the same measured against the
   original accompaniment, chords in the key, bass motion, cadences at phrase ends,
   and whether the piano's chords and bass are the ones in the original.
3. Rhythm readability, read from the engraved MusicXML: ties, short notes, dots,
   syncopations, little rests inside phrases, tied stubs, accidentals, ledger lines.
4. Flute playability: range, leaps, longest stretch without a rest or breath mark
   (seconds at the printed tempo), fast runs.
5. Sound: the preview mp3 against the original recording (chroma, DTW and beat-grid
   aligned), whether the flute plays the pitch that is loudest in the original at
   that moment, flute vs piano loudness, clipping.
Results: out/audit/music_audit.json, and the tables between the AUTO markers in
bench/music_audit.md (the written findings around them are kept).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sheetswap import pipeline  # noqa: E402
from sheetswap.checks import lead_in_ticks  # noqa: E402
from sheetswap.config import FLUTE_COMFY, TICKS_PER_BEAT as T  # noqa: E402
from sheetswap.model import Analysis, ArrangeOptions, BeatGrid, QNote  # noqa: E402

OUT = ROOT / "out"
WORK = OUT / "audit"
MD = ROOT / "bench" / "music_audit.md"
GT = ROOT / "samples" / "gt"
BEGIN, END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"

# ---------------------------------------------------------------- note names

_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def midi(name: str) -> int:
    letter, rest, alter = name[0], name[1:], 0
    while rest and rest[0] in "#b":
        alter += 1 if rest[0] == "#" else -1
        rest = rest[1:]
    return 12 * (int(rest) + 1) + _PC[letter] + alter


def nm(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


# ---------------------------------------------------------------- the real tunes
# Encoded from the published music. Onsets and lengths are in `unit` beats from
# beat 1 of bar 1 (negative = pickup). None = rhythm not encoded (pitch only).

def _at(bpb):
    return lambda bar, beat: (bar - 1) * bpb + (beat - 1)


_s = _at(3)
SATIE = dict(  # Gymnopédie No. 1, D major, 3/4; 4-bar intro, tune enters on beat 2 of bar 5
    bpb=3, unit="quarter", key="D major", source="Satie 1888 (first edition), bars 5-19",
    notes=[("F#5", _s(5, 2), 1), ("A5", _s(5, 3), 1), ("G5", _s(6, 1), 1), ("F#5", _s(6, 2), 1),
           ("C#5", _s(6, 3), 1), ("B4", _s(7, 1), 1), ("C#5", _s(7, 2), 1), ("D5", _s(7, 3), 1),
           ("A4", _s(8, 1), 3), ("F#4", _s(9, 1), 12),
           ("F#5", _s(13, 2), 1), ("A5", _s(13, 3), 1), ("G5", _s(14, 1), 1), ("F#5", _s(14, 2), 1),
           ("C#5", _s(14, 3), 1), ("B4", _s(15, 1), 1), ("C#5", _s(15, 2), 1), ("D5", _s(15, 3), 1),
           ("A4", _s(16, 1), 3), ("C#5", _s(17, 1), 3), ("F#5", _s(18, 1), 3), ("E5", _s(19, 1), 3)])

ELISE = dict(  # Für Elise, A minor, 3/8 (unit = 8th); right-hand line incl. the broken chords
    bpb=3, unit="eighth", key="A minor", source="WoO 59, bars 0-8 (right hand)",
    notes=[("E5", -1, .5), ("D#5", -.5, .5),
           ("E5", 0, .5), ("D#5", .5, .5), ("E5", 1, .5), ("B4", 1.5, .5), ("D5", 2, .5), ("C5", 2.5, .5),
           ("A4", 3, 1), ("C4", 4.5, .5), ("E4", 5, .5), ("A4", 5.5, .5),
           ("B4", 6, 1), ("E4", 7.5, .5), ("G#4", 8, .5), ("B4", 8.5, .5),
           ("C5", 9, 1), ("E4", 10.5, .5), ("E5", 11, .5), ("D#5", 11.5, .5),
           ("E5", 12, .5), ("D#5", 12.5, .5), ("E5", 13, .5), ("B4", 13.5, .5), ("D5", 14, .5),
           ("C5", 14.5, .5),
           ("A4", 15, 1), ("C4", 16.5, .5), ("E4", 17, .5), ("A4", 17.5, .5),
           ("B4", 18, 1), ("E4", 19.5, .5), ("C5", 20, .5), ("B4", 20.5, .5),
           ("A4", 21, 1)])

CHOPIN = dict(  # Nocturne Op. 9 No. 2, E-flat major, 12/8 (unit = 8th); rhythm for bars 0-2 only
    bpb=12, unit="eighth", key="E-flat major", source="Op. 9 No. 2, bars 0-4 (right hand)",
    notes=[("Bb4", -1, 1),
           ("G5", 0, 4), ("F5", 4, 1), ("G5", 5, 1), ("F5", 6, 3), ("Eb5", 9, 2), ("Bb4", 11, 1),
           ("G5", 12, 2), ("C5", 14, 1), ("C6", 15, 2), ("G5", 17, 1), ("Bb5", 18, 3), ("Ab5", 21, 2),
           ("G5", 23, 1),
           ("F5", None, None), ("G5", None, None), ("D5", None, None), ("Eb5", None, None),
           ("C5", None, None), ("Bb4", None, None),
           ("D6", None, None), ("C6", None, None), ("Bb5", None, None), ("Ab5", None, None),
           ("G5", None, None), ("Ab5", None, None), ("C5", None, None), ("D5", None, None),
           ("Eb5", None, None)])

_a = _at(3)
_ANTHEM_PHRASE = [  # "O say can you see ... twilight's last gleaming", in C, 3/4
    ("C4", 0, 1), ("E4", 1, 1), ("G4", 2, 1), ("C5", 3, 2), ("E5", 5, .75), ("D5", 5.75, .25),
    ("C5", 6, 1), ("E4", 7, 1), ("F#4", 8, 1), ("G4", 9, 2), ("G4", 11, .5), ("G4", 11.5, .5),
    ("E5", 12, 1.5), ("D5", 13.5, .5), ("C5", 14, 1), ("B4", 15, 2), ("A4", 17, .75), ("B4", 17.75, .25),
    ("C5", 18, 1), ("C5", 19, 1), ("G4", 20, 1), ("E4", 21, 1), ("C4", 22, 1)]
ANTHEM = dict(  # sung in C here; the tune is the same in any key
    bpb=3, unit="quarter", key="C major (as sung)", source="Star-Spangled Banner, bars 0-16",
    notes=[("G4", -1, .75), ("E4", -.25, .25)] + _ANTHEM_PHRASE
    + [("G4", 23, .75), ("E4", 23.75, .25)] + [(n, o + 24, d) for n, o, d in _ANTHEM_PHRASE])

AMERICA = dict(  # Ward's "Materna", in C here (sung in A-flat), 4/4
    bpb=4, unit="quarter", key="C major (reference)", source="America the Beautiful, bars 0-16",
    notes=[("G4", -1, 1),
           ("G4", 0, 1.5), ("E4", 1.5, .5), ("E4", 2, 1), ("G4", 3, 1),
           ("G4", 4, 1.5), ("D4", 5.5, .5), ("D4", 6, 1), ("E4", 7, 1),
           ("F4", 8, 1), ("G4", 9, 1), ("A4", 10, 1), ("B4", 11, 1), ("G4", 12, 3), ("G4", 15, 1),
           ("G4", 16, 1.5), ("E4", 17.5, .5), ("E4", 18, 1), ("G4", 19, 1),
           ("G4", 20, 1.5), ("D4", 21.5, .5), ("D4", 22, 1), ("D5", 23, 1),
           ("C#5", 24, 1), ("D5", 25, 1), ("E5", 26, 1), ("B4", 27, 1), ("D5", 28, 3), ("G4", 31, 1),
           ("E5", 32, 1.5), ("E5", 33.5, .5), ("D5", 34, 1), ("C5", 35, 1),
           ("C5", 36, 1.5), ("B4", 37.5, .5), ("B4", 38, 1), ("C5", 39, 1),
           ("D5", 40, 1), ("B4", 41, 1), ("A4", 42, 1), ("G4", 43, 1), ("C5", 44, 3), ("C5", 47, 1),
           ("C5", 48, 1.5), ("A4", 49.5, .5), ("A4", 50, 1), ("C5", 51, 1),
           ("C5", 52, 1.5), ("G4", 53.5, .5), ("G4", 54, 1), ("G4", 55, 1),
           ("A4", 56, 1), ("C5", 57, 1), ("G4", 58, 1), ("D5", 59, 1), ("C5", 60, 3)])

# Option sets a user would pick. The first is the main one (rendered, listened to, looked at).
REAL = {
    "433f3ceb": dict(title="Satie, Gymnopédie No. 1", meter="3/4", tune=SATIE, transpose=[0],
                     opts=[dict(meter=3), dict(meter=3, simple=True)]),
    "a70b82d9": dict(title="Beethoven, Für Elise", meter="3/8", tune=ELISE, transpose=[0],
                     opts=[dict(meter=3), dict(meter=3, simple=True)]),
    "827a1e2d": dict(title="Chopin, Nocturne Op. 9 No. 2", meter="12/8", tune=CHOPIN, transpose=[0],
                     opts=[dict(), dict(simple=True)]),
    "886519f9": dict(title="Star-Spangled Banner (soprano + band)", meter="3/4", tune=ANTHEM,
                     transpose=list(range(12)),
                     opts=[dict(meter=3), dict(meter=3, simple=True), dict(meter=3, tempo=-1)]),
    "9469ce8f": dict(title="America the Beautiful (male voice + band)", meter="4/4", tune=AMERICA,
                     transpose=list(range(12)), opts=[dict(), dict(simple=True)]),
}
BENCH_SETS = {"A": "piano", "B": "piano + clarinet/violin", "C": "broken chords", "D": "sung + drums"}
LOOK = [("433f3ceb", 0, "duet"), ("a70b82d9", 0, "duet"), ("827a1e2d", 0, "duet"),
        ("886519f9", 1, "duet"), ("9469ce8f", 0, "solo"), ("A_bwv267", 0, "duet")]


def tune_notes(tune: dict) -> list[tuple[int, float | None, float | None]]:
    return [(midi(n), o, d) for n, o, d in tune["notes"]]


def bench_tune(piece: str) -> dict:
    meta = json.loads((GT / piece / "meta.json").read_text())
    bpm, fd = meta["bpm"], meta["first_downbeat_s"]
    rows = json.loads((GT / piece / "truth.json").read_text())
    notes = [(int(p), round((s - fd) * bpm / 60 * 4) / 4, round((e - s) * bpm / 60 * 4) / 4)
             for p, s, e in rows]
    return dict(bpb=meta["beats_per_bar"], unit="quarter", notes=notes, bpm=bpm,
                first_downbeat_s=fd, source="truth.json")


# ---------------------------------------------------------------- shared helpers

def arrange_grid(an: Analysis, opts: ArrangeOptions) -> BeatGrid:
    """The beat grid pipeline.arrange uses for these options (same steps, same order)."""
    grid = pipeline.rescale_tempo(an.grid, opts.tempo)
    bpb = opts.meter if opts.meter in (3, 4) else grid.beats_per_bar
    if bpb != grid.beats_per_bar or opts.barshift:
        first = grid.first_downbeat + opts.barshift % bpb
        starts = [n.start for ns in an.sources.values() for n in ns]
        if starts:
            first_note_beat = int(np.floor(grid.to_beats(min(starts)))) + grid.first_downbeat
            while first - bpb >= first_note_beat:
                first -= bpb
        grid = BeatGrid(grid.beats, bpb, first)
    return grid


class Where:
    """Printed bar.beat of a tick (bars counted as the score shows them)."""

    def __init__(self, arr):
        self.bar = arr.beats_per_bar * T
        self.lead = lead_in_ticks(arr)

    def __call__(self, tick: int) -> str:
        t = tick + self.lead
        beat = (t % self.bar) / T + 1
        return f"{t // self.bar + 1}.{beat:g}"

    def bar_no(self, tick: int) -> int:
        return (tick + self.lead) // self.bar + 1


def pct(a: float, b: float) -> float | None:
    return round(100.0 * a / b, 1) if b else None


def tonic_pc(fifths: int, mode: str) -> int:
    return (7 * fifths + (9 if mode == "minor" else 0)) % 12


def scale_pcs(fifths: int, mode: str) -> set[int]:
    t = tonic_pc(fifths, mode)
    steps = (0, 2, 3, 5, 7, 8, 9, 10, 11) if mode == "minor" else (0, 2, 4, 5, 7, 9, 11)
    return {(t + s) % 12 for s in steps}


# ---------------------------------------------------------------- 1. accuracy

MATCH, MISMATCH, GAP_TUNE, GAP_FLUTE = 2.0, -1.0, -1.0, -0.5
SCALES = (1 / 3, 0.5, 2 / 3, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)


def align(tune: list[int], flute: list[int], trans: int):
    """Semi-global alignment: the whole tune against any stretch of the flute part.
    Returns (score, ops) with ops ("match"|"sub", i, j), ("miss", i, None), ("extra", None, j)."""
    n, m = len(tune), len(flute)
    D = [[0.0] * (m + 1) for _ in range(n + 1)]
    P = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0], P[i][0] = D[i - 1][0] + GAP_TUNE, 1
    fpc = [p % 12 for p in flute]
    for i in range(1, n + 1):
        tp = (tune[i - 1] + trans) % 12
        Di, Dp, Pi = D[i], D[i - 1], P[i]
        for j in range(1, m + 1):
            diag = Dp[j - 1] + (MATCH if fpc[j - 1] == tp else MISMATCH)
            up = Dp[j] + GAP_TUNE
            left = Di[j - 1] + GAP_FLUTE
            if diag >= up and diag >= left:
                Di[j], Pi[j] = diag, 0
            elif up >= left:
                Di[j], Pi[j] = up, 1
            else:
                Di[j], Pi[j] = left, 2
    j = max(range(m + 1), key=lambda k: D[n][k])
    score, i, ops = D[n][j], n, []
    while i > 0:
        move = P[i][j] if j > 0 else 1
        if move == 0:
            ok = (tune[i - 1] + trans) % 12 == flute[j - 1] % 12
            ops.append(("match" if ok else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == 1:
            ops.append(("miss", i - 1, None))
            i -= 1
        else:
            ops.append(("extra", None, j - 1))
            j -= 1
    return score, ops[::-1]


def accuracy(tune: dict, arr, transpositions: list[int]) -> dict:
    notes = tune_notes(tune) if isinstance(tune["notes"][0][0], str) else tune["notes"]
    tp = [p for p, _, _ in notes]
    ons = [o for _, o, _ in notes]
    flute = sorted(arr.flute, key=lambda q: q.start)
    fp = [q.pitch for q in flute]
    where = Where(arr)
    if not flute:
        return dict(recovered=0.0, n_tune=len(tp))
    best = None
    for t in transpositions:
        sc, o = align(tp, fp, t)
        if best is None or sc > best[0]:
            best = (sc, t, o)
    score, trans, ops = best
    hits = {i: j for op, i, j in ops if op == "match"}
    subs = [(i, j) for op, i, j in ops if op == "sub"]
    miss = [i for op, i, j in ops if op == "miss"]
    extra = sum(1 for op, _, _ in ops if op == "extra")
    aligned_js = [j for _, _, j in ops if j is not None]

    # intervals between neighbouring tune notes that were both found
    pairs = [(i, hits[i], hits[i + 1]) for i in range(len(tp) - 1) if i in hits and i + 1 in hits]
    int_ok = sum(1 for i, j, k in pairs if fp[k] - fp[j] == tp[i + 1] - tp[i])
    octave = [(i, j, k) for i, j, k in pairs
              if fp[k] - fp[j] != tp[i + 1] - tp[i] and (fp[k] - fp[j] - tp[i + 1] + tp[i]) % 12 == 0]

    # rhythm: inter-onset times, one global tempo factor (flute beats per tune beat)
    rp = [(i, j, k) for i, j, k in pairs if ons[i] is not None and ons[i + 1] is not None
          and ons[i + 1] > ons[i]]
    ratios = [(flute[k].start - flute[j].start) / T / (ons[i + 1] - ons[i]) for i, j, k in rp]
    ratios = [r for r in ratios if r > 0]
    out_r = {}
    if ratios:
        raw = median(ratios)
        s = min(SCALES, key=lambda c: abs(math.log(c / raw)))
        dev = [abs((flute[k].start - flute[j].start) - s * (ons[i + 1] - ons[i]) * T) for i, j, k in rp]
        out_r = dict(scale_raw=round(raw, 2), scale=s,
                     rhythm_exact=pct(sum(d <= 1 for d in dev), len(dev)),
                     rhythm_16th=pct(sum(d <= 3 for d in dev), len(dev)), rhythm_pairs=len(dev))
        # bar lines: compare where tune notes sit in their bar with where the flute puts them
        bar = arr.beats_per_bar * T
        tune_bar = tune["bpb"] * s * T
        if abs(tune_bar - bar) < 1:
            offs = Counter((flute[j].start - round(s * ons[i] * T)) % bar
                           for i, j in hits.items() if ons[i] is not None)
            if offs:
                off, cnt = offs.most_common(1)[0]
                n_off = sum(offs.values())
                out_r.update(bar_offset_beats=round(off / T, 2),
                             bar_offset_share=pct(cnt, n_off),
                             bar_right_pct=pct(offs.get(0, 0), n_off),
                             bar_offsets={f"{o / T:+g}": pct(c, n_off) for o, c in offs.most_common(3)},
                             barshift_fix=(int(round(off / T)) % arr.beats_per_bar
                                           if off % T == 0 else None))
        else:
            out_r.update(meter_note=f"printed {arr.beats_per_bar}/4 bar = {bar / T / s:g} tune "
                                    f"{tune['unit']}s, real bar = {tune['bpb']}")

    def ex_sub(i, j):
        return f"bar {where(flute[j].start)}: {nm(tp[i] + trans)[:-1]} expected, {nm(fp[j])} written"

    def ex_miss(i):
        later = [hits[k] for k in range(i + 1, len(tp)) if k in hits]
        at = where(flute[later[0]].start) if later else "end"
        return f"{nm(tp[i] + trans)[:-1]} (tune note {i + 1}) missing before bar {at}"

    def ex_oct(i, j, k):
        return (f"bar {where(flute[k].start)}: {nm(fp[j])}->{nm(fp[k])} "
                f"({fp[k] - fp[j]:+d}) where the tune goes {tp[i + 1] - tp[i]:+d}")

    return dict(
        n_tune=len(tp), transpose=trans, recovered=pct(len(hits), len(tp)),
        wrong=len(subs), missing=len(miss), extra=extra,
        span=(where(flute[aligned_js[0]].start), where(flute[aligned_js[-1]].start)) if aligned_js else None,
        interval_exact=pct(int_ok, len(pairs)), octave_jumps=len(octave),
        examples_wrong=[ex_sub(i, j) for i, j in subs[:6]],
        examples_missing=[ex_miss(i) for i in miss[:6]],
        examples_octave=[ex_oct(*x) for x in octave[:4]],
        **out_r)


# ---------------------------------------------------------------- 2. harmony

def is_strong(tick: int, bpb: int) -> bool:
    pos = tick % (bpb * T)
    return pos == 0 or (bpb == 4 and pos == 2 * T)


def metric_weight(tick: int, bpb: int) -> float:
    if is_strong(tick, bpb):
        return 2.0
    return 1.5 if tick % T == 0 else 1.0


class Notes:
    """(pitch, start, end) in ticks, with vector queries."""

    def __init__(self, rows):
        rows = [r for r in rows if r[2] > r[1]]
        self.P = np.array([r[0] for r in rows], dtype=int)
        self.S = np.array([r[1] for r in rows], dtype=float)
        self.E = np.array([r[2] for r in rows], dtype=float)
        self.W = np.array([r[3] if len(r) > 3 else 1.0 for r in rows], dtype=float)

    def __len__(self):
        return len(self.P)

    def over(self, a, b):
        return np.nonzero((self.S < b) & (self.E > a))[0]

    def at(self, t):
        return np.nonzero((self.S <= t) & (self.E > t))[0]


def clash_profile(flute: list[QNote], acc: Notes, bpb: int, where: Where | None = None) -> dict:
    """Flute against an accompaniment: clashes (weighted) and chord tones on strong beats."""
    if not len(acc):
        return {}
    tot = harsh_w = tri_w = 0.0
    strong = strong_ct = strong_clash = 0
    examples = []
    for f in flute:
        idx = acc.over(f.start, f.end)
        if len(idx):
            cuts = {f.start, f.end}
            cuts.update(int(x) for x in acc.S[idx] if f.start < x < f.end)
            cuts.update(int(x) for x in acc.E[idx] if f.start < x < f.end)
            cuts = sorted(cuts)
            for x0, x1 in zip(cuts, cuts[1:]):
                live = idx[(acc.S[idx] < x1) & (acc.E[idx] > x0)]
                if not len(live):
                    continue
                w = (x1 - x0) * metric_weight(x0, bpb)
                tot += w
                d = (f.pitch - acc.P[live]) % 12
                harsh = bool(np.any((d == 1) | (d == 11)))
                tri = (f.pitch - acc.P[live].min()) % 12 == 6
                if harsh or tri:
                    harsh_w += w
                if tri:
                    tri_w += w
        if is_strong(f.start, bpb):
            live = acc.at(f.start)
            if len(live):
                strong += 1
                pcs = set(int(p) for p in acc.P[live] % 12)
                strong_ct += (f.pitch % 12) in pcs
                d = (f.pitch - acc.P[live]) % 12
                if np.any((d == 1) | (d == 11)):
                    strong_clash += 1
                    if where and len(examples) < 5:
                        bad = sorted(nm(int(p)) for p, dd in zip(acc.P[live], d) if dd in (1, 11))
                        examples.append(f"bar {where(f.start)}: flute {nm(f.pitch)} on "
                                        f"{'/'.join(bad)}")
    return dict(clash_pct=pct(harsh_w, tot), tritone_bass_pct=pct(tri_w, tot),
                strong_chord_tone_pct=pct(strong_ct, strong), strong_clash_pct=pct(strong_clash, strong),
                strong_notes=strong, examples_clash=examples)


@dataclass
class Group:
    start: int
    dur: int
    pcs: frozenset
    bass: int | None


def piano_groups(arr) -> list[Group]:
    rh, lh = defaultdict(list), defaultdict(list)
    for q in arr.rh:
        rh[q.start].append(q)
    for q in arr.lh:
        lh[q.start].append(q)
    out = []
    for s in sorted(set(rh) | set(lh)):
        ns = rh[s] + lh[s]
        bass = min(q.pitch for q in (lh[s] or rh[s]))
        out.append(Group(s, max(q.dur for q in ns), frozenset(q.pitch % 12 for q in ns), bass))
    return out


def triad_root(pcs, bass_pc):
    best = None
    for root in range(12):
        for qual, third in (("maj", 4), ("min", 3), ("dim", 3)):
            fifth = 6 if qual == "dim" else 7
            tri = {root, (root + third) % 12, (root + fifth) % 12}
            sc = (len(tri & pcs), root == bass_pc, qual != "dim")
            if best is None or sc > best[0]:
                best = (sc, root, qual)
    return best[1], best[2]


def harmony(arr, acc_orig: Notes, bass_orig: Notes, where: Where) -> dict:
    bpb = arr.beats_per_bar
    flute = sorted(arr.flute, key=lambda q: q.start)
    piano = Notes([(q.pitch, q.start, q.end) for q in arr.rh + arr.lh])
    out = dict(piano=clash_profile(flute, piano, bpb, where),
               original=clash_profile(flute, acc_orig, bpb))
    bar = bpb * T
    fbars = {f.start // bar for f in flute}
    pbars = {b for q in arr.rh + arr.lh for b in range(q.start // bar, (q.end - 1) // bar + 1)}
    out["flute_bars_without_piano_pct"] = pct(len(fbars - pbars), len(fbars))
    groups = piano_groups(arr)
    if not groups:
        return out
    key = scale_pcs(arr.key_fifths, arr.key_mode)
    tonic = tonic_pc(arr.key_fifths, arr.key_mode)
    tot = sum(g.dur for g in groups)
    out["chords_in_key_pct"] = pct(sum(g.dur for g in groups if g.pcs <= key), tot)
    bars = max(1, (max(g.start + g.dur for g in groups) - min(g.start for g in groups)) // (bpb * T))
    out["chord_onsets_per_bar"] = round(len(groups) / bars, 2)

    # bass motion
    seq = [g.bass for g in groups]
    moves = [(a, b) for a, b in zip(seq, seq[1:]) if a != b]
    cls = Counter()
    for a, b in moves:
        k = min((b - a) % 12, (a - b) % 12)
        cls["4th/5th" if k == 5 else "step" if k in (1, 2) else "3rd" if k in (3, 4) else "tritone"] += 1
    leaps7 = [(a, b) for a, b in moves if abs(b - a) >= 10]
    out["bass_moves"] = {k: pct(v, len(moves)) for k, v in cls.items()}
    out["bass_7th_leaps_pct"] = pct(len(leaps7), len(moves))
    out["bass_7th_leap_examples"] = [f"{nm(a)}->{nm(b)}" for a, b in leaps7[:4]]

    # cadences: chord under the last note of each phrase (2+ bars, ended by a beat of rest)
    ends, start = [], flute[0].start if flute else 0
    for i, f in enumerate(flute):
        last = i + 1 == len(flute)
        if last or flute[i + 1].start - f.end >= T:
            if f.end - start >= 2 * bpb * T:
                ends.append(f)
            if not last:
                start = flute[i + 1].start
    kinds = Counter()
    for f in ends:
        g = next((g for g in reversed(groups) if g.start <= f.start < g.start + g.dur), None)
        g = g or next((g for g in groups if f.start <= g.start < f.end), None)
        if g is None:
            kinds["no chord"] += 1
            continue
        root, _ = triad_root(set(g.pcs), g.bass % 12)
        rel = (root - tonic) % 12
        kinds["I" if rel == 0 else "V" if rel == 7 else "IV" if rel == 5 else "other"] += 1
    out["phrase_ends"] = sum(kinds.values())
    out["cadence_I_or_V_pct"] = pct(kinds["I"] + kinds["V"], sum(kinds.values()))
    out["cadences"] = dict(kinds)
    out["last_chord_tonic"] = (triad_root(set(groups[-1].pcs), groups[-1].bass % 12)[0] == tonic)
    out["last_flute_note_degree"] = (flute[-1].pitch - tonic) % 12 if flute else None

    # are they the original's chords? pitch classes and bass of the original under each chord
    if len(acc_orig):
        prec_w = bass_w = bass_n = 0.0
        for g in groups:
            idx = acc_orig.over(g.start, g.start + g.dur)
            if not len(idx):
                continue
            ov = np.minimum(acc_orig.E[idx], g.start + g.dur) - np.maximum(acc_orig.S[idx], g.start)
            chroma = np.zeros(12)
            np.add.at(chroma, acc_orig.P[idx] % 12, ov * acc_orig.W[idx])
            ref = {pc for pc in range(12) if chroma[pc] >= 0.15 * chroma.max()}
            prec_w += g.dur * len(g.pcs & ref) / len(g.pcs)
            b = bass_orig.over(g.start, g.start + T // 2) if len(bass_orig) else []
            if len(b):
                bass_n += g.dur
                bass_w += g.dur * (int(bass_orig.P[b].min()) % 12 == g.bass % 12)
        out["chord_pcs_in_original_pct"] = pct(prec_w, tot)
        out["bass_matches_original_pct"] = pct(bass_w, bass_n)
    return out


def original_accompaniment(an: Analysis, opts: ArrangeOptions, grid: BeatGrid):
    """(accompaniment, bass) of the recording in ticks, without the tune's own notes."""
    melody, used, _ = pipeline.pick_melody(an, opts.melody_source)
    pool, bass = pipeline._accompaniment_pool(an, used, melody)

    def rows(ns):
        return [(n.pitch, grid.to_ticks(n.start), grid.to_ticks(n.end), n.velocity / 127)
                for n in ns if n.end - n.start >= 0.1]
    acc = rows(list(pool) + list(bass or []))
    return Notes(acc), Notes(rows(bass) if bass else acc)


def truth_accompaniment(set_name: str, piece: str, grid: BeatGrid, truth_rows):
    import pretty_midi
    path = GT / piece / ("D_song.mid" if set_name == "D" else "full.mid")
    pm = pretty_midi.PrettyMIDI(str(path))
    allrows = [(n.pitch, grid.to_ticks(n.start), grid.to_ticks(n.end), 1.0)
               for i in pm.instruments if not i.is_drum for n in i.notes]
    sop = {(p, round(s, 1)) for p, s, _ in truth_rows}
    acc = [r for r in allrows if (r[0], round(grid.to_seconds(r[1] / T), 1)) not in sop]
    return Notes(acc), Notes(allrows)


# ---------------------------------------------------------------- 3. notation (from the MusicXML)

def _ledgers(dnn: int, bottom_dnn: int) -> int:
    step = dnn - bottom_dnn
    return (step - 8) // 2 if step > 9 else ((-step) // 2 if step < -1 else 0)


def notation(xml_path: Path, bpb: int, key_fifths: int) -> dict:
    from music21 import clef as m21clef, converter, pitch as m21pitch
    sc = converter.parse(str(xml_path))
    parts = list(sc.parts)
    bottoms = {"treble": m21pitch.Pitch("E4").diatonicNoteNum,
               "bass": m21pitch.Pitch("G2").diatonicNoteNum}
    out = {}
    for pi, part in enumerate(parts):
        measures = list(part.getElementsByClass("Measure"))
        cl = next(iter(part.recurse().getElementsByClass(m21clef.Clef)), None)
        bottom = bottoms["bass" if isinstance(cl, m21clef.BassClef) else "treble"]
        heads = led3 = 0
        pieces = []
        for mi, m in enumerate(measures):
            for el in m.notesAndRests:
                ql = float(el.duration.quarterLength)
                if ql <= 0:
                    continue
                pieces.append(dict(bar=mi, abs=mi * bpb + float(el.offset), ql=ql, rest=el.isRest,
                                   tie=None if el.isRest or el.tie is None else el.tie.type,
                                   dots=el.duration.dots, tup=bool(el.duration.tuplets)))
                if not el.isRest:
                    for p in el.pitches:
                        heads += 1
                        led3 += _ledgers(p.diatonicNoteNum, bottom) >= 3
        name = ["flute", "piano_rh", "piano_lh"][pi] if pi < 3 else f"part{pi}"
        cl_name = type(cl).__name__.replace("Clef", "").lower() if cl else "?"
        if pi > 0:
            out[name] = dict(clef=cl_name, ledger3_pct=pct(led3, heads))
            continue
        notes = [p for p in pieces if not p["rest"]]
        bars = len({p["bar"] for p in notes}) or 1
        chains, cur = [], None
        for p in notes:
            if p["tie"] in (None, "start") or cur is None:
                cur = dict(start=p["abs"], length=0.0, pieces=[])
                chains.append(cur)
            cur["length"] += p["ql"]
            cur["pieces"].append(p["ql"])
        sync = [c for c in chains if c["start"] % 1 > 1e-6
                and c["start"] + c["length"] > math.floor(c["start"]) + 1 + 1e-6]
        stubs = sum(1 for c in chains if len(c["pieces"]) > 1 for q in c["pieces"] if q <= 0.25 + 1e-6)
        seq = sorted(pieces, key=lambda p: p["abs"])
        choppy = sum(1 for a, r, b in zip(seq, seq[1:], seq[2:])
                     if r["rest"] and r["ql"] < 1 and not a["rest"] and not b["rest"])
        out["flute"] = dict(
            clef=cl_name, bars_with_notes=bars, written_notes=len(notes),
            notes_per_bar=round(len(notes) / bars, 2),
            ties_per_bar=round(sum(p["tie"] in ("start", "continue") for p in notes) / bars, 2),
            tied_notes_pct=pct(sum(len(c["pieces"]) > 1 for c in chains), len(chains)),
            shorter_than_8th_pct=pct(sum(p["ql"] < 0.5 - 1e-6 and not p["tup"] for p in notes), len(notes)),
            triplet_pct=pct(sum(p["tup"] for p in notes), len(notes)),
            dotted_pct=pct(sum(p["dots"] > 0 for p in notes), len(notes)),
            offbeat_across_beat_pct=pct(len(sync), len(chains)),
            tied_16th_stubs_per_bar=round(stubs / bars, 2),
            short_rests_in_phrase_per_bar=round(choppy / bars, 2),
            ledger3_pct=pct(led3, heads))
    # accidentals actually printed in the flute part
    root = ET.parse(xml_path).getroot()
    p1 = root.find("part")
    if p1 is not None and "flute" in out:
        acc = sum(1 for n in p1.iter("note") if n.find("accidental") is not None)
        out["flute"]["accidentals_per_bar"] = round(acc / out["flute"]["bars_with_notes"], 2)
    return out


# ---------------------------------------------------------------- 4. playability

def playability(arr, where: Where) -> dict:
    fl = sorted(arr.flute, key=lambda q: q.start)
    if not fl:
        return {}
    spt = 60.0 / max(arr.bpm, 1) / T
    total = sum(q.dur for q in fl)
    lo, hi = FLUTE_COMFY
    leaps = [(a, b) for a, b in zip(fl, fl[1:]) if abs(b.pitch - a.pitch) > 12 and b.start - a.end < T]
    minutes = max((fl[-1].end - fl[0].start) * spt / 60, 1e-6)
    stretches, s0 = [], fl[0]
    for a, b in zip(fl, fl[1:]):
        if b.start - a.end >= T // 2 or a.breath_after:
            stretches.append((s0.start, a.end))
            s0 = b
    stretches.append((s0.start, fl[-1].end))
    secs = [(e - s) * spt for s, e in stretches]
    worst = max(range(len(secs)), key=secs.__getitem__)
    iois = [(b.start - a.start) * spt for a, b in zip(fl, fl[1:])]
    runs, cur = [], 1
    for x in iois:
        if x < 0.15:
            cur += 1
        else:
            if cur >= 4:
                runs.append(cur)
            cur = 1
    if cur >= 4:
        runs.append(cur)
    return dict(
        low=nm(min(q.pitch for q in fl)), high=nm(max(q.pitch for q in fl)),
        comfy_time_pct=pct(sum(q.dur for q in fl if lo <= q.pitch <= hi), total),
        low_C4_Db4_pct=pct(sum(q.dur for q in fl if q.pitch < lo), total),
        above_A6_pct=pct(sum(q.dur for q in fl if q.pitch > hi), total),
        leaps_over_octave_per_min=round(len(leaps) / minutes, 2),
        leap_examples=[f"bar {where(b.start)}: {nm(a.pitch)}->{nm(b.pitch)}" for a, b in leaps[:4]],
        longest_breathless_s=round(secs[worst], 1),
        longest_breathless_at=f"bars {where(stretches[worst][0])}-{where(stretches[worst][1])}",
        stretches_over_10s=sum(s > 10 for s in secs), breath_marks=sum(q.breath_after for q in fl),
        fast_runs=len(runs), notes_faster_than_150ms_pct=pct(sum(x < 0.15 for x in iois), len(iois)),
        printed_bpm=arr.bpm)


# ---------------------------------------------------------------- 5. sound

SR, HOP = 22050, 2048


def _chroma(y):
    import librosa
    c = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    rms = librosa.feature.rms(y=y, frame_length=4096, hop_length=HOP)[0]
    n = min(c.shape[1], len(rms))
    c, rms = c[:, :n], rms[:n]
    loud = rms > rms.max() * 10 ** (-40 / 20)
    return c, loud


def _cos(a, b):
    na, nb = np.linalg.norm(a, axis=0), np.linalg.norm(b, axis=0)
    return np.sum(a * b, axis=0) / np.maximum(na * nb, 1e-9)


def _dtw_sim(A, B):
    import librosa
    A2, B2 = A[:, ::2], B[:, ::2]
    _, wp = librosa.sequence.dtw(X=A2, Y=B2, metric="cosine")
    return float(np.mean(_cos(A2[:, wp[:, 0]], B2[:, wp[:, 1]])))


def _part_midi(pm, program):
    import copy
    import pretty_midi
    out = copy.deepcopy(pm)
    out.instruments = [i for i in out.instruments if i.program == program]
    for inst in out.instruments:
        inst.control_changes.insert(0, pretty_midi.ControlChange(7, 127, 0.0))
    return out


def sound(res: dict, orig_wav: Path, arr, grid: BeatGrid, vocals_wav: Path | None) -> dict:
    import librosa
    import pretty_midi
    from sheetswap.config import FLUTE_PROGRAM, PIANO_PROGRAM, SOUNDFONT
    from sheetswap.synth import synthesize

    out = {}
    if res.get("mp3") is None:
        return dict(error="no preview")
    yp_st, _ = librosa.load(str(res["mp3"]), sr=SR, mono=False)
    out["peak_dbfs"] = round(20 * math.log10(max(float(np.abs(yp_st).max()), 1e-9)), 2)
    out["clipped_samples"] = int(np.sum(np.abs(yp_st) >= 0.999))
    yp = librosa.to_mono(yp_st)
    yo, _ = librosa.load(str(orig_wav), sr=SR)
    Cp, lp = _chroma(yp)
    Co, lo_ = _chroma(yo)
    out["dtw_chroma_sim"] = round(_dtw_sim(Cp, Co), 3)
    out["dtw_chroma_sim_null"] = round(_dtw_sim(Cp, Co[:, ::-1]), 3)

    # beat-grid alignment: preview time -> score tick -> original time
    spt = 60.0 / max(arr.bpm, 1) / T
    lead = lead_in_ticks(arr)
    tp = np.arange(Cp.shape[1]) * HOP / SR
    ticks = tp / spt - lead
    to = np.array([grid.to_seconds(t / T) for t in ticks])
    fo = np.round(to * SR / HOP).astype(int)
    ok = (fo >= 0) & (fo < Co.shape[1]) & lp
    ok[ok] &= lo_[fo[ok]]
    if ok.any():
        sims = _cos(Cp[:, ok], Co[:, fo[ok]])
        out["grid_chroma_sim"] = round(float(np.mean(sims)), 3)
        nulls = []
        for k in np.linspace(0.1, 0.9, 9):
            sh = np.roll(Co, int(k * Co.shape[1]), axis=1)
            nulls.append(float(np.mean(_cos(Cp[:, ok], sh[:, fo[ok]]))))
        out["grid_chroma_sim_null"] = round(float(np.mean(nulls)), 3)

    # does the flute play the pitch that stands out in the original right then?
    def tracking(C):
        Cn = C / np.maximum(C.max(axis=0, keepdims=True), 1e-9)
        w = top1 = top3 = sal = 0.0
        for q in arr.flute:
            a = int(grid.to_seconds(q.start / T) * SR / HOP)
            b = max(a + 1, int(grid.to_seconds(q.end / T) * SR / HOP))
            if a < 0 or b > Cn.shape[1]:
                continue
            m = Cn[:, a:b].mean(axis=1)
            order = np.argsort(-m)
            pc = q.pitch % 12
            w += q.dur
            top1 += q.dur * (order[0] == pc)
            top3 += q.dur * (pc in order[:3])
            sal += q.dur * m[pc]
        return dict(top1_pct=pct(top1, w), top3_pct=pct(top3, w), salience=round(sal / w, 3) if w else None)

    out["melody_vs_mix"] = tracking(Co)
    if vocals_wav and vocals_wav.exists():
        yv, _ = librosa.load(str(vocals_wav), sr=SR)
        Cv, _ = _chroma(yv)
        out["melody_vs_vocals"] = tracking(Cv)

    # loudness: flute alone and piano alone, same synth and volume as the preview
    pm = pretty_midi.PrettyMIDI(str(res["midi"]))
    if any(i.program == PIANO_PROGRAM for i in pm.instruments):
        lev = []
        for prog in (FLUTE_PROGRAM, PIANO_PROGRAM):
            a = synthesize(_part_midi(pm, prog), SOUNDFONT, SR)
            a = a.mean(axis=1) if a.ndim == 2 else a
            lev.append(librosa.feature.rms(y=a.astype(np.float32), frame_length=4096, hop_length=HOP)[0])
        n = min(len(lev[0]), len(lev[1]))
        f, p = lev[0][:n], lev[1][:n]
        both = (f > 1e-4) & (p > 1e-4)
        if both.any():
            d = 20 * np.log10(f[both] / p[both])
            out["flute_minus_piano_db"] = round(float(np.median(d)), 1)
            out["frames_piano_louder_pct"] = pct(int(np.sum(d < 0)), int(both.sum()))
    return out


# ---------------------------------------------------------------- running

def work_dir(name: str, src: Path) -> Path:
    d = WORK / name
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "analysis.json", d / "analysis.json")
    meta = src / "meta.json"
    if meta.exists():
        shutil.copy(meta, d / "meta.json")
    return d


def render(d: Path, opts: ArrangeOptions) -> dict:
    shutil.rmtree(d / "arr" / opts.key(), ignore_errors=True)   # always fresh
    return pipeline.arrange_and_render(d, opts)


def audit_one(label: str, an: Analysis, d: Path, o: dict, tune: dict, trans: list[int],
              acc_fn, do_sound: bool, orig_wav: Path, vocals: Path | None) -> dict:
    opts = ArrangeOptions(mode="duet", **o)
    grid = arrange_grid(an, opts)
    res = render(d, opts)
    arr = res["arr"]
    assert arr.beats_per_bar == grid.beats_per_bar and arr.bpm == round(grid.bpm()), "grid copy is stale"
    where = Where(arr)
    acc, bass = acc_fn(grid)
    row = dict(label=label, opts=o, key=f"{arr.key_fifths:+d} {arr.key_mode}", meter=f"{arr.beats_per_bar}/4",
               bpm=arr.bpm, flute_notes=len(arr.flute), warnings=arr.warnings,
               accuracy=accuracy(tune, arr, trans),
               harmony=harmony(arr, acc, bass, where),
               notation=notation(res["musicxml"], arr.beats_per_bar, arr.key_fifths),
               play=playability(arr, where),
               page1=str(d / "arr" / opts.key() / "page-1.svg"))
    if do_sound:
        row["sound"] = sound(res, orig_wav, arr, grid, vocals)
    return row


def run_real(ids, do_sound: bool) -> list[dict]:
    rows = []
    for sid in ids:
        spec = REAL[sid]
        src = OUT / sid
        an = Analysis.load(src / "analysis.json")
        d = work_dir(sid, src)
        vocals = src / "stems" / "vocals.wav" if an.kind == "song" else None
        for k, o in enumerate(spec["opts"]):
            opts = ArrangeOptions(mode="duet", **o)
            row = audit_one(spec["title"], an, d, o, spec["tune"], spec["transpose"],
                            lambda g, an=an, opts=opts: original_accompaniment(an, opts, g),
                            do_sound and k == 0, src / "audio.wav", vocals)
            row.update(id=sid, real_meter=spec["meter"], main=(k == 0))
            rows.append(row)
            print(f"{sid} {o}: tune {row['accuracy'].get('recovered')}% "
                  f"clash {row['harmony'].get('piano', {}).get('clash_pct')}%", flush=True)
    return rows


def run_bench(sets, do_sound: bool, limit: int = 0) -> list[dict]:
    index = json.loads((GT / "index.json").read_text())
    rows = []
    for s in sets:
        for piece in index[s][: limit or None]:
            src = OUT / "bench" / f"{s}_{piece}"
            if not (src / "analysis.json").exists():
                continue
            an = Analysis.load(src / "analysis.json")
            d = work_dir(f"bench/{s}_{piece}", src)
            tune = bench_tune(piece)
            truth_rows = json.loads((GT / piece / "truth.json").read_text())
            row = audit_one(f"{s}_{piece}", an, d, {}, tune, [0],
                            lambda g, s=s, piece=piece, tr=truth_rows: truth_accompaniment(s, piece, g, tr),
                            do_sound, src / "audio.wav", None)
            # meter and bar lines against the truth
            grid = arrange_grid(an, ArrangeOptions())
            bar_s = tune["bpb"] * 60 / tune["bpm"]
            dbs = [tune["first_downbeat_s"] + k * bar_s for k in range(int(an.duration / bar_s))]
            bar_t = grid.beats_per_bar * T
            on_bar = [abs(((grid.to_ticks(t) + bar_t / 2) % bar_t) - bar_t / 2) <= 1.5 for t in dbs]
            row.update(id=f"{s}_{piece}", set=s, real_meter=f"{tune['bpb']}/4",
                       tempo_ratio=round(row["bpm"] / tune["bpm"], 2),
                       true_downbeats_on_barlines_pct=pct(sum(on_bar), len(on_bar)))
            rows.append(row)
            print(f"{s}_{piece}: tune {row['accuracy'].get('recovered')}% "
                  f"clash {row['harmony'].get('piano', {}).get('clash_pct')}%", flush=True)
    return rows


def look(rows: list[dict], out_dir: Path) -> list[str]:
    """Page 1 of a few scores as PNG (qlmanage thumbnails; no window opens)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for ident, k, mode in LOOK:
        r = next((r for r in rows if r.get("id") == ident), None)
        if r is None:
            continue
        cand = [x for x in rows if x.get("id") == ident]
        r = cand[min(k, len(cand) - 1)]
        svg = Path(r["page1"])
        if mode == "solo":
            o = ArrangeOptions(mode="solo", **r["opts"])
            d = svg.parent.parent.parent
            res = render(d, o)
            svg = d / "arr" / o.key() / "page-1.svg"
            del res
        dst = out_dir / f"{ident}-{mode}-{k}.svg"
        shutil.copy(svg, dst)
        subprocess.run(["qlmanage", "-t", "-s", "1400", "-o", str(out_dir), str(dst)],
                       capture_output=True, timeout=120)
        png = out_dir / (dst.name + ".png")
        if png.exists():
            made.append(str(png))
    return made


# ---------------------------------------------------------------- report

def _g(d, *path, default="–"):
    for p in path:
        if not isinstance(d, dict) or d.get(p) is None:
            return default
        d = d[p]
    return d


def _opt(o: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in o.items()) or "defaults"


def tables(real: list[dict], bench: list[dict]) -> str:
    L = []
    if real:
        L += ["### Real recordings: accuracy against the published tune", "",
              "| song | options | printed | tune notes found | intervals exact | same rhythm (±16th) | tempo factor | bar lines | wrong / missing / extra |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in real:
            a = r["accuracy"]
            if a.get("bar_offsets"):
                bars = f"right for {a['bar_right_pct']}% of notes; " + ", ".join(
                    f"{k} beat: {v}%" for k, v in a["bar_offsets"].items() if k != "+0")
            else:
                bars = a.get("meter_note", "–")
            L.append(f"| {r['label']} | {_opt(r['opts'])} | {r['meter']} {r['bpm']} bpm | "
                     f"{a.get('recovered')}% of {a['n_tune']} | {_g(a, 'interval_exact')}% | "
                     f"{_g(a, 'rhythm_exact')}% ({_g(a, 'rhythm_16th')}%) | {_g(a, 'scale')} | {bars} | "
                     f"{a.get('wrong')} / {a.get('missing')} / {a.get('extra')} |")
        L += ["", "### Real recordings: harmony (flute + piano, main option set)", "",
              "| song | strong-beat flute notes in the piano chord | clashes vs piano | clashes vs original | tritone on bass | chords in key | bass 7th leaps | cadences I/V | chord notes in original | bass = original | flute bars with no piano |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in [r for r in real if r.get("main")]:
            h = r["harmony"]
            L.append(f"| {r['label']} | {_g(h, 'piano', 'strong_chord_tone_pct')}% | "
                     f"{_g(h, 'piano', 'clash_pct')}% | {_g(h, 'original', 'clash_pct')}% | "
                     f"{_g(h, 'piano', 'tritone_bass_pct')}% | {_g(h, 'chords_in_key_pct')}% | "
                     f"{_g(h, 'bass_7th_leaps_pct')}% | {_g(h, 'cadence_I_or_V_pct')}% of {_g(h, 'phrase_ends')} | "
                     f"{_g(h, 'chord_pcs_in_original_pct')}% | {_g(h, 'bass_matches_original_pct')}% | "
                     f"{_g(h, 'flute_bars_without_piano_pct')}% |")
        L += ["", "### Real recordings: reading and playing the flute part", "",
              "| song | options | notes/bar | ties/bar | tied 16th stubs/bar | < 8th | dotted | off-beat across beat | short rests in phrases/bar | accidentals/bar | range | longest without breath | leaps > 8ve/min |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in real:
            n, p = r["notation"].get("flute", {}), r["play"]
            L.append(f"| {r['label']} | {_opt(r['opts'])} | {_g(n, 'notes_per_bar')} | {_g(n, 'ties_per_bar')} | "
                     f"{_g(n, 'tied_16th_stubs_per_bar')} | {_g(n, 'shorter_than_8th_pct')}% | {_g(n, 'dotted_pct')}% | "
                     f"{_g(n, 'offbeat_across_beat_pct')}% | {_g(n, 'short_rests_in_phrase_per_bar')} | "
                     f"{_g(n, 'accidentals_per_bar')} | {p.get('low')}–{p.get('high')} | "
                     f"{p.get('longest_breathless_s')} s | {p.get('leaps_over_octave_per_min')} |")
        snd = [r for r in real if r.get("sound")]
        if snd:
            L += ["", "### Real recordings: how the preview sounds next to the original", "",
                  "| song | chroma sim, DTW (null) | chroma sim, beat grid (null) | flute pitch = loudest in original | flute pitch in top 3 | vs vocal stem top 1 / top 3 | flute − piano (frames piano louder) | peak / clipped |",
                  "|---|---|---|---|---|---|---|---|"]
            for r in snd:
                s = r["sound"]
                v = s.get("melody_vs_vocals")
                L.append(f"| {r['label']} | {_g(s, 'dtw_chroma_sim')} ({_g(s, 'dtw_chroma_sim_null')}) | "
                         f"{_g(s, 'grid_chroma_sim')} ({_g(s, 'grid_chroma_sim_null')}) | "
                         f"{_g(s, 'melody_vs_mix', 'top1_pct')}% | {_g(s, 'melody_vs_mix', 'top3_pct')}% | "
                         f"{(str(v['top1_pct']) + '% / ' + str(v['top3_pct']) + '%') if v else '–'} | "
                         f"{_g(s, 'flute_minus_piano_db')} dB ({_g(s, 'frames_piano_louder_pct')}%) | "
                         f"{_g(s, 'peak_dbfs')} dBFS / {_g(s, 'clipped_samples')} |")
        L += ["", "Examples (main option set):", ""]
        for r in [r for r in real if r.get("main")]:
            a, h, p = r["accuracy"], r["harmony"], r["play"]
            ex = a.get("examples_wrong", [])[:3] + a.get("examples_missing", [])[:2] + a.get("examples_octave", [])[:2]
            L.append(f"- **{r['label']}** (tune found from bar {a.get('span', ['?'])[0]}, transposed {a.get('transpose')}): "
                     + "; ".join(ex) + ". Clashes on strong beats: "
                     + ("; ".join(_g(h, "piano", "examples_clash", default=[])[:3]) or "none")
                     + f". Bass 7th leaps: {', '.join(h.get('bass_7th_leap_examples', [])) or 'none'}."
                     + f" Longest breathless stretch {p.get('longest_breathless_s')} s at {p.get('longest_breathless_at')}.")
    if bench:
        L += ["", "### Test music (true melody and true chords known), default options", "",
              "| set | pieces | tune notes found | intervals exact | same rhythm (±16th) | meter right | tempo right | true downbeats on bar lines | strong-beat flute notes in the piano chord | clashes vs piano | clashes vs real chords | chord notes in real chord | bass = real bass | ties/bar | < 8th | longest without breath | chroma sim grid (null) | flute − piano |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in BENCH_SETS:
            rs = [r for r in bench if r["set"] == s]
            if not rs:
                continue

            def m(f, digits=1):
                xs = [f(r) for r in rs]
                xs = [x for x in xs if isinstance(x, (int, float))]
                return round(float(np.mean(xs)), digits) if xs else "–"
            meter_ok = sum(r["meter"] == r["real_meter"] for r in rs)
            tempo_ok = sum(abs(r["tempo_ratio"] - 1) < 0.1 for r in rs)
            snd = m(lambda r: _g(r, "sound", "grid_chroma_sim", default=None), 3)
            null = m(lambda r: _g(r, "sound", "grid_chroma_sim_null", default=None), 3)
            L.append(f"| {s} ({BENCH_SETS[s]}) | {len(rs)} | {m(lambda r: r['accuracy'].get('recovered'))}% | "
                     f"{m(lambda r: r['accuracy'].get('interval_exact'))}% | "
                     f"{m(lambda r: r['accuracy'].get('rhythm_exact'))}% ({m(lambda r: r['accuracy'].get('rhythm_16th'))}%) | "
                     f"{meter_ok}/{len(rs)} | {tempo_ok}/{len(rs)} | {m(lambda r: r['true_downbeats_on_barlines_pct'])}% | "
                     f"{m(lambda r: _g(r, 'harmony', 'piano', 'strong_chord_tone_pct', default=None))}% | "
                     f"{m(lambda r: _g(r, 'harmony', 'piano', 'clash_pct', default=None))}% | "
                     f"{m(lambda r: _g(r, 'harmony', 'original', 'clash_pct', default=None))}% | "
                     f"{m(lambda r: _g(r, 'harmony', 'chord_pcs_in_original_pct', default=None))}% | "
                     f"{m(lambda r: _g(r, 'harmony', 'bass_matches_original_pct', default=None))}% | "
                     f"{m(lambda r: _g(r, 'notation', 'flute', 'ties_per_bar', default=None))} | "
                     f"{m(lambda r: _g(r, 'notation', 'flute', 'shorter_than_8th_pct', default=None))}% | "
                     f"{m(lambda r: r['play'].get('longest_breathless_s'))} s | {snd} ({null}) | "
                     f"{m(lambda r: _g(r, 'sound', 'flute_minus_piano_db', default=None))} dB |")
        L += ["", "<details><summary>Per piece</summary>", "",
              "| piece | printed | real | tune found | intervals | rhythm ±16th | downbeats on bar lines | clashes piano / real | bass = real | ties/bar |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in bench:
            a, h = r["accuracy"], r["harmony"]
            L.append(f"| {r['id']} | {r['meter']} {r['bpm']} | {r['real_meter']} x{r['tempo_ratio']} | "
                     f"{a.get('recovered')}% | {_g(a, 'interval_exact')}% | {_g(a, 'rhythm_16th')}% | "
                     f"{r['true_downbeats_on_barlines_pct']}% | {_g(h, 'piano', 'clash_pct')}% / "
                     f"{_g(h, 'original', 'clash_pct')}% | {_g(h, 'bass_matches_original_pct')}% | "
                     f"{_g(r, 'notation', 'flute', 'ties_per_bar')} |")
        L += ["", "</details>"]
    return "\n".join(L) + "\n"


def write_md(text: str) -> None:
    body = f"{BEGIN}\n{text}{END}"
    if MD.exists():
        old = MD.read_text()
        if BEGIN in old and END in old:
            MD.write_text(old[:old.index(BEGIN)] + body + old[old.index(END) + len(END):])
            return
    MD.write_text("# Music audit\n\n" + body + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="only the real recordings")
    ap.add_argument("--bench", action="store_true", help="only the test music")
    ap.add_argument("--sets", default="ABCD")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-sound", action="store_true")
    ap.add_argument("--look", action="store_true")
    ap.add_argument("--songs", nargs="*", default=list(REAL))
    ap.add_argument("--report", action="store_true", help="only rebuild the tables from the last run")
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    if a.report:
        prev = json.loads((WORK / "music_audit.json").read_text())
        text = tables(prev["real"], prev["bench"])
        write_md(text)
        print(text)
        return
    try:                                   # a partial run keeps the other half of the last run
        prev = json.loads((WORK / "music_audit.json").read_text())
    except (OSError, ValueError):
        prev = dict(real=[], bench=[])
    real = prev.get("real", []) if a.bench else run_real(a.songs, not a.no_sound)
    if not a.bench and set(a.songs) != set(REAL):
        real += [r for r in prev.get("real", []) if r.get("id") not in a.songs]
    bench = prev.get("bench", []) if a.real else run_bench(list(a.sets), not a.no_sound, a.limit)
    (WORK / "music_audit.json").write_text(json.dumps(dict(real=real, bench=bench), indent=1, default=str))
    if a.look:
        for p in look(real + bench, WORK / "look"):
            print("page 1:", p)
    text = tables(real, bench)
    write_md(text)
    print(text)


if __name__ == "__main__":
    main()
