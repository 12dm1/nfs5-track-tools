"""Texture seam and tone editor.

    python tools/texture_editor [new.png] [original.png] [--selftest | --ui-selftest]

Drop an AI-regenerated texture and (optionally) the original it replaces, pick
the tiling axis, and the seam is gone.  The colour sliders pull the new texture
back to the original's tone; the size dropdown writes the power-of-two the game
wants.  Drag and drop works: first file dropped is the new texture, second the
original.
"""

import os
import sys
import tempfile

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import imageops as io      # noqa: E402
import pipeline            # noqa: E402
import seams               # noqa: E402
import tone as tone_mod    # noqa: E402


# -------------------------------------------------------------- selftest ---

def _synthetic(tmp):
    """A texture with a hard tone ramp (so the seam is glaring), a hole in the
    alpha, and a darker, smaller 'original' to match against."""
    rng = np.random.default_rng(7)
    h = w = 192
    base = io.gauss_blur(rng.random((h, w, 3)).astype(np.float32), 2.0)
    ramp = np.linspace(0.0, 0.45, w, dtype=np.float32)[None, :, None]
    new = np.clip(base * 0.7 + ramp + 0.2, 0, 1)
    alpha = np.ones((h, w), np.float32)
    alpha[20:60, 20:60] = 0.0
    new_path = os.path.join(tmp, 'new.png')
    io.save_png(new_path, new, alpha)

    orig = np.clip(io.gauss_blur(rng.random((64, 64, 3)).astype(np.float32), 2.0) * 0.35, 0, 1)
    key = orig.copy()
    key[8:20, 8:20] = pipeline.MAGENTA          # key colour, no alpha channel
    orig_path = os.path.join(tmp, 'orig.png')
    io.save_png(orig_path, key)
    return new_path, orig_path


def selftest():
    tmp = tempfile.mkdtemp(prefix='texture_editor_')
    new_path, orig_path = _synthetic(tmp)
    st = pipeline.State()
    st.new = pipeline.Source(new_path)
    st.orig = pipeline.Source(orig_path)
    ok = True

    def check(name, cond, note=''):
        nonlocal ok
        ok = ok and bool(cond)
        print('  %-34s %s %s' % (name, 'OK' if cond else 'FAIL', note))

    check('new: alpha channel read', st.new.has_file_alpha and st.new.binary_alpha())
    check('original: magenta detected', st.orig.magenta_hits == 144,
          '%d px' % st.orig.magenta_hits)
    st.orig.use_key = True
    check('original: key mask', abs(st.orig.alpha().sum() - (64 * 64 - 144)) < 1)
    bled = st.new.stack()[:, :, :3]
    check('bleed fill covers the hole', bled[20:60, 20:60].std() > 1e-4)

    raw = np.dstack([st.new.rgb, st.new.stack()[:, :, 3]])
    before = seams.seam_error(raw, 'xy')
    for mode in seams.MODES:
        st.seam_mode = mode
        st.axis = 'xy'
        rgb, a = pipeline.process(st, 0)
        stack = np.dstack([rgb, a if a is not None else np.ones(rgb.shape[:2], np.float32)])
        err = seams.seam_error(stack, 'xy')
        good = all(err[k][0] <= max(err[k][1] * 1.6, 0.004) for k in err)
        check('seam %-8s x %.4f y %.4f' % (mode, err['x'][0], err['y'][0]), good,
              '(was x %.4f)' % before['x'][0])

    st.seam_mode = 'freq'
    st.match, st.tone_map = 1.0, 0.7
    rgb, _ = pipeline.process(st, 0)
    ref = st.orig.stack()[:, :, :3]
    m_new = io.rgb_to_lab(rgb)[:, :, 0].mean()
    m_ref = io.rgb_to_lab(ref)[:, :, 0].mean()
    m_raw = io.rgb_to_lab(st.new.rgb)[:, :, 0].mean()
    check('tone match pulls L towards ref', abs(m_new - m_ref) < abs(m_raw - m_ref) * 0.5,
          'L %.1f -> %.1f, ref %.1f' % (m_raw, m_new, m_ref))

    st.size_idx = pipeline.SIZES.index(128)
    rgb, a = pipeline.process(st, 0)
    check('resize to 128', rgb.shape[:2] == (128, 128))
    stack = np.dstack([rgb, a if a is not None else np.ones(rgb.shape[:2], np.float32)])
    err = seams.seam_error(stack, 'xy')
    check('seam survives the resize', all(err[k][0] <= max(err[k][1] * 1.8, 0.004) for k in err),
          'x %.4f y %.4f' % (err['x'][0], err['y'][0]))
    check('alpha still binary', a is not None and io.is_binary(a))

    st.write_key = True
    rgb, a = pipeline.process(st, 0)
    check('key colour written, alpha dropped', a is None
          and io.key_colour_hits(rgb, st.out_key, 0.02) > 100)

    st.write_key = False
    st.alpha_source = 'original'
    rgb, a = pipeline.process(st, 0)
    check('alpha taken from the original', a is not None and 0.0 < a.mean() < 1.0)

    st2 = pipeline.State()
    st2.new = pipeline.Source(new_path)
    st2.match = 1.0
    check('works without an original', pipeline.process(st2, 0)[0].shape[:2] == (192, 192))

    out = os.path.join(tmp, 'out.png')
    io.save_png(out, *pipeline.process(st, 0))
    check('saved file re-reads', io.load_image(out)[0].shape[:2] == (128, 128))
    print('texture_editor selftest: %s  (%s)' % ('OK' if ok else 'FAILED', tmp))
    return 0 if ok else 1


