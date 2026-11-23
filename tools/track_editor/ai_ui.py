"""AI6 step 1: the "AI line" mode of the editor (read-only diagnostics).

3D overlay: centre line (optionally coloured by the .aispeed zones/caps),
lane edges, walls (.hsw), racing lines (.lsp forward/reverse), the cars'
real trajectories from a race_test trace (coloured by speed / speed minus the
AI target / pedals / steering / slip) and event posts (walls, car hits,
objects, spins, stalls, respawns).
Panels: AI (data + layers), AI events (click = camera to the spot), AI plots
(speed and lateral offset along the slices; click or drag the cursor line =
camera to that slice).
"""

import os
import time

import numpy as np
from imgui_bundle import imgui, implot

import ai_data
from ai_render import Mesh, colour_ramp, diverging

COLOUR_MODES = ('Speed', 'Speed - AI target', 'Pedals', 'Steering', 'Slip angle', 'Car')
CENTRE_MODES = ('Plain', 'Zone factor (.aispeed)', 'Speed cap (.aispeed)')
CAR_COLOURS = [(1.0, 1.0, 1.0), (1.0, 0.55, 0.1), (0.3, 0.8, 1.0), (0.6, 1.0, 0.3),
               (1.0, 0.4, 0.8), (0.9, 0.9, 0.3), (0.6, 0.5, 1.0), (0.4, 1.0, 0.8)]
EVENT_COLOURS = {'wall': (1.0, 0.15, 0.1), 'car': (1.0, 0.6, 0.0), 'spin': (0.1, 0.9, 1.0),
                 'stall': (1.0, 1.0, 1.0), 'respawn': (0.8, 0.2, 1.0)}
EVENT_OTHER = (1.0, 0.3, 0.7)            # hit3 (objects) and other contact kinds
COL_DIM = imgui.ImVec4(0.6, 0.6, 0.6, 1.0)
COL_WARN = imgui.ImVec4(1.0, 0.75, 0.3, 1.0)


def _v4(c, a=1.0):
    return imgui.ImVec4(float(c[0]), float(c[1]), float(c[2]), a)


def event_colour(kind):
    return EVENT_COLOURS.get(kind, EVENT_OTHER)


