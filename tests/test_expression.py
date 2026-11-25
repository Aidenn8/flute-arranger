"""Expression marks: what add_expression reads from a performance, how the
marks are engraved (MusicXML) and how the MIDI plays them."""
import json
import queue
import re
import types

import pretty_midi
import pytest

from sheetswap import config, pipeline
from sheetswap.cli import build_parser
from sheetswap.expression import (DYNAMICS, ORN_BARS, add_expression, felt_bpm, tempo_word,
                                  upper_neighbour)
from sheetswap.model import Analysis, Arrangement, ArrangeOptions, BeatGrid, Mark, Note, QNote
from sheetswap.score import write_outputs

BEAT = 0.6                      # s per quarter: 100 bpm


def grid(n_beats=200, beat=BEAT, slow_from=None, slow=1.0):
    """A steady 4/4 grid (optionally slowing down by `slow` per beat from a beat on)."""
    beats, t, gap = [], 0.0, beat
    for i in range(n_beats):
        beats.append(t)
        if slow_from is not None and i >= slow_from:
            gap *= slow
        t += gap
    return BeatGrid(beats=beats, beats_per_bar=4, first_downbeat=0, beat_ticks=48)


def perform(spec, g=None, sound=0.97):
    """spec: [(pitch, start tick, ticks, velocity)] -> (flute QNotes, played Notes).
    A note sounds `sound` of its written length (or the fraction given as a 5th item)."""
    g = g or grid()
    line, played = [], []
    for item in spec:
        p, s, d, v = item[:4]
        frac = item[4] if len(item) > 4 else sound
        line.append(QNote(p, s, d, v))
        t0, t1 = g.to_seconds(s / 48), g.to_seconds((s + d) / 48)
        played.append(Note(p, t0, t0 + frac * (t1 - t0), v))
    return line, played, g


def arrangement(line, **kw):
    kw.setdefault("bpm", 100)
    return Arrangement(flute=line, beats_per_bar=4, **kw)


def kinds(marks, kind):
    return [m for m in marks if m.kind == kind]


# ---------------------------------------------------------------- dynamics


def test_louder_phrases_get_louder_dynamics():
    # four 2-bar phrases (7 quarters + a rest), each played louder than the last
    spec = []
    for k, base in enumerate((44, 60, 76, 92)):
        spec += [(72 + i % 5, 384 * k + 48 * i, 48, base + i) for i in range(7)]
    line, played, g = perform(spec)
    marks = add_expression(arrangement(line), played, g)
    dyn = kinds(marks, "dynamic")
    levels = [DYNAMICS.index(m.value) for m in dyn]
    assert len(dyn) >= 3 and dyn[0].start == 0
    assert levels == sorted(levels) and levels[-1] - levels[0] >= 2
    assert all(b.start - a.start >= 2 * 192 for a, b in zip(dyn, dyn[1:]))  # 2 bars apart
    assert not kinds(marks, "dim")


def test_crescendo_gets_a_hairpin_and_steady_playing_does_not():
    spec = [(72 + i % 4, 48 * i, 48, 50 + 2 * i) for i in range(24)]     # 6 bars, steadily louder
    line, played, g = perform(spec)
    marks = add_expression(arrangement(line), played, g)
    cresc = kinds(marks, "cresc")
    assert cresc and not kinds(marks, "dim")
    assert max(m.end - m.start for m in cresc) >= 192                     # at least a bar
    steady = [(p, s, d, 80) for p, s, d, _ in spec]
    line, played, g = perform(steady)
    marks = add_expression(arrangement(line), played, g)
    assert len(kinds(marks, "dynamic")) == 1
    assert not kinds(marks, "cresc") and not kinds(marks, "dim")


# ---------------------------------------------------------------- articulation and slurs


def test_staccato_when_notes_sound_short_but_not_at_the_phrase_end():
    short = [(72 + i, 48 * i, 48, 80, 0.3) for i in range(6)]            # played detached
    line, played, g = perform(short)
    marks = add_expression(arrangement(line), played, g)
    stacc = {m.start for m in kinds(marks, "staccato")}
    assert stacc == {48 * i for i in range(5)}                            # not the last note
    legato = [(72 + i, 48 * i, 48, 80) for i in range(6)]
    line, played, g = perform(legato)
    assert not kinds(add_expression(arrangement(line), played, g), "staccato")


