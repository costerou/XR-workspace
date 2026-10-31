# Native XR backend decision — 2026-10-31

Status: **source-qualified design, not a working XR path**. Milestone A is read-only evidence and diagnostics. Milestone B remains subject to plan review and explicit runtime permissions. Neither tracking backend is cleared for execution or distribution. No SDK, capture, mode switch, virtual output or input monitor was run for this decision.

## Decision

Retain the approved **native out-of-process engine + Omarchy control plugin**. Use C++20, CMake, Wayland client IPC, EGL 1.5/OpenGL ES 3.2 and GBM for a disposable native spike. Do not link Hyprland internals or inject an in-process plugin. First qualify independent outputs, non-focusable presentation, capture and restoration without glasses tracking.

**Provisional tracking choice: direct VITURE SDK in an isolated tracking worker, behind our own adapter.** This reuses vendor tracking rather than inventing fusion. It is a qualification target, not a claim that Beast host orientation works. Require user review of the actual license and a provenance-checked x86_64 SDK package before any build or initialization. The pinned public header copy identifies SDK **2.4.0**, not a verified installed library or a guarantee that the mutable vendor site describes precisely that binary.[12][17]

Reason: direct access preserves callback timestamp information and avoids adopting XRLinuxDriver's mouse emulation, shared-memory exposure and metrics infrastructure. The tradeoff is substantial: the inspected driver deliberately uses raw fusion for native-DoF devices, while vendor documentation's Beast native 3DoF claim does **not** establish host-accessible pose in bypass mode.[9][11][17] If a licensed direct SDK cannot supply usable Beast orientation without conflicting onboard stabilization, stop tracking qualification. Do not silently implement fusion or substitute on-device follow for the approved independent-monitor behavior. Revisit an XRLinuxDriver adapter only through a reviewed follow-up resolving its private IPC, licenses and timestamp limitations.

This decision is deliberately reversible. Source evidence does not presently justify selecting a production tracking backend.

## Local evidence boundary

Read the approved spec at `docs/superpowers/specs/2026-10-31-viture-workspace-design.md` with this document.

| Snapshot | Observed | Not established |
|---|---|---|
| `host-report.json`, 15:09:31 UTC | Physical Samsung Odyssey G93SD baseline; x86_64; AMD Navi 32 and Radeon 610M | Which GPU a future renderer will use |
| `luma-ultra/host-report.json`, 15:12:54 UTC, plus `observations.md` | User-confirmed Luma Ultra; USB `35ca:1104`; new DP-2 CVT/VITURE; active `1920x1080` at `60.0` Hz | Firmware, tracking, active 120 Hz, sustained cadence |
| `beast/host-report.json`, 15:14:30 UTC | No matching VITURE USB and no second display yet | This is **not successful Beast enumeration** |
| `beast-connected/host-report.json`, 15:15:02 UTC, plus `observations.md` | User-confirmed Beast; `35ca:1201`; DP-2 Visitech AS/VITURE Beast; active `1920x1080` at `120.0` Hz | XR presentation cadence, host orientation, drift, comfort |

Exact supplemental Luma Ultra `availableModes`: `1920x1080@60.00Hz`, `1920x1200@120.00Hz`, `1920x1200@90.00Hz`, `1920x1200@60.00Hz`, `1920x1080@90.00Hz`. **No 1080p120 token in this observation.** Beast tokens: `1920x1080@120.00Hz`, `1920x1080@119.88Hz`. Do not round 119.88 to 120. Source desktop resolution and glasses scanout resolution are different quantities: three 1080p virtual desktops can be composed into a 1920x1200 presentation, but readability and transport must be tested rather than presumed.

Pair attribution uses user-confirmed sequential hotplug, USB product text and the newly appearing physical display. The diagnostic has no general USB-to-output association algorithm. Microphone `35ca:1102` is not a second tracked pair. The Luma observation maps DP-1/DP-2 to card1 at PCI `03:00.0` (Navi 32); DMA-BUF/render GPU ownership is still untested.

Known collector gap: `session.py` drops Hyprland `availableModes`; `report.py` expects structured `available_modes`. Historical reports correctly retain unknown rather than manufacturing advertisements. Milestone B Task 1 must normalize exact raw tokens with tests, retaining malformed/missing/empty distinctions and provenance. Do not rewrite these historical snapshots as if the CLI collected fields it did not.

