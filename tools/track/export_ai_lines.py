"""Export NFS4 vroad and authored AI lines for Blender inspection.

    python export_ai_lines.py Hometown ../../build/ai-lines

The OBJ contains edge polylines, not render geometry.  Import it together with
the road-context OBJ from export_hs_walls.py when the road surface is useful.
"""

import argparse
import os
from pathlib import Path

import nfs4_frd
import nfs5_track_files
import export_hs_walls
import nfs5cfg


GAME = nfs5cfg.GAME
TRACKS = nfs5cfg.HS_TRACKS


class ObjLines:
    def __init__(self, material_file):
        self.lines = ['# NFS4 AI-line diagnostic; Y is up',
                      'mtllib ' + material_file]
        self.vertex_count = 0

    def polyline(self, name, material, points, closed=True, lift=0.0):
        if not points:
            return
        self.lines += ['o ' + name, 'usemtl ' + material]
        first = self.vertex_count + 1
        for x, y, z in points:
            self.lines.append('v %.6f %.6f %.6f' % (x, y + lift, z))
        indices = list(range(first, first + len(points)))
        if closed and len(points) > 2:
            indices.append(first)
        self.lines.append('l ' + ' '.join(str(i) for i in indices))
        self.vertex_count += len(points)

    def segments(self, name, material, segments, lift=0.0):
        self.lines += ['o ' + name, 'usemtl ' + material]
        for segment in segments:
            first = self.vertex_count + 1
            for x, y, z in (segment['a'], segment['b']):
                self.lines.append('v %.6f %.6f %.6f' % (x, y + lift, z))
            self.lines.append('l %d %d' % (first, first + 1))
            self.vertex_count += 2

    def save(self, path):
        path.write_text('\n'.join(self.lines) + '\n', encoding='utf-8')


def racing_points(track, offsets):
    points = []
    for i, offset in enumerate(offsets):
        vr = track.vroad(i)
        points.append(nfs5_track_files._offset(vr['ref'], vr['right'], offset))
    return points


def export(track_name, output):
    source = os.path.join(TRACKS, track_name)
    track = nfs4_frd.read(os.path.join(source, 'tr.frd'))
    spdfa = Path(source, 'spdfa.bin').read_bytes()
    spdra = Path(source, 'spdra.bin').read_bytes()
    _speed_f, _aux_f, line_f = nfs5_track_files.decode_spd(spdfa, track.n_vroad)
    _speed_r, _aux_r, line_r = nfs5_track_files.decode_spd(spdra, track.n_vroad)

    output.mkdir(parents=True, exist_ok=True)
    stem = track_name.lower() + '-ai-lines'
    mtl = output / (stem + '.mtl')
    obj = output / (stem + '.obj')
    mtl.write_text(
        'newmtl vroad_center\nKd 0.05 0.35 1.0\n'
        'newmtl ai_forward\nKd 0.1 1.0 0.2\n'
        'newmtl ai_backward\nKd 1.0 0.1 0.8\n'
        'newmtl collision_wall\nKd 1.0 0.45 0.05\n', encoding='utf-8')

    writer = ObjLines(mtl.name)
    centre = [track.vroad(i)['ref'] for i in range(track.n_vroad)]
    closed = nfs5_track_files.is_circuit(track)
    writer.polyline('VROAD_CENTER', 'vroad_center', centre, closed, 0.10)
    writer.polyline('AI_FORWARD', 'ai_forward',
                    racing_points(track, line_f), closed, 0.25)
    writer.polyline('AI_BACKWARD', 'ai_backward',
                    racing_points(track, line_r), closed, 0.40)
    writer.segments('COLLISION_WALLS', 'collision_wall',
                    export_hs_walls.wall_segments(track), 0.15)
    writer.save(obj)
    print(obj)
    return obj


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('track', nargs='?', default='Hometown')
    parser.add_argument('output', nargs='?',
                        default=os.path.join(nfs5cfg.WORK, 'build', 'ai-lines'))
    args = parser.parse_args()
    export(args.track, Path(args.output))


if __name__ == '__main__':
    main()
