"""Every options-menu and Maximize route on the real supervisor stack (ViewControl, UnifiedPointer, OptionsMenu,
options_rows) at Cody's real desktop sizes, driven the way the compositor drives it: a sampled cursor, the bound left
button, bound keys and the wheel helper. Written from the live session of 2026-11-06 (Pro 2): `row_count 0` while open,
"very buggy, almost unusable".

Offline only: a fake clock and a fake compositor client. No renderer process, GPU, compositor or glasses.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests' / 'native_spike')]
from tools.native_spike import free_pointer as fp, geometry, options_geometry as og, options_menu as om, orbit
from tools.native_spike import keybinds, unified_cursor as uc, view, xr_control
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore
from test_free_pointer import Clock, MemoryPlacements

SPACING = 20


def layout(sizes, w=1920, h=1200, s=SPACING):
    """spike/src/view.cpp overview_layout: one row at a shared height."""
    aspects = sum(a / b for a, b in sizes)
    height = min(h, max(0, w - (len(sizes) + 1) * s) / aspects)
    x, out = (w - (height * aspects + (len(sizes) - 1) * s)) / 2, []
    for a, b in sizes:
        out.append((x, (h - height) / 2, height * a / b, height))
        x += height * a / b + s
    return out


def canvas(sizes, w=1920, h=1200):
    d, a = math.tan(math.radians(23)), w / h  # the renderer's fallback FOV (wayland.cpp update_fov)
    return {'w': w, 'h': h, 'half': (d * a / math.sqrt(1 + a * a), d / math.sqrt(1 + a * a)), 'tiles': layout(sizes, w, h)}


FLAT = dict(x=0, y=0, distance=100, curvature=0)
# The session's desktops (5120x1440@1.25 and 2560x1440@2, journal 2026-11-06), then the saved profiles and harder cases.
LAYOUTS = {
    'pro2-flat': (((5120, 1440, 1.25), (2560, 1440, 2.0)), [dict(FLAT), dict(FLAT)], [0, 0]),
    'pro2-moved': (((5120, 1440, 1.25), (2560, 1440, 2.0)),
                   [dict(x=93, y=-15, distance=46, curvature=0), dict(x=-104, y=216, distance=67, curvature=0)], [16, 7]),
    'luma-saved': (((2560, 1600, 1.25), (1920, 1200, 1.25), (1920, 1200, 1.25)),
                   [dict(x=-67, y=44, distance=36, curvature=0), dict(x=160, y=16, distance=45, curvature=0),
                    dict(x=-167, y=-152, distance=100, curvature=0)], [-47, -165, -183]),
    'beast-saved': (((1920, 1080, 1.0),), [dict(x=-128, y=50, distance=99, curvature=0)], [146]),
    'curved-rolled': (((5120, 1440, 1.25), (2560, 1440, 2.0)),
                      [dict(x=30, y=-40, distance=140, curvature=70), dict(x=-30, y=60, distance=80, curvature=120)], [900, -1200]),
}


class Hypr:
    """The compositor's input side as bind_check and keybinds.py describe it: the run's left-button binds live in the
    default submap, so entering the key panel's submap leaves them inactive."""
    def __init__(self): self.submap, self.options, self.calls = False, False, []
    def open_key_panel(self, *args): self.submap = True; self.calls.append('open_key_panel')
    def close_key_panel(self): self.submap = False; self.calls.append('close_key_panel')
    def owned(self, on): self.options = on; self.calls.append(on)


