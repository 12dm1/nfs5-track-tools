# HS9: bringing the converted tracks up in the game

Date: 2026-09-19. Continues HS1-HS8 (the pipeline was ready but had never run in the game). Everything here comes
from the live game (Porsche Unleashed v3.5 with the PC DX11 wrapper).

State on 2026-09-19 (evening, Hometown re-run):

* the track **loads, drives, collision works, surfaces work**;
* geometry and diffuse textures draw (QFS compact index + V flip);
* **accepted:** intro (7.1), sky backdrop (7.2, "looks fine, do not touch"), Coastal weather (7.3), camera jerk on
  the line (7.4), invisible FRD wall (7.5, Hometown 2026-09-20), surfaces from the POLYVROADDATA nibble (7.6),
  cutout seams (7.7, Hometown 2026-09-20), LOD x2 (7.8; the tier popping is a fog matter, not this stage).
  Wall and foliage details: [HS11](HS11-walls-alpha.md).

Test tracks: Hometown (main bring-up), Gt1, Snowy, Summit (4 of 19). Reference: `monaco1` (a circuit, like all of
ours), `alps`, `canyon`.

---

## 1. How to measure anything here

No debugger on the machine (no cdb, windbg, procdump). So:

**EIP sampler.** `Wow64GetThreadContext` via P/Invoke: find the busiest thread, suspend it, read the context, scan
the stack for addresses inside the `PORSCHE.EXE` code range and print a pseudo stack. It found the infinite loop
(section 3); without it there was nothing to search with.

**Class names from RTTI.** MSVC puts a pointer to the complete object locator in the slot before the vtable; from
there `+0x0C` -> type descriptor, `+8` -> name. Implemented as `RttiName()` in the wrapper. It turned "some object"
into `WTreeElem_Tri`, `RDrawArticleBundle` and so on: the most useful trick of the session.

**Naked thunks for counters.** To count calls of a function whose calling convention is unknown:
`__declspec(naked)` + `inc` + `jmp` to the original. Touches neither registers nor stack, works with any convention.

**Vtable slot replacement.** Virtual methods are hooked by writing into the table itself
(`WriteDword(0x005B3CF0 + 4, ...)`), not by finding call sites.

**Control transplant.** The most productive experiment: install the *whole* file set of a stock track under our
track's name and see what changes. In one race it separated "the CRP is wrong" from "the registration is wrong".

**Ghidra.** The full export of the dev machine does not cover `0x474000-0x48A000`; that range was exported
separately with a focused script. If a function is missing from the main export, look in the focused one.

---

## 2. How to run without losing data

* Use the wrapper's launcher. A bare `PORSCHE.EXE` does not load the wrapper.
* The wrapper log is opened with `"w"` and **wiped at every start**. Copy it before a restart.
* Track files are locked while the game runs; close the game before replacing them.
* **`SaveData/player.lst` is zeroed when the game crashes in a race**, and then the game does not start at all: it
  crashes at `0x004DF31E` on a null dereference. Fix: write the profile name without extension and without a
  trailing zero: `printf '8Stig' > SaveData/player.lst` (for `SaveData/8Stig.sav`). The save itself is intact.

Diagnostics were switched on in the wrapper INI `[content]`: `trace_respawn`, `trace_bundles`, `trace_accessors`,
`trace_materials`, `trace_files`. All temporary.

---

## 3. Fixed: the infinite respawn loop

**Symptom.** Loading completes, then a beige screen, a blue screen and a hang. The process is alive and burns 100%
of one core.

**Diagnosis.** The sampler showed a stable stack `5A4EC0 -> 49B4F1 -> 45DCD9 -> ... -> 40FB6A -> 40F683 / 40F68C`.
A loop in `FUN_0040f540`, putting the car back on the track:

```
0040F666  loop: slice = FUN_004817d0(slice, direction, -1)
0040F687        if (!FUN_0040f480(car, slice)) goto loop     ; no iteration counter
0040F693        FUN_0049e220(car); ... FUN_0049b090(car, slice, ...)
```

`FUN_0040f480` takes the slice centre from the SimD table at `[00628BC0]+0x0C` (stride 0x50), asks what surface is
under it and rejects the slice if the low nibble of the answer is zero:

```
0040F4C4  test byte ptr [car+2Ch], 0Fh
0040F4C8  jnz  ok                        ; else xor eax,eax; ret
```

