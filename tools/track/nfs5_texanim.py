"""HZ4: NFS4 animated textures -> GameData/Track/<track>.tan for the wrapper.

NFS4 animates a texture from the polygon's anim byte (nfshs.exe
FUN_00434690, 1/64 s clock):

  frames  anim & 7 frames, anim >> 3 ticks each; the frames are consecutive
          texture ids of tr0.qfs (Aquatica bubbles 151..153, water 165..170,
          Redrock/Hometown/Country 4-frame water)
  scroll  with texflags 0x2000 instead: v += (clock & 0x3FF) / 1024 * 15
          (Dolphin Cove 168/197, Snowy 107/108)

PU has neither, so the DX11 wrapper does it (wrapper/src/tex_anim.cpp): when
the game uploads a base texture it creates the other frames from this file
and swaps them in at bind time; scrolls shift v at draw time.  Within a
track every animated texture is used by animated polygons only (Lost Canyons
224 has 6 static ones of 35), so animating the texture is equivalent.

The frames are read from the installed GameData/Track/<track>.fsh, so any
replaced or upscaled texture keeps its size.

.tan layout, little-endian:
    char magic[4] = 'TAN1', uint32 count, then per entry
    char tag[4], uint8 kind (1 frames, 2 scroll), uint8 frames,
    uint16 delay (ticks of 1/64 s), float scroll (v per tick),
    uint16 w, uint16 h, (frames - 1) * w * h * 4 bytes BGRA (frames 1..n-1)

    python nfs5_texanim.py --all [--install <game folder>]
"""

import argparse
import collections
import os
import shutil
import struct
import sys

import nfs4_frd as frd
from ea_refpack import fsh_dir, load_maybe_qfs
from nfs5_fsh import CODE_ARGB8888, index_tag
import nfs5cfg

GAME = nfs5cfg.GAME
HS = nfs5cfg.HS_TRACKS
KIND_FRAMES, KIND_SCROLL = 1, 2
SCROLL_PER_TICK = 15.0 / 1024.0     # nfshs.exe: (clock & 0x3FF) * (1/1024) * 15
MAX_FRAMES = 7                      # anim & 7


def _all_polygons(track):
    for b in range(track.n_blocks):
        for c in range(11):
            yield from track.polygons(b, c)
        for obj in track.objects(b):
            yield from obj['polygons']
    for obj in track.global_objects_flat():
        yield from obj['polygons']


def animated_textures(track):
    """{texture id: (kind, frames, delay)} from the most common anim byte."""
    votes = collections.defaultdict(collections.Counter)
    for poly in _all_polygons(track):
        if poly['anim_flags']:
            votes[poly['texture_id']][(poly['anim_flags'],
                                       bool(poly['texflags'] & 0x2000))] += 1
    out = {}
    for tex, counter in votes.items():
        (anim, scroll), _n = counter.most_common(1)[0]
        frames, delay = anim & 7, anim >> 3
        if scroll:
            out[tex] = (KIND_SCROLL, 1, max(delay, 1))
        elif frames >= 2:
            out[tex] = (KIND_FRAMES, frames, max(delay, 1))
    return out


def _level0(data, entries, tag):
    entry = next((e for e in entries if e['tag'] == tag), None)
    if entry is None or entry['code'] & 0x7F != CODE_ARGB8888 & 0x7F:
        return None
    w, h = entry['w'], entry['h']
    off = entry['off'] + 16
    return w, h, bytes(data[off:off + w * h * 4])


def build_tan(nfs4_track, fsh_path):
    """-> (.tan bytes, [(tag, kind, frames, delay)])"""
    track = frd.read(os.path.join(HS, nfs4_track, 'tr.frd'))
    data = load_maybe_qfs(fsh_path)
    _head, entries = fsh_dir(data)
    body, listed = [], []
    for tex, (kind, frames, delay) in sorted(animated_textures(track).items()):
        base = _level0(data, entries, index_tag(tex))
        if base is None:
            continue
        w, h, _px = base
        pixels = []
        for k in range(1, frames):
            frame = _level0(data, entries, index_tag(tex + k))
            if frame is None or frame[:2] != (w, h):
                break
            pixels.append(frame[2])
        if kind == KIND_FRAMES and len(pixels) != frames - 1:
            # Lost Canyons 60 asks for 5 frames, the fifth texture is of
            # another size: play the ones that match.
            print('HZ4: %s texture %d: %d of %d frames usable'
                  % (nfs4_track, tex, len(pixels) + 1, frames))
            frames = len(pixels) + 1
            if frames < 2:
                continue
        body.append(struct.pack('<4sBBHfHH', index_tag(tex).encode('latin1'), kind,
                                frames, delay,
                                SCROLL_PER_TICK if kind == KIND_SCROLL else 0.0, w, h))
        body.extend(pixels)
        listed.append((index_tag(tex), kind, frames, delay))
    return b'TAN1' + struct.pack('<I', len(listed)) + b''.join(body), listed


def directory_of(name):
    return {'coastal': 'dolphin'}.get(name.lower(), name.lower())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tracks', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--out', default=os.path.join(nfs5cfg.WORK, 'build', 'texanim'))
    ap.add_argument('--install', metavar='GAME_DIR')
    args = ap.parse_args()
    names = sorted(d for d in os.listdir(HS) if os.path.exists(os.path.join(HS, d, 'tr.frd')))
    wanted = names if args.all else args.tracks
    if not wanted:
        ap.error('name tracks or pass --all')
    os.makedirs(args.out, exist_ok=True)
    for name in wanted:
        directory = directory_of(name)
        fsh = os.path.join(args.install or GAME, 'GameData', 'Track', '%s.fsh' % directory)
        blob, listed = build_tan(name, fsh)
        path = os.path.join(args.out, '%s.tan' % directory)
        target = os.path.join(args.install, 'GameData', 'Track', '%s.tan' % directory) \
            if args.install else None
        if not listed:
            # Nothing animates: no file, and none left over from a run before.
            if target and os.path.exists(target):
                os.remove(target)
            continue
        with open(path, 'wb') as f:
            f.write(blob)
        if target:
            shutil.copy2(path, target)
        print('%-9s %2d textures  %s' % (name, len(listed), ' '.join(
            '%s:%s%d/%d' % (t, 'S' if k == KIND_SCROLL else 'F', n, d)
            for t, k, n, d in listed)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
