# Head stabilization

The panel’s **Display → Stabilization** setting steadies tiny head movements. It appears both before starting and during
a session, outside Startup options. Off is 0, Light is 25 (the default), Medium is 60 and Strong is 100. The slider accepts
every whole value between 0 and 100. Changes apply live and persist through the existing view/profile store. Dragging
coalesces pending changes while the controller acknowledges the previous value, so the final value is retained.

The filter lives once, at `Presentation::draw_test_pattern`'s pose intake, before `PoseState` and renderer prediction.
Standard XRLinuxDriver, its 3DoF variant and the new Luma driver all use that intake. Overview, follow, maximize,
edge pan, world-locked video and immersive 3D therefore share the same stabilized head pose. It does not filter desktop
animations. Recentering resets it before taking the new reference. Discontinuities in device time reset it; duplicate
samples do not advance it. Off returns the input unchanged.

## How responsiveness is preserved

`Stabilizer` is a causal shortest-arc quaternion low-pass, with a double-precision accumulator; it never filters Euler
angles or integrates angular velocity. At rest the cutoff is 1 / 0.379 / 0.125 Hz for Light / Medium / Strong. Between
Off and Light, filter influence also increases continuously. Speed and positive acceleration open the cutoff on the
current sample, without a delayed velocity filter. At 20 degrees/second and above, orientation and existing prediction
pass through. Above 3 degrees/second the remaining orientation error is capped at 0.045 degrees (1.5 ms equivalent at
30 degrees/second); below that it is bounded at 0.2 degrees. Tiny slow movement is intentionally smoothed more heavily.
An exact stop after motion snaps to the input. Already-identical orientations are preserved exactly to avoid float
rounding creep. Interpolation is convex along the shortest arc and adds no extrapolation or overshoot.

The renderer still derives angular velocity from the **original** published history, avoiding the false speed caused
by switching filter strengths. Its existing prediction horizon and cap are unchanged during clear motion. The same
motion gate attenuates renderer prediction at rest so it cannot amplify noise back into the view. Driver-side prediction,
if a feed already includes it, is left intact. In base `00c2328`, the Luma prediction module exists, but `FusionEstimator`
publishes `Fusion::update` directly and the renderer uses the existing quaternion-history predictor; this change does
not wire in or replace that separate driver module.

## Offline measurements, 2026-11-04

No install or live trial was performed. The running controller, XR units, compositor, driver and glasses were left alone.
Replays used private feed prefixes and the worktree binary, never the live feed.

| Recording | Duration | Quiet windows | Off RMS | Light RMS | Medium RMS | Strong RMS |
|---|---:|---:|---:|---:|---:|---:|
| Imported 20-minute USB recording | 1,224 s | 852 s | 0.04999° | 0.03246° | 0.02561° | 0.02481° |
| Still capture (`stage-g.lcap`) | 89.5 s | 78 s | 0.000891° | 0.000522° | 0.000289° | 0.000114° |
| Second recorder (`capture-g6b`) | 44.7 s | 9 s | 0.000971° | 0.000561° | 0.000300° | 0.000128° |
| `axes-b.csv` | 40 s | 5 s | 0.006104° | 0.005415° | 0.004994° | 0.005021° |

The long recording improves by **35% / 49% / 50%**; the still capture improves by **41% / 68% / 87%**. The axes capture
is mostly deliberate movement, so its five quiet windows improve less (11% / 18% / 18%); stronger settings are not
necessarily better on every short transition. `axes.csv` (26 s) has no complete quiet windows under the selection rule,
so no still-jitter number is invented for it. Both axes files were used for turn measurements.

| Known synthetic turn speed | Off | Light worst added delay | Medium | Strong |
|---|---:|---:|---:|---:|
| 30°/s | 0 ms | 1.500 ms | 1.500 ms | 1.500 ms |
| 90°/s | 0 ms | 0.500 ms | 0.500 ms | 0.500 ms |
| 300°/s | 0 ms | 0.092 ms | 0.096 ms | 0.099 ms |

These are maxima across 60, 120 and 1,000 Hz and several between-sample onset phases, not averages. Sustained deliberate
turns bypass the filter. Recorded turns in the 30°/s and 90°/s bands add less than 0.001 ms equivalent error at every
preset. None of these recordings reaches the 300°/s band, so **300°/s is synthetic-only**.

Synthetic tremor (8 and 19 Hz, 0.025° and 0.008° amplitudes, arbitrary 3D axis) falls by about **88% / 95% / 98%**. The
predeclared pass thresholds are 25% / 45% / 60%. The real still capture passes those same thresholds; the long mixed
recording separately checks 25% / 40% / 45%. Ten-minute constant and noisy-still tests check no added drift or bias
(within 0.0001°), and stop tests check no overshoot or trailing movement. Quaternion signs, timestamp repeats/restarts,
recenter, prediction preservation/suppression and live Off/strength changes are covered too.

