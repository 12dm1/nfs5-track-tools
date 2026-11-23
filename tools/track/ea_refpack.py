"""EA RefPack (QFS) decompression + SHPI/FSH directory reader.

Research artifact for HS1 (NFS4 High Stakes -> NFS5 Porsche Unleashed track
migration). Both games use the same container family:

  NFS4:  Data/Tracks/<name>/tr0.qfs, sky.qfs   -> RefPack(SHPI), PAL8 pixels
  NFS5:  GameData/Track/<name>.fsh, .env       -> RefPack(SHPI) or plain SHPI,
                                                  ARGB8888 pixels with full mip chain

Read-only for now. The writer (SHPI + mip chain, needed by HS2) is not here yet.

    python ea_refpack.py <file.qfs|file.fsh|file.env>
"""

import struct
import sys

# ---------------------------------------------------------------- RefPack ---

def refpack_decompress(data: bytes) -> bytes:
    """Decompress an EA RefPack (0x10FB) stream. Raises ValueError if not one."""
    if (data[0] & 0xFE) != 0x10 or data[1] != 0xFB:
        raise ValueError("not refpack: %s" % data[:2].hex())
    flags = data[0]
    o = 2
    if flags & 0x01:                       # 4-byte uncompressed size
        o += 4
    else:                                  # 3-byte uncompressed size (BE)
        o += 3

    out = bytearray()
    while o < len(data):
        b0 = data[o]; o += 1
        if b0 < 0x80:                      # 2-byte command
            b1 = data[o]; o += 1
            plain = b0 & 3
            offset = ((b0 & 0x60) << 3) + b1 + 1
            count = ((b0 & 0x1C) >> 2) + 3
        elif b0 < 0xC0:                    # 3-byte command
            b1, b2 = data[o], data[o + 1]; o += 2
            plain = (b1 >> 6) & 3
            offset = ((b1 & 0x3F) << 8) + b2 + 1
            count = (b0 & 0x3F) + 4
        elif b0 < 0xE0:                    # 4-byte command
            b1, b2, b3 = data[o], data[o + 1], data[o + 2]; o += 3
            plain = b0 & 3
            offset = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1
            count = ((b0 & 0x0C) << 6) + b3 + 5
        elif b0 < 0xFC:                    # plain-only run
            plain = ((b0 & 0x1F) << 2) + 4
            offset = count = 0
        else:                              # terminator
            plain = b0 & 3
            offset = count = 0

        if plain:
            out += data[o:o + plain]; o += plain
        if count:
            src = len(out) - offset
            for i in range(count):
                out.append(out[src + i])
        if b0 >= 0xFC:
            break
    return bytes(out)


def refpack_compress_stored(data: bytes) -> bytes:
    """Wrap `data` in a literal-only RefPack stream.

    DO NOT USE for files the game reads. The stream round-trips through
    `refpack_decompress` above at every size from 0 to 2.5 MB, but the game
    rejects it: a track whose .fsh was written this way dies with
    "FILE_loadpack - unable to unpack file" from real\\cmn\\loadpack.c:146.
    The header is fine - the dispatcher at 005713A0 accepts byte[1] == 0xFB and
    indexes its jump table on byte[0] & 0xFE - so the mismatch is inside the
    decoder proper (one of 00594760 / 00594400 / 00593800 / 00593640), and
    whatever it expects of literal runs is not what this emits.

    Kept because the game reads UNCOMPRESSED .fsh, .env and .crp perfectly well
    (alps.crp ships that way), so compression is not needed - and because the
    round-trip here is a useful reminder that agreeing with your own decoder
    proves self-consistency, not compatibility.

    Opcodes 0xE0..0xFB carry a run of ((b0 & 0x1F) << 2) + 4 literal bytes, so
    0xFB is the maximum 112-byte run, and 0xFC..0xFF terminates with 0..3
    trailing literals.
    """
    out = bytearray(b'\x10\xfb')
    out += len(data).to_bytes(3, 'big')
    # Runs carry a multiple of four bytes and the terminator only 0..3, so the
    # tail is exactly len % 4 and everything before it splits into <=112-byte
    # runs.
    tail = len(data) % 4
    o = 0
    while o < len(data) - tail:
        run = min(112, len(data) - tail - o)
        out.append(0xE0 | ((run - 4) >> 2))
        out += data[o:o + run]
        o += run
    out.append(0xFC | tail)
    out += data[o:]
    return bytes(out)


def load_maybe_qfs(path: str) -> bytes:
    """Read a file, transparently decompressing it if it is RefPack."""
    d = open(path, "rb").read()
    if len(d) > 2 and (d[0] & 0xFE) == 0x10 and d[1] == 0xFB:
        return refpack_decompress(d)
    return d


# ------------------------------------------------------------- SHPI / FSH ---

PIXEL_FORMATS = {
    0x60: "DXT1", 0x61: "DXT3", 0x6D: "ARGB4444", 0x78: "RGB565",
    0x7B: "PAL8", 0x7D: "ARGB8888", 0x7E: "ARGB1555", 0x7F: "RGB888",
}


def fsh_dir(data: bytes):
    """Parse a SHPI archive. Returns (header dict, list of entry dicts)."""
    if data[:4] != b"SHPI":
        raise ValueError("not SHPI: %s" % data[:4])
    size, num, dirid = struct.unpack_from("<II4s", data, 4)
    entries = []
    for i in range(num):
        tag, off = struct.unpack_from("<4sI", data, 16 + i * 8)
        code = data[off]
        entries.append(dict(
            tag=tag.decode("latin1"),
            off=off,
            code=code,
            fmt=PIXEL_FORMATS.get(code & 0x7F, hex(code)),
            # block size: NFS4 fills this in, NFS5 leaves it 0
            blk=int.from_bytes(data[off + 1:off + 4], "little"),
            w=struct.unpack_from("<H", data, off + 4)[0],
            h=struct.unpack_from("<H", data, off + 6)[0],
            # NFS5: byte +15 is log2(size) << 4 (128 -> 0x70, 256 -> 0x80)
            size_exp=data[off + 15] >> 4,
        ))
    return dict(size=size, num=num, dirid=dirid.decode("latin1")), entries


def mipchain_bytes(w: int, h: int, bpp: int = 4) -> int:
    """Size of a full mip chain down to 1x1, as NFS5 stores it (16-byte aligned,
    excluding the 16-byte entry header)."""
    total = 0
    while w >= 1 and h >= 1:
        total += w * h * bpp
        if w == 1 and h == 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    return total


if __name__ == "__main__":
    for p in sys.argv[1:]:
        d = load_maybe_qfs(p)
        head, entries = fsh_dir(d)
        print(f"{p}: {head} entries={len(entries)}")
        for e in entries:
            print(f"   {e['tag']!r:8s} {e['fmt']:9s} {e['w']:4d}x{e['h']:<4d} "
                  f"off={e['off']:<9d} blk={e['blk']:<7d} size_exp={e['size_exp']}")
