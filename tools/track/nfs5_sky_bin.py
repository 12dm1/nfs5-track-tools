"""Reader for NFS5 (Porsche Unleashed) `GameData/Track/Sky/<Track>.bin`.

The game calls this blob "SkyCnfig" (`FUN_00459660` loads it whole, falling back
to `Sky\\default.bin`, and `FUN_00459700` upgrades older versions in place).
Every shipped file is version 9 and exactly 0x71DC bytes.

It holds everything about a track's atmosphere that varies along the road:

  * the sky dome shape (a paraboloid: y = height - curvature * (x^2 + z^2)),
  * the sun's position on that paraboloid and the lens-flare switch,
  * the horizon panorama layout (16 slots around the compass),
  * per-detail-level sky colours for weather region A and B,
  * three keyframe curves indexed by TRACK SLICE:
      - `curve_region` (0xDDC)  -- 0..1 blend between region A and B colours
      - `weather`      (0x1A5C) -- fog colour + density, rain and snow strength
      - `curve_b`      (0x655C) -- same shape as curve_region, unused in stock files

Keyframes are linearly interpolated between the two slices that bracket the
car's current slice (`FUN_0045a560`, with wraparound for circuits); the fog
density is cubed before it reaches `grFogTable` (`FUN_0045a6a0`).

Versions below 9 kept 50-entry copies of the three tables at 0x4C / 0x1DC /
0xB4C.  Shipped v9 files still contain stale data there; the game never reads
it.  See `docs/formats/sky-bin.md`.

    python nfs5_sky_bin.py <Sky/alps.bin> [--keys] [--curves] [--raw]
"""

import struct
import sys

SIZE = 0x71DC
VERSION = 9

OFF_HORIZON_SLOTS = 0x1C
OFF_LEGACY_CURVE_A = 0x4C
OFF_LEGACY_WEATHER = 0x1DC
OFF_WATER = 0xB3C
OFF_LEGACY_CURVE_B = 0xB4C
OFF_SUN = 0xCDC
OFF_DETAIL = 0xCE8
OFF_FLARE = 0xDD8
OFF_CURVE_REGION = 0xDDC
OFF_WEATHER = 0x1A5C
OFF_CURVE_B = 0x655C

MAX_KEYS = 400
WEATHER_SIZE = 0x30
CURVE_SIZE = 8
DETAIL_SIZE = 0x3C


class WeatherKey:
    """One row of the 0x1A5C table (48 bytes)."""

    __slots__ = ('slice', 'fog_density_a', 'fog_density_b', 'unknown_0c',
                 'fog_colour_a', 'fog_colour_b', 'rain', 'unknown_1c', 'snow',
                 'unknown_24', 'unknown_28', 'unknown_2c')

    def __init__(self, blob, off):
        (self.slice, self.fog_density_a, self.fog_density_b, self.unknown_0c,
         self.fog_colour_a, self.fog_colour_b, self.rain, self.unknown_1c,
         self.snow, self.unknown_24, self.unknown_28,
         self.unknown_2c) = struct.unpack_from('<i3f2I6f', blob, off)

    def rgb(self, which='a'):
        c = self.fog_colour_a if which == 'a' else self.fog_colour_b
        return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)

    def __repr__(self):
        return ('slice %-5d fog %.4f %s  rain %.2f snow %.2f'
                % (self.slice, self.fog_density_a, self.rgb(), self.rain, self.snow))


class DetailColours:
    """One of the four per-detail-level records at 0xCE8 (60 bytes).

    `FUN_00459950` picks the record for the current detail setting and walks
    DOWN until it finds one with `in_use` set.  Each record carries two blocks
    of 28 bytes -- block 0 for the normal sky pass, block 1 for the pass with
    render flag 0x10 (the water-reflected sky on the coastal tracks).  Within a
    block, `follow_fog != 0` makes the sky colour track the interpolated FOG
    colour instead (`FUN_0045d2b0` -> `FUN_0045a720(t, 1)`), which is what alps
    and autobahn do; otherwise A and B are blended by `curve_region`.
    """

    __slots__ = ('in_use', 'blocks')

    def __init__(self, blob, off):
        self.in_use = struct.unpack_from('<i', blob, off)[0]
        self.blocks = []
        for b in range(2):
            base = off + 4 + b * 0x1C
            follow_fog = struct.unpack_from('<i', blob, base)[0]
            v = struct.unpack_from('<6f', blob, base + 4)
            self.blocks.append(dict(follow_fog=follow_fog,
                                    colour_a=(v[0], v[1], v[2]),
                                    colour_b=(v[3], v[4], v[5])))


