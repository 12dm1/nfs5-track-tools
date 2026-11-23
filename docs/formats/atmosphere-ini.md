# `data/atmosphere/<track>.ini` (track atmosphere, editor format)

The single source of truth for a converted track's sky, fog and weather. `convert_track.py` turns it into
`GameData/Track/Sky/<Track>.bin` ([sky-bin.md](sky-bin.md)) and bakes `light_tint` into the CRP vertex colours.
If the file is missing, the converter generates a first approximation from the NFS4 `tr.ini` and **saves it here**
so it can be refined in the editor. Model and I/O: `tools/track/track_atmosphere.py`. Editor:
[../editor.md](../editor.md). `data/atmosphere/presets/` holds the 20 stock PU tracks in the same format
(`python tools/track/track_atmosphere.py --presets`).

Flat INI (`configparser`). Floats are written with `%.9g`, colours as integers 0..255.

```ini
[track]
name = alps
slices = 1800                  ; informative

[sky]
dome_radius = 689.66
dome_curvature = 0.0002
dome_height = 125
angle = 0.8017                 ; turns
scale = 1.5
sun_x = -122.11
sun_y = -26.4
lens_flare = 1
horizon_rows = 2
horizon_rotation = 0
horizon_bottom = -101.31
horizon_top = 346.71           ; band HEIGHT
horizon_slots = 0 1 2 3 4 5 6 7 0 1 2 3 4 5 6 7   ; 16 values, +256 = mirrored panel
horizon_angle = 0.25           ; optional, turns; missing = keep the template value
water_angle = 0
water_dome_radius = 1000
water_dome_curvature = -0.0002
water_dome_height = -75

[sky_colour]
follow_fog = 1                 ; 1 = sky colour comes from fog A, colour_* are ignored
colour_a = 128,128,144
colour_b = 75,75,144
water_colour_a = 128,128,144
water_colour_b = 75,75,144

[key:0]                        ; section number = slice
fog_colour = 139,103,87        ; fog A: sky + horizon
fog_density = 0.1467
fog_colour_b = 139,103,87      ; fog B: world; optional, default = fog A
fog_density_b = 0.1003
rain = 0
snow = 0
storm = 0
region = 0                     ; sky region A<->B blend, 0..1
light_tint = 1,1,1             ; multiplier of the baked df colours; NOT in the .bin

[key:648]
fog_colour = 46,71,121
fog_density = 0.26
snow = 1
region = 0.97
light_tint = 0.62,0.88,1.28
```

Rules:

* **one key list for all curves.** The binary has separate key lists for the weather table and the region curve;
  importing takes the union of their breakpoints and evaluates both curves at every point (lossless for piecewise
  linear curves);
* at most 400 keys, slices ascending, no duplicates;
* a missing field inherits its default: colours and densities are required in the first key, `fog_*_b` default to
  fog A, the rest default to 0, `light_tint` to `1,1,1`;
* densities are the **raw** values; the game cubes the interpolated value. Densities >= 1 are legal (the runtime must
  accept them: HZ7);
* `light_tint` is applied per article: the slice comes from the article name (`RD%04dC` / `CNK%04dL`),
  `df' = clamp(round(df * tint), 0, 255)`. It reaches the game only after reconverting the CRP.

Import from NFS4 (`from_nfs4_ini`): `[fog]` = global colour/density, each `[fog region N]` adds start/centre/end
keys; `[weather] type` 2 = snow, 1 = rain, `denstiy` % between `startSlice` and `endSlice` with a `fade` ramp;
`[lightning] lightningChance`/100 = storm; horizon band from `[strip]`; sun from `[sun] angleTheta/angleRho`;
sky colour from `[strip] hrzSunColor`. `fogHorizon` and tunnel cover are ignored.
