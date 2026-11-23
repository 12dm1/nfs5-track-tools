# HS1: migrating NFS4 High Stakes tracks to NFS5 Porsche Unleashed

Date: 2026-09-18. Research only, nothing patched. Later stages (HS2..HS19, HZ1..HZ7) built on it; where this
document says "open", check the later findings first.

Sources: `NeedforSpeedHighStakes/Data/Tracks/*` (19 tracks), `GameData/Track/*`, `FEData/Data/nfs5.trk`,
[T007](T007-pu-track-formats.md), `NFS5WorldExporter` (`CrpParser.cs`),
[LibOpenNFS](https://github.com/OpenNFS/LibOpenNFS) (`NFS4/PC/FRD/*`), the string table of `PORSCHE.EXE`.
Research tools: `tools/track/ea_refpack.py`, `tools/track/nfs4_frd.py`, `tools/track/nfs5_crp.py`.

---

## Conclusion

**The migration is real and structurally cheaper than it looks.** NFS4 and NFS5 use the same EA Canada track
engine, two container generations apart. Exactly the things that usually kill such ports match:

| what | NFS4 (HS) | NFS5 (PU) | match? |
|---|---|---|---|
| World unit | metre (P911 = 1.90x1.30x4.57 m) | metre (player half extents 0.95/0.63/2.15) | **yes** |
| Axes | Y up, Z along, left-handed | Y up, D3D left-handed | **yes** |
| Road topology | vroad point, block = 8 points, step 4.62 m | slice, chunk = 8 slices, step ~5.9 m | **yes** |
| Lighting | baked, uint32 D3DCOLOR per vertex | baked, D3DCOLOR per face corner (`df`) | almost |
| Textures | SHPI (FSH) inside RefPack (`.qfs`) | SHPI (FSH) inside RefPack (`.fsh`) | **yes** |
| Compression | RefPack `0x10FB` | RefPack `0x10FB` | **yes** |

Only the packaging differs: **FRD (a flat dump of structures) vs karT CRP (an article/part tree)**, and NFS5 moved
collision (`.edg`), AI routes (`.key`/`.jnc`), triggers and cameras (`.scn`) into separate files, where NFS4 kept
all of that inside the FRD and `spd*.bin`.

The main work is a **CRP writer** (only readers existed anywhere) and rebuilding the road geometry into the NFS5
slice scheme. Everything else (textures, collision, AI, light, sound, cameras, sky, cops) maps almost mechanically.

Estimate at the time: one working track (geometry + textures + collision + start/finish + AI) is realistic; full
parity with the original (weather/night, animated textures, branches/shortcuts) is not, part of it has to be cut
or done in the runtime. (Status 2026-11: all 19 tracks drive; animated textures, global objects, walls,
ambient sound and AI were done later, see the other findings.)

---

## 1. What was proven in practice, not from documentation

1. **An NFS4 FRD parser was written and matches byte for byte on all 29 files** (19 x `tr.frd` + 10 x `trn.frd`):
   after parsing, the read position equals the file size, `rest = 0` for every file. The structure is fully known,
   no unparsed tails. Script: `tools/track/nfs4_frd.py` (has a self test).
2. **The Python RefPack decoder works** on `.qfs` (NFS4) and `.fsh`/`.crp` (NFS5): the same codec.
   `tools/track/ea_refpack.py`.
3. **The world scales match.** From `Data/Cars/P911/car.viv -> car.fce` (FCE4, header `0x00101014`, vertex table
   at `0x2038`) the model is 1.90 x 1.30 x 4.57 m. In NFS5 the player's physics half extents are (0.95, 0.63, 2.15),
   i.e. 1.90 x 1.26 x 4.30 m. **No coordinate rescale is needed.**
4. **The grid step is almost the same.** NFS4: 4.61-4.63 m between neighbouring vroad points on all 19 tracks.
   NFS5: the `RD####C` chunk centres of alps give 10.54 km over 225 chunks = 47.1 m per chunk = **5.88 m per slice**.
5. **alps.crp parsed** (1752 articles, 286 MiscData): names, `Base`, the `pr` structure, material to `alps.fsh` tag
   binding (203 of 204 `mt` resolve).

---

## 2. Source inventory (NeedforSpeedHighStakes/Data/Tracks)

19 tracks. Numbers from our FRD parser (polygons = sum over the 11 LOD chunks, objects = all XObj including the two
global chunks):

| track | blocks | vroad | length, km | verts | polys | objects | anim | special | lights | sounds | textures | night |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Aquatica | 180 | 1434 | 6.63 | 47643 | 40954 | 64 | 2 | 0 | 128 | 3 | 175 | - |
| Atlantic | 166 | 1325 | 6.13 | 46211 | 37529 | 78 | 1 | 0 | 3 | 4 | 180 | - |
| Coastal | 135 | 1075 | 4.95 | 39019 | 39512 | 810 | 1 | 4 | 134 | 2 | 223 | yes |
| Country | 175 | 1399 | 6.45 | 39025 | 36519 | 166 | 1 | 0 | 26 | 8 | 177 | - |
| Empire | 154 | 1230 | 5.66 | 32366 | 30670 | 225 | 5 | 0 | 191 | 7 | 309 | - |
| France | 122 | 973 | 4.48 | 35324 | 35109 | 365 | 2 | 6 | 52 | 0 | 187 | yes |
| Germany | 136 | 1086 | 5.01 | 42248 | 33898 | 166 | 0 | 8 | 136 | 0 | 198 | yes |
| Gt1 | 104 | 832 | 3.83 | 30017 | 29509 | 445 | 0 | 95 | 242 | 0 | 141 | yes |
| Gt2 | 110 | 880 | 4.05 | 31584 | 30558 | 459 | 0 | 49 | 330 | 0 | 127 | yes |
| Gt3 | 119 | 952 | 4.38 | 34611 | 28810 | 338 | 1 | 103 | 269 | 0 | 156 | yes |
| Hills | 136 | 1087 | 5.01 | 36100 | 34995 | 314 | 0 | 12 | 82 | 0 | 156 | yes |
| Hometown | 120 | 959 | 4.42 | 33429 | 28115 | 162 | 2 | 0 | 22 | 6 | 186 | - |
| Lostcany | 212 | 1690 | 7.81 | 50644 | 54499 | 375 | 3 | 0 | 21 | 3 | 241 | - |
| Park | 118 | 939 | 4.32 | 39633 | 33999 | 567 | 10 | 1 | 22 | 0 | 183 | yes |
| Redrock | 183 | 1463 | 6.75 | 45023 | 42243 | 409 | 0 | 0 | 19 | 2 | 217 | - |
| Rockypas | 186 | 1483 | 6.84 | 48331 | 43272 | 280 | 0 | 0 | 24 | 1 | 193 | - |
| Snowy | 129 | 1029 | 4.74 | 35653 | 39824 | 615 | 9 | 16 | 28 | 5 | 207 | yes |
| Summit | 216 | 1723 | 7.94 | 59229 | 52303 | 308 | 2 | 0 | 50 | 3 | 196 | - |
| Uk | 105 | 839 | 3.86 | 30037 | 27093 | 603 | 4 | 12 | 87 | 2 | 169 | yes |

**Total**: 2806 blocks, 22398 slices, ~756k vertices, ~699k polygons, 6749 objects, 1866 light sources, 46 sound
sources.

For comparison, the NFS5 budget: alps LOD0 = 130 535 vertices / ~61.7k triangles, coastal = 106 703 / ~49k,
monaco1 = 92 032 / ~33k. So **every HS track fits the PU norm** (NFS4 polygons are quads: Hometown's 28k quads =
56k triangles).

NFS4 track folder:

| file | what | parse status |
|---|---|---|
| `tr.frd` | all geometry + collision + light + sound + objects | **fully parsed** |
| `trn.frd` | night variant of the same track (10 of 19) | same format |
| `tr0.qfs` / `trn0.qfs` | track textures: SHPI, PAL8 (8-bit palette), up to 128x128 | **parsed** |
| `sky.qfs` | sky/horizon: SHPI, ARGB8888, 64x64 and 128x128, 43 tags (`HDC0..7`, `CLDD`, `CHRN`, ...) | **parsed** |
| `tr.ini` / `trw.ini` / `trn.ini` / `trnw.ini` | day / rain / night / night+rain: horizon, clouds, fog by slice regions, ambient, light direction, car shadow, 32 "track glows" | text, readable |
| `heights.sim` | `float32[nVRoad]`, one value per slice (checked on all 19) | size confirmed |
| `spdfa.bin` / `spdra.bin` | size = `5*nVRoad + ceil(nVRoad/2)`: `ceil(N/2)` speed bytes, `N` helper bytes, then `N x float32` lateral offsets of the authored AI line | **speed and line understood** |
| `tr.cam` | `uint32 count + count x 68 bytes`: cameras (checked on 8 tracks) | size confirmed |
| `tr00.can ... tr06.can`, `tr00a.can` | camera animations; same sizes on every track, a shared template | not parsed |
| `tr.vis` | France only | not parsed |

---

## 3. `tr.frd` format (NFS4 PC), confirmed

The structure matches `LibOpenNFS::NFS4`; we re-checked it on the real files and found among other things that
`nVertices` in `XObjHeader` is at **+32**, not +28.

```
char      header[28]                  // first 4 bytes: 58 00 EF BE
uint32    nBlocks                     // the file stores N-1; the real N = +1
uint32    numVRoad                    // invariant: (numVRoad+7)/8 == nBlocks
VRoadBlock     vroad[numVRoad]        // 84 bytes
TrkBlockHeader hdr[nBlocks]           // 1512 bytes
TrkBlock       block[nBlocks]         // variable length, sizes from hdr
uint32 nGlobal0; XObjChunk global0
uint32 nGlobal1; XObjChunk global1
```

### VRoadBlock, 84 bytes (the NFS4 "slice")

| off | type | field |
|---|---|---|
| 0 | float3 | `refPt`: road centre in world coordinates |
| 12 | float3 | `normal` (up) |
| 24 | float3 | `forward` (along the track) |
| 36 | float3 | `right` (across) |
| 48 | float | `leftWall`: distance to the left barrier (minimum over tracks 7.6-7.9) |
| 52 | float | `rightWall` |
| 56 | float[2] | lane / drivable shoulder width (3.0...6.0) |
| 64 | uint32[5] | not decoded (the first is usually `0xFFFFFFFF`) |

The axes are orthonormal (`dot(forward, right) ~ 0`), values are quantised to 1/128, a legacy of NFS3 where they
were `int8`.

### TrkBlockHeader, 1512 bytes

| off | field |
|---|---|
| 0 | `uint32 sz[11]`: polygon count of the 11 chunks (below) |
| 44 | `uint32 ptr[11]`: **memory pointers of the original build** that ended up in the file. Useful as a checksum: `ptr[i+1] - ptr[i] == sz[i] * 13` |
| 88 | `nVertices, nHiResVert, nLoResVert, nMedResVert, nVerticesDup, nObjectVert` |
| 112 | 2 pointers (vertices / shading) |
| 120 | `float3 ptCentre` |
| 132 | `float3 ptBounding[4]` |
| 180 | `int16 nbdData[300][2]`: neighbour blocks |
| 1380 | `NumObjs nobj[4]`: four XObj chunks per block |
| 1412 | `uint32 nPolygons`: `POLYVROADDATA` records |
| 1416 | `float3 ptMin, ptMax` |
| 1444 | `uint32 nPositions` |
| 1448 | `nXobj, nPolyobj, nSoundsrc, nLightsrc` (each a `num/unknown` pair) |
| 1480 | `uint32 neighbors[8]` |

Polygon chunks (`sz[0..10]`): `0 LOW_RES_TRACK, 1 LOW_RES_MISC, 2 MED_RES_TRACK, 3 MED_RES_MISC, 4 HIGH_RES_TRACK,
5 HIGH_RES_MISC, 6 LANES, 7..10 HIGH_RES_MISC1..4`. For the migration LOD0 = chunk 4 (road) + 5, 7..10 (scenery) +
6 (lane markings). Chunks 0-3 are ready-made LOD1/LOD2 and go straight into `vt/uv/pr` with LOD indices 1 and 2.

### TrkBlock: the block body

```
float3        vertices[nVertices]
uint32        shading[nVertices]        // D3DCOLOR: BAKED LIGHT
POLYVROADDATA polyVroad[nPolygons]      // 24 bytes: hs_minmax[4], flags[5], _, uint16 vroadEntry,
                                        //           int16 normal[3], int16 forward[3]
RefExtraObject  xobj[nXobj]             // 20 bytes: int3 pt, uint16 _, uint16 globalno,
                                        //           uint8 _[3], uint8 collision_type
                                        // collision_type: 0 none, 1 static, 2 destructible, 3 unknown
RefExtraObject2 polyobj[nPolyobj]       // 20 bytes: uint16 _, uint8 type, uint8 id, int3 pt,
                                        //           uint8 crossindex, uint8 _[3]
SoundSource   sound[nSoundsrc]          // 16 bytes: int3 refpoint, uint32 type
LightSource   light[nLightsrc]          // 16 bytes: int3 refpoint, uint32 type
Polygon       poly[11][sz[i]]           // 13 bytes each
XObjChunk     chunk[4]                  // nobj[i].num objects each
```

### Polygon, 13 bytes (the key structure for the conversion)

```
uint16 vertex[4];   // quad
uint16 texture;     // texture_id = texture & 0x07FF -> INDEX of the entry in tr0.qfs
uint16 texflags;    // 0x8000 no backface cull, 0x0020 mirror Y, 0x0010 mirror X,
                    // 0x0008 invert, 0x0004 rotate, 0x0800 lane marking strip
uint8  animFlags;   // 0x20 end of row, 0x10 two-sided; used for animated textures (see HZ4)
```

**NFS4 stores no UVs at all.** Each quad maps the whole texture: base UVs `(1,1) (0,1) (0,0) / (1,1) (0,0) (1,0)`,
then `invert / rotate / mirrorX / mirrorY` from `texflags` are applied. The UVs are **generated
deterministically**: nothing is lost, it is just a different encoding.

### XObjChunk / XObjHeader, 52 bytes

```
uint32 type;          // 2, 4 = normal; 3 = animated; 6 = special (physics)
uint32 index; uint32 _;
float3 pt;            // position
uint32 size; uint32 _;
uint32 nVertices;     // <- offset +32
uint32 _[2];
uint32 nPolygons;     // <- offset +44
uint32 _;
```

Then by type:

* `3` (anim): `uint16 _, uint8 type, uint8 id, uint16 nKeyframes, uint16 delay, AnimKeyframe kf[nKeyframes]`
  (20 bytes: `int3 pt`, `int16 quat[4]`), then the geometry;
* `6` (special): 72 bytes (`float3 location, float mass, float transform[9], float3 collisionDimensions, ...`),
  then the geometry;
* the geometry is always `float3 v[nVertices]; uint32 shading[nVertices]; Polygon p[nPolygons]`.

---

## 4. New facts about NFS5 (adds to T007)

### 4.1 A track is a set of "branches", each cut into chunks of 8 slices

Article names in `.crp`:

* `RD%04dC (%d PRIMARY)`: the road surface. The first number is the **first slice of the chunk** (always a multiple
  of 8), the number in brackets is the **branch/route number**.
* `CNK%04dL/R/E/B (%d MATERIAL_NAME)`: terrain left/right of the same chunk (`E`/`B` = junctions, tunnels).
* `OBJECT##`, `MESH##`, `SHRUB##`, `PILON##`, `Box##`, `HYDRANT##`: separate scenery.

Branches and slices (checked):

| track | branches | chunks | slices (chunks x 8) | max slice in `.key` | `.jnc` records |
|---|---|---|---|---|---|
| alps | 2 | 259 | 2072 | 2104 | 6 |
| coastal | 2 | 180 | 1440 | 1419 | 6 |
| forest | 2 | 244 | 1952 | 1903 | 6 |
| foothills | 2 | 208 | 1664 | 1326 | 6 |
| farmland | 4 | 239 | 1912 | 1974 | 14 |
| canyon | 6 | 224 | 1792 | 1589 | 17 |
| castle | 8 | 412 | 3296 | 3428 | 24 |
| industrial | 10 | 285 | 2280 | 2343 | 26 |
| autobahn | 18 | 266 | 2128 | 3088 | 35 |
| skidpad | 1 | 36 | 288 | - | 1 |

The global slice index used by `.key`, `.cop` and `.map` is a running number over all branches. A converted track
simplifies it: **one `PRIMARY` branch, slices = the source `nVRoad`, chunks = `nBlocks`**, a minimal `.jnc`.

### 4.2 `.map` is NOT the diffuse road textures

Every `textureN` in `alps.map` resolves to a tag of **`alps.env`** (`1  `, `BT1 `, `BT2 `, `YT1 `, `YT2 `) plus the
special value `SKY ` (542722899). `.map` sets **reflection/environment and tints by slice ranges**; the road diffuse
lives in the CRP materials. The sections are sparse: `section0` is the default for the whole track, the others are
local overrides (tunnels, bridges). One section is enough for a new track.

The `.map` header (`tint / ambient / interior / lightdecal`) holds global light multipliers, the direct receiver of
`[light] AmbientRed/Green/Blue` from `tr.ini`.

### 4.3 `Base` part decoded

```
+0x00 uint16 version (0x0610 / 0x0611)
+0x0C uint32 LOD count
+0x14 float3 bboxMin
+0x20 float3 bboxMax
+0x2C float3 centre
+0x38 float  1.0
+0x3C float  sphere radius (written twice)       // half the bbox diagonal
+0x64 LOD table, 12 bytes per record:
        +0  uint32 marker (always 1)
        +4  uint16 lo, uint16 hi, uint16 lodIndex, uint16 distance
      3-LOD example: (0000,0FFF,0,150) (1000,1FFF,1,250) (2000,2FFF,2,65535)
```

Size: 112 bytes with 1 LOD, +24 per extra LOD (136 with 3). **Fully authorable.** (HS9 later found that the last
LOD distance must be 10000, not 65535, for converted tracks; the tests check it.)

### 4.4 `pr` (primitives): authorable

```
+0x00 uint32 infoFlags        // byte0 = D3DFILLMODE (3 = tri list)
+0x04 int16  materialIndex
+0x08 float3 centre; +0x14 float radius
+0x28 int32  nInfoRows; +0x2C int32 nIndexRows
+0x30 InfoRow[nInfoRows]      // 16 bytes: f0, int32 dataOffset, uint16 len, uint16 levelIndex
                              // levelIndex: 0 = vt, 2 = uv, 3 = df
      IndexRow[nIndexRows]    // 8 bytes: uint16, tag 'Iv'/'Iu', uint32 offset
      uint8 indices[...]      // BYTE indices -> at most 256 vertices per primitive
```

The byte indices are why NFS5 cuts the world into small articles. The converter must respect the limit when
chunking.

### 4.5 FSH in NFS5

* Every track entry is `0x7D` ARGB8888, typically 128x128, up to 256x256.
* **A full mip chain down to 1x1 is stored**: block size = `w*h*4 * 4/3 + 16`, aligned to 16 (87408 bytes for
  128x128).
* Byte `+15` of the entry header = `log2(size) << 4` (128 -> 0x70, 256 -> 0x80).
* `.fsh` and most `.crp` are RefPack-compressed, but `alps.crp` and every `.drp` are not -> **the engine takes
  both, a compressor is not required.**
* (Found later, 2026-10-14: textures must be square; non-square entries crashed Germany/Gt2/UK. The converter pads.)

### 4.6 Other

* `.edg`: segments average 12.07 m (median 11.67): a thinned polyline of the borders, not a per-slice grid. Flags:
  `0x00` road (60%), `0x30` (30%), `0x10`, `0x60`.
* `.key`: `uint32 ver=1, uint32 count, 400 x (uint32 slice, float speed)`; the last record is always
  `slice=100000, speed=600` (sentinel). Speeds 149...600.
* `.cop`: `uint32 count + count x 20` bytes, with a slice index (100, 300, 500, ...).
* `nfs5.trk`: in `propsI[0]` (offset 108) bit `0x10000` = circuit (set for monaco1-5 and skidpad, not for
  point-to-point). The low bits 0/3 are another track type flag.
* The engine enumerates `%s\*.crp` in the folder (string `'No *.crp files in %s'`), so a track may have several
  CRPs.
* Frontend art: `FEData/TrackArt/<dir>.fsh` (map), `<name>Sp.fsh` (splash), `<name>SpSm.fsh` (small splash); the
  names come from `nfs5.trk`.

---

## 5. Transfer matrix

| subsystem | source (NFS4) | target (NFS5) | how | difficulty |
|---|---|---|---|---|
| Road surface | `poly[4]` (+ `poly[6]` markings) per block | article `RD%04dC (1 PRIMARY)` with `vt/uv/df/pr/Base` | block -> chunk 1:1, quad -> 2 triangles | medium |
| Terrain / scenery | `poly[5]`, `poly[7..10]` | `CNK%04dL/R` | split by the sign of `dot(right, v - refPt)` | medium |
| LOD | `poly[0..3]` (low/med res) | `vt/uv/df/pr` with LOD 1 and 2 + distances in `Base` | direct | low |
| Plain objects | XObj type 4 + `RefExtraObject` | article `OBJECT##` | `pt` baked into world vertices; `XObjHeader.attribute_index` selects the `RefExtraObject` | medium |
| Billboard cards | XObj type 2 + separate `RefExtraObject2` | article `Object##_MID2`, `Base=0x4611` | crossed card: the source plane + a copy rotated 90 deg in yaw; the stock `HBillBoard` path | medium |
| Animated objects | XObj type 3 (`AnimKeyframe`: int3 pos + int16 quat[4]) | `Anim` + `Anqt` (52 bytes: quat float4, pos float3, scale float3) | unpack int16 -> float (HZ2: 0.1 s per key) | medium |
| Destructible objects | XObj type 4 + `RefExtraObject.collision_type == 2` | placed article `Base=0x0601`, `Base+0x44=SIGN` + stock definitions/`Desc` | the exact FRD flag, no texture heuristics | medium |
| Special physics | XObj type 6 (mass, collisionDimensions) | (HZ2: smashables via type 6) | not a destructible marker | high |
| Vertex light | `shading[nVertices]` (D3DCOLOR per vertex) | `df` (D3DCOLOR per **face corner**) | repeat the vertex colour for each face corner | **low** |
| Light sources | `LightSource{int3 16.16, uint32 type}` | `ef` part in the CRP + stock light `mt`/FSH | walk every `LightSource`, map Coastal types to PU 4CCs, keep the `0x101` LOD marker | medium |
| Surfaces (asphalt/dirt/snow) | low nibble of `POLYVROADDATA.flags[4]`, per polygon (11.3) | (HS9: the nibble of `mt+0` word) | see HS9 | was an open risk |
| Textures | `tr0.qfs`, SHPI PAL8 <= 128x128, addressed by index | `<track>.fsh`, SHPI ARGB8888 + mips, addressed by 4CC tag | PAL8 -> ARGB8888, generate tags, build mips | low |
| UV | implicit + `texflags` | explicit `uv` float2 | generate from the flags | low |
| Road collision | `vroad.leftWall/rightWall` + axes | `.edg` (28 bytes: flags + 2 x float3) | left/right polylines, thinned to ~12 m | low |
| Walls / barriers | `POLYVROADDATA.flags`, `RefExtraObject.collision` | `.edg` flags 0x10/0x30 (and later `.hsw`, HS16) | flag mapping | medium |
| AI route | `spdfa.bin` / `spdra.bin`, array 0 = speed per slice | `_forward.key` / `_backwards.key` (<= 400 points) | thin to key points + calibrate units | medium |
| Branches | `nbdData`, `PositionData.extraNeighbor1/2` | `.jnc` (36 bytes: idx + 8 float probabilities) | v1 = one branch, minimal `.jnc` | high (for shortcuts) |
| Heights | `heights.sim` (`float[nVRoad]`) | - | no receiver found in PU, probably not needed | - |
| Sound | `SoundSource{int3 16.16, uint32 type}`, 0-8 per track | `<track>_audio.scn` (`AUDIO_ELEMENT`), 101-318 per track | positions 1:1 + type -> PU sound_id (HS13 did the full job) | low |
| Replay cameras | `tr.cam` (uint32 count + 68 bytes) | `<track>_cameras.scn` (`CAMERA_ELEMENT` + trigger type=6) | the 68-byte record needs decoding | medium |
| Start / finish | from `vroad[0]` and `vroad[n-1]` + width | `_fstart.scn` / `_bstart.scn` (TRIGGER type 2 and 1) | generated from the slice frame | low |
| Sky | `sky.qfs` (43 tags, ARGB8888) + `[strip]`/`[clouds]` of `tr.ini` | `Sky/<Name>.fsh` (8 tags: `st1a`, `sc1a`, `cc1a`, `st1b`, `sc1b`, `hzcl`, `horz`, `cm1a`) + `Sky/<Name>.txt` + `.bin` | pick/rebuild the layers; `.bin` from a similar track (later: written by `nfs5_sky_bin`, ATM) | medium |
| Fog | `tr.ini` `[fog]` + up to 9 `[fog region N]` by slice | PU has the per-slice fog in `Sky/*.bin` (ATM1) | ATM1/ATM3 | was high |
| Scene light | `tr.ini` `[light] Ambient*`, `lightTheta/lightRho`, `carshadow` | `.map` header: `tint/ambient/interior/lightdecal` | RGB -> multipliers | low |
| Reflections | (not in NFS4) | `.env` (7 records 64x64) | generate from the sky or copy a template | low |
| Cops | defined differently in NFS4 | `Cops/<track>.cop` (20 bytes per point) | one every ~200 slices | low |
| Registration | - | `FEData/Data/nfs5.trk` (+1192 bytes) + names + 3 `TrackArt` files | generated (HS8) | low |
| Night / rain | `trn.frd`, `trw.ini`, `trnw.ini` | PU tracks have no weather variants | a separate "clone" track or cut | medium |
| Animated textures | `Polygon.animFlags` (length/period) | not in PU | done in the runtime (HZ4) | medium |

---

## 6. Gaps and risks (as of HS1)

1. **Nobody had a CRP writer.** Only readers (NFS5WorldExporter, ours). Written from scratch: article table ->
   MiscData -> part tables -> data, with every relative offset (units of 16 bytes for article/part tables, bytes for
   `DataOffset`; relative addressing is switched on by flag bit 1). The main engineering work and risk (done: HS3).
2. **The full `LightSource.type` table is not closed for every HS track.** The import walks every light source of
   every block and writes the known Coastal/Dolphin Cove types; unknown types are counted and reported.
3. **The `.key` speed units were not calibrated.** NFS5 range 149...600, NFS4 byte 33...128 (HS6/AI1 calibrated).
4. **Branches / shortcuts.** HS tracks have alternative paths; in v1 they are just geometry without their own AI
   route. Full support needs `.jnc` and the running slice numbering across branches.
5. **Per-slice fog** (resolved by ATM1: PU has it in `Sky/*.bin`).
6. **Weather / night variants** (`trw.ini`, `trn.frd`) are unknown to PU. A night version can be installed as a
   separate track (10 tracks have a night FRD).
7. **Material mapping.** In NFS4 a material = texture index + flags; in NFS5 `mt` is a >= 0x138-byte block of render
   parameters. A template `mt` from a stock track is used and only the 4CC and flags are filled in.
8. **`POLYVROADDATA` (24 bytes per polygon)** binds the polygon to a vroad record and packs normal/direction; not
   fully decoded. It affects which polygons count as drivable.
9. **Legal/practical**: converted tracks contain EA assets from HS. Fine for personal use; distributing the
   converter (without data) is fine, distributing converted tracks is not. **This repository ships no game data.**

---

## 7. What the engine / runtime must change

**Requirement: zero edits of original files, everything behind `[content] extended_content` in the PC wrapper INI.**
With `0` (default) the wrapper applies no patch. So the track list is replaced in memory, not in the file, and the
names come from a hook, not from `FEStrings.csv`. (The web runtime does the same; see `docs/runtime.md`.)

Minimum (v1, "the track drives"):

| # | change | where | why |
|---|---|---|---|
| E1 | point the track list loader at `FEData/Data/nfs5ext.trk` | 7 `push` references to the string `'nfs5.trk'` (`0x005DB328`) in `0x004FAD8D...0x004FB1C7`; patch each 4-byte operand | the loader `FUN_004fae20` takes `filesize/0x4A8`, so extra records are picked up; the stock `nfs5.trk` stays untouched |
| E2 | track names via a hook of `0x004E06D0` (GetString) for the new FEString IDs | wrapper (`H_GetString`) | otherwise an empty string in the menu; `FEStrings.csv` is not edited |
| E3 | track art: **new** files `FEData/TrackArt/*.fsh` x3 | files only | otherwise a hole in the UI |
| E4 | audit loader limits | `FUN_00488090` (edg), `FUN_0044fb40` (key), `FUN_004876e0` (crp), `FUN_004409d0` (map/env) | fixed buffers (edge / article / texture counts) |
| E5 | skip `_ST%d.scn` | `FUN_0046d330` | a new track must not need Factory Driver missions |

Nice to have (v2, "like the original"):

| # | change | why |
|---|---|---|
| E6 | per-slice fog | moved to `Sky/*.bin` (ATM1), no runtime change needed |
| E7 | animated textures (frame swap by timer at texture bind) | `Polygon.animFlags` from HS (done: HZ4) |
| E8 | ~~INI switch~~: promoted to v1 (`[content] extended_content`) | control |
| E9 | longer track list in the frontend (scroll/pages) if it stops at 15 | more than one new track (works: 34 tracks) |
| E10 | ~~two-sided polygons (`texflags & 0x8000`, `animFlags & 0x10`)~~ | closed: reversed winding in `nfs4_geometry` (HS5, 2026-09-19) |

---

## 8. Converter architecture (as planned; see the code for what was built)

Reader -> intermediate representation -> writer:

```
tools/track/
  ea_refpack.py       # RefPack decode/encode, SHPI (FSH) read/write
  nfs4_frd.py         # FRD reader (validated)
  nfs5_crp.py         # karT reader
  nfs5_crp_writer.py  # karT WRITER
  nfs4_geometry.py    # FRD -> CRP articles (HS5)
  nfs5_track_files.py # .edg .jnc .key .dtx .scn writers (HS6)
  nfs5_environment.py # .map .env Sky/ Cops/ (HS7)
  nfs5_frontend.py    # nfs5ext.trk, names, TrackArt (HS8)
  convert_track.py    # CLI
```

Pipeline:

1. `nfs4_frd.read()` -> blocks, vroad, polygons, objects, lights, sounds.
2. Quad -> 2 triangles; UVs from `texflags`; vertex colour -> 3 face-corner colours.
3. Split scenery into L/R by `dot(right, v - refPt)`.
4. Re-chunk under the 256-vertex limit per `pr` (greedy grouping by material).
5. Textures: `tr0.qfs` -> unpack PAL8 -> ARGB8888 -> mips (box filter) -> `<track>.fsh`, generated tags, a
   "NFS4 index -> 4CC" map.
6. `.edg` from the left/right `vroad` points, thinned (Douglas-Peucker to ~12 m).
7. `.key` from `spdfa.bin`: local extremes of the speed profile, <= 400 points, final sentinel `(100000, 600.0)`.
8. `.map`: one section + tints from `tr.ini [light]`; `.env`: a template or rendered from the sky; `.dtx`: copy of
   11 bytes.
9. `_fstart` / `_bstart` / `_cameras` / `_audio.scn`: generated from vroad, `tr.cam`, `SoundSource`.
10. `Sky/`: from `sky.qfs` + `tr.ini [strip]/[clouds]`.
11. `Cops/<track>.cop`: every ~200 slices.
12. `nfs5ext.trk`: append a record, names, `TrackArt`.

Each step is checkable: the CRP writer must pass a **round trip** (`write -> nfs5_crp.read -> compare`), and a
finished CRP must open in `NFS5WorldExporter` (OBJ export, checked in Blender outside the game, no screenshots).

---

## 9. Stage plan

| stage | content | acceptance |
|---|---|---|
| **HS1** | this research | document + validated readers |
| **HS2** | SHPI writer + mips; `tr0.qfs -> <track>.fsh` | `<track>.fsh` opens in our reader and `NFS5WorldExporter`; structure matches PU |
| **HS3** | `nfs5_crp_writer.py`: minimal CRP (1 article, 1 material, 1 `pr`) | the game loads a stub track, one triangle visible |
| **HS4** | round trip: rebuild `alps.crp` from its own parse | geometry matches, the game does not crash |
| **HS5** | Hometown geometry: road + terrain + LOD + baked light | the track drives, no holes |
| **HS6** | `.edg`, `.key`, `.jnc`, `_fstart` / `_bstart` | bots drive the distance, collision works |
| **HS7** | sky, `.env`, tints, sound, cameras, cops | visually close to HS |
| **HS8** | `nfs5.trk` + frontend + art; INI switch | the track is selectable in the menu, the race counts |
| **HS9** | batch run of all 19 tracks, failure report | "what converted" table |

The first target track was **Hometown**: the simplest fitting one (120 blocks, 162 objects, 0 special, 22 lamps, no
night variant).

---

## 10. Open questions after HS1

1. ~~`ef` part format~~ **closed**, 11.2. Left: subtypes 3 (56 bytes) and 4 (72 bytes) and the full 4CC list.
1a. **Where PU stores the road surface type**: answered in HS9 (`mt+0` word nibble).
2. The 68-byte `tr.cam` record vs PU `CAMERA_ELEMENT`, field by field.
3. Units of `speed` in PU `.key` (AI1 calibrated `--scale 4.30`).
4. The helper `uint8[nVRoad]` in `spdfa.bin`; the speed and the final `float32[nVRoad]` lateral offsets are decoded
   in HS6.
5. `POLYVROADDATA`: full meaning of `flags[5]` and `hs_minmax[4]`.
6. How PU builds its slice table: from the `RD####C` names, from `.edg` or from a separate structure (SimD records,
   see HS6/AI1).
7. Hard limit on the number of tracks in the UI (none hit at 34).
8. `heights.sim`: any receiver in PU at all.

---

## 11. Addendum: light, surfaces and sound

Follow-up 2026-09-18.

### 11.1 Light in NFS4: everything is baked, night is a second copy of the track

`shading[nVertices]` is a D3DCOLOR per vertex, **alpha always 255**, RGB ~100...255: a pure light multiplier, not a
colour.

The night variant is not a runtime feature but **a separate relit FRD**. Mean vertex brightness (first 40 blocks,
luma):

| track | `tr.frd` (day) | `trn.frd` (night) |
|---|---|---|
| Coastal | 184.3 | 68.5 |
| Snowy | 160.1 | 70.6 |
| Gt1 | 129.5 | 108.3 |

The geometry is the same (Gt1 even has the same vertex count), only the vertex colours and `tr.ini -> trn.ini`
change. Practical result: **a night version comes almost for free**: the same converter, a different input FRD, a
different `nfs5ext.trk` record.

Other light sources in NFS4:

* `tr.ini [light]`: `AmbientRed/Green/Blue`, direction `lightTheta`/`lightRho`, `carshadow=[A,R,G,B]`;
* `tr.ini [strip]`: horizon colours (`hrzSunColor`, `hrzSkyTopColor`, ...);
* `tr.ini [track glows]`: 32 records `glowN=[A,R,G,B], type, ?, ?, size, (x,y), T0`;
* `LightSource` in the blocks: **`int32 x, y, z` in 16.16 fixed point** (divide by 65536; the coordinates fall into
  the track bbox) + `uint32 type`. `type` is packed: on some tracks `0xFFFF00NN` where `NN` = the `glowN` index
  (Hometown: 1 and 7; Empire: 1, 2, 4, 5, 6, 7, all within 0...31), but Gt1/Coastal use another packing
  (`0x00190000`, `0x005A0066`). Confirmed for Coastal/Dolphin Cove: `0x001D0000 -> lpol` (79),
  `0x00190000 -> tunb` (48), `0x005A0066 -> lmpy` (6); the sentinel `0xFFFF0000` is skipped. The converter walks
  these records on every track and reports the unmapped types separately (the `HS5: skipped unknown light type(s)`
  lines you see during a conversion).

### 11.2 Light in NFS5: the `ef` part decoded

`ef` is an effect marker bound to an article. Type 2 (44 bytes) is the most common:

```
+0x00 uint32 type          // 2 = 44 bytes, 3 = 56, 4 = 72 (castle only)
+0x04 uint32 0
+0x08 float3 position      // world coordinates, near the centre of its article
+0x14 float  0
+0x18 float  0
+0x1C float  1.0           // scale
+0x20 uint32 0
+0x24 char[4] 4CC          // EFFECT NAME (reversed in the file)
+0x28 uint32 id            // hash / id
```

4CC dictionary over four tracks:

| 4CC | where | what |
|---|---|---|
| `tunb` `tuny` `tuno` | alps (76), autobahn (198), industrial (37) | tunnel lights |
| `lmpy` | castle (270), monaco1 (18) | yellow lamp |
| `stry` `strg` `strb` `strr` | monaco1, industrial, autobahn | street light yellow/green/blue/red |
| `bldy` | industrial (225) | lit building windows |
| `lpol` | industrial (97) | lamp post |
| `lamp` | monaco1 (22) | lamp |
| `nred` `fred` | industrial | red (signal) |
| `swht` | alps, castle | white |
| `helf` `helr` `helw` | alps, castle, autobahn, industrial | rescue helicopter lights |
| `arwa`...`arwe` | every track, 1-6 each | arrow sign lights |
| `wrna` `wrnb` | every track | warning flashers |

The article names carrying `ef` confirm the meaning: `SWITCHTUNB_SECONDARY`, `LAMPWALL1`, `LIGHTPOLE_RIGID2_LOD`,
`ABLAMPPOST1_RIGID2`, `BLD_LIGHT`, `SUPERTUNNEL18_JUNC`, `FLASHERS02`, `RESCUEHELI`.

Light transfer in total: vertex light -> `df` mechanically; `LightSource.type` -> 4CC from the table; `ef` uses the
44-byte type-2 records, `Base` LOD markers `0x101` and 11 stock light `mt` records from Industrial
(`NormMatState`/`FullAmbient`); the bitmaps (`lyte`, `pole`, `cone`, `plon`, `iolp`, `lint`, `lwin`, `indl`) are
added to `<track>.fsh`. `convert_track.py` runs this pass for every track with `LightSource` records and prints
`lamps X/Y` = effects written / source lights. The global `tr.ini [light]` multipliers go into the `.map` header.

### 11.3 Surfaces

**NFS4 has a per-polygon surface.** The low nibble of `POLYVROADDATA.flags[4]` behaves like a surface material code
and correlates with the track theme (share of polygons where the nibble >= 2%):

| track | low nibble distribution |
|---|---|
| Snowy | `1`:37% **`F`:57%** |
| Country | `1`:41% **`F`:52%** |
| Summit | `1`:28% `A`:30% **`F`:38%** |
| Lostcany | `1`:29% **`D`:62%** |
| Redrock | `1`:30% **`D`:56%** |
| Park | `1`:30% `3`:22% **`D`:41%** |
| Hills | `1`:34% `3`:19% **`D`:31%** |
| Aquatica | `1`:38% `3`:12% `A`:35% |
| Empire | `1`:51% `A`:41% |
| Coastal | `1`:41% `2`:30% |
| Gt1 | `1`:39% `3`:29% `C`:14% |

`1` is on every track (28-54%): asphalt. `F` gathers on snow/field tracks, `D` on canyon and dirt ones, `A` and `3`
on grass/coastal ones. The high nibble (`0x80`, `0x40`, `0x20`) holds separate flags, not the surface.

**In NFS5** (as of HS1 the receiver was unknown; HS9 found it in the material's `mt+0` word):

* `PORSCHE.EXE` at `0x005D1940` holds a name table: `Glass, Pylon, Bush, Tree, Rock, Earth, Ice, Snow, Wood,
  Metal, Gravel, WetAsphalt, Asphalt, HeadLight, CarBody, Rain/Race/Tires`, next to it `RockScrape, EarthScrape,
  IceScrape, SnowScrape, WoodScrape, MetalScrape`: the hit and scrape sound table (`DAT_005cc4d8`);
* `animdefs.txt` puts `audioType=kAudIce/kAudSnow/kAudEarth/...` on **objects**, not on the road.

Checked and **excluded** as the surface carrier:

* `.edg`: flags are only `0x00/0x10/0x20/0x30/0x50/0x60` on all 15 tracks, a road/wall classification;
* CRP material `mt` field `+0x2C`: the shader class (0-3 for normal materials, `7` for water `wtr3`, `muck`;
  matches the `basicwater` / `multinormalwater` / ... strings in the EXE);
* `GameData/Track/Data/TR%02d.RHO`: 2048 bytes = 64 records of 32 bytes, not bound to slices, purpose unknown.

### 11.4 Sound zones

NFS4: `SoundSource` in the blocks, position also **16.16 fixed point**, plus `uint32 type` packed like the lamps
(`0x0001000N` or a small number). Few sources: Country 8, Empire 7, Hometown 6, 46 over all 19 tracks.

NFS5: `<track>_audio.scn`, text, parsed with `'%d %d %f %f %d %d %d %d %d %d %d %d'` (`0x005D1A40`, next to the
string `'CAudio Element'`):

```
AUDIO_ELEMENT 4
-159.056000 6.341764 -110.432465
2 265 450.000000 200.000000 15 48 0 0 522 -1 100 0
Wind Slice101
```

i.e. `type, sound_id, radius, falloff radius, ..., volume`. The labels in the stock files point at slices
(`Wind Slice101`, `Slc70 Crow`). PU is much denser: alps 101 elements, castle 318.

So moving the sound zones is trivial (positions 1:1, only a `type -> sound_id` mapping), but 6-8 HS sources make a
much emptier soundscape than PU's own tracks. HS13 solved it differently: the real NFS4 ambience scripts from
`nfshs.exe` (see [HS13](HS13-ambient-sound.md)).
