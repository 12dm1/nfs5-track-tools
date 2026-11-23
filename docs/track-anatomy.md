# Anatomy of a Porsche Unleashed track

Every file a track is made of, who writes it, what in the game reads it, and how the files depend on each other.
Read this before you change a track by hand or write a new generator (the converter is one; a source other than
NFS4 would be another, see [plans/D2-own-tracks.md](plans/D2-own-tracks.md)).

`<track>` is the PU directory name (`hometown`), `<Track>` the same capitalised (`Hometown`). Terms:
[glossary.md](glossary.md). Byte layouts: [formats.md](formats.md) and T007. This page replaces the old
"Adding New Tracks" list at the end of T007, written before any track was added.

## 1. The model in one paragraph

A PU track is a **road cut into slices**: one slice every few metres (PU 6.0 m, converted NFS4 tracks ~4.6 m), each
with a centre, direction vectors and a drivable width (the CRP's `SimD` records). Eight slices make a **chunk**; the
visible world is CRP articles named after the chunk they belong to (`RD0040C` = the road from slice 40, `CNK0040L/R`
= the terrain beside it) plus free objects. Everything that varies along the road is keyed by **slice index**: AI
speeds (`.key`), reflections (`.map`), fog and weather (`Sky/*.bin`), police (`.cop`), racing lines (`.lsp`). The
frontend knows the track only through one 1192-byte record in the track list (`nfs5.trk`, for new tracks
`nfs5ext.trk`).

## 2. The files

### Registered in the frontend

| file | content | written by | read by | needed |
|---|---|---|---|---|
| `FEData/Data/nfs5ext.trk` | the 15 stock records + one per converted track (id, name id, directory, environment name, splash names, circuit flag) | `nfs5_frontend.build_ext_trk` | `FUN_004FAE20` (count = size / 0x4A8), via the runtime's redirect ([runtime.md](runtime.md) §1) | yes: without a record the track is not in the list |
| `FEData/Data/nfs5ext.names` | the menu names for name ids 0x2000+ | `nfs5_frontend.write_name_table` | the runtime's `GetString` hook (§2); ids must be consecutive | yes, or the name is empty |
| `FEData/TrackArt/<track>.fsh` | menu map + name strip (drawn from the vroad) | `nfs5_frontend.build_track_art` | frontend, track list | written always |
| `FEData/TrackArt/<track>Sp.fsh`, `<track>SpSm.fsh` | loading splashes, small map | same | frontend, loading screen | written always |

### The track itself (`GameData/Track/`)

| file | content | written by | read by (v3.5) | needed |
|---|---|---|---|---|
| `<track>.crp` | karT archive: road/terrain/object articles, materials (`mt`, surface in `mt+0`), `SimD`/`SimT` slice table, `Vis`, `RMth`, `sn` last | `nfs4_geometry.py` + `nfs5_crp_writer.py`, `track_corridor.py` (SimD walls) | `FUN_004876E0`, `FUN_004879D0` (articles, `.edg`, `Vis`), `FUN_00480080` (SimD) | yes; "ERROR: Couldn't load track" otherwise |
| `<track>.fsh` | textures, every entry a power-of-two square, ARGB8888 with mips | `nfs5_fsh.py` | with the CRP | yes; a non-square entry crashes (`FUN_00535BC0`) |
| `<track>.map` | text INI: global light multipliers + reflection/tint sections by slice range; `section0` is the default | `nfs5_environment.build_map` | `FUN_004409D0` | yes (one section is enough) |
| `<track>.env` | reflection cube faces (FSH); `.map` textures name these tags or `SKY ` | `nfs5_environment.build_env` | `FUN_004409D0` | yes |
| `<track>.edg` | AI and camera barrier: polylines of 28-byte segments, flag `0x00` road edge, `0x10` barrier | `nfs5_track_files.py` | `FUN_00488090` | yes |
| `<track>.jnc` | junctions; one record `[1, 1, 0...]` = a closed loop | `nfs5_track_files.py` | generic loader | yes; it decides circuit vs point-to-point |
| `<track>.dtx` | detail texture config, 11 bytes, a copy of `alps.dtx` | copied | `FUN_0044E010` | yes |
| `<track>_forward.key`, `<track>_backwards.key` | AI speed keys: version 1, count, 400 x (slice, speed), sentinel `100000, 600` | `nfs5_track_files.py` | `FUN_0044FB40` | yes |
| `racer/work/<track>0.lsp`, `<track>1.lsp` | racing lines forward / reverse | `nfs5_track_files.build_lsp` | AI | yes for AI races |
| `<track>_audio.scn` | ambient `AUDIO_ELEMENT`s; bank numbers must exist in `trackext.viv` | `nfs5_ambient.py` | `*.scn` scan | written when the track has ambience (see §5) |
| `Cops/<track>.cop` | police spawn slices | `nfs5_environment.build_cop` | police | written always |
| `Sky/<Track>.bin` | atmosphere: fog, sky colour, rain/snow/storm keys by slice; <= 400 keys per table, ends with a negative slice | `track_atmosphere.py` from `data/atmosphere/<track>.ini` | sky and fog | yes |
| `Sky/<Track>.txt` | sky layers (MULTINORMAL), cloud scale and wind | `nfs5_environment.build_sky_txt` | sky | yes |
| `Sky/<Track>.fsh` | sky textures and the horizon panorama (`horz`, atlas 4 x rows) | `nfs5_environment.build_sky_fsh` | sky | yes |
| `animdefs.txt` | **shared**: stock object definitions + the HZ3 block (object sounds of converted tracks) | `nfs5_ambient.py` | object physics and sound | the stock file is enough for a track without converted objects' sounds |

"yes" = the engine loads the file on every race (the loaders in T007) and the converter always writes it. Leaving
one out was not tested file by file: write them all.

Not written for converted tracks: `_fstart.scn`/`_bstart.scn` (point-to-point start grids; a circuit must not have
them), `_cameras.scn` (replay cameras; the game does without), `_ST%d.scn` (Factory Driver missions), `.drp`.

### Read only by a runtime (NEW formats)

| file | content | written by | without it |
|---|---|---|---|
| `GameData/Sounds/trackext.viv` | **shared**: the 593 stock banks + the banks of all converted tracks | `nfs5_ambient.build_all` | no ambient sound on converted tracks |
| `<track>.hsw` | High Stakes polygon walls ([formats/hsw.md](formats/hsw.md)); its presence also tells the runtime the track is converted (walls and the converted-track AI on, [runtime.md](runtime.md) §4) | `export_hs_walls.py` | cars drive through walls: PU's own collision does not cover them |
| `<track>.tan` | animated textures ([formats/tan.md](formats/tan.md)) | `nfs5_texanim.py` (separate step) | water and screens stand still |
| `<track>.aispeed`, `<track>_r.aispeed`, `<track>.aiprofile`, `aiprofile_cars.txt` | AI zones, caps, speed profile, per-car scales ([formats/ai-files.md](formats/ai-files.md)) | `install_ai_data.py` (separate step) | bots drive the untuned line: slower, more wall contacts |

## 3. What must agree between the files

1. **One directory name.** Every file name above starts with `<track>`; the `nfs5.trk` record's directory field is
   the same string, its environment field is `<Track>` (the `Sky/` file names), its splash fields `<track>Sp` and
   `<track>SpSm`. **15 characters at most** (a 17-character name loaded as its first 15: "ERROR - Couldn't load
   track", then a crash), a-z 0-9 _, and never a stock track's directory. `convert_track.py` checks all three.
2. **One slice count.** `N` = the number of slices (NFS4 `nVRoad`). The CRP has `N` SimD records and road articles
   every 8 slices; `.key` slices, `.cop` slices, `.map` section ranges, `Sky/*.bin` keys, `.lsp` points and
   `.aispeed` zones all index `0 ... N-1`. Change the road and *everything* along it has to be rebuilt.
3. **Texture tags.** CRP materials name `.fsh` entries by their 4-character tag; `.map` textures name `.env` tags
   (or `SKY `), not the road textures.
4. **Circuit.** A circuit has all three: `.jnc` = one record `[1, 1, 0...]`, `nfs5.trk` flag `0x10000` (props at
   +108), no `_fstart/_bstart.scn`. A point-to-point track has a dead-end `.jnc` record and both start files.
5. **Sound numbers.** Every bank number in `_audio.scn` and in the HZ3 block of `animdefs.txt` is in
   `trackext.viv`; numbers come from `data/ambient/pu_bank_ids.json`, never regenerated.
6. **Shared files are rebuilt whole.** `trackext.viv` always holds the banks of all 19 High Stakes tracks.
   **`nfs5ext.trk` holds only the tracks of the last `convert_track.py` run**: convert every track you want in the
   menu in one run (`--all`, or name them all), or the others drop out of the list (their files stay).

## 4. The smallest track that loads and drives

For a generator that is not the NFS4 converter, in order of what the game needs first:

1. A record in `nfs5ext.trk` (copy one, set id, name id, directory, environment, splash names, circuit flag) and a
   name in `nfs5ext.names`.
2. `<track>.crp` with road articles, materials with surface words, `SimD` slice records, `sn` last, last LOD 10000.
3. `<track>.fsh` with every texture the CRP names.
4. `.map` (one section), `.env`, `.edg`, `.jnc`, `.dtx`, both `.key` (the converter's writers take a slice list).
5. `Sky/<Track>.{bin,txt,fsh}`: a template from another track works (`sky_bin_for` patches one).
6. For a race against bots: both `.lsp`. For walls that stop cars: `.hsw` and a runtime.

The converter's writers already produce all of this from an in-memory track (`nfs4_frd.Frd`: vroad, blocks,
polygons, objects); a new source only has to fill the same model. That is the plan in
[plans/D2-own-tracks.md](plans/D2-own-tracks.md).

## 5. Tracks made by the community

`convert_track.py` reads any NFS4 track folder with the stock file set (`tr.frd`, `tr0.qfs`, `sky.qfs`, `tr.ini`,
`spdfa.bin`, `spdra.bin`), including tracks made in T3ED. Ambient sound is the exception: High Stakes keeps the
ambience scripts of its own 19 tracks inside `nfshs.exe`, so a custom track has none. The converter then uses the
track its NFS Mixer `Mixer.ini` is based on (`Based On=N`, the tram bank number), or `--ambient-from <HS track>`,
or none. [new-tracks-frd.md](new-tracks-frd.md) is the walkthrough.
