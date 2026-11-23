"""Parser for `Sky/<Track>.txt` -- the sky levels (cloud layers) of a track.

A file is a list of `BEGIN ... END` blocks, one per detail level. `#` lines are
comments; the rest is positional. Two layouts exist (`FUN_0045fb80`):

  multi*  (42 values)  level, type, blocks-sun, then per weather region A and B:
          3 sky maps, 3 X scales, 3 Y scales, move ratio, 3 wind speeds,
          3 wind directions, warp speed, warp amount; then 3 cloud textures.
  basic   (23 values)  level, type, blocks-sun, then per region: sky map,
          alpha map, cloud texture, X scale, Y scale, move ratio, wind speed,
          wind direction, warp speed, warp amount.

A layer = one 16x16 ARGB sky map (the dome's VERTEX COLOURS, alpha included)
modulating one tiled cloud texture (`flat` = plain white). The number of
cloud textures sets the number of layers.
"""

import os

LEVEL_ORDER = ('AMAZING', 'HIGH', 'MEDIUM', 'LOW')
NONE = ('none', '')


class Region:
    __slots__ = ('sky_map', 'x_scale', 'y_scale', 'wind_speed', 'wind_dir')

    def __init__(self, sky_map, x_scale, y_scale, wind_speed, wind_dir):
        self.sky_map = sky_map
        self.x_scale, self.y_scale = x_scale, y_scale
        self.wind_speed, self.wind_dir = wind_speed, wind_dir


class Layer:
    __slots__ = ('texture', 'a', 'b')

    def __init__(self, texture, a, b):
        self.texture, self.a, self.b = texture, a, b


class SkyLevel:
    __slots__ = ('level', 'kind', 'blocks_sun', 'layers', 'water')


def _num(s):
    try:
        return float(s)
    except ValueError:
        return 0.0


def _name(s):
    return None if s.lower() in NONE else s


def _blocks(text):
    cur, out = None, []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.upper() == 'BEGIN':
            cur = []
        elif line.upper() == 'END':
            if cur is not None:
                out.append(cur)
            cur = None
        elif cur is not None:
            cur.append(line)
    return out


def _parse_block(v):
    lvl = SkyLevel()
    lvl.level, lvl.kind, lvl.blocks_sun = v[0].upper(), v[1].lower(), v[2].upper() == 'YES'
    lvl.water = lvl.kind.endswith('water')
    lvl.layers = []
    body = v[3:]
    if lvl.kind.startswith('basic'):
        if len(body) < 20:
            return None
        regions = []
        for base in (0, 10):
            r = body[base:base + 10]
            regions.append((_name(r[0]), _name(r[2]), Region(_name(r[0]), _num(r[3]), _num(r[4]),
                                                             _num(r[6]), _num(r[7]))))
        (_, tex, ra), (_, _, rb) = regions
        if tex:
            lvl.layers.append(Layer(tex, ra, rb))
        return lvl
    if len(body) < 39:
        return None
    regs = []
    for base in (0, 18):
        r = body[base:base + 18]
        regs.append([Region(_name(r[i]), _num(r[3 + i]), _num(r[6 + i]), _num(r[10 + i]), _num(r[13 + i]))
                     for i in range(3)])
    for i, tex in enumerate(body[36:39]):
        if _name(tex):
            lvl.layers.append(Layer(tex, regs[0][i], regs[1][i]))
    return lvl


def parse(text):
    return [lvl for lvl in (_parse_block(b) for b in _blocks(text) if len(b) >= 3) if lvl]


def best_level(levels):
    """The most detailed non-water level -- what a modern PC gets."""
    usable = [l for l in levels if not l.water and l.layers]
    usable.sort(key=lambda l: LEVEL_ORDER.index(l.level) if l.level in LEVEL_ORDER else 9)
    return usable[0] if usable else None


def txt_path(fsh_path):
    p = os.path.splitext(fsh_path)[0] + '.txt'
    return p if os.path.exists(p) else None


def load(fsh_path):
    p = txt_path(fsh_path) if fsh_path else None
    if not p:
        return None
    with open(p, 'r', encoding='latin1') as f:
        return best_level(parse(f.read()))
