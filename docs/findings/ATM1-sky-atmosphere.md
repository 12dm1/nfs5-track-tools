# ATM1: track atmosphere: `Sky/*.bin`, `Sky/*.txt`, sun, fog, precipitation

Date: 2026-09-21. The game was not started; everything comes from decompilation and parsing of stock data.
Parser: `tools/track/nfs5_sky_bin.py`. **Read the ATM10 corrections at the end first**: they fix several statements
in the main text. The corrected field table is in [../formats/sky-bin.md](../formats/sky-bin.md).

## Why

The NFS4 -> PU pipeline used to **copy someone else's `.bin` as a whole** (`sky_bin_for()`): `alps.bin` for snowy
tracks, `Coastal.bin` for the rest. So all 19 converted tracks had fog turning blue at slice 440 and snow from slice
648 to 1120, whatever was really there (`Gt1.bin`, `Snowy.bin`, `Summit.bin` were byte-identical to `alps.bin`). To
fix it properly a full format breakdown was needed; it is below. (Today the pipeline generates the `.bin` from the
source track: `tools/track/track_atmosphere.py` and `data/atmosphere/<track>.ini`.)

## Overview: what controls what

| file | what it sets | format |
|---|---|---|
| `Sky/<Track>.bin` | dome, sun, horizon, sky colours, **fog and precipitation curves by slice** | binary, 0x71DC, version 9 |
| `Sky/<Track>.txt` | sky layers (textures, speed, wind, warp) for two detail levels x two weather regions | text with comments |
| `Sky/<Track>.fsh` | sky textures: `st*` (sky map), `sc*`/`cc*`/`cm*` (clouds), `hzcl` (horizon colour), `hor0..hor3`/`horz` (panorama) | SHPI |
| `GameData/Render/particle.fsh` | precipitation textures `snow`, `rain` | SHPI |
| `GameData/Render/common.fsh` | sun and flares: `SUNA`, `GLAR`, `FLR0`, `FLR1`, `spik` | SHPI |
| `GameData/Render/flare.dat`, `flarepos.dat` | global flare configuration (not per track) | binary |
| `<track>.crp`, `df` parts | **baked per-vertex light**: the strongest "atmospheric" effect, and it is NOT here | see [T007](T007-pu-track-formats.md) |

RTTI class names confirm the structure: `SkyLevel_Base`, `SkyHorizon`, `SkyLevel_BasicSky`, `SkyLevel_MultiSky`,
`SkyLevel_FreeClouds`, `SkyMapSource_Texture`, `SkyMapSource_Noise`, `Scroller_BasicSky`, `Scroller_FreeClouds`,
`R3DSkyMapTextureLayer`.

## `Sky/<Track>.bin`: field map

Loaded as a whole (`FUN_00459660`, the block is called `SkyCnfig`), with a fallback to `Sky\default.bin`. The first
dword is the version; `FUN_00459700` upgrades old versions to 9. All stock files are version 9, exactly 0x71DC bytes.

| offset | type | field | note |
|---|---|---|---|
| 0x000 | u32 | `version` | 9 |
| 0x004 | f32 | `horizon_start` | 0.0...1.0, per track (ATM10: start angle of the horizon ring, turns) |
| 0x008 | i32 | `horizon_rotation` | start index into the 16 horizon slots (`FUN_00459d30`) (ATM10: shifts the `hzcl` colours) |
| 0x00C | i32 | `horizon_columns` | 4 in all files (ATM10: panels per row of the `horz` texture) |
| 0x010 | i32 | `horizon_rows` | 2, Monaco 1 |
| 0x014 | f32 | `horizon_bottom` | -28...-183 |
| 0x018 | f32 | `horizon_top` | +115...+367 (ATM10: band height, not the top edge) |
| 0x01C | u16[16] | `horizon_slots` | 16 slots around the ring; value = panel 0...7, `+256` = mirrored |
| 0x03C | f32 | `angle` | sky rotation (ATM10: in turns, not radians) |
| 0x040 | f32 | `dome_radius` | 500...1500 |
| 0x044 | f32 | `dome_curvature` | 2.0e-4 everywhere |
| 0x048 | f32 | `dome_height` | 44...125 |
| 0x04C | - | legacy curve A | 50x8, version < 9 |
| 0x1DC | - | legacy weather | 50x48, version < 9 |
| 0xB3C | f32x4 | `water_*` | copy of {angle, radius, curvature, height} for the sky reflected in water; filled only for Coastal/Dolphin/Hometown |
| 0xB4C | - | legacy curve B | 50x8, version < 9 |
| 0xCDC | f32 | `sun_x` | |
| 0xCE0 | f32 | `sun_y` | |
| 0xCE4 | f32 | `sky_scale` | overall scale of the dome and the sun (alps 1.5, coastal 0.236) |
| 0xCE8 | 4x60 | sky colours by detail level | see below |
| 0xDD8 | i32 | `lens_flare` | 1 in all stock files |
| 0xDDC | 400x8 | **region A<->B curve** | (slice, 0...1) |
| 0x1A5C | 400x48 | **weather** | fog, rain, snow |
| 0x655C | 400x8 | second curve | in all stock tracks the values are uninitialised (0xCB), the slices are valid |

