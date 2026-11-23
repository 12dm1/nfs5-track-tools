# HS12: movable NFS4 objects (XObj type 3), destructibles, HZ2 global objects

Date: 2026-09-19 (research), 2026-09-20/21 (destructibles), 2026-10-18 (HZ2, section 8). Sources: all 19
`tr.frd`, `tools/track/nfs4_frd.py`, `tools/track/nfs4_geometry.py`, `tools/track/nfs5_crp.py`,
`GameData/Track/{alps,autobahn,castle,industrial}.crp`, [HS1](HS1-nfs4-track-migration.md) section 3,
[T007](T007-pu-track-formats.md) Anqt, [HS5](HS5-geometry-conversion.md).

---

## Conclusion

In NFS4 "cars on the neighbouring road" and "the train in Kindiak Park" are **the same container**: XObj **type 3**
with an array of keys `(position 16.16, quaternion int16)`. Not a separate format, not AI, not race traffic.

PU already has the receiver: the CRP parts `Anim` + `Anqt` (52 bytes per key: quat float4, pos float3, scale float3).
Stock Autobahn runs **a locomotive + 5 carriages** (`Locomotive`, `carrage01`...`05`) on 251 keys, a ~2.4 km closed
path. The Park train is the same class of data, just two trains and more keys.

At the time of the research the converter wrote no animation and baked type 3 into the pose of key 0, and objects of
the **global** FRD chunks (where the Park train lives) were not written at all. **HZ2 (section 8) fixed both.**

---

## 1. Where the moving objects live

The FRD holds XObj in two places:

* four chunks per block (`nobj[4]`), bound to a piece of the track;
* two global chunks at the end of the file (`nGlobal0`, `nGlobal1`).

Type 3 is almost always in **global0**. Exceptions: Uk (4 cars in blocks 41 and 44), Hometown (1 spinner in block 75 +
1 path in global0), Country (1 spinner in a block), Snowy (2 spinners in block 1 + paths in global0).

Type 3 / type 6 inventory over all 19 (counts match HS1: Park 10 anim, Uk 4, Gt* special, not anim):

| track | type 3 | where | type 6 (physics, not a path) |
|---|---|---|---|
| Park | **10** | all g0 | 1 |
| Snowy | 9 | b1 + g0 | 16 |
| Empire | 5 | g0 | 0 |
| Uk | **4** | b41, b44 | 12 |
| Lostcany | 3 | g0 | 0 |
| Aquatica | 2 | g0 | 0 |
| France | 2 | g0 | 6 |
| Hometown | 2 | b75 + g0 | 0 |
| Summit | 2 | g0 | 0 |
| Atlantic, Coastal, Country, Gt3 | 1 each | | Gt3: 103 special |
| Germany, Gt1, Gt2, Hills, Redrock, Rockypas | 0 | | Gt1/Gt2: special only |

Two kinds of keys:

* **PATH**: the position moves along a spline (train, cars, plane/boat).
* **SPIN**: the position stays, the quaternion turns (Hometown windmill, Snowy/Country fans, Coastal).

---

## 2. Kindiak Park: the train

10 animated objects, all `g0`, `atype=3`, `delay=6`. Two trains of 5 meshes.

| group | keys | path length | loop gap | span XYZ, m | starts (about) |
|---|---|---|---|---|---|
| A (aid 1,2,3,4,8) | 374 | ~1211 m (aid=2 shorter, 665 m) | 0.6 m on four | 545 / 31 / 164 | vroad ~328-342, 18-32 m from the centre line |
| B (aid 5,6,7,9,10) | 441 | ~1342 m | 0.0 m | 576 / 53 / 252 | vroad ~738-756, 18-30 m from the centre line |

Meshes repeat within a train: 27 v / 14 p (carriage), 49/21, 78/47, plus B's locomotive 135/104. The start points of
train A are 18 / 18 / 34 / 54 m apart: carriages on one thread.

The keys of a train are **an offset along the same polyline** (alignment to the nearest point of another path:
offset 10, 21, 33, 363 frames; mean error of the carriages 0.2-0.9 m). The "locomotive"/special mesh has max errors of
hundreds of metres: another thread at a passing loop, or a shortened path record (aid=2).

