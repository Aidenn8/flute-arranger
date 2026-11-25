# Reference set: real recordings + real sheet music

Used by `bench/ref_score.py` to judge the flute part's timing against the printed
music, not against our own guesses. Built by `bench/make_refs.py` into
`samples/ref/<piece>/` (git-ignored; run the script to rebuild it):

```sh
.venv/bin/python -I bench/make_refs.py              # download what is missing, write ref.json
.venv/bin/python -I bench/make_refs.py --no-fetch --show fur_elise   # print the first bars
```

Each piece folder holds one folder per download (`rec/` recording, `score/` the
Mutopia Project MIDI + LilyPond source + preview image, `flute/` a published flute
arrangement) with a `source.json`, and the files written from them:

- `ref.json`: the melody a flutist would play. `notes` = `{pitch (MIDI), onset_beats,
  dur_beats}` in **quarter notes from the first downbeat** (negative = pickup), plus
  `meter`, `bar_beats` (bar length in quarters), `pickup_beats`, `key`,
  `bpm_marking` (quarter notes per minute, only when the composer printed one),
  `tempo_text`, the `form` played and how many grace notes were left out.
- `ref_flute.json`: the same from the flute arrangement, where one exists.

## How the melody is taken from the score

The Mutopia MIDI files are written by LilyPond from the engraved source, one track per
staff, timed in ticks (so the tempo changes some editors add for playback don't
matter). For piano the right-hand staff, for violin the violin staff (for Schubert the
sung line, which the violin plays). Then:

1. **Top line**: at each onset the highest new note, unless a note at least as high is
   still held (an inner voice under a held tune note); each note ends where the next
   begins at the latest.
2. **Grace notes left out** (LilyPond plays them as tiny off-grid notes stealing time
   from the note before; that time is given back). Trills are not written out.
3. **Form**: the repeats the performer actually plays, checked against each recording
   (the length, and which way of unfolding the repeats matches the transcribed notes
   best; `--show` and the comments in `make_refs.py` say what was chosen).
4. Hand fixes, all in `PIECES` in `make_refs.py`: Satie's chords-only intro left out and
   a long tied F#4 rejoined (the chords strike the same key, which cuts the MIDI note);
   in Moonlight the right hand's 8th-triplets (the accompaniment, written in the same
   staff as the tune) are left out by their note value (`drop_len`), which leaves
   Beethoven's separate top voice.

The first bars of every reference were read against the LilyPond source by hand.

## The pieces

