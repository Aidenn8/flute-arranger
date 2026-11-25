from fractions import Fraction

import pretty_midi
from music21 import articulations, converter, layout, meter, note, stream, tempo

from sheetswap.model import Arrangement, QNote
from sheetswap.score import build_score, spell, write_outputs


def q(pitch, start, dur, **kw):
    return QNote(pitch, start, dur, **kw)


def flute_part(score):
    return score.parts[0]


def bar(part, number):
    return part.getElementsByClass(stream.Measure)[number - 1]


def shape(measure):
    """[(kind, quarterLength, tie type or None)] for one measure."""
    out = []
    for el in measure.notesAndRests:
        kind = "r" if el.isRest else ("c" if el.isChord else "n")
        out.append((kind, Fraction(el.quarterLength), el.tie.type if el.tie else None))
    return out


def duet():
    flute = [q(76, 0, 48), q(77, 48, 48), q(79, 96, 48), q(81, 144, 48),
             q(79, 192, 24), q(77, 216, 24), q(76, 240, 96)]
    rh = [q(p, 0, 144) for p in (55, 60, 64)] + [q(p, 144, 144) for p in (53, 57, 60)]
    lh = [q(36, 0, 144), q(41, 144, 144)]
    return Arrangement(flute=flute, rh=rh, lh=lh, beats_per_bar=3, bpm=96.4,
                       key_fifths=0, key_mode="major")


def test_musicxml_written_and_bars_full(tmp_path):
    paths = write_outputs(duet(), "Test Song", tmp_path)
    assert set(paths) >= {"musicxml", "midi"}
    assert paths["musicxml"].exists() and paths["midi"].exists()
    xml = paths["musicxml"].read_text()
    assert "<part-name>Flute</part-name>" in xml
    assert "<part-name>Piano</part-name>" in xml
    assert "<staves>2</staves>" in xml
    back = converter.parse(paths["musicxml"])
    counts = set()
    for part in back.parts:
        measures = list(part.getElementsByClass(stream.Measure))
        counts.add(len(measures))
        for m in measures:
            assert m.duration.quarterLength == 3.0, (part, m.number)
    assert counts == {3}


def test_tempo_meter_key_title():
    arr = duet()
    arr.key_fifths, arr.key_mode = 2, "major"
    s = build_score(arr, "Test Song")
    assert s.metadata.title == "Test Song"
    first = bar(flute_part(s), 1)
    assert first.getElementsByClass(meter.TimeSignature)[0].ratioString == "3/4"
    mm = list(s.recurse().getElementsByClass(tempo.MetronomeMark))
    assert len(mm) == 1 and mm[0].number == 96
    assert mm[0].referent.quarterLength == 1.0
    ks = first.keySignature
    assert ks.sharps == 2
    assert flute_part(s).partName == "Flute"


def test_pickup_bar_holds_only_the_pickup():
    # R-PAGE-5: the pickup bar has no rests before its notes and is bar 0
    arr = Arrangement(flute=[q(67, -24, 24), q(72, 0, 48), q(74, 48, 144)])
    s = build_score(arr, "Pickup")
    part = flute_part(s)
    m0, m1 = bar(part, 1), bar(part, 2)
    assert m0.number == 0 and m1.number == 1
    assert m0.duration.quarterLength == 0.5 and m0.paddingLeft == 3.5
    assert [el.isRest for el in m0.notesAndRests] == [False]
    assert m0.notes[0].pitch.midi == 67 and m0.notes[0].offset == 0
    first_down = m1.notes[0]
    assert first_down.offset == 0 and first_down.pitch.midi == 72


def test_flat_key_spelling():
    arr = Arrangement(flute=[q(70, 0, 48), q(66, 48, 48), q(65, 96, 96)], key_fifths=-1)
    s = build_score(arr, "F major")
    names = [n.pitch.name for n in flute_part(s).recurse().notes]
    assert names == ["B-", "G-", "F"]
    assert bar(flute_part(s), 1).keySignature.sharps == -1


