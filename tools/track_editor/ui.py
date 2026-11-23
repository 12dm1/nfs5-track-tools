"""imgui panels of the atmosphere editor: files/presets, Sky, Keys, curve
lanes, read-only sky layers. Every edit goes through `Editor` so the preview
(`AtmosphereState`) is recompiled on the spot."""

import os

from imgui_bundle import imgui

import editor as ed_mod
import skygeo
from sky_render import Preview

COL_TEXT_DIM = imgui.ImVec4(0.6, 0.6, 0.6, 1.0)
COL_WARN = imgui.ImVec4(1.0, 0.75, 0.3, 1.0)
LANE_H = 44
STRIP_H = 26
RULER_H = 16
LABEL_W = 96


def _v2(x, y):
    return imgui.ImVec2(x, y)


def _vp(x, y):
    """A position inside the main window: with viewports enabled imgui
    window positions are desktop coordinates."""
    o = imgui.get_main_viewport().pos
    return imgui.ImVec2(o.x + x, o.y + y)


def _u32(r, g, b, a=255):
    return imgui.IM_COL32(int(r), int(g), int(b), int(a))


def _rgb01(c):
    return [c[0] / 255.0, c[1] / 255.0, c[2] / 255.0]


def _rgb255(c):
    return tuple(max(0, min(255, int(round(v * 255.0)))) for v in c[:3])


MODE_ATMOSPHERE = 0
MODE_AI = 2


class UiState:
    """Interaction state that lives between frames."""

    def __init__(self, editor, camera_slice, set_camera_slice):
        self.editor = editor
        self.camera_slice = camera_slice          # callable -> float
        self.set_camera_slice = set_camera_slice  # callable(float)
        self.drag = None                          # (lane attr, key index)
        self.preset = 0
        self.show_layers = True
        self.status = None                        # (slice, cubed fog density)
        self.preview = Preview()
        self.sky = None                           # SkyRenderer once GL is up
        self.mode = MODE_ATMOSPHERE
        self.ai = None                            # ai_ui.AiView, set from __main__
        self.horizon = None                       # HorizonUi, made on first draw


# ------------------------------------------------------------------ files ---

def panel_file(ui):
    ed = ui.editor
    imgui.set_next_window_pos(_vp(10, 10), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(330, 250), imgui.Cond_.first_use_ever)
    if imgui.begin('File')[0]:
        imgui.text('track: %s   slices: %d' % (ed.track, ed.n_slices))
        if ed.dirty:
            imgui.text_colored(COL_WARN, 'unsaved changes')
        imgui.text('ini: %s' % ed.ini_path)
        if ui.status:
            imgui.text('slice %d  world fog (cubed) %.5f' % ui.status)
        if imgui.button('Save ini'):
            ed.save_ini()
        imgui.same_line()
        if imgui.button('Load ini'):
            try:
                ed.reload_ini()
            except OSError as e:
                ed.message = 'failed: %s' % e
        if imgui.button('Write Sky/%s.bin to game' % ed.track.capitalize()):
            ed.export_bin()
        imgui.separator()
        names = ed.preset_names()
        if names:
            ui.preset = min(ui.preset, len(names) - 1)
            _, ui.preset = imgui.combo('preset', ui.preset, names)
            if imgui.button('Load preset (slices by track fraction)'):
                try:
                    ed.load_preset(names[ui.preset])
                except (OSError, ValueError) as e:
                    ed.message = 'failed: %s' % e
        imgui.separator()
        imgui.text_wrapped(ed.message)
        imgui.text_colored(COL_TEXT_DIM, 'TAB camera, RMB look, WASD/Space/Ctrl')
    imgui.end()


# -------------------------------------------------------------------- sky ---

def _slider(ed, label, obj, attr, lo, hi, fmt='%.3f'):
    changed, v = imgui.slider_float(label, float(getattr(obj, attr)), lo, hi, fmt)
    if changed:
        setattr(obj, attr, v)
        ed.touch()


def _colour(ed, label, obj, attr):
    changed, v = imgui.color_edit3(label, _rgb01(getattr(obj, attr)))
    if changed:
        setattr(obj, attr, _rgb255(v))
        ed.touch()


