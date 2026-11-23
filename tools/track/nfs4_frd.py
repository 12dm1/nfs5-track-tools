"""Reader for NFS4 (High Stakes, PC) tr.frd track files.

Research artifact for HS1. Structure follows LibOpenNFS::NFS4 with one
correction found here: XObjHeader.nVertices lives at +32, not +28.

Validated: parsing consumes every one of the 29 FRD files shipped with High
Stakes (19 x tr.frd + 10 x trn.frd) exactly to EOF -- see `python nfs4_frd.py`
with no arguments, which runs that self-test against
`NeedforSpeedHighStakes/Data/Tracks`.

Sizes (all little-endian):
    header            28 bytes, then uint32 nBlocks (stored as N-1), uint32 numVRoad
    VRoadBlock        84   refPt/normal/forward/right float3, leftWall, rightWall,
                           2 floats (lane width), 5 uint32 undecoded
    TrkBlockHeader  1512   see FIELD OFFSETS below
    Polygon           13   uint16 vertex[4], texture, texflags, uint8 animFlags
    POLYVROADDATA     24   per road polygon
    RefExtraObject    20 / RefExtraObject2 20 / SoundSource 16 / LightSource 16
    XObjHeader        52   type, index, _, float3 pt (types 2/4 only), size, _,
                           nVertices(+32), _[2], nPolygons(+44), _
                           Type 3 (animated) stores leftover pointers at +12,
                           not a world position; rest pose is keyframe 0.
    AnimKeyframe      20   int3 pt (16.16), int16 quat[4] (1.0 = 16384)
"""

import math
import os
import struct
import sys

TRKHDR_SIZE = 1512
VROAD_SIZE = 84
POLY_SIZE = 13
XOBJHDR_SIZE = 52
OBJECT_ATTRIBUTE_SIZE = 20

# RefExtraObject.collision_type.  Names follow the NFS4 FRD schema used by
# speedtools; value 3 exists in Redrock/Lost Canyons but its behaviour is not
# yet identified.
COLLISION_NONE = 0
COLLISION_STATIC = 1
COLLISION_DESTRUCTIBLE = 2
COLLISION_UNKNOWN = 3

# Polygon chunk indices inside TrkBlockHeader.sz[11]
CHUNK_NAMES = [
    "LOW_RES_TRACK", "LOW_RES_MISC", "MED_RES_TRACK", "MED_RES_MISC",
    "HIGH_RES_TRACK", "HIGH_RES_MISC", "LANES",
    "HIGH_RES_MISC1", "HIGH_RES_MISC2", "HIGH_RES_MISC3", "HIGH_RES_MISC4",
]
LOD0_CHUNKS = (4, 5, 6, 7, 8, 9, 10)   # what a converter should take as LOD0

# TrkBlockHeader field offsets
OFF_SZ = 0            # uint32[11]
OFF_PTR = 44          # uint32[11]  original build's memory pointers
OFF_COUNTS = 88       # nVertices, nHiRes, nLoRes, nMedRes, nDup, nObjectVert
OFF_CENTRE = 120      # float3
OFF_BOUNDING = 132    # float3[4]
OFF_NBD = 180         # int16[300][2]
OFF_NOBJ = 1380       # 4 x (uint32 num, uint32 unknown)
OFF_NPOLYGONS = 1412
OFF_PTMINMAX = 1416   # float3 min, float3 max
OFF_NPOSITIONS = 1444
OFF_NXOBJ = 1448
OFF_NPOLYOBJ = 1456
OFF_NSOUND = 1464
OFF_NLIGHT = 1472
OFF_NEIGHBORS = 1480  # uint32[8]


