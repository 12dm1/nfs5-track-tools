"""HS13/HS14: NFS4 track ambience -> PU `<track>_audio.scn` + sound banks.

NFS4 describes the ambience of a track in four places (see
`docs/pipeline/ambient-sound.md`); three of them live inside `nfshs.exe`
and are extracted ahead of time into `data/ambient/nfs4_ambient.json`:

  loop zones     slice range + bank slot + side, faded in and out along the range
  one-shot zones slice ranges + per-frame probability + a group of bank slots
  SoundSource    a point in a block, `type & 0xffff` = bank slot

All three index the same per-track bank `Data/Audio/Sfx/tram%02d.bnk`.  PU has
no slice-based ambience at all: `<track>_audio.scn` holds positioned elements,
each naming a bank of `track.viv` by the three digits in its filename.  So

  * every referenced NFS4 slot becomes one PU bank (`ea_bnk.export`), and
  * every NFS4 zone becomes elements placed along the road (`_chain`).

A loop zone turns into a chain of points spaced `LOOP_SPACING` metres apart so
the loop stays audible along the whole stretch; PU cross-fades them by distance
on its own.  A one-shot zone turns into one element per bank slot of its group,
spread over the range, each repeating with the interval that reproduces the
original firing rate (see `_oneshot_interval`).

Bank numbers are allocated from the free ranges of `track.viv` and kept in
`data/ambient/pu_bank_ids.json` so that two tracks never claim the same
number.  Nothing of the game is modified: the banks go into a separate
`GameData/Sounds/trackext.viv`, which the wrapper substitutes for `track.viv`
when `[content] extended_content=1`.
"""

import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'audio'))
import ea_bnk                                                    # noqa: E402
import nfs5cfg                                                   # noqa: E402

HS_SFX = 'Data/Audio/Sfx/tram%02d.bnk'      # under the High Stakes folder (nfs5cfg.NFS4_ROOT)
DATA = os.path.join(nfs5cfg.DATA, 'ambient')
AMBIENT_JSON = os.path.join(DATA, 'nfs4_ambient.json')
BANK_IDS_JSON = os.path.join(DATA, 'pu_bank_ids.json')
OVERRIDES_JSON = os.path.join(DATA, 'ambient_overrides.json')

# PU element field defaults, set by ear (HS14).  The first pass used 150 m for
# everything and Hometown's water carried across the track; 75 m then made
# Gt1's grandstand crowd inaudible at racing speed.  What actually made the
# water too much was the length of its chain, not the radius of one point, so
# the radius is back up and the per-slot overrides handle the loud zones.
LOOP_SPACING = 45.0        # m between the points of a loop chain
LOOP_RANGE = 60.0          # audible radius; > spacing so the chain has no holes
LOOP_FADE = 20.0           # full-volume radius
LOOP_VOLUME = 0.9          # NFS4 runs its loops slightly hotter than PU expects
# Third listening pass: at 110/150 m every sound started far too early.  The
# 55-65 m of Hometown's water override is what sounded right, so that is now
# the default for everything; chains are denser to keep the coverage.
ONESHOT_SPACING = 60.0     # m between the points of a one-shot chain
POINT_RANGE = 70.0         # SoundSource and one-shot radius
POINT_FADE = 20.0
# Script blocks with a slot but no probability are events: NFS4 plays them
# once as the car reaches a given slice (a bell, a bump).  PU has no slice
# trigger for sound, so they become a short-range element that repeats slowly
# enough to be heard about once per pass.
EVENT_RANGE = 45.0
EVENT_INTERVAL_S = 25.0
SIDE_OFFSET = 12.0         # how far off the road edge a side-panned zone sits
MAX_ELEMENTS = 320         # PU's own busiest track (castle) has 318
# Size cap, set empirically (HS14, 2026-09-21): every converted bank that
# played in game was under 2^15 samples and every silent one was over it (for
# EA-XA the byte size is the same limit, 0.536 bytes/sample).  Long loops are
# resampled down, not truncated.  An earlier note here blamed PU's streaming
# window at 00656ACC; its 16-bit cursor turned out to be bounds-checked.
MAX_BANK_BYTES = 15000
# What the whole window holds; the acceptance check keeps the worst spot on a
# track well under it.
STREAM_WINDOW_BYTES = 32764

