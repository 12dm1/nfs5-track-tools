"""Make a texture tile: four ways to kill the seam.

All the band modes work the same way.  The image is first stretched by the
overlap on both sides of each processed axis, so there is spare material just
past each edge; the centre stays where it was.  The output is the centred crop,
whose leading band is merged with that spare material -- after the merge the
first column/row continues the last one exactly, which is what tiling means.

  wrap   plain cross-fade of the band; always works, softens the band
  freq   low frequencies cross-faded, high frequencies cut along a seam path
  cut    everything cut along the minimum-error path (Efros-Freeman quilting)
  poisson  no stretch at all: re-integrate the gradients with periodic
           boundary conditions, which also flattens the slow tone drift that
           makes AI textures look blotchy when tiled

Images arrive as a 4-channel float32 stack (bled rgb + alpha).
"""

import numpy as np

import imageops as io

MODES = ('wrap', 'freq', 'cut', 'poisson')
MODE_LABELS = {
    'wrap': 'Wrap blend (simple)',
    'freq': 'Freq split (default)',
    'cut': 'Min-cut (Efros-Freeman)',
    'poisson': 'Poisson wrap (FFT)',
}


# ------------------------------------------------------------- seam path ---

def _min_error_path(cost, lo, hi):
    """Cheapest top-to-bottom path through `cost`, columns limited to
    [lo, hi] so the band edges stay pure."""
    h, m = cost.shape
    big = np.float32(1e9)
    acc = np.full((h, m), big, np.float32)
    back = np.zeros((h, m), np.int16)
    acc[0, lo:hi + 1] = cost[0, lo:hi + 1]
    for y in range(1, h):
        left = np.roll(acc[y - 1], 1)
        left[0] = big
        right = np.roll(acc[y - 1], -1)
        right[-1] = big
        stack = np.stack([left, acc[y - 1], right])
        pick = np.argmin(stack, axis=0)
        acc[y] = stack[pick, np.arange(m)] + cost[y]
        back[y] = pick - 1
        acc[y, :lo] = big
        acc[y, hi + 1:] = big
    path = np.zeros(h, np.int32)
    path[h - 1] = int(np.argmin(acc[h - 1]))
    for y in range(h - 1, 0, -1):
        path[y - 1] = int(np.clip(path[y] + back[y, path[y]], lo, hi))
    return path


def _cut_mask(tail, head, feather):
    """0 -> take `tail` (the spare material), 1 -> take `head` (the crop).

    Column 0 is always pure tail and the last column always pure head, which
    is what keeps the join exact.  Difference in alpha counts double, so the
    path happily runs through transparent pixels where nothing can show.
    """
    h, m = tail.shape[:2]
    d = tail[:, :, :3] - head[:, :, :3]
    both = tail[:, :, 3] * head[:, :, 3]
    da = tail[:, :, 3] - head[:, :, 3]
    cost = (d * d).sum(axis=2) * both + 4.0 * da * da
    f = max(0, int(feather))
    lo = min(1 + f, m - 2)
    hi = max(lo, m - 2 - f)
    path = _min_error_path(cost.astype(np.float32), lo, hi)
    xs = np.arange(m)[None, :]
    mask = (xs >= path[:, None]).astype(np.float32)
    if f:
        mask = io.gauss_blur(mask, f * 0.5, mode_x='edge', mode_y='wrap')
    mask[:, 0] = 0.0
    mask[:, -1] = 1.0
    return mask[:, :, None]


# ------------------------------------------------------------- band join ---

