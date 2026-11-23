"""AI6 batch: racing line + speed profile for many converted tracks at once.

    python -m tools.track_editor.ai_batch run [--tracks a b ...] [--max-games 60] [--gap 7]
    python -m tools.track_editor.ai_batch report RUN_DIR

Every track runs its own pipeline in a thread; one launcher starts all games
(race_test, test wrapper Drivers/dx11ai6z.dll) at least --gap s apart, at most
--max-games at once and only while enough memory is free. Forward races only.

Per track:
  lines   old = the shipped line (.lsp.orig, else the installed one),
          rec = the player's newest complete lap (ai_record make), as
          racer/work/<track>0.<line>.lsp for [ai] line_tag;
  stage 1 old/rec x lat, 2 seeds, a few iterations: the hard incidents of each
          variant lower its profile before them (ai_tune.add_cuts);
  mix     per sector the line with the lower time + incident cost over stage 1,
          blended over 12 slices at the joins;
  stage 2 the best of old/rec and mix x lat x lead, more iterations;
  confirm the two best (variant, iteration) profiles and the old AI (old line,
          .aispeed, no profile) on fresh seeds; the best one is written as
          <track>0.final.lsp + <track>.final.aiprofile;
  cars    final vs old AI with GT1 and 944 physics.
Nothing is installed; `install` copies the final files over the real ones.
"""

import argparse
import copy
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

import numpy as np

if __package__:
    from . import ai_data, ai_record, ai_speed, ai_tune
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ai_data
    import ai_record
    import ai_speed
    import ai_tune

GAME = ai_speed.GAME
TRACK_DIR = os.path.join(GAME, 'GameData', 'Track')
WORK = os.path.join(TRACK_DIR, 'racer', 'work')
ROOT = os.path.join(ai_data.nfs5cfg.WORK, 'reports', 'race_test', 'ai6_batch')
TRACKS = ('aquatica atlantic country dolphin empire france germany gt1 gt2 gt3 hills hometown '
          'lostcany park redrock rockypas snowy summit uk').split()
BASE = dict(brake=3.5, crest=1.2)
PENALTY_S = 4.0             # seconds per hard incident per lap in the score
CARS = {'gt1': ('gt1', 'GT1race', 'Gt1'), '944': ('944', '944coupe25', '944')}


# ------------------------------------------------------------------ launcher

def free_gb():
    class MS(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                    ('avail', ctypes.c_ulonglong), ('tp', ctypes.c_ulonglong), ('ap', ctypes.c_ulonglong),
                    ('tv', ctypes.c_ulonglong), ('av', ctypes.c_ulonglong), ('ext', ctypes.c_ulonglong)]
    m = MS()
    m.len = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.avail / 2 ** 30


class Launcher:
    """One thread starts every game: FIFO, >= gap s apart, <= max_games running."""

    def __init__(self, max_games=60, gap=7.0, min_free_gb=3.0, log=print):
        self.max_games, self.gap, self.min_free = max_games, gap, min_free_gb
        self.q = queue.Queue()
        self.running = []
        self.started = 0
        self.log = log
        self.stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def run(self, cmd, out, env):
        """Blocks until launched; -> Popen."""
        req = {'cmd': cmd, 'out': out, 'env': env, 'ev': threading.Event(), 'p': None}
        self.q.put(req)
        req['ev'].wait()
        return req['p']

    def _loop(self):
        last = 0.0
        while not self.stop:
            req = self.q.get()
            while True:
                self.running = [p for p in self.running if p.poll() is None]
                wait = last + self.gap - time.time()
                if len(self.running) < self.max_games and wait <= 0 and free_gb() >= self.min_free:
                    break
                time.sleep(max(min(wait, 1.0), 0.5))
            log = open(req['out'] + '.log', 'w')
            req['p'] = subprocess.Popen(req['cmd'], cwd=GAME, env=req['env'], stdout=log,
                                        stderr=subprocess.STDOUT)
            self.running.append(req['p'])
            self.started += 1
            last = time.time()
            req['ev'].set()


