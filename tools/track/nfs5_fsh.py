"""HS2: convert NFS4 track textures (tr0.qfs) into an NFS5 track FSH.

Source side (High Stakes), all verified against the shipped files:
  * SHPI archive inside RefPack; 4878 entries are code 0x7B (8-bit palettised)
    and 25 are code 0x7D (ARGB8888) across the 38 shipped archives
  * each palettised bitmap is immediately followed by an attached palette block:
    code 0x2D, 256x1, block size 0x210 = 16-byte header + 256 x uint16
  * palette entries are ARGB1555; an entry of 0x0000 marks the colour key.
    Across Hometown only 46 of 25161 used indices are keyed, one per cut-out
    texture, so this is a classic single transparent index, not real alpha.
  * polygons address textures by INDEX into the archive, not by tag.
    That index skips directory entries whose attached 0x6F comment is
    `<mirrored>` (forward track). The copies share a tag with the
    `<nonmirrored>` original; using the raw directory made every ID after
    the first mirror bind the wrong bitmap. Hometown: 186 dir -> 174 used,
    FRD ids 0..173. Lane slots 0-9 in QFS are 1x1 stubs; the bitmaps live
    in Data/GameArt/sfx.fsh as lin0..lin9.

Target side (Porsche Unleashed), verified against alps.fsh:
  * every entry is code 0x7D ARGB8888 stored B,G,R,A (channel 3 is a constant
    255 on opaque textures)
  * a full mip chain down to 1x1 follows the 16-byte entry header with no
    per-level headers (level 1 matches a box filter of level 0 to ~4/255)
  * byte +15 of the entry header is (levels - 1) << 4: 128 -> 0x70, 256 -> 0x80
  * entry data is padded to a 16-byte boundary; the SHPI size field is the
    real file size; the engine accepts the archive uncompressed

    python nfs5_fsh.py <tr0.qfs> <out.fsh>      convert one archive
    python nfs5_fsh.py --selftest               convert + verify all 19 tracks
"""

import os
import struct
import sys

from ea_refpack import fsh_dir, load_maybe_qfs

CODE_ARGB8888 = 0x7D
CODE_ARGB4444 = 0x6D
CODE_PAL8 = 0x7B
CODE_PALETTE = 0x2D
CODE_ARGB1555 = 0x7E
CODE_RGB565 = 0x78
CODE_RGB888 = 0x7F
CODE_TEXT = 0x6F

# Hometown's source texture 19 (QFS tag ``0021``) is the black-to-grey mask
# used by the sun shafts in HIGH_RES_MISC4.  It is deliberately not a keyed
# cutout: the original renderer treats the luminance as light intensity.
# The converted PU archive addresses it by source index, hence output tag
# ``0019``.
SOFT_ALPHA_TEXTURE_IDS = {
    # Black-to-grey PAL8 intensity mask; converted to warm RGB + alpha below.
    'hometown': frozenset((19,)),
    # Dolphin Cove / source directory Coastal: LRAY is already ARGB8888 with
    # a genuine graduated alpha channel and must bypass finish_alpha().
    'coastal': frozenset((160,)),
}

# Per-track intensity tuning for translucent effects.  Dolphin Cove's native
# LRAY was authored for the NFS4 renderer and is about four times too bright
# through PU's FakeSort/SrcAlpha path.
SOFT_ALPHA_DIVISORS = {
    ('coastal', 160): 4,
}


def finish_alpha(bgra, w, h):
    """Dilate keyed holes, then snap mixed cutouts to 0/255.

    Soft A2C coverage mixed a 0.1-alpha mip ring with the sky and drew a cyan
    rim, worst when two cutouts overlapped. Binary alpha plus a second dilate
    keeps the hole leaf-coloured without a partial-alpha collar.
    """
    out = dilate_alpha_rgb(bgra, w, h)
    n = w * h
    mixed = False
    for i in range(n):
        a = out[i * 4 + 3]
        if a not in (0, 255):
            mixed = True
            break
    if not mixed:
        return dilate_alpha_rgb(out, w, h)
    hard = bytearray(out)
    for i in range(n):
        hard[i * 4 + 3] = 0 if hard[i * 4 + 3] < 128 else 255
    return dilate_alpha_rgb(hard, w, h)