`car+0x2C` is filled by `FUN_00474060` -> `FUN_00473240`: descend the track quadtree to the cell with the point,
then among the kind-1 elements take the one whose height is closest to the query.

**The NaN in the camera position was a consequence, not the cause**: the loop never exits, `FUN_0049b090` (the actual
car placement) is never called, the position stays uninitialised.

**Root.** A quadtree census (`trace_respawn=1`):

```
Monaco:   nodes 771  objects 20477  kind1=14183  usable=13406
          classes: WTreeElem_Tri | WTreeElem_Edge | CylinderObject | BoxObject
Hometown: nodes 225  objects 4403   kind1=0      usable=0
          classes: WTreeElem_Edge only
```

Kind 1 is `WTreeElem_Tri`, collision triangles. We had none; only the `.edg` edges were in the tree.

**Mechanics.** The `uint16` word at **+0 of the `mt` material record**:

* the engine builds `WTreeElem_Tri` only if this word is **non-zero**;
* a surface counts as drivable only if its **low nibble is non-zero**.

Checked by counting on Monaco: materials with `C000` and a zero nibble give 825 index units on LOD0, and the tree has
exactly **777** kind-1 elements with a zero nibble; `14183 - 777 = 13406` matches the usable count exactly.

**Surface map** from the 15 stock tracks, grouping materials by nibble against the texture tag:

| nibble | surface | texture tags |
|---|---|---|
| 1 | asphalt | `rd01 rd11 rd12 rd22 rd39 rd42...rd69 alks` (239 records) |
| 2 | second road type | `rdbb rdbs rdcc rdcf rdcg rdch rdec` (24) |
| 3 | grass | `grs7 grsd grsj xgrs rdg1 rdg3 rdg4` (32) |
| 4 | stone / cobbles | `rd25...rd33 rdst rdcr rdcs` (46) |
| 5 | dirt | `rddd rddr rddt rdgd rdqd` (27) |
| 7 | planks / wood | `rdb1 rdb2 rdyj rdyk rdyl wldc` (10) |
| 8 | ice | `icyy rda7` (2) |
| 9 | snow | `snw3 snw7 rdsn rd13 rd14 rda3 rda5` (9) |

The high bits of the same word are separate flags, unrelated to the nibble: `0010` guard rails, `0020` fences,
`0030` snow walls, `0060` walls, `C000` solid scenery (rocks, trees). A material can carry them with a zero nibble:
then it is solid but not drivable.

**Fix.** `nfs5_crp_writer.Material(surface=...)` writes the word at +0; `nfs4_geometry` assigns surfaces by chunk
role and keys materials by `(texture_id, surface)`: one NFS4 texture is used on the road and on the shoulder, and
the originals duplicate materials exactly the same way (`rdma` twice in monaco1).

**Result in the game:** `kind1=85107, usable=85107`, types `0001`/`0003`, no respawn failure. The race runs, the car
is on the road, playable.

(The first version gave the terrain one type per track; 7.6 replaced it with the NFS4 per-polygon nibble,
`NIBBLE_SURFACE` in `nfs4_geometry.py`.)

---

## 4. Fixed: LOD switch distances

`FUN_00450de0` and `FUN_00450010` walk the LOD rows and, past the last one, `return` without drawing anything:

```c
p = (ushort*)(Base + 0x6E);              // distance of LOD row 0
while (*p * *p <= distance_squared) {
    ++lod;  p += 6;                       // 12-byte step
    if (n_lod <= lod) return;
}
```

Our writer used `DEFAULT_DISTANCES[0] = 150` for an article with one LOD, so such an article vanished beyond 150 m.
The stock files write **10000** there (alps 1451 articles, canyon 1309, monaco1 389). `Article.FAR_DISTANCE` was
added; the last row is forced to be "infinite".

A real bug, fixed, but it **did not cause the invisible geometry**.

---

## 5. Checked and ruled out

Listed so nobody walks this again. All checked on data.