class Rig:
    def __init__(self, name='pro2-flat'):
        sizes, placements, rolls = LAYOUTS[name]
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.names = [f'XR-{i + 1}' for i in range(len(sizes))]
        state = view.ViewState(self.names)
        state.placements = [dict(p) for p in placements]
        self.c = view.ViewControl(state, 'run', 'session', lambda *_: None, LayoutStore(Path(self.tmp.name) / 'l.json'), None,
                                  placements=MemoryPlacements(), clock=self.clock)
        self.p = self.c.overview_pointer = uc.UnifiedPointer(self.c, clock=self.clock)
        self.g = canvas([(w, h) for w, h, _ in sizes])
        self.c.set_overview_geometry(self.g)
        self.p.rolls[:len(rolls)] = rolls
        x, self.outputs = 2560, []
        for i, (w, h, s) in enumerate(sizes):
            self.outputs.append({'index': i, 'x': x, 'y': 0, 'width': w / s, 'height': h / s})
            x += w / s
        self.hypr = Hypr()
        self.c.options_binds = self.hypr.owned
        self.wire = bytearray()
        self.applied = []
        def apply(control, row_id, value, monitor, scope):
            self.applied.append((row_id, value, monitor, scope))
            return None
        self.record = apply
        self.cursor = None
        self.sample(self.outputs[0]['x'] + 200, 300)
        self.sample(self.outputs[0]['x'] + 200, 0)
        assert self.p.mode == 'free', self.p.mode

    def close(self):
        self.c.join_focus(2); self.c.stop_menu_binds(); self.p.close(); self.tmp.cleanup()

    def settle(self):
        for t in list(self.c._menu_threads): t.join(2)

    def take(self, raw):
        if raw: self.wire.extend(raw)
        return raw

    def sample(self, x, y, advance=0.004):
        self.clock.now += advance
        self.cursor = (x, y)
        self.p.feed((x, y), (0.0, 0.0), self.outputs, self.p.want[0])
        self.take(self.c.poll())

    def tile(self, i): return self.g['tiles'][i]
    def placement(self, i): return self.p._rolled()[i]

    def aim_glass(self, i, gx, gy):
        """The dot onto glass point (gx, gy) of desktop i, by hand motion from where it is."""
        return self.aim_point(fp.project_glass(self.g, self.tile(i), self.placement(i), gx, gy))

    def aim_tile(self, i, x, y):
        return self.aim_point(fp.project(self.g, self.tile(i), self.placement(i), x, y))

    def aim_point(self, target):
        self.clock.now += 0.004
        self.p.feed(self.cursor, ((target[0] - self.p.pointer[0]) / self.p.gain, (target[1] - self.p.pointer[1]) / self.p.gain),
                    self.outputs, self.p.want[0])
        self.take(self.c.poll())
        return self.p.hover

    def button(self, op):
        message = dict(schema=1, run_id='run', session='session', request_id=1, op=op, payload={})
        assert not self.hypr.submap, 'the left-button binds are off in the key panel submap: the click never reaches the run'
        self.take(self.c.on_message(decode_control((json.dumps(message) + '\n').encode())))
        self.settle()

    def click(self):
        self.button('resize_press'); self.button('resize_release')

    def panel_xy(self, i, x, y):
        r = og.panel_rect(self.tile(i), fp.pill_rect(self.tile(i)))
        return self.aim_glass(i, r[0] + x, r[1] + y)

    def open(self, i=0):
        gear = og.gear_rect(fp.pill_rect(self.tile(i)))
        hover = self.aim_glass(i, gear[0] + gear[2] / 2, gear[1] + gear[3] / 2)
        assert hover == ('options_gear', i), hover
        self.click()
        assert self.c.options.snapshot()['open']

    def key(self, code, mods=0):
        message = dict(schema=1, run_id='run', session='session', request_id=1, op='binds_key', payload=dict(mods=mods, code=code))
        self.take(self.c.on_message(decode_control((json.dumps(message) + '\n').encode())))
        self.settle()

    def wheel(self, delta):
        message = dict(schema=1, run_id='run', session='session', request_id=1, op='options_scroll', payload=dict(delta=delta))
        self.take(self.c.on_message(decode_control((json.dumps(message) + '\n').encode())))

    def snap(self): return self.c.options.snapshot()

    def tab(self, category):
        """The section by mouse: open its dropdown, then the item as drawn."""
        i = self.index()
        x, y, w, h = og.section_box()
        self.panel_xy(i, x + w / 2, y + h / 2); self.click()
        assert self.snap()['focus'] == og.FOCUS_LIST
        x, y, w, h = og.section_item(om.CATEGORIES.index(category))
        self.panel_xy(self.index(), x + w / 2, y + h / 2); self.click()
        d = self.snap()
        assert d['category'] == category and d['focus'] == og.FOCUS_SECTION

    def index(self, d=None): return self.names.index((d or self.snap())['monitor'])


@pytest.fixture(params=sorted(LAYOUTS))
def rig(request):
    r = Rig(request.param)
    yield r
    r.close()


@pytest.fixture
def pro2():
    r = Rig('pro2-flat')
    yield r
    r.close()


def test_the_session_route_privacy_is_not_an_empty_menu(pro2):
    """Journal 2026-11-06: options_menu category privacy, scope monitor, row_count 0 while open."""
    pro2.open()
    pro2.tab('privacy')
    d = pro2.snap()
    assert d['rows'], (d['scope'], d['category'])
    assert any(r['id'] == 'privacy_display' for r in d['rows'])


def test_no_tab_is_ever_blank_from_either_scope(rig):
    rig.open()
    for start in om.SCOPES:
        for category in om.CATEGORIES:
            rig.c.options_intent('options_category', category=category)
            rig.c.options_intent('options_scope', scope=start)
            d = rig.snap()
            assert d['rows'], (start, category, d['scope'])
            assert all(r['scope'] == d['scope'] for r in d['rows'])


def test_scope_choice_survives_a_tab_that_has_only_the_other_scope(pro2):
    pro2.open()
    pro2.c.options_intent('options_category', category='3d')
    pro2.c.options_intent('options_scope', scope='workspace')
    pro2.c.options_intent('options_category', category='placement')   # monitor only
    assert pro2.snap()['scope'] == 'monitor' and pro2.snap()['rows']
    pro2.c.options_intent('options_category', category='3d')          # both: the choice comes back
    assert pro2.snap()['scope'] == 'workspace'


