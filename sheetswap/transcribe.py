"""Audio -> notes.

piano():   ByteDance piano transcription (high-resolution piano model, with velocities).
general(): Spotify Basic Pitch, ONNX model (any instrument or voice).
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
