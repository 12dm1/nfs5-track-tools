"""AI1: rewrite the AI speed profiles (.key) of installed converted tracks.

    python apply_key.py --game <game folder> [--scale 4.30] [--budget 120] Gt1 ...

Defaults reproduce the shipped conversion (nfs5_track_files.build_key).
"""
import argparse
import os
import sys

import nfs4_frd as frd
import nfs5_track_files as tf
from convert_track import HS, track_directory


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tracks', nargs='+')
    ap.add_argument('--game', required=True)
    ap.add_argument('--scale', type=float, default=tf.AI_SPEED_SCALE)
    ap.add_argument('--budget', type=int, default=tf.KEY_BUDGET)
    a = ap.parse_args()
    for name in a.tracks:
        src = os.path.join(HS, name)
        track = frd.read(os.path.join(src, 'tr.frd'))
        d = track_directory(name)
        for spd, suffix in (('spdfa.bin', '_forward'), ('spdra.bin', '_backwards')):
            blob = tf.build_key(open(os.path.join(src, spd), 'rb').read(), track.n_vroad,
                                a.scale, a.budget)
            with open(os.path.join(a.game, 'GameData', 'Track', d + suffix + '.key'), 'wb') as f:
                f.write(blob)
        print('%s: key scale %.2f, budget %d' % (d, a.scale, a.budget))
    return 0


if __name__ == '__main__':
    sys.exit(main())
