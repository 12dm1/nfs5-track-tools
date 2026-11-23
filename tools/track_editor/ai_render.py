"""AI6 overlay in the 3D view: flat ribbons on the road (lines, trajectories),
translucent wall panels and event posts. Input in game space, drawn in GL
space (Z mirrored, as render.TrackRenderer does)."""

import numpy as np

VERTEX = """
#version 330
uniform mat4 uMvp;
in vec3 in_pos;
in vec4 in_col;
out vec4 v_col;
void main() {
    gl_Position = uMvp * vec4(in_pos, 1.0);
    v_col = in_col;
}
"""

FRAGMENT = """
#version 330
in vec4 v_col;
out vec4 f_col;
void main() { f_col = v_col; }
"""

LIFT = 0.25          # m above the road, so ribbons do not z-fight with it


def to_gl(p):
    p = np.asarray(p, np.float64).copy()
    p[..., 2] = -p[..., 2]
    return p


def colour_ramp(t):
    """0..1 -> blue, cyan, green, yellow, red (a turbo-ish ramp)."""
    t = np.clip(np.asarray(t, np.float64), 0.0, 1.0)
    stops = np.array([[0.15, 0.25, 1.0], [0.0, 0.85, 1.0], [0.1, 0.95, 0.2],
                      [1.0, 0.9, 0.0], [1.0, 0.15, 0.05]])
    x = t * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    return stops[i] * (1 - f) + stops[i + 1] * f


def diverging(t):
    """-1..1 -> blue, white, red."""
    t = np.clip(np.asarray(t, np.float64), -1.0, 1.0)[..., None]
    blue, white, red = np.array([0.2, 0.4, 1.0]), np.array([0.95, 0.95, 0.95]), np.array([1.0, 0.2, 0.1])
    return np.where(t < 0, white + (blue - white) * -t, white + (red - white) * t)


class Mesh:
    """Triangles with RGBA colours, collected on the CPU."""

    def __init__(self):
        self.parts = []

    def ribbon(self, pts, cols, width, lift=LIFT, breaks=None):
        """Flat strip along `pts` (game space, (n, 3)); `cols` (n, 3|4) per
        point; `breaks[i]` True = no segment between i and i+1."""
        pts = to_gl(pts)
        n = len(pts)
        if n < 2:
            return
        cols = np.asarray(cols, np.float32)
        if cols.ndim == 1:
            cols = np.tile(cols, (n, 1))
        if cols.shape[1] == 3:
            cols = np.column_stack([cols, np.ones(n, np.float32)])
        d = np.zeros_like(pts)
        d[1:-1] = pts[2:] - pts[:-2]
        d[0], d[-1] = pts[1] - pts[0], pts[-1] - pts[-2]
        side = np.column_stack([-d[:, 2], np.zeros(n), d[:, 0]])
        side /= np.maximum(np.linalg.norm(side, axis=1, keepdims=True), 1e-9)
        up = np.array([0.0, lift, 0.0])
        a = pts + side * (width * 0.5) + up
        b = pts - side * (width * 0.5) + up
        seg = np.ones(n - 1, bool)
        if breaks is not None:
            seg &= ~np.asarray(breaks[:n - 1], bool)
        step = np.linalg.norm(pts[1:] - pts[:-1], axis=1)
        seg &= step < 50.0                       # respawn teleports
        i = np.nonzero(seg)[0]
        if not len(i):
            return
        tri_p = np.stack([a[i], b[i], a[i + 1], a[i + 1], b[i], b[i + 1]], axis=1).reshape(-1, 3)
        tri_c = np.stack([cols[i], cols[i], cols[i + 1], cols[i + 1], cols[i], cols[i + 1]],
                         axis=1).reshape(-1, 4)
        self.parts.append((tri_p, tri_c))

    def panels(self, segs, col, height_scale=1.0, min_height=0.5):
        """Vertical panels over wall edges (n, 7): a.xyz, b.xyz, height."""
        if not len(segs):
            return
        a, b = to_gl(segs[:, 0:3]), to_gl(segs[:, 3:6])
        h = np.maximum(segs[:, 6] * height_scale, min_height)[:, None]
        up = np.zeros_like(a)
        up[:, 1:2] = h
        tri_p = np.stack([a, b, a + up, a + up, b, b + up], axis=1).reshape(-1, 3)
        tri_c = np.tile(np.asarray(col, np.float32), (len(tri_p), 1))
        self.parts.append((tri_p, tri_c))

    def posts(self, pts, cols, height=4.0, width=0.7):
        """A vertical cross (two quads) at every point."""
        if not len(pts):
            return
        p = to_gl(pts)
        cols = np.asarray(cols, np.float32)
        if cols.shape[1] == 3:
            cols = np.column_stack([cols, np.ones(len(cols), np.float32)])
        out_p, out_c = [], []
        up = np.array([0.0, height, 0.0])
        for axis in (np.array([1.0, 0, 0]), np.array([0, 0, 1.0])):
            a = p - axis * width * 0.5
            b = p + axis * width * 0.5
            out_p.append(np.stack([a, b, a + up, a + up, b, b + up], axis=1).reshape(-1, 3))
            out_c.append(np.repeat(cols, 6, axis=0))
        self.parts.append((np.vstack(out_p), np.vstack(out_c)))

    def arrays(self):
        if not self.parts:
            return np.zeros((0, 3), np.float32), np.zeros((0, 4), np.float32)
        return (np.vstack([p for p, _ in self.parts]).astype(np.float32),
                np.vstack([c for _, c in self.parts]).astype(np.float32))


class OverlayRenderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        self.vbo = None
        self.vao = None
        self.count = 0
        self.on_top = False

    def upload(self, mesh):
        pos, col = mesh.arrays()
        self.count = len(pos)
        if self.vbo is not None:
            self.vbo.release()
            self.vao.release()
            self.vbo = self.vao = None
        if not self.count:
            return
        buf = np.zeros(self.count, dtype=[('pos', '<f4', 3), ('col', '<f4', 4)])
        buf['pos'], buf['col'] = pos, col
        self.vbo = self.ctx.buffer(buf.tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(self.vbo, '3f 4f', 'in_pos', 'in_col')])

    def draw(self, mvp):
        if not self.count:
            return
        ctx = self.ctx
        self.prog['uMvp'].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        ctx.disable(ctx.CULL_FACE)
        if self.on_top:
            ctx.disable(ctx.DEPTH_TEST)
        else:
            ctx.enable(ctx.DEPTH_TEST)
        ctx.depth_mask = False
        self.vao.render()
        ctx.depth_mask = True
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
