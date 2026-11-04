"""python -m tools.depth_engine {file,serve,bench}"""
from __future__ import annotations

import argparse
import signal
import sys
import time

import numpy as np

from . import shm as S


def load_image(path: str) -> np.ndarray:
    if path.endswith(".npy"):
        return np.load(path)
    try:
        import cv2
        bgr = cv2.imread(path, cv2.IMREAD_COLOR)
        if bgr is None:
            raise SystemExit(f"cannot read {path}")
        return np.dstack([bgr[..., ::-1], np.full(bgr.shape[:2], 255, np.uint8)])
    except ImportError:
        from PIL import Image
        return np.array(Image.open(path).convert("RGBA"))


def save_map(path: str, depth: np.ndarray) -> None:
    if path.endswith(".npy"):
        np.save(path, depth)
        return
    grey = (np.clip(depth, 0, 1) * 255).astype(np.uint8)
    try:
        import cv2
        cv2.imwrite(path, grey)
    except ImportError:
        from PIL import Image
        Image.fromarray(grey).save(path)


def cmd_file(a) -> int:
    from .backends import make_backend, resize_rgba
    from .temporal import TemporalFilter, luma_of
    rgba = load_image(a.input)
    w, h = S.input_size(rgba.shape[1], rgba.shape[0], a.long)
    backend = make_backend(a.backend, w, h)
    px = resize_rgba(rgba, w, h)
    start = time.perf_counter()
    raw = backend.infer([px])[0]
    took = time.perf_counter() - start
    depth, _, flat = TemporalFilter().update(raw, luma_of(px), 1 / 60)
    save_map(a.output, depth)
    print(f"{a.input}: {backend.name} {depth.shape[1]}x{depth.shape[0]} in {took * 1000:.1f} ms{' (flat)' if flat else ''} -> {a.output}")
    return 0


def cmd_serve(a) -> int:
    from .backends import make_backend
    from .engine import Engine
    from .scheduler import Scheduler
    shm = S.DepthShm.open(a.shm)
    w, h = S.input_size(a.src_width, a.src_height, a.long)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    backend = make_backend(a.backend, w, h)
    if backend.size and (w > shm.out_w or h > shm.out_h):  # A fixed-size model's map has to fit the file.
        raise SystemExit(f"{w}x{h} does not fit {a.shm}")
    if backend.size:  # Each batch size compiles on first use, which must not read as a dead engine mid-session.
        blank = np.zeros((h, w, 4), np.uint8)
        for n in range(1, min(a.batch, shm.channels) + 1):
            backend.infer([blank] * n)
    engine = Engine(shm, backend, Scheduler(a.focused_hz, a.other_hz, a.batch))
    deadline = time.monotonic() + a.seconds if a.seconds else None
    print(f"depth engine: {backend.name} {w}x{h}, {shm.channels} channels", flush=True)
    engine.run(lambda: stop or (deadline is not None and time.monotonic() > deadline))
    print(f"depth engine: {engine.stats.inferred} maps, {engine.stats.unchanged} unchanged frames skipped", flush=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="depth_engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("file", help="one picture in, one depth map out")
    f.add_argument("input")
    f.add_argument("output")
    f.add_argument("--backend", default="rocm", choices=["rocm", "ncnn", "fake"])
    f.add_argument("--long", type=int, default=S.LONG_HIGH)
    f.set_defaults(fn=cmd_file)
    s = sub.add_parser("serve", help="serve a shared-memory file made by the renderer")
    s.add_argument("--shm", required=True)
    s.add_argument("--backend", default="rocm", choices=["rocm", "ncnn", "fake"])
    s.add_argument("--long", type=int, default=S.LONG_BALANCED)
    s.add_argument("--src-width", type=int, default=1920)
    s.add_argument("--src-height", type=int, default=1200)
    s.add_argument("--focused-hz", type=float, default=60)
    s.add_argument("--other-hz", type=float, default=30)
    s.add_argument("--batch", type=int, default=4)
    s.add_argument("--seconds", type=float, default=0)
    s.set_defaults(fn=cmd_serve)
    b = sub.add_parser("bench", help="measure latency, rate, VRAM and power; see bench.py")
    b.set_defaults(fn=None)
    a, rest = p.parse_known_args(argv)
    if a.cmd == "bench":
        from .bench import main as bench_main
        return bench_main(rest)
    if rest:
        p.error(f"unknown arguments {rest}")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
