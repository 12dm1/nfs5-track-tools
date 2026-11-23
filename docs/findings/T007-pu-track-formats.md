# T007: Track Format Analysis — Findings

> 2026-09-13: T019 car research (not part of this repository)
> corrects shared CRP details below: Part data length at +5 is uint24 **LE**;
> vt/nm/uv/tr info uses low 4 bits for LOD, middle 8 for the variant/animation
> index and high 4 for switch. The separate pr packing remains different.
> Data offsets are relative only when flags bit 1 is set. MiscData sf can contain
> a complete embedded SHPI archive. Consult T019 before implementing a parser.

## Summary

NFS5 tracks are composed of 10+ file types per track, stored in `GameData/Track/`. There are 15 tracks total: 10 standard (coastal, forest, foothills, alps, autobahn, castle, farmland, industrial, canyon, skidpad) and 5 Monaco circuit variants. Track definitions live in `FEData/Data/nfs5.trk` (1192 bytes per entry, no global header).

## Track Inventory

| Track | Type | Has _fstart/_bstart | Has _st*.scn (FD missions) | Has .drp | JNC entries |
|-------|------|---------------------|----------------------------|----------|-------------|
| coastal | Point-to-point | Yes | st16 | Yes | 6 |
| forest | Point-to-point | Yes | st33 | No | 6 |
| foothills | Point-to-point | Yes | st25, st27, st36 | Yes | 6 |
| alps | Point-to-point | Yes | st22, st35 | No (alps2.drp exists) | 6 |
| autobahn | Point-to-point | Yes | st14, st112 | No | 35 |
| castle | Point-to-point | Yes | st21, st28 | Yes | 24 |
| farmland | Point-to-point | Yes | st15 | No | 14 |
| industrial | Point-to-point | Yes | st18, st23, st32 | Yes | 26 |
| canyon | Point-to-point | Yes | st12, st19, st111, st210 | Yes | 17 |
| skidpad | Arena (FD only) | No | st1,11,110,13,17,24,26,29,31,34,37 | No | 1 |
| monaco1–5 | Circuit | No | None | No | 4–11 |

## Circuit vs Point-to-Point Detection

Determined by **JNC file topology** and **presence of start SCN files**:

- **Point-to-point**: JNC entry 0 has exactly 1 nonzero float (1 connection = dead end = track start). Has `_fstart.scn` and `_bstart.scn` with start/finish trigger pairs.
- **Circuit (Monaco)**: ALL JNC entries have 2+ nonzero floats (every junction has 2+ connections = no dead ends). No `_fstart.scn`/`_bstart.scn` — start position handled differently.
- **Arena (Skidpad)**: Single JNC entry. No forward/backward keys. Only Factory Driver mission SCNs.

## File Format Reference

### nfs5.trk — Track Definitions

**Location**: `FEData/Data/nfs5.trk` (17,880 bytes)
**Loader**: `FUN_004fae20` — `DAT_00691c94 = filesize / 0x4A8`
**Structure**: 15 entries × 1192 bytes (0x4A8), no global file header.

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Track ID (8, 9, 10, ..., 34 — non-sequential) |
| 4 | 4 | uint32 | FEString ID for display name (300–315) |
| 8 | 20 | char[20] | Directory name (e.g., "coastal") |
| 28 | 20 | char[20] | Backwards key prefix (always "bk") |
| 48 | 20 | char[20] | Environment/sky reference name |
| 68 | 20 | char[20] | Splash screen image name |
| 88 | 20 | char[20] | Small splash screen name |
| 108 | 16 | mixed | Track properties (varies per track) |
| 124 | 24 | uint16[12] | Map section rendering properties |
| 148 | 4 | uint32 | Flags |
| 152 | 278 | — | Reserved / padding (mostly zeros) |
| 430 | 2 | uint16 | AI opponent count (informational, code hardcodes 8 slots) |
| 432 | 608 | — | 8 × 76-byte AI opponent entries |
| 1040 | 152 | — | Remaining data / padding |

**AI Opponent Entry (76 bytes = 0x4C):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 20 | char[20] | Driver name (e.g., "Alberta") |
| 20 | 48 | char[48] | Car name (e.g., "911 Carrera 4") |
| 68 | 4 | uint32 | Reserved (always 0) |
| 72 | 4 | uint32 | Performance value (time-related, default fill = 359999) |

Empty AI slots have performance value = 0, replaced by loader with 359999 (~3600 sec = "never finishes").

**Track IDs → Directories:**

| ID | Directory | ID | Directory |
|----|-----------|----|-----------| 
| 8 | coastal | 18 | monaco1 |
| 9 | Forest | 25 | monaco2 |
| 10 | foothills | 26 | monaco3 |
| 11 | alps | 29 | monaco4 |
| 12 | AutoBahn | 30 | monaco5 |
| 13 | castle | 32 | skidpad |
| 14 | farmland | 34 | canyon |
| 16 | industrial | | |

Note: IDs 15, 17, 19–24, 27–28, 31, 33 are not used.

