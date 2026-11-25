"""Pick the tune a listener would hum out of a note transcription.

top_voice walks through a polyphonic transcription (piano, or an instrument
stem) and keeps one line: high, loud, fresh notes joined by small steps.
clean_monophonic tidies a line that is already one voice (the vocal stem).
"""
from __future__ import annotations

import numpy as np

from .model import Note

# top_voice
MIN_DUR = 0.05        # s, shorter notes are transcription noise
WINDOW = 200          # how many earlier notes may come right before a note
SAME_ONSET = 0.05     # s, notes struck closer than this are one chord
REF_SPAN = 3.0        # s, each side, for the local height of the tune ...
REF_Q = 75            # ... taken as this percentile of the highest sounding pitch
REF_STEP = 0.05       # s, time step for that
PITCH_W = 0.5       # gain per octave above or below the local top line
VEL_W = 0.5           # gain for full velocity
DECAY = 1.25          # s, a held note's worth fades so fresh notes win over ringing ones
COVER = 2.5           # per s spent under a higher note struck with or after it
STEP_COST = 0.04      # per semitone between neighbours in the line
LEAP_COST = 0.3       # extra for a leap over an octave
UNDER = 5             # semitones: a line note this far under ...
UNDER_SHARE = 0.8     # ... an unchosen note for this share of its length is dropped
OVERLAP = 0.3         # a line note may still sound this share of its length into the next ...
OVERLAP_MAX = 0.2     # ... but no more than this many seconds (legato, not a held note)
NEAR = 4              # semitones: "the next note near it in pitch" ...
PEDAL_RATIO = 1.5     # ... notes ringing this many times past it look pedal-held
PEDAL_SPAN = 2.0      # s, each side, for judging whether the pedal is down

# clean_monophonic
MERGE_GAP = 0.08      # s
MIN_KEEP = 0.06       # s


def top_voice(notes: list[Note]) -> list[Note]:
    """Highest sensible single line through `notes` (returns new Note objects).

    Dynamic programming over notes sorted by onset (notes under MIN_DUR are
    dropped). A line note lasts until the next line note is struck, so note j
    followed by note i is worth, for L = min(dur_j, start_i - start_j):

        w_j * DECAY * (1 - exp(-L / DECAY))          fading, like a piano note
        - COVER * (part of L after a higher note was struck with/after j)
        w_j = 1 + VEL_W*vel/127 + PITCH_W*min((pitch - ref)/12, 1)

    ref is the REF_Q percentile, over time within REF_SPAN of the onset, of
    the highest sounding pitch: the tune's height, which a short rest doesn't
    pull down, so accompaniment well under the tune is worth little even while
    the tune rests. Moving between line notes costs STEP_COST*|dp| (+ LEAP_COST
    over an octave). Note j may come right before note i if it was struck at
    least SAME_ONSET earlier and has ended by start_i + min(OVERLAP*dur_j,
    OVERLAP_MAX) (one voice can't hold a note while playing the next). Where
    the pedal looks down, ends only say when the pedal came up, so that second
    rule is off. Only the last WINDOW notes are searched, plus the best line
    that ended before them (so a long rest over busy accompaniment keeps the
    line going, with no leap cost after it).
    Afterwards line notes are cut at the next line note, and line notes buried
    UNDER+ semitones under an unchosen note struck with them are dropped.
    """
    ns = _unisons_merged(notes)
    if not ns:
        return []
    p = np.array([n.pitch for n in ns], dtype=float)
    s = np.array([n.start for n in ns], dtype=float)
    e = np.array([n.end for n in ns], dtype=float)
    d = e - s
    v = np.array([n.velocity for n in ns], dtype=float)

    ref = _local_top(p, s, e)
    w = 1 + VEL_W * v / 127 + PITCH_W * np.minimum((p - ref) / 12, 1.0)
    free = np.maximum(_first_higher_onset(p, s, e) - s, 0.0)   # time before being covered
    pedal = _pedal_down(p, s, e)

    def worth(idx, length):
        return (w[idx] * DECAY * (1 - np.exp(-length / DECAY))
                - COVER * np.maximum(length - free[idx], 0.0))

    def can_lead(idx, t, pedal_down):
        """Notes idx may come right before a line note struck at time t."""
        slack = np.minimum(OVERLAP * d[idx], OVERLAP_MAX)
        return (s[idx] <= t - SAME_ONSET) & (pedal_down | (e[idx] <= t + slack))

    n = len(ns)
    score = np.zeros(n)               # best line ending at i, not counting i itself
    total = np.zeros(n)               # ... and counting i to its end
    pred = np.full(n, -1)
    total[0] = worth(0, d[0])
    carry, carry_j, gone = -np.inf, -1, 0     # best total among notes out of the window
    for i in range(1, n):
        lo = max(0, i - WINDOW)
        while gone < lo:
            if total[gone] > carry:
                carry, carry_j = total[gone], gone
            gone += 1
        best, best_j = 0.0, -1
        hi = int(np.searchsorted(s, s[i] - SAME_ONSET, side="right"))
        if hi > lo:
            idx = np.arange(lo, hi)
            dp = np.abs(p[idx] - p[i])
            val = (score[idx] + worth(idx, np.minimum(d[idx], s[i] - s[idx]))
                   - STEP_COST * dp - LEAP_COST * (dp > 12))
            val = np.where(can_lead(idx, s[i], pedal[i]), val, -np.inf)
            k = int(np.argmax(val))
            if val[k] > best:
                best, best_j = val[k], lo + k
        if carry > best and can_lead(carry_j, s[i], pedal[i]):
            c = score[carry_j] + worth(carry_j, min(d[carry_j], s[i] - s[carry_j]))
            if c > best:
                best, best_j = c, carry_j
        score[i], pred[i] = best, best_j
        total[i] = best + worth(i, d[i])

    path = _backtrack(score, pred, worth, can_lead, s, d, pedal)
    keep = _not_buried(path, p, s, e)
    line = [Note(ns[i].pitch, ns[i].start, ns[i].end, ns[i].velocity)
            for i, ok in zip(path, keep) if ok]
    for a, b in zip(line, line[1:]):
        a.end = min(a.end, b.start)
    return line


