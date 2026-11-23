"""HS7: sky, reflections, lighting tints, ambient audio and police spawns.

Ported from the NFS4 side as follows.

`tr.ini` carries the lighting and weather description: `[light]` has the ambient
level, `[strip]` the horizon and sky colours, `[clouds]` the cloud layer, `[sun]`
its position. All of it is plain text and is read straight into the PU files.

Sky textures line up better than expected. NFS4 keeps the horizon as eight
64x64 panels HDC0..HDC7 whose upper rows are colour-keyed transparent and whose
lower rows are the opaque terrain silhouette. PU's `horz` is a 4x2 atlas of
64x128 panels and its ring has 16 faces, each showing one atlas panel (slot
table in the sky `.bin`). Each HDC panel goes whole into its own atlas cell,
stretched in height only; the band height comes from `[strip]` (see
`horizon_band`), so nothing is cropped. NFS4 panels are 22.5 degrees wide:
eight of them plus their mirror images (`hrzMirror=1`) or two copies close
the circle, which is exactly PU's 16 slots.

The rest of PU's `Sky/<Name>.fsh`:

    st1a, st1b  16x16  hemispherical sky gradient, zenith at the centre
    sc1a, sc1b  16x16  cloud layer per weather region
    cm1a        32x32  cloud texture
    cc1a        32x32  second cloud layer
    hzcl        16x2   horizon vertex colours (row 0 top, row 1 bottom)
    horz      256x256  horizon panorama

`.env` holds seven 64x64 faces used for car reflections (1, 2, 3, BT1, BT2, YT1,
YT2). They are generated as a sky-to-ground gradient from the same `[strip]`
colours, which is what a reflection off a car body mostly shows.

`.map` gets one default section plus the header multipliers. It does NOT carry
road diffuse textures - those live in the CRP materials - it selects the
environment map per slice range and scales the baked lighting (HS1 4.2).

Not ported: replay cameras. NFS4's `tr.cam` is a uint32 count followed by
68-byte records whose fields are not decoded, and a wrong camera is worse than
no camera. Open question 2 in HS1.
"""

import math
import os
import re
import struct

from nfs5_fsh import write_fsh

ENV_FACES = ('1   ', '2   ', '3   ', 'BT1 ', 'BT2 ', 'YT1 ', 'YT2 ')
ENV_SIZE = 64
COP_SPACING = 200          # slices between police spawn points, as PU does it


# ---------------------------------------------------------------- tr.ini ---

def read_ini(path):
    """NFS4 track ini -> {section: {key: raw string}}. Values keep their text;
    helpers below pull colours and numbers out."""
    out, section = {}, None
    with open(path, 'r', encoding='latin1') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('['):
                section = line[1:line.index(']')].lower()
                out.setdefault(section, {})
            elif '=' in line and section is not None:
                key, value = line.split('=', 1)
                out[section][key.strip().lower()] = value.strip()
    return out


def ini_colour(ini, section, key, default=(128, 128, 128)):
    """`[12,34,56]` or `[255,12,34,56]` -> (r, g, b)."""
    raw = ini.get(section, {}).get(key.lower())
    if not raw:
        return default
    nums = [int(n) for n in re.findall(r'-?\d+', raw)]
    if len(nums) >= 4:
        nums = nums[1:4]
    return tuple(max(0, min(255, n)) for n in nums[:3]) if len(nums) >= 3 else default


def ini_float(ini, section, key, default=0.0):
    raw = ini.get(section, {}).get(key.lower())
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- map ---

