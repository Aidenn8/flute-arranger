"""Notation rules (docs/notation_rules.md): check an arrangement as it will be printed, and tidy it.

lint(arr, score=None) -> list[Issue]
    Checks the score the arrangement makes (built here, or the one passed in,
    e.g. read back from MusicXML) against the rulebook: how notes and rests are
    split and tied at beats (R-METER, R-REST, R-TIE), beam groups (R-BEAM),
    which accidentals are printed (R-ACC), the pickup bar and final barline
    (R-PAGE); and the flute line itself: breathing (R-BREATH), phrase ends
    (R-PHR), slurs, dynamics and ornaments against the flute's registers
    (R-MARK, R-FLUTE, R-RANGE). Each Issue names its rule and printed bar.
    "error" issues break a rule of writing; "advice" ones are worth a look.

tidy(arr) -> Arrangement
    The safe fixes, applied before the score is written (score.write_outputs).
    Pitches and note starts never change. It
    - holds a phrase's last note to the end of its beat when a rest of at
      least a beat follows, instead of leaving a rest fragment inside the beat
      (R-PHR-3, R-REST-6); staccato notes keep their length;
    - adds breath marks so no stretch of continuous playing runs past
      BREATH_LIMIT seconds, at the best place in it (R-BREATH-2, R-BREATH-3);
    - ends slurs at breath marks and rests (R-PHR-4).
    The input is not changed. tidy_report(arr) also says what was done.

Everything about how values are split, beamed and spelled is done while
engraving (score.py); lint checks the result independently.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from pathlib import Path

from .checks import bar_ticks, lead_in_ticks, pickup_start, printed_bar
from .config import FLUTE_HARD, TICKS_PER_BEAT as T
from .model import QUARTER_BEAT, Arrangement, Mark, QNote

ERROR, ADVICE, FIXED = "error", "advice", "fixed"   # Issue levels ("fixed": what tidy did)

BREATH_LIMIT = 10.0   # s of continuous playing a flutist can manage (R-BREATH-2)
BREATH_AIM = 8.0      # s: where tidy aims to put a missing breath ...
BREATH_SOONEST = 4.0  # s: ... and never sooner than this after the last one
GAP = 6               # ticks: a rest of an 8th or more is a place to breathe (R-BREATH-5)
GAP_SECONDS = 0.35    # ... and so is any rest at least this long
HIGH = 93             # A6: soft playing gets hard, 8va territory (R-FLUTE-1, R-RANGE-3)
LOW = 67              # G4: below it the flute is weak (R-FLUTE-1)
REPORT = "notation.json"


@dataclass(frozen=True)
class Issue:
    rule: str          # rule ID in docs/notation_rules.md, e.g. "R-METER-3"
    bar: int           # printed bar number (a pickup bar is 0)
    message: str
    level: str = ERROR

    def __str__(self) -> str:
        return f"bar {self.bar}: {self.message} ({self.rule})"


# ---------------------------------------------------------------- meter helpers


def _unit(arr: Arrangement) -> int:
    """The beat as the page shows it: a quarter, or a dotted quarter (6/8, 9/8,
    12/8 and 3/8, which is one dotted beat a bar however it is counted)."""
    return QUARTER_BEAT if arr.beat_ticks == QUARTER_BEAT else 18


def _seconds_per_tick(arr: Arrangement) -> float:
    bpm = arr.bpm if arr.bpm and arr.bpm > 0 else 100.0
    return 60.0 / bpm / arr.beat_ticks


SIMPLE_VALUES = {3, 6, 9, 12, 18, 24, 36, 48}       # 16th ... whole, dotted values
COMPOUND_VALUES = {3, 6, 9, 12, 18, 36, 72}         # no half or whole notes in 6/8-12/8
TRIPLET_VALUES = {4, 8}
# inside a dotted-quarter beat: offset -> lengths that may start there (R-METER-7)
IN_DOTTED_BEAT = {0: (18, 12, 9, 6, 3), 3: (9, 6, 3), 6: (12, 6, 3), 9: (9, 6, 3),
                  12: (6, 3), 15: (3,)}
# inside a quarter beat: length -> offsets it may start on (R-METER-5)
IN_QUARTER_BEAT = {12: (0,), 9: (0, 3), 6: (0, 3, 6), 3: (0, 3, 6, 9), 8: (0, 4), 4: (0, 4, 8)}


def note_rule(pos: int, dur: int, bpb: int, unit: int) -> str | None:
    """None if a written note of `dur` ticks may start `pos` ticks into a bar,
    else the rule it breaks (R-METER-2..7, R-METER-10)."""
    bar = bpb * unit
    end = pos + dur
    if end > bar:
        return "R-METER-1"
    if unit == QUARTER_BEAT:
        if dur not in SIMPLE_VALUES | TRIPLET_VALUES:
            return "R-METER-10"
        off = pos % unit
        if pos // unit == (end - 1) // unit:                 # inside one beat
            return None if off in IN_QUARTER_BEAT.get(dur, ()) else "R-METER-5"
        if off == 0 and dur in (12, 18, 24, 36, 48):
            if bpb == 4 and pos < 24 < end:                  # across the middle of 4/4
                ok = (pos, dur) in {(0, 48), (0, 36), (12, 36), (12, 24), (12, 18)}
                return None if ok else "R-METER-3"
            return None if dur != 48 or pos == 0 else "R-METER-2"
        if off == 6 and dur in (12, 18):                     # 8th-quarter-8th syncopation
            half = 24 if bpb == 4 else bar
            if pos // half == (end - 1) // half:
                return None
            return "R-METER-3" if bpb == 4 else "R-METER-4"
        if bpb == 4 and pos < 24 < end:
            return "R-METER-3"
        return "R-METER-4"
    if dur not in COMPOUND_VALUES:
        return "R-METER-10"
    off = pos % unit
    if pos // unit == (end - 1) // unit:
        return None if dur in IN_DOTTED_BEAT.get(off, ()) else "R-METER-7"
    if off == 0 and dur in (36, 72) and (dur != 72 or pos == 0):
        return None
    return "R-METER-7"


def rest_rule(pos: int, dur: int, bpb: int, unit: int) -> str | None:
    """None if a written rest of `dur` ticks may start at `pos` (R-REST-2..5)."""
    bar = bpb * unit
    if dur == bar and pos == 0:
        return None                                          # a whole-bar rest
    off = pos % unit
    crosses = pos // unit != (pos + dur - 1) // unit
    if unit == QUARTER_BEAT:
        if dur in (9, 18, 36):
            return "R-REST-3"
        if dur == 24:
            return None if bpb == 4 and pos in (0, 24) else "R-REST-2"
        if crosses:
            return "R-REST-2"
        if dur in TRIPLET_VALUES:
            return None if off % 4 == 0 and off + dur <= unit else "R-REST-4"
        if dur == 12:
            return None if off == 0 else "R-REST-2"
        if dur == 6:
            return None if off in (0, 6) else "R-REST-4"
        return None if dur == 3 else "R-REST-4"
    if dur == 36:
        return None if bar == 72 and pos in (0, 36) else "R-REST-5"
    if crosses:
        return "R-REST-5"
    if dur == 18:
        return None if off == 0 else "R-REST-5"
    if dur == 12:
        return None if off == 0 else "R-REST-5"
    if dur == 6:
        return None if off % 6 == 0 else "R-REST-5"
    return None if dur == 3 else "R-REST-5"


# ---------------------------------------------------------------- written-score checks

_NAMES = {"sharp": "#", "flat": "b", "natural": "", "double-sharp": "x", "double-flat": "bb"}


def _pname(p) -> str:
    acc = _NAMES.get(p.accidental.name, "") if p.accidental is not None else ""
    return f"{p.step}{acc}{p.octave}"


def _elements(m):
    """(pos ticks into the full bar, length ticks, element) for a measure's notes and rests."""
    from music21 import stream
    pad = round(Fraction(m.paddingLeft) * T)
    out = []
    for el in m.recurse().notesAndRests:
        if el.duration.isGrace:
            continue
        ctx = el.getContextByClass(stream.Measure)
        if ctx is not m:
            continue
        out.append((round(Fraction(el.offset) * T) + pad, round(Fraction(el.quarterLength) * T), el))
    return sorted(out, key=lambda x: x[0])


