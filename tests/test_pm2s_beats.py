"""PM2S (sheetswap.pm2s_beats): beats, bars and note values read from played notes."""
import numpy as np
import pytest

from sheetswap import pipeline, pm2s_beats
from sheetswap.model import BeatGrid, Note

needs_pm2s = pytest.mark.skipif(not pm2s_beats.available(),
                                reason="PM2S not installed (scripts/install_pm2s.sh)")


def _waltz(bars=32, spb=0.5):
    """A steady waltz played on the piano: a bass note on 1, chords on 2 and 3, a tune
    in quarters on top (a little uneven, as played)."""
    rng = np.random.default_rng(0)
    notes = []
    for b in range(bars):
        t0 = b * 3 * spb
        notes.append(Note(43 + 5 * (b % 2), t0, t0 + 0.9 * spb, 72))
        for k in (1, 2):
            notes += [Note(p, t0 + k * spb, t0 + k * spb + 0.6 * spb, 52) for p in (55, 59, 62)]
        for k, p in enumerate((74, 76, 79)):
            s = t0 + k * spb + rng.uniform(-0.01, 0.01)
            notes.append(Note(p + b % 3, s, t0 + (k + 1) * spb - 0.04, 88))
    return notes


@needs_pm2s
def test_reads_a_steady_waltz():
    notes = _waltz()
    r = pm2s_beats.read(notes)
    assert r is not None and r.time_signature in ("3/4", "6/8", "2/4", "4/4")
    ibi = np.median(np.diff(r.beats))
    assert 0.45 < ibi < 0.55                       # one beat a quarter, as played
    assert len(r.hands) == len(r.position) == len(r.value) == len(notes)
    assert sorted(r.order) == list(range(len(notes)))
    g = pm2s_beats.grid(r)
    assert g.tracker == "pm2s" and g.beats_per_bar == r.beats_per_bar
    assert 0 <= g.first_downbeat < g.beats_per_bar


def test_too_few_notes_read_nothing():
    assert pm2s_beats.read([Note(60, 0.0, 0.5)] * 3) is None


def test_quantize_tune_places_notes_where_pm2s_says():
    # PM2S's beats every 0.5 s; it puts the 3 notes on beat 0, beat 0 + an 8th, beat 1
    reading = pm2s_beats.Reading(beats=[0.5 * i for i in range(9)], downbeats=[0.0, 2.0],
                                 beats_per_bar=4, first_downbeat=0, time_signature="4/4",
                                 order=[0, 1, 2], position=[0.0, 0.5, 1.0], value=[0.5, 0.5, 1.0])
    played = [Note(72, 0.02, 0.2), Note(74, 0.29, 0.45), Note(76, 0.47, 0.98)]
    grid = BeatGrid([0.5 * i for i in range(9)], 4, 0)
    line = pm2s_beats.quantize_tune(played, played, reading, grid)
    assert [(q.pitch, q.start, q.dur) for q in line] == [(72, 0, 24), (74, 24, 24), (76, 48, 48)]


def test_piano_beats_from_pm2s_only_when_asked(monkeypatch, tmp_path):
    reading = pm2s_beats.Reading(beats=[0.5 * i for i in range(8)], downbeats=[0.0, 1.5],
                                 beats_per_bar=3, first_downbeat=0, time_signature="3/4")
    monkeypatch.setattr(pm2s_beats, "read", lambda notes, **kw: reading)
    sources = {"piano": [Note(60, 0.0, 0.4)] * 20}
    monkeypatch.setenv("SHEETSWAP_PIANO_BEATS", "pm2s")
    g = pipeline.find_beats(tmp_path / "audio.wav", {}, "piano", sources)
    assert g.tracker == "pm2s" and g.beats == reading.beats and g.beats_per_bar == 3
