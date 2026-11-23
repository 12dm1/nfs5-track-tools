# SKY3 — importing upscaled horizons (2026-10-19)

Inverse of SKY2 (`tools/track/export_sky_textures.py`). Atmosphere editor,
atmosphere mode, window **Horizon import**.

## Game check

Alps `horz` rebuilt as 1024x1024 ARGB8888 without mips, race via race_test:
`Tex_Alloc slot=5 1024x1024 fmt=7 game_mip=0 levels=11`, race ran, no crash.
The exe allocates at the FSH size even though the wrapper reports a max texture
size of 256 (about+0x14/+0x20); vertices carry normalised UVs
(`dx11_draw.cpp: dv.u = src->u`), so the ring maps the bigger atlas 1:1. The
SKY1 edge clearing in `dx11_texture.cpp` does not depend on size.

## Use

1. Upscale the four `<track>_<tag>_<part>.png` from `<work>/textures/sky/<track>/`
   (names may get suffixes: `alps_horz_0_x4.png` matches part 0).
2. Editor -> Horizon import -> *Choose folder...* (or `...` per part).
   Default folder = the dump folder; the largest matching image wins.
3. *Preview* puts the atlas on the editor's horizon; *Write Sky/<Track>.fsh to game*
   saves it. *Restore original* copies back `<work>/backups/sky_fsh/<Track>.fsh`
   (taken on the first write).

Atlas scale = power of two nearest to part size / manifest size (1..16); off
sizes are resampled (Lanczos). Alpha: the PNG's own, else `alpha/<name>` next to it,
else the current horizon's alpha resized. Tracks that share a horizon
(Monaco2..5 = Monaco1, Canyon hor2 = hor1) are written together unless unticked.
Only the horizon entry is rewritten (ARGB8888, no mips); every other FSH block
is copied byte for byte.

Code: `tools/track_editor/horizon_import.py`, panel in `ui.py`, preview
`SkyRenderer.set_horizon/reload_horizon`. Test:
`python tools/track_editor/test_sky3_horizon_import.py` (34 horizons: 1x
bit-exact, x4 + alpha folder, write keeps other blocks, restore).

Not done: whether hor0..hor2 (lower detail levels) also accept big sizes was
not run in game; the game draws `horz` at top detail on every stock track.
