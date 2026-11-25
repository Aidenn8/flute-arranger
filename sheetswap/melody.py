"""Pick the tune a listener would hum out of a note transcription.

top_voice walks through a polyphonic transcription (piano, or an instrument
stem) and keeps one line: high, loud, fresh notes joined by small steps.
piano_melody takes the tune a pianist voices (plays louder); fill_tune adds the
softer tune notes between those (passing notes, runs), choosing the path that
moves most like a melody. clean_monophonic tidies a line that is already one
voice (the vocal stem).
"""
from __future__ import annotations

from bisect import bisect_right

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
    Gaps up to FIGURE_GAP also get the tune hand's figures (_figure_notes), and
    tune notes are held while their key is struck again (_hold_restruck).
    """
    line = projected_line(notes)
    if not line:
        return top_voice(notes)
    top = top_voice(notes)
    ns = sorted(notes, key=lambda n: n.start)
    starts = np.array([n.start for n in ns])
    vels = np.array([n.velocity for n in ns], dtype=float)
    pedal = _pedal_down(np.array([n.pitch for n in ns], dtype=float), starts,
                        np.array([n.end for n in ns]))

    def pedal_at(t: float) -> bool:
        return bool(pedal[min(int(np.searchsorted(starts, t)), len(pedal) - 1)])

    out = list(line)
    for k, (a, b) in enumerate(zip(line, line[1:])):
        gap = b.start - a.start
        if not FILL_GAP[0] <= gap <= FIGURE_GAP:
            continue
        lo = np.searchsorted(starts, a.start)
        hi = np.searchsorted(starts, b.start)
        pace = float(np.median(np.diff([n.start for n in line[max(0, k - 3):k + 1]]))) if k else np.inf
        register = np.median([n.pitch for n in line
                              if a.start - REF_SPAN <= n.start <= b.start + REF_SPAN])
        out += _figure_notes(ns[lo:hi], a, b, pace, max(min(a.pitch, b.pitch), register))
        if gap > FILL_GAP[1]:
            continue
        around = float(np.median(vels[lo:hi])) if hi > lo else 0.0
        floor = min(a.pitch, b.pitch) - FILL_RANGE
        # not while the tune note a still sounds (as in _figure_notes): Chopin's
        # E minor prelude holds B4 for a bar over repeated chords topped by E4.
        # Where the pedal looks down, ends only say when it came up (top_voice).
        held_until = a.end - min(OVERLAP * (a.end - a.start), OVERLAP_MAX)
        for n in top:
            if (a.start + SAME_ONSET < n.start < b.start - SAME_ONSET
                    and (n.start > held_until or pedal_at(n.start))
                    and n.pitch >= floor and n.velocity >= around + FILL_LOUDER):
                out.append(Note(n.pitch, n.start, n.end, n.velocity))
    seen: set[tuple[int, float]] = set()
    uniq = []
    for n in sorted(out, key=lambda n: n.start):
        if (n.pitch, round(n.start, 3)) not in seen:
            seen.add((n.pitch, round(n.start, 3)))
            uniq.append(n)
    _hold_restruck(uniq, ns)
    for x, y in zip(uniq, uniq[1:]):
        x.end = min(x.end, y.start)
    return [n for n in uniq if n.end > n.start]


FIGURE_GAP = 8.0      # s: longest gap in the voiced tune that figures are looked for in
FIGURE_RANGE = 15     # semitones under the tune's register around a gap: still the tune's hand
FIGURE_MIN = 0.03     # s, shortest figure note
FIGURE_SOFT = 15      # velocity: notes struck this much softer than the loudest with them don't count
FIGURE_RUN = 3        # figure notes come in runs of at least this many, ...
FIGURE_PACE = 1.5     # ... no more than this many times faster than the tune just before


def _overtones(ns: list[Note]) -> set[int]:
    """Indexes (into ns, sorted) of notes an octave or two over a louder note
    struck with them: the piano model hearing that note's overtone."""
    out = set()
    for i, n in enumerate(ns):
        for j in range(i - 1, -1, -1):
            if n.start - ns[j].start >= SAME_ONSET:
                break
            if (n.pitch - ns[j].pitch) in (12, 24) and ns[j].velocity > n.velocity:
                out.add(i)
        for j in range(i + 1, len(ns)):
            if ns[j].start - n.start >= SAME_ONSET:
                break
            if (n.pitch - ns[j].pitch) in (12, 24) and ns[j].velocity > n.velocity:
                out.add(i)
    return out