def test_chromatic_notes_spelled_by_direction():
    # F major: F# leading up to G, A-flat leading down to G
    arr = Arrangement(flute=[q(65, 0, 48), q(66, 48, 48), q(67, 96, 48),
                             q(69, 144, 48), q(68, 192, 48), q(67, 240, 48)], key_fifths=-1)
    names = [n.pitch.name for n in flute_part(build_score(arr, "x")).recurse().notes]
    assert names == ["F", "F#", "G", "A", "A-", "G"]
    # C major: B-flat leading down
    arr = Arrangement(flute=[q(71, 0, 48), q(70, 48, 48), q(69, 96, 48)])
    names = [n.pitch.name for n in flute_part(build_score(arr, "x")).recurse().notes]
    assert names == ["B", "B-", "A"]
    assert spell(70, 0, "major", direction=1).name == "A#"
    assert spell(70, 0, "major", direction=-1).name == "B-"
    assert spell(68, 0, "minor", direction=-1).name == "G#"   # leading tone stays


def test_spelling_follows_key():
    assert spell(66, 2, "major").name == "F#"
    assert spell(70, 2, "major").name == "B-"        # b6, borrowed from D minor (no context)
    assert spell(63, 0, "minor").name == "D#"        # A minor: #4, leading tone to E
    assert spell(66, 0, "minor").name == "F#"        # A minor: raised 6th
    assert spell(70, 0, "major").name == "B-"        # C major: b7
    assert spell(65, 6, "major").name == "E#"        # F# major
    assert spell(71, -6, "major").name == "C-"       # G-flat major
    assert spell(71, -6, "major").midi == 71
    assert spell(60, 7, "major").name == "B#"        # C# major
    assert spell(60, 7, "major").midi == 60
    assert spell(73, -1, "minor").name == "C#"       # D minor leading tone
    assert spell(66, -2, "minor").name == "F#"       # G minor leading tone
    assert spell(68, 0, "minor").name == "G#"        # A minor leading tone


def test_rhythm_is_beat_aware():
    flute = [
        q(72, 0, 192),                                         # bar 1: whole note
        # bar 2 empty
        q(74, 396, 48), q(76, 444, 36),                         # bar 3: 16th-offset note
        q(76, 576, 24), q(77, 600, 48), q(79, 648, 24), q(77, 672, 96),  # bar 4: syncopation
        q(74, 840, 48),                                       # bar 5: crosses the middle
        q(72, 1104, 96),                                       # bar 6 -> 7 across the barline
    ]
    s = build_score(Arrangement(flute=flute), "Rhythm")
    part = flute_part(s)
    F = Fraction
    assert shape(bar(part, 1)) == [("n", F(4), None)]
    m2 = bar(part, 2)
    assert shape(m2) == [("r", F(4), None)]
    assert m2.notesAndRests[0].fullMeasure in (True, "always")
    assert shape(bar(part, 3)) == [("r", F(1, 4), None), ("n", F(3, 4), "start"),
                                   ("n", F(1, 4), "stop"), ("n", F(3, 4), None),
                                   ("r", F(2), None)]
    assert shape(bar(part, 4)) == [("n", F(1, 2), None), ("n", F(1), None),
                                   ("n", F(1, 2), None), ("n", F(2), None)]
    assert shape(bar(part, 5)) == [("r", F(1), None), ("r", F(1, 2), None),
                                   ("n", F(1, 2), "start"), ("n", F(1, 2), "stop"),
                                   ("r", F(1, 2), None), ("r", F(1), None)]
    assert shape(bar(part, 6)) == [("r", F(2), None), ("r", F(1), None), ("n", F(1), "start")]
    assert shape(bar(part, 7))[0] == ("n", F(1), "stop")
    # piece ends with a final barline
    last = part.getElementsByClass(stream.Measure)[-1]
    assert last.rightBarline is not None and last.rightBarline.type == "final"


