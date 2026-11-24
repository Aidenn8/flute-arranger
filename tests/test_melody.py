import random
import time

from sheetswap.melody import clean_monophonic, top_voice
from sheetswap.model import Note


def _rows(line):
    return [(n.pitch, round(n.start, 3), round(n.end, 3)) for n in line]


def _is_monophonic(line):
    return all(a.start < b.start and a.end <= b.start + 1e-9 and a.end > a.start
               for a, b in zip(line, line[1:]))


def _same(a, b, tol=0.03):
    return a.pitch == b.pitch and abs(a.start - b.start) <= tol


def test_top_voice_melody_over_chords():
    melody = [Note(p, i * 0.5, i * 0.5 + 0.5, 90) for i, p in enumerate([72, 74, 76, 77])]
    chords = [Note(p, i * 0.5, i * 0.5 + 0.5, 70) for i in range(4) for p in (48, 52, 55)]
    out = top_voice(chords + melody)
    assert _rows(out) == _rows(melody)


def _mary(start, beat=0.5):
    """'Mary had a little lamb' with rests, notes a bit detached."""
    tune = [(76, 1), (74, 1), (72, 1), (74, 1), (76, 1), (76, 1), (76, 1), (None, 1),
            (74, 1), (74, 1), (74, 1), (None, 1), (76, 1), (79, 1), (79, 1), (None, 1),
            (76, 1), (74, 1), (72, 1), (74, 1), (76, 1), (76, 1), (76, 1), (76, 1),
            (74, 1), (74, 1), (76, 1), (74, 1), (72, 2), (None, 2)]
    out, t = [], start
    for p, beats in tune:
        if p is not None:
            out.append(Note(p, t, t + 0.9 * beats * beat, 85))
        t += beats * beat
    return out


def test_top_voice_ignores_sparse_low_chords():
    # 8 s of low whole-note chords alone, then the tune over more of them
    voicings = [(48, 55, 64), (43, 50, 59), (41, 53, 57), (43, 47, 55)]
    chords = []
    for k in range(12):                       # 0..24 s, one chord every 2 s
        for p in voicings[k % 4]:
            chords.append(Note(p, 2.0 * k, 2.0 * k + 2.0, 70))
    melody = _mary(8.0)
    out = top_voice(chords + melody)
    after = [n for n in out if n.start >= 8.0 - 1e-6]
    assert all(any(_same(n, m) for m in melody) for n in after), _rows(after)
    assert all(any(_same(n, m) for n in out) for m in melody)
    assert _is_monophonic(out)


def test_top_voice_alberti_with_inner_chord_tones():
    rng = random.Random(7)
    beat = 0.5
    tune = [72, 74, 76, 77, 79, 77, 76, 74, 72, 76, 79, None, 77, 74, 72, 74,
            76, 77, 79, 79, 77, 76, 74, 72, 74, 76, 74, None, 72, 74, 76, 72]
    melody = []
    for i, p in enumerate(tune):              # starts after a one-bar intro
        if p is None:
            continue
        s = (4 + i) * beat + rng.uniform(-0.015, 0.015)
        melody.append(Note(p, s, s + beat * rng.uniform(0.85, 1.0), rng.randint(80, 95)))
    accomp = []
    for bar in range(9):
        b0 = bar * 4 * beat
        for k, p in enumerate([48, 55, 52, 55] * 2):      # Alberti bass in eighths
            s = b0 + k * beat / 2 + rng.uniform(-0.01, 0.01)
            accomp.append(Note(p, s, s + 0.3, rng.randint(50, 65)))
        for p in (64, 67):                                # held chord tones under the tune
            accomp.append(Note(p, b0, b0 + 4 * beat - 0.05, 60))
    out = top_voice(accomp + melody)
    hits = sum(any(_same(n, m) for n in out) for m in melody)
    extra = sum(not any(_same(n, m) for m in melody) for n in out)
    assert hits >= 0.9 * len(melody), (hits, len(melody))
    assert extra <= 0.1 * len(melody), (extra, _rows(out))
    assert _is_monophonic(out)


