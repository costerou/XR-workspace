"""The Step-Back Band through the real sampler feed and click route, offline, on a small hand-made surface."""
import copy
import math
import unittest
from tools.native_spike import free_pointer as fp, restore_band as rb, view, unified_cursor as uc

OUTPUT = dict(index=1, x=1000, y=0, width=800, height=600)
TOP = (1400, 0)


def geometry():
    # A surface stepped back to half size: picture 200..600 x 150..450 on an 800 x 600 canvas, the pill above it.
    return dict(monitor='XR-3', maximized=True, canvas=[800, 600, 1, .75], tile=[0, 0, 800, 600], placement=[0, 0, 2, 0, 0], rect=[350, -100, 100, 50],
                picture=[200, 150, 600, 450], hit_mesh=[[-.125, .625, -1], [-.125, .5, -1], [.125, .625, -1], [.125, .5, -1]], view=[1, 0, 0, 0, 1, 0, 0, 0, 1])


def settle(p, cursor=TOP, start=0.0, seconds=rb.DWELL_S + .02):
    for i in range(round(seconds / .01) + 1):
        p.feed(cursor, (0, 0), [OUTPUT], p.want[0]); p.poll(start + i * .01)


class RestoreTests(unittest.TestCase):
    def control(self):
        c = view.ViewControl(view.ViewState(['XR-1', 'XR-3']), 'run', 'session', lambda n: None, None, None)
        c.state.selected = 1; c.state.apply('zoom_in')
        p = fp.OverviewPointer(c); c.overview_pointer = p
        self.addCleanup(p.close)
        c.set_maximize_geometry(geometry())
        return c, p

    def on_restore(self, p):
        settle(p)
        p.band_point = (400, 75)
        p.feed(TOP, (0, 0), [OUTPUT], p.want[0]); p.poll(1)
        self.assertTrue(p.band_hot())

    def test_a_rest_on_the_edge_hands_over_and_coming_down_hands_back(self):
        c, p = self.control()
        p.feed((1100, 100), (0, 0), [OUTPUT], 0); p.poll(0)
        self.assertNotEqual(p.want[1], 'free')
        settle(p)
        self.assertEqual((p.want[1], p.mode), ('free', 'restore'))
        p.feed(TOP, (0, 300), [OUTPUT], p.want[0]); p.poll(1)
        self.assertEqual(p.want[1], 'desktop')
        self.assertEqual(p.want[2][1], rb.LAND_PX)

    def test_press_release_runs_real_restore_preserving_selection_and_placements(self):
        c, p = self.control(); original = copy.deepcopy(c.state.placements)
        self.on_restore(p)
        p.on_op('resize_press')
        self.assertTrue(c.state.zoomed)
        result = p.on_op('resize_release')
        self.assertFalse(c.state.zoomed)
        self.assertEqual(c.state.selected, 1)
        self.assertEqual(c.state.placements, original)
        self.assertIn(b'"zoomed":false', result)

    def test_removed_or_changed_geometry_cancels_held_restore(self):
        c, p = self.control()
        self.on_restore(p); p.on_op('resize_press')
        c.set_maximize_geometry(None)
        p.on_op('resize_release')
        self.assertTrue(c.state.zoomed)
        self.assertNotEqual(p.mode, 'restore')

    def test_no_output_pixel_is_on_the_pill(self):
        c, p = self.control()
        for y in (1, 100, 300, 599):
            p.feed((1400, y), (0, 0), [OUTPUT], p.want[0]); p.poll(y)
            self.assertNotEqual(p.want[1], 'free')

    def test_malformed_geometry_rejected(self):
        for patch in ({'extra': 1}, {'view': [1] * 9}, {'hit_mesh': [[float('nan'), 0, 1]]}, {'monitor': '0'}, {'picture': [1, 2]}):
            with self.assertRaises(ValueError): fp.parse_maximize_geometry(dict(geometry(), **patch))
        g = geometry(); del g['picture']
        with self.assertRaises(ValueError): fp.parse_maximize_geometry(g)


