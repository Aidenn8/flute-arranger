"""Sheet music in: the exact notes of a written score, ready for the flute.

`load_score(path)` reads MusicXML (.musicxml, .xml, compressed .mxl) with
music21, or a MIDI file written by a notation program, and returns a
ScoreSource: the score's parts and staves, meter, key, tempo, title, and for
every part the line a flute could play from it, with the score's expression
marks. Everything is in ticks (TICKS_PER_BEAT per quarter note), tick 0 is the
downbeat of the first full bar, and a pickup's notes have negative ticks.
Nothing is guessed from sound: the rhythm is the score's own.

The line taken from a part (`top_line`):
- a single-line part (violin, flute, voice, ...) as written; where it plays
  two or more notes at once (double stops, divisi), the top note;
- a piano part (also organ, harp, guitar, ...): the top line of its upper
  staff, the right hand. At each new note the highest one is taken, except a
  note that starts under a higher note still held from before (an inner voice).
  The right hand is kept even where it rests while the tune moves to the left
  hand: a simple rule; "Which part is the tune?" can pick the left hand.
Ties are joined into one note; a grace note becomes a "grace" mark on the
note it leads into.

Which part is the tune by default ("auto", `auto_part`): the first
single-line part that has a fair share of the notes and isn't a bass part
(violin I in a quartet, the voice in a song, the soprano in a chorale),
otherwise the piano's right hand.

Kept from the score, as model.Mark (see there for the kinds): dynamics,
hairpins, slurs, staccato / tenuto / accent, fermatas, trills, mordents
(value "inverted" for the upper mordent), turns (value "inverted" too), and
tempo and character words. Single-note marks start at their note's tick;
slurs and hairpins run from their first note's start to their last note's
start (a hairpin over one note: to that note's end). Piano-only things
(pedal, fingering, arpeggio signs, hand and pedal words) are dropped.

Repeats are written out in full (repeat bars and 1st/2nd endings, in the
order music21 plays them), since the flute part has no repeat signs; words
such as "D.C. al Fine" stay as text.

The tick grid has 12 ticks per quarter: 16ths, 8th and 16th triplets fit
exactly; 32nds and other values that don't are rounded to the nearest tick,
with a warning.

MIDI files: only those that look written rather than played are read here
(`load_notated_midi`: a time signature, and nearly all notes starting on a
32nd / 16th-triplet grid). Their ticks come straight from the file. A MIDI
file has no bar lines, and notation programs start it at the pickup, so the
first downbeat is found as the place in the bar that puts the most note time
on downbeats and beats (the file's own start wins near-ties).

The parsed score is kept as JSON (ScoreSource.save, song_dir/score.json by
the pipeline), so arranging never parses the file again.
"""
from __future__ import annotations

import json
import os
import re
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from .config import TICKS_PER_BEAT as T
from .model import (DOTTED_BEAT, EIGHTH_BEAT, METERS, QUARTER_BEAT, Analysis, Arrangement,
                    ArrangeOptions, BeatGrid, Mark, Note, QNote, normalize_part, time_signature)

SCORE_EXTS = (".musicxml", ".xml", ".mxl")
MIDI_EXTS = (".mid", ".midi")
FORMAT = 1                     # score.json layout

DEFAULT_BPM = {QUARTER_BEAT: 100, DOTTED_BEAT: 72, EIGHTH_BEAT: 144}   # per beat, when the score has no tempo
DYNAMIC_VELOCITY = {"pppp": 25, "ppp": 32, "pp": 42, "p": 55, "mp": 68, "mf": 80,
                    "f": 96, "ff": 108, "fff": 118, "ffff": 124}
LINE_SHARE = 0.25              # auto: a single-line part needs this share of the busiest one's notes
BASS_MEAN = 55                 # auto: a line whose notes average below G3 is a bass part
NOTATED_SHARE = 0.9            # MIDI: share of onsets on the 32nd / 16th-triplet grid
GRID48 = 48                    # MIDI: positions per quarter a written note can start on
PHASE_MARGIN = 1.15            # MIDI: a bar phase must beat the file's own start by this much
GRACE_MAX_Q = 0.12             # MIDI: shorter off-grid notes are grace notes (in quarters)

NOTE_MARKS = {"staccato", "tenuto", "accent", "fermata", "trill", "mordent", "turn", "grace"}
PLACED_MARKS = {"dynamic", "text"}
NEAR = 2                       # ticks: a dynamic or word this soon after a note's start belongs to it

_ARTICULATIONS = {"Staccato": ("staccato",), "Staccatissimo": ("staccato",),
                  "Spiccato": ("staccato",), "Tenuto": ("tenuto",),
                  "DetachedLegato": ("tenuto", "staccato"), "Accent": ("accent",),
                  "StrongAccent": ("accent",)}
_EXPRESSIONS = {"Fermata": ("fermata", ""), "Trill": ("trill", ""),
                "InvertedMordent": ("mordent", "inverted"), "Mordent": ("mordent", ""),
                "InvertedTurn": ("turn", "inverted"), "Turn": ("turn", "")}
_PIANO_WORDS = re.compile(
    r"^(\*|ped\.?|pedal|sost(\.|enuto)?\s*ped(\.|al)?|una\s+corda|u\.\s*c\.|tre\s+corde|t\.\s*c\.|"
    r"m\.\s*[dgs]\.?|[rl]\.\s*h\.?|mano\s+(destra|sinistra)|8va|8vb|15ma|loco|"
    r"(con|senza)\s+ped(\.|ale)?|(with|without)\s+ped(\.|al)?)$", re.I)
_KEYBOARD_WORDS = re.compile(
    r"piano|klavier|clavier|keyboard|harpsichord|cembalo|clavecin|organ|orgel|organo|celest|"
    r"harp|arpa|guitar|gitarre|chitarra|lute|accordion|marimba|vibraphone|xylophone", re.I)
_GENERIC_TITLES = {"", "untitled", "untitled score", "title", "score", "music", "song"}
_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def note_name(pitch: int) -> str:
    return f"{_NAMES[pitch % 12]}{pitch // 12 - 1}"


def _plain(obj):
    """json.dumps fallback: numpy numbers (from pretty_midi) as Python numbers."""
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"{type(obj).__name__} can't go into score.json")


# ---------------------------------------------------------------- data


@dataclass
class ScorePart:
    name: str                 # what the page shows: "Violin I", "Piano, right hand"
    instrument: str = ""      # "Violin", "Piano", ... ("" when the score doesn't say)
    staff: int = 0            # 0: a whole part; 1, 2, ...: a staff of a keyboard part (1 = right hand)
    keyboard: bool = False
    melody: list[QNote] = field(default_factory=list)    # the line a flute could play
    notes: list[QNote] = field(default_factory=list)     # every note on this staff
    marks: list[Mark] = field(default_factory=list)      # the expression on `melody`
    warnings: list[str] = field(default_factory=list)    # about this part only

    @property
    def label(self) -> str:
        if not self.melody:
            return self.name
        lo = min(q.pitch for q in self.melody)
        hi = max(q.pitch for q in self.melody)
        return f"{self.name} ({note_name(lo)}-{note_name(hi)})"

    def mean_pitch(self) -> float:
        return sum(q.pitch for q in self.melody) / len(self.melody) if self.melody else 0.0


