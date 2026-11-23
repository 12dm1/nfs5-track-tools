"""Panels.  The left column is the whole pipeline top to bottom; the rest of
the window is the viewport."""

import os
import time

import numpy as np
from imgui_bundle import imgui, portable_file_dialogs as pfd

import imageops as io
import pipeline
import seams
import view as view_mod

PANEL_W = 370
AXES = ('xy', 'x', 'y', '')
AXIS_LABELS = {'xy': 'X and Y', 'x': 'X only', 'y': 'Y only', '': 'off'}
IMG_FILTER = ['Images', '*.png *.bmp *.tga *.jpg *.jpeg *.tif *.tiff', 'All files', '*']


class UiState:
    def __init__(self, state):
        self.state = state
        self.layout = 'tile'
        self.compare = 'result'
        self.wipe = 0.5
        self.diff_gain = 4.0
        self.preview_max = 384
        self.result = None              # (rgb, alpha) at preview resolution
        self.result_sig = None
        self.ref_preview = None
        self.seam_stats = {}
        self.last_ms = 0.0
        self.status = ''
        self.dialog = None              # (kind, pfd object)
        self.viewport = None
        self.dirty = True

    # ------------------------------------------------------------- files ---
    def load(self, kind, path):
        try:
            src = pipeline.Source(path)
        except Exception as e:          # noqa: BLE001 -- shown in the status line
            self.status = 'cannot open %s: %s' % (os.path.basename(path), e)
            return
        if not src.has_file_alpha and src.magenta_hits > 0:
            src.use_key = True
        setattr(self.state, 'new' if kind == 'new' else 'orig', src)
        self.status = '%s: %s %dx%d%s' % (kind, src.name, src.w, src.h,
                                          ', alpha' if src.has_file_alpha else '')
        if kind == 'new' and self.viewport is not None:
            self.viewport.reset_view()
        self.dirty = True

    def pick(self, kind):
        self.dialog = (kind, pfd.open_file('Open %s texture' % kind, os.getcwd(), IMG_FILTER))

    def tick_dialog(self):
        if self.dialog is None:
            return
        kind, dlg = self.dialog
        if not dlg.ready():
            return
        res = dlg.result()
        self.dialog = None
        if kind == 'save':
            if res:
                self.save(res if isinstance(res, str) else res[0])
        elif res:
            self.load(kind, res[0])

    # ---------------------------------------------------------- pipeline ---
    def recompute(self):
        sig = self.state.signature()
        if sig == self.result_sig and not self.dirty:
            return False
        t = time.perf_counter()
        out = pipeline.process(self.state, self.preview_max)
        self.last_ms = (time.perf_counter() - t) * 1000.0
        self.result_sig = sig
        self.dirty = False
        if out is None:
            self.result = None
            return False
        self.result = out
        rgb, a = out
        stack = np.dstack([rgb, a if a is not None else np.ones(rgb.shape[:2], np.float32)])
        self.seam_stats = seams.seam_error(stack, self.state.axis or 'xy')
        self.ref_preview = pipeline.reference_at(self.state, (rgb.shape[1], rgb.shape[0]))
        return True

    def save(self, path):
        if self.state.new is None:
            return
        if not os.path.splitext(path)[1]:
            path += '.png'
        rgb, a = pipeline.process(self.state, 0)
        io.save_png(path, rgb, a)
        self.status = 'saved %s  %dx%d' % (os.path.basename(path), rgb.shape[1], rgb.shape[0])

    def default_save_path(self):
        if self.state.new is None:
            return os.getcwd()
        base, _ = os.path.splitext(self.state.new.path)
        w, h = self.state.target_size()
        return '%s_tile_%dx%d.png' % (base, w, h)


# --------------------------------------------------------------- widgets ---

def _combo(label, items, current, labels=None):
    names = [labels[i] if labels else str(i) for i in items]
    idx = items.index(current) if current in items else 0
    changed, idx = imgui.combo(label, idx, names)
    return changed, items[idx]


