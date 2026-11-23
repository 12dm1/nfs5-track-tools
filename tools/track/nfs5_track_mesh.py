"""CRP -> ready-to-draw buffers for the atmosphere editor preview (ATM4).

No window, no GL: flat numpy arrays. Geometry comes out already unrolled --
the `df` (baked vertex colour) part stores one colour per triangle CORNER, in
order, with no index array of its own, so every triangle corner is one vertex
of the output and drawing needs no index buffer (ATM2 §7).

    python nfs5_track_mesh.py <GameData/Track> <track name>
"""

import re
import sys

import numpy as np

import nfs5_crp as crp_mod
import nfs5_fsh as fsh
import ea_refpack as ea

SLICE_RE = re.compile(r'^(RD|CNK)(\d+)')
CENTRE_RE = re.compile(r'^RD(\d+)C\b')

BLEND_OPAQUE = 'opaque'
BLEND_TEST = 'alpha_test'


class TrackMesh:
    __slots__ = ('positions', 'uvs', 'colours', 'article_slice', 'batches',
                 'textures', 'centreline', 'slices', 'unresolved_materials',
                 'articles', 'bbox', 'skipped_prims', 'name',
                 'mt_by_tag')


def _array_part(parts, tag, lod=0):
    for p in parts:
        if p['tag'] == tag and (p['info'] & 0xFF) == lod:
            return p
    return None


def _article_slice(name):
    m = SLICE_RE.match(name or '')
    return int(m.group(2)) if m else -1


def load_track(track_dir, name):
    crp = crp_mod.read('%s/%s.crp' % (track_dir, name))
    fsh_data = ea.load_maybe_qfs('%s/%s.fsh' % (track_dir, name))
    _head, entries = ea.fsh_dir(fsh_data)
    entry_by_tag = {e['tag']: e for e in entries}

    materials = {}
    for i, m in enumerate(e for e in crp.misc() if e['tag'] == 'mt'):
        materials[m['index']] = m
    d = crp.data

    per_key = {}          # (fsh_tag, blend) -> lists of chunks
    unresolved = set()
    skipped = 0
    centreline = {}       # slice -> (primary?, x, y, z)
    max_slice = 0
    mins = np.full(3, np.inf)
    maxs = np.full(3, -np.inf)

    for ai in range(crp.n_articles):
        aname = crp.name(ai)
        aslice = _article_slice(aname)
        max_slice = max(max_slice, aslice)
        parts = crp.parts(ai)

        cm = CENTRE_RE.match(aname or '')
        if cm:
            base = crp.base(ai)
            if base:
                primary = 'PRIMARY' in aname
                s = int(cm.group(1))
                prev = centreline.get(s)
                if prev is None or (primary and not prev[0]):
                    centreline[s] = (primary,) + tuple(base['centre'])

        vt = _array_part(parts, 'vt')
        uv = _array_part(parts, 'uv')
        df = _array_part(parts, 'df')
        if vt is None or uv is None or df is None:
            continue
        vt_arr = np.frombuffer(d, '<f4', vt['count'] * 4, vt['data']).reshape(-1, 4)
        uv_arr = np.frombuffer(d, '<f4', uv['count'] * 2, uv['data']).reshape(-1, 2)
        df_arr = np.frombuffer(d, '<u1', df['count'] * 4, df['data']).reshape(-1, 4)

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
            # Index rows list which streams are indexed, in stream order: 'Iv'
            # (into vt) then 'Iu' (into uv), one byte per corner each. A stream
            # without a row is consumed in order (object prims: no rows at
            # all; some shadows/trees: 'Iv' only, UVs sequential).
            q = t['index_data']
            seq = np.arange(n, dtype=np.int32)
            iv = iu = seq
            for row in t['index_rows']:
                idx = np.frombuffer(d, '<u1', n, q).astype(np.int32)
                q += n
                if row['tag'] == 'Iv':
                    iv = idx
                elif row['tag'] == 'Iu':
                    iu = idx

            if (vt_base + iv.max() >= len(vt_arr) or uv_base + iu.max() >= len(uv_arr)
                    or df_base + n > len(df_arr)):
                skipped += 1
                continue
            pos = vt_arr[vt_base + iv, :3]
            uvs = uv_arr[uv_base + iu]
            # D3DCOLOR is BGRA in memory
            col = df_arr[df_base:df_base + n][:, [2, 1, 0, 3]]

            mat = materials.get(t['material'])
            tag = mat['fsh_tag'] if mat else None
            if tag is None or tag not in entry_by_tag:
                unresolved.add((t['material'], tag))
                continue
            blend = BLEND_OPAQUE if mat['desc'] == 'Opaque' else BLEND_TEST
            per_key.setdefault((tag, blend), []).append(
                (pos, uvs, col, np.full(n, aslice, np.int32)))
            mins = np.minimum(mins, pos.min(axis=0))
            maxs = np.maximum(maxs, pos.max(axis=0))

    mesh = TrackMesh()
    mesh.name = name
    pos_l, uv_l, col_l, sl_l, batches = [], [], [], [], []
    first = 0
    for (tag, blend), chunks in sorted(per_key.items()):
        count = 0
        for pos, uvs, col, sl in chunks:
            pos_l.append(pos)
            uv_l.append(uvs)
            col_l.append(col)
            sl_l.append(sl)
            count += len(pos)
        batches.append((tag, first, count, blend))
        first += count

    mesh.positions = np.ascontiguousarray(np.concatenate(pos_l), np.float32)
    mesh.uvs = np.ascontiguousarray(np.concatenate(uv_l), np.float32)
    mesh.colours = np.ascontiguousarray(np.concatenate(col_l), np.uint8)
    mesh.article_slice = np.concatenate(sl_l)
    mesh.batches = batches
    mesh.unresolved_materials = sorted(unresolved, key=str)
    mesh.articles = crp.n_articles
    mesh.skipped_prims = skipped

    mesh.textures = {}
    for tag in sorted({b[0] for b in batches}):
        w, h, bgra = fsh.decode_entry(fsh_data, entry_by_tag[tag])
        rgba = np.frombuffer(bytes(bgra), np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]]
        mesh.textures[tag] = (w, h, rgba.tobytes())

    mesh.centreline = [(s,) + v[1:] for s, v in sorted(centreline.items())]
    mesh.slices = max_slice + 8
    mesh.bbox = (mins, maxs)
    mt_by_tag = {}
    for m in materials.values():
        tag = m.get('fsh_tag')
        if tag:
            mt_by_tag.setdefault(tag, []).append(
                {'index': m['index'], 'desc': m.get('desc', '')})
    mesh.mt_by_tag = mt_by_tag
    return mesh


if __name__ == '__main__':
    track_dir, name = sys.argv[1], sys.argv[2]
    m = load_track(track_dir, name)
    lo, hi = m.positions.min(axis=0), m.positions.max(axis=0)
    print('%s: %d triangles, %d batches, %d textures, slices %d, centreline pts %d'
          % (name, len(m.positions) // 3, len(m.batches), len(m.textures),
             m.slices, len(m.centreline)))
    print('  bbox  min %s  max %s' % (np.round(lo, 1), np.round(hi, 1)))
    if m.unresolved_materials:
        print('  unresolved: %s' % m.unresolved_materials)
