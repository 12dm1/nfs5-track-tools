"""TX1: dump every track texture to PNG (RGBA) for HD regeneration.

    python export_track_textures.py [--out DIR]

Layout of DIR (default <work>/textures/tracks):
  <track>/<track>_<tag>.png   textures used by one track only
  common/<track>_<tag>.png    same pixels in two or more tracks; named after
                              the first track in alphabetical order
  manifest.csv                one row per (track, tag): file, size, pixel
                              format, rotation, pixel hash

Source is the installed GameData/Track/<track>.fsh (level 0 only), so the
converted High Stakes tracks come out exactly as the game draws them, with
tags 0000.. = FRD texture ids.

NFS4 orientation: there is no single rotation for High Stakes bitmaps. The
FRD builds every quad from implicit corner UVs, so a bitmap's "up" is set by
the polygon vertex order, per texture. Across the 19 tracks about 45 % of
the textures are upright, 35 % lie on their side one way, 10 % the other
way, a few upside-down. This script votes per texture over all its
near-vertical polygons (track chunks and objects): the bitmap direction
that points at world +Y. A texture is rotated upright when one direction
wins at least 60 % of the votes; ground-only and mixed textures stay as
they are. The applied rotation is written to manifest.csv (degrees
counter-clockwise) so a regenerated texture can be turned back before
packing. Rotation is off by default (--rotate-nfs4 turns it on); the default
dump keeps every bitmap exactly as stored in the FSH.
"""

import argparse
import collections
import csv
import hashlib
import math
import os
import sys

from PIL import Image

import nfs4_frd
from ea_refpack import fsh_dir, load_maybe_qfs
from nfs5_fsh import decode_pal8
import fsh_pixels
import nfs5cfg

ROOT = nfs5cfg.GAME
TRACK_DIR = os.path.join(ROOT, 'GameData', 'Track')
HS_DIR = nfs5cfg.HS_TRACKS
DEFAULT_OUT = os.path.join(nfs5cfg.WORK, 'textures', 'tracks')

# Installed PU name -> High Stakes source folder. Only Coastal was renamed
# (PU already has its own coastal track).
HS_SOURCE = {name: name.capitalize() for name in (
    'aquatica', 'atlantic', 'country', 'empire', 'france', 'germany', 'gt1',
    'gt2', 'gt3', 'hills', 'hometown', 'lostcany', 'park', 'redrock',
    'rockypas', 'snowy', 'summit', 'uk')}
HS_SOURCE['dolphin'] = 'Coastal'

VOTE_SHARE = 0.6
MIN_VOTES = 2
# Bitmap side that faces world up -> PIL rotate() angle (CCW) that puts it on top.
UPRIGHT_ANGLE = {'top': 0, 'right': 90, 'bottom': 180, 'left': 270}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(a):
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def up_side(poly, verts):
    """Bitmap side facing world +Y on a near-vertical quad, or None.

    UVs are the ones the converter hands PU (quad_uvs, V already flipped),
    with row 0 of the stored bitmap at v = 0. Checked by eye on Hometown,
    Coastal and Empire: trees, windows and doors voted 'top' are upright.
    """
    if max(poly['vertex']) >= len(verts):
        return None
    uv = nfs4_frd.quad_uvs(poly)
    p = [verts[i] for i in poly['vertex']]
    normal = _unit(_cross(_sub(p[1], p[0]), _sub(p[2], p[0])))
    if abs(normal[1]) > 0.3:
        return None
    du_y = dv_y = 0.0
    for i in range(4):
        for j in range(4):
            if uv[i][1] == uv[j][1] and uv[i][0] > uv[j][0]:
                du_y += _unit(_sub(p[i], p[j]))[1]
            if uv[i][0] == uv[j][0] and uv[i][1] > uv[j][1]:
                dv_y += _unit(_sub(p[i], p[j]))[1]
    if abs(dv_y) >= abs(du_y):
        if abs(dv_y) < 0.5:
            return None
        return 'bottom' if dv_y > 0 else 'top'
    if abs(du_y) < 0.5:
        return None
    return 'right' if du_y > 0 else 'left'


