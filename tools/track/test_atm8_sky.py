"""ATM8 acceptance (no GL): sky geometry and weather maths of the preview.

  * sun position = ATM1 formula, and it lies on the dome paraboloid;
  * dome mesh: rim at `dome_radius`, apex at `dome_height * scale`;
  * horizon slots: 16 panels, +256 mirrors, `horizon_rotation` shifts the ring;
  * alps snow: starts at slice 648, off by 1343, particle count follows the
    0..1 value linearly (the preview knob is the only free factor);
  * storm classes/rates per ATM1 (0.135 weak, 0.545 medium, >= 0.66 strong);
  * `ARGB4444` decoding of the render archives (SUNA/GLAR/FLR0/FLR1/spik,
    snow/rain) loads with the expected sizes.

    python test_atm8_sky.py
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'track_editor'))

import assets                      # noqa: E402
import nfs5_sky_bin as skybin      # noqa: E402
import skygeo                      # noqa: E402
import track_atmosphere as atm     # noqa: E402


def check_sun(problems):
    d = atm.from_sky_bin(skybin.read(atm.sky_bin_path('alps'))).sky
    x, y, z = skygeo.sun_position(d)
    a = d.angle * 2.0 * math.pi                   # the game's trig takes TURNS
    ex = (math.cos(a) * d.sun_x + math.sin(a) * d.sun_y) * d.scale
    ey = (d.dome_height - (d.sun_x ** 2 + d.sun_y ** 2) * d.dome_curvature) * d.scale
    ez = (math.cos(a) * d.sun_y - math.sin(a) * d.sun_x) * d.scale
    if max(abs(x - ex), abs(y - ey), abs(z - ez)) > 1e-4:
        problems.append('sun not at the ATM1 formula: %s vs %s' % ((x, y, z), (ex, ey, ez)))
    on_dome = d.dome_height - d.dome_curvature * (x * x + z * z) / d.scale ** 2
    if abs(y / d.scale - on_dome) > 1e-4:
        problems.append('the sun is not on the dome')
    print('alps sun: (%.1f, %.1f, %.1f) on the paraboloid, dome angle %.3f turn' % (x, y, z, d.angle))


def check_dome(problems):
    d = atm.from_sky_bin(skybin.read(atm.sky_bin_path('alps'))).sky
    v = skygeo.dome_grid(d)
    idx = skygeo.dome_indices()
    if v.shape != (256, 3) or len(idx) != 15 * 15 * 6 or idx.max() != 255:
        problems.append('the dome is not a 16x16 grid: %s, %d indices' % (v.shape, len(idx)))
    # side = dome_radius (FUN_00460ec0): every vertex within R/2 * scale along both plane axes
    half = d.dome_radius * 0.5 * d.scale
    corner = math.hypot(half, half)
    far = float(((v[:, 0] ** 2 + v[:, 2] ** 2) ** 0.5).max())
    if abs(far - corner) > 0.05:
        problems.append('grid corner at %.1f, expected %.1f' % (far, corner))
    top = float(v[:, 1].max())
    apex = d.dome_height * d.scale
    if not (apex - 0.5 * d.scale <= top <= apex):
        problems.append('dome top %.2f, expected just below %.2f' % (top, apex))
    uv = skygeo.dome_uv(2.0, 4.0)
    if tuple(uv[-1]) != (7.5, 3.75):
        problems.append('layer UV: %s, expected (7.5, 3.75)' % (tuple(uv[-1]),))
    print('dome: 16x16 grid, side %.0f, grid corner at %.1f, top y=%.1f' % (d.dome_radius, far, top))


def check_horizon(problems):
    d = atm.from_sky_bin(skybin.read(atm.sky_bin_path('coastal'))).sky
    d.horizon_slots = [0, 1, 2, 3, 4, 5, 6, 7, 256, 257, 258, 259, 260, 261, 262, 263]
    d.horizon_rotation, d.horizon_rows, d.horizon_angle = 0, 2, 0.25
    d.horizon_bottom, d.horizon_top = -100.0, 300.0
    q = skygeo.horizon_panels(d)
    if len(q) != 16:
        problems.append('%d horizon panels instead of 16' % len(q))
    # panel 5 = column 1, row 1 of a 4x2 atlas
    if q[5]['uv'][0] != (0.25, (1 + 0.01) / 2) or q[5]['uv'][2] != (0.5, (1 + 0.98) / 2):
        problems.append('panel 5: uv %s' % (q[5]['uv'],))
    if q[9]['uv'][0][0] != 0.5 or q[9]['uv'][1][0] != 0.25 or not q[9]['mirrored']:
        problems.append('mirrored panel 257: uv %s' % (q[9]['uv'],))
    ys = sorted({round(c[1], 3) for p in q for c in p['corners']})
    if ys != [-100.0, 200.0]:
        problems.append('strip height %s, expected bottom -100 and top -100+300' % ys)
    r = [math.hypot(c[0], c[2]) for p in q for c in p['corners']]
    if max(abs(v - 400.0) for v in r) > 1e-3:
        problems.append('ring radius is not 400')
    x0, _, z0 = q[0]['corners'][0]
    if abs(x0 - 400.0) > 1e-3 or abs(z0) > 1e-3:       # 0.25 turn: sin = 1, cos = 0
        problems.append('ring start angle: point 0 = (%.1f, %.1f)' % (x0, z0))
    d.horizon_rotation = 2
    if skygeo.horizon_panels(d)[0]['colour_index'] != (2, 3) or skygeo.horizon_panels(d)[0]['panel'] != 0:
        problems.append('horizon_rotation must shift only the hzcl colours')
    print('horizon: 16 panels, 4x2 atlas, mirror, ring r=400, top = bottom + height')


def check_layers(problems):
    import sky_layers
    path = assets.sky_fsh_path('industrial')
    lvl = sky_layers.load(path)
    if lvl is None or lvl.level != 'HIGH' or lvl.kind != 'multinormal' or not lvl.blocks_sun:
        problems.append('Industrial.txt: level parsed wrong')
        return
    got = [(l.texture, l.a.sky_map, l.a.x_scale, l.a.y_scale, l.a.wind_speed, l.a.wind_dir) for l in lvl.layers]
    want = [('cm1a', 'st1a', 2.0, 3.0, 7.0, 270.0), ('cm2a', 'sc1a', 4.0, 4.0, 3.0, 220.0)]
    if got != want:
        problems.append('Industrial.txt layers: %s' % got)
    basic = sky_layers.load(assets.sky_fsh_path('canyon'))
    if basic is None or [(l.texture, l.a.sky_map) for l in basic.layers] != [('flat', 'st1a')]:
        problems.append('Canyon.txt (basic) parsed wrong')
    n = 0
    for t in atm.STOCK_TRACKS:
        p = assets.sky_fsh_path(t)
        l = sky_layers.load(p) if p else None
        if l is None or not l.layers:
            problems.append('%s: no sky layers' % t)
        else:
            n += 1
    print('sky layers: industrial %s; tracks parsed %d/%d' % (got, n, len(atm.STOCK_TRACKS)))


def check_snow(problems):
    sky = skybin.read(atm.sky_bin_path('alps'))
    knob = 3000.0
    snow = lambda s: sky.precipitation_at(s)[1]
    counts = {s: skygeo.particle_count(snow(s), knob) for s in (400, 640, 648, 900, 1120, 1230, 1343, 1400)}
    print('alps snow (value -> particles at %g): %s' % (
        knob, ', '.join('%d: %.2f -> %d' % (s, snow(s), c) for s, c in counts.items())))
    if snow(648) < 0.99 or counts[648] != int(knob):
        problems.append('snow at 648 not full: %.3f' % snow(648))
    if snow(640) > 0.9:
        problems.append('snow at 640 already full (%.3f) -- does not start at 648' % snow(640))
    if snow(1343) > 1e-6 or counts[1343] != 0 or counts[1400] != 0:
        problems.append('snow did not end by 1343')
    if abs(snow(1230) - 0.51) > 0.01 or abs(counts[1230] - 0.51 * knob) > 0.02 * knob:
        problems.append('snow at 1230: %.3f, particle count %d' % (snow(1230), counts[1230]))
    if any(snow(s) <= 0 for s in (700, 900, 1120)):
        problems.append('snow disappears inside 648..1120')
    if skygeo.particle_count(0.5, 1000) * 2 != skygeo.particle_count(1.0, 1000):
        problems.append('particle count not linear in the value')


def check_storm(problems):
    cases = ((0.03, -1), (0.135, 0), (0.545, 1), (0.66, 2), (1.0, 2))
    for value, cls in cases:
        if skygeo.storm_class(value) != cls:
            problems.append('storm %.3f: class %d, expected %d' % (value, skygeo.storm_class(value), cls))
    dt = 1.0
    got = [skygeo.storm_chance(v, dt) for v in (0.1, 0.5, 0.9)]
    if [round(g, 4) for g in got] != [0.0333, 0.0667, 0.1]:
        problems.append('lightning chance %s, expected 0.0333 / 0.0667 / 0.1' % got)
    autobahn = skybin.read(atm.sky_bin_path('autobahn'))
    industrial = skybin.read(atm.sky_bin_path('industrial'))
    ca = max(skygeo.storm_class(autobahn.storm_at(s)) for s in range(0, 260, 8))
    ci = max(skygeo.storm_class(industrial.storm_at(s)) for s in range(0, 1090, 8))
    print('storm: limits/chances as ATM1; autobahn max class %d (ATM1: 0.135 = weak), industrial %d (0.545 = medium)' % (ca, ci))
    if ca != 0 or ci != 1:
        problems.append('stock storm classes: autobahn %d (expected 0), industrial %d (expected 1)' % (ca, ci))


def check_textures(problems):
    sky_tex, _path, common, particles = assets.load_sky_textures('alps')
    horizon = assets.horizon_texture(sky_tex)
    want = {'SUNA': (64, 64), 'GLAR': (64, 64), 'FLR0': (64, 64), 'FLR1': (64, 64), 'spik': (32, 32)}
    for tag, size in want.items():
        if tag not in common or common[tag][:2] != size or len(common[tag][2]) != size[0] * size[1] * 4:
            problems.append('common.fsh %s did not load as %s' % (tag, size))
    for tag in ('snow', 'rain'):
        if tag not in particles or particles[tag][:2] != (16, 16):
            problems.append('particle.fsh %s did not load' % tag)
    if horizon is None or horizon[:2] != (256, 256):
        problems.append('Alps.fsh: no 256x256 horz panorama')
    # ARGB4444 sanity: 4-bit channels expand to multiples of 17, with a graded glow
    px = common['FLR0'][2]
    reds = set(px[0::4])
    if len(reds) < 8 or any(v % 17 for v in reds):
        problems.append('FLR0: colours do not look like decoded ARGB4444 (%s)' % sorted(reds))
    print('textures: common %s, particles %s, horz %s' % (sorted(common), sorted(particles), horizon[:2]))


if __name__ == '__main__':
    problems = []
    check_sun(problems)
    check_dome(problems)
    check_horizon(problems)
    check_layers(problems)
    check_snow(problems)
    check_storm(problems)
    check_textures(problems)
    print('\n' + ('ALL PASSED' if not problems else 'FAILURES'))
    for p in problems:
        print('  -', p)
    sys.exit(1 if problems else 0)
