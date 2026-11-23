"""Reader for NFS5 (Porsche Unleashed) karT track archives (.crp / .drp).

Research artifact for HS1. Complements T007/T019 with what this pass decoded:

  * article names follow `RD%04dC (%d PRIMARY)` (road) and
    `CNK%04dL/R/E/B (%d MATERIAL)` (terrain). The first number is the starting
    slice of an 8-slice chunk; the number in brackets is the route/branch id.
  * `Base` part layout (bbox + bounding sphere + LOD switch distances).
  * `pr` part layout (InfoRows with levelIndex 0=vt / 2=uv / 3=df, IndexRows
    'Iv'/'Iu', then BYTE-sized indices -> max 256 verts per primitive).
  * part DataLength at +5 is uint24 LITTLE-endian (T019 correction), and
    DataOffset is relative to the part entry, in bytes.

    python nfs5_crp.py <track.crp> [--parts] [--base]
"""

import collections
import re
import struct
import sys

from ea_refpack import load_maybe_qfs

ARTICLE_SIZE = 16
MISC_SIZE = 16
PART_SIZE = 16

NAME_RE = re.compile(r"^(RD|CNK|OBJ)(\d+)([CLREB])?\s*\((\d+)\s*(.*)\)")


class Crp:
    def __init__(self, data: bytes):
        if data[:4] != b"karT":
            raise ValueError("not karT: %s" % data[:4])
        self.data = data
        header_info, self.n_misc, art_table_off = struct.unpack_from("<3I", data, 4)
        self.n_articles = header_info >> 5
        self.flags = header_info & 0x1F
        self.article_base = art_table_off * 16
        self.misc_base = self.article_base + self.n_articles * ARTICLE_SIZE

    # -- articles ----------------------------------------------------------

    def parts(self, index):
        """Part table of one article: list of dicts."""
        b = self.article_base + index * ARTICLE_SIZE
        _, table_len, table_off = struct.unpack_from("<3i", self.data, b + 4)
        table = b + table_off * 16
        out = []
        for j in range(table_len):
            p = table + j * PART_SIZE
            raw = self.data[p:p + 4]
            tag4 = raw[::-1]
            info = struct.unpack_from("<H", self.data, p)[0]
            out.append(dict(
                pos=p,
                raw=raw,
                tag=(tag4.decode("latin1") if tag4 in (b"Base", b"Name", b"Anim", b"Anqt")
                     else raw[2:4][::-1].decode("latin1")),
                info=info,
                type_byte=self.data[p + 4],
                # T019: uint24 LITTLE-endian, not big
                length=int.from_bytes(self.data[p + 5:p + 8], "little"),
                count=struct.unpack_from("<i", self.data, p + 8)[0],
                data=p + struct.unpack_from("<i", self.data, p + 12)[0],
            ))
        return out

    def name(self, index):
        for part in self.parts(index):
            if part["tag"] == "Name":
                return self.data[part["data"]:part["data"] + 80].split(b"\0")[0] \
                           .decode("latin1", "replace")
        return None

    def base(self, index):
        """Decoded `Base` part: bbox, centre, radius, LOD switch distances."""
        for part in self.parts(index):
            if part["tag"] != "Base":
                continue
            o, d = part["data"], self.data
            n_lod = struct.unpack_from("<I", d, o + 0x0C)[0]
            # LOD switch table: each descriptor is preceded by a 4-byte marker
            # (always 1), so the stride is 12 -- marker at 0x64 + i*12,
            # descriptor at 0x68 + i*12. Base is 112 bytes at 1 LOD, +24 per
            # extra LOD (136 at 3).
            lods = []
            for i in range(n_lod):
                lo, hi, idx, dist = struct.unpack_from("<4H", d, o + 0x68 + i * 12)
                lods.append(dict(lo=lo, hi=hi, lod=idx, distance=dist))
            return dict(
                version=struct.unpack_from("<H", d, o)[0],
                n_lod=n_lod,
                bbox_min=struct.unpack_from("<3f", d, o + 0x14),
                bbox_max=struct.unpack_from("<3f", d, o + 0x20),
                centre=struct.unpack_from("<3f", d, o + 0x2C),
                radius=struct.unpack_from("<f", d, o + 0x3C)[0],
                lods=lods,
            )
        return None

    def triangles(self, part):
        """Decoded `pr` part header: material, info rows, index rows."""
        o, d = part["data"], self.data
        n_info, n_index = struct.unpack_from("<2i", d, o + 0x28)
        info_rows, index_rows = [], []
        q = o + 0x30
        for _ in range(n_info):
            f0, data_off, length, level = struct.unpack_from("<IiHH", d, q)
            info_rows.append(dict(f0=f0, data_offset=data_off,
                                  length=length, level=level))
            q += 16
        for _ in range(n_index):
            idx = struct.unpack_from("<H", d, q)[0]
            tag = d[q + 2:q + 4][::-1].decode("latin1")
            off = struct.unpack_from("<I", d, q + 4)[0]
            index_rows.append(dict(index=idx, tag=tag, offset=off))
            q += 8
        return dict(
            fill_mode=d[o],
            material=struct.unpack_from("<h", d, o + 4)[0],
            centre=struct.unpack_from("<3f", d, o + 8),
            radius=struct.unpack_from("<f", d, o + 0x14)[0],
            info_rows=info_rows,
            index_rows=index_rows,
            index_data=q,
            count=part["count"],
        )

    def vertices(self, part):
        return [struct.unpack_from("<4f", self.data, part["data"] + i * 16)
                for i in range(part["count"])]

    # -- misc data (materials) ---------------------------------------------

    def misc(self):
        out = []
        for i in range(self.n_misc):
            b = self.misc_base + i * MISC_SIZE
            tag = bytes([self.data[b + 3], self.data[b + 2]]).decode("latin1")
            off = b + struct.unpack_from("<i", self.data, b + 12)[0]
            entry = dict(index=struct.unpack_from("<H", self.data, b)[0],
                         tag=tag, data=off,
                         type_byte=self.data[b + 4],
                         length=int.from_bytes(self.data[b + 5:b + 8], "little"),
                         count=struct.unpack_from("<i", self.data, b + 8)[0])
            if tag == "mt":
                entry["desc"] = self.data[off + 0x10:off + 0x20].split(b"\0")[0] \
                                    .decode("latin1", "replace")
                entry["fsh_tag"] = self.data[off + 0x28:off + 0x2C] \
                                       .decode("latin1", "replace")
            out.append(entry)
        return out

    # -- convenience -------------------------------------------------------

    def branches(self):
        """{branch id: set of chunk start slices} from RD article names."""
        out = collections.defaultdict(set)
        for i in range(self.n_articles):
            m = NAME_RE.match(self.name(i) or "")
            if m and m.group(1) == "RD":
                out[int(m.group(4))].add(int(m.group(2)))
        return out


def read(path: str) -> Crp:
    return Crp(load_maybe_qfs(path))


if __name__ == "__main__":
    path = sys.argv[1]
    crp = read(path)
    print(f"{path}: articles={crp.n_articles} misc={crp.n_misc} flags={crp.flags:#x}")
    branches = crp.branches()
    total_chunks = sum(max(v) // 8 + 1 for v in branches.values())
    print(f"branches={len(branches)} chunks={total_chunks} "
          f"slices={total_chunks * 8} ids={sorted(branches)}")
    kinds = collections.Counter(
        re.sub(r"\d+", "#", crp.name(i) or "<none>")[:24]
        for i in range(crp.n_articles))
    for k, v in kinds.most_common(12):
        print(f"  {v:5d} {k}")
    mats = [m for m in crp.misc() if m["tag"] == "mt"]
    print(f"materials={len(mats)} sample={[m['fsh_tag'] for m in mats[:8]]}")
    if "--base" in sys.argv:
        print("article 0:", crp.name(0), crp.base(0))
    if "--parts" in sys.argv:
        for p in crp.parts(0):
            print(f"  {p['tag']:5s} info={p['info']:#06x} len={p['length']:6d} "
                  f"count={p['count']}")
