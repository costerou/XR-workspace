"""Privacy mode (Ctrl+Alt+D): the main physical display DPMS off / on during a run, back on at run end and recovery.

Offline only: a fake Hyprland client, fake notify, the generated Lua run under stock Lua against a stand-in `hl` (the
dispatch order and the existence guard) and, when Hyprland is installed, built through `Hyprland --verify-config`
(nothing is dispatched there). No compositor, renderer or glasses.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, privacy, view, xr_control
from tools.native_spike.ipc import decode_control

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['XR-1', 'XR-2']
HYPRLAND = shutil.which('Hyprland')
LUA = shutil.which('lua') or shutil.which('lua5.4') or shutil.which('luajit')


def monitor(name, description='', dpms=True, **extra):
    return {'id': hash(name) & 0xff, 'name': name, 'description': description, 'dpmsStatus': dpms, 'disabled': False,
            'mirrorOf': 'none', **extra}


def desk(dp1=True):
    """Cody's set-up during a run: the Odyssey on DP-1 (fallback), the VITURE on DP-2 (glasses), XR-1 and XR-2."""
    return [monitor('DP-1', 'Samsung Electric Company Odyssey G93SD HCSY900228', dp1), monitor('DP-2', 'CVT VITURE 0x88888800'),
            monitor('XR-1'), monitor('XR-2')]


class Client:
    """Hyprland stand-in: `monitors`, and set_dpms changing them the way the generated Lua would."""
    def __init__(self, mons=None, obey=True):
        self.mons, self.obey, self.calls = mons if mons is not None else desk(), obey, []
        self.gate = None

    def query(self, kind, argument=None, timeout=2.0):
        if kind != 'monitors': raise ValueError(kind)
        return [dict(m) for m in self.mons]

    def set_dpms(self, name, on, keep=None):
        if self.gate is not None: self.gate.wait(5)
        (privacy.wake_lua(name) if on else privacy.blank_lua(name, keep))  # the real validation
        self.calls.append((name, on, keep))
        if self.obey:
            for m in self.mons:
                if m['name'] == name: m['dpmsStatus'] = on


class MainDisplayTests(unittest.TestCase):
    def test_the_fallback_is_the_main_display(self):
        self.assertEqual(privacy.main_display(desk(), 'DP-2', 'DP-1'), 'DP-1')

    def test_without_a_fallback_the_only_non_viture_non_xr_monitor(self):
        self.assertEqual(privacy.main_display(desk(), 'DP-2'), 'DP-1')
        # The glasses by description, even when they are not the run's output name.
        self.assertEqual(privacy.main_display(desk(), 'HDMI-A-9'), 'DP-1')

    def test_never_the_glasses_or_a_virtual_desktop(self):
        only_virtual = [monitor('DP-2', 'CVT VITURE'), monitor('XR-1'), monitor('XR-3')]
        self.assertIsNone(privacy.main_display(only_virtual, 'DP-2', 'XR-1'))
        self.assertIsNone(privacy.main_display([monitor('DP-1')], 'DP-1'))

    def test_two_candidates_and_no_fallback_among_them_blank_nothing(self):
        two = desk() + [monitor('HDMI-A-1', 'Dell')]
        self.assertIsNone(privacy.main_display(two, 'DP-2'))
        self.assertEqual(privacy.main_display(two, 'DP-2', 'HDMI-A-1'), 'HDMI-A-1')

    def test_disabled_mirrored_and_odd_names_are_skipped(self):
        self.assertIsNone(privacy.main_display([monitor('DP-1', disabled=True), monitor('DP-2', 'VITURE')], 'DP-2', 'DP-1'))
        self.assertIsNone(privacy.main_display([monitor('DP-1', mirrorOf='DP-2'), monitor('DP-2', 'VITURE')], 'DP-2', 'DP-1'))
        self.assertIsNone(privacy.main_display([monitor('WL-1'), monitor('DP-2', 'VITURE')], 'DP-2'))
        with self.assertRaises(ValueError): privacy.main_display(None, 'DP-2')


