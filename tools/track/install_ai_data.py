"""Install the tuned AI data of the converted tracks (data/ai) into a game.

    python install_ai_data.py --install <game folder> [--dry-run]
    python install_ai_data.py --install <game folder> hometown gt1

convert_track.py writes first-approximation racing lines (NFS4 lines, see
HS6); the files in data/ai are the result of the AI1/AI6 tuning and replace
them:

    data/ai/<track>.aispeed, <track>_r.aispeed   -> GameData/Track/
    data/ai/<track>.aiprofile                    -> GameData/Track/
    data/ai/aiprofile_cars.txt                   -> GameData/Track/
    data/ai/lines/<track><dir>.lsp               -> GameData/Track/racer/work/

<track> is the PU directory name (NFS4 Coastal = dolphin). The runtime reads
.aispeed/.aiprofile/aiprofile_cars.txt (docs/runtime.md); the .lsp files are
stock PU racing lines. Run it after convert_track.py --install. A file that
already exists in the game is copied to <file>.pre_ai once (never
overwritten later), so the original can be restored by hand.
"""

import argparse
import os
import shutil
import sys

import nfs5cfg

AI_DIR = os.path.join(nfs5cfg.DATA, 'ai')


def plan(tracks=None):
    """[(source, path relative to the game folder)]"""
    want = {t.lower() for t in tracks} if tracks else None
    out = []
    for name in sorted(os.listdir(AI_DIR)):
        src = os.path.join(AI_DIR, name)
        if not os.path.isfile(src):
            continue
        if name == 'aiprofile_cars.txt':
            out.append((src, os.path.join('GameData', 'Track', name)))
            continue
        stem, ext = os.path.splitext(name)
        if ext not in ('.aispeed', '.aiprofile'):
            continue
        track = stem[:-2] if stem.endswith('_r') else stem
        if want is None or track in want:
            out.append((src, os.path.join('GameData', 'Track', name)))
    lines = os.path.join(AI_DIR, 'lines')
    for name in sorted(os.listdir(lines)):
        stem, ext = os.path.splitext(name)
        if ext != '.lsp' or (want is not None and stem[:-1] not in want):
            continue
        out.append((os.path.join(lines, name),
                    os.path.join('GameData', 'Track', 'racer', 'work', name)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tracks', nargs='*', help='PU track directory names (default: all)')
    ap.add_argument('--install', metavar='GAME_DIR', default=None,
                    help='game folder (default: [paths] game from nfs5tools.ini)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    game = args.install or nfs5cfg.require_game()
    if not os.path.isdir(os.path.join(game, 'GameData', 'Track')):
        ap.error('%s has no GameData/Track' % game)
    files = plan(args.tracks)
    if not files:
        ap.error('nothing to install for: %s' % ' '.join(args.tracks))
    for src, rel in files:
        dst = os.path.join(game, rel)
        if args.dry_run:
            print('would copy %s -> %s' % (os.path.relpath(src, nfs5cfg.REPO), rel))
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst) and not os.path.exists(dst + '.pre_ai'):
            shutil.copy2(dst, dst + '.pre_ai')
        shutil.copy2(src, dst)
    print('%s %d files' % ('planned' if args.dry_run else 'installed', len(files)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
