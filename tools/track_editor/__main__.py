"""Track editor window: atmosphere (fog, sky, weather along the road) and AI lines.

    python tools/track_editor <game>/GameData/Track alps [--atmosphere x.ini] [--ai]
        [--selftest | --ui-selftest | --ai-selftest | --ai-ui-selftest]

--ai opens in the "AI line" mode (racing lines, walls, .aispeed zones and the
race_test traces of this track; see ai_ui.py).

Keys: TAB switch camera. Free: WASD + right mouse drag, Space/Ctrl up/down.
Rail: W/S along the road (slices), Up/Down height. Shift = fast.
Panels: file/presets, Sky, keys, curve lanes (drag points), read-only layers.
"""

import os
import sys
import tempfile
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'track'))
sys.path.insert(0, _HERE)

import glfw          # noqa: E402
import moderngl      # noqa: E402

import nfs5_track_mesh as track_mesh   # noqa: E402
from camera import FreeCamera, RailCamera, perspective   # noqa: E402
from render import TrackRenderer   # noqa: E402
from sky_render import Preview, SkyRenderer   # noqa: E402
from state import AtmosphereState   # noqa: E402
import skygeo   # noqa: E402

NEAR, FAR, FOV = 0.5, 20000.0, 70.0


def make_window(width, height, visible):
    if not glfw.init():
        raise RuntimeError('glfw.init failed')
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.VISIBLE, glfw.TRUE if visible else glfw.FALSE)
    window = glfw.create_window(width, height, 'track_editor', None, None)
    if not window:
        raise RuntimeError('glfw.create_window failed')
    glfw.make_context_current(window)
    return window


def glfw_version():
    v = glfw.get_version_string()
    return v.decode() if isinstance(v, bytes) else v


def cam_position(cam):
    return cam.position if isinstance(cam, FreeCamera) else cam.position_forward()[0]


def draw_scene(target, size, renderer, sky, state, cam, slc, preview):
    """One frame: sky pass, track, overlay (particles, flares, flash)."""
    ctx = renderer.ctx
    target.use()
    ctx.viewport = (0, 0, size[0], size[1])
    bg = state.background(slc)
    ctx.clear(*bg)
    proj = perspective(FOV, size[0] / size[1], NEAR, FAR)
    view = cam.view()
    colour, density, scale = state.fog(slc)
    if sky is not None:
        sky.draw_sky(view, proj, state, slc, bg, preview)
    renderer.draw(proj @ view, colour, density, scale)
    if sky is not None:
        sky.draw_overlay(view, proj, state, slc, preview, cam_position(cam), target, size)
    return bg


def render_at(renderer, state, fbo, rail, slc, width, height, sky=None, preview=None):
    rail.slice = slc
    bg = draw_scene(fbo, (width, height), renderer, sky, state, rail, rail.slice, preview)
    img = np.frombuffer(fbo.read(components=3), np.uint8).reshape(-1, 3)
    return img, bg


