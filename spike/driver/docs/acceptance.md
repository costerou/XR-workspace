# Luma driver: acceptance criteria and merge plan

Owner: drv-review-qa. Written 2026-11-03 against master `c5787ac` and the swarm's unmerged work as it stood at about
about 00:10. Twelve of the sixteen other branches had been pushed. drv-transport, drv-fusion, drv-prediction and
drv-performance were still only in their worktrees, so anything said about a sibling branch may change. Re-check it before
merging.

This document decides when `xr-luma-driver` may replace XRLinuxDriver. Nothing has been accepted yet. Keep three kinds of
evidence apart when reporting, because each proves less than the next:

| Level | Meaning | Who can produce it |
|---|---|---|
| **Offline** | Tests and fakes. No device is opened. | Any session |
| **Bench** | The real driver on the real glasses, with no XR session | Orchestrator with Cody, step by step |
| **Session** | The real driver feeding a real XR session in the glasses | Orchestrator with Cody, step by step |

Zero drops can only be shown at Session level. A green ctest says nothing about it.

## Cody's bar, and what happens when goals collide

"The most high-performance driver for us, with ZERO dropping and the most features." In order:

1. Zero USB drops
2. Lowest latency, sensor to published pose
3. Highest sample rate the hardware supports
4. No drift
5. Instant calibration
6. Rock-solid reconnect
7. As many useful features as the hardware safely allows, each behind a flag with tests

**A higher goal always wins.** If 1 kHz, a real-time thread or a feature turns out to bring drops back, it comes down or
stays off, whatever it would add. Three examples: if drops return at 1 kHz, step down through `ImuRate` (500, 240, 120 Hz)
and record the rate that holds; if a feature's command coincides with a drop, its flag stays off; and if prediction needs
more than the feed carries, the feed does not grow.

Z, S, R and F block replacing XRLinuxDriver. C, L, D and P are held to their targets too, but a miss there is reported to
Cody with the number, and Cody decides. X decides which features may be turned on.

## State at assembly

Written 2026-11-04 on branch `drv-assemble`, with all 17 branches merged and a real capture in hand. **Nothing here has run on
the glasses yet.** Everything below is Offline level.

What the capture settled (`capture-findings.md`): the IMU's endpoint, packet, rate, scales and axes, and the start command
the old driver uses.

What blocks the first live run: nothing. What it has to find out, in stage G of `live-test-plan.md`, in this order:

1. **Whether `FA 55 95` alone starts the IMU.** The timing says it is what opens the stream. Whether the SDK's earlier
   steps were also needed is unknown.
2. **Whether the IMU keeps flowing when nobody reads the camera endpoint.** `FA 55 95` very probably starts the cameras
   too. If the IMU stalls unless `0x82` is drained, the driver needs `--drain-camera`, and the 19 MB/s that may be behind the drops comes
   back. That would remove the main hoped-for advantage of this driver.
3. **Why the glasses drop.** Still unknown. An IMU-only driver could pass the soak because the cameras are idle, not because
   of anything else it does. That is still a win, but the report must say which it was.

### Decisions taken at assembly

Where two pieces disagreed, the safer one won.

| Conflict | Decision | Why |
|---|---|---|
| drv-features waited 2 s after connect and blamed a drop within 3 s; drv-display-control waited 5 minutes and 60 s | 5 minutes and 60 s for both | 5 of the 11 dropped sessions fell at 18 to 135 s. A test pins the defaults |
| Daemon settles 200 ms after a return; Transport holds off 2 s | Both apply, about 2.2 s | 12 of 20 reconnects right after a drop were followed by another. **R1's 1 s target therefore cannot pass yet.** Zero drops outranks reconnect speed. The soak should tune it |
| S4 said nothing reaches 0x04/0x85 by default | The start must: it lives there | Only the exact frames captured from VITURE's own software pass, enforced in `Transport`. `protocol.md` set that rule in advance. S4 is reworded below |
| S3 asked for a stop on exit | None is sent | No capture has shown a stop, and guessing at the firmware channel is forbidden. Step K2 can find one |
| Feature flags that send commands | Refused at the command line | No ids are known (X4) |
| Display control | In the library, not on the command line | Same reason: `UnmappedWire` refuses everything |

