# HS6: collision, AI route and track topology

Date: 2026-09-18. Stage 5 of the plan in [HS1](HS1-nfs4-track-migration.md). Code: `tools/track/nfs5_track_files.py`,
acceptance `tools/track/test_hs6_support.py`. The game was not started. (Walls were later replaced by the `.hsw`
runtime collision, HS16-HS19; the AI was tuned in AI1-AI6.)

## Main finding: all 19 High Stakes tracks are circuits

The distance between the first and the last vroad point of each of the 19 tracks is **exactly one step** (~4.6 m
with a grid step of 4.61-4.63 m). The spline is closed; no HS track is point-to-point.

That changes three things:

* `.jnc` is written like skidpad's: **one record `[1, 1, 0...]`**, not two dead ends. PU tells a circuit from a
  point-to-point by the `.jnc` topology: a dead end is one non-zero float, a closed route two or more (T007);
* `_fstart.scn` / `_bstart.scn` are **not written at all**. No PU circuit (monaco1-5) has them, so the engine does
  without, and a circuit must not get them;
* in `nfs5.trk` the tracks must be registered with the circuit flag: bit `0x10000` in `propsI[0]`, as monaco and
  skidpad have (HS8).

The start trigger generator is written and works (format decoded exactly, below) but is not used by any track in
the current set.

## Result

```
python tools/track/test_hs6_support.py
Aquatica   circuit  slices  1434  edg  5721  key 117 points  folded    0/ 2866  ok
...
Uk         circuit  slices   839  edg  3350  key 112 points  folded    0/ 1676  ok
ALL PASSED
```

All 19 tracks produce `.edg`, `.key` x2, `.jnc` and `.dtx` that pass the check.

## `.edg`: collision borders

Four polylines are built from vroad: the drivable edge left and right (flag `0x00`, from the `lane` width) and the
barrier left and right (flag `0x10`, from `leftWall`/`rightWall`). That gives `4 x (slices - 1)` segments.

**The polyline is broken at jumps.** NFS4 stores the barrier as a scalar per slice, so a sharp jump of `leftWall`
(the track opens up) costs it nothing. PU needs a polyline, and the same jump becomes one diagonal 62-71 m segment
blocking what was open. Segments longer than three grid steps are not written: a gap in the barrier is a gap in the
barrier. 0.1-0.6% of the segments are lost this way on the 19 tracks.

The `.edg` header is not a count (the engine takes the count from the file size) and its meaning is unknown: on the
15 stock tracks it correlates neither with the record count nor with the track length. We write the record count;
nothing depends on it.

### What the check deliberately does NOT require

The first acceptance checked that the road geometry lies inside the barriers. That was **a wrong premise**: the NFS4
`HIGH_RES_TRACK` chunk is not a narrow ribbon, it reaches +-43 m from the centre line at the 99th percentile
(shoulders, exits, plazas), while `leftWall`/`rightWall` stay around 12-16 m. The barrier is a collision boundary,
not the outline of the visible surface; 6-27% of road vertices outside is normal.

Instead the real failure mode is checked: **a folded polyline** at a hairpin where the wall offset exceeds the turn
radius and the barrier turns inside out. 0 or 1 such segments per track over all 19.

## `spdfa.bin` / `spdra.bin`: speed and a ready racing line

A second parse after the first in-game test closed the old question about the file structure. For `N = nVRoad` the
size splits like this on all 19 tracks:

```
ceil(N / 2) x uint8    speed: one value per two slices
N x uint8              helper AI array, meaning unknown
N x float32            lateral offset of the racing line in metres
```

A line point is `vroad.ref + vroad.right * offset`. Forward uses `spdfa`, reverse `spdra`; both are indexed in the
same geographic slice order.

Confirmed geometrically on Hometown: the old LSP along the `vroad` centre crosses the collision wall of the
boulevard island at slices 510-519 exactly, while the lines from `spdfa` and `spdra` pass with a minimum clearance
of 3.34 and 3.12 m. In the 90-degree town corner the authored offset reaches about 7 m to the inside: it contains an
early turn-in and an apex.

`build_lsp` writes the separate authored lines into `<track>0.lsp` and `<track>1.lsp`, not the `vroad` centre into
both. (AI1/AI6 later replaced them with learned lines, kept in `data/ai/lines/`.)

## `.key`: speed profile with a calibrated scale

NFS4 stores a speed byte per two slices (18...160 over all tracks, mean 82); PU a float (149...600, mean 371). The
scale was **fitted offline, not guessed**:

both games set the corner speed from the radius, so `v = k * sqrt(R)`. Fitting each side and dividing the
coefficients gives the unit ratio. Over 19 912 NFS4 samples and 283 PU samples it came out **4.30**; for comparison,
matching the means gives 4.52, matching the maxima 3.75. 4.30 is used (`AI_SPEED_SCALE`).

The profile of 832-1723 points is thinned to <= 120 (stock PU has 12...89): a point is kept where the speed departs
from the last kept one by more than a tolerance, and the tolerance grows until the budget fits. The file is exactly
3208 bytes, closing record `(100000, 600.0)`, free slots `(0xFFFFFFFF, 400.0)`, as in the originals.

**The reverse profile is indexed by the same geographic slices.** The correctly extracted half-rate `spdra` arrays
correlate more strongly with `spdfa` at the same index than after a flip. So `_backwards.key` is built from `spdra`
without an extra `[::-1]`: the engine chooses the direction itself.

## Start triggers (written, not used)

Format decoded from `alps_fstart.scn` to the byte:

```
TRIGGER_ELEMENT 5
<position>
<forward>\t<up>\t<right>          three basis vectors, tab separated
<type> 1 1.0 <width> 0.25 0.0 200.0 -1 -1 -1 -1 -1 -1 0 65535 0 0
<left post>\t<right post>
0 0 0 0\t0 0 0 0
```

Type 2 = start, 1 = finish. The gate posts are `position +- right*(width/2)`, `+` printed first; checked on an
original to the centimetre. CRLF lines, numbers with 6 decimals.

The gate width is the **drivable** width (`lane` left + right), not the distance between barriers: on an open stretch
the latter gives 43 m, while stock PU gates are 19-29 m.

The originals' `GEOM_ELEMENT` (finish arch, FourCC `FNSH`) is not written: it points at an article of the shared
object library that a converted track does not have, and a dangling reference is worse than no arch.

## `.dtx`

Correction to T007: "the same 11 bytes on every track" is **wrong**: all 15 files differ. Byte 0 is always 0, bytes
1-4 small (0-6), bytes 5-10 look like counters (up to 72). The fields are not decoded, so the template of a stock
track (alps) is copied; every value is smaller than the texture count of our archive, so no out-of-range access.

## What the acceptance does not prove

That the car drives. Proven: the files match their formats, slices are within the track, barriers are not folded,
the circuit topology is described the same way as skidpad's. Whether the AI drives with these speeds and whether the
player gets stuck in a barrier gap was a question for the first launch (HS9).

## Next

HS7: sky, `.env`, tints from `tr.ini`, sound zones, cameras, cops. HS8: registration in `nfs5ext.trk` with the
circuit flag, enabled by `extended_content`.