| hypothesis | closed by |
|---|---|
| Missing `Vis ` block | `FUN_00488b50` tests "does a ray cross a blocking line". With a zero count it returns **0 = not occluded**. Missing `Vis` makes more visible, not less. The block is 24 bytes per segment, XZ point pairs; monaco1 has 13 open chains 17-310 m from the centre line: occlusion lines |
| Missing `Desc` block | Asset source paths (`I:\NFS5Tracks\ModelLibrary\Objects\Smackable\Bus`), unrelated to rendering |
| Article names (`PRIMARY`, `SECONDARY`, `TERRAIN`) | The strings `PRIMARY`/`SECONDARY`/`RIGID`/`CCYL` are not in the EXE at all; the engine does not parse names |
| Textures not found | All 203 material tags of hometown are in `hometown.fsh`; monaco1 has 0 misses too |
| Bounds and culling spheres | `Base+0x14` min, `+0x20` max, `+0x2C` centre, `+0x3C`/`+0x40` radius. Values right: centre (645.71, 15.29, 86.48), radius 55.33 for a half diagonal of 55.3 |
| Camera position | `R1 cubemap pos=(642.7, 11.2, 95.5)`: inside the track bounds |
| LOD row table | Rows at `+0x64`, 12-byte step, byte 0 = 1 (draw gate), `info` range at +4/+6, distance at +10: byte for byte as monaco1 |
| Bundle classification | All 591 articles are taken by `WStaticBundle` (monaco 403 of 510). Checks: `WScene`/`WInstance` = bit 15 of the first `Base` word, `WMidGround` = bits 13-14, `WStatic` takes the rest without checks |
| `pr` encoding | In alps and canyon **our variant is the main one**: f0 = (11, 10, 9) with `Iv`+`Iu` rows, 5187 of 5394 primitives in alps |
| Primitive draw queue flags | Byte 2 of the first `pr` word selects the queue (bits 0/1/4). We write `02`, the most common stock combination: alps 2593, canyon 2758, forest 3588 |
| Article table records | Byte for byte as stock, including `+4 = 0x1A` |
| `vt`, `uv`, `df` | 4th vertex component = 1.0 (as alps), sensible UVs, `df` with alpha 255 and reasonable colours |
| Index windows | No out-of-range access; our file is cleaner than the stock ones in this respect |
| MiscData tags | `RMth`, `SimD`, `mt` written correctly (the tag = the first 4 bytes of the record reversed) |
| Byte 3 of the first `pr` word | We always write 0. 1983 of 2513 monaco primitives also have 0 and they draw |
| Bit 16 of the first `Base` word | Set on monaco road articles, not on ours. Only affects skipping LOD 0: `if (DAT_006197e5 && n_lod>1 && !(Base[0] & 0x10000)) lod = 1`. Worth setting (7.8 does), but it does not hide geometry by itself |

---

## 6. Solved: geometry not drawn

### 6.1 Where it stopped

**Control transplant.** The whole monaco1 file set under the hometown names (`.crp .fsh .edg .jnc .map .env .dtx
_audio.scn _cameras.scn`, `racer/work/hometown0.lsp`, `hometown1.lsp`). The track drew. So the registration via
`nfs5ext.trk`, `.edg`, `.jnc`, `.map`, `.env`, `.lsp` were fine; the `.crp` was guilty.

**Draw call counter.** Naked thunks on slots `+04` and `+08` of the `RDrawArticleBundle` vtable (`0x005B3CF0`):

```
Monaco:    +04 = ~20000 per sample, continuous
Hometown:  +04 = 4980 once when entering the race, then 0, 0, 0, 0, 0
```

The data was fine (the collision builder read the same articles and built 85107 correct triangles), the render
object existed (`RDrawArticleBundle held=591`, linked into the global list), but the per-article draw was never
called. The rejection happened **above**, in visibility.

### 6.2 Engine map collected on the way

All checked, exact addresses.

**World loading**

| address | what |
|---|---|
| `FUN_00471b6b` | BWorld init: copies the track name, creates the world, loads the CRP, prints "ERROR: Couldn't load track %s" |
| `FUN_004879d0` | Post-load: collects all articles by tag `'Arti'` (`0x41727469`), runs them through the bundle chain, then `.edg` (`FUN_00488090`), `Vis` (`FUN_00488a60`) |
| `FUN_00488a60` | Reads `Vis ` (`0x56697320`), 24-byte records -> 28 bytes in memory |
| `FUN_00480080` | Reads `SimD`/`SimT` |

**Globals**

