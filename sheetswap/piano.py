"""Piano part for duet mode.

The flute carries the tune; the piano holds the harmony and bass underneath
it. No piano note ever sounds at or above a flute note it overlaps.

Two styles:
- "chords": the chords harmony.harmonize finds (from the accompaniment, the
  tune and the key; from the tune alone when the accompaniment is too thin),
  struck where they change and again each bar. Left hand holds the real bass
  note in the bass register: the root, or the 3rd/5th for an inversion.
  Right hand plays three chord tones in close position (a seventh chord
  leaves out the tone the bass has), moving to the nearest voicing of the
  next chord and staying at least CHORD_GAP semitones under the lowest
  flute note it overlaps.
- "cleaned": the transcription itself minus the tune, quantized to 8ths,
  split into hands, thinned, and pushed under the flute.

Each hand comes out as plain chords: every note struck at one tick has the
same length, and it stops by the hand's next onset.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from math import floor

from .harmony import Chord, harmonize
from .model import DOTTED_BEAT, EIGHTH_BEAT, QUARTER_BEAT, SIXTEENTH, BeatGrid, Note, QNote

LH_LOW, LH_HIGH = 36, 47     # chords style: bass note register (C2..B2)
RH_LOW, RH_HIGH = 52, 67     # chords style: right-hand triad register (E3..G4)
RH_FLOOR = 48                # how low the right hand may go when the flute dips
RH_CENTER = 59.5             # first chord sits around here
CHORD_GAP = 3                # chords style: RH at least this far under the flute
CLEAN_GAP = 2                # cleaned style: notes >= flute - 1 must move
SAFE_GAP = 1                 # every style: strictly under the flute
SPLIT = 60                   # cleaned style: below middle C goes to the left hand
MAX_RH, MAX_LH = 3, 2        # cleaned style: notes per onset
TUNE_TOL = SIXTEENTH         # ticks for "starts with the flute note"
TUNE_VOTES = 4               # flute notes that must agree on the tune's octave move
MIN_NOTE_S = 0.05            # shorter transcribed notes are blips
PIANO_LOW, PIANO_HIGH = 21, 108


class _FluteFloor:
    """Lowest flute pitch sounding anywhere in a tick range."""

    def __init__(self, flute: list[QNote]):
        self.notes = sorted((q for q in flute if q.dur > 0), key=lambda q: q.start)
        self.starts = [q.start for q in self.notes]
        self.longest = max((q.dur for q in self.notes), default=0)

    def lowest(self, a: float, b: float) -> int | None:
        i0 = bisect_right(self.starts, a - self.longest)
        i1 = bisect_left(self.starts, b)
        pitches = [q.pitch for q in self.notes[i0:i1] if q.end > a]
        return min(pitches) if pitches else None


def _keep_under(hand: list[QNote], flute: _FluteFloor, gap: int) -> list[QNote]:
    """Notes that come within `gap` semitones of an overlapping flute note go
    down an octave; if that isn't enough, or the octave below is already
    struck at that tick, they are left out."""
    out, struck = [], set()
    for n in sorted(hand, key=lambda q: (q.start, q.pitch)):
        pitch = n.pitch
        low = flute.lowest(n.start, n.end)
        if low is not None and pitch > low - gap:
            pitch -= 12
            if pitch > low - gap:
                continue
        if pitch < PIANO_LOW or (n.start, pitch) in struck:
            continue
        struck.add((n.start, pitch))
        out.append(QNote(pitch, n.start, n.dur, n.velocity))
    return out


def _quantize_chords(notes: list[Note], grid: BeatGrid, step: int = EIGHTH_BEAT) -> list[QNote]:
    """Polyphonic quantize in beat space: onsets and ends snap to `step`
    ticks, every note lasts at least one step, the same pitch struck twice at
    one tick becomes one note, and a pitch is cut off when it is struck again."""
    def snap(t: float) -> int:
        return int(floor(grid.to_ticks(t) / step + 0.5)) * step

    merged: dict[tuple[int, int], QNote] = {}
    for n in notes:
        start = snap(n.start)
        dur = max(step, snap(n.end) - start)
        q = merged.get((n.pitch, start))
        if q is None:
            merged[(n.pitch, start)] = QNote(n.pitch, start, dur, n.velocity)
        else:
            q.dur, q.velocity = max(q.dur, dur), max(q.velocity, n.velocity)
    by_pitch = sorted(merged.values(), key=lambda q: (q.pitch, q.start))
    for a, b in zip(by_pitch, by_pitch[1:]):
        if a.pitch == b.pitch and a.end > b.start:
            a.dur = b.start - a.start
    return sorted(by_pitch, key=lambda q: (q.start, q.pitch))


def _as_chords(hand: list[QNote]) -> list[QNote]:
    """One length per onset (the longest), cut at the hand's next onset."""
    groups: dict[int, dict[int, QNote]] = defaultdict(dict)
    for n in hand:
        old = groups[n.start].get(n.pitch)
        if old is None or n.velocity > old.velocity:
            groups[n.start][n.pitch] = n
    starts = sorted(groups)
    out = []
    for i, s in enumerate(starts):
        notes = sorted(groups[s].values(), key=lambda q: q.pitch)
        dur = max(n.dur for n in notes)
        if i + 1 < len(starts):
            dur = min(dur, starts[i + 1] - s)
        out += [QNote(n.pitch, s, dur, n.velocity) for n in notes]
    return out


