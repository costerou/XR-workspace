"""R in the placement panel ended the session (Cody, 2026-11-04 03:21). `level()` took a request id, `_menu_update` took later
ones, and the roll frame went out last: the renderer read an id it had already passed as a replay and stopped.

Every test here looks at what reaches the renderer rather than at what a handler returned, through control_wire.faults:
ids rising, and, under the ctest control_wire, the real C++ decoder as well.
  * SessionTests: the real _launch, Supervisor.run, PrivateServer and xr_control.send. Only the renderer process is stood in,
    on the real socket pair, sending the real status datagrams.
  * WalkTests: every op the run takes, in random order, through the real ViewControl, pointer, key bind panel, VR video and
    DesktopManager. This is what guards the whole class: any handler that emits frames out of order, any payload the
    renderer does not take.

Offline only: no compositor, GPU or glasses. A stand-in renderer never draws, so the real one's half is ctest capture_e2e.
"""
import json
import os
from pathlib import Path
import random
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, immersive, keybinds as kb, profiles, resize, supervisor, unified_cursor as uc, video, view, xr_control
from tools.native_spike.geometry import PLACEMENT_KEYS
from tools.native_spike.ipc import PrivateServer
from tools.native_spike.profiles import LayoutStore

from control_wire import faults, ids, problems
from test_desktops import Run as DesktopRun
from test_keybinds import ARGS, FakeClient
from test_layout_reset import SupervisorStandIn
from test_video import info

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
GEOMETRY = FIXTURE['geometry']['line'].encode() + b'\n'
G = fp.parse_overview_geometry(json.loads(GEOMETRY)['overview_geometry'])
NAMES = ['XR-1', 'XR-2', 'XR-3']
RUN, SESSION = 'run1', 'session1'
MODEL = 'luma-ultra'
CODE = {name: code for code, name in kb.KEYCODES.items()}
OUTPUT = {'width': 1920, 'height': 1080}
TILTED = [120, -80, 40]  # 1/100 degree: head tracking always measures something, so R always has a roll to change


class Renderer:
    """The renderer process (popen for _launch): takes everything on the control socket, beats, and sends status datagrams."""
    def __init__(self, argv, **_):
        args = dict(zip(argv[1::2], argv[2::2]))
        self.control = socket.socket(fileno=os.dup(int(args['--supervised-fd'])))
        self.status = socket.socket(fileno=os.dup(int(args['--status-fd'])))
        self.rc = None
        self.wire = bytearray()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.control.settimeout(0.02)
        while self.rc is None:
            try: data = self.control.recv(65536)
            except TimeoutError: data = None
            except OSError: data = b''
            if data == b'': return
            if data:
                with self.lock: self.wire += data
            try: self.control.send(b'H')
            except OSError: return

    def received(self):
        with self.lock: return bytes(self.wire)

    def poll(self): return self.rc
    def terminate(self): self.rc = 0
    def kill(self): self.rc = -9


class Client:
    """All _launch wants of the Hyprland client: who holds the other end of the Wayland socket."""
    _peer = (os.getpid(), os.getuid(), os.getgid())