class SkyBin:
    def __init__(self, data):
        if len(data) != SIZE:
            raise ValueError('expected %d bytes, got %d' % (SIZE, len(data)))
        self.data = data
        u = lambda fmt, off: struct.unpack_from(fmt, data, off)
        self.version, = u('<I', 0)
        if self.version != VERSION:
            raise ValueError('version %d, only %d is understood' % (self.version, VERSION))

        self.unknown_04, = u('<f', 4)
        self.horizon_rotation, = u('<i', 8)
        self.unknown_0c, = u('<i', 0x0C)          # 4 in every shipped file
        self.horizon_rows, = u('<i', 0x10)        # 2, Monaco 1
        self.horizon_bottom, = u('<f', 0x14)
        self.horizon_top, = u('<f', 0x18)
        self.horizon_slots = list(u('<16H', OFF_HORIZON_SLOTS))

        # Sky dome: y = height - curvature * (x^2 + z^2), rotated by `angle`
        # and scaled by `sky_scale` (`FUN_00461090`, `FUN_00460ec0`).
        self.angle, = u('<f', 0x3C)
        self.dome_radius, = u('<f', 0x40)
        self.dome_curvature, = u('<f', 0x44)
        self.dome_height, = u('<f', 0x48)

        # The same four fields again for the water-reflected sky; only the
        # coastal family sets them, everyone else keeps the v3 defaults.
        (self.water_angle, self.water_dome_radius, self.water_dome_curvature,
         self.water_dome_height) = u('<4f', OFF_WATER)

        self.sun_x, self.sun_y, self.sky_scale = u('<3f', OFF_SUN)
        self.lens_flare, = u('<i', OFF_FLARE)

        self.detail = [DetailColours(data, OFF_DETAIL + i * DETAIL_SIZE)
                       for i in range(4)]

        self.curve_region = self._curve(OFF_CURVE_REGION)
        self.curve_b = self._curve(OFF_CURVE_B)
        self.weather = self._weather(OFF_WEATHER)

    # -- tables ------------------------------------------------------------

    def _curve(self, base):
        out = []
        for i in range(MAX_KEYS):
            sl, value = struct.unpack_from('<if', self.data, base + i * CURVE_SIZE)
            if sl < 0:
                break
            out.append((sl, value))
        return out

    def _weather(self, base):
        out = []
        for i in range(MAX_KEYS):
            if struct.unpack_from('<i', self.data, base + i * WEATHER_SIZE)[0] < 0:
                break
            out.append(WeatherKey(self.data, base + i * WEATHER_SIZE))
        return out

    # -- evaluation --------------------------------------------------------

    @staticmethod
    def _bracket(keys, slc, key_of):
        """Index pair bracketing `slc`, plus the 0..1 factor the game uses.

        `FUN_0045a560` returns 1 - (slc - lo) / (hi - lo), i.e. the weight of
        the LOWER key, and that is what the lerps below expect.
        """
        if not keys:
            return 0, 0, 1.0
        lo = 0
        for i in range(len(keys) - 1):
            if key_of(keys[i]) <= slc < key_of(keys[i + 1]):
                lo = i
                break
        else:
            lo = len(keys) - 1 if slc >= key_of(keys[-1]) else 0
        hi = min(lo + 1, len(keys) - 1)
        span = key_of(keys[hi]) - key_of(keys[lo])
        if span <= 0:
            return lo, hi, 1.0
        return lo, hi, 1.0 - (slc - key_of(keys[lo])) / span

    def fog_at(self, slc):
        """Channel A -- the fog of the SKY pass (dome layers and horizon), and
        the colour `follow_fog` takes. (r, g, b), density as handed to
        grFogTable (the cube is applied). The world uses `fog_b_at`."""
        lo, hi, f = self._bracket(self.weather, slc, lambda k: k.slice)
        a, b = self.weather[lo], self.weather[hi]
        density = (a.fog_density_a * f + b.fog_density_a * (1.0 - f)) ** 3
        ca, cb = a.rgb(), b.rgb()
        colour = tuple(int(ca[i] * f + cb[i] * (1.0 - f) + 0.5) for i in range(3))
        return colour, density

    def fog_b_at(self, slc):
        """Channel B: the fog the WORLD is drawn with. `FUN_0045d4f0` sets
        channel A (`fog_at`) before the sky levels and the horizon, draws them,
        then switches to channel B, which stays on for the track."""
        lo, hi, f = self._bracket(self.weather, slc, lambda k: k.slice)
        a, b = self.weather[lo], self.weather[hi]
        density = (a.fog_density_b * f + b.fog_density_b * (1.0 - f)) ** 3
        ca, cb = a.rgb('b'), b.rgb('b')
        colour = tuple(int(ca[i] * f + cb[i] * (1.0 - f) + 0.5) for i in range(3))
        return colour, density

    @property
    def horizon_angle(self):
        """+0x04: start angle of the horizon ring, in TURNS (`FUN_00459d30`)."""
        return self.unknown_04

    @property
    def horizon_columns(self):
        """+0x0C: panels per row of the `horz` texture (4 everywhere)."""
        return self.unknown_0c

    def precipitation_at(self, slc):
        """(rain, snow) strengths; they scale the particle counts directly."""
        lo, hi, f = self._bracket(self.weather, slc, lambda k: k.slice)
        a, b = self.weather[lo], self.weather[hi]
        return (a.rain * f + b.rain * (1.0 - f),
                a.snow * f + b.snow * (1.0 - f))

    def storm_at(self, slc):
        """Lightning strength 0..1 (weather field +0x1C, classes in ATM1)."""
        lo, hi, f = self._bracket(self.weather, slc, lambda k: k.slice)
        a, b = self.weather[lo], self.weather[hi]
        return a.unknown_1c * f + b.unknown_1c * (1.0 - f)

    def region_blend_at(self, slc):
        lo, hi, f = self._bracket(self.curve_region, slc, lambda k: k[0])
        return self.curve_region[lo][1] * f + self.curve_region[hi][1] * (1.0 - f)


