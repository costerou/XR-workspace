# Viture Workspace — proposed design

Status: design approved by Cody on 2026-10-31. Implementation planning in progress; application implementation not started.
Local home: `/home/cody/Projects/viture-workspace`. No GitHub account, remote, publication, or cloud service required.

## Intent
Build a daily-use Omarchy XR workspace for Cody's VITURE Beast and Luma Ultra. Prioritize responsive, comfortable head tracking, stable smooth-follow, independent virtual desktops, and reliable keyboard/mouse focus. XR Monitor Studio is the user's reference experience; its implementation has not been identified or audited.

This is a substantial native desktop project, not merely a settings widget. A mockup is not evidence of tracking, virtual-output, or refresh-rate support.

## Confirmed requirements
- Omarchy integration, preferably presented as a plugin.
- Both Beast and Luma Ultra, normally one pair connected at a time (proposed initial scope).
- Independent virtual monitor outputs, not mirrored copies; support adding/removing monitors and placing distinct windows on each. Three monitors are the initial acceptance configuration, not a fixed product limit.
- Per-monitor XYZ position, yaw/pitch/roll, apparent size, desktop resolution, desktop scaling, and horizontal XR curvature.
- Presets: standard 16:9, ultrawide (21:9 class), super-ultrawide 32:9, portrait 9:16, custom dimensions. Presets initialize editable values, not compulsory modes.
- 0 DoF, Anchor, and Smooth Follow modes; configurable anchor-recenter shortcut.
- Head-direction targeting, not eye tracking. Completed head selection transfers keyboard focus to the target monitor's active window.
- Deliberate mouse movement into another monitor selects it; an idle pointer must not repeatedly reclaim focus.
- Smooth Follow centers and aligns the selected monitor in yaw, pitch, and roll. Move the whole arrangement together, preserving relative placement.
- Adjustable selection dwell and follow responsiveness; stable selection during centering.
- Head-directed focus lock while typing or dragging, enabled by default. Resume after typing inactivity or drag completion.
- Target 120 Hz where the hardware, mode, and transport support it. No unverified claim of flawless performance.

## Architecture and alternatives
The user delegated the architecture choice to the builder after alternatives were presented.

1. Extend a complete existing XR stack: reuse more work, but inherit platform, capture, performance, and licensing constraints. Refract's documented virtual-monitor path is GNOME-specific; Viture Virtual Display describes itself as early development with performance work remaining. Neither has been proven suitable for this project.
2. **Selected direction: native out-of-process XR engine plus Omarchy control plugin**, reusing a suitable tracking backend behind an adapter. Own the focus/follow state machine and Hyprland integration. Better failure isolation and control over timing, at the cost of native rendering and virtual-output integration work.
3. Hyprland in-process rendering plugin: possible efficient compositor access but ABI coupling and desktop crash exposure. Not the default; reconsider only with evidence that the out-of-process path cannot meet requirements and with user approval.

**Approved narrow lifecycle amendment:** Cody approved a small compositor-resident, version-pinned Hyprland ownership broker after accepting ABI coupling and compositor-crash exposure. It only creates/configures headless output objects, assigns workspaces and performs handle-addressed checked evacuation/removal. Rendering, capture, tracking and supervision remain out of process. Offline implementation/build/tests are authorized; plugin loading, isolated compositor execution and physical-session managed-output tests require separate fresh permission. Epoch loss makes old journal UUIDs unproven, not absent. Stock name-addressed deletion and legacy `keyword monitor` are not compatibility fallbacks.

The Omarchy shell plugin is the control/status surface, not the frame renderer. Version one is local-only, no telemetry or sign-in. Use local authenticated IPC with user-only filesystem permissions between controls and engine. Keep configuration and diagnostic events outside the render-critical path.

## Verified host and compatibility boundary
Read-only discovery found x86_64, Omarchy 4.5.0rc2-1, Hyprland 0.56.2, an AMD Navi 32 device reported as RX 7700 XT / 7800 XT, and an AMD Radeon 610M. Exact scanout/render GPU ownership is not yet determined. Initial target is this host, not the user's ARM laptop; avoid accidentally building around the bot's headless display.

Published XRLinuxDriver documentation lists Luma Ultra and Beast support but flags Beast drift/calibration caveats. Official SDK search results suggest newer Beast native tracking support. Neither path is validated here. The builder must inspect actual SDK version, licensing, timestamps, axis conventions, tracking quality and distribution conditions before choosing a backend. Do not run downloaded installers or accept vendor terms silently. Do not let multiple drivers compete for the device.

### Feasibility gates before production integration
1. Identify the physical user session, active GPUs, connected glasses model/firmware and exposed modes. Inspect output mode lists/EDID and available tracking interfaces for each pair. Do not change modes or firmware during discovery.
2. Validate the installed Hyprland version's headless/virtual outputs, workspace mapping, capture protocols, frame delivery for static content, cursor handling and focus control. Prove input can reach virtual desktops while the XR presentation output stays visible and does not steal focus. Never assume an available portal implies independent virtual output creation.
3. Exercise a minimal real capture-to-XR presentation path and determine DMA-BUF import, explicit synchronization support, format/modifier compatibility, GPU copies and multi-GPU costs. Prefer GPU-resident buffers; report fallback copies.
4. Measure head pose timestamps, update rate, drift and reconnect behavior separately on both glasses. Verify display refresh in the intended 2D or stereo mode; do not assume 120 Hz panel capability guarantees 120 Hz in every transport mode.

