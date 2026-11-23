"""EA BNKl sound banks (NFS4 High Stakes tram*.bnk, PU track.viv/*.bnk).

Layout (version 4, the only one both games use for ambience):

    0x00  'BNKl'
    0x04  u8  version (4)
    0x06  u16 slot count
    0x08  u32 header size (= start of sample data)
    0x0c  u32 data size
    0x10  u32 (NFS4: 0xffffffff, PU: 0)  -- not part of the table
    0x14  u32 rel[slot]   'PT' header at 0x14 + 4*slot + rel; 0 = empty slot

A 'PT' header is 'PT' + u16 platform followed by tagged fields
(tag, size, big-endian value).  Fields before 0xFD describe the slot
(volume 0x06, priority 0x0a, pan/pitch spreads ...), 0xFD opens a sample
sub-header (0x85 sample count, 0x84 rate, 0x86/0x87 loop, 0x88 absolute
data offset, 0x83 codec 7 = EA-XA), 0x8A closes it, 0xFE starts another
sample of the same slot (random variation) and 0xFF ends the slot.

Sample data is EA-XA ADPCM, mono, 15 bytes per 28 samples.
"""
import struct

DEFAULT_RATE = 22050
CODEC_EAXA = 7
CODEC_MT = 9
BLOCK_BYTES, BLOCK_SAMPLES = 15, 28
PU_SLOT_TAGS = (0x06, 0x0a, 0x0d, 0x0e, 0x0f, 0x11, 0x13)
# See fit_sample(): PU goes quiet on a bank of 2^15 samples or more.
MAX_SAMPLES = 28000


def parse_pt(b, off):
    assert b[off:off + 2] == b'PT', b[off:off + 4]
    platform = struct.unpack_from('<H', b, off + 2)[0]
    # PU 00590A80: byte 3 bit 1 set (NFS4 .pat files, 'PT', 0, 2) = 4 more header bytes
    p = off + (8 if b[off + 3] & 2 else 4)
    outer, samples, cur = {}, [], None
    while p < len(b):
        tag = b[p]; p += 1
        if tag == 0xFD:
            cur = {}
            continue
        if tag == 0xFF:
            break
        if tag == 0xFE:
            cur = None
            continue
        size = b[p]; p += 1
        val = int.from_bytes(b[p:p + size], 'big'); p += size
        if tag == 0x8A:
            samples.append(cur); cur = None
            continue
        (cur if cur is not None else outer)[tag] = val
    return dict(platform=platform, outer=outer, samples=samples, end=p)