### .map — Track Surface Sections

**Format**: Text INI
**Loader**: `FUN_004409d0` (1685 bytes, also loads .env)
**Load pattern**: `sprintf("%s%s.map", track_path, track_name)`

Structure:
```ini
[header]
count=39                                    # Number of sections
tint=1.0,0.57,0.57,0.57                   # ARGB tint color
ambient=1.0,1.23,1.23,1.23                # Ambient light ARGB
interior=1.0,0.98,0.98,0.98               # Interior light ARGB
lightdecal=1.0,1.17,1.17,1.17             # Decal light ARGB

[section0]
startslice=0                               # Track slice range
endslice=0
texture1=542722899                         # Texture ID (4CC as uint32)
clamp1=0                                   # Texture clamping
clamp2=1
tint1=1.0,1.0,1.0,1.0                    # Per-section tint
tint2=1.0,1.0,1.0,1.0
texture1.open=538976305                    # Road variant (open road)
texture1.covered=538976305                 # Road variant (covered/tunnel)
texture2.open=538976305
texture2.covered=538976305
texture3.open=538976305
texture3.covered=538976305
texturesize=6.0                            # Texture repeat scale
aspect=1.0                                 # Texture aspect ratio
```

Texture IDs are uint32 representations of 4-character codes from the FSH texture archive.

### .edg — Edge / Collision Data

**Format**: Binary
**Loader**: `FUN_00488090` → `FUN_00487e80` (parser)
**Load pattern**: `sprintf("%s.edg", track_name)`
**Structure**: `[uint32 header] + N × 28-byte entries`, where `N = (filesize - 4) / 28`

**Entry (28 bytes = 0x1C):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Flags (bitfield, see below) |
| 4 | 4 | float | X1 — start point X |
| 8 | 4 | float | Y1 — start point Y (up) |
| 12 | 4 | float | Z1 — start point Z |
| 16 | 4 | float | X2 — end point X |
| 20 | 4 | float | Y2 — end point Y (up) |
| 24 | 4 | float | Z2 — end point Z |

Entries form connected chains: entry N's end point equals entry N+1's start point.

**Edge flags:**

| Flag | Meaning (inferred) | Alps count | Notes |
|------|-------------------|------------|-------|
| 0x00 | Normal road edge (drivable boundary) | 4583 (60%) | Most common |
| 0x10 | Hard wall / barrier | 776 | Solid collision |
| 0x20 | Soft boundary | varies | |
| 0x30 | Hard wall + soft boundary | 2311 (30%) | Second most common |
| 0x40 | Additional property flag | — | Combined with others |
| 0x50 | 0x10 + 0x40 | varies | |
| 0x60 | 0x20 + 0x40 | 23 | Rare |

First entry has special high-value flags (e.g., 0x012EDF00) — likely encodes track metadata, not wall type.

**Invisible walls**: Edges with flags 0x10, 0x30, 0x50, 0x60 act as collision barriers. Flag 0x00 = drivable road edge only.

**Header value**: Not a simple count. Varies per track (29087 for alps, 56699 for castle). Possibly encodes total track length or boundary hash.

### .jnc — Junction / Lane Probabilities

**Format**: Binary, 36 bytes per entry
**Load pattern**: `sprintf("C%s.jnc", track_name)` (note: "C" prefix!)
**Structure**: Array of `filesize / 36` entries.

