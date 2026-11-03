"""Circle only (overview_cursor.py): in the overview the free pointer's dot is the only pointer. Inside a desktop the dot
is drawn where the cursor maps onto it and the real cursor is hidden; away from the desktops, outside the overview and at
run end the cursor shows again. Display only: the binds, hand-backs and clicks are free_pointer.py's and unchanged.

The Lua runs against a stub `hl` (any Lua interpreter) and against Hyprland's own (`Hyprland --verify-config` on a
throwaway config, as bind_check does: no compositor is started or contacted), together with free mode's grab, hand-back
and bind removal in every order. Offline only: fake clocks, fake compositor clients and sockets, no renderer or glasses.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, overview_cursor as oc, resize, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike.profiles import LayoutStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
G = fp.parse_overview_geometry(json.loads(FIXTURE['geometry']['line'])['overview_geometry'])
NAMES = ['XR-1', 'XR-2', 'XR-3']
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-3', 'x': 6400, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
OUTPUTS = resize.outputs_from_monitors(MONITORS, NAMES)
X1, X2 = 2560, 4480
T0 = G['tiles'][0]
GAIN = T0[2] / 1920
BIND_ARGS = ('/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)
LUA = shutil.which('lua') or shutil.which('luajit')
HYPRLAND = shutil.which('Hyprland')


def frames(data, ops=None):
    out = [json.loads(line) for line in (data or b'').decode().splitlines()]
    return [f for f in out if ops is None or f['op'] in ops]


def last(fs, op):
    return next((f['payload'] for f in reversed(fs) if f['op'] == op), None)


def dot(point):
    return {'visible': True, 'x': round(point[0]), 'y': round(point[1]), 'held': False}


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class DotTests(unittest.TestCase):
    """The supervisor side: which `overview_pointer` frames go to the renderer (OverviewPointer, fed like the sampler)."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda name: None,
                                        LayoutStore(Path(tmp.name) / 'l.json'), None, clock=self.clock)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = fp.OverviewPointer(self.control, clock=self.clock)
        self.addCleanup(self.p.close)
        self.control.set_overview_geometry(G)
        self.cur = None

    def at(self, x, y):
        self.clock.now += 0.004
        self.cur = (x, y)
        self.p.feed((x, y), (0.0, 0.0), OUTPUTS, self.p.want[0])
        return frames(self.control.poll())

    def move(self, dx, dy):
        self.clock.now += 0.004
        self.p.feed(self.cur, (float(dx), float(dy)), OUTPUTS, self.p.want[0])
        return frames(self.control.poll())

    def test_inside_a_desktop_the_dot_is_the_only_pointer(self):
        out = self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop'); self.assertIn(self.p.mode, oc.DOT_MODES)  # so the sampler hides the cursor
        self.assertEqual(last(out, 'overview_pointer'), dot(fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + T0[2] / 2, T0[1] + T0[3] / 2)))
        self.assertEqual(self.p.want[1], 'away')  # display only: no binds, the hidden cursor still clicks the window
        out = self.at(X1 + 480, 270)  # and it follows the cursor
        self.assertEqual(last(out, 'overview_pointer'), dot(fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + T0[2] / 4, T0[1] + T0[3] / 4)))

    def test_the_dot_sits_where_the_cursor_is_on_a_placed_desktop(self):
        placed = {**DEFAULT_PLACEMENT, 'x': 10, 'y': -5, 'distance': 150, 'curvature': 30}
        self.control.state.placements = [placed] + self.control.state.placements[1:]
        out = self.at(X1 + 480, 270)
        q = fp.project(G, T0, placed, T0[0] + 480 / 1920 * T0[2], T0[1] + 270 / 1080 * T0[3])
        self.assertEqual(last(out, 'overview_pointer'), dot(q))

    def test_one_dot_across_the_edge_and_back(self):
        out = self.at(X1 + 960, 300) + self.at(X1 + 960, 0)  # desktop, then the top edge: free
        self.assertEqual(self.p.mode, 'free')
        out += self.move(0, -40)
        out += self.move(0, (40 * GAIN + T0[1] - self.p.pointer[1] + fp.ENTER_PX + 2) / GAIN)  # back in: desktop
        self.assertEqual(self.p.mode, 'desktop')
        handed_back = self.p.pointer
        dots = [f['payload'] for f in out if f['op'] == 'overview_pointer']
        self.assertGreater(len(dots), 3)
        self.assertTrue(all(d['visible'] for d in dots))  # never a frame without it: no gap at either switch
        self.assertEqual(dots[-1], dot(handed_back))  # the hand-back leaves it where it is
        self.assertEqual(self.at(*self.p.want[2]), [])  # the cursor landing at the hand-back spot moves nothing

    def test_no_dot_away_from_the_desktops_or_outside_the_overview(self):
        out = self.at(100, 100)  # the physical monitor: the cursor shows there
        self.assertEqual(self.p.mode, 'away'); self.assertNotIn(self.p.mode, oc.DOT_MODES)
        self.assertIsNone(last(out, 'overview_pointer'))
        self.at(X1 + 960, 540)
        self.assertTrue(self.p.sent['visible'])
        for gate in ('zoom', 'typing', 'video'):
            with self.subTest(gate):
                if gate == 'zoom': out = frames(self.control.on_message({'op': 'zoom_in', 'payload': {}}))
                elif gate == 'typing': out = frames(self.control.on_message({'op': view.TYPING_OP, 'payload': {}}))
                else: self.control.video_active = True; out = []
                out += self.at(X1 + 960, 540)
                self.assertEqual(self.p.mode, 'off')
                self.assertFalse(last(out, 'overview_pointer')['visible'])
                if gate == 'zoom': self.control.on_message({'op': 'zoom_out', 'payload': {}})
                elif gate == 'typing': self.control.on_message({'op': view.TYPING_OP, 'payload': {}})
                else: self.control.video_active = False
                self.at(X1 + 960, 541)
                self.assertTrue(self.p.sent['visible'])

    def test_idle_hides_the_dot_inside_a_desktop_too(self):
        self.at(X1 + 960, 540)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        self.assertFalse(last(frames(self.control.poll()), 'overview_pointer')['visible'])  # OLED care, as in free mode
        self.assertTrue(last(self.at(X1 + 961, 540), 'overview_pointer')['visible'])


