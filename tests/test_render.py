import pytest

from sheetswap import config
from sheetswap.model import Arrangement, QNote
from sheetswap.render import render_audio, render_svg
from sheetswap.score import write_outputs


def duet():
    flute = [QNote(72 + (i % 5), 12 * i, 12) for i in range(32)]
    rh = [QNote(p, 24 * i, 24) for i in range(16) for p in (55, 60, 64)]
    lh = [QNote(36, 48 * i, 48) for i in range(8)]
    return Arrangement(flute=flute, rh=rh, lh=lh, bpm=120)


def test_render_svg_has_staff(tmp_path):
    paths = write_outputs(duet(), "Render Test", tmp_path)
    pages = render_svg(paths["musicxml"])
    assert isinstance(pages, list) and pages
    first = pages[0]
    assert first.lstrip().startswith("<svg") or "<svg" in first[:300]
    staves = first.count('class="staff"')
    assert staves >= 3 and staves % 3 == 0          # flute + piano grand staff per system
    assert "Render Test" in first


def test_render_svg_solo(tmp_path):
    arr = Arrangement(flute=[QNote(72, 12 * i, 12) for i in range(16)])
    pages = render_svg(write_outputs(arr, "Solo", tmp_path)["musicxml"])
    assert pages and pages[0].count('class="staff"') >= 1


def test_render_svg_works_on_other_threads(tmp_path):
    # the web server renders on request threads; Verovio's default resource
    # path is only right on the first thread that used it
    import threading
    path = write_outputs(duet(), "Threads", tmp_path)["musicxml"]
    render_svg(path)                                  # first use, this thread
    results = []
    for _ in range(2):
        t = threading.Thread(target=lambda: results.append(len(render_svg(path))))
        t.start()
        t.join()
    assert results == [1, 1]


def test_breath_at_bar_end_moves_before_barline(tmp_path):
    from sheetswap.render import _bar_end_breaths
    flute = [QNote(72, 0, 24), QNote(74, 24, 24, breath_after=True),      # bar 1: ends the bar
             QNote(76, 48, 12, breath_after=True), QNote(77, 60, 36)]     # bar 2: mid-bar
    xml = write_outputs(Arrangement(flute=flute), "B", tmp_path)["musicxml"]
    assert _bar_end_breaths(xml) == {"1": 4.5}           # onset 3 + 0.75 * 2 beats
    import verovio
    from sheetswap.render import _move_breaths
    tk = verovio.toolkit()
    tk.loadFile(str(xml))
    moved = _move_breaths(tk.getMEI(), {"1": 4.5})
    assert 'tstamp="4.500"' in moved and moved.count("<breath") == 2
    svg = render_svg(xml)[0]
    assert svg.count('class="breath"') == 2


def test_render_audio_none_without_soundfont(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOUNDFONT", tmp_path / "missing.sf2")
    midi = write_outputs(duet(), "x", tmp_path)["midi"]
    assert render_audio(midi, tmp_path / "a.mp3") is None


@pytest.mark.skipif(not config.SOUNDFONT.exists(), reason="soundfont not downloaded")
def test_render_audio_mp3(tmp_path):
    midi = write_outputs(duet(), "x", tmp_path)["midi"]
    out = render_audio(midi, tmp_path / "preview.mp3")
    assert out == tmp_path / "preview.mp3"
    data = out.read_bytes()
    assert len(data) > 20_000                    # ~16 s at 128k
    assert data[:3] == b"ID3" or data[0] == 0xFF