def _written_checks(score, arr: Arrangement) -> list[Issue]:
    from music21 import stream
    issues: list[Issue] = []
    bpb, unit = arr.bar_ticks // _unit(arr), _unit(arr)
    bar = arr.bar_ticks
    lead = lead_in_ticks(arr)
    parts = list(score.parts)
    for k, part in enumerate(parts):
        who = "" if k == 0 else " (piano)"
        measures = list(part.getElementsByClass(stream.Measure))
        if not measures:
            continue
        ks = measures[0].keySignature
        key_alter = {}
        if ks is not None:
            for p in ks.alteredPitches:
                key_alter[p.step] = int(p.alter)
        prev_state: dict[tuple[str, int], int] = {}
        for m in measures:
            num = m.number
            els = _elements(m)
            pad = round(Fraction(m.paddingLeft) * T)
            total = sum(d for _, d, _ in els)
            full_rest = len(els) == 1 and els[0][2].isRest and (
                getattr(els[0][2], "fullMeasure", False) in (True, "always"))
            if not full_rest and total != bar - pad:
                issues.append(Issue("R-METER-1", num, f"the bar holds {Fraction(total, unit)} "
                                    f"beats instead of {Fraction(bar - pad, unit)}{who}"))
            # rhythm of each written value
            for pos, dur, el in els:
                if el.isRest:
                    if full_rest:
                        continue
                    rule = rest_rule(pos, dur, bpb, unit)
                    if rule:
                        issues.append(Issue(rule, num, f"a {el.duration.fullName.lower()} rest "
                                            f"hides the beat{who}"))
                else:
                    rule = note_rule(pos, dur, bpb, unit)
                    if rule:
                        issues.append(Issue(rule, num, f"a {el.duration.fullName.lower()} "
                                            f"hides the beat{who}"))
            # needless ties inside the bar (R-TIE-1)
            for (p1, d1, a), (p2, d2, b) in zip(els, els[1:]):
                if a.isRest or b.isRest or a.tie is None or b.tie is None:
                    continue
                if a.tie.type not in ("start", "continue") or b.tie.type not in ("stop", "continue"):
                    continue
                if a.duration.tuplets or b.duration.tuplets:
                    continue
                values = SIMPLE_VALUES if unit == QUARTER_BEAT else COMPOUND_VALUES
                if d1 + d2 in values and note_rule(p1, d1 + d2, bpb, unit) is None:
                    issues.append(Issue("R-TIE-1", num, f"two tied notes could be one "
                                        f"{_value_name(d1 + d2)}{who}"))
            issues += _beam_checks(els, num, bpb, unit, who)
            # accidentals (R-ACC-1, R-ACC-3, R-ACC-6, R-ACC-7)
            state: dict[tuple[str, int], int] = {}
            seen_letters: set[str] = set()
            names_by_midi: dict[int, set[str]] = {}
            for _, _, el in els:
                if el.isRest:
                    continue
                tied_on = el.tie is not None and el.tie.type in ("stop", "continue")
                for p in el.pitches:
                    alter = int(p.alter)
                    shown = p.accidental is not None and p.accidental.displayStatus is True \
                        and p.accidental.displayType != "never"
                    names_by_midi.setdefault(p.midi, set()).add(_pname(p))
                    if abs(alter) == 2 and abs(arr.key_fifths) <= 4:
                        issues.append(Issue("R-ACC-7", num, f"double accidental on {_pname(p)}"
                                            f"{who}"))
                    if tied_on:
                        continue
                    now = state.get((p.step, p.octave), key_alter.get(p.step, 0))
                    if alter != now and not shown:
                        issues.append(Issue("R-ACC-1", num, f"{_pname(p)} needs its accidental{who}"))
                    elif not shown:
                        if p.step not in seen_letters and needs_courtesy(
                                p.step, p.octave, alter, prev_state, key_alter.get(p.step, 0)):
                            issues.append(Issue("R-ACC-3", num, f"{_pname(p)} after the bar "
                                                f"line needs a courtesy accidental{who}", ADVICE))
                        other = [a for (s, o), a in state.items() if s == p.step and o != p.octave]
                        if other and any(a != alter for a in other):
                            issues.append(Issue("R-ACC-3", num, f"{_pname(p)} needs a courtesy "
                                                f"accidental (another octave differs){who}",
                                                ADVICE))
                    if shown or alter != now:
                        state[(p.step, p.octave)] = alter
                    seen_letters.add(p.step)
            for midi, names in names_by_midi.items():
                if len(names) > 1:
                    issues.append(Issue("R-ACC-6", num, f"one pitch spelled two ways "
                                        f"({' and '.join(sorted(names))}){who}"))
            prev_state = state
        last = measures[-1]
        if last.rightBarline is None or last.rightBarline.type != "final":
            issues.append(Issue("R-PAGE-4", last.number, f"the last bar needs a final barline{who}"))
    # the pickup (R-PAGE-5): a lead-in bar that is full of rests before its notes
    if lead and parts:
        firsts = [list(p.getElementsByClass(stream.Measure))[:1] for p in parts]
        firsts = [f[0] for f in firsts if f]
        if firsts and all(Fraction(f.paddingLeft) == 0 for f in firsts):
            opening = [_elements(f) for f in firsts]
            if all(e and e[0][2].isRest for e in opening) and any(
                    any(not x[2].isRest for x in e) for e in opening):
                issues.append(Issue("R-PAGE-5", firsts[0].number,
                                    "the pickup is written as a full bar starting with rests"))
    return issues


