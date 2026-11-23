"""AI6 step 2: what a race_test run says about a speed profile.

    python -m tools.track_editor.ai_tune check summit RUN_DIR [--scale 1.1]
    python -m tools.track_editor.ai_tune loop summit --lat 10 11.5 13 [--iters 4] [--install]

`loop`: every iteration races each --lat once in parallel (race_test, one
seed, one lap, test wrapper Drivers/dx11ai6z.dll via RACE_TEST_DRIVER by default) with
its own <track>.t<lat>.aiprofile ([ai] profile_tag), then lowers the corner
speed before every hard incident (wall/object contact above 40 km/h, spin,
stall) by --cut over --before slices up to 2 past it and regenerates.
--install copies the best one (fewest incidents, then lap time) to
<track>[_r].aiprofile.

Per car: lap time, how closely the target speed (+EB8) followed the profile
(proof the wrapper used it), how far the car stayed below the target, and the
incidents: wall/object contacts above 40 km/h, spins, line misses (> 4 m off
the line) and stalls, each with the slice where the trouble started.
"""

import argparse
import json
import os
import sys

import numpy as np

if __package__:
    from . import ai_data, ai_speed
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ai_data
    import ai_speed

GAME = ai_speed.GAME


def incidents(tr, track, direction, min_kmh=40.0, miss_m=4.0):
    """[(car, slice, kind, kmh)] for laps >= 1 (lap 0 = start crowd)."""
    out = []
    line = track.lat_lsp[direction]
    for e in tr.events:
        d0 = tr.cars.get(e.get('car'))
        if d0 is not None and d0['lap'][min(np.searchsorted(d0['tick'], e.get('tick', 0)), len(d0['tick']) - 1)] < 1:
            continue
        if e.get('type') in ('wall', 'spin', 'stall') or str(e.get('type', '')).startswith('hit'):
            car = e.get('car')
            d = tr.cars.get(car)
            if d is None:
                continue
            row = np.searchsorted(d['tick'], e.get('tick', 0))
            row = min(row, len(d['tick']) - 1)
            kmh = e.get('kmh', d['kmh'][row])
            if e['type'] != 'stall' and kmh is not None and kmh < min_kmh:
                continue
            out.append((car, int(e['slice']), e['type'], float(kmh or 0)))
    for car, d in tr.cars.items():
        off = np.abs(d['lateral'] - line[d['slice'] % track.n])
        bad = (off > miss_m) & (d['kmh'] > min_kmh) & (d['lap'] >= 1)
        start = bad.copy()
        start[1:] &= ~bad[:-1]
        last = -10 ** 6
        for i in np.nonzero(start)[0]:
            if d['tick'][i] - last > 3 * ai_data.TICKS:          # one episode per 3 s
                out.append((car, int(d['slice'][i]), 'miss', float(d['kmh'][i])))
            last = d['tick'][i]
    return sorted(out, key=lambda x: (x[1], x[0]))


def crashes(tr, track, drop_kmh=40.0, drop_frac=0.3, window_s=1.5):
    """Real crashes only (user 2026-10-19: lap time first, light contacts are fine):
    [(car, slice, kind, kmh)] with kind rollover (up_y < 0.5), spin, stall, respawn, or
    heavy (a wall/object contact followed by >= drop_kmh and >= drop_frac speed loss within
    window_s). Laps >= 1 except respawns (any lap). One per car per 3 s."""
    found = []                                  # (car, tick, slice, kind, kmh)
    win = int(window_s * ai_data.TICKS)
    for car, d in tr.cars.items():
        on_side = (d['up_y'] < 0.5) & (d['lap'] >= 1)
        start = on_side.copy()
        start[1:] &= ~on_side[:-1]
        for i in np.nonzero(start)[0]:
            found.append((car, int(d['tick'][i]), int(d['slice'][i]), 'rollover', float(d['kmh'][max(i - 8, 0)])))
    for e in tr.events:
        car, kind = e.get('car'), str(e.get('type', ''))
        d = tr.cars.get(car)
        if d is None:
            continue
        tick = int(e.get('tick', 0))
        i = min(int(np.searchsorted(d['tick'], tick)), len(d['tick']) - 1)
        sl = int(e.get('slice', d['slice'][i]))
        if kind == 'respawn':
            found.append((car, tick, sl, 'respawn', 0.0))
        elif d['lap'][i] < 1:
            continue
        elif kind in ('spin', 'stall'):
            found.append((car, tick, sl, kind, float(d['kmh'][i])))
        elif kind == 'wall' or kind.startswith('hit'):
            v0 = float(d['kmh'][max(i - 1, 0)])
            j = int(np.searchsorted(d['tick'], tick + win))
            vmin = float(d['kmh'][i:max(j, i + 1)].min())
            if v0 - vmin >= drop_kmh and v0 - vmin >= drop_frac * v0:
                found.append((car, tick, sl, 'heavy', v0))
    found.sort()
    out, last = [], {}
    for car, tick, sl, kind, kmh in found:
        if kind != 'respawn' and car in last and tick - last[car] < 3 * ai_data.TICKS:
            continue
        last[car] = tick
        out.append((car, sl, kind, kmh))
    return sorted(out, key=lambda x: (x[1], x[0]))


