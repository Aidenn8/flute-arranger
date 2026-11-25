"""Sheet music in (score_input): a score's exact notes and marks, the part choice,
MusicXML/MXL/notated MIDI, and the whole way to a flute part.

"can you add a feature where you can input in piano/violin music and get out flute"
"""
import json
import zipfile
from fractions import Fraction
from pathlib import Path

import pretty_midi
import pytest
from music21 import corpus, stream

from sheetswap import pipeline, score_input
from sheetswap.config import TICKS_PER_BEAT as T
from sheetswap.model import ArrangeOptions, Mark, QNote

# ---------------------------------------------------------------- hand-written scores

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN"
  "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="3.1">"""


def _n(step, octave, dur, typ, alter=0, voice=1, staff=None, extra="", notations="", pre=""):
    """One <note>; durations in divisions."""
    alt = f"<alter>{alter}</alter>" if alter else ""
    st = f"<staff>{staff}</staff>" if staff else ""
    nt = f"<notations>{notations}</notations>" if notations else ""
    return (f"<note>{pre}<pitch><step>{step}</step>{alt}<octave>{octave}</octave></pitch>"
            f"<duration>{dur}</duration>{extra}<voice>{voice}</voice><type>{typ}</type>{st}{nt}</note>")


def _rest(dur, typ, voice=1, staff=None):
    st = f"<staff>{staff}</staff>" if staff else ""
    return f"<note><rest/><duration>{dur}</duration><voice>{voice}</voice><type>{typ}</type>{st}</note>"


def _words(text, staff=1):
    return (f'<direction placement="below"><direction-type><words>{text}</words></direction-type>'
            f"<staff>{staff}</staff></direction>")


def _dyn(level, staff=1):
    return (f'<direction placement="below"><direction-type><dynamics><{level}/></dynamics>'
            f"</direction-type><staff>{staff}</staff></direction>")


# A little piano piece in G, 3/4, with a one-beat pickup (divisions: 2 a quarter).
# Right hand: a tune in voice 1 over an inner voice 2 and a chord; left hand: a bass.
PIANO = HEAD + """
<work><work-title>Little Air</work-title></work>
<identification><creator type="composer">Test Composer</creator></identification>
<part-list><score-part id="P1"><part-name>Piano</part-name>
<score-instrument id="P1-I1"><instrument-name>Piano</instrument-name></score-instrument></score-part></part-list>
<part id="P1">
<measure number="0" implicit="yes">
<attributes><divisions>2</divisions><key><fifths>1</fifths><mode>major</mode></key>
<time><beats>3</beats><beat-type>4</beat-type></time><staves>2</staves>
<clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef></attributes>
<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>90</per-minute></metronome></direction-type><staff>1</staff><sound tempo="90"/></direction>
""" + _dyn("p") + _words("dolce") + _n("D", 5, 2, "quarter", staff=1, notations='<slur type="start" number="1"/>') + """
<backup><duration>2</duration></backup>""" + _rest(2, "quarter", voice=5, staff=2) + """
</measure>
<measure number="1">
""" + _n("G", 5, 4, "half", staff=1) + _n("F", 5, 2, "quarter", alter=1, staff=1, notations='<slur type="stop" number="1"/>') + """
<backup><duration>6</duration></backup>
""" + _n("B", 4, 2, "quarter", voice=2, staff=1) + _n("C", 5, 2, "quarter", voice=2, staff=1) + _n("D", 5, 2, "quarter", voice=2, staff=1) + """
<backup><duration>6</duration></backup>
""" + _n("G", 3, 6, "half", voice=5, staff=2, extra="", pre="").replace("<type>half</type>", "<type>half</type><dot/>") + """
</measure>
<measure number="2">
<direction placement="below"><direction-type><wedge type="crescendo" number="1"/></direction-type><staff>1</staff></direction>
""" + _n("C", 5, 2, "quarter", staff=1) + _n("E", 5, 2, "quarter", staff=1, pre="<chord/>", notations="<articulations><staccato/></articulations>") + """
<note><grace slash="yes"/><pitch><step>F</step><alter>1</alter><octave>5</octave></pitch><voice>1</voice><type>eighth</type><staff>1</staff></note>
""" + _n("G", 5, 2, "quarter", staff=1, notations="<articulations><tenuto/></articulations>") + _n("A", 5, 1, "eighth", staff=1) + _n("B", 5, 1, "eighth", staff=1, extra='<tie type="start"/>', notations='<tied type="start"/>') + """
<direction placement="below"><direction-type><wedge type="stop" number="1"/></direction-type><staff>1</staff></direction>
<backup><duration>6</duration></backup>
""" + _words("Ped.", staff=2) + _n("C", 3, 2, "quarter", voice=5, staff=2, notations="<technical><fingering>5</fingering></technical>") + _n("E", 3, 2, "quarter", voice=5, staff=2) + _n("G", 3, 2, "quarter", voice=5, staff=2) + """
</measure>
<measure number="3">
""" + _n("B", 5, 2, "quarter", staff=1, extra='<tie type="stop"/>', notations='<tied type="stop"/>') + _dyn("f") + _n("A", 5, 2, "quarter", staff=1, notations='<fermata type="upright"/>') + _n("G", 5, 2, "quarter", staff=1, notations="<ornaments><trill-mark/></ornaments>") + """
<backup><duration>6</duration></backup>
""" + _n("G", 2, 4, "half", voice=5, staff=2) + _rest(2, "quarter", voice=5, staff=2) + """
<barline location="right"><bar-style>light-heavy</bar-style></barline>
</measure>
</part></score-partwise>
"""

