"""HS3: writer for NFS5 (Porsche Unleashed) karT archives (.crp / .drp).

No writer for this format existed anywhere; this is the one piece the whole
track migration hangs on. Everything encoded here was derived from the shipped
tracks and cross-checked with `nfs5_crp.py`.

File layout (matches alps.crp):

    0x00  header, 16 bytes: 'karT', headerInfo, numMiscData, articleTableOffset
          headerInfo = (articleCount << 5) | flags, flags = 0x1A
          articleTableOffset is in units of 16 bytes
    0x10  article table, 16 bytes each: 'itrA', headerInfo, partTableLength,
          offsetToPartTable (units of 16 bytes, relative to the article entry)
          misc table, 16 bytes each -- SAME encoding as a part entry
          part tables, then the data blobs

Part / MiscData entry, 16 bytes:

    +0  4-char tag reversed ('Base'), or 2-char tag reversed in bytes 2..3 with
        an info word in bytes 0..1
    +4  typeByte: 0x03 + 8*LOD for vt/uv/df/pr and tr, 0xEA Base/Anim/Anqt,
        0xFA Name, 0xF3 ef, 0xFB mt
    +5  uint24 LITTLE-endian data length
    +8  int32 element count
    +12 int32 data offset, in BYTES, relative to the entry itself
        (relative addressing is what flag bit 1 in the article flags selects)

Info word: for vt/uv/df the low byte is the LOD; for pr bits 15..12 are the LOD
and bits 11..0 the primitive index; for mt it is the material slot.

Parts are ordered by (tag, info) -- 'Base','Name','df','pr','uv','vt' with the
info word ascending inside each tag, which is what the originals do.

Primitive (`pr`) data block:

    +0x00 byte fillMode (3 = triangle list), byte 0, byte 1..2, byte 0..2
    +0x04 int16 material index, +0x06 uint16 0x8000
    +0x08 float3 centre, +0x14 float radius
    +0x28 int32 infoRowCount, +0x2C int32 indexRowCount
    +0x30 InfoRow[]: uint32 f0, int32 dataOffset, uint16 length,
          uint16 levelIndex, byte lod, byte 0, uint16 tail
          levelIndex 0 = vt (f0 11, tail 0x0000), 2 = uv (f0 10, tail 0x0001),
          3 = df (f0 9, tail 0xFFFF)
          dataOffset and length are BYTE offsets into that LOD's vt/uv/df array
          IndexRow[]: uint16 1, tag 'Iv'/'Iu' reversed, uint32 offset
          then the index bytes: count for 'Iv', then count for 'Iu'

Indices are single BYTES relative to the InfoRow base, so a primitive can reach
at most 256 vertices -- that is why the originals chunk the world so finely.

Materials are written from a 76-byte template lifted out of a shipped track,
with the FSH 4CC and the shader class patched in. The remaining fields of an
`mt` block are not decoded, and inventing them is not worth the risk.
"""

import math
import struct

ART_FLAGS = 0x1A
TYPE_GEOM = 0x03            # + 8 * LOD
TYPE_BASE = 0xEA
TYPE_NAME = 0xFA
TYPE_EF = 0xF3
TYPE_MT = 0xFB

LEVEL_VT, LEVEL_UV, LEVEL_DF = 0, 2, 3
_LEVEL_F0 = {LEVEL_VT: 11, LEVEL_UV: 10, LEVEL_DF: 9}
_LEVEL_TAIL = {LEVEL_VT: 0x0000, LEVEL_UV: 0x0001, LEVEL_DF: 0xFFFF}
_LEVEL_STRIDE = {LEVEL_VT: 16, LEVEL_UV: 8, LEVEL_DF: 4}


def _align16(n):
    return (n + 15) & ~15


def _ident_bytes(tag, info):
    """First 4 bytes of a part/misc row. FUN_0059f700 binary-searches this as a uint32."""
    if len(tag) == 4:
        return tag.encode('latin1')[::-1]
    return struct.pack('<H', info) + tag.encode('latin1')[::-1]


def _ident_key(tag, info):
    return struct.unpack('<I', _ident_bytes(tag, info))[0]


