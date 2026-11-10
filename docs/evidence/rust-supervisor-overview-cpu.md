# RR-64: supervisor CPU with the overview open (Rust)

Recorded 2026-11-10 (re-run at 14:30 with the real pointer) for the Rust rebuild's supervisor-CPU gate (`docs/rust-rebuild-plan.md`, section 4: "Supervisor CPU
with overview open", ≤ 1 % of a core; the Python baseline was 3.7 %).

```sh
cd rust && TMPDIR=/tmp cargo test --release -p xr-workspace --test supervisor_overview_bench -- \
    --ignored --nocapture --test-threads=1
```

`XR_BENCH_SECONDS` sets the measured window (default 10 s, after a 1 s warm-up). `TMPDIR=/tmp` keeps the fake
compositor's socket path under the `AF_UNIX` length limit. The bench is
`rust/crates/xr-workspace/tests/supervisor_overview_bench.rs`; its three tests are `#[ignore]`d, so the normal test run
skips them.

For the same-machine Python figure:

```sh
TMPDIR=/tmp XDG_RUNTIME_DIR=<empty 0700 dir under /tmp> HYPRLAND_INSTANCE_SIGNATURE=fake \
  /usr/bin/python3 -m tools.native_spike.bench_control --only overview --overview-seconds 10
```

## How it was measured

Nothing touches the live compositor, renderer, glasses or controller service. The Hyprland client is given the fake's
instance signature and runtime directory explicitly.

| Part | What ran |
|---|---|
| Run loop | The real `Supervisor::run_hosting` (heartbeat watchdog, 50 ms pass, bounded frame send), hosting the pointer through the new `supervisor::hook` seam. |
| Sampler | The real sampler thread (`sampler::run::start`), `Variant::Unified`, 250 Hz, on `sampler::live::Live` over a real `hyprland::Client`: the one-shot request socket, with each query after the first sent on a `Lane`. |
| Compositor | `bench_control.FAKE_HYPRLAND` in `oscillate` mode with a 0.02 s period, so the cursor moves 10 px every 20 ms. It runs as its own process, under an interpreter copy named `Hyprland`, in a private 0700 runtime directory under `/tmp`. This is the same fake that `FakeCompositor('oscillate', 0.02)` starts for the Python bench. |
| Renderer | `bench_control.FAKE_RENDERER` as its own process: it sends 120 Hz heartbeats and reads and counts every frame. |
| Pointer | The real `UnifiedPointer` (an `OverviewPointer` for the Locked test), on a minimal `Control` holding a `ViewState` and the bench layout's `Geometry`. `supervisor::pointer_host` shares it between the two threads: the sampler reads and feeds it through `SampledPointer`, one lock per call; the run loop polls it through `PointerHook`, its `LoopHook`. The loop polls only when the pointer's `WakeFd` is signalled or one of its own timers (`pointer_host::due`) is up. The unified sampler installs its click binds as it would live; they are Lua text the fake answers `ok` to. |

CPU is the whole supervisor process: `getrusage(RUSAGE_SELF)`, user plus system time, across the window, as a
percentage of one core. The fakes are separate processes, so their CPU is not counted. The per-thread split comes from
`/proc/self/task/<tid>/schedstat` (time on a CPU, in ns) for the loop thread and for `xr-sampler`. Every run checked
two things: each frame sent reached the renderer, and the run ended `Interrupted`.

Machine: AMD Ryzen 7 9700X (8 cores, 16 threads), Linux 7.2.15-1-omarchy, Rust release profile, Python 3.14.8. The
desktop was in normal use while the bench ran, at a load average of about 1.5 to 3.5. Other agents were building in parallel, and one run during a load spike is reported apart below.

## Results

Every figure is a percentage of one core.

### The real pointer

Three runs of the Rust bench, each test 10 s, at 14:25 to 14:32 (load average about 1.2). Each cell lists runs 1 to 3.

| Run | Process | Loop thread | Sampler thread |
|---|---|---|---|
| Rust, overview open (Unified) | 0.49 / 0.55 / 0.49 | 0.15 / 0.16 / 0.16 | 0.34 / 0.40 / 0.34 |
| Rust, overview closed, Unified, cursor on XR-1 | 0.47 / 0.40 / 0.44 | 0.07 / 0.06 / 0.07 | 0.40 / 0.34 / 0.37 |
| Rust, overview closed, Locked sampler (it blocks) | 0.08 / 0.09 / 0.09 | 0.08 / 0.09 / 0.09 | 0.00 |

The same in every run: about 245 `cursorpos` samples a second in both Unified tests and none in the Locked one. With the
overview open, the pointer was fed 2702 to 2705 samples, and 551 `overview_pointer` frames were sent and all 551 read
by the renderer (50 a second). Loop passes ran at 170 a second open and 120 a second closed. There were 0 sampler
errors; the bench now asserts that. The fake saw 5 `/eval` requests with the overview open, the click binds among them.

### The stand-in pointer (earlier runs)

