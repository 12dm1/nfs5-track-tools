# HS2: High Stakes textures -> Porsche Unleashed format

Date: 2026-09-18. Stage 1 of the plan in [HS1](HS1-nfs4-track-migration.md). Tool: `tools/track/nfs5_fsh.py`.
The game was not started: the check is structural, on files.

## Result

**Done.** All 19 High Stakes tracks convert into PU-format archives and pass the check:

```
python tools/track/nfs5_fsh.py --selftest
...
all archives passed
```

| | |
|---|---|
| archives converted | 19 |
| textures | 3621 |
| total output | ~47 MB uncompressed |
| check failures | 0 |

The self test writes to `<work>/build/hs2-textures/<track>.fsh`.

## The source format

Parsing `tr0.qfs` showed three things HS1 did not have:

1. **The palette is an attachment, not part of the entry.** Every 8-bit bitmap is followed directly by a block with
   code `0x2D`, size 256x1 and block length `0x210` (16-byte header + 256 x uint16). The palette entries are
   ARGB1555.
2. **Transparency is a colour key, not an alpha channel.** A palette entry equal to `0x0000` means a transparent
   texel. On Hometown: of 25 161 indices actually used only 46 point at such an entry, exactly one per texture, and
   46 of 186 textures have one: classic cut-out sprites (trees, fences, signs). The other 21 944 zero palette slots
   are just unused tails. Example: texture `'0160'` 128x128 uses index 255 in 7682 of 16384 texels, and its colour
   is `0x0000`.
3. **There are exactly two formats.** Over all 38 archives (19 x `tr0.qfs` + 19 x `trn0.qfs` where present): 4878
   entries `0x7B` and 25 entries `0x7D` (ARGB8888). The latter are effects like `'LRAY'` in Coastal, 64x64, with a
   real alpha channel. NFS4 `0x7D` has the same channel order as PU (B,G,R,A), so they are copied as they are.

## The target format

Checked on `alps.fsh`:

* entries are code `0x7D` ARGB8888, bytes in **B, G, R, A** order (on an opaque texture channel 3 is the constant
  255, not channel 0);
* **a full mip chain down to 1x1 is stored**, levels follow each other without headers of their own. Level 1 matches
  a box filter of level 0 with a mean error of 4.1/255, so these are mips;
* byte `+15` of the entry header = `(levels - 1) << 4`: 128x128 -> `0x70`, 256x256 -> `0x80`, 64x64 -> `0x60`. In
  `.env`, which has no mips, it is 0;
* an entry block is aligned to 16 bytes; the `size` field of the SHPI header is the real file size;
* the engine accepts an uncompressed archive (`alps.crp` and every `.drp` are stored that way), so no RefPack
  compressor is needed, only the decoder that already exists.

## The converter

`tr0.qfs` -> `<track>.fsh`:

1. RefPack decode, read the SHPI directory;
2. per entry: palette into a 256-colour LUT, indices expanded to BGRA (palette entry `0x0000` -> fully transparent
   texel);
3. mip chain by box filter, **RGB averaged weighted by alpha**: otherwise transparent texels would pull black into the
   edges of the cut-out sprites;
4. write the archive by the PU contract (alignment, byte `+15`, size field).

**Tags are assigned by source index** (`'0000'`, `'0001'`, ...), because NFS4 polygons address a texture by its
index in the archive, not by tag, and the native tags are not unique (Hometown has `'0011'` twice). `index_tag()`
returns the 4CC; the converter returns a manifest `(index, tag, w, h)` that the next stage uses to fill the `mt`
materials in the CRP.

Later additions (see the code and HS9): the light bitmaps (`lyte`, `pole`, ...) are copied in from
`industrial.fsh` for tracks with lamps; **non-square textures are padded to square** (2026-10-14: non-square
entries crashed Germany, Gt2 and UK in the game); `--fix-sizes` repairs an installed archive in place (backup in
`<work>/backups`).

## Check

`verify()` re-reads the written archive with our own reader and compares: file size against the `size` field, tag
uniqueness, pixel code, sizes, the mip byte and, most important, that the distance to the next entry equals exactly
the aligned size of the mip chain. `_roundtrip_pixels()` also compares level 0 of every texture byte for byte with
a fresh decode of the source.

What the check does **not** prove: that the game shows these textures. That became clear at HS5 with the geometry.

## Next

HS3: a minimal karT/CRP writer (1 article, 1 material, 1 `pr`).