def test_top_voice_pedal_held_notes():
    # with the sustain pedal down, transcribers report every note ringing to the
    # next pedal change, so the tune's notes overlap each other and the chords
    tune = [72, 74, 76, 77, 79, 77, 76, 74, 72, 76, 79, 84, 79, 76, 74, 72]
    notes, melody = [], []
    for i, p in enumerate(tune):
        s = i * 0.5
        bar_end = (i // 4 + 1) * 2.0
        melody.append(Note(p, s, bar_end + 0.05, 90))
        for q in (48, 52, 55, 60):
            notes.append(Note(q, s, bar_end + 0.05, 60))
    out = top_voice(notes + melody)
    assert [(n.pitch, round(n.start, 3)) for n in out] == [(m.pitch, m.start) for m in melody]
    assert _is_monophonic(out)


def test_top_voice_keeps_held_note_over_moving_inner_voice():
    # chorale-like: the tune holds half notes while the alto moves in eighths
    # a third to a fifth below; the line must not dip into the alto
    melody = [Note(p, s, s + dur - 0.02, 90) for p, s, dur in
              [(76, 0, 1.0), (74, 1.0, 1.0), (72, 2.0, 2.0), (74, 4.0, 1.0), (76, 5.0, 1.0), (72, 6.0, 2.0)]]
    alto = [72, 71, 69, 71, 69, 67, 69, 67, 64, 65, 67, 69, 72, 71, 69, 67]
    notes = [Note(p, k * 0.5, k * 0.5 + 0.48, 65) for k, p in enumerate(alto)]
    notes += [Note(48, k * 1.0, k * 1.0 + 0.98, 60) for k in range(8)]
    out = top_voice(notes + melody)
    assert [(n.pitch, n.start) for n in out] == [(m.pitch, m.start) for m in melody]
    assert [round(n.end, 2) for n in out] == [round(m.end, 2) for m in melody]


def test_top_voice_long_rest_over_busy_accompaniment():
    # more than 200 accompaniment notes pass while the tune rests for 3 s; the
    # line must rest too, then pick the tune up again, keeping the (longer)
    # phrase before the rest
    first = [Note(72 + (i % 5), i * 0.5, i * 0.5 + 0.45, 90) for i in range(16)]
    second = [Note(79 - i, 11.0 + i * 0.5, 11.0 + i * 0.5 + 0.45, 90) for i in range(6)]
    rng = random.Random(5)
    busy = []
    for k in range(14 * 75):                  # 75 notes a second, well under the tune
        s = k / 75
        busy.append(Note(rng.randint(36, 59), s, s + 0.06, 50))
    out = top_voice(busy + first + second)
    assert all(any(_same(n, m) for n in out) for m in first + second)
    assert all(any(_same(n, m) for m in first + second) for n in out), _rows(out)


def test_top_voice_fast_on_long_piece():
    rng = random.Random(1)
    notes = []
    for _ in range(4000):                     # ~4 minutes of dense piano
        s = rng.uniform(0, 240)
        notes.append(Note(rng.randint(36, 96), s, s + rng.uniform(0.03, 1.5), rng.randint(30, 110)))
    before = [(n.pitch, n.start, n.end, n.velocity) for n in notes]
    t0 = time.perf_counter()
    out = top_voice(notes)
    assert time.perf_counter() - t0 < 2.0
    assert out and _is_monophonic(out)
    assert [(n.pitch, n.start, n.end, n.velocity) for n in notes] == before   # input untouched


def test_top_voice_empty():
    assert top_voice([]) == []
    assert top_voice([Note(60, 0.0, 0.02)]) == []


def test_clean_monophonic_merges_vibrato_splits():
    notes = [Note(69, 0.00, 0.30, 70), Note(70, 0.30, 0.34, 40), Note(69, 0.34, 0.60, 75),
             Note(69, 0.65, 1.00, 72),                       # vibrato wobble split into pieces
             Note(72, 1.20, 1.80, 90), Note(71, 1.50, 1.70, 40),   # quiet ghost under it
             Note(74, 2.00, 2.04, 80)]                       # 40 ms blip
    out = clean_monophonic(notes)
    assert [(n.pitch, round(n.start, 2), round(n.end, 2)) for n in out] == [(69, 0.0, 1.0), (72, 1.2, 1.8)]
    assert out[0].velocity == 75
    assert _is_monophonic(out)


def test_clean_monophonic_keeps_louder_at_overlap():
    out = clean_monophonic([Note(60, 0.0, 1.0, 50), Note(64, 0.5, 1.2, 90)])
    assert _rows(out) == [(60, 0.0, 0.5), (64, 0.5, 1.2)]
    out = clean_monophonic([Note(60, 0.0, 1.0, 90), Note(64, 0.5, 1.2, 50)])
    assert _rows(out) == [(60, 0.0, 1.0), (64, 1.0, 1.2)]