@dataclass
class ScoreSource:
    parts: list[ScorePart]
    title: str = ""
    composer: str = ""
    beats_per_bar: int = 4
    beat_ticks: int = QUARTER_BEAT
    meter: str = "4/4"        # the time signature as the score writes it
    key_fifths: int = 0
    key_mode: str = "major"
    quarter_bpm: float = 100.0
    pickup: int = 0           # ticks of pickup before the first full bar
    auto_part: int = 0
    origin: str = "musicxml"  # "musicxml" or "midi"
    warnings: list[str] = field(default_factory=list)

    @property
    def bpm(self) -> float:
        """Beats (beat_ticks long) per minute."""
        return self.quarter_bpm * T / self.beat_ticks

    def to_dict(self) -> dict:
        d = asdict(self)
        d["format"] = FORMAT
        for p, src in zip(d["parts"], self.parts):
            p["melody"] = [[q.pitch, q.start, q.dur, q.velocity] for q in src.melody]
            p["notes"] = [[q.pitch, q.start, q.dur] for q in src.notes]
            p["marks"] = [[m.kind, m.start, m.end, m.value] for m in src.marks]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ScoreSource":
        if d.get("format") != FORMAT:
            raise ValueError("score.json from another version")
        d = dict(d)
        d.pop("format", None)
        parts = []
        for p in d.pop("parts"):
            p = dict(p)
            p["melody"] = [QNote(int(a), int(b), int(c), int(v)) for a, b, c, v in p["melody"]]
            p["notes"] = [QNote(int(a), int(b), int(c)) for a, b, c in p["notes"]]
            p["marks"] = [Mark(str(k), int(s), int(e), str(v)) for k, s, e, v in p["marks"]]
            parts.append(ScorePart(**p))
        return cls(parts=parts, **d)

    def save(self, path: Path) -> None:
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), default=_plain))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "ScoreSource":
        return cls.from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------- meter


def meter_ticks(num: int, den: int) -> tuple[int, int, bool]:
    """(beats per bar, ticks per beat, written exactly) for a time signature.

    2/4, 3/4, 4/4, 5/4, 6/4: quarter beats; 6/8, 9/8, 12/8: dotted quarters;
    3/8, 5/8, 7/8: eighths (model.time_signature gives them back as written).
    Others are written with the same bar length in one of those: 2/2 as 4/4,
    3/2 as 6/4, 12/16 as 6/8 (exact = False).
    """
    if num <= 0 or den <= 0:
        return 4, QUARTER_BEAT, False
    if den == 4:
        return num, QUARTER_BEAT, True
    if den == 8:
        if num % 3 == 0 and num > 3:
            return num // 3, DOTTED_BEAT, True
        return num, EIGHTH_BEAT, True
    if den == 16 and num % 6 == 0:
        return num // 6, DOTTED_BEAT, False
    bar = Fraction(num * 4 * T, den)
    if bar.denominator == 1:
        bar = int(bar)
        if bar % QUARTER_BEAT == 0:
            return bar // QUARTER_BEAT, QUARTER_BEAT, False
        if bar % EIGHTH_BEAT == 0:
            return bar // EIGHTH_BEAT, EIGHTH_BEAT, False
    return 4, QUARTER_BEAT, False


def _meter_choice(src: ScoreSource, meter: str) -> tuple[int, int]:
    """The score's own meter, or the one picked on the page (same ticks, other bars)."""
    if meter not in METERS:
        return src.beats_per_bar, src.beat_ticks
    bpb, unit = METERS[meter]
    if meter == "3/8" and src.beat_ticks == DOTTED_BEAT:
        return 1, DOTTED_BEAT
    return bpb, unit


# ---------------------------------------------------------------- the line


@dataclass
class _Ev:
    start: int
    end: int
    pitch: int                 # the top pitch of a chord
    velocity: int = 80
    refs: list = field(default_factory=list)   # what marks refer to (first = the onset)
    tie_next: bool = False
    grace: bool = False


def top_line(events: list[_Ev]) -> list[_Ev]:
    """The highest line through `events` (see the module notes).

    Returns new events: one per line note, ties joined (refs collects every
    event a note absorbed), each cut at the next note's start."""
    evs = sorted((e for e in events if not e.grace and e.end > e.start),
                 key=lambda e: (e.start, -e.pitch))
    line: list[_Ev] = []
    active: list[_Ev] = []
    i = 0
    while i < len(evs):
        t = evs[i].start
        j = i
        while j < len(evs) and evs[j].start == t:
            j += 1
        top = evs[i]
        active = [a for a in active if a.end > t]
        if not any(a.pitch >= top.pitch for a in active):
            line.append(replace(top, refs=list(top.refs)))
        active.extend(evs[i:j])
        i = j
    joined: list[_Ev] = []
    for e in line:
        prev = joined[-1] if joined else None
        if prev and prev.tie_next and prev.pitch == e.pitch and prev.end == e.start:
            prev.end = e.end
            prev.tie_next = e.tie_next
            prev.refs.extend(e.refs)
        else:
            joined.append(e)
    for a, b in zip(joined, joined[1:]):
        a.end = min(a.end, b.start)
    return [e for e in joined if e.end > e.start]


def _graces_to_marks(graces: list[_Ev], line: list[_Ev], before: int = 0) -> list[Mark]:
    """Grace notes -> "grace" marks on the line note they lead into: the one
    starting at their tick, or (MIDI, where a grace is played just before its
    note) the first one starting within `before` ticks after."""
    starts = [e.start for e in line]
    groups: dict[int, list[int]] = {}
    for g in sorted(graces, key=lambda g: g.start):
        i = bisect_left(starts, g.start)
        if i < len(starts) and starts[i] - g.start <= before:
            groups.setdefault(i, []).append(g.pitch)
    out = []
    for i, pitches in groups.items():
        run = pitches + [line[i].pitch]
        if len(pitches) > 1 and any(abs(b - a) > 2 for a, b in zip(run, run[1:])):
            continue              # a rolled chord (a string player's multiple stop), not a tune ornament
        out += [Mark("grace", starts[i], 0, str(p)) for p in pitches]
    return out


def _apply_dynamics(line: list[QNote], marks: list[Mark]) -> None:
    """Velocity from the dynamic marking in force (for the preview's playback)."""
    levels = sorted((m.start, DYNAMIC_VELOCITY[m.value]) for m in marks
                    if m.kind == "dynamic" and m.value in DYNAMIC_VELOCITY)
    if not levels:
        return
    ticks = [t for t, _ in levels]
    for q in line:
        i = bisect_right(ticks, q.start + NEAR) - 1
        if i >= 0:
            q.velocity = levels[i][1]


