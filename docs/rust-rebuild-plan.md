# Rust rebuild plan

Status: plan only, written 2026-11-10. No code has changed. Numbers quoted here come from
`docs/performance.md`, `docs/stabilization.md`, `docs/usb-drops.md`, `docs/tracking-3dof-ab.md`,
`docs/stereo-render.md`, `docs/pro2/` and `spike/driver/docs/`. Anything marked **(target)** is a
goal, and anything marked **(not measured)** has no baseline yet.

## 0. The short version

- Most of XR Workspace can sensibly move to Rust: the Luma driver, the Pro 2 bridge host, the
  renderer, the supervisor and the controller. The Hyprland plugin, the QML panel, the VITURE SDK
  and the depth model stay as they are.
- Rust on its own will not make head tracking faster. The Luma driver already adds only 5–10 µs
  (p50) and allocates nothing. The real latency is elsewhere: the renderer reads the pose through
  a mutex, estimates head velocity from a sample that is about 8 ms old, and draws a full refresh
  before the frame is shown (read-to-present 16.6 ms measured live). The plan fixes those, and
  uses the rewrite to do it.
- What Rust does buy: memory-safe parsing of USB and JSON input, one toolchain, a supervisor that
  is pinned by hash like the other binaries (today Python runs from the checkout), no Python
  start-up on every key press, and no 250 Hz Python loop while the overview is open.
- Rough size: 7–10 months for one person, in eight phases. Every phase ships on its own and can be
  rolled back by restoring the previous pins.

## 1. Inventory

| Part | Where | Language, size | What it does |
|---|---|---|---|
| Controller | `tools/xr_controller/` | Python, about 1,000 lines | User service. Owns the state machine, consent scope, the panel socket, and starts each run as a transient systemd unit. Ticks at 1 Hz. |
| Supervisor | `tools/native_spike/` | Python, about 15,000 lines (largest: `view.py` 2,366, `free_pointer.py` 2,313, `unified_cursor.py` 1,118) | Runs inside each run. Journal, guard, Hyprland IPC, window placement, pointer sampling, key binds, video, privacy mode, USB-drop hold, recovery. |
| Key helper | `tools/native_spike/xr_control.py` | Python, 291 lines | One new Python process per bound key press. Sends one frame to the run control socket. |
| Renderer | `spike/src`, `spike/include/xr` | C++, 11,852 + 3,385 lines | Single thread. Wayland layer surface, zero-copy dmabuf capture, GLES drawing (16 shader programs), prediction, stabilizer, HUD, video (libmpv), depth and stereo. |
| Ownership plugin | `spike/ownership/` | C++23, 903 lines | Hyprland plugin. Creates and removes the `XR-n` headless outputs. Pinned to one Hyprland commit hash. |
| Luma driver | `spike/driver/` (`xr-luma-driver`) | C++, about 11,700 lines with tests and docs | Reads the Luma Ultra IMU over raw usbdevfs, runs fusion, publishes the pose. No SDK, no libusb. |
| Pro 2 bridge | `tools/pro2_runtime/` (`bridge.cpp`, `launch.py`) | C++20 + Python | Loads VITURE `libglasses.so` from a pinned file descriptor, republishes its pose callback. |
| Quiet shim | `spike/quiet_driver/quiet_driver.c` | C, 73 lines | `LD_PRELOAD` shim that keeps XRLinuxDriver off the glasses' USB as far as it can. |
| XRLinuxDriver | `/usr/bin/xrDriver` (external) | C | Fallback driver for other glasses, and behind the hidden `tracking-override`. |
| Depth engine | `tools/depth_engine/` | Python, PyTorch | Depth Anything V2 for immersive mode. Talks through the `XRD1` shared-memory block. |
| Panel | `omarchy-plugin/cody.viture-workspace/` | QML, 32 files, about 2,231 lines | Talks only to the controller socket. No polling. |
| Offline tools | `tools/luma_capture`, `luma_probe`, `luma_soak`, `spike/bench/*.py` | Python | Capture, probe, soak-log analysis, bench comparison. |
| Benches | `spike/bench/`, `spike/tests/gl_bench*` | C++ | Loopback compositor, capture bench, tracking bench, GL call counting, malloc counter. |
| Installer | `python -m tools.xr_controller install` | Python | Pins renderer, plugin and driver by hash. No PKGBUILD. |

