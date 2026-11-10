"""The hidden renderer trial flags file (supervisor.RENDERER_FLAGS): off with no file, strict about what it accepts."""
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.native_spike import supervisor
from tools.native_spike.supervisor import _launch


class RendererFlagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'renderer-flags'
        patcher = patch.object(supervisor, 'RENDERER_FLAGS', self.path); patcher.start(); self.addCleanup(patcher.stop)

    def test_no_file_no_flags(self):
        self.assertEqual(supervisor._renderer_flags(), [])

    def test_late_latch_guard(self):
        for value in ('0', '1', '1.5', '0.25', '8'):
            with self.subTest(value):
                self.path.write_text(f'--late-latch-guard-ms {value}\n')
                self.assertEqual(supervisor._renderer_flags(), ['--late-latch-guard-ms', value])

    def test_refuses_anything_else(self):
        for text in ('--late-latch-guard-ms', '--late-latch-guard-ms 9', '--late-latch-guard-ms -1', '--late-latch-guard-ms nan',
                     '--late-latch-guard-ms 1e1', '--late-latch-guard-ms 1 --late-latch-guard-ms 2', '--capture XR-9',
                     '--pose-feed xrld', '--late-latch-guard-ms 1;rm'):
            with self.subTest(text):
                self.path.write_text(text)
                with self.assertRaises(ValueError): supervisor._renderer_flags()

    def test_default_path_follows_xdg_config_home(self):
        with patch.object(supervisor, 'RENDERER_FLAGS', None), patch.dict('os.environ', {'XDG_CONFIG_HOME': self.tmp.name}):
            self.assertEqual(supervisor._renderer_flags_path(), Path(self.tmp.name) / 'viture-workspace/renderer-flags')

    def launch(self, **kwargs):
        _, wayland = socket.socketpair(); self.addCleanup(wayland.close)
        class Client: _peer = (*struct.unpack('3i', wayland.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)), 'x')
        seen = []
        class Child:
            def poll(self): return 0
        def spawn(argv, **_): seen.append(argv); return Child()
        _launch(Client, 'run', 'session', 'DP-2', wayland, None, popen=spawn, capture=['XR-1'], **kwargs)
        return seen[0]

    def test_launch_passes_them_only_with_head_tracking(self):
        self.path.write_text('--late-latch-guard-ms 1.0\n')
        self.assertEqual(self.launch(pose_feed=True)[-6:], ['--capture', 'XR-1', '--pose-feed', 'xrld', '--late-latch-guard-ms', '1.0'])
        self.assertNotIn('--late-latch-guard-ms', self.launch(pose_feed=False))

    def test_launch_unchanged_without_the_file(self):
        self.assertEqual(self.launch(pose_feed=True)[-4:], ['--capture', 'XR-1', '--pose-feed', 'xrld'])


if __name__ == '__main__':
    unittest.main()