Not the race spline: 18-32 m beside vroad, 31-53 m height range. The two groups start at different places of the
loop (~1740 vs ~2600): two trains or two directions.

---

## 3. UK: the cars

4 type-3 objects in blocks 41 and 44 (not global). 15 / 18 / 23 / 26 keys, delay 32 / 25 / 19 / 12, path 63 / 62 /
68 / 115 m. The quaternion **does not change** (spin=0): constant orientation, only the position moves.

Relative to vroad (projection onto `right`): height stable, **up ~ 6.2-6.8 m** above the slice centre; `along` ~ 0:
the motion is almost within one cross-section, not along the player's track; `lat` goes from ~+30 m to ~-30 m
(aid 11, 14) or +60 to -53 (aid 24).

So: **short shuttles crossing the race ribbon** from a neighbouring road (side gap ~30 m at the start, like a second
carriageway). `loop_gap` equals the path length: the keys are not closed. Looping is an NFS4 engine behaviour, not a
closed spline.

---

## 4. The PU receiver (in stock)

Anqt key (T007), 52 bytes: quat, pos (world), scale (usually 1,1,1), 12 bytes tail.

From stock CRPs:

* `Anim` type_byte `0xEA`, part Count = **1**, 16-byte payload: the first uint32 = **the Anqt key count**, the rest
  zero (Alps `RESCUEHELI`: 101).
* `Anqt` Count = key count, length = count x 52.
* Stock key budgets: helicopter 101, castle van/pickup `ANIVAN01`/`ANIPICKUP` 101, industrial crane 201-399,
  **autobahn train 251**.
* Autobahn: `Locomotive` + `carrage01`...`05`, path 2257-2405 m, loop gap 0, spans of hundreds of metres: the same
  pattern as Park, on a PU track.

The karT writer passes `Anim`/`Anqt` through as `raw_parts` (alps round trip).

An animated PU article is the opposite of the old conversion: `vt` without the world offset, the world in `Anqt`
(CrpParser: do not apply `tr` on top of the keys). Appending Anqt to vertices with a baked offset would move the
object by the sum of both transforms.

AABB: on Hometown a type 3 with garbage in header+12 inflated the tree to 1e14; the pose must come from key 0, not
from the header (HS9 6.5).

---

## 5. Field mapping NFS4 -> PU

NFS4, 8-byte preamble + 20-byte key:

```
uint16 unk, uint8 atype, uint8 aid, uint16 nKeyframes, uint16 delay
key: int32 x,y,z (/65536), int16 qx,qy,qz,qw (/16384)
```

PU:

```
Anim payload[0:4] = nKeys
Anqt[i]: float4 quat, float3 pos, float3 scale=1, 12 bytes ?
```

`aid` tells the carriages apart; in PU they are separate articles. `atype` is 3 on every parsed PATH/SPIN.

Still open: collision of a moving article with the player's car (the converted animated objects have none, like the
Alps helicopter); type 1 in the FRD outside the global chunks (Uk 12, Park 1, Coastal 7...).

---

## 6. Type 6

Special physics with a 72-byte mass/transform/dimensions block; **not** a general "can be knocked over" marker. The
exact destructible marker of a normal XObj is `RefExtraObject.collision_type` (7.6). HZ2 turns type 6 into smackables
(8.4).

---

## 7. PU destructible objects: the working sign transfer

Experiment date: 2026-09-20, in the original PC format on converted Hometown. A separate branch from type 3: it needs
the stock PU library of destructible object definitions, not `Anim`/`Anqt`.

### 7.1 Minimal working structure

A working destructible sign consists of:

* a placed CRP article with `Base` flags `0x0601`;
* four bytes `SIGN` at `Base+0x44`;
* the stock definition articles from Alps with flags `0x86xx`, including the definition with the tag `SIGN`;
* the stock `Desc` MiscData from Alps;
* `animdefs.txt`: `SIGN`, `collideType=kSmackable`, `mass=80`;
* the usual `Name`, `tr` and geometry `pr`/`uv`/`vt` of the placed article.