| address | what |
|---|---|
| `0x00628960` | BWorld; `+0x24` the track object, its first field the quadtree root |
| `0x00628BC0` | **pointer** to the slice table: `[0]` count, `[3]` data, stride 0x50, centre at +4 |
| `0x0061BC4C` | head of the global render object list |
| `0x005E5084` | part tag table: `vt nm uv df Base pr ef`, 4 bytes each, reversed. Indices: Base = 4, pr = 5, ef = 6 |
| `0x006197e5`, `0x006197e6` | detail flags, affect LOD choice and queue |

**Fill bundles (`W*Bundle`)**

| constructor | class | selection (first `Base` word) |
|---|---|---|
| `FUN_00482840` | `WSceneBundle` (vtable `0x005B47E0`) | bit 15, claim = `FUN_00482970` |
| `FUN_00478040` | `WInstanceBundle` (`0x005B4718`) | bit 15, claim = `FUN_004781A0` |
| `FUN_00477710` | `WAnimBundle` (`0x005B46B8`) | claim = `FUN_004777F0` |
| `FUN_0047cc80` | `WMidGroundBundle` (`0x005B477C`) | bits 13-14, claim = `FUN_0047CCE0` |
| `FUN_00483640` | `WStaticBundle` (`0x005B4814`) | no checks, takes all the rest, claim = `FUN_00483690` |
| `FUN_00479900` | `WLineBundle` (`0x005B4750`) | claim = `FUN_004894E0` |

`WStaticBundle::claim` also creates the render object: `push 14h; call 0059EF90; call 00450C40; mov [bundle+8], eax`.

**Render objects (`RDraw*`)**

| class | vtable | key slots |
|---|---|---|
| `RDrawArticleBundle` | `0x005B3CF0` | `+04` = `FUN_00450DE0` (draw an article), `+08` = `FUN_00450CA0` |
| `RDrawAnimArticleBundle` | `0x005B3CB8` | `+08` = `FUN_00450010` |
| `RDrawLineBundle` | `0x005B3D28` | `+00` = `FUN_004513F0` |

`FUN_0044fdd0` is the `RDrawableBundle` base constructor:

```c
this[1] = bundle;                       // +4
n = bundle[0x18];                       // articles held by the bundle
if (n > 9999) n = 100;
this[2] = alloc(n); memset(this[2], 0, n);   // +8: one byte per article
this[3] = DAT_0061bc4c; DAT_0061bc4c = this; // +0C: link into the list
```

The bytes at `this+8` are set and consumed **within a frame**: at the page flip they are zero on a stock track too.

**Drawing one article, `FUN_00450de0`**

```c
Base = FUN_0043c7d0(4, -1);
lod = 0;
if (DAT_006197e5 && 1 < Base[3] && !(Base[0] & 0x10000)) lod = 1;   // skip LOD0
if (lod < Base[3]) {
    p = (ushort*)(Base + lod*12 + 0x6E);
    while (*p * *p <= d2) { ++lod; p += 6; if (Base[3] <= lod) return; }
    row = Base + 100 + lod*12;
    if (row[0]) {                                   // geometry gate
        prim = FUN_00451200(5, (short)row[1], *(uint16*)(row+6));
        while (prim) {
            d2 = |prim.centre - camera|^2;          // centre at pr+8
            flags = prim[0];                        // first pr word
            if      (flags & 0x20000)  FUN_004668f0(...);   // byte2 bit 1
            else if (flags & 0x10000)  FUN_00466a90(...);   // byte2 bit 0
            else if (flags & 0x100000) FUN_004669c0(...);   // byte2 bit 4
            else if ((flags & 0x40000) && DAT_006197e6) FUN_00466a90(...);
            // otherwise the primitive is silently skipped
            prim = next;
        }
    }
    if (row[1] & 0xFF) { ... ef, glow ... }         // ef gate: byte 1 of the row
}
```

**Collision and surfaces**

| address | what |
|---|---|
| `FUN_004750b0` | **Collision triangle builder**, not rendering. Creates `WTreeElem_Tri` via `FUN_004853c0` (4 points) and `FUN_00485c50` (3 points) |
| `FUN_00473240` | Finds the zone under a point: quadtree descent + nearest-by-height kind-1 element |
| `FUN_00473330` | **Surface type at a point.** Called by car physics (`0040BB0B`), sound (`004AA164`) and four more places |
| `FUN_00484450` | Point-in-packed-cell test (AABB in a uint32) |
| `FUN_00484500` | Quadtree descent to a leaf |
| `FUN_0040f540` / `FUN_0040f480` | Respawn and "is the slice usable" |
| `FUN_004817d0` | Step to the next slice through sections and junctions |