def dilate_alpha_rgb(bgra, w, h, passes=None):
    """Copy opaque RGB into A=0 texels until the hole is filled.

    NFS4 colour-keys 0x0000 to transparent black; leftover ARGB1555 RGB is
    often sky. A 4-pixel ring was not enough: bilinear / aniso still mixed
    that sky into the leaf edge. Flood the nearest opaque colour into every
    hole. Alpha stays 0.
    Fully opaque and fully transparent images are left alone.
    """
    n = w * h
    saw_clear = saw_solid = False
    for i in range(n):
        if bgra[i * 4 + 3]:
            saw_solid = True
        else:
            saw_clear = True
        if saw_clear and saw_solid:
            break
    else:
        return bgra
    out = bytearray(bgra)
    has = [bgra[i * 4 + 3] > 0 for i in range(n)]
    neigh = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    limit = passes if passes is not None else max(w, h)
    for _ in range(limit):
        nxt = bytearray(out)
        nxt_has = list(has)
        changed = False
        for y in range(h):
            for x in range(w):
                i = y * w + x
                if has[i]:
                    continue
                acc_b = acc_g = acc_r = count = 0
                for dy, dx in neigh:
                    yy = y + dy
                    xx = x + dx
                    if yy < 0 or yy >= h or xx < 0 or xx >= w:
                        continue
                    j = yy * w + xx
                    if not has[j]:
                        continue
                    p = j * 4
                    acc_b += out[p]
                    acc_g += out[p + 1]
                    acc_r += out[p + 2]
                    count += 1
                if count:
                    o = i * 4
                    nxt[o] = acc_b // count
                    nxt[o + 1] = acc_g // count
                    nxt[o + 2] = acc_r // count
                    nxt_has[i] = True
                    changed = True
        out = nxt
        has = nxt_has
        if not changed:
            break
    return out


def decode_entry(data, entry):
    """Any NFS4 track-archive entry -> (w, h, BGRA bytearray).

    Only two codes occur across the 38 shipped archives: 0x7B palettised
    (4878 entries) and 0x7D ARGB8888 (25 entries, e.g. Coastal 'LRAY').
    NFS4's 0x7D uses the same B,G,R,A order as NFS5, so it is a straight copy.
    Tracks made by the community (FSHTool, T3ED) also use 0x78 RGB565 (most of
    the entries of a modern custom track), 0x7F RGB888 and 0x6D ARGB4444.
    """
    if entry['code'] == CODE_PAL8:
        return decode_pal8(data, entry)
    if entry['code'] == CODE_ARGB8888:
        w, h = entry['w'], entry['h']
        o = entry['off'] + 16
        return w, h, finish_alpha(bytearray(data[o:o + w * h * 4]), w, h)
    if entry['code'] == CODE_ARGB1555:
        return decode_argb1555(data, entry)
    if entry['code'] in (CODE_RGB565, CODE_RGB888, CODE_ARGB4444):
        return decode_direct(data, entry)
    raise ValueError('entry %r: unsupported pixel format %#04x'
                     % (entry['tag'], entry['code']))


def decode_direct(data, entry):
    """RGB565, RGB888 (B,G,R bytes) and ARGB4444 -> (w, h, BGRA bytearray).
    565 and 888 carry no alpha: every texel is opaque."""
    w, h, code = entry['w'], entry['h'], entry['code']
    o = entry['off'] + 16
    out = bytearray(w * h * 4)
    if code == CODE_RGB888:
        for i in range(w * h):
            out[i * 4:i * 4 + 4] = data[o + i * 3:o + i * 3 + 3] + b'\xff'
        return w, h, finish_alpha(out, w, h)
    for i in range(w * h):
        c = data[o + i * 2] | (data[o + i * 2 + 1] << 8)
        if code == CODE_RGB565:
            r, g, b = (c >> 11) & 0x1F, (c >> 5) & 0x3F, c & 0x1F
            px = ((b << 3) | (b >> 2), (g << 2) | (g >> 4), (r << 3) | (r >> 2), 255)
        else:                                       # ARGB4444
            a, r, g, b = (c >> 12) & 0xF, (c >> 8) & 0xF, (c >> 4) & 0xF, c & 0xF
            px = (b * 17, g * 17, r * 17, a * 17)
        out[i * 4:i * 4 + 4] = bytes(px)
    return w, h, finish_alpha(out, w, h)


def decode_argb1555(data, entry):
    """16-bit 1-5-5-5. 32 of the 734 sky entries across the 19 tracks use it,
    the rest are ARGB8888."""
    w, h = entry['w'], entry['h']
    o = entry['off'] + 16
    out = bytearray(w * h * 4)
    for i in range(w * h):
        c = data[o + i * 2] | (data[o + i * 2 + 1] << 8)
        r = (c >> 10) & 0x1F
        g = (c >> 5) & 0x1F
        b = c & 0x1F
        out[i * 4:i * 4 + 4] = bytes(((b << 3) | (b >> 2), (g << 3) | (g >> 2),
                                      (r << 3) | (r >> 2),
                                      255 if (c & 0x8000) else 0))
    return w, h, finish_alpha(out, w, h)


