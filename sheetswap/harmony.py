"""Key and chord detection from transcribed notes.

Key: Krumhansl-Kessler profiles correlated with a weighted pitch-class histogram.

Chords: one chord per beat, chosen for the whole piece at once by dynamic
programming (Viterbi), so the harmonic rhythm follows the music: Bach may
change chord every beat, Satie once a bar. Each beat scores every chord of
the vocabulary (major, minor, dominant 7th, major 7th, minor 7th,
diminished, and sus4 when it is clearly there) on
- the accompaniment: how much of its duration x velocity chroma the chord's
  tones explain, minus what they leave out and the tones they lack;
- the tune: flute notes (strong beats most) should be chord tones, and a
  chord tone a semitone from the tune is a clash;
- the bass (the lowest sounding notes, or the bass part when there is one):
  the root is best, the 3rd or 5th gives an inversion (slash chord);
- the key: diatonic chords first, then secondary dominants and borrowed
  chords, so a chord with no third is major or minor as the key says.
Changing chord costs more on weak beats than on a downbeat, common moves
(V-I, ii-V, IV-V, I-IV, ...) cost less, and phrase ends (a rest in the tune)
lean towards V-I. When the accompaniment is thin or noisy, the tune itself
is harmonized with the key's diatonic chords instead.

Everything runs in beat space (ticks), so a drifting tempo doesn't move the
chord changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, sqrt

import numpy as np

from .config import TICKS_PER_BEAT as T
from .model import BeatGrid, Note, QNote

# Krumhansl-Kessler key profiles; index 0 is the tonic.
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Chord vocabulary: intervals above the root, in the order root, third,
# fifth, seventh (sus4's "third" is the fourth).
QUALITIES: dict[str, tuple[int, ...]] = {
    "maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6), "sus4": (0, 5, 7),
    "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
}
CHORDS = [(root, q) for root in range(12) for q in QUALITIES]
_INDEX = {c: i for i, c in enumerate(CHORDS)}

MIN_FILL = 16.0      # a beat needs this much velocity x ticks per tick to count as sounding

# accompaniment fit, per chord tone role (root, third, fifth, seventh)
EXPLAIN = (1.0, 1.0, 0.8, 0.9)      # credit for the energy a chord tone explains
LACKING = (0.5, 0.2, 0.1, 0.35)    # cost of a chord tone that is (almost) absent
LACKING_DIM = (0.5, 0.35, 0.35)     # a diminished chord needs its tritone
PRESENT = 0.08                      # share of the beat's energy that makes a tone "there"
OUTSIDE = 0.6                       # cost per unit of energy outside the chord
# bass: credit for the lowest note by role; a non-chord bass costs
BASS_ROLE = (1.0, 0.45, 0.35, 0.1)
BASS_OUTSIDE = -0.6
# the tune: credit for a triad tone, cost of a note a semitone from a chord tone
TUNE_CLASH = 0.8

W_ACC, W_TUNE, W_BASS = 1.0, 0.6, 0.5            # normal accompaniment
W_ACC_THIN, W_TUNE_THIN = 0.25, 1.0              # thin accompaniment: follow the tune

# cost of a new chord at a downbeat / the middle of a 4/4 bar / another beat / off the beat
CHANGE = (0.25, 0.45, 0.9, 1.4)
CHANGE_THIN = (0.25, 0.5, 1.2, 5.0)
CADENCE_TONIC, CADENCE_DOMINANT, CADENCE_STEP, FINAL_TONIC = 0.3, 0.1, 0.25, 0.4
_TONIC_QUALITIES = {"major": ("maj", "maj7"), "minor": ("min", "m7")}

# thin accompaniment: under this share of the tune's beats has any, or this
# much of its sound is the tune itself, or it hardly looks like chords
THIN_COVER, THIN_ECHO, THIN_FIT, THIN_ONSETS = 0.5, 0.6, 0.3, 0.25


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    da = [x - ma for x in a]
    db = [y - mb for y in b]
    den = sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return sum(x * y for x, y in zip(da, db)) / den if den > 0 else 0.0


def _fifths(major_tonic: int) -> int:
    f = (major_tonic * 7) % 12
    return f - 12 if f > 6 else f


def detect_key(pitches_weights: list[tuple[int, float]]) -> tuple[int, str]:
    """(fifths, "major"|"minor"). Minor keys report their relative major's
    fifths (A minor -> 0, E minor -> 1, D minor -> -1). C major when unsure."""
    chroma = [0.0] * 12
    for pitch, weight in pitches_weights:
        if weight > 0:
            chroma[int(pitch) % 12] += float(weight)
    if max(chroma) - min(chroma) <= 0:
        return 0, "major"
    best = None
    for mode, profile in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for tonic in range(12):
            r = _pearson(chroma, [profile[(pc - tonic) % 12] for pc in range(12)])
            if best is None or r > best[0] + 1e-12:
                best = (r, tonic, mode)
    _, tonic, mode = best
    return _fifths(tonic if mode == "major" else (tonic + 3) % 12), mode


def tonic_of(key: tuple[int, str]) -> int:
    fifths, mode = key
    return (7 * fifths + (9 if mode == "minor" else 0)) % 12


def chord_tones(root: int, quality: str) -> tuple[int, ...]:
    """Pitch classes in the order root, third, fifth(, seventh)."""
    return tuple((root + i) % 12 for i in QUALITIES[quality])


@dataclass
class Chord:
    """One chord with one bass note, inside one bar. `bass` is a pitch class:
    the root, or the 3rd or 5th for an inversion."""
    start: int
    dur: int
    root: int
    quality: str
    bass: int
    level: float          # mean velocity of the accompaniment under it

    @property
    def end(self) -> int:
        return self.start + self.dur

    @property
    def tones(self) -> tuple[int, ...]:
        return chord_tones(self.root, self.quality)


# ---------------------------------------------------------------- chord tables

def _tables():
    n = len(CHORDS)
    explain, lacking = np.zeros((n, 12)), np.zeros((n, 12))
    member, triad = np.zeros((n, 12), bool), np.zeros((n, 12), bool)
    bass = np.full((n, 12), BASS_OUTSIDE)
    for i, (root, q) in enumerate(CHORDS):
        for role, pc in enumerate(chord_tones(root, q)):
            explain[i, pc] = EXPLAIN[role]
            lacking[i, pc] = (LACKING_DIM if q == "dim" else LACKING)[role]
            member[i, pc] = True
            triad[i, pc] = role < 3
            bass[i, pc] = BASS_ROLE[role]
    near = np.roll(member, 1, axis=1) | np.roll(member, -1, axis=1)
    return explain, lacking, member, triad, near, bass


_EXPLAIN, _LACKING, _MEMBER, _TRIAD, _NEAR, _BASS = _tables()


def _key_class(root: int, quality: str, tonic: int, mode: str) -> str:
    """"diatonic", "chromatic" (secondary dominants and leading-tone chords,
    borrowed chords) or "far"."""
    deg = (root - tonic) % 12
    if mode == "major":
        diatonic = {0: "maj maj7", 2: "min m7", 4: "min m7", 5: "maj maj7",
                    7: "maj 7 sus4", 9: "min m7", 11: "dim"}
        chromatic = {0: "7", 2: "maj 7", 4: "maj 7", 9: "maj 7", 11: "maj 7",
                     5: "min", 10: "maj", 8: "maj dim", 1: "dim", 3: "dim", 6: "dim"}
    else:
        diatonic = {0: "min m7", 2: "dim", 3: "maj maj7", 5: "min m7", 7: "maj 7 min sus4",
                    8: "maj maj7", 10: "maj 7", 11: "dim"}
        chromatic = {0: "7", 2: "maj 7 min m7", 5: "maj 7", 7: "m7", 1: "maj",
                     4: "dim", 6: "dim"}
    if quality in diatonic.get(deg, "").split():
        return "diatonic"
    if quality in chromatic.get(deg, "").split():
        return "chromatic"
    return "far"


def _priors(tonic: int, mode: str) -> np.ndarray:
    out = np.zeros(len(CHORDS))
    for i, (root, q) in enumerate(CHORDS):
        cls = _key_class(root, q, tonic, mode)
        out[i] = {"diatonic": 0.0, "chromatic": -0.25, "far": -0.55}[cls]
        if q in ("7", "maj7", "m7", "dim"):
            # a seventh (or diminished) chord only from the key, or as a dominant
            out[i] -= 0.05 if cls != "far" else 0.45
        if q == "sus4":
            out[i] -= 0.7
    return out


def _tune_vocabulary(tonic: int, mode: str) -> np.ndarray:
    """Chords for harmonizing a bare tune: the key's triads, V7 and V/V."""
    keep = np.zeros(len(CHORDS), bool)
    degrees = ({0: "maj", 2: "min", 4: "min", 5: "maj", 7: "maj 7", 9: "min"}
               if mode == "major" else
               {0: "min", 2: "dim", 3: "maj", 5: "min", 7: "maj 7", 8: "maj", 10: "maj"})
    for deg, qs in degrees.items():
        for q in qs.split():
            keep[_INDEX[((tonic + deg) % 12, q)]] = True
    keep[_INDEX[((tonic + 2) % 12, "maj")]] = True          # V/V
    keep[_INDEX[((tonic + 2) % 12, "7")]] = True
    return keep


