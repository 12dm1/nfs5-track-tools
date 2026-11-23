# `GameData/Track/<track>.hsw` (High Stakes walls, new file)

Written by `tools/track/export_hs_walls.py` (called from `convert_track.py`). Read by the runtime wall hook
(00498FF0): see [../runtime.md](../runtime.md) and [../findings/HS11-walls-alpha.md](../findings/HS11-walls-alpha.md).
The stock game ignores it.

Little-endian:

```
char   magic[4] = 'HSW1'
uint32 count
count x { float ax, ay, az, bx, by, bz, height }      // road walls
--- HZ7 tail (optional; an older runtime stops after the walls) ---
char   magic[4] = 'HSX1'
uint32 flags                                         // 1 = one-sided: the free side is LEFT of a->b in XZ
uint32 objects
objects x { float ax, ay, az, bx, by, bz, height }   // static object walls (posts, trunks, rocks)
```

* `a`, `b` are the wall base points in PU world space (metres, same space as the CRP).
* `height` is the wall height above the base. Road walls: 16 m, or the FRD edge byte x 0.25 m (HZ5), shortened
  under an overpass. A car whose centre is above `base + height - 0.2` passes over.
* Road wall edges are oriented so the road is to the left of a->b (`_oriented`). With flag 1 the runtime tests a
  wall only when the car centre is on its free side, so a wall keeps the car on its road and entering from outside
  is free (as in NFS4).
* Object walls: the bottom 2 m of each steep face (`|n.y| < 0.7|n|`) of a `collision_type == 1` object, trimmed to
  opaque texels; translucent textures give only 10 cm trunks (HZ7).
* `--wall-inset <m>` moves road walls towards the road (default 0: the runtime collides the car as an OBB).
