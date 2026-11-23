# HS5: High Stakes geometry -> Porsche Unleashed articles

Date: 2026-09-21. Stage 4 of the plan in [HS1](HS1-nfs4-track-migration.md), built on [HS2](HS2-texture-pipeline.md)
(textures) and [HS3](HS3-crp-writer.md) (karT writer). Code: `tools/track/nfs4_geometry.py`, acceptance
`tools/track/test_hs5_geometry.py`. The billboard scheme was checked in the game on Coastal / Dolphin Cove.

## Result

**All 19 High Stakes tracks convert to `.crp` without losing geometry.**

```
python tools/track/test_hs5_geometry.py <all 19 tracks>
...
ALL PASSED
```

The acceptance compares **sets of triangles**, not files: the set from the `.crp` against the set computed directly
from the FRD. A triangle is three corners, each with position, UV and baked colour. A difference would mean geometry
really lost, duplicated or moved.

| track | blocks | articles | materials | triangles | size, MB |
|---|---|---|---|---|---|
| Aquatica | 180 | 671 | 146 | 82 068 | 4.5 |
| Atlantic | 166 | 656 | 143 | 75 285 | 3.9 |
| Coastal | 135 | 1224 | 190 | 81 071 | 5.8 |
| Country | 175 | 819 | 143 | 73 944 | 3.9 |
| Empire | 154 | 741 | 233 | 61 702 | 3.6 |
| France | 122 | 801 | 168 | 71 091 | 4.5 |
| Germany | 136 | 675 | 177 | 68 211 | 4.2 |
| Gt1 | 104 | 747 | 102 | 59 432 | 3.6 |
| Gt2 | 110 | 826 | 97 | 61 772 | 3.7 |
| Gt3 | 119 | 690 | 100 | 57 822 | 3.4 |
| Hills | 136 | 722 | 144 | 70 901 | 4.2 |
| Hometown | 120 | 591 | 150 | 57 055 | 3.4 |
| Lostcany | 212 | 931 | 207 | 110 589 | 7.2 |
| Park | 118 | 1010 | 134 | 69 887 | 4.7 |
| Redrock | 183 | 965 | 182 | 86 277 | 6.1 |
| Rockypas | 186 | 914 | 180 | 88 212 | 5.1 |
| Snowy | 129 | 1077 | 190 | 81 180 | 5.4 |
| Summit | 216 | 1039 | 173 | 106 393 | 5.6 |
| Uk | 105 | 975 | 144 | 55 491 | 3.6 |

**Total 1 418 383 triangles, zero differences.** (Article counts grew later with HZ2 global objects and
animation: Hometown is 662 articles today.)

For comparison with stock PU: alps LOD0 = ~62k triangles, coastal ~49k. The converted tracks are of the same order.

## How the model maps

NFS4 and NFS5 share the topology (HS1), so there is no recalculation at all: an NFS4 block = 8 vroad points, a PU road
chunk = 8 slices, both games in metres with the same axes. **Block `b` becomes the articles at slice `b*8`.**

Per block:

| article | from |
|---|---|
| `RD%04dC (1 PRIMARY)` | road surface |
| `CNK%04dE (1 LANES)` | lane markings |
| `CNK%04dL (1 TERRAIN)` | scenery left of the centre line |
| `CNK%04dR (1 TERRAIN)` | scenery right of it |
| `OBJECT%d_%d` | a plain XObj type 4 of the block |
| `Object%02d_MID2` | XObj type 2: a billboard card |

The side is the sign of `dot(right, point - refPt)` at the block's middle vroad point: the same axis NFS4 itself
uses for the road cross-section.

**LODs come for free.** NFS4 already ships low and medium detail geometry in separate chunks, and they map onto the
PU LODs one to one:

| LOD | road | scenery |
|---|---|---|
| 0 | chunk 4 `HIGH_RES_TRACK` | 5, 7..10 `HIGH_RES_MISC*` |
| 1 | chunk 2 `MED_RES_TRACK` | 3 `MED_RES_MISC` |
| 2 | chunk 0 `LOW_RES_TRACK` | 1 `LOW_RES_MISC` |

