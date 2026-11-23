"""Sky pass of the preview: cloud layers on the dome, horizon panorama, sun and
flare chain, precipitation particles, lightning flash. Geometry in `skygeo`,
layer definitions in `sky_layers`.

What the game does (`FUN_0045d4f0`): set fog channel A, draw the sky levels
and the horizon WITH that fog on, then switch to fog channel B for the world.
So the panorama and the clouds dissolve into the fog exactly as far as channel
A says -- on top of alps the horizon (400 units away) is ~100% fog colour.

Order in a frame: `draw_sky` (layers, sun, horizon -- no depth, the track
simply paints over them), then the track, then `draw_overlay` (precipitation,
flare chain, flash)."""

import random

import numpy as np

import assets
import sky_layers
import skygeo

SPRITE_VS = """
#version 330
uniform vec2 uCentre;
uniform vec2 uHalf;
out vec2 v_uv;
void main() {
    vec2 c = vec2[6](vec2(-1,-1), vec2(1,-1), vec2(1,1), vec2(-1,-1), vec2(1,1), vec2(-1,1))[gl_VertexID];
    v_uv = c * 0.5 + 0.5;
    gl_Position = vec4(uCentre + c * uHalf, 0.0, 1.0);
}
"""

SPRITE_FS = """
#version 330
uniform sampler2D uTex;
uniform bool uUseTex;
uniform vec4 uColour;
in vec2 v_uv;
out vec4 f_col;
void main() {
    vec4 t = uUseTex ? texture(uTex, vec2(v_uv.x, 1.0 - v_uv.y)) : vec4(1.0);
    f_col = vec4(t.rgb * uColour.rgb, t.a * uColour.a);
}
"""

FOG_GLSL = """
vec3 apply_fog(vec3 rgb, float w, vec3 fog_colour, float d, float scale) {
    float f = clamp((1.0 - exp(-d * w)) * scale, 0.0, 1.0);
    return mix(rgb, fog_colour, f);
}
"""

MESH_VS = """
#version 330
uniform mat4 uVP;
uniform vec2 uUvOffset;
in vec3 in_pos;
in vec2 in_uv;
in vec4 in_col;
out vec2 v_uv;
out vec4 v_col;
out float v_w;
void main() {
    gl_Position = uVP * vec4(in_pos, 1.0);
    v_w = gl_Position.w;
    v_uv = in_uv + uUvOffset;
    v_col = in_col;
}
"""

MESH_FS = """
#version 330
uniform sampler2D uTex;
uniform bool uUseTex;
uniform bool uAlphaTest;
uniform vec3 uFogColour;
uniform float uFogDensity;
uniform float uFogScale;
in vec2 v_uv;
in vec4 v_col;
in float v_w;
out vec4 f_col;
""" + FOG_GLSL + """
void main() {
    vec4 t = uUseTex ? texture(uTex, v_uv) : vec4(1.0);
    if (uAlphaTest && t.a < 0.5) discard;
    vec4 c = t * v_col;
    f_col = vec4(apply_fog(c.rgb, v_w, uFogColour, uFogDensity, uFogScale), c.a);
}
"""

PRECIP_VS = """
#version 330
uniform mat4 uView;
uniform mat4 uProj;
uniform vec3 uCam;
uniform float uTime;
uniform float uBox;
uniform vec2 uSize;
uniform vec3 uDrift;
uniform float uWobble;
out vec2 v_uv;
float h(float n) { return fract(sin(n * 12.9898) * 43758.5453); }
void main() {
    vec2 c = vec2[6](vec2(-1,-1), vec2(1,-1), vec2(1,1), vec2(-1,-1), vec2(1,1), vec2(-1,1))[gl_VertexID];
    float n = float(gl_InstanceID);
    vec3 seed = vec3(h(n), h(n + 17.13), h(n + 53.7));
    vec3 base = seed * uBox + uDrift * uTime
              + vec3(uWobble * sin(uTime * 1.3 + n), 0.0, uWobble * cos(uTime * 1.1 + n * 1.7));
    vec3 wp = uCam + mod(base - uCam, uBox) - vec3(uBox * 0.5);
    vec4 vp = uView * vec4(wp, 1.0);
    vp.xy += c * uSize;
    gl_Position = uProj * vp;
    v_uv = c * 0.5 + 0.5;
}
"""

