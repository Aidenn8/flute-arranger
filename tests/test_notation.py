"""The notation rulebook (docs/notation_rules.md): score.py writes by it, notation.lint
checks it, notation.tidy applies its safe fixes. Test names cite the rule IDs."""
import json
import random
from fractions import Fraction

from music21 import converter, note, stream, tie

from sheetswap import notation
from sheetswap.checks import pickup_start, printed_bar
from sheetswap.model import Arrangement, Mark, QNote
from sheetswap.notation import Issue, lint, note_rule, rest_rule, tidy, tidy_report
from sheetswap.score import build_score, write_outputs

F = Fraction


def q(pitch, start, dur, **kw):
    return QNote(pitch, start, dur, **kw)


def bar(score, number, part=0):
    for m in score.parts[part].getElementsByClass(stream.Measure):
        if m.number == number:
            return m
    raise KeyError(number)


def shape(measure):
    """[(kind, quarterLength, tie type)] of a measure."""
    return [("r" if el.isRest else "n", F(el.quarterLength), el.tie.type if el.tie else None)
            for el in measure.notesAndRests]


def beam_groups(measure):
    """Beamed runs of a measure, as lists of quarterLengths."""
    groups, cur = [], []
    for el in measure.notesAndRests:
        types = el.beams.getTypes() if not el.isRest else []
        if not types:
            continue
        cur.append(F(el.quarterLength))
        if types[0] == "stop":
            groups.append(cur)
            cur = []
    return groups


def rules(issues):
    return {i.rule for i in issues}


def written(arr):
    return notation._written_checks(build_score(arr, "t"), arr)


# ---------------------------------------------------------------- meter (R-METER, R-TIE)


def test_r_meter_3_half_bar_rule_in_four_four():
    # beat 2 "and" lasting past beat 3 is tied at beat 3; quarter-half-quarter is not
    arr = Arrangement(flute=[q(72, 0, 18), q(74, 18, 18), q(76, 36, 12),
                             q(72, 48, 12), q(74, 60, 24), q(76, 84, 12)])
    s = build_score(arr, "t")
    assert shape(bar(s, 1)) == [("n", F(3, 2), None), ("n", F(1, 2), "start"),
                                ("n", F(1), "stop"), ("n", F(1), None)]
    assert shape(bar(s, 2)) == [("n", F(1), None), ("n", F(2), None), ("n", F(1), None)]
    assert not written(arr)
    # the rule itself: what may cross the middle of a 4/4 bar
    assert note_rule(12, 24, 4, 12) is None            # half on beat 2
    assert note_rule(12, 36, 4, 12) is None            # dotted half on beat 2
    assert note_rule(18, 12, 4, 12) == "R-METER-3"     # quarter on 2-and across beat 3
    assert note_rule(24, 24, 4, 12) is None            # half on beat 3
    assert note_rule(36, 24, 4, 12) == "R-METER-1"     # over the bar line


def test_r_meter_4_syncopation_inside_a_half_bar():
    assert note_rule(6, 12, 4, 12) is None             # 8th-quarter-8th in beats 1-2
    assert note_rule(30, 12, 4, 12) is None            # ... in beats 3-4
    assert note_rule(6, 12, 3, 12) is None and note_rule(18, 12, 3, 12) is None
    assert note_rule(3, 12, 2, 12) == "R-METER-4"      # off the 8th: tie at the beat
    assert note_rule(3, 9, 2, 12) is None              # 16th-dotted 8th inside a beat (R-METER-5)
    assert note_rule(6, 9, 2, 12) == "R-METER-4"


def test_r_meter_7_compound_meters_show_dotted_beats():
    assert note_rule(0, 36, 2, 18) is None             # dotted half fills 6/8
    assert note_rule(6, 12, 2, 18) is None             # 8th-quarter
    assert note_rule(12, 12, 2, 18) == "R-METER-7"     # quarter across the beat
    assert note_rule(0, 24, 2, 18) == "R-METER-10"     # no half notes in 6/8
    assert note_rule(0, 72, 4, 18) is None             # dotted whole: a 12/8 bar


def test_r_tie_1_needless_tie_is_found():
    m = stream.Measure(number=1)
    a, b = note.Note("C5", quarterLength=1), note.Note("C5", quarterLength=1)
    a.tie, b.tie = tie.Tie("start"), tie.Tie("stop")
    m.append([a, b, note.Note("D5", quarterLength=2)])
    p = stream.Part([m])
    s = stream.Score([p])
    issues = notation._written_checks(s, Arrangement(flute=[q(72, 0, 24), q(74, 24, 24)]))
    assert "R-TIE-1" in rules(issues)


