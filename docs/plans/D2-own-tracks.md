# D2 plan: your own track, made in Blender

Status: **plan**, nothing built yet (2026-11-23). Today a new track can only come in through the NFS4 format: a
High Stakes track, or one made by the community in T3ED ([new-tracks-frd.md](../new-tracks-frd.md)). D2 adds a path
from a scene you model yourself.

## What the converter needs from a source

Every PU file is written from one in-memory model, `nfs4_frd.Frd`, plus a few side files. The writers use only this
much of it (counted in the code, 2026-11-23):

| interface | used by |
|---|---|
| `n_vroad`, `vroad(i)` (ref, normal, forward, right, left/right wall, lane), `road_length` | every stage: slices, SimD, `.edg`, `.key`, `.lsp`, map art, atmosphere |
| `n_blocks`, `vertices(b)`, `shading(b)`, `polygons(b, chunk)` | geometry (CRP), walls, corridor |
| `polyvroad(b)` (per road polygon: wall edges and surface nibble) | walls (`.hsw`), corridor, surfaces |
| `objects(b)`, `global_objects_flat()`, `lights(b)` | objects, animation, lamps, object sounds |
| `tr0.qfs` (textures by polygon texture index), `sky.qfs`, `tr.ini`, `spdfa/spdra.bin` | textures, sky, atmosphere seed, AI speeds and racing line |

Several stages open `tr.frd` by path themselves (`nfs4_geometry.convert`, `export_hs_walls`), so the model is not
passed around as one object.

## Options

| | how | for | against |
|---|---|---|---|
| **A (recommended)** | Blender scene -> **a complete NFS4 track folder** (`tr.frd`, `tr0.qfs`, `sky.qfs`, `tr.ini`, `spdfa/spdra.bin`) -> the converter, unchanged | zero changes to a converter that already makes 19 + community tracks race; the folder opens in T3ED for fixes; it may even run in High Stakes; one validator for every source | FRD constraints: quads, 8 slices per block, per-block vertex pools, POLYVROADDATA for walls; a few FRD fields are not understood (5 dwords of a vroad record, parts of the block header: NFS4 uses them, PU does not) |
| B | Blender scene -> an object with the `Frd` interface -> the converter's writers | triangles as they are, no NFS4 limits | refactor every stage that reads `tr.frd` by path; a source format only this tool reads |
| C | Blender scene -> PU files directly | no NFS4 model at all | re-implements the converter's stages; months |

A first. If the FRD writer hits a wall (an NFS4 limit that hurts PU tracks), the same builder can feed B, because A's
builder produces exactly the data B needs before it serialises it.

## Steps

### D2.0 Spike: a generated oval (no Blender)

A Python script builds a flat 800 m oval as an NFS4 folder: vroad every 4.6 m, 8-slice blocks, road quads with one
texture, walls as POLYVROADDATA edges, `spdfa/spdra` from curvature, `tr.ini` and `sky.qfs` from a template.

Acceptance: `nfs4_frd.py` reads it to EOF; `convert_track.py` converts it; in nfs5-web it loads (`[hs] track ...
walls`), the walls stop the car, the AI drives a full lap. That proves the minimal FRD a PU track needs and settles
the open FRD fields for our purpose.

### D2.1 Blender conventions and exporter

A scene layout, checked by the exporter (`tools/blender/export_track.py`, run by `blender --background`, like
`blender_build_fragment.py`):

| collection / object | meaning |
|---|---|
| `Centerline` (curve) | the road's centre and direction; closed = circuit. Its tilt is the road's banking |
| `Road` | drivable surface; material name or custom property `surface` = asphalt / dirt / grass / snow... |
| `Terrain` | everything visible beside the road |
| `Walls` | wall faces (or edges) that stop cars; become POLYVROADDATA edges and the `.hsw` |
| `Objects` | props: static, or `smackable` with a mass; instanced meshes stay instances |
| `Lights`, `Sounds` (empties) | lamps; ambience points (later) |
| scene properties | track name, atmosphere preset (`data/atmosphere/presets/*.ini`), ambience source |

Output: JSON + NumPy arrays (as `export_track_fragment.py` does in the other direction), metres, game axes.

### D2.2 Builder: scene -> NFS4 folder

1. Slice the centreline every 4.6 m (the HS spacing: the runtime's AI tuning for converted tracks assumes it).
2. Per slice: ref point, forward, right, normal; lane width and left/right wall distance by casting rays to `Walls`.
3. Blocks of 8 slices; every `Road`/`Terrain` face goes to the block of its nearest slice; triangles paired into
   quads (or degenerate quads); per-block vertex pools.
4. POLYVROADDATA for road polygons: surface nibble from the material, wall flags from `Walls`.
5. Objects as XObj (static first; animated later).
6. Textures: Blender images -> `tr0.qfs` (power-of-two squares, ARGB8888).
7. AI: speeds from curvature and the racing line from `racing_line.py`, written as `spdfa/spdra`.
8. `tr.ini` from the chosen atmosphere preset; `sky.qfs` from a template or the scene's sky images.

### D2.3 Validator for any source

`tools/track/check_source.py <folder>`: files present (any case), FRD parsed to EOF, spd sizes `5N + ceil(N/2)`,
block count against `N`, texture codes the converter decodes, the PU directory name (15 characters, not stock), a
closed loop for a circuit. It runs on HS tracks, community tracks and D2 output alike.

### D2.4 An example track and the guide

A small original circuit made in Blender with CC0 textures and models (Poly Haven), its `.blend` and export in the
repository (our own content, no game data), and `docs/own-track.md`: model, export, build, convert, race.

### D2.5 Optional

The generated folder in T3ED and in High Stakes itself; point-to-point tracks (the start trigger generator exists
in `nfs5_track_files.py`, unused so far).

## Testing

Each step ends with a race in nfs5-web: `make_mod_layer.py` over the build folder, a direct race with
`fe=0 track_name=<track> car[0].aidrive=1`, the log lines from [tutorial.md](../tutorial.md) step 2, plus wall hits
and lap completion. The race script used for D1 (headless Chrome, server log) becomes a tool in nfs5-web.

## Decisions to make before D2.0

1. Option A (NFS4 folder) as recommended, or B.
2. Slice spacing 4.6 m (reuse the HS AI tuning) as recommended, or PU's 6.0 m.
3. Circuits only in the first version (all 19 converted tracks are circuits) as recommended.
4. Who makes the example track's art: CC0 assets assembled by an agent in Blender, or the user's own.