# ------------------------------------------------------------------- app ---

def run(new_path, orig_path, ui_selftest=False):
    from imgui_bundle import hello_imgui, imgui
    import moderngl

    import ui as ui_mod
    import view as view_mod

    st = pipeline.State()
    ui = ui_mod.UiState(st)
    app = {'ctx': None, 'errors': [], 'frame': 0}

    def post_init():
        app['ctx'] = moderngl.create_context()
        ui.viewport = view_mod.Viewport(app['ctx'])
        if new_path:
            ui.load('new', new_path)
        if orig_path:
            ui.load('orig', orig_path)
        _install_drop_handler(ui)

    def gui():
        try:
            size = imgui.get_io().display_size
            ui_mod.draw_gui(ui, size.x, size.y)
            if ui_selftest:
                app['frame'] += 1
                _script(ui, app, hello_imgui)
        except Exception as e:          # noqa: BLE001 -- re-raised after the loop
            app['errors'].append(e)
            hello_imgui.get_runner_params().app_shall_exit = True

    rp = hello_imgui.RunnerParams()
    rp.app_window_params.window_title = 'texture_editor'
    rp.app_window_params.window_geometry.size = (1500, 900)
    rp.app_window_params.hidden = ui_selftest
    rp.imgui_window_params.default_imgui_window_type = \
        hello_imgui.DefaultImGuiWindowType.no_default_window
    rp.fps_idling.enable_idling = False
    rp.callbacks.post_init = post_init
    rp.callbacks.show_gui = gui
    hello_imgui.run(rp)
    if app['errors']:
        raise app['errors'][0]
    return 0


def _install_drop_handler(ui):
    """hello_imgui owns the window, so reach through to glfw for file drops."""
    try:
        import ctypes
        import glfw
        from imgui_bundle import hello_imgui
        ptr = hello_imgui.get_runner_params().backend_pointers.glfw_window
        if not ptr:
            return
        window = ctypes.cast(ctypes.c_void_p(int(ptr)), ctypes.POINTER(glfw._GLFWwindow))

        def on_drop(_win, paths):
            for i, p in enumerate(paths):
                p = p.decode('utf-8', 'replace') if isinstance(p, bytes) else p
                if i == 0 and ui.state.new is None:
                    ui.load('new', p)
                elif i == 0:
                    ui.load('orig' if ui.state.orig is None else 'new', p)
                elif i == 1:
                    ui.load('orig', p)

        ui._drop_cb = glfw.set_drop_callback(window, on_drop)
    except Exception as e:              # noqa: BLE001 -- the buttons still work
        ui.status = 'drag and drop unavailable (%s)' % e


def _script(ui, app, hello_imgui):
    """--ui-selftest: drive the panels the way a user would."""
    import view as view_mod
    f = app['frame']
    if f == 2:
        tmp = tempfile.mkdtemp(prefix='texture_editor_ui_')
        n, o = _synthetic(tmp)
        ui.load('new', n)
        ui.load('orig', o)
    elif f == 4:
        ui.state.match, ui.state.tone_map = 1.0, 0.7
        ui.dirty = True
    elif f == 6:
        ui.state.seam_mode = 'cut'
        ui.state.size_idx = pipeline.SIZES.index(256)
        ui.dirty = True
    elif f == 8:
        ui.layout, ui.compare = 'tile', 'wipe'
    elif f == 10:
        ui.layout, ui.compare = 'offset', 'diff'
    elif f == 12:
        ui.save(os.path.join(tempfile.gettempdir(), 'texture_editor_ui_out.png'))
    elif f >= 14:
        import moderngl
        ok = (ui.result is not None and ui.result[0].shape[:2] == (256, 256)
              and ui.viewport.tex_new is not None and ui.viewport.tex_ref is not None
              and all(e <= max(fl * 1.8, 0.004) for e, fl in ui.seam_stats.values())
              and os.path.exists(os.path.join(tempfile.gettempdir(), 'texture_editor_ui_out.png')))
        # Every mode must put real pixels in the offscreen target, and that
        # target must be a texture -- imgui samples it by GL name, so a
        # renderbuffer would silently show whatever texture shares the id.
        spread = {}
        for lay in view_mod.LAYOUTS:
            for cmp_ in view_mod.COMPARES:
                ui.layout, ui.compare = lay, cmp_
                ui.viewport.render(320, 240, lay, cmp_, 0.5, True, ui.diff_gain)
                px = np.frombuffer(ui.viewport.fbo.read(components=3), np.uint8)
                spread['%s/%s' % (lay, cmp_)] = int(px.max()) - int(px.min())
        ok = ok and isinstance(ui.viewport.fbo_tex, moderngl.Texture)
        ok = ok and all(v > 8 for v in spread.values())
        print('UI SELFTEST: preview %s, seams %s, viewport spread %s -> %s' % (
            ui.result[0].shape if ui.result else None,
            {k: '%.4f' % v[0] for k, v in ui.seam_stats.items()}, spread,
            'OK' if ok else 'FAIL'))
        if not ok:
            app['errors'].append(RuntimeError('texture_editor UI selftest failed'))
        hello_imgui.get_runner_params().app_shall_exit = True


def main(argv):
    if '--selftest' in argv:
        return selftest()
    args = [a for a in argv if not a.startswith('--')]
    new_path = args[0] if len(args) > 0 else None
    orig_path = args[1] if len(args) > 1 else None
    for p in (new_path, orig_path):
        if p and not os.path.exists(p):
            print('no such file: %s' % p)
            return 2
    return run(new_path, orig_path, ui_selftest='--ui-selftest' in argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
