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
# Mix: with the soundfont patched by sheetswap.synth, a single flute line sits
# about 7 dB above single piano notes at equal velocity (measured), and piano
# chords close some of that gap, so the tune leads and the piano supports it.
# Both channels stay at full MIDI volume (CC 7 in TinySoundFont is steep).
_VOLUME = {config.FLUTE_PROGRAM: 127, config.PIANO_PROGRAM: 127}


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


def render_audio(midi: Path, out_mp3: Path) -> Path | None:
    """MIDI -> 44.1 kHz stereo (sheetswap.synth + SOUNDFONT) -> mp3 128k.

    Returns None when the soundfont, the synth or ffmpeg is missing.
    """
    soundfont = Path(config.SOUNDFONT)
    if not soundfont.exists():
        return None
    try:
        import imageio_ffmpeg
        import pretty_midi
        from .synth import synthesize
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None

    pm = pretty_midi.PrettyMIDI(str(midi))
    for inst in pm.instruments:
        vol = _VOLUME.get(inst.program, 100)
        inst.control_changes.insert(0, pretty_midi.ControlChange(7, vol, 0.0))
    audio = synthesize(pm, soundfont, RATE)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio * (0.89 / peak)

    out_mp3 = Path(out_mp3)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2",
           "-i", "pipe:0", "-codec:a", "libmp3lame", "-b:a", "128k", str(out_mp3)]
    proc = subprocess.run(cmd, input=audio.astype(np.float32).tobytes(), capture_output=True)
    if proc.returncode != 0 or not out_mp3.exists():
        # the preview is optional: the score still shows without it
        log.warning("ffmpeg could not write %s: %s", out_mp3,
                    proc.stderr.decode(errors="replace").strip()[-300:])
        out_mp3.unlink(missing_ok=True)
        return None
    return out_mp3
