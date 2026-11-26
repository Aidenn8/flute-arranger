# Tune picker on the reference recordings: rules vs learned

Written by `bench/tune_picker_judge.py` (bench/ref_score.py's measures, default options, flute
alone, cached analyses, rhythm reader: rules). Each cell: rules -> learned. *learned
used*: whether the learned picker chose the tune (only piano recordings use it).

| piece | instr. | learned used | found | extra | onset exact | same length | listen dev median ms | listen dev p90 ms |
|---|---|---|---|---|---|---|---|---|
| fur_elise | piano | yes | 63.3 -> 78.2 | 2.8 -> 6.9 | 84.2 -> 79.8 | 68.5 -> 67.7 | 0.0 -> 0.0 | 266.0 -> 206.0 |
| gymnopedie_1 | piano | yes | 98.3 -> 97.5 | 4.1 -> 12.1 | 96.6 -> 95.7 | 87.9 -> 89.6 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| nocturne_op9_2 | piano | yes | 76.8 -> 93.3 | 4.8 -> 8.2 | 54.8 -> 50.7 | 64.7 -> 64.9 | 104.0 -> 148.0 | 605.0 -> 591.0 |
| traumerei | piano | yes | 49.4 -> 98.1 | 3.6 -> 9.7 | 41.8 -> 47.8 | 58.2 -> 62.4 | 352.0 -> 243.0 | 929.0 -> 820.0 |
| clair_de_lune | piano | yes | 52.6 -> 81.1 | 14.6 -> 24.1 | 40.9 -> 39.6 | 29.6 -> 30.6 | 254.0 -> 172.0 | 1734.0 -> 603.0 |
| gavotte_bwv1006 | violin | no | 58.8 -> 58.8 | 2.3 -> 2.3 | 74.5 -> 74.5 | 55.6 -> 55.6 | 0.0 -> 0.0 | 395.0 -> 395.0 |
| menuet_bwv1006 | violin | no | 66.3 -> 66.3 | 11.0 -> 11.0 | 72.1 -> 72.1 | 71.3 -> 71.3 | 0.0 -> 0.0 | 483.0 -> 483.0 |
| allemanda_bwv1004 | violin | no | 75.6 -> 75.6 | 0.2 -> 0.2 | 73.3 -> 73.3 | 72.4 -> 72.4 | 0.0 -> 0.0 | 191.0 -> 191.0 |
| schubert_ave_maria | violin | no | 53.7 -> 53.7 | 51.7 -> 51.7 | 20.9 -> 20.9 | 22.9 -> 22.9 | 412.0 -> 412.0 | 1621.0 -> 1621.0 |
| air_bwv1068 | violin | no | 46.0 -> 46.0 | 42.1 -> 42.1 | 21.3 -> 21.3 | 30.7 -> 30.7 | 359.0 -> 359.0 | 2470.0 -> 2470.0 |
| chopin_prelude_4 | piano | yes | 88.3 -> 93.5 | 19.5 -> 17.8 | 85.3 -> 83.3 | 69.1 -> 72.2 | 0.0 -> 0.0 | 483.0 -> 467.0 |
| chopin_prelude_7 | piano | yes | 97.9 -> 97.9 | 2.1 -> 2.1 | 95.7 -> 95.7 | 91.5 -> 91.5 | 0.0 -> 0.0 | 212.0 -> 212.0 |
| chopin_prelude_20 | piano | yes | 69.5 -> 100.0 | 0.0 -> 4.8 | 87.8 -> 91.5 | 78.0 -> 84.7 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| chopin_waltz_69_2 | piano | yes | 89.7 -> 98.1 | 1.4 -> 1.1 | 94.8 -> 96.2 | 88.2 -> 89.9 | 0.0 -> 0.0 | 181.0 -> 0.0 |
| kinderszenen_1 | piano | yes | 81.8 -> 99.0 | 16.2 -> 16.8 | 66.7 -> 65.3 | 38.3 -> 42.9 | 106.0 -> 0.0 | 726.0 -> 667.0 |
| mozart_k545_1 | piano | yes | 81.0 -> 98.4 | 8.4 -> 18.3 | 92.1 -> 94.4 | 83.8 -> 89.0 | 0.0 -> 0.0 | 224.0 -> 112.0 |
| moonlight_1 | piano | yes | 83.6 -> 87.7 | 25.0 -> 67.7 | 65.7 -> 64.5 | 53.9 -> 37.4 | 362.0 -> 661.0 | 1609.0 -> 4696.0 |
| brahms_waltz_15 | piano | yes | 66.8 -> 74.3 | 0.0 -> 0.0 | 30.4 -> 25.9 | 8.0 -> 9.4 | 155.0 -> 155.0 | 487.0 -> 411.0 |
| **piano mean** | | | 76.8 -> 92.1 | 7.9 -> 14.6 | 72.1 -> 71.6 | 63.1 -> 64.0 | 102.5 -> 106.1 | 573.5 -> 675.8 |
