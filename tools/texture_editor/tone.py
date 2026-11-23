"""Bringing the AI texture's colour back to the original's.

Three tools that do genuinely different jobs:

  match_mean_std  overall brightness and contrast, per Lab channel
  match_hist      full histogram match, stronger and blunter
  match_tone_map  divides out the new texture's large-scale shading and
                  multiplies in the original's -- the one that matters here,
                  because the old textures have the lighting baked in

plus the usual manual trims on top.  Every one takes a 0..1 strength so the
slider can sit anywhere between "leave it alone" and "full match".
"""

import numpy as np

import imageops as io


_RANGES = ((0.0, 100.0), (-110.0, 110.0), (-110.0, 110.0))


def ref_stats(lab, mask):
    """Per-channel Lab mean/std of the reference -- cached by the caller."""
    return io.masked_stats(lab, mask)


def ref_cdf(lab, mask, bins=512):
    """Per-channel (bin centres, CDF) of the reference -- cached by the caller."""
    out = []
    sel = None if mask is None else mask.reshape(-1) > 0.5
    for c, (lo, hi) in enumerate(_RANGES):
        v = lab[:, :, c].reshape(-1)
        if sel is not None and sel.sum() >= 16:
            v = v[sel]
        edges = np.linspace(lo, hi, bins + 1, dtype=np.float32)
        hist = np.histogram(v, edges)[0].astype(np.float64)
        out.append((0.5 * (edges[:-1] + edges[1:]).astype(np.float32),
                    np.cumsum(hist) / max(hist.sum(), 1.0)))
    return out


def match_mean_std(rgb, mask, stats, strength):
    if strength <= 0.0 or stats is None:
        return rgb
    lab = io.rgb_to_lab(rgb)
    m_s, s_s = io.masked_stats(lab, mask)
    m_r, s_r = stats
    out = (lab - m_s) * (s_r / s_s) + m_r
    return io.lab_to_rgb(lab + (out - lab) * float(strength))


def match_hist(rgb, mask, cdfs, strength):
    """Per-channel CDF match in Lab, over the masked pixels only."""
    if strength <= 0.0 or cdfs is None:
        return rgb
    lab = io.rgb_to_lab(rgb)
    out = lab.copy()
    sel = None if mask is None else mask.reshape(-1) > 0.5
    for c, (lo, hi) in enumerate(_RANGES):
        src = lab[:, :, c]
        v = src.reshape(-1)
        if sel is not None and sel.sum() >= 16:
            v = v[sel]
        mid, rcdf = cdfs[c]
        edges = np.linspace(lo, hi, mid.size + 1, dtype=np.float32)
        sh = np.histogram(v, edges)[0].astype(np.float64)
        scdf = np.cumsum(sh) / max(sh.sum(), 1.0)
        lut = np.interp(scdf, rcdf, mid).astype(np.float32)
        out[:, :, c] = np.interp(src, mid, lut).astype(np.float32)
    return io.lab_to_rgb(lab + (out - lab) * float(strength))


def match_tone_map(rgb, ref, strength, sigma_frac=0.15, field=96):
    """Transfer the original's large-scale shading onto the new detail.

    The ratio is low-frequency by construction, so it is built on a small
    proxy and scaled back up -- blurring at full resolution would cost more
    than the rest of the chain put together.
    """
    if strength <= 0.0 or ref is None:
        return rgb
    h, w = rgb.shape[:2]
    fw = max(16, min(field, w))
    fh = max(16, int(round(fw * h / float(w))))
    lin = io.srgb_to_linear(rgb)
    small_s = io.resize(lin, fw, fh)
    small_r = io.resize(io.srgb_to_linear(ref), fw, fh)
    sigma = max(1.5, sigma_frac * min(fw, fh))
    lo_s = io.gauss_blur(small_s, sigma)
    lo_r = io.gauss_blur(small_r, sigma)
    ratio = np.clip((lo_r + 1e-3) / (lo_s + 1e-3), 0.25, 4.0) ** float(strength)
    ratio = np.clip(io.resize(ratio, w, h), 0.25 ** strength, 4.0 ** strength)
    return io.linear_to_srgb(lin * ratio)


def adjust(rgb, exposure=0.0, contrast=0.0, saturation=1.0, hue=0.0, gamma=1.0):
    """exposure in stops, contrast -1..1, hue in degrees."""
    out = rgb
    if exposure:
        out = io.linear_to_srgb(io.srgb_to_linear(out) * (2.0 ** exposure))
    if gamma != 1.0:
        out = np.clip(out, 0.0, 1.0) ** (1.0 / max(gamma, 1e-3))
    if contrast:
        k = 1.0 + float(contrast) * (3.0 if contrast > 0 else 1.0)
        out = np.clip((out - 0.5) * k + 0.5, 0.0, 1.0)
    if saturation != 1.0 or hue:
        lab = io.rgb_to_lab(out)
        if hue:
            th = np.radians(hue)
            a, b = lab[:, :, 1].copy(), lab[:, :, 2].copy()
            lab[:, :, 1] = a * np.cos(th) - b * np.sin(th)
            lab[:, :, 2] = a * np.sin(th) + b * np.cos(th)
        lab[:, :, 1:] *= saturation
        out = io.lab_to_rgb(lab)
    return np.clip(out, 0.0, 1.0)


def auto_strengths(rgb, mask, ref, ref_mask):
    """What 'Auto' sets: full statistical match, tone map at 0.7 -- pushing the
    shading transfer all the way tends to eat the new texture's own contrast."""
    if ref is None:
        return 0.0, 0.0
    return 1.0, 0.7