class Session:
    """A run with everything real but the compositor and the renderer process, keys arriving as the binds send them.
    `events`: Hyprland's event socket too, so a config reload can start its worker threads (reload)."""
    def __init__(self, test, events=False):
        self.test = test
        tmp = tempfile.TemporaryDirectory(); test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.server = PrivateServer(self.dir, RUN, SESSION)
        self.path = f'/proc/self/fd/{self.server.directory_fd}/control.sock'
        self.control = view.ViewControl(view.ViewState(list(NAMES)), RUN, SESSION, lambda name: None, LayoutStore(self.dir / 'layout.json'), self.server)
        self.pointer = self.control.overview_pointer = uc.UnifiedPointer(self.control)
        self.control.wake = self.pointer.wake_reader
        self.control.keys = kb.BindControl(self.control, kb.KeyMap(), self.dir / 'keybinds.json', client=FakeClient(), bind_args=ARGS)
        self.control.keys.installed()
        self.video = video.VideoControl(self.control, threads=False, probe=lambda fd: info())
        self.hyprland = None
        if events:
            self.hyprland, self.control.events = socket.socketpair()
            self.control.events.setblocking(False)
            test.addCleanup(self.hyprland.close); test.addCleanup(self.control.events.close)
        self.renderer = None
        self.report = {}
        self.result = {}
        wayland, peer = socket.socketpair()
        test.addCleanup(peer.close)

        def popen(argv, **kw):
            self.renderer = Renderer(argv, **kw)
            return self.renderer

        def run():
            self.result['rc'] = supervisor._launch(Client, RUN, SESSION, 'DP-2', wayland, None, popen=popen, capture=list(NAMES),
                                                   control=self.video, report=self.report)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)
        self.status()
        self.renderer.status.send(GEOMETRY)  # the first thing a renderer reports
        self.tilts(TILTED)

    def close(self):
        if self.renderer is not None: self.renderer.rc = 0
        self.thread.join(5)
        if self.renderer is not None:
            self.renderer.thread.join(5)
            self.renderer.control.close(); self.renderer.status.close()
        self.control.keys.stop()
        self.control.join_focus(2)
        self.pointer.close()
        self.server.close()

    def status(self):
        """A status reply comes from the supervisor's loop, so everything sent before it is done."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5); s.connect(self.path)
            s.sendall(xr_control.control_frame(RUN, SESSION, 'status', {}, time.monotonic_ns()).encode())
            reply = b''
            while not reply.endswith(b'\n'):
                chunk = s.recv(65536)
                if not chunk: raise AssertionError(f'the run stopped answering: {self.report}')
                reply += chunk
        return json.loads(reply)

    def settle(self):
        """Twice: the loop's poll runs after the events of a pass, so the second reply comes after a whole pass."""
        self.status()
        return self.status()

    def tilts(self, tilts):
        self.renderer.status.send(json.dumps({'roll_geometry': {'tilt': tilts}}).encode() + b'\n')
        self.settle()

    def reload(self):
        self.hyprland.send(b'configreloaded>>\n')
        self.settle()

    def outputs(self):
        return resize.outputs_from_monitors([{'name': n, 'x': 2560 + 1920 * i, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}
                                             for i, n in enumerate(NAMES)], NAMES)

    def keys(self, *ops):
        """Each as its bind sends it, one at a time."""
        for op in ops:
            xr_control.send(self.path, RUN, SESSION, op, {})
            self.settle()

    def press(self, mods, code):
        """A key while the key bind panel is open (its key subscription)."""
        xr_control.send(self.path, RUN, SESSION, kb.WIRE_OP, {'mods': mods, 'code': code})
        self.settle()

    def wire(self):
        self.settle()
        for _ in range(100):  # the bytes are on their way to the stand-in
            data = self.renderer.received()
            if data.endswith(b'\n'): return data
            time.sleep(0.005)
        raise AssertionError('a frame was cut short')

    def sent(self, op):
        return [f['payload'] for f in map(json.loads, self.wire().splitlines()) if f['op'] == op]

    def clean(self):
        """Whatever was sent, the renderer takes all of it, and the run is still up."""
        self.test.assertEqual(faults(self.wire(), RUN, SESSION), [])
        self.test.assertIsNone(self.renderer.rc)
        self.test.assertEqual(self.report, {})