def test_fast_notes_never_read_as_staccato():
    # 16ths at 100 bpm (0.15 s): too fast to judge from the transcription
    fast = [(72 + i % 3, 12 * i, 12, 80, 0.3) for i in range(16)]
    line, played, g = perform(fast)
    assert not kinds(add_expression(arrangement(line), played, g), "staccato")


def test_slurs_break_at_repeats_leaps_rests_and_staccatos():
    spec = [(72, 0, 48, 80), (74, 48, 48, 80), (76, 96, 48, 80),     # steps: one slur
            (76, 144, 48, 80), (77, 192, 48, 80),                     # repeated E: new slur
            (89, 240, 48, 80), (88, 288, 48, 80),                     # leap of an octave: new slur
            (86, 384, 48, 80), (84, 432, 48, 80, 0.3),                # after a rest; staccato
            (83, 480, 48, 80), (81, 528, 96, 80)]
    line, played, g = perform(spec)
    marks = add_expression(arrangement(line), played, g)
    slurs = sorted((m.start, m.end) for m in kinds(marks, "slur"))
    assert slurs == [(0, 96), (144, 192), (240, 288), (480, 528)]
    assert [m.start for m in kinds(marks, "staccato")] == [432]


def test_long_legato_runs_are_cut_into_slurs_of_two_bars_at_most():
    spec = [(72 + (i % 8 if i % 16 < 8 else 8 - i % 8), 48 * i, 48, 80) for i in range(24)]
    line, played, g = perform(spec)
    slurs = kinds(add_expression(arrangement(line), played, g), "slur")
    assert len(slurs) >= 3
    assert all(m.end - m.start <= 2 * 192 for m in slurs)
    covered = sorted((m.start, m.end) for m in slurs)
    assert all(a[1] < b[0] for a, b in zip(covered, covered[1:]))        # no overlaps


def test_two_notes_further_apart_than_a_slur_get_none():
    # a note held three bars, legato into the next: no slur (it used to recurse forever)
    line, played, g = perform([(72, 0, 576, 80), (74, 576, 48, 80)])
    assert kinds(add_expression(arrangement(line), played, g), "slur") == []


def test_accent_on_a_clearly_louder_weak_beat():
    spec = [(72 + i % 3, 48 * i, 48, 120 if i == 5 else 70) for i in range(12)]
    line, played, g = perform(spec)
    marks = add_expression(arrangement(line), played, g)
    assert [m.start for m in kinds(marks, "accent")] == [240]
    loud_downbeat = [(p, s, d, 120 if s == 192 else 70) for p, s, d, _ in spec]
    line, played, g = perform(loud_downbeat)
    assert not kinds(add_expression(arrangement(line), played, g), "accent")


# ---------------------------------------------------------------- tempo


def test_fermata_on_a_long_last_note_and_rit_where_the_beat_slows():
    spec = [(72 + i % 5, 48 * i, 48, 80) for i in range(30)] + [(72, 1440, 192, 80)]
    g = grid(slow_from=26, slow=1.12)                     # the last two bars slow down
    line, played, g = perform(spec, g)
    marks = add_expression(arrangement(line), played, g)
    assert [m.start for m in kinds(marks, "fermata")] == [1440]
    texts = {m.value: m for m in kinds(marks, "text")}
    assert "rit." in texts and 1152 <= texts["rit."].start <= 1344
    line, played, g = perform(spec)                       # steady: no rit.
    assert "rit." not in {m.value for m in kinds(add_expression(arrangement(line), played, g),
                                                         "text")}


def test_tempo_words_follow_the_metronome_table():
    assert tempo_word(50) == "Largo" and tempo_word(70) == "Adagio"
    assert tempo_word(90) == "Andante" and tempo_word(116) == "Allegretto"
    assert tempo_word(140) == "Allegro" and tempo_word(180) == "Presto"
    assert felt_bpm(60, 72) == 120 and felt_bpm(150, 24) == 100 and felt_bpm(90, 48) == 90
    spec = [(72 + i % 5, 48 * i, 48, 80) for i in range(8)]
    line, played, g = perform(spec)
    words = kinds(add_expression(arrangement(line, bpm=90), played, g), "text")
    assert words[0].start == 0 and words[0].value.split()[0] == "Andante"


