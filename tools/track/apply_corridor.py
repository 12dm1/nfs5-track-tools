"""AI1: write the real-wall AI corridor into installed converted-track CRPs.

    python apply_corridor.py --game <game folder> Gt1 France ...
    python apply_corridor.py --game ... --restore Gt1 ...   (back to NFS4 walls)

Patches only the SimD records' leftWall/rightWall/laneLeft/laneRight
(+0x40..+0x4C, see nfs4_geometry.build_sim_blobs) in place; every other byte
of the CRP stays as installed. The converter writes the same values
(nfs4_geometry.AI_CORRIDOR_FROM_WALLS).
"""
import argparse
import os
import struct
import sys

import nfs4_frd as frd
import nfs5_crp
import nfs5_track_files as track_files
import track_corridor
from convert_track import HS, track_directory


def simd_offset(crp):
    for m in crp.misc():
        if m['tag'] == 'Si' and m['length'] == m['count'] * 80:
            return m['data'], m['count']
    raise ValueError('no SimD in CRP')


def patch(game, name, restore=False, margin=track_corridor.MARGIN_M):
    src = os.path.join(HS, name)
    track = frd.read(os.path.join(src, 'tr.frd'))
    if restore:
        walls = [(track.vroad(i)['left_wall'], track.vroad(i)['right_wall'])
                 for i in range(track.n_vroad)]
    else:
        _s, _a, line = track_files.decode_spd(
            open(os.path.join(src, 'spdfa.bin'), 'rb').read(), track.n_vroad)
        walls = track_corridor.real_walls(track, line, margin)
    walls = track_corridor.widen_for_shortcuts(track, walls)[0]   # HZ5
    path = os.path.join(game, 'GameData', 'Track', '%s.crp' % track_directory(name))
    data = bytearray(open(path, 'rb').read())
    off, count = simd_offset(nfs5_crp.Crp(bytes(data)))
    if count != track.n_vroad:
        raise ValueError('%s: SimD has %d records, FRD %d slices' % (path, count, track.n_vroad))
    narrowed = 0
    for i, (left, right) in enumerate(walls):
        lane = track.vroad(i)['lane']
        o = off + i * 80 + 0x40
        old = struct.unpack_from('<2f', data, o)
        narrowed += (left < old[0] - 0.01) + (right < old[1] - 0.01)
        struct.pack_into('<4f', data, o, left, right, min(lane[0], left), min(lane[1], right))
    with open(path, 'wb') as f:
        f.write(data)
    return path, narrowed


def write_edg(game, name, restore=False):
    """GameData/Track/<track>.edg along the real walls (margin 0: the .edg does
    not stop a car, only the AI and the camera read it)."""
    src = os.path.join(HS, name)
    track = frd.read(os.path.join(src, 'tr.frd'))
    walls = None
    if not restore:
        _s, _a, line = track_files.decode_spd(
            open(os.path.join(src, 'spdfa.bin'), 'rb').read(), track.n_vroad)
        walls = track_corridor.real_walls(track, line, margin=0.0)
    else:
        walls = [(track.vroad(i)['left_wall'], track.vroad(i)['right_wall'])
                 for i in range(track.n_vroad)]
    # HZ5: open the barrier over shortcuts (track_corridor.widen_for_shortcuts).
    walls = track_corridor.widen_for_shortcuts(track, walls, symmetric_left=False)[0]
    path = os.path.join(game, 'GameData', 'Track', '%s.edg' % track_directory(name))
    blob = track_files.build_edg(track, walls)
    with open(path, 'wb') as f:
        f.write(blob)
    return path, len(blob)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tracks', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--margin', type=float, default=track_corridor.MARGIN_M)
    ap.add_argument('--edg', action='store_true', help='rewrite the .edg barrier instead of SimD')
    a = ap.parse_args()
    if a.edg:
        for name in a.tracks:
            path, size = write_edg(a.game, name, a.restore)
            print('%s: %s, %d bytes' % (os.path.basename(path),
                  'NFS4 walls' if a.restore else 'real-wall barrier', size))
        return 0
    for name in a.tracks:
        path, narrowed = patch(a.game, name, a.restore, a.margin)
        print('%s: %s, %d wall values changed' % (os.path.basename(path),
              'NFS4 walls restored' if a.restore else 'real-wall corridor', narrowed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
