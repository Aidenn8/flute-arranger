# Music audit: is the sheet music accurate, and does it sound good?

2026-11-24. The question: *"just make sure the sheet music is accurate and sounds good."* This is a measurement, not a fix: nothing in `sheetswap/` was changed.

Rerun (about 6 minutes; renders into `out/audit/`, which git ignores):

```sh
heat run .venv/bin/python bench/music_audit.py --look   # everything, plus page-1 PNGs in out/audit/look/
heat run .venv/bin/python bench/music_audit.py --real   # only the 5 recordings (keeps the test-music numbers)
.venv/bin/python bench/music_audit.py --report          # rebuild the tables below from out/audit/music_audit.json
```

The tables between the AUTO markers are rewritten on every run; the text around them is kept.

## What was measured

- **Inputs.**
  - Five real recordings: Satie Gymnopédie 1, Für Elise, Chopin Op. 9/2, the Star-Spangled Banner (soprano and band), and America the Beautiful (male voice and band).
  - 39 test pieces (Bach chorales: piano, piano with clarinet or violin, broken chords, and sung with drums). For these the true melody (`truth.json`) and the true chords (`full.mid`) are known.
- **Options.** Each piece was run as a user would set it up: flute + piano with simple chords, `meter=3` for the 3/4 pieces, and "Make it easier" both on and off. For the anthem, half tempo was also tried. The flute line is the same in solo and duet mode.
- **The real tunes.** I encoded the published melodies by hand in `bench/music_audit.py`:
  - Satie bars 5–19.
  - Für Elise bars 0–8, right hand including the broken chords.
  - Chopin bars 0–4 (rhythm for bars 0–2 only).
  - Anthem bars 0–16.
  - America bars 0–16.
  
  I checked the Satie and America timings against the transcription: the tune enters on beat 2 of bar 5, and America is sung in A♭.
- **Accuracy.** The whole tune is aligned against the flute part on pitch classes (any octave, any key for the songs). From the alignment:
  - how many tune notes are found, in order;
  - "intervals exact": neighbouring found notes keep the real interval, so a note in the wrong octave counts as wrong;
  - "same rhythm": distances between notes match after one overall tempo factor;
  - where the real bar lines fall in the printed part.
- **Harmony.**
  - A *clash* is a flute note a semitone, major 7th or minor 9th from a sounding piano note, or a tritone above the bass. It is weighted by length and by beat (downbeat ×2, other beats ×1.5).
  - The same is measured against the original accompaniment: the transcription for the recordings, Bach's real lower voices for test music.
  - Also measured: chords in the key, bass leaps, the chord under each phrase ending, and whether the piano's chord notes and bass are the ones sounding in the original.
- **Reading and playing.** Read from the engraved MusicXML: ties, tied 16th stubs, dots, notes starting off the beat and crossing the next one, accidentals, and ledger lines. Also the range, leaps over an octave, and the longest stretch without a rest or breath mark at the printed tempo.
- **Sound.**
  - The preview mp3 against `audio.wav`, compared on chroma (which pitches sound), aligned two ways: free alignment (DTW) and the score's own beat grid.
  - Each comparison has a "null" figure: the same comparison with the original reversed or shifted, i.e. what unrelated music would score.
  - Whether the flute's pitch is the loudest pitch in the original (or the vocal stem) at that moment.
  - Flute versus piano loudness, re-synthesized separately with the same soundfont, and clipping.
- **Look.** I read page 1 of six scores as a player would (PNGs in `out/audit/look/`).

## Verdict

When the tune is clear, **the pitches are mostly right**:
- 96–100% of the anthem's and Satie's tune notes appear in order;
- 89–99% of the test melodies, with 99.7–100% of intervals exact;
- the preview's flute plays the singer's pitch 95% of the time on the anthem.

**The flute part almost always fits the instrument**:
- C4–C7, with 96–100% of the time in the comfortable D4–A6;
- no rests chopping up phrases;
- no clipping.

What hurts "accurate and sounds good" is everything around the pitches:
- **where the bar lines and beats fall** (wrong in most pieces, including clean test music);
- **human timing written as syncopation**;
- **a piano part whose chords clash with the tune 3–10 times as often as the real harmony does**;
- **a melody picker that drops the softer half of an evenly played tune**;
- **a preview where the piano is 11–19 dB under the flute**, so you hardly hear it.

## Top problems, ranked by how much they hurt

### 1. Bar lines and the beat level are wrong in most pieces

**Evidence**
- **Test music, clean synthetic timing.** In the 27 pieces where meter and tempo were detected right, the printed bar lines fall on the real downbeats in only 7.
  - In 18 they are exactly one beat early: the one-beat pickup is printed as beat 1.
  - Examples: A_bwv66_6 has 94% of its notes one beat late in the bar; A_bwv2_6 has 100%.
  - The sung set (D) is printed at double tempo in 5 of 12 pieces, because the hi-hat's 8ths become the beat.
  - The meter is wrong in 4 of 39.
- **Satie.** The bar lines are right for only 45% of the tune: phrase 1 is a beat off, phrase 2 is right. The beat tracker loses a beat during the four-bar held F#4.
  - So the piano holds a D chord for six bars (5–10) where Satie alternates G and D every bar.
  - The tune enters on beat 3 instead of beat 2.
