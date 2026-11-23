"""The direct-colour FSH codes community tracks use (0x78 RGB565, 0x7F RGB888, 0x6D ARGB4444), decoded on
synthetic entries: no game file needed.

    python tools/track/test_fsh_codes.py
"""
import struct
import sys

import nfs5_fsh as fsh


def entry(code, w, h, pixels):
    return bytes(16) + pixels, {'tag': 'test', 'code': code, 'w': w, 'h': h, 'off': 0, 'blk': 0}


def check(name, got, want):
    ok = got == want
    print('%-4s %-44s %s' % ('ok' if ok else 'FAIL', name, '' if ok else 'got %r want %r' % (got, want)))
    return ok


def main():
    ok = True
    # RGB565: pure red, pure green, pure blue, white (2x2)
    data, e = entry(fsh.CODE_RGB565, 2, 2, struct.pack('<4H', 0xF800, 0x07E0, 0x001F, 0xFFFF))
    w, h, bgra = fsh.decode_entry(data, e)
    ok &= check('565 size', (w, h), (2, 2))
    ok &= check('565 red/green/blue/white as BGRA', bytes(bgra),
                bytes((0, 0, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255, 255, 255, 255, 255)))
    # RGB888 stored B,G,R
    data, e = entry(fsh.CODE_RGB888, 2, 1, bytes((10, 20, 30, 40, 50, 60)))
    _w, _h, bgra = fsh.decode_entry(data, e)
    ok &= check('888 keeps B,G,R and is opaque', bytes(bgra), bytes((10, 20, 30, 255, 40, 50, 60, 255)))
    # ARGB4444: opaque orange; a fully transparent texel is dilated from its neighbour and stays alpha 0
    data, e = entry(fsh.CODE_ARGB4444, 2, 1, struct.pack('<2H', 0xFF80, 0x0000))
    _w, _h, bgra = fsh.decode_entry(data, e)
    ok &= check('4444 opaque texel', bytes(bgra[:4]), bytes((0, 0x88, 0xFF, 0xFF)))
    ok &= check('4444 transparent texel keeps alpha 0', bgra[7], 0)
    print('ALL PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
