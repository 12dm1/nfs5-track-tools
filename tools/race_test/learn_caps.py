"""AI4c: learn per-slice AI speed caps ("max start end kmh" lines of GameData/Track/<track>.aispeed).

    python learn_caps.py --game <game folder> --track uk BATCH [BATCH ...]
        [--step 0.1] [--decel 4.0] [--before 4] [--after 2] [--floor 40] [--dry-run]

The severity factors of the same file (learn_zones.py) cannot slow a car on the
straight before a bend the speed planner does not see yet. A cap can: the wrapper
clamps the planner's target speed (car+EB8, m/s) to the cap of the car's slice.

For every wall / object contact and every spin of a batch (race_trace), the speed
the car carried into it (its highest km/h in the 2 s before) minus --step becomes
the cap over slices s-before..s+after (lower caps already there stay). In front of
the spot the cap rises along a braking curve, v^2 = v_spot^2 + 2 * decel * distance,
so the AI brakes on the straight, gently, instead of inside the bend. Severity
factor lines are kept unchanged.
"""
import argparse
import glob
import json
import math
import os
import struct
import sys

from learn_zones import read_zones, spins, track_files, window

NO_CAP = 0.0


def read_caps(path, n):
    cap = [NO_CAP] * n
    if os.path.exists(path):
        for line in open(path):
            p = line.split()
            if len(p) == 4 and p[0] == 'max':
                a, b, v = int(p[1]), int(p[2]), float(p[3])
                for i in range(a, b + 1):
                    cap[i % n] = v
    return cap


def runs_of(values, skip):
    i, n = 0, len(values)
    while i < n:
        if skip(values[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and abs(values[j + 1] - values[i]) < 1e-6:
            j += 1
        yield i, j, values[i]
        i = j + 1


def write(path, k, cap, note):
    lines = ['# AI4 speed zones: "start end factor" = bend severity x factor (> 1 = slower);',
             '# "max start end kmh" = cap on the AI target speed. ' + note]
    lines += ['%d %d %.3f' % r for r in runs_of(k, lambda v: abs(v - 1.0) < 1e-6)]
    lines += ['max %d %d %.0f' % r for r in runs_of(cap, lambda v: v <= 0.0)]
    open(path, 'w').write('\n'.join(lines) + '\n')


def slice_length(lsp_path, n):
    data = open(lsp_path, 'rb').read()
    pts = [struct.unpack_from('<2f', data, 4 + 8 * i) for i in range(n)]
    d = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(n - 1)]
    d.sort()
    return d[len(d) // 2]


def trace_speeds(trace_path):
    """car -> [(tick, kmh)] of the first race in the trace."""
    out = {}
    seen = False
    for line in open(trace_path):
        if line.startswith('#'):
            if seen:
                break
            continue
        p = line.split(',')
        if p[0] != 'S':
            continue
        seen = True
        out.setdefault(int(p[2]), []).append((int(p[1]), float(p[7])))
    return out


def spin_events(trace_path):
    """(car, tick, slice) of every spin start (same rule as learn_zones.spins)."""
    out = []
    last = {}
    for line in open(trace_path):
        if line.startswith('#') and last:
            break
        p = line.strip().split(',')
        if p[0] != 'S' or len(p) < 18:
            continue
        car, sl, kmh = int(p[2]), int(p[3]), float(p[7])
        fx, fz, vx, vz = float(p[11]), float(p[12]), float(p[13]), float(p[14])
        v, f = math.hypot(vx, vz), math.hypot(fx, fz)
        slip = math.degrees(math.acos(max(-1.0, min(1.0, (vx * fx + vz * fz) / (v * f))))) if v > 1 and f > 0.1 else 0
        spinning = slip > 60 and kmh > 30
        if spinning and not last.get(car):
            out.append((car, int(p[1]), sl))
        last[car] = spinning or (last.get(car) and slip > 30)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('batches', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--track', required=True)
    ap.add_argument('--step', type=float, default=0.1, help='cap = entry speed x (1 - step)')
    ap.add_argument('--decel', type=float, default=4.0, help='braking curve in front of a cap, m/s^2')
    ap.add_argument('--before', type=int, default=4)
    ap.add_argument('--after', type=int, default=2)
    ap.add_argument('--floor', type=float, default=60.0,
                    help='events entered slower than this are not speed problems; no cap below it')
    ap.add_argument('--quiet', type=int, default=192,
                    help='ticks without another event of the car before a counted one')
    ap.add_argument('--window', type=int, default=128, help='ticks before the event for the entry speed')
    ap.add_argument('--reverse', action='store_true', help='reverse races: <track>_r.aispeed, <track>1.lsp')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    lsp, path = track_files(a.game, a.track, a.reverse)
    n = (os.path.getsize(lsp) - 4) // 8
    seg = slice_length(lsp, n)
    k = read_zones(path, n)
    cap = read_caps(path, n)

    spots = {}           # slice -> lowest new cap
    count = 0
    for b in a.batches:
        for d in glob.glob(os.path.join(b, '*_%s_*' % a.track)):
            ev_path = os.path.join(d, 'events.json')
            tp = os.path.join(d, 'dx11z_trace.csv')
            if not (os.path.exists(ev_path) and os.path.exists(tp)):
                continue
            speeds = trace_speeds(tp)
            ev = json.load(open(ev_path))
            events = [(e['car'], e['tick'], e['slice']) for e in ev if e['type'] in ('wall', 'hit3')]
            events += spin_events(tp)
            # only the first event of an episode: a hit after a spin or a car
            # contact happened at whatever speed the first one left
            busy = {}
            for e in ev:
                if e['type'] in ('wall', 'hit3', 'car'):
                    busy.setdefault(e['car'], []).append(e['tick'])
            for car, _t, _s in events:
                busy.setdefault(car, []).append(_t)
            for car, tick, sl in events:
                if any(tick - a.quiet <= t < tick for t in busy.get(car, ())):
                    continue
                before = [v for t, v in speeds.get(car, ()) if tick - a.window <= t <= tick]
                if not before or not 0 <= sl < n:
                    continue
                entry = max(before)
                if entry < a.floor:
                    continue
                current = cap[sl] if cap[sl] > 0 else entry
                new = max(a.floor, min(entry, current) * (1.0 - a.step))
                spots[sl] = min(spots.get(sl, new), new)
                count += 1
    for sl, v in spots.items():
        for i in window(sl, a.before, a.after, a.reverse):
            j = i % n
            if cap[j] <= 0 or cap[j] > v:
                cap[j] = v
    # braking curves in front of every capped slice (from the lowest caps up)
    ms = [c / 3.6 if c > 0 else None for c in cap]
    if a.reverse:                    # driving order: the curve rises against it
        ms = ms[::-1]
    for _ in range(2):
        for i in range(2 * n - 1, -1, -1):
            j, nxt = i % n, (i + 1) % n
            if ms[nxt] is None:
                continue
            reach = math.sqrt(ms[nxt] ** 2 + 2.0 * a.decel * seg)
            if reach * 3.6 > 330:
                continue
            if ms[j] is None or ms[j] > reach:
                ms[j] = reach
    if a.reverse:
        ms = ms[::-1]
    cap = [round(m * 3.6) if m is not None and m * 3.6 < 330 else NO_CAP for m in ms]
    capped = sum(1 for c in cap if c > 0)
    low = min((c for c in cap if c > 0), default=0)
    print('%-9s %3d events -> %3d spots, %4d slices capped, lowest %3.0f km/h (slice %.2f m)'
          % (a.track, count, len(spots), capped, low, seg))
    if not a.dry_run:
        write(path, k, cap, 'learned by learn_caps.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