**`WTreeElem_*` classes**: `Quad` (node), `Tri`, `Edge`, `Face`, `Object`, `BoxObject`, `CylinderObject`. Kind 1 =
`Tri`, kind 2 = `Edge` (from `.edg`), kind 0 = `BoxObject`/`CylinderObject`.

### 6.5 Root (2026-09-19): a type-3 XObj inflated the render tree AABB

Instrumented run (Monaco -> Hometown, the game not restarted between the tracks):

* Monaco: tree origin ~ `(-1520, -105, -873)`, sizeXZ ~ 2438, 273-316 candidate nodes, `draw+04` 43 / 157 (1 vs 7
  walks = main frame vs cubemap).
* Hometown: the walks run, but **always 0 candidates**, `sphere+14=0`, `draw+04=0` from the first frame. Tree:
  origin `(-5.36e13, -383000, -1.07e14)`, sizeXZ `1.07e14`.

A packed AABB with such a world size has a zero grid step: `0047F400` culls every node.

The eight tree corners come from `WStaticBundle+08` = `004893A0` -> `00477f40` (min/max from `Base+0x14` / `+0x20`).
`hometown.crp` had **one** article with a garbage `Base`: `OBJECT600_0` (index 399), `Z = -1.072e14`, `Y = -382974`.
All others within +-750 m.

It was an XObj of **type 3** (animated). The local vertices were fine (~5 m). The `pt` field of the header (+12) is
not a world position for type 3 but pointer leftovers (like the neighbouring dwords `31034797`...). The converter had
baked that garbage into `vt`. The rest pose in keyframe 0: `int3` 16.16 = `(-432.92, 1.88, 339.95)` near the centre
of block 75 `(-453, 1, 330)`, the kf0 quaternion is the identity `(0,0,0,16384)`.

Fix: `tools/track/nfs4_frd.py` takes position and rotation of type 3 from keyframe 0, not from the header. (HZ2 later
made the animation play.) Type 3 also exists on Country (1), Snowy (2), Uk (4); one of Snowy's two `pt` was
`-2.35e38`.

HS5 Hometown + Snowy after the fix: 57055 / 81180 triangles, Hometown bbox `(-635,-28,-635)...(745,56,566)`,
`OBJECT600_0` around `(-437,-3,338)...(-428,7,343)`.

### 6.6 Track textures (2026-09-19)

After the AABB fix the Hometown geometry appeared, but without diffuse (the sky was textured: it loads separately via
`Sky/<Name>.fsh`).

`FUN_0044a770` looks for the MiscData `sf  ` (an embedded SHPI), else `sn  ` (a file name). monaco1 / alps have
`sn` = `monaco1.fsh\0` / `alps.fsh\0` as the last misc. The converted CRPs had no such block, the engine never
registered `hometown.fsh`, `mt+0x28` did not resolve, the articles drew with baked `df` only.

Fix: `source_fsh_blob()` writes `sn`.

After that the geometry was textured but the whole map turned white. Changing the `mt` template to Opaque did not
help. The wrapper log after the race `treset` had no 64x64 upload at all, only the sky. So `hometown.fsh` never
reached DTEX through `sn`.

Cause: `FUN_0059f700` finds MiscData by **binary search** on the uint32 ident. Stock archives keep `sn`
(`0x736e0000`) **after** every `mt`. The converter had put `sn` together with RM/Sim before the materials: the key
was in the middle of the table, the search went right over the `mt` entries and missed it.

Fix: `build_crp` sorts MiscData by ident. `sn` is last again.

Embedding the SHPI as an `sf` MiscData **does not work**: the Hometown load crashed half way (`0xC0000005` EIP
`004753BF` in `FUN_004750b0`, reading `[ecx+18]` with ECX=0) right after `WStaticBundle given=591`. The images
registered under the ident `sf` (`0x73660000`) while every `mt+0x24` said `sn` (`0x736e0000`). The tests check
"no extra `sf`" and "`sn` last".

### 6.4 Tools left in the repository

* `tools/track/roundtrip_crp.py`: runs a stock `.crp` through our reader and writer, to separate writer bugs from
  conversion bugs. **Note:** `extract()` in `test_crp_writer.py` misread primitives with only an `Iv` row and no
  `Iu` (read `uidx` past the data). For a fair comparison `Article` has `raw_base` and `raw_parts` (pass-through of
  `ef`, `Anim`, `Anqt` and the `Base` flag word); with them a monaco1 round trip matches the original on every tag
  except `pr` (492612 vs 494484 bytes).