PRECIP_FS = """
#version 330
uniform sampler2D uTex;
in vec2 v_uv;
out vec4 f_col;
void main() {
    vec4 t = texture(uTex, vec2(v_uv.x, 1.0 - v_uv.y));
    if (t.a < 0.02) discard;
    f_col = t;
}
"""

VERTEX_DTYPE = [('pos', '<f4', 3), ('uv', '<f4', 2), ('col', 'u1', 4)]
TICKS_PER_SECOND = 64.0      # wind is per game tick; the tick length is a guess


class Preview:
    """User-facing switches and knobs of the sky pass (edited in the UI)."""

    def __init__(self):
        self.dome_wire = False
        self.layers = True
        self.sky_fog = True
        self.horizon = True
        self.sun = True
        self.flares = True
        self.precip = True
        self.storm = True
        # The game turns 0..1 into a count by a factor stored in an unopened
        # field; this is OUR factor. The 0..1 value itself reaches the game
        # exactly, the look of the density here is NOT calibrated.
        self.precip_per_unit = 3000.0
        self.sun_size = 0.05
        self.storm_mult = 1.0


def _mat(m):
    return np.ascontiguousarray(m.T, np.float32).tobytes()


def _tex(ctx, tex, repeat=False):
    w, h, rgba = tex
    t = ctx.texture((w, h), 4, rgba)
    t.build_mipmaps()
    t.filter = (ctx.LINEAR_MIPMAP_LINEAR, ctx.LINEAR)
    t.repeat_x = t.repeat_y = repeat
    return t