# One PU interval unit is 8 frames at 60 Hz: an element with ivl=64 repeated
# every ~9.5 s in game including its 1.2 s sample (measured 2026-09-21).
INTERVAL_UNIT = 8.0 / 60.0
# The NFS4 scripts are evaluated once per audio update; 30 Hz reproduces the
# density of the original (p=0.006 -> a call every ~5.5 s).
NFS4_SCRIPT_HZ = 30.0
# NFS4 volumes are 0..127, PU's are 0..100. The per-track master
# (`volume`, 75/63/45 of 127) is NOT applied: PU scales ambience by its own
# audio setting, and folding both in made everything inaudible.
NFS4_VOL_MAX = 127.0
LOOP_FADE_FLOOR = 0.7      # quietest element at the end of a long loop chain
LOOP_FADE_MIN_POINTS = 5   # shorter chains are all "edge"; do not fade them


def _load(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ------------------------------------------------------------ bank numbers ---

def free_bank_ids(viv_path):
    """Numbers not used by the stock archive, in the order we hand them out."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'audio'))
    import pu_track_viv
    return pu_track_viv.free_ids(viv_path)


class BankRegistry:
    """Persistent `track/slot -> PU bank number` map, shared by all tracks."""

    def __init__(self, free_ids, path=BANK_IDS_JSON):
        self.path = path
        self.free = list(free_ids)
        self.map = {}
        if os.path.exists(path):
            self.map = _load(path)
        used = set(self.map.values())
        self.free = [i for i in self.free if i not in used]

    def id_for(self, track, slot, variant=0):
        key = '%s/%d/%d' % (track.lower(), slot, variant)
        if key not in self.map:
            if not self.free:
                raise RuntimeError('track.viv bank numbers exhausted (1000 max)')
            self.map[key] = self.free.pop(0)
        return self.map[key]

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.map, f, indent=1, sort_keys=True)


# ------------------------------------------------------------- geometry -----

def _road(track, slice_index):
    """Slice -> (position, right vector, half width). Slices are vroad points."""
    i = max(0, min(track.n_vroad - 1, int(slice_index)))
    vr = track.vroad(i)
    return vr['ref'], vr['right'], sum(vr['lane']) / 2.0


def _point(track, slice_index, side, height=2.0):
    ref, right, half = _road(track, slice_index)
    d = 0.0 if not side else side * (half + SIDE_OFFSET)
    return (ref[0] + right[0] * d, ref[1] + right[1] * d + height, ref[2] + right[2] * d)


def _chain(track, start, end, spacing):
    """Slice indices spaced `spacing` metres apart over [start, end]."""
    start, end = max(0, int(start)), min(track.n_vroad - 1, int(end))
    if end <= start:
        return [start]
    step_m = 0.0
    pts = [track.vroad(i)['ref'] for i in range(start, end + 1)]
    length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    if length <= 0:
        return [start]
    count = max(1, int(round(length / spacing)))
    return [start + (end - start) * k // count for k in range(count + 1)]


# ------------------------------------------------------------- elements -----

def _element(pos, bank_id, label, kind=0, vol=100, interval=0, interval_rnd=0,
             repeat=0, repeat_rnd=0, rng=POINT_RANGE, fade=POINT_FADE):
    return '\r\n'.join([
        'AUDIO_ELEMENT 4',
        '%f %f %f' % pos,
        '%d %d %f %f %d %d %d %d 0 -1 %d 0'
        % (kind, bank_id, rng, fade, interval, interval_rnd, repeat, repeat_rnd, vol),
        label, ''])


def _volume(nfs4_vol):
    """NFS4 volume (0..127) -> PU volume (0..100)."""
    return max(1, min(100, int(round(nfs4_vol / NFS4_VOL_MAX * 100.0))))


def _seconds_to_units(seconds):
    return max(1, min(30000, int(round(seconds / INTERVAL_UNIT))))


def _oneshot_interval(probability, audible, sample_seconds):
    """NFS4 per-frame probability -> PU interval for one element of a chain.

    While the car is inside the zone NFS4 starts a sound `p * hz` times per
    second, whichever slot of the group it picks.  A PU chain reproduces that
    rate when each of the `audible` elements currently in range repeats every
    `audible / (p * hz)` seconds.  The first pass used `variants / rate` and
    put one element per variant across the whole zone, which is why Gt1 - four
    short zones on a fast circuit - came out silent.
    """
    if not probability:
        return 0
    rate = probability * NFS4_SCRIPT_HZ
    return _seconds_to_units(max(1.0, audible / rate - sample_seconds))


# --------------------------------------------------------------- builder ----

class AmbientBuilder:
    """Everything needed to emit one track's audio scene and its banks."""

    def __init__(self, hs_root, ambient=None, registry=None, overrides=None):
        self.hs_root = hs_root
        self.ambient = ambient or _load(AMBIENT_JSON)
        self.registry = registry
        raw = overrides if overrides is not None else (
            _load(OVERRIDES_JSON) if os.path.exists(OVERRIDES_JSON) else {})
        self.overrides = {k: v for k, v in raw.items() if not k.startswith('_')}
        self._banks = {}
        # The exe spells the tracks LostCany/RockyPas, the directories don't.
        self._names = {k.lower(): k for k in self.ambient['tram_bank']}

    def _key(self, nfs4_track):
        return self._names[nfs4_track.lower()]

    def _override(self, nfs4_track, slot):
        """Per-slot corrections made by ear; see ambient_overrides.json."""
        return self.overrides.get('%s/%d' % (nfs4_track.lower(), slot), {})

    def _bank(self, nfs4_track, slot, variant=0):
        """-> (pu_bank_id, sample dict) or None when the slot is empty."""
        nfs4_track = self._key(nfs4_track)
        number = self.ambient['tram_bank'][nfs4_track]
        path = os.path.join(self.hs_root, HS_SFX % number)
        if path not in self._banks:
            self._banks[path] = ea_bnk.read_bank(open(path, 'rb').read())
        bank = self._banks[path]
        if slot < 0 or slot >= len(bank['slots']) or bank['slots'][slot] is None:
            return None
        pt = bank['slots'][slot]
        if variant >= len(pt['samples']):
            return None
        sample = dict(pt['samples'][variant])
        sample['outer'] = pt['outer']
        return self.registry.id_for(nfs4_track, slot, variant), sample

    def build(self, track, nfs4_track):
        """-> (scn bytes, {bank filename: bytes}, stats dict)"""
        nfs4_track = self._key(nfs4_track)
        text, banks = [], {}
        counts = {'loops': 0, 'oneshots': 0, 'sources': 0, 'missing': 0}

        def emit(slot, variant, pos, label, unloop=False, **kw):
            got = self._bank(nfs4_track, slot, variant)
            if not got:
                counts['missing'] += 1
                return False
            bank_id, sample = got
            name = '%s%02d_%03d.bnk' % (nfs4_track.lower()[:6], slot, bank_id)
            if name not in banks:
                # Loop from 0 first: a loop start inside the sample selects a
                # decoder that overruns its buffer (HS15), and trimming before
                # the size cap keeps more of the rate for the part that plays.
                fitted = ea_bnk.fit_sample(ea_bnk.loop_from_start(sample), MAX_BANK_BYTES)
                banks[name] = ea_bnk.write_bank(fitted, loop=not unloop and fitted['loop'])
            text.append(_element(pos, bank_id, label, **kw))
            return True

        for zone in self.ambient['loop_zones'][nfs4_track]:
            side = zone['side']
            over = self._override(nfs4_track, zone['slot'])
            rng = over.get('range', LOOP_RANGE)
            spacing = over.get('spacing', LOOP_SPACING)
            body = _chain(track, zone['start'], zone['end'], spacing)
            vol = over.get('volume') or int(round(_volume(zone['volume']) * LOOP_VOLUME))
            # `interval_s` turns a short NFS4 loop into a sound that repeats
            # with gaps: a one-second bird chirp looped back to back is what
            # NFS4 does, but it reads as a stuck sample in PU's mix.
            gap = over.get('interval_s')
            for index in body:
                edge = min(abs(index - zone['start']), abs(zone['end'] - index))
                span = max(1, zone['fade_in_end'] - zone['start'])
                ramp = 1.0
                if len(body) >= LOOP_FADE_MIN_POINTS and edge < span:
                    ramp = LOOP_FADE_FLOOR + (1.0 - LOOP_FADE_FLOOR) * edge / span
                v = max(1, int(round(vol * ramp)))
                interval = _seconds_to_units(gap) if gap else 0
                if emit(zone['slot'], 0, _point(track, index, side),
                        'HS loop s%d slice%d' % (zone['slot'], index),
                        kind=0, vol=v, rng=rng, fade=min(LOOP_FADE, rng / 3.0),
                        interval=interval, interval_rnd=interval,
                        unloop=bool(gap)):
                    counts['loops'] += 1

        for block in self.ambient['oneshot_scripts'][nfs4_track]:
            if block['slot'] and not block['prob']:
                base, variants = block['slot']
                lo, hi = block['slices'][0] if block['slices'] else (0, 0)
                vol_base, vol_span = block['vol'] or (100, 1)
                index = (lo + hi) // 2
                interval = _seconds_to_units(EVENT_INTERVAL_S)
                if emit(base, 0, _point(track, index, 0),
                        'HS event s%d slice%d' % (base, index),
                        kind=0, vol=_volume(vol_base + vol_span / 2.0),
                        rng=EVENT_RANGE, fade=POINT_FADE,
                        interval=interval, interval_rnd=interval // 2):
                    counts['events'] = counts.get('events', 0) + 1
                continue
            if not block['slot'] or not block['prob']:
                continue
            base, variants = block['slot']
            variants = max(1, min(32, variants))
            ranges = [r for r in block['slices'] if r[1] is not None] or [(0, track.n_vroad - 1)]
            vol_base, vol_span = block['vol'] or (100, 1)
            probability = block['prob'][0] / block['prob'][1]
            # How many chain points are within earshot at once; the interval of
            # each has to be that much longer to keep the original rate.
            audible = max(1.0, 2.0 * POINT_RANGE / ONESHOT_SPACING)
            points = [(lo, i) for lo, hi in ranges
                      for i in _chain(track, lo, hi, ONESHOT_SPACING)]
            # A short zone has fewer points than the group has slots, and
            # cycling would then use only the first two or three of them.
            # Stack a few elements per point instead and stretch the interval
            # to match, so the whole group is heard at the original rate.
            reps = max(1, min(4, -(-variants // max(1, len(points)))))
            step = 0
            for _lo, index in points:
                for _ in range(reps):
                    slot = base + step % variants
                    got = self._bank(nfs4_track, slot, 0)
                    step += 1
                    if not got:
                        counts['missing'] += 1
                        continue
                    sample = got[1]
                    over = self._override(nfs4_track, slot)
                    seconds = sample['n'] / float(sample['rate'])
                    interval = _seconds_to_units(over['interval_s']) if over.get('interval_s')                         else _oneshot_interval(probability, audible * reps, seconds)
                    side = 0 if block['pan'] == 'random' else (1 if step % 2 else -1)
                    if emit(slot, 0, _point(track, index, side),
                            'HS oneshot s%d slice%d' % (slot, index),
                            kind=0, vol=over.get('volume') or _volume(vol_base + vol_span / 2.0),
                            rng=over.get('range', POINT_RANGE),
                            interval=interval, interval_rnd=interval):
                        counts['oneshots'] += 1

        for pos, kind in _sound_sources(track):
            slot, periodic = kind & 0xFFFF, (kind >> 16) & 0xFFFF
            if emit(slot, 0, (pos[0], pos[1] + 2.0, pos[2]),
                    'HS source s%d' % slot, kind=0, vol=_volume(110),
                    interval=64 if periodic else 0,
                    interval_rnd=64 if periodic else 0):
                counts['sources'] += 1

        if len(text) > MAX_ELEMENTS:
            step = len(text) / float(MAX_ELEMENTS)
            text = [text[int(i * step)] for i in range(MAX_ELEMENTS)]
            counts['trimmed'] = True
        counts['elements'] = len(text)
        counts['banks'] = len(banks)
        return (''.join(text).encode('latin1'), banks, counts)

    def object_sounds(self, track, nfs4_track):
        """-> ({bank filename: bytes}, animdefs text) for the animated objects
        of this track that NFS4 gives a sound (see object_sound_specs)."""
        nfs4_track = self._key(nfs4_track)
        aids = {obj['anim']['aid'] for obj in _anim_objects(track)}
        banks, text = {}, []
        for aid, spec in sorted(object_sound_specs(nfs4_track, self.ambient).items()):
            if aid not in aids:
                continue
            number = self.ambient['tram_bank'][nfs4_track]
            path = os.path.join(self.hs_root, HS_SFX % number)
            if path not in self._banks:
                self._banks[path] = ea_bnk.read_bank(open(path, 'rb').read())
            slots = self._banks[path]['slots']
            if spec['slot'] >= len(slots) or slots[spec['slot']] is None:
                continue
            pt = slots[spec['slot']]
            sample = dict(pt['samples'][0])
            sample['outer'] = pt['outer']
            if spec['period_s']:
                sample = _pad_to_loop(sample, spec['period_s'])
            # A key of its own: the same slot may also be an ambient element,
            # built there with different loop handling.
            bank_id = self.registry.id_for(nfs4_track, OBJECT_SLOT_BASE + spec['slot'])
            name = '%s%02d_%03d.bnk' % (nfs4_track.lower()[:6], spec['slot'], bank_id)
            fitted = ea_bnk.fit_sample(ea_bnk.loop_from_start(sample), MAX_BANK_BYTES)
            banks[name] = ea_bnk.write_bank(fitted, loop=True)
            rng = min(float(spec['radius']), OBJECT_RANGE_MAX)
            text.append(ANIMDEFS_SECTION % dict(
                tag=spec['tag'], track=nfs4_track, aid=aid, slot=spec['slot'],
                bank=bank_id, range=rng, fade=rng * OBJECT_FADE_SHARE))
        return banks, ''.join(text)


# --------------------------------------------------------- object sounds ----
#
# NFS4 keeps the sounds of animated objects in nfshs.exe (0x5AE910, HS13
# §2.4), one 9 x int16 entry per object, indexed by the object's anim id:
#
#   {radius, pan, slot, a, b, c, d, mode, extra}
#
# FUN_00428290 picks the two nearest objects inside `radius`; FUN_00427E10
# plays `slot` of the track's tram bank at volume (r^2 - d^2) / r^2.  So a
# radius of 0 is silent (Park carriages, UK cars).  Slots >= 80 are not tram
# slots (Empire), left out.  Mode -4 fades by the object's slice over [a, b]
# and [c, d]; with extra != -1 it is a random one-shot (Park's second, 1 s
# sound) and is left out.  Mode -3 plays a one-shot every `b` frames
# (Summit gondolas).
#
# PU attaches a sound to an animated article through the four characters at
# Base+0x44 and a `SECTION:Audio` of that tag in GameData/Track/animdefs.txt
# (stock: autobahn Locomotive TRAN, bank 833).  FUN_004777F0 looks the tag up
# (FUN_0047EDF0) and FUN_004AEF30 moves the sound with the object every frame,
# with Doppler.  That path restarts the repeat timer each frame, so only a
# continuous loop works: mode -3 becomes one loop of sample + silence.
# Field order of the record (parser FUN_0047D980 -> CAudioElem +0x0E..0x20):
# PatchID = bank, Range, FadeIn = full-volume radius, Trigger = repeat
# interval, FadeOut = its random part, MinRepeat / RandomRepeat = count,
# Volume.

OBJECT_RANGE_MAX = 300.0          # stock TRAN
OBJECT_FADE_SHARE = 0.2
OBJECT_SLOT_BASE = 1000           # registry key space apart from ambient slots
# Mode -3 period unit: assumed 1/64 s like the NFS4 anim clock (HZ2), so
# Summit's gondola knocks every 2 s.  Not verified.
OBJECT_PERIOD_UNIT_S = 1.0 / 64.0
_TAG_DIGITS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
ANIMDEFS_BEGIN = '// >>> HZ3: NFS4 animated object sounds, generated by nfs5_ambient.py'
ANIMDEFS_END = '// <<< HZ3'
ANIMDEFS_SECTION = '''
// %(track)s anim %(aid)d, tram slot %(slot)d
SECTION:Audio
{
\ttag=%(tag)s;
\tSECTION:Sound
\t{
\t\tPatchID=%(bank)d;
\t\tRange=%(range)d;
\t\tFadeIn=%(fade)d;
\t\tTrigger=0;
\t\tFadeOut=0;
\t\tMinRepeat=0;
\t\tRandomRepeat=0;
\t\tVolume=100;
\t}
}
'''


def object_sound_specs(nfs4_track, ambient=None):
    """{aid: {tag, slot, radius, period_s}} for the objects that keep a sound.

    Pure table lookup, shared by the CRP converter (which writes the tag) and
    the sound build (which writes the bank and the animdefs section)."""
    ambient = ambient or _load(AMBIENT_JSON)
    names = list(ambient['tram_bank'])
    key = {n.lower(): n for n in names}.get(nfs4_track.lower())
    if key is None:
        return {}
    out = {}
    for aid, e in enumerate(ambient['object_sounds'].get(key, [])):
        radius, _pan, slot, _a, b, _c, _d, mode, extra = e
        if radius <= 0 or not 0 <= slot < 80 or aid >= len(_TAG_DIGITS):
            continue
        if mode == -4 and extra != -1:
            continue
        out[aid] = dict(tag='N4' + _TAG_DIGITS[names.index(key)] + _TAG_DIGITS[aid],
                        slot=slot, radius=radius,
                        period_s=b * OBJECT_PERIOD_UNIT_S if mode == -3 and b > 0 else None)
    return out


def _anim_objects(track):
    objs = [o for b in range(track.n_blocks) for o in track.objects(b)]
    objs += track.global_objects_flat()
    return [o for o in objs if o.get('anim')]


def _pad_to_loop(sample, period_s):
    """One-shot -> loop of the sample followed by silence, `period_s` long."""
    pcm = list(ea_bnk.decode_sample(sample))
    n = max(len(pcm), int(round(period_s * sample['rate'])))
    pcm += [0] * (n - len(pcm))
    out = dict(sample)
    out.update(n=n, codec='pcm16', data=struct.pack('<%dh' % n, *pcm), loop=True)
    out[0x86], out[0x87] = 0, n - 1
    return out


def animdefs_with(stock_text, sections):
    """stock animdefs.txt with the generated block replaced by `sections`."""
    text = stock_text
    if ANIMDEFS_BEGIN in text:
        head, rest = text.split(ANIMDEFS_BEGIN, 1)
        tail = rest.split(ANIMDEFS_END, 1)[1] if ANIMDEFS_END in rest else ''
        text = head.rstrip('\r\n') + '\r\n' + tail.lstrip('\r\n')
    if not sections:
        return text
    block = (ANIMDEFS_BEGIN + '\n' + sections + ANIMDEFS_END + '\n').replace('\n', '\r\n')
    return text.rstrip('\r\n') + '\r\n\r\n' + block


def _sound_sources(track):
    """FRD SoundSource records: 16.16 fixed point position + packed type."""
    out = []
    for block in range(track.n_blocks):
        h = track.headers[block]
        if not h['n_sound']:
            continue
        o = track.block_offsets[block] + h['n_vertices'] * 16 + h['n_polygons'] * 24
        o += h['n_xobj'] * 20 + h['n_polyobj'] * 20
        for i in range(h['n_sound']):
            x, y, z, kind = struct.unpack_from('<4i', track.data, o + i * 16)
            out.append(((x / 65536.0, y / 65536.0, z / 65536.0), kind))
    return out


# ------------------------------------------------------------- whole set ----

def hs_track_names(game_root):
    """Every NFS4 track that has a tr.frd, in directory order."""
    root = nfs5cfg.HS_TRACKS
    return sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, 'tr.frd')))


def build_all(game_root, write_scn_for, out_root, directory_of, verbose=True):
    """Ambience for every NFS4 track at once.

    The archive has to hold the banks of *all* converted tracks: the wrapper
    swaps the whole of track.viv for it, so a track left out of the archive
    loses its sounds the moment another one is rebuilt.  So every track is
    built here, and `<dir>_audio.scn` is written only for those listed in
    `write_scn_for` (NFS4 names, any case).  Returns per-track stats and the
    number of banks in the archive.
    """
    import nfs4_frd
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'audio'))
    import pu_track_viv

    stock_viv = os.path.join(game_root, 'GameData', 'Sounds', 'track.viv')
    registry = BankRegistry(free_bank_ids(stock_viv))
    builder = AmbientBuilder(nfs5cfg.NFS4_ROOT, registry=registry)
    wanted = {n.lower() for n in write_scn_for}
    track_dir = os.path.join(out_root, 'GameData', 'Track')
    os.makedirs(track_dir, exist_ok=True)

    banks, stats, animdefs = {}, {}, []
    for name in hs_track_names(game_root):
        frd_path = os.path.join(nfs5cfg.HS_TRACKS, name, 'tr.frd')
        track = nfs4_frd.read(frd_path)
        scn, track_banks, st = builder.build(track, name)
        banks.update(track_banks)
        object_banks, sections = builder.object_sounds(track, name)
        banks.update(object_banks)
        animdefs.append(sections)
        st['object_sounds'] = len(object_banks)
        st['directory'] = directory_of(name)
        if name.lower() in wanted:
            path = os.path.join(track_dir, '%s_audio.scn' % st['directory'])
            with open(path, 'wb') as f:
                f.write(scn)
            st['scn'] = path
        stats[name] = st
        if verbose:
            print('%-9s -> %-9s elements %3d  banks %2d  object sounds %d%s' % (
                name, st['directory'], st['elements'], st['banks'], st['object_sounds'],
                '  (scn)' if name.lower() in wanted else ''))

    entries = pu_track_viv.read_viv(open(stock_viv, 'rb').read())
    stock_ids = {pu_track_viv.bank_id(n) for n, _ in entries}
    for bank_name, blob in sorted(banks.items()):
        if pu_track_viv.bank_id(bank_name) in stock_ids:
            raise RuntimeError('bank number of %s collides with the stock archive' % bank_name)
        entries.append((bank_name, blob))
    sound_dir = os.path.join(out_root, 'GameData', 'Sounds')
    os.makedirs(sound_dir, exist_ok=True)
    with open(os.path.join(sound_dir, 'trackext.viv'), 'wb') as f:
        f.write(pu_track_viv.write_viv(entries))
    # Sounds that follow animated objects need their SECTION:Audio in the one
    # animdefs.txt all tracks share; the stock file is kept, only our block
    # is replaced.
    stock = open(os.path.join(game_root, 'GameData', 'Track', 'animdefs.txt'), 'rb').read()
    with open(os.path.join(track_dir, 'animdefs.txt'), 'wb') as f:
        f.write(animdefs_with(stock.decode('latin1'), ''.join(animdefs)).encode('latin1'))
    registry.save()
    return stats, len(banks)