def test_three_four_rests_and_dotted_half():
    flute = [q(72, 0, 144), q(74, 288, 48)]
    s = build_score(Arrangement(flute=flute, beats_per_bar=3), "Waltz")
    part = flute_part(s)
    F = Fraction
    assert shape(bar(part, 1)) == [("n", F(3), None)]
    assert shape(bar(part, 2)) == [("r", F(3), None)]
    assert shape(bar(part, 3)) == [("n", F(1), None), ("r", F(1), None), ("r", F(1), None)]


def test_triplets_get_brackets(tmp_path):
    flute = [q(72, 0, 16), q(74, 16, 16), q(76, 32, 16), q(77, 48, 144)]
    arr = Arrangement(flute=flute)
    s = build_score(arr, "Triplets")
    notes = list(flute_part(s).recurse().notes)
    assert [Fraction(n.quarterLength) for n in notes[:3]] == [Fraction(1, 3)] * 3
    assert notes[0].duration.tuplets[0].type == "start"
    assert notes[2].duration.tuplets[0].type == "stop"
    assert notes[0].duration.tuplets[0].bracket is False      # beamed: number only
    xml = write_outputs(arr, "Triplets", tmp_path)["musicxml"].read_text()
    assert "<tuplet" in xml and "<time-modification>" in xml


def test_unbeamed_triplet_keeps_bracket():
    # triplet quarter + triplet 8th cannot be beamed together
    flute = [q(72, 0, 32), q(74, 32, 16), q(76, 48, 144)]
    notes = list(flute_part(build_score(Arrangement(flute=flute), "x")).recurse().notes)
    assert notes[0].duration.tuplets[0].bracket is not False


def test_solo_long_rest_becomes_multi_bar_rest(tmp_path):
    from music21 import spanner
    flute = [q(72, 0, 192), q(74, 192 * 5, 192)]        # bars 2-5 empty
    arr = Arrangement(flute=flute)
    s = build_score(arr, "x")
    mmr = list(s.recurse().getElementsByClass(spanner.MultiMeasureRest))
    assert len(mmr) == 1 and mmr[0].numRests == 4
    xml = write_outputs(arr, "x", tmp_path)["musicxml"].read_text()
    assert "<multiple-rest" in xml
    # a duet keeps every bar so the piano stays visible
    duet_arr = Arrangement(flute=flute, lh=[q(36, 0, 192 * 6)])
    assert not list(build_score(duet_arr, "x").recurse().getElementsByClass(
        spanner.MultiMeasureRest))


def test_breath_mark_on_last_tied_piece(tmp_path):
    # 3 beats from beat 3 in 4/4: crosses the barline, breath goes after the tie
    flute = [q(72, 96, 144, breath_after=True), q(74, 240, 48)]
    arr = Arrangement(flute=flute)
    s = build_score(arr, "Breath")
    notes = list(flute_part(s).recurse().notes)
    marks = [any(isinstance(a, articulations.BreathMark) for a in n.articulations)
             for n in notes]
    assert marks == [False, True, False]
    assert notes[1].tie.type == "stop"
    xml = write_outputs(arr, "Breath", tmp_path)["musicxml"].read_text()
    assert "breath-mark" in xml


def test_duet_piano_grand_staff():
    s = build_score(duet(), "Duet")
    parts = list(s.parts)
    assert len(parts) == 3
    assert parts[0].partName == "Flute"
    assert all(isinstance(p, stream.PartStaff) for p in parts[1:])
    groups = list(s.getElementsByClass(layout.StaffGroup))
    assert len(groups) == 1 and groups[0].symbol == "brace"
    assert set(groups[0].getSpannedElements()) == set(parts[1:])
    assert bar(parts[2], 1).clef.sign == "F"
    # RH triad held a dotted half -> one chord, no ties
    assert shape(bar(parts[1], 1)) == [("c", Fraction(3), None)]
    assert sorted(p.midi for p in bar(parts[1], 1).notes[0].pitches) == [55, 60, 64]


def test_solo_has_one_part():
    s = build_score(Arrangement(flute=[q(72, 0, 48)]), "Solo")
    assert len(s.parts) == 1
    assert not list(s.getElementsByClass(layout.StaffGroup))


