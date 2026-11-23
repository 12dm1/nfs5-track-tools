# HS11: walls and cutout (and HS16-HS19 wall physics)

2026-09-20. Status: **wall and cutout accepted** (Hometown). HS16-HS19 below refined the wall physics.

The runtime side lives in the PC wrapper (`hs_corridor.cpp`, `hs_corridor_math.h`) and in the web build
(`runtime/ext/hs_walls.cpp` of nfs5-web). This document is the contract both follow; `docs/runtime.md` summarises it.

## Verified addresses (correction to HS9 7.5)

Disassembled from the installed `PORSCHE.EXE` v3.5 (Capstone).

* `00471B6B` copies the selected track name from `00657444` into the **string buffer** `00628940`, then formats the
  full CRP path into `00628964`. `0065B2B8` holds only the folder.
* `0040CA20` calls `0049AF70`, `0040B650`, `0040BFE0` and moves the computed throttle/brake/steer into the control
  fields. It is the AI controller; a player limiter cannot go only there.
* The Ghidra export is incomplete for physics too: the EXE has a `call 00495020` at **00499027**, inside
  **00498FF0**, outside the car-pair loop. "00495020 is called only from 004980D0" is wrong.
* `00410E25` writes the address `00498FF0` into `car+E2C`, the collision step callback.
* Prologue of `00498FF0`: `83 EC 08 53 8B 5C 24 10`, eight bytes of whole instructions without relative addresses.
  Checked before the detour.
* `0049506E` passes `0x3D000000` (1/32) for the predicted move by velocity. `0049AE30` recomputes the body's world
  points.

## Changes

The runtime hooks `00498FF0`, before and after the original step. The track name comes from the loaded world; only
converted tracks are handled (stock tracks are excluded: they have no `.hsw`).

Walls no longer come from SimD `leftWall`/`rightWall`. The sidecar `GameData/Track/<name>.hsw` (`HSW1` + count + 7
floats per edge, format in `docs/formats.md`) is written from the same POLYVROADDATA as the Blender check OBJ. The
hook loads it from the CRP path without `.crp`. (HS11 collided an XZ circle with half width `car+3A8` against the
segments; HS16 replaced it with the car OBB.) Y only separates a short overpass wall from the upper deck. Without a
`.hsw` the corridor is not replaced by SimD. 8 m grid, up to 96 nearest edges. Height, vertical and tangential speed
are kept; after a push `0049AE30` is called.

World cutout is recognised from the race states: ref 128..160, blend 0, Z write, compare not ALWAYS. For it A2C is
off, discard stays, the alpha of a surviving pixel is forced to 1, the sampler is CLAMP in U/V on every mip level.
The WRAP the game asked for is kept and restored when leaving this mode. Ref 10/255, additive, HUD without depth and
frontend keep the old policy.

## Accepted

Wall **accepted** (2026-09-20, Hometown). Cutout **accepted** (same day: light-blue rim, foliage silhouette, corn
texture `0143`, thread along the top of a card).

### Wall

Source: FRD edges in `.hsw`, not SimD. Hometown `walls=2058`.

The real NFS4 walls are the edges of road polygons `POLYVROADDATA` (speedtools `frd.ksy` / `frd_data.py`, the T3ED
field "Polygon Road Flags"):

* edge bytes are almost always `0` or **`255`** (not `1`); a `bool` catches it (HZ5: the byte is the wall height in
  1/4 m units);
* `collision_flags & 0x80` = `has_wall_collision`;
* `& 0x20` = `has_finite_height`; `& 0x40` = `has_object_collision`;
* the low nibble is the surface (already in `mt`), not a wall.

Hometown `tr.frd`: **2058** segments with `0x80` and a non-zero edge, **6 closed outlines** (every vertex has degree
2):

| outline | segments | centre XYZ | meaning |
|---|---:|---|---|
| 0 | 1010 | 118, 5, -38 | long ring |
| 1 | 994 | 128, 5, -30 | second long ring |
| 2 | 22 | -546, -1, 23 | small island |
| 3 | 16 | 608, 27, -534 | small island |
| 4 | 8 | -187, 1, 408 | small island |
| 5 | 8 | -58, -6, 399 | small island |

Start vroad0 ~ `(638, 10, 105)`; the SimD walls there are 19.8 / 23.8 m: an envelope, neither a fence nor an island.

Blender OBJ (Y up, metres) from `tools/track/export_hs_walls.py`: `<track>-actual-walls.obj` (FRD walls, orange
16 m, crimson only at an overpass), `<track>-old-corridor.obj` (the SimD corridor, light blue),
`<track>-road-context.obj` (road quads, grey).

