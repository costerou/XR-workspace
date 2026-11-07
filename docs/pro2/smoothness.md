# Pro 2 motion: why the first live test felt like lost frames

First live test: `pro2-supervised-20261106-191126`, 90 s at 44.5 degrees. The picture held still in the right direction and was comfortable, but it was "not as smooth as the other glasses, not really jitter, almost like it was losing frames". This page explains what happened, what changed, and the exact numbers. Everything below is offline: captures replayed through the renderer's own code. Nothing here has been seen in the glasses yet; [smooth-live-test.md](smooth-live-test.md) is that step.

## What went wrong

Three separate things, found in this order.

**Prediction never ran.** The bridge published `look_ahead_cfg {0,0,0,0}`. Its fourth value is the cap that bounds the prediction, so `look_ahead_from` (`spike/src/predict.cpp`) refused it as unbounded, `prediction_horizon` returned nothing, and all 10691 frames counted as unpredicted while status said `enabled:true`. The history was fine. `tests/pro2_runtime/test_prediction_feed.py`, through the real bridge, Publisher, PoseFeed and `predict.cpp`, reproduced it: `gaps_ok 38, omega_ok 38, horizon_ok 0, cap_ms 0` ([red log](smooth-evidence/prediction-root-cause-red.log)). The old runtime contract even listed "lookahead 0" as a choice; nobody had noticed what a zero cap does.

**The feed is not an even 60 Hz.** Every capture is a strict repeating 15, 15, 20 ms: two gaps of 15 for every one of 20, 60.16 Hz on average, on all three captures. On a 120 Hz display (8.33 ms) most samples last two frames, but the cadence beats against the display, so some last one frame and some three. That uneven hold is the "losing frames" feel, on top of half the frames not moving at all. Speed worked out with the real timestamps is flat across the cadence (8.1, 8.4, 8.4 deg/s by phase), so the timestamps are honest and can be trusted to time-correct.

**Turning prediction on is not enough.** The stock path recomputes the pose from scratch each frame, so each new sample shifts the view by its velocity error times the whole horizon of up to 37 ms, and at a sudden stop it runs past the head by 1.2 to 2.1 degrees (60 to 100 pixels).

## The fix

`spike/include/xr/motion_smooth.hpp`, used only when a run passes `--pro2-test-smooth-ms`, so no other glasses take this path. The view is a state of its own, stepped once per frame: carry on at the measured speed, then ease out what the newest prediction says is left over. A new sample changes the slope, never the position.

The speed comes from the last three samples over their real device-time gap. The stabilizer stays in front of it, so a still head stays still.

### Exact parameters

| Parameter | Value | Why |
| --- | --- | --- |
| `look_ahead_cfg` | `{0, 0, 0, 40}` | Cap 40 ms. No panel constant: the Pro 2's is unmeasured |
| Horizon | age + read-to-present, at most 40 ms | Measured live as about 17 to 37 ms |
| `tau_ms` | 35 | Continuity filter. 15 to 50 swept; shorter gets jerkier, longer drifts further off |
| `max_predict_deg` | 6 | The most the prediction may add |
| `stall_ms` | 40 | No new sample for this long: speed stops being carried. The view can run on by that much time first, at most 8.8 degrees at 200 deg/s, then settles within 6. The bridge ends a feed silent for 100 ms |
| `brake_power` | 3 | Speed scale while slowing is (newest interval's share of the window's speed) to this power |
| `brake_floor` | 4 deg/s | Speeds around this are noise, not a stop |
| `snap_deg` | 30 | 1500 deg/s in a 20 ms gap, past any head: taken at once. A recentre resets the smoother instead |
| `max_gap_ms` | 60 | A longer gap has no usable speed: no prediction |
| Stabilization | 25 | Unchanged |

## Offline acceptance

`tools/pro2_runtime/smooth_acceptance.py` replays each capture through the real `Stabilizer`, `PoseState`, `PoseAge`, `prediction_horizon` and `MotionSmoother` (`spike/tests/pro2_smooth_replay.cpp`) on a 120 Hz grid with 16.6 ms read-to-present, as measured live, at four grid phases. The reference is the capture smoothed by a centred 12 ms Gaussian, weighted so it does not carry the cadence itself. Only [aggregates](smooth-evidence/replay-aggregates.json) are committed.

| Directional capture | Held (live test) | Stock prediction | Smoothed | Limit |
| --- | --- | --- | --- | --- |
| Frames that did not move | 49.8% | 3.9% | 0.84% | 2% |
| Step error p95, degrees | 0.325 | 0.146 | 0.105 | 0.12 |
| Jerk p95, degrees per frame² | 0.627 | 0.224 | 0.068 | 0.08 |
| Slow-turn jerk p95 | 0.281 | 0.174 | 0.046 | 0.07 |
| Lag, ms | 28.3 | 7.6 | 8.0 | 9 |
| Worst overshoot, degrees | 0 | 1.24 | 0.22 | 0.30 |