These are first implementation milestones after design approval. If a gate fails, report evidence and alternatives; do not silently downgrade the request. Some checks require Cody to connect each pair and provide comfort feedback. No performance or tracking success can be signed off from a mockup or synthetic benchmark alone.

## Runtime components
- Device adapter: capability discovery, exclusive device access, timestamped orientation, calibration/recenter and reconnect. Orientation quaternions internally; normalize axes and clocks explicitly.
- Workspace adapter: managed virtual outputs, workspace/window association, monitor and input events, remembered active window per workspace. Use version-supported Hyprland APIs; no per-frame shell subprocesses.
- Renderer: present captured virtual desktops as independently sized/curved surfaces. Separate capture updates from head-pose presentation: a stationary application still needs head-tracked frames. Use freshest complete buffers and bounded queues. Sample pose late; add bounded prediction only after measured latency justifies it. Never smooth raw tracking so heavily that following becomes laggy.
- Focus/follow controller: deterministic timestamp-driven state machine, testable without hardware. Head selection, mouse intent, typing/drag suppression, manual focus, mode transitions and monitor removal share one arbitration policy.
- Settings/UI: Omarchy plugin for mode, recenter, status and opening the full arrangement editor. The approved browser prototype defines interaction intent, not the production implementation technology. Toolkit and native language are builder decisions after feasibility checks.
- Recovery supervisor: restore the user's desktop on explicit exit, disconnect or engine failure, without requiring the renderer to be alive.

## Coordinates, layouts and viewing modes
Per-monitor local transform, physical/apparent extent and curvature belong to a saved layout. A separate global viewing transform implements follow and recenter; it must not overwrite layout geometry.

0 DoF: orientation-independent head-locked workspace view; monitor switching brings the selected desktop into view. No world anchor is claimed.
Anchor: orientation-fixed workspace. Head turns select and transfer keyboard focus but do not auto-center the workspace. Recenter maps the selected monitor's center and orientation to the current head view, moving the group. Translation-tracked room anchoring/6 DoF is not required in version one.
Smooth Follow: selected monitor center and frame converge to the head-centered viewing frame across yaw/pitch/roll. Preserve layout geometry. Use time-based, non-oscillatory interpolation rather than frame-count-based easing. A curved monitor aligns by its center tangent frame.

Manual monitor orientation is retained in local layout coordinates even when the global follow transform aligns the selected screen with the user. Use quaternion interpolation and shortest rotation paths to avoid Euler wrap discontinuities.

Curvature: horizontal cylindrical arc, flat at zero; keep desktop pixels and XR geometry separate. Represent apparent size by a clearly documented center angular size or physical extent at a reference distance. Exact slider ranges and conversion are builder decisions, tested for readability and valid geometry. Presets change pixel aspect ratio and initial physical aspect consistently; a selected preset must not stretch desktop content.

## Focus arbitration
- Raycast head-forward direction against visible monitor surfaces, including curvature; select only after dwell and boundary hysteresis. Use visible closest-hit ordering for overlap; no targeting through an occluding monitor.
- On committed selection, focus the active/last-focused valid window on that monitor's current workspace. If empty, focus the workspace without inventing a window target. Revalidate targets to handle windows closing mid-transition.
- Deliberate mouse entry into a virtual output wins over head selection. Distinguish real pointer input from synthetic warps; suppress events caused by the controller itself. Preserve drag ownership until release.
- During follow centering, hold the selected monitor against geometry-induced reselection. A new deliberate mouse/manual selection may interrupt; head selection becomes eligible after settling plus rearming hysteresis. It must never get permanently locked.
- Typing suppresses head-directed changes only, not an explicit user click or keyboard monitor switch. Detect input activity without storing key contents. Proposed initial idle timeout: 500 ms, adjustable and subject to testing. A button-held drag blocks head selection until release. If global activity cannot be detected safely on this Hyprland version, report that gap rather than claiming the safeguard works.
- Existing compositor focus-follows-mouse behavior must not override a head selection merely because the pointer is stationary elsewhere. Resolve coexistence in the integration spike, avoid globally changing desktop behavior outside the managed session.
- Proposed dwell: 250 ms; not a confirmed user preference or validated comfort value. Separate selection delay from follow dynamics.

