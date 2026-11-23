"""Editing logic of the atmosphere editor -- no imgui, no GL, testable headless.

`Editor` owns the `Atmosphere` being edited, the compiled `AtmosphereState`
the renderer reads, the selected key, and the file operations (ini, preset,
direct `Sky/<Track>.bin` export).
"""

import os
import shutil

import numpy as np

import nfs5_sky_bin as skybin
import track_atmosphere as atm
from state import AtmosphereState, default_atmosphere_path

MAX_KEYS = skybin.MAX_KEYS - 1   # the table needs its terminator entry

# ATM1 "Storm": thresholds of the lightning classes on the +0x1C weather field
STORM_CLASSES = ((0.04, 'none'), (0.33, 'weak (class 0)'), (0.66, 'medium (class 1)'),
                 (1.01, 'strong (class 2)'))
STORM_CHANCE = (0.0, 0.0333, 0.0667, 0.1)     # per-tick multiplier by class, see ATM1


def storm_class(value):
    """0 = none, 1 = weak, 2 = medium, 3 = strong."""
    for i, (limit, _) in enumerate(STORM_CLASSES):
        if value < limit:
            return i
    return 3


def storm_label(value):
    return STORM_CLASSES[storm_class(value)][1]


LANES = (
    # (label, attribute, max) -- scalar lanes; fog colour is a gradient strip
    ('sky fog A', 'fog_density', 0.30),
    ('world fog B', 'fog_density_b', 0.30),
    ('rain', 'rain', 1.0),
    ('snow', 'snow', 1.0),
    ('storm', 'storm', 1.0),
    ('region A<->B', 'region', 1.0),
)


