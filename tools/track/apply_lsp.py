"""AI1: rewrite the racing lines (.lsp) of installed converted tracks.

    python apply_lsp.py --game <game folder> [--smooth 3] Gt1 ...

--smooth SIGMA low-passes the authored NFS4 lateral offsets (spdfa/spdra)
with a Gaussian of SIGMA slices along the ring: the authored lines jump up to
3.7 m between two slices (4.6 m apart), a 30-60 degree kink the PU pursuit
controller chases. 0 reproduces the shipped conversion.
"""
import argparse
import math
import os
import sys

import nfs4_frd as frd
import nfs5_track_files as tf
from convert_track import HS, track_directory


def smooth(values, sigma, ring=True):
    if sigma <= 0:
        return list(values)
    r = int(math.ceil(sigma * 3))
    w = [math.exp(-0.5 * (k / sigma) ** 2) for k in range(-r, r + 1)]
    n = len(values)
    out = []
    for i in range(n):
        acc = tot = 0.0
        for k, wk in zip(range(-r, r + 1), w):
            j = i + k
            if ring:
                j %= n
            elif not 0 <= j < n:
                continue
            acc += values[j] * wk
            tot += wk
        out.append(acc / tot)
    return out


def wall_crossings(track, segments, i, span=30.0, dy=4.0):
    """Lateral offsets (m, along vroad right) where wall edges cross slice i."""
    v = track.vroad(i)
    r, rt = v['ref'], v['right']
    nn = math.hypot(rt[0], rt[2]) or 1.0
    rx, rz = rt[0] / nn, rt[2] / nn
    out = []
    for s in segments:
        a, b = s['a'], s['b']
        if abs((a[1] + b[1]) * 0.5 - r[1]) > dy:
            continue
        ex, ez = b[0] - a[0], b[2] - a[2]
        den = rx * ez - rz * ex
        if abs(den) < 1e-9:
            continue
        t = ((a[0] - r[0]) * ez - (a[2] - r[2]) * ex) / den
        u = ((a[0] - r[0]) * rz - (a[2] - r[2]) * rx) / den
        if 0.0 <= u <= 1.0 and -span <= t <= span:
            out.append(t)
    return sorted(out)


def clear_line(track, line, clearance, rate, passes=4, obstacle=None, force=()):
    """AI2: keep the racing line `clearance` m from every real wall (.hsw edges:
    islands, pillar rows, kerb walls) inside the lane it runs in, and let it
    move sideways at most `rate` m per slice, so that it settles into the lane
    of an island early instead of crossing in front of its nose."""
    import export_hs_walls
    segments = export_hs_walls.wall_segments(track)
    n = len(line)
    grid = {}
    for s in segments:
        for gx in range(int(min(s['a'][0], s['b'][0]) // 16), int(max(s['a'][0], s['b'][0]) // 16) + 1):
            for gz in range(int(min(s['a'][2], s['b'][2]) // 16), int(max(s['a'][2], s['b'][2]) // 16) + 1):
                grid.setdefault((gx, gz), []).append(s)
    lo, hi = [], []
    for i in range(n):
        v = track.vroad(i)
        near = {}
        cx, cz = v['ref'][0], v['ref'][2]
        for gx in range(int(cx // 16) - 2, int(cx // 16) + 3):
            for gz in range(int(cz // 16) - 2, int(cz // 16) + 3):
                for s in grid.get((gx, gz), ()):
                    near[id(s)] = s
        xs = wall_crossings(track, near.values(), i)
        lefts = [x for x in xs if x < line[i]]
        rights = [x for x in xs if x > line[i]]
        left = max(lefts, default=-30.0)
        right = min(rights, default=30.0)
        # An obstacle inside the road (island, pillar): a thin wall (< 8 m
        # between its faces) with a lane of 5 m or more beyond it that is
        # bounded by another wall. A double-faced outer barrier has open
        # ground beyond it and keeps `clearance`.
        inner_l = (len(lefts) >= 3 and left - lefts[-2] < 8.0
                   and lefts[-2] - lefts[-3] >= 5.0)
        inner_r = (len(rights) >= 3 and rights[1] - right < 8.0
                   and rights[2] - rights[1] >= 5.0)
        cl_l = obstacle if (obstacle is not None and inner_l) else clearance
        cl_r = obstacle if (obstacle is not None and inner_r) else clearance
        a, b = left + cl_l, right - cl_r
        if a > b:
            a = b = (left + right) * 0.5
        lo.append(a)
        hi.append(b)
    # The correction (not the line) ramps in and out at `rate` per slice, so
    # the authored line keeps its own shape away from the obstacles and is
    # already shifted when the car reaches one.
    d = [min(max(line[i], lo[i]), hi[i]) - line[i] for i in range(n)]
    for a, b, x in force:                       # a chosen lane on slices a..b
        for i in range(a, b + 1):
            d[i % n] = x - line[i % n]
            lo[i % n] = hi[i % n] = x
    for _ in range(passes):
        for i in range(n - 2, -1, -1):          # backwards: prepare for what comes
            d[i] = min(max(d[i], d[i + 1] - rate), d[i + 1] + rate)
            d[i] = min(max(d[i], lo[i] - line[i]), hi[i] - line[i])
        for i in range(1, n):                   # forwards
            d[i] = min(max(d[i], d[i - 1] - rate), d[i - 1] + rate)
            d[i] = min(max(d[i], lo[i] - line[i]), hi[i] - line[i])
    x = [line[i] + d[i] for i in range(n)]
    moved = sum(1 for a, b in zip(line, x) if abs(a - b) > 0.25)
    return x, moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tracks', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--smooth', type=float, default=0.0)
    ap.add_argument('--clearance', type=float, default=0.0,
                    help='keep the line this many metres from real walls (0 = off)')
    ap.add_argument('--obstacle-clearance', type=float, default=None,
                    help='clearance from walls with road beyond them (islands, pillars)')
    ap.add_argument('--force', action='append', default=[], metavar='A:B:X',
                    help='put the line at lateral X m on slices A..B (needs one track)')
    ap.add_argument('--rate', type=float, default=0.3,
                    help='max sideways move of the line per slice with --clearance')
    a = ap.parse_args()
    for name in a.tracks:
        src = os.path.join(HS, name)
        track = frd.read(os.path.join(src, 'tr.frd'))
        d = track_directory(name)
        ring = tf.is_circuit(track)
        for direction, spd in enumerate(('spdfa.bin', 'spdra.bin')):
            _s, _a, line = tf.decode_spd(open(os.path.join(src, spd), 'rb').read(), track.n_vroad)
            line = smooth(line, a.smooth, ring)
            moved = 0
            force = [tuple(float(v) if k == 2 else int(v) for k, v in enumerate(f.split(':')))
                     for f in a.force] if direction == 0 else []
            if a.clearance > 0 or a.obstacle_clearance or force:
                line, moved = clear_line(track, line, a.clearance, a.rate,
                                         obstacle=a.obstacle_clearance, force=force)
            path = os.path.join(a.game, 'GameData', 'Track', 'racer', 'work', '%s%d.lsp' % (d, direction))
            with open(path, 'wb') as f:
                f.write(tf.build_lsp(track, line))
        print('%s: lsp smooth %.1f, clearance %.1f m, obstacles %s m, rate %.2f m/slice (%d slices moved)'
              % (d, a.smooth, a.clearance, a.obstacle_clearance, a.rate, moved))
    return 0


if __name__ == '__main__':
    sys.exit(main())
