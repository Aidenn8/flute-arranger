"""Key and chord detection from transcribed notes.

Key: Krumhansl-Kessler profiles correlated with a weighted pitch-class histogram.
Chords: one major or minor triad per span of the beat grid, matched against
a duration x velocity chroma. Spans run in beat space (ticks), so a drifting
tempo doesn't move the chord changes.
"""
from __future__ import annotations

from collections import defaultdict
from math import ceil, floor, sqrt

from .config import TICKS_PER_BEAT
from .model import BeatGrid, Note

# Krumhansl-Kessler key profiles; index 0 is the tonic.
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Triad templates: weight of root, third, fifth. The root counts most, so
# C+E reads as C major rather than A minor unless something says otherwise.
TONE_WEIGHTS = (1.0, 0.9, 0.8)
THIRDS = (("maj", 4), ("min", 3))
TEMPLATES = [(root, quality, ((root, TONE_WEIGHTS[0]),
                              ((root + third) % 12, TONE_WEIGHTS[1]),
                              ((root + 7) % 12, TONE_WEIGHTS[2])))
             for root in range(12) for quality, third in THIRDS]

MIN_FILL = 16.0   # a span needs this much velocity x ticks per tick to count as sounding
BASS_NEAR = 0.85  # the bass may pick any chord scoring at least this share of the best
STICKY = 0.9      # keep the previous chord unless the new best beats it clearly


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


def _chroma_by_span(notes: list[Note], grid: BeatGrid, span: int):
    """span index -> (12-bin velocity x ticks chroma, ticks of sounding notes)."""
    chroma: dict[int, list[float]] = defaultdict(lambda: [0.0] * 12)
    sounding: dict[int, float] = defaultdict(float)
    for n in notes:
        s, e = grid.to_ticks(n.start), grid.to_ticks(n.end)
        if e <= s:
            continue
        for k in range(floor(s / span), ceil(e / span)):
            overlap = min(e, (k + 1) * span) - max(s, k * span)
            if overlap > 0:
                chroma[k][n.pitch % 12] += overlap * n.velocity
                sounding[k] += overlap
    return chroma, sounding


def _score(chroma: list[float]) -> dict[tuple[int, str], float]:
    return {(root, quality): sum(chroma[pc] * w for pc, w in tones)
            for root, quality, tones in TEMPLATES}


def chord_spans(notes: list[Note], grid: BeatGrid, span: int,
                bass: list[Note] | None = None) -> list[tuple[int, int, str, float]]:
    """(span index, root_pc, "maj"|"min", mean velocity) for every span that
    has a chord; silent spans are left out. Span k covers ticks
    [k*span, (k+1)*span). Bass notes, when given, join the chroma and pick
    the root among the chords that fit almost as well as the best one."""
    bass = list(bass or [])
    chroma, sounding = _chroma_by_span(list(notes) + bass, grid, span)
    bass_chroma, _ = _chroma_by_span(bass, grid, span)
    need = MIN_FILL * span
    out = []
    prev = None                                   # (span index, chord)
    for k in sorted(chroma):
        energy = sum(chroma[k])
        if energy < need:
            continue
        scores = _score(chroma[k])
        best = max(scores, key=scores.get)        # first max: lower root, major
        choice, decided = best, False
        low = bass_chroma.get(k)
        if low is not None and sum(low) >= need:
            bass_pc = max(range(12), key=low.__getitem__)
            fits = [c for c, s in scores.items()
                    if c[0] == bass_pc and s >= BASS_NEAR * scores[best]]
            if fits:
                choice, decided = max(fits, key=scores.get), True
        if (not decided and prev is not None and prev[0] == k - 1
                and scores[prev[1]] >= STICKY * scores[best]):
            choice = prev[1]
        out.append((k, choice[0], choice[1], energy / sounding[k]))
        prev = (k, choice)
    return out


def detect_chords(notes: list[Note], grid: BeatGrid, span: int) -> list[tuple[int, int, int, str]]:
    """(start_tick, dur_ticks, root_pc, "maj"|"min"), one per span with sound.
    The same chord in back-to-back spans of one bar is merged into one row;
    rows never cross a bar line, so the chord is struck again each bar."""
    bar = grid.beats_per_bar * TICKS_PER_BEAT
    rows: list[list] = []
    for k, root, quality, _ in chord_spans(notes, grid, span):
        start = k * span
        if rows:
            last = rows[-1]
            if (last[0] + last[1] == start and (last[2], last[3]) == (root, quality)
                    and last[0] // bar == (start + span - 1) // bar):
                last[1] += span
                continue
        rows.append([start, span, root, quality])
    return [tuple(r) for r in rows]
