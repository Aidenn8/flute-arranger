"""Build the ground-truth benchmark set from Bach chorales (music21 corpus).

The soprano is the melody. For each of 12 chorales, samples/gt/<name>/ gets:
  truth.json   [[pitch, start_s, end_s], ...] of the soprano, as rendered
  full.mid     all four voices, humanized (same timing as the audio)
  A_piano.wav  all four voices on piano
  B_mix.wav    soprano on clarinet or violin, lower voices on piano, mixed
  meta.json    tempo, meter, instruments, durations
  D_song.wav   set D, a sung song: soprano on a sung "ooh" voice with
               vibrato and scoops (pitch bends), lower voices on piano, and a
               kick/snare/hi-hat beat, mixed (same timing as truth.json)
  D_song.mid   the three parts of D_song.wav, pitch bends included
  D_meta.json  set D's render settings (meta.json stays as sets A/B wrote it)
Three extra chorales get a harder render instead:
  C_arpeggio.wav  soprano over broken chords in 8ths built from the lower
                  voices, all on piano (full.mid has the same two parts)
plus samples/gt/index.json listing which pieces are in sets A, B, C and D.

Run: .venv/bin/python bench/make_groundtruth.py          # every set
     .venv/bin/python bench/make_groundtruth.py D        # only these sets (A and B go together)
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pretty_midi
from music21 import corpus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sheetswap import config  # noqa: E402
from bench.synth import synthesize, write_wav  # noqa: E402

SR = 44100
GT_DIR = config.ROOT / "samples" / "gt"

# Plain SATB chorales (four named parts, no extra instruments).
CHORALES = [
    "bwv66.6", "bwv269", "bwv347", "bwv153.1", "bwv86.6", "bwv267",
    "bwv40.8", "bwv244.37", "bwv26.6", "bwv64.2", "bwv2.6", "bwv101.7",
]
ARPEGGIO_CHORALES = ["bwv104.6", "bwv108.6", "bwv113.8"]

CLARINET, VIOLIN = 71, 40
JITTER_S = 0.015          # +-15 ms onset/offset jitter
ALBERTI = [0, 2, 1, 2]    # low, high, middle, high

# Set D: a sung line. Vibrato and scoops are pitch bends on the voice's channel.
VOICE_OOHS = 53         # GM Voice Oohs; Choir Aahs (52) rings ~0.4 s into each next note, like a choir in a hall
BEND_RANGE_CENTS = 200    # the GM default pitch-bend range, +-2 semitones
BEND_STEP_S = 0.01        # one pitch-bend message every 10 ms
VIBRATO_HZ = (5.2, 5.8)   # per note
VIBRATO_CENTS = (40, 60)  # depth (+-), per note
VIBRATO_DELAY_S = 0.15    # vibrato starts this far into a note ...
VIBRATO_RAMP_S = 0.15     # ... and reaches full depth over this long
SCOOP_CHANCE = 0.6        # share of notes that start flat and slide up ...
SCOOP_CENTS = (20, 70)    # ... from this far under the pitch ...
SCOOP_S = (0.05, 0.12)    # ... over this long
DETUNE_SD_CENTS = 6       # per-note intonation error
KICK, SNARE, HIHAT = 36, 38, 42   # GM drum keys (channel 10)
DRUM_LEVEL = 0.6          # drums RMS relative to the piano's

Voice = list[tuple[int, float, float]]   # (pitch, start_ql, end_ql)
Timed = list[tuple[int, float, float, int]]  # (pitch, start_s, end_s, velocity)


@dataclass
class Piece:
    chorale: str
    name: str
    voices: list[Voice]        # soprano, alto, tenor, bass
    beats_per_bar: int
    pickup_ql: float           # length of the opening partial bar (0 if none)
    total_ql: float


def load_chorale(cid: str) -> Piece:
    score = corpus.parse(f"bach/{cid}")
    parts = list(score.parts)
    if len(parts) != 4:
        raise ValueError(f"{cid}: expected 4 parts, got {len(parts)}")
    voices: list[Voice] = []
    for part in parts:
        flat = part.stripTies().flatten()
        v: Voice = []
        for n in flat.notes:
            ql = float(n.quarterLength)
            if ql <= 0 or n.duration.isGrace:
                continue
            pitch = max(p.midi for p in n.pitches)   # a stray chord keeps its top note
            v.append((pitch, float(n.offset), float(n.offset) + ql))
        v.sort(key=lambda x: x[1])
        voices.append(v)
    ts = parts[0].recurse().getElementsByClass("TimeSignature").first()
    bpb = int(ts.numerator) if ts is not None and ts.denominator == 4 else 4
    m0 = parts[0].getElementsByClass("Measure").first()
    pickup = 0.0
    if m0 is not None and float(m0.duration.quarterLength) < bpb - 1e-6:
        pickup = float(m0.duration.quarterLength)
    total = max(e for v in voices for _, _, e in v)
    return Piece(cid, cid.replace(".", "_"), voices, bpb, pickup, total)


def humanize(voice: Voice, spb: float, lead: float, rng: np.random.Generator,
             base_vel: int, vel_sd: float = 7.0) -> Timed:
    """Seconds with +-15 ms jitter, slightly detached ends, varied velocity.
    Notes in one voice never overlap after this."""
    out: Timed = []
    for pitch, s_ql, e_ql in voice:
        s = lead + s_ql * spb + rng.uniform(-JITTER_S, JITTER_S)
        e = lead + e_ql * spb + rng.uniform(-JITTER_S, JITTER_S)
        e -= min(0.06, 0.1 * (e - s))                  # small gap before the next note
        vel = int(np.clip(round(base_vel + rng.normal(0, vel_sd)), 30, 115))
        out.append((pitch, max(0.0, s), e, vel))
    out.sort(key=lambda x: x[1])
    fixed: Timed = []
    for i, (p, s, e, v) in enumerate(out):
        if i + 1 < len(out):
            e = min(e, out[i + 1][1] - 0.01)
        fixed.append((p, s, max(e, s + 0.05), v))
    return fixed


def arpeggiate(piece: Piece) -> Voice:
    """Broken chords in 8ths from the alto, tenor and bass sounding at each 8th."""
    lower = piece.voices[1:]
    out: Voice = []
    q, k = 0.0, 0
    while q < piece.total_ql - 1e-6:
        chord = sorted({p for v in lower for (p, s, e) in v if s <= q + 1e-6 < e})
        if chord:
            idx = min(ALBERTI[k % 4], len(chord) - 1) if len(chord) >= 3 else (k % len(chord))
            out.append((chord[idx], q, min(q + 0.9, piece.total_ql)))  # held a little, like pedal
        q += 0.5
        k += 1
    return out


def make_midi(parts: list[tuple[str, int, Timed]], bpm: float, bpb: int) -> pretty_midi.PrettyMIDI:
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(bpb, 4, 0.0))
    for name, program, notes in parts:
        inst = pretty_midi.Instrument(program=program, name=name)
        inst.notes = [pretty_midi.Note(velocity=v, pitch=p, start=s, end=e) for p, s, e, v in notes]
        pm.instruments.append(inst)
    return pm


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0


def pad_to(x: np.ndarray, n: int) -> np.ndarray:
    return x if len(x) >= n else np.concatenate([x, np.zeros((n - len(x), x.shape[1]), x.dtype)])


def write_truth(path: Path, melody: Timed) -> None:
    path.write_text(json.dumps([[p, round(s, 4), round(e, 4)] for p, s, e, _ in melody]))


def humanized_satb(idx: int, cid: str) -> tuple[Piece, float, float, list[Timed]]:
    """The chorale and its four humanized voices as sets A, B and D play them:
    (piece, bpm, lead-in seconds, [soprano, alto, tenor, bass])."""
    piece = load_chorale(cid)
    rng = np.random.default_rng(1000 + idx)
    bpm = float(rng.integers(72, 101))
    spb = 60.0 / bpm
    lead = spb                                     # one silent beat before the music
    base = int(rng.integers(62, 80))
    timed = [humanize(v, spb, lead, rng, base) for v in piece.voices]
    return piece, bpm, lead, timed


def build_ab(idx: int, cid: str) -> dict:
    piece, bpm, lead, timed = humanized_satb(idx, cid)
    spb = 60.0 / bpm
    names = ["Soprano", "Alto", "Tenor", "Bass"]
    out = GT_DIR / piece.name
    out.mkdir(parents=True, exist_ok=True)

    write_truth(out / "truth.json", timed[0])
    full = make_midi([(n, config.PIANO_PROGRAM, t) for n, t in zip(names, timed)], bpm, piece.beats_per_bar)
    full.write(str(out / "full.mid"))

    # A: everything on piano.
    a = synthesize(full, config.SOUNDFONT, SR)
    write_wav(a, out / "A_piano.wav", SR)

    # B: soprano on a solo instrument, lower voices on piano, mixed at equal loudness.
    solo = CLARINET if idx % 2 == 0 else VIOLIN
    mel = synthesize(make_midi([("Soprano", solo, timed[0])], bpm, piece.beats_per_bar), config.SOUNDFONT, SR)
    acc = synthesize(make_midi([(n, config.PIANO_PROGRAM, t) for n, t in zip(names[1:], timed[1:])],
                               bpm, piece.beats_per_bar), config.SOUNDFONT, SR)
    n = max(len(mel), len(acc))
    mel, acc = pad_to(mel, n), pad_to(acc, n)
    mix = mel * (rms(acc) / max(rms(mel), 1e-9)) + acc
    write_wav(mix, out / "B_mix.wav", SR)

    meta = {
        "chorale": cid, "set": ["A", "B"], "bpm": bpm, "beats_per_bar": piece.beats_per_bar,
        "pickup_beats": piece.pickup_ql, "first_note_s": round(lead, 4),
        "first_downbeat_s": round(lead + piece.pickup_ql * spb, 4),
        "melody_program_B": solo, "melody_notes": len(timed[0]),
        "duration_s": round(len(a) / SR, 3),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def build_c(idx: int, cid: str) -> dict:
    piece = load_chorale(cid)
    rng = np.random.default_rng(2000 + idx)
    bpm = float(rng.integers(72, 101))
    spb = 60.0 / bpm
    lead = spb
    base = int(rng.integers(62, 76))
    melody = humanize(piece.voices[0], spb, lead, rng, base + 12, vel_sd=6)   # melody voiced a bit louder
    arp = humanize(arpeggiate(piece), spb, lead, rng, base - 8, vel_sd=6)
    out = GT_DIR / f"{piece.name}_arp"
    out.mkdir(parents=True, exist_ok=True)
    write_truth(out / "truth.json", melody)
    pm = make_midi([("Melody", config.PIANO_PROGRAM, melody), ("Arpeggio", config.PIANO_PROGRAM, arp)],
                   bpm, piece.beats_per_bar)
    pm.write(str(out / "full.mid"))
    c = synthesize(pm, config.SOUNDFONT, SR)
    write_wav(c, out / "C_arpeggio.wav", SR)
    meta = {
        "chorale": cid, "set": ["C"], "bpm": bpm, "beats_per_bar": piece.beats_per_bar,
        "pickup_beats": piece.pickup_ql, "first_note_s": round(lead, 4),
        "first_downbeat_s": round(lead + piece.pickup_ql * spb, 4),
        "melody_notes": len(melody), "accompaniment_notes": len(arp),
        "duration_s": round(len(c) / SR, 3),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def sung_bends(melody: Timed, rng: np.random.Generator) -> list[pretty_midi.PitchBend]:
    """Pitch bends that make a held synth voice sound sung: per note a small
    detune, often a scoop up from under the pitch, then vibrato that starts
    VIBRATO_DELAY_S in and fades in over VIBRATO_RAMP_S."""
    bends: list[pretty_midi.PitchBend] = []
    for _p, s, e, _v in melody:
        detune = rng.normal(0, DETUNE_SD_CENTS)
        scoop = rng.uniform(*SCOOP_CENTS) if rng.random() < SCOOP_CHANCE else 0.0
        scoop_s = rng.uniform(*SCOOP_S)
        rate = rng.uniform(*VIBRATO_HZ)
        depth = rng.uniform(*VIBRATO_CENTS)
        t = np.arange(0.0, e - s, BEND_STEP_S)
        tv = np.maximum(t - VIBRATO_DELAY_S, 0.0)
        cents = (detune - scoop * np.clip(1 - t / scoop_s, 0, 1)
                 + depth * np.clip(tv / VIBRATO_RAMP_S, 0, 1) * np.sin(2 * np.pi * rate * tv))
        values = np.clip(np.round(cents / BEND_RANGE_CENTS * 8192), -8192, 8191).astype(int)
        bends += [pretty_midi.PitchBend(int(v), float(s + x)) for v, x in zip(values, t)]
    return bends


def drum_part(piece: Piece, bpm: float, lead: float, rng: np.random.Generator) -> Timed:
    """A plain beat under the whole piece: closed hi-hat on every 8th, kick on
    beat 1 (and 3 in 4/4), snare on the other beats."""
    spb = 60.0 / bpm
    hits: Timed = []
    x = 0.0
    while x < piece.total_ql - 1e-6:
        t = lead + x * spb
        pos = (x - piece.pickup_ql) % piece.beats_per_bar
        on_beat = pos % 1 == 0
        hits.append((HIHAT, t, t + 0.1, int(rng.integers(50, 66)) + (12 if on_beat else 0)))
        if on_beat:
            beat = int(pos)
            if beat == 0 or (piece.beats_per_bar == 4 and beat == 2):
                hits.append((KICK, t, t + 0.1, int(rng.integers(95, 111))))
            else:
                hits.append((SNARE, t, t + 0.1, int(rng.integers(85, 101))))
        x += 0.5
    return hits


def _midi_of(insts: list[pretty_midi.Instrument], bpm: float, bpb: int) -> pretty_midi.PrettyMIDI:
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(bpb, 4, 0.0))
    pm.instruments.extend(insts)
    return pm


def build_d(idx: int, cid: str) -> dict:
    """Set D: the A/B timing (so truth.json holds), soprano sung on an "ooh"
    voice with vibrato, lower voices on piano, and drums, mixed like a song."""
    piece, bpm, lead, timed = humanized_satb(idx, cid)
    bpb = piece.beats_per_bar
    out = GT_DIR / piece.name
    out.mkdir(parents=True, exist_ok=True)
    truth = json.dumps([[p, round(s, 4), round(e, 4)] for p, s, e, _ in timed[0]])
    if not (out / "truth.json").exists():
        write_truth(out / "truth.json", timed[0])
    elif (out / "truth.json").read_text() != truth:
        raise RuntimeError(f"{piece.name}: truth.json doesn't match the soprano timing")

    rng = np.random.default_rng(3000 + idx)
    voice = make_midi([("Soprano", VOICE_OOHS, timed[0])], bpm, bpb).instruments[0]
    voice.pitch_bends = sung_bends(timed[0], rng)
    names = ["Alto", "Tenor", "Bass"]
    piano = make_midi([(n, config.PIANO_PROGRAM, t) for n, t in zip(names, timed[1:])], bpm, bpb).instruments
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    drums.notes = [pretty_midi.Note(velocity=v, pitch=k, start=s, end=e)
                   for k, s, e, v in drum_part(piece, bpm, lead, rng)]
    _midi_of([voice, *piano, drums], bpm, bpb).write(str(out / "D_song.mid"))

    sung = synthesize(_midi_of([voice], bpm, bpb), config.SOUNDFONT, SR)
    acc = synthesize(_midi_of(piano, bpm, bpb), config.SOUNDFONT, SR)
    beat = synthesize(_midi_of([drums], bpm, bpb), config.SOUNDFONT, SR)
    n = max(len(sung), len(acc), len(beat))
    sung, acc, beat = pad_to(sung, n), pad_to(acc, n), pad_to(beat, n)
    mix = (sung * (rms(acc) / max(rms(sung), 1e-9)) + acc
           + beat * (DRUM_LEVEL * rms(acc) / max(rms(beat), 1e-9)))
    write_wav(mix, out / "D_song.wav", SR)

    meta = {
        "chorale": cid, "set": ["D"], "bpm": bpm, "beats_per_bar": bpb,
        "voice_program": VOICE_OOHS, "vibrato_hz": list(VIBRATO_HZ),
        "vibrato_cents": list(VIBRATO_CENTS), "vibrato_delay_s": VIBRATO_DELAY_S,
        "drum_level": DRUM_LEVEL, "melody_notes": len(timed[0]),
        "duration_s": round(n / SR, 3),
    }
    (out / "D_meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def main() -> None:
    want = {s.upper() for s in sys.argv[1:]} or {"A", "B", "C", "D"}
    if want - {"A", "B", "C", "D"}:
        sys.exit("usage: make_groundtruth.py [A] [B] [C] [D]   (no sets = all)")
    if not config.SOUNDFONT.exists():
        sys.exit(f"missing {config.SOUNDFONT}; run scripts/fetch_models.sh first")
    GT_DIR.mkdir(parents=True, exist_ok=True)
    index_path = GT_DIR / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    t0 = time.time()
    if want & {"A", "B"}:
        index["A"], index["B"] = [], []
        for i, cid in enumerate(CHORALES):
            m = build_ab(i, cid)
            name = cid.replace(".", "_")
            index["A"].append(name)
            index["B"].append(name)
            print(f"{name:12s} {m['bpm']:.0f} bpm  {m['duration_s']:6.1f} s  {m['melody_notes']} melody notes", flush=True)
    if "C" in want:
        index["C"] = []
        for i, cid in enumerate(ARPEGGIO_CHORALES):
            m = build_c(i, cid)
            name = cid.replace(".", "_") + "_arp"
            index["C"].append(name)
            print(f"{name:12s} {m['bpm']:.0f} bpm  {m['duration_s']:6.1f} s  {m['melody_notes']} melody notes", flush=True)
    if "D" in want:
        index["D"] = []
        for i, cid in enumerate(CHORALES):
            m = build_d(i, cid)
            name = cid.replace(".", "_")
            index["D"].append(name)
            print(f"{name:12s} D {m['bpm']:.0f} bpm  {m['duration_s']:6.1f} s  sung + piano + drums", flush=True)
    index = {k: index[k] for k in ("A", "B", "C", "D") if k in index}
    index_path.write_text(json.dumps(index, indent=1))
    print(f"done in {time.time() - t0:.0f} s -> {GT_DIR}")


if __name__ == "__main__":
    main()
