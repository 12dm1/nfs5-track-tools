"""AI6: a racing line from a lap the player drove.

    run_record_line.bat                      (game with the trace on; the session is
                                              split into <work>/reports/line_record/ on exit)
    python -m tools.track_editor.ai_record collect dx11z_trace.csv
    python -m tools.track_editor.ai_record make hometown [--rec DIR] [--lap N] [--dry]
    python -m tools.track_editor.ai_record restore hometown [--dir 0|1]
    python -m tools.track_editor.ai_record splice summit 910 919 [--from orig|bak|FILE] [--blend 10] [--dir 0|1]

`make` takes the newest recording of the track (or --rec), car 0 = the player,
and the last completed lap without a respawn (or --lap). For every slice it
finds where the car crossed the slice line (the plane through the vroad point
across the driving direction), smooths the lateral offset a little, keeps it
--margin m inside the .hsw walls next to it and writes
GameData/Track/racer/work/<track>{0,1}.lsp (0 = forward race, 1 = reverse).
The first install keeps the shipped line as <track>N.lsp.orig, every install
the previous one as .lsp.bak; `restore` puts .orig back.
"""

import argparse
import glob
import os
import re
import shutil
import struct
import sys
import time

import numpy as np

if __package__:
    from . import ai_data
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ai_data

GAME = ai_data.nfs5cfg.GAME
RECORDS = ai_data.RECORDS
MIN_TICKS = 128               # shorter races (a restart from the grid) are dropped


# ---------------------------------------------------------------- collect ---

def collect(src, out_root=RECORDS):
    """Split a session's dx11z_trace.csv into one run dir per race:
    <out_root>/<yyyymmdd-hhmmss>_<track>_<k>/dx11z_trace.csv."""
    if not os.path.isfile(src):
        print('no trace: %s' % src)
        return []
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(os.path.getmtime(src)))
    races, cur = [], None
    for line in open(src, encoding='latin1'):
        if line.startswith('# race'):
            cur = {'head': line, 'lines': [], 'last': 0}
            m = re.search(r'track=(\S+)', line)
            cur['track'] = m.group(1).lower() if m else 'unknown'
            races.append(cur)
            continue
        if cur is None:
            continue
        cur['lines'].append(line)
        if line.startswith('S,'):
            try:
                cur['last'] = int(line.split(',', 2)[1])
            except ValueError:
                pass
    out = []
    for k, r in enumerate(races):
        if r['last'] < MIN_TICKS:
            continue
        d = os.path.join(out_root, '%s_%s_%d' % (stamp, r['track'], k))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'dx11z_trace.csv'), 'w', encoding='latin1') as f:
            f.write(r['head'])
            f.writelines(r['lines'])
        out.append(d)
        print('%s: %s, %.0f s' % (os.path.relpath(d, out_root), r['track'], r['last'] / ai_data.TICKS))
    if not out:
        print('no race longer than %.0f s in %s' % (MIN_TICKS / ai_data.TICKS, src))
    return out


def newest_record(track, direction=None, root=RECORDS):
    """Newest recording of the track; with `direction`, the newest of that
    direction that has a usable lap (a completed lap without a respawn)."""
    pat = re.compile(r'^\d{8}-\d{6}_%s_\d+$' % re.escape(track.lower()))
    found = sorted((d for d in glob.glob(os.path.join(root, '*')) if pat.match(os.path.basename(d))),
                   key=lambda d: (os.path.basename(d)[:15], int(d.rsplit('_', 1)[1])), reverse=True)
    if direction is None:
        return found[0] if found else None
    track_obj = None
    for d in found:
        path = os.path.join(d, 'dx11z_trace.csv')
        if track_obj is None:
            track_obj = ai_data.AiTrack(os.path.join(GAME, 'GameData', 'Track'), track)
        tr = ai_data.Trace(path, track_obj)
        if (1 if tr.reverse else 0) != direction or 0 not in tr.cars:
            continue
        if pick_lap(tr.cars[0], respawn_ticks(path), track_obj.n, tr.reverse)[0] is not None:
            return d
    return None


# ------------------------------------------------------------------- make ---

def respawn_ticks(path, car=0):
    out = []
    for line in open(path, encoding='latin1'):
        if line.startswith('#') and out:
            break
        if line.startswith('R,'):
            p = line.split(',')
            if int(p[2]) == car:
                out.append(int(p[1]))
    return out