The panel offers Standard, 3DoF test and New driver from one setting, `tracking_driver`. Standard stays the default until Cody
accepts (F8). Each fails differently, on purpose. The new driver falls back to Standard if it does not calibrate within 30 s,
and says why (F9). The 3DoF test never does, because a test that quietly ran the standard driver would give a false result.
**Fallback covers the start only.** If the stream stalls after `luma: calibrated`, the session stays on the new driver with the
feed marked stale while the daemon retries, and Cody has to choose Standard and restart.

The live bench run of 2026-11-04 passed stage G with `--start stream` alone: one command, 1000 Hz, no camera traffic, all four
signs right. At rest on the desk, true pitch is 21 degrees, which the feed reads as 12.5 with the mount.

Two pieces are merged but deliberately not wired in:

- **`luma::Predictor`.** Scored on the real session 25 ms ahead, head moving: no prediction 1.35 degrees rms, the renderer's
  existing finite difference 0.195, a raw gyro step 0.128, Predictor 0.115, with a worse tail than the gyro step (2.3 degrees
  against 1.3 at p99.99). That is under 0.1 degrees better than what runs today, and wiring it means changing what the feed's
  history means. `luma_replay_report --predict-ms 25` reruns this; `--predict-ms 10,17,25` is the D3 replay bench
  (`docs/rust-rebuild-plan.md`), RMS and p95 per horizon, run on the fixture by ctest `luma_prediction_bench`.
- **`rt::Pipeline`, the reader and fusion ring.** The daemon runs inline on one thread. On the real capture, parsing through
  publishing takes 0.12 µs, so a ring has nothing to shed. The thread policy is applied.

### Criteria, as of assembly

| Group | State |
|---|---|
| Z zero drops | Untested. Session level only |
| C calibration | Replay: 2.60 s from a moving start (C2), tilt 0.30 degrees (C4). C1, C3, C5, C6 need the glasses. The saved bias is wired, written every 5 minutes because the driver outlives its runs |
| L latency, rate | Replay: 0.12 µs CPU per packet, 10 to 70 µs worst; no allocation in the hot path; 1 kHz. USB and scheduling not yet measured. L3 needs the shadow run, K1 |
| D drift | Replay: about 0.5 degrees/min against an offline reference, tilt held. **Heading is the known weakness**: see `capture-findings.md` |
| P resources | Offline only |
| R reconnect | Fakes only, now including the whole driver over a fake kernel (`test_whole_driver_on_a_fake_kernel`). R1 cannot pass as configured, see above |
| S safety | S1, S2, S5, S6 offline by `contract_test`. S4, S7 by tests. S3 changed. S8, S9 fakes |
| F fit | F1 to F6, F8, F9 pass offline. F10 passes: no stand-ins left |
| X features | X1, X2, X4 pass. The rest need glasses |

## Baselines, measured

From the kernel journal, user journal and `~/.local/state/xr_driver/driver.log` for 2026-11-01 to 11-03, on
xr-driver-git 2.11.9.

| What | Measured | Note |
|---|---|---|
| Hub disconnects on 2026-11-03 | 37, in 16 clusters | A cluster is drops less than 120 s apart. 15 of the 16 began during an XR run. |
| Clusters per hour of XR run | about 1.1 on Nov 1 and 2 (4 in 3.6 h), 1.8 on Nov 3 (15 in 8.5 h) | Nov 1 is approximate. |
| XR run length on Nov 3 | median 7.8 min, longest 100 min | Drops end many of these. |
| Kernel: disconnect to glasses back on USB | fastest 0.9 s, median 4.2 s, 33 of 37 within 17 s; the rest 55 s, 204 s, 302 s and one overnight | Nothing in the driver can beat this. |
| Calibration wait | median 11.5 s, range 10.9 to 15.5 s (69 cycles) | Fixed in the driver. Tracking start adds about 30 s on top unless the faster start works (not tried on glasses). |
| IMU rate | 1 kHz, the fastest VITURE's SDK lists; pose published every sample, the two older samples 16 and 32 samples back | `src/buffer.c`, `viture_device_carina.h`, `viture_protocol_public.h` |

Not measured yet, so they must be taken in the same conditions as the new driver: XRLinuxDriver's CPU use, its
sensor-to-feed latency, and its yaw drift. It was not running when this was written.

`journalctl -k` reads the current boot only, so it shows nothing for earlier days after a reboot. Use `journalctl -k -b -1`.

## Why 30 minutes of soak is not enough

A session on the old driver would often pass a 30-minute zero-drop test by luck. If drops arrive at random at the rates
above, the chance of an **unfixed** driver showing none in a soak of each length is:

