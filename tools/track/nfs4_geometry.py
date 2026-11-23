"""HS5: NFS4 track geometry -> NFS5 karT articles.

Maps the High Stakes block model onto the Porsche Unleashed slice model. The
two line up naturally (HS1): an NFS4 track block covers 8 vroad points, a PU
road chunk covers 8 slices, both worlds are in metres with the same axes, so
block b becomes the articles at slice b*8 and nothing is rescaled.

Per block:

    RD%04dC (1 PRIMARY)   road surface, from the track chunks
    CNK%04dL (1 TERRAIN)  scenery left of the road centre line
    CNK%04dR (1 TERRAIN)  scenery right of it
    OBJECT%d              one per attached XObj

LOD mapping, using the low/medium resolution chunks NFS4 already ships:

    LOD0  chunk 4 HIGH_RES_TRACK + 6 LANES   |  5, 7..10 HIGH_RES_MISC*
    LOD1  chunk 2 MED_RES_TRACK              |  3 MED_RES_MISC
    LOD2  chunk 0 LOW_RES_TRACK              |  1 LOW_RES_MISC

Conversions applied:

  * quad -> two triangles (v0,v1,v2) and (v0,v2,v3); a quad whose last two
    indices repeat is emitted as one triangle
  * two-sided quads (texflags 0x8000, or animFlags 0x10) also emit the
    reversed winding so PU's back-face cull still shows the other side.
    The extra copy shares vertices; from any one viewpoint only one winding
    is front-facing, so the two do not z-fight.
  * UVs are generated from texflags, because NFS4 stores none: every quad maps
    the whole texture and the flags permute the corners the same way as
    speedtools (mirror Y/X, invert = 180, then rotate)
  * baked light: NFS4's per-vertex D3DCOLOR is written out per face corner,
    which is what PU's `df` wants - the same colour simply repeats for every
    corner that uses the vertex
  * XObj geometry is local space with a world `pt`; the translation is baked
    into the vertices, so objects come out in world space like the road chunks
    and need no `tr` matrix. Static objects (type 2/4) store `pt` in the
    header. Animated objects (type 3) do not: header +12 is leftover pointers,
    and using it as a float3 blows the render-tree AABB to 1e14 (Hometown
    OBJECT600_0). They become ANIM* articles instead: local mesh, keyframes
    resampled into PU `Anim`/`Anqt` (see anim_frames).
  * the two file-global XObj chunks (rock spires, hillsides, buildings,
    animated objects, loose type-6 props) are converted too, see
    _global_articles

Primitives are split so that no primitive needs more than 256 vertices, which
is the hard limit imposed by karT's single-byte indices.

    python nfs4_geometry.py <Tracks/Hometown> <out.crp> [--template alps.crp]
"""

import collections
import math
import os
import re
import struct
import sys
import zlib

import nfs4_frd as frd
import nfs5_crp as crp_reader
from nfs5_crp_writer import (Article, Lod, Material, Prim, build_crp,
                             material_template, misc_blobs_from, MiscBlob,
                             raw_articles_with_base_flag, raw_materials_from,
                             source_fsh_blob, Effect, TYPE_BASE)
from nfs5_fsh import (index_tag, cutout_texture_ids, soft_alpha_texture_ids)
import nfs5_track_files as track_files
import nfs5_ambient as ambient
import track_corridor

# chunk index -> LOD, per article kind
ROAD_CHUNKS = {4: 0, 2: 1, 0: 2}
TERRAIN_CHUNKS = {5: 0, 7: 0, 8: 0, 9: 0, 10: 0, 3: 1, 1: 2}
# Lane markings get their own article. They carry their own vertices - not one
# is shared with the road - and sit within +-0.03 m of the road surface, so
# merging them into the road mesh would z-fight. Kept separate here so a depth
# bias or decal pass can be applied later without touching the road geometry.
LANE_CHUNKS = {6: 0}

MAX_PRIM_VERTS = 256
# Every stock PU track caps a `pr` at 252 corners (84 triangles). Larger
# primitives draw, but past ~128 triangles the engine overruns a fixed
# per-primitive buffer and a stray sliver follows the camera (Summit 930,
# Snowy 492 corners; Dolphin at 348 was still fine). Match the stock cap.
MAX_PRIM_CORNERS = 252
SLICES_PER_BLOCK = 8

# PU's stock card foliage (for example TREES_MID2) is an HBillBoard article.
# The same renderer path is used for the other NFS4 type-2 polygon objects;
# the source type is not a semantic "tree" marker.  0x4611 is the Base word
# used by the shipped mid-distance tree cards and is the safest known
# billboard dispatch for imported cards.
BILLBOARD_BASE_FLAGS = 0x4611

# Coastal/Dolphin Cove uses packed LightSource values rather than the simple
# 0xFFFF00NN glow-index form seen on Hometown.  Keep the first mapping
# deliberately conservative; the unknown sentinel is audited but not turned
# into a guessed visual effect.
LIGHT_EFFECTS = {
    0x001D0000: 'lpol',  # regular roadside/pole lights
    0x00190000: 'tunb',  # paired overhead lights in the covered section
    0x005A0066: 'lmpy',  # isolated yellow lamp group
}


def _block_effects(track, block, track_name=''):
    """Convert NFS4 LightSource records to NFS5 type-2 ``ef`` markers."""
    effects = []
    unknown = set()
    for index, (x, y, z, source_type) in enumerate(track.lights(block)):
        source_type &= 0xFFFFFFFF
        # This table is currently evidence-backed for Coastal/Dolphin Cove.
        # Other tracks still pass through this same importer and report their
        # unmapped source types instead of silently dropping the light pass.
        effect_name = (LIGHT_EFFECTS.get(source_type)
                       if track_name.lower() in ('coastal', 'dolphin') else None)
        if effect_name is None:
            unknown.add(source_type)
            continue
        position = (x / 65536.0, y / 65536.0, z / 65536.0)
        # Stock ids look like per-instance hashes. Keep rebuilt CRPs
        # deterministic while making repeated light types unique.
        seed = struct.pack('<3fII', *position, source_type, index)
        effect_id = zlib.crc32(seed) & 0xFFFFFFFF
        effects.append(Effect(position, effect_name, effect_id))
    return effects, unknown

