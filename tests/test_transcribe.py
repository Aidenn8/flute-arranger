import io
import time

import numpy as np
import pytest
import soundfile as sf

from sheetswap import transcribe
from sheetswap.model import Note

SR = 44100
ARPEGGIO = [(60, 0.5), (64, 1.0), (67, 1.5), (72, 2.0)]     # C4 E4 G4 C5


def synth(path, notes, seconds, note_len=0.5):
    """Piano-ish tones: 8 harmonics, upper ones decaying faster, 3 ms attack."""
    y = np.zeros(int(seconds * SR))
    for pitch, start in notes:
        f0 = 440.0 * 2 ** ((pitch - 69) / 12)
        t = np.arange(int(note_len * SR)) / SR
        release = np.clip((note_len - t) / 0.03, 0, 1)
        tone = sum(np.exp(-(1.5 + 0.8 * k) * t) * np.sin(2 * np.pi * k * f0 * t) / k
                   for k in range(1, 9))
        i = int(start * SR)
        y[i:i + len(t)] += (np.minimum(1, t / 0.003) * release * tone)[: len(y) - i]
    sf.write(path, (0.5 * y / np.abs(y).max()).astype(np.float32), SR)


def matches(notes, pitch, onset, tol=0.06):
    return [n for n in notes if n.pitch == pitch and abs(n.start - onset) <= tol]


# C5 D5 E5 E5 G5 as (pitch, onset, length); the second E5 is sung again
SUNG = [(72, 0.3, 0.45), (74, 0.75, 0.55), (76, 1.3, 0.5), (76, 1.8, 0.45), (79, 2.25, 0.7)]


def sung_line(path, notes, seconds=3.4, vibrato_cents=50.0, rate=5.5, glide=0.04, dip=0.15):
    """A legato sung line: one continuous tone (5 harmonics) whose pitch
    glides from note to note with +-vibrato_cents vibrato at `rate` Hz.
    Between two notes of the same pitch the loudness dips to `dip` (a
    consonant), not to silence; the line fades in and out at its ends."""
    t = np.arange(int(seconds * SR)) / SR
    pitch = np.full(t.shape, float(notes[0][0]))
    for (p0, s0, _), (p1, s1, _) in zip(notes, notes[1:]):
        pitch[t >= s1] = p1
        ramp = (t >= s1 - glide / 2) & (t < s1 + glide / 2)
        pitch[ramp] = p0 + (p1 - p0) * (t[ramp] - (s1 - glide / 2)) / glide
    pitch += vibrato_cents / 100 * np.sin(2 * np.pi * rate * t)
    phase = 2 * np.pi * np.cumsum(440.0 * 2 ** ((pitch - 69) / 12)) / SR
    tone = sum(np.sin(k * phase) / k for k in range(1, 6))
    start, end = notes[0][1], notes[-1][1] + notes[-1][2]
    amp = np.clip(np.minimum((t - start) / 0.04, (end - t) / 0.06), 0, 1)
    for (p0, _, _), (p1, s1, _) in zip(notes, notes[1:]):
        if p0 == p1:
            amp *= 1 - (1 - dip) * np.clip(1 - np.abs(t - s1) / 0.05, 0, 1)
    sf.write(path, (0.4 * amp * tone / np.abs(tone).max()).astype(np.float32), SR)


def test_vocal_line_one_note_per_sung_pitch(tmp_path):
    wav = tmp_path / "sung.wav"
    sung_line(wav, SUNG)
    t0 = time.perf_counter()
    notes = transcribe.vocal_line(wav)
    print(f"vocal_line: {time.perf_counter() - t0:.2f} s, "
          f"{[(n.pitch, round(n.start, 3), round(n.end, 3), n.velocity) for n in notes]}")
    assert [n.pitch for n in notes] == [p for p, _, _ in SUNG]
    for n, (_, onset, _) in zip(notes, SUNG):
        assert abs(n.start - onset) <= 0.08, f"{n} should start near {onset} s"
    assert all(a.end <= b.start for a, b in zip(notes, notes[1:])), "notes overlap"
    assert all(1 <= n.velocity <= 127 for n in notes)