def _unisons_merged(notes):
    """Copies of the notes of at least MIN_DUR, sorted by onset (high first);
    same-pitch notes struck within SAME_ONSET become one (one key, one note)."""
    out: list[Note] = []
    last: dict[int, Note] = {}
    for n in sorted(notes, key=lambda n: (n.start, -n.pitch)):
        if n.end - n.start < MIN_DUR:
            continue
        m = last.get(n.pitch)
        if m is not None and n.start - m.start < SAME_ONSET:
            m.end = max(m.end, n.end)
            m.velocity = max(m.velocity, n.velocity)
            continue
        last[n.pitch] = Note(n.pitch, n.start, n.end, n.velocity)
        out.append(last[n.pitch])
    return out


def _local_top(p, s, e):
    """REF_Q percentile over time of the highest sounding pitch, within
    REF_SPAN of each onset (silence left out)."""
    t0 = s.min()
    a = np.round((s - t0) / REF_STEP).astype(int)
    b = np.maximum(np.round((e - t0) / REF_STEP).astype(int), a + 1)
    half = int(round(REF_SPAN / REF_STEP))
    top = np.full(b.max() + 2 * half, np.nan)       # padded by `half` on both sides
    for i in np.argsort(p, kind="stable"):          # higher pitches overwrite lower ones
        top[half + a[i]:half + b[i]] = p[i]
    windows = np.lib.stride_tricks.sliding_window_view(top, 2 * half + 1)[a]
    return np.nanpercentile(windows, REF_Q, axis=1)   # each window holds its own onset


def _first_higher_onset(p, s, e):
    """For each note, when the first higher note is struck with or during it
    (inf if never). Notes struck earlier that still ring don't count."""
    a = np.searchsorted(s, s - SAME_ONSET, side="left")
    b = np.searchsorted(s, e, side="left")
    h = np.full(len(p), np.inf)
    for j in range(len(p)):
        higher = p[a[j]:b[j]] > p[j]
        if higher.any():
            h[j] = s[a[j] + int(np.argmax(higher))]
    return h


def _pedal_down(p, s, e):
    """True where note ends look pedal-held: around that time, most notes ring
    on PEDAL_RATIO+ times longer than the gap to the next note struck within
    NEAR semitones of them (with fingers alone that ratio stays near 1)."""
    n = len(p)
    ratio = np.full(n, np.nan)
    first = np.searchsorted(s, s + SAME_ONSET, side="left")
    for j in range(n):
        a = first[j]
        near = np.abs(p[a:a + 64] - p[j]) <= NEAR
        if near.any():
            ratio[j] = (e[j] - s[j]) / (s[a + int(np.argmax(near))] - s[j])
    valid = ~np.isnan(ratio)
    long = valid & (ratio > PEDAL_RATIO)
    cv = np.concatenate([[0], np.cumsum(valid)])
    cl = np.concatenate([[0], np.cumsum(long)])
    lo = np.searchsorted(s, s - PEDAL_SPAN, side="left")
    hi = np.searchsorted(s, s + PEDAL_SPAN, side="right")
    return 2 * (cl[hi] - cl[lo]) > (cv[hi] - cv[lo])