class Frd:
    def __init__(self, data: bytes):
        self.data = data
        self.n_blocks = struct.unpack_from("<I", data, 28)[0] + 1
        self.n_vroad = struct.unpack_from("<I", data, 32)[0]
        if (self.n_vroad + 7) // 8 != self.n_blocks:
            raise ValueError("vroad/block invariant violated: %d / %d"
                             % (self.n_vroad, self.n_blocks))
        self.vroad_base = 36
        self.hdr_base = 36 + self.n_vroad * VROAD_SIZE
        self.headers = [self._header(i) for i in range(self.n_blocks)]
        self.block_offsets = []          # filled by walk()
        self.end = self.walk()

    # -- vroad -------------------------------------------------------------

    def vroad(self, i):
        o = self.vroad_base + i * VROAD_SIZE
        d = self.data
        return dict(
            ref=struct.unpack_from("<3f", d, o),
            normal=struct.unpack_from("<3f", d, o + 12),
            forward=struct.unpack_from("<3f", d, o + 24),
            right=struct.unpack_from("<3f", d, o + 36),
            left_wall=struct.unpack_from("<f", d, o + 48)[0],
            right_wall=struct.unpack_from("<f", d, o + 52)[0],
            lane=struct.unpack_from("<2f", d, o + 56),
        )

    def road_length(self):
        pts = [self.vroad(i)["ref"] for i in range(self.n_vroad)]
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    # -- block headers -----------------------------------------------------

    def _header(self, i):
        b = self.hdr_base + i * TRKHDR_SIZE
        d = self.data
        counts = struct.unpack_from("<6I", d, b + OFF_COUNTS)
        return dict(
            offset=b,
            sz=struct.unpack_from("<11I", d, b + OFF_SZ),
            n_vertices=counts[0],
            n_hi=counts[1], n_lo=counts[2], n_med=counts[3],
            n_dup=counts[4], n_object_vert=counts[5],
            centre=struct.unpack_from("<3f", d, b + OFF_CENTRE),
            bounding=struct.unpack_from("<12f", d, b + OFF_BOUNDING),
            nobj=struct.unpack_from("<8I", d, b + OFF_NOBJ)[0::2],
            n_polygons=struct.unpack_from("<I", d, b + OFF_NPOLYGONS)[0],
            pt_min=struct.unpack_from("<3f", d, b + OFF_PTMINMAX),
            pt_max=struct.unpack_from("<3f", d, b + OFF_PTMINMAX + 12),
            n_positions=struct.unpack_from("<I", d, b + OFF_NPOSITIONS)[0],
            n_xobj=struct.unpack_from("<I", d, b + OFF_NXOBJ)[0],
            n_polyobj=struct.unpack_from("<I", d, b + OFF_NPOLYOBJ)[0],
            n_sound=struct.unpack_from("<I", d, b + OFF_NSOUND)[0],
            n_light=struct.unpack_from("<I", d, b + OFF_NLIGHT)[0],
            neighbors=struct.unpack_from("<8I", d, b + OFF_NEIGHBORS),
        )

    # -- body walk ---------------------------------------------------------

    def _xobj_chunk(self, o, n, stats):
        """Skip over an XObjChunk, recording per-object info. Returns new offset."""
        heads = []
        for i in range(n):
            b = o + i * XOBJHDR_SIZE
            heads.append((
                struct.unpack_from("<I", self.data, b)[0],        # type
                struct.unpack_from("<I", self.data, b + 32)[0],   # nVertices
                struct.unpack_from("<I", self.data, b + 44)[0],   # nPolygons
            ))
        o += n * XOBJHDR_SIZE
        for typ, nv, npoly in heads:
            stats["objects"] += 1
            if typ == 3:                                  # animated
                n_keys = struct.unpack_from("<H", self.data, o + 4)[0]
                o += 8 + n_keys * 20
                stats["anim"] += 1
                stats["keyframes"] += n_keys
            elif typ == 6:                                # special / physics
                o += 72
                stats["special"] += 1
            else:
                stats["normal"] += 1
            o += nv * 12 + nv * 4 + npoly * POLY_SIZE
        return o

    def walk(self):
        """Walk the whole file, recording where each block body starts.
        Returns the final offset, which must equal len(data)."""
        o = self.hdr_base + self.n_blocks * TRKHDR_SIZE
        self.stats = dict(objects=0, anim=0, special=0, normal=0, keyframes=0,
                          vertices=0, polygons=0, lights=0, sounds=0)
        for h in self.headers:
            self.block_offsets.append(o)
            o += h["n_vertices"] * 16                     # float3 + uint32 shading
            o += h["n_polygons"] * 24                     # POLYVROADDATA
            o += h["n_xobj"] * 20 + h["n_polyobj"] * 20
            o += h["n_sound"] * 16 + h["n_light"] * 16
            o += sum(h["sz"]) * POLY_SIZE
            for n in h["nobj"]:
                o = self._xobj_chunk(o, n, self.stats)
            self.stats["vertices"] += h["n_vertices"]
            self.stats["polygons"] += sum(h["sz"])
            self.stats["lights"] += h["n_light"]
            self.stats["sounds"] += h["n_sound"]
        self.globals = []
        self.global_objects = []
        for _ in range(2):
            n = struct.unpack_from("<I", self.data, o)[0]; o += 4
            self.globals.append(n)
            # Keep the decoded objects as well as the aggregate count.  The
            # global chunks contain most of the moving/special scenery on NFS4
            # (notably Park's trains and several type-6 candidates).
            objs, o = self._chunk_objects(o, n)
            self.global_objects.append(objs)
            self.stats['objects'] += n
            self.stats['anim'] += sum(x['type'] == 3 for x in objs)
            self.stats['special'] += sum(x['type'] == 6 for x in objs)
            self.stats['normal'] += sum(x['type'] not in (3, 6) for x in objs)
            self.stats['keyframes'] += 0
        return o

    # -- per-block accessors ----------------------------------------------

    def vertices(self, block):
        h = self.headers[block]
        o = self.block_offsets[block]
        return [struct.unpack_from("<3f", self.data, o + i * 12)
                for i in range(h["n_vertices"])]

    def shading(self, block):
        """Baked per-vertex lighting, D3DCOLOR (ARGB as uint32)."""
        h = self.headers[block]
        o = self.block_offsets[block] + h["n_vertices"] * 12
        return list(struct.unpack_from("<%dI" % h["n_vertices"], self.data, o))

    def polyvroad(self, block):
        """POLYVROADDATA for this block. `vroad_entry` indexes HIGH_RES_TRACK.

        24 bytes: hs_minmax[4], edge flags[4], collision flags, pad,
        uint16 poly index, then int16 normal/forward vectors.
        The low nibble of flags[4] (byte +8) is the surface code. There are
        fewer records than high-res road polygons; missing indices stay visual.
        """
        h = self.headers[block]
        o = self.block_offsets[block] + h["n_vertices"] * 16
        out = []
        for i in range(h["n_polygons"]):
            b = o + i * 24
            flags = self.data[b + 4:b + 9]
            out.append(dict(
                flags=flags,
                surface=flags[4] & 0x0F,
                wall_edges=tuple(bool(v) for v in flags[:4]),
                wall_collision=bool(flags[4] & 0x80),
                finite_height=bool(flags[4] & 0x20),
                object_collision=bool(flags[4] & 0x40),
                vroad_entry=struct.unpack_from("<H", self.data, b + 10)[0],
            ))
        return out

    def polygons(self, block, chunk):
        """Polygons of one of the 11 LOD/type chunks. 13 bytes each."""
        h = self.headers[block]
        o = self.block_offsets[block]
        o += h["n_vertices"] * 16 + h["n_polygons"] * 24
        o += h["n_xobj"] * 20 + h["n_polyobj"] * 20
        o += h["n_sound"] * 16 + h["n_light"] * 16
        for i in range(chunk):
            o += h["sz"][i] * POLY_SIZE
        return [self._polygon(o + i * POLY_SIZE) for i in range(h["sz"][chunk])]

    def object_attributes(self, block):
        """The block's RefExtraObject table, indexed by XObjHeader.index.

        The final byte is the authored NFS4 collision class.  In particular,
        value 2 is the exact ``destructible`` marker; it is not inferred from
        a model, texture, name, or object type.
        """
        h = self.headers[block]
        o = self.block_offsets[block]
        o += h['n_vertices'] * 16 + h['n_polygons'] * 24
        out = []
        for i in range(h['n_xobj']):
            b = o + i * OBJECT_ATTRIBUTE_SIZE
            fixed = struct.unpack_from('<3i', self.data, b)
            out.append(dict(
                location=tuple(v / 65536.0 for v in fixed),
                unknown1=struct.unpack_from('<H', self.data, b + 12)[0],
                identifier=struct.unpack_from('<H', self.data, b + 14)[0],
                unknown2=self.data[b + 16:b + 19],
                collision_type=self.data[b + 19],
            ))
        return out

    def _polygon(self, p):
        v = struct.unpack_from("<4H", self.data, p)
        tex, flags = struct.unpack_from("<2H", self.data, p + 8)
        return dict(vertex=v, texture_id=tex & 0x07FF, texflags=flags,
                    anim_flags=self.data[p + 12],
                    cull=(flags & 0x8000) == 0,
                    mirror_x=bool(flags & 0x0010),
                    mirror_y=bool(flags & 0x0020),
                    invert=bool(flags & 0x0008),
                    rotate=bool(flags & 0x0004),
                    # Lane bit lives on the texture word, not texflags
                    # (speedtools frd.ksy). 0x2000 on flags is UV animation.
                    lane=bool(tex & 0x0800),
                    animate_uv=bool(flags & 0x2000))

    def _chunk_objects(self, o, n):
        """Read one XObjChunk starting at `o`. Returns (objects, new offset).
        Vertices are local; `pt` is the world translation baked by the converter.
        Type 3 headers do not hold a usable `pt` — rest pose comes from keyframe 0."""
        heads = []
        for i in range(n):
            b = o + i * XOBJHDR_SIZE
            heads.append((struct.unpack_from('<I', self.data, b)[0],
                          struct.unpack_from('<I', self.data, b + 4)[0],
                          struct.unpack_from('<3f', self.data, b + 12),
                          struct.unpack_from('<I', self.data, b + 32)[0],
                          struct.unpack_from('<I', self.data, b + 44)[0]))
        o += n * XOBJHDR_SIZE
        out = []
        for typ, attribute_index, pt, nv, npoly in heads:
            quat = (0.0, 0.0, 0.0, 1.0)
            physics = None
            anim = None
            if typ == 3:                                  # animated: keyframes first
                atype, aid, n_keys, delay = struct.unpack_from('<2B2H', self.data, o + 2)
                keys = []
                for k in range(n_keys):
                    ix, iy, iz, qx, qy, qz, qw = struct.unpack_from(
                        '<3i4h', self.data, o + 8 + k * 20)
                    keys.append(((ix / 65536.0, iy / 65536.0, iz / 65536.0),
                                 (qx / 16384.0, qy / 16384.0,
                                  qz / 16384.0, qw / 16384.0)))
                if keys:
                    pt, quat = keys[0]
                anim = dict(atype=atype, aid=aid, delay=delay, keys=keys)
                o += 8 + n_keys * 20
            elif typ == 6:                                # special: physics block
                phys = struct.unpack_from('<3f f 9f 3f 2f', self.data, o)
                physics = dict(location=phys[0:3], mass=phys[3],
                               transform=phys[4:13],
                               collision_dimensions=phys[13:16],
                               tail=phys[16:18])
                o += 72
            verts = [struct.unpack_from('<3f', self.data, o + i * 12) for i in range(nv)]
            local_verts = verts
            if quat[0] != 0.0 or quat[1] != 0.0 or quat[2] != 0.0:
                verts = [_rotate_quat(v, quat) for v in verts]
            o += nv * 12
            shading = list(struct.unpack_from('<%dI' % nv, self.data, o)) if nv else []
            o += nv * 4
            polys = [self._polygon(o + i * POLY_SIZE) for i in range(npoly)]
            o += npoly * POLY_SIZE
            record = dict(type=typ, attribute_index=attribute_index, pt=pt,
                          verts=verts, shading=shading, polygons=polys,
                          collision_type=COLLISION_NONE)
            if physics is not None:
                record['physics'] = physics
            if anim is not None:
                # `verts` above is the rest pose (keyframe 0 rotation);
                # an animated article needs the unrotated mesh.
                record['anim'] = anim
                record['local_verts'] = local_verts
            out.append(record)
        return out, o

    def objects(self, block):
        """Extra objects attached to a block: signs, poles, barriers, trees."""
        h = self.headers[block]
        attributes = self.object_attributes(block)
        o = self.block_offsets[block]
        o += h['n_vertices'] * 16 + h['n_polygons'] * 24
        o += h['n_xobj'] * 20 + h['n_polyobj'] * 20
        o += h['n_sound'] * 16 + h['n_light'] * 16
        o += sum(h['sz']) * POLY_SIZE
        out = []
        for n in h['nobj']:
            objs, o = self._chunk_objects(o, n)
            out.extend(objs)
        # RefExtraObject is the first object-attribute table and describes the
        # normal (type 4) chunk.  Type-2 billboard/polygon objects use the
        # separate RefExtraObject2 table; treating their header word as an
        # index here falsely makes whole forests destructible on Coastal.
        # Animated/special records likewise carry unrelated values.
        for obj in out:
            if obj['type'] != 4:
                continue
            index = obj['attribute_index']
            if index < len(attributes):
                obj['object_attribute'] = attributes[index]
                obj['collision_type'] = attributes[index]['collision_type']
        return out

    def global_objects_flat(self):
        """Extra objects from the two file-global XObj chunks."""
        return [obj for chunk in self.global_objects for obj in chunk]

    def lights(self, block):
        """LightSource list: int3 refpoint (fixed point) + uint32 type."""
        h = self.headers[block]
        o = self.block_offsets[block]
        o += h["n_vertices"] * 16 + h["n_polygons"] * 24
        o += h["n_xobj"] * 20 + h["n_polyobj"] * 20 + h["n_sound"] * 16
        return [struct.unpack_from("<4i", self.data, o + i * 16)
                for i in range(h["n_light"])]


