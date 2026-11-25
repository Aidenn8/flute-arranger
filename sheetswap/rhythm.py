"""Turn a melody in seconds into notes on the beat grid, and add breath marks.

All the work happens in beat space (grid.to_ticks), so a song that speeds up
or slows down still lands on the beat.

The beat is grid.beat_ticks long: a quarter (48), a dotted quarter (72, in
6/8, 9/8, 12/8) or an eighth (24, in 3/8). A quarter beat splits into 8ths,
16ths or 8th triplets; a dotted beat into three 8ths or six 16ths (no
triplets: three even notes in a dotted beat are plain 8ths); an eighth beat
into two 16ths. (The tick grid is finer than that, for sheet music with 32nds
and 16th triplets; a recording is read no finer than 16ths.)

Onsets are read a beat at a time as rhythm patterns (the places where notes
start in the beat), the way a musician reads them: the likeliest pattern
given how far each onset is from it and how common the pattern is. How
common is learned from the piece itself, on top of the usual vocabulary of
printed music (VOCAB), so a minuet in 8ths keeps its 8ths where the playing
wavers, and a piece in 16th runs reads its 16ths. Where a beat sits in the bar
counts too: beat 1 (then the middle of the bar) rarely starts without a note
(DOWN_EMPTY, MID_EMPTY).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace
from itertools import combinations
from statistics import median

from .model import (DOTTED_BEAT, EIGHTH_BEAT, QUARTER_BEAT, SIXTEENTH, TRIPLET_8TH, BeatGrid,
                    Note, QNote)

TRIPLET = TRIPLET_8TH
EIGHTH = QUARTER_BEAT // 2
QUARTER = QUARTER_BEAT
# The timing constants below were tuned on a grid of 12 ticks a quarter; this
# is one of those ticks (a third of a 16th) on today's grid.
TICK12 = QUARTER_BEAT / 12

# How common each beat pattern (onset places, ticks from the beat) is in
# printed music, as pseudo-counts; any other pattern on the beat's grid
# counts RARE. A quarter beat: held, a quarter, two 8ths, an 8th after an 8th
# rest or tie, four 16ths, 8th + two 16ths, two 16ths + 8th, dotted 8th +
# 16th, 16th rest + three 16ths, 16th + dotted 8th, a triplet, quarter + 8th
# of a triplet.
S, R = SIXTEENTH, TRIPLET
VOCAB = {
    QUARTER_BEAT: {(): 4, (0,): 8, (0, 2 * S): 6, (2 * S,): 1, (0, S, 2 * S, 3 * S): 4,
                   (0, 2 * S, 3 * S): 2, (0, S, 2 * S): 2, (0, 3 * S): 1.5, (S, 2 * S, 3 * S): 0.5,
                   (0, S): 0.3, (0, R, 2 * R): 1, (0, 2 * R): 0.2},
    # a dotted beat: held, dotted quarter, three 8ths, quarter + 8th, 8th +
    # quarter, siciliano (dotted 8th, 16th, 8th), six 16ths, 8ths mixed with 16ths
    DOTTED_BEAT: {(): 4, (0,): 8, (0, 2 * S, 4 * S): 6, (0, 4 * S): 3, (0, 2 * S): 1, (2 * S,): 0.5,
                  (2 * S, 4 * S): 0.5, (4 * S,): 0.3, (0, 3 * S, 4 * S): 1,
                  (0, S, 2 * S, 3 * S, 4 * S, 5 * S): 3, (0, 2 * S, 4 * S, 5 * S): 0.5,
                  (0, S, 2 * S, 4 * S): 0.5, (0, 2 * S, 3 * S, 4 * S): 0.5,
                  (0, 2 * S, 3 * S, 4 * S, 5 * S): 0.3, (0, S, 2 * S, 3 * S, 4 * S): 0.3},
    # an eighth beat (3/8 in three): held, an 8th, two 16ths, 16th rest + 16th
    EIGHTH_BEAT: {(): 4, (0,): 8, (0, S): 4, (S,): 0.5},
}
del S, R
RARE = 0.05
PRIOR_BEATS = 24     # the vocabulary weighs as much as this many of the piece's own beats
ROUNDS = 4           # times the piece's own pattern counts are learned again
SPREAD = (0.8 * TICK12, 1.25 * TICK12, 2.5 * TICK12)   # timing spread of the playing, in
                                                         # ticks (least, first guess, most)
DROP = 15.0          # cost of leaving an onset out (when a beat can't hold them all) ...
GRACE_DROP = 5.0     # ... or of a grace note: under a 16th long, and under half as long as a note
                     # starting less than a 16th from it
MERGE = 1.5 * TICK12  # ticks: onsets closer than this are one note (the louder one stays)
PUSH = 0.25          # of a beat: an onset this close before a beat may be its downbeat
TRIPLET_CLEAR = 1.0 * TICK12  # ticks: a beat clearly in triplets has 3+ onsets this close to
                              # its triplet 8ths ...
TRIPLET_SHARE = 0.1  # ... and triplets are read only if this share of beats with 2+ onsets are
# The meter's hierarchy (docs/notation_rules.md R-METER-3, R-PHR-2): beat 1 is
# the strongest place in the bar, then the middle of a 4/4 or 12/8 bar, then the
# other beats. A tune starts a note on beat 1 far more often than it ties into it
# or syncopates across it, so on beat 1 a pattern with no onset at the beat's
# start counts this much less likely (on the middle beat MID_EMPTY):
DOWN_EMPTY = 0.5
MID_EMPTY = 0.75
# Gaps shorter than a beat are held through (a flute line is legato; a
# pianist's lifted fingers are not rests).
SHORT_BAR = 3        # felt beats: shorter bars (2/4, 6/8, 3/8) get breath windows of 4 bars


def snap(ticks: float, step: int) -> int:
    """Nearest multiple of step (halves round up)."""
    return int(math.floor(ticks / step + 0.5)) * step


def _beat_of(ticks: float, unit: int = QUARTER) -> int:
    return math.floor(ticks / unit)


def uses_triplets(onsets: list[float], unit: int) -> bool:
    """Whether a piece plays 8th triplets (onsets in ticks).

    Played 16ths and 8ths waver enough to fit a triplet now and then about
    as well as their own grid, so a beat only counts as clear evidence when
    it holds three or more onsets, all within TRIPLET_CLEAR ticks of the
    triplet grid with the 2nd and 3rd triplet 8ths both there, and that grid
    fits at least twice as well as 16ths. The piece reads triplets when
    TRIPLET_SHARE of its beats with two or more onsets are such beats (none
    of the reference recordings without triplets has more than one).
    """
    if unit != QUARTER_BEAT:
        return False
    by_beat: dict[int, list[float]] = {}
    for x in onsets:
        b = _beat_of(x, unit)
        by_beat.setdefault(b, []).append(x - b * unit)
    busy = [xs for xs in by_beat.values() if len(xs) >= 2]
    clear = 0
    for xs in busy:
        on3 = [snap(x, TRIPLET) for x in xs]
        err3 = sum(abs(x - q) for x, q in zip(xs, on3))
        err16 = sum(abs(x - snap(x, SIXTEENTH)) for x in xs)
        if (len(xs) >= 3 and {TRIPLET, 2 * TRIPLET} <= set(on3) and err3 < 0.5 * err16
                and all(abs(x - q) <= TRIPLET_CLEAR for x, q in zip(xs, on3))):
            clear += 1
    return clear > 0 and clear >= TRIPLET_SHARE * len(busy)


def _lattices(unit: int, simple: bool, triplets: bool) -> list[tuple[int, ...]]:
    """The finest grids a beat is read on: 16ths (and 8th triplets in a
    quarter beat of a piece that uses them); 8ths only when simple."""
    lats = [tuple(range(0, unit, SIXTEENTH))]
    if unit == QUARTER_BEAT and triplets:
        lats.append((0, TRIPLET, 2 * TRIPLET))
    if simple:
        lats = [tuple(p for p in lats[0] if p % EIGHTH == 0)]
    return lats


def _vocabulary(unit: int, simple: bool, triplets: bool) -> dict[tuple[int, ...], float]:
    """Every pattern a beat may hold, with its prior pseudo-count. Triplet
    notes come at least two to a beat (a lone one would be a 16th's place
    played unevenly)."""
    known = VOCAB.get(unit, {})
    pats: dict[tuple[int, ...], float] = {}
    for k, lat in enumerate(_lattices(unit, simple, triplets)):
        for r in range(2 if k else 0, len(lat) + 1):
            for c in combinations(lat, r):
                pats.setdefault(c, known.get(c, RARE))
    return pats


def _fit(xs: list[float], pat: tuple[int, ...]) -> tuple[float, list[int | None]]:
    """Read onsets xs (ticks from the beat, in order) as pattern pat, leaving
    out len(xs) - len(pat) of them the best way. Returns (squared timing
    error, each onset's place or None if left out)."""
    n, m = len(xs), len(pat)
    if n == m:
        return sum((x - p) ** 2 for x, p in zip(xs, pat)), list(pat)
    inf = float("inf")
    d = [[inf] * (m + 1) for _ in range(n + 1)]
    d[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(0, m - n + i), min(i, m) + 1):
            best = d[i - 1][j]                         # onset i left out
            if j:
                best = min(best, d[i - 1][j - 1] + (xs[i - 1] - pat[j - 1]) ** 2)
            d[i][j] = best
    places: list[int | None] = [None] * n
    j = m
    for i in range(n, 0, -1):
        if j and d[i][j] == d[i - 1][j - 1] + (xs[i - 1] - pat[j - 1]) ** 2:
            places[i - 1] = pat[j - 1]
            j -= 1
    return d[n][m], places


def _options(xs: list[float], grace: list[bool], by_size: dict[int, list[tuple[int, ...]]],
             most: int) -> list[tuple[tuple[int, ...], float, float, list[int | None]]]:
    """Every way to read one beat's onsets xs (ticks from the beat, in order):
    (pattern, squared timing error, cost of the onsets left out, places).
    All onsets are read, or all but one (DROP; GRACE_DROP for a grace note,
    `grace`), or, when the beat can't hold them all, the best `most`."""
    n = len(xs)
    if n > most:
        out = []
        for p in by_size[most]:
            ssd, places = _fit(xs, p)
            out.append((p, ssd, DROP * (n - most), places))
        return out
    out = [(p, sum((x - q) ** 2 for x, q in zip(xs, p)), 0.0, list(p))
           for p in by_size.get(n, ())]
    for j in range(n):
        rest = xs[:j] + xs[j + 1:]
        cost = GRACE_DROP if grace[j] else DROP
        for p in by_size.get(n - 1, ()):
            out.append((p, sum((x - q) ** 2 for x, q in zip(rest, p)), cost,
                        list(p[:j]) + [None] + list(p[j:])))
    return out


def _read_rhythm(onsets: list[float], unit: int, simple: bool = False,
                 grace: list[bool] | None = None, beats_per_bar: int = 0
                 ) -> tuple[list[int | None], dict[int, tuple[int, ...]]]:
    """Each onset's tick (None = left out) and each beat's pattern.

    The line is read a beat at a time: the pattern P and the onsets' places
    in it that minimise
        -log p(P) + sum (onset - place)^2 / (2 spread^2) + cost of onsets left out
    (DROP, or GRACE_DROP for onsets flagged in `grace`). An onset within PUSH
    of the next beat may instead be that beat's downbeat (a note leaned into
    a little early), so the beats are read together, by dynamic programming
    over that choice. p(P) is VOCAB, worth PRIOR_BEATS beats, plus the
    patterns this piece was read as last round; the spread is the piece's
    (robust) timing error from last round. A few rounds learn what the piece
    uses: 8ths, 16th runs, triplets.

    With beats_per_bar (beat 0 is the first downbeat) the pattern's chance
    also follows the meter's hierarchy: times DOWN_EMPTY / MID_EMPTY for a
    pattern with no onset at the start of beat 1 / of the middle beat of a
    4/4 or 12/8 bar (a note held or tied into it, a syncopation).
    """
    grace = grace or [False] * len(onsets)
    vocab = _vocabulary(unit, simple, not simple and uses_triplets(onsets, unit))
    by_size: dict[int, list[tuple[int, ...]]] = {}
    for p in vocab:
        by_size.setdefault(len(p), []).append(p)
    most = max(by_size)
    natural: dict[int, list[int]] = {}
    for i, x in enumerate(onsets):
        natural.setdefault(_beat_of(x, unit), []).append(i)
    beats = sorted(set(natural) | {b + 1 for b in natural})

    def near_next(idx: list[int], b: int) -> bool:
        return bool(idx) and (b + 1) * unit - onsets[idx[-1]] <= PUSH * unit

    # every way to read each beat: (an onset came in, its last goes out) -> (onsets, options)
    table: dict[int, dict[tuple[bool, bool], tuple[list[int], list]]] = {}
    for b in beats:
        own, prev = natural.get(b, []), natural.get(b - 1, [])
        table[b] = {}
        for came in (False, True) if near_next(prev, b - 1) else (False,):
            for goes in (False, True) if near_next(own, b) else (False,):
                idx = ([prev[-1]] if came else []) + (own[:-1] if goes else own)
                xs = [onsets[i] - b * unit for i in idx]
                table[b][came, goes] = (idx, _options(xs, [grace[i] for i in idx], by_size, most))

    def chosen(b: int, way: tuple[bool, bool, int]):
        came, goes, k = way
        idx, opts = table[b][came, goes]
        return idx, opts[k]

    bpb = max(beats_per_bar, 1)
    empty_factor = {0: DOWN_EMPTY} if beats_per_bar else {}
    if beats_per_bar >= 4 and beats_per_bar % 2 == 0:
        empty_factor[beats_per_bar // 2] = MID_EMPTY

    counts: Counter = Counter()
    spread = SPREAD[1]
    prior = sum(vocab.values())
    picks: dict[int, tuple[bool, bool, int]] = {}
    for _ in range(ROUNDS):
        seen = sum(counts.values())
        overall = {p: (PRIOR_BEATS * c / prior + counts[p]) / (PRIOR_BEATS + seen)
                   for p, c in vocab.items()}
        costs = {pos: {p: -math.log(q * (f if 0 not in p else 1.0)) for p, q in overall.items()}
                 for pos, f in empty_factor.items()}
        plain = {p: -math.log(q) for p, q in overall.items()}

        var2 = 2 * spread ** 2
        cost = {False: 0.0}                        # so far, by whether the last beat pushed out
        back = []
        for b in beats:
            new: dict[bool, float] = {}
            arg: dict[bool, tuple[bool, int]] = {}
            cost_of = costs.get(b % bpb, plain) if beats_per_bar else plain
            for (came, goes), (_, opts) in table[b].items():
                if came not in cost:
                    continue
                c, k = min((cost_of[p] + ssd / var2 + left_out, k)
                           for k, (p, ssd, left_out, _) in enumerate(opts))
                if goes not in new or cost[came] + c < new[goes]:
                    new[goes], arg[goes] = cost[came] + c, (came, k)
            back.append((b, arg))
            cost = new
        new_picks, goes = {}, False
        for b, arg in reversed(back):
            came, k = arg[goes]
            new_picks[b] = (came, goes, k)
            goes = came
        if new_picks == picks:
            break
        picks = new_picks
        counts = Counter(chosen(b, way)[1][0] for b, way in picks.items())
        counts[()] = beats[-1] - beats[0] + 1 - sum(v for p, v in counts.items() if p)
        devs = [abs(b * unit + pl - onsets[i]) for b, way in picks.items()
                for i, pl in zip(chosen(b, way)[0], chosen(b, way)[1][3]) if pl is not None]
        spread = min(max(1.4826 * median(devs), SPREAD[0]), SPREAD[2]) if devs else SPREAD[1]

    ticks: list[int | None] = [None] * len(onsets)
    patterns: dict[int, tuple[int, ...]] = {}
    for b, way in picks.items():
        idx, (pattern, _, _, places) = chosen(b, way)
        patterns[b] = pattern
        for i, pl in zip(idx, places):
            if pl is not None:
                ticks[i] = b * unit + pl
    return ticks, patterns


def _end_step(pattern: tuple[int, ...], unit: int, simple: bool) -> int:
    """The grid a note's end snaps to in a beat read as `pattern`: the
    pattern's own (16ths, triplets), else 8ths (16ths in an eighth beat)."""
    if simple:
        return EIGHTH
    if any(p % TRIPLET == 0 and p % SIXTEENTH for p in pattern):
        return TRIPLET
    if any(p % EIGHTH for p in pattern) or unit == EIGHTH_BEAT:
        return SIXTEENTH
    return EIGHTH


def quantize_line(notes: list[Note], grid: BeatGrid, simple: bool = False) -> list[QNote]:
    """Monophonic notes (seconds) -> sorted, non-overlapping QNotes (ticks).

    Onsets are read as rhythm patterns (_read_rhythm): 16ths or 8th
    triplets where the piece uses them, 8ths only when simple. Onsets closer
    than MERGE keep the louder note; a note under half as long as one starting less
    than a 16th from it may be left out as a grace note (a sung scoop, an
    ornament) when that reads more simply. A note ends at its end snapped to
    its beat's grid; if that leaves less than the shortest note before the
    next onset it is held to that onset instead.
    """
    if not notes:
        return []
    unit = grid.beat_ticks
    min_dur = EIGHTH if simple else SIXTEENTH

    raw = sorted(
        ((grid.to_ticks(n.start), grid.to_ticks(max(n.end, n.start)), n) for n in notes),
        key=lambda x: (x[0], -x[2].velocity),
    )
    merged: list[tuple[float, float, Note]] = []
    for s, e, n in raw:
        if merged and s - merged[-1][0] < MERGE:
            if (n.velocity, n.dur) > (merged[-1][2].velocity, merged[-1][2].dur):
                merged[-1] = (s, e, n)
            continue
        merged.append((s, e, n))
    grace = [e - s < SIXTEENTH and any(0 <= j < len(merged) and abs(merged[j][0] - s) < SIXTEENTH
                                       and merged[j][2].dur > 2 * n.dur for j in (i - 1, i + 1))
             for i, (s, e, n) in enumerate(merged)]
    ticks, patterns = _read_rhythm([s for s, _, _ in merged], unit, simple, grace,
                                   grid.beats_per_bar)

    def step_at(t: float) -> int:
        return _end_step(patterns.get(_beat_of(t, unit), ()), unit, simple)

    kept: dict[int, tuple[float, Note]] = {}
    for (s, e, n), st in zip(merged, ticks):
        if st is not None:
            kept[st] = (e, n)
    starts = sorted(kept)

    out = []
    for i, st in enumerate(starts):
        e, n = kept[st]
        nxt = starts[i + 1] if i + 1 < len(starts) else None
        if nxt is not None and nxt - e < max(min_dur, unit):
            en = nxt                       # short gap (or overlap): hold to the next note
        else:
            en = max(snap(e, step_at(e)), st + min_dur)
            if nxt is not None and nxt - en < min_dur:
                en = nxt
        out.append(QNote(n.pitch, st, en - st, n.velocity))
    return out


def add_breath_marks(line: list[QNote], beats_per_bar: int,
                     beat_ticks: int = QUARTER) -> list[QNote]:
    """Mark places to breathe in long phrases that have no rest.

    A phrase is a run of notes without a rest of an 8th or longer. Phrases
    are cut into windows of 2 bars (4 bars of 2/4, 6/8 or 3/8, which hold
    fewer than SHORT_BAR felt beats: quarters, or dotted quarters in 8th-note
    meters), counted from the first downbeat. In a phrase longer than two
    windows, each window gets breath_after on its longest note (latest one on
    a tie). Skipped: the window holding the phrase end, and notes ending
    within half a window of the phrase start or end. Returns copies; the
    input is not changed.
    """
    out = [replace(n) for n in sorted(line, key=lambda n: n.start)]
    if not out:
        return out
    bar = beats_per_bar * beat_ticks
    felt = QUARTER if beat_ticks == QUARTER else DOTTED_BEAT
    window = (2 if bar >= SHORT_BAR * felt else 4) * bar
    margin = window // 2

    phrases, cur = [], [0]
    for i in range(1, len(out)):
        if out[i].start - out[i - 1].end >= EIGHTH:
            phrases.append(cur)
            cur = []
        cur.append(i)
    phrases.append(cur)

    for idx in phrases:
        p_start, p_end = out[idx[0]].start, out[idx[-1]].end
        if p_end - p_start <= 2 * window:
            continue
        ws = math.floor(p_start / window) * window
        while ws + window < p_end:
            we = ws + window
            cands = [i for i in idx[:-1]
                     if ws < out[i].end <= we
                     and p_start + margin <= out[i].end <= p_end - margin]
            if cands:
                best = max(cands, key=lambda i: (out[i].dur, out[i].end))
                out[best].breath_after = True
            ws = we
    return out