## Monitor lifecycle and presets
Start with three independent outputs. Allow add/remove with stable identifiers; don't recycle identity accidentally when indices shift. Actual supported limits are capability- and performance-dependent, not unlimited.
Before removing an output, move its workspaces/windows to a chosen surviving managed output, verify the move and only then remove the output. Abort removal if migration cannot be verified. If the last managed output is being removed, exit XR through desktop restoration instead of leaving windows unreachable. UI may disable last-output removal and offer Exit XR.
Validate requested dimensions/scales against compositor and renderer constraints. Budget by total pixel load as well as monitor count. Never silently substitute a lower resolution or refresh target. Apply layout changes transactionally, with rollback on partial failure.
Save per-glasses profiles and layouts locally with schema versioning and atomic writes. Store mode, follow/dwell settings, focus safeguards, shortcuts and monitor geometry. Auto-detect the active device; a profile dropdown does not impersonate connected hardware.

## Safety and recovery
Snapshot only the display/workspace state the app will modify. Track owned resources explicitly. On disconnect/crash, recover windows onto an available physical output, remove only app-created outputs and undo app-owned temporary settings. Preserve unrelated changes the user made during the session. Startup detects stale resources and offers recovery.
Never silently edit stock Omarchy files, install system packages, alter udev rules, stop another XR service, change firmware, or restart Hyprland. Ask before disruptive integration steps. User configuration changes must be backed up and reversible. Inspect shortcut conflicts before installing bindings; the mockup's Super+Alt+R is only a proposal.
No capture, typing metadata or head-pose data leaves the machine. Diagnostics do not record key content or screen images by default. Local IPC must not expose shell command execution.

## Performance and acceptance
120 Hz is a preferred output mode and presentation target, not a rendered FPS label. Expose negotiated display mode, presentation cadence, missed presentation deadlines, capture cadence, pose age, and GPU/CPU timing where reliably measurable. Clearly distinguish sensor rate, source desktop cadence and XR presentation rate. Do not describe software timing as measured photon latency.

Builder-proposed release benchmark: three independent 1920x1080 outputs at 100% scaling on this host; a mix of typing, scrolling and animated content with head motion for at least ten minutes after warmup. Target at least 99% of expected 120 Hz presentation intervals without a missed refresh during the measured steady-state run. Report distribution and worst stalls, not just an average. Mode changes and startup are recorded separately, not hidden. If actual negotiated modes cannot run at 120 Hz, this acceptance item remains unmet.

Comfort and motion-to-photon latency require user evaluation and, for numerical photon-latency claims, suitable external measurement. Do not invent an absolute latency guarantee. Larger/ultrawide presets are separately benchmarked and not covered by the baseline promise. The user must confirm comfortable follow and tolerable drift on both pairs before daily-use signoff.

Required tests:
- Unit/replay: quaternion wrap, irregular sensor cadence, dwell cancellation, border jitter, mouse/head arbitration, idle pointer, typing/drag locks, empty desktops, centering latch/rearm, recenter, mode transitions and monitor deletion.
- Integration: independently place windows on three virtual outputs; input/focus works while renderer remains visible; pointer traverses outputs correctly; all layout controls map to native state; persistence, add/remove and rollback verified by readback.
- Hardware per pair: 120 Hz mode verification, real tracking and calibration, yaw/pitch/roll following, Anchor and 0 DoF, actual recenter binding, performance run, USB loss/reconnect and recovery.
- Failure injection: lost capture stream, crashed renderer, dead tracking backend, stale IPC, invalid configuration and shutdown during layout changes. Never leave inaccessible windows or a permanently altered desktop.

No-go behaviors: recurring stutter, focus oscillation, a monitor chasing the head before selection can complete, unrequested typing target changes, claiming 6 DoF or 120 Hz without verification, tracking conflicts, permanent desktop corruption.

## Scope boundaries
Version one excludes eye tracking, hand tracking, camera passthrough, 6 DoF room mapping, multiple users/glasses simultaneously, Windows/macOS, and guaranteed arbitrary monitor counts/resolutions. ARM compatibility is desirable but not an initial release gate. No inference that these are rejected forever.

## Approved visual direction
Arrangement preview plus per-monitor inspector; modes and focus/follow settings below the preview. Add/remove controls, type presets, custom pixel dimensions and per-monitor curvature are included. Compact Omarchy control entry opens the full editor.
Prototype: `.superpowers/brainstorm/881646-1793457927/content/workspace-controls-v2.html`. It is a schematic HTML exploration, not production code. HTML parsing/unique-ID checks and JavaScript syntax checks passed. Browser interaction verification was blocked by localhost tool policy; do not count it as tested native behavior.

## Builder discretion and remaining validation
Builder chooses implementation language, rendering API, toolkit, tracking adapter and test tooling from evidence gathered at the gates. Keep dependencies justified and licensing recorded. UI slider bounds, transient animation details and profile storage schema are engineering choices. Do not change the confirmed behavior to fit a convenient library without asking.

Next step after design approval: write an implementation plan, then implement in measured milestones with real execution evidence. A feasibility failure is an honest blocker, not permission to substitute a mockup for the application.

## Research references
- https://github.com/wheaney/XRLinuxDriver — published device matrix and driver caveats.
- https://github.com/parheliatech/refract — documented GNOME virtual-output implementation.
- https://github.com/mgschwan/viture_virtual_display — Wayland capture experiment and performance caveat.
- https://www.viture.com/developer/glasses-sdk/glasses — vendor SDK reference to validate directly during feasibility.
