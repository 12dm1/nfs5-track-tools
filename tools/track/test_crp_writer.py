"""HS3/HS4 acceptance: build karT archives and read them back.

    python test_crp_writer.py

Builds a minimal archive from scratch, then re-emits every article of eight
shipped tracks through the writer and compares what the reader gets back.
"""

import collections
import os
import struct
import sys

import nfs5_crp as reader
from nfs5_crp_writer import Article, Lod, Material, Prim, build_crp, material_template
import nfs5cfg

SCRATCH = os.path.join(nfs5cfg.WORK, 'build', 'hs3-crp')
TRACK_DIR = os.path.join(nfs5cfg.GAME, 'GameData', 'Track')
ALPS = TRACK_DIR + '/alps.crp'
KNOWN_PARTS = {'Base', 'Name', 'vt', 'uv', 'df', 'pr', 'tr'}


# ------------------------------------------------------ read a real article ---

def extract(crp, index):
    """A real article expressed in the writer's own model."""
    lods, name, transform = {}, None, None
    for p in crp.parts(index):
        tag, info = p['tag'], p['info']
        if tag == 'Name':
            name = crp.data[p['data']:p['data'] + 80].split(b'\0')[0].decode('latin1')
        elif tag == 'vt':
            lods.setdefault(info & 0xFF, Lod()).verts = [
                struct.unpack_from('<3f', crp.data, p['data'] + i * 16)
                for i in range(p['count'])]
        elif tag == 'uv':
            lods.setdefault(info & 0xFF, Lod()).uvs = [
                struct.unpack_from('<2f', crp.data, p['data'] + i * 8)
                for i in range(p['count'])]
        elif tag == 'df':
            lods.setdefault(info & 0xFF, Lod()).df = list(
                struct.unpack_from('<%dI' % p['count'], crp.data, p['data']))
        elif tag == 'tr':
            transform = list(struct.unpack_from('<16f', crp.data, p['data']))
        elif tag == 'pr':
            lod = (info >> 12) & 0xF
            t = crp.triangles(p)
            bases = {r['level']: r for r in t['info_rows']}
            n = p['count']
            vidx = list(crp.data[t['index_data']:t['index_data'] + n])
            uidx = list(crp.data[t['index_data'] + n:t['index_data'] + 2 * n])
            prim = Prim(
                material=t['material'], vidx=vidx, uidx=uidx,
                vert_base=bases[0]['data_offset'] // 16 if 0 in bases else 0,
                vert_count=bases[0]['length'] // 16 if 0 in bases else 0,
                uv_base=bases[2]['data_offset'] // 8 if 2 in bases else 0,
                uv_count=bases[2]['length'] // 8 if 2 in bases else 0,
                df_base=bases[3]['data_offset'] // 4 if 3 in bases else 0,
                fill_mode=t['fill_mode'])
            prim.centre, prim.radius = t['centre'], t['radius']
            lods.setdefault(lod, Lod()).prims.append(prim)
    art = Article(name or '', lods, transform=transform)
    base = crp.base(index)
    if base:
        art.distances = [l['distance'] for l in base['lods']]
    return art


def snapshot(crp, index):
    """Everything the writer claims to preserve, as plain comparable data."""
    art = extract(crp, index)
    rows = []
    if art.transform is not None:
        rows.append(('tr',) + tuple(round(x, 5) for x in art.transform))
    for lod in sorted(art.lods):
        d = art.lods[lod]
        rows.append(('lod', lod, len(d.verts), len(d.uvs), len(d.df)))
        for v in d.verts:
            rows.append(('v',) + tuple(round(x, 4) for x in v))
        for uv in d.uvs:
            rows.append(('uv',) + tuple(round(x, 5) for x in uv))
        rows.append(('df', tuple(d.df)))
        for p in sorted(d.prims, key=lambda q: (q.material, tuple(q.vidx))):
            rows.append(('pr', p.material, p.fill_mode, tuple(p.vidx), tuple(p.uidx),
                         p.vert_base, p.vert_count, p.uv_base, p.uv_count, p.df_base))
    return art.name, rows


# ----------------------------------------------------------------- minimal ---

