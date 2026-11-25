"""Expression for the flute part: what a player or an editor adds to the bare notes.

add_expression reads how the recording was played (the transcribed melody, in
seconds, and the beat grid, which follows the performer's tempo) and a few
rules editors follow, and returns Marks (model.Mark) in ticks on the flute
part. Notes and rhythm are never changed.

Levels: "plain" adds nothing, "expressive" (the default) adds the marks below,
"ornaments" also adds a few ornaments.

"Bars" below are bars as phrasing counts them: short bars (2/4, 3/8) count
in pairs, as breath marks do (_unit), except for slur lengths.

Dynamics
    Loudness is the melody's velocity: the transcription models read it from
    how loud each note sounds. A piece's own median loudness is its basic
    dynamic, which comes from its tempo (a recording's overall level says
    nothing, it is normalised; slow pieces are mostly soft): p under Adagio
    speed, mp up to Andante, mf faster. Every STEP of velocity above or below
    the median is one level (pp p mp mf f ff). The loudness is smoothed
    over about a bar and read per phrase (phrases end at rests and breath
    marks); a new dynamic is written at a phrase start only when the
    phrase sits HYSTERESIS of a level past the boundary to the next level,
    and never within MIN_DYN_GAP bars of the last one, so the part reads
    like an edition (one dynamic per phrase at most), not a loudness meter.
Hairpins
    The loudness, smoothed over about a beat, is followed through each phrase
    (between rests); a swing of at least RISE from a low point to a high point
    (or back) taking at least a bar is a crescendo (or diminuendo) wedge from
    the one to the other. A dynamic inside a wedge cuts it there; a wedge that
    would run into a dynamic going the other way is dropped.
Articulation
    staccato: the note sounded less than half its slot (onset to next onset;
    the slot at least STACC_SLOT long, the silence at least STACC_GAP, so the
    transcription's timing noise on fast notes does not count), written no
    longer than a beat, and not the last note before a rest or breath (there
    the shortness is the phrase ending, not a staccato).
    accent / tenuto: a note on a weak beat clearly louder than its neighbours
    (two each side in the phrase): ACCENT louder than their mean and louder
    than each is an accent; TENUTO louder and at least a beat long is a
    tenuto (a leaned-on long note). At most one per bar.
Slurs
    Notes that were played connected (sounding at least LEGATO of their slot)
    are slurred, in the same phrase, moving by step or a small leap (up to
    BIG_LEAP semitones). A slur breaks at repeated notes (they have to be
    tongued), big leaps, rests, breath marks and staccatos, and is never longer
    than SLUR_BARS bars: longer runs are cut after their longest note (a
    phrase breathes after a long note), preferring a cut before a bar line.
Fermata and rit.
    The last note gets a fermata when it is long (half a bar or more); any
    other note that ends a phrase gets one where the beat stops (a beat that
    lasted FERMATA_BEAT times its neighbours). "rit." is written where the
    beat slows clearly before a phrase end: the last beats at least RIT
    slower than the bars before, getting slower; "a tempo" where the next
    phrase is back near the tempo (its first four bars less than RIT slower).
Tempo word
    The standard metronome ranges (Largo under 60, Larghetto, Adagio 66-76,
    Andante 76-108, Moderato, Allegretto 112-120, Allegro, Presto 168+) for
    the felt beat (a dotted-quarter beat counts double, an 8th beat of 3/8
    two-thirds), plus a character word: leggiero (light, many staccatos),
    con brio (fast and loud), espressivo (dynamics move a lot), dolce (soft
    and calm).
Ornaments (level "ornaments" only)
    A trill on the long note before a cadence's last note (scale degree 2 or 7
    going to the tonic, the V-I approach); a turn (or a mordent on shorter
    notes) on the long peak note of a phrase; a grace note (acciaccatura from
    the next scale note above) on the long note reached by an upward leap of
    a 4th or more. Never on fast notes (under a beat or ORN_SECONDS), never
    on staccatos, and at most one every ORN_BARS bars, cadences first.

Sheet music input: marks the score already has (arr.expression) are kept; at
"expressive" or "ornaments" only what is missing is added (no second
dynamics plan over the score's own, no slurs over its slurs, no second
articulation or ornament on a note).
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass

import numpy as np

from .model import DOTTED_BEAT, EIGHTH_BEAT, Arrangement, BeatGrid, Mark, Note, QNote

LEVELS = ("plain", "expressive", "ornaments")
DYNAMICS = ("pp", "p", "mp", "mf", "f", "ff")
HAIRPINS = ("cresc", "dim")
ARTICULATIONS = ("staccato", "tenuto", "accent")
ORNAMENTS = ("trill", "mordent", "turn", "grace")
TEMPO_CHANGES = ("rit.", "a tempo")

MATCH_TOL = 3        # ticks: a flute note is the performed note starting within a 16th of it
STEP = 8.0           # velocity per dynamic level
HYSTERESIS = 0.3     # of a level, past the boundary, before the dynamic changes
MIN_DYN_GAP = 2      # bars between two dynamic marks at least
RISE = 6.0           # velocity: the smallest swing written as a hairpin
STACC_RATIO = 0.5    # a staccato note sounds under this share of its slot ...
STACC_SLOT = 0.25    # s ... the slot at least this long ...
STACC_GAP = 0.12     # s ... and silent at least this long
ACCENT = 14.0        # velocity over the neighbours' mean: an accent
TENUTO = 9.0         # ... a tenuto (on a long note)
LEGATO = 0.75        # share of the slot a slurred note sounds at least
BIG_LEAP = 7         # semitones: wider leaps break a slur
SLUR_BARS = 2        # longest slur
FERMATA_BEAT = 1.8   # a beat this many times its neighbours: the tempo stopped
RIT = 1.15           # the last beats before a phrase end this much slower: rit.
RIT_START = 1.06     # ... it starts where beats are this much slower
ORN_BARS = 4         # bars between ornaments at least
ORN_SECONDS = 0.4    # an ornamented note lasts at least this long
TEMPO_WORDS = ((60, "Largo"), (66, "Larghetto"), (76, "Adagio"), (108, "Andante"),
               (112, "Moderato"), (120, "Allegretto"), (168, "Allegro"), (math.inf, "Presto"))


# ---------------------------------------------------------------- what was played


@dataclass
class _Played:
    vel: float           # loudness (velocity); interpolated when not found
    found: bool          # a performed note was matched to this flute note
    sounded: float       # s the performed note sounded (the slot when not found)
    slot: float          # s from this onset to the next (inf for the last note)
    secs: float          # written length in seconds at the local tempo


def _seconds(grid: BeatGrid, tick: float) -> float:
    return grid.to_seconds(tick / grid.beat_ticks)


def _played(line: list[QNote], melody: list[Note], grid: BeatGrid, shift: int) -> list[_Played]:
    mel = sorted(melody, key=lambda n: n.start)
    ticks = [grid.to_ticks(n.start) for n in mel]
    found: list[Note | None] = []
    for q in line:
        best, cost = None, math.inf
        j = bisect_left(ticks, q.start - MATCH_TOL)
        while j < len(mel) and ticks[j] <= q.start + MATCH_TOL:
            c = abs(ticks[j] - q.start) + (0 if (mel[j].pitch + shift - q.pitch) % 12 == 0
                                           else MATCH_TOL)
            if c < cost:
                best, cost = mel[j], c
            j += 1
        found.append(best)
    idx = [i for i, n in enumerate(found) if n is not None]
    vel_known = [found[i].velocity for i in idx]
    vels = (np.interp(range(len(line)), idx, vel_known) if idx
            else np.full(len(line), 80.0))
    onsets = [n.start if n is not None else _seconds(grid, q.start) for q, n in zip(line, found)]
    out = []
    for i, (q, n) in enumerate(zip(line, found)):
        slot = onsets[i + 1] - onsets[i] if i + 1 < len(line) else math.inf
        secs = _seconds(grid, q.end) - _seconds(grid, q.start)
        sounded = n.dur if n is not None else min(slot, secs)
        out.append(_Played(float(vels[i]), n is not None, sounded, slot, secs))
    return out


def _split(line: list[QNote], at_breaths: bool) -> list[list[int]]:
    """Runs of note indices without a rest between them (and without a breath mark)."""
    if not line:
        return []
    out, cur = [], [0]
    for i in range(1, len(line)):
        a = line[i - 1]
        if line[i].start > a.end or (at_breaths and a.breath_after):
            out.append(cur)
            cur = []
        cur.append(i)
    out.append(cur)
    return out


def _smooth(pos: np.ndarray, val: np.ndarray, w: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian-weighted average of val around each position."""
    d = (pos[:, None] - pos[None, :]) / max(sigma, 1e-6)
    k = w[None, :] * np.exp(-0.5 * d * d)
    return (k @ val) / k.sum(axis=1)


