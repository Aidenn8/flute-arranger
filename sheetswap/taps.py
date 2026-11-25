"""Tap the beat: the user's taps along the recording set the beat and the bars.

On a recording's page the user plays the song and taps on every beat (space
bar or the Tap button) for 8 beats or more, and may mark the first beat of
each bar (Shift, B or the "bar start" button). The tap times
(audio.currentTime, seconds) come here.

Taps are sloppy: tens of ms early or late, the odd double tap, a missed beat,
a stray tap between beats, all of them a little late through the speakers'
latency, and usually only part of the song. So the taps settle what the beat
tracker most often gets wrong, and the tracker's grid keeps the timing:

- the level: how many of the tracker's beats one tapped beat takes, a simple
  ratio (2: the tracker heard 8ths; 1/3: it heard one beat a bar of 3/4);
- the phase: which of the tracker's beats (or points between them) are the
  beats, from where the taps fall;
- the bar: tapped bar starts give the meter and beat 1; without them the
  tracker's bar lines at the new level (3 taps a tracker bar = 3/4; a
  tracker beat holding 3 or 4 taps is a bar).

The new beats are the tracker's beats at the tapped level and phase, so they
follow its tempo changes over the whole song; where a tap disagrees with them
(the tracker slipped or drifted there), the tap's own time is used.

    grid = grid_from_taps(taps, duration, bar_starts, analysis.grid)
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .config import TICKS_PER_BEAT
from .model import QUARTER_BEAT, BeatGrid

MIN_TAPS = 8              # the page asks for at least this many
MIN_PERIOD, MAX_PERIOD = 0.2, 4.0   # seconds between tapped beats (300 down to 15 a minute)
OFF_BEAT = 0.3            # beats: a tap farther than this from where its beat falls is dropped
PAUSE = 4.5               # beats: a longer gap means the user stopped tapping for a while
NEIGHBOURS = 4            # taps on each side for the local tempo and the local line
SUPPORT = 0.5             # a tap must keep the beat with this share of its neighbours
RATIOS = ((1, 1), (2, 1), (3, 1), (4, 1), (1, 2), (1, 3), (1, 4),
          (3, 2), (2, 3), (4, 3), (3, 4))   # tapped beat / tracker beat
RATIO_TOL = 0.1           # |log| distance from the measured ratio to one of RATIOS
TAP_LAG = 0.05            # s: taps usually come a little late (speaker latency beats anticipation)
SNAP = 0.2                # tapped beats: a tap this close to the tracker's beat takes the tracker's time
LAG_SHARE = 0.25          # share of the taps that must agree on a lag
LAG_WIDTH = 0.15          # ...within this share of the tracker's grid step
METER_SHARE = 0.75        # share of the gaps between tapped bar starts a meter must explain
ON_TRACKER = 0.1          # tracker beats: a new beat this close to one of them is on it
TAPS_FILE = "taps.json"   # in the song folder: the taps and the automatic grid (for undo)
AGREE_MIN = 0.5           # tracker_agreement below this, with taps over less than COVER_MIN
COVER_MIN = 0.8           # of the song: the page asks for taps through the whole song
UNSTEADY = "The taps don't keep a steady beat. Try again: one tap on every beat while the song plays."


def grid_from_taps(taps, duration: float, downbeats=None, current: BeatGrid | None = None) -> BeatGrid:
    """A BeatGrid (tracker="taps") covering 0 s .. duration from the user's taps.

    taps: seconds of every tap; downbeats: the taps marked as a bar's first
    beat (or None); current: the automatic grid, followed outside the taps.
    Raises ValueError when there are too few taps or they keep no beat.
    """
    current, times, nums, period, r, q, lattice, snapped, anchors = _read(taps, duration, current)

    def where(n: np.ndarray) -> np.ndarray:
        """Tracker positions of beat numbers n: between taps through the anchors,
        beyond them at the tapped level from the outermost tap the tracker agrees with."""
        out = np.interp(n, nums, anchors)
        lo, hi = n < nums[0], n > nums[-1]
        if snapped.any():
            first, last = np.flatnonzero(snapped)[[0, -1]]
            out[lo] = lattice[first] + r * n[lo]
            out[hi] = lattice[last] + r * n[hi]
        else:
            out[lo] = anchors[0] + r * (n[lo] - nums[0])
            out[hi] = anchors[-1] + r * (n[hi] - nums[-1])
        return out

    def seconds(xs: np.ndarray) -> np.ndarray:
        return np.array([current.to_seconds(v - current.first_downbeat) for v in xs])

    # beat numbers from just before 0 s to just after the end
    lo, hi = int(nums[0]), int(nums[-1])
    step = max(1, int(round(1.0 / period)))
    for _ in range(100000):
        if seconds(where(np.array([lo], dtype=float)))[0] <= 0.0:
            break
        lo -= step
    for _ in range(100000):
        if seconds(where(np.array([hi], dtype=float)))[0] >= duration:
            break
        hi += step
    n_all = np.arange(lo, hi + 1, dtype=float)
    xs = where(n_all)
    secs = seconds(xs)
    keep = secs > 0.0
    first_in = int(np.argmax(keep))
    if first_in > 0:
        keep[first_in - 1] = True                           # one beat at or before 0 s
    while keep.sum() > 2 and secs[np.flatnonzero(keep)[-2]] >= duration:
        keep[np.flatnonzero(keep)[-1]] = False              # one beat at or after the end
    n_all, xs, secs = n_all[keep], xs[keep], secs[keep]
    secs[0] = max(secs[0], 0.0)
    ok = np.concatenate([[True], np.diff(secs) > 1e-3])
    n_all, xs, secs = n_all[ok], xs[ok], secs[ok]

    bpb, bar_phase = _bars(n_all, xs, r, q, current, _marked(downbeats, times, nums, duration))
    start = current.beats[min(current.first_downbeat, len(current.beats) - 1)]
    bar_start = [i for i in range(len(n_all)) if (int(n_all[i]) - bar_phase) % bpb == 0]
    first = next((i for i in bar_start if secs[i] >= start - 0.25 * period), bar_start[0])
    return BeatGrid([float(s) for s in secs], int(bpb), int(first), QUARTER_BEAT, tracker="taps")


def tracker_agreement(taps, duration: float, current: BeatGrid | None) -> float:
    """Share of the taps that fall on the tracker's grid at the tapped level, all
    with one phase (1.0: the tracker keeps step with the taps; low: it slips
    against them, so the grid beyond the taps may be off too)."""
    *_, lattice, snapped, _anchors = _read(taps, duration, current)
    if lattice is None:
        return 0.0
    common = Counter(np.round(lattice[snapped], 6)).most_common(1)
    return common[0][1] / len(lattice) if common else 0.0


def _read(taps, duration: float, current: BeatGrid | None):
    """The taps against the tracker's grid: (grid, tap times, beat numbers,
    seconds per tapped beat, tracker beats per tapped beat r, its denominator q
    (None: no plain ratio), each tap's snapped phase, whether the tracker's
    beat is taken there, the tracker positions the beats pass through)."""
    tracked = current is not None and len(current.beats) >= 2
    if not tracked:
        current = BeatGrid([0.0, 1.0], 4, 0)                # a clock: 1 "beat" a second
    t = _clean(taps, duration)
    if len(t) < MIN_TAPS:
        raise ValueError(f"Tap at least {MIN_TAPS} beats.")
    times, nums, segs = _number_taps(t)
    if len(times) < max(4, MIN_TAPS // 2):
        raise ValueError(UNSTEADY)
    period = _tempo(times, nums, segs)
    if not MIN_PERIOD <= period <= MAX_PERIOD:
        raise ValueError("The taps are too fast or too slow for a beat.")

    x = _tracker_beats(current, times)
    r, q = _ratio(x, nums, segs, plain=tracked)
    nums = _link_segments(x, nums, segs, r)
    if q:
        y = x - _lag(x - r * nums, q, current, times)
        lattice = np.round((y - r * nums) * q) / q          # snapped phase of each tap
        z = lattice + r * nums                              # the tracker's beat nearest each tap
        snapped = np.abs(y - z) <= SNAP * r
        anchors = np.where(snapped, z, y)
    else:
        lattice, snapped, anchors = None, np.zeros(len(x), dtype=bool), x
    return current, times, nums, period, r, q, lattice, snapped, anchors


def first_bar_at(grid: BeatGrid, music_start: float) -> BeatGrid:
    """The same bar lines, with beat 1 the first bar start at or after the music
    starts (so the pickup is shorter than a bar and no bar is empty)."""
    b = grid.beats
    if len(b) < 2:
        return grid
    period = float(np.median(np.diff(b)))
    bpb = grid.beats_per_bar
    ph = grid.first_downbeat % bpb
    first = next((i for i in range(len(b)) if i % bpb == ph and b[i] >= music_start - 0.25 * period),
                 grid.first_downbeat)
    return replace(grid, first_downbeat=first)


# ---------------------------------------------------------------- the taps on their own

def _clean(taps, duration: float) -> np.ndarray:
    out = []
    for v in taps or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and -1.0 <= f <= duration + 1.0:
            out.append(f)
    return np.unique(np.array(out, dtype=float))


def _typical(gaps: np.ndarray, around: float | None = None) -> float:
    """The time of one beat among gaps between taps: their median, refined on
    the gaps of about one beat (double taps and missed beats left out)."""
    p = float(np.median(gaps)) if around is None else around
    for _ in range(3):
        one = gaps[(gaps > 0.6 * p) & (gaps < 1.6 * p)]
        if not len(one):
            break
        p = float(np.median(one))
    return p


def _number_taps(t: np.ndarray):
    """(times, beat numbers, segment ids) of the taps that fall on beats.

    A tap is dropped when it keeps the beat with too few of its neighbours (a
    stray tap), when it comes less than half a beat after the last one kept
    (a double tap) or too far from a whole number of beats after it. A gap of
    more than PAUSE beats starts a new segment (numbered later, against the
    tracker). Then each tap is checked against a line through its neighbours.
    """
    gaps = np.diff(t)
    p_all = _typical(gaps)
    local = np.array([_typical(gaps[max(0, i - NEIGHBOURS):i + NEIGHBOURS + 1], p_all)
                      for i in range(len(gaps))])
    tap_p = np.concatenate([[local[0]], local])             # period around each tap

    # strays: taps out of step with most of their neighbours
    good = np.ones(len(t), dtype=bool)
    for i in range(len(t)):
        nb = [j for j in range(max(0, i - NEIGHBOURS), min(len(t), i + NEIGHBOURS + 1)) if j != i]
        d = np.abs(t[nb] - t[i]) / tap_p[i]
        k = np.round(d)
        near = (k >= 1) & (k <= 4)
        fits = near & (np.abs(d - k) <= OFF_BEAT)
        if not near.any() or fits.sum() < SUPPORT * near.sum():
            good[i] = False                                 # out of step, or alone
    if good.sum() < SUPPORT * len(t):
        raise ValueError(UNSTEADY)
    t, tap_p = t[good], tap_p[good]

    times, nums, segs = [t[0]], [0], [0]
    for i in range(1, len(t)):
        p = tap_p[i]
        gap = t[i] - times[-1]
        if gap > PAUSE * p:
            times.append(t[i])
            nums.append(nums[-1] + int(round(gap / p)))
            segs.append(segs[-1] + 1)
            continue
        k = int(round(gap / p))
        if k < 1 or abs(gap - k * p) > OFF_BEAT * p:
            continue
        times.append(t[i])
        nums.append(nums[-1] + k)
        segs.append(segs[-1])
    times, nums, segs = np.array(times), np.array(nums, dtype=float), np.array(segs)

    keep = np.ones(len(times), dtype=bool)
    for i in range(len(times)):
        nb = [j for j in range(max(0, i - NEIGHBOURS), min(len(times), i + NEIGHBOURS + 1))
              if j != i and segs[j] == segs[i]]
        if len(nb) < 3:
            continue
        a, b = _line(nums[nb], times[nb])
        if abs(times[i] - (a * nums[i] + b)) > OFF_BEAT * a:
            keep[i] = False
    return times[keep], nums[keep], segs[keep]


def _line(n: np.ndarray, t: np.ndarray) -> tuple[float, float]:
    """t ~ a*n + b (Theil-Sen: a stray point doesn't tilt it)."""
    i, j = np.triu_indices(len(n), 1)
    dn = n[j] - n[i]
    ok = dn != 0
    a = float(np.median((t[j] - t[i])[ok] / dn[ok])) if ok.any() else 1.0
    return a, float(np.median(t - a * n))


def _tempo(times, nums, segs) -> float:
    """Median seconds per tapped beat, from neighbouring taps in a segment."""
    same = segs[1:] == segs[:-1]
    per = (np.diff(times) / np.diff(nums))[same]
    if not len(per):
        raise ValueError(UNSTEADY)
    return float(np.median(per))


# ---------------------------------------------------------------- against the tracker's grid

def _tracker_beats(grid: BeatGrid, secs) -> np.ndarray:
    """Positions in the tracker's beats (0 = its first beat), fractional."""
    return np.array([grid.to_beats(float(s)) + grid.first_downbeat for s in secs])


def _ratio(x, nums, segs, plain: bool = True) -> tuple[float, int | None]:
    """(tracker beats per tapped beat, its denominator if it is a plain ratio
    (one of RATIOS) else None; with plain=False always the measured ratio)."""
    same = segs[1:] == segs[:-1]
    per = (np.diff(x) / np.diff(nums))[same]
    raw = float(np.median(per[per > 0])) if (per > 0).any() else 1.0
    p, q = min(RATIOS, key=lambda pq: abs(math.log(raw * pq[1] / pq[0])))
    if plain and abs(math.log(raw * q / p)) <= RATIO_TOL:
        return p / q, q
    return raw, None


def _link_segments(x, nums, segs, r: float) -> np.ndarray:
    """Number the beats across pauses in tapping by the tracker's beats between them."""
    nums = nums.copy()
    for k in range(1, len(nums)):
        if segs[k] != segs[k - 1]:
            jump = max(1, int(round((x[k] - x[k - 1]) / r)))
            nums[k:] += nums[k - 1] + jump - nums[k]
    return nums


def _lag(phase: np.ndarray, q: int, grid: BeatGrid, times) -> float:
    """How late the taps come against the tracker's grid points (1/q of its
    beats apart), in its beats.

    Where the taps fall between the grid points clusters around one place,
    or around a few when the tracker slips in places. Of the places that hold
    at least LAG_SHARE of the taps, the one nearest TAP_LAG seconds late wins
    (a steady lag is the speakers; the other places are the tracker slipping),
    and the lag is the median of the taps there."""
    step = 1.0 / q
    b = np.asarray(grid.beats, dtype=float)
    lo, hi = np.searchsorted(b, [min(times), max(times)])
    gaps = np.diff(b[max(0, lo - 1):hi + 2])
    beat_s = float(np.median(gaps)) if len(gaps) else 0.5
    bias = TAP_LAG / beat_s                                # seconds -> tracker beats
    w = (phase - bias + step / 2) % step - step / 2 + bias    # within half a step of the bias
    width = LAG_WIDTH * step

    def around(c):
        return np.abs((w - c + step / 2) % step - step / 2) <= width

    support = np.array([around(c).mean() for c in w])
    ok = support >= min(LAG_SHARE, support.max())
    c = w[ok][np.argmin(np.abs(w[ok] - bias))]
    return float(np.median(c + ((w[around(c)] - c + step / 2) % step - step / 2)))


def _marked(downbeats, times, nums, duration: float) -> np.ndarray | None:
    """Beat numbers of the taps marked as bar starts (None: no marks)."""
    if not downbeats:
        return None
    d = _clean(downbeats, duration)
    if not len(d):
        return None
    sec_per = np.diff(times) / np.diff(nums)
    before = d < times[0]
    after = d > times[-1]
    n = np.interp(d, times, nums)
    n[before] = nums[0] - (times[0] - d[before]) / sec_per[0]
    n[after] = nums[-1] + (d[after] - times[-1]) / sec_per[-1]
    return np.unique(np.round(n)).astype(int)


def _bars(n_all, xs, r: float, q, current: BeatGrid, marks) -> tuple[int, int]:
    """(beats per bar, bar phase: beat numbers n with n % bpb == phase start a bar)."""
    bpb, ph = _tracker_bars(n_all, xs, r, q, current)
    if marks is not None and len(marks):
        gaps = np.diff(marks)
        m_bpb = None
        if len(gaps):
            m_bpb = next((m for m in (4, 3, 2) if np.mean(gaps % m == 0) >= METER_SHARE), None)
        if m_bpb is None and len(gaps) == 0:
            m_bpb = bpb                                    # one mark: just where the bar starts
        if m_bpb is not None:
            bpb = m_bpb
            ph = Counter(int(n) % bpb for n in marks).most_common(1)[0][0]
    return bpb, ph


def _tracker_bars(n_all, xs, r: float, q, current: BeatGrid) -> tuple[int, int]:
    """The tracker's bar lines at the tapped level.

    u = taps per tracker bar. 2-4: that many beats a bar, on its bar lines. A
    tracker beat holding 3 or 4 taps is a bar of its own (a waltz tracked a
    bar a beat). More taps a tracker bar than 4: the largest of 4, 3, 2 that
    divides them. Fewer than 2 (taps slower than the tracker's bar): its
    count of beats a bar is kept, beat 1 on one of its bar lines if they show
    one, else on the beat nearest its first downbeat.
    """
    bpb_n = current.beats_per_bar if current.beats_per_bar in (2, 3, 4) else 4
    near = np.abs(xs - np.round(xs)) < ON_TRACKER
    k = np.round(xs).astype(int)
    on_down = near & ((k - current.first_downbeat) % max(1, current.beats_per_bar) == 0)
    marks = n_all[on_down]
    bpb = bpb_n
    if q:
        u = bpb_n / r
        U = int(round(u))
        per_beat = 1.0 / r
        if abs(u - U) < 0.05 and 2 <= U <= 4:
            bpb = U
        elif r < 1 and abs(per_beat - round(per_beat)) < 0.05 and round(per_beat) in (3, 4):
            bpb = int(round(per_beat))
            marks = n_all[near]
        elif abs(u - U) < 0.05 and U > 4:
            bpb = next((m for m in (4, 3, 2) if U % m == 0), bpb_n)
    if len(marks):
        counts = Counter(int(np.round(n)) % bpb for n in marks)
        ph, c = counts.most_common(1)[0]
        if c >= 0.6 * len(marks):
            return bpb, ph
    i = int(np.argmin(np.abs(xs - current.first_downbeat)))
    return bpb, int(n_all[i]) % bpb


# ---------------------------------------------------------------- the song folder

def save_taps(song_dir: Path, taps, downbeats, auto: BeatGrid, duration: float | None = None) -> None:
    """taps.json: the taps, the automatic grid they were tapped against and,
    with the song's duration, whether the page should ask for more taps
    (needs_more: they cover little of the song and the tracker slips against them)."""
    data = {"taps": [round(float(v), 3) for v in taps],
            "bars": [round(float(v), 3) for v in downbeats or []],
            "auto_grid": asdict(auto), "ticks_per_beat": TICKS_PER_BEAT}
    if duration and len(data["taps"]) >= 2:
        try:
            agree = tracker_agreement(taps, duration, auto)
        except ValueError:
            agree = 0.0
        covered = (max(data["taps"]) - min(data["taps"])) / duration
        data.update(agree=round(agree, 2), covered=round(covered, 2),
                    needs_more=bool(agree < AGREE_MIN and covered < COVER_MIN))
    tmp = Path(song_dir) / (TAPS_FILE + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, Path(song_dir) / TAPS_FILE)


def load_taps(song_dir: Path) -> dict | None:
    """taps.json, or None when the song's beat is the automatic one."""
    try:
        d = json.loads((Path(song_dir) / TAPS_FILE).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def saved_grid(song_dir: Path) -> BeatGrid | None:
    """The automatic grid kept in taps.json (None if there is none)."""
    d = load_taps(song_dir)
    try:
        grid = BeatGrid(**d["auto_grid"]) if d else None
        written = int(d.get("ticks_per_beat", TICKS_PER_BEAT)) if d else TICKS_PER_BEAT
    except (KeyError, TypeError, ValueError):
        return None
    if grid is not None and written != TICKS_PER_BEAT:       # saved on another tick grid
        grid.beat_ticks = grid.beat_ticks * TICKS_PER_BEAT // written
    return grid


def forget_taps(song_dir: Path) -> None:
    (Path(song_dir) / TAPS_FILE).unlink(missing_ok=True)