### Wire protocols (all must stay byte-compatible during the migration)

1. **Panel ↔ controller.** `$XDG_RUNTIME_DIR/viture-workspace/controller.sock`. Newline JSON,
   `{schema:1, request_id, op}`, 16 KiB frame limit, same-UID check, at most 32 clients. Ops:
   `status`, `get_settings`, `set_settings`, `set_pro2_projection`, `begin_start`, `confirm_start`,
   `begin_recover`, `confirm_recover`, `cancel`, `stop`, `video_open`, `view_op`. Pushes
   `event:"state"` snapshots.
2. **Run control socket.** `$XDG_RUNTIME_DIR/xr-workspace-spike/<run_id>/control.sock`. Frame is
   exactly `{schema, request_id, run_id, session, op, payload}`. `request_id` strictly increases.
   Queue of 8.
3. **Supervisor → renderer.** Socketpair on `--supervised-fd`. Same six-key envelope, duplicate
   keys rejected, depth at most 32, 16,384 bytes (524,288 for `options_menu`). File descriptors
   travel by `SCM_RIGHTS`. 23 ops: `status`, `start`, `stop`, `select`, `recover`, `view`,
   `recenter`, `style`, `help`, `placement`, `placement_menu`, `options_menu`, `resize_handle`,
   `move_handle`, `overview_pointer`, `roll`, `desktops`, `environment`, `binds`, `edge_pan`,
   `immersive`, `restore_band`, `video`.
4. **Renderer → supervisor.** Heartbeat byte `H` (2 s gap ends the run). Status datagrams:
   `control_applied`, `control_refused`, `owner_readback`, geometry replies, and the status dict
   (`presented_hz`, `missed`, `capture_path`, `pose_age_ms`, `read_to_present` p50/p95/mean/max).
5. **Supervisor ↔ plugin.** hyprctl command `xrspike `, seven keys including `epoch`, at most
   8,192 bytes in and 1 MiB out. Ops: `hello`, `pair_hello`, `pair_observe`, `cursor_shape`,
   `create`, `reshape`, `inspect`, `remove`.
6. **Hyprland IPC.** Allow-listed queries and dispatches, generated Lua through `/eval` (at most
   4,096 characters), events from `.socket2.sock`.
7. **Pose feed.** SysV shared memory, one segment per value, keyed by `ftok(<prefix><name>, 0)`:
   `pose_orientation` (16 floats), `pose_orientation_mutex` (process-shared robust
   `pthread_mutex_t`), `disabled`, `display_fov`, `look_ahead_cfg`. Single writer, enforced by
   `flock` on `<prefix>writer.lock`.
8. **Newer pose channels, published but unused.** Seqlock `/dev/shm/xr-luma-pose` (`PoseRecord`,
   40 bytes) and `PredictionWire` (64 bytes, magic `0x4c505231`).
9. **Depth.** `XRD1` memfd, 4,096-byte header, seqlock, three slots.
10. **Files on disk.** `journal.json`, consent `<run_id>.json`, `.progress.json`, `.result.json`,
    `.recovery.json`, `installed.json`, the pin files, `profiles.json`, `window-memory.json`.

### Invariants the rewrite must not weaken

- No change to the glasses or the desktop without per-run consent. The scope may only shrink.
- Journal first: intent is written before a change, and a step is confirmed only after readback.
  Recovery runs in reverse, outputs last, and keeps anything ambiguous.
- Only hash-pinned binaries are launched. The plugin is loaded exactly once.
- Driver safety rules S1–S10: no network, no USB reset or `CLEAR_HALT`, one start command per
  connection, no polling, listen first, discard the first packet, no uinput, one driver,
  unprivileged, and `ldd` shows only the C/C++ runtime.