def panel_sky(ui):
    ed = ui.editor
    d, c = ed.atmo.sky, ed.atmo.sky_colour
    imgui.set_next_window_pos(_vp(10, 270), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(330, 430), imgui.Cond_.first_use_ever)
    if imgui.begin('Sky')[0]:
        if imgui.collapsing_header('Dome', imgui.TreeNodeFlags_.default_open):
            _slider(ed, 'radius', d, 'dome_radius', 100.0, 3000.0, '%.1f')
            _slider(ed, 'curvature', d, 'dome_curvature', 0.0, 0.001, '%.6f')
            _slider(ed, 'height', d, 'dome_height', -200.0, 400.0, '%.1f')
            _slider(ed, 'rotation (turns)', d, 'angle', 0.0, 1.0, '%.4f')
            _slider(ed, 'scale', d, 'scale', 0.05, 3.0)
        if imgui.collapsing_header('Sun', imgui.TreeNodeFlags_.default_open):
            _slider(ed, 'sun x', d, 'sun_x', -700.0, 700.0, '%.1f')
            _slider(ed, 'sun y', d, 'sun_y', -700.0, 700.0, '%.1f')
            changed, v = imgui.checkbox('lens flare', bool(d.lens_flare))
            if changed:
                d.lens_flare = int(v)
                ed.touch()
        if imgui.collapsing_header('Sky colour'):
            changed, v = imgui.checkbox('follow fog', bool(c.follow_fog))
            if changed:
                c.follow_fog = int(v)
                ed.touch()
            imgui.begin_disabled(bool(c.follow_fog))
            _colour(ed, 'colour A', c, 'colour_a')
            _colour(ed, 'colour B', c, 'colour_b')
            imgui.end_disabled()
            _colour(ed, 'water A', c, 'water_colour_a')
            _colour(ed, 'water B', c, 'water_colour_b')
        if imgui.collapsing_header('Horizon'):
            changed, v = imgui.slider_int('rows', d.horizon_rows, 1, 2)
            if changed:
                d.horizon_rows = v
                ed.touch()
            changed, v = imgui.slider_int('colour shift hzcl', d.horizon_rotation, 0, 15)
            if changed:
                d.horizon_rotation = v
                ed.touch()
            if d.horizon_angle is None:
                d.horizon_angle = 0.0
            _slider(ed, 'ring angle (turns)', d, 'horizon_angle', 0.0, 1.0, '%.4f')
            _slider(ed, 'bottom', d, 'horizon_bottom', -400.0, 100.0, '%.1f')
            _slider(ed, 'strip height', d, 'horizon_top', 0.0, 600.0, '%.1f')
            imgui.text_colored(COL_TEXT_DIM, 'ring r=400; top = bottom + height')
            imgui.text_colored(COL_TEXT_DIM, 'slots: panel 0..7, M = mirrored (+256)')
            for i in range(16):
                v = d.horizon_slots[i]
                panel, mirrored = v & 0xFF, bool(v & 0x100)
                imgui.push_id(i)
                imgui.set_next_item_width(60)
                changed_p, panel = imgui.input_int('##p', panel, 0, 0)
                imgui.same_line()
                changed_m, mirrored = imgui.checkbox('M', mirrored)
                imgui.pop_id()
                if changed_p or changed_m:
                    d.horizon_slots[i] = max(0, min(7, panel)) + (256 if mirrored else 0)
                    ed.touch()
                if i % 2 == 0:
                    imgui.same_line()
        if imgui.collapsing_header('Water copy'):
            _slider(ed, 'rotation (turns)##w', d, 'water_angle', 0.0, 1.0, '%.4f')
            _slider(ed, 'radius##w', d, 'water_dome_radius', 100.0, 3000.0, '%.1f')
            _slider(ed, 'curvature##w', d, 'water_dome_curvature', -0.001, 0.001, '%.6f')
            _slider(ed, 'height##w', d, 'water_dome_height', -400.0, 200.0, '%.1f')
    imgui.end()


# ------------------------------------------------------------------- keys ---