Tables end with a record with a negative slice number, at most 400 records.

### Dome and sun

`FUN_00461090` and `FUN_00460ec0` build a paraboloid:

```
y = dome_height - dome_curvature * (x^2 + z^2)
```

rotated by `angle` around the vertical and multiplied by `sky_scale`; `dome_radius` sets the extent. The sun position
is on the same paraboloid:

```
sun.x = ( cos(angle)*sun_x + sin(angle)*sun_y ) * sky_scale
sun.y = ( dome_height - (sun_x^2 + sun_y^2)*dome_curvature ) * sky_scale
sun.z = ( cos(angle)*sun_y - sin(angle)*sun_x ) * sky_scale
```

So the sun is set by **two numbers in the plane**, and its height comes from the dome curvature. Alps: (-122.1,
-26.4) with dome height 125 and scale 1.5; Coastal: (-289.1, -204.1) with scale 0.236. (Angles are in turns, see
ATM10.)

`lens_flare` (0xDD8) enables the flare system `FUN_00460460`: every 200 ms it does a THRASH `readrect` 4x4 at the sun
point (occlusion test) and draws up to 14 elements whose positions come from `GameData/Render/flarepos.dat` (14
floats: 1.0, 0.75, -0.75, 0.5, -0.5, 0.25, -0.25, 0, -1.0, then zeros: coordinates along the "sun -> screen centre"
axis). Textures: `SUNA`, `GLAR`, `FLR0`, `FLR1`, `spik` from `common.fsh`. These are global files, not per track; the
`.bin` only holds the sun position, scale and the switch. **A runtime must implement `readrect`** or the flares never
show (the PC wrapper does this as "G7").

### Sky colours by detail level (0xCE8)

Four records of 60 bytes, index = detail level (`DAT_00657d9c`, also the index of the `low`/`medium`/`high`/`amazing`
blocks in the `.txt`). `FUN_00459950` takes the record of the current level and **walks down** until it finds one
with `in_use != 0`.

```
+0x00 i32  in_use
+0x04 block 0 (normal sky pass)                    28 bytes
+0x20 block 1 (pass with flag 0x10: water reflection)  28 bytes

block: i32 follow_fog, f32 colour_A[3], f32 colour_B[3]   (components 0...255)
```

`FUN_00459a70`: if `follow_fog != 0`, the sky colour **comes from the fog** (`FUN_0045d2b0` -> `FUN_0045a720(t, 1)`),
otherwise it is a linear mix of `colour_A` and `colour_B` by the region curve (0xDDC).

* alps and autobahn: `follow_fog = 1`, the sky is recoloured together with the fog. That is why their records 1 and 2
  still hold the EA editor default (254, 0, 0): those colours are never read.
* Coastal: `follow_fog = 0`, block 0 = (254, 209.7, 196) sunset, block 1 (water) = (254, 170.7, 165.5): the sky and
  its reflection have different colours.
* Forest: (119, 187, 254): clear blue sky.

**For an editor:** fill all four records the same way; then the question "which one will the game take at this detail
level" never comes up.