def felt_bpm(bpm: float, beat_ticks: int) -> float:
    """The tempo a player feels: a dotted-quarter beat counts double (its 8ths
    move like a quarter beat's 8ths twice as fast), an 8th beat of 3/8 two-thirds."""
    if beat_ticks == DOTTED_BEAT:
        return bpm * 2
    if beat_ticks == EIGHTH_BEAT:
        return bpm * 2 / 3
    return bpm


def tempo_word(felt: float) -> str:
    for top, word in TEMPO_WORDS:
        if felt < top:
            return word
    return "Presto"


def _base_level(felt: float) -> int:
    """Index into DYNAMICS of the piece's median loudness: p, mp or mf."""
    return 1 if felt < 72 else 2 if felt < 108 else 3


def _unit(arr: Arrangement) -> int:
    """A bar as phrasing counts it: short bars (2/4, 3/8: under three quarter
    beats or two dotted ones) count in pairs, as breath marks do."""
    return arr.bar_ticks * (1 if arr.bar_ticks >= 3 * 12 else 2)


def _strong(tick: int, arr: Arrangement) -> bool:
    """On beat 1 (or the middle of a bar of four)."""
    pos = tick % arr.bar_ticks
    return pos == 0 or (arr.beats_per_bar == 4 and pos == 2 * arr.beat_ticks)


