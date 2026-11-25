"""Reading sheet music from pictures (sheetswap.omr).

The fast tests draw pages with OpenCV and stand in for the readers (homr,
Audiveris, oemer); the `slow` tests engrave music, photograph it (Verovio SVG
-> Quick Look PNG) and read it back with each reader that is installed.
"""
import shutil
import subprocess
import sys

import cv2
import numpy as np
import pytest
from music21 import converter, key, meter, note, stream

from sheetswap import omr

SPACING = 12


def drawn_page(width=1400, height=900, staves=3, spacing=SPACING, top=150, gap=180,
               left=120, right=None, system=1):
    """White page with five-line staves and a few noteheads, as grey uint8; every
    `system` staves joined at the left by a system bar line."""
    img = np.full((height, width), 255, np.uint8)
    right = right or width - 120
    for s in range(staves):
        y0 = top + s * gap
        for i in range(5):
            cv2.line(img, (left, y0 + i * spacing), (right, y0 + i * spacing), 0, 2)
        for k, x in enumerate(range(left + 80, right - 40, 70)):
            y = y0 + (k % 9) * spacing // 2
            cv2.ellipse(img, (x, y), (int(spacing * 0.65), int(spacing * 0.5)), -20, 0, 360, 0, -1)
            cv2.line(img, (x + int(spacing * 0.6), y), (x + int(spacing * 0.6), y - 3 * spacing), 0, 2)
    for s in range(0, staves, system):
        last = min(s + system, staves) - 1
        if last > s:
            cv2.line(img, (left, top + s * gap), (left, top + last * gap + 4 * spacing), 0, 3)
    return img


def save(img, path):
    cv2.imwrite(str(path), img)
    return path


# --------------------------------------------------------------------------- page clean-up


def test_staff_spacing_and_staves_found():
    page = drawn_page()
    spacing, thick = omr.staff_spacing(omr._binarize(page))
    assert abs(spacing - SPACING) <= 1 and 1 <= thick <= 3
    staves = omr.find_staves(page, spacing)
    assert len(staves) == 3
    top, bottom, x0, x1 = staves[0]
    assert abs(top - 150) <= 2 and abs(bottom - (150 + 4 * SPACING)) <= 2
    assert x0 <= 125 and x1 >= 1275