def _transparency(src, title, ui):
    if src is None:
        return
    if src.has_file_alpha:
        imgui.text_disabled('%s: alpha channel in file' % title)
        return
    if imgui.tree_node_ex('%s transparency' % title, imgui.TreeNodeFlags_.default_open
                          if src.magenta_hits else 0):
        changed, v = imgui.checkbox('key colour##%s' % title, src.use_key)
        if changed:
            src.use_key = v
            ui.dirty = True
        if src.use_key:
            changed, col = imgui.color_edit3('colour##%s' % title, list(src.key_colour))
            if changed:
                src.key_colour = list(col)
                ui.dirty = True
            changed, t = imgui.slider_float('tolerance##%s' % title, src.key_tol, 0.0, 0.3)
            if changed:
                src.key_tol = t
                ui.dirty = True
            hits = io.key_colour_hits(src.rgb, src.key_colour, src.key_tol)
            imgui.text_disabled('%d px (%.1f%%) transparent' % (hits, 100.0 * hits / (src.w * src.h)))
        elif src.magenta_hits:
            imgui.text_disabled('%d magenta px found' % src.magenta_hits)
        imgui.tree_pop()


def _files(ui):
    st = ui.state
    imgui.separator_text('Files')
    if imgui.button('Load new texture...', imgui.ImVec2(-1, 0)):
        ui.pick('new')
    if st.new is not None:
        imgui.text('%s  %dx%d' % (st.new.name, st.new.w, st.new.h))
        _transparency(st.new, 'new', ui)
    else:
        imgui.text_disabled('the AI texture goes here')
    imgui.spacing()
    if imgui.button('Load original...', imgui.ImVec2(-1, 0)):
        ui.pick('orig')
    if st.orig is not None:
        imgui.text('%s  %dx%d' % (st.orig.name, st.orig.w, st.orig.h))
        imgui.same_line()
        if imgui.small_button('x##clearorig'):
            st.orig = None
            ui.dirty = True
        _transparency(st.orig, 'original', ui)
    else:
        imgui.text_disabled('optional: drives the colour match')


def _tone(ui):
    st = ui.state
    imgui.separator_text('Tone')
    has_ref = st.orig is not None
    if imgui.button('Auto'):
        if st.new is not None and has_ref:
            st.match, st.tone_map = 1.0, 0.7
            ui.dirty = True
    imgui.same_line()
    if imgui.button('Reset##tone'):
        st.match = st.tone_map = st.exposure = st.contrast = st.hue = 0.0
        st.saturation = st.gamma = 1.0
        ui.dirty = True
    if not has_ref:
        imgui.same_line()
        imgui.text_disabled('(no original)')
    imgui.begin_disabled(not has_ref)
    changed, st.match_mode = _combo('match##mode', ('meanstd', 'hist'), st.match_mode,
                                    {'meanstd': 'mean / contrast', 'hist': 'histogram'})
    ui.dirty |= changed
    changed, st.match = imgui.slider_float('match', st.match, 0.0, 1.0)
    ui.dirty |= changed
    changed, st.tone_map = imgui.slider_float('tone map', st.tone_map, 0.0, 1.0)
    ui.dirty |= changed
    if imgui.is_item_hovered():
        imgui.set_tooltip('Transfers the large-scale shading of the original\n'
                          'onto the new detail -- the old textures have the\n'
                          'lighting baked in.')
    imgui.end_disabled()
    imgui.spacing()
    for label, attr, lo, hi in (('exposure', 'exposure', -2.0, 2.0),
                                ('contrast', 'contrast', -1.0, 1.0),
                                ('saturation', 'saturation', 0.0, 2.0),
                                ('hue', 'hue', -180.0, 180.0),
                                ('gamma', 'gamma', 0.4, 2.5)):
        changed, v = imgui.slider_float(label, getattr(st, attr), lo, hi)
        if changed:
            setattr(st, attr, v)
            ui.dirty = True


