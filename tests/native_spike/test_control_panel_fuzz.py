"""Everything the placement panel can say to the renderer, through the renderer's own decoder.

R ended a session because two sides disagreed about one frame (test_control_wire.py), and nothing had put what the supervisor
really sends through what the renderer really reads. Here that is the point of every test:
  * ContractTests: whatever a frame builder lets through, the renderer takes. Each builder at its bounds and either side of them.
    A size Python allows and C++ does not would drop every panel frame for that desktop, without a word.
  * PanelFlowTests: real clicks on the panel, through the real pointer: the sliders, a slider held, the Resolution, Scale and
    Background lists and the desktop row, between R, Tab and the rest of its keys, on 1 to 4 desktops.
  * InterleaveTests: every ordered pair of panel actions, menu frames beside action frames (placement, roll, view, desktops), from
    each state a panel can be in. The ids must rise across all of it.
  * ThreadTests: a frame takes its id when it is built, and `next_id += 1` is not atomic. So every id is taken on the
    supervisor's thread, with the real worker threads running.

The contract needs the real decoder (control_wire.TOOL, the ctest control_wire), and is skipped without it. The rest also check
the ids in a plain run of the Python suite. Offline only: no compositor, GPU or glasses.
"""
import itertools
import json
import os
import random
import socket
import threading
import time
import unittest

from tools.native_spike import desktops as dk, edge_pan, environment, free_pointer as fp, geometry, keybinds as kb, resize, view
from tools.native_spike.geometry import PLACEMENT_BOUNDS, PLACEMENT_KEYS, RESOLUTIONS

from control_wire import TOOL, faults, ids, problems, refused
from test_control_wire import CODE, G, NAMES, RUN, SESSION, Rig, Session, TILTED, OUTPUT

MENU_KEYS = ('menu_reset', 'menu_back', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev', 'menu_inc', 'menu_dec',
             'menu_inc_big', 'menu_dec_big', 'menu_ultrawide', 'menu_add', 'menu_delete', 'menu_toggle')


def geometry_for(count):
    """An overview layout with `count` slots, as the renderer reports one after a desktop change."""
    width = 1840 / count
    return fp.parse_overview_geometry({'w': 1920, 'h': 1200, 'half': [0.36, 0.225],
                                       'tiles': [[40 + i * width, 435.0, width - 40, 330.0] for i in range(count)]})