| Soak, in total | at 1.1 clusters/h | at 1.8 clusters/h |
|---|---:|---:|
| 30 min | 58% | 41% |
| 1 h | 33% | 17% |
| 2 h | 11% | 3% |
| 3 h | 3.7% | 0.5% |
| 4 h | 1.2% | 0.1% |

So the gate has two parts. **30 minutes without a drop** is the first go/no-go, as Cody asked. **3 hours in total without
a drop** is what lets us say the problem is fixed with about 95% confidence, even at the lower rate. The hours may be
spread over several sessions. This treats drops as independent, which is rough, and three days is a thin sample, so treat
these as sizes for the test and not as exact odds.

## Acceptance criteria

Numbers marked *proposed* are starting points that the first real measurements may revise. Change them here, with the
reason, before the run they apply to and not after it.

### Z. Zero drops (the first requirement)

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| Z1 | No `usb 5-2: USB disconnect` in one continuous soak | 0 in 30 min | `tools/luma_soak` drop gate | Session |
| Z2 | No disconnect across all soaks on one build | 0 in 3 h total | `tools/luma_soak` reports, added up | Session |
| Z3 | Same conditions as the baseline | Same cable, port, DP mode, normal head motion, a real XR session at 120 Hz | Recorded in the soak report | Session |
| Z4 | Changed one thing at a time | No `usb-drop-fix` change (udev, autosuspend, `quiet_driver`, 3DoF shim) goes in during a soak series. If one was already in, say so and take a fresh XRLinuxDriver baseline with it. | Soak report | Session |

Any drop fails the series and restarts the 3 h count, even if the session survives it. A deliberate unplug does not count,
but it must be logged with its time. A bench-only soak is useful first but does not satisfy Z1 or Z2: the drops were
measured during XR runs. If a drop happens anyway, the soak report's last 5 seconds of driver activity is the evidence.

### C. Calibration

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| C1 | First stream sample to `luma: calibrated`, glasses still | 3 s or less, against the 11.5 s baseline | Daemon log | Bench |
| C2 | Same with the glasses moving (put on while starting) | Never hangs; calibrated within `coarse_timeout_s` (2.5 s) at reduced quality, then improves | Daemon log, `FusionStatus` | Bench |
| C3 | Started upside down on a desk | Correct level once worn; steps H21 to H23 pass | Glasses | Session |
| C4 | Up is real gravity | 1 degree or less from the accelerometer's average when still (*proposed*) | Replay, then bench | Bench |
| C5 | Instant start with a saved calibration | `calibrated` within 100 ms of the first sample (*proposed*), then improves to full quality in the background. Needs the saved bias stored between sessions. | Daemon log | Bench |
| C6 | A stale saved calibration does no harm | With a wrong saved bias, tracking recovers without restarting and D1 holds after it settles | Offline with a bad bias, then bench | Offline, Bench |

### L. Latency and rate

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| L1 | USB packet in hand to pose in the feed, added by the driver | p99 under 0.5 ms | `spike/driver/bench`, fake 1 kHz transport | Offline |
| L2 | The same on the glasses | p99 under 1 ms (*proposed*) | Daemon stats | Bench |
| L3 | Against XRLinuxDriver | Age of the feed at the renderer's read is no worse, same conditions, same day | Passive feed reader on each driver | Session |
| L4 | Sample rate published | The highest the glasses hold with zero drops: 1 kHz unless a soak forces it down. Every sample published, no batching. | Daemon stats | Bench |
| L5 | Lost samples | Under 0.1%; no gap over 20 ms while connected (*proposed*). The renderer calls tracking lost at 100 ms. | Daemon stats | Session |
| L6 | Prediction still runs | `angular_velocity` returns a value on every frame | Offline replay through `predict.cpp` | Offline |
| L7 | Worst case, not only the typical case | Longest added delay under 2 ms, on the bench and with the desktop busy (*proposed*) | `spike/driver/bench`, daemon stats | Offline, Bench |

### D. Drift

The bar says no drift. Roll and pitch can meet that, because gravity corrects them. Heading is harder: an IMU-only driver
has nothing to correct it with, and XRLinuxDriver's 6DoF mode probably has the cameras doing that. So zero is the aim for
heading, D1 and D2 are the most we expect to reach, and D4 may show ours behind. If the capture reveals a magnetometer,
test it as a heading correction behind a flag.

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| D1 | Yaw drift, glasses still for 10 min | 0.5 degrees/min or less (*proposed*; `fusion_test` holds this on simulated data) | Passive feed reader | Bench |
| D2 | Yaw error after 5 min of head motion and return to the start | 2 degrees or less (*proposed*) | Passive feed reader, stand or marked start point | Bench |
| D3 | Roll and pitch | No long-term drift at all (gravity corrects them) | Same runs | Bench |
| D4 | Against XRLinuxDriver | Report D1 and D2 for both. If ours is worse, Cody decides whether it is acceptable. | Same day | Bench |

