# Converting a High Stakes track: the pipeline

One command converts an NFS4 (Need for Speed: High Stakes, PC) track into a set of new Porsche Unleashed files.
This page is the overview; every stage links to the research note that explains *why* it is done that way.

Before you start: [setup.md](setup.md) (games, Python, `nfs5tools.ini`).

## 1. Quick start

```
python tools/track/convert_track.py --list                      # the 19 HS tracks found
python tools/track/convert_track.py Hometown                    # build into work/build/tracks
python tools/track/convert_track.py Hometown --install <game>   # build and copy into the game
python tools/track/nfs5_texanim.py Hometown --install <game>    # animated textures (reads the installed .fsh)
python tools/track/install_ai_data.py --install <game> hometown # tuned AI lines/speeds from data/ai
```

Then run the game with a runtime that implements [runtime.md](runtime.md) (the PC wrapper with
`[content] extended_content=1`, or nfs5-web). Hometown appears in the track list as a circuit.

`--all` converts every track folder found (the 19 High Stakes tracks, and community tracks if you put them there).
Track names on the command line are the NFS4 folder names (`Hometown`, `Coastal`, `Lostcany`...); the PU directory
name is the lower-case name, except NFS4 `Coastal`, which becomes `dolphin` (Dolphin Cove) so it does not collide
with PU's own `coastal`. The directory must have at most 15 characters, only a-z 0-9 _, and not be a stock track's;
the converter refuses anything else and `--directory <name>` (one track per run) sets another.

**`nfs5ext.trk` lists only the tracks of this run.** Convert every track you want in the menu in one run (`--all`,
or name them all); a track converted alone replaces the list, and the others drop out of the menu (their files stay).

A track made by the community: [new-tracks-frd.md](new-tracks-frd.md) (`--ambient-from`, `--directory`). A first
session step by step: [tutorial.md](tutorial.md). Every output file and how they depend on each other:
[track-anatomy.md](track-anatomy.md).

Partial rebuilds:

| flag | rewrites only |
|---|---|
| `--crp-only` | `GameData/Track/<track>.crp` |
| `--sky-only` | `GameData/Track/Sky/<Track>.fsh` and `.bin` |
| `--walls-only [--wall-inset M]` | `GameData/Track/<track>.hsw` |
| `--art-only` | `FEData/TrackArt/*` (does not touch the CRP or `nfs5ext.trk`) |

Sound only: `python tools/track/build_ambient.py --all --install <game>` ([pipeline/ambient-sound.md](pipeline/ambient-sound.md)).

## 2. Stages

