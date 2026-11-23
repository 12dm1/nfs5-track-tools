"""SKY3: import upscaled horizon parts back into `Sky/<Track>.fsh`.

The inverse of `tools/track/export_sky_textures.py` (SKY2). That tool cut
every horizon atlas (`horz`, `hor0..hor3`) into four parts listed in
`<work>/textures/sky/manifest.csv`; this module takes four PNGs of the
same layout at any size (128x128 parts upscaled x4 = 512x512 -> a 1024x1024
atlas), puts each part at its manifest rect scaled to the new size and writes
the atlas into the track's sky archive as ARGB8888.

Larger horizons are fine in game: the exe allocates the texture at the FSH
size and the wrapper takes normalised UVs (checked on Alps with 1024x1024).

Alpha of a part, first found wins:
  1. the PNG's own alpha channel;
  2. a greyscale PNG with the same name in an `alpha/` folder next to it
     (SKY2 dumps one per part, for upscalers that drop transparency);
  3. the part's alpha in the current archive, resized (bicubic).

The first write keeps the archive as it was in `<work>/backups/sky_fsh/`;
`restore()` puts that copy back.
"""

import csv
import os
import re
import shutil
import struct

import numpy as np
from PIL import Image

import ea_refpack as ea
import track_atmosphere as atm

SKY_DIR = os.path.join(atm.GAME, 'GameData', 'Track', 'Sky')
ASSET_DIR = os.path.join(atm.nfs5cfg.WORK, 'textures', 'sky')
MANIFEST = os.path.join(ASSET_DIR, 'manifest.csv')
BACKUP_DIR = os.path.join(atm.nfs5cfg.WORK, 'backups', 'sky_fsh')
HORIZON_TAGS = ('horz', 'hor0', 'hor1', 'hor2', 'hor3')
PARTS = 4
SCALES = (1, 2, 4, 8, 16)            # atlas stays a power of two, <= 4096
CODE_ARGB8888 = 0x7D
IMAGE_EXT = ('.png', '.tga', '.bmp', '.jpg', '.jpeg', '.tif', '.tiff', '.webp')


def _align16(n):
    return (n + 15) & ~15


# ---------------------------------------------------------------- manifest ---

def read_manifest():
    if not os.path.exists(MANIFEST):
        return []
    with open(MANIFEST, newline='') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ('part', 'x', 'y', 'w', 'h', 'atlas_w', 'atlas_h', 'horizon_rows'):
            r[k] = int(r[k])
    return rows


class Target:
    """One horizon texture of a track: where its parts sit in the atlas and
    which dumped files are its defaults."""

    def __init__(self, track, tag, rows, by_key):
        rows = sorted(rows, key=lambda r: r['part'])
        self.track, self.tag = track, tag
        self.rects = [(r['x'], r['y'], r['w'], r['h']) for r in rows]
        self.atlas = (rows[0]['atlas_w'], rows[0]['atlas_h'])
        self.rows = rows[0]['horizon_rows']
        self.same_as = rows[0]['same_as']
        src = rows
        if self.same_as and self.same_as != 'transparent':
            src = sorted(by_key.get(self.same_as, rows), key=lambda r: r['part'])
        # stem of the dumped part files: <track>_<tag>_<part>
        self.source = '%s/%s' % (src[0]['track'], src[0]['tag'])
        self.stems = ['%s_%s_%d' % (r['track'], r['tag'], r['part']) for r in src]
        self.folder = os.path.join(ASSET_DIR, src[0]['track'])

    @property
    def key(self):
        return '%s/%s' % (self.track, self.tag)


def targets(track):
    """{tag: Target} of `track` (lower-case file stem) from the manifest."""
    rows = read_manifest()
    by_key = {}
    for r in rows:
        by_key.setdefault('%s/%s' % (r['track'], r['tag']), []).append(r)
    track = track.lower()
    return {r_tag: Target(track, r_tag, by_key['%s/%s' % (track, r_tag)], by_key)
            for r_tag in sorted({r['tag'] for r in rows if r['track'] == track},
                                key=HORIZON_TAGS.index)}


def sharing(target):
    """Other textures that are copies of this one (Monaco2..5 = Monaco1,
    Canyon hor2 = hor1): [(track, tag)]."""
    src = target.source
    out = []
    for r in read_manifest():
        if r['part'] != 0:
            continue
        key = '%s/%s' % (r['track'], r['tag'])
        if key != target.key and (r['same_as'] == src or key == src):
            out.append((r['track'], r['tag']))
    return out


# ----------------------------------------------------------------- finding ---