def read(path):
    with open(path, 'rb') as f:
        return SkyBin(f.read())


# ----------------------------------------------------------------- write ---

def write(sky, template):
    """Patch `template` (an existing .bin's bytes, e.g. a stock file used as
    a stand-in) with every field `sky` carries, byte-identically to the
    reader above.  `sky` needs only the attributes `SkyBin` exposes --
    `SkyBin` instances work directly, as does any duck-typed stand-in
    (`track_atmosphere.to_sky_bin` builds one from an `Atmosphere`).

    The legacy 50-entry tables at 0x4C / 0x1DC / 0xB4C are never touched --
    version 9 (the only version this module understands) does not read them,
    and ATM2 says to leave them as the template has them.  Everything from
    the header through the three slice-keyed tables at the end IS known and
    gets rewritten; there is no other unaccounted byte range in the format
    (see the offset arithmetic in ATM1/ATM2).

    `write(read(blob), blob) == blob` for every shipped `Sky/*.bin` -- ATM3
    acceptance.
    """
    if len(template) != SIZE:
        raise ValueError('template must be %d bytes, got %d' % (SIZE, len(template)))
    buf = bytearray(template)

    struct.pack_into('<I', buf, 0, VERSION)
    struct.pack_into('<f', buf, 0x04, sky.unknown_04)
    struct.pack_into('<i', buf, 0x08, sky.horizon_rotation)
    struct.pack_into('<i', buf, 0x0C, sky.unknown_0c)
    struct.pack_into('<i', buf, 0x10, sky.horizon_rows)
    struct.pack_into('<f', buf, 0x14, sky.horizon_bottom)
    struct.pack_into('<f', buf, 0x18, sky.horizon_top)
    struct.pack_into('<16H', buf, OFF_HORIZON_SLOTS, *sky.horizon_slots)

    struct.pack_into('<f', buf, 0x3C, sky.angle)
    struct.pack_into('<f', buf, 0x40, sky.dome_radius)
    struct.pack_into('<f', buf, 0x44, sky.dome_curvature)
    struct.pack_into('<f', buf, 0x48, sky.dome_height)

    struct.pack_into('<4f', buf, OFF_WATER, sky.water_angle, sky.water_dome_radius,
                     sky.water_dome_curvature, sky.water_dome_height)

    struct.pack_into('<3f', buf, OFF_SUN, sky.sun_x, sky.sun_y, sky.sky_scale)
    struct.pack_into('<i', buf, OFF_FLARE, sky.lens_flare)

    for i, d in enumerate(sky.detail):
        base = OFF_DETAIL + i * DETAIL_SIZE
        struct.pack_into('<i', buf, base, d.in_use)
        for b, blk in enumerate(d.blocks):
            bb = base + 4 + b * 0x1C
            struct.pack_into('<i', buf, bb, blk['follow_fog'])
            struct.pack_into('<6f', buf, bb + 4, *blk['colour_a'], *blk['colour_b'])

    _write_curve(buf, OFF_CURVE_REGION, sky.curve_region)
    _write_curve(buf, OFF_CURVE_B, sky.curve_b)
    _write_weather(buf, OFF_WEATHER, sky.weather)

    return bytes(buf)