# --- surfaces ---------------------------------------------------------------
#
# The low nibble of a material's word at +0 is the surface type. It was read
# off the shipped tracks rather than guessed: grouping every `mt` record of the
# 15 stock CRPs by that nibble lines the values up with the texture each
# material names, and the names are legible.
#
#   1  asphalt   rd01 rd11 rd22 rd39 rd42..rd69, alks   (239 records)
#   2  a second road surface  rdbb rdbs rdcc rdcf rdch  (24)
#   3  grass     grs7 grsd grsj xgrs, rdg1 rdg3 rdg4    (32)
#   4  stone     rd25..rd33, rdst rdcr rdcs             (46)
#   5  dirt      rddd rddr rddt rdgd rdqd               (27)
#   7  boards    rdb1 rdb2 rdyj rdyk rdyl wldc          (10)
#   8  ice       icyy rda7                              (2)
#   9  snow      snw3 snw7 rdsn rd13 rd14 rda3 rda5     (9)
#
# The upper bits are separate flags: 0010 barriers, 0020 fences, 0030 snow
# walls, 0060 walls, C000 solid scenery (cliffs, trees). A material can carry
# those with a zero nibble, and the engine still builds collision triangles for
# it - monaco1 has 825 index units' worth of C000 primitives at LOD0 and 777 of
# its 14183 tree elements have a zero nibble - they are simply solid rather
# than drivable. Nibble and flags are independent.
SURFACE_NONE = 0x0000
SURFACE_ASPHALT = 0x0001
SURFACE_ROAD2 = 0x0002
SURFACE_GRASS = 0x0003
SURFACE_STONE = 0x0004
SURFACE_DIRT = 0x0005
SURFACE_BOARDS = 0x0007
SURFACE_ICE = 0x0008
SURFACE_SNOW = 0x0009
SURFACE_SOLID = 0xC000          # collides, not drivable

ROAD_SURFACE = SURFACE_ASPHALT
LANE_SURFACE = SURFACE_ASPHALT

# Terrain chunks have no POLYVROADDATA. Grass is the default because it is
# what most of these tracks are; the two winter tracks say so by name.
TERRAIN_SURFACE_DEFAULT = SURFACE_GRASS
TERRAIN_SURFACE = {
    'snowy': SURFACE_SNOW,
    'summit': SURFACE_SNOW,
}

# POLYVROADDATA flags[4] low nibble -> PU mt nibble. HS1 §11.3: 1 is asphalt
# on every track; the rest follow how far the poly sits from the centre line
# on Hometown (1/4/7 on the lane, 11 just past the painted edge, 3/2/0/13
# out on the verge).
NIBBLE_SURFACE = {
    0: SURFACE_GRASS,
    1: SURFACE_ASPHALT,
    2: SURFACE_ROAD2,
    3: SURFACE_GRASS,
    4: SURFACE_STONE,
    5: SURFACE_DIRT,
    6: SURFACE_DIRT,
    7: SURFACE_BOARDS,
    8: SURFACE_ICE,
    9: SURFACE_SNOW,
    10: SURFACE_GRASS,
    11: SURFACE_DIRT,
    12: SURFACE_STONE,
    13: SURFACE_DIRT,
    14: SURFACE_DIRT,
    15: SURFACE_GRASS,
}


def terrain_surface(track_name):
    return TERRAIN_SURFACE.get(track_name.lower(), TERRAIN_SURFACE_DEFAULT)


def surface_from_nibble(nibble, track_name=''):
    if nibble == 15 and track_name.lower() in TERRAIN_SURFACE:
        return TERRAIN_SURFACE[track_name.lower()]
    return NIBBLE_SURFACE.get(nibble, SURFACE_ASPHALT)


class _PrimBuilder:
    """Accumulates triangles for one material until the 256-vertex limit."""

    def __init__(self, material):
        self.material = material
        self.vmap = {}          # source vertex index -> local index
        self.verts = []
        self.umap = {}          # (u, v) -> local index
        self.uvs = []
        self.vidx = []
        self.uidx = []
        self.df = []
        self.queue = 2

    def fits(self, tri_vertices):
        new = sum(1 for v in set(tri_vertices) if v not in self.vmap)
        return (len(self.verts) + new <= MAX_PRIM_VERTS
                and len(self.vidx) + len(tri_vertices) <= MAX_PRIM_CORNERS)

    def add(self, corners):
        """corners: [(source index, position, uv, colour)] * 3"""
        for src, pos, uv, colour in corners:
            local = self.vmap.get(src)
            if local is None:
                local = len(self.verts)
                self.vmap[src] = local
                self.verts.append(pos)
            self.vidx.append(local)
            u = self.umap.get(uv)
            if u is None:
                u = len(self.uvs)
                self.umap[uv] = u
                self.uvs.append(uv)
            self.uidx.append(u)
            self.df.append(colour)


def _emit(builders, lod_data):
    """Flush primitive builders into one Lod, laying out vt/uv/df as the
    concatenation of each primitive's own window."""
    for b in builders:
        if not b.vidx:
            continue
        prim = Prim(material=b.material, vidx=b.vidx, uidx=b.uidx,
                    vert_base=len(lod_data.verts), vert_count=len(b.verts),
                    uv_base=len(lod_data.uvs), uv_count=len(b.uvs),
                    df_base=len(lod_data.df), queue=b.queue)
        lod_data.verts.extend(b.verts)
        lod_data.uvs.extend(b.uvs)
        lod_data.df.extend(b.df)
        lod_data.prims.append(prim)


