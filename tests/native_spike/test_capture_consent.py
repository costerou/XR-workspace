"""Managed-output capture scope (user grant 2026-10-31): XR-n only, explicit consent, never presentation-only."""
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike.__main__ import load_consent
from tools.native_spike.supervisor import _launch, capture_arguments

INSTANCE = 'a' * 40 + '_123_456'
CAPTURE_ALLOW = ['headless-outputs', 'capture-managed-outputs']


class CaptureConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def write(self, **changes):
        data = {'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1',
                'allow': ['headless-outputs', 'test-pattern'], 'capture': False, 'input': False, 'sdk': False,
                'mode_switch': False, **changes}
        path = Path(self.tmp.name) / f'c{len(os.listdir(self.tmp.name))}.json'
        path.write_text(json.dumps(data)); path.chmod(0o600)
        return path

    def test_managed_capture_scope_is_explicit_and_exact(self):
        ok = load_consent(self.write(capture='managed-outputs', allow=CAPTURE_ALLOW), INSTANCE, 'DP-2')
        self.assertEqual(ok['capture'], 'managed-outputs')
        self.assertIs(load_consent(self.write(), INSTANCE, 'DP-2')['capture'], False)
        for changes in ({'capture': True, 'allow': CAPTURE_ALLOW}, {'capture': 'everything', 'allow': CAPTURE_ALLOW},
                        {'capture': 'managed-outputs'},  # allow list must name the capture scope
                        {'capture': False, 'allow': CAPTURE_ALLOW}, {'capture': 'managed-outputs', 'allow': CAPTURE_ALLOW + ['x']},
                        {'capture': 'managed-outputs', 'allow': CAPTURE_ALLOW, 'input': True}):
            with self.subTest(changes), self.assertRaises(ValueError):
                load_consent(self.write(**changes), INSTANCE, 'DP-2')

    def test_presentation_only_can_never_capture(self):
        for allow in (['test-pattern'], CAPTURE_ALLOW):
            with self.assertRaises(ValueError):
                load_consent(self.write(gate='P2-presentation-only', allow=allow, capture='managed-outputs'), INSTANCE, 'DP-2',
                             presentation_only=True)

    def test_capture_arguments_only_for_managed_names(self):
        self.assertEqual(capture_arguments(['XR-1', 'XR-2', 'XR-3'], 'DP-2'), ['--capture', 'XR-1,XR-2,XR-3'])
        self.assertEqual(capture_arguments(None, 'DP-2'), [])
        # Desktops added in the glasses go up to XR-4 (geometry.MAX_DESKTOPS); never a fifth.
        self.assertEqual(capture_arguments(['XR-4', 'XR-1'], 'DP-2'), ['--capture', 'XR-1,XR-4'])
        for bad in (['DP-1'], ['XR-1', 'XR-1'], ['XR-5'], ['XR-1', 'DP-2'], [], ['XR-1,XR-2'],
                    ['XR-1', 'XR-2', 'XR-3', 'XR-4', 'XR-5']):
            with self.subTest(bad), self.assertRaises(ValueError):
                capture_arguments(bad, 'DP-2')

    def test_launch_passes_capture_and_never_with_animation(self):
        peer, wayland = socket.socketpair()
        self.addCleanup(peer.close)
        class Client: _peer = (*__import__('struct').unpack('3i', wayland.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)), 'x')
        seen = []
        class Child:
            def poll(self): return 0
        envs = []
        def spawn(argv, **kwargs):
            seen.append(argv); envs.append(kwargs.get('env')); return Child()
        _launch(Client, 'run', 'session', 'DP-2', wayland, None, popen=spawn, capture=['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual(seen[0][-2:], ['--capture', 'XR-1,XR-2,XR-3'])
        # The renderer writes nothing to disk, including GPU driver shader caches.
        self.assertEqual(envs[0].get('MESA_SHADER_CACHE_DISABLE'), 'true')
        _, wayland2 = socket.socketpair()
        with self.assertRaises(ValueError):
            _launch(Client, 'run', 'session', 'DP-2', wayland2, None, popen=spawn, capture=['XR-1'], animate_pattern_ms=10)


if __name__ == '__main__':
    unittest.main()
