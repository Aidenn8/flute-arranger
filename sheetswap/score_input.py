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
- a piano part (also organ, harp, guitar, ...): each staff's top line (at
  each new note the highest one, except a note that starts under a higher note
  still held from before, an inner voice), and one more part, "<piano>, the
  tune", that follows the tune bar by bar through both hands (`find_tune`): into
  the left hand under right-hand chords, onto a separate upper voice over a
  figure in the same hand, silent through an accompaniment-only introduction.
  It is added only where it differs from the right hand's top line.
Ties are joined into one note; a grace note becomes a "grace" mark on the
note it leads into.

Which part is the tune by default ("auto", `auto_part`): the first
single-line part that has a fair share of the notes and isn't a bass part
(violin I in a quartet, the voice in a song, the soprano in a chorale),
otherwise the piano's tune, otherwise its right hand.

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

The tick grid has 48 ticks per quarter: everything down to 32nds, 16th
triplets and 64ths fits exactly (the page writes down to 32nds and 16th
triplets); values that don't fit (quintuplets, 128ths) are rounded to the
nearest tick, with a warning.

MIDI files: only those that look written rather than played are read here
(`load_notated_midi`: a time signature, and nearly all notes starting on a
32nd / 16th-triplet grid). Their ticks come straight from the file. A MIDI
file has no bar lines, and notation programs start it at the pickup, so the
first downbeat is found as the bar phase that puts the most note time on
downbeats while cutting the fewest notes at bar lines, with a prior on the
pickup's length (`_first_downbeat`).

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
from .config import OLD_TICKS_PER_BEAT
from .model import (DOTTED_BEAT, EIGHTH_BEAT, METERS, QUARTER_BEAT, SIXTEENTH, THIRTY_SECOND,
                    TRIPLET_8TH, TRIPLET_16TH, Analysis, Arrangement, ArrangeOptions, BeatGrid, Mark,
                    Note, QNote, normalize_part, time_signature)

SCORE_EXTS = (".musicxml", ".xml", ".mxl")
MIDI_EXTS = (".mid", ".midi")
FORMAT = 2                     # score.json layout (1: ticks were 12 a quarter)

DEFAULT_BPM = {QUARTER_BEAT: 100, DOTTED_BEAT: 72, EIGHTH_BEAT: 144}   # per beat, when the score has no tempo
DYNAMIC_VELOCITY = {"pppp": 25, "ppp": 32, "pp": 42, "p": 55, "mp": 68, "mf": 80,
                    "f": 96, "ff": 108, "fff": 118, "ffff": 124}
LINE_SHARE = 0.25              # auto: a single-line part needs this share of the busiest one's notes
BASS_MEAN = 55                 # auto: a line whose notes average below G3 is a bass part
NOTATED_SHARE = 0.9            # MIDI: share of onsets on the 32nd / 16th-triplet grid
GRID48 = 48                    # MIDI: positions per quarter a written note can start on
GRACE_MAX_Q = 0.12             # MIDI: shorter off-grid notes are grace notes (in quarters)