class Panel(Rig):
    """Rig, driven the way a person does: the dot, the left button and the keys. Every byte is checked as it goes out."""
    def __init__(self, test, desktops=None):
        super().__init__(test, desktops)
        self.test = test
        self.wire, self.last, self.trace, self.seen = bytearray(), 0, [], set()
        self.g = None
        self.layout()

    def layout(self):
        count = len(self.c.state.names)
        if self.g is None or len(self.g['tiles']) != count:
            self.g = geometry_for(count)
            self.c.set_overview_geometry(self.g)

    def take(self, out, what):
        data = bytes(out) if out else b''
        self.trace = (self.trace + [what])[-8:]
        self.test.assertEqual(problems(data, self.last), [], self.trace)
        if data: self.last = ids(data)[-1]
        self.wire += data
        close = getattr(out, 'close_fds', None)
        if close is not None: close()
        for frame in map(json.loads, data.splitlines()):
            self.seen.add(frame['op'])
            if frame['op'] == 'placement':
                if 'glide' in frame['payload']: self.seen.add('placement glide')
            if frame['op'] == 'desktops' and frame['payload'].get('ease'): self.seen.add('desktops ease')
            if frame['op'] != 'placement_menu': continue
            p = frame['payload']
            self.seen.update(key for key in ('picker', 'resolution_picker', 'background_picker', 'armed', 'busy', 'wide', 'surface') if p.get(key))
            self.seen.add(f"scale {p.get('scale')}")
            self.seen.add(f"field {p['field']}")
            self.seen.add(f"{len(p['placements'])} desktops")
            if 'names' in p and p['names'] != list(range(1, len(p['names']) + 1)): self.seen.add('names with a gap')
        return data

    def key(self, op): return self.take(self.v.on_message({'op': op, 'payload': {}}), op)

    def tick(self, seconds=0.004):
        self.clock.now += seconds
        out = self.take(self.v.poll(), 'poll')
        self.layout()
        return out

    def feed(self, cursor, motion):
        self.clock.now += 0.004
        self.cursor = cursor
        self.p.feed(cursor, motion, self.outputs_now(), self.p.want[0])
        return self.tick(0)

    def free(self):
        """The dot free, off the top of the desktop the selection is on."""
        if self.c.state.zoomed or self.c.state.typing or self.c.video_active or self.c.keys.open: return False
        if self.p.mode != 'free':
            x = 2560 + 1920 * self.c.state.selected + 960
            self.feed((x, 300), (0.0, 0.0))
            self.feed((x, 0), (0.0, 0.0))
        return self.p.mode == 'free' and self.p.pointer is not None

    def aim(self, point):
        if not self.free(): return False
        dx, dy = point[0] - self.p.pointer[0], point[1] - self.p.pointer[1]
        self.feed(self.cursor, (dx / self.p.gain, dy / self.p.gain))
        return True

    def on_panel(self, lx, ly):
        s = self.c.state
        i = s.menu_desktop
        tile = self.g['tiles'][i]
        x, y, _, _ = fp.panel_rect(tile)
        return fp.project_glass(self.g, tile, s.menu_surface or s.placements[i], x + lx, y + ly)

    def click(self, point, expect=None):
        """Press and release on `point`. `expect`: what the dot must be on, for a test that means to hit it: a click that misses
        would pass for a test of nothing."""
        if point is not None and self.aim(point):
            if expect is not None: self.test.assertEqual((self.p.hover or (None,))[0], expect, self.trace)
            self.key('resize_press')
            self.key('resize_release')
        elif expect is not None: self.test.fail(f'nothing to click for {expect}: {self.trace}')

    def rect(self, r):
        x, y, w, h = r
        return self.on_panel(x + w / 2, y + h / 2) if self.c.state.menu_open else None

    def pill(self, i):
        s = self.c.state
        i %= len(s.names)
        tile = self.g['tiles'][i]
        x, y, w, h = fp.pill_rect(tile)
        return fp.project_glass(self.g, tile, s.placements[i], x + w / 2, y + h / 2)

    def bar(self, i, share):
        s = self.c.state
        i %= len(s.names)
        tile = self.g['tiles'][i]
        x, y, w, h = fp.level_bar_rect(tile)
        return fp.project(self.g, tile, s.placements[i], x + w * share, y + h / 2)

    def slider_point(self, row, t):
        return self.on_panel(fp.PANEL_KNOB_LO + t * (fp.PANEL_KNOB_HI - fp.PANEL_KNOB_LO), fp.PANEL_ROWS_TOP + fp.PANEL_ROW_H * (row + 0.5))

    def brightness_point(self, t):
        return self.on_panel(fp.PANEL_KNOB_LO + t * (fp.PANEL_KNOB_HI - fp.PANEL_KNOB_LO), fp.PANEL_BRIGHTNESS_TOP + fp.PANEL_ROW_H / 2)

    def hold(self, row, t):
        """A slider pressed and dragged, not let go."""
        if not self.c.state.menu_open or not self.aim(self.slider_point(row, t)): return
        self.key('resize_press')
        self.aim(self.slider_point(row, min(t + 0.2, 1.0)))

    def tilts(self, *tilts):
        count = len(self.c.state.names)
        self.c.set_roll_geometry((list(tilts) * count)[:count])

    def panel_open(self):
        if not self.c.state.menu_open: self.key('menu_toggle')