| Roll capture | Held (live test) | Stock prediction | Smoothed |
| --- | --- | --- | --- |
| Frames that did not move | 50.3% | 5.6% | 1.63% |
| Jerk p95 | 0.666 | 0.210 | 0.069 |
| Slow-turn jerk p95 | 0.295 | 0.205 | 0.059 |
| Lag, ms | 27.6 | 5.6 | 6.7 |
| Worst overshoot, degrees | 0 | 2.14 | 0.05 |

Held in this table is the live test's behaviour: the replay's 28 ms lag and 50% still frames match what that run's own stats imply (pose age 9.4 ms plus read-to-present 16.6 ms). On a stationary head, step p95 is 0.0008 degrees against 0.0014 held, so no jitter is added. With 2 ms of read timing jitter added, jerk rises to about 0.10 and every other limit holds.

About 49 pixels is one degree here, so 0.22 degrees of overshoot is about 11 pixels for a moment at a hard stop. It is not zero: the limit is 0.30, and only `hold` is truly zero. The reference path's own floor is about 0.03 degrees of step error.

Limits of this evidence: three captures from one wearer, 65 seconds in all, none of it a fast whip turn (peak 72 deg/s). The overshoot figures rest on few real stops: 4 in the directional capture and 1 or 2 in the roll one, each seen at four display phases. The parameters were tuned on these same captures; there is no hold-out. `Lag` is software-visible only: panel delay cannot be seen from a capture. Whether it feels right is the live test's question.

## Pose rate

Offline answer, from the pinned SDK (`8dbe441e…`), never loaded:

- `viture_glasses_provider.h`: `is_product_support_imu_frequency(product, mode, rate)`, and support "can differ between raw and pose modes". `viture_protocol_public.h` codes: 0 = 60, 1 = 90, 2 = 120, 3 = 240, 4 = 500, 5 = 1000 Hz.
- Viture's public reference lists Pro 2 pose at 60/90/120 Hz ([protocol-evidence.md](protocol-evidence.md)).
- `objdump` of that function in the pinned `libglasses.so`, read as a file: for `0x1301`, the result is `rate <= 3 || mode != 1`. So in pose mode 60, 90, 120 **and 240 Hz** are accepted.
- That the SDK accepts a rate does not show what the firmware delivers, or on what cadence. The 15/15/20 pattern sits on a 5 ms grid, which suggests the rates may be uneven too.

`--pose-rate` (consent `pose_rate_hz`) takes 60, 90, 120 or 240. It defaults to 60, is capability-checked again at run time, and a refusal stops the run rather than dropping to 60. It is only a flag: the smoothing is the fix that needs no new hardware behaviour. The desk test for it is in [smooth-live-test.md](smooth-live-test.md).

## Why two desktops appeared

The consent asked for one, and `plan_outputs` creates exactly what it is given (`tests/pro2_runtime/test_desktop_count.py`). The only other way to a new output is Add on a desktop's open glass panel, or its N key. The run's stats fit that: XR-2 captured 1215 frames to XR-1's 5224, is the same size as XR-1, and the panel was last open on XR-2. No log records the press itself, so this is the only path in the code, not an observed click.

A supervised test now keeps its consent: no DesktopManager, and Add, Remove and reshape are refused with a notice. A panel session keeps them, as every other model has.

## The panel route

`tools/xr_controller/pro2_route.py`. Off until `python -m tools.xr_controller pro2-route enable --bundle … --evidence <run>/pro2-test-result.json` names a bundle and a clean supervised run of that same bundle: the result carries the manifest hash. With no opt-in, detection, consent and managed runs refuse the Pro 2 exactly as before.

- It runs one pinned bundle: bridge, renderer and plugin, by manifest hash. Not `installed.json`, so it cannot change another model's pins, and a rebuilt bundle turns the route off until enabled again.
- Projection lives in `pro2-settings.json`: 44.5 by default, then 45.5, 46.5, 47.5, 48.5, 49.5 and 50, labelled approximate in the panel and the consent box. An unreadable value is 44.5.
- Nothing falls back. No route means no start, never XRLinuxDriver. A bridge that stops ends the session and puts the windows back: there is no hold and no reconnect yet, so a USB drop ends a Pro 2 session.
- The consent box has nothing to untick: the route's tracking is the route.
- Sessions have a 12 hour deadline; `pro2-sdk` is not in `tracking.SCOPES`, so the incident monitor does not watch its tracking yet. The running Hyprland must be the one the bundle's plugin was built for. Each session leaves its private log folder in the state directory.
- `disable` removes the opt-in; recovery of a session that already ran still works without it.

Luma and Beast: `identify`, consent keys, `profiles.json` and the start path are unchanged; tests compare them.
