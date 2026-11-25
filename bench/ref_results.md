# Timing against real sheet music

`bench/ref_score.py` on the reference set in `samples/ref/` (sources: `bench/REFERENCES.md`).
Default options, flute alone. All beats are written quarter notes.

## Notes and timing

| piece | instr. | notes found | pitch right | extra | onset exact | onset ±16th | timing off (mean, beats) | early / late | same length | same gap to next | meter ours / real | bar = real bar | bar lines off by (q) | beat slips | tempo ratio | raw: onset in bar | raw: downbeats on bar lines |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | piano | 63.3% | 98.9% | 2.8% | 84.2% | 87.4% | 0.216 | 12.6% / 3.2% | 68.5% | 90.0% | 3/8 / 3/8 | yes | 0.0 (98.6%) | 2 | 1.0 | 86.0% | 92.1% |
| gymnopedie_1 | piano | 98.3% | 99.1% | 4.1% | 96.6% | 96.6% | 0.078 | 0.0% / 3.4% | 87.9% | 94.7% | 3/4 / 3/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 97.4% | 94.2% |
| nocturne_op9_2 | piano | 76.8% | 98.7% | 4.8% | 54.8% | 64.7% | 0.511 | 19.6% / 25.6% | 64.7% | 68.7% | 12/8 / 12/8 | yes | 0.0 (55.4%) | 19 | 1.0 | 29.5% | 43.3% |
| traumerei | piano | 49.4% | 98.8% | 3.6% | 41.8% | 44.3% | 1.051 | 38.0% / 20.3% | 58.2% | 76.0% | 4/4 / 4/4 | yes | 0.0 (43.0%) | 5 | 1.0 | 7.6% | 10.5% |
| clair_de_lune | piano | 52.6% | 85.0% | 14.6% | 40.9% | 60.4% | 0.867 | 35.2% / 23.9% | 29.6% | 43.8% | 3/4 / 9/8 | no | 0.0 (28.3%) | 16 | 2.0 | 7.5% | 55.6% |
| gavotte_bwv1006 | violin | 58.8% | 89.3% | 2.3% | 74.5% | 75.8% | 0.254 | 14.3% / 11.2% | 55.6% | 74.6% | 4/4 / 2/2 | no | 0.0 (91.7%) | 2 | 0.5 | 6.8% | 39.7% |
| menuet_bwv1006 | violin | 66.3% | 88.4% | 11.0% | 72.1% | 80.3% | 0.275 | 5.7% / 22.1% | 71.3% | 73.2% | 3/4 / 3/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 73.0% | 69.6% |
| allemanda_bwv1004 | violin | 75.6% | 100.0% | 0.2% | 73.3% | 92.4% | 0.084 | 11.2% / 15.4% | 72.4% | 88.6% | 4/4 / 4/4 | yes | 0.0 (33.3%) | 6 | 1.0 | 26.4% | 23.1% |
| schubert_ave_maria | violin | 53.7% | 64.6% | 51.7% | 20.9% | 54.2% | 1.436 | 32.7% / 46.4% | 22.9% | 12.9% | 3/4 / 4/4 | no | -0.667 (43.8%) | 8 | 2.529 (not plain) | 4.6% | 0.0% |
| air_bwv1068 | violin | 46.0% | 70.6% | 42.1% | 21.3% | 52.8% | 0.807 | 15.7% / 63.0% | 30.7% | 21.7% | 4/4 / 4/4 | no | 0.0 (89.8%) | 5 | 2.0 | 7.9% | 11.1% |
| chopin_prelude_4 | piano | 88.3% | 97.1% | 19.5% | 85.3% | 92.6% | 0.072 | 5.9% / 8.8% | 69.1% | 85.7% | 4/4 / 2/2 | yes | 0.0 (100.0%) | 0 | 1.0 | 85.3% | 91.3% |
| chopin_prelude_7 | piano | 97.9% | 100.0% | 2.1% | 95.7% | 95.7% | 0.037 | 4.3% / 0.0% | 91.5% | 93.5% | 3/4 / 3/4 | yes | 0.0 (74.5%) | 1 | 1.0 | 76.6% | 81.2% |
| chopin_prelude_20 | piano | 69.5% | 100.0% | 0.0% | 87.8% | 90.2% | 0.311 | 4.9% / 7.3% | 78.0% | 90.9% | 4/4 / 4/4 | yes | -1.0 (100.0%) | 0 | 1.0 | 4.9% | 10.0% |
| chopin_waltz_69_2 | piano | 89.7% | 99.8% | 1.4% | 94.8% | 96.0% | 0.041 | 4.1% / 1.1% | 88.2% | 94.0% | 3/4 / 3/4 | yes | 0.0 (49.6%) | 6 | 1.0 | 46.4% | 49.6% |
| kinderszenen_1 | piano | 81.8% | 97.6% | 16.2% | 66.7% | 77.8% | 0.279 | 14.8% / 18.5% | 38.3% | 49.2% | 12/8 / 2/4 | no | 0.0 (76.5%) | 3 | 1.5 | 13.6% | 31.4% |
| mozart_k545_1 | piano | 81.0% | 98.5% | 8.4% | 92.1% | 95.5% | 0.066 | 4.0% / 3.8% | 83.8% | 93.0% | 4/4 / 4/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 92.1% | 87.2% |
| moonlight_1 | piano | 83.6% | 97.1% | 25.0% | 65.7% | 67.6% | 0.665 | 24.5% / 9.8% | 53.9% | 58.2% | 3/4 / 2/2 | no | -1.0 (35.3%) | 6 | 1.0 | 21.6% | 41.2% |
| brahms_waltz_15 | piano | 66.8% | 97.7% | 0.0% | 30.4% | 57.6% | 0.492 | 35.2% / 34.4% | 8.0% | 8.3% | 4/4 / 3/4 | no | 0.0 (100.0%) | 6 | 0.375 (not plain) | 14.4% | 30.6% |
| **mean** | | 72.2% | 93.4% | 11.7% | 66.6% | 76.8% | 0.4 | 15.7% / 17.7% | 59.6% | 67.6% | | 11/18 | 10/18 right | 4.7 | 16/18 plain | 39.0% | 47.9% |

