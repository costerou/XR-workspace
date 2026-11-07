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
| `stall_ms` | 40 | No new sample for this long: speed stops being carried. The view can run on by up to that long at its speed first, then settles within 6 (at 200 deg/s it went 8.8 degrees past the sample in the review's case). The bridge ends a feed silent for 100 ms |
| `brake_power` | 3 | Speed scale while slowing is (newest interval's share of the window's speed) to this power |
| `brake_floor` | 4 deg/s | Speeds around this are noise, not a stop |
| `snap_deg` | 30 | 1500 deg/s in a 20 ms gap, past any head: taken at once. A recentre resets the smoother instead |
| `max_gap_ms` | 60 | A longer gap has no usable speed: no prediction |
| Stabilization | 25 | Unchanged |
| Health age limit | 2 s | Was 0.5 s |
| Tilt limit | 0.3 degrees of lean per 10 of turn, in motion, at the shipped mount | |
| Mount pitch | -11 degrees | Fit to one wearer, provisional |

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

## Why the picture leaned in turns

First smoothing run, `pro2-supervised-20261106-215652`: smoothing on was better and off clearly worse, but "the image tilts when I turn my head": turning right the picture leaned left, turning left it leaned right.

The Pro 2's SDK pose is gravity-referenced: its heading starts near 0, but its pitch does not. The captures start 17.3, 19.5 and 26.8 degrees pitched against gravity. The head turns about gravity, and `PoseState` applied that turn in the glasses' pitched frame (`conj(center) * q`), where a turn about gravity has a roll in it of sin(pitch) of the turn: sin 19.5 degrees is 0.33, the 0.32/0.34 cross-axis fractions of the first directional analysis.

Through the real path, in the directional capture's left and right turns, with a horizontal line at the screen's centre as the measure:

| | Unlevelled (Run A) | Levelled |
| --- | --- | --- |
| Turn right | 26.4 degrees, leans left 8.66 | 27.6 degrees, leans right 1.54 |
| Turn left | 18.8 degrees, leans right 6.16 | 19.8 degrees, leans left 0.64 |
| Lean per 10 degrees of turn, turn alone | **3.67** | **0.012** |
| Fit error, rms degrees | 0.067 | 0.036 |

"Turn alone" splits the rendered lean by least squares into the wearer's own roll (the SDK's gravity roll, coefficient 0.97), the true perspective of a head both turned and pitched (coefficient 1.04), and what is left for yaw. Cody's head rolled and pitched a little in those turns, by up to 3.6 and 5.4 degrees. That is the 1.54 degrees that remains, and any glasses would show it.

Hypotheses, against this evidence:

| Hypothesis | Verdict | Evidence |
| --- | --- | --- |
| A pitched reference, yaw about a tilted axis | **The cause** | The left turn's axis is `[0, 1, -0.025]` in the reference frame, within 1.5 degrees of reference up in body axes (9.6 on the right turn, where the head also pitched). Reference up is 19.5 degrees from body up. The lean follows that: the right sign on both sides, and 3.67 per 10 degrees against 3.34 from sin(19.5) |
| H1 axis or sign mix-up | No | All 24 proper signed permutations of the turn axis leave the same 0.346 off-axis fraction: relabelling axes cannot remove an angle between them. The sign pattern also fits the pitch |
| Active or passive convention | No | `q * conj(c)` is the pure turn, and looking up raises the forward axis from -20 to +15.6 degrees: `q` is body to reference, as used |
| Pose relative to the start, not gravity | No, as worded | Pitch is absolute; only heading starts near 0 |
| Unmeasured mount pitch | Partly, and it does not matter | Upstream's constant -8.5 leaves 1.24 degrees of total lean per 10, and +8.5 makes it 4.58. Levelling also cuts the yaw that head roll leaks into the view from 4.43 to 1.30 per 10, which suggests most of the pitch is a frame offset, not the wearer looking down. Gravity gives it per session, so nothing needs measuring |
| The smoother | No | Held and smoothed show the same 2.85 per 10 |

That was the first fix, levelling to the SDK's gravity at each recentre. It removed the large lean, and it was the wrong reference: see the next section. Three numbers above are superseded by it: the split's "turn alone" figures of 3.67 and 0.012 only show that the render matches geometry in the SDK's own frame. Levelling to that frame makes the second number small by construction, so it says nothing about whether that frame suits the eye, and the remaining 1.54 degrees was wrongly put down to the wearer alone.

## Why the lean flipped, and the mount pitch

Run B, `pro2-supervised-20261106-223104`, with gravity levelling (14.47 degrees taken out): "when I turn left the screen tilts slightly left and when I turn right the screen tilts slightly right". Cody sees neither on the Luma, through the same renderer.

**A head does not turn about gravity.** The Luma's 20 minute capture has raw gyro and accelerometer at 1 kHz with no vendor fusion in between, from the same head. Over 28 real turns of 10 degrees or more, with gravity tilt ranging from -12 to +18 degrees, the turn axis stays put: axis tilt = 0.12 x gravity tilt - 6.2, r 0.19, slope 90% range -0.14 to 0.45 ([turn-axis.json](smooth-evidence/turn-axis.json)). A head turning about gravity would give a slope of 1. So the right frame is fixed to the glasses, and gravity at the start is the wrong thing to level to: it depends on the head's pitch at that moment.

