"""Build the ground-truth benchmark set from Bach chorales (music21 corpus).

The soprano is the melody. For each of 12 chorales, samples/gt/<name>/ gets:
  truth.json   [[pitch, start_s, end_s], ...] of the soprano, as rendered
  full.mid     all four voices, humanized (same timing as the audio)
  A_piano.wav  all four voices on piano
  B_mix.wav    soprano on clarinet or violin, lower voices on piano, mixed
  meta.json    tempo, meter, instruments, durations
Three extra chorales get a harder render instead:
  C_arpeggio.wav  soprano over broken chords in 8ths built from the lower
                  voices, all on piano (full.mid has the same two parts)
plus samples/gt/index.json listing which pieces are in sets A, B and C.

Run: .venv/bin/python bench/make_groundtruth.py
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


def build_ab(idx: int, cid: str) -> dict:
    piece = load_chorale(cid)
    rng = np.random.default_rng(1000 + idx)
    bpm = float(rng.integers(72, 101))
    spb = 60.0 / bpm
    lead = spb                                     # one silent beat before the music
    base = int(rng.integers(62, 80))
    names = ["Soprano", "Alto", "Tenor", "Bass"]
    timed = [humanize(v, spb, lead, rng, base) for v in piece.voices]
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


def main() -> None:
    if not config.SOUNDFONT.exists():
        sys.exit(f"missing {config.SOUNDFONT}; run scripts/fetch_models.sh first")
    GT_DIR.mkdir(parents=True, exist_ok=True)
    index = {"A": [], "B": [], "C": []}
    t0 = time.time()
    for i, cid in enumerate(CHORALES):
        m = build_ab(i, cid)
        name = cid.replace(".", "_")
        index["A"].append(name)
        index["B"].append(name)
        print(f"{name:12s} {m['bpm']:.0f} bpm  {m['duration_s']:6.1f} s  {m['melody_notes']} melody notes", flush=True)
    for i, cid in enumerate(ARPEGGIO_CHORALES):
        m = build_c(i, cid)
        name = cid.replace(".", "_") + "_arp"
        index["C"].append(name)
        print(f"{name:12s} {m['bpm']:.0f} bpm  {m['duration_s']:6.1f} s  {m['melody_notes']} melody notes", flush=True)
    (GT_DIR / "index.json").write_text(json.dumps(index, indent=1))
    print(f"done in {time.time() - t0:.0f} s -> {GT_DIR}")


if __name__ == "__main__":
    main()
