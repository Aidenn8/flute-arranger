"""Plain data types passed between the steps.

Times come in two flavours:
- Note: seconds, straight from the audio.
- QNote: ticks on the beat grid (TICKS_PER_BEAT = 48 per quarter note,
  whatever the beat), where tick 0 is the first downbeat. Negative ticks are a
  pickup. 48 holds every value written music uses down to 32nds and 16th
  triplets (and 32nd triplets).

The beat itself is a quarter (48 ticks: 2/4, 3/4, 4/4), a dotted quarter
(72 ticks: 6/8, 9/8, 12/8, or one beat per bar of 3/8) or an eighth (24 ticks:
3/8 counted in three).
"""
from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field, asdict
from pathlib import Path
from statistics import median
from typing import Mapping

from .config import OLD_TICKS_PER_BEAT, TICKS_PER_BEAT

QUARTER_BEAT = TICKS_PER_BEAT              # 48
DOTTED_BEAT = TICKS_PER_BEAT * 3 // 2      # 72
EIGHTH_BEAT = TICKS_PER_BEAT // 2          # 24
# Note values inside a beat, in ticks
SIXTEENTH = TICKS_PER_BEAT // 4            # 12
THIRTY_SECOND = TICKS_PER_BEAT // 8        # 6
TRIPLET_8TH = TICKS_PER_BEAT // 3          # 16
TRIPLET_16TH = TICKS_PER_BEAT // 6         # 8

# Time choices on the page: "auto" (as heard) or a time signature, as
# (beats per bar, ticks per beat).
METERS = {
    "2/4": (2, QUARTER_BEAT), "3/4": (3, QUARTER_BEAT), "4/4": (4, QUARTER_BEAT),
    "3/8": (3, EIGHTH_BEAT),
    "6/8": (2, DOTTED_BEAT), "9/8": (3, DOTTED_BEAT), "12/8": (4, DOTTED_BEAT),
}
_OLD_METERS = {0: "auto", 2: "2/4", 3: "3/4", 4: "4/4"}   # before 6/8 etc. existed


def time_signature(beats_per_bar: int, beat_ticks: int) -> tuple[int, int]:
    """(numerator, denominator): dotted beats count three 8ths each (2 -> 6/8,
    1 -> 3/8), eighth beats count 8ths (3 -> 3/8), quarter beats quarters."""
    if beat_ticks == DOTTED_BEAT:
        return beats_per_bar * 3, 8
    if beat_ticks == EIGHTH_BEAT:
        return beats_per_bar, 8
    return beats_per_bar, 4


def normalize_meter(value) -> str:
    """A meter choice as "auto" or one of METERS' keys; anything else -> "auto".

    The old numeric choices still work: 0 = as heard, 2/3/4 = beats per bar."""
    if isinstance(value, str):
        v = value.strip()
        if v == "auto" or v in METERS:
            return v
        try:
            value = int(v)
        except ValueError:
            return "auto"
    if isinstance(value, int) and not isinstance(value, bool):
        return _OLD_METERS.get(value, "auto")
    return "auto"


def normalize_part(value) -> str:
    """A part choice for sheet music: "auto", a part number ("0", "1", ...) or a
    part's name, trimmed to 60 characters; empty or unusable -> "auto"."""
    if isinstance(value, bool) or value is None:
        return "auto"
    v = " ".join(str(value).split())[:60]
    return v if v and v.lower() != "auto" else "auto"


def _slug(text: str) -> str:
    """Text safe for a folder name (part choices go into ArrangeOptions.key())."""
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")[:40] or "x"


@dataclass
class Note:
    pitch: int
    start: float
    end: float
    velocity: int = 80

    @property
    def dur(self) -> float:
        return self.end - self.start


@dataclass
class QNote:
    pitch: int
    start: int
    dur: int
    velocity: int = 80
    breath_after: bool = False

    @property
    def end(self) -> int:
        return self.start + self.dur


@dataclass
class BeatGrid:
    beats: list[float]
    beats_per_bar: int = 4
    first_downbeat: int = 0
    beat_ticks: int = QUARTER_BEAT      # 48 quarter, 72 dotted quarter, 24 eighth
    tracker: str = "librosa"            # what found the beats: "neural" (Beat This!), "librosa", "midi"

    @property
    def bar_ticks(self) -> int:
        return self.beats_per_bar * self.beat_ticks

    def _beats(self) -> list[float]:
        if len(self.beats) >= 2:
            return self.beats
        start = self.beats[0] if self.beats else 0.0
        return [start, start + 0.5]

    def to_beats(self, t: float) -> float:
        """Seconds -> beats counted from the first downbeat (piecewise linear)."""
        b = self._beats()
        if t <= b[0]:
            idx = (t - b[0]) / (b[1] - b[0])
        elif t >= b[-1]:
            idx = len(b) - 1 + (t - b[-1]) / (b[-1] - b[-2])
        else:
            i = bisect_right(b, t) - 1
            idx = i + (t - b[i]) / (b[i + 1] - b[i])
        return idx - self.first_downbeat

    def to_ticks(self, t: float) -> float:
        return self.to_beats(t) * self.beat_ticks

    def to_seconds(self, beat: float) -> float:
        """Inverse of to_beats."""
        b = self._beats()
        idx = beat + self.first_downbeat
        if idx <= 0:
            return b[0] + idx * (b[1] - b[0])
        if idx >= len(b) - 1:
            return b[-1] + (idx - (len(b) - 1)) * (b[-1] - b[-2])
        i = int(idx)
        return b[i] + (idx - i) * (b[i + 1] - b[i])

    def bpm(self) -> float:
        b = self._beats()
        gaps = [b2 - b1 for b1, b2 in zip(b, b[1:]) if b2 > b1]
        return 60.0 / median(gaps) if gaps else 120.0