### What these numbers mean

The production C++ filter is exercised by `stabilization_trace`. Real input is sampled as the latest available pose at
120 Hz, matching a display read; no samples are interpolated across gaps. Quiet windows are selected once from the raw
input: complete one-second windows with 95th-percentile speed below 3°/s, maximum below 8°/s and no gap above 25 ms.
Each window is expressed in a common local rotation-vector frame and its linear trend removed. RMS is the length of
that remaining angular variation, aggregated across all qualifying windows. Windows are not selected separately for
each setting. Real recordings are not committed to the repository.

Added delay is angular difference divided by input angular speed, an equivalent-delay proxy; synthetic motion has a
known answer. There is no independent physical head reference, no labeled continuous ten-minute still capture and no
optical display measurement. These results measure the filter before prediction, **not physical head accuracy or
motion-to-photon latency**. Prediction interaction has its own synthetic test. Sensor yaw drift cannot be removed
without an external reference; the filter does not add an integrating source of drift.

## Reproduce without touching XR

Build in the worktree only:

```sh
cmake -S spike -B build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build -j 6
DRI_PRIME=pci-0000_12_00_0 XR_DEPTH_TEST_NODE=/dev/dri/by-path/pci-0000:12:00.0-render \
  ctest --test-dir build --output-on-failure -j 4
```

The iGPU here is PCI `0000:12:00.0`; verify that mapping before using this command on another machine. These are offscreen
GPU tests and the repository's isolated test compositor, not the user's running compositor.

The artifacts are at `/home/cody/.hermes/profiles/coder/previews/stabilization/`: `before-after.png`, `index.html`,
`measurements.json`, replay logs and this report. To reproduce the data, use `python -m tools.luma_capture import-usbmon`
for `usbmon5-xr-20261104-001946.bin`, and `merge` for the saved `capture-g6b` directory. Run `build/driver/xr-luma-driver`
with `--replay FILE --ipc-prefix PRIVATE_PREFIX --pose-log OUTPUT.bin --pose-log-every 1`. A replay must have its own
private prefix. No real driver is started by that command. `stage-g.lcap` is the merged first recorder capture.

```sh
uv run --offline --with numpy --with pillow python tools/stabilization/measure.py \
  --trace build/stabilization_trace --out /home/cody/.hermes/profiles/coder/previews/stabilization \
  AXES.csv AXES-B.csv STAGE-POSES.bin USBMON-POSES.bin G6B-POSES.bin
XR_STABILIZATION_RECORDINGS=/home/cody/.hermes/profiles/coder/previews/stabilization \
  uv run --offline --with pytest --with numpy --with pillow python -m pytest tests/stabilization -q
python -m tools.native_spike.bind_check
```

Run pytest separately for each directory under `tests/`, plus `omarchy-plugin/cody.viture-workspace/tests`,
`spike/driver/research` and `spike/driver/docs/tools`. Bare repository-wide discovery incorrectly collects executable
CTest scripts requiring command-line arguments, and several older test directories reuse module names. Use pytest,
numpy and Pillow in the isolated test environment. QML tests use the real Qt runner offscreen with fake controller sockets.

Initial red runs: the unfiltered baseline gave 0% tremor reduction; settings tests failed because stabilization was absent;
the QML test failed because there were no controls. Expanded tests also caught and fixed float accumulation bias at the
strongest cutoff. Full-suite validation exposed an existing 3D failure-notification race; a pending-failure latch now
prevents stale polling from retrying the engine before the loop consumes the failure. Its deterministic regression
blocks fake-engine cleanup while issuing a poll. Fake-driver lifecycle tests now isolate process discovery from real
running drivers, while retaining the real scanner test against a fake `/proc`.

## Final verification

- 102/102 CTest tests passed, ownership plugin enabled; no skipped GPU tests. The GL logs identify the Ryzen 7 9700X iGPU.
- 3,671 pytest tests passed across the separate suites, with 24 existing environment-dependent skips; the final native
  suite alone passed 2,434 tests and 10,799 subtests. QML's 11 Python harness tests passed, including live preset/slider,
  pending-value coalescing and pre-start controls.
- `bind_check`: configuration valid, no key clashes. `git diff --check`: clean.
- One unrelated pointer wall-clock test measured 57 ms against a 50 ms limit while compilation and GPU tests overlapped.
  The full native suite subsequently passed when run alone; its limit and implementation were not changed.
