"""HS7 acceptance: sky, reflections, tints, ambient audio and police spawns.

Checks each generated file against the shape PU's own tracks have, and checks
the cross-references that would otherwise fail silently: `.map` must name
textures that exist in the `.env` it ships with, and `Sky/<Name>.txt` must name
layers that exist in `Sky/<Name>.fsh`.

    python test_hs7_environment.py [track name ...]
"""

import os
import re
import struct
import sys

import ea_refpack as ea
import nfs4_frd as frd
import nfs5_environment as env
import nfs5_fsh as fsh
import nfs5_sky_bin as skybin
import track_atmosphere as atmosphere
import nfs5cfg

HS = nfs5cfg.HS_TRACKS
BUILD = os.path.join(nfs5cfg.WORK, 'build')
STOCK_SKY_BINS = [os.path.join(atmosphere.SKY_DIR, n) for n in
                  ('Alps.bin', 'Coastal.bin')]
ATMOSPHERE_DIRECTORIES = {'coastal': 'dolphin'}   # as convert_track.TRACK_DIRECTORIES

SKY_TAGS = {'st1a': (16, 16), 'st1b': (16, 16), 'sc1a': (16, 16), 'sc1b': (16, 16),
            'cm1a': (32, 32), 'cc1a': (32, 32), 'hzcl': (16, 2), 'horz': (256, 256)}


def check_env(path, problems):
    d = open(path, 'rb').read()
    head, entries = ea.fsh_dir(d)
    tags = [e['tag'] for e in entries]
    if set(tags) != set(env.ENV_FACES):
        problems.append('env: faces %r' % tags)
    ordered = sorted(entries, key=lambda e: e['off'])
    for i, e in enumerate(ordered):
        nxt = ordered[i + 1]['off'] if i + 1 < len(ordered) else len(d)
        if (e['w'], e['h']) != (env.ENV_SIZE, env.ENV_SIZE):
            problems.append('env: %s %dx%d' % (e['tag'], e['w'], e['h']))
        if e['code'] != fsh.CODE_ARGB8888:
            problems.append('env: %s code %#04x' % (e['tag'], e['code']))
        # PU's own .env has one level per face, no mip chain
        want = (16 + e['w'] * e['h'] * 4 + 15) & ~15
        if nxt - e['off'] != want:
            problems.append('env: %s block %d instead of %d' % (e['tag'], nxt - e['off'], want))
    return {e['tag'] for e in entries}


def check_sky_fsh(path, problems, has_panorama):
    d = open(path, 'rb').read()
    _head, entries = ea.fsh_dir(d)
    got = {e['tag']: (e['w'], e['h']) for e in entries}
    if got != SKY_TAGS:
        problems.append('sky.fsh: %r' % sorted(got.items()))
    horz = next((e for e in entries if e['tag'] == 'horz'), None)
    if horz and has_panorama:
        o = horz['off'] + 16

        def row_alpha(y):
            return sum(d[o + (y * 256 + x) * 4 + 3] for x in range(256)) // 256

        # Each 128-row half holds four whole HDC panels; their bottom rows
        # are the opaque terrain foot. Fail only if that foot is a hole.
        bottom = sum(row_alpha(y) for y in range(112, 128)) // 16
        if bottom < 200:
            problems.append('sky.fsh: horz bottom is transparent (alpha %d)' % bottom)
    sc = next((e for e in entries if e['tag'] == 'sc1a'), None)
    if sc:
        o = sc['off'] + 16
        opaque = sum(1 for i in range(sc['w'] * sc['h'])
                     if d[o + i * 4 + 3] > 240)
        if opaque == sc['w'] * sc['h']:
            problems.append('sky.fsh: sc1a fully opaque')
    return set(got)


def check_map(path, env_tags, problems):
    text = open(path, 'r', encoding='latin1').read()
    if not re.search(r'^\[header\]', text, re.M):
        problems.append('map: no [header]')
    count = re.search(r'^count=(\d+)', text, re.M)
    sections = len(re.findall(r'^\[section\d+\]', text, re.M))
    if not count or int(count.group(1)) != sections:
        problems.append('map: count=%s vs %d sections'
                        % (count.group(1) if count else '?', sections))
    for m in re.finditer(r'^texture\d(?:\.\w+)?=(\d+)', text, re.M):
        tag = struct.pack('<I', int(m.group(1))).decode('latin1')
        if tag not in env_tags:
            problems.append('map: reference to %r, missing from the .env' % tag)
            break
    for key in ('ambient', 'tint', 'interior', 'lightdecal'):
        if not re.search(r'^%s=' % key, text, re.M):
            problems.append('map: no %s' % key)


def check_sky_txt(path, sky_tags, problems):
    text = open(path, 'r', encoding='latin1').read()
    if not text.startswith('BEGIN') or 'END' not in text:
        problems.append('sky.txt: no BEGIN/END')
    named = set(re.findall(r'^\t([a-z]{2}\d[ab])$', text, re.M))
    missing = named - sky_tags
    if missing:
        problems.append('sky.txt: layers %r missing from the .fsh' % sorted(missing))
    if not named:
        problems.append('sky.txt: no layer named')