def check(name, run, scale=1.0, game=GAME, quiet=False, tag=''):
    track = ai_data.AiTrack(os.path.join(game, 'GameData', 'Track'), name)
    path = run if run.endswith('.csv') else os.path.join(run, 'dx11z_trace.csv')
    tr = ai_data.Trace(path, track)
    direction = 1 if tr.reverse else 0
    prof = ai_speed.read_profile(ai_speed.profile_path(name, direction, game, tag))
    pv = np.array([prof.get(i, 0.0) for i in range(track.n)]) * scale
    res = {'cars': {}, 'incidents': incidents(tr, track, direction)}
    for car, d in sorted(tr.cars.items()):
        m = (d['lap'] >= 1) & (d['lap'] < d['lap'].max())     # timed laps, not the cool-down
        want = pv[d['slice'][m] % track.n]
        got = d['target_kmh'][m]
        follow = float(np.mean(np.abs(got - want) < 2.0)) if m.any() else 0.0
        below = float(np.mean(want - d['kmh'][m])) if m.any() else 0.0
        laps = tr.stats.get(car, {}).get('lap_times_s') or []
        res['cars'][car] = {'laps': laps, 'follow': follow, 'below_kmh': below}
        if not quiet:
            print('car %d: laps %s | target = profile %.0f %% | car below target %.0f km/h on average'
                  % (car, ' '.join('%.1f' % x for x in laps) or '-', follow * 100, below))
    if not quiet:
        for car, s, kind, kmh in res['incidents']:
            print('  car %d slice %d: %s at %.0f km/h' % (car, s, kind, kmh))
    return res


ROOT = os.path.join(ai_data.nfs5cfg.WORK, 'reports', 'race_test', 'ai6_profile')
HARD = ('wall', 'spin', 'stall')


def add_cuts(mult, incs, direction, cut, before, floor=0.6):
    """Per-slice factor array: each incident given (but line misses / car contacts) lowers
    [s-before, s+2] once per run."""
    n = len(mult)
    hit = np.zeros(n, bool)
    for _car, s, kind, _kmh in incs:
        if kind not in ('miss', 'car'):
            span = range(s - before, s + 3) if direction == 0 else range(s - 2, s + before + 1)
            for i in span:
                hit[i % n] = True
    mult[hit] = np.maximum(mult[hit] * cut, floor)
    return int(hit.sum())


def fixes_of(mult):
    out, i, n = [], 0, len(mult)
    while i < n:
        j = i
        while j + 1 < n and abs(mult[j + 1] - mult[i]) < 1e-6:
            j += 1
        if abs(mult[i] - 1.0) > 1e-6:
            out.append((i, j, float(mult[i])))
        i = j + 1
    return out


def race(name, tag, seed, out, laps=1, opponents=3, car='993', time_scale=4, extra=()):
    import subprocess
    env = dict(os.environ)
    env.setdefault('RACE_TEST_DRIVER', r'Drivers\dx11ai6')
    cmd = [sys.executable, os.path.join(ai_data.nfs5cfg.TOOLS, 'race_test', 'race_test.py'),
           '--track', name, '--car', car, '--laps', str(laps), '--opponents', str(opponents),
           '--seed', str(seed), '--time-scale', str(time_scale), '--seconds', '900',
           '--ini', 'ai.speed_profile=1', '--ini', 'ai.profile_tag=' + tag, '--out', out] + list(extra)
    log = open(out + '.log', 'w')
    return subprocess.Popen(cmd, cwd=GAME, env=env, stdout=log, stderr=subprocess.STDOUT)


def run_dir(out):
    import glob
    runs = sorted(glob.glob(os.path.join(out, '*_batch', '0*')))
    return runs[-1] if runs else None


