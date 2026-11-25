# Notation rules for the flute part

The brief for this rulebook: the sheet music should incorporate "first of all how music is
written basically and also music theory and how it should be written". It collects the
engraving conventions and the pieces of tonal theory the app relies on, in short rules
with IDs, so code, tests and reviews can cite them (`R-METER-3`, `R-BEAM-2`, ...).

Each rule says what a published edition does. Where the app does something narrower, the
rule says so ("the app ..."). `sheetswap/notation.py` checks an arrangement against the
rules marked **[lint]** and applies the fixes marked **[tidy]** before the score is
written; `sheetswap/score.py` follows the rules marked **[score]** while engraving.

Sources (paraphrased, not quoted):
- Elaine Gould, *Behind Bars: The Definitive Guide to Music Notation* (Faber Music, 2011):
  chapters on meter and rhythmic grouping, beaming, rests, ties, accidentals, woodwind
  parts and part preparation.
- Kurt Stone, *Music Notation in the Twentieth Century: A Practical Guidebook* (Norton,
  1980): general conventions, accidentals, beaming, ornaments.
- Gardner Read, *Music Notation: A Manual of Modern Practice* (2nd ed., 1969).
- Common-practice theory texts: Aldwell & Schachter, *Harmony and Voice Leading*;
  Kostka, Payne & Almén, *Tonal Harmony*; Laitz, *The Complete Musician*; Caplin,
  *Classical Form* (phrase types).
- Flute: Samuel Adler, *The Study of Orchestration*; Nancy Toff, *The Flute Book*.

Ticks below are the app's grid: 12 ticks to a quarter note (`config.TICKS_PER_BEAT`),
so an 8th is 6, a 16th 3, a triplet 8th 4, a dotted-quarter beat 18.

---

## 1. The page (R-PAGE)

- **R-PAGE-1** At the start of the first system: clef, then key signature, then time
  signature; the tempo marking above the staff over the time signature. Every later
  system repeats clef and key signature, not the time signature. [score]
- **R-PAGE-2** Flute parts stay in the treble clef throughout (no clef changes). Piano:
  right hand treble, left hand bass; the right hand may use the bass clef when it sits
  low for a whole piece. [score]
- **R-PAGE-3** Title centred at the top, instrument name before the first system, bar
  numbers at the start of each system. (Verovio)
- **R-PAGE-4** The piece ends with a final barline (thin-thick). A double barline marks a
  change of key, meter or section. [score] [lint]
- **R-PAGE-5** Pickup (anacrusis): the first bar holds only the pickup notes, with no
  rests before them, and is numbered 0 so bar 1 is the first full bar. Traditionally the
  last bar is shortened by the same amount; the app leaves the last bar full. [score]
  [lint]
- **R-PAGE-6** Repeats: write a passage out unless it repeats exactly (notes, rhythm and
  marks). A repeat back to the very beginning needs no start-repeat sign; different
  endings use 1st/2nd-time bars. The app writes everything out, because recorded
  repeats are rarely identical; that is always correct, only longer.
- **R-PAGE-7** In a part for one player, two or more empty bars in a row become one
  multi-bar rest with its count above. In a score (flute with piano) every bar is shown.
  [score]

## 2. Meter: show the beat (R-METER)

The reader must see where every beat falls. A note that starts on a beat may last
several beats; a note that starts between beats is tied at the next beat that has to
be visible.

- **R-METER-1** No note or rest crosses a bar line. A note longer than the rest of its
  bar is tied into the next bar. [score] [lint]
- **R-METER-2** Simple meters (2/4, 3/4, 4/4): a note starting on a beat may span whole
  beats (half, dotted half, whole) as long as R-METER-3 allows it. In 2/4 and 3/4
  every beat-start value that fits the bar is fine (quarter-half in 3/4, half on beat 1
  of 2/4, dotted quarter on beat 2 of 3/4). [score]
- **R-METER-3** Half-bar rule (4/4, and 12/8 by analogy): the middle of the bar (beat 3)
  must be visible. A note across beat 3 is tied at beat 3, except the familiar
  patterns that start on a beat: the whole note; a dotted half on beat 1 or 2; a half on
  beat 2 (quarter-half-quarter); a dotted quarter on beat 2 (followed by an 8th). Any
  note that starts off a beat and crosses beat 3 is always tied there. [score] [lint]
- **R-METER-4** Syncopation: an off-beat note that lasts past the next beat is tied at
  that beat, except the common 8th-quarter-8th (or 8th-dotted quarter) figure that
  stays inside one half bar of 4/4, or inside a bar of 2/4 or 3/4. [score] [lint]