# ---------------------------------------------------------------- scale steps


def _scale(fifths: int) -> set[int]:
    """Pitch classes of the key signature (a minor key reads its relative major's)."""
    tonic = (7 * fifths) % 12
    return {(tonic + s) % 12 for s in (0, 2, 4, 5, 7, 9, 11)}


def upper_neighbour(pitch: int, fifths: int) -> int:
    """The next note of the key signature above (what a trill or mordent sign means)."""
    sc = _scale(fifths)
    return next(p for p in range(pitch + 1, pitch + 13) if p % 12 in sc)


def lower_neighbour(pitch: int, fifths: int) -> int:
    sc = _scale(fifths)
    return next(p for p in range(pitch - 1, pitch - 13, -1) if p % 12 in sc)


def tonic_pc(fifths: int, mode: str) -> int:
    major = (7 * fifths) % 12
    return (major + 9) % 12 if mode == "minor" else major


# ---------------------------------------------------------------- the marks


def _dynamics(line, pl, arr, base) -> list[Mark]:
    bar = _unit(arr)
    pos = np.array([q.start for q in line], dtype=float)
    vel = np.array([p.vel for p in pl])
    w = np.array([min(q.dur, arr.beat_ticks) for q in line], dtype=float)
    if not any(p.found for p in pl) or np.ptp(vel) < 1.0:
        return [Mark("dynamic", line[0].start, value=DYNAMICS[base])]
    order = np.argsort(vel)
    cum = np.cumsum(w[order])
    med = float(vel[order][np.searchsorted(cum, cum[-1] / 2)])
    curve = _smooth(pos, vel, w, 0.75 * bar)
    marks: list[Mark] = []
    cur = None
    last = -math.inf
    for seg in _split(line, at_breaths=True):
        start = line[seg[0]].start
        near = [i for i in seg if line[i].start < start + 2 * bar]
        level = base + (float(np.average(curve[near], weights=w[near])) - med) / STEP
        if cur is None:
            cur = int(min(5, max(0, round(level))))
            marks.append(Mark("dynamic", start, value=DYNAMICS[cur]))
            last = start
            continue
        if line[seg[-1]].end - start < bar or start - last < MIN_DYN_GAP * bar:
            continue
        if abs(level - cur) >= 0.5 + HYSTERESIS:
            new = int(min(5, max(0, round(level))))
            if new != cur:
                cur = new
                marks.append(Mark("dynamic", start, value=DYNAMICS[cur]))
                last = start
    return marks


def _turning_points(curve: np.ndarray, idx: list[int]) -> list[int]:
    """Lows and highs of curve along idx that swing at least RISE from one to the
    next (a zigzag: smaller wiggles are ignored)."""
    turns: list[int] = []
    hi = lo = ext = idx[0]
    trend = 0
    for i in idx[1:]:
        if trend == 0:
            hi = i if curve[i] > curve[hi] else hi
            lo = i if curve[i] < curve[lo] else lo
            if curve[i] - curve[lo] >= RISE:
                turns, trend, ext = [lo], 1, i
            elif curve[hi] - curve[i] >= RISE:
                turns, trend, ext = [hi], -1, i
        elif (curve[i] - curve[ext]) * trend >= 0:
            ext = i                                  # still going the same way
        elif abs(curve[ext] - curve[i]) >= RISE:
            turns.append(ext)
            trend, ext = -trend, i
    if trend:
        turns.append(ext)
    return turns


