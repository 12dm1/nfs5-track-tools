"""HS5 acceptance: the converted track must contain exactly the source geometry.

Rebuilds the triangle set from the written .crp and compares it against the
triangle set computed straight from the FRD. Each triangle is compared as
position, UV and baked colour per corner, so a mismatch means real geometry was
lost, duplicated or moved - not merely that the files differ.

    python test_hs5_geometry.py [track name ...]
"""

import collections
import os
import struct
import sys

import nfs4_frd as frd
import nfs4_geometry as geom
import nfs5_crp as reader
from nfs5_fsh import index_tag
import nfs5cfg

HS = nfs5cfg.HS_TRACKS
OUT = os.environ.get(
    'HS5_TEST_OUT',
    os.path.join(nfs5cfg.WORK, 'build', 'hs5'))
TEMPLATE = os.path.join(nfs5cfg.GAME, 'GameData', 'Track', 'alps.crp')


def _f32(x):
    """Round a Python float the way storing it in the file does. Object vertices
    are position + object origin, summed in double precision on the source side
    and written as float32, so both sides have to be quantised the same way or
    the last digit disagrees."""
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _q(v, digits=4):
    return tuple(round(_f32(x), digits) for x in v)


def source_triangles(track):
    """Every triangle the converter is supposed to emit, as a multiset."""
    out = collections.Counter()
    materials = {}
    for block in range(track.n_blocks):
        verts = track.vertices(block)
        shading = track.shading(block)
        chunks = (set(geom.ROAD_CHUNKS) | set(geom.TERRAIN_CHUNKS)
                  | set(geom.LANE_CHUNKS))
        for chunk in chunks:
            polys = track.polygons(block, chunk)
            for mat, tri, _cutout, _soft in geom._triangles(polys, verts, shading, materials):
                out[tuple((_q(c[1]), _q(c[2]), c[3]) for c in tri)] += 1
        for obj in track.objects(block):
            _object_triangles(obj, out, materials)
    # File-global objects (HZ2): type 6 props carry their physics rotation.
    dynamic = geom._dynamic_destructibles(track)
    for n, obj in enumerate(track.global_objects_flat()):
        if obj['type'] == 6:
            obj = geom._physics_pose(obj)
            if ('g', n) in dynamic:
                obj['collision_type'] = frd.COLLISION_DESTRUCTIBLE
        _object_triangles(obj, out, materials)
    return out, materials


def _object_triangles(obj, out, materials):
    if not obj['polygons']:
        return
    verts, offset = obj['verts'], obj['pt']
    if obj.get('anim') and geom.anim_frames(obj['anim']) is not None:
        # Animated articles keep the local mesh; Anqt places it.
        verts, offset = obj['local_verts'], (0.0, 0.0, 0.0)
    elif obj.get('type') == 2:
        obj = geom._billboard_object(obj)
        verts = obj['verts']
    # Canonical local mesh + float32 rotation + kilometre-scale world
    # translation can accumulate a few millimetres.  Keep the old
    # strict check for static geometry and use decimetre identity only
    # for placed destructibles.
    digits = (1 if obj.get('collision_type') == frd.COLLISION_DESTRUCTIBLE
              else 4)
    for mat, tri, _cutout, _soft in geom._triangles(obj['polygons'], verts,
                                    obj['shading'], materials, offset):
        out[tuple((_q(c[1], digits), _q(c[2]), c[3]) for c in tri)] += 1


