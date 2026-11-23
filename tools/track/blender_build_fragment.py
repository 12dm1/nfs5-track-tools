"""Blender side of export_track_fragment.py (runs inside blender --background).

    blender --background --factory-startup --python blender_build_fragment.py -- <dir> <out base>

Reads <dir>/fragment.json + fragment_mesh.npz, writes <base>.blend (images
packed) and <base>.glb.
"""

import json
import os
import sys

import bpy
import numpy as np


def log(*a):
    print('[frag]', *a, flush=True)


def socket(sockets, identifier):
    for s in sockets:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


def make_material(m, root):
    mat = bpy.data.materials.new(m['name'])
    mat.use_nodes = True
    mat.use_backface_culling = False
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get('Principled BSDF')
    bsdf.inputs['Roughness'].default_value = 0.85
    bsdf.inputs['Specular IOR Level'].default_value = 0.2

    path = os.path.join(root, m['image'])
    img = bpy.data.images.get(os.path.basename(path)) or bpy.data.images.load(path)
    img.pack()
    tex = nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.location = (-700, 300)

    vc = nodes.new('ShaderNodeVertexColor')
    vc.layer_name = 'baked_light'
    vc.location = (-700, 0)

    mix = nodes.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    mix.label = 'baked light'
    mix.location = (-350, 200)
    socket(mix.inputs, 'Factor_Float').default_value = 1.0
    links.new(tex.outputs['Color'], socket(mix.inputs, 'A_Color'))
    links.new(vc.outputs['Color'], socket(mix.inputs, 'B_Color'))
    links.new(socket(mix.outputs, 'Result_Color'), bsdf.inputs['Base Color'])

    if m['mode'] == 'clip':
        rnd = nodes.new('ShaderNodeMath')      # round(a) == a >= 0.5; glTF reads it as MASK
        rnd.operation = 'ROUND'
        rnd.location = (-350, -100)
        links.new(tex.outputs['Alpha'], rnd.inputs[0])
        links.new(rnd.outputs[0], bsdf.inputs['Alpha'])
        mat.surface_render_method = 'DITHERED'
    elif m['mode'] == 'blend':
        links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
        mat.surface_render_method = 'BLENDED'
    mat['fsh_tag'] = m['fsh_tag']
    mat['game_desc'] = m['game_desc']
    return mat


def build_mesh(name, pos, uv, col, mat):
    """pos/uv/col are per triangle corner; weld equal positions into shared verts."""
    key = np.round(pos.astype(np.float64), 4)
    verts, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    tri = inv.reshape(-1, 3)
    good = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    tri = tri[good]
    g3 = np.repeat(good, 3)
    uv, col, mat = uv[g3], col[g3], mat[good]
    nt = len(tri)

    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts))
    me.vertices.foreach_set('co', verts.astype(np.float32).ravel())
    me.loops.add(nt * 3)
    me.loops.foreach_set('vertex_index', tri.astype(np.int32).ravel())
    me.polygons.add(nt)
    me.polygons.foreach_set('loop_start', np.arange(0, nt * 3, 3, dtype=np.int32))
    me.polygons.foreach_set('material_index', mat.astype(np.int32))
    me.update(calc_edges=True)
    me.uv_layers.new(name='UVMap').data.foreach_set('uv', uv.astype(np.float32).ravel())
    ca = me.color_attributes.new('baked_light', 'BYTE_COLOR', 'CORNER')
    ca.data.foreach_set('color_srgb', (col.astype(np.float32) / 255.0).ravel())
    me.color_attributes.active_color = ca
    me.validate(clean_customdata=False)
    return me