- Privacy mode wakes the fallback display before anything else during recovery.
- Pinned windows, special workspaces and the panel are never adopted.
- Monitor config is never edited, apart from the one confirmed `monitors.lua` rule removal.
- Cody's acceptance order: zero USB drops, then lowest latency, highest rate, no drift, instant
  calibration, solid reconnect, then features.

### Known problems carried into the plan

- Heading drifts about 0.5°/min (10–30° in 20 minutes). Gyro bias follows temperature, about
  0.016°/s per °C. There is no magnetometer and the cameras are off by design.
- USB drops: 54 in 25.5 hours, up to 3.2 per hour, often in pairs. Root cause is unproven and is
  on the glasses' side. Kernel re-enumeration takes 0.9 s at best, 4.2 s median. The hold logic
  has never met a real drop. The Pro 2 path has no reconnect at all.
- Never measured: real-glasses USB latency, real-time scheduling, the 3-hour soak gate, physical
  motion-to-photon, per-phase start-up times, renderer GPU time in a live run.
- The renderer consumes the mutex feed and derives velocity from two quaternion samples 2–50 ms
  apart. The driver's own predictor and the seqlock channels have no consumer.
- Head-tracked capture-to-present is 13.15 / 16.47 ms (p50 / p99) against 5.72 / 9.90 ms
  untracked, so drawing on every callback costs about one refresh.
- Pro 2 pose age is 9.4 ms p50 at 60 Hz, against 3.1 ms through XRLinuxDriver.
- The renderer asks for a GLES 2 context but six shader files use `#version 300 es`. It works only
  because Mesa hands back a 3.x context.
- Python is not pinned. It runs from the checkout.

## 2. Verdict per part

| Part | Verdict | Reason |
|---|---|---|
| Luma driver | **Rewrite in Rust** | Parses untrusted USB bytes in a long-lived process; Rust removes that class of bug. Small, well tested, has bit-exact replay fixtures, so parity is provable. The rewrite is also where the driver improvements land. |
| Pro 2 bridge host | **Rewrite in Rust, SDK through FFI** | Small. Gains reconnect, event-driven wake-up and the shared publisher crate. Drops the `libcrypto` dependency. |
| VITURE SDK (`libglasses.so`) | **Keep, FFI only** | Proprietary binary. Still loaded from a pinned file descriptor after a SHA-256 check. |
| Renderer | **Rewrite in Rust, keep GLES** | Largest latency win lives here (late latch, gyro-rate prediction). Port the logic crates first, the GL and Wayland shell last. |
| Supervisor | **Rewrite in Rust** | Biggest safety surface that is not pinned today. Removes the 250 Hz Python loop and the per-sample Hyprland reconnect. Highest porting risk because of the journal and recovery rules. |
| Controller | **Rewrite in Rust** | Small. Same binary as the supervisor. Becomes pinned. |
| Key helper | **Rewrite in Rust first** | Tiny, and it runs on every key press. Cheapest visible win. |
| Installer | **Rewrite in Rust** (subcommand) | Same layout and pin files. Pins the supervisor and controller too. |
| Hyprland ownership plugin | **Keep C++** | Compiled against Hyprland's internal C++ headers and pinned by API hash. No Rust binding exists or could stay stable. |
| QML panel | **Keep** | Runs inside the Omarchy shell. Only speaks the controller socket, which does not change. |
| Quiet shim | **Keep C** | 73 lines, only for the XRLinuxDriver fallback. Nothing to gain. |
| XRLinuxDriver | **Keep, external** | Fallback for other glasses. Not ours. |
| Depth engine | **Keep Python** | It is a PyTorch model runner. The shared-memory contract stays. |
| Offline tools | **Keep Python** | They act as independent test oracles for the capture format and soak logs. |
| Loopback compositor and benches | **Keep C++ for now** | An independent harness is worth more than a ported one while the renderer is being replaced. Revisit after phase 7. |

### Crates, and why

- **Workspace.** One Cargo workspace under `rust/`, with `Cargo.lock` committed and `cargo-deny`
  checking licences. Crates: `xr-wire`, `xr-pose`, `xr-luma-driver`, `xr-pro2-bridge`,
  `xr-renderer`, `xr-workspace` (controller, supervisor, key helper, installer).