def _backtrack(score, pred, worth, can_lead, s, d, pedal):
    """Follow the best line back. When it starts fresh (nothing in the window
    was worth joining), carry on with the best line that can lead into it, so
    a long rest over busy accompaniment doesn't cut off everything earlier."""
    allidx = np.arange(len(s))
    path: list[int] = []
    val = score + worth(allidx, d)
    while True:
        k = int(np.argmax(val))
        if not val[k] > 0:
            break
        chain = []
        while k >= 0:
            chain.append(k)
            k = int(pred[k])
        path[:0] = chain[::-1]
        first = chain[-1]
        length = np.minimum(d, s[first] - s)
        val = np.where(can_lead(allidx, s[first], pedal[first]),
                       score + worth(allidx, length), -np.inf)
    return path


def _not_buried(path, p, s, e):
    """False for line notes UNDER+ semitones under unchosen notes, struck with
    or during them, for more than UNDER_SHARE of their time as line notes.
    Octave doublings struck together don't count (the pitch class is right)."""
    chosen = np.zeros(len(p), bool)
    chosen[path] = True
    keep = []
    for k, i in enumerate(path):
        start = s[i]
        end = min(e[i], s[path[k + 1]]) if k + 1 < len(path) else e[i]
        cand = np.flatnonzero(~chosen & (p >= p[i] + UNDER)
                              & (s >= start - SAME_ONSET) & (s < end))
        cand = cand[~(((p[cand] - p[i]) % 12 == 0) & (np.abs(s[cand] - start) <= SAME_ONSET))]
        if len(cand) == 0:
            keep.append(True)
            continue
        cuts = np.unique(np.clip(np.concatenate([[start, end], s[cand], e[cand]]), start, end))
        mids = (cuts[:-1] + cuts[1:]) / 2
        hit = ((s[cand][None, :] <= mids[:, None]) & (e[cand][None, :] > mids[:, None])).any(axis=1)
        keep.append(float(np.diff(cuts)[hit].sum()) <= UNDER_SHARE * (end - start))
    return keep


VOICING_GAP = 12      # velocity: a projected tune note is this much over the notes around it
VOICING_SPAN = 1.5    # s, each side: "around it"
VOICING_MIN = 20      # projected notes needed (and at least VOICING_SHARE of all notes)
VOICING_SHARE = 0.08
VOICING_TOP = 0.75    # share of projected notes that must be the highest struck at their moment
VOICING_COVER = 0.3   # share of the piece the projected line must cover


def projected_line(notes: list[Note]) -> list[Note] | None:
    """The tune as the pianist voiced it, or None if they didn't voice one.

    Real pianists play the tune clearly louder than the accompaniment (e.g.
    velocity 85-90 over chords at 45-70). When enough notes stand that far
    above the notes around them, are nearly always the highest note struck at
    their moment, and together cover a good part of the piece, they are the
    tune: each is held until the next. Stretches without such notes (a
    chords-only intro) become rests. Evenly played music returns None, and
    top_voice is used instead.
    """
    ns = sorted(notes, key=lambda n: n.start)
    if len(ns) < VOICING_MIN:
        return None
    s = np.array([n.start for n in ns])
    v = np.array([n.velocity for n in ns], dtype=float)
    p = np.array([n.pitch for n in ns])
    loud = []
    for i in range(len(ns)):
        lo = np.searchsorted(s, s[i] - VOICING_SPAN)
        hi = np.searchsorted(s, s[i] + VOICING_SPAN)
        others = np.r_[v[lo:i], v[i + 1:hi]]
        if len(others) >= 3 and v[i] >= np.median(others) + VOICING_GAP:
            loud.append(i)
    if len(loud) < max(VOICING_MIN, VOICING_SHARE * len(ns)):
        return None
    on_top = []
    for i in loud:
        lo = np.searchsorted(s, s[i] - SAME_ONSET)
        hi = np.searchsorted(s, s[i] + SAME_ONSET)
        if p[i] >= p[lo:hi].max():
            on_top.append(i)
    if len(on_top) < VOICING_TOP * len(loud):
        return None
    line: list[Note] = []
    for i in on_top:                 # an accented bass note under the tune isn't the tune
        n = ns[i]
        if line and n.start - line[-1].start < SAME_ONSET:
            if n.pitch > line[-1].pitch:
                line[-1] = Note(n.pitch, n.start, n.end, n.velocity)
            continue
        line.append(Note(n.pitch, n.start, n.end, n.velocity))
    # a bass note struck alone can also be loud and "on top": drop notes more
    # than an octave under the tune's own register around them
    ls = np.array([n.start for n in line])
    lp = np.array([n.pitch for n in line])
    keep = []
    for n in line:
        lo = np.searchsorted(ls, n.start - REF_SPAN)
        hi = np.searchsorted(ls, n.start + REF_SPAN)
        if n.pitch >= np.median(lp[lo:hi]) - 12:
            keep.append(n)
    line = keep
    for a, b in zip(line, line[1:]):
        a.end = min(a.end, b.start)
    span = max(ns[-1].end - ns[0].start, 1e-6)
    if sum(n.end - n.start for n in line) / span < VOICING_COVER:
        return None
    return line