## Matching installed Hyprland, not generic Wayland promises

Fresh read-only commands: `hyprctl version`, `pacman -Q hyprland aquamarine hyprlang hyprutils wayland wayland-protocols`, `uname -m`.

- Installed package `hyprland 0.56.2-4`; running report: **0.56.2**, commit **`efb50993780079460b0cbed1363e2166a2de1d9f`**, clean, tag v0.56.2.
- Aquamarine 0.15.1; Hyprutils 0.14.2; Hyprlang 0.6.8; Wayland 1.26.0; wayland-protocols 1.49.
- `pkg-config` reports EGL 1.5, GLESv2 3.2, GBM `26.2.5-arch1.1`, libdrm 2.4.134. GCC 16.2.1 and CMake 4.4.4 are available. These are discovery results, not a native build test.
- Every Hyprland source reference below is pinned to the **running commit**, not current main. Protocol XML versions on disk can exceed the compositor's bound versions: installed linux-dmabuf XML is v6, but this compositor creates v5.[2]

| Concern | Concrete matching implementation | Evidence level / next test |
|---|---|---|
| Independent outputs | `HyprCtl.cpp::dispatchOutput`, lines 1760–1812: `output create headless NAME`, `output remove NAME`; creation asks an existing Aquamarine backend and can return `no backend replied to the request`.[1] | Source-supported; create/readback three explicitly owned names; never infer creation from `ok` alone |
| Workspace and focus | Legacy dispatch translation implements `moveworkspacetomonitor`, `movetoworkspacesilent`, `focusmonitor`, `focuswindow`; `focuswindow` resolves a window selector.[5] | Use `.socket.sock` requests and `.socket2.sock` events for lifecycle, never per-frame shell processes. Re-read clients/workspaces/activewindow after mutations; validate addresses immediately before dispatch |
| Capture | `zwlr_screencopy_manager_v1` v3; `capture_output` with cursor flag, `copy`/`copy_with_damage`, buffer/linux_dmabuf/buffer_done/ready/failed. Non-damage `copy` damages the monitor; first screenshare schedules a frame.[2][3][4] | Selected baseline; initial frame + static source + animated source tests; capture only managed virtual outputs |
| Alternative capture | `ext_image_copy_capture_manager_v1` v1 and image capture sources are constructed; cursor-session creation checks cursor-position permission.[2][21] | Available in source, not selected for first spike. Does not itself create outputs or prove frame cadence |
| Capture permission | Screenshare implementation checks dynamic screencopy permission, delays pending capture and renders denial content when denied.[4] | User must authorize the actual request; a completed frame is not proof of intended pixels. No broad allow-rule injection |
| Presentation without input theft | Layer-shell v5; layer-surface mapping examines keyboard interactivity, with NONE distinct from exclusive/on-demand.[2][8] | Output-bound overlay, keyboard interactivity NONE, empty `wl_surface` input region, no exclusive zone reservation. Protocol semantics support the design; visibility/focus coexistence still needs live proof.[20] |
| Input activity | InputCapture is an active capture/EIS path; InputManager suppresses normal delivery while captured. IdleNotify reports idle/resume, not per-key timing or held-button ownership.[6][7][23] | **No inspected passive global activity API satisfies typing/drag safety.** Do not use capture as an invisible observer. Permission-gated, read-only evdev activity reduction is a proposed experiment, not an existing Hyprland feature |
| Follow-mouse coexistence | InputManager reads `input:follow_mouse`; source presence alone does not prove idle-pointer behavior under programmatic focus.[7] | Test current settings first. No silent global toggle; if necessary, request a temporary override and journal/conditionally restore exactly that value |
| DMA-BUF | Compositor constructs linux-dmabuf v5; format/modifier validation and device/tranche feedback exist.[2][22] | Allocate capture buffers on negotiated render device with GBM; EGL import; log formats/modifiers and cross-device fallback explicitly |
| Synchronization | DRM syncobj v1 creation is conditional on renderer and timeline support; `wp_presentation` v2 is constructed.[2] | Registry/extension negotiation required. Never claim explicit sync for screencopy simply because surface syncobj exists |