class Editor:
    def __init__(self, track, n_slices, state=None, ini_path=None):
        self.track = track
        self.n_slices = n_slices
        self.ini_path = ini_path or os.path.join(atm.ASSETS_DIR, '%s.ini' % track)
        self.state = state or AtmosphereState.for_track(track)
        self.selected = 0
        self.dirty = False
        self.message = ''
        self.atmo.name = track
        self.atmo.slices = n_slices

    # -- access --------------------------------------------------------------

    @property
    def atmo(self):
        return self.state.atmo

    def touch(self):
        """Call after any in-place edit: keep keys sorted, recompile the .bin."""
        keys = self.atmo.keys
        chosen = keys[self.selected] if 0 <= self.selected < len(keys) else None
        keys.sort(key=lambda k: k.slice)
        if chosen is not None:
            self.selected = keys.index(chosen)
        self.state.set_atmosphere(self.atmo)
        self.dirty = True

    # -- keys ----------------------------------------------------------------

    def key_at(self, slc):
        """A new `Key` carrying the curves' current values at `slc`."""
        keys = self.atmo.keys
        xs = [k.slice for k in keys]

        def lerp(getter):
            return float(np.interp(slc, xs, [getter(k) for k in keys]))

        def colour(attr):
            return tuple(int(round(np.interp(slc, xs, [getattr(k, attr)[i] for k in keys])))
                         for i in range(3))
        return atm.Key(
            slice=int(slc), fog_colour=colour('fog_colour'),
            fog_density=lerp(lambda k: k.fog_density),
            fog_colour_b=colour('fog_colour_b'),
            fog_density_b=lerp(lambda k: k.fog_density_b),
            rain=lerp(lambda k: k.rain), snow=lerp(lambda k: k.snow),
            storm=lerp(lambda k: k.storm), region=lerp(lambda k: k.region),
            light_tint=tuple(float(np.interp(slc, xs, [k.light_tint[i] for k in keys]))
                             for i in range(3)))

    def add_key(self, slc):
        slc = int(max(0, min(self.n_slices - 1, slc)))
        if len(self.atmo.keys) >= MAX_KEYS:
            self.message = 'max %d keys (format limit)' % MAX_KEYS
            return False
        if any(k.slice == slc for k in self.atmo.keys):
            self.message = 'key already exists at slice %d' % slc
            return False
        key = self.key_at(slc)
        self.atmo.keys.append(key)
        self.selected = len(self.atmo.keys) - 1
        self.touch()
        return True

    def delete_key(self, index):
        if len(self.atmo.keys) <= 1:
            self.message = 'need at least one key'
            return False
        del self.atmo.keys[index]
        self.selected = min(self.selected, len(self.atmo.keys) - 1)
        self.touch()
        return True

    def move_key(self, index, slc):
        """Slice of one key, kept unique and inside the track."""
        slc = int(max(0, min(self.n_slices - 1, slc)))
        if any(i != index and k.slice == slc for i, k in enumerate(self.atmo.keys)):
            return False
        self.atmo.keys[index].slice = slc
        self.touch()
        return True

    def set_value(self, index, attr, value):
        setattr(self.atmo.keys[index], attr, value)
        self.touch()

    # -- presets and files ---------------------------------------------------

    @staticmethod
    def preset_names():
        return sorted(os.path.splitext(f)[0] for f in os.listdir(atm.PRESETS_DIR)
                      if f.endswith('.ini'))

    def load_preset(self, name):
        """Another track's atmosphere, slices carried over by fraction of
        track length (ATM2 §9). The sky/dome part is taken as is."""
        preset = atm.load_ini(os.path.join(atm.PRESETS_DIR, '%s.ini' % name))
        if preset.slices <= 0:
            raise ValueError('preset %s has no track length (slices=0)' % name)
        atmo = atm.rescale(preset, preset.slices, self.n_slices)
        atmo.name, atmo.slices = self.track, self.n_slices
        self.state.set_atmosphere(atmo)
        self.selected = 0
        self.dirty = True
        self.message = 'loaded preset %s (%d -> %d slices)' % (name, preset.slices, self.n_slices)

    def save_ini(self, path=None):
        path = path or self.ini_path
        self.atmo.name, self.atmo.slices = self.track, self.n_slices
        atm.save_ini(self.atmo, path)
        self.dirty = False
        self.message = 'saved %s' % path
        return path

    def reload_ini(self, path=None):
        path = path or self.ini_path
        self.state.set_atmosphere(atm.load_ini(path))
        self.selected = 0
        self.dirty = False
        self.message = 'loaded %s' % path

    def export_bin(self, sky_dir=atm.SKY_DIR):
        """Write `Sky/<Track>.bin` straight into the game. The first overwrite
        of an existing file keeps a `.orig` copy beside it."""
        name = self.track.capitalize()
        path = os.path.join(sky_dir, '%s.bin' % name)
        for candidate in (self.track, name):
            existing = os.path.join(sky_dir, '%s.bin' % candidate)
            if os.path.exists(existing):
                path = existing
                break
        if os.path.exists(path) and not os.path.exists(path + '.orig'):
            shutil.copy2(path, path + '.orig')
        with open(path, 'wb') as f:
            f.write(atm.to_sky_bin(self.atmo, self.state.template))
        self.message = 'written %s' % path
        return path

    # -- read-only sky layers (ATM2 §8) ---------------------------------------

    def layers_text(self):
        for d in (atm.SKY_DIR,):
            for name in (self.track.capitalize(), self.track):
                p = os.path.join(d, '%s.txt' % name)
                if os.path.exists(p):
                    with open(p, 'r', encoding='latin1') as f:
                        lines = [ln.rstrip() for ln in f]
                    return p, [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(('#', ';', '//'))]
        return None, []


def open_editor(track, n_slices, ini=None):
    """Editor over the track's atmosphere: explicit ini, else the saved
    `data/atmosphere/<track>.ini`, else preset, else the stock .bin."""
    path = ini or default_atmosphere_path(track)
    state = AtmosphereState.for_track(track, path)
    ed = Editor(track, n_slices, state)
    if ini:
        ed.ini_path = ini
    return ed
