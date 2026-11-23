"""Export FRD polygon wall edges and the old vroad envelope for Blender.

Source semantics: e-rk/speedtools, specs/frd.ksy and frd_data.py.
A wall is 16 m, or the height its edge byte gives (1/4 m units, HZ5), and
is shortened when its edge lies under another block's road (Redrock
flyover), not when the same block banks.
No game data is modified.
"""
import argparse
import collections
import hashlib
import json
import math
import struct
from pathlib import Path

import nfs4_frd

# Display height is 16 m. Only a true overpass is shortened: another
# track block's road contains this edge in XZ, with at least MIN_DECK_M
# of air, and the blocks are not neighbours. Same-block height jumps are
# banking; they made the previous sawtooth on the Redrock spiral.
MAX_WALL_M = 16.0
MIN_WALL_M = 0.6
OVERPASS_GAP_M = 0.4
MIN_DECK_M = 2.5
MIN_BLOCK_SEP = 8
CELL_M = 8.0
SAMPLES = 6
OLD_CORRIDOR_M = 16.0
# HZ5: the per-edge byte of POLYVROADDATA (+4..+7) is the wall's own height
# in 1/4 m; 0xFF is a full wall. Atlantica's ramps: values 24/26/28/30/32 on
# edges based at 3.4/2.8/2.3/1.8/1.3 m all top out at 9.3 m, the deck above
# (9.7 m); 1 and 2 are kerbs. Taking 16 m for every edge walled the decks in,
# so a car could drive up but never drop off.
EDGE_HEIGHT_UNIT_M = 0.25
EDGE_FULL = 0xFF
# Pull every wall edge this far (m, XZ) towards the road polygon it bounds.
# Positive = the collision stands in front of the visible wall. 0 keeps the
# FRD edge; the wrapper already collides the car as its OBB.
WALL_INSET_M = 0.0


def wall_segments(track):
    result = []
    for block in range(track.n_blocks):
        verts = track.vertices(block)
        polys = track.polygons(block, 4)
        for rec in track.polyvroad(block):
            if not rec['wall_collision']:
                continue
            pi = rec['vroad_entry']
            face = polys[pi]['vertex']
            corners = [verts[i] for i in dict.fromkeys(face)]
            inner = (sum(p[0] for p in corners) / len(corners),
                     sum(p[2] for p in corners) / len(corners))
            for edge, enabled in enumerate(rec['wall_edges']):
                a, b = verts[face[edge]], verts[face[(edge + 1) % 4]]
                if enabled and a != b:
                    value = rec['flags'][edge]
                    result.append(dict(block=block, polygon=pi, edge=edge,
                                       a=a, b=b, inner=inner,
                                       edge_height=(None if value == EDGE_FULL
                                                    else value * EDGE_HEIGHT_UNIT_M),
                                       finite_height=rec['finite_height'],
                                       flags=rec['flags'].hex()))
    return result


def components(segments):
    adjacent = collections.defaultdict(set)
    for i, s in enumerate(segments):
        for p in (s['a'], s['b']):
            adjacent[tuple(round(x, 3) for x in p)].add(i)
    seen, out = set(), []
    for start in range(len(segments)):
        if start in seen:
            continue
        todo, group = [start], []
        seen.add(start)
        while todo:
            i = todo.pop()
            group.append(i)
            for p in (segments[i]['a'], segments[i]['b']):
                for j in adjacent[tuple(round(x, 3) for x in p)] - seen:
                    seen.add(j)
                    todo.append(j)
        out.append(group)
    return sorted(out, key=len, reverse=True)