def _seam(ui):
    st = ui.state
    imgui.separator_text('Seam')
    changed, st.axis = _combo('tiling axis', AXES, st.axis, AXIS_LABELS)
    ui.dirty |= changed
    imgui.begin_disabled(not st.axis)
    changed, st.seam_mode = _combo('method', seams.MODES, st.seam_mode, seams.MODE_LABELS)
    ui.dirty |= changed
    imgui.begin_disabled(st.seam_mode == 'poisson')
    changed, st.overlap_pct = imgui.slider_float('overlap %', st.overlap_pct, 1.0, 20.0)
    ui.dirty |= changed
    if imgui.is_item_hovered():
        imgui.set_tooltip('The image is stretched by this much and the spare\n'
                          'material is merged into the opposite edge.\n'
                          'Wider = safer join, more of the edge resampled.')
    imgui.begin_disabled(st.seam_mode == 'wrap')
    changed, st.feather = imgui.slider_int('feather px', st.feather, 0, 8)
    ui.dirty |= changed
    imgui.end_disabled()
    imgui.end_disabled()
    imgui.end_disabled()
    for ax, (err, floor) in sorted(ui.seam_stats.items()):
        ok = err <= floor * 1.6
        imgui.text_colored(imgui.ImVec4(0.5, 0.9, 0.5, 1) if ok else imgui.ImVec4(0.95, 0.6, 0.3, 1),
                           '%s seam %.4f   (texture floor %.4f)' % (ax.upper(), err, floor))


def _output(ui):
    st = ui.state
    imgui.separator_text('Output')
    items = [pipeline.SIZE_SAME, pipeline.SIZE_ORIGINAL] + list(range(len(pipeline.SIZES)))
    labels = {pipeline.SIZE_SAME: 'same as new', pipeline.SIZE_ORIGINAL: 'same as original'}
    labels.update({i: str(n) for i, n in enumerate(pipeline.SIZES)})
    changed, st.size_idx = _combo('size', items, st.size_idx, labels)
    ui.dirty |= changed
    changed, v = imgui.checkbox('keep aspect ratio', st.keep_aspect)
    if changed:
        st.keep_aspect = v
        ui.dirty = True
    changed, st.alpha_source = _combo('alpha', pipeline.ALPHA_SOURCES, st.alpha_source,
                                      pipeline.ALPHA_LABELS)
    ui.dirty |= changed
    changed, v = imgui.checkbox('write key colour instead of alpha', st.write_key)
    if changed:
        st.write_key = v
        ui.dirty = True
    if st.write_key:
        changed, col = imgui.color_edit3('out key', list(st.out_key))
        if changed:
            st.out_key = list(col)
            ui.dirty = True
    w, h = st.target_size()
    imgui.text_disabled('result: %d x %d' % (w, h))
    imgui.begin_disabled(st.new is None)
    if imgui.button('Save PNG...', imgui.ImVec2(-1, 0)):
        ui.dialog = ('save', pfd.save_file('Save texture', ui.default_save_path(),
                                           ['PNG', '*.png']))
    if imgui.button('Quick save next to the source', imgui.ImVec2(-1, 0)):
        ui.save(ui.default_save_path())
    imgui.end_disabled()