def race_cmd(name, out, seed, profile_tag=None, line_tag=None, car='993', spec=None, aispec=None,
             opponents=3):
    cmd = [sys.executable, os.path.join(ai_data.nfs5cfg.TOOLS, 'race_test', 'race_test.py'),
           '--track', name, '--car', car, '--laps', '1', '--opponents', str(opponents),
           '--seed', str(seed), '--time-scale', '4', '--seconds', '900', '--out', out]
    if spec:
        cmd += ['--spec', spec, '--aispec', aispec]
    if profile_tag is not None:
        cmd += ['--ini', 'ai.speed_profile=1', '--ini', 'ai.profile_tag=' + profile_tag]
    else:
        cmd += ['--ini', 'ai.speed_profile=0']
    if line_tag:
        cmd += ['--ini', 'ai.line_tag=' + line_tag]
    env = dict(os.environ)
    env.setdefault('RACE_TEST_DRIVER', r'Drivers\dx11ai6')
    return cmd, env


# ------------------------------------------------------------------ analysis

def sector_times(d, n, size):
    """Seconds per sector of the timed lap (lap 1) of one car; nan where unknown."""
    k = int(np.ceil(n / size))
    out = np.full(k, np.nan)
    m = d['lap'] >= 1
    if not m.any() or d['lap'].max() < 2:
        return out
    prog = np.maximum.accumulate(d['lap'] * n + d['slice'] % n)
    for j in range(k):
        a, b = n + j * size, n + min((j + 1) * size, n)
        ia, ib = np.searchsorted(prog, a), np.searchsorted(prog, b)
        if ib < len(prog) and ia < ib:
            out[j] = (d['tick'][ib] - d['tick'][ia]) / ai_data.TICKS
    return out