### P. Resource use

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| P1 | CPU at 1 kHz | Under 1% of one core with the fake transport, and no more than XRLinuxDriver on the glasses | Bench; `/proc/<pid>/stat` across a soak | Offline, Session |
| P2 | Memory | Under 1 MB growth over 30 min after startup; no allocation per sample | `/proc/<pid>/status`, tests | Session |
| P3 | Real-time priority | Works without it and without sudo; only an optional extra | Daemon started with no rtprio | Bench |

### R. Reconnect

The contract's "within about 1 s" cannot be counted from the disconnect, because the kernel alone takes 0.9 s at best and
4.2 s typically to bring the glasses back. The clock starts when the device returns.

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| R1 | Kernel's `usb 5-2.1: New USB device found` to first fresh pose | 1 s or less in at least 19 of 20 events. As configured it takes about 2.2 s, on purpose: see the decisions above | Kernel log against daemon log | Bench |
| R2 | The test runs inside the real isolation | Done in `unshare --user --net`, never only outside it. See the note below. | `tracking.py` launch | Bench |
| R3 | Calibration kept when the glasses return within the keep window | `luma: calibrated restored`, no new still period | Daemon log | Bench |
| R4 | Renderer stays alive | Session survives, tracking returns, nothing needs restarting. The feed's shared memory stays the same segments, because a running renderer holds them. | Session | Session |
| R5 | Bursts | The real pattern, 2 to 23 s between drops, does not lead to a tight reopen loop or a hang | Fake test, then real unplugs | Offline, Bench |
| R6 | One start per connection | Exactly one stream-start command per connection, including every reconnect | usbmon | Bench |
| R7 | Glasses stay away | Holds the last pose, marks it stale, recovers whenever they return; no crash at any gap | Fake test, 5 min unplug | Offline, Bench |
| R8 | Every time | 20 unplugs in a row, all recovered with nothing restarted. One failure fails R8. | Bench unplugs, in the namespace | Bench |

Measured 2026-11-03 with a passive listener: **kernel USB plug and unplug events never reach a process in
`unshare --user --net`.** A harmless test event reached a listener on the host and 0 reached one in the namespace.
`lib/kobject_uevent.c` says why: "Restrict uevents to initial user namespace". The contract's "hotplug via libudev or
netlink" therefore cannot work where the driver runs. `transport.hpp` already avoids this: it notices removal from the open
file and return by other means. R2 keeps it honest. A test outside the namespace would pass and prove nothing.

### S. Safety

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| S1 | No network | No network code in the sources; running process has only a down `lo` and no internet sockets | `contract_test`; `/proc/<pid>/net`, `/proc/<pid>/fd` | Offline, Bench |
| S2 | No device resets | No reset, set-configuration, set-interface or clear-halt in the sources or on the wire; no `reset high-speed USB device` in the kernel log. VITURE's SDK does send CLEAR_HALT at start, 4 in the capture. Ours must not | `contract_test`, usbmon, journal | Offline, Bench |
| S3 | Minimal commands | By default the wire shows the start once per connection, **nothing else**, and 0 of MsgID `0x0007`. No stop, until a capture shows one | usbmon report | Bench |
| S4 | No unknown opcodes | Only built commands reach the device. On 0x04, only the exact frames in `allowed_mcu_frame`, each captured from VITURE's own software, and `Transport` refuses anything else before it reaches the wire | `transport_test`, usbmon | Offline, Bench |
| S5 | No VITURE SDK, no new dependencies | `ldd` shows only the C and C++ runtime | `contract_test`, `ldd` | Offline |
| S6 | Cannot move the mouse | No uinput | `contract_test` | Offline |
| S7 | Optional features are off | Display control, firmware readout, brightness and the rest send nothing unless asked for | Tests with a fake transport, S3 | Offline, Bench |
| S8 | One driver at a time | Refuses to start if xrDriver or another copy is running, and never writes the feed alongside it | Fake driver tests | Offline |
| S9 | Clean stop | SIGTERM sends stop, releases the interface and exits. After `kill -9` the renderer recovers the lock. | Tests, Bench | Offline, Bench |
| S10 | Unprivileged | Runs as Cody with no sudo. Access comes from the xr-driver package's `70-viture-xr.rules`, so removing that package needs a replacement rule first. | luma_probe | Bench |