def test_a_scope_press_on_a_one_scope_tab_says_why_nothing_changed(pro2):
    pro2.open()
    pro2.tab('placement')
    x, y, w, h = og.scope_box()
    pro2.panel_xy(0, x + w * .75, y + h / 2); pro2.click()
    d = pro2.snap()
    assert d['scope'] == 'monitor' and d['rows'] and d['message']


def test_opening_the_menu_leaves_the_mouse_working(pro2):
    pro2.open()
    assert 'open_key_panel' not in pro2.hypr.calls
    pro2.tab('layout')                 # Pro 2 session route: every step below is a real click after the open
    x, y, w, h = og.close_box()
    pro2.panel_xy(0, x + w / 2, y + h / 2); pro2.click()
    assert not pro2.snap()['open']


def test_the_keyboard_is_owned_while_the_menu_has_focus_and_released_after(pro2):
    pro2.open(); pro2.settle()
    assert pro2.hypr.options is True
    pro2.key(9)                        # Escape
    assert not pro2.snap()['open']
    assert pro2.hypr.options is False


def test_a_press_on_a_window_gives_the_keyboard_back(pro2):
    """desktop click passes through and restores typing (contract B1+C1)."""
    pro2.open(); pro2.settle()
    pro2.sample(pro2.outputs[0]['x'] + 400, 300)                       # free dot still: comes in over the desktop
    pro2.aim_tile(0, *fp_center(pro2.tile(0)))
    assert pro2.p.mode == 'desktop'
    pro2.button('resize_press'); pro2.button('resize_release')
    assert not pro2.c.options.keyboard_owned
    assert pro2.hypr.options is False
    assert pro2.snap()['open']


def fp_center(t): return t[0] + t[2] / 2, t[1] + t[3] / 2


def test_a_click_on_empty_space_closes_the_menu(pro2):
    pro2.open()
    pro2.aim_point((60, 1100))
    assert pro2.p.hover is None
    pro2.click()
    assert not pro2.snap()['open']


def test_a_click_on_another_handle_closes_and_still_does_its_job(pro2):
    pro2.open()
    b = fp.level_bar_rect(pro2.tile(1))
    assert pro2.aim_tile(1, b[0] + b[2] / 2, b[1] + b[3] / 2)[0] == 'roll'
    pro2.click()
    assert not pro2.snap()['open']


def test_opening_the_placement_panel_closes_the_options_menu(pro2):
    pro2.open()
    pill = fp.pill_rect(pro2.tile(1))
    assert pro2.aim_glass(1, pill[0] + pill[2] / 2, pill[1] + pill[3] / 2)[0] == 'pill'
    pro2.click()
    assert pro2.c.state.menu_open
    assert not pro2.snap()['open']


def test_the_key_for_the_placement_panel_closes_the_options_menu(pro2):
    pro2.open()
    pro2.take(pro2.c.on_message({'op': 'menu_toggle', 'payload': {}}))
    assert pro2.c.state.menu_open and not pro2.snap()['open']


def test_reading_with_the_dot_on_the_menu_does_not_time_it_out(pro2):
    pro2.open()
    for second in range(45):
        pro2.panel_xy(0, 100 + second % 7, 200)
        pro2.clock.now += 1
        pro2.take(pro2.c.poll())
    assert pro2.snap()['open']


def test_a_menu_left_alone_still_closes_at_30_seconds(pro2):
    pro2.open(); pro2.settle()
    pro2.clock.now += 31
    pro2.take(pro2.c.poll()); pro2.settle()
    assert not pro2.snap()['open']
    assert pro2.hypr.options is False


@pytest.mark.parametrize('op', ('zoom_in', 'mode_next'))
def test_maximizing_with_the_menu_open_leaves_no_invisible_menu(pro2, op):
    pro2.open(); pro2.settle()
    if op == 'mode_next': pro2.take(pro2.c.on_message({'op': 'zoom_in', 'payload': {}}))
    else: pro2.take(pro2.c.on_message({'op': op, 'payload': {}}))
    pro2.take(pro2.c.poll()); pro2.settle()
    assert pro2.c.state.zoomed
    assert not pro2.snap()['open']
    assert pro2.hypr.options is False


def test_the_maximize_button_with_the_menu_open_on_the_other_desktop(pro2):
    pro2.open(0)
    r = fp.maximize_rect(pro2.tile(1))
    assert pro2.aim_tile(1, r[0] + r[2] / 2, r[1] + r[3] / 2) == ('maximize', 1)
    pro2.click(); pro2.take(pro2.c.poll()); pro2.settle()
    assert pro2.c.state.zoomed and pro2.c.state.selected == 1
    assert not pro2.snap()['open']


def test_a_press_on_maximize_dragged_off_it_does_nothing(pro2):
    r = fp.maximize_rect(pro2.tile(0))
    assert pro2.aim_tile(0, r[0] + r[2] / 2, r[1] + r[3] / 2) == ('maximize', 0)
    pro2.button('resize_press')
    pro2.aim_point((60, 1100))
    pro2.button('resize_release')
    assert not pro2.c.state.zoomed


