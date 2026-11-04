"""The sample-image test: the real model on pictures with a known answer. It needs the GPU and the setup.sh runtime, so it
runs only when asked for, and never during a glasses session:

    XR_DEPTH_GPU_TESTS=1 python3 -m unittest discover -s tests/depth_engine -p 'test_real_model.py' -v
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(os.environ.get("XR_DEPTH_CACHE", Path.home() / ".cache/viture-depth")) / "venv/bin/python"

CHECK = textwrap.dedent("""
    import json, sys
    import numpy as np
    from tools.depth_engine import sample, shm as S
    from tools.depth_engine.backends import make_backend, resize_rgba
    from tools.depth_engine.temporal import TemporalFilter, luma_of
    name, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    b = make_backend(name, w, h)
    def depth(rgba):
        px = resize_rgba(rgba, w, h)
        raw = b.infer([px])[0]
        assert np.isfinite(raw).all(), "model gave NaN or infinity"
        return TemporalFilter().update(raw, luma_of(px), 1 / 60)[0], raw
    at = lambda d, x, y: float(d[int(y * d.shape[0]), int(x * d.shape[1])])
    s, _ = depth(sample.street())
    t, _ = depth(sample.desktop())
    blank = {}
    for value in (0, 255):
        px = np.full((h, w, 4), value, np.uint8)
        blank[str(value)] = float(np.ptp(b.infer([px])[0]))
    np.save(sys.argv[4], s)
    print(json.dumps({
        "near_block": at(s, .75, .75), "far_block": at(s, .23, .41), "road_near": at(s, .3, .9), "road_far": at(s, .5, .46), "sky": at(s, .5, .1),
        "windows": [at(t, .25, .3), at(t, .75, .25), at(t, .75, .7)], "wall": at(t, .25, .85), "blank": blank, "shape": list(s.shape)}))
""")


@unittest.skipUnless(os.environ.get("XR_DEPTH_GPU_TESTS") == "1" and PYTHON.exists(), "GPU test: set XR_DEPTH_GPU_TESTS=1 after setup.sh")
class RealModel(unittest.TestCase):
    maps: dict = {}

    def run_model(self, name, w, h):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "check.py"
            script.write_text(CHECK)
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            saved = Path(d) / "map.npy"
            r = subprocess.run([PYTHON, script, name, str(w), str(h), saved], capture_output=True, text=True, env=env, cwd=d, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.maps[name] = saved.read_bytes()
        return json.loads(r.stdout.strip().splitlines()[-1])

    def assert_sees(self, m):
        # The street: things low in the frame are near, the far block and the sky are not.
        self.assertGreater(m["near_block"], 0.8)
        self.assertLess(m["far_block"], 0.25)
        self.assertLess(m["sky"], 0.1)
        self.assertGreater(m["road_near"], m["road_far"] + 0.4)
        # The desktop: every window stands in front of the wallpaper.
        for window in m["windows"]:
            self.assertGreater(window, m["wall"] + 0.4)

    def test_rocm_one_desktop_preset(self):
        m = self.run_model("rocm", 518, 322)
        self.assertEqual(m["shape"], [322, 518])
        self.assert_sees(m)

    def test_rocm_several_desktops_preset(self):
        m = self.run_model("rocm", 420, 266)
        self.assertEqual(m["shape"], [266, 420])
        self.assert_sees(m)

    def test_ncnn_fallback_sees_the_same(self):
        self.assert_sees(self.run_model("ncnn", 420, 266))
        self.run_model("rocm", 420, 266)
        a = np.load(io.BytesIO(self.maps["rocm"]))
        b = np.load(io.BytesIO(self.maps["ncnn"]))
        self.assertLess(float(np.abs(a - b).mean()), 0.03)  # The same weights must draw the same map.


if __name__ == "__main__":
    unittest.main()
