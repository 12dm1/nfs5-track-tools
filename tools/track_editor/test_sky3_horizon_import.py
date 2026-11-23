"""SKY3 self-test: horizon import round trip on copies of the sky archives.

    python tools/track_editor/test_sky3_horizon_import.py

For every dumped horizon: the stock parts at 1x rebuild the atlas bit for bit;
x4 RGB-only parts (alpha dropped, as upscalers do) plus an `alpha/` folder
give a 1024-wide atlas whose alpha comes from those files; the written FSH
keeps every other entry byte for byte. The game's own files are not touched.
"""

import os
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'track'))
sys.path.insert(0, _HERE)

import ea_refpack as ea          # noqa: E402
import horizon_import as hi      # noqa: E402


def entry_blocks(data):
    _h, es = ea.fsh_dir(data)
    ends = sorted(e['off'] for e in es) + [len(data)]
    return {e['tag']: data[e['off']:next(o for o in ends if o > e['off'])] for e in es}


def main():
    tmp = tempfile.mkdtemp(prefix='sky3_')
    sky_copy = os.path.join(tmp, 'Sky')
    os.makedirs(sky_copy)
    real_sky = hi.SKY_DIR
    hi.SKY_DIR, hi.BACKUP_DIR = sky_copy, os.path.join(tmp, 'backup')
    fails = done = 0
    try:
        tracks = sorted({r['track'] for r in hi.read_manifest()})
        for track in tracks:
            hi.SKY_DIR = real_sky
            src = hi.sky_fsh(track)
            hi.SKY_DIR = sky_copy
            if src is None:
                continue
            shutil.copy2(src, os.path.join(sky_copy, os.path.basename(src)))
            for tag, t in hi.targets(track).items():
                paths = hi.find_parts(t.folder, t)
                if not all(paths):
                    continue                        # transparent: nothing dumped
                fsh = hi.sky_fsh(track)
                old = hi.current_atlas(fsh, tag)
                imp = hi.Import(t, paths, fsh)
                ok1 = imp.scale == 1 and np.array_equal(imp.atlas, old)

                up = os.path.join(tmp, 'up_%s_%s' % (track, tag))
                os.makedirs(os.path.join(up, 'alpha'))
                for p, (_x, _y, w, h) in zip(paths, t.rects):
                    im = Image.open(p)
                    name = os.path.splitext(os.path.basename(p))[0] + '_x4.png'
                    im.convert('RGB').resize((w * 4, h * 4), Image.BICUBIC).save(os.path.join(up, name))
                    im.getchannel('A').resize((w * 4, h * 4), Image.NEAREST).save(
                        os.path.join(up, 'alpha', name))
                imp4 = hi.Import(t, hi.find_parts(up, t), fsh)
                want_a = np.array(Image.fromarray(old[:, :, 3], 'L').resize(
                    (old.shape[1] * 4, old.shape[0] * 4), Image.NEAREST))
                ok2 = (imp4.size == (t.atlas[0] * 4, t.atlas[1] * 4)
                       and np.array_equal(imp4.atlas[:, :, 3], want_a)
                       and all('alpha from alpha/' in n for n in imp4.notes))

                before = entry_blocks(ea.load_maybe_qfs(fsh))
                hi.write(track, imp4)
                after_data = ea.load_maybe_qfs(fsh)
                after = entry_blocks(after_data)
                got = hi.current_atlas(fsh, tag)
                ok3 = (list(before) == list(after)
                       and all(before[k] == after[k] for k in before if k != tag)
                       and np.array_equal(got, imp4.atlas)
                       and ea.fsh_dir(after_data)[0]['size'] == len(after_data))
                hi.restore(track)
                ok4 = open(fsh, 'rb').read() == open(src, 'rb').read()
                ok = ok1 and ok2 and ok3 and ok4
                fails += not ok
                done += 1
                print('%-4s %-10s %s  1x=%s x4=%s write=%s restore=%s %s'
                      % ('OK' if ok else 'FAIL', track, tag, ok1, ok2, ok3, ok4,
                         '' if ok else imp4.notes))
            # backups are per file: drop it so the next restore test is clean
            b = os.path.join(hi.BACKUP_DIR, os.path.basename(src))
            if os.path.exists(b):
                os.remove(b)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d horizons, %d failed' % (done, fails))
    return 1 if fails or not done else 0


if __name__ == '__main__':
    sys.exit(main())