def panel_keys(ui):
    ed = ui.editor
    keys = ed.atmo.keys
    imgui.set_next_window_pos(_vp(350, 10), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(380, 500), imgui.Cond_.first_use_ever)
    if imgui.begin('Keys')[0]:
        imgui.text('keys: %d of %d' % (len(keys), ed_mod.MAX_KEYS))
        if imgui.begin_list_box('##keys', _v2(-1, 120)):
            for i, k in enumerate(keys):
                if imgui.selectable('slice %d' % k.slice, i == ed.selected)[0]:
                    ed.selected = i
            imgui.end_list_box()
        if imgui.button('Add at camera slice'):
            ed.add_key(round(ui.camera_slice()))
        imgui.same_line()
        if imgui.button('Delete'):
            ed.delete_key(ed.selected)
        imgui.same_line()
        if imgui.button('Go to camera'):
            ui.set_camera_slice(keys[ed.selected].slice)
        keys = ed.atmo.keys
        ed.selected = max(0, min(ed.selected, len(keys) - 1))
        k = keys[ed.selected]
        imgui.separator()
        changed, v = imgui.slider_int('slice', k.slice, 0, ed.n_slices - 1)
        if changed:
            ed.move_key(ed.selected, v)
            k = ed.atmo.keys[ed.selected]
        imgui.text('SKY and horizon fog (channel A)')
        _colour(ed, 'colour A', k, 'fog_colour')
        _slider(ed, 'density A', k, 'fog_density', 0.0, 0.3, '%.4f')
        imgui.text_colored(COL_TEXT_DIM, 'in-game (cubed): %.6f' % (k.fog_density ** 3))
        imgui.text('WORLD fog - track (channel B)')
        _colour(ed, 'colour B', k, 'fog_colour_b')
        _slider(ed, 'density B', k, 'fog_density_b', 0.0, 0.3, '%.4f')
        imgui.separator()
        _slider(ed, 'rain', k, 'rain', 0.0, 1.0)
        _slider(ed, 'snow', k, 'snow', 0.0, 1.0)
        _slider(ed, 'storm', k, 'storm', 0.0, 1.0)
        imgui.same_line()
        imgui.text(ed_mod.storm_label(k.storm))
        _slider(ed, 'region A<->B', k, 'region', 0.0, 1.0)
        changed, t = imgui.slider_float3('light tint', list(k.light_tint), 0.0, 2.0, '%.2f')
        if changed:
            k.light_tint = tuple(t)
            ed.touch()
        imgui.text_colored(COL_TEXT_DIM, 'tint only takes effect after track reconversion')
    imgui.end()


# ----------------------------------------------------------------- curves ---

def _x_of(slc, x0, x1, n):
    return x0 + (x1 - x0) * slc / float(max(n - 1, 1))


def _slice_of(x, x0, x1, n):
    return int(round((x - x0) / max(x1 - x0, 1.0) * (n - 1)))


def _polyline(dl, pts, col, thick=1.5):
    for a, b in zip(pts, pts[1:]):
        dl.add_line(_v2(*a), _v2(*b), col, thick)


