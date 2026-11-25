"""Build the reference set for bench/ref_score.py: real recordings + the real sheet music.

    .venv/bin/python -I bench/make_refs.py            # download what's missing, then write ref.json
    .venv/bin/python -I bench/make_refs.py --no-fetch # only rebuild ref.json from the downloads
    .venv/bin/python -I bench/make_refs.py --show fur_elise   # print the first bars of a reference

Every piece gets a folder samples/ref/<piece>/ (git-ignored) with one folder per download:
  rec/     the recording (Wikimedia Commons, or reused from samples/real/) + source.json
  score/   the machine-readable score (Mutopia Project MIDI + LilyPond source) + source.json
  flute/   a published flute arrangement of the same piece, when one exists
and the files this script writes from them:
  ref.json        the true melody a flutist would play (see `write_ref`)
  ref_flute.json  the same from the flute arrangement

The melody is taken from the score's MIDI (one track per staff/voice, written by
LilyPond from the engraved source): the right hand's top voice for piano, the
violin part for violin. What to take, which repeats the performer plays and any
hand fixes are written out per piece in PIECES, checked against the LilyPond
source and the recording. Downloads are treated as plain data: nothing in them is
run, and zip files are only read for their .mid/.ly members.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from fetch_recordings import (decode_check, download, file_info, get_with_retry,  # noqa: E402
                              license_ok, session)

REF = ROOT / "samples" / "ref"
REAL = ROOT / "samples" / "real"
MUTOPIA = "https://www.mutopiaproject.org/ftp/"
MAX_SCORE_BYTES = 5 * 1024 * 1024

# ---------------------------------------------------------------- the pieces
# rec: ("real", <folder in samples/real>) or ("commons", <File: title>)
# score / flute: Mutopia ftp path without extension (".mid" + ".ly"), or a
#   ("zip", <path to -mids.zip or .mid>, <path to -lys.zip>) pair for multi-file pieces.
# kind: what a user would pick on the upload page ("piano" = solo piano,
#   "ensemble" = piano/other instruments, no singing or drums).

PIECES: dict[str, dict] = {
    "fur_elise": dict(
        instrument="piano", kind="piano", title="Beethoven: Für Elise, WoO 59",
        rec=("real", "beethoven_fur_elise"),
        score="BeethovenLv/WoO59/fur_Elise_WoO59/fur_Elise_WoO59",
        # right hand, broken-chord notes included (flute versions keep them); both repeats
        ref=dict(meter=(3, 8), key="A minor", tempo_text="Poco moto", pickup=0.5,
                 form=[(0, 12), (0, 11), (12, 34.5), (13.5, 33), (34.5, None)])),
    "gymnopedie_1": dict(
        instrument="piano", kind="piano", title="Satie: Gymnopédie No. 1",
        rec=("real", "satie_gymnopedie_1"),
        score="SatieE/gymnopedie_1/gymnopedie_1",
        # the tune only: the 4-bar chords-only intro is left out; Satie's two endings
        # (bars 1-31, then 32-39 / 40-47). The chords re-strike the tune's long F#4 in
        # bars 9-12, which splits it in the MIDI: joined back into one note.
        ref=dict(meter=(3, 4), key="D major", tempo_text="Lent et douloureux",
                 form=[(0, 93), (93, 117), (0, 93), (117, None)], drop=[(0, 12)],
                 join=[(24, 36)])),
    "nocturne_op9_2": dict(
        instrument="piano", kind="piano", title="Chopin: Nocturne in E-flat, Op. 9 No. 2",
        rec=("real", "chopin_nocturne_op9_2"),
        score="ChopinFF/O9/chopin_nocturne_op9_n2/chopin_nocturne_op9_n2",
        ref=dict(meter=(12, 8), key="E-flat major", bpm=66, tempo_text="Andante, eighth = 132",
                 pickup=0.5)),
    "traumerei": dict(
        instrument="piano", kind="piano", title="Schumann: Träumerei, Op. 15 No. 7",
        rec=("commons", "File:Robert Schumann - scenes from childhood, op. 15 - vii. dreaming.ogg"),
        score="SchumannR/O15/SchumannOp15No07/SchumannOp15No07",
        # bars 1-8 repeated
        ref=dict(meter=(4, 4), key="F major", bpm=100, tempo_text="quarter = 100 (Schumann)",
                 pickup=1.0, form=[(0, 33), (1, 33), (33, None)])),
    "clair_de_lune": dict(
        instrument="piano", kind="piano", title="Debussy: Clair de lune",
        rec=("commons", "File:Clair de lune (Claude Debussy) Suite bergamasque.ogg"),
        score="DebussyC/L75/debussy_Ste_Bergamesq_Clair/debussy_Ste_Bergamesq_Clair",
        ref=dict(meter=(9, 8), key="D-flat major", tempo_text="Andante très expressif")),
    "gavotte_bwv1006": dict(
        instrument="violin", kind="ensemble", title="Bach: Gavotte en rondeau, Partita No. 3, BWV 1006",
        rec=("commons", "File:J.S.Bach partita No.3 Gavotte in Rondeau.ogg"),
        score="BachJS/BWV1006/bwv-1006_3/bwv-1006_3",
        # top line of the double stops; refrain repeated; the edition's MIDI has an
        # empty spacer bar at quarters 32-36
        ref=dict(meter=(2, 2), key="E major", pickup=2.0, form=[(0, 32), (0, 32), (36, None)])),
    "menuet_bwv1006": dict(
        instrument="violin", kind="ensemble", title="Bach: Menuet I, Partita No. 3, BWV 1006",
        rec=("commons", "File:Partita No.3 J.S.Bach (Minuet 1).ogg"),
        score="BachJS/BWV1006/bwv-1006_4/bwv-1006_4",
        # the performer repeats bars 1-8 but not bars 9-34 (checked against the recording:
        # 123 written quarters in 68 s, and the best match of all four ways to play it)
        ref=dict(meter=(3, 4), key="E major", form=[(0, 24), (0, 24), (24, None)])),
    "allemanda_bwv1004": dict(
        instrument="violin", kind="ensemble", title="Bach: Allemanda, Partita No. 2, BWV 1004",
        rec=("commons", "File:Bach Partita 2 - Allemanda - 18-04-2013.ogg"),
        score="BachJS/BWV1004/bwv-1004_1/bwv-1004_1",
        # (the Commons description says "1st Partita"; the notes are BWV 1004's Allemanda)
        # played without the repeats; each half of the edition's MIDI ends with a spacer
        # 16th where the next upbeat goes
        ref=dict(meter=(4, 4), key="D minor", pickup=0.25, form=[(0, 64), (64.25, 128.25)])),
    "schubert_ave_maria": dict(
        instrument="violin", kind="ensemble", title="Schubert: Ave Maria, D. 839 (violin and piano)",
        rec=("commons", "File:Schubert - Ave Maria, arr. for violin and piano (Menuhin, Baller).flac"),
        score="SchubertF/D839/SchubertF-D839_AveMaria/SchubertF-D839_AveMaria",
        # the sung line is the tune the violin plays; the MIDI has all three verses written
        # out and starts with one quarter of silence
        ref=dict(tracks=[0], meter=(4, 4), key="B-flat major", tempo_text="Sehr langsam",
                 pickup=1.0)),
    "air_bwv1068": dict(
        instrument="violin", kind="ensemble", title="Bach: Air, Suite No. 3, BWV 1068 (strings)",
        rec=("commons", "File:Air - Air Force Strings - United States Air Force Band.mp3"),
        score=("zip", "BachJS/BWV1068/bach-air/bach-air-mids.zip", "BachJS/BWV1068/bach-air/bach-air-lys.zip"),
        flute="BachJS/BWV1068/bach_air_bmv_1068/bach_air_bmv_1068",
        # first violin (the tune); bars 1-5 + two equal endings (6, 7), bars 8-19 repeated
        ref=dict(file="bach-air-violin1.mid", meter=(4, 4), key="D major",
                 form=[(0, 24), (0, 20), (24, 28), (28, None), (28, None)]),
        ref_flute=dict(file="bach_air_bmv_1068.mid", instrument="flute", meter=(4, 4), key="D major",
                       form=[(0, 24), (0, 20), (24, 28), (28, None), (28, None)])),
    # ---- more solo piano (added to widen the judge: melody in chords, over triplets,
    # dotted rhythms, waltzes, Alberti bass, a slow alla breve)
    "chopin_prelude_4": dict(
        instrument="piano", kind="piano", title="Chopin: Prelude in E minor, Op. 28 No. 4",
        rec=("commons", "File:Ivan Ilić-Chopin Prelude Opus 28 n.4.ogg"),
        score="ChopinFF/O28/Chop-28-4/Chop-28-4",
        ref=dict(meter=(2, 2), key="E minor", tempo_text="Largo", pickup=1.0)),
    "chopin_prelude_7": dict(
        instrument="piano", kind="piano", title="Chopin: Prelude in A major, Op. 28 No. 7",
        rec=("commons", "File:Ivan Ilić-Chopin - Prelude no. 7 in A major.ogg"),
        score=("zip", "ChopinFF/O28/Chop-28-7/Chop-28-7.mid", "ChopinFF/O28/Chop-28-7/Chop-28-7-lys.zip"),
        ref=dict(meter=(3, 4), key="A major", tempo_text="Andantino", pickup=1.0)),
    "chopin_prelude_20": dict(
        instrument="piano", kind="piano", title="Chopin: Prelude in C minor, Op. 28 No. 20",
        # Musopen's recording; Commons' "Prelude no. 20 op. 28 c minor.ogg" is a MIDI file
        # played by a soundfont, not a performance
        rec=("commons", "File:Chopin - Preludes, Op. 28 - No. 20 'Funeral march'.ogg"),
        score="ChopinFF/O28/Chop-28-20/Chop-28-20",
        ref=dict(meter=(4, 4), key="C minor", tempo_text="Largo")),
    "chopin_waltz_69_2": dict(
        instrument="piano", kind="piano", title="Chopin: Waltz in B minor, Op. 69 No. 2",
        rec=("commons", "File:Chopin - Waltz No. 10 in B minor, Op. 69 No. 2 (Olga Gurevich).flac"),
        score="ChopinFF/O69/w10-h-moll-cfi/w10-h-moll-cfi",
        # A (bars 1-32), B (33-64) played twice with its two endings, Trio, A again
        # (the best match of the two ways; 149 quarters a minute against the edition's 152)
        ref=dict(meter=(3, 4), key="B minor", tempo_text="Moderato", pickup=1.0,
                 form=[(0, 193), (97, 190), (193, None)])),
    "kinderszenen_1": dict(
        instrument="piano", kind="piano", title="Schumann: Von fremden Ländern und Menschen, Op. 15 No. 1",
        rec=("commons", "File:Robert Schumann - scenes from childhood, op. 15 - i. of foreign lands and peoples.ogg"),
        score="SchumannR/O15/SchumannOp15No01/SchumannOp15No01",
        # both halves repeated (bars 1-8, 9-22): the best match of the four ways to play it
        ref=dict(meter=(2, 4), key="G major", pickup=1.0,
                 form=[(0, 17), (1, 17), (17, None), (17, None)])),
    "mozart_k545_1": dict(
        instrument="piano", kind="piano", title="Mozart: Piano Sonata in C, K. 545, I. Allegro",
        rec=("commons", "File:Wolfgang Amadeus Mozart - sonata no. 16 in c major, k.545 'sonata facile' - i. allegro.ogg"),
        score="MozartWA/KV545/K545-1/K545-1",
        # played without the repeats (134 s; with the exposition repeated the match is far worse)
        ref=dict(meter=(4, 4), key="C major", tempo_text="Allegro")),
    "moonlight_1": dict(
        instrument="piano", kind="piano", title="Beethoven: Sonata Op. 27 No. 2 ('Moonlight'), I. Adagio sostenuto",
        rec=("commons", "File:Ludwig van Beethoven - sonata no. 14 in c sharp minor 'moonlight', op. 27 no. 2 - i. adagio sostenuto.ogg"),
        score=("zip", "BeethovenLv/O27/moonlight/moonlight-mids.zip", "BeethovenLv/O27/moonlight/moonlight-lys.zip"),
        ref=dict(file="moonlight1.mid", meter=(2, 2), key="C-sharp minor", tempo_text="Adagio sostenuto",
                 drop_len=[1 / 3])),
    "brahms_waltz_15": dict(
        instrument="piano", kind="piano", title="Brahms: Waltz in A-flat, Op. 39 No. 15",
        rec=("commons", "File:Brahms-waltz15.ogg"),
        score="BrahmsJ/O39/waltz-op39-15/waltz-op39-15",
        ref=dict(meter=(3, 4), key="A-flat major")),
}


# ---------------------------------------------------------------- downloading

def _get_small(s, url: str) -> bytes:
    r = get_with_retry(s, url)
    if len(r.content) > MAX_SCORE_BYTES:
        raise ValueError(f"{url}: larger than {MAX_SCORE_BYTES} bytes")
    return r.content


def fetch_mutopia(s, spec, folder: Path) -> None:
    """Mutopia files into `folder` (MIDI + LilyPond), with source.json."""
    if (folder / "source.json").exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    if isinstance(spec, tuple):
        for zpath in spec[1:]:
            data = _get_small(s, MUTOPIA + zpath)
            if zpath.endswith((".mid", ".ly")):          # a plain file next to a zip of the others
                (folder / Path(zpath).name).write_bytes(data)
                files.append(Path(zpath).name)
                time.sleep(1.0)
                continue
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = Path(info.filename).name          # flat: never a path from the zip
                    if info.is_dir() or not name.endswith((".mid", ".midi", ".ly", ".ily")):
                        continue
                    if info.file_size > MAX_SCORE_BYTES:
                        continue
                    (folder / name).write_bytes(z.read(info))
                    files.append(name)
            time.sleep(1.0)
        page = MUTOPIA + str(Path(spec[1]).parent) + "/"
    else:
        for ext in (".mid", ".ly"):
            name = Path(spec).name + ext
            (folder / name).write_bytes(_get_small(s, MUTOPIA + spec + ext))
            files.append(name)
            time.sleep(1.0)
        page = MUTOPIA + str(Path(spec).parent) + "/"
    (folder / "source.json").write_text(json.dumps(
        {"site": "Mutopia Project", "ftp_area": page, "files": files,
         "note": "license: see the LilyPond header (mutopia license) and REFERENCES.md"},
        indent=1, ensure_ascii=False))


def fetch_preview(s, spec, folder: Path) -> None:
    """Mutopia's picture of the first lines of the printed score, to look at next to ours."""
    if isinstance(spec, tuple) or not folder.exists() or (folder / "preview.png").exists():
        return
    r = s.get(MUTOPIA + spec + "-preview.png", timeout=60)
    if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) < MAX_SCORE_BYTES:
        (folder / "preview.png").write_bytes(r.content)
    time.sleep(1.0)


