"""Decode the per-track ambient data compiled into nfshs.exe.

NFS4 has no data file for track ambience.  Four tables/scripts drive it, all
indexed by the track's position in the exe's name list (see NAMES):

  0x5aed20  u8[19]   number of the track's `Data/Audio/Sfx/tram%02d.bnk`
  0x5ae978  u32[19]  master ambience volume for the track (0..127)
  0x5ae580  ptr[19]  loop zones: {start, fade_in_end, fade_out_start, end,
                     slot, side, volume} x N, terminated by start < 0
  0x5adcf4  ptr[19]  one-shot script: a function, called every audio update
                     from FUN_0042d2e0 with EAX = car, EDX = base pan
  0x5ae910  ptr[19]  sounds of animated objects, 9 x i16 each

The scripts are hand-written chains of

    if slice in [A, B] (and sometimes a test on car+0x5c4):
        if rand % MOD > THR:                      # probability per update
            SNDplay(tram, slot = BASE + rand % N, pan, vol = V0 + rand % VN)

with the arguments in registers (Watcom fastcall: EAX bank, EDX slot,
EBX pan, ECX volume).  `_Machine` below is a tiny interpreter over exactly the
instruction forms those scripts use, so BASE/N/V0/VN/MOD/THR come out exact
instead of being pattern-matched.  Anything it does not recognise clobbers the
register, and the block is reported with the field missing rather than wrong.
"""

import json
import os
import struct
import sys

import capstone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'track'))
import nfs5cfg  # noqa: E402

EXE = 'nfshs.exe'                    # under the High Stakes folder (nfs5cfg.NFS4_ROOT)
NAMES = ('Hills Germany Coastal Park France Uk Snowy Gt1 Gt2 Gt3 Hometown Redrock '
         'Atlantic RockyPas Country LostCany Aquatica Summit Empire').split()

SCRIPT_TABLE = 0x5adcf4
TRAM_INDEX = 0x5aed20
TRAM_VOLUME = 0x5ae978
LOOP_TABLE = 0x5ae580
OBJECT_TABLE = 0x5ae910

PLAY = 0x42e1f0            # SNDplay wrapper
RAND = 0x5474d8            # returns a 32-bit random in EAX
SLICE_FIELD = '0xf8]'      # car + 0xF8 = current slice
SPEED_FIELD = '0x5c4]'     # car + 0x5C4 = the other condition the scripts use


def fo_code(va): return va - 0x400000 - 0xc00
def fo_data(va): return va - 0x400000 - 0x1a00


# ------------------------------------------------------- register machine ---

class _Val:
    """base + rand % span  (span == 1 means the constant `base`)."""

    __slots__ = ('base', 'span', 'random')

    def __init__(self, base=0, span=1, random=False):
        self.base, self.span, self.random = base, span, random

    def __repr__(self):
        return '%d+r%%%d' % (self.base, self.span) if self.span > 1 else str(self.base)

    def as_pair(self):
        return [self.base, self.span]


RANDOM = object()          # raw output of the RNG, before a div/and narrows it
UNKNOWN = None


