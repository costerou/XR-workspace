# Plan: capture-to-glasses and head tracking

Follows the qualified managed lifecycle (`phys3`–`phys5`) and tracking feasibility (`docs/feasibility/tracking`). User grants on 2026-10-31:
- **Capture:** only the app's own `XR-1..3` outputs, in memory, never to disk or network, never DP-1/DP-2.
- **Tracking:** via network-blocked XRLinuxDriver.

## Milestones

**C1. Capture to glasses (SHM, CPU blit).**
- Renderer binds `ext_output_image_capture_source_manager_v1` and `ext_image_copy_capture_manager_v1` on its existing connection. It captures each allowlisted output into SHM buffers and nearest-neighbour blits all three, side by side, into its layer surface on the target.
- Allowlist: the supervisor passes `--capture XR-1,XR-2,XR-3`. The renderer accepts only exact names `XR-[1-3]`, never its own `--output`, and fails closed if a name is absent or duplicated.
- No frame IO besides SHM and memfd.
- Metrics: capture frames, failures, ready-to-present age, blit time.
- Consent: a new capture scope, `capture: "managed-outputs"`, with `allow` including `capture-managed-outputs`, for both P2 managed and VM-isolated consents. `false` keeps today's behaviour exactly.
- Acceptance: offline tests, then a VM run (headless outputs plus pattern windows captured and presented), then a live run with the user's visual confirmation.

**C2. GPU presentation.**
- EGL/GLES on the layer surface (`wl_egl_window`). Captured SHM frames are uploaded as textures; then dmabuf capture with `EGL_EXT_image_dma_buf_import` for zero-copy, with explicit sync where offered.
- Reports copies, fallbacks and per-frame GPU/CPU time. Presentation cadence is measured with presentation-time, separately from capture cadence.

**T1. Tracking adapter.**
- In the renderer: a reader for XRLinuxDriver's SysV pose feed (see the feasibility doc). Stale timestamps or `disabled` mean tracking loss and fall back to a head-locked layout.
- Recenter.
- Late pose sampling per presented frame: world-anchored flat desktops rotated by yaw/pitch/roll.
- Supervisor: network-blocked driver start, readiness on calibration, clean stop.

**T2.** Smooth follow, Anchor and 0DoF modes, per-display transforms and curvature, and head-direction focus. Each will be specified before implementation, and focus/input control needs a separate grant.

## Constraints carried forward

- Never capture non-managed outputs, and never write frames anywhere.
- Never claim 120 FPS from mode readback: report measured presentation cadence only.
- Normal managed safeguards and isolated-mode separation are unchanged.

## Progress

**C1 done (2026-10-31).**
- VM: `isolated-vm-capture-live.json`.
- Live: `physical-capture-cap1-live.json`. The user saw XR-1..3 as three tiles in the Luma, updating live. There were 0 capture failures, 0 discarded presentations, and no files or writable file mappings in the renderer.
- Bugs found on the way:
  - Constraints arrive asynchronously, so setup now waits for them (bounded).
  - Hyprland sends `shm_format` before `buffer_size`; constraints now accumulate in any order.
  - The renderer inherited stdin; it now gets `/dev/null`.
- Measured CPU/SHM path: 1,215 presented over 33.8 s (mean interval 27.9 ms, about 36 fps), with ~30 capture frames/s per source. This is far below 120 Hz and motivates C2. Not a latency measurement.
