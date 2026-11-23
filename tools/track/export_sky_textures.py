"""SKY2: dump every track's horizon panorama (`horz`, `hor0..hor3`) to PNG for
HD upscaling, four pieces per texture.

    python export_sky_textures.py [--out DIR]

Source is the installed GameData/Track/Sky/<Track>.fsh of every track that has
a .crp (stock PU tracks and the converted High Stakes ones alike).

The horizon texture is a 256x256 atlas of 4 columns x `horizon_rows` rows
(sky .bin +0x10). With 2 rows the ring shows panels 0..7 in reading order, one
continuous 512x128 panorama, so the four 128x128 quarters of the atlas are
the panorama cut into four consecutive pieces of two panels each:

    part 0 = panels 0-1 (atlas top-left)     part 2 = panels 4-5 (bottom-left)
    part 1 = panels 2-3 (atlas top-right)    part 3 = panels 6-7 (bottom-right)

Monaco has 1 row: four 64x256 panels, one part each.

Layout of DIR (default <work>/textures/sky):
  <track>/<track>_<tag>_<part>.png        RGBA, pixels exactly as stored
  <track>/alpha/<track>_<tag>_<part>.png  the alpha channel as greyscale, for
                                          upscalers that drop transparency
  manifest.csv                            one row per part: atlas rect, rows,
                                          origin (pu / nfs4), source format, hash

Skipped: fully transparent horizons (Aquatica, Atlantic, Gt1, Gt2 draw no
silhouette) and exact duplicates of a texture already dumped (Monaco1..5 share
one horizon, Canyon hor1 = hor2); their manifest rows carry `same_as` = the
track/tag whose files to use and an empty `file`.

Packing back: put each upscaled part at (x, y) * scale of a scale*256 atlas,
as listed in manifest.csv.
"""

import argparse
import csv
import hashlib
import os

from PIL import Image

from ea_refpack import fsh_dir, load_maybe_qfs
import nfs5_sky_bin
import fsh_pixels
import nfs5cfg

ROOT = nfs5cfg.GAME
TRACK_DIR = os.path.join(ROOT, 'GameData', 'Track')
SKY_DIR = os.path.join(TRACK_DIR, 'Sky')
DEFAULT_OUT = os.path.join(nfs5cfg.WORK, 'textures', 'sky')

# Installed tracks converted from High Stakes (same list as TX1).
NFS4_TRACKS = {'aquatica', 'atlantic', 'country', 'dolphin', 'empire', 'france',
               'germany', 'gt1', 'gt2', 'gt3', 'hills', 'hometown', 'lostcany',
               'park', 'redrock', 'rockypas', 'snowy', 'summit', 'uk'}
HORIZON_TAGS = ('horz', 'hor0', 'hor1', 'hor2', 'hor3')
PARTS = 4


def sky_files():
    """track -> (fsh path, bin path or None), matched case-insensitively."""
    by_lower = {f.lower(): f for f in os.listdir(SKY_DIR)}
    out = {}
    for f in sorted(os.listdir(TRACK_DIR)):
        stem, ext = os.path.splitext(f)
        if ext.lower() != '.crp' or '.' in stem:          # skip *.sign-test.crp
            continue
        track = stem.lower()
        fsh = by_lower.get(track + '.fsh')
        if fsh is None:
            print('%-10s no Sky/%s.fsh, skipped' % (track, track))
            continue
        b = by_lower.get(track + '.bin')
        out[track] = (os.path.join(SKY_DIR, fsh), b and os.path.join(SKY_DIR, b))
    return out


def horizon_rows(bin_path):
    if bin_path:
        try:
            return nfs5_sky_bin.read(bin_path).horizon_rows
        except ValueError:
            pass
    return 2


def part_rects(w, h, rows):
    """Four (x, y, w, h) rects in panorama order."""
    if rows == 1:
        pw = w // PARTS
        return [(i * pw, 0, pw, h) for i in range(PARTS)]
    hw, hh = w // 2, h // 2
    return [(0, 0, hw, hh), (hw, 0, hw, hh), (0, hh, hw, hh), (hw, hh, hw, hh)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=DEFAULT_OUT)
    args = ap.parse_args()

    rows_out = []
    seen = {}                                   # atlas pixel hash -> track/tag
    for track, (fsh_path, bin_path) in sky_files().items():
        data = load_maybe_qfs(fsh_path)
        _head, entries = fsh_dir(data)
        rows = horizon_rows(bin_path)
        origin = 'nfs4' if track in NFS4_TRACKS else 'pu'
        tdir = os.path.join(args.out, track)
        tags = []
        for e in entries:
            if e['tag'] not in HORIZON_TAGS:
                continue
            img = Image.frombytes('RGBA', (e['w'], e['h']),
                                  fsh_pixels.texture_rgba(data, e))
            key = hashlib.sha1(img.tobytes()).hexdigest()
            if img.getchannel('A').getextrema()[1] == 0:
                same_as, note = 'transparent', '(empty)'
            else:
                same_as = seen.get(key, '')
                note = '(= %s)' % same_as if same_as else ''
                seen.setdefault(key, '%s/%s' % (track, e['tag']))
            if not same_as:
                os.makedirs(os.path.join(tdir, 'alpha'), exist_ok=True)
            for part, (x, y, w, h) in enumerate(part_rects(e['w'], e['h'], rows)):
                piece = img.crop((x, y, x + w, y + h))
                name = '%s_%s_%d.png' % (track, e['tag'], part)
                if not same_as:
                    piece.save(os.path.join(tdir, name))
                    piece.getchannel('A').save(os.path.join(tdir, 'alpha', name))
                rows_out.append(dict(
                    track=track, origin=origin, sky_file=os.path.basename(fsh_path),
                    tag=e['tag'], part=part,
                    file='' if same_as else '%s/%s' % (track, name), same_as=same_as,
                    x=x, y=y, w=w, h=h, atlas_w=e['w'], atlas_h=e['h'],
                    horizon_rows=rows, src_format=e['fmt'],
                    sha1=hashlib.sha1(piece.tobytes()).hexdigest()[:16]))
            tags.append(e['tag'] + note)
        print('%-10s %-4s rows %d  %s' % (track, origin, rows, ' '.join(tags) or 'NO HORIZON'))

    with open(os.path.join(args.out, 'manifest.csv'), 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=list(rows_out[0]))
        wr.writeheader()
        wr.writerows(rows_out)
    print('%d PNGs -> %s' % (sum(1 for r in rows_out if r['file']), args.out))


if __name__ == '__main__':
    main()
