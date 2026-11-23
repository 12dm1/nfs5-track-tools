"""Atmosphere model: `Sky/<Track>.bin` <-> `Atmosphere` <-> `atmosphere.ini`.

The editor (ATM2) touches atmosphere only through this module -- the ini
format in `docs/formats/atmosphere-ini.md` is the single source
of truth for the pipeline, `Sky/*.bin` is a write-only target.

`Atmosphere` carries **one merged list of keys** for every slice-indexed
curve (fog, rain/snow/storm, sky region blend), instead of the two
independently-keyed tables the binary format uses (weather 0x1A5C, region
curve 0xDDC). `from_sky_bin` builds that union losslessly: both source
curves are piecewise linear, so sampling each at the other's breakpoints too
does not change either curve (ATM2 §5). `to_sky_bin` writes the merged list
into both tables, which is equally lossless in the other direction.

    python track_atmosphere.py <Sky/alps.bin> --dump-ini out.ini
    python track_atmosphere.py --presets   # dump all 20 stock tracks
"""

import configparser
import math
import os
import struct
import sys

import nfs5_environment as environment
import nfs5_sky_bin as skybin
import nfs5cfg

GAME = nfs5cfg.GAME
SKY_DIR = os.path.join(GAME, 'GameData', 'Track', 'Sky')
ASSETS_DIR = os.path.join(nfs5cfg.DATA, 'atmosphere')
TEMPLATE_SKY_BIN = os.path.join(SKY_DIR, 'Coastal.bin')
PRESETS_DIR = os.path.join(nfs5cfg.DATA, 'atmosphere', 'presets')

# The 20 shipped tracks (matches `GameData/Track/*.map`, one per real track;
# `GameData/Track/Sky/` additionally has dev leftovers -- 3Coastal/3Farmland/
# glukFarmland -- and unused legacy files -- Monaco.bin, default.bin -- plus
# the differently-structured `*_lightning.bin`, none of which are here).
STOCK_TRACKS = (
    'alps', 'autobahn', 'canyon', 'castle', 'coastal', 'dolphin', 'farmland',
    'foothills', 'forest', 'gt1', 'hometown', 'industrial',
    'monaco1', 'monaco2', 'monaco3', 'monaco4', 'monaco5',
    'skidpad', 'snowy', 'summit',
)


def track_slices(crp_path):
    """Track length in slices: last `RD%04dC` chunk start + 8 (CNK numbering
    is not reliable -- foothills/monaco run far past the road)."""
    import re
    import nfs5_crp
    crp = nfs5_crp.read(crp_path)
    starts = [int(m.group(1)) for m in
              (re.match(r'^RD(\d+)C\b', crp.name(i) or '') for i in range(crp.n_articles)) if m]
    return max(starts) + 8 if starts else 0


def sky_bin_path(track):
    for name in (track, track.capitalize()):
        p = os.path.join(SKY_DIR, name + '.bin')
        if os.path.exists(p):
            return p
    raise FileNotFoundError('no Sky/*.bin for %r in %s' % (track, SKY_DIR))


# --------------------------------------------------------------- model -----

class SkyDome:
    __slots__ = ('dome_radius', 'dome_curvature', 'dome_height', 'angle', 'scale',
                 'sun_x', 'sun_y', 'lens_flare', 'horizon_rows', 'horizon_rotation',
                 'horizon_bottom', 'horizon_top', 'horizon_slots', 'horizon_angle',
                 'water_angle', 'water_dome_radius', 'water_dome_curvature',
                 'water_dome_height')

    # `angle`, `water_angle`, `horizon_angle` are in TURNS (the game's trig
    # helpers multiply by 2*pi). `horizon_top` is the HEIGHT of the horizon
    # band: its top edge is horizon_bottom + horizon_top (`FUN_00459d30`).
    # `horizon_angle` None = keep the template's value.
    def __init__(self, **kw):
        kw.setdefault('horizon_angle', None)
        for k in self.__slots__:
            setattr(self, k, kw[k])


class SkyColour:
    __slots__ = ('follow_fog', 'colour_a', 'colour_b', 'water_colour_a', 'water_colour_b')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw[k])