def test_empty_flute_still_builds(tmp_path):
    paths = write_outputs(Arrangement(flute=[]), "Nothing", tmp_path)
    back = converter.parse(paths["musicxml"])
    assert len(back.parts[0].getElementsByClass(stream.Measure)) >= 1


def test_midi_programs_and_timing(tmp_path):
    arr = duet()
    arr.bpm = 120
    paths = write_outputs(arr, "Duet", tmp_path)
    pm = pretty_midi.PrettyMIDI(str(paths["midi"]))
    by_prog = {}
    for inst in pm.instruments:
        by_prog.setdefault(inst.program, []).extend(inst.notes)
    assert len(by_prog[73]) == len(arr.flute)
    assert len(by_prog[0]) == len(arr.rh) + len(arr.lh)
    first = min(by_prog[73], key=lambda n: n.start)
    assert abs(first.start - 0.0) < 1e-3 and first.pitch == 76
    longest = max(by_prog[73], key=lambda n: n.end - n.start)
    assert abs((longest.end - longest.start) - 1.0) < 1e-3     # 96 ticks at 120 bpm


def test_midi_has_pickup_lead_in(tmp_path):
    arr = Arrangement(flute=[q(67, -24, 24), q(72, 0, 48)], bpm=60)
    pm = pretty_midi.PrettyMIDI(str(write_outputs(arr, "P", tmp_path)["midi"]))
    starts = sorted(n.start for inst in pm.instruments for n in inst.notes)
    assert abs(starts[0] - 3.5) < 1e-3 and abs(starts[1] - 4.0) < 1e-3


# ---------------------------------------------------------------- compound meters

def beam_groups(measure):
    """Runs of beamed notes in a measure, as lists of quarterLengths."""
    groups, cur = [], []
    for el in measure.notesAndRests:
        b = el.beams.getTypes() if not el.isRest else []
        if not b:
            continue
        cur.append(Fraction(el.quarterLength))
        if b[0] == "stop":
            groups.append(cur)
            cur = []
    return groups


def six_eight(flute, bpb=2, beat_ticks=72, bpm=60):
    return Arrangement(flute=flute, beats_per_bar=bpb, beat_ticks=beat_ticks, bpm=bpm)


def test_six_eight_signature_tempo_and_beams():
    flute = [q(72 + i, 24 * i, 24) for i in range(6)]                    # bar 1: six 8ths
    flute += [q(72, 144, 48), q(74, 192, 24), q(76, 216, 24), q(77, 240, 24), q(79, 264, 24)]
    s = build_score(six_eight(flute, bpm=66), "Jig")
    part = flute_part(s)
    assert bar(part, 1).getElementsByClass(meter.TimeSignature)[0].ratioString == "6/8"
    mm = list(s.recurse().getElementsByClass(tempo.MetronomeMark))
    assert mm[0].number == 66 and mm[0].referent.quarterLength == 1.5       # dotted quarter = 66
    F = Fraction
    assert beam_groups(bar(part, 1)) == [[F(1, 2)] * 3, [F(1, 2)] * 3]      # 8ths in threes
    # quarter + flagged 8th, then three beamed 8ths
    assert shape(bar(part, 2)) == [("n", F(1), None), ("n", F(1, 2), None)] + [("n", F(1, 2), None)] * 3
    assert beam_groups(bar(part, 2)) == [[F(1, 2)] * 3]