def _entry(tag, info, type_byte, length, count, rel_offset):
    """One 16-byte part/misc entry."""
    return (_ident_bytes(tag, info) + bytes([type_byte])
            + length.to_bytes(3, 'little')
            + struct.pack('<ii', count, rel_offset))


# ------------------------------------------------------------------ model ---

class Prim:
    """One `pr`: a triangle list over a window of the LOD's vt/uv/df arrays."""

    def __init__(self, material, vidx, uidx, vert_base=0, vert_count=None,
                 uv_base=0, uv_count=None, df_base=0, fill_mode=3, queue=2):
        if len(vidx) != len(uidx):
            raise ValueError('vertex and UV index counts differ')
        if vidx and max(vidx) > 255 or uidx and max(uidx) > 255:
            raise ValueError('indices are single bytes; split the primitive')
        self.material = material
        self.vidx = list(vidx)
        self.uidx = list(uidx)
        self.vert_base = vert_base
        self.vert_count = vert_count if vert_count is not None else (max(vidx) + 1 if vidx else 0)
        self.uv_base = uv_base
        self.uv_count = uv_count if uv_count is not None else (max(uidx) + 1 if uidx else 0)
        self.df_base = df_base
        self.fill_mode = fill_mode
        # Byte 2 of the prim header selects a draw queue (bit 1 = 0x20000).
        # Zero makes FUN_00450de0 skip the prim, while FUN_004750b0 still
        # turns it into collision triangles.
        self.queue = queue
        self.centre = (0.0, 0.0, 0.0)
        self.radius = 0.0

    @property
    def corners(self):
        return len(self.vidx)

    def encode(self):
        rows = [
            (LEVEL_DF, self.df_base * 4, self.corners * 4),
            (LEVEL_UV, self.uv_base * 8, self.uv_count * 8),
            (LEVEL_VT, self.vert_base * 16, self.vert_count * 16),
        ]
        body = bytearray(0x30)
        body[0] = self.fill_mode
        body[2] = self.queue
        struct.pack_into('<hH', body, 4, self.material, 0x8000)
        struct.pack_into('<4f', body, 8, self.centre[0], self.centre[1],
                         self.centre[2], self.radius)
        struct.pack_into('<ii', body, 0x28, len(rows), 2)
        for level, off, length in rows:
            body += struct.pack('<IiHHBBH', _LEVEL_F0[level], off, length, level,
                                0, 0, _LEVEL_TAIL[level])
        body += struct.pack('<H2sI', 1, b'vI', 0)                 # 'Iv' reversed
        body += struct.pack('<H2sI', 1, b'uI', self.corners)      # 'Iu' reversed
        body += bytes(self.vidx) + bytes(self.uidx)
        return bytes(body)


class Lod:
    def __init__(self, verts=None, uvs=None, df=None, prims=None):
        self.verts = list(verts or [])       # (x, y, z)
        self.uvs = list(uvs or [])           # (u, v)
        self.df = list(df or [])             # D3DCOLOR per face corner
        self.prims = list(prims or [])


class Effect:
    """One NFS5 ``ef`` light/glow marker.

    Type-2 effects are the common light-point form observed in shipped
    tracks.  The 4CC is stored byte-reversed inside the payload, just like
    the other EA identifiers.  The final word is an instance id/hash; the
    game accepts a deterministic value here, which is preferable to copying
    an unrelated stock id.
    """

    def __init__(self, position, effect='lpol', effect_id=0, scale=1.0):
        if len(position) != 3:
            raise ValueError('effect position must be float3')
        if len(effect) != 4:
            raise ValueError('effect 4CC must be four characters')
        self.position = tuple(float(v) for v in position)
        self.effect = effect
        self.effect_id = int(effect_id) & 0xFFFFFFFF
        self.scale = float(scale)

    def encode(self):
        blob = bytearray(44)
        struct.pack_into('<II3f3fI', blob, 0,
                         2, 0,
                         self.position[0], self.position[1], self.position[2],
                         0.0, 0.0, self.scale, 0)
        blob[0x24:0x28] = self.effect.encode('latin1')[::-1]
        struct.pack_into('<I', blob, 0x28, self.effect_id)
        return bytes(blob)