| stage | input (NFS4) | output (PU) | code | research |
|---|---|---|---|---|
| HS2 textures | `tr0.qfs` | `<track>.fsh` (square, ARGB8888, mips) | `nfs5_fsh.py` | [HS2](findings/HS2-texture-pipeline.md) |
| HS5 geometry | `tr.frd` (blocks, objects, XObj, anim) | `<track>.crp` (karT archive) | `nfs4_geometry.py`, `nfs5_crp_writer.py` | [HS3](findings/HS3-crp-writer.md), [HS5](findings/HS5-geometry-conversion.md), [HS12](findings/HS12-movable-objects.md) |
| HS6 collision + AI route | vroad, `spdfa`/`spdra` speeds | `.edg`, `_forward.key`, `_backwards.key`, `.jnc`, `.dtx`, `racer/work/<track>0/1.lsp`, SimD corridor in the CRP | `nfs5_track_files.py`, `track_corridor.py` | [HS6](findings/HS6-collision-and-ai.md) |
| HS11 walls | FRD wall edges, object faces | `<track>.hsw` | `export_hs_walls.py` | [HS11](findings/HS11-walls-alpha.md), [HZ5/HZ7](findings/HZ4-animated-textures.md) |
| HS7 environment | `tr.ini`, sky, `[strip]` | `.map`, `.env`, `Sky/<Track>.{bin,txt,fsh}`, `Cops/<track>.cop` | `nfs5_environment.py`, `track_atmosphere.py` | [HS7](findings/HS7-environment.md), [ATM1](findings/ATM1-sky-atmosphere.md), [SKY1](findings/SKY1-horizon-slit.md) |
| HS13 ambience | `nfshs.exe` tables, `tram*.bnk`, FRD `SoundSource` | `<track>_audio.scn`, `GameData/Sounds/trackext.viv`, `animdefs.txt` HZ3 block | `nfs5_ambient.py`, `tools/audio/*` | [HS13](findings/HS13-ambient-sound.md), [HS15](findings/HS15-sound-codec-buffer-overrun.md) |
| HS8 frontend | everything above | `FEData/Data/nfs5ext.trk`, `nfs5ext.names`, `FEData/TrackArt/<track>{,Sp,SpSm}.fsh` | `nfs5_frontend.py` | [HS8](findings/HS8-registration.md) |
| HZ4 animated textures | FRD `anim` bytes, installed `.fsh` | `<track>.tan` | `nfs5_texanim.py` | [HZ4](findings/HZ4-animated-textures.md) |
| AI tuning | race traces | `.aispeed`, `.aiprofile`, `aiprofile_cars.txt`, tuned `.lsp` | `tools/track_editor/ai_*.py`, `tools/race_test/*` | [AI1](findings/AI1-converted-track-ai.md), [AI6](findings/AI6-ai-line-view.md) |

The first bring-up in the game, and every bug it found: [HS9](findings/HS9-in-game-bringup.md). The original plan
and the NFS4 side of every format: [HS1](findings/HS1-nfs4-track-migration.md). The PU formats:
[T007](findings/T007-pu-track-formats.md) and [formats.md](formats.md).

## 3. The output tree

For one track (`<track>` = PU directory name, `<Track>` = capitalised):

```
FEData/Data/nfs5ext.trk               15 stock records + one per converted track (all converted so far)
FEData/Data/nfs5ext.names             name table for the GetString hook
FEData/TrackArt/<track>.fsh           menu map + name strip
FEData/TrackArt/<track>Sp.fsh         splashes
FEData/TrackArt/<track>SpSm.fsh       small map
GameData/Sounds/trackext.viv          593 stock banks + the banks of ALL 19 converted tracks
GameData/Track/<track>.crp            geometry (karT), RefPack-compressed
GameData/Track/<track>.fsh            textures
GameData/Track/<track>.map            surfaces, texture tints
GameData/Track/<track>.env            reflection cube map
GameData/Track/<track>.edg            AI/camera barrier
GameData/Track/<track>.jnc            junctions (circuit loop)
GameData/Track/<track>.dtx            detail texture config, copied from alps.dtx
GameData/Track/<track>_forward.key    AI speed keys
GameData/Track/<track>_backwards.key
GameData/Track/<track>.hsw            NEW: walls for the runtime
GameData/Track/<track>_audio.scn      ambient sound elements
GameData/Track/animdefs.txt           stock file + the HZ3 block (object sounds)
GameData/Track/Cops/<track>.cop       police spawn points
GameData/Track/Sky/<Track>.bin        atmosphere (from data/atmosphere/<track>.ini)
GameData/Track/Sky/<Track>.txt        sky layers
GameData/Track/Sky/<Track>.fsh        sky textures and horizon panorama
GameData/Track/racer/work/<track>0.lsp, <track>1.lsp   racing lines (forward, reverse)
--- separate steps ---
GameData/Track/<track>.tan            NEW: animated textures (nfs5_texanim.py)
GameData/Track/<track>{,_r}.aispeed   NEW: AI speed zones and caps (install_ai_data.py)
GameData/Track/<track>.aiprofile      NEW: AI speed profile
GameData/Track/aiprofile_cars.txt     NEW: per-car profile scale
```

