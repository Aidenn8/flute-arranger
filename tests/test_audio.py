import subprocess
import time

import imageio_ffmpeg
import numpy as np
import pytest
import soundfile as sf

from sheetswap import audio, separate

SR = 44100


def tone(freq, seconds, sr=SR, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def peak_freq(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(spec)]


def encode(src_wav, dst):
    """Make a compressed copy of a wav with the bundled ffmpeg."""
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y",
           "-i", str(src_wav), str(dst)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        pytest.skip(f"bundled ffmpeg cannot write {dst.suffix}")


def test_load_audio_resamples_to_44k_mono(tmp_path):
    src = tmp_path / "in.wav"
    sf.write(src, np.stack([tone(440, 2.0, 22050), tone(440, 2.0, 22050)], axis=1), 22050)
    dst = tmp_path / "out" / "audio.wav"
    dur = audio.load_audio(src, dst)
    info = sf.info(dst)
    assert (info.samplerate, info.channels, info.subtype) == (44100, 1, "PCM_16")
    assert dur == pytest.approx(2.0, abs=0.01)
    x, _ = sf.read(dst)
    assert peak_freq(x, SR) == pytest.approx(440, abs=2)


@pytest.mark.parametrize("ext", ["mp3", "m4a", "aac", "flac", "ogg"])
def test_load_audio_decodes_compressed_formats(tmp_path, ext):
    wav = tmp_path / "in.wav"
    sf.write(wav, tone(440, 1.5), SR)
    src = tmp_path / f"song.{ext}"
    encode(wav, src)
    dst = tmp_path / "audio.wav"
    dur = audio.load_audio(src, dst)
    assert dur == pytest.approx(1.5, abs=0.1)
    x, sr = sf.read(dst)
    assert sr == 44100 and x.ndim == 1
    assert peak_freq(x, sr) == pytest.approx(440, abs=3)


def test_upload_rejects_non_audio(tmp_path):
    dst = tmp_path / "audio.wav"
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    text = tmp_path / "notes.mp3"
    text.write_text("this is not a song\n" * 200)
    for bad in (empty, text, tmp_path / "missing.mp3"):
        with pytest.raises(ValueError, match="not audio"):
            audio.load_audio(bad, dst)
        assert not dst.exists()

    # a 30-minute file is refused after decoding at most 12 min + 1 s
    long = tmp_path / "long.flac"
    sf.write(long, np.zeros(1800 * 8000, dtype=np.float32), 8000)
    t0 = time.perf_counter()
    with pytest.raises(ValueError, match="too long"):
        audio.load_audio(long, dst)
    assert time.perf_counter() - t0 < 20
    assert not dst.exists()


def test_load_audio_can_overwrite_its_input(tmp_path):
    p = tmp_path / "song.wav"
    sf.write(p, tone(440, 1.0, 48000), 48000)
    assert audio.load_audio(p, p) == pytest.approx(1.0, abs=0.01)
    assert sf.info(p).samplerate == 44100
    assert [f.name for f in tmp_path.iterdir()] == ["song.wav"]


def test_load_audio_max_seconds(tmp_path):
    src = tmp_path / "in.wav"
    sf.write(src, tone(330, 3.0), SR)
    with pytest.raises(ValueError, match="too long"):
        audio.load_audio(src, tmp_path / "a.wav", max_seconds=2)
    assert audio.load_audio(src, tmp_path / "b.wav", max_seconds=3) == pytest.approx(3.0, abs=0.01)


def test_load_stereo(tmp_path):
    src = tmp_path / "in.wav"
    sf.write(src, np.stack([tone(440, 1.0, 48000), tone(660, 1.0, 48000)], axis=1), 48000)
    dst = tmp_path / "stereo.wav"
    assert audio.load_stereo(src, dst) is None
    x, sr = sf.read(dst)
    assert sr == 44100 and x.shape[1] == 2
    assert peak_freq(x[:, 0], sr) == pytest.approx(440, abs=3)
    assert peak_freq(x[:, 1], sr) == pytest.approx(660, abs=3)
    with pytest.raises(ValueError, match="not audio"):
        bad = tmp_path / "bad.m4a"
        bad.write_bytes(b"\x00" * 1000)
        audio.load_stereo(bad, dst)


def write_stems(tmp_path, amps):
    rng = np.random.default_rng(0)
    paths = {}
    for name, amp in amps.items():
        p = tmp_path / f"{name}.wav"
        sf.write(p, (amp * rng.standard_normal(SR * 2) * 0.1).astype(np.float32), SR)
        paths[name] = p
    return paths


def test_classify_song(tmp_path):
    stems = write_stems(tmp_path, {"vocals": 0.6, "drums": 0.5, "bass": 0.5, "other": 1.0})
    assert separate.classify(stems) == "song"


def test_classify_piano(tmp_path):
    stems = write_stems(tmp_path, {"vocals": 0.2, "drums": 0.1, "bass": 0.15, "other": 1.0})
    assert separate.classify(stems) == "piano"


def test_classify_band(tmp_path):
    stems = write_stems(tmp_path, {"vocals": 0.1, "drums": 0.6, "bass": 0.4, "other": 1.0})
    assert separate.classify(stems) == "band"


def synthetic_band(seconds=15.0):
    """Melody + bass made of decaying harmonic tones, plus noise-burst 'drums'.

    Returns (mix, drums_only, burst_onsets_in_seconds).
    """
    rng = np.random.default_rng(1)
    n = int(seconds * SR)
    mix = np.zeros(n, np.float32)
    drums = np.zeros(n, np.float32)

    def note(midi, start, dur, amp, harmonics=6):
        f0 = 440.0 * 2 ** ((midi - 69) / 12)
        t = np.arange(int(dur * SR)) / SR
        env = np.exp(-3.0 * t) * np.minimum(1, t / 0.005)
        y = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, harmonics + 1))
        i = int(start * SR)
        mix[i:i + len(t)] += (amp * env * y)[: n - i]

    melody = [72, 74, 76, 77, 79, 77, 76, 74]
    for k in range(int(seconds / 0.5)):
        note(melody[k % len(melody)], k * 0.5, 0.5, 0.25)
    for k in range(int(seconds / 2.0)):
        note([36, 41, 43, 36][k % 4], k * 2.0, 2.0, 0.35, harmonics=4)
    onsets = np.arange(0.0, seconds - 0.2, 0.5)
    hit = rng.standard_normal(int(0.12 * SR)) * np.exp(-np.arange(int(0.12 * SR)) / (0.02 * SR))
    for t0 in onsets:
        i = int(t0 * SR)
        drums[i:i + len(hit)] += 0.5 * hit
    return mix + drums, drums, onsets


@pytest.mark.slow
def test_separate_runs_on_synthetic_mix(tmp_path):
    mix, _, onsets = synthetic_band(15.0)
    wav = tmp_path / "mix.wav"
    sf.write(wav, mix / np.abs(mix).max() * 0.8, SR)
    t0 = time.perf_counter()
    stems = separate.separate(wav, tmp_path / "stems")
    elapsed = time.perf_counter() - t0
    print(f"demucs on 15 s: {elapsed:.1f} s")
    assert set(stems) == {"vocals", "drums", "bass", "other"}
    for p in stems.values():
        info = sf.info(p)
        assert (info.samplerate, info.channels) == (44100, 1)
        assert info.frames == len(mix)

    # the noise bursts should land in the drums stem, right on the hits
    d, _ = sf.read(stems["drums"])
    on = np.zeros(len(d), bool)
    for t in onsets:
        on[int(t * SR):int((t + 0.08) * SR)] = True
    assert np.mean(d[on] ** 2) > 10 * np.mean(d[~on] ** 2)
    energy = {k: np.mean(sf.read(p)[0] ** 2) for k, p in stems.items()}
    assert energy["drums"] > energy["vocals"]
    assert separate.classify(stems) in ("band", "piano", "song")
    assert elapsed < 60
