"""Cut a rectangular fragment out of a PU track CRP and build a Blender scene.

    python export_track_fragment.py farmland --preset gas
    python export_track_fragment.py farmland --rect -3640 -260 -3020 200 --tag town

Output: <work>/blender/<track>_<tag>/
    <track>_<tag>.blend   one object per CRP article, grouped in collections
    <track>_<tag>.glb     the same scene for tools that do not read .blend
    textures/*.png        top mip of every FSH page used, RGBA
    fragment.json         region, origin offset, object and texture list
    README.md

Geometry is LOD0 (the most detailed of the three `pr` levels). Articles
that carry a `tr` part store authored-space vertices; the matrix places them.
Coordinates: game (x, y-up, z) -> Blender (x, z, y), moved so the region
centre sits at the origin; the subtracted offset is in fragment.json.
The `df` part (baked light, one colour per triangle corner) becomes the
`baked_light` colour attribute; the materials multiply it in like the game.
"""

import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image

import nfs5_crp as crp_mod
import nfs5_fsh as fsh
import ea_refpack as ea
import nfs5cfg

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK_DIR = os.path.join(nfs5cfg.GAME, 'GameData', 'Track')
TEXTURE_NOTES = os.path.join(nfs5cfg.WORK, 'textures', 'tracks')
BLENDER = r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'

# x0, z0, x1, z1 in game units, plus a short description for the README
PRESETS = {
    ('farmland', 'gas'): ((-3660, -280, -3000, 230),
                          'Farm town at the gas station / store: the main road '
                          '(route 1, slices ~640-770) and the parallel branch '
                          '(route 2) that splits off and rejoins it.'),
    ('farmland', 'church'): ((-1570, 30, -960, 370),
                             'Church village: the road forks around the church '
                             'yard (route 4) and rejoins route 1 at the two '
                             'CAN8YJUN junction chunks (slices ~208-344).'),
}

SLICE_RE = re.compile(r'^(RD|CNK|OBJ)(\d+)')
VEG_RE = re.compile(r'SHRUB|TRUNK|DECID|OAK|BUSH|PINE|TREE|SHRABB', re.I)

MODE_OPAQUE, MODE_CLIP, MODE_BLEND = 'opaque', 'clip', 'blend'


def collection_of(name):
    if name.startswith('RD'):
        return 'Road'
    if name.startswith('Shadow'):
        return 'Shadows'
    if name.startswith('WOBJ'):
        return 'Background'
    if VEG_RE.search(name):
        return 'Vegetation'
    if name.startswith('CNK'):
        return 'Terrain_Buildings'
    if re.search(r'_SGN\b|_SGN$', name):
        return 'Signs'
    return 'Objects'


def safe_tag(tag):
    return ''.join(c if c.isalnum() else '_%02X' % ord(c) for c in tag)


def array_part(parts, tag, lod=0):
    for p in parts:
        if p['tag'] == tag and (p['info'] & 0xFF) == lod:
            return p
    return None


