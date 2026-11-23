"""AI4d: keep the lap time a relaxed zone set won back, restore the safe set where it cost trouble.

    python revert_zones.py --game <game folder> --safe DIR --state DIR --track uk BATCH [BATCH ...]
        [--lead 40] [--tail 5] [--ramp-slope 0.025]

--safe holds the known-good .aispeed files (e.g. <work>/backups/aispeed-C4), --state the relaxed
ones the BATCHes were raced with (factors given back, caps maybe lower). Around every spot with
a wall/object contact, spin or stall in the batches (slices s-lead..s+tail: the braking that led
there), the safe severity factors come back; everywhere else the relaxed ones stay. Caps: the
lower of both everywhere. The ramps in front of the factors are rebuilt.
"""
import argparse
import glob
import json
import os
import sys

from learn_caps import read_caps, write
from learn_zones import ramp_dir, read_zones, spins, track_files, window


def trouble(batches, track):
    out = []
    for b in batches:
        for d in glob.glob(os.path.join(b, '*_%s_*' % track)):
            ev = os.path.join(d, 'events.json')
            if os.path.exists(ev):
                out += [e['slice'] for e in json.load(open(ev)) if e['type'] in ('wall', 'hit3', 'stall')]
            tp = os.path.join(d, 'dx11z_trace.csv')
            if os.path.exists(tp):
                out += spins(tp)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('batches', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--safe', required=True)
    ap.add_argument('--state', required=True)
    ap.add_argument('--track', required=True)
    ap.add_argument('--lead', type=int, default=40)
    ap.add_argument('--tail', type=int, default=5)
    ap.add_argument('--ramp-slope', type=float, default=0.025)
    ap.add_argument('--reverse', action='store_true', help='reverse races: <track>_r.aispeed, <track>1.lsp')
    a = ap.parse_args()
    lsp, live = track_files(a.game, a.track, a.reverse)
    n = (os.path.getsize(lsp) - 4) // 8
    name = os.path.basename(live)
    ks, cs = read_zones(os.path.join(a.safe, name), n), read_caps(os.path.join(a.safe, name), n)
    kr, cr = read_zones(os.path.join(a.state, name), n), read_caps(os.path.join(a.state, name), n)
    spots = trouble(a.batches, a.track)
    restore = set()
    for s in spots:
        for i in window(s, a.lead, a.tail, a.reverse):
            restore.add(i % n)
    k = [max(ks[i], kr[i]) if i in restore else kr[i] for i in range(n)]
    k = [round(v, 3) for v in ramp_dir(k, a.ramp_slope, a.reverse)]
    cap = [min(c for c in (cs[i], cr[i]) if c > 0) if (cs[i] > 0 or cr[i] > 0) else 0.0 for i in range(n)]
    write(live, k, cap, 'safe C4 around trouble, relaxed elsewhere')
    ex = lambda t: sum(v - 1.0 for v in t)
    print('%-9s %3d trouble events, %4d slices restored, factor excess safe %.0f relaxed %.0f -> %.0f'
          % (a.track, len(spots), len(restore), ex(ks), ex(kr), ex(k)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