def panel_curves(ui, display_w, display_h):
    ed = ui.editor
    n = ed.n_slices
    keys = ed.atmo.keys
    io = imgui.get_io()
    height = RULER_H + STRIP_H * 2 + LANE_H * (len(ed_mod.LANES) + 1) + 90
    imgui.set_next_window_pos(_vp(10, max(10, display_h - height - 10)), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(display_w - 20, height), imgui.Cond_.first_use_ever)
    if imgui.begin('Curves along track')[0]:
        dl = imgui.get_window_draw_list()
        origin = imgui.get_cursor_screen_pos()
        width = imgui.get_content_region_avail().x
        x0, x1 = origin.x + LABEL_W, origin.x + width - 8
        y = origin.y
        cam_x = _x_of(ui.camera_slice(), x0, x1, n)
        mouse = io.mouse_pos
        hit = 9.0

        # ruler: click or drag moves the camera along the road
        imgui.set_cursor_screen_pos(_v2(x0, y))
        imgui.invisible_button('##ruler', _v2(x1 - x0, RULER_H))
        if imgui.is_item_active():
            ui.set_camera_slice(max(0, min(n - 1, _slice_of(mouse.x, x0, x1, n))))
        dl.add_rect_filled(_v2(x0, y), _v2(x1, y + RULER_H), _u32(40, 40, 46))
        step = 100 if n > 600 else 50
        for s in range(0, n, step):
            xs = _x_of(s, x0, x1, n)
            dl.add_line(_v2(xs, y + 8), _v2(xs, y + RULER_H), _u32(140, 140, 150), 1.0)
            dl.add_text(_v2(xs + 2, y), _u32(170, 170, 180), str(s))
        y += RULER_H

        # fog colour strips: A = sky and horizon, B = world; click selects a key
        for label, attr in (('colour: sky A', 'fog_colour'), ('colour: world B', 'fog_colour_b')):
            dl.add_text(_v2(origin.x, y + 6), _u32(200, 200, 200), label)
            edge = ([(x0, getattr(keys[0], attr))]
                    + [(_x_of(k.slice, x0, x1, n), getattr(k, attr)) for k in keys]
                    + [(x1, getattr(keys[-1], attr))])
            for (xa, ca), (xb, cb) in zip(edge, edge[1:]):
                if xb > xa:
                    dl.add_rect_filled_multi_color(
                        _v2(xa, y), _v2(xb, y + STRIP_H - 2), _u32(*ca), _u32(*cb),
                        _u32(*cb), _u32(*ca))
            imgui.set_cursor_screen_pos(_v2(x0, y))
            imgui.invisible_button('##strip_' + attr, _v2(x1 - x0, STRIP_H - 2))
            if imgui.is_item_clicked():
                ed.selected = min(range(len(keys)),
                                  key=lambda i: abs(_x_of(keys[i].slice, x0, x1, n) - mouse.x))
            for i, k in enumerate(keys):
                xs = _x_of(k.slice, x0, x1, n)
                sel = i == ed.selected
                dl.add_line(_v2(xs, y), _v2(xs, y + STRIP_H - 2),
                            _u32(255, 230, 90) if sel else _u32(20, 20, 20), 2.0 if sel else 1.0)
            y += STRIP_H

        # scalar lanes with draggable handles
        for label, attr, top in ed_mod.LANES:
            _lane(ui, dl, label, attr, top, x0, x1, y, origin.x, mouse, hit)
            y += LANE_H

        # light tint lane (read-only curves; edited numerically in "Keys")
        dl.add_rect_filled(_v2(x0, y), _v2(x1, y + LANE_H - 4), _u32(28, 28, 34))
        dl.add_text(_v2(origin.x, y + 12), _u32(200, 200, 200), 'light tint')
        for ch, col in enumerate(((240, 90, 90), (90, 220, 110), (100, 150, 255))):
            pts = [(x0, y + LANE_H - 8 - keys[0].light_tint[ch] / 2.0 * (LANE_H - 12))]
            pts += [(_x_of(k.slice, x0, x1, n), y + LANE_H - 8 - k.light_tint[ch] / 2.0 * (LANE_H - 12)) for k in keys]
            pts += [(x1, y + LANE_H - 8 - keys[-1].light_tint[ch] / 2.0 * (LANE_H - 12))]
            _polyline(dl, pts, _u32(*col), 1.2)
        y += LANE_H

        # camera position across all lanes
        dl.add_line(_v2(cam_x, origin.y), _v2(cam_x, y - 4), _u32(255, 70, 70), 2.0)
        imgui.set_cursor_screen_pos(_v2(origin.x, y + 4))
        imgui.text_colored(COL_TEXT_DIM,
                           'drag points on lanes; double-click on a lane to add a key; '
                           'click the ruler to move camera to slice; red line = camera')
    imgui.end()


