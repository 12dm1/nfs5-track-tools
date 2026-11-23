"""AI6 step 1: what the AI drives on a converted track, read-only.

Track side (all in game space, X/Z ground plane, slice = SimD index = NFS4
vroad index on converted tracks):
  * vroad centre / right / lane from the NFS4 source tr.frd (convert_track.HS);
  * the installed racing lines GameData/Track/racer/work/<track>{0,1}.lsp;
  * the walls the wrapper collides with, GameData/Track/<track>.hsw
    (HSW1 corridor edges + HSX1 object edges);
  * the speed zones / caps GameData/Track/<track>[_r].aispeed.
Run side: a race_test dx11z_trace.csv (race_trace.cpp S/H/R rows) with the
events race_test derives from it (walls, car hits, respawns, stalls) and the
spins learn_zones counts (slip > 60 deg above 30 km/h).
"""

import glob
import json
import math
import os
import re
import struct
import sys
import threading

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.normpath(os.path.join(_HERE, '..'))
for sub in ('track', 'race_test'):
    p = os.path.join(_TOOLS, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import nfs5cfg  # noqa: E402  (tools/track is on the path now)
REPORTS = os.path.join(nfs5cfg.WORK, 'reports', 'race_test')
RECORDS = os.path.join(nfs5cfg.WORK, 'reports', 'line_record')   # ai_record.collect
TICKS = 64.0

# S row columns after the 'S' tag (race_trace.cpp Before())
S_COLS = ('tick', 'car', 'slice', 'x', 'y', 'z', 'kmh', 'stopped', 'surface', 'up_y',
          'fx', 'fz', 'vx', 'vz', 'throttle', 'brake', 'steer', 'target')


def game_root_of(track_dir):
    """GameData/Track -> the game folder."""
    return os.path.normpath(os.path.join(track_dir, '..', '..'))


def hs_source(name):
    """Installed track name -> NFS4 tr.frd path, or None (stock PU track)."""
    try:
        from convert_track import HS, track_directory
    except Exception:          # noqa: BLE001 -- no HS install: lines and walls only
        return None
    if not os.path.isdir(HS):
        return None
    for folder in os.listdir(HS):
        if track_directory(folder) == name.lower():
            path = os.path.join(HS, folder, 'tr.frd')
            return path if os.path.isfile(path) else None
    return None


def read_lsp(path):
    data = open(path, 'rb').read()
    n = (len(data) - 4) // 8
    return np.frombuffer(data, '<f4', n * 2, 4).reshape(n, 2).astype(np.float64)


def read_hsw(path):
    """-> (corridor edges, object edges), each (n, 7): a.xyz, b.xyz, height."""
    data = open(path, 'rb').read()
    if data[:4] != b'HSW1':
        return np.zeros((0, 7)), np.zeros((0, 7))
    n = struct.unpack_from('<I', data, 4)[0]
    walls = np.frombuffer(data, '<f4', n * 7, 8).reshape(n, 7).astype(np.float64)
    o = 8 + n * 28
    objects = np.zeros((0, 7))
    if data[o:o + 4] == b'HSX1':
        m = struct.unpack_from('<I', data, o + 8)[0]
        objects = np.frombuffer(data, '<f4', m * 7, o + 12).reshape(m, 7).astype(np.float64)
    return walls, objects


def read_aispeed(path, n):
    """-> (factor per slice, cap km/h per slice; 0 = no cap), as the wrapper reads it."""
    factor = np.ones(n)
    cap = np.zeros(n)
    if not os.path.isfile(path):
        return None
    for line in open(path, encoding='latin1'):
        p = line.split()
        if not p or p[0].startswith('#'):
            continue
        try:
            if p[0] == 'max' and len(p) >= 4:
                a, b, k = int(p[1]), int(p[2]), float(p[3])
                cap[[(i % n) for i in range(a, b + 1)]] = max(k, 0.0)
            elif len(p) >= 3:
                a, b, k = int(p[0]), int(p[1]), float(p[2])
                factor[[(i % n) for i in range(a, b + 1)]] = k
        except ValueError:
            continue
    return factor, cap


class AiTrack:
    """Everything about the road the AI planner and the walls know."""

    def __init__(self, track_dir, name, centreline=None):
        """`centreline`: the CRP article centres [(slice, x, y, z)], heights
        for a stock track that has no vroad."""
        self.name = name
        self.game = game_root_of(track_dir)
        self.errors = []
        work = os.path.join(self.game, 'GameData', 'Track', 'racer', 'work')
        self.lsp = {}
        for d in (0, 1):
            path = os.path.join(work, '%s%d.lsp' % (name, d))
            if os.path.isfile(path):
                self.lsp[d] = read_lsp(path)
        self.n = max((len(v) for v in self.lsp.values()), default=0)
        hsw = os.path.join(self.game, 'GameData', 'Track', '%s.hsw' % name)
        self.walls, self.objects = read_hsw(hsw) if os.path.isfile(hsw) else (np.zeros((0, 7)),) * 2
        self.frd_path = hs_source(name)
        self.centre = None        # (n, 3)
        self.right = None         # (n, 2) unit X/Z
        self.lane = None          # (n, 2) lane half-widths
        if self.frd_path:
            try:
                import nfs4_frd as frd
                t = frd.read(self.frd_path)
                vr = [t.vroad(i) for i in range(t.n_vroad)]
                self.centre = np.array([v['ref'] for v in vr], np.float64)
                r = np.array([(v['right'][0], v['right'][2]) for v in vr], np.float64)
                self.right = r / np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-9)
                self.lane = np.array([v['lane'] for v in vr], np.float64)
                self.n = len(vr)
            except Exception as e:      # noqa: BLE001
                self.errors.append('tr.frd: %s' % e)
        if self.centre is None and self.lsp:
            # stock track: no vroad, the forward line stands in for the centre
            pts = self.lsp.get(0, next(iter(self.lsp.values())))
            y = np.zeros(len(pts))
            if centreline:
                cl = np.array(centreline, np.float64)
                y = np.interp(np.arange(len(pts)), cl[:, 0] + 4.0, cl[:, 2])
            self.centre = np.column_stack([pts[:, 0], y, pts[:, 1]])
        self.zones = {}
        for d, suffix in ((0, ''), (1, '_r')):
            z = read_aispeed(os.path.join(self.game, 'GameData', 'Track', '%s%s.aispeed' % (name, suffix)),
                             max(self.n, 1))
            if z is not None:
                self.zones[d] = z
        self.lat_lsp = {d: self.lateral_along(p) for d, p in self.lsp.items()}
        self.wall_left, self.wall_right = self._wall_bounds()

    def use_line(self, direction, tag):
        """Replace a line by racer/work/<track><d>.<tag>.lsp ([ai] line_tag); False = no file."""
        path = os.path.join(self.game, 'GameData', 'Track', 'racer', 'work', '%s%d.%s.lsp' % (self.name, direction, tag))
        if not tag or not os.path.isfile(path):
            return False
        self.lsp[direction] = read_lsp(path)
        self.lat_lsp[direction] = self.lateral_along(self.lsp[direction])
        return True

    # ---------------------------------------------------------------- geometry
    @property
    def has_vroad(self):
        return self.right is not None

    def height_at(self, slices):
        """Road height at fractional slices (centre y)."""
        s = np.asarray(slices, np.float64)
        idx = np.arange(len(self.centre))
        return np.interp(s, idx, self.centre[:, 1], period=len(self.centre))

    def lateral_along(self, pts):
        """Lateral offset (m, along vroad right) of one point per slice."""
        if not self.has_vroad or len(pts) != self.n:
            return None
        d = pts - self.centre[:, [0, 2]]
        return (d * self.right).sum(axis=1)

    def lateral_at(self, slices, xz):
        """Lateral offset of arbitrary points; `slices` is the game's slice
        guess (car+8), refined to the nearest vroad point within +-6."""
        if not self.has_vroad:
            return np.full(len(xz), np.nan)
        slices = np.asarray(slices, np.int64) % self.n
        offs = np.arange(-6, 7)
        cand = (slices[:, None] + offs[None, :]) % self.n
        c = self.centre[cand][:, :, [0, 2]]
        dist = ((c - xz[:, None, :]) ** 2).sum(axis=2)
        best = cand[np.arange(len(cand)), dist.argmin(axis=1)]
        d = xz - self.centre[best][:, [0, 2]]
        return (d * self.right[best]).sum(axis=1)

    def point_at(self, slices, lateral):
        s = np.asarray(slices, np.int64) % self.n
        p = self.centre[s].copy()
        p[:, 0] += self.right[s, 0] * lateral
        p[:, 2] += self.right[s, 1] * lateral
        return p

    def _segs_near(self, i, reach=2):
        """.hsw corridor + object edges in the grid cells around slice i."""
        if self._grid is None:
            self._segs = np.vstack([self.walls, self.objects]) if len(self.objects) else self.walls
            self._grid = {}
            for k, s in enumerate(self._segs):
                for gx in range(int(min(s[0], s[3]) // self.CELL), int(max(s[0], s[3]) // self.CELL) + 1):
                    for gz in range(int(min(s[2], s[5]) // self.CELL), int(max(s[2], s[5]) // self.CELL) + 1):
                        self._grid.setdefault((gx, gz), []).append(k)
        cx, _cy, cz = self.centre[i]
        gx0, gz0 = int(cx // self.CELL), int(cz // self.CELL)
        near = set()
        for gx in range(gx0 - reach, gx0 + reach + 1):
            for gz in range(gz0 - reach, gz0 + reach + 1):
                near.update(self._grid.get((gx, gz), ()))
        return self._segs[sorted(near)] if near else np.zeros((0, 7))

    CELL = 32.0
    _grid = None

    def _wall_bounds(self, span=40.0, dy=4.0):
        """Nearest wall crossing on each side of the centre per slice (NaN = none
        within `span`), corridor and object edges alike."""
        n = self.n
        left, right = np.full(n, np.nan), np.full(n, np.nan)
        if not self.has_vroad or not len(self.walls):
            return left, right
        for i in range(n):
            s = self._segs_near(i)
            if not len(s):
                continue
            cx, cy, cz = self.centre[i]
            s = s[np.abs((s[:, 1] + s[:, 4]) * 0.5 - cy) <= dy]
            if not len(s):
                continue
            rx, rz = self.right[i]
            ex, ez = s[:, 3] - s[:, 0], s[:, 5] - s[:, 2]
            den = rx * ez - rz * ex
            ok = np.abs(den) > 1e-9
            den = np.where(ok, den, 1.0)
            t = ((s[:, 0] - cx) * ez - (s[:, 2] - cz) * ex) / den
            u = ((s[:, 0] - cx) * rz - (s[:, 2] - cz) * rx) / den
            hit = ok & (u >= 0.0) & (u <= 1.0) & (np.abs(t) <= span)
            t = t[hit]
            if (t < 0).any():
                left[i] = t[t < 0].max()
            if (t > 0).any():
                right[i] = t[t > 0].min()
        return left, right


# ----------------------------------------------------------------- traces ---

class Trace:
    """One race of a dx11z_trace.csv: per-car S rows as arrays, events."""

    def __init__(self, path, track=None):
        self.path = path
        self.run_dir = os.path.dirname(path)
        self.summary = {}
        sp = os.path.join(self.run_dir, 'summary.json')
        if os.path.isfile(sp):
            try:
                self.summary = json.load(open(sp, encoding='utf-8'))
            except (OSError, ValueError):
                pass
        rows = []
        self.track_name = None
        for line in open(path, encoding='latin1'):
            if line.startswith('#'):
                if rows:
                    break
                m = re.search(r'track=(\S+)', line)
                self.track_name = m.group(1) if m else None
                continue
            if not line.startswith('S,'):
                continue
            p = line.rstrip().split(',')
            if len(p) < 19:
                continue
            try:
                rows.append([float(v) for v in p[1:19]])
            except ValueError:
                continue
        a = np.array(rows, np.float64) if rows else np.zeros((0, len(S_COLS)))
        self.cars = {}
        for car in sorted({int(c) for c in a[:, 1]}) if len(a) else ():
            r = a[a[:, 1] == car]
            d = {k: r[:, i] for i, k in enumerate(S_COLS)}
            d['slice'] = d['slice'].astype(np.int64)
            v = np.hypot(d['vx'], d['vz'])
            f = np.hypot(d['fx'], d['fz'])
            cos = (d['vx'] * d['fx'] + d['vz'] * d['fz']) / np.maximum(v * f, 1e-9)
            d['slip'] = np.where((v > 1) & (f > 0.1), np.degrees(np.arccos(np.clip(cos, -1, 1))), 0.0)
            d['target_kmh'] = d['target'] * 3.6
            if track is not None:
                d['lateral'] = track.lateral_at(d['slice'], np.column_stack([d['x'], d['z']]))
            self.cars[car] = d
        self.top = int(max((c['slice'].max() for c in self.cars.values()), default=0))
        self.n_slices = track.n if track is not None and track.n else self.top + 1
        self.reverse = self._reverse()
        self._laps()
        self.events = self._events()
        self.stats = self._stats()

    def args(self):
        return {k: v for k, v in (s.split('=', 1) for s in self.summary.get('args', []) if '=' in s)}

    def laps_wanted(self):
        try:
            return int(self.args().get('num_laps', 0))
        except ValueError:
            return 0

    def _reverse(self):
        if self.args().get('reverse') in ('1',):
            return True
        n = self.n_slices
        votes = 0
        for d in self.cars.values():
            ds = np.diff(d['slice'])
            ds = ds[np.abs(ds) < n // 4]
            votes += int(np.sign(ds.sum())) if len(ds) else 0
        return votes < 0

    def _laps(self):
        """lap index per row (0 = grid to the first wrap); race_test.LapCounter rules."""
        from race_test import LapCounter
        top = max(self.n_slices - 1, self.top)
        for d in self.cars.values():
            lc = LapCounter()
            lap = np.zeros(len(d['tick']), np.int64)
            k = 0
            s = d['slice']
            for i in range(1, len(s)):
                if lc.step(int(s[i - 1]), int(s[i]), top):
                    k += 1
                lap[i] = k
            d['lap'] = lap
        self.max_lap = int(max((d['lap'].max() for d in self.cars.values()), default=0))

    def _events(self):
        out = []
        ev = os.path.join(self.run_dir, 'events.json')
        try:
            if os.path.isfile(ev):
                out = json.load(open(ev, encoding='utf-8'))
            else:
                from race_test import read_trace, analyse_trace
                from pathlib import Path
                S, H, R = read_trace(Path(self.path))
                out = analyse_trace(S, H, R, self.laps_wanted(), 0.0)['events']
        except Exception:          # noqa: BLE001 -- events are optional
            out = []
        for car, d in self.cars.items():
            spinning = False
            for i in range(len(d['tick'])):
                now = d['slip'][i] > 60 and d['kmh'][i] > 30
                if now and not spinning:
                    out.append({'type': 'spin', 'car': car, 'tick': int(d['tick'][i]),
                                'slice': int(d['slice'][i]), 'x': float(d['x'][i]),
                                'z': float(d['z'][i]), 'kmh': round(float(d['kmh'][i]))})
                spinning = now or (spinning and d['slip'][i] > 30)
        out.sort(key=lambda e: (e.get('tick', 0), e.get('car', 0)))
        return out

    def _stats(self):
        st = {}
        tr = self.summary.get('trace', {}).get('cars', {})
        for car, d in self.cars.items():
            s = dict(tr.get(str(car), {}))
            s['events'] = {}
            for e in self.events:
                if e.get('car') == car:
                    s['events'][e['type']] = s['events'].get(e['type'], 0) + 1
            s['avg_kmh_trace'] = float(d['kmh'].mean()) if len(d['kmh']) else 0.0
            if 'lap_times_s' not in s:
                s['lap_times_s'] = [float(d['tick'][d['lap'] == k][-1] - d['tick'][d['lap'] == k][0]) / TICKS
                                    for k in range(1, int(d['lap'].max()))]
            st[car] = s
        return st

    @property
    def recorded(self):
        """A session the player drove (run_record_line.bat), not a race_test run."""
        return self.path.startswith(RECORDS)

    def label(self):
        rel = os.path.relpath(self.path, REPORTS) if self.path.startswith(REPORTS) else self.path
        return rel.replace('\\', '/').replace('/dx11z_trace.csv', '')


def rows_of_lap(d, lap):
    """Row mask of one lap (-1 = all rows)."""
    return np.ones(len(d['tick']), bool) if lap < 0 else d['lap'] == lap


# ---------------------------------------------------------------- finding ---

class TraceFinder:
    """Background scan of reports/race_test for runs of one track: run dirs are
    NNN_<track>_<car>[_sSEED] at any depth up to 4 below the reports root;
    and of reports/line_record for the player's sessions,
    <yyyymmdd-hhmmss>_<track>_<race>."""

    def __init__(self, track, root=REPORTS, records=RECORDS):
        self.track = track.lower()
        self.root = root
        self.records = records
        self.results = []          # (mtime, path)
        self.done = False
        self._re = re.compile(r'^\d{3}_%s_' % re.escape(self.track))
        self._rec = re.compile(r'^\d{8}-\d{6}_%s_\d+$' % re.escape(self.track))
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        found = []
        try:
            for p in glob.iglob(os.path.join(self.records, '*', 'dx11z_trace.csv')):
                if self._rec.match(os.path.basename(os.path.dirname(p))):
                    try:
                        found.append((os.path.getmtime(p), os.path.normpath(p)))
                    except OSError:
                        pass
            for depth in range(1, 5):
                pattern = os.path.join(self.root, *(['*'] * depth), 'dx11z_trace.csv')
                for p in glob.iglob(pattern):
                    if self._re.match(os.path.basename(os.path.dirname(p))):
                        try:
                            found.append((os.path.getmtime(p), os.path.normpath(p)))
                        except OSError:
                            pass
        finally:
            found.sort(reverse=True)
            self.results = found
            self.done = True