def actions(panel):
    """Every panel action, named, each one a person could do at any moment: most are stale in some state, which is the point."""
    p = panel
    out = {key: (lambda key=key: p.key(key)) for key in MENU_KEYS}
    out.update({
        'layout_reset': lambda: p.key(view.RESET_OP), 'maximize': lambda: p.key('zoom_in'), 'overview': lambda: p.key('zoom_out'),
        'next': lambda: p.key('next'), 'typing': lambda: p.key(view.TYPING_OP), 'key panel': lambda: p.key(kb.PANEL_OP), 'help': lambda: p.key('help'),
        'background key': lambda: p.key(view.ENVIRONMENT_OP),
        'key R': lambda: p.key(kb.PANEL_OP) if not p.c.keys.open else p.take(p.c.keys.key(0, CODE['R']), 'key R'),
        'key Shift+R': lambda: p.key(kb.PANEL_OP) if not p.c.keys.open else p.take(p.c.keys.key(1, CODE['R']), 'key Shift+R'),
        'poll': lambda: p.tick(), 'wait': lambda: p.tick(0.7), 'idle': lambda: p.tick(31), 'tilt': lambda: p.tilts(150, -70, 33),
        'pill': lambda: p.click(p.pill(p.c.state.selected)), 'other pill': lambda: p.click(p.pill(p.c.state.selected + 1)),
        'bar ccw': lambda: p.click(p.bar(0, 1 / 6)), 'bar level': lambda: p.click(p.bar(0, 1 / 2)), 'bar cw': lambda: p.click(p.bar(0, 5 / 6)),
        'scale box': lambda: p.click(p.rect(fp.PANEL_SCALE_BOX)), 'scale 150': lambda: p.click(p.rect(fp.scale_item_rect(2))),
        'scale 200': lambda: p.click(p.rect(fp.scale_item_rect(4))), 'remove desktop': lambda: (p.key('menu_delete'), p.key('menu_delete'), p.tick()),
        'resolution box': lambda: p.click(p.rect(fp.PANEL_RES_BOX)), 'resolution 1440p': lambda: p.click(p.rect(fp.resolution_item_rect(3))),
        'background box': lambda: p.click(p.rect(fp.PANEL_BACKGROUND_BOX)), 'background grid': lambda: p.click(p.rect(fp.background_item_rect(1))),
        'background switch': lambda: p.click(p.rect(fp.PANEL_BACKGROUND_SWITCH)),
        'slider': lambda: p.click(p.slider_point(0, 0.8) if p.c.state.menu_open else None),
        'brightness': lambda: p.click(p.brightness_point(0.3) if p.c.state.menu_open else None),
        'hold slider': lambda: p.hold(1, 0.3), 'let go': lambda: p.key('resize_release'),
    })
    return out


def tour(count):
    """A list of 0..count-1 holding every ordered pair once, end to end (a de Bruijn sequence: the Lyndon words of length 1 and 2,
    in order)."""
    out = []
    for a in range(count):
        out.append(a)
        for b in range(a + 1, count): out += [a, b]
    return out + out[:1]


class TourTests(unittest.TestCase):
    def test_a_tour_holds_every_ordered_pair(self):
        for count in (1, 2, 5, 48):
            walk = tour(count)
            self.assertEqual({pair for pair in zip(walk, walk[1:])}, set(itertools.product(range(count), repeat=2)))
            self.assertEqual(len(walk), count * count + 1)


class InterleaveTests(unittest.TestCase):
    """Each state, then every ordered pair of actions from there on, so menu frames land beside every kind of action frame."""
    STATES = {
        'panel open': lambda p: p.panel_open(),
        'moved and tilted': lambda p: (p.panel_open(), p.key('menu_inc'), p.tilts(*TILTED)),
        'a list open': lambda p: (p.panel_open(), p.click(p.rect(fp.PANEL_SCALE_BOX))),
        'a slider held': lambda p: (p.panel_open(), p.hold(0, 0.4)),
        'remove armed': lambda p: (p.panel_open(), p.key('menu_delete')),
        'maximized': lambda p: p.key('zoom_in'),
        'key panel': lambda p: p.key(kb.PANEL_OP),
        'four desktops': lambda p: (p.panel_open(), p.key('menu_add'), p.tick()),
    }

    def test_every_pair_of_actions_from_every_state(self):
        for state, enter in self.STATES.items():
            with self.subTest(state):
                p = Panel(self)
                enter(p)
                acts = list(actions(p).items())
                for i in tour(len(acts)):
                    name, act = acts[i]
                    act()
                    if name in ('typing',) and p.c.state.typing: p.key(view.TYPING_OP)  # or a third of the tour is typing mode
                self.assertEqual(faults(p.wire, 'run', 'session'), [], f'{state}: {p.trace}')


