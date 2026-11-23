"""AI4: correct the installed racing lines from where the cars hit walls.

    python learn_line.py --game <game folder> BATCH_DIR [BATCH_DIR ...]
        [--gain 0.3] [--max-step 2.0] [--margin 2.0] [--dry-run]

Every wall contact of a race_test batch (events.json + the installed .lsp)
tells on which side of the racing line the car was when it hit: it cut inside
(pursuit chord) or ran wide. The line is moved the other way over the stretch
that steered the car there (slices s-6..s+10 around the contact s, Gaussian
taper), by gain * |deviation| per contact, at most max_step per slice and
iteration, and never closer than `margin` to a real wall (.hsw) or 5 m to an
island or pillar. Repeat: run a batch, learn, run again; the line converges
where the cars actually stop hitting things. <track>0.lsp (forward), or with
--reverse <track>1.lsp (reverse races).
"""
import argparse
import glob
import json
import math
import os
import struct
import sys
from collections import defaultdict

import numpy as np

import export_hs_walls
import nfs4_frd as frd
import nfs5_track_files as tf
from apply_lsp import wall_crossings
from convert_track import HS, track_directory

NAMES = {'aquatica': 'Aquatica', 'atlantic': 'Atlantic', 'dolphin': 'Coastal', 'country': 'Country',
         'empire': 'Empire', 'france': 'France', 'germany': 'Germany', 'gt1': 'Gt1', 'gt2': 'Gt2',
         'gt3': 'Gt3', 'hills': 'Hills', 'hometown': 'Hometown', 'lostcany': 'Lostcany', 'park': 'Park',
         'redrock': 'Redrock', 'rockypas': 'Rockypas', 'snowy': 'Snowy', 'summit': 'Summit', 'uk': 'Uk'}


def read_lsp(path, n):
    data = open(path, 'rb').read()
    return np.array([struct.unpack_from('<2f', data, 4 + 8 * i) for i in range(n)])


def laterals(track, pts):
    out = np.zeros(len(pts))
    for i in range(len(pts)):
        v = track.vroad(i)
        r, rt = v['ref'], v['right']
        nn = math.hypot(rt[0], rt[2]) or 1.0
        out[i] = ((pts[i][0] - r[0]) * rt[0] + (pts[i][1] - r[2]) * rt[2]) / nn
    return out


def bounds(track, line, margin, obstacle=5.0, span=30.0):
    segments = export_hs_walls.wall_segments(track)
    grid = {}
    for s in segments:
        for gx in range(int(min(s['a'][0], s['b'][0]) // 16), int(max(s['a'][0], s['b'][0]) // 16) + 1):
            for gz in range(int(min(s['a'][2], s['b'][2]) // 16), int(max(s['a'][2], s['b'][2]) // 16) + 1):
                grid.setdefault((gx, gz), []).append(s)
    n = len(line)
    lo, hi = np.zeros(n), np.zeros(n)
    for i in range(n):
        v = track.vroad(i)
        cx, cz = v['ref'][0], v['ref'][2]
        near = {}
        for gx in range(int(cx // 16) - 2, int(cx // 16) + 3):
            for gz in range(int(cz // 16) - 2, int(cz // 16) + 3):
                for s in grid.get((gx, gz), ()):
                    near[id(s)] = s
        xs = wall_crossings(track, near.values(), i, span=span)
        lefts = [x for x in xs if x < line[i]]
        rights = [x for x in xs if x > line[i]]
        left = max(lefts, default=-span)
        right = min(rights, default=span)
        inner_l = len(lefts) >= 3 and left - lefts[-2] < 8.0 and lefts[-2] - lefts[-3] >= 5.0
        inner_r = len(rights) >= 3 and rights[1] - right < 8.0 and rights[2] - rights[1] >= 5.0
        a = left + (obstacle if inner_l else margin)
        b = right - (obstacle if inner_r else margin)
        # never force a move: a line already closer stays where it is
        lo[i] = min(a, line[i])
        hi[i] = max(b, line[i])
    return lo, hi


def contacts(batches, track_dir_name, kinds=('wall',)):
    """(slice, car lateral x/z) of every wall episode of this track in the batches."""
    out = []
    for b in batches:
        for d in glob.glob(os.path.join(b, '*_%s_*' % track_dir_name)):
            ev_path = os.path.join(d, 'events.json')
            if not os.path.exists(ev_path):
                continue
            for e in json.load(open(ev_path)):
                if e['type'] in kinds:
                    out.append((e['slice'], e['x'], e['z']))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('batches', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--gain', type=float, default=0.3)
    ap.add_argument('--max-step', type=float, default=2.0)
    ap.add_argument('--margin', type=float, default=2.0)
    ap.add_argument('--before', type=int, default=6)
    ap.add_argument('--after', type=int, default=10)
    ap.add_argument('--kinds', default='wall',
                    help='contact kinds to learn from, e.g. wall,hit3 (hit3 = objects / terrain)')
    ap.add_argument('--reverse', action='store_true',
                    help='reverse races: learn <track>1.lsp; the slice number falls while driving')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    for dname, hs in NAMES.items():
        hits = contacts(a.batches, dname, tuple(a.kinds.split(',')))
        src = os.path.join(HS, hs)
        track = frd.read(os.path.join(src, 'tr.frd'))
        n = track.n_vroad
        d = track_directory(hs)
        lsp0 = os.path.join(a.game, 'GameData', 'Track', 'racer', 'work', '%s%d.lsp' % (d, 1 if a.reverse else 0))
        line = laterals(track, read_lsp(lsp0, n))
        push = np.zeros(n)
        for sl, x, z in hits:
            if not 0 <= sl < n:
                continue
            v = track.vroad(sl)
            r, rt = v['ref'], v['right']
            nn = math.hypot(rt[0], rt[2]) or 1.0
            dev = ((x - r[0]) * rt[0] + (z - r[2]) * rt[2]) / nn - line[sl]
            amount = -a.gain * dev                     # away from where the car went
            for k in range(-a.before, a.after + 1):
                w = math.exp(-0.5 * (k / ((a.before + a.after) / 4.0)) ** 2)
                push[(sl - k if a.reverse else sl + k) % n] += amount * w   # k > 0: after the contact
        push = np.clip(push, -a.max_step, a.max_step)
        if not hits or not np.any(push):
            print('%-9s no wall contacts' % dname)
            continue
        lo, hi = bounds(track, line, a.margin)
        new = np.clip(line + push, lo, hi)
        moved = int(np.sum(np.abs(new - line) > 0.05))
        print('%-9s %3d contacts, %4d slices moved, max %.1f m, mean %.2f m'
              % (dname, len(hits), moved, np.abs(new - line).max(), np.abs(new - line)[np.abs(new - line) > 0.05].mean() if moved else 0))
        if a.dry_run:
            continue
        with open(lsp0, "wb") as f:
            f.write(tf.build_lsp(track, list(new)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
