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
   - Release fix review (`fa7a3ca`): no blocking findings. Fixed in `51fbeb0`:
     - Release whenever the output is no longer live, including destroyed-but-blocked entries.
     - `Backend::release` is now pure virtual.
     - `NativeModel` really clears its gap and owned references and snapshots, and its repeat-dead check runs against the released state. A mutation that drops the not-live release is caught.
   - Re-verified in the VM (`isolated-vm-wsretain-final-live.json`, module `e031543a…`): identical clean result, with binders active and the compositor alive.
4. Get explicit new user consent. A retry would use module `e031543a…` (`build/native-spike-ws-release-2`) and would report the actual placement mismatch. The physical managed run is **not** qualified.

## Second live attempt `phys2` (fixed module `e031543a…`)

The user approved it and confirmed a clean 1200p120 image and saved work. Evidence: `physical-managed-phys2-live.json`.

- **Result:** creation was again refused by readback, with the new diagnostics: `expected [{"id": 2, "name": "XR-SPIKE-phys2-workspace-0"}] observed [{"id": 71, "name": "71"}, {"id": 2, …}]`.
- **Recovery:** removed the single created output (journal `done`).
- **No crash.** Same compositor instance. Monitors, workspaces (1, 21) and clients were identical to the baseline; no leftover layer, renderer or output.
- **Root cause of the mismatch:** Omarchy's `/usr/share/omarchy/default/hypr/monitor-workspaces.lua` gives every monitor its own block of ten workspaces (reconciled on `monitor.added`/`removed`/`config.reloaded`). It persists block assignments by monitor identity in `~/.local/state/omarchy/monitor-workspaces`. Each per-run headless name (`XR-SPIKE-phys1-0`, `XR-SPIKE-phys2-0`) was registered as a new monitor (blocks 6 and 7), and Omarchy showed workspace 71 on it. The broker's pre-planned workspace id therefore always conflicts on Omarchy.
- **Side effect:** those two block entries now persist in the user's Omarchy state file. Left untouched pending the user's decision.
- **Status:** the managed lifecycle is now safe on the live desktop in this failure path (create, refuse, recover, no crash, no residue in compositor state). Managed operation is still blocked by the design conflict with Omarchy's per-monitor workspaces.

## Third live attempt `phys3` (Omarchy-native module `1d6bbd1c…`, commit `547502f`): PASSED

The user approved it, confirmed a clean 1920x1200@120 image and saved work. Evidence: `physical-managed-phys3-live.json`.

- **Creation:** the managed CLI created `XR-1`, `XR-2` and `XR-3` at 1920x1080@120 (refresh readback), at x = 5632, 7552 and 9472. Omarchy assigned blocks 6–8 and showed workspaces 61, 71 and 81 with full bars. All journal entries were `confirmed`.
- **Renderer:** the test pattern was only on DP-2. The user confirmed it was clear in the glasses and DP-1 was normal.
- **Teardown:** SIGTERM to the exact renderer child (parent: the CLI) gave CLI exit 0, `complete`, renderer return code 0.
- **Afterwards:** the same compositor instance, with no config errors. Workspaces (1, 21), all 5 clients, layers and monitors matched the baseline, except DP-2's session-only 1200p120. Omarchy's merged record was unchanged and no renderer process remained.
- **Kept on purpose:** Omarchy's registry now permanently holds `XR-1`–`XR-3` as blocks 6–8, as designed.

This is the first successful physical managed lifecycle. It is lifecycle evidence only: no capture, tracking, focus or controls, and no sustained-120 FPS, motion-to-photon or comfort claim. Live crash paths: see the next section.

## Live crash paths `phys4` and `phys5` (same module): PASSED

Evidence: `physical-managed-crash-live.json`. Both runs reused Omarchy blocks 6–8 (workspaces 61, 71, 81); no new registry entries were added.

- **`phys4`:** SIGKILL to the exact renderer child mid-run. Supervisor recovery reported `complete` with renderer return code −9 (CLI exit 2, by design).
- **`phys5`:** SIGKILL to the managed CLI itself mid-run.
  - The renderer exited when its supervisor channel closed, and no guard worker remained.
  - `XR-1`–`XR-3` and the 3x `confirmed` journal survived.
  - A fresh `python -m tools.native_spike recover` with the run's consent completed using the saved epoch and handles.
- **After each:** the compositor instance, monitors, workspaces, all clients, layers and Omarchy's state and merged files matched the pre-run snapshot. There were no config errors and no renderer process. The user confirmed the desktop was normal in the glasses and on DP-1 both times.
