# Luma driver: acceptance criteria and merge plan

Owner: drv-review-qa. Written 2026-11-03 against master `c5787ac` and the swarm's unmerged work as it stood at about
23:50. Seven branches had been pushed and the rest were still in their worktrees, so anything said about a sibling branch
may change. Re-check it before merging.

This document decides when `xr-luma-driver` may replace XRLinuxDriver. Nothing has been accepted yet. Keep three kinds of
evidence apart when reporting, because each proves less than the next:

| Level | Meaning | Who can produce it |
|---|---|---|
| **Offline** | Tests and fakes. No device is opened. | Any session |
| **Bench** | The real driver on the real glasses, with no XR session | Orchestrator with Cody, step by step |
| **Session** | The real driver feeding a real XR session in the glasses | Orchestrator with Cody, step by step |

Zero drops can only be shown at Session level. A green ctest says nothing about it.

## What blocks everything

1. **The Luma Ultra's IMU packet and start command are not public.** `spike/driver/docs/protocol.md` found this, and
   `protocol.hpp` agrees: `start_imu(Device::luma_ultra)` returns nothing on purpose, because guessing an opcode is not
   safe. Endpoints 0x04/0x85 also carry firmware flashing. Until a USB capture of a normal session answers both
   questions, the driver cannot read a single sample from the glasses, however good its other parts are. This is
   **Cody's decision to make**: the capture needs one-time sudo setup (see drv-recorder's plan), and it records traffic
   produced by the closed SDK, whose licence he has not accepted.
2. **Nobody yet knows why the glasses drop.** The `0x0007` errors probably follow the drops rather than cause them
   (`protocol.md`). XRLinuxDriver runs the glasses in 6DoF with the cameras streaming, which plain display use does not.
   So an IMU-only driver could pass the soak because the cameras are off, not because of anything else it does. That is
   still a win, but the report must say which it was.

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
| IMU rate | 1 kHz; pose published every sample, the two older samples 16 and 32 samples back | `src/buffer.c`, `viture_device_carina.h` |

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

### L. Latency and rate

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| L1 | USB packet in hand to pose in the feed, added by the driver | p99 under 0.5 ms | `spike/driver/bench`, fake 1 kHz transport | Offline |
| L2 | The same on the glasses | p99 under 1 ms (*proposed*) | Daemon stats | Bench |
| L3 | Against XRLinuxDriver | Age of the feed at the renderer's read is no worse, same conditions, same day | Passive feed reader on each driver | Session |
| L4 | Sample rate published | At least the 1 kHz baseline | Daemon stats | Bench |
| L5 | Lost samples | Under 0.1%; no gap over 20 ms while connected (*proposed*). The renderer calls tracking lost at 100 ms. | Daemon stats | Session |
| L6 | Prediction still runs | `angular_velocity` returns a value on every frame | Offline replay through `predict.cpp` | Offline |

### D. Drift

An IMU-only driver has nothing to correct its heading with, and XRLinuxDriver's 6DoF mode has the cameras. "No drift" may
not be reachable, so measure both and compare before deciding.

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| D1 | Yaw drift, glasses still for 10 min | 1 degree/min or less (*proposed*) | Passive feed reader | Bench |
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
| R1 | Kernel's `usb 5-2.1: New USB device found` to first fresh pose | 1 s or less in at least 9 of 10 events | Kernel log against daemon log | Bench |
| R2 | The test runs inside the real isolation | Done in `unshare --user --net`, never only outside it. See the note below. | `tracking.py` launch | Bench |
| R3 | Calibration kept when the glasses return within the keep window | `luma: calibrated restored`, no new still period | Daemon log | Bench |
| R4 | Renderer stays alive | Session survives, tracking returns, nothing needs restarting. The feed's shared memory stays the same segments, because a running renderer holds them. | Session | Session |
| R5 | Bursts | The real pattern, 2 to 23 s between drops, does not lead to a tight reopen loop or a hang | Fake test, then real unplugs | Offline, Bench |
| R6 | One start per connection | Exactly one stream-start command per connection, including every reconnect | usbmon | Bench |
| R7 | Glasses stay away | Holds the last pose, marks it stale, recovers whenever they return; no crash at any gap | Fake test, 5 min unplug | Offline, Bench |

Measured 2026-11-03 with a passive listener: **kernel USB plug and unplug events never reach a process in
`unshare --user --net`.** A harmless test event reached a listener on the host and 0 reached one in the namespace.
`lib/kobject_uevent.c` says why: "Restrict uevents to initial user namespace". The contract's "hotplug via libudev or
netlink" therefore cannot work where the driver runs. `transport.hpp` already avoids this: it notices removal from the open
file and return by other means. R2 keeps it honest. A test outside the namespace would pass and prove nothing.

### S. Safety