def _hairpins(line, pl, arr, dyn: list[Mark]) -> list[Mark]:
    bar, beat = _unit(arr), arr.beat_ticks
    if not any(p.found for p in pl):
        return []
    pos = np.array([q.start for q in line], dtype=float)
    vel = np.array([p.vel for p in pl])
    w = np.array([min(q.dur, beat) for q in line], dtype=float)
    fine = _smooth(pos, vel, w, max(beat, bar / 3))
    level = {m.start: DYNAMICS.index(m.value) for m in dyn}
    out: list[Mark] = []
    for phrase in _split(line, at_breaths=False):
        turns = _turning_points(fine, phrase)
        for a, b in zip(turns, turns[1:]):
            if b <= a or abs(fine[b] - fine[a]) < RISE:
                continue
            kind = "cresc" if fine[b] > fine[a] else "dim"
            s, e = line[a].start, line[b].start
            inside = sorted(t for t in level if s < t <= e)
            if inside:
                t = inside[0]
                before = max((u for u in level if u <= s), default=None)
                if before is not None:
                    up = level[t] > level[before]
                    if up != (kind == "cresc") or level[t] == level[before]:
                        continue                  # the next dynamic goes the other way
                e = t
            if e - s < bar:
                continue
            out.append(Mark(kind, s, e))
    return out


def _articulation(line, pl, arr, ends: set[int]) -> list[Mark]:
    beat = arr.beat_ticks
    out: list[Mark] = []
    stresses: list[tuple[float, Mark]] = []
    pos = np.array([q.start for q in line], dtype=float)
    w = np.array([min(q.dur, beat) for q in line], dtype=float)
    local = _smooth(pos, np.array([p.vel for p in pl]), w, _unit(arr) / 2)
    for phrase in _split(line, at_breaths=False):
        for k, i in enumerate(phrase):
            q, p = line[i], pl[i]
            if (p.found and i not in ends and q.dur <= beat and p.slot >= STACC_SLOT
                    and p.sounded < STACC_RATIO * p.slot and p.slot - p.sounded >= STACC_GAP):
                out.append(Mark("staccato", q.start))
                continue
            if not p.found or _strong(q.start, arr):
                continue
            nb = [pl[j].vel for j in phrase[max(0, k - 2):k] + phrase[k + 1:k + 3] if pl[j].found]
            if len(nb) < 2:
                continue
            # louder than the neighbours and than the passage around it (in
            # broken-chord writing the tune's notes are louder than the figures)
            d = p.vel - max(float(np.mean(nb)), float(local[i]))
            if d >= ACCENT and p.vel > max(nb):
                stresses.append((d, Mark("accent", q.start)))
            elif d >= TENUTO and p.vel > max(nb) and q.dur >= beat:
                stresses.append((d, Mark("tenuto", q.start)))
    taken: set[int] = set()
    for d, m in sorted(stresses, key=lambda x: -x[0]):    # strongest first, one per bar
        b = m.start // _unit(arr)
        if b not in taken:
            taken.add(b)
            out.append(m)
    return out


def _slurs(line, pl, arr, stacc: set[int]) -> list[Mark]:
    bar = arr.bar_ticks

    def joins(i: int) -> bool:          # note i slurs into note i + 1
        a, b = line[i], line[i + 1]
        if b.start > a.end or a.breath_after:
            return False
        if a.start in stacc or b.start in stacc or a.pitch == b.pitch:
            return False
        if abs(b.pitch - a.pitch) > BIG_LEAP:
            return False
        p = pl[i]
        return not p.found or p.sounded >= LEGATO * p.slot

    runs, cur = [], [0]
    for i in range(len(line) - 1):
        if joins(i):
            cur.append(i + 1)
        else:
            runs.append(cur)
            cur = [i + 1]
    runs.append(cur)

    out: list[Mark] = []

    def place(run: list[int]) -> None:
        if len(run) < 2:
            return
        if line[run[-1]].start - line[run[0]].start <= SLUR_BARS * bar:
            out.append(Mark("slur", line[run[0]].start, line[run[-1]].start))
            return
        best, score = None, -math.inf
        for k in range(1, len(run) - 2):        # end the first slur on run[k]
            a, b = line[run[k]], line[run[k + 1]]
            if a.start - line[run[0]].start > SLUR_BARS * bar:
                break
            sc = a.dur / arr.beat_ticks + (1.5 if b.start % bar == 0 else 0) \
                + 0.1 * abs(b.pitch - a.pitch)
            if sc > score:
                best, score = k, sc
        if best is None:
            best = 1
        place(run[:best + 1])
        place(run[best + 1:])

    for run in runs:
        place(run)
    return out