def test_maximize_hit_on_every_layout(rig):
    for i in range(len(rig.names)):
        r = fp.maximize_rect(rig.tile(i))
        for fx, fy in ((.5, .5), (.05, .5), (.95, .5), (.5, .05), (.5, .95)):
            if i: rig.c.state.selected = 0
            hover = rig.aim_tile(i, r[0] + fx * r[2], r[1] + fy * r[3])
            assert hover == ('maximize', i) or (hover is not None and hover[0] == 'desktop' and hover[1] != i), (i, fx, fy, hover)


def rows_reachable(r, category):
    r.tab(category)
    return r.snap()


@pytest.mark.parametrize('category', om.CATEGORIES)
def test_every_row_answers_a_click_at_its_drawn_place(rig, category):
    rig.open()
    rig.c.options._apply = rig.record
    rig.tab(category)
    d = rig.snap()
    assert d['rows']
    i = rig.index(d)
    for index, row in enumerate(d['rows']):
        d = rig.snap()
        y = og.BODY_TOP + index * og.ROW_H + og.ROW_H / 2 - d['scroll']
        if y < og.BODY_TOP or y >= og.BODY_BOTTOM:
            rig.panel_xy(i, 40, 200)
            rig.wheel(og.ROW_H * (index + 1) - og.BODY_H - d['scroll'])
            d = rig.snap()
            y = og.BODY_TOP + index * og.ROW_H + og.ROW_H / 2 - d['scroll']
        assert og.BODY_TOP <= y < og.BODY_BOTTOM
        x = (og.TRACK_X0 + og.TRACK_X1) / 2 if row['kind'] == 'range' else 40
        hover = rig.panel_xy(i, x, y)
        assert hover and hover[0] == 'options_panel', (row['id'], hover)
        rig.applied.clear()
        rig.click()
        assert rig.snap()['focus'] == (0 if row['id'] in ('layout_reset', 'stop') else index), row['id']
        if row['kind'] == 'choice' and row['enabled'] and row.get('choices'):   # the click only opened its list: the pick applies
            snap = rig.snap()
            assert not rig.applied and snap['list'] >= 0, row['id']
            n = len(row['choices'])
            x, y, w, h = og.choice_item(og.row_top(index, snap['scroll']), n, (snap['list'] + 1) % n)
            rig.panel_xy(i, x + w / 2, y + h / 2); rig.click()
            assert rig.snap()['list'] == -1, row['id']
        if row['id'] in ('layout_reset', 'stop'):           # asks first: the page turns into its two answers
            assert [r['id'] for r in rig.snap()['rows']] == ['confirmation.apply', 'confirmation.cancel'] and not rig.applied
            rig.panel_xy(i, 40, og.BODY_TOP + og.ROW_H + og.ROW_H / 2 - rig.snap()['scroll']); rig.click()
            assert any(r['id'] == row['id'] for r in rig.snap()['rows'])
        elif row['enabled'] and row['kind'] != 'readonly' and not (row['kind'] == 'choice' and not row.get('choices')):
            assert [a[0] for a in rig.applied] == [row['id']], row['id']
        rig.c.options.pending = None
        rig.c.options.data.update(status='idle', message='')


def test_the_wheel_scrolls_the_long_page_end_to_end(pro2):
    pro2.open()
    pro2.tab('video')
    assert len(pro2.snap()['rows']) * og.ROW_H > og.BODY_H
    pro2.panel_xy(0, 300, 200)
    pro2.wheel(og.ROW_H)
    assert pro2.snap()['scroll'] == og.ROW_H
    pro2.wheel(4096)
    assert pro2.snap()['scroll'] == len(pro2.snap()['rows']) * og.ROW_H - og.BODY_H


def test_the_wheel_off_the_menu_scrolls_nothing(pro2):
    pro2.open()
    pro2.tab('video')
    pro2.aim_point((60, 1100))
    pro2.wheel(og.ROW_H)
    assert pro2.snap()['scroll'] == 0


def test_a_shorter_page_never_leaves_the_rows_scrolled_away(pro2):
    pro2.open()
    pro2.tab('video')
    pro2.panel_xy(0, 300, 200); pro2.wheel(4096)
    assert pro2.snap()['scroll'] > 0
    rows = pro2.snap()['rows']
    pro2.c.options._rows = lambda *_: rows[:3]
    pro2.c.options.refresh()
    assert pro2.snap()['scroll'] == 0


def test_the_header_only_answers_where_it_is_drawn(pro2):
    pro2.open()
    before = pro2.snap()
    pro2.panel_xy(0, 100, og.section_box()[1] + 5); pro2.click()     # the label beside the box: nothing is drawn to press there
    after = pro2.snap()
    assert after['category'] == before['category'] and after['focus'] == before['focus'] and after['open']


def test_every_section_is_one_press_from_the_list(pro2):
    pro2.open()
    for category in om.CATEGORIES:
        pro2.tab(category)