### Weather record (0x1A5C, 48 bytes)

```
+0x00 i32  slice (key)
+0x04 f32  fog density A      -> cubed -> grFogTable
+0x08 f32  fog density B
+0x0C f32  ? (0 everywhere)
+0x10 u32  fog colour A (ARGB)
+0x14 u32  fog colour B (ARGB)
+0x18 f32  RAIN    0...1
+0x1C f32  STORM   0...1  (lightning frequency, see below)
+0x20 f32  SNOW    0...1
+0x24..0x2C  ? (0 everywhere)
```

Channels A and B are two fog passes (`FUN_0045d2e0` and `FUN_0045d3b0`, both from `FUN_0045d4f0`). **ATM10: A = sky,
B = world.** Interpolation: `FUN_0045a560` returns `1 - (slice - lo) / (hi - lo)` with wrap handling for circuits,
then a per-component colour lerp (`FUN_0045a720/800`) and a density lerp with the cube (`FUN_0045a6a0/6e0`). It goes
to the driver through `FUN_00540480`: state **0x15** (mode), **0x0E** (density), **0x0F** (colour); these are Glide
states.

### Curves by slice (0xDDC and 0x655C)

Records of 8 bytes: `i32 slice`, `f32 value`. Same interpolation (`FUN_0045a350`). The 0xDDC curve is the **A<->B**
factor: it mixes the sky colours (when `follow_fog = 0`) and the layer textures of region A and region B from the
`.txt` (`st1a` <-> `st1b`, `sc1a` <-> `sc1b`). On alps it goes 0 -> 1.0 by slice 604, so the sky really moves from
the light `st1a` (146,140,149) to the dark blue `st1b` (77,88,108).

The 0x655C curve has valid slices in all stock tracks, but the values are 0xCB: uninitialised memory. Purpose
unknown.

### Legacy areas

Before version 9 three tables lived at 0x4C, 0x1DC and 0xB4C, 50 records each. In stock v9 files they hold **stale**
data (the alps legacy copy has, for example, snow 0.346 at slice 439, which the current table does not have). The game
at version 9 does not look there. When writing your own `.bin`, keep those bytes as in the template.

## `Sky/<Track>.txt`: sky layers

The file documents itself in comments. Structure: several `BEGIN ... END` blocks, each with its detail level (`LOW`,
`MEDIUM`, `HIGH`, `AMAZING`: 20-byte strings from 0x5CF3A0, same index `DAT_00657d9c`). All stock tracks have two
blocks.

Inside a block: the layer type and **two weather regions (A and B)**, each with three sky map textures, X and Y
scales, `move ratio`, wind speed and direction (three values each, one per texture), warp speed and warp amount; at
the end of the block three cloud textures.

Type dictionary (`FUN_0045fb80`, strings 0x5CF404...0x5CF4FC) and the classes behind them (`FUN_0045d800`):

| type in `.txt` | flag | class |
|---|---|---|
| `basic` | 0x02 | `SkyLevel_BasicSky` |
| `multinormal`, `multiadditive`, `multiattenuate`, `multimodulate` | 0x04 / 0x08 / 0x10 / 0x20 | `SkyLevel_MultiSky`, argument 0...3 |
| `freecloud` | 0x40 | `SkyLevel_FreeClouds` |
| `config` | - | a settings-only block |
| `...water` (`basicwater`, `multinormalwater`, ...) | +0x200 | the same class in the "water" variant: uses the mirrored field set at 0xB3C |
| `additive` | - | **no longer supported**, the game prints "use multiadditive" |

The mapping of each `multi*` to 0...3 was not traced.

`SkyHorizon` is always added as the last layer (`FUN_00459b50`): the horizon panorama; the texture is chosen by detail
level from the table `hor0`/`hor1`/`hor2`/`hor3` (VA 0x5CEF14, 6-byte step). Stock FSH files contain `hor0`
(industrial), `hor1`/`hor2` (canyon) and the common `horz`; the latter appears to be the fallback.

### Storm (+0x1C)

`FUN_0045d030` interpolates the field by slice (`FUN_0045a910`) and passes it to `FUN_0045ccf0`, which rolls a die
every tick:

