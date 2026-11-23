# Glossary, coordinates and units

The words the code, the notes and the runtime use, with the file or note where each one is defined. If a term in a
research note is not here, the note defines it itself.

## Coordinates and units

| | |
|---|---|
| World unit | **metre**, in both games (HS1 §1: a car is ~1.9 x 1.3 x 4.5 m in each). The converter does not rescale. |
| Axes | **Y up**, Direct3D **left-handed**, in both games. Positions are copied as they are. |
| To Blender (Z up, right-handed) | game `(x, y, z)` -> Blender `(x, z, y)`; one swap of two axes also flips the handedness, so nothing is mirrored. UV: `v -> 1 - v`. This is what `export_track_fragment.py`, `export_hs_walls.py` and `export_ai_lines.py` write. |
| To OpenGL / glTF (Y up, right-handed) | negate X on positions and normals, `v -> 1 - v` (T007 §karT). |
| Slice spacing | NFS4 vroad: ~4.6 m (4.61-4.63). PU's own tracks: 6.0 m. AI lookahead is in slices, so converted tracks scale it by 1.3 (AI1). |
| Chunk | 8 slices. NFS4 block `b` becomes the CRP articles at slice `b * 8` (HS5). |
| Speeds in the engine | m/s (`car+D60`, the planner's target `car+EB8`; AI1). `.key` speeds are an internal unit, 149...600 on PU's tracks; the converter maps NFS4 `spd*.bin` bytes onto it with the scale calibrated in AI1. |
| Animation time | `Anqt` keyframes: 0.1 s per key (HZ2 in HS12). |
| Sky angles | in **turns** (0.25 = 90 degrees) in `data/atmosphere/<track>.ini` ([formats/atmosphere-ini.md](formats/atmosphere-ini.md)). |
| Along-the-road data | indexed by **slice** (global slice index): `.key`, `.cop`, `.map` sections, `Sky/*.bin` keys, `.aispeed`, `.lsp`. |

## Names of a track

| name | example | where |
|---|---|---|
| NFS4 folder | `Hometown`, `Coastal`, `NeighborhoodDrive` | `Data/Tracks/<folder>/tr.frd`; what you pass to `convert_track.py` |
| PU directory (`<track>`) | `hometown`, `dolphin` | lower case folder name (NFS4 `Coastal` -> `dolphin`); prefix of every file in `GameData/Track/`; **<= 15 characters**, a-z 0-9 _ (the record field has 20 bytes, but the engine keeps 15: a longer name fails to load); `convert_track.py --directory` sets it |
| `<Track>` | `Hometown` | capitalised directory: `Sky/<Track>.*`, the environment name in the track record |
| menu name | `Hometown`, `Dolphin Cove` | `HS_TRACK_NAMES` in `nfs5_frontend.py`; an unknown folder keeps its own name |
| track id | 15, 17, 19... | `nfs5.trk` record +0; free list 15, 17, 19-24, 27, 28, 31, 33, then 35+ |
| name id | 0x2000 + n | `nfs5.trk` record +4; answered by the runtime's `GetString` hook from `nfs5ext.names` |

## Terms

| term | meaning | defined in |
|---|---|---|
| **article** | a named object in a CRP: `RD%04dC (%d PRIMARY)` road chunk, `CNK%04dL/R/E/B` terrain beside it, `OBJECT##`, `SHRUB##`... | HS1 §4.1 |
| **Anim / Anqt** | animation parts of an article (52-byte keys: quaternion, position, scale) | HS12 |
| **animdefs.txt** | `GameData/Track/animdefs.txt`: collision type, sound and mass of track objects by 4-letter tag; the converter appends an HZ3 block | T007, HS12 |
| **bank (sound)** | an EA `BNKl` sound bank; PU track banks live in `track.viv`, numbered < 1000; converted ones get numbers from `data/ambient/pu_bank_ids.json` | HS13, HS15 |
| **Base** | the part of an article with its bounding box and LOD table | HS1 §4.3 |
| **block** | NFS4 `TrkBlock`: 8 vroad slices of geometry, objects and lights | HS1 §3 |
| **branch** | a route through a PU track; stock tracks have 2-18, a converted track one (`PRIMARY`) | HS1 §4.1 |
| **chunk** | 8 slices of one branch; one road article and its terrain articles | HS1 §4.1 |
| **circuit / point-to-point** | told apart by `.jnc` (a dead end = one non-zero float) and the `nfs5.trk` flag `0x10000`. A circuit has no `_fstart/_bstart.scn`. All 19 HS tracks are circuits | T007, HS6 |
| **corridor** | the drivable width per slice the AI and the off-track respawn use (the SimD walls); `track_corridor.py` narrows it to the real walls | AI1 |
| **CRP / karT** | `<track>.crp`: the track's geometry archive (magic `karT`), usually RefPack-compressed | T007 §karT, HS3 |
| **df** | per-vertex baked light colour of a primitive; the atmosphere's `light_tint` is multiplied into it | HS1 §11.2 |
| **DRP** | an extra uncompressed CRP-format file some stock tracks have (cones, finish line models); not needed | T007 |
| **`.edg`** | AI and camera barrier polylines, 28-byte segments with flags | T007, HS6 |
| **extended content** | the runtime mode that swaps in `nfs5ext.trk`, the name table and `trackext.viv` | [runtime.md](runtime.md) §1-3 |
| **FRD** | NFS4 `tr.frd`: blocks, polygons, vroad, objects, XObj, lights, sound sources. Read by `nfs4_frd.py` | HS1 §3 |
| **FSH / SHPI, QFS** | EA texture archive; QFS = the same RefPack-compressed. Pixel codes: `0x7D` ARGB8888, `0x7E` ARGB1555, `0x6D` ARGB4444, `0x78` RGB565, `0x7F` RGB888, `0x7B` 8-bit with a `0x2D` palette | HS2, T007 |
| **`.hsw`** | NEW file: the High Stakes polygon walls for the runtime's collision | [formats/hsw.md](formats/hsw.md), HS11 |
| **`.jnc`** | junctions of the branches (36-byte records; loaded as `C<track>.jnc`) | T007 |
| **`.key`** | AI speed keys along the slices (forward and `_backwards`), 3208 bytes | T007 |
| **LOD** | level of detail; a CRP's last LOD distance must be 10000 | HS1 §4.3, HS9 |
| **LSP** | racing line, `racer/work/<track>0.lsp` (forward) and `<track>1.lsp` (reverse) | HS6, AI6 |
| **`.map`** | reflection/environment and tints by slice range, not the road textures | HS1 §4.2 |
| **material / `mt`** | CRP MiscData record of a material; the surface type (asphalt, dirt, snow...) is the low nibble of its `mt+0` word | HS9 §7.6 |
| **MiscData** | the CRP's table of non-article records (`mt`, `SimD`, `RMth`, `sn`...); `sn` must be the last | HS3 |
| **mod layer** | the folder nfs5-web lays over the game folder (`--mod`), built by its `tools/make_mod_layer.py` | [tutorial.md](tutorial.md) |
| **nfs5ext.trk** | NEW: the stock `nfs5.trk` records + one per converted track, 1192 bytes each | HS8 |
| **part** | a typed block of an article: `vt` vertices, `nm` normals, `uv`, `df`, `pr` primitives, `tr` transforms, `Base`... | T007, HS3 |
| **pr (primitive)** | a triangle list with **byte** indices: at most 256 vertices each, which is why the world is cut small | HS1 §4.4 |
| **RefPack** | EA's LZ compression (`10 FB` header) used by `.crp`, `.fsh`, `.qfs` | T007 |
| **runtime** | whatever loads the new files into the game: the PC DX11 wrapper or nfs5-web | [runtime.md](runtime.md) |
| **`_audio.scn`** | the track's ambient sound elements (`AUDIO_ELEMENT`) | T007, HS13 |
| **SimD** | per-slice simulation records in the CRP MiscData (centre, direction vectors, wall widths; stride 0x50): PU's own road model, read by `FUN_00480080` and used by the AI and the respawn | HS9, AI1 |
| **Sky bin / atmosphere** | `Sky/<Track>.bin`: fog, sky colour, rain/snow keys by slice (<= 400 keys per table), written from `data/atmosphere/<track>.ini` | ATM1, [formats/sky-bin.md](formats/sky-bin.md) |
| **slice** | one step along the road: a vroad point. The *global slice index* runs over all branches | HS1 §4.1 |
| **smackable** | a track object that flies off when hit (`kSmackable` in animdefs) | HS12 |
| **spdfa / spdra** | NFS4 `spdfa.bin`/`spdra.bin`: AI speeds, a helper array and the racing line offset per slice, forward / reverse | HS6 |
| **`.tan`** | NEW: animated texture table for the runtime | [formats/tan.md](formats/tan.md), HZ4 |
| **THRASH** | PU's renderer driver interface (`Drivers\*.dll`); the PC wrapper is a THRASH driver | [runtime.md](runtime.md) |
| **tram bank** | NFS4 `Data/Audio/Sfx/tram%02d.bnk`, a track's ambience bank (Hometown 0 ... Uk 14 ... Gt1-3 17) | HS13 |
| **trackext.viv** | NEW: the stock `track.viv` banks + the banks of all converted tracks | HS13 |
| **vroad** | NFS4 "virtual road": per slice a reference point, forward/normal/right vectors, lane width and left/right wall distances | HS1 §3 |
| **XObj** | NFS4 extra objects (types 1-4; type 3 animated, type 4 special) | HS5, HS12 |