class AiView:
    """State of the AI mode between frames."""

    def __init__(self, track_dir, name, camera_slice, focus, centreline=None):
        self.centreline = centreline
        self.track_dir = track_dir
        self.name = name
        self.camera_slice = camera_slice      # callable -> float
        self.focus = focus                    # callable(slice, top_view, height)
        self.track = None
        self.trace = None
        self.finder = None
        self.error = None
        self.trace_idx = -1
        self.path_buf = ''
        self.dirty = True
        self.layers = {'centre': True, 'lanes': False, 'walls': True, 'objects': True,
                       'line0': True, 'line1': False, 'traj': True, 'events': True}
        self.on_top = False
        self.centre_mode = 0
        self.colour_mode = 0
        self.lap = -1
        self.cars = set()
        self.plot_car = 0
        self.width = 0.6
        self.top_view = True
        self.top_height = 120.0
        self.selected_event = -1
        self.event_filter = {'wall': True, 'car': False, 'spin': True, 'stall': True,
                             'respawn': True, 'other': True}
        self.load_seconds = 0.0

    # ------------------------------------------------------------- loading
    def ensure_loaded(self):
        if self.track is not None or self.error:
            return
        t0 = time.perf_counter()
        try:
            self.track = ai_data.AiTrack(self.track_dir, self.name, self.centreline)
        except Exception as e:      # noqa: BLE001
            self.error = 'track: %s' % e
            return
        self.load_seconds = time.perf_counter() - t0
        self.finder = ai_data.TraceFinder(self.name)
        self.dirty = True

    def load_trace(self, path):
        try:
            self.trace = ai_data.Trace(path, self.track)
            self.error = None
        except Exception as e:      # noqa: BLE001
            self.error = 'trace: %s' % e
            return False
        self.cars = set(self.trace.cars)
        self.plot_car = min(self.cars) if self.cars else 0
        if self.plot_car == 0 and 1 in self.cars:
            self.plot_car = 1                 # car 0 is the player on aidrive
        self.lap = -1
        self.selected_event = -1
        # the line the race used: <track>1.lsp for reverse races
        self.layers['line0'] = not self.trace.reverse
        self.layers['line1'] = self.trace.reverse
        self.dirty = True
        return True

    def tick(self):
        """Auto-load the newest trace once the background scan is done."""
        f = self.finder
        if f is not None and f.done and self.trace is None and self.trace_idx < 0 and f.results:
            self.trace_idx = 0
            self.load_trace(f.results[0][1])

    def line_dir(self):
        return 1 if self.trace is not None and self.trace.reverse else 0

    def visible_events(self):
        if self.trace is None:
            return []
        out = []
        for i, e in enumerate(self.trace.events):
            kind = e.get('type')
            key = kind if kind in self.event_filter else 'other'
            if e.get('car') in self.cars and self.event_filter.get(key, True):
                if self.lap >= 0:
                    d = self.trace.cars.get(e.get('car'))
                    if d is not None:
                        j = np.searchsorted(d['tick'], e.get('tick', 0))
                        j = min(j, len(d['lap']) - 1)
                        if d['lap'][j] != self.lap:
                            continue
                out.append((i, e))
        return out

    # ---------------------------------------------------------------- mesh
    def build_mesh(self):
        m = Mesh()
        t = self.track
        if t is None or not t.n:
            return m
        idx = np.arange(t.n)
        if self.layers['centre'] and t.centre is not None:
            cols = np.tile([0.55, 0.55, 0.55, 0.9], (t.n, 1))
            z = t.zones.get(self.line_dir())
            if z is not None and self.centre_mode == 1:
                f = z[0]
                c = colour_ramp(np.clip((f - 1.0) / 0.5, 0, 1) * 0.75 + 0.25)
                c[f <= 1.0001] = (0.55, 0.55, 0.55)
                cols[:, :3] = c
            elif z is not None and self.centre_mode == 2:
                cap = z[1]
                c = colour_ramp(np.clip((cap - 40.0) / 200.0, 0, 1))
                c[cap <= 0] = (0.55, 0.55, 0.55)
                cols[:, :3] = c
            m.ribbon(t.centre, cols, 0.35 if self.centre_mode == 0 else 1.2,
                     breaks=self._ring_breaks(t.n))
        if self.layers['lanes'] and t.has_vroad:
            for sign, k in ((-1, 0), (1, 1)):
                m.ribbon(t.point_at(idx, sign * t.lane[:, k]), (1.0, 1.0, 1.0, 0.5), 0.2)
        for d, col in ((0, (1.0, 0.85, 0.1)), (1, (0.2, 0.9, 1.0))):
            if self.layers['line%d' % d] and d in t.lsp:
                pts = t.lsp[d]
                y = t.height_at(idx)[:len(pts)]
                p3 = np.column_stack([pts[:, 0], y, pts[:, 1]])
                m.ribbon(p3, col, 0.5, lift=0.4, breaks=self._ring_breaks(len(pts)))
        tr = self.trace
        if self.layers['traj'] and tr is not None:
            vmax = max((float(d['kmh'].max()) for d in tr.cars.values() if len(d['kmh'])), default=200.0)
            for car in sorted(self.cars):
                d = tr.cars.get(car)
                if d is None or len(d['tick']) < 2:
                    continue
                mask = ai_data.rows_of_lap(d, self.lap)
                if mask.sum() < 2:
                    continue
                pts = np.column_stack([d['x'], d['y'], d['z']])[mask]
                cols = self._traj_colours(d, mask, vmax, car)
                m.ribbon(pts, cols, self.width, lift=0.6)
        if self.layers['events'] and tr is not None:
            ev = self.visible_events()
            if ev:
                sl = np.array([e.get('slice', 0) for _, e in ev])
                pts = np.column_stack([[e.get('x', 0.0) for _, e in ev], t.height_at(sl),
                                       [e.get('z', 0.0) for _, e in ev]])
                cols = np.array([event_colour(e.get('type')) for _, e in ev])
                m.posts(pts, cols)
                sel = [k for k, (i, _) in enumerate(ev) if i == self.selected_event]
                if sel:
                    m.posts(pts[sel], np.array([[1.0, 1.0, 0.2]]), height=12.0, width=1.4)
        # translucent panels last: they write depth and would hide what follows
        if self.layers['walls']:
            m.panels(t.walls, (1.0, 0.2, 0.15, 0.35))
        if self.layers['objects']:
            m.panels(t.objects, (1.0, 0.6, 0.1, 0.45))
        return m

    @staticmethod
    def _ring_breaks(n):
        return np.zeros(n, bool)

    def _traj_colours(self, d, mask, vmax, car):
        mode = self.colour_mode
        n = int(mask.sum())
        if mode == 0:
            return colour_ramp((d['kmh'][mask] - 30.0) / max(vmax - 30.0, 1.0))
        if mode == 1:
            # car slower than the planner wants = blue, faster (over the target) = red
            return diverging((d['kmh'][mask] - d['target_kmh'][mask]) / 30.0)
        if mode == 2:
            thr = d['throttle'][mask] / 255.0
            brk = d['brake'][mask] / 255.0
            c = np.column_stack([brk, thr * (1 - brk * 0.5), np.zeros(n)])
            coast = (thr < 0.05) & (brk < 0.05)
            c[coast] = (0.45, 0.45, 0.6)
            return c
        if mode == 3:
            s = np.abs(d['steer'][mask]) / 126.0
            c = colour_ramp(0.25 + 0.75 * s)
            c[s >= 0.95] = (1.0, 0.0, 1.0)           # full lock
            return c
        if mode == 4:
            return colour_ramp(np.clip(d['slip'][mask] / 60.0, 0, 1))
        return np.tile(CAR_COLOURS[car % len(CAR_COLOURS)], (n, 1))