def _is_two_sided(poly):
    """NFS4 stores 'do not cull the back face' on the polygon, not the material.

    texflags bit 0x8000 is the usual switch (parser field `cull` is False).
    animFlags bit 0x10 is the same intent on a few misc polys (HS1 E10);
    on Hometown the two bits never overlap.
    PU culls per draw, so the converter duplicates the triangle with
    reversed winding instead of trying to turn cull off for one material.
    """
    return (not poly['cull']) or bool(poly['anim_flags'] & 0x10)


def _triangles(polys, verts, shading, materials, offset=(0.0, 0.0, 0.0),
               keep=None, surface=SURFACE_NONE, poly_surfaces=None,
               cutout_ids=None, allow_cutout=False, soft_ids=None):
    """Yield (material, corners, cutout, soft_alpha) for every triangle."""
    holes = cutout_ids or set()
    soft_masks = soft_ids or set()
    for i, poly in enumerate(polys):
        if keep is not None and not keep(poly):
            continue
        v = poly['vertex']
        uv = frd.quad_uvs(poly)
        surf = surface
        if poly_surfaces is not None and i < len(poly_surfaces):
            mapped = poly_surfaces[i]
            if mapped is not None:
                surf = mapped
        # Keyed on the surface as well as the texture: one High Stakes texture
        # can appear on both the road and the shoulder, and the two need
        # different surfaces. The originals duplicate materials the same way -
        # monaco1 carries `rdma` twice and alps `rda1` twice.
        # A third key, cutout, keeps holey scenery off the Opaque road slot:
        # the same bitmap can be a drain on the asphalt and a bush beside it.
        cutout = allow_cutout and poly['texture_id'] in holes
        soft = allow_cutout and poly['texture_id'] in soft_masks
        # A light shaft must not become a collidable grass/terrain material.
        if soft:
            surf = SURFACE_NONE
        mat = materials.setdefault((poly['texture_id'], surf, cutout, soft),
                                   len(materials))
        corners = []
        for corner in range(4):
            idx = v[corner]
            if idx >= len(verts):
                corners = None
                break
            p = verts[idx]
            corners.append((idx,
                            (p[0] + offset[0], p[1] + offset[1], p[2] + offset[2]),
                            uv[corner],
                            shading[idx] if idx < len(shading) else 0xFFFFFFFF))
        if corners is None:
            continue
        tris = [(0, 1, 2)] if v[2] == v[3] else [(0, 1, 2), (0, 2, 3)]
        two_sided = _is_two_sided(poly)
        for a, b, c in tris:
            if len({v[a], v[b], v[c]}) < 3:
                continue
            yield mat, [corners[a], corners[b], corners[c]], cutout, soft
            if two_sided:
                yield mat, [corners[c], corners[b], corners[a]], cutout, soft


def _build_lods(groups, materials, cutout_ids=None, allow_cutout=False,
                soft_ids=None):
    """groups: {lod: [(polys, verts, shading, offset, keep)]} -> {lod: Lod}"""
    lods = {}
    for lod, sources in sorted(groups.items()):
        data = Lod()
        builders = {}
        order = []
        for item in sources:
            polys, verts, shading, offset, keep, surface = item[:6]
            poly_surfaces = item[6] if len(item) > 6 else None
            for mat, tri, cutout, soft in _triangles(
                    polys, verts, shading, materials, offset, keep, surface,
                    poly_surfaces, cutout_ids, allow_cutout, soft_ids):
                b = builders.get(mat)
                if b is None or not b.fits([c[0] for c in tri]):
                    b = _PrimBuilder(mat)
                    # Stock PU foliage is FakeSort on queue 1 (004666AF).
                    # Soft light shafts use the same sorted alpha queue as
                    # cutouts, but retain graduated alpha in their texture.
                    b.queue = 1 if cutout or soft else 2
                    builders[mat] = b
                    order.append(b)
                b.add(tri)
        _emit(order, data)
        if data.prims:
            lods[lod] = data
    return lods


def _billboard_object(obj):
    """Return a type-2 mesh as the crossed-card shape used by PU.

    High Stakes type-2 records are usually one vertical card (occasionally
    several coplanar cards with different textures).  PU's HBillBoard stock
    articles use the same card plus a perpendicular copy, which keeps the
    object readable while the renderer turns the article toward the camera.
    Keep the source card intact and rotate a duplicate around the local Y axis
    through its X/Z centre; the world placement is still applied later.
    """
    verts = list(obj['verts'])
    shading = list(obj['shading'])
    if not verts or not obj['polygons']:
        return obj
    cx = (min(v[0] for v in verts) + max(v[0] for v in verts)) * 0.5
    cz = (min(v[2] for v in verts) + max(v[2] for v in verts)) * 0.5
    offset = len(verts)
    verts.extend((cx - (v[2] - cz), v[1], cz + (v[0] - cx))
                 for v in obj['verts'])
    shading.extend(obj['shading'])
    polygons = list(obj['polygons'])
    for poly in obj['polygons']:
        clone = dict(poly)
        clone['vertex'] = tuple(index + offset for index in poly['vertex'])
        polygons.append(clone)
    result = dict(obj)
    result['verts'] = verts
    result['shading'] = shading
    result['polygons'] = polygons
    return result


def _side(track, block, point):
    """Which side of the road centre line a point falls on."""
    vr = track.vroad(min(block * SLICES_PER_BLOCK + 4, track.n_vroad - 1))
    ref, right = vr['ref'], vr['right']
    d = sum((point[i] - ref[i]) * right[i] for i in range(3))
    return 'R' if d >= 0 else 'L'