def test_vocal_line_silence(tmp_path):
    wav = tmp_path / "silence.wav"
    sf.write(wav, np.zeros(2 * SR, dtype=np.float32), SR)
    assert transcribe.vocal_line(wav) == []


@pytest.mark.slow
@pytest.mark.parametrize("name", ["piano", "general"])
def test_arpeggio_pitches_and_onsets(tmp_path, name):
    wav = tmp_path / "arp.wav"
    synth(wav, ARPEGGIO, 3.0)
    t0 = time.perf_counter()
    notes = getattr(transcribe, name)(wav)
    print(f"{name}: {time.perf_counter() - t0:.1f} s, "
          f"{[(n.pitch, round(n.start, 3), round(n.dur, 3)) for n in notes]}")
    assert all(isinstance(n, Note) for n in notes)
    for pitch, onset in ARPEGGIO:
        assert matches(notes, pitch, onset), f"{name} missed MIDI {pitch} at {onset} s"
    # No wrong notes. Harmonics of these bright tones can show up as extra notes
    # an octave or two up (same pitch class); blips under 50 ms are ignored, as
    # the melody step drops those anyway.
    played = {p for p, _ in ARPEGGIO}
    wrong = [n for n in notes
             if n.pitch % 12 not in {p % 12 for p in played} and n.dur >= 0.05]
    assert not wrong, f"wrong notes: {wrong}"
    assert all(n.end <= 3.0 + 1e-6 for n in notes), "a note runs past the end of the clip"
    assert notes == sorted(notes, key=lambda n: (n.start, n.pitch))


@pytest.mark.slow
def test_general_melody_over_chords(tmp_path):
    """Basic Pitch on a polyphonic clip: the melody notes are all there."""
    melody = [(72, 0.0), (74, 0.5), (76, 1.0), (77, 1.5), (79, 2.0), (77, 2.5), (76, 3.0)]
    chords = [(p, s) for s in (0.0, 1.0, 2.0, 3.0) for p in (48, 52, 55)]
    wav = tmp_path / "poly.wav"
    synth(wav, melody + chords, 4.0)
    notes = transcribe.general(wav)
    for pitch, onset in melody:
        assert matches(notes, pitch, onset), f"missed MIDI {pitch} at {onset} s"


def test_piano_events_to_notes():
    events = [
        {"midi_note": 64, "onset_time": 1.0, "offset_time": 1.5, "velocity": 128},
        {"midi_note": 60, "onset_time": 0.5, "offset_time": 0.9, "velocity": 0},
        {"midi_note": 62, "onset_time": 0.7, "offset_time": 0.7, "velocity": 50},
    ]
    assert transcribe._piano_events_to_notes(events) == [
        Note(60, 0.5, 0.9, 1), Note(64, 1.0, 1.5, 127)]
    # notes are cut at the end of the audio; ones starting after it vanish
    assert transcribe._piano_events_to_notes(events, duration=1.2) == [
        Note(60, 0.5, 0.9, 1), Note(64, 1.0, 1.2, 127)]
    assert transcribe._piano_events_to_notes(events, duration=0.6) == [Note(60, 0.5, 0.6, 1)]


def test_basic_pitch_events_to_notes():
    events = [(1.0, 1.2, 67, 0.5, [0, 1]), (0.2, 0.6, 60, 1.2, None), (0.3, 0.3, 61, 0.4, None)]
    assert transcribe._bp_events_to_notes(events) == [
        Note(60, 0.2, 0.6, 127), Note(67, 1.0, 1.2, 64)]


def test_piano_checkpoint_present_means_no_download(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe.config, "MODELS_DIR", tmp_path)
    ckpt = tmp_path / transcribe.PIANO_CKPT_NAME
    with open(ckpt, "wb") as f:
        f.truncate(transcribe.PIANO_CKPT_SIZE)          # sparse file, instant

    def no_network(*a, **k):
        raise AssertionError("tried to download")
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", no_network)
    assert transcribe.piano_checkpoint() == ckpt


def test_piano_checkpoint_bad_download_is_discarded(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe.config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(transcribe.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(b"<html>not a model</html>"))
    with pytest.raises(RuntimeError, match="checksum"):
        transcribe.piano_checkpoint()
    assert list(tmp_path.iterdir()) == []