| piece | instrument | recording (Wikimedia Commons) | license | length | score (Mutopia Project) | score license | form played |
|---|---|---|---|---|---|---|---|
| `fur_elise` | piano | [For Elise, JMC Han](https://commons.wikimedia.org/wiki/File:For_Elise_(F%C3%BCr_Elise)_Beethoven_JMC_Han.ogg) (reused from `samples/real/`) | CC BY-SA 4.0 | 160 s | [Für Elise WoO 59](https://www.mutopiaproject.org/ftp/BeethovenLv/WoO59/fur_Elise_WoO59/), after Breitkopf & Härtel 1888 | public domain | both repeats |
| `gymnopedie_1` | piano | [Gymnopédie No. 1, Robin Alciatore](https://commons.wikimedia.org/wiki/File:Erik_Satie_-_gymnopedies_-_la_1_ere._lent_et_douloureux.ogg) (reused) | public domain | 184 s | [Gymnopédie No. 1](https://www.mutopiaproject.org/ftp/SatieE/gymnopedie_1/), Dover | public domain | both halves (Satie's 1st/2nd ending) |
| `nocturne_op9_2` | piano | [Nocturne Op. 9 No. 2, Frank Levy](https://commons.wikimedia.org/wiki/File:Nocturne_in_E_flat_major,_Op._9_no._2.mp3) (reused) | public domain | 271 s | [Op. 9 No. 2](https://www.mutopiaproject.org/ftp/ChopinFF/O9/chopin_nocturne_op9_n2/), after Schirmer 1881 | CC BY-SA 3.0 | as written (no repeats) |
| `traumerei` | piano | [Scenes from Childhood, No. 7](https://commons.wikimedia.org/wiki/File:Robert_Schumann_-_scenes_from_childhood,_op._15_-_vii._dreaming.ogg), Musopen | public domain | 203 s | [Op. 15 No. 7](https://www.mutopiaproject.org/ftp/SchumannR/O15/SchumannOp15No07/) | public domain | bars 1-8 repeated |
| `clair_de_lune` | piano | [Clair de lune, Laurens Goedhart (2011)](https://commons.wikimedia.org/wiki/File:Clair_de_lune_(Claude_Debussy)_Suite_bergamasque.ogg) | public domain | 304 s | [Suite bergamasque: Clair de lune](https://www.mutopiaproject.org/ftp/DebussyC/L75/debussy_Ste_Bergamesq_Clair/), after Fromont 1905 | public domain | as written |
| `gavotte_bwv1006` | violin solo | [Partita No. 3, Gavotte en rondeau, Vicond](https://commons.wikimedia.org/wiki/File:J.S.Bach_partita_No.3_Gavotte_in_Rondeau.ogg) | CC BY 3.0 | 176 s | [BWV 1006, Gavotte](https://www.mutopiaproject.org/ftp/BachJS/BWV1006/bwv-1006_3/), after Bach-Gesellschaft | CC BY-SA 3.0 | refrain repeated |
| `menuet_bwv1006` | violin solo | [Partita No. 3, Minuet 1, Vicond](https://commons.wikimedia.org/wiki/File:Partita_No.3_J.S.Bach_(Minuet_1).ogg) | CC BY 3.0 | 68 s | [BWV 1006, Menuet I](https://www.mutopiaproject.org/ftp/BachJS/BWV1006/bwv-1006_4/) | CC BY-SA 3.0 | bars 1-8 repeated, 9-34 once |
| `allemanda_bwv1004` | violin solo | [Bach Partita 2 - Allemanda (2013)](https://commons.wikimedia.org/wiki/File:Bach_Partita_2_-_Allemanda_-_18-04-2013.ogg), QMUL | CC0 | 182 s | [BWV 1004, Allemanda](https://www.mutopiaproject.org/ftp/BachJS/BWV1004/bwv-1004_1/) | CC BY-SA 3.0 | no repeats |
| `schubert_ave_maria` | violin + piano | [Ave Maria arr. violin and piano, Menuhin / Baller (1948 film)](https://commons.wikimedia.org/wiki/File:Schubert_-_Ave_Maria,_arr._for_violin_and_piano_(Menuhin,_Baller).flac) | public domain | 263 s | [D. 839](https://www.mutopiaproject.org/ftp/SchubertF/D839/SchubertF-D839_AveMaria/) (voice + piano), after Peters | public domain | all three verses |
| `air_bwv1068` | strings (1st violins) | [Air, Air Force Strings (2000)](https://commons.wikimedia.org/wiki/File:Air_-_Air_Force_Strings_-_United_States_Air_Force_Band.mp3), US government work | public domain | 183 s | [BWV 1068, Air (orchestra)](https://www.mutopiaproject.org/ftp/BachJS/BWV1068/bach-air/), after Bach-Gesellschaft | CC BY-SA 3.0 | both halves repeated |
| `chopin_prelude_4` | piano | [Prelude Op. 28 No. 4, Ivan Ilić](https://commons.wikimedia.org/wiki/File:Ivan_Ili%C4%87-Chopin_Prelude_Opus_28_n.4.ogg) (from IMSLP) | CC BY 3.0 | 110 s | [Op. 28 No. 4](https://www.mutopiaproject.org/ftp/ChopinFF/O28/Chop-28-4/), after Peters 1879 | public domain | as written |
| `chopin_prelude_7` | piano | [Prelude No. 7, Ivan Ilić](https://commons.wikimedia.org/wiki/File:Ivan_Ili%C4%87-Chopin_-_Prelude_no._7_in_A_major.ogg) (from IMSLP) | CC BY-SA 3.0 | 38 s | [Op. 28 No. 7](https://www.mutopiaproject.org/ftp/ChopinFF/O28/Chop-28-7/), after Peters | public domain | as written |
| `chopin_prelude_20` | piano | [Preludes Op. 28 No. 20, Musopen](https://commons.wikimedia.org/wiki/File:Chopin_-_Preludes,_Op._28_-_No._20_%27Funeral_march%27.ogg) | CC BY 3.0 | 91 s | [Op. 28 No. 20](https://www.mutopiaproject.org/ftp/ChopinFF/O28/Chop-28-20/), after Peters | public domain | as written |
| `chopin_waltz_69_2` | piano | [Waltz Op. 69 No. 2, Olga Gurevich](https://commons.wikimedia.org/wiki/File:Chopin_-_Waltz_No._10_in_B_minor,_Op._69_No._2_(Olga_Gurevich).flac) (Musopen) | public domain | 216 s | [Op. 69 No. 2](https://www.mutopiaproject.org/ftp/ChopinFF/O69/w10-h-moll-cfi/), after Peters (Scholtz) | public domain | B section repeated |
| `kinderszenen_1` | piano | [Scenes from Childhood, No. 1](https://commons.wikimedia.org/wiki/File:Robert_Schumann_-_scenes_from_childhood,_op._15_-_i._of_foreign_lands_and_peoples.ogg), Musopen | public domain | 112 s | [Op. 15 No. 1](https://www.mutopiaproject.org/ftp/SchumannR/O15/SchumannOp15No01/), after "Leichte Stücke" 1900 | public domain | both halves repeated |
| `mozart_k545_1` | piano | [Sonata K. 545, i. Allegro](https://commons.wikimedia.org/wiki/File:Wolfgang_Amadeus_Mozart_-_sonata_no._16_in_c_major,_k.545_%27sonata_facile%27_-_i._allegro.ogg), Musopen | public domain | 134 s | [K. 545, i](https://www.mutopiaproject.org/ftp/MozartWA/KV545/K545-1/) | CC BY-SA 3.0 | no repeats |
| `moonlight_1` | piano | [Sonata Op. 27 No. 2, i, Paul Pitman](https://commons.wikimedia.org/wiki/File:Ludwig_van_Beethoven_-_sonata_no._14_in_c_sharp_minor_%27moonlight%27,_op._27_no._2_-_i._adagio_sostenuto.ogg) (Musopen) | public domain | 336 s | [Op. 27 No. 2](https://www.mutopiaproject.org/ftp/BeethovenLv/O27/moonlight/), after Berners 1908 (ed. Winterberger) | CC BY-SA 2.5 | as written |
| `brahms_waltz_15` | piano | [Waltz Op. 39 No. 15, Martha Goldstein](https://commons.wikimedia.org/wiki/File:Brahms-waltz15.ogg) | CC BY-SA 2.0 | 88 s | [Op. 39 No. 15](https://www.mutopiaproject.org/ftp/BrahmsJ/O39/waltz-op39-15/), ed. R. Joseffy | public domain | as written (the edition writes its repeats out) |

Notes per piece:

- **Für Elise**: the whole right hand, including the broken-chord notes (A4 then
  C4-E4-A4): every flute version keeps them, and the existing `bench/music_audit.py`
  tune does too. Mutopia's MIDI is "Poco moto" at the typesetter's 72 per quarter; no
  composer's marking.
- **Gymnopédie**: the tune begins in bar 5; Satie's chords in bars 1-4 are not part of
  it. Bars 9-12 are one F#4 held for 12 beats.
- **Nocturne**: 12/8; the Mutopia edition prints Chopin's "eighth = 132" (66 per
  quarter). The fioriture are kept; grace notes are not. Bar 10's four repeated E-flats
  are as the edition's MIDI has them (four 8ths).
- **Träumerei**: Schumann's own marking is quarter = 100 (Mutopia's MIDI slows it to 56
  and lower for playback; ticks are used, so that doesn't matter).
- **Clair de lune**: 9/8, with duplets (two dotted 8ths in a beat): a good rhythm test.
- **Allemanda**: the Commons description says "1st Partita"; the notes are the
  Allemanda of Partita No. 2 (D minor, BWV 1004), as the file name says. Played
  without the repeats (the second half starts at 91 s of 182 s).
- **Schubert**: Menuhin plays the sung line; the score's voice part (three verses, in
  the MIDI after one quarter of silence) is the reference.
- **Air**: the recording is a string orchestra; the tune is the first violins.
- **The eight piano pieces added later** (2/2, 2/4, 3/4, 4/4; melody in chords, over
  triplets, in a waltz, over an Alberti bass) were chosen to widen the judge for piano
  recordings. Each repeat form was chosen as the best match of the ways to play it (DP
  alignment of the reference's pitch classes against the transcribed tune, and the
  tempo that implies): Kinderszenen 1 with both halves repeated (48 quarters a minute;
  without repeats it would be 24), the B-minor waltz with its B section played twice
  (149 quarters a minute against the edition's 152; as written 123), K. 545 without
  the repeats (with the exposition repeated the match is far worse).
- **Chopin Prelude No. 4**: the right hand from the edition's pickup (B3 dotted 8th, B4
  16th); the closing chords' top notes are the tune.
- **Chopin Prelude No. 7**: the tune is the top of the right hand's chords; the edition
  ships its MIDI file next to a zip of LilyPond files (both are read).
- **Chopin Prelude No. 20**: the top line of the right hand's chords. Commons' other
  file of this prelude ("Prelude no. 20 op. 28 c minor.ogg") is a MIDI file played by a
  soundfont, not a performance, so the Musopen recording is used.
- **Moonlight**: the tune is Beethoven's separate top voice (bars 5 on; LilyPond's
  `topmain`); the right hand's 8th-triplets are left out (see hand fixes). Where the
  tune rests (bars 1-4, much of the middle section) the reference has no notes.
- **Brahms Op. 39 No. 15**: the top line of the right hand; in the closing bars that
  includes the right hand's triplet figures under the held tune note.

Searched but not used: Massenet's Méditation, Elgar's Salut d'amour, Dvořák's
Humoresque, Monti's Czárdás and Gluck's "Melody" have free recordings on Commons
but no machine-readable score on Mutopia; the Bach-Gounod Ave Maria (Mutopia has
piano + violin) has no free recording I could confirm is violin-led (the Internet
Archive 78s found are sung with a violin obbligato, or from 1930 and later).

## Flute arrangements

Only one of the eighteen has a machine-readable published flute arrangement: Mutopia's
**Air from BWV 1068 for flute and guitar** (public domain, "new adaptation from
Bach-Gesellschaft"), in `air_bwv1068/flute/`, as `ref_flute.json`. Mutopia's whole
flute list (51 pieces) has nothing else from this set; KernScores and the music21
corpus have no flute versions of these pieces; the flute arrangements on IMSLP are
scanned PDFs, not data.

What that arranger did with the violin line, compared note by note:

| | first violin (`ref.json`) | flute arrangement (`ref_flute.json`) |
|---|---|---|
| key | D major | D major (unchanged) |
| octave / range | A4-C6 | A4-C6 (unchanged: it already sits in the flute's best range) |
| rhythm | as Bach wrote | identical, note for note, including ties over the bar lines |
| ornaments | grace F#5 before the E5 in the edition's bar 13 (played twice) | that grace written out as two 32nds (F#5, E5) in the 16th's place |
| form | both halves repeated | the same repeats |

So for a violin melody that already lies well for the flute, the published flute
version keeps key, octave and every rhythm, and only writes an ornament out in real
note values. The flute part we write should be judged the same way: same octave
(or one octave move), same note values, same bar lines.
