"""AI3: our own racing line for converted NFS4 tracks.

    python racing_line.py --game <game folder> [--wall-margin 2.0] [--shoulder 2.0] Gt1 ...
    python racing_line.py --game ... --report Gt1        (statistics only, writes nothing)

NFS4's AI ran on its spline like on rails, so the authored lines (spdfa/spdra)
pass a metre or two from pillars, kink by up to 3.7 m between two slices and
swing across the road just before an island. Porsche Unleashed steers a real
car at a point of the line 30-40 m ahead, with inertia and slip, and that line
puts it into the walls.

This builds the line from the track instead:

  corridor  per slice, the lateral interval between the real walls (the .hsw
            edges) around the authored line point - so an island is passed in
            the same lane as before - narrowed by --wall-margin (car half width
            and a buffer) and limited to the road: +-(NFS4 lane + --shoulder)
            where no wall is near (Summit's narrow road with banks beside it).
  line      the minimum-curvature path inside that corridor over the whole
            ring (projected gradient on the squared second differences of the
            line points): outside entry, apex, outside exit, no kinks.

The same line is written to both <track>0.lsp and <track>1.lsp: minimum
curvature does not depend on the direction.
"""
import argparse
import math
import os
import sys

import numpy as np

import export_hs_walls
import nfs4_frd as frd
import nfs5_track_files as tf
from apply_lsp import smooth, clear_line, wall_crossings
from convert_track import HS, track_directory

SPAN = 30.0


def geometry(track):
    """Centres (N,2) and unit right vectors (N,2) of the vroad slices."""
    c = np.array([[v['ref'][0], v['ref'][2]] for v in (track.vroad(i) for i in range(track.n_vroad))])
    r = np.array([[v['right'][0], v['right'][2]] for v in (track.vroad(i) for i in range(track.n_vroad))])
    r /= np.linalg.norm(r, axis=1, keepdims=True)
    return c, r


