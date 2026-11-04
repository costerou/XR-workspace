"""In-session placement from the keyboard: contract tests written ahead of the placement menu, kept for the glass pills.

Since feature/glass-pills (2026-11-02) the head-locked menu card is gone: the same state and ops drive a glass
pill under each desktop, and 'placement_menu' {open, desktop, ...} means "that desktop's pill is expanded into its
placement panel". The keyboard contract below is unchanged; the pointer side (pill clicks, sliders, click outside) is
tested in test_glass_pills.py.

SHARED CONTRACT (feature/placement-menu, base f593574)
  * The Python side owns menu state {open, desktop 0..2, field, placements[3]} and sends it to the renderer as the view
    op 'placement_menu' with exactly that payload whenever it changes (open=false hides the overlay).
  * Hotkey actions: menu_toggle, menu_desktop_next/prev, menu_field_next/prev, menu_inc, menu_dec, menu_inc_big,
    menu_dec_big, menu_reset. They reach the supervisor like every other bind: xr_control.py -> run control socket ->
    ViewControl.on_message.
  * menu_toggle is bound to Ctrl+Alt+L (Ctrl+Alt+P is pose prediction), agreed by all three threads.
  * Changes apply live through the existing 'placement' renderer frame, inside geometry.PLACEMENT_BOUNDS, and are saved
    to the glasses profile through PlacementStore, debounced and on close.

Only the contract and existing public API are used here (ViewControl, ViewState, decode_control, xr_control, BINDS,
install_binds_lua, PlacementStore). Step sizes, the initial desktop/field and the in-menu chords are implementation
choices and are measured or looked up; only the toggle chord is fixed by the contract.

Tests that need MENU-WIRING skip until it lands (feature-detected: the control socket accepts 'menu_toggle'). The
sentinel test `test_menu_wiring_landed` is an expected failure; once wiring lands it becomes an unexpected success and
fails the suite on purpose, so the integrator removes the decorator and the skipped tests are known to be running.
Renderer decoding of 'placement_menu' is tested in spike/tests/placement_menu_ipc_test.cpp against the same fixture.

Offline only: no compositor, renderer binary or glasses.
"""
import copy
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tools.native_spike import geometry as g, profiles, view
from tools.native_spike.ipc import decode_control, PrivateServer

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'placement_menu_payloads.json'
NAMES = ['XR-1', 'XR-2', 'XR-3']
TILTED = {'x': -20, 'y': 10, 'distance': 120, 'curvature': 40}
MENU_FIELDS = ('x', 'y', 'distance', 'curvature')  # contract order = menu order
MENU_ACTIONS = ('menu_toggle', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev',
                'menu_inc', 'menu_dec', 'menu_inc_big', 'menu_dec_big', 'menu_reset', 'menu_ultrawide', 'menu_add', 'menu_delete')
MENU_PAYLOAD_KEYS = {'open', 'desktop', 'field', 'placements'}
MENU_TOGGLE_CHORD, MENU_TOGGLE_LABEL = 'CTRL + ALT + L', 'Ctrl+Alt+L'  # contract; Ctrl+Alt+P is pose prediction
# Baselines at f593574: the menu must not widen any of these.
BASE_CONTROL_OPS = {'status', 'start', 'stop', 'select', 'recover', 'zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up',
                    'spacing_down', 'recenter', 'mode_next', 'prediction', 'help', 'placement'}
BASE_CAPABILITIES = ['zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next',
                     'prediction', 'help']  # cursor_follow removed 2026-11-02
BASE_QUERY = {'monitors', 'workspaces', 'clients', 'activewindow', 'getoption', 'cursorpos'}
BASE_PANEL_KEYS = {'schema', 'gate', 'run_id', 'instance', 'output', 'recovery_output', 'model', 'allow', 'capture',
                   'tracking', 'mode_switch', 'plugin', 'renderer', 'desktops', 'input', 'sdk'}
BASE_PROFILE_KEYS = {'desktops', 'environment', 'fallback', 'mode', 'mode_switch', 'placements', 'previous_setup', 'prediction',
                     'rolls', 'spacing', 'tracking', 'tracking_driver_binary', 'video_quality'}  # cursor_switch_animate (Ctrl+Alt+A) added and removed 2026-11-03
# rolls: each desktop's levelling roll, saved with the monitor setup since remember-layout-reset-bind (2026-11-03);
# previous_setup: the layout Ctrl+Alt+R last replaced, a one-step backup
# video_quality: the VR video quality menu (quality.py, 2026-11-03); environment: the background (environment.py, 2026-11-03)
# Real-shaped bind arguments (2026-11-01): the live controller's checkout path, a token_hex(16) run id (state.py) and a
# sha256-hex session id (HyprlandClient.verified_session).
LIVE_BIND_ARGS = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-per-desktop-transform/tools/native_spike/xr_control.py',
                  '/run/user/1000/xr-workspace-spike/' + 'a' * 32 + '/control.sock', 'a' * 32, 'e' * 64)


def wire(op, payload=None, request_id=5):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': 'run', 'session': 'session', 'op': op,
                        'payload': {} if payload is None else payload}) + '\n').encode()


def _control_accepts(op):
    try: return decode_control(wire(op))['op'] == op
    except ValueError: return False


WIRING = _control_accepts('menu_toggle')
RENDERER = 'placement_menu' in (ROOT / 'spike/src/ipc.cpp').read_text()
NEEDS_WIRING = unittest.skipUnless(WIRING, 'needs MENU-WIRING: the control socket does not accept menu_* hotkey ops yet')


MENU_ROW_KEYS = {'names', 'wide', 'armed', 'busy'}  # the open panel's desktop row (desktops.py), all optional
MENU_SCALE_KEYS = {'scale', 'scales', 'picker'}       # its Scale dropdown, all optional
MENU_BACKGROUND_KEYS = {'background', 'brightness', 'background_picker'}  # its background rows (environment.py), all optional
SCALE_STEPS = (100, 125, 150, 175, 200, 250, 300)     # geometry.SCALES in percent
MENU_RESOLUTION_KEYS = {'resolution', 'resolution_picker'}  # its Resolution dropdown, all optional
RESOLUTION_BOUNDS = ((640, 7680), (480, 4320))        # width, height: even (geometry.CUSTOM_BOUNDS)


