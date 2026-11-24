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
PHRASE_REST = TICKS_PER_BEAT   # a rest at least this long (ticks) ends a phrase
EASY_MAX = 2                   # most sharps/flats in an "easy" key


def fit_to_flute(line: list[QNote], octave: int = 0) -> tuple[list[QNote], int]:
    """Move `line` into the flute's range. Returns (new line, global shift).

    One octave shift for the whole line is picked by cost (time outside the
    comfortable and hard ranges, plus distance of the median from G5); the
    user's `octave` (-1/0/+1) is added if 90% of notes stay playable. Notes
    still outside C4..C7 are fixed a phrase at a time (phrases end at rests of
    a beat or more): the whole phrase moves by octaves if that fits it all,
    so its leaps stay intact; otherwise single notes move by octaves.
    The global shift is a multiple of 12; phrase moves are not included.
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
    for phrase in _phrases(out):
        _fold(out, phrase)
    return out, shift


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


def _phrases(line: list[QNote]) -> list[list[int]]:
    """Indices of `line`, in time order, split at rests of PHRASE_REST+ ticks."""
    phrases: list[list[int]] = []
    end = None
    for i in sorted(range(len(line)), key=lambda i: line[i].start):
        if end is None or line[i].start - end >= PHRASE_REST:
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
