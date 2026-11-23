"""HS8: register a converted track with the frontend, without touching the
files the game shipped with.

Nothing here writes into `FEData/Data/nfs5.trk` or `FEData/Locale/FEStrings.csv`.
Instead:

  * a full **`nfs5ext.trk`** is generated - the stock 15 records copied verbatim,
    then one appended record per converted track. The wrapper points the loader
    at this file when `[content] extended_content=1`, and the stock file is left
    alone. The loader derives the record count from the file size
    (`FUN_004fae20`: `filesize / 0x4A8`), so appending is all that is needed.

  * track names come from a **GetString hook** on `0x004E06D0` for a private ID
    range, so FEStrings.csv is untouched. NAME_ID_BASE below has to match the
    wrapper.

  * `FEData/TrackArt/` gets three new files per track, which is pure addition.

A record is built by copying a stock one and patching it, the same approach the
CRP writer takes with materials: of the 1192 bytes only the name fields, the
ids and the type flags are understood, and inventing the rest would be worse
than inheriting it. monaco1 is the template, because it is a circuit - and so
is every High Stakes track (HS6).

The track map in `<dir>.fsh` is the loading overlay: a gold centre line on
straight alpha, a name strip, and the stock chequered flags. Size follows the
track bounding box, the same way the shipped coastal/alps/monaco files do.
"""

import math
import os
import struct

from PIL import Image, ImageDraw, ImageFont, ImageFilter

import ea_refpack as ea
from nfs5_fsh import write_frontend_fsh
import nfs5cfg

TRK_STRIDE = 0x4A8
TRK_COUNT_OFFSET = None          # no header; count is filesize / stride

OFF_TRACK_ID = 0
OFF_NAME_ID = 4
OFF_DIRECTORY = 8
OFF_BACKWARDS = 28
OFF_ENV = 48
OFF_SPLASH = 68
OFF_SPLASH_SMALL = 88
OFF_PROPS = 108

# The 16-bit field at +0x6C is the availability rule and the one at +0x6E marks
# a looped track. FUN_004FBC10 rebuilds the two frontend lists on every screen
# change and applies them:
#
#   [0x6C] == 3   the track is unlocked by a tournament: the rebuild scans the
#                 tournament table (00691E64) and keeps the record only if an
#                 unlocked tournament lists this track id. Stock Forest, alps,
#                 AutoBahn, castle, industrial, canyon and monaco1/2/4/5 use it.
#   [0x6C] == 0   always available - coastal, foothills, farmland, monaco3.
#   [0x6E] == 1   looped: the record also goes into LoopedTrack.Track.
#
# Copying monaco1 wholesale gave the new tracks rule 3, so they were built into
# the lists at startup and then pruned again the moment a menu screen rebuilt
# them - which is exactly what "the new tracks are not in the list" looked like.
# Converted tracks belong to no tournament, so they get rule 0.
AVAILABILITY_ALWAYS = 0
LOOPED_FLAG = 1

# FEString ids for the new names. Must match the wrapper's GetString hook.
NAME_ID_BASE = 0x2000

# Track ids the stock roster leaves unused (T007), then upward from 35.
FREE_TRACK_IDS = [15, 17, 19, 20, 21, 22, 23, 24, 27, 28, 31, 33] + list(range(35, 64))

ART_SPLASH = (256, 192)

# Gold from coastal.fsh solid texels: (255,187,0) core. Highlight is grey
# on the light side; the dark side is a black drop-shadow with falling alpha.
LINE_CORE = (255, 187, 0, 255)
LINE_WHITE = (220, 220, 220, 255)

SCREEN_W = 640
MAP_BOTTOM = 311
LABEL_Y = 306

# ~0.22 px/m matches Hometown 1286 m -> ~280 px and coastal-sized circuits.
MAP_M_PER_PX = 0.22
MAP_LONG_MIN, MAP_LONG_MAX = 240, 450
MAP_SHORT_MAX = 220
SMALL_LONG = 140