def hs_rotations(hs_name):
    """{texture id: CCW degrees} for one High Stakes track."""
    frd = nfs4_frd.read(os.path.join(HS_DIR, hs_name, 'tr.frd'))
    votes = collections.defaultdict(collections.Counter)

    def vote(polys, verts):
        for poly in polys:
            side = up_side(poly, verts)
            if side:
                votes[poly['texture_id']][side] += 1

    for block in range(frd.n_blocks):
        verts = frd.vertices(block)
        for chunk in range(11):
            vote(frd.polygons(block, chunk), verts)
        for obj in frd.objects(block):
            vote(obj['polygons'], obj['verts'])
    for obj in frd.global_objects_flat():
        vote(obj['polygons'], obj['verts'])

    out = {}
    for tex, counts in votes.items():
        side, n = counts.most_common(1)[0]
        total = sum(counts.values())
        if total >= MIN_VOTES and n >= VOTE_SHARE * total:
            out[tex] = UPRIGHT_ANGLE[side]
    return out


def entry_rgba(data, entry):
    if entry['code'] == 0x7B:                       # PAL8 + attached palette
        w, h, bgra = decode_pal8(data, entry)
        rgba = bytearray(bgra)
        rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]
        return bytes(rgba)
    return fsh_pixels.texture_rgba(data, entry)


def safe_tag(tag):
    return ''.join(c if c.isalnum() or c in '-_' else '_' for c in tag)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--rotate-nfs4', action='store_true',
                    help='turn NFS4 textures upright (off: bitmaps as stored)')
    args = ap.parse_args()

    tracks = sorted(f[:-4] for f in os.listdir(TRACK_DIR) if f.lower().endswith('.fsh'))
    images = []                          # (track, tag, entry, Image, angle, hash)
    for track in tracks:
        data = load_maybe_qfs(os.path.join(TRACK_DIR, track + '.fsh'))
        _head, entries = fsh_dir(data)
        hs = HS_SOURCE.get(track)
        rot = hs_rotations(hs) if hs and args.rotate_nfs4 else {}
        n_rot = 0
        for e in entries:
            img = Image.frombytes('RGBA', (e['w'], e['h']), entry_rgba(data, e))
            angle = 0
            if hs and e['tag'].isdigit():
                angle = rot.get(int(e['tag']), 0)
            if angle:
                img = img.rotate(angle, expand=True)
                n_rot += 1
            digest = hashlib.sha1(img.tobytes() + repr(img.size).encode()).hexdigest()[:16]
            images.append((track, e['tag'], e, img, angle, digest))
        print('%-10s %4d textures%s' % (track, len(entries),
              ('  (NFS4, %d rotated)' % n_rot) if hs else ''))

    owners = collections.defaultdict(set)
    first = {}
    for track, tag, _e, _img, _a, digest in images:
        owners[digest].add(track)
        first.setdefault(digest, '%s_%s' % (track, safe_tag(tag)))

    os.makedirs(args.out, exist_ok=True)
    written = set()
    rows = []
    for track, tag, e, img, angle, digest in images:
        shared = len(owners[digest]) > 1
        folder = 'common' if shared else track
        name = (first[digest] if shared else '%s_%s' % (track, safe_tag(tag))) + '.png'
        rel = folder + '/' + name
        if rel not in written:
            os.makedirs(os.path.join(args.out, folder), exist_ok=True)
            img.save(os.path.join(args.out, folder, name))
            written.add(rel)
        rows.append(dict(file=rel, track=track, tag=tag, width=e['w'], height=e['h'],
                         code='%#04x' % e['code'], source='nfs4' if track in HS_SOURCE else 'pu',
                         rotation_ccw=angle, shared_tracks=len(owners[digest]), hash=digest))

    with open(os.path.join(args.out, 'manifest.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n_common = len({r['file'] for r in rows if r['file'].startswith('common/')})
    print('%d entries -> %d PNG files (%d in common/) in %s'
          % (len(rows), len(written), n_common, args.out))


if __name__ == '__main__':
    sys.exit(main())
