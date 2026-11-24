"""Split a mix into vocals / drums / bass / other with Demucs (htdemucs)."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

STEMS = ("vocals", "drums", "bass", "other")
SAMPLE_RATE = 44100
SONG_VOCALS_SHARE = 0.15     # vocals above this share of stem energy -> "song"
PIANO_RHYTHM_SHARE = 0.06    # drums + bass below this share -> "piano"

_model = None
_devices: list[str] | None = None    # filled on first use; "mps" dropped after a failure


def mps_safe() -> bool:
    """True when torch's Apple GPU backend can be used without risking a crash.

    On macOS 14, torch 2.15's mps Linear kernel calls a Metal method that only
    exists on macOS 15+, and the process aborts (an Objective-C exception, not a
    Python one, so there is nothing to catch). htdemucs hits it on 14.4.
    """
    import platform
    import torch
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except ValueError:
        major = 0
    return major >= 15 and torch.backends.mps.is_available()


def _device_order() -> list[str]:
    global _devices
    if _devices is None:
        _devices = ["mps", "cpu"] if mps_safe() else ["cpu"]
    return _devices


def _get_model():
    global _model
    if _model is None:
        from demucs.pretrained import get_model
        _model = get_model("htdemucs")
        _model.eval()
    return _model


def _read_stereo(wav: Path, samplerate: int):
    import torch
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)   # (n, ch)
    if data.shape[0] == 0:
        raise ValueError("not audio")
    x = torch.from_numpy(data.T.copy())                              # (ch, n)
    if x.shape[0] == 1:
        x = x.repeat(2, 1)
    elif x.shape[0] > 2:
        x = x[:2]
    if sr != samplerate:
        from demucs.audio import convert_audio
        x = convert_audio(x, sr, samplerate, 2)
    return x


def _run(model, x, device: str):
    import torch
    from demucs.apply import apply_model
    ref = x.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((x - mean) / std)[None], device=device,
                          shifts=0, split=True, overlap=0.25, progress=False)[0]
    return out.mul_(std).add_(mean)                                  # (sources, ch, n)


def separate(wav: Path, out_dir: Path) -> dict[str, Path]:
    """Run htdemucs on wav; write mono 16-bit 44.1 kHz stems into out_dir.

    Returns {"vocals": path, "drums": path, "bass": path, "other": path}.
    Uses Apple's GPU (mps) where that is safe (see mps_safe), otherwise the CPU.
    """
    model = _get_model()
    x = _read_stereo(Path(wav), model.samplerate)
    out = None
    devices = _device_order()
    for device in list(devices):
        try:
            out = _run(model, x, device)
            break
        except Exception as exc:                 # mps op gaps, out of memory, ...
            if device == "cpu":
                raise
            log.warning("demucs on %s failed (%s); using cpu", device, exc)
            devices.remove(device)
            model.to("cpu")
    stems = out.mean(1).cpu().numpy()            # (sources, n) mono
    if model.samplerate != SAMPLE_RATE:
        import librosa
        stems = librosa.resample(stems, orig_sr=model.samplerate, target_sr=SAMPLE_RATE)
    peak = float(np.abs(stems).max()) if stems.size else 0.0
    if peak > 0.999:                             # same gain for all, keeps energy ratios
        stems = stems * (0.999 / peak)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, audio in zip(model.sources, stems):
        if name in STEMS:
            path = out_dir / f"{name}.wav"
            sf.write(str(path), audio, SAMPLE_RATE, subtype="PCM_16")
            paths[name] = path
    return paths


def _energy(path: Path) -> float:
    """Sum of squares over the whole file (all channels), read in blocks."""
    total = 0.0
    for block in sf.blocks(str(path), blocksize=SAMPLE_RATE * 30, dtype="float32",
                           always_2d=True):
        total += float(np.square(block, dtype=np.float64).sum())
    return total


def classify(stems: dict[str, Path]) -> str:
    """Guess what kind of recording this is from the stem energies.

    "song" if the vocals hold more than 15% of the energy, "piano" if drums
    and bass together hold less than 6%, otherwise "band".
    """
    energy = {name: _energy(stems[name]) for name in STEMS if name in stems}
    total = sum(energy.values())
    if total <= 0:
        return "piano"
    if energy.get("vocals", 0.0) / total > SONG_VOCALS_SHARE:
        return "song"
    if (energy.get("drums", 0.0) + energy.get("bass", 0.0)) / total < PIANO_RHYTHM_SHARE:
        return "piano"
    return "band"