### Rendering and timing choice

Prefer **GPU-resident capture buffers**, not a claim of zero-copy: matching `copyDmabuf()` renders the source into the supplied buffer, then signals completion; SHM uses framebuffer readback. Both involve actual work.[4] The proposed path is virtual-output compositor image → compositor GPU copy into client DMA-BUF → EGL image texture → GLES arrangement draw → EGL/Wayland glasses surface → compositor scanout. Cross-GPU imports can add copies or fail; pinning the render device is a measured decision, not an assumption from PCI enumeration.

Use a three-buffer capture pool per output, at most one capture request in flight, and retain the freshest complete image for repeated presentation. Never recycle a buffer while compositor capture or renderer sampling owns it. Initially use compositor ready/release sequencing and GL fences with implicit DMA-BUF synchronization; instrument and stress this path. Surface explicit synchronization, if exposed by the EGL stack, is separately measured. Stop on corruption/fence failure; a blocking `glFinish` diagnostic may localize a race but is not an accepted performance solution.

Use `wl_surface.frame` for pacing and `wp_presentation` feedback for actual presented/discarded events and clock identity. The renderer must redraw static desktops with fresh head pose, independently of capture damage. Capture `ready` timestamps are **not display timestamps**: matching ScreenshareFrame explicitly notes its timestamp is after commit to Aquamarine rather than page flip.[3][4] No software metric establishes photon latency.

SHM/readback plus texture upload is an explicit diagnostic fallback, not an automatic success path. Record CPU/GPU time and bytes transferred; stop or seek approval if this cannot meet the same workload. PipeWire/portal is not the first path: it adds negotiation and does not solve independent-output creation. In-process rendering or a different compositor requires user architecture approval, never an opportunistic fallback.

## Tracking comparison

