"""The viewport: one quad, two independent ways of looking at the result.

  layout   single | tile 3x3 (the only honest way to judge a seam) |
           offset 50% (rolls the seam into the middle of the frame)
  compare  result | A/B wipe against the original | |new - original| amplified

The two are independent, so the original can be wiped in while tiled.

Transparency is composited over a checkerboard so alpha is never a guess.
"""

import numpy as np

# Two independent axes: how the texture is laid out, and what it is compared
# against.  Wiping against the original while tiled 3x3 is the useful case.
LAYOUTS = ('single', 'tile', 'offset')
LAYOUT_LABELS = {'single': 'Single', 'tile': 'Tile 3x3', 'offset': 'Offset 50%'}
COMPARES = ('result', 'wipe', 'diff')
COMPARE_LABELS = {'result': 'Result', 'wipe': 'A/B wipe', 'diff': 'Diff'}

_VERT = """
#version 330
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

_FRAG = """
#version 330
uniform sampler2D tex_new;
uniform sampler2D tex_ref;
uniform vec2 uv0;
uniform vec2 uv1;
uniform float tiles;
uniform vec2 tex_off;
uniform int mode;
uniform float wipe;
uniform float has_ref;
uniform float diff_gain;
uniform vec2 checker;
in vec2 v_uv;
out vec4 f_colour;

vec3 over_checker(vec4 c, vec2 px) {
    float k = mod(floor(px.x / 8.0) + floor(px.y / 8.0), 2.0);
    vec3 bg = mix(vec3(0.22), vec3(0.32), k);
    return mix(bg, c.rgb, c.a);
}

void main() {
    vec2 uv = mix(uv0, uv1, vec2(v_uv.x, 1.0 - v_uv.y));
    vec2 px = v_uv * checker;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        f_colour = vec4(0.10, 0.10, 0.11, 1.0);
        return;
    }
    vec2 t = uv * tiles + tex_off;
    vec4 c = texture(tex_new, t);
    if (mode == 1 && has_ref > 0.5 && uv.x < wipe) {
        c = vec4(texture(tex_ref, t).rgb, 1.0);
    } else if (mode == 2) {
        vec3 d = has_ref > 0.5 ? abs(c.rgb - texture(tex_ref, t).rgb) : vec3(0.0);
        c = vec4(clamp(d * diff_gain, 0.0, 1.0), 1.0);
    }
    vec3 rgb = over_checker(c, px);
    if (mode == 1 && has_ref > 0.5 && abs(uv.x - wipe) < 0.0015) {
        rgb = vec3(1.0, 0.85, 0.2);
    }
    f_colour = vec4(rgb, 1.0);
}
"""


class Viewport:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        quad = np.array([-1, -1, 3, -1, -1, 3], np.float32)
        self.vao = ctx.vertex_array(self.prog, [(ctx.buffer(quad), '2f', 'in_pos')])
        self.fbo = None
        self.fbo_tex = None
        self.fbo_size = (0, 0)
        self.tex_new = None
        self.tex_ref = None
        self.new_size = (1, 1)
        self.zoom = 1.0
        self.pan = [0.0, 0.0]

    # ------------------------------------------------------------ upload ---
    def _upload(self, tex, rgba):
        h, w = rgba.shape[:2]
        data = np.ascontiguousarray(
            np.round(np.clip(rgba, 0, 1) * 255.0).astype(np.uint8)).tobytes()
        if tex is not None and tex.size == (w, h):
            tex.write(data)
        else:
            if tex is not None:
                tex.release()
            tex = self.ctx.texture((w, h), 4, data)
        tex.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
        tex.repeat_x = tex.repeat_y = True
        return tex

    def set_result(self, rgb, alpha):
        a = np.ones(rgb.shape[:2], np.float32) if alpha is None else alpha
        self.tex_new = self._upload(self.tex_new, np.dstack([rgb, a]))
        self.new_size = (rgb.shape[1], rgb.shape[0])

    def set_reference(self, rgb):
        if rgb is None:
            if self.tex_ref is not None:
                self.tex_ref.release()
                self.tex_ref = None
            return
        self.tex_ref = self._upload(
            self.tex_ref, np.dstack([rgb, np.ones(rgb.shape[:2], np.float32)]))

    # ------------------------------------------------------------ render ---
    def _uv_rect(self, vw, vh, tiles):
        """The uv rectangle the viewport corners map to: fit, then zoom/pan."""
        w, h = self.new_size
        ar = (w * tiles) / float(h * tiles)
        va = vw / max(vh, 1.0)
        sx, sy = (va / ar, 1.0) if va > ar else (1.0, ar / va)
        sx /= self.zoom
        sy /= self.zoom
        cx, cy = 0.5 + self.pan[0], 0.5 + self.pan[1]
        return ((cx - sx * 0.5, cy - sy * 0.5), (cx + sx * 0.5, cy + sy * 0.5))

    def render(self, vw, vh, layout, compare, wipe, has_ref, diff_gain=4.0):
        vw, vh = max(16, int(vw)), max(16, int(vh))
        if self.fbo is None or self.fbo_size != (vw, vh):
            if self.fbo is not None:
                self.fbo.release()
                self.fbo_tex.release()
            # A real texture, not simple_framebuffer's renderbuffer: imgui is
            # handed this object's GL name and must be able to sample it.
            self.fbo_tex = self.ctx.texture((vw, vh), 4)
            self.fbo_tex.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
            self.fbo_tex.repeat_x = self.fbo_tex.repeat_y = False
            self.fbo = self.ctx.framebuffer(color_attachments=[self.fbo_tex])
            self.fbo_size = (vw, vh)
        tiles = 3.0 if layout == 'tile' else 1.0
        uv0, uv1 = self._uv_rect(vw, vh, tiles)
        p = self.prog
        p['uv0'].value = uv0
        p['uv1'].value = uv1
        p['tiles'].value = tiles
        p['tex_off'].value = (0.5, 0.5) if layout == 'offset' else (0.0, 0.0)
        p['mode'].value = COMPARES.index(compare)
        p['wipe'].value = float(wipe)
        p['has_ref'].value = 1.0 if (has_ref and self.tex_ref is not None) else 0.0
        p['diff_gain'].value = float(diff_gain)
        p['checker'].value = (float(vw), float(vh))
        p['tex_new'].value = 0
        p['tex_ref'].value = 1
        self.tex_new.use(0)
        (self.tex_ref or self.tex_new).use(1)
        self.fbo.use()
        self.fbo.clear(0.08, 0.08, 0.09, 1.0)
        self.vao.render()
        self.ctx.screen.use()
        return self.fbo_tex

    def reset_view(self):
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