Before the pointer's port landed, the bench used a stand-in pointer. It turned each sample that moved the cursor into one
frame, and its want never changed, so the sampler made no binds or warps. Six runs of the Rust bench, each test 10 s. Run 1 was at 12:18 (load average about 2.9), runs 2 to 4 at 12:48 (about
1.8), and runs 5 and 6 at 12:58 (about 2 to 2.6). Each cell lists runs 1 to 6 in order.

| Run | Process | Loop thread | Sampler thread |
|---|---|---|---|
| Rust, overview open (Unified) | 0.81 / 0.48 / 0.62 / 0.50 / 0.46 / 0.57 | 0.14 / 0.12 / 0.11 / 0.09 / 0.08 / 0.12 | 0.67 / 0.36 / 0.50 / 0.41 / 0.38 / 0.46 |
| Rust, overview closed, Unified, cursor on XR-1 | 0.72 / 0.45 / 0.54 / 0.53 / 0.44 / 0.52 | 0.11 / 0.08 / 0.07 / 0.08 / 0.07 / 0.08 | 0.61 / 0.36 / 0.46 / 0.45 / 0.37 / 0.44 |
| Rust, overview closed, Locked sampler (it blocks) | 0.11 / 0.09 / 0.09 / 0.09 / 0.07 / 0.08 | 0.11 / 0.09 / 0.09 / 0.09 / 0.07 / 0.08 | 0.00 |
| Python `bench_overview`, same machine, 12:19, 2 runs | 4.28 / 3.61 | 1.87 / 1.60 | 2.41 / 2.01 |
| Python baseline (`docs/evidence/rust-baseline/`, 600 s) | 3.7 | 1.65 | 2.0 |

What the Rust runs did, the same in every run:

| | Open (Unified) | Closed (Unified) | Closed (Locked) |
|---|---|---|---|
| Samples fed to the pointer | 243 to 245 a second | none (outside the overview) | none |
| `cursorpos` requests to the fake | about 245 a second | about 245 a second | 0 |
| `overview_pointer` frames | 50 a second, 551 sent = 551 read by the renderer | 0 | 0 |
| Loop passes | 170 a second | 120 a second | 120 a second |
| Sampler errors | 0 | 0 | 0 |

One more run, at 12:52, is not in the table. It ran while other builds pushed the load average to 11 on 16 threads,
and it failed the gate: 1.15 % with the overview open, and 1.80 % closed with Unified. CPU time per request rises under
contention (cache misses, scheduler and socket work). The figures above come from a normally busy desktop, which is
also how the Python figures were taken. A heavily loaded machine can push the open-overview figure past 1 %.

The Python bench drew the same 551 `overview_pointer` frames over its run and sampled at 240 a second. Its record also
shows `sampler_errors` close to one per sample, which this task did not look into.

## Reading it against the target

**Gate met with the real pointer: 0.49 to 0.55 % of one core with the overview open. With the stand-in it was 0.46 to
0.81 %, against ≤ 1 %. Python was 3.6 to 4.3 % on the same
machine, and 3.7 % in the baseline.** The bench asserts the 1 % limit. The margin is real but not wide (the worst run
was 0.81 %, on a loaded desktop), and almost all of it is the sampler's socket round trips.

- **The run loop.** The Python loop calls `control.poll(now)` on every pass: each heartbeat, each status datagram,
  each sampler poke and each 50 ms timeout. It also runs the pointer's 250 Hz `ViewControl` work. The Rust loop wakes
  for the 120 Hz heartbeat and for one `WakeFd` signal per cursor move. It polls the hook only then, or at the hook's
  deadline. With the overview open that is about 170 passes a second (120 heartbeats plus 50 moves). Closed, it is
  the 120 heartbeats alone, and an idle hook adds no pass (`tests/supervisor_hook.rs`,
  `an_idle_hook_adds_no_loop_pass`).
- **The sampler.** Hyprland has no cursor-motion push. The sampler still makes one `cursorpos` request per sample:
  about 245 a second, each a new connection, because the request socket is one-shot. What Rust removes is the cost
  around each request. The Python client walks the path and re-resolves the peer on every request. In Rust, the
  `Lane` keeps the verified identity and a pidfd, so only the first query in a run (and the first after a failure)
  takes the full checks.
- **Closed overview.** The Plain and Locked samplers block outside the overview, so with either one the supervisor
  costs only the heartbeat loop. The Unified sampler keeps sampling at 250 Hz outside the overview while the cursor
  is on an XR monitor, to hold the cursor on home and to drive the edge push. Python behaves the same way. In that
  case a closed overview costs about the same as an open one. This bench does not change that behaviour.
- **The real pointer.** The real pointer's mapping maths, state machine and frame building cost little next to a
  socket round trip. With the overview open, the loop thread went from about 0.1 % (stand-in) to about 0.16 %. The
  sampler thread did not change. The pointer adds no timer wakes of its own while the cursor moves: its due times are
  the idle close, held presses, a resting pan and the restore band's dwell, and none of them falls due here.
- **Machine load.** The machine was a working desktop, not idle, and the runs vary. The samples per second come in
  slightly under 250 because each wait is timed from the end of the previous sample.