def _join_x(e, m, w, mode, feather):
    """`e` is (H, W + 2m, C); return (H, W, C) that tiles along X."""
    head = e[:, m:m + w]
    tail = e[:, m + w:]
    out = head.copy()
    band = head[:, :m]
    t = io.smoothstep(m)[None, :, None]
    if mode == 'wrap':
        out[:, :m] = tail * (1.0 - t) + band * t
    elif mode == 'cut':
        c = _cut_mask(tail, band, feather)
        out[:, :m] = tail * (1.0 - c) + band * c
    else:                                   # freq
        sigma = max(1.0, m / 3.0)
        lo_t = io.gauss_blur(tail, sigma, mode_x='edge', mode_y='wrap')
        lo_b = io.gauss_blur(band, sigma, mode_x='edge', mode_y='wrap')
        c = _cut_mask(tail, band, feather)
        lo = lo_t * (1.0 - t) + lo_b * t
        hi = (tail - lo_t) * (1.0 - c) + (band - lo_b) * c
        out[:, :m] = lo + hi
    return out


# --------------------------------------------------------------- poisson ---

def _poisson(a, wrap_x, wrap_y):
    """Re-integrate the gradients with periodic boundaries.

    A non-wrapped axis is mirrored first, which is the cheap way to get a
    Neumann boundary out of an FFT solver.
    """
    h0, w0 = a.shape[:2]
    if not wrap_y:
        a = np.concatenate([a, a[::-1]], axis=0)
    if not wrap_x:
        a = np.concatenate([a, a[:, ::-1]], axis=1)
    h, w = a.shape[:2]
    gx = np.roll(a, -1, axis=1) - a
    gy = np.roll(a, -1, axis=0) - a
    if wrap_x:
        gx[:, -1] = 0.0                     # drop the jump across the seam
    if wrap_y:
        gy[-1, :] = 0.0
    div = (gx - np.roll(gx, 1, axis=1)) + (gy - np.roll(gy, 1, axis=0))
    den = ((2.0 * np.cos(2.0 * np.pi * np.arange(w) / w) - 2.0)[None, :]
           + (2.0 * np.cos(2.0 * np.pi * np.arange(h) / h) - 2.0)[:, None])
    den[0, 0] = 1.0
    out = np.empty_like(a)
    for c in range(a.shape[2]):
        f = np.fft.fft2(div[:, :, c])
        f[0, 0] = 0.0
        r = np.real(np.fft.ifft2(f / den))
        out[:, :, c] = r + (a[:, :, c].mean() - r.mean())
    return np.ascontiguousarray(out[:h0, :w0])


# ------------------------------------------------------------------- api ---

def make_tileable(stack, axis, mode, overlap, feather):
    """stack: (H, W, 4) float32 bled rgb + alpha.  axis: 'x' | 'y' | 'xy'."""
    if mode not in MODES or not axis:
        return stack
    h, w = stack.shape[:2]
    wrap_x, wrap_y = 'x' in axis, 'y' in axis
    if mode == 'poisson':
        return _poisson(stack.astype(np.float32), wrap_x, wrap_y)
    m = int(max(2, min(overlap, (min(w, h) - 4) // 2)))
    mx, my = (m if wrap_x else 0), (m if wrap_y else 0)
    e = io.resize(stack, w + 2 * mx, h + 2 * my)
    if wrap_x:
        e = _join_x(e, mx, w, mode, feather)
    if wrap_y:
        e = np.ascontiguousarray(np.transpose(_join_x(
            np.ascontiguousarray(np.transpose(e, (1, 0, 2))), my, h, mode, feather),
            (1, 0, 2)))
    return e


def seam_error(a, axis):
    """Mean |difference| across the wrap, and the same across an ordinary
    neighbouring pair -- the pair is the noise floor to compare against."""
    rgb = a[:, :, :3]
    out = {}
    if 'x' in axis:
        out['x'] = (float(np.abs(rgb[:, 0] - rgb[:, -1]).mean()),
                    float(np.abs(rgb[:, 1:] - rgb[:, :-1]).mean()))
    if 'y' in axis:
        out['y'] = (float(np.abs(rgb[0, :] - rgb[-1, :]).mean()),
                    float(np.abs(rgb[1:, :] - rgb[:-1, :]).mean()))
    return out