# (pitch, start, length) of the right hand's top line, ticks (48 a quarter), 0 = bar 1
PIANO_TUNE = [(74, -48, 48), (79, 0, 96), (78, 96, 48), (76, 144, 48), (79, 192, 48),
              (81, 240, 24), (83, 264, 72), (81, 336, 48), (79, 384, 48)]
PIANO_BASS = [(55, 0, 144), (48, 144, 48), (52, 192, 48), (55, 240, 48), (43, 288, 96)]

# A violin tune in 6/8 with an 8th pickup, a double stop at the end, and a
# repeat with 1st and 2nd endings (divisions: 2 a quarter, so an 8th is 1).
VIOLIN = HEAD + """
<part-list><score-part id="P1"><part-name>Violin</part-name>
<score-instrument id="P1-I1"><instrument-name>Violin</instrument-name></score-instrument></score-part></part-list>
<part id="P1">
<measure number="0" implicit="yes">
<attributes><divisions>2</divisions><key><fifths>1</fifths><mode>major</mode></key>
<time><beats>6</beats><beat-type>8</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
""" + _n("D", 5, 1, "eighth") + """
</measure>
<measure number="1">
<barline location="left"><bar-style>heavy-light</bar-style><repeat direction="forward"/></barline>
""" + _n("G", 5, 1, "eighth") + _n("F", 5, 1, "eighth", alter=1) + _n("G", 5, 1, "eighth") + _n("A", 5, 2, "quarter") + _n("G", 5, 1, "eighth") + """
</measure>
<measure number="2">
<barline location="left"><ending number="1" type="start"/></barline>
""" + _n("B", 5, 3, "quarter").replace("<type>quarter</type>", "<type>quarter</type><dot/>") + _n("A", 5, 1, "eighth") + _n("G", 5, 1, "eighth") + _n("F", 5, 1, "eighth", alter=1) + """
<barline location="right"><bar-style>light-heavy</bar-style><ending number="1" type="stop"/><repeat direction="backward"/></barline>
</measure>
<measure number="3">
<barline location="left"><ending number="2" type="start"/></barline>
""" + _n("B", 4, 6, "half").replace("<type>half</type>", "<type>half</type><dot/>") + _n("G", 5, 6, "half", pre="<chord/>").replace("<type>half</type>", "<type>half</type><dot/>") + """
<barline location="right"><bar-style>light-heavy</bar-style><ending number="2" type="stop"/></barline>
</measure>
</part></score-partwise>
"""
# played: pickup, bar 1, bar 2 (1st ending), bar 1 again, bar 3 (2nd ending)
BAR1 = [(79, 0, 24), (78, 24, 24), (79, 48, 24), (81, 72, 48), (79, 120, 24)]
VIOLIN_TUNE = ([(74, -24, 24)] + BAR1 + [(83, 144, 72), (81, 216, 24), (79, 240, 24), (78, 264, 24)]
               + [(p, s + 288, d) for p, s, d in BAR1] + [(79, 432, 144)])


