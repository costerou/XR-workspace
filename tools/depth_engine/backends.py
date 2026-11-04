"""The runtimes. docs/depth-engine.md has the numbers behind the choice.

rocm  Depth Anything V2 Small, half precision, PyTorch on ROCm. Default: fastest, and its compute queues left a 120 Hz
      GL renderer untouched in the contention test.
ncnn  The same weights on Vulkan compute. Half the speed, but needs only one small library; the fallback.
fake  No model: depth from brightness. For tests, needs no GPU.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

CACHE = Path(os.environ.get("XR_DEPTH_CACHE", Path.home() / ".cache" / "viture-depth"))
DAV2_CODE = CACHE / "dav2-code"
DAV2_WEIGHTS = CACHE / "models" / "depth_anything_v2_vits.pth"
NCNN_MODELS = CACHE / "ncnn"
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class BackendUnavailable(RuntimeError):
    pass


def resize_rgba(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    """Area-average shrink; the renderer normally sends frames at the right size already, so this is the slow path."""
    if rgba.shape[1] == w and rgba.shape[0] == h:
        return rgba
    try:
        import cv2
        return cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA if rgba.shape[1] > w else cv2.INTER_LINEAR)
    except ImportError:
        ys = (np.arange(h) * rgba.shape[0] / h).astype(int)
        xs = (np.arange(w) * rgba.shape[1] / w).astype(int)
        return rgba[ys][:, xs]


class FakeBackend:
    """Brighter is nearer. Enough to exercise everything around the model."""
    name = "fake"
    size = None

    def infer(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        return [f[..., :3].astype(np.float32).mean(axis=2) / 255.0 for f in frames]


class RocmBackend:
    name = "rocm"

    def __init__(self, w: int, h: int):
        os.environ.setdefault("ROCR_VISIBLE_DEVICES", "0")  # The RX 7800 XT; the iGPU is too slow and must stay free.
        if not DAV2_WEIGHTS.exists() or not (DAV2_CODE / "depth_anything_v2" / "dpt.py").exists():
            raise BackendUnavailable(f"model not installed in {CACHE}: run tools/depth_engine/setup.sh")
        try:
            import torch
            sys.path.insert(0, str(DAV2_CODE))
            from depth_anything_v2.dpt import DepthAnythingV2
        except ImportError as e:
            raise BackendUnavailable(f"no ROCm PyTorch here ({e}): run with the setup.sh venv") from e
        if not torch.cuda.is_available():
            raise BackendUnavailable("ROCm sees no GPU")
        torch.set_num_threads(1)  # The work is on the GPU; idle CPU threads would only spin against the serving loop.
        self.torch = torch
        self.size = (w, h)
        net = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
        net.load_state_dict(torch.load(DAV2_WEIGHTS, map_location="cpu", weights_only=True))
        self.net = net.eval().half().cuda(0)
        self.mean = torch.from_numpy(MEAN * 255)[:, None, None].half().cuda(0)
        self.std = torch.from_numpy(STD * 255)[:, None, None].half().cuda(0)

    def infer(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        torch = self.torch
        with torch.inference_mode():
            x = torch.from_numpy(np.stack(frames)[..., :3]).cuda(0).permute(0, 3, 1, 2).half()
            y = self.net((x - self.mean) / self.std).float().cpu().numpy()
        return list(y)


class NcnnBackend:
    name = "ncnn"

    def __init__(self, w: int, h: int):
        try:
            import ncnn
        except ImportError as e:
            raise BackendUnavailable("no ncnn module: run with the setup.sh venv") from e
        d = NCNN_MODELS / f"dav2_{w}x{h}"
        if not (d / "m.ncnn.param").exists():
            raise BackendUnavailable(f"no ncnn model for {w}x{h}: run tools/depth_engine/setup.sh --ncnn {w}x{h}")
        self.ncnn = ncnn
        self.size = (w, h)
        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = True
        self.net.set_vulkan_device(0)
        self.net.opt.num_threads = 2
        self.net.load_param(str(d / "m.ncnn.param"))
        self.net.load_model(str(d / "m.ncnn.bin"))

    def infer(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        out = []
        for f in frames:
            # A contiguous copy: ncnn.Mat reads the buffer straight through and would scramble transpose()'s strided view.
            x = np.ascontiguousarray(((f[..., :3].astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1))
            ex = self.net.create_extractor()
            ex.input("in0", self.ncnn.Mat(x).clone())
            _, y = ex.extract("out0")
            out.append(np.array(y).squeeze().copy())
        return out


def to_half(depth: np.ndarray) -> np.ndarray:
    """float32 to float16. numpy's conversion is scalar and takes 1 ms a map; PyTorch's uses the CPU's F16C unit."""
    torch = sys.modules.get("torch")
    if torch is not None:
        return torch.from_numpy(depth).half().numpy()
    return depth.astype(np.float16)


def make_backend(name: str, w: int, h: int):
    if name == "rocm":
        return RocmBackend(w, h)
    if name == "ncnn":
        return NcnnBackend(w, h)
    if name == "fake":
        return FakeBackend()
    raise BackendUnavailable(f"unknown backend {name}")
