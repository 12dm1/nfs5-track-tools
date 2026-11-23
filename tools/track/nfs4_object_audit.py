"""Inventory NFS4 object candidates and build a Hometown visual test scene.

The generated scene is intentionally a debug artifact.  It imports the local
geometry of NFS4 type 6 (physics) objects and a small sample of type 3
animated objects, places them in a row near Hometown's first vroad point, and
adds a `hometown_fstart.scn` layout.  Texture ids are remapped to Hometown's
first material: the first pass is about shape, scale, placement and collision,
not texture fidelity.

Usage:
    python nfs4_object_audit.py --audit
    python nfs4_object_audit.py --debug --out ../../build/hs-object-debug
"""

import argparse
import copy
import csv
import json
import math
import os
import shutil
import struct
import sys

import nfs4_frd as frd
import nfs4_geometry as geometry
import nfs5cfg


GAME = nfs5cfg.GAME
HS_ROOT = nfs5cfg.HS_TRACKS
TRACK_ROOT = os.path.join(GAME, 'GameData', 'Track')
TEMPLATE_CRP = os.path.join(TRACK_ROOT, 'alps.crp')


def _json_object(track_name, block, index, obj):
    pts = obj['verts']
    lo = tuple(min(v[k] for v in pts) for k in range(3)) if pts else (0, 0, 0)
    hi = tuple(max(v[k] for v in pts) for k in range(3)) if pts else (0, 0, 0)
    p = obj.get('physics') or {}
    return {
        'track': track_name,
        'block': block,
        'index': index,
        'type': obj['type'],
        'position': obj['pt'],
        'vertices': len(obj['verts']),
        'polygons': len(obj['polygons']),
        'local_bbox_min': lo,
        'local_bbox_max': hi,
        'mass': p.get('mass'),
        'collision_dimensions': p.get('collision_dimensions'),
    }


def collect():
    rows = []
    for name in sorted(os.listdir(HS_ROOT)):
        src = os.path.join(HS_ROOT, name, 'tr.frd')
        if not os.path.isfile(src):
            continue
        track = frd.read(src)
        for block in range(track.n_blocks):
            for index, obj in enumerate(track.objects(block)):
                if obj['type'] in (3, 6):
                    rows.append((name, block, index, obj,
                                 _json_object(name, block, index, obj)))
        for index, obj in enumerate(track.global_objects_flat()):
            if obj['type'] in (3, 6):
                label = 'global'
                row = _json_object(name, label, index, obj)
                rows.append((name, label, index, obj, row))
    return rows


def write_obj(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='ascii') as f:
        f.write('# NFS4 object audit export\n')
        for x, y, z in obj['verts']:
            f.write('v %.6f %.6f %.6f\n' % (x, y, z))
        for poly in obj['polygons']:
            v = poly['vertex']
            if v[2] == v[3]:
                f.write('f %d %d %d\n' % (v[0] + 1, v[1] + 1, v[2] + 1))
            else:
                f.write('f %d %d %d\n' % (v[0] + 1, v[1] + 1, v[2] + 1))
                f.write('f %d %d %d\n' % (v[0] + 1, v[2] + 1, v[3] + 1))