- **USB: no `hidapi`, no `rusb`.** Both pull in libusb or hidraw. That breaks the "`ldd` shows only
  the C runtime" rule, and libusb issues control transfers and configuration calls we do not
  choose. The current driver uses raw usbdevfs ioctls with 8 URBs per endpoint, and the Rust one
  keeps that, through `rustix` (or `libc`) in one small module. `nusb` is the fallback if the
  hand-written module turns out to be a burden; it is pure Rust but brings its own enumeration.
- **Threads, not `tokio`.** The driver has at most two threads (reader and fusion) joined by a
  fixed single-producer single-consumer ring. The renderer is one thread around `poll`. The
  controller serves at most 32 clients at 1 Hz. An async runtime would add scheduling jitter in
  the driver and nothing useful elsewhere. Event loops use `rustix::event::poll`, or `mio` for the
  controller and supervisor.
- **`serde` + `serde_json`** for every JSON protocol, with `deny_unknown_fields` and a custom
  visitor that rejects duplicate keys, NaN and Infinity, to match `strict_json` and
  `decode_control`. Size and depth limits are checked before parsing.
- **Pose shared memory.** `libc` for `shmget`/`shmat`/`ftok` and for the robust process-shared
  `pthread_mutex_t`. The mutex must remain the glibc one because XRLinuxDriver-compatible readers
  share it. The seqlock channels use `core::sync::atomic`.
- **Maths.** `glam` (f32 for drawing, f64 for the fusion accumulators).
- **Wayland: `wayland-client` + `wayland-protocols` + `wayland-protocols-wlr`.** Not
  `smithay-client-toolkit`: the renderer needs one input-less layer surface, capture sessions and
  presentation feedback. The toolkit's window, seat and output helpers would be unused weight.
- **Graphics: `glow` over `khronos-egl`, with `gbm` and `drm-fourcc`.** Not `wgpu` and not `ash`.
  Reasons: libmpv's render API is OpenGL only; the 16 GLSL programs and the golden images port
  unchanged, so pixel parity can be proven; GPU cost is already 17–70 µs per frame on the RX 7800
  XT, so Vulkan has nothing to win there. The context request becomes an honest GLES 3.0.
  Vulkan stays an option for later only if explicit present timing proves necessary.
- **Video.** A thin hand-written FFI over the libmpv client and render APIs (about a dozen
  functions), rather than a third-party wrapper crate.
- **Pro 2 SDK.** `libloading` on `/proc/self/fd/N`, `sha2` for the pin check.
- **Supervisor.** `rustix` for sockets, `flock`, `memfd` and peer credentials; `xkbcommon` for
  key names; `sha2` for the installer. systemd is still driven through `systemctl` and
  `systemd-run`, as today.

## 3. The head-tracking driver

### Current pipeline (Luma Ultra, `35ca:1104`)

1. **Read.** Raw usbdevfs. Interrupt endpoint `0x81`, 28-byte packets at 1,000 Hz: sequence
   (`u16`), device time in 100 µs ticks, gyro (16.4 LSB per °/s), accelerometer (8,192 LSB per g),
   temperature. No magnetometer, no checksum. Eight URBs per endpoint and a preallocated ring.
   The glasses answer about half of the 500 µs polls empty, so the process wakes about 2,000 times
   per second.
2. **Fusion.** Mahony complementary filter with a rotation-vector update and gyro bias tracking.
   Quality states: Uncalibrated, Coarse, Good, Recovering.
3. **Calibration.** Needs 1.5 s still (`cal_still_s`), with a coarse fallback at 2.5 s. A saved
   bias in `luma-gyro-bias` warm-starts it and is rewritten every 5 minutes.
4. **Prediction.** A `Predictor` (alpha-beta filters) exists, but the daemon publishes the fusion
   output directly. The renderer does its own prediction from pose history.
5. **Publish.** The mutex-guarded SysV feed (writer never waits; it skips on a busy lock and
   handles `EOWNERDEAD`), plus the unused seqlock and `PredictionWire` channels.