def decode_pal8(data, entry):
    """NFS4 palettised entry -> (w, h, BGRA bytearray). Colour-keyed texels
    come back fully transparent black."""
    w, h = entry['w'], entry['h']
    pixels = data[entry['off'] + 16: entry['off'] + 16 + w * h]
    pal_at = entry['off'] + entry['blk']
    if data[pal_at] != CODE_PALETTE:
        raise ValueError('entry %r: expected a palette attachment, got %#04x'
                         % (entry['tag'], data[pal_at]))
    raw = struct.unpack_from('<256H', data, pal_at + 16)

    # Expand ARGB1555 -> BGRA8888 once per palette, then index it per texel.
    lut = []
    for c in raw:
        if c == 0:                                  # colour key
            lut.append(b'\0\0\0\0')
            continue
        r = (c >> 10) & 0x1F
        g = (c >> 5) & 0x1F
        b = c & 0x1F
        # 5 -> 8 bits by bit replication, so 31 maps to 255
        lut.append(bytes(((b << 3) | (b >> 2), (g << 3) | (g >> 2),
                          (r << 3) | (r >> 2), 255 if (c & 0x8000) else 0)))

    out = bytearray(w * h * 4)
    for i, idx in enumerate(pixels):
        out[i * 4:i * 4 + 4] = lut[idx]
    return w, h, finish_alpha(out, w, h)


# -------------------------------------------------------------------- mips ---

# PU track archives never go below 16x16 (alps/monaco). NFS4 Hometown has
# 1x1..8x8 placeholders; FUN_0044d7e0 only fills texture+0x18 for widths
# 32/64/128/256/512, and a 1x1 GIMX is 32 bytes so the 7-dword memcmp at
# +0x38 walks off the block. Scale by doubling so the filter stays nearest.
MIN_TRACK_SIZE = 16
# QFS slots 0-9 are 1x1 stubs. Real markings are sfx.fsh lin0..lin9.
LANE_PLACEHOLDER_SLOTS = 10
LANE_PLACEHOLDER_MAX_PIXELS = 4
LANE_PLACEHOLDER_SIZE = 32


def scale_nn(src, w, h, nw, nh):
    """Nearest-neighbour resize. src/dst are packed BGRA."""
    dst = bytearray(nw * nh * 4)
    for y in range(nh):
        sy = y * h // nh
        for x in range(nw):
            sx = x * w // nw
            o = (y * nw + x) * 4
            p = (sy * w + sx) * 4
            dst[o:o + 4] = src[p:p + 4]
    return dst


def make_lane_placeholder(size=LANE_PLACEHOLDER_SIZE):
    """Solid off-white BGRA bitmap used in place of QFS 1x1 lane stubs."""
    pix = bytes((210, 220, 230, 255))
    return size, size, bytearray(pix * (size * size))


def is_lane_placeholder(index, w, h):
    return index < LANE_PLACEHOLDER_SLOTS and w * h <= LANE_PLACEHOLDER_MAX_PIXELS


def _attached_blocks(data, off):
    """Offsets of blocks chained from this SHPI entry via the 24-bit extra field."""
    extra = int.from_bytes(data[off + 1:off + 4], 'little')
    seen = set()
    while extra and off not in seen:
        seen.add(off)
        nxt = off + extra
        if nxt + 8 > len(data):
            break
        off = nxt
        extra = int.from_bytes(data[off + 1:off + 4], 'little')
        yield off


def attached_text(data, entry_off):
    """0x6F comment on a bitmap, if any (`<mirrored>`, `<nonmirrored>`, ...)."""
    for off in _attached_blocks(data, entry_off):
        if data[off] != CODE_TEXT:
            continue
        n = struct.unpack_from('<H', data, off + 4)[0]
        raw = data[off + 8:off + 8 + max(n, 0)]
        return raw.split(b'\0', 1)[0].decode('latin1', 'replace')
    return ''


def text_kind(text):
    return text.strip().lower().strip('<>')