`nfs5ext.trk` and `trackext.viv` are rebuilt as a whole every time. `trackext.viv` always holds the banks of all
19 High Stakes tracks, so a run never silences another track; `nfs5ext.trk` holds the 15 stock records and the
tracks **of this run only** (see §1).

## 4. Hard rules (break one and the game crashes or the track misbehaves)

* **Never edit stock files.** Everything is a new file or an in-memory redirect. Do not patch stock `.crp` files: fix
  model problems at runtime.
* **Textures must be power-of-two squares.** Non-square textures crashed germany/gt2/uk (the loader's caller
  `FUN_00535bc0` dereferences a null page); `nfs5_fsh.ensure_square_pot` stretches them.
* **The last LOD distance must be 10000**, not 65535 (HS9).
* **`sn` MiscData must be the last entry** of the CRP (the game looks it up by binary search on the ident); no `sf`.
* **Sound loops must start at sample 0**, at most 28 000 samples, at most 0x7FFC bytes, mono EA-XA, one slot per bank
  ([HS15](findings/HS15-sound-codec-buffer-overrun.md)).
* **Bank numbers** come from `data/ambient/pu_bank_ids.json`. Never regenerate it: the numbers are baked into the
  `.scn` files. 115 numbers are left.
* **Atmosphere tables hold at most 400 keys** and end with a negative slice.
* Track ids for new records come from the free list (15, 17, 19-24, 27, 28, 31, 33, then from 35); name ids from
  0x2000.

## 5. Checking a conversion

Offline (no game window):

| script | checks |
|---|---|
| `tools/track/test_crp_writer.py` | karT writer round trip |
| `tools/track/test_hs5_geometry.py` | the CRP contains exactly the source geometry |
| `tools/track/test_hs6_support.py` | collision, AI route, topology files |
| `tools/track/test_hs7_environment.py` | sky, reflections, tints, ambience, police |
| `tools/track/test_atm3_atmosphere.py` ... `test_atm8_sky.py` | atmosphere model, mesh, fog maths, editor, sky |
| `tools/track/test_hs14_ambient.py` | the installed sound: banks, loop starts, sizes, collisions |
| `tools/track_editor/test_sky3_horizon_import.py` | horizon import round trip (run `export_sky_textures.py` first) |

In the game: [findings/AUTO1-race-test.md](findings/AUTO1-race-test.md) (`tools/race_test/race_test.py`) runs races
without the frontend, with the AI driving the player car, and reports contacts, respawns and lap times. It is built
for the PC wrapper (command-line race setup and its logs).

## 6. Beyond conversion

* **Atmosphere** (fog, sky colour, snow/rain, storms, baked light tint along the road): edit
  `data/atmosphere/<track>.ini` in the track editor, then `convert_track.py <Track> --sky-only --install <game>`
  (`--crp-only` too if you changed `light_tint`). [editor.md](editor.md), [formats/atmosphere-ini.md](formats/atmosphere-ini.md).
* **Horizon panorama in HD**: `export_sky_textures.py` -> upscale -> the editor's import window. [SKY3](findings/SKY3-horizon-import.md).
* **Track textures in HD**: `export_track_textures.py` / `export_nfs4_textures_x4.py` -> regenerate -> the texture
  editor fixes seams and tone. [texture-editor.md](texture-editor.md).
* **AI**: [pipeline/ai.md](pipeline/ai.md).
* **Blender**: `export_track_fragment.py` cuts a rectangle of a PU track into a `.blend`/`.glb` with textures;
  `export_hs_walls.py` / `export_ai_lines.py` export walls and lines for inspection.
* **Your own track**: today through the NFS4 format, made or edited in T3ED (the community's track editor) and
  converted like any High Stakes track: [new-tracks-frd.md](new-tracks-frd.md). From a scene modelled in Blender:
  planned, [plans/D2-own-tracks.md](plans/D2-own-tracks.md). What any generator has to produce:
  [track-anatomy.md](track-anatomy.md).