def _dedupe(marks: list[Mark]) -> list[Mark]:
    seen, out = set(), []
    for m in sorted(marks, key=lambda m: (m.start, m.kind, m.end, m.value)):
        k = (m.kind, m.start, m.end, m.value)
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out


# ---------------------------------------------------------------- MusicXML (music21)


class _Ticks:
    """Quarter-note offsets -> ticks from the first full bar; remembers where
    a value fell off the 12-ticks-a-quarter grid."""

    def __init__(self, origin):
        self.origin = Fraction(origin).limit_denominator(100000)
        self.inexact: list[int] = []

    def __call__(self, offset, note: bool = False) -> int:
        x = (Fraction(offset).limit_denominator(100000) - self.origin) * T
        r = round(x)
        if note and x != r:
            self.inexact.append(r)
        return r


def _clean_words(text) -> str:
    return " ".join(str(text or "").split())


def _class_names(obj) -> list[str]:
    return [c.__name__ for c in type(obj).__mro__]


def _note_marks(el, start: int) -> list[Mark]:
    out = []
    for a in getattr(el, "articulations", ()):
        for name in _class_names(a):
            if name in _ARTICULATIONS:
                out += [Mark(k, start) for k in _ARTICULATIONS[name]]
                break
    for x in getattr(el, "expressions", ()):
        for name in _class_names(x):
            if name in _EXPRESSIONS:
                kind, value = _EXPRESSIONS[name]
                out.append(Mark(kind, start, 0, value))
                break
    return out


def _tied_on(el, pitch: int) -> bool:
    """Is the note `pitch` of this note or chord tied to the next one?"""
    t = getattr(el, "tie", None)
    if hasattr(el, "notes") and not hasattr(el, "pitch"):      # a chord
        for n in el.notes:
            if n.pitch.midi == pitch and n.tie is not None:
                t = n.tie
                break
    return t is not None and t.type in ("start", "continue")


def _instrument_info(part, n_staves: int) -> tuple[str, str, bool]:
    """(name, instrument, keyboard) of a music21 part."""
    from music21 import instrument as m21inst
    inst = part.getInstrument(returnDefault=False)
    cls = type(inst).__name__ if inst is not None else ""
    if cls in ("Instrument", "") or inst is None:
        cls = ""
    name = _clean_words(part.partName) or _clean_words(getattr(inst, "partName", "")) or cls
    keyboard = (n_staves >= 2
                or isinstance(inst, (m21inst.KeyboardInstrument, m21inst.Harp, m21inst.Guitar,
                                     m21inst.PitchedPercussion))
                or bool(_KEYBOARD_WORDS.search(name or "")))
    return name, cls, keyboard


def _groups(score) -> list[list]:
    """The score's staves, grouped into parts (a piano's two staves together)."""
    from music21 import stream
    groups: list[list] = []
    keys: list[str] = []
    for p in score.parts:
        if isinstance(p, stream.PartStaff):
            k = re.sub(r"-Staff\d+$", "", str(p.id))
            if keys and keys[-1] == k:
                groups[-1].append(p)
                continue
            keys.append(k)
        else:
            keys.append(f"part{len(keys)}")
        groups.append([p])
    return groups


def _measure_table(staff, ticks: _Ticks) -> list[tuple[int, int]]:
    from music21 import stream
    ms = list(staff.getElementsByClass(stream.Measure))
    table = []
    for i, m in enumerate(ms):
        a = ticks(m.offset)
        b = ticks(ms[i + 1].offset) if i + 1 < len(ms) else ticks(m.offset + m.duration.quarterLength)
        table.append((a, max(a, b)))
    return table


def _play_order(staff) -> tuple[list[int] | None, str]:
    """Measure indices in playing order with the repeat bars and 1st/2nd
    endings written out; None to keep the score as written.

    An end-repeat goes back to the last start-repeat, or, without one, to the
    bar after the previous repeated section (the usual reading; music21's own
    expander goes back to the beginning). An ending bracket is played on the
    times through its numbers say. D.C. and D.S. are left as words."""
    from music21 import bar, spanner, stream
    ms = list(staff.getElementsByClass(stream.Measure))
    n = len(ms)
    where = {id(m): i for i, m in enumerate(ms)}
    starts = {i for i, m in enumerate(ms)
              if isinstance(m.leftBarline, bar.Repeat) and m.leftBarline.direction == "start"}
    ends = {}
    for i, m in enumerate(ms):
        b = m.rightBarline
        if isinstance(b, bar.Repeat) and b.direction == "end":
            ends[i] = max(2, int(b.times or 2))
    if not ends:
        return None, ""
    endings: dict[int, set[int]] = {}
    for rb in staff.spannerBundle.getByClass(spanner.RepeatBracket):
        nums = {int(k) for k in (getattr(rb, "numberRange", None) or ()) if int(k) > 0} or {1}
        for m in rb.getSpannedElements():
            if id(m) in where:
                endings[where[id(m)]] = nums
    order: list[int] = []
    i, start, times, jumped = 0, 0, 1, False
    while i < n:
        if len(order) > 20 * n:
            return None, ("The repeat signs in this score couldn't be followed, so the flute part "
                          "is written straight through once: play the repeats as the original marks them.")
        if i in starts and not jumped:
            start, times = i, 1
        jumped = False
        nums = endings.get(i)
        if nums is not None and times not in nums:
            i += 1                        # an ending for another time through
            continue
        order.append(i)
        if i in ends and times < ends[i]:
            times += 1
            i, jumped = start, True
            continue
        if i in ends or (nums is not None and max(nums) <= times and i + 1 not in endings):
            start, times = i + 1, 1       # past the repeated section
        i += 1
    if order == list(range(n)):
        return None, ""
    return order, "The repeats in the original are written out in full."