def _write(tmp_path: Path, text: str, name: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def _rows(notes):
    return [(q.pitch, q.start, q.dur) for q in notes]


# ---------------------------------------------------------------- reading MusicXML


def test_piano_right_hand_top_line_exact(tmp_path):
    src = score_input.load_score(_write(tmp_path, PIANO, "air.musicxml"))
    assert [p.name for p in src.parts] == ["Piano, right hand", "Piano, left hand"]
    assert [p.staff for p in src.parts] == [1, 2] and all(p.keyboard for p in src.parts)
    assert src.auto_part == 0                                 # the right hand
    rh, lh = src.parts
    assert _rows(rh.melody) == PIANO_TUNE                     # inner voice under the held G5 left out
    assert _rows(lh.melody) == PIANO_BASS
    assert {(q.pitch, q.start) for q in rh.notes} >= {(71, 0), (72, 48), (74, 96), (72, 144)}
    assert (src.title, src.composer) == ("Little Air", "Test Composer")
    assert (src.meter, src.beats_per_bar, src.beat_ticks) == ("3/4", 3, 48)
    assert (src.key_fifths, src.key_mode, src.quarter_bpm, src.pickup) == (1, "major", 90.0, 48)
    assert src.warnings == []


def test_piano_marks_carried_and_piano_only_things_dropped(tmp_path):
    src = score_input.load_score(_write(tmp_path, PIANO, "air.musicxml"))
    marks = {(m.kind, m.start, m.value) for m in src.parts[0].marks}
    assert {("dynamic", -48, "p"), ("text", -48, "dolce"), ("staccato", 144, ""),
            ("tenuto", 192, ""), ("grace", 192, "78"), ("fermata", 336, ""),
            ("trill", 384, ""), ("dynamic", 336, "f")} <= marks
    slur = [m for m in src.parts[0].marks if m.kind == "slur"]
    assert [(m.start, m.end) for m in slur] == [(-48, 96)]          # first note to last note
    cresc = [m for m in src.parts[0].marks if m.kind == "cresc"]
    assert len(cresc) == 1 and cresc[0].start == 144 and cresc[0].end > 144
    words = {m.value for p in src.parts for m in p.marks if m.kind == "text"}
    assert words == {"dolce"}                 # "Ped." is piano only; no word made up for "quarter = 90"
    # the left hand shares the piano's dynamics, not the right hand's slurs and accents
    lh = {(m.kind, m.start) for m in src.parts[1].marks}
    assert ("dynamic", -48) in lh and not any(k in ("slur", "staccato") for k, _ in lh)
    # playback loudness follows the dynamics
    vel = {q.start: q.velocity for q in src.parts[0].melody}
    assert vel[-48] < vel[336]


def test_violin_six_eight_pickup_double_stop_and_repeats(tmp_path):
    src = score_input.load_score(_write(tmp_path, VIOLIN, "jig.musicxml"))
    assert len(src.parts) == 1 and not src.parts[0].keyboard
    assert (src.meter, src.beats_per_bar, src.beat_ticks, src.pickup) == ("6/8", 2, 72, 24)
    assert _rows(src.parts[0].melody) == VIOLIN_TUNE                 # top of the double stop
    assert "repeats in the original are written out" in " ".join(src.warnings)


def test_compressed_mxl_reads_the_same(tmp_path):
    xml = _write(tmp_path, PIANO, "air.musicxml")
    mxl = tmp_path / "air.mxl"
    with zipfile.ZipFile(mxl, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><container><rootfiles>'
                   '<rootfile full-path="score.xml" media-type="application/vnd.recordare.musicxml+xml"/>'
                   "</rootfiles></container>")
        z.writestr("score.xml", xml.read_text())
    a, b = score_input.load_score(xml), score_input.load_score(mxl)
    assert _rows(a.parts[0].melody) == _rows(b.parts[0].melody) == PIANO_TUNE
    assert a.to_dict() == b.to_dict()


def test_bad_file_says_so(tmp_path):
    with pytest.raises(ValueError, match="MusicXML"):
        score_input.load_score(_write(tmp_path, "<html>not music</html>", "page.xml"))


def _one_bar_score(path, values):
    """A flute part of one 4/4 bar: (pitch name, quarterLength) in order."""
    from music21 import meter, note
    s = stream.Score()
    p = stream.Part()
    p.partName = "Flute"
    m = stream.Measure(number=1)
    m.append(meter.TimeSignature("4/4"))
    for name, ql in values:
        m.append(note.Note(name, quarterLength=ql))
    p.append(m)
    s.insert(0, p)
    s.write("musicxml", fp=str(path))
    return path


def test_tuplets_and_32nds_are_exact(tmp_path):
    values = ([(n, Fraction(1, 3)) for n in ("C5", "D5", "E5")]              # 8th triplet
              + [(n, Fraction(1, 6)) for n in ("F5", "G5", "A5", "B5", "C6", "D6")]  # 16th triplets
              + [("E6", 0.875), ("F6", 0.125), ("G6", 1.0)])       # double-dotted 8th + 32nd
    src = score_input.load_score(_one_bar_score(tmp_path / "tuplets.musicxml", values))
    assert [(q.start, q.dur) for q in src.parts[0].melody] == [
        (0, 16), (16, 16), (32, 16), (48, 8), (56, 8), (64, 8), (72, 8), (80, 8), (88, 8),
        (96, 42), (138, 6), (144, 48)]
    assert src.parts[0].warnings == [] and src.warnings == []
    arr, _, _ = score_input.arrange_score(src, ArrangeOptions())
    assert not [w for w in arr.warnings if "quicker" in w or "shorter" in w]
    # finer than the grid (a 16th quintuplet: 9.6 ticks): rounded, with a warning
    five = [(n, Fraction(1, 5)) for n in ("C5", "D5", "E5", "F5", "G5")] + [("A5", 3.0)]
    src = score_input.load_score(_one_bar_score(tmp_path / "five.musicxml", five))
    assert [q.start for q in src.parts[0].melody][:3] == [0, 10, 19]
    assert src.parts[0].warnings and "don't fit this app's finest grid" in src.parts[0].warnings[0]


# ---------------------------------------------------------------- the music21 corpus


def test_bach_chorale_soprano_exact():
    src = score_input.load_score(corpus.getWork("bach/bwv66.6"))
    assert [p.name for p in src.parts] == ["Soprano", "Alto", "Tenor", "Bass"]
    assert src.auto_part == 0 and src.pickup == 48 and (src.key_fifths, src.key_mode) == (3, "minor")
    sop = corpus.parse("bach/bwv66.6").parts[0].stripTies().flatten()
    first_bar = 1.0                                           # the pickup is one quarter
    want = [(n.pitch.midi, round((n.offset - first_bar) * T), round(n.quarterLength * T))
            for n in sop.notes]
    assert _rows(src.parts[0].melody) == want
    assert sum(m.kind == "fermata" for m in src.parts[0].marks) == 6


def test_string_quartet_first_violin_with_its_marks():
    src = score_input.load_score(corpus.getWork("haydn/opus74no1/movement3.mxl"))
    assert [p.name for p in src.parts] == ["Violin 1", "Violin 2", "Viola", "Violoncello"]
    assert src.auto_part == 0 and not any(p.keyboard for p in src.parts)
    v1 = src.parts[0]
    kinds = {m.kind for m in v1.marks}
    assert {"dynamic", "slur", "staccato"} <= kinds
    # the first 14 bars, as written (then repeated): top notes of violin 1, ties joined
    part = corpus.parse("haydn/opus74no1/movement3.mxl").parts[0].stripTies().flatten()
    want = [(max(p.midi for p in n.pitches), round(n.offset * T)) for n in part.notes
            if not n.duration.isGrace and n.offset < 14 * 3]
    got = [(q.pitch, q.start) for q in v1.melody if q.start < 14 * 144]
    assert got == want
    # the repeat is written out: bars 1-14 come again right after
    again = [(p, s - 14 * 144) for p, s in ((q.pitch, q.start) for q in v1.melody)
             if 14 * 144 <= s < 28 * 144]
    assert again == want


# ---------------------------------------------------------------- which part


def test_part_choice_by_auto_number_and_name(tmp_path):
    src = score_input.load_score(_write(tmp_path, PIANO, "air.musicxml"))
    assert score_input.choose_part(src, "auto") == (0, [])
    assert score_input.choose_part(src, "1") == (1, [])
    assert score_input.choose_part(src, "piano, left hand") == (1, [])
    assert score_input.choose_part(src, "left")[0] == 1
    idx, warn = score_input.choose_part(src, "Oboe")
    assert idx == 0 and "no part" in warn[0]


def test_auto_prefers_a_solo_line_over_the_piano():
    piano_rh = score_input.ScorePart("Piano, right hand", "Piano", 1, True, [QNote(84, 0, 48)] * 40)
    violin = score_input.ScorePart("Violin", "Violin", 0, False, [QNote(76, 0, 48)] * 30)
    cello = score_input.ScorePart("Cello", "Violoncello", 0, False, [QNote(43, 0, 48)] * 50)
    assert score_input.auto_part([piano_rh, cello, violin]) == 2        # not the bass line
    assert score_input.auto_part([piano_rh]) == 0
    assert score_input.auto_part([cello]) == 0                          # nothing else to take


# ---------------------------------------------------------------- the tune of a piano part
# "work more on converting piano music input to flute sheet music --> improve the accuracy
# and rhythm": the tune is followed through both hands (score_input.find_tune).

# Schumann's "Happy Farmer" texture, 4/4: right-hand after-beat chords over a left-hand
# tune for two bars, then the tune moves up into the right hand over left-hand chords.
FARMER_LH = [("F3", 1.5), ("A3", 0.5), ("C4", 1.5), ("F3", 0.5),
             ("Bb3", 0.5), ("D4", 0.5), ("F4", 0.5), ("D4", 0.5), ("C4", 2.0)]
FARMER_RH = [("Bb4", 1.5), ("A4", 0.5), ("G4", 1.5), ("C5", 0.5),
             ("F4", 1.5), ("A4", 0.5), ("C5", 2.0)]


def _farmer_score(path):
    from music21 import chord, instrument, layout, meter, note
    sc = stream.Score()
    rh, lh = stream.PartStaff(id="P1-Staff1"), stream.PartStaff(id="P1-Staff2")
    rh.insert(0, instrument.Piano())
    for st in (rh, lh):
        st.append(meter.TimeSignature("4/4"))
    for top in (["C4", "F4", "A4"], ["D4", "F4", "Bb4"]):        # bars 1-2: chords after the beat
        for _ in range(2):
            rh.append(note.Rest(quarterLength=0.5))
            rh.append(chord.Chord(top, quarterLength=0.5))
            rh.append(chord.Chord(top, quarterLength=0.5))
            rh.append(note.Rest(quarterLength=0.5))
    for name, q in FARMER_LH:
        lh.append(note.Note(name, quarterLength=q))
    for name, q in FARMER_RH:                                     # bars 3-4: the tune up top
        rh.append(note.Note(name, quarterLength=q))
    for low in (["C3", "E3", "G3"], ["F2", "C3", "A3"]):
        for _ in range(4):
            lh.append(chord.Chord(low, quarterLength=1.0))
    sc.insert(0, rh)
    sc.insert(0, lh)
    sc.insert(0, layout.StaffGroup([rh, lh], symbol="brace"))
    sc.write("musicxml", fp=str(path))
    return path


def _ticks(seq, start=0):
    out, t = [], start
    for name, q in seq:
        out.append((pretty_midi.note_name_to_number(name), t, int(q * T)))
        t += int(q * T)
    return out


def test_piano_tune_follows_the_left_hand_and_back(tmp_path):
    src = score_input.load_score(_farmer_score(tmp_path / "farmer.musicxml"))
    names = [p.name for p in src.parts]
    assert names == ["Piano, right hand", "Piano, left hand", "Piano, the tune"]
    assert src.auto_part == 2 and src.parts[2].tune and src.parts[2].keyboard
    want = _ticks(FARMER_LH) + _ticks(FARMER_RH, start=384)
    assert _rows(src.parts[2].melody) == want
    # the hands are still there to choose, and the page lists the tune first
    assert _rows(src.parts[0].melody)[:2] == [(69, 24, 24), (69, 48, 24)]
    back = score_input.ScoreSource.from_dict(json.loads(json.dumps(src.to_dict())))
    assert back.parts[2].tune and back.auto_part == 2


def test_piano_tune_from_a_midi_file_with_one_track_a_hand(tmp_path):
    pm = pretty_midi.PrettyMIDI(resolution=480)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    q = 0.5                                                      # seconds a quarter (120 bpm)
    rh = pretty_midi.Instrument(program=0, name="up")
    lh = pretty_midi.Instrument(program=0, name="down")
    for bar, top in enumerate((["C4", "F4", "A4"], ["D4", "F4", "Bb4"])):
        for half in range(2):
            for k in (1, 2):
                t = (bar * 4 + half * 2 + k * 0.5) * q
                rh.notes += [pretty_midi.Note(70, pretty_midi.note_name_to_number(n), t, t + q / 2)
                             for n in top]
    for p, s, d in _ticks(FARMER_LH) + [(p, s + 384, d) for p, s, d in _ticks(FARMER_LH)]:
        lh.notes.append(pretty_midi.Note(80, p, s / T * q, (s + d) / T * q))
    pm.instruments += [rh, lh]
    path = tmp_path / "farmer.mid"
    pm.write(str(path))
    src = score_input.load_notated_midi(path)
    tune = src.parts[src.auto_part]
    assert tune.tune and tune.name == "Acoustic Grand Piano, the tune"
    assert [q.pitch for q in tune.melody] == [p for p, _, _ in _ticks(FARMER_LH)] * 2


def test_piano_tune_rests_through_an_accompaniment_introduction():
    """Moonlight-sonata texture: two bars of a triplet figure alone, then a tune in long
    notes above the same figure: the flute waits, then plays the tune without the figure."""
    rh, lh = [], []
    figure = [56, 61, 64]
    for b in range(6):
        for beat in range(4):
            for k, p in enumerate(figure):
                rh.append(QNote(p, b * 192 + beat * 48 + 16 * k, 16))
        lh.append(QNote(37, b * 192, 192))
        lh.append(QNote(49, b * 192, 192))
    tune = [(68, 384, 96), (69, 480, 96), (68, 576, 144), (66, 720, 48), (64, 768, 96),
            (68, 864, 96), (66, 960, 96), (64, 1056, 96)]
    rh += [QNote(p, s, d) for p, s, d in tune]
    line, where = score_input.find_tune([rh, lh], 192, 48)
    assert _rows(line) == tune
    assert [where[b] for b in range(6)] == [-1, -1, 0, 0, 0, 0]


def test_piano_tune_keeps_broken_chord_notes_that_are_the_tune():
    """Für Elise: the right hand's A-C-E-A answer is a broken chord and part of the tune;
    the left hand's arpeggios under it are not."""
    rh_line = [(76, 0, 12), (75, 12, 12), (76, 24, 12), (71, 36, 12), (74, 48, 12), (72, 60, 12),
               (69, 72, 24), (60, 108, 12), (64, 120, 12), (69, 132, 12),
               (71, 144, 24), (64, 180, 12), (68, 192, 12), (71, 204, 12),
               (72, 216, 24), (64, 252, 12), (76, 264, 12), (75, 276, 12)]
    lh = []
    for b, (a, c, d) in enumerate([(45, 52, 57), (40, 52, 56), (45, 52, 57)]):
        lh += [QNote(a, 72 + 72 * b, 12), QNote(c, 84 + 72 * b, 12), QNote(d, 96 + 72 * b, 12)]
    rh = [QNote(p, s, d) for p, s, d in rh_line]
    line, where = score_input.find_tune([rh, lh], 72, 24)
    assert _rows(line) == rh_line
    assert set(where.values()) == {0}


def test_duet_piano_part_leaves_the_tune_out_of_both_hands(tmp_path, monkeypatch):
    from sheetswap import piano
    seen = {}

    def fake(notes, line, grid, style, shift=0, key=None):
        seen["notes"] = notes
        return [], []

    monkeypatch.setattr(piano, "accompany", fake)
    src = score_input.load_score(_farmer_score(tmp_path / "farmer.musicxml"))
    score_input.arrange_score(src, ArrangeOptions(mode="duet"))
    sec = 60 / src.quarter_bpm / T
    pool = {(n.pitch, round(n.start / sec)) for n in seen["notes"]}
    tune = {(q.pitch, q.start) for q in src.parts[src.auto_part].melody}
    assert pool and not pool & tune                               # neither hand doubles the flute
    assert {(60, 24), (65, 24), (69, 24), (48, 384)} <= pool      # the chords stay


def test_options_part_survives_the_form_and_names_its_folder():
    o = ArrangeOptions.from_form({"part": " Violin  I "})
    assert o.part == "Violin I" and o.key().endswith("-pViolin_I")
    assert ArrangeOptions.from_form({}).part == "auto"
    assert "-p" not in ArrangeOptions().key().split("-x")[-1]
    assert ArrangeOptions(part=2).part == "2"
    assert ArrangeOptions.from_form({"part": "../../etc"}).key().endswith("-petc")


# ---------------------------------------------------------------- arranging


def _score_song(tmp_path, text, name="input.musicxml"):
    d = tmp_path / "song"
    d.mkdir(parents=True)
    (d / name).write_text(text)
    (d / "meta.json").write_text(json.dumps({"title": "air"}))
    return d


def test_end_to_end_musicxml_to_flute(tmp_path):
    d = _score_song(tmp_path, PIANO)
    an = pipeline.analyze(d)
    assert an.kind == "score" and an.title == "Little Air"
    assert (d / "score.json").exists()
    assert pipeline.analyze(d).score.parts[0].name == "Piano, right hand"     # from the cache
    for mode in ("solo", "duet"):
        res = pipeline.arrange_and_render(d, ArrangeOptions(mode=mode))
        arr = res["arr"]
        assert res["svgs"] and "<svg" in res["svgs"][0]
        assert res["musicxml"].exists() and res["midi"].exists()
        assert [(q.pitch % 12, q.start, q.dur) for q in arr.flute] == \
            [(p % 12, s, du) for p, s, du in PIANO_TUNE]                         # exact rhythm
        assert arr.time_signature == (3, 4) and arr.bpm == 90 and arr.key_fifths == 1
        kinds = {m.kind for m in arr.expression}
        assert {"dynamic", "slur", "staccato", "fermata", "trill", "grace"} <= kinds
        assert (mode == "duet") == bool(arr.rh or arr.lh)
        xml = res["musicxml"].read_text()
        assert "<fermata" in xml and "<staccato" in xml
    left = pipeline.arrange_and_render(d, ArrangeOptions(part="1"))["arr"]
    assert [q.start for q in left.flute] == [s for _, s, _ in PIANO_BASS]
    assert all(60 <= q.pitch <= 96 for q in left.flute)                     # moved up for the flute


def test_simple_and_easy_key_and_meter_choices(tmp_path):
    d = _score_song(tmp_path, VIOLIN)
    an = pipeline.analyze(d)
    arr = pipeline.arrange(an, ArrangeOptions())
    assert arr.time_signature == (6, 8) and arr.beat_ticks == 72
    assert [q.start for q in arr.flute] == [s for _, s, _ in VIOLIN_TUNE]
    easy = pipeline.arrange(an, ArrangeOptions(simple=True))
    assert all(q.start % 24 == 0 and q.dur >= 24 for q in easy.flute)
    three = pipeline.arrange(an, ArrangeOptions(meter="3/8"))             # same notes, other bars
    assert three.time_signature == (3, 8) and [q.start for q in three.flute] == [q.start for q in arr.flute]


def test_simplify_line():
    line = [QNote(60, 0, 12), QNote(62, 12, 12), QNote(64, 24, 36), QNote(65, 60, 12),
            QNote(67, 72, 16), QNote(69, 88, 16), QNote(71, 104, 16)]
    assert _rows(score_input.simplify_line(line)) == [(60, 0, 24), (64, 24, 48), (67, 72, 48)]


def test_marks_follow_notes_that_move():
    line = [QNote(72, 0, 48), QNote(74, 48, 48), QNote(76, 96, 48)]
    marks = [Mark("dynamic", 8, 0, "p"), Mark("staccato", 24), Mark("slur", 0, 120),
             Mark("text", 160, 0, "rit."), Mark("cresc", 20, 120)]
    got = {(m.kind, m.start, m.end) for m in score_input.attach_marks(marks, line)}
    assert got == {("dynamic", 0, 0), ("slur", 0, 96), ("text", 96, 0), ("cresc", 48, 120)}


# ---------------------------------------------------------------- MIDI written in a notation program

# (pitch, start, length) in ticks: dotted rhythms, 8th and quarter triplets,
# a 16th-triplet group, syncopations and long notes, in 4/4 at 96
TRICKY = [(72, 0, 36), (74, 36, 12), (76, 48, 16), (77, 64, 16), (79, 80, 16), (81, 96, 24),
          (79, 120, 48), (77, 168, 24), (76, 192, 12), (74, 204, 12), (72, 216, 12), (74, 228, 12),
          (76, 240, 32), (77, 272, 32), (79, 304, 32), (84, 336, 8), (83, 344, 8), (81, 352, 8),
          (79, 360, 24), (77, 384, 72), (76, 456, 24), (74, 480, 96), (72, 576, 192)]


def _midi(path, tune, jitter=0.0, resolution=480, bpm=96):
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=resolution)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0.0))
    q = 60 / bpm
    mel = pretty_midi.Instrument(program=73, name="Flute")
    for i, (p, s, d) in enumerate(tune):
        wob = jitter * ((i * 7) % 5 - 2) / 2
        mel.notes.append(pretty_midi.Note(90, p, max(0.0, s / T * q + wob), (s + d) / T * q))
    bass = pretty_midi.Instrument(program=0, name="Piano")
    for b in range(0, 4 * 4 * T, 4 * T):
        bass.notes.append(pretty_midi.Note(70, 48, b / T * q, (b + 4 * T) / T * q))
    pm.instruments += [mel, bass]
    pm.write(str(path))
    return path


