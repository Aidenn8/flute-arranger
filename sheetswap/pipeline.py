"""Glue: analyze a song once (slow, cached), then arrange it any number of ways (fast)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from .model import Analysis, ArrangeOptions, Arrangement, BeatGrid, Note, QNote

ProgressFn = Callable[[int, int, str], None]
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".webm", ".aiff", ".aif"}
MIDI_EXTS = {".mid", ".midi"}


def _noop(step: int, total: int, msg: str) -> None:
    pass


def find_input(song_dir: Path) -> Path:
    for p in sorted(Path(song_dir).glob("input.*")):
        if p.suffix.lower() in AUDIO_EXTS | MIDI_EXTS:
            return p
    raise FileNotFoundError("no input file in " + str(song_dir))


def _title(song_dir: Path) -> str:
    meta = Path(song_dir) / "meta.json"
    if meta.exists():
        try:
            return json.loads(meta.read_text()).get("title", "") or ""
        except (OSError, ValueError):
            pass
    return ""


# ---------------------------------------------------------------- analyze

def analyze(song_dir: Path, kind: str = "auto", progress: ProgressFn = _noop) -> Analysis:
    """Turn the uploaded file into notes + a beat grid. Cached in analysis.json."""
    song_dir = Path(song_dir)
    cached = song_dir / "analysis.json"
    if cached.exists():
        try:
            return Analysis.load(cached)
        except (OSError, ValueError, KeyError, TypeError):
            pass                                  # unreadable cache: analyze again
    src = find_input(song_dir)
    if src.suffix.lower() in MIDI_EXTS:
        progress(1, 2, "Reading the MIDI file")
        an = _analyze_midi(src, song_dir.name)
    else:
        an = _analyze_audio(src, song_dir, kind, progress)
    an.title = _title(song_dir)
    tmp = song_dir / "analysis.json.tmp"
    an.save(tmp)
    os.replace(tmp, cached)
    return an


def _analyze_midi(src: Path, song_id: str) -> Analysis:
    import pretty_midi
    from .beats import grid_from_midi
    from .melody import top_voice, piano_melody

    pm = pretty_midi.PrettyMIDI(str(src))
    insts = [i for i in pm.instruments if not i.is_drum and i.notes]
    if not insts:
        raise ValueError("This MIDI file has no notes.")

    def to_notes(inst) -> list[Note]:
        return [Note(n.pitch, n.start, n.end, n.velocity) for n in inst.notes]

    all_notes = sorted((n for i in insts for n in to_notes(i)), key=lambda n: (n.start, -n.pitch))
    total = len(all_notes)
    candidates = [i for i in insts if len(i.notes) >= 0.2 * total]
    if len(insts) >= 2 and candidates:
        best = max(candidates, key=lambda i: sum(n.pitch for n in i.notes) / len(i.notes))
        melody = top_voice(to_notes(best))
    else:
        melody = piano_melody(all_notes)
    return Analysis(song_id=song_id, kind="midi", duration=pm.get_end_time(),
                    grid=grid_from_midi(pm),
                    sources={"midi_melody": melody, "midi_all": all_notes})


def _analyze_audio(src: Path, song_dir: Path, kind: str, progress: ProgressFn) -> Analysis:
    from . import audio, separate, transcribe
    from .beats import track_beats

    total = 5
    progress(1, total, "Reading the file")
    mono = song_dir / "audio.wav"
    duration = audio.load_audio(src, mono)

    stems: dict[str, Path] = {}
    if kind not in ("piano", "ensemble"):
        progress(2, total, "Separating the instruments (singing, bass, drums, the rest)")
        stereo = song_dir / "stereo.wav"
        audio.load_stereo(src, stereo)
        stems = separate.separate(stereo, song_dir / "stems")
        stereo.unlink(missing_ok=True)
        if kind == "auto":
            kind = separate.classify(stems)

    progress(3, total, "Writing down the notes")
    sources: dict[str, list[Note]] = {}
    if kind == "piano" and not stems:
        # the user said solo piano: the piano model alone is the most accurate
        sources["piano"] = transcribe.piano(mono)
    elif kind in ("piano", "ensemble"):
        # no singing, drums or bass: piano, maybe with a violin, clarinet, ...
        # The piano model hears the piano exactly but is deaf to other
        # instruments; the general model hears everything. Notes only the
        # general model heard belong to another instrument.
        sources["piano"] = transcribe.piano(mono)
        sources["other"] = transcribe.general(mono)
        if (kind == "piano" and other_instrument_cover(sources["other"], sources["piano"], duration)
                >= ENSEMBLE_COVER):
            kind = "ensemble"
    else:
        if kind == "song":
            # a pitch tracker made for one voice: vibrato averages out, one note per syllable
            sources["vocals"] = transcribe.vocal_line(stems["vocals"])
        sources["other"] = transcribe.general(stems["other"])
        sources["bass"] = transcribe.general(stems["bass"])

    progress(4, total, "Finding the beat")
    drums = stems.get("drums") if kind in ("song", "band") else None
    beat_notes = sources.get("piano") or (sources.get("other", []) + sources.get("bass", []))
    grid = track_beats(mono, drums, beat_notes)

    progress(5, total, "Saving")
    return Analysis(song_id=song_dir.name, kind=kind, duration=duration, grid=grid, sources=sources)


# Another instrument plays a continuous line the piano model can't hear; a
# piano's leftovers are scattered overtones and fragments. So: take the
# general-model notes the piano model didn't hear (within 120 ms and a
# semitone), follow a line through them, and see how much of the song that
# line covers. Measured: synthetic solo piano 0.02-0.14, real piano
# recordings 0.17-0.18, piano with a clarinet or violin 0.29-0.81.
ENSEMBLE_COVER = 0.24


def unheard_notes(general: list[Note], piano: list[Note],
                  tol: float = 0.12, semis: int = 1) -> list[Note]:
    starts: dict[int, list[float]] = {}
    for m in piano:
        starts.setdefault(m.pitch, []).append(m.start)
    return [n for n in general
            if not any(abs(s - n.start) < tol
                       for d in range(-semis, semis + 1) for s in starts.get(n.pitch + d, ()))]


def other_instrument_cover(general: list[Note], piano: list[Note], duration: float) -> float:
    from .melody import top_voice
    line = top_voice(unheard_notes(general, piano))
    return sum(n.end - n.start for n in line) / max(duration, 1.0)


# ---------------------------------------------------------------- arrange

def _coverage(notes: list[Note]) -> float:
    return sum(n.end - n.start for n in notes)


def _as_melody(name: str, notes: list[Note]) -> list[Note]:
    from .melody import top_voice, piano_melody
    if name == "vocals":
        # already one clean line; merging touching same-pitch notes would undo re-sung notes
        return sorted(notes, key=lambda n: n.start)
    if name in ("piano", "midi_all"):
        return piano_melody(notes)
    return top_voice(notes)


def pick_melody(an: Analysis, source: str) -> tuple[list[Note], str, list[str]]:
    """Return (melody notes in seconds, source used, warnings)."""
    s = an.sources
    warnings: list[str] = []
    order = {
        "vocals": ["vocals"],
        "piano": ["piano", "midi_all", "other"],
        "other": ["other", "piano", "midi_all"],
    }
    if source in order:
        for name in order[source]:
            if s.get(name):
                if name != order[source][0]:
                    warnings.append(f"This song has no separate '{source}' part, "
                                    "so the tune was taken from another part.")
                return _as_melody(name, s[name]), name, warnings
        warnings.append(f"Couldn't find a '{source}' part; picked the tune automatically instead.")

    vocals = s.get("vocals") or []
    if vocals and _coverage(vocals) >= 0.15 * max(an.duration, 1.0):
        return _as_melody("vocals", vocals), "vocals", warnings
    if s.get("midi_melody"):
        return s["midi_melody"], "midi_melody", warnings
    # in an ensemble the tune is usually the non-piano instrument
    order = ("other", "piano", "midi_all") if an.kind == "ensemble" else ("piano", "other", "midi_all")
    for name in order:
        if s.get(name):
            return _as_melody(name, s[name]), name, warnings
    return [], "", warnings + ["No notes were found in this file."]


def _accompaniment_pool(an: Analysis, melody_src: str,
                        melody: list[Note]) -> tuple[list[Note], list[Note] | None]:
    """Notes the piano part is built from (with the tune's notes removed), plus bass notes."""
    s = an.sources
    if "piano" in s:
        pool = s["piano"]
    elif "midi_all" in s:
        pool = s["midi_all"]
    else:
        pool = s.get("other", [])
    if melody_src != "vocals":
        taken = {(n.pitch, round(n.start, 2)) for n in melody}
        pool = [n for n in pool if (n.pitch, round(n.start, 2)) not in taken]
    return pool, s.get("bass") or None


def rescale_tempo(grid: BeatGrid, tempo: int) -> BeatGrid:
    """Half (-1) or double (+1) the beat rate; the first downbeat stays where it is.

    Beat trackers often lock onto twice (or half) the felt tempo, which writes
    the same music in note values half (or twice) as long as they should be.
    """
    b = list(grid.beats)
    if tempo < 0 and len(b) >= 4:
        off = grid.first_downbeat % 2
        return BeatGrid(b[off::2], grid.beats_per_bar, (grid.first_downbeat - off) // 2)
    if tempo > 0 and len(b) >= 2:
        doubled = [t for a, c in zip(b, b[1:]) for t in (a, (a + c) / 2)] + [b[-1]]
        return BeatGrid(doubled, grid.beats_per_bar, grid.first_downbeat * 2)
    return grid


def bar_grid(an: Analysis, opts: ArrangeOptions, melody: list[Note]) -> BeatGrid:
    """The beat grid the score is written on: tempo and meter choices, then bar lines.

    For audio, beat 1 is placed where the tune's long notes fall (a MIDI file's
    own bar lines are kept); the user's bar-line shift comes on top, keeping
    the pickup shorter than a bar (3 beats later = 1 beat earlier).
    """
    from .beats import melody_downbeat

    grid = rescale_tempo(an.grid, opts.tempo)
    bpb = opts.meter if opts.meter in (3, 4) else grid.beats_per_bar
    grid = BeatGrid(grid.beats, bpb, grid.first_downbeat)
    starts = [n.start for ns in an.sources.values() for n in ns]
    if an.kind != "midi" and starts:
        grid = melody_downbeat(melody, grid, min(starts))
    if opts.barshift:
        first = grid.first_downbeat + opts.barshift % bpb
        if starts:
            first_note_beat = int(np.floor(grid.to_beats(min(starts)))) + grid.first_downbeat
            while first - bpb >= first_note_beat:
                first -= bpb
        grid = BeatGrid(grid.beats, bpb, first)
    return grid


def arrange(an: Analysis, opts: ArrangeOptions) -> Arrangement:
    from .rhythm import quantize_line, add_breath_marks
    from .flute import fit_to_flute, easy_key_shift, key_after_shift
    from .harmony import detect_key
    from .piano import accompany
    from .checks import check_arrangement

    melody, used, warnings = pick_melody(an, opts.melody_source)
    grid = bar_grid(an, opts, melody)
    line = quantize_line(melody, grid, simple=opts.simple)

    everything = [n for notes in an.sources.values() for n in notes]
    weights = [(n.pitch, max(n.end - n.start, 0.01)) for n in everything] or [(60, 1.0)]
    fifths, mode = detect_key(weights)

    shift = 0
    if opts.easy_key:
        shift = easy_key_shift(fifths)
        if shift:
            line = [QNote(q.pitch + shift, q.start, q.dur, q.velocity, q.breath_after) for q in line]
            fifths = key_after_shift(fifths, shift)

    line, _octaves = fit_to_flute(line, opts.octave)
    line = add_breath_marks(line, grid.beats_per_bar)

    rh: list[QNote] = []
    lh: list[QNote] = []
    if opts.mode == "duet":
        pool, bass = _accompaniment_pool(an, used, melody)
        rh, lh = accompany(pool, line, grid, opts.piano_style, shift=shift, bass=bass)
        if not rh and not lh:
            warnings.append("No chords could be found for a piano part, so this is the flute alone.")

    arr = Arrangement(flute=line, rh=rh, lh=lh, beats_per_bar=grid.beats_per_bar,
                      bpm=round(grid.bpm()), key_fifths=fifths, key_mode=mode,
                      shift=shift, warnings=warnings)
    arr.warnings += check_arrangement(arr, opts.simple)
    return arr


# ---------------------------------------------------------------- render (cached per option set)

def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename, so a crash never leaves half a cache file."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    """A cache file's contents, or None if it's missing or unreadable (then it's rebuilt)."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _arr_from_json(d: dict) -> Arrangement:
    d = dict(d)
    for k in ("flute", "rh", "lh"):
        d[k] = [QNote(**q) for q in d.get(k, [])]
    return Arrangement(**d)


def arrange_and_render(song_dir: Path, opts: ArrangeOptions) -> dict:
    from .score import write_outputs
    from .render import render_svg, render_audio

    song_dir = Path(song_dir)
    out = song_dir / "arr" / opts.key()
    info_path = out / "info.json"
    info = _read_json(info_path)
    if info is None:
        an = Analysis.load(song_dir / "analysis.json")
        arr = arrange(an, opts)
        out.mkdir(parents=True, exist_ok=True)
        title = an.title or _title(song_dir) or "Untitled"
        paths = write_outputs(arr, title, out)
        svgs = render_svg(paths["musicxml"])
        for i, svg in enumerate(svgs, 1):
            (out / f"page-{i}.svg").write_text(svg)
        mp3 = render_audio(paths["midi"], out / "preview.mp3")
        info = {
            "pages": len(svgs),
            "musicxml": paths["musicxml"].name,
            "midi": paths["midi"].name,
            "mp3": mp3.name if mp3 else None,
            "arr": asdict(arr),
        }
        write_atomic(info_path, json.dumps(info))
    arr = _arr_from_json(info["arr"])
    return {
        "svgs": [(out / f"page-{i}.svg").read_text() for i in range(1, info["pages"] + 1)],
        "musicxml": out / info["musicxml"],
        "midi": out / info["midi"],
        "mp3": (out / info["mp3"]) if info["mp3"] else None,
        "warnings": arr.warnings,
        "arr": arr,
    }