class RequestIdClockTests(unittest.TestCase):
    def test_the_key_helper_stamps_the_monotonic_clock(self):
        # The run refuses an id that is not above the last one, so a wall clock stepped back would drop every key.
        seen = []
        with mock.patch.object(xr_control, 'control_frame', lambda *a: seen.append(a[-1]) or 'x'), \
             mock.patch.object(time, 'monotonic_ns', lambda: 7), mock.patch.object(time, 'time_ns', lambda: 9):
            try: xr_control.send('/nonexistent/control.sock', RUN, SESSION, 'zoom_in', {})
            except OSError: pass
        self.assertEqual(seen, [7])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.s = Session(self)

    def test_r_on_a_moved_desktop_the_way_it_ended_a_session(self):
        s = self.s
        s.keys('menu_toggle', 'menu_inc', 'menu_inc_big')
        self.assertNotEqual(s.status()['placements'][0]['x'], 0)
        s.keys('menu_reset')
        s.clean()
        self.assertEqual(s.status()['placements'][0]['x'], 0)
        self.assertEqual(s.sent('roll')[-1]['roll'][0], -TILTED[0])  # and it did level: the frame was not just lost

    def test_r_with_every_frame_it_can_send(self):
        s = self.s
        for setup in (('menu_toggle', 'menu_inc'), ('menu_desktop_next', 'menu_inc'), ('menu_field_next', 'menu_dec_big')):
            with self.subTest(setup):
                s.tilts([t + 10 for t in TILTED])  # a measured change: there is a roll to send
                s.keys(*setup, 'menu_reset')
                s.clean()

    def test_r_with_a_list_open_on_a_flat_desktop(self):
        # No placement to change: the menu frame alone (the list closing) came after the roll.
        s = self.s
        s.keys('menu_toggle')
        s.control.state.background_picker = True
        s.keys('menu_reset')
        self.assertFalse(s.sent('placement_menu')[-1].get('background_picker'))
        s.clean()

    def test_r_on_each_desktop_in_turn(self):
        s = self.s
        s.keys('menu_toggle')
        for i in range(len(NAMES)):
            with self.subTest(desktop=i):
                s.keys('menu_inc', 'menu_reset')
                s.tilts([t + i + 1 for t in TILTED])
                s.keys('menu_desktop_next')
        s.clean()

    def test_r_again_and_again(self):
        s = self.s
        s.keys('menu_toggle', 'menu_inc', 'menu_reset', 'menu_reset', 'menu_reset')
        s.clean()

    def test_r_while_the_desktop_is_settling(self):
        # The renderer keeps reporting tilts after a reset moved the desktop (level: LEVEL_SETTLE_S), each levelled again.
        s = self.s
        s.keys('menu_toggle', 'menu_inc', 'menu_reset')
        for tilt in (150, 90, -40):
            s.tilts([tilt, -80, 40])
            s.keys('menu_inc', 'menu_reset')
        s.clean()

    def test_r_after_coming_back_from_maximized(self):
        s = self.s
        s.keys('zoom_in', 'menu_toggle')  # the panel opens from maximized: the overview first
        self.assertFalse(s.status()['view']['zoomed'])
        s.keys('menu_inc', 'menu_reset')
        s.clean()

    def test_a_late_r_while_maximized_does_nothing(self):
        # Maximizing collapses the panel and its binds go off the loop, so a key can still arrive.
        s = self.s
        s.keys('menu_toggle', 'menu_inc', 'zoom_in')
        before = s.status()['placements']
        s.keys('menu_reset')
        self.assertEqual(s.status()['placements'], before)
        s.clean()

    def test_r_after_an_edge_pan_switch(self):
        s = self.s
        s.keys('zoom_in')
        plan = s.pointer.pan_plan(1, 'right', 0.5, OUTPUT)
        self.assertIsNotNone(plan)
        for travel in (5, plan.total_px / 2, plan.total_px):
            s.pointer.set_pan(plan.frame(travel))
            s.settle()
        s.pointer.request_switch(0, 1, panned=True)
        s.settle()
        self.assertEqual(s.status()['view']['selected'], 1)
        self.assertTrue(s.sent('view')[-1].get('pan'))
        s.keys('menu_toggle', 'menu_inc', 'menu_reset')
        s.clean()

    def test_r_during_a_pan(self):
        s = self.s
        s.keys('zoom_in')
        plan = s.pointer.pan_plan(1, 'right', 0.5, OUTPUT)
        s.pointer.set_pan(plan.frame(plan.total_px / 3))
        s.settle()
        s.keys('menu_reset')                                 # a late key: maximized, no panel
        s.keys('menu_toggle', 'menu_inc')                    # the panel takes the view back to the overview
        s.pointer.set_pan(plan.frame(plan.total_px / 2))     # and the sampler has not heard yet
        s.keys('menu_reset')
        s.pointer.set_pan(None)
        s.clean()

    def test_ctrl_alt_r_after_r(self):
        s = self.s
        s.keys('menu_toggle', 'menu_inc', 'menu_reset', 'menu_inc', view.RESET_OP, view.RESET_OP)
        s.clean()

    def test_r_and_shift_r_in_the_key_bind_panel(self):
        s = self.s
        s.keys(kb.PANEL_OP)
        self.assertTrue(s.control.keys.open)
        s.press(0, CODE['down'])
        s.press(0, kb.ENTER_CODES[0] if isinstance(kb.ENTER_CODES, tuple) else min(kb.ENTER_CODES))  # a change to take back
        s.press(12, CODE['J'])  # Ctrl+Alt+J
        s.press(0, CODE['R'])
        s.press(0, CODE['R'])   # already the default
        s.press(1, CODE['R'])   # Shift+R arms
        s.press(1, CODE['R'])   # and resets
        s.clean()

    def test_shift_r_in_the_key_bind_panel_with_the_placement_panel_open(self):
        s = self.s
        s.keys('menu_toggle', 'menu_inc', kb.PANEL_OP)  # the bind panel takes over: the placement panel collapses
        s.press(1, CODE['R'])
        s.press(1, CODE['R'])
        s.keys(kb.PANEL_OP, 'menu_toggle', 'menu_reset')
        s.clean()


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


