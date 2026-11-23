"""ATM4 acceptance: CRP -> draw-ready mesh buffers (`nfs5_track_mesh`).

  * alps: every material with a valid 4CC resolves to a tag in alps.fsh
    (alps material 0 carries a junk tag -- ATM4 finding, two window quads);
  * every triangle corner has a `df` colour and the buffers agree in length;
  * mesh bbox == union of the articles' `Base.bbox`, tolerance 1e-3;
  * all 20 stock tracks load without exceptions;
  * converted tracks (HS pipeline builds), if present: errors are printed,
    never fail the run.

    python test_atm4_mesh.py [track ...]
"""

import glob
import os
import sys

import numpy as np

import nfs5_crp as crp_mod
import nfs5_track_mesh as tm
import track_atmosphere as ta
import nfs5cfg

TRACK_DIR = os.path.join(nfs5cfg.GAME, 'GameData', 'Track')
BUILD = os.path.join(nfs5cfg.WORK, 'build')
BBOX_TOL = 1e-3


def _valid_tag(tag):
    return tag is not None and len(tag) == 4 and tag.isalnum()


def check_alps(problems):
    mesh = tm.load_track(TRACK_DIR, 'alps')
    n = len(mesh.positions)
    if n % 3:
        problems.append('alps: %d corners, not a multiple of 3' % n)
    if not (len(mesh.uvs) == len(mesh.colours) == len(mesh.article_slice) == n):
        problems.append('alps: buffers of different lengths')
    if sum(b[2] for b in mesh.batches) != n:
        problems.append('alps: the batches do not cover all corners')
    bad = [u for u in mesh.unresolved_materials if _valid_tag(u[1])]
    if bad:
        problems.append('alps: materials with a valid tag missing from the fsh: %s' % bad)
    if mesh.skipped_prims:
        problems.append('alps: primitives skipped with indices outside the window: %d'
                        % mesh.skipped_prims)

    crp = crp_mod.read('%s/alps.crp' % TRACK_DIR)
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for i in range(crp.n_articles):
        b = crp.base(i)
        if b:
            lo = np.minimum(lo, b['bbox_min'])
            hi = np.maximum(hi, b['bbox_max'])
    d_lo = np.abs(lo - mesh.bbox[0]).max()
    d_hi = np.abs(hi - mesh.bbox[1]).max()
    print('alps: %d triangles, %d batches, %d textures, bbox difference %.2e / %.2e, '
          'junk materials %d'
          % (n // 3, len(mesh.batches), len(mesh.textures), d_lo, d_hi,
             len(mesh.unresolved_materials)))
    if max(d_lo, d_hi) > BBOX_TOL:
        problems.append('alps: mesh bbox %s..%s != union of Base.bbox %s..%s'
                        % (mesh.bbox[0], mesh.bbox[1], lo, hi))
    return mesh


def check_stock(tracks, problems):
    for t in tracks:
        try:
            m = tm.load_track(TRACK_DIR, t)
        except Exception as e:  # noqa: BLE001 -- the test reports, not hides
            problems.append('%s: %s: %s' % (t, type(e).__name__, e))
            continue
        note = ''
        if m.skipped_prims:
            note += ' skipped %d' % m.skipped_prims
        bad = [u for u in m.unresolved_materials]
        if bad:
            note += ' without texture %s' % bad[:3]
        print('%-10s %6d tri  %3d batches  slices %5d  axis %4d%s'
              % (t, len(m.positions) // 3, len(m.batches), m.slices,
                 len(m.centreline), note))


def check_converted():
    errors = []
    for crp in sorted(glob.glob(os.path.join(BUILD, '*', 'GameData', 'Track', '*.crp'))):
        d, name = os.path.split(crp)
        name = name[:-4]
        if not os.path.exists(os.path.join(d, name + '.fsh')):
            continue
        try:
            m = tm.load_track(d, name)
            print('  conv. %-12s %6d tri' % (name, len(m.positions) // 3))
        except Exception as e:  # noqa: BLE001
            errors.append('%s: %s: %s' % (crp, type(e).__name__, e))
    if errors:
        print('errors on converted tracks (do not fail the test):')
        for e in errors:
            print('  -', e)


if __name__ == '__main__':
    tracks = sys.argv[1:] or list(ta.STOCK_TRACKS)
    problems = []
    check_alps(problems)
    check_stock(tracks, problems)
    if len(sys.argv) == 1:
        check_converted()
    print('\n' + ('ALL PASSED' if not problems else 'FAILURES'))
    for p in problems:
        print('  -', p)
    sys.exit(1 if problems else 0)