def _terminate(buf, off):
    """Mark the end of a table at `off` with a negative slice number.

    Only the sign matters to the game (`FUN_0045a560` et al. just test
    `slice < 0`); shipped files do NOT agree on a sentinel value -- alps uses
    -1, others carry whatever was in memory when EA's tool last wrote there
    (e.g. 0xCBCBCBCB, the classic uninitialised-heap fill). If the template
    already has a negative slice right here, leave its bytes alone so a
    self-round-trip (`write(read(blob), blob)`) stays byte-identical;
    otherwise stamp an explicit -1.
    """
    if struct.unpack_from('<i', buf, off)[0] >= 0:
        struct.pack_into('<i', buf, off, -1)


def _write_curve(buf, base, keys):
    if len(keys) > MAX_KEYS - 1:
        raise ValueError('curve has %d keys, max %d' % (len(keys), MAX_KEYS - 1))
    for i, (slc, value) in enumerate(keys):
        struct.pack_into('<if', buf, base + i * CURVE_SIZE, slc, value)
    _terminate(buf, base + len(keys) * CURVE_SIZE)


def _write_weather(buf, base, keys):
    if len(keys) > MAX_KEYS - 1:
        raise ValueError('weather table has %d keys, max %d' % (len(keys), MAX_KEYS - 1))
    for i, k in enumerate(keys):
        struct.pack_into('<i3f2I6f', buf, base + i * WEATHER_SIZE,
                         k.slice, k.fog_density_a, k.fog_density_b, k.unknown_0c,
                         k.fog_colour_a, k.fog_colour_b, k.rain, k.unknown_1c,
                         k.snow, k.unknown_24, k.unknown_28, k.unknown_2c)
    _terminate(buf, base + len(keys) * WEATHER_SIZE)


# ------------------------------------------------------------------ dump ---

def _slots(values):
    return ' '.join(('m%d' % (v - 256)) if v >= 256 else str(v) for v in values)


def dump(sky, keys=False, curves=False):
    print('version %d' % sky.version)
    print('  dome        radius %.1f  height %.1f  curvature %.2e  angle %.4f  scale %.3f'
          % (sky.dome_radius, sky.dome_height, sky.dome_curvature, sky.angle, sky.sky_scale))
    print('  water dome  radius %.1f  height %.1f  curvature %.2e  angle %.4f'
          % (sky.water_dome_radius, sky.water_dome_height,
             sky.water_dome_curvature, sky.water_angle))
    print('  sun         x %.2f  y %.2f   lens flare %d' % (sky.sun_x, sky.sun_y, sky.lens_flare))
    print('  horizon     rows %d  rotation %d  bottom %.1f  top %.1f'
          % (sky.horizon_rows, sky.horizon_rotation, sky.horizon_bottom, sky.horizon_top))
    print('              slots %s' % _slots(sky.horizon_slots))
    print('  unknown     +0x04 %.4f   +0x0C %d' % (sky.unknown_04, sky.unknown_0c))
    for i, d in enumerate(sky.detail):
        if not d.in_use:
            print('  detail %d    unused' % i)
            continue
        for b, blk in enumerate(d.blocks):
            print('  detail %d/%d  A %s  B %s%s'
                  % (i, b,
                     ' '.join('%6.1f' % c for c in blk['colour_a']),
                     ' '.join('%6.1f' % c for c in blk['colour_b']),
                     '  follow_fog' if blk['follow_fog'] else ''))
    print('  tables      weather %d  region curve %d  curve_b %d'
          % (len(sky.weather), len(sky.curve_region), len(sky.curve_b)))
    if keys:
        print('  weather keys:')
        for k in sky.weather:
            print('    slice %-5d  fogA %.4f %-14s fogB %.4f %-14s rain %.3f snow %.3f  '
                  '[%.3f %.3f %.3f %.3f]'
                  % (k.slice, k.fog_density_a, '%d,%d,%d' % k.rgb('a'),
                     k.fog_density_b, '%d,%d,%d' % k.rgb('b'), k.rain, k.snow,
                     k.unknown_0c, k.unknown_1c, k.unknown_24, k.unknown_28))
    if curves:
        print('  region curve:')
        for sl, v in sky.curve_region:
            print('    slice %-5d  %.4f' % (sl, v))


if __name__ == '__main__':
    path = sys.argv[1]
    sky = read(path)
    print('%s:' % path)
    dump(sky, keys='--keys' in sys.argv, curves='--curves' in sys.argv)