def check_audio(path, track, count, problems):
    text = open(path, 'r', encoding='latin1').read()
    elements = re.findall(r'AUDIO_ELEMENT 4\r?\n([-\d.]+) ([-\d.]+) ([-\d.]+)', text)
    if len(elements) != count:
        problems.append('audio: %d elements instead of %d' % (len(elements), count))
    lo = [min(track.vroad(i)['ref'][k] for i in range(track.n_vroad)) for k in range(3)]
    hi = [max(track.vroad(i)['ref'][k] for i in range(track.n_vroad)) for k in range(3)]
    for e in elements:
        p = [float(v) for v in e]
        if not all(lo[k] - 300 <= p[k] <= hi[k] + 300 for k in range(3)):
            problems.append('audio: source %r outside the track' % (p,))
            break


def check_sky_bin(path, atmo, source, n_slices, problems):
    blob = open(path, 'rb').read()
    try:
        sky = skybin.SkyBin(blob)
    except ValueError as e:
        problems.append('sky.bin: %s' % e)
        return
    for stock in STOCK_SKY_BINS:
        if blob == open(stock, 'rb').read():
            problems.append('sky.bin: byte for byte another track\'s %s' % os.path.basename(stock))
    if not 1 <= len(sky.weather) <= skybin.MAX_KEYS - 1:
        problems.append('sky.bin: %d weather keys' % len(sky.weather))
    if sky.weather[0].slice != 0 or sky.weather[-1].slice != n_slices - 1:
        problems.append('sky.bin: the keys do not cover the track 0..%d' % (n_slices - 1))
    if len(sky.weather) != len(atmo.keys) or len(sky.curve_region) != len(atmo.keys):
        problems.append('sky.bin: key count differs from the ini')
    if any(k.slice >= n_slices for k in sky.weather):
        problems.append('sky.bin: slice outside the track')
    ini = os.path.join(atmosphere.ASSETS_DIR, '%s.ini' % atmo.name)
    if not os.path.exists(ini):
        problems.append('missing %s' % ini)


def check_cop(path, n_slices, problems):
    d = open(path, 'rb').read()
    n = struct.unpack_from('<I', d, 0)[0]
    if len(d) != 4 + n * 20:
        problems.append('cop: size %d for %d points' % (len(d), n))
        return
    for i in range(n):
        enabled, slice_idx = struct.unpack_from('<2I', d, 4 + i * 20)
        if enabled != 1 or slice_idx >= n_slices:
            problems.append('cop: point %d slice %d' % (i, slice_idx))
            break


def check(name):
    src = os.path.join(HS, name)
    out = os.path.join(BUILD, 'hs7', name.lower())
    os.makedirs(out, exist_ok=True)
    track = frd.read(os.path.join(src, 'tr.frd'))
    sky = ea.load_maybe_qfs(os.path.join(src, 'sky.qfs'))
    _h, entries = ea.fsh_dir(sky)
    by = {e['tag']: e for e in entries}
    directory = ATMOSPHERE_DIRECTORIES.get(name.lower(), name.lower())
    atmo, source = atmosphere.atmosphere_for(directory, src, track.n_vroad)
    result = env.convert(track, name.lower(), src, out, by,
                         lambda e: fsh.decode_entry(sky, e),
                         atmosphere.sky_bin_for(atmo))

    lower, cap = name.lower(), name.lower().capitalize()
    problems = []
    env_tags = check_env(os.path.join(out, '%s.env' % lower), problems)
    drawn = 'HDC0' in by and env.horizon_band(env.read_ini(os.path.join(src, 'tr.ini')))
    sky_tags = check_sky_fsh(os.path.join(out, 'Sky', '%s.fsh' % cap), problems,
                             bool(drawn))
    check_map(os.path.join(out, '%s.map' % lower), env_tags, problems)
    check_sky_txt(os.path.join(out, 'Sky', '%s.txt' % cap), sky_tags, problems)
    # Ambient audio moved to HS13/HS14 (`convert(..., ambient=...)`); this
    # test calls convert without a builder, so no _audio.scn is produced.
    sounds = result.get('audio', {}).get('elements', 0)
    if sounds:
        check_audio(os.path.join(out, '%s_audio.scn' % lower), track, sounds, problems)
    check_cop(os.path.join(out, 'Cops', '%s.cop' % lower), track.n_vroad, problems)
    check_sky_bin(os.path.join(out, 'Sky', '%s.bin' % cap), atmo, source,
                  track.n_vroad, problems)

    print('%-10s sounds %2d  cops %2d  horizon %-3s sky %6d B  env %6d B  atmosphere %s/%d  %s'
          % (name, sounds,
             struct.unpack_from('<I', open(os.path.join(out, 'Cops', '%s.cop' % lower),
                                           'rb').read(), 0)[0],
             'yes' if 'HDC0' in by else 'no',
             result['files']['Sky/%s.fsh' % cap], result['files']['%s.env' % lower],
             source, len(atmo.keys),
             'ok' if not problems else '; '.join(problems[:2])))
    return problems


if __name__ == '__main__':
    names = sys.argv[1:] or sorted(os.listdir(HS))
    fails = []
    for n in names:
        fails += check(n)
    print('\n' + ('ALL PASSED' if not fails else 'FAILURES'))
    sys.exit(1 if fails else 0)