FILL_GAP = (1.0, 4.0)   # s: gaps inside a voiced tune this long are filled from the top line
FILL_LOUDER = 4         # velocity over the median of the notes around, for a fill note
FILL_RANGE = 7          # semitones under the lower of the two tune notes around the gap


def piano_melody(notes: list[Note]) -> list[Note]:
    """The tune of a piano part: the pianist's voicing if clear, else top_voice.

    A pianist doesn't voice every tune note equally: softer stretches drop out
    of projected_line and would read as rests. Gaps of FILL_GAP inside the tune
    are filled with top-line notes that still stand out a little and stay near
    the tune's register; longer gaps (interludes, a chords-only intro) stay rests.
    """
    line = projected_line(notes)
    if not line:
        return top_voice(notes)
    top = top_voice(notes)
    starts = np.array([n.start for n in notes])
    vels = np.array([n.velocity for n in notes], dtype=float)
    out = list(line)
    for a, b in zip(line, line[1:]):
        gap = b.start - a.start
        if not FILL_GAP[0] <= gap <= FILL_GAP[1]:
            continue
        lo = np.searchsorted(starts, a.start)
        hi = np.searchsorted(starts, b.start)
        around = float(np.median(vels[lo:hi])) if hi > lo else 0.0
        floor = min(a.pitch, b.pitch) - FILL_RANGE
        for n in top:
            if (a.start + SAME_ONSET < n.start < b.start - SAME_ONSET and n.pitch >= floor
                    and n.velocity >= around + FILL_LOUDER):
                out.append(Note(n.pitch, n.start, n.end, n.velocity))
    out.sort(key=lambda n: n.start)
    for x, y in zip(out, out[1:]):
        x.end = min(x.end, y.start)
    return [n for n in out if n.end > n.start]


def clean_monophonic(notes: list[Note]) -> list[Note]:
    """Tidy a single-voice line (returns new Note objects).

    Where notes overlap the louder one keeps the overlap; same-pitch pieces
    with gaps under MERGE_GAP are joined (vibrato makes pitch trackers split
    a held note); notes under MIN_KEEP are dropped.
    """
    ns = sorted((Note(n.pitch, n.start, n.end, n.velocity) for n in notes),
                key=lambda n: (n.start, -n.velocity))
    out: list[Note] = []
    for n in ns:
        while out and n.start < out[-1].end:
            prev = out[-1]
            if n.velocity > prev.velocity:
                prev.end = n.start
                if prev.end <= prev.start:
                    out.pop()
                    continue
            else:
                n.start = prev.end
            break
        if n.end > n.start:
            out.append(n)
    out = _merge_same_pitch(out)
    out = [n for n in out if n.end - n.start >= MIN_KEEP]
    return _merge_same_pitch(out)


def _merge_same_pitch(line: list[Note]) -> list[Note]:
    out: list[Note] = []
    for n in line:
        if out and out[-1].pitch == n.pitch and n.start - out[-1].end < MERGE_GAP:
            out[-1].end = max(out[-1].end, n.end)
            out[-1].velocity = max(out[-1].velocity, n.velocity)
        else:
            out.append(n)
    return out
