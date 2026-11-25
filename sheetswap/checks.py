"""Hard checks on a finished arrangement, reported in plain English."""
from __future__ import annotations

from bisect import bisect_left

from .config import FLUTE_HARD
from .model import Arrangement, QNote

_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def bar_ticks(arr: Arrangement) -> int:
    return arr.bar_ticks


def lead_in_ticks(arr: Arrangement) -> int:
    """Whole bars added before tick 0 so pickup notes (negative ticks) fit."""
    earliest = min((n.start for n in (*arr.flute, *arr.rh, *arr.lh)), default=0)
    if earliest >= 0:
        return 0
    bar = bar_ticks(arr)
    return -(earliest // bar) * bar


def pickup_start(arr: Arrangement) -> int:
    """Where the music of a pickup bar starts, in ticks into that (first) bar;
    0 when the first bar is complete (no pickup, or a lead-in of whole bars).

    A pickup bar holds only its notes, no rests before them (R-PAGE-5)."""
    lead = lead_in_ticks(arr)
    if not lead:
        return 0
    earliest = min(n.start for n in (*arr.flute, *arr.rh, *arr.lh))
    return (earliest + lead) % bar_ticks(arr)


def printed_bar(arr: Arrangement, tick: int) -> int:
    """The bar number printed over a tick: bars count from 1 at the first full
    bar, so a pickup bar is bar 0 (R-PAGE-5)."""
    return (tick + lead_in_ticks(arr)) // bar_ticks(arr) + (0 if pickup_start(arr) else 1)


def _name(pitch: int) -> str:
    return f"{_NAMES[pitch % 12]}{pitch // 12 - 1}"


def _where(arr: Arrangement, notes: list[QNote]) -> str:
    bars = sorted({printed_bar(arr, n.start) for n in notes})
    shown = ", ".join(str(b) for b in bars[:8]) + (", ..." if len(bars) > 8 else "")
    return f"bar {shown}" if len(bars) == 1 else f"bars {shown}"


def _many(n: int, what: str, one: str, more: str) -> str:
    return f"1 {what} {one}" if n == 1 else f"{n} {what}s {more}"


def _piano_over_flute(flute: list[QNote], piano: list[QNote]) -> list[QNote]:
    """Piano notes that sound at or above a flute note sounding at the same time."""
    starts = [f.start for f in flute]
    longest = max(f.dur for f in flute)
    bad = []
    for p in piano:
        i = bisect_left(starts, p.end) - 1          # last flute note starting before p ends
        while i >= 0 and flute[i].start + longest > p.start:
            f = flute[i]
            if f.end > p.start and f.dur > 0 and p.pitch >= f.pitch:
                bad.append(p)
                break
            i -= 1
    return bad


def check_arrangement(arr: Arrangement, simple: bool) -> list[str]:
    flute = sorted(arr.flute, key=lambda n: (n.start, n.pitch))
    if not flute:
        return ["The flute part is empty: no melody was found."]
    problems: list[str] = []
    lo, hi = FLUTE_HARD

    bad = [n for n in flute if not lo <= n.pitch <= hi]
    if bad:
        worst = max(bad, key=lambda n: abs(n.pitch - (lo + hi) / 2))
        problems.append(f"{_many(len(bad), 'flute note', 'is', 'are')} outside the flute "
                        f"range {_name(lo)}-{_name(hi)} (for example {_name(worst.pitch)}), "
                        f"{_where(arr, bad)}.")

    shortest = 6 if simple else 3
    bad = [n for n in flute if n.dur < shortest]
    if bad:
        label = "an 8th note" if simple else "a 16th note"
        problems.append(f"{_many(len(bad), 'flute note', 'is', 'are')} shorter than "
                        f"{label}, {_where(arr, bad)}.")

    bad = [a for a, b in zip(flute, flute[1:]) if a.end > b.start]
    if bad:
        problems.append(f"{_many(len(bad), 'flute note', 'overlaps', 'overlap')} the next "
                        f"note (the flute plays one note at a time), {_where(arr, bad)}.")

    bad = _piano_over_flute(flute, [*arr.rh, *arr.lh])
    if bad:
        problems.append(f"{_many(len(bad), 'piano note', 'sounds', 'sound')} at or above "
                        f"the flute, {_where(arr, bad)}.")
    return problems