# ---------------------------------------------------------------- rests (R-REST)


def test_r_rest_2_rests_group_by_beat_and_half_bar():
    # 4/4: a note on beat 1 then silence: quarter rest, half rest (beats 3-4)
    s = build_score(Arrangement(flute=[q(72, 0, 12), q(74, 48, 48)]), "t")
    assert shape(bar(s, 1)) == [("n", F(1), None), ("r", F(1), None), ("r", F(2), None)]
    # 3/4: two silent beats are two quarter rests, not a half rest
    s = build_score(Arrangement(flute=[q(72, 0, 12), q(74, 36, 36)], beats_per_bar=3), "t")
    assert shape(bar(s, 1)) == [("n", F(1), None), ("r", F(1), None), ("r", F(1), None)]
    assert rest_rule(12, 24, 4, 12) == "R-REST-2"      # half rest on beat 2
    assert rest_rule(24, 24, 4, 12) is None
    assert rest_rule(0, 18, 4, 12) == "R-REST-3"       # dotted rest in simple time


def test_r_rest_4_rest_on_the_second_sixteenth():
    s = build_score(Arrangement(flute=[q(72, 0, 3), q(74, 12, 36)]), "t")
    assert shape(bar(s, 1))[:3] == [("n", F(1, 4), None), ("r", F(1, 4), None),
                                    ("r", F(1, 2), None)]
    assert rest_rule(3, 9, 4, 12) == "R-REST-3" and rest_rule(3, 6, 4, 12) == "R-REST-4"


def test_r_rest_5_compound_rests():
    s = build_score(Arrangement(flute=[q(72, 0, 6), q(74, 36, 36)], beats_per_bar=2,
                                beat_ticks=18), "t")
    # 8th, then two 8th rests (never a quarter rest on the 2nd 8th), then a dotted-quarter rest
    assert shape(bar(s, 1)) == [("n", F(1, 2), None), ("r", F(1, 2), None),
                                ("r", F(1, 2), None), ("r", F(3, 2), None)]
    assert rest_rule(6, 12, 2, 18) == "R-REST-5" and rest_rule(0, 12, 2, 18) is None


# ---------------------------------------------------------------- beams (R-BEAM)


def test_r_beam_2_four_four_eighths_in_half_bars_never_across_the_middle():
    eighths = [q(72 + i % 3, 6 * i, 6) for i in range(8)]
    s = build_score(Arrangement(flute=eighths), "t")
    assert beam_groups(bar(s, 1)) == [[F(1, 2)] * 4, [F(1, 2)] * 4]
    # with 16ths in the half bar, each beat is its own group
    mixed = [q(72, 0, 6), q(74, 6, 6), q(76, 12, 3), q(77, 15, 3), q(79, 18, 6),
             q(77, 24, 24)]
    s = build_score(Arrangement(flute=mixed), "t")
    assert beam_groups(bar(s, 1)) == [[F(1, 2)] * 2, [F(1, 4), F(1, 4), F(1, 2)]]
    assert not written(Arrangement(flute=eighths)) and not written(Arrangement(flute=mixed))


def test_r_beam_3_six_eight_beams_in_threes_and_sixteenths_by_dotted_beat():
    flute = [q(72, 0, 6), q(74, 6, 6), q(76, 12, 6)] + [q(77 + i % 2, 18 + 3 * i, 3)
                                                         for i in range(6)]
    s = build_score(Arrangement(flute=flute, beats_per_bar=2, beat_ticks=18), "t")
    assert beam_groups(bar(s, 1)) == [[F(1, 2)] * 3, [F(1, 4)] * 6]


def test_r_beam_5_sixteenth_beams_unbroken_inside_a_beat():
    flute = [q(72 + i % 3, 3 * i, 3) for i in range(8)]
    s = build_score(Arrangement(flute=flute, beats_per_bar=2), "t")
    secondary = [el.beams.getTypes()[1] for el in bar(s, 1).notes]
    assert secondary == ["start", "continue", "continue", "stop"] * 2
    # a dotted 8th + 16th: the 16th gets a partial beam pointing back into the beat
    s = build_score(Arrangement(flute=[q(72, 0, 9), q(74, 9, 3), q(76, 12, 12)],
                                beats_per_bar=2), "t")
    last16 = bar(s, 1).notes[1].beams.beamsList[1]
    assert last16.type == "partial" and last16.direction == "left"


# ---------------------------------------------------------------- accidentals (R-ACC)