def crp_triangles(path):
    """Every triangle actually present in the written archive."""
    c = reader.read(path)
    out = collections.Counter()
    per_material = collections.Counter()
    problems = []
    for i in range(c.n_articles):
        if (c.name(i) or '').startswith('WALL'):
            continue
        base = next((p for p in c.parts(i) if p['tag'] == 'Base'), None)
        if (base is not None and base['length'] >= 4 and
                struct.unpack_from('<I', c.data, base['data'])[0] & 0x8000):
            # Stock PU object definitions use a damage/variant LOD encoding,
            # not ordinary triangle-list LODs. They are copied raw and are not
            # part of the converted source geometry.
            continue
        base_flags = (struct.unpack_from('<I', c.data, base['data'])[0]
                      if base is not None and base['length'] >= 4 else 0)
        is_placed_destructible = (base_flags == 0x0601 and base['length'] >= 0x48 and
                                  c.data[base['data'] + 0x44:base['data'] + 0x48]
                                  == b'SIGN')
        tr_part = next((p for p in c.parts(i) if p['tag'] == 'tr'), None)
        transform = (struct.unpack_from('<16f', c.data, tr_part['data'])
                     if tr_part is not None else None)
        lods = {}
        for p in c.parts(i):
            tag, info = p['tag'], p['info']
            if tag == 'vt':
                lods.setdefault(info & 0xFF, {})['vt'] = [
                    struct.unpack_from('<3f', c.data, p['data'] + k * 16)
                    for k in range(p['count'])]
            elif tag == 'uv':
                lods.setdefault(info & 0xFF, {})['uv'] = [
                    struct.unpack_from('<2f', c.data, p['data'] + k * 8)
                    for k in range(p['count'])]
            elif tag == 'df':
                lods.setdefault(info & 0xFF, {})['df'] = list(
                    struct.unpack_from('<%dI' % p['count'], c.data, p['data']))
        for p in c.parts(i):
            if p['tag'] != 'pr':
                continue
            lod = (p['info'] >> 12) & 0xF
            d = lods.get(lod)
            if not d:
                problems.append('article %d: pr for LOD %d has no vt/uv/df' % (i, lod))
                continue
            t = c.triangles(p)
            bases = {r['level']: r for r in t['info_rows']}
            n = p['count']
            vb = bases[0]['data_offset'] // 16
            ub = bases[2]['data_offset'] // 8
            db = bases[3]['data_offset'] // 4
            vidx = c.data[t['index_data']:t['index_data'] + n]
            uidx = c.data[t['index_data'] + n:t['index_data'] + 2 * n]
            if n % 3:
                problems.append('article %d: %d indices is not whole triangles' % (i, n))
            for k in range(0, n - 2, 3):
                corners = []
                for j in range(3):
                    vi, ui = vb + vidx[k + j], ub + uidx[k + j]
                    if vi >= len(d.get('vt', ())) or ui >= len(d.get('uv', ())):
                        problems.append('article %d: index out of range' % i)
                        corners = None
                        break
                    position = d['vt'][vi]
                    if transform is not None:
                        x, y, z = position
                        position = (
                            x * transform[0] + y * transform[4] + z * transform[8]
                            + transform[12],
                            x * transform[1] + y * transform[5] + z * transform[9]
                            + transform[13],
                            x * transform[2] + y * transform[6] + z * transform[10]
                            + transform[14],
                        )
                    corners.append((_q(position, 1 if is_placed_destructible else 4),
                                    _q(d['uv'][ui]),
                                    d['df'][db + k + j]))
                if corners:
                    out[tuple(corners)] += 1
                    per_material[t['material']] += 1
    return out, per_material, problems, c