def wall_hits(path, car=0):
    """(tick, slice, kind) of the car's contacts (H rows)."""
    out = []
    seen_rows = False
    for line in open(path, encoding='latin1'):
        if line.startswith('#'):
            if seen_rows:
                break
            continue
        seen_rows = True
        if line.startswith('H,'):
            p = line.split(',')
            if int(p[2]) == car:
                out.append((int(p[1]), int(p[3]), (int(p[9], 16) >> 16) & 0xFFFF))
    return out


def pick_lap(d, respawns, n, reverse, lap=None):
    """Completed laps = those followed by another wrap, and the last one when it
    covers the ring and ends at the line (the session ended right after it);
    -> (lap, reason) or (None, reason)."""
    last = int(d['lap'].max())
    complete = list(range(1, last))
    # grid -> line counts when the grid sits right behind the line (a few slices missing)
    if last >= 1 and len(np.unique(d['slice'][d['lap'] == 0])) > 0.97 * n:
        complete.insert(0, 0)
    s = d['slice'][d['lap'] == last]
    end = (n - 1 - s[-1]) if not reverse else s[-1]
    if last >= 1 and len(np.unique(s)) > 0.97 * n and 0 <= end <= 3:
        complete.append(last)
    if lap is not None:
        return (lap, 'asked') if lap in complete else (None, 'lap %d is not complete (complete: %s)'
                                                        % (lap, complete or 'none'))
    for k in reversed(complete):
        t = d['tick'][d['lap'] == k]
        if not any(t[0] <= r <= t[-1] for r in respawns):
            return k, 'last completed lap without a respawn'
    return None, 'no completed lap without a respawn (completed: %d)' % len(complete)


def forward_dirs(track):
    """Unit X/Z along increasing slice numbers."""
    c = track.centre[:, [0, 2]]
    f = np.roll(c, -1, axis=0) - np.roll(c, 1, axis=0)
    f[0], f[-1] = c[1] - c[0], c[-1] - c[-2]
    if np.linalg.norm(c[0] - c[-1]) < 3 * np.median(np.linalg.norm(np.diff(c, axis=0), axis=1)):
        f[0], f[-1] = c[1] - c[-1], c[0] - c[-2]
    return f / np.maximum(np.linalg.norm(f, axis=1, keepdims=True), 1e-9)


def crossings(track, d, rows):
    """Per slice the X/Z where the car crossed its line (NaN = not found)."""
    n = track.n
    c = track.centre[:, [0, 2]]
    f = forward_dirs(track)
    p = np.column_stack([d['x'][rows], d['z'][rows]])
    s = d['slice'][rows] % n
    out = np.full((n, 2), np.nan)
    by_slice = {}
    for k, v in enumerate(s):
        by_slice.setdefault(int(v), []).append(k)
    for i in range(n):
        cand = sorted({k for o in range(-10, 11) for k in by_slice.get((i + o) % n, ())})
        cand = [k for k in cand if k + 1 < len(p)]
        if not cand:
            continue
        ks = np.array(cand)
        a = ((p[ks] - c[i]) * f[i]).sum(axis=1)
        b = ((p[ks + 1] - c[i]) * f[i]).sum(axis=1)
        hit = np.nonzero((a <= 0) != (b <= 0))[0]
        hit = [h for h in hit if np.linalg.norm(p[ks[h] + 1] - p[ks[h]]) < 20.0]   # not a teleport
        if not hit:
            continue
        h = hit[0]
        t = a[h] / (a[h] - b[h])
        out[i] = p[ks[h]] + (p[ks[h] + 1] - p[ks[h]]) * t
    return out