def article_triangles(crp, ai, materials):
    """World-space LOD0 triangles of one article.

    Returns (pos[n,3], uv[n,2], col[n,4] RGBA u8, mat_key[n/3]) or None.
    """
    d = crp.data
    parts = crp.parts(ai)
    vt, uv, df = (array_part(parts, t) for t in ('vt', 'uv', 'df'))
    if vt is None or uv is None or df is None:
        return None
    vt_arr = np.frombuffer(d, '<f4', vt['count'] * 4, vt['data']).reshape(-1, 4)
    uv_arr = np.frombuffer(d, '<f4', uv['count'] * 2, uv['data']).reshape(-1, 2)
    df_arr = np.frombuffer(d, '<u1', df['count'] * 4, df['data']).reshape(-1, 4)

    pos_l, uv_l, col_l, mat_l = [], [], [], []
    for p in parts:
        if p['tag'] != 'pr' or (p['info'] >> 12) != 0:
            continue
        t = crp.triangles(p)
        n = p['count']
        if n == 0 or n % 3:
            continue
        rows = {r['level']: r for r in t['info_rows']}
        if not all(k in rows for k in (0, 2, 3)):
            continue
        vt_base = rows[0]['data_offset'] // 16
        uv_base = rows[2]['data_offset'] // 8
        df_base = rows[3]['data_offset'] // 4
        q = t['index_data']
        iv = iu = np.arange(n, dtype=np.int32)
        for row in t['index_rows']:
            idx = np.frombuffer(d, '<u1', n, q).astype(np.int32)
            q += n
            if row['tag'] == 'Iv':
                iv = idx
            elif row['tag'] == 'Iu':
                iu = idx
        if (vt_base + iv.max() >= len(vt_arr) or uv_base + iu.max() >= len(uv_arr)
                or df_base + n > len(df_arr)):
            continue
        mat = materials.get(t['material'])
        if not mat or not mat.get('fsh_tag'):
            continue
        pos_l.append(vt_arr[vt_base + iv, :3])
        uv_l.append(uv_arr[uv_base + iu])
        col_l.append(df_arr[df_base:df_base + n][:, [2, 1, 0, 3]])  # BGRA -> RGBA
        mat_l.append(np.full(n // 3, t['material'], np.int32))
    if not pos_l:
        return None
    pos = np.concatenate(pos_l).astype(np.float64)
    tr = array_part(parts, 'tr')
    if tr is not None:
        m = np.frombuffer(d, '<f4', 16, tr['data']).reshape(4, 4).astype(np.float64)
        pos = pos @ m[:3, :3] + m[3, :3]          # D3D row vectors: v * M
    return pos, np.concatenate(uv_l), np.concatenate(col_l), np.concatenate(mat_l)


def texture_mode(desc, rgba):
    a = rgba[:, 3]
    if desc == 'FakeSort':
        return MODE_BLEND if (a < 255).any() else MODE_OPAQUE
    if desc == 'Opaque' or a.min() == 255:
        return MODE_OPAQUE
    return MODE_CLIP


def load_notes(track):
    notes = {}
    for sub in ('common', track):
        path = os.path.join(TEXTURE_NOTES, sub, 'descriptions.txt')
        if not os.path.exists(path):
            continue
        for line in open(path, encoding='utf-8'):
            fn, _, text = line.strip().partition(' ')
            if fn.startswith(track + '_') and fn.endswith('.png'):
                notes[fn[len(track) + 1:-4]] = text.strip()
    return notes


def export(track, rect, tag, out_dir, blurb='', build_blend=True):
    x0, z0, x1, z1 = rect
    cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
    crp = crp_mod.read(os.path.join(TRACK_DIR, track + '.crp'))
    fsh_data = ea.load_maybe_qfs(os.path.join(TRACK_DIR, track + '.fsh'))
    _head, entries = ea.fsh_dir(fsh_data)
    entry_by_tag = {e['tag']: e for e in entries}
    materials = {m['index']: m for m in crp.misc() if m['tag'] == 'mt'}

    def inside(x, z):
        return (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)

    objects, chunks = [], []
    used_mats = set()
    ys = []
    for ai in range(crp.n_articles):
        tri = article_triangles(crp, ai, materials)
        if tri is None:
            continue
        pos, uv, col, mat = tri
        name = crp.name(ai) or 'article_%d' % ai
        lo, hi = pos.min(axis=0), pos.max(axis=0)
        mid = (lo + hi) / 2
        if inside(mid[0], mid[2]):
            keep = np.ones(len(mat), bool)        # whole article
        else:
            c = pos.reshape(-1, 3, 3).mean(axis=1)  # clip big pieces per triangle
            keep = inside(c[:, 0], c[:, 2])
            if not keep.any():
                continue
        k3 = np.repeat(keep, 3)
        pos, uv, col, mat = pos[k3], uv[k3], col[k3], mat[keep]
        known = np.array([materials[m]['fsh_tag'] in entry_by_tag for m in mat])
        if not known.all():
            k3 = np.repeat(known, 3)
            pos, uv, col, mat = pos[k3], uv[k3], col[k3], mat[known]
        if not len(mat):
            continue
        used_mats.update(int(m) for m in np.unique(mat))
        ys.append(pos[:, 1])
        objects.append(dict(name=name, collection=collection_of(name), article=ai,
                            clipped=bool(not keep.all()), triangles=int(len(mat))))
        chunks.append((pos, uv, col, mat))

    if not objects:
        raise SystemExit('nothing inside the rectangle')
    cy = float(np.percentile(np.concatenate(ys), 5))     # ground level ~ origin
    origin = (cx, cy, cz)

    # textures: one PNG per FSH tag, top mip, straight RGBA
    os.makedirs(os.path.join(out_dir, 'textures'), exist_ok=True)
    notes = load_notes(track)
    tex_info, mat_list, mat_index = {}, [], {}
    for mi in sorted(used_mats):
        m = materials[mi]
        ftag = m['fsh_tag']
        if ftag not in tex_info:
            w, h, bgra = fsh.decode_entry(fsh_data, entry_by_tag[ftag])
            rgba = np.frombuffer(bytes(bgra), np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]]
            fn = '%s_%s.png' % (track, safe_tag(ftag))
            Image.frombytes('RGBA', (w, h), rgba.tobytes()).save(
                os.path.join(out_dir, 'textures', fn), optimize=True)
            tex_info[ftag] = dict(file='textures/' + fn, w=w, h=h, rgba=rgba,
                                  note=notes.get(safe_tag(ftag), ''))
        mode = texture_mode(m.get('desc', ''), tex_info[ftag]['rgba'])
        key = (ftag, mode)
        if key not in mat_index:
            mat_index[key] = len(mat_list)
            mat_list.append(dict(name='%s_%s' % (safe_tag(ftag), mode), image=tex_info[ftag]['file'],
                                 mode=mode, game_desc=m.get('desc', ''), fsh_tag=safe_tag(ftag)))
        m['_blender_index'] = mat_index[key]

    # flat arrays for Blender: game (x,y,z) -> blender (x,z,y), centred, V flipped
    P, U, C, M, offs = [], [], [], [], [0]
    for pos, uv, col, mat in chunks:
        p = pos - origin
        P.append(np.stack([p[:, 0], p[:, 2], p[:, 1]], axis=1).astype(np.float32))
        U.append(np.stack([uv[:, 0], 1.0 - uv[:, 1]], axis=1).astype(np.float32))
        C.append(col)
        M.append(np.array([materials[i]['_blender_index'] for i in mat], np.int32))
        offs.append(offs[-1] + len(mat))
    np.savez_compressed(os.path.join(out_dir, 'fragment_mesh.npz'),
                        pos=np.concatenate(P), uv=np.concatenate(U),
                        col=np.concatenate(C), mat=np.concatenate(M),
                        tri_offsets=np.array(offs, np.int64))

    # road centre lines of every route that passes through the fragment
    lines = {}
    for ai in range(crp.n_articles):
        nm = crp.name(ai) or ''
        mm = re.match(r'^RD(\d+)C \((\d+) PRIMARY', nm)
        b = crp.base(ai) if mm else None
        if b:
            x, y, z = b['centre']
            if inside(x, z):
                lines.setdefault(mm.group(2), []).append(
                    (int(mm.group(1)), [x - cx, z - cz, y - cy]))
    centrelines = {'route_%s' % k: [p for _, p in sorted(v)] for k, v in sorted(lines.items())}

    col_all = np.concatenate(C)
    meta = dict(track=track, tag=tag, description=blurb,
                rect_game_xz=[x0, z0, x1, z1],
                origin_game_xyz=list(origin),
                axes='blender = (game_x - ox, game_z - oz, game_y - oy); 1 unit = 1 m',
                lod='LOD0 (highest of 3)',
                materials=mat_list, centrelines=centrelines,
                objects=objects,
                textures=[dict(fsh_tag=safe_tag(k), file=v['file'], size=[v['w'], v['h']],
                               note=v['note']) for k, v in sorted(tex_info.items())],
                stats=dict(triangles=int(offs[-1]), objects=len(objects),
                           materials=len(mat_list), textures=len(tex_info),
                           baked_light_mean_rgb=[round(float(v), 1) for v in col_all[:, :3].mean(0)],
                           baked_light_alpha_min=int(col_all[:, 3].min())))
    with open(os.path.join(out_dir, 'fragment.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1)
    write_readme(out_dir, meta)
    print('%s_%s: %d objects, %d triangles, %d materials, %d textures'
          % (track, tag, len(objects), offs[-1], len(mat_list), len(tex_info)))
    print('  baked light mean RGB %s, alpha min %d' % (meta['stats']['baked_light_mean_rgb'],
                                                      meta['stats']['baked_light_alpha_min']))

    if build_blend:
        base = os.path.join(out_dir, '%s_%s' % (track, tag))
        for ext in ('.blend', '.blend1'):           # no Blender .blend1 backups
            if os.path.exists(base + ext):
                os.remove(base + ext)
        cmd = [BLENDER, '--background', '--factory-startup', '--python',
               os.path.join(HERE, 'blender_build_fragment.py'), '--', out_dir, base]
        r = subprocess.run(cmd, capture_output=True, text=True)
        log = r.stdout + r.stderr
        for line in log.splitlines():
            if line.startswith('[frag]') or 'Error' in line or 'Traceback' in line:
                print('  ' + line)
        if r.returncode or not os.path.exists(base + '.blend'):
            print(log[-4000:])
            raise SystemExit('blender failed (%d)' % r.returncode)


def write_readme(out_dir, meta):
    s = meta['stats']
    by_col = {}
    for o in meta['objects']:
        by_col.setdefault(o['collection'], []).append(o['name'])
    lines = [
        '# %s / %s: track fragment for re-modelling' % (meta['track'], meta['tag']),
        '',
        meta['description'],
        '',
        'Source: Need for Speed Porsche Unleashed (2000), `GameData/Track/%s.crp` + `.fsh`.'
        % meta['track'],
        '',
        '- Geometry: %s, %d triangles in %d objects (one per game article).'
        % (meta['lod'], s['triangles'], s['objects']),
        '- Units: metres. Blender Z is up. The fragment centre is at the origin;'
        ' blender (x, y, z) = game (x %+.1f, z %+.1f, y %+.1f).'
        % (-meta['origin_game_xyz'][0], -meta['origin_game_xyz'][2], -meta['origin_game_xyz'][1]),
        '- `preview_top.png` / `preview_persp.png`: Blender renders of this scene for orientation.',
        '- Textures: `textures/`, the original top mip (the most detail the game has), RGBA PNG.'
        ' Cutout materials use alpha clip at 0.5; `blend` materials are semi-transparent.',
        '- Colour attribute `baked_light`: the game\'s pre-baked lighting/tint per triangle corner.'
        ' The materials multiply it into the texture like the game does; set the'
        ' "baked light" mix factor to 0 to see the bare textures.',
        '- Curves `centreline_route_N`: the racing line centre of every route through the fragment'
        ' (route 1 = main road; other numbers = branches).',
        '- Geometry at the rectangle edge is cut per triangle, so terrain borders are jagged.',
        '',
        '## Collections',
        '',
    ]
    for col in sorted(by_col):
        names = sorted(set(re.sub(r'^\S+ \(\d+ ', '', n).rstrip(')').strip() for n in by_col[col]))
        lines.append('- **%s** (%d objects): %s' % (col, len(by_col[col]), ', '.join(names[:40])
                                                    + (' ...' if len(names) > 40 else '')))
    lines += ['', '## Textures', '', '| file | size | content (auto, may be wrong) |', '|---|---|---|']
    for t in meta['textures']:
        lines.append('| %s | %dx%d | %s |' % (t['file'], t['size'][0], t['size'][1], t['note']))
    with open(os.path.join(out_dir, 'README.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('track')
    ap.add_argument('--preset', help='named region: %s' % ', '.join(sorted({k[1] for k in PRESETS})))
    ap.add_argument('--rect', nargs=4, type=float, metavar=('X0', 'Z0', 'X1', 'Z1'))
    ap.add_argument('--tag', default='fragment')
    ap.add_argument('--out')
    ap.add_argument('--no-blend', action='store_true')
    a = ap.parse_args()
    blurb = ''
    if a.preset:
        rect, blurb = PRESETS[(a.track, a.preset)]
        tag = a.preset
    elif a.rect:
        rect, tag = a.rect, a.tag
    else:
        ap.error('--preset or --rect')
    out = a.out or os.path.join(nfs5cfg.WORK, 'blender', '%s_%s' % (a.track, tag))
    os.makedirs(out, exist_ok=True)
    export(a.track, rect, tag, out, blurb, not a.no_blend)


if __name__ == '__main__':
    main()
