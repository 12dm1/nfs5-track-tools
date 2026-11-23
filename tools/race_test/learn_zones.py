"""AI4: learn per-track AI speed zones (GameData/Track/<track>.aispeed) from race_test batches.

    python learn_zones.py --game <game folder> --track uk BATCH [BATCH ...]
        [--step 0.15] [--min-events 2] [--lead 12] [--tail 4] [--max 3.0] [--dry-run]

The wrapper multiplies the bend severity the AI's speed planner measures by the
factor of the slice the car is on ([ai] speed_zones=1), like EA's own per-track
table in FUN_004a2dc0 for the stock tracks: > 1 = the AI slows down there.

Trouble = wall and object/terrain contacts (race_trace kinds 4 and 3), stalls,
and spins (slip > 60 deg above 30 km/h, from the S rows). Every spot (10-slice
bin) with at least --min-events of them across the batches gets its factor
raised by --step over slices [s - lead, s + tail] (braking happens before the
spot), then every zone gets a ramp in front of it (--ramp-slope per slice), so
the AI brakes early and gently instead of inside the bend. Car-to-car contacts
are counted but not targeted. Spots without trouble
keep their factor. The file is plain text, "start end factor" per line.
"""
import argparse
import glob
import json
import math
import os
import sys
from collections import Counter, defaultdict


def read_zones(path, n):
    k = [1.0] * n
    if os.path.exists(path):
        for line in open(path):
            p = line.split()
            if len(p) == 3 and not line.startswith('#'):
                a, b, f = int(p[0]), int(p[1]), float(p[2])
                for i in range(a, b + 1):
                    k[i % n] = f
    return k


def ramp(k, slope):
    """Approach every zone gradually: the factor before a zone falls by at most `slope`
    per slice (k[i] >= k[j] - slope * (j - i) for the next 1/slope slices). A step
    from 1.0 to 1.75 made the AI brake hard inside the bend before and spin; the ramp
    starts the braking earlier and gently. Idempotent (max-plus with a linear kernel)."""
    n = len(k)
    out = list(k)
    reach = int(max(k) / slope) + 1 if slope > 0 else 0
    for i in range(n):
        for d in range(1, min(reach, n)):
            v = k[(i + d) % n] - slope * d
            if v > out[i]:
                out[i] = v
    return out


def track_files(game, track, reverse):
    """(racing line, zone file) of a direction: reverse races (REVERSE=1, the slice
    number falls) use <track>1.lsp and <track>_r.aispeed."""
    lsp = os.path.join(game, 'GameData', 'Track', 'racer', 'work', '%s%d.lsp' % (track, 1 if reverse else 0))
    return lsp, os.path.join(game, 'GameData', 'Track', '%s%s.aispeed' % (track, '_r' if reverse else ''))


def window(s, lead, tail, reverse):
    """Slices from `lead` before a spot to `tail` after it, in driving order."""
    return range(s - tail, s + lead + 1) if reverse else range(s - lead, s + tail + 1)


def ramp_dir(k, slope, reverse):
    return ramp(k[::-1], slope)[::-1] if reverse else ramp(k, slope)


def write_zones(path, k, note):
    lines = ['# AI4 speed zones: start end factor (bend severity x factor; > 1 = slower)',
             '# ' + note]
    i, n = 0, len(k)
    while i < n:
        if abs(k[i] - 1.0) < 1e-6:
            i += 1
            continue
        j = i
        while j + 1 < n and abs(k[j + 1] - k[i]) < 1e-6:
            j += 1
        lines.append('%d %d %.3f' % (i, j, k[i]))  # one line per run of equal factors
        i = j + 1
    if os.path.exists(path):                    # keep the speed caps (learn_caps.py)
        lines += [l.rstrip('\n') for l in open(path) if l.startswith('max ')]
    open(path, 'w').write('\n'.join(lines) + '\n')
    return len(lines) - 2