def _progressions(tonic: int, mode: str) -> np.ndarray:
    """Bonus for moving from chord i to chord j (rows: from)."""
    common = ({(0, 5), (0, 7), (5, 7), (2, 7), (9, 2), (9, 5), (7, 9), (5, 0), (0, 9),
               (4, 9), (5, 2), (2, 0)} if mode == "major" else
              {(0, 5), (0, 7), (5, 7), (2, 7), (8, 2), (8, 5), (7, 8), (5, 0), (0, 8),
               (3, 8), (10, 3), (0, 3), (5, 2)})
    tonic_q = _TONIC_QUALITIES[mode]
    n = len(CHORDS)
    P = np.zeros((n, n))
    for i, (ra, qa) in enumerate(CHORDS):
        da = (ra - tonic) % 12
        for j, (rb, qb) in enumerate(CHORDS):
            if i == j:
                continue
            b = 0.0
            motion = (rb - ra) % 12
            if motion == 0:
                b += 0.1                         # C -> C7, Am -> Am7: same harmony
            elif motion == 5:                    # down a fifth
                b += 0.08 + (0.04 if qa in ("maj", "7") else 0.0) + (0.04 if qa == "7" else 0.0)
            elif motion == 6:
                b -= 0.1
            db = (rb - tonic) % 12
            if (da, db) in common:
                b += 0.06
            if da == 7 and db == 0 and qa in ("maj", "7") and qb in tonic_q:
                b += 0.15
            P[i, j] = b
    return P


