"""HS6 acceptance: collision, AI route and topology files.

Beyond checking each file against its own format, this looks for the failure
mode that actually breaks collision: a barrier polyline that folds back on
itself where the wall offset exceeds the corner radius.

    python test_hs6_support.py [track name ...]
"""

import collections
import math
import os
import re
import struct
import sys

import nfs4_frd as frd
import nfs5_crp as reader
import nfs5_track_files as support
import export_hs_walls
import nfs5cfg

HS = nfs5cfg.HS_TRACKS
BUILD = os.environ.get(
    'NFS_HS6_BUILD',
    os.path.join(nfs5cfg.WORK, 'build'))
DTX = os.path.join(nfs5cfg.GAME, 'GameData', 'Track', 'alps.dtx')


def check_key(path, n_slices, problems, label):
    d = open(path, 'rb').read()
    if len(d) != 8 + support.KEY_SLOTS * 8:
        problems.append('%s: %d bytes instead of 3208' % (label, len(d)))
        return
    version, count = struct.unpack_from('<II', d, 0)
    if version != 1:
        problems.append('%s: version %d' % (label, version))
    if not 2 <= count <= support.KEY_SLOTS:
        problems.append('%s: %d points' % (label, count))
        return
    slots = [struct.unpack_from('<If', d, 8 + i * 8) for i in range(support.KEY_SLOTS)]
    real = slots[:count - 1]
    if [s for s, _ in real] != sorted(s for s, _ in real):
        problems.append('%s: slices not ascending' % label)
    if any(s >= n_slices for s, _ in real):
        problems.append('%s: slice outside the track' % label)
    if any(not support.KEY_MIN_SPEED - 1 <= v <= support.KEY_MAX_SPEED for _, v in real):
        problems.append('%s: speed out of range' % label)
    if slots[count - 1] != (support.KEY_SENTINEL_SLICE, support.KEY_SENTINEL_SPEED):
        problems.append('%s: no closing record' % label)
    for s, v in slots[count:]:
        if (s, v) != support.KEY_UNUSED:
            problems.append('%s: garbage in the free slots' % label)
            break


def read_lsp(path, n_slices, problems, label):
    d = open(path, 'rb').read()
    expected = 4 + n_slices * 8
    if len(d) != expected:
        problems.append('%s: %d bytes instead of %d' % (label, len(d), expected))
        return []
    if struct.unpack_from('<I', d, 0)[0] != 1:
        problems.append('%s: wrong version' % label)
    return [struct.unpack_from('<2f', d, 4 + i * 8)
            for i in range(n_slices)]


def check_lsp(path, track, lateral, problems, label):
    points = read_lsp(path, track.n_vroad, problems, label)
    if not points:
        return points
    for i, actual in enumerate(points):
        vr = track.vroad(i)
        expected = support._offset(vr['ref'], vr['right'], lateral[i])
        if math.dist(actual, (expected[0], expected[2])) > 1e-3:
            problems.append('%s: point %d does not match the spd line' % (label, i))
            break
    return points


def _point_segment_xz(point, wall):
    a, b = wall['a'], wall['b']
    vx, vz = b[0] - a[0], b[2] - a[2]
    q = vx * vx + vz * vz
    u = 0.0 if q == 0.0 else max(0.0, min(1.0,
        ((point[0] - a[0]) * vx + (point[2] - a[2]) * vz) / q))
    wall_y = a[1] + u * (b[1] - a[1])
    if abs(point[1] - wall_y) > 2.0:       # an overpass is not this road's wall
        return 1e9
    return math.hypot(point[0] - (a[0] + u * vx),
                      point[2] - (a[2] + u * vz))


def check_hometown_island(track, lateral, problems, label):
    """Regression for the boulevard island crossed by the old centre LSP."""
    walls = export_hs_walls.wall_segments(track)
    clearance = 1e9
    for i in range(500, 526):
        vr = track.vroad(i)
        point = support._offset(vr['ref'], vr['right'], lateral[i])
        clearance = min(clearance,
                        min(_point_segment_xz(point, wall) for wall in walls))
    if clearance < 3.0:
        problems.append('%s: island clearance %.2f m' % (label, clearance))


def check_edg(path, track, problems):
    d = open(path, 'rb').read()
    if (len(d) - 4) % 28:
        problems.append('edg: size not a multiple of 28')
        return None
    n = (len(d) - 4) // 28
    walls = []
    for i in range(n):
        o = 4 + i * 28
        flags = struct.unpack_from('<I', d, o)[0]
        a = struct.unpack_from('<3f', d, o + 4)
        b = struct.unpack_from('<3f', d, o + 16)
        if any(math.isnan(x) or abs(x) > 1e6 for x in a + b):
            problems.append('edg: record %d outside the world' % i)
            break
        # Hometown writes 0x00 on the barrier (monaco low byte). Skip nothing
        # that is a real segment — lane 0x00 edges are not emitted at all.
        if (flags & 0xFF) not in (0x00, 0x10, 0x30, 0x50, 0x60):
            continue
        walls.append((a, b))
        if math.dist(a, b) > 60:
            problems.append('edg: segment %.0f m long' % math.dist(a, b))
            break
    if not walls:
        problems.append('edg: no barriers')
    # Two wall polylines; a circuit also writes last-to-first. Fewer when
    # the polyline was broken at a jump in the wall distance.
    expected = 2 * (track.n_vroad if support.is_circuit(track)
                    else track.n_vroad - 1)
    if not expected * 0.97 <= n <= expected:
        problems.append('edg: %d records vs %d expected' % (n, expected))
    return walls


