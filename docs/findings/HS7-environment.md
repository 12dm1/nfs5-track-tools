# HS7: sky, reflections, lighting, sound and cops

Date: 2026-09-18. Stage 6 of the plan in [HS1](HS1-nfs4-track-migration.md). Code: `tools/track/nfs5_environment.py`,
acceptance `tools/track/test_hs7_environment.py`. The game was not started. (The sky and fog were reworked later:
ATM1-ATM10 for the per-slice atmosphere, HZ1 below for the horizon, SKY1/SKY3 for the horizon texture.)

## Result

```
python tools/track/test_hs7_environment.py
Aquatica   sounds  3  cops  7  horizon no   sky 274768 B  env 114880 B  ok
...
Uk         sounds  2  cops  4  horizon yes  sky 274768 B  env 114880 B  ok
ALL PASSED
```

All 19 tracks get `.map`, `.env`, `Sky/<Name>.fsh`, `Sky/<Name>.txt`, `Sky/<Name>.bin`, `<track>_audio.scn` and
`Cops/<track>.cop`. `Sky/<Name>.fsh` comes out exactly as large (274 768 bytes) as the stock `Sky/Alps.fsh`: the
structure matches to the byte.

## Sky: the formats fit better than expected

NFS4 stores the horizon as **eight 64x64 panels** `HDC0...HDC7`: a 512x64 panorama whose top rows are transparent
(colour key, green `0,255,0,a=0`) and the bottom rows an opaque terrain silhouette.

The PU `horz` 256x256 is built **the same way**: two stacked 256x128 strips, each going from a=0 at the top to a=255
at the bottom. Checked on `Alps.fsh`.

So the panorama is resampled into the atlas. The other layers:

| PU layer | size | from |
|---|---|---|
| `st1a` / `st1b` | 16x16 | a generated "fisheye": zenith `hrzSkyTopColor`, edge `hrzSunColor` / `hrzOppositeSunColor` of `tr.ini [strip]` |
| `sc1a` / `sc1b` | 16x16 | `CLDD` (clouds, day) of `sky.qfs` |
| `cm1a` / `cc1a` | 32x32 | same |
| `hzcl` | 16x2 | (white since HZ1, see below) |
| `horz` | 256x256 | the `HDC0...HDC7` panorama |

**Three tracks have no horizon at all.** Aquatica, Atlantic and Gt1 have no `HD*`/`HN*` panel in `sky.qfs`, only
clouds and sun. They get a fully transparent `horz`. The acceptance knows and does not require the alpha ramp.

Full contents of NFS4 `sky.qfs` (43 entries for Hometown):

* `HDC0...7` / `HDW0...7`: horizon, day, clear / rain;
* `HNC0...7` / `HNW0...7`: the same at night;
* `CLDD` / `CLDN` / `CLWD` / `CLWN`: clouds day/night x dry/rain, 64x64;
* `CHRD` / `CHRN` / `CHNW` / `CHRW`: 128x128, cloudy horizon;
* `SUND` / `SUNN` / `SUNW`: sun sprite with alpha;
* `SNNW`: on 13 tracks, probably snow.

Only the clear day set is transferred. The night set (`HN*`) belongs to a night variant of the track (the night
geometry is in `trn.frd`, see HS1 11.1).

`nfs5_fsh.py` also gained an **ARGB1555** (`0x7E`) decoder: 32 of 734 sky archive entries use it, the rest are
ARGB8888.

## `.env`: reflections

Seven 64x64 faces (`1  `, `2  `, `3  `, `BT1 `, `BT2 `, `YT1 `, `YT2 `), one level without mips, as in the
originals. Generated as a vertical gradient from the same `[strip]` colours: the upper faces sky -> horizon, the
lower ones horizon -> ground. Enough for the car body reflection.

## `.map`: light multipliers