class Article:
    """One named object: geometry per LOD plus the LOD switch distances.

    `transform` is the optional 16-float `tr` matrix, D3D row-vector order, that
    places a local-space object in the world. Static road and terrain chunks are
    authored in world space and carry none.
    """

    DEFAULT_DISTANCES = (300, 500)
    # What the originals give an article that has nowhere further to fall back
    # to; see the walk in encode_base.
    FAR_DISTANCE = 10000

    def __init__(self, name, lods=None, distances=None, transform=None,
                 raw_base=None, raw_parts=(), effects=(), base_flags=None):
        self.name = name
        self.lods = dict(lods or {})
        self.distances = list(distances) if distances else None
        self.transform = list(transform) if transform else None
        if self.transform is not None and len(self.transform) != 16:
            raise ValueError('tr must be 16 floats, got %d' % len(self.transform))
        self.effects = list(effects)
        # Most generated articles use the ordinary static PU Base flags.  A
        # few renderer classes are selected by this word, however.  In
        # particular the stock foliage cards use 0x4611 and are dispatched to
        # the HBillBoard path.  Keep this override explicit rather than
        # guessing from the mesh shape: poles, trees and bushes can all be
        # type-2 FRD polygon objects.
        self.base_flags = base_flags
        # Parts this model has no opinion about - `Anim` and `Anqt` keyframes
        # and, with raw_base, the flag word the originals carry, so a shipped
        # archive can be re-emitted through this writer and compared against
        # itself.
        self.raw_base = raw_base
        self.raw_parts = list(raw_parts)

    # -- derived bounds ---------------------------------------------------

    def bounds(self):
        pts = [v for lod in self.lods.values() for v in lod.verts]
        pts.extend(effect.position for effect in self.effects)
        if not pts:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs, ys, zs = zip(*pts)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def centre_radius(self):
        lo, hi = self.bounds()
        centre = tuple((a + b) / 2.0 for a, b in zip(lo, hi))
        radius = math.sqrt(sum(((b - a) / 2.0) ** 2 for a, b in zip(lo, hi)))
        return centre, radius

    def encode_base(self):
        lo, hi = self.bounds()
        centre, radius = self.centre_radius()
        n_lod = len(self.lods)
        blob = bytearray(112 + 24 * (n_lod - 1))
        # 0x10000: keep LOD 0 when DAT_006197e5 is set (monaco RD articles).
        # Articles with `ef` use the stock effect-enabled low flag word
        # 0x711.  In particular, converted road articles need 0x10711;
        # leaving them at the ordinary 0x10610 makes the effect parts parse
        # correctly but remain invisible in-game.
        if self.base_flags is not None:
            flags = self.base_flags
        elif self.effects:
            flags = (0x00010000 if n_lod > 1 else 0) | 0x00000711
        else:
            flags = 0x00010610 if n_lod > 1 else 0x00010611
        struct.pack_into('<I', blob, 0, flags)
        struct.pack_into('<I', blob, 0x0C, n_lod)
        struct.pack_into('<I', blob, 0x10, 1)
        struct.pack_into('<3f', blob, 0x14, *lo)
        struct.pack_into('<3f', blob, 0x20, *hi)
        struct.pack_into('<3f', blob, 0x2C, *centre)
        struct.pack_into('<f', blob, 0x38, 1.0)
        struct.pack_into('<2f', blob, 0x3C, radius, radius)
        # The last LOD's distance has to be effectively infinite. The draw path
        # walks the rows while the switch distance squared is below the squared
        # distance to the article, and returns - drawing nothing at all - once
        # it runs past the last one:
        #
        #   004503D8  p = (ushort *)(Base + 6E)          ; row 0, distance
        #   004503E0  while (*p * *p <= d2) { ++lod; p += 6;
        #   004503F2                          if (n_lod <= lod) return; }
        #
        # Taking DEFAULT_DISTANCES[0] for a single-LOD article, as this used to,
        # capped it at 150 m; every shipped track writes 10000 there instead
        # (alps 1451 articles, canyon 1309, monaco1 389).
        explicit = bool(self.distances)
        dist = list(self.distances) if explicit else list(self.DEFAULT_DISTANCES)
        while len(dist) < n_lod:
            dist.append(self.FAR_DISTANCE)
        dist = dist[:n_lod]
        if not explicit:
            dist[-1] = self.FAR_DISTANCE
        for i, lod in enumerate(sorted(self.lods)):
            if lod > 15:
                # The info word only has 4 bits for the LOD in a `pr`. Values
                # above 3 in the wild are the variant/damage-switch encoding
                # described in T019, which this writer does not model.
                raise ValueError('LOD %d is out of range; this article uses the '
                                 'variant encoding, not plain LODs' % lod)
            at = 0x64 + i * 12
            # Stock articles carrying ``ef`` use the 0x101 LOD marker.  The
            # renderer tests its high byte before entering the effect branch;
            # a plain 0x1 still draws geometry but silently skips all glows.
            struct.pack_into('<I', blob, at, 0x101 if self.effects else 1)
            struct.pack_into('<4H', blob, at + 4, lod << 12, (lod << 12) | 0x0FFF,
                             lod, dist[i])
        return bytes(blob)

    # -- part list --------------------------------------------------------

    def parts(self):
        """[(tag, info, typeByte, count, payload)] in the order the originals use."""
        out = [('Base', 0, TYPE_BASE, 0,
                self.encode_base() if self.raw_base is None else self.raw_base),
               ('Name', 0, TYPE_NAME, 0, self.name.encode('latin1') + b'\0')]
        for i, effect in enumerate(self.effects):
            out.append(('ef', i, TYPE_EF, 1, effect.encode()))
        out.extend(self.raw_parts)
        if self.transform is not None:
            out.append(('tr', 0, TYPE_GEOM, 1,
                        struct.pack('<16f', *self.transform)))
        for lod in sorted(self.lods):
            d = self.lods[lod]
            t = TYPE_GEOM + 8 * lod
            if d.df:
                out.append(('df', lod, t, len(d.df),
                            struct.pack('<%dI' % len(d.df), *d.df)))
            for i, p in enumerate(d.prims):
                out.append(('pr', (lod << 12) | i, t, p.corners, p.encode()))
            if d.uvs:
                out.append(('uv', lod, t, len(d.uvs),
                            b''.join(struct.pack('<2f', *uv) for uv in d.uvs)))
            if d.verts:
                out.append(('vt', lod, t, len(d.verts),
                            b''.join(struct.pack('<4f', v[0], v[1], v[2], 1.0)
                                     for v in d.verts)))
        out.sort(key=lambda p: (p[0], p[1]))
        return out