6. **Lifecycle.** Runs inside `unshare --user --net`. Read timeout 20 ms, stale after 100 ms,
   silent reopen after 1 s, settle back-off 200 ms doubling to 3.2 s. `LumaWatch` restarts a
   crashed driver at most 3 times in 5 minutes.

Pro 2 (`35ca:1301`): SDK callback thread → 256-entry ring → owner thread that sleeps 2 ms between
polls → publisher. 60 Hz by default (60/90/120/240 selectable). Fails closed, no reconnect.

### Measured today

| Quantity | Value | Source |
|---|---|---|
| Driver added latency, ring mode | p50 5–10 µs, p99 23–40 µs | `docs/performance.md`, fake 1 kHz transport, loaded machine |
| Driver added latency, inline mode | p99 24–25 µs, one run 852 µs | same |
| Tails without real-time scheduling | milliseconds; a short EEVDF slice cut p99 from 2,228 to 26 µs | same |
| CPU | 0.3–0.7 % of one core | same |
| Lost samples | 0 of about 288,000 in 22 runs | same |
| Allocations in the hot path | 0 | same |
| Fusion cost | 0.12 µs per packet median, 10–70 µs worst | replay |
| Seqlock reader sees pose after sensor time | 9 µs p50, 30 µs p99 | same |
| Calibration | 2.60 s while moving (replay), about 1.5 s on a desk | `docs/tracking-3dof-ab.md` |
| XRLinuxDriver calibration | 11.5 s median, plus about 30 s before tracking starts | same |
| Tilt error | 0.30° median, 0.66° p95 | same |
| Heading drift | about 0.5°/min | same |
| Renderer read-to-present | 16.6 ms live | `docs/pro2/smoothness.md` |
| Pro 2 pose age | 9.4 ms p50 at 60 Hz | same |
| Real-glasses USB latency, real-time scheduling, 3 h soak | **not measured** | — |

The "30 seconds of stillness" belongs to XRLinuxDriver. Our driver already needs 1.5–2.6 s. The
goal below is to remove the wait entirely.

### Improvements, each with its measurement

| # | Change | Why | How to measure | Target |
|---|---|---|---|---|
| D1 | Measure the real glasses first: URB completion time against device time, per packet | No baseline exists for real USB. Everything else is judged against it. | New `--latency-log` mode; reuse the `tracking_bench` report format | Baseline recorded with the machine idle and loaded |
| D2 | Lock-free handoff: renderer reads the seqlock channel; the mutex feed is still published for compatibility | Removes the up-to-2 ms lock wait and the skip-on-busy writer path | `pose_age_ms` in the status dict; a reader wait histogram | Pose age p99 ≤ 1.5 ms at 1 kHz; zero reader waits |
| D3 | Driver publishes angular velocity and acceleration straight from the gyro (`PredictionWire`); renderer stops differencing quaternions | Velocity is under 1 ms old instead of about 8 ms, and is not quantised by pose spacing | Replay `luma_imu_real.bin`: predict at 10, 17 and 25 ms and compare with the orientation the recording reaches | Prediction error RMS at 17 ms cut by at least half **(target)** |
| D4 | Late latch in the renderer: start drawing as late as the compositor deadline allows, read the pose last | Read-to-present is 16.6 ms; the untracked path shows about 6 ms is reachable | `read_to_present` p50/p95 in the status dict; `capture_e2e_bench` | p50 ≤ 9 ms, p95 ≤ 11 ms at 120 Hz **(target)** |
| D5 | Use the display vsync events on endpoint `0x83`, which are on the IMU timebase | Gives the prediction horizon the true scan-out time without guessing a clock offset. It also yields sensor-to-vsync latency with no camera. | Horizon error against `wp_presentation` timestamps | Horizon error p95 ≤ 1 ms **(target)** |
| D6 | Temperature bias model: learn bias against temperature per device at rest, store it, and apply zero-rate updates whenever the head is still | Bias moves 0.016°/s per °C; this is the main cause of heading drift | 20-minute desk-still recording and a cold-start warm-up recording, replayed | Drift ≤ 0.1°/min from 0.5°/min **(target)** |
| D7 | Instant calibration: publish at once from the stored bias-at-temperature, refine in the background, never demand stillness | Removes the 1.5–2.6 s wait and the supervisor's fixed 5 s `LUMA_SETTLE` | Time from first packet to first pose and to `Good`; tilt error at 0.5 s | First pose < 100 ms; tilt < 1° within 0.5 s |
| D8 | Reconnect: watch `/dev/bus/usb` with inotify instead of timed back-off, and resume warm (keep orientation and bias, rejoin heading smoothly) | uevents do not reach the namespace; today reopen waits on timers. Second priority after drops. | Fake-transport drop tests, `luma_soak` log fixtures, then real drops | First pose ≤ 50 ms after the device node returns; no visible jump |
| D9 | Real-time scheduling proven: `SCHED_FIFO` 20, `mlockall`, CPU affinity, with a normal-priority fallback | Tails are milliseconds without it | `tracking_bench --load`, p99.9 and worst case | p99.9 ≤ 100 µs under load **(target)** |
| D10 | Fewer wake-ups: reap all completed URBs per wake | About 2,000 wakes per second for 1,000 samples | Context switches per second from `/proc/<pid>/status` | ≤ 1,100 per second, CPU ≤ 0.3 % |
| D11 | Fusion review by replay only: keep Mahony as the reference, compare an error-state Kalman filter with bias-in-state | Without a magnetometer, heading cannot be observed, so a new filter alone will not fix drift. It is adopted only if replay shows it. | Replay tilt error and drift on all fixtures | Adopt only if tilt p95 or drift improves |
| D12 | Pro 2: wake by `eventfd` from the SDK callback, request 240 Hz, add reconnect, run the smoother in the bridge and publish velocity | Pose age 9.4 ms; 2 ms sleep; no reconnect | `provider.pose_rate_hz` and `gap_ms_counts` in `result.json`; pose age | Pose age p50 ≤ 3 ms; survives a drop |
| D13 | Run the 3-hour soak gate before and after the port | First acceptance priority is zero drops, and the port must not make drops worse | `tools/luma_soak` | No more drops than the C++ driver over equal hours |

