"""Re-emit a shipped track's .crp through this project's own writer.

    python roundtrip_crp.py <in.crp> <out.crp>

A converted track loads, collides and drives, but draws nothing: the engine
submits four calls and 102 triangles a frame, which is the sky dome alone.
Copying a stock track's whole file set into a converted track's slot makes it
draw, so the fault is in the .crp this project produces and not in the .edg,
.jnc, .map, .env, .lsp or the roster entry.

This narrows that down by one more step. It reads a stock archive into the
writer's own model and writes it straight back out. If the result still draws,
the writer is sound and the fault is upstream in the High Stakes geometry
conversion; if it stops drawing, the writer is dropping something, and diffing
the two files says what.

What the model does not carry is worth stating, because it is what a failure
here would point at: the `Base` flag word (the writer composes its own), `ef`
parts, and `Anim`/`Anqt` keyframes.
"""

import os
import sys

import nfs5_crp as reader
from nfs5_crp_writer import Article, build_crp
from test_crp_writer import extract


class RawMisc:
    """A MiscData entry copied byte for byte, tag and all."""

    def __init__(self, tag, info, type_byte, count, blob):
        self.tag = tag
        self.info = info
        self.type_byte = type_byte
        self.count = count
        self.blob = blob


# The 16-byte entry stores the tag reversed in its first four bytes; a two-char
# tag keeps its index in the low half instead, which is how `mt` records are
# numbered. Both come back out of the writer through the same _entry().
def read_misc(crp):
    materials, others = [], []
    for i, m in enumerate(crp.misc()):
        raw = crp.data[crp.misc_base + i * 16:crp.misc_base + i * 16 + 4]
        tag4 = raw[::-1].decode('latin1').rstrip('\0')
        blob = crp.data[m['data']:m['data'] + m['length']]
        entry = RawMisc(tag4 if len(tag4) == 4 else m['tag'], m['index'],
                        m['type_byte'], m['count'], blob)
        (materials if m['tag'] == 'mt' else others).append(entry)
    return materials, others


def main():
    src, dst = sys.argv[1], sys.argv[2]
    crp = reader.read(src)

    # Everything the writer's model does not describe travels verbatim: the
    # `Base` flag word, `ef`, `Anim` and `Anqt`. Without that the first attempt
    # was not a fair test - dropping the flag word alone left WSceneBundle
    # holding nothing, and the run died at 004762BD on a null.
    #
    # A few articles key their vt/uv/df on a variant index (info 0010, 0020, …)
    # instead of a plain LOD, which the model cannot express at all; those are
    # copied part for part.
    PASSTHROUGH = ('ef', 'Anim', 'Anqt')
    articles, verbatim = [], 0
    for i in range(crp.n_articles):
        raw = [(p['tag'], p['info'], p['type_byte'], p['count'],
                crp.data[p['data']:p['data'] + p['length']])
               for p in crp.parts(i) if p['tag'] in PASSTHROUGH]
        base = next((p for p in crp.parts(i) if p['tag'] == 'Base'), None)
        raw_base = crp.data[base['data']:base['data'] + base['length']] if base else None

        art = extract(crp, i)
        art.raw_base = raw_base
        art.raw_parts = raw
        # With raw_base in place encode_base() never runs, so the variant
        # articles no longer announce themselves by raising - the LOD key is
        # what gives them away.
        if art.lods and max(art.lods) > 15:
            art = Article(crp.name(i) or '', raw_base=raw_base, raw_parts=[
                (p['tag'], p['info'], p['type_byte'], p['count'],
                 crp.data[p['data']:p['data'] + p['length']])
                for p in crp.parts(i) if p['tag'] not in ('Base', 'Name')])
            verbatim += 1
        articles.append(art)
    print('articles copied verbatim (variant encoding): %d' % verbatim)

    materials, others = read_misc(crp)

    blob = build_crp(articles, materials, extra_misc=others)
    with open(dst, 'wb') as f:
        f.write(blob)

    print('%s -> %s' % (os.path.basename(src), os.path.basename(dst)))
    print('  articles   %d' % len(articles))
    print('  materials  %d' % len(materials))
    print('  other MiscData %d: %s' % (
        len(others), ' '.join(sorted(set(m.tag for m in others)))))
    print('  was %d bytes, now %d' % (len(crp.data), len(blob)))

    back = reader.Crp(blob)
    print('  read back: articles %d, misc %d' % (back.n_articles, back.n_misc))


if __name__ == '__main__':
    main()