NOTE_MARKS = {"staccato", "tenuto", "accent", "fermata", "trill", "mordent", "turn", "grace"}
PLACED_MARKS = {"dynamic", "text"}
NEAR = TRIPLET_16TH            # ticks: a dynamic or word this soon after a note's start belongs to it

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
    tune: bool = False        # follows the tune through a keyboard part's staves (find_tune)

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
        if d.get("format") not in (1, FORMAT):
            raise ValueError("score.json from another version")
        k = T // OLD_TICKS_PER_BEAT if d.get("format") == 1 else 1    # format 1: 12 ticks a quarter
        d = dict(d)
        d.pop("format", None)
        parts = []
        for p in d.pop("parts"):
            p = dict(p)
            p["melody"] = [QNote(int(a), k * int(b), k * int(c), int(v)) for a, b, c, v in p["melody"]]
            p["notes"] = [QNote(int(a), k * int(b), k * int(c)) for a, b, c in p["notes"]]
            p["marks"] = [Mark(str(m), k * int(s), k * int(e), str(v)) for m, s, e, v in p["marks"]]
            parts.append(ScorePart(**p))
        d["beat_ticks"] = k * int(d.get("beat_ticks", QUARTER_BEAT // k))
        d["pickup"] = k * int(d.get("pickup", 0))
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


# ---------------------------------------------------------------- the tune of a keyboard part
#
# A piano's tune is not always the right hand's top line: it moves to the left hand
# under right-hand chords (Schumann's "Happy Farmer", Chopin's B minor prelude), it
# is a separate upper voice over a figure in the same hand (the Moonlight sonata's
# triplets), and an introduction may have no tune at all. find_tune follows it bar by
# bar through each staff's top line, scoring how much each looks like a tune:
# - register: the tune is usually the highest line (one point an octave above C4);
# - steps: a tune moves mostly by step (half a point for a line that only steps);
# - accompaniment (minus 2.5 points for a bar of it): figures that repeat beat after
#   beat (Alberti bass, broken-chord patterns), block chords struck again within two
#   bars (after-beat chords, a chord pattern), the same note pulsed in 8ths, and an
#   even run of short notes under a separate, longer upper voice (a figure).
# Broken-chord notes that are the tune stay: a line is taken or left a whole bar at a
# time, so Für Elise's A-C-E-A answer keeps its chord notes. Moving the tune to another
# staff costs half a point, so it moves only for a clear reason; a bar where every line
# scores below -1.5 has no tune (the flute rests). When the tune moves, the new line's
# upbeat (notes after a rest in the last beat before the move) comes with it.
# Measured with bench/piano_tune.py on 12 piano pieces with hand-checked tunes.

TUNE_REGISTER = 1.0     # per octave above C4, the line's mean (clipped to +-2 octaves)
TUNE_STEP = 0.5         # times the share of steps (1-2 semitones) among the line's moves
TUNE_ACCOMP = 2.5       # times the share of the bar's notes that look like accompaniment
TUNE_REST = -1.5        # what "no tune in this bar" scores
TUNE_SWITCH = 0.5       # moving the tune to another line
TUNE_HOLD = 0.2         # bonus for a line still holding a note through a bar it doesn't strike in
FIGURE_BARS = 2         # a figure: an even run of short notes at least this many bars long
FIGURE_MAX = T // 2     # ... of notes no longer than an 8th
FIGURE_UNDER = 0.3      # ... with this share of its notes sounding under a longer, higher note
ALTERNATION = 8         # this many notes swinging between two pitches: a measured trill figure
FIGURE_WEIGHT = 2.0     # a figure's note counts as this much accompaniment


def _skyline(notes: list[QNote]) -> list[QNote]:
    """top_line for QNotes: the highest line, each note cut at the next one's start."""
    evs = [_Ev(q.start, q.end, q.pitch, q.velocity) for q in notes if q.dur > 0]
    return [QNote(e.pitch, e.start, e.end - e.start, e.velocity) for e in top_line(evs)]


def _chains(notes: list[QNote]) -> dict[int, list[list[int]]]:
    """Even runs of equally short notes (each onset where the last one ends, or one
    note later), by length: {duration: [[indices of `notes`], ...]}."""
    by_dur: dict[int, list[int]] = {}
    for i, q in enumerate(notes):
        if 0 < q.dur <= FIGURE_MAX:
            by_dur.setdefault(q.dur, []).append(i)
    out: dict[int, list[list[int]]] = {}
    for d, idx in by_dur.items():
        idx.sort(key=lambda i: notes[i].start)
        chains: list[list[int]] = []
        for i in idx:
            last = notes[chains[-1][-1]].start if chains else None
            # the next note of the run, at the same time (a chord) or one note later; a run
            # skipping one note goes on (a note of it struck by the tune's own key)
            if chains and notes[i].start in (last, last + d, last + 2 * d):
                chains[-1].append(i)
            else:
                chains.append([i])
        out[d] = chains
    return out


def _figure_notes(staves: list[list[QNote]], bar: int) -> list[set[int]]:
    """For each staff, the indices of its notes that form an accompaniment figure
    under a separate tune: an even run of equally short notes at least FIGURE_BARS
    bars long with at least two longer notes sounding above it in the same staff (the
    Moonlight sonata's triplets under its tune). Then every run of that note length a
    bar or longer, anywhere in the piece, is the same figure (where the tune rests),
    and so is every run of three or more within a bar of it (the figure passes between
    the hands). A run with nothing above it (Für Elise's 16ths, a scale) is not a figure."""
    chains = [_chains(notes) for notes in staves]
    spans: dict[int, list[tuple[int, int]]] = {}        # note length -> where a figure of it runs
    for notes, by_dur in zip(staves, chains):
        order = sorted(range(len(notes)), key=lambda j: notes[j].start)
        starts_of = [notes[j].start for j in order]
        for d, runs in by_dur.items():
            for ch in runs:
                a, b = notes[ch[0]].start, notes[ch[-1]].end
                if b - a < FIGURE_BARS * bar:
                    continue
                tops: dict[int, int] = {}
                for i in ch:
                    tops[notes[i].start] = max(tops.get(notes[i].start, 0), notes[i].pitch)
                times = sorted(tops)
                members = set(ch)
                lo_i = bisect_left(starts_of, a)
                hi_i = bisect_left(starts_of, b)
                upper = []
                for j in order[lo_i:hi_i]:
                    q = notes[j]
                    if j in members or q.dur < 2 * d:
                        continue
                    k = max(0, bisect_right(times, q.start) - 1)      # the run's note sounding then
                    if q.pitch > tops[times[k]]:
                        upper.append(q)
                if len(upper) < 2:
                    continue
                under = 0
                for u in upper:                                     # run notes under a tune note
                    under += bisect_left(times, u.end) - bisect_left(times, u.start)
                if under >= FIGURE_UNDER * len(tops):
                    spans.setdefault(d, []).append((a - bar, b + bar))
    out = []
    for notes, by_dur in zip(staves, chains):
        mine: set[int] = set()
        for d, near in spans.items():
            for ch in by_dur.get(d, ()):
                a, b = notes[ch[0]].start, notes[ch[-1]].end
                if b - a >= bar or (len(ch) >= 3 and any(a < y and b > x for x, y in near)):
                    mine |= set(ch)
        out.append(mine)
    return out


def _swings(line: list[QNote]) -> set[tuple[int, int]]:
    """(pitch, start) of the notes of `line` inside a run of ALTERNATION or more notes
    swinging between two pitches (a measured trill or tremolo figure)."""
    out: set[tuple[int, int]] = set()
    i = 0
    while i < len(line):
        j = i + 2
        while j < len(line) and line[j].pitch == line[j - 2].pitch != line[j - 1].pitch:
            j += 1
        if j - i >= ALTERNATION:
            out |= {(q.pitch, q.start) for q in line[i:j]}
            i = j
        else:
            i += 1
    return out


def _accomp_flags(line: list[QNote], notes: list[QNote], figure: set[tuple[int, int]],
                  bar: int, beat: int, swing: set[tuple[int, int]] = frozenset()) -> list[float]:
    """For each note of a staff's top line: how much it looks like accompaniment, 0
    (not at all), 1, or FIGURE_WEIGHT for a note of a figure (_figure_notes)."""
    groups: dict[int, list[QNote]] = {}
    for q in notes:
        groups.setdefault(q.start, []).append(q)
    chord_at: dict[int, frozenset] = {}           # onset -> pitches of the block chord struck there
    for t, qs in groups.items():
        top = max(qs, key=lambda q: q.pitch)
        same = [q.pitch for q in qs if abs(q.dur - top.dur) <= T // OLD_TICKS_PER_BEAT]
        if len(same) >= 2:
            chord_at[t] = frozenset(same)
    onsets = sorted(groups)
    sig: dict[int, tuple] = {}                    # beat index -> the line's notes in it
    for q in line:
        k = q.start // beat
        sig[k] = sig.get(k, ()) + ((q.start - k * beat, q.pitch),)
    flags = []
    for i, q in enumerate(line):
        prev = line[i - 1] if i else None
        k = q.start // beat
        if (q.pitch, q.start) in figure:
            flags.append(FIGURE_WEIGHT)
            continue
        flag = (q.pitch, q.start) in swing
        c = chord_at.get(q.start)
        if not flag and c is not None:
            k_on = bisect_left(onsets, q.start)          # the same chord struck again next to it
            again = any(0 <= j < len(onsets) and abs(onsets[j] - q.start) <= beat
                        and chord_at.get(onsets[j]) == c for j in (k_on - 1, k_on + 1))
            octaves = len({x % 12 for x in c}) == 1 and q.pitch < 60       # a bass in octaves
            struck = prev is not None and prev.pitch == q.pitch and prev.start in chord_at
            flag = again or octaves or struck
        if not flag and q.dur <= beat // 2:            # a note pulsed in short values
            nxt = line[i + 1] if i + 1 < len(line) else None
            flag = any(o is not None and o.pitch == q.pitch and abs(o.start - q.start) <= beat
                       and o.dur <= beat // 2 for o in (prev, nxt))
        if not flag and len(sig[k]) >= 2:          # a figure repeated beat after beat (3 times or more)
            flag = any(all(sig.get(k + p * (j + m)) == sig[k] for m in range(3))
                       for p in (1, 2) for j in (-2, -1, 0))
        flags.append(1.0 if flag else 0.0)
    return flags


def _bar_score(line: list[QNote], flags: list[float], lo: int, hi: int, b: int, bar: int) -> float:
    """How much the notes line[lo:hi] (those starting in bar b) sound like the tune."""
    if lo == hi:
        held = lo > 0 and line[lo - 1].end > b * bar
        return TUNE_REST + (TUNE_HOLD if held else 0.0)
    seg = line[lo:hi]
    weight = sum(max(1, q.dur) for q in seg)
    mean = sum(q.pitch * max(1, q.dur) for q in seg) / weight
    reg = max(-2.0, min(2.0, (mean - 60) / 12))
    run = ([line[lo - 1]] if lo and seg[0].start - line[lo - 1].end < bar else []) + seg
    moves = [abs(b2.pitch - a.pitch) for a, b2 in zip(run, run[1:]) if b2.pitch != a.pitch]
    step = sum(m <= 2 for m in moves) / len(moves) if moves else 0.0
    acc = sum(flags[lo:hi]) / len(seg)
    return TUNE_REGISTER * reg + TUNE_STEP * step - TUNE_ACCOMP * acc


def find_tune(staves: list[list[QNote]], bar: int, beat: int
              ) -> tuple[list[QNote], dict[int, int]]:
    """The tune of a keyboard part (see the notes above): `staves` are every note of
    each staff, the top staff first. Returns the tune's notes (one line, each cut at
    the next one's start) and, for every bar index (tick // bar), the staff the tune's
    notes there come from (-1: none start in that bar)."""
    lines: list[tuple[int, list[QNote], list[float]]] = []
    figures = _figure_notes(staves, bar)
    # trills and tremolos are found on all the staves' notes together: one passed
    # between the hands (Für Elise's E-D# before the reprise) belongs to both
    swing = _swings(_skyline([q for notes in staves for q in notes]))
    for s, notes in enumerate(staves):
        fig_idx = figures[s]
        figure = {(notes[i].pitch, notes[i].start) for i in fig_idx}
        sky = _skyline(notes)
        lines.append((s, sky, _accomp_flags(sky, notes, figure, bar, beat, swing)))
        if fig_idx:
            rest = [q for i, q in enumerate(notes) if i not in fig_idx]
            upper = _skyline(rest)
            lines.append((s, upper, _accomp_flags(upper, rest, set(), bar, beat, swing)))
    lines = [ln for ln in lines if ln[1]]
    if not lines:
        return [], {}
    first = min(ln[1][0].start for ln in lines) // bar
    last = max(ln[1][-1].start for ln in lines) // bar
    bars = list(range(first, last + 1))
    # per line: index range of the notes starting in each bar
    spans = []
    for _, ln, _ in lines:
        starts = [q.start for q in ln]
        spans.append({b: (bisect_left(starts, b * bar), bisect_left(starts, (b + 1) * bar)) for b in bars})
    n = len(lines)
    rest = n                                       # the "no tune" state
    emit = [[_bar_score(lines[k][1], lines[k][2], *spans[k][b], b, bar) for k in range(n)] + [TUNE_REST]
            for b in bars]

    def cost(i: int, j: int) -> float:
        if i == j:
            return 0.0
        if i < n and j < n and lines[i][0] == lines[j][0]:
            return TUNE_SWITCH * 0.2               # the same staff, with or without its figure
        return TUNE_SWITCH

    score = list(emit[0])
    back: list[list[int]] = []
    for row in emit[1:]:
        prev = score
        ptr, score = [], []
        for j in range(n + 1):
            i = max(range(n + 1), key=lambda i: (prev[i] - cost(i, j), i == j))
            ptr.append(i)
            score.append(prev[i] - cost(i, j) + row[j])
        back.append(ptr)
    state = max(range(n + 1), key=lambda j: score[j])
    path = [state]
    for ptr in reversed(back):
        state = ptr[state]
        path.append(state)
    path.reverse()

    picked: list[tuple[QNote, int]] = []            # (note, line index)
    for b, k in zip(bars, path):
        if k != rest:
            lo, hi = spans[k][b]
            picked += [(q, k) for q in lines[k][1][lo:hi]]
    for b, a, c in zip(bars[1:], path, path[1:]):
        if c == a or c == rest:
            continue
        picked = _upbeat(picked, lines, a if a != rest else None, c, b * bar, beat)
    picked.sort(key=lambda x: (x[0].start, -x[0].pitch))
    out: list[QNote] = []
    for q, _ in picked:
        if out and out[-1].start == q.start:
            continue
        out.append(replace(q))
    for x, y in zip(out, out[1:]):
        x.dur = min(x.dur, y.start - x.start)
    where = {b: (lines[k][0] if k != rest and spans[k][b][0] < spans[k][b][1] else -1)
             for b, k in zip(bars, path)}
    return [q for q in out if q.dur > 0], where


def _upbeat(picked, lines, a, c, at: int, beat: int):
    """The tune moves from line a (None: from silence) to line c at tick `at`: c's
    upbeat in the beat before (unflagged notes after a rest or after accompaniment in
    c) replaces a's notes from there, unless a strikes a note at that same moment."""
    _, ln, flags = lines[c]
    idx = [i for i, q in enumerate(ln) if at - beat <= q.start < at]
    a_starts = {q.start for q, k in picked if k == a and at - beat <= q.start < at}
    for pos, i in enumerate(idx):
        q = ln[i]
        after_rest = i == 0 or ln[i - 1].end < q.start or flags[i - 1]
        if not after_rest or any(flags[j] for j in idx[pos:]) or q.start in a_starts:
            continue
        keep = [(p, k) for p, k in picked if not (k == a and p.start >= q.start and p.start < at)]
        return keep + [(ln[j], c) for j in idx[pos:]]
    return picked


def _tune_part(name: str, instrument: str, staves: list["ScorePart"], bar: int,
               beat: int) -> "ScorePart | None":
    """A part following the tune through a keyboard part's staves (top staff first),
    with each staff's marks where the tune is taken from it; None when the tune is
    simply the top staff's own line."""
    line, where = find_tune([p.notes for p in staves], bar, beat)
    if not line or [(q.pitch, q.start) for q in line] == [(q.pitch, q.start) for q in staves[0].melody]:
        return None
    marks: list[Mark] = []
    for s, p in enumerate(staves):
        for m in p.marks:
            if m.kind in NOTE_MARKS or m.kind == "slur":
                if where.get(m.start // bar) == s:
                    marks.append(m)
            elif s == 0:                  # dynamics, hairpins and words: the top staff has them all
                marks.append(m)
    marks = attach_marks(_dedupe(marks), line)
    _apply_dynamics(line, marks)
    return ScorePart(f"{name}, the tune", instrument, 0, True, line, [], marks, [], tune=True)


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
    a value fell off the tick grid (TICKS_PER_BEAT a quarter)."""

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
    tunes: list[ScorePart] = []            # keyboard parts' tunes, listed after every staff
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
        first_part = len(parts)
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
        mine = parts[first_part:]
        if keyboard and len(g) > 1 and len(mine) >= 2:
            tune = _tune_part(base, inst, mine, bar, beat)
            if tune:
                tunes.append(tune)
    parts += tunes
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
    return (f"Some notes don't fit this app's finest grid (quintuplets, 128ths or similar, "
            f"bar{'s' if len(bars) > 1 else ''} {shown} of the original): they were rounded, "
            "so check those bars against the original.")


# ---------------------------------------------------------------- MIDI written by a notation program


def _on_grid(q: Fraction, tol: Fraction = Fraction(1, 2400)) -> bool:
    """Does `q` (quarters) sit on the 48-a-quarter grid, within `tol` quarters?"""
    x = q * GRID48
    return abs(x - round(x)) <= tol * GRID48


END_TOL = Fraction(T, 240)     # MIDI: an end this close (ticks) to a tick is on it


def _snap_end(x: Fraction, tol: Fraction = END_TOL) -> int:
    """An end in ticks: exact when it's on the tick grid (within `tol`), else the
    nearest 16th or 8th-triplet point (notation programs shorten notes a little)."""
    r = round(x)
    if abs(x - r) <= tol:
        return r
    c3 = SIXTEENTH * round(x / SIXTEENTH)
    c4 = TRIPLET_8TH * round(x / TRIPLET_8TH)
    return c3 if abs(c3 - x) <= abs(c4 - x) else c4


def _pickup_prior(p: int, bar: int, beat: int) -> float:
    """How often a piece starts with a pickup of p ticks (0 = none), roughly as in the
    bench's scores: none or one beat most often, half a bar (a gavotte), half or a third
    of a beat sometimes, anything else rarely."""
    if p == 0:
        return 0.40
    if p == beat:
        return 0.30
    if 2 * p == bar:
        return 0.10
    if p % beat == 0:
        return 0.06
    if beat in (2 * p, 3 * p, 4 * p):
        return 0.05
    return 0.01


def _first_downbeat(notes: list[tuple[int, int]], bar: int, beat: int, bpb: int) -> int:
    """Where bar 1 starts in a MIDI file (ticks after its start). Each bar phase on the
    16th / 8th-triplet grid is scored by (bench/midi_downbeat.py):
    - note time starting on downbeats (a note's length up to a bar; half that on the
      middle beat of an even bar, a tenth on other beats; R-METER-2/3);
    - minus twice the note time crossing a bar line (written music ties few notes
      over bar lines, R-METER-1, so the right bar lines cut few notes);
    - plus a prior on the pickup it implies (_pickup_prior; R-PAGE-5).
    A piece starting just after a bar line starts with a rest, not a long pickup."""
    if bar <= 0 or not notes:
        return 0
    import math
    first = min(s for s, _ in notes)
    total = sum(min(d, bar) for _, d in notes) or 1
    best, best_v = 0, -math.inf
    for o in range(bar):
        if o % SIXTEENTH and o % TRIPLET_8TH:
            continue
        on = cross = 0.0
        for start, dur in notes:
            pos = (start - o) % bar
            w = min(dur, bar)
            if pos == 0:
                on += w
            elif bpb % 2 == 0 and 2 * pos == bar:
                on += 0.5 * w
            elif pos % beat == 0:
                on += 0.1 * w
            if pos and pos + dur > bar:
                cross += w
        d0 = o + bar * -(-(first - o) // bar)
        if 4 * (d0 - first) > 3 * bar:
            d0 -= bar
        v = (on - 2.0 * cross) / total + 0.05 * math.log(_pickup_prior((d0 - first) % bar, bar, beat))
        if v > best_v:
            best, best_v = d0, v
    return best


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

    # notes in ticks from the file's start; graces apart
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
                    up = min(-(-x0 // SIXTEENTH) * SIXTEENTH, -(-x0 // TRIPLET_8TH) * TRIPLET_8TH)
                    if up - x0 < Fraction(T, 5):
                        x0 = Fraction(up)
            start = round(x0)
            if abs(x0 - start) > tol * T:
                inexact.append(start)
            end = _snap_end(e * T, max(END_TOL, tol * T))
            if end <= start:
                end = start + max(1, round(dur_q * T))
            evs.append(_Ev(start, end, int(n.pitch), int(n.velocity), refs=[k]))
        per_inst.append((evs, graces, inexact))

    if pickup_at is not None:
        d0 = int(pickup_at * T)
    else:
        allev = [(e.start, e.end - e.start) for evs, _, _ in per_inst for e in evs]
        d0 = _first_downbeat(allev, bar, beat, bpb)

    parts: list[ScorePart] = []
    programs: list[int] = []
    for idx, (inst, (evs, graces, inexact)) in enumerate(zip(insts, per_inst)):
        for e in evs + graces:
            e.start -= d0
            e.end -= d0
        line = top_line(evs)
        # close the small gap a grace note took from the note before it
        gstarts = sorted(g.start for g in graces)
        for a, b in zip(line, line[1:]):
            gap = b.start - a.end
            if 0 < gap < SIXTEENTH and bisect_left(gstarts, a.end) < bisect_left(gstarts, b.start):
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
            programs.append(inst.program)
    if not parts:
        raise ValueError("This MIDI file has no notes.")
    # a keyboard instrument on two or more tracks (one per staff): its tune (find_tune)
    for prog in dict.fromkeys(programs):
        staves = [p for p, pr in zip(parts, programs) if pr == prog and p.keyboard]
        if len(staves) >= 2:
            staves.sort(key=lambda p: -sum(q.pitch for q in p.notes) / len(p.notes))
            tune = _tune_part(staves[0].instrument, staves[0].instrument, staves, bar, beat)
            if tune:
                parts.append(tune)
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
    tunes = [i for i in keys if parts[i].tune]
    if tunes:
        return tunes[0]
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
    """Notes the page can only write approximately (off the 32nd / 16th-triplet grid:
    64ths, 32nd triplets); ones shorter than a 32nd are left to checks.check_arrangement,
    which names them."""
    def off(t: int) -> bool:
        return bool(t % T % THIRTY_SECOND and t % T % TRIPLET_16TH)

    bad = [q for q in line if q.dur >= THIRTY_SECOND and (off(q.start) or off(q.end))]
    if not bad:
        return None
    bars = sorted({(q.start + lead) // bar + 1 for q in bad})
    shown = ", ".join(str(b) for b in bars[:6]) + (", ..." if len(bars) > 6 else "")
    return (f"{len(bad)} note{'s are' if len(bad) > 1 else ' is'} quicker than 32nds or 16th-note "
            f"triplets (bar{'s' if len(bars) > 1 else ''} {shown}); the page writes "
            f"{'them' if len(bad) > 1 else 'it'} on the nearest 32nd, so check against the original.")


BREATH_PHRASE_BARS = 2         # a slur this long or longer is a phrase, and so is the music since the last breath


def _phrase_breaths(line: list[QNote], marks: list[Mark], bar: int, add_breath_marks, bpb: int,
                    beat: int) -> list[QNote]:
    """Breath marks where the score's own phrasing ends: after the last note of a
    phrase slur (one at least a bar long) that the next note follows without a rest,
    when the music since the last rest or breath runs at least BREATH_PHRASE_BARS bars
    (R-BREATH-1, R-BREATH-3, R-PHR-4); short slurs over a figure or a sung melisma are
    not phrases. Then rhythm.add_breath_marks finds places in what is still too long,
    stretch by stretch between these breaths."""
    out = [replace(q) for q in sorted(line, key=lambda q: q.start)]
    ends = {m.end for m in marks if m.kind == "slur" and m.end - m.start >= bar}
    last = out[0].start if out else 0
    for i, q in enumerate(out[:-1]):
        nxt = out[i + 1]
        if nxt.start - q.end >= EIGHTH_BEAT:
            last = nxt.start                       # a rest: the player breathes there anyway
            continue
        if q.start in ends and q.end - last >= BREATH_PHRASE_BARS * bar:
            q.breath_after = True
            last = nxt.start
    pieces, cur = [], []
    for q in out:
        cur.append(q)
        if q.breath_after:
            pieces.append(cur)
            cur = []
    if cur:
        pieces.append(cur)
    merged = []
    for piece in pieces:
        done = add_breath_marks(piece, bpb, beat)
        if piece[-1].breath_after:
            done[-1].breath_after = True
        merged += done
    return merged


def _phrase_cuts(src: ScoreSource, part: ScorePart, line: list[QNote], marks: list[Mark]) -> set[int]:
    """Where a new phrase starts inside the line (for flute.phrase_octaves): the note
    after each slur's last note (R-PHR-4), and where a piano's tune passes from one
    hand to the other."""
    starts = sorted(q.start for q in line)
    cuts = set()
    for m in marks:
        if m.kind == "slur":
            i = bisect_right(starts, m.end)
            if i < len(starts):
                cuts.add(starts[i])
    if part.tune:
        hand = {}
        for k, p in enumerate(src.parts):
            if p.keyboard and not p.tune:
                for q in p.notes:
                    hand.setdefault((q.pitch, q.start), k)
        prev = None
        for q in sorted(part.melody, key=lambda q: q.start):
            h = hand.get((q.pitch, q.start))
            if h is not None and prev is not None and h != prev:
                cuts.add(q.start)
            prev = h if h is not None else prev
    return cuts


def arrange_score(src: ScoreSource, opts: ArrangeOptions
                  ) -> tuple[Arrangement, list[Note], BeatGrid]:
    """The flute part from the chosen part's exact notes (no melody picking,
    beat tracking or quantizing): moved into the flute's range, with the
    score's meter, key, tempo and expression marks (arr.expression). Duet mode
    adds a piano part built from all the other notes of the score (for a piano's
    tune part, without the tune's notes in either hand).

    Then polished for the flute (flute.phrase_octaves, flute.outline_leaps): phrases
    sitting in the weak low register, or marked f below G4, move up an octave whole
    (R-FLUTE-1/2; phrases end at rests, at the score's slur ends and where the tune
    changes hands), fast leaps over an octave become a playable outline (R-FLUTE-5),
    and breath marks go where the score's slurs end a phrase (R-BREATH-1/3).

    Also returns the chosen part's notes in seconds and the steady beat grid
    they sit on, for the expression step (expression.add_expression)."""
    from .checks import check_arrangement, lead_in_ticks
    from .flute import easy_key_shift, fit_to_flute, key_after_shift, outline_leaps, phrase_octaves
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
    if not opts.octave:                    # the player's own octave choice is left as chosen
        line, _moved = phrase_octaves(line, [(m.start, m.value) for m in marks if m.kind == "dynamic"],
                                      beat, bpb * beat, _phrase_cuts(src, part, line, marks))
    tick_s = 60.0 / src.quarter_bpm / T
    line, _outlined = outline_leaps(line, max(SIXTEENTH, min(EIGHTH_BEAT, int(0.25 / tick_s))))
    marks = _move_graces(marks, before, line)
    line = _phrase_breaths(line, marks, bpb * beat, add_breath_marks, bpb, beat)

    rh: list[QNote] = []
    lh: list[QNote] = []
    taken = {(q.pitch, q.start) for q in part.melody}
    pool = [replace(q, start=q.start + moved) for i, p in enumerate(src.parts) for q in p.notes
            if not ((i == idx or (part.tune and p.keyboard)) and (q.pitch, q.start) in taken)]
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
    arr.warnings += check_arrangement(arr, opts.simple, shortest=THIRTY_SECOND)
    return arr, played, grid
