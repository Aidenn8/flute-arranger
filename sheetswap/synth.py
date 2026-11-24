"""Render a pretty_midi.PrettyMIDI to audio with a SoundFont, in pure Python.

Uses the `tinysoundfont` package (no fluidsynth needed). Each instrument keeps
its own General MIDI program; drum tracks go on a drum channel. The soundfont
is patched in memory first (see `_soundfont_bytes`) because tinysoundfont has
no SF2 modulators, which leaves MuseScore_General's pianos muffled.

    from sheetswap.synth import render_midi
    render_midi(pm, Path("out.wav"))
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile as sf
import tinysoundfont

from . import config

DRUM_CHANNEL = 9
MELODIC_CHANNELS = [c for c in range(16) if c != DRUM_CHANNEL]
BLOCK = 1 << 16          # max samples generated per call
TAIL_SECONDS = 1.5       # let the last notes ring out


def _channel_map(pm: pretty_midi.PrettyMIDI) -> list[tuple[int, int, bool]]:
    """(channel, program, is_drum) per instrument.

    Instruments that share a program share a channel, so up to 15 different
    melodic programs fit; beyond that, channels are reused round-robin.
    """
    by_program: dict[int, int] = {}
    out = []
    for inst in pm.instruments:
        if inst.is_drum:
            out.append((DRUM_CHANNEL, inst.program, True))
            continue
        if inst.program not in by_program:
            by_program[inst.program] = MELODIC_CHANNELS[len(by_program) % len(MELODIC_CHANNELS)]
        out.append((by_program[inst.program], inst.program, False))
    return out


def _events(pm: pretty_midi.PrettyMIDI, chans: list[tuple[int, int, bool]]) -> list[tuple]:
    """Time-sorted (time, order, kind, chan, a, b). At equal times: controls,
    then note-offs, then note-ons, so repeated notes retrigger cleanly."""
    ev = []
    for inst, (chan, _prog, _drum) in zip(pm.instruments, chans):
        for cc in inst.control_changes:
            ev.append((cc.time, 0, "cc", chan, cc.number, cc.value))
        for pb in inst.pitch_bends:
            ev.append((pb.time, 0, "pb", chan, pb.pitch + 8192, 0))
        for n in inst.notes:
            if n.end <= n.start:
                continue
            ev.append((n.end, 1, "off", chan, n.pitch, 0))
            ev.append((n.start, 2, "on", chan, n.pitch, max(1, min(127, n.velocity))))
    ev.sort(key=lambda e: (e[0], e[1]))
    return ev


_GEN = np.dtype([("oper", "<u2"), ("amount", "<i2")])
_FILTER_FC, _LFO_TO_FC, _ENV_TO_FC, _ATTENUATION = 8, 10, 11, 48


def _pdta_chunks(data: bytes) -> dict[str, tuple[int, int]]:
    """Offsets and sizes of the sub-chunks of the RIFF 'pdta' list."""
    out: dict[str, tuple[int, int]] = {}
    pos = 12
    while pos + 8 <= len(data):
        cid = bytes(data[pos:pos + 4])
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"LIST" and bytes(data[pos + 8:pos + 12]) == b"pdta":
            p, end = pos + 12, pos + 8 + size
            while p + 8 <= end:
                sid = bytes(data[p:p + 4]).decode("latin-1")
                ssz = int.from_bytes(data[p + 4:p + 8], "little")
                out[sid] = (p + 8, ssz)
                p += 8 + ssz + (ssz & 1)
        pos += 8 + size + (size & 1)
    return out


@functools.lru_cache(maxsize=1)
def _soundfont_bytes(path: str) -> bytes:
    """The soundfont with two fixes so tinysoundfont sounds like FluidSynth/MuseScore.

    tinysoundfont ignores SF2 modulators. MuseScore_General's pianos (and some
    other presets) set a very low base filter cutoff and rely on modulators to
    open it, so they come out muffled and the middle velocity layer is nearly
    silent. Fix 1: switch off the low-pass filter generators (filter cutoff to
    "off", LFO/envelope-to-filter to 0). Fix 2: scale initialAttenuation by 0.4,
    the convention FluidSynth uses and this soundfont was balanced for.
    The file on disk is not changed.
    """
    data = bytearray(Path(path).read_bytes())
    chunks = _pdta_chunks(data)
    for name in ("igen", "pgen"):
        if name not in chunks:
            continue
        off, size = chunks[name]
        gens = np.frombuffer(data, dtype=_GEN, count=size // 4, offset=off)  # writable view
        oper, amount = gens["oper"], gens["amount"]
        fc = oper == _FILTER_FC
        amount[fc] = 13500 if name == "igen" else 0      # instrument: off; preset offsets: none
        amount[(oper == _LFO_TO_FC) | (oper == _ENV_TO_FC)] = 0
        att = oper == _ATTENUATION
        amount[att] = np.round(amount[att] * 0.4).astype(np.int16)
    return bytes(data)


def _generate(synth: tinysoundfont.Synth, n: int, chunks: list[np.ndarray]) -> None:
    while n > 0:
        k = min(n, BLOCK)
        buf = synth.generate_simple(k)
        chunks.append(np.frombuffer(buf, dtype=np.float32).reshape(-1, 2).copy())
        n -= k


def synthesize(pm: pretty_midi.PrettyMIDI, sf2: Path = config.SOUNDFONT, sr: int = 44100,
               gain_db: float = -6.0, tail: float = TAIL_SECONDS, fix_soundfont: bool = True) -> np.ndarray:
    """Return float32 stereo audio, shape (samples, 2), not normalized."""
    sf2 = Path(sf2)
    if not sf2.exists():
        raise FileNotFoundError(f"soundfont missing: {sf2} (run scripts/fetch_models.sh)")
    synth = tinysoundfont.Synth(gain=gain_db, samplerate=sr)
    sfid = synth.sfload(_soundfont_bytes(str(sf2.resolve())) if fix_soundfont else str(sf2))
    chans = _channel_map(pm)
    done = set()
    for chan, prog, drum in chans:
        if chan in done:
            continue
        done.add(chan)
        try:
            synth.program_select(chan, sfid, 128 if drum else 0, prog, is_drums=drum)
        except RuntimeError:
            # preset missing in this soundfont: fall back to bank 0 / piano
            synth.program_select(chan, sfid, 0, 0 if not drum else prog, is_drums=drum)

    chunks: list[np.ndarray] = []
    cursor = 0
    for t, _order, kind, chan, a, b in _events(pm, chans):
        target = int(round(max(0.0, t) * sr))
        if target > cursor:
            _generate(synth, target - cursor, chunks)
            cursor = target
        if kind == "on":
            synth.noteon(chan, a, b)
        elif kind == "off":
            synth.noteoff(chan, a)
        elif kind == "cc":
            synth.control_change(chan, a, b)
        elif kind == "pb":
            synth.pitchbend(chan, max(0, min(16383, a)))
    synth.notes_off()
    _generate(synth, int(round(tail * sr)), chunks)
    if not chunks:
        return np.zeros((0, 2), dtype=np.float32)
    return np.concatenate(chunks, axis=0)


def write_wav(audio: np.ndarray, out_wav: Path, sr: int = 44100, mono: bool = True,
              peak: float | None = 0.89) -> Path:
    """Write float audio as 16-bit wav. `peak` normalizes the max |sample| (None = as is)."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    x = np.asarray(audio, dtype=np.float32)
    if mono and x.ndim == 2:
        x = x.mean(axis=1)
    m = float(np.max(np.abs(x))) if x.size else 0.0
    if peak is not None and m > 0:
        x = x * (peak / m)
    sf.write(str(out_wav), np.clip(x, -1.0, 1.0), sr, subtype="PCM_16")
    return out_wav


def render_midi(pm: pretty_midi.PrettyMIDI, out_wav: Path, sf2: Path = config.SOUNDFONT,
                sr: int = 44100, *, mono: bool = True, normalize: bool = True) -> Path:
    """Render `pm` to a 16-bit wav (mono by default, peak-normalized to -1 dBFS)."""
    audio = synthesize(pm, sf2, sr)
    return write_wav(audio, out_wav, sr, mono=mono, peak=0.89 if normalize else None)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m sheetswap.synth in.mid out.wav")
    render_midi(pretty_midi.PrettyMIDI(sys.argv[1]), Path(sys.argv[2]))
