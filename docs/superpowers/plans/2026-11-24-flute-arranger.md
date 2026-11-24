# Flute Arranger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app that turns any song (audio or MIDI) into flute sheet music the user can play, either flute alone or flute + supporting piano.

**Architecture:** A Python package `sheetswap` with one module per step: analyze (slow, cached: load → separate → transcribe → beats) and arrange (fast: melody → flute fit → rhythm → piano → score → render). A Flask app with plain old-web HTML runs analysis in a background thread and re-arranges on option changes.

**Tech Stack:** Python 3.11 (`.venv` via uv), torch, demucs, piano_transcription_inference, basic-pitch (onnx), librosa, music21, verovio, pretty_midi, mir_eval, Flask, pytest; Homebrew ffmpeg + fluid-synth.

**Spec:** `docs/superpowers/specs/2026-11-24-flute-arranger-design.md`

## Global Constraints

- Flute hard range C4–C7 (MIDI 60–96); comfortable D4–A6 (62–93).
- Time unit for notation: `TICKS_PER_BEAT = 12` (16th = 3, 8th-triplet = 4, 8th = 6). Beat = quarter note. Meters: 4/4 or 3/4.
- Shortest flute note: 16th (3 ticks) normally, 8th (6 ticks) in simple mode.
- In duet mode, no piano note sounds at or above the flute note sounding at the same time.
- Old-web UI: server-rendered HTML, browser default serif font, default blue links, `<hr>`, `<table border=1>`; no CSS frameworks, no JS frameworks, no animations. Small inline `<style>` only for the print page.
- Server: Flask on `http://localhost:5230`. All song data under `out/<song_id>/` (git-ignored). Models and soundfont under `models/` (git-ignored).
- No Claude mentions in repo files or commits. Commits use the configured noreply identity.
- Never open browser tabs or players automatically.

## Review Focus

1. **Song with a long intro and no melody yet** (chords only for a while): once the melody enters, the flute line must follow it and never drop into the chords. Test: `test_top_voice_ignores_sparse_low_chords` in Task 3.
2. **Rubato or drifting tempo**: notation should stay on the beat because quantization runs in beat space. Test: `test_quantize_follows_tempo_drift` in Task 2.
3. **Melody running off the flute range for one phrase only**: move that phrase by an octave, not single notes. Test: `test_fold_moves_whole_phrase` in Task 4.
4. **Uploads that aren't music** (empty file, a .txt renamed to .mp3, a 30-minute file): readable error, cap at 12 minutes. Test: `test_upload_rejects_non_audio` in Task 8.
5. **Notes before the first downbeat (pickup)**: the score starts with a pickup bar padded with rests. Test: `test_pickup_notes_padded` in Task 6.

---

## File structure

```
sheetswap/
  config.py      constants, paths, ranges
  model.py       dataclasses: Note, QNote, BeatGrid, Analysis, ArrangeOptions, Arrangement (+ json save/load)
  beats.py       beat tracking + meter; MIDI beat grid
  rhythm.py      quantize_line, quantize_chords, add_breath_marks
  melody.py      top_voice, clean_monophonic
  flute.py       fit_to_flute, easy_key_shift
  harmony.py     detect_key, detect_chords
  piano.py       accompany
  score.py       build_score, write_outputs
  checks.py      check_arrangement
  render.py      render_svg, render_audio
  audio.py       load_audio
  separate.py    separate (demucs)
  transcribe.py  piano, general
  pipeline.py    analyze, arrange, arrange_and_render
  web/app.py, web/templates/*.html
tests/           one test file per module
eval/            make_groundtruth.py, run_eval.py, fetch_recordings.py
scripts/fetch_models.sh
run.sh           starts the app
```

### Task 1: Data model and scaffold

**Files:** Create `sheetswap/{__init__,config,model}.py`, `tests/test_model.py`, `requirements.txt`, `pytest.ini`, `run.sh`.

