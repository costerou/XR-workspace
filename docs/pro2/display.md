# Pro2 display — early evidence / integration contract
Canonical model key `pro2` acknowledged. No hardware changes or USB device access.

Passive `edid-decode /sys/class/drm/card1-DP-2/edid` on Nov 4: VIT manufacturer, generic VITURE name, model 48; base preferred DTD 1920x1080@60, second 120; CTA DTD 120/90/60. Both block checksums valid. No 1200p or SBS timing in current EDID. Generic EDID is not model identity; correlate with hardware stream's exact USB inventory.

Official https://www.viture.com/academy/xr-glasses/pro-2 documents 1080p, 60/90/120 Hz, 50° FOV and **hold L1**, NOT R1, for 2D/3D. Do not instruct R1 or run Luma SBS code. Vendor page does not establish provider packet/FOV semantics; driver must independently source those. 50° axis is not specified, do not silently assume diagonal calibration.

Integrator: preserve physical mode, mode_switch=False. No tracking/provider eligibility follows from EDID. Initial intended picture at 1920x1080@60; if currently higher rate preserve it and defer approval/validation to a later gate. 90/120 and SBS are optional later gates, not initial prerequisites. Do not call apply_session_mode or restore_mode_lua for pro2: both are Luma 1920x1200@120. managed.py:185/204 restoration also needs exclusion even with mode_switch=False. runner.py:111 startup and panel_consent.py eligibility need integrator coordination; no edits here to detection/settings/shared startup.

Owned patch under investigation: view.session_mode accepts NaN refresh as eligible because abs(NaN)>0.5 is false. Regression first, retain all legacy valid modes. Pure model-aware display eligibility helper planned; wiring remains integrator-owned until provider proof.

## Owned implementation / handoff
`tools/native_spike/display_policy.py:initial_picture_mode(model,width,height,refresh)` is a pure observed-mode eligibility predicate: pro2 1920x1080@60 ±0.5 Hz; existing Luma/Beast 1920x1200@120 ±0.5. Unknown keys, nonfinite/bool refresh, noninteger sizes reject. False means preserve and report, NEVER modeset. It is intentionally not wired into startup while exact provider compatibility is unknown. Integrator owns shared startup wiring. Do not replace legacy restore validation with this predicate: Pro2 must not enter restore_mode_lua at all.

`view.session_mode` now rejects nonfinite refresh; a synthetic NaN regression failed before fix (accepted placement), then passed. All tests are offline, not hardware validation.

Aspect design: 1920/1080 = 16:9 per eye. Future SBS, only after recapture and explicit opt-in, would need per-eye dimensions from observed wide mode; never pass doubled width as a mono aspect. Current renderer VideoMode::draw uses eye_viewports and half_fov_tan; no projection/calibration change here. Vendor 50° FOV does not establish optical correction, quaternion mapping, or which angular axis its number denotes. Driver report must resolve FOV source before use; preserve all older model constants.

Next safe live gate (parent permission only): after exact provider proof and integration, inspect current readback, preserve mode, verify first 2D picture at observed 1080p. No 3D or high-Hz requirement. Later L1/SBS gate needs before/after passive EDID and host readback, explicit picture confirmation and recovery plan. No button press or mode switch performed by this stream.

## Review locations
- `tools/native_spike/view.py:875` legacy session restoration eligibility; NaN rejection fix is at line 879.
- `tools/native_spike/hyprland.py:320` applies the fixed legacy session mode.
- `tools/native_spike/managed.py:185` records the restoration mode, and line 204 reapplies it.
- `tools/xr_controller/runner.py:111` legacy startup mode application.
- `tools/native_spike/panel_consent.py:21` fixed Luma consent mode; keep unchanged here.
- `spike/src/video_mode.cpp:88` per-eye projection; `spike/include/xr/video.hpp:43` explicitly names the FOV parameter diagonal.

Verification logs are `/tmp/pro2-display-native-tests.log` and `/tmp/pro2-display-controller-tests.log`. Three added offline tests pass; 530 controller tests pass. Full native suite result will be appended on completion.

Final system-Python verification: native discovery ran 2460 tests in 124.876s, 21 skipped, one loader error: `test_stabilization` cannot import `pytest` (not installed for system Python). No assertion failures. ResourceWarnings for sockets/subprocesses were emitted. Controller discovery: 530 tests, OK. Focused new eligibility tests: 3 tests, OK. `git diff --check` clean. No package installation attempted.