def check_menu_payload(p):
    """Reference validator for the 'placement_menu' payload, straight from the shared contract. Since the glass pills an
    open panel may also carry 'surface' (the placement it is drawn on while the free pointer holds one of its sliders).
    Since desktops can be added and removed in XR: one placement per desktop (1..MAX_DESKTOPS), and the desktop row:
    'names' (each desktop's XR-n number, increasing), 'wide', 'armed', 'busy'. Then the Scale dropdown ('scale', 'scales',
    'picker'), the Resolution dropdown above it ('resolution': [width, height]; 'resolution_picker') and the Background one
    ('background', 'brightness', 'background_picker'), at most one list open, only on an open panel."""
    if not isinstance(p, dict) or not MENU_PAYLOAD_KEYS <= set(p) or \
            set(p) - MENU_PAYLOAD_KEYS - {'surface'} - MENU_ROW_KEYS - MENU_SCALE_KEYS - MENU_RESOLUTION_KEYS - MENU_BACKGROUND_KEYS:
        raise ValueError('payload must have exactly open, desktop, field, placements (and the optional surface and row keys)')
    if type(p['open']) is not bool: raise ValueError('open must be a bool')
    g.validate_placements(p['placements'])
    n = len(p['placements'])
    if type(p['desktop']) is not int or not 0 <= p['desktop'] < n: raise ValueError('desktop must index the placements')
    if not isinstance(p['field'], str) or p['field'] not in MENU_FIELDS + ('brightness',): raise ValueError('unknown field')
    if 'surface' in p:
        if p['open'] is not True: raise ValueError('surface only with an open panel')
        g.validate_placement(p['surface'])
    if 'names' in p:
        names = p['names']
        if (not isinstance(names, list) or len(names) != n or any(type(x) is not int or not 1 <= x <= g.MAX_DESKTOPS for x in names)
                or names != sorted(set(names))): raise ValueError('names must be increasing XR numbers, one per desktop')
    for key in ('wide', 'armed', 'busy', 'picker', 'resolution_picker', 'background_picker'):
        if key in p and type(p[key]) is not bool: raise ValueError(f'{key} must be a bool')
    if p.get('picker') and p['open'] is not True: raise ValueError('the Scale list opens only on an open panel')
    lists = [bool(p.get(k)) for k in ('picker', 'resolution_picker', 'background_picker')]
    if any(lists) and p['open'] is not True: raise ValueError('a dropdown list opens only on an open panel')
    if sum(lists) > 1: raise ValueError('one dropdown list at a time')
    if 'resolution' in p:
        r = p['resolution']
        if not isinstance(r, list) or len(r) != 2 or any(type(v) is not int or v % 2 or not lo <= v <= hi
                                                         for v, (lo, hi) in zip(r, RESOLUTION_BOUNDS)):
            raise ValueError('resolution must be an even [width, height] within the desktop bounds')
    if 'background' in p and p['background'] not in ('none', 'grid', 'smoke'): raise ValueError('background must be a kBackgrounds name')
    if 'brightness' in p and (type(p['brightness']) is not int or not 10 <= p['brightness'] <= 200): raise ValueError('brightness out of range')
    if 'scale' in p and (type(p['scale']) is not int or p['scale'] not in SCALE_STEPS): raise ValueError('scale must be a step')
    if 'scales' in p:
        s = p['scales']
        if not isinstance(s, list) or any(type(v) is not int or v not in SCALE_STEPS for v in s) or s != sorted(set(s)):
            raise ValueError('scales must be increasing steps')
    return p


def frames_of(raw):
    """Renderer frames from on_message's return value (None, or newline-terminated JSON lines)."""
    if raw is None: return []
    assert isinstance(raw, bytes) and raw.endswith(b'\n'), raw
    out = [json.loads(line) for line in raw.decode().splitlines()]
    for f in out: assert set(f) == {'schema', 'request_id', 'run_id', 'session', 'op', 'payload'}, f
    return out


class MemoryStore:
    """PlacementStore stand-in: load() -> one placement per desktop, save(one per desktop)."""
    def __init__(self, value=None): self.value, self.saved = copy.deepcopy(value) if value else [dict(g.DEFAULT_PLACEMENT) for _ in range(3)], []
    def load(self): return copy.deepcopy(self.value)
    def save(self, value):
        g.validate_placements(value)  # same contract as PlacementStore.save
        self.value = copy.deepcopy(value); self.saved.append(copy.deepcopy(value))


class SettingsSpy:
    """Layout settings store; the menu must never write layout settings."""
    def __init__(self): self.saved = []
    def load(self): return dict(view.DEFAULT_SETTINGS)
    def save(self, s): self.saved.append(dict(s))