def data_bytes(n_samples):
    return -(-n_samples // BLOCK_SAMPLES) * BLOCK_BYTES


def read_bank(b):
    """-> dict(version, header_size, data_size, slots=[None | pt dict])"""
    assert b[:4] == b'BNKl', b[:4]
    version = b[4]
    n = struct.unpack_from('<H', b, 6)[0]
    # v4 (PU, NFS4 tram/officer/dispatch): slot table at 0x14; v2 (NFS4 copspch1-6,
    # pgcop, cnteng ...): slot table at 0x0C and no data-size field
    tbl = 0x14 if version >= 4 else 0x0C
    header_size = struct.unpack_from('<I', b, 8)[0]
    data_size = struct.unpack_from('<I', b, 12)[0] if version >= 4 else len(b) - header_size
    slots = []
    for i in range(n):
        rel = struct.unpack_from('<I', b, tbl + 4 * i)[0]
        if rel == 0:
            slots.append(None)
            continue
        pt = parse_pt(b, tbl + 4 * i + rel)
        for s in pt['samples']:
            s['rate'] = s.get(0x84, DEFAULT_RATE)
            s['n'] = s[0x85]
            s['loop'] = 0x86 in s or 0x87 in s
            # NFS4: 0x83=7 -> EA-XA, 0x83=9 -> MicroTalk 10:1 (speech, ea_mt.py), no codec
            # tag -> 16-bit PCM (0x92 = bytes/sample). PU: 0x80=1 on every bank, always EA-XA.
            if s.get(0x83) == CODEC_MT:
                s['codec'] = 'mt'
                s['data'] = b[s[0x88]:]          # variable bit rate: the decoder stops by itself
                continue
            s['codec'] = 'eaxa' if (s.get(0x83) == CODEC_EAXA or 0x80 in s) else 'pcm16'
            nbytes = data_bytes(s['n']) if s['codec'] == 'eaxa' else s['n'] * 2
            s['data'] = b[s[0x88]:s[0x88] + nbytes]
        slots.append(pt)
    return dict(version=version, header_size=header_size, data_size=data_size, slots=slots)


def _field(tag, val, size=None):
    # PT values are signed (PUR12): with the top bit of the first byte set a value reads
    # negative, e.g. a sample count of 32768..65535 in 2 bytes, and PU plays nothing. EA's own
    # banks add a leading zero byte (SND_coll slot 2: 85 03 00 93 80 = 37760). This is probably
    # the "PU goes quiet at 2^15 samples" limit of fit_sample() below (not re-tested there).
    if size is None:
        size = max(1, (val.bit_length() + 8) // 8)
    return bytes([tag, size]) + val.to_bytes(size, 'big')


def write_bank(sample, outer=None, rate=None, loop=None):
    """One-slot PU-style bank around one sample dict from read_bank().

    `outer` are slot fields (volume etc.) copied from the source slot unless
    given.  Loop points are kept unless `loop` is False, which is how a short
    NFS4 loop becomes a sound PU can repeat with gaps.
    """
    outer = dict(sample.get('outer', {}) if outer is None else outer)
    # keep only slot tags that PU's own banks use (NFS4 adds 0x93=0 and 3D
    # attenuation tags 0x19..0x23 the PU player never sees)
    outer = {k: v for k, v in outer.items() if k in PU_SLOT_TAGS}
    rate = rate or sample['rate']
    loop = sample['loop'] if loop is None else loop
    pt = b'PT\0\0'
    for tag in sorted(outer):
        pt += _field(tag, outer[tag])
    pt += b'\xfd' + _field(0x80, 1) + _field(0x85, sample['n']) + _field(0x84, rate)
    if loop:
        pt += _field(0x86, sample.get(0x86, 0)) + _field(0x87, sample.get(0x87, sample['n'] - 1))
    data_off_pos = len(pt) + 2
    pt += _field(0x88, 0, 4) + _field(0x8A, 0, 4) + b'\xff'
    header_size = (0x18 + len(pt) + 15) & ~15
    pt = bytearray(pt + b'\0' * (header_size - 0x18 - len(pt)))
    struct.pack_into('>I', pt, data_off_pos, header_size)
    data = sample['data']
    if sample.get('codec') == 'pcm16':
        data = encode_eaxa(struct.unpack('<%dh' % (len(data) // 2), data))
    head = b'BNKl' + struct.pack('<BBHII', 4, 0, 1, header_size, len(data)) + struct.pack('<II', 0, 4)
    return head + bytes(pt) + data


# ---------------------------------------------------------------- decoder --

_EA_XA_TABLE = ((0, 0), (240, 0), (460, -208), (392, -220))


def decode_eaxa(data, n_samples):
    """EA-XA ADPCM mono -> list of int16 (for sanity checks / WAV export)."""
    out = []
    p1 = p2 = 0
    pos = 0
    while pos + BLOCK_BYTES <= len(data) and len(out) < n_samples:
        hdr = data[pos]
        c1, c2 = _EA_XA_TABLE[(hdr >> 4) & 3]
        shift = (hdr & 0x0F) + 8
        for i in range(BLOCK_SAMPLES):
            byte = data[pos + 1 + i // 2]
            nib = (byte >> 4) if i % 2 == 0 else (byte & 0x0F)
            if nib >= 8:
                nib -= 16
            s = (((nib << 28) >> shift) + p1 * c1 + p2 * c2) >> 8
            s = max(-32768, min(32767, s))
            out.append(s)
            p2, p1 = p1, s
        pos += BLOCK_BYTES
    return out[:n_samples]


def decode_sample(s):
    """sample dict from read_bank() -> list of int16"""
    if s['codec'] == 'mt':
        import numpy as np
        try:
            from .ea_mt import decode as decode_mt
        except ImportError:
            from ea_mt import decode as decode_mt
        return [int(v) for v in np.clip(np.round(decode_mt(s['data'], s['n'])), -32768, 32767)]
    if s['codec'] == 'pcm16':
        return list(struct.unpack('<%dh' % (len(s['data']) // 2), s['data']))
    return decode_eaxa(s['data'], s['n'])


def encode_eaxa(pcm):
    """int16 samples -> EA-XA ADPCM bytes (exhaustive coef/shift search per block)."""
    pcm = list(pcm)
    pad = (-len(pcm)) % BLOCK_SAMPLES
    pcm += [0] * pad
    out = bytearray()
    p1 = p2 = 0
    for pos in range(0, len(pcm), BLOCK_SAMPLES):
        block = pcm[pos:pos + BLOCK_SAMPLES]
        best = None
        for ci, (c1, c2) in enumerate(_EA_XA_TABLE):
            for sh in range(16):
                shift = sh + 8
                h1, h2 = p1, p2
                err = 0
                nibs = []
                for smp in block:
                    pred = (h1 * c1 + h2 * c2) >> 8
                    # value the nibble has to encode, in the decoder's scale
                    target = (smp - pred) << 8
                    nib = int(round(target / float(1 << (28 - shift))))
                    nib = max(-8, min(7, nib))
                    dec = (((nib << 28) >> shift) + h1 * c1 + h2 * c2) >> 8
                    dec = max(-32768, min(32767, dec))
                    err += (dec - smp) * (dec - smp)
                    nibs.append(nib & 0xF)
                    h2, h1 = h1, dec
                    if best is not None and err >= best[0]:
                        break
                if best is None or err < best[0]:
                    best = (err, ci, sh, nibs, h1, h2)
        err, ci, sh, nibs, p1, p2 = best
        out.append((ci << 4) | sh)
        for i in range(0, BLOCK_SAMPLES, 2):
            out.append((nibs[i] << 4) | nibs[i + 1])
    return bytes(out)


def resample(pcm, src_rate, dst_rate):
    """Linear resample. Used to keep a bank inside the size the game ships."""
    if dst_rate == src_rate or not pcm:
        return list(pcm), src_rate
    n = max(1, int(len(pcm) * dst_rate / float(src_rate)))
    step = (len(pcm) - 1) / float(max(1, n - 1)) if n > 1 else 0.0
    out = []
    for i in range(n):
        x = i * step
        k = int(x)
        f = x - k
        a = pcm[k]
        b = pcm[min(k + 1, len(pcm) - 1)]
        out.append(int(a + (b - a) * f))
    return out, dst_rate


def loop_from_start(sample):
    """Cut a looping sample down to its loop region, so the loop starts at 0.

    PU picks the decoder by the loop start (0x005984D0, 8th argument): EA-XA
    mono with loop start 0 gets codec 6, anything above 0 gets codec 7, whose
    loop handler (0x0059C690) parks up to 55 decoded samples in a buffer sized
    for a loop that begins inside the first 28-sample block.  Every stock bank
    has a loop start of 0..37; NFS4 loops start hundreds of samples in, drive
    codec 7 into states it was never tested with, and it writes past its
    instance into the next DSP node - the in-race crash of HS15/R4.  For an
    ambient loop the one-off intro before the loop start is never missed.
    """
    if not sample.get('loop'):
        return sample
    start = sample.get(0x86, 0)
    if not start:
        return sample
    pcm = decode_sample(sample)
    end = min(sample.get(0x87, len(pcm) - 1), len(pcm) - 1)
    if end <= start:
        return sample
    body = pcm[start:end + 1]
    out = dict(sample)
    out['n'] = len(body)
    out['codec'] = 'pcm16'
    out['data'] = struct.pack('<%dh' % len(body), *body)
    out[0x86] = 0
    out[0x87] = len(body) - 1
    return out


def _cut(pcm, length, loop, rate, fade_seconds=0.25):
    fade = max(1, min(length // 4, int(rate * fade_seconds)))
    body = list(pcm[:length])
    if loop:
        for j in range(fade):
            w = (j + 1) / float(fade)
            i = length - fade + j
            body[i] = int(body[i] * (1.0 - w) + pcm[len(pcm) - fade + j] * w)
    else:
        for j in range(fade):
            i = length - fade + j
            body[i] = int(body[i] * (1.0 - (j + 1) / float(fade)))
    return body


def fit_sample(sample, max_bytes, max_samples=MAX_SAMPLES,
               rates=(22050, 16000, 11025, 8000, 5500)):
    """Return a copy of `sample` small enough for PU to play.

    PU stops playing a track bank somewhere around 2^15 samples - everything
    that worked in game was under it and everything silent was over - so the
    real limit is the sample count, and for EA-XA (0.536 bytes per sample) the
    byte size is just the same limit seen from the other side.  An over-long
    NFS4 loop is dropped to a lower sample rate rather than truncated: the
    sound keeps its length and its loop points, and only loses treble.
    """
    pcm = decode_sample(sample)
    for rate in rates:
        if rate > sample['rate']:
            continue
        out, got = resample(pcm, sample['rate'], rate)
        if (data_bytes(len(out)) <= max_bytes and len(out) <= max_samples) or rate == rates[-1]:
            limit = min(max_samples, max_bytes // BLOCK_BYTES * BLOCK_SAMPLES)
            if len(out) > limit:
                # A handful of NFS4 sounds run 7-17 s and stay too long even at
                # the lowest rate: cut them.  A loop gets its new end
                # cross-faded into what used to precede its start, so the wrap
                # stays seamless; a one-shot fades out.
                out = _cut(out, limit, sample.get('loop'), got)
            scale = got / float(sample['rate'])
            fitted = dict(sample)
            fitted['rate'] = got
            fitted['n'] = len(out)
            fitted['codec'] = 'pcm16'
            fitted['data'] = struct.pack('<%dh' % len(out), *out)
            for tag in (0x86, 0x87):
                if tag in fitted:
                    fitted[tag] = max(0, min(len(out) - 1, int(fitted[tag] * scale)))
            return fitted
    return sample


def write_wav(path, pcm, rate):
    import wave
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack('<%dh' % len(pcm), *pcm))


# -------------------------------------------------------------------- cli --

def _cli():
    import argparse, os
    ap = argparse.ArgumentParser(description='EA BNKl tools (NFS4 tram banks / PU track.viv banks)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('list', help='print slots of a bank')
    a.add_argument('bank')
    a = sub.add_parser('wav', help='decode every slot of a bank to WAV files (audition)')
    a.add_argument('bank'); a.add_argument('outdir')
    a = sub.add_parser('export', help='wrap one slot as a single-slot PU bank (for track.viv)')
    a.add_argument('bank'); a.add_argument('slot', type=int); a.add_argument('out')
    a.add_argument('--variant', type=int, default=0, help='sample index inside the slot')
    args = ap.parse_args()
    bk = read_bank(open(args.bank, 'rb').read())
    if args.cmd == 'list':
        for i, s in enumerate(bk['slots']):
            if s:
                print('%3d %-40s %s' % (i, {'%02x' % k: v for k, v in s['outer'].items()},
                      ' | '.join('%s%.2fs@%d' % ('L' if x['loop'] else '', x['n'] / x['rate'], x['rate']) for x in s['samples'])))
    elif args.cmd == 'wav':
        os.makedirs(args.outdir, exist_ok=True)
        base = os.path.splitext(os.path.basename(args.bank))[0]
        for i, s in enumerate(bk['slots']):
            if not s:
                continue
            for k, x in enumerate(s['samples']):
                name = '%s_%02d%s%s.wav' % (base, i, '_v%d' % k if len(s['samples']) > 1 else '', '_loop' if x['loop'] else '')
                write_wav(os.path.join(args.outdir, name), decode_sample(x), x['rate'])
        print('wrote', args.outdir)
    elif args.cmd == 'export':
        s = bk['slots'][args.slot]
        x = dict(s['samples'][args.variant]); x['outer'] = s['outer']
        open(args.out, 'wb').write(write_bank(x))
        print('wrote', args.out)


if __name__ == '__main__':
    _cli()