- **R-METER-5** Inside a quarter beat: on the beat a quarter, dotted 8th, 8th or 16th
  may start; on the second 16th a dotted 8th (filling the beat), an 8th
  (16th-8th-16th) or a 16th; on the 8th a 8th or 16th; on the last 16th only a 16th.
  [score]
- **R-METER-6** Dotted values are written as dots (not ties) when the dotted value starts
  where R-METER-2 to R-METER-5 allow it; otherwise the value is split and tied
  (R-TIE-2). [score]
- **R-METER-7** Compound meters (6/8, 9/8, 12/8): the beat is a dotted quarter and the
  page shows dotted-quarter beats. On a beat a dotted quarter, dotted half (two beats)
  or dotted whole (a full 12/8 bar) may start; inside a beat the figures are
  quarter-8th, 8th-quarter, dotted 8th-16th-8th, three 8ths, 16ths. Nothing starting
  inside a beat crosses into the next one untied. [score] [lint]
- **R-METER-8** 3/8 is one beat per bar (or three 8th beats when slow): group the bar as
  one dotted-quarter beat; a dotted quarter fills it. [score]
- **R-METER-9** Triplets only where the beat really divides in three; one beat is either
  duple (8ths/16ths) or triplets, never a mix. Compound meters have no triplets (their
  beat already divides in three). [score] [lint]
- **R-METER-10** The app writes nothing shorter than a 16th (32nds are simplified) and,
  with "easier to play", nothing shorter than an 8th (checks.py). Off-grid lengths
  (not a multiple of a 16th or a triplet 8th) are errors. [lint]

## 3. Ties and dots (R-TIE)

- **R-TIE-1** Tie only to show a beat, the half bar or a bar line that would otherwise be
  hidden. Never tie two values that are one allowed value at that position (two tied
  quarters on beats 1-2 are a half note; two tied 8ths inside a beat are a quarter).
  [score] [lint]
- **R-TIE-2** Prefer a dot to a tie whenever the dotted value is allowed where it starts.
  [score]
- **R-TIE-3** When a long note is split, the pieces follow the beat: an off-beat start
  fills to the beat first, then whole beats, then the remainder. [score]
- **R-TIE-4** A tie joins two notes of the same pitch only (different pitches under a
  curve are a slur). Attack marks (accent, staccato, tenuto) and dynamics go on the first
  note of a tie; a fermata on the last; a breath mark after the last. [score] [lint]
- **R-TIE-5** The app writes no double dots; a double-dotted value is a dotted value tied
  to a 16th, or a 16th rest follows. [score]

## 4. Rests (R-REST)

- **R-REST-1** A whole bar of silence is one whole rest, centred, in every meter
  (also 3/4, 6/8, 3/8). [score]
- **R-REST-2** Simple meters: a rest never covers two beats, except a half rest on beat 1
  or 3 of 4/4 (never starting on beat 2 or 4). In 3/4 two silent beats are two quarter
  rests. [score] [lint]
- **R-REST-3** No dotted rests in simple meters. [score] [lint]
- **R-REST-4** Inside a beat, rests complete the subdivisions: after a 16th on the beat
  comes a 16th rest then an 8th rest (not a dotted-8th rest); a rest on the second
  16th never runs past the 8th. [score] [lint]
- **R-REST-5** Compound meters: a whole silent beat is a dotted-quarter rest; half a bar
  of 12/8 (beats 1-2 or 3-4) may be a dotted-half rest; the first two 8ths of a beat
  may be one quarter rest; the last two 8ths of a beat are two 8th rests (a quarter
  rest never starts on the second 8th). [score] [lint]
- **R-REST-6** Rests are not fragmented needlessly: silence inside a beat or a bar is
  written with the fewest rests R-REST-2 to R-REST-5 allow. [score] [tidy]
- **R-REST-7** Very short rests between legato notes (under a 16th at the end of a note)
  are not written; the note is held to the next one. (rhythm.py, score.py)

## 5. Beaming (R-BEAM)

- **R-BEAM-1** Notes shorter than a quarter are beamed by beat. In 2/4 and 3/4 each
  quarter beat is one group. [score] [lint]
- **R-BEAM-2** 4/4: 8th notes may be beamed in fours over beats 1-2 or 3-4, never across
  beats 2-3 (the middle of the bar). Groups with 16ths or rests beam per beat. The app
  beams a half bar of four plain 8ths as one group. [score] [lint]
