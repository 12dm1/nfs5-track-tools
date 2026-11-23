"""AI6 step 2: the AI's target speed per slice from the racing line itself.

    python -m tools.track_editor.ai_speed make summit [--lat 9] [--brake 6] [--lead 0.4] [--dir 0|1]
    python -m tools.track_editor.ai_speed calibrate summit TRACE [--car N]

`make` writes GameData/Track/<track>[_r].aiprofile ("slice kmh" lines) that the
wrapper ([ai] speed_profile=1, ai_tuning.cpp) puts into a racer's target speed
(+EB8) instead of the planner's, on converted tracks:
  * curvature of the .lsp line (circle through the points `span` slices before
    and after, in X/Z; crests are not modelled), lightly smoothed;
  * corner speed sqrt(lat / curvature), at most --vmax;
  * crests (jumps): v^2 <= crest * g / vertical curvature, the road heights along
    the line over +-vspan slices; crest 1 = the car just stays on the ground, 0 = off
    (+-2 slices takes 1-2 slice bumps for jumps: hometown 198 102 km/h, the old AI's GT1 178);
  * braking: backwards along the driving direction, v^2 <= v_next^2 + 2 brake ds;
  * lead: the value at a slice is the lowest of the next `lead` seconds, so the
    AI's speed controller starts braking that much earlier.
`fix` lines in an existing file (# fix start end factor) are kept and applied on
top: the auto-tuner's local corrections survive a regeneration.

`calibrate` prints, for a trace, the lateral acceleration v^2 * curvature the
cars reached along the line (percentiles, and the top corners).
"""

import argparse
import os
import re
import sys

import numpy as np

if __package__:
    from . import ai_data
    from .ai_record import ring_smooth
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ai_data
    from ai_record import ring_smooth

GAME = ai_data.nfs5cfg.GAME
DEFAULTS = dict(lat=10.0, brake=4.5, lead=0.5, vmax=280.0, span=2, smooth=1.0, crest=1.2, vspan=2, arc=0.0)


def profile_path(name, direction, game=GAME, tag=''):
    """tag: [ai] profile_tag of the wrapper, <track>[_r].<tag>.aiprofile."""
    return os.path.join(game, 'GameData', 'Track', '%s%s%s.aiprofile'
                        % (name, '_r' if direction else '', '.' + tag if tag else ''))


def line_points(track, direction):
    """(n, 3) racing line with road heights, in slice order."""
    p = track.lsp[direction]
    return np.column_stack([p[:, 0], track.centre[:, 1], p[:, 1]])


def is_ring(track):
    c = track.centre[:, [0, 2]]
    return np.linalg.norm(c[0] - c[-1]) < 3 * np.median(np.linalg.norm(np.diff(c, axis=0), axis=1))


def curvature_arc(pts, dist=10.0, ring=True, smooth=1.0):
    """1 / radius of the circle through the line points `dist` metres before and after
    slice i (X/Z, along the line). By distance, not by slices: where the line cuts a corner
    across fanned-out slices, several slices share a metre or two of line and the circle
    through i+-2 slices is a few metres wide (empire 864: R 2 m, 25 km/h on a straight cut)."""
    n = len(pts)
    xz = pts[:, [0, 2]]
    seg = np.linalg.norm(np.roll(xz, -1, axis=0) - xz, axis=1)
    if not ring:
        seg[-1] = 0.0
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    ext = np.vstack([xz, xz[:1]]) if ring else np.vstack([xz, xz[-1:]])

    def at(sv):
        sv = np.mod(sv, total) if ring else np.clip(sv, 0.0, cum[n - 1])
        return np.column_stack([np.interp(sv, cum, ext[:, 0]), np.interp(sv, cum, ext[:, 1])])

    a, b, c = at(cum[:n] - dist), xz, at(cum[:n] + dist)
    ab, bc, ca = np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)
    cross = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    k = 2.0 * cross / np.maximum(ab * bc * ca, 1e-6)
    if smooth > 0:
        k = ring_smooth(k, smooth, ring)
    return k


def curvature(pts, span=2, ring=True, smooth=1.0):
    """1 / radius of the circle through points i-span, i, i+span (X/Z)."""
    n = len(pts)
    xz = pts[:, [0, 2]]
    idx = np.arange(n)
    if ring:
        a, c = xz[(idx - span) % n], xz[(idx + span) % n]
    else:
        a, c = xz[np.maximum(idx - span, 0)], xz[np.minimum(idx + span, n - 1)]
    b = xz
    ab, bc, ca = np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)
    cross = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    k = 2.0 * cross / np.maximum(ab * bc * ca, 1e-6)
    if smooth > 0:
        k = ring_smooth(k, smooth, ring)
    return k