def main():
    root, base = sys.argv[sys.argv.index('--') + 1:][:2]
    meta = json.load(open(os.path.join(root, 'fragment.json'), encoding='utf-8'))
    d = np.load(os.path.join(root, 'fragment_mesh.npz'))
    pos = d['pos'].reshape(-1, 3)
    uv, col, mats, offs = d['uv'], d['col'], d['mat'], d['tri_offsets']

    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    scene = bpy.context.scene
    scene.name = '%s_%s' % (meta['track'], meta['tag'])
    scene.unit_settings.system = 'METRIC'

    materials = [make_material(m, root) for m in meta['materials']]

    top = bpy.data.collections.new('%s_%s' % (meta['track'], meta['tag']))
    scene.collection.children.link(top)
    cols = {}

    def coll(name):
        if name not in cols:
            cols[name] = bpy.data.collections.new(name)
            top.children.link(cols[name])
        return cols[name]

    tris = 0
    for i, o in enumerate(meta['objects']):
        a, b = int(offs[i]), int(offs[i + 1])
        m = mats[a:b]
        used = np.unique(m)
        remap = np.zeros(len(materials), np.int32)
        remap[used] = np.arange(len(used))
        me = build_mesh(o['name'], pos[a * 3:b * 3], uv[a * 3:b * 3], col[a * 3:b * 3], remap[m])
        for u in used:
            me.materials.append(materials[u])
        ob = bpy.data.objects.new(o['name'], me)
        ob['crp_article'] = o['article']
        ob['clipped_at_region_edge'] = o['clipped']
        coll(o['collection']).objects.link(ob)
        tris += len(me.polygons)

    lines = coll('Centrelines')
    for name, pts in meta['centrelines'].items():
        if len(pts) < 2:
            continue
        cu = bpy.data.curves.new('centreline_' + name, 'CURVE')
        cu.dimensions = '3D'
        sp = cu.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        for p, xyz in zip(sp.points, pts):
            p.co = (xyz[0], xyz[1], xyz[2], 1.0)
        lines.objects.link(bpy.data.objects.new('centreline_' + name, cu))

    world = scene.world or bpy.data.worlds.new('World')
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs['Color'].default_value = (0.55, 0.68, 0.85, 1.0)
        bg.inputs['Strength'].default_value = 1.0
    sun = bpy.data.lights.new('Sun', 'SUN')
    sun.energy = 3.0
    sun_ob = bpy.data.objects.new('Sun', sun)
    sun_ob.rotation_euler = (0.8, 0.2, 0.6)
    scene.collection.objects.link(sun_ob)

    for scr in bpy.data.screens:
        for area in scr.areas:
            if area.type == 'VIEW_3D':
                for sp in area.spaces:
                    if sp.type == 'VIEW_3D':
                        sp.shading.type = 'MATERIAL'
                        sp.clip_end = 5000.0

    cams = add_cameras(scene, pos, coll('Cameras'))

    meta_txt = bpy.data.texts.new('README.md')
    meta_txt.from_string(open(os.path.join(root, 'README.md'), encoding='utf-8').read())

    bpy.ops.wm.save_as_mainfile(filepath=base + '.blend', compress=True)
    log('blend', base + '.blend', len(meta['objects']), 'objects', tris, 'faces')

    try:
        bpy.ops.export_scene.gltf(filepath=base + '.glb', export_format='GLB',
                                  export_apply=False, export_yup=True)
        log('glb', base + '.glb')
    except Exception as e:                      # the .blend is the main product
        log('glb export failed:', e)

    try:
        scene.render.engine = 'BLENDER_EEVEE'
        scene.render.image_settings.file_format = 'PNG'
        for cam, fn in cams:
            scene.camera = cam
            scene.render.filepath = os.path.join(root, fn)
            bpy.ops.render.render(write_still=True)
            log('preview', fn)
    except Exception as e:
        log('preview render failed:', e)


def add_cameras(scene, pos, coll):
    """Top-down ortho over the whole fragment + one oblique overview."""
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    cx, cy = (lo[:2] + hi[:2]) / 2
    w, h = hi[0] - lo[0], hi[1] - lo[1]
    scene.render.resolution_x = 2048
    scene.render.resolution_y = max(512, int(2048 * h / max(w, 1e-3)))
    top = bpy.data.cameras.new('cam_top')
    top.type = 'ORTHO'
    top.ortho_scale = float(max(w, h)) * 1.02
    top.clip_end = 5000.0
    top_ob = bpy.data.objects.new('cam_top', top)
    top_ob.location = (cx, cy, hi[2] + 200.0)
    coll.objects.link(top_ob)

    per = bpy.data.cameras.new('cam_persp')
    per.lens = 28
    per.clip_end = 5000.0
    per_ob = bpy.data.objects.new('cam_persp', per)
    r = float(max(w, h)) * 0.55
    per_ob.location = (cx - r * 0.6, cy - r * 0.8, r * 0.45)
    per_ob.rotation_euler = (np.radians(62), 0.0, np.radians(-36.87))
    coll.objects.link(per_ob)
    return [(top_ob, 'preview_top.png'), (per_ob, 'preview_persp.png')]


main()
