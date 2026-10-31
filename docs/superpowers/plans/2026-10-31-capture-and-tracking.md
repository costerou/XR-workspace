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

**C2a done (2026-10-31).**
- VM (llvmpipe): `isolated-vm-capture-gl-live.json`. Live (RX 7800 XT): `physical-capture-cap2-live.json`. The user found it smoother than C1.
- While content changes, 1,894/1,943 presentation intervals sit at ~8.33 ms (the 120 Hz mode). The renderer is damage-driven, so idle gaps raise the mean.
- Composition draw+swap mean is 62 µs; SHM upload mean is 0.44 ms. About 1 GB/s of CPU copies remains, which C2b removes.
- Found and fixed: Mesa wrote a shader cache to disk. The renderer now runs with `MESA_SHADER_CACHE_DISABLE=true`.
- Next: T1 (pose-driven redraw every vsync) and C2b (dmabuf zero-copy).

**View controls done (2026-10-31, user-requested).**
- Smooth zoom between the overview and a single desktop.
- Ctrl+Alt+Left/Right switching, with focus and cursor following.
- Spacing via Ctrl+Alt+[ / ], saved in `~/.config/viture-workspace/layout.json`.
- Binds exist only during a run (Ctrl+Alt+arrows stay free for text navigation otherwise).
- The view covers the whole output, so the Luma's own bar no longer shows above the tiles.
- Evidence: VM `isolated-vm-zoom-controls-live.json`, live `physical-zoom-controls-live.json`. The user confirmed zoom, switching and focus work and feel smooth. Spacing keys are not yet pressed live.

**T1 done (2026-10-31).**
- VM: synthetic driver feed, `isolated-vm-tracking-live.json`. Live: `physical-tracking-trk1-live.json`.
- The user confirmed yaw, pitch and roll keep the desktops fixed in space, and Ctrl+Alt+Space recenter works and feels good.
- Measured: a sustained ~119.8 Hz presentation over 3.5 min (mean 8.346 ms, 0 discarded, 0 tracking losses) with the driver network-blocked.
- Not yet: motion-to-photon latency, prediction, the Beast, long-session comfort, Anchor/0DoF/smooth-follow modes.
- Next: C2b (dmabuf zero-copy), then T2 modes.

**C2b implemented (2026-10-31), VM fallback verified; live zero-copy pending.**
- Each capture session that advertises a dmabuf device gets three GBM buffers on that device's render node. They are imported once into GL as EGLImages and wrapped as `wl_buffer`s via `zwp_linux_dmabuf_v1`. The compositor renders into the slot that is neither the latest frame nor the one GL is sampling, so there is no CPU copy.
- Format choice: XRGB8888, then ARGB8888. Among offered modifiers EGL can import, LINEAR comes first. An implicit modifier stays implicit end to end.
- Every missing piece falls back to SHM upload and records a fixed reason in `outputs[].fallback`:
  - no GPU composition;
  - no linux-dmabuf;
  - no render node;
  - GBM allocation failure;
  - no importable format;
  - EGL import failure;
  - the compositor rejecting the buffer or the capture.
- Fixed while here: constraints that changed during an in-flight frame could stall capture. They are now applied once that frame completes or fails.
- VM: `isolated-vm-dmabuf-live.json`. The guest cannot allocate GBM buffers, so all three outputs report `shm` with "gbm allocation failed"; capture, tracking and teardown are unchanged. Zero-copy itself needs the live RX 7800 XT run (`path: "dmabuf"`, `composition.path: "gles2-dmabuf-import"`, no upload counts).
