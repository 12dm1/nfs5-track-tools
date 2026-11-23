"""ATM7 acceptance: the editor's scenario without a window.

Open alps, load the coastal preset (slices carried over by fraction of
length), add/move/edit keys, save the ini, "restart" (a fresh editor over the
saved file) and check everything is where it was; also the `.bin` export and
the storm-class labels (ATM1 thresholds).

    python test_atm7_editor.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'track_editor'))

import editor as ed_mod            # noqa: E402
import nfs5_sky_bin as skybin      # noqa: E402
import track_atmosphere as atm     # noqa: E402

N_SLICES = 1800     # alps


def keys_equal(a, b):
    return (len(a) == len(b) and all(
        x.slice == y.slice and x.fog_colour == y.fog_colour
        and abs(x.fog_density - y.fog_density) < 1e-6 and abs(x.snow - y.snow) < 1e-6
        and abs(x.storm - y.storm) < 1e-6 and abs(x.region - y.region) < 1e-6
        and tuple(x.light_tint) == tuple(y.light_tint) for x, y in zip(a, b)))


def check_scenario(problems, tmp):
    ini = os.path.join(tmp, 'alps_edit.ini')
    ed = ed_mod.open_editor('alps', N_SLICES)
    ed.ini_path = ini
    n0 = len(ed.atmo.keys)

    ed.load_preset('coastal')
    coastal = atm.load_ini(os.path.join(atm.PRESETS_DIR, 'coastal.ini'))
    if not ed.atmo.keys or ed.atmo.keys[-1].slice > N_SLICES - 1:
        problems.append('preset: keys outside the track')
    expect_last = min(N_SLICES - 1, round(coastal.keys[-1].slice / coastal.slices * N_SLICES))
    if ed.atmo.keys[-1].slice != expect_last:
        problems.append('preset: last key %d, expected %d' % (ed.atmo.keys[-1].slice, expect_last))

    n1 = len(ed.atmo.keys)
    if not ed.add_key(700):
        problems.append('add_key(700) refused')
    added = ed.atmo.keys[ed.selected]
    if added.slice != 700 or len(ed.atmo.keys) != n1 + 1:
        problems.append('add_key: the key is not at slice 700')
    if ed.add_key(700):
        problems.append('add_key: a duplicate slice was accepted')

    if not ed.move_key(ed.selected, 733):
        problems.append('move_key did nothing')
    ed.set_value(ed.selected, 'snow', 0.75)
    ed.set_value(ed.selected, 'storm', 0.5)
    ed.set_value(ed.selected, 'light_tint', (0.62, 0.88, 1.28))
    sl = [k.slice for k in ed.atmo.keys]
    if sl != sorted(sl) or len(set(sl)) != len(sl):
        problems.append('keys not sorted/unique after the edits: %s' % sl)
    if ed.atmo.keys[ed.selected] is not added:
        problems.append('the selection lost its key after sorting')
    if abs(ed.state.sky.precipitation_at(733)[1] - 0.75) > 1e-6:
        problems.append('preview not recomputed: snow at 733 = %.3f' % ed.state.sky.precipitation_at(733)[1])

    saved_keys = list(ed.atmo.keys)
    ed.save_ini()

    ed2 = ed_mod.open_editor('alps', N_SLICES, ini=ini)       # "restart"
    if not keys_equal(saved_keys, ed2.atmo.keys):
        problems.append('keys differ after a restart')
    if ed2.atmo.sky.dome_radius != ed.atmo.sky.dome_radius:
        problems.append('section [sky] differs after a restart')
    print('scenario: alps %d keys -> preset coastal %d -> +1 key, edits, ini, restart: %d keys match'
          % (n0, n1, len(ed2.atmo.keys)))

    # delete keeps at least one key and clears selection safely
    for _ in range(len(ed2.atmo.keys) + 2):
        ed2.delete_key(0)
    if len(ed2.atmo.keys) != 1:
        problems.append('delete_key: %d keys left instead of 1' % len(ed2.atmo.keys))

    # .bin export into a scratch Sky dir (never the game's)
    sky_dir = os.path.join(tmp, 'Sky')
    os.makedirs(sky_dir)
    with open(os.path.join(sky_dir, 'Alps.bin'), 'wb') as f:
        f.write(open(atm.sky_bin_path('alps'), 'rb').read())
    path = ed.export_bin(sky_dir)
    sky = skybin.read(path)
    if len(sky.weather) != len(saved_keys) or abs(sky.precipitation_at(733)[1] - 0.75) > 1e-6:
        problems.append('export_bin: wrong content')
    if not os.path.exists(path + '.orig'):
        problems.append('export_bin: no .orig copy')
    print('export_bin: %d keys, .orig kept' % len(sky.weather))


def check_storm(problems):
    cases = ((0.0, 'none'), (0.135, 'weak'), (0.545, 'medium'), (0.66, 'strong'), (1.0, 'strong'))
    for value, word in cases:
        if word not in ed_mod.storm_label(value):
            problems.append('storm %.3f: %r, expected %r' % (value, ed_mod.storm_label(value), word))
    if [ed_mod.storm_class(v) for v in (0.039, 0.04, 0.329, 0.33, 0.659, 0.66)] != [0, 1, 1, 2, 2, 3]:
        problems.append('storm class limits differ from the ATM1 table')
    print('storm: limits 0.04 / 0.33 / 0.66 -> none / weak / medium / strong')


if __name__ == '__main__':
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        check_scenario(problems, tmp)
    check_storm(problems)
    print('\n' + ('ALL PASSED' if not problems else 'FAILURES'))
    for p in problems:
        print('  -', p)
    sys.exit(1 if problems else 0)