- **R-BEAM-3** Compound meters beam in threes, one group per dotted-quarter beat
  (6/8 = 3+3, 9/8 = 3+3+3, 12/8 = 3+3+3+3); never across beats. [score] [lint]
- **R-BEAM-4** 3/8: one beam over the bar. [score]
- **R-BEAM-5** Secondary (16th) beams follow the 8th subdivisions of the beat; a 16th
  next to an 8th inside a beat takes a partial beam (a "flag" pointing into the
  group). [score]
- **R-BEAM-6** Beams never cross a bar line, and a beam does not start or end on a rest
  that sits on a beat boundary. [score]
- **R-BEAM-7** Triplets: a beamed triplet shows only the number 3; a triplet that is not
  under one beam (with a quarter or a rest) gets a bracket. [score]
- **R-BEAM-8** A beamed group has one stem direction, set by the note furthest from the
  middle line. (Verovio)

## 6. Stems, ledger lines, octave signs (R-STEM, R-RANGE)

- **R-STEM-1** Notes below the middle line (B4) take stems up, notes above it stems
  down; on the middle line the stem goes down unless the neighbours go up. Stems are
  about an octave long; with ledger lines they reach the middle line. (Verovio)
- **R-RANGE-1** Flute written range C4 to C7 (B3 only with a B foot; C#7 and D7 are
  extreme). Written pitch = sounding pitch for the C flute. The app keeps every flute
  note in C4-C7 (`config.FLUTE_HARD`, flute.py). [lint]
- **R-RANGE-2** Flutists read ledger lines up to C7 (five lines) without trouble: single
  high notes use ledger lines, never an octave sign. Below the staff, C4 needs one
  ledger line.
- **R-RANGE-3** An 8va sign is for a passage that stays above A6 (four or more notes in a
  row at A6 or higher), never for one or two notes; it ends with a hook. The app
  never writes 8va; the lint reports such runs. [lint]

## 7. Accidentals and spelling (R-ACC)

- **R-ACC-1** An accidental lasts to the end of the bar for notes on the same line or
  space; the bar line cancels it. [score]
- **R-ACC-2** A note tied across a bar line keeps its accidental without repeating it; a
  new attack of that pitch later in the new bar takes the accidental again. [score]
- **R-ACC-3** Courtesy (cautionary) accidentals: in the bar after an accidental, the first
  note of the same letter that goes back to another alteration gets a courtesy
  accidental: judged in its own octave if the bar before played that octave, else in
  any octave. Inside a bar, the same letter in another octave with a different
  alteration also gets one. [score] [lint]
- **R-ACC-4** Notes of the key use the key signature's letters (F# major writes E#, not
  F; G-flat major writes C-flat, not B). [score] [lint]
