# A track of your own today: through the NFS4 format (T3ED)

High Stakes has had a track-making community for twenty years. Its editor, **T3ED** (the Need for Speed 3/4 track
editor by JimDiabolo, 4.5.0 is the current release on NFSAddons), reads and writes `tr.frd`, and NFSAddons lists
hundreds of tracks made with it. `convert_track.py` takes such a track like a stock one. So a track of your own can
come from:

* **a published community track**: download it, convert it, race it. Respect its author: the converted files are a
  derivative of their work; keep them for yourself unless they agree to more.
* **your own edit in T3ED**: start from a High Stakes track (or a community one), move, split and add polygons,
  retexture (FSHTool), add lanes, objects and lights in T3ED, then convert the result.

A track modelled from scratch in Blender is planned, not built: [plans/D2-own-tracks.md](plans/D2-own-tracks.md).

## What the converter needs

The track folder with the stock file set; T3ED and the NFS Mixer keep it:

| file | used for |
|---|---|
| `tr.frd` | geometry, road (vroad), walls, objects, lights, sound sources |
| `tr0.qfs` | textures. Codes `0x7B` (8-bit + palette), `0x7D`, `0x7E`, `0x78` RGB565, `0x7F`, `0x6D`; any size (non-square ones are made square) |
| `sky.qfs`, `tr.ini` | sky, fog, light: the seed of `data/atmosphere/<track>.ini` |
| `spdfa.bin`, `spdra.bin` | AI speeds and racing line; size `5N + ceil(N/2)` bytes for `N` slices |
| `Mixer.ini` (optional) | `Based On=N`: the stock track whose ambient sound it borrows |

Not used: the night and weather variants (`trn.frd`, `trn0.qfs`, `TrW.ini`, `TrNW.ini`), `*(lo-rez).qfs`, `tr.can`,
`tr.cam`, `heights.sim`, extra `T*.qfs`. File names may be in any case on Windows; on Linux rename them to lower
case.

Put the folder where the converter looks for tracks, `<nfs4>/Data/Tracks/<Folder>/` (where the NFS Mixer installs
it too). To keep your High Stakes copy untouched, make a second root instead and point the converter at it with the
environment variable `NFS4_ROOT`: `Data/Tracks/<Folder>` (the track), a link to the real `Data/Audio`, links to the 19
stock track folders (the sound archive reads them) and a copy of `nfshs.exe`.

## Convert and race

```
python tools/track/convert_track.py NeighborhoodDrive --directory nbhdrive --out work/build/community
python tools/track/nfs5_texanim.py NeighborhoodDrive --directory nbhdrive --install work/build/community
```

* `--directory`: the PU name of the track. Needed when the folder name is longer than 15 characters (the engine
  cuts it and the race crashes), has characters other than a-z 0-9 _, or is a stock track's name (its files would
  be overwritten). The converter refuses such a name and tells you.
* `--ambient-from <HS track>` or `none`: whose ambient sound the track gets. Default: the one `Mixer.ini` names,
  else none.
* `install_ai_data.py` has tuned AI data for the 19 High Stakes tracks only; a new track races on its own line.
* The first conversion writes `data/atmosphere/<track>.ini` from its `tr.ini`. Tune it in the editor
  ([editor.md](editor.md)); do not commit it for a track that is not yours to publish.

Then race it exactly as in [tutorial.md](tutorial.md) step 2, with `track_name=nbhdrive`.

## Tested on: Neighborhood Drive

A circuit by Zalcus20 on NFSAddons, made with T3ED 4.3 and 4.4 (2025), converted and raced in nfs5-web on
2026-11-23:

```
NeighborhoodDrive ambience of Uk (Mixer.ini "Based On"); --ambient-from changes it
textures: 39.0 MB is over the 20 MB a track can use; 304 halved, now 20.0 MB
NeighborhoodDrive walls=3041 overpass=0 finite=0 objects=2572 inset=0.00
NeighborhoodDrive circuit  slices 1400  articles 1616  materials 746  primitives   8666  lamps 0/140  sounds 73/31 banks
```

In nfs5-web (`fe=0 track_name=nbhdrive car[0].aidrive=1`, 90 s): `[hs] track 'nbhdrive' walls
'.\gamedata\track\nbhdrive.hsw': 5613` (FRD walls plus object walls), 144 frames/s, 600-750 draws a frame, 6 wall
hits with the AI driving, no error. The only message box is the known `fe=0` HUD font assert ([tutorial.md](tutorial.md)
step 2).

It took five fixes, each one a rule for any community track:

| symptom | cause | fix |
|---|---|---|
| `unsupported pixel format 0x78` | 531 of its 741 textures are RGB565; the 19 stock tracks use only `0x7B`/`0x7D`/`0x7E` | `nfs5_fsh.decode_direct`: `0x78`, `0x7F`, `0x6D` (`test_fsh_codes.py`) |
| `KeyError: 'neighborhooddrive'` in the ambience | High Stakes keeps ambience tables for its own 19 tracks only | borrow a stock track's: `Mixer.ini` `Based On`, or `--ambient-from` |
| `ERROR - Couldn't load track: 'neighborhooddri'`, then a crash | the engine keeps 15 characters of a directory name | `--directory`; the converter checks every name |
| `TEXTURE_converttype - UNABLE TO CONVERT TO TARGET TEXTURE TYPE`, then a crash | 654 textures, 39 MB as ARGB8888 with mips; the driver gives the game 32 MB for everything | `nfs5_fsh.fit_budget`: the largest textures are halved until the track fits 20 MB |
| in nfs5-web the race ran without walls (cars through them) and without the converted-track AI | the runtime knew converted tracks by a list of the 19 High Stakes names | nfs5-web (2026-11-23): a track with a `.hsw` beside its CRP is converted; [runtime.md](runtime.md) §4 asks every runtime to do the same |

## Limits

* Ambient sound: the borrowed track's loop zones are placed by slice number, so on a different road they fall in
  different places; the community track's own object sounds (the HZ3 block of `animdefs.txt`) are not built.
* Textures over the 20 MB budget lose resolution, largest first.
* No night or weather variant: PU's night and weather come from `data/atmosphere/<track>.ini`.
* AI: no tuned `.aispeed`/`.aiprofile`, so bots are slower and touch more walls than on the 19 tuned tracks
  ([pipeline/ai.md](pipeline/ai.md) explains the tuning; its race loop runs on the PC wrapper).
