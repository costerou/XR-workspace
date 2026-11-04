"""Measures the real engine as the renderer will use it: a feeder process publishes changing desktops into the shared
file at 120 Hz per desktop and reads the depth maps back, so the numbers include every copy, the resize, the smoothing
and the shared-memory hand-over. Latency is capture timestamp to map available (done_ns - source_ns of what it reads).

  python -m tools.depth_engine bench --backend rocm --size 518x322 --channels 1 --seconds 8
  python -m tools.depth_engine bench --all --out results.json

Power is the whole card from hwmon, VRAM is this process's own, both read while the load runs.
"""
from __future__ import annotations

import argparse
import glob
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from . import sample, shm as S
from .backends import make_backend, resize_rgba
from .engine import Engine
from .scheduler import Scheduler

CARD = "/sys/class/drm/card1/device/"  # RX 7800 XT on this machine; checked at start.
RX7800 = "0x747e"


def card_path() -> str:
    for d in glob.glob("/sys/class/drm/card?/device/"):
        try:
            if open(d + "device").read().strip() == RX7800:
                return d
        except OSError:
            pass
    return CARD


def read_int(path: str) -> int:
    try:
        return int(open(path).read())
    except (OSError, ValueError):
        return 0


def process_vram_mb(pid: int) -> float:
    """KFD's own count for ROCm; the DRM fdinfo of the process for Vulkan."""
    total = sum(read_int(p) for p in glob.glob(f"/sys/class/kfd/kfd/proc/{pid}/vram_*"))
    if total:
        return total / 2**20
    fds = {}
    for p in glob.glob(f"/proc/{pid}/fdinfo/*"):
        try:
            info = dict(line.split(":", 1) for line in open(p) if ":" in line)
        except OSError:
            continue
        if "drm-client-id" in info and "drm-total-vram" in info:
            fds[info["drm-client-id"]] = max(fds.get(info["drm-client-id"], 0), int(info["drm-total-vram"].split()[0]))
    return sum(fds.values()) / 1024


def rss_mb(pid: int) -> float:
    try:
        return int(open(f"/proc/{pid}/statm").read().split()[1]) * 4096 / 2**20
    except OSError:
        return 0.0


def feeder(path: str, channels: int, size: tuple[int, int], seconds: float, hz: float, focused: int, engine_pid: int, result) -> None:
    """Plays the renderer. The pictures slide a little every frame, so nothing is skipped as unchanged."""
    w, h = size
    shm = S.DepthShm.open(path)
    base = [resize_rgba(sample.street() if i % 2 == 0 else sample.desktop(), w * 2, h) for i in range(channels)]
    for ch in range(channels):
        shm.set_control(ch, S.ENABLED | (S.FOCUSED if ch < focused else 0))
    card = card_path()
    hw = glob.glob(card + "hwmon/hwmon*/")[0]
    seq = 1
    seen = [0] * channels
    lat = [[] for _ in range(channels)]
    power, vram, rss = [], [], []
    start = time.monotonic()
    next_tick = next_sample = start
    settle = start + 1.0  # Clocks and the first frames settle before anything counts.
    while (now := time.monotonic()) < start + seconds + 1.0:
        for ch in range(channels):
            off = seq % w
            shm.publish(S.INPUT, ch, base[ch][:, off:off + w], S.FMT_RGBA8, seq, time.monotonic_ns())
        seq += 1
        for ch in range(channels):
            f = shm.fetch(S.OUTPUT, ch, seen[ch])
            if f is not None:
                seen[ch] = f.source_seq
                if now > settle:
                    lat[ch].append((f.done_ns - f.source_ns) / 1e6)
        if now >= next_sample:
            next_sample = now + 0.1
            if now > settle:
                power.append(read_int(hw + "power1_average") / 1e6)
                vram.append(process_vram_mb(engine_pid))
                rss.append(rss_mb(engine_pid))
        next_tick += 1.0 / hz
        time.sleep(max(0.0, next_tick - time.monotonic()))
    result.put({"lat": lat, "power": power, "vram": vram, "rss": rss})


def proxy_path() -> str | None:
    """Builds render_proxy.cpp once; None without a compiler or GLES headers."""
    out = Path(tempfile.gettempdir()) / f"xr-depth-render-proxy-{os.getuid()}"
    src = Path(__file__).with_name("render_proxy.cpp")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        if subprocess.run(["g++", "-O2", "-o", str(out), str(src), "-lEGL", "-lGLESv2"], capture_output=True).returncode:
            return None
    return str(out)


def live_xr() -> bool:
    out = subprocess.run(["systemctl", "--user", "list-units", "viture-xr*", "--no-legend", "--no-pager", "--state=active"],
                         capture_output=True, text=True).stdout
    return bool(out.strip())


def wait_for_quiet(limit: float = 30.0, busy: int = 30) -> int:
    """Other work on the GPU spoils a measurement, so give it a while to finish. Returns the busy level measured with."""
    path = card_path() + "gpu_busy_percent"
    end = time.monotonic() + limit
    level = 100
    while time.monotonic() < end:
        level = max(read_int(path) for _ in range(5) if time.sleep(0.1) is None)
        if level < busy:
            break
    return level


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else 0.0