- **Anthem.** `meter=3` prints it at double tempo: each 3/4 bar is half a real bar, and 66% of the notes start off the beat and cross it. Half tempo gives the right note lengths, but no tune note sits where it belongs in the bar (offsets of +1.25 and +1.5 beats).
- **America.** The beat locked onto the band's triplet 8ths, three per real beat. No option can undo a factor of 3; rhythm agreement is 10–24%.
- **Unsupported meters.** Chopin (12/8) and Für Elise (3/8) can't be written in their own meters.

**Why it matters.** Metre is what a player reads first. With the bar line a beat early:
- every cadence and long note lands on a weak beat;
- notes are tied across beats (1.5–2.1 ties per bar on the songs);
- the piano's half-bar chords straddle the real chord changes.

That last point is half of problem 3: fixing the bar lines alone cuts the test-music clashes from 19.8% to 15.7% (set A) and from 18.9% to 13.0% (set B).

**Fix**
1. **Choose the bar phase from musical evidence, not onset strength alone.** `beats._meter`/`_finish` picks the phase whose onsets are loudest, but a chorale has a chord on every beat. Score each phase instead by:
   - where the chords change (chroma novelty at each beat);
   - bass onsets;
   - long melody notes and phrase-final notes;
   - a pickup allowed before the first downbeat.
   
   Simplest place: a step in `pipeline.arrange` before `quantize_line` that sets the default bar shift. A crude version (long notes plus bass onsets) already places the bar lines right in 13 of 27 pieces against 7 today. It mistakes beat 3 for beat 1 in 4/4, which harmonic change would settle.
2. **Recheck the phase every phrase** (about every 8 bars), so one lost beat doesn't shift the rest of the piece (Satie).
3. **Choose the beat level from the tune, not the drums.** About one sung syllable or melody note per beat is the usual level. Allow a factor of 1/3 as well as 1/2.
4. **Add 6/8, 9/8 and 12/8**: in `score._piece`, use dotted-quarter beats in the rhythm rules; in `ArrangeOptions.meter`, add the new choices.

### 2. Human timing is written as syncopation and ties

**Evidence**
- On the real recordings, 35–46% of flute notes start on a 16th between beats (Satie 9%). In 30–64% of cases the note is the only one in its beat and still isn't on the beat. On machine-timed test music this is 1–5%.
- In Satie, 10 of the 11 long notes off the 16th grid start exactly a 16th early, i.e. the pianist anticipating the beat.
- Full mode:
  - Für Elise: 0.8 tied 16th stubs per bar, 33% of notes crossing a beat off the beat;
  - Chopin: 0.9 stubs per bar, 34% of notes crossing a beat off the beat;
  - America: 1.0 stubs per bar, 55% of notes crossing a beat off the beat;
  - test set B (melody on clarinet or violin): only 56% of notes in the same rhythm, 20% of notes shorter than an 8th.
- Pages, as a player sees them:
  - Satie bar 15: 8th rest, 16th rest, then a 16th F#5 tied to a quarter, where the music has a quarter rest and F#5 on beat 2;
  - Für Elise: the pickup is a 16th tied across the bar line;
  - Chopin bar 1: a dotted quarter tied to a 16th.
- "Make it easier to play" removes the stubs (0 per bar) and roughly halves the ties, but the off-beat crossings remain (anthem 49%).

**Why it matters.** Performers play around the beat on purpose. The engraver's job is to write the *intended* rhythm. A lone note a 16th early is an anticipation of the beat, not a notated syncopation.

**Fix.** In `rhythm._beat_steps`/`quantize_line`, a beat with one onset currently gets no 8th grid and falls back to 16ths.
- Snap a lone onset to the beat or the half-beat when it is within a 16th (3 ticks).
- More generally, choose each beat's rhythm by the lowest notation cost (ties + dots + off-beat starts + 16ths) among the spellings within the timing error, not by the smallest error.

### 3. The piano's chords clash with the tune

**Evidence**
- **Test music:**
  - the flute clashes with the piano 15–31% of the time, but with Bach's real lower voices only 3–7%;
  - only 46–67% of flute notes on strong beats are in the piano chord;
  - the piano's bass is the real bass only 30–56% of the time.