**Entry (36 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Junction index (sequential from 0) |
| 4 | 32 | float[8] | Lane transition probabilities |

The float array defines AI lane preference at each junction:
- `[1.0, 0, 0, ...]` = single path (dead end / no choice)
- `[1.0, 1.0, 0, ...]` = two equal paths (circuit segment)
- `[0.85, 0.15, 1.0, ...]` = split with 85% preference for lane 0
- `[0.1, 0.3, 0.6, 1.0, ...]` = three-way split

**Circuit detection**: If JNC entry 0 has >1 nonzero float, the track is a circuit.

Junction counts per track:
- Simple P2P (alps, coastal, foothills, forest): 6 junctions
- Complex P2P (autobahn): 35 junctions (many lane splits)
- Monaco circuits: 4–11 junctions
- Skidpad: 1 junction (single area)

### .key — AI Route Keyframes

**Format**: Binary, fixed 3208 bytes (0xC88)
**Loader**: `FUN_0044fb40`
**Load pattern**: `sprintf("%s%s_forward.key", track_path, track_name)` (or `_backwards.key`)
**Direction**: Selected by `DAT_00657430` (0 = forward, 1 = backward)

**Structure:**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Version (must be 1) |
| 4 | 4 | uint32 | Valid keyframe count (e.g., 40 for alps) |
| 8 | 3200 | — | 400 × 8-byte keyframe slots |

**Keyframe slot (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Slice index (0xFFFFFFFF = unused) |
| 4 | 4 | float | Speed value (default 400.0 for unused slots) |

Active keyframes define AI target speed at specific track slices. The valid_count field indicates how many slots from the beginning contain real data.

Speed values range from ~200 to ~600 in game units. Default fill for empty slots: index=0xFFFFFFFF, speed=400.0.

### .env — Environment Reflection Maps

**Format**: EA SHPI/FSH archive (binary)
**Magic**: `SHPI` (0x53485049)
**Loader**: Part of `FUN_004409d0`
**Load pattern**: `sprintf("%s%s.env", track_path, track_name)`

Contains environment cubemap textures for car reflections. Typical entries:
- `1`, `2`, `3` — Main environment map faces (64×64 pixels each)
- `BT1`, `BT2` — Bottom textures
- `YT1`, `YT2` — Vertical textures

Record type 0x7D = GIMX bitmap format with width/height fields.

### .fsh — Track Textures (EA SHPI Format)

**Format**: EA SHPI/FSH bitmap container (binary)
**Magic**: `SHPI` (0x53485049)
**Compression**: May be wrapped in QFS/RefPack (`.qfs` extension) — decompress first
**Load pattern**: `sprintf("%s%s.fsh", track_path, track_name)`

Main texture archive for the track surface. Large files (3–9 MB). Contains road surface, guardrails, vegetation, buildings textures referenced by 4CC tags in .map sections and CRP material entries.

#### FSH Header (16 bytes)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | char[4] | Magic: `SHPI` |
| 4 | 4 | int32 | Total file size |
| 8 | 4 | int32 | Number of bitmap entries |
| 12 | 4 | char[4] | Directory ID (e.g., "G264", "G354") |

#### FSH Directory (8 bytes per entry, immediately after header)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | char[4] | Tag — 4-char bitmap name (matches CRP material texture refs) |
| 4 | 4 | int32 | Offset from file start to bitmap data |

#### Bitmap Entry Header (16 bytes at each directory offset)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | byte | Pixel format code (see table below) |
| 1 | 3 | uint24 LE | Block size (this entry's data block) |
| 4 | 2 | uint16 | Width in pixels |
| 6 | 2 | uint16 | Height in pixels |
| 8 | 4 | — | Misc/rotation center |
| 12 | 4 | — | Position |

Pixel data follows at offset +16.

#### Pixel Format Codes

| Code | Format | BPP | Notes |
|------|--------|-----|-------|
| 0x60 | DXT1 | 4 | S3TC block compression |
| 0x61 | DXT3 | 8 | S3TC with explicit alpha |
| 0x6D | ARGB4444 | 16 | 4 bits per channel |
| 0x78 | RGB565 | 16 | No alpha, 6-bit green |
| 0x7B | Palettized8 | 8 | 256-entry BGRA palette (1024 bytes) after pixel data |
| 0x7D | ARGB8888 | 32 | Full-quality, stored as BGRA in memory |
| 0x7E | ARGB1555 | 16 | 1-bit alpha, 5-bit RGB |
| 0x7F | RGB888 | 24 | No alpha, stored BGR |

#### CRP ↔ FSH Texture Mapping

Each CRP MiscData `mt` entry contains a 4-char FSH tag at offset +0x28 (40 bytes into the material data block). This tag matches a directory entry in the track's .fsh file, linking CRP materials to their textures.

### .crp / .drp — 3D Scenery Archives (karT format)

**Format**: EA karT archive — three-zone structure: Article directory → MiscData (materials) → Part tables + geometry data
**Magic**: `karT` (4 bytes, "Track" reversed in LE); cars use `" raC"` ("Car ")
**Compression**: Most .crp files are **RefPack compressed** (`0x10FB` magic); only `alps.crp` is uncompressed. All .drp files are uncompressed.
**Load pattern**: `sprintf("%s.crp", track_name)` at 0x0048773F, 0x004FB886
**Main loader**: `FUN_004876e0` (opens CRP, initializes track objects)
**Processors**: `FUN_00471850` (camera anims), `FUN_0044a770` (materials + geometry)
**Third-party parser**: NFS5WorldExporter by TheBorn (C#/.NET 8, OBJ/FBX export, RefPack decompression)

#### RefPack Compression

| Byte | Size | Field |
|------|------|-------|
| 0 | 1 | Magic: `0x10` |
| 1 | 1 | Magic: `0xFB` |
| 2 | 3 | Decompressed size (uint24 big-endian) |
| 5 | ... | LZ77-variant compressed data |

Command encoding (4 tiers by leading byte):
- `0x00–0x7F` (2 bytes): plain = b0 & 3, offset = ((b0 & 0x60) << 3) + b1 + 1, copy = ((b0 & 0x1C) >> 2) + 3
- `0x80–0xBF` (3 bytes): plain = (b1 >> 6) & 3, copy = (b0 & 0x3F) + 4, offset = ((b1 & 0x3F) << 8) + b2 + 1
- `0xC0–0xDF` (4 bytes): plain = b0 & 3, offset = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1, copy = ((b0 & 0x0C) << 6) + b3 + 5
- `0xE0–0xFB` (1 byte): plain-only = ((b0 & 0x1F) << 2) + 4
- `0xFC–0xFF` (1 byte): end marker, trailing = b0 & 3

All tracks except `alps.crp` are RefPack compressed. Typical compression ratio ~2.5–3×.

#### karT Header (16 bytes)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | char[4] | Magic: `karT` (bytes "k","a","r","T" → uint32 LE 0x5472616B) |
| 4 | 4 | uint32 | HeaderInfo: bits[4:0] = flags (0x1A typical), bits[31:5] = article count |
| 8 | 4 | int32 | NumMiscData — number of MiscData entries (materials + source file refs) |
| 12 | 4 | int32 | ArticleTableOffset (multiplied by 16 for byte offset) |

Article count = `HeaderInfo >> 5`. Flags = `HeaderInfo & 0x1F`.

#### File Layout (Three Zones)

```
Offset 0x00:    karT Header (16 bytes)
Offset 0x10:    Article Table — NumArticles × 16-byte Article entries
                MiscData Table — NumMiscData × 16-byte entries (materials, source file refs)
                Part Tables + Geometry Data — pointed to by Articles and MiscData entries
```

#### Article Entry (16 bytes) — Top-level object containers

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Identifier: always `0x41727469` = "Arti" (LE bytes: `i`,`t`,`r`,`A`) |
| 4 | 4 | uint32 | HeaderInfo: bits[4:0] = flags, bits[31:5] = info |
| 8 | 4 | int32 | PartTableLength — number of Part entries in this article's Part Table |
| 12 | 4 | int32 | Offset to Part Table — relative from this Article's position, in units of 16 bytes |

Each Article points to a Part Table — a flat array of Parts that describe the article's geometry, transforms, names, animations, etc.

#### MiscData Entry (16 bytes) — Materials and texture source file refs

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 2 | uint16 | Index (material slot number) |
| 2 | 1 | byte | Tag char 1 (e.g., `'t'` for "mt") |
| 3 | 1 | byte | Tag char 2 (e.g., `'m'` for "mt") |
| 4 | 8 | — | Flags / metadata |
| 12 | 4 | int32 | Data offset (relative from this entry's position) |

Known MiscData types:
- `mt` (material): data block contains description (16 chars at +0x10), texture FSH tag (4 chars at +0x28), full block ≥0x138 bytes
- `sf` (source file): FSH texture file reference

#### Part Entry (16 bytes) — Geometry components within an Article

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | RawIdentifier — either 4-byte reversed ASCII or 2-byte tag + info word |
| 4 | 1 | byte | TypeByte — type/flags |
| 5 | 3 | uint24 BE | DataLength — size of pointed data |
| 8 | 4 | int32 | Count — element count |
| 12 | 4 | int32 | DataOffset — relative from this Part's position |

**Identifier decoding**:
- **4-byte tags**: bytes[0:3] reversed ASCII → `esaB`="Base", `emaN`="Name", `minA`="Anim", `tqnA`="Anqt"
- **2-byte tags**: bytes[2:3] form the tag (LE), bytes[0:1] = info word with packed fields:
  - Triangle (`pr`): bits[15:12] = LOD level, bits[11:0] = part index
  - Others (`vt`/`nm`/`uv`/`df`/`ef`/`tr`/`$n`): bits[15:8] = LOD level, bits[7:4] = animation index, bits[3:0] = damage switch

#### Part Types

| Tag | CrpPartType | Stride | Purpose |
|-----|-------------|--------|---------|
| `Base` | Base | variable | Object base info / LOD reference |
| `Name` | Name | — | Null-terminated name string |
| `vt` | Vertex | 16 | Vertex positions: float4 (x, y, z, w=1.0) |
| `nm` | Normal | 16 | Vertex normals: float4 (nx, ny, nz, nw) |
| `uv` | UV | 8 | Texture coordinates: float2 (u, v) |
| `pr` | Triangle | variable | Primitive / triangle data with material refs (see below) |
| `df` | Define | 4 | **Per-face-corner baked lighting** — D3DCOLOR (BGRA), 4 bytes each |
| `ef` | Effect | variable | Lighting / glow effects |
| `tr` | Transform | 64 | 4×4 transformation matrix (float[16], D3D row-major) |
| `$n` | Culling | variable | Backface culling normals + thresholds |
| `mt` | Material | — | Material reference (index into MiscData) |
| `sf` | SourceFile | — | FSH texture file reference (in MiscData) |
| `Anim` | Anim | 16 | Animation descriptor (frame count in Count field) |
| `Anqt` | Anqt | 52 | Quaternion keyframe stream (see Animation section) |

#### Triangle Data (pr) Internal Structure

The `pr` part's data block contains a complex sub-structure:

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0x00 | 4 | uint32 | InfoFlags — byte[0] = D3DFILLMODE (1=strip, 3=tri list, 4=quad list) |
| 0x04 | 2 | int16 | MaterialIndex — index into material table |
| 0x28 | 4 | int32 | NumInfoRows — typically 3 or 4 |
| 0x2C | 4 | int32 | NumIndexRows — typically 0 or 2 |
| 0x30 | N×16 | InfoRow[] | Data source references |
| ... | M×8 | IndexRow[] | Index array references |
| ... | ... | byte[] | Actual index data (byte-sized when NumIndexRows > 0) |

**InfoRow (16 bytes)** — references vertex/UV/color data sources:
- Field at +4 (int32): data byte offset into the corresponding vt/uv/df buffer
- Field at +10 (uint16): levelIndex identifies the data type:
  - `0` = vertex data (dataOffset / 16 = vertex base index)
  - `2` = UV data (dataOffset / 8 = UV base index)
  - `3` = df face-corner colors (dataOffset / 4 = color base index)

**IndexRow (8 bytes)**: tag "Iv" = vertex indices, "Iu" = UV indices

When NumIndexRows > 0: indices are byte-sized (uint8), offset by the base from InfoRows.
When NumIndexRows == 0: sequential indices (used for dynamic/animated objects).

#### Baked Lighting (df — Per-Face-Corner Colors)

**This is the game's baked lighting system.** The `df` tag stores per-face-corner D3DCOLOR values (BGRA in memory, 4 bytes each). These are NOT per-vertex — the same vertex can have different colors on different faces (bright near a light source on one face, dark on another).

Memory layout: `byte[0]=B, byte[1]=G, byte[2]=R, byte[3]=A` (D3DCOLOR is ARGB as uint32, stored BGRA in little-endian).

The DamageSwitch field in the df Part entry acts as LOD selector: 0 = colors for LOD 0 geometry, 1 = LOD 1, etc.

The game reads the df base index from the pr part's InfoRow with levelIndex=3 (at `sub_4750B0`/`sub_5A0BC0`). Each pr part references its own region of the df buffer independently — accumulating a running index across pr parts is incorrect for ~62% of terrain geometry.

#### Transform Matrix Convention

The `tr` part stores 16 floats in D3D row-major / row-vector convention:
```
M[0]  M[1]  M[2]  M[3]      ← row 0 (basis X)
M[4]  M[5]  M[6]  M[7]      ← row 1 (basis Y)
M[8]  M[9]  M[10] M[11]     ← row 2 (basis Z)
M[12] M[13] M[14] M[15]     ← row 3 (translation)
```
Transformed position: `x' = x*M[0] + y*M[4] + z*M[8] + M[12]`, etc.

Static road chunks (CNK*) are already in world space — no `tr` part.
Dynamic objects (signs, cones, barriers) store local-space geometry + world-placement `tr` matrix.

#### Animation Keyframes (Anqt — 52 bytes per key)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 16 | float4 | Quaternion (x, y, z, w) — unit magnitude |
| 16 | 12 | float3 | Position (x, y, z) — world space |
| 28 | 12 | float3 | Scale (x, y, z) — always (1, 1, 1) in observed data |
| 40 | 12 | — | Flags/markers (not decoded) |

Playback rate: 30 FPS (matches observed helicopter rotor rotation). The `Anim` part's Count field = total keyframe count. NFS5 loops all world animation clips.

Known animated articles: RESCUEHELI, RESCUEHELIBLADES, RESCUEHELIBACKBLADE.

#### Coordinate System

- D3D left-handed, Y up
- To convert to OpenGL/Blender right-handed: negate X on vertices and normals, flip V on UVs (1.0 - V)
- Face winding: CW in D3D (front face) → CCW in OpenGL after X-flip

#### Object Grouping

Objects in the data section follow a repeating pattern per Article:
```
Name → vt (vertices) → nm (normals) → uv (UVs) → df (face colors) → pr (triangles) × N → tr (transform) → Base
```
Each Article defines one scenery object (building, tree, barrier, etc.). Alps has ~163 objects with geometry + LOD 0 data.

#### Track CRP Inventory

| Track | CRP size | Compressed | Decomp size | DRP file | DRP size |
|-------|----------|------------|-------------|----------|----------|
| alps | 6,168,576 | No | — | alps2.drp | 6,156,288 |
| autobahn | 2,300,714 | Yes | 6,459,392 | — | — |
| canyon | 2,246,668 | Yes | 5,914,624 | canyon.drp | 5,898,240 |
| castle | 3,520,811 | Yes | 9,822,208 | castle.drp | 9,809,920 |
| coastal | 1,735,195 | Yes | 4,698,112 | coastal.drp | 4,685,824 |
| farmland | 2,221,894 | Yes | 6,017,024 | — | — |
| foothills | 2,425,414 | Yes | 6,946,816 | foothills.drp | 6,934,528 |
| forest | 2,384,358 | Yes | 6,324,224 | — | — |
| industrial | 3,022,388 | Yes | 8,241,152 | industrial.drp | 8,232,960 |
| monaco1 | 1,345,767 | Yes | 3,768,320 | — | — |
| monaco2 | 1,386,285 | Yes | 3,854,336 | — | — |
| monaco3 | 1,524,147 | Yes | 4,112,384 | — | — |
| monaco4 | 1,699,847 | Yes | 4,571,136 | — | — |
| monaco5 | 1,364,047 | Yes | 3,801,088 | — | — |
| skidpad | 730,076 | Yes | 2,596,864 | — | — |

DRP (Detail Render Pack) files use the identical karT format but are never compressed. 6 tracks have DRP files. Not directly referenced by string in EXE — loaded via generic resource system.

Known GEOM_ELEMENT FourCC tags (from SCN + NFS5WorldExporter cross-reference):
- `CONE` (0x454E4F43) — Traffic cone → DRP article "default cone"
- `ENDW` (0x57444E45) — End wall → DRP article "EndWall"
- `FNSH` (0x48534E46) — Finish line → DRP article "FinishLine"
- `ARW1` (0x31575241) — Direction arrow → DRP article "arrow"
- Additional: various per-track tags for mission-specific objects

### .dtx — Detail Texture Config

**Format**: Binary, 11 bytes (fixed for all tracks)
**Loader**: `FUN_0044e010` (615 bytes)
**Load pattern**: `sprintf("%s%s.dtx", track_path, track_name)`

Tiny configuration file controlling detail texture rendering. Same 11-byte content across all tracks.

### .scn — Scene Files (Text Format)

All SCN files share the same text-based element format. Loaded by matching wildcard `*.scn` or specific patterns.

#### Element Types

**TRIGGER_ELEMENT** — Trigger zones (start/finish lines, waypoints, camera triggers):
```
TRIGGER_ELEMENT <version=5>
<pos_x> <pos_y> <pos_z>
<rot_matrix 3x3, tab-separated columns>
<type> <subtype> <param1> <width> <height> <param4> <range> <wp_id> <p1> <p2> <wp_id2> <p3> <geom_link> <flag1> <flag2> <p4> <p5>
<left_x> <left_y> <left_z>    <right_x> <right_y> <right_z>
<dir_flag> <dx> <dy> <dz>     <dir_flag> <dx> <dy> <dz>
[optional label]
```

Trigger type (first number on param line):
| Type | Meaning | Context |
|------|---------|---------|
| 0 | Waypoint | Factory Driver mission checkpoints |
| 1 | Finish / timing checkpoint | Start/end of timed sections |
| 2 | Start line | Beginning of race/mission |
| 6 | Camera activation zone | In _cameras.scn |

Key parameters:
- `wp_id` (8th): Sequential waypoint index (0, 1, 2, ...)
- `wp_id2` (11th): Usually matches wp_id
- `geom_link` (13th): Index to associated GEOM_ELEMENT (-1 = none)
- `width` (4th): Trigger zone width
- `flag2` (15th): 0 = non-ordered, 1 = must hit in sequence, 65535 = special

**GEOM_ELEMENT** — Positioned geometry (cones, arrows, markers):
```
GEOM_ELEMENT <version=4|5>
<pos_x> <pos_y> <pos_z>
<rot_matrix 3x3, tab-separated 9 floats row-major>
<type> <fourcc_uint32> <p1> <p2> <p3> <visible> <collidable> <scale_x%> <scale_y%> <scale_z%>
[label]
```

- `fourcc_uint32`: **must be parsed as integer** (exceeds float32 24-bit mantissa precision, so float parse mangles bits)
- `rot_matrix`: 3×3 rotation matrix, row-major (R00 R01 R02 R10 R11 R12 R20 R21 R22)
- `scale_x/y/z`: percentage (100 = 1×), divide by 100 for actual scale
- `visible`, `collidable`: boolean flags

Known FourCC → DRP article name mapping:
| FourCC (LE text) | uint32 | DRP Article Name |
|------------------|--------|-----------------|
| CONE | `0x454E4F43` | "default cone" |
| ENDW | `0x57444E45` | "EndWall" |
| FNSH | `0x48534E46` | "FinishLine" |
| ARW1 | `0x31575241` | "arrow" |

The placement matrix is built as: rotation rows × per-axis scale in rows 0–2, position in row 3 (D3D row-vector convention).

**CAMERA_ELEMENT** — Camera positions:
```
CAMERA_ELEMENT <version=4>
<pos_x> <pos_y> <pos_z>
<rot_matrix 3x3>
<flag> <param> <active>
<cam_type> <flag2> <duration> <flag3> <flag4> <speed> <flag5> <fov> <blend>
<target_x> <target_y> <target_z>
[label]
```

**AUDIO_ELEMENT** — Positioned audio sources:
```
AUDIO_ELEMENT <version=4>
<pos_x> <pos_y> <pos_z>
<audio_type> <sound_id> <range> <fade_range> <param1> <param2> <param3> <param4> <param5> <param6> <volume> <param7>
[label]
```

#### SCN File Types

| Pattern | Purpose | Content |
|---------|---------|---------|
| `_fstart.scn` | Forward start grid | 2× TRIGGER (start + finish) + 2× GEOM (grid markers) |
| `_bstart.scn` | Backward start grid | Same, positions swapped |
| `_cameras.scn` | Replay/spectator cameras | CAMERA_ELEMENT + TRIGGER type=6 pairs |
| `_audio.scn` / `_Audio.scn` | Ambient audio | AUDIO_ELEMENTs for wind, birds, tunnel echoes |
| `_ST%d.scn` | Factory Driver missions | Waypoints, cones, arrows, start/finish |

#### Factory Driver Missions (_ST%d.scn)

Loaded by `FUN_0046d330` via `sprintf("_ST%d.scn", mission_number)`.

Mission SCN files contain:
1. **Start trigger** (type=2): Where the player starts, labeled "Start"
2. **End trigger** (type=1): Finish zone, labeled "End"
3. **Waypoint triggers** (type=0): Checkpoints the player must pass through, labeled "Waypoint Tigger" [sic — typo in original EA data]
4. **Cone GEOM_ELEMENTs** (model 0x454E5043): Physical cones the player must hit or avoid
5. **Arrow GEOM_ELEMENTs** (models 0x315F4541–0x335F4541): Direction indicators

Waypoint triggers have parameters controlling mission behavior:
- `param1` (3rd number): Hit threshold distance
- `flag2` (15th number): 1 = must hit in order, 0 = any order
- `geom_link`: Links waypoint to specific cone geometry

Skidpad has 11 mission variants (the most of any track), used for skills tests.

### Cops/ .cop — Police Spawn Points

**Format**: Binary
**Structure**: `[uint32 count] + count × 20-byte entries`

**Entry (20 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | uint32 | Enabled flag (1 = active, 0xFFFFFFFF = disabled) |
| 4 | 4 | uint32 | Slice index (track position: 100, 300, 500, etc.) |
| 8 | 4 | uint32 | Reserved (always 0) |
| 12 | 4 | uint32 | Cop type/variant |
| 16 | 4 | uint32 | Additional flags |

Monaco tracks have **no** cop files — no police in circuit races.

### Sky/ — Skybox Data

| File | Format | Content |
|------|--------|---------|
| `*.txt` | Text config | Quality levels (HIGH/MEDIUM/LOW), sky layer type, texture refs, wind speed/direction, warp |
| `*.fsh` | EA SHPI | Sky textures (base gradient + cloud layers) |
| `*.bin` | Binary (29148 bytes each) | Sky dome mesh vertex data (pre-subdivided hemisphere) |
| Subdirs | BMP files | Individual sky texture mipmaps |

Sky .txt structure defines weather regions (A, B) with texture layers, scale factors, movement ratios.

#### Sky Dome Architecture (from nfs5js RE)

**Builder function**: `ConstructSkyLayerScene`
**UV/geometry builder**: `InitSkyLayerSceneGradientTable` (builds UV coords and mesh geometry — NOT light colors despite the name)

**Three layers composited**:
1. **Base gradient**: 16×16 pixel texture (e.g., `st1a` for skidpad), hemispherical "fisheye cap" projection — zenith = texture center, horizon = texture edge. Original likely used dithering for smoother gradients.
2. **Cloud layer 1** (`sc1a`): Tiles over gradient. Scale (e.g., 3×3), wind speed (0-10), bearing (direction), moveRatio (0-400, 0=enabled, higher=slower).
3. **Cloud layer 2** (`sc2a`): Same parameters, independent movement. Alpha composited.

**Dome radius**: 900 world units. Must re-center on camera position every frame (otherwise camera outside dome on tracks that start far from origin).

**Rendering**: `THREE.BackSide` equivalent — dome renders inward. Sphere built with backface rendering so visible from inside.

**Note**: Three examined gradient-stop tables in .bin files were either identical across all tracks or varied in unrelated ways — dynamic ambient/directional light colors could NOT be decoded from sky data.

### Track/ Data/ — Physics and Camera Data

| File | Format | Content |
|------|--------|---------|
| `camera.viv` | VIV archive | Camera preset data |
| `TR%02d.RHO` | Binary (2048 bytes) | Forward physics/road parameters per track |
| `TR%02dR.RHO` | Binary (2048 bytes) | Reverse physics/road parameters |
| `tr%02da.viv` | VIV archive | Additional track data |

### animdefs.txt — Track Object Definitions

Text config defining physics and collision properties for all track-side objects:

```
SECTION:ANIM
{
    tag=SIGN;              // 4-char object tag
    collideType=kSmackable;  // kCylinder | kBox | kSmackable
    audioType=kAudMetalSign; // Impact sound
    mass=80;               // Physics mass
}
```

Collision types: `kCylinder`, `kBox`, `kSmackable` (breakable)
Audio types: `kAudSign`, `kAudMetalSign`, `kAudGlass`, `kAudWoodBox`, `kAudPylon`, `kAudTree`, `kAudBush`, `kAudCarBody`, `kAudWood`, `kAudSnow`, `kAudIce`, `kAudEarth`, `kAudRock`

Known object tags: ENDW (end wall), FNSH (finish), SIGN, GAS, TABL, SEAT, REST, DIST, TEMP, BBOX (exploding crate), and more.

## File Loading — EXE Functions

| Format | Load Pattern | Function | Size |
|--------|-------------|----------|------|
| .trk | `nfs5.trk` | `FUN_004fae20` | 397 |
| .map | `%s%s.map` | `FUN_004409d0` | 1685 |
| .env | `%s%s.env` | `FUN_004409d0` | (same) |
| .edg | `%s.edg` | `FUN_00488090` → `FUN_00487e80` | 929 + 524 |
| .key | `%s%s_forward.key` | `FUN_0044fb40` | 230 |
| .dtx | `%s%s.dtx` | `FUN_0044e010` | 615 |
| .crp | `%s.crp` / `C%s%s.crp` | 0x441A6E, 0x48773F, 0x4FB886 | — |
| .scn (mission) | `_ST%d.scn` | `FUN_0046d330` | 717 |
| .scn (start) | `_FStart.scn` / `_BStart.scn` | (via *.scn scan) | — |
| .jnc | `C%s.jnc` | (via generic loader) | — |

## Adding New Tracks

### Required Files

A minimal new track needs:
1. `trackname.map` — surface sections (text INI, can start with 1 section)
2. `trackname.fsh` — textures (EA SHPI format)
3. `trackname.edg` — edge collision data (28-byte chain entries)
4. `trackname.jnc` — junction topology (36-byte entries)
5. `trackname.env` — environment maps (SHPI with 7 cubemap entries)
6. `trackname.crp` — 3D scenery
7. `trackname.dtx` — detail texture config (copy 11-byte file from existing track)
8. `trackname_forward.key` — AI route (3208 bytes, fill with default values if no AI needed)
9. `trackname_backwards.key` — reverse AI route
10. `trackname_fstart.scn` — forward start grid
11. `trackname_bstart.scn` — backward start grid
12. `trackname_cameras.scn` — replay cameras
13. `trackname_audio.scn` — ambient audio
14. `Sky/TrackName.*` — skybox data (.bin, .fsh, .txt)
15. `Cops/trackname.cop` — police spawn points
16. **nfs5.trk entry** — append 1192-byte record to `FEData/Data/nfs5.trk`

### nfs5.trk Entry Requirements

1. Choose an unused Track ID (not 8–14, 16, 18, 25–26, 29–30, 32, 34)
2. Reference an existing FEString ID (300–315) or add new string to FEStrings.csv
3. Directory name must match track file prefix
4. Environment name can reference an existing Sky/ subdirectory
5. Fill 8 AI opponent slots (76 bytes each) — empty slots get performance=0 → replaced with 359999

### Circuit Track Specifics

For a circuit track (like Monaco):
- JNC entry 0 must have 2+ nonzero floats (no dead-end start)
- No _fstart/_bstart SCN files needed (start position embedded differently)
- Forward and backward key files still required
- No cop files (no police on circuits)

### EXE Patching (for track count)

The track loader (`FUN_004fae20`) auto-detects count via `filesize / 0x4A8`. Simply appending entries to nfs5.trk works. However:
- Track selection UI may need patching for >15 tracks
- FEString IDs for new track names need to be added to `FEData/Locale/FEStrings.csv`
- Splash screen images need entries in frontend data

## Key Global Addresses

| Address | Purpose |
|---------|---------|
| `DAT_00691c90` | Loaded track data buffer (nfs5.trk contents) |
| `DAT_00691c94` | Track count (`filesize / 0x4A8`) |
| `DAT_00691ce8` | Track data pointer (same as DAT_00691c90) |
| `DAT_0065b2b8` | Current track directory path |
| `DAT_0065b360` | Track data file base path |
| `DAT_00657430` | Direction flag (0 = forward, 1 = backward) |
| `DAT_00628130` | Track render state |
| `DAT_00657494` | Mission active flag |
| `DAT_00628168` | Mission data pointer |
| `DAT_006573ec` | Mission element count |

## Key Function Reference

| Function | Size | Purpose |
|----------|------|---------|
| `FUN_004fae20` | 397 | Track definitions loader (nfs5.trk) |
| `FUN_004409d0` | 1685 | MAP + ENV loader |
| `FUN_00488090` | 929 | EDG loader (allocator + calls parser) |
| `FUN_00487e80` | 524 | EDG parser (reads 28-byte entries) |
| `FUN_0044fb40` | 230 | KEY file loader (AI route keyframes) |
| `FUN_0044e010` | 615 | DTX loader |
| `FUN_0046d330` | 717 | Factory Driver mission SCN loader |
| `FUN_00485100` | — | Edge segment constructor (from parsed EDG data) |
| `FUN_00489010` | — | Edge buffer resize |
| `FUN_004fb750` | 803 | Track list initialization |
| `FUN_004f3a40` | 640 | Car list loader (nfs5.car) |