def read(path: str) -> Frd:
    return Frd(open(path, "rb").read())


# Implicit UVs: NFS4 stores no texture coordinates -- every quad maps the whole
# texture, then texflags permute the corners. The permute matches speedtools
# FrdData._texture_flags_to_uv (the working Blender importer): mirror Y, then
# mirror X, then invert as a 180 turn, then rotate by flipping one component
# per corner. A 90-degree cycle plus U/V swap looks similar but is wrong on
# about 40% of Hometown's high-res road polys.
#
# After that, V is flipped. High Stakes is Direct3D (V=0 at the top of the
# bitmap). Porsche Unleashed draws through Glide (t=0 at the bottom). Without
# the flip every track texture is upside-down.
BASE_QUAD_UV = [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)]


def _rotate_quat(v, q):
    """Rotate vector `v` by quaternion `q` = (x, y, z, w). Identity is a no-op."""
    qx, qy, qz, qw = q
    if qx == 0.0 and qy == 0.0 and qz == 0.0:
        return v
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def quad_uvs(poly) -> list:
    """Reconstruct the four UVs of a polygon from its texflags."""
    uv = [[1.0, 1.0], [0.0, 1.0], [0.0, 0.0], [1.0, 0.0]]
    if poly["mirror_y"]:
        uv[1][1], uv[2][1] = uv[2][1], uv[1][1]
        uv[0][1], uv[3][1] = uv[3][1], uv[0][1]
    if poly["mirror_x"]:
        uv[0][0], uv[1][0] = uv[1][0], uv[0][0]
        uv[2][0], uv[3][0] = uv[3][0], uv[2][0]
    if poly["invert"]:
        uv = [[1.0 - u, 1.0 - v] for u, v in uv]
    if poly["rotate"]:
        uv[0][1] = 1.0 - uv[0][1]
        uv[1][0] = 1.0 - uv[1][0]
        uv[2][1] = 1.0 - uv[2][1]
        uv[3][0] = 1.0 - uv[3][0]
    # Direct3D (NFS4) -> Glide (PU): V=0 is the opposite edge of the bitmap.
    return [(u, 1.0 - v) for u, v in uv]