---

## 7. Bring-up on live Hometown (2026-09-19, evening)

Geometry and textures are in the game (6.5-6.6). Below: six remarks from a race plus alpha seams and LOD distance. Do
not reopen the closed hypotheses of section 5 or "geometry not drawn" of section 6.

### 7.1 Intro camera before 3-2-1: went straight to BumperCam

**Symptom.** No start fly-by: the camera starts in the bumper.

**Not the cause.** Removing the `.edg` edges with flag `0x00` (that fixed the camera jerk on the markings, 7.4) did
not affect the intro. `FUN_00487e80` reads only the low byte of the edge flag; that is the camera/AI path on the
track, not the intro clip.

**Root.** The Hometown CRP had no MiscData `CmAn` (`0x436d416e`). `FUN_00471850` returns 0 without these clips. Stock
alps / monaco1 hold five clips `camera00`...`camera04` of 6340 bytes each: name[8], float3 A, float3 B, 0, float
0.1, uint32 121, then 121 x 52 bytes of `Anqt` (unit quaternion and position relative to the car). The records follow
each other under one ident; the reader sees tag `'Cm'` and index `16750` (`'An'` in the two-character MiscData
encoding).

**Fix.** `misc_blobs_from(..., tags=('RM','Cm'))` copies the five clips from the `alps.crp` template.
`_retarget_cman` moves only A/B to `vroad[0]` and a point 30 m ahead along `forward`. The keys stay: they are ~2 m
from the car.

In the game: the fly-by before the countdown is **accepted** (2026-09-19).

### 7.2 Sky backdrop (horz): accepted, do not touch

User (2026-09-19): looks fine. (HZ1 later reworked the horizon from the NFS4 `[strip]` data; see
[HS7](HS7-environment.md).)

NFS4 HDC is a 512x64 panorama, PU `horz` two 256x128 halves. History of attempts (do not repeat):

| attempt | effect in the game |
|---|---|
| stretch 64 -> 128 over the whole half | mountains at the zenith, too high |
| unstretched strip at the **bottom** of the half | mountains under the horizon, invisible |
| unstretched strip at the **top** | higher than (2), still low |
| crop the whole sky down to the silhouette (a >= 200), silhouette at row 0 | no visible shift; kept (until HZ1) |

`Sky/Hometown.txt` = MULTINORMAL, Y scale `1 / 2 / 0` like stock Alps. Do not invent a `.bin` of your own: the
atmosphere writer patches a stock template (ATM3).

### 7.3 Weather: accepted

Hometown no longer copies Alps.bin. Dry tracks -> Coastal.bin template, winter ones by name -> Alps.bin. (ATM3+
writes the weather keys from `data/atmosphere/<track>.ini`.)

### 7.4 Camera jerk on the painted line: fixed

There are no more edges with low byte `0x00` **at the lane width** (kind 2, camera/AI "road"). They sat on the
markings. The barrier polyline at `leftWall`/`rightWall` is written as `0x00` again (like monaco): a different
geometry, not the marking line.

### 7.5 Invisible walls: accepted (Hometown 2026-09-20)

