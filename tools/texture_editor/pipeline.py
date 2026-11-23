"""Editor state and the processing chain.

  tone  ->  seam  ->  resize  ->  alpha

in that order on purpose: tone before the seam so the blend works on the
colours that ship, the resize last but through wrapped padding so it cannot
re-open the seam it just closed.
"""

import os

import numpy as np

import imageops as io
import seams
import tone as tone_mod

SIZES = (2048, 1024, 512, 256, 128, 64)
SIZE_SAME = -1
SIZE_ORIGINAL = -2
ALPHA_SOURCES = ('new', 'original', 'opaque')
ALPHA_LABELS = {'new': 'from new texture', 'original': 'from original',
                'opaque': 'none (opaque)'}
MAGENTA = (1.0, 0.0, 1.0)


class Source:
    """One loaded file plus everything derived from it that never changes."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.rgb, self.file_alpha = io.load_image(path)
        self.h, self.w = self.rgb.shape[:2]
        self.use_key = False
        self.key_colour = list(MAGENTA)
        self.key_tol = 0.06
        self.magenta_hits = io.key_colour_hits(self.rgb, MAGENTA, 0.06)
        self._cache = None
        self._cache_key = None
        self._small = {}
        self._derived = {}

    @property
    def has_file_alpha(self):
        return self.file_alpha is not None

    def alpha(self):
        if self.use_key:
            return io.key_mask(self.rgb, self.key_colour, self.key_tol)
        return self.file_alpha

    def stack(self):
        """(H, W, 4) with the colour bled into the transparent pixels."""
        key = (self.use_key, tuple(self.key_colour), round(self.key_tol, 4))
        if self._cache_key != key:
            a = self.alpha()
            rgb = io.bleed_fill(self.rgb, a)
            if a is None:
                a = np.ones((self.h, self.w), np.float32)
            self._cache = np.ascontiguousarray(np.dstack([rgb, a]))
            self._cache_key = key
            self._small.clear()
            self._derived.clear()
        return self._cache

    def stack_at(self, max_side):
        """The stack scaled down for the live preview, cached -- resampling a
        2k texture on every slider frame is most of the frame time otherwise."""
        full = self.stack()
        if not max_side or max(self.w, self.h) <= max_side:
            return full
        if max_side not in self._small:
            k = max_side / float(max(self.w, self.h))
            self._small[max_side] = io.resize(full, max(8, int(round(self.w * k))),
                                              max(8, int(round(self.h * k))))
        return self._small[max_side]

    def _lab(self, max_side):
        key = ('lab', max_side)
        if key not in self._derived:
            s = self.stack_at(max_side)
            self._derived[key] = (io.rgb_to_lab(s[:, :, :3]), s[:, :, 3])
        return self._derived[key]

    def lab_stats(self, max_side):
        key = ('stats', max_side)
        if key not in self._derived:
            self._derived[key] = tone_mod.ref_stats(*self._lab(max_side))
        return self._derived[key]

    def lab_cdf(self, max_side):
        key = ('cdf', max_side)
        if key not in self._derived:
            self._derived[key] = tone_mod.ref_cdf(*self._lab(max_side))
        return self._derived[key]

    def has_alpha(self):
        return self.alpha() is not None

    def binary_alpha(self):
        a = self.alpha()
        return a is not None and io.is_binary(a)


class State:
    def __init__(self):
        self.new = None                 # Source: the AI texture
        self.orig = None                # Source: the original, optional
        # tone
        self.match_mode = 'meanstd'     # meanstd | hist
        self.match = 0.0
        self.tone_map = 0.0
        self.exposure = 0.0
        self.contrast = 0.0
        self.saturation = 1.0
        self.hue = 0.0
        self.gamma = 1.0
        # seam
        self.axis = 'xy'
        self.seam_mode = 'freq'
        self.overlap_pct = 6.0
        self.feather = 2
        # output
        self.size_idx = SIZE_SAME
        self.keep_aspect = True
        self.alpha_source = 'new'
        self.write_key = False          # burn the key colour into the output
        self.out_key = list(MAGENTA)

    def target_size(self):
        if self.new is None:
            return (0, 0)
        w, h = self.new.w, self.new.h
        if self.size_idx == SIZE_ORIGINAL and self.orig is not None:
            return (self.orig.w, self.orig.h)
        if self.size_idx < 0:
            return (w, h)
        n = SIZES[self.size_idx]
        if not self.keep_aspect or w == h:
            return (n, n)
        if w >= h:
            return (n, max(1, int(round(n * h / float(w)))))
        return (max(1, int(round(n * w / float(h)))), n)

    def signature(self):
        return (id(self.new), id(self.orig),
                None if self.new is None else self.new._cache_key,
                None if self.orig is None else self.orig._cache_key,
                self.match_mode, self.match, self.tone_map, self.exposure,
                self.contrast, self.saturation, self.hue, self.gamma,
                self.axis, self.seam_mode, self.overlap_pct, self.feather,
                self.size_idx, self.keep_aspect, self.alpha_source,
                self.write_key, tuple(self.out_key))


def process(state, max_side=0):
    """Run the chain.  max_side > 0 caps the working resolution for the live
    preview; Save calls this with 0 and gets the real thing."""
    if state.new is None:
        return None
    stack = state.new.stack_at(max_side)
    tw, th = state.target_size()
    if max_side and max(tw, th) > max_side:
        s = max_side / float(max(tw, th))
        tw, th = max(8, int(round(tw * s))), max(8, int(round(th * s)))

    rgb = stack[:, :, :3]
    alpha = stack[:, :, 3]
    mask = alpha
    ref = ref_mask = None
    if state.orig is not None:
        ostack = state.orig.stack_at(max_side)
        ref, ref_mask = ostack[:, :, :3], ostack[:, :, 3]

    # ---- tone
    if state.match > 0.0 and ref is not None:
        if state.match_mode == 'hist':
            rgb = tone_mod.match_hist(rgb, mask, state.orig.lab_cdf(max_side), state.match)
        else:
            rgb = tone_mod.match_mean_std(rgb, mask, state.orig.lab_stats(max_side), state.match)
    if state.tone_map > 0.0 and ref is not None:
        rgb = tone_mod.match_tone_map(rgb, ref, state.tone_map)
    rgb = tone_mod.adjust(rgb, state.exposure, state.contrast,
                          state.saturation, state.hue, state.gamma)

    # ---- seam
    work = np.ascontiguousarray(np.dstack([rgb, alpha]))
    if state.axis:
        m = int(round(state.overlap_pct / 100.0 * min(work.shape[0], work.shape[1])))
        work = np.clip(seams.make_tileable(work, state.axis, state.seam_mode,
                                           m, state.feather), 0.0, 1.0)

    # ---- resize
    if (work.shape[1], work.shape[0]) != (tw, th):
        work = np.clip(io.resize(work, tw, th, wrap_x='x' in state.axis,
                                 wrap_y='y' in state.axis), 0.0, 1.0)

    out_rgb = work[:, :, :3]
    out_a = work[:, :, 3]

    # ---- alpha
    if state.alpha_source == 'opaque':
        out_a = None
    elif state.alpha_source == 'original' and state.orig is not None:
        oa = state.orig.alpha()
        if oa is None:
            out_a = None
        else:
            out_a = np.clip(io.resize(oa, tw, th, 'x' in state.axis,
                                      'y' in state.axis), 0.0, 1.0)
            if state.orig.binary_alpha():
                out_a = (out_a >= 0.5).astype(np.float32)
    elif state.alpha_source == 'new' and not state.new.has_alpha():
        out_a = None
    elif state.new.binary_alpha() and out_a is not None:
        out_a = (out_a >= 0.5).astype(np.float32)

    if out_a is not None and state.write_key:
        hole = out_a < 0.5
        out_rgb = out_rgb.copy()
        out_rgb[hole] = np.asarray(state.out_key, np.float32)
        out_a = None
    return out_rgb, out_a


def reference_at(state, size):
    """The original resampled to `size` -- what the A/B and diff views show."""
    if state.orig is None:
        return None
    return np.clip(io.resize(state.orig.stack()[:, :, :3], size[0], size[1]), 0.0, 1.0)
