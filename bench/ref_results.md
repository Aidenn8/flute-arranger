# Timing against real sheet music

`bench/ref_score.py` on the reference set in `samples/ref/` (sources: `bench/REFERENCES.md`).
Default options, flute alone. All beats are written quarter notes.

## Notes and timing

| piece | instr. | notes found | pitch right | extra | onset exact | onset ±16th | timing off (mean, beats) | early / late | same length | same gap to next | meter ours / real | bar = real bar | bar lines off by (q) | beat slips | tempo ratio | raw: onset in bar | raw: downbeats on bar lines |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | piano | 42.5% | 99.2% | 0.8% | 65.4% | 74.8% | 0.423 | 26.5% / 8.1% | 54.7% | 83.8% | 3/8 / 3/8 | yes | 0.0 (57.3%) | 7 | 1.0 | 43.6% | 49.2% |
| gymnopedie_1 | piano | 98.3% | 99.1% | 4.1% | 89.7% | 92.2% | 0.17 | 5.2% / 5.2% | 77.6% | 80.5% | 4/4 / 3/4 | no | 1.0 (40.5%) | 7 | 1.0 | 28.4% | 28.8% |
| nocturne_op9_2 | piano | 73.2% | 99.0% | 6.2% | 33.0% | 59.9% | 0.543 | 39.1% / 27.9% | 36.0% | 40.7% | 4/4 / 12/8 | no | -1.0 (15.8%) | 24 | 2.4 (not plain) | 6.7% | 14.8% |
| traumerei | piano | 45.6% | 98.6% | 3.9% | 38.4% | 57.5% | 0.663 | 37.0% / 24.7% | 35.6% | 39.5% | 4/4 / 4/4 | no | 2.0 (32.9%) | 6 | 2.0 | 8.2% | 21.4% |
| clair_de_lune | piano | 57.6% | 95.6% | 7.6% | 44.3% | 60.9% | 0.642 | 34.5% / 21.3% | 32.8% | 48.6% | 4/4 / 9/8 | no | 1.5 (41.4%) | 19 | 2.0 | 8.0% | 23.5% |
| gavotte_bwv1006 | violin | 62.3% | 82.8% | 11.5% | 35.5% | 57.1% | 0.49 | 28.9% / 35.5% | 32.1% | 36.4% | 4/4 / 2/2 | yes | 0.0 (50.2%) | 19 | 1.0 | 20.8% | 27.4% |
| menuet_bwv1006 | violin | 66.3% | 83.6% | 17.0% | 58.2% | 79.5% | 0.324 | 17.2% / 24.6% | 50.0% | 57.3% | 3/4 / 3/4 | yes | -1.0 (49.2%) | 2 | 1.0 | 25.4% | 37.5% |
| allemanda_bwv1004 | violin | 82.1% | 97.2% | 4.2% | 22.5% | 82.2% | 0.211 | 31.5% / 45.9% | 50.2% | 52.0% | 4/4 / 4/4 | no | 0.0 (21.8%) | 30 | 2.353 (not plain) | 6.3% | 15.4% |
| schubert_ave_maria | violin | 41.8% | 53.8% | 68.2% | 9.2% | 20.2% | 1.559 | 41.2% / 49.6% | 12.6% | 9.1% | 4/4 / 4/4 | no | 0.5 (21.8%) | 29 | 2.312 (not plain) | 4.2% | 7.7% |
| air_bwv1068 | violin | 46.4% | 66.3% | 53.5% | 13.3% | 50.8% | 0.738 | 39.8% / 46.9% | 15.6% | 10.8% | 4/4 / 4/4 | no | 2.0 (23.4%) | 17 | 3.882 | 6.2% | 5.3% |
| **mean** | | 61.6% | 87.5% | 17.7% | 41.0% | 63.5% | 0.6 | 30.1% / 29.0% | 39.7% | 45.9% | | 3/10 | 2/10 right | 16.0 | 7/10 plain | 15.8% | 23.1% |

How to read it: *tempo ratio* = our quarter notes per written quarter (2 = every note value doubled, the tempo mark doubled; *not plain* = no simple ratio fits, the beat we wrote doesn't follow the music's pulse). *bar = real bar*: our bar holds as much music as the real one, after the tempo ratio. *bar lines off by*: where our bar 1 falls against the real one, in written quarters (share of notes with that offset). *beat slips*: times that offset jumps by one of our beats along the piece. *onset*: each matched note's place after those corrections (16th = 0.25). *early / late*: notes at least half a tick early or late. *raw*: no corrections at all.