# ------------------------------------------------------------------ panels ---

def _checkbox(view, label, key, holder=None):
    holder = view.layers if holder is None else holder
    ch, v = imgui.checkbox(label, holder[key])
    if ch:
        holder[key] = v
        view.dirty = True


def panel_main(view):
    imgui.set_next_window_pos(_vp(10, 60), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(imgui.ImVec2(430, 560), imgui.Cond_.first_use_ever)
    visible = imgui.begin('AI')[0]
    try:
        if visible:
            t = view.track
            if view.error:
                imgui.text_colored(COL_WARN, view.error)
            if t is None:
                imgui.text('loading...')
                return
            imgui.text('%s: %d slices, %d wall + %d object edges' % (view.name, t.n, len(t.walls), len(t.objects)))
            imgui.text_colored(COL_DIM, 'vroad: %s | lines %s | zones %s' % (
                'tr.frd' if t.has_vroad else 'none (stock track)',
                '/'.join(str(d) for d in sorted(t.lsp)) or '-',
                '/'.join(('fwd', 'rev')[d] for d in sorted(t.zones)) or '-'))
            for e in t.errors:
                imgui.text_colored(COL_WARN, e)

            imgui.separator_text('Layers')
            _checkbox(view, 'Centre', 'centre')
            imgui.same_line()
            _checkbox(view, 'Lane edges', 'lanes')
            imgui.same_line()
            _checkbox(view, 'Walls', 'walls')
            imgui.same_line()
            _checkbox(view, 'Objects', 'objects')
            _checkbox(view, 'Line fwd (0.lsp)', 'line0')
            imgui.same_line()
            _checkbox(view, 'Line rev (1.lsp)', 'line1')
            _checkbox(view, 'Trajectories', 'traj')
            imgui.same_line()
            _checkbox(view, 'Events', 'events')
            imgui.same_line()
            ch, v = imgui.checkbox('On top', view.on_top)
            if ch:
                view.on_top = v
            imgui.set_next_item_width(200)
            ch, v = imgui.combo('Centre colour', view.centre_mode, list(CENTRE_MODES))
            if ch:
                view.centre_mode, view.dirty = v, True

            imgui.separator_text('Race trace')
            f = view.finder
            if f is not None and not f.done:
                imgui.text_colored(COL_DIM, 'scanning reports/race_test + line_record ...')
            elif f is not None:
                labels = ['%s  %s' % (time.strftime('%m-%d %H:%M', time.localtime(m)),
                                      _short(p)) for m, p in f.results[:200]]
                imgui.set_next_item_width(-60)
                ch, v = imgui.combo('##trace', max(view.trace_idx, 0), labels or ['(no traces)'])
                if ch and labels:
                    view.trace_idx = v
                    view.load_trace(f.results[v][1])
                imgui.same_line()
                if imgui.button('Rescan'):
                    view.finder = ai_data.TraceFinder(view.name)
                    view.trace_idx = -1 if view.trace is None else view.trace_idx
            imgui.set_next_item_width(-60)
            _ch, view.path_buf = imgui.input_text('##path', view.path_buf)
            imgui.same_line()
            if imgui.button('Load') and view.path_buf.strip():
                p = view.path_buf.strip().strip('"')
                if os.path.isdir(p):
                    p = os.path.join(p, 'dx11z_trace.csv')
                view.load_trace(p)

            tr = view.trace
            if tr is not None:
                imgui.text_colored(COL_DIM, '%s | %s | laps %d | %s' % (
                    tr.track_name, 'reverse' if tr.reverse else 'forward', tr.max_lap,
                    ' '.join(a for a in tr.summary.get('args', []) if a.startswith(('num_laps', 'rand_seed')))))
                if tr.track_name and tr.track_name.lower() != view.name.lower():
                    imgui.text_colored(COL_WARN, 'trace is from another track: %s' % tr.track_name)
                imgui.set_next_item_width(200)
                ch, v = imgui.combo('Colour by', view.colour_mode, list(COLOUR_MODES))
                if ch:
                    view.colour_mode, view.dirty = v, True
                imgui.set_next_item_width(200)
                laps = ['All laps'] + ['Lap %d%s' % (k, ' (grid -> line)' if k == 0 else '')
                                       for k in range(tr.max_lap + 1)]
                ch, v = imgui.combo('Lap', view.lap + 1, laps)
                if ch:
                    view.lap, view.dirty = v - 1, True
                imgui.set_next_item_width(200)
                ch, v = imgui.slider_float('Ribbon width', view.width, 0.2, 3.0, '%.1f m')
                if ch:
                    view.width, view.dirty = v, True
                _legend(view.colour_mode)

                imgui.separator_text('Cars (radio = plotted car)')
                for car in sorted(tr.cars):
                    on = car in view.cars
                    ch, v = imgui.checkbox('##car%d' % car, on)
                    if ch:
                        (view.cars.add if v else view.cars.discard)(car)
                        view.dirty = True
                    imgui.same_line()
                    if imgui.radio_button('##plot%d' % car, view.plot_car == car):
                        view.plot_car = car
                    imgui.same_line()
                    imgui.text_colored(_v4(CAR_COLOURS[car % len(CAR_COLOURS)]), _car_line(tr, car))
    finally:
        imgui.end()


def _short(path):
    rel = path
    if path.startswith(ai_data.REPORTS):
        rel = os.path.relpath(path, ai_data.REPORTS)
    elif path.startswith(ai_data.RECORDS):
        rel = 'driven: ' + os.path.relpath(path, ai_data.RECORDS)
    return rel.replace('\\', '/').replace('/dx11z_trace.csv', '')


def _car_line(tr, car):
    s = tr.stats.get(car, {})
    ev = s.get('events', {})
    laps = s.get('lap_times_s') or []
    name = ('player' if tr.recorded else 'player(ai)') if car == 0 else 'bot %d' % car
    return '%-10s laps %s | avg %d km/h | wall %d obj %d car %d spin %d stall %d' % (
        name, ' '.join('%.1f' % x for x in laps) or '-', round(s.get('avg_kmh', s.get('avg_kmh_trace', 0))),
        ev.get('wall', 0), sum(v for k, v in ev.items() if k.startswith('hit')), ev.get('car', 0),
        ev.get('spin', 0), ev.get('stall', 0))


def _legend(mode):
    text = {0: 'blue slow -> red fast',
            1: 'blue = slower than the AI target, white = on target, red = faster (must brake)',
            2: 'green throttle, red brake, yellow both, grey coasting',
            3: 'blue straight -> red lock, magenta = full lock',
            4: 'green 0 -> red 60+ deg slip',
            5: 'one colour per car'}[mode]
    imgui.text_colored(COL_DIM, text)


def panel_events(view):
    imgui.set_next_window_pos(_vp(450, 60), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(imgui.ImVec2(420, 380), imgui.Cond_.first_use_ever)
    visible = imgui.begin('AI events')[0]
    try:
        if visible:
            if view.trace is None:
                imgui.text('no trace loaded')
                return
            for k in ('wall', 'other', 'car', 'spin', 'stall', 'respawn'):
                col = EVENT_OTHER if k == 'other' else EVENT_COLOURS[k]
                imgui.push_style_color(imgui.Col_.text, _v4(col))
                _checkbox(view, 'objects' if k == 'other' else k, k, view.event_filter)
                imgui.pop_style_color()
                imgui.same_line()
            imgui.new_line()
            ch, v = imgui.checkbox('Top view', view.top_view)
            if ch:
                view.top_view = v
            imgui.same_line()
            imgui.set_next_item_width(140)
            _ch, view.top_height = imgui.slider_float('height', view.top_height, 20.0, 600.0, '%.0f m')
            ev = view.visible_events()
            imgui.text_colored(COL_DIM, '%d events - click = camera there' % len(ev))
            flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v |
                     imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable)
            if imgui.begin_table('ev', 6, flags):
                imgui.table_setup_scroll_freeze(0, 1)
                for h in ('time', 'car', 'kind', 'slice', 'km/h', 'detail'):
                    imgui.table_setup_column(h)
                imgui.table_headers_row()
                for i, e in ev:
                    imgui.table_next_row()
                    imgui.table_next_column()
                    label = '%7.1f##e%d' % ((e.get('tick', 0) - (view.trace.summary.get('trace', {})
                                             .get('start_tick') or 0)) / ai_data.TICKS, i)
                    if imgui.selectable(label, view.selected_event == i,
                                        imgui.SelectableFlags_.span_all_columns)[0]:
                        view.selected_event = i
                        view.dirty = True
                        view.focus(float(e.get('slice', 0)), view.top_view, view.top_height,
                                   (e.get('x'), e.get('z')), view.line_dir())
                    imgui.table_next_column()
                    imgui.text(str(e.get('car')))
                    imgui.table_next_column()
                    imgui.text_colored(_v4(event_colour(e.get('type'))), str(e.get('type')))
                    imgui.table_next_column()
                    imgui.text(str(e.get('slice')))
                    imgui.table_next_column()
                    imgui.text(str(e.get('kmh', '')))
                    imgui.table_next_column()
                    det = []
                    if 'seconds' in e:
                        det.append('%.1f s' % e['seconds'])
                    if 'impact' in e:
                        det.append('impact %.1f' % e['impact'])
                    if 'contacts' in e and e['contacts'] > 1:
                        det.append('%d contacts' % e['contacts'])
                    imgui.text(', '.join(det))
                imgui.end_table()
    finally:
        imgui.end()


def _segments(x, y):
    """Split at slice wraps (a lap crossing the line) so a line plot does not
    draw a horizontal stroke across the whole track."""
    if len(x) < 2:
        return []
    cut = np.nonzero(np.abs(np.diff(x)) > 50)[0] + 1
    out = []
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(x)]):
        if b - a >= 2:
            out.append((np.ascontiguousarray(x[a:b], np.float64), np.ascontiguousarray(y[a:b], np.float64)))
    return out