class SkyRenderer:
    def __init__(self, ctx, track):
        self.ctx = ctx
        self.sprite = ctx.program(vertex_shader=SPRITE_VS, fragment_shader=SPRITE_FS)
        self.mesh = ctx.program(vertex_shader=MESH_VS, fragment_shader=MESH_FS)
        self.precip = ctx.program(vertex_shader=PRECIP_VS, fragment_shader=PRECIP_FS)
        self.empty_vao = ctx.vertex_array(self.sprite, [])
        self.precip_vao = ctx.vertex_array(self.precip, [])

        self.track = track
        sky_tex, fsh_path, common, particles = assets.load_sky_textures(track)
        self.sky_tex = sky_tex                       # raw (w, h, rgba) by tag
        horizon = assets.horizon_texture(sky_tex)
        self.horizon_tex = _tex(ctx, horizon) if horizon else None
        self.common = {k: _tex(ctx, v) for k, v in common.items()}
        self.particles = {k: _tex(ctx, v) for k, v in particles.items()}
        self.level = sky_layers.load(fsh_path)
        self.layer_tex = {}
        if self.level:
            for layer in self.level.layers:
                raw = sky_tex.get(layer.texture)
                if raw and layer.texture not in self.layer_tex:
                    self.layer_tex[layer.texture] = _tex(ctx, raw, repeat=True)
        hz = sky_tex.get('hzcl')
        self.hzcl = (np.frombuffer(hz[2], np.uint8).reshape(hz[1], hz[0], 4)
                     if hz and hz[:2] == (16, 2) else np.full((2, 16, 4), 255, np.uint8))

        n = skygeo.GRID * skygeo.GRID
        self.dome_key = None
        self.dome_pos = None
        self.dome_ibo = ctx.buffer(skygeo.dome_indices().tobytes())
        self.dome_vbo = ctx.buffer(reserve=n * 24, dynamic=True)
        self.dome_vao = ctx.vertex_array(
            self.mesh, [(self.dome_vbo, '3f 2f 4f1', 'in_pos', 'in_uv', 'in_col')], self.dome_ibo)
        self.horizon_vbo = ctx.buffer(reserve=16 * 6 * 24, dynamic=True)
        self.horizon_vao = ctx.vertex_array(
            self.mesh, [(self.horizon_vbo, '3f 2f 4f1', 'in_pos', 'in_uv', 'in_col')])
        self.horizon_verts = 0
        self.wind = [[0.0, 0.0] for _ in range(3)]

        self.time = 0.0
        self.flash = 0.0
        self.rng = random.Random(1)
        self.sun_ndc = None      # (x, y) if the sun is in front of the camera
        self.sun_half = (0.0, 0.0)
        self.stats = {}
        self.layers_drawn = 0

    def set_horizon(self, tex):
        """SKY3: draw (w, h, rgba) as the horizon -- an import being previewed."""
        if self.horizon_tex is not None:
            self.horizon_tex.release()
        self.horizon_tex = _tex(self.ctx, tex) if tex else None

    def reload_horizon(self):
        """SKY3: back to what Sky/<Track>.fsh holds now (after a write or restore)."""
        path = assets.sky_fsh_path(self.track)
        if path:
            self.sky_tex.update(assets.load_fsh(path, assets.HORIZON_TAGS))
        self.set_horizon(assets.horizon_texture(self.sky_tex))

    # -- per frame -----------------------------------------------------------

    def update(self, dt, state, slc, preview):
        self.time += dt
        if self.level:
            region = state.sky.region_blend_at(slc)
            ticks = dt * TICKS_PER_SECOND
            for i, layer in enumerate(self.level.layers[:3]):
                ua, va = skygeo.wind_velocity(layer.a.wind_speed, layer.a.wind_dir)
                ub, vb = skygeo.wind_velocity(layer.b.wind_speed, layer.b.wind_dir)
                self.wind[i][0] = (self.wind[i][0] + ticks * (ua * (1 - region) + ub * region)) % 1.0
                self.wind[i][1] = (self.wind[i][1] + ticks * (va * (1 - region) + vb * region)) % 1.0
        self.flash = max(0.0, self.flash - dt * 4.0)
        if preview.storm:
            value = state.sky.storm_at(slc)
            if self.rng.random() < skygeo.storm_chance(value, dt * preview.storm_mult):
                self.flash = 1.0

    def trigger_flash(self):
        self.flash = 1.0

    # -- sky pass (before the track) -----------------------------------------

    def _dome_positions(self, d):
        key = (d.dome_radius, d.dome_curvature, d.dome_height, d.angle, d.scale)
        if key != self.dome_key:
            pos = skygeo.dome_grid(d)
            pos[:, 2] *= -1.0                     # game -> GL
            self.dome_pos, self.dome_key = pos, key
        return self.dome_pos

    def _sky_map(self, name):
        raw = self.sky_tex.get(name) if name else None
        if not raw or raw[:2] != (skygeo.GRID, skygeo.GRID):
            return None
        return np.frombuffer(raw[2], np.uint8).reshape(-1, 4).astype(np.float32)

    def _write_dome(self, pos, uv, colours):
        buf = np.zeros(len(pos), dtype=VERTEX_DTYPE)
        buf['pos'], buf['uv'] = pos, uv
        buf['col'] = np.clip(np.round(colours), 0, 255).astype(np.uint8)
        self.dome_vbo.write(buf.tobytes())

    def _write_horizon(self, d):
        buf = np.zeros(16 * 6, dtype=VERTEX_DTYPE)
        n = 0
        for q in skygeo.horizon_panels(d):
            i0, i1 = q['colour_index']
            cols = [self.hzcl[0, i0], self.hzcl[0, i1], self.hzcl[1, i1], self.hzcl[1, i0]]
            for v in (0, 1, 2, 0, 2, 3):
                x, y, z = q['corners'][v]
                buf[n] = ((x, y, -z), q['uv'][v], cols[v])
                n += 1
        self.horizon_vbo.write(buf.tobytes())
        self.horizon_verts = n

    def _sprite(self, tex, centre, half, colour, additive=True):
        ctx = self.ctx
        p = self.sprite
        p['uCentre'].value = tuple(centre)
        p['uHalf'].value = tuple(half)
        p['uColour'].value = tuple(colour)
        p['uUseTex'].value = tex is not None
        if tex is not None:
            tex.use(0)
            p['uTex'].value = 0
        ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE) if additive else (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
        self.empty_vao.render(vertices=6)
        ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)

    def _draw_sun(self, d, rot, proj, preview):
        self.sun_ndc = None
        sx, sy, sz = skygeo.sun_position(d)
        v = rot @ np.array([sx, sy, -sz, 1.0])          # game -> GL
        if v[2] >= -1e-3:
            return
        clip = proj @ v
        ndc = clip[:2] / clip[3]
        dist = float(np.linalg.norm(v[:3]))
        self.sun_half = (preview.sun_size * dist * proj[0, 0] / -v[2],
                         preview.sun_size * dist * proj[1, 1] / -v[2])
        self.sun_ndc = (float(ndc[0]), float(ndc[1]))
        if preview.sun and 'SUNA' in self.common:
            # _sun_scale < 1 while rebuilding the reflection cube (MAT16): the
            # game keeps only `sun_reflection` of the sun in the road mirror.
            k = getattr(self, '_sun_scale', 1.0)
            self._sprite(self.common['SUNA'], ndc, self.sun_half, (k, k, k, 1.0))

    def draw_sky(self, view, proj, state, slc, bg, preview):
        """Cloud layers, sun, horizon. `view` is the camera view (GL space);
        only its rotation is used -- the sky is centred on the camera."""
        ctx = self.ctx
        d = state.atmo.sky
        rot = view.copy()
        rot[:3, 3] = 0.0
        ctx.disable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        ctx.enable(ctx.BLEND)
        ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)

        m = self.mesh
        m['uVP'].write(_mat(proj @ rot))
        m['uTex'].value = 0
        colour, density, scale = state.sky_fog(slc)
        m['uFogColour'].value = tuple(colour)
        m['uFogDensity'].value = float(density) if preview.sky_fog else 0.0
        m['uFogScale'].value = float(scale) if preview.sky_fog else 1.0
        m['uAlphaTest'].value = False

        pos = self._dome_positions(d)
        region = state.sky.region_blend_at(slc)
        layers = self.level.layers[:3] if (self.level and preview.layers) else []
        blocks_sun = bool(layers) and self.level.blocks_sun
        sun_done = False
        drawn = 0
        for i, layer in enumerate(layers):
            ma, mb = self._sky_map(layer.a.sky_map), self._sky_map(layer.b.sky_map)
            if ma is None and mb is None:
                continue
            ma = mb if ma is None else ma
            mb = ma if mb is None else mb
            xs = layer.a.x_scale * (1 - region) + layer.b.x_scale * region
            ys = layer.a.y_scale * (1 - region) + layer.b.y_scale * region
            self._write_dome(pos, skygeo.dome_uv(xs, ys), ma * (1.0 - region) + mb * region)
            tex = self.layer_tex.get(layer.texture)      # None = 'flat': plain white
            m['uUseTex'].value = tex is not None
            if tex is not None:
                tex.use(0)
            m['uUvOffset'].value = tuple(self.wind[i])
            self.dome_vao.render()
            drawn += 1
            if blocks_sun and not sun_done:
                # guess: "this level blocks the sun" = the cloud layers above
                # the base one cover it
                self._draw_sun(d, rot, proj, preview)
                sun_done = True
        self.layers_drawn = drawn
        if not drawn:
            flat = np.tile(np.array([bg[0] * 255, bg[1] * 255, bg[2] * 255, 255.0], np.float32),
                           (len(pos), 1))
            self._write_dome(pos, skygeo.dome_uv(1, 1), flat)
            m['uUseTex'].value = False
            m['uUvOffset'].value = (0.0, 0.0)
            self.dome_vao.render()
        if preview.dome_wire:
            ctx.wireframe = True
            m['uUseTex'].value = False
            self.dome_vao.render()
            ctx.wireframe = False
        if not sun_done:
            self._draw_sun(d, rot, proj, preview)

        if preview.horizon and self.horizon_tex is not None:
            self._write_horizon(d)
            m['uUseTex'].value = True
            m['uAlphaTest'].value = True
            m['uUvOffset'].value = (0.0, 0.0)
            self.horizon_tex.use(0)
            self.horizon_vao.render(vertices=self.horizon_verts)
        ctx.disable(ctx.BLEND)

    # -- overlay (after the track) -------------------------------------------

    def draw_overlay(self, view, proj, state, slc, preview, cam_pos, target, size):
        """Particles, flare chain (only if the sun pixel is unoccluded),
        lightning flash. `target` is the framebuffer just drawn into."""
        ctx = self.ctx
        rain, snow = state.sky.precipitation_at(slc)
        self.stats = {'rain': rain, 'snow': snow, 'flares': 0, 'sun_visible': False,
                      'layers': self.layers_drawn}
        ctx.disable(ctx.CULL_FACE)

        if preview.precip:
            ctx.enable(ctx.DEPTH_TEST)
            ctx.depth_mask = False
            ctx.enable(ctx.BLEND)
            ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
            p = self.precip
            p['uView'].write(_mat(view))
            p['uProj'].write(_mat(proj))
            p['uCam'].value = tuple(float(c) for c in cam_pos)
            p['uTime'].value = self.time
            p['uBox'].value = 60.0
            p['uTex'].value = 0
            for name, value, size_xy, drift, wobble in (
                    ('snow', snow, (0.12, 0.12), (0.4, -1.6, 0.3), 0.5),
                    ('rain', rain, (0.03, 0.55), (0.0, -14.0, 0.0), 0.0)):
                n = skygeo.particle_count(value, preview.precip_per_unit)
                self.stats[name + '_count'] = n
                if n and name in self.particles:
                    self.particles[name].use(0)
                    p['uSize'].value = size_xy
                    p['uDrift'].value = drift
                    p['uWobble'].value = wobble
                    self.precip_vao.render(vertices=6, instances=n)
            ctx.depth_mask = True
            ctx.disable(ctx.DEPTH_TEST)

        ctx.enable(ctx.BLEND)
        if preview.flares and state.atmo.sky.lens_flare and self.sun_ndc is not None:
            sx, sy = self.sun_ndc
            fade = max(0.0, min(1.0, 1.4 - max(abs(sx), abs(sy))))
            visible = fade > 0.0 and self._sun_unoccluded(target, size)
            self.stats['sun_visible'] = visible
            if visible:
                aspect = size[0] / float(size[1])
                order = ('GLAR', 'FLR0', 'FLR1', 'FLR0', 'FLR1', 'FLR0', 'FLR1', 'FLR0', 'spik')
                for i, t in enumerate(skygeo.FLARE_POSITIONS):
                    tex = self.common.get(order[i])
                    if tex is None:
                        continue
                    h = 0.16 if i == 0 else 0.05 + 0.03 * ((i * 5) % 3)
                    self._sprite(tex, (sx * t, sy * t), (h / aspect, h),
                                 (1.0, 1.0, 1.0, (0.55 if i == 0 else 0.30) * fade))
                    self.stats['flares'] += 1

        if self.flash > 0.0:
            self._sprite(None, (0.0, 0.0), (1.0, 1.0), (1.0, 1.0, 1.0, 0.7 * self.flash ** 2))
        ctx.disable(ctx.BLEND)

    def _sun_unoccluded(self, target, size):
        sx, sy = self.sun_ndc
        px = int((sx * 0.5 + 0.5) * size[0])
        py = int((sy * 0.5 + 0.5) * size[1])
        if not (0 <= px < size[0] and 0 <= py < size[1]):
            return True          # off screen: the game's readrect also just samples there
        depth = np.frombuffer(target.read(viewport=(px, py, 1, 1), components=1,
                                          attachment=-1, dtype='f4'), np.float32)[0]
        return bool(depth >= 0.99999)
