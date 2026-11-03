"""A desktop that shrinks pulls its floating windows back inside it (tools/native_spike/fit_windows.py).

Offline: the plan from Hyprland-shaped JSON, the generated Lua run in a real interpreter against a stand-in for Hyprland's
dispatchers, and the worker through test_desktops' fake broker with a fake window client. Nothing talks to a compositor.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

from tools.native_spike import desktops as dk, fit_windows as fw, geometry as g
from tools.native_spike.hyprland import HyprlandClient, plan_outputs
from test_desktops import D169, FLAT, WIDE, Run

ROOT = Path(__file__).resolve().parents[2]
LUA = shutil.which('lua5.4') or shutil.which('lua')
XR2 = {'name': 'XR-2', 'width': 1280, 'height': 720, 'scale': 1.0, 'x': 1920, 'y': 0}  # just shrunk from 1920x1080
WORKSPACES = [{'id': 41, 'monitor': 'XR-2'}, {'id': 42, 'monitor': 'XR-2'}, {'id': -98, 'monitor': 'XR-2'},
              {'id': 51, 'monitor': 'XR-3'}]
MONITORS = [{'name': 'XR-2', 'reserved': [0, 0, 0, 0]}, {'name': 'XR-3', 'reserved': [0, 0, 0, 0]}]


def window(address='0xa', at=(2900, 500), size=(600, 400), workspace=41, **more):
    return {'address': address, 'at': list(at), 'size': list(size), 'workspace': {'id': workspace}, 'floating': True,
            'mapped': True, 'hidden': False, 'fullscreen': 0, 'pinned': False, **more}


def plan(*clients, spec=XR2, monitors=MONITORS):
    return fw.plan(list(clients), WORKSPACES, monitors, spec)


class PlanTests(unittest.TestCase):
    def test_a_window_sticking_out_moves_the_least_it_takes(self):
        # XR-2 is now x 1920..3200, y 0..720. Sticking out to the right by 300 and down by 180.
        self.assertEqual(plan(window()), [{'address': '0xa', 'x': 2600, 'y': 320, 'w': 0, 'h': 0}])
        self.assertEqual(plan(window(at=(1000, 100))), [{'address': '0xa', 'x': 1920, 'y': 100, 'w': 0, 'h': 0}])  # out to the left
        self.assertEqual(plan(window(at=(2000, 500))), [{'address': '0xa', 'x': 2000, 'y': 320, 'w': 0, 'h': 0}])  # one axis only

    def test_a_window_inside_stays(self):
        self.assertEqual(plan(window(at=(2000, 100)), window('0xb', at=(1920, 0), size=(1280, 720))), [])

    def test_a_window_bigger_than_the_desktop_also_shrinks(self):
        self.assertEqual(plan(window(at=(2000, 100), size=(1800, 1000))), [{'address': '0xa', 'x': 1920, 'y': 0, 'w': 1280, 'h': 720}])
        self.assertEqual(plan(window(at=(2000, 100), size=(1800, 500))), [{'address': '0xa', 'x': 1920, 'y': 100, 'w': 1280, 'h': 500}])

    def test_it_lands_clear_of_the_bar(self):
        bar = [{'name': 'XR-2', 'reserved': [0, 26, 0, 0]}]
        self.assertEqual(plan(window(at=(2900, -50), size=(600, 100)), monitors=bar),
                         [{'address': '0xa', 'x': 2600, 'y': 26, 'w': 0, 'h': 0}])
        self.assertEqual(plan(window(at=(2000, 100), size=(600, 900)), monitors=bar),
                         [{'address': '0xa', 'x': 2000, 'y': 26, 'w': 600, 'h': 694}])
        self.assertEqual(plan(window(at=(2000, 10), size=(100, 100)), monitors=bar), [])  # inside, if under the bar: left alone

    def test_nonsense_reserved_areas_mean_the_whole_output(self):
        for reserved in ([0, 800, 0, 0], [1, 2], 'top', [-5, 0, 0, 0], None):
            with self.subTest(reserved):
                got = plan(window(), monitors=[{'name': 'XR-2', 'reserved': reserved}])
                self.assertEqual(got, [{'address': '0xa', 'x': 2600, 'y': 320, 'w': 0, 'h': 0}])
        self.assertEqual(fw.free_area(XR2, [])[1], (1920, 0, 1280, 720))

    def test_logical_pixels_at_a_scale(self):
        spec = {'name': 'XR-2', 'width': 3840, 'height': 2160, 'scale': 2.0, 'x': 1920, 'y': 0}  # 1920x1080 logical
        self.assertEqual(plan(window(at=(3400, 800)), spec=spec), [{'address': '0xa', 'x': 3240, 'y': 680, 'w': 0, 'h': 0}])
        spec = {'name': 'XR-2', 'width': 2560, 'height': 1440, 'scale': 1.25, 'x': 0, 'y': 100}  # 2048x1152
        self.assertEqual(plan(window(at=(1800, 1000)), spec=spec), [{'address': '0xa', 'x': 1448, 'y': 852, 'w': 0, 'h': 0}])

    def test_every_workspace_of_that_output_but_nothing_else(self):
        got = plan(window('0xa'), window('0xb', workspace=42),  # shown or not
                   window('0xc', workspace=51), window('0xd', workspace=-98), window('0xe', workspace=7))
        self.assertEqual([m['address'] for m in got], ['0xa', '0xb'])

    def test_only_floating_windows_that_can_move(self):
        skipped = (window(floating=False), window(fullscreen=1), window(fullscreen=2), window(mapped=False), window(hidden=True),
                   window(address='bad'), window(address='0xABC'), window(address=7), window(at=(1, 2, 3)), window(size=(0, 100)),
                   window(size=(100, -1)), {**window(), 'workspace': None}, {**window(), 'at': None}, 'not a window', None,
                   window(at=(float('nan'), 1)), window(at=(True, 1)))
        for c in skipped:
            with self.subTest(c): self.assertEqual(plan(c), [])
        self.assertEqual(len(plan(window(pinned=True))), 1)  # a pinned window is floating too, and on this output

    def test_a_workspace_list_that_is_not_one_is_refused(self):
        for bad in ((None, WORKSPACES, MONITORS), ([], {}, MONITORS), ([], WORKSPACES, 'x')):
            with self.subTest(bad), self.assertRaises(ValueError): fw.plan(*bad, XR2)
        self.assertEqual(fw.plan([window()], [{'id': 'x', 'monitor': 'XR-2'}, None, {'id': True, 'monitor': 'XR-2'}], MONITORS, XR2), [])


class ShrunkTests(unittest.TestCase):
    """Which desktops a change shrinks (dk.Plan.shrunk): their logical size got smaller, whatever made it."""
    def setUp(self):
        self.specs = plan_outputs([], [], 'run')

    def test_resolution(self):
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(1280, 720)).shrunk, ['XR-2'])
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(2560, 1440)).shrunk, [])
        # Wider but shorter, and the other way round: a window can stick out either way.
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(1680, 1050)).shrunk, ['XR-2'])
        specs = plan_outputs([], [], 'run', [D169, {'width': 2560, 'height': 1600, 'scale': 1.0}, D169])
        self.assertEqual(dk.plan(specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(3440, 1440)).shrunk, ['XR-2'])

    def test_ultrawide_off_and_scale_up(self):
        specs = plan_outputs([], [], 'run', [D169, WIDE, D169])
        self.assertEqual(dk.plan(specs, [FLAT] * 3, 'ultrawide', 1).shrunk, ['XR-2'])
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, 'ultrawide', 1).shrunk, [])
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, dk.SCALE, 0, scale=1.5).shrunk, ['XR-1'])
        scaled = plan_outputs([], [], 'run', [{**D169, 'scale': 1.5}, D169, D169])
        self.assertEqual(dk.plan(scaled, [FLAT] * 3, dk.SCALE, 0, scale=1.0).shrunk, [])

    def test_a_neighbour_that_only_slides_is_not_shrunk_and_add_or_remove_shrink_nothing(self):
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, dk.RESOLUTION, 0, size=(1280, 720)).shrunk, ['XR-1'])
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, 'remove', 1).shrunk, [])
        self.assertEqual(dk.plan(self.specs, [FLAT] * 3, 'add', 1).shrunk, [])


class FakeWindows:
    """The Hyprland client as DesktopManager uses it: three queries and one eval."""
    def __init__(self, transport, clients):
        self.transport, self.clients, self.fits, self.calls, self.fail = transport, clients, [], [], None

    def query(self, kind):
        self.calls.append((kind, len([e for e in self.transport.events if e == 'reshape'])))
        if self.fail == kind: raise OSError('compositor gone')
        return {'clients': self.clients, 'workspaces': WORKSPACES, 'monitors': MONITORS}[kind]

    def fit_windows(self, moves):
        if self.fail == 'fit': raise ValueError('compositor rejected')
        self.fits.append(moves)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(self)
        self.windows = self.run.manager.windows = FakeWindows(self.run.transport, [window()])

    def change(self, action=dk.RESOLUTION, index=1, **kw):
        kw = kw or ({'size': (1280, 720)} if action == dk.RESOLUTION else {})
        self.assertTrue(self.run.manager.start(dk.plan(self.run.manager.specs, [FLAT] * 3, action, index, **kw)))
        return self.run.manager.take_result()

    def test_a_shrink_pulls_its_windows_in_after_every_broker_step(self):
        _, error, done = self.change()
        self.assertIsNone(error)
        self.assertEqual(done, ['reshape', 'reshape'])  # XR-2 shrank, XR-3 slid
        self.assertEqual({n for _, n in self.windows.calls}, {2})  # read after both, where Hyprland has them now
        self.assertEqual(self.windows.fits, [[{'address': '0xa', 'x': 2600, 'y': 320, 'w': 0, 'h': 0}]])
        self.assertEqual((self.run.manager.fitted, self.run.manager.fit_failures), (1, 0))

    def test_ultrawide_off_and_a_larger_scale_do_too(self):
        self.change('ultrawide', 1); self.assertEqual(self.windows.fits, [])  # grew: nothing to pull in
        self.windows.clients = [window(at=(3700, 100), size=(800, 400))]
        self.change('ultrawide', 1)
        self.assertEqual(self.windows.fits[-1], [{'address': '0xa', 'x': 3040, 'y': 100, 'w': 0, 'h': 0}])
        self.windows.clients = [window(at=(2800, 100))]
        self.change(dk.SCALE, 1, scale=2.0)  # 960x540 logical
        self.assertEqual(self.windows.fits[-1], [{'address': '0xa', 'x': 2280, 'y': 100, 'w': 0, 'h': 0}])

    def test_a_change_that_shrinks_nothing_does_not_even_ask(self):
        self.change(dk.RESOLUTION, 1, size=(2560, 1440))
        self.assertEqual((self.windows.calls, self.windows.fits), ([], []))

    def test_nothing_sticking_out_nothing_sent(self):
        self.windows.clients = [window(at=(2000, 100), size=(200, 200))]
        self.change()
        self.assertEqual(self.windows.fits, [])

    def test_a_refused_change_moves_no_window(self):
        self.run.transport.refuse_reshape = True
        _, error, _ = self.change()
        self.assertIsNotNone(error)
        self.assertEqual((self.windows.calls, self.windows.fits), ([], []))

    def test_a_window_failure_never_fails_a_change_that_worked(self):
        for kind in ('clients', 'workspaces', 'monitors', 'fit'):
            with self.subTest(kind):
                self.setUp()
                self.windows.fail = kind
                self.assertIsNone(self.change()[1])
                self.assertEqual((self.run.manager.desktops()[1]['width'], self.run.manager.fit_failures), (1280, 1))
                self.assertEqual(self.windows.fits, [])

    def test_no_client_no_pull(self):
        self.run.manager.windows = None
        self.assertIsNone(self.change()[1])

    def test_a_run_that_is_ending_moves_nothing(self):
        change = dk.plan(self.run.manager.specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(1280, 720))
        self.run.manager._stop = True
        self.run.manager._fit_windows(change)
        self.assertEqual((self.windows.calls, self.windows.fits), ([], []))

    def test_the_run_gives_the_manager_its_client(self):
        source = (ROOT / 'tools/native_spike/managed.py').read_text()
        self.assertIn("windows=client if hasattr(client, 'fit_windows') else None", source)


class LuaTests(unittest.TestCase):
    MOVES = [{'address': '0xa', 'x': 2600, 'y': 320, 'w': 0, 'h': 0}, {'address': '0xbad', 'x': 1, 'y': 2, 'w': 3, 'h': 4},
             {'address': '0xb', 'x': -10, 'y': 26, 'w': 1280, 'h': 694}]

    def test_only_validated_plans_reach_lua(self):
        self.assertEqual(fw.lua([]), [])
        ok = self.MOVES[0]
        for bad in (None, 'x', [None], [{}], [{**ok, 'more': 1}], [{**ok, 'address': 'x"); os.exit() --'}], [{**ok, 'x': 1.5}],
                    [{**ok, 'y': True}], [{**ok, 'w': -1, 'h': 5}], [{**ok, 'w': 5}], [{**ok, 'h': 5}], [{**ok, 'x': 1 << 40}]):
            with self.subTest(bad), self.assertRaises(ValueError): fw.lua(bad)

    def test_many_windows_stay_under_the_clients_eval_limit(self):
        worst = {'address': '0x' + 'f' * 16, 'x': -2147483647, 'y': -2147483647, 'w': 2147483647, 'h': 2147483647}
        codes = fw.lua([worst] * (fw.BATCH * 2 + 1))
        self.assertEqual(len(codes), 3)
        self.assertLessEqual(max(len(c) for c in codes), 4096)

    def test_the_client_evals_them(self):
        client = HyprlandClient.__new__(HyprlandClient)
        sent = []
        client._request = lambda request: sent.append(request) or b'ok'
        client.fit_windows(self.MOVES)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith('/eval local M={{"0xa",2600,320,0,0}'))
        with self.assertRaises(ValueError): client.fit_windows([{'address': 'nope'}])

    @unittest.skipUnless(LUA, 'needs a Lua interpreter')
    def test_it_runs_resize_then_move_and_one_bad_window_stops_nothing(self):
        harness = '''
hl = {dsp = {window = {resize = function(a) return {"resize", a} end, move = function(a) return {"move", a} end}}}
function hl.dispatch(c)
  if c[2].window == "address:0xbad" then error("no such window") end
  print(c[1], c[2].window, c[2].x, c[2].y, c[2].relative)
end
'''
        lines = []
        for code in fw.lua(self.MOVES):
            done = subprocess.run([LUA, '-'], input=harness + code, capture_output=True, text=True, timeout=10)
            self.assertEqual(done.returncode, 0, done.stderr)
            lines += [line.split('\t') for line in done.stdout.splitlines()]
        self.assertEqual(lines, [['move', 'address:0xa', '2600', '320', 'nil'],
                                 ['resize', 'address:0xb', '1280', '694', 'nil'],  # about its centre, so before the move
                                 ['move', 'address:0xb', '-10', '26', 'nil']])


class TableTests(unittest.TestCase):
    def test_geometry_helpers(self):
        self.assertEqual(g.logical_height({'width': 2560, 'height': 1440, 'scale': 1.25}), 1152)
        self.assertEqual(g.logical_width({'width': 2560, 'height': 1440, 'scale': 1.25}), 2048)


if __name__ == '__main__':
    unittest.main()