class PanelFlowTests(unittest.TestCase):
    def test_every_step_of_the_scale_list_on_every_size_it_can_have(self):
        for size in RESOLUTIONS:
            with self.subTest(size):
                p = Panel(self)
                p.panel_open()
                p.click(p.rect(fp.PANEL_RES_BOX), 'resolution_box')
                p.click(p.rect(fp.resolution_item_rect(RESOLUTIONS.index(size))), 'resolution')
                p.tick()
                self.assertEqual((p.c.state.sizes[0]['width'], p.c.state.sizes[0]['height']), size)
                for step, percent in enumerate(view.SCALE_PERCENTS):
                    p.click(p.rect(fp.PANEL_SCALE_BOX), 'scale_box')
                    p.click(p.rect(fp.scale_item_rect(step)), 'scale')
                    p.tick()
                    p.key('menu_reset')
                    if percent / 100 in geometry.valid_scales(*size): self.assertEqual(view.scale_percent(p.c.state.sizes[0]['scale']), percent)
                self.assertEqual(faults(p.wire, 'run', 'session'), [])

    def test_every_background_and_every_slider_at_both_ends_with_r_between(self):
        p = Panel(self)
        p.panel_open()
        for i, (name, _) in enumerate(environment.BACKGROUNDS):
            p.click(p.rect(fp.PANEL_BACKGROUND_BOX), 'background_box')
            p.click(p.rect(fp.background_item_rect(i)), 'background')
            self.assertEqual(p.c.state.environment['background'], name)
            p.key('menu_reset')
        for row, key in enumerate(PLACEMENT_KEYS):
            for t in (0.0, 0.5, 1.0):
                p.click(p.slider_point(row, t), 'slider')
                self.assertEqual(p.c.state.menu_field, key)
                p.key('menu_reset')
        for t in (0.0, 0.5, 1.0):
            p.click(p.brightness_point(t), 'slider')
            self.assertEqual(p.c.state.menu_field, environment.BRIGHTNESS_FIELD)
            p.key('menu_reset')
        self.assertEqual(faults(p.wire, 'run', 'session'), [])

    def test_one_list_at_a_time_however_it_is_asked_for(self):
        boxes = {'scale': (fp.PANEL_SCALE_BOX, 'scale_picker'), 'resolution': (fp.PANEL_RES_BOX, 'resolution_picker'),
                 'background': (fp.PANEL_BACKGROUND_BOX, 'background_picker')}
        count = lambda p: sum(getattr(p.c.state, attr) for _, attr in boxes.values())
        for first, second in itertools.product(boxes, repeat=2):
            with self.subTest(first=first, second=second):
                p = Panel(self)
                p.panel_open()
                p.click(p.rect(boxes[first][0]), f'{first}_box')
                self.assertEqual(count(p), 1)
                p.click(p.rect(boxes[second][0]))  # a press with a list open closes it, and may land on that list's items
                self.assertLessEqual(count(p), 1)
                self.assertEqual(faults(p.wire, 'run', 'session'), [])
                q = Panel(self)  # the box's handler with the other list still up: only its own check keeps this one list
                q.panel_open()
                q.click(q.rect(boxes[first][0]), f'{first}_box')
                q.take(getattr(q.c, f'{second}_box')(0), f'{second} box')
                self.assertLessEqual(count(q), 1)

    def test_r_tab_and_a_list_while_a_slider_is_held(self):
        for key in ('menu_reset', 'menu_desktop_next', 'menu_field_next', 'menu_back', 'menu_inc', 'menu_delete', view.RESET_OP, 'zoom_in'):
            with self.subTest(key):
                p = Panel(self)
                p.panel_open()
                p.tilts(*TILTED)
                p.hold(0, 0.4)
                self.assertIsNotNone(p.c.state.menu_surface, 'the slider is held')
                p.key(key)
                p.key('resize_release')
                p.tick()
                self.assertEqual(faults(p.wire, 'run', 'session'), [])

    def test_a_panel_walk_through_real_clicks(self):
        seen = set()
        for seed in range(6):
            with self.subTest(seed=seed):
                p = Panel(self)
                acts = list(actions(p).items())
                rng = random.Random(seed)
                for _ in range(2500):
                    if p.c.state.typing: p.key(view.TYPING_OP)
                    elif not p.c.state.menu_open and rng.random() < 0.5: p.key('menu_back' if p.c.keys.open else 'menu_toggle')
                    if p.c.keys.open and rng.random() < 0.3: p.key(kb.PANEL_OP)
                    if p.c.state.zoomed and rng.random() < 0.3: p.key('zoom_out')
                    rng.choice(acts)[1]()
                self.assertEqual(faults(p.wire, 'run', 'session'), [], p.trace)
                seen |= p.seen
        want = {'picker', 'resolution_picker', 'background_picker', 'surface', 'busy', 'armed', 'wide', 'field brightness', 'names with a gap', 'placement glide',
                'desktops ease', '1 desktops', '2 desktops', '4 desktops', 'scale 150', 'scale 200', 'placement', 'roll', 'view', 'desktops', 'environment',
                'overview_pointer', 'move_handle', 'binds', 'help'} | {f'field {key}' for key in PLACEMENT_KEYS}
        self.assertEqual(want - seen, set())