def build_map(ini):
    """One default section; the header multipliers come from `[light]`.

    NFS4 states ambient as a percentage, PU as a multiplier around 1.0, so the
    conversion is value/100. `tint` has no NFS4 counterpart and is left neutral.
    """
    amb = tuple(ini_float(ini, 'light', 'ambient' + c, 100.0) / 100.0
                for c in ('red', 'green', 'blue'))
    lines = ['[header]', 'count=1',
             'tint=1.000000,1.000000,1.000000,1.000000',
             'ambient=1.000000,%.6f,%.6f,%.6f' % amb,
             'interior=1.000000,1.000000,1.000000,1.000000',
             'lightdecal=1.000000,1.000000,1.000000,1.000000',
             '', '[section0]', 'startslice=0', 'endslice=0']
    env = struct.unpack('<I', b'1   ')[0]
    bt1 = struct.unpack('<I', b'BT1 ')[0]
    bt2 = struct.unpack('<I', b'BT2 ')[0]
    lines += ['texture1=%d' % env, 'clamp1=0', 'clamp2=1',
              'tint1=1.000000,1.000000,1.000000,1.000000',
              'tint2=1.000000,1.000000,1.000000,1.000000',
              'texture1.open=%d' % env, 'texture1.covered=%d' % env,
              'texture2.open=%d' % bt1, 'texture2.covered=%d' % bt1,
              'texture3.open=%d' % bt2, 'texture3.covered=%d' % bt2,
              'texturesize=6.000000', 'aspect=1.000000', '']
    return '\r\n'.join(lines).encode('latin1')


# ------------------------------------------------------------------- env ---

def _gradient_face(top, bottom, size=ENV_SIZE):
    """Vertical sky-to-ground blend as BGRA."""
    out = bytearray(size * size * 4)
    for y in range(size):
        t = y / max(1, size - 1)
        c = [int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)]
        row = bytes((c[2], c[1], c[0], 255)) * size
        out[y * size * 4:(y + 1) * size * 4] = row
    return out


def build_env(ini):
    sky = ini_colour(ini, 'strip', 'hrzSkyTopColor', (120, 182, 239))
    horizon = ini_colour(ini, 'strip', 'hrzSunColor', (223, 210, 151))
    ground = ini_colour(ini, 'strip', 'hrzEarthBotColor', (207, 153, 98))
    faces = []
    for tag in ENV_FACES:
        top, bottom = (sky, horizon) if tag[0] in '123Y' else (horizon, ground)
        faces.append((tag, ENV_SIZE, ENV_SIZE, _gradient_face(top, bottom)))
    # PU's own .env holds a single level per face, no mip chain.
    return write_fsh(faces, mips=False)


# ------------------------------------------------------------------- sky ---