**Interfaces (produces; every later task uses these exact names):**
```python
# config.py
TICKS_PER_BEAT = 12; FLUTE_HARD = (60, 96); FLUTE_COMFY = (62, 93)
ROOT: Path; OUT_DIR = ROOT/"out"; MODELS_DIR = ROOT/"models"; SOUNDFONT = MODELS_DIR/"MuseScore_General.sf2"
# model.py
@dataclass Note(pitch:int, start:float, end:float, velocity:int=80)            # seconds
@dataclass QNote(pitch:int, start:int, dur:int, velocity:int=80, breath_after:bool=False)  # ticks; 0 = first downbeat; negative = pickup
@dataclass BeatGrid(beats:list[float], beats_per_bar:int, first_downbeat:int)
    .to_beats(t: float) -> float        # piecewise-linear over beats, extrapolate with edge interval; first downbeat = 0.0
    .to_ticks(t: float) -> float        # to_beats * TICKS_PER_BEAT
    .bpm() -> float                     # 60 / median beat interval
@dataclass Analysis(song_id:str, kind:str, duration:float, grid:BeatGrid, sources:dict[str,list[Note]], title:str="")
    kind ∈ {"piano","song","band","midi"}; sources keys ⊆ {"piano","vocals","other","bass","midi_melody","midi_all"}
    .save(path) / Analysis.load(path)    # json
@dataclass ArrangeOptions(mode="solo"|"duet", melody_source="auto"|"vocals"|"piano"|"other", octave:int=0, simple:bool=False, easy_key:bool=False, piano_style="chords"|"cleaned")
    .key() -> str                       # short stable string, used as cache folder name
    ArrangeOptions.from_form(d: Mapping) -> ArrangeOptions   # tolerant of missing/bad values → defaults
@dataclass Arrangement(flute:list[QNote], rh:list[QNote], lh:list[QNote], beats_per_bar:int, bpm:float, key_fifths:int, key_mode:str, shift:int, warnings:list[str])
```

- [ ] Test `test_beatgrid_to_beats`: beats=[1.0,1.5,2.0,2.5], first_downbeat=1 → `to_beats(1.5)==0`, `to_beats(1.75)==0.5`, `to_beats(0.5)==-2`, `to_beats(3.0)==3`.
- [ ] Test `test_analysis_roundtrip`: save → load equals the original.
- [ ] Test `test_options_from_form_defaults`: `from_form({"octave":"x","mode":"nope"})` → defaults; `key()` differs when any field differs.
- [ ] Implement; `run.sh` = `exec .venv/bin/python -m sheetswap.web.app`. `pytest -q` passes. Commit.

### Task 2: Beats and rhythm (`beats.py`, `rhythm.py`)

