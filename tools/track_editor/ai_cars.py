"""AI6: per-car scale of the speed profiles (GameData/Track/aiprofile_cars.txt).

    python -m tools.track_editor.ai_cars calibrate [--scales 0.78 0.85 0.92 1.0] [--tracks ...]
    python -m tools.track_editor.ai_cars check RUN_DIR_OF_A_BATCH [--cars gt1 944]

The profiles (ai_batch, <track>0.final.lsp + <track>.final.aiprofile) are tuned
with default.sim / default.ais. `calibrate` races every car physics of the game
with the profiles x each --scale ([ai] profile_scale, all cars in a race the same)
and writes the scale with the best summed score (lap + 10 s per hard incident) per .ais to aiprofile_cars.txt. `check` races the cars with
that file in place against the old AI on every track.
"""

import argparse
import json
import os
import sys
import threading
import time

import numpy as np

if __package__:
    from . import ai_batch, ai_data
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ai_batch
    import ai_data

GAME = ai_batch.GAME
CARS_FILE = os.path.join(ai_batch.TRACK_DIR, 'aiprofile_cars.txt')
DRIVER = r'Drivers\dx11ai8'
# .ais -> (CarModel, .sim): one physics per AI tuning file (918, CarreraGT, Gt4: unfinished additions, left out)
CARS = {
    '356': ('356_1', '356coupe15'), '356A': ('356a', '356Acoupe16'), '356B': ('356b', '356Bcoupe16'),
    '550a': ('550', '550Arace'), '901': ('901', '901coupe20'), '914': ('914', '914coupe20'),
    '928': ('928', '928coupeGTS54'), '930t': ('930', '930Tcoupe33'), '935': ('935', '935race'),
    '944': ('944', '944coupe25'), '959': ('959', '959coupe29'), '964': ('964', '964coupe36'),
    '965t': ('964', '965Tcoupe36'), '993': ('993', '993coupe36'), '993t': ('993', '993Tcoupe36'),
    '996': ('996', '996coupe34'), '996t': ('996', '996Tcoupe36'),
    'Gt1': ('gt1', 'GT1race'), 'Gt2': ('gt2', 'GT2race'), 'Gt3': ('gt3', 'GT3race'),
    'boxster': ('boxster', 'boxster25'),
    'default': ('993', 'default'),       # the tuning car itself (scale 1, not calibrated)
}
TRACKS = ('gt2', 'uk', 'france', 'rockypas', 'summit')


def cmd_of(track, out, seed, ais, scale=None, final=True, keep_log=False):
    model, sim = CARS[ais]
    cmd, env = ai_batch.race_cmd(track, out, seed, 'final' if final else None, 'final' if final else 'old',
                                 model, sim, ais)
    if scale is not None:
        cmd += ['--ini', 'ai.profile_scale=%g' % scale]
    if keep_log:
        cmd += ['--keep-log']
    env['RACE_TEST_DRIVER'] = DRIVER
    return cmd, env


def run_all(L, jobs):
    """jobs: [(key, track, cmd, env, out)] -> {key: [(track, run_dir)]} (launched in order)."""
    procs = []
    for key, track, cmd, env, out in jobs:
        procs.append((key, track, out, L.run(cmd, out, env)))
    res = {}
    for key, track, out, p in procs:
        try:
            p.wait(timeout=1800)
        except Exception:          # noqa: BLE001
            p.kill()
        res.setdefault(key, []).append((track, ai_batch.ai_tune.run_dir(out)))
    return res


def tracks_of(names):
    out = {}
    for t in names:
        tr = ai_data.AiTrack(ai_batch.TRACK_DIR, t)
        tr.use_line(0, 'final')
        old = ai_data.AiTrack(ai_batch.TRACK_DIR, t)
        old.use_line(0, 'old')
        out[t] = (tr, old)
    return out


def read_cars_file(path):
    out = {}
    if os.path.isfile(path):
        for line in open(path, encoding='latin1'):
            p = line.split()
            if len(p) >= 2 and not p[0].startswith('#'):
                out[p[0]] = float(p[1])
    return out