class RawArticle:
    """An article copied byte-for-byte from an existing karT archive.

    Object-definition libraries use several variant LOD encodings that the
    geometry writer intentionally does not reinterpret.  Keeping their part
    rows raw is both lossless and sufficient for appending the stock PU
    smackable definitions to a converted track.
    """

    def __init__(self, parts):
        self._parts = list(parts)
        self.lods = {}

    def parts(self):
        return list(self._parts)


def raw_articles_with_base_flag(crp, mask, value=None):
    """Copy every source article whose Base flags match ``mask``/``value``."""
    if value is None:
        value = mask
    out = []
    for i in range(crp.n_articles):
        base = next((p for p in crp.parts(i) if p['tag'] == 'Base'), None)
        if base is None or base['length'] < 4:
            continue
        flags = struct.unpack_from('<I', crp.data, base['data'])[0]
        if flags & mask != value:
            continue
        parts = []
        for p in crp.parts(i):
            payload = crp.data[p['data']:p['data'] + p['length']]
            parts.append((p['tag'], p['info'], p['type_byte'], p['count'], payload))
        out.append(RawArticle(parts))
    return out


class MiscBlob:
    """A MiscData entry copied verbatim from a shipped track.

    Needed for `RM` - the render-mode definitions. A material names the mode it
    draws with ("Normal", "Opaque", "UVAnimate", ...) and the engine resolves
    that name against the modes the archive declares. Emitting materials
    without the modes they name makes 0044BB70 miss, the create path at
    0044B520 miss too, and 0044ABFA dereference the null that the shared exit
    left in esi - which is exactly how a converted track died on load, while a
    stock track survives the very same misses because its RM entries are there.
    alps declares 14 of them; their internal layout is not decoded, so they are
    copied whole, the way materials are.
    """

    def __init__(self, tag, info, type_byte, count, data):
        self.tag = tag
        self.info = info
        self.type_byte = type_byte
        self.count = count
        self.blob = bytes(data)