#### S1 to S10 on the Rust driver

`rust/crates/xr-luma-driver/tests/safety.rs` runs the offline half of S1 to S10 against the built `xr-luma-driver` binary
(`cargo test -p xr-luma-driver --test safety`). No test there opens a USB device. Every run uses its own `--ipc-prefix`,
and the one live run is skipped while VITURE glasses are plugged in.

| Id | Rust evidence |
|---|---|
| S1 | `s1_s2_s5_s6_sources_have_nothing_forbidden`: the source scan applies `contract_test`'s rules after stripping comments, and also checks `std::net`, `TcpStream`, `UdpSocket`, `libc::socket` and every `libc::SYS_*` except `futex` and `sched_setattr`. `s1_s2_replay_makes_no_network_or_reset_syscall`: a whole replay with a drop runs under a seccomp filter that kills the process on any socket syscall (`seccomp_filter_kills_a_socket_call` shows the filter works). `s1_s6_running_process_has_no_network_and_no_input_device`: the process runs inside `unshare --user --net`; `/proc/<pid>/net/dev` lists only `lo`, `lo` is DOWN, the tcp/udp/raw tables are empty, and no fd is a socket |
| S2 | The same scan. `libc::ioctl` has one call site, in `usbdevfs.rs`, behind `is_allowed_ioctl` (`libc_sys_refuses_a_forbidden_code_before_the_kernel`). The seccomp filter also kills on USBDEVFS RESET, RESETEP, CLEAR_HALT, SETINTERFACE, SETCONFIGURATION, DISCONNECT and DISCONNECT_CLAIM |
| S3 | `s3_start_once_per_connection_and_nothing_else`: the binary's own wiring runs over a capture with a drop and a device that waits for its start. The wire shows `FA 55 95 00 00` once per connection, twice in all, and nothing else. There is no stop command. Daemon tests: `no_stop_command_configured`, `stop_while_disconnected_sends_nothing` |
| S4 | `s4_every_command_is_an_allowed_frame`: every frame each `--start` mode can send passes `allowed_mcu_frame`. The only data-channel request is MsgID `0x100B` (`--start full`), and MsgID `0x0007` is never sent. `Transport` refuses everything else: `mcu_channel_passes_only_allowlisted_frames`, `usbfs_refuses_the_flash_channel_on_the_ultra` |
| S5 | `s5_ldd_shows_only_the_c_runtime`: `linux-vdso`, `libc.so.6`, `libm.so.6`, `libgcc_s.so.1` and the loader. Rust links no C++ runtime. `s5_dependencies_are_libc_and_xr_pose_only` checks both Cargo.toml files |
| S6 | The scan forbids `uinput` and `/dev/input`. The running process has no such fd |
| S7 | `s7_listen_sends_nothing_and_unported_features_are_refused`: `--start listen` writes nothing. `--feature-*` exits 2 and `--latency-log` exits 4: they are refused, not run differently from the C++ driver |
| S8 | `s8_second_copy_refuses_to_start`: a second copy on the same prefix exits 5 with `already-running` and logs nothing else, while the first keeps running. The feed's own guard: `second_writer_is_refused_until_the_first_is_gone` |
| S9 | `s9_sigterm_stops_cleanly`: SIGTERM, SIGINT and SIGHUP each give exit 0 within 500 ms. `s9_kill_9_leaves_the_lock_free`: after `kill -9` the next copy takes the lock on the same prefix. The interface release: `usbfs_close_cancels_and_releases`. The renderer side: xr-pose `reader_recovers_a_lock_whose_holder_died` |
| S10 | `s10_running_driver_has_no_privilege`: no uid 0, and CapEff, CapPrm and CapAmb are all zero. `getcap` on the binary is empty. `s10_live_run_is_unprivileged_and_waits_quietly` scans for the glasses as the user under the seccomp filter and stops on SIGTERM. It runs only when no glasses are plugged in |