def _viewport(ui, x, y, w, h):
    st = ui.state
    imgui.set_next_window_pos(imgui.ImVec2(x, y))
    imgui.set_next_window_size(imgui.ImVec2(w, h))
    imgui.begin('View', None, imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_collapse
                | imgui.WindowFlags_.no_bring_to_front_on_focus)
    imgui.text_disabled('layout')
    for m in view_mod.LAYOUTS:
        imgui.same_line()
        if imgui.radio_button(view_mod.LAYOUT_LABELS[m], ui.layout == m):
            ui.layout = m
    imgui.same_line()
    imgui.text_disabled('   compare')
    has_ref = ui.ref_preview is not None
    imgui.begin_disabled(not has_ref)
    for m in view_mod.COMPARES:
        imgui.same_line()
        if imgui.radio_button(view_mod.COMPARE_LABELS[m], ui.compare == m):
            ui.compare = m
    imgui.end_disabled()
    if not has_ref:
        imgui.same_line()
        imgui.text_disabled('(load an original)')
        ui.compare = 'result'
    if ui.compare == 'diff':
        imgui.same_line()
        imgui.set_next_item_width(110)
        _, ui.diff_gain = imgui.slider_float('gain', ui.diff_gain, 1.0, 16.0)

    imgui.set_next_item_width(140)
    _, ui.viewport.zoom = imgui.slider_float('zoom', ui.viewport.zoom, 0.25, 8.0)
    imgui.same_line()
    if imgui.button('Fit'):
        ui.viewport.reset_view()
    imgui.same_line()
    imgui.set_next_item_width(150)
    changed, ui.preview_max = _combo('preview', (256, 384, 512, 768, 0), ui.preview_max,
                                     {256: '256 px', 384: '384 px', 512: '512 px',
                                      768: '768 px', 0: 'full res'})
    if changed:
        ui.dirty = True
    if imgui.is_item_hovered():
        imgui.set_tooltip('Working resolution while you drag the sliders.\n'
                          'Saving always runs the chain at full resolution.')

    avail = imgui.get_content_region_avail()
    vw, vh = int(max(16, avail.x)), int(max(16, avail.y - 26))
    if ui.result is not None:
        tex = ui.viewport.render(vw, vh, ui.layout, ui.compare, ui.wipe,
                                 has_ref, ui.diff_gain)
        pos = imgui.get_cursor_screen_pos()
        imgui.image(imgui.ImTextureRef(tex.glo), imgui.ImVec2(vw, vh),
                    imgui.ImVec2(0, 1), imgui.ImVec2(1, 0))
        if imgui.is_item_hovered():
            mx = (imgui.get_io().mouse_pos.x - pos.x) / float(vw)
            if ui.compare == 'wipe' and imgui.is_mouse_down(0):
                ui.wipe = float(np.clip(mx, 0.0, 1.0))
            wheel = imgui.get_io().mouse_wheel
            if wheel:
                ui.viewport.zoom = float(np.clip(ui.viewport.zoom * (1.1 ** wheel), 0.25, 16.0))
            if imgui.is_mouse_dragging(2):
                d = imgui.get_io().mouse_delta
                ui.viewport.pan[0] -= d.x / float(vw) / ui.viewport.zoom
                ui.viewport.pan[1] -= d.y / float(vh) / ui.viewport.zoom
    else:
        imgui.dummy(imgui.ImVec2(vw, vh))
        imgui.text_disabled('Load a texture to start (or drop a file on the window).')
    rgb = ui.result[0] if ui.result else None
    imgui.text_disabled('preview %s | %.0f ms | %s' % (
        'x'.join(str(v) for v in (rgb.shape[1], rgb.shape[0])) if rgb is not None else '-',
        ui.last_ms, ui.status))
    imgui.end()


def draw_gui(ui, w, h):
    ui.tick_dialog()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(PANEL_W, h))
    imgui.begin('Controls', None, imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_collapse)
    # Output is pinned to the bottom: the size dropdown and Save must never be
    # the thing that scrolled off the end of the panel.
    rows = 10 + (1 if ui.state.write_key else 0)
    footer = rows * imgui.get_frame_height_with_spacing() + 24
    avail = imgui.get_content_region_avail()
    imgui.begin_child('scroll', imgui.ImVec2(0, max(80.0, avail.y - footer)))
    _files(ui)
    _tone(ui)
    _seam(ui)
    imgui.end_child()
    imgui.separator()
    _output(ui)
    imgui.end()
    if ui.recompute() and ui.result is not None:
        ui.viewport.set_result(*ui.result)
        ui.viewport.set_reference(ui.ref_preview)
    _viewport(ui, PANEL_W, 0, max(64, w - PANEL_W), h)
