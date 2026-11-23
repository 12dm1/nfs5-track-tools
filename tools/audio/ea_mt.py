"""EA MicroTalk 10:1 decoder (UTK; PT sample field 0x83 = 9), used by NFS4 High Stakes speech
banks (officer*/disp*/helicop/copspch/*tloc .bnk, ipspch .pat suspect descriptions).

Port of PORSCHE.EXE's own decoder (FUN_0059a800 header, FUN_0059a910 frame, FUN_0059abe0
excitation, FUN_0059ae00 / FUN_0059b580 LPC synthesis), cross-checked against the public
utkdec (vgmstream). Tables are PORSCHE.EXE's (rc table 0x5E34C4, codebooks 0x5E35C4,
commands 0x5E37C8, interpolation 0x5C180C).

PUR12 fix: a zero run in the reduced-bandwidth excitation is clamped relative to the
excitation pointer (5 + align), not to the frame start. The PUR5 scratch decoder clamped it
relative to the frame start, read one extra code when align = 1 and desynchronised the bit
stream: about 1 sample in 20 came out as noise peaking at 1e5..1e7 ("end bursts", the
Whisper transcripts of those samples were hallucinations).

    pcm = decode(data, n_samples)            -> float64 numpy array (int16 scale, NOT clipped:
                                                HS speech is mastered hot, ~1 % of samples
                                                exceed 32767; normalise before converting)
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter, lfiltic

RC_TABLE = (
    0.0, -0.9967759847640991, -0.990327000617981, -0.983879029750824,
    -0.9774309992790222, -0.970982015132904, -0.9645339846611023, -0.9580850005149841,
    -0.9516370296478271, -0.9307540059089661, -0.9049599766731262, -0.8791670203208923,
    -0.8533729910850525, -0.8275790214538574, -0.8017860054969788, -0.7759919762611389,
    -0.7501980066299438, -0.7244049906730652, -0.6986110210418701, -0.6706349849700928,
    -0.619047999382019, -0.5674600005149841, -0.5158730149269104, -0.4642859995365143,
    -0.41269800066947937, -0.36111098527908325, -0.3095239996910095, -0.2579370141029358,
    -0.20634900033473969, -0.15476199984550476, -0.10317499935626984, -0.051587000489234924,
    0.0, 0.051587000489234924, 0.10317499935626984, 0.15476199984550476,
    0.20634900033473969, 0.2579370141029358, 0.3095239996910095, 0.36111098527908325,
    0.41269800066947937, 0.4642859995365143, 0.5158730149269104, 0.5674600005149841,
    0.619047999382019, 0.6706349849700928, 0.6986110210418701, 0.7244049906730652,
    0.7501980066299438, 0.7759919762611389, 0.8017860054969788, 0.8275790214538574,
    0.8533729910850525, 0.8791670203208923, 0.9049599766731262, 0.9307540059089661,
    0.9516370296478271, 0.9580850005149841, 0.9645339846611023, 0.970982015132904,
    0.9774309992790222, 0.983879029750824, 0.990327000617981, 0.9967759847640991,
)
CODEBOOKS = (
    bytes.fromhex(
        '040605090406050d0406050a04060511040605090406050e0406050a04060515'
        '040605090406050d0406050a04060512040605090406050e0406050a04060519'
        '040605090406050d0406050a04060511040605090406050e0406050a04060516'
        '040605090406050d0406050a04060512040605090406050e0406050a04060500'
        '040605090406050d0406050a04060511040605090406050e0406050a04060515'
        '040605090406050d0406050a04060512040605090406050e0406050a0406051a'
        '040605090406050d0406050a04060511040605090406050e0406050a04060516'
        '040605090406050d0406050a04060512040605090406050e0406050a04060502'),
    bytes.fromhex(
        '040b070f040c0813040b0710040c0817040b070f040c0814040b0710040c081b'
        '040b070f040c0813040b0710040c0818040b070f040c0814040b0710040c0801'
        '040b070f040c0813040b0710040c0817040b070f040c0814040b0710040c081c'
        '040b070f040c0813040b0710040c0818040b070f040c0814040b0710040c0803'
        '040b070f040c0813040b0710040c0817040b070f040c0814040b0710040c081b'
        '040b070f040c0813040b0710040c0818040b070f040c0814040b0710040c0801'
        '040b070f040c0813040b0710040c0817040b070f040c0814040b0710040c081c'
        '040b070f040c0813040b0710040c0818040b070f040c0814040b0710040c0803'),
)
COMMANDS = (   # (next model, code size in bits, pulse value)
    (1, 8, 0.0), (1, 7, 0.0), (0, 8, 0.0), (0, 7, 0.0), (0, 2, 0.0), (0, 2, -1.0), (0, 2, 1.0),
    (0, 3, -1.0), (0, 3, 1.0), (1, 4, -2.0), (1, 4, 2.0), (1, 3, -2.0), (1, 3, 2.0), (1, 5, -3.0),
    (1, 5, 3.0), (1, 4, -3.0), (1, 4, 3.0), (1, 6, -4.0), (1, 6, 4.0), (1, 5, -4.0), (1, 5, 4.0),
    (1, 7, -5.0), (1, 7, 5.0), (1, 6, -5.0), (1, 6, 5.0), (1, 8, -6.0), (1, 8, 6.0), (1, 7, -6.0),
    (1, 7, 6.0),
)
INTERP = (0.5973859429359436, 0.1145915612578392, 0.018032679334282875)
CODEC_MT = 9            # PT sample field 0x83


class _Bits:
    """LSB-first bit reader with an 8..15 bit look-ahead, as the game's."""

    def __init__(self, data):
        self.d = data
        self.p = 1
        self.v = data[0] if data else 0
        self.n = 8

    def peek(self):
        return self.v

    def skip(self, k):
        self.v >>= k
        self.n -= k
        if self.n < 8:
            b = self.d[self.p] if self.p < len(self.d) else 0
            self.p += 1
            self.v |= b << self.n
            self.n += 8

    def read(self, k):
        r = self.v & ((1 << k) - 1)
        self.skip(k)
        return r


