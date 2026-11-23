"""Pixel plumbing for the texture editor: load/save, blur, colour spaces,
resampling and the transparency handling every other module relies on.

Images travel as float32 sRGB in [0, 1] with shape (H, W, 3) plus a separate
float32 alpha (H, W).  Transparent pixels carry *bled* colour (the nearest
opaque colour pushed outward), so no blur, blend or resample can ever drag
black or key-colour magenta into a visible edge.
"""

import numpy as np
from PIL import Image

EPS = 1e-6


# ----------------------------------------------------------------- files ---

def load_image(path):
    """-> (rgb float32 HxWx3, alpha float32 HxW or None)."""
    im = Image.open(path)
    has_alpha = im.mode in ('RGBA', 'LA') or 'transparency' in im.info
    im = im.convert('RGBA' if has_alpha else 'RGB')
    a = np.asarray(im, dtype=np.float32) / 255.0
    if a.shape[2] == 4:
        return np.ascontiguousarray(a[:, :, :3]), np.ascontiguousarray(a[:, :, 3])
    return a, None


def save_png(path, rgb, alpha=None):
    buf = np.clip(rgb, 0.0, 1.0)
    if alpha is not None:
        buf = np.dstack([buf, np.clip(alpha, 0.0, 1.0)])
    Image.fromarray(np.round(buf * 255.0).astype(np.uint8)).save(path)


# ------------------------------------------------------------ key colour ---

def key_mask(rgb, colour, tol):
    """Alpha from a key colour: 0 where rgb is within `tol` of `colour`."""
    d = np.abs(rgb - np.asarray(colour, np.float32)).max(axis=2)
    return (d > tol).astype(np.float32)


def key_colour_hits(rgb, colour, tol):
    return int(np.count_nonzero(np.abs(rgb - np.asarray(colour, np.float32)).max(axis=2) <= tol))


def is_binary(alpha):
    return bool(np.all((alpha <= 0.02) | (alpha >= 0.98)))


# ------------------------------------------------------------------ blur ---

def _box1d(a, r, axis, mode):
    if r < 1:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    ap = np.pad(a, pad, mode='wrap' if mode == 'wrap' else 'edge')
    cs = np.cumsum(ap, axis=axis, dtype=np.float64)
    zeros = np.zeros_like(cs.take([0], axis=axis))
    cs = np.concatenate([zeros, cs], axis=axis)
    n = a.shape[axis]
    hi = cs.take(np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = cs.take(np.arange(0, n), axis=axis)
    return ((hi - lo) / float(2 * r + 1)).astype(np.float32)


def box_blur(a, r, mode_x='edge', mode_y='edge'):
    a = _box1d(a, r, 1, mode_x)
    return _box1d(a, r, 0, mode_y)


def gauss_blur(a, sigma, mode_x='edge', mode_y='edge'):
    """Three box passes, the usual cheap stand-in for a real gaussian."""
    if sigma <= 0.3:
        return a
    w = np.sqrt(12.0 * sigma * sigma / 3.0 + 1.0)
    r = max(1, int(round((w - 1.0) / 2.0)))
    for _ in range(3):
        a = box_blur(a, r, mode_x, mode_y)
    return a


# ------------------------------------------------------------ bleed fill ---

def bleed_fill(rgb, alpha, thresh=0.5):
    """Push opaque colour outward into the transparent pixels.

    Doubling the box radius each round fills any image in log2(size) passes,
    so this stays cheap enough to run once per loaded file.
    """
    if alpha is None:
        return rgb
    known = alpha >= thresh
    if known.all():
        return rgb
    if not known.any():
        return np.zeros_like(rgb)
    res = rgb * known[:, :, None]
    kn = known.astype(np.float32)
    h, w = alpha.shape
    r = 1
    while kn.min() < 0.5 and r <= max(h, w):
        bo = box_blur(res * kn[:, :, None], r)
        bw = box_blur(kn, r)
        todo = (kn < 0.5) & (bw > 1e-4)
        if todo.any():
            res[todo] = bo[todo] / bw[todo][:, None]
            kn[todo] = 1.0
        r *= 2
    return res


# -------------------------------------------------------------- resample ---

def resize(a, tw, th, wrap_x=False, wrap_y=False, pad=8):
    """Lanczos resample.  With wrap_* the image is padded from the opposite
    edge first, so an already-tiling texture still tiles afterwards."""
    h, w = a.shape[:2]
    if (w, h) == (tw, th):
        return a.copy()
    px = pad if wrap_x else 0
    py = pad if wrap_y else 0
    if px or py:
        a = np.pad(a, [(py, py), (px, px)] + [(0, 0)] * (a.ndim - 2), mode='wrap')
    ox = int(round(px * tw / float(w)))
    oy = int(round(py * th / float(h)))
    out_w, out_h = tw + 2 * ox, th + 2 * oy
    chans = a if a.ndim == 3 else a[:, :, None]
    out = np.empty((out_h, out_w, chans.shape[2]), np.float32)
    for c in range(chans.shape[2]):
        im = Image.fromarray(np.ascontiguousarray(chans[:, :, c], np.float32), mode='F')
        out[:, :, c] = np.asarray(im.resize((out_w, out_h), Image.LANCZOS), np.float32)
    out = out[oy:oy + th, ox:ox + tw]
    return out if a.ndim == 3 else out[:, :, 0]


# ---------------------------------------------------------- colour space ---

def srgb_to_linear(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055).astype(np.float32)


_M_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                   [0.2126729, 0.7151522, 0.0721750],
                   [0.0193339, 0.1191920, 0.9503041]], np.float32)
_M_RGB = np.linalg.inv(_M_XYZ).astype(np.float32)
_WP = np.array([0.95047, 1.0, 1.08883], np.float32)


def _f(t):
    d = 6.0 / 29.0
    return np.where(t > d ** 3, np.cbrt(np.maximum(t, 1e-8)), t / (3 * d * d) + 4.0 / 29.0)


def _finv(t):
    d = 6.0 / 29.0
    return np.where(t > d, t ** 3, 3 * d * d * (t - 4.0 / 29.0))


def rgb_to_lab(rgb):
    xyz = srgb_to_linear(rgb) @ _M_XYZ.T / _WP
    f = _f(xyz)
    return np.dstack([116.0 * f[:, :, 1] - 16.0,
                      500.0 * (f[:, :, 0] - f[:, :, 1]),
                      200.0 * (f[:, :, 1] - f[:, :, 2])]).astype(np.float32)


def lab_to_rgb(lab):
    fy = (lab[:, :, 0] + 16.0) / 116.0
    fx = fy + lab[:, :, 1] / 500.0
    fz = fy - lab[:, :, 2] / 200.0
    xyz = np.dstack([_finv(fx), _finv(fy), _finv(fz)]).astype(np.float32) * _WP
    return linear_to_srgb(xyz @ _M_RGB.T)


def luminance(rgb):
    return (rgb * np.array([0.2126, 0.7152, 0.0722], np.float32)).sum(axis=2)


# --------------------------------------------------------------- helpers ---

def masked_stats(a, mask):
    """Per-channel (mean, std) over mask > 0.5; whole image when mask is None."""
    flat = a.reshape(-1, a.shape[2])
    if mask is not None:
        sel = mask.reshape(-1) > 0.5
        if sel.sum() >= 16:
            flat = flat[sel]
    return flat.mean(axis=0), np.maximum(flat.std(axis=0), 1e-4)


def smoothstep(n):
    t = (np.arange(n, dtype=np.float32) + 0.5) / float(n)
    return t * t * (3.0 - 2.0 * t)