## Reading the page

| piece | ties per bar, ours / score | notes shorter than an 8th, ours / score | printed tempo (quarter) | played at (quarter) | marking |
|---|---|---|---|---|---|
| fur_elise | 0.44 / 0.0 | 52.4% / 81.9% | 75.0 | 72.7 | – |
| gymnopedie_1 | 0.49 / 0.23 | 3.3% / 0.0% | 87.0 | 80.7 | – |
| nocturne_op9_2 | 1.01 / 0.03 | 16.7% / 54.7% | 120.0 | 50.0 | 66 |
| traumerei | 0.77 / 0.0 | 18.4% / 0.0% | 90.0 | 42.2 | 100 |
| clair_de_lune | 0.8 / 0.32 | 10.6% / 7.0% | 129.0 | 65.8 | – |
| gavotte_bwv1006 | 1.09 / 0.0 | 37.0% / 2.1% | 154.0 | 152.0 | – |
| menuet_bwv1006 | 0.43 / 0.0 | 34.4% / 2.2% | 116.0 | 113.8 | – |
| allemanda_bwv1004 | 1.17 / 0.0 | 32.2% / 98.7% | 104.0 | 43.7 | – |
| schubert_ave_maria | 1.2 / 0.0 | 38.0% / 69.5% | 152.0 | 66.0 | – |
| air_bwv1068 | 1.4 / 0.06 | 35.0% / 68.1% | 132.0 | 34.8 | – |

## Listening

Our flute part (at its printed tempo) vs the reference melody (at the recording's average tempo), both synthesized as flute, and vs the original recording. DTW on chroma + onset strength finds where each of our notes lands; it is paired with the nearest note of the same pitch class there (the score's notes, or the notes heard in the recording). Deviation = how far that pair sits off a straight line through its six neighbours on each side (local tempo and offset), so a passage played slower is not an error and one note early or late is (against the recording it also holds the performer's own note-to-note rubato, which a steady flute part can't follow; against the score it is ours alone). *pitch = loudest*: our pitch class is the strongest one in the other audio there. *floor*: the score against itself played 15% slower (should be 0). *transcribed back*: Basic Pitch on our rendered flute vs our score (onset 50 ms), a check that the audio is the score.

| piece | vs score: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch = loudest | floor (ms) | vs recording: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch in top 3 | transcribed back F1 |
|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | 0.0 / 413.0 | 0.0 / 0.5 | 39.1% | 57.6% | 0.0 / 0.0 | 228.0 / 964.0 | 0.276 / 1.168 | 79.4% | 65.5% | 0.862 |
| gymnopedie_1 | 0.0 / 341.0 | 0.0 / 0.458 | 42.3% | 95.1% | 0.0 / 0.0 | 71.0 / 267.0 | 0.095 / 0.36 | 60.8% | 93.4% | 0.804 |
| nocturne_op9_2 | 191.0 / 532.0 | 0.159 / 0.444 | 83.8% | 77.5% | 0.0 / 0.0 | 119.0 / 910.0 | 0.099 / 0.759 | 71.6% | 78.1% | 0.815 |
| traumerei | 342.0 / 1130.0 | 0.24 / 0.795 | 81.2% | 81.8% | 0.0 / 0.0 | 657.0 / 4372.0 | 0.462 / 3.075 | 90.4% | 76.6% | 0.797 |
| clair_de_lune | 233.0 / 2494.0 | 0.256 / 2.734 | 85.0% | 66.5% | 0.0 / 0.0 | 259.0 / 2231.0 | 0.284 / 2.446 | 80.6% | 81.2% | 0.742 |
| gavotte_bwv1006 | 79.0 / 253.0 | 0.201 / 0.642 | 59.9% | 58.0% | 0.0 / 0.0 | 54.0 / 233.0 | 0.137 / 0.591 | 52.8% | 86.0% | 0.801 |
| menuet_bwv1006 | 96.0 / 309.0 | 0.182 / 0.586 | 58.8% | 58.5% | 0.0 / 0.0 | 45.0 / 282.0 | 0.085 / 0.534 | 45.6% | 78.4% | 0.8 |
| allemanda_bwv1004 | 78.0 / 195.0 | 0.057 / 0.142 | 64.0% | 78.2% | 0.0 / 0.0 | 37.0 / 94.0 | 0.027 / 0.068 | 37.7% | 94.8% | 0.727 |
| schubert_ave_maria | 198.0 / 1144.0 | 0.218 / 1.259 | 66.8% | 46.9% | 0.0 / 0.0 | 87.0 / 386.0 | 0.096 / 0.425 | 68.4% | 91.4% | 0.568 |
| air_bwv1068 | 249.0 / 1316.0 | 0.144 / 0.763 | 84.9% | 63.1% | 0.0 / 0.0 | 252.0 / 1074.0 | 0.146 / 0.623 | 82.6% | 64.1% | 0.457 |
| **mean** | 146.6 / 812.7 | | 66.6% | 68.3% | 0.0 / 0.0 | 180.9 / 1081.3 | | 67.0% | 81.0% | 0.7 |

