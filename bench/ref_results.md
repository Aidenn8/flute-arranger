# Timing against real sheet music

`bench/ref_score.py` on the reference set in `samples/ref/` (sources: `bench/REFERENCES.md`).
Default options, flute alone. All beats are written quarter notes.

## Notes and timing

| piece | instr. | notes found | pitch right | extra | onset exact | onset ±16th | timing off (mean, beats) | early / late | same length | same gap to next | meter ours / real | bar = real bar | bar lines off by (q) | beat slips | tempo ratio | raw: onset in bar | raw: downbeats on bar lines |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | piano | 42.1% | 98.7% | 0.8% | 74.1% | 76.7% | 0.358 | 23.7% / 2.2% | 55.2% | 84.5% | 3/8 / 3/8 | yes | 0.0 (98.7%) | 2 | 1.0 | 76.3% | 87.3% |
| gymnopedie_1 | piano | 98.3% | 99.1% | 4.1% | 96.6% | 96.6% | 0.078 | 0.0% / 3.4% | 87.1% | 94.7% | 3/4 / 3/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 97.4% | 94.2% |
| nocturne_op9_2 | piano | 70.2% | 98.6% | 4.3% | 52.6% | 67.4% | 0.529 | 20.7% / 26.7% | 57.2% | 63.8% | 12/8 / 12/8 | yes | 0.0 (58.6%) | 12 | 1.059 (not plain) | 29.1% | 46.4% |
| traumerei | piano | 45.6% | 97.3% | 2.6% | 1.4% | 12.3% | 2.046 | 37.0% / 61.6% | 5.5% | 7.3% | 3/4 / 4/4 | yes | 0.0 (46.6%) | 8 | 0.833 (not plain) | 9.6% | 16.7% |
| clair_de_lune | piano | 50.0% | 85.8% | 11.1% | 37.7% | 57.6% | 0.801 | 36.4% / 25.8% | 25.2% | 41.3% | 3/4 / 9/8 | no | 0.0 (33.8%) | 12 | 2.0 | 5.3% | 53.3% |
| gavotte_bwv1006 | violin | 58.5% | 85.1% | 6.1% | 69.2% | 70.0% | 0.3 | 11.0% / 19.8% | 55.6% | 65.1% | 4/4 / 2/2 | no | 0.0 (92.7%) | 2 | 0.5 | 5.0% | 26.3% |
| menuet_bwv1006 | violin | 66.8% | 82.6% | 16.3% | 62.6% | 78.0% | 0.334 | 8.1% / 29.3% | 50.4% | 59.5% | 3/4 / 3/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 62.6% | 58.3% |
| allemanda_bwv1004 | violin | 72.1% | 98.7% | 0.8% | 5.6% | 24.4% | 0.459 | 61.0% / 33.3% | 67.2% | 81.1% | 4/4 / 4/4 | yes | 0.0 (63.8%) | 19 | 1.0 | 1.5% | 4.0% |
| schubert_ave_maria | violin | 52.3% | 68.0% | 62.8% | 10.1% | 38.3% | 1.656 | 47.7% / 42.3% | 12.1% | 9.1% | 3/4 / 4/4 | no | 0.0 (25.5%) | 23 | 2.29 (not plain) | 5.4% | 10.5% |
| air_bwv1068 | violin | 46.4% | 68.1% | 49.2% | 11.7% | 51.6% | 0.729 | 21.1% / 67.2% | 23.4% | 15.7% | 4/4 / 4/4 | no | 0.0 (81.2%) | 4 | 2.0 | 5.5% | 10.5% |
| **mean** | | 60.2% | 88.2% | 15.8% | 42.2% | 57.3% | 0.7 | 26.7% / 31.2% | 43.9% | 52.2% | | 6/10 | 6/10 right | 8.2 | 7/10 plain | 29.8% | 40.8% |

How to read it: *tempo ratio* = our quarter notes per written quarter (2 = every note value doubled, the tempo mark doubled; *not plain* = no simple ratio fits, the beat we wrote doesn't follow the music's pulse). *bar = real bar*: our bar holds as much music as the real one, after the tempo ratio. *bar lines off by*: where our bar 1 falls against the real one, in written quarters (share of notes with that offset). *beat slips*: times that offset jumps by one of our beats along the piece. *onset*: each matched note's place after those corrections (16th = 0.25). *early / late*: notes at least half a tick early or late. *raw*: no corrections at all.

## Reading the page