def test_written_midi_takes_the_exact_path_and_beats_the_quantizer(tmp_path):
    d = tmp_path / "song"
    d.mkdir()
    _midi(d / "input.mid", TRICKY)
    an = pipeline.analyze(d)
    assert an.kind == "score"
    names = [p.name for p in an.score.parts]
    assert names[an.score.auto_part].startswith("Track 1: Flute")
    exact = pipeline.arrange(an, ArrangeOptions())
    want = [(p % 12, s, du) for p, s, du in TRICKY]
    assert [(q.pitch % 12, q.start, q.dur) for q in exact.flute] == want
    # the audio-style quantizer on the same file gets a good part of the rhythm wrong
    old = pipeline.arrange(pipeline._analyze_midi(d / "input.mid", "x"), ArrangeOptions())
    old_hits = len({(q.pitch % 12, q.start, q.dur) for q in old.flute} & set(want))
    assert old_hits < len(want) * 0.8


def test_played_midi_still_goes_to_the_quantizer(tmp_path):
    played = _midi(tmp_path / "played.mid", TRICKY, jitter=0.03)
    assert score_input.load_notated_midi(played) is None
    d = tmp_path / "song"
    d.mkdir()
    (d / "input.mid").write_bytes(played.read_bytes())
    assert pipeline.analyze(d).kind == "midi"
    assert score_input.load_notated_midi(tmp_path / "missing.mid") is None