def needs_courtesy(step: str, octave: int, alter: int, prev: dict, key: int) -> bool:
    """R-ACC-3: the first note of a letter after a bar line, back to the key
    signature, when the bar before altered it: in the same octave if that
    octave was played there, else in any octave."""
    if (step, octave) in prev:
        before = prev[(step, octave)]
        return before != key and alter != before
    return any(a != key and a != alter for (s, _), a in prev.items() if s == step)


def _value_name(ticks: int) -> str:
    return {3: "16th", 6: "8th", 9: "dotted 8th", 12: "quarter", 18: "dotted quarter",
            24: "half note", 36: "dotted half", 48: "whole note", 72: "dotted whole"}.get(
        ticks, f"{ticks}-tick note")


def _beam_unit(pos: int, bpb: int, unit: int) -> int:
    """Which beam group a position belongs to: its quarter beat (R-BEAM-1) or
    dotted-quarter beat (R-BEAM-3; 3/8 is one dotted beat a bar, R-BEAM-4)."""
    return pos // unit


def _beam_checks(els, num: int, bpb: int, unit: int, who: str) -> list[Issue]:
    issues: list[Issue] = []
    group: list[tuple[int, int, object]] = []
    for pos, dur, el in els:
        types = el.beams.getTypes() if not el.isRest else []
        if not types:
            group = []
            continue
        group.append((pos, dur, el))
        if types[0] != "stop":
            continue
        units = {_beam_unit(p, bpb, unit) for p, _, _ in group}
        if len(units) > 1:
            plain = all(d == 6 and not e.duration.tuplets for _, d, e in group)
            halves = {p // 24 for p, _, _ in group}
            if unit == QUARTER_BEAT and bpb == 4 and plain and len(halves) == 1:
                pass                                  # four 8ths over a half bar (R-BEAM-2)
            elif unit == QUARTER_BEAT and bpb == 4 and len({p // 24 for p, _, _ in group}) > 1:
                issues.append(Issue("R-BEAM-2", num, f"a beam crosses the middle of the bar{who}"))
            else:
                rule = "R-BEAM-1" if unit == QUARTER_BEAT else "R-BEAM-3"
                issues.append(Issue(rule, num, f"a beam crosses a beat{who}"))
        # broken 16th beams inside one beat (R-BEAM-5)
        for (p1, _, a), (p2, _, b) in zip(group, group[1:]):
            ta, tb = a.beams.getTypes(), b.beams.getTypes()
            if (len(ta) > 1 and len(tb) > 1 and ta[1] == "stop" and tb[1] == "start"
                    and p1 // unit == p2 // unit):
                issues.append(Issue("R-BEAM-5", num, f"the 16th beam is broken inside a beat{who}",
                                    ADVICE))
        group = []
    # 8ths and 16ths of one beat left unbeamed
    notes = [(p, d, e) for p, d, e in els if not e.isRest and d < 12 and e.duration.type != "quarter"]
    for (p1, d1, a), (p2, d2, b) in zip(notes, notes[1:]):
        if p1 + d1 == p2 and _beam_unit(p1, bpb, unit) == _beam_unit(p2, bpb, unit) \
                and not a.beams.getTypes() and not b.beams.getTypes():
            rule = "R-BEAM-1" if unit == QUARTER_BEAT else "R-BEAM-3"
            issues.append(Issue(rule, num, f"notes of one beat are not beamed together{who}"))
    return issues


# ---------------------------------------------------------------- the flute line (ticks)


def _runs(line: list[QNote], arr: Arrangement) -> list[list[int]]:
    """Stretches of continuous playing: split at rests long enough to breathe and at breath marks."""
    spt = _seconds_per_tick(arr)
    out, cur = [], []
    for i, q in enumerate(line):
        cur.append(i)
        nxt = line[i + 1] if i + 1 < len(line) else None
        if nxt is None or q.breath_after or _breathing_gap(nxt.start - q.end, spt):
            out.append(cur)
            cur = []
    return out


def _breathing_gap(ticks: int, spt: float) -> bool:
    return ticks >= GAP or (ticks > 0 and ticks * spt >= GAP_SECONDS)


def _line(arr: Arrangement) -> list[QNote]:
    return sorted((q for q in arr.flute if q.dur > 0), key=lambda q: (q.start, q.pitch))


def _line_checks(arr: Arrangement) -> list[Issue]:
    issues: list[Issue] = []
    line = _line(arr)
    if not line:
        return issues
    spt = _seconds_per_tick(arr)
    bar = lambda t: printed_bar(arr, t)          # noqa: E731

    lo, hi = FLUTE_HARD
    for q in line:
        if not lo <= q.pitch <= hi:
            issues.append(Issue("R-RANGE-1", bar(q.start), f"note {_pitch_name(q.pitch)} is "
                                f"outside the flute's range"))
    for a, b in zip(line, line[1:]):
        if a.end > b.start:
            issues.append(Issue("R-FLUTE-4", bar(a.start), "two notes overlap (the flute "
                                "plays one note at a time)"))
    run = 0
    for i, q in enumerate(line):                 # 8va territory: four or more notes from A6 up
        run = run + 1 if q.pitch >= HIGH else 0
        if run == 4:
            issues.append(Issue("R-RANGE-3", bar(line[i - 3].start), "a passage above A6 "
                                "(an 8va sign would help)", ADVICE))

    for idx in _runs(line, arr):                 # breathing (R-BREATH-2)
        last = line[idx[0]].start
        for i in idx:
            q = line[i]
            if q.dur * spt > BREATH_LIMIT:
                issues.append(Issue("R-BREATH-2", bar(q.start), f"one note is held "
                                    f"{q.dur * spt:.0f} s, longer than a breath lasts", ADVICE))
                last = q.end
            elif (q.end - last) * spt > BREATH_LIMIT:
                issues.append(Issue("R-BREATH-2", bar(q.start), f"over {BREATH_LIMIT:.0f} s of "
                                    "playing without a breath"))
                last = q.end
    for i in range(len(line) - 1):               # breath inside a fast run (R-BREATH-3)
        a, b = line[i], line[i + 1]
        if a.breath_after and b.start - a.end < GAP and a.dur < 6 and b.dur < 6:
            issues.append(Issue("R-BREATH-3", bar(a.start), "a breath inside a run of fast notes",
                                ADVICE))

    unit = _unit(arr)                             # phrase ends left inside a beat (R-PHR-3)
    stacc = {m.start for m in arr.expression if m.kind == "staccato"}
    for i, q in enumerate(line):
        nxt = line[i + 1].start if i + 1 < len(line) else None
        if _holdable(q, nxt, unit, stacc):
            issues.append(Issue("R-PHR-3", bar(q.start), "a phrase ends with a rest inside the "
                                "beat (hold the last note to the beat)", ADVICE))

    issues += _mark_checks(arr, line)
    return issues


def _pitch_name(p: int) -> str:
    return ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"][p % 12] + str(p // 12 - 1)


def _mark_checks(arr: Arrangement, line: list[QNote]) -> list[Issue]:
    issues: list[Issue] = []
    marks = arr.expression
    if not marks:
        return issues
    bar = lambda t: printed_bar(arr, t)          # noqa: E731
    starts = {q.start: q for q in line}
    spt = _seconds_per_tick(arr)
    dyns = sorted((m for m in marks if m.kind == "dynamic"), key=lambda m: m.start)
    if dyns and dyns[0].start > line[0].start + 3:
        issues.append(Issue("R-MARK-1", bar(line[0].start), "the first note has no dynamic",
                            ADVICE))
    for a, b in zip(dyns, dyns[1:]):
        if a.start == b.start and a.value != b.value:
            issues.append(Issue("R-MARK-1", bar(a.start), "two dynamics on one note"))
    for m in marks:
        if m.kind in ("cresc", "dim"):
            if m.end - m.start < arr.beat_ticks:
                issues.append(Issue("R-MARK-5", bar(m.start), "a hairpin shorter than a beat",
                                    ADVICE))
            if any(m.start < d.start < m.end - 3 for d in dyns):
                issues.append(Issue("R-MARK-5", bar(m.start), "a hairpin runs past the next "
                                    "dynamic", ADVICE))
        elif m.kind == "trill":
            q = starts.get(m.start)
            if q is not None and (q.pitch <= 62 or q.pitch >= HIGH):
                issues.append(Issue("R-FLUTE-3", bar(m.start), f"a trill on {_pitch_name(q.pitch)} "
                                    "is awkward on the flute", ADVICE))
        elif m.kind == "slur":
            inside = [q for q in line if m.start <= q.start <= m.end]
            if len(inside) >= 2:
                if any(a.breath_after or b.start > a.end for a, b in zip(inside, inside[1:])):
                    issues.append(Issue("R-PHR-4", bar(m.start), "a slur runs over a rest or a "
                                        "breath mark"))
                elif len({q.pitch for q in inside}) == 1:
                    issues.append(Issue("R-PHR-4", bar(m.start), "a slur over repeated notes "
                                        "only", ADVICE))
                if (inside[-1].end - inside[0].start) * spt > BREATH_LIMIT:
                    issues.append(Issue("R-PHR-5", bar(m.start), "a slur longer than one breath",
                                        ADVICE))
    for i, d in enumerate(dyns):                  # dynamics against the register (R-FLUTE-2)
        end = dyns[i + 1].start if i + 1 < len(dyns) else float("inf")
        notes = [q.pitch for q in line if d.start <= q.start < end]
        if not notes:
            continue
        mid = sorted(notes)[len(notes) // 2]
        if d.value in ("f", "ff") and mid < LOW:
            issues.append(Issue("R-FLUTE-2", bar(d.start), f"{d.value} in the flute's low "
                                "register will not carry", ADVICE))
        if d.value in ("pp", "p") and max(notes) >= HIGH:
            issues.append(Issue("R-FLUTE-2", bar(d.start), f"{d.value} on notes above A6 is "
                                "hard to play", ADVICE))
    return issues


# ---------------------------------------------------------------- lint


def lint(arr: Arrangement, score=None) -> list[Issue]:
    """Rule violations in the score this arrangement makes (or in `score`, a
    music21 Score of it), errors first, then by bar."""
    if score is None:
        from .score import build_score
        score = build_score(arr, "")
    issues = _written_checks(score, arr) + _line_checks(arr)
    seen, out = set(), []
    for i in issues:
        if (i.rule, i.bar, i.message) not in seen:
            seen.add((i.rule, i.bar, i.message))
            out.append(i)
    return sorted(out, key=lambda i: (i.level != ERROR, i.bar, i.rule))


def summary(issues: list[Issue], top: int = 3) -> dict:
    """For the song page: a headline and the first few issues (errors first),
    e.g. "follows standard notation", or "2 things to fix" and "bar 12: ..."."""
    errors = [i for i in issues if i.level == ERROR]
    if errors:
        head = f"{len(errors)} thing{'s' if len(errors) != 1 else ''} to fix"
        label = ""
    else:
        head = "follows standard notation"
        label = "Worth a look" if issues else ""
    return {"headline": head, "label": label, "top": [str(i) for i in issues[:top]],
            "more": max(0, len(issues) - top), "errors": len(errors),
            "advice": len(issues) - len(errors)}


# ---------------------------------------------------------------- tidy


def _holdable(q: QNote, nxt: int | None, unit: int, staccato: set[int]) -> bool:
    """A phrase's last note that stops inside its beat, with at least a beat
    (at most a quarter: two 8ths of a dotted beat) of silence after that beat:
    it can be held to the beat (R-PHR-3)."""
    if q.start in staccato or q.end % unit == 0:
        return False
    target = q.end - q.end % unit + unit
    return nxt is None or nxt - target >= min(unit, QUARTER_BEAT)


def _hold_phrase_ends(line: list[QNote], arr: Arrangement, fixes: list[Issue]) -> None:
    unit = _unit(arr)
    stacc = {m.start for m in arr.expression if m.kind == "staccato"}
    for i, q in enumerate(line):
        nxt = line[i + 1].start if i + 1 < len(line) else None
        if _holdable(q, nxt, unit, stacc):
            q.dur = q.end - q.end % unit + unit - q.start
            fixes.append(Issue("R-PHR-3", printed_bar(arr, q.start),
                               "held the phrase's last note to the end of its beat", FIXED))


def _breath_score(i: int, line: list[QNote], arr: Arrangement, aim: float, spt: float) -> float:
    """How good a place the end of note i is for a breath (R-BREATH-3)."""
    from .expression import tonic_pc
    q, nxt = line[i], line[i + 1]
    unit, bar = _unit(arr), bar_ticks(arr)
    s = min(q.dur / unit, 4.0)                               # after a long note
    if q.end % bar == 0:
        s += 2.0                                             # at a bar line
    elif arr.beats_per_bar == 4 and q.end % (bar // 2) == 0:
        s += 1.0                                             # at the half bar
    elif q.end % unit == 0:
        s += 0.5
    fast = lambda n: n.dur < 6 or n.dur * spt < 0.2         # noqa: E731
    if fast(q) and fast(nxt):
        s -= 3.0                                             # inside a fast run
    tonic = tonic_pc(arr.key_fifths, arr.key_mode)
    if nxt.pitch - q.pitch == 1 and nxt.pitch % 12 == tonic:
        s -= 3.0                                             # leading tone -> tonic
    if nxt.start % bar == 0 and q.dur < unit and q.start % bar >= bar - unit:
        s -= 2.0                                             # an upbeat into its downbeat
    s -= 0.3 * abs(q.end * spt - aim)                        # near the aim
    return s


def _add_breaths(line: list[QNote], arr: Arrangement, fixes: list[Issue]) -> None:
    spt = _seconds_per_tick(arr)
    for idx in _runs(line, arr):
        last = line[idx[0]].start * spt
        end = line[idx[-1]].end * spt
        while end - last > BREATH_LIMIT:
            # not too soon after the last breath, nor just before the next one
            lo, hi = last + BREATH_SOONEST, min(last + BREATH_LIMIT, end - BREATH_SOONEST)
            cands = [i for i in idx[:-1] if lo <= line[i].end * spt <= hi]
            if not cands:
                cands = [i for i in idx[:-1] if last < line[i].end * spt <= last + BREATH_LIMIT]
            if not cands:
                break                                        # one very long note: nothing to do
            best = max(cands, key=lambda i: _breath_score(i, line, arr, last + BREATH_AIM, spt))
            line[best].breath_after = True
            fixes.append(Issue("R-BREATH-2", printed_bar(arr, line[best].start),
                               "added a breath mark", FIXED))
            last = line[best].end * spt


def _split_slurs(marks: list[Mark], line: list[QNote], fixes: list[Issue],
                 arr: Arrangement) -> list[Mark]:
    out: list[Mark] = []
    for m in marks:
        if m.kind != "slur":
            out.append(m)
            continue
        inside = [q for q in line if m.start <= q.start <= m.end]
        pieces, cur = [], []
        for k, q in enumerate(inside):
            cur.append(q)
            nxt = inside[k + 1] if k + 1 < len(inside) else None
            if nxt is not None and (q.breath_after or nxt.start > q.end):
                pieces.append(cur)
                cur = []
        pieces.append(cur)
        if len(pieces) == 1:
            out.append(m)
            continue
        fixes.append(Issue("R-PHR-4", printed_bar(arr, m.start),
                           "ended a slur at a breath or rest", FIXED))
        out += [Mark("slur", p[0].start, p[-1].start) for p in pieces if len(p) >= 2]
    return out


def tidy_report(arr: Arrangement) -> tuple[Arrangement, list[Issue]]:
    """tidy(arr) and the fixes it made (as Issues: rule, bar, what was done)."""
    fixes: list[Issue] = []
    line = [replace(q) for q in _line(arr)]
    rest = [replace(q) for q in arr.flute if q.dur <= 0]
    _hold_phrase_ends(line, arr, fixes)
    _add_breaths(line, arr, fixes)
    marks = _split_slurs(list(arr.expression), line, fixes, arr)
    return replace(arr, flute=line + rest, expression=marks, warnings=list(arr.warnings)), fixes


def tidy(arr: Arrangement) -> Arrangement:
    return tidy_report(arr)[0]


# ---------------------------------------------------------------- the report file


def write_report(out_dir: Path, issues: list[Issue], fixes: list[Issue]) -> Path:
    path = Path(out_dir) / REPORT
    data = {"summary": summary(issues), "issues": [asdict(i) for i in issues],
            "fixes": [asdict(f) for f in fixes]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return path


def read_report(out_dir: Path) -> dict | None:
    try:
        return json.loads((Path(out_dir) / REPORT).read_text())
    except (OSError, ValueError):
        return None


def report_for(out_dir: Path, arr: Arrangement | None) -> dict | None:
    """The notation report of an arrangement folder. Folders written before
    the check existed get one made once from their own score.musicxml (so it
    describes the page as it is); None if that can't be read."""
    rep = read_report(out_dir)
    xml = Path(out_dir) / "score.musicxml"
    if rep is not None or arr is None or not xml.is_file():
        return rep
    try:
        from music21 import converter
        issues = lint(arr, converter.parse(str(xml)))
    except Exception:                 # an unreadable or foreign score: no check shown
        return None
    write_report(out_dir, issues, [])
    return read_report(out_dir)