class RestoreSamplerTests(unittest.TestCase):
    def test_sampler_grabs_only_after_the_rest_and_removes_the_grab_on_the_way_back(self):
        c = view.ViewControl(view.ViewState(['XR-1', 'XR-3']), 'run', 'session', lambda n: None, None, None)
        c.state.selected = 1; c.state.apply('zoom_in')
        p = uc.UnifiedPointer(c); c.overview_pointer = p; self.addCleanup(p.close)
        c.set_maximize_geometry(geometry())

        class Client:
            def __init__(self): self.position = (1100, 100); self.calls = []
            def query(self, *args, **kwargs): return dict(x=self.position[0], y=self.position[1])
            def grab_pointer(self, *args): self.calls.append(('grab', args)); self.position = args[-2:]
            lock_pointer = grab_pointer
            def release_pointer(self, *args): self.calls.append(('release', args)); self.position = args or self.position
            def remove_resize_binds(self): self.calls.append(('remove',))
            def arm_click_binds(self, *args): self.calls.append(('arm',))
            def warp_cursor(self, *args): self.position = args
        client = Client(); sampler = uc.UnifiedSampler(client, ['XR-1', 'XR-3'], p, ())
        self.assertFalse(sampler.sample_restore([OUTPUT])); p.poll(1)
        self.assertEqual(client.calls, [])
        client.position = TOP
        for i in range(30): sampler.sample_restore([OUTPUT]); p.poll(2 + i * .01)
        self.assertEqual(client.calls, [])                  # the grab comes after the rest
        for i in range(30, 38): sampler.sample_restore([OUTPUT]); p.poll(2 + i * .01)
        self.assertEqual(p.mode, 'restore')
        for i in range(3): sampler.sample_restore([OUTPUT]); p.poll(3 + i * .01)
        self.assertEqual(client.calls[0][0], 'grab')
        parked = client.position
        client.position = (parked[0], parked[1] + 150)
        for i in range(3): sampler.sample_restore([OUTPUT]); p.poll(4 + i * .01)
        self.assertEqual(client.calls[-1][0], 'release')
        self.assertEqual(client.calls[-1][1][1], rb.LAND_PX)
        self.assertFalse(sampler.grabbed)

    def test_the_view_turns_the_dot_with_the_desktop(self):
        g = fp.parse_maximize_geometry(geometry())
        g['view'] = [0, 0, 1, 0, 1, 0, -1, 0, 0]    # a surface placed round to world +X, the view turned onto it
        ray = fp.band_ray(g, 400, 300)
        self.assertAlmostEqual(ray[0], 1)
        self.assertAlmostEqual(ray[2], 0)


class ClickBinds(unittest.TestCase):
    def test_arm_needs_the_non_consuming_binds_only(self):
        from tools.native_spike.unified_cursor import click_binds_lua
        lua = click_binds_lua('/usr/bin/python3', '/x/client.py', '/run/c.sock', 'run', 'session')
        self.assertEqual(lua.count('non_consuming = true'), 2)
        self.assertNotIn('hook', lua)


class RestoreEpochTests(unittest.TestCase):
    def open(self):
        c = view.ViewControl(view.ViewState(['XR-3']), 'run', 'session', None, None, None)
        c.state.apply('zoom_in'); p = fp.OverviewPointer(c); c.overview_pointer = p; self.addCleanup(p.close)
        g = geometry(); c.set_maximize_geometry(g)
        output = dict(index=0, x=1000, y=0, width=800, height=600)
        for i in range(40): p.feed(TOP, (0, 0), [output], p.want[0]); p.poll(i * .01)
        p.band_point = (400, 75); p.feed(TOP, (0, 0), [output], p.want[0]); p.poll(1)
        return c, p, g

    def test_camera_only_status_does_not_invalidate_structural_press_target(self):
        c, p, g = self.open()
        p.on_op('resize_press')
        epoch = p._restore_epoch
        camera = copy.deepcopy(g); angle = .001
        camera['view'] = [math.cos(angle), -math.sin(angle), 0, math.sin(angle), math.cos(angle), 0, 0, 0, 1]
        c.set_maximize_geometry(camera)
        self.assertEqual(p._restore_epoch, epoch)
        p.on_op('resize_release'); self.assertFalse(c.state.zoomed)

    def test_output_change_cancels_held_restore_and_geometry(self):
        c, p, g = self.open()
        p.on_op('resize_press'); p.desktops_changed(); p.on_op('resize_release')
        self.assertTrue(c.state.zoomed)
        self.assertIsNone(p.maximize_geometry)
        self.assertIsNone(p._restore_press)


if __name__ == '__main__':
    unittest.main()