def misc_blobs_from(crp, tags=('RM', 'Cm')):
    """Lift MiscData entries of the given tags out of a parsed track.

    `Cm` is the 2-char encoding of `CmAn` (index 16750 = 'An'): five 6340-byte
    start-intro clips. Without them FUN_00471850 returns 0 and the race opens
    in BumperCam. The converter copies the blobs and retargets the header.
    """
    out = []
    for m in crp.misc():
        if m['tag'] not in tags:
            continue
        out.append(MiscBlob(m['tag'], m['index'], m['type_byte'], m['count'],
                            crp.data[m['data']:m['data'] + m['length']]))
    return out


def source_fsh_blob(filename):
    """MiscData `sn`: the track FSH the engine loads before it binds `mt` tags.

    FUN_0044a770 looks up tag `sf  ` (embedded SHPI) and, if that is missing,
    tag `sn  ` (a filename). Stock tracks ship `alps.fsh\\0` / `monaco1.fsh\\0`
    as that blob. Without it the archive's textures are never registered and
    articles draw vertex colour only; the sky FSH is a separate loader.
    """
    if not filename.endswith('.fsh'):
        filename = filename + '.fsh'
    return MiscBlob('sn', 0, 0xFB, 0, filename.encode('latin1') + b'\0')


def source_fsh_embedded(shpi_bytes):
    """MiscData `sf`: raw SHPI bytes. Do not emit this on a track CRP.

    FUN_0044a770 prefers `sf` over `sn`. Cars use it for local atlases.
    Putting a track GIMX here crashed Hometown at 004753BF (null material
    slot in FUN_004750b0) because the images are registered under ident
    `sf` / 0x73660000 while `mt+0x24` is `sn` / 0x736e0000. Kept so a later
    pass can try it again with a matching namespace, not as the converter
    default.
    """
    if shpi_bytes[:4] != b'SHPI':
        raise ValueError('sf blob is not a SHPI archive')
    return MiscBlob('sf', 0, 0xFB, 0, shpi_bytes)


class Material:
    """A 76-byte `mt` block built from a template with the texture patched in.

    The word at +0 is what makes a polygon exist for the physics as well as for
    the renderer. The engine turns a primitive into WTreeElem_Tri collision
    triangles only when that word is nonzero, and the surface lookup
    (FUN_00473330, which the car physics and the tyre sounds both call) keeps
    only the triangles whose low nibble is nonzero - see SURFACE_* in
    nfs4_geometry. Across the five shipped tracks sampled, bytes +2 and +3 are
    zero in all 1141 material records, so the field is written as a uint16.

    Leaving it at zero is what stranded the first converted tracks: with no
    collision triangles anywhere, the respawn loop at 0040F666 refused every
    slice and the game spun in it forever.
    """

    SIZE = 76

    def __init__(self, fsh_tag, template, shader_class=None, surface=0):
        if len(template) < self.SIZE:
            raise ValueError('material template is %d bytes, need %d'
                             % (len(template), self.SIZE))
        blob = bytearray(template[:self.SIZE])
        blob[0x28:0x2C] = fsh_tag.encode('latin1')[:4].ljust(4)
        if shader_class is not None:
            struct.pack_into('<I', blob, 0x2C, shader_class)
        struct.pack_into('<H', blob, 0, surface)
        self.blob = bytes(blob)


class RawMaterial:
    """A complete stock ``mt`` blob copied without changing its render mode."""

    def __init__(self, blob):
        if len(blob) < 4:
            raise ValueError('material blob is too short')
        self.blob = bytes(blob)


def raw_materials_from(crp, fsh_tags):
    """Copy all stock ``mt`` records whose FSH tag is in ``fsh_tags``."""
    wanted = set(fsh_tags)
    out = []
    for m in crp.misc():
        if m['tag'] != 'mt' or m.get('fsh_tag') not in wanted:
            continue
        out.append(RawMaterial(crp.data[m['data']:m['data'] + m['length']]))
    return out


# ------------------------------------------------------------------ build ---