class _Unfold:
    """Moves ticks from the score as written to the score played with its repeats."""

    def __init__(self, table: list[tuple[int, int]], order: list[int]):
        self.table = table
        self.starts = [a for a, _ in table]
        self.slots: list[tuple[int, int]] = []          # (measure index, new start)
        cursor = table[order[0]][0]
        for i in order:
            a, b = table[i]
            self.slots.append((i, cursor))
            cursor += b - a
        self.where: dict[int, list[int]] = {}
        for k, (i, _) in enumerate(self.slots):
            self.where.setdefault(i, []).append(k)

    def _measure(self, t: int) -> int | None:
        i = bisect_right(self.starts, t) - 1
        if i < 0:
            return None
        a, b = self.table[i]
        return i if t < b or i == len(self.table) - 1 else None

    def spans(self, start: int, end: int):
        """(new start, new end) for each time [start, end) is played."""
        i = self._measure(start)
        if i is None:
            return
        a = self.table[i][0]
        for k in self.where.get(i, ()):
            new = self.slots[k][1] + start - a
            # held on only while the following slots play the following measures
            reach, kk, ii = self.table[i][1], k, i
            while end > reach and kk + 1 < len(self.slots) and self.slots[kk + 1][0] == ii + 1:
                kk, ii = kk + 1, ii + 1
                reach = self.table[ii][1]
            yield new, new + min(end, reach) - start

    def notes(self, notes: list[QNote]) -> list[QNote]:
        out = [replace(q, start=s, dur=e - s) for q in notes
               for s, e in self.spans(q.start, q.end) if e > s]
        return sorted(out, key=lambda q: (q.start, q.pitch))

    def marks(self, marks: list[Mark]) -> list[Mark]:
        out = []
        for m in marks:
            for s, e in self.spans(m.start, max(m.end, m.start + 1)):
                out.append(replace(m, start=s, end=(e if m.end else 0)))
        return sorted(out, key=lambda m: (m.start, m.kind))


def _music21_score(path: Path):
    from music21 import converter, stream
    try:
        obj = converter.parse(str(path), format="musicxml", forceSource=True)
    except Exception as e:                # music21 raises many kinds for bad files
        raise ValueError("That file isn't sheet music this can read. Please upload MusicXML "
                         "(.musicxml, .mxl or .xml, as MuseScore, Finale or Sibelius export it).") from e
    if isinstance(obj, stream.Opus):
        scores = list(obj.scores)
        if not scores:
            raise ValueError("That file has no music in it.")
        obj = scores[0]
    if not isinstance(obj, stream.Score):
        s = stream.Score()
        s.insert(0, obj)
        obj = s
    return obj


def _read_musicxml(path: Path) -> ScoreSource:
    from music21 import key as m21key, meter, stream, tempo
    score = _music21_score(path)
    try:
        if not all(p.atSoundingPitch is True for p in score.parts):
            score.toSoundingPitch(inPlace=True)        # clarinet, horn, ... parts are written transposed
    except Exception:
        pass
    groups = [g for g in _groups(score) if any(len(s.recurse().notes) for s in g)]
    if not groups:
        raise ValueError("That sheet music has no notes in it.")
    first = groups[0][0]
    measures = list(first.getElementsByClass(stream.Measure))
    warnings: list[str] = []

    # meter, pickup
    sigs = list(first.recurse().getElementsByClass(meter.TimeSignature))
    ts = sigs[0] if sigs else meter.TimeSignature("4/4")
    num, den = ts.numerator, ts.denominator
    bpb, beat, exact = meter_ticks(num, den)
    if not exact:
        warnings.append(f"The original is in {num}/{den}; the flute part is written in "
                        f"{'/'.join(map(str, time_signature(bpb, beat)))}, which has the same bar length.")
    origin = Fraction(0)
    if len(measures) >= 2:
        m0 = measures[0]
        bar_q = Fraction(ts.barDuration.quarterLength).limit_denominator(1000)
        if Fraction(m0.duration.quarterLength).limit_denominator(1000) < bar_q:
            origin = Fraction(measures[1].offset).limit_denominator(100000)
    ticks = _Ticks(origin)
    bar = bpb * beat
    changed = [s for s in sigs[1:] if s.ratioString != ts.ratioString]
    if changed:
        where = changed[0].measureNumber
        warnings.append(f"The time changes to {changed[0].ratioString} in bar {where}; the flute part "
                        f"keeps {'/'.join(map(str, time_signature(bpb, beat)))} all through, so its bar "
                        "lines differ from the original from there on.")

    # key
    keys = list(first.recurse().getElementsByClass(m21key.KeySignature))
    fifths = keys[0].sharps if keys else 0
    mode = getattr(keys[0], "mode", None) if keys else None
    if len({k.sharps for k in keys}) > 1:
        other = next(k for k in keys if k.sharps != fifths)
        warnings.append(f"The key changes in bar {other.measureNumber}; the flute part keeps the "
                        "opening key signature and writes the other notes with accidentals.")

    # tempo, and the tempo words every part shares
    qbpm = None
    tempo_marks: list[Mark] = []
    for staff in (s for g in groups for s in g):
        flat = staff.flatten()
        for el in flat.getElementsByClass(tempo.TempoIndication):
            t = ticks(flat.elementOffset(el))
            # music21 makes up a word for a bare metronome number ("maestoso" for 90): not the score's
            words = "" if getattr(el, "textImplicit", False) else _clean_words(getattr(el, "text", "") or "")
            if words and not _PIANO_WORDS.match(words):
                tempo_marks.append(Mark("text", t, 0, words))
            if qbpm is None and isinstance(el, tempo.MetronomeMark):
                try:
                    q = el.getQuarterBPM()
                except Exception:
                    q = None
                if q and q > 0:
                    qbpm = float(q)
            if qbpm is None and isinstance(el, tempo.TempoText):
                try:
                    mm = el.getMetronomeMark()
                    q = mm.getQuarterBPM() if mm is not None else None
                except Exception:
                    q = None
                if q and q > 0:
                    qbpm = float(q)
    if qbpm is None:
        qbpm = DEFAULT_BPM[beat if beat in DEFAULT_BPM else QUARTER_BEAT] * beat / T

    spanners = list(score.spannerBundle)
    table = _measure_table(first, ticks)
    order, note = _play_order(first)
    if note:
        warnings.append(note)
    unfold = _Unfold(table, order) if order else None

    parts: list[ScorePart] = []
    names_seen: dict[str, int] = {}
    for g in groups:
        name, inst, keyboard = _instrument_info(g[0], len(g))
        base = name or f"Part {len(names_seen) + 1}"
        names_seen[base] = names_seen.get(base, 0) + 1
        if names_seen[base] > 1:
            base = f"{base} ({names_seen[base]})"
        shared: list[Mark] = []                # a piano's dynamics, hairpins and words serve both hands
        staves = []
        for k, staff in enumerate(g, 1):
            staves.append(_read_staff(staff, ticks, spanners))
            shared += [m for m in staves[-1][2] if m.kind in ("dynamic", "cresc", "dim", "text")]
        for k, (line, every, marks, inexact) in enumerate(staves, 1):
            if not line:
                continue
            if len(g) == 1:
                label = base
            elif len(g) == 2:
                label = f"{base}, {'right' if k == 1 else 'left'} hand"
            else:
                label = f"{base}, staff {k}"
            own = marks + (shared if len(g) > 1 else []) + tempo_marks
            melody = [QNote(e.pitch, e.start, e.end - e.start, e.velocity) for e in line]
            own = _dedupe(own)
            if unfold:
                melody, every, own = unfold.notes(melody), unfold.notes(every), unfold.marks(own)
            _apply_dynamics(melody, own)
            pw = []
            if inexact:
                pw.append(_inexact_warning(inexact, bar))
            parts.append(ScorePart(label, inst, k if len(g) > 1 else 0, keyboard, melody,
                                   every, own, pw))
    if not parts:
        raise ValueError("That sheet music has no notes in it.")

    if mode not in ("major", "minor"):
        mode = _guess_mode(fifths, [q for p in parts for q in p.notes])
    md = score.metadata
    title = _clean_words(getattr(md, "title", "") if md else "")
    if title.lower() in _GENERIC_TITLES or title.lower().endswith((".xml", ".mxl", ".musicxml")):
        title = ""
    composer = _clean_words(getattr(md, "composer", "") if md else "")
    return ScoreSource(parts=parts, title=title, composer=composer, beats_per_bar=bpb,
                       beat_ticks=beat, meter=f"{num}/{den}", key_fifths=max(-7, min(7, fifths)),
                       key_mode=mode, quarter_bpm=round(qbpm, 3),
                       pickup=int(origin * T), auto_part=auto_part(parts), origin="musicxml",
                       warnings=warnings)