One default section plus the header. `ambient` comes from `tr.ini [light] AmbientRed/Green/Blue`: NFS4 gives them in
percent, PU as a multiplier around 1.0, so divided by 100. `tint`, `interior` and `lightdecal` stay neutral: there is
no direct match in NFS4.

Reminder from HS1 4.2: `.map` **holds no road diffuse**, that lives in the CRP materials. Here only the environment
map per slice range and the baked light scale. The acceptance checks that every `textureN` resolves to a tag of the
generated `.env`.

## Sound

Positions transfer exactly (16.16 fixed point -> metres). But **NFS4 `type` is not a PU sound id** and there is no
mapping table between the two sound sets. At HS7 all sources were written with a neutral ambient id and the source
`type` kept in the element label. **Superseded by HS13**, which moves the real NFS4 ambience (scripts and banks).

## Cops

`Cops/<track>.cop` with points every 200 slices, as PU places them (100, 300, 500...): 4-9 points per track.

## Not done: replay cameras

NFS4 `tr.cam` is `uint32 count` and 68-byte records (size confirmed on 8 tracks), but the fields are not decoded. A
misplaced camera is worse than a missing one, so `_cameras.scn` is not written. Open question 2 of HS1.

## What the acceptance does not prove

That the sky looks like High Stakes. Proven: the files have the structure of PU's own tracks, the cross references
resolve (`.map` -> `.env` tags, `Sky/*.txt` -> `Sky/*.fsh` layers), the horizon panorama goes from transparent to
solid, the sound sources lie within the track bounds, the cops within the slice count.

## Next

HS8: registration: `nfs5ext.trk` with the circuit flag, the GetString hook for the names, `FEData/TrackArt/` art,
enabled by `[content] extended_content`.

## HZ1, 2026-10-17: horizon without cropping, band geometry from `[strip]`

Complaint: the horizon was cut at the top, the mountain peaks missing. Cause: `_horz_half` cut everything above the
row where the mean alpha >= 200, 7...25 rows of 64 (Germany 25), which are exactly the peaks. It cut because the
horizon band came from the Coastal.bin template (-170.4 ... +0.3), i.e. entirely below eye level.

Now (`nfs5_environment.horizon_band` / `_horz_atlas`):

* **The band geometry comes from NFS4 `[strip]`**: the panels span `hrzPmxBottom...hrzPmxTop` in band coordinates,
  the bottom of the band is `hrzBottomYOff` below the eye, a cylinder of radius `hrzfRadius`; scale x400/R. The
  panel v range 0.01...0.98 is honoured. Example: Germany -5...+17 deg, Dolphin -11...+7 deg. If the bottom of the
  panels is above -20 units (Country, France), the last row is extended down to -20.
* **A 4x2 atlas of 64x128 cells, HDCi in its own cell**, only stretched vertically. Before, 512 -> 256 in width: the
  panorama went around the circle 4 times, the mountains half as wide. NFS4 panels are 22.5 deg each; slots
  `0..7 + m7..m0` with `hrzMirror=1`, otherwise `0..7 0..7`.
* `hrzHasPMX=0` (Aquatica, Atlantic, Gt1, **Gt2**): no panels, `horz` transparent, band from the template. Gt2 has
  HDC entries in sky.qfs, but NFS4 does not draw them.
* Transparent texels below `hrzGouraudMiddle` get the ground colour (`hrzEarthTop -> BotColor`), like the Gouraud
  strip behind the panels in NFS4 (the sea under the Dolphin Cove islands).
* `hzcl` is white: the old sun/ground pair modulated the panels and darkened the bottom.
* `hrzAngle` is NOT transferred yet (sign/origin not verified).

The `horizon_bottom/top/slots` fields were rewritten in `data/atmosphere/*.ini` (15 tracks; the hand values of
hometown/snowy were tuned for the cropped texture and replaced). Rebuild only the sky:
`convert_track.py --all --sky-only --install <game>`. race_test germany/dolphin/snowy/gt2 with rendering: PASS.