def check_folds(track, problems):
    """A barrier is the centre line pushed sideways by leftWall/rightWall. Where
    that offset exceeds the corner radius the polyline folds back on itself and
    the collision hull turns inside out. Counted here as segments that run
    against the direction of travel.

    Note what is deliberately NOT checked: that road geometry stays inside the
    barriers. It does not, and should not - NFS4's HIGH_RES_TRACK chunk reaches
    +-43 m from the centre line at the 99th percentile (shoulders, verges,
    run-off) while leftWall/rightWall sit near 12-16 m. The barrier is the
    collision boundary, not an outline of the visible surface.
    """
    folded = total = 0
    for i in range(track.n_vroad - 1):
        a, b = track.vroad(i), track.vroad(i + 1)
        for sign, key in ((-1.0, 'left_wall'), (1.0, 'right_wall')):
            pa = support._offset(a['ref'], a['right'], sign * a[key])
            pb = support._offset(b['ref'], b['right'], sign * b[key])
            seg = [pb[j] - pa[j] for j in range(3)]
            total += 1
            if sum(seg[j] * a['forward'][j] for j in range(3)) <= 0:
                folded += 1
    if folded > total * 0.01:
        problems.append('folded wall segments: %d of %d' % (folded, total))
    return folded, total


def support_slices():
    import nfs4_geometry
    return nfs4_geometry.SLICES_PER_BLOCK


def check(name):
    track = frd.read(os.path.join(HS, name, 'tr.frd'))
    out = os.path.join(BUILD, 'hs6', name.lower())
    spdfa = open(os.path.join(HS, name, 'spdfa.bin'), 'rb').read()
    spdra = open(os.path.join(HS, name, 'spdra.bin'), 'rb').read()
    forward_speed, _forward_aux, forward_line = support.decode_spd(
        spdfa, track.n_vroad)
    backward_speed, _backward_aux, backward_line = support.decode_spd(
        spdra, track.n_vroad)
    result = support.convert(track, name.lower(), out, spdfa, spdra,
                             open(DTX, 'rb').read())
    problems = []

    check_key(os.path.join(out, '%s_forward.key' % name.lower()),
              track.n_vroad, problems, 'forward.key')
    check_key(os.path.join(out, '%s_backwards.key' % name.lower()),
              track.n_vroad, problems, 'backwards.key')
    check_lsp(os.path.join(out, 'racer', 'work', '%s0.lsp' % name.lower()),
              track, forward_line, problems, 'forward.lsp')
    check_lsp(os.path.join(out, 'racer', 'work', '%s1.lsp' % name.lower()),
              track, backward_line, problems, 'backwards.lsp')
    if len(forward_speed) != track.n_vroad or len(backward_speed) != track.n_vroad:
        problems.append('spd: speed not expanded to the slice count')
    if name.lower() == 'hometown':
        check_hometown_island(track, forward_line, problems, 'forward.lsp')
        check_hometown_island(track, backward_line, problems, 'backwards.lsp')
    check_edg(os.path.join(out, '%s.edg' % name.lower()), track, problems)

    jnc = open(os.path.join(out, '%s.jnc' % name.lower()), 'rb').read()
    if len(jnc) % 36:
        problems.append('jnc: size not a multiple of 36')
    else:
        nz = sum(1 for v in struct.unpack_from('<8f', jnc, 4) if v)
        if result['circuit'] and nz < 2:
            problems.append('jnc: a circuit described as a dead end')
        if not result['circuit'] and nz != 1:
            problems.append('jnc: a point-to-point described as a circuit')
    has_start = os.path.exists(os.path.join(out, '%s_fstart.scn' % name.lower()))
    if result['circuit'] and has_start:
        problems.append('a circuit must not have _fstart.scn')

    folded, total = check_folds(track, problems)

    kf = open(os.path.join(out, '%s_forward.key' % name.lower()), 'rb').read()
    print('%-10s %s  slices %5d  edg %5d  key %3d points  folded %4d/%5d  %s'
          % (name, 'circuit' if result['circuit'] else 'p-to-p ', track.n_vroad,
             (os.path.getsize(os.path.join(out, '%s.edg' % name.lower())) - 4) // 28,
             struct.unpack_from('<I', kf, 4)[0], folded, total,
             'ok' if not problems else '; '.join(problems[:2])))
    return problems


if __name__ == '__main__':
    names = sys.argv[1:] or sorted(os.listdir(HS))
    fails = []
    for n in names:
        fails += check(n)
    print('\n' + ('ALL PASSED' if not fails else 'FAILURES'))
    sys.exit(1 if fails else 0)