| piece | ties per bar, ours / score | notes shorter than an 8th, ours / score | printed tempo (quarter) | played at (quarter) | marking |
|---|---|---|---|---|---|
| fur_elise | 0.33 / 0.0 | 57.5% / 81.9% | 75.0 | 72.7 | – |
| gymnopedie_1 | 0.21 / 0.23 | 2.9% / 0.0% | 83.0 | 80.7 | – |
| nocturne_op9_2 | 1.19 / 0.03 | 35.7% / 54.7% | 58.5 | 50.2 | 66 |
| traumerei | 0.48 / 0.0 | 15.1% / 0.0% | 41.0 | 42.6 | 100 |
| clair_de_lune | 0.7 / 0.32 | 12.3% / 7.0% | 119.0 | 61.1 | – |
| gavotte_bwv1006 | 1.24 / 0.0 | 68.2% / 2.1% | 75.0 | 151.9 | – |
| menuet_bwv1006 | 0.57 / 0.0 | 32.5% / 2.2% | 115.0 | 113.8 | – |
| allemanda_bwv1004 | 0.57 / 0.0 | 80.4% / 98.7% | 43.0 | 43.7 | – |
| schubert_ave_maria | 0.76 / 0.0 | 53.6% / 69.5% | 73.0 | 51.4 | – |
| air_bwv1068 | 1.64 / 0.06 | 60.4% / 68.1% | 70.0 | 34.8 | – |

## Listening

Our flute part (at its printed tempo) vs the reference melody (at the recording's average tempo), both synthesized as flute, and vs the original recording. DTW on chroma + onset strength finds where each of our notes lands; it is paired with the nearest note of the same pitch class there (the score's notes, or the notes heard in the recording). Deviation = how far that pair sits off a straight line through its six neighbours on each side (local tempo and offset), so a passage played slower is not an error and one note early or late is (against the recording it also holds the performer's own note-to-note rubato, which a steady flute part can't follow; against the score it is ours alone). *pitch = loudest*: our pitch class is the strongest one in the other audio there. *floor*: the score against itself played 15% slower (should be 0). *transcribed back*: Basic Pitch on our rendered flute vs our score (onset 50 ms), a check that the audio is the score.

| piece | vs score: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch = loudest | floor (ms) | vs recording: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch in top 3 | transcribed back F1 |
|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | 0.0 / 413.0 | 0.0 / 0.5 | 27.4% | 54.4% | 0.0 / 0.0 | 163.0 / 843.0 | 0.197 / 1.021 | 67.6% | 64.6% | 0.888 |
| gymnopedie_1 | 0.0 / 0.0 | 0.0 / 0.0 | 1.8% | 95.1% | 0.0 / 0.0 | 146.0 / 445.0 | 0.196 / 0.598 | 81.7% | 92.6% | 0.819 |
| nocturne_op9_2 | 166.0 / 679.0 | 0.138 / 0.568 | 67.5% | 74.8% | 0.0 / 0.0 | 196.0 / 949.0 | 0.163 / 0.793 | 83.6% | 75.8% | 0.816 |
| traumerei | 618.0 / 2606.0 | 0.439 / 1.848 | 93.8% | 74.0% | 0.0 / 0.0 | 2183.0 / 5575.0 | 1.549 / 3.955 | 100.0% | 71.4% | 0.876 |
| clair_de_lune | 310.0 / 1984.0 | 0.316 / 2.022 | 70.9% | 68.7% | 0.0 / 0.0 | 531.0 / 2820.0 | 0.541 / 2.874 | 90.0% | 76.8% | 0.739 |
| gavotte_bwv1006 | 0.0 / 395.0 | 0.0 / 1.0 | 37.6% | 56.8% | 0.0 / 0.0 | 57.0 / 232.0 | 0.145 / 0.587 | 54.6% | 90.2% | 0.843 |
| menuet_bwv1006 | 42.0 / 396.0 | 0.08 / 0.75 | 49.6% | 57.3% | 0.0 / 0.0 | 51.0 / 213.0 | 0.097 / 0.405 | 51.7% | 80.9% | 0.831 |
| allemanda_bwv1004 | 47.0 / 343.0 | 0.034 / 0.25 | 49.5% | 68.6% | 0.0 / 0.0 | 87.0 / 280.0 | 0.063 / 0.204 | 70.8% | 95.5% | 0.698 |
| schubert_ave_maria | 294.0 / 1434.0 | 0.252 / 1.228 | 74.2% | 41.5% | 0.0 / 0.0 | 88.0 / 361.0 | 0.075 / 0.309 | 71.5% | 91.3% | 0.553 |
| air_bwv1068 | 249.0 / 1768.0 | 0.145 / 1.026 | 87.1% | 59.5% | 0.0 / 0.0 | 248.0 / 1070.0 | 0.144 / 0.621 | 87.4% | 61.9% | 0.405 |
| **mean** | 172.6 / 1001.8 | | 55.9% | 65.1% | 0.0 / 0.0 | 375.0 / 1278.8 | | 75.9% | 80.1% | 0.7 |