| Id | Criterion | Target | Measured by | Level |
|---|---|---|---|---|
| S1 | No network | No network code in the sources; running process has only a down `lo` and no internet sockets | `contract_test`; `/proc/<pid>/net`, `/proc/<pid>/fd` | Offline, Bench |
| S2 | No device resets | No reset, set-configuration, set-interface or clear-halt in the sources or on the wire; no `reset high-speed USB device` in the kernel log | `contract_test`, usbmon, journal | Offline, Bench |
| S3 | Minimal commands | By default the wire shows start once per connection, stop once on exit, **nothing else**, and 0 of MsgID `0x0007` | usbmon report | Bench |
| S4 | No unknown opcodes | Only built commands reach the device, and none reaches 0x04/0x85 by default | Code review, usbmon | Offline, Bench |
| S5 | No VITURE SDK, no new dependencies | `ldd` shows only the C and C++ runtime | `contract_test`, `ldd` | Offline |
| S6 | Cannot move the mouse | No uinput | `contract_test` | Offline |
| S7 | Optional features are off | Display control, firmware readout, brightness and the rest send nothing unless asked for | Tests with a fake transport, S3 | Offline, Bench |
| S8 | One driver at a time | Refuses to start if xrDriver or another copy is running, and never writes the feed alongside it | Fake driver tests | Offline |
| S9 | Clean stop | SIGTERM sends stop, releases the interface and exits. After `kill -9` the renderer recovers the lock. | Tests, Bench | Offline, Bench |
| S10 | Unprivileged | Runs as Cody with no sudo. Access comes from the xr-driver package's `70-viture-xr.rules`, so removing that package needs a replacement rule first. | luma_probe | Bench |

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

## Seams the contract does not pin

The contract fixes `types.hpp`, `parse_imu` and `Fusion::update` exactly. These links between threads it leaves open, so
`contract_test` does not check them. Each needs one agreed answer before the matching step of the merge.

| Seam | Between | State at writing | Action |
|---|---|---|---|
| Device access | contract, transport | The contract says hidraw first, but the Luma Ultra has no HID interface. Usbfs is the only path. | Update the contract text. |
| Hotplug | contract, transport | Contract's libudev or netlink cannot work (see R). Transport already avoids them. | Update the contract text. |
| Log lines | daemon, integration, soak monitor | The daemon prints `luma: calibrated restored` and `luma: connected`; integration matches `luma: calibrated`. | Agree the list. One test should feed real daemon output to integration's parser. |
| Daemon flags | daemon, integration | Daemon has `--ipc-prefix`, `--replay`, `--stale-ms`, `--reopen-ms`, `--keep-calibration-ms`. | Integration's fake driver must take the same flags. |
| Capture format | recorder, daemon `--replay` | `load_capture` exists; `capture-format.md` is not written yet. | One file replayed end to end before merging the daemon. |
| Angular velocity | fusion, prediction, publisher | `PoseOut` has no field for it and `contract_test` refuses extras. | Keep it beside `PoseOut`, as `FusionStatus` and `predict.hpp` do. The feed cannot carry it anyway. |
| Seqlock | performance, publisher | The task asks for a seqlock in shared memory, but `pose.cpp` reads under the robust mutex. | The compatible feed keeps the mutex. A seqlock is for the later clean IPC or between internal threads only. |
| Bias saved between sessions | fusion, daemon | `FusionConfig` can take a saved bias; nothing stores it. | Optional. Decide whether "instant calibration" wants it. |

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
| 9 | drv-performance | Leaf | `rt.*`, `bench/` | Check the seqlock seam |
| 10 | drv-prediction | Leaf | `predict.*`, `predict.cmake` | Delete its `tests/stub/luma/types.hpp` |
| 11 | drv-display-control | Leaf, off by default | `display.*` | Add/add on `CMakeLists.txt`: keep what is there; see below |
| 12 | drv-features | Leaf, off by default | `features.*`, `features.cmake` | Clean, after the CMake check below |
| 13 | drv-daemon | Needs every piece above | `main.cpp`, `daemon.*`, `wiring.*`, `daemon.cmake` | Add/add on `CMakeLists.txt`. Replace the stand-ins in `wiring.cpp` with the real pieces (F10). |
| 14 | drv-integration | Last: it launches and pins the finished binary | `tools/native_spike`, `tools/xr_controller`, the panel | Likely conflicts with `usb-drop-fix` if that changed `tracking.py`, `managed.py` or `runner.py` |
| 15 | Set `LUMA_CONTRACT_REQUIRED=1` | Everything is in, so a missing header must now fail, not skip | `spike/driver/CMakeLists.txt` | One line |

### Expected conflicts

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
drv-display-control and drv-daemon each add the same option and `add_subdirectory(driver)` with different wording, and
`usb-drop-fix` also changes the file. Keep drv-transport's block, plus `usb-drop-fix`'s change. Remove the other two lines
as well: drv-features has its own copy of the option with `include(driver/features.cmake)`, and drv-protocol-impl has
`include(driver/protocol.cmake)` with no option around it at all, so that one would build even with the driver switched off.
Once `add_subdirectory(driver)` and the glob are in, both includes would also build their tests a second time.

In the same trial, drv-protocol-research, drv-upstream-issues, drv-6dof-research and drv-protocol-impl merged cleanly on
master, and drv-features merged cleanly on top of them. The unpushed branches have not been trial-merged.

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

1. Cody decides on the USB capture (blocker 1). Take it, then fill in `parse_imu` and the start command.
2. Offline: replay the capture through the whole driver.
3. Bench: read-only detection, then 60 s of stream, then S1 to S4, then C, D, R.
4. Session: 30-minute soak (Z1). Any drop stops the series.
5. Session: up to 3 h in total (Z2), then L3, P1, F7.
6. Cody accepts, and only then does `tracking_driver` change default.