def _read_staff(staff, ticks: _Ticks, spanners) -> tuple[list[_Ev], list[QNote], list[Mark], list[int]]:
    """(top line, every note, marks, ticks that weren't exact) of one staff."""
    from music21 import dynamics, expressions, harmony, note as m21note, spanner
    flat = staff.flatten()
    offsets = {}
    elements = {}
    events: list[_Ev] = []
    graces: list[_Ev] = []
    every: list[tuple[QNote, bool]] = []
    ticks.inexact = []
    for el in flat.notes:
        if isinstance(el, (harmony.Harmony, m21note.Unpitched)):
            continue
        pitches = [p.midi for p in getattr(el, "pitches", ())]
        if not pitches:
            continue
        off = flat.elementOffset(el)
        offsets[id(el)] = off
        elements[id(el)] = el
        start = ticks(off, note=True)
        top = max(pitches)
        if el.duration.isGrace:
            graces.append(_Ev(start, start, top, refs=[id(el)], grace=True))
            continue
        end = ticks(off + el.duration.quarterLength, note=True)
        if end <= start:
            end = start + 1
        events.append(_Ev(start, end, top, refs=[id(el)], tie_next=_tied_on(el, top)))
        for p in sorted(set(pitches)):
            every.append((QNote(p, start, end - start), _tied_on(el, p)))
    inexact = sorted(set(ticks.inexact))
    line = top_line(events)
    owner = {r: i for i, e in enumerate(line) for r in e.refs}

    marks: list[Mark] = []
    for e in line:
        marks += _note_marks(elements[e.refs[0]], e.start)
    marks += _graces_to_marks(graces, line)
    for el in flat.getElementsByClass(dynamics.Dynamic):
        value = _clean_words(el.value)
        if value:
            marks.append(Mark("dynamic", ticks(flat.elementOffset(el)), 0, value))
    for el in flat.getElementsByClass(expressions.TextExpression):
        words = _clean_words(el.content)
        if words and not _PIANO_WORDS.match(words):
            marks.append(Mark("text", ticks(flat.elementOffset(el)), 0, words))
    for el in flat.getElementsByClass("RepeatExpression"):      # Fine, D.C., Segno, ... as words
        try:
            words = _clean_words(el.getText())
        except Exception:                 # music21 keeps no text for some of these
            words = ""
        if words:
            marks.append(Mark("text", ticks(flat.elementOffset(el)), 0, words))
    for sp in spanners:
        els = sp.getSpannedElements()
        if not els:
            continue
        a, b = els[0], els[-1]
        if isinstance(sp, spanner.Slur):
            i, j = owner.get(id(a)), owner.get(id(b))
            if i is not None and j is not None and j > i:
                marks.append(Mark("slur", line[i].start, line[j].start))
        elif isinstance(sp, dynamics.DynamicWedge):
            if id(a) not in offsets and not _in(flat, a):
                continue
            s = ticks(_offset(flat, a, offsets))
            if b is a or len(els) == 1:          # over one note: to its end
                e = ticks(_offset(flat, a, offsets) + a.duration.quarterLength)
            else:                                # first note to last note, like a slur
                e = ticks(_offset(flat, b, offsets))
            kind = "cresc" if isinstance(sp, dynamics.Crescendo) else "dim"
            marks.append(Mark(kind, s, max(e, s)))
        elif isinstance(sp, expressions.TrillExtension):
            i = owner.get(id(a))
            if i is not None:
                marks.append(Mark("trill", line[i].start))
    return line, _join_ties(every), _dedupe(marks), inexact


def _in(flat, el) -> bool:
    try:
        flat.elementOffset(el)
        return True
    except Exception:                    # music21: "not in this stream"
        return False


def _offset(flat, el, offsets: dict):
    if id(el) in offsets:
        return offsets[id(el)]
    return flat.elementOffset(el)


def _join_ties(every: list[tuple[QNote, bool]]) -> list[QNote]:
    """Every note of a staff, tied notes joined."""
    out: list[QNote] = []
    open_: dict[tuple[int, int], int] = {}            # (pitch, end) -> index of a note tied on
    for q, tied in sorted(every, key=lambda x: (x[0].start, x[0].pitch)):
        i = open_.pop((q.pitch, q.start), None)
        if i is not None:
            out[i].dur = q.end - out[i].start
        else:
            out.append(replace(q))
            i = len(out) - 1
        if tied:
            open_[(q.pitch, out[i].end)] = i
    return out


def _guess_mode(fifths: int, notes: list[QNote]) -> str:
    from .harmony import detect_key
    f, mode = detect_key([(q.pitch, q.dur) for q in notes] or [(60, 1.0)])
    return mode if f == fifths else "major"