def _centroid(poly, verts):
    idx = [i for i in poly['vertex'] if i < len(verts)]
    if not idx:
        return None
    return tuple(sum(verts[i][k] for i in idx) / len(idx) for k in range(3))


def _object_yaw(obj):
    """Horizontal authored axis used to align PU's thin SIGN collision box."""
    points = [(v[0], v[2]) for v in obj['verts']]
    best_dx = best_dz = best_d2 = 0.0
    for i, a in enumerate(points):
        for b in points[i + 1:]:
            dx, dz = b[0] - a[0], b[1] - a[1]
            d2 = dx * dx + dz * dz
            if d2 > best_d2:
                best_dx, best_dz, best_d2 = dx, dz, d2
    if best_d2 < 1e-10:
        return 0.0
    if best_dx < 0.0 or (abs(best_dx) < 1e-5 and best_dz < 0.0):
        best_dx, best_dz = -best_dx, -best_dz
    return math.atan2(best_dz, best_dx)


def _destructible_article(obj, block, number, materials, cutout_ids,
                          soft_ids=None):
    """Convert one NFS4 collision_type=2 XObj to a placed PU SIGN object."""
    yaw = _object_yaw(obj)
    c, s = math.cos(yaw), math.sin(yaw)
    # Make the source object's longest horizontal axis local X.  The inverse
    # rotation in `tr` leaves its rendered pose unchanged while rotating the
    # stock SIGN hitbox onto the authored sign/fence/panel plane.
    verts = [(c * x + s * z, y, -s * x + c * z)
             for x, y, z in obj['verts']]
    group = {0: [(obj['polygons'], verts, obj['shading'],
                  (0.0, 0.0, 0.0), None, SURFACE_NONE)]}
    lods = _build_lods(group, materials, cutout_ids, True, soft_ids)
    if not lods:
        return None
    px, py, pz = obj['pt']
    transform = (c, 0.0, s, 0.0,
                 0.0, 1.0, 0.0, 0.0,
                 -s, 0.0, c, 0.0,
                 px, py, pz, 1.0)
    article = Article('OBJ%04dL (1 NFS4_%d_%d_SGN)'
                      % (block * SLICES_PER_BLOCK, block, number),
                      lods, transform=transform)
    base = bytearray(article.encode_base())
    struct.pack_into('<I', base, 0, 0x0601)
    base[0x44:0x48] = b'SIGN'
    article.raw_base = bytes(base)
    article.is_destructible = True
    return article


# PU keeps the race cars and the smackables in one object list whose index is
# masked with 0x7F (FUN_004a1280: table[(index & 0x7F) - numcars]), so cars +
# smackables must stay <= 128, and the game itself takes at most 125
# smackables. Empire (127 placed signs), Lost Canyons (176) and Redrock (188)
# crashed while loading any race with 4 or more cars. 9 cars is the most a race
# holds with traffic (TR1), so at most 119 objects stay dynamic.
MAX_DESTRUCTIBLE = 119


def _dynamic_destructibles(track, limit=None):
    """(block, n) of the collision_type=2 objects that become PU smackables:
    all of them when they fit, else the `limit` nearest to the road; the
    others are not converted at all (user decision 2026-10-14).

    Global type-6 props (cones, tyre stacks, benches, phone boxes) take what
    budget the signs leave, under the key ('g', index into
    global_objects_flat()); the ones left over stay as static scenery, so
    adding them never drops a sign that was dynamic before."""
    limit = MAX_DESTRUCTIBLE if limit is None else limit
    road = [track.vroad(i)['ref'] for i in range(track.n_vroad)]

    def road_distance(pt):
        return min((pt[0] - r[0]) ** 2 + (pt[2] - r[2]) ** 2 for r in road)

    def nearest(found, room):
        if len(found) > room:
            found.sort(key=lambda f: road_distance(f[2]))
        return {(b, n) for b, n, _pt in found[:max(room, 0)]}

    signs = []
    for block in range(track.n_blocks):
        for n, obj in enumerate(track.objects(block)):
            if obj['polygons'] and obj.get('collision_type') == frd.COLLISION_DESTRUCTIBLE:
                signs.append((block, n, obj['pt']))
    props = [('g', n, obj['pt'])
             for n, obj in enumerate(track.global_objects_flat())
             if obj['polygons'] and obj['type'] == 6]
    chosen = nearest(signs, limit)
    return chosen | nearest(props, limit - len(chosen))


# --- global and animated objects ---------------------------------------------
#
# The two file-global XObj chunks hold scenery that belongs to no block:
#   type 1  large static meshes already in world space (pt = 0): France's rock
#           spires, Germany/Hills hillsides, UK and Coastal buildings
#   type 2  cards, as in the blocks
#   type 3  animated objects (also found in a few blocks)
#   type 6  loose props with a 72-byte physics record
# Before HZ2 (2026-10-18) none of them reached the CRP.

# NFS4 AnimDelay is the time each keyframe is held, in 1/64 s.  Inferred from
# speeds, not read from NFS4 code: Park train 1.8 m/key at delay 6 = 68 km/h,
# Hometown plane 12.7 m at 25 = 117 km/h, Summit gondola 4 m at 38 = 25 km/h.
NFS4_ANIM_TICKS = 64.0
# PU plays world animations at a fixed 0.1 s per Anqt key and loops them:
# FUN_004777f0 builds the player with step 0x3DCCCCCD and loop flag 1;
# FUN_00470E90 takes frame % (keys - 2) and interpolates to frame + 1, so the
# last key is never a start point.
PU_ANIM_STEP = 0.1
# Stock castle ANIVAN01 / ANIPICKUP and the autobahn train: single LOD, local
# mesh, no `tr`, world placement only in Anqt.
ANIM_BASE_FLAGS = 0x0610