def vertical_curvature(pts, span=2, ring=True):
    """d(slope)/ds of the heights along the line; < 0 = crest (the road falls away)."""
    n = len(pts)
    idx = np.arange(n)
    if ring:
        a, c = (idx - span) % n, (idx + span) % n
    else:
        a, c = np.maximum(idx - span, 0), np.minimum(idx + span, n - 1)
    seg = np.linalg.norm(np.roll(pts[:, [0, 2]], -1, axis=0) - pts[:, [0, 2]], axis=1)
    if not ring:
        seg[-1] = 0.0
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]

    def dist(i, j):                       # forward distance i -> j along slice order
        d = cum[j] - cum[i]
        return np.where(d < 0, d + total, d) if ring else d

    y = pts[:, 1]
    d1, d2 = np.maximum(dist(a, idx), 0.5), np.maximum(dist(idx, c), 0.5)
    g1, g2 = (y - y[a]) / d1, (y[c] - y) / d2
    return (g2 - g1) / ((d1 + d2) * 0.5)


def compute(track, direction, lat, brake, lead, vmax, span=2, smooth=1.0, crest=1.2, fixes=(), vspan=2, arc=0.0,
            caps=()):
    """-> (km/h per slice, corner-only km/h, curvature)."""
    pts = line_points(track, direction)
    ring = is_ring(track)
    k = curvature(pts, span, ring, smooth)
    if arc > 0:          # the by-slice circle explodes where slices bunch up; by distance it does not
        k = np.minimum(k, curvature_arc(pts, arc, ring, smooth))
    v = np.minimum(np.sqrt(lat / np.maximum(k, 1e-6)), vmax / 3.6)
    if crest > 0:
        kv = vertical_curvature(pts, int(vspan), ring)
        over = -kv > 1e-4
        v[over] = np.minimum(v[over], np.sqrt(crest * 9.81 / -kv[over]))
    for a, b, f in fixes:
        for i in range(a, b + 1):
            v[i % len(v)] *= f
    # caps (km/h): a hard limit before the braking pass, so the car brakes for it in
    # time; a factor cannot slow a straight (vmax x f stays above the braking curve)
    for a, b, kmh in caps:
        for i in range(a, b + 1):
            v[i % len(v)] = min(v[i % len(v)], kmh / 3.6)
    corner = v.copy()
    # driving order: forward = rising slice numbers, reverse = falling
    order = np.arange(len(v)) if direction == 0 else np.arange(len(v))[::-1]
    p = pts[order]
    ds = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)       # i -> next in driving order
    if not ring:
        ds[-1] = 0.0
    w = v[order]
    for _ in range(2 if ring else 1):
        for j in range(len(w) - 1, -1, -1):
            nxt = (j + 1) % len(w)
            if nxt == 0 and not ring:
                continue
            w[j] = min(w[j], np.sqrt(w[nxt] ** 2 + 2.0 * brake * ds[j]))
    out = w.copy()
    if lead > 0:
        step = max(float(np.median(ds)), 0.5)
        for j in range(len(w)):
            m = int(np.ceil(w[j] * lead / step))
            js = (j + np.arange(m + 1)) % len(w) if ring else np.arange(j, min(j + m + 1, len(w)))
            out[j] = w[js].min()
    res = np.empty_like(out)
    res[order] = out
    return res * 3.6, corner * 3.6, k


def read_fixes(path):
    """'# fix start end factor' lines of an existing .aiprofile."""
    out = []
    if os.path.isfile(path):
        for line in open(path, encoding='latin1'):
            m = re.match(r'#\s*fix\s+(-?\d+)\s+(-?\d+)\s+([\d.]+)', line)
            if m:
                out.append((int(m.group(1)), int(m.group(2)), float(m.group(3))))
    return out


def read_caps(path):
    """'# cap start end kmh' lines of an existing .aiprofile."""
    out = []
    if os.path.isfile(path):
        for line in open(path, encoding='latin1'):
            m = re.match(r'#\s*cap\s+(-?\d+)\s+(-?\d+)\s+([\d.]+)', line)
            if m:
                out.append((int(m.group(1)), int(m.group(2)), float(m.group(3))))
    return out


def read_profile(path):
    v = {}
    for line in open(path, encoding='latin1'):
        p = line.split()
        if len(p) >= 2 and not p[0].startswith('#'):
            v[int(p[0])] = float(p[1])
    return v