def corridor(track, ref, wall_margin, shoulder, obstacle_margin=5.0):
    """[lo, hi] lateral bounds per slice (metres along the slice's right)."""
    segments = export_hs_walls.wall_segments(track)
    grid = {}
    for s in segments:
        for gx in range(int(min(s['a'][0], s['b'][0]) // 16), int(max(s['a'][0], s['b'][0]) // 16) + 1):
            for gz in range(int(min(s['a'][2], s['b'][2]) // 16), int(max(s['a'][2], s['b'][2]) // 16) + 1):
                grid.setdefault((gx, gz), []).append(s)
    n = track.n_vroad
    lo, hi = np.zeros(n), np.zeros(n)
    for i in range(n):
        v = track.vroad(i)
        cx, cz = v['ref'][0], v['ref'][2]
        near = {}
        for gx in range(int(cx // 16) - 2, int(cx // 16) + 3):
            for gz in range(int(cz // 16) - 2, int(cz // 16) + 3):
                for s in grid.get((gx, gz), ()):
                    near[id(s)] = s
        xs = wall_crossings(track, near.values(), i, span=SPAN)
        lefts = [x for x in xs if x < ref[i]]
        rights = [x for x in xs if x > ref[i]]
        left = max(lefts, default=-SPAN)
        right = min(rights, default=SPAN)
        # Islands and pillars (a thin wall with a walled lane beyond it) get
        # obstacle_margin: 5 m took the Redrock/Lost Canyons/Empire obstacle
        # hits from 36 to 5 (OB5, 2026-10-14).
        inner_l = len(lefts) >= 3 and left - lefts[-2] < 8.0 and lefts[-2] - lefts[-3] >= 5.0
        inner_r = len(rights) >= 3 and rights[1] - right < 8.0 and rights[2] - rights[1] >= 5.0
        a = left + (obstacle_margin if inner_l else wall_margin)
        b = right - (obstacle_margin if inner_r else wall_margin)
        # No wall close by: keep to the road (banks, ditches and rocks are not
        # walls in the .hsw but flip a car).
        a = max(a, -(v['lane'][0] + shoulder))
        b = min(b, v['lane'][1] + shoulder)
        if a > b:
            mid = (max(left, -(v['lane'][0] + shoulder)) + min(right, v['lane'][1] + shoulder)) * 0.5
            a = b = mid
        lo[i], hi[i] = a, b
    return lo, hi


def min_curvature(c, r, lo, hi, x0, iters=20000, step=0.03):
    """Minimise sum |P[i-1] - 2 P[i] + P[i+1]|^2, P = c + r * x (ring), with
    lo <= x <= hi: accelerated projected gradient (FISTA). The gradient's
    Lipschitz constant is 32 (second difference squared), so step < 1/32."""
    x = np.clip(np.array(x0, dtype=float), lo, hi)
    y, t = x.copy(), 1.0

    def grad(v):
        p = c + r * v[:, None]
        d = np.roll(p, 1, axis=0) - 2.0 * p + np.roll(p, -1, axis=0)
        g = np.roll(d, 1, axis=0) - 2.0 * d + np.roll(d, -1, axis=0)
        return 2.0 * np.einsum('ij,ij->i', g, r)

    for _ in range(iters):
        x_new = np.clip(y - step * grad(y), lo, hi)
        t_new = (1.0 + math.sqrt(1.0 + 4.0 * t * t)) * 0.5
        y = x_new + ((t - 1.0) / t_new) * (x_new - x)
        x, t = x_new, t_new
    return x


def curvature(c, r, x):
    p = c + r * x[:, None]
    a = np.roll(p, 1, axis=0) - p
    b = np.roll(p, -1, axis=0) - p
    ang = np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(axis=1))
    seg = np.linalg.norm(a, axis=1) + np.linalg.norm(b, axis=1)
    return np.abs(math.pi - np.abs(ang)) / np.maximum(seg * 0.5, 0.1)


def build(name, wall_margin, shoulder, iters, obstacle_margin=5.0):
    src = os.path.join(HS, name)
    track = frd.read(os.path.join(src, 'tr.frd'))
    _s, _a, authored = tf.decode_spd(open(os.path.join(src, 'spdfa.bin'), 'rb').read(), track.n_vroad)
    ring = tf.is_circuit(track)
    ref, _moved = clear_line(track, smooth(authored, 3, ring), 0.0, 0.3, obstacle=5.0)
    c, r = geometry(track)
    lo, hi = corridor(track, ref, wall_margin, shoulder, obstacle_margin)
    x = min_curvature(c, r, lo, hi, ref, iters=iters)
    return track, np.array(authored), np.array(ref), x, c, r, lo, hi


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('tracks', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--wall-margin', type=float, default=2.0)
    ap.add_argument('--shoulder', type=float, default=2.0)
    ap.add_argument('--obstacle-margin', type=float, default=5.0)
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--report', action='store_true', help='print statistics, write nothing')
    a = ap.parse_args()
    for name in a.tracks:
        track, authored, ref, x, c, r, lo, hi = build(name, a.wall_margin, a.shoulder, a.iters,
                                                       a.obstacle_margin)
        k_old, k_new = curvature(c, r, ref), curvature(c, r, x)
        print('%-9s slices %4d | max |curvature| %.3f -> %.3f 1/m, sum %.1f -> %.1f | '
              'mean |shift| %.1f m, max %.1f m | corridor width median %.1f m'
              % (name, len(x), k_old.max(), k_new.max(), k_old.sum(), k_new.sum(),
                 np.abs(x - ref).mean(), np.abs(x - ref).max(), np.median(hi - lo)))
        if a.report:
            continue
        d = track_directory(name)
        for direction in (0, 1):
            path = os.path.join(a.game, 'GameData', 'Track', 'racer', 'work', '%s%d.lsp' % (d, direction))
            with open(path, 'wb') as f:
                f.write(tf.build_lsp(track, list(x)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