The full stock definition library must be kept. With only the placed article the engine creates no smackable
(`numSmackables=0`). Replacing the stock definitions with our own `SIGN` or a new tag `U001`/`CONE` loaded partly
visually but registered no destructibles. So the stock `SIGN` dispatch is used, not a new physics type.

### 7.2 Why a second sign stayed after a hit

The destructible article was added on top of the converter's normal static `OBJECT<slice>_<index>` article. After a
hit the dynamic object flew off but the static article kept drawing. Fix: drop the static source article for every
transferred XObj and keep only the dynamic copy.

### 7.3 Hitbox and misses at speed

The stock `SIGN` physics shape is fixed and much narrower than the visual geometry. A runtime probe showed the same
half extents for every placed object, about `(0.757, 1.588, 0.162)` m. A sign can look like it blocks the line while
the narrow hitbox passes beside the car. In stock PU placed signs carry an individual yaw in `tr`; identity for every
converted sign is not equivalent.

Correction:

1. the main axis of the sign is computed from the horizontal XObj geometry;
2. the local mesh is rotated into the canonical orientation of the stock `SIGN`;
3. the same yaw is written into `tr`, so the visual mesh stays where it was and the stock hitbox turns with the sign.

### 7.4 Hometown inventory

Transferred from the Hometown FRD: 46 road signs (textures `9..17`), 2 special signs at the boulevard island
(`151/152`), 8 wooden panels (`32`). Each panel has four small dark elements (`31`, frame/posts); instead of 32
independent physics objects they are merged with their panel into one of 8 dynamic assemblies. The static source
articles of panel and frame are removed. Result: 56 dynamic assemblies, runtime probe `expected=56 registered=56`
(88 FRD XObj with the exact flag: 48 signs, 8 boards and 32 frame parts).

### 7.5 Still to check

* miss rate after the yaw correction at low and high speed;
* a separate box definition for panels and signs if needed;
* sound types: the panels use `SIGN` physics, so the sound is the stock "metal sign", not `kAudWoodBox`;
* UK/redrock fences, the phone box, snowy snowball, hometown tunnel after inventorying their XObj.

### 7.6 The exact destructible marker in NFS4

Date: 2026-09-21. No more texture heuristics. The last byte of each block's `RefExtraObject` record is
`collision_type`:

```text
0x00 none
0x01 static
0x02 destructible
0x03 unknown
```

Confirmed by `speedtools/specs/frd.ksy`; speedtools adds destroy low/high-speed actions only to objects with the enum
`destructible`. `XObjHeader.attribute_index` (the second `uint32` of the 52-byte header) indexes this table directly
for normal XObj type 4.

Do not apply this index to type 2. Billboard/polygon objects belong to the separate table `RefExtraObject2`; on Coastal
their header field is often zero. Indexing the first table wrongly declares whole groups of trees destructible. On the
tracks examined, the real `collision_type=2` objects are type 4.

Normal XObj with `collision_type=2`:

| track | destructible | track | destructible |
|---|---:|---|---:|
| Aquatica | 35 | Atlantic | 29 |
| Coastal / Dolphin Cove | 83 | Country | 114 |
| Empire | 127 | France | 29 |
| Germany | 37 | Gt1 / Gt2 / Gt3 | 0 / 0 / 0 |
| Hills | 24 | Hometown | 88 |
| Lost Canyons | 176 | Park | 45 |
| Redrock | 188 | Rocky Pass | 44 |
| Snowy Ridge | 118 | Summit | 58 |
| UK | 108 | **total** | **1303** |

### 7.7 Automatic transfer in the converter

`nfs4_frd.py` decodes `RefExtraObject`, keeps `attribute_index` and sets `collision_type` on normal XObj. For each
`collision_type == 2`, `nfs4_geometry.py`:

1. creates no static copy;
2. keeps the mesh local, computes the horizontal yaw and writes a world `tr`, aligning the narrow PU `SIGN` hitbox
   with the plane of the source object;
3. writes placed `Base` flags `0x0601` and the `SIGN` dispatch at `Base+0x44`;
4. adds once, from Alps, the full library of 70 definitions with `0x86xx` and 59 `Desc` records.

The regression test rebuilds Hometown and Coastal, compares all geometry, checks that the number of source
`collision_type=2` equals the number of placed `SIGN` and that the definitions are present. `dolphin.crp` has 83
placed `SIGN` without static duplicates. The total is capped by `MAX_DESTRUCTIBLE` = 119 per track.