def _cadence_step(tonic: int, mode: str) -> np.ndarray:
    n = len(CHORDS)
    P = np.zeros((n, n))
    for i, (ra, qa) in enumerate(CHORDS):
        if (ra - tonic) % 12 == 7 and qa in ("maj", "7"):
            for q in _TONIC_QUALITIES[mode]:
                P[i, _INDEX[(tonic, q)]] = CADENCE_STEP
    return P


# ---------------------------------------------------------------- per-beat evidence

def _ticks(notes: list[Note], grid: BeatGrid) -> list[tuple[float, float, int, int]]:
    out = []
    for n in notes:
        s, e = grid.to_ticks(n.start), grid.to_ticks(n.end)
        if e > s:
            out.append((s, e, n.pitch, n.velocity))
    return out


def _unit_chroma(rows, unit: int, k0: int, K: int):
    """(velocity x ticks chroma, ticks of sounding notes, onsets) per unit."""
    chroma = np.zeros((K, 12))
    sounding = np.zeros(K)
    onsets = np.zeros(K)
    for s, e, p, v in rows:
        a, b = max(floor(s / unit), k0), min(ceil(e / unit), k0 + K)
        for k in range(a, b):
            ov = min(e, (k + 1) * unit) - max(s, k * unit)
            if ov > 0:
                chroma[k - k0, p % 12] += ov * v
                sounding[k - k0] += ov
        ks = floor(s / unit) - k0
        if 0 <= ks < K:
            onsets[ks] += 1
    return chroma, sounding, onsets


def _lowest_line(rows, base: int, count: int, step: int) -> np.ndarray:
    """Lowest sounding pitch in each `step`-tick slice from `base` (999 = silent)."""
    low = np.full(count, 999)
    for s, e, p, v in rows:
        if v < 20:
            continue
        lo = max(0, ceil((s - base) / step - 0.5))
        hi = min(count, ceil((e - base) / step - 0.5))
        if hi > lo:
            np.minimum(low[lo:hi], p, out=low[lo:hi])
    return low