class _Machine:
    """Interpret one straight-line run of a script up to a SNDplay call."""

    def __init__(self):
        self.reg = {r: UNKNOWN for r in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi')}
        self.prob = None       # (hits, modulus) of the last `rand % MOD > THR`
        self.last_mod = None

    def _imm(self, text):
        try:
            return int(text, 0)
        except ValueError:
            return None

    def step(self, ins):
        m, ops = ins.mnemonic, [o.strip() for o in ins.op_str.split(',')]
        dst = ops[0] if ops else ''
        src = ops[1] if len(ops) > 1 else ''

        if m == 'call':
            if ins.op_str == hex(RAND):
                self.reg['eax'] = RANDOM
                self.reg['edx'] = UNKNOWN
            return
        if m == 'xor' and dst == src and dst in self.reg:
            self.reg[dst] = _Val(0)
            return
        if m == 'div' and src == '' and dst in self.reg:
            # edx:eax / reg -> eax quotient, edx remainder.  The scripts always
            # zero edx first, so the remainder is `rand % divisor`.
            div = self.reg.get(dst)
            span = div.base if isinstance(div, _Val) else None
            self.reg['edx'] = _Val(0, span, True) if span else UNKNOWN
            self.reg['eax'] = UNKNOWN
            self.last_mod = span
            return
        if m in ('mov', 'lea') and dst in self.reg:
            if m == 'lea':
                inner = src.strip('[]')
                parts = [p.strip() for p in inner.split('+')]
                if len(parts) == 2 and parts[0] in self.reg:
                    base = self.reg[parts[0]]
                    add = self._imm(parts[1])
                    if isinstance(base, _Val) and add is not None:
                        self.reg[dst] = _Val(base.base + add, base.span, base.random)
                        return
                self.reg[dst] = UNKNOWN
                return
            imm = self._imm(src)
            if imm is not None:
                self.reg[dst] = _Val(imm)
            elif src in self.reg:
                self.reg[dst] = self.reg[src]
            else:
                self.reg[dst] = UNKNOWN      # memory / stack slot
            return
        if m in ('add', 'and', 'inc', 'shl', 'sar', 'sub', 'imul', 'neg') and dst in self.reg:
            cur = self.reg[dst]
            imm = self._imm(src) if src else None
            if m == 'add' and imm is not None and isinstance(cur, _Val):
                self.reg[dst] = _Val(cur.base + imm, cur.span, cur.random)
            elif m == 'and' and imm is not None and cur is RANDOM:
                # `and eax, mask` on a raw random is `rand % (mask + 1)`
                self.reg[dst] = _Val(0, imm + 1, True)
            elif m == 'and' and imm is not None and isinstance(cur, _Val) and cur.random:
                self.reg[dst] = _Val(cur.base, min(cur.span, imm + 1), True)
            else:
                self.reg[dst] = UNKNOWN
            return
        if m == 'cmp' and dst == 'edx':
            thr = self._imm(src)
            if thr is not None and self.last_mod:
                # fires on `ja`/`jbe`-skip, i.e. remainder > thr
                self.prob = (self.last_mod - 1 - thr, self.last_mod)
            return
        if m == 'push':
            return
        # anything else: forget what it touched
        if dst in self.reg:
            self.reg[dst] = UNKNOWN

    def value(self, reg):
        v = self.reg.get(reg)
        return v if isinstance(v, _Val) else None


# ------------------------------------------------------------ script scan ---

def _ranges(cmps):
    """[(value, jcc)] over car+0xF8 -> [(lo, hi)] slice ranges."""
    out, i = [], 0
    while i < len(cmps):
        v, j = cmps[i]
        if j in ('je', 'jne'):
            out.append((v, v)); i += 1
        elif i + 1 < len(cmps):
            v2, j2 = cmps[i + 1]
            lo = v if j in ('jl', 'je') else v + 1
            hi = v2 if j2 in ('jg', 'jle', 'jne') else v2 - 1
            if hi >= lo:
                out.append((lo, hi))
            i += 2
        else:
            out.append((v, v)); i += 1
    return out


def decode_scripts(b, verbose=False):
    """-> {track: [block dicts]} for the 19 one-shot scripts."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    ptrs = [struct.unpack_from('<I', b, fo_data(SCRIPT_TABLE) + 4 * i)[0] for i in range(19)]
    order = sorted(set(ptrs))
    out = {}
    for name, p in zip(NAMES, ptrs):
        end = min([q for q in order if q > p] + [p + 0x1000])
        machine, cmps, speed, blocks = _Machine(), [], None, []
        for ins in md.disasm(b[fo_code(p):fo_code(end)], p):
            if ins.mnemonic == 'ret':
                break
            if ins.mnemonic == 'cmp' and SLICE_FIELD in ins.op_str:
                cmps.append((int(ins.op_str.split(',')[1], 0), None))
            elif ins.mnemonic == 'cmp' and SPEED_FIELD in ins.op_str:
                speed = [int(ins.op_str.split(',')[1], 0), None]
            elif ins.mnemonic.startswith('j') and ins.mnemonic != 'jmp':
                if cmps and cmps[-1][1] is None:
                    cmps[-1] = (cmps[-1][0], ins.mnemonic)
                elif speed and speed[1] is None:
                    speed[1] = ins.mnemonic
            machine.step(ins)
            if ins.mnemonic == 'call' and ins.op_str == hex(PLAY):
                slot, vol, pan = machine.value('edx'), machine.value('ecx'), machine.value('ebx')
                blocks.append(dict(
                    slices=_ranges([c for c in cmps if c[1]]),
                    speed=speed if speed and speed[1] else None,
                    prob=list(machine.prob) if machine.prob else None,
                    slot=slot.as_pair() if slot else None,
                    vol=vol.as_pair() if vol else None,
                    pan='random' if (pan and pan.span > 1) else 'side'))
                machine, cmps, speed = _Machine(), [], None
        out[name] = blocks
        if verbose:
            print('==', name)
            for k in blocks:
                print('   ', k)
    return out


# -------------------------------------------------------------- tables -----

def tram_numbers(b):
    return {n: b[fo_data(TRAM_INDEX) + i] for i, n in enumerate(NAMES)}


def volumes(b):
    return {n: struct.unpack_from('<I', b, fo_data(TRAM_VOLUME) + 4 * i)[0]
            for i, n in enumerate(NAMES)}


def loop_zones(b):
    out = {}
    for i, n in enumerate(NAMES):
        p = struct.unpack_from('<I', b, fo_data(LOOP_TABLE) + 4 * i)[0]
        zones = []
        while p:
            rec = struct.unpack_from('<7i', b, fo_data(p))
            if rec[0] < 0:
                break
            zones.append(dict(start=rec[0], fade_in_end=rec[1], fade_out_start=rec[2],
                              end=rec[3], slot=rec[4], side=rec[5], volume=rec[6]))
            p += 28
        out[n] = zones
    return out


def object_sounds(b):
    """Per animated object, in FRD order: {radius, ?, sound, ranges..., mode}."""
    ptrs = [struct.unpack_from('<I', b, fo_data(OBJECT_TABLE) + 4 * i)[0] for i in range(19)]
    order = sorted(set(p for p in ptrs if p))
    out = {}
    for i, n in enumerate(NAMES):
        p = ptrs[i]
        if not p:
            out[n] = []
            continue
        end = min([q for q in order if q > p] + [p + 0x12 * 12])
        out[n] = [list(struct.unpack_from('<9h', b, fo_data(p) + 0x12 * k))
                  for k in range((end - p) // 0x12)]
    return out


def extract(game_root):
    b = open(os.path.join(game_root, EXE), 'rb').read()
    return dict(tram_bank=tram_numbers(b), volume=volumes(b), loop_zones=loop_zones(b),
                object_sounds=object_sounds(b), oneshot_scripts=decode_scripts(b))


def main():
    game = nfs5cfg.NFS4_ROOT
    out = sys.argv[1] if len(sys.argv) > 1 else 'nfs4_ambient.json'
    data = extract(game)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    blocks = sum(len(v) for v in data['oneshot_scripts'].values())
    solved = sum(1 for v in data['oneshot_scripts'].values() for k in v if k['slot'])
    print('wrote %s: %d loop zones, %d one-shot blocks (%d with a slot)'
          % (out, sum(len(v) for v in data['loop_zones'].values()), blocks, solved))


if __name__ == '__main__':
    main()
