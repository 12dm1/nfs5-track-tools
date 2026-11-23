# `GameData/Track/Sky/<Track>.bin` (sky configuration, version 9)

Binary, little-endian, exactly 0x71DC bytes, **not** RefPack-compressed. Loaded whole by `FUN_00459660` (block
`SkyCnfig`), falling back to `Sky\default.bin`. Research and formulas: [../findings/ATM1-sky-atmosphere.md](../findings/ATM1-sky-atmosphere.md).
Reader: `tools/track/nfs5_sky_bin.py`. Writer: `track_atmosphere.to_sky_bin` (template + patch).

| offset | type | field | meaning |
|---|---|---|---|
| 0x000 | u32 | `version` | 9 (older versions are upgraded by `FUN_00459700`) |
| 0x004 | f32 | `horizon_angle` | start angle of the horizon ring, **turns** |
| 0x008 | i32 | `horizon_rotation` | shifts the `hzcl` horizon vertex colours (16 directions) |
| 0x00C | i32 | `horizon_columns` | panels per row of the `horz` atlas (4) |
| 0x010 | i32 | `horizon_rows` | rows of the atlas (2; Monaco 1) |
| 0x014 | f32 | `horizon_bottom` | bottom edge of the horizon band (y) |
| 0x018 | f32 | `horizon_top` | **height** of the band; top edge = bottom + height |
| 0x01C | u16[16] | `horizon_slots` | panel per ring face: `panel` 0..7, `+256` = mirrored |
| 0x03C | f32 | `angle` | dome rotation, turns |
| 0x040 | f32 | `dome_radius` | side of the 16x16 dome grid |
| 0x044 | f32 | `dome_curvature` | `y = height - curvature*(a^2+b^2)` |
| 0x048 | f32 | `dome_height` | |
| 0x04C | 50x8 | legacy curve A | stale in v9 files, keep as in the template |
| 0x1DC | 50x48 | legacy weather | stale, keep |
| 0xB3C | f32x4 | `water_angle`, `water_dome_radius`, `water_dome_curvature`, `water_dome_height` | the sky reflected in water (`...water` layers) |
| 0xB4C | 50x8 | legacy curve B | stale, keep |
| 0xCDC | f32 | `sun_x` | sun position in the dome plane |
| 0xCE0 | f32 | `sun_y` | |
| 0xCE4 | f32 | `sky_scale` | scale of dome and sun (the horizon ring radius 400 is not scaled) |
| 0xCE8 | 4 x 60 | sky colours per detail level | `i32 in_use`, then 2 blocks (normal, water) of `i32 follow_fog, f32 colour_A[3], f32 colour_B[3]` (0..255). The game takes the current level and walks down to the first `in_use`. Fill all four the same |
| 0xDD8 | i32 | `lens_flare` | 1 = flare system on (needs THRASH `readrect`) |
| 0xDDC | 400 x 8 | region curve | `i32 slice, f32 value 0..1`: mixes sky colours A/B and the `.txt` layer sets A/B |
| 0x1A5C | 400 x 48 | weather | see below |
| 0x655C | 400 x 8 | unknown curve | values uninitialised (0xCB) in stock files |

Weather record (48 bytes):

```
+0x00 i32  slice
+0x04 f32  fog density A (sky + horizon)   -- the game cubes the interpolated value
+0x08 f32  fog density B (world)
+0x0C f32  0
+0x10 u32  fog colour A, ARGB (alpha 0xFF)
+0x14 u32  fog colour B, ARGB
+0x18 f32  rain 0..1
+0x1C f32  storm 0..1  (< 0.04 none, < 0.33 class 0, < 0.66 class 1, else class 2)
+0x20 f32  snow 0..1
+0x24 f32[3] 0
```

Rules:

* every table ends with a record whose slice is **negative**; at most 400 records, more silently corrupts the next
  table;
* interpolation between keys is linear in slice (`FUN_0045a560`, wraps on circuits), then the density is cubed;
* `follow_fog` is an integer; 1 = the sky colour comes from fog A;
* write files by patching a stock template (`Coastal.bin`) so legacy areas and unknown fields stay valid.