def test_a_slider_drag_lands_even_if_the_page_refreshed_during_it(pro2):
    pro2.open()
    d = pro2.snap()
    index = next(i for i, r in enumerate(d['rows']) if r['id'] == 'placement_distance')
    pro2.panel_xy(0, 40, 200); pro2.wheel(4096)
    y = og.BODY_TOP + index * og.ROW_H + og.ROW_H / 2 - pro2.snap()['scroll']
    pro2.panel_xy(0, og.TRACK_X0 + 10, y)
    pro2.button('resize_press')
    pro2.c.options._bump()                       # an unrelated readback changed the page mid drag
    pro2.panel_xy(0, og.TRACK_X1, y)
    pro2.button('resize_release')
    assert pro2.c.state.placements[0]['distance'] == geometry.PLACEMENT_BOUNDS['distance'][1]


def test_a_slider_follows_the_hand_while_held(pro2):
    pro2.open()
    d = pro2.snap()
    index = next(i for i, r in enumerate(d['rows']) if r['id'] == 'placement_curvature')
    pro2.panel_xy(0, 40, 200); pro2.wheel(4096)
    y = og.BODY_TOP + index * og.ROW_H + og.ROW_H / 2 - pro2.snap()['scroll']
    pro2.panel_xy(0, og.TRACK_X0 + 10, y)
    pro2.button('resize_press')
    pro2.panel_xy(0, (og.KNOB_LO + og.KNOB_HI) / 2, y)
    lo, hi = geometry.PLACEMENT_BOUNDS['curvature']
    assert float(pro2.snap()['rows'][index]['value']) == pytest.approx((lo + hi) / 2, abs=1)   # what the renderer draws
    assert pro2.c.state.placements[0]['curvature'] == 0                                        # the owner hears at the release
    pro2.c.options.tick()                                                                      # a tick does not undo the preview
    assert float(pro2.snap()['rows'][index]['value']) == pytest.approx((lo + hi) / 2, abs=1)
    pro2.button('resize_release')
    assert pro2.c.state.placements[0]['curvature'] == pytest.approx((lo + hi) / 2, abs=1)


def test_action_rows_do_not_stay_pending_for_ever(pro2):
    """A receipt alone never says applied (contract v3): the footer stays pending, then says what is known."""
    pro2.open()
    pro2.tab('layout')
    d = pro2.snap()
    index = next(i for i, r in enumerate(d['rows']) if r['id'] == 'spacing_up')
    pro2.panel_xy(0, 40, og.BODY_TOP + index * og.ROW_H + og.ROW_H / 2); pro2.click()
    assert pro2.snap()['status'] == 'pending'
    for rid in list(pro2.c.options.pending_ids): pro2.c.options.acknowledge(rid)   # the renderer's control_applied
    pro2.c.options.tick()
    assert pro2.snap()['status'] == 'pending'
    pro2.clock.now += 6
    pro2.c.options.tick()
    d = pro2.snap()
    assert d['status'] == 'idle' and d['message'] and pro2.c.options.pending is None


def test_a_change_with_no_readback_ends_in_a_stated_timeout(pro2):
    pro2.open()
    pro2.c.options._apply = lambda *_: None
    d = pro2.snap()
    pro2.panel_xy(0, 40, og.row_top(0, 0) + og.ROW_H / 2); pro2.click()
    assert pro2.snap()['status'] == 'pending'
    pro2.clock.now += 6
    pro2.c.options.tick()
    d = pro2.snap()
    assert d['open'] and d['status'] == 'error' and d['message']


def test_every_key_route_through_the_bound_op(pro2):
    pro2.open()
    pro2.key(23)                       # Tab from Close: the first row
    assert pro2.snap()['focus'] == 0
    pro2.key(23, 1)                    # Shift+Tab: back
    assert pro2.snap()['focus'] == -1
    pro2.key(116); pro2.key(116)       # Down
    assert pro2.snap()['focus'] == 1
    pro2.key(111)                      # Up
    assert pro2.snap()['focus'] == 0
    for _ in range(2): pro2.key(23, 1)
    assert pro2.snap()['focus'] == og.FOCUS_SECTION  # Shift+Tab past Close: the section box, and the list is not a stop
    pro2.key(114)                      # Right: next section
    assert pro2.snap()['category'] == 'output'
    pro2.key(36)                       # Return opens the list
    assert pro2.snap()['focus'] == og.FOCUS_LIST
    pro2.key(116)                      # Down walks it, open
    assert pro2.snap()['category'] == '3d' and pro2.snap()['focus'] == og.FOCUS_LIST
    pro2.key(9)                        # Esc shuts the list, not the menu
    assert pro2.snap()['focus'] == og.FOCUS_SECTION and pro2.snap()['open']
    pro2.key(9)
    assert not pro2.snap()['open']


def test_a_press_off_the_open_list_only_shuts_it(pro2):
    pro2.open()
    x, y, w, h = og.section_box()
    pro2.panel_xy(0, x + w / 2, y + h / 2); pro2.click()
    assert pro2.snap()['focus'] == og.FOCUS_LIST
    before = pro2.snap()['category']
    close = og.close_box()
    pro2.panel_xy(0, close[0] + 4, close[1] + 4); pro2.click()   # Close is off the list: the list shuts first
    assert pro2.snap()['open'] and pro2.snap()['focus'] == og.FOCUS_SECTION and pro2.snap()['category'] == before