def read_extra(path):
    """Header lines the wrapper reads besides params/fix/cap: '# carmul k', '# carscale <ais> k'."""
    if not os.path.isfile(path):
        return []
    return [line.rstrip('\n') for line in open(path, encoding='latin1')
            if line.startswith('# carmul') or line.startswith('# carscale')]


def write_profile(path, kmh, params, fixes, caps=(), extra=()):
    with open(path + '.tmp', 'w', encoding='latin1', newline='\n') as f:
        f.write('# AI6 speed profile (tools/track_editor/ai_speed.py): target km/h per slice\n')
        f.write('# params %s\n' % ' '.join('%s=%g' % kv for kv in sorted(params.items())))
        for a, b, x in fixes:
            f.write('# fix %d %d %.3f\n' % (a, b, x))
        for a, b, x in caps:
            f.write('# cap %d %d %.1f\n' % (a, b, x))
        for line in extra:
            f.write(line + '\n')
        for i, v in enumerate(kmh):
            f.write('%d %.1f\n' % (i, v))
    os.replace(path + '.tmp', path)


def make(name, direction=0, fixes=None, write=True, game=GAME, tag='', quiet=False, track=None, caps=None,
         extra=None, **kw):
    """`track`: an AiTrack to use (its line may be a tagged one, AiTrack.use_line)."""
    params = dict(DEFAULTS)
    params.update({k: v for k, v in kw.items() if v is not None})
    track = track or ai_data.AiTrack(os.path.join(game, 'GameData', 'Track'), name)
    if direction not in track.lsp or not track.has_vroad:
        print('%s: no line %d or no vroad' % (name, direction))
        return None
    path = profile_path(name, direction, game, tag)
    fixes = read_fixes(path) if fixes is None else fixes
    caps = read_caps(path) if caps is None else caps
    extra = read_extra(path) if extra is None else extra
    kmh, corner, k = compute(track, direction, fixes=fixes, caps=caps, **params)
    pts = line_points(track, direction)
    ds = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    t = (ds / (np.maximum(kmh[:-1], 1) / 3.6)).sum()
    if not quiet:
        print('%s%s: %.0f-%.0f km/h, mean %.0f, ideal lap %.1f s (%s, %d fixes)' % (
            name, '_r' if direction else '', kmh.min(), kmh.max(), kmh.mean(), t,
            ' '.join('%s=%g' % kv for kv in sorted(params.items())), len(fixes)))
    if write:
        write_profile(path, kmh, params, fixes, caps, extra)
        if not quiet:
            print('  wrote %s' % os.path.relpath(path, game))
    return kmh


def calibrate(name, trace, car=None, direction=None, game=GAME):
    track = ai_data.AiTrack(os.path.join(game, 'GameData', 'Track'), name)
    tr = ai_data.Trace(trace, track)
    direction = (1 if tr.reverse else 0) if direction is None else direction
    k = curvature(line_points(track, direction), ring=is_ring(track))
    for c, d in sorted(tr.cars.items()):
        if car is not None and c != car:
            continue
        m = (d['lap'] >= 1) & (d['slip'] < 10) & (d['kmh'] > 30)
        v = d['kmh'][m] / 3.6
        a = v * v * k[d['slice'][m] % track.n]
        tight = k[d['slice'][m] % track.n] > 1 / 150.0
        print('car %d: v^2*k over bends (r < 150 m): p50 %.1f p90 %.1f p98 %.1f m/s2 (%d rows)' % (
            c, *np.percentile(a[tight], [50, 90, 98]), tight.sum()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('make')
    m.add_argument('track')
    m.add_argument('--dir', type=int, default=0)
    for key in ('lat', 'brake', 'lead', 'vmax', 'crest'):
        m.add_argument('--' + key, type=float, help='default %g' % DEFAULTS[key])
    m.add_argument('--no-fixes', action='store_true', help='drop the # fix lines of the old file')
    m.add_argument('--tag', default='', help='write <track>.<tag>.aiprofile ([ai] profile_tag)')
    m.add_argument('--dry', action='store_true')
    c = sub.add_parser('calibrate')
    c.add_argument('track')
    c.add_argument('trace')
    c.add_argument('--car', type=int)
    a = ap.parse_args(argv)
    if a.cmd == 'make':
        r = make(a.track.lower(), a.dir, [] if a.no_fixes else None, not a.dry, tag=a.tag,
                 lat=a.lat, brake=a.brake, lead=a.lead, vmax=a.vmax, crest=a.crest)
        return 0 if r is not None else 1
    trace = a.trace if a.trace.endswith('.csv') else os.path.join(a.trace, 'dx11z_trace.csv')
    calibrate(a.track.lower(), trace, a.car)
    return 0


if __name__ == '__main__':
    sys.exit(main())