def test_r_acc_3_courtesy_accidental_after_the_bar_line(tmp_path):
    # F#6 and F4 in bar 1, then F6 in bar 2: F6 shows a courtesy natural
    arr = Arrangement(flute=[q(90, 0, 12), q(65, 12, 36), q(89, 48, 48)])
    path = write_outputs(arr, "t", tmp_path)["musicxml"]
    back = converter.parse(str(path))
    f6 = bar(back, 2).notes[0]
    assert f6.pitch.nameWithOctave == "F6"
    assert f6.pitch.accidental is not None and f6.pitch.accidental.name == "natural"
    assert f6.pitch.accidental.displayStatus is not False
    assert "R-ACC-3" not in rules(lint(arr))


def test_r_acc_2_tied_note_keeps_its_accidental():
    arr = Arrangement(flute=[q(73, 36, 24), q(74, 60, 12), q(73, 72, 24)])
    s = build_score(arr, "t")
    tied, _, again = bar(s, 2).notes
    assert tied.tie.type == "stop" and not tied.pitch.accidental.displayStatus
    assert again.pitch.accidental.displayStatus                   # struck again: shown
    assert "R-ACC-1" not in rules(lint(arr))


def test_r_acc_6_one_spelling_per_bar():
    # A# (rising to B) and Bb (falling to A) in one bar of C major: one spelling
    arr = Arrangement(flute=[q(70, 0, 6), q(71, 6, 6), q(70, 12, 6), q(69, 18, 6),
                             q(70, 24, 12), q(72, 36, 12)])
    s = build_score(arr, "t")
    names = {n.pitch.name for n in bar(s, 1).notes if n.pitch.midi == 70}
    assert len(names) == 1
    assert "R-ACC-6" not in rules(lint(arr))


def test_r_acc_1_missing_accidental_is_found():
    m = stream.Measure(number=1)
    a, b = note.Note("F#5", quarterLength=2), note.Note("F5", quarterLength=2)
    a.pitch.accidental.displayStatus = True
    m.append([a, b])                     # F natural after F# with no natural sign
    s = stream.Score([stream.Part([m])])
    issues = notation._written_checks(s, Arrangement(flute=[q(78, 0, 24), q(77, 24, 24)]))
    assert "R-ACC-1" in rules(issues)


# ---------------------------------------------------------------- page (R-PAGE)


def test_r_page_5_pickup_bar_is_bar_zero(tmp_path):
    arr = Arrangement(flute=[q(67, -12, 12), q(72, 0, 36)], beats_per_bar=3)
    assert pickup_start(arr) == 24 and printed_bar(arr, -12) == 0 and printed_bar(arr, 0) == 1
    xml = write_outputs(arr, "t", tmp_path)["musicxml"].read_text()
    assert '<measure implicit="yes" number="0">' in xml
    assert "R-PAGE-5" not in rules(lint(arr))
    # a lead-in written as a full bar of rests is what R-PAGE-5 forbids
    m1 = stream.Measure(number=1)
    m1.append([note.Rest(quarterLength=2), note.Note("G4", quarterLength=1)])
    m2 = stream.Measure(number=2)
    m2.append(note.Note("C5", quarterLength=3))
    old = stream.Score([stream.Part([m1, m2])])
    assert "R-PAGE-5" in rules(notation._written_checks(old, arr))


def test_score_follows_the_rulebook_for_random_rhythms():
    """Whatever the rhythm, every meter: no written-rhythm, rest, tie, beam or
    accidental errors (R-METER, R-REST, R-TIE, R-BEAM, R-ACC)."""
    for bpb, beat in [(2, 12), (3, 12), (4, 12), (2, 18), (4, 18), (3, 6)]:
        for seed in range(6):
            r = random.Random(seed * 31 + bpb + beat)
            t, notes = r.choice([0, -3, -6, -12]), []
            while t < 300:
                step = r.choice([3, 6, 6, 9, 12, 15, 18, 21, 24, 36])
                if r.random() < 0.8:
                    notes.append(q(r.randint(62, 90), t, step))
                t += step + (r.choice([0, 3, 6, 12]) if r.random() < 0.3 else 0)
            arr = Arrangement(flute=notes, beats_per_bar=bpb, beat_ticks=beat)
            assert written(arr) == [], (bpb, beat, seed)


# ---------------------------------------------------------------- tidy: breathing, phrases


def long_line(n_bars=12, bpm=60):
    """Continuous quarters in 4/4 at 60 bpm: one note a second, no rests."""
    return Arrangement(flute=[q(72 + (i % 5), 12 * i, 12) for i in range(4 * n_bars)], bpm=bpm)


