# VITURE Workspace — handoff to Claude

## Start here
You are taking over Cody's local Omarchy XR workspace project from Hermes. Work on the existing project, not a replacement scaffold. The goal is a working application; preserve the safety boundaries and actual evidence. Do not confuse tests, virtual-machine trials, and physical-glasses acceptance. The last implementation agent was stopped for this handoff. Its native pair-proof changes are **uncommitted and not accepted**. Inspect them before continuing; do not blindly discard or commit them.

## User's product requirements
- Local Omarchy application, preferably with a small Hyprland plugin where needed. No GitHub account/remote required. Project belongs in `/home/cody/Projects`.
- VITURE Beast and VITURE Luma Ultra support.
- Independent virtual desktops, not mirrored screens. Arbitrary add/remove; three 1080p desktops is an initial benchmark, not a limit.
- Per-display position, rotation, physical size, resolution, scale and curvature; standard, ultrawide, super-ultrawide, portrait and custom presets.
- Extremely responsive smooth follow across yaw, pitch and roll, centering the selected display without breaking relative layout.
- Head-direction focus (not eye tracking), transferring keyboard focus; active mouse movement can override, stationary pointer cannot steal focus.
- Typing/drag protection, including inputs already held at startup.
- Head tracking / 3 DoF, Anchor mode with recenter binding, head-locked 0 DoF.
- 120 Hz support, with actual measured rendering performance and real-device comfort acceptance. Exact mode readback is NOT sustained 120 FPS.
- Prompt before connecting/disconnecting glasses. Give short grounded progress updates, not repeated planning narration.

## Repository and current state
- Main repo: `/home/cody/Projects/viture-workspace`
- Main branch `master`: `e946d8a` (completed feasibility foundation).
- Active worktree: `/home/cody/Projects/viture-workspace/.worktrees/native-spike`
- Active branch: `feat/native-spike`
- Current committed HEAD: `c78635139ad4fc4eeb49f5e8e053675e4a18f6da` — isolated-mode scope/proof-boundary docs.
- Native branch is NOT merged to master.
- Verified current dirty tracked files:
  - `spike/CMakeLists.txt`
  - `spike/ownership/plugin.cpp`
  - `spike/ownership/registry.cpp`
  - `spike/ownership/registry.hpp`
- Untracked in-progress tests:
  - `spike/tests/extract_pair_methods.py`
  - `spike/tests/ownership_pair_boundary_test.cpp`
  - `spike/tests/ownership_pair_test.cpp`
  - `spike/tests/ownership_pair_wire_test.py`
- These are interrupted **native pair-proof implementation**, not reviewed deliverables. Latest status showed 158 insertions / 8 deletions in tracked files. No final build/test/ABI acceptance is claimed for this dirty tree.
- Separate untracked physical-test evidence must be preserved:
  - `docs/feasibility/native-spike/beast-presentation-live.md`
  - `luma-presentation-live.md`
  - `luma-timing-live.json`
  - `luma-animation-live.json`
  - `luma-animation-release-live.json`
- Last implementation transcript: `/home/cody/.hermes/cache/delegation/live/deleg_b574eb77/task-0.log`. Agent interrupted after partial work; no final report was delivered.

## Architecture
Native C++ renderer plus Python CLI/supervisor/recovery; a small compositor-resident ownership broker establishes exact output incarnation ownership. Do not move the renderer into the compositor. Raw external IPC could not safely prove ownership for deletion, so managed mutation uses opaque object-bound handles and epochs, never a name-only fallback.

Implemented components:
- `tools/xr_probe/`: bounded read-only diagnostics, session/USB/DRM/mode reporting.
- `spike/`: C++20 renderer, layer-shell SHM presentation, exact mode/layout contracts, presentation-time feedback and finite generated animation.
- `tools/native_spike/`: consent, selected IPC/Wayland peer checks, durable journal, supervisor, broker client, managed creation and explicit restart recovery.
- `spike/ownership/`: real Hyprland ownership module/registry/lifecycle code.
- `tests/probe/`, `tests/native_spike/`, `spike/tests/`.