def test_minimal(template):
    tri = Prim(material=0, vidx=[0, 1, 2], uidx=[0, 1, 2], vert_count=3, uv_count=3)
    lod = Lod(verts=[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 0.0, 10.0)],
              uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
              df=[0xFFFFFFFF] * 3, prims=[tri])
    art = Article('RD0000C (1 PRIMARY)', {0: lod})
    blob = build_crp([art], [Material('0000', template)])
    with open(os.path.join(SCRATCH, 'minimal.crp'), 'wb') as f:
        f.write(blob)

    c = reader.Crp(blob)
    problems = []
    if c.n_articles != 1:
        problems.append('articles=%d' % c.n_articles)
    if c.n_misc != 1:
        problems.append('misc=%d' % c.n_misc)
    if c.name(0) != 'RD0000C (1 PRIMARY)':
        problems.append('name=%r' % c.name(0))
    b = c.base(0)
    if not b or b['n_lod'] != 1:
        problems.append('base=%r' % b)
    elif b['bbox_max'] != (10.0, 0.0, 10.0):
        problems.append('bbox_max=%r' % (b['bbox_max'],))
    mats = [m for m in c.misc() if m['tag'] == 'mt']
    if len(mats) != 1 or mats[0].get('fsh_tag') != '0000':
        problems.append('material=%r' % mats)
    got = snapshot(c, 0)[1]
    want = [('lod', 0, 3, 3, 3),
            ('v', 0.0, 0.0, 0.0), ('v', 10.0, 0.0, 0.0), ('v', 0.0, 0.0, 10.0),
            ('uv', 0.0, 0.0), ('uv', 1.0, 0.0), ('uv', 0.0, 1.0),
            ('df', (0xFFFFFFFF,) * 3),
            ('pr', 0, 3, (0, 1, 2), (0, 1, 2), 0, 3, 0, 3, 0)]
    if got != want:
        problems.append('geometry mismatch:\n  got  %r\n  want %r' % (got, want))
    print('minimal archive: %d bytes, %s'
          % (len(blob), 'ok' if not problems else '; '.join(problems)))
    return problems


# -------------------------------------------------------------- round-trip ---

def _is_variant(parts):
    """The same ten articles ship on every track and use the variant vertex-set
    encoding (extra `vt` with info 0x10/0x20/0x30, typeByte 0x83): animated
    trackside people, i.e. vertex morph poses rather than LODs. The writer
    models plain LODs only, so they are reported and skipped."""
    return any(p['tag'] in ('vt', 'uv', 'df') and (p['info'] & 0xFF) > 3
               for p in parts)


def test_roundtrip(name):
    """Re-emit every article of a real track and compare what comes back."""
    src = reader.read('%s/%s.crp' % (TRACK_DIR, name))
    picks, arts, originals = [], [], []
    skipped, dropped = 0, collections.Counter()
    for i in range(src.n_articles):
        parts = src.parts(i)
        if not parts or not src.base(i) or _is_variant(parts):
            skipped += 1
            continue
        for tag in {p['tag'] for p in parts} - KNOWN_PARTS:
            dropped[tag] += 1
        picks.append(i)
        originals.append(snapshot(src, i))
        arts.append(extract(src, i))

    blob = build_crp(arts, [Material('0000', material_template(src))])
    with open(os.path.join(SCRATCH, name + '-roundtrip.crp'), 'wb') as f:
        f.write(blob)

    out = reader.Crp(blob)
    bad = ['%s article %d (%s)' % (name, idx, originals[k][0])
           for k, idx in enumerate(picks) if snapshot(out, k) != originals[k]]
    verts = sum(len(l.verts) for a in arts for l in a.lods.values())
    prims = sum(len(l.prims) for a in arts for l in a.lods.values())
    trs = sum(1 for a in arts if a.transform is not None)
    print('%-11s %5d/%-5d art  vert %7d  prim %6d  tr %4d  %9d B  %s'
          % (name, len(picks), src.n_articles, verts, prims, trs, len(blob),
             'ok' if not bad else '%d mismatches' % len(bad)))
    if dropped:
        print('            parts not written %s, articles skipped %d'
              % (dict(dropped), skipped))
    for line in bad[:5]:
        print('   ', line)
    return bad


TRACKS = ('alps', 'coastal', 'monaco1', 'skidpad', 'castle', 'autobahn',
          'canyon', 'industrial')

if __name__ == '__main__':
    os.makedirs(SCRATCH, exist_ok=True)
    fails = test_minimal(material_template(reader.read(ALPS)))
    for track in TRACKS:
        fails += test_roundtrip(track)
    print('\n' + ('ALL PASSED' if not fails else 'FAILURES'))
    sys.exit(1 if fails else 0)