class LuaTests(unittest.TestCase):
    def test_blank_turns_the_display_off_then_the_glasses_on_and_only_if_both_exist(self):
        lua = privacy.blank_lua('DP-1', 'DP-2')
        self.assertIn('local t,g="DP-1","DP-2"', lua)
        self.assertLess(lua.index('action="off",monitor=t'), lua.index('action="on",monitor=g'))
        self.assertIn('if a and b then', lua)
        self.assertIn('if m.name==t then', privacy.wake_lua('DP-1'))
        self.assertEqual(privacy.WAKE_ALL_LUA, xr_control.WAKE_ALL_DISPLAYS)

    def test_names_are_validated(self):
        for bad in ('XR-1', 'DP-1"', 'DP-1 ', '', None, 'DP-1",monitor="DP-2', 'eDP-1x'):
            with self.subTest(bad):
                with self.assertRaises(ValueError): privacy.blank_lua(bad, 'DP-2')
                with self.assertRaises(ValueError): privacy.blank_lua('DP-1', bad)
                with self.assertRaises(ValueError): privacy.wake_lua(bad)
        with self.assertRaises(ValueError): privacy.blank_lua('DP-2', 'DP-2')

    def run_lua(self, code, names):
        stub = ('local log = {} hl = {dsp = {dpms = function(a) return a end}, '
                'dispatch = function(a) log[#log+1] = a.action .. ":" .. tostring(a.monitor) end, '
                'get_monitors = function() local r = {} for _, n in ipairs({' + ', '.join(f'"{n}"' for n in names) +
                '}) do r[#r+1] = {name = n} end return r end} ')
        out = subprocess.run([LUA, '-e', stub + code + ' print(table.concat(log, " "))'], capture_output=True, text=True,
                             timeout=10, check=True)
        return out.stdout.strip()

    @unittest.skipUnless(LUA, 'no lua interpreter')
    def test_the_generated_lua_dispatches_in_order_and_never_without_its_monitors(self):
        blank, wake = privacy.blank_lua('DP-1', 'DP-2'), privacy.wake_lua('DP-1')
        self.assertEqual(self.run_lua(blank, ['DP-1', 'DP-2', 'XR-1']), 'off:DP-1 on:DP-2')
        # A missing monitor would make Hyprland's selector apply to every monitor: nothing is dispatched instead.
        self.assertEqual(self.run_lua(blank, ['DP-2', 'XR-1']), '')
        self.assertEqual(self.run_lua(blank, ['DP-1', 'XR-1']), '')
        self.assertEqual(self.run_lua(wake, ['DP-1', 'DP-2']), 'on:DP-1')
        self.assertEqual(self.run_lua(wake, ['DP-2']), '')
        self.assertEqual(self.run_lua(privacy.WAKE_ALL_LUA, []), 'on:nil')

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_hyprland_builds_the_dpms_dispatchers_and_runs_the_generated_lua(self):
        # --verify-config has no monitors, so the generated Lua runs without dispatching; the dispatchers are built
        # explicitly to check the real API takes these arguments.
        with tempfile.TemporaryDirectory() as home:
            cfg, out = Path(home) / 'h.lua', Path(home) / 'out.txt'
            cfg.write_text(
                'local r = {}\n'
                'for _, a in ipairs({{action="off",monitor="DP-1"},{action="on",monitor="DP-2"},{action="on"}}) do\n'
                '  local ok, d = pcall(hl.dsp.dpms, a) r[#r+1] = tostring(ok) .. " " .. tostring(d) end\n'
                f'local ok1 = pcall(function() {privacy.blank_lua("DP-1", "DP-2")} end)\n'
                f'local ok2 = pcall(function() {privacy.wake_lua("DP-1")} end)\n'
                f'local f = io.open("{out}", "w") f:write(table.concat(r, ","), "|", tostring(ok1), tostring(ok2)) f:close()\n')
            subprocess.run([HYPRLAND, '--verify-config', '-c', str(cfg)], env={'HOME': home, 'XDG_RUNTIME_DIR': home,
                           'PATH': '/usr/bin:/bin', 'HYPRLAND_INSTANCE_SIGNATURE': 'xr-privacy-test'}, cwd=home, timeout=60,
                           stdin=subprocess.DEVNULL, capture_output=True)
            self.assertEqual(out.read_text(), ','.join(['true HL.Dispatcher'] * 3) + '|truetrue')