def _tempo_marks(line, arr, grid: BeatGrid, ends: set[int]) -> list[Mark]:
    """Fermatas, rit. and a tempo, from where the beat slows or stops."""
    out: list[Mark] = []
    bar, beat = arr.bar_ticks, arr.beat_ticks
    per_unit = max(1, _unit(arr) // beat)       # beats in a (paired) bar
    last = line[-1]
    if last.dur >= max(beat, bar // 2):
        out.append(Mark("fermata", last.start))
    beats = np.asarray(grid._beats(), dtype=float)
    if len(beats) < 8:
        return out
    gaps = np.diff(beats)
    k0 = grid.first_downbeat                  # beat index of tick 0

    def gap_at(tick: float) -> int:          # index into gaps of the beat holding tick
        return int(math.floor(tick / beat)) + k0

    def ok(k: int) -> bool:
        return 0 <= k < len(gaps)

    # where the beat stops at a phrase end: a fermata on the held note
    for i in ends:
        q = line[i]
        if q is last or q.dur < beat:
            continue
        for k in range(gap_at(q.start), gap_at(q.end - 1) + 1):
            around = [gaps[j] for j in range(k - 4, k + 5) if ok(j) and j != k]
            if ok(k) and len(around) >= 4 and gaps[k] >= FERMATA_BEAT * float(np.median(around)):
                out.append(Mark("fermata", q.start))
                break

    # rit. into a phrase end, a tempo where the next phrase is back in time
    starts = {q.start for q in line}
    last_rit = -math.inf
    phrases = _split(line, at_breaths=False)
    for n, phrase in enumerate(phrases):
        end_q = line[phrase[-1]]
        k_end = gap_at(end_q.start)
        ref_ks = [k for k in range(k_end - 6 * per_unit, k_end - 2 * per_unit)
                  if ok(k)]
        if len(ref_ks) < 4 or not ok(k_end):
            continue
        ref = float(np.median(gaps[ref_ks]))
        r = gaps / ref
        k = k_end
        while ok(k - 1) and k - 1 > k_end - 2 * per_unit and r[k - 1] >= RIT_START:
            k -= 1
        span = list(range(k, k_end + 1))
        if (len(span) < 2 or float(np.mean(r[span[-2:]])) < RIT or r[k_end] < r[k]):
            continue
        tick = (k - k0) * beat
        first = next((q.start for q in line if q.start >= tick), None)
        if first is None or first < line[phrase[0]].start or first - last_rit < 4 * _unit(arr):
            continue
        out.append(Mark("text", first, end_q.end, "rit."))
        last_rit = first
        if n + 1 < len(phrases):
            nxt = line[phrases[n + 1][0]]
            ks = [j for j in range(gap_at(nxt.start), gap_at(nxt.start) + 4 * per_unit)
                  if ok(j)]
            if ks and float(np.median(r[ks])) < RIT and nxt.start in starts:
                out.append(Mark("text", nxt.start, value="a tempo"))
    return out


def _ornaments(line, pl, arr, avoid: set[int], taken: list[int]) -> list[Mark]:
    bar, beat = _unit(arr), arr.beat_ticks
    tonic = tonic_pc(arr.key_fifths, arr.key_mode)

    def long_enough(i: int, beats: float = 1.0) -> bool:
        return line[i].dur >= beats * beat and pl[i].secs >= ORN_SECONDS

    cands: list[tuple[int, int, Mark]] = []       # (priority, tick, mark)
    for phrase in _split(line, at_breaths=False):
        if len(phrase) >= 3:
            f, p = line[phrase[-1]], line[phrase[-2]]
            if (f.pitch % 12 == tonic and (p.pitch - tonic) % 12 in (2, 11)
                    and 0 < abs(p.pitch - f.pitch) <= 2 and long_enough(phrase[-2])):
                cands.append((0, p.start, Mark("trill", p.start)))
        if len(phrase) >= 4 and line[phrase[-1]].end - line[phrase[0]].start >= 2 * bar:
            inner = phrase[1:-1]
            top = max(inner, key=lambda i: (line[i].pitch, line[i].dur))
            if all(line[i].pitch < line[top].pitch for i in phrase if i != top) \
                    and long_enough(top):
                kind = "turn" if long_enough(top, 2) else "mordent"
                cands.append((1, line[top].start, Mark(kind, line[top].start)))
        for a, b in zip(phrase, phrase[1:]):
            if line[b].pitch - line[a].pitch >= 5 and long_enough(b) and _strong(line[b].start, arr):
                g = upper_neighbour(line[b].pitch, arr.key_fifths)
                if g <= 96:
                    cands.append((2, line[b].start, Mark("grace", line[b].start,
                                                         value=str(g))))
    out: list[Mark] = []
    limit = max(1, round((line[-1].end - line[0].start) / (ORN_BARS * bar)))
    for _, t, m in sorted(cands, key=lambda c: (c[0], c[1])):
        if len(taken) >= limit:
            break
        if t in avoid or any(abs(t - u) < ORN_BARS * bar for u in taken):
            continue
        out.append(m)
        taken.append(t)
        avoid.add(t)
    return out


def _character(dyn, pins, stacc, line, arr, felt: float, base: int) -> str:
    n_bars = max(1.0, (line[-1].end - line[0].start) / arr.bar_ticks)
    levels = {m.value for m in dyn}
    if len(stacc) >= 0.2 * len(line) and felt >= 100:
        return "leggiero"
    if base >= 4 and felt >= 120:
        return "con brio"
    if felt < 132 and (len(levels) >= 3 or len(pins) >= n_bars / 6):
        return "espressivo"
    if base <= 2 and felt < 108:
        return "dolce"
    return ""


# ---------------------------------------------------------------- entry point


def add_expression(arr: Arrangement, melody_notes: list[Note], grid: BeatGrid,
                   level: str = "expressive") -> list[Mark]:
    """The flute part's marks: the ones it has (from sheet music) plus, at
    "expressive" or "ornaments", what the performance and the rules add."""
    have = list(arr.expression)
    line = sorted((q for q in arr.flute if q.dur > 0), key=lambda q: q.start)
    if level not in LEVELS[1:] or not line:
        return have
    pl = _played(line, melody_notes, grid, arr.shift)
    kinds = {m.kind for m in have}
    on_note: dict[int, set[str]] = {}
    for m in have:
        on_note.setdefault(m.start, set()).add(m.kind)
    ends = {seg[-1] for seg in _split(line, at_breaths=True)}
    felt = felt_bpm(arr.bpm, arr.beat_ticks)
    base = _base_level(felt)
    new: list[Mark] = []

    dyn = [m for m in have if m.kind == "dynamic"]
    pins: list[Mark] = [m for m in have if m.kind in HAIRPINS]
    if not dyn:
        dyn = _dynamics(line, pl, arr, base)
        new += dyn
        if not pins:
            pins = _hairpins(line, pl, arr, dyn)
            new += pins

    arts = [m for m in _articulation(line, pl, arr, ends)
            if not on_note.get(m.start, set()) & set(ARTICULATIONS)]
    new += arts
    stacc = {m.start for m in have + arts if m.kind == "staccato"}

    slurs = [m for m in have if m.kind == "slur"]
    for s in _slurs(line, pl, arr, stacc):
        if not any(s.start <= o.end and o.start <= s.end for o in slurs):
            new.append(s)

    for m in _tempo_marks(line, arr, grid, ends):
        if m.kind == "fermata" and "fermata" in on_note.get(m.start, set()):
            continue
        if m.kind == "text" and any(o.kind == "text" and o.start == m.start for o in have):
            continue
        new.append(m)

    if not any(o.kind == "text" and o.start <= line[0].start for o in have):
        words = f"{tempo_word(felt)} {_character(dyn, pins, stacc, line, arr, felt, base)}"
        new.append(Mark("text", line[0].start, value=words.strip()))

    if level == "ornaments":
        avoid = stacc | {m.start for m in have if m.kind in ORNAMENTS}
        taken = sorted({m.start for m in have if m.kind in ORNAMENTS})
        new += _ornaments(line, pl, arr, avoid, taken)

    new = [Mark(m.kind, int(m.start), int(m.end), str(m.value)) for m in new]   # JSON-safe
    return have + sorted(new, key=lambda m: (m.start, m.kind))
