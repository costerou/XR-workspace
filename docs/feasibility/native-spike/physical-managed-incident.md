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

## Required before any further live attempt

1. Reproduce the crash in the VM with an output-binding client: a bar-like client that binds every new `wl_output`.
2. Find a removal sequence that does not crash, such as letting clients settle and disabling the output before destroying it. Verify it in the VM.
3. Make creation failures report the observed workspaces and windows (bounded), so a mismatch is diagnosable.
4. Get explicit new user consent. The physical managed run is **not** qualified.
