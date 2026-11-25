"""Turn a melody in seconds into notes on the beat grid, and add breath marks.

All the work happens in beat space (grid.to_ticks), so a song that speeds up
or slows down still lands on the beat.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .config import TICKS_PER_BEAT
from .model import BeatGrid, Note, QNote

SIXTEENTH = 3
TRIPLET = 4
EIGHTH = 6
TRIPLET_GAIN = 0.4   # triplets must cut a beat's snapping error by more than this
EIGHTH_TOL = 1.25    # ticks: a beat is written in 8ths if every onset is this close to one
QUARTER = TICKS_PER_BEAT
LONG_NOTE = 9        # ticks (3/4 of a beat): a beat's only note this long snaps to the beat
LEGATO_GAP = TICKS_PER_BEAT  # gaps shorter than a beat are held through (a flute line is
                             # legato; a pianist's lifted fingers are not rests)


def snap(ticks: float, step: int) -> int:
    """Nearest multiple of step (halves round up)."""
    return int(math.floor(ticks / step + 0.5)) * step


def _beat_of(ticks: float) -> int:
    return math.floor(ticks / TICKS_PER_BEAT)


def _beat_steps(notes: list[tuple[float, float]]) -> dict[int, int]:
    """The coarsest grid each beat's onsets fit: 8ths, else 8th triplets, else 16ths.

    Real playing (rubato, a late finger) lands between grid lines; always
    snapping to 16ths turns that into fussy dotted-16th rhythms, so a beat
    only gets 16ths when its notes really are that fast. A beat holding one
    long note (a singer or pianist leaning into it a 16th early or late) puts
    it on the beat when it's that close, and on an 8th otherwise.
    """
    by_beat: dict[int, list[tuple[float, float]]] = {}
    for s, e in notes:
        by_beat.setdefault(_beat_of(s), []).append((s, e))
    steps = {}
    for beat, se in by_beat.items():
        ss = [s for s, _ in se]
        if len(se) == 1 and se[0][1] - se[0][0] >= LONG_NOTE:
            s = ss[0]
            steps[beat] = QUARTER if abs(s - snap(s, QUARTER)) <= SIXTEENTH else EIGHTH
            continue
        on8 = [snap(s, EIGHTH) for s in ss]
        if max(abs(s - q) for s, q in zip(ss, on8)) <= EIGHTH_TOL and len(set(on8)) == len(on8):
            steps[beat] = EIGHTH
            continue
        if len(ss) < 2:
            continue
        err16 = sum(abs(s - snap(s, SIXTEENTH)) for s in ss)
        err3 = sum(abs(s - snap(s, TRIPLET)) for s in ss)
        if err3 < (1 - TRIPLET_GAIN) * err16:
            steps[beat] = TRIPLET
    return steps


def quantize_line(notes: list[Note], grid: BeatGrid, simple: bool = False) -> list[QNote]:
    """Monophonic notes (seconds) -> sorted, non-overlapping QNotes (ticks).

    Onsets snap per beat to 16ths or 8th triplets (simple: 8ths). Two notes
    landing on the same tick keep the louder one. A note ends at its snapped
    end; if that leaves less than the shortest note before the next onset it
    is held to that onset instead.
    """
    if not notes:
        return []
    min_dur = EIGHTH if simple else SIXTEENTH
    default = EIGHTH if simple else SIXTEENTH

    raw = sorted(
        ((grid.to_ticks(n.start), grid.to_ticks(max(n.end, n.start)), n) for n in notes),
        key=lambda x: (x[0], -x[2].velocity),
    )
    steps = {} if simple else _beat_steps([(s, e) for s, e, _ in raw])

    def step_at(t: float) -> int:
        return steps.get(_beat_of(t), default)

    # snap onsets; on a collision keep the louder note (then the longer one)
    kept: dict[int, tuple[float, Note]] = {}
    for s, e, n in raw:
        st = snap(s, step_at(s))
        old = kept.get(st)
        if old is None or (n.velocity, n.dur) > (old[1].velocity, old[1].dur):
            kept[st] = (e, n)
    starts = sorted(kept)

    out = []
    for i, st in enumerate(starts):
        e, n = kept[st]
        nxt = starts[i + 1] if i + 1 < len(starts) else None
        if nxt is not None and nxt - e < max(min_dur, LEGATO_GAP):
            en = nxt                       # short gap (or overlap): hold to the next note
        else:
            en = max(snap(e, step_at(e)), st + min_dur)
            if nxt is not None and nxt - en < min_dur:
                en = nxt
        out.append(QNote(n.pitch, st, en - st, n.velocity))
    return out


def add_breath_marks(line: list[QNote], beats_per_bar: int) -> list[QNote]:
    """Mark places to breathe in long phrases that have no rest.

    A phrase is a run of notes without a rest of an 8th or longer. In a
    phrase longer than 4 bars, each 2-bar window (counted from the first
    downbeat) gets breath_after on its longest note (latest one on a tie).
    Skipped: the window holding the phrase end, and notes ending within a bar
    of the phrase start or end. Returns copies; the input is not changed.
    """
    out = [replace(n) for n in sorted(line, key=lambda n: n.start)]
    if not out:
        return out
    bar = beats_per_bar * TICKS_PER_BEAT
    window = 2 * bar

    phrases, cur = [], [0]
    for i in range(1, len(out)):
        if out[i].start - out[i - 1].end >= EIGHTH:
            phrases.append(cur)
            cur = []
        cur.append(i)
    phrases.append(cur)

    for idx in phrases:
        p_start, p_end = out[idx[0]].start, out[idx[-1]].end
        if p_end - p_start <= 4 * bar:
            continue
        ws = math.floor(p_start / window) * window
        while ws + window < p_end:
            we = ws + window
            cands = [i for i in idx[:-1]
                     if ws < out[i].end <= we
                     and p_start + bar <= out[i].end <= p_end - bar]
            if cands:
                best = max(cands, key=lambda i: (out[i].dur, out[i].end))
                out[best].breath_after = True
            ws = we
    return out
