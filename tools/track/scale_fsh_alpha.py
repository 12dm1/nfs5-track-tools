"""Scale one ARGB8888 FSH texture's alpha channel, including every mip."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ea_refpack import fsh_dir
from nfs5_fsh import CODE_ARGB8888


def scale_alpha(source_path, output_path, tag, divisor):
    data = bytearray(open(source_path, 'rb').read())
    if data[:4] != b'SHPI':
        raise ValueError('source is not an unpacked SHPI archive')
    _header, entries = fsh_dir(data)
    matches = [entry for entry in entries if entry['tag'] == tag]
    if len(matches) != 1:
        raise ValueError('expected one %s entry, found %d' % (tag, len(matches)))
    entry = matches[0]
    if entry['code'] != CODE_ARGB8888:
        raise ValueError('%s is not ARGB8888' % tag)

    offset = entry['off'] + 16
    width, height = entry['w'], entry['h']
    levels = changed = 0
    before_min, before_max = 255, 0
    after_min, after_max = 255, 0
    while True:
        pixels = width * height
        for i in range(pixels):
            alpha_at = offset + i * 4 + 3
            old = data[alpha_at]
            new = old // divisor
            before_min, before_max = min(before_min, old), max(before_max, old)
            after_min, after_max = min(after_min, new), max(after_max, new)
            if new != old:
                data[alpha_at] = new
                changed += 1
        levels += 1
        offset += pixels * 4
        if width == 1 and height == 1:
            break
        width, height = max(1, width // 2), max(1, height // 2)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'wb') as stream:
        stream.write(data)
    return dict(tag=tag, divisor=divisor, levels=levels, changed=changed,
                before=(before_min, before_max), after=(after_min, after_max))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('output')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--divisor', type=int, required=True)
    args = parser.parse_args()
    if args.divisor < 1:
        parser.error('--divisor must be positive')
    print(scale_alpha(args.source, args.output, args.tag, args.divisor))


if __name__ == '__main__':
    main()