def spins(trace_path):
    """(slice) of every spin start: slip > 60 deg above 30 km/h."""
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
            out.append(sl)
        last[car] = spinning or (last.get(car) and slip > 30)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('batches', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--track', required=True, help='track file name, e.g. uk')
    ap.add_argument('--step', type=float, default=0.15)
    ap.add_argument('--min-events', type=int, default=2)
    ap.add_argument('--lead', type=int, default=12)
    ap.add_argument('--tail', type=int, default=4)
    ap.add_argument('--max', type=float, default=3.0)
    ap.add_argument('--ramp-slope', type=float, default=0.025,
                    help='largest factor drop per slice before a zone (0 = steps)')
    ap.add_argument('--decay', type=float, default=1.0,
                    help='zones with no trouble in these batches nor --history keep this share '
                         'of their excess over 1.0 (0.9 = give back 10%% per iteration)')
    ap.add_argument('--history', nargs='*', default=[],
                    help='earlier batches: their trouble only blocks the decay, raises nothing')
    ap.add_argument('--reverse', action='store_true', help='reverse races: <track>_r.aispeed, <track>1.lsp')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    events = Counter()
    kinds = defaultdict(Counter)
    top = 0
    runs = 0
    past = Counter()
    for b in a.history:
        for d in glob.glob(os.path.join(b, '*_%s_*' % a.track)):
            ev_path = os.path.join(d, 'events.json')
            if os.path.exists(ev_path):
                for e in json.load(open(ev_path)):
                    if e['type'] in ('wall', 'hit3', 'stall'):
                        past[e['slice'] // 10] += 1
            tp = os.path.join(d, 'dx11z_trace.csv')
            if os.path.exists(tp):
                for sl in spins(tp):
                    past[sl // 10] += 1
    for b in a.batches:
        for d in glob.glob(os.path.join(b, '*_%s_*' % a.track)):
            ev_path = os.path.join(d, 'events.json')
            if not os.path.exists(ev_path):
                continue
            runs += 1
            summary = json.load(open(os.path.join(d, 'summary.json')))
            top = max(top, summary.get('trace', {}).get('top_slice', 0))
            for e in json.load(open(ev_path)):
                if e['type'] in ('wall', 'hit3', 'stall'):
                    events[e['slice'] // 10] += 1
                    kinds[e['slice'] // 10][e['type']] += 1
                elif e['type'] == 'car':
                    kinds[e['slice'] // 10]['car'] += 1
            tp = os.path.join(d, 'dx11z_trace.csv')
            if os.path.exists(tp):
                for sl in spins(tp):
                    events[sl // 10] += 1
                    kinds[sl // 10]['spin'] += 1
    lsp, path = track_files(a.game, a.track, a.reverse)
    n = (os.path.getsize(lsp) - 4) // 8 if os.path.exists(lsp) else top + 1
    hot = sorted(b for b, c in events.items() if c >= a.min_events)
    # every slice of a hot spot's braking stretch is raised once per iteration
    k2 = read_zones(path, n)
    raised = set()
    for b in hot:
        s = b * 10 + 5
        for i in window(s, a.lead, a.tail, a.reverse):
            raised.add(i % n)
    for j in raised:
        k2[j] = min(a.max, k2[j] * (1.0 + a.step))
    kept = set(raised)
    for b in set(events) | set(past):
        s = b * 10 + 5
        for i in window(s, a.lead, a.tail, a.reverse):
            kept.add(i % n)
    decayed = 0
    if a.decay < 1.0:
        for j in range(n):
            if j not in kept and k2[j] > 1.0:
                k2[j] = 1.0 + (k2[j] - 1.0) * a.decay
                decayed += 1
    k2 = [round(v, 3) for v in ramp_dir(k2, a.ramp_slope, a.reverse)]
    total = sum(events.values())
    print('%s: %d runs, %d trouble events in %d spots, %d hot spots (>= %d): %s'
          % (a.track, runs, total, len(events), len(hot), a.min_events,
             ', '.join('%d:%s' % (b * 10, dict(kinds[b])) for b in hot[:12])))
    if a.dry_run:
        return 0
    zones = write_zones(path, k2, 'learned from %d runs, %d hot spots' % (runs, len(hot)))
    print('  -> %s: %d zones, max factor %.2f, %d slices decayed' % (path, zones, max(k2), decayed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