def _figure_notes(ns: list[Note], a: Note, b: Note, pace: float, tune: float) -> list[Note]:
    """Notes the tune's hand plays one at a time between tune notes a and b
    (`ns`, sorted): the broken chords and runs between phrases (Für Elise's
    A-C-E-A), part of the line a flutist plays.

    Such a note is the only one struck at its moment in the tune's register
    (at most FIGURE_RANGE under `tune`, the higher of the lower tune note
    around the gap and the tune's median pitch around it, which keeps the
    bass hand's figures out; notes struck together are chords;
    overtones the model heard and much softer notes don't count), while the
    tune note a no longer sounds: accompaniment moving under a held tune
    note, as in a nocturne, is not the tune (a legato overlap as in
    top_voice is allowed). The same key struck again just as a ends is a, held.
    A figure carries on the tune's motion: at least FIGURE_RUN notes, at
    most FIGURE_PACE times faster than the tune's last notes (`pace`, s
    between onsets); faster notes after a held tune note are accompaniment
    (Debussy's arpeggios under Clair de lune's long notes).
    """
    floor = tune - FIGURE_RANGE
    held_until = a.end - min(OVERLAP * (a.end - a.start), OVERLAP_MAX)
    ghost = _overtones(ns)
    out: list[Note] = []
    i = 0
    while i < len(ns):
        j = i + 1
        while j < len(ns) and ns[j].start - ns[i].start < SAME_ONSET:
            j += 1
        group = [ns[k] for k in range(i, j) if k not in ghost and ns[k].pitch >= floor]
        t = ns[i].start
        i = j
        if not group or not max(a.start + SAME_ONSET, held_until) < t < b.start - SAME_ONSET:
            continue
        loudest = max(n.velocity for n in group)
        group = [n for n in group if n.velocity > loudest - FIGURE_SOFT]
        top = max(group, key=lambda n: n.pitch)
        if (len(group) > 1 or top.end - top.start < FIGURE_MIN
                or (top.pitch == a.pitch and top.start <= a.end + RESTRIKE_GAP)):
            continue
        out.append(Note(top.pitch, top.start, top.end, top.velocity))
    if len(out) < FIGURE_RUN:
        return []
    if pace > FIGURE_PACE * float(np.median(np.diff([a.start] + [n.start for n in out]))):
        return []
    return out


RESTRIKE_GAP = 0.5    # s: the same key struck again this soon after a tune note ends continues it


def _hold_restruck(line: list[Note], ns: list[Note]) -> None:
    """Hold tune notes (in place) while the same key is struck again under
    them before the next tune note (`ns`: all notes, sorted): Satie's F#4
    lasts four bars while each bar's chord strikes F#4 again, which ends the
    transcribed note (with the pedal down it sounds on)."""
    by_pitch: dict[int, list[Note]] = {}
    for n in ns:
        by_pitch.setdefault(n.pitch, []).append(n)
    starts = {p: [n.start for n in v] for p, v in by_pitch.items()}
    for k, a in enumerate(line):
        limit = line[k + 1].start if k + 1 < len(line) else float("inf")
        same = by_pitch.get(a.pitch, [])
        end = a.end
        for n in same[bisect_right(starts.get(a.pitch, []), a.start + SAME_ONSET):]:
            if n.start >= limit - SAME_ONSET or n.start > end + RESTRIKE_GAP:
                break
            end = max(end, n.end)
        a.end = min(end, limit)


# fill_tune: the tune's softer notes between the notes the pianist voiced. The
# scores are log-odds of "this note is the tune", fitted on the notes of 11
# reference recordings (bench/REFERENCES.md) and rounded; each says something
# a melody does (docs/notation_rules.md R-THEORY-6: passing and neighbour notes
# move by step).
TUNE_GAP = 6.0        # s: longest gap between voiced tune notes that is searched (a slow
                      # tune voices a note only every bar: Clair de lune)
