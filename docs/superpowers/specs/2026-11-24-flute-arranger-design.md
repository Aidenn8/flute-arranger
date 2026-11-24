# Flute Arranger: design

Date: 2026-11-24. Status: approved by the user ("work on this idea"); remaining calls made by the builder and marked as such.

## Why

The user plays flute. They want to take songs they like, which are usually not written for flute (piano pieces, pop songs with singing, band music), and get sheet music they can actually play. This is a personal practice tool that runs locally on their Mac. It is not a product, and it won't be published.

## What it does

Input: an audio file (mp3, wav, m4a, flac) or a MIDI file of any song.

Output, the user's choice of:

1. **Flute solo**: one melody line, in flute range, readable rhythm.
2. **Flute + piano**: the same flute part on top, with a piano part underneath (grand staff) that *supports* the flute. The piano holds the harmony and bass and stays out of the flute's way. It never doubles or sits above the melody.

Each result gives:
- the score shown on the page (flute part, or flute + piano), printable to PDF
- a downloadable MusicXML (to open or edit in MuseScore etc.) and MIDI
- an audio preview of the arrangement, so the user can hear it before playing

## Goals

- Works on general music: solo piano, piano + other instruments, songs with singing.
- The flute part is always playable: every note in flute range, no absurd rhythms (no 64th-note runs, no mess of tiny tied notes), sensible key signature and note spelling.
- The melody picked is the one a listener would hum.
- Changing an option (octave, difficulty, accompaniment style) re-renders in seconds. The slow audio analysis runs once per file and is cached.
- Old-internet look: plain HTML, default serif font, blue underlined links, gray form buttons, `<hr>`, tables. No frameworks, no animations.

## Non-goals

- Downloading from YouTube or streaming sites (the user supplies the file).
- Live microphone or real-time transcription.
- A note editor in the browser (MusicXML download covers editing in a real notation app).
- Instruments other than flute. The range and register rules live in one config, so adding others later is cheap, but it isn't a goal now.
- Hosting it online.

## Anti-goals (would count as failure even if it "works")

- Sheet music that looks machine-made in a way that stops you playing it: wrong meter, notes off the beat, constant ties across beats, clefs or ledger lines everywhere.
- A piano part that buries or fights the flute.
- A busy modern UI.
- Waiting minutes every time an option is changed.

## Approaches considered

| Approach | How | Trade-off |
|---|---|---|
| **A. Local Python pipeline of specialist models + rule-based arranging (picked)** | Separate the stems (Demucs) → transcribe (piano model / Basic Pitch / pitch tracker on vocals) → pick the melody → fit to flute → quantize to a beat grid → build the score (music21) → engrave (Verovio). | Each step can be inspected and tested, runs offline, and the arranging rules can be tuned. Most of the work is the arranging and rhythm code. |
| B. One end-to-end model (e.g. MT3, Pop2Piano) | A single model maps audio straight to notes or a piano cover. | Less control over the melody choice and flute fit. MT3 is heavy (JAX), and Pop2Piano only outputs piano. It still needs everything after transcription. |
| C. Wrap a commercial service (Klangio etc.) | Send the audio to an API. | Costs money, sends the user's music out, and gives no control over the arranging, which is the part that matters. |

The user handed over the how ("use as many agents/skills as needed"), so A is the builder's pick.

## Pipeline (approach A)

1. **Load**: decode to mono 44.1 kHz with ffmpeg; optional start/end trim. MIDI input skips steps 2–3.
2. **Separate** (only when the song isn't solo piano): Demucs `htdemucs` → vocals, bass, drums, other.
3. **Transcribe**:
   - piano-heavy audio / "other" stem → ByteDance piano transcription (high-accuracy piano notes with velocities)
   - general instruments → Basic Pitch (Spotify, works on any instrument)
   - vocals stem → monophonic pitch tracking → notes
   - Result: note lists per source (pitch, onset, offset, velocity), cached on disk.
4. **Beats and meter**: beat tracker → beat times and downbeats → meter (4/4 or 3/4, possibly 6/8). Note times are mapped from seconds onto beats, so tempo drift in real recordings doesn't wreck the notation.
5. **Pick the melody** (the core step):
   - singing present → the vocal line is the melody
   - otherwise → a "top voice" path through the transcribed notes, chosen by dynamic programming. It favors high, loud, sustained notes and small jumps, and it avoids jumping down into chords when the tune rests.
   - user override: melody source = auto / singing / piano top line / other instruments.
6. **Fit to flute**:
   - pick one octave shift for the whole piece that puts the most notes in the comfortable range (D4–A6), and never outside C4–C7
   - fold any leftovers phrase by phrase rather than note by note, so leaps aren't broken
   - optional "easier key": transpose to the nearest key with ≤2 sharps/flats
7. **Clean the rhythm**: quantize onsets and durations to the beat grid (16ths, or triplets when they fit better), merge stray tiny notes, remove ornaments in "simpler" mode (shortest note an 8th, repeated notes merged), add breath marks at long rests or every few bars in long unbroken phrases.
8. **Piano part** (flute + piano mode), two styles:
   - *cleaned original*: the transcription minus the melody notes, with anything above the flute note removed, hands split, quantized
   - *simple chords*: detect a chord per beat or half bar and voice it as left-hand bass + right-hand triad kept below the flute
9. **Build the score**: music21 Score → key signature (detected), meter, tempo, parts → MusicXML + MIDI.
10. **Engrave and preview**: Verovio renders MusicXML to SVG on the server. Audio preview comes from FluidSynth with a free General MIDI soundfont (flute + piano), or a simple built-in synth if that's unavailable.

## App

- Local Flask server, `http://localhost:5230`.
- Page 1: upload form (file, what's in it: auto / solo piano / song with singing / band, and output: flute solo / flute + piano).
- Page 2: progress page with a meta refresh every 2 s ("Step 3 of 7: finding the melody…").
- Page 3: result. Options form (melody source, octave, difficulty, easier key, piano style) re-arranges from the cached notes. Score SVG, audio preview, links: Print, MusicXML, MIDI.
- Library page listing past arrangements (plain table).
- Heavy model runs are wrapped with the user's `heat` guard where practical.

## Testing

- **Ground-truth set**: arrangements where the melody is known (hand-written and public-domain MIDI with a separate melody track), rendered to audio with FluidSynth piano. Run the full pipeline and score melody F1 (mir_eval) against the real melody.
- **Real recordings**: public-domain piano recordings (e.g. Wikimedia Commons or Musopen) plus at least one song with singing. Check by listening and reading the score.
- **Hard checks on every output**: all flute notes in C4–C7; every bar full; no note shorter than a 16th in normal mode or an 8th in simpler mode; the piano never sits above the flute in flute + piano mode.
- Unit tests for the melody, flute-fit, quantize and piano-reduction functions.

## Builder's calls (the user can overrule any)

- Flask + server-rendered HTML instead of a JS app, to fit the old-web look.
- Python 3.11 virtualenv via `uv`, because the music ML packages lag behind Python 3.14.
- Verovio for engraving: MuseScore isn't installed, and Verovio installs with pip.
- Install ffmpeg and FluidSynth through Homebrew, and download a free soundfont into the project's `models/` folder.
- Flute ranges: written C4–C7 hard limit, D4–A6 comfortable.
- PDF through the browser's print dialog on a print-friendly score page.
