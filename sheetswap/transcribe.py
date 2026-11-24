"""Audio -> notes.

piano():      ByteDance piano transcription (high-resolution piano model, with velocities).
general():    Spotify Basic Pitch, ONNX model (any instrument or voice).
vocal_line(): pYIN pitch tracking of a separated vocal stem, one note per sung pitch.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import os
import sys
import types
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config
from .model import Note

log = logging.getLogger(__name__)

# --- piano model -------------------------------------------------------------

# Official checkpoint (same file and URL the package's own downloader uses).
PIANO_CKPT_NAME = "note_F1=0.9677_pedal_F1=0.9186.pth"
PIANO_CKPT_URL = ("https://zenodo.org/record/4034264/files/"
                  "CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1")
PIANO_CKPT_SIZE = 171_966_578
PIANO_CKPT_MD5 = "22b961b77c1878239fec963362097045"

# Devices tried in order; one that raises is dropped. mps only where
# separate.mps_safe() says so: on macOS 14.4 + torch 2.15 this model aborts the
# process on mps (same missing Metal method as Demucs), so it runs on the CPU
# there, about 12 s per 30 s of audio on an M-series Mac.
_PIANO_DEVICES: list[str] | None = None
_piano = None
_piano_device = None


def _piano_devices() -> list[str]:
    global _PIANO_DEVICES
    if _PIANO_DEVICES is None:
        from .separate import mps_safe
        _PIANO_DEVICES = ["mps", "cpu"] if mps_safe() else ["cpu"]
    return _PIANO_DEVICES


def piano_checkpoint() -> Path:
    """Path of the piano checkpoint in models/, downloading it if missing."""
    path = config.MODELS_DIR / PIANO_CKPT_NAME
    if path.exists() and path.stat().st_size == PIANO_CKPT_SIZE:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    log.info("downloading piano model (~165 MB) to %s", path)
    md5 = hashlib.md5()
    with urllib.request.urlopen(PIANO_CKPT_URL, timeout=60) as resp, open(part, "wb") as f:
        while chunk := resp.read(1 << 20):
            md5.update(chunk)
            f.write(chunk)
    if md5.hexdigest() != PIANO_CKPT_MD5:
        part.unlink(missing_ok=True)
        raise RuntimeError("piano model download failed its checksum")
    os.replace(part, path)
    return path


def _load_piano(device: str):
    import torch
    from piano_transcription_inference import PianoTranscription
    with contextlib.redirect_stdout(io.StringIO()):
        pt = PianoTranscription(checkpoint_path=str(piano_checkpoint()),
                                device=torch.device("cpu"))
    pt.model.to(device)          # the package only moves models for cuda itself
    return pt


def _read_mono(wav: Path, sr: int) -> np.ndarray:
    data, file_sr = sf.read(str(wav), dtype="float32", always_2d=True)
    audio = data.mean(axis=1)
    if file_sr != sr and audio.size:
        import librosa
        audio = librosa.resample(audio, orig_sr=file_sr, target_sr=sr)
    return audio.astype(np.float32)


def _clip_velocity(v) -> int:
    return int(min(127, max(1, round(float(v)))))


def _piano_events_to_notes(events: list[dict], duration: float = float("inf")) -> list[Note]:
    """Package events -> Notes. The model pads audio to 10 s segments, so a note
    with no detected offset can run past the end; ends are clipped to duration."""
    notes = []
    for e in events:
        start = float(e["onset_time"])
        end = min(float(e["offset_time"]), duration)
        if end > start:
            notes.append(Note(int(e["midi_note"]), start, end, _clip_velocity(e["velocity"])))
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


def piano(wav: Path) -> list[Note]:
    """Transcribe a piano recording (or piano-heavy mix) to notes in seconds."""
    global _piano, _piano_device
    from piano_transcription_inference import sample_rate
    audio = _read_mono(Path(wav), sample_rate)
    if audio.size == 0:
        return []
    devices = _piano_devices()
    for device in list(devices):
        try:
            if _piano is None or _piano_device != device:
                _piano, _piano_device = _load_piano(device), device
            with contextlib.redirect_stdout(io.StringIO()):
                result = _piano.transcribe(audio, None)
            return _piano_events_to_notes(result["est_note_events"], audio.size / sample_rate)
        except Exception as exc:
            if device == "cpu":
                raise
            log.warning("piano model on %s failed (%s); using cpu", device, exc)
            devices.remove(device)
            _piano = None
    return []


# --- Basic Pitch ---------------------------------------------------------------

# Onset 0.5 is the Basic Pitch default. Frame 0.4 (default 0.3) drops most of
# the faint ghost notes it reports one or two octaves above real notes, which
# would otherwise pull the melody line upward. On synthetic tones 0.5 removes
# nearly all of them with no missed notes, but may lose quiet real notes
# (singing especially); worth re-checking on real recordings.
BP_ONSET_THRESHOLD = 0.5
BP_FRAME_THRESHOLD = 0.4
BP_MIN_NOTE_MS = 58.0

_bp_model = None


def _stub_resampy_if_broken() -> None:
    """basic_pitch imports resampy at load time, but only uses it to turn MIDI
    back into audio, which we never do. resampy 0.4.2 imports pkg_resources,
    which recent setuptools no longer ships; stand in a stub when that fails."""
    try:
        import resampy  # noqa: F401
    except ImportError:
        stub = types.ModuleType("resampy")

        def resample(*args, **kwargs):
            raise NotImplementedError("resampy is not importable here")
        stub.resample = resample
        sys.modules["resampy"] = stub


def _basic_pitch_model():
    global _bp_model
    if _bp_model is None:
        _stub_resampy_if_broken()
        logging.disable(logging.WARNING)      # import warns about absent TF / TFLite
        try:
            from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
            from basic_pitch.inference import Model
        finally:
            logging.disable(logging.NOTSET)
        _bp_model = Model(build_icassp_2022_model_path(FilenameSuffix.onnx))
    return _bp_model


def _bp_events_to_notes(events) -> list[Note]:
    notes = [Note(int(p), float(s), float(e), _clip_velocity(127 * float(a)))
             for s, e, p, a, *_ in events if float(e) > float(s)]
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


def general(wav: Path, *, onset_threshold: float | None = None,
            frame_threshold: float | None = None) -> list[Note]:
    """Transcribe any instrument or voice with Basic Pitch (ONNX)."""
    model = _basic_pitch_model()
    from basic_pitch.inference import predict
    with contextlib.redirect_stdout(io.StringIO()):
        _, _, events = predict(
            str(wav), model,
            onset_threshold=BP_ONSET_THRESHOLD if onset_threshold is None else onset_threshold,
            frame_threshold=BP_FRAME_THRESHOLD if frame_threshold is None else frame_threshold,
            minimum_note_length=BP_MIN_NOTE_MS,
            melodia_trick=True,
        )
    return _bp_events_to_notes(events)


# --- sung line (pYIN) ------------------------------------------------------------

# Basic Pitch hears a sung note's vibrato (about 5.5 Hz, up to +-60 cents) as
# notes a semitone apart, so a held note comes out as a flicker of short
# notes. pYIN gives one pitch curve instead; the rest turns that curve into
# notes: one per held pitch, at the centre of the vibrato.
VL_SR = 16000
VL_FRAME = 1024          # 64 ms: room for two periods of the lowest pitch
VL_HOP = 256             # 16 ms per frame
VL_FMIN = 65.4           # C2
VL_FMAX = 1046.5         # C6
VL_RESOLUTION = 0.1      # semitones between pYIN's pitch states
VL_SMOOTH = 0.13         # s, running median of the pitch: shrinks the vibrato to a wobble
VL_HYST = 0.3            # semitones past the half-way point before the note changes
VL_GAP = 0.035           # s, voicing dropouts this short are bridged
VL_MIN_NOTE = 0.14       # s, shorter pieces join a neighbour (scoops, slides, wide vibrato) or go
VL_EDGE = 0.2            # s, a piece this short opening a voiced stretch joins the next if it
                         # is a scoop or the previous note ringing on ...
VL_LEFTOVER_S = 0.3      # ... (same pitch as a note that ended this recently)
VL_SNAP_DB = 3.0         # a change of pitch moves back to a loudness dip this deep ...
VL_SNAP_BACK = 0.15      # ... at most this far before it
VL_DIP_DB = 8.0          # an energy dip this deep inside one pitch is a new syllable ...
VL_DIP_MIN = 0.2         # ... if it leaves this much on each side
VL_FLOOR_DB = -32.0      # voiced frames this far under the loud level are bleed, not singing
VL_LOUD_Q = 90           # percentile of voiced-frame loudness taken as the loud level


def vocal_line(wav: Path) -> list[Note]:
    """Notes of a single sung line, from an already separated vocal stem.

    pYIN tracks the pitch every 16 ms. Quiet frames (stem bleed) count as
    unvoiced, and dropouts under VL_GAP are bridged. Inside each voiced
    stretch the pitch, in semitones, gets a running median over VL_SMOOTH,
    then is rounded with hysteresis (it must move VL_HYST past the half-way
    point to change note). Pieces under VL_MIN_NOTE join the neighbour
    nearest in pitch (stretches that short are dropped). Each piece's pitch
    is the median of the raw curve over it (the centre of the vibrato). A
    short scoop or leftover of the previous note opening a stretch joins the
    note after it, and changes of pitch move back to the syllable's loudness
    dip when there is one close before. Last, a piece splits where the
    loudness dips VL_DIP_DB under both sides (the same pitch sung again).
    Velocity follows loudness. Notes come back sorted and don't overlap.
    """
    import librosa

    y = _read_mono(Path(wav), VL_SR)
    if y.size < VL_FRAME or not np.any(np.abs(y) > 1e-4):
        return []
    f0, voiced, _ = librosa.pyin(y, fmin=VL_FMIN, fmax=VL_FMAX, sr=VL_SR,
                                 frame_length=VL_FRAME, hop_length=VL_HOP,
                                 resolution=VL_RESOLUTION)
    # loudness over half a pitch frame (same frame centres), so short dips show
    rms = librosa.feature.rms(y=y, frame_length=VL_FRAME // 2, hop_length=VL_HOP)[0]
    n = min(len(f0), len(rms))
    f0, voiced, rms = f0[:n], voiced[:n] & np.isfinite(f0[:n]), rms[:n]
    db = 20 * np.log10(np.maximum(rms, 1e-10))
    if not voiced.any():
        return []
    loud = float(np.percentile(db[voiced], VL_LOUD_Q))
    voiced &= db > loud + VL_FLOOR_DB
    if not voiced.any():
        return []

    hop_s = VL_HOP / VL_SR
    voiced = _bridge_gaps(voiced, int(VL_GAP / hop_s))
    known = np.flatnonzero(np.isfinite(f0))
    midi = np.interp(np.arange(n), known, librosa.hz_to_midi(f0[known]))
    width = max(1, int(round(VL_SMOOTH / hop_s))) | 1
    min_len = max(1, int(round(VL_MIN_NOTE / hop_s)))

    notes: list[Note] = []
    for a, b in _runs(voiced):
        m = midi[a:b]
        smooth = _running_median(m, width)
        pieces = _join_equal(_absorb_short(_hysteresis(smooth), m, min_len))
        if len(pieces) == 1 and b - a < min_len:
            continue
        pieces = _join_equal([[s, e, int(round(float(np.median(m[s:e]))))] for s, e, _ in pieces])
        last = notes[-1].pitch if notes and a * hop_s - notes[-1].end <= VL_LEFTOVER_S else None
        pieces = _tidy_start(pieces, last, int(round(VL_EDGE / hop_s)))
        _snap_to_dips(pieces, db[a:b], min_len, int(round(VL_SNAP_BACK / hop_s)))
        for s, e, p in pieces:
            for s2, e2 in _split_on_dips(db[a:b], s, e, int(round(VL_DIP_MIN / hop_s))):
                vel = _clip_velocity(100 + 2 * (float(np.mean(db[a + s2:a + e2])) - loud))
                notes.append(Note(p, float((a + s2) * hop_s), float((a + e2) * hop_s), vel))
    notes.sort(key=lambda n: n.start)
    return notes


def _tidy_start(pieces: list[list], last: int | None, edge: int) -> list[list]:
    """A short first piece (under `edge` frames) of a voiced stretch joins the
    next piece when it is the previous note still sounding (`last`, its pitch,
    reverb or a slow release) or a scoop from 1-2 semitones under."""
    while (len(pieces) > 1 and pieces[0][1] - pieces[0][0] < edge
           and (pieces[0][2] == last or 0 < pieces[1][2] - pieces[0][2] <= 2)):
        pieces[1][0] = pieces[0][0]
        del pieces[0]
    return pieces


def _snap_to_dips(pieces: list[list], db: np.ndarray, min_len: int, back: int) -> None:
    """Move each change of pitch back (at most `back` frames, or a little
    forward) to a loudness dip of VL_SNAP_DB there: the new syllable's onset.
    A pitch change shows late when a note is scooped into or the last one rings on."""
    if VL_SNAP_DB <= 0:
        return
    for k in range(1, len(pieces)):
        j = pieces[k][0]
        lo = max(pieces[k - 1][0] + min_len, j - back)
        hi = min(pieces[k][1] - min_len, j + back // 3)
        if hi <= lo:
            continue
        i = lo + int(np.argmin(db[lo:hi + 1]))
        depth = min(db[max(0, i - back):i].max(initial=-np.inf),
                    db[i + 1:i + 1 + back].max(initial=-np.inf)) - db[i]
        if depth >= VL_SNAP_DB:
            pieces[k - 1][1] = pieces[k][0] = i


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[a, b) frame ranges where mask is True."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _bridge_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    """Fill False runs of up to max_gap frames that have True on both sides."""
    out = mask.copy()
    for a, b in _runs(~mask):
        if a > 0 and b < len(mask) and b - a <= max_gap:
            out[a:b] = True
    return out


def _running_median(x: np.ndarray, width: int) -> np.ndarray:
    from scipy.ndimage import median_filter
    return median_filter(x, size=min(width, len(x) | 1), mode="nearest")


def _hysteresis(s: np.ndarray) -> list[list]:
    """[start, end, semitone] pieces of the smoothed pitch s. The semitone
    changes once s is VL_HYST past the half-way point; the boundary goes back
    to where s crossed the half-way point."""
    pieces = []
    a, q = 0, int(round(float(s[0])))
    for i in range(1, len(s)):
        if abs(s[i] - q) > 0.5 + VL_HYST:
            up = s[i] > q
            j = i
            while j > a + 1 and (s[j - 1] - q > 0.5 if up else q - s[j - 1] > 0.5):
                j -= 1
            pieces.append([a, j, q])
            a, q = j, int(round(float(s[i])))
    pieces.append([a, len(s), q])
    return pieces


def _join_equal(pieces: list[list]) -> list[list]:
    out: list[list] = []
    for p in pieces:
        if out and out[-1][2] == p[2] and out[-1][1] == p[0]:
            out[-1][1] = p[1]
        else:
            out.append(list(p))
    return out


def _absorb_short(pieces: list[list], m: np.ndarray, min_len: int) -> list[list]:
    """Pieces under min_len frames go to the neighbour nearest in pitch (a
    scoop into a note, a slide between notes); a tie splits them down the middle."""
    pieces = _join_equal(pieces)
    while len(pieces) > 1:
        lens = [e - s for s, e, _ in pieces]
        k = int(np.argmin(lens))
        if lens[k] >= min_len:
            break
        s, e, _ = pieces[k]
        centre = float(np.median(m[s:e]))
        left = pieces[k - 1] if k > 0 else None
        right = pieces[k + 1] if k + 1 < len(pieces) else None
        dl = abs(left[2] - centre) if left else np.inf
        dr = abs(right[2] - centre) if right else np.inf
        if left and right and abs(dl - dr) < 0.25:
            left[1] = right[0] = (s + e) // 2
        elif dl < dr:
            left[1] = e
        else:
            right[0] = s
        del pieces[k]
        pieces = _join_equal(pieces)
    return pieces


def _split_on_dips(db: np.ndarray, s: int, e: int, min_len: int) -> list[tuple[int, int]]:
    """Split frames [s, e) where the loudness dips VL_DIP_DB under the loudest
    point on each side, deepest dip first, keeping pieces >= min_len."""
    if e - s < 2 * min_len:
        return [(s, e)]
    x = db[s:e]
    left = np.maximum.accumulate(x)
    right = np.maximum.accumulate(x[::-1])[::-1]
    depth = np.minimum(left, right) - x
    depth[:min_len] = depth[len(x) - min_len + 1:] = -np.inf
    k = int(np.argmax(depth))
    if depth[k] < VL_DIP_DB:
        return [(s, e)]
    return (_split_on_dips(db, s, s + k, min_len)
            + _split_on_dips(db, s + k, e, min_len))