| value | class | lightning chance per tick | thunder sound |
|---|---|---|---|
| < 0.04 | - | no lightning | - |
| 0.04 ... 0.33 | 0 | `dt * 0.0333` | 40 |
| 0.33 ... 0.66 | 1 | `dt * 0.0667` | 80 |
| >= 0.66 | 2 | `dt * 0.1` | 126 |

`FUN_0045cd90` sets up the flash; size and offset come from per-class tables (0.4/0.3/0.2 and 0.2/0.1/0.1), the scene
width from `dome_radius` (0x40), and it plays thunder with a delay (random value x 65535), imitating the distance to
the strike.

Stock values: autobahn 0.135 (class 0), industrial 0.545 (class 1). Nobody uses class 2: a free "heavy storm"
setting.

## Precipitation

This is **not** a sky layer and not `particle.ini`; it is a separate subsystem. `FUN_00459260` takes the
slice-interpolated "snow" (+0x20) and "rain" (+0x18) values and, for each of two emitters, computes the particle count
`count = round(value * scale)`, scattering particles randomly in a volume around the camera. The texture is chosen
from the table `sno0 sno1 sno2 sno3 ran0 ran1 ran2 ran3` (VA 0x5CEEB8, tags stored byte-reversed) with a fallback to
the tag `snow`, which is in `GameData/Render/particle.fsh` along with `rain`.

What stock tracks do:

* **alps** is the only one with snow: 0 up to slice 440, short bursts of 0.27...0.41 in the gaps between tunnels (473,
  504, 538, 557, 578, 605, 619; covered sections per `.map` are 481-495, 545-551, 585-599, 625-639), then exactly 1.0
  from 648 to 1120 and a fall to 0 by 1343.
* **autobahn**: rain, 1.0 at the start, then switching on and off roughly every 50-100 slices, zero under bridges and
  in tunnels.
* the rest: zeros.

## Summary for alps (what started it all)

Four mechanisms, all tied to the slice number, all switching over the same stretch of the climb:

1. **Baked light** (`df` in `alps.crp`): (113,107,135) in the valley -> (72,101,147) at the top, while the textures
   there are snowy (~220,221,236), so the cold light shows at full strength. This is not in the `.bin` but in the CRP
   vertices.
2. **Fog**: the colour moves (139,103,87) -> (46,71,121) over slices 436...648, density 0.147 -> 0.260 (after the
   cube: 5.6 times denser).
3. **Sky**: `follow_fog = 1`, the sky colour follows the fog; plus the 0xDDC curve moves the layers from `st1a` to
   `st1b` by slice 604.
4. **Snow**: 1.0 over slices 648...1120.

## Still unknown

* Weather record fields `+0x0C`, `+0x24`, `+0x28`, `+0x2C` (zero in all files).
* Separate `autobahn_lightning.bin` / `industrial_lightning.bin` (5216 bytes, a different structure): not parsed;
  they have nothing to do with the storm field `+0x1C`, which lives in the normal `.bin`.
* Purpose of the 0x655C curve.
* Exact mapping of the `multi*` modes to the argument 0...3.

## Consequences (all done since)

1. `sky_bin_for()` was replaced with generating the `.bin` from the source track. NFS4 `tr.ini` has `[fog]` and up to
   nine `[fog region N]` by slice: direct raw material for the 0x1A5C table. This closed item **E6** of
   [HS1](HS1-nfs4-track-migration.md) (which assumed per-slice fog had to go through the runtime: not needed, the
   mechanism is native).
2. The NFS4 horizon panorama is 8 panels `HDC0...HDC7`, and `horizon_slots` is exactly 16 slots with values 0...7 and
   a mirror flag. The mapping is direct.
3. Snow/rain for converted tracks can be derived from `tr.ini` and the covered sections of `.map` (as alps does).

## Tool

```
python tools/track/nfs5_sky_bin.py <game>/GameData/Track/Sky/alps.bin [--keys] [--curves]
```