What is not covered here:
- strace is not installed, and the audit does not install it. The seccomp filter stands in for it, and it is the stronger proof: the process dies on its first forbidden call, where a trace only shows what one run did.
- The bench half still needs the glasses: usbmon for S2 to S4, the kernel log for S2, the live netns check for S1, and S10 against the real device rule.
- The Rust driver does not yet have the vsync feed, PredictionWire's motion state, `--latency-log` or any `--feature-*`.

### F. Fit with the renderer (no renderer change)

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| F1 | Bytes match | The real `pose.cpp` reads back what was published | `publish_test` | Offline |
| F2 | Reader untouched | `xr/pose.hpp` feed types unchanged | `contract_test` | Offline |
| F3 | Quaternion order | The feed is x, y, z, w. `Quatf` is w, x, y, z, so the publisher must reorder it. | `contract_test`, `publish_test` | Offline |
| F4 | Older samples | 2 to 50 ms behind the newest and evenly spaced, or prediction switches off without saying so (`predict.cpp`). Keep XRLinuxDriver's 16 and 32 samples. | Test, L6 | Offline |
| F5 | Long sessions | Timestamps stay under 2^24 ms, about 4.6 hours, or a float loses them | Test with a large clock | Offline |
| F6 | `disabled` is cleared | `pose.cpp` treats true as no tracking, and XRLinuxDriver starts true | `publish_test` | Offline |
| F7 | Level and floor | Steps H21 to H23 pass; Grid floor is the right way up | Glasses | Session |
| F8 | Default unchanged | `tracking_driver` stays `xrdriver` until Cody accepts | pytest | Offline |
| F9 | Fallback is visible | Falling back to xrdriver always shows why. A silent fallback would pass a soak on the wrong driver. | pytest, soak report records which driver ran | Offline, Session |
| F10 | No stand-ins | `luma: warning stand-in` never appears on a live run | Daemon log | Bench |

### X. Features

The list and flags are in `spike/driver/docs/features.md`. Most features there are code waiting on ids nobody has seen yet.

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| X1 | Every feature has a flag and tests | No exceptions, including a test that shows the flag off does nothing | `features_test`, `display_test` | Offline |
| X2 | Default state | Only features with no USB traffic may default on. Anything that sends a command defaults off. | `features.md` table against the code | Offline |
| X3 | No polling, ever | With every flag on, each command appears at most once per connection or once per explicit request | usbmon report | Bench |
| X4 | No guessed ids | A feature whose id is unknown sends nothing and says so. Brightness, dimming, firmware and wear query stay disabled until a capture shows the id. Endpoints 0x04/0x85 also carry firmware flashing. | Tests, code review | Offline |
| X5 | Commands earn their place | Each command-sending feature passes a 30-minute soak with only that flag added, after Z2 passes. Any drop keeps it off. | `tools/luma_soak` | Session |
| X6 | The configuration Cody runs | The exact set of flags in daily use passes Z1 and Z2 together | `tools/luma_soak` | Session |
| X7 | Features cost no latency | L1 and L7 still hold with the zero-traffic features on | `spike/driver/bench` | Offline |
| X8 | Wrong guesses are safe | Auto-pause and the OLED saver never trigger while worn across one hour of normal use, before either can default on | Real use | Session |
| X9 | Other devices count | Opening anything but the glasses' own interface, such as the microphone's HID at `35ca:1102`, counts as a new command path and needs X5 | Code review, usbmon | Bench |
| X10 | Cameras stay off | The driver never starts a camera stream. 6DoF stays out until `6dof-feasibility.md`'s plan shows it does not raise drops; then it needs its own full Z series. | `contract_test`, journal | Offline, Session |

## Seams the contract does not pin

The contract fixes `types.hpp`, `parse_imu` and `Fusion::update` exactly. These links between threads it leaves open, so
`contract_test` does not check them. How each ended up:

| Seam | Between | How it ended up |
|---|---|---|
| Device access | contract, transport | usbfs only: the Luma Ultra has no HID interface |
| Hotplug | contract, transport | No libudev or netlink: uevents never reach the sandbox. See R |
| Axes | protocol, fusion, prediction | `parse_imu` returns forward, left, up, measured from the capture |
| Log lines | daemon, integration, soak monitor | The daemon prints `luma: calibrated restored=0\|1`; integration matches the prefix. `tests/luma_soak` and `tests/native_spike` cover their sides |
| Daemon flags | daemon, integration | Integration launches with no flags, so defaults are the live behaviour |
| Capture format | recorder, daemon `--replay` | The daemon reads `.lcap` through `load_replay`, one endpoint. Replayed end to end on the real session |
| Angular velocity | fusion, prediction, publisher | Beside `PoseOut`, not in it. The feed cannot carry it |
| Seqlock | performance, publisher | The compatible feed keeps the mutex |
| Bias saved between sessions | fusion, daemon | `luma-gyro-bias` under the state directory, read at start, written at exit |
| Which features the daemon wires in | features, display, daemon | Those that send nothing, at their defaults. Temperature now has a real source |

