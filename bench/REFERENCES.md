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
   a long tied F#4 rejoined (the chords strike the same key, which cuts the MIDI note).

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

Searched but not used: Massenet's Méditation, Elgar's Salut d'amour, Dvořák's
Humoresque, Monti's Czárdás and Gluck's "Melody" have free recordings on Commons
but no machine-readable score on Mutopia; the Bach-Gounod Ave Maria (Mutopia has
piano + violin) has no free recording I could confirm is violin-led (the Internet
Archive 78s found are sung with a violin obbligato, or from 1930 and later).

## Flute arrangements

Only one of the ten has a machine-readable published flute arrangement: Mutopia's
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
