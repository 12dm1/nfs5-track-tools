# texture_editor: seam and tone editor for AI-regenerated textures

Takes an AI-upscaled / regenerated texture, kills the tiling seam, pulls its
colour back to the original it replaces, and writes the power-of-two PNG the
game wants.

    run_texture_editor.bat                       empty window, drop files in
    run_texture_editor.bat new.png               opens that texture
    run_texture_editor.bat new.png original.png  and the original to match against

The original is optional — without it the colour-match sliders are disabled and
everything else still works.  Drag and drop: first file becomes the new texture,
second the original.

## The chain

    tone  ->  seam  ->  resize  ->  alpha

Tone runs first so the seam blend works on the colours that actually ship; the
resize runs last but pads from the opposite edge, so it cannot re-open the seam
it just closed.

## Seam methods

All the band methods work the same way: the image is stretched by the overlap on
each processed axis, which leaves spare material just past each edge, and the
centred crop has its leading band merged with that material.  After the merge
the first column continues the last one exactly — that is what tiling means.
The centre of the image does not move.

| method | what it does | when |
|---|---|---|
| `Wrap blend` | plain cross-fade of the band | always works, softens the band |
| `Freq split` | low frequencies cross-faded, high frequencies cut along a seam path | **default**, keeps detail |
| `Min-cut` | everything cut along the minimum-error path (Efros-Freeman) | structured textures: brick, stone, tiles |
| `Poisson wrap` | no stretch: re-integrates the gradients with periodic boundaries (FFT) | also flattens the slow tone drift that makes AI textures look blotchy when tiled |

`overlap %` is how much material the merge gets — wider is a safer join but
resamples more of the edge.  `feather` softens the cut path by a few pixels.

The panel prints the measured seam next to the texture's own noise floor:
`X seam 0.0021 (texture floor 0.0016)`.  Green means the join is at or below
what neighbouring columns already differ by, i.e. invisible.

## Tone

| control | what it does |
|---|---|
| `match` (mean/contrast) | matches Lab mean and standard deviation to the original |
| `match` (histogram) | full CDF match, stronger and blunter |
| `tone map` | divides out the new texture's large-scale shading and multiplies in the original's — the important one, because the old textures have the lighting baked in |
| exposure / contrast / saturation / hue / gamma | manual trims on top |

`Auto` = full statistical match plus tone map at 0.7; pushing the shading
transfer all the way tends to eat the new texture's own contrast.

All statistics are taken over the visible pixels only (see below).

## Transparency

Handled throughout, because a mishandled hole poisons both the seam and the tone
match:

* an alpha channel in the PNG is used as is;
* without one, `key colour` builds the mask from a colour (magenta by default,
  auto-enabled when magenta pixels are found) with a tolerance slider;
* transparent pixels get the nearest opaque colour bled into them at load, so
  no blur, blend or resample can drag black or magenta into a visible edge;
* a binary mask stays binary after the resize;
* `alpha` in the Output block picks where the result's alpha comes from — the
  new texture, the original (useful when the AI pass lost it), or none;
* `write key colour instead of alpha` burns the key colour back in and drops the
  alpha channel, for the formats that want it that way.

## Views

Two independent switch rows, so any layout can be compared against the original
at the same time — wiping the original in while tiled 3x3 is the useful case.

* **layout** — `Single`, `Tile 3x3` (the one that matters: a seam is not judged
  on a single copy), `Offset 50%` (rolls the texture by half so the seam lands
  in the middle of the frame).
* **compare** — `Result`, `A/B wipe` (drag the split with the left button),
  `Diff` with a gain slider, which is how tone drift becomes visible.  Disabled
  until an original is loaded.

Alpha is composited over a checkerboard.  Wheel zooms, middle-drag pans.

## Preview resolution

The `Output` block — export size, alpha source and Save — is pinned to the
bottom of the panel, so it stays put however far the rest is scrolled.

The sliders run the chain at a reduced resolution (384 px by default, changeable
in the viewport toolbar) — roughly 70-100 ms per change on a 1024² source.
Saving always runs at full resolution: ~0.7 s for 1024².

## Tests

    python tools/texture_editor --selftest      # headless: chain, seams, alpha, save
    python tools/texture_editor --ui-selftest   # hidden window: panels, GL viewport

## Layout

| file | holds |
|---|---|
| `imageops.py` | load/save, box/gaussian blur, sRGB and Lab, wrap-padded resize, bleed fill |
| `seams.py` | the four seam methods, the minimum-error path, the seam metric |
| `tone.py` | mean/std, histogram and tone-map matching, manual adjustments |
| `pipeline.py` | `Source` (a loaded file and its caches), `State`, `process()` |
| `view.py` | the moderngl viewport: tile, offset, wipe, diff |
| `ui.py` | the imgui panels |