def usable_qfs_entries(data, entries, mirrored_track=False):
    """Directory entries NFS4 actually numbers for FRD texture_id.

    Each mirrored texture is stored twice (same tag, extra 0x6F comment).
    A forward track skips `<mirrored>`; a mirrored track skips `<nonmirrored>`.
    Uncommented entries are used in both cases. Hometown: 186 -> 174, and
    FRD ids run 0..173 with nothing left over.
    """
    out = []
    for entry in entries:
        kind = text_kind(attached_text(data, entry['off']))
        if mirrored_track:
            if kind == 'nonmirrored':
                continue
        elif kind == 'mirrored':
            continue
        out.append(entry)
    return out


def default_sfx_path(qfs_path):
    """.../Data/Tracks/<name>/tr0.qfs -> .../Data/GameArt/sfx.fsh"""
    data_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(qfs_path))))
    for parts in (('GameArt', 'sfx.fsh'), ('GAMEART', 'SFX.FSH')):
        cand = os.path.join(data_dir, *parts)
        if os.path.exists(cand):
            return cand
    return None


def load_sfx_lanes(sfx_path):
    """lin0..lin9 from the shared SFX archive, keyed by slot number."""
    data = load_maybe_qfs(sfx_path)
    _head, entries = fsh_dir(data)
    by_tag = {e['tag']: e for e in entries}
    out = {}
    for i in range(LANE_PLACEHOLDER_SLOTS):
        entry = by_tag.get('lin%d' % i)
        if entry is None:
            continue
        out[i] = decode_entry(data, entry)
    return out


def converted_textures(src_path, sfx_path=None):
    """QFS directory, compacted and with lane stubs replaced -> (tag, w, h, BGRA)."""
    data = load_maybe_qfs(src_path)
    _head, entries = fsh_dir(data)
    usable = usable_qfs_entries(data, entries)
    if sfx_path is None:
        sfx_path = default_sfx_path(src_path)
    lanes = load_sfx_lanes(sfx_path) if sfx_path else {}
    track_name = os.path.basename(os.path.dirname(src_path)).lower()
    soft_ids = SOFT_ALPHA_TEXTURE_IDS.get(track_name, frozenset())
    textures = []
    for i, entry in enumerate(usable):
        if i in soft_ids and entry['code'] == CODE_ARGB8888:
            # Preserve native soft alpha. decode_entry() intentionally calls
            # finish_alpha() for ordinary cutouts and would harden LRAY.
            w, h = entry['w'], entry['h']
            off = entry['off'] + 16
            bgra = bytearray(data[off:off + w * h * 4])
        else:
            w, h, bgra = decode_entry(data, entry)
        if is_lane_placeholder(i, w, h):
            if i in lanes:
                w, h, bgra = lanes[i]
            else:
                w, h, bgra = make_lane_placeholder()
        else:
            w, h, bgra = ensure_min_size(w, h, bgra)
            w, h, bgra = ensure_square_pot(w, h, bgra)
        if track_name == 'hometown' and i in soft_ids:
            bgra = soft_mask_to_alpha(bgra)
        divisor = SOFT_ALPHA_DIVISORS.get((track_name, i), 1)
        if divisor > 1:
            bgra = divide_alpha(bgra, divisor)
        textures.append((index_tag(i), w, h, bgra))
    return textures


def texture_from_fsh(src_path, tag):
    """Read one ARGB8888 level-0 texture from an existing NFS5 FSH."""
    data = load_maybe_qfs(src_path)
    _head, entries = fsh_dir(data)
    entry = next((e for e in entries if e['tag'] == tag), None)
    if entry is None:
        raise KeyError('%s is not present in %s' % (tag, src_path))
    w, h, bgra = decode_entry(data, entry)
    return tag, w, h, bytearray(bgra)


def soft_alpha_texture_ids(src_path):
    """Source texture ids that are translucent light masks, not cutouts."""
    track_name = os.path.basename(os.path.dirname(src_path)).lower()
    return SOFT_ALPHA_TEXTURE_IDS.get(track_name, frozenset())


def soft_mask_to_alpha(bgra):
    """Turn Hometown's black/luminance ray mask into a PU-style soft alpha.

    The source bitmap is opaque grayscale because the NFS4 effect uses the
    black value as zero light.  PU's normal translucent material needs the
    same information in alpha; a warm-white RGB keeps vertex shading and the
    stock ``bem2`` look while alpha carries the shaft intensity.
    """
    out = bytearray(bgra)
    for i in range(0, len(out), 4):
        lum = (int(out[i]) + int(out[i + 1]) + int(out[i + 2])) // 3
        out[i:i + 4] = bytes((255, 255, 221, lum))
    return out


def divide_alpha(bgra, divisor):
    """Reduce a soft effect's opacity while preserving its RGB colour."""
    if divisor < 1:
        raise ValueError('alpha divisor must be positive')
    out = bytearray(bgra)
    for i in range(3, len(out), 4):
        out[i] //= divisor
    return out