def untangle(pts, ring=True, max_turn=60.0, grow=1, centre=None, max_turn_vs_road=30.0):
    """Line points (n, 2) where the line turns more than max_turn degrees in one slice step
    are replaced by a straight run between the good points around them. Where slices fan
    out on the inside of a corner the plane crossings land out of order (empire 862-866:
    the line went backwards 1 m a step while the player drove a smooth arc).
    -> (points, list of replaced slice runs)."""
    p = np.asarray(pts, np.float64).copy()
    n = len(p)
    idx = np.arange(n)
    nxt = (idx + 1) % n if ring else np.minimum(idx + 1, n - 1)
    prv = (idx - 1) % n if ring else np.maximum(idx - 1, 0)
    a, b = p - p[prv], p[nxt] - p
    la, lb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    cos = (a * b).sum(axis=1) / np.maximum(la * lb, 1e-9)
    turn = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    bad = (turn > max_turn) & (la > 1e-6) & (lb > 1e-6)
    if centre is not None:
        # also: a sharp turn the road does not make (real 90-degree street corners turn the
        # vroad as well: hometown 557, country 592)
        c = np.asarray(centre, np.float64)
        ca, cb = c - c[prv], c[nxt] - c
        ccos = (ca * cb).sum(axis=1) / np.maximum(np.linalg.norm(ca, axis=1) * np.linalg.norm(cb, axis=1), 1e-9)
        road = np.degrees(np.arccos(np.clip(ccos, -1, 1)))
        bad |= (turn > 35.0) & (turn - road > max_turn_vs_road) & (la > 1e-6) & (lb > 1e-6)
    if not ring:
        bad[0] = bad[-1] = False
    for _ in range(grow):
        bad = bad | bad[prv] | bad[nxt]
    if bad.all() or not bad.any():
        return p, []
    good = np.nonzero(~bad)[0]
    if ring:
        ext = np.concatenate([good - n, good, good + n])
        for c in (0, 1):
            p[bad, c] = np.interp(idx[bad], ext, np.concatenate([p[good, c]] * 3))
    else:
        for c in (0, 1):
            p[bad, c] = np.interp(idx[bad], good, p[good, c])
    return p, runs(np.nonzero(bad)[0])


def ring_smooth(x, sigma, ring):
    if sigma <= 0:
        return x.copy()
    r = int(np.ceil(sigma * 3))
    w = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    if ring:
        xp = np.concatenate([x[-r:], x, x[:r]])
        return np.convolve(xp, w / w.sum(), 'valid')
    xp = np.concatenate([np.full(r, x[0]), x, np.full(r, x[-1])])
    return np.convolve(xp, w / w.sum(), 'valid')


def wall_crossings(track, i, span=30.0, dy=4.0):
    """Lateral offsets of all .hsw edges (corridor + objects) across slice i."""
    segs = track._segs_near(i)
    if not len(segs):
        return np.zeros(0)
    cx, cy, cz = track.centre[i]
    s = segs[np.abs((segs[:, 1] + segs[:, 4]) * 0.5 - cy) <= dy]
    rx, rz = track.right[i]
    ex, ez = s[:, 3] - s[:, 0], s[:, 5] - s[:, 2]
    den = rx * ez - rz * ex
    ok = np.abs(den) > 1e-9
    den = np.where(ok, den, 1.0)
    t = ((s[:, 0] - cx) * ez - (s[:, 2] - cz) * ex) / den
    u = ((s[:, 0] - cx) * rz - (s[:, 2] - cz) * rx) / den
    return np.sort(t[ok & (u >= 0) & (u <= 1) & (np.abs(t) <= span)])


def keep_inside(track, lat, margin):
    """Clamp each lateral offset `margin` m inside the walls on both sides of it."""
    out = lat.copy()
    moved = []
    for i in range(len(lat)):
        xs = wall_crossings(track, i)
        left = xs[xs < lat[i]].max() if (xs < lat[i]).any() else -np.inf
        right = xs[xs > lat[i]].min() if (xs > lat[i]).any() else np.inf
        a, b = left + margin, right - margin
        if a > b:
            a = b = (left + right) * 0.5
        v = min(max(lat[i], a), b)
        if abs(v - lat[i]) > 0.05:
            moved.append((i, lat[i], v))
        out[i] = v
    return out, moved


def runs(idx, gap=1):
    """[3,4,5,9] -> [(3,5),(9,9)]."""
    out = []
    for i in idx:
        if out and i - out[-1][1] <= gap:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def write_tagged(path, pts):
    with open(path + '.tmp', 'wb') as f:
        f.write(struct.pack('<I', 1))
        f.write(np.asarray(pts, '<f4').tobytes())
    os.replace(path + '.tmp', path)


def write_lsp(path, pts):
    if os.path.isfile(path):
        if not os.path.isfile(path + '.orig'):
            shutil.copy2(path, path + '.orig')
        shutil.copy2(path, path + '.bak')
    # a new file, not a rewrite in place: game copies hard-link GameData (nfs5r_shots)
    with open(path + '.tmp', 'wb') as f:
        f.write(struct.pack('<I', 1))
        f.write(np.asarray(pts, '<f4').tobytes())
    os.replace(path + '.tmp', path)


