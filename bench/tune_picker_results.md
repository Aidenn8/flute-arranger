# Learned tune picker: training results

Written by `bench/train_tune_picker.py`. Notes are our piano transcription of rendered
excerpts (bench/TRAINING.md); a note is a tune note if it matches one of the source's tune
notes (same key, onset within 60 ms). *Rules* = piano_melody + fill_tune (the app's
picker), *learned* = melody.learned_tune. Scored per transcribed note: notes not scored
(POP909's BRIDGE track, ASAP notes with no score note) are left out.

## Transcriber errors measured on the training excerpts

```
{
 "miss_rate_by_velocity": {
  "0-30": 0.237,
  "30-45": 0.038,
  "45-60": 0.029,
  "60-75": 0.038,
  "75-90": 0.038,
  "90-128": 0.023
 },
 "onset_jitter_ms_median": 2.3,
 "onset_jitter_ms_p90": 5.8,
 "velocity_fit": [
  0.996,
  0.246
 ],
 "end_ratio_median": 1.003,
 "ghosts_per_source_note": 0.037,
 "ghost_intervals": {
  "12": 139,
  "-12": 36,
  "0": 24,
  "-24": 15,
  "-19": 15,
  "-8": 14,
  "-7": 14,
  "-5": 14
 }
}
```

## Training variants (validation excerpts)

| trained on | notes | fit (s) | rules P / R / F1 | learned P / R / F1 | line step |
|---|---|---|---|---|---|
| real excerpts only | 15116 | 11 | 0.849 / 0.356 / 0.502 | 0.923 / 0.744 / 0.824 | {'bias': -0.5, 'step': 0.0, 'leap': 0.0, 'repeat': 0.0} |
| simulated + real | 1158768 | 60 | 0.849 / 0.356 / 0.502 | 0.906 / 0.896 / 0.901 | {'bias': -0.5, 'step': 0.0, 'leap': 0.5, 'repeat': 0.0} |
| simulated + real, without the rules' picks | 1158768 | 61 | 0.849 / 0.356 / 0.502 | 0.906 / 0.902 / 0.904 | {'bias': -0.5, 'step': 0.0, 'leap': 0.5, 'repeat': 0.5} |

## Validation excerpts (model: simulated + real)

| excerpt | rules P | R | F1 | learned P | R | F1 |
|---|---|---|---|---|---|---|
| asap/Beethoven/Piano_Sonatas_18-1/Avdeeva01@162 | 0.825 | 0.241 | 0.373 | 0.887 | 0.825 | 0.855 |
| asap/Beethoven/Piano_Sonatas_22-1/IVLEVA01@147 | 0.967 | 0.436 | 0.601 | 0.935 | 0.904 | 0.920 |
| asap/Beethoven/Piano_Sonatas_28-2/KimHJ03@264 | 0.895 | 0.456 | 0.604 | 0.853 | 0.896 | 0.874 |
| asap/Beethoven/Piano_Sonatas_30-1/CHEN02@22 | 0.894 | 0.476 | 0.622 | 0.848 | 0.925 | 0.885 |
| asap/Chopin/Etudes_op_10_1/Avdeeva02@17 | 1.000 | 0.071 | 0.132 | 0.997 | 0.929 | 0.962 |
| asap/Schubert/Piano_Sonatas_894-2/KimSY16@243 | 0.959 | 0.723 | 0.824 | 0.926 | 0.946 | 0.936 |
| pop909/041@91 | 0.644 | 0.402 | 0.495 | 0.622 | 0.761 | 0.685 |
| pop909/593@9 | 0.307 | 0.209 | 0.249 | 0.656 | 0.651 | 0.654 |
| pop909/645@129 | 0.969 | 0.481 | 0.642 | 0.892 | 0.961 | 0.925 |
| pop909/747@86 | 0.763 | 0.636 | 0.694 | 0.932 | 0.846 | 0.887 |
| pop909/833@29 | 0.907 | 0.856 | 0.881 | 0.975 | 0.990 | 0.982 |
| *pop909 notes* | 0.746 | 0.554 | 0.636 | 0.830 | 0.856 | 0.843 |
| *asap notes* | 0.924 | 0.295 | 0.447 | 0.931 | 0.908 | 0.920 |
| **all notes** | 0.849 | 0.356 | 0.502 | 0.906 | 0.896 | 0.901 |

## Test excerpts (model: simulated + real)

| excerpt | rules P | R | F1 | learned P | R | F1 |
|---|---|---|---|---|---|---|
| asap/Bach/Fugue_bwv_873/Lisiecki02@24 | 0.819 | 0.596 | 0.690 | 0.720 | 0.976 | 0.828 |
| asap/Beethoven/Piano_Sonatas_9-2/Tysman05@20 | 0.955 | 0.728 | 0.826 | 0.940 | 0.966 | 0.953 |
| asap/Liszt/Concert_Etude_S145_1/FEINER03@21 | 0.945 | 0.316 | 0.473 | 0.845 | 0.956 | 0.897 |
| asap/Scriabin/Sonatas_5/FaB05M@517 | 0.880 | 0.450 | 0.596 | 0.766 | 0.962 | 0.853 |
| pop909/333@105 | 0.346 | 0.196 | 0.250 | 0.600 | 0.587 | 0.593 |
| pop909/408@19 | 0.746 | 0.465 | 0.573 | 0.838 | 0.921 | 0.877 |
| pop909/519@37 | 0.833 | 0.175 | 0.289 | 0.939 | 0.748 | 0.833 |
| *pop909 notes* | 0.621 | 0.268 | 0.374 | 0.806 | 0.756 | 0.780 |
| *asap notes* | 0.882 | 0.482 | 0.623 | 0.796 | 0.965 | 0.872 |
| **all notes** | 0.833 | 0.434 | 0.570 | 0.798 | 0.918 | 0.854 |

## Features that matter most (validation, permutation importance, ROC AUC drop)

- onsets_per_s: 0.0164
- under_cluster_top: 0.0125
- free_s: 0.0114
- over_tops_median: 0.0086
- pitch: 0.0081
- piece_vel_median: 0.0059
- under_context_max: 0.0053
- to_prev_top: 0.0048
- held_above_max: 0.0045
- to_next_top: 0.0041
- context_position: 0.0035
- near_prev: 0.0032
- under_near_max: 0.0027
- over_local_top: 0.0022
- near_next: 0.0020