## What the converter does

* **quad -> two triangles** `(v0,v1,v2)` and `(v0,v2,v3)`; when the last two indices repeat, one triangle;
  degenerate ones are dropped;
* **UVs are generated from `texflags`**: NFS4 has none, every quad maps the whole texture and the flags permute
  the corners;
* **baked light**: NFS4 `shading[]` is per vertex, PU `df` per face corner, so the vertex colour is repeated for
  every corner that uses it. Both are D3DCOLOR, no value conversion;
* **plain objects**: XObj geometry is in local coordinates, `pt` is the world position. The offset is baked into
  the vertices and the object comes out in world coordinates like the road chunks. No `tr` matrix: NFS4 objects
  have no rotation;
* **billboard objects**: FRD `XObj type 2` is a general class of polygon cards, not only trees: also poles, bushes
  and similar. For PU the article is named `Object%02d_MID2` with `Base=0x4611`. The vertical source card is
  duplicated rotated 90 deg around its local vertical axis: the crossed-card shape of the stock `TREES_MID2` and
  `Object##_MID2`. That enables the `HBillBoard` path and the turn towards the player;
* **primitives are cut at 256 vertices**: the hard limit of karT's one-byte indices (HS3). Inside a primitive
  vertices are deduplicated by source index and UVs by value, so there is little bloat.

## Lane markings are separate, and why

The lane strips (chunk 6 `LANES`) **share no vertex with the road** (1352 marking vertices, 0 shared) and lie within
+-0.03 m of the surface. Merged into the road mesh they z-fight.

So the markings get their own `CNK%04dE` article. The `E` suffix exists in the originals (`CNK0264E` in autobahn).
The positions stay exactly as in the source; the z-fight is solved by depth bias / a decal pass at draw time (the
Glide state `0x18` = depth bias) and the PU decal notion (`lightdecal` in `.map`). See HS9 for how it ended up.

## Billboard pipeline for XObj type 2

The stock PU `TREES_MID2` articles have `Base=0x4611`, a MID2 name and two perpendicular cards. One flag is not
enough: a single plane with the same `Base` lands in `WMidGround` but does not give the expected billboard. So the
converter:

1. does not apply the `RefExtraObject` table to type 2;
2. keeps the source card, texture, UV and `df`;
3. creates a second copy rotated 90 deg around the local Y axis;
4. writes the article with `Base=0x4611` and the name `Object%02d_MID2`.

Coastal / Dolphin Cove has 703 type-2 objects. After conversion the game loaded all 703 into `WMidGround`; they show
and turn with the camera.

## Materials

Each NFS4 texture in use gets one `mt`, whose 4CC is `index_tag(index)` from HS2, the tag of that texture in the
generated `.fsh`. The acceptance checks that the set of tags in the `.crp` equals exactly the set of textures the
texture converter writes. Hometown: 150 materials of 186 archive textures; the other 36 are unused by the geometry
(sky, effects, interface).

## Not done at HS5 (later status in brackets)

* **two-sided polygons**: closed (E10, 2026-09-19). `texflags & 0x8000` and `animFlags & 0x10` produce a second
  copy of the triangle with reversed winding. PU still culls; one face shows from each side.
* **light sources** (`ef`): closed for Coastal/Dolphin Cove: the converter walks `LightSource`, writes type-2
  effects and adds the stock light materials; the code table for the other HS tracks keeps growing.
* **animation** (`Anim`/`Anqt`): (done in HZ2).
* **vertices are duplicated across primitives.** Stock PU lets the vt/uv/df windows of different primitives
  overlap and reuses vertices; we give each primitive its own window. Hometown: 79 619 vertices vs 33 429 in the
  source. Moderate size cost, no correctness cost.

## What the acceptance does not prove

That the game loads and shows it. Proven: no triangle lost or moved, materials point at existing textures, slice
numbering is a multiple of 8 and starts at zero. The engine's expectations were checked in HS6/HS8/HS9.

## Next

HS6: `.edg` (borders from `leftWall`/`rightWall`), `.key` (from `spdfa.bin`), `.jnc`, `_fstart`/`_bstart`.