def build_crp(articles, materials, extra_misc=()):
    """Serialise articles, materials and any copied MiscData into a karT archive.

    MiscData rows are stored in ident-uint32 order. FUN_0059f700 looks them up
    with a binary search, and stock tracks keep `sn` after every `mt` so the
    key 0x736e0000 is the last one. Emitting `sn` with the copied RM/Sim rows
    put it in the middle of the table; the search then walked off the end and
    the sidecar FSH was never opened.
    """
    extra_misc = list(extra_misc)
    misc_items = [(m.tag, m.info, m.type_byte, m.count, m.blob)
                  for m in extra_misc]
    for k, m in enumerate(materials):
        misc_items.append(('mt', k, TYPE_MT, 0, m.blob))
    misc_items.sort(key=lambda it: _ident_key(it[0], it[1]))

    n_art = len(articles)
    n_misc = len(misc_items)
    art_table = 16
    misc_table = art_table + n_art * 16

    part_lists = [a.parts() for a in articles]
    part_tables, cursor = [], misc_table + n_misc * 16
    for parts in part_lists:
        part_tables.append(cursor)
        cursor += len(parts) * 16

    # Data blobs, 16-aligned, in part-table order; materials last.
    blob_at, blobs, cursor = [], [], _align16(cursor)
    for parts in part_lists:
        row = []
        for _tag, _info, _tb, _cnt, payload in parts:
            row.append(cursor)
            blobs.append((cursor, payload))
            cursor = _align16(cursor + len(payload))
        blob_at.append(row)
    misc_at = []
    for _tag, _info, _tb, _cnt, payload in misc_items:
        misc_at.append(cursor)
        blobs.append((cursor, payload))
        cursor = _align16(cursor + len(payload))

    out = bytearray(cursor)
    struct.pack_into('<4sIii', out, 0, b'karT', (n_art << 5) | ART_FLAGS,
                     n_misc, art_table // 16)
    for i, parts in enumerate(part_lists):
        at = art_table + i * 16
        out[at:at + 16] = (b'itrA' + struct.pack('<Iii', ART_FLAGS, len(parts),
                                                 (part_tables[i] - at) // 16))
    for i, (tag, info, type_byte, count, payload) in enumerate(misc_items):
        at = misc_table + i * 16
        out[at:at + 16] = _entry(tag, info, type_byte, len(payload), count,
                                 misc_at[i] - at)
    for i, parts in enumerate(part_lists):
        for j, (tag, info, type_byte, count, payload) in enumerate(parts):
            at = part_tables[i] + j * 16
            out[at:at + 16] = _entry(tag, info, type_byte, len(payload), count,
                                     blob_at[i][j] - at)
    for at, payload in blobs:
        out[at:at + len(payload)] = payload
    return bytes(out)


def _printable_fsh_tag(tag):
    """True if `mt+0x28` is a real 4CC, not a leftover pointer.

    Alps' first material is named Normal and its tag is `1\\x84\\xa0\\x10`,
    which is not in alps.fsh. Cloning that record (w04=1, shader class 0,
    the two floats at +0x44 left at 0) made every converted surface bind a
    dummy slot: the wrapper's missing-texture path is a 1x1 white texel, so
    the track drew white while Sky/*.fsh (a separate loader) still looked
    right. Stock roads are Opaque (w04=4, class 3, +0x44/+0x48 = 1).
    """
    if not tag or len(tag) != 4:
        return False
    return all(32 <= ord(c) < 127 for c in tag)


def material_template(crp, fsh_tag=None, prefer_desc='Opaque'):
    """Lift a 76-byte `mt` template out of an already-parsed track (nfs5_crp.Crp).

    With `fsh_tag`, prefer the material that uses that texture. Otherwise pick
    an Opaque road material whose 4CC actually exists; never the first record
    blindly — on alps that one is an untextured dummy.
    """
    chosen = None
    preferred = None
    for m in crp.misc():
        if m['tag'] != 'mt':
            continue
        tag = m.get('fsh_tag') or ''
        usable = _printable_fsh_tag(tag)
        if chosen is None and usable:
            chosen = m
        if fsh_tag and tag == fsh_tag:
            chosen = m
            break
        if prefer_desc and m.get('desc') == prefer_desc and usable:
            if preferred is None:
                preferred = m
    if fsh_tag is None and preferred is not None:
        chosen = preferred
    if chosen is None:
        raise ValueError('no mt entry in the source archive')
    return crp.data[chosen['data']:chosen['data'] + Material.SIZE]