def _matches(name, stem):
    """`alps_horz_0.png`, `alps_horz_0_x4.png`, `alps_horz_0-upscaled.png`,
    but not `alps_horz_01.png`."""
    base, ext = os.path.splitext(name)
    if ext.lower() not in IMAGE_EXT:
        return False
    b, s = base.lower(), stem.lower()
    return b == s or (b.startswith(s) and not b[len(s)].isdigit())


def find_part(folder, stem):
    """The largest image in `folder` whose name starts with `stem`."""
    if not folder or not os.path.isdir(folder):
        return None
    best = None
    for name in os.listdir(folder):
        p = os.path.join(folder, name)
        if not os.path.isfile(p) or not _matches(name, stem):
            continue
        try:
            with Image.open(p) as im:
                area = im.size[0] * im.size[1]
        except OSError:
            continue
        if best is None or area > best[0] or (area == best[0] and len(name) < len(best[1])):
            best = (area, name, p)
    return best[2] if best else None


def find_parts(folder, target):
    return [find_part(folder, s) for s in target.stems]


def _alpha_file(path):
    """`alpha/<same name>` next to a part, or one whose name starts with the
    part's dumped stem."""
    folder, name = os.path.split(path)
    adir = os.path.join(folder, 'alpha')
    exact = os.path.join(adir, name)
    if os.path.isfile(exact):
        return exact
    stem = re.match(r'(.+?_[a-z0-9]{4}_\d)(?!\d)', os.path.splitext(name)[0].lower())
    return find_part(adir, stem.group(1)) if stem else None


# ------------------------------------------------------------------- build ---

def sky_fsh(track):
    """The game's own `Sky/<Track>.fsh` (case-insensitive), or None."""
    if not os.path.isdir(SKY_DIR):
        return None
    want = track.lower() + '.fsh'
    for name in os.listdir(SKY_DIR):
        if name.lower() == want:
            return os.path.join(SKY_DIR, name)
    return None


def current_atlas(fsh_path, tag):
    """RGBA uint8 array (h, w, 4) of `tag` in the archive, or None."""
    import assets
    tex = assets.load_fsh(fsh_path, (tag,)).get(tag)
    if tex is None:
        return None
    w, h, rgba = tex
    return np.frombuffer(rgba, np.uint8).reshape(h, w, 4)


def pick_scale(target, sizes):
    """Atlas scale from the parts' sizes: the power of two nearest to the
    mean ratio part size / manifest size."""
    ratios = []
    for (w, h), (_x, _y, rw, rh) in zip(sizes, target.rects):
        ratios += [w / rw, h / rh]
    r = sum(ratios) / len(ratios)
    return min(SCALES, key=lambda s: abs(np.log2(s) - np.log2(max(r, 1e-6))))


class Import:
    """Four part images composed into one atlas. `notes` = what happened to
    each part (resized, where its alpha came from)."""

    def __init__(self, target, paths, fsh_path=None):
        self.target = target
        self.paths = list(paths)
        self.notes = []
        self.atlas = None             # (h, w, 4) uint8
        self.scale = 1
        missing = [i for i, p in enumerate(self.paths) if not p or not os.path.isfile(p)]
        if missing:
            raise ValueError('part %s: no file' % ', '.join(str(i) for i in missing))
        images = [Image.open(p) for p in self.paths]
        try:
            self._build(images, fsh_path)
        finally:
            for im in images:
                im.close()

    def _build(self, images, fsh_path):
        t = self.target
        self.scale = s = pick_scale(t, [im.size for im in images])
        aw, ah = t.atlas[0] * s, t.atlas[1] * s
        old = current_atlas(fsh_path, t.tag) if fsh_path else None
        atlas = np.zeros((ah, aw, 4), np.uint8)
        for i, (im, path, (x, y, w, h)) in enumerate(zip(images, self.paths, t.rects)):
            pw, ph = w * s, h * s
            note = '%dx%d' % im.size
            has_alpha = im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info)
            rgba = im.convert('RGBA')
            if rgba.size != (pw, ph):
                rgba = rgba.resize((pw, ph), Image.LANCZOS)
                note += ' -> resized %dx%d' % (pw, ph)
            px = np.array(rgba)
            if has_alpha:
                note += ', alpha from file'
            else:
                ap = _alpha_file(path)
                if ap:
                    a = Image.open(ap).convert('L')
                    if a.size != (pw, ph):
                        a = a.resize((pw, ph), Image.BICUBIC)
                    px[:, :, 3] = np.array(a)
                    note += ', alpha from alpha/%s' % os.path.basename(ap)
                elif old is not None:
                    k = old.shape[1] / t.atlas[0]    # the archive may already hold an upscale
                    ox, oy, ow, oh = (int(round(v * k)) for v in (x, y, w, h))
                    a = Image.fromarray(np.ascontiguousarray(old[oy:oy + oh, ox:ox + ow, 3]), 'L')
                    if a.getextrema()[1] == 0:
                        note += ', NO ALPHA (opaque; the old horizon was empty)'
                        px[:, :, 3] = 255
                    else:
                        px[:, :, 3] = np.array(a.resize((pw, ph), Image.BICUBIC))
                        note += ', alpha from the current horizon (resized)'
                else:
                    px[:, :, 3] = 255
                    note += ', NO ALPHA (opaque)'
            atlas[y * s:(y + h) * s, x * s:(x + w) * s] = px
            self.notes.append('part %d: %s  %s' % (i, os.path.basename(path), note))
        self.atlas = atlas

    @property
    def size(self):
        return self.atlas.shape[1], self.atlas.shape[0]

    def rgba_bytes(self):
        return np.ascontiguousarray(self.atlas).tobytes()