# ---------------------------------------------------------------- ornaments


def ornament_tune():
    """16 bars: every 2-bar phrase rises by a leap to a long peak and cadences 2-1."""
    spec = []
    for k in range(8):
        s = 384 * k
        spec += [(67, s, 48, 80), (72, s + 48, 24, 80), (74, s + 72, 24, 80),  # fast notes
                 (79, s + 96, 96, 80),                                         # leap up to the peak
                 (77, s + 192, 48, 80), (74, s + 240, 96, 80), (72, s + 336, 48, 80)]
    return perform(spec)


def test_ornaments_are_sparse_and_only_on_long_notes():
    line, played, g = ornament_tune()
    arr = arrangement(line)
    marks = add_expression(arr, played, g, "ornaments")
    orn = [m for m in marks if m.kind in ("trill", "mordent", "turn", "grace")]
    assert orn
    assert len(orn) <= 16 / ORN_BARS
    starts = sorted(m.start for m in orn)
    assert all(b - a >= ORN_BARS * 192 for a, b in zip(starts, starts[1:]))
    dur = {q.start: q.dur for q in line}
    assert all(dur[m.start] >= 48 for m in orn)                       # never on fast notes
    for m in kinds(marks, "grace"):
        assert int(m.value) == upper_neighbour(next(q.pitch for q in line if q.start == m.start), 0)
    assert not [m for m in add_expression(arr, played, g, "expressive")
                if m.kind in ("trill", "mordent", "turn", "grace")]


def test_cadence_trill_on_the_long_second_degree():
    spec = [(76, 0, 48, 80), (79, 48, 48, 80), (77, 96, 48, 80), (76, 144, 48, 80),
            (74, 192, 144, 80), (72, 336, 48, 80)]                    # ... D (long) - C: a 2-1 cadence
    line, played, g = perform(spec)
    marks = add_expression(arrangement(line), played, g, "ornaments")
    assert [m.start for m in kinds(marks, "trill")] == [192]


# ---------------------------------------------------------------- levels and sheet music input


def test_plain_adds_nothing():
    line, played, g = ornament_tune()
    assert add_expression(arrangement(line), played, g, "plain") == []
    have = [Mark("dynamic", 0, value="f")]
    assert add_expression(arrangement(line, expression=have), played, g, "plain") == have


def test_score_marks_are_kept_and_not_doubled():
    spec = [(72 + i % 5, 48 * i, 48, 80) for i in range(16)]
    line, played, g = perform(spec)
    have = [Mark("dynamic", 0, value="f"), Mark("slur", 0, 144), Mark("staccato", 192),
            Mark("text", 0, value="Allegro")]
    marks = add_expression(arrangement(line, expression=list(have)), played, g, "ornaments")
    assert marks[:len(have)] == have
    assert kinds(marks, "dynamic") == [have[0]]
    assert not kinds(marks, "cresc") and not kinds(marks, "dim")
    assert not [m for m in kinds(marks, "slur")[1:] if m.start <= 144 and m.end >= 0]
    assert [m for m in marks if m.start == 192 and m.kind in ("staccato", "tenuto", "accent")] \
        == [have[2]]
    assert kinds(marks, "text") == [have[3]]


# ---------------------------------------------------------------- engraving


def marked_arrangement():
    flute = [QNote(72 + i % 5, 48 * i, 48) for i in range(16)] + [QNote(74, 768, 96),
                                                                   QNote(72, 864, 96)]
    marks = [Mark("dynamic", 0, value="p"), Mark("cresc", 0, 144), Mark("dynamic", 192, value="f"),
             Mark("slur", 0, 144), Mark("staccato", 192), Mark("staccato", 240),
             Mark("accent", 288), Mark("tenuto", 336), Mark("dim", 384, 672),
             Mark("mordent", 432), Mark("turn", 576), Mark("grace", 528, value="83"),
             Mark("trill", 768), Mark("fermata", 864), Mark("text", 0, value="Andante dolce"),
             Mark("text", 672, 960, "rit."), Mark("dynamic", 768, value="mp")]
    return Arrangement(flute=flute, beats_per_bar=4, bpm=84, expression=marks)