**Interfaces:**
```python
beats.track_beats(wav: Path, drums_wav: Path|None=None) -> BeatGrid   # librosa beat_track on drums stem if given else mix
beats.grid_from_midi(pm: pretty_midi.PrettyMIDI) -> BeatGrid
rhythm.quantize_line(notes: list[Note], grid: BeatGrid, simple: bool=False) -> list[QNote]   # monophonic in/out, sorted, no overlaps
rhythm.quantize_chords(notes: list[Note], grid: BeatGrid, step: int=6) -> list[QNote]       # polyphonic
rhythm.add_breath_marks(line: list[QNote], beats_per_bar: int) -> list[QNote]
```
Meter: for m in (4,3) and each phase p, score = mean onset strength on beats ≡ p (mod m) ÷ mean on the others; pick the best, with ties going to 4; first_downbeat = the first beat with phase p. Quantize line: snap onsets per beat to the 16th grid or the 8th-triplet grid (triplet only if it lowers that beat's total error by >40% and the beat has ≥2 onsets); simple mode uses an 8th grid. Collisions keep the louder note. Each note lasts to its quantized end, extended to the next onset when the gap is shorter than the min note; min dur 3 (simple: 6). Breath marks: in any stretch longer than 4 bars without a rest of at least one 8th, set `breath_after` on the longest note ending in each 2-bar window.

- [ ] Tests: `test_meter_three_four` (synthetic clicks accented every 3rd beat → beats_per_bar 3), `test_quantize_straight_eighths`, `test_quantize_triplets`, `test_quantize_follows_tempo_drift` (beats spaced 0.5→0.7 s, notes on beats → all starts multiples of 12), `test_simple_mode_min_eighth`, `test_breath_marks_long_phrase`. Fail → implement → pass → commit.

### Task 3: Melody (`melody.py`)

**Interfaces:** `top_voice(notes: list[Note]) -> list[Note]`, `clean_monophonic(notes: list[Note]) -> list[Note]`.

top_voice is a DP over notes sorted by start (drop notes shorter than 50 ms):
```
gain(n)   = dur(n)·(1 + 0.15·(pitch−p_med)/12 + 0.5·vel/127)     # p_med = median pitch of all notes
jump(a,b) = 0.04·|pitch_b − pitch_a| + (0.3 if |Δ| > 12 else 0)
best[i]   = gain(i) + max(0, max_{j: end_j ≤ start_i + 0.3·dur_j, j among last 200} best[j] − jump(j,i))
```
Backtrack from the best end, then trim overlaps (end_j = min(end_j, start_i)). Then drop any path note that sits ≥5 semitones below an unchosen note sounding over >80% of its length (keeps the line on top). clean_monophonic: at overlaps keep the louder note; merge same-pitch fragments with gaps under 80 ms; drop notes under 60 ms. Constants are starting values, tuned in Task 10.

- [ ] Tests: `test_top_voice_melody_over_chords` (melody C5-D5-E5-F5 over C-major block chords C3/E3/G3 → exactly the melody), `test_top_voice_ignores_sparse_low_chords` (8 s of low chords alone, then melody over chords → every path note after the melody starts is a melody note, and all melody notes are present), `test_clean_monophonic_merges_vibrato_splits`. Fail → implement → pass → commit.

### Task 4: Flute fit (`flute.py`)

**Interfaces:** `fit_to_flute(line: list[QNote], octave: int=0) -> tuple[list[QNote], int]` (new line, global shift in semitones); `easy_key_shift(fifths: int) -> int`.

Global shift k·12, k∈[−4,4]: cost = Σ dur·(0 in comfy, 1 in hard-only, 20 outside) + 0.02·|median − 79|·len. Pick the best, then add `octave·12` if that keeps ≥90% of notes in the hard range. Leftovers: split phrases at rests ≥ 1 beat; move the whole phrase ±12 if that brings it all inside the hard range, otherwise move single notes by octaves. easy_key_shift: the semitone shift in [−5,6] with the smallest |shift| whose resulting key has ≤2 sharps/flats (0 if already ≤2).

- [ ] Tests: `test_fit_moves_bass_melody_up` (melody around C3 → all in 60–96, shift ∈ {24, 36}), `test_fit_all_in_hard_range` (random line 20–110 → all in range), `test_fold_moves_whole_phrase`, `test_octave_override`, `test_easy_key_shift` (5 sharps → ≤2 accidentals, |shift| ≤ 6). Commit.

### Task 5: Harmony and piano (`harmony.py`, `piano.py`)

**Interfaces:**
```python
harmony.detect_key(pitches_weights: list[tuple[int, float]]) -> tuple[int, str]   # (fifths, "major"|"minor"), Krumhansl profiles
harmony.detect_chords(notes: list[Note], grid: BeatGrid, span: int) -> list[tuple[int, int, int, str]]  # (start_tick, dur_ticks, root_pc, "maj"|"min")
piano.accompany(notes: list[Note], flute: list[QNote], grid: BeatGrid, style: str, shift: int=0, bass: list[Note]|None=None) -> tuple[list[QNote], list[QNote]]  # (rh, lh)
```
`shift` = the key transposition applied to the flute (easy key), added to every accompaniment pitch; the flute's octave moves aren't applied. Chords style (span = half bar): 24 triad templates on duration×velocity chroma, LH root in 36–47 held for the span (bass notes, if given, decide the root when they agree with a template), RH triad voiced nearest the previous voicing in 52–67, kept ≥3 semitones under the lowest flute note in that span (drop an octave if needed). Cleaned style: remove notes matching the melody (same pitch, onset ±60 ms), `quantize_chords(step=6)`, LH = pitch < 60 (≤2 per onset, keep lowest), RH = the rest (≤3 per onset, drop highest first), any RH note ≥ (sounding flute pitch − 1) is dropped an octave or removed.

- [ ] Tests: `test_detect_key_c_major`, `test_detect_key_a_minor`, `test_chords_c_g_progression`, `test_piano_never_above_flute` (both styles), `test_lh_in_bass_register`. Commit.

### Task 6: Score and checks (`score.py`, `checks.py`)

**Interfaces:** `build_score(arr: Arrangement, title: str) -> music21.stream.Score`; `write_outputs(arr, title, out_dir: Path) -> dict[str, Path]` (keys "musicxml", "midi"); `check_arrangement(arr: Arrangement, simple: bool) -> list[str]`.

Parts: Flute (MIDI program 73); in duet mode (rh or lh non-empty), Piano as two PartStaffs (treble RH, bass LH) in a brace StaffGroup. Key signature from `key_fifths`; spell black keys with flats if fifths < 0, else sharps. Time signature `beats_per_bar/4`; tempo mark = round(bpm). Pickup: if min start < 0, shift everything right by one bar (start rests). Ticks → quarterLength via `Fraction(ticks, 12)`. Use music21 `makeMeasures`/`makeTies`/`makeNotation`. Breath mark = `articulations.BreathMark`.

- [ ] Tests: `test_musicxml_written_and_bars_full`, `test_pickup_notes_padded`, `test_flat_key_spelling` (F major: B♭ spelled B-flat), `test_checks_flag_out_of_range_and_short_notes`, `test_checks_flag_piano_above_flute`. Commit.

### Task 7: Render (`render.py`, `scripts/fetch_models.sh`)

**Interfaces:** `render_svg(musicxml: Path) -> list[str]` (SVG string per page; Verovio, pageWidth 2100, scale 40, adjustPageHeight); `render_audio(midi: Path, out_mp3: Path) -> Path|None` (fluidsynth + SOUNDFONT → wav → ffmpeg mp3 128k; None when a tool is missing).

fetch_models.sh downloads MuseScore_General.sf2 (MIT) into `models/` if missing.

- [ ] Tests: `test_render_svg_has_staff`, `test_render_audio_mp3` (skip without fluidsynth or soundfont). Commit.

### Task 8: Front end and pipeline (`audio.py`, `separate.py`, `transcribe.py`, `pipeline.py`)

**Interfaces:**
```python
audio.load_audio(src: Path, dst_wav: Path, max_seconds: int=720) -> float   # ffmpeg → 44.1k mono wav; ValueError("not audio") / ValueError("too long")
separate.separate(wav: Path, out_dir: Path) -> dict[str, Path]               # htdemucs: vocals, drums, bass, other
transcribe.piano(wav: Path) -> list[Note]      # ByteDance piano_transcription_inference, mps → cpu
transcribe.general(wav: Path) -> list[Note]    # basic-pitch (onnx)
pipeline.analyze(song_dir: Path, kind: str="auto", progress: Callable[[int,int,str],None]=noop) -> Analysis   # cached at song_dir/analysis.json
pipeline.arrange(an: Analysis, opts: ArrangeOptions) -> Arrangement
pipeline.arrange_and_render(song_dir: Path, opts: ArrangeOptions) -> dict   # {"svgs","musicxml","midi","mp3","warnings","arr"}; cached in song_dir/arr/<opts.key()>/
```
Auto kind: run demucs; vocals energy >15% of the mix → "song"; drums+bass <6% → "piano" (piano model on the full mix); else "band". Sources: piano → {"piano"}; song → {"vocals": clean_monophonic(general(vocals)), "other": general(other), "bass": general(bass)}; band → {"other", "bass"}. MIDI input (.mid): the track with the highest mean pitch among tracks with ≥20% of notes → "midi_melody", all → "midi_all"; grid from MIDI. arrange order: melody (auto = vocals > midi_melody > top_voice(piano or other)) → quantize_line → detect_key → easy key shift → fit_to_flute → add_breath_marks → accompany (duet) → check_arrangement into warnings.

- [ ] Tests: `test_upload_rejects_non_audio`, `test_pipeline_on_rendered_midi` (render a tiny MIDI with fluidsynth → analyze(kind="piano") → arrange solo → no check failures, melody F1 > 0.6). Commit.

### Task 9: Web app (`web/app.py`, `web/templates/`)

Routes: `GET /` (upload form + table of past songs); `POST /upload` (save to `out/<id>/input.<ext>`, queue analysis on one worker thread, redirect); `GET /song/<id>` (analyzing → progress page with `<meta http-equiv="refresh" content="2">` and "Step n of m: …"; failed → message + last log lines; done → options form (GET) + warnings + score SVGs + `<audio controls>` for preview and original + links Print / MusicXML / MIDI); `GET /song/<id>/print?...` (SVGs only, print CSS); `GET /song/<id>/file/<path>` (served only from inside the song dir); `POST /song/<id>/delete`. On startup, songs left mid-analysis are marked failed ("interrupted, please upload again").

```
Flute Arranger
Turn any song into flute sheet music.
-----------------------------------------
File: [Choose file]
What is it?  (o) let it guess  ( ) solo piano  ( ) song with singing  ( ) band, no singing
Make:        (o) flute only  ( ) flute + piano
[Upload]
-----------------------------------------
Your songs
| Title | Added | Status | |
```
- [ ] Tests (Flask test client, pipeline stubbed): `test_index_lists_songs`, `test_upload_redirects`, `test_song_page_progress`, `test_file_route_blocks_traversal`. Commit.

### Task 10: Evaluation and tuning (`eval/`)

- `make_groundtruth.py`: 12 Bach chorales from the music21 corpus → soprano = melody; renders set A (all voices on piano) and set B (soprano on a solo wind instrument + lower voices on piano, mixed). Saves wav + truth melody json under `samples/gt/`.
- `fetch_recordings.py`: 2–3 public-domain piano recordings from Wikimedia Commons into `samples/real/`.
- `run_eval.py`: analyze + arrange; melody note F1 (mir_eval, onset tolerance 80 ms, octave-agnostic pitch match) and check results; writes `eval/results.md`.
- [ ] Target: mean F1 ≥ 0.75 on set A, ≥ 0.6 on set B, zero check failures. Tune melody/quantize constants until met; record the numbers. Listen to and read the real-recording outputs. Commit.

### Task 11: Docs and final review

- [ ] README (what it is; setup: `brew install ffmpeg fluid-synth`, `uv venv --python 3.11 .venv`, `uv pip install -r requirements.txt`, `scripts/fetch_models.sh`, `./run.sh`; limits).
- [ ] Fresh reviewer pass on the whole branch; fix confirmed issues; final `pytest -q`; commit.