class Menu:
    """Drives a real ViewControl with contract ops and records what reaches the renderer."""
    def __init__(self, test, names=NAMES, store=None, initial=None):
        self.t, self.store, self.settings, self.focused = test, store or MemoryStore(initial), SettingsSpy(), []
        self.c = view.ViewControl(view.ViewState(names, placements=self.store.load()), 'run', 'session', self.focused.append,
                                  self.settings, None, placements=self.store)
        self.menu, self.last_id = None, 0
        for f in frames_of(self.c.initial_frame()): self._record(f)

    def _record(self, f):
        self.t.assertGreater(f['request_id'], self.last_id, 'renderer request ids must increase')
        self.last_id = f['request_id']
        if f['op'] == 'placement_menu': self.menu = check_menu_payload(f['payload'])
        if f['op'] == 'placement': g.validate_placements(f['payload']['desktops'], len(self.c.state.names))

    def send(self, op, payload=None):
        message = decode_control(wire(op, payload, request_id=self.last_id + 1000))  # exactly what the socket delivers
        out = frames_of(self.c.on_message(message))
        for f in out: self._record(f)
        return out

    def ops(self, frames): return [f['op'] for f in frames]
    def menu_frames(self, frames): return [f['payload'] for f in frames if f['op'] == 'placement_menu']
    def placement_frames(self, frames): return [f['payload']['desktops'] for f in frames if f['op'] == 'placement']

    def open(self):
        if self.menu is None or not self.menu['open']:
            frames = self.send('menu_toggle')
            self.t.assertEqual([m['open'] for m in self.menu_frames(frames)], [True], 'menu_toggle must open with one frame')
        return self.menu

    def goto(self, desktop=None, field=None):
        self.open()
        for _ in range(3):
            if desktop is None or self.menu['desktop'] == desktop: break
            self.send('menu_desktop_next')
        for _ in range(len(MENU_FIELDS)):
            if field is None or self.menu['field'] == field: break
            self.send('menu_field_next')
        if desktop is not None: self.t.assertEqual(self.menu['desktop'], desktop)
        if field is not None: self.t.assertEqual(self.menu['field'], field)

    def value(self, desktop=None, field=None):
        d = self.menu['desktop'] if desktop is None else desktop
        return self.c.state.placements[d][self.menu['field'] if field is None else field]

    def set_live(self, desktops):
        """Set placements through the existing panel path (decoded exactly like the control socket does)."""
        return self.send('placement', {'desktops': desktops})

    def wait_saved(self, expected, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.store.value == expected: return True
            time.sleep(0.02)
        return self.store.value == expected


class SentinelTests(unittest.TestCase):
    def test_menu_wiring_landed(self):
        """MENU-WIRING is merged: the wiring-gated tests run instead of skipping."""
        self.assertTrue(WIRING)

    def test_menu_renderer_landed(self):
        """MENU-UI is merged: the renderer accepts placement_menu frames."""
        self.assertTrue(RENDERER)


class MenuSchemaTests(unittest.TestCase):
    """The 'placement_menu' payload schema. Runs now: it pins the contract the other branches must meet."""
    @classmethod
    def setUpClass(cls): cls.cases = json.loads(FIXTURE.read_text())

    def test_fixture_valid_payloads_pass_reference_validator(self):
        self.assertGreaterEqual(len(self.cases['valid']), 3)
        for p in self.cases['valid']:
            with self.subTest(p=p): check_menu_payload(p)

    def test_fixture_invalid_payloads_rejected_by_reference_validator(self):
        self.assertGreaterEqual(len(self.cases['invalid']), 30)
        for case in self.cases['invalid']:
            with self.subTest(case['why']), self.assertRaises(ValueError): check_menu_payload(case['payload'])

    def test_fixture_covers_every_field_and_bound(self):
        valid = self.cases['valid']
        self.assertEqual({p['field'] for p in valid}, set(MENU_FIELDS) | {'brightness'})
        self.assertEqual({p['desktop'] for p in valid}, set(range(g.MAX_DESKTOPS)))
        self.assertEqual({len(p['placements']) for p in valid}, set(range(1, g.MAX_DESKTOPS + 1)))  # 1..4 desktops
        self.assertEqual({p['open'] for p in valid}, {True, False})
        flat = [pl for p in valid for pl in p['placements']]
        for key, (lo, hi) in g.PLACEMENT_BOUNDS.items():
            self.assertIn(lo, [pl[key] for pl in flat], key); self.assertIn(hi, [pl[key] for pl in flat], key)

    def test_menu_fields_are_the_placement_keys(self):
        self.assertEqual(MENU_FIELDS, g.PLACEMENT_KEYS)
        self.assertEqual(set(MENU_FIELDS), set(g.PLACEMENT_BOUNDS))

    def test_control_socket_never_accepts_menu_state(self):
        """Menu state is owned by the supervisor; neither the panel nor a bind may inject a 'placement_menu' frame."""
        for p in self.cases['valid'][:2]:
            with self.assertRaises(ValueError): decode_control(wire('placement_menu', p))


@NEEDS_WIRING
class MenuControlSocketTests(unittest.TestCase):
    def test_every_hotkey_action_decodes_with_empty_payload(self):
        for op in MENU_ACTIONS:
            with self.subTest(op=op):
                self.assertEqual(decode_control(wire(op))['op'], op)
                with self.assertRaises(ValueError): decode_control(wire(op, {'step': 5}))  # no payload smuggling
                with self.assertRaises(ValueError): decode_control(wire(op, {'desktops': [TILTED]}))

    def test_control_ops_grow_by_exactly_the_menu_actions(self):
        needs_payload = {'select', 'placement'}  # probed with an empty payload, so these two are rejected here
        candidates = BASE_CONTROL_OPS | set(MENU_ACTIONS) | {'placement_menu', 'menu', 'menu_set', 'menu_save', 'menu_open',
                                                             'menu_close', 'view', 'style', 'exec', 'eval', 'mutate'}
        self.assertEqual({op for op in candidates if _control_accepts(op)}, (BASE_CONTROL_OPS - needs_payload) | set(MENU_ACTIONS))

    def test_bind_helper_accepts_menu_actions_and_sends_one_frame(self):
        from tools.native_spike import xr_control
        self.assertTrue(set(MENU_ACTIONS) <= set(xr_control.OPS))
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / 'c.sock')
        server = socket.socket(socket.AF_UNIX); server.bind(path); server.listen(1); self.addCleanup(server.close)
        got = []
        def accept():
            conn, _ = server.accept()
            with conn: got.append(conn.recv(4096))
        t = threading.Thread(target=accept); t.start()
        r = subprocess.run([sys.executable, '-I', str(Path(view.__file__).with_name('xr_control.py')), path, 'run', 'session',
                            'menu_inc_big'], timeout=5)
        t.join(5)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(decode_control(got[0])['op'], 'menu_inc_big')