def test_each_scope_half_picks_its_scope(pro2):
    pro2.open()
    pro2.tab('3d')          # the one section with rows in both scopes
    x, y, w, h = og.scope_box()
    pro2.panel_xy(0, x + w * .75, y + h / 2); pro2.click()
    assert pro2.snap()['scope'] == 'workspace'
    pro2.panel_xy(0, x + w * .75, y + h / 2); pro2.click()
    assert pro2.snap()['scope'] == 'workspace'   # the lit half does nothing, it does not toggle
    pro2.panel_xy(0, x + w * .25, y + h / 2); pro2.click()
    assert pro2.snap()['scope'] == 'monitor'


def test_page_keys_scroll(pro2):
    pro2.open(); pro2.tab('video')
    limit = len(pro2.snap()['rows']) * og.ROW_H - og.BODY_H
    pro2.key(117)                      # a page down: one strip of rows
    assert pro2.snap()['scroll'] == min(og.BODY_H, limit)
    pro2.key(117)
    assert pro2.snap()['scroll'] == limit
    pro2.key(112); pro2.key(112)
    assert pro2.snap()['scroll'] == 0


def test_cursor_on_a_desktop_under_the_menu_is_the_menu(monkeypatch):
    """The menu is drawn on top: a window hidden under it must never take the click."""
    r = Rig('luma-saved')
    try:
        t = r.tile(1)
        r.c.state.placements[1] = dict(FLAT)
        r.p.rolls[:3] = [0, 0, 0]
        panel = og.panel_rect(t, fp.pill_rect(t))
        target = fp.project_glass(r.g, t, r.placement(1), panel[0] + 300, panel[1] + 200)
        # XR-3 moved down under XR-2's menu.
        for x, y in ((x, y) for y in range(-400, 0, 20) for x in range(-400, 400, 20)):
            r.c.state.placements[2] = dict(x=x, y=y, distance=100, curvature=0)
            under = fp.unproject(r.g, r.tile(2), r.placement(2), target)
            if under is not None and fp.inside_by(r.tile(2), under) > 40: break
        else: raise AssertionError('the fixture needs XR-3 under the menu')
        r.open(1)
        r.c.state.selected = 2                         # a click selected XR-3: its home, the menu stays (a desktop is no outside)
        # The real cursor, on XR-3, comes up under the menu.
        o = r.outputs[2]
        s = o['width'] / r.tile(2)[2]
        r.sample(o['x'] + 40, o['y'] + 40)
        r.p.mode = 'desktop'
        r.sample(o['x'] + (under[0] - r.tile(2)[0]) * s, o['y'] + (under[1] - r.tile(2)[1]) * s)
        assert r.p.mode == 'free', 'the window under the menu would take this click'
        assert r.p.hover[0] == 'options_panel'
    finally:
        r.close()


def test_stale_scroll_and_focus_are_gone_after_reopen(pro2):
    pro2.open()
    pro2.tab('video'); pro2.panel_xy(0, 300, 200); pro2.wheel(380)
    pro2.key(9)
    pro2.open()
    d = pro2.snap()
    assert d['scroll'] == 0 and d['focus'] == -1 and d['category'] == 'video' and d['rows']


def test_every_page_decodes_in_the_real_native_parser(rig):
    tool = os.environ.get('CONTROL_WIRE_CHECK', str(ROOT / 'build/release/control_wire_check'))
    if not Path(tool).exists(): pytest.skip('control_wire_check not built')
    rig.open()
    rig.wire.clear()
    for scope in om.SCOPES:
        for category in om.CATEGORIES:
            rig.take(rig.c.options_intent('options_category', category=category))
            rig.take(rig.c.options_intent('options_scope', scope=scope))
    frames = [l for l in bytes(rig.wire).splitlines() if b'"options_menu"' in l]
    for n, line in enumerate(frames):
        frame = json.loads(line); frame['request_id'] = n + 1
        frames[n] = json.dumps(frame, separators=(',', ':')).encode()
    done = subprocess.run([tool, 'run', 'session'], input=b'\n'.join(frames) + b'\n', capture_output=True, timeout=15, check=True)
    last = json.loads(done.stdout.splitlines()[-1])
    assert last['refused'] == 0 and last['accepted'] == len(frames), done.stdout[-400:]


