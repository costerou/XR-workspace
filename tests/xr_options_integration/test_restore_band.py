"""R3, the Step-Back Band: Restore off the picture, through the real pointer, control and native geometry, on a fake compositor.

Resting the pointer on the output's top edge eases the picture back, hands the pointer to the dot, which leaves the output into the
band above the picture; Restore is there. Back down, the picture comes forward and the app has the pointer again."""
import json
import subprocess
from pathlib import Path

import pytest
from unittest.mock import Mock

from tools.native_spike import free_pointer as fp, restore_band as rb, view
from test_options_live_routes import LAYOUTS, Rig, ROOT

TOOL = ROOT / 'build/release/options_geometry_dump'
SIZES = {'luma XR-1': (2560, 1600, 1.25), 'luma XR-2': (1920, 1200, 1.25), 'pro2 XR-1': (5120, 1440, 1.25), 'pro2 XR-2': (2560, 1440, 2.0)}
SHAPES = {'flat': (0, 0), 'rolled': (0, .14), 'curved': (1.05, 0)}


def native(width, height, curvature=0.0, roll=0.0):
    """The renderer's maximize_geometry for one maximized desktop (spike/src/maximize_geometry.hpp)."""
    case = dict(monitor='XR-1', source=[width, height], canvas=[1920, 1200, .78, .4875], placement=[0, 0, 1, curvature, roll])
    done = subprocess.run([str(TOOL), '--band'], input=json.dumps([case]).encode(), capture_output=True, check=True, timeout=15)
    return json.loads(done.stdout.splitlines()[0])['maximize_geometry']


class Band:
    """A Rig maximized on XR-1, its output `w`/`scale` by `h`/`scale`, the renderer's geometry given to the pointer."""
    def __init__(self, width=5120, height=1440, scale=1.25, shape='flat', layout='pro2-flat'):
        self.r = r = Rig(layout)
        r.outputs[0].update(width=width / scale, height=height / scale)
        r.take(r.c.on_message({'op': 'zoom_in', 'payload': {}}))
        self.o = r.outputs[0]
        self.g = native(width, height, *SHAPES[shape])
        r.p.set_maximize_geometry(self.g)
        r.take(r.c.poll())
        self.frames = []

    def close(self): self.r.close()
    @property
    def p(self): return self.r.p

    def at(self, x, y, advance=.01):
        """A sample with the cursor at output px (x, y)."""
        n = len(self.r.wire)
        self.r.sample(self.o['x'] + x, self.o['y'] + y, advance)
        self.frames += [json.loads(line) for line in bytes(self.r.wire[n:]).splitlines()]

    def rest(self, x, y, seconds, step=.01):
        for _ in range(round(seconds / step)): self.at(x, y, step)

    def move(self, dx, dy, advance=.01):
        """Hand motion while the cursor is parked: what the sampler reads in free mode."""
        n = len(self.r.wire)
        self.r.clock.now += advance
        self.p.feed(self.r.cursor, (dx, dy), self.r.outputs, self.p.want[0])
        self.r.take(self.r.c.poll())
        self.frames += [json.loads(line) for line in bytes(self.r.wire[n:]).splitlines()]

    def button(self, op):
        n = len(self.r.wire)
        self.r.button(op)
        self.frames += [json.loads(line) for line in bytes(self.r.wire[n:]).splitlines()]

    def key(self, op):
        n = len(self.r.wire)
        self.r.take(self.r.c.on_message({'op': op, 'payload': {}}))
        self.frames += [json.loads(line) for line in bytes(self.r.wire[n:]).splitlines()]
    def opened(self): return [f['payload']['open'] for f in self.frames if f.get('op') == 'restore_band']
    def trigger(self, x=400):
        self.at(x, 300); self.at(x, 0); self.rest(x, 0, rb.DWELL_S + .03)
        assert self.p.mode == 'restore'
    def to_restore(self):
        """Hand motion taking the dot onto the Restore pill."""
        x, y, w, h = self.g['rect']
        cx, cy = fp.band_canvas(self.g, x + w / 2, y + h / 2)
        at = self.p.band_point
        k = self.p.band_gain
        self.move((cx - at[0]) / k, (cy - at[1]) / k)


@pytest.fixture
def band():
    b = Band()
    yield b
    b.close()


# --- no accidental trigger -------------------------------------------------------------------------------------------
def test_a_brief_touch_does_nothing(band):
    band.at(400, 300); band.at(400, 0); band.rest(400, 0, rb.DWELL_S - .03); band.at(400, 200)
    band.rest(400, 200, 1)
    assert band.p.mode != 'restore' and band.opened() == [] and band.p.want[1] != 'free'


def test_just_under_the_dwell_does_nothing_and_just_over_opens(band):
    band.at(400, 300); band.at(400, 0)
    band.rest(400, 0, rb.DWELL_S - .02)
    assert band.opened() == []
    band.rest(400, 0, .03)
    assert band.opened() == [True]