def ensure_min_size(w, h, bgra, min_size=MIN_TRACK_SIZE):
    """Double until both sides meet `min_size`, keeping power-of-two sizes."""
    nw, nh = w, h
    while nw < min_size:
        nw *= 2
    while nh < min_size:
        nh *= 2
    if nw == w and nh == h:
        return w, h, bgra
    return nw, nh, scale_nn(bgra, w, h, nw, nh)


def is_square_pot(w, h):
    return w == h and w > 0 and w & (w - 1) == 0


def ensure_square_pot(w, h, bgra):
    """Stretch to the next power-of-two square.

    PU's texture constructor FUN_00554960 returns NULL for a texture that is
    not square or not a power of two, and its caller FUN_00535bc0 dereferences
    the result: the track crashes while loading (Landstrasse 0015 33x32,
    Raceway 2 0097 48x48, Durham Road 0048 64x32). CRP UVs are normalised, so
    the stretched texture maps exactly as before.
    """
    if is_square_pot(w, h):
        return w, h, bgra
    size = 1
    while size < max(w, h):
        size *= 2
    if size % w == 0 and size % h == 0:
        return size, size, scale_nn(bgra, w, h, size, size)
    return size, size, scale_bilinear(bgra, w, h, size, size)


def scale_bilinear(src, w, h, nw, nh):
    """Bilinear resize of packed BGRA. RGB is weighted by alpha so keyed texels
    do not darken the edges; a keyed (0/255) alpha stays keyed."""
    keyed = all(a in (0, 255) for a in src[3::4])
    dst = bytearray(nw * nh * 4)
    for y in range(nh):
        fy = max(0.0, (y + 0.5) * h / nh - 0.5)
        y0 = min(int(fy), h - 1)
        y1 = min(y0 + 1, h - 1)
        ty = fy - y0
        for x in range(nw):
            fx = max(0.0, (x + 0.5) * w / nw - 0.5)
            x0 = min(int(fx), w - 1)
            x1 = min(x0 + 1, w - 1)
            tx = fx - x0
            acc = [0.0, 0.0, 0.0]
            alpha = wsum = 0.0
            for sx, sy, k in ((x0, y0, (1 - tx) * (1 - ty)), (x1, y0, tx * (1 - ty)),
                              (x0, y1, (1 - tx) * ty), (x1, y1, tx * ty)):
                p = (sy * w + sx) * 4
                a = src[p + 3]
                alpha += a * k
                kw = k * (a + 1)
                wsum += kw
                for c in range(3):
                    acc[c] += src[p + c] * kw
            o = (y * nw + x) * 4
            for c in range(3):
                dst[o + c] = min(255, int(acc[c] / wsum + 0.5))
            dst[o + 3] = (255 if alpha >= 127.5 else 0) if keyed else min(255, int(alpha + 0.5))
    return dst


