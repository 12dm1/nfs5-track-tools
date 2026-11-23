# SKY1: vertical "gap" in the alps / canyon sky (2026-09-22)

**Status: fixed, user-verified on alps and canyon (2026-09-22).**

**Symptom.** A bright vertical slit in the sky backdrop, a few percent of a
panel wide, at panel ("tile") boundaries; alps and canyon, not hometown / uk /
country. Ten draw-time experiments (sampler clamp, mips, aniso, guard band,
seam-triangle dropping) changed nothing — nothing at draw time could, see below.

**Cause (found offline, `tools/track/sky_cutout_check.py`).** The wrapper
classifies every uploaded texture as a binary colour-key cutout when it has
< 5% mid-alpha and >= 8% fully transparent texels (`dx11_texture.cpp`
`ClassifyBinaryCutout`). The horizon panorama atlas `Sky/<Track>.fsh` `horz`
(canyon also `hor1`/`hor2`, industrial `hor0`) is 256x256 with the sky above the
mountains transparent: alps 17% clear / 0.6% mid / 82% solid -> flagged.
`ClearCutoutBorder` then zeroed the alpha of all four outer edges. Rows 0/255 were
transparent anyway, but columns 0/255 are ~80% opaque mountain (210/256 texels).
The horizon ring samples the atlas with CLAMP and its 4-column panels meet at
U = 0 and U = 1, so every column-0 / column-3 junction became a see-through
slit: 2 texels of a 64-texel panel, widened by bilinear + mip level = "a percent
of the picture". Tracks whose `horz` is opaque (99.9% solid) are not flagged.

| track      | horz alpha (clear/mid/solid) | edge opaque T/B/L/R | flagged |
|------------|------------------------------|---------------------|---------|
| Alps       | 17.0 / 0.6 / 82.4 %          | 0/0/210/210         | yes     |
| Canyon     | 20.7 / 0 / 79.3 % (+hor1/2)  | 0/0/174/173         | yes     |
| Monaco     | 33.3 / 0 / 66.7 %            | 0/256/101/247       | yes     |
| Autobahn   | 8.5 / 0 / 91.5 %             | 0/0/231/231         | yes     |
| Industrial | hor0 8.8 / 0 / 91.2 %        | 0/256/238/237       | yes     |
| Country    | 0.1 / 0 / 99.9 %             | 227/256/254/254     | no      |
| Hometown   | 0.1 / 0 / 99.9 %             | 221/256/256/256     | no      |
| Uk         | 0.6 / 0 / 99.4 %             | 203/256/250/250     | no      |

In-game confirmation (dx11z.log, alps): the horizon ring binds `slot=6 256x256
fmt=6 cutout=1 clampST=0/0 U[0.000..1.000] V[0.111..0.990]`; the cloud dome
`slot=5 32x32 cutout=0 clampST=1/1 U[7.4..8.5]` was never touched.

**Fix.** `ClearCutoutBorder` clears an edge only when it is < 1/8 opaque (the
NFS4 stray-texel hairline it was written for, HS11); an edge carrying content
(panorama atlas, tiling barrier) is kept. For alps `horz` the log now reads
`cleared=TB--`.

**Diagnostics kept in the build.**
- `HS11 tex cutout=1 slot=N WxH fmt=F clear=..% mid=..% solid=..% edge opaque
  T/B/L/R=a/b/c/d cleared=TB--` for every cutout classification (budget 160).
- `SKY1 draw slot=N WxH fmt=F cutout=.. clampST=s/t blend=b n=.. U[..] V[..]
  rhw0=.. z0=..` for the first draw of each texture object in the sky pass
  (race, depth test always, no depth write; budget 64).
- `tools/track/sky_cutout_check.py [Track ...]` reproduces the classifier on
  `GameData/Track/Sky/*.fsh` without running the game.

**Lesson.** The artifact scaled with the texture, not the screen — that points
at the upload path, and the upload path can be checked offline from the game
files. Any heuristic that mutates texture content must say so in the log.

## Related, committed together: bar5 stone-barrier stretch

Hard world cutouts (`HardCutoutWanted`, binary-keyed bitmaps only now) force
the sampler to CLAMP and the shader insets UVs into [0,1]; the alps stone
barrier `bar5` tiles its cutout texture 16 times (U = 0..16) and was squashed
into one stretched copy. `FlushVerts` now detects a hard-cutout draw whose UVs
leave [0,1], restores the game's WRAP for that draw
(`State_OverrideCutoutClamp`) and gives every triangle its own UV rect so the
shader's cutout inset is bypassed. User-verified 2026-09-22. The mip builder
(`DownsampleAlphaWeighted`) also keeps the plain RGB average for fully
transparent 2x2 blocks instead of black.
