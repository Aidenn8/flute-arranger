"""Decode uploaded audio files to plain wav with the bundled ffmpeg binary."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import soundfile as sf

from . import config

SAMPLE_RATE = 44100
_TIMEOUT = 600          # seconds; decoding 12 minutes takes a few seconds


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _decode(src: Path, dst_wav: Path, channels: int, max_seconds: int) -> float:
    """ffmpeg src -> 16-bit 44.1 kHz wav at dst_wav; returns the duration in seconds.

    Decoding stops one second past max_seconds, so a very long upload costs no
    more than a 12-minute one. dst_wav is only written when everything worked.
    """
    src = Path(src).resolve()
    dst_wav = Path(dst_wav).resolve()
    if not src.is_file() or src.stat().st_size == 0:
        raise ValueError("not audio")
    dst_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_wav.with_name(dst_wav.name + ".part")
    cmd = [
        _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file",
        "-i", f"file:{src}",
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-t", str(max_seconds + 1),
        "-ac", str(channels), "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-f", "wav", f"file:{tmp}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT)
        frames = sf.info(str(tmp)).frames if proc.returncode == 0 and tmp.exists() else 0
    except (subprocess.TimeoutExpired, RuntimeError, OSError):
        frames = 0
    duration = frames / SAMPLE_RATE
    if frames == 0 or duration > max_seconds + 0.5:     # small slack for codec padding
        tmp.unlink(missing_ok=True)
        raise ValueError("not audio" if frames == 0 else "too long")
    os.replace(tmp, dst_wav)
    return duration


def load_audio(src: Path, dst_wav: Path, max_seconds: int = config.MAX_SECONDS) -> float:
    """Decode any audio file to 44.1 kHz mono 16-bit wav. Returns its length in seconds.

    Raises ValueError("not audio") for empty or undecodable input and
    ValueError("too long") when it runs past max_seconds.
    """
    return _decode(src, dst_wav, 1, max_seconds)


def load_stereo(src: Path, dst_wav: Path) -> None:
    """Decode to 44.1 kHz stereo 16-bit wav (input for source separation).

    Same errors as load_audio.
    """
    _decode(src, dst_wav, 2, config.MAX_SECONDS)