def loop(name, lats, iters=4, seeds=(100,), cut=0.94, before=15, direction=0, install=False,
         stagger=10.0, crests=(None,), brakes=(None,), leads=(None,), **kw):
    """Each iteration: every lat x seed raced at once (games started `stagger` s
    apart); the hard incidents of all seeds of a lat cut that lat's profile once."""
    import time as _t
    track = ai_data.AiTrack(os.path.join(GAME, 'GameData', 'Track'), name)
    n = track.n
    stamp = _t.strftime('%Y%m%d-%H%M%S')
    base = os.path.join(ROOT, '%s_%s' % (stamp, name))
    variants = [(lat, crest, brake, lead) for lat in lats for crest in crests for brake in brakes
                for lead in leads]

    def tag_of(v):
        return 't%g' % v[0] + ''.join('%s%g' % (c, x) for c, x in zip('cbl', v[1:]) if x is not None)

    def params_of(v):
        p = dict(kw, lat=v[0])
        for key, x in zip(('crest', 'brake', 'lead'), v[1:]):
            if x is not None:
                p[key] = x
        return p

    mults = {v: np.ones(n) for v in variants}
    history = []
    for it in range(iters):
        procs = []
        for v in variants:
            ai_speed.make(name, direction, fixes_of(mults[v]), True, tag=tag_of(v), quiet=True, **params_of(v))
        os.makedirs(base, exist_ok=True)
        for v in variants:
            for seed in seeds:
                out = os.path.join(base, 'i%d_%s_s%d' % (it, tag_of(v), seed))
                procs.append((v, seed, race(name, tag_of(v), seed, out), out))
                _t.sleep(stagger)
        for _v, _seed, p, _out in procs:
            p.wait()
        print('iteration %d' % it, flush=True)
        for v in variants:
            fixes = fixes_of(mults[v])
            incs, laps, hard, missing = [], [], [], 0
            for v2, seed, _p, out in procs:
                if v2 != v:
                    continue
                rd = run_dir(out)
                if not rd or not os.path.isfile(os.path.join(rd, 'dx11z_trace.csv')):
                    missing += 1
                    continue
                res = check(name, rd, 1.0, quiet=True, tag=tag_of(v))
                incs += res['incidents']
                hard += [x for x in res['incidents'] if x[2] in HARD or x[2].startswith('hit')]
                laps += [c['laps'][0] for c in res['cars'].values() if c['laps']]
            cut_n = add_cuts(mults[v], incs, direction, cut, before)
            history.append({'iter': it, 'lat': v[0], 'crest': v[1], 'brake': v[2], 'lead': v[3], 'hard': len(hard), 'laps': laps,
                            'fixes': fixes, 'missing': missing})
            print('  %-10s median lap %s (%d cars) | hard incidents %d (%s) | cut %d slices, min factor %.2f%s' % (
                tag_of(v), '%.1f' % float(np.median(laps)) if laps else '-', len(laps), len(hard),
                ' '.join('%s@%d' % (k, s) for _c, s, k, _v in sorted(hard, key=lambda x: x[1])[:14]),
                cut_n, mults[v].min(), ' | %d runs missing' % missing if missing else ''), flush=True)
        json.dump(history, open(os.path.join(base, 'history.json'), 'w'), indent=1)
    last = [h for h in history if h['iter'] == iters - 1 and h['laps']]
    if install and last:
        best = min(last, key=lambda h: (h['hard'], float(np.median(h['laps']))))
        # the profile the best runs raced with (not the cuts made after them)
        v = (best['lat'], best['crest'], best['brake'], best['lead'])
        ai_speed.make(name, direction, best['fixes'], True, **params_of(v))
        print('installed %s (%d hard incidents, median lap %.1f s)' % (
            tag_of(v), best['hard'], float(np.median(best['laps']))))
    return history


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('track')
    c.add_argument('run')
    c.add_argument('--scale', type=float, default=1.0)
    lp = sub.add_parser('loop')
    lp.add_argument('track')
    lp.add_argument('--lat', type=float, nargs='+', required=True)
    lp.add_argument('--iters', type=int, default=4)
    lp.add_argument('--seeds', type=int, nargs='+', default=[100])
    lp.add_argument('--stagger', type=float, default=10.0, help='seconds between game launches')
    lp.add_argument('--cut', type=float, default=0.94)
    lp.add_argument('--before', type=int, default=15)
    lp.add_argument('--crest', type=float, nargs='+', default=[None])
    lp.add_argument('--brake', type=float, nargs='+', default=[None])
    lp.add_argument('--lead', type=float, nargs='+', default=[None])
    lp.add_argument('--install', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd == 'check':
        check(a.track.lower(), a.run, a.scale)
        return 0
    kw = {}
    loop(a.track.lower(), a.lat, a.iters, a.seeds, a.cut, a.before, install=a.install,
         stagger=a.stagger, crests=a.crest, brakes=a.brake, leads=a.lead, **kw)
    return 0


if __name__ == '__main__':
    sys.exit(main())