def _lane(ui, dl, label, attr, top, x0, x1, y, label_x, mouse, hit):
    ed = ui.editor
    n = ed.n_slices
    keys = ed.atmo.keys
    y0, y1 = y + 2, y + LANE_H - 6
    dl.add_rect_filled(_v2(x0, y0), _v2(x1, y1), _u32(28, 28, 34))
    dl.add_text(_v2(label_x, y + 12), _u32(200, 200, 200), label)
    dl.add_line(_v2(x0, y1), _v2(x1, y1), _u32(60, 60, 70), 1.0)

    def to_xy(k):
        return (_x_of(k.slice, x0, x1, n), y1 - min(getattr(k, attr) / top, 1.0) * (y1 - y0 - 4))

    pts = [to_xy(k) for k in keys]
    line = [(x0, pts[0][1])] + pts + [(x1, pts[-1][1])]
    _polyline(dl, line, _u32(120, 200, 255), 1.6)

    imgui.set_cursor_screen_pos(_v2(x0, y0))
    imgui.invisible_button('##lane_' + attr, _v2(x1 - x0, y1 - y0))
    hovered = imgui.is_item_hovered()
    if hovered and imgui.is_mouse_clicked(0):
        near = [i for i, (px, py) in enumerate(pts) if abs(px - mouse.x) < hit and abs(py - mouse.y) < hit]
        if near:
            ui.drag = (attr, keys[near[0]])      # by object: sorting can reorder indices
            ed.selected = near[0]
    if hovered and imgui.is_mouse_double_clicked(0):
        ed.add_key(_slice_of(mouse.x, x0, x1, n))
    if ui.drag and ui.drag[0] == attr:
        if imgui.is_mouse_down(0) and ui.drag[1] in ed.atmo.keys:
            idx = ed.atmo.keys.index(ui.drag[1])
            ed.selected = idx
            value = max(0.0, min(top, (y1 - mouse.y) / max(y1 - y0 - 4, 1.0) * top))
            setattr(ui.drag[1], attr, value)
            ed.move_key(idx, _slice_of(mouse.x, x0, x1, n))   # recompiles, keeps order
        else:
            ui.drag = None
    for i, (px, py) in enumerate(pts):
        sel = i == ed.selected
        dl.add_circle_filled(_v2(px, py), 5.0 if sel else 3.5,
                             _u32(255, 230, 90) if sel else _u32(120, 200, 255))


# ----------------------------------------------------------------- layers ---

def panel_layers(ui):
    ed = ui.editor
    imgui.set_next_window_pos(_vp(740, 10), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(420, 320), imgui.Cond_.first_use_ever)
    if imgui.begin('Sky layers (read-only)')[0]:
        path, lines = ed.layers_text()
        if path is None:
            imgui.text('Sky/%s.txt not found' % ed.track.capitalize())
        else:
            imgui.text_colored(COL_TEXT_DIM, path)
            imgui.text_colored(COL_TEXT_DIM, 'layer editing is a separate task (ATM2 §8)')
            imgui.begin_child('##layers', _v2(0, 0))
            for ln in lines:
                imgui.text_unformatted(ln)
            imgui.end_child()
    imgui.end()


# ---------------------------------------------------------------- horizon ---

class HorizonUi:
    """SKY3: four upscaled horizon parts -> preview -> Sky/<Track>.fsh."""

    def __init__(self, track):
        import horizon_import as hi
        self.hi = hi
        self.track = track
        self.targets = hi.targets(track)
        self.tags = list(self.targets)
        self.also = True                  # write to the tracks that share it
        self.dialog = None                # (part index or 'folder', pfd object)
        self.message = ''
        self.thumb = None
        self.select_tag(0)

    @property
    def target(self):
        return self.targets[self.tags[self.tag_i]] if self.tags else None

    def select_tag(self, i):
        self.tag_i = i
        self.imp = None
        t = self.target
        self.folder = t.folder if t else ''
        self.paths = self.hi.find_parts(self.folder, t) if t else []
        self.sharing = self.hi.sharing(t) if t else []

    def set_folder(self, folder):
        self.folder = folder
        found = self.hi.find_parts(folder, self.target)
        self.paths = [f or p for f, p in zip(found, self.paths)]
        self.message = '%d of 4 parts found in %s' % (sum(1 for f in found if f), folder)
        self.imp = None

    def build(self, sky):
        try:
            self.imp = self.hi.Import(self.target, self.paths, self.hi.sky_fsh(self.track))
        except (OSError, ValueError) as e:
            self.imp = None
            self.message = 'import failed: %s' % e
            return
        w, h = self.imp.size
        if sky is not None:
            sky.set_horizon((w, h, self.imp.rgba_bytes()))
            self.make_thumb(sky.ctx)
        self.message = 'preview: %dx%d atlas (x%d), not written yet' % (w, h, self.imp.scale)

    def make_thumb(self, ctx):
        from PIL import Image
        if self.thumb is not None:
            self.thumb.release()
        im = Image.fromarray(self.imp.atlas, 'RGBA').resize((256, 256), Image.LANCZOS)
        self.thumb = ctx.texture((256, 256), 4, im.tobytes())

    def also_list(self):
        return self.sharing if self.also else []

    def write(self, sky):
        try:
            paths = self.hi.write(self.track, self.imp, also=self.also_list())
        except (OSError, ValueError) as e:
            self.message = 'write failed: %s' % e
            return
        if sky is not None:
            sky.reload_horizon()
        w, h = self.imp.size
        self.message = 'written %dx%d: %s' % (w, h, ', '.join(os.path.basename(p) for p in paths))

    def restore(self, sky):
        paths = self.hi.restore(self.track, also=self.also_list())
        if sky is not None:
            sky.reload_horizon()
        self.imp = None
        self.message = ('restored %s' % ', '.join(os.path.basename(p) for p in paths)
                        if paths else 'no backup to restore')

    def pick(self, what):
        from imgui_bundle import portable_file_dialogs as pfd
        if what == 'folder':
            self.dialog = (what, pfd.select_folder('Folder with the 4 horizon parts', self.folder or ''))
        else:
            start = self.paths[what] or self.folder or ''
            self.dialog = (what, pfd.open_file('Horizon part %d' % what, start,
                                               ['Images', '*.png *.tga *.bmp *.jpg *.jpeg *.tif *.tiff *.webp']))

    def tick_dialog(self):
        if self.dialog is None or not self.dialog[1].ready():
            return
        what, dlg = self.dialog
        res = dlg.result()
        self.dialog = None
        if not res:
            return
        if what == 'folder':
            self.set_folder(res)
        else:
            self.paths[what] = res[0]
            self.imp = None