class RecordingClient:
    def __init__(self, cursor=(X1 + 960, 540)):
        self.cursor, self.calls, self.lock = cursor, [], threading.Lock()
        self.fail_hide = 0
    def query(self, kind, argument=None, timeout=2.0):
        if kind == 'monitors': return MONITORS
        if kind == 'cursorpos': return {'x': self.cursor[0], 'y': self.cursor[1]}
        raise ValueError(kind)
    def _call(self, name):
        with self.lock: self.calls.append(name)
    def grab_pointer(self, *args): self._call('grab'); self.cursor = args[-2:]
    def release_pointer(self, *args):
        self._call('release')
        if args: self.cursor = args
    def remove_resize_binds(self): self._call('remove')
    def warp_cursor(self, x, y): self._call('warp'); self.cursor = (x, y)
    def hide_overview_cursor(self):
        self._call('hide')
        if self.fail_hide:
            self.fail_hide -= 1
            raise OSError('refused')
    def show_overview_cursor(self): self._call('show')
    def seen(self, *names):
        with self.lock: return [c for c in self.calls if c in names]


class CursorStateTests(unittest.TestCase):
    def setUp(self):
        self.control, self.clock = mock.Mock(reloads=0), Clock()
        self.c = oc.OverviewCursor(self.control, clock=self.clock)
        self.client = RecordingClient()

    def test_only_changes_are_sent_and_the_first_step_always_is(self):
        for hidden in (False, False, True, True, False):
            self.c.step(self.client, hidden)
        self.assertEqual(self.client.calls, ['show', 'hide', 'show'])  # the first: a killed run's hidden cursor comes back

    def test_a_refused_call_is_retried(self):
        self.client.fail_hide = 1
        self.c.step(self.client, True)
        self.assertIsNone(self.c.hidden); self.assertEqual(self.c.errors, 1)
        self.c.step(self.client, True)
        self.assertTrue(self.c.hidden); self.assertEqual(self.client.calls, ['hide', 'hide'])

    def test_a_client_without_it_is_left_alone(self):
        self.c.step(object(), True); self.c.stop(object())
        self.assertIsNone(self.c.hidden)

    def test_a_config_reload_sends_again_but_never_loops(self):
        self.c.step(self.client, True)
        self.control.reloads += 1
        self.c.step(self.client, True)
        self.assertEqual(self.client.calls, ['hide', 'hide'])  # the reload showed it
        self.control.reloads += 1; self.clock.now += oc.RELOAD_REAPPLY_S / 2
        for _ in range(3): self.c.step(self.client, True)
        self.assertEqual(self.client.calls, ['hide', 'hide'])  # at most once per RELOAD_REAPPLY_S...
        self.clock.now += oc.RELOAD_REAPPLY_S / 2
        self.c.step(self.client, True)
        self.assertEqual(self.client.calls, ['hide', 'hide', 'hide'])  # ...but a reload is never dropped
        self.c.step(self.client, False)
        self.control.reloads += 1; self.clock.now += oc.RELOAD_REAPPLY_S
        self.c.step(self.client, False)
        self.assertEqual(self.client.calls[-1:], ['show'])  # shown: a reload changes nothing, nothing more is sent

    def test_stop_shows_it_unless_it_is_known_shown(self):
        for state, sent in ((True, ['show']), (None, ['show']), (False, [])):
            with self.subTest(state):
                client = RecordingClient()
                self.c.hidden = state
                self.c.stop(client)
                self.assertEqual(client.calls, sent); self.assertIs(self.c.hidden, False)
        broken = mock.Mock(); broken.show_overview_cursor.side_effect = OSError('gone')
        self.c.hidden = True
        self.c.stop(broken)  # best effort: never raises
        self.assertTrue(self.c.hidden)