def selftest(mesh, state, width=640, height=360):
    make_window(width, height, visible=False)
    ctx = moderngl.create_context()
    renderer = TrackRenderer(ctx, mesh)
    renderer.set_tint(state.tint_table(renderer.n_slices))
    fbo = ctx.framebuffer(ctx.renderbuffer((width, height)),
                          ctx.depth_renderbuffer((width, height)))
    rail = RailCamera(mesh.centreline)
    slices = rail.slices

    frames = 60
    mid = slices[len(slices) // 3]
    t0 = time.perf_counter()
    for _ in range(frames):
        img, bg = render_at(renderer, state, fbo, rail, mid, width, height)
    ctx.finish()
    dt = (time.perf_counter() - t0) / frames

    clear = np.round(np.array(bg) * 255).astype(np.int32)
    covered = float(np.mean(np.abs(img.astype(np.int32) - clear).max(axis=1) > 3))
    colours = len({tuple(c) for c in img[::7]})
    print('GL: %s | %s' % (ctx.info['GL_RENDERER'], ctx.info['GL_VERSION']))
    print('moderngl %s  glfw %s' % (moderngl.__version__, glfw_version()))
    print('frame %dx%d: geometry coverage %.1f%%, unique colours %d, '
          'std %.1f, %.2f ms/frame (%.0f FPS), %d tri' %
          (width, height, covered * 100, colours, float(img.std()), dt * 1000,
           1 / dt, renderer.triangles))
    ok = covered > 0.10 and colours > 50 and img.std() > 10

    # Fog moves along the road: mean colour of the frame at two slices.
    lo, hi = slices[0] + 0.15 * (slices[-1] - slices[0]), slices[0] + 0.45 * (slices[-1] - slices[0])
    means = []
    for s in (lo, hi):
        frame, _ = render_at(renderer, state, fbo, rail, s, width, height)
        m = frame.reshape(-1, 3).mean(axis=0)
        means.append(m)
        print('slice %6.0f: fog %s density %.5f  mean frame colour %s  b/r %.2f'
              % (s, tuple(int(c * 255) for c in state.fog(s)[0]), state.fog(s)[1],
                 tuple(int(v) for v in m), m[2] / max(m[0], 1.0)))
    if state.fog(lo)[0] != state.fog(hi)[0]:
        ok = ok and (means[1][2] / max(means[1][0], 1.0)) != (means[0][2] / max(means[0][0], 1.0))
    # GPU light tint: red x0.3 on every slice must darken the red channel only.
    base, _ = render_at(renderer, state, fbo, rail, mid, width, height)
    renderer.set_tint(np.tile(np.array([0.3, 1.0, 1.0], np.float32), (renderer.n_slices, 1)))
    tinted, _ = render_at(renderer, state, fbo, rail, mid, width, height)
    renderer.set_tint(state.tint_table(renderer.n_slices))
    mb, mt = base.mean(axis=0), tinted.mean(axis=0)
    print('tint R x0.3: mean colour %s -> %s' % (tuple(int(v) for v in mb), tuple(int(v) for v in mt)))
    ok = ok and mt[0] < mb[0] * 0.8 and abs(mt[2] - mb[2]) < 1.0
    ok = sky_selftest(ctx, renderer, state, fbo, rail, mesh, width, height) and ok
    print('SELFTEST', 'OK' if ok else 'FAIL')
    glfw.terminate()
    return 0 if ok else 1


def sky_selftest(ctx, renderer, state, fbo, rail, mesh, width, height):
    """Dome, horizon, sun + flares, snow and flash each change the frame the way
    they should (numbers, no screenshots)."""
    ok = True
    sky = SkyRenderer(ctx, mesh_name(mesh))
    frame = lambda cam, slc, pv: render_at_cam(renderer, state, fbo, sky, cam, slc, pv, width, height)
    off = Preview()
    off.horizon = off.sun = off.flares = off.precip = off.storm = False

    # snow: nothing at slice 300 (dry), particles at 900 (snow 1.0)
    for slc in (300, 900):
        rail.slice = slc
        a = frame(rail, slc, off)
        on = Preview()
        on.horizon = on.sun = on.flares = on.storm = False
        b = frame(rail, slc, on)
        diff = float(np.mean(np.abs(a.astype(np.int32) - b.astype(np.int32)).max(axis=1) > 8))
        rain, snow = state.sky.precipitation_at(slc)
        expect = max(rain, snow) > 0.01
        print('snow: slice %d value %.2f particles %d  changed pixels %.3f%%'
              % (slc, snow, sky.stats.get('snow_count', 0), diff * 100))
        good = (diff > 0.0005) if expect else (diff == 0.0)
        ok = ok and good

    print('sky layers: %d drawn (%s)' % (sky.layers_drawn, ', '.join(
        '%s x %s' % (l.a.sky_map, l.texture) for l in (sky.level.layers if sky.level else []))))
    ok = ok and sky.layers_drawn > 0

    # a camera high above the track: nothing occludes the sky
    eye = rail.position_forward()[0] + np.array([0.0, 4000.0, 0.0])
    sx, sy, sz = skygeo.sun_position(state.atmo.sky)
    to_sun = np.array([sx, sy, -sz])
    cam = FreeCamera(eye, to_sun)
    a = frame(cam, 900, off)
    sun_only = Preview()
    sun_only.horizon = sun_only.precip = sun_only.storm = False
    sun_only.flares = False
    b = frame(cam, 900, sun_only)
    c0 = (slice(height // 2 - 20, height // 2 + 20), slice(width // 2 - 20, width // 2 + 20))
    lum = lambda im: im.reshape(height, width, 3)[c0].mean()
    print('sun: NDC %s, centre brightness %.1f -> %.1f' % (sky.sun_ndc, lum(a), lum(b)))
    ok = ok and lum(b) > lum(a) + 5

    flares = Preview()
    flares.horizon = flares.precip = flares.storm = False
    c = frame(cam, 900, flares)
    print('flares: %d sprites, sun visible: %s, frame differs by %.2f%%' % (
        sky.stats['flares'], sky.stats['sun_visible'],
        100 * float(np.mean(np.abs(c.astype(np.int32) - b.astype(np.int32)).max(axis=1) > 4))))
    ok = ok and sky.stats['flares'] > 0

    # horizon: look level. Without sky fog the panorama must show; with fog
    # channel A it dissolves as far as the density at that slice says.
    level = FreeCamera(eye, (1.0, 0.0, 0.0))

    def horizon_share(slc, fog):
        best = 0.0
        for yaw in range(0, 360, 45):
            level.yaw = np.radians(yaw)
            a_pv, b_pv = Preview(), Preview()
            for pv in (a_pv, b_pv):
                pv.sun = pv.flares = pv.precip = pv.storm = False
                pv.sky_fog = fog
            a_pv.horizon = False
            base = frame(level, slc, a_pv).astype(np.int32)
            with_h = frame(level, slc, b_pv).astype(np.int32)
            best = max(best, float(np.mean(np.abs(base - with_h).max(axis=1) > 8)))
        return best

    lo_slc, hi_slc = rail.slices[0], rail.slices[len(rail.slices) // 2]
    clear = horizon_share(lo_slc, False)
    print('horizon without fog: panorama covers up to %.1f%% of frame' % (clear * 100))
    for slc in (lo_slc, hi_slc):
        d_a = state.sky_fog(slc)[1]
        f400 = 1.0 - np.exp(-d_a * 400.0)
        print('horizon with fog A: slice %d density %.5f -> at 400 units fog %.0f%%, visible %.1f%% of frame'
              % (slc, d_a, f400 * 100, horizon_share(slc, True) * 100))
    ok = ok and (clear > 0.005 if sky.horizon_tex is not None else True)

    # lightning flash brightens the frame
    calm = frame(rail, 900, off)
    sky.trigger_flash()
    storm = Preview()
    storm.horizon = storm.sun = storm.flares = storm.precip = False
    sky.flash = 1.0
    flashed = render_at_cam(renderer, state, fbo, sky, rail, 900, storm, width, height, keep_flash=True)
    print('flash: mean brightness %.1f -> %.1f' % (calm.mean(), flashed.mean()))
    ok = ok and flashed.mean() > calm.mean() + 20
    return ok


def ai_selftest(mesh, track_dir, name, width=640, height=360):
    """AI6: the overlay data lines up with the track and draws (numbers only)."""
    import ai_data
    from ai_render import OverlayRenderer
    from ai_ui import AiView
    make_window(width, height, visible=False)
    ctx = moderngl.create_context()
    renderer = TrackRenderer(ctx, mesh)
    state = AtmosphereState.for_track(name, None)
    renderer.set_tint(state.tint_table(renderer.n_slices))
    fbo = ctx.framebuffer(ctx.renderbuffer((width, height)), ctx.depth_renderbuffer((width, height)))
    rail = RailCamera(mesh.centreline)
    view = AiView(os.path.abspath(track_dir), name, lambda: rail.slice, lambda *a: None, mesh.centreline)
    view.ensure_loaded()
    t = view.track
    ok = t is not None and t.n > 0
    print('AI track: %d slices, lines %s, walls %d, objects %d, zones %s, vroad %s, %.2f s'
          % (t.n, sorted(t.lsp), len(t.walls), len(t.objects), sorted(t.zones), t.has_vroad, view.load_seconds))
    # the AI data sits on the drawn road: every vroad centre point has track
    # geometry right under it (same space as the CRP, no offset or mirror)
    if t.has_vroad:
        idx = np.arange(0, t.n, 7)
        verts = mesh.positions[::3].astype(np.float64)
        dist, dy = [], []
        for i in idx:
            c = t.centre[i]
            d2 = (verts[:, 0] - c[0]) ** 2 + (verts[:, 2] - c[2]) ** 2
            j = int(np.argmin(d2))
            dist.append(np.sqrt(d2[j]))
            dy.append(abs(verts[j, 1] - c[1]))
        dist, dy = np.array(dist), np.array(dy)
        print('vroad centre -> nearest track vertex: median %.2f m (max %.2f), height diff median %.2f m'
              % (np.median(dist), dist.max(), np.median(dy)))
        ok = ok and np.median(dist) < 4.0 and np.median(dy) < 2.0
        for d, lat in t.lat_lsp.items():
            print('line %d: lateral %.1f .. %.1f m, walls inside %.0f%% of slices' % (
                d, np.nanmin(lat), np.nanmax(lat),
                100 * np.mean((np.isnan(t.wall_left) | (lat > t.wall_left)) & (np.isnan(t.wall_right) | (lat < t.wall_right)))))
    f = ai_data.TraceFinder(name)
    while not f.done:
        time.sleep(0.05)
    print('traces found: %d' % len(f.results))
    ev_slice = None
    if f.results:
        view.load_trace(f.results[0][1])
        tr = view.trace
        print('trace %s: cars %s, %s, laps %d, events %d' % (tr.label(), sorted(tr.cars),
              'reverse' if tr.reverse else 'forward', tr.max_lap, len(tr.events)))
        for car in sorted(tr.cars):
            d = tr.cars[car]
            print('  car %d: %d rows, lateral median |%.1f| m, max %.0f km/h, events %s'
                  % (car, len(d['tick']), float(np.nanmedian(np.abs(d['lateral']))) if t.has_vroad else -1,
                     d['kmh'].max(), tr.stats[car]['events']))
            ok = ok and len(d['tick']) > 10
        ev = view.visible_events()
        ev_slice = ev[0][1]['slice'] if ev else None
    overlay = OverlayRenderer(ctx)
    m = view.build_mesh()
    overlay.upload(m)
    print('overlay: %d triangles in %d parts' % (overlay.count // 3, len(m.parts)))
    ok = ok and overlay.count > 0
    slc = ev_slice if ev_slice is not None else rail.slices[len(rail.slices) // 3]
    rail.slice = float(np.clip(slc - 12, rail.slices[0], rail.slices[-1]))
    proj = perspective(FOV, width / height, NEAR, FAR)

    def frame(with_overlay):
        draw_scene(fbo, (width, height), renderer, None, state, rail, rail.slice, None)
        if with_overlay:
            overlay.draw(proj @ rail.view())
        return np.frombuffer(fbo.read(components=3), np.uint8).reshape(-1, 3).astype(np.int32)

    a, b = frame(False), frame(True)
    changed = float(np.mean(np.abs(a - b).max(axis=1) > 8))
    print('frame at slice %.0f: overlay changes %.1f%% of pixels' % (rail.slice, changed * 100))
    ok = ok and changed > 0.002
    for mode in range(6):
        view.colour_mode = mode
        view.build_mesh()
    print('AI SELFTEST', 'OK' if ok else 'FAIL')
    glfw.terminate()
    return 0 if ok else 1


def mesh_name(mesh):
    return mesh.name


def render_at_cam(renderer, state, fbo, sky, cam, slc, preview, width, height, keep_flash=False):
    if not keep_flash:
        sky.flash = 0.0
    draw_scene(fbo, (width, height), renderer, sky, state, cam, slc, preview)
    return np.frombuffer(fbo.read(components=3), np.uint8).reshape(-1, 3)


def run(mesh, name, ini=None, ui_selftest=False, track_dir=None, ai_ui_selftest=False, ai_mode=False):
    """The editor: hello_imgui owns the window and GL context, the 3D scene is
    drawn in its `custom_background` callback, panels in `show_gui`."""
    from imgui_bundle import hello_imgui, imgui

    import editor as editor_mod
    import ui as ui_mod

    n_slices = int(mesh.centreline[-1][0]) + 8
    ed = editor_mod.open_editor(name, n_slices, ini)
    rail = RailCamera(mesh.centreline)
    pos, fwd = rail.position_forward()
    free = FreeCamera(pos, fwd)
    app = {'cam': rail, 'renderer': None, 'ctx': None, 'sky': None, 'version': -1,
           'frame': 0, 'errors': [], 'fog': None}

    def camera_slice():
        return rail.slice if app['cam'] is rail else rail.slice_near(free.position)

    def set_camera_slice(s):
        rail.slice = float(np.clip(s, rail.slices[0], rail.slices[-1]))
        app['cam'] = rail

    ui = ui_mod.UiState(ed, camera_slice, set_camera_slice)

    def focus(s, top=False, height=120.0, xz=None, direction=0):
        """AI mode: camera to a slice. Top view looks straight down with the
        driving direction up the screen; otherwise a chase view a few slices
        behind (the rail camera for forward races, the free one for reverse)."""
        s = float(np.clip(s, rail.slices[0], rail.slices[-1]))
        sign = -1.0 if direction else 1.0
        t = ui.ai.track if ui.ai is not None else None

        def road_at(x):
            # the vroad centre (on the road), else the CRP article centres
            if t is not None and t.has_vroad:
                k = np.arange(t.n)
                return np.array([np.interp(x, k, t.centre[:, 0], period=t.n),
                                 np.interp(x, k, t.centre[:, 1], period=t.n),
                                 -np.interp(x, k, t.centre[:, 2], period=t.n)])
            return rail.at(x)
        here = road_at(s)
        ahead = road_at(s + 8.0 * sign) - here
        if np.linalg.norm(ahead) < 1e-3:
            ahead = np.array([1.0, 0.0, 0.0])
        if top:
            target = here if xz is None or xz[0] is None else np.array([xz[0], here[1], -xz[1]])
            free.position = target + np.array([0.0, height, 0.0])
            free.yaw = float(np.arctan2(ahead[2], ahead[0]))
            free.pitch = -1.55
            app['cam'] = free
        elif direction == 0:
            rail.slice = float(np.clip(s - 12.0, rail.slices[0], rail.slices[-1]))
            app['cam'] = rail
        else:
            eye = road_at(s + 12.0) + np.array([0.0, 6.0, 0.0])
            look = here - eye
            free.position = eye
            free.yaw = float(np.arctan2(look[2], look[0]))
            free.pitch = float(np.arcsin(look[1] / (np.linalg.norm(look) + 1e-9)))
            app['cam'] = free

    import ai_ui
    ui.ai = ai_ui.AiView(os.path.abspath(track_dir), name, camera_slice, focus, mesh.centreline)
    if ai_mode:
        ui.mode = ui_mod.MODE_AI


    def post_init():
        app['ctx'] = moderngl.create_context()
        app['renderer'] = TrackRenderer(app['ctx'], mesh)
        app['sky'] = SkyRenderer(app['ctx'], name)
        from imgui_bundle import implot
        implot.create_context()
        from ai_render import OverlayRenderer
        app['overlay'] = OverlayRenderer(app['ctx'])
        ui.sky = app['sky']

    def background():
        renderer, ctx, sky = app['renderer'], app['ctx'], app['sky']
        if renderer is None:
            return
        io = imgui.get_io()
        w = int(io.display_size.x * io.display_framebuffer_scale.x)
        h = int(io.display_size.y * io.display_framebuffer_scale.y)
        if w <= 0 or h <= 0:
            return
        if app['version'] != ed.state.version:
            renderer.set_tint(ed.state.tint_table(renderer.n_slices))
            app['version'] = ed.state.version
        slc = camera_slice()
        sky.update(min(io.delta_time, 0.1), ed.state, slc, ui.preview)
        app['fog'] = (slc, ed.state.fog(slc)[1])
        draw_scene(ctx.screen, (w, h), renderer, sky, ed.state, app['cam'], slc, ui.preview)
        if ui.mode == ui_mod.MODE_AI and ui.ai is not None and app.get('overlay'):
            ov = app['overlay']
            if ui.ai.dirty and ui.ai.track is not None:
                ov.upload(ui.ai.build_mesh())
                ui.ai.dirty = False
            ov.on_top = ui.ai.on_top
            ov.draw(perspective(FOV, w / h, NEAR, FAR) @ app['cam'].view())

    def handle_input(io):
        cam = app['cam']
        dt = min(io.delta_time, 0.1)
        if io.want_text_input:
            return
        if imgui.is_key_pressed(imgui.Key.tab, False):
            if cam is rail:
                free.position = rail.position_forward()[0].copy()
                app['cam'] = free
            else:
                app['cam'] = rail
            cam = app['cam']
        down = imgui.is_key_down
        fast = down(imgui.Key.left_shift)
        walk = float(down(imgui.Key.w)) - float(down(imgui.Key.s))
        if cam is free:
            if imgui.is_mouse_down(1) and not io.want_capture_mouse:
                free.look(io.mouse_delta.x, io.mouse_delta.y)
            free.move(dt, walk, float(down(imgui.Key.d)) - float(down(imgui.Key.a)),
                      float(down(imgui.Key.space)) - float(down(imgui.Key.left_ctrl)), fast)
        else:
            rail.move(dt, walk, float(down(imgui.Key.up_arrow)) - float(down(imgui.Key.down_arrow)), fast)

    def ai_script(frame):
        """--ai-ui-selftest: the AI mode with every panel, colour mode and a jump."""
        v = ui.ai
        if frame == 2:
            ui.mode = ui_mod.MODE_AI
        elif frame < 400 and v.trace is None:
            return                               # trace scan + load
        elif 'ai_t0' not in app:
            app['ai_t0'] = frame
            ev = v.visible_events()
            if ev:
                i, e = ev[0]
                v.selected_event = i
                v.dirty = True
                v.focus(float(e['slice']), True, 120.0, (e['x'], e['z']), v.line_dir())
        else:
            k = frame - app['ai_t0']
            if 1 <= k <= 6:
                v.colour_mode = k - 1
                v.centre_mode = k % 3
                v.lap = 0 if k == 3 else -1
                v.layers['lanes'] = k % 2 == 0
                v.dirty = True
            elif k == 8:
                v.focus(100.0, False, 120.0, None, 1)
            elif k >= 12:
                ov = app.get('overlay')
                ok = v.trace is not None and ov is not None and ov.count > 0 and not v.dirty
                print('AI UI SELFTEST: trace %s, overlay %d tri, camera %s at slice %.0f -> %s' % (
                    v.trace.label() if v.trace else None, ov.count // 3 if ov else -1,
                    app['cam'].name, camera_slice(), 'OK' if ok else 'FAIL'))
                if not ok:
                    app['errors'].append(RuntimeError('AI UI selftest failed'))
                hello_imgui.get_runner_params().app_shall_exit = True

    def script(frame):
        """--ui-selftest: drive the editor the way a user would."""
        if frame == 2:
            ed.load_preset('coastal')
        elif frame == 3:
            ed.add_key(round(ed.n_slices * 0.3))
        elif frame == 4:
            ed.move_key(ed.selected, ed.atmo.keys[ed.selected].slice + 5)
            ed.set_value(ed.selected, 'snow', 0.5)
        elif frame == 6:
            ed.save_ini(os.path.join(tempfile.gettempdir(), 'atmo_ui_selftest.ini'))
            # SKY3: the dumped parts preview as the horizon (nothing written)
            hz = ui.horizon
            if hz is not None and hz.target is not None and all(hz.paths):
                hz.build(ui.sky)
                ok = hz.imp is not None and ui.sky.horizon_tex.size == hz.imp.size
                print('UI SELFTEST horizon: %s %s -> %s' % (hz.target.key, hz.message, 'OK' if ok else 'FAIL'))
                if not ok:
                    app['errors'].append(RuntimeError('SKY3 horizon preview failed'))
                ui.sky.reload_horizon()
        elif frame >= 8:
            hello_imgui.get_runner_params().app_shall_exit = True

    def gui():
        try:
            io = imgui.get_io()
            handle_input(io)
            ui.status = app['fog']
            ui_mod.draw_gui(ui, io.display_size.x, io.display_size.y)
            if ui_selftest:
                app['frame'] += 1
                (ai_script if ai_ui_selftest else script)(app['frame'])
        except Exception as e:      # noqa: BLE001 -- surfaced after the loop ends
            app['errors'].append(e)
            hello_imgui.get_runner_params().app_shall_exit = True

    rp = hello_imgui.RunnerParams()
    rp.app_window_params.window_title = 'track_editor: %s' % name
    rp.app_window_params.window_geometry.size = (1600, 900)
    rp.app_window_params.hidden = ui_selftest
    rp.imgui_window_params.default_imgui_window_type = \
        hello_imgui.DefaultImGuiWindowType.no_default_window
    # panels can leave the main window
    rp.imgui_window_params.enable_viewports = True
    rp.fps_idling.enable_idling = False
    rp.callbacks.post_init = post_init
    rp.callbacks.custom_background = background
    rp.callbacks.show_gui = gui
    hello_imgui.run(rp)
    if app['errors']:
        raise app['errors'][0]
    if ui_selftest:
        print('UI SELFTEST OK: %d keys, ini %s' % (len(ed.atmo.keys), ed.ini_path))
    return 0


def main(argv):
    ini = None
    if '--atmosphere' in argv:
        i = argv.index('--atmosphere')
        ini = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith('--')]
    if len(args) != 2:
        print(__doc__)
        return 2
    track_dir, name = args
    t0 = time.perf_counter()
    mesh = track_mesh.load_track(track_dir, name)
    print('%s: %d triangles, %d batches, loaded in %.1f s'
          % (name, len(mesh.positions) // 3, len(mesh.batches), time.perf_counter() - t0))
    if '--ai-selftest' in argv:
        return ai_selftest(mesh, track_dir, name)
    if '--selftest' in argv:
        state = AtmosphereState.for_track(name, ini)
        print('atmosphere: %d keys' % len(state.atmo.keys))
        return selftest(mesh, state)
    ai_ui_test = '--ai-ui-selftest' in argv
    return run(mesh, name, ini, ui_selftest='--ui-selftest' in argv or ai_ui_test,
               track_dir=track_dir, ai_ui_selftest=ai_ui_test, ai_mode='--ai' in argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
