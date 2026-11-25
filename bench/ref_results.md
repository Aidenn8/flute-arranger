# Timing against real sheet music

`bench/ref_score.py` on the reference set in `samples/ref/` (sources: `bench/REFERENCES.md`).
Default options, flute alone. All beats are written quarter notes.

## Rhythm and tune changes: before → after

Same beat grids for both columns (the cached analyses and beat code as of commit 09ebcf5);
before = the rhythm, melody and pipeline code of that commit, after = with these changes:
rhythm read as per-beat patterns with the piece's own vocabulary (8ths stay 8ths, triplets
only in pieces that clearly play them, notes leaned into a beat land on it, sung scoops left
out); a violin's split held notes rejoined (unless the piano model heard a new attack); Für
Elise's broken chords kept as part of the tune; a tune note held while chords strike its key
again (Gymnopédie's F#4). Listening = "vs score" deviation.

| piece | found | exact | ±16th | same len | ties/bar (score) | <8th % (score) | listen dev med / p90 ms | notes >50 ms |
|---|---|---|---|---|---|---|---|---|
| fur_elise | 42.1 → 58.3 | 74.1 → 81.9 | 76.7 → 86.6 | 55.2 → 63.6 | 0.33 → 0.28 (0.00) | 57.5 → 63.4 (82) | 0/413 → 0/257 | 27.4 → 25.6 |
| gymnopedie_1 | 98.3 → 98.3 | 96.6 → 96.6 | 96.6 → 96.6 | 87.1 → 87.9 | 0.21 → 0.26 (0.23) | 2.9 → 0.0 (0) | 0/0 → 0/0 | 1.8 → 2.8 |
| nocturne_op9_2 | 70.2 → 73.4 | 52.6 → 56.7 | 67.4 → 65.4 | 57.2 → 63.8 | 1.19 → 1.05 (0.03) | 35.7 → 34.2 (55) | 166/679 → 112/686 | 67.5 → 57.2 |
| menuet_bwv1006 | 66.8 → 66.3 | 62.6 → 72.1 | 78.0 → 80.3 | 50.4 → 71.3 | 0.57 → 0.27 (0.00) | 32.5 → 12.0 (2) | 42/396 → 0/483 | 49.6 → 40.0 |
| gavotte_bwv1006 | 58.5 → 58.8 | 69.2 → 74.0 | 70.0 → 75.3 | 55.6 → 54.8 | 1.24 → 1.18 (0.00) | 68.2 → 59.5 (2) | 0/395 → 0/395 | 37.6 → 38.2 |
| allemanda_bwv1004 | 75.4 → 75.6 | 69.6 → 73.3 | 91.2 → 92.4 | 70.8 → 72.4 | 0.82 → 0.71 (0.00) | 86.4 → 85.5 (99) | 0/215 → 0/191 | 32.5 → 27.1 |
| traumerei | 45.6 → 45.6 | 27.4 → 34.2 | 38.4 → 35.6 | 43.8 → 52.1 | 0.70 → 0.43 (0.00) | 18.4 → 3.3 (0) | 376/1751 → 441/1884 | 73.3 → 76.7 |
| clair_de_lune | 50.0 → 50.0 | 37.7 → 36.4 | 57.6 → 57.0 | 25.2 → 29.1 | 0.70 → 0.59 (0.32) | 12.3 → 4.7 (7) | 310/1984 → 245/1866 | 70.9 → 72.1 |
| schubert_ave_maria | 52.3 → 53.7 | 10.1 → 20.9 | 38.3 → 54.9 | 12.1 → 24.2 | 0.76 → 0.71 (0.00) | 53.6 → 30.3 (70) | 294/1434 → 467/1543 | 74.2 → 87.8 |
| air_bwv1068 | 46.4 → 46.0 | 11.7 → 16.5 | 51.6 → 54.3 | 23.4 → 26.8 | 1.64 → 1.18 (0.06) | 60.4 → 30.3 (68) | 249/1768 → 334/1768 | 87.1 → 87.7 |
| **mean** | 60.6 → 62.6 | 51.2 → 56.3 | 66.6 → 69.8 | 48.1 → 54.6 | 0.82 → 0.67 | 42.8 → 32.3 | 144/904 → 160/907 | 52.2 → 51.5 |

The Gavotte's and Clair de lune's <8th shares can't reach the score's while their beat is
half/twice the written one. Listening got worse on Schubert, the Air and Träumerei (bar or
tempo still wrong there); for Schubert and the Air it grows with how many split notes are
rejoined, though their onsets and lengths match the score better.

## Notes and timing

| piece | instr. | notes found | pitch right | extra | onset exact | onset ±16th | timing off (mean, beats) | early / late | same length | same gap to next | meter ours / real | bar = real bar | bar lines off by (q) | beat slips | tempo ratio | raw: onset in bar | raw: downbeats on bar lines |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | piano | 58.3% | 99.1% | 2.7% | 81.9% | 86.6% | 0.232 | 14.0% / 4.0% | 63.6% | 88.9% | 3/8 / 3/8 | yes | 0.0 (99.1%) | 2 | 1.0 | 83.2% | 89.0% |
| gymnopedie_1 | piano | 98.3% | 99.1% | 4.1% | 96.6% | 96.6% | 0.078 | 0.0% / 3.4% | 87.9% | 94.7% | 3/4 / 3/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 97.4% | 94.2% |
| nocturne_op9_2 | piano | 73.4% | 98.7% | 4.7% | 56.7% | 65.4% | 0.528 | 18.1% / 25.2% | 63.8% | 70.2% | 12/8 / 12/8 | yes | 0.0 (58.4%) | 14 | 1.0 | 30.5% | 46.2% |
| traumerei | piano | 45.6% | 97.3% | 2.6% | 34.2% | 35.6% | 1.377 | 41.1% / 24.7% | 52.1% | 73.2% | 4/4 / 4/4 | yes | 0.0 (50.7%) | 6 | 1.0 | 11.0% | 16.7% |
| clair_de_lune | piano | 50.0% | 84.4% | 13.1% | 36.4% | 57.0% | 0.881 | 36.4% / 27.2% | 29.1% | 45.2% | 3/4 / 9/8 | no | 0.0 (30.5%) | 15 | 2.0 | 6.6% | 53.3% |
| gavotte_bwv1006 | violin | 58.8% | 89.3% | 2.3% | 74.0% | 75.3% | 0.257 | 14.0% / 11.9% | 54.8% | 73.7% | 4/4 / 2/2 | no | 0.0 (91.7%) | 2 | 0.5 | 6.5% | 36.2% |
| menuet_bwv1006 | violin | 66.3% | 88.4% | 11.0% | 72.1% | 80.3% | 0.275 | 5.7% / 22.1% | 71.3% | 73.2% | 3/4 / 3/4 | yes | 0.0 (100.0%) | 0 | 1.0 | 73.0% | 69.6% |
| allemanda_bwv1004 | violin | 75.6% | 100.0% | 0.2% | 73.3% | 92.4% | 0.084 | 11.2% / 15.4% | 72.4% | 88.6% | 4/4 / 4/4 | yes | 0.0 (33.3%) | 6 | 1.0 | 26.4% | 23.1% |
| schubert_ave_maria | violin | 53.7% | 64.6% | 51.7% | 20.9% | 54.9% | 1.435 | 32.7% / 46.4% | 24.2% | 13.9% | 3/4 / 4/4 | no | -0.667 (43.8%) | 8 | 2.51 (not plain) | 4.6% | 0.0% |
| air_bwv1068 | violin | 46.0% | 70.6% | 42.1% | 16.5% | 54.3% | 0.8 | 18.1% / 65.4% | 26.8% | 19.3% | 4/4 / 4/4 | no | 0.0 (86.6%) | 8 | 2.0 | 9.4% | 11.1% |
| **mean** | | 62.6% | 89.2% | 13.4% | 56.3% | 69.8% | 0.6 | 19.1% / 24.6% | 54.6% | 64.1% | | 6/10 | 6/10 right | 6.1 | 9/10 plain | 34.9% | 43.9% |

How to read it: *tempo ratio* = our quarter notes per written quarter (2 = every note value doubled, the tempo mark doubled; *not plain* = no simple ratio fits, the beat we wrote doesn't follow the music's pulse). *bar = real bar*: our bar holds as much music as the real one, after the tempo ratio. *bar lines off by*: where our bar 1 falls against the real one, in written quarters (share of notes with that offset). *beat slips*: times that offset jumps by one of our beats along the piece. *onset*: each matched note's place after those corrections (16th = 0.25). *early / late*: notes at least half a tick early or late. *raw*: no corrections at all.

## Reading the page

| piece | ties per bar, ours / score | notes shorter than an 8th, ours / score | printed tempo (quarter) | played at (quarter) | marking |
|---|---|---|---|---|---|
| fur_elise | 0.28 / 0.0 | 63.4% / 81.9% | 75.0 | 72.8 | – |
| gymnopedie_1 | 0.26 / 0.23 | 0.0% / 0.0% | 83.0 | 80.7 | – |
| nocturne_op9_2 | 1.05 / 0.03 | 34.2% / 54.7% | 58.5 | 50.1 | 66 |
| traumerei | 0.43 / 0.0 | 3.3% / 0.0% | 49.0 | 42.5 | 100 |
| clair_de_lune | 0.59 / 0.32 | 4.7% / 7.0% | 119.0 | 61.2 | – |
| gavotte_bwv1006 | 1.18 / 0.0 | 59.5% / 2.1% | 75.0 | 151.8 | – |
| menuet_bwv1006 | 0.27 / 0.0 | 12.0% / 2.2% | 115.0 | 113.8 | – |
| allemanda_bwv1004 | 0.71 / 0.0 | 85.5% / 98.7% | 45.0 | 43.7 | – |
| schubert_ave_maria | 0.71 / 0.0 | 30.3% / 69.5% | 73.0 | 50.6 | – |
| air_bwv1068 | 1.18 / 0.06 | 30.3% / 68.1% | 70.0 | 34.8 | – |

## Listening

Our flute part (at its printed tempo) vs the reference melody (at the recording's average tempo), both synthesized as flute, and vs the original recording. DTW on chroma + onset strength finds where each of our notes lands; it is paired with the nearest note of the same pitch class there (the score's notes, or the notes heard in the recording). Deviation = how far that pair sits off a straight line through its six neighbours on each side (local tempo and offset), so a passage played slower is not an error and one note early or late is (against the recording it also holds the performer's own note-to-note rubato, which a steady flute part can't follow; against the score it is ours alone). *pitch = loudest*: our pitch class is the strongest one in the other audio there. *floor*: the score against itself played 15% slower (should be 0). *transcribed back*: Basic Pitch on our rendered flute vs our score (onset 50 ms), a check that the audio is the score.

| piece | vs score: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch = loudest | floor (ms) | vs recording: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch in top 3 | transcribed back F1 |
|---|---|---|---|---|---|---|---|---|---|---|
| fur_elise | 0.0 / 257.0 | 0.0 / 0.312 | 25.6% | 67.3% | 0.0 / 0.0 | 134.0 / 602.0 | 0.163 / 0.73 | 69.8% | 70.6% | 0.879 |
| gymnopedie_1 | 0.0 / 0.0 | 0.0 / 0.0 | 2.8% | 96.7% | 0.0 / 0.0 | 152.0 / 558.0 | 0.205 / 0.75 | 83.7% | 90.2% | 0.819 |
| nocturne_op9_2 | 112.0 / 686.0 | 0.093 / 0.574 | 57.2% | 82.0% | 0.0 / 0.0 | 196.0 / 998.0 | 0.164 / 0.834 | 87.7% | 74.4% | 0.821 |
| traumerei | 441.0 / 1884.0 | 0.312 / 1.335 | 76.7% | 77.9% | 0.0 / 0.0 | 928.0 / 4003.0 | 0.658 / 2.838 | 90.0% | 80.5% | 0.859 |
| clair_de_lune | 245.0 / 1866.0 | 0.25 / 1.902 | 72.1% | 63.1% | 0.0 / 0.0 | 370.0 / 3178.0 | 0.377 / 3.24 | 87.0% | 77.2% | 0.714 |
| gavotte_bwv1006 | 0.0 / 395.0 | 0.0 / 1.0 | 38.2% | 60.8% | 0.0 / 0.0 | 52.0 / 226.0 | 0.133 / 0.571 | 51.7% | 91.8% | 0.85 |
| menuet_bwv1006 | 0.0 / 483.0 | 0.0 / 0.917 | 40.0% | 54.2% | 0.0 / 0.0 | 61.0 / 237.0 | 0.115 / 0.45 | 60.0% | 87.7% | 0.867 |
| allemanda_bwv1004 | 0.0 / 191.0 | 0.0 / 0.139 | 27.1% | 74.6% | 0.0 / 0.0 | 63.0 / 153.0 | 0.046 / 0.111 | 59.7% | 95.6% | 0.718 |
| schubert_ave_maria | 467.0 / 1543.0 | 0.394 / 1.301 | 87.8% | 38.7% | 0.0 / 0.0 | 101.0 / 479.0 | 0.085 / 0.404 | 72.6% | 92.5% | 0.614 |
| air_bwv1068 | 334.0 / 1768.0 | 0.194 / 1.026 | 87.7% | 59.5% | 0.0 / 0.0 | 285.0 / 1324.0 | 0.165 / 0.768 | 92.1% | 60.8% | 0.451 |
| **mean** | 159.9 / 907.3 | | 51.5% | 67.5% | 0.0 / 0.0 | 234.2 / 1175.8 | | 75.4% | 82.1% | 0.8 |

## Against a published flute arrangement

| piece | notes found | onset exact | timing off (beats) | same length |
|---|---|---|---|---|
| air_bwv1068 | 45.7% | 16.5% | 0.8 | 26.8% |

## Largest onset errors (after tempo and bar-line correction)

- **fur_elise**: commonest errors (quarters: count) {'-1': 9, '-0.5': 5, '+0.5': 5, '-0.0833': 4}; F5 written bar 53 beat 1.75 -> ours -4.25 q; C5 written bar 54 beat 2 -> ours -3.75 q; E5 written bar 53 beat 1 -> ours -3.75 q
- **gymnopedie_1**: commonest errors (quarters: count) {'+1': 2, '+4': 1, '+3': 1}; F#4 written bar 48 beat 1 -> ours +4.00 q; D5 written bar 78 beat 1 -> ours +3.00 q; G5 written bar 71 beat 1 -> ours +1.00 q
- **nocturne_op9_2**: commonest errors (quarters: count) {'+0.5': 33, '-0.5': 17, '+0.25': 11, '-0.25': 7}; B6 written bar 34 beat 3 -> ours -12.00 q; C7 written bar 34 beat 1.5 -> ours -11.50 q; C7 written bar 35 beat 2.75 -> ours -9.25 q
- **traumerei**: commonest errors (quarters: count) {'-0.5': 16, '+2': 4, '+3': 3, '+1': 3}; F4 written bar 25 beat 1 -> ours -6.50 q; C5 written bar 31 beat 1 -> ours +6.00 q; F5 written bar 30 beat 4 -> ours +6.00 q
- **clair_de_lune**: commonest errors (quarters: count) {'-0.25': 17, '+0.25': 14, '+0.5': 6, '+0.75': 4}; F#5 written bar 22 beat 2 -> ours -4.50 q; C4 written bar 8 beat 4 -> ours +4.00 q; C#5 written bar 68 beat 5.25 -> ours -4.00 q
- **gavotte_bwv1006**: commonest errors (quarters: count) {'+0.5': 21, '-1': 20, '-0.5': 18, '+1': 13}; E5 written bar 56 beat 4.5 -> ours -3.50 q; E5 written bar 36 beat 4 -> ours -3.00 q; Ab5 written bar 100 beat 4 -> ours +3.00 q
- **menuet_bwv1006**: commonest errors (quarters: count) {'+0.5': 9, '+0.25': 6, '-0.25': 4, '+2': 4}; B5 written bar 19 beat 2 -> ours +3.00 q; B4 written bar 41 beat 2 -> ours +2.50 q; Eb5 written bar 41 beat 3 -> ours +2.50 q
- **allemanda_bwv1004**: commonest errors (quarters: count) {'+0.25': 36, '-0.25': 22, '+0.5': 15, '-0.5': 11}; D5 written bar 32 beat 3 -> ours +1.75 q; D5 written bar 29 beat 1.5 -> ours -1.00 q; A5 written bar 16 beat 3 -> ours +1.00 q
- **schubert_ave_maria**: commonest errors (quarters: count) {'+0.0833': 14, '+0.167': 12, '-0.0833': 8, '-0.167': 6}; A4 written bar 17 beat 4.75 -> ours -20.42 q; A4 written bar 17 beat 4.25 -> ours -20.08 q; Bb4 written bar 18 beat 1 -> ours -19.00 q
- **air_bwv1068**: commonest errors (quarters: count) {'+0.25': 21, '+0.125': 20, '+1.75': 6, '+1': 6}; C#5 written bar 25 beat 2.375 -> ours -3.88 q; B4 written bar 25 beat 2.5 -> ours -3.75 q; C#5 written bar 25 beat 2.625 -> ours -3.62 q