def make(name, rec=None, lap=None, smooth=1.0, margin=1.2, write=True, game=GAME, tag='', direction=None):
    track_dir = os.path.join(game, 'GameData', 'Track')
    rec = rec or newest_record(name, direction)
    if not rec:
        print('no recording of %s in %s' % (name, RECORDS))
        return 1
    path = rec if rec.endswith('.csv') else os.path.join(rec, 'dx11z_trace.csv')
    track = ai_data.AiTrack(track_dir, name)
    if not track.has_vroad:
        print('%s: no NFS4 vroad (stock track?) - only converted tracks are supported' % name)
        return 1
    tr = ai_data.Trace(path, track)
    if tr.track_name and tr.track_name.lower() != name.lower():
        print('recording is from %s, not %s' % (tr.track_name, name))
        return 1
    if 0 not in tr.cars:
        print('no player car in %s' % path)
        return 1
    d = tr.cars[0]
    respawns = respawn_ticks(path)
    k, why = pick_lap(d, respawns, track.n, tr.reverse, lap)
    print('%s: %s race, %s' % (os.path.relpath(path, RECORDS) if path.startswith(RECORDS) else path,
                               'reverse' if tr.reverse else 'forward', why if k is None else
                               'lap %d (%s)' % (k, why)))
    if k is None:
        return 1
    idx = np.nonzero(d['lap'] == k)[0]
    lo, hi = max(idx[0] - 3, 0), min(idx[-1] + 40, len(d["tick"]))   # + the line after the last wrap
    rows = np.arange(lo, hi)
    t0, t1 = d['tick'][idx[0]], d['tick'][idx[-1]]
    print('  %.0f-%.0f s into the race: lap time %.1f s, avg %.0f km/h, max %.0f km/h' % (
        t0 / ai_data.TICKS, t1 / ai_data.TICKS, (t1 - t0) / ai_data.TICKS,
        d['kmh'][idx].mean(), d['kmh'][idx].max()))
    hits = [h for h in wall_hits(path) if t0 <= h[0] <= t1]
    if hits:
        print('  contacts in this lap: %s' % ', '.join('slice %d (%s)' % (s, {4: 'wall', 5: 'car'}.get(c, 'obj'))
                                                         for _t, s, c in hits[:20]))

    pts = crossings(track, d, rows)
    miss = np.nonzero(np.isnan(pts[:, 0]))[0]
    lat = np.full(track.n, np.nan)
    ok = ~np.isnan(pts[:, 0])
    lat[ok] = ((pts[ok] - track.centre[ok][:, [0, 2]]) * track.right[ok]).sum(axis=1)
    if len(miss):
        print('  %d slices without a crossing, interpolated: %s' % (
            len(miss), ' '.join('%d-%d' % r if r[0] != r[1] else str(r[0]) for r in runs(miss))))
        if len(miss) > track.n // 10:
            print('  too many gaps - the lap does not cover the track')
            return 1
        i = np.arange(track.n)
        lat[miss] = np.interp(miss, i[ok], lat[ok], period=track.n)
    raw = lat.copy()
    ring = np.linalg.norm(track.centre[0, [0, 2]] - track.centre[-1, [0, 2]]) < 20.0
    lat = ring_smooth(lat, smooth, ring)
    lat, moved = keep_inside(track, lat, margin)
    if moved:
        print('  kept %.1f m off the walls on %d slices: %s' % (
            margin, len(moved), ' '.join('%d-%d' % r if r[0] != r[1] else str(r[0])
                                          for r in runs([m[0] for m in moved], 2))))
    step = np.abs(np.diff(lat))
    print('  smoothing %.1f slices: max change vs the driven line %.2f m; max sideways step %.2f m/slice'
          % (smooth, np.abs(lat - raw).max(), step.max()))
    direction = 1 if tr.reverse else 0
    old = track.lat_lsp.get(direction)
    if old is not None:
        diff = lat - old
        big = np.nonzero(np.abs(diff) > 2.0)[0]
        print('  vs the installed %s line: mean |diff| %.2f m, max %.1f m at slice %d; > 2 m on %d slices'
              % ('reverse' if direction else 'forward', np.abs(diff).mean(), np.abs(diff).max(),
                 int(np.abs(diff).argmax()), len(big)))
    p = track.point_at(np.arange(track.n), lat)[:, [0, 2]]
    p, tangled = untangle(p, ring, centre=track.centre[:, [0, 2]])
    if tangled:
        print('  untangled (out-of-order crossings, straight runs): %s' % ' '.join('%d-%d' % r for r in tangled))
    out = os.path.join(track_dir, 'racer', 'work', '%s%d%s.lsp' % (name, direction, '.' + tag if tag else ''))
    if write and tag:                   # [ai] line_tag file for tuning runs: no .orig/.bak
        write_tagged(out, p)
        print('  wrote %s' % os.path.relpath(out, game))
    elif write:
        write_lsp(out, p)
        print('  wrote %s (previous kept as .bak%s)' % (
            os.path.relpath(out, game), ', shipped as .orig' if os.path.isfile(out + '.orig') else ''))
    else:
        print('  --dry: %s not written' % os.path.relpath(out, game))
    return 0


