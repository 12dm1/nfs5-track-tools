"""HS6: collision, AI route and start/finish files for a converted track.

Everything here is derived from the NFS4 vroad spline, which carries exactly
what Porsche Unleashed needs: a centre point, an orthonormal basis, the
distance to the barrier on each side and the drivable lane half-width.

  `.edg`   uint32 header + 28-byte entries (flags + float3 start + float3 end).
           One barrier polyline per side at leftWall/rightWall, flags 0x00
           (monaco's low byte). 0x10/0x30 did not stop the car on Hometown.
           Lane-width edges are not written: they sit on the painted line
           and the chase/intro camera treats them as a wall.
           Circuits also get the last-to-first segment so the ring closes.
           The header is not the entry count - the loader derives that from the
           file size - and its meaning is unknown (it correlates with neither
           entry count nor track length across the 15 shipped tracks), so the
           entry count is written and nothing depends on it.

  `.key`   version 1, count, then 400 slots of (uint32 slice, float speed).
           The last counted slot is the sentinel (100000, 600.0) and unused
           slots are (0xFFFFFFFF, 400.0), as the originals do.

  `.jnc`   36 bytes per junction: uint32 index + 8 lane probabilities. PU tells
           a circuit from a point-to-point track by the topology here: a dead
           end shows up as a single nonzero float, a closed loop as two or more
           (T007). All 19 High Stakes tracks turn out to be closed loops - the
           gap between the first and last vroad point is one step, ~4.6 m - so
           they get skidpad's single [1, 1, 0...] entry.

  `_fstart.scn` / `_bstart.scn`
           Only written for a track that is NOT a loop. PU's own circuits ship
           without them (none of monaco1-5 has one), so a converted loop must
           not have them either.
           TRIGGER_ELEMENT version 5, CRLF line endings. Basis vectors are
           printed as three tab-separated groups - forward, up, right - and the
           two gate posts are pos +- right * width/2, in that order.

The NFS4 `spdf?.bin` layout is:

    ceil(nVRoad / 2) uint8  speed (one value for every two vroad slices)
    nVRoad           uint8  auxiliary AI values (semantics still unknown)
    nVRoad           float  signed lateral racing-line offset in metres

The lateral offset is measured along the vroad `right` vector.  It is the
authored NFS4 racing line, not another speed table: on Hometown it moves the
line around the boulevard island that the vroad centre intersects and reaches
the inside of the 90-degree city corner before the centre line turns.

AI speed units are provisional; see AI_SPEED_SCALE.
"""

import math
import os
import struct

EDG_FLAG_ROAD = 0x00
# Monaco stores almost every barrier with low byte 0x00 (the upper bits are a
# per-entry payload). Alps uses 0x10/0x30 for some walls, but 0x10 and 0x30 on
# Hometown never stopped the car — FUN_00487e80 only copies the low byte, and
# the car vs-edge path (FUN_004740d0 / FUN_00473ba0) does not filter on it the
# way T007 assumed. Match monaco: 0x00 on the leftWall/rightWall polyline.
# Lane-width 0x00 edges are still omitted (they jerked the chase camera).
EDG_FLAG_WALL = 0x00
KEY_SLOTS = 400
KEY_SENTINEL_SLICE = 100000
KEY_SENTINEL_SPEED = 600.0
KEY_UNUSED = (0xFFFFFFFF, 400.0)
KEY_MAX_SPEED = 600.0
KEY_MIN_SPEED = 120.0

# NFS4 stores one AI speed byte per two vroad points (18..160 across the 19
# tracks, mean 82); PU stores floats (149..600, mean 371). The scale below was fitted
# offline rather than guessed: for both games the AI speed at a corner tracks
# sqrt(radius), so fitting v = k*sqrt(R) on each side and dividing gives the
# unit ratio. Over 19912 NFS4 samples and 283 PU samples that came out at 4.30,
# against 4.52 by matching means and 3.75 by matching maxima.
#
# To refine it in game: write a .key holding one constant, drive, and read the
# `speed` column of dx11z_netsync.csv.
AI_SPEED_SCALE = 4.30