def _normalise(q):
    s = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / s for c in q)


def _slerp(a, b, t):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0.0:
        b, dot = tuple(-c for c in b), -dot
    if dot > 0.9995:
        return _normalise(tuple(x + (y - x) * t for x, y in zip(a, b)))
    theta = math.acos(dot)
    sa, sb = math.sin((1.0 - t) * theta), math.sin(t * theta)
    s = math.sin(theta)
    return tuple((x * sa + y * sb) / s for x, y in zip(a, b))


def anim_frames(anim):
    """NFS4 keys -> PU keys at PU_ANIM_STEP, or None for a static object.

    The NFS4 clip runs key 0 .. n-1 and starts over.  It is resampled over
    that span; the extra key m (= key 0) closes the loop, and key m+1 is the
    padding PU never starts from.  Paths that do not close (the France
    balloon, Snowy chairlift) snap back during the last 0.1 s.
    """
    keys = anim['keys']
    n = len(keys)
    if n < 2:
        return None
    span = (n - 1) * max(anim['delay'], 1) / NFS4_ANIM_TICKS
    m = max(2, int(round(span / PU_ANIM_STEP)))
    frames = []
    for k in range(m):
        s = k * (n - 1) / m
        i = min(int(s), n - 2)
        f = s - i
        (p0, q0), (p1, q1) = keys[i], keys[i + 1]
        pos = tuple(a + (b - a) * f for a, b in zip(p0, p1))
        frames.append((pos, _slerp(_normalise(q0), _normalise(q1), f)))
    frames += frames[:2]
    # Keep neighbouring quaternions in one hemisphere so PU's interpolation
    # takes the short way round.
    out = [frames[0]]
    for pos, q in frames[1:]:
        if sum(x * y for x, y in zip(out[-1][1], q)) < 0.0:
            q = tuple(-c for c in q)
        out.append((pos, q))
    return out


def _anqt(frames):
    """Anqt keys: quat xyzw, pos xyz + 1.0, scale 1,1,1, 8 bytes of zero.

    The stock tail word is uninitialised memory; FUN_00470F70 reads only
    the quaternion, the position and the three floats at +0x20."""
    return b''.join(struct.pack('<4f4f3f2I', *q, *p, 1.0, 1.0, 1.0, 1.0, 0, 0)
                    for p, q in frames)


def _animated_article(obj, name, materials, cutout_ids, soft_ids=None,
                      sound_tags=None):
    """`sound_tags` {aid: 4-char tag}: Base+0x44 of an object that NFS4 gives
    a sound; the tag's SECTION:Audio in animdefs.txt is written by
    nfs5_ambient (HZ3)."""
    frames = anim_frames(obj['anim'])
    if frames is None:
        return None
    group = {0: [(obj['polygons'], obj['local_verts'], obj['shading'],
                  (0.0, 0.0, 0.0), None, SURFACE_NONE)]}
    lods = _build_lods(group, materials, cutout_ids, True, soft_ids)
    if not lods:
        return None
    anim = struct.pack('<4I', len(frames), 0, 0, 0)
    article = Article(name, lods, base_flags=ANIM_BASE_FLAGS,
                      raw_parts=[('Anim', 0, TYPE_BASE, 1, anim),
                                 ('Anqt', 0, TYPE_BASE, len(frames),
                                  _anqt(frames))])
    tag = (sound_tags or {}).get(obj['anim']['aid'])
    if tag:
        base = bytearray(article.encode_base())
        base[0x44:0x48] = tag.encode('ascii')
        article.raw_base = bytes(base)
    return article


def _physics_pose(obj):
    """Type-6 mesh with its physics rotation applied (pt stays separate).

    The 3x3 is read row-wise (world = x*row0 + y*row1 + z*row2): that way
    Germany's four benches come out exactly square to the road, the other
    reading leaves them at 45-90 degrees."""
    m = obj['physics']['transform']
    verts = [(x * m[0] + y * m[3] + z * m[6],
              x * m[1] + y * m[4] + z * m[7],
              x * m[2] + y * m[5] + z * m[8]) for x, y, z in obj['verts']]
    result = dict(obj)
    result['verts'] = verts
    return result