STOCK_MAP = os.path.join(nfs5cfg.GAME, 'FEData', 'TrackArt', 'coastal.fsh')

# Names as High Stakes itself shows them. Source: Data/TEXT/TEXT.MET (track
# name strings "Celtic Ruins" .. "Empire City") read in the order of the track
# folder table in NFSHS.EXE @0x1969D3 (Hills, Germany, Coastal, Park, France,
# Uk, Snowy, Gt1, Gt2, Gt3, Hometown, ...) - the two lists are 1:1.
HS_TRACK_NAMES = {
    'Hills': 'Celtic Ruins',
    'Germany': 'Landstrasse',
    'Coastal': 'Dolphin Cove',
    'Park': 'Kindiak Park',
    'France': 'Route Adonf',
    'Uk': 'Durham Road',
    'Snowy': 'Snowy Ridge',
    'Gt1': 'Raceway',
    'Gt2': 'Raceway 2',
    'Gt3': 'Raceway 3',
    'Hometown': 'Hometown',
    'Redrock': 'Redrock Ridge',
    'Atlantic': 'Atlantica',
    'Rockypas': 'Rocky Pass',
    'Country': 'Country Woods',
    'Lostcany': 'Lost Canyons',
    'Aquatica': 'Aquatica',
    'Summit': 'Summit',
    'Empire': 'Empire City',
}


# ------------------------------------------------------------------- .trk ---

def read_trk(path):
    data = open(path, 'rb').read()
    if len(data) % TRK_STRIDE:
        raise ValueError('%s: %d bytes is not a whole number of records'
                         % (path, len(data)))
    return [data[i:i + TRK_STRIDE] for i in range(0, len(data), TRK_STRIDE)]


def _put_string(record, offset, text, size=20):
    field = text.encode('latin1')[:size - 1].ljust(size, b'\0')
    record[offset:offset + size] = field


def build_trk_record(template, track_id, name_id, directory, env, splash,
                     splash_small, circuit=True):
    r = bytearray(template)
    struct.pack_into('<I', r, OFF_TRACK_ID, track_id)
    struct.pack_into('<I', r, OFF_NAME_ID, name_id)
    _put_string(r, OFF_DIRECTORY, directory)
    _put_string(r, OFF_BACKWARDS, 'bk')
    _put_string(r, OFF_ENV, env)
    _put_string(r, OFF_SPLASH, splash)
    _put_string(r, OFF_SPLASH_SMALL, splash_small)
    struct.pack_into('<HH', r, OFF_PROPS, AVAILABILITY_ALWAYS,
                     LOOPED_FLAG if circuit else 0)
    return bytes(r)


def build_ext_trk(stock_path, tracks, template_index=8):
    """stock records + one per converted track. `tracks` is a list of dicts with
    name/directory/circuit. Returns (blob, [(name, track_id, name_id)])."""
    records = read_trk(stock_path)
    used = {struct.unpack_from('<I', r, OFF_TRACK_ID)[0] for r in records}
    template = records[template_index]
    free = [i for i in FREE_TRACK_IDS if i not in used]
    if len(free) < len(tracks):
        raise ValueError('only %d free track ids for %d tracks'
                         % (len(free), len(tracks)))

    added = []
    for i, t in enumerate(tracks):
        d = t['directory']
        records.append(build_trk_record(
            template, free[i], NAME_ID_BASE + i, d, d.capitalize(),
            d + 'Sp', d + 'SpSm', circuit=t.get('circuit', True)))
        added.append((t['name'], free[i], NAME_ID_BASE + i))
    return b''.join(records), added


# -------------------------------------------------------------- track art ---

def track_display_name(name):
    """Menu name of an HS track folder (any case); unknown folders keep theirs."""
    for key, title in HS_TRACK_NAMES.items():
        if key.lower() == name.lower():
            return title
    return name.replace('_', ' ')


def track_title(name):
    """Loading-screen label: the menu name in capitals, as PU's own art."""
    return track_display_name(name).upper()


