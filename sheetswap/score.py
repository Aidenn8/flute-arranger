"""Arrangement -> engraved score (music21), MusicXML and MIDI.

Notes come in as ticks (TICKS_PER_BEAT per quarter). Each note or rest is cut
into pieces that follow the usual engraving rules, so the page shows the beat:
- pieces never cross a barline (ties carry long notes over);
- off-beat notes are tied at the next beat, except the common 8th-quarter-8th
  syncopation inside a half bar;
- rests never use dots and never cross a beat (a half rest is allowed on
  beats 1 and 3 of 4/4); an empty bar gets one whole-bar rest.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import pretty_midi
from music21 import (articulations, chord, clef, duration, instrument, key, layout,
                     metadata, meter, note, pitch, spanner, stream, tempo, tie)
from music21 import bar as m21bar

from .checks import bar_ticks, lead_in_ticks
from .config import FLUTE_PROGRAM, PIANO_PROGRAM, TICKS_PER_BEAT as T
from .model import Arrangement, QNote

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
    return table, frozenset(in_key)


def spell(midi: int, fifths: int, mode: str = "major", direction: int = 0) -> pitch.Pitch:
    """Spell a MIDI pitch in a key. `direction` is where the melody goes next:
    a chromatic note leading up is written sharp, leading down flat."""
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
        out.append(_Event(n.start + shift, end - n.start, (n.pitch,), n.velocity,
                          n.breath_after, (nxt > n.pitch) - (nxt < n.pitch)))
    return out


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


def _tidy(events: list[_Event]) -> list[_Event]:
    """Snap starts and ends onto the grid of the beat they fall in, so every
    beat is either plain 16ths or 8th triplets (never a mix of the two)."""
    grids = _beat_grids(events)

    def snap(pos: int) -> int:
        b, off = divmod(pos, T)
        g = grids.get(b, 4 if off in (4, 8) else 3)
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


def _piece(pos: int, left: int, bpb: int, rest: bool, trip_beat) -> int:
    """Longest piece that may start at `pos` (ticks into the bar), at most `left` long."""
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


def _pieces(start: int, end: int, bpb: int, rest: bool, trip_beats: set[int]):
    """Split [start, end) into notated pieces: [(start, dur, whole_bar_rest)]."""
    bar = bpb * T
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
                d = _piece(pos, seg_end - b0 - pos, bpb, rest,
                           lambda beat: (b0 // T + beat) in trip_beats)
                out.append((b0 + pos, d, False))
                pos += d
        s = seg_end
    return out


# ---------------------------------------------------------------- music21 parts


def _ql(ticks: int) -> Fraction:
    return Fraction(ticks, T)


def _fill_measures(part: stream.Stream, events: list[_Event], n_bars: int, arr: Arrangement,
                   staff_clef, top: bool, compress_rests: bool = False) -> None:
    bpb = arr.beats_per_bar
    bar = bpb * T
    trip_beats = {pos // T for e in events for pos in (e.start, e.end) if pos % T in (4, 8)}
    measures = [stream.Measure(number=i + 1) for i in range(n_bars)]

    def put(s: int, el) -> None:
        m = measures[s // bar]
        m.insert(_ql(s % bar), el)

    def rests(a: int, b: int) -> None:
        for s, d, whole in _pieces(a, b, bpb, True, trip_beats):
            r = note.Rest(quarterLength=_ql(d))
            if whole:
                r.fullMeasure = True
            put(s, r)

    cursor = 0
    for e in events:
        if e.start > cursor:
            rests(cursor, e.start)
        pieces = _pieces(e.start, e.end, bpb, False, trip_beats)
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
        cursor = e.end
    if cursor < n_bars * bar:
        rests(cursor, n_bars * bar)

    first = measures[0]
    first.insert(0, staff_clef)
    first.insert(0, key.KeySignature(max(-7, min(7, arr.key_fifths))).asKey(arr.key_mode))
    first.insert(0, meter.TimeSignature(f"{bpb}/4"))
    if top:
        first.insert(0, tempo.MetronomeMark(number=_bpm(arr), referent=duration.Duration(1.0)))
    measures[-1].rightBarline = m21bar.Barline("final")
    for m in measures:
        part.append(m)
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


def _layout(arr: Arrangement) -> _Layout:
    shift = lead_in_ticks(arr)
    flute = _tidy(_line_events(arr.flute, shift))
    rh = _tidy(_chord_events(arr.rh, shift, arr.flute))
    lh = _tidy(_chord_events(arr.lh, shift, arr.flute))
    last = max((e.end for e in (*flute, *rh, *lh)), default=0)
    bar = bar_ticks(arr)
    return _Layout(flute, rh, lh, max(1, -(-last // bar)), shift)


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
                   compress_rests=not _is_duet(arr))
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
    bpm = _bpm(arr)
    sec = 60.0 / bpm / T
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(arr.beats_per_bar, 4, 0.0))
    pm.key_signature_changes.append(
        pretty_midi.KeySignature(_key_number(max(-7, min(7, arr.key_fifths)), arr.key_mode), 0.0))
    parts = [("Flute", FLUTE_PROGRAM, lay.flute)]
    if _is_duet(arr):
        parts.append(("Piano", PIANO_PROGRAM, lay.rh + lay.lh))
    for name, program, events in parts:
        inst = pretty_midi.Instrument(program=program, name=name)
        for e in events:
            start, end = e.start * sec, e.end * sec
            if e.breath:                        # leave a little air before the next note
                end -= min(0.1, 0.25 * (end - start))
            for p in e.pitches:
                inst.notes.append(pretty_midi.Note(max(1, min(127, e.velocity)), p, start, end))
        pm.instruments.append(inst)
    pm.write(str(path))


def write_outputs(arr: Arrangement, title: str, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path, midi_path = out_dir / "score.musicxml", out_dir / "score.mid"
    lay = _layout(arr)
    _build(arr, title, lay).write("musicxml", fp=str(xml_path))
    _write_midi(arr, lay, midi_path)
    return {"musicxml": xml_path, "midi": midi_path}