def _thin(hand: list[QNote], most: int) -> tuple[list[QNote], list[QNote]]:
    """(kept, extra): at most `most` notes per onset, keeping the lowest."""
    groups: dict[int, list[QNote]] = defaultdict(list)
    for n in hand:
        groups[n.start].append(n)
    kept, extra = [], []
    for s in sorted(groups):
        ordered = sorted(groups[s], key=lambda q: q.pitch)
        kept += ordered[:most]
        extra += ordered[most:]
    return kept, extra


def _fold(pitch: int) -> int:
    while pitch < PIANO_LOW:
        pitch += 12
    while pitch > PIANO_HIGH:
        pitch -= 12
    return pitch


# ---------------------------------------------------------------- chords style

def _rh_tones(chord: Chord, shift: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(right-hand pitch classes, the ones to keep when only two fit).

    Triads: all three. Seventh chords: the three tones the bass doesn't play
    (Gmaj7 over G: B D F#). The third (and seventh) matter most."""
    tones = chord.tones
    rh = tones
    if len(tones) == 4:
        rh = tuple(t for t in tones if t != chord.bass)[:3]
    guide = (tones[1],) + ((tones[3],) if len(tones) == 4 else ())
    return (tuple((t + shift) % 12 for t in rh),
            tuple((t + shift) % 12 for t in guide))


def _close_voicings(tones: tuple[int, ...], lo: int, hi: int, size: int) -> list[tuple[int, ...]]:
    """Every run of `size` consecutive chord tones lying inside lo..hi."""
    out = []
    for bottom in range(lo, hi + 1):
        if bottom % 12 not in tones:
            continue
        v, p = [bottom], bottom
        while len(v) < size:
            p += 1
            if p % 12 in tones:
                v.append(p)
        if v[-1] <= hi:
            out.append(tuple(v))
    return out


def _nearest(cands: list[tuple[int, ...]], prev: tuple[int, ...] | None) -> tuple[int, ...]:
    """The voicing that moves least from the previous one (smooth voice
    leading); the first chord, and ties, go to the one nearest RH_CENTER."""
    def cost(v):
        mean = sum(v) / len(v)
        if not prev:
            move = 0.0
        elif len(prev) == len(v):
            move = sum(abs(a - b) for a, b in zip(v, prev))
        else:
            move = len(v) * abs(mean - sum(prev) / len(prev))
        return move, abs(mean - RH_CENTER), v
    return min(cands, key=cost)


def _voice(tones: tuple[int, ...], ceiling: int | None, prev: tuple[int, ...] | None,
           guide: tuple[int, ...] = ()) -> tuple[int, ...]:
    """Right-hand chord under the flute: three chord tones in close position
    in RH_LOW..RH_HIGH; if the flute is too low for that, reaching down to
    RH_FLOOR; then two chord tones (with the third if possible); else nothing."""
    top = RH_HIGH if ceiling is None else min(RH_HIGH, ceiling - CHORD_GAP)
    for lo in (RH_LOW, RH_FLOOR):
        cands = _close_voicings(tones, lo, top, 3)
        if cands:
            return _nearest(cands, prev)
    pairs = _close_voicings(tones, RH_FLOOR, top, 2)
    pairs = [v for v in pairs if set(guide) & {p % 12 for p in v}] or pairs
    return _nearest(pairs, prev) if pairs else ()


def _velocity(level: float) -> int:
    return max(36, min(72, round(0.75 * level)))


def _chords_style(notes, flute_notes, flute: _FluteFloor, grid: BeatGrid, shift: int, bass, key):
    tune = [QNote(q.pitch - shift, q.start, q.dur) for q in flute_notes]
    rh_notes, lh_notes, prev = [], [], None
    # one Chord per run of the same harmony inside a bar, so it is held to its
    # end and struck again each bar; the pickup belongs to the flute alone.
    # Chords may change each beat: a quarter, or a dotted quarter in 6/8 and 3/8.
    unit = QUARTER_BEAT if grid.beat_ticks == QUARTER_BEAT else DOTTED_BEAT
    for c in harmonize(notes, grid, bass, tune, key, unit=unit):
        if c.start < 0:
            continue
        tones, guide = _rh_tones(c, shift)
        rh = _voice(tones, flute.lowest(c.start, c.end), prev, guide)
        prev = rh or prev
        velocity = _velocity(c.level)
        rh_notes += [QNote(p, c.start, c.dur, velocity) for p in rh]
        # left hand: the real bass note (the root, or the 3rd/5th of an inversion)
        lh_notes.append(QNote(LH_LOW + ((c.bass + shift) - LH_LOW) % 12, c.start, c.dur,
                              min(78, velocity + 6)))
    return (_keep_under(rh_notes, flute, CHORD_GAP),
            _keep_under(lh_notes, flute, SAFE_GAP))


# --------------------------------------------------------------- cleaned style

def _without_tune(notes: list[Note], flute: list[QNote], grid: BeatGrid, shift: int) -> list[Note]:
    """Remove the source notes that are the flute's tune.

    The flute is the tune moved by whole octaves (plus `shift`), so a match is
    a note of the same pitch class starting within a 16th of a flute note.
    The octave move most flute notes agree on picks the exact note; failing
    that, the top note at that moment goes if it has the flute's pitch class.
    Same-class notes that would reach the flute go too, so they aren't just
    dropped an octave to double the tune."""
    if not flute or not notes:
        return list(notes)
    ticks = [grid.to_ticks(n.start) for n in notes]
    order = sorted(range(len(notes)), key=ticks.__getitem__)
    sorted_ticks = [ticks[i] for i in order]
    near = []
    for f in flute:
        lo = bisect_left(sorted_ticks, f.start - TUNE_TOL)
        hi = bisect_right(sorted_ticks, f.start + TUNE_TOL)
        near.append([order[j] for j in range(lo, hi)])

    def offset(f: QNote, i: int) -> int:
        return f.pitch - notes[i].pitch - shift

    votes: Counter = Counter()
    for f, idx in zip(flute, near):
        votes.update({offset(f, i) for i in idx if offset(f, i) % 12 == 0})
    move = None
    if votes:
        move, count = votes.most_common(1)[0]
        if count < max(TUNE_VOTES, 0.5 * len(flute)):
            move = None
    drop = set()
    for f, idx in zip(flute, near):
        same = [i for i in idx if offset(f, i) % 12 == 0]
        if not same:
            continue
        exact = [i for i in same if offset(f, i) == move]
        if exact:
            drop.update(exact)
        else:
            top = max(idx, key=lambda i: notes[i].pitch)
            if top in same:
                drop.add(top)
        drop.update(i for i in same if offset(f, i) <= 1)
    return [n for i, n in enumerate(notes) if i not in drop]


def _cleaned_style(notes, flute_notes, flute: _FluteFloor, grid: BeatGrid, shift: int, bass):
    bar = grid.bar_ticks
    source = _without_tune(list(notes) + list(bass or []), flute_notes, grid, shift)
    source = [n for n in source if n.end - n.start >= MIN_NOTE_S]
    rh, lh = [], []
    for q in _quantize_chords(source, grid, step=EIGHTH_BEAT):
        if q.start < -bar:                    # nothing before the pickup bar
            continue
        pitch = _fold(q.pitch + shift)
        velocity = max(30, min(72, round(0.8 * q.velocity)))
        (lh if pitch < SPLIT else rh).append(QNote(pitch, q.start, q.dur, velocity))
    # the left hand keeps its lowest two; extra notes up in the right hand's
    # register (often the chord's third) move across, lower ones just double the bass
    lh, extra = _thin(lh, MAX_LH)
    rh, _ = _thin(rh + [n for n in extra if n.pitch >= RH_LOW], MAX_RH)
    hands = []
    for hand in (rh, lh):
        hand = _keep_under(hand, flute, CLEAN_GAP)
        # equal lengths per onset can lengthen a note, so check the flute again
        hands.append(_keep_under(_as_chords(hand), flute, CLEAN_GAP))
    return hands[0], hands[1]


def accompany(notes: list[Note], flute: list[QNote], grid: BeatGrid, style: str,
              shift: int = 0, bass: list[Note] | None = None,
              key: tuple[int, str] | None = None) -> tuple[list[QNote], list[QNote]]:
    """(right hand, left hand) under the flute line.

    `notes` are the transcribed accompaniment notes (seconds, original key);
    `flute` is the finished flute line (ticks, already transposed and moved
    into range); `shift` is the key transposition given to the flute, added
    here to every piano pitch. The flute's octave moves are not applied.
    `bass` (optional) is a separate bass line that gives the chords' bass
    notes and, in the cleaned style, joins the source notes. `key` is
    (fifths, mode) of the original notes; detected when left out.
    In the chords style the tune helps choose the chords, and when the
    accompaniment is too thin to follow, the tune is harmonized instead."""
    floor_ = _FluteFloor(flute)
    if style == "chords":
        return _chords_style(notes, flute, floor_, grid, shift, bass, key)
    if style == "cleaned":
        return _cleaned_style(notes, flute, floor_, grid, shift, bass)
    raise ValueError(f"unknown piano style: {style!r}")
