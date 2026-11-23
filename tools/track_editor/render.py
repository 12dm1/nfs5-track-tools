"""GL side of the preview: track buffers -> fogged `texture * vertex colour`."""

import numpy as np


VERTEX = """
#version 330
uniform mat4 uMvp;
uniform sampler2D uTint;
uniform int uTintN;
in vec3 in_pos;
in vec2 in_uv;
in vec4 in_col;
in int in_slice;
out vec2 v_uv;
out vec4 v_col;
out float v_w;
void main() {
    gl_Position = uMvp * vec4(in_pos, 1.0);
    v_w = gl_Position.w;
    v_uv = in_uv;
    // ATM2 6: df' = clamp(df * tint) by the slice of the article the corner came from
    vec3 tint = vec3(1.0);
    if (in_slice >= 0 && in_slice < uTintN)
        tint = texelFetch(uTint, ivec2(in_slice, 0), 0).rgb;
    v_col = vec4(clamp(in_col.rgb * tint, 0.0, 1.0), in_col.a);
}
"""

# dx7z guFogGenerateExp, as in wrapper/src/dx11_shaders.h ApplyFog:
#   f = saturate((1 - exp(-d * w)) / (1 - exp(-d * w63)))
# `d` arrives already cubed (SkyBin.fog_at), `scale` = 1 / (1 - exp(-d * w63)).
FOG_GLSL = """
vec3 apply_fog(vec3 rgb, float w, vec3 fog_colour, float d, float scale) {
    float f = clamp((1.0 - exp(-d * w)) * scale, 0.0, 1.0);
    return mix(rgb, fog_colour, f);
}
"""

FRAGMENT = """
#version 330
uniform sampler2D uTex;
uniform bool uAlphaTest;
uniform vec3 uFogColour;
uniform float uFogDensity;
uniform float uFogScale;
uniform vec3 uHighlight;
uniform bool uHidden;
in vec2 v_uv;
in vec4 v_col;
in float v_w;
out vec4 f_col;
""" + FOG_GLSL + """
void main() {
    if (uHidden) discard;
    vec4 t = texture(uTex, v_uv);
    if (uAlphaTest && t.a < 0.5) discard;
    vec3 rgb = t.rgb * v_col.rgb * uHighlight;
    f_col = vec4(apply_fog(rgb, v_w, uFogColour, uFogDensity, uFogScale), 1.0);
}
"""


class TrackRenderer:
    def __init__(self, ctx, mesh):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        n = len(mesh.positions)
        buf = np.zeros(n, dtype=[('pos', '<f4', 3), ('uv', '<f4', 2), ('col', 'u1', 4),
                                 ('slice', '<i4')])
        # The game is left-handed, GL right-handed: mirror Z once here.
        buf['pos'] = mesh.positions * np.array([1.0, 1.0, -1.0], np.float32)
        buf['uv'] = mesh.uvs
        buf['col'] = mesh.colours
        buf['slice'] = mesh.article_slice
        self.vbo = ctx.buffer(buf.tobytes())
        self.vao = ctx.vertex_array(
            self.prog, [(self.vbo, '3f 2f 4f1 i', 'in_pos', 'in_uv', 'in_col', 'in_slice')])
        self.batches = mesh.batches
        self.triangles = n // 3
        self.textures = {}
        for tag, (w, h, rgba) in mesh.textures.items():
            tex = ctx.texture((w, h), 4, rgba)
            tex.build_mipmaps()
            tex.filter = (ctx.LINEAR_MIPMAP_LINEAR, ctx.LINEAR)
            tex.anisotropy = 8.0
            tex.repeat_x = tex.repeat_y = True
            self.textures[tag] = tex
        self.n_slices = int(mesh.slices)
        self.tint_tex = ctx.texture((self.n_slices, 1), 3, dtype='f4')
        self.tint_tex.filter = (ctx.NEAREST, ctx.NEAREST)
        self.set_tint(np.ones((self.n_slices, 3), np.float32))

    def set_tint(self, table):
        self.tint_tex.write(np.ascontiguousarray(table, np.float32).tobytes())

    def draw(self, mvp, fog_colour, fog_density, fog_scale):
        ctx = self.ctx
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        p = self.prog
        p['uMvp'].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
        p['uFogColour'].value = tuple(fog_colour)
        p['uFogDensity'].value = float(fog_density)
        p['uFogScale'].value = float(fog_scale)
        p['uTex'].value = 0
        p['uTint'].value = 1
        p['uTintN'].value = self.n_slices
        self.tint_tex.use(1)
        p['uHidden'].value = False
        p['uHighlight'].value = (1.0, 1.0, 1.0)
        for tag, first, count, blend in self.batches:
            self.textures[tag].use(0)
            p['uAlphaTest'].value = blend != 'opaque'
            self.vao.render(first=first, vertices=count)