def calibrate(scales, tracks, seeds=(400,), max_games=60, gap=7.0, cars=None, L=None):
    """`cars`: only these .ais; the other lines of the existing file are kept. `scales` and
    `tracks` may be dicts .ais -> list (a grid per car)."""
    def per(x, ais):
        return x[ais] if isinstance(x, dict) else x
    old_file = CARS_FILE if os.path.isfile(CARS_FILE) else CARS_FILE + '.prev'
    keep = read_cars_file(old_file)
    notes = []
    if os.path.isfile(old_file):
        notes = [x for x in open(old_file, encoding='latin1').read().split('\n')[2:] if x.startswith('#')]
    if not cars:
        keep = {}
    cars = list(cars or CARS)
    if os.path.isfile(CARS_FILE):
        os.replace(CARS_FILE, CARS_FILE + '.prev')     # calibration = every car x1, scale from the INI
    base = os.path.join(ai_batch.ROOT, 'cars_' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(base, exist_ok=True)
    log = open(os.path.join(base, 'log.txt'), 'a', encoding='utf-8')

    def say(s):
        log.write(s + '\n')
        log.flush()
        print(s, flush=True)

    L = L or ai_batch.Launcher(max_games, gap, 3.0, say)
    jobs = []
    for seed in seeds:
        for ais in cars:
            for t in per(tracks, ais):
                for k in per(scales, ais):
                    out = os.path.join(base, '%s_%s_x%g_s%d' % (t, ais, k, seed))
                    cmd, env = cmd_of(t, out, seed, ais, k)
                    jobs.append(((ais, k), t, cmd, env, out))
    say('calibrate: %d races (%d cars, %d seeds)' % (len(jobs), len(cars), len(seeds)))
    res = run_all(L, jobs)
    trk = tracks_of(sorted({t for ais in cars for t in per(tracks, ais)}))
    table, best = {}, {}
    for (ais, k), runs in sorted(res.items()):
        tot, hard, laps = 0.0, 0, []
        for t, rd in runs:
            ev = ai_batch.evaluate(trk[t][0], [rd], 4)
            # lap time first, + 10 s per real crash (ai_tune.crashes; user 2026-10-19)
            n = len(ev['laps']) + ev['missing']
            tot += (ev['score'] + 6.0 * ev['hard'] / max(n, 1)) if ev['score'] < 1e8 else 1000.0
            hard += ev['hard']
            laps += ev['laps']
        table[(ais, k)] = (tot, hard, len(laps))
        say('%-10s x%-5g score %7.1f hard %3d laps %d' % (ais, k, tot, hard, len(laps)))
    best = dict(keep)
    for ais in cars:
        k = min(per(scales, ais), key=lambda x: table.get((ais, x), (1e9,))[0])
        best[ais] = k
    with open(CARS_FILE + '.tmp', 'w', encoding='latin1', newline='\n') as f:
        f.write('# AI6 per-car scale of the speed profiles (ai_cars.py calibrate, %s)\n' % time.strftime('%Y-%m-%d'))
        f.write('# <.ais name> <scale>: the profiles are tuned with default.sim/default.ais\n')
        for line in notes:
            f.write(line + '\n')
        f.write('* %g\n' % best.pop('*', 1.0))
        for ais, k in sorted(best.items()):
            f.write('%s %g\n' % (ais, k))
    os.replace(CARS_FILE + '.tmp', CARS_FILE)
    json.dump({'%s x%g' % key: v for key, v in table.items()}, open(os.path.join(base, 'table.json'), 'w'), indent=1)
    say('wrote %s: %s' % (CARS_FILE, ' '.join('%s=%g' % kv for kv in sorted(best.items()))))
    return base


def check(cars, tracks, seeds=(300, 301), max_games=60, gap=7.0, batch=None):
    """`batch`: an ai_batch run dir; its car_<gt1|944>_oldai_s<seed> races (same seeds,
    deterministic) stand in for the old-AI runs."""
    batch_car = {'Gt1': 'gt1', '944': '944'}
    base = os.path.join(ai_batch.ROOT, 'check_' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(base, exist_ok=True)
    log = open(os.path.join(base, 'log.txt'), 'a', encoding='utf-8')

    def say(s):
        log.write(s + '\n')
        log.flush()
        print(s, flush=True)

    L = ai_batch.Launcher(max_games, gap, 3.0, say)
    jobs = []
    for t in tracks:
        for ais in cars:
            for seed in seeds:
                for final in (True, False):
                    if not final and batch and ais in batch_car and                             ai_batch.ai_tune.run_dir(os.path.join(batch, t, 'car_%s_oldai_s%d' % (batch_car[ais], seed))):
                        continue
                    out = os.path.join(base, '%s_%s_%s_s%d' % (t, ais, 'final' if final else 'oldai', seed))
                    cmd, env = cmd_of(t, out, seed, ais, None, final, keep_log=final and seed == seeds[0])
                    jobs.append(((t, ais, final), t, cmd, env, out))
    say('check: %d races' % len(jobs))
    res = run_all(L, jobs)
    if batch:
        for t in tracks:
            for ais in cars:
                for seed in seeds:
                    rd = ai_batch.ai_tune.run_dir(os.path.join(batch, t, 'car_%s_oldai_s%d' % (batch_car.get(ais, '-'), seed)))
                    if rd:
                        res.setdefault((t, ais, False), []).append((t, rd))
    trk = tracks_of(tracks)
    rows = ['| track | car | old AI lap / hard | new lap / hard |', '|---|---|---|---|']
    summary = {}
    for t in tracks:
        for ais in cars:
            ev = {}
            for final in (True, False):
                rds = [rd for _t, rd in res.get((t, ais, final), [])]
                ev[final] = ai_batch.evaluate(trk[t][0] if final else trk[t][1], rds, 4)
            med = {f: float(np.median(e['laps'])) if e['laps'] else float('nan') for f, e in ev.items()}
            summary['%s %s' % (t, ais)] = dict(old=dict(lap=med[False], hard=ev[False]['hard'], missing=ev[False]['missing']),
                                               new=dict(lap=med[True], hard=ev[True]['hard'], missing=ev[True]['missing'],
                                                        hard_at=ev[True]['hard_at']))
            rows.append('| %s | %s | %.1f / %d%s | %.1f / %d%s |' % (
                t, ais, med[False], ev[False]['hard'], ' (%d unfinished)' % ev[False]['missing'] if ev[False]['missing'] else '',
                med[True], ev[True]['hard'], ' (%d unfinished)' % ev[True]['missing'] if ev[True]['missing'] else ''))
            say(rows[-1])
    open(os.path.join(base, 'report.md'), 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
    json.dump(summary, open(os.path.join(base, 'summary.json'), 'w'), indent=1)
    return base


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('calibrate')
    c.add_argument('--scales', type=float, nargs='+', default=[0.78, 0.85, 0.92, 1.0])
    c.add_argument('--tracks', nargs='+', default=list(TRACKS))
    c.add_argument('--seeds', type=int, nargs='+', default=[400])
    k = sub.add_parser('check')
    k.add_argument('--cars', nargs='+', default=['Gt1', '944'])
    k.add_argument('--tracks', nargs='+', default=list(ai_batch.TRACKS))
    k.add_argument('--seeds', type=int, nargs='+', default=[300, 301])
    k.add_argument('--batch', help='ai_batch run dir: reuse its old-AI races of GT1/944')
    a = ap.parse_args(argv)
    if a.cmd == 'calibrate':
        calibrate(a.scales, a.tracks, tuple(a.seeds))
    else:
        check(a.cars, a.tracks, tuple(a.seeds), batch=a.batch)
    return 0


if __name__ == '__main__':
    sys.exit(main())
