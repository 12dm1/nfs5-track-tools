"""Build the NFS4 ambience for converted tracks without rebuilding the tracks.

    python build_ambient.py --all --install <game folder>
    python build_ambient.py Hills Park --install <game folder>
    python build_ambient.py --all                  (output only, in build/ambient)

Writes `GameData/Track/<dir>_audio.scn` for the tracks asked for and always
`GameData/Sounds/trackext.viv` with the banks of all 19 tracks plus
`GameData/Track/animdefs.txt` with the sounds of animated objects (see
nfs5_ambient.build_all for why).  With --install only those files are copied
into the game.  Everything about the sound pipeline: docs/pipeline/ambient-sound.md.
"""

import argparse
import os
import shutil
import sys

import nfs5_ambient as ambient
import nfs5cfg

GAME = nfs5cfg.GAME
# Must match convert_track.TRACK_DIRECTORIES: NFS4 Coastal is Dolphin Cove and
# must not land on PU's own coastal.
TRACK_DIRECTORIES = {'coastal': 'dolphin'}


def directory_of(name):
    return TRACK_DIRECTORIES.get(name.lower(), name.lower())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tracks', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--out', default=os.path.join(nfs5cfg.WORK, 'build', 'ambient'))
    ap.add_argument('--install', metavar='GAME_DIR')
    args = ap.parse_args()

    names = ambient.hs_track_names(GAME)
    wanted = names if args.all else args.tracks
    unknown = [n for n in wanted if n.lower() not in {x.lower() for x in names}]
    if unknown:
        ap.error('unknown tracks: %s (have: %s)' % (', '.join(unknown), ', '.join(names)))
    if not wanted:
        ap.error('name tracks or pass --all')

    out = os.path.normpath(args.out)
    stats, banks = ambient.build_all(GAME, wanted, out, directory_of)
    print('\narchive: %d banks in GameData/Sounds/trackext.viv' % banks)

    if args.install:
        files = [os.path.join('GameData', 'Sounds', 'trackext.viv'),
                 os.path.join('GameData', 'Track', 'animdefs.txt')]
        files += [os.path.relpath(st['scn'], out) for st in stats.values() if 'scn' in st]
        for rel in files:
            dst = os.path.join(args.install, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(out, rel), dst)
        print('installed %d files into %s' % (len(files), args.install))
    return 0


if __name__ == '__main__':
    sys.exit(main())