def test_midi_at_low_resolution_keeps_triplets(tmp_path):
    src = score_input.load_notated_midi(_midi(tmp_path / "t.mid", TRICKY, resolution=220))
    assert src is not None
    assert _rows(src.parts[0].melody) == TRICKY and not src.parts[0].warnings


def _midi_from(path, rows, num=4, den=4, program=0):
    """A written MIDI file from (pitch, start, length) in ticks (48 a quarter), 120 bpm."""
    pm = pretty_midi.PrettyMIDI(resolution=480)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(num, den, 0))
    inst = pretty_midi.Instrument(program=program)
    for p, s, d in rows:
        inst.notes.append(pretty_midi.Note(80, p, s / (2 * T), (s + d) / (2 * T)))
    pm.instruments.append(inst)
    pm.write(str(path))
    return path


@pytest.mark.parametrize("name", ["bach/bwv144.3", "bach/bwv2.6"])
def test_midi_pickup_found_where_bar_lines_cut_fewest_notes(tmp_path, name):
    """Bach chorales start on beat 4 (a quarter pickup); music21 writes their MIDI from
    the pickup on. The bar lines that tie the fewest notes over (R-METER-1) put bar 1 a
    beat in. The old rule (most note time on downbeats, the file's own start winning
    near-ties) left bar 1 at the file's start: every bar line a beat early, the
    cadences in the middle of the bar (bench/midi_downbeat.py: 71 of 120 chorales right
    before, 108 now)."""
    path = tmp_path / "chorale.mid"
    corpus.parse(name).write("midi", fp=str(path))
    src = score_input.load_notated_midi(path)
    assert src.pickup == 48 and (src.beats_per_bar, src.beat_ticks) == (4, 48)