def _cell(x, z):
    return (int(x // CELL_M), int(z // CELL_M))


def _barycentric_xz(tri, x, z):
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri
    den = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2)
    if abs(den) < 1e-8:
        return None
    w0 = ((z1 - z2) * (x - x2) + (x2 - x1) * (z - z2)) / den
    w1 = ((z2 - z0) * (x - x2) + (x0 - x2) * (z - z2)) / den
    w2 = 1.0 - w0 - w1
    if w0 < -0.02 or w1 < -0.02 or w2 < -0.02:
        return None
    return w0 * y0 + w1 * y1 + w2 * y2


def road_index(track):
    """High-res road triangles hashed on XZ. Terrain is ignored."""
    triangles = []
    grid = collections.defaultdict(list)
    for block in range(track.n_blocks):
        verts = track.vertices(block)
        for poly in track.polygons(block, 4):
            idx = list(dict.fromkeys(poly['vertex']))
            if len(idx) < 3:
                continue
            pts = [verts[i] for i in idx]
            faces = [tuple(pts[:3])]
            if len(pts) == 4:
                faces.append((pts[0], pts[2], pts[3]))
            for tri in faces:
                n = len(triangles)
                triangles.append(dict(tri=tri, block=block))
                xs = [p[0] for p in tri]
                zs = [p[2] for p in tri]
                x0, x1 = int(min(xs) // CELL_M), int(max(xs) // CELL_M)
                z0, z1 = int(min(zs) // CELL_M), int(max(zs) // CELL_M)
                for cx in range(x0, x1 + 1):
                    for cz in range(z0, z1 + 1):
                        grid[(cx, cz)].append(n)
    return triangles, grid


def display_height(a, b, wall_block, triangles, grid):
    """16 m, unless this edge sits under a distant block's road."""
    height = MAX_WALL_M
    for s in range(SAMPLES):
        t = s / (SAMPLES - 1)
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        z = a[2] + (b[2] - a[2]) * t
        cx, cz = _cell(x, z)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for rec in (triangles[n] for n in grid.get((cx + dx, cz + dz), ())):
                    if abs(rec['block'] - wall_block) < MIN_BLOCK_SEP:
                        continue
                    road_y = _barycentric_xz(rec['tri'], x, z)
                    if road_y is None:
                        continue
                    gap = road_y - y
                    if gap < MIN_DECK_M:
                        continue
                    if gap > MAX_WALL_M:
                        continue
                    cut = gap - OVERPASS_GAP_M
                    if cut < height:
                        height = cut
    if height < MIN_WALL_M:
        return MIN_WALL_M
    if height > MAX_WALL_M:
        return MAX_WALL_M
    return height


class Obj:
    def __init__(self, mtl):
        self.lines = ['# Game coordinates: Y up; dimensions in meters',
                      'mtllib ' + mtl]
        self.n = 0

    def group(self, name, material):
        self.lines += ['o ' + name, 'usemtl ' + material]

    def face(self, vertices):
        self.lines += ['v %.6f %.6f %.6f' % tuple(v) for v in vertices]
        self.lines.append('f ' + ' '.join(str(self.n + i + 1)
                                          for i in range(len(vertices))))
        self.n += len(vertices)

    def wall(self, a, b, height):
        self.face([a, b, (b[0], b[1] + height, b[2]),
                   (a[0], a[1] + height, a[2])])

    def save(self, path):
        path.write_text('\n'.join(self.lines) + '\n', encoding='utf-8')


def annotate_heights(track, segments):
    triangles, grid = road_index(track)
    for s in segments:
        s['height'] = display_height(s['a'], s['b'], s['block'], triangles, grid)
        s['overpass'] = s['height'] < MAX_WALL_M - 0.05
        if s.get('edge_height') is not None:
            s['height'] = min(s['height'], s['edge_height'])


def inset_edge(a, b, inner, inset):
    """Move edge a-b by `inset` metres in XZ towards the point `inner`."""
    if not inset:
        return a, b
    dx, dz = b[0] - a[0], b[2] - a[2]
    length = math.hypot(dx, dz)
    if length < 1e-6:
        return a, b
    nx, nz = -dz / length, dx / length
    if (inner[0] - a[0]) * nx + (inner[1] - a[2]) * nz < 0:
        nx, nz = -nx, -nz
    return ((a[0] + nx * inset, a[1], a[2] + nz * inset),
            (b[0] + nx * inset, b[1], b[2] + nz * inset))


def _oriented(a, b, inner):
    """a, b with `inner` (the free side) on the left: cross(b - a, inner - a) > 0
    in XZ. FRD winding already gives that for all but a few odd polygons."""
    cross = (b[0] - a[0]) * (inner[1] - a[2]) - (b[2] - a[2]) * (inner[0] - a[0])
    return (a, b) if cross >= 0 else (b, a)


# HZ7: NFS4 static scenery (RefExtraObject.collision_type 1: poles, cacti,
# lamp posts, rocks, trees) stops the car; PU had no collision for it. Each
# steep face of such an object becomes a one-sided wall at its foot: the
# lowest OBJECT_BAND_M of the face, trimmed to the opaque texels there (a
# crossed-quad palm is 4.6 m wide but its trunk is not), as tall as the face;
# see-through ones keep only a 10 cm trunk (below).
# The base is the face's own bottom, so the top of an arch over the road
# stands too high to touch a car driving under it.
OBJECT_BAND_M = 2.0
OBJECT_STEEP = 0.7          # |n.y| / |n| below this is a face, not a roof
OBJECT_SAMPLES = 16
OBJECT_ALPHA_MIN = 128
OBJECT_MIN_WIDTH_M = 0.15
OBJECT_BIN_M = 0.25         # sample spacing along a face, at least
OBJECT_GAP_M = 0.8          # a clear gap wider than this splits the wall
# A see-through texture (tree, palm, cactus, sprite pole) collides with its
# trunk only: the opaque runs of the lowest OBJECT_TRUNK_BAND_M, each shrunk
# to OBJECT_TRUNK_M at its middle, so low branches stay drive-through.
OBJECT_TRUNK_BAND_M = 0.5
OBJECT_TRUNK_M = 0.10


def _newell(points):
    n = [0.0, 0.0, 0.0]
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


class _Alpha:
    """Alpha of the installed track textures (ARGB8888 level 0), by FRD id."""

    def __init__(self, fsh_path):
        self.cache = {}
        self.data = self.entries = None
        if fsh_path and Path(fsh_path).is_file():
            from ea_refpack import fsh_dir, load_maybe_qfs
            self.data = load_maybe_qfs(str(fsh_path))
            self.entries = {e['tag']: e for e in fsh_dir(self.data)[1]}

    def lookup(self, texture_id):
        """(w, h, alpha bytes) or None when unknown/opaque-only."""
        if texture_id in self.cache:
            return self.cache[texture_id]
        found = None
        if self.entries is not None:
            from nfs5_fsh import CODE_ARGB8888, index_tag
            e = self.entries.get(index_tag(texture_id))
            if e is not None and e['code'] & 0x7F == CODE_ARGB8888 & 0x7F:
                w, h = e['w'], e['h']
                px = self.data[e['off'] + 16:e['off'] + 16 + w * h * 4]
                alpha = px[3::4]
                if len(alpha) == w * h and min(alpha) < OBJECT_ALPHA_MIN:
                    found = (w, h, alpha)
        self.cache[texture_id] = found
        return found

    def opaque(self, texture_id, u, v):
        tex = self.lookup(texture_id)
        if tex is None:
            return True
        w, h, alpha = tex
        # quad_uvs against the converted .fsh rows: v = 0 is row 0. Checked on
        # Atlantica's palms: the other way round the foot band reads the crown
        # (2.0 m wide instead of a 0.25 m trunk).
        x = min(w - 1, max(0, int((u % 1.0 if u != 1.0 else 0.999) * w)))
        y = min(h - 1, max(0, int((v % 1.0 if v != 1.0 else 0.999) * h)))
        return alpha[y * w + x] >= OBJECT_ALPHA_MIN


def _face_segments(points, uvs, texture_id, alpha):
    """One steep face -> [(a, b, height)] at its foot: one per opaque run
    along the face, so a see-through gap (a gate, a portal) stays open."""
    n = _newell(points)
    n = (-n[0], -n[1], -n[2])           # FRD faces wind clockwise seen from outside
    nl = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    hl = math.hypot(n[0], n[2])
    if nl < 1e-6 or abs(n[1]) > OBJECT_STEEP * nl:
        return []
    nx, nz = n[0] / hl, n[2] / hl
    tx, tz = nz, -nx                    # along the face, free side on the left
    y0 = min(p[1] for p in points)
    y1 = max(p[1] for p in points)
    quad = points if len(points) == 4 else points + [points[-1]]
    quv = uvs if len(uvs) == 4 else uvs + [uvs[-1]]
    # Sample at most OBJECT_BIN_M apart so runs can be told apart.
    edge = max(math.dist(quad[k], quad[(k + 1) % 4]) for k in range(4))
    steps = max(OBJECT_SAMPLES, min(512, int(math.ceil(edge / OBJECT_BIN_M))))
    hits = []
    depth, base = 0.0, None
    for i in range(steps + 1):
        s = i / steps
        for j in range(steps + 1):
            t = j / steps
            w = ((1 - s) * (1 - t), s * (1 - t), s * t, (1 - s) * t)
            p = [sum(w[k] * quad[k][c] for k in range(4)) for c in range(3)]
            if p[1] > y0 + OBJECT_BAND_M:
                continue
            u = sum(w[k] * quv[k][0] for k in range(4))
            v = sum(w[k] * quv[k][1] for k in range(4))
            if not alpha.opaque(texture_id, u, v):
                continue
            hits.append((p[0] * tx + p[2] * tz, p[1]))
            depth += p[0] * nx + p[2] * nz
            base = p[1] if base is None or p[1] < base else base
    if not hits:
        return []
    depth /= len(hits)
    trunk = alpha.lookup(texture_id) is not None
    if trunk:
        hits = [h for h in hits if h[1] <= base + OBJECT_TRUNK_BAND_M]
    hits = sorted(h[0] for h in hits)
    runs = [[hits[0], hits[0]]]
    for along in hits[1:]:
        if along - runs[-1][1] > OBJECT_GAP_M:
            runs.append([along, along])
        else:
            runs[-1][1] = along
    height = min(MAX_WALL_M, max(MIN_WALL_M, y1 - base))
    out = []
    for lo, hi in runs:
        width = OBJECT_TRUNK_M if trunk else OBJECT_MIN_WIDTH_M
        if trunk or hi - lo < width:
            mid = 0.5 * (lo + hi)
            lo, hi = mid - 0.5 * width, mid + 0.5 * width
        out.append(((nx * depth + tx * lo, base, nz * depth + tz * lo),
                    (nx * depth + tx * hi, base, nz * depth + tz * hi), height))
    return out


def object_segments(track, fsh_path=None):
    """HZ7: one-sided walls around NFS4 static scenery (collision_type 1)."""
    alpha = _Alpha(fsh_path)
    out = []
    for block in range(track.n_blocks):
        for obj in track.objects(block):
            if obj['type'] != 4 or obj['collision_type'] != nfs4_frd.COLLISION_STATIC:
                continue
            verts, pt = obj['verts'], obj['pt']
            for poly in obj['polygons']:
                ids = list(dict.fromkeys(poly['vertex']))
                if len(ids) < 3 or max(ids) >= len(verts):
                    continue
                order = [poly['vertex'].index(i) for i in ids]
                uv = nfs4_frd.quad_uvs(poly)
                points = [tuple(verts[i][c] + pt[c] for c in range(3)) for i in ids]
                uvs = [uv[k] for k in order]
                for a, b, h in _face_segments(points, uvs, poly['texture_id'], alpha):
                    out.append(dict(a=a, b=b, height=h, block=block))
                    if not poly['cull']:    # two-sided face: wall both ways
                        out.append(dict(a=b, b=a, height=h, block=block))
    return out


# HZ7 tail after the HSW1 records: 'HSX1', uint32 flags, uint32 count, then
# `count` more 7-float records (object walls). Flag 1: every record, walls
# included, has its free side on the left of a->b (one-sided in XZ). A
# wrapper without HZ7 reads `count` HSW1 records and ignores the tail.
HSX_ONE_SIDED = 1


def write_hsw(path, segments, inset=None, objects=()):
    """Sidecar the wrapper loads: HSW1 + count + 7 floats per edge + HSX1 tail."""
    inset = WALL_INSET_M if inset is None else inset
    blob = [struct.pack('<4sI', b'HSW1', len(segments))]
    for s in segments:
        a, b = inset_edge(s['a'], s['b'], s['inner'], inset)
        a, b = _oriented(a, b, s['inner'])
        blob.append(struct.pack('<7f', a[0], a[1], a[2], b[0], b[1], b[2],
                                s['height']))
    blob.append(struct.pack('<4sII', b'HSX1', HSX_ONE_SIDED, len(objects)))
    for s in objects:
        a, b = s['a'], s['b']
        blob.append(struct.pack('<7f', a[0], a[1], a[2], b[0], b[1], b[2],
                                s['height']))
    path.write_bytes(b''.join(blob))


def convert_hsw(source, dest, inset=None, fsh=None):
    """`fsh`: the installed track textures for trimming object walls to
    their opaque texels (default: <dest>.fsh beside the sidecar)."""
    source = Path(source)
    dest = Path(dest)
    track = nfs4_frd.read(str(source))
    segments = wall_segments(track)
    annotate_heights(track, segments)
    objects = object_segments(track, fsh if fsh is not None else dest.with_suffix('.fsh'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_hsw(dest, segments, inset, objects)
    n_short = sum(1 for s in segments if s['overpass'])
    n_finite = sum(1 for s in segments if s.get('edge_height') is not None)
    print('%s walls=%d overpass=%d finite=%d objects=%d inset=%.2f -> %s' % (
        source.parent.name, len(segments), n_short, n_finite, len(objects),
        WALL_INSET_M if inset is None else inset, dest))
    return len(segments)


def export(source, directory):
    track = nfs4_frd.read(str(source))
    segments = wall_segments(track)
    annotate_heights(track, segments)
    groups = components(segments)
    directory.mkdir(parents=True, exist_ok=True)
    prefix = source.parent.name.lower()
    mtl = prefix + '-walls.mtl'
    (directory / mtl).write_text(
        'newmtl actual_wall\nKd 1 0.55 0.05\n'
        'newmtl overpass_wall\nKd 1 0.1 0.2\n'
        'newmtl old_corridor\nKd 0.05 0.65 1\n'
        'newmtl road\nKd 0.35 0.35 0.35\n', encoding='utf-8')
    actual, old, road = Obj(mtl), Obj(mtl), Obj(mtl)
    summary = []
    n_short = sum(1 for s in segments if s['overpass'])
    short_h = [s['height'] for s in segments if s['overpass']]
    for n, group in enumerate(groups):
        points = [segments[i][k] for i in group for k in ('a', 'b')]
        center = [sum(p[k] for p in points) / len(points) for k in range(3)]
        degrees = collections.Counter(tuple(round(x, 3) for x in p)
                                      for p in points)
        summary.append(dict(component=n, segments=len(group), center=center,
                            endpoint_degrees=dict(collections.Counter(
                                degrees.values()))))
        for overpass in (False, True):
            tag = 'overpass' if overpass else 'full'
            mat = 'overpass_wall' if overpass else 'actual_wall'
            actual.group('FRD_walls_%02d_%s' % (n, tag), mat)
            for i in group:
                s = segments[i]
                if s['overpass'] == overpass:
                    actual.wall(s['a'], s['b'], s['height'])
    for side, sign in [('left_wall', -1), ('right_wall', 1)]:
        old.group('OLD_vroad_' + side, 'old_corridor')
        points = []
        for i in range(track.n_vroad):
            v = track.vroad(i)
            points.append(tuple(v['ref'][k] + sign * v[side] * v['right'][k]
                                for k in range(3)))
        for a, b in zip(points, points[1:] + points[:1]):
            old.wall(a, b, OLD_CORRIDOR_M)
    for block in range(track.n_blocks):
        road.group('Road_block_%03d' % block, 'road')
        vs = track.vertices(block)
        for poly in track.polygons(block, 4):
            road.face([vs[i] for i in dict.fromkeys(poly['vertex'])])
    actual.save(directory / (prefix + '-actual-walls.obj'))
    old.save(directory / (prefix + '-old-corridor.obj'))
    road.save(directory / (prefix + '-road-context.obj'))
    write_hsw(directory / (prefix + '.hsw'), segments)
    slim = []
    for s in segments:
        slim.append(dict(block=s['block'], polygon=s['polygon'], edge=s['edge'],
                         a=[round(x, 4) for x in s['a']],
                         b=[round(x, 4) for x in s['b']],
                         height=round(s['height'], 3),
                         overpass=s['overpass'],
                         finite_height=s['finite_height'],
                         flags=s['flags']))
    report = dict(
        source=str(source.resolve()),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        wall_segments=len(segments),
        overpass_segments=n_short,
        overpass_height_m=None if not short_h else dict(
            min=round(min(short_h), 3), max=round(max(short_h), 3),
            median=round(sorted(short_h)[len(short_h) // 2], 3)),
                  display_height_m=dict(full=MAX_WALL_M, min=MIN_WALL_M,
                              deck=MIN_DECK_M, block_sep=MIN_BLOCK_SEP,
                              gap=OVERPASS_GAP_M),
        components=summary)
    (directory / (prefix + '-wall-segments.json')).write_text(
        json.dumps(dict(report, segments=slim), indent=2) + '\n',
        encoding='utf-8')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--hsw-only', action='store_true',
                   help='Write the sidecar .hsw without Blender OBJ files')
    p.add_argument('--wall-inset', type=float, default=None, metavar='M',
                   help='pull walls towards the road by M metres (default %.2f)'
                        % WALL_INSET_M)
    a = p.parse_args()
    if a.hsw_only:
        convert_hsw(a.source, a.output, a.wall_inset)
    else:
        export(a.source, a.output)