Key invariants:
- Intent is durably written before mutation; actual state is inspected before confirmation.
- Resource UUID, generated display name and object-incarnation handle are distinct.
- Recovery restores saved broker epoch/fallback authority; it must not mint a new hello to adopt resources.
- Preserve ambiguous/user-modified resources and report incomplete recovery instead of deleting them.
- Lifetime lock covers the canonical managed run, including teardown. Recovery through copied/relocated journals is refused before broker access.
- Normal presentation requires separate enabled, DPMS-on, nonmirrored target and recovery outputs.
- Selected IPC and Wayland socket peers must agree; no ambient session fallback.
- Potentially blocking checks run in a spawned guard worker, not the watchdog heartbeat loop.
- Renderer uses keyboard NONE, empty input region, no capture/input access in generated-pattern tests.

## What has actually passed
### Offline committed baseline (before interrupted pair-proof edits)
105 native Python tests, 62 probe tests and 11 CTests; Release native build including ownership module. Managed integration and canonical lock correction independently reviewed.

Important commits:
- `2827dc2857cc8530db8264187982eaef38e76a17`: fresh default workspace adoption, replacement gap retirement, full rule equality including VRR/EOTF.
- `dedab60292fe3fee9468356ae152607ee063cd9b`: call synchronous monitor-layout arrangement after applying monitor rule; exact geometry verification retained.
- `791c85aec0b27bb35f9de897ac20308f4d46b1b5`: broker-backed managed CLI/supervision/restart recovery.
- `a9f27a5a055a675d8375c0a82d76d8f5f1535dd2`: canonical recovery lock correction, independent review passed.
- `c78635139ad4fc4eeb49f5e8e053675e4a18f6da`: records isolated-mode architecture proof gap; no mode implementation in that commit.

### Physical glasses (presentation-only, no ownership plugin loaded)
- Beast generated pattern, normal exit and controlled renderer crash restored the desktop, user confirmed.
- Luma initially distorted at unadvertised 1920x1080@120. Authorized runtime-only 1080p60 corrected it; advertised 1920x1200@120 (reported119.999 Hz) also clean. Corrected-mode normal/crash tests passed user visual confirmation. No saved monitor config edit.
- Luma static presentation feedback worked.
- Initial unoptimized 10-second animation:923 presented frames, observed ~92.4287 Hz, no discarded feedback. Release repeat evidence exists in `luma-animation-release-live.json`; read the actual file before quoting its result.
- No sustained full capture/GPU/head-tracking workload or final performance/comfort acceptance.

### Isolated guest runtime
Disposable x86 KVM, matching Hyprland, non-GL virtio GPU, software llvmpipe, no host graphics passthrough. Actual guest presentation feedback verified, not just responsive IPC.
- Reviewed old native module loaded and broker handshake succeeded.
- Three 1920x1080 outputs at exactly120000mHz, correct positions1280/3200/5120, deferred stability, handle-addressed deletion and repeated recovery passed.
- Three actual native Wayland windows migrated to DRM fallback with original identities preserved; all clients exited0, surfaces/windows disappeared, fallback geometry unchanged.
- Scratch controller SIGKILL/restart: PID209 killed; fresh PID220 reopened real saved journal, same epoch/handles, production recovery APIs evacuated all windows, journal done x3, repeated recovery and cleanup passed.
- These are broker/test-controller qualifications, NOT full production supervisor/renderer end-to-end acceptance.
- Owned VM processes verified stopped after trials. Do not infer current global process state without checking; some root-owned process entries were inaccessible.

## Immediate task / blocker
User approved an explicit **isolated VM test mode**, with separate consent and verified VM identity, leaving normal production safeguards unchanged. Goal: execute the full managed launcher/supervisor in a guest.