def test_prepare_crops_browser_chrome_and_scales_to_target(tmp_path):
    page = drawn_page(width=1100, height=700, staves=2, spacing=8, top=200, gap=140,
                      left=160, right=1060)
    page[:70] = 222                                  # browser bar with an address field
    cv2.rectangle(page, (200, 20), (900, 50), 255, -1)
    cv2.putText(page, "example.com/score", (220, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 40, 1)
    page[70:, :110] = 235                            # side panel
    jpg = tmp_path / "shot.jpg"
    cv2.imwrite(str(jpg), page, [cv2.IMWRITE_JPEG_QUALITY, 70])
    out = tmp_path / "prepared.png"
    info = omr.prepare(jpg, out)
    assert info["staves"] == 2 and abs(info["spacing"] - 8) <= 1
    img = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    again = omr.staff_spacing(omr._binarize(img))[0]
    assert abs(again - omr.TARGET_SPACING) <= 2      # staff lines now TARGET_SPACING apart
    assert len(omr.find_staves(img, again)) == 2
    # the address bar's text and the panels are gone: nothing dark above the first staff
    first_top = omr.find_staves(img, again)[0][0]
    assert (img[: first_top - int(3.5 * again)] < 128).sum() == 0   # (stems reach 3 spaces up)
    assert img.shape[1] < 1100 * info["scale"] + 4 * omr.TARGET_SPACING


def test_prepare_straightens_a_tilted_photo():
    page = drawn_page()
    m = cv2.getRotationMatrix2D((700, 450), 2.0, 1.0)
    tilted = cv2.warpAffine(page, m, (1400, 900), borderValue=255)
    info = omr.prepare(tilted)
    assert abs(abs(info["angle"]) - 2.0) < 0.3
    assert info["staves"] == 3


def test_prepare_evens_out_a_shadow():
    page = drawn_page().astype(np.float32)
    shade = np.linspace(1.0, 0.45, page.shape[1])[None, :]       # dark towards the right
    photo = (page * shade).astype(np.uint8)
    info = omr.prepare(photo)
    img = info["image"]
    assert info["staves"] == 3
    border = img[:, -int(2 * omr.TARGET_SPACING):]
    assert border.min() > 200                       # the shaded margin came out white


def test_transparent_and_dark_mode_pictures(tmp_path):
    rgba = np.zeros((300, 400, 4), np.uint8)       # transparent, black lines
    for i in range(5):
        rgba[100 + i * 10, 20:380] = (0, 0, 0, 255)
    p = save(rgba, tmp_path / "clear.png")
    gray = omr.load_gray(p)
    assert gray[50, 50] == 255 and gray[100, 200] < 50
    dark = 255 - drawn_page()                        # white music on black
    gray = omr.load_gray(save(dark, tmp_path / "dark.png"))
    assert gray[10, 10] == 255 and gray[150, 300] < 50


# --------------------------------------------------------------------------- errors


def test_no_music_is_a_plain_error(tmp_path):
    blank = np.full((600, 900), 255, np.uint8)
    cv2.putText(blank, "Hello, this is just text", (50, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 0, 3)
    p = save(blank, tmp_path / "words.png")
    with pytest.raises(ValueError) as e:
        omr.image_to_musicxml(p, tmp_path / "out")
    assert str(e.value) == ("Couldn't read any music in that image — try a sharper "
                            "screenshot of just the music.")


def test_wrong_file_type(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not a picture")
    with pytest.raises(ValueError, match="PNG, JPG or PDF"):
        omr.image_to_musicxml(p, tmp_path / "out")


def _page_xml(path, pitches, staves=1, ks=0, parts_apart=False):
    """A small one-page MusicXML like the readers write (4/4, quarter notes):
    `staves` staves of one part (a piano), or with `parts_apart` that many parts."""
    parts = []
    for s in range(staves):
        p = stream.PartStaff() if staves > 1 and not parts_apart else stream.Part()
        for b in range(0, len(pitches), 4):
            m = stream.Measure()
            if b == 0:
                m.append(key.KeySignature(ks))
                m.append(meter.TimeSignature("4/4"))
            for midi in pitches[b:b + 4]:
                m.append(note.Note(midi - 24 * s, quarterLength=1))
            p.append(m)
        parts.append(p)
    sc = stream.Score()
    for p in parts:
        sc.insert(0, p)
    if staves > 1 and not parts_apart:
        from music21 import layout
        sc.insert(0, layout.StaffGroup(parts, symbol="brace"))
    sc.write("musicxml", fp=str(path))
    return path


def test_join_pages_in_order(tmp_path):
    a = _page_xml(tmp_path / "a.musicxml", [60, 62, 64, 65, 67, 69, 71, 72], staves=2, ks=1)
    b = _page_xml(tmp_path / "b.musicxml", [72, 71, 69, 67], staves=1, ks=1)
    out = omr.join_musicxml([a, b], tmp_path / "joined.musicxml", title="Two pages")
    sc = converter.parse(str(out))
    assert len(sc.parts) == 2                         # the piano's two staves
    top = [n.pitch.midi for n in sc.parts[0].recurse().notes]
    assert top == [60, 62, 64, 65, 67, 69, 71, 72, 72, 71, 69, 67]
    bars = sc.parts[0].getElementsByClass(stream.Measure)
    assert [m.number for m in bars] == [1, 2, 3]
    low = sc.parts[1].getElementsByClass(stream.Measure)
    assert len(low) == 3 and not list(low[2].recurse().notes)    # page 2 had no left hand
    assert sc.metadata.bestTitle == "Two pages"


def test_oemer_wrapped_step_back_is_repaired(tmp_path):
    # oemer writes a step back of 12 as <forward>65524 (16-bit wrap)
    xml = tmp_path / "page.musicxml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise><part-list><score-part id="P1">'
        '<part-name>P</part-name></score-part></part-list><part id="P1"><measure number="1">'
        '<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type>'
        '</time><clef><sign>G</sign><line>2</line></clef></attributes>'
        '<note><pitch><step>C</step><octave>5</octave></pitch><duration>16</duration>'
        '<voice>1</voice><type>whole</type></note>'
        '<forward><duration>65524</duration></forward>'
        '<note><pitch><step>E</step><octave>4</octave></pitch><duration>12</duration>'
        '<voice>2</voice><type>half</type><dot/></note>'
        '<forward><duration>0</duration></forward>'
        '</measure></part></score-partwise>')
    omr.fix_oemer_xml(xml)
    text = xml.read_text()
    assert "65524" not in text and "<backup><duration>12</duration></backup>" in text
    assert text.count("<forward>") == 0
    sc = converter.parse(str(xml))
    assert [n.offset for n in sc.parts[0].recurse().notes] == [0.0, 1.0]


def _fake_oemer(monkeypatch, calls):
    """Stub the model: each page 'reads' as four notes numbered by page; oemer is
    the only reader."""
    def run(png, xml):
        calls.append(png.name)
        k = len(calls)
        _page_xml(xml, [60 + k, 62 + k, 64 + k, 65 + k])
        return xml
    monkeypatch.setenv("SHEETSWAP_OMR", "oemer")
    monkeypatch.setattr(omr, "_run_oemer", run)
    monkeypatch.setattr(omr, "_ensure_models", lambda: None)


def _fake_readers(monkeypatch, **readers):
    """Every reader installed; each one given is stood in for by `fn(png, xml)` (None:
    it fails), the others must not be called."""
    calls = []

    def make(name, fn):
        def run(png, xml):
            calls.append(name)
            if fn is None:
                raise RuntimeError(f"{name} lost track of a staff")
            fn(png, xml)
            return xml
        return run

    for name in omr.ENGINES:
        monkeypatch.setattr(omr, f"_run_{name}", make(name, readers.get(name)))
    monkeypatch.setattr(omr, "engine_available", lambda name: True)
    monkeypatch.setattr(omr, "_ensure_models", lambda: None)
    monkeypatch.delenv("SHEETSWAP_OMR", raising=False)
    return calls


def test_several_screenshots_become_one_score(tmp_path, monkeypatch):
    calls = []
    _fake_oemer(monkeypatch, calls)
    shots = [save(drawn_page(staves=k), tmp_path / f"shot{k}.png") for k in (1, 2, 3)]
    out = omr.images_to_musicxml(shots, tmp_path / "song")
    assert out == tmp_path / "song" / "omr.musicxml"
    assert calls == ["page01.png", "page02.png", "page03.png"]
    sc = converter.parse(str(out))
    firsts = [m.notes[0].pitch.midi for m in sc.parts[0].getElementsByClass(stream.Measure)]
    assert firsts == [61, 62, 63]                     # pages kept in the order given
    assert (tmp_path / "song" / "omr" / "page02.png").exists()


def test_bad_screenshot_among_several_is_named(tmp_path, monkeypatch):
    _fake_oemer(monkeypatch, [])
    good = save(drawn_page(), tmp_path / "good.png")
    bad = save(np.full((500, 800), 255, np.uint8), tmp_path / "blurry.png")
    with pytest.raises(ValueError, match="blurry.png"):
        omr.images_to_musicxml([good, bad], tmp_path / "song")


# --------------------------------------------------------------------------- rhythm repair
# "work on any ideas to imrpove the accuracy and rhtyhm": bars that add up, rests with
# their real value, the time signature the bars support, notes back on their staff.

def _o_note(step, octave, dur, typ, staff=1, alter=0, dot=False, chord=False):
    """A note as oemer writes it (16 divisions a quarter)."""
    return (f"<note>{'<chord/>' if chord else ''}<pitch><step>{step}</step><alter>{alter}</alter>"
            f"<octave>{octave}</octave></pitch><duration>{dur}</duration><type>{typ}</type>"
            f"{'<dot/>' if dot else ''}<staff>{staff}</staff><voice>1</voice></note>")


def _o_rest(dur, staff=1):
    return f'<note><rest measure="yes" /><duration>{dur}</duration><staff>{staff}</staff></note>'


def _o_score(path, measures, beats=4, beat_type=4, staves=1):
    clefs = ('<clef number="1"><sign>G</sign><line>2</line></clef>'
             + ('<clef number="2"><sign>F</sign><line>4</line></clef>' if staves > 1 else ""))
    time = f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>" if beats else ""
    body = "".join(
        f'<measure number="{k + 1}">'
        + (f"<attributes><divisions>16</divisions><key><fifths>0</fifths></key>{time}"
           f"{'<staves>2</staves>' if staves > 1 else ''}{clefs}</attributes>" if k == 0 else "")
        + m + "</measure>" for k, m in enumerate(measures))
    path.write_text('<?xml version="1.0" encoding="UTF-8"?><score-partwise><part-list>'
                    '<score-part id="P1"><part-name>Piano</part-name></score-part></part-list>'
                    f'<part id="P1">{body}</part></score-partwise>')
    return path


def _bars(path, staff=0):
    sc = converter.parse(str(path))
    out = []
    for m in sc.parts[staff].getElementsByClass(stream.Measure):
        out.append([(n.nameWithOctave if n.isNote else "r", float(n.offset), float(n.quarterLength))
                    for n in m.notesAndRests])
    return out


def test_repair_reads_a_misread_rest_and_keeps_the_pickup(tmp_path):
    """Für Elise in 3/8 as oemer reads it: the 16th rests come out as 8th rests, the
    bars as 9/8, every rest as a whole-bar rest."""
    e = lambda s, o, t: _o_note(s, o, 4, "16th") if t == 16 else _o_note(s, o, 8, "eighth")  # noqa: E731
    bars = [e("E", 5, 16) + e("D", 5, 16),                              # pickup
            "".join(e(s, o, 16) for s, o in [("E", 5), ("D", 5), ("E", 5), ("B", 4), ("D", 5), ("C", 5)]),
            e("A", 4, 8) + _o_rest(8) + e("C", 4, 16) + e("E", 4, 16) + e("A", 4, 16),
            e("B", 4, 8) + _o_rest(12) + e("E", 4, 16) + e("G", 4, 16) + e("B", 4, 16),
            e("C", 5, 8) + _o_rest(8) + e("E", 4, 16) + e("E", 5, 16) + e("D", 5, 16),
            "".join(e(s, o, 16) for s, o in [("E", 5), ("D", 5), ("E", 5), ("B", 4), ("D", 5), ("C", 5)]),
            e("A", 4, 8) + _o_rest(8) + e("C", 4, 16) + e("E", 4, 16) + e("A", 4, 16),
            e("B", 4, 8) + _o_rest(8)]
    xml = _o_score(tmp_path / "elise.musicxml", bars, beats=9, beat_type=8)
    done = omr.repair_rhythm(xml)
    assert done["meter"] == "3/8" and done["fixed"] >= 4
    got = _bars(xml)
    assert sum(q for _, _, q in got[0]) == 0.5                          # the pickup stays short
    assert all(sum(q for _, _, q in b) == 1.5 for b in got[1:-1])       # R-METER-1
    assert got[2] == [("A4", 0.0, 0.5), ("r", 0.5, 0.25), ("C4", 0.75, 0.25), ("E4", 1.0, 0.25),
                      ("A4", 1.25, 0.25)]
    assert 'measure="yes"' not in xml.read_text()                       # rests have their value


def test_repair_takes_the_odd_8th_in_a_16th_run(tmp_path):
    run = [("D", 4, 8, "eighth")] + [(s, o, 4, "16th") for s, o in
                                      [("E", 4), ("F", 4), ("G", 4), ("A", 4), ("B", 4), ("C", 5)]]
    run += [("D", 5, 8, "eighth"), ("A", 4, 4, "16th"), ("B", 4, 4, "16th"), ("C", 5, 8, "eighth"),
            ("D", 5, 4, "16th"), ("E", 5, 4, "16th"), ("F", 5, 4, "16th"), ("G", 5, 4, "16th")]
    bar = "".join(_o_note(s, o, d, t) for s, o, d, t in run)               # 72 divisions for 64
    plain = "".join(_o_note(s, 5, 16, "quarter") for s in "CDEF")
    xml = _o_score(tmp_path / "k545.musicxml", [plain, plain, bar, plain, plain])
    assert omr.repair_rhythm(xml)["meter"] == "4/4"
    got = _bars(xml)[2]
    assert [q for _, _, q in got] == [0.5] + [0.25] * 14                 # D5 and C5 were 16ths
    assert sum(q for _, _, q in got) == 4.0


def test_repair_moves_left_hand_notes_read_on_the_treble_staff(tmp_path):
    """K545's Alberti bass climbs above the bass staff; oemer reads its top notes
    against the treble staff (F3, three ledger lines down): they go back down."""
    rh = _o_note("C", 5, 32, "half") + _o_note("F", 3, 16, "quarter") + _o_note("E", 5, 16, "quarter")
    lh = "".join(_o_note(s, 4, 8, "eighth", staff=2) for s in "CEC")
    bar = rh + "<backup><duration>64</duration></backup>" + lh + _o_rest(40, staff=2)
    full = _o_note("C", 5, 64, "whole") + "<backup><duration>64</duration></backup>" + _o_rest(64, staff=2)
    xml = _o_score(tmp_path / "alberti.musicxml", [full, bar, full], staves=2)
    done = omr.repair_rhythm(xml)
    assert done["moved"] == 1
    top = _bars(xml, 0)[1]
    assert [n for n, _, _ in top] == ["C5", "E5", "r"] or [n for n, _, _ in top][:2] == ["C5", "E5"]
    low = [n for b in _bars(xml, 1) for n, _, _ in b]
    assert "G4" in low                                                 # F3 in the treble = G4 above the bass staff


def test_repair_puts_a_misread_key_signature_back(tmp_path):
    """One system read with 5 flats among systems of 4 (R-ACC-9): its G-flats were the
    misreading's, not the music's."""
    bar = "".join(_o_note(s, 5, 16, "quarter") for s in "CDEF")
    gs = _o_note("G", 4, 16, "quarter", alter=-1) * 4
    key5 = ('<print new-system="yes" /><attributes><key><fifths>-5</fifths></key></attributes>')
    key4 = ('<print new-system="yes" /><attributes><key><fifths>-4</fifths></key></attributes>')
    xml = _o_score(tmp_path / "keys.musicxml", [bar, bar, key5 + gs, gs, key4 + bar, bar])
    text = xml.read_text().replace("<fifths>0</fifths>", "<fifths>-4</fifths>")
    xml.write_text(text)
    done = omr.repair_rhythm(xml)
    assert done["keys"] == 2
    sc = converter.parse(str(xml))
    g = [n.pitch.name for n in sc.parts[0].recurse().notes if n.pitch.step == "G"]
    assert g == ["G"] * 8
    assert {k.sharps for k in sc.recurse().getElementsByClass(key.KeySignature)} == {-4}


def test_repair_leaves_a_right_score_alone(tmp_path):
    plain = "".join(_o_note(s, 5, 16, "quarter") for s in "CDEF")
    xml = _o_score(tmp_path / "ok.musicxml", [plain] * 4)
    done = omr.repair_rhythm(xml)
    assert done["meter"] == "4/4" and done["fixed"] == done["padded"] == done["moved"] == 0
    assert all(b == [(f"{s}5", float(i), 1.0) for i, s in enumerate("CDEF")] for b in _bars(xml))


def test_repair_trusts_a_time_signature_read_off_the_page(tmp_path):
    """Bars of four quarters fit 4/4 and 2/2 alike: a 2/2 oemer never read (it
    writes none, join_musicxml guesses) goes to 4/4, a 2/2 Audiveris or homr read
    on the page stays."""
    plain = "".join(_o_note(s, 5, 16, "quarter") for s in "CDEF")
    guessed = _o_score(tmp_path / "guess.musicxml", [plain] * 4, beats=2, beat_type=2)
    assert omr.repair_rhythm(guessed)["meter"] == "4/4"
    read = _o_score(tmp_path / "read.musicxml", [plain] * 4, beats=2, beat_type=2)
    read.write_text(read.read_text().replace(
        "<part-list>", "<identification><encoding><software>sheetswap.omr: audiveris"
                       "</software></encoding></identification><part-list>"))
    assert omr.repair_rhythm(read)["meter"] == "2/2"


def test_repair_every_part_and_each_pages_divisions(tmp_path):
    """A quartet's second part has an overfull bar (an 8th rest read as a quarter
    rest); page 2 counts 12 divisions a quarter where page 1 counted 16."""
    def part(pid, bars):
        return f'<part id="{pid}">' + "".join(
            f'<measure number="{k + 1}">'
            + ('<attributes><divisions>16</divisions><time><beats>4</beats><beat-type>4</beat-type>'
               '</time><clef><sign>G</sign><line>2</line></clef></attributes>' if k == 0 else "")
            + ('<attributes><divisions>12</divisions></attributes>' if k == 2 else "")
            + b + "</measure>" for k, b in enumerate(bars)) + "</part>"
    q16 = "".join(_o_note(s, 5, 16, "quarter") for s in "CDEF")
    q12 = "".join(_o_note(s, 5, 12, "quarter") for s in "CDEF")
    over = (_o_note("C", 4, 16, "quarter") + _o_rest(16) + _o_note("D", 4, 8, "eighth")
            + "".join(_o_note(s, 4, 16, "quarter") for s in "EF"))
    xml = tmp_path / "q.musicxml"
    xml.write_text('<?xml version="1.0" encoding="UTF-8"?><score-partwise><part-list>'
                   '<score-part id="P1"><part-name>Violin</part-name></score-part>'
                   '<score-part id="P2"><part-name>Viola</part-name></score-part></part-list>'
                   + part("P1", [q16, q16, q12, q12]) + part("P2", [q16, over, q12, q12])
                   + "</score-partwise>")
    done = omr.repair_rhythm(xml)
    assert done["meter"] == "4/4" and done["fixed"] == 1 and done["padded"] == done["left"] == 0
    for p in (0, 1):
        assert all(sum(q for _, _, q in b) == 4.0 for b in _bars(xml, p))
    assert _bars(xml, 0)[3] == [(f"{s}5", float(i), 1.0) for i, s in enumerate("CDEF")]
    assert [n for n, _, _ in _bars(xml, 1)[1]] == ["C4", "r", "D4", "E4", "F4"]


def test_name_option_and_model_failure(tmp_path, monkeypatch):
    calls = _fake_readers(monkeypatch)                  # every reader fails
    with pytest.raises(ValueError, match="Couldn't read any music"):
        omr.image_to_musicxml(save(drawn_page(), tmp_path / "p.png"), tmp_path / "x",
                              name="input.musicxml")
    assert calls == ["homr", "audiveris", "oemer"]      # each one tried, best first


# --------------------------------------------------------------------------- choosing a reader
# "you can do all 4 steps": step 3, a stronger reader. homr first, Audiveris next (it
# also reads choirs and quartets), oemer last; the next one takes over when one fails.


def test_chain_order_env_and_unknown_reader(monkeypatch):
    monkeypatch.setattr(omr, "engine_available", lambda name: name != "audiveris")
    monkeypatch.delenv("SHEETSWAP_OMR", raising=False)
    assert omr.chain() == ["homr", "oemer"]             # not installed: left out
    assert omr.chain("audiveris,oemer") == ["oemer"]
    monkeypatch.setenv("SHEETSWAP_OMR", "oemer,homr")
    assert omr.chain() == ["oemer", "homr"]
    with pytest.raises(ValueError, match="Unknown sheet-music reader"):
        omr.chain("photoscore")


def test_no_reader_installed_is_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.setattr(omr, "engine_available", lambda name: False)
    with pytest.raises(RuntimeError, match="fetch-audiveris"):
        omr.image_to_musicxml(save(drawn_page(), tmp_path / "p.png"), tmp_path / "x")


def test_next_reader_takes_over_when_one_fails(tmp_path, monkeypatch):
    calls = _fake_readers(monkeypatch, audiveris=lambda png, xml: _page_xml(xml, [72, 74, 76, 77]))
    out = omr.image_to_musicxml(save(drawn_page(), tmp_path / "p.png"), tmp_path / "x")
    assert calls == ["homr", "audiveris"]               # homr failed, oemer never needed
    sc = converter.parse(str(out))
    assert [n.pitch.midi for n in sc.recurse().notes] == [72, 74, 76, 77]
    assert "audiveris" in out.read_text()               # <software> names the reader


def test_a_reading_missing_staves_goes_to_the_next_reader(tmp_path, monkeypatch):
    """A choir page (four staves a system): homr reading only one staff a system is
    passed over for a reader that finds all four parts."""
    page = save(drawn_page(height=1300, staves=8, gap=140, system=4), tmp_path / "choir.png")
    calls = _fake_readers(monkeypatch,
                          homr=lambda png, xml: _page_xml(xml, [60, 62, 64, 65] * 4),
                          audiveris=lambda png, xml: _page_xml(xml, [72, 74, 76, 77], staves=4,
                                                               parts_apart=True))
    out = omr.image_to_musicxml(page, tmp_path / "x")
    assert calls == ["homr", "audiveris"]
    assert len(converter.parse(str(out)).parts) == 4
    # no reader does better: the short reading is kept rather than nothing
    calls = _fake_readers(monkeypatch, homr=lambda png, xml: _page_xml(xml, [60, 62, 64, 65]))
    out = omr.image_to_musicxml(page, tmp_path / "y")
    assert calls == ["homr", "audiveris", "oemer"]
    assert [n.pitch.midi for n in converter.parse(str(out)).recurse().notes] == [60, 62, 64, 65]


def test_system_sizes_from_the_opening_bar_lines():
    piano = drawn_page(height=1300, staves=6, gap=150, system=2)
    info = omr.prepare(piano)
    assert info["staves"] == 6 and info["systems"] == [2, 2, 2]
    choir = drawn_page(height=1300, staves=8, gap=140, system=4)
    assert omr.prepare(choir)["systems"] == [4, 4]
    assert omr.prepare(drawn_page())["systems"] == [1, 1, 1]      # a melody: no joins


def test_staves_tilted_their_own_way_are_all_kept():
    """A photo taken at an angle: after straightening, the staves still lean
    differently (perspective), but every one stays inside the crop."""
    page = drawn_page(width=1600, height=1500, staves=6, gap=200, left=150)
    src = np.float32([[0, 0], [1600, 0], [1600, 1500], [0, 1500]])
    dst = np.float32([[60, 0], [1540, 70], [1600, 1440], [0, 1500]])
    photo = cv2.warpPerspective(page, cv2.getPerspectiveTransform(src, dst), (1600, 1500),
                                borderValue=255)
    gray = omr.rotate(photo, omr.deskew_angle(omr._binarize(photo)))
    found = omr.find_staves(gray, SPACING)
    every = omr.find_tilted_staves(gray, SPACING, found)
    assert len(every) == 6 and len(every) > len(found)


def test_unzip_compressed_musicxml(tmp_path):
    import zipfile
    xml = _page_xml(tmp_path / "s.musicxml", [60, 62, 64, 65])
    mxl = tmp_path / "s.mxl"
    with zipfile.ZipFile(mxl, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        z.writestr("score.xml", xml.read_text())
    out = omr.unzip_mxl(mxl, tmp_path / "out.musicxml")
    assert [n.pitch.midi for n in converter.parse(str(out)).recurse().notes] == [60, 62, 64, 65]


def test_join_keeps_every_part_of_a_quartet(tmp_path):
    a = _page_xml(tmp_path / "a.musicxml", [72, 74, 76, 77, 79, 81, 83, 84], staves=4, parts_apart=True)
    b = _page_xml(tmp_path / "b.musicxml", [84, 83, 81, 79], staves=2, parts_apart=True)
    out = omr.join_musicxml([a, b], tmp_path / "q.musicxml", engines=["audiveris"])
    sc = converter.parse(str(out))
    assert len(sc.parts) == 4
    first = [n.pitch.midi for n in sc.parts[0].recurse().notes]
    assert first == [72, 74, 76, 77, 79, 81, 83, 84, 84, 83, 81, 79]
    for p in sc.parts:
        assert [m.number for m in p.getElementsByClass(stream.Measure)] == [1, 2, 3]
    viola = sc.parts[2].getElementsByClass(stream.Measure)          # not on page 2: rests
    assert not list(viola[2].recurse().notes) and viola[2].duration.quarterLength == 4


@pytest.mark.skipif(sys.platform != "darwin", reason="PDF pages are drawn with macOS PDFKit")
def test_pdf_pages_skip_a_title_page(tmp_path, monkeypatch):
    from PIL import Image
    calls = []
    _fake_oemer(monkeypatch, calls)
    title = np.full((1100, 850), 255, np.uint8)
    cv2.putText(title, "Sonata", (250, 400), cv2.FONT_HERSHEY_SIMPLEX, 3, 0, 6)
    pages = [Image.fromarray(title), Image.fromarray(drawn_page(1700, 2200, staves=8, gap=220)),
             Image.fromarray(drawn_page(1700, 2200, staves=5, gap=220))]
    pdf = tmp_path / "score.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    out = omr.image_to_musicxml(pdf, tmp_path / "song")
    assert len(sorted((tmp_path / "song" / "omr" / "pdf01").glob("page*.png"))) == 3
    assert len(calls) == 2                            # the title page was skipped
    sc = converter.parse(str(out))
    assert len(sc.parts[0].getElementsByClass(stream.Measure)) == 2


# --------------------------------------------------------------------------- the real model

MELODY = [  # (midi, quarters): 8 bars of 4/4 in G major
    (67, 1), (71, 1), (74, 1), (71, 1), (72, 2), (69, 2),
    (71, 1), (74, 1), (79, 1), (78, 1), (76, 2), (74, 2),
    (72, 1), (71, 1), (69, 1), (67, 1), (66, 2), (69, 2),
    (67, 0.5), (69, 0.5), (71, 1), (72, 1), (74, 1), (79, 4),
    (78, 1), (76, 1), (74, 1), (72, 1), (71, 2), (74, 2),
    (69, 1), (71, 1), (72, 2),
]
# This melody reads at 1.00; harder engraved pages (accidentals, double stops,
# 16ths) measured 0.88-0.98 on the top line, so 0.85 leaves room without hiding a break.
MIN_PITCH_ACC = 0.85


def _engrave(tmp_path):
    from sheetswap.render import render_svg

    part = stream.Part()
    part.append(key.KeySignature(1))
    part.append(meter.TimeSignature("4/4"))
    for midi, q in MELODY:
        part.append(note.Note(midi, quarterLength=q))
    sc = stream.Score([part.makeMeasures()])
    xml = tmp_path / "melody.musicxml"
    sc.write("musicxml", fp=str(xml))
    svg = render_svg(xml)[0]
    # qlmanage's view is ~800 px wide: shrink the outer size, keep the page
    import re
    m = re.match(r'<svg width="(\d+)px" height="(\d+)px"', svg)
    w, h = int(m.group(1)), int(m.group(2))
    svg = svg.replace(m.group(0), f'<svg width="700px" height="{round(700 * h / w)}px" '
                                  f'viewBox="0 0 {w} {h}"', 1)
    (tmp_path / "melody.svg").write_text(svg)
    subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", str(tmp_path), str(tmp_path / "melody.svg")],
                   check=True, capture_output=True, timeout=60)
    return tmp_path / "melody.svg.png"


def _pitch_accuracy(truth, got):
    """Share of the written notes found, in order (edit-distance alignment)."""
    n, m = len(truth), len(got)
    d = [[i + j if i * j == 0 else 0 for j in range(m + 1)] for i in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (truth[i - 1] != got[j - 1]))
    i, j, hits = n, m, 0
    while i and j:
        same = truth[i - 1] == got[j - 1]
        if d[i][j] == d[i - 1][j - 1] + (not same):
            hits += same
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return hits / n


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("qlmanage") is None, reason="needs macOS Quick Look")
@pytest.mark.parametrize("engine", [None, *omr.ENGINES])
def test_reads_an_engraved_melody(tmp_path, engine):
    if engine and not omr.engine_available(engine):
        pytest.skip(f"{engine} isn't installed")
    png = _engrave(tmp_path)
    out = omr.image_to_musicxml(png, tmp_path / "song", engine=engine)
    sc = converter.parse(str(out))
    got = []
    for n in sc.parts[0].recurse().notes:
        got.append(max(p.midi for p in n.pitches))
    acc = _pitch_accuracy([p for p, _ in MELODY], got)
    assert acc >= MIN_PITCH_ACC, f"pitch accuracy {acc:.2f}"
    ks = sc.recurse().getElementsByClass(key.KeySignature)
    assert ks and ks[0].sharps == 1
    if engine in omr.READS_METER:                       # read off the page, not guessed
        ts = sc.recurse().getElementsByClass(meter.TimeSignature)
        assert ts and ts[0].ratioString == "4/4"


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("qlmanage") is None, reason="needs macOS Quick Look")
def test_reads_a_four_part_chorale(tmp_path):
    """More than two staves a system (a choir): every part comes back, soprano on top."""
    if not omr.engine_available("audiveris"):
        pytest.skip("Audiveris isn't installed")
    from music21 import corpus
    from sheetswap.render import render_svg
    xml = tmp_path / "chorale.musicxml"
    corpus.parse("bach/bwv66.6").measures(1, 8).write("musicxml", fp=str(xml))
    svg = render_svg(xml)[0]
    import re
    m = re.match(r'<svg width="(\d+)px" height="(\d+)px"', svg)
    w, h = int(m.group(1)), int(m.group(2))
    svg = svg.replace(m.group(0), f'<svg width="700px" height="{round(700 * h / w)}px" '
                                  f'viewBox="0 0 {w} {h}"', 1)
    (tmp_path / "chorale.svg").write_text(svg)
    subprocess.run(["qlmanage", "-t", "-s", "2000", "-o", str(tmp_path), str(tmp_path / "chorale.svg")],
                   check=True, capture_output=True, timeout=60)
    out = omr.image_to_musicxml(tmp_path / "chorale.svg.png", tmp_path / "song")
    sc = converter.parse(str(out))
    assert len(sc.parts) == 4
    truth = [n.pitch.midi for n in converter.parse(str(xml)).parts[0].recurse().notes]
    got = [max(p.midi for p in n.pitches) for n in sc.parts[0].recurse().notes]
    assert _pitch_accuracy(truth, got) >= MIN_PITCH_ACC