def _plot(label, x, y, col, weight=1.5):
    spec = implot.Spec(line_color=_v4(col), line_weight=weight)
    for xs, ys in _segments(np.asarray(x, np.float64), np.asarray(y, np.float64)):
        ok = np.isfinite(ys)
        if ok.all():
            implot.plot_line(label, xs, ys, spec)
        else:
            for xs2, ys2 in _runs(xs, ys, ok):
                implot.plot_line(label, xs2, ys2, spec)


def _runs(xs, ys, ok):
    out, start = [], None
    for i, v in enumerate(list(ok) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= 2:
                out.append((np.ascontiguousarray(xs[start:i]), np.ascontiguousarray(ys[start:i])))
            start = None
    return out


def panel_plots(view, display_w, display_h):
    h = 360
    imgui.set_next_window_pos(_vp(10, max(10, display_h - h - 10)), imgui.Cond_.first_use_ever)
    imgui.set_next_window_size(imgui.ImVec2(display_w - 20, h), imgui.Cond_.first_use_ever)
    visible = imgui.begin('AI plots')[0]
    try:
        if visible:
            t, tr = view.track, view.trace
            if t is None:
                return
            cam = float(view.camera_slice())
            avail = imgui.get_content_region_avail()
            ph = max(80.0, (avail.y - 8) / 2)
            n = t.n
            car = view.plot_car
            d = tr.cars.get(car) if tr is not None else None
            mask = ai_data.rows_of_lap(d, view.lap) if d is not None else None
            x_axis = implot.ImAxis_.x1
            if getattr(view, '_xlink', None) is None or view._xlink_n != n:
                view._xlink = (implot.BoxedValue(0.0), implot.BoxedValue(float(n)))
                view._xlink_n = n
            for which in ('speed', 'lateral'):
                if implot.begin_plot('##%s' % which, imgui.ImVec2(-1, ph), implot.Flags_.no_title):
                    try:
                        implot.setup_axes('slice' if which == 'lateral' else '', 'km/h' if which == 'speed' else 'm (+ = right)')
                        implot.setup_axis_links(x_axis, view._xlink[0], view._xlink[1])
                        if which == 'speed':
                            implot.setup_axis_limits(implot.ImAxis_.y1, 0, 280, implot.Cond_.once)
                        else:
                            implot.setup_axis_limits(implot.ImAxis_.y1, -25, 25, implot.Cond_.once)
                        if which == 'speed':
                            _speed_plot(view, t, tr, car, d, mask)
                        else:
                            _lateral_plot(view, t, tr, car, d, mask)
                        ch, new, _c, _h, _held = implot.drag_line_x(1 if which == 'speed' else 2, cam,
                                                                    imgui.ImVec4(1, 1, 0.3, 1), 1.5)
                        if ch:
                            view.focus(new, False, view.top_height, None, view.line_dir())
                        elif implot.is_plot_hovered() and _clicked_still():
                            pos = implot.get_plot_mouse_pos()
                            view.focus(float(pos.x), view.top_view and imgui.get_io().key_shift,
                                       view.top_height, None, view.line_dir())
                    finally:
                        implot.end_plot()
            imgui.text_colored(COL_DIM, 'click a plot = camera to that slice (Shift = top view), drag the yellow line to scrub;'
                               ' wheel = zoom, right drag = box zoom, double click = fit')
    finally:
        imgui.end()


def _clicked_still():
    """Left button released without dragging (a drag pans the plot)."""
    if not imgui.is_mouse_released(0):
        return False
    try:
        return imgui.get_io().mouse_drag_max_distance_sqr[0] < 16.0
    except (AttributeError, IndexError, TypeError):
        return True


def _speed_plot(view, t, tr, car, d, mask):
    z = t.zones.get(view.line_dir())
    if z is not None:
        cap = np.where(z[1] > 0, z[1], np.nan)
        _plot('cap .aispeed', np.arange(t.n), cap, (0.8, 0.4, 1.0), 2.0)
        _plot('zone factor x100', np.arange(t.n), np.where(z[0] != 1.0, z[0] * 100.0, np.nan),
              (0.6, 0.6, 0.6), 1.0)
    if tr is None:
        return
    for c in sorted(view.cars):
        dd = tr.cars.get(c)
        if dd is None or c == car:
            continue
        m = ai_data.rows_of_lap(dd, view.lap)
        _plot('car %d' % c, dd['slice'][m], dd['kmh'][m], np.array(CAR_COLOURS[c % 8]) * 0.55, 1.0)
    if d is not None:
        _plot('AI target car %d' % car, d['slice'][mask], d['target_kmh'][mask], (1.0, 0.3, 0.3), 1.0)
        _plot('speed car %d' % car, d['slice'][mask], d['kmh'][mask], CAR_COLOURS[car % 8], 2.0)
        brk = d['brake'][mask] > 0
        if brk.any():
            spec = implot.Spec(marker=implot.Marker_.square, marker_size=1.5,
                               marker_fill_color=imgui.ImVec4(1, 0.2, 0.2, 1),
                               marker_line_color=imgui.ImVec4(1, 0.2, 0.2, 1))
            implot.plot_scatter('brake car %d' % car, np.ascontiguousarray(d['slice'][mask][brk], np.float64),
                                np.full(int(brk.sum()), 3.0), spec)
    _event_markers(view, 'speed')


def _lateral_plot(view, t, tr, car, d, mask):
    x = np.arange(t.n, dtype=np.float64)
    _plot('wall left', x, t.wall_left, (1.0, 0.25, 0.2), 1.5)
    _plot('wall right', x, t.wall_right, (1.0, 0.25, 0.2), 1.5)
    if t.has_vroad and view.layers['lanes']:
        _plot('lane', x, -t.lane[:, 0], (0.8, 0.8, 0.8), 1.0)
        _plot('lane', x, t.lane[:, 1], (0.8, 0.8, 0.8), 1.0)
    for dd, col in ((0, (1.0, 0.85, 0.1)), (1, (0.2, 0.9, 1.0))):
        lat = t.lat_lsp.get(dd)
        if lat is not None and (view.layers['line%d' % dd] or dd == view.line_dir()):
            _plot('line %s' % ('fwd' if dd == 0 else 'rev'), x, lat, col, 2.0)
    if d is not None and 'lateral' in d:
        _plot('car %d' % car, d['slice'][mask], d['lateral'][mask], CAR_COLOURS[car % 8], 1.5)
    _event_markers(view, 'lateral')


def _event_markers(view, which):
    if view.trace is None or not view.layers['events']:
        return
    by_kind = {}
    for _i, e in view.visible_events():
        if which == 'speed':
            y = e.get('kmh')
            if y is None:
                d = view.trace.cars.get(e.get('car'))
                y = 0.0 if d is None else float(np.interp(e.get('tick', 0), d['tick'], d['kmh']))
        else:
            y = float(view.track.lateral_at(np.array([e.get('slice', 0)]),
                                            np.array([[e.get('x', 0.0), e.get('z', 0.0)]]))[0])
        by_kind.setdefault(e.get('type'), []).append((float(e.get('slice', 0)), float(y)))
    for kind, pts in by_kind.items():
        a = np.array(pts, np.float64)
        col = _v4(event_colour(kind))
        spec = implot.Spec(marker=implot.Marker_.diamond, marker_size=5, marker_fill_color=col,
                           marker_line_color=col)
        # column views must be copied: the binding wants owning 1D arrays
        implot.plot_scatter(kind, a[:, 0].copy(), a[:, 1].copy(), spec)


def _vp(x, y):
    o = imgui.get_main_viewport().pos
    return imgui.ImVec2(o.x + x, o.y + y)


def draw(view, display_w, display_h):
    view.ensure_loaded()
    view.tick()
    panel_main(view)
    panel_events(view)
    panel_plots(view, display_w, display_h)