## Against a published flute arrangement

| piece | notes found | onset exact | timing off (beats) | same length |
|---|---|---|---|---|
| air_bwv1068 | 46.0% | 13.3% | 0.738 | 15.6% |

## Largest onset errors (after tempo and bar-line correction)

- **fur_elise**: commonest errors (quarters: count) {'-1': 16, '+0.25': 13, '-1.25': 8, '-0.25': 6}; G5 written bar 55 beat 2.125 -> ours -4.88 q; G5 written bar 55 beat 1.625 -> ours -4.62 q; A4 written bar 99 beat 1.5 -> ours -3.50 q
- **gymnopedie_1**: commonest errors (quarters: count) {'-0.25': 3, '+1': 2, '+4': 2, '-5': 1}; D5 written bar 39 beat 1 -> ours -5.00 q; F#4 written bar 48 beat 1 -> ours +4.00 q; D5 written bar 78 beat 1 -> ours +4.00 q
- **nocturne_op9_2**: commonest errors (quarters: count) {'-0.125': 34, '-0.5': 23, '+0.5': 16, '-0.25': 16}; B6 written bar 33 beat 4 -> ours -8.00 q; C7 written bar 33 beat 2.5 -> ours -8.00 q; C7 written bar 35 beat 1.5 -> ours -6.00 q
- **traumerei**: commonest errors (quarters: count) {'-0.5': 8, '-0.125': 6, '-0.25': 4, '+0.25': 2}; C5 written bar 30 beat 1 -> ours +4.50 q; C5 written bar 10 beat 1 -> ours -3.50 q; D5 written bar 27 beat 3.5 -> ours +3.50 q
- **clair_de_lune**: commonest errors (quarters: count) {'+0.25': 13, '+0.5': 10, '-0.25': 8, '-0.5': 7}; Ab5 written bar 20 beat 3.5 -> ours +5.62 q; C#6 written bar 21 beat 3.25 -> ours +4.25 q; Ab4 written bar 68 beat 2 -> ours -3.75 q
- **gavotte_bwv1006**: commonest errors (quarters: count) {'+0.5': 45, '-0.25': 42, '+0.25': 38, '-0.5': 27}; B4 written bar 91 beat 3.5 -> ours -7.50 q; Ab4 written bar 91 beat 3 -> ours -4.50 q; B4 written bar 93 beat 3.5 -> ours +4.00 q
- **menuet_bwv1006**: commonest errors (quarters: count) {'-0.25': 15, '+0.25': 9, '+0.5': 6, '+2': 5}; B5 written bar 19 beat 1 -> ours +3.25 q; B5 written bar 19 beat 2 -> ours +3.00 q; B5 written bar 19 beat 3 -> ours +2.33 q
- **allemanda_bwv1004**: commonest errors (quarters: count) {'+0.25': 91, '+0.125': 67, '-0.25': 55, '-0.125': 25}; D5 written bar 32 beat 3 -> ours +2.00 q; E5 written bar 16 beat 4.75 -> ours +2.00 q; D5 written bar 29 beat 1.5 -> ours -1.25 q
- **schubert_ave_maria**: commonest errors (quarters: count) {'+1': 6, '+0.375': 4, '-0.5': 4, '-1': 3}; Bb4 written bar 32 beat 3.75 -> ours +7.88 q; C5 written bar 32 beat 3 -> ours +7.50 q; C5 written bar 32 beat 4.1667 -> ours +6.83 q
- **air_bwv1068**: commonest errors (quarters: count) {'-0.0625': 13, '-0.125': 8, '-0.25': 7, '+0.125': 7}; F#5 written bar 26 beat 2.75 -> ours -4.00 q; C#5 written bar 25 beat 2.375 -> ours -3.75 q; F#5 written bar 1 beat 1 -> ours +3.62 q