Out of scope unless Cody decides otherwise: camera-based heading or 6DoF.

## 4. Performance targets

| Area | Today | Target | Measured by |
|---|---|---|---|
| Presented rate | 119.9–120.0 Hz | ≥ 119.9 Hz | `presented_hz`, timing report |
| Missed frames | 0 in accepted runs | 0 `discarded` in a 30-minute run | `missed`, `discarded` |
| Stalls | — | 0 intervals over 34 ms | `stall_intervals` |
| Read-to-present | 16.6 ms | p50 ≤ 9 ms, p95 ≤ 11 ms | `read_to_present` |
| Capture-to-present, head-tracked | 13.15 / 16.47 ms | ≤ 7 / 11 ms | `capture_e2e_bench` |
| Pose age, Luma | — (**not measured** live) | p99 ≤ 1.5 ms | `pose_age_ms` |
| Pose age, Pro 2 | 9.4 ms p50 | ≤ 3 ms p50 | same |
| Sensor-to-vsync (motion-to-photon proxy) | **not measured** | ≤ 12 ms before prediction, residual ≤ 2 ms after | D5 vsync events; optional 240 fps camera check |
| Driver added latency | p50 5–10 µs, p99 23–40 µs | no worse; p99.9 ≤ 100 µs with real-time scheduling | `tracking_bench` |
| Driver CPU | 0.3–0.7 % | ≤ 0.3 % | same |
| Driver lost samples, allocations | 0, 0 | 0, 0 | same, `malloc_count` |
| Renderer GPU time | 17–70 µs (bench), 0.41 ms median stereo | no worse | `gl_bench`; add a GPU timer query to the status dict |
| Renderer GL calls per frame | current `gl_call_counts` | equal or fewer | `gl_bench` |
| Supervisor CPU with overview open | **not measured** (250 Hz loop, one new Hyprland connection per sample) | ≤ 1 % of a core | `pidstat` over a scripted overview session |
| Key press to renderer frame | **not measured** (one Python start per press) | ≤ 5 ms | timestamp in helper against `control_applied` |
| Memory | **not measured** | driver ≤ 8 MiB, supervisor ≤ 20 MiB, renderer no worse | `VmRSS` at 10 minutes |
| Heading drift | 0.5°/min | ≤ 0.1°/min | D6 recordings |
| Calibration | 1.5–2.6 s, plus 5 s settle | first pose < 100 ms, no settle | driver log, `.progress.json` phase times |
| Start-up, confirm to first frame | **not measured** | ≤ 3 s on Luma | phase timestamps added to `.progress.json` |
| Reconnect | 0.9–4.2 s kernel, plus our timers | kernel time + ≤ 50 ms | soak log |