def _notes_to_json(notes: list[Note]) -> list[list]:
    return [[n.pitch, round(n.start, 4), round(n.end, 4), n.velocity] for n in notes]


def _notes_from_json(rows: list[list]) -> list[Note]:
    return [Note(int(p), float(s), float(e), int(v)) for p, s, e, v in rows]


@dataclass
class Analysis:
    song_id: str
    kind: str                     # piano | song | band | midi
    duration: float
    grid: BeatGrid
    sources: dict[str, list[Note]] = field(default_factory=dict)
    title: str = ""

    def save(self, path: Path) -> None:
        data = {
            "song_id": self.song_id,
            "kind": self.kind,
            "duration": self.duration,
            "title": self.title,
            "grid": asdict(self.grid),
            "ticks_per_beat": TICKS_PER_BEAT,
            "sources": {k: _notes_to_json(v) for k, v in self.sources.items()},
        }
        Path(path).write_text(json.dumps(data))

    @classmethod
    def load(cls, path: Path) -> "Analysis":
        d = json.loads(Path(path).read_text())
        grid = BeatGrid(**d["grid"])
        # the grid's beat length is in ticks: files from before the finer grid used 12 a quarter
        written = int(d.get("ticks_per_beat", OLD_TICKS_PER_BEAT))
        if written != TICKS_PER_BEAT and "beat_ticks" in d["grid"]:
            grid.beat_ticks = grid.beat_ticks * TICKS_PER_BEAT // written
        return cls(
            song_id=d["song_id"],
            kind=d["kind"],
            duration=d["duration"],
            title=d.get("title", ""),
            grid=grid,
            sources={k: _notes_from_json(v) for k, v in d["sources"].items()},
        )


_CHOICES = {
    "mode": ("solo", "duet"),
    "melody_source": ("auto", "vocals", "piano", "other"),
    "piano_style": ("chords", "cleaned"),
    "expression": ("plain", "expressive", "ornaments"),
}


@dataclass
class ArrangeOptions:
    mode: str = "solo"
    melody_source: str = "auto"
    octave: int = 0               # -1, 0, +1 relative to the automatic choice
    simple: bool = False
    easy_key: bool = False
    piano_style: str = "chords"
    meter: str = "auto"           # "auto" (as heard) or a key of METERS, e.g. "6/8"
    barshift: int = 0             # move the bar lines this many beats later (0-3)
    tempo: int = 0                # -1 = half as fast (beats twice as long), +1 = twice as fast
    expression: str = "expressive"  # "plain" (notes only), "expressive" (dynamics, slurs, ...), "ornaments"
    part: str = "auto"           # sheet music: which part/staff is the tune ("auto", an index or a name)

    def __post_init__(self) -> None:
        self.meter = normalize_meter(self.meter)
        self.part = normalize_part(self.part)

    def key(self) -> str:
        return (f"{self.mode}-{self.melody_source}-o{self.octave:+d}-"
                f"{'simple' if self.simple else 'full'}-"
                f"{'easy' if self.easy_key else 'orig'}-{self.piano_style}"
                f"-m{self.meter.replace('/', '-')}"
                f"-b{self.barshift}-t{self.tempo:+d}-x{self.expression}"
                + (f"-p{_slug(self.part)}" if self.part != "auto" else ""))

    @classmethod
    def from_form(cls, d: Mapping) -> "ArrangeOptions":
        opts = cls()
        for name, allowed in _CHOICES.items():
            v = d.get(name)
            if v in allowed:
                setattr(opts, name, v)
        try:
            opts.octave = max(-1, min(1, int(d.get("octave", 0))))
        except (TypeError, ValueError):
            opts.octave = 0
        opts.meter = normalize_meter(d.get("meter", "auto"))
        try:
            b = int(d.get("barshift", 0))
            opts.barshift = b if 0 <= b <= 3 else 0
        except (TypeError, ValueError):
            opts.barshift = 0
        try:
            opts.tempo = max(-1, min(1, int(d.get("tempo", 0))))
        except (TypeError, ValueError):
            opts.tempo = 0
        truthy = ("1", "on", "true", "yes", True)
        opts.simple = d.get("simple") in truthy
        opts.easy_key = d.get("easy_key") in truthy
        opts.part = normalize_part(d.get("part", "auto"))
        return opts


@dataclass
class Mark:
    """An expression mark on the flute part, in ticks like QNote (0 = first downbeat).

    kind: "dynamic" (value p, pp, mp, mf, f, ff, sf, fp), "cresc" / "dim"
    (hairpin from start to end), "slur" (start..end, first and last note),
    "staccato", "tenuto", "accent", "fermata", "trill", "mordent", "turn",
    "grace" (value = MIDI pitch of a grace note before the note at start),
    "text" (value = words: "dolce", "rit.", "Andante espressivo", ...).
    Single-note marks attach to the flute note that starts at `start`.
    """
    kind: str
    start: int
    end: int = 0
    value: str = ""


@dataclass
class Arrangement:
    flute: list[QNote]
    rh: list[QNote] = field(default_factory=list)
    lh: list[QNote] = field(default_factory=list)
    beats_per_bar: int = 4
    bpm: float = 100.0            # beats (beat_ticks long) per minute
    key_fifths: int = 0
    key_mode: str = "major"
    shift: int = 0
    warnings: list[str] = field(default_factory=list)
    beat_ticks: int = QUARTER_BEAT
    expression: list[Mark] = field(default_factory=list)

    @property
    def bar_ticks(self) -> int:
        return self.beats_per_bar * self.beat_ticks

    @property
    def time_signature(self) -> tuple[int, int]:
        return time_signature(self.beats_per_bar, self.beat_ticks)
