"""AI1: the drivable corridor of a converted track, from its real walls.

NFS4's vroad leftWall/rightWall are a coarse envelope (multiples of ~3.95 m,
median 12-16 m, up to 35 m) that lies BEHIND the real barriers; PU's own
tracks store the road edge there (median 5-6 m). Porsche Unleashed reads the
SimD walls for its AI and for the off-track respawn check (FUN_00408750:
|car+ED0| > wall -> FUN_0040f540), the .edg barrier for AI and camera. The
cars themselves are stopped by the FRD polygon walls (.hsw, wrapper hook), so
the AI believed it had metres of room where there was a wall: in the 2026-10-14
baseline 85-100% of wall contacts happened 6-9 m off the racing line, while the
line itself kept 6-8 m from every wall.

real_walls() casts a ray from the racing-line point of every slice along
-right and +right to the first wall edge (the same edges the .hsw holds) at
about road height, and returns per side min(old wall, hit + margin). A side
without a hit keeps the old value.
"""
import math

import export_hs_walls

CELL = 16.0
MAX_RAY_M = 40.0
MAX_DY_M = 4.0          # ignore walls of a deck above or below the road
MARGIN_M = 1.0          # keep the respawn check clear of a car touching the wall


def _grid(segments):
    grid = {}
    for s in segments:
        x0, x1 = sorted((s['a'][0], s['b'][0]))
        z0, z1 = sorted((s['a'][2], s['b'][2]))
        for gx in range(int(x0 // CELL), int(x1 // CELL) + 1):
            for gz in range(int(z0 // CELL), int(z1 // CELL) + 1):
                grid.setdefault((gx, gz), []).append(s)
    return grid


def _ray(ox, oz, dx, dz, seg, max_t):
    ax, az = seg['a'][0], seg['a'][2]
    ex, ez = seg['b'][0] - ax, seg['b'][2] - az
    den = dx * ez - dz * ex
    if abs(den) < 1e-9:
        return None
    t = ((ax - ox) * ez - (az - oz) * ex) / den
    u = ((ax - ox) * dz - (az - oz) * dx) / den
    return t if 0.0 <= u <= 1.0 and 0.0 < t <= max_t else None


def _first_hit(grid, ox, oy, oz, dx, dz):
    best, seen = None, set()
    for k in range(int(MAX_RAY_M // 4) + 2):
        px, pz = ox + dx * k * 4.0, oz + dz * k * 4.0
        for cx in (int(px // CELL) - 1, int(px // CELL), int(px // CELL) + 1):
            for cz in (int(pz // CELL) - 1, int(pz // CELL), int(pz // CELL) + 1):
                if (cx, cz) in seen:
                    continue
                seen.add((cx, cz))
                for s in grid.get((cx, cz), ()):
                    if abs((s['a'][1] + s['b'][1]) * 0.5 - oy) > MAX_DY_M:
                        continue
                    t = _ray(ox, oz, dx, dz, s, MAX_RAY_M)
                    if t is not None and (best is None or t < best):
                        best = t
    return best


def real_walls(track, line, margin=MARGIN_M):
    """[(left, right)] per slice: distance from the slice centre to the wall on
    each side, never wider than NFS4's leftWall/rightWall."""
    grid = _grid(export_hs_walls.wall_segments(track))
    out = []
    for i in range(track.n_vroad):
        v = track.vroad(i)
        ref, rt = v['ref'], v['right']
        n = math.hypot(rt[0], rt[2]) or 1.0
        rx, rz = rt[0] / n, rt[2] / n
        ox, oz = ref[0] + rx * line[i], ref[2] + rz * line[i]
        left, right = v['left_wall'], v['right_wall']
        hit = _first_hit(grid, ox, ref[1], oz, -rx, -rz)
        if hit is not None:
            left = min(left, max(0.5, hit - line[i] + margin))
        hit = _first_hit(grid, ox, ref[1], oz, rx, rz)
        if hit is not None:
            right = min(right, max(0.5, line[i] + hit + margin))
        out.append((left, right))
    return out


# --- HZ5: shortcuts --------------------------------------------------------
#
# NFS4 lets a car drive wherever its POLYVROADDATA road polygons go; the
# vroad leftWall/rightWall are only an envelope, and shortcuts leave it by up
# to 58 m (Atlantica 962-969, Lost Canyons 111-120, Aquatica 257-370, Empire
# 676-723). PU's off-track check FUN_00408750 compares |car+ED0| with the
# slice's leftWall (+0x40) on BOTH sides and starts the respawn (0040F540);
# the AI and the camera read the .edg barrier. The real walls are the .hsw
# polygon edges, so on those slices the corridor is widened to the drivable
# road and the respawn check reads the wider of the two sides.

SHORTCUT_MARGIN_M = 1.5


def drivable_extent(track):
    """[(left, right)] per slice: how far the drivable road polygons of the
    blocks around it reach to each side of the slice centre (0 = none)."""
    out = [[0.0, 0.0] for _ in range(track.n_vroad)]
    slices = [track.vroad(i) for i in range(track.n_vroad)]
    for block in range(track.n_blocks):
        verts = track.vertices(block)
        polys = track.polygons(block, 4)
        lo = max(0, block * 8 - 8)
        hi = min(track.n_vroad, block * 8 + 16)
        for rec in track.polyvroad(block):
            if rec['vroad_entry'] >= len(polys):
                continue
            for k in dict.fromkeys(polys[rec['vroad_entry']]['vertex']):
                p = verts[k]
                best = None
                for i in range(lo, hi):
                    v = slices[i]
                    d = [p[j] - v['ref'][j] for j in range(3)]
                    along = abs(sum(d[j] * v['forward'][j] for j in range(3)))
                    if best is None or along < best[0]:
                        best = (along, i, sum(d[j] * v['right'][j] for j in range(3)))
                _along, i, lat = best
                side = 1 if lat > 0 else 0
                out[i][side] = max(out[i][side], abs(lat))
    return [tuple(x) for x in out]


def widen_for_shortcuts(track, walls, margin=SHORTCUT_MARGIN_M, symmetric_left=True):
    """`walls` [(left, right)] widened where drivable road reaches past them.

    With symmetric_left the left value also covers the right side, because
    that is the only one FUN_00408750 reads. Returns (walls, widened slices)."""
    reach = drivable_extent(track)
    out, widened = [], 0
    for (left, right), (rl, rr) in zip(walls, reach):
        nl, nr = max(left, rl + margin), max(right, rr + margin)
        if (nl, nr) != (left, right):
            widened += 1
            if symmetric_left:
                nl = max(nl, nr)
        out.append((nl, nr))
    return out, widened