Current normal guard accepts physical connector patterns DP/HDMI-A/eDP/DVI-D; virtio exposes Virtual-1/Virtual-2. Do NOT merely relax this regex globally, rename a virtual output to pretend it is physical, fabricate EDID, or run the plugin on the live desktop instead.

Proof gap discovered:
1. Broker schema1 only retains fallback; no selected target/fallback pair DRM identity.
2. Python sysfs matching by name does not bind a compositor output object to a kernel connector.
3. Existing internal `physical=true` conflates DRM fallback eligibility with physical classification.
4. Explicit isolated mode needs durable mode/boot/runtime/pair proof through consent, launch, spawned guard and recovery, with cross-mode rejection.

Approved implementation direction (in progress when stopped):
- Add a versioned native pair-proof contract retaining exact target/fallback objects.
- Use public Aquamarine `CDRMOutput::getConnectorID()` and backend `drmFD()` plus `fstat` major/minor to bind object to DRM device/connector; do not open host devices for this work.
- Separate virtual DRM classification from physical; preserve normal schema1 behavior and physical guards.
- Fail closed on replacement, disconnect, mirror/DPMS changes and same-name substitution.
- Combine native proof with guest boot/virtualization/virtio sysfs evidence in later Python isolated-mode integration.
- Guest-local proof cannot attest host QEMU isolation arguments or defeat malicious root/hypervisor. Independently validate launcher envelope.
- Finish native contract and review it BEFORE adding all CLI changes or loading new module. The existing reviewed module does not include new pair-proof contract.

Then:
1. Implement explicit CLI mode and separate run consent bound to boot/session/runtime/target/fallback, with tests for cross-mode misuse and false/missing VM evidence.
2. Independent review.
3. Qualify two actual nonGLvirtio scanouts; `max_outputs=2` alone is insufficient with `-display none`, per-output xres/yres must also be enabled. Read topology report.
4. Verify each output's actual rendering and native DRM association.
5. Run full managed production CLI/supervisor through explicit isolated mode, normal teardown and bounded crash/restart cases. No physical performance inference.

## Permissions and safety boundaries
Authorized:
- Local project/worktree development and necessary packages/dependencies.
- Ownership-broker architecture and offline implementation.
- Disposable isolated VM/plugin qualification, including the explicitly approved VM test mode.
- Previously specified physical presentation-only trials and Luma temporary mode changes, as recorded in evidence; not blanket future live mutations.

NOT authorized by general 'continue':
- Loading ownership plugin into current desktop.
- Host VT takeover, GPU/DRM/render-node passthrough, host VKMS/module experimentation, substituting live compositor if VM fails.
- General desktop capture, input access, SDK license acceptance/execution, tracking integration requiring those permissions.
- Reboot/factory reset/config erasure. User's old 'reset' comment meant chat context/usage, not machine reset.
- Publication/GitHub upload; project is local.

Explicit forbidden physical compositor instance for VM fixtures:
`efb50993780079460b0cbed1363e2166a2de1d9f_1793311125_1142154332`
Still verify live identity; this historical deny value is not sufficient protection by itself.

VM envelope: nonGL virtio full-system KVM, disconnected network, no shared folders, no host graphics/input/VT/session descriptors, private regular disk images, finite process bounds, host display/session environment unset. Guest commands must only execute in verified guest. Preserve baseline evidence; create fresh scratch candidates rather than overwriting prior images.

## Read these files first (relative to active worktree)
- `docs/superpowers/specs/2026-10-31-viture-workspace-design.md`
- `docs/superpowers/plans/2026-10-31-viture-native-spike.md`
- `docs/feasibility/native-spike/managed-integration-report.md`
- `docs/feasibility/native-spike/runbook.md` (some historical portions may be stale; compare code/reports)
- `.superpowers/sdd/2026-10-31-viture-native-spike/isolated-mode-scope-amendment.md`
- `.superpowers/sdd/2026-10-31-viture-native-spike/isolated-mode-implementation-report.md`
- Same SDD directory: `managed-guest-topology.md`, `managed-lock-fix-report.md`, `ownership-broker-report.md`, `ownership-offset-fix-report.md`, `ownership-controller-crash-retest.md`, `ownership-window-migration-final-report.md`, `ownership-isolated-rerun-report.md`, `managed-output-next.md`.
- Treat older plans as historical when later reviewed contracts supersede them.