def _vroad_xz(track):
    pts = [track.vroad(i)['ref'] for i in range(track.n_vroad)]
    xs = [p[0] for p in pts]
    zs = [p[2] for p in pts]
    return pts, min(xs), max(xs), min(zs), max(zs)


def _is_closed(pts, gap=20.0):
    if len(pts) < 2:
        return False
    dx = pts[0][0] - pts[-1][0]
    dz = pts[0][2] - pts[-1][2]
    return math.hypot(dx, dz) < gap


def _canvas_size(span_x, span_z, long_side, short_max):
    if span_x >= span_z:
        w = long_side
        h = max(64, min(short_max, int(round(long_side * span_z / span_x))))
    else:
        h = min(short_max, long_side)
        w = max(64, min(MAP_LONG_MAX, int(round(h * span_x / span_z))))
    return w, h


def _map_size(span_x, span_z):
    long_m = max(span_x, span_z)
    long_px = int(round(long_m * MAP_M_PER_PX))
    long_px = max(MAP_LONG_MIN, min(MAP_LONG_MAX, long_px))
    return _canvas_size(span_x, span_z, long_px, MAP_SHORT_MAX)


def _project(pts, min_x, min_z, span_x, span_z, w, h, margin):
    scale = min((w - 2 * margin) / span_x, (h - 2 * margin) / span_z)
    ox = (w - span_x * scale) / 2 - min_x * scale
    oy = (h - span_z * scale) / 2 - min_z * scale
    screen = []
    for p in pts:
        x = p[0] * scale + ox
        y = h - (p[2] * scale + oy)
        screen.append((x, y))
    return screen


def _rgba_to_bgra(img):
    r, g, b, a = img.split()
    return bytearray(Image.merge('RGBA', (b, g, r, a)).tobytes())


def _stroke(draw, xy, colour, width):
    draw.line(xy, fill=colour, width=width, joint='curve')
    rad = max(1, width // 2)
    x0, y0 = xy[0]
    draw.ellipse((x0 - rad, y0 - rad, x0 + rad, y0 + rad), fill=colour)
    x1, y1 = xy[-1]
    draw.ellipse((x1 - rad, y1 - rad, x1 + rad, y1 + rad), fill=colour)


def _polyline(track, w, h):
    pts, min_x, max_x, min_z, max_z = _vroad_xz(track)
    span_x = (max_x - min_x) or 1.0
    span_z = (max_z - min_z) or 1.0
    margin = max(8, int(round(min(w, h) * 0.06)))
    screen = _project(pts, min_x, min_z, span_x, span_z, w, h, margin)
    xy = list(screen)
    if _is_closed(pts) and len(xy) > 2:
        xy = xy + [xy[0]]
    return pts, screen, xy


def draw_bevel_line(track, w, h, ss=4):
    """Stock loading stroke: 3px gold core + black drop shadow.

    coastal.fsh only uses two hues — gold (15,11,0) and black with falling
    alpha. A grey highlight looked like a dirty extra palette entry in-game.
    """
    _pts, screen_1x, xy1 = _polyline(track, w, h)
    sw, sh = w * ss, h * ss
    xy = [(x * ss, y * ss) for x, y in xy1]

    shadow = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
    core = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
    _stroke(ImageDraw.Draw(shadow),
            [(x + 3 * ss, y + 3 * ss) for x, y in xy], (0, 0, 0, 255), 7 * ss)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=ss * 0.35))
    _stroke(ImageDraw.Draw(core), xy, LINE_CORE, 3 * ss)

    img = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
    img.alpha_composite(shadow)
    img.alpha_composite(core)
    img = img.resize((w, h), Image.Resampling.BOX)
    return _snap_gold_black(_rgba_to_bgra(img), w, h), screen_1x