TUNE_RANGE = 7        # semitones: candidates reach this far under the tune's register ...
TUNE_SOFT = 15        # ... and are at most this much softer than the voiced tune around them
TUNE_AROUND = 3.0     # s each side: "the tune around them"
TUNE_BIAS = 0.4       # a top note in the tune's register, all else even, is more likely tune
TUNE_LOUD = 0.05      # per velocity unit over the notes struck within VOICING_SPAN of it
TUNE_STEP = 0.5       # moving by step (1-2 semitones) from the line note before
TUNE_SAME = -1.3      # the same key again: a restrike under the pedal, rarely the tune
TUNE_LEAP = -1.0      # a leap of a 4th to a minor 6th ...
TUNE_BIG_LEAP = -2.0  # ... a minor 6th or more
TUNE_HELD = -1.2      # struck while the line note before sounds on for TUNE_HELD_S or
TUNE_HELD_S = 0.3     #   more: accompaniment under a held tune note
TUNE_LONG_GAP = -0.9  # in a gap of over a second: the tune rests or is held there
TUNE_BUSY = -1.2      # TUNE_BUSY_N or more candidates in one gap: a running accompaniment
TUNE_BUSY_N = 6
TUNE_NEIGHBOUR_SOFT = 5   # velocity: between two voiced notes of one pitch, a note at most
                          # this much softer than both (else: accompaniment, Moonlight's triplets)


def _interval_score(d: int) -> float:
    d = abs(d)
    if d == 0:
        return TUNE_SAME
    if d < 3:
        return TUNE_STEP
    if d >= 8:
        return TUNE_BIG_LEAP
    if d >= 5:
        return TUNE_LEAP
    return 0.0


def fill_tune(line: list[Note], notes: list[Note]) -> list[Note]:
    """The voiced tune (piano_melody) with the softer tune notes between its notes.

    projected_line keeps only the notes the pianist played clearly louder;
    passing notes, the second half of a figure and quick runs drop out, and
    gaps under FILL_GAP are not searched at all (K.545's 16th scales, Für
    Elise's E-D#-E-B). In every gap of up to TUNE_GAP between line notes a and
    b, the candidates are the top note struck at each moment (overtones the
    model heard and much softer notes don't count, as in _figure_notes) that
    sit no more than TUNE_RANGE under the tune's register and are at most
    TUNE_SOFT softer than the tune around them. Through them the likeliest
    path a -> ... -> b is chosen by dynamic programming (Viterbi), each note
    scored as tune-like (TUNE_BIAS, louder than its surroundings, a short and
    quiet gap) and each move by its interval (steps cheap, restrikes and wide
    leaps dear), and a note struck while the one before still sounds is
    taken for accompaniment. Between two voiced notes of the same pitch only
    notes played nearly as loud count (TUNE_NEIGHBOUR_SOFT): a neighbour note
    of the tune, not the accompaniment under a repeated note. Lines from
    top_voice (no voicing) are returned as they are. Returns new Note
    objects, each cut at the next.
    """
    if not line or projected_line(notes) is None:
        return [Note(n.pitch, n.start, n.end, n.velocity) for n in line]
    ns = sorted(notes, key=lambda n: (n.start, -n.pitch))
    struck_end = {(n.pitch, round(n.start, 3)): n.end for n in ns}
    tops = _cluster_tops(ns)
    ts = np.array([n.start for n in tops])
    all_s = np.array([n.start for n in ns])
    all_v = np.array([n.velocity for n in ns], dtype=float)
    lp = np.array([n.pitch for n in line], dtype=float)
    lv = np.array([n.velocity for n in line], dtype=float)
    ls = np.array([n.start for n in line])
    out = [Note(n.pitch, n.start, n.end, n.velocity) for n in line]
    for a, b in zip(line, line[1:]):
        if b.start - a.start > TUNE_GAP:
            continue
        lo = int(np.searchsorted(ts, a.start + SAME_ONSET))
        hi = int(np.searchsorted(ts, b.start - SAME_ONSET))
        if hi <= lo:
            continue
        near = np.abs(ls - a.start) <= TUNE_AROUND
        floor = max(min(a.pitch, b.pitch), float(np.median(lp[near]))) - TUNE_RANGE
        soft = float(np.median(lv[near])) - TUNE_SOFT
        if a.pitch == b.pitch:
            # a neighbour note between two voiced notes of one pitch is played like
            # them; a softer one is the accompaniment under a repeated tune note
            soft = max(soft, min(a.velocity, b.velocity) - TUNE_NEIGHBOUR_SOFT)
        cand = [n for n in tops[lo:hi] if n.pitch >= floor and n.velocity >= soft]
        if not cand:
            continue
        around = [float(np.median(all_v[np.searchsorted(all_s, c.start - VOICING_SPAN):
                                         np.searchsorted(all_s, c.start + VOICING_SPAN)]))
                  for c in cand]
        a_end = struck_end.get((a.pitch, round(a.start, 3)), a.end)
        out += _tune_path(a, b, cand, a_end, around)
    out.sort(key=lambda n: n.start)
    for x, y in zip(out, out[1:]):
        x.end = min(x.end, y.start)
    return [n for n in out if n.end > n.start]