def downsample(src, w, h):
    """One box-filtered mip level. RGB is averaged weighted by alpha so that
    transparent texels do not bleed black into cut-out edges."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    dst = bytearray(nw * nh * 4)
    for y in range(nh):
        y0 = (2 * y) * w
        y1 = (2 * y + 1) * w if h > 1 else y0
        for x in range(nw):
            x0 = 2 * x
            x1 = x0 + 1 if w > 1 else x0
            acc_b = acc_g = acc_r = acc_a = wsum = 0
            for base in (y0, y1):
                for xx in (x0, x1):
                    p = (base + xx) * 4
                    a = src[p + 3]
                    acc_b += src[p] * a
                    acc_g += src[p + 1] * a
                    acc_r += src[p + 2] * a
                    acc_a += a
                    wsum += a
            o = (y * nw + x) * 4
            if wsum:
                dst[o] = acc_b // wsum
                dst[o + 1] = acc_g // wsum
                dst[o + 2] = acc_r // wsum
            dst[o + 3] = acc_a // 4
    return nw, nh, dst


def build_mip_chain(bgra, w, h):
    levels = [(w, h, bgra)]
    while w > 1 or h > 1:
        w, h, bgra = downsample(bgra, w, h)
        levels.append((w, h, bgra))
    return levels


def mip_levels_for(w, h):
    n = 1
    while w > 1 or h > 1:
        w, h = max(1, w // 2), max(1, h // 2)
        n += 1
    return n


# ------------------------------------------------------------------- write ---

def _align16(n):
    return (n + 15) & ~15


def write_fsh(textures, dirid=b'GIMX', mips=True):
    """textures: list of (tag, w, h, BGRA bytes). Returns the archive bytes."""
    n = len(textures)
    data_start = _align16(16 + n * 8)
    blocks, offsets, off = [], [], data_start
    for tag, w, h, bgra in textures:
        chain = build_mip_chain(bgra, w, h) if mips else [(w, h, bgra)]
        body = b''.join(bytes(level[2]) for level in chain)
        blk = bytearray(_align16(16 + len(body)))
        blk[0] = CODE_ARGB8888
        # NFS5 leaves the 24-bit block-size field at zero and relies on the
        # directory offsets; match that rather than inventing a value.
        struct.pack_into('<HH', blk, 4, w, h)
        blk[15] = (len(chain) - 1) << 4
        blk[16:16 + len(body)] = body
        blocks.append(bytes(blk))
        offsets.append(off)
        off += len(blk)

    out = bytearray(off)
    struct.pack_into('<4sII4s', out, 0, b'SHPI', off, n, dirid)
    for i, (tag, _w, _h, _d) in enumerate(textures):
        struct.pack_into('<4sI', out, 16 + i * 8, tag.encode('latin1')[:4].ljust(4), offsets[i])
    for blk, o in zip(blocks, offsets):
        out[o:o + len(blk)] = blk
    return bytes(out)


def bgra_to_argb4444(bgra, w, h):
    """Straight BGRA8888 -> packed ARGB4444 (the frontend TrackArt format)."""
    out = bytearray(w * h * 2)
    for i in range(w * h):
        b, g, r, a = bgra[i * 4:i * 4 + 4]
        c = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
        out[i * 2] = c & 0xFF
        out[i * 2 + 1] = c >> 8
    return bytes(out)


def write_frontend_fsh(textures, dirid=b'GIMX'):
    """TrackArt SHPI: ARGB4444, no mips, blit origin in the 16-byte header.

    textures: (tag, w, h, BGRA, (x, y, u, v))
    Stock coastal/alps store the 640x480 blit origin in (x, y). The extra
    pair (u, v) is used on the name strip and the two flags; the map itself
    leaves it 0.
    """
    n = len(textures)
    data_start = _align16(16 + n * 8)
    blocks, offsets, off = [], [], data_start
    for tag, w, h, bgra, extra in textures:
        body = bgra_to_argb4444(bgra, w, h)
        blk = bytearray(_align16(16 + len(body)))
        blk[0] = CODE_ARGB4444
        struct.pack_into('<HH', blk, 4, w, h)
        struct.pack_into('<4H', blk, 8, extra[0] & 0xFFFF, extra[1] & 0xFFFF,
                         extra[2] & 0xFFFF, extra[3] & 0xFFFF)
        blk[16:16 + len(body)] = body
        blocks.append(bytes(blk))
        offsets.append(off)
        off += len(blk)

    out = bytearray(off)
    struct.pack_into('<4sII4s', out, 0, b'SHPI', off, n, dirid)
    for i, (tag, _w, _h, _d, _e) in enumerate(textures):
        struct.pack_into('<4sI', out, 16 + i * 8,
                         tag.encode('latin1')[:4].ljust(4), offsets[i])
    for blk, o in zip(blocks, offsets):
        out[o:o + len(blk)] = blk
    return bytes(out)


# --------------------------------------------------------------- pipeline ---

# The THRASH driver tells the game how much texture memory it has (about+0x70: 32 MB in nfs5-web and in the PC
# wrapper), and the game fits a track's textures, the cars, the sky and the HUD into it. A track over that fails in
# TEXTURE_converttype (005350E0: "UNABLE TO CONVERT TO TARGET TEXTURE TYPE") and the race crashes: a community track
# with 654 textures (~37 MB as ARGB8888 with mips) did, 2026-11-23. The heaviest stock track, castle, takes ~21 MB;
# the 19 High Stakes tracks 3 MB at most.
TRACK_TEXTURE_BUDGET = 20 << 20


def texture_bytes(w, h):
    """ARGB8888 with the full mip chain, as write_fsh stores it."""
    return w * h * 4 * 4 // 3


def fit_budget(textures, budget=TRACK_TEXTURE_BUDGET, min_size=MIN_TRACK_SIZE):
    """Halve the largest textures until the archive fits the budget. -> (textures, number of halvings)"""
    total = sum(texture_bytes(w, h) for _tag, w, h, _bgra in textures)
    out, halved = list(textures), 0
    while total > budget:
        i = max(range(len(out)), key=lambda k: out[k][1] * out[k][2])
        tag, w, h, bgra = out[i]
        if max(w, h) <= min_size:
            break
        nw, nh, small = downsample(bgra, w, h)
        total += texture_bytes(nw, nh) - texture_bytes(w, h)
        out[i] = (tag, nw, nh, small)
        halved += 1
    return out, halved


def build_fsh_bytes(src_path, mips=True, sfx_path=None, extra_textures=()):
    """tr0.qfs -> uncompressed NFS5 SHPI bytes plus the (index, tag, w, h) list."""
    textures = converted_textures(src_path, sfx_path=sfx_path)
    textures.extend((tag,) + tuple(ensure_square_pot(w, h, bgra))
                    for tag, w, h, bgra in extra_textures)
    before = sum(texture_bytes(w, h) for _tag, w, h, _bgra in textures)
    textures, halved = fit_budget(textures)
    if halved:
        print('textures: %.1f MB is over the %d MB a track can use; %d halved, now %.1f MB'
              % (before / 2 ** 20, TRACK_TEXTURE_BUDGET >> 20, halved,
                 sum(texture_bytes(w, h) for _tag, w, h, _bgra in textures) / 2 ** 20))
    manifest = [(i, tag, w, h) for i, (tag, w, h, _bgra) in enumerate(textures)]
    return write_fsh(textures, mips=mips), manifest


def convert_qfs(src_path, dst_path, mips=True, sfx_path=None, extra_textures=()):
    """tr0.qfs -> NFS5 .fsh. Tags are the compacted FRD index ('0000'..),
    because that is what NFS4 polygons reference (source tags are not unique
    and mirrored copies pad the directory).
    Returns the manifest: list of (index, tag, w, h)."""
    blob, manifest = build_fsh_bytes(src_path, mips=mips, sfx_path=sfx_path,
                                     extra_textures=extra_textures)
    with open(dst_path, 'wb') as f:
        f.write(blob)
    return manifest


def cutout_texture_ids(src_path, sfx_path=None, hole_frac=0.0):
    """Source indices that have any keyed hole after conversion.

    Lane placeholders 0-9 are painted lines on clear and stay Opaque.
    A 2% cutoff left thin foliage (corn, hedges) on Opaque with leftover
    alphaRef 10/255, which is the remaining sky rim.
    """
    ids = set()
    for i, (_tag, w, h, bgra) in enumerate(
            converted_textures(src_path, sfx_path=sfx_path)):
        if i < LANE_PLACEHOLDER_SLOTS:
            continue
        n = w * h
        if not n:
            continue
        clear = 0
        step = 4
        for p in range(3, n * 4, step):
            if bgra[p] == 0:
                clear += 1
        if clear / n > hole_frac:
            ids.add(i)
    return ids


def index_tag(i):
    """Stable 4CC for source texture index i: '0000'..'9999', then base36."""
    if i < 10000:
        return '%04d' % i
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    s = ''
    v = i
    while v:
        s = digits[v % 36] + s
        v //= 36
    return s.rjust(4, '0')[-4:]


# ------------------------------------------------------------------ verify ---

def verify(path, manifest):
    """Re-read a written archive and check it against the NFS5 contract."""
    data = open(path, 'rb').read()
    head, entries = fsh_dir(data)
    problems = []
    if head['size'] != len(data):
        problems.append('size field %d != file size %d' % (head['size'], len(data)))
    if len(entries) != len(manifest):
        problems.append('entry count %d != manifest %d' % (len(entries), len(manifest)))
    tags = [e['tag'] for e in entries]
    if len(set(tags)) != len(tags):
        problems.append('duplicate tags')
    ordered = sorted(entries, key=lambda e: e['off'])
    for i, e in enumerate(ordered):
        nxt = ordered[i + 1]['off'] if i + 1 < len(ordered) else len(data)
        levels = mip_levels_for(e['w'], e['h'])
        body = sum(max(1, e['w'] >> k) * max(1, e['h'] >> k) * 4 for k in range(levels))
        want = _align16(16 + body)
        if nxt - e['off'] != want:
            problems.append('%s: block %d, expected %d' % (e['tag'], nxt - e['off'], want))
        if e['code'] != CODE_ARGB8888:
            problems.append('%s: code %#04x' % (e['tag'], e['code']))
        if not is_square_pot(e['w'], e['h']):
            problems.append('%s: %dx%d is not a power-of-two square (crashes PU)'
                            % (e['tag'], e['w'], e['h']))
        if data[e['off'] + 15] != (levels - 1) << 4:
            problems.append('%s: mip byte %#04x, expected %#04x'
                            % (e['tag'], data[e['off'] + 15], (levels - 1) << 4))
    by_tag = {e['tag']: e for e in entries}
    for _idx, tag, w, h in manifest:
        e = by_tag.get(tag)
        if e is None:
            problems.append('%s: missing' % tag)
        elif (e['w'], e['h']) != (w, h):
            problems.append('%s: %dx%d, expected %dx%d' % (tag, e['w'], e['h'], w, h))
    return problems


def _roundtrip_pixels(src_path, dst_path):
    """Level 0 of the written archive must equal the converted source."""
    textures = converted_textures(src_path)
    dst = open(dst_path, 'rb').read()
    _h2, dst_entries = fsh_dir(dst)
    if len(dst_entries) != len(textures):
        return 'entry count %d != converted %d' % (len(dst_entries), len(textures))
    for i, (tag, w, h, bgra) in enumerate(textures):
        d = dst_entries[i]
        got = dst[d['off'] + 16: d['off'] + 16 + w * h * 4]
        if got != bytes(bgra):
            return 'texture %d (%s) level 0 differs' % (i, tag)
    return None


def _selftest():
    import nfs5cfg
    base = nfs5cfg.HS_TRACKS
    out_dir = os.path.join(nfs5cfg.WORK, 'build', 'hs2-textures')
    out_dir = os.path.normpath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    bad = 0
    for track in sorted(os.listdir(base)):
        src = os.path.join(base, track, 'tr0.qfs')
        if not os.path.exists(src):
            continue
        dst = os.path.join(out_dir, track.lower() + '.fsh')
        manifest = convert_qfs(src, dst)
        problems = verify(dst, manifest)
        rt = _roundtrip_pixels(src, dst)
        if rt:
            problems.append(rt)
        status = 'OK ' if not problems else 'FAIL'
        if problems:
            bad += 1
        print('%s %-10s %3d textures  %8d -> %8d bytes  %s'
              % (status, track, len(manifest), os.path.getsize(src),
                 os.path.getsize(dst), '; '.join(problems[:3])))
    print('\n%s' % ('all archives passed' if not bad else '%d failures' % bad))
    return 1 if bad else 0


def fix_sizes(path, backup_dir=None):
    """Rewrite an installed track FSH so every texture is a power-of-two
    square (see ensure_square_pot). Only the offending entries change: the
    archive is rebuilt with write_fsh, which must reproduce the unchanged
    file byte for byte first. Returns the list of fixed (tag, w, h, size)."""
    data = open(path, 'rb').read()
    _head, entries = fsh_dir(data)
    textures, fixed = [], []
    for e in entries:
        if e['code'] != CODE_ARGB8888:
            raise ValueError('%s: entry %s code %#04x, not a converted track archive'
                             % (path, e['tag'], e['code']))
        w, h = e['w'], e['h']
        o = e['off'] + 16
        textures.append((e['tag'], w, h, bytearray(data[o:o + w * h * 4])))
    dirid = data[12:16]
    if write_fsh(textures, dirid=dirid) != data:
        raise ValueError('%s does not round-trip through write_fsh; refusing to rewrite' % path)
    for i, (tag, w, h, bgra) in enumerate(textures):
        if not is_square_pot(w, h):
            nw, nh, nb = ensure_square_pot(w, h, bgra)
            textures[i] = (tag, nw, nh, nb)
            fixed.append((tag, w, h, nw))
    if not fixed:
        return fixed
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        backup = os.path.join(backup_dir, os.path.basename(path) + '.before-texsize')
        if not os.path.exists(backup):
            with open(backup, 'wb') as f:
                f.write(data)
    with open(path, 'wb') as f:
        f.write(write_fsh(textures, dirid=dirid))
    return fixed


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    if sys.argv[1:2] == ['--fix-sizes']:
        # python nfs5_fsh.py --fix-sizes GameData/Track/germany.fsh ...
        import nfs5cfg
        backups = os.path.join(nfs5cfg.WORK, 'backups')
        for p in sys.argv[2:]:
            done = fix_sizes(p, backups)
            print('%s: %s' % (p, ', '.join('%s %dx%d -> %d' % f for f in done) or 'ok'))
        sys.exit(0)
    src, dst = sys.argv[1], sys.argv[2]
    man = convert_qfs(src, dst)
    problems = verify(dst, man)
    print('%s -> %s: %d textures, %d bytes' % (src, dst, len(man), os.path.getsize(dst)))
    print('check: ' + ('ok' if not problems else '; '.join(problems)))