@NEEDS_WIRING
class MenuOpenCloseTests(unittest.TestCase):
    def test_starts_closed_and_initial_frames_never_show_the_menu(self):
        m = Menu(self)
        self.assertTrue(m.menu is None or m.menu['open'] is False)

    def test_toggle_opens_then_closes_with_full_payload(self):
        m = Menu(self, initial=[TILTED, g.DEFAULT_PLACEMENT, TILTED])
        opened = m.open()
        self.assertTrue(opened['open'])
        self.assertIn(opened['desktop'], (0, 1, 2)); self.assertIn(opened['field'], MENU_FIELDS)
        self.assertEqual(opened['placements'], [TILTED, g.DEFAULT_PLACEMENT, TILTED])
        closed = m.menu_frames(m.send('menu_toggle'))
        self.assertEqual(len(closed), 1)
        self.assertFalse(closed[0]['open'])
        self.assertEqual(closed[0]['placements'], [TILTED, g.DEFAULT_PLACEMENT, TILTED])  # open=false still carries a full payload
        self.assertTrue(m.menu_frames(m.send('menu_toggle'))[0]['open'])  # reopens

    def test_actions_while_closed_change_nothing(self):
        m = Menu(self)
        before = copy.deepcopy(m.c.state.placements)
        for op in MENU_ACTIONS[1:]:
            with self.subTest(op=op):
                frames = m.send(op)
                self.assertNotIn('placement', m.ops(frames))
                self.assertTrue(all(not p['open'] for p in m.menu_frames(frames)), 'a closed menu must stay hidden')
        self.assertEqual(m.c.state.placements, before)
        self.assertEqual(m.store.saved, [])

    def test_menu_never_touches_view_layout_settings_or_focus(self):
        m = Menu(self)
        view_before = m.c.state.view()
        m.goto(desktop=2, field='curvature')
        for op in ('menu_inc', 'menu_inc_big', 'menu_dec', 'menu_reset', 'menu_desktop_prev', 'menu_field_prev', 'menu_toggle'):
            m.send(op)
        self.assertEqual(m.settings.saved, [])          # layout.json / profile view settings untouched
        self.assertEqual(m.c.state.view(), view_before)  # zoom, selection, spacing, mode unchanged
        m.c.join_focus(1)
        self.assertEqual(m.focused, [])                   # no compositor focus dispatch from the menu

    def test_help_card_still_toggles_with_menu_open(self):
        m = Menu(self); m.open()
        self.assertEqual(m.ops(m.send('help')), ['help'])
        self.assertTrue(m.menu['open'])


@NEEDS_WIRING
class MenuNavigationTests(unittest.TestCase):
    def test_desktop_next_and_prev_wrap(self):
        m = Menu(self); start = m.open()['desktop']
        seen = [start]
        for _ in range(3):
            frames = m.send('menu_desktop_next')
            self.assertEqual(len(m.menu_frames(frames)), 1); seen.append(m.menu['desktop'])
        self.assertEqual(seen, [start, (start + 1) % 3, (start + 2) % 3, start])
        m.goto(desktop=0); m.send('menu_desktop_prev')
        self.assertEqual(m.menu['desktop'], 2)   # wraps backwards
        m.send('menu_desktop_next')
        self.assertEqual(m.menu['desktop'], 0)   # wraps forwards

    def test_field_next_and_prev_wrap_in_contract_order(self):
        m = Menu(self); m.goto(field='x')
        order = ['x']
        rows = list(MENU_FIELDS) + ['brightness']  # the background's Brightness row comes after the placement sliders
        for _ in rows:
            m.send('menu_field_next'); order.append(m.menu['field'])
        self.assertEqual(order, rows + ['x'])
        m.send('menu_field_prev'); self.assertEqual(m.menu['field'], 'brightness')
        back = []
        for _ in rows:
            m.send('menu_field_prev'); back.append(m.menu['field'])
        self.assertEqual(back, list(reversed(rows))[1:] + ['brightness'])

    def test_navigation_does_not_change_placements_or_save(self):
        m = Menu(self, initial=[TILTED] * 3); m.open()
        for op in ('menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev') * 3:
            self.assertNotIn('placement', m.ops(m.send(op)))
        self.assertEqual(m.c.state.placements, [TILTED] * 3)
        self.assertEqual(m.store.saved, [])

    def test_reopen_starts_on_the_viewed_desktop_and_keeps_the_field(self):
        """MENU-WIRING's choice, adopted: opening selects the desktop being looked at (view selection); the field stays."""
        m = Menu(self); m.goto(desktop=2, field='curvature')
        m.send('menu_toggle')
        self.assertEqual(m.send('next')[0]['op'], 'view')   # look at XR-2 while the menu is closed
        m.send('menu_toggle')
        self.assertEqual((m.menu['open'], m.menu['desktop'], m.menu['field']), (True, m.c.state.selected, 'curvature'))
        self.assertEqual(m.menu['desktop'], 1)