def test_six_eight_rests_ties_and_dotted_notes():
    F = Fraction
    flute = [q(72, 0, 144),                       # bar 1: dotted half
             q(74, 168, 24), q(76, 192, 24),         # bar 2: 8th rest, two 8ths | dotted quarter rest
             q(77, 312, 72),                      # bar 3: 8th rest, quarter tied to an 8th, ...
             q(79, 384, 48),                      # ... quarter on the 2nd 8th of beat 2
             q(81, 432, 36), q(79, 468, 12), q(77, 480, 24),   # bar 4: siciliano, then a beat's rest
             q(76, 624, 24)]                      # bar 5: quarter rest + 8th, dotted quarter rest
    part = flute_part(build_score(six_eight(flute), "x"))
    assert shape(bar(part, 1)) == [("n", F(3), None)]
    assert shape(bar(part, 2)) == [("r", F(1, 2), None), ("n", F(1, 2), None),
                                   ("n", F(1, 2), None), ("r", F(3, 2), None)]
    assert shape(bar(part, 3)) == [("r", F(1, 2), None), ("n", F(1), "start"),
                                   ("n", F(1, 2), "stop"), ("n", F(1), None)]
    assert shape(bar(part, 4)) == [("n", F(3, 4), None), ("n", F(1, 4), None),
                                   ("n", F(1, 2), None), ("r", F(3, 2), None)]
    assert beam_groups(bar(part, 4)) == [[F(3, 4), F(1, 4), F(1, 2)]]
    assert shape(bar(part, 5)) == [("r", F(1), None), ("n", F(1, 2), None), ("r", F(3, 2), None)]


def test_compound_rests_show_the_beat():
    F = Fraction
    # 12/8: an empty half bar is one dotted half rest; 9/8 has none
    part = flute_part(build_score(six_eight([q(72, 0, 144)], bpb=4), "x"))
    assert shape(bar(part, 1)) == [("n", F(3), None), ("r", F(3), None)]
    part = flute_part(build_score(six_eight([q(72, 0, 72)], bpb=3), "x"))
    assert shape(bar(part, 1)) == [("n", F(3, 2), None), ("r", F(3, 2), None), ("r", F(3, 2), None)]
    # an empty bar is a whole-bar rest
    part = flute_part(build_score(six_eight([q(72, 0, 144), q(74, 288, 144)]), "x"))
    m2 = bar(part, 2)
    assert shape(m2) == [("r", F(3), None)] and m2.notesAndRests[0].fullMeasure in (True, "always")


def test_three_eight_in_eighth_beats():
    F = Fraction
    flute = [q(76, 0, 12), q(75, 12, 12), q(76, 24, 12), q(75, 36, 12), q(76, 48, 12), q(71, 60, 12),
             q(74, 72, 48), q(72, 120, 24), q(69, 144, 72)]
    s = build_score(six_eight(flute, bpb=3, beat_ticks=24, bpm=144), "Elise")
    part = flute_part(s)
    assert bar(part, 1).getElementsByClass(meter.TimeSignature)[0].ratioString == "3/8"
    mm = list(s.recurse().getElementsByClass(tempo.MetronomeMark))[0]
    assert mm.number == 144 and mm.referent.quarterLength == 0.5            # eighth = 144
    assert beam_groups(bar(part, 1)) == [[F(1, 4)] * 6]                     # one beam a bar
    assert shape(bar(part, 2)) == [("n", F(1), None), ("n", F(1, 2), None)]
    assert shape(bar(part, 3)) == [("n", F(3, 2), None)]
    for m in part.getElementsByClass(stream.Measure):
        assert m.duration.quarterLength == 1.5
    # the same notes on one dotted beat a bar look the same
    one = flute_part(build_score(six_eight(flute, bpb=1, beat_ticks=72, bpm=48), "x"))
    assert [shape(m) for m in one.getElementsByClass(stream.Measure)] == \
           [shape(m) for m in part.getElementsByClass(stream.Measure)]


def test_compound_midi_tempo_and_signature(tmp_path):
    # 6/8 at dotted quarter = 60: an 8th lasts 1/3 s, a bar 2 s
    arr = six_eight([q(72, 0, 24), q(74, 24, 24), q(76, 48, 24), q(77, 144, 72)], bpm=60)
    pm = pretty_midi.PrettyMIDI(str(write_outputs(arr, "M", tmp_path)["midi"]))
    sig = pm.time_signature_changes[0]
    assert (sig.numerator, sig.denominator) == (6, 8)
    assert abs(pm.get_tempo_changes()[1][0] - 90) < 1e-3                    # quarters a minute
    notes = sorted(pm.instruments[0].notes, key=lambda n: n.start)
    assert [round(n.start, 3) for n in notes] == [0.0, 0.333, 0.667, 2.0]
    assert abs(notes[-1].end - notes[-1].start - 1.0) < 1e-3


