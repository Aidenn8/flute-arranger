"""Turn a melody in seconds into notes on the beat grid, and add breath marks.

All the work happens in beat space (grid.to_ticks), so a song that speeds up
or slows down still lands on the beat.

The beat is grid.beat_ticks long: a quarter (12), a dotted quarter (18, in
6/8, 9/8, 12/8) or an eighth (6, in 3/8). A quarter beat splits into 8ths,
16ths or 8th triplets; a dotted beat into three 8ths or six 16ths (no
triplets: three even notes in a dotted beat are plain 8ths); an eighth beat
into two 16ths.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .model import DOTTED_BEAT, EIGHTH_BEAT, QUARTER_BEAT, BeatGrid, Note, QNote

SIXTEENTH = 3
TRIPLET = 4
EIGHTH = 6
TRIPLET_GAIN = 0.4   # triplets must cut a beat's snapping error by more than this
EIGHTH_TOL = 1.25    # ticks: a beat is written in 8ths if every onset is this close to one
QUARTER = QUARTER_BEAT
LONG_NOTE = 0.75     # of a beat: a beat's only note this long snaps to the beat...
LEAN = 0.25          # ...when it starts within this share of a beat of it (a 16th in 4/4)
ANTICIPATE = 0.125   # of a beat: a long note starting this little before a beat belongs to it
# Gaps shorter than a beat are held through (a flute line is legato; a
# pianist's lifted fingers are not rests).
SHORT_BAR = 3        # felt beats: shorter bars (2/4, 6/8, 3/8) get breath windows of 4 bars


def snap(ticks: float, step: int) -> int:
    """Nearest multiple of step (halves round up)."""
    return int(math.floor(ticks / step + 0.5)) * step


def _beat_of(ticks: float, unit: int = QUARTER) -> int:
    return math.floor(ticks / unit)


def _note_beat(s: float, e: float, unit: int) -> int:
    """The beat a note's onset is judged in: its own, or the next one for a
    long note that starts just before it (ANTICIPATE)."""
    b = _beat_of(s, unit)
    if e - s >= LONG_NOTE * unit and (b + 1) * unit - s <= ANTICIPATE * unit:
        b += 1
    return b


def _half_beat(unit: int) -> int:
    """The grid a long note falls back to when it is not on the beat."""
    return SIXTEENTH if unit == EIGHTH_BEAT else EIGHTH


def _beat_steps(notes: list[tuple[float, float]], unit: int = QUARTER) -> dict[int, int]:
    """The coarsest grid each beat's onsets fit: 8ths, else 8th triplets
    (quarter beats only), else 16ths.

    Real playing (rubato, a late finger) lands between grid lines; always
    snapping to 16ths turns that into fussy dotted-16th rhythms, so a beat
    only gets 16ths when its notes really are that fast. A beat holding one
    long note (a singer or pianist leaning into it a little early or late)
    puts it on the beat when it's that close (a dotted quarter in 6/8), and
    on an 8th otherwise.
    """
    by_beat: dict[int, list[tuple[float, float]]] = {}
    for s, e in notes:
        by_beat.setdefault(_note_beat(s, e, unit), []).append((s, e))
    steps = {}
    for beat, se in by_beat.items():
        ss = [s for s, _ in se]
        if len(se) == 1 and se[0][1] - se[0][0] >= LONG_NOTE * unit:
            s = ss[0]
            steps[beat] = unit if abs(s - snap(s, unit)) <= LEAN * unit else _half_beat(unit)
            continue
        if unit == EIGHTH_BEAT:
            continue                       # 16ths are the only split of an 8th beat
        on8 = [snap(s, EIGHTH) for s in ss]
        if max(abs(s - q) for s, q in zip(ss, on8)) <= EIGHTH_TOL and len(set(on8)) == len(on8):
            steps[beat] = EIGHTH
            continue
        if len(ss) < 2 or unit != QUARTER:
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
    unit = grid.beat_ticks
    min_dur = EIGHTH if simple else SIXTEENTH
    default = EIGHTH if simple else SIXTEENTH

    raw = sorted(
        ((grid.to_ticks(n.start), grid.to_ticks(max(n.end, n.start)), n) for n in notes),
        key=lambda x: (x[0], -x[2].velocity),
    )
    steps = {} if simple else _beat_steps([(s, e) for s, e, _ in raw], unit)

    def step_at(t: float) -> int:
        return steps.get(_beat_of(t, unit), default)

    # snap onsets; on a collision keep the louder note (then the longer one)
    kept: dict[int, tuple[float, Note]] = {}
    for s, e, n in raw:
        st = snap(s, steps.get(_note_beat(s, e, unit), default))
        old = kept.get(st)
        if old is None or (n.velocity, n.dur) > (old[1].velocity, old[1].dur):
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