def decode_spd(blob, n_slices):
    """Decode one NFS4 spdfa/spdra file.

    Returns `(speed_bytes, auxiliary, lateral_offsets)`.  Speeds are expanded
    to one value per slice; the source stores one byte for each pair of slices.
    The auxiliary byte array is preserved for future research but is not used
    by the PU conversion.
    """
    n_speed = (n_slices + 1) // 2
    expected = n_speed + n_slices + n_slices * 4
    if len(blob) != expected:
        raise ValueError('spd size %d, expected %d for %d slices'
                         % (len(blob), expected, n_slices))

    packed_speeds = blob[:n_speed]
    auxiliary = blob[n_speed:n_speed + n_slices]
    lateral = struct.unpack_from('<%df' % n_slices, blob,
                                 n_speed + n_slices)
    if any(not math.isfinite(value) for value in lateral):
        raise ValueError('spd racing line contains a non-finite offset')

    speeds = [packed_speeds[min(i // 2, n_speed - 1)]
              for i in range(n_slices)]
    return speeds, auxiliary, lateral


def _axes(vr):
    return vr['ref'], vr['forward'], vr['normal'], vr['right']


def _offset(ref, right, distance):
    return tuple(ref[i] + right[i] * distance for i in range(3))


# --------------------------------------------------------------------- edg ---

def _segments(flags, points, max_segment, close):
    """Polyline to edg entries. Skip jumps longer than max_segment."""
    pairs = list(zip(points, points[1:]))
    if close and len(points) > 2:
        pairs.append((points[-1], points[0]))
    out = []
    for a, b in pairs:
        if math.dist(a, b) > max_segment:
            continue
        out.append((flags, a, b))
    return out


def build_edg(track, walls=None):
    """Left and right barrier polylines along the vroad walls, or along
    `walls` [(left, right)] per slice (AI1: the real-wall corridor)."""
    left, right = [], []
    for i in range(track.n_vroad):
        vr = track.vroad(i)
        ref, _fwd, _up, right_ax = _axes(vr)
        lw, rw = walls[i] if walls else (vr['left_wall'], vr['right_wall'])
        left.append(_offset(ref, right_ax, -lw))
        right.append(_offset(ref, right_ax, rw))

    # NFS4 evaluates the barrier as a per-slice scalar, so a sudden jump in
    # leftWall/rightWall - the track opening into a plaza - costs it nothing.
    # PU wants a polyline, where the same jump becomes one long diagonal
    # segment walling off an area that was open. The polyline is broken there
    # instead: an opening in the barrier is what an opening is.
    step = track.road_length() / max(1, track.n_vroad - 1)
    max_segment = step * 3.0
    close = is_circuit(track)

    entries = _segments(EDG_FLAG_WALL, left, max_segment, close)
    entries += _segments(EDG_FLAG_WALL, right, max_segment, close)
    out = bytearray(struct.pack('<I', len(entries)))
    for flags, a, b in entries:
        out += struct.pack('<I6f', flags, a[0], a[1], a[2], b[0], b[1], b[2])
    return bytes(out)


# --------------------------------------------------------------------- key ---

KEY_BUDGET = 120        # PU's own tracks use 12..89 of the 400 slots


def _simplify(profile, budget):
    """Keep the points where the speed profile actually turns, within budget."""
    tolerance = 1.0
    while True:
        kept = [0]
        last = profile[0]
        for i in range(1, len(profile) - 1):
            if abs(profile[i] - last) >= tolerance:
                kept.append(i)
                last = profile[i]
        kept.append(len(profile) - 1)
        if len(kept) <= budget or tolerance > 1000:
            return kept
        tolerance *= 1.4


def build_key(spd_blob, n_slices, scale=None, budget=None):
    """NFS4 packed AI speeds -> a PU .key file.

    Both spdfa and spdra use geographic vroad slice order.  The game traverses
    those slices in the selected direction, so the reverse profile must not be
    reversed a second time here.
    """
    speed_bytes, _auxiliary, _lateral = decode_spd(spd_blob, n_slices)
    scale = AI_SPEED_SCALE if scale is None else scale
    budget = KEY_BUDGET if budget is None else budget
    speeds = [min(KEY_MAX_SPEED, max(KEY_MIN_SPEED, b * scale))
              for b in speed_bytes]
    kept = _simplify(speeds, min(budget, KEY_SLOTS - 1))

    out = bytearray(struct.pack('<II', 1, len(kept) + 1))
    for i in kept:
        out += struct.pack('<If', i, speeds[i])
    out += struct.pack('<If', KEY_SENTINEL_SLICE, KEY_SENTINEL_SPEED)
    for _ in range(KEY_SLOTS - len(kept) - 1):
        out += struct.pack('<If', *KEY_UNUSED)
    return bytes(out)


# --------------------------------------------------------------------- lsp ---

def build_lsp(track, lateral_offsets):
    """The racing line: `uint32 1` then one (X, Z) pair per slice.

    Lives in GameData/Track/racer/work/<name><direction>.lsp, and the engine
    builds that name as sprintf("%s%s%d.lsp", path, track, direction) at
    00403281. A missing one is not reported as a missing file: the resource
    ends up with an empty name and the load fails with "FILE_loadpack - unable
    to unpack file", which is how a converted track died once its geometry and
    sim data were accepted.

    The point count matches SimD exactly.  Shipped forward and reverse files
    share slice order and differ in their lateral line.  NFS4 stores the same
    information as one signed float per vroad point in spdfa/spdra.
    """
    if len(lateral_offsets) != track.n_vroad:
        raise ValueError('racing line has %d offsets for %d slices'
                         % (len(lateral_offsets), track.n_vroad))
    out = bytearray(struct.pack('<I', 1))
    for i in range(track.n_vroad):
        vr = track.vroad(i)
        point = _offset(vr['ref'], vr['right'], lateral_offsets[i])
        out += struct.pack('<2f', point[0], point[2])
    return bytes(out)


# --------------------------------------------------------------------- jnc ---

def is_circuit(track):
    """A closed loop: the last vroad point sits one step from the first."""
    a, b = track.vroad(0)['ref'], track.vroad(track.n_vroad - 1)['ref']
    gap = math.dist(a, b)
    step = track.road_length() / (track.n_vroad - 1)
    return gap < step * 2


def build_jnc(circuit=True):
    """Junction topology. A loop is one junction with two connections, the way
    skidpad does it; a point-to-point track is two dead ends."""
    if circuit:
        return struct.pack('<I8f', 0, 1.0, 1.0, 0, 0, 0, 0, 0, 0)
    out = bytearray()
    for i in range(2):
        out += struct.pack('<I8f', i, 1.0, 0, 0, 0, 0, 0, 0, 0)
    return bytes(out)


# --------------------------------------------------------------------- scn ---

def _vec(v):
    return '%f %f %f' % (v[0], v[1], v[2])


def _trigger(pos, forward, up, right, width, kind):
    left = _offset(pos, right, width / 2.0)
    other = _offset(pos, right, -width / 2.0)
    return '\r\n'.join([
        'TRIGGER_ELEMENT 5',
        _vec(pos),
        '\t'.join([_vec(forward), _vec(up), _vec(right)]),
        '%d 1 1.000000 %f 0.250000 0.000000 200.000000 -1 -1 -1 -1 -1 -1 0 65535 0 0'
        % (kind, width),
        '\t'.join([_vec(left), _vec(other)]),
        '\t'.join(['0 0.000000 0.000000 0.000000'] * 2),
        '', ''])


def build_start_scn(track, backwards=False):
    """Start gate and finish gate. Trigger type 2 starts, type 1 finishes.

    The GEOM_ELEMENTs the originals add are the finish-line props; they point
    at articles of the shared object library that a converted track does not
    have, so they are left out rather than dangling.
    """
    first, last = 0, track.n_vroad - 1
    order = [(last, 2), (first, 1)] if backwards else [(first, 2), (last, 1)]
    text = ''
    for index, kind in order:
        vr = track.vroad(index)
        ref, fwd, up, right = _axes(vr)
        if backwards:
            fwd = tuple(-c for c in fwd)
            right = tuple(-c for c in right)
        # The gate spans the drivable road, not the gap between barriers:
        # leftWall/rightWall can be 20 m or more on an open stretch.
        width = sum(vr['lane'])
        text += _trigger(ref, fwd, up, right, width, kind)
    return text.encode('latin1')


# ---------------------------------------------------------------- pipeline ---

def convert(track, name, out_dir, spdfa, spdra, dtx_template):
    """Write every support file for one converted track. Returns a summary."""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'Cops'), exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'racer', 'work'), exist_ok=True)
    written = {}

    def put(filename, blob):
        with open(os.path.join(out_dir, filename), 'wb') as f:
            f.write(blob)
        written[filename] = len(blob)

    # HZ5: the barrier the AI and camera read opens up over shortcuts.
    import track_corridor
    vroad_walls = [(track.vroad(i)['left_wall'], track.vroad(i)['right_wall'])
                   for i in range(track.n_vroad)]
    put('%s.edg' % name, build_edg(track, track_corridor.widen_for_shortcuts(
        track, vroad_walls, symmetric_left=False)[0]))
    _forward_speed, _forward_aux, forward_line = decode_spd(spdfa, track.n_vroad)
    _backward_speed, _backward_aux, backward_line = decode_spd(spdra, track.n_vroad)
    # build_key validates/decodes the blobs too.  Keep the decoded values above
    # because the same source files also carry the two authored racing lines.
    put('%s_forward.key' % name, build_key(spdfa, track.n_vroad))
    put('%s_backwards.key' % name, build_key(spdra, track.n_vroad))
    circuit = is_circuit(track)
    put('%s.jnc' % name, build_jnc(circuit))
    if not circuit:
        put('%s_fstart.scn' % name, build_start_scn(track))
        put('%s_bstart.scn' % name, build_start_scn(track, backwards=True))
    lines = (forward_line, backward_line)
    for direction, lateral in enumerate(lines):
        put(os.path.join('racer', 'work', '%s%d.lsp' % (name, direction)),
            build_lsp(track, lateral))
    put('%s.dtx' % name, dtx_template)
    return dict(files=written, circuit=circuit)


if __name__ == '__main__':
    import sys

    import nfs4_frd as frd

    src, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
    track = frd.read(os.path.join(src, 'tr.frd'))
    spdfa = open(os.path.join(src, 'spdfa.bin'), 'rb').read()
    spdra = open(os.path.join(src, 'spdra.bin'), 'rb').read()
    import nfs5cfg
    dtx = open(os.path.join(nfs5cfg.GAME, 'GameData', 'Track', 'alps.dtx'), 'rb').read()
    result = convert(track, name, out, spdfa, spdra, dtx)
    print('track: %s' % ('circuit' if result['circuit'] else 'point-to-point'))
    for fn, size in result['files'].items():
        print('  %-26s %8d bytes' % (fn, size))