class Numbered:
    """Frames from the real builders, numbered as they come: what a builder refuses never reaches a renderer."""
    def __init__(self):
        self.next, self.data, self.built, self.refused = 2, bytearray(), 0, 0

    def add(self, make, *args, **kw):
        try: frame = make('run', 'session', self.next, *args, **kw)
        except (ValueError, TypeError):
            self.refused += 1
            return
        self.data += frame
        self.next += 1
        self.built += 1


def edges(lo, hi):
    return sorted({lo, lo + 1, (lo + hi) // 2, hi - 1, hi})


@unittest.skipUnless(TOOL, 'needs the real decoder: the ctest control_wire')
class ContractTests(unittest.TestCase):
    def check(self, frames, at_least):
        self.assertGreaterEqual(frames.built, at_least)
        self.assertEqual(refused(frames.data, 'run', 'session')[:12], [])

    def placements(self, rng, count):
        return [{key: rng.choice(edges(*PLACEMENT_BOUNDS[key])) for key in PLACEMENT_KEYS} for _ in range(count)]

    def test_placement_menu_at_every_bound(self):
        rng = random.Random(1)
        frames = Numbered()
        widths, heights = edges(*geometry.CUSTOM_BOUNDS['width']), edges(*geometry.CUSTOM_BOUNDS['height'])
        fields = PLACEMENT_KEYS + (environment.BRIGHTNESS_FIELD,)
        for _ in range(12000):
            count = rng.randint(1, geometry.MAX_DESKTOPS)
            desktop = rng.randrange(count)
            menu = dict(open=rng.random() < 0.7, desktop=desktop, field=rng.choice(fields), placements=self.placements(rng, count))
            if rng.random() < 0.8: menu['names'] = sorted(rng.sample(range(1, geometry.MAX_DESKTOPS + 1), count))
            if rng.random() < 0.6: menu['surface'] = self.placements(rng, 1)[0]
            for key in ('wide', 'armed', 'busy'):
                if rng.random() < 0.5: menu[key] = rng.random() < 0.5
            if rng.random() < 0.7:
                menu['resolution'] = rng.choice(RESOLUTIONS) if rng.random() < 0.5 else (rng.choice(widths), rng.choice(heights))
                menu['resolution'] = list(menu['resolution'])
                menu['scale'] = rng.choice(view.SCALE_PERCENTS)
                menu['scales'] = sorted(rng.sample(view.SCALE_PERCENTS, rng.randint(0, len(view.SCALE_PERCENTS))))
            lists = rng.choice(((), ('picker',), ('resolution_picker',), ('background_picker',), ('picker', 'background_picker')))
            for key in lists: menu[key] = True
            if rng.random() < 0.6: menu['background'] = rng.choice(environment.NAMES)
            if rng.random() < 0.6: menu['brightness'] = rng.choice(edges(*environment.BRIGHTNESS_BOUNDS))
            frames.add(view.placement_menu_frame, menu)
        self.check(frames, 4000)

    def test_every_even_size_a_desktop_can_have(self):
        frames = Numbered()
        base = dict(open=True, desktop=0, field='x', placements=self.placements(random.Random(2), 1))
        for w in range(*geometry.CUSTOM_BOUNDS['width'], 2):
            frames.add(view.placement_menu_frame, dict(base, resolution=[w, 1080], scale=100, scales=[100]))
        for h in range(*geometry.CUSTOM_BOUNDS['height'], 2):
            frames.add(view.placement_menu_frame, dict(base, resolution=[1920, h], scale=100, scales=[100]))
        self.check(frames, 5000)

    def test_every_scale_set_a_size_can_offer(self):
        frames = Numbered()
        base = dict(open=True, desktop=0, field='x', placements=self.placements(random.Random(3), 1))
        for w, h in RESOLUTIONS:
            steps = [view.scale_percent(s) for s in geometry.valid_scales(w, h)]
            for step in steps: frames.add(view.placement_menu_frame, dict(base, resolution=[w, h], scale=step, scales=steps))
        self.check(frames, len(RESOLUTIONS))

    def test_placement_and_its_glide(self):
        rng = random.Random(4)
        frames = Numbered()
        for _ in range(3000):
            frames.add(view.placement_frame, self.placements(rng, rng.randint(1, geometry.MAX_DESKTOPS)), glide=rng.choice((None, 1, view.GLIDE_MS, view.MAX_GLIDE_MS)))
        self.check(frames, 3000)

    def test_view_with_every_flag(self):
        frames = Numbered()
        for selected, spacing, mode, zoomed, prediction, typing, flags in itertools.product(
                range(geometry.MAX_DESKTOPS), edges(0, view.MAX_SPACING), view.MODES, (False, True), (False, True), (False, True), ({}, {'center': True}, {'cut': True}, {'pan': True})):
            frames.add(view.view_frame, dict(zoomed=zoomed, selected=selected, spacing=spacing, mode=mode, cursor_follow=False, prediction=prediction, typing=typing, **flags))
        self.check(frames, 1000)

    def test_merged_stabilization_and_depth_strength_fields(self):
        from tools.native_spike import immersive
        frames = Numbered()
        for strength in edges(0, 100):
            frames.add(view.view_frame, dict(zoomed=True, selected=0, spacing=40,
                mode='anchor', cursor_follow=False, prediction=True, typing=False,
                stabilization=strength))
        for strength in edges(0, 200):
            params = immersive.wire_params(None)
            params['strengths'] = [strength] * geometry.MAX_DESKTOPS
            frames.add(immersive.renderer_frame, [True], True, params=params)
        self.check(frames, 10)

    def test_desktops_in_every_combination(self):
        frames = Numbered()
        names = [f'XR-{i}' for i in range(1, geometry.MAX_DESKTOPS + 1)]
        for count in range(1, len(names) + 1):
            for outputs in itertools.combinations(names, count):
                for renew_count in range(count + 1):
                    for renew in itertools.combinations(outputs, renew_count):
                        for ease in (False, True): frames.add(view.desktops_frame, list(outputs), list(renew), ease=ease)
        self.check(frames, 100)

    def test_roll_at_its_bounds(self):
        rng = random.Random(5)
        frames = Numbered()
        for _ in range(3000):
            hover = rng.randint(-1, geometry.MAX_DESKTOPS - 1)
            frames.add(fp.roll_frame, dict(roll=[rng.choice(edges(-fp.MAX_ROLL, fp.MAX_ROLL)) for _ in range(geometry.MAX_DESKTOPS)],
                                           icons=[rng.choice(fp.ROLL_ICONS) for _ in range(geometry.MAX_DESKTOPS)], hover=hover,
                                           side='none' if hover < 0 else rng.choice(fp.LEVEL_SEGMENTS), active=rng.random() < 0.5))
        self.check(frames, 3000)

    def test_the_handles_and_the_dot(self):
        rng = random.Random(6)
        frames = Numbered()
        limit = fp.MAX_POINTER_PX
        for _ in range(4000):
            frames.add(resize.handle_frame, dict(visible=rng.random() < 0.5, desktop=rng.randrange(geometry.MAX_DESKTOPS), corner=rng.choice(('tl', 'tr', 'bl', 'br')), active=rng.random() < 0.5))
            frames.add(resize.pill_frame, dict(visible=rng.random() < 0.5, desktop=rng.randrange(geometry.MAX_DESKTOPS), hover=rng.random() < 0.5, active=rng.random() < 0.5))
            pointer = dict(visible=rng.random() < 0.5, x=rng.choice(edges(-limit, limit)), y=rng.choice(edges(-limit, limit)), held=rng.random() < 0.5)
            if rng.random() < 0.5: pointer.update(az=rng.choice(edges(-fp.MAX_POINTER_AZ, fp.MAX_POINTER_AZ)), el=rng.choice(edges(-fp.MAX_POINTER_EL, fp.MAX_POINTER_EL)))
            if rng.random() < 0.3: pointer['drag'] = dict(w=rng.choice(edges(1, fp.MAX_GHOST_PX)), h=rng.choice(edges(1, fp.MAX_GHOST_PX)), target=rng.randint(-1, geometry.MAX_DESKTOPS - 1))
            frames.add(fp.overview_pointer_frame, pointer)
        self.check(frames, 8000)

    def test_the_background(self):
        frames = Numbered()
        for name in environment.NAMES:
            for brightness in edges(*environment.BRIGHTNESS_BOUNDS):
                frames.add(environment.renderer_frame, dict(background=name, last=environment.NAMES[1], brightness=brightness))
        self.check(frames, len(environment.NAMES))

    def test_the_edge_pan_at_its_bounds(self):
        rng = random.Random(7)
        frames = Numbered()
        for _ in range(2000):
            payload = {'active': True, 'from': rng.randrange(geometry.MAX_DESKTOPS), 'to': rng.randrange(geometry.MAX_DESKTOPS), 'side': rng.choice(edge_pan.SIDES)}
            payload.update({key: rng.choice((0, 1, 5000, 9999, 10000)) for key in ('exit', 'entry', 'peek', 'cross', 'turn', 'way', 'zoom')})
            frames.add(edge_pan.frame_bytes, payload)
        frames.add(edge_pan.frame_bytes, None)
        self.check(frames, 2000)

    def test_the_help_card_and_the_bind_panel_at_their_longest(self):
        # R and Shift+R in the key bind panel redraw both. Every bind on the longest keys, every note in use, the frame still fits.
        long_keys = {op: 'CTRL + ALT + SHIFT + SUPER + Page_Down' for op, _ in kb.KeyMap().items()}
        desktop = kb.DesktopBinds.from_text(''.join(f'bind\n\tmodmask: 77\n\tsubmap: \n\tkey: {chord.split(" + ")[-1]}\n\tkeycode: 0\n\tcatchall: false\n\tdescription: {"x" * 200}\n\tdispatcher: exec\n\targ: x\n\n'
                                                    for chord in long_keys.values()))
        rig = Rig(self)
        rig.c.keys = kb.BindControl(rig.c, kb.KeyMap(long_keys), rig.dir / 'long.json', client=None, bind_args=ARGS_FOR_KEYS, desktop=desktop)
        rig.c.keys.open = True
        frames = bytearray()
        for _ in range(3):
            frames += rig.c.keys._frame()
            frames += rig.c._help_toggle()
        self.assertTrue(frames)
        self.assertEqual(faults(frames, 'run', 'session'), [])
        self.assertLess(max(map(len, bytes(frames).splitlines())), 16384)


ARGS_FOR_KEYS = ('/usr/bin/python3', '/opt/x/xr_control.py', '/run/user/1000/x/control.sock', 'run', 'session')


class ThreadTests(unittest.TestCase):
    """An id taken off the loop can repeat one (`next_id += 1` is not atomic), and a repeat is a replay. So none is."""
    def test_every_id_is_taken_on_the_supervisors_thread(self):
        taken = []

        class Watched(view.ViewControl):
            @property
            def next_id(self): return self._next_id

            @next_id.setter
            def next_id(self, value):
                taken.append(threading.current_thread().name)
                self._next_id = value

        def watched(*args, **kw):
            control = Watched(*args, **kw, menu_binds=lambda on: None, typing_binds=lambda on: None, on_reload=lambda: None)
            taken.clear()  # (the constructor's, on this thread)
            return control
        original, view.ViewControl = view.ViewControl, watched
        self.addCleanup(setattr, view, 'ViewControl', original)
        s = Session(self, events=True)
        view.ViewControl = original
        loop = s.thread.name
        stop = threading.Event()

        def sampler():  # as the real one: the pointer is told from its own thread, all the time
            x = 0.0
            while not stop.is_set():
                x = (x + 7) % 1900
                s.pointer.feed((2560 + x, 540.0), (1.0, 0.0), s.outputs(), s.pointer.want[0])
                s.pointer.set_pan(None)
                time.sleep(0.001)
        thread = threading.Thread(target=sampler, name='sampler', daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(stop.set)
        for _ in range(3):
            s.keys('menu_toggle', 'menu_inc', 'menu_reset', 'menu_field_next', 'menu_back')
            s.keys(view.TYPING_OP, view.TYPING_OP, kb.PANEL_OP, kb.PANEL_OP)
            s.reload()  # the worker threads: a reload's rebinding, and the key panel's
        stop.set()
        thread.join(2)
        s.clean()
        self.assertGreater(len(taken), 10)
        self.assertEqual(set(taken), {loop})


if __name__ == '__main__':
    unittest.main()
