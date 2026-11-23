"""TX2: NFS4 track textures, upscaled x4, turned upright, mirrored halves made whole.

    python export_nfs4_textures_x4.py [--out DIR] [--scale 4]

Same source and layout as export_track_textures.py (TX1), but only the 19
converted High Stakes tracks:

  <track>/<track>_<tag>.png   textures used by one NFS4 track only
  common/<track>_<tag>.png    same pixels in two or more NFS4 tracks
  <name>.txt                  next to every texture that was rotated and/or
                              mirrored: what was done and how to undo it
  manifest.csv                one row per (track, tag)

Steps per texture, in this order:
  1. mirror: the bitmap is half of an object (half a fir tree, half a facade)
     when the FRD draws it on a pair of quads that share one bitmap edge and
     are mirror images across it. The whole object is stored bitmap + flipped
     copy across that edge, so the image doubles in width or height.
  2. rotate: turned upright (90/180/270 CCW) when nearly all its quads stand
     upright in the world and agree on which bitmap side faces up.
  3. upscale: Lanczos x4 on premultiplied alpha (no colour-key fringes);
     alpha is kept (soft after scaling).

Mirror test, over all quads of a texture (same bitmap pixels pooled across
tracks): a quad is "paired" when some other quad of the same object (or of
the track mesh) shares an edge with it, both map that edge to the same
bitmap side and the same texel ends. It is "chained" when its opposite side
is shared too. Half objects: >= 50 % of quads paired on one side and at most
30 % of those chained. Road, terrain and wall strips that NFS4 mirrors every
other quad form chains and are left alone.

Rotation test: the up-vote of export_track_textures.up_side() over every
near-vertical quad; rotate when >= 75 % of all quads vote and >= 75 % of the
votes pick the same side.
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
from export_track_textures import (HS_DIR, HS_SOURCE, ROOT, TRACK_DIR, UPRIGHT_ANGLE,
                                   entry_rgba, safe_tag, up_side)
import nfs5cfg

DEFAULT_OUT = os.path.join(nfs5cfg.WORK, 'textures', 'nfs4_x4')

OPPOSITE = {'left': 'right', 'right': 'left', 'top': 'bottom', 'bottom': 'top'}
ROT_VERTICAL_SHARE = 0.75
ROT_VOTE_SHARE = 0.75
MIRROR_PAIRED_SHARE = 0.5
MIRROR_CHAIN_SHARE = 0.3


def _edge_side(a, b):
    """Bitmap side an edge lies on, from the UVs of its ends (None = diagonal)."""
    if a[0] == b[0]:
        return 'left' if a[0] == 0 else 'right'
    if a[1] == b[1]:
        return 'top' if a[1] == 0 else 'bottom'
    return None


class TexStats:
    def __init__(self):
        self.n = 0                                  # quads using the texture
        self.up = collections.Counter()             # up_side votes
        self.paired = collections.Counter()         # side -> quads with a mirror twin
        self.chained = collections.Counter()        # ... whose opposite side is shared too

    def add(self, other):
        self.n += other.n
        self.up.update(other.up)
        self.paired.update(other.paired)
        self.chained.update(other.chained)

    def rotation(self):
        """(CCW degrees, reason)."""
        votes = sum(self.up.values())
        if votes < 2:
            return 0, 'not on upright quads'
        side, n = self.up.most_common(1)[0]
        if votes < ROT_VERTICAL_SHARE * self.n:
            return 0, 'only %d of %d quads upright' % (votes, self.n)
        if n < ROT_VOTE_SHARE * votes:
            return 0, 'up side mixed %s' % dict(self.up)
        return UPRIGHT_ANGLE[side], '%d of %d upright quads have the %s side up' % (n, votes, side)

    def mirror(self):
        """Bitmap side the mirror copy attaches to, or None."""
        if not self.paired:
            return None
        side, n = self.paired.most_common(1)[0]
        if n < 2 or n < MIRROR_PAIRED_SHARE * self.n:
            return None
        if self.chained[side] > MIRROR_CHAIN_SHARE * n:
            return None
        return side


def hs_stats(hs_name):
    """{texture id: TexStats} for one High Stakes track."""
    frd = nfs4_frd.read(os.path.join(HS_DIR, hs_name, 'tr.frd'))
    groups = []                       # (polys, verts, group id); edges match inside a group
    for block in range(frd.n_blocks):
        verts = frd.vertices(block)
        groups.append(([p for c in range(11) for p in frd.polygons(block, c)], verts, 'track'))
        for k, obj in enumerate(frd.objects(block)):
            groups.append((obj['polygons'], obj['verts'], (block, k)))
    for k, obj in enumerate(frd.global_objects_flat()):
        groups.append((obj['polygons'], obj['verts'], ('global', k)))

    stats = collections.defaultdict(TexStats)
    edges = collections.defaultdict(list)      # (tex, edge key) -> [(quad, side, {pos: uv})]
    quads = []
    for polys, verts, gid in groups:
        for poly in polys:
            if max(poly['vertex']) >= len(verts):
                continue
            pos = [(gid,) + tuple(round(c, 2) for c in verts[i]) for i in poly['vertex']]
            if len(set(pos)) < 4:
                continue
            tex = poly['texture_id']
            st = stats[tex]
            st.n += 1
            side = up_side(poly, verts)
            if side:
                st.up[side] += 1
            uv = nfs4_frd.quad_uvs(poly)
            quad = dict(tex=tex, corners=frozenset(pos), sides={})
            quads.append(quad)
            for i in range(4):
                j = (i + 1) % 4
                s = _edge_side(uv[i], uv[j])
                if s:
                    key = frozenset((pos[i], pos[j]))
                    quad['sides'][s] = key
                    edges[(tex, key)].append((quad, s, {pos[i]: uv[i], pos[j]: uv[j]}))

    def neighbours(quad, key):
        return [e for e in edges[(quad['tex'], key)]
                if e[0] is not quad and e[0]['corners'] != quad['corners']]

    for quad in quads:
        for s, key in quad['sides'].items():
            mine = next(e[2] for e in edges[(quad['tex'], key)] if e[0] is quad)
            if any(so == s and all(uo[p] == mine[p] for p in key)
                   for _q, so, uo in neighbours(quad, key)):
                st = stats[quad['tex']]
                st.paired[s] += 1
                far = quad['sides'].get(OPPOSITE[s])
                if far and neighbours(quad, far):
                    st.chained[s] += 1
    return stats


def make_whole(img, side):
    """Bitmap + its mirror copy across `side`."""
    w, h = img.size
    if side in ('left', 'right'):
        flip = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        out = Image.new('RGBA', (w * 2, h))
        out.paste(flip if side == 'left' else img, (0, 0))
        out.paste(img if side == 'left' else flip, (w, 0))
    else:
        flip = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        out = Image.new('RGBA', (w, h * 2))
        out.paste(flip if side == 'top' else img, (0, 0))
        out.paste(img if side == 'top' else flip, (0, h))
    return out


def upscale(img, k):
    """Lanczos on premultiplied RGBA, so colour-key texels do not bleed in."""
    if k == 1:
        return img
    size = (img.width * k, img.height * k)
    pm = img.convert('RGBa').resize(size, Image.Resampling.LANCZOS).convert('RGBA')
    # Fully transparent texels: keep the original colour key (nearest).
    key = img.resize(size, Image.Resampling.NEAREST)
    alpha = pm.getchannel('A')
    clear = alpha.point(lambda a: 255 if a == 0 else 0)
    pm.paste(key, (0, 0), clear)
    pm.putalpha(alpha)
    return pm


# Which part of the whole image (before rotation) is the stored bitmap.
HALF_NAME = {'left': 'right half', 'right': 'left half',
             'top': 'bottom half', 'bottom': 'top half'}


def meta_text(row, why_rot, k):
    lines = ['# %s - TX2 NFS4 texture, x%d' % (row['file'], k),
             'source_fsh: GameData/Track/%s.fsh  tag %s' % (row['track'], row['tag']),
             'source_png: track_textures/%s' % row['src_file'],
             'source_size: %dx%d' % (row['src_w'], row['src_h']),
             'tracks: %s' % row['tracks'],
             'scale: %d' % k,
             'mirror: %s' % (row['mirror'] or 'none')]
    if row['mirror']:
        lines.append('mirror_note: the stored bitmap is half of an object; the game draws it'
                     ' twice, flipped across its %s edge. This PNG is the whole object;'
                     ' the stored half is the %s (before rotation).'
                     % (row['mirror'], HALF_NAME[row['mirror']]))
    lines.append('rotation_ccw: %d' % row['rotation_ccw'])
    lines.append('rotation_note: %s' % why_rot)
    lines.append('stats: quads=%s up_votes=%s mirror_pairs=%s'
                 % (row['quads'], row['up_votes'], row['mirror_pairs']))
    undo = []
    if row['rotation_ccw']:
        undo.append('rotate %d deg clockwise' % row['rotation_ccw'])
    if row['mirror']:
        undo.append('keep the %s' % HALF_NAME[row['mirror']])
    undo.append('downscale x%d to %dx%d' % (k, row['src_w'], row['src_h']))
    lines.append('pack_back: ' + ', then '.join(undo))
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--scale', type=int, default=4)
    args = ap.parse_args()

    images = []                           # (track, tag, entry, Image, hash)
    track_stats = {}
    for track in sorted(HS_SOURCE):
        data = load_maybe_qfs(os.path.join(TRACK_DIR, track + '.fsh'))
        _head, entries = fsh_dir(data)
        track_stats[track] = hs_stats(HS_SOURCE[track])
        for e in entries:
            img = Image.frombytes('RGBA', (e['w'], e['h']), entry_rgba(data, e))
            digest = hashlib.sha1(img.tobytes() + repr(img.size).encode()).hexdigest()[:16]
            images.append((track, e['tag'], e, img, digest))
        print('%-10s %4d textures' % (track, len(entries)))

    # Pool the FRD statistics of identical pixels across tracks.
    owners = collections.defaultdict(list)
    pooled = collections.defaultdict(TexStats)
    for track, tag, _e, _img, digest in images:
        owners[digest].append(track)
        if tag.isdigit():
            st = track_stats[track].get(int(tag))
            if st:
                pooled[digest].add(st)

    # File names of the TX1 dump (shared pixels there may sit in common/).
    tx1 = {}
    tx1_manifest = os.path.join(nfs5cfg.WORK, 'textures', 'tracks', 'manifest.csv')
    if os.path.exists(tx1_manifest):
        with open(tx1_manifest, newline='') as f:
            tx1 = {(r['track'], r['tag']): r['file'] for r in csv.DictReader(f)}

    os.makedirs(args.out, exist_ok=True)
    written = {}
    rows = []
    n_rot = n_mir = 0
    for track, tag, e, img, digest in images:
        shared = len(set(owners[digest])) > 1
        folder = 'common' if shared else track
        name = '%s_%s' % (sorted(owners[digest])[0] if shared else track, safe_tag(tag))
        rel = folder + '/' + name + '.png'
        st = pooled.get(digest, TexStats())
        side = st.mirror()
        angle, why_rot = st.rotation()
        row = dict(file=rel, track=track, tag=tag, src_w=e['w'], src_h=e['h'],
                   src_file=tx1.get((track, tag), ''),
                   tracks=' '.join(sorted(set(owners[digest]))),
                   mirror=side or '', rotation_ccw=angle, quads=st.n,
                   up_votes=' '.join('%s=%d' % kv for kv in sorted(st.up.items())),
                   mirror_pairs=' '.join('%s=%d/%d' % (s, n, st.chained[s])
                                         for s, n in sorted(st.paired.items())),
                   hash=digest)
        if rel not in written:
            out = make_whole(img, side) if side else img
            if angle:
                out = out.rotate(angle, expand=True)
            out = upscale(out, args.scale)
            os.makedirs(os.path.join(args.out, folder), exist_ok=True)
            out.save(os.path.join(args.out, folder, name + '.png'))
            if side or angle:
                with open(os.path.join(args.out, folder, name + '.txt'), 'w') as f:
                    f.write(meta_text(row, why_rot, args.scale))
            n_rot += bool(angle)
            n_mir += bool(side)
            written[rel] = out.size
        row['width'], row['height'] = written[rel]
        rows.append(row)

    fields = ['file', 'track', 'tag', 'src_file', 'width', 'height', 'src_w', 'src_h', 'mirror',
              'rotation_ccw', 'quads', 'up_votes', 'mirror_pairs', 'tracks', 'hash']
    with open(os.path.join(args.out, 'manifest.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print('%d entries -> %d PNG files in %s; %d rotated, %d mirrored to whole'
          % (len(rows), len(written), args.out, n_rot, n_mir))


if __name__ == '__main__':
    sys.exit(main())