def fetch_recording(s, rec, folder: Path) -> None:
    if (folder / "source.json").exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    how, what = rec
    if how == "real":
        src = REAL / what
        meta = json.loads((src / "source.json").read_text())
        shutil.copy(src / meta["file"], folder / meta["file"])
        meta["copied_from"] = f"samples/real/{what}"
        (folder / "source.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
        return
    info = file_info(s, what)
    if not license_ok(info["license"]):
        raise ValueError(f"license not allowed: {info['license']}")
    name = "recording" + info["file_ext"]
    dest = folder / name
    download(s, info, dest)
    dur, db = decode_check(dest)
    if dur > 6 * 60 + 5:
        raise ValueError(f"{dur:.0f} s is longer than 6 minutes")
    info.update(file=name, decoded_s=round(dur, 2), rms_dbfs=round(db, 1))
    (folder / "source.json").write_text(json.dumps(info, indent=1, ensure_ascii=False))


def fetch_all(only: list[str]) -> None:
    s = session()
    for slug, p in PIECES.items():
        if only and slug not in only:
            continue
        d = REF / slug
        print(slug, flush=True)
        jobs = [(fetch_recording, p["rec"], "rec"), (fetch_mutopia, p["score"], "score"),
                (fetch_preview, p["score"], "score")]
        if p.get("flute"):
            jobs += [(fetch_mutopia, p["flute"], "flute"), (fetch_preview, p["flute"], "flute")]
        for fn, spec, folder in jobs:
            try:
                fn(s, spec, d / folder)
            except Exception as e:      # one bad source shouldn't stop the rest
                print(f"  {folder} failed: {e}", flush=True)
        time.sleep(1.0)


# ---------------------------------------------------------------- the melody from the score

GRID = 48            # positions per quarter note a written note can start on (16th triplets, 32nds)


def midi_notes(path: Path, tracks: list[int]) -> list[list[float]]:
    """[pitch, onset, end] in quarter notes from the MIDI start (tick based, so tempo
    changes in the file don't matter)."""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(str(path))
    res = pm.resolution
    out = []
    for i in tracks:
        for n in pm.instruments[i].notes:
            out.append([n.pitch, pm.time_to_tick(n.start) / res, pm.time_to_tick(n.end) / res])
    out.sort(key=lambda r: (r[1], -r[0]))
    return out


def _on_grid(q: float) -> bool:
    return abs(q * GRID - round(q * GRID)) < 0.02


def split_graces(notes: list[list[float]]) -> tuple[list[list[float]], list[list[float]]]:
    """LilyPond plays a grace note as a very short note squeezed in before the main
    note, off the rhythmic grid (and shortens the note before it). Returns (notes, graces)."""
    keep, graces = [], []
    for r in notes:
        if _on_grid(r[1]):
            keep.append(r)
        elif r[2] - r[1] < 0.12:
            graces.append(r)
        elif r[2] - r[1] >= 0.5:
            # a grace note of the same pitch merged into its main note: start on the beat
            # part the main note was written on (the next 8th-triplet / 16th position)
            s = min(np.ceil(r[1] * 4 - 1e-6) / 4, np.ceil(r[1] * 3 - 1e-6) / 3)
            keep.append([r[0], s, r[2]] if s - r[1] < 0.2 else r)
        else:
            keep.append(r)
    return keep, graces


def skyline(notes: list[list[float]]) -> list[list[float]]:
    """The top line: at each onset the highest new note, unless a note at least as high
    is still sounding from before (an inner voice under a held melody note)."""
    out = []
    i = 0
    while i < len(notes):
        t = notes[i][1]
        group = [r for r in notes[i:] if abs(r[1] - t) < 1e-6]
        i += len(group)
        top = max(group, key=lambda r: r[0])
        held = any(r[1] < t - 1e-6 and r[2] > t + 1e-6 and r[0] >= top[0] for r in notes)
        if not held:
            out.append(list(top))
    for a, b in zip(out, out[1:]):          # one line: a note ends where the next begins
        a[2] = min(a[2], b[1])
    return out


def close_grace_gaps(line: list[list[float]], graces: list[list[float]]) -> None:
    """Give back the time a removed grace note took from the note before it."""
    for a, b in zip(line, line[1:]):
        if 0 < b[1] - a[2] < 0.2 and any(a[2] - 1e-6 <= g[1] < b[1] for g in graces):
            a[2] = b[1]


def join_repeats(line: list[list[float]], a: float, b: float) -> list[list[float]]:
    """Within [a, b): touching notes of the same pitch become one held note (a tie the
    MIDI lost because another voice struck the same key)."""
    out: list[list[float]] = []
    for r in line:
        prev = out[-1] if out else None
        if (prev and a - 1e-6 <= prev[1] and r[1] < b - 1e-6 and prev[0] == r[0]
                and abs(prev[2] - r[1]) < 1e-6):
            prev[2] = r[2]
        else:
            out.append(list(r))
    return out


def unfold(line: list[list[float]], form: list[tuple[float, float | None]]) -> list[list[float]]:
    """Play the score's segments in order (repeats as the performer plays them)."""
    end_all = max((r[2] for r in line), default=0.0)
    out, cursor = [], 0.0
    for a, b in form:
        b = end_all if b is None else b
        for p, s, e in line:
            if a - 1e-6 <= s < b - 1e-6:
                out.append([p, cursor + s - a, cursor + min(e, b) - a])
        cursor += b - a
    return out


def build_ref(slug: str, spec: dict, folder: str = "score", key: str = "ref") -> dict:
    cfg = spec[key]
    d = REF / slug / folder
    mids = sorted(d.glob("*.mid"))
    path = d / cfg["file"] if cfg.get("file") else mids[0]
    notes = midi_notes(path, cfg.get("tracks", [0]))
    notes, graces = split_graces(notes)
    for a, b in cfg.get("drop", []):
        notes = [r for r in notes if not (a - 1e-6 <= r[1] < b - 1e-6)]
    for length in cfg.get("drop_len", []):
        # an accompaniment voice in the tune's staff, told apart by its note value
        # (Moonlight: every 8th-triplet of the right hand is accompaniment)
        # (LilyPond rounds tuplet lengths down by a MIDI tick or two)
        notes = [r for r in notes if abs(r[2] - r[1] - length) > 0.01]
    line = skyline(notes)
    close_grace_gaps(line, graces)
    for a, b in cfg.get("join", []):
        line = join_repeats(line, a, b)
    line = unfold(line, cfg.get("form", [(0.0, None)]))
    pick = cfg.get("pickup", 0.0)
    num, den = cfg["meter"]
    return {
        "piece": slug, "title": spec["title"], "instrument": cfg.get("instrument", spec["instrument"]),
        "beat": "quarter note: onset_beats and dur_beats count quarter notes from the first downbeat",
        "meter": [num, den], "bar_beats": num * 4 / den, "pickup_beats": pick,
        "key": cfg["key"], "bpm_marking": cfg.get("bpm"), "tempo_text": cfg.get("tempo_text", ""),
        "source": f"{folder}/{path.name}, tracks {cfg.get('tracks', [0])}",
        "form": cfg.get("form", [(0.0, None)]), "graces_removed": len(graces),
        "notes": [{"pitch": int(p), "onset_beats": round(s - pick, 4), "dur_beats": round(e - s, 4)}
                  for p, s, e in line if e > s],
    }


NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def show(ref: dict, bars: int = 12) -> None:
    """Print the first bars of a reference, one bar per line, as name@beat+length."""
    bar = ref["bar_beats"]
    rows: dict[int, list[str]] = {}
    for n in ref["notes"]:
        b = int((n["onset_beats"] + 1e-6) // bar)
        if b >= bars:
            break
        name = f"{NAMES[n['pitch'] % 12]}{n['pitch'] // 12 - 1}"
        rows.setdefault(b, []).append(f"{name}@{n['onset_beats'] - b * bar:g}+{n['dur_beats']:g}")
    print(f"{ref['piece']}: {ref['meter'][0]}/{ref['meter'][1]}, {ref['key']}, "
          f"{len(ref['notes'])} notes, pickup {ref['pickup_beats']}")
    for b in sorted(rows):
        print(f"  bar {b + 1:3d}: " + " ".join(rows[b]))


def write_refs(only: list[str]) -> None:
    for slug, spec in PIECES.items():
        if (only and slug not in only) or "ref" not in spec:
            continue
        for key, folder, name in (("ref", "score", "ref.json"), ("ref_flute", "flute", "ref_flute.json")):
            if key not in spec or not (REF / slug / folder).exists():
                continue
            ref = build_ref(slug, spec, folder, key)
            (REF / slug / name).write_text(json.dumps(ref, indent=1, ensure_ascii=False))
            print(f"{slug}/{name}: {len(ref['notes'])} notes, "
                  f"{ref['notes'][-1]['onset_beats'] / ref['bar_beats']:.0f} bars")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pieces", nargs="*")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the first bars of each reference")
    ap.add_argument("--bars", type=int, default=12)
    a = ap.parse_args()
    if not a.no_fetch:
        fetch_all(a.pieces)
    write_refs(a.pieces)
    if a.show:
        for slug in a.pieces or list(PIECES):
            for name in ("ref.json", "ref_flute.json"):
                p = REF / slug / name
                if p.exists():
                    show(json.loads(p.read_text()), a.bars)


if __name__ == "__main__":
    main()