def _snap_gold_black(bgra, w, h):
    """Keep only the two hues the original maps use: gold and black.

    Anything low-chroma (grey highlight, dirty AA) becomes black with the
    same alpha — that is the coastal shadow. Anything with colour becomes
    gold (15,11,0) with the same alpha.
    """
    gold_b, gold_g, gold_r = 0, 187, 255
    out = bytearray(bgra)
    n = w * h
    for i in range(n):
        o = i * 4
        b, g, r, a = out[o], out[o + 1], out[o + 2], out[o + 3]
        if a == 0:
            out[o:o + 4] = b'\0\0\0\0'
            continue
        if max(r, g, b) - min(r, g, b) < 40:
            out[o:o + 4] = bytes((0, 0, 0, a))
        else:
            out[o:o + 4] = bytes((gold_b, gold_g, gold_r, a))
    return out


def draw_flat_line(track, w, h, colour=LINE_WHITE, width=3, ss=4):
    """Small-map / splash line: one colour, still on straight alpha."""
    _pts, _screen, xy1 = _polyline(track, w, h)
    sw, sh = w * ss, h * ss
    xy = [(x * ss, y * ss) for x, y in xy1]
    img = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
    _stroke(ImageDraw.Draw(img), xy, colour, width * ss)
    img = img.resize((w, h), Image.Resampling.BOX)
    return _rgba_to_bgra(img)


def draw_loading_map(track):
    pts, min_x, max_x, min_z, max_z = _vroad_xz(track)
    span_x = (max_x - min_x) or 1.0
    span_z = (max_z - min_z) or 1.0
    w, h = _map_size(span_x, span_z)
    bgra, screen = draw_bevel_line(track, w, h)
    return w, h, bgra, screen, _is_closed(pts)


def _font(size):
    for path in (r'C:\Windows\Fonts\arialbd.ttf',
                 r'C:\Windows\Fonts\arial.ttf',
                 r'C:\Windows\Fonts\tahoma.ttf'):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_name_label(text):
    """Gold caps strip, transparent background, 13 px tall like coastal."""
    text = (text or '').upper()
    font = _font(11)
    probe = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    box = probe.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    w = max(24, tw + 8)
    h = 13
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = (w - tw) // 2 - box[0]
    y = (h - th) // 2 - box[1]
    draw.text((x, y), text, font=font, fill=LINE_CORE)
    return w, h, _rgba_to_bgra(img)


def _decode_argb4444_bgra(data, entry):
    w, h = entry['w'], entry['h']
    o = entry['off'] + 16
    out = bytearray(w * h * 4)
    for i in range(w * h):
        c = data[o + i * 2] | (data[o + i * 2 + 1] << 8)
        a = (c >> 12) & 0xF
        r = (c >> 8) & 0xF
        g = (c >> 4) & 0xF
        b = c & 0xF
        out[i * 4:i * 4 + 4] = bytes((b * 17, g * 17, r * 17, a * 17))
    return w, h, out


_STOCK_FLAG = None


def stock_flag():
    """16x16 chequered flag from the shipped coastal.fsh Fini entry."""
    global _STOCK_FLAG
    if _STOCK_FLAG is not None:
        return _STOCK_FLAG
    path = os.path.normpath(STOCK_MAP)
    data = ea.load_maybe_qfs(path)
    _head, entries = ea.fsh_dir(data)
    flag = None
    for e in entries:
        if e['tag'].rstrip() in ('Fini', 'S', 'F', 'star', 'fin') and e['w'] == 16:
            flag = _decode_argb4444_bgra(data, e)
            break
    if flag is None:
        raise RuntimeError('no 16x16 flag in %s' % path)
    _STOCK_FLAG = flag
    return flag


def _map_origin(w, h):
    """Stock maps sit on the 640x480 loading screen: centred in X, bottom
    edge at MAP_BOTTOM so the name strip at LABEL_Y sits just under the line.
    coastal 257x184 -> (193, 127); alps 414x132 -> (115, 177)."""
    x = (SCREEN_W - w) // 2
    y = MAP_BOTTOM - h
    if y < 40:
        y = 40
    return x, y