@pytest.mark.parametrize('y', (1, 2, 5, 14, 30, 60))
def test_resting_near_the_top_without_touching_it_does_nothing(band, y):
    """Browser tabs, title bars and the bar: being near the edge is not pressing against it."""
    band.at(400, 300); band.at(400, y); band.rest(400, y, 2)
    assert band.opened() == []


def test_sliding_along_the_edge_does_nothing(band):
    band.at(100, 300)
    for x in range(100, 1100, 6): band.at(x, 0, .01)
    assert band.opened() == []


def test_a_small_jitter_keeps_the_rest(band):
    band.at(400, 300); band.at(400, 0)
    for i in range(45): band.at(400 + i % 2, 0, .01)
    assert band.opened() == [True]


def test_a_press_restarts_the_rest_and_a_held_button_never_opens(band):
    band.at(400, 300); band.at(400, 0); band.rest(400, 0, .3)
    band.button('resize_press')
    band.rest(400, 0, 2)                    # a drag to the top, held there
    assert band.opened() == []
    band.button('resize_release')
    band.rest(400, 0, rb.DWELL_S - .03)     # the click on the bar counts as no rest
    assert band.opened() == []
    band.rest(400, 0, .05)
    assert band.opened() == [True]


def test_a_click_on_the_top_row_is_the_apps(band):
    band.at(400, 300); band.at(400, 0); band.rest(400, 0, .1)
    assert band.p.want[1] != 'free'
    band.button('resize_press'); band.button('resize_release')
    assert band.p.mode != 'restore' and band.opened() == []


def test_leaving_the_zone_cancels(band):
    band.at(400, 300); band.at(400, 0); band.rest(400, 0, .3); band.at(400, rb.ZONE_PX + 2); band.at(400, 0); band.rest(400, 0, .1)
    assert band.opened() == []


@pytest.mark.parametrize('blocked', ('typing', 'keys', 'video', 'no geometry'))
def test_not_while_something_else_owns_the_pointer(blocked):
    b = Band()
    try:
        if blocked == 'typing': b.r.c.state.typing = True
        if blocked == 'keys': b.r.c.keys = Mock(open=True, poll=lambda *_: b'')
        if blocked == 'video': b.r.c.video_active = True
        if blocked == 'no geometry': b.p.set_maximize_geometry(None)
        b.at(400, 300); b.at(400, 0); b.rest(400, 0, 1)
        assert b.opened() == []
    finally: b.close()


def test_an_edge_pan_is_not_a_rest(band):
    band.at(400, 300); band.at(400, 0)
    band.p.set_pan(dict(side=0))
    band.rest(400, 0, 1)
    assert band.opened() == []


def test_a_nudged_touch_still_rests():
    """With a desktop placed above, EdgePush puts a touch NUDGE_PX back in: the rest carries on there, without another touch."""
    b = Band()
    try:
        b.at(400, 300); b.at(400, 0); b.rest(400, 12, rb.DWELL_S + .03)
        assert b.opened() == [True]
    finally: b.close()


def test_outside_the_output_is_not_the_edge(band):
    band.at(400, 300); band.r.sample(100, 0); band.rest(-2000, 0, 1)
    assert band.opened() == []


# --- handoff ---------------------------------------------------------------------------------------------------------
def test_the_pointer_hands_over_to_the_dot(band):
    band.trigger()
    assert band.p.want[1] == 'free' and band.opened() == [True]
    dots = [f['payload'] for f in band.frames if f.get('op') == 'overview_pointer']
    assert dots and dots[-1]['visible'] and 'az' in dots[-1] and 'el' in dots[-1]


def test_the_dot_leaves_the_output_into_the_band(band):
    band.trigger()
    band.move(0, -40)
    assert band.p.band_point[1] < band.g['picture'][1]    # above the picture: no output pixel maps there
    assert band.p.mode == 'restore'


def test_a_click_on_restore_restores_and_the_app_gets_nothing(band):
    band.trigger(); band.to_restore()
    band.button('resize_press'); band.button('resize_release')
    assert not band.r.c.state.zoomed and band.p.mode != 'restore' and band.opened()[-1] is False


def test_drag_off_restore_does_not_restore(band):
    band.trigger(); band.to_restore()
    band.button('resize_press'); band.move(0, -30)
    x0, y0, x1, y1 = band.g['picture']
    band.p.band_point = (x0 + 2, 5.0)
    band.move(0, 0); band.button('resize_release')
    assert band.r.c.state.zoomed


# --- return ----------------------------------------------------------------------------------------------------------
def test_moving_down_brings_the_picture_forward_and_gives_the_app_its_pointer(band):
    band.trigger(); band.move(0, -30)
    band.move(0, 300)
    assert band.opened() == [True, False] and band.p.mode != 'restore'
    assert band.p.want[1] == 'desktop'
    x, y = band.p.want[2]
    assert band.o['x'] <= x < band.o['x'] + band.o['width'] and y == band.o['y'] + rb.LAND_PX