**Height, accepted in Blender (2026-09-19).** 16 m everywhere. Shorter only for an edge whose XZ lies under the road
of **another** block: `|delta block| >= 8`, clearance >= 2.5 m. Redrock has **10** such segments (clearance 3.1...5.1
m), the two blocks ~105 deg apart. Hometown 0. Do not use `finite` from the FRD and do not cut the banking of the same
block: that produced a saw along a spiral (1372 false cuts). (HZ5 later took the height from the edge byte.)

### Cutout (Hometown, 2026-09-20)

Three edges, three causes.

**1. Light-blue rim between two trees.** The converter cloned Opaque and always wrote `pr` queue **2**. Stock
canyon/alps draw foliage `FakeSort` queue **1**. Queue 1 sorts the cards; the rim on the front tree disappeared right
after the CRP change. Material key `(tex_id, surface, cutout)`: a holey bush does not share the Opaque slot of the
same image on asphalt. Road and markings 0-9 stay Opaque. Template: alps `bsha` (`mt+4 = 0x0281`), not `alt1`.
Hometown after `hole_frac=0`: 39 FakeSort, 838 queue-1 primitives. `test_hs5_geometry` requires FakeSort and queue
1/2.

**2. Black / light silhouette against the sky.** `FakeSort` queue 1 is byte 2 = 1 -> `pr[0] & 0x10000` ->
`FUN_00466A90`, flushed by `00466660` via `esi+0C` with `alphaRef=10`. Pass 0xA0 (`004666AF`, `esi+8`) is another
queue (`0x100000` -> `FUN_004669C0`). So at first there was no cutout log line (it logged only for ref >= 64) and the
leftover 10/255 kept a bilinear edge. `GenerateMips` averaged the transparent black colour key into RGB (a dark
rim); mips are now alpha weighted like `nfs5_fsh.downsample`. The RGB of opaque neighbours is bled into every A=0
texel (FSH `dilate_alpha_rgb` and the runtime's own dilation on upload). Not enough alone: at ref 10 the rim is the
alpha edge, not the hole colour.

The runtime flags a binary colour key on upload (holes and A >= 128, mid alpha < 5%, holes >~ 8%). In a race with
blend 0 and Z write that is a world cutout: no A2C, discard, `CoverageAlpha=1`, sampler CLAMP, threshold 0x80 in
the shader constants; the game's `setstate 0x24` is not changed (cars/sprites stay at 10/255).

**3. Grey thread along the top of a card.** NFS4 UVs are exactly 0/1 (`quad_uvs`). The texture edge has crumbs with
A>0; anisotropic filtering with CLAMP pulls them into a line against the sky. The cutout frame is zeroed by alpha
(1 px); the shader inset for a hard cutout is **1.5** texels (half a texel only helps against WRAP).

Cars, additive, HUD without Z and the frontend keep their policy.

## HS16: wall ~0.3 m "beyond" the visible one: circle instead of body (2026-09-22)

Symptom: the car first drives onto the visible wall and only then hits an invisible one "about 0.3 m further".

Cause: the collision model, not the `.hsw` coordinates (FRD edges are written untransformed and match the CRP
geometry). The car was an **XZ circle with the half width** `car+3A8` (0.94 m), the body an OBB 0.94 x 0.63 x 2.10 m
(`car+3A8/3AC/3B0`, axes `car+364/370/37C`). A body corner passes the wall by `hw*cos(t) + hl*sin(t) - hw`: 5 deg ->
0.18 m, 10 deg -> 0.37 m, 20 deg -> 0.78 m, head-on -> 1.16 m.

Fix: `HsCarBox` + SAT in XZ along the edge normal and the body's horizontal right/forward axes; the box support uses
all three axes (car tilt included). The axis with the largest separation is chosen; at the end of a wall the car is
pushed along its own axis. 1/32 prediction and tangential speed kept.

Pipeline knob for a residual gap: `export_hs_walls.WALL_INSET_M` (default 0) and `convert_track.py --wall-inset M`:
move each edge M metres towards the centroid of its road polygon (negative = outwards). Rewrite only the walls:
`convert_track.py --all --walls-only --wall-inset 0.1 --install <game folder>`. With 0 the `.hsw` is byte for byte
the previous one.

## HS17: wall hit without sound/sparks: hand the response to the game (2026-09-22)

Symptom: the car does not hit the invisible wall, it "stops": no sound, no sparks, no damage. The hook only clamped
the position and cut the speed.

The stock PU path (`FUN_00495020`, called from the `00498FF0` step): for the 8 OBB corners an `.edg` edge is searched
(`FUN_004740d0`), but **only for corners outside the slice** (`FUN_00474060`, `flags & 0xF`), so on a wide HS
corridor it never fires. On a hit: `DAT_00628C88` = edge material (low byte; monaco 0 -> clamped to `0x60`),
`FUN_00494000(car, normal, point, push, 1)`: impulse `vel += n*(-vn*1.1)` (005B27A4), yaw kick `FUN_00493f10`, hit
class `car+0x438` (`0x4000d` metal when |vn| >= speed*0.125, else scrape `0x4001a`), returns the hit force `|vn|*3`
(005B2600); the position push `push*1.05` applies only if the moved point is outside the slice. Then `car+0x440` =
contact point, `FUN_0040a630` (timer), for force > 20 (005B2470) `car+0x430` = force and
`FUN_00494df0(car, normal, 1)` -> damage panel / sparks (`FUN_00494500`). All cdecl (`00495521: add esp,18h`,
`00495568: add esp,0Ch`).

The hook now (`ReplayWallHit`, pre phase, before its own correction) finds the nearest `.hsw` wall (`HsWallNearest`,
SAT normal towards the car) and, if `vn < 0` and the contact condition holds, calls exactly that chain with our
normal, the body support point (`HsBoxSupportPoint`, Y = centre) and `push = n*depth`. `HsWallConstrain` still moves
the body out of the wall afterwards. Bounce, sound, sparks and damage are stock. Switch: `[physics] hs_wall_impact=0`.

## HS18: hit "early" with a gap at the corners: hit at the moment of contact (2026-09-23)

Symptom: with a corner the car hits the wall with a gap and often snags; sideways it is fine. Log: `hit ...
vn=-42.32 sep=1.283`: the hit fired on the prediction `sep + vn/32 <= 0` and bounced while up to `|vn|/32` (1.3 m at
42 m/s) remained. Head-on and at an angle `vn` is large, sliding sideways small: hence "at the corners".

Fix: `ReplayWallHit` first moves the car along -n by `sep` (to the contact), then hands the hit to the game; the body
is rebuilt (`0049AE30`). The SAT separation is never more than the true gap, so it never pushes into the wall.

## HS19 (2026-10-19): phantom head-on hit beside a wall

Symptom: a light side graze of an invisible wall stops the car dead and flips it. Crash recorder (hometown, slice
777, 51.5 m/s): `OURHIT n(-0.99 -0.14) sep 1.263`, the normal = -forward of the car, velocity 51.5 -> -3.8 m/s,
+9 m/s up. The rock wall (.hsw segments 1683/1685/1687) was 1.28-1.33 m to the side during the whole step: no contact.

Cause: `ReplayWallHit` predicted with `HsBoxSegSeparation` at the current position, whose axis is the one of LARGEST
separation. For the next piece of a wall polyline that starts ahead of the nose, that is the car's forward axis
(nose -> shared vertex, 1.28 m vs 1.285 m on the wall normal: a tie broken by 1e-3). `sep + vn/32 < 0` with vn = full
speed then "predicted" a head-on contact; HS18 moved the car the gap forward and the stock 00494000 bounce reversed
it. The lookahead clamp had the same flaw (phantom braking). The crash log held 1451 such player hits (n.fwd ~ -1,
sep > 0.5 m) on all 19 HS tracks.

Fix: `HsBoxSegSweep` / `HsWallSweep` = translational swept SAT: contact time = the latest entry over the wall normal
and the car's right/forward axes, a hit only if all overlap at once within the step; its axis is the normal, the gap
is the current separation along it. Used by ReplayWallHit when the car is not already inside a wall and by the
lookahead. Also catches a > 4 m/step head-on (no tunnelling). race_test hometown + france 4x120 s PASS, no respawns.

### HS19b (same day): ghost vertex of a wall polyline

Second report (hometown, slice 440, 54 m/s): side touching the left wall (sep -0.004, n = wall normal), four steps
later `n(0.20 -0.98) sep 0.395` = -forward again -> stop + flip. Geometry: .hsw 930 -> 932 almost collinear
(179.4 deg), the car's side 0.04 m from the line, the nose 0.42 m before the shared vertex. With the side already
overlapping the line, the only axis still to "enter" for segment 932 is the car's forward axis, so even the swept SAT
reports a head-on contact with the next piece's first vertex (the classic internal-edge problem).

Fix: `HsWallSharedEnd`: when the chosen axis is a car axis and the touched end of the segment coincides (5 cm) with
an end of another wall piece in the gathered set, the contact uses the segment's own normal
(`HsWallNormalSeparation`) instead; a sweep contact that is not moving into that normal is dropped. Free ends (pole
tips, wall ends whose neighbour faces away and is filtered by one-sided walls, HZ7) keep the car-axis contact.
