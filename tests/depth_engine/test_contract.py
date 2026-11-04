"""The C++ header against the Python mirror: same layout, same sizes, and a real exchange between the two."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.depth_engine import shm as S  # noqa: E402


def binary():
    for d in ("build/release", "build/native-spike"):
        p = ROOT / d / "depth_contract_test"
        if p.exists():
            return p
    return None


@unittest.skipIf(binary() is None, "depth_contract_test is not built")
class Contract(unittest.TestCase):
    def test_layout(self):
        c = json.loads(subprocess.run([binary(), "--layout"], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(c["header"], 64)
        self.assertEqual(c["meta"], S.META_BYTES)
        self.assertEqual(c["channel"], S.CHANNEL_HEADER_BYTES)
        self.assertEqual(c["page"], S.HEADER_BYTES)
        self.assertEqual(c["slots"], S.SLOTS)
        self.assertEqual(c["magic"], S.MAGIC)
        self.assertEqual(c["version"], S.VERSION)
        self.assertEqual(c["in"], S.OFF_META_IN)
        self.assertEqual(c["out"], S.OFF_META_OUT)
        self.assertEqual(c["engine_ns"], S.OFF_ENGINE_NS)
        self.assertEqual(c["engine_pid"], S.OFF_ENGINE_PID)
        self.assertEqual((c["latest_out"], c["control"], c["source_seq"], c["flags"]), (4, 8, 16, 40))
        self.assertEqual(c["stride"], S.channel_stride(518, 322, 518, 322))
        self.assertEqual(c["bytes"], S.shm_bytes(4, 518, 322, 518, 322))

    def test_cpp_renderer_against_python_engine(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "shm")
            peer = subprocess.Popen([binary(), "--peer", path], stdout=subprocess.PIPE, text=True)
            try:
                for _ in range(200):
                    if os.path.exists(path) and os.path.getsize(path) > 0:
                        try:
                            S.DepthShm.open(path).close()
                            break
                        except ValueError:
                            pass
                    time.sleep(0.01)
                env = dict(os.environ, PYTHONPATH=str(ROOT))
                engine = subprocess.Popen([sys.executable, "-m", "tools.depth_engine", "serve", "--shm", path, "--backend", "fake", "--seconds", "12"],
                                          env=env, stdout=subprocess.DEVNULL, cwd=ROOT)
                out, _ = peer.communicate(timeout=20)
                engine.terminate()
                engine.wait(5)
            finally:
                if peer.poll() is None:
                    peer.kill()
            self.assertEqual(peer.returncode, 0)
            self.assertIn("depth 42x28", out)


class Sizes(unittest.TestCase):
    def test_same_as_the_header_tests(self):
        self.assertEqual(S.input_size(1920, 1200, S.LONG_HIGH), (518, 322))
        self.assertEqual(S.input_size(1920, 1200, S.LONG_BALANCED), (420, 266))
        self.assertEqual(S.input_size(1200, 1920, S.LONG_HIGH), (322, 518))
        self.assertEqual(S.input_size(3840, 1200, S.LONG_BALANCED), (854, 266))
        self.assertEqual(S.input_size(0, 1200, 518), (0, 0))


if __name__ == "__main__":
    unittest.main()