## Artifacts / commands
Scratch root `/home/cody/.hermes/cache/scratch` (may be pruned; preserve needed evidence promptly):
- `xr-guest-build/`: exact package-owned rootfs/QEMU setup.
- `xr-renderer-retest/`, `xr-presentation/`: working plugin-free guest presentation.
- `xr-ownership-rerun/`: successful empty-workspace lifecycle.
- `xr-window-migration-final/`: successful populated migration and complete cleanup.
- `xr-controller-crash-retest/`: successful controller crash/restart.
Each has setup/run/verify scripts, images, logs, hashes and manifests. Read scripts before running; preparation scripts often refuse overwrite and are not replay-safe against existing evidence directories.

Old reviewed runtime module:
`build/native-spike-offset-fix/libxr-ownership.so`
SHA256 `2be43536ebd358f94851ec927857a7305bbef20a880427fcfef4b19afc168e31`
Do not confuse this with default build module or a future dirty-tree pair-proof build. Runtime module selection must be explicit.

From active worktree:
```sh
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/probe -v
cmake -S spike -B build/claude-verification -DCMAKE_BUILD_TYPE=Release \
  -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/claude-verification -j2
ctest --test-dir build/claude-verification --output-on-failure
```
Bare unittest discovery previously found zero tests; use explicit suites. Build/link success is not runtime ABI/loading acceptance. Do not overwrite preserved reviewed builds merely to verify interrupted code.

Historical pinned environment: Omarchy4.5.0rc2, Hyprland0.56.2-4 commit efb509..., Aquamarine0.15.1, kernel7.2.8-9, Mesa26.2.5; verify installed current versions before relying on ABI. Guest rootfs is copied from package-owned runtime closure, not personal home/config/secrets.

## Proven pitfalls
- Do not set LIBGL_ALWAYS_SOFTWARE=1 in this guest: it hides DRM EGL identity. Omission lets Mesa choose kms_swrast/llvmpipe. Two startup EGL warnings preceded successful fallback; repeated renderer-state/device-match failures are a stop condition.
- applyMonitorRule stores offset; layout arrangement is separate and compositor-wide. Verify fallback/all-output geometry, not only new managed outputs.
- Wayland inherited socket credentials identify opener, not exec child. Test client now opens/validates its own guest socket then execs same PID.
- This Hyprland uses Lua dispatch: `hl.dsp.window.move({workspace=N,follow=false,window="address:..."})`; legacy dispatch syntax rejected. Check pinned API before guessing commands.
- SIGUSR1 can interrupt poll with EINTR; scratch client fixed balanced Wayland prepare/read/cancel handling without hiding other errors.
- Guest root mount source may be /dev/root, not /dev/vda. Verify major/minor, block node, sysfs virtio driver and kernel/QEMU mapping, not string aliases.
- Whole-VM poweroff does not prove graceful client/plugin cleanup. Capture window/surface/process removal before teardown.
- Full managed app, compositor/plugin unload/reload, lost replies/partial creation, authority loss, same-name replacement, fallback loss, special/pinned windows and compositor restart remain distinct acceptance gates.

## Suggested first actions
1. Read handoff + scope/proof reports and inspect git status/diff, including all untracked pair tests.
2. Inspect interrupted transcript for actual last commands, then run independent offline tests/build in a new directory. Do not infer passing status from the stopped agent.
3. Finish/review the minimal native proof contract, then CLI isolated mode as described above.
4. Continue toward a usable application; avoid treating repeated scratch tests as a substitute for missing capture/tracking/focus/controls implementation. Seek specific permissions when those stages need them.