Phase 0 fills in every "not measured" cell before any target is treated as binding.

## 5. Migration phases

Rules for every phase:

- Wire protocols and file formats do not change. Where a new channel is added (D2, D3), the old
  one keeps working beside it.
- The installer pins a matching set. A phase is switched on by a pin or a hidden override, never
  by a mixed, unpinned install.
- Rollback is the existing one: restore the previous pin files, unit and symlink. Old hash folders
  are kept.
- The journal format is frozen, so either supervisor can recover a run the other started.
- A phase ends only when its parity tests pass and the full existing suites still pass (102
  ctest, about 3,671 pytest, the QML tests).

| Phase | Work | Parity proof | Rollback | Effort |
|---|---|---|---|---|
| 0. Baseline | Cargo workspace skeleton. Record every missing baseline (D1, start-up phases, supervisor CPU, memory, soak). Dump a corpus of control frames, generated Lua and journal files from the Python and C++ code. | None needed; nothing ships. | — | 1–1.5 weeks |
| 1. Shared crates | `xr-wire` (all envelopes, strict JSON) and `xr-pose` (SysV feed, seqlock, `PredictionWire`). | `control_wire_check`, the `*_payloads.json` fixtures and a fuzz corpus give identical accept/refuse results in C++ and Rust. C++ `pose_test` reads what Rust writes and the reverse. | Not shipped yet. | 1–2 weeks |
| 2. Luma driver port | Straight port, same behaviour. | Bit-identical pose output on `luma_imu_real.bin` and `luma_imu_upside_down.bin`. Transport tests against the fake transport. Same log lines (`luma: calibrated`). `tracking_bench` no worse. Soak D13. | Re-pin the C++ `xr-luma-driver`. Hidden `tracking-override` value keeps the old one selectable. | 3–4 weeks |
| 3. Driver improvements | D2–D11, one at a time. D2 and D3 need a small consumer change in the C++ renderer's `pose.cpp` and `predict.cpp`, later carried into the Rust renderer. | Each row's measurement in section 3. | Each improvement has a flag in `luma-driver-flags`; default flips only after its gate. | 4–6 weeks |
| 4. Pro 2 bridge | Rust host, SDK through FFI, then D12. | `docs/pro2/fixtures/hardware-2026-11-04/` replay; same `result.json` fields; `smooth_acceptance.py`. | Re-pin the old bundle under `pro2/<manifest-sha12>/`. | 1.5–2 weeks |
| 5. Key helper and controller | `xr_control` first, then the controller service and installer. | Frame-for-frame equality with `xr_control.py`. The 665 controller tests re-pointed at the socket, plus the QML tests unchanged. | Restore the old unit file and bind command. | 2–3 weeks |
| 6. Supervisor | Cold libraries first (journal, Hyprland client, Lua generators, guard), then the run loop and features. Selected per run by the controller. | Generated Lua and renderer frames equal the phase 0 corpus byte for byte. Journal round trip both ways: Rust recovers a Python run and Python recovers a Rust run. Fault-injection recovery tests. | Controller starts the Python supervisor again. Python stays installed for two releases. | 8–12 weeks |
| 7. Renderer | Logic crates first (view, predict, stabilizer, glass, HUD layout, IPC), then Wayland, capture and GL, then video, depth and stereo. D4 lands here in its final form. | Golden images, every `capture_e2e` mode in the loopback compositor, `gl_call_counts`, the timing report, the status dict fields. | Re-pin `xr-native-spike`. | 8–12 weeks |
| 8. Clean-up | Remove retired C++ and Python, port or retire their tests, update `docs/technical.md`. | Full suites. | Git history. | 1–2 weeks |

