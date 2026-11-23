"""Acceptance for the NFS4 ambience in the game folder (HS14/HS15).

    python test_hs14_ambient.py

Checks what is installed, not what the builder meant to write:

  * trackext.viv keeps all 593 stock banks byte for byte and adds ours under
    numbers the stock archive does not use;
  * every added bank is one EA-XA slot PU can actually play: under 28000
    samples (silent from ~2^15 on), at most 0x7FFC bytes (the streamer refuses
    more), and a loop - if any - starting at sample 0 (a later start selects
    codec 7, which overruns its buffer and crashes the race, HS15);
  * every AUDIO_ELEMENT of every converted track names a bank that is in the
    archive, sits inside the track's bounds, and the count stays under PU's
    own maximum.
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'audio'))
import ea_bnk                                                    # noqa: E402
import pu_track_viv                                              # noqa: E402
import nfs4_frd                                                  # noqa: E402
import nfs5_ambient as ambient                                   # noqa: E402
from build_ambient import directory_of                           # noqa: E402
import nfs5cfg

GAME = nfs5cfg.GAME
MAX_SAMPLES = 28000
MAX_STREAM_BYTES = 0x7FFC
BOUNDS_MARGIN = 60.0      # side offset + a little


def main():
    failures = []
    stock = pu_track_viv.read_viv(open(os.path.join(GAME, 'GameData', 'Sounds', 'track.viv'), 'rb').read())
    ext = pu_track_viv.read_viv(open(os.path.join(GAME, 'GameData', 'Sounds', 'trackext.viv'), 'rb').read())

    if ext[:len(stock)] != stock:
        failures.append('trackext.viv does not start with the stock archive unchanged')
    stock_ids = {pu_track_viv.bank_id(n) for n, _ in stock}
    added = ext[len(stock):]
    ids = {}
    for name, blob in added:
        bid = pu_track_viv.bank_id(name)
        if bid in stock_ids:
            failures.append('%s: number %d is a stock bank' % (name, bid))
        if bid in ids:
            failures.append('%s: number %d used twice' % (name, bid))
        ids[bid] = name
        bank = ea_bnk.read_bank(blob)
        slots = [s for s in bank['slots'] if s]
        if len(slots) != 1 or len(slots[0]['samples']) != 1:
            failures.append('%s: not a single-sample bank' % name)
            continue
        s = slots[0]['samples'][0]
        if s['codec'] != 'eaxa':
            failures.append('%s: codec %s' % (name, s['codec']))
        if s['n'] > MAX_SAMPLES:
            failures.append('%s: %d samples > %d' % (name, s['n'], MAX_SAMPLES))
        if len(blob) > MAX_STREAM_BYTES:
            failures.append('%s: %d bytes > 0x7FFC' % (name, len(blob)))
        if s['loop'] and s.get(0x86, 0) != 0:
            failures.append('%s: loop starts at %d, must be 0 (HS15)' % (name, s.get(0x86)))

    total = 0
    for nfs4 in ambient.hs_track_names(GAME):
        d = directory_of(nfs4)
        path = os.path.join(GAME, 'GameData', 'Track', '%s_audio.scn' % d)
        if not os.path.exists(path):
            failures.append('%s: no %s' % (nfs4, os.path.basename(path)))
            continue
        track = nfs4_frd.read(os.path.join(nfs5cfg.HS_TRACKS, nfs4, 'tr.frd'))
        pts = [track.vroad(i)['ref'] for i in range(0, track.n_vroad, 4)]
        lo = [min(p[k] for p in pts) - BOUNDS_MARGIN for k in range(3)]
        hi = [max(p[k] for p in pts) + BOUNDS_MARGIN for k in range(3)]
        text = open(path, encoding='latin1').read()
        blocks = text.split('AUDIO_ELEMENT 4')[1:]
        outside = missing = 0
        for blk in blocks:
            lines = [x for x in blk.splitlines() if x.strip()]
            pos = [float(v) for v in lines[0].split()]
            bid = int(lines[1].split()[1])
            if bid not in ids:
                missing += 1
            if any(pos[k] < lo[k] or pos[k] > hi[k] for k in (0, 2)):
                outside += 1
        if missing:
            failures.append('%s: %d elements name banks not in the archive' % (nfs4, missing))
        if outside:
            failures.append('%s: %d elements outside the track bounds' % (nfs4, outside))
        if len(blocks) > ambient.MAX_ELEMENTS:
            failures.append('%s: %d elements > %d' % (nfs4, len(blocks), ambient.MAX_ELEMENTS))
        total += len(blocks)
        print('%-9s %-9s elements %3d  ok' % (nfs4, d, len(blocks)) if not (missing or outside)
              else '%-9s %-9s elements %3d  FAIL' % (nfs4, d, len(blocks)))

    print('\nstock banks %d, added %d, elements %d' % (len(stock), len(added), total))
    if failures:
        print('\n'.join('FAIL ' + f for f in failures))
        print('\nFAILED')
        return 1
    print('ALL PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