def splice(name, a, b, source='orig', blend=10, direction=0, game=GAME):
    """Slices a..b of the installed line from another line (.orig, .bak or a
    .lsp path), easing in and out over `blend` slices on each side."""
    track_dir = os.path.join(game, 'GameData', 'Track')
    path = os.path.join(track_dir, 'racer', 'work', '%s%d.lsp' % (name, direction))
    src = source if os.path.isfile(source) else path + '.' + source
    if not os.path.isfile(src):
        print('no %s' % src)
        return 1
    track = ai_data.AiTrack(track_dir, name)
    cur = track.lateral_along(ai_data.read_lsp(path))
    other = track.lateral_along(ai_data.read_lsp(src))
    if cur is None or other is None:
        print('%s: lines do not match the vroad (%d slices)' % (name, track.n))
        return 1
    n = track.n
    w = np.zeros(n)
    for i in range(a - blend, b + blend + 1):
        if i < a:
            t = (i - (a - blend - 1)) / (blend + 1.0)
        elif i > b:
            t = ((b + blend + 1) - i) / (blend + 1.0)
        else:
            t = 1.0
        w[i % n] = t * t * (3 - 2 * t)                 # smoothstep
    lat = cur * (1 - w) + other * w
    write_lsp(path, track.point_at(np.arange(n), lat)[:, [0, 2]])
    k = np.arange(a, b + 1) % n
    print('%s: slices %d-%d from %s (blend %d): lateral was %.1f..%.1f m, now %.1f..%.1f m'
          % (os.path.relpath(path, game), a, b, os.path.basename(src), blend,
             cur[k].min(), cur[k].max(), lat[k].min(), lat[k].max()))
    return 0


def restore(name, direction, game=GAME):
    path = os.path.join(game, 'GameData', 'Track', 'racer', 'work', '%s%d.lsp' % (name, direction))
    if not os.path.isfile(path + '.orig'):
        print('no %s.orig' % os.path.relpath(path, game))
        return 1
    shutil.copyfile(path + '.orig', path + '.tmp')
    os.replace(path + '.tmp', path)
    print('restored %s' % os.path.relpath(path, game))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('collect')
    c.add_argument('trace')
    m = sub.add_parser('make')
    m.add_argument('track')
    m.add_argument('--rec', help='recording dir or dx11z_trace.csv (default: newest of the track)')
    m.add_argument('--lap', type=int)
    m.add_argument('--tag', default='', help='write <track><d>.<tag>.lsp for [ai] line_tag (installed line untouched)')
    m.add_argument('--dir', type=int, default=None, help='newest recording of this direction (0/1)')
    m.add_argument('--smooth', type=float, default=1.0, help='Gaussian sigma in slices (0 = as driven)')
    m.add_argument('--margin', type=float, default=1.2, help='metres from the walls')
    m.add_argument('--dry', action='store_true')
    r = sub.add_parser('restore')
    r.add_argument('track')
    r.add_argument('--dir', type=int, default=None, help='0 forward, 1 reverse (default both)')
    sp = sub.add_parser('splice')
    sp.add_argument('track')
    sp.add_argument('start', type=int)
    sp.add_argument('end', type=int)
    sp.add_argument('--from', dest='source', default='orig', help='orig, bak or a .lsp path')
    sp.add_argument('--blend', type=int, default=10, help='slices of easing on each side')
    sp.add_argument('--dir', type=int, default=0)
    a = ap.parse_args(argv)
    if a.cmd == 'splice':
        return splice(a.track.lower(), a.start, a.end, a.source, a.blend, a.dir)
    if a.cmd == 'collect':
        return 0 if collect(a.trace) else 1
    if a.cmd == 'make':
        return make(a.track.lower(), a.rec, a.lap, a.smooth, a.margin, not a.dry, tag=a.tag, direction=a.dir)
    rc = 0
    for d in ((0, 1) if a.dir is None else (a.dir,)):
        rc |= restore(a.track.lower(), d)
    return rc


if __name__ == '__main__':
    sys.exit(main())