class Key:
    """One merged breakpoint. `light_tint` is modelled but never reaches the
    `.bin` -- ATM2 §6 applies it to baked CRP vertex colour at conversion
    time, on a different cycle than the atmosphere editor."""

    __slots__ = ('slice', 'fog_colour', 'fog_density', 'fog_colour_b', 'fog_density_b',
                 'rain', 'snow', 'storm', 'region', 'light_tint')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw[k])


class Atmosphere:
    __slots__ = ('name', 'slices', 'sky', 'sky_colour', 'keys')

    def __init__(self, name, slices, sky, sky_colour, keys):
        self.name = name
        self.slices = slices
        self.sky = sky
        self.sky_colour = sky_colour
        self.keys = keys  # sorted by .slice, unique


# ---------------------------------------------------------- bin -> model ---

def _f32(v):
    """Round-trip through float32. The keyed fields below are lerped in
    double precision but the `.bin` only ever stores float32, and `%.9g`
    (ATM2 §5) only round-trips exactly AT float32 precision -- so the model
    snaps here to keep `model -> ini -> model` exact rather than just close."""
    return struct.unpack('<f', struct.pack('<f', v))[0]


def _argb_to_rgb(argb):
    return ((argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF)


def _rgb_to_argb(rgb):
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return 0xFF000000 | (r << 16) | (g << 8) | b


def _interp_weather(weather, slc, density_attr, colour_attr):
    lo, hi, f = skybin.SkyBin._bracket(weather, slc, lambda k: k.slice)
    a, b = weather[lo], weather[hi]
    density = getattr(a, density_attr) * f + getattr(b, density_attr) * (1.0 - f)
    ca, cb = _argb_to_rgb(getattr(a, colour_attr)), _argb_to_rgb(getattr(b, colour_attr))
    colour = tuple(round(ca[i] * f + cb[i] * (1.0 - f)) for i in range(3))
    return colour, _f32(density)


def _interp_scalar(weather, slc, attr):
    lo, hi, f = skybin.SkyBin._bracket(weather, slc, lambda k: k.slice)
    a, b = weather[lo], weather[hi]
    return _f32(getattr(a, attr) * f + getattr(b, attr) * (1.0 - f))


def _detail_to_sky_colour(sky):
    """All four detail-level records are meant to agree (ATM2 §6 / ATM1
    "for the editor: fill all four records the same"); take the first
    `in_use` one, block 0 (the non-water pass) for `colour_*`, block 1 (the
    water-reflection pass) for `water_colour_*`."""
    d = next((d for d in sky.detail if d.in_use), sky.detail[0])
    normal, water = d.blocks[0], d.blocks[1]
    return SkyColour(
        follow_fog=normal['follow_fog'],
        colour_a=tuple(round(c) for c in normal['colour_a']),
        colour_b=tuple(round(c) for c in normal['colour_b']),
        water_colour_a=tuple(round(c) for c in water['colour_a']),
        water_colour_b=tuple(round(c) for c in water['colour_b']),
    )


def from_sky_bin(sky, name='', slices=0):
    dome = SkyDome(
        dome_radius=sky.dome_radius, dome_curvature=sky.dome_curvature,
        dome_height=sky.dome_height, angle=sky.angle, scale=sky.sky_scale,
        sun_x=sky.sun_x, sun_y=sky.sun_y, lens_flare=sky.lens_flare,
        horizon_rows=sky.horizon_rows, horizon_rotation=sky.horizon_rotation,
        horizon_bottom=sky.horizon_bottom, horizon_top=sky.horizon_top,
        horizon_slots=list(sky.horizon_slots), horizon_angle=sky.unknown_04,
        water_angle=sky.water_angle, water_dome_radius=sky.water_dome_radius,
        water_dome_curvature=sky.water_dome_curvature,
        water_dome_height=sky.water_dome_height,
    )
    colour = _detail_to_sky_colour(sky)

    merged = sorted(set(k.slice for k in sky.weather) | set(s for s, _ in sky.curve_region))
    keys = []
    for slc in merged:
        fog_colour, fog_density = _interp_weather(sky.weather, slc, 'fog_density_a', 'fog_colour_a')
        fog_colour_b, fog_density_b = _interp_weather(sky.weather, slc, 'fog_density_b', 'fog_colour_b')
        keys.append(Key(
            slice=slc, fog_colour=fog_colour, fog_density=fog_density,
            fog_colour_b=fog_colour_b, fog_density_b=fog_density_b,
            rain=_interp_scalar(sky.weather, slc, 'rain'),
            snow=_interp_scalar(sky.weather, slc, 'snow'),
            storm=_interp_scalar(sky.weather, slc, 'unknown_1c'),
            region=_f32(sky.region_blend_at(slc)),
            light_tint=(1.0, 1.0, 1.0),
        ))
    return Atmosphere(name, slices, dome, colour, keys)


# ---------------------------------------------------------- model -> bin ---

class _WeatherKeyLike:
    __slots__ = ('slice', 'fog_density_a', 'fog_density_b', 'unknown_0c',
                 'fog_colour_a', 'fog_colour_b', 'rain', 'unknown_1c', 'snow',
                 'unknown_24', 'unknown_28', 'unknown_2c')


def _weather_row(key):
    w = _WeatherKeyLike()
    w.slice = key.slice
    w.fog_density_a = key.fog_density
    w.fog_density_b = key.fog_density_b
    w.fog_colour_a = _rgb_to_argb(key.fog_colour)
    w.fog_colour_b = _rgb_to_argb(key.fog_colour_b)
    w.rain = key.rain
    w.unknown_1c = key.storm
    w.snow = key.snow
    w.unknown_0c = w.unknown_24 = w.unknown_28 = w.unknown_2c = 0.0
    return w


class _DetailLike:
    __slots__ = ('in_use', 'blocks')


def _sky_colour_to_detail(colour):
    block_normal = dict(follow_fog=colour.follow_fog,
                        colour_a=tuple(float(c) for c in colour.colour_a),
                        colour_b=tuple(float(c) for c in colour.colour_b))
    block_water = dict(follow_fog=colour.follow_fog,
                       colour_a=tuple(float(c) for c in colour.water_colour_a),
                       colour_b=tuple(float(c) for c in colour.water_colour_b))
    out = []
    for _ in range(4):
        d = _DetailLike()
        d.in_use = 1
        d.blocks = [dict(block_normal), dict(block_water)]
        out.append(d)
    return out


class _SkyLike:
    __slots__ = ('unknown_04', 'unknown_0c', 'horizon_rotation', 'horizon_rows',
                 'horizon_bottom', 'horizon_top', 'horizon_slots', 'angle',
                 'dome_radius', 'dome_curvature', 'dome_height',
                 'water_angle', 'water_dome_radius', 'water_dome_curvature',
                 'water_dome_height', 'sun_x', 'sun_y', 'sky_scale', 'lens_flare',
                 'detail', 'curve_region', 'curve_b', 'weather')


def to_sky_bin(atmo, template):
    """`template` supplies the byte layout and every field this model does
    not carry: the legacy tables (untouched by `nfs5_sky_bin.write`), and
    the two header floats (0x04, 0x0C) whose consumer was never found
    (ATM1 "what is still unknown") -- template + patch, same as the
    reader/writer pair one level down."""
    base = skybin.SkyBin(template)
    sky = _SkyLike()
    sky.unknown_04 = base.unknown_04 if atmo.sky.horizon_angle is None else atmo.sky.horizon_angle
    sky.unknown_0c = base.unknown_0c

    d = atmo.sky
    sky.horizon_rotation = d.horizon_rotation
    sky.horizon_rows = d.horizon_rows
    sky.horizon_bottom = d.horizon_bottom
    sky.horizon_top = d.horizon_top
    sky.horizon_slots = d.horizon_slots
    sky.angle = d.angle
    sky.dome_radius = d.dome_radius
    sky.dome_curvature = d.dome_curvature
    sky.dome_height = d.dome_height
    sky.water_angle = d.water_angle
    sky.water_dome_radius = d.water_dome_radius
    sky.water_dome_curvature = d.water_dome_curvature
    sky.water_dome_height = d.water_dome_height
    sky.sun_x = d.sun_x
    sky.sun_y = d.sun_y
    sky.sky_scale = d.scale
    sky.lens_flare = d.lens_flare

    sky.detail = _sky_colour_to_detail(atmo.sky_colour)
    sky.curve_region = [(k.slice, k.region) for k in atmo.keys]
    sky.curve_b = base.curve_b
    sky.weather = [_weather_row(k) for k in atmo.keys]

    return skybin.write(sky, template)


# --------------------------------------------------------------- ini I/O ---

def _fmt_f(v):
    return '%.9g' % v


def _fmt_rgb(rgb):
    return ','.join(str(round(c)) for c in rgb)


def _fmt_rgbf(rgb):
    return ','.join(_fmt_f(c) for c in rgb)


def _parse_rgb(s):
    return tuple(int(x) for x in s.split(','))


def _parse_rgbf(s):
    return tuple(float(x) for x in s.split(','))


def save_ini(atmo, path):
    cp = configparser.ConfigParser()
    cp.optionxform = str

    cp['track'] = {'name': atmo.name, 'slices': str(atmo.slices)}

    d = atmo.sky
    cp['sky'] = {
        'dome_radius': _fmt_f(d.dome_radius), 'dome_curvature': _fmt_f(d.dome_curvature),
        'dome_height': _fmt_f(d.dome_height), 'angle': _fmt_f(d.angle),
        'scale': _fmt_f(d.scale), 'sun_x': _fmt_f(d.sun_x), 'sun_y': _fmt_f(d.sun_y),
        'lens_flare': str(d.lens_flare), 'horizon_rows': str(d.horizon_rows),
        'horizon_rotation': str(d.horizon_rotation),
        'horizon_bottom': _fmt_f(d.horizon_bottom), 'horizon_top': _fmt_f(d.horizon_top),
        'horizon_slots': ' '.join(str(v) for v in d.horizon_slots),
        **({} if d.horizon_angle is None else {'horizon_angle': _fmt_f(d.horizon_angle)}),
        'water_angle': _fmt_f(d.water_angle), 'water_dome_radius': _fmt_f(d.water_dome_radius),
        'water_dome_curvature': _fmt_f(d.water_dome_curvature),
        'water_dome_height': _fmt_f(d.water_dome_height),
    }

    c = atmo.sky_colour
    cp['sky_colour'] = {
        'follow_fog': str(c.follow_fog),
        'colour_a': _fmt_rgb(c.colour_a), 'colour_b': _fmt_rgb(c.colour_b),
        'water_colour_a': _fmt_rgb(c.water_colour_a),
        'water_colour_b': _fmt_rgb(c.water_colour_b),
    }

    for k in atmo.keys:
        cp['key:%d' % k.slice] = {
            'fog_colour': _fmt_rgb(k.fog_colour), 'fog_density': _fmt_f(k.fog_density),
            'fog_colour_b': _fmt_rgb(k.fog_colour_b), 'fog_density_b': _fmt_f(k.fog_density_b),
            'rain': _fmt_f(k.rain), 'snow': _fmt_f(k.snow), 'storm': _fmt_f(k.storm),
            'region': _fmt_f(k.region), 'light_tint': _fmt_rgbf(k.light_tint),
        }

    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        cp.write(f)


def load_ini(path):
    cp = configparser.ConfigParser()
    cp.optionxform = str
    with open(path, 'r', encoding='utf-8') as f:
        cp.read_file(f)

    name = cp['track'].get('name', '')
    slices = cp['track'].getint('slices', fallback=0)

    s = cp['sky']
    dome = SkyDome(
        dome_radius=s.getfloat('dome_radius'), dome_curvature=s.getfloat('dome_curvature'),
        dome_height=s.getfloat('dome_height'), angle=s.getfloat('angle'),
        scale=s.getfloat('scale'), sun_x=s.getfloat('sun_x'), sun_y=s.getfloat('sun_y'),
        lens_flare=s.getint('lens_flare'), horizon_rows=s.getint('horizon_rows'),
        horizon_rotation=s.getint('horizon_rotation'),
        horizon_bottom=s.getfloat('horizon_bottom'), horizon_top=s.getfloat('horizon_top'),
        horizon_slots=[int(v) for v in s['horizon_slots'].split()],
        horizon_angle=s.getfloat('horizon_angle', fallback=None),
        water_angle=s.getfloat('water_angle'), water_dome_radius=s.getfloat('water_dome_radius'),
        water_dome_curvature=s.getfloat('water_dome_curvature'),
        water_dome_height=s.getfloat('water_dome_height'),
    )
    if len(dome.horizon_slots) != 16:
        raise ValueError('%s: horizon_slots needs 16 values, got %d'
                         % (path, len(dome.horizon_slots)))

    sc = cp['sky_colour']
    colour = SkyColour(
        follow_fog=sc.getint('follow_fog'),
        colour_a=_parse_rgb(sc['colour_a']), colour_b=_parse_rgb(sc['colour_b']),
        water_colour_a=_parse_rgb(sc['water_colour_a']),
        water_colour_b=_parse_rgb(sc['water_colour_b']),
    )

    keys = []
    for section in cp.sections():
        if not section.startswith('key:'):
            continue
        slc = int(section[len('key:'):])
        k = cp[section]
        fog_colour = _parse_rgb(k['fog_colour'])
        fog_density = k.getfloat('fog_density')
        keys.append(Key(
            slice=slc, fog_colour=fog_colour, fog_density=fog_density,
            fog_colour_b=_parse_rgb(k.get('fog_colour_b')) if 'fog_colour_b' in k else fog_colour,
            fog_density_b=k.getfloat('fog_density_b', fallback=fog_density),
            rain=k.getfloat('rain', fallback=0.0), snow=k.getfloat('snow', fallback=0.0),
            storm=k.getfloat('storm', fallback=0.0), region=k.getfloat('region', fallback=0.0),
            light_tint=_parse_rgbf(k.get('light_tint', '1,1,1')),
        ))
    keys.sort(key=lambda k: k.slice)
    if len(keys) != len(set(k.slice for k in keys)):
        raise ValueError('%s: duplicate slice among [key:N] sections' % path)

    return Atmosphere(name, slices, dome, colour, keys)


# --------------------------------------------------------------- rescale ---

def rescale(atmo, slices_old, slices_new):
    """New `Atmosphere` with every key's slice carried over by fraction of
    track length (ATM2 §9): `slice_new = round(slice_old / slices_old *
    slices_new)`. Coarse by design -- a starting point for hand-tuning, not
    a physical mapping. Ties (two old slices landing on the same new one)
    keep the later key."""
    if slices_old <= 0:
        raise ValueError('slices_old must be positive, got %d' % slices_old)
    by_slice = {}
    for k in atmo.keys:
        new_slc = round(k.slice / slices_old * slices_new)
        new_slc = max(0, min(slices_new - 1, new_slc))
        new_key = Key(**{name: getattr(k, name) for name in Key.__slots__})
        new_key.slice = new_slc
        by_slice[new_slc] = new_key
    keys = sorted(by_slice.values(), key=lambda k: k.slice)
    return Atmosphere(atmo.name, slices_new, atmo.sky, atmo.sky_colour, keys)


# ---------------------------------------------------------- light tint -----

def tint_at(atmo, slc):
    """Per-channel baked-light multiplier at `slc`: piecewise linear between
    keys, held flat before the first and after the last (ATM2 §6)."""
    keys = atmo.keys
    if not keys:
        return (1.0, 1.0, 1.0)
    if slc <= keys[0].slice:
        return tuple(keys[0].light_tint)
    if slc >= keys[-1].slice:
        return tuple(keys[-1].light_tint)
    for a, b in zip(keys, keys[1:]):
        if a.slice <= slc <= b.slice:
            f = (slc - a.slice) / float(b.slice - a.slice)
            return tuple(a.light_tint[i] * (1 - f) + b.light_tint[i] * f for i in range(3))
    return (1.0, 1.0, 1.0)


def tint_colour(d3dcolor, tint):
    """`df' = clamp(round(df * tint), 0, 255)` on the R, G, B bytes of a
    D3DCOLOR (0xAARRGGBB); alpha is left alone."""
    out = d3dcolor & 0xFF000000
    for shift, m in ((16, tint[0]), (8, tint[1]), (0, tint[2])):
        out |= max(0, min(255, int(round(((d3dcolor >> shift) & 0xFF) * m)))) << shift
    return out


# ---------------------------------------------------- NFS4 tr.ini -> auto ---

# NFS4 `fogDensity` is a 0..100 percentage; PU stores a raw value that the
# game cubes. Stock files span 0.005 (haze) .. 0.26 (alps summit, half
# visibility ~40 m). Map linearly onto 0.05 .. 0.26 -- density 20 lands near
# alps' valley fog. A first approximation; the editor is where it gets tuned.
FOG_RAW_MIN, FOG_RAW_MAX = 0.05, 0.26

_RAIN, _SNOW = 1, 2   # NFS4 [weather] type; only Snowy (2) uses it in HS data


def _fog_raw(density):
    return FOG_RAW_MIN + (FOG_RAW_MAX - FOG_RAW_MIN) * max(0.0, min(100.0, density)) / 100.0


def _curve_at(points, s):
    """Piecewise-linear over sorted (slice, value) points, flat outside."""
    if s <= points[0][0]:
        return points[0][1]
    if s >= points[-1][0]:
        return points[-1][1]
    for (s0, v0), (s1, v1) in zip(points, points[1:]):
        if s0 <= s <= s1:
            f = (s - s0) / float(s1 - s0) if s1 > s0 else 1.0
            if isinstance(v0, tuple):
                return tuple(v0[i] * (1 - f) + v1[i] * f for i in range(len(v0)))
            return v0 * (1 - f) + v1 * f
    return points[-1][1]


def _sorted_points(points):
    """Sort by slice; a repeated slice keeps the last value given."""
    return sorted(dict(points).items())


def sun_plane_position(theta, rho, height, curvature):
    """(sun_x, sun_y) on the dome paraboloid for azimuth `theta` and elevation
    `rho` (radians): solve (H - c r^2) / r = tan(rho) for the plane radius."""
    t = math.tan(rho)
    r = (-t + math.sqrt(t * t + 4.0 * curvature * height)) / (2.0 * curvature)
    return r * math.cos(theta), r * math.sin(theta)


def from_nfs4_ini(ini, n_slices, name, template_blob):
    """First-approximation atmosphere for a converted HS track.

    Fog: `[fog]` is the global colour/density, each `[fog region N]` adds a
    start/centre/end key triple. Precipitation: `[weather]` type 2 = snow,
    1 = rain (assumed), `denstiy`% between startSlice and endSlice with a
    `fade`-slice ramp on both sides. Storm: `[lightning] lightningChance`/100
    on the whole track. Dome comes from the template, the horizon band from
    `[strip]` (`environment.horizon_band`), sun from `[sun]`, sky colour
    from `[strip]`. Ignored: `fogHorizon`, tunnel cover (the
    generated `.map` has no covered sections to read it from).
    """
    last = max(0, n_slices - 1)
    base = from_sky_bin(skybin.SkyBin(template_blob), name=name, slices=n_slices)
    sky = base.sky

    fog_col = environment.ini_colour(ini, 'fog', 'fogColor', (128, 128, 128))
    fog_den = environment.ini_float(ini, 'fog', 'fogDensity', 20.0)
    fog_c = [(0, fog_col), (last, fog_col)]
    fog_d = [(0, fog_den), (last, fog_den)]
    for section in sorted(k for k in ini if k.startswith('fog region')):
        for prefix, slice_key in (('s', 'startSlice'), ('c', 'centerSlice'), ('e', 'endSlice')):
            sl = int(environment.ini_float(ini, section, slice_key, -1))
            if not 0 <= sl <= last:
                continue
            fog_c.append((sl, environment.ini_colour(ini, section, prefix + '_color', fog_col)))
            fog_d.append((sl, environment.ini_float(ini, section, prefix + '_density', fog_den)))
    fog_c, fog_d = _sorted_points(fog_c), _sorted_points(fog_d)

    precip = [(0, 0.0), (last, 0.0)]
    wtype = int(environment.ini_float(ini, 'weather', 'type', 0))
    w0 = int(environment.ini_float(ini, 'weather', 'startSlice', 0))
    w1 = int(environment.ini_float(ini, 'weather', 'endSlice', 0))
    fade = max(1, int(environment.ini_float(ini, 'weather', 'fade', 1)))
    strength = environment.ini_float(ini, 'weather', 'denstiy', 100.0) / 100.0
    if wtype in (_RAIN, _SNOW) and w1 > w0:
        precip += [(max(0, w0 - fade), 0.0), (w0, strength), (w1, strength),
                   (min(last, w1 + fade), 0.0)]
    precip = _sorted_points(precip)
    storm = environment.ini_float(ini, 'lightning', 'lightningChance', 0.0) / 100.0

    slices = sorted({s for s, _ in fog_c} | {s for s, _ in fog_d} | {s for s, _ in precip})
    keys = []
    for s in slices:
        rain = snow = 0.0
        p = _f32(_curve_at(precip, s))
        if wtype == _SNOW:
            snow = p
        elif wtype == _RAIN:
            rain = p
        colour = tuple(int(round(c)) for c in _curve_at(fog_c, s))
        density = _f32(_fog_raw(_curve_at(fog_d, s)))
        keys.append(Key(slice=s, fog_colour=colour, fog_density=density,
                        fog_colour_b=colour, fog_density_b=density,
                        rain=rain, snow=snow, storm=_f32(storm), region=0.0,
                        light_tint=(1.0, 1.0, 1.0)))

    sky.angle = 0.0
    band = environment.horizon_band(ini)
    if band is not None:
        bottom, height, sky.horizon_slots = band
        sky.horizon_bottom, sky.horizon_top = _f32(bottom), _f32(height)
    if int(environment.ini_float(ini, 'sun', 'hasSun', 1)):
        sky.sun_x, sky.sun_y = (_f32(v) for v in sun_plane_position(
            environment.ini_float(ini, 'sun', 'angleTheta', 0.5),
            environment.ini_float(ini, 'sun', 'angleRho', 0.3),
            sky.dome_height, sky.dome_curvature))
        sky.lens_flare = 1
    else:
        sky.lens_flare = 0
    colour = tuple(int(c) for c in environment.ini_colour(
        ini, 'strip', 'hrzSunColor', (128, 128, 144)))
    base.sky_colour = SkyColour(follow_fog=0, colour_a=colour, colour_b=colour,
                                water_colour_a=colour, water_colour_b=colour)
    base.keys = keys
    return base


def atmosphere_for(name, src_dir, n_slices, assets_dir=ASSETS_DIR, template=TEMPLATE_SKY_BIN):
    """`(Atmosphere, 'ini'|'auto')` for a converted track.

    `<assets_dir>/<name>.ini` wins when present (hand-tuned in the editor);
    otherwise a first approximation is generated from the NFS4 `tr.ini` and
    saved there so it can be refined."""
    path = os.path.join(assets_dir, '%s.ini' % name)
    if os.path.exists(path):
        return load_ini(path), 'ini'
    ini = environment.read_ini(os.path.join(src_dir, 'tr.ini'))
    atmo = from_nfs4_ini(ini, n_slices, name, open(template, 'rb').read())
    save_ini(atmo, path)
    return atmo, 'auto'


def sky_bin_for(atmo, template=TEMPLATE_SKY_BIN):
    return to_sky_bin(atmo, open(template, 'rb').read())


# ------------------------------------------------------------- presets -----

def dump_presets(out_dir=PRESETS_DIR, tracks=STOCK_TRACKS, verbose=True):
    os.makedirs(out_dir, exist_ok=True)
    for track in tracks:
        sky = skybin.read(sky_bin_path(track))
        atmo = from_sky_bin(sky, name=track,
                            slices=track_slices(os.path.join(GAME, 'GameData', 'Track', '%s.crp' % track)))
        ini_path = os.path.join(out_dir, '%s.ini' % track)
        save_ini(atmo, ini_path)
        if verbose:
            print('%-10s %3d keys -> %s' % (track, len(atmo.keys), ini_path))
    return len(tracks)


if __name__ == '__main__':
    if '--presets' in sys.argv:
        n = dump_presets()
        print('done: %d presets in %s' % (n, PRESETS_DIR))
    elif '--dump-ini' in sys.argv:
        i = sys.argv.index('--dump-ini')
        bin_path, ini_path = sys.argv[1], sys.argv[i + 1]
        sky = skybin.read(bin_path)
        atmo = from_sky_bin(sky, name=os.path.splitext(os.path.basename(bin_path))[0])
        save_ini(atmo, ini_path)
        print('%d keys -> %s' % (len(atmo.keys), ini_path))
    else:
        print(__doc__)
