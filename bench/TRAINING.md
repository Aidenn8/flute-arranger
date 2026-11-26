# Trained models: data, licenses, how to reproduce

The app's steps are hand-written rules; some have a trained alternative, measured against
the rules on the reference recordings (`bench/ref_score.py`, `bench/REFERENCES.md`) before
it is used. Training data lives in `data/` (git-ignored) and models in `models/`
(git-ignored); nothing downloaded is ever executed.

## Tune picker (melody.learned_tune)

Which transcribed piano notes are the tune? The rules (`piano_melody` + `fill_tune`) decide
that with hand-set thresholds and a small hand-fitted logistic score; `learned_tune` asks a
trained model instead.

**Status: opt-in.** `SHEETSWAP_TUNE_PICKER=learned` switches it on for piano recordings
(the default stays the rules, `melody.PICKER_DEFAULT`). It finds far more of the tune
(92% of the reference notes against 77%) but also takes more accompaniment, and it clearly
hurts the Moonlight Sonata (see the results below).

```sh
# data (about 150 MB), into data/
mkdir -p data/pop909_zip data/pop909 data/asap_zip data/asap
curl -L -A "Mozilla/5.0" -o data/pop909_zip/pop909-master.zip https://codeload.github.com/music-x-lab/POP909-Dataset/zip/refs/heads/master
unzip -q data/pop909_zip/pop909-master.zip -d data/pop909
curl -L -A "Mozilla/5.0" -o data/asap_zip/asap-master.zip https://codeload.github.com/fosfrancesco/asap-dataset/zip/refs/heads/master
unzip -q data/asap_zip/asap-master.zip -d data/asap

heat run .venv/bin/python -I bench/train_tune_picker.py all      # ~1 h: render + transcribe, then train
.venv/bin/python bench/tune_picker_judge.py                      # rules vs learned on the reference recordings
SHEETSWAP_TUNE_PICKER=learned .venv/bin/python bench/ref_score.py --no-analyze
```

### Data

