"""Texture pixels of an EA FSH page: RGBA decoding and the texture identity key.

texture_rgba() turns one FSH entry (ARGB8888, ARGB1555, RGB565, ARGB4444, RGB888) into R,G,B,A bytes.
texture_key() is a FNV-1a 64 hash of the mip-0 pixels in R,G,B,A order: the same value a renderer can compute when the
game uploads the texture (after its own conversion to RGBA, before any cutout processing), so a texture on disk can be
matched with the texture the game draws. NFS5 track FSH pages store ARGB8888 as B,G,R,A bytes: they are swapped first.
"""

import struct

from ea_refpack import fsh_dir, load_maybe_qfs

FNV_OFFSET = 0xcbf29ce484222325
FNV_PRIME  = 0x100000001b3
FNV_MASK   = (1 << 64) - 1


def fnv1a_64(data: bytes) -> int:
    h = FNV_OFFSET
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & FNV_MASK
    return h


def _bgra_to_rgba(bgra: bytes, n: int) -> bytearray:
    """Swap B↔R for n pixels (4 bytes each)."""
    out = bytearray(bgra)
    for i in range(0, n * 4, 4):
        out[i], out[i + 2] = out[i + 2], out[i]
    return out


def texture_key(fsh_data: bytes, entry: dict) -> tuple:
    """(tag, width, height, hash) for one FSH entry.

    Reads raw level-0 pixels — no finish_alpha / dilate, matching the wrapper's
    hash point (after ConvertToRGBA, before DilateRgbIntoAlpha).
    """
    tag = entry['tag']
    w, h = entry['w'], entry['h']
    code = entry['code']
    off = entry['off'] + 16          # skip the 16-byte SHPI entry header

    if code == 0x7D:                 # ARGB8888: B,G,R,A in memory
        raw = fsh_data[off:off + w * h * 4]
        rgba = _bgra_to_rgba(raw, w * h)
    elif code == 0x7E:               # ARGB1555
        rgba = _expand_1555(fsh_data, off, w, h)
    elif code == 0x78:               # RGB565
        rgba = _expand_565(fsh_data, off, w, h)
    elif code == 0x6D:               # ARGB4444
        rgba = _expand_4444(fsh_data, off, w, h)
    elif code == 0x7F:               # RGB888: B,G,R per pixel
        rgba = _expand_888(fsh_data, off, w, h)
    else:
        raise ValueError('unsupported pixel code %#04x for tag %s' % (code, tag))

    return tag, w, h, fnv1a_64(bytes(rgba))


def _expand_1555(data, off, w, h):
    """ARGB1555 → R,G,B,A, same rule as wrapper ConvertToRGBA case 3."""
    out = bytearray(w * h * 4)
    for i in range(w * h):
        c = data[off + i * 2] | (data[off + i * 2 + 1] << 8)
        a = 0xFF if (c & 0x8000) else 0x00
        r = ((c >> 10) & 0x1F) * 255 // 31
        g = ((c >>  5) & 0x1F) * 255 // 31
        b = (c & 0x1F) * 255 // 31
        o = i * 4
        out[o] = r; out[o+1] = g; out[o+2] = b; out[o+3] = a
    return out


def _expand_565(data, off, w, h):
    """RGB565 → R,G,B,A, same rule as wrapper ConvertToRGBA case 4."""
    out = bytearray(w * h * 4)
    for i in range(w * h):
        c = data[off + i * 2] | (data[off + i * 2 + 1] << 8)
        r = ((c >> 11) & 0x1F) * 255 // 31
        g = ((c >>  5) & 0x3F) * 255 // 63
        b = (c & 0x1F) * 255 // 31
        o = i * 4
        out[o] = r; out[o+1] = g; out[o+2] = b; out[o+3] = 0xFF
    return out


def _expand_4444(data, off, w, h):
    """ARGB4444 → R,G,B,A, same rule as wrapper ConvertToRGBA case 7."""
    out = bytearray(w * h * 4)
    for i in range(w * h):
        c = data[off + i * 2] | (data[off + i * 2 + 1] << 8)
        a = ((c >> 12) & 0xF) * 17
        r = ((c >>  8) & 0xF) * 17
        g = ((c >>  4) & 0xF) * 17
        b = (c & 0xF) * 17
        o = i * 4
        out[o] = r; out[o+1] = g; out[o+2] = b; out[o+3] = a
    return out


def _expand_888(data, off, w, h):
    """RGB888 (B,G,R per pixel) → R,G,B,A, same rule as ConvertToRGBA case 5."""
    out = bytearray(w * h * 4)
    for i in range(w * h):
        p = off + i * 3
        b, g, r = data[p], data[p+1], data[p+2]
        o = i * 4
        out[o] = r; out[o+1] = g; out[o+2] = b; out[o+3] = 0xFF
    return out


def all_texture_keys(fsh_path: str) -> list:
    """[(tag, w, h, hash), ...] for every entry in an NFS5 track FSH."""
    data = load_maybe_qfs(fsh_path)
    _head, entries = fsh_dir(data)
    return [texture_key(data, e) for e in entries]


# ---- texture pixels from FSH entry (RGBA bytes) ----

def texture_rgba(fsh_data: bytes, entry: dict) -> bytes:
    tag = entry['tag']
    w, h = entry['w'], entry['h']
    code = entry['code']
    off = entry['off'] + 16
    if code == 0x7D:
        return bytes(_bgra_to_rgba(fsh_data[off:off + w * h * 4], w * h))
    elif code == 0x7E:
        return bytes(_expand_1555(fsh_data, off, w, h))
    elif code == 0x78:
        return bytes(_expand_565(fsh_data, off, w, h))
    elif code == 0x6D:
        return bytes(_expand_4444(fsh_data, off, w, h))
    elif code == 0x7F:
        return bytes(_expand_888(fsh_data, off, w, h))
    raise ValueError('unsupported pixel code %#04x for tag %s' % (code, tag))


def detect_cutout(rgba: bytes, n: int) -> bool:
    saw_0 = saw_ff = False
    for i in range(n):
        a = rgba[i * 4 + 3]
        if a == 0:
            saw_0 = True
        if a == 255:
            saw_ff = True
        if saw_0 and saw_ff:
            return True
    return False
