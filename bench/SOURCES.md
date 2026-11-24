# Benchmark data sources

Everything here is downloaded or generated into git-ignored folders (`models/`,
`samples/`). Nothing in this list is committed to the repo.

## Soundfont

| File | Source | License |
|---|---|---|
| `models/MuseScore_General.sf2` (~215 MB) | https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General/MuseScore_General.sf2 (official MuseScore mirror) | MIT (license text saved as `models/MuseScore_General_License.md`) |

Fetched by `scripts/fetch_models.sh`.

## Ground truth: Bach chorales (`samples/gt/`)

Made by `bench/make_groundtruth.py` from the music21 corpus (Bach chorale
encodings shipped with music21; the music is public domain). The soprano is the
melody; `truth.json` holds its notes as `[pitch, start_s, end_s]`, with the
same humanized timing as the audio.

| Set | Pieces | Render |
|---|---|---|
| A | bwv66.6, bwv269, bwv347, bwv153.1, bwv86.6, bwv267, bwv40.8, bwv244.37, bwv26.6, bwv64.2, bwv2.6, bwv101.7 | `A_piano.wav`: all four voices on piano |
| B | same 12 | `B_mix.wav`: soprano on clarinet (even index) or violin (odd index), lower voices on piano, equal loudness |
| C | bwv104.6, bwv108.6, bwv113.8 (folders end in `_arp`) | `C_arpeggio.wav`: soprano over broken chords in 8ths built from the lower voices, all piano |

Tempo is fixed per piece, drawn from 72-100 bpm; note onsets and ends are
jittered by up to 15 ms and velocities vary. One silent beat leads in. Seeds are
fixed, so reruns give the same files.

## Real recordings (`samples/real/`)

Fetched by `bench/fetch_recordings.py` through the Wikimedia Commons API. Each
file sits in its own folder with a `source.json` holding the full metadata.

### Solo piano

| Folder | Title | Performer / author | License | Length | URL |
|---|---|---|---|---|---|
| `satie_gymnopedie_1` | Erik Satie, Gymnopédie No. 1 ("lent et douloureux") | Robin Alciatore (via Musopen) | Public domain | 3:04 | https://commons.wikimedia.org/wiki/File:Erik_Satie_-_gymnopedies_-_la_1_ere._lent_et_douloureux.ogg |
| `beethoven_fur_elise` | Beethoven, Für Elise (classroom edition) | Jason M. C. Han | CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0) | 2:40 | https://commons.wikimedia.org/wiki/File:For_Elise_(F%C3%BCr_Elise)_Beethoven_JMC_Han.ogg |
| `chopin_nocturne_op9_2` | Chopin, Nocturne in E-flat major, Op. 9 No. 2 | Frank Levy (via Musopen) | Public domain (Musopen PD dedication) | 4:31 | https://commons.wikimedia.org/wiki/File:Nocturne_in_E_flat_major,_Op._9_no._2.mp3 |

### Songs with singing plus accompaniment

| Folder | Title | Performer / author | License | Length | URL |
|---|---|---|---|---|---|
| `star_spangled_banner_vocal` | "The Star-Spangled Banner", solo soprano with band | MU1 Amy Broadbent, United States Navy Band | Public domain (US government work) | 1:28 | https://commons.wikimedia.org/wiki/File:%22The_Star-Spangled_Banner%22_-_Solo_vocalist_-_U.S._Navy_Band.oga |
| `america_the_beautiful_vocal` | "America the Beautiful", male vocalist with band (music Samuel A. Ward, lyrics Katharine Lee Bates) | Courtney Williams, United States Navy Band | Public domain (US government work) | 3:46 | https://commons.wikimedia.org/wiki/File:America_the_Beautiful_(male_vocalist)_-_United_States_Navy_Band.opus |

The CC BY-SA recording needs attribution (performer, license, link) if any
derived audio or score is shared.

Spares listed in the script, used only if one of the above fails: Debussy
"Clair de lune" (public domain) and Frank Crumit's 1927 "Kingdom Coming"
(public domain).