| Candidate / version | Host/device coverage | Interface and clocks | License/reuse boundary | Decision / blockers |
|---|---|---|---|---|
| Direct vendor SDK; public header copy **2.4.0** at XRLinuxDriver commit below | Vendor lists Linux x86_64/aarch64, Luma Ultra 3/6DoF and Beast native 3DoF; both pairs physically enumerated here, neither tracked.[12][17] | Provider create/initialize/start/stop/shutdown/destroy; Gen1/2 `VitureImuPoseCallback(float*, uint64_t)`; Carina `xr_device_provider_get_gl_pose_carina(handle, pose, 0.0, &status)` on a worker. Gen1/2 buffer contains roll/pitch/yaw + wxyz in NWU; Carina returns position + wxyz in OpenGL Y-up. Carina poll signature has **no sample timestamp**; do not label poll receipt as sample time.[13][14][17] | Proprietary SDK, dynamic link only after user approval; no binary/header vendoring now. Agreement limits distribution and disclosure and may conflict with public benchmarking/GPL combinations.[18] | **First qualification candidate, blocked on license/package and Beast pose availability.** Preserve raw timestamp and receive monotonic time separately; no prediction until mapped/validated |
| XRLinuxDriver **`958908487a36aad15c6cbb5b7865e404c7b7dfac`** (README's Beast support references v2.11.x; not asserted as checked-out release version) | README supports both architectures and pairs; Beast caveat: extra drift/calibration and possible axis inversion.[9] | `src/devices/viture.c` branches Carina vs native-DoF raw-fusion; vendor callback timestamps divided to uint32 milliseconds. Carina stamps polled pose with IMU callback elapsed time and can reset epoch. Generic IPC orientation array + process-shared mutex; Breezy writes `get_epoch_time_ms()` at export time plus orientation/parity, **not a sensor acquisition timestamp** and not our authenticated versioned API.[11][15][16] | Root LICENSE GPLv3; bundled vendor blobs/headers are **not made GPL by proximity**. IPC avoids directly linking driver code, but does not erase vendor terms or guarantee legal separation. Any code reuse/fork needs its own license review.[10][18] | Fallback research candidate; unmodified daemon not acceptable: documented metrics, default mouse emulation, generic SysV `0666` and Breezy SHM `0644` source modes; actual effective access also depends on environment.[9][15][16] |

Do not equate SDK `native_set_dof`/native smooth-follow with our layout/focus state machine. Vendor docs distinguish native and bypass controls, and dimension convenience switching targets 60 Hz. Native pose availability, onboard stabilization interactions, permitted 120 Hz transport and firmware requirements must be tested separately; stereo 120 Hz is not established by these sources.[17]

For Luma Ultra, request 3DoF before initialize if approved, ignore translation and pass prediction `0.0`. Public Carina callbacks include a roughly 25 Hz camera-rate pose callback, which is not a high-rate substitute for the polling path.[14][17] No camera images are requested or saved. Whether internal 3DoF initialization still uses cameras is unverified; explain that to the user before initialization.

For Beast, attempt only the documented pose callback at a supported frequency in the approved current device mode; report unsupported/no-data honestly. The inspected driver chooses raw callbacks + xrDeviceKit fusion for native-DoF hardware, which makes the direct path a material risk rather than a solved improvement.[11] A separate read-only tree check of xrDeviceKit commit `586520b4b45790076e9f96f3507409dcf56fd913` found no top-level LICENSE/README; this is **not a full license audit** and does not authorize copying its fusion. Do not add that dependency as an unreviewed rescue.

### License and privacy stop gate

The actual vendor agreement (effective September 2025), rather than a search summary, grants object-code development use and distribution only as a component of developed programs. It restricts publishing SDK material, benchmark disclosure to third parties without consent, header disclosure, incompatible copyleft conveyance, and use of “VITURE” in an application's name. It describes data collection unless opted out. These are engineering/distribution risks, not legal advice.[18] The public reference calls its stat reporter opt-in, creating a privacy question that must be resolved for the actual binary; never initialize the reporter or provide credentials.[17]

The developer download UI's retrieved JS contains an email/name/platform request and required agreement checkbox; no form was submitted and no terms were clicked.[19] The agreement also purports to cover accessing documentation. Public pages/headers were inspected under the user's read-only research authorization, **not with authority to assent on Cody's behalf**. Stop additional SDK acquisition/use until Cody reviews these terms. This task does not purport to determine their enforceability.

Before tracking execution: obtain the exact package/version/hash and included license notices through a user-authorized route; resolve local-only/no-telemetry behavior, allowed benchmark reporting (including any third-party/cloud reviewer), application naming, and any distribution intentions. Use a network-denied worker when an approved mechanism is available, with a test that it cannot open network connections; do not silently install sandbox tools or change host firewall policy. If isolation cannot be established, tracking stays blocked. No binaries were downloaded/executed or represented as audited here.

## Unanswered questions become milestone-B tests

| Gate | Required experiment / passing evidence |
|---|---|
| B1 evidence | Normalize the exact `availableModes` tokens offline; preserve old snapshots; new preflight targets explicit physical instance, never bot headless session |
| B2 ownership/recovery | Kill renderer during create/remove and disconnect; independent supervisor readbacks verify reachable windows, no orphan owned outputs, no overwritten user changes |
| B3 compositor | Three independent outputs with three distinguishable local test windows; layer presentation remains visible while each receives keyboard/mouse input; read back workspace/window association |
| B4 capture/GPU | First/static/animated frame correctness, cursor behavior, sustained static redraw, resize/removal, denied permission, format/modifier and buffer-lifetime stress; identify both render and scanout devices |
| B5 focus safety | Permission-approved passive activity source; no key contents persisted; typing and held drag block head selection, deliberate mouse/manual focus wins, idle pointer does not steal focus. If unavailable, automatic head focus is blocked, not shipped without safeguards |
| B6 per-pair tracking | Parent prompts **Luma Ultra**, then separately **Beast**; actual SDK version/firmware, finite normalized quaternions, physical yaw/pitch/roll signs, raw-vs-receipt clock behavior, calibration/drift and USB reconnect; no assumed timestamp epoch/unit |
| B7 transport/performance | Independently verify negotiated mode for intended 2D/any separately approved stereo path, distinguish capture/sensor/presentation cadence. Ten-minute post-warmup three-1080p workload, at least 99% of expected 120 Hz intervals without missed refresh, distributions/worst stalls; unmet modes remain unmet |
| B8 spike exit gate | Review actual renderer/capture/pose/input qualification evidence and user drift/comfort feedback on both pairs, limited to the behavior exercised; unresolved SDK/license/input/render gates block production integration. Smooth Follow/recenter and full controller comfort acceptance are deferred to a separately reviewed focus/follow-controller milestone, not passed by this spike. Numerical photon-latency claims require external measurement |

The spike plan is `docs/superpowers/plans/2026-10-31-viture-native-spike.md`. It is intentionally a feasibility slice, not permission to build the complete editor/plugin or declare the product complete. Passing B1–B8 permits review of the subsequent production plan, not daily-use/production signoff. The later controller milestone must include deterministic replay tests and per-pair hardware evaluation of 0 DoF, Anchor, Smooth Follow and the actual recenter binding. Cody must confirm comfortable follow and tolerable drift on both pairs before daily-use/production signoff; all acceptance requirements in the approved design's “Performance and acceptance” section remain mandatory.

## Retrieval provenance

Source inspection used read-only HTTPS GET via Python stdlib; no downloaded program, installer, build or import was executed. Public headers were read as text in scratch, not copied into this repository. Commit-pinned source URLs below are immutable references; mutable vendor pages were fetched 2026-10-31. Raw HTML SHA-256: SDK reference `cc21ad778fed10b24e9c83d777ba9b92ae61ca589439d5e62292f032edecc316`; agreement `0dbe8990abb806860887da40c34be6ad79954d608155d1565a641cc650cf601d`. These hashes identify inspected responses, not vendor signatures or binary provenance. Scratch evidence is temporary; re-fetch and re-review terms before use.

## Sources

[1] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/debug/HyprCtl.cpp — hypr/src/debug/HyprCtl.cpp
[2] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/managers/ProtocolManager.cpp — hypr/src/managers/ProtocolManager.cpp
[3] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/protocols/Screencopy.cpp — hypr/src/protocols/Screencopy.cpp
[4] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/managers/screenshare/ScreenshareFrame.cpp — hypr/src/managers/screenshare/ScreenshareFrame.cpp
[5] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/config/legacy/DispatcherTranslator.cpp — hypr/src/config/legacy/DispatcherTranslator.cpp
[6] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/protocols/InputCapture.cpp — hypr/src/protocols/InputCapture.cpp
[7] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/managers/input/InputManager.cpp — hypr/src/managers/input/InputManager.cpp
[8] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/desktop/view/LayerSurface.cpp — hypr/src/desktop/view/LayerSurface.cpp
[9] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/README.md — xrl/README.md
[10] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/LICENSE — xrl/LICENSE
[11] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/src/devices/viture.c — xrl/src/devices/viture.c
[12] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/include/sdks/viture_version.h — xrl/include/sdks/viture_version.h
[13] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/include/sdks/viture_device.h — xrl/include/sdks/viture_device.h
[14] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/include/sdks/viture_device_carina.h — xrl/include/sdks/viture_device_carina.h
[15] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/src/ipc.c — xrl/src/ipc.c
[16] https://raw.githubusercontent.com/wheaney/XRLinuxDriver/958908487a36aad15c6cbb5b7865e404c7b7dfac/src/plugins/breezy_desktop.c — xrl/src/plugins/breezy_desktop.c
[17] https://www.viture.com/developer/glasses-sdk/glasses — Vendor public SDK reference
[18] https://www.viture.com/viture-sdk-license-agreement — Vendor SDK agreement, effective September 2025
[19] https://www.viture.com/developer — Vendor download request UI
[20] https://raw.githubusercontent.com/swaywm/wlr-protocols/b010a03648b88d143236de193bddbfea0c08bc84/unstable/wlr-layer-shell-unstable-v1.xml — wlr/unstable/wlr-layer-shell-unstable-v1.xml
[21] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/protocols/ImageCopyCapture.cpp — hypr/src/protocols/ImageCopyCapture.cpp
[22] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/protocols/LinuxDMABUF.cpp — hypr/src/protocols/LinuxDMABUF.cpp
[23] https://raw.githubusercontent.com/hyprwm/Hyprland/efb50993780079460b0cbed1363e2166a2de1d9f/src/protocols/IdleNotify.cpp — hypr/src/protocols/IdleNotify.cpp
