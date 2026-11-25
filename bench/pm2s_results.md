# PM2S vs Beat This! on piano recordings

`bench/pm2s_compare.py`: the piano pieces of the reference set, default options, flute
alone, scored as `bench/ref_score.py` scores them. Variants: **beatthis** (the app
as it is: Beat This! beats and bars, rule-based rhythm), **pm2s** (PM2S beats and
bars from the piano transcription, then the same pipeline; keeping PM2S's own bar
lines instead of moving them to the tune's long notes changed nothing on any piece),
**pm2s_q** (PM2S beats and its quantised onsets and lengths), **beatthis_q** (Beat
This! beats and bars, PM2S's quantised onsets and lengths).

## found

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 63.3 | 58.4 | 49.0 | 51.2 |
| gymnopedie_1 | 98.3 | 98.3 | 83.9 | 84.7 |
| nocturne_op9_2 | 76.8 | 77.6 | 60.1 | 59.4 |
| traumerei | 49.4 | 49.4 | 43.8 | 45.6 |
| clair_de_lune | 52.6 | 61.9 | 54.3 | 45.4 |
| chopin_prelude_4 | 88.3 | 88.3 | 80.5 | 84.4 |
| chopin_prelude_7 | 97.9 | 95.8 | 85.4 | 85.4 |
| chopin_prelude_20 | 69.5 | 69.5 | 69.5 | 69.5 |
| chopin_waltz_69_2 | 89.7 | 86.0 | 41.5 | 43.6 |
| kinderszenen_1 | 81.8 | 81.8 | 80.8 | 78.8 |
| mozart_k545_1 | 81.0 | 78.8 | 66.9 | 67.1 |
| moonlight_1 | 83.6 | 83.6 | 83.6 | 78.7 |
| brahms_waltz_15 | 66.8 | 75.9 | 68.4 | 59.4 |
| **mean** (yes: count) | 76.85 | 77.33 | 66.75 | 65.63 |

## onset exact

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 84.2 | 7.1 | 5.9 | 52.8 |
| gymnopedie_1 | 96.6 | 28.4 | 27.3 | 69.0 |
| nocturne_op9_2 | 54.8 | 8.6 | 16.8 | 21.2 |
| traumerei | 41.8 | 11.4 | 5.7 | 19.2 |
| clair_de_lune | 40.9 | 40.6 | 22.6 | 10.9 |
| chopin_prelude_4 | 85.3 | 17.6 | 17.7 | 50.8 |
| chopin_prelude_7 | 95.7 | 8.7 | 19.5 | 46.3 |
| chopin_prelude_20 | 87.8 | 92.7 | 90.2 | 82.9 |
| chopin_waltz_69_2 | 94.8 | 70.2 | 15.2 | 31.8 |
| kinderszenen_1 | 66.7 | 63.0 | 60.0 | 59.0 |
| mozart_k545_1 | 92.1 | 84.9 | 69.3 | 68.1 |
| moonlight_1 | 65.7 | 82.4 | 54.9 | 36.5 |
| brahms_waltz_15 | 30.4 | 12.7 | 16.4 | 21.6 |
| **mean** (yes: count) | 72.06 | 40.64 | 32.42 | 43.85 |

## ±16th

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 87.4 | 43.5 | 38.5 | 78.0 |
| gymnopedie_1 | 96.6 | 44.0 | 34.3 | 72.0 |
| nocturne_op9_2 | 64.7 | 34.3 | 32.4 | 40.7 |
| traumerei | 44.3 | 30.4 | 22.9 | 37.0 |
| clair_de_lune | 60.4 | 49.7 | 34.1 | 27.7 |
| chopin_prelude_4 | 92.6 | 32.4 | 25.8 | 70.8 |
| chopin_prelude_7 | 95.7 | 10.9 | 26.8 | 56.1 |
| chopin_prelude_20 | 90.2 | 92.7 | 92.7 | 90.2 |
| chopin_waltz_69_2 | 96.0 | 70.5 | 15.8 | 35.8 |
| kinderszenen_1 | 77.8 | 75.3 | 73.8 | 76.9 |
| mozart_k545_1 | 95.5 | 87.1 | 82.0 | 83.5 |
| moonlight_1 | 67.6 | 92.2 | 65.7 | 41.7 |
| brahms_waltz_15 | 57.6 | 23.2 | 31.2 | 41.4 |
| **mean** (yes: count) | 78.95 | 52.78 | 44.31 | 57.83 |

## same length

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 68.5 | 3.1 | 3.0 | 50.7 |
| gymnopedie_1 | 87.9 | 5.2 | 9.1 | 42.0 |
| nocturne_op9_2 | 64.7 | 4.1 | 20.1 | 27.4 |
| traumerei | 58.2 | 2.5 | 0.0 | 28.8 |
| clair_de_lune | 29.6 | 41.7 | 15.9 | 4.4 |
| chopin_prelude_4 | 69.1 | 10.3 | 4.8 | 30.8 |
| chopin_prelude_7 | 91.5 | 39.1 | 4.9 | 24.4 |
| chopin_prelude_20 | 78.0 | 85.4 | 85.4 | 75.6 |
| chopin_waltz_69_2 | 88.2 | 72.3 | 10.6 | 18.6 |
| kinderszenen_1 | 38.3 | 38.3 | 37.5 | 32.1 |
| mozart_k545_1 | 83.8 | 78.8 | 64.9 | 60.9 |
| moonlight_1 | 53.9 | 59.8 | 12.7 | 19.8 |
| brahms_waltz_15 | 8.0 | 6.3 | 5.5 | 5.4 |
| **mean** (yes: count) | 63.05 | 34.38 | 21.11 | 32.38 |

## ties/bar

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 0.24 | 0.61 | 0.62 | 0.25 |
| gymnopedie_1 | 0.26 | 0.34 | 0.21 | 0.36 |
| nocturne_op9_2 | 1.09 | 0.55 | 0.76 | 2.52 |
| traumerei | 0.43 | 0.33 | 0.16 | 0.9 |
| clair_de_lune | 0.55 | 0.59 | 0.64 | 0.89 |
| chopin_prelude_4 | 0.19 | 0.26 | 0.13 | 1.16 |
| chopin_prelude_7 | 0.06 | 0.08 | 0.64 | 1.0 |
| chopin_prelude_20 | 0.08 | 0.14 | 0.14 | 0.33 |
| chopin_waltz_69_2 | 0.23 | 0.68 | 0.25 | 0.58 |
| kinderszenen_1 | 0.56 | 0.28 | 0.54 | 1.12 |
| mozart_k545_1 | 0.35 | 0.46 | 0.32 | 0.33 |
| moonlight_1 | 0.59 | 0.45 | 0.23 | 0.32 |
| brahms_waltz_15 | 0.43 | 0.54 | 0.57 | 1.07 |
| **mean** (yes: count) | 0.39 | 0.41 | 0.40 | 0.83 |

## meter

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 3/8 | 4/4 | 4/4 | 3/8 |
| gymnopedie_1 | 3/4 | 2/4 | 2/4 | 3/4 |
| nocturne_op9_2 | 12/8 | 6/8 | 6/8 | 12/8 |
| traumerei | 4/4 | 2/4 | 2/4 | 4/4 |
| clair_de_lune | 3/4 | 6/8 | 6/8 | 3/4 |
| chopin_prelude_4 | 4/4 | 2/4 | 2/4 | 4/4 |
| chopin_prelude_7 | 3/4 | 3/4 | 3/4 | 3/4 |
| chopin_prelude_20 | 4/4 | 2/4 | 2/4 | 4/4 |
| chopin_waltz_69_2 | 3/4 | 9/8 | 9/8 | 3/4 |
| kinderszenen_1 | 12/8 | 6/8 | 6/8 | 12/8 |
| mozart_k545_1 | 4/4 | 4/4 | 4/4 | 4/4 |
| moonlight_1 | 3/4 | 6/8 | 6/8 | 3/4 |
| brahms_waltz_15 | 4/4 | 3/4 | 3/4 | 4/4 |
| **mean** (yes: count) | – | – | – | – |

## meter ok

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | yes | no | no | yes |
| gymnopedie_1 | yes | yes | yes | yes |
| nocturne_op9_2 | yes | no | no | yes |
| traumerei | yes | no | no | yes |
| clair_de_lune | no | no | no | no |
| chopin_prelude_4 | yes | no | no | yes |
| chopin_prelude_7 | yes | no | no | yes |
| chopin_prelude_20 | yes | no | no | yes |
| chopin_waltz_69_2 | yes | no | no | yes |
| kinderszenen_1 | no | yes | yes | no |
| mozart_k545_1 | yes | yes | yes | yes |
| moonlight_1 | no | no | no | no |
| brahms_waltz_15 | no | no | no | no |
| **mean** (yes: count) | 9/13 | 3/13 | 3/13 | 9/13 |

## tempo ratio

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 1.0 | 1.562 | 1.556 | 1.0 |
| gymnopedie_1 | 1.0 | 0.698 | 0.667 | 1.0 |
| nocturne_op9_2 | 1.0 | 1.375 | 1.5 | 1.1 |
| traumerei | 1.0 | 0.75 | 0.725 | 0.967 |
| clair_de_lune | 2.0 | 1.0 | 1.0 | 1.389 |
| chopin_prelude_4 | 1.0 | 0.625 | 0.6 | 1.0 |
| chopin_prelude_7 | 1.0 | 0.528 | 0.625 | 1.0 |
| chopin_prelude_20 | 1.0 | 1.0 | 1.0 | 1.0 |
| chopin_waltz_69_2 | 1.0 | 0.5 | 0.556 | 1.0 |
| kinderszenen_1 | 1.5 | 1.5 | 1.5 | 1.5 |
| mozart_k545_1 | 1.0 | 1.0 | 1.0 | 1.0 |
| moonlight_1 | 1.0 | 1.5 | 1.5 | 1.0 |
| brahms_waltz_15 | 0.375 | 0.75 | 0.714 | 0.345 |
| **mean** (yes: count) | 1.07 | 0.98 | 1.00 | 1.02 |

## listen dev (ms)

| piece | beatthis | pm2s | pm2s_q | beatthis_q |
|---|---|---|---|---|
| fur_elise | 0.0 | 68.0 | 115.0 | 56.0 |
| gymnopedie_1 | 0.0 | 372.0 | 390.0 | 0.0 |
| nocturne_op9_2 | 104.0 | 186.0 | 339.0 | 418.0 |
| traumerei | 352.0 | 549.0 | 492.0 | 441.0 |
| clair_de_lune | 254.0 | 377.0 | 574.0 | 592.0 |
| chopin_prelude_4 | 0.0 | 109.0 | 1146.0 | 369.0 |
| chopin_prelude_7 | 0.0 | 147.0 | 231.0 | 198.0 |
| chopin_prelude_20 | 0.0 | 0.0 | 0.0 | 0.0 |
| chopin_waltz_69_2 | 0.0 | 0.0 | 398.0 | 373.0 |
| kinderszenen_1 | 106.0 | 106.0 | 106.0 | 106.0 |
| mozart_k545_1 | 0.0 | 0.0 | 19.0 | 0.0 |
| moonlight_1 | 362.0 | 0.0 | 194.0 | 1172.0 |
| brahms_waltz_15 | 155.0 | 167.0 | 167.0 | 311.0 |
| **mean** (yes: count) | 102.54 | 160.08 | 320.85 | 310.46 |

## Key and time signature

| piece | score | app's key | PM2S key signature (as a major key) | PM2S bar |
|---|---|---|---|---|
| fur_elise | A minor, 3/8 | +0 minor | C | 4/4 |
| gymnopedie_1 | D major, 3/4 | +2 major | G | 2/4 |
| nocturne_op9_2 | E-flat major, 12/8 | -3 major | Eb | 2/4 |
| traumerei | F major, 4/4 | -1 major | F | 2/4 |
| clair_de_lune | D-flat major, 9/8 | -5 major | Db | 2/4 |
| chopin_prelude_4 | E minor, 2/2 | +1 minor | G | 2/4 |
| chopin_prelude_7 | A major, 3/4 | +3 major | A | 3/4 |
| chopin_prelude_20 | C minor, 4/4 | -3 minor | Eb | 2/4 |
| chopin_waltz_69_2 | B minor, 3/4 | +2 minor | D | 3/4 |
| kinderszenen_1 | G major, 2/4 | +1 major | G | 2/4 |
| mozart_k545_1 | C major, 4/4 | +0 major | C | 4/4 |
| moonlight_1 | C-sharp minor, 2/2 | +4 minor | E | 2/4 |
| brahms_waltz_15 | A-flat major, 3/4 | -4 major | Ab | 3/4 |

## Verdict

- **pm2s**: tune notes exactly where the score has them 40.6% on average against 72.1% for beatthis; more than a point better on 2 of 13 pieces, worse on 10.
- **pm2s_q**: tune notes exactly where the score has them 32.4% on average against 72.1% for beatthis; more than a point better on 1 of 13 pieces, worse on 12.
- **beatthis_q**: tune notes exactly where the score has them 43.9% on average against 72.1% for beatthis; more than a point better on 0 of 13 pieces, worse on 13.

So the app keeps Beat This! and its own rhythm reading for piano recordings; PM2S stays
installed for this comparison and as an opt-in (`SHEETSWAP_PIANO_BEATS=pm2s`, see
`pipeline.find_beats`).
