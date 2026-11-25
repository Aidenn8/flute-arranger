# Flute Arranger

Turn a song you like into flute sheet music you can play: the flute part alone, or the flute on top with a simple piano part underneath that supports it.

You give it a recording (mp3, wav, m4a, flac, ogg, opus, aiff) or a MIDI file. It listens for the tune, moves it into the flute's range, writes it out with a clean rhythm, and shows the score in a plain web page you can print. Everything runs on your own Mac; nothing is uploaded anywhere.

## Start it

```sh
./run.sh
```

Then open <http://localhost:5230>. Upload a song, pick what's in it, and wait a few minutes the first time (it has to listen through the whole song). After that, changing any option takes a few seconds.

### Or let it listen to your Mac

Under **Listen to this computer** on the start page: press **Start listening**, play the song on your Mac (Spotify, YouTube, anything; headphones are fine), then press **Stop and make the sheet music**. Quiet before and after the song is cut off, and from there it's the same as an upload. It stops by itself after 12 minutes.

The first time, macOS has to allow it (it counts listening to the computer's sound as screen recording):

1. Build the small recorder once: `scripts/build_sysaudio.sh` (uses the Swift compiler from Xcode).
2. Press **Ask macOS for permission** on the start page, then in **System Settings → Privacy & Security → Screen & System Audio Recording** turn on the app you started Flute Arranger from (usually Terminal).
3. Quit that app, open it again, and run `./run.sh`.

Command line, if you'd rather:

```sh
.venv/bin/python -m sheetswap.cli song.mp3 --mode duet          # flute + piano
.venv/bin/python -m sheetswap.cli song.mp3 --meter 3/4 --simple  # 3/4, easier rhythms
.venv/bin/python -m sheetswap.cli song.mp3 --meter 6/8           # also 2/4, 4/4, 3/8, 9/8, 12/8
```

## The options on a song's page

| Option | What it does |
|---|---|
| **Make** | flute only, or flute + piano (the piano stays under the flute and never covers it) |
| **Which part is the tune?** | let it guess, the singing, the piano's top line, or another instrument |
| **Octave** | move the flute part an octave lower or higher (always kept between C4 and C7) |
| **Beat** | twice as fast if the notes look too short (lots of 16ths in a calm song), half as fast if they look too long |
| **Time** | 4/4 or 3/4, if the bars feel too long or too short |
| **Bar lines** | move the bar lines 1–3 beats later if the strong beats don't land at the start of each bar |
| **Make it easier to play** | no notes faster than 8ths |
| **Move to an easier key** | at most 2 sharps or flats |
| **Expression** | *expressive* (the default) writes in how the recording is played: dynamics (p, mf, f) and swells, slurs, staccatos and accents, rit. and fermatas, and a tempo word like "Andante dolce"; the preview plays them. *with ornaments* adds a few trills, turns and grace notes (at most one every 4 bars, only on long notes); *plain* is just the notes |
| **Piano part** | simple chords, or follow the original piano more closely |

Each page has the score, a preview you can listen to (flute + piano), the original recording, a print / save-as-PDF link, and MusicXML (opens in MuseScore if you want to edit) and MIDI downloads.

## What works well, and what doesn't yet

Measured on test music whose real melody is known (`bench/results.md`):

| Kind of music | How much of the real tune ends up in the flute part |
|---|---|
| Solo piano | 90% |
| Piano with broken-chord accompaniment | 95% |
| Singing (with vibrato) over piano and drums | 96% |
| Piano plus a solo instrument (clarinet, violin) | 67% |

And for the flute + piano parts together (`bench/music_audit.md`, which also checks the real recordings against the published melodies):

| Test music | Flute notes clashing with the piano | Strong-beat flute notes inside the piano chord | Piano bass = real bass |
|---|---|---|---|
| Solo piano | 3.5% (was 19.8%) | 95% | 86% |
| Piano plus a solo instrument | 6.1% (was 18.9%) | 92% | 85% |
| Singing over piano and drums | 4.1% (was 15.4%) | 92% | 86% |

### Against real sheet music

`bench/ref_score.py` checks the flute part against the published scores of 10 real recordings (5 piano, 5 violin; sources in `bench/REFERENCES.md`), note by note, and also *listens*: it plays our flute part and the score's melody back as flute and compares their timing.

| Piece (real recording) | Time | Note starts exactly where the score has them | Listening: our flute vs the score |
|---|---|---|---|
| Satie, Gymnopédie No. 1 (piano) | 3/4 ✓ | 97% | on time (median 0 ms) |
| Beethoven, Für Elise (piano) | 3/8 ✓ | 82% | on time |
| Bach, Allemanda BWV 1004 (violin) | 4/4 ✓ | 73% (92% within a 16th) | on time |
| Bach, Gavotte BWV 1006 (violin) | 4/4 for 2/2 | 74% | on time |
| Bach, Menuet BWV 1006 (violin) | 3/4 ✓ | 72% | on time |
| Chopin, Nocturne Op. 9 No. 2 (piano) | 12/8 ✓ | 57% | about a tenth of a beat off |
| Schumann, Träumerei (piano) | 4/4 ✓ | 34% | drifts |
| Debussy, Clair de lune (piano) | 3/4 at double speed (real: 9/8) | 36% | drifts |
| Bach, Air (strings) | 4/4 at double speed | 17% (54% within a 16th) | try **Beat: half as fast** |
| Schubert, Ave Maria (violin + piano) | 3/4 (real: 4/4) | 21% | drifts |

What this means for a recording you bring:
- **Steady pieces with a clear tune** come out close to the printed music, including 3/8, 6/8 and 12/8.
- **Very slow, very free playing** (Träumerei, Clair de lune) and pieces where the beat tracker hears the 8th or the triplet as the beat still come out at the wrong note level. Use **Beat** (half / twice as fast) and **Time** to fix the level and meter; **Bar lines** if beat 1 sits in the wrong place.
- **Songs with singing**: the notes of the tune come out right, one note per sung syllable; singers stretch rhythm a lot, so **Make it easier to play** gives the most readable result.
- It finds the tune best when one instrument or voice clearly carries it; in broken-chord piano writing (Für Elise) some chord notes of the tune are still missed (58% of Für Elise's notes found).

Known limits: from a recording, 16th-note triplets and 32nd runs are simplified (sheet music keeps them exactly); violin double stops lose their lower note; a male voice that sits between two pitches can land a semitone off.

## Setting it up from scratch

Needs macOS with Python 3.11 available through [uv](https://docs.astral.sh/uv/).

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python --no-deps tinysoundfont
uv venv --python 3.11 tools/homr && uv pip install --python tools/homr/bin/python "homr[cpu]==0.7.1" && tools/homr/bin/homr --init  # sheet-music reader 1 (own venv, ~120 MB of models)
.venv/bin/python -m sheetswap.omr --fetch-audiveris   # sheet-music reader 2: Audiveris 5.11 + its own Java, into tools/audiveris
scripts/fetch_models.sh       # piano sound for previews (MuseScore_General, MIT)
scripts/build_sysaudio.sh     # optional: the recorder for "Listen to this computer"
scripts/install_pm2s.sh       # optional: PM2S, beats read from a piano recording's notes (off by default)
```

The piano transcription model downloads itself into `models/` on first use, and the stem separation model into `~/.cache/huggingface`. No Homebrew packages are needed: audio is decoded with the ffmpeg binary that `imageio-ffmpeg` ships.

On macOS 14 the models run on the CPU (the Apple GPU path crashes there), so a 4-minute song takes 2–4 minutes to analyse.

## How it works

1. **Separate** the song into singing, drums, bass and the rest (Demucs), unless it's solo piano.
2. **Write down the notes**: a piano model (ByteDance's piano transcription) and a general model for any instrument (Spotify's Basic Pitch). Notes only the general model hears belong to some other instrument, which tells "solo piano" apart from "piano + violin".
3. **Find the beat and the bars** with Beat This! (a neural beat and downbeat tracker, ISMIR 2024), which follows a performer's tempo changes; 3 vs 4 beats a bar is settled by which bar length makes the rhythm repeat. Where the notes fall inside each beat tells 6/8-type music (beats split in three, written 9/8 or 12/8) from 2/4-3/4-4/4, and fast three-beat bars split in two become 3/8. (PM2S, a model that reads beats, bars and note values from a piano performance's notes, is installed for comparison but not used: on the 13 piano reference recordings it put 41% of the tune's notes exactly where the score has them, against 72% for Beat This! plus the rhythm reading below (better on 2 pieces), `bench/pm2s_results.md`. `SHEETSWAP_PIANO_BEATS=pm2s` switches it on for solo-piano analyses.)
4. **Pick the tune**: the singing if there is any; the notes a pianist plays louder; otherwise the highest sensible line through the notes.
5. **Fit it to the flute**: one octave move for the whole piece, then whole phrases moved where needed, never single notes.
6. **Write the rhythm**: each beat is read as the most likely printed figure (two 8ths, dotted 8th + 16th, a 16th run, ...) given where the notes were played, learning the piece's own common figures; triplets only where the piece really uses them; notes held through short gaps; breath marks in long phrases.
7. **Piano part**: one chord per beat at most, chosen across the whole piece at once (dynamic programming): chords that contain the tune's strong-beat notes, belong to the key, move the way real progressions do (ii–V–I, cadences at phrase ends), with 7th chords and the real bass note (inversions included). A tune with no accompaniment is harmonized. Voiced below the flute, never above it.
8. **Engrave** with Verovio, and play a preview with a soundfont. The writing follows the rules in `docs/notation_rules.md` (from Gould's *Behind Bars*, Stone and the usual theory texts): every beat visible, beams per meter, pickup bars, courtesy accidentals, a breath at least every 10 seconds. The song page's **Notation check** line says whether the score follows them, or names the bars that don't.

Code: `sheetswap/` (one file per step), web page in `sheetswap/web/`, tests in `tests/` (`.venv/bin/python -m pytest`; `-m slow` runs the model tests), measurements in `bench/`.

The design notes are in `docs/superpowers/specs/` and `docs/superpowers/plans/`.