def _cluster_tops(ns: list[Note]) -> list[Note]:
    """The highest note struck at each moment (notes within SAME_ONSET), leaving
    out overtones the model heard and notes FIGURE_SOFT softer than the loudest."""
    ghost = _overtones(ns)
    tops = []
    i = 0
    while i < len(ns):
        j = i + 1
        while j < len(ns) and ns[j].start - ns[i].start < SAME_ONSET:
            j += 1
        group = [k for k in range(i, j) if k not in ghost]
        if group:
            loudest = max(ns[k].velocity for k in group)
            group = [k for k in group if ns[k].velocity > loudest - FIGURE_SOFT]
            tops.append(ns[max(group, key=lambda k: ns[k].pitch)])
        i = j
    return tops


def _tune_path(a: Note, b: Note, cand: list[Note], a_end: float, around: list[float]) -> list[Note]:
    """The likeliest run of `cand` (in time order) between tune notes a and b
    (Viterbi over which candidate comes right before which); [] if going
    straight from a to b scores best. a_end: when a's key was let go."""
    k = len(cand)
    context = (TUNE_BIAS + (TUNE_LONG_GAP if b.start - a.start > 1.0 else 0.0)
               + (TUNE_BUSY if k >= TUNE_BUSY_N else 0.0))
    seq = [a] + cand
    ends = [a_end] + [c.end for c in cand]
    best = [0.0] + [-np.inf] * k
    back = [-1] * (k + 1)
    for i in range(1, k + 1):
        c = seq[i]
        node = context + TUNE_LOUD * (c.velocity - around[i - 1])
        for j in range(i):
            if seq[j].start >= c.start - SAME_ONSET:
                continue
            held = TUNE_HELD if ends[j] - c.start > TUNE_HELD_S else 0.0
            v = best[j] + _interval_score(c.pitch - seq[j].pitch) + node + held
            if v > best[i]:
                best[i], back[i] = v, j
    # a and b are both tune notes: a repeated note between them is the tune's own
    # (Moonlight's dotted G#-G#), not a restrike to be avoided by adding notes
    direct = 0.0 if a.pitch == b.pitch else _interval_score(b.pitch - a.pitch)
    final = [direct] + [best[i] + _interval_score(b.pitch - seq[i].pitch) for i in range(1, k + 1)]
    i = int(np.argmax(final))
    path = []
    while i > 0:
        path.append(seq[i])
        i = back[i]
    return [Note(n.pitch, n.start, n.end, n.velocity) for n in reversed(path)]


JOIN_GAP = 0.02       # s: a same-pitch note starting this soon after the last one ended ...
JOIN_BEATS = 1 / 3    # ... and less than this many beats after it started continues it ...
STRUCK_TOL = 0.05     # ... unless the piano model heard that key struck within this many s


def join_split_notes(line: list[Note], to_beats, struck: list[Note] = ()) -> list[Note]:
    """Rejoin held notes that the general transcription model split.

    On a bowed or blown note the model often hears one held note as two or
    three same-pitch notes back to back (the bow changes pressure, the
    vibrato swells). A real repeated note needs a new attack, and a tune
    rarely repeats a note faster than a third of a beat; so a note of the
    same pitch starting within JOIN_GAP of the previous one's end and less
    than JOIN_BEATS (`to_beats`: seconds -> beats) after its start is the
    same note, unless a note of `struck` (the piano model's notes: it hears
    attacks, a piano's or a sharp bow stroke) starts with it, which makes it
    a real repeated note. Piano transcription hears every key strike, so
    this is not used on piano lines. Returns new Note objects.
    """
    attacks: dict[int, list[float]] = {}
    for n in struck:
        attacks.setdefault(n.pitch, []).append(n.start)
    for v in attacks.values():
        v.sort()

    def attacked(n: Note) -> bool:
        ts = attacks.get(n.pitch, [])
        i = bisect_right(ts, n.start - STRUCK_TOL)
        return i < len(ts) and ts[i] <= n.start + STRUCK_TOL

    out: list[Note] = []
    for n in sorted(line, key=lambda n: n.start):
        prev = out[-1] if out else None
        if (prev is not None and prev.pitch == n.pitch and n.start - prev.end < JOIN_GAP
                and to_beats(n.start) - to_beats(prev.start) < JOIN_BEATS and not attacked(n)):
            prev.end = max(prev.end, n.end)
            prev.velocity = max(prev.velocity, n.velocity)
            continue
        out.append(Note(n.pitch, n.start, n.end, n.velocity))
    return out


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