def _rc_to_lpc(rc):
    tmp1 = [0.0] * 12
    tmp2 = [0.0] * 12
    lpc = [0.0] * 12
    for i in range(10, -1, -1):
        tmp2[1 + i] = rc[i]
    tmp2[0] = 1.0
    for i in range(12):
        x = -tmp2[11] * rc[11]
        for j in range(10, -1, -1):
            x -= tmp2[j] * rc[j]
            tmp2[j + 1] = x * rc[j] + tmp2[j]
        tmp1[i] = tmp2[0] = x
        for j in range(i):
            x -= tmp1[i - 1 - j] * lpc[j]
        lpc[i] = x
    return lpc


def _excitation(bs, multipulse, out, base, stride):
    """Fill out[5 + base + i] for i = 0, stride, ... < 108."""
    i = 0
    if not multipulse:                       # RELP: every sample coded, 0 / -2 / +2
        while i < 108:
            v = bs.peek() & 3
            if v in (0, 2):
                val = 0.0
                bs.skip(1)
            else:
                val = -2.0 if v == 1 else 2.0
                bs.skip(2)
            out[5 + base + i] = val
            i += stride
        return
    model = 0
    while i < 108:
        cmd = CODEBOOKS[model][bs.peek() & 0xFF]
        model, size, val = COMMANDS[cmd]
        bs.skip(size)
        if cmd > 3:                          # pulse |v| <= 6
            out[5 + base + i] = val
            i += stride
        elif cmd > 1:                        # 7..70 zeros, clamped to the subframe (relative to base!)
            count = 7 + bs.read(6)
            if i + count * stride > 108:
                count = (108 - i) // stride
            for _ in range(max(0, count)):
                out[5 + base + i] = 0.0
                i += stride
        else:                                # pulse |v| >= 7
            x = 7
            while bs.read(1):
                x += 1
            if not bs.read(1):
                x = -x
            out[5 + base + i] = float(x)
            i += stride


def decode(data, n_samples):
    """MicroTalk 10:1 stream -> float64 array of n_samples (int16 scale, unclipped)."""
    bs = _Bits(data)
    reduced = bs.read(1)
    thresh = 32 - bs.read(4)
    g0 = 8.0 * (1 + bs.read(4))
    mult = 1.04 + bs.read(6) * 0.001
    gains = [g0]
    for _ in range(63):
        gains.append(gains[-1] * mult)
    c0, c1, c2 = INTERP
    rc = [0.0] * 12
    adapt = np.zeros(324)
    hist = np.zeros(12)                      # last 12 outputs, hist[0] = most recent
    out = []
    total = 0
    while total < n_samples:
        mp = False
        delta = [0.0] * 12
        for i in range(12):
            if i == 0:
                idx = bs.read(6)
                mp = idx < thresh
            elif i < 4:
                idx = bs.read(6)
            else:
                idx = 16 + bs.read(5)
            delta[i] = (RC_TABLE[idx] - rc[i]) * 0.25
        frame = np.zeros(432)
        for s in range(4):
            lag = bs.read(8)
            pg = bs.read(4) * (1.0 / 15.0)
            fg = gains[bs.read(6)]
            exc = [0.0] * 118
            if not reduced:
                _excitation(bs, mp, exc, 0, 1)
            else:
                align = bs.read(1)
                zero = bs.read(1)
                _excitation(bs, mp, exc, align, 2)
                if zero:
                    for j in range(54):
                        exc[5 + (1 - align) + 2 * j] = 0.0
                else:
                    b = 5 + (1 - align)
                    for j in range(0, 108, 2):
                        k = b + j
                        exc[k] = ((exc[k - 1] + exc[k + 1]) * c0 - (exc[k - 3] + exc[k + 3]) * c1
                                  + (exc[k - 5] + exc[k + 5]) * c2)
                    fg *= 0.5
            ex = np.array(exc[5:113])
            base = 108 * s + 216 - lag
            # the decoders read adapt_cb[base..] straight from their state block: a negative
            # base falls into gains[49..63] / rc / synthesis history, as laid out there
            mem = np.concatenate([np.array(gains[49:64]), np.array(rc), hist, adapt, frame])
            cb = mem[39 + base:39 + base + 108]
            frame[108 * s:108 * s + 108] = fg * ex + pg * cb
        adapt = frame[108:432].copy()
        y = np.empty(432)
        pos = 0
        for i in range(4):                   # 3 blocks of 12 with interpolated rc, then 33 blocks
            for j in range(12):
                rc[j] += delta[j]
            a = np.concatenate([[1.0], -np.array(_rc_to_lpc(rc))])
            nblk = 1 if i < 3 else 33
            seg = frame[pos:pos + 12 * nblk]
            ys, _ = lfilter([1.0], a, seg, zi=lfiltic([1.0], a, y=hist))
            y[pos:pos + 12 * nblk] = ys
            hist = np.concatenate([hist[::-1], ys])[::-1][:12]
            pos += 12 * nblk
        out.append(y)
        total += 432
    return np.concatenate(out)[:n_samples]
