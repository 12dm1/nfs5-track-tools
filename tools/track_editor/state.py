"""Atmosphere state behind the preview: model -> game-accurate evaluation.

The editable model is an `Atmosphere`; everything the renderer needs is read
from the `SkyBin` that model compiles to (`to_sky_bin`), so the preview shows
exactly what the game's own interpolation (`fog_at`, `region_blend_at`) makes
of the numbers -- including the cube on the density and the lerp weights.
"""

import math
import os

import numpy as np

import nfs5_sky_bin as skybin
import track_atmosphere as atm

W63 = 262144.0 / 5.0     # guFogTableIndexToW(63): where the fog table reaches 1.0


def fog_scale(density):
    """1 / (1 - exp(-d * w63)); the table is normalised so it ends at 1."""
    d = max(density, 1e-9)
    return 1.0 / (1.0 - math.exp(-d * W63))


def fog_factor(density, w):
    """Reference implementation of the shader's fog blend factor."""
    f = (1.0 - math.exp(-density * w)) * fog_scale(density)
    return min(1.0, max(0.0, f))


def default_atmosphere_path(track):
    for d in (atm.ASSETS_DIR, atm.PRESETS_DIR):
        p = os.path.join(d, '%s.ini' % track)
        if os.path.exists(p):
            return p
    return None


class AtmosphereState:
    def __init__(self, atmo, template=atm.TEMPLATE_SKY_BIN):
        self.template = open(template, 'rb').read()
        self.version = 0
        self.set_atmosphere(atmo)

    @classmethod
    def for_track(cls, track, path=None):
        path = path or default_atmosphere_path(track)
        if path:
            return cls(atm.load_ini(path))
        return cls(atm.from_sky_bin(skybin.read(atm.sky_bin_path(track)), name=track))

    def set_atmosphere(self, atmo):
        self.atmo = atmo
        self.sky = skybin.SkyBin(atm.to_sky_bin(atmo, self.template))
        self.version += 1

    def fog(self, slc):
        """WORLD fog = channel B: ((r, g, b) 0..1, cubed density, table scale)."""
        colour, density = self.sky.fog_b_at(slc)
        return tuple(c / 255.0 for c in colour), density, fog_scale(density)

    def sky_fog(self, slc):
        """SKY fog = channel A (dome layers + horizon), same tuple."""
        colour, density = self.sky.fog_at(slc)
        return tuple(c / 255.0 for c in colour), density, fog_scale(density)

    def background(self, slc):
        c = self.atmo.sky_colour
        if c.follow_fog:
            return tuple(v / 255.0 for v in self.sky.fog_at(slc)[0])
        t = self.sky.region_blend_at(slc)
        return tuple((c.colour_a[i] * (1 - t) + c.colour_b[i] * t) / 255.0 for i in range(3))

    def tint_table(self, n_slices):
        """(n, 3) float32 light tint per slice, piecewise linear between keys."""
        keys = self.atmo.keys
        xs = [k.slice for k in keys]
        out = np.ones((n_slices, 3), np.float32)
        if not keys:
            return out
        grid = np.arange(n_slices)
        for i in range(3):
            out[:, i] = np.interp(grid, xs, [k.light_tint[i] for k in keys])
        return out