def evaluate(track, run_dirs, cars_expected, sector=40):
    """Races of one profile -> dict(score, laps, hard = crashes (ai_tune.crashes), missing, incidents, sect...)."""
    laps, hard, missing, inc = [], [], 0, []
    k = int(np.ceil(track.n / sector))
    sect, sect_n, sect_hard = np.zeros(k), np.zeros(k), np.zeros(k)
    for rd in run_dirs:
        path = os.path.join(rd, 'dx11z_trace.csv') if rd else None
        if not path or not os.path.isfile(path):
            missing += cars_expected
            continue
        try:
            tr = ai_data.Trace(path, track)
            # real crashes only (user 2026-10-19): light wall/car contacts cost their time, nothing more
            incs = ai_tune.crashes(tr, track)
        except Exception:          # noqa: BLE001 -- a broken trace counts as missing
            missing += cars_expected
            continue
        h = incs
        hard += h
        inc += incs
        for x in h:
            sect_hard[(x[1] % track.n) // sector] += 1
        got = 0
        for car, d in tr.cars.items():
            lt = tr.stats.get(car, {}).get('lap_times_s') or []
            if lt:
                laps.append(float(lt[0]))
                got += 1
                st = sector_times(d, track.n, sector)
                ok = ~np.isnan(st)
                sect[ok] += st[ok]
                sect_n[ok] += 1
        missing += max(cars_expected - got, 0)
    total = len(laps) + missing
    if not laps:
        score = 1e9
    else:
        med = float(np.median(laps))
        score = (sum(laps) + missing * 1.5 * med) / total + PENALTY_S * len(hard) / total
    return dict(score=score, laps=laps, hard=len(hard), missing=missing,
                hard_at=sorted(int(x[1]) for x in hard), incidents=[list(x) for x in inc],
                sect=sect, sect_n=sect_n, sect_hard=sect_hard)


# ------------------------------------------------------------------ pipeline

class TrackJob:
    def __init__(self, name, launcher, base, plan, log):
        self.name, self.L, self.plan = name, launcher, plan
        self.dir = os.path.join(base, name)
        os.makedirs(self.dir, exist_ok=True)
        self.logf = open(os.path.join(self.dir, 'log.txt'), 'a', encoding='utf-8')
        self.glog = log
        self.track = ai_data.AiTrack(TRACK_DIR, name)
        self.tracks = {}
        self.history = []
        self.state = 'start'

    def log(self, msg):
        line = '%s %-9s %s' % (time.strftime('%H:%M:%S'), self.name, msg)
        self.logf.write(line + '\n')
        self.logf.flush()
        self.glog(line)

    def save(self):
        json.dump({'state': self.state, 'history': self.history}, open(os.path.join(self.dir, 'history.json'), 'w'),
                  indent=1, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o))

    # lines
    def line_track(self, tag):
        if tag not in self.tracks:
            t = copy.copy(self.track)
            t.lsp, t.lat_lsp = dict(self.track.lsp), dict(self.track.lat_lsp)
            if not t.use_line(0, tag):
                raise RuntimeError('no line %s' % tag)
            self.tracks[tag] = t
        return self.tracks[tag]

    def prepare_lines(self):
        inst = os.path.join(WORK, '%s0.lsp' % self.name)
        old = inst + '.orig' if os.path.isfile(inst + '.orig') else inst
        shutil.copyfile(old, os.path.join(WORK, '%s0.old.lsp.tmp' % self.name))
        os.replace(os.path.join(WORK, '%s0.old.lsp.tmp' % self.name), os.path.join(WORK, '%s0.old.lsp' % self.name))
        lines = ['old']
        if self.name == 'summit':          # recorded + 910-919 of the shipped line (2026-10-18)
            shutil.copyfile(inst, os.path.join(WORK, 'summit0.rec.lsp'))
            lines.append('rec')
        else:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ai_record.make(self.name, None, None, write=True, tag='rec', direction=0)
            self.log('rec line: ' + ' | '.join(x.strip() for x in buf.getvalue().splitlines()[:2]))
            if rc == 0:
                lines.append('rec')
        return lines

    def make_mix(self, a, b, choose_b, blend=12):
        """Line `a` with the sectors where choose_b[j] taken from line `b`."""
        n, size = self.track.n, self.plan['sector']
        w = np.zeros(n)
        for j, c in enumerate(choose_b):
            if c:
                w[j * size:min((j + 1) * size, n)] = 1.0
        ring = ai_speed.is_ring(self.track)
        kern = np.ones(blend) / blend
        if ring:
            w = np.real(np.fft.ifft(np.fft.fft(w) * np.fft.fft(np.concatenate([kern, np.zeros(n - blend)]))))
            w = np.roll(w, -(blend // 2))
        else:
            w = np.convolve(w, kern, mode='same')
        w = np.clip(w, 0, 1)
        w = w * w * (3 - 2 * w)
        la, lb = self.line_track(a).lat_lsp[0], self.line_track(b).lat_lsp[0]
        lat = la * (1 - w) + lb * w
        pts = self.track.point_at(np.arange(n), lat)[:, [0, 2]]
        ai_record.write_tagged(os.path.join(WORK, '%s0.mix.lsp' % self.name), pts)
        self.tracks.pop('mix', None)

    # races
    def races(self, specs):
        """specs: [(out, seed, profile_tag, line_tag, car)] -> run dirs (None = failed)."""
        procs = []
        for out, seed, ptag, ltag, car in specs:
            model, spec, aispec = CARS.get(car, ('993', None, None))
            cmd, env = race_cmd(self.name, out, seed, ptag, ltag, model, spec, aispec)
            procs.append((out, self.L.run(cmd, out, env)))
        dirs = []
        for out, p in procs:
            try:
                p.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                p.kill()
            dirs.append(ai_tune.run_dir(out))
        return dirs

    @staticmethod
    def vtag(v):
        line, lat, lead = v
        return '%st%gl%g' % (line, lat, lead)

    def make_profile(self, v, mult, tag=None):
        line, lat, lead = v
        ai_speed.make(self.name, 0, ai_tune.fixes_of(mult), True, tag=tag or self.vtag(v), quiet=True,
                      track=self.line_track(line), lat=lat, lead=lead, **BASE)

    def stage(self, label, variants, seeds, iters, mults=None):
        mults = mults or {v: np.ones(self.track.n) for v in variants}
        results = []
        for it in range(iters):
            specs, owner = [], []
            for v in variants:
                self.make_profile(v, mults[v])
                for seed in seeds:
                    out = os.path.join(self.dir, '%s_i%d_%s_s%d' % (label, it, self.vtag(v), seed))
                    specs.append((out, seed, self.vtag(v), v[0], None))
                    owner.append(v)
            dirs = self.races(specs)
            for v in variants:
                rds = [d for d, o in zip(dirs, owner) if o == v]
                ev = evaluate(self.line_track(v[0]), rds, 4)
                fixes = ai_tune.fixes_of(mults[v])
                ai_tune.add_cuts(mults[v], [(c, sl, 'stall' if k == 'respawn' else k, kmh)
                                            for c, sl, k, kmh in ev['incidents']],
                                 0, self.plan['cut'], self.plan['before'])
                rec = dict(stage=label, iter=it, line=v[0], lat=v[1], lead=v[2], fixes=fixes,
                           score=ev['score'], laps=ev['laps'], hard=ev['hard'], missing=ev['missing'],
                           hard_at=ev['hard_at'])
                self.history.append(rec)
                results.append((v, it, fixes, ev))
                self.log('%s i%d %-14s score %6.1f  median %s  hard %d%s  [%s]' % (
                    label, it, self.vtag(v), ev['score'],
                    '%.1f' % np.median(ev['laps']) if ev['laps'] else '-', ev['hard'],
                    ' missing %d' % ev['missing'] if ev['missing'] else '',
                    ' '.join(str(s) for s in ev['hard_at'][:12])))
            self.save()
        return results, mults

    def run(self):
        try:
            self._run()
        except Exception:          # noqa: BLE001
            self.state = 'failed'
            self.log('FAILED\n' + traceback.format_exc())
            self.save()

    def _run(self):
        P = self.plan
        lines = self.prepare_lines()
        self.state = 'stage1'
        v1 = [(line, lat, P['lead1']) for line in lines for lat in P['lats1']]
        r1, m1 = self.stage('s1', v1, P['seeds1'], P['iters1'])
        best_line = min(r1, key=lambda r: r[3]['score'])[0][0]
        cand = [best_line]
        if 'rec' in lines:
            # sectors: summed time over all stage-1 runs of a line (same lats), + incidents
            acc = {}
            for v, it, fixes, ev in r1:
                a = acc.setdefault(v[0], [np.zeros_like(ev['sect']), np.zeros_like(ev['sect']), np.zeros_like(ev['sect'])])
                a[0] += ev['sect']
                a[1] += ev['sect_n']
                a[2] += ev['sect_hard']
            cost = {}
            for line, (s, cnt, h) in acc.items():
                runs = sum(1 for v, *_ in r1 if v[0] == line) * len(P['seeds1']) * 4
                cost[line] = s / np.maximum(cnt, 1) + PENALTY_S * h / max(runs, 1)
                cost[line][cnt == 0] = 1e6
            other = 'old' if best_line == 'rec' else 'rec'
            choose = cost[other] < cost[best_line] - P['mix_margin']
            if choose.any() and not choose.all():
                self.make_mix(best_line, other, choose)
                cand.append('mix')
                self.log('mix = %s with %s on sectors %s' % (best_line, other, ' '.join(
                    str(j * P['sector']) for j in np.nonzero(choose)[0])))
            else:
                self.log('no mix (%d of %d sectors better on %s)' % (int(choose.sum()), len(choose), other))
        best1 = min(r1, key=lambda r: r[3]['score'])
        lat0 = best1[0][1]
        lats2 = sorted(set([lat0, lat0 + P['lat_step']] if lat0 >= max(P['lats1']) else
                           [lat0 - P['lat_step'] / 2, lat0 + P['lat_step'] / 2]))
        self.state = 'stage2'
        v2 = [(line, lat, lead) for line in cand for lat in lats2 for lead in P['leads2']]
        # a variant of stage 1 keeps its cuts
        m2 = {v: (m1[v].copy() if v in m1 else np.ones(self.track.n)) for v in v2}
        r2, _ = self.stage('s2', v2, P['seeds2'], P['iters2'], m2)
        allr = sorted(r1 + r2, key=lambda r: r[3]['score'])
        top = []
        for r in allr:
            if all(r[0] != t[0] for t in top):
                top.append(r)
            if len(top) == 2:
                break
        # confirm on fresh seeds: the two best and the old AI
        self.state = 'confirm'
        specs, names = [], []
        for k, (v, it, fixes, ev) in enumerate(top):
            mult = np.ones(self.track.n)
            for a, b, f in fixes:
                mult[a:b + 1] = f
            self.make_profile(v, mult, tag='cand%d' % k)
            for seed in P['seeds_c']:
                specs.append((os.path.join(self.dir, 'c_cand%d_s%d' % (k, seed)), seed, 'cand%d' % k, v[0], None))
                names.append('cand%d' % k)
        for seed in P['seeds_c']:
            specs.append((os.path.join(self.dir, 'c_oldai_s%d' % seed), seed, None, 'old', None))
            names.append('oldai')
        dirs = self.races(specs)
        conf = {}
        for nm in ('cand0', 'cand1', 'oldai'):
            rds = [d for d, x in zip(dirs, names) if x == nm]
            if rds:
                line = 'old' if nm == 'oldai' else top[int(nm[-1])][0][0]
                conf[nm] = evaluate(self.line_track(line), rds, 4)
                self.log('confirm %-6s %s score %6.1f median %s hard %d%s' % (
                    nm, self.vtag(top[int(nm[-1])][0]) if nm != 'oldai' else 'old AI', conf[nm]['score'],
                    '%.1f' % np.median(conf[nm]['laps']) if conf[nm]['laps'] else '-', conf[nm]['hard'],
                    ' missing %d' % conf[nm]['missing'] if conf[nm]['missing'] else ''))
        # stage + confirm together: less luck in the pick
        def joint(k):
            return (conf['cand%d' % k]['score'] + top[k][3]['score']) / 2 if 'cand%d' % k in conf else 1e9
        k = min(range(len(top)), key=joint)
        v, it, fixes, ev = top[k]
        shutil.copyfile(os.path.join(WORK, '%s0.%s.lsp' % (self.name, v[0])), os.path.join(WORK, '%s0.final.lsp' % self.name))
        shutil.copyfile(ai_speed.profile_path(self.name, 0, tag='cand%d' % k), ai_speed.profile_path(self.name, 0, tag='final'))
        self.history.append(dict(stage='final', variant=self.vtag(v), line=v[0], lat=v[1], lead=v[2], fixes=fixes,
                                 confirm={nm: dict(score=c['score'], laps=c['laps'], hard=c['hard'], missing=c['missing'],
                                                   hard_at=c['hard_at']) for nm, c in conf.items()}))
        self.log('FINAL %s (%d fixes): confirm score %.1f vs old AI %.1f' % (
            self.vtag(v), len(fixes), conf['cand%d' % k]['score'], conf['oldai']['score'] if 'oldai' in conf else -1))
        self.save()
        # other cars
        self.state = 'cars'
        specs, names = [], []
        for car in CARS:
            for seed in P['seeds_car']:
                specs.append((os.path.join(self.dir, 'car_%s_final_s%d' % (car, seed)), seed, 'final', 'final', car))
                names.append((car, 'final'))
                specs.append((os.path.join(self.dir, 'car_%s_oldai_s%d' % (car, seed)), seed, None, 'old', car))
                names.append((car, 'oldai'))
        dirs = self.races(specs)
        cars = {}
        for car in CARS:
            for which in ('final', 'oldai'):
                rds = [d for d, x in zip(dirs, names) if x == (car, which)]
                c = evaluate(self.line_track('old' if which == 'oldai' else v[0]), rds, 4)
                cars['%s_%s' % (car, which)] = dict(score=c['score'], laps=c['laps'], hard=c['hard'],
                                                    missing=c['missing'], hard_at=c['hard_at'])
                self.log('car %-4s %-6s score %6.1f median %s hard %d%s [%s]' % (
                    car, which, c['score'], '%.1f' % np.median(c['laps']) if c['laps'] else '-', c['hard'],
                    ' missing %d' % c['missing'] if c['missing'] else '', ' '.join(str(s) for s in c['hard_at'][:12])))
        self.history.append(dict(stage='cars', results=cars))
        self.state = 'done'
        self.save()


PLAN = dict(lats1=(13.0, 16.0, 19.0), lead1=1.0, seeds1=(100, 101), iters1=3,
            leads2=(1.0, 1.5), lat_step=3.0, seeds2=(102, 103), iters2=4,
            seeds_c=(200, 201, 202, 203), seeds_car=(300, 301),
            cut=0.94, before=15, sector=40, mix_margin=0.3)


def run(tracks, max_games=60, gap=7.0, min_free=3.0, plan=None):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = os.path.join(ROOT, stamp)
    os.makedirs(base, exist_ok=True)
    glog = open(os.path.join(base, 'progress.txt'), 'a', encoding='utf-8')
    lock = threading.Lock()

    def log(line):
        with lock:
            glog.write(line + '\n')
            glog.flush()
            print(line, flush=True)

    L = Launcher(max_games, gap, min_free, log)
    p = dict(PLAN, **(plan or {}))
    json.dump(p, open(os.path.join(base, 'plan.json'), 'w'), indent=1)
    log('batch %s: %d tracks, <= %d games, %.0f s apart' % (base, len(tracks), max_games, gap))
    jobs = [TrackJob(t, L, base, p, log) for t in tracks]
    threads = [threading.Thread(target=j.run, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    last = 0
    while any(t.is_alive() for t in threads):
        time.sleep(5)
        if time.time() - last > 300:
            last = time.time()
            log('-- %d games started, %d running, %.1f GB free | %s' % (
                L.started, len([x for x in L.running if x.poll() is None]), free_gb(),
                ' '.join('%s:%s' % (j.name, j.state) for j in jobs)))
    log('batch done')
    return base


def read_params(path):
    """'# params k=v ...' of an .aiprofile -> dict of floats."""
    for line in open(path, encoding='latin1'):
        if line.startswith('# params'):
            return {k: int(float(v)) if k == 'span' else float(v) for k, v in (x.split('=') for x in line.split()[2:])}
    return {}


def refine(tracks, iters=3, seeds=(500, 501, 502, 503), max_games=60, gap=7.0, penalty=10.0, L=None,
           cars=(None,)):
    """More cut iterations on <track>.final.aiprofile (its line, params and fixes), all tracks
    at once; iteration 0 re-measures it on these seeds. The best by lap + `penalty` s per hard
    incident replaces final (the old one kept as .final.aiprofile.prev)."""
    base = os.path.join(ROOT, 'refine_' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(base, exist_ok=True)
    glog = open(os.path.join(base, 'progress.txt'), 'a', encoding='utf-8')
    lock = threading.Lock()

    def log(line):
        with lock:
            glog.write(line + '\n')
            glog.flush()
            print(line, flush=True)

    L = L or Launcher(max_games, gap, 3.0, log)

    def one(name):
        try:
            src = ai_speed.profile_path(name, 0, tag='final')
            params = read_params(src)
            fixes = ai_speed.read_fixes(src)
            track = ai_data.AiTrack(TRACK_DIR, name)
            track.use_line(0, 'final')
            mult = np.ones(track.n)
            for a, b, f in fixes:
                mult[a:b + 1] = f
            job = TrackJob.__new__(TrackJob)
            job.name, job.L = name, L
            best = None
            for it in range(iters + 1):
                tag = 'ref%d' % it
                used = ai_tune.fixes_of(mult)
                ai_speed.make(name, 0, used, True, tag=tag, quiet=True, track=track,
                              caps=ai_speed.read_caps(src), extra=ai_speed.read_extra(src),
                              **{k: v for k, v in params.items() if k in ai_speed.DEFAULTS})
                specs = [(os.path.join(base, '%s_i%d_%s_s%d' % (name, it, car or 'default', sd)), sd, tag, 'final', car)
                         for car in cars for sd in seeds]
                ev = evaluate(track, job.races(specs), 4)
                n = len(ev['laps']) + ev['missing']
                score = ev['score'] + (penalty - PENALTY_S) * ev['hard'] / max(n, 1)
                log('%-9s i%d score %6.1f median %s hard %d%s [%s]' % (
                    name, it, score, '%.1f' % np.median(ev['laps']) if ev['laps'] else '-', ev['hard'],
                    ' missing %d' % ev['missing'] if ev['missing'] else '', ' '.join(str(x) for x in ev['hard_at'][:12])))
                if best is None or score < best[1]:
                    best = (it, score)
                ai_tune.add_cuts(mult, [(c, sl, 'stall' if k == 'respawn' else k, kmh) for c, sl, k, kmh in ev['incidents']],
                                 0, 0.94, 15)
            if best[0] > 0:
                shutil.copyfile(src, src + '.prev')
                shutil.copyfile(ai_speed.profile_path(name, 0, tag='ref%d' % best[0]), src)
            log('%-9s REFINED: iteration %d kept' % (name, best[0]))
        except Exception:          # noqa: BLE001
            log('%s FAILED\n%s' % (name, traceback.format_exc()))

    threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in tracks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log('refine done')
    return base


CREST_VARIANTS = {'c12v2': dict(crest=1.2, vspan=2), 'c25v3': dict(crest=2.5, vspan=3), 'c0': dict(crest=0.0)}


def retune(tracks, variants=None, iters=4, runs=((None, 600), ('gt1', 601), ('944', 602)),
           confirm=((None, 700), (None, 701), ('gt1', 702), ('gt1', 703), ('944', 704), ('944', 705)),
           penalty=10.0, L=None, label='retune'):
    """Crest models x cut iterations on <track>.final.aiprofile (its line, params, fixes), with
    the default car, GT1 and 944 (per-car scales: RACE_TEST_DRIVER = a wrapper with them). Score
    = lap + `penalty` s per real crash (ai_tune.crashes). The best (variant, iteration) is raced
    against the current profile (c12v2 iteration 0) on the `confirm` runs; it replaces final
    (old one kept as .final.aiprofile.pre_<label>) when the mean of both scores is lower."""
    variants = variants or CREST_VARIANTS
    base = os.path.join(ROOT, '%s_%s' % (label, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(base, exist_ok=True)
    glog = open(os.path.join(base, 'progress.txt'), 'a', encoding='utf-8')
    lock = threading.Lock()
    summary = {}

    def log(line):
        with lock:
            glog.write(line + '\n')
            glog.flush()
            print(line, flush=True)

    L = L or Launcher(60, 7.0, 3.0, log)

    def score_of(ev):
        n = len(ev['laps']) + ev['missing']
        return ev['score'] + (penalty - PENALTY_S) * ev['hard'] / max(n, 1)

    def one(name):
        try:
            src = ai_speed.profile_path(name, 0, tag='final')
            params = {k: v for k, v in read_params(src).items() if k in ai_speed.DEFAULTS}
            fixes0 = ai_speed.read_fixes(src)
            track = ai_data.AiTrack(TRACK_DIR, name)
            track.use_line(0, 'final')
            mult0 = np.ones(track.n)
            for a, b, f in fixes0:
                mult0[a:b + 1] = f
            mults = {v: mult0.copy() for v in variants}
            job = TrackJob.__new__(TrackJob)
            job.name, job.L = name, L
            hist = []
            for it in range(iters):
                specs, owner = [], []
                for v, over in variants.items():
                    tag = 'rt_%s_i%d' % (v, it)
                    ai_speed.make(name, 0, ai_tune.fixes_of(mults[v]), True, tag=tag, quiet=True, track=track,
                                  **dict(params, **over))
                    for car, seed in runs:
                        specs.append((os.path.join(base, '%s_%s_i%d_%s_s%d' % (name, v, it, car or 'default', seed)),
                                      seed, tag, 'final', car))
                        owner.append(v)
                dirs = job.races(specs)
                for v in variants:
                    ev = evaluate(track, [d for d, o in zip(dirs, owner) if o == v], 4)
                    sc = score_of(ev)
                    hist.append((sc, v, it, ev))
                    log('%-9s %-6s i%d score %6.1f median %s crashes %d%s [%s]' % (
                        name, v, it, sc, '%.1f' % np.median(ev['laps']) if ev['laps'] else '-', ev['hard'],
                        ' missing %d' % ev['missing'] if ev['missing'] else '',
                        ' '.join('%s@%d' % (x[2][0], x[1]) for x in ev['incidents'][:10])))
                    ai_tune.add_cuts(mults[v], [(c, sl, 'stall' if k == 'respawn' else k, kmh)
                                                for c, sl, k, kmh in ev['incidents']], 0, 0.94, 15)
            cur = next(h for h in hist if h[1] == 'c12v2' and h[2] == 0)
            best = min(hist, key=lambda h: h[0])
            pick = cur
            if best is not cur:
                cspecs, cown = [], []
                for which, (sc, v, it, ev) in (('cur', cur), ('new', best)):
                    tag = 'rt_%s_i%d' % (v, it)
                    for car, seed in confirm:
                        cspecs.append((os.path.join(base, '%s_c_%s_%s_s%d' % (name, which, car or 'default', seed)),
                                       seed, tag, 'final', car))
                        cown.append(which)
                dirs = job.races(cspecs)
                conf = {w: evaluate(track, [d for d, o in zip(dirs, cown) if o == w], 4) for w in ('cur', 'new')}
                j = {w: (score_of(conf[w]) + h[0]) / 2 for w, h in (('cur', cur), ('new', best))}
                for w in ('cur', 'new'):
                    log('%-9s confirm %s (%s i%d) score %6.1f median %s crashes %d -> joint %.1f' % (
                        name, w, (cur if w == 'cur' else best)[1], (cur if w == 'cur' else best)[2], score_of(conf[w]),
                        '%.1f' % np.median(conf[w]['laps']) if conf[w]['laps'] else '-', conf[w]['hard'], j[w]))
                if j['new'] < j['cur']:
                    pick = best
                summary[name] = {w: dict(score=score_of(conf[w]), laps=conf[w]['laps'], crashes=conf[w]['hard'],
                                         missing=conf[w]['missing']) for w in conf}
            if pick is not cur:
                shutil.copyfile(src, src + '.pre_' + label)
                shutil.copyfile(ai_speed.profile_path(name, 0, tag='rt_%s_i%d' % (pick[1], pick[2])), src)
            summary.setdefault(name, {})['pick'] = '%s i%d' % (pick[1], pick[2])
            log('%-9s PICK %s i%d%s' % (name, pick[1], pick[2], '' if pick is not cur else ' (current kept)'))
        except Exception:          # noqa: BLE001
            log('%s FAILED\n%s' % (name, traceback.format_exc()))

    threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in tracks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    json.dump(summary, open(os.path.join(base, 'summary.json'), 'w'), indent=1)
    log('retune done')
    return base


def install(base, tracks=None):
    """final files over the real line and profile (first install keeps .orig)."""
    for name in tracks or TRACKS:
        lsp = os.path.join(WORK, '%s0.final.lsp' % name)
        prof = ai_speed.profile_path(name, 0, tag='final')
        if not (os.path.isfile(lsp) and os.path.isfile(prof)):
            print('%s: no final files' % name)
            continue
        ai_record.write_lsp(os.path.join(WORK, '%s0.lsp' % name), ai_data.read_lsp(lsp))
        dst = ai_speed.profile_path(name, 0)
        shutil.copyfile(prof, dst + '.tmp')
        os.replace(dst + '.tmp', dst)
        print('%s: installed line + profile' % name)


def report(base):
    """Markdown table of every track's history.json -> base/report.md (and printed)."""
    def med(x):
        return '%.1f' % float(np.median(x['laps'])) if x and x.get('laps') else '-'

    rows = ['| track | pick | old AI lap / hard | new lap / hard | GT1 old / new | 944 old / new | state |',
            '|---|---|---|---|---|---|---|']
    tot = dict(old_hard=0, new_hard=0, gain=[])
    for name in sorted(os.listdir(base)):
        hp = os.path.join(base, name, 'history.json')
        if not os.path.isfile(hp):
            continue
        h = json.load(open(hp))
        fin = next((x for x in h['history'] if x.get('stage') == 'final'), None)
        cars = next((x for x in h['history'] if x.get('stage') == 'cars'), {}).get('results', {})
        if not fin:
            rows.append('| %s | - | - | - | - | - | %s |' % (name, h['state']))
            continue
        c = fin['confirm']
        old = c.get('oldai')
        # the picked candidate: log.txt says which variant each candN was

        names = dict(re.findall(r'confirm (cand\d)\s+(\S+)', open(os.path.join(base, name, 'log.txt'),
                                                                  encoding='utf-8').read()))
        best = next((k for k, v in names.items() if v == fin['variant'] and k in c), 'cand0')
        new = c[best]
        if old and old['laps'] and new['laps']:
            tot['gain'].append(float(np.median(new['laps'])) / float(np.median(old['laps'])) - 1)
        tot['old_hard'] += old['hard'] if old else 0
        tot['new_hard'] += new['hard']

        def pair(car):
            a, b = cars.get('%s_oldai' % car), cars.get('%s_final' % car)
            return '%s / %s (%s / %s)' % (med(a), med(b), a['hard'] if a else '-', b['hard'] if b else '-')
        rows.append('| %s | %s, %d fixes | %s / %d | %s / %d | %s | %s | %s |' % (
            name, fin['variant'], len(fin['fixes']), med(old), old['hard'] if old else -1, med(new), new['hard'],
            pair('gt1'), pair('944'), h['state']))
    if tot['gain']:
        rows.append('')
        rows.append('Median lap change new vs old AI: %+.1f %% (mean over %d tracks); hard incidents %d -> %d '
                    '(4 cars x 4 seeds per track).' % (100 * float(np.mean(tot['gain'])), len(tot['gain']),
                                                       tot['old_hard'], tot['new_hard']))
    text = '\n'.join(rows) + '\n'
    open(os.path.join(base, 'report.md'), 'w', encoding='utf-8').write(text)
    print(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--tracks', nargs='+', default=list(TRACKS))
    r.add_argument('--max-games', type=int, default=60)
    r.add_argument('--gap', type=float, default=7.0)
    r.add_argument('--min-free-gb', type=float, default=3.0)
    r.add_argument('--plan', default='{}', help='JSON overrides of PLAN')
    rf = sub.add_parser('refine')
    rf.add_argument('--tracks', nargs='+', required=True)
    rf.add_argument('--iters', type=int, default=3)
    rp = sub.add_parser('report')
    rp.add_argument('base')
    i = sub.add_parser('install')
    i.add_argument('base')
    i.add_argument('--tracks', nargs='+')
    a = ap.parse_args(argv)
    if a.cmd == 'run':
        if a.gap < 3.0:
            ap.error('--gap below 3 s (user rule 2026-10-19: >= 3 s between game starts)')
        run(a.tracks, a.max_games, a.gap, a.min_free_gb, json.loads(a.plan))
    elif a.cmd == 'refine':
        refine(a.tracks, a.iters)
    elif a.cmd == 'report':
        report(a.base)
    else:
        install(a.base, a.tracks)
    return 0


if __name__ == '__main__':
    sys.exit(main())