PU does not slide along the SimD `leftWall`/`rightWall`. The real NFS4 walls are road polygon edges
(POLYVROADDATA `0x80` + edge byte 255). Sidecar `GameData/Track/<name>.hsw`, hook `00498FF0`, XZ circle, height 16 m
(shorter only under another block's overpass). The boulevard islands are part of the outlines. `.edg` still does not
hold the car. (HS16-HS19 and HZ5/HZ7 refined it: OBB, standard hit response, sweep, edge height.)

History, so nobody goes back to the envelope:

SimD Hometown (959 slices, fields +0x40/+0x44, as `build_sim_blobs`):

| | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| leftWall | 7.80 | 7.92 | **15.78** | 27.48 | 31.60 |
| rightWall | 3.95 | 11.76 | **11.87** | 23.73 | 31.67 |

Slices 0-2: walls 19.8/23.8, **lane 6.00/6.00**. Stock for comparison: monaco1 walls are **6/6** almost everywhere
(max 17/11), alps **4-5** (max 11/12). Hometown's lane equals monaco's typical wall: that is the lane edge, not the
wall. The NFS4 wall = `leftWall`/`rightWall`, often 12-24 m, **behind** the guard rail and **before** the rocks.

What is **not** a wall (checked in the game, do not repeat):

* `.edg` 0x10/0x30 and 0x00 on the barrier: camera/AI (`FUN_00487e80` takes the low byte). Does not stop the car.
* Vertical `WALL####` / queue 0 / `C000`: `FUN_004853c0` drops zero XZ area.
* A `0x0060` material ribbon and "ground outside the corridor not drivable": that is height; on the rocks the car
  launches and flies off.

PU itself reads SimD+0x40 in `FUN_00408750`: if `|car+0xED0|` exceeds the wall it calls `FUN_0040f540`
(**respawn**, not sliding). On Hometown it did not hold the player.

A wrapper attempt `hs_corridor.cpp` (clamp the position into the corridor minus the OBB half width (`car+0x3A8`)
after `FUN_00495020`) **did not work in the race**: the hook filtered on the basename of `DAT_0065B2B8`, which is
the **tracks folder** (`.\gamedata\track\`), not the name; and in the Ghidra call graph `FUN_00495020` is called from
`FUN_004980d0` (**car-car** resolution). The per-frame car tick is `FUN_0040ca20` -> `FUN_0040b650` (slice in
`car+8`) -> `FUN_0040bfe0` (2622 bytes, reads `DAT_00628BC0`).

Keep the `.edg` 0x00 barrier for the camera/AI.

### 7.6 Surfaces: fixed

`POLYVROADDATA` nibble -> `mt` nibble (`NIBBLE_SURFACE`). User confirmed: not just asphalt.

### 7.7 Semi-transparent texture seams: fixed

Accepted on Hometown 2026-09-20. Analysis and code: HS11. Three edges:

1. A light-blue rim between two trees: the converter wrote Opaque queue 2; foliage is now `FakeSort` queue 1 (alps
   `bsha`, `mt+4=0x0281`). Road and slots 0-9 stay Opaque.
2. The silhouette against the sky: queue 1 merges in `00466A90` with a leftover `alphaRef=10`, not in pass 0xA0. The
   runtime recognises a binary colour key and cuts it as a world cutout (threshold 0x80 in the shader, no A2C,
   CLAMP). Mips weighted by alpha; RGB bled into A=0.
3. A thread along the top of a card: UV exactly 0/1, crumbs at the edge, anisotropy. An A=0 frame + 1.5 texel inset.

Do not switch A2C off globally. Do not rebuild the sky for this.

### 7.8 Track LOD distance x2: further is fine, tier popping is expected

`FUN_00450de0`: `while (*p * *p <= d2) { ++lod; ... if (n_lod <= lod) return; }`. The wrapper's `car_lod_distance`
is **cars only**.

`Article.DEFAULT_DISTANCES`: was `(150, 250)`, now `(300, 500)`, the last row always `FAR_DISTANCE=10000`. The third
value **must not** stay `65535`: `ushort*ushort` in `FUN_00450de0` overflows a signed int (`0xFFFE0001` = -131071),
`while (*p * *p <= d2)` is always true and the article **vanishes** beyond the last working tier. The first `Base`
word carries bit 16 (`0x00010610` / `0x00010611`) so LOD0 is not skipped with `DAT_006197e5`.

These are the **switch** distances of the road/terrain tiers that NFS4 already ships as chunks 4/2/0 and
5,7-10 / 3 / 1. The converter generates no new LOD geometry. A visible mesh change at 300/500 m is normal; fog hides
it (the per-slice fog came with ATM1).

### 7.9 HS11 closed

FRD wall and cutout accepted on Hometown 2026-09-20.

### 7.10 Failed evening race 2026-09-19: do not repeat blindly

| # | what was installed | result |
|---|---|---|
| 2 | `horz` silhouette at row 0, alpha of row 0 = 220 | **accepted** as is |
| 5 | `hs_corridor` after `FUN_00495020`, name filter from `0065B2B8` | did not work: the folder = `.\gamedata\track\`; 00495020 is not the per-frame ground |
| 7 | discard + CoverageAlpha=1 + UV clamp + binary FSH | did not work: the A2C edge mixes with the sky in the RT; the WRAP strip lived on |

The wall was closed in HS11 (hook `00498FF0`, sidecar `.hsw`), the cutout there too (7.7).