def run_one(backend_name: str, size: tuple[int, int], channels: int, seconds: float, focused_hz: float, other_hz: float, batch: int,
            with_proxy: bool) -> dict:
    w, h = size
    directory = tempfile.mkdtemp(prefix="xr-depth-", dir="/dev/shm")
    path = os.path.join(directory, "shm")
    try:
        shm = S.DepthShm.create(path, channels, w, h, w, h)
        backend = make_backend(backend_name, w, h)
        engine = Engine(shm, backend, Scheduler(focused_hz, other_hz, batch))
        warm = sample.street(w, h)
        for _ in range(3):  # The first runs compile and allocate.
            backend.infer([warm] * min(batch, channels))
        queue = mp.get_context("spawn").Queue()
        feed = mp.get_context("spawn").Process(target=feeder, args=(path, channels, size, seconds, 120.0, 1, os.getpid(), queue))
        feed.start()
        proxy = None
        if with_proxy and (binary := proxy_path()):
            env = dict(os.environ, DRI_PRIME="pci-0000_03_00_0")
            proxy = subprocess.Popen([binary, str(seconds)], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        while feed.is_alive():
            if engine.step() == 0:
                time.sleep(0.0005)
            if not queue.empty():
                break
        data = queue.get(timeout=30)
        feed.join(5)
        out = {"backend": backend_name, "size": f"{w}x{h}", "channels": channels, "batch": batch, "focused_hz": focused_hz, "other_hz": other_hz}
        all_lat = [v for lat in data["lat"] for v in lat]
        out["fps_per_desktop"] = [round(len(lat) / seconds, 1) for lat in data["lat"]]
        out["latency_ms"] = {"p50": round(percentile(all_lat, 50), 1), "p95": round(percentile(all_lat, 95), 1), "p99": round(percentile(all_lat, 99), 1)}
        out["power_w"] = round(float(np.mean(data["power"])), 0) if data["power"] else 0
        out["vram_mb"] = round(max(data["vram"], default=0))
        out["rss_mb"] = round(max(data["rss"], default=0))
        if proxy:
            out["renderer"] = proxy.communicate(timeout=30)[0].strip()
        shm.close()
        return out
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def flicker(backend_name: str, size: tuple[int, int], frames: int = 120) -> dict:
    """The same still picture with sensor-like noise: how much does each pixel wander, before and after smoothing?"""
    from .temporal import TemporalFilter, luma_of
    w, h = size
    backend = make_backend(backend_name, w, h)
    rng = np.random.default_rng(1)
    result = {}
    for name, base in (("street", sample.street()), ("desktop", sample.desktop())):
        px = resize_rgba(base, w, h)
        filt = TemporalFilter()
        raw, smooth = [], []
        for _ in range(frames):
            noise = rng.normal(0, 2.0, px.shape[:2] + (1,)).repeat(3, axis=2)
            noisy = px.copy()
            noisy[..., :3] = np.clip(px[..., :3].astype(np.float32) + noise, 0, 255).astype(np.uint8)
            d = backend.infer([noisy])[0]
            lo, hi = np.percentile(d, (2, 98))
            raw.append((d - lo) / max(hi - lo, 1e-6))
            smooth.append(filt.update(d, luma_of(noisy), 1 / 60)[0].copy())
        r = float(np.std(np.stack(raw[20:]), axis=0).mean())
        s = float(np.std(np.stack(smooth[20:]), axis=0).mean())
        result[name] = {"raw": round(r, 5), "smoothed": round(s, 5), "better": round(r / max(s, 1e-9), 1)}
    return result




def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="depth_engine bench")
    p.add_argument("--backend", default="rocm")
    p.add_argument("--size", default="518x322")
    p.add_argument("--channels", type=int, default=1)
    p.add_argument("--batch", type=int, default=4)
    p.add_argument("--focused-hz", type=float, default=1000)
    p.add_argument("--other-hz", type=float, default=1000)
    p.add_argument("--seconds", type=float, default=8)
    p.add_argument("--proxy", action="store_true", help="also run the 120 Hz stand-in renderer and report its misses")
    p.add_argument("--flicker", action="store_true")
    p.add_argument("--out")
    a = p.parse_args(argv)
    if live_xr():  # Full GPU load during someone's glasses session could cost them frames.
        print("XR is running: not measuring", file=sys.stderr)
        return 3
    w, h = (int(v) for v in a.size.split("x"))
    idle = wait_for_quiet()
    if a.flicker:
        result = flicker(a.backend, (w, h))
    else:
        result = run_one(a.backend, (w, h), a.channels, a.seconds, a.focused_hz, a.other_hz, a.batch, a.proxy)
    result["gpu_busy_before"] = idle
    print(json.dumps(result), flush=True)
    if a.out:
        with open(a.out, "a") as f:
            f.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
