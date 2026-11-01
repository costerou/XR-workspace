"""Parking leaves the interactive row contiguous on any host."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from tools.native_spike import hyprland as h


def monitor(name, ident, **changes):
    result = dict(id=ident, name=name, width=1920, height=1080, x=0, y=0,
                  scale=1.0, transform=0, refreshRate=60.0, disabled=False,
                  dpmsStatus=True, mirrorOf='none')
    return dict(result, **changes)


class PresentationLayoutTests(unittest.TestCase):
    def test_scaled_desktop_adjoins_first_virtual_and_parking_is_idempotent(self):
        desktop = monitor('DP-1', 0, width=5120, height=1440, scale=1.25)
        glasses = monitor('DP-7', 1, height=1200, x=4096, scale=1.25, refreshRate=119.999)
        parked = h.presentation_placement([desktop, glasses], 'DP-7')
        self.assertEqual(parked, dict(glasses, x=-1536, y=9344))
        specs = h.plan_outputs([desktop, parked], [], 'run')
        self.assertEqual([(s['x'], s['y']) for s in specs], [(4096, 0), (6016, 0), (7936, 0)])
        self.assertEqual(h.presentation_placement([desktop, parked], 'DP-7'), parked)

    def test_rotation_negative_coordinates_and_owned_outputs(self):
        desktop = monitor('HDMI-A-2', 0, x=-2000, y=-300, width=1200, height=1920, transform=1, scale=1.5)
        glasses = monitor('DP-3', 1, width=3840, height=2160, transform=1)
        owned = monitor('XR-1', 2, x=9999, y=99999)
        parked = h.presentation_placement([desktop, glasses, owned], 'DP-3')
        self.assertEqual((parked['x'], parked['y']), (-4160, 8692))

    def test_missing_pair_fails(self):
        for snapshot, name in [([monitor('DP-1', 0)], 'DP-2'), ([monitor('DP-2', 0)], 'DP-2')]:
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                h.presentation_placement(snapshot, name)

    def test_managed_output_cannot_be_parked_as_presentation(self):
        with self.assertRaises(ValueError):
            h.presentation_placement([monitor('DP-1', 0), monitor('XR-1', 1)], 'XR-1')

    def test_client_requires_readback(self):
        desktop, glasses = monitor('DP-1', 0), monitor('DP-2', 1)
        client = h.HyprlandClient('a'*40+'_123_456', '/tmp')
        snapshots = [[desktop, glasses], [desktop, dict(glasses, x=-1920, y=9272)]]
        with patch.object(client, 'query', side_effect=snapshots), patch.object(client, '_eval'):
            parked = client.park_presentation('DP-2')
        self.assertEqual((parked['x'], parked['y']), (-1920, 9272))
        with patch.object(client, 'query', return_value=[desktop, glasses]), patch.object(client, '_eval'), patch.object(h, 'DEADLINE', .01):
            with self.assertRaisesRegex(ValueError, 'presentation placement.*not observed'):
                client.park_presentation('DP-2')


class ReloadPlacementTests(unittest.TestCase):
    def test_reload_preserves_120hz_after_parking(self):
        from tools.native_spike.managed import view_controls
        class Client:
            def __init__(self, runtime):
                self.runtime_dir = runtime
                self.desktop = monitor('DP-1', 0)
                self.glasses = monitor('DP-2', 1, height=1200, refreshRate=119.999, x=-1920, y=9272)
            def query(self, kind, *args, **kwargs):
                return [self.desktop, self.glasses] if kind == 'monitors' else {}
            def install_view_binds(self, *args): pass
            def reinstall_view_binds(self, *args): pass
            def remove_view_binds(self): pass
            def park_presentation(self, output):
                self.glasses = h.presentation_placement(self.query('monitors'), output)
            def apply_session_mode(self, output):
                self.glasses.update(width=1920, height=1200, refreshRate=119.999)
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            with view_controls(client, 'run', 'session', ['XR-1','XR-2','XR-3'], 'DP-2') as control:
                client.glasses.update(x=1920, y=0, height=1080, refreshRate=60.0)
                control.on_events(b'configreloaded>>\n')
                control.join_focus()
                self.assertEqual((client.glasses['x'], client.glasses['y']), (-1920, 9272))
                self.assertEqual((client.glasses['width'], client.glasses['height'], client.glasses['refreshRate']),
                                 (1920, 1200, 119.999))