def _nearest_block(track, point):
    best, best_d = 0, None
    for i in range(0, track.n_vroad, 2):
        r = track.vroad(i)['ref']
        d = (r[0] - point[0]) ** 2 + (r[2] - point[2]) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return min(best // SLICES_PER_BLOCK, track.n_blocks - 1)


def _global_articles(track, dynamic, materials, holes, soft_ids,
                     sound_tags=None):
    """Articles for the file-global objects (see the table above)."""
    articles = []
    for n, obj in enumerate(track.global_objects_flat()):
        if not obj['polygons']:
            continue
        verts = obj['verts']
        centre = tuple(obj['pt'][k] + (min(v[k] for v in verts)
                                       + max(v[k] for v in verts)) * 0.5
                       for k in range(3))
        block = _nearest_block(track, centre)
        slice0 = block * SLICES_PER_BLOCK
        if obj['type'] == 3:
            article = _animated_article(obj, 'ANIM%d_G%d' % (slice0, n),
                                        materials, holes, soft_ids, sound_tags)
            if article is not None:
                articles.append(article)
                continue
        if obj['type'] == 6:
            obj = _physics_pose(obj)
            if ('g', n) in dynamic:
                article = _destructible_article(obj, block, 1000 + n,
                                                materials, holes, soft_ids)
                if article is not None:
                    articles.append(article)
                continue
        billboard = obj['type'] == 2
        if billboard:
            obj = _billboard_object(obj)
        group = {0: [(obj['polygons'], obj['verts'], obj['shading'],
                      obj['pt'], None, SURFACE_NONE)]}
        lods = _build_lods(group, materials, holes, True, soft_ids)
        if lods:
            articles.append(Article(
                'Object%02d_MID2' % n if billboard else 'OBJECT%d_G%d' % (slice0, n),
                lods, base_flags=BILLBOARD_BASE_FLAGS if billboard else None))
    return articles


def build_articles(track, branch=1, terrain=TERRAIN_SURFACE_DEFAULT, name='',
                   cutout_ids=None, extra_objects=(), soft_ids=None,
                   sound_tags=None):
    """FRD -> articles and material slots.

    ``soft_ids`` contains graduated-alpha effects such as the Hometown and
    Dolphin Cove sun shafts. They remain separate from keyed cutouts so their
    collision surface can be forced to zero without changing other polygons.
    """
    materials = {}
    articles = []
    holes = cutout_ids or set()
    dynamic = _dynamic_destructibles(track)
    for block in range(track.n_blocks):
        slice0 = block * SLICES_PER_BLOCK
        verts = track.vertices(block)
        shading = track.shading(block)
        block_effects, unknown_effect_types = _block_effects(
            track, block, name)
        if unknown_effect_types:
            print('HS5: skipped unknown light type(s) in %s block %d: %s'
                  % (name or '<track>', block,
                     ', '.join('0x%08X' % t for t in sorted(unknown_effect_types))))

        road = collections.defaultdict(list)
        for chunk, lod in ROAD_CHUNKS.items():
            polys = track.polygons(block, chunk)
            poly_surfaces = None
            if chunk == 4:
                poly_surfaces = [None] * len(polys)
                for rec in track.polyvroad(block):
                    idx = rec['vroad_entry']
                    if idx < len(polys):
                        poly_surfaces[idx] = surface_from_nibble(
                            rec['surface'], name)
            road[lod].append((polys, verts, shading,
                              (0.0, 0.0, 0.0), None, ROAD_SURFACE,
                              poly_surfaces))
        lods = _build_lods(road, materials, holes, False, soft_ids)
        if lods:
            articles.append(Article('RD%04dC (%d PRIMARY)' % (slice0, branch),
                                    lods, effects=block_effects))
            block_effects = []

        lanes = collections.defaultdict(list)
        for chunk, lod in LANE_CHUNKS.items():
            polys = track.polygons(block, chunk)
            if polys:
                lanes[lod].append((polys, verts, shading, (0.0, 0.0, 0.0),
                                   None, LANE_SURFACE))
        lods = _build_lods(lanes, materials, holes, False, soft_ids)
        if lods:
            articles.append(Article('CNK%04dE (%d LANES)' % (slice0, branch),
                                    lods, effects=block_effects))
            block_effects = []

        sides = {'L': collections.defaultdict(list), 'R': collections.defaultdict(list)}
        for chunk, lod in TERRAIN_CHUNKS.items():
            polys = track.polygons(block, chunk)
            if not polys:
                continue
            split = {'L': [], 'R': []}
            for poly in polys:
                c = _centroid(poly, verts)
                if c is not None:
                    split[_side(track, block, c)].append(poly)
            for side, chosen in split.items():
                if chosen:
                    sides[side][lod].append((chosen, verts, shading,
                                             (0.0, 0.0, 0.0), None, terrain))
        for side in ('L', 'R'):
            lods = _build_lods(sides[side], materials, holes, True, soft_ids)
            if lods:
                articles.append(Article('CNK%04d%s (%d TERRAIN)'
                                        % (slice0, side, branch), lods,
                                        effects=block_effects))
                block_effects = []

        # A malformed/empty block should not lose its light markers.  The
        # article has no triangles, but its Base bounds include the effects,
        # so the renderer can still cull and submit the points correctly.
        if block_effects:
            articles.append(Article('LIGHTS%04d (%d NFS4 LIGHTS)'
                                    % (slice0, branch), {0: Lod()},
                                    effects=block_effects))

        for n, obj in enumerate(track.objects(block)):
            if not obj['polygons']:
                continue
            if obj.get('collision_type') == frd.COLLISION_DESTRUCTIBLE:
                if (block, n) not in dynamic:
                    continue                # over MAX_DESTRUCTIBLE: left out entirely
                article = _destructible_article(obj, block, n, materials,
                                                holes, soft_ids)
                if article is not None:
                    articles.append(article)
                # Never emit a second static copy: it would remain standing
                # after the dynamic object has been hit.
                continue
            if obj.get('anim'):
                article = _animated_article(obj, 'ANIM%d_%d' % (slice0, n),
                                            materials, holes, soft_ids,
                                            sound_tags)
                if article is not None:
                    articles.append(article)
                    continue
            if obj.get('type') == 2:
                obj = _billboard_object(obj)
            group = {0: [(obj['polygons'], obj['verts'], obj['shading'],
                          obj['pt'], None, SURFACE_NONE)]}
            lods = _build_lods(group, materials, holes, True, soft_ids)
            if lods:
                # Type 2 is FRD's polygon/card object class.  Keep its
                # vertices in the same local+placement representation used by
                # the source (the offset is applied while building the PU
                # vertex stream), but select PU's billboard article path.
                # Type 4 remains a normal static article.
                billboard = obj.get('type') == 2
                article_name = ('Object%02d_MID2' % n
                                if billboard else 'OBJECT%d_%d' % (slice0, n))
                articles.append(Article(
                    article_name, lods,
                    base_flags=BILLBOARD_BASE_FLAGS if billboard else None))

    articles.extend(_global_articles(track, dynamic, materials, holes,
                                     soft_ids, sound_tags))

    # Optional objects for a deliberately isolated debug scene.  Their
    # vertices remain local and the caller supplies a destination through the
    # SCN file; this avoids baking the source track's world position into the
    # imported article.  Texture ids are allowed to be remapped by the caller
    # because objects may come from another NFS4 track.
    for item in extra_objects:
        obj = item['object']
        if not obj['polygons']:
            continue
        group = {0: [(obj['polygons'], obj['verts'], obj['shading'],
                      tuple(item.get('offset', (0.0, 0.0, 0.0))),
                      None, item.get('surface', SURFACE_NONE))]}
        lods = _build_lods(group, materials, holes, True, soft_ids)
        if lods:
            articles.append(Article(item['name'], lods))
    return articles, materials


def _retarget_cman(blob, origin, ahead):
    """CmAn header carries two world points; keyframes are car-relative.

    FUN_00471850 loads every MiscData `CmAn`. Without those clips the start
    intro never leaves BumperCam. The 8-char name and 121 x 52-byte Anqt
    keys are copied from the template; only the two header positions move.
    """
    if len(blob) < 0x2C:
        return blob
    out = bytearray(blob)
    struct.pack_into('<3f', out, 8, *origin)
    struct.pack_into('<3f', out, 0x14, *ahead)
    return bytes(out)


# AI1: SimD walls from the real wall edges (track_corridor), not NFS4's envelope.
AI_CORRIDOR_FROM_WALLS = False   # AI1 A/B 2026-10-14: no effect on the AI line, worse respawns; kept for reference


def build_sim_blobs(track, walls=None):
    """`SimD` and `SimT`: the per-slice simulation table the engine looks for.

    FUN_00480080 fetches the MiscData tagged 'SimD' and 'SimT' and prints
    "Could not find sim data in data group" when either is missing, which is
    where a converted track stopped once its render modes were in place.

    SimD is 80 bytes per slice and is NFS4's vroad record almost field for
    field, so it converts directly:

        +0x00 uint32  0
        +0x04 float3  centre
        +0x10 float3  up
        +0x1C float3  forward
        +0x28 float3  right      (equals up x forward in the shipped data)
        +0x34 uint32  0x00018000
        +0x38 uint32  1
        +0x3C uint32  1
        +0x40 float4  leftWall, rightWall, laneLeft, laneRight

    SimT lists the length of each route segment; the lengths always sum to the
    SimD record count (alps 1795+272+22+16 = 2105, castle 19 segments = 3443).
    A converted track is one closed loop, so it gets a single segment.
    """
    # HZ5: shortcuts and plazas leave the vroad envelope; widen the slice
    # walls there so PU's off-track check does not fight the drivable road.
    if walls is None:
        walls = [(track.vroad(i)['left_wall'], track.vroad(i)['right_wall'])
                 for i in range(track.n_vroad)]
    walls, _widened = track_corridor.widen_for_shortcuts(track, walls)
    records = bytearray()
    for i in range(track.n_vroad):
        vr = track.vroad(i)
        rec = bytearray(80)
        struct.pack_into('<I', rec, 0x00, 0)
        struct.pack_into('<3f', rec, 0x04, *vr['ref'])
        struct.pack_into('<3f', rec, 0x10, *vr['normal'])
        struct.pack_into('<3f', rec, 0x1C, *vr['forward'])
        struct.pack_into('<3f', rec, 0x28, *vr['right'])
        struct.pack_into('<III', rec, 0x34, 0x00018000, 1, 1)
        left, right = walls[i]
        # A lane never reaches past its wall (PU's own tracks: lane <= wall).
        struct.pack_into('<4f', rec, 0x40, left, right,
                         min(vr['lane'][0], left), min(vr['lane'][1], right))
        records += rec
    segments = struct.pack('<I', track.n_vroad)
    return [MiscBlob('SimD', 0, 0xFA, track.n_vroad, bytes(records)),
            MiscBlob('SimT', 0, 0xFA, 1, segments)]


def _fill_prim_bounds(articles):
    """The originals store the article's own centre and radius in every `pr`."""
    for art in articles:
        centre, radius = art.centre_radius()
        for lod in art.lods.values():
            for prim in lod.prims:
                prim.centre, prim.radius = centre, radius


# Stock light-material records copied from industrial.crp for ``ef`` (lamp
# effect) articles; their render modes (e.g. FullAmbient) come with them.
EFFECT_MATERIAL_TAGS = ('lyte', 'pole', 'cone', 'plon', 'iolp', 'lint', 'lwin', 'indl')


def apply_light_tint(articles, tint_at):
    """Multiply each RD/CNK article's baked `df` colours by the atmosphere's
    light tint at the article's slice (ATM2 §6). `tint_at(slice) -> (r, g, b)`."""
    from track_atmosphere import tint_colour
    changed = 0
    for art in articles:
        m = re.match(r'^(RD|CNK)(\d+)', getattr(art, 'name', '') or '')
        if not m or not hasattr(art, 'lods'):
            continue
        tint = tint_at(int(m.group(2)))
        if all(abs(c - 1.0) < 1e-6 for c in tint):
            continue
        for lod in art.lods.values():
            lod.df = [tint_colour(c, tint) for c in lod.df]
        changed += 1
    return changed


def convert(track_dir, out_path, template_crp, branch=1, name=None,
            extra_objects=(), light_tint=None):
    track = frd.read(os.path.join(track_dir, 'tr.frd'))
    if name is None:
        name = os.path.basename(os.path.normpath(track_dir))
    terrain = terrain_surface(name)
    qfs = os.path.join(track_dir, 'tr0.qfs')
    holes = cutout_texture_ids(qfs) if os.path.exists(qfs) else set()
    soft_ids = soft_alpha_texture_ids(qfs) if os.path.exists(qfs) else set()
    # Animated objects NFS4 gives a sound carry a tag (HZ3, nfs5_ambient).
    sound_tags = {aid: spec['tag'] for aid, spec in
                  ambient.object_sound_specs(
                      os.path.basename(os.path.normpath(track_dir))).items()}
    articles, materials = build_articles(track, branch, terrain, name, holes,
                                         extra_objects=extra_objects,
                                         soft_ids=soft_ids,
                                         sound_tags=sound_tags)
    _fill_prim_bounds(articles)
    tinted = apply_light_tint(articles, light_tint) if light_tint else 0
    destructible_count = sum(bool(getattr(a, 'is_destructible', False))
                             for a in articles)

    source = crp_reader.read(template_crp)
    # Opaque, not the first alps `mt` (that one is an untextured dummy).
    # Foliage clones alps `bsha` (FakeSort, mt+4 = 0x0281), not alt1.
    opaque = material_template(source)
    fake = material_template(source, fsh_tag='bsha', prefer_desc='FakeSort')
    slots = sorted(materials.items(), key=lambda kv: kv[1])
    mats = []
    for slot, _slot in slots:
        tex_id, surface, cutout, is_soft = slot
        # FakeSort is already instantiated and proven by every converted
        # track. It gives soft masks queue-1 alpha blending without the
        # NormMatState runtime crash observed on Hometown.
        tmpl = fake if cutout or is_soft else opaque
        mats.append(Material(index_tag(tex_id), tmpl, surface=surface))

    # ``ef`` articles need the stock light-material records in addition to the
    # bitmap in the FSH.  The ordinary converted-material set only contains
    # road/terrain templates, so copy the complete effect records from
    # Industrial, the broadest shipped light-material library.
    effect_source_path = os.path.join(os.path.dirname(template_crp),
                                      'industrial.crp')
    if os.path.exists(effect_source_path):
        effect_source = crp_reader.read(effect_source_path)
        mats.extend(raw_materials_from(effect_source, EFFECT_MATERIAL_TAGS))
    # Render-mode definitions have to travel with the materials that name them,
    # or the engine crashes resolving "Opaque" on load - see MiscBlob.
    # 'Cm' + index 16750 is MiscData CmAn (the 2-char tag encoding of 'An').
    # FUN_00471850 returns 0 without those clips and the start intro never
    # leaves BumperCam. Copy the five clips from the template and move only
    # the two header world points onto this track's start slice.
    cman_index = 16750
    modes = [m for m in misc_blobs_from(source)
             if m.tag != 'Cm' or m.info == cman_index]
    vr0 = track.vroad(0)
    ahead = tuple(vr0['ref'][k] + vr0['forward'][k] * 30.0 for k in range(3))
    for m in modes:
        if m.tag == 'Cm':
            m.blob = _retarget_cman(m.blob, vr0['ref'], ahead)
    # A placed Base+0x44 SIGN is only an ordinary article unless the archive
    # also carries PU's 0x86xx object-definition library and Desc records.
    # Copy the full stock set exactly, as the runtime resolves SIGN through
    # this library and its definitions cross-reference one another.
    definitions = (raw_articles_with_base_flag(source, 0x8000)
                   if destructible_count else [])
    descriptions = (misc_blobs_from(source, tags=('De',))
                    if destructible_count else [])
    articles.extend(definitions)
    walls = None
    spdfa = os.path.join(track_dir, 'spdfa.bin')
    if AI_CORRIDOR_FROM_WALLS and os.path.exists(spdfa):
        _speed, _aux, line = track_files.decode_spd(open(spdfa, 'rb').read(), track.n_vroad)
        walls = track_corridor.real_walls(track, line)
    sim = build_sim_blobs(track, walls)
    # Stock tracks name the sidecar via MiscData `sn`. Embedding the SHPI as
    # `sf` (the car-atlas branch) crashed mid-load at 004753BF: FUN_0044a770
    # registers those images under ident `sf` (0x73660000) while every `mt`
    # looks up namespace `sn` (0x736e0000), then FUN_004750b0 reads a null
    # material slot. Sidecar `GameData/Track/<name>.fsh` is written by
    # convert_track.py / nfs5_fsh.py.
    source_name = source_fsh_blob('%s.fsh' % name.lower())

    blob = build_crp(articles, mats,
                     extra_misc=modes + descriptions + sim + [source_name])
    with open(out_path, 'wb') as f:
        f.write(blob)
    return dict(articles=len(articles), materials=len(mats), tinted_articles=tinted,
                light_sources=sum(len(track.lights(block))
                                  for block in range(track.n_blocks)),
                light_effects=sum(len(getattr(article, 'effects', ()))
                                  for article in articles),
                terrain_surface=terrain,
                drivable_materials=sum(1 for (_t, s, _c, _soft), _i in slots
                                       if s & 0x0F),
                cutout_materials=sum(1 for (_t, _s, c, _soft), _i in slots
                                     if c),
                destructible_objects=destructible_count,
                object_definitions=len(definitions),
                render_modes=len(modes), sim_slices=track.n_vroad,
                blocks=track.n_blocks,
                slices=track.n_vroad, bytes=len(blob),
                verts=sum(len(l.verts) for a in articles for l in a.lods.values()),
                prims=sum(len(l.prims) for a in articles for l in a.lods.values()))


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    import nfs5cfg
    template = os.path.join(nfs5cfg.GAME, 'GameData', 'Track', 'alps.crp')
    if '--template' in sys.argv:
        template = sys.argv[sys.argv.index('--template') + 1]
    output_name = None
    if '--name' in sys.argv:
        output_name = sys.argv[sys.argv.index('--name') + 1]
    stats = convert(args[0], args[1], template, name=output_name)
    print('%s -> %s' % (args[0], args[1]))
    for k in ('blocks', 'slices', 'articles', 'materials', 'cutout_materials',
              'destructible_objects', 'object_definitions',
              'verts', 'prims', 'bytes'):
        print('   %-10s %d' % (k, stats[k]))