def write_audit(rows, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'nfs4_object_candidates.json'), 'w',
              encoding='utf-8') as f:
        json.dump([r[4] for r in rows], f, indent=2)
    fields = list(rows[0][4]) if rows else []
    with open(os.path.join(out_dir, 'nfs4_object_candidates.csv'), 'w',
              newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows([r[4] for r in rows])
    for n, (track, block, index, obj, _row) in enumerate(rows):
        write_obj(os.path.join(out_dir, 'obj',
                               '%04d_%s_b%s_o%03d_t%d.obj'
                               % (n, track, block, index, obj['type'])), obj)


def _fourcc(name):
    return int.from_bytes(name.encode('ascii'), 'little')


def _debug_scn(placements):
    out = []
    for name, pos in placements:
        out.extend([
            'GEOM_ELEMENT 5',
            '%.6f %.6f %.6f' % pos,
            '1.000000 0.000000 0.000000\t0.000000 1.000000 0.000000\t0.000000 0.000000 1.000000',
            '0 %d 0 0 -1 1 1 100.000000 100.000000 100.000000' % _fourcc(name),
            'NFS4 debug %s' % name,
        ])
    return '\n'.join(out) + '\n'


def make_debug(rows, out_root, limit=40):
    track_dir = os.path.join(out_root, 'GameData', 'Track')
    os.makedirs(track_dir, exist_ok=True)
    for filename in os.listdir(TRACK_ROOT):
        src = os.path.join(TRACK_ROOT, filename)
        dst = os.path.join(track_dir, filename)
        if os.path.isfile(src) and filename.lower().startswith('hometown'):
            shutil.copy2(src, dst)
    sky_src = os.path.join(TRACK_ROOT, 'Sky')
    sky_dst = os.path.join(track_dir, 'Sky')
    if os.path.isdir(sky_src):
        shutil.copytree(sky_src, sky_dst, dirs_exist_ok=True)
    cops_src = os.path.join(TRACK_ROOT, 'Cops')
    cops_dst = os.path.join(track_dir, 'Cops')
    if os.path.isdir(cops_src):
        shutil.copytree(cops_src, cops_dst, dirs_exist_ok=True)

    hometown = frd.read(os.path.join(HS_ROOT, 'Hometown', 'tr.frd'))
    vr = hometown.vroad(0)
    origin, forward, right = vr['ref'], vr['forward'], vr['right']
    # Put physical candidates first; the first test drive should show the
    # objects most likely to correspond to NFS4's breakable scenery.
    chosen = sorted(rows, key=lambda r: (0 if r[3]['type'] == 6 else 1,
                                         r[0], str(r[1]), r[2]))[:limit]
    extras = []
    placements = []
    manifest = []
    for i, (source, block, index, original, row) in enumerate(chosen):
        tag = 'D%03d' % i
        obj = copy.deepcopy(original)
        # Hometown's texture archive is deliberately used for all debug
        # objects.  Remapping avoids out-of-range source texture references.
        for poly in obj['polygons']:
            poly['texture_id'] = 0
        col = i % 8
        line = i // 8
        lateral = (col - 3.5) * 5.0
        along = 25.0 + line * 5.0
        pos = tuple(origin[k] + forward[k] * along + right[k] * lateral
                    for k in range(3))
        # Bake the debug destination into the article as well.  Hometown has
        # no normal race layout SCN, so the CRP must remain visible even when
        # the optional SCN loader is not invoked for this track.
        extras.append({'name': tag, 'object': obj, 'offset': pos,
                       'surface': geometry.SURFACE_SOLID})
        placements.append((tag, pos))
        item_manifest = dict(row)
        item_manifest.update(debug_name=tag, position=pos)
        manifest.append(item_manifest)

    out_crp = os.path.join(track_dir, 'hometown.crp')
    geometry.convert(os.path.join(HS_ROOT, 'Hometown'), out_crp,
                     TEMPLATE_CRP, name='hometown', extra_objects=extras)
    with open(os.path.join(track_dir, 'hometown_fstart.scn'), 'w',
              encoding='ascii') as f:
        f.write(_debug_scn(placements))
    with open(os.path.join(out_root, 'nfs4_debug_manifest.json'), 'w',
              encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    return len(chosen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--out', default=os.path.join(nfs5cfg.WORK, 'build',
                                                   'hs-object-debug'))
    ap.add_argument('--limit', type=int, default=40)
    args = ap.parse_args()
    if not args.audit and not args.debug:
        ap.error('use --audit and/or --debug')
    rows = collect()
    audit_dir = os.path.join(args.out, 'audit')
    write_audit(rows, audit_dir)
    if args.debug:
        n = make_debug(rows, args.out, args.limit)
        print('debug Hometown: %d objects -> %s' % (n, args.out))
    print('candidates: %d (type3=%d type6=%d)' %
          (len(rows), sum(r[3]['type'] == 3 for r in rows),
           sum(r[3]['type'] == 6 for r in rows)))


if __name__ == '__main__':
    main()