class ToggleTests(unittest.TestCase):
    def make(self, client=None, watch=60):
        notes = []
        return privacy.PrivacyDisplay(client or Client(), 'DP-2', 'DP-1', notify=notes.append, watch=watch), notes

    def test_toggle_blanks_then_restores_the_main_display(self):
        p, _ = self.make()
        self.assertIn('Privacy on: DP-1 is off', p.toggle())
        self.assertEqual(p.client.calls, [('DP-1', False, 'DP-2')])
        self.assertEqual(p.blanked, 'DP-1')
        self.assertIs(privacy.dpms_status(p.client.mons, 'DP-1'), False)
        self.assertIs(privacy.dpms_status(p.client.mons, 'DP-2'), True)  # the glasses stay on
        self.assertEqual(p.toggle(), 'Privacy off: DP-1 is back on.')
        self.assertEqual(p.client.calls[-1], ('DP-1', True, None))
        self.assertIsNone(p.blanked)

    def wait_for(self, check, timeout=5):
        end = time.monotonic() + timeout
        while not check():
            if time.monotonic() > end: self.fail('timed out')
            time.sleep(0.005)

    def test_a_display_something_else_woke_is_blanked_again_by_the_watcher(self):
        p, _ = self.make(watch=0.01)
        p.toggle()
        p.client.mons[0]['dpmsStatus'] = True  # an idle daemon's resume, or the monitor back after a hotplug
        self.wait_for(lambda: len(p.client.calls) == 2)
        self.assertEqual(p.client.calls[1], ('DP-1', False, 'DP-2'))
        self.assertIs(privacy.dpms_status(p.client.mons, 'DP-1'), False)
        self.assertEqual(p.blanked, 'DP-1')
        self.assertEqual(p.toggle(), 'Privacy off: DP-1 is back on.')  # the next press still means privacy off
        self.wait_for(lambda: p._watcher is None)
        p.client.mons[0]['dpmsStatus'] = True
        time.sleep(0.05)
        self.assertEqual(len(p.client.calls), 3)  # privacy off: nothing watches any more
        p.close()

    def test_the_watcher_survives_compositor_errors_and_stops_at_close(self):
        p, _ = self.make(watch=0.01)
        p.toggle()
        real = p.client.query
        p.client.query = mock.Mock(side_effect=OSError('busy'))
        time.sleep(0.05)
        p.client.query = real
        p.client.mons[0]['dpmsStatus'] = True
        self.wait_for(lambda: len(p.client.calls) == 2)
        p.close()
        self.assertFalse(p._watcher.is_alive())
        self.assertEqual(p.client.calls[-1], ('DP-1', True, None))

    def test_the_first_press_means_privacy_on_even_if_the_display_is_asleep(self):
        p, _ = self.make(Client(desk(dp1=False)))
        self.assertIn('Privacy on: DP-1 is off', p.toggle())
        self.assertEqual(p.client.calls, [('DP-1', False, 'DP-2')])
        p.close()

    def test_refusals(self):
        p, _ = self.make(Client([monitor('DP-2', 'VITURE'), monitor('XR-1')]))
        self.assertEqual(p.toggle(), 'No single main display found to turn off.')
        self.assertEqual(p.client.calls, [])
        p, _ = self.make(Client(obey=False))
        self.assertEqual(p.toggle(), 'DP-1 did not turn off.')
        self.assertIsNone(p.blanked)

    def test_request_runs_off_the_loop_and_notifies(self):
        p, notes = self.make()
        p.request()
        for t in p._threads: t.join(5)
        self.assertEqual(len(notes), 1); self.assertIn('Privacy on', notes[0])

    def test_close_turns_the_display_back_on_waits_for_a_toggle_in_flight_and_refuses_later_ones(self):
        client = Client()
        client.gate = threading.Event()
        p, notes = self.make(client)
        p.request()                                   # blocks in set_dpms until the gate opens
        closer = threading.Thread(target=p.close); closer.start()
        closer.join(0.2); self.assertTrue(closer.is_alive())  # close() waits for the toggle in flight
        client.gate.set(); closer.join(5)
        self.assertEqual(client.calls, [('DP-1', False, 'DP-2'), ('DP-1', True, None)])
        self.assertIs(privacy.dpms_status(client.mons, 'DP-1'), True)
        p.request()
        for t in p._threads: t.join(5)
        self.assertEqual(len(client.calls), 2)

    def test_close_without_a_blank_touches_nothing(self):
        p, _ = self.make()
        p.close()
        self.assertEqual(p.client.calls, [])

    def test_wake_for_recovery(self):
        client = Client(desk(dp1=False))
        self.assertTrue(privacy.wake(client, 'DP-1'))
        self.assertEqual(client.calls, [('DP-1', True, None)])
        self.assertFalse(privacy.wake(client, 'DP-1'))  # already on: nothing sent
        self.assertFalse(privacy.wake(object(), 'DP-1'))  # a client that cannot
        self.assertFalse(privacy.wake(client, None))
        broken = Client(desk(dp1=False)); broken.query = mock.Mock(side_effect=OSError('gone'))
        self.assertFalse(privacy.wake(broken, 'DP-1'))