How to read it: *tempo ratio* = our quarter notes per written quarter (2 = every note value doubled, the tempo mark doubled; *not plain* = no simple ratio fits, the beat we wrote doesn't follow the music's pulse). *bar = real bar*: our bar holds as much music as the real one, after the tempo ratio. *bar lines off by*: where our bar 1 falls against the real one, in written quarters (share of notes with that offset). *beat slips*: times that offset jumps by one of our beats along the piece. *onset*: each matched note's place after those corrections (16th = 0.25). *early / late*: notes at least half a tick early or late. *raw*: no corrections at all.

## Reading the page

| piece | ties per bar, ours / score | notes shorter than an 8th, ours / score | printed tempo (quarter) | played at (quarter) | marking |
|---|---|---|---|---|---|
| fur_elise | 0.24 / 0.0 | 68.1% / 81.9% | 75.0 | 72.8 | – |
| gymnopedie_1 | 0.26 / 0.23 | 0.0% / 0.0% | 83.0 | 80.7 | – |
| nocturne_op9_2 | 1.09 / 0.03 | 37.1% / 54.7% | 58.5 | 50.3 | 66 |
| traumerei | 0.43 / 0.0 | 4.2% / 0.0% | 49.0 | 42.6 | 100 |
| clair_de_lune | 0.55 / 0.32 | 4.9% / 7.0% | 119.0 | 60.9 | – |
| gavotte_bwv1006 | 1.11 / 0.0 | 59.2% / 2.1% | 75.0 | 151.8 | – |
| menuet_bwv1006 | 0.27 / 0.0 | 12.0% / 2.2% | 115.0 | 113.8 | – |
| allemanda_bwv1004 | 0.71 / 0.0 | 85.5% / 98.7% | 45.0 | 43.7 | – |
| schubert_ave_maria | 0.68 / 0.0 | 28.9% / 69.5% | 73.0 | 50.6 | – |
| air_bwv1068 | 0.58 / 0.06 | 20.6% / 68.1% | 70.0 | 34.8 | – |
| chopin_prelude_4 | 0.19 / 0.04 | 7.6% / 6.5% | 60.0 | 59.8 | – |
| chopin_prelude_7 | 0.06 / 0.0 | 18.4% / 16.7% | 88.0 | 84.5 | – |
| chopin_prelude_20 | 0.08 / 0.0 | 7.1% / 16.9% | 38.0 | 34.6 | – |
| chopin_waltz_69_2 | 0.23 / 0.13 | 0.0% / 1.9% | 158.0 | 63.7 | – |
| kinderszenen_1 | 0.56 / 0.07 | 6.2% / 16.2% | 78.0 | 47.0 | – |
| mozart_k545_1 | 0.35 / 0.0 | 69.8% / 74.7% | 136.0 | 134.0 | – |
| moonlight_1 | 0.59 / 0.0 | 11.9% / 8.2% | 52.0 | 51.3 | – |
| brahms_waltz_15 | 0.43 / 0.0 | 63.4% / 4.3% | 37.0 | 96.6 | – |

