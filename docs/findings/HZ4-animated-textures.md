# HZ4: NFS4 animated textures and the sky in the Lost Canyons cave

Date: 2026-10-18. This file also holds HZ5 (invisible walls: structures and shortcuts), HZ6 (fringe on masked
textures) and HZ7 (texture names after the first race, object collision, one-sided walls).

## 1. How NFS4 animates textures

The polygon `anim` byte (`Polygon +12`, 13-byte FRD record) and the renderer `nfshs.exe FUN_00434690`:

```
if anim != 0:
  if texflags & 0x2000 == 0:            # frame switching
      tex = base + (clock / (anim >> 3)) % (anim & 7)
  else:                                 # scrolling
      v += (clock & 0x3FF) * (1/1024) * 15   # constant 0x59289D = 1/1024
```

`clock` = `_DAT_00853B5C`, ticks of 1/64 s. The frames are consecutive in `tr0.qfs` (after the `<mirrored>`
selection, like all FRD texture ids). Scrolling gives exactly 15 repeats per 1024 ticks, so the wrap is seamless.

13 tracks have 32 such textures. Examples:

* Aquatica: bubbles 151 (3 frames of 6 ticks) and water 165 (6 of 10);
* Redrock, Hometown, Country, Rocky Pass, Summit: water, 4 frames each;
* Empire: 10 textures;
* Dolphin Cove 168/197 and Snowy 107/108: scrolling.

Within a track, animated textures are used only on animated polygons (exception: Lost Canyons 224, 6 static of 35),
so animating the texture itself is equivalent. Lost Canyons byte 60 asks for 5 frames, but the fifth texture has a
different size, so 4 are played.

## 2. Implementation for PU

PU has no such mechanism; the runtime does the work.

* `tools/track/nfs5_texanim.py` writes `GameData/Track/<track>.tan`: the base texture tag, the kind, the frame count,
  the delay, the scroll step and the BGRA pixels of frames 1...n-1. The frames are taken from the **installed**
  `<track>.fsh`, so replaced or upscaled textures keep their size. Format: [../formats/tan.md](../formats/tan.md).
* Runtime (PC wrapper `tex_anim.cpp`; nfs5-web `runtime/web/r_web.c`): the game uploads a texture (`Tex_Update`);
  its DTEX name equals the FSH tag. If the tag is in the `.tan`, frame slots are created (same processing as a game
  load: dilation, cutout classification, mips; at reduced texture detail the frames are box-downscaled). The bind
  substitutes the current frame from a 64 Hz clock, and the vertex flush shifts `v` for scrolling. Freeing the
  texture or a texture reset frees the frames.
* PC wrapper INI: `[content] texture_animation=1` (default), 0 turns it off.

Check: race_test on 6 tracks with rendering; every animated texture logs `HZ4 texanim: slot ... frames=...`, all runs
PASS.

## 3. The sky in the Lost Canyons cave

In Lost Canyons `tr.ini`, fog regions 0 and 1 take the colour to black at slices 850...910...1066...1077. The NFS4
sky is blended with the fog (`fogHorizon=0.80`), so in the cave it is black. Our atmosphere keys also had black fog,
but with the NFS4 density 0.071. The PU sky hangs at `dome_radius` and at that density is barely fogged. Also the PU
sky colour `colour_a/b` is only the background colour under the layers (`FUN_00459a70` -> `FUN_00466e60`); it does
not darken the sky layers.

A data-only fix: in `data/atmosphere/lostcany.ini`, keys 910/1000/1066 raise fog **A** density (sky + horizon) with a
black colour. Fog **B** (world) is untouched. On the way in (850 -> 910) and out (1066 -> 1077) the sky darkens and
brightens smoothly. Rebuild the sky: `python tools/track/convert_track.py Lostcany --sky-only --install <game>`.

Open: the sun sprite is not fogged. If the sun is visible in the cave, that is the next step.

**HZ7 correction.** Density 1.0 did not work: the game writes the cube of the density into the fog state, and the PC
wrapper (`THRASH_STATE_FOG_DENSITY`) discarded any value `>= 1` as garbage and **turned fog off**. The keys are now
0.9 (cube 0.73; the dome at a depth of ~116 m still goes black), and the wrapper accepts densities up to 64. **Any
runtime must accept fog densities >= 1.**

---

# HZ5: invisible walls: structures and shortcuts (2026-10-18)

## Structures: wall height from the edge byte

POLYVROADDATA (+4...+7) holds one byte per edge of a road polygon. 0 = no wall, **0xFF = full wall, any other value =
wall height in 1/4 m**. Proof: the Atlantica ramps, where the player could not jump down (slice 237, player on a deck
at y = 9.7 above the road at y = 0). Edges with values 24/26/28/30/32 have bases at 3.4/2.8/2.3/1.8/1.3 m, and all
tops are at 9.3 m: the edge of the deck. Values 1-2 are kerbs. Lost Canyons shows the same picture.

`.hsw` used to give every wall 16 m, so the deck was fenced in. Now `export_hs_walls.wall_segments` writes
`edge_height`, and height = min(16 m / overpass, byte x 0.25). The runtime ignores a wall when the car centre is
above `base + h - 0.2`. Walls with a finite height: Empire 141, Atlantica 23, Rocky Pass 16, Aquatica 14, Summit 12,
the rest 0-5.

## Shortcuts: the slice corridor

Drivable NFS4 road polygons (with POLYVROADDATA) extend 10-58 m beyond the vroad `leftWall/rightWall`:

* Atlantica 836-855, 962-969;
* Aquatica 257-370;
* Lost Canyons 111-120, 1016-1019;
* Empire 78-121, 676-723;
* Hometown 555-559.

The NFS4 envelope ended up in two places:

* SimD `+0x40/+0x44`: the off-track check `FUN_00408750`. It compares `|car+ED0|` with `leftWall` on **both** sides
  (+0x44 is never read) and triggers `FUN_0040F540` (respawn). The AI reads it too.
* `.edg`: the barrier read by the AI and the camera.

The real NFS4 walls are in `.hsw`, so on these slices the corridor is widened to the edge of the drivable road
+ 1.5 m (`track_corridor.widen_for_shortcuts`). For SimD the left wall also covers the right side.

* **Converter:** `build_sim_blobs` and `nfs5_track_files.convert`.
* **Installed game:** `apply_corridor.py --restore` (SimD) and `apply_corridor.py --edg` (barrier from the real walls
  plus widening).
* **Amount of widening:** Lost Canyons 1047 slices of 1690 (up to +58 m), Aquatica 612, Hometown 478, Empire 459.
* **race_test** with three opponents on 6 tracks: 0 respawns, 0 stops.

The previous `.hsw` files equal an `export_hs_walls` build with `inset 0`.

---

# HZ6: fringe and a line along the quad edge on masked textures (2026-10-18)

Symptom (Atlantica, hedges 0072/0073/0079/0080): a light blue/turquoise fringe around the foliage outline and a
straight line along the quad edge where the texture is transparent. The same in places on trees and fences of other
tracks.

Cause: the PC wrapper did not enable its "hard" mask path for these draws (`HardCutoutWanted`: threshold 0x80,
surviving pixels opaque, CLAMP sampler, 1.5 texel inset):

* **8 % threshold.** `ClassifyBinaryCutout` required >= 8 % holes. NFS4 hedges have 4.6-7.7 % (a silhouette along one
  edge), and every track has 1-12 such textures. Without the mask, WRAP pulls in the opposite edge (the line along
  the quad edge), and alpha 10/255 with blending gives a translucent rim.
* **Sorted queue.** NFS4 masks arrive with an alpha-test threshold of 10/255, and some draws have no depth write
  (FakeSort). `HardCutoutWanted` required `depthWrite`, so even masks with >= 8 % holes went without the hard path in
  those draws.

Fix (renderer side):

* a texture is `keyed` when its alpha is binary: >= 1 % holes and <= 1 % intermediate alpha. Car pages `CAR*` are
  excluded: their alpha is the paint zone. `cutout` keeps the old rule. Border clearing works for `keyed`.
* the hard path checks `keyed`. Without depth write it is enabled only for a threshold >= 10/255.
* the vertex flush disables it for one draw if any vertex alpha < 0xF8: a fading object keeps its blending.
* log: `HZ6 keyed draw:` for the first draw of every such texture (threshold, hard, depth write, minimum vertex alpha,
  capture pass).

Check:

* **race_test mode.** race_test runs in 16-bit mode: `race video mode` is off and all cutout paths are inactive. A
  temporary build with the mode forced was used. On Atlantica all four hedge textures had `hard=1`, with and without
  depth write.
* **Run with three opponents.** alps, coastal, hometown, empire, park, france, industrial, lostcany: all PASS. New
  `keyed` textures in the main view: hometown 0138/0157, france 0158, industrial shpc.
* Not checked by eye.

---

# HZ7: texture names after the first race, object collision, one-sided walls (2026-10-18)

## Texture names (the fire in the Lost Canyons cave)

The runtime takes the DTEX name (FSH tag) from the callback `006A3A50`. On every race the texture manager reinstalls
the stock handler (`push 004677D0` at `00467F68`/`00467F81` -> setter `005556A1`), and the hook silently vanished.
From the second race on, every load got the last name of the first race (`0026`). `.tan` found no tags, and the Lost
Canyons fire stopped animating. The `CAR*` check of HZ6 suffered too. Now the runtime patches both `push`es to its own
callback, so the hook survives a race restart. **Any runtime that keys data by texture name must do the same.**

## Objects with collision (posts, cacti, trees)

`RefExtraObject.collision_type == 1` (static) on normal XObj (type 4). The runtime had no collision for them.
`export_hs_walls.object_segments` turns every steep face of such an object (`|n.y| < 0.7|n|`) into a one-sided wall
at its base:

* the bottom 2 m of the face;
* trimmed to the opaque texels of the texture (a palm made of crossed 4.6 m quads keeps a 0.25 m trunk);
* a gap over 0.8 m splits the wall into parts;
* height = the face height, base = its bottom. So the top of an arch over the road does not touch a car under it.
* **Translucent textures** (trees, palms, cacti, sprite posts) give only a trunk: the opaque pieces of the bottom
  0.5 m, each shrunk to 10 cm around its middle. You can drive through branches. Opaque objects (rocks, booths,
  pillars) keep their full width.

The FRD face normal is `-newell` (on a closed post all faces point outwards). Two-sided faces (no cull) give a wall
both ways. The texture V orientation was checked on the Atlantica palms. Amount: from 0 (GT) to 1691 (Summit) walls.

## One-sided walls

A wall edge is walked so that the road lies to the left of a->b. This holds for all edges except 31 on Empire, which
`_oriented` flips. The runtime checks a wall only when the car centre is on its free side. As in NFS4, a wall keeps
the car on its road, and entering from outside is free. This does **not** fix the "invisible ceiling" hit on the Lost
Canyons shortcut (checked by the user); that is the open task HZ8.

## `.hsw` format

Unchanged `HSW1` + count + wall records. Then a tail: `HSX1`, flags (1 = one-sided), count, then the object records.
An old runtime does not read the tail and works as before. Full layout: [../formats/hsw.md](../formats/hsw.md).

* **race_test:** 8 tracks with objects, 3 bots: PASS, 0 respawns and 0 stops, hits per car as in the baseline.