# ------------------------------------------------------------------- write ---

def replace_textures(data, new):
    """SHPI bytes with the entries named in `new` ({tag: (w, h, RGBA bytes)})
    replaced by ARGB8888 blocks without mips; every other block is copied
    byte for byte (its formats and attachments stay as they are)."""
    _h, entries = ea.fsh_dir(data)
    ends = sorted(e['off'] for e in entries) + [len(data)]
    n = len(entries)
    blocks = []
    for e in entries:
        if e['tag'] in new:
            w, h, rgba = new[e['tag']]
            px = np.frombuffer(rgba, np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]]   # -> B,G,R,A
            blk = bytearray(_align16(16 + w * h * 4))
            blk[:16] = data[e['off']:e['off'] + 16]
            blk[0] = CODE_ARGB8888
            blk[1:4] = b'\0\0\0'          # NFS5 leaves the block size at zero
            struct.pack_into('<HH', blk, 4, w, h)
            blk[15] = 0                   # no mips: the wrapper builds its own
            blk[16:16 + w * h * 4] = px.tobytes()
        else:
            end = next(o for o in ends if o > e['off'])
            blk = data[e['off']:end]
        blocks.append(bytes(blk))
    off = _align16(16 + n * 8)
    out = bytearray(off)
    struct.pack_into('<4sII4s', out, 0, b'SHPI', 0, n, data[12:16])
    for i, (e, blk) in enumerate(zip(entries, blocks)):
        struct.pack_into('<4sI', out, 16 + i * 8, e['tag'].encode('latin1'), len(out))
        out += blk
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out)


def backup_path(fsh_path):
    return os.path.join(BACKUP_DIR, os.path.basename(fsh_path))


def has_backup(track):
    p = sky_fsh(track)
    return bool(p) and os.path.exists(backup_path(p))


def write(track, imp, tags=None, also=()):
    """Write `imp` into Sky/<track>.fsh as `tags` (default: the import's own
    tag), plus every (track, tag) in `also`. Returns the written paths."""
    tags = list(tags or [imp.target.tag])
    jobs = {}
    for t, tag in [(track, g) for g in tags] + list(also):
        jobs.setdefault(t.lower(), set()).add(tag)
    w, h = imp.size
    tex = (w, h, imp.rgba_bytes())
    written = []
    for t, tag_set in sorted(jobs.items()):
        path = sky_fsh(t)
        if path is None:
            raise FileNotFoundError('Sky/%s.fsh not found' % t.capitalize())
        data = ea.load_maybe_qfs(path)
        have = {e['tag'] for e in ea.fsh_dir(data)[1]}
        lost = sorted(tag_set - have)
        if lost:
            raise ValueError('%s has no %s' % (os.path.basename(path), ', '.join(lost)))
        os.makedirs(BACKUP_DIR, exist_ok=True)
        if not os.path.exists(backup_path(path)):
            shutil.copy2(path, backup_path(path))
        blob = replace_textures(data, {tag: tex for tag in tag_set})
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(blob)
        os.replace(tmp, path)
        written.append(path)
    return written


def restore(track, also=()):
    """Put the backed-up archives back. Returns the restored paths."""
    out = []
    for t in [track] + [a for a, _tag in also]:
        path = sky_fsh(t)
        if path and os.path.exists(backup_path(path)):
            shutil.copy2(backup_path(path), path)
            out.append(path)
    return sorted(set(out))