Order follows value against risk: the driver is first because it is small, provable and holds
the top acceptance priorities. The renderer is last because it is the largest and its biggest win
(D4) can be tried earlier in C++ during phase 3.

## 6. Risks and open questions

### Risks

- **The rewrite does not fix USB drops.** They come from the glasses' side. The plan can only
  prove the Rust driver is no worse (D13) and recover faster (D8). At about 3 drops per hour,
  telling "no worse" from noise needs tens of soak hours.
- **Two implementations for months.** While a Python or C++ part and its Rust twin both exist,
  every feature has to be written twice or frozen. The supervisor is the long one.
- **Recovery correctness.** A bug in the Rust journal or recovery code can leave the desktop
  changed. This is why the format is frozen and cross-recovery is a gate.
- **Test migration.** About 2,594 supervisor tests are Python unit tests of Python modules. Those
  that test behaviour through a wire can be kept; the rest must be ported or dropped knowingly.
- **Hand-written usbdevfs and pthread FFI** are `unsafe` and tied to the kernel and glibc layouts.
  They stay small and are covered by the fake transport and cross-language tests.
- **inotify inside the user and network namespace** (D8) is assumed to see `/dev/bus/usb` changes.
  This must be checked before it is relied on.
- **Late latch (D4)** trades latency against missed frames. A late frame is worse than a slow one,
  so the margin has to adapt.
- **Plugin stays pinned** to one Hyprland commit. Rust changes nothing there.
- **Uncommitted work.** `handoff.md` describes privacy and pinned-window changes still in flight;
  the phase 0 corpus must be taken after they land.

### Hard to reverse

- Giving the seqlock and `PredictionWire` layouts a real consumer fixes their ABI.
- Dropping the Python supervisor and its tests.
- The choice of GLES over Vulkan for the new renderer.
- Changing the on-disk journal, consent or pin formats (the plan does not).
- Any calibration data format written to the user's config (D6).

### Decisions taken (2026-11-10)

Cody asked for best judgement on the questions below. These are the working answers; any of
them can be overturned.

1. Wins first. D2–D4 may land in the C++ renderer before it is ported. One Rust codebase is
   still the end state.
2. No feature freeze. During phase 6 the Python supervisor gets bug fixes only; new features go
   into the Rust one.
3. The SDK is loaded from the user's installed package and never redistributed, until the
   licence text is found.
4. Real-time scheduling is built with a normal-priority fallback. Installing the limits file
   stays a human step.
5. Cameras stay off. The drift target is 0.1°/min.
6. GLES.
7. The hash-pinning installer stays.
8. Soaks run during normal use. No camera rig is assumed.
9. XRLinuxDriver stays as the fallback.
10. `rust/` inside this repo.

Tasks are tracked on the Notion board "XR Workspace — Rust rebuild".

### Questions for Cody

1. Is the aim a single Rust codebase, or measurable wins only? If wins come first, may D2–D4 land
   in the C++ renderer before it is ported?
2. Is a feature freeze on the Python supervisor acceptable while it is ported (two to three
   months)?
3. Do you have the VITURE SDK licence text? It was not found in the repo or the package. May we
   keep loading `libglasses.so` from our own Rust host, and may it be redistributed?
4. May the driver install `/etc/security/limits.d/90-xr-luma-driver.conf` (root, needs a reboot)
   so real-time scheduling can be measured and used?
5. Do the cameras stay off for good? Without a camera or magnetometer reference, 0.1°/min is the
   realistic drift floor, not zero.
6. Renderer stays on GLES (recommended), or do you want Vulkan?
7. Keep the hash-pinning installer, or move to a PKGBUILD?
8. Can you give real-glasses time for soaks (tens of hours) and, optionally, a 240 fps camera
   check of motion-to-photon?
9. Does XRLinuxDriver stay as the fallback for other glasses and behind `tracking-override`?
10. Is a `rust/` workspace inside this repo the right home?