Parses all fields and prints the tables. API: `read(path)` -> `SkyBin` with `fog_at(slice)`, `fog_b_at(slice)`,
`precipitation_at(slice)`, `region_blend_at(slice)`; the interpolation follows the game's, including the density
cube. Checked on alps: slice 0 -> (139,103,87) 0.00316; 648 -> (46,71,121) 0.01758, snow 1.0; 1230 -> snow 0.51.

## ATM10 corrections (2026-09-21), from decompiling the sky builders

Decompiled: `FUN_0045d4f0` (sky pass), `FUN_00459b50`/`FUN_00459d30`/`FUN_0045a1a0` (SkyHorizon),
`FUN_00460ec0`/`FUN_00460f40` (dome mesh), `FUN_0045b5a0` (SkyLevel_MultiSky), `FUN_0045ec40`/`FUN_0045f1b0` (UV and
wind). This **corrects** several statements above.

1. **Fog channels A and B are not "two independent passes" but the sky and the world.** `FUN_0045d4f0` sets near/far
   = 0.15/700, enables fog **A** (`FUN_0045d2e0`: density +0x04, colour +0x10), draws the sky levels and the horizon
   **with that fog**, then enables fog **B** (`FUN_0045d3b0`: +0x08, +0x14) and restores the projection; the whole
   world is drawn with it. There are no other calls. So the panorama and clouds dissolve in fog A by depth: on alps in
   the valley (A^3 = 0.00316) the horizon at 400 units is 72 % fogged, at the top (0.0115...0.0176) 99-100 %.
   `follow_fog` takes colour A. `SkyBin.fog_at` = channel A (sky), `SkyBin.fog_b_at` = channel B (world).
2. **Angles are in turns (0...1), not radians.** `FUN_005327e0/f0` = sin/cos(x * 2 pi). This applies to `angle`
   (0x3C), `water_angle` (0xB3C) and the sun formula.
3. **`+0x04` is the start angle of the horizon ring (turns), `+0x0C` is the number of panels per row of the `horz`
   texture (4).** Previously listed as unknown.
4. **`horizon_top` (0x18) is the band height**; the top edge = `horizon_bottom + horizon_top`. The ring has 16 faces,
   radius **400** (a constant, not multiplied by `sky_scale`), point k = (400*sin(a0+k/16), y, 400*cos(a0+k/16)).
5. **The panorama is an atlas of `columns x horizon_rows` (4x2 = 8 panels of 64x128 in a 256x256 `horz`; Monaco
   4x1).** Slot k: panel `slot & 0xFF` -> column `panel % 4`, row `panel / 4`; u = col/4...(col+1)/4 (swapped for
   `+256`), v = (row+0.01)/rows...(row+0.98)/rows. Panorama rows are NOT stacked on top of each other; the
   "duplicated backdrop above" in the first editor preview was a misreading of this.
6. **`horizon_rotation` (0x08) shifts the `hzcl` colours, not the panels.** `hzcl` 16x2 holds the horizon vertex
   colours: column = direction (16 of them), row 0 = top edge, row 1 = bottom edge (not "a row per weather region").
   Only the panels in the view sector are drawn (`FUN_0045a1a0`).
7. **The dome is a square 16x16 vertex grid with side `dome_radius`** (not a disc of radius `dome_radius`),
   `y = height - curvature*(a^2+b^2)`, rotation `angle`, all x `sky_scale`. Vertex (r, c): b = -R/2 + r*step,
   a = +R/2 - c*step.
8. **A sky layer = sky map x cloud texture.** The 16x16 ARGB sky map holds the **vertex colours of the grid** (texel
   (r, c) -> vertex r*16+c), including alpha (layer transparency over the dome); the cloud texture tiles: u = c /
   Xscale, v = r / Yscale (+ wind offset + warp). `flat` = a white texture (pure gradient). Wind: UV shift per tick =
   speed * 0.00025 * (sin, cos)(-dir/360 turn). The number of layers = the number of cloud textures (up to 3).
   Regions A/B are mixed by the 0xDDC curve.

Not decompiled: the exact tick length for wind, `move ratio`, warp, the `multiadditive/attenuate/modulate` modes, the
order "sun <-> a layer that blocks the sun", the second set of horizon colours (+0x100 in the `FUN_00459d30` buffer).