@NEEDS_WIRING
class MenuAdjustTests(unittest.TestCase):
    def steps(self, m, field):
        """(small, big) step for `field`, measured from the default value on desktop 0 and undone again."""
        m.goto(desktop=0, field=field)
        start = m.value()
        m.send('menu_inc'); small = m.value() - start; m.send('menu_dec')
        self.assertEqual(m.value(), start, f'{field}: menu_dec must undo menu_inc')
        m.send('menu_inc_big'); big = m.value() - start; m.send('menu_dec_big')
        self.assertEqual(m.value(), start, f'{field}: menu_dec_big must undo menu_inc_big')
        return small, big

    def test_small_and_big_steps_per_field(self):
        m = Menu(self)
        for field in MENU_FIELDS:
            with self.subTest(field=field):
                small, big = self.steps(m, field)
                self.assertGreater(small, 0); self.assertGreater(big, small)
                self.assertIsInstance(small, int); self.assertIsInstance(big, int)

    def test_adjust_touches_only_the_selected_desktop_and_field(self):
        m = Menu(self, initial=[TILTED] * 3); m.goto(desktop=1, field='distance')
        m.send('menu_inc')
        self.assertGreater(m.c.state.placements[1]['distance'], TILTED['distance'])
        self.assertEqual({k: v for k, v in m.c.state.placements[1].items() if k != 'distance'},
                         {k: v for k, v in TILTED.items() if k != 'distance'})
        self.assertEqual((m.c.state.placements[0], m.c.state.placements[2]), (TILTED, TILTED))

    def test_x_wraps_round_past_behind_you(self):
        for start, op, want in ((1799, 'menu_inc', 1801 - 3600), (1700, 'menu_inc_big', 1850 - 3600), (-1799, 'menu_dec', 1799),
                                (1800, 'menu_inc', 1802 - 3600)):
            with self.subTest(start=start, op=op):
                m = Menu(self, initial=[g.DEFAULT_PLACEMENT, {**g.DEFAULT_PLACEMENT, 'x': start}, g.DEFAULT_PLACEMENT])
                m.goto(desktop=1, field='x')
                frames = m.send(op)
                self.assertEqual(m.value(), want)
                self.assertIn('placement', m.ops(frames))
                g.validate_placements(m.c.state.placements)

    def test_clamped_exactly_at_every_bound(self):
        """From one step inside each bound, inc/dec land exactly on it (not short), then stay there with no frame. x, the
        turn round you, has no bound to stop at: it carries on round past behind you (test_x_wraps_round_past_behind_you)."""
        for field in MENU_FIELDS:
            if field == 'x': continue
            lo, hi = g.PLACEMENT_BOUNDS[field]
            for bound, near, ops in ((hi, hi - 1, ('menu_inc', 'menu_inc_big')), (lo, lo + 1, ('menu_dec', 'menu_dec_big'))):
                for op in ops:
                    with self.subTest(field=field, bound=bound, op=op):
                        m = Menu(self, initial=[g.DEFAULT_PLACEMENT, {**g.DEFAULT_PLACEMENT, field: near}, g.DEFAULT_PLACEMENT])
                        m.goto(desktop=1, field=field)
                        m.send(op)
                        self.assertEqual(m.value(), bound)
                        self.assertEqual(m.menu['placements'][1][field], bound)
                        for again in ops * 3:
                            frames = m.send(again)
                            self.assertNotIn('placement', m.ops(frames), 'no renderer frame when clamped')
                            self.assertEqual(m.value(), bound)
                        g.validate_placements(m.c.state.placements)

    def test_hammering_never_leaves_bounds(self):
        m = Menu(self)
        for field in MENU_FIELDS:
            lo, hi = g.PLACEMENT_BOUNDS[field]
            m.goto(desktop=2, field=field)
            for _ in range(300):
                m.send('menu_inc_big')
                self.assertTrue(lo <= m.value() <= hi, field)
            if field != 'x': self.assertEqual(m.value(), hi, field)  # x turns on round you: always in range, never stuck
            for _ in range(300):
                m.send('menu_dec_big')
                self.assertTrue(lo <= m.value() <= hi, field)
            if field != 'x': self.assertEqual(m.value(), lo, field)

    def test_reset_restores_flat_default_for_the_selected_desktop_only(self):
        m = Menu(self, initial=[TILTED] * 3); m.goto(desktop=1, field='distance')
        frames = m.send('menu_reset')
        self.assertEqual(m.c.state.placements, [TILTED, g.DEFAULT_PLACEMENT, TILTED])
        self.assertEqual(m.placement_frames(frames), [[TILTED, g.DEFAULT_PLACEMENT, TILTED]])
        self.assertEqual(m.menu['placements'], [TILTED, g.DEFAULT_PLACEMENT, TILTED])
        self.assertEqual((m.menu['desktop'], m.menu['field']), (1, 'distance'))  # selection kept
        self.assertNotIn('placement', m.ops(m.send('menu_reset')))           # already flat: nothing to send

    def test_external_panel_change_refreshes_an_open_menu(self):
        """placements is part of menu state; a panel 'placement' while open must re-send placement_menu."""
        m = Menu(self); m.open()
        frames = m.set_live([TILTED, TILTED, g.DEFAULT_PLACEMENT])
        self.assertIn('placement', m.ops(frames))
        self.assertEqual(m.menu['placements'], [TILTED, TILTED, g.DEFAULT_PLACEMENT])
        m.goto(desktop=0, field='x'); m.send('menu_inc')
        self.assertGreater(m.value(), TILTED['x'])  # menu adjusts from the panel's value, not a stale copy


@NEEDS_WIRING
class MenuLiveApplyTests(unittest.TestCase):
    def test_every_change_sends_a_placement_frame_and_a_menu_frame(self):
        m = Menu(self); m.goto(desktop=2, field='curvature')
        for op in ('menu_inc', 'menu_inc_big', 'menu_dec', 'menu_dec_big'):
            with self.subTest(op=op):
                frames = m.send(op)
                self.assertEqual(sorted(m.ops(frames)), ['placement', 'placement_menu'])
                self.assertEqual(m.placement_frames(frames), [m.c.state.placements])
                self.assertEqual(m.menu['placements'], m.c.state.placements)

    def test_placement_frame_is_the_existing_renderer_frame(self):
        m = Menu(self); m.goto(desktop=0, field='curvature')
        frame = next(f for f in m.send('menu_inc') if f['op'] == 'placement')
        self.assertEqual(set(frame['payload']), {'desktops'})
        rebuilt = json.loads(view.placement_frame('run', 'session', frame['request_id'], frame['payload']['desktops']))
        self.assertEqual(frame, rebuilt)

    def test_status_reports_menu_edits(self):
        m = Menu(self); m.goto(desktop=0, field='distance'); m.send('menu_inc_big')
        self.assertEqual(m.c.status_reply({})['placements'], m.c.state.placements)

    def test_fewer_outputs_one_placement_each_in_menu_and_frames(self):
        store = MemoryStore([g.DEFAULT_PLACEMENT, g.DEFAULT_PLACEMENT, TILTED])
        m = Menu(self, names=['XR-1'], store=store)
        menu = m.open()
        self.assertEqual(len(menu['placements']), 1)  # one per desktop of the run (desktops come and go in XR)
        self.assertEqual(menu['names'], [1])
        m.goto(desktop=0, field='x')
        frames = m.send('menu_inc')
        self.assertEqual([len(d) for d in m.placement_frames(frames)], [1])  # renderer frame matches the run's outputs
        m.send('menu_toggle')
        # The run saves its own desktops' placements; the profile store keeps any saved tail (test_placement).
        self.assertTrue(m.wait_saved([m.c.state.placements[0]]), f'saved {store.value}')