| dataset | what | used as | license |
|---|---|---|---|
| [POP909](https://github.com/music-x-lab/POP909-Dataset) (Wang, Chen et al., ISMIR 2020) | 909 Chinese pop songs arranged for piano by musicians: MIDI with a MELODY track (the sung tune), a BRIDGE track (fills) and a PIANO track (accompaniment, with pedal) | tune = MELODY; BRIDGE notes are heard but not scored | MIT |
| [ASAP](https://github.com/fosfrancesco/asap-dataset) (Foscarin et al., ISMIR 2020) | 1067 performances (Disklavier MIDI from the Yamaha e-Competition / MAESTRO: real velocities and pedal) of 222 classical pieces, aligned beat by beat to their MusicXML/MIDI scores | tune = the top line of the score's right-hand staff (bench/make_refs.py's rule: at each onset the highest new note, unless a note at least as high still sounds), carried to the performance through the beat alignment; performance notes with no score note are not scored | CC BY-NC-SA 4.0; the trained model is for this non-commercial project only |

Splits are by piece (POP909 song number; ASAP composer + title), hashed: about 70% train,
15% validation, 15% test. No piece of `bench/ref_score.py`, `bench/piano_tune.py` or
`bench/run_bench.py` is in either dataset (`tune_data.JUDGE_TITLES` is checked against the
ASAP titles: none match; POP909 is pop only).

POP909's arrangers played the tune far louder than the rest (velocity ~110 over ~50); real
pianists voice it much less, so every song gets a random voicing gap of -4 to +30 instead
(some songs not voiced at all).

### Making the input look like a recording

The app never sees the MIDI: it sees our piano transcription of a recording, with missed
notes, ghost notes (overtones heard as notes an octave up), timing jitter and pedal-length
note ends. So:

1. **Rendered and transcribed** (`train_tune_picker.py render`, `tune_data.render_and_transcribe`):
   40 excerpts of 90 s (train 10 POP909 + 12 ASAP, validation 5 + 6, test 3 + 4) are rendered
   with the piano soundfont (models/MuseScore_General.sf2): note ends held by the pedal as a
   piano rings (tinysoundfont has no sustain pedal), velocity spread over 30 dB (the soundfont
   alone spans ~12 dB), a random room (reverb 0.3-1.6 s, noise -60 to -40 dB). The app's own
   transcriber (`sheetswap.transcribe.piano`) writes them down and the labels are carried over
   (same key, onset within 60 ms; notes it invented = not tune). About 70 s of CPU per excerpt.
2. **Velocity as on a real piano** (`tune_data.heard_velocities`): on the soundfont the
   transcriber reports velocity squeezed into a narrow band (heard ≈ 0.3 × played + 67, and
   still only 0.6 × played with 40 dB of spread: it judges loudness partly by timbre, which a
   soundfont doesn't change), while on real piano recordings it spreads 40-95 like a
   performance. Matched notes get the played velocity plus noise (sd 6); ghosts are mapped
   back through the squeeze measured on the matched notes.
3. **Simulated** (`tune_data.ErrorModel`): what the transcriber did on the 22 training
   excerpts is replayed on 300 POP909 songs and 2 performances of each ASAP training piece
   (1467 windows of 120 s). Measured: misses 24% for notes under velocity 30, 2-4% otherwise;
   3.7 ghost notes per 100 played, mostly an octave over a louder note; note ends at the
   pedal-held end (median ratio 1.00); onset jitter 2 ms median (a soundfont is cleaner than
   a room).

Validation and test are always rendered + transcribed excerpts, never simulated ones.

### Model

Per transcribed note, 46 features (`melody.FEATURES`): pitch, length, velocity; its place
in its chord (notes above / below, louder, an octave or a twelfth over a louder note); notes
struck earlier still sounding above it and how long until a higher note comes; its height
against the local top line (as `top_voice` measures it) and the notes within 0.5 s and 1.5 s;
velocity against the notes and chord tops around it; note and onset density; intervals to
the chords before and after; the same key struck before / after; pedal down; piece-level
loudness spread and density; and the rules' own picks. A scikit-learn
`HistGradientBoostingClassifier` (400 trees, 1.16 million notes, about 1 minute on the CPU)
gives each note's chance of being the tune; `_likely_line` keeps the line with the largest
total log-odds minus costs between neighbours (one note per moment, joined freely after a
rest). The line's bias and costs are chosen on the validation excerpts (bias -0.5, no
interval cost, 0.5 for a leap over an octave, no cost for the same key again). Saved to
models/tune_picker/ with meta.json (features, line settings, scores, sklearn version);
missing, unreadable or made for other features -> the rules.

In the app the meter and bar lines are still found on the notes the pianist voiced
(`melody.VoicedLine`; finding them on the learned tune instead was a little worse: onset
exact 71.0% against 71.6%), and the learned tune is written on them (`fill_tune`). Music with
no voiced tune (`projected_line` finds none, e.g. a sequencer's chorale) keeps `top_voice`:
the model is unsure of the soprano in evenly played four-part chords (bench set A, flute
F1 0.45 against 0.90).

### Results

Training: 257 s for features + three fits + the line grid (render 45 min, simulation 4 min
before that). Details per excerpt and feature importance: `bench/tune_picker_results.md`.

Tune notes found among the transcribed notes (precision / recall / F1):

| | rules | learned |
|---|---|---|
| validation, 11 excerpts | 0.85 / 0.36 / 0.50 | 0.91 / 0.90 / **0.90** |
| test, 7 excerpts | 0.83 / 0.43 / 0.57 | 0.80 / 0.92 / **0.85** |
| trained on the 22 rendered excerpts only (validation) | | 0.92 / 0.74 / 0.82 |
| without the rules' picks as features (validation) | | 0.91 / 0.90 / 0.90 |

(The rules' recall is low here partly because these labels count every note of the right
hand's top line, figuration included; the reference melodies leave some of that out.)

The judge, `bench/tune_picker_judge.py` (bench/ref_score.py's own measures, in one process
for both pickers; the rules column equals `bench/ref_results.md`), means over the 13 piano
recordings, rules -> learned (all in `bench/tune_picker_judge.md`):

| notes found | extra | onset exact | same length | listening: median / 90% deviation (ms) |
|---|---|---|---|---|
| 76.8% -> 92.1% | 7.9% -> 14.6% | 72.1% -> 71.6% | 63.1% -> 64.0% | 102 / 574 -> 106 / 676 |

Better: Träumerei (found 49 -> 98%), Chopin Prelude 20 (70 -> 100%), Kinderszenen 1 (82 ->
99%), K. 545 (81 -> 98%, same length 84 -> 89%), Clair de lune (53 -> 81%, 90% deviation
1.7 s -> 0.6 s), the B-minor waltz, the nocturne, Für Elise (63 -> 78%). Worse: Moonlight
(extra 25 -> 68%, same length 54 -> 37%, 90% deviation 1.6 s -> 4.7 s: it takes the right
hand's triplets wherever the slow top voice rests or holds), Gymnopédie (extra 4 -> 12%: the
F#4 Satie holds over four bars is struck again by each bar's chord, and the learned line takes
every strike), small losses of onset exactness in Für Elise and the nocturne. The violin
pieces are untouched (their tune is not the piano's). `bench/run_bench.py` A-D: 0.90 / 0.73 /
0.95 / 0.98 with both pickers, no check failures (its synthetic pieces are evenly played,
so they keep the rules).

### What would make it the default

- **Labels that know accompaniment figures.** "Top line of the right hand" calls the
  triplets of Moonlight (and Chopin's Op. 10 No. 1 arpeggios) the tune whenever the melody
  rests; the MusicXML scores have voices, which would tell the melody's voice from the
  figuration's.
- **Evenly played four-part music** (chorales) in the training set, so it can replace
  `top_voice` there too.
- **Real recordings** for validation: the excerpts are a soundfont in a simulated room.

## Rhythm reader (rhythm.quantize_line_learned)

Where does each tune note go on the page? The rules (`rhythm.quantize_line`) read each beat
as the likeliest printed figure given where its notes were played. The learned reader asks a
small network trained on our own kind of mistakes instead: for every note of the tune line
*as the pipeline sees it* (transcribed notes, picked by `piano_melody` + `fill_tune`, timed
against a Beat This! grid that is a little off) it reads where the score writes the note in
the beat (or the beat before / after), how long a rest follows it, and whether it is a note of
the score at all. Code: `sheetswap/rhythm.py` (features, network, decoding),
`bench/train_rhythm.py` (data, training, evaluation, the judge for both readers).

**Status: opt-in.** `SHEETSWAP_RHYTHM=learned` switches it on for recordings whose tune comes
from the piano transcription (`rhythm.LEARNED_SOURCES`; violin, voice and MIDI files keep the
rules). Default stays the rules (`rhythm.RHYTHM_DEFAULT`): on the judge it is a little better
on average but clearly worse on two pieces, and the listening check gets worse (below).
Without `models/rhythm/` (or torch) it falls back to the rules.

```sh
# ASAP into data/ (the same folder the tune picker uses)
mkdir -p data/asap_zip data/asap
curl -L -A "Mozilla/5.0" -o data/asap_zip/asap-master.zip https://github.com/fosfrancesco/asap-dataset/archive/refs/heads/master.zip
unzip -q data/asap_zip/asap-master.zip -d data/asap

.venv/bin/python -I bench/train_rhythm.py prepare            # 1 min: score <-> performance notes, splits
heat run .venv/bin/python -I bench/train_rhythm.py render    # 80 min: 64 excerpts synthesized, transcribed, beat-tracked
.venv/bin/python -I bench/train_rhythm.py stats              # the measured error statistics
heat run .venv/bin/python -I bench/train_rhythm.py build     # 4 min: training lines
heat run .venv/bin/python -I bench/train_rhythm.py train      # 8 min (8 epochs, dropout 0.3, no absolute pitch)
.venv/bin/python -I bench/train_rhythm.py evaluate           # rules vs learned on held-out pieces
heat run .venv/bin/python -I bench/train_rhythm.py judge     # bench/ref_score.py with each reader
```

### Data

| what | where | license |
|---|---|---|
| ASAP: 1,067 performance MIDIs (Disklavier, from MAESTRO) of 222 classical piano scores, with score MIDI and beat / downbeat / time-signature annotations for both ([fosfrancesco/asap-dataset](https://github.com/fosfrancesco/asap-dataset), master, 2026-11-26) | `data/asap/asap-dataset-master/` | CC BY-NC-SA 4.0: non-commercial; the weights in `models/rhythm/` are derived from it and carry the same terms |
| MuseScore_General soundfont (already in the app) for the rendered excerpts | `models/MuseScore_General.sf2` | MIT |

**Splits, by piece.** A piece is (composer, title) with ASAP's repeat variants (`_repeat`,
`_no_repeat`, ...) folded together; a hash of it puts 80% of pieces in train, 10% in
validation, 10% in test: 845 / 76 / 115 performances of 177 / 18 / 25 pieces. None of the
judge's pieces is in ASAP except Beethoven's Op. 27 No. 2 (the judge has its 1st movement,
ASAP the 3rd), which is forced into test. The 31 performances ASAP marks as not aligned to
their score are left out.

**Labels.** ASAP has no note-level alignment, so each performance note is matched to the
score note of the same pitch nearest to where the annotated beats put it (one to one; 95% of
performance notes matched, median offset 0, 90% within 0.07 beat). A note's label is its
score position in beats, at the level the app writes beats: half-note beats (2/2, 3/2, 4/2)
split in two, compound meters (6/8, 9/8, 12/8, 6/4, 12/16) on dotted beats, 3/8 and 5/8 on
eighth beats. Tune-line notes with no score note (transcription extras), score grace notes
and a second note at an already written place are labelled "not a score note"; 32nds and
16th triplets (no place class on a recording) are left unlabelled.

### Making the input look like a recording

Measuring our own mistakes: 64 ASAP performances (one per piece; 40 train, 12 validation,
12 test pieces) were synthesized for 90 s each (`sheetswap.synth`), transcribed with the
app's piano model (`transcribe.piano`) and beat-tracked with Beat This!
(`neural_beats.track_beats_neural`), exactly as an upload. Compared with the performance
MIDI and its annotated beats (`stats`, the 40 training excerpts, 32,813 played notes):

| | measured |
|---|---|
| notes found | 99.3%; by played velocity under 30: 80%, 30-50: 99.3%, over 50: 99.6-99.9% |
| extra notes | 3.6 per 100 played: overtones (octave, 12th) 2.8, re-strikes inside a held note 0.5, others 0.3 |
| onset error | median -5 ms (5-95%: -11 to 0 ms) |
| note ends | follow the key, not the pedal: median -8 ms (pedal up), -5 ms (pedal down, 95%: +150 ms); the pedal comes up a median 0.38 s later |
| velocity | transcribed = 0.21 x played + 75 (sd 6): the soundfont squeezes dynamics, so not copied (below) |
| Beat This! level | the annotated beat level in 20 of 40 excerpts; the others twice as fast (8), half as fast (4) or 3:4-like ratios |
| Beat This! timing (right level) | beat error sd median 0.044 beat (5-95%: 0.02-0.11), lag-1 correlation median 0.16, beats a quarter beat or more off: median 4.6% (up to half in one excerpt), slips median 1.9 per 100 beats |

The training set applies these to every performance's MIDI (`sim_transcription`,
`sim_beats`, `sim_examples`), with a per-performance error scale (0.7-3x) since a real
recording is harder than a synthesized one:
- notes missed by velocity bin (rate x scale); onsets moved by measured errors (spread x
  1-(1 + scale), plus 5 ms x scale of noise); ends moved by the measured end errors for the
  pedal's state; velocities kept from the performance (x 0.8-1.2, +- 6, noise 8) rather than
  the soundfont's squeezed ones; extra notes of each measured kind at the measured rate x
  scale, each copied from a measured example (offset, length, velocity, interval);
- the grid: annotated beats moved by an AR(1) error with one measured excerpt's spread,
  correlation and bias; for quarter beats, 15% of lines on a grid twice as fast, 10% half
  as fast and 10% half a beat off (the labels follow the grid's beats, so a line played off
  the beat throughout is read off the beat, as the grid has it); bar lines moved in 20%,
  the wrong number of beats a bar in 10%;
- the tune line picked by `piano_melody` + `fill_tune` (2 of 3 versions) or `top_voice`
  (1 of 3), then cut and merged exactly as `quantize_line` does.

Three versions per training performance: 3,517 lines, 1.16 M notes (927 k labelled); one per
validation / test performance. The rendered excerpts' own lines are used as they are (Beat
This!'s grid, its beats tied to the annotated ones; labels only where that is clear): 27 /
10 / 10 lines with 4,006 / 1,366 / 1,183 labelled notes; the 27 training lines count 4x.

### Model

Per note, 63 inputs (`rhythm.learned_features`): where in the beat it was heard (and sines of
it at 1-4x the beat), gaps to its neighbours and its length (beats and seconds), the local
beat length against the piece's, velocity (and against its neighbours), steps to its
neighbours, the beat's kind, its place in the bar, a grace-note look, how many notes share
its beat, whether the piece plays triplets, and where the rules read it (one-hot), so the
network learns where to overrule them. Absolute pitch is left out (input 21 zeroed): with it,
the network learned pieces by heart (ASAP has ~5 performances of each).

A linear layer (96) and a 2-layer bidirectional GRU (96 each way, dropout 0.3) over the whole
line, then three outputs: the place (24 classes: 8 fractions of a beat in the beat before /
heard / after, masked to what the beat's kind allows: 16ths and 8th triplets in a quarter,
16ths in a dotted beat, 16ths in an eighth beat; 8ths when "easier to play"), the rest after
it (12 classes), and "not a score note". 292 k parameters, 1.2 MB. Decoding
(`decode_places`) picks the likeliest places with onsets strictly in order, a note left out
at weight 3 x log p(not a score note) (3 beat 1 on validation). A note ends at the next one
less the rest read after it; after a long rest it ends as in the rules.

Training: AdamW, one-cycle LR 2e-3, windows of 256 notes, batch 24, CPU (4 threads),
8 epochs in 8.3 min; kept epoch 7 (best mean of simulated and real validation accuracy).
A first run with absolute pitch, dropout 0.1 and no off-beat grids (stopped after 6 epochs,
4.9 min) overfit and read Chopin's Op. 25 No. 4 (right hand off the beat throughout) half a
beat late: 3.5% of its notes right against the rules' 100%; the second run reads it 100%.

### Results

Held-out pieces, tune notes with a label (`evaluate`; "same length" counts notes with the
right place and the right length):

| | onset exact, rules -> learned | within a 16th | same length |
|---|---|---|---|
| validation, rendered excerpts (10 pieces, 1,366 notes) | 0.880 -> **0.922** | 0.995 -> 0.994 | 0.594 -> 0.680 |
| test, rendered excerpts (10 pieces, 1,183 notes) | 0.828 -> **0.910** | 0.990 -> 0.990 | 0.573 -> 0.723 |
| validation, simulated (18 pieces, 40 k notes) | 0.575 -> **0.802** | 0.790 -> 0.937 | 0.276 -> 0.503 |
| test, simulated (25 pieces, 46 k notes) | 0.602 -> **0.864** | 0.828 -> 0.941 | 0.295 -> 0.538 |

Better on 5 / 6 / 16 / 24 pieces of those sets, worse on 1 / 2 / 0 / 0. Adding a bonus to the
rules' places before decoding (so the network only overrules them when sure) only lowered
these numbers (validation: 0.922 / 0.802 with none, 0.903 / 0.712 with +3 log units), so
there is none.

The judge (`bench/ref_score.py --no-analyze`, real recordings against the printed music;
`judge` writes `data/rhythm/judge/table.md`), rules / learned, learned on piano tunes only:

| piece | onset exact | ±16th | same length | ties/bar | listen dev (ms) |
|---|---|---|---|---|---|
| brahms_waltz_15 | 30.4 / **40.0** | 57.6 / 67.4 | 8.0 / 18.5 | 0.43 / 0.29 | 155 / 156 |
| chopin_prelude_4 | 85.3 / **89.7** | 92.6 / 92.6 | 69.1 / 76.5 | 0.19 / 0.12 | 0 / 0 |
| traumerei | 41.8 / **44.3** | 44.3 / 46.8 | 58.2 / 59.5 | 0.43 / 0.37 | 352 / 352 |
| fur_elise | 84.2 / 85.3 | 87.4 / 88.2 | 68.5 / 70.7 | 0.24 / 0.23 | 0 / 0 |
| chopin_waltz_69_2 | 94.8 / 95.9 | 96.0 / 96.0 | 88.2 / 90.7 | 0.23 / 0.16 | 0 / 0 |
| clair_de_lune | 40.9 / 41.8 | 60.4 / 59.5 | 29.6 / 29.7 | 0.55 / 0.51 | 254 / **328** |
| gymnopedie_1 | 96.6 / 96.6 | 96.6 / 96.6 | 87.9 / 87.9 | 0.26 / 0.24 | 0 / 0 |
| chopin_prelude_7 | 95.7 / 95.7 | 95.7 / 95.7 | 91.5 / 91.5 | 0.06 / 0.06 | 0 / 0 |
| chopin_prelude_20 | 87.8 / 87.8 | 90.2 / 90.2 | 78.0 / 80.5 | 0.08 / 0.00 | 0 / 0 |
| nocturne_op9_2 | 54.8 / 54.5 | 64.7 / 64.1 | 64.7 / 66.3 | 1.09 / 0.86 | 104 / **131** |
| mozart_k545_1 | 92.1 / 90.6 | 95.5 / 94.9 | 83.8 / 82.3 | 0.35 / 0.37 | 0 / 0 |
| kinderszenen_1 | 66.7 / **64.2** | 77.8 / 74.1 | 38.3 / 39.5 | 0.56 / 0.32 | 106 / 106 |
| moonlight_1 | 65.7 / **59.8** | 67.6 / 62.7 | 53.9 / 52.0 | 0.59 / 0.61 | 362 / **396** |
| 5 violin pieces | rules (unchanged) | | | | |
| **mean, 18 pieces** | 66.6 / 67.1 | 76.8 / 76.9 | 59.6 / 61.0 | 0.50 / 0.40 | 117 / 124 |

On the violin pieces it was worse (Bach's Air 21.3 -> 11.8% onset exact, the Allemanda 73.3
-> 71.6%; their tune comes from Basic Pitch, which it never saw), hence piano tunes only.
`bench/run_bench.py` A-D with it on: 0.90 / 0.73 / 0.95 / 0.98, no check failures (as with
the rules).

### What would make it the default

- **Real recordings in training.** The rendered excerpts are one clean soundfont: there the
  rules are already 83-88% exact and the network 91-92%; on the judge's recordings both are
  far lower and the gains shrink to a point. Recordings of ASAP's performances (MAESTRO's
  audio covers about half of them) transcribed as uploads would show the real errors.
- **Moonlight and Kinderszenen**: dotted 8th + 16th over triplets, and 2/4 written as 12/8
  by the app. Both lose 2-6 points; the grid's beat level is wrong in Kinderszenen (x1.5),
  and lines on a grid at a 3:4 level were not simulated.
- **The listening check** gets worse on the three freest pieces (Clair de lune, the
  nocturne, Moonlight: +27 to +74 ms median), where the network moves notes to other beats
  more freely than the rules.