def test_musicxml_has_the_marks(tmp_path):
    xml = write_outputs(marked_arrangement(), "Marks", tmp_path)["musicxml"].read_text()
    for tag in ("dynamics", "slur", "staccato", "tenuto", "accent", "wedge", "fermata",
                "trill-mark", "inverted-mordent", "turn", "grace"):
        assert re.search(f"<{tag}[\\s>/]", xml), tag
    assert len(re.findall(r'<wedge[^>]*type="crescendo"', xml)) == 1
    assert len(re.findall(r'<wedge[^>]*type="diminuendo"', xml)) == 1
    assert '<fermata type="upright"' in xml
    assert re.search(r'<direction placement="below">\s*<direction-type>\s*<dynamics', xml)
    # the tempo words and the metronome mark are one heading above the staff
    head = re.search(r'<direction placement="above"><direction-type><words[^>]*>'
                     r'Andante dolce[^<]*</words></direction-type>\s*<direction-type>\s*<metronome',
                     xml)
    assert head
    assert re.search(r"<words[^>]*>rit\.</words>", xml)


def test_marked_score_renders(tmp_path):
    from sheetswap.render import render_svg
    svgs = render_svg(write_outputs(marked_arrangement(), "Marks", tmp_path)["musicxml"])
    assert svgs and "hairpin" in svgs[0] and "slur" in svgs[0] and "fermata" in svgs[0]


def flute_notes(path):
    pm = pretty_midi.PrettyMIDI(str(path))
    return pm, sorted(pm.instruments[0].notes, key=lambda n: (n.start, n.pitch))


def test_midi_plays_the_marks(tmp_path):
    arr = marked_arrangement()
    pm, notes = flute_notes(write_outputs(arr, "Marks", tmp_path)["midi"])
    by_start = {}
    for n in notes:
        by_start.setdefault(round(n.start, 3), n)
    beat = 60 / 84
    first, stacc = by_start[0.0], by_start[round(4 * beat, 3)]
    assert stacc.velocity > first.velocity                              # f after p (+ cresc)
    assert stacc.end - stacc.start < 0.5 * beat                         # staccato
    assert by_start[round(6 * beat, 3)].velocity >= stacc.velocity + 10  # accent
    assert abs(notes[1].start - notes[0].end) < 1e-3                    # slurred: legato
    assert len(notes) > len(arr.flute) + 10                             # trill, turn, mordent, grace
    assert any(n.pitch == 83 for n in notes)                            # the grace note
    cc = [c.value for c in pm.instruments[0].control_changes if c.number == 11]
    assert cc and cc[0] < max(cc)                                       # the crescendo swells
    times, tempi = pm.get_tempo_changes()
    assert len(tempi) > 2 and min(tempi) < 0.8 * tempi[0]               # rit. and fermata slow down
    last = notes[-1]
    assert last.end - last.start > 2 * beat                             # the fermata holds


def test_midi_velocity_follows_dynamics(tmp_path):
    flute = [QNote(72 + i % 3, 48 * i, 48, 80) for i in range(16)]
    arr = Arrangement(flute=flute, beats_per_bar=4, bpm=100, expression=[
        Mark("dynamic", 0, value="pp"), Mark("dynamic", 192, value="mf"),
        Mark("dynamic", 384, value="ff"), Mark("dynamic", 576, value="p")])
    _, notes = flute_notes(write_outputs(arr, "Dyn", tmp_path)["midi"])
    v = [n.velocity for n in notes]
    assert v[0] < v[4] < v[8] and v[12] < v[8] and v[12] > v[0]
    assert len(set(v[0:4])) == 1


def test_no_marks_plays_as_before(tmp_path):
    flute = [QNote(72, 0, 48, 70), QNote(74, 48, 48, 90)]
    _, notes = flute_notes(write_outputs(Arrangement(flute=flute, bpm=120), "Plain", tmp_path)["midi"])
    assert [(round(n.start, 3), round(n.end, 3)) for n in notes] == [(0.0, 0.5), (0.5, 1.0)]
    pm = pretty_midi.PrettyMIDI(str(tmp_path / "score.mid"))
    assert not pm.instruments[0].control_changes and len(pm.get_tempo_changes()[1]) == 1