def test_r_breath_2_a_breath_at_least_every_ten_seconds():
    arr = long_line()
    assert "R-BREATH-2" in rules(lint(arr))
    fixed, fixes = tidy_report(arr)
    breaths = [n for n in fixed.flute if n.breath_after]
    assert breaths and {f.rule for f in fixes} == {"R-BREATH-2"}
    ends = [0] + [n.end for n in breaths] + [fixed.flute[-1].end]
    assert all(4 * 12 <= b - a <= 10 * 12 for a, b in zip(ends, ends[1:]))   # 4-10 s apart
    assert all(n.end % 48 == 0 for n in breaths)               # at bar lines (R-BREATH-3)
    assert "R-BREATH-2" not in rules(lint(fixed))


def test_r_breath_3_no_breath_between_leading_tone_and_tonic():
    # bar lines fall between B4 and C5 (leading tone -> tonic): breathe elsewhere
    notes = []
    for b in range(12):
        notes += [q(64, 48 * b, 12), q(65, 48 * b + 12, 12), q(67, 48 * b + 24, 12),
                  q(71, 48 * b + 36, 12)]
    notes[-1] = q(72, notes[-1].start, 12)
    fixed = tidy(Arrangement(flute=[q(n.pitch if n.pitch != 64 else 72, n.start, n.dur)
                                    for n in notes], bpm=60))
    line = sorted(fixed.flute, key=lambda n: n.start)
    for a, b in zip(line, line[1:]):
        if a.breath_after:
            assert not (a.pitch == 71 and b.pitch == 72)


def test_breath_every_phrase_kept_and_rests_count_as_breaths():
    # two-bar phrases (8 s at 60 bpm) separated by a beat of rest: nothing to add
    notes = [q(72 + i % 4, 12 * i + 12 * (i // 8), 12) for i in range(32)]
    arr = Arrangement(flute=notes, bpm=60)
    assert tidy_report(arr)[1] == []
    marked = Arrangement(flute=[q(72, 0, 48, breath_after=True)] + notes[4:], bpm=60)
    assert tidy(marked).flute[0].breath_after                  # existing breaths stay


def test_r_phr_3_phrase_end_held_to_its_beat():
    # an 8th on beat 1 then silence until the next bar: written as a quarter
    arr = Arrangement(flute=[q(72, 0, 6), q(74, 48, 12), q(76, 60, 3), q(77, 72, 6)],
                      expression=[Mark("staccato", 72)])
    fixed = tidy(arr)
    by_start = {n.start: n for n in fixed.flute}
    assert by_start[0].dur == 12                               # held to the end of beat 1
    assert by_start[60].dur == 3                               # next note too close: kept
    assert by_start[72].dur == 6                               # staccato keeps its length
    assert [n.start for n in fixed.flute] == [n.start for n in arr.flute]
    assert [n.pitch for n in fixed.flute] == [n.pitch for n in arr.flute]
    assert arr.flute[0].dur == 6                               # the input is not changed


def test_r_phr_4_slurs_end_at_breath_marks():
    arr = long_line(n_bars=12)
    arr.expression = [Mark("slur", 0, 12 * 47)]
    fixed, fixes = tidy_report(arr)
    slurs = [m for m in fixed.expression if m.kind == "slur"]
    assert len(slurs) >= 2 and "R-PHR-4" in rules(fixes)
    breaths = {n.start for n in fixed.flute if n.breath_after}
    for s in slurs:
        assert not any(s.start <= b < s.end for b in breaths)
    assert "R-PHR-4" not in rules(lint(fixed))


def test_r_flute_and_range_advice():
    arr = Arrangement(flute=[q(93 + i % 3, 12 * i, 12) for i in range(6)] + [q(61, 72, 24)],
                      expression=[Mark("dynamic", 0, value="pp"), Mark("trill", 72)])
    found = rules(lint(arr))
    assert {"R-RANGE-3", "R-FLUTE-2", "R-FLUTE-3"} <= found


# ---------------------------------------------------------------- report


def test_write_outputs_writes_the_notation_check(tmp_path):
    arr = Arrangement(flute=[q(72, 0, 24), q(74, 24, 24), q(76, 48, 48)], bpm=90)
    paths = write_outputs(arr, "t", tmp_path)
    rep = json.loads(paths["notation"].read_text())
    assert rep["summary"]["headline"] == "follows standard notation"
    assert rep["issues"] == [] and rep["summary"]["top"] == []
    assert notation.report_for(tmp_path, arr)["summary"]["errors"] == 0


def test_summary_lists_errors_first_with_bars():
    issues = [Issue("R-PHR-3", 2, "x", "advice"), Issue("R-REST-2", 5, "a rest hides the beat")]
    issues = sorted(issues, key=lambda i: (i.level != "error", i.bar))
    s = notation.summary(issues)
    assert s["headline"] == "1 thing to fix"
    assert s["top"][0] == "bar 5: a rest hides the beat (R-REST-2)"