class SamplerTests(unittest.TestCase):
    """The sampler thread hides and shows the cursor around its own grab, hand-back and bind removal."""
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = fp.OverviewPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.control.poll()
            if predicate(): return True
            time.sleep(0.003)
        return predicate()

    def start(self, client):
        sampler = fp.PointerSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return sampler

    def test_hidden_on_the_desktops_shown_away_and_outside_the_overview(self):
        client = RecordingClient()
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: client.seen('show', 'hide') == ['show', 'hide']))  # start: shown, then on XR-1
        self.assertEqual(self.p.mode, 'desktop')
        client.cursor = (100, 100)  # a warp to the physical monitor: away
        self.assertTrue(self.wait(lambda: client.seen('show', 'hide')[-1:] == ['show']))
        self.assertEqual(self.p.mode, 'away')
        client.cursor = (X2 + 300, 300)  # onto XR-2
        self.assertTrue(self.wait(lambda: client.seen('show', 'hide')[-1:] == ['hide']))
        self.control.state.zoomed = True  # zoomed in: the normal cursor
        self.assertTrue(self.wait(lambda: client.seen('show', 'hide')[-1:] == ['show']))
        self.control.state.zoomed = False
        self.assertTrue(self.wait(lambda: client.seen('show', 'hide')[-1:] == ['hide']))
        sampler.stop()
        self.assertEqual(client.seen('show', 'hide'), ['show', 'hide', 'show', 'hide', 'show', 'hide', 'show'])
        self.assertEqual(client.seen('grab', 'release', 'remove'), [])  # display only: no bind was touched for it

    def test_free_mode_and_back_stay_hidden_and_leaving_shows_it_after_the_binds(self):
        client = RecordingClient()
        self.start(client)
        self.assertTrue(self.wait(lambda: client.seen('hide') == ['hide']))
        client.cursor = (X1 + 960, 0)  # the top edge: free
        self.assertTrue(self.wait(lambda: client.seen('grab') == ['grab'] and self.p.mode == 'free'))
        client.cursor = (X1 + 960, 540 + 150)  # the parked cursor moves down: the dot goes back into XR-1
        self.assertTrue(self.wait(lambda: client.seen('release') == ['release'] and self.p.mode == 'desktop'))
        client.cursor = (X1 + 960, 0)
        self.assertTrue(self.wait(lambda: client.seen('grab') == ['grab', 'grab']))
        self.assertEqual(client.seen('show', 'hide'), ['show', 'hide'])  # never shown in between
        self.control.state.zoomed = True
        self.assertTrue(self.wait(lambda: client.seen('show', 'hide')[-1:] == ['show']))
        self.assertEqual(client.seen('remove', 'show', 'hide')[-2:], ['remove', 'show'])

    def test_a_config_reload_hides_it_again(self):
        client = RecordingClient()
        self.start(client)
        self.assertTrue(self.wait(lambda: client.seen('hide') == ['hide']))
        self.control.on_events(b'configreloaded>>\n')
        self.assertTrue(self.wait(lambda: client.seen('hide') == ['hide', 'hide']))
        self.control.on_events(b'configreloaded>>\n')  # within RELOAD_REAPPLY_S: waits, never a loop
        time.sleep(0.1)
        self.assertEqual(client.seen('hide'), ['hide', 'hide'])

    def test_run_end_in_free_mode_shows_it_after_removing_the_binds(self):
        client = RecordingClient(cursor=(X1 + 960, 0))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: client.seen('grab') == ['grab'] and client.seen('hide') == ['hide']))
        sampler.stop()
        self.assertEqual(client.seen('remove', 'show')[-2:], ['remove', 'show'])