# ---------------------------------------------------------------- options, pipeline, page


def test_expression_option_key_form_and_cli():
    keys = {ArrangeOptions(expression=x).key() for x in ("plain", "expressive", "ornaments")}
    assert len(keys) == 3
    assert ArrangeOptions().expression == "expressive"
    assert ArrangeOptions.from_form({"expression": "ornaments"}).expression == "ornaments"
    assert ArrangeOptions.from_form({"expression": "loud"}).expression == "expressive"
    assert build_parser().parse_args(["x.mp3", "--expression", "plain"]).expression == "plain"


def _played_song(tmp_path):
    """A song folder with a finished analysis: a solo piano tune getting louder."""
    notes = [Note(72 + (i % 8 if i % 16 < 8 else 8 - i % 8), i * BEAT,
                  i * BEAT + (0.25 if i % 4 == 3 else 0.58), 50 + 2 * i) for i in range(32)]
    an = Analysis(song_id="song", kind="piano", duration=32 * BEAT, grid=grid(40),
                  sources={"piano": notes}, title="Tune")
    d = tmp_path / "song"
    d.mkdir()
    an.save(d / "analysis.json")
    (d / "meta.json").write_text(json.dumps({"title": "Tune"}))
    return d


def test_pipeline_levels_change_marks_not_notes(tmp_path):
    d = _played_song(tmp_path)
    an = Analysis.load(d / "analysis.json")
    plain = pipeline.arrange(an, ArrangeOptions(expression="plain"))
    expr = pipeline.arrange(an, ArrangeOptions(expression="expressive"))
    orn = pipeline.arrange(an, ArrangeOptions(expression="ornaments"))
    assert plain.expression == []
    assert kinds(expr.expression, "dynamic") and kinds(expr.expression, "slur")
    assert plain.flute == expr.flute == orn.flute
    res = pipeline.arrange_and_render(d, ArrangeOptions())
    assert res["arr"].expression == expr.expression           # survives the cache
    assert "<dynamics" in res["musicxml"].read_text()


def test_song_page_has_the_expression_choice(tmp_path, monkeypatch):
    from sheetswap.web import app as web
    out = tmp_path / "out"
    monkeypatch.setattr(config, "OUT_DIR", out)
    monkeypatch.setattr(web, "_jobs", queue.Queue())
    monkeypatch.setattr(web, "_ensure_worker", lambda: None)
    calls = []

    def fake(song_dir, opts):
        calls.append(opts)
        return {"svgs": ["<svg></svg>"], "musicxml": None, "midi": None, "mp3": None,
                "warnings": [], "arr": Arrangement(flute=[], bpm=90)}

    monkeypatch.setattr(web, "_pipeline", lambda: types.SimpleNamespace(
        analyze=lambda *a: None, arrange_and_render=fake))
    d = out / "abc12345"
    d.mkdir(parents=True)
    meta = web.new_meta("abc12345", "Song.mp3", "auto", "solo")
    meta.update(status="done")
    (d / "meta.json").write_text(json.dumps(meta))
    client = web.app.test_client()
    html = client.get("/song/abc12345").get_data(as_text=True)
    assert 'name="expression" value="expressive" checked' in html
    assert "with ornaments" in html
    html = client.get("/song/abc12345?expression=ornaments").get_data(as_text=True)
    assert calls[-1].expression == "ornaments"
    assert "expression=ornaments" in html                     # the print link keeps the choice


@pytest.mark.parametrize("level", ["expressive", "ornaments"])
def test_marks_sit_on_flute_notes(level):
    line, played, g = ornament_tune()
    marks = add_expression(arrangement(line), played, g, level)
    starts = {q.start for q in line}
    for m in marks:
        if m.kind in ("dynamic", "staccato", "tenuto", "accent", "fermata", "trill", "mordent",
                      "turn", "grace", "slur", "cresc", "dim"):
            assert m.start in starts, m
        if m.kind == "slur":
            assert m.end in starts and m.end > m.start