def test_midi_pickup_of_a_written_tune(tmp_path):
    tune = [(67, 0, 48)]
    t = 48
    for p, d in [(72, 96), (71, 48), (69, 48), (67, 96), (69, 96), (71, 48), (72, 48), (74, 96),
                 (72, 144), (67, 48), (72, 96), (74, 96), (76, 96), (74, 48), (72, 48), (71, 144)]:
        tune.append((p, t, d))
        t += d
    src = score_input.load_notated_midi(_midi_from(tmp_path / "tune.mid", tune))
    assert src.pickup == 48
    assert [q.start for q in src.parts[0].melody][:3] == [-48, 0, 96]


def test_bar_lines_option_moves_a_midi_files_bars(tmp_path):
    d = tmp_path / "song"
    d.mkdir()
    _midi(d / "input.mid", TRICKY)
    an = pipeline.analyze(d)
    later = pipeline.arrange(an, ArrangeOptions(barshift=1))
    assert [q.start for q in later.flute][:3] == [-48, -12, 0]
    # a MusicXML score's bar lines are exact: the option leaves them alone
    s = _score_song(tmp_path / "xml", PIANO)
    xml_an = pipeline.analyze(s)
    assert [q.start for q in pipeline.arrange(xml_an, ArrangeOptions(barshift=1)).flute] == \
        [q.start for q in pipeline.arrange(xml_an, ArrangeOptions()).flute]