## Against a published flute arrangement

| piece | notes found | onset exact | timing off (beats) | same length |
|---|---|---|---|---|
| air_bwv1068 | 46.0% | 11.7% | 0.729 | 23.4% |

## Largest onset errors (after tempo and bar-line correction)

- **fur_elise**: commonest errors (quarters: count) {'-1': 17, '-1.25': 8, '-0.5': 6, '-3.38': 3}; G5 written bar 55 beat 2.125 -> ours -3.62 q; G5 written bar 55 beat 1.625 -> ours -3.62 q; A4 written bar 99 beat 1.5 -> ours -3.50 q
- **gymnopedie_1**: commonest errors (quarters: count) {'+1': 2, '+4': 1, '+3': 1}; F#4 written bar 48 beat 1 -> ours +4.00 q; D5 written bar 78 beat 1 -> ours +3.00 q; G5 written bar 71 beat 1 -> ours +1.00 q
- **nocturne_op9_2**: commonest errors (quarters: count) {'+0.5': 28, '+0.25': 17, '-0.25': 16, '-0.5': 16}; C7 written bar 33 beat 6.5 -> ours -10.50 q; B6 written bar 34 beat 1 -> ours -10.00 q; C7 written bar 35 beat 2.75 -> ours -9.00 q
- **traumerei**: commonest errors (quarters: count) {'+0.333': 6, '-0.333': 5, '-0.167': 5, '+0.5': 5}; D5 written bar 27 beat 3.5 -> ours +9.50 q; C5 written bar 26 beat 1 -> ours +9.33 q; D5 written bar 26 beat 4.5 -> ours +9.17 q
- **clair_de_lune**: commonest errors (quarters: count) {'+0.25': 13, '-0.25': 13, '+0.5': 5, '-0.5': 4}; C#6 written bar 23 beat 4 -> ours -5.00 q; C4 written bar 8 beat 4 -> ours +4.00 q; C#5 written bar 68 beat 5.25 -> ours -4.00 q
- **gavotte_bwv1006**: commonest errors (quarters: count) {'+0.5': 40, '+1': 20, '-1': 15, '-0.5': 15}; Ab4 written bar 100 beat 1 -> ours +5.00 q; B4 written bar 93 beat 3.5 -> ours +4.00 q; E5 written bar 56 beat 4.5 -> ours -3.50 q
- **menuet_bwv1006**: commonest errors (quarters: count) {'+0.25': 12, '+0.5': 7, '-0.25': 5, '+0.333': 4}; B5 written bar 19 beat 1 -> ours +4.00 q; B5 written bar 19 beat 2 -> ours +3.50 q; B5 written bar 19 beat 3 -> ours +2.75 q
- **allemanda_bwv1004**: commonest errors (quarters: count) {'-0.5': 180, '+0.5': 64, '+0.25': 38, '-0.25': 25}; B4 written bar 15 beat 3.75 -> ours +2.25 q; D5 written bar 32 beat 3 -> ours +1.50 q; D5 written bar 16 beat 2 -> ours -1.33 q
- **schubert_ave_maria**: commonest errors (quarters: count) {'-0.125': 10, '+0.125': 6, '+0.25': 5, '-0.5': 5}; A4 written bar 17 beat 4.75 -> ours -19.25 q; A4 written bar 17 beat 4.25 -> ours -19.00 q; D5 written bar 18 beat 2.5 -> ours -18.75 q
- **air_bwv1068**: commonest errors (quarters: count) {'+0.125': 27, '+0.25': 15, '+0.375': 9, '-0.125': 7}; C#5 written bar 25 beat 2.375 -> ours -3.88 q; B4 written bar 25 beat 2.5 -> ours -3.75 q; F#5 written bar 1 beat 1 -> ours +3.50 q
