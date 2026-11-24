"""Plain data types passed between the steps.

Times come in two flavours:
- Note: seconds, straight from the audio.
- QNote: ticks on the beat grid (TICKS_PER_BEAT per quarter-note beat),
  where tick 0 is the first downbeat. Negative ticks are a pickup.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field, asdict
from pathlib import Path
from statistics import median
from typing import Mapping

from .config import TICKS_PER_BEAT


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
        return self.to_beats(t) * TICKS_PER_BEAT

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
            "sources": {k: _notes_to_json(v) for k, v in self.sources.items()},
        }
        Path(path).write_text(json.dumps(data))

    @classmethod
    def load(cls, path: Path) -> "Analysis":
        d = json.loads(Path(path).read_text())
        return cls(
            song_id=d["song_id"],
            kind=d["kind"],
            duration=d["duration"],
            title=d.get("title", ""),
            grid=BeatGrid(**d["grid"]),
            sources={k: _notes_from_json(v) for k, v in d["sources"].items()},
        )


_CHOICES = {
    "mode": ("solo", "duet"),
    "melody_source": ("auto", "vocals", "piano", "other"),
    "piano_style": ("chords", "cleaned"),
}


@dataclass
class ArrangeOptions:
    mode: str = "solo"
    melody_source: str = "auto"
    octave: int = 0               # -1, 0, +1 relative to the automatic choice
    simple: bool = False
    easy_key: bool = False
    piano_style: str = "chords"
    meter: int = 0                # 0 = as detected, else beats per bar (3 or 4)
    barshift: int = 0             # move the bar lines this many beats later (0-3)
    tempo: int = 0                # -1 = half as fast (beats twice as long), +1 = twice as fast

    def key(self) -> str:
        return (f"{self.mode}-{self.melody_source}-o{self.octave:+d}-"
                f"{'simple' if self.simple else 'full'}-"
                f"{'easy' if self.easy_key else 'orig'}-{self.piano_style}-m{self.meter}"
                f"-b{self.barshift}-t{self.tempo:+d}")

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
        try:
            m = int(d.get("meter", 0))
            opts.meter = m if m in (0, 3, 4) else 0
        except (TypeError, ValueError):
            opts.meter = 0
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
        return opts


@dataclass
class Arrangement:
    flute: list[QNote]
    rh: list[QNote] = field(default_factory=list)
    lh: list[QNote] = field(default_factory=list)
    beats_per_bar: int = 4
    bpm: float = 100.0
    key_fifths: int = 0
    key_mode: str = "major"
    shift: int = 0
    warnings: list[str] = field(default_factory=list)