## Listening

Our flute part (at its printed tempo) vs the reference melody (at the recording's average tempo), both synthesized as flute, and vs the original recording. DTW on chroma + onset strength finds where each of our notes lands; it is paired with the nearest note of the same pitch class there (the score's notes, or the notes heard in the recording). Deviation = how far that pair sits off a straight line through its six neighbours on each side (local tempo and offset), so a passage played slower is not an error and one note early or late is (against the recording it also holds the performer's own note-to-note rubato, which a steady flute part can't follow; against the score it is ours alone). *pitch = loudest*: our pitch class is the strongest one in the other audio there. *floor*: the score against itself played 15% slower (should be 0). *transcribed back*: Basic Pitch on our rendered flute vs our score (onset 50 ms), a check that the audio is the score.

| piece | vs score: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch = loudest | floor (ms) | vs recording: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch in top 3 | transcribed back F1 |
|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | 0.0 / 266.0 | 0.0 / 0.323 | 23.0% | 57.3% | 0.0 / 0.0 | 103.0 / 587.0 | 0.124 / 0.712 | 61.6% | 71.3% | 0.935 |
| gymnopedie_1 | 0.0 / 0.0 | 0.0 / 0.0 | 2.8% | 96.7% | 0.0 / 0.0 | 152.0 / 558.0 | 0.205 / 0.75 | 83.7% | 90.2% | 0.819 |
| nocturne_op9_2 | 104.0 / 605.0 | 0.087 / 0.507 | 57.0% | 80.7% | 0.0 / 0.0 | 182.0 / 713.0 | 0.153 / 0.597 | 87.6% | 75.0% | 0.851 |
| traumerei | 352.0 / 929.0 | 0.25 / 0.659 | 73.9% | 83.1% | 0.0 / 0.0 | 716.0 / 2882.0 | 0.508 / 2.046 | 91.0% | 79.5% | 0.869 |
| clair_de_lune | 254.0 / 1734.0 | 0.258 / 1.761 | 73.8% | 66.2% | 0.0 / 0.0 | 363.0 / 2899.0 | 0.369 / 2.944 | 86.8% | 78.5% | 0.724 |
| gavotte_bwv1006 | 0.0 / 395.0 | 0.0 / 1.0 | 32.9% | 63.3% | 0.0 / 0.0 | 52.0 / 221.0 | 0.132 / 0.559 | 51.2% | 91.8% | 0.852 |
| menuet_bwv1006 | 0.0 / 483.0 | 0.0 / 0.917 | 40.0% | 54.2% | 0.0 / 0.0 | 61.0 / 237.0 | 0.115 / 0.45 | 60.0% | 87.7% | 0.867 |
| allemanda_bwv1004 | 0.0 / 191.0 | 0.0 / 0.139 | 27.1% | 74.6% | 0.0 / 0.0 | 63.0 / 153.0 | 0.046 / 0.111 | 59.7% | 95.6% | 0.718 |
| schubert_ave_maria | 412.0 / 1621.0 | 0.347 / 1.366 | 89.0% | 39.3% | 0.0 / 0.0 | 101.0 / 479.0 | 0.085 / 0.404 | 72.0% | 92.5% | 0.614 |
| air_bwv1068 | 359.0 / 2470.0 | 0.208 / 1.433 | 91.8% | 57.9% | 0.0 / 0.0 | 312.0 / 1081.0 | 0.181 / 0.627 | 92.1% | 62.7% | 0.444 |
| chopin_prelude_4 | 0.0 / 483.0 | 0.0 / 0.481 | 23.1% | 74.7% | 0.0 / 0.0 | 836.0 / 2826.0 | 0.834 / 2.816 | 96.6% | 63.2% | 0.775 |
| chopin_prelude_7 | 0.0 / 212.0 | 0.0 / 0.298 | 21.3% | 91.7% | 0.0 / 0.0 | 69.0 / 622.0 | 0.098 / 0.877 | 61.1% | 68.8% | 0.682 |
| chopin_prelude_20 | 0.0 / 0.0 | 0.0 / 0.0 | 7.5% | 95.1% | 0.0 / 0.0 | 402.0 / 2482.0 | 0.232 / 1.433 | 81.8% | 73.2% | 0.682 |
| chopin_waltz_69_2 | 0.0 / 181.0 | 0.0 / 0.192 | 11.1% | 71.7% | 0.0 / 0.0 | 33.0 / 191.0 | 0.035 / 0.203 | 39.7% | 77.0% | 0.856 |
| kinderszenen_1 | 106.0 / 726.0 | 0.083 / 0.569 | 51.8% | 82.8% | 0.0 / 0.0 | 103.0 / 457.0 | 0.081 / 0.358 | 74.7% | 89.9% | 0.607 |
| mozart_k545_1 | 0.0 / 224.0 | 0.0 / 0.5 | 17.4% | 47.8% | 0.0 / 0.0 | 23.0 / 216.0 | 0.051 / 0.482 | 33.0% | 80.0% | 0.923 |
| moonlight_1 | 362.0 / 1609.0 | 0.31 / 1.376 | 69.0% | 72.1% | 0.0 / 0.0 | 405.0 / 2259.0 | 0.346 / 1.932 | 89.3% | 88.6% | 0.678 |
| brahms_waltz_15 | 155.0 / 487.0 | 0.25 / 0.783 | 80.7% | 65.6% | 0.0 / 0.0 | 161.0 / 777.0 | 0.258 / 1.25 | 87.1% | 85.9% | 0.798 |
| **mean** | 116.9 / 700.9 | | 44.1% | 70.8% | 0.0 / 0.0 | 229.8 / 1091.1 | | 72.7% | 80.6% | 0.8 |

