"""ATM6 acceptance: fog maths of the preview against the game's own numbers.

  * `SkyBin.fog_at` on alps at ten control slices vs the ATM1 reference values
    (slice 0 -> (139,103,87) 0.00316, 648 -> (46,71,121) 0.01758, snow 1230 -> 0.51);
  * an independent Python re-derivation of the interpolation + cube matches
    `fog_at` exactly on those slices;
  * `AtmosphereState` (Atmosphere -> .bin -> SkyBin) reproduces the original
    fog within 1/255 colour, 1e-6 density;
  * the GLSL `apply_fog` the preview compiles matches `fog_factor` on the GPU.

    python test_atm6_fog.py
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'track_editor'))

import nfs5_sky_bin as skybin       # noqa: E402
import track_atmosphere as atm      # noqa: E402
from state import AtmosphereState, W63, fog_factor, fog_scale   # noqa: E402

SLICES = (0, 300, 500, 620, 648, 900, 1120, 1230, 1343, 1700)
ATM1 = {0: ((139, 103, 87), 0.00316), 648: ((46, 71, 121), 0.01758)}


def reference_fog(sky, s):
    """Straight re-implementation: linear scan, lerp raw density, then cube."""
    keys = sky.weather
    if s <= keys[0].slice:
        lo = hi = keys[0]
    elif s >= keys[-1].slice:
        lo = hi = keys[-1]
    else:
        i = next(i for i in range(len(keys) - 1) if keys[i].slice <= s < keys[i + 1].slice)
        lo, hi = keys[i], keys[i + 1]
    t = 0.0 if hi.slice == lo.slice else (s - lo.slice) / float(hi.slice - lo.slice)
    density = (lo.fog_density_a * (1 - t) + hi.fog_density_a * t) ** 3
    colour = tuple(int(lo.rgb()[c] * (1 - t) + hi.rgb()[c] * t + 0.5) for c in range(3))
    return colour, density


def check_reference(problems):
    sky = skybin.read(atm.sky_bin_path('alps'))
    for s, (colour, density) in ATM1.items():
        c, d = sky.fog_at(s)
        if c != colour or abs(d - density) > 6e-6:
            problems.append('fog_at(%d) = %s %.5f, ATM1 says %s %.5f' % (s, c, d, colour, density))
    rain, snow = sky.precipitation_at(1230)
    if abs(snow - 0.51) > 0.01:
        problems.append('snow at 1230: %.3f, ATM1 says 0.51' % snow)

    worst = 0.0
    for s in SLICES:
        c1, d1 = sky.fog_at(s)
        c2, d2 = reference_fog(sky, s)
        if max(abs(c1[i] - c2[i]) for i in range(3)) > 0 or abs(d1 - d2) > 1e-9:
            problems.append('slice %d: fog_at %s %.6f != independent result %s %.6f'
                            % (s, c1, d1, c2, d2))
        worst = max(worst, abs(d1 - d2))
    print('fog_at vs independent result on %d slices: max density difference %.1e'
          % (len(SLICES), worst))


def check_state(problems):
    sky = skybin.read(atm.sky_bin_path('alps'))
    state = AtmosphereState(atm.from_sky_bin(sky, name='alps'))
    # world = channel B, sky = channel A (FUN_0045d4f0)
    for s in SLICES:
        for name, want, got in (('world/B', sky.fog_b_at(s), state.fog(s)),
                                ('sky/A', sky.fog_at(s), state.sky_fog(s))):
            c1, d1 = want
            colour, d2, scale = got
            c2 = tuple(round(v * 255) for v in colour)
            if max(abs(c1[i] - c2[i]) for i in range(3)) > 1 or abs(d1 - d2) > 1e-6:
                problems.append('state %s (%d): %s %.6f vs %s %.6f' % (name, s, c2, d2, c1, d1))
            if d2 > 0 and abs(scale * (1 - math.exp(-d2 * W63)) - 1.0) > 1e-9:
                problems.append('fog_scale(%d) does not normalise the table' % s)
    if sky.fog_b_at(0)[1] >= sky.fog_at(0)[1]:
        problems.append('alps: world fog (B) not thinner than sky fog (A) at slice 0')
    print('AtmosphereState on alps: world=B and sky=A, %d slices within 1/255 and 1e-6' % len(SLICES))


def check_glsl(problems):
    import moderngl
    import render
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as e:  # noqa: BLE001
        print('GLSL: no standalone context (%s) -- check skipped' % e)
        return
    vs = """#version 330
in vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }"""
    fs = """#version 330
uniform float uW[8]; uniform float uD; uniform float uScale;
out vec4 o;
""" + render.FOG_GLSL + """
void main() {
    o = vec4(apply_fog(vec3(0.0), uW[int(gl_FragCoord.x)], vec3(1.0), uD, uScale).r, 0.0, 0.0, 1.0);
}"""
    prog = ctx.program(vertex_shader=vs, fragment_shader=fs)
    vbo = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], np.float32).tobytes())
    vao = ctx.vertex_array(prog, [(vbo, '2f', 'p')])
    fbo = ctx.framebuffer(ctx.texture((8, 1), 4, dtype='f4'))
    fbo.use()
    ws = [0.5, 10.0, 50.0, 150.0, 400.0, 1500.0, 20000.0, 52428.8]
    worst = 0.0
    for _, d in sorted(ATM1.items()) + [(0, 0.1), (0, 0.02), (0, 1e-4)]:
        density = d[1] if isinstance(d, tuple) else d
        prog['uW'].value = ws
        prog['uD'].value = density
        prog['uScale'].value = fog_scale(density)
        fbo.clear(0, 0, 0, 1)
        vao.render(vertices=3)
        got = np.frombuffer(fbo.read(components=4, dtype='f4'), np.float32).reshape(8, 4)[:, 0]
        for i, w in enumerate(ws):
            want = fog_factor(density, w)
            worst = max(worst, abs(float(got[i]) - want))
            if abs(float(got[i]) - want) > 1e-4:
                problems.append('GLSL fog d=%g w=%g: GPU %.6f, Python %.6f' % (density, w, got[i], want))
    print('GLSL apply_fog vs Python fog_factor: max difference %.1e' % worst)


if __name__ == '__main__':
    problems = []
    check_reference(problems)
    check_state(problems)
    check_glsl(problems)
    print('\n' + ('ALL PASSED' if not problems else 'FAILURES'))
    for p in problems:
        print('  -', p)
    sys.exit(1 if problems else 0)