def test_options_keys_are_plain_binds_with_a_dead_run_cleanup():
    """No submap: the catch-all that eats keys and the mouse binds never comes up for the options menu."""
    lua = view.install_options_binds_lua('/usr/bin/python3', '/x/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)
    assert 'submap' not in lua and 'catchall' not in lua
    assert 'xr_workspace_menu_binds = {' in lua
    assert xr_control.REMOVE_MENU_BINDS.split(' xr_workspace_mouse_binds')[0].split(' ipairs(')[1].startswith('xr_workspace_menu_binds')
    for chord, op in view.OPTIONS_BINDS:
        name, mods, code = op.split()
        assert name == xr_control.KEY_OP
        assert xr_control._key(['x', 'p', 'r', 's', name, mods, code]) == {'mods': int(mods), 'code': int(code)}
        assert keybinds.KEYCODES[int(code)]


def native_layout():
    tool = ROOT / 'build/release/options_geometry_dump'
    if not tool.exists(): pytest.skip('options_geometry_dump not built')
    cases = []
    for name in sorted(LAYOUTS):
        r = Rig(name)
        try:
            for i in range(len(r.names)):
                p = r.placement(i)
                w, h, (hw, hh) = r.g['w'], r.g['h'], r.g['half']
                cases.append(dict(canvas=[w, h, hw, hh], tile=list(r.tile(i)), wire=dict(p),
                                  placement=[p['x'] * orbit.TENTH, p['y'] * orbit.TENTH, p['distance'] / 100, math.radians(p['curvature']),
                                             math.radians(p.get('roll', 0) / 100)]))
        finally: r.close()
    done = subprocess.run([str(tool), '--layout'], input=json.dumps(cases).encode(), capture_output=True, check=True, timeout=15)
    return cases, json.loads(done.stdout)


def test_drawn_rects_are_the_hit_rects_at_every_real_layout():
    """Native glass.hpp / options_geometry.hpp rects and world points against the supervisor's mirror."""
    cases, native = native_layout()
    assert native
    for case, n in zip(cases, native):
        tile, wire = tuple(case['tile']), case['wire']
        g = dict(w=case['canvas'][0], h=case['canvas'][1], half=tuple(case['canvas'][2:]), tiles=[tile])
        pill = fp.pill_rect(tile)
        assert pill == pytest.approx(tuple(n['pill']), abs=1e-3)
        assert og.panel_rect(tile, pill) == pytest.approx(tuple(n['panel']), abs=1e-3)
        assert og.gear_rect(pill) == pytest.approx(tuple(n['gear']), abs=1e-3)
        assert fp.maximize_rect(tile) == pytest.approx(tuple(n['maximize']), abs=1e-3)
        x, y, w, h = n['gear_hit']
        assert og.gear_hit(pill, (x + .01, y + .01)) and og.gear_hit(pill, (x + w - .01, y + h - .01))
        assert not og.gear_hit(pill, (x - .5, y)) and not og.gear_hit(pill, (x + w + .5, y))
        x, y, w, h = n['maximize_hit']
        assert fp.maximize_hit(tile, (x + .01, y + .01)) and fp.maximize_hit(tile, (x + w - .01, y + h - .01))
        assert not fp.maximize_hit(tile, (x - .5, y)) and not fp.maximize_hit(tile, (x + w + .5, y))
        px, py, _, _ = n['panel']
        world = orbit.place_point(g, tile, wire, *fp.glass_to_desktop(tile, wire, px + 300, py + 220))
        assert world == pytest.approx(tuple(n['panel_mid']), abs=2e-4)
        m = n['maximize']
        assert orbit.place_point(g, tile, wire, m[0] + m[2] / 2, m[1] + m[3] / 2) == pytest.approx(tuple(n['maximize_mid']), abs=2e-4)


def n_item(tool, i):
    return json.loads(subprocess.run([str(tool), '--section', str(i)], capture_output=True, check=True, timeout=15).stdout)


def test_menu_constants_are_the_natives():
    tool = ROOT / 'build/release/options_geometry_dump'
    if not tool.exists(): pytest.skip('options_geometry_dump not built')
    n = json.loads(subprocess.run([str(tool)], capture_output=True, check=True, timeout=15).stdout)
    for key, value in n.items():
        assert getattr(og, key.upper()) == pytest.approx(value), key
    for i in range(og.SECTIONS):
        assert og.section_item(i) == pytest.approx(tuple(n_item(tool, i)))


class Binds:
    def __init__(self): self.log = []
    def menu(self, on): self.log.append(('menu', on))
    def options(self, on): self.log.append(('options', on))


def test_the_panel_and_menu_keys_share_one_table_off_before_on(pro2):
    b = Binds()
    pro2.c.menu_binds, pro2.c.options_binds = b.menu, b.options
    pro2.c.options_intent('options_open', monitor='XR-1'); pro2.settle()
    assert b.log == [('options', True)]
    pro2.take(pro2.c.on_message({'op': 'menu_toggle', 'payload': {}})); pro2.settle()
    assert b.log == [('options', True), ('options', False), ('menu', True)]
    b.log.clear()
    pro2.take(pro2.c.options_intent('options_open', monitor='XR-1')); pro2.settle()
    assert b.log == [('menu', False), ('options', True)]


def test_a_config_reload_puts_the_menu_keys_back(pro2):
    b = Binds()
    pro2.c.options_binds = b.options
    pro2.c.options_intent('options_open', monitor='XR-1'); pro2.settle()
    b.log.clear()
    pro2.c._sync_menu_binds(force=True); pro2.settle()
    assert b.log == [('options', True)]


def test_a_dead_runs_key_clears_the_menu_keys(monkeypatch):
    dropped = []
    monkeypatch.setattr(xr_control, 'send', lambda *a: (_ for _ in ()).throw(OSError('no run')))
    monkeypatch.setattr(xr_control, '_eval_stale', lambda env, code: dropped.append(code) or True)
    assert xr_control.main(['x', '/run/user/1000/c', 'a' * 32, 'e' * 64, 'binds_key', '0', '9']) == 1
    assert any(xr_control.REMOVE_MENU_BINDS in c for c in dropped)


def test_managed_wires_the_keys_to_the_plain_binds_not_the_submap():
    source = (ROOT / 'tools/native_spike/managed.py').read_text()
    block = source[source.index('def options_binds(owned):'):source.index('control.options_binds = options_binds')]
    assert 'install_options_binds' in block and 'open_key_panel' not in block


# (desktop name, logical w, h): the Pro 2 session's. The maximized tile fills the canvas width (view.cpp zoomed_layout).
RESTORE_CASES = (('XR-1', 5120 / 1.25, 1440 / 1.25), ('XR-2', 2560 / 2.0, 1440 / 2.0))


def restore_lines(w=1920, h=1200):
    tool = ROOT / 'build/release/options_geometry_dump'
    if not tool.exists(): pytest.skip('options_geometry_dump not built')
    d, a = math.tan(math.radians(23)), w / h
    half = [d * a / math.sqrt(1 + a * a), d / math.sqrt(1 + a * a)]
    cases = []
    for name, lw, lh in RESTORE_CASES:
        tile_h = min(h, w * lh / lw)
        cases.append(dict(monitor=name, canvas=[w, h, *half], tile=[(w - tile_h * lw / lh) / 2, (h - tile_h) / 2, tile_h * lw / lh, tile_h]))
    done = subprocess.run([str(tool), '--restore'], input=json.dumps(cases).encode(), capture_output=True, check=True, timeout=15)
    return [fp.parse_maximize_geometry(json.loads(line)['maximize_geometry']) for line in done.stdout.splitlines()]


MAXIMIZE_W = fp.MAXIMIZE_W


def test_restore_hit_region_is_the_drawn_button_at_the_real_sizes():
    """Current placement, unchanged (the mockups thread owns any move): its cursor region is the drawn rect plus the pad, no more."""
    for geometry, (name, lw, lh) in zip(restore_lines(), RESTORE_CASES):
        output = dict(index=0, x=0, y=0, width=lw, height=lh)
        x, y, w, h = geometry['rect']
        tx, ty, tw, th = geometry['tile']
        assert (w, h) == (MAXIMIZE_W, 26) and x > tx and y > ty, name
        def over(px, py):
            cursor = ((px - tx) / tw * lw, (py - ty) / th * lh)
            return fp.restore_mesh_hit(geometry, fp.restore_cursor_ray(geometry, output, cursor))
        assert over(x + w / 2, y + 13) and over(x - 3.5, y + 13) and over(x + w + 3.5, y + 13) and over(x + w / 2, y + 29.5), name
        assert not over(x - 5, y + 13) and not over(x + w + 5, y + 13) and not over(x + w / 2, y + 31), name


def test_resolution_changes_only_by_a_deliberate_pick(pro2):
    sizes = ['1920x1080', '1920x1200', '2560x1600', '3840x2160']     # this rig's outputs have no size, so the row is given
    pro2.c.options._rows = lambda *_: [dict(id='resolution', label='Resolution', scope='monitor', kind='choice', value='1920x1200', enabled=True, choices=sizes)]
    pro2.c.options._apply = pro2.record
    pro2.open()
    pro2.tab('output')
    d = pro2.snap()
    index = 0
    row = d['rows'][index]
    y = og.row_top(index, d['scroll']) + og.ROW_H / 2
    pro2.panel_xy(0, og.BOX_X1 - 40, y); pro2.click()        # a click on the row: its list
    d = pro2.snap()
    assert d['list'] == row['choices'].index(row['value']) and not pro2.applied
    pro2.panel_xy(0, 40, 20); pro2.click()                   # a press off the list: shut, nothing applied, the menu stays
    assert pro2.snap()['open'] and pro2.snap()['list'] == -1 and not pro2.applied
    pro2.panel_xy(0, og.BOX_X1 - 40, y); pro2.click()
    pro2.key(114)                                            # Right does not step it
    assert not pro2.applied
    pro2.panel_xy(0, og.BOX_X1 - 40, y); pro2.click()
    other = (pro2.snap()['list'] + 1) % len(row['choices'])
    x, iy, w, h = og.choice_item(og.row_top(index, pro2.snap()['scroll']), len(row['choices']), other)
    pro2.panel_xy(0, x + w / 2, iy + h / 2); pro2.click()    # an item: that value, once
    assert pro2.applied == pro2.applied[:1] and pro2.applied[0][0] == 'resolution'