## Against a published flute arrangement

| piece | notes found | onset exact | timing off (beats) | same length |
|---|---|---|---|---|
| air_bwv1068 | 45.7% | 21.3% | 0.807 | 30.7% |

## Largest onset errors (after tempo and bar-line correction)

- **fur_elise**: commonest errors (quarters: count) {'-1': 7, '+0.5': 5, '-0.5': 4, '-0.0833': 4}; A5 written bar 55 beat 1.5 -> ours -4.50 q; G5 written bar 55 beat 1.625 -> ours -4.12 q; G5 written bar 55 beat 2.125 -> ours -4.12 q
- **gymnopedie_1**: commonest errors (quarters: count) {'+1': 2, '+4': 1, '+3': 1}; F#4 written bar 48 beat 1 -> ours +4.00 q; D5 written bar 78 beat 1 -> ours +3.00 q; G5 written bar 71 beat 1 -> ours +1.00 q
- **nocturne_op9_2**: commonest errors (quarters: count) {'+0.5': 29, '-0.5': 21, '+0.25': 13, '-0.25': 9}; C7 written bar 34 beat 2.5 -> ours -12.50 q; B6 written bar 34 beat 3 -> ours -12.00 q; C7 written bar 35 beat 2.75 -> ours -9.00 q
- **traumerei**: commonest errors (quarters: count) {'-0.5': 17, '+2': 4, '-1': 4, '+0.5': 3}; F4 written bar 25 beat 1 -> ours -6.50 q; C5 written bar 31 beat 1 -> ours +6.00 q; F5 written bar 30 beat 4 -> ours +6.00 q
- **clair_de_lune**: commonest errors (quarters: count) {'-0.25': 18, '+0.25': 13, '-2.5': 6, '+0.5': 5}; F4 written bar 7 beat 4.5 -> ours +7.50 q; Ab5 written bar 23 beat 2.5 -> ours -4.00 q; Bb5 written bar 24 beat 1.5 -> ours +3.75 q
- **gavotte_bwv1006**: commonest errors (quarters: count) {'-1': 20, '-0.5': 19, '+0.5': 18, '+1': 13}; E5 written bar 56 beat 4.5 -> ours -3.50 q; E5 written bar 36 beat 4 -> ours -3.00 q; A4 written bar 105 beat 1 -> ours +3.00 q
- **menuet_bwv1006**: commonest errors (quarters: count) {'+0.5': 9, '+0.25': 6, '-0.25': 4, '+2': 4}; B5 written bar 19 beat 2 -> ours +3.00 q; B4 written bar 41 beat 2 -> ours +2.50 q; Eb5 written bar 41 beat 3 -> ours +2.50 q
- **allemanda_bwv1004**: commonest errors (quarters: count) {'+0.25': 36, '-0.25': 22, '+0.5': 15, '-0.5': 11}; D5 written bar 32 beat 3 -> ours +1.75 q; D5 written bar 29 beat 1.5 -> ours -1.00 q; A5 written bar 16 beat 3 -> ours +1.00 q
- **schubert_ave_maria**: commonest errors (quarters: count) {'+0.0833': 14, '+0.167': 12, '-0.0833': 8, '+0.333': 6}; A4 written bar 17 beat 4.75 -> ours -20.42 q; A4 written bar 17 beat 4.25 -> ours -20.08 q; Bb4 written bar 18 beat 1 -> ours -19.00 q
- **air_bwv1068**: commonest errors (quarters: count) {'+0.25': 26, '+0.125': 10, '+1': 7, '+0.75': 7}; C#5 written bar 25 beat 2.375 -> ours -3.88 q; B4 written bar 25 beat 2.5 -> ours -3.75 q; C#5 written bar 25 beat 2.625 -> ours -3.62 q
- **chopin_prelude_4**: commonest errors (quarters: count) {'+1': 3, '-0.25': 1, '-0.0833': 1, '-0.167': 1}; Eb4 written bar 24 beat 3 -> ours +1.00 q; E4 written bar 23 beat 1 -> ours +1.00 q; E4 written bar 24 beat 1 -> ours +1.00 q
- **chopin_prelude_7**: commonest errors (quarters: count) {'-0.75': 1, '-1': 1}; A5 written bar 16 beat 1 -> ours -1.00 q; A5 written bar 15 beat 3 -> ours -0.75 q
- **chopin_prelude_20**: commonest errors (quarters: count) {'-3': 1, '-8': 1, '+0.5': 1, '+0.25': 1}; Eb4 written bar 3 beat 4 -> ours -8.00 q; F4 written bar 2 beat 2 -> ours -3.00 q; C5 written bar 13 beat 1 -> ours +1.00 q
- **chopin_waltz_69_2**: commonest errors (quarters: count) {'-0.5': 11, '-0.25': 8, '-0.75': 5, '-1': 2}; D5 written bar 63 beat 2.5 -> ours +2.50 q; D5 written bar 31 beat 2.5 -> ours +2.50 q; B4 written bar 63 beat 2 -> ours +2.00 q
- **kinderszenen_1**: commonest errors (quarters: count) {'-0.0833': 5, '+0.667': 4, '+0.333': 3, '-2': 2}; E4 written bar 34 beat 1 -> ours +3.00 q; A4 written bar 32 beat 2 -> ours -2.00 q; B4 written bar 15 beat 2 -> ours +2.00 q
- **mozart_k545_1**: commonest errors (quarters: count) {'+0.25': 10, '-0.25': 6, '+1': 3, '-1': 3}; E4 written bar 54 beat 1.5 -> ours -4.50 q; F5 written bar 52 beat 4 -> ours -3.00 q; D4 written bar 34 beat 2.5 -> ours -1.50 q
- **moonlight_1**: commonest errors (quarters: count) {'-0.5': 7, '-2': 3, '+0.5': 3, '+1.5': 2}; C3 written bar 38 beat 1 -> ours -7.00 q; C#3 written bar 37 beat 4 -> ours -7.00 q; Eb3 written bar 37 beat 3.3333 -> ours -6.58 q
- **brahms_waltz_15**: commonest errors (quarters: count) {'+0.25': 18, '-0.25': 15, '-0.5': 14, '-0.75': 9}; Ab5 written bar 44 beat 1 -> ours +3.00 q; Bb5 written bar 43 beat 3 -> ours +2.50 q; C5 written bar 9 beat 3.5 -> ours -2.50 q