# What the walk presses. The view's own ops, then the ones the control socket takes beside them.
KEYS = sorted(set(view.USER_OPS) | set(view.MENU_OPS) | set(view.HELP_PAGE_OPS) |
              {view.RESET_OP, view.TYPING_OP, view.ENVIRONMENT_OP, view.PLAYBACK_MENU_OP, kb.PANEL_OP, 'resize_press', 'resize_release'} |
              set(view.SUPER_OPS) | {o for o in video.VIDEO_OPS if o != 'video_open'})
ROOM_KEYS = ('menu_reset', 'menu_reset', 'menu_inc', 'menu_dec_big', 'menu_field_next', 'menu_desktop_next', 'menu_add', 'menu_delete', 'menu_ultrawide',
             'resize_press', 'resize_release', view.RESET_OP)
SEEDS = 8


class Rig:
    """The whole control stack but the Supervisor's loop, on a fake clock: real ViewControl, pointer, key bind panel, VR video and
    DesktopManager. `test` owns the cleanups."""
    def __init__(self, test, desktops=None):
        self.addCleanup = test.addCleanup
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clock = Clock()
        self.run = run = DesktopRun(test, desktops)
        store = profiles.PlacementStore(self.dir / 'profiles.json', MODEL)
        settings = profiles.ProfileStore(self.dir / 'profiles.json', MODEL)
        state = view.ViewState(run.manager.names())
        self.colour = 0x336699
        self.c = view.ViewControl(state, 'run', 'session', lambda name: None, settings, None, clock=self.clock, placements=store,
                                  border=lambda: {'int': 0xff000000 | self.colour})
        self.addCleanup(self.c.join_focus, 2)
        self.c.desktops, run.manager.store = run.manager, store
        run.manager.move_windows = lambda source, target: None
        state.wide, state.sizes = run.manager.wide(), run.manager.desktops()
        self.c.notify = lambda text: None
        self.c.attach_supervisor(SupervisorStandIn())
        self.p = self.c.overview_pointer = uc.UnifiedPointer(self.c, clock=self.clock)
        self.addCleanup(self.p.close)
        self.c.keys = kb.BindControl(self.c, kb.KeyMap(), self.dir / 'keybinds.json', client=FakeClient(), bind_args=ARGS)
        self.c.keys.installed()
        self.addCleanup(self.c.keys.stop)
        self.v = video.VideoControl(self.c, threads=False, probe=lambda fd: info(), clock=self.clock)
        self.addCleanup(self.v.close)
        self.c.set_overview_geometry(G)
        self.film = self.dir / 'film_360.mp4'
        self.film.write_bytes(b'not really a film')
        self.cursor = (2560 + 960, 540)

    def handles(self):
        """Canvas points worth aiming at: what a click does something on."""
        s = self.c.state
        out = []
        for i, tile in enumerate(G['tiles'][:len(s.placements)]):
            x, y, w, h = fp.pill_rect(tile)
            out.append(fp.project_glass(G, tile, s.placements[i], x + w / 2, y + h / 2))
            bx, by, bw, bh = fp.level_bar_rect(tile)
            for share in (1 / 6, 1 / 2, 5 / 6): out.append(fp.project(G, tile, s.placements[i], bx + bw * share, by + bh / 2))
            if s.menu_open and s.menu_desktop == i:
                px, py, _, _ = fp.panel_rect(tile)
                for row in range(len(PLACEMENT_KEYS)):
                    out.append(fp.project_glass(G, tile, s.placements[i], px + fp.PANEL_KNOB_LO + 40 * row, py + fp.PANEL_ROWS_TOP + fp.PANEL_ROW_H * (row + 0.5)))
        return out

    def leave(self, rng):
        """The way out of a state that takes most keys away, some of the time. Without this a walk spends itself in a video or
        in typing mode, which is how a clean result comes to prove nothing (test_the_walk_gets_everywhere keeps it honest)."""
        s = self.c.state
        if self.v.active and rng.random() < 0.25: return 'video_exit'
        if s.typing and rng.random() < 0.4: return view.TYPING_OP
        if self.c.keys.open and rng.random() < 0.15: return kb.PANEL_OP
        if s.zoomed and rng.random() < 0.08: return 'zoom_out'
        if not s.menu_open and not s.zoomed and not s.typing and not self.c.video_active and rng.random() < 0.3: return 'menu_toggle'
        return None

    def step(self, rng):
        """One thing happening to the run. Its bytes and what it was."""
        out = self.leave(rng)
        if out is not None: return self.v.on_message({'op': out, 'payload': {}}), out
        roll = rng.random()
        if roll < 0.40:
            op = rng.choice(ROOM_KEYS if rng.random() < 0.6 else KEYS)
            return self.v.on_message({'op': op, 'payload': {}}), op
        if roll < 0.45:
            self.clock.now += rng.choice((0.1, 0.7, 3, 11, 31))
            return self.v.poll(), 'poll'
        if roll < 0.52:
            tilts = [rng.randint(-900, 900) if rng.random() < 0.9 else None for _ in self.c.state.names]
            self.c.set_roll_geometry(tilts)
            return b'', 'tilts'
        if roll < 0.58 and self.c.keys.open:
            code = rng.choice(sorted(kb.KEYCODES))
            return self.v.on_message({'op': kb.WIRE_OP, 'payload': {'mods': rng.choice((0, 0, 1, 12)), 'code': code}}), f'key {code}'
        if roll < 0.59:
            return self.v.on_message({'op': 'video_open', 'payload': {'path': str(self.film)}}), 'video_open'
        if roll < 0.595:
            return self.v.initial_frame(), 'a new renderer'  # (a USB drop, survive.py: the ids carry on, and the new one starts anywhere)
        if roll < 0.60:
            self.colour = rng.randrange(1 << 24)  # a theme switch: the border colour comes back with the reload
            return self.v.on_events(b'configreloaded>>\n'), 'reload'
        if roll < 0.62 and self.c.state.menu_open:
            self.c.state.background_picker = True  # (what a click on its box does)
            return b'', 'list'
        if roll < 0.635:  # the settings panel: absolute placements for every desktop, as given
            desktops = [dict(p, x=rng.randint(-600, 600), distance=rng.randint(40, 160)) for p in self.c.state.placements]
            return self.v.on_message({'op': 'placement', 'payload': {'desktops': desktops}}), 'placement'
        if roll < 0.64:
            background = rng.choice(('none', 'grid', 'smoke'))
            return self.v.on_message({'op': view.ENVIRONMENT_SET_OP, 'payload': {'background': background, 'brightness': rng.randint(20, 100)}}), 'environment'
        if roll < 0.66 and self.c.state.menu_open:
            s = self.c.state  # a slider held and moved, then let go (what the pointer does on one)
            held = s.menu_surface is None
            out = self.c.pill_slide(s.menu_desktop, rng.choice(PLACEMENT_KEYS), rng.random(), surface=dict(s.placements[s.menu_desktop]) if held else None)
            return out if rng.random() < 0.7 else out + self.c.pill_release(), 'slider'
        if roll < 0.70:
            home, target = self.c.state.selected, rng.randrange(len(self.c.state.names))
            if target != home:
                plan = self.p.pan_plan(target, 'right', rng.random(), OUTPUT)
                self.p.set_pan(plan.frame(rng.random() * plan.total_px) if plan is not None else None)
                if rng.random() < 0.3: self.p.request_switch(home, target, panned=plan is not None)
            return self.v.poll(), 'pan'
        if roll < 0.84 and self.p.mode == 'free' and self.p.pointer is not None:
            aim = rng.choice(self.handles())
            self.clock.now += 0.004
            self.p.feed(self.cursor, ((aim[0] - self.p.pointer[0]) / self.p.gain, (aim[1] - self.p.pointer[1]) / self.p.gain), self.outputs_now(), self.p.want[0])
            return self.v.poll(), 'aim'
        self.clock.now += 0.004
        self.cursor = (rng.uniform(2560, 2560 + 1920 * len(self.c.state.names) - 1), rng.choice((0.0, rng.uniform(0, 1079), 1079.0)))
        self.p.feed(self.cursor, (0.0, 0.0), self.outputs_now(), self.p.want[0])
        return self.v.poll(), 'cursor'

    def outputs_now(self):
        names = list(self.c.state.names)
        return resize.outputs_from_monitors([{'name': n, 'x': 2560 + 1920 * i, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}
                                             for i, n in enumerate(names)], names)

    def where(self):
        s = self.c.state
        names = {'overview panel': s.menu_open, 'maximized': s.zoomed, 'typing': s.typing, 'video': self.c.video_active,
                 'key panel': self.c.keys.open, 'four desktops': len(s.names) == 4, 'one desktop': len(s.names) == 1,
                 'list open': s.background_picker or s.scale_picker or s.resolution_picker, 'held slider': s.menu_surface is not None}
        return {name for name, on in names.items() if on}


