# Head stabilization implementation plan

Goal: reduce micro-shake without perceptible extra turn latency, shared by every driver and rendering mode.

Architecture: a timestamped quaternion adaptive low-pass at the renderer's single pose intake, before PoseState and renderer prediction. Preserve driver prediction. Use shortest-arc interpolation, speed/acceleration adaptation and exact pass-through on clear motion. Filter no animation. Reset on timestamp discontinuity and recenter. Off is exact bypass; default Light (25), Medium (60), Strong (100). Continuous integer strength 0–100.

Constraints: work offline in this worktree; no install, merge, live controller, units, Hyprland, driver or glasses access. No agents. Commit as costerou and push head-stabilization normally.

- [x] Tests first: quaternion tremor reduction 25/45/60%, deliberate turns 30/90/300 deg/s under 2 ms, stops without overshoot, ten-minute stationary drift, sign invariance, duplicate/reset timestamps, setting changes.
- [x] Implement isolated filter in spike/include/xr/stabilization.hpp; consume in wayland.cpp before tracking/prediction. Add CTest and an offline trace executable sharing the production implementation.
- [x] Test and implement validated absolute stabilization settings through view/profiles/controller IPC; optional renderer field for old payload compatibility; shared QML display controls before and during sessions.
- [x] Replay real axes and imported USB captures offline; report jitter as angular RMS around local trend, explicitly distinguish measured movement from unknown physical ground truth. Measure synthetic known-speed phase error and real added angular error/speed.
- [x] Generate reproducible plots and machine-readable measurements in the requested preview directory; document limitations.
- [x] Full CTest with iGPU render tests, pytest, QML and bind_check. Self-review, commit and push; no install.

Review focus: invalid settings; stale/reconnecting feeds; quaternion sign flips and arbitrary axes; sub-degree float precision; original prediction's amplification of stationary noise. Explicit speed bands cannot establish physical motion-to-photon latency without a reference sensor.
