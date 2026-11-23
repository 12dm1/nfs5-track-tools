"""Offline check: would the wrapper's ClassifyBinaryCutout() flag the sky cloud
textures of each track, and how opaque is their outer texel ring (what
ClearCutoutBorder would erase)?  Mirrors dx11_texture.cpp exactly:
  a < 8 -> clear, a >= 128 -> solid, else mid;
  cutout = clear && solid && !(mid*20 > n) && !(clear*12 < n)
"""
import glob
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ea_refpack import fsh_dir, load_maybe_qfs  # noqa: E402

import nfs5cfg  # noqa: E402
SKY = os.path.join(nfs5cfg.GAME, "GameData", "Track", "Sky")


def decode_alpha(data, e):
    """Return list of 8-bit alpha per texel, as the wrapper would see it."""
    w, h, o, code = e['w'], e['h'], e['off'] + 16, e['code'] & 0x7F
    n = w * h
    if code == 0x7D:      # ARGB8888 B,G,R,A
        return [data[o + i * 4 + 3] for i in range(n)], 'ARGB8888'
    if code == 0x6D:      # ARGB4444 little-endian: a = top nibble of the word
        return [((data[o + i * 2 + 1] >> 4) & 0xF) * 17 for i in range(n)], 'ARGB4444'
    if code == 0x7E:      # ARGB1555
        return [255 if (data[o + i * 2 + 1] & 0x80) else 0 for i in range(n)], 'ARGB1555'
    if code == 0x78:      # RGB565
        return [255] * n, 'RGB565'
    if code == 0x7F:      # RGB888
        return [255] * n, 'RGB888'
    return None, hex(code)


def classify(alpha):
    clear = sum(1 for a in alpha if a < 8)
    solid = sum(1 for a in alpha if a >= 128)
    mid = len(alpha) - clear - solid
    n = len(alpha)
    cutout = bool(clear and solid) and not (mid * 20 > n) and not (clear * 12 < n)
    return cutout, clear, mid, solid


def border_opaque(alpha, w, h):
    """Opaque (a>=8) texels on each edge of the outer ring."""
    top = sum(1 for x in range(w) if alpha[x] >= 8)
    bot = sum(1 for x in range(w) if alpha[(h - 1) * w + x] >= 8)
    left = sum(1 for y in range(h) if alpha[y * w] >= 8)
    right = sum(1 for y in range(h) if alpha[y * w + w - 1] >= 8)
    return top, bot, left, right


def layer_textures(txt_path):
    """Texture tags named in the HIGH block of Sky/<Track>.txt."""
    if not os.path.exists(txt_path):
        return set()
    tags = set()
    with open(txt_path, 'r', errors='replace') as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith('#') and re.fullmatch(r'[A-Za-z0-9]{2,4}', s):
                tags.add(s.lower())
    return tags


def main():
    only = set(a.lower() for a in sys.argv[1:])
    for fsh in sorted(glob.glob(os.path.join(SKY, '*.fsh'))):
        name = os.path.splitext(os.path.basename(fsh))[0]
        if only and name.lower() not in only:
            continue
        data = load_maybe_qfs(fsh)
        try:
            hdr, entries = fsh_dir(data)
        except Exception as ex:  # noqa: BLE001
            print(name, 'unreadable:', ex)
            continue
        used = layer_textures(os.path.join(SKY, name + '.txt'))
        print('==', name, 'entries=%d' % len(entries), 'layers:', ' '.join(sorted(used)))
        for e in entries:
            tag = e['tag']
            tl = tag.lower()
            if not (tl in used or tl.startswith(('sc', 'cc', 'cm', 'st', 'hor', 'hz', 'flat'))):
                continue
            alpha, fmt = decode_alpha(data, e)
            if alpha is None:
                print('  %-4s %s %dx%d  (undecoded %s)' % (tag, fmt, e['w'], e['h'], fmt))
                continue
            cut, clear, mid, solid = classify(alpha)
            n = len(alpha)
            t, b, l, r = border_opaque(alpha, e['w'], e['h'])
            flag = 'CUTOUT->border cleared' if cut else '-'
            print('  %-4s %-8s %3dx%-3d clear=%5.1f%% mid=%5.1f%% solid=%5.1f%%  edge opaque T/B/L/R=%d/%d/%d/%d  %s'
                  % (tag, fmt, e['w'], e['h'], 100.0 * clear / n, 100.0 * mid / n, 100.0 * solid / n,
                     t, b, l, r, flag))


if __name__ == '__main__':
    main()
