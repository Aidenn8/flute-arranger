"""Reading sheet music from pictures (sheetswap.omr).

The fast tests draw pages with OpenCV and stub out the OMR model; the one
`slow` test engraves a melody, photographs it (Verovio SVG -> Quick Look PNG)
and reads it back with the real model.
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
               left=120, right=None):
    """White page with five-line staves and a few noteheads, as grey uint8."""
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


def _page_xml(path, pitches, staves=1, ks=0):
    """A small one-page MusicXML like oemer writes (4/4, quarter notes)."""
    parts = []
    for s in range(staves):
        p = stream.PartStaff() if staves > 1 else stream.Part()
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
    if staves > 1:
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
    """Stub the model: each page 'reads' as four notes numbered by page."""
    def run(png, xml):
        calls.append(png.name)
        k = len(calls)
        _page_xml(xml, [60 + k, 62 + k, 64 + k, 65 + k])
        return xml
    monkeypatch.setattr(omr, "_run_oemer", run)
    monkeypatch.setattr(omr, "_ensure_models", lambda: None)


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


def test_name_option_and_model_failure(tmp_path, monkeypatch):
    def broken(png, xml):
        raise IndexError("oemer lost track of a staff")
    monkeypatch.setattr(omr, "_run_oemer", broken)
    monkeypatch.setattr(omr, "_ensure_models", lambda: None)
    with pytest.raises(ValueError, match="Couldn't read any music"):
        omr.image_to_musicxml(save(drawn_page(), tmp_path / "p.png"), tmp_path / "x",
                              name="input.musicxml")


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
def test_reads_an_engraved_melody(tmp_path):
    png = _engrave(tmp_path)
    out = omr.image_to_musicxml(png, tmp_path / "song")
    sc = converter.parse(str(out))
    got = []
    for n in sc.parts[0].recurse().notes:
        got.append(max(p.midi for p in n.pitches))
    acc = _pitch_accuracy([p for p, _ in MELODY], got)
    assert acc >= MIN_PITCH_ACC, f"pitch accuracy {acc:.2f}"
    ks = sc.recurse().getElementsByClass(key.KeySignature)
    assert ks and ks[0].sharps == 1
