**Audit map — paths relative to repository at00c2328 unless absolute.**

This maps identified USB, display and recovery entry points. A text/source audit cannot prove absence of opaque SDK behavior or all external privileged writes. Comments, diagnostic suggestions and test fixtures were separated from executable actions.

| Area | Source | What it can do / implication |
|---|---|---|
| USB ioctl gate | `spike/driver/src/transport.cpp:171-193` | Only claim/release/submit/reap/discard. Reset, configuration, interface changes, clear-halt and detach rejected twice. |
| USB open / claim | `transport.cpp:329-375,396-444` | Reads discovery data, waits for arrival settle, opens USBFS, claims interface0, queues reads. |
| Teardown / failed transfers | `transport.cpp:449-465,537-568` | Cancels pending requests, releases, closes. A failed IN transfer can trigger reconnect; error wording “gone or reset” does not prove reset. |
| MCU command allowlist | `transport.cpp:164-168,703-705` | Recognizes known95/97 frames; full startup also has a separately wired protocol path. Do not mistake valid device commands for harmless firmware behavior by definition. |
| Counter / reconnect | `spike/driver/src/daemon.cpp:123-172,202-210` | Counts Link::open attempts; one start per successful connection; one-second sample silence can close/retry. |
| Transport adapter | `spike/driver/include/luma/transport_link.hpp:20-24` | Link open pumps an already-started transport while it waits; not every call opens USBFS. |
| Endpoint setup | `spike/driver/src/wiring.cpp:202-213,267-274` | Default IMU81 and MCU85 reads, MCU04 start; optional events/camera/full-start differ. |
| Temperature/features | `wiring.cpp:98,141-145,164-168`; `protocol.cpp:208`; `features.cpp:549` | Passive IMU telemetry. Runtime feature flags requiring uncaptured USB commands rejected. Last temperature can be stale during outage. |
| Quiet SDK shim | `spike/quiet_driver/quiet_driver.c:37-72` | Requests3DoF, holds start, caches successful mode polls. Hold does not cover all provider initialization. |
| Tracking startup/readiness | `tools/native_spike/tracking.py:211-224,272-282,313-328,340-347,364-366,402-425` | Shim SDK-only; readiness from log markers; Luma startup timeout can restart/fallback; running watch restarts dead process, not ordinary reconnect. |
| Standalone active probe | `tools/usb_probe/probe.py:36-71` | Starts/stops SDK; NOT a passive probe. Not run. |
| Cached read-only probes | `tools/luma_probe/sysfs.py:124-141`; `tools/luma_soak/probes.py:23-38`; `tools/xr_probe/hardware.py:56` | Sysfs/proc inventory. |
| Capture | `tools/luma_capture/usbmon.py:64-139`; `rawmon.py` | Read monitor ring; capture ioctls are not device reset ioctls; correct48-byte read format. |
| USB rules | `packaging/udev/71-xr-workspace-glasses.rules:10-14`; installed `/etc/udev/rules.d/71-xr-workspace-glasses.rules` | Access tags, hub and1104 power/control=on. No camera override, reset or deauthorization. |
| XR mode startup | `tools/xr_controller/runner.py:84-114` | Starts tracking if requested, then120Hz mode if enabled, then parks output. |
| Mode writes | `tools/native_spike/hyprland.py:320-343,369-393`; `view.py:863-884` | Runtime hl.monitor rule; park preserves observed mode. |
| Recovery / reload | `tools/native_spike/managed.py:98-110,185-204`; `survive.py` | Reapply mode after connection returns, restart view, preserve session desktops. These are downstream actions but can affect secondary failures. |
| DPMS/privacy | `tools/native_spike/privacy.py:76-77`; `hyprland.py:267` | Privacy affects other output; glasses explicitly DPMS on. No periodic glasses-off loop found. |
| Stereo/depth | `tools/native_spike/immersive.py:23-39`; `sbs.py`; `tools/depth_engine/` | Engine requested for3D desktop; starts before SBS readiness. No direct hub power control identified. |
| Renderer surface | `spike/src/wayland.cpp:1172` | Layer-shell, not xdg fullscreen. Global fullscreen VRR setting does not establish active VRR. |
| Incident observation | `tools/xr_controller/incident_monitor.py:72-84,163,350` | Reads kernel, sysfs, compositor state; DRM times are sampled observations. |
| Incident attribution | `tools/xr_controller/incident_report.py:187-190` | Kernel USB signal automatically creates hardware-leaning high-confidence prose; not causal evidence. |
| Incident hook | `incident_report.py:379-415`; `~/.config/viture-workspace/on-incident` | Validates and invokes hook; installed hook posts a signed local notification, no USB action. Downstream agent actions are outside this small hook's audit. |
| Rolling capture | `~/.hermes/profiles/coder/t3/usb_rolling_capture.py` | Opens usbmon5 read-only; journal watcher, file rotation/copy. Now blind to glasses on bus1. |
| Actual controller | `~/.config/systemd/user/viture-workspace-controller.service` | Imports feature-placement-menu-integrate; key tracking/hyprland files compared to investigative branch. |
| Udev child probing | `/usr/lib/udev/rules.d/69-libmtp.rules:38` | mtp-probe during enumeration. Matched first-drop journal comparison has no immediate MTP precursor. |
| Steam timer | `~/.local/share/SLSsteam/ensure-desktop-coverage.sh --guardian` | Desktop launcher reconciliation every5minutes; observed association weak and nonspecific. No direct USB action found by scoped search. |

**Verified host identities and settings**

- Original hub: usb5-2, xHCI12:00.4 AMD1022:15b7; new hub: usb1-5, xHCI10:00.0 AMD1022:43f7.
- DP-2: discrete GPU03:00.0. Integrated GPU12:00.0 is separate.
- New hub and1104: control on, runtime active, suspended time0. Camera: auto/suspended. Microphone: on/active. New xHCI suspended time0.
- usbcore autosuspend2, quirks empty; xhci quirks0/link_quirk0 at inspection. No glasses USB2-LPM attributes exposed.
- amdgpu runpm=-1/aspm=-1, PCIe policy default, dGPU active/performance auto: current defaults, not recorded failure-time measurements. No inference about actual historical gfxoff state.
- Host Type-C class/UCSI driver path absent. No corresponding historical error evidence.

**Reproduction and validation**

Run `python analysis/drop-root-cause-20261104/timeline.py`, then `captures.py`, then `correlate.py`. They only read journal/state/capture files and write derived artifacts beside themselves. `captures.py` prints unique drops but preserves overlapping-folder observations in its JSON; do not sum duplicates as independent failures. Its final completion window is4.99seconds, excluding the final10ms to avoid classifying teardown as a precursor. Setup packets and four-byte root status are retained; pixel, audio, serial-string and IMU payloads are not.

The timestamp join is based on journal realtime; no NTP-jump correction was inferred. Exact kernel/usbmon timings agree to milliseconds for matched drops. Session driver selection is corroborated by available Luma log/capture evidence in the morning; old consent selection alone cannot prove that no fallback occurred. Unit lifetimes can encompass startup/recovery rather than continuous rendering. `next_renderer_buffer` describes a later exit, never a guaranteed instantaneous3D state.
