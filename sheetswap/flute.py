"""Fit a tune into the flute's range, and find an easier key to play it in.

Key signatures are counted in fifths: sharps positive, flats negative
(C major / A minor = 0, D major = 2, F major = -1).
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from .config import FLUTE_COMFY, FLUTE_HARD, TICKS_PER_BEAT
from .model import QNote

SWEET = 79                     # G5, the middle of the flute's best register
EDGE_COST = 1                  # per tick in the hard range but not the comfortable one
OUT_COST = 20                  # per tick outside the hard range
CENTER_COST = 0.02             # per note, per semitone the median sits away from SWEET
KEEP_SHARE = 0.9               # the octave option applies only if this share stays playable
PHRASE_REST = TICKS_PER_BEAT   # default: a rest of a beat (ticks) or more ends a phrase
EASY_MAX = 2                   # most sharps/flats in an "easy" key


def fit_to_flute(line: list[QNote], octave: int = 0,
                 beat_ticks: int = PHRASE_REST) -> tuple[list[QNote], int]:
    """Move `line` into the flute's range. Returns (new line, global shift).

    One octave shift for the whole line is picked by cost (time outside the
    comfortable and hard ranges, plus distance of the median from G5); the
    user's `octave` (-1/0/+1) is added if 90% of notes stay playable. Notes
    still outside C4..C7 are fixed a phrase at a time (phrases end at rests of
    a beat, `beat_ticks`, or more): the whole phrase moves by octaves if that
    fits it all, so its leaps stay intact; otherwise single notes move by
    octaves. The global shift is a multiple of 12; phrase moves are not included.
    """
    if not line:
        return [], 0
    k = min(range(-4, 5), key=lambda k: (_cost(line, 12 * k), abs(k), k))
    shift = 12 * k
    if octave:
        moved = shift + 12 * octave
        if sum(_playable(n.pitch + moved) for n in line) >= KEEP_SHARE * len(line):
            shift = moved
    out = [replace(n, pitch=n.pitch + shift) for n in line]
    for phrase in _phrases(out, beat_ticks):
        _fold(out, phrase)
        _fix_octave_slips(out, phrase)
    return out, shift


SLIP = 7    # semitones: a note this far from both neighbours, on the same side, is an octave slip


def _fix_octave_slips(line: list[QNote], idx: list[int]) -> None:
    """Move single notes that stick out an octave from both neighbours.

    Transcription (and octave folding) sometimes puts one note an octave off:
    E5 E4 E5. A real leap stays near at least one neighbour (C5 C6 B5), so
    only notes more than a 7th away from both, on the same side, move.
    """
    for a, i, b in zip(idx, idx[1:], idx[2:]):
        p, before, after = line[i].pitch, line[a].pitch, line[b].pitch
        up, down = p - before, p - after
        if abs(up) > SLIP and abs(down) > SLIP and (up > 0) == (down > 0):
            q = p - 12 if up > 0 else p + 12
            if _playable(q) and abs(q - before) <= SLIP and abs(q - after) <= SLIP:
                line[i] = replace(line[i], pitch=q)
    # a phrase's first or last note more than an octave from its neighbour is a
    # slip too (an opening octave leap, C4 to C5, is exactly 12 and stays)
    if len(idx) >= 2:
        for i, j in ((idx[0], idx[1]), (idx[-1], idx[-2])):
            p, other = line[i].pitch, line[j].pitch
            if abs(p - other) > 12:
                q = p - 12 if p > other else p + 12
                if _playable(q):
                    line[i] = replace(line[i], pitch=q)


def _playable(pitch: int) -> bool:
    return FLUTE_HARD[0] <= pitch <= FLUTE_HARD[1]


def _cost(line: list[QNote], shift: int) -> float:
    lo, hi = FLUTE_COMFY
    cost = 0.0
    for n in line:
        p = n.pitch + shift
        if not lo <= p <= hi:
            cost += n.dur * (EDGE_COST if _playable(p) else OUT_COST)
    med = median(n.pitch for n in line) + shift
    return cost + CENTER_COST * abs(med - SWEET) * len(line)


def _phrases(line: list[QNote], rest: int = PHRASE_REST) -> list[list[int]]:
    """Indices of `line`, in time order, split at rests of `rest`+ ticks."""
    phrases: list[list[int]] = []
    end = None
    for i in sorted(range(len(line)), key=lambda i: line[i].start):
        if end is None or line[i].start - end >= rest:
            phrases.append([])
            end = line[i].end
        phrases[-1].append(i)
        end = max(end, line[i].end)
    return phrases


def _fold(out: list[QNote], phrase: list[int]) -> None:
    pitches = [out[i].pitch for i in phrase]
    if all(_playable(p) for p in pitches):
        return
    for k in (12, -12, 24, -24, 36, -36):
        if all(_playable(p + k) for p in pitches):
            for i in phrase:
                out[i] = replace(out[i], pitch=out[i].pitch + k)
            return
    lo, hi = FLUTE_HARD
    for i in phrase:
        p = out[i].pitch
        while p < lo:
            p += 12
        while p > hi:
            p -= 12
        out[i] = replace(out[i], pitch=p)


def key_after_shift(fifths: int, shift: int) -> int:
    """Key signature after moving the music `shift` semitones, spelled with at
    most 6 accidentals; 6 sharps or 6 flats (a tie) is spelled with flats."""
    x = (fifths + 7 * shift) % 12
    return x if x < 6 else x - 12


def easy_key_shift(fifths: int) -> int:
    """Smallest shift in -5..6 semitones to a key with at most EASY_MAX
    sharps/flats (0 if the key already is one). Ties: fewer accidentals, then
    the downward shift."""
    if abs(fifths) <= EASY_MAX:
        return 0
    ok = [s for s in range(-5, 7) if abs(key_after_shift(fifths, s)) <= EASY_MAX]
    return min(ok, key=lambda s: (abs(s), abs(key_after_shift(fifths, s)), s))


# ---------------------------------------------------------------- polish for parts read from a score
#
# A part taken from sheet music keeps every note and rhythm of the original, so its
# shape is the composer's, written for another instrument. Two things a flutist (and
# an arranger) would still change, from docs/notation_rules.md:
# - the register, phrase by phrase (R-FLUTE-1/2): the low register below G4 is soft and
#   breathy and a piano covers it; f there is hardly possible; above A6 is shrill and
#   hard to play softly. fit_to_flute picks one octave for the whole tune, so a phrase
#   that sits low (the Moonlight sonata's tune when it drops into the bass) stays low.
#   phrase_octaves moves such phrases by an octave, whole (their intervals intact),
#   only where it clearly helps: each move costs, and so does a change of octave from
#   one phrase to the next. No 8va signs: the app never writes them (R-RANGE-3).
# - fast leaps of more than an octave (R-FLUTE-5): a broken-chord figure written for
#   a pianist's hand (an arpeggio over two octaves in 16ths) is played as its outline,
#   each note moved by octaves to stay within an octave of the one before.

LOW_WEAK = 67                  # G4: below it the flute is weak (R-FLUTE-1)
LOW_REG = 72                   # C5: C4-B4 is the low register
HIGH_BRIGHT = 89               # F6: from here up, bright and hard to play softly
TOP = 93                       # A6 (R-FLUTE-2, R-RANGE-3)
LOUD_DYN = {"f", "ff", "fff", "ffff", "sf", "sfz", "fz", "sfp"}
SOFT_DYN = {"p", "pp", "ppp", "pppp"}
# costs in bars' worth of notes in a poor register (_note_cost per tick, over a bar)
MOVE_COST = 0.1                # moving a phrase an octave from where fit_to_flute put it
CHANGE_COST = 0.3              # the next phrase in another octave, straight on (no rest between)
REST_CHANGE_COST = 0.15        # ... after a rest
FAR_CHANGE_COST = 0.05         # ... after a rest of two bars or more
FAST = TICKS_PER_BEAT // 2     # an 8th: notes this short count as fast (R-FLUTE-5)


def _note_cost(pitch: int, dyn: str) -> float:
    """How poorly a note sits on the flute (per tick), R-FLUTE-1/2."""
    if pitch < LOW_WEAK:
        return 1.0 + (1.0 if dyn in LOUD_DYN else 0.0)
    if pitch < LOW_REG:
        return 0.3 + (0.5 if dyn in LOUD_DYN else 0.0)
    if pitch < HIGH_BRIGHT:
        return 0.0
    if pitch < TOP:
        return 0.3
    return 1.0 + (1.0 if dyn in SOFT_DYN else 0.0)


def phrase_octaves(line: list[QNote], dynamics: list[tuple[int, str]] | None = None,
                   beat_ticks: int = PHRASE_REST, bar_ticks: int = 4 * TICKS_PER_BEAT,
                   cuts: set[int] | None = None) -> tuple[list[QNote], int]:
    """Move whole phrases by an octave where the register clearly calls for it (see
    the notes above). `dynamics`: (tick, "p"/"f"/...) in force from that tick. Phrases
    end at rests of a beat or more, and a new one starts at each tick in `cuts` (the
    score's slur ends, where the tune passes to the other hand). Returns (new line,
    phrases moved)."""
    from bisect import bisect_right
    if not line:
        return [], 0
    out = [replace(q) for q in line]
    levels = sorted(dynamics or [])
    marks = [t for t, _ in levels]

    def dyn(t: int) -> str:
        i = bisect_right(marks, t) - 1
        return levels[i][1] if i >= 0 else ""

    phrases = []
    for ph in _phrases(out, beat_ticks):
        cur = [ph[0]]
        for i in ph[1:]:
            if cuts and out[i].start in cuts:
                phrases.append(cur)
                cur = []
            cur.append(i)
        phrases.append(cur)
    shifts = (-12, 0, 12)
    costs = []
    for idx in phrases:
        row = []
        for k in shifts:
            if not all(_playable(out[i].pitch + k) for i in idx):
                row.append(float("inf"))
                continue
            c = sum(_note_cost(out[i].pitch + k, dyn(out[i].start)) * max(1, out[i].dur)
                    for i in idx) / bar_ticks           # in bars' worth of badly placed notes
            row.append(c + (MOVE_COST if k else 0.0))
        costs.append(row)
    # the cheapest octave for each phrase, a change of octave between phrases costing
    best = [list(costs[0])]
    back: list[list[int]] = []
    for p in range(1, len(phrases)):
        gap = out[phrases[p][0]].start - max(out[i].end for i in phrases[p - 1])
        change = (FAR_CHANGE_COST if gap >= 2 * bar_ticks
                  else REST_CHANGE_COST if gap >= beat_ticks else CHANGE_COST)
        row, ptr = [], []
        for j in range(3):
            i = min(range(3), key=lambda i: (best[-1][i] + (change if i != j else 0.0), i != j))
            ptr.append(i)
            row.append(best[-1][i] + (change if i != j else 0.0) + costs[p][j])
        best.append(row)
        back.append(ptr)
    j = min(range(3), key=lambda j: (best[-1][j], j != 1))
    choice = [j]
    for ptr in reversed(back):
        j = ptr[j]
        choice.append(j)
    choice.reverse()
    moved = 0
    for idx, j in zip(phrases, choice):
        k = shifts[j]
        if k:
            moved += 1
            for i in idx:
                out[i] = replace(out[i], pitch=out[i].pitch + k)
    return out, moved


def outline_leaps(line: list[QNote], fast: int = FAST) -> tuple[list[QNote], int]:
    """Fast notes (`fast` ticks or shorter, played legato) leaping more than an octave
    from the note before move by octaves to land within an octave of it (R-FLUTE-5):
    the figure keeps its notes, in a span a flute can play. Returns (line, notes moved)."""
    out = sorted((replace(q) for q in line), key=lambda q: q.start)
    moved = 0
    for i in range(1, len(out)):
        a, b = out[i - 1], out[i]
        if b.start - a.end >= 3 or max(a.dur, b.dur) > fast:
            continue
        p = b.pitch
        while p - a.pitch > 12 and _playable(p - 12):
            p -= 12
        while a.pitch - p > 12 and _playable(p + 12):
            p += 12
        if p != b.pitch:
            out[i] = replace(b, pitch=p)
            moved += 1
    return out, moved
