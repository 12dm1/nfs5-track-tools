# File formats

Every file the pipeline reads or writes, with the place it is specified. PU = Porsche Unleashed v3.5, HS = High
Stakes. "Stock" = a format the unmodified game already reads; "new" = read only by the runtime
([runtime.md](runtime.md)).

## Porsche Unleashed, stock formats

| file | what | spec | code |
|---|---|---|---|
| `FEData/Data/nfs5.trk` / `nfs5ext.trk` | track records, 0x4A8 bytes each | [T007](findings/T007-pu-track-formats.md#nfs5trk--track-definitions), [HS8](findings/HS8-registration.md) | `nfs5_frontend.py` |
| `GameData/Track/<t>.crp` | karT 3D archive: articles, materials (`mt`, `sn`, `Cm`...), parts (`vt nm uv pr df tr Anqt Base`), RefPack-compressed | [T007](findings/T007-pu-track-formats.md#crp--drp--3d-scenery-archives-kart-format), [HS3](findings/HS3-crp-writer.md), [HS5](findings/HS5-geometry-conversion.md), [HS12](findings/HS12-movable-objects.md) | `nfs5_crp.py` (read), `nfs5_crp_writer.py` (write) |
| `GameData/Track/<t>.fsh`, `Sky/*.fsh`, `FEData/TrackArt/*.fsh` | EA SHPI texture container | [T007](findings/T007-pu-track-formats.md#fsh--track-textures-ea-shpi-format), [HS2](findings/HS2-texture-pipeline.md) | `ea_refpack.py`, `nfs5_fsh.py`, `fsh_pixels.py` |
| `.map` | text INI: surfaces, texture refs, tints | [T007](findings/T007-pu-track-formats.md#map--track-surface-sections), [HS7](findings/HS7-environment.md) | `nfs5_environment.py` |
| `.edg` | AI/camera barrier: uint32 + 28-byte edges | [T007](findings/T007-pu-track-formats.md#edg--edge--collision-data), [HS6](findings/HS6-collision-and-ai.md) | `nfs5_track_files.py`, `track_corridor.py` |
| `.jnc` | junctions / lane probabilities | [T007](findings/T007-pu-track-formats.md#jnc--junction--lane-probabilities) | `nfs5_track_files.py` |
| `_forward.key`, `_backwards.key` | AI speed keys: version, count, 400 x (slice, speed) | [T007](findings/T007-pu-track-formats.md#key--ai-route-keyframes), [HS6](findings/HS6-collision-and-ai.md) | `nfs5_track_files.py`, `apply_key.py` |
| `.env` | reflection cube map (SHPI) | [T007](findings/T007-pu-track-formats.md#env--environment-reflection-maps) | `nfs5_environment.py` |
| `.dtx` | detail texture config | [T007](findings/T007-pu-track-formats.md#dtx--detail-texture-config) | copied from `alps.dtx` |
| `_audio.scn`, `.scn` | text scene elements (`AUDIO_ELEMENT`, triggers, cameras) | [T007](findings/T007-pu-track-formats.md#scn--scene-files-text-format), [HS13](findings/HS13-ambient-sound.md) | `nfs5_ambient.py` |
| `Cops/<t>.cop` | police spawn points | [T007](findings/T007-pu-track-formats.md#cops-cop--police-spawn-points) | `nfs5_environment.py` |
| `Sky/<T>.bin` | sky, fog, weather by slice | [formats/sky-bin.md](formats/sky-bin.md), [ATM1](findings/ATM1-sky-atmosphere.md) | `nfs5_sky_bin.py`, `track_atmosphere.py` |
| `Sky/<T>.txt` | sky layers (text) | [ATM1](findings/ATM1-sky-atmosphere.md#skytracktxt-sky-layers) | `tools/track_editor/sky_layers.py` |
| `racer/work/<t><dir>.lsp` | racing line: uint32 1 + (x, z) float pairs | [formats/ai-files.md](formats/ai-files.md) | `nfs5_track_files.py`, `apply_lsp.py`, `ai_record.py` |
| `GameData/Sounds/track.viv` / `trackext.viv` | BIGF archive of one-slot banks, number = 3 digits in the name | [HS13](findings/HS13-ambient-sound.md), [pipeline/ambient-sound.md](pipeline/ambient-sound.md) | `tools/audio/pu_track_viv.py` |
| `*.bnk` | `BNKl` v4 sound bank, EA-XA ADPCM | [HS13](findings/HS13-ambient-sound.md), [HS15](findings/HS15-sound-codec-buffer-overrun.md) | `tools/audio/ea_bnk.py` |
| `animdefs.txt` | animated article definitions incl. `SECTION:Audio` | [pipeline/ambient-sound.md](pipeline/ambient-sound.md) section 11 | `nfs5_ambient.py` |
| CRP MiscData `mt` +0 | surface word: low nibble = drivable surface, high bits flags | [HS9](findings/HS9-in-game-bringup.md) section 3 | `nfs5_crp_writer.Material` |

## New files (runtime only)

| file | what | spec |
|---|---|---|
| `FEData/Data/nfs5ext.names` | tab-separated name table for ids from 0x2000 | [runtime.md](runtime.md) item 2 |
| `GameData/Track/<t>.hsw` | walls and object walls | [formats/hsw.md](formats/hsw.md) |
| `GameData/Track/<t>.tan` | animated textures | [formats/tan.md](formats/tan.md) |
| `GameData/Track/<t>[_r].aispeed` | AI speed zones and caps | [formats/ai-files.md](formats/ai-files.md) |
| `GameData/Track/<t>[_r][.tag].aiprofile` | AI speed profile | [formats/ai-files.md](formats/ai-files.md) |
| `GameData/Track/aiprofile_cars.txt` | per-car profile scale | [formats/ai-files.md](formats/ai-files.md) |

## Repository data

| file | what | spec |
|---|---|---|
| `data/atmosphere/<t>.ini` | editable atmosphere of a converted track | [formats/atmosphere-ini.md](formats/atmosphere-ini.md) |
| `data/ambient/nfs4_ambient.json` | NFS4 ambience tables extracted from `nfshs.exe` | [HS13](findings/HS13-ambient-sound.md) |
| `data/ambient/pu_bank_ids.json` | `track/slot/variant -> bank number` registry (never regenerate) | [pipeline/ambient-sound.md](pipeline/ambient-sound.md) |
| `data/ambient/ambient_overrides.json` | per-slot corrections by ear | [pipeline/ambient-sound.md](pipeline/ambient-sound.md) section 6 |

## High Stakes, source formats

| file | what | spec | code |
|---|---|---|---|
| `Data/Tracks/<T>/tr.frd` | blocks, polygons, vroad, objects, XObj, lights, sound sources | [HS1](findings/HS1-nfs4-track-migration.md), [HS5](findings/HS5-geometry-conversion.md), [HS12](findings/HS12-movable-objects.md); upstream: e-rk/speedtools `specs/frd.ksy` | `nfs4_frd.py` |
| `tr0.qfs` | textures (RefPack SHPI), `<mirrored>` selection | [HS2](findings/HS2-texture-pipeline.md) | `nfs5_fsh.py` |
| `tr.ini` | fog, fog regions, weather, lightning, sun, `[strip]` horizon | [HS7](findings/HS7-environment.md), [formats/atmosphere-ini.md](formats/atmosphere-ini.md) | `nfs5_environment.py`, `track_atmosphere.from_nfs4_ini` |
| `spdfa.bin`, `spdra.bin` | AI speeds per slice | [HS6](findings/HS6-collision-and-ai.md) | `nfs5_track_files.decode_spd` |
| `Data/Audio/Sfx/tram%02d.bnk` | ambience banks | [HS13](findings/HS13-ambient-sound.md) | `tools/audio/ea_bnk.py` |
| `nfshs.exe` tables | ambience zones/scripts/object sounds | [HS13](findings/HS13-ambient-sound.md) | `tools/audio/nfs4_ambient_scripts.py` |