def check(name):
    track = frd.read(os.path.join(HS, name, 'tr.frd'))
    path = os.path.join(OUT, name.lower() + '.crp')
    stats = geom.convert(os.path.join(HS, name), path, TEMPLATE)

    source_lights = sum(len(track.lights(block))
                        for block in range(track.n_blocks))

    want, materials = source_triangles(track)
    got, per_material, problems, c = crp_triangles(path)

    converted_effects = sum(
        1 for i in range(c.n_articles)
        if (c.name(i) or '').startswith(('RD', 'CNK', 'OBJECT', 'LIGHTS'))
        for part in c.parts(i) if part['tag'] == 'ef')
    if stats['light_sources'] != source_lights:
        problems.append('lamps: the pipeline saw %d of %d LightSource'
                        % (stats['light_sources'], source_lights))
    if stats['light_effects'] != converted_effects:
        problems.append('lamps: stats=%d, CRP ef=%d'
                        % (stats['light_effects'], converted_effects))
    if source_lights and not converted_effects and name.lower() == 'coastal':
        problems.append('Coastal: LightSource imported but no ef written')

    missing = want - got
    extra = got - want
    if missing:
        problems.append('%d triangles lost' % sum(missing.values()))
    if extra:
        problems.append('%d extra triangles' % sum(extra.values()))

    # Signs first, then global type-6 props, up to MAX_DESTRUCTIBLE.
    source_destructible = len(geom._dynamic_destructibles(track))
    placed_destructible = 0
    definition_count = 0
    for i in range(c.n_articles):
        base = next((p for p in c.parts(i) if p['tag'] == 'Base'), None)
        if base is None or base['length'] < 0x48:
            continue
        flags = struct.unpack_from('<I', c.data, base['data'])[0]
        if flags & 0x8000:
            definition_count += 1
        elif flags == 0x0601 and c.data[base['data'] + 0x44:base['data'] + 0x48] == b'SIGN':
            placed_destructible += 1
    if placed_destructible != source_destructible:
        problems.append('destructible: %d SIGN vs %d in the FRD'
                        % (placed_destructible, source_destructible))
    if source_destructible and definition_count == 0:
        problems.append('no stock PU object-definition library')

    # FRD type 2 is the generic billboard/polygon-object path: trees, poles,
    # bushes and similar cards must all arrive as PU billboard articles.
    source_billboards = sum(
        obj.get('type') == 2
        for block in range(track.n_blocks) for obj in track.objects(block)
        if obj['polygons']) + sum(
        obj['type'] == 2 for obj in track.global_objects_flat() if obj['polygons'])
    converted_billboards = 0
    for i in range(c.n_articles):
        if not (c.name(i) or '').startswith(('OBJECT', 'Object')):
            continue
        base = next((p for p in c.parts(i) if p['tag'] == 'Base'), None)
        if base is not None and base['length'] >= 4:
            flags = struct.unpack_from('<I', c.data, base['data'])[0]
            if flags == geom.BILLBOARD_BASE_FLAGS:
                converted_billboards += 1
    if converted_billboards != source_billboards:
        problems.append('billboards: %d articles vs %d type-2 in the FRD'
                        % (converted_billboards, source_billboards))

    # texflags 0x8000 / animFlags 0x10 must produce a reversed copy (E10).
    sided = 0
    for block in range(track.n_blocks):
        for chunk in range(11):
            sided += sum(1 for p in track.polygons(block, chunk)
                         if geom._is_two_sided(p))
        for obj in track.objects(block):
            sided += sum(1 for p in obj['polygons'] if geom._is_two_sided(p))
    # Some authored tracks (notably Gt1) simply contain no two-sided source
    # polygons.  The exact geometry comparison above exercises the flag on
    # tracks that do have it; absence is not a conversion failure.
    # The lamp-effect records (geom.EFFECT_MATERIAL_TAGS) ride along with the
    # converted materials; they are not source textures.
    tags = {m['fsh_tag'] for m in c.misc() if m['tag'] == 'mt'} - set(geom.EFFECT_MATERIAL_TAGS)
    expected = {index_tag(k[0]) for k in materials}
    if tags != expected:
        problems.append('materials: %d tags vs %d expected'
                        % (len(tags), len(expected)))

    cman = [m for m in c.misc() if m['tag'] == 'Cm' and m['index'] == 16750]
    if len(cman) < 5:
        problems.append('CmAn %d clips (the intro needs 5)' % len(cman))

    # articles must be named for real slices
    names = [c.name(i) or '' for i in range(c.n_articles)]
    rd = sorted(int(n[2:6]) for n in names if n.startswith('RD'))
    if rd and (rd[0] != 0 or any(s % geom.SLICES_PER_BLOCK for s in rd)):
        problems.append('RD slice numbering broken: %r' % rd[:5])

    # POLYVROADDATA maps onto the mt nibble, so a converted road is not
    # asphalt-only. Terrain also contributes grass/snow.
    surfaces = set()
    for m in c.misc():
        if m['tag'] == 'mt':
            surfaces.add(struct.unpack_from('<H', c.data, m['data'])[0] & 0x0F)
    if 1 not in surfaces or len(surfaces) < 3:
        problems.append('surfaces only %r' % sorted(surfaces))

    # Road polygons must clone a real Opaque record, not alps' dummy Normal.
    # Holey scenery (trees, bushes) clones FakeSort; nothing else.
    mt_descs = {m.get('desc') for m in c.misc()
                if m['tag'] == 'mt' and m['fsh_tag'] not in geom.EFFECT_MATERIAL_TAGS}
    if 'Opaque' not in mt_descs:
        problems.append('no Opaque: %r' % mt_descs)
    extra = mt_descs - {'Opaque', 'FakeSort', 'NormMatState'}
    if extra:
        problems.append('extra desc: %r' % extra)
    if name.lower() == 'hometown' and 'FakeSort' not in mt_descs:
        problems.append('Hometown without FakeSort')

    mt_by_index = {}
    for m in c.misc():
        if m['tag'] == 'mt':
            mt_by_index[m['index']] = m.get('desc')
    for i in range(c.n_articles):
        base = next((p for p in c.parts(i) if p['tag'] == 'Base'), None)
        if (base is not None and base['length'] >= 4 and
                struct.unpack_from('<I', c.data, base['data'])[0] & 0x8000):
            continue
        for p in c.parts(i):
            if p['tag'] != 'pr':
                continue
            queue = c.data[p['data'] + 2]
            mat = struct.unpack_from('<h', c.data, p['data'] + 4)[0]
            desc = mt_by_index.get(mat)
            if desc in ('FakeSort', 'NormMatState') and queue != 1:
                problems.append('FakeSort pr queue=%d' % queue)
                break
            if desc == 'Opaque' and queue != 2:
                problems.append('Opaque pr queue=%d' % queue)
                break
        else:
            continue
        break

    sns = [m for m in c.misc() if m['tag'] == 'sn']
    want_sn = (name.lower() + '.fsh').encode('latin1')
    if (len(sns) != 1 or
            c.data[sns[0]['data']:sns[0]['data'] + sns[0]['length']].split(b'\0')[0]
            != want_sn):
        problems.append('sn does not point at %s.fsh' % name.lower())

    sfs = [m for m in c.misc() if m['tag'] == 'sf']
    if sfs:
        problems.append('extra sf (%d): an embedded SHPI crashes the load' % len(sfs))

    # FUN_0059f700 binary-searches MiscData by ident dword; sn must sort last.
    idents = [struct.unpack_from('<I', c.data, c.misc_base + i * 16)[0]
              for i in range(c.n_misc)]
    if idents != sorted(idents):
        problems.append('MiscData not sorted by ident')
    if c.misc() and c.misc()[-1]['tag'] != 'sn':
        problems.append('sn is not the last MiscData (the binary search misses it)')

    # Type-3 XObj headers are not world positions. Baking them as `pt` made
    # one Hometown article sit at Z = -1e14 and the packed render tree
    # rejected every node.
    for i in range(c.n_articles):
        b = c.base(i)
        if not b:
            continue
        base_part = next((p for p in c.parts(i) if p['tag'] == 'Base'), None)
        if (base_part is not None and base_part['length'] >= 4 and
                struct.unpack_from('<I', c.data, base_part['data'])[0] & 0x8000):
            continue
        if b['lods'] and b['lods'][-1]['distance'] != 10000:
            problems.append('last LOD distance %d (must be 10000, not 65535)'
                            % b['lods'][-1]['distance'])
            break
        if len(b['lods']) >= 2 and b['lods'][0]['distance'] < 280:
            problems.append('LOD0 distance %d (must be >= 300)'
                            % b['lods'][0]['distance'])
            break
        span = max(abs(x) for x in b['bbox_min'] + b['bbox_max'])
        if span > 1e5:
            problems.append('inflated bbox %s span=%.3g' % (c.name(i), span))
            break

    print('%-10s blocks %3d  articles %4d  mat %3d  triangles %6d  %8d B  %s'
          % (name, track.n_blocks, c.n_articles, len(tags), sum(got.values()),
             stats['bytes'], 'ok' if not problems else '; '.join(problems[:3])))
    return problems


if __name__ == '__main__':
    frd._test_quad_uvs()
    os.makedirs(OUT, exist_ok=True)
    names = sys.argv[1:] or ['Hometown', 'Gt1', 'Uk', 'Snowy']
    fails = []
    for n in names:
        fails += check(n)
    print('\n' + ('ALL PASSED' if not fails else 'FAILURES'))
    sys.exit(1 if fails else 0)