def test_six_eight_renders_with_verovio(tmp_path):
    from sheetswap.render import render_svg
    flute = [q(74, -24, 24)] + [q(72 + i % 5, 24 * i, 24) for i in range(12)] + [q(79, 288, 144)]
    paths = write_outputs(six_eight(flute), "Jig", tmp_path)
    xml = paths["musicxml"].read_text()
    assert "<beats>6</beats>" in xml and "<beat-type>8</beat-type>" in xml
    svgs = render_svg(paths["musicxml"])
    assert svgs and "<svg" in svgs[0]


# ---------------------------------------------------------------- 32nds and 16th triplets (sheet music)

def test_thirty_seconds_are_written_exactly(tmp_path):
    from sheetswap.notation import lint
    F = Fraction
    flute = [q(72 + i % 4, 6 * i, 6) for i in range(8)]                       # beat 1: eight 32nds
    flute += [q(74, 48, 18), q(76, 66, 6), q(77, 72, 18), q(76, 90, 6)]       # beat 2: dotted 16th + 32nd, twice
    flute += [q(74, 96, 12), q(72, 108, 6), q(71, 114, 6), q(72, 120, 24),    # beat 3: 16th, two 32nds, 8th
              q(79, 144, 48)]
    arr = Arrangement(flute=flute)
    part = flute_part(build_score(arr, "32nds"))
    assert shape(bar(part, 1)) == [("n", F(1, 8), None)] * 8 + [
        ("n", F(3, 8), None), ("n", F(1, 8), None), ("n", F(3, 8), None), ("n", F(1, 8), None),
        ("n", F(1, 4), None), ("n", F(1, 8), None), ("n", F(1, 8), None), ("n", F(1, 2), None),
        ("n", F(1), None)]
    first = list(bar(part, 1).notes)[:8]
    assert [len(n.beams.beamsList) for n in first] == [3] * 8                 # one group, 3 beams
    assert [e for e in lint(arr) if e.level == "error"] == []
    xml = write_outputs(arr, "32nds", tmp_path)["musicxml"].read_text()
    assert xml.count("<type>32nd</type>") == 12


def test_sixteenth_triplets_are_written_exactly():
    from sheetswap.notation import lint
    F = Fraction
    flute = [q(72 + i % 3, 8 * i, 8) for i in range(6)]                       # beat 1: six 16th triplets
    flute += [q(74, 48, 8), q(76, 56, 8), q(77, 64, 16), q(79, 80, 8),        # beat 2: two 16th triplets,
              q(77, 88, 8), q(76, 96, 96)]                                     # an 8th triplet, two more
    arr = Arrangement(flute=flute)
    part = flute_part(build_score(arr, "Triplets"))
    m = bar(part, 1)
    assert shape(m) == [("n", F(1, 6), None)] * 6 + [
        ("n", F(1, 6), None), ("n", F(1, 6), None), ("n", F(1, 3), None), ("n", F(1, 6), None),
        ("n", F(1, 6), None), ("n", F(2), None)]
    notes = list(m.notes)
    assert all(n.duration.tuplets and n.duration.tuplets[0].numberNotesActual == 3 for n in notes[:10])
    assert [e for e in lint(arr) if e.level == "error"] == []


def test_sixteenth_triplets_on_the_middle_of_a_triplet_beat():
    # 8th triplet, two 16th triplets (the 2nd on the beat's middle), 8th triplet
    F = Fraction
    arr = Arrangement(flute=[q(72, 0, 48), q(74, 48, 16), q(76, 64, 8), q(77, 72, 8), q(79, 80, 16),
                             q(76, 96, 96)])
    m = bar(flute_part(build_score(arr, "Triplets")), 1)
    assert shape(m) == [("n", F(1), None), ("n", F(1, 3), None), ("n", F(1, 6), None),
                        ("n", F(1, 6), None), ("n", F(1, 3), None), ("n", F(2), None)]