## Merge plan

Nothing here merges or installs anything by itself. Merging to master and installing stay with the orchestrator and Cody.
Each branch comes in with a normal merge, never a force push, and the driver stays off in practice throughout, since
`tracking_driver` defaults to `xrdriver`.

`usb-drop-fix` is not part of the swarm but overlaps it: it has `spike/quiet_driver/` and touches `spike/CMakeLists.txt`.
Merge whichever is ready first, and expect the small conflict in the other.

The brief named nine branches, but seventeen are running. All are placed below.

| Step | Branch | Why here | Files it brings | Expect |
|---|---|---|---|---|
| 1 | drv-protocol-research, drv-upstream-issues, drv-6dof-research | Docs only, no risk | `spike/driver/docs/` | Clean. Check no `__pycache__` was committed under `docs/tools/`. |
| 2 | drv-hil-harness, drv-soak-monitor | Stand-alone Python, needed for the first live steps | `tools/luma_probe`, `tools/luma_soak`, `docs/live-test-plan.md` | Clean |
| 3 | drv-recorder | Stand-alone, and the capture it makes unblocks everything | `tools/luma_capture`, `capture-format.md` | Clean |
| 4 | drv-transport | Owns the CMake skeleton and the option | `transport.*`, `spike/driver/CMakeLists.txt`, `spike/CMakeLists.txt` | Conflict in `spike/CMakeLists.txt`; see below |
| 5 | drv-review-qa | Goes with the skeleton so every later step runs it | This file, `contract_test.cpp` | Add the wiring block below in the same merge, or ctest reports a false failure |
| 6 | drv-protocol-impl | Owns `types.hpp`, which all the rest need | `types.hpp`, `protocol.*`, `protocol.cmake` | See below: the compile error |
| 7 | drv-fusion | Needs only types | `fusion.*`, `imu_sim.hpp` | Add/add on `types.hpp`: keep step 6's |
| 8 | drv-publisher | Needs only types | `publish.*` | Add/add on `types.hpp` and `CMakeLists.txt`: keep what is there; see below |
| 9 | drv-performance | Leaf. Needed early for L1, L7 and X7. | `rt.*`, `bench/` and its own `bench/CMakeLists.txt`, `docs/performance.md` | Check the seqlock seam. `bench/stubs` must not end up on the real include path. |
| 10 | drv-prediction | Leaf | `predict.*`, `predict.cmake` | Delete its `tests/stub/luma/types.hpp` |
| 11 | drv-display-control | Leaf, off by default | `display.*` | Add/add on `CMakeLists.txt`: keep what is there; see below |
| 12 | drv-features | Leaf, off by default | `features.*`, `features.cmake` | Clean, after the CMake check below |
| 13 | drv-daemon | Needs every piece above | `main.cpp`, `daemon.*`, `wiring.*`, `daemon.cmake` | Add/add on `CMakeLists.txt`. Replace the stand-ins in `wiring.cpp` with the real pieces (F10). |
| 14 | drv-integration | Last: it launches and pins the finished binary | `tools/native_spike`, `tools/xr_controller`, the panel | Likely conflicts with `usb-drop-fix` if that changed `tracking.py`, `managed.py` or `runner.py` |
| 15 | Set `LUMA_CONTRACT_REQUIRED=1` | Everything is in, so a missing header must now fail, not skip | `spike/driver/CMakeLists.txt` | One line |

### Expected conflicts

The plan below was followed, with drv-review-qa first as asked, and all 15 steps are done on `drv-assemble`. Kept as the
record of what was expected.

**`spike/driver/CMakeLists.txt`.** Four branches add this file, and more add `*.cmake` beside it. Keep drv-transport's. It
globs `src/*.cpp` and `tests/*_test.cpp`, so most pieces need no entry. Take none of the other versions blindly, because
under the glob they break the configure step:

- drv-publisher also defines `xr_luma_driver`, and a second definition is a CMake error. Its test needs `../src/pose.cpp`,
  `../src/predict.cpp` and `../tests` added, because it deliberately compiles the real reader. It also registers test
  name `luma_publish`, which the glob registers too. Give it a glob exception or add those sources to the globbed target.