- **Real recordings, wrong chords:**
  - Für Elise gets **A major (C#)** in A minor (page 1, bar 3).
  - Satie's Gmaj7 (G2 + B3 D4 F#4) is read as **B minor**, because without a bass line the root comes from the three upper notes (bars 2, 11–13).
  - The anthem gets **F# major, E major and F minor** in C ("dawn's early light" should be D7–G).
  - Chopin and America have only 66% and 54% of their chords in the key.
  - Only 25–46% of phrase endings in the anthem, America and Chopin have I or V under them.

**Causes and fixes** (measured by overriding the functions in memory, no code changed; see the what-if table below):

1. **The harmonic rhythm is fixed** at one chord per half bar (per bar in 3/4), but Bach changes chord every beat and Satie every bar.
   - One chord per beat raises the test-music bass match from 39% to 70%, but makes Satie worse (strong-beat chord tones 69% → 56%).
   - Fix: choose the chords by dynamic programming over beats in `harmony.chord_spans`. Charge a cost for each change and favour changes on strong beats.
2. **When a chord's third is missing, major always wins** (`chord_spans` takes the first best of `TEMPLATES`, which lists major before minor).
   - Fix: prefer the chord that belongs to the detected key (×1.08 for diatonic triads).
   - Broken-chord set: clashes 30.7% → 19.9%. Anthem: chords in key 72% → 93%. Für Elise: 86% → 94%.
3. **Solo piano has no bass line**, so the root is guessed from the upper notes.
   - Fix: pass the lowest note of each chord in the piano transcription as `bass` in `pipeline._accompaniment_pool`.
   - Bass match: Für Elise 53% → 73%, Chopin 57% → 69%.
4. **There are no seventh chords.** With Satie's correct root G, a plain triad turns the flute's F#5 (the chord's major 7th) into a clash: 16.4% → 20.2%.
   - Fix: add maj7, m7 and dominant-7 templates in `harmony`, and voice the 7th in `piano._voice`.
5. **All four together on test music** (with corrected bar lines):
   - clashes 19.8% → 11.7% (A), 18.9% → 9.8% (B), 15.4% → 10.6% (D);
   - strong-beat flute notes in the piano chord 58% → 79%;
   - bass right 39% → 76%.

### 4. Songs: the piano is built from the singer's echo

**Evidence**
- On the anthem the band's "other" and "bass" stems hold mostly vocal bleed: their notes are the sung tune itself (G4 E4 C4 E4 G4 … E5 D5 C5). So:
  - only 40% of the piano's chord notes are present in the band transcription;
  - the piano is silent in 13% of flute bars (every other bar on page 1);
  - clashes are 24% against the piano versus 11% against the original.
- Removing the bleed leaves nothing usable: clashes go to 89%.

**Fix.** When the accompaniment transcription is thin, or mostly matches the vocal line in pitch class and time, harmonize the tune itself.
- Each half bar, choose among I, ii, IV, V(7), vi and V/V by dynamic programming, so that the tune's strong-beat notes are chord tones.
- End phrases with V–I, and keep the bass smooth.
- Put this in a new function in `harmony.py`, called from `piano._chords_style`.

Chords that simply follow the tune already cut the anthem's clashes from 24% to 12% and raise strong-beat chord tones from 50% to 86%.

### 5. The piano melody picker drops the softer half of the tune

**Evidence.** In Für Elise only 77% of the opening is found, and whole bars are empty (bars 4–5 and 8–9).
- The right hand's "A4 – C4 E4 A4 – B4 – E4 G#4 B4 – C5" is missing.
- The preview's flute plays the loudest pitch of the original only 25% of the time.
- The transcription heard all of those notes (velocities 60–82). But `melody.projected_line` keeps only notes at least 12 velocity above their neighbours, and turns everything else into rests. The pianist plays the E–D# motif loudest.
- The same function also lets one intro chord note into Satie's flute part (F#4, bar 3).

**Fix.** In `melody.piano_melody`, use `projected_line` only as the spine.
- Fill every gap longer than a beat with `top_voice` over the notes inside that gap.
- Keep the gap as a rest only where the transcription is silent above the bass.

### 6. In the preview the piano is barely audible

**Evidence**
- The flute is 18.7 dB louder than the piano on Satie, 15.3 on Chopin, 13.7 on the anthem, 13.2 on America and 11.0 on Für Elise. On the test sets it is 12–15 dB louder.
- The piano is louder in only 2–12% of the frames where both play.
- The cause: chord velocities are `0.75 × level`, clamped to 36–72, while the flute keeps the recording's 80–95.

**Why it matters.** In a flute and piano duo the accompaniment usually sits roughly 3–8 dB under the soloist. At 15+ dB below, the user can't hear whether the chords are right, which is the whole point of the preview.

**Fix.** Aim for the flute about 6 dB over the piano, and check it with this audit's "flute − piano" column. Either:
- raise the chord velocities in `piano._chords_style` (e.g. clamp to 56–84, with the left hand +6), or
- lower the flute channel in `render._VOLUME`.

### 7. Notes in the wrong octave, and leaps over an octave

**Evidence**
- Intervals exact: Satie 95%, Chopin 93%, Für Elise 70%.
- Satie bar 21: E4 is written for E5, a 14-semitone drop where the tune steps down a tone.
- Chopin bar 9: F5 G4 D5 for F5 G5 D5.
- Für Elise bar 23: E6.
- Leaps over an octave: 1.5 per minute in Für Elise and 2.8 in Chopin, where the tunes have none.

**Fix.** Smooth out octave displacements in `flute.fit_to_flute`, after the phrase moves. Move a note by an octave when:
- it sits more than a 7th from both neighbours, and
- moving it brings both intervals within a 6th.

Better, also stop the picker from choosing an accompaniment note an octave away (`melody.top_voice`).

### Smaller things a player would notice

- **Spelling.** The same pitch is spelled D#5 in bars 2, 6 and 10 of Für Elise, but E♭5 in bar 7. `score.spell` flips sharps and flats by where the melody goes next. Keep the key's leading-tone spellings (D# in A minor) unless the note really resolves down.
- **Long final note.** America's final C6 is written as six tied bars (285 ticks, 11.6 s at the printed tempo). Write one note with a fermata, in `score._fill_measures` or when the line is made.
- **Low right-hand chords.**
  - 24% of Satie's and 15% of Für Elise's right-hand noteheads need 3 or more ledger lines below the treble staff. The right hand lies in E3–G4, and `build_score` keeps treble clef unless the median is under G3.
  - Fix: switch the right hand to bass clef when most chords lie below C4, or voice them higher where the flute allows.
- **Stray clef.** In flute-only scores a small treble clef appears after a multi-bar rest (America, bar 3).
- **High register.** The flute's register centre is G5 (`flute.SWEET`), so hymn-range tunes go up an octave. On the anthem 23% of notes need 3 or more ledger lines (E6–G6). That is playable, but bright and effortful. An option to prefer the lower octave when the whole tune fits D4–A5 would help.

## What is good (keep it)

- **Pitches of clear tunes.**
  - Satie: 22 of 22 tune notes found;
  - anthem: 48 of 50, every interval exact;
  - Bach test melodies: 83–99% found, 99.7–100% of intervals exact.
- **Rhythm when the beat is right.** Piano test music has 96% of notes in the same rhythm, broken chords 98.5%, and bwv267 (page 1) reads cleanly in quarters and 8ths.
- **Meter (3 vs 4)** is right in 35 of 39 test pieces.
- **Playable.**
  - Every flute part stays within C4–C7, and 96–100% of the playing time is in D4–A6.
  - No little rests chop up phrases (0.0 per bar).
  - Breath stretches are at most 8.3 s, apart from the one held final note.
- **"Make it easier to play"** does what it says: no notes shorter than an 8th, no tied 16th stubs, and ties drop by 40–60%.
- **Preview.**
  - No clipping (peak about −1.4 dBFS).
  - The flute plays the singer's pitch 95% of the time on the anthem, and America's 86% of the time against its vocal stem.
  - On the score's own beat grid, the preview's pitches match the original at 0.69–0.77 (cosine similarity of chroma), against 0.35–0.55 when misaligned.
- **The piano never sits above the flute** (all checks passed).

## What-if experiment (functions overridden in memory, nothing changed on disk)

Columns: clashes / strong-beat flute notes in the piano chord / piano bass = real bass. Each is a mean over the pieces in the set.

| test set | as now | bar lines fixed | one chord per beat | bars + beat | key prior | all four (+ bass from piano) |
|---|---|---|---|---|---|---|
| A piano | 19.8 / 58 / 39 | 15.7 / 68 / 46 | 16.3 / 73 / 70 | 16.2 / 77 / 70 | 19.8 / 58 / 38 | **11.7 / 79 / 76** |
| B piano + clarinet/violin | 18.9 / 64 / 43 | 13.0 / 73 / 45 | 11.8 / 75 / 69 | 11.1 / 80 / 69 | 19.0 / 64 / 41 | **9.8 / 80 / 76** |
| C broken chords | 30.7 / 46 / 30 | 31.8 / 56 / 29 | 31.2 / 50 / 58 | 30.5 / 55 / 59 | **19.9** / 54 / 30 | 22.0 / 67 / 64 |
| D sung + drums | 15.4 / 67 / 56 | 14.4 / 71 / 59 | 15.1 / 75 / 73 | 14.9 / 76 / 72 | 13.6 / 67 / 56 | **10.6 / 81 / 72** |

On the recordings (same columns, then chords in key):

| song | as now | one chord per beat | bass from piano | key prior | beat + bass + key |
|---|---|---|---|---|---|
| Satie | 16.4 / 69 / 49 / 75 | 17.8 / 56 / 58 / 75 | 20.2 / 62 / 54 / 78 | 15.0 / 72 / 51 / 80 | 16.5 / 54 / 70 / 85 |
| Für Elise | 31.1 / 50 / 53 / 86 | 37.8 / 47 / 59 / 79 | 28.4 / 50 / 73 / 74 | 28.0 / 50 / 53 / 94 | 25.8 / 58 / 72 / 93 |
| Chopin | 18.7 / 58 / 57 / 66 | 21.9 / 56 / 65 / 64 | 22.2 / 59 / 69 / 66 | 18.3 / 60 / 59 / 71 | 20.5 / 58 / 73 / 72 |
| Anthem | 24.1 / 50 / 50 / 72 | 11.8 / 86 / 88 / 52 | = | 22.7 / 50 / 50 / 93 | 11.3 / 86 / 88 / 86 |
| America | 18.3 / 59 / 73 / 54 | 18.5 / 62 / 79 / 53 | = | 19.4 / 59 / 74 / 61 | 18.6 / 62 / 80 / 61 |

What the tables say:
- A fixed chord rhythm can't serve both Bach and Satie, which is why fix 3.1 is a dynamic-programming search rather than "one chord per beat".
- The key prior is nearly free, and helps most where it matters.
- The bass from the piano only helps once the chords line up with the bar.

## Measured tables

<!-- AUTO:BEGIN -->
### Real recordings: accuracy against the published tune

| song | options | printed | tune notes found | intervals exact | same rhythm (±16th) | tempo factor | bar lines | wrong / missing / extra |
|---|---|---|---|---|---|---|---|---|
| Satie, Gymnopédie No. 1 | meter=3 | 3/4 87 bpm | 100.0% of 22 | 95.2% | 90.5% (90.5%) | 1.0 | right for 40.9% of notes; +1 beat: 59.1% | 0 / 0 / 0 |
| Satie, Gymnopédie No. 1 | meter=3, simple=True | 3/4 87 bpm | 100.0% of 22 | 95.2% | 90.5% (90.5%) | 1.0 | right for 40.9% of notes; +1 beat: 59.1% | 0 / 0 / 0 |
| Beethoven, Für Elise | meter=3 | 3/4 152 bpm | 74.3% of 35 | 72.2% | 16.7% (55.6%) | 1.5 | printed 3/4 bar = 2 tune eighths, real bar = 3 | 8 / 1 / 13 |
| Beethoven, Für Elise | meter=3, simple=True | 3/4 152 bpm | 74.3% of 35 | 72.2% | 0.0% (61.1%) | 1.5 | printed 3/4 bar = 2 tune eighths, real bar = 3 | 8 / 1 / 13 |
| Chopin, Nocturne Op. 9 No. 2 | defaults | 4/4 119 bpm | 100.0% of 29 | 100.0% | 46.2% (53.8%) | 1.0 | printed 4/4 bar = 4 tune eighths, real bar = 12 | 0 / 0 / 2 |
| Chopin, Nocturne Op. 9 No. 2 | simple=True | 4/4 119 bpm | 100.0% of 29 | 100.0% | 46.2% (46.2%) | 1.0 | printed 4/4 bar = 4 tune eighths, real bar = 12 | 0 / 0 / 2 |
| Star-Spangled Banner (soprano + band) | meter=3 | 3/4 145 bpm | 96.0% of 50 | 100.0% | 44.4% (48.9%) | 2.0 | printed 3/4 bar = 1.5 tune quarters, real bar = 3 | 1 / 1 / 2 |
| Star-Spangled Banner (soprano + band) | meter=3, simple=True | 3/4 145 bpm | 96.0% of 50 | 100.0% | 46.7% (46.7%) | 2.0 | printed 3/4 bar = 1.5 tune quarters, real bar = 3 | 1 / 1 / 2 |
| Star-Spangled Banner (soprano + band) | meter=3, tempo=-1 | 3/4 73 bpm | 96.0% of 50 | 100.0% | 46.7% (82.2%) | 1.0 | right for 0.0% of notes; +1.5 beat: 31.2%, +1 beat: 18.8%, +1.25 beat: 10.4% | 1 / 1 / 2 |
| America the Beautiful (male voice + band) | defaults | 4/4 123 bpm | 83.9% of 56 | 100.0% | 24.4% (24.4%) | 3.0 | printed 4/4 bar = 1.33333 tune quarters, real bar = 4 | 5 / 4 / 17 |
| America the Beautiful (male voice + band) | simple=True | 4/4 123 bpm | 83.9% of 56 | 100.0% | 24.4% (24.4%) | 3.0 | printed 4/4 bar = 1.33333 tune quarters, real bar = 4 | 5 / 4 / 16 |

### Real recordings: harmony (flute + piano, main option set)

| song | strong-beat flute notes in the piano chord | clashes vs piano | clashes vs original | tritone on bass | chords in key | bass 7th leaps | cadences I/V | chord notes in original | bass = original | flute bars with no piano |
|---|---|---|---|---|---|---|---|---|---|---|
| Satie, Gymnopédie No. 1 | 75.0% | 14.8% | 19.5% | 1.7% | 88.5% | 0.0% | 66.7% of 9 | 91.8% | 76.5% | 0.0% |
| Beethoven, Für Elise | 70.5% | 17.1% | 25.2% | 6.6% | 93.7% | 1.1% | 71.4% of 14 | 79.7% | 92.1% | 0.0% |
| Chopin, Nocturne Op. 9 No. 2 | 73.7% | 13.4% | 21.6% | 2.2% | 75.2% | 3.9% | 42.9% of 7 | 87.2% | 88.1% | 0.0% |
| Star-Spangled Banner (soprano + band) | 92.3% | 4.4% | 9.4% | 1.1% | 98.0% | 0.0% | 100.0% of 7 | 34.8% | 25.0% | 3.2% |
| America the Beautiful (male voice + band) | 76.6% | 12.7% | 13.8% | 4.3% | 62.3% | 3.7% | 20.0% of 15 | 77.6% | 92.6% | 2.2% |

### Real recordings: reading and playing the flute part

| song | options | notes/bar | ties/bar | tied 16th stubs/bar | < 8th | dotted | off-beat across beat | short rests in phrases/bar | accidentals/bar | range | longest without breath | leaps > 8ve/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Satie, Gymnopédie No. 1 | meter=3 | 1.99 | 0.4 | 0.05 | 2.6% | 16.3% | 2.5% | 0.0 | 0.21 | D4–A5 | 8.3 s | 0.34 |
| Satie, Gymnopédie No. 1 | meter=3, simple=True | 1.95 | 0.36 | 0.0 | 0.0% | 14.7% | 2.5% | 0.0 | 0.21 | D4–A5 | 8.3 s | 0.34 |
| Beethoven, Für Elise | meter=3 | 3.04 | 0.92 | 0.63 | 26.9% | 16.8% | 27.0% | 0.0 | 0.36 | G4–E6 | 4.5 s | 0.0 |
| Beethoven, Für Elise | meter=3, simple=True | 2.54 | 0.45 | 0.0 | 0.0% | 10.3% | 10.4% | 0.0 | 0.37 | G4–E6 | 4.7 s | 0.0 |
| Chopin, Nocturne Op. 9 No. 2 | defaults | 3.54 | 1.01 | 0.4 | 16.6% | 17.7% | 21.3% | 0.0 | 0.4 | Eb4–C7 | 6.6 s | 0.69 |
| Chopin, Nocturne Op. 9 No. 2 | simple=True | 3.13 | 0.71 | 0.0 | 0.0% | 12.7% | 14.8% | 0.0 | 0.39 | Eb4–C7 | 6.6 s | 0.69 |
| Star-Spangled Banner (soprano + band) | meter=3 | 2.68 | 1.1 | 0.16 | 6.6% | 23.6% | 51.4% | 0.0 | 0.09 | C5–G6 | 4.4 s | 0.0 |
| Star-Spangled Banner (soprano + band) | meter=3, simple=True | 2.57 | 1.0 | 0.0 | 0.0% | 22.9% | 49.5% | 0.0 | 0.09 | C5–G6 | 4.3 s | 0.0 |
| Star-Spangled Banner (soprano + band) | meter=3, tempo=-1 | 4.47 | 1.32 | 0.82 | 28.9% | 15.1% | 36.4% | 0.0 | 0.18 | C5–G6 | 7.2 s | 0.0 |
| America the Beautiful (male voice + band) | defaults | 3.33 | 1.25 | 0.41 | 15.6% | 20.9% | 39.2% | 0.0 | 0.56 | C4–C7 | 13.0 s | 0.85 |
| America the Beautiful (male voice + band) | simple=True | 2.94 | 0.88 | 0.0 | 0.0% | 20.7% | 32.9% | 0.0 | 0.55 | C4–C7 | 12.9 s | 1.13 |

### Real recordings: how the preview sounds next to the original

| song | chroma sim, DTW (null) | chroma sim, beat grid (null) | flute pitch = loudest in original | flute pitch in top 3 | vs vocal stem top 1 / top 3 | flute − piano (frames piano louder) | peak / clipped |
|---|---|---|---|---|---|---|---|
| Satie, Gymnopédie No. 1 | 0.856 (0.698) | 0.788 (0.465) | 59.0% | 96.1% | – | 8.4 dB (14.3%) | -1.45 dBFS / 0 |
| Beethoven, Für Elise | 0.84 (0.797) | 0.77 (0.574) | 25.6% | 70.4% | – | 3.3 dB (28.5%) | -1.57 dBFS / 0 |
| Chopin, Nocturne Op. 9 No. 2 | 0.855 (0.78) | 0.785 (0.514) | 39.5% | 84.3% | – | 7.2 dB (13.0%) | -1.49 dBFS / 0 |
| Star-Spangled Banner (soprano + band) | 0.769 (0.693) | 0.714 (0.415) | 95.2% | 99.5% | 95.2% / 99.5% | 6.5 dB (11.2%) | -1.54 dBFS / 0 |
| America the Beautiful (male voice + band) | 0.863 (0.729) | 0.804 (0.5) | 61.2% | 93.1% | 87.5% / 97.4% | 7.5 dB (14.6%) | -1.54 dBFS / 0 |

Examples (main option set):

- **Satie, Gymnopédie No. 1** (tune found from bar 48.2, transposed 0): bar 62.2: F#5->E4 (-14) where the tune goes -2. Clashes on strong beats: bar 7.1: flute C#5 on D2; bar 15.1: flute G5 on F#3; bar 49.1: flute G5 on F#3. Bass 7th leaps: none. Longest breathless stretch 8.3 s at bars 5.3-9.3.
- **Beethoven, Für Elise** (tune found from bar 21.2, transposed 0): bar 21.2.75: Eb expected, E5 written; bar 23.2: Eb expected, E5 written; bar 28.3.5: A expected, C5 written; A (tune note 30) missing before bar 42.2.25; bar 26.3.5: A4->C5 (+3) where the tune goes -9; bar 32.1: B4->E5 (+5) where the tune goes -7. Clashes on strong beats: bar 5.1: flute Eb5 on E4; bar 42.1: flute Eb5 on E2/E4; bar 47.1: flute F5 on E4. Bass 7th leaps: B2->C2. Longest breathless stretch 4.5 s at bars 81.2.5-85.2.
- **Chopin, Nocturne Op. 9 No. 2** (tune found from bar 1.1, transposed 0): . Clashes on strong beats: bar 4.1: flute Eb5 on D4; bar 22.1: flute G5 on Ab3; bar 29.3: flute D5 on Eb2/Eb4. Bass 7th leaps: B2->C2, B2->C2, Bb2->C2, C2->Bb2. Longest breathless stretch 6.6 s at bars 61.4-65.1.
- **Star-Spangled Banner (soprano + band)** (tune found from bar 1.3.5, transposed 0): bar 30.1: C expected, B5 written; C (tune note 21) missing before bar 13.2.5. Clashes on strong beats: bar 30.1: flute B5 on C2/C4. Bass 7th leaps: none. Longest breathless stretch 4.4 s at bars 21.2-24.3.75.
- **America the Beautiful (male voice + band)** (tune found from bar 10.4.5, transposed 8): bar 16.1.5: Bb expected, C5 written; bar 16.3.75: Bb expected, Ab4 written; bar 26.1: Bb expected, B4 written; C (tune note 30) missing before bar 32.4; Bb (tune note 38) missing before bar 38.3. Clashes on strong beats: bar 26.1: flute B4 on Bb3; bar 43.1: flute G5 on Ab2/Ab3; bar 46.3: flute E5 on F4. Bass 7th leaps: Bb2->C2, C2->Bb2, C2->B2. Longest breathless stretch 13.0 s at bars 102.1.75-108.4.5.

### Test music (true melody and true chords known), default options

| set | pieces | tune notes found | intervals exact | same rhythm (±16th) | meter right | tempo right | true downbeats on bar lines | strong-beat flute notes in the piano chord | clashes vs piano | clashes vs real chords | chord notes in real chord | bass = real bass | ties/bar | < 8th | longest without breath | chroma sim grid (null) | flute − piano |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A (piano) | 12 | 89.2% | 99.8% | 96.3% (96.3%) | 11/12 | 12/12 | 60.6% | 94.6% | 3.5% | 5.5% | 83.4% | 86.1% | 0.0 | 1.6% | 7.3 s | 0.785 (0.515) | 5.9 dB |
| B (piano + clarinet/violin) | 12 | 83.2% | 99.7% | 56.0% (71.3%) | 10/12 | 11/12 | 19.4% | 92.2% | 6.1% | 6.6% | 82.9% | 84.9% | 0.2 | 20.2% | 7.2 s | 0.843 (0.532) | 5.2 dB |
| C (broken chords) | 3 | 98.7% | 100.0% | 98.5% (98.5%) | 3/3 | 2/3 | 66.7% | 81.4% | 6.2% | 3.0% | 65.9% | 67.2% | 0.0 | 0.0% | 6.2 s | 0.751 (0.445) | 4.4 dB |
| D (sung + drums) | 12 | 96.6% | 100.0% | 93.3% (94.2%) | 11/12 | 7/12 | 60.6% | 92.2% | 4.1% | 5.5% | 80.2% | 86.0% | 0.2 | 1.4% | 5.9 s | 0.835 (0.587) | 4.7 dB |

<details><summary>Per piece</summary>

| piece | printed | real | tune found | intervals | rhythm ±16th | downbeats on bar lines | clashes piano / real | bass = real | ties/bar |
|---|---|---|---|---|---|---|---|---|---|
| A_bwv66_6 | 4/4 77 | 4/4 x1.0 | 94.4% | 100.0% | 87.1% | 0.0% | 0.0% / 1.4% | 90.9% | 0.0 |
| A_bwv269 | 3/4 98 | 3/4 x1.0 | 91.3% | 100.0% | 100.0% | 100.0% | 1.4% / 1.9% | 91.9% | 0.09 |
| A_bwv347 | 4/4 89 | 4/4 x1.0 | 96.2% | 100.0% | 100.0% | 100.0% | 3.7% / 5.5% | 88.2% | 0.07 |
| A_bwv153_1 | 4/4 80 | 4/4 x1.0 | 100.0% | 100.0% | 95.7% | 0.0% | 4.5% / 3.5% | 77.5% | 0.0 |
| A_bwv86_6 | 4/4 93 | 4/4 x1.0 | 86.0% | 100.0% | 96.8% | 100.0% | 6.5% / 8.0% | 87.2% | 0.0 |
| A_bwv267 | 4/4 84 | 4/4 x1.0 | 92.3% | 100.0% | 98.5% | 100.0% | 6.4% / 6.2% | 79.1% | 0.06 |
| A_bwv40_8 | 4/4 86 | 4/4 x1.0 | 67.1% | 97.4% | 94.7% | 100.0% | 1.3% / 4.0% | 87.5% | 0.15 |
| A_bwv244_37 | 4/4 73 | 4/4 x1.0 | 79.2% | 100.0% | 90.9% | 100.0% | 8.9% / 12.0% | 85.1% | 0.15 |
| A_bwv26_6 | 4/4 82 | 4/4 x1.0 | 95.0% | 100.0% | 97.1% | 0.0% | 0.8% / 2.7% | 84.2% | 0.0 |
| A_bwv64_2 | 3/4 93 | 4/4 x1.0 | 89.5% | 100.0% | 100.0% | 27.3% | 3.0% / 6.6% | 90.2% | 0.0 |
| A_bwv2_6 | 4/4 92 | 4/4 x1.0 | 90.9% | 100.0% | 100.0% | 100.0% | 5.3% / 8.0% | 75.0% | 0.08 |
| A_bwv101_7 | 4/4 80 | 4/4 x1.0 | 88.0% | 100.0% | 95.0% | 0.0% | 0.6% / 6.1% | 95.8% | 0.0 |
| B_bwv66_6 | 4/4 77 | 4/4 x1.0 | 52.8% | 100.0% | 64.3% | 0.0% | 12.1% / 14.5% | 90.9% | 0.0 |
| B_bwv269 | 4/4 98 | 3/4 x1.0 | 89.1% | 100.0% | 80.0% | 22.7% | 2.9% / 5.6% | 76.7% | 0.71 |
| B_bwv347 | 4/4 89 | 4/4 x1.0 | 62.3% | 100.0% | 89.5% | 100.0% | 2.8% / 4.6% | 88.2% | 0.0 |
| B_bwv153_1 | 4/4 80 | 4/4 x1.0 | 100.0% | 100.0% | 71.7% | 0.0% | 6.5% / 3.8% | 74.4% | 0.09 |
| B_bwv86_6 | 4/4 93 | 4/4 x1.0 | 72.1% | 100.0% | 87.0% | 0.0% | 11.7% / 11.2% | 85.0% | 0.1 |
| B_bwv267 | 4/4 84 | 4/4 x1.0 | 100.0% | 100.0% | 63.6% | 0.0% | 11.2% / 6.5% | 77.9% | 0.17 |
| B_bwv40_8 | 4/4 86 | 4/4 x1.0 | 73.7% | 100.0% | 82.2% | 0.0% | 1.9% / 3.1% | 88.5% | 0.0 |
| B_bwv244_37 | 4/4 147 | 4/4 x2.01 | 98.1% | 96.0% | 52.0% | 83.3% | 11.2% / 9.4% | 93.8% | 0.56 |
| B_bwv26_6 | 4/4 82 | 4/4 x1.0 | 57.5% | 100.0% | 80.0% | 0.0% | 1.1% / 2.5% | 81.6% | 0.1 |
| B_bwv64_2 | 3/4 93 | 4/4 x1.0 | 100.0% | 100.0% | 29.7% | 27.3% | 2.7% / 5.2% | 90.2% | 0.0 |
| B_bwv2_6 | 4/4 92 | 4/4 x1.0 | 93.2% | 100.0% | 94.6% | 0.0% | 3.5% / 5.6% | 77.3% | 0.27 |
| B_bwv101_7 | 4/4 80 | 4/4 x1.0 | 100.0% | 100.0% | 61.2% | 0.0% | 5.2% / 6.9% | 93.8% | 0.31 |
| C_bwv104_6_arp | 4/4 78 | 4/4 x1.0 | 100.0% | 100.0% | 95.6% | 0.0% | 9.4% / 6.4% | 71.1% | 0.0 |
| C_bwv108_6_arp | 4/4 144 | 4/4 x2.0 | 98.4% | 100.0% | 100.0% | 100.0% | 5.7% / 1.4% | 67.7% | 0.04 |
| C_bwv113_8_arp | 4/4 96 | 4/4 x1.0 | 97.7% | 100.0% | 100.0% | 100.0% | 3.6% / 1.1% | 62.8% | 0.0 |
| D_bwv66_6 | 4/4 154 | 4/4 x2.0 | 100.0% | 100.0% | 100.0% | 0.0% | 1.0% / 1.9% | 83.3% | 0.16 |
| D_bwv269 | 4/4 98 | 3/4 x1.0 | 100.0% | 100.0% | 100.0% | 27.3% | 0.4% / 2.3% | 89.1% | 0.44 |
| D_bwv347 | 4/4 89 | 4/4 x1.0 | 98.1% | 100.0% | 98.0% | 100.0% | 4.5% / 6.4% | 88.2% | 0.07 |
| D_bwv153_1 | 4/4 160 | 4/4 x2.0 | 95.7% | 100.0% | 95.2% | 100.0% | 3.8% / 3.4% | 84.8% | 0.19 |
| D_bwv86_6 | 4/4 93 | 4/4 x1.0 | 97.7% | 100.0% | 97.5% | 0.0% | 3.6% / 7.9% | 85.0% | 0.18 |
| D_bwv267 | 4/4 84 | 4/4 x1.0 | 97.4% | 100.0% | 98.6% | 0.0% | 6.8% / 6.9% | 77.6% | 0.33 |
| D_bwv40_8 | 4/4 86 | 4/4 x1.0 | 94.7% | 100.0% | 94.0% | 100.0% | 1.9% / 5.4% | 86.2% | 0.19 |
| D_bwv244_37 | 4/4 147 | 4/4 x2.01 | 98.1% | 100.0% | 94.0% | 100.0% | 12.0% / 10.4% | 86.5% | 0.08 |
| D_bwv26_6 | 4/4 164 | 4/4 x2.0 | 85.0% | 100.0% | 74.1% | 100.0% | 2.2% / 3.6% | 92.5% | 0.24 |
| D_bwv64_2 | 4/4 93 | 4/4 x1.0 | 97.4% | 100.0% | 91.7% | 100.0% | 2.1% / 4.4% | 92.5% | 0.08 |
| D_bwv2_6 | 4/4 92 | 4/4 x1.0 | 97.7% | 100.0% | 97.6% | 0.0% | 6.6% / 7.4% | 81.8% | 0.25 |
| D_bwv101_7 | 4/4 160 | 4/4 x2.0 | 98.0% | 100.0% | 89.4% | 100.0% | 4.6% / 5.6% | 84.2% | 0.08 |

</details>
<!-- AUTO:END -->