@NEEDS_WIRING
class MenuPersistenceTests(unittest.TestCase):
    def test_rapid_steps_are_debounced(self):
        m = Menu(self); m.goto(desktop=0, field='x')
        for _ in range(20): m.send('menu_inc')
        self.assertLessEqual(len(m.store.saved), 2, f'{len(m.store.saved)} profile writes for 20 key presses')

    def test_close_persists_the_current_placements(self):
        m = Menu(self); m.goto(desktop=1, field='curvature')
        for _ in range(5): m.send('menu_inc')
        m.send('menu_toggle')
        self.assertTrue(m.wait_saved(m.c.state.placements), f'saved {m.store.value}, live {m.c.state.placements}')
        self.assertEqual(m.store.saved[-1], m.c.state.placements)

    def test_debounced_save_lands_without_closing(self):
        m = Menu(self); m.goto(desktop=0, field='curvature'); m.send('menu_inc')
        expected = copy.deepcopy(m.c.state.placements)
        # Some implementations flush from the supervisor loop rather than a timer; give them their hook if present.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and m.store.value != expected:
            for hook in ('tick', 'flush_menu', 'flush'):
                fn = getattr(m.c, hook, None)
                if callable(fn):
                    try: fn(time.monotonic())
                    except TypeError: fn()
            time.sleep(0.05)
        self.assertEqual(m.store.value, expected)
        self.assertTrue(m.menu['open'])

    def test_save_failure_never_blocks_live_apply(self):
        class Broken(MemoryStore):
            def save(self, value): raise OSError('read-only profile')
        m = Menu(self, store=Broken()); m.goto(desktop=0, field='x')
        self.assertIn('placement', m.ops(m.send('menu_inc')))
        m.send('menu_toggle')                      # flush on close fails quietly
        m.send('menu_toggle')                      # and the menu still works afterwards
        self.assertTrue(m.menu['open'])
        self.assertIn('placement', m.ops(m.send('menu_inc')))

    def test_no_store_means_live_only(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, SettingsSpy(), None)  # CLI runs
        frames = frames_of(c.on_message(decode_control(wire('menu_toggle'))))
        self.assertEqual([f['op'] for f in frames], ['placement_menu'])

    def test_real_placement_store_keeps_other_profile_fields(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'profiles.json'
        profiles.save_profile(path, 'beast', {**profiles.DEFAULT_PROFILE, 'spacing': 120, 'tracking': False})
        m = Menu(self, store=profiles.PlacementStore(path, 'beast'))
        m.goto(desktop=2, field='curvature'); m.send('menu_inc_big'); m.send('menu_toggle')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and profiles.load_profile(path, 'beast')['placements'] != m.c.state.placements:
            time.sleep(0.02)
        p = profiles.load_profile(path, 'beast')
        self.assertEqual(p['placements'], m.c.state.placements)
        self.assertEqual((p['spacing'], p['tracking']), (120, False))
        self.assertEqual(set(p), BASE_PROFILE_KEYS)  # no new profile fields (menu state is never saved)
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')


@NEEDS_WIRING
class MenuSupervisorEndToEndTests(unittest.TestCase):
    """Real bind helper -> run control socket -> real Supervisor loop -> renderer channel and profiles.json."""
    def test_hotkeys_drive_renderer_frames_and_profile(self):
        from tools.native_spike.supervisor import Supervisor
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        runtime = Path(tmp.name) / 'rt'; runtime.mkdir(mode=0o700)
        path = Path(tmp.name) / 'profiles.json'
        server = PrivateServer(runtime, 'run', 'session'); self.addCleanup(server.close)
        store = profiles.PlacementStore(path, 'beast')
        control = view.ViewControl(view.ViewState(NAMES, placements=store.load()), 'run', 'session', lambda n: None,
                                   profiles.ProfileStore(path, 'beast'), server, placements=store)
        parent, renderer = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(renderer.close)
        class Child:
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done = True
            def kill(self): self.done = True
        child = Child()
        sup = Supervisor(child, lambda: 'recovered', control=control)
        client = str(Path(view.__file__).with_name('xr_control.py'))
        saved_while_open = []
        def press(op):
            subprocess.run([sys.executable, '-I', client, str(server.path), 'run', 'session', op], timeout=5, check=True)
            time.sleep(0.1)
        def drive():
            time.sleep(0.15)
            for op in ('menu_toggle', 'menu_inc_big', 'menu_inc'): press(op)
            deadline = time.monotonic() + 3      # debounced save while the menu is still open
            while time.monotonic() < deadline:
                if profiles.load_profile(path, 'beast')['placements'] != [g.DEFAULT_PLACEMENT] * 3:
                    saved_while_open.append(profiles.load_profile(path, 'beast')['placements']); break
                time.sleep(0.05)
            press('menu_reset'); press('menu_toggle')
            time.sleep(0.3); renderer.sendall(b'H'); time.sleep(0.2); child.done = True
        t = threading.Thread(target=drive); t.start()
        renderer.sendall(b'H')
        self.assertEqual(sup.run(parent), 'recovered')
        t.join(10)
        frames = [json.loads(l) for l in renderer.recv(1 << 20).decode().splitlines()]
        menus = [f['payload'] for f in frames if f['op'] == 'placement_menu']
        for p in menus: check_menu_payload(p)
        self.assertEqual([p['open'] for p in (menus[0], menus[-1])], [True, False])
        placements = [f['payload']['desktops'] for f in frames if f['op'] == 'placement']
        self.assertEqual(len(placements), 3)                          # inc_big, inc, reset
        d, field = menus[0]['desktop'], menus[0]['field']
        self.assertGreater(placements[1][d][field], placements[0][d][field])
        self.assertEqual(placements[-1], [g.DEFAULT_PLACEMENT] * 3)   # reset to flat
        self.assertEqual(len(saved_while_open), 1, 'debounced save did not reach profiles.json while the menu was open')
        self.assertEqual(profiles.load_profile(path, 'beast')['placements'], [g.DEFAULT_PLACEMENT] * 3)  # saved on close
        ids = [f['request_id'] for f in frames]
        self.assertEqual(ids, sorted(ids))


def bind_commands(lua):
    """(chord, full shell command) for every hl.bind in generated Lua. Accepts the inline form
    exec_cmd("<prefix> op") and the shared-prefix form `local c = "<prefix> "` ... exec_cmd(c .. "op") that MENU-WIRING
    uses to stay under the 4096-byte eval cap."""
    local = re.search(r'local c = "([^"]*)"', lua)
    out = []
    for m in re.finditer(r'hl\.bind\("([^"]+)", hl\.dsp\.exec_cmd\((?:"([^"]*)"|c \.\. "([^"]*)")\)\)', lua):
        if m.group(2) is not None: out.append((m.group(1), m.group(2)))
        else:
            assert local is not None, 'exec_cmd(c .. ...) without a local c'
            out.append((m.group(1), local.group(1) + m.group(3)))
    return out


def installed_binds(lua):
    return [(chord, cmd.rsplit(' ', 1)[1]) for chord, cmd in bind_commands(lua)]


def menu_bind_tables():
    """Every (chord, op) table in view.py that mentions a menu action (BINDS or a separate in-menu table)."""
    tables = {}
    for name, value in vars(view).items():
        if isinstance(value, (tuple, list)) and value and all(
                isinstance(e, (tuple, list)) and len(e) == 2 and all(isinstance(s, str) for s in e) for e in value):
            if any(op in MENU_ACTIONS for _, op in value): tables[name] = list(value)
    return tables


class BindSafetyTests(unittest.TestCase):
    """Run now: guards the always-installed bind set that the menu will extend."""
    def test_run_binds_never_take_unmodified_keys(self):
        """Plain arrows, R, Tab... must never be bound globally for a whole run (they would break typing)."""
        for chord, op in installed_binds(view.install_binds_lua(*LIVE_BIND_ARGS)):
            with self.subTest(chord=chord, op=op):
                self.assertTrue({'CTRL', 'ALT', 'SUPER'} & set(chord.split(' + ')), f'{chord} -> {op} has no modifier')

    def test_run_binds_have_unique_chords(self):
        chords = [c for c, _ in installed_binds(view.install_binds_lua(*LIVE_BIND_ARGS))]
        self.assertEqual(len(chords), len(set(chords)))
        self.assertEqual(len(chords), len(view.BINDS))

    def _evals(self, *calls):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        for call in calls:
            try: getattr(client, call)(*([] if call == 'remove_view_binds' else LIVE_BIND_ARGS))
            except ValueError as exc:
                self.fail(f'{call}: {exc} ({len(view.install_binds_lua(*LIVE_BIND_ARGS))} bytes of bind Lua for {len(view.BINDS)} binds)')
        return sent

    def test_run_start_bind_install_fits_one_eval(self):
        """HyprlandClient._eval caps code at 4096 bytes. With the live checkout path, a token_hex run id and a sha256 session
        the 11 current binds take ~4.0 KB, so one more global bind in the same eval makes every run fail at start."""
        self._evals('install_view_binds', 'remove_view_binds')

    def test_reload_bind_reinstall_fits_one_eval(self):
        """After a Hyprland config reload (Omarchy theme switch) during a run, remove+install goes out in one eval. At
        f593574 that was ~4.15 KB with live-sized arguments and raised, so the run lost its hotkeys; MENU-WIRING's
        shorter bind commands brought it well under the 4096-byte cap."""
        self._evals('reinstall_view_binds')


@NEEDS_WIRING
class MenuBindTests(unittest.TestCase):
    def test_toggle_is_a_run_bind_and_does_not_clash(self):
        installed = installed_binds(view.install_binds_lua(*LIVE_BIND_ARGS))
        toggles = [c for c, op in installed if op == 'menu_toggle']
        self.assertEqual(len(toggles), 1, 'menu_toggle needs exactly one always-installed chord')
        self.assertIn(('CTRL + ALT + P', 'prediction'), view.BINDS)   # Ctrl+Alt+P is taken: pose prediction
        self.assertNotEqual(toggles[0], 'CTRL + ALT + P')
        self.assertEqual(toggles[0], MENU_TOGGLE_CHORD)
        self.assertEqual(view.chord_label(toggles[0]), MENU_TOGGLE_LABEL)
        self.assertEqual([op for c, op in installed if c == toggles[0]], ['menu_toggle'])

    def test_every_menu_action_has_a_chord(self):
        bound = {op for table in menu_bind_tables().values() for _, op in table}
        self.assertEqual(set(MENU_ACTIONS) - bound, set(), 'menu actions with no key')

    def test_shift_is_the_big_step_modifier(self):
        for table in menu_bind_tables().values():
            chords = {op: c for c, op in table}
            for small, big in (('menu_inc', 'menu_inc_big'), ('menu_dec', 'menu_dec_big')):
                if small in chords and big in chords:
                    with self.subTest(big=big):
                        parts = chords[big].split(' + ')
                        self.assertIn('SHIFT', parts)
                        self.assertEqual(set(parts) - {'SHIFT'}, set(chords[small].split(' + ')))  # same key, plus Shift

    def test_help_card_lists_the_menu_toggle(self):
        chord = next(c for c, op in view.BINDS if op == 'menu_toggle')
        self.assertIn(view.chord_label(chord), [k for k, _ in view.help_rows()])
        self.assertLessEqual(len(view.help_rows()), 64)   # renderer limit kMaxHelpRows

    def test_readme_documents_the_real_toggle_chord(self):
        chord = view.chord_label(next(c for c, op in view.BINDS if op == 'menu_toggle'))
        # The README is the plain-language guide (its "## Placing desktops" section); docs/technical.md has the detail.
        for doc, heading in (('README.md', '## Placing desktops'), ('docs/technical.md', '### Placement pills')):
            section = (ROOT / doc).read_text().split(heading, 1)
            self.assertEqual(len(section), 2, f'{doc} needs a "{heading}" section')
            self.assertIn(chord, section[1].split('\n## ', 1)[0])
        self.assertEqual(chord, MENU_TOGGLE_LABEL)


class GlassPanelSourceTests(unittest.TestCase):
    """What the pixel tests cannot see (spike/tests/glass_test.cpp covers the geometry, the look under the 191/255 cap and
    the panel content): the open panel shares the help card's font and its Material Design 3 scheme."""
    def test_the_panel_loads_the_same_font_family_as_the_help_card(self):
        src = (ROOT / 'spike/src/wayland.cpp').read_text()
        loads = re.findall(r's->(\w+)\.set_font\(HudFont::load\((\w+)', src)
        self.assertIn(('help', 'kHudFontFamily'), loads)
        self.assertIn(('glass', 'kHudFontFamily'), loads)

    def test_the_panel_and_the_help_card_draw_from_one_material_scheme(self):
        # Both take the M3 scheme of the theme colour they are drawn with (glass_theme_scheme), whose roles stay under the
        # OLED cap (spike/tests/material_test.cpp); the panel has no colours of its own to drift from the card's.
        glass = (ROOT / 'spike/src/glass.cpp').read_text()
        hud = (ROOT / 'spike/src/hud.cpp').read_text()
        self.assertRegex(glass, r'HudImage render_glass_panel\([^)]*\) \{\s*const auto& m = glass_theme_scheme\(accent\);')
        self.assertRegex(hud, r'HudImage render_help_card\([^)]*\)[^{]*\{\s*const auto& m = glass_theme_scheme\(accent\);')
        self.assertNotRegex(glass, r'constexpr std::array<float, 3> k\w+\{')

    def test_the_head_locked_menu_card_is_gone(self):
        for path in ('spike/src/wayland.cpp', 'spike/src/hud.cpp', 'spike/include/xr/hud.hpp'):
            src = (ROOT / path).read_text()
            for name in ('PlacementMenuOverlay', 'render_placement_menu_card', 'menu_geometry_json', 'render_menu_pointer'):
                self.assertNotIn(name, src, f'{path}: {name}')


# The open panel's footer (spike/src/glass.cpp glass_panel_hint, drawn with the system font): arrow glyph -> bound key.
HINT_KEYS = {'\u2191': 'up', '\u2193': 'down', '\u2190': 'left', '\u2192': 'right', 'Tab': 'Tab', 'R': 'R'}


@unittest.skipUnless(WIRING, 'needs MENU-WIRING binds')
class PanelHintAgreementTests(unittest.TestCase):
    """The keys the open panel's footer tells you to press are the keys wiring binds, and the README names them."""
    def hint_keys(self):
        src = (ROOT / 'spike/src/glass.cpp').read_text()
        raw = re.search(r'glass_panel_hint\(bool ascii\) \{\s*return ascii \? "[^"]+" : "([^"]+)"', src).group(1)
        hint = raw.encode('ascii').decode('unicode_escape').encode('latin-1').decode('utf-8')  # C escapes -> text
        keys = [ch for ch in hint if ch in HINT_KEYS] + [w for w in hint.split() if w in HINT_KEYS]
        self.assertEqual(set(keys), set(HINT_KEYS), hint)
        return keys

    def test_every_footer_key_is_bound_while_a_panel_is_open(self):
        parts = {p for c, _ in view.MENU_BINDS for p in c.split(' + ')}
        for key in self.hint_keys():
            with self.subTest(key=key): self.assertIn(HINT_KEYS[key], parts)

    def test_readme_names_every_footer_key(self):
        section = (ROOT / 'README.md').read_text().split('## Placing desktops', 1)[1].split('\n## ', 1)[0]
        for key in self.hint_keys():
            with self.subTest(key=key): self.assertIn(HINT_KEYS[key].title() if len(HINT_KEYS[key]) > 1 else key, section)


class MenuToggleKeyTests(unittest.TestCase):
    """Runs now: the agreed toggle key is free today and documented."""
    def test_ctrl_alt_l_is_free_and_ctrl_alt_p_stays_prediction(self):
        self.assertIn(('CTRL + ALT + P', 'prediction'), view.BINDS)
        self.assertEqual([op for chord, op in view.BINDS if chord == MENU_TOGGLE_CHORD and op != 'menu_toggle'], [])

    def test_readme_section_uses_ctrl_alt_l(self):
        for doc, heading in (('README.md', '## Placing desktops'), ('docs/technical.md', '### Placement pills')):
            section = (ROOT / doc).read_text().split(heading, 1)[1].split('\n## ', 1)[0]
            self.assertIn(MENU_TOGGLE_LABEL, section)
            self.assertNotRegex(section, r'Press \*\*Ctrl\+Alt\+M\*\*')  # the old mouse mode's key is gone
            self.assertNotRegex(section, r'Press \*\*Ctrl\+Alt\+P')
        self.assertNotIn('Ctrl+Alt+M', (ROOT / 'README.md').read_text())  # the user guide no longer names it at all


class NoScopeChangeTests(unittest.TestCase):
    """The menu is presentation only. None of these grants, allowlists or persisted shapes may widen. Runs now."""
    def test_consent_shapes_unchanged(self):
        from tools.native_spike import panel_consent, supervisor
        self.assertEqual(panel_consent.PANEL_KEYS, BASE_PANEL_KEYS)
        self.assertEqual((supervisor.CAPTURE_SCOPE, supervisor.CAPTURE_ALLOW), ('managed-outputs', ['headless-outputs', 'capture-managed-outputs']))

    def test_hyprland_allowlists_unchanged(self):
        from tools.native_spike import hyprland as h
        self.assertEqual(h.QUERY, BASE_QUERY)
        client = h.HyprlandClient('a' * 40 + '_1_2', Path('/nonexistent'))
        client._request = lambda wire: b'ok'
        for bad in ('dispatch submap placement', 'keyword bind CTRL,M,exec,x', 'dispatch hl.dsp.focus({monitor="DP-1"})',
                    'dispatch exec x', 'eval hl.bind("up", x)'):
            with self.subTest(bad), self.assertRaises(ValueError): client.mutate(bad)

    def test_panel_capabilities_unchanged(self):
        """capabilities become panel buttons (RunningEditor.qml); menu actions are glasses hotkeys, not buttons."""
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.assertEqual(c.status_reply({})['capabilities'], BASE_CAPABILITIES)

    def test_bind_commands_only_run_the_control_helper(self):
        lua = view.install_binds_lua(*LIVE_BIND_ARGS)
        cmds = [cmd for _, cmd in bind_commands(lua)]
        self.assertEqual(len(cmds), lua.count('hl.bind('))
        self.assertEqual(len(cmds), lua.count('exec_cmd('))  # every command was parsed, none hidden
        prefix = '{} -I {} {} {} {} '.format(*LIVE_BIND_ARGS)
        for cmd in cmds:
            self.assertTrue(cmd.startswith(prefix), cmd)
            self.assertRegex(cmd[len(prefix):], r'\A[a-z_]+\Z')
        self.assertNotIn('os.execute', lua); self.assertNotIn('io.', lua)

    def test_profile_shape_unchanged(self):
        self.assertEqual(set(profiles.DEFAULT_PROFILE), BASE_PROFILE_KEYS)
        self.assertNotIn('menu', json.dumps(profiles.DEFAULT_PROFILE))
        self.assertEqual(view.DEFAULT_SETTINGS, {'spacing': 40, 'mode': 'anchor', 'prediction': True,  # cursor_follow removed
                                                 'video_quality': {'filter': 'catmull-rom', 'deband': False},
                                             'environment': {'background': 'none', 'last': 'grid', 'brightness': 100}})

    def test_placement_bounds_unchanged(self):
        # x / y became the orbit round you in tenths of a degree (orbit.py, 2026-11-02); distance's floor went from 50 to
        # 10 (up to ten times the size, past the field of view: huge resize, 2026-11-02); curvature is unchanged.
        self.assertEqual(g.PLACEMENT_BOUNDS, {'x': (-1800, 1800), 'y': (-900, 900), 'distance': (10, 200),
                                              'curvature': (0, 120)})


if __name__ == '__main__':
    unittest.main()
