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
    flute = [q(76, 0, 12), q(77, 12, 12), q(79, 24, 12), q(81, 36, 12),
             q(79, 48, 6), q(77, 54, 6), q(76, 60, 24)]
    rh = [q(p, 0, 36) for p in (55, 60, 64)] + [q(p, 36, 36) for p in (53, 57, 60)]
    lh = [q(36, 0, 36), q(41, 36, 36)]
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


def test_pickup_notes_padded():
    arr = Arrangement(flute=[q(67, -6, 6), q(72, 0, 12), q(74, 12, 36)])
    s = build_score(arr, "Pickup")
    part = flute_part(s)
    m1, m2 = bar(part, 1), bar(part, 2)
    assert m1.duration.quarterLength == 4.0
    rests = [el for el in m1.notesAndRests if el.isRest]
    notes = [el for el in m1.notesAndRests if not el.isRest]
    assert sum(r.quarterLength for r in rests) == 3.5
    assert len(notes) == 1 and notes[0].pitch.midi == 67 and notes[0].offset == 3.5
    first_down = m2.notes[0]
    assert first_down.offset == 0 and first_down.pitch.midi == 72


def test_flat_key_spelling():
    arr = Arrangement(flute=[q(70, 0, 12), q(66, 12, 12), q(65, 24, 24)], key_fifths=-1)
    s = build_score(arr, "F major")
    names = [n.pitch.name for n in flute_part(s).recurse().notes]
    assert names == ["B-", "G-", "F"]
    assert bar(flute_part(s), 1).keySignature.sharps == -1


def test_chromatic_notes_spelled_by_direction():
    # F major: F# leading up to G, A-flat leading down to G
    arr = Arrangement(flute=[q(65, 0, 12), q(66, 12, 12), q(67, 24, 12),
                             q(69, 36, 12), q(68, 48, 12), q(67, 60, 12)], key_fifths=-1)
    names = [n.pitch.name for n in flute_part(build_score(arr, "x")).recurse().notes]
    assert names == ["F", "F#", "G", "A", "A-", "G"]
    # C major: B-flat leading down
    arr = Arrangement(flute=[q(71, 0, 12), q(70, 12, 12), q(69, 24, 12)])
    names = [n.pitch.name for n in flute_part(build_score(arr, "x")).recurse().notes]
    assert names == ["B", "B-", "A"]
    assert spell(70, 0, "major", direction=1).name == "A#"
    assert spell(70, 0, "major", direction=-1).name == "B-"
    assert spell(68, 0, "minor", direction=-1).name == "G#"   # leading tone stays


def test_spelling_follows_key():
    assert spell(66, 2, "major").name == "F#"
    assert spell(70, 2, "major").name == "A#"
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
        q(72, 0, 48),                                         # bar 1: whole note
        # bar 2 empty
        q(74, 99, 12), q(76, 111, 9),                         # bar 3: 16th-offset note
        q(76, 144, 6), q(77, 150, 12), q(79, 162, 6), q(77, 168, 24),  # bar 4: syncopation
        q(74, 210, 12),                                       # bar 5: crosses the middle
        q(72, 276, 24),                                       # bar 6 -> 7 across the barline
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
    flute = [q(72, 0, 36), q(74, 72, 12)]
    s = build_score(Arrangement(flute=flute, beats_per_bar=3), "Waltz")
    part = flute_part(s)
    F = Fraction
    assert shape(bar(part, 1)) == [("n", F(3), None)]
    assert shape(bar(part, 2)) == [("r", F(3), None)]
    assert shape(bar(part, 3)) == [("n", F(1), None), ("r", F(1), None), ("r", F(1), None)]


def test_triplets_get_brackets(tmp_path):
    flute = [q(72, 0, 4), q(74, 4, 4), q(76, 8, 4), q(77, 12, 36)]
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
    flute = [q(72, 0, 8), q(74, 8, 4), q(76, 12, 36)]
    notes = list(flute_part(build_score(Arrangement(flute=flute), "x")).recurse().notes)
    assert notes[0].duration.tuplets[0].bracket is not False


def test_solo_long_rest_becomes_multi_bar_rest(tmp_path):
    from music21 import spanner
    flute = [q(72, 0, 48), q(74, 48 * 5, 48)]          # bars 2-5 empty
    arr = Arrangement(flute=flute)
    s = build_score(arr, "x")
    mmr = list(s.recurse().getElementsByClass(spanner.MultiMeasureRest))
    assert len(mmr) == 1 and mmr[0].numRests == 4
    xml = write_outputs(arr, "x", tmp_path)["musicxml"].read_text()
    assert "<multiple-rest" in xml
    # a duet keeps every bar so the piano stays visible
    duet_arr = Arrangement(flute=flute, lh=[q(36, 0, 48 * 6)])
    assert not list(build_score(duet_arr, "x").recurse().getElementsByClass(
        spanner.MultiMeasureRest))


def test_breath_mark_on_last_tied_piece(tmp_path):
    # 3 beats from beat 3 in 4/4: crosses the barline, breath goes after the tie
    flute = [q(72, 24, 36, breath_after=True), q(74, 60, 12)]
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
    s = build_score(Arrangement(flute=[q(72, 0, 12)]), "Solo")
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
    assert abs((longest.end - longest.start) - 1.0) < 1e-3     # 24 ticks at 120 bpm


def test_midi_has_pickup_lead_in(tmp_path):
    arr = Arrangement(flute=[q(67, -6, 6), q(72, 0, 12)], bpm=60)
    pm = pretty_midi.PrettyMIDI(str(write_outputs(arr, "P", tmp_path)["midi"]))
    starts = sorted(n.start for inst in pm.instruments for n in inst.notes)
    assert abs(starts[0] - 3.5) < 1e-3 and abs(starts[1] - 4.0) < 1e-3