- **R-ACC-5** Chromatic notes are spelled by what they do: a chromatic passing note is
  raised going up and lowered going down (C-C#-D, D-Db-C); a chromatic lower neighbour
  is raised (E-D#-E), an upper one lowered (G-Ab-G); a note that resolves by a
  semitone is spelled so the step is a minor 2nd (Gb-F, not F#-F); the leading tone of
  V is #4. In minor the raised 6th and 7th are sharps or naturals on the scale's own
  letters (A minor: F#, G#; never Ab). [score] [lint]
- **R-ACC-6** Within one bar the same sounding pitch is not spelled two ways (A# then
  Bb): the app gives every note of that pitch in the bar the spelling most of them
  have (the earliest on a tie). Through the piece, a chromatic degree keeps one
  spelling in the same function. [score] [lint]
- **R-ACC-7** Double sharps or flats only where theory needs them, which happens in keys
  of five or more sharps or flats (G# minor's leading tone F##, a secondary leading
  tone in F# major); never in keys of four or fewer. Direction-spelled passing notes
  always take a single accidental. [score] [lint]
- **R-ACC-8** A melodic semitone between neighbouring notes uses two adjacent letters (a
  minor 2nd: E-F, F#-G, Bb-A) except inside a chromatic line, where one letter takes
  two forms. The augmented 2nd of harmonic minor (F-G# in A minor) is the one
  augmented step a melody normally shows. [lint]
- **R-ACC-9** Key signatures: sharps in the order F C G D A E B, flats B E A D G C F, at
  their standard staff positions; at most 7. [score]

## 8. Phrasing and slurs (R-PHR)

- **R-PHR-1** Phrases are normally 4 or 8 bars (2+2, 4+4; bars of 2/4, 3/8 and fast 6/8
  count in pairs). Phrase ends are where breath marks, slur ends and dynamics changes
  belong.
- **R-PHR-2** A phrase ends with a cadence whose arrival note falls on a strong beat (beat
  1, or beat 3 of 4/4; beat 1 or 4 of 6/8 counted in 8ths). A "feminine" ending lands
  the arrival on a weak beat after an accented appoggiatura and is also normal. [lint]
- **R-PHR-3** The cadence note is long: it is held at least through its beat, usually
  a half note or longer, and is not followed by fragmentary rests inside its beat.
  The app's fix: a phrase's last note that stops inside its beat, with at least a
  quarter beat of silence after that beat (a whole beat in 2/4-4/4, two 8ths in 6/8,
  9/8, 12/8 and 3/8), or the last note of the piece, is held to the end of its beat,
  so the rests after it start on a beat. Staccato notes keep their length; nothing
  else is lengthened. [tidy] [lint]
- **R-PHR-4** A slur covers one phrase or one figure, from its first to its last note.
  It does not run over a rest or a breath mark (the app ends it there), and it does not
  cover repeated notes only (those are tongued; slurred repeated notes mean portato).
  [tidy] [lint]
- **R-PHR-5** A flute slur is no longer than one breath (expression.py writes at most
  2 bars; the lint flags slurs over 10 s). Slurs are not nested in a flute part. [lint]

## 9. Breathing (R-BREATH)

- **R-BREATH-1** A phrase end without a rest gets a breath mark (a comma above the staff
  just after the note). Before a rest no mark is needed: the rest is the breath.
  [score] [tidy]
- **R-BREATH-2** A flutist needs a breath roughly every 6 to 10 seconds of continuous
  playing at moderate dynamics (the flute uses air faster than the reed instruments;
  low and loud playing uses more). The app gives every stretch of more than 10 s
  without a rest (an 8th or 0.35 s) or breath mark a breath, aiming for about 8 s and
  never closer than 4 s to the breath before or after. A single note held longer than
  10 s can't be helped by a mark; the lint names it. [tidy] [lint]
- **R-BREATH-3** Where to breathe: after a long note, at a phrase or sub-phrase end, at
  a bar line or the half bar. Never inside a tie, between an upbeat and its downbeat,
  between a leading tone and its resolution, or inside a run of fast notes. [tidy]
- **R-BREATH-4** A breath takes its time from the note before it: the written rhythm
  does not change (the preview shortens that note slightly). [score]

## 10. Dynamics, articulation and words (R-MARK)

- **R-MARK-1** Dynamics and hairpins go below the staff of a one-staff instrument, under
  the note they start on. The first note has a dynamic; later dynamics come at phrase
  starts, at most about one per phrase. [score] [lint]
- **R-MARK-2** Articulations (staccato, tenuto, accent) sit at the notehead, on the side
  away from the stem, staccato and tenuto nearest the note. [score]
- **R-MARK-3** Fermatas, trills, turns and mordents go above the staff. [score]
- **R-MARK-4** Tempo: an Italian tempo word in bold upright type above the first bar with
  a metronome mark whose note is the beat (a quarter in 2/4-4/4, a dotted quarter in
  6/8-12/8 and one-beat 3/8, an 8th in 3/8 counted in three). Tempo changes (rit.,
  a tempo) go above in italics; expression words (dolce, espressivo) below in italics
  with the dynamics. [score]
- **R-MARK-5** A hairpin spans at least a beat and stops before the next dynamic.
  [score] [lint]
- **R-MARK-6** Dynamics, slurs and articulations attach to notes, never to rests.
  [score]

## 11. Music theory the app uses (R-THEORY)

- **R-THEORY-1** Keys: a key signature is counted in fifths (sharps positive, flats
  negative); a major key's tonic is 7 x fifths semitones above C; its relative minor's
  tonic three semitones lower, sharing the signature. (harmony.py, score.py)
- **R-THEORY-2** Scale degrees: 1 tonic, 2 supertonic, 3 mediant, 4 subdominant, 5
  dominant, 6 submediant, 7 leading tone (subtonic when a whole step below 1).
- **R-THEORY-3** Minor: natural minor (the signature), harmonic minor (raised 7, the
  leading tone, needed for V), melodic minor (raised 6 and 7 going up, natural going
  down). Raised degrees are spelled on their own letters (R-ACC-5).
- **R-THEORY-4** Cadences: authentic V-I (perfect when both chords are in root position
  and the melody ends on 1, usually 2-1 or 7-1; imperfect otherwise), half cadence
  (ends on V; melody on 2, 7 or 5), plagal IV-I, deceptive V-vi. The arrival chord
  falls on a strong beat (R-PHR-2). A trill on the note before the final tonic (the
  penultimate 2 or 7) is the classical cadential trill. (harmony.py, expression.py)
- **R-THEORY-5** Phrase forms: the period (antecedent ending on a half cadence,
  consequent on an authentic one) and the sentence (2 + 2 + 4 bars).
- **R-THEORY-6** Non-chord tones and how they are written:
  - passing tone: steps in and out in the same direction, normally on a weak beat or
    the weak part of a beat; spelled in its direction (R-ACC-5);
  - neighbour tone: steps away and back, normally unaccented; a chromatic lower
    neighbour is raised, an upper one lowered;
  - appoggiatura: leapt to, resolves by step (usually down), accented: written as a
    full-size note on the beat, taking the beat's time;
  - suspension: prepared, held (tied) over the strong beat, resolves down by step;
    here the tie across the beat is the correct notation (R-TIE-1);
  - anticipation: the next chord's note arriving early on a weak part of the beat,
    struck again (not tied);
  - escape tone: stepped to, left by leap the other way; pedal: a held or repeated
    note under changing chords.
- **R-THEORY-7** Harmonic rhythm: chords change on beats, more often towards cadences;
  strong-beat melody notes are normally chord tones, except appoggiaturas and
  suspensions. The piano part has at most one chord per beat. (harmony.py, piano.py)
- **R-THEORY-8** Ornaments: a trill starts on the upper note in Baroque and Classical
  music; an acciaccatura (slashed grace note) comes before the beat, an appoggiatura
  (unslashed) takes time on the beat. The app writes slashed grace notes only.

## 12. Flute idioms (R-FLUTE)

- **R-FLUTE-1** Registers: low C4-B4 is soft and breathy and is easily covered by a
  piano; middle C5-B5 is the most flexible; high C6-C7 is bright and loud, hard to
  play softly above A6. [lint]
- **R-FLUTE-2** Dynamics must fit the register: no f/ff on passages sitting below G4,
  no pp/p on notes at A6 and above. [lint]
- **R-FLUTE-3** Awkward or impossible trills: on the lowest notes (C4-D4: foot-key
  trills) and at A6 and above (several high trills are impossible). [lint]
- **R-FLUTE-4** One note at a time: chords and double stops in a source are reduced to
  one line (the top note) (melody.py, score_input.py; checks.py reports overlaps).
- **R-FLUTE-5** Wide slurred leaps downward into the low register and fast wide leaps
  in the low register are hard; an octave leap up slurred is easy. (expression.py
  breaks slurs at leaps over a 5th.)
- **R-FLUTE-6** Breathing: see R-BREATH; plan breaths, don't leave them to chance.

## 13. Where each rule lives

| Rules | Done by |
|---|---|
| R-METER, R-TIE, R-REST (splitting notes and rests into written values) | `score._pieces` |
| R-BEAM-1..8 (beam groups per meter, the 4/4 half-bar group, 16th beams, stems) | `score._beam_part` |
| R-PAGE-5 (pickup bar 0), R-PAGE-4, R-PAGE-7 | `score._fill_measures`; bar numbers `checks.printed_bar` |
| R-ACC-4, R-ACC-5, R-ACC-7 (spelling) | `score.spell`, `score._passing` |
| R-ACC-6 (one spelling per bar) | `score._one_spelling_per_bar` |
| R-ACC-1..3 (which accidentals are printed) | music21 `makeNotation` + `score._courtesy_accidentals` |
| R-BREATH-2/3, R-PHR-3, R-PHR-4 (safe fixes) | `notation.tidy`, run by `score.write_outputs` |
| everything marked [lint] | `notation.lint`, written to `notation.json`; the song page shows it as "Notation check" |
| R-RANGE-1, R-FLUTE-4 | `flute.py`, `checks.py` |
| R-PHR-4/5, R-MARK, R-THEORY-4 | `expression.py` |
| R-BREATH-1 (breaths in long phrases) | `rhythm.add_breath_marks`, then `notation.tidy` |

`notation.tidy` never changes a pitch or when a note starts. The one change to a length
it makes is R-PHR-3 (holding a phrase's last note to the end of its beat instead of
writing a rest fragment inside that beat); breath marks are added, never removed;
slurs are ended at breath marks and rests. The arrangement passed in is not changed:
`arr` as arranged (and as the benchmarks score it) keeps its rhythm; only the written
page and the preview follow the tidied copy.

Lint levels: "error" breaks a rule of writing (the page says "N things to fix"),
"advice" is a musical point worth a look (the page still says "follows standard
notation" and lists it as "Worth a look").