def _resample(src, sw, sh, dw, dh):
    out = bytearray(dw * dh * 4)
    for y in range(dh):
        sy = min(sh - 1, y * sh // dh)
        for x in range(dw):
            sx = min(sw - 1, x * sw // dw)
            s = (sy * sw + sx) * 4
            d = (y * dw + x) * 4
            out[d:d + 4] = src[s:s + 4]
    return out


def _cloud_alpha(bgra, nimbus=0.80):
    """CLDD is opaque grey. PU's sc1a uses alpha as coverage, so dark texels
    (sky showing through) become transparent and the rest stay soft."""
    out = bytearray(bgra)
    for i in range(0, len(out), 4):
        lum = (out[i] + out[i + 1] + out[i + 2]) // 3
        alpha = int(lum * nimbus)
        if alpha < 40:
            alpha = 0
        out[i + 3] = min(255, alpha)
    return out


HORIZON_RADIUS = 400.0                           # PU ring radius, FUN_00459d30
HORIZON_V_TOP, HORIZON_V_BOTTOM = 0.01, 0.98     # v range of a panel on the ring
HORIZON_FOOT = -20.0     # the band reaches at least this far below the eye


def _horizon_extent(ini):
    """(top, panel_bottom, foot) of the NFS4 panorama in PU ring units, or
    None when the track draws no panels (`hrzHasPMX=0`).

    NFS4 `[strip]`: the panels span hrzPmxBottom..hrzPmxTop in strip units,
    the strip starts hrzBottomYOff below the eye, on a cylinder of radius
    hrzfRadius; PU's ring has radius 400. Where the panels end above
    HORIZON_FOOT (Country, France) their last row is carried down to it, as
    NFS4's earth strip would cover that gap.
    """
    if not int(ini_float(ini, 'strip', 'hrzHasPMX', 1)):
        return None
    off = ini_float(ini, 'strip', 'hrzBottomYOff', -750.0)
    k = HORIZON_RADIUS / (ini_float(ini, 'strip', 'hrzfRadius', 1500.0) or 1500.0)
    top = (ini_float(ini, 'strip', 'hrzPmxTop', 1200.0) + off) * k
    bottom = min(top - 1.0, (ini_float(ini, 'strip', 'hrzPmxBottom', 600.0) + off) * k)
    return top, bottom, min(bottom, HORIZON_FOOT)


def horizon_band(ini):
    """(horizon_bottom, horizon_top, horizon_slots) for the PU sky `.bin`, or
    None to keep the template's (no panels).

    A panel shows v 0.01..0.98 of its atlas cell, so the band is that much
    of the image extent: texture row 0 lands on the NFS4 top edge.
    `horizon_top` is the band HEIGHT, as in the `.bin`. NFS4 panels are
    22.5 degrees wide; eight of them plus their mirror images (`hrzMirror`)
    or two plain copies close the circle.
    """
    ext = _horizon_extent(ini)
    if ext is None:
        return None
    top, _bottom, foot = ext
    h = top - foot
    slots = list(range(8))
    if int(ini_float(ini, 'strip', 'hrzMirror', 0)):
        slots += [0x100 | p for p in range(7, -1, -1)]
    else:
        slots += list(range(8))
    return (top - HORIZON_V_BOTTOM * h,
            (HORIZON_V_BOTTOM - HORIZON_V_TOP) * h, slots)


def _on_earth(bgra, pw, ph, ini):
    """NFS4 draws the panels over its gouraud strip: below hrzGouraudMiddle
    that strip is earth, hrzEarthTopColor there fading to hrzEarthBotColor at
    the strip's foot. PU has only sky behind the ring, so transparent texels
    under that line (Dolphin Cove's sea under the islands) get the earth
    colour baked in; those above it stay sky."""
    pmx_top = ini_float(ini, 'strip', 'hrzPmxTop', 1200.0)
    pmx_bottom = ini_float(ini, 'strip', 'hrzPmxBottom', 600.0)
    middle = ini_float(ini, 'strip', 'hrzGouraudMiddle', 750.0)
    e_top = ini_colour(ini, 'strip', 'hrzEarthTopColor', (117, 106, 15))
    e_bot = ini_colour(ini, 'strip', 'hrzEarthBotColor', e_top)
    out = bytearray(bgra)
    for y in range(ph):
        h = pmx_top - (y + 0.5) / ph * (pmx_top - pmx_bottom)
        if h >= middle:
            continue
        t = min(1.0, max(0.0, (middle - h) / middle)) if middle > 0 else 1.0
        earth = [e_top[i] + (e_bot[i] - e_top[i]) * t for i in (2, 1, 0)]
        for x in range(pw):
            o = (y * pw + x) * 4
            a = out[o + 3] / 255.0
            for i in range(3):
                out[o + i] = int(round(out[o + i] * a + earth[i] * (1.0 - a)))
            out[o + 3] = 255
    return out


def _horz_atlas(panels, ini):
    """HDC0..7 -> `horz` 256x256: panel p at column p % 4, row p // 4 of
    64x128 cells. Each 64x64 source is stretched in height only and never
    cropped; any foot below it repeats its last row (`_horizon_extent`)."""
    top, bottom, foot = _horizon_extent(ini)
    rows = max(1, min(128, int(round(128 * (top - bottom) / (top - foot)))))
    out = bytearray(256 * 256 * 4)
    for p, (pw, ph, bgra) in enumerate(panels):
        cell = _resample(_on_earth(bgra, pw, ph, ini), pw, ph, 64, rows)
        x0, y0 = (p % 4) * 64, (p // 4) * 128
        for y in range(128):
            src = min(y, rows - 1) * 64 * 4
            d = ((y0 + y) * 256 + x0) * 4
            out[d:d + 64 * 4] = cell[src:src + 64 * 4]
    return out


def _panels(entries, decode, prefix, count=8):
    """HDC0..HDC7 decoded as [(w, h, bgra)], or None if any is missing."""
    panels = []
    for i in range(count):
        e = entries.get('%s%d' % (prefix, i))
        if e is None:
            return None
        panels.append(decode(e))
    return panels


def _sky_gradient(zenith, horizon, size=16):
    """Fisheye cap: zenith at the centre, horizon at the rim."""
    out = bytearray(size * size * 4)
    c = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            r = min(1.0, math.hypot(x - c, y - c) / c)
            col = [int(zenith[i] + (horizon[i] - zenith[i]) * r) for i in range(3)]
            o = (y * size + x) * 4
            out[o:o + 4] = bytes((col[2], col[1], col[0], 255))
    return out


def _solid(colour, size, alpha=255):
    px = bytes((colour[2], colour[1], colour[0], alpha))
    return bytearray(px * (size * size))


def build_sky_fsh(ini, sky_entries, decode):
    zenith = ini_colour(ini, 'strip', 'hrzSkyTopColor', (120, 182, 239))
    sun = ini_colour(ini, 'strip', 'hrzSunColor', (223, 210, 151))
    opposite = ini_colour(ini, 'strip', 'hrzOppositeSunColor', sun)

    textures = [('st1a', 16, 16, _sky_gradient(zenith, sun)),
                ('st1b', 16, 16, _sky_gradient(zenith, opposite))]

    cloud = sky_entries.get('CLDD') or sky_entries.get('CLWD')
    nimbus = ini_float(ini, 'clouds', 'cloudNimbus', 0.80)
    if cloud is not None:
        cw, ch, cdata = decode(cloud)
        sc = _cloud_alpha(_resample(cdata, cw, ch, 16, 16), nimbus)
        cm = _cloud_alpha(_resample(cdata, cw, ch, 32, 32), nimbus)
        textures += [('sc1a', 16, 16, sc),
                     ('sc1b', 16, 16, bytearray(sc)),
                     ('cm1a', 32, 32, cm),
                     ('cc1a', 32, 32, bytearray(cm))]
    else:
        grey = ini_colour(ini, 'strip', 'hrzSkyTopColor', (160, 160, 180))
        textures += [('sc1a', 16, 16, _solid(grey, 16, alpha=0)),
                     ('sc1b', 16, 16, _solid(grey, 16, alpha=0)),
                     ('cm1a', 32, 32, _solid(grey, 32, alpha=0)),
                     ('cc1a', 32, 32, _solid(grey, 32, alpha=0))]

    # White = panels as drawn in NFS4. The ring modulates `horz` by these
    # vertex colours; the old sun/earth pair darkened the silhouette's foot.
    textures.append(('hzcl', 16, 2, bytearray(b'\xff' * (16 * 2 * 4))))

    # Aquatica, Atlantic and Gt1 ship no horizon panels at all, only clouds
    # and a sun. They get a fully transparent `horz`: no silhouette.
    # Gt2 ships panels but sets hrzHasPMX=0: NFS4 does not draw them either.
    panels = _panels(sky_entries, decode, 'HDC')
    if panels and _horizon_extent(ini) is not None:
        horz = _horz_atlas(panels, ini)
    else:
        horz = bytearray(256 * 256 * 4)
    textures.append(('horz', 256, 256, horz))
    return write_fsh(textures, mips=False)


SKY_TXT = """BEGIN
#Computer capability
\tHIGH
#sky level type
\tMULTINORMAL
#Does this layer block the sun
NO
#
#  WEATHER REGION A
#
\tst1a
\tsc1a
\tnone
#X Scale Factors
\t1
\t%(xscale)d
\t0
#Y Scale Factors
\t1
\t%(yscale)d
\t0
#move ratio
\t%(move)d
#wind speed
\t0
\t%(wind)d
\t0
#wind direction
\t%(dir)d
\t%(dir)d
\t0
#warp speed
\t0
#warp amount
\t0
#
#  WEATHER REGION B
#
\tst1b
\tsc1b
\tnone
#X Scale Factors
\t1
\t%(xscale)d
\t0
#Y Scale Factors
\t1
\t%(yscale)d
\t0
#move ratio
\t%(move)d
#wind speed
\t0
\t%(wind)d
\t0
#wind direction
\t%(dir)d
\t%(dir)d
\t0
#warp speed
\t0
#warp amount
\t0
# CLOUD TEXTURES
\tflat
\tcc1a
\tnone
END
"""


def build_sky_txt(ini):
    return (SKY_TXT % dict(
        xscale=max(1, int(ini_float(ini, 'clouds', 'cloudUscale', 1.0) * 2)),
        yscale=max(1, int(ini_float(ini, 'clouds', 'cloudVscale', 1.0) * 2)),
        # PU: 50 is fast, 400 is slow. Mapping NFS4 windSpeed=10 onto 50/10
        # made Hometown's layer stutter. Stock skies sit at 400/2.
        move=400,
        wind=2,
        dir=int(ini_float(ini, 'clouds', 'windDir', 90)) % 360,
    )).replace('\n', '\r\n').encode('latin1')


# ----------------------------------------------------------------- audio ---

def build_audio_scn(track, nfs4_name, builder):
    """NFS4 ambience -> AUDIO_ELEMENTs + the banks they need.

    The work is in `nfs5_ambient`: NFS4 keeps its loop zones and one-shot
    scripts inside nfshs.exe, and every reference is a slot of the track's
    `tram%02d.bnk` (HS13).  Here we only hand it the road geometry.
    """
    return builder.build(track, nfs4_name)


# ------------------------------------------------------------------- cop ---

def build_cop(n_slices, spacing=COP_SPACING):
    points = list(range(spacing // 2, n_slices, spacing))
    out = bytearray(struct.pack('<I', len(points)))
    for s in points:
        out += struct.pack('<5I', 1, s, 0, 1, 1)
    return bytes(out)


# -------------------------------------------------------------- pipeline ---

def convert(track, name, src_dir, out_dir, sky_entries, decode, sky_bin_template,
            nfs4_name=None, ambient=None):
    ini = read_ini(os.path.join(src_dir, 'tr.ini'))
    sky_dir = os.path.join(out_dir, 'Sky')
    os.makedirs(sky_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'Cops'), exist_ok=True)
    written = {}

    def put(rel, blob):
        path = os.path.join(out_dir, rel)
        with open(path, 'wb') as f:
            f.write(blob)
        written[rel.replace('\\', '/')] = len(blob)


    put('%s.map' % name, build_map(ini))
    put('%s.env' % name, build_env(ini))
    audio_banks, audio_stats = {}, {}
    if ambient is not None:
        scn, audio_banks, audio_stats = build_audio_scn(track, nfs4_name or name, ambient)
        put('%s_audio.scn' % name, scn)
    put(os.path.join('Cops', '%s.cop' % name), build_cop(track.n_vroad))
    put(os.path.join('Sky', '%s.fsh' % name.capitalize()),
        build_sky_fsh(ini, sky_entries, decode))
    put(os.path.join('Sky', '%s.txt' % name.capitalize()), build_sky_txt(ini))
    put(os.path.join('Sky', '%s.bin' % name.capitalize()), sky_bin_template)
    return dict(files=written, audio_banks=audio_banks, audio=audio_stats)
