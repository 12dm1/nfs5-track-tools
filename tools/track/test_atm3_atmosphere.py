"""ATM3 acceptance: `Sky/*.bin` writer, `Atmosphere` model, `atmosphere.ini`.

No game, no 3D -- pure data checks against ATM2 §10:

  * `write(read(blob), blob) == blob` for all 20 stock tracks;
  * `model -> ini -> model` matches field by field (at float32 precision --
    that is the format's native precision, and the only precision `%.9g`
    round-trips exactly, see `track_atmosphere._f32`);
  * alps: `fog_at`/`region_blend_at` survive a full `bin -> model -> bin`
    round trip within 1/255 colour and 1e-6 density, on every slice.

Also dumps the 20 stock presets to `data/atmosphere/presets/` (the folder
is in git; the files are rewritten only when they change).

    python test_atm3_atmosphere.py
"""

import os
import struct
import sys
import tempfile

import nfs5_sky_bin as skybin
import track_atmosphere as atm

ALPS_SLICES = 2105  # ATM1: checked on slices 0..2104
REPORT_DIR = os.path.join(atm.nfs5cfg.WORK, 'reports', 'ATM3')


def _f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]


def check_bin_roundtrip(problems):
    for track in atm.STOCK_TRACKS:
        path = atm.sky_bin_path(track)
        blob = open(path, 'rb').read()
        sky = skybin.SkyBin(blob)
        out = skybin.write(sky, blob)
        if out == blob:
            continue
        off = next(i for i in range(len(blob)) if out[i] != blob[i])
        problems.append('%s: write(read(blob), blob) != blob, first difference 0x%X'
                         % (track, off))
    print('bin round trip: %d/%d tracks byte for byte' %
          (len(atm.STOCK_TRACKS) - sum(1 for p in problems if 'write(read' in p),
           len(atm.STOCK_TRACKS)))


def _dome_equal(a, b):
    floats = ('dome_radius', 'dome_curvature', 'dome_height', 'angle', 'scale',
              'sun_x', 'sun_y', 'horizon_bottom', 'horizon_top', 'water_angle',
              'water_dome_radius', 'water_dome_curvature', 'water_dome_height')
    ints = ('lens_flare', 'horizon_rows', 'horizon_rotation')
    return (all(_f32(getattr(a, f)) == _f32(getattr(b, f)) for f in floats)
            and all(getattr(a, f) == getattr(b, f) for f in ints)
            and a.horizon_slots == b.horizon_slots)


def _colour_equal(a, b):
    return (a.follow_fog == b.follow_fog and a.colour_a == b.colour_a
            and a.colour_b == b.colour_b and a.water_colour_a == b.water_colour_a
            and a.water_colour_b == b.water_colour_b)


def _key_equal(a, b):
    scalars = ('fog_density', 'fog_density_b', 'rain', 'snow', 'storm', 'region')
    return (a.slice == b.slice and a.fog_colour == b.fog_colour
            and a.fog_colour_b == b.fog_colour_b
            and all(_f32(getattr(a, f)) == _f32(getattr(b, f)) for f in scalars)
            and tuple(_f32(v) for v in a.light_tint) == tuple(_f32(v) for v in b.light_tint))


def check_ini_roundtrip(problems, tmp_dir):
    for track in ('alps', 'coastal', 'monaco1'):
        sky = skybin.read(atm.sky_bin_path(track))
        model1 = atm.from_sky_bin(sky, name=track, slices=0)
        ini_path = os.path.join(tmp_dir, '%s.ini' % track)
        atm.save_ini(model1, ini_path)
        model2 = atm.load_ini(ini_path)

        if model1.name != model2.name:
            problems.append('%s: ini [track].name differs' % track)
        if not _dome_equal(model1.sky, model2.sky):
            problems.append('%s: ini [sky] differs field by field' % track)
        if not _colour_equal(model1.sky_colour, model2.sky_colour):
            problems.append('%s: ini [sky_colour] differs field by field' % track)
        if len(model1.keys) != len(model2.keys):
            problems.append('%s: ini keys %d != %d after the round trip'
                             % (track, len(model1.keys), len(model2.keys)))
        else:
            bad = next((k1.slice for k1, k2 in zip(model1.keys, model2.keys)
                       if not _key_equal(k1, k2)), None)
            if bad is not None:
                problems.append('%s: [key:%d] differs field by field' % (track, bad))
        print('ini round trip  %-10s %3d keys' % (track, len(model1.keys)))


