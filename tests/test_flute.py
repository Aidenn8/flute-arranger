import random

from sheetswap.config import FLUTE_HARD
from sheetswap.flute import easy_key_shift, fit_to_flute, key_after_shift
from sheetswap.model import QNote


def _line(pitches, dur=12, gap=0, start=0):
    out, t = [], start
    for p in pitches:
        out.append(QNote(p, t, dur))
        t += dur + gap
    return out


def _inside(line):
    lo, hi = FLUTE_HARD
    return all(lo <= n.pitch <= hi for n in line)


def test_fit_moves_bass_melody_up():
    line = _line([48, 50, 52, 53, 55, 53, 52, 50, 48, 43, 47, 48])
    out, shift = fit_to_flute(line)
    assert _inside(out)
    assert shift in (24, 36)
    assert [n.pitch - m.pitch for n, m in zip(out, line)] == [shift] * len(line)


def test_fit_all_in_hard_range():
    rng = random.Random(3)
    for _ in range(20):
        line = _line([rng.randint(20, 110) for _ in range(60)],
                     dur=rng.choice([3, 6, 12]), gap=rng.choice([0, 6, 12, 24]))
        before = [(n.pitch, n.start, n.dur) for n in line]
        for octave in (-1, 0, 1):
            out, shift = fit_to_flute(line, octave)
            assert _inside(out)
            assert shift % 12 == 0
            assert [(n.start, n.dur) for n in out] == [(n.start, n.dur) for n in line]
            assert all((n.pitch - m.pitch) % 12 == 0 for n, m in zip(out, line))
        assert [(n.pitch, n.start, n.dur) for n in line] == before   # input untouched


def test_fold_moves_whole_phrase():
    low = [60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60]
    high = [86, 89, 91, 93, 96, 98, 96, 93]          # one note above C7
    line = _line(low) + _line(high, start=len(low) * 12 + 24)   # two-beat rest between
    out, shift = fit_to_flute(line)
    assert shift == 0
    assert _inside(out)
    assert [n.pitch for n in out[:len(low)]] == low
    assert [n.pitch for n in out[len(low):]] == [p - 12 for p in high]


def test_fold_single_note_when_phrase_cannot_move():
    line = _line([60, 66, 72, 78, 84, 90, 96, 98, 96, 90])   # one phrase spanning C4..D7
    out, shift = fit_to_flute(line)
    assert shift == 0
    assert [n.pitch for n in out] == [60, 66, 72, 78, 84, 90, 96, 86, 96, 90]


def test_octave_override():
    line = _line([72, 74, 76, 77, 79, 81, 79, 77, 76, 74, 72])
    base, s0 = fit_to_flute(line)
    up, s1 = fit_to_flute(line, octave=1)
    down, s2 = fit_to_flute(line, octave=-1)
    assert s0 == 0 and s1 == 12 and s2 == -12
    assert [n.pitch for n in up] == [n.pitch + 12 for n in base]
    assert [n.pitch for n in down] == [n.pitch - 12 for n in base]
    # an octave up would push a quarter of this line past C7, so it's ignored
    wide = _line(list(range(62, 94, 2)))
    out, s = fit_to_flute(wide, octave=1)
    assert s == 0 and [n.pitch for n in out] == [n.pitch for n in wide]


def test_fit_keeps_other_fields():
    line = [QNote(48, -6, 6, 70), QNote(50, 0, 12, 99, True)]
    out, shift = fit_to_flute(line)
    assert [(n.start, n.dur, n.velocity, n.breath_after) for n in out] == \
           [(-6, 6, 70, False), (0, 12, 99, True)]
    assert fit_to_flute([]) == ([], 0)


def test_easy_key_shift():
    s = easy_key_shift(5)
    assert s != 0 and abs(s) <= 6
    assert abs(key_after_shift(5, s)) <= 2
    for fifths in range(-7, 8):
        s = easy_key_shift(fifths)
        assert -5 <= s <= 6
        assert abs(key_after_shift(fifths, s)) <= 2
        if abs(fifths) <= 2:
            assert s == 0


def test_key_after_shift():
    assert key_after_shift(0, 2) == 2       # C -> D
    assert key_after_shift(0, -2) == -2     # C -> B-flat
    assert key_after_shift(0, 1) == -5      # C -> D-flat, not C-sharp (7 sharps)
    assert key_after_shift(0, 6) == -6      # C -> G-flat: 6 vs -6 tie goes to flats
    assert key_after_shift(-1, 7) == 0      # F -> C
    assert key_after_shift(5, 1) == 0       # B -> C
    assert key_after_shift(3, 0) == 3
    assert key_after_shift(7, 0) == -5      # C-sharp respelled as D-flat
    assert key_after_shift(-3, 12) == -3


def test_octave_slip_is_fixed_but_real_leaps_stay():
    def line(pitches):
        return [QNote(p, 12 * i, 12) for i, p in enumerate(pitches)]
    out, _ = fit_to_flute(line([76, 77, 64, 76, 74]))        # E5 F5 E4 E5 D5: E4 is a slip
    assert [n.pitch for n in out] == [76, 77, 76, 76, 74]
    out, _ = fit_to_flute(line([72, 84, 83, 79, 77]))        # C5 C6 B5: a real octave leap
    assert [n.pitch for n in out] == [72, 84, 83, 79, 77]
    out, _ = fit_to_flute(line([79, 67, 79]))                # G5 G4 G5 (first/last untouched)
    assert [n.pitch for n in out][1] == 79


def test_phrase_edge_slips_fixed_but_octave_leap_kept():
    def line(pitches):
        return [QNote(p, 12 * i, 12) for i, p in enumerate(pitches)]
    out, _ = fit_to_flute(line([95, 80, 81, 83, 81]))     # B6 then G#5...: B6 is a slip
    first, second = out[0].pitch, out[1].pitch
    assert first % 12 == 95 % 12 and abs(first - second) <= 12   # B, now near the G#
    out, _ = fit_to_flute(line([72, 84, 83, 79, 77]))     # C5 C6: an octave leap stays
    assert [n.pitch for n in out][:2] == [72, 84]