def test_no_click_is_lost_after_returning(band):
    band.trigger(); band.move(0, 300)
    band.at(400, rb.LAND_PX)
    band.button('resize_press'); band.button('resize_release')
    assert band.r.c.state.zoomed and band.p.mode != 'restore' and band.opened() == [True, False]


def test_returning_does_not_open_again_until_the_pointer_has_left(band):
    band.trigger(); band.move(0, 300)
    band.rest(400, rb.LAND_PX, 1)
    band.at(400, 0); band.rest(400, 0, .1)
    assert band.opened() == [True, False]
    band.rest(400, 0, rb.DWELL_S)
    assert band.opened() == [True, False, True]


def test_the_side_margins_are_not_the_picture(band):
    band.trigger()
    x0, y0, x1, y1 = band.g['picture']
    assert band.p.mode == 'restore'
    if x0 > 2:
        band.p.band_point = (x0 / 2, (y0 + y1) / 2); band.move(0, 0)
        assert band.p.mode == 'restore'


# --- cancel ----------------------------------------------------------------------------------------------------------
def test_a_press_off_restore_closes_it(band):
    band.trigger(); band.move(0, -3)
    band.button('resize_press'); band.button('resize_release')
    assert band.opened() == [True, False] and band.r.c.state.zoomed and band.p.mode != 'restore'


def test_it_closes_by_itself_when_left_alone(band):
    band.trigger()
    band.r.clock.now += rb.IDLE_S + .1
    band.move(0, 0)
    assert band.opened() == [True, False] and band.p.want[1] == 'desktop'


def test_ctrl_alt_down_still_works_and_closes_it(band):
    band.trigger()
    band.key('zoom_out')
    band.move(0, 0)
    assert not band.r.c.state.zoomed and band.p.mode != 'restore' and band.opened()[-1] is False


def test_ctrl_alt_down_works_with_nothing_open(band):
    band.r.take(band.r.c.on_message({'op': 'zoom_out', 'payload': {}}))
    assert not band.r.c.state.zoomed


def test_the_geometry_going_closes_it(band):
    band.trigger()
    band.p.set_maximize_geometry(None); band.move(0, 0)
    assert band.p.mode != 'restore' and band.opened()[-1] is False


def test_a_stale_press_does_not_restore(band):
    band.trigger(); band.to_restore()
    band.button('resize_press')
    band.p.set_maximize_geometry(dict(band.g, tile=[10, 10, 1900, 1100]))
    band.button('resize_release')
    assert band.r.c.state.zoomed


# --- every real size and shape ---------------------------------------------------------------------------------------
@pytest.mark.parametrize('shape', SHAPES)
@pytest.mark.parametrize('name', SIZES)
def test_restore_is_reachable_and_clear_of_the_picture(name, shape):
    w, h, scale = SIZES[name]
    b = Band(w, h, scale, shape)
    try:
        x0, y0, x1, y1 = b.g['picture']
        rx, ry = fp.band_canvas(b.g, b.g['rect'][0] + b.g['rect'][2] / 2, b.g['rect'][1] + b.g['rect'][3])
        assert 0 < ry < y0, (ry, y0)                      # the pill's bottom edge above the picture, in the view
        b.trigger(); b.to_restore()
        assert b.p.band_hot()
        b.button('resize_press'); b.button('resize_release')
        assert not b.r.c.state.zoomed
    finally: b.close()


@pytest.mark.parametrize('layout', sorted(LAYOUTS))
def test_on_every_layout(layout):
    b = Band(layout=layout)
    try:
        b.trigger(); b.move(0, 300)
        assert b.opened() == [True, False]
    finally: b.close()


def test_the_drawn_pill_is_the_hit_region():
    g = native(1920, 1200)
    x, y, w, h = g['rect']
    for px, py, inside in ((x + w / 2, y + h / 2, True), (x - 3, y + h / 2, True), (x + w + 3, y + h / 2, True), (x - 12, y + h / 2, False), (x + w + 12, y + h / 2, False), (x + w / 2, y - 12, False)):
        cx, cy = fp.band_canvas(g, px, py)
        assert fp.restore_mesh_hit(g, fp.band_ray(g, cx, cy)) is inside, (px, py)


def test_the_band_constants_are_the_natives():
    n = json.loads(subprocess.run([str(TOOL)], capture_output=True, check=True, timeout=15).stdout)
    assert n['step_scale'] == pytest.approx(rb.STEP_SCALE) and n['step_ms'] == rb.STEP_MS and n['band_min'] == rb.BAND_MIN and n['band_gap'] == rb.BAND_GAP
