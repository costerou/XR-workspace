# Phase 0 baseline: supervisor CPU, key press latency, memory

Recorded 2026-11-10 for the Rust rebuild (`docs/rust-rebuild-plan.md`, section 4). Raw result:
[`control-plane-2026-11-10.json`](control-plane-2026-11-10.json).

```sh
cmake --build build/release --target luma_rt_bench capture_e2e_bench xr-native-spike
TMPDIR=/tmp /usr/bin/python3 -m tools.native_spike.bench_baseline --build build/release --seconds 600 --live \
  --json docs/evidence/rust-baseline/control-plane-2026-11-10.json
```

`TMPDIR=/tmp` keeps the fake compositor's socket path under the `AF_UNIX` length limit.

## How it was measured

All three workloads ran together for 600 s. None of them touches the live compositor, renderer, glasses or controller
service. `--live` found no XR run up at the time, so there are no live numbers.

| Process | What ran |
|---|---|
| Supervisor | `bench_control`'s overview: the real `Supervisor` loop, `ViewControl`, unified cursor sampler and `HyprlandClient` against the fake compositor (cursor moves 10 px every 20 ms), and a fake renderer sending 120 Hz heartbeats. The recenter key was pressed through the real bind helper (`xr_control.py`) every 2 s. |
| Driver | `luma_rt_bench`: the driver's real ring pipeline, real fusion and seqlock, fed by a fake 1 kHz transport. |
| Renderer | The real `xr-native-spike` in `capture_e2e_bench`'s loopback compositor: gl-dmabuf, 3 outputs, 120 Hz. |

CPU and memory come from `/proc/PID/stat` and `/proc/PID/status`, sampled once a second. These are the fields
`pidstat -u -r 1` reads. sysstat is not installed, and this task does not install anything.

Key press to frame is the time from spawning the helper to the renderer reading the `recenter` frame. Both ends use
`CLOCK_MONOTONIC`. A real renderer applies the frame on its next refresh, which adds up to 8.3 ms at 120 Hz and then
`control_applied`.

The machine was a 16-CPU desktop with other agents running. Load average went from 4.4 at the start to 6.2 at the end.
About 34 GiB of swap was in use, so the kernel swapped idle pages out of every process. VmRSS alone is therefore below
the working set, so the table also gives VmRSS + VmSwap and the peak (VmHWM).

## Results (600 s)

| Measure | Supervisor | Driver (bench) | Renderer |
|---|---|---|---|
| %CPU, whole window (pidstat equivalent) | **3.68** (usr 2.98, sys 0.69) | 5.27 (process); pipeline 0.47 | 2.80 |
| %CPU per second, p50 / p95 / p99 | 3 / 5 / 9 | 5 / 6 / 6 | 3 / 4 / 7 |
| VmRSS at 10 min | **12.3 MiB** | **4.4 MiB** | **58.6 MiB** |
| VmRSS + VmSwap at 10 min | 21.8 MiB | 5.1 MiB | 98.1 MiB |
| VmHWM (peak) | 24.5 MiB | 5.0 MiB | 124.6 MiB |

| Key press to renderer frame (300 presses, 0 unanswered) | p50 | p90 | **p95** | p99 | mean |
|---|---|---|---|---|---|
| ms | **7.32** | 7.96 | **8.98** | 20.52 | 8.14 |

Other figures from the same window:

- Supervisor threads: loop 1.65 % and cursor sampler 2.02 % of a core, at 240 cursor samples a second.
  Cursor move to dot frame: p50 2.27 ms, p95 4.18 ms.
- The helper spawns, which Hyprland does in a real run, cost the bench process 0.006 %. They are left in the supervisor
  figure.
- The driver bench process includes the fake transport's sender thread. Its own pipeline figure (reader plus fusion) is
  0.47 %. Latency was p50 7.6 µs and p99 17.4 µs, with 0 samples lost and 0 allocations.

## Reading it against the targets

- **Supervisor CPU with the overview open: 3.7 % against ≤ 1 %.** About half is the 240 Hz cursor sampler, which makes
  one Hyprland request per sample, and half is the loop.
- **Key press to frame: p50 7.3 ms and p95 9.0 ms, before the renderer's refresh, against ≤ 5 ms.** Most of it is Python
  start-up in the helper (`bench_control --only control_op,startup` breaks this down).
- **Memory.** The supervisor bench process imports less than the real runner, so its number is a lower bound. The live
  runner process showed about 30 MiB RSS one second after start, against a target of ≤ 20 MiB. The driver figure is the
  bench process: the pipeline without usbdevfs, at 4.4–5.1 MiB against ≤ 8 MiB. The renderer peaks at about 125 MiB.
  Most of that is mapped file pages (EGL and Mesa libraries), so "no worse" should be judged on VmHWM and RssAnon.

An earlier 600 s run, under heavy load (load average 19–27), gave: supervisor 5.1 %, key press p50 7.9 ms and p95
19.7 ms, and 14 driver bench samples lost. Load moves the tail a lot, so compare runs only at similar load.