def _along(screen, index, step=12.0):
    """A point a few pixels along the polyline, for the flag's extra pair."""
    n = len(screen)
    x0, y0 = screen[index]
    nxt = screen[(index + 1) % n] if n > 1 else (x0 + step, y0)
    dx, dy = nxt[0] - x0, nxt[1] - y0
    length = math.hypot(dx, dy) or 1.0
    return int(round(x0 + dx / length * step)), int(round(y0 + dy / length * step))


def build_track_art(directory, track, display_name=None):
    """The three FEData/TrackArt files, as ARGB4444 with blit origins filled in.

    Zeroed (x,y) in the FSH header is why Hometown sat in the top-left: the
    game blits the map at that origin. Stock files store screen coordinates.
    """
    tag = directory[:4].ljust(4)
    title = display_name or track_title(directory)
    fw, fh, flag = stock_flag()

    mw, mh, map_img, screen, closed = draw_loading_map(track)
    mx, my = _map_origin(mw, mh)
    lw, lh, label = draw_name_label(title)
    lx = (SCREEN_W - lw) // 2

    sx, sy = screen[0]
    start_x = int(round(mx + sx - fw / 2.0))
    start_y = int(round(my + sy - fh / 2.0))
    start_u, start_v = _along(screen, 0)
    start_u, start_v = int(round(mx + start_u)), int(round(my + start_v))

    if closed or len(screen) < 2:
        fin_x, fin_y, fin_u, fin_v = start_x, start_y, start_u, start_v
    else:
        fx, fy = screen[-1]
        fin_x = int(round(mx + fx - fw / 2.0))
        fin_y = int(round(my + fy - fh / 2.0))
        fin_u, fin_v = _along(screen, len(screen) - 2)
        fin_u, fin_v = int(round(mx + fin_u)), int(round(my + fin_v))

    main = write_frontend_fsh([
        (tag, mw, mh, map_img, (mx, my, 0, 0)),
        (tag, lw, lh, label, (lx, LABEL_Y, 0, 230)),
        ('Fini', fw, fh, bytearray(flag), (start_x, start_y, start_u, start_v)),
        ('Fini', fw, fh, bytearray(flag), (fin_x, fin_y, fin_u, fin_v)),
    ])

    splash_img = draw_bevel_line(track, *ART_SPLASH)[0]
    splash = write_frontend_fsh(
        [(tag, ART_SPLASH[0], ART_SPLASH[1], bytearray(splash_img), (0, 0, 0, 0))
         for _ in range(5)]
        + [('star', fw, fh, bytearray(flag), (0, 0, 0, 0)),
           ('fin ', fw, fh, bytearray(flag), (0, 0, 0, 0))])

    pts, min_x, max_x, min_z, max_z = _vroad_xz(track)
    span_x = (max_x - min_x) or 1.0
    span_z = (max_z - min_z) or 1.0
    sw, sh = _canvas_size(span_x, span_z, SMALL_LONG, SMALL_LONG)
    small_img = draw_flat_line(track, sw, sh)
    small = write_frontend_fsh([
        (tag, sw, sh, small_img, (0, 0, 0, 0)),
        ('s   ', fw, fh, bytearray(flag), (0, 0, 0, 0)),
        ('f   ', fw, fh, bytearray(flag), (0, 0, 0, 0)),
    ])
    return {'%s.fsh' % directory: main,
            '%sSp.fsh' % directory: splash,
            '%sSpSm.fsh' % directory: small}


# ---------------------------------------------------------------- manifest ---

def write_name_table(path, added):
    """Base ids only, consecutive from NAME_ID_BASE. The wrapper answers
    nameId / +0x14 / +0x28 / +0x3C from the four columns of the row, the same
    layout as a stock FEStrings.csv track (display / CAPS / short / SHORT CAPS)."""
    lines = ['# id\tname\tcaps\tshort\tshort caps'
             '   -- generated by HS8, consumed by the extended_content hook']
    for name, _track_id, name_id in added:
        lines.append('%d\t%s\t%s\t%s\t%s'
                     % (name_id, name, name.upper(), name, name.upper()))
    with open(path, 'w', encoding='latin1') as f:
        f.write('\n'.join(lines) + '\n')
