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
.venv/bin/python -m sheetswap.cli song.mp3 --meter 3 --simple    # 3/4, easier rhythms
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

On real recordings:
- **Piano pieces played with a clear melody** (Satie's Gymnopédie No. 1, Chopin's Nocturne Op. 9 No. 2) come out right: it follows the notes the pianist plays louder, and stays quiet through a chords-only intro.
- **Songs with singing** (tested on a soprano and a male singer with a band): the notes of the tune come out right, one note per sung syllable. Singers stretch the rhythm a lot, so turn on **Make it easier to play**: the anthem then reads in plain quarters, halves and 8ths.
- **Very free tempo** (e.g. Für Elise played with lots of rubato) gets the right notes but a busier rhythm than the printed music.
- **Beat and bars**: the meter is usually right; with drums the beat is sometimes twice the real tempo, and where the bar starts is the weakest part. Use *Beat*, *Time* and *Bar lines* when the score reads oddly.

It works best on recordings where the tune is clear: one main instrument or voice carrying it, not too much reverb.

Known limits:
- Only 3/4 and 4/4: music in 3/8, 6/8 or 12/8 (Für Elise, Chopin's Nocturne) is written in 4/4, so it reads busier than the printed music.
- Bar lines are placed where the tune's long notes fall (right in about 60% of the test pieces, up from about 25%); very slow, freely played piano (Gymnopédie) can drift a beat. Use **Bar lines** when the strong beats sit in the wrong place.
- Notes faster than about 1/7 s in a sung line are merged into their neighbours; a male voice that sits between two pitches can land a semitone off.

## Setting it up from scratch

Needs macOS with Python 3.11 available through [uv](https://docs.astral.sh/uv/).

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python --no-deps tinysoundfont
scripts/fetch_models.sh       # piano sound for previews (MuseScore_General, MIT)
scripts/build_sysaudio.sh     # optional: the recorder for "Listen to this computer"
```

The piano transcription model downloads itself into `models/` on first use, and the stem separation model into `~/.cache/huggingface`. No Homebrew packages are needed: audio is decoded with the ffmpeg binary that `imageio-ffmpeg` ships.

On macOS 14 the models run on the CPU (the Apple GPU path crashes there), so a 4-minute song takes 2–4 minutes to analyse.

## How it works

1. **Separate** the song into singing, drums, bass and the rest (Demucs), unless it's solo piano.
2. **Write down the notes**: a piano model (ByteDance's piano transcription) and a general model for any instrument (Spotify's Basic Pitch). Notes only the general model hears belong to some other instrument, which tells "solo piano" apart from "piano + violin".
3. **Find the beat and the meter** from the drums, or from the transcribed notes.
4. **Pick the tune**: the singing if there is any; the notes a pianist plays louder; otherwise the highest sensible line through the notes.
5. **Fit it to the flute**: one octave move for the whole piece, then whole phrases moved where needed, never single notes.
6. **Clean the rhythm**: snap to plain 8ths where the playing allows, hold notes through short gaps, add breath marks in long phrases.
7. **Piano part**: one chord per beat at most, chosen across the whole piece at once (dynamic programming): chords that contain the tune's strong-beat notes, belong to the key, move the way real progressions do (ii–V–I, cadences at phrase ends), with 7th chords and the real bass note (inversions included). A tune with no accompaniment is harmonized. Voiced below the flute, never above it.
8. **Engrave** with Verovio, and play a preview with a soundfont.

Code: `sheetswap/` (one file per step), web page in `sheetswap/web/`, tests in `tests/` (`.venv/bin/python -m pytest`; `-m slow` runs the model tests), measurements in `bench/`.

The design notes are in `docs/superpowers/specs/` and `docs/superpowers/plans/`.