# ---------------------------------------------------------------- the published melodies (if downloaded)

REF = Path(__file__).resolve().parent.parent / "samples" / "ref"


@pytest.mark.parametrize("slug", ["menuet_bwv1006", "gavotte_bwv1006"])
def test_violin_scores_match_the_published_melody(slug):
    """bench/make_refs.py's melodies, built from the same Mutopia MIDI files:
    single-line violin pieces come out note for note, rhythm and bar lines included."""
    folder = REF / slug
    if not (folder / "ref.json").exists():
        pytest.skip("samples/ref not downloaded (bench/make_refs.py)")
    ref = json.loads((folder / "ref.json").read_text())
    path = folder / ref["source"].split(", tracks ")[0]
    src = score_input.load_notated_midi(path)
    assert src is not None and src.auto_part == 0
    pm = pretty_midi.PrettyMIDI(str(path))
    first = min(Fraction(pm.time_to_tick(n.start), pm.resolution) for i in pm.instruments for n in i.notes)
    d0 = first - Fraction(min(q.start for q in src.parts[0].notes), T)     # our bar 1, in the file
    assert d0 == Fraction(ref["pickup_beats"]).limit_denominator(100)
    line = [(q.pitch, Fraction(q.start, T) + d0, Fraction(q.end, T) + d0) for q in src.parts[0].melody]
    played = []
    cursor = Fraction(0)
    end_all = max(e for _, _, e in line)
    for a, b in ref["form"]:                                  # the repeats as the performer plays them
        a = Fraction(a).limit_denominator(100)
        b = end_all if b is None else Fraction(b).limit_denominator(100)
        played += [(p, float(cursor + s - a - d0), float(min(e, b) - s)) for p, s, e in line if a <= s < b]
        cursor += b - a
    want = [(n["pitch"], n["onset_beats"], n["dur_beats"]) for n in ref["notes"]]
    assert [(p, round(s, 4), round(du, 4)) for p, s, du in played] == \
        [(p, round(s, 4), round(du, 4)) for p, s, du in want]