def _image_size(path):
    from PIL import Image
    try:
        with Image.open(path) as im:
            return '%dx%d %s' % (im.size[0], im.size[1], im.mode)
    except OSError:
        return 'unreadable'


def panel_horizon(ui):
    ed = ui.editor
    if ui.horizon is None or ui.horizon.track != ed.track:
        ui.horizon = HorizonUi(ed.track)
    hz = ui.horizon
    hz.tick_dialog()
    imgui.set_next_window_pos(_vp(1170, 10), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(420, 620), imgui.Cond_.first_use_ever)
    if imgui.begin('Horizon import')[0]:
        _horizon_body(ui, hz)
    imgui.end()


def _horizon_body(ui, hz):
    ed = ui.editor
    t = hz.target
    if t is None:
        imgui.text_wrapped('%s has no horizon in <work>/textures/sky/manifest.csv '
                           '(run tools/track/export_sky_textures.py)' % ed.track)
        return
    if len(hz.tags) > 1:
        changed, i = imgui.combo('texture', hz.tag_i, hz.tags)
        if changed:
            hz.select_tag(i)
            t = hz.target
    imgui.text('%s  atlas %dx%d, %d row(s), parts %dx%d'
               % (t.tag, t.atlas[0], t.atlas[1], t.rows, t.rects[0][2], t.rects[0][3]))
    if t.same_as == 'transparent':
        imgui.text_colored(COL_WARN, 'stock horizon is empty: parts need their own alpha')
    elif t.same_as:
        imgui.text_colored(COL_TEXT_DIM, 'same texture as %s' % t.same_as)
    imgui.text_colored(COL_TEXT_DIM, 'any size; the atlas is scaled by the nearest power of two')
    imgui.separator()
    imgui.text_wrapped('folder: %s' % (hz.folder or '-'))
    if imgui.button('Choose folder...'):
        hz.pick('folder')
    imgui.same_line()
    if imgui.button('Rescan'):
        hz.set_folder(hz.folder)
    for i in range(4):
        imgui.push_id(i)
        if imgui.button('...'):
            hz.pick(i)
        imgui.same_line()
        p = hz.paths[i] if i < len(hz.paths) else None
        if p:
            imgui.text('%d: %s  (%s)' % (i, os.path.basename(p), _image_size(p)))
        else:
            imgui.text_colored(COL_WARN, '%d: %s* not found' % (i, t.stems[i]))
        imgui.pop_id()
    imgui.separator()
    imgui.begin_disabled(not all(hz.paths))
    if imgui.button('Preview'):
        hz.build(ui.sky)
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button('Show current'):
        hz.imp = None
        if ui.sky is not None:
            ui.sky.reload_horizon()
        hz.message = 'showing Sky/%s.fsh' % ed.track.capitalize()
    if hz.sharing:
        _, hz.also = imgui.checkbox('also write to %s' % ', '.join('%s/%s' % s for s in hz.sharing),
                                    hz.also)
    imgui.begin_disabled(hz.imp is None)
    if imgui.button('Write Sky/%s.fsh to game' % ed.track.capitalize()):
        hz.write(ui.sky)
    imgui.end_disabled()
    imgui.same_line()
    imgui.begin_disabled(not hz.hi.has_backup(ed.track))
    if imgui.button('Restore original'):
        hz.restore(ui.sky)
    imgui.end_disabled()
    imgui.text_wrapped(hz.message)
    if hz.imp is not None:
        for n in hz.imp.notes:
            imgui.text_colored(COL_WARN if 'NO ALPHA' in n else COL_TEXT_DIM, n)
        if hz.thumb is not None:
            imgui.image(imgui.ImTextureRef(hz.thumb.glo), _v2(256, 256))


