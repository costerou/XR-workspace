# Physical managed run `phys1`: compositor crash (2026-10-31)

## What was authorised and done

The user approved a physical managed run with the Luma Ultra (USB `35ca:1104`) on DP-2 as the target and DP-1 (Odyssey G93SD, 5120x1440@240) as the fallback.

1. **Baseline:** recorded in `physical-managed-phys1-baseline.json`. Running Hyprland was `efb50993…` v0.56.2, with the `hyprbars` plugin loaded. There were 7 clients, none pinned or special.
2. **DP-2 mode:** set to 1920x1200@120 runtime-only (`hl.monitor` eval), read back as 119.999 Hz. The user confirmed a clean image.
3. **Plugin load:** loaded `build/native-spike-pair-proof/libxr-ownership.so` (SHA256 `4d4fc81c…a63f`) on the live desktop. The load triggered a config reload that reset DP-2 to 1920x1080@60. I re-applied 1200p120 (119.999 Hz readback).
4. **Managed run:** started `python -m tools.native_spike run` (schema-1 normal mode) with exact P2 consent `phys1`.

## What happened (all within 16:51:52)

| Time | Event |
|---|---|
| — | `XR-SPIKE-phys1-0` was created by the broker |
| — | Its readback failed: `blocked: workspace placement mismatch` |
| ~.33 s | Recovery evacuated and removed it; the journal recorded `done` (`physical-managed-phys1-journal.json`). The CLI exited 2 |
| ~.47 s | Hyprland crashed with SIGSEGV (`physical-managed-phys1-hyprland-crash.txt`): `CWLOutputProtocol::bindManager` → `CWLOutputResource::CWLOutputResource(…, CMonitor)` → weak-pointer decrement. A client was binding a `wl_output` global as the compositor processed it, immediately after the short-lived headless output was removed |

The user's desktop session was lost and restarted. The new session has no plugins loaded and no XR outputs. The stale `phys1` journal has no pending operation.

## Assessment

**Crash:**
- The fault is inside Hyprland's `wl_output` bind path, not broker code. The plugin is listed in the report, and our create-then-immediately-destroy sequence is the plausible trigger.
- The VM never showed it because the guest has no bar, notification daemon or other client that binds new outputs. That is a coverage gap in the VM trials.
- Root cause is unconfirmed and has not been reproduced.

**Placement mismatch:**
- Not established. The live log level does not record workspace or monitor events, and the CLI error does not report the observed workspaces.
- Candidates include Omarchy shell reacting to the new output and the user's `~/.config/hypr/xr-controls.lua` event hooks (`workspace.active`, `monitor.focused`).
- None is proven, and I made no config changes.

## Root cause and fix (VM, 2026-10-31)

**Reproduction** (`isolated-vm-bindrace-crash-live.json`):
- Setup: the pair-proof module `4d4fc81c…`, plus four diagnostic clients in the guest. Two bind every new `wl_output` as it is announced, like a bar. Two repeatedly connect, bind all outputs and disconnect.
- Workload: production create-then-immediately-recover iterations.
- Result: Hyprland segfaulted on iteration 1 with the identical stack and offsets as the physical crash (`bindManager+0x12d` → `CWLOutputResource` constructor `+0x2ad` → `+0x984ff0`).

**Cause:** in pinned Hyprland `src/protocols/core/Output.cpp`, `bindManager` constructs `CWLOutputResource` with `m_monitor.lock()`.
- Normally a removed monitor has expired, so the constructor returns early and the late bind is harmless.
- The broker's `NativeObject` held a **strong** `PHLMONITOR` to every managed output, which kept the destroyed monitor alive.
- The late bind then ran `updateState()` against a torn-down monitor and output.
- The same retention made one iteration's recovery report incomplete.

**Fix** (`754da0e`): hold monitors as `PHLMONITORREF`, with an explicit bound flag so replacement detection is unchanged. Hyprutils weak equality compares control blocks, so identity comparisons after expiry stay correct. A boundary test asserts the broker no longer keeps a removed monitor alive (RED before the fix).

**Verification** (`isolated-vm-bindrace-fixed-live.json`, module `973942be…`):
- Same load: 25/25 iterations completed with the compositor alive.
- Each persistent binder logged 53 output binds, covering all 50 managed outputs as they appeared.
- Host QEMU FD audits were clean.

**Diagnostics** (`c7a283e`): creation mismatches now report the expected and observed workspaces and windows, bounded and ASCII-escaped.

The cause of phys1's placement mismatch is still unknown.

## Required before any further live attempt

1. ~~Reproduce in the VM~~ done. ~~Fix~~ done: weak monitor references.
2. ~~Diagnosable mismatch~~ done.
3. ~~Independent review of the weak-monitor change~~ done: no blocking findings.
   - The reviewer confirmed the root cause against the pinned `Monitor.cpp`: on destroy Hyprland nulls `m_output` and removes the monitor, so only an outside strong reference lets the late bind's `lock()` succeed.
   - The reviewer's should-fix: the boundary test was circular, since it defines its own `NativeObject`. It is now closed by a `static_assert` on the production field (checked: a revert fails to compile). The module binary is unchanged (`973942be…`).
   - Follow-up from the review, now confirmed and fixed. `isolated-vm-wsretain-before-live.json` (module `973942be…`): after 4 complete recoveries and a full CLI run, every evacuated `XR-SPIKE-…-workspace-N` (on the fallback) and every Hyprland auto-created gap workspace (on the other output) stayed listed, empty, for the compositor lifetime. The registry keeps dead entries for readback, and their objects held strong `PHLWORKSPACE` members.
   - Fix `fa7a3ca`: the registry calls `Backend::release` when an entry is proven dead, and the native backend drops its workspace references and snapshots.
   - Verified in `isolated-vm-wsretain-fixed-live.json` (module `7643f411…`), with output-binding clients active and the compositor alive: the workspace list matched the baseline after every recovery and after a CLI run. A workspace holding a migrated window stayed on the fallback with its window and disappeared when the window closed.
4. Get explicit new user consent. A retry with module `7643f411…` (`build/native-spike-ws-release`) would report the actual placement mismatch. The physical managed run is **not** qualified.
