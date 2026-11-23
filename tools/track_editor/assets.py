"""Texture loading for the sky pass: level 0 of PU FSH entries -> RGBA bytes.

`nfs5_fsh.decode_entry` only knows the NFS4 track formats; the shared render
archives (`common.fsh`, `particle.fsh`) also use ARGB4444 and RGB565."""

import os

import numpy as np

import ea_refpack as ea
import track_atmosphere as atm

RENDER_DIR = os.path.join(atm.GAME, 'GameData', 'Render')
HORIZON_TAGS = ('horz', 'hor0', 'hor1', 'hor2', 'hor3')


def decode_level0(data, e):
    w, h, o = e['w'], e['h'], e['off'] + 16
    code = e['code'] & 0x7F
    if code == 0x7D:          # ARGB8888, stored B,G,R,A
        px = np.frombuffer(data, np.uint8, w * h * 4, o).reshape(-1, 4)
        rgba = px[:, [2, 1, 0, 3]]
    elif code == 0x6D:        # ARGB4444
        v = np.frombuffer(data, '<u2', w * h, o).astype(np.uint32)
        n = lambda sh: ((v >> sh) & 0xF) * 17
        rgba = np.stack([n(8), n(4), n(0), n(12)], axis=1).astype(np.uint8)
    elif code == 0x7E:        # ARGB1555
        v = np.frombuffer(data, '<u2', w * h, o).astype(np.uint32)
        c5 = lambda sh: (((v >> sh) & 0x1F) * 255 // 31)
        rgba = np.stack([c5(10), c5(5), c5(0), (v >> 15) * 255], axis=1).astype(np.uint8)
    elif code == 0x78:        # RGB565
        v = np.frombuffer(data, '<u2', w * h, o).astype(np.uint32)
        rgba = np.stack([((v >> 11) & 0x1F) * 255 // 31, ((v >> 5) & 0x3F) * 255 // 63,
                         (v & 0x1F) * 255 // 31, np.full(w * h, 255)], axis=1).astype(np.uint8)
    else:
        raise ValueError('%s: pixel format %#04x not handled' % (e['tag'], e['code']))
    return w, h, np.ascontiguousarray(rgba).tobytes()


def load_fsh(path, tags=None):
    """{tag: (w, h, rgba bytes)}; `tags` limits what is decoded."""
    data = ea.load_maybe_qfs(path)
    _h, entries = ea.fsh_dir(data)
    return {e['tag']: decode_level0(data, e) for e in entries
            if tags is None or e['tag'] in tags}


def sky_fsh_path(track):
    """`Sky/<Name>.fsh` of the track: the game's own, else the newest
    converted build that has it."""
    names = (track.capitalize(), track)
    roots = [os.path.join(atm.GAME, 'GameData', 'Track', 'Sky')]
    build = os.path.join(atm.nfs5cfg.WORK, 'build')
    if os.path.isdir(build):
        for d in sorted(os.listdir(build), reverse=True):
            roots.append(os.path.join(build, d, 'GameData', 'Track', 'Sky'))
            roots.append(os.path.join(build, d, track, 'Sky'))
    for root in roots:
        for n in names:
            p = os.path.join(root, n + '.fsh')
            if os.path.exists(p):
                return p
    return None


def load_sky_textures(track):
    """(all textures of `Sky/<Track>.fsh` or {}, its path, sun/flare textures,
    particle textures)."""
    path = sky_fsh_path(track)
    sky = load_fsh(path) if path else {}
    common = load_fsh(os.path.join(RENDER_DIR, 'common.fsh'), ('SUNA', 'GLAR', 'FLR0', 'FLR1', 'spik'))
    particles = load_fsh(os.path.join(RENDER_DIR, 'particle.fsh'), ('snow', 'rain'))
    return sky, path, common, particles


def horizon_texture(sky_textures):
    """`FUN_00459b50`: `hor<detail level>` if the archive has it, else the
    shared `horz` (no walking down). The preview assumes the top level, 3."""
    for t in ('hor3', 'horz'):
        if t in sky_textures:
            return sky_textures[t]
    return None