def _unit_lowest(part, fallback, unit: int, k0: int, K: int, step: int = 3) -> np.ndarray:
    """Per unit: ticks each pitch class is the lowest sounding note, weighted
    by register. `part` (a bass part) decides where it sounds, `fallback`
    (the accompaniment) elsewhere."""
    n = max(1, unit // step)
    base, count = k0 * unit, K * n
    line = _lowest_line(part, base, count, step) if part else np.full(count, 999)
    weight = np.where(line < 999, 1.0, 0.0)
    if fallback:
        fb = _lowest_line(fallback, base, count, step)
        use = (line >= 999) & (fb < 999)
        line = np.where(use, fb, line)
        weight = np.where(use, 0.6 if part else 1.0, weight)
    ok = line < 999
    ks, pcs = np.arange(count) // n, line % 12
    register = np.where(line <= 52, 1.0, np.where(line <= 64, 0.5, 0.2))
    low = np.zeros((K, 12))
    np.add.at(low, (ks[ok], pcs[ok]), step * weight[ok] * register[ok])
    return low


def _unit_bass_note(part, fallback, unit: int, k0: int, K: int) -> np.ndarray:
    """Per unit: the bass note a chord starting there stands on: the lowest
    note below middle C struck in the unit (a 16th early counts) or still
    held well past its start (not just a dying tail); the bass part first.
    -1 = none."""
    out = np.full(K, -1)
    for src in (part, fallback):
        low = np.full(K, 999)
        for s, e, p, v in src:
            if p >= 60 or v < 20 or e - s < 3:
                continue
            first = floor((s + 3) / unit)
            last = min(floor((e - 3 - 1e-9) / unit), k0 + K - 1)  # held past a unit start + a 16th
            for k in range(max(first, k0), max(first, last) + 1):
                if k - k0 < K:
                    low[k - k0] = min(low[k - k0], p)
        take = (out < 0) & (low < 999)
        out[take] = low[take]
    return out


def _unit_tune(melody: list[QNote], unit: int, bar: int, k0: int, K: int) -> np.ndarray:
    """Per unit: tune pitch classes weighted by length and beat (downbeat and
    the middle of a 4/4 bar 2, other beats 1.5, off the beat 1); a strong
    note filling the whole unit sums to 1."""
    out = np.zeros((K, 12))
    for q in melody:
        a, b = max(floor(q.start / unit), k0), min(ceil(q.end / unit), k0 + K)
        for k in range(a, b):
            s, e = max(q.start, k * unit), min(q.end, (k + 1) * unit)
            if e > s:
                out[k - k0, q.pitch % 12] += (e - s) / unit * _metric(s, bar) / 2.0
    return np.minimum(out, 1.0)


def _metric(tick: float, bar: int) -> float:
    pos = tick % bar
    if pos == 0 or (bar == 4 * T and pos == 2 * T):
        return 2.0
    return 1.5 if pos % T == 0 else 1.0


def _change_cost(tick: int, bar: int, costs) -> float:
    pos = tick % bar
    if pos == 0:
        return costs[0]
    if bar == 4 * T and pos == 2 * T:
        return costs[1]
    return costs[2] if pos % T == 0 else costs[3]


def _phrase_ends(melody: list[QNote], bar: int) -> tuple[list[int], int | None]:
    """Ticks where phrases end (the last note before a rest of a beat or more,
    in a phrase of a bar or more), and the tune's last note."""
    notes = sorted(melody, key=lambda q: q.start)
    if not notes:
        return [], None
    ends, start = [], notes[0].start
    for i, q in enumerate(notes[:-1]):
        if notes[i + 1].start - q.end >= T:
            if q.end - start >= bar:
                ends.append(q.start)
            start = notes[i + 1].start
    return ends, notes[-1].start


# ---------------------------------------------------------------- choosing

def _acc_fit(a: np.ndarray) -> np.ndarray:
    explained = a @ _EXPLAIN.T
    outside = a @ (~_MEMBER).T
    lacking = np.maximum(0.0, 1.0 - a / PRESENT) @ _LACKING.T
    return explained - OUTSIDE * outside - lacking


def _tune_fit(m: np.ndarray) -> np.ndarray:
    return m @ _TRIAD.T - TUNE_CLASH * (m @ _NEAR.T)


def _viterbi(E: np.ndarray, change: np.ndarray, P: np.ndarray, extra: dict[int, np.ndarray]) -> list[int]:
    n, C = E.shape
    diag = np.arange(C)
    score = E[0].copy()
    back = np.zeros((n, C), dtype=np.int32)
    for i in range(1, n):
        M = score[:, None] + (P - change[i])
        if i in extra:
            M = M + extra[i]
        M[diag, diag] = score
        back[i] = M.argmax(axis=0)
        score = M[back[i], diag] + E[i]
    path = [int(score.argmax())]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    return path[::-1]


def _is_thin(energy, need, onsets, tune, fit, echo_share) -> bool:
    tuned = tune.sum(axis=1) > 0
    if not tuned.any():
        return False
    covered = (energy >= need) & tuned
    cover = covered.sum() / tuned.sum()
    if cover < THIN_COVER or onsets[tuned].sum() < THIN_ONSETS * tuned.sum():
        return True
    if echo_share > THIN_ECHO:
        return True
    return covered.any() and float(np.mean(fit[covered])) < THIN_FIT


def harmonize(notes: list[Note], grid: BeatGrid, bass: list[Note] | None = None,
              melody: list[QNote] | None = None, key: tuple[int, str] | None = None,
              unit: int = T) -> list[Chord]:
    """Chords for the piece, as Chord rows that never cross a bar line.

    `notes`: the accompaniment (seconds); `bass`: an optional bass part
    (seconds) that joins the chroma and gives the bass notes; `melody`: the
    tune (ticks, same key as `notes`); `key`: (fifths, mode), detected when
    left out. A new chord may start every `unit` ticks. Where the
    accompaniment is silent there is no chord, unless it is too thin to
    follow, in which case the tune is harmonized wherever it plays. With no
    accompaniment at all (a voice alone, a melody-only MIDI file) the tune is
    harmonized, so the flute still gets a supporting piano part."""
    bar = grid.beats_per_bar * T
    acc = _ticks(list(notes), grid)
    low_part = _ticks(list(bass or []), grid)
    rows = acc + low_part
    melody = [q for q in (melody or []) if q.dur > 0]
    starts = [r[0] for r in rows] + [q.start for q in melody]
    ends = [r[1] for r in rows] + [q.end for q in melody]
    if not rows and not melody:
        return []
    k0 = floor(min(starts) / unit)
    K = ceil(max(ends) / unit) - k0
    A, sounding, onsets = _unit_chroma(rows, unit, k0, K)
    energy = A.sum(axis=1)
    need = MIN_FILL * unit
    tune = _unit_tune(melody, unit, bar, k0, K)
    if key is None:
        weights = [(p, (e - s) / T) for s, e, p, _ in rows] + [(q.pitch, q.dur / T) for q in melody]
        key = detect_key(weights)
    tonic, mode = tonic_of(key), key[1]

    a = A / np.maximum(energy, 1e-9)[:, None]
    fit = _acc_fit(a)
    best_fit = fit.max(axis=1)
    tuned = tune.sum(axis=1) > 0
    echo = float((a * (tune > 0)).sum(axis=1)[tuned & (energy >= need)].sum()
                 / max(1, (tuned & (energy >= need)).sum()))
    thin = bool(melody) and _is_thin(energy, need, onsets, tune, best_fit, echo)

    prior = _priors(tonic, mode)
    if thin:
        prior = np.where(_tune_vocabulary(tonic, mode), prior, -1e6)
        active = (energy >= need) | tuned
        # carry the chord through short rests in the tune
        idx = np.nonzero(active)[0]
        for x, y in zip(idx, idx[1:]):
            if 1 < y - x and (y - x - 1) * unit < bar:
                active[x + 1:y] = True
        conf = np.clip(np.sqrt(energy / (3 * need)), 0.0, 1.0)
        E = (W_ACC_THIN * conf[:, None] * fit + W_TUNE_THIN * _tune_fit(tune) + prior[None, :])
        costs = CHANGE_THIN
        bass_note = np.full(K, -1)
    else:
        active = energy >= need
        conf = np.clip(np.sqrt(energy / (3 * need)), 0.0, 1.0)
        low = _unit_lowest(low_part, acc, unit, k0, K)
        bass_note = _unit_bass_note(low_part, acc, unit, k0, K)
        lw = low.sum(axis=1)
        beta = low / np.maximum(lw, 1e-9)[:, None]
        bconf = np.clip(lw / unit, 0.0, 1.0)
        E = (W_ACC * conf[:, None] * fit + W_TUNE * _tune_fit(tune)
             + W_BASS * bconf[:, None] * (beta @ _BASS.T) + prior[None, :])
        costs = CHANGE

    # phrase ends lean towards the tonic (and V before it)
    cad_rows = np.zeros(len(CHORDS))
    for q in _TONIC_QUALITIES[mode]:
        cad_rows[_INDEX[(tonic, q)]] = CADENCE_TONIC
    dom = (tonic + 7) % 12
    cad_rows[_INDEX[(dom, "maj")]] = cad_rows[_INDEX[(dom, "7")]] = CADENCE_DOMINANT
    phrase_ends, last = _phrase_ends(melody, bar)
    cadence_units = {floor(t / unit) - k0 for t in phrase_ends}
    for k in cadence_units:
        if 0 <= k < K:
            E[k] += cad_rows
    if last is not None and 0 <= floor(last / unit) - k0 < K:
        for q in _TONIC_QUALITIES[mode]:
            E[floor(last / unit) - k0, _INDEX[(tonic, q)]] += FINAL_TONIC

    P = _progressions(tonic, mode)
    cad_step = _cadence_step(tonic, mode)
    change = np.array([_change_cost((k0 + k) * unit, bar, costs) for k in range(K)])

    choice = [-1] * K
    k = 0
    while k < K:
        if not active[k]:
            k += 1
            continue
        j = k
        while j < K and active[j]:
            j += 1
        extra = {i - k: cad_step for i in range(k + 1, j) if i in cadence_units}
        choice[k:j] = _viterbi(E[k:j], change[k:j], P, extra)
        k = j

    return _rows(choice, bass_note, energy, sounding, unit, k0, bar)


def _inversion(idx: int, note: int) -> int:
    """The bass pitch class: the real bass note when it is the chord's 3rd
    or 5th (a slash chord), else the root."""
    root, q = CHORDS[idx]
    tones = chord_tones(root, q)
    if note >= 0 and q != "sus4" and note % 12 in tones[1:3]:
        return note % 12
    return root


def _rows(choice, bass_note, energy, sounding, unit, k0, bar) -> list[Chord]:
    """One Chord per run of the same chord inside a bar, standing on the bass
    note found where it starts."""
    out: list[Chord] = []
    level_all = float(energy.sum() / sounding.sum()) if sounding.sum() > 0 else 64.0
    sums: list[list[float]] = []
    for k, idx in enumerate(choice):
        if idx < 0:
            continue
        start = (k0 + k) * unit
        last = out[-1] if out else None
        if (last is not None and last.end == start and (last.root, last.quality) == CHORDS[idx]
                and last.start // bar == start // bar):
            last.dur += unit
            sums[-1][0] += energy[k]
            sums[-1][1] += sounding[k]
            continue
        out.append(Chord(start, unit, CHORDS[idx][0], CHORDS[idx][1],
                         _inversion(idx, int(bass_note[k])), level_all))
        sums.append([energy[k], sounding[k]])
    for c, (e, n) in zip(out, sums):
        if n > 0:
            c.level = e / n
    return out


# ---------------------------------------------------------------- simple views

def detect_chords(notes: list[Note], grid: BeatGrid, span: int = T,
                  bass: list[Note] | None = None, melody: list[QNote] | None = None,
                  key: tuple[int, str] | None = None) -> list[tuple[int, int, int, str]]:
    """(start_tick, dur_ticks, root_pc, quality), a chord changing at most
    every `span` ticks. The same chord in a row inside one bar is one row;
    rows never cross a bar line, so the chord is struck again each bar."""
    bar = grid.beats_per_bar * T
    rows: list[list] = []
    for c in harmonize(notes, grid, bass, melody, key, unit=span):
        if rows:
            last = rows[-1]
            if (last[0] + last[1] == c.start and (last[2], last[3]) == (c.root, c.quality)
                    and last[0] // bar == c.start // bar):
                last[1] += c.dur
                continue
        rows.append([c.start, c.dur, c.root, c.quality])
    return [tuple(r) for r in rows]
