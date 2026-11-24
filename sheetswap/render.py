"""Engrave MusicXML to SVG pages (Verovio) and play MIDI to an mp3 preview."""
from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from . import config

log = logging.getLogger(__name__)

RATE = 44100
VEROVIO_OPTIONS = {
    "pageWidth": 2100,
    "scale": 40,
    "adjustPageHeight": True,
    "breaks": "auto",
    "footer": "none",
}
# Mix for MuseScore_General under TinySoundFont. Its grand piano plays the
# velocity layer from about 76 to 105 some 20 dB too quietly, so piano notes
# are mapped into the soft layer (30..75); that also suits an accompaniment.
# The flute is far louder per note, so its channel volume (MIDI CC 7) comes
# down until it sits a few dB in front of the piano, and the master gain
# brings the whole mix up.
_VOLUME = {config.FLUTE_PROGRAM: 86, config.PIANO_PROGRAM: 127}
_PIANO_VELOCITY = (30, 75)
GAIN_DB = 16.0


def _velocity(program: int, velocity: int) -> int:
    if program == config.PIANO_PROGRAM:
        lo, hi = _PIANO_VELOCITY
        return lo + round(velocity * (hi - lo) / 127)
    return velocity


def _bar_end_breaths(musicxml: Path) -> dict[str, float]:
    """Bar number -> where to draw a breath mark that follows the bar's last note.

    Verovio puts such a mark at the very end of the bar, which draws the comma
    on or after the barline. Three quarters of the way through the note puts
    it just before the barline, where engravers write it. Only the top part
    (the flute) is looked at. Times are in beats counted from 1, as in MEI.
    """
    root = ET.parse(musicxml).getroot()
    part = root.find("part")
    if part is None:
        return {}
    divisions, beats, beat_type = 1, 4, 4
    out: dict[str, float] = {}
    for measure in part.findall("measure"):
        attrs = measure.find("attributes")
        if attrs is not None:
            divisions = int(attrs.findtext("divisions") or divisions)
            beats = int(attrs.findtext("time/beats") or beats)
            beat_type = int(attrs.findtext("time/beat-type") or beat_type)
        per_beat = divisions * 4 / beat_type
        pos = last = 0
        for el in measure:
            dur = int(el.findtext("duration") or 0)
            if el.tag == "note":
                if el.find("chord") is None:
                    last, pos = pos, pos + dur
                if (el.find("notations/articulations/breath-mark") is not None
                        and last + dur >= beats * per_beat):
                    out[measure.get("number")] = last / per_beat + 1 + 0.75 * dur / per_beat
            elif el.tag == "backup":
                pos -= dur
            elif el.tag == "forward":
                pos += dur
    return out


def _move_breaths(mei: str, where: dict[str, float]) -> str:
    """Set the tstamp of staff-1 breath marks in the given MEI measures."""
    def fix_measure(m: re.Match) -> str:
        tstamp = where.get(m.group(1))
        if tstamp is None:
            return m.group(0)

        def fix_breath(b: re.Match) -> str:
            tag = b.group(0)
            if 'staff="1"' not in tag:
                return tag
            return re.sub(r'\btstamp="[0-9.]+"', f'tstamp="{tstamp:.3f}"', tag)

        return re.sub(r"<breath\b[^>]*>", fix_breath, m.group(0))

    return re.sub(r'<measure\b[^>]*\bn="([^"]*)"[^>]*>.*?</measure>', fix_measure, mei,
                  flags=re.S)


def render_svg(musicxml: Path) -> list[str]:
    """One SVG string per page."""
    import verovio

    verovio.enableLog(verovio.LOG_OFF)
    tk = verovio.toolkit()
    tk.setOptions(VEROVIO_OPTIONS)
    if not tk.loadFile(str(musicxml)):
        raise ValueError(f"Verovio could not read {musicxml}")
    try:
        where = _bar_end_breaths(Path(musicxml))
    except (ET.ParseError, ValueError):
        where = {}
    if where:
        fixed = _move_breaths(tk.getMEI(), where)
        if not tk.loadData(fixed):          # fall back to the file as it was
            tk.loadFile(str(musicxml))
    return [tk.renderToSVG(page) for page in range(1, tk.getPageCount() + 1)]


def _soft_clip(x: np.ndarray, knee: float = 0.8) -> np.ndarray:
    over = np.abs(x) > knee
    if over.any():
        y = np.abs(x[over]) - knee
        x[over] = np.sign(x[over]) * (knee + (1 - knee) * np.tanh(y / (1 - knee)))
    return x


def render_audio(midi: Path, out_mp3: Path) -> Path | None:
    """MIDI -> 44.1 kHz stereo (TinySoundFont + SOUNDFONT) -> mp3 128k.

    Returns None when the soundfont, the synth or ffmpeg is missing.
    """
    soundfont = Path(config.SOUNDFONT)
    if not soundfont.exists():
        return None
    try:
        import imageio_ffmpeg
        import pretty_midi
        import tinysoundfont
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None

    pm = pretty_midi.PrettyMIDI(str(midi))
    synth = tinysoundfont.Synth(gain=GAIN_DB, samplerate=RATE)
    sfid = synth.sfload(str(soundfont))
    events = []                                   # (time, on?, channel, pitch, velocity)
    channels = [c for c in range(16) if c != 9]   # channel 10 is drums in General MIDI
    for chan, inst in zip(channels, (i for i in pm.instruments if not i.is_drum)):
        synth.program_select(chan, sfid, 0, inst.program)
        synth.control_change(chan, 7, _VOLUME.get(inst.program, 100))
        for n in inst.notes:
            events.append((n.start, 1, chan, n.pitch, _velocity(inst.program, n.velocity)))
            events.append((n.end, 0, chan, n.pitch, 0))
    events.sort(key=lambda e: (e[0], e[1]))       # note-offs before note-ons at one time
    end_time = max((e[0] for e in events), default=0.0) + 1.5   # let the last note ring

    out_mp3 = Path(out_mp3)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2",
           "-i", "pipe:0", "-codec:a", "libmp3lame", "-b:a", "128k", str(out_mp3)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def emit(frames: int) -> None:
        while frames > 0:
            n = min(frames, RATE)
            block = np.frombuffer(synth.generate_simple(n), dtype=np.float32).copy()
            proc.stdin.write(_soft_clip(block).tobytes())
            frames -= n

    try:
        done = 0
        for t, on, chan, key, vel in events:
            at = int(round(t * RATE))
            if at > done:
                emit(at - done)
                done = at
            if on:
                synth.noteon(chan, key, vel)
            else:
                synth.noteoff(chan, key)
        emit(int(round(end_time * RATE)) - done)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0 or not out_mp3.exists():
        # the preview is optional: the score still shows without it
        log.warning("ffmpeg could not write %s: %s", out_mp3, err.strip()[-300:])
        out_mp3.unlink(missing_ok=True)
        return None
    return out_mp3