class WalkTests(unittest.TestCase):
    """Random, reproducible: the seed and step of a failure are in its message."""
    STEPS = 2500

    def walk(self, seed, seen=None):
        rig = Rig(self)
        rng = random.Random(seed)
        wire, last = bytearray(), 0
        for n in range(self.STEPS):
            out, what = rig.step(rng)
            data = bytes(out) if out else b''
            self.assertEqual(problems(data, last), [], f'seed {seed} step {n}: {what}')
            self.assertTrue(rig.v.controls or not rig.v.menu, f'seed {seed} step {n}: {what}: the quality menu is on the card')
            if data: last = ids(data)[-1]
            wire += data
            close = getattr(out, 'close_fds', None)
            if close is not None: close()
            if seen is not None: seen.update({f"frame {f['op']}" for f in map(json.loads, data.splitlines())} | rig.where())
        return wire

    def test_every_op_in_any_order(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                self.assertEqual(faults(self.walk(seed), 'run', 'session'), [], f'seed {seed}')

    def test_the_walk_gets_everywhere(self):
        seen = set()
        for seed in range(4): self.walk(seed, seen)
        want = {f'frame {op}' for op in ('view', 'placement', 'placement_menu', 'roll', 'edge_pan', 'help', 'binds', 'video', 'desktops',
                                         'environment', 'overview_pointer', 'resize_handle', 'move_handle', 'recenter', 'style')}
        want |= {'overview panel', 'maximized', 'typing', 'video', 'key panel', 'four desktops', 'list open', 'held slider'}
        self.assertEqual(want - seen, set())


class ImmersiveTests(unittest.TestCase):
    """3D's frames. One the renderer refused would be dropped, not fatal, so a mismatch between the two ends would not stop anything: 3D
    would simply never draw, and the first anyone knew would be in the glasses."""
    def control(self, setting=None):
        store = mock.Mock(); store.load.return_value = view.DEFAULT_SETTINGS
        return view.ViewControl(view.ViewState(NAMES, immersive=setting), RUN, SESSION, lambda name: None, store, mock.Mock())

    def test_every_look_gets_through(self):
        c = self.control()
        wire = bytearray(c.initial_frame())
        for preset in immersive.PRESETS:
            for strength in (0, *immersive.STRENGTHS, immersive.STRENGTH_MAX):
                for convergence in (0, immersive.PRESETS[preset]['convergence'], 100):
                    frame = c.set_immersive({**immersive.DEFAULTS, 'global': True, 'preset': preset, 'strength': strength, 'convergence': convergence})
                    wire += frame
        self.assertEqual(wire.count(b'"op":"immersive"'), len(immersive.PRESETS) * (2 + len(immersive.STRENGTHS)) * 3)
        self.assertEqual(faults(wire, RUN, SESSION), [])

    def test_a_new_renderer_s_first_frames_get_through(self):
        c = self.control({**immersive.DEFAULTS, 'global': True, 'strength': 150})
        self.assertEqual(faults(c.initial_frame(), RUN, SESSION), [])
        self.assertEqual(faults(c.set_immersive({**immersive.DEFAULTS, 'global': True}), RUN, SESSION), [])


if __name__ == '__main__':
    unittest.main()