class LuaStubTests(unittest.TestCase):
    """HIDE_LUA / SHOW_LUA with free mode's own Lua, in a real Lua interpreter against a stub `hl`."""
    STUB = r'''
local state = { invisible = false, x = 100, y = 200, binds = 0 }
hl = { dsp = { cursor = {}, exec_cmd = function(c) return c end } }
function hl.get_config(k) return state.invisible end
function hl.get_cursor_pos() return { x = state.x, y = state.y } end
function hl.config(t) state.invisible = t.cursor.invisible end
function hl.dsp.cursor.move(p) return p end
function hl.dispatch(p) state.x, state.y = p.x, p.y end
function hl.bind(chord, cmd, opts)
  state.binds = state.binds + 1
  return setmetatable({ remove = function() state.binds = state.binds - 1 end }, { __tostring = function() return "HL.Keybind" end })
end
local function show(tag) print(tag, state.invisible, state.binds) end
local function reset(invisible)
  xr_workspace_overview_cursor, xr_workspace_grab, xr_workspace_resize_binds = nil, nil, nil
  state.invisible, state.binds = invisible, 0
end
'''
    GRAB = fp.grab_pointer_lua(*BIND_ARGS, 3520, 540)
    HAND_BACK = fp.release_pointer_lua(4500, 30)
    REMOVE = resize.remove_resize_binds_lua()

    def run_lua(self, steps):
        """steps: (tag, lua) pairs, or a bare Lua statement; -> [(tag, invisible, binds)]."""
        if LUA is None: self.skipTest('no Lua interpreter')
        body = '\n'.join(s if isinstance(s, str) else f'do {s[1]} end show("{s[0]}")' for s in steps)
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'run.lua'
            script.write_text(self.STUB + body)
            done = subprocess.run([LUA, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        return [(t, i == 'true', int(b)) for t, i, b in (line.split('\t') for line in done.stdout.splitlines())]

    def test_hide_remembers_once_and_show_puts_it_back(self):
        out = self.run_lua(['reset(false)', ('hide', oc.HIDE_LUA), ('again', oc.HIDE_LUA), ('show', oc.SHOW_LUA),
                            ('show2', oc.SHOW_LUA), 'reset(true)', ('user_hide', oc.HIDE_LUA), ('user_show', oc.SHOW_LUA)])
        self.assertEqual(out, [('hide', True, 0), ('again', True, 0), ('show', False, 0), ('show2', False, 0),
                               ('user_hide', True, 0), ('user_show', True, 0)])  # a cursor the user hid stays hidden

    def test_free_mode_in_between_and_either_exit_order(self):
        for exit_steps in ([('remove', self.REMOVE), ('show', oc.SHOW_LUA)], [('show', oc.SHOW_LUA), ('remove', self.REMOVE)]):
            with self.subTest([t for t, _ in exit_steps]):
                out = self.run_lua(['reset(false)', ('hide', oc.HIDE_LUA), ('grab', self.GRAB), ('hand_back', self.HAND_BACK),
                                    ('grab2', self.GRAB)] + exit_steps)
                self.assertEqual([i for _, i, _ in out[:4]], [True] * 4)  # a hand-back into a desktop keeps it hidden
                self.assertEqual(out[4][1], True)  # the first of the two exit steps alone: still hidden
                self.assertEqual(out[5][1:], (False, 0))  # both: shown, binds gone

    def test_a_grab_before_the_hide_is_the_same(self):
        out = self.run_lua(['reset(false)', ('grab', self.GRAB), ('hide', oc.HIDE_LUA), ('hand_back', self.HAND_BACK),
                            ('show', oc.SHOW_LUA)])
        self.assertEqual([i for _, i, _ in out], [True, True, True, False])

    def test_a_show_during_a_grab_leaves_it_to_the_grab(self):
        out = self.run_lua(['reset(false)', ('hide', oc.HIDE_LUA), ('grab', self.GRAB), ('show', oc.SHOW_LUA),
                            ('remove', self.REMOVE)])
        self.assertEqual(out[2], ('show', True, 2))  # free mode is still on: its dot is the pointer
        self.assertEqual(out[3], ('remove', False, 0))

    def test_a_reload_then_hiding_again_keeps_the_cursors_own_state(self):
        out = self.run_lua(['reset(false)', ('hide', oc.HIDE_LUA), 'state.invisible = false',  # the reload showed it
                            ('rehide', oc.HIDE_LUA), ('show', oc.SHOW_LUA),
                            'reset(false)', ('hide2', oc.HIDE_LUA), 'xr_workspace_overview_cursor = nil state.invisible = false',
                            ('fresh', oc.HIDE_LUA), ('show2', oc.SHOW_LUA)])  # a reload that also cleared Lua's globals
        self.assertEqual([i for _, i, _ in out], [True, True, False, True, True, False])

    def test_a_dead_runs_stale_binds_give_the_cursor_back(self):
        resize_click = xr_control.REMOVE_RESIZE_BINDS + ' ' + xr_control.SHOW_OVERVIEW_CURSOR  # drop_stale_menu_binds' eval
        panel_key = xr_control.REMOVE_MENU_BINDS + ' ' + xr_control.SHOW_OVERVIEW_CURSOR
        out = self.run_lua(['reset(false)', ('hide', oc.HIDE_LUA), ('grab', self.GRAB), ('click', resize_click),
                            'reset(false)', ('hide2', oc.HIDE_LUA), ('key', panel_key)])
        self.assertEqual(out[2], ('click', False, 0))  # died in free mode: binds gone and the cursor back
        self.assertEqual(out[4], ('key', False, 0))  # died inside a desktop with a panel open
        self.assertEqual(self.run_lua(['reset(false)', ('hide', oc.HIDE_LUA), ('grab', self.GRAB),
                                       ('old', xr_control.REMOVE_RESIZE_BINDS)])[2], ('old', True, 0))  # why it is needed


@unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
class VerifyConfigTests(unittest.TestCase):
    """The same Lua against Hyprland's own `hl` (cursor.invisible read back with hl.get_config). `--verify-config` runs a
    throwaway config in a throwaway HOME and starts no compositor; no socket of the running desktop is used."""
    def test_hyprlands_own_cursor_setting_follows(self):
        # Without their warps: a cursor move in verify mode (no compositor) crashes Hyprland 0.56.2. Visibility is the same.
        grab, warp = fp.grab_pointer_lua(*BIND_ARGS, 3520, 540), view.warp_cursor_lua(3520, 540)
        self.assertTrue(grab.endswith(' ' + warp))
        grab, back, remove = grab[:-len(warp) - 1], fp.release_pointer_lua(), resize.remove_resize_binds_lua()
        stale = xr_control.REMOVE_RESIZE_BINDS + ' ' + xr_control.SHOW_OVERVIEW_CURSOR
        scenarios = {
            'session': [oc.HIDE_LUA, grab, back, grab, remove, oc.SHOW_LUA],
            'grab_first': [grab, oc.HIDE_LUA, back, oc.SHOW_LUA],
            'dead_run': [oc.HIDE_LUA, grab, stale],
        }
        with tempfile.TemporaryDirectory(prefix='xr-overview-cursor-') as tmp:
            out = Path(tmp) / 'out.tsv'
            lines = [f'local out = assert(io.open("{out}", "w"))',
                     'local function rec(tag) local ok, v = pcall(hl.get_config, "cursor.invisible") '
                     'out:write(tag, "\\t", tostring(ok and v), "\\n") end']
            for name, chunks in scenarios.items():
                lines.append(f'{resize.remove_resize_binds_lua()} xr_workspace_overview_cursor = nil '
                             'pcall(hl.config, { cursor = { invisible = false } })')
                lines += [f'do {c} end rec("{name}")' for c in chunks]
            lines.append('out:close()')
            harness = Path(tmp) / 'harness.lua'
            harness.write_text('\n'.join(lines) + '\n')
            env = {'HOME': tmp, 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'XDG_CONFIG_HOME': f'{tmp}/.config',
                   'XDG_STATE_HOME': f'{tmp}/.local/state', 'XDG_RUNTIME_DIR': tmp, 'LANG': 'C.UTF-8',
                   'HYPRLAND_INSTANCE_SIGNATURE': 'xr-overview-cursor-check'}
            r = subprocess.run([HYPRLAND, '--verify-config', '-c', str(harness)], env=env, cwd=tmp, timeout=60,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertTrue(out.exists(), r.stdout + r.stderr)
            got = {}
            for line in out.read_text().splitlines():
                name, value = line.split('\t')
                got.setdefault(name, []).append(value)
        self.assertIn('config ok', r.stdout)
        self.assertEqual(got, {'session': ['true', 'true', 'true', 'true', 'true', 'false'],
                               'grab_first': ['true', 'true', 'true', 'false'],
                               'dead_run': ['true', 'true', 'false']})


class WiringTests(unittest.TestCase):
    def test_lua_is_fixed_guarded_and_fits_an_eval(self):
        self.assertEqual(xr_control.SHOW_OVERVIEW_CURSOR, oc.SHOW_LUA)  # the stdlib-only helper's copy
        for lua in (oc.HIDE_LUA, oc.SHOW_LUA):
            self.assertNotIn('os.', lua); self.assertNotIn('io.', lua)
            self.assertEqual(lua.count('hl.config'), lua.count('pcall(hl.config,'))  # a refused change never breaks it
            self.assertEqual(lua.count('hl.get_config'), lua.count('pcall(hl.get_config,'))
            self.assertIn(oc.TABLE, lua); self.assertIn(resize.GRAB_TABLE, lua)
        for code in (xr_control.REMOVE_MENU_BINDS, xr_control.REMOVE_RESIZE_BINDS):
            self.assertLess(len('/eval ' + code + ' ' + xr_control.SHOW_OVERVIEW_CURSOR), 4096)

    def test_hyprland_client_sends_exactly_that_lua(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.hide_overview_cursor(); client.show_overview_cursor()
        self.assertEqual(sent, ['/eval ' + oc.HIDE_LUA, '/eval ' + oc.SHOW_LUA])

    def test_a_dead_runs_left_button_sends_the_show_after_its_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:  # a fake compositor socket in a throwaway runtime dir
            instance = 'a' * 40 + '_1_2'
            d = Path(tmp) / 'hypr' / instance; d.mkdir(parents=True)
            received = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(d / '.socket.sock')); server.listen(1)
                def serve():
                    conn, _ = server.accept()
                    with conn: received.append(conn.recv(8192)); conn.sendall(b'ok')
                t = threading.Thread(target=serve, daemon=True); t.start()
                env = {'XDG_RUNTIME_DIR': tmp, 'HYPRLAND_INSTANCE_SIGNATURE': instance}
                self.assertTrue(xr_control.drop_stale_menu_binds(env, code=xr_control.REMOVE_RESIZE_BINDS))
                t.join(2)
        self.assertEqual(received, [('/eval ' + xr_control.REMOVE_RESIZE_BINDS + ' ' + xr_control.SHOW_OVERVIEW_CURSOR).encode()])

    def test_view_controls_hides_it_on_a_desktop_and_shows_it_before_the_view_binds_go(self):
        from tools.native_spike import managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else
                                                          {'x': X1 + 960, 'y': 540} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json')) as control:
            control.set_overview_geometry(G)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.hide_overview_cursor.called:
                control.poll(); time.sleep(0.01)
            self.assertTrue(client.hide_overview_cursor.called)
            self.assertFalse(client.grab_pointer.called)  # inside a desktop: no binds, only the cursor hidden
        names = [c[0] for c in client.method_calls]
        self.assertLess(names.index('hide_overview_cursor'), len(names) - 1 - names[::-1].index('show_overview_cursor'))
        self.assertLess(len(names) - 1 - names[::-1].index('show_overview_cursor'), names.index('remove_view_binds'))


if __name__ == '__main__':
    unittest.main()