def check_alps_fidelity(problems):
    template = open(atm.sky_bin_path('alps'), 'rb').read()
    original = skybin.SkyBin(template)
    model = atm.from_sky_bin(original, name='alps')
    roundtrip = skybin.SkyBin(atm.to_sky_bin(model, template))

    max_colour = 0
    max_density = 0.0
    max_region = 0.0
    for slc in range(ALPS_SLICES):
        c1, d1 = original.fog_at(slc)
        c2, d2 = roundtrip.fog_at(slc)
        max_colour = max(max_colour, max(abs(c1[i] - c2[i]) for i in range(3)))
        max_density = max(max_density, abs(d1 - d2))
        max_region = max(max_region, abs(original.region_blend_at(slc)
                                         - roundtrip.region_blend_at(slc)))

    print('alps fidelity    colour %d/255  density %.2e  region %.2e'
          % (max_colour, max_density, max_region))
    if max_colour > 1:
        problems.append('alps: fog_at colour off by %d/255 (limit 1)' % max_colour)
    if max_density > 1e-6:
        problems.append('alps: fog_at density off by %.2e (limit 1e-6)' % max_density)
    if max_region > 1e-6:
        problems.append('alps: region_blend_at off by %.2e (limit 1e-6)' % max_region)


def check_presets(problems):
    n = atm.dump_presets(verbose=False)
    found = [t for t in atm.STOCK_TRACKS
            if os.path.exists(os.path.join(atm.PRESETS_DIR, '%s.ini' % t))]
    print('presets          %d/%d files in %s' % (len(found), n, atm.PRESETS_DIR))
    if len(found) != len(atm.STOCK_TRACKS):
        missing = sorted(set(atm.STOCK_TRACKS) - set(found))
        problems.append('presets: files missing for %s' % ', '.join(missing))


def check_tint(problems):
    """ATM9: df' = clamp(round(df * tint)) per channel, tint interpolated by slice."""
    if atm.tint_colour(0x80FF8040, (0.4, 2.0, 1.0)) != 0x8066FF40:
        problems.append('tint_colour: %#x' % atm.tint_colour(0x80FF8040, (0.4, 2.0, 1.0)))
    model = atm.from_sky_bin(skybin.read(atm.sky_bin_path('alps')), name='alps')
    model.keys[0].light_tint = (0.5, 1.0, 1.0)
    model.keys[1].light_tint = (1.5, 1.0, 1.0)
    a, b = model.keys[0].slice, model.keys[1].slice
    mid = atm.tint_at(model, (a + b) // 2)[0] if (a + b) % 2 == 0 else 1.0
    if (a + b) % 2 == 0 and abs(mid - 1.0) > 1e-9:
        problems.append('tint_at: midpoint between keys %.3f instead of 1.0' % mid)
    if atm.tint_at(model, -5)[0] != 0.5 or atm.tint_at(model, 10 ** 6)[0] != model.keys[-1].light_tint[0]:
        problems.append('tint_at: not flat outside the keys')
    print('tint             ok')


def write_report(problems):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('# ATM3: atmosphere model, writer, presets\n\n')
        f.write('Checks from ATM2 section 10, without the game. Status: %s.\n\n'
               % ('ALL PASSED' if not problems else 'FAILURES'))
        f.write('* `nfs5_sky_bin.write()`: the `Sky/*.bin` writer, template + patch; '
               'the table terminators keep the template\'s byte pattern.\n')
        f.write('* `track_atmosphere.py`: the `Atmosphere` model, `atmosphere.ini`, '
               '`rescale()`.\n')
        f.write('* Presets of the 20 stock tracks: `data/atmosphere/presets/`.\n')
        if problems:
            f.write('\n## Failures\n\n')
            for p in problems:
                f.write('* %s\n' % p)


if __name__ == '__main__':
    problems = []
    check_bin_roundtrip(problems)
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_ini_roundtrip(problems, tmp_dir)
    check_alps_fidelity(problems)
    check_presets(problems)
    check_tint(problems)
    write_report(problems)

    print('\n' + ('ALL PASSED' if not problems else 'FAILURES'))
    if problems:
        for p in problems:
            print('  -', p)
    sys.exit(1 if problems else 0)