def _inexact_warning(ticks: list[int], bar: int) -> str:
    bars = sorted({t // bar + 1 + (1 if t < 0 else 0) for t in ticks})
    shown = ", ".join(str(b) for b in bars[:6]) + (", ..." if len(bars) > 6 else "")
    return (f"Some notes are quicker than this app's finest grid (32nds or similar, "
            f"bar{'s' if len(bars) > 1 else ''} {shown} of the original): they were rounded, "
            "so check those bars against the original.")


# ---------------------------------------------------------------- MIDI written by a notation program


def _on_grid(q: Fraction, tol: Fraction = Fraction(1, 2400)) -> bool:
    """Does `q` (quarters) sit on the 48-a-quarter grid, within `tol` quarters?"""
    x = q * GRID48
    return abs(x - round(x)) <= tol * GRID48


def _snap_end(x: Fraction, tol: Fraction = Fraction(1, 20)) -> int:
    """An end in 12-tick units: exact when it's on the tick grid (within `tol`),
    else the nearest 16th or 8th-triplet point (notation programs shorten notes a little)."""
    r = round(x)
    if abs(x - r) <= tol:
        return r
    c3, c4 = 3 * round(x / 3), 4 * round(x / 4)
    return c3 if abs(c3 - x) <= abs(c4 - x) else c4


def _bar_phase(notes: list[tuple[int, int]], bar: int, beat: int) -> int:
    """Where the bar lines fall in a MIDI file (ticks after its start, 0..bar-1):
    the phase that puts the most note time on downbeats (and some on beats)."""
    if bar <= 0 or not notes:
        return 0
    score = {}
    for o in range(bar):
        if o % 3 and o % 4:
            continue
        s = 0.0
        for start, dur in notes:
            pos = (start - o) % bar
            w = min(dur, bar)
            if pos == 0:
                s += 2.0 * w
            elif pos % beat == 0:
                s += 0.5 * w
        score[o] = s
    best = max(score, key=lambda o: (score[o], o == 0))
    return best if score[best] > PHASE_MARGIN * score[0] else 0


def _midi_key(pm, notes: list[QNote]) -> tuple[int, str]:
    from .harmony import detect_key
    ks = sorted(pm.key_signature_changes, key=lambda k: k.time)
    if ks:
        n = int(ks[0].key_number)
        tonic, minor = n % 12, n >= 12
        major = (tonic + 3) % 12 if minor else tonic
        f = (major * 7) % 12
        return (f - 12 if f > 6 else f), ("minor" if minor else "major")
    return detect_key([(q.pitch, q.dur) for q in notes] or [(60, 1.0)])


_MIDI_KEYBOARD = set(range(0, 24)) | set(range(24, 32)) | {46}


def _read_midi(path: Path, notated_only: bool) -> ScoreSource | None:
    import pretty_midi
    try:
        pm = pretty_midi.PrettyMIDI(str(path))
    except Exception as e:                 # broken file: pretty_midi/mido raise many kinds
        if notated_only:
            return None
        raise ValueError("That MIDI file can't be read.") from e
    insts = [i for i in pm.instruments if not i.is_drum and i.notes]
    if not insts:
        if notated_only:
            return None
        raise ValueError("This MIDI file has no notes.")
    res = pm.resolution

    def q(t: float) -> Fraction:
        return Fraction(int(pm.time_to_tick(t)), int(res))

    # a written position is stored to the nearest MIDI tick: half a tick of slack
    # (a file at 220 ticks a quarter can't hold a triplet exactly)
    tol = Fraction(1, 2 * int(res)) + Fraction(1, 10000)
    raw = [[(n, q(n.start), q(n.end)) for n in inst.notes] for inst in insts]
    starts = sum(_on_grid(s, tol) for rows in raw for _, s, _ in rows)
    ends = sum(_on_grid(e, tol) for rows in raw for _, _, e in rows)
    total = sum(len(rows) for rows in raw)
    sigs = sorted(pm.time_signature_changes, key=lambda s: s.time)
    if notated_only and (not sigs or min(starts, ends) < NOTATED_SHARE * total):
        return None
    warnings: list[str] = []
    if not sigs:
        warnings.append("This MIDI file doesn't say its time signature, so it's written in 4/4.")
    num, den = (sigs[0].numerator, sigs[0].denominator) if sigs else (4, 4)
    pickup_at = None
    if len(sigs) >= 2:                     # a short first bar of its own: a pickup
        a, b = sigs[0], sigs[1]
        first_bar = Fraction(a.numerator * 4, a.denominator)
        if q(a.time) == 0 and q(b.time) == first_bar and first_bar < Fraction(b.numerator * 4, b.denominator):
            num, den, pickup_at = b.numerator, b.denominator, first_bar
            sigs = sigs[1:]
    bpb, beat, exact = meter_ticks(num, den)
    if not exact:
        warnings.append(f"The original is in {num}/{den}; the flute part is written in "
                        f"{'/'.join(map(str, time_signature(bpb, beat)))}, which has the same bar length.")
    if any((s.numerator, s.denominator) != (num, den) for s in sigs[1:]):
        warnings.append("The time signature changes in this file; the flute part keeps the first one "
                        "all through, so later bar lines may differ from the original.")
    bar = bpb * beat

    # notes in 12-tick units from the file's start; graces apart
    per_inst = []
    for rows in raw:
        evs, graces, inexact = [], [], []
        for k, (n, s, e) in enumerate(rows):
            dur_q = e - s
            x0 = s * T
            if not _on_grid(s, tol):
                if dur_q < Fraction(GRACE_MAX_Q).limit_denominator(100):
                    graces.append(_Ev(round(x0), round(x0), int(n.pitch), grace=True))
                    continue
                if dur_q >= Fraction(1, 2):
                    # a grace note joined to its main note: start on the beat part it was written on
                    up = min(-(-x0 // 3) * 3, -(-x0 // 4) * 4)
                    if up - x0 < Fraction(12, 5):
                        x0 = Fraction(up)
            start = round(x0)
            if abs(x0 - start) > tol * T:
                inexact.append(start)
            end = _snap_end(e * T, max(Fraction(1, 20), tol * T))
            if end <= start:
                end = start + max(1, round(dur_q * T))
            evs.append(_Ev(start, end, int(n.pitch), int(n.velocity), refs=[k]))
        per_inst.append((evs, graces, inexact))

    if pickup_at is not None:
        d0 = int(pickup_at * T)
    else:
        allev = [(e.start, e.end - e.start) for evs, _, _ in per_inst for e in evs]
        phase = _bar_phase(allev, bar, beat)
        first = min(s for s, _ in allev)
        d0 = phase + bar * -(-(first - phase) // bar)
        if 4 * (d0 - first) > 3 * bar:
            d0 -= bar                      # music starting just after a bar line: a rest, not a long pickup

    parts: list[ScorePart] = []
    for idx, (inst, (evs, graces, inexact)) in enumerate(zip(insts, per_inst)):
        for e in evs + graces:
            e.start -= d0
            e.end -= d0
        line = top_line(evs)
        # close the small gap a grace note took from the note before it
        gstarts = sorted(g.start for g in graces)
        for a, b in zip(line, line[1:]):
            gap = b.start - a.end
            if 0 < gap < 3 and bisect_left(gstarts, a.end) < bisect_left(gstarts, b.start):
                a.end = b.start
        marks = _graces_to_marks(graces, line, before=T)
        melody = [QNote(e.pitch, e.start, e.end - e.start, e.velocity) for e in line]
        every = sorted((QNote(e.pitch, e.start, e.end - e.start, e.velocity) for e in evs),
                       key=lambda x: (x.start, x.pitch))
        gm = pretty_midi.program_to_instrument_name(inst.program)
        tname = _clean_words(inst.name).strip(":")
        label = f"Track {idx + 1}: {gm}" + (f" ({tname})" if tname and not tname.startswith("\\") else "")
        pw = [_inexact_warning([t - d0 for t in inexact], bar)] if inexact else []
        if melody:
            parts.append(ScorePart(label, gm, 0, inst.program in _MIDI_KEYBOARD, melody,
                                   every, marks, pw))
    if not parts:
        raise ValueError("This MIDI file has no notes.")
    fifths, mode = _midi_key(pm, [x for p in parts for x in p.notes])
    _, tempi = pm.get_tempo_changes()
    qbpm = float(tempi[0]) if len(tempi) and tempi[0] > 0 else DEFAULT_BPM[beat] * beat / T
    return ScoreSource(parts=parts, beats_per_bar=bpb, beat_ticks=beat, meter=f"{num}/{den}",
                       key_fifths=fifths, key_mode=mode, quarter_bpm=round(qbpm, 3),
                       pickup=max(0, -min(q.start for p in parts for q in p.melody)),
                       auto_part=auto_part(parts), origin="midi", warnings=warnings)


# ---------------------------------------------------------------- entry points


def load_score(path: Path) -> ScoreSource:
    """Read sheet music (MusicXML/MXL) or a MIDI file. Raises ValueError when it can't."""
    path = Path(path)
    if path.suffix.lower() in MIDI_EXTS:
        return _read_midi(path, notated_only=False)
    return _read_musicxml(path)


def load_notated_midi(path: Path) -> ScoreSource | None:
    """The MIDI file as a score if it was written in a notation program (it has a
    time signature and its notes sit on the written-rhythm grid); None if it
    looks played in, which is left to the quantizer."""
    return _read_midi(Path(path), notated_only=True)


# ---------------------------------------------------------------- which part


def auto_part(parts: list[ScorePart]) -> int:
    """The part most likely to carry the tune (see the module notes)."""
    lines = [i for i, p in enumerate(parts) if not p.keyboard and p.melody]
    if lines:
        busiest = max(len(parts[i].melody) for i in lines)
        good = [i for i in lines if len(parts[i].melody) >= LINE_SHARE * busiest
                and parts[i].mean_pitch() >= BASS_MEAN]
        if good:
            return good[0]
    keys = [i for i, p in enumerate(parts) if p.keyboard and p.melody]
    upper = [i for i in keys if parts[i].staff == 1]
    if upper:
        return upper[0]
    if keys:
        return max(keys, key=lambda i: parts[i].mean_pitch())
    return lines[0] if lines else 0


def choose_part(src: ScoreSource, choice) -> tuple[int, list[str]]:
    """(part index, warnings) for ArrangeOptions.part: "auto", a number or a name."""
    c = normalize_part(choice)
    auto = src.auto_part if 0 <= src.auto_part < len(src.parts) else 0
    if c == "auto":
        return auto, []
    idx = None
    if c.isdigit() and int(c) < len(src.parts):
        idx = int(c)
    else:
        low = c.casefold()
        for test in (lambda p: p.name.casefold() == low,
                     lambda p: p.instrument.casefold() == low,
                     lambda p: low in p.name.casefold()):
            idx = next((i for i, p in enumerate(src.parts) if test(p)), None)
            if idx is not None:
                break
    if idx is None:
        return auto, [f'This score has no part "{c}", so the tune was taken from '
                      f"{src.parts[auto].name}."]
    if not src.parts[idx].melody:
        return auto, [f"{src.parts[idx].name} has no notes, so the tune was taken from "
                      f"{src.parts[auto].name}."]
    return idx, []


def part_choices(song_dir: Path) -> list[tuple[str, str]] | None:
    """The page's "Which part is the tune?" choices for a song read from sheet
    music: [("auto", ...), ("0", "Violin I (G3-E6)"), ...]; None for other songs."""
    try:
        d = json.loads((Path(song_dir) / "score.json").read_text())
        parts = [p["name"] for p in d["parts"]]
        ranges = []
        for p in d["parts"]:
            ps = [row[0] for row in p["melody"]]
            ranges.append(f" ({note_name(min(ps))}-{note_name(max(ps))})" if ps else "")
        auto = int(d.get("auto_part", 0))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    if not parts:
        return None
    auto_name = parts[auto] if 0 <= auto < len(parts) else parts[0]
    return [("auto", f"let it choose ({auto_name})")] + [
        (str(i), name + rng) for i, (name, rng) in enumerate(zip(parts, ranges))]


# ---------------------------------------------------------------- arranging


def score_origin(song_dir: Path) -> str:
    """"musicxml" or "midi" for a song read from a score (its bar lines are
    exact, or were inferred from a MIDI file); "" for other songs."""
    try:
        return str(json.loads((Path(song_dir) / "score.json").read_text()).get("origin", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def simplify_line(line: list[QNote], step: int = EIGHTH_BEAT) -> list[QNote]:
    """No notes quicker than 8ths: notes starting between 8ths are left out and
    the note before holds through them (a 16th run keeps every other note,
    dotted 8th + 16th and three triplet 8ths become one quarter)."""
    notes = sorted(line, key=lambda q: q.start)
    kept: list[QNote] = []
    for q in notes:
        if q.start % step == 0:
            kept.append(replace(q))
        elif kept:
            kept[-1].dur = max(kept[-1].dur, q.end - kept[-1].start)
    for a, b in zip(kept, kept[1:]):
        a.dur = min(max(a.dur, step), b.start - a.start)
    if kept:
        kept[-1].dur = max(kept[-1].dur, step)
    return kept


def attach_marks(marks: list[Mark], line: list[QNote]) -> list[Mark]:
    """Marks moved onto the notes of `line` (after notes were left out or
    simplified): single-note marks stay only on a note starting at their tick;
    dynamics and words go to the next note (the last one before, at the end);
    slurs and hairpins start on a note, slurs end on one."""
    starts = sorted({q.start for q in line})
    if not starts:
        return []
    have = set(starts)
    out = []
    for m in marks:
        if m.kind in NOTE_MARKS:
            if m.start in have:
                out.append(m)
            continue
        i = bisect_left(starts, m.start)
        if i > 0 and m.start - starts[i - 1] <= NEAR:
            i -= 1                        # written just after the note's start (after its grace notes)
        nxt = starts[i] if i < len(starts) else None
        if m.kind in PLACED_MARKS:
            out.append(replace(m, start=nxt if nxt is not None else starts[-1]))
        elif m.kind == "slur":
            j = bisect_right(starts, m.end) - 1
            if nxt is not None and j >= 0 and starts[j] > nxt:
                out.append(replace(m, start=nxt, end=starts[j]))
        elif m.kind in ("cresc", "dim"):
            if nxt is not None and nxt < m.end:
                out.append(replace(m, start=nxt))
        else:
            out.append(m)
    return _dedupe(out)


def _move_graces(marks: list[Mark], before: dict[int, int], line: list[QNote]) -> list[Mark]:
    """Grace notes follow their note when it moves (easier key, octaves)."""
    from .config import FLUTE_HARD
    after = {q.start: q.pitch for q in line}
    out = []
    for m in marks:
        if m.kind == "grace" and m.start in after and m.start in before:
            p = int(m.value) + after[m.start] - before[m.start]
            while p < FLUTE_HARD[0]:
                p += 12
            while p > FLUTE_HARD[1]:
                p -= 12
            m = replace(m, value=str(p))
        out.append(m)
    return out


def _bar_shift(src: ScoreSource, barshift: int, bpb: int, beat: int) -> int:
    """Ticks to add to every note for "Bar lines: n beats later". Only for a
    MIDI file, whose bar lines were inferred; a MusicXML score's are exact.
    The pickup stays shorter than a bar (3 beats later = 1 beat earlier)."""
    if src.origin != "midi" or not barshift:
        return 0
    bar = bpb * beat
    moved = -(barshift % bpb) * beat
    earliest = min((q.start for p in src.parts for q in p.melody), default=0)
    while earliest + moved <= -bar:
        moved += bar
    return moved


def score_grid(src: ScoreSource, bpb: int, beat: int, notes: list[QNote]) -> tuple[BeatGrid, callable]:
    """A steady beat grid at the score's tempo, and ticks -> seconds on it
    (time 0 = the start of the bar holding the earliest note)."""
    bar = bpb * beat
    earliest = min((q.start for q in notes), default=0)
    lead = -(earliest // bar) * bar if earliest < 0 else 0
    spb = 60.0 / src.quarter_bpm * beat / T
    last = max((q.end for q in notes), default=bar)
    n = (lead + last) // beat + 3
    grid = BeatGrid([i * spb for i in range(n)], bpb, lead // beat, beat, tracker="score")
    return grid, (lambda t: (t + lead) / beat * spb)


def analysis_for(src: ScoreSource, song_id: str) -> Analysis:
    """The Analysis the pipeline caches for a score (kind "score"); arranging
    reads the ScoreSource itself, the notes here (in seconds) are for anything else."""
    every = [q for p in src.parts for q in p.notes]
    grid, sec = score_grid(src, src.beats_per_bar, src.beat_ticks, every)
    melody = src.parts[src.auto_part].melody if src.parts else []
    to_notes = lambda qs: [Note(q.pitch, sec(q.start), sec(q.end), q.velocity) for q in qs]  # noqa: E731
    duration = sec(max((q.end for q in every), default=0))
    return Analysis(song_id=song_id, kind="score", duration=duration, grid=grid,
                    sources={"score_melody": to_notes(melody), "score_all": to_notes(every)},
                    title=src.title)


def _quick_note_warning(line: list[QNote], bar: int, lead: int) -> str | None:
    """Notes the page can only write approximately (off the 16th / 8th-triplet grid);
    ones shorter than a 16th are left to checks.check_arrangement, which names them."""
    bad = [q for q in line if q.dur >= 3 and (q.start % T % 3 and q.start % T % 4
                                             or q.end % T % 3 and q.end % T % 4)]
    if not bad:
        return None
    bars = sorted({(q.start + lead) // bar + 1 for q in bad})
    shown = ", ".join(str(b) for b in bars[:6]) + (", ..." if len(bars) > 6 else "")
    return (f"{len(bad)} note{'s are' if len(bad) > 1 else ' is'} quicker than 16ths or 8th-note "
            f"triplets (bar{'s' if len(bars) > 1 else ''} {shown}); the page writes "
            f"{'them' if len(bad) > 1 else 'it'} on the nearest 16th, so check against the original.")


def arrange_score(src: ScoreSource, opts: ArrangeOptions
                  ) -> tuple[Arrangement, list[Note], BeatGrid]:
    """The flute part from the chosen part's exact notes (no melody picking,
    beat tracking or quantizing): moved into the flute's range, with the
    score's meter, key, tempo and expression marks (arr.expression). Duet mode
    adds a piano part built from all the other notes of the score.

    Also returns the chosen part's notes in seconds and the steady beat grid
    they sit on, for the expression step (expression.add_expression)."""
    from .checks import check_arrangement, lead_in_ticks
    from .flute import easy_key_shift, fit_to_flute, key_after_shift
    from .rhythm import add_breath_marks

    idx, warnings = choose_part(src, opts.part)
    part = src.parts[idx]
    warnings = list(src.warnings) + list(part.warnings) + warnings
    bpb, beat = _meter_choice(src, opts.meter)
    line = [replace(q, breath_after=False) for q in part.melody]
    marks = list(part.marks)
    moved = _bar_shift(src, opts.barshift, bpb, beat)
    if moved:
        line = [replace(q, start=q.start + moved) for q in line]
        marks = [replace(m, start=m.start + moved, end=(m.end + moved if m.end else 0))
                 for m in marks]
    if opts.simple:
        line = simplify_line(line)
    marks = attach_marks(marks, line)

    fifths, mode = src.key_fifths, src.key_mode
    shift = 0
    before = {q.start: q.pitch for q in line}
    if opts.easy_key:
        shift = easy_key_shift(fifths)
        if shift:
            line = [replace(q, pitch=q.pitch + shift) for q in line]
            fifths = key_after_shift(fifths, shift)
    line, _octaves = fit_to_flute(line, opts.octave, beat)
    marks = _move_graces(marks, before, line)
    line = add_breath_marks(line, bpb, beat)

    rh: list[QNote] = []
    lh: list[QNote] = []
    taken = {(q.pitch, q.start) for q in part.melody}
    pool = [replace(q, start=q.start + moved) for i, p in enumerate(src.parts) for q in p.notes
            if i != idx or (q.pitch, q.start) not in taken]
    melody_moved = [replace(q, start=q.start + moved) for q in part.melody]
    grid, sec = score_grid(src, bpb, beat, pool + melody_moved)
    played = [Note(q.pitch, sec(q.start), sec(q.end), q.velocity) for q in melody_moved]
    if opts.mode == "duet":
        from .piano import accompany
        notes = [Note(q.pitch, sec(q.start), sec(q.end), q.velocity) for q in pool]
        if notes:
            rh, lh = accompany(notes, line, grid, opts.piano_style, shift=shift,
                               key=(src.key_fifths, src.key_mode))
        if not rh and not lh:
            warnings.append("No chords could be found for a piano part, so this is the flute alone.")

    arr = Arrangement(flute=line, rh=rh, lh=lh, beats_per_bar=bpb,
                      bpm=round(src.quarter_bpm * T / beat), key_fifths=fifths, key_mode=mode,
                      shift=shift, warnings=warnings, beat_ticks=beat, expression=marks)
    if not opts.simple:
        w = _quick_note_warning(line, bpb * beat, lead_in_ticks(arr))
        if w:
            arr.warnings.append(w)
    arr.warnings += check_arrangement(arr, opts.simple)
    return arr, played, grid
