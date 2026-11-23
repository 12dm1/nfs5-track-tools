"""Add / replace sound banks in PU GameData/Sounds/track.viv.

track.viv is a BIGF archive.  PORSCHE.EXE (FUN_004affd0) indexes it by the
three digits before ".bnk" in each entry name (`name_NNN.bnk`, NNN < 1000),
so a new ambient sound only needs a bank file with an unused number; the
prefix is free text.  Used numbers in the stock file: 1-839 with holes,
989-999 (thunder).  Free: 9 14 30 46 61 138 215 237-250 431 486-504 519-524
530-549 558-750 840-988 990.
"""
import struct, sys, os


def read_viv(b):
    assert b[:4] == b'BIGF', b[:4]
    total, count, header = struct.unpack_from('>III', b, 4)
    pos, entries = 16, []
    for _ in range(count):
        off, ln = struct.unpack_from('>II', b, pos)
        end = b.index(0, pos + 8, header)
        entries.append((b[pos + 8:end].decode('latin1'), b[off:off + ln]))
        pos = end + 1
    return entries


def write_viv(entries, align=4):
    header = 16 + sum(9 + len(n.encode('latin1')) for n, _ in entries)
    header = (header + align - 1) & ~(align - 1)
    body, table, pos = bytearray(), bytearray(), header
    for name, data in entries:
        table += struct.pack('>II', pos, len(data)) + name.encode('latin1') + b'\0'
        body += data
        padded = (len(data) + align - 1) & ~(align - 1)
        body += b'\0' * (padded - len(data))
        pos += padded
    head = b'BIGF' + struct.pack('>III', header + len(body), len(entries), header)
    return bytes(head + table + b'\0' * (header - 16 - len(table)) + body)


def bank_id(name):
    return int(name[-7:-4])


def add_banks(viv_path, new, out_path=None):
    """new: {'label_900.bnk': bytes}; replaces an existing entry with the same id."""
    entries = read_viv(open(viv_path, 'rb').read())
    ids = {bank_id(n): i for i, (n, _) in enumerate(entries)}
    for name, data in new.items():
        bid = bank_id(name)
        if bid in ids:
            entries[ids[bid]] = (name, data)
        else:
            entries.append((name, data))
            ids[bid] = len(entries) - 1
    open(out_path or viv_path, 'wb').write(write_viv(entries))


def free_ids(viv_path):
    used = {bank_id(n) for n, _ in read_viv(open(viv_path, 'rb').read())}
    return [i for i in range(1, 1000) if i not in used]


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('usage: pu_track_viv.py <track.viv> <bank_NNN.bnk>... [-o out.viv]')
        sys.exit(1)
    args = sys.argv[1:]
    out = None
    if '-o' in args:
        i = args.index('-o'); out = args[i + 1]; del args[i:i + 2]
    viv, banks = args[0], args[1:]
    add_banks(viv, {os.path.basename(p): open(p, 'rb').read() for p in banks}, out)
    print('ok', out or viv)