def _test_quad_uvs():
    """Flag combos match speedtools, then V is flipped for Glide."""
    flags = dict(mirror_x=False, mirror_y=False, invert=False, rotate=False)
    cases = [
        ({}, [(1.0, 0.0), (0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]),
        ({"invert": True}, [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]),
        ({"rotate": True}, [(1.0, 1.0), (1.0, 0.0), (0.0, 0.0), (0.0, 1.0)]),
        ({"invert": True, "rotate": True},
         [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]),
        ({"mirror_y": True}, [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)]),
    ]
    for extra, want in cases:
        poly = dict(flags, **extra)
        got = quad_uvs(poly)
        if got != want:
            raise AssertionError("quad_uvs(%s) = %s, expected %s"
                                 % (extra, got, want))


def _self_test(base):
    _test_quad_uvs()
    ok = bad = 0
    for track in sorted(os.listdir(base)):
        for name in ("tr.frd", "trn.frd"):
            p = os.path.join(base, track, name)
            if not os.path.exists(p):
                continue
            try:
                f = read(p)
                rest = len(f.data) - f.end
                s = f.stats
                flag = "OK " if rest == 0 else "MISMATCH"
                if rest == 0:
                    ok += 1
                else:
                    bad += 1
                print(f"{flag} {track:10s} {name:8s} blocks={f.n_blocks:4d} "
                      f"vroad={f.n_vroad:5d} len={f.road_length()/1000:5.2f}km "
                      f"verts={s['vertices']:6d} polys={s['polygons']:6d} "
                      f"obj={s['objects']:4d} anim={s['anim']:3d} "
                      f"special={s['special']:4d} light={s['lights']:4d} "
                      f"snd={s['sounds']:3d} rest={rest}")
            except Exception as exc:                       # noqa: BLE001
                bad += 1
                print(f"ERR {track:10s} {name:8s} {type(exc).__name__}: {exc}")
    print(f"\n{ok} file(s) parsed exactly to EOF, {bad} failure(s)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_self_test(sys.argv[1]))
    import nfs5cfg
    default = nfs5cfg.HS_TRACKS
    sys.exit(_self_test(os.path.normpath(default)))