- drv-display-control defines target `luma_display_test`, which the glob also creates, and a separate `xr_luma_display`
  library. Drop both and let the glob build it, unless the display code is meant to stay out of the main library.
- drv-daemon's file is a placeholder that includes `daemon.cmake`. Keep the include line. `predict.cmake`, `protocol.cmake`
  and `features.cmake` need the same check: each must add only what the glob does not.

**`spike/driver/include/luma/types.hpp`.** Three branches add it: drv-protocol-impl, drv-fusion and drv-publisher. They
differ only in comments and spacing today, and `contract_test` passes on the first. Keep drv-protocol-impl's. If git
reports a real difference in a struct, stop and look, because that branch coded against something else.

**A compile error git will not show.** `kLumaUltraProductId` is defined in both `transport.hpp` and `protocol.hpp`, so any
file including both fails, and the daemon has to. Fix it at step 6: keep the definition in one header and include that
header from the other. Confirmed by compiling the two headers together.

**`spike/CMakeLists.txt`.** A trial merge of the pushed branches with `git merge-tree` shows real conflicts here. drv-transport,
drv-publisher, drv-display-control and drv-daemon each add the same option and `add_subdirectory(driver)` with different
wording, and
`usb-drop-fix` also changes the file. Keep drv-transport's block, plus `usb-drop-fix`'s change. Remove two other lines as well: drv-features has its own copy of the option with `include(driver/features.cmake)`, and drv-protocol-impl has
`include(driver/protocol.cmake)` with no option around it at all, so that one would build even with the driver switched off.
Once `add_subdirectory(driver)` and the glob are in, both includes would also build their tests a second time.

The same trial, run in the order below against master, gave:

- Clean: the three research branches, drv-hil-harness, drv-soak-monitor, drv-recorder, drv-review-qa, drv-protocol-impl,
  drv-features and drv-integration.
- `spike/CMakeLists.txt`: drv-publisher, drv-display-control, drv-daemon.
- `types.hpp`: drv-publisher, as predicted above.
- Not yet tried: drv-transport, drv-fusion, drv-performance, drv-prediction. `usb-drop-fix` was left out too, so
  drv-integration's clean result does not cover the overlap with it.
- A clean merge is not a working build: the duplicate targets and the compile error above only appear on building.

### After every step

```
cmake -S spike -B build/release -G Ninja -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
ninja -C build/release                 # read the exit status
ctest --test-dir build/release -R luma # no GPU, no device
```

Add `pytest tests/native_spike tests/xr_controller` at step 14. Check for a live XR run in a separate step first, and read
the answer. Run the full suite before the final merge.

Before Bench work starts: `luma_contract` must say PASS, not Skipped.

## Wiring this test

drv-transport's glob already builds `tests/contract_test.cpp`, but without what the test needs: on its own it reports a
false failure, because CTest treats exit 77 as a failure unless told otherwise. Add this at the end of
`spike/driver/CMakeLists.txt`, in the step 5 merge:

```cmake
if(TEST luma_contract)
  target_compile_definitions(luma_contract_test PRIVATE XR_ENABLE_LUMA_DRIVER=1)
  target_include_directories(luma_contract_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../include")
  set_tests_properties(luma_contract PROPERTIES SKIP_RETURN_CODE 77)
endif()
```

If the glob changes, the test still needs those three things by hand: the definition, `spike/include` on the include path,
and `SKIP_RETURN_CODE 77`. At step 15 change the definition line to `XR_ENABLE_LUMA_DRIVER=1 LUMA_CONTRACT_REQUIRED=1`.

The test reads the source tree from its own path, or from a first argument. It checks shape, not behaviour: passing means
the pieces will fit together, not that tracking works.

## Order of live work

Each step needs Cody's go-ahead and is run by the orchestrator. `spike/driver/docs/live-test-plan.md` has the exact steps.

1. Done: the USB capture, `parse_imu` and the start command.
2. Done: the capture replayed through the whole driver.
3. Bench: read-only detection, then stage G to find the start that works, then 60 s of stream, then S1 to S4, then C, D, R.
4. Session: 30-minute soak (Z1). Any drop stops the series.
5. Session: up to 3 h in total (Z2), then L3, P1, F7.
6. Session: features, one flag at a time (X5), then the chosen set together (X6).
7. Cody accepts, and only then does `tracking_driver` change default.