def panel_preview(ui):
    pv = ui.preview
    imgui.set_next_window_pos(_vp(740, 340), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(420, 300), imgui.Cond_.first_use_ever)
    if imgui.begin('Sky preview')[0]:
        for label, attr in (('dome wireframe', 'dome_wire'), ('cloud layers', 'layers'), ('sky fog (channel A)', 'sky_fog'),
                            ('horizon', 'horizon'), ('sun', 'sun'),
                            ('flares', 'flares'), ('precipitation', 'precip'), ('lightning', 'storm')):
            _, v = imgui.checkbox(label, getattr(pv, attr))
            setattr(pv, attr, v)
        _, pv.sun_size = imgui.slider_float('sun size', pv.sun_size, 0.01, 0.2, '%.3f')
        _, pv.precip_per_unit = imgui.slider_float('particles per precip unit', pv.precip_per_unit,
                                                   100.0, 30000.0, '%.0f')
        imgui.text_wrapped('Precipitation value 0..1 maps to the game exactly; particle count in game = '
                           'round(value * multiplier), but the game multiplier is unknown. Density here '
                           'is NOT calibrated - preview only.')
        _, pv.storm_mult = imgui.slider_float('lightning frequency x', pv.storm_mult, 0.0, 30.0, '%.1f')
        if imgui.button('Flash now') and ui.sky is not None:
            ui.sky.trigger_flash()
        if ui.status and ui.editor:
            slc = ui.status[0]
            sky = ui.editor.state.sky
            rain, snow = sky.precipitation_at(slc)
            storm = sky.storm_at(slc)
            cls = skygeo.storm_class(storm)
            imgui.separator()
            imgui.text('slice %d: snow %.2f  rain %.2f' % (slc, snow, rain))
            if ui.sky is not None and 'snow_count' in ui.sky.stats:
                imgui.text('particles: snow %d  rain %d' % (ui.sky.stats['snow_count'],
                                                            ui.sky.stats.get('rain_count', 0)))
            imgui.text('storm %.2f: %s' % (storm, ed_mod.storm_label(storm)))
    imgui.end()


def _panel_mode(ui):
    imgui.set_next_window_pos(_vp(10, 10), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(_v2(200, 50), imgui.Cond_.first_use_ever)
    if imgui.begin('Mode', flags=imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.always_auto_resize)[0]:
        if imgui.radio_button('Atmosphere', ui.mode == MODE_ATMOSPHERE):
            ui.mode = MODE_ATMOSPHERE
        imgui.same_line()
        if imgui.radio_button('AI line', ui.mode == MODE_AI):
            ui.mode = MODE_AI
    imgui.end()


def draw_gui(ui, display_w, display_h):
    _panel_mode(ui)
    if ui.mode == MODE_ATMOSPHERE:
        panel_preview(ui)
        panel_file(ui)
        panel_sky(ui)
        panel_keys(ui)
        panel_layers(ui)
        panel_horizon(ui)
        panel_curves(ui, display_w, display_h)
    elif ui.mode == MODE_AI:
        import ai_ui
        if ui.ai is not None:
            ai_ui.draw(ui.ai, display_w, display_h)