| Lean per 10 degrees of turn, through the same view | In motion | End to end |
| --- | --- | --- |
| Luma, real path (its publisher's -8.5), 34 turns | +0.53 (IQR 0.01 to 1.34) | +0.39 (IQR -0.20 to 1.31) |
| Luma, no mount | +2.04 | +2.17 |
| Luma, levelled to its true gravity | +1.91 | +1.75 |
| Pro 2 directional, no mount | +2.03 | +3.26 |
| Pro 2 directional, levelled at 19.5 | -1.46 | -0.47 |
| Pro 2 directional, -11 | **+0.06** | +1.13 |

Positive leans against the turn, as the first run did. Levelling to true gravity does not clean up even the Luma.

The candidates for what Cody saw:

| | Verdict | Numbers |
| --- | --- | --- |
| (a) True perspective of a flat desktop | Real, not the cause | Pure yaw leaves the line through the screen's centre level. The top edge alone leans 1.99 degrees per 10 of turn on the Pro 2 and 2.53 on the Luma, and the bottom edge leans the other way. The Luma shows more of it and does not bother him. The last run's card said to watch the edges, so it may have added to the impression |
| (b) Levelling over-corrects a fixed frame | **The cause** | The turn axis is fixed to the head. Run B's frame was -14.47. A lean with the turn means the true axis is less tilted than that, and -11.3 predicts -0.57 per 10: the sign and the "slightly" |
| (c) Cody's own head roll | Small, not the cause | The Luma's IQR is about 0.7 either side turn to turn, centred near zero. It does not pick a side |
| (d) SDK gravity not true | Open | Levelling leaves opposite signs on the two glasses, which fits the SDK's pitch not being true gravity, but nothing here measures that. The fixed frame does not depend on it |

**Candidates for the mount pitch, on the directional capture.** The start pitch does not enter: this is a fixed turn on every pose.

| Mount pitch | Lean in motion | End to end | Fits the live runs? |
| --- | --- | --- | --- |
| 0 (first runs) | +2.03 | +3.26 | Against the turn, clearly: yes |
| -8.5 | +0.50 | +1.60 | Upstream's constant, not used. Near the Luma's in-motion lean |
| **-11** | **+0.06** | +1.13 | **Default** |
| -14.47 (Run B) | -0.57 | +0.47 | In motion, the sign and size Cody reported |
| -17 | -1.01 | +0.01 | No: Run B would then have leaned against the turn |
| -19.5 | -1.46 | -0.47 | |

Estimates of the axis: -11.3 from the lean in motion, -11.2 and -11.1 from the angular velocity itself, -15.2 over the whole window, -17 end to end (-17.4 left, -16.8 right). Run B rules out anything past -14.47, which leaves the in-motion estimates. **So -11 fits the two live reports and two estimates, but it rests on two usable turns in one capture and it is not settled.** That capture still has +1.13 end to end, about 3 degrees at a 27 degree hold. On the Luma the in-motion and end-to-end figures agree to 0.09, and here they differ by 1.07; the cause is unknown.

Now shipped: `mount_pitch_deg`, -11 by default, -30 to 30, set in the consent or with `--mount-pitch`, applied by the bridge through the same publisher option the Luma uses. `level_gravity` keeps gravity levelling as a comparison, off by default. The renderer now measures the turn axis in each run, as `turn_axis_forward_deg`, `turn_axis_left_deg` and `turn_seconds`. On the directional capture that reads -10.29 with no mount and -0.14 at -11. The mount that would zero a run is its mount plus `turn_axis_forward_deg`.

## Why the run stopped at about 60 seconds

Run A ended with `stop_reason guard-failed` after 60.2 s of a 120 s lease. The bridge did not end it: `seconds`, SDK and USB were all fine, and its orderly stop is the supervisor's teardown. The stop has no recorded reason, because the guard worker threw every exception away and sent one byte. So one cause is proven as a defect, and this run's own cause is not proven:

- **Proven:** `health.json` is replaced 50 times a second, and `read_private_json` refuses `st_nlink != 1`. A read that opens just before a replace holds the old copy, whole and valid, with no links left: it raises, and any exception ends the guard. Against the real writer and reader that is 514 failures in 5.9 million reads, or one in 2424 with reads spaced as the guard's are: about 20 minutes on average on an idle machine, so about a 9% chance by 58 seconds, more under load.
- **Possible:** the monitor scanned every process's `/proc/*/maps` on the heartbeat thread (48 to 78 ms here, unbounded under memory pressure) against a 0.5 s age limit.

Fixed: a health read accepts the unlinked copy. The scan has its own thread, once a second. The age limit is 2 s; the bridge still stops itself on 100 ms of silence. The guard names any failure on the run's stderr, and the parent's `MEASURED` shows `stop_reason`, `guard_reason` and `provider.failure`, so the next stop explains itself.

## The prediction cap

`max_predicted_deg` reached 6 in Run A. That is normal motion, and my "under about 2.5" was wrong: it came from captures that peak at 54 deg/s, and Run A asked for normal turns and sharp stops. The horizon is 17 to 37 ms, so 6 degrees needs at least 160 to 350 deg/s, and a brisk head turn peaks around there. The real capture sped up, on the same sample clock:

| Head speed | Peak deg/s | Most predicted, degrees | Frames at the cap | Snaps |
| --- | --- | --- | --- | --- |
| Captured | 54 | 1.74 | 0 | 0 |
| x3 | 169 | 3.93 | 0 | 0 |
| x5 | 222 | 5.97 | 0 | 0 |
| x6 | 285 | 6.0 | 0.68% | 0 |
| x8 | 359 | 6.0 | 2.49% | 0 |

Run A recorded only the maximum, so its peak speed is not known. Nothing changed: the cap does its job, for a few frames at the fastest part of a turn. `peak_speed_dps` and `capped_frames` are now in the run's stats.

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
