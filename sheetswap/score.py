"""Arrangement -> engraved score (music21), MusicXML and MIDI.

Notes come in as ticks (TICKS_PER_BEAT per quarter). Each note or rest is cut
into pieces that follow the usual engraving rules, so the page shows the beat:
- pieces never cross a barline (ties carry long notes over);
- off-beat notes are tied at the next beat, except the common 8th-quarter-8th
  syncopation inside a half bar;
- rests never use dots and never cross a beat (a half rest is allowed on
  beats 1 and 3 of 4/4); an empty bar gets one whole-bar rest.

In 6/8, 9/8, 12/8 and 3/8 the page shows dotted-quarter beats instead: notes
inside a beat are 8ths, 16ths, a quarter + 8th or a dotted 8th + 16th + 8th,
never crossing into the next beat untied; a whole beat of rest is one dotted
quarter rest (a dotted half rest for half a bar of 12/8), and part of a beat
is a quarter rest for its first two 8ths, else 8th and 16th rests. 8ths beam
in threes.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np
import pretty_midi
from music21 import (articulations, chord, clef, duration, dynamics, expressions, instrument,
                     key, layout, metadata, meter, note, pitch, spanner, stream, tempo, tie)
from music21 import bar as m21bar

from .checks import bar_ticks, lead_in_ticks
from .config import FLUTE_PROGRAM, PIANO_PROGRAM, TICKS_PER_BEAT as T
from .expression import lower_neighbour, upper_neighbour
from .model import DOTTED_BEAT, QUARTER_BEAT, Arrangement, Mark, QNote

D = DOTTED_BEAT       # the beat the page shows in 6/8, 9/8, 12/8 and 3/8 (18 ticks)


@dataclass(frozen=True)
class _Meter:
    bpb: int          # beats per bar
    beat: int         # ticks per beat: 12 (quarter), 18 (dotted quarter) or 6 (eighth)

    @property
    def bar(self) -> int:
        return self.bpb * self.beat

    @property
    def compound(self) -> bool:
        """Written in dotted-quarter beats (8ths in threes): 6/8, 9/8, 12/8, 3/8."""
        return self.beat != QUARTER_BEAT


def _meter_of(arr: Arrangement) -> _Meter:
    return _Meter(arr.beats_per_bar, arr.beat_ticks)

# ---------------------------------------------------------------- spelling

_LETTERS = "CDEFGAB"
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_SHARPS = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
           ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
_FLATS = [("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
          ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
_ACC = {-2: "--", -1: "-", 0: "", 1: "#", 2: "##"}


@lru_cache(maxsize=64)
def _spellings(fifths: int, mode: str) -> tuple[dict[int, tuple[str, int]], frozenset[int]]:
    """(pitch class -> (letter, alter), pitch classes that belong to the key).

    Notes of the scale are written as the key signature has them (so F# major
    gets E#, G-flat major gets C-flat); other black keys are sharps in sharp
    keys and flats in flat keys; minor keys get their raised leading tone.
    """
    table = dict(enumerate(_SHARPS if fifths >= 0 else _FLATS))
    alters = dict.fromkeys(_LETTERS, 0)
    for letter in ("FCGDAEB"[:fifths] if fifths > 0 else "BEADGCF"[:-fifths]):
        alters[letter] = 1 if fifths > 0 else -1
    in_key = set()
    for letter in _LETTERS:
        pc = (_LETTER_PC[letter] + alters[letter]) % 12
        table[pc] = (letter, alters[letter])
        in_key.add(pc)
    if mode == "minor":
        major_pc = (7 * fifths) % 12
        tonic_letter = table[major_pc][0]
        lead = _LETTERS[(_LETTERS.index(tonic_letter) + 4) % 7]   # 7th degree of the minor key
        table[(major_pc + 8) % 12] = (lead, alters[lead] + 1)
        in_key.add((major_pc + 8) % 12)
    # chromatic notes: the spelling each altered degree conventionally takes,
    # so a note is written the same way all through the piece
    tonic = (7 * fifths + (9 if mode == "minor" else 0)) % 12
    for interval, way in (_MINOR_CHROMATIC if mode == "minor" else _MAJOR_CHROMATIC).items():
        pc = (tonic + interval) % 12
        if pc in in_key:
            continue
        letter, alter = table[(pc - way) % 12]          # the scale note it alters
        if -2 <= alter + way <= 2:
            table[pc] = (letter, alter + way)
    return table, frozenset(in_key)


# Semitones above the tonic -> +1 (raise the scale note below) or -1 (lower the one above).
# Major: #1 (leading tone to ii), b3 (blue / borrowed), #4 (leading tone to V),
# b6 and b7 (borrowed from the minor key). Minor: b2 (Neapolitan), #3 (Picardy
# third), #4 (leading tone to V), #6 (melodic minor); #7 is already in the key.
_MAJOR_CHROMATIC = {1: 1, 3: -1, 6: 1, 8: -1, 10: -1}
_MINOR_CHROMATIC = {1: -1, 4: 1, 6: 1, 9: 1}


def spell(midi: int, fifths: int, mode: str = "major", direction: int = 0) -> pitch.Pitch:
    """Spell a MIDI pitch in a key. `direction` is set for a chromatic passing
    note (+1 stepping up, -1 stepping down): it is written sharp going up and
    flat going down. Otherwise (neighbour notes like E-D#-E, leaps) chromatic
    notes take their key's conventional spelling."""
    table, in_key = _spellings(max(-7, min(7, fifths)), mode)
    pc = midi % 12
    letter, alter = table[pc]
    if pc not in in_key and direction:
        letter, alter = (_SHARPS if direction > 0 else _FLATS)[pc]
    octave = (midi - alter - _LETTER_PC[letter]) // 12 - 1
    return pitch.Pitch(f"{letter}{_ACC[alter]}{octave}")


# ---------------------------------------------------------------- events


@dataclass
class _Event:
    start: int
    dur: int
    pitches: tuple[int, ...]
    velocity: int = 80
    breath: bool = False
    direction: int = 0          # where the melody goes next: +1 up, -1 down, 0 unknown

    @property
    def end(self) -> int:
        return self.start + self.dur


def _line_events(line: list[QNote], shift: int) -> list[_Event]:
    """Monophonic line: one note per onset (louder wins), cut at the next onset."""
    best: dict[int, QNote] = {}
    for n in line:
        if n.dur <= 0:
            continue
        old = best.get(n.start)
        if old is None or (n.velocity, n.pitch) > (old.velocity, old.pitch):
            best[n.start] = n
    notes = [best[s] for s in sorted(best)]
    out = []
    for i, n in enumerate(notes):
        end = min(n.end, notes[i + 1].start) if i + 1 < len(notes) else n.end
        nxt = next((m.pitch for m in notes[i + 1:i + 4] if m.pitch != n.pitch), n.pitch)
        prv = next((m.pitch for m in reversed(notes[max(0, i - 3):i]) if m.pitch != n.pitch), n.pitch)
        out.append(_Event(n.start + shift, end - n.start, (n.pitch,), n.velocity,
                          n.breath_after, _passing(prv, n.pitch, nxt)))
    return out


def _passing(prev: int, p: int, nxt: int) -> int:
    """Which way a chromatic note leans, for spelling it: +1 up, -1 down, 0 neither.

    A note that resolves by a semitone is spelled so the step is a minor
    second (G-flat to F, not F-sharp to F); a passing note follows its
    direction; anything else (E D# E... with a leap away) gets the key's
    conventional spelling.
    """
    if abs(nxt - p) == 1:
        return nxt - p
    if 0 < p - prev <= 2 and 0 < nxt - p <= 2:
        return 1
    if 0 < prev - p <= 2 and 0 < p - nxt <= 2:
        return -1
    return 0


def _flute_lookup(flute: list[QNote]):
    """unsafe(pitches, lo, hi): would holding `pitches` over [lo, hi) sit at or
    above a flute note sounding in that time?"""
    line = sorted((f for f in flute if f.dur > 0), key=lambda f: f.start)
    starts = [f.start for f in line]
    longest = max((f.dur for f in line), default=0)

    def unsafe(pitches, lo: int, hi: int) -> bool:
        top = max(pitches)
        i = bisect_left(starts, hi) - 1
        while i >= 0 and line[i].start + longest > lo:
            f = line[i]
            if f.end > lo and top >= f.pitch:
                return True
            i -= 1
        return False

    return unsafe


def _chord_events(notes: list[QNote], shift: int, flute: list[QNote]) -> list[_Event]:
    """One staff of piano: one chord per onset, held until the next onset at most.

    Shortening notes at the next onset keeps a single clean rhythm per staff
    (pianists pedal through). Notes of one chord are evened out to the longest
    one unless that would push a note up into the flute.
    """
    unsafe = _flute_lookup(flute)
    groups: dict[int, list[QNote]] = {}
    for n in notes:
        if n.dur > 0:
            groups.setdefault(n.start, []).append(n)
    starts = sorted(groups)
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else None
        durs = [min(n.dur, nxt - s) if nxt is not None else n.dur for n in groups[s]]
        dur = max(durs)
        short = [n for n, d in zip(groups[s], durs) if d < dur]
        if short and unsafe([n.pitch for n in short], s + min(durs), s + dur):
            dur = min(durs)
        pitches = tuple(sorted({n.pitch for n in groups[s]}))
        vel = max(n.velocity for n in groups[s])
        out.append(_Event(s + shift, dur, pitches, vel))
    return out


def _beat_grids(events: list[_Event]) -> dict[int, int]:
    """Beat index -> 4 (8th-triplet grid) or 3 (16th grid), voted by the onsets in it."""
    votes: dict[int, list[int]] = {}
    for e in events:
        off = e.start % T
        if off in (3, 6, 9, 4, 8):
            v = votes.setdefault(e.start // T, [0, 0])
            v[0 if off % 3 == 0 else 1] += 1
    return {b: (4 if trip > six else 3) for b, (six, trip) in votes.items()}


def _tidy(events: list[_Event], compound: bool = False) -> list[_Event]:
    """Snap starts and ends onto the grid of the beat they fall in, so every
    beat is either plain 16ths or 8th triplets (never a mix of the two).
    Compound meters have no triplets: everything goes on the 16th grid."""
    grids = {} if compound else _beat_grids(events)

    def snap(pos: int) -> int:
        b, off = divmod(pos, T)
        g = grids.get(b, 4 if off in (4, 8) and not compound else 3)
        return b * T + round(off / g) * g

    snapped: dict[int, _Event] = {}
    for e in events:
        s = snap(e.start)
        if s in snapped:            # two onsets collapsed together: keep the first
            continue
        snapped[s] = _Event(s, max(snap(e.end) - s, 0), e.pitches, e.velocity, e.breath,
                            e.direction)
    out = [snapped[s] for s in sorted(snapped)]
    for a, b in zip(out, out[1:]):
        a.dur = min(a.dur, b.start - a.start)
    for a in out:
        if a.dur <= 0:              # shorter than one grid step: give it one step
            g = grids.get(a.start // T, 3)
            a.dur = g
    for a, b in zip(out, out[1:]):
        a.dur = min(a.dur, b.start - a.start)
    return [a for a in out if a.dur > 0]


# ---------------------------------------------------------------- rhythm pieces


def _piece(pos: int, left: int, m: _Meter, rest: bool, trip_beat) -> int:
    """Longest piece that may start at `pos` (ticks into the bar), at most `left` long."""
    if m.compound:
        return _compound_piece(pos, left, m.bar, rest)
    return _simple_piece(pos, left, m.bpb, rest, trip_beat)


# Inside one dotted-quarter beat: offset (ticks) -> note lengths that may start
# there without crossing the beat. 0: quarter (+8th), dotted 8th (+16th+8th),
# 8th, 16th; on the 2nd 8th a quarter fills the beat (8th + quarter); on a
# 16th off the 8th grid a dotted 8th or 8th fills to an 8th or the beat.
_IN_DOTTED_BEAT = {0: (12, 9, 6, 3), 3: (9, 6, 3), 6: (12, 6, 3), 9: (9, 6, 3),
                   12: (6, 3), 15: (3,)}


def _compound_piece(pos: int, left: int, bar: int, rest: bool) -> int:
    """_piece for 6/8, 9/8, 12/8 and 3/8 (bars of dotted-quarter beats)."""
    off = pos % D
    cands: list[int] = []
    if rest:
        if off == 0:
            if bar == 4 * D and pos % (2 * D) == 0:
                cands.append(2 * D)        # dotted half rest: half a bar of 12/8
            cands += [D, 12]               # a whole beat; a quarter rest for two 8ths
        if off % 6 == 0:
            cands.append(6)
        if off % 3 == 0:
            cands.append(3)
    else:
        if off == 0:
            for d in (4 * D, 2 * D, D):    # dotted whole, dotted half, dotted quarter
                if pos + d <= bar and (d != 4 * D or pos == 0):
                    cands.append(d)
        cands += _IN_DOTTED_BEAT.get(off, ())
    cands = [d for d in cands if d <= left]
    if cands:
        return max(cands)
    for d in (6, 3, 2, 1):                 # off the grid (should not happen after _tidy)
        if d <= left and off + d <= D:
            return d
    return 1


def _simple_piece(pos: int, left: int, bpb: int, rest: bool, trip_beat) -> int:
    """_piece for 2/4, 3/4 and 4/4 (bars of quarter beats)."""
    off = pos % T
    beat = pos // T
    bar = bpb * T
    trip = trip_beat(beat)
    cands: list[int] = []
    if rest:
        if off == 0:
            if bpb == 4 and pos % 24 == 0:
                cands.append(24)
            cands.append(T)
    else:
        if off == 0:
            for d in (48, 36, 24, 18, 12):
                if pos + d > bar or (d == 48 and pos != 0):
                    continue
                if d == 18 and trip_beat(beat + 1):
                    continue
                cands.append(d)
        elif off == 6 and not trip:
            # 8th-quarter-8th syncopation, kept inside a half bar of 4/4
            ok = (6, 30) if bpb == 4 else (6, 18)
            if pos in ok:
                if pos + 18 <= bar:
                    cands.append(18)
                if not trip_beat(beat + 1):
                    cands.append(12)
    # inside one beat
    if trip:
        if off in (0, 4):
            cands.append(8)
        if off % 4 == 0:
            cands.append(4)
    else:
        if not rest and off in (0, 3):
            cands.append(9)
        if off in (0, 6) or (off == 3 and not rest):
            cands.append(6)
        if off % 3 == 0:
            cands.append(3)
    cands = [d for d in cands if d <= left]
    if cands:
        return max(cands)
    # off the grid (should not happen after _tidy): fill to the next simple point
    for d in (6, 4, 3, 2, 1):
        if d <= left and off + d <= T:
            return d
    return 1


def _pieces(start: int, end: int, m: _Meter, rest: bool, trip_beats: set[int]):
    """Split [start, end) into notated pieces: [(start, dur, whole_bar_rest)]."""
    bar = m.bar
    out = []
    s = start
    while s < end:
        b0 = s - s % bar
        seg_end = min(end, b0 + bar)
        if rest and s == b0 and seg_end == b0 + bar:
            out.append((s, bar, True))
        else:
            pos = s - b0
            while b0 + pos < seg_end:
                d = _piece(pos, seg_end - b0 - pos, m, rest,
                           lambda beat: (b0 // T + beat) in trip_beats)
                out.append((b0 + pos, d, False))
                pos += d
        s = seg_end
    return out


# ---------------------------------------------------------------- music21 parts


def _ql(ticks: int) -> Fraction:
    return Fraction(ticks, T)


def _fill_measures(part: stream.Stream, events: list[_Event], n_bars: int, arr: Arrangement,
                   staff_clef, top: bool, compress_rests: bool = False,
                   marks: "_Marks | None" = None) -> None:
    mtr = _meter_of(arr)
    bar = mtr.bar
    trip_beats = set() if mtr.compound else {
        pos // T for e in events for pos in (e.start, e.end) if pos % T in (4, 8)}
    measures = [stream.Measure(number=i + 1) for i in range(n_bars)]

    def put(s: int, el) -> None:
        m = measures[s // bar]
        m.insert(_ql(s % bar), el)

    def rests(a: int, b: int) -> None:
        for s, d, whole in _pieces(a, b, mtr, True, trip_beats):
            r = note.Rest(quarterLength=_ql(d))
            if whole:
                r.fullMeasure = True
            put(s, r)

    cursor = 0
    placed: list[list] = []          # per event: its notated pieces
    for e in events:
        if e.start > cursor:
            rests(cursor, e.start)
        pieces = _pieces(e.start, e.end, mtr, False, trip_beats)
        placed.append([])
        for i, (s, d, _) in enumerate(pieces):
            if len(e.pitches) == 1:
                el = note.Note(spell(e.pitches[0], arr.key_fifths, arr.key_mode, e.direction))
            else:
                el = chord.Chord([spell(p, arr.key_fifths, arr.key_mode) for p in e.pitches])
            el.duration = duration.Duration(_ql(d))
            el.volume.velocity = max(1, min(127, e.velocity))
            if len(pieces) > 1:
                el.tie = tie.Tie("start" if i == 0 else "stop" if i == len(pieces) - 1
                                 else "continue")
            if e.breath and i == len(pieces) - 1:
                el.articulations.append(articulations.BreathMark())
            put(s, el)
            placed[-1].append(el)
        cursor = e.end
    if cursor < n_bars * bar:
        rests(cursor, n_bars * bar)

    first = measures[0]
    first.insert(0, staff_clef)
    first.insert(0, key.KeySignature(max(-7, min(7, arr.key_fifths))).asKey(arr.key_mode))
    num, den = arr.time_signature
    first.insert(0, meter.TimeSignature(f"{num}/{den}"))
    if top:   # the beat the tempo counts: a quarter, dotted quarter or eighth
        first.insert(0, tempo.MetronomeMark(number=_bpm(arr),
                                            referent=duration.Duration(_ql(arr.beat_ticks))))
    measures[-1].rightBarline = m21bar.Barline("final")
    for m in measures:
        part.append(m)
    if marks is not None:
        _attach_marks(part, measures, events, placed, marks, arr)
    part.makeNotation(inPlace=True)
    _number_only_when_beamed(part)
    if compress_rests:
        _multi_bar_rests(part)


def _number_only_when_beamed(part: stream.Stream) -> None:
    """Triplets under one beam show just the "3"; others keep the bracket."""
    group: list = []
    for el in part.recurse().notesAndRests:
        tuplets = el.duration.tuplets
        if not tuplets:
            group = []
            continue
        group.append(el)
        if tuplets[0].type == "stop":
            if all(not g.isRest and g.beams.beamsList for g in group):
                for g in group:
                    g.duration.tuplets[0].bracket = False
            group = []


def _multi_bar_rests(part: stream.Stream) -> None:
    """Runs of two or more empty bars become one numbered multi-bar rest."""
    run: list[note.Rest] = []

    def close() -> None:
        if len(run) >= 2:
            part.insert(0, spanner.MultiMeasureRest(list(run)))
        run.clear()

    for m in part.getElementsByClass(stream.Measure):
        items = list(m.notesAndRests)
        if len(items) == 1 and items[0].isRest:
            run.append(items[0])
        else:
            close()
    close()


@dataclass
class _Layout:
    flute: list[_Event]
    rh: list[_Event]
    lh: list[_Event]
    n_bars: int
    shift: int
    marks: "_Marks"


def _layout(arr: Arrangement) -> _Layout:
    shift = lead_in_ticks(arr)
    compound = _meter_of(arr).compound
    flute = _tidy(_line_events(arr.flute, shift), compound)
    rh = _tidy(_chord_events(arr.rh, shift, arr.flute), compound)
    lh = _tidy(_chord_events(arr.lh, shift, arr.flute), compound)
    last = max((e.end for e in (*flute, *rh, *lh)), default=0)
    bar = bar_ticks(arr)
    return _Layout(flute, rh, lh, max(1, -(-last // bar)), shift,
                   _place_marks(arr.expression, flute, shift, bar))


# ---------------------------------------------------------------- expression marks
#
# Marks (model.Mark, ticks on the flute part) are attached to the laid-out
# flute events. On the page they sit where a wind part has them: dynamics and
# hairpins below the staff, tempo changes (rit., a tempo) above in italics,
# character words (dolce, ...) below in italics, the opening tempo word in bold
# beside the metronome mark, fermatas and ornaments above, articulations on
# the notehead side, grace notes as slashed 8ths. Verovio stacks dynamics and
# hairpins on one line and keeps them clear of each other.

DYNAMIC_LEVELS = ("pp", "p", "mp", "mf", "f", "ff")
_SINGLE = ("staccato", "tenuto", "accent", "fermata", "trill", "mordent", "turn")
_TEMPO_WORDS = {"Grave", "Largo", "Lento", "Larghetto", "Adagio", "Adagietto", "Andante",
                "Andantino", "Moderato", "Allegretto", "Allegro", "Vivace", "Presto",
                "Prestissimo", "Tempo", "Maestoso"}
_ABOVE = ("rit", "rall", "a tempo", "accel", "allarg", "string", "meno mosso",
          "più mosso", "piu mosso", "tempo")
_RESETS = ("a tempo", "tempo")


@dataclass
class _Marks:
    """The flute part's marks, by laid-out event (index) or layout tick."""
    on: dict[int, set[str]] = field(default_factory=dict)       # event -> single-note marks
    grace: dict[int, int] = field(default_factory=dict)          # event -> grace pitch
    slurs: list[tuple[int, int]] = field(default_factory=list)   # first, last event
    wedges: list[tuple[int, int, str]] = field(default_factory=list)   # first, last event, kind
    wedge_ticks: list[tuple[int, int, str]] = field(default_factory=list)
    dynamics: list[tuple[int, str, int | None]] = field(default_factory=list)  # tick, level, event
    texts: list[tuple[int, str]] = field(default_factory=list)   # tick, words
    heading: str = ""                                            # tempo word(s) at the start
    rits: list[tuple[int, int]] = field(default_factory=list)    # start, end tick
    a_tempo: list[int] = field(default_factory=list)             # ticks

    def __bool__(self) -> bool:
        return bool(self.on or self.grace or self.slurs or self.wedges or self.dynamics
                    or self.texts or self.heading or self.rits)


def _nearest(starts: list[int], tick: int, tol: int = 3) -> int | None:
    """Index of the event starting at tick, or within tol ticks of it (tidying moves onsets)."""
    i = bisect_left(starts, tick)
    near = [j for j in (i - 1, i) if 0 <= j < len(starts) and abs(starts[j] - tick) <= tol]
    return min(near, key=lambda j: abs(starts[j] - tick)) if near else None


def _at_or_before(starts: list[int], tick: int) -> int | None:
    j = _nearest(starts, tick)
    if j is None:
        j = bisect_left(starts, tick + 1) - 1
    return j if j >= 0 else None


def _place_marks(marks: list[Mark], events: list[_Event], shift: int, bar: int) -> _Marks:
    out = _Marks()
    if not marks or not events:
        return out
    starts = [e.start for e in events]
    for m in sorted(marks, key=lambda m: (m.start, m.kind)):
        s = m.start + shift
        e = (m.end if m.end > m.start else m.start) + shift
        i = _nearest(starts, s)
        if m.kind == "dynamic" and m.value in DYNAMIC_LEVELS:
            out.dynamics.append((starts[i] if i is not None else s, m.value, i))
        elif m.kind in ("cresc", "dim") and e > s:
            out.wedge_ticks.append((s, e, m.kind))
            j = _at_or_before(starts, e)
            if i is not None and j is not None and j >= i:
                out.wedges.append((i, j, m.kind))
        elif m.kind == "slur":
            j = _nearest(starts, e)
            if i is not None and j is not None and j > i:
                out.slurs.append((i, j))
        elif m.kind in _SINGLE and i is not None:
            out.on.setdefault(i, set()).add(m.kind)
        elif m.kind == "grace" and i is not None and m.value.strip().isdigit():
            out.grace[i] = int(m.value)
        elif m.kind == "text" and m.value.strip():
            words = " ".join(m.value.split())
            low = words.lower()
            if not out.heading and s <= starts[0] and words.split()[0] in _TEMPO_WORDS:
                out.heading = words
                continue
            out.texts.append((starts[i] if i is not None else s, words))
            if low.startswith(("rit", "rall")):
                out.rits.append((s, e if e > s else s + 2 * bar))
            elif low.startswith(_RESETS) or words.split()[0] in _TEMPO_WORDS:
                out.a_tempo.append(s)
    return out


def _attach_marks(part: stream.Stream, measures: list, events: list[_Event], placed: list[list],
                  marks: _Marks, arr: Arrangement) -> None:
    """Put the marks on the music21 notes (before makeNotation, so grace notes get
    their accidentals): first piece of a tied note for attacks, last for fermatas."""
    bar = _meter_of(arr).bar

    def put(tick: int, el) -> None:
        k = min(max(tick, 0) // bar, len(measures) - 1)
        measures[k].insert(_ql(max(tick, 0) - k * bar), el)

    with_dynamic = set()
    for tick, level, i in marks.dynamics:
        d = dynamics.Dynamic(level)
        d.placement = "below"
        put(tick, d)
        if i is not None:
            with_dynamic.add(i)
    for i, j, kind in marks.wedges:
        if j > i and j in with_dynamic:          # "< f": the wedge stops before the new dynamic
            j -= 1
        w = (dynamics.Crescendo if kind == "cresc" else dynamics.Diminuendo)(
            placed[i][0], placed[j][-1])
        w.placement = "below"
        part.insert(0, w)
    for i, j in marks.slurs:
        sl = spanner.Slur(placed[i][0], placed[j][-1])
        if any(p < 71 for e in events[i:j + 1] for p in e.pitches):
            sl.placement = "above"      # over stems-up notes, clear of the dynamics below
        part.insert(0, sl)
    for i, kinds in marks.on.items():
        first, last = placed[i][0], placed[i][-1]
        for kind, cls in (("accent", articulations.Accent), ("tenuto", articulations.Tenuto),
                          ("staccato", articulations.Staccato)):
            if kind in kinds:
                first.articulations.append(cls())
        if "fermata" in kinds:
            f = expressions.Fermata()
            f.type = "upright"                  # above the staff, whichever way the stem goes
            last.expressions.append(f)
        for kind, cls in (("trill", expressions.Trill), ("mordent", expressions.InvertedMordent),
                          ("turn", expressions.Turn)):
            if kind in kinds:
                orn = cls()
                orn.placement = "above"
                first.expressions.append(orn)
    for i, gp in marks.grace.items():
        g = note.Note(spell(gp, arr.key_fifths, arr.key_mode), type="eighth")
        put(events[i].start, g.getGrace(appoggiatura=False))     # slashed: an acciaccatura
    for tick, words in marks.texts:
        te = expressions.TextExpression(words)
        low = words.lower()
        if words.split()[0] in _TEMPO_WORDS and not low.startswith("tempo"):
            te.placement = "above"
            te.style.fontWeight = "bold"
        else:
            te.placement = "above" if low.startswith(_ABOVE) else "below"
            te.style.fontStyle = "italic"
        put(tick, te)
    if marks.heading:
        for mm in measures[0].getElementsByClass(tempo.MetronomeMark):
            mm.text = marks.heading


def _is_duet(arr: Arrangement) -> bool:
    return bool(arr.rh or arr.lh)


def _bpm(arr: Arrangement) -> int:
    """The printed tempo; the MIDI plays at exactly this speed too."""
    return int(round(arr.bpm)) if arr.bpm and arr.bpm > 0 else 100


def build_score(arr: Arrangement, title: str) -> stream.Score:
    return _build(arr, title, _layout(arr))


def _build(arr: Arrangement, title: str, lay: _Layout) -> stream.Score:
    score = stream.Score()
    score.insert(0, metadata.Metadata())
    score.metadata.title = title or "Untitled"

    flute = stream.Part(id="Flute")
    flute.partName, flute.partAbbreviation = "Flute", "Fl."
    flute.insert(0, instrument.Flute())
    _fill_measures(flute, lay.flute, lay.n_bars, arr, clef.TrebleClef(), top=True,
                   compress_rests=not _is_duet(arr), marks=lay.marks)
    score.insert(0, flute)

    if _is_duet(arr):
        rh = stream.PartStaff(id="PianoRH")
        lh = stream.PartStaff(id="PianoLH")
        for staff in (rh, lh):
            staff.partName, staff.partAbbreviation = "Piano", "Pno."
            staff.insert(0, instrument.Piano())
        rh_pitches = sorted(p for e in lay.rh for p in e.pitches)
        rh_low = rh_pitches and rh_pitches[len(rh_pitches) // 2] < 55
        _fill_measures(rh, lay.rh, lay.n_bars, arr,
                       clef.BassClef() if rh_low else clef.TrebleClef(), top=False)
        _fill_measures(lh, lay.lh, lay.n_bars, arr, clef.BassClef(), top=False)
        score.insert(0, rh)
        score.insert(0, lh)
        group = layout.StaffGroup([rh, lh], name="Piano", abbreviation="Pno.",
                                  symbol="brace")
        group.barTogether = True
        score.insert(0, group)
    return score


# ---------------------------------------------------------------- files


def _key_number(fifths: int, mode: str) -> int:
    tonic = (7 * fifths) % 12
    return (tonic + 9) % 12 + 12 if mode == "minor" else tonic


def _write_midi(arr: Arrangement, lay: _Layout, path: Path) -> None:
    """The parts as MIDI, playing the marks (see _Performance); with no marks
    it plays the notes as written at the printed tempo."""
    bpm = _bpm(arr)                               # beats of arr.beat_ticks per minute
    perf = _Performance(arr, lay)
    pm = pretty_midi.PrettyMIDI(resolution=MIDI_RESOLUTION,
                                initial_tempo=bpm * arr.beat_ticks / T)   # quarters per minute
    perf.tempo_map(pm)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(*arr.time_signature, 0.0))
    pm.key_signature_changes.append(
        pretty_midi.KeySignature(_key_number(max(-7, min(7, arr.key_fifths)), arr.key_mode), 0.0))
    flute = pretty_midi.Instrument(program=FLUTE_PROGRAM, name="Flute")
    flute.notes = perf.flute_notes(_balanced_velocity(lay.flute, MIX_VELOCITY[FLUTE_PROGRAM]))
    flute.control_changes = perf.expression_cc()
    pm.instruments.append(flute)
    if _is_duet(arr):
        piano = pretty_midi.Instrument(program=PIANO_PROGRAM, name="Piano")
        events = lay.rh + lay.lh
        level = _balanced_velocity(events, MIX_VELOCITY[PIANO_PROGRAM])
        for e in events:
            start, end = perf.at(e.start), perf.at(e.end)
            v = perf.follow(level(e.velocity), e.start)
            for p in e.pitches:
                piano.notes.append(pretty_midi.Note(v, p, start, end))
        piano.control_changes = perf.expression_cc()
        pm.instruments.append(piano)
    pm.write(str(path))


# Playing the marks. Dynamics: the note-on velocity (DYN_VELOCITY) and CC 11,
# expression (DYN_CC11), together about 3 dB a level with this soundfont
# (velocity scales a flute note's amplitude, CC 11 its cube); hairpins ramp
# both, CC 11 also inside held notes; the piano follows the same dynamics so
# the balance stays. Accents are louder (ACCENT_PLUS), staccatos sound
# STACCATO_KEEP of their length, slurred notes run into the next, tenutos are
# held full, other notes that lead straight into the next are tongued (a gap
# of DETACH of the note, at most DETACH_MAX s). "rit." slows to RIT_SLOW of
# the tempo by its end, "a tempo" returns; a fermata holds its note
# FERMATA_HOLD times as long (at most FERMATA_MAX s more). Trills (from the
# upper note, with a turn at the end), mordents, turns and grace notes are
# played out, ORN_STEP s per ornament note.
MIDI_RESOLUTION = 480                 # MIDI ticks per quarter: 40 per tick of ours
DYN_VELOCITY = (45, 56, 68, 80, 94, 108)          # pp p mp mf f ff
DYN_CC11 = (88, 98, 106, 114, 121, 127)
ACCENT_PLUS, TENUTO_PLUS = 14, 4
STACCATO_KEEP = 0.45
DETACH, DETACH_MAX = 0.12, 0.05
RIT_SLOW = 0.75
FERMATA_HOLD, FERMATA_MAX = 1.75, 2.0
ORN_STEP = 0.07
PIANO_FOLLOW = 0.8                    # of the flute's velocity change, for the piano


class _Performance:
    def __init__(self, arr: Arrangement, lay: _Layout):
        self.arr, self.lay, self.marks = arr, lay, lay.marks
        bar = _meter_of(arr).bar
        self.n = lay.n_bars * bar + bar               # ticks covered by the tempo map
        self.sec = 60.0 / _bpm(arr) / arr.beat_ticks  # seconds per tick at the printed tempo
        self.mult = self._tempo_multipliers()
        self.cum = np.concatenate([[0.0], np.cumsum(self.mult * self.sec)])
        self.level = self._level_curve()

    # ---- time
    def _tempo_multipliers(self) -> np.ndarray:
        n = self.n
        mult = np.ones(n)
        resets = sorted(self.marks.a_tempo)
        for s, e in self.marks.rits:
            s, e = max(0, min(s, n)), max(0, min(e, n))
            span = max(e - s, 1)
            for t in range(s, e):                 # the speed falls a 16th at a time
                speed = 1 - (1 - RIT_SLOW) * min(1.0, ((t - s) // 3 * 3 + 3) / span)
                mult[t] = 1 / speed
            back = next((r for r in resets if r >= e), n)
            mult[e:max(e, min(back, n))] = 1 / RIT_SLOW
        for i, kinds in self.marks.on.items():
            if "fermata" in kinds:
                ev = self.lay.flute[i]
                a, b = max(0, ev.start), min(ev.end, n)
                held = float(np.sum(mult[a:b])) * self.sec
                mult[a:b] *= min(FERMATA_HOLD, 1 + FERMATA_MAX / max(held, 1e-3))
        return mult

    def at(self, tick: float) -> float:
        """Seconds at a (layout) tick."""
        if tick >= self.n:
            return float(self.cum[-1] + (tick - self.n) * self.sec * self.mult[-1])
        return float(np.interp(tick, np.arange(self.n + 1), self.cum))

    def tempo_map(self, pm: pretty_midi.PrettyMIDI) -> None:
        """Write the tempo changes into pm (pretty_midi has no public way to add them)."""
        per = MIDI_RESOLUTION // T
        changes = [0] + [t for t in range(1, self.n) if self.mult[t] != self.mult[t - 1]]
        if len(changes) == 1:
            return
        pm._tick_scales = [(t * per, self.sec * float(self.mult[t]) / per) for t in changes]
        pm._update_tick_to_time(self.n * per + 1)

    # ---- loudness
    def _level_curve(self):
        """Dynamic level over ticks (0 = pp ... 5 = ff): steps at dynamics, ramps over
        hairpins to the next dynamic (or one level on); None without dynamics."""
        dyn = sorted((t, DYNAMIC_LEVELS.index(lv)) for t, lv, _ in self.marks.dynamics)
        if not dyn:
            return None
        cur = float(dyn[0][1])
        xs, ys = [-1e9], [cur]

        def go(t: float, v: float, ramp: bool) -> None:
            t = max(t, xs[-1])
            if ramp:
                xs.append(t)
                ys.append(v)
            elif v != ys[-1]:
                xs.extend([max(t - 1e-3, xs[-1]), t])
                ys.extend([ys[-1], v])

        items = [(t, 0, lv, 0) for t, lv in dyn] + \
                [(s, 1, e, 1 if k == "cresc" else -1) for s, e, k in self.marks.wedge_ticks]
        for t, kind, a, b in sorted(items):
            if kind == 0:
                go(t, float(a), False)
                cur = float(a)
                continue
            e, way = a, b
            nxt = next((lv for u, lv in dyn if t < u <= e + T), None)
            target = float(nxt) if nxt is not None and (nxt - cur) * way > 0 \
                else min(5.0, max(0.0, cur + way))
            go(t, cur, True)
            go(e, target, True)
            cur = target
        return lambda tick: float(np.interp(tick, xs, ys))

    def velocity(self, tick: int) -> int:
        return int(round(np.interp(self.level(tick), range(6), DYN_VELOCITY)))

    def follow(self, v: int, tick: int) -> int:
        """A piano velocity moved with the flute's dynamics."""
        if self.level is None:
            return v
        return max(1, min(127, v + round(PIANO_FOLLOW * (self.velocity(tick) - DYN_VELOCITY[3]))))

    def expression_cc(self) -> list[pretty_midi.ControlChange]:
        if self.level is None:
            return []
        ticks = sorted(set(range(0, self.n, 3)) | {t for t, _, _ in self.marks.dynamics if t >= 0})
        out, last = [], None
        for t in ticks:
            v = int(round(np.interp(self.level(t), range(6), DYN_CC11)))
            if v != last:
                out.append(pretty_midi.ControlChange(11, v, self.at(t)))
                last = v
        return out

    # ---- the flute
    def flute_notes(self, balanced) -> list[pretty_midi.Note]:
        marks, events = self.marks, self.lay.flute
        inner = {k for i, j in marks.slurs for k in range(i, j)}   # slurred into the next note
        out: list[pretty_midi.Note] = []
        for i, e in enumerate(events):
            kinds = marks.on.get(i, set())
            start, end = self.at(e.start), self.at(e.end)
            joined = i + 1 < len(events) and events[i + 1].start == e.end
            if marks:
                if "staccato" in kinds:
                    end = start + max(0.06, STACCATO_KEEP * (end - start))
                elif joined and i not in inner and "tenuto" not in kinds:
                    end -= min(DETACH_MAX, DETACH * (end - start))
            if e.breath and "staccato" not in kinds:   # leave a little air before the next note
                end -= min(0.1, 0.25 * (end - start))
            v = self.velocity(e.start) if self.level is not None else balanced(e.velocity)
            v += ACCENT_PLUS if "accent" in kinds else TENUTO_PLUS if "tenuto" in kinds else 0
            v = max(1, min(127, v))
            p = e.pitches[0]
            if i in marks.grace:
                g = min(ORN_STEP, 0.5 * (start - out[-1].start)) if out else ORN_STEP
                if out and out[-1].end > start - g:
                    out[-1].end = max(out[-1].start + 0.02, start - g)
                out.append(pretty_midi.Note(max(1, v - 10), marks.grace[i], max(0.0, start - g),
                                            start))
            orn = next((k for k in ("trill", "mordent", "turn") if k in kinds), None)
            for q, a, b in _ornament(orn, p, start, end, self.arr.key_fifths):
                out.append(pretty_midi.Note(v if q == p else max(1, v - 6), q, a, b))
        return out


def _ornament(kind: str | None, p: int, start: float, end: float,
              fifths: int) -> list[tuple[int, float, float]]:
    """The notes an ornament on pitch p over [start, end) is played as."""
    dur = end - start
    if kind is None or dur <= 0:
        return [(p, start, end)]
    up, low = upper_neighbour(p, fifths), lower_neighbour(p, fifths)
    if kind == "trill":
        n = max(4, int(dur / ORN_STEP))
        n -= n % 2
        d = dur / n
        seq = [up, p] * (n // 2 - 1) + [low, p]
        return [(q, start + k * d, start + (k + 1) * d) for k, q in enumerate(seq)]
    if kind == "mordent":
        d = min(ORN_STEP, dur / 6)
        return [(p, start, start + d), (up, start + d, start + 2 * d), (p, start + 2 * d, end)]
    d = min(1.3 * ORN_STEP, dur / 6)              # a turn: upper, main, lower, main
    return [(up, start, start + d), (p, start + d, start + 2 * d),
            (low, start + 2 * d, start + 3 * d), (p, start + 3 * d, end)]


# Playback loudness: transcribed velocities say more about the recording than
# about the part, so each part is centred on a level that puts the flute in
# front of the piano without hiding it (measured on five real duets: the flute
# sits a median 4-11 dB over the piano; a piano note fades while a flute note
# holds, so the piano needs the higher velocity), keeping a quarter of the
# original swings.
MIX_VELOCITY = {FLUTE_PROGRAM: 80, PIANO_PROGRAM: 96}
MIX_KEEP = 0.25


def _balanced_velocity(events, target: int):
    vels = sorted(e.velocity for e in events) or [target]
    mid = vels[len(vels) // 2]
    return lambda v: max(1, min(127, round(target + MIX_KEEP * (v - mid))))


def write_outputs(arr: Arrangement, title: str, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path, midi_path = out_dir / "score.musicxml", out_dir / "score.mid"
    lay = _layout(arr)
    _build(arr, title, lay).write("musicxml", fp=str(xml_path))
    if lay.marks.heading:
        xml_path.write_text(_join_tempo_heading(xml_path.read_text()))
    _write_midi(arr, lay, midi_path)
    return {"musicxml": xml_path, "midi": midi_path}


_TEMPO_SPLIT = re.compile(
    r"<direction>(\s*<direction-type>\s*<metronome\b.*?</direction-type>)(.*?</direction>)"
    r"\s*<direction>\s*<direction-type>\s*<words\b[^>]*>(.*?)</words>\s*</direction-type>"
    r"\s*</direction>", re.S)


def _join_tempo_heading(xml: str) -> str:
    """music21 writes a tempo's words and its metronome mark as two directions,
    which Verovio draws apart (the words under the staff); as one direction
    above the staff it reads "Andante espressivo  quarter = 84" as an edition
    prints it (an en space between: Verovio drops a plain space at the end of
    a text)."""
    return _TEMPO_SPLIT.sub(
        lambda m: ('<direction placement="above"><direction-type>'
                   f'<words font-weight="bold">{m.group(3)}&#8194;</words></direction-type>'
                   + m.group(1) + m.group(2)), xml, count=1)