class HyprlandClientTests(unittest.TestCase):
    def test_set_dpms_sends_only_generated_lua(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        sent = []
        client._request = lambda req, timeout=h.DEADLINE: sent.append(req) or b'ok'
        client.set_dpms('DP-1', False, keep='DP-2')
        client.set_dpms('DP-1', True)
        self.assertEqual(sent, ['/eval ' + privacy.blank_lua('DP-1', 'DP-2'), '/eval ' + privacy.wake_lua('DP-1')])
        with self.assertRaises(ValueError): client.set_dpms('DP-1', False)
        with self.assertRaises(ValueError): client.set_dpms('XR-1', True)
        client._request = lambda req, timeout=h.DEADLINE: b'error'
        with self.assertRaises(ValueError): client.set_dpms('DP-1', True)
        # Not a generic mutation: the allowlist still refuses dpms dispatches.
        with self.assertRaises(ValueError): h.HyprlandClient._check_mutation('dispatch dpms off DP-1')


class GuardAndRecoveryTests(unittest.TestCase):
    class Raw:
        def __init__(self, mons): self.mons = mons
        def verified_session(self): return {'id': 'session'}
        def query(self, kind, *a, **k): return self.mons

    def raw_monitors(self, dp1=True, dp2=True):
        from test_hyprland import RawFixture
        base = RawFixture().monitors[0]
        return [dict(base, id=0, name='DP-1', dpmsStatus=dp1), dict(base, id=1, name='DP-2', x=1920, dpmsStatus=dp2)]

    def test_the_guard_lets_the_fallback_sleep_but_not_the_glasses(self):
        from tools.native_spike.supervisor import validate_physical_pair
        validate_physical_pair(self.Raw(self.raw_monitors(dp1=False)), 'session', 'DP-2', 'DP-1', recovery_asleep_ok=True)
        with self.assertRaises(ValueError):
            validate_physical_pair(self.Raw(self.raw_monitors(dp1=False)), 'session', 'DP-2', 'DP-1')
        with self.assertRaises(ValueError):
            validate_physical_pair(self.Raw(self.raw_monitors(dp2=False)), 'session', 'DP-2', 'DP-1', recovery_asleep_ok=True)

    def test_the_managed_guard_allows_privacy_mode(self):
        from tools.native_spike import managed
        broker = mock.Mock(approved_physical='DP-1', session='session')
        with mock.patch.object(managed, 'validate_physical_pair') as check:
            managed.ManagedGuard(broker, 'DP-2', [])()
        self.assertEqual(check.call_args.kwargs, {'recovery_asleep_ok': True})

    def test_recovery_turns_the_fallback_back_on_before_restoring(self):
        from tools.native_spike import managed
        client = Client(desk(dp1=False))
        journal = mock.Mock(broker={'recovery_output': 'DP-1'}, output_names=list)
        order = []
        with mock.patch('tools.native_spike.managed.recover', side_effect=lambda j, b: order.append('recover') or
                        mock.Mock(complete=False, reasons=[])), mock.patch('tools.native_spike.restore.load', side_effect=OSError):
            managed.recover_restoring(client, journal, None, 'run')
        self.assertEqual(client.calls, [('DP-1', True, None)])
        self.assertEqual(order, ['recover'])
        self.assertIs(privacy.dpms_status(client.mons, 'DP-1'), True)

    def test_crash_recovery_wakes_the_fallback_before_its_physical_check(self):
        from tools.native_spike import managed
        seen = []
        with mock.patch.object(managed, 'validate_physical_pair', side_effect=lambda *a, **k: seen.append('check') or
                               (_ for _ in ()).throw(ValueError('stop here'))), \
                mock.patch('tools.native_spike.privacy.wake', side_effect=lambda c, n: seen.append(('wake', n))), \
                mock.patch.object(managed, 'token', return_value='run'):
            client = mock.Mock(runtime_dir=Path('/r'))
            client.verified_session.return_value = {'id': 'session'}
            path = Path('/r/xr-workspace-spike/run/journal.json')
            with self.assertRaises(ValueError):
                managed.recover_managed(client, {'run_id': 'run', 'output': 'DP-2', 'recovery_output': 'DP-1'}, path)
        self.assertEqual(seen, [('wake', 'DP-1'), 'check'])


class WiringTests(unittest.TestCase):
    def control(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        c.privacy = mock.Mock()
        return c

    def test_bind_help_row_and_wire(self):
        self.assertIn(('CTRL + ALT + D', 'privacy_display'), view.BINDS)
        self.assertEqual(dict(view.help_rows())['Ctrl+Alt+D'], 'Privacy: main display off / on')
        self.assertIn(privacy.PRIVACY_OP, xr_control.OPS)
        frame = (b'{"schema":1,"request_id":7,"run_id":"run","session":"s","op":"privacy_display","payload":{}}\n')
        self.assertEqual(decode_control(frame)['op'], 'privacy_display')
        self.assertNotIn(privacy.PRIVACY_OP, view.USER_OPS)  # not a panel capability
        self.assertIn('{"Ctrl+Alt+D", "Privacy: main display off / on"}', (ROOT / 'spike/tests/help_card_test.cpp').read_text())

    def test_the_op_reaches_the_worker_and_not_in_typing_mode(self):
        c = self.control()
        self.assertIsNone(c.on_message({'op': 'privacy_display', 'payload': {}}))
        c.privacy.request.assert_called_once_with()
        c.state.typing = True
        c.on_message({'op': 'privacy_display', 'payload': {}})
        self.assertEqual(c.privacy.request.call_count, 1)

    def test_it_works_while_a_video_plays(self):
        from tools.native_spike.video import IGNORED_WHILE_PLAYING
        self.assertNotIn(privacy.PRIVACY_OP, IGNORED_WHILE_PLAYING)

    def test_no_run_listening_turns_every_display_on(self):
        with mock.patch.object(xr_control, '_eval_stale', return_value=True) as stale:
            self.assertEqual(xr_control.main(['x', '/nonexistent/control.sock', 'run', 's', 'privacy_display']), 1)
        stale.assert_called_once()
        self.assertEqual(stale.call_args.args[1], xr_control.WAKE_ALL_DISPLAYS)

    def test_bind_check_covers_it(self):
        self.assertIn(('run', 'CTRL + ALT + D', 'privacy_display'), bind_check.run_chords())
        # Omarchy's Super+Ctrl+D and Super+Ctrl+Alt+D are other chords.
        self.assertEqual(bind_check.clashes([('desktop', 'bind', 'SUPER + CTRL + D'), ('desktop', 'bind', 'SUPER + CTRL + ALT + D')]), [])
        self.assertEqual(len(bind_check.clashes([('desktop', 'bind', 'ctrl + alt + d')])), 1)

    def test_view_controls_builds_and_closes_it(self):
        src = (ROOT / 'tools/native_spike/managed.py').read_text()
        self.assertLess(src.index('control.privacy.close()'), src.index('client.remove_view_binds()'))
        self.assertIn('PrivacyDisplay(client, output, fallback)', src)


if __name__ == '__main__':
    unittest.main()