# ---------------------------------------------------------------- the web page and the command line


@pytest.fixture
def web_out(tmp_path, monkeypatch):
    import queue
    from sheetswap import config
    from sheetswap.web import app as web
    d = tmp_path / "out"
    monkeypatch.setattr(config, "OUT_DIR", d)
    monkeypatch.setattr(web, "_jobs", queue.Queue())
    monkeypatch.setattr(web, "_ensure_worker", lambda: None)
    web.app.config["TESTING"] = True
    return d, web, web.app.test_client()


def test_upload_accepts_sheet_music(web_out):
    import io
    out, web, client = web_out
    for name in ("sonata.musicxml", "sonata.mxl", "sonata.xml"):
        resp = client.post("/upload", content_type="multipart/form-data",
                           data={"file": (io.BytesIO(b"<score-partwise/>"), name)})
        assert resp.status_code == 302
    assert sorted(p.suffix for p in out.glob("*/input.*")) == [".musicxml", ".mxl", ".xml"]
    page = client.get("/").get_data(as_text=True)
    assert ".musicxml" in page and "sheet music" in page


def test_song_page_lists_the_scores_parts(web_out, monkeypatch):
    import types
    from sheetswap.model import Arrangement
    out, web, client = web_out
    d = out / "abc12345"
    d.mkdir(parents=True)
    meta = web.new_meta("abc12345", "air.musicxml", "auto", "solo")
    meta["status"] = "done"
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "input.musicxml").write_text(PIANO)
    score_input.load_score(d / "input.musicxml").save(d / "score.json")
    calls = []

    def fake(song_dir, opts):
        calls.append(opts)
        return {"svgs": ["<svg></svg>"], "musicxml": None, "midi": None, "mp3": None,
                "warnings": [], "arr": Arrangement(flute=[], beats_per_bar=3, bpm=90)}
    monkeypatch.setattr(web, "_pipeline", lambda: types.SimpleNamespace(arrange_and_render=fake))
    html = client.get("/song/abc12345").get_data(as_text=True)
    assert 'name="part" value="auto" checked' in html and "let it choose (Piano, right hand)" in html
    assert 'name="part" value="1"' in html and "Piano, left hand (G2-G3)" in html
    assert 'name="melody_source"' not in html and 'name="tempo"' not in html     # audio-only choices
    assert 'name="barshift"' not in html                     # MusicXML bar lines are exact
    assert "as written" in html and "It's sheet music" in html
    html = client.get("/song/abc12345?part=1").get_data(as_text=True)
    assert calls[-1].part == "1" and 'name="part" value="1" checked' in html
    assert "part=1" in html                                   # carried into the print link
    # a MIDI file read as a score: its bars were guessed, so they can be moved
    src = score_input.load_score(d / "input.musicxml")
    src.origin = "midi"
    src.save(d / "score.json")
    assert 'name="barshift"' in client.get("/song/abc12345").get_data(as_text=True)


def test_cli_part_option():
    from sheetswap.cli import build_parser
    ap = build_parser()
    assert ap.parse_args(["x.musicxml"]).part == "auto"
    assert ap.parse_args(["x.musicxml", "--part", "Violin I"]).part == "Violin I"