---

## 8. HZ2, 2026-10-18: global objects and animation

User report: on France (Route Adonf) trees hang over the abyss with no rock under them, the balloon does not rise.
Same on other tracks.

### 8.1 Root

`build_articles` walked only `track.objects(block)`. The two global chunks at the end of `tr.frd` never reached the
CRP. They hold:

| type | what | example |
|---|---|---|
| 1 | large static meshes, vertices already in world space (`pt` = 0) | France: 6 rock pillars 50-95 m (x 346-454, z 210-721, y 101-211), Germany/Hills: slopes up to 500 m, UK 12, Coastal 7 buildings |
| 2 | cards | Germany 2, Coastal 1 |
| 3 | animation | France balloon, Hometown plane, Summit cable cars, Park trains |
| 6 | small physics objects (72 bytes: mass, 3x3, dims) | Gt1 95, Gt3 103, Gt2 49, Snowy 16, UK 12, Hills 12 |

### 8.2 Animation in PU (checked in the code)

* `FUN_004777f0` creates a player for each article with `Anqt`:
  `FUN_00471490(count, keys, 0, flags=1, step=0x3DCCCCCD (0.1 s), 0)`. The key step is fixed at 0.1 s; flags bit0 =
  loop.
* `FUN_00470E90`: frame = time / step; looping uses `frame % (n - 2)` and interpolates to `frame + 1`, so the last key
  is never a start key.
* `FUN_00470F70`: quaternion slerp, position, lerp of three floats at +0x20. A 52-byte key = quat xyzw, pos xyz, 1.0,
  scale 1,1,1, 8 bytes of tail (stock files have memory garbage there).
* `FUN_0048A1E0` quat -> 3x3: row 0 = (1-2(y^2+z^2), 2(xy+wz), 2(xz-wy)). With D3D row vectors that is a normal
  active rotation, the same convention as `nfs4_frd._rotate_quat`. The NFS4 quaternion goes in unchanged.
* The article is like the stock ANIVAN01 / Autobahn train: Base `0x0610`, one LOD, local mesh, no `tr`, the world only
  in `Anqt`; `Anim` = (nkeys, 0, 0, 0).

### 8.3 NFS4 timing

`delay` = key duration in 1/64 s. **Inferred from speeds**, the NFS4 code was not read: Park train 1.8 m/key at delay 6
= 68 km/h, Hometown plane 12.7 m at 25 = 117 km/h, Summit cable car 4 m at 38 = 25 km/h. At 1/32 the plane would fly
58 km/h. Constant `NFS4_ANIM_TICKS`.

`anim_frames` resamples the keys onto the 0.1 s grid over key 0 ... n-1. Key m = key 0 closes the loop, key m+1 is
padding. Open paths (France balloon, Snowy chairs) return to the start within the last 0.1 s. The longest clip is the
Aquatica boat, 2342 keys (234 s); it loads fine.

### 8.4 Type 6

The 3x3 matrix is read by rows (world = x*row0 + y*row1 + z*row2). That puts the four Germany benches exactly across
the road; read by columns they come out 45-90 deg off. The objects become `SIGN` smackables in the shared
`MAX_DESTRUCTIBLE` = 119 budget, **after** the signs. No sign that was dynamic drops out. The rest stay static (Snowy
15, UK 1). Game counter (PC wrapper `[content] trace_smackables=1`): Gt3 103, Gt1 95, UK 119, Snowy 119, Germany 45,
Hills 36.

### 8.5 Result

+50 animated articles, +74 global static ones, ~380 new smackables (Gt1/2/3 had 0). All 19 CRPs installed
(`convert_track.py --all --crp-only`). Hometown and Country had hand-tuned `Si` values (a few floats) in the installed
CRP; they were carried over byte for byte. race_test 20 s, full render, all 16 tracks run PASS.
`test_hs5_geometry` matches triangles and smackables; the old material difference (+8 effect tags, `FullAmbient`)
was there before HZ2.

Open: confirm the `delay` speed by eye; animated objects have no collision (like the Alps helicopter).
