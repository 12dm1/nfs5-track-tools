# HS3/HS4: karT (CRP) writer and round trip on real tracks

Date: 2026-09-18. Stages 2 and 3 of the plan in [HS1](HS1-nfs4-track-migration.md). Code:
`tools/track/nfs5_crp_writer.py`, acceptance `tools/track/test_crp_writer.py`. The game was not started.

## Result

**The main blocker of the project is gone.** No karT writer existed anywhere, only readers. Now there is one, and it
passes a full round trip:

```
python tools/track/test_crp_writer.py
minimal archive: 592 bytes, ok
alps         1742/1752  art  vert   67217  prim   5374  tr  171    4096112 B  ok
coastal       758/768   art  vert   43385  prim   3209  tr   81    2649712 B  ok
monaco1       500/510   art  vert   28714  prim   2493  tr   73    1814832 B  ok
skidpad       266/276   art  vert   12053  prim    908  tr   60     707776 B  ok
castle       2661/2671  art  vert  117984  prim  12665  tr  161    7602816 B  ok
autobahn     1302/1312  art  vert   69415  prim   4972  tr  100    4226208 B  ok
canyon       1570/1580  art  vert   63871  prim   4658  tr  111    3882416 B  ok
industrial   1805/1815  art  vert   91844  prim   9966  tr  107    5991440 B  ok
ALL PASSED
```

| | |
|---|---|
| articles parsed and written again | 10 604 |
| vertices | 494 483 |
| primitives | 44 245 |
| placement matrices `tr` | 864 |
| differences after re-reading | **0** |

The check compares data, not "the file opened": article name, LOD count, every vertex (4 digits), every UV
(5 digits), the whole `df` array, and per primitive the material, fill mode, both byte index tables and the four
window bases/lengths into the vt/uv/df arrays.

## What had to be decoded further

HS1 described `Base` and `pr` roughly; the writer needed byte precision.

**MiscData uses the same 16-byte format as the parts.** A material record:
`00 00 74 6d | fb | 4c 00 00 | 00 00 00 00 | d0 65 5c 00`: tag `mt` with the info word = slot number, `typeByte`
0xFB, uint24 **LE** length = 76, count, relative offset. The MiscData table has no format of its own.

**typeByte encodes the LOD:** `0x03 + 8*LOD` for `vt`/`uv`/`df`/`pr` and `tr` (0x03, 0x0B, 0x13), `0xEA` for
`Base`/`Anim`/`Anqt`, `0xFA` for `Name`, `0xF3` for `ef`, `0xFB` for `mt`.

**Info word:** for `vt`/`uv`/`df` the low byte is the LOD; for `pr` bits 15..12 are the LOD, bits 11..0 the
primitive number; for `mt` the material slot.

**Parts are ordered by (tag, info word)**: `Base, Name, df..., pr..., uv..., vt...`. The originals do exactly that.

**InfoRow inside `pr`** (from 251 alps articles, 666 rows of each type):

| levelIndex | what | f0 | tail [12:16] |
|---|---|---|---|
| 0 | `vt` | 11 | `LOD 00 00 00` |
| 2 | `uv` | 10 | `LOD 00 01 00` |
| 3 | `df` | 9 | `LOD 00 ff ff` |

Byte 12 of the tail is the LOD. `IndexRow` is always `(1, 'Iv')` and `(1, 'Iu')`. `pr` header: byte 0 = fill mode
(3 = triangle list; 1 and 4 also occur), byte 2 = 1 or 2, `+4` int16 material, `+6` = 0x8000.

**`Base`: the LOD table has a 12-byte step, not 8**: each descriptor is preceded by a 4-byte marker `1`. Block
size: 112 bytes with one LOD, +24 per extra LOD. Fixed in HS1 (4.3) and in the reader.

## Additions and remaining limits

* **`ef`** (glow): the type-2 format is written; for articles with effects the Base LOD gets the stock marker
  `0x101`, otherwise the game skips the glow branch. The CRP also gets the light `mt` records
  `NormMatState`/`FullAmbient` copied from Industrial; the FSH gets the matching bitmap tags. Coastal produces 133
  real effects.
* **`Anim`/`Anqt`**: animation, 19-32 per stock track (HZ2 writes them for converted tracks).
* **10 articles on every stock track** use a variant vertex encoding (extra `vt` with info 0x10/0x20/0x30 and
  typeByte 0x83). The same set on every track: `Docker2`, `DockerCarryBox`, `FishermanPointing`,
  `FishermanPulling`, `HammerMan`, `SawMan`, `Sweeper`, `WaverMan`, `WaverWoman`, `SteelMillWelder`: **animated
  roadside pedestrians** where the extra vertex sets are morph frames, not LODs. Not needed for HS tracks (no such
  objects); `roundtrip_crp.py` copies them verbatim.
* **Materials are written from a template.** The `mt` block is 76 bytes of which only the texture 4CC (`+0x28`) and
  the shader class (`+0x2C`, 7 = water) are reliably understood. The writer takes a template from a stock track and
  patches those fields. HS9 later found the surface type in the low nibble of the `mt+0` word.

## The limit that drives the world cut

`pr` indices are **one byte**, so one primitive addresses at most 256 vertices. That is why the originals cut the
world into small articles. The HS5 converter respects this when grouping NFS4 polygons by material.

## What the check does not prove

That the game loads and shows it. The round trip proves our writer and reader agree and no geometry bit is lost,
not that we read the engine's expectations right. The first real check was HS5, with a whole track.

## Next

HS5: Hometown geometry: road from `poly[4]`, terrain from `poly[5]` + `poly[7..10]` split L/R, LOD1/LOD2 from
`poly[0..3]`, baked light `shading[]` -> `df`.
