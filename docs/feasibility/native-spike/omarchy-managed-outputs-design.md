# Managed outputs as first-class Omarchy monitors: design

Decided by the user on 2026-10-31 ("Join Omarchy"), after live run `phys2` (see `physical-managed-incident.md`).

## Problem

Omarchy's `default/hypr/monitor-workspaces.lua` (package `omarchy-settings` 4.5.0rc2) gives every monitor its own block of ten workspaces:

- **Block assignment:** blocks are keyed by monitor identity (description, or the name when the description is empty) and persisted in `~/.local/state/omarchy/monitor-workspaces`.
- **Reconcile:** runs on `monitor.added`, `monitor.removed` and `config.reloaded`.
- **New monitor:** shown on its block's home workspace.
- **Removed monitor:** workspaces that hold windows are renumbered into the receiving monitor's block and remembered. Empty ones are left to go away.
- **Returning monitor:** remembered workspaces move back home under their old numbers.

The broker pre-planned and pinned one workspace per managed output (`XR-SPIKE-<run>-workspace-<n>`, lowest free id). Omarchy always placed the new output on its own block instead (workspace 71 for `XR-SPIKE-phys2-0`), so strict readback refused every creation. Per-run output names also registered a new permanent block on every run.

## Decision

The broker owns **outputs**, not workspaces. Workspaces on managed outputs belong to Omarchy and the user.

1. **Stable names.** Managed output *n* (0-based index) is named `XR-<n+1>` (`XR-1`, `XR-2`, `XR-3`), with no run id.
   - Omarchy gives each its own permanent block, like a real monitor, and the bar labels it 1–10.
   - Run, journal and UUID/handle identity are unchanged. Names are labels, never authority.
   - The existing collision check blocks creation if any monitor or rule already has that name, for example an orphan left after authority loss.
2. **No workspace planning or pinning.**
   - The `create` wire payload drops `workspace` and `workspace_name`.
   - Native `configure` applies the monitor rule and layout only. It does not create, adopt, rename or switch workspaces.
   - `verified()` checks the output (rule, mode, geometry, enabled, DPMS, no mirror, transform), never which workspace is active. The user may switch workspaces on a managed output during a run.
3. **Readback (Python).** The observation must show the exact geometry and an enabled, DPMS-on, nonmirrored output. Its workspaces must all be regular (`special: false`, id > 0). Windows must be unpinned and on one of those workspaces. Mismatches report bounded observed state.
4. **Teardown is unchanged.**
   - The broker evacuates every workspace on the output to the fallback, refuses special or pinned windows, removes its rule, destroys the output, proves reachability and releases references.
   - After removal, Omarchy renumbers evacuated workspaces that hold windows into the fallback's block and returns them when `XR-<n>` comes back. This is the persistence a real monitor has, not broker state.
5. **Run-independent tokens.** The wire validator requires `name == "XR-" + (index+1)`, and the journal validates the same. Normal and isolated schemas share the payload.

## Consequences and limits

- Windows can come back onto a managed output at creation (Omarchy restore). Creation accepts them if they are on that output's regular workspaces and unpinned.
- The guard still stops a run if a special or pinned window appears on a managed output, and recovery then preserves it. That conservative behaviour is unchanged.
- Only the first three blocks after existing ones are used. Indexes stay limited to 0..2 until more desktops are configurable.
- The VM reproduces Omarchy by loading the package's `monitor-workspaces.lua` and `paths.lua` by package path in the guest config, with no personal config.

## Acceptance (VM before any live retry)

- Three `XR-n` outputs get distinct Omarchy blocks (persisted in the guest's state file), and creation readback passes with Omarchy active.
- Normal teardown, renderer crash and controller crash with recovery all complete with Omarchy active.
- A window left on `XR-1` at teardown lands on the fallback in Omarchy's block. On the next run it returns to `XR-1`, creation accepts it, and the following teardown evacuates it again.
- A second run reuses the same blocks; the state file gains no new entries.
- Output-binding clients stay active throughout; no crash.

## VM acceptance result (`isolated-vm-omarchy-live.json`, module `a7fd2d69…`, commit `0725797`)

The guest ran Omarchy's real `monitor-workspaces.lua` (package copy, SHA256 `31158499…`), with output-binding clients active throughout. All acceptance items passed:

- **Blocks:** XR-1, XR-2 and XR-3 received their own Omarchy blocks (shown 21, 31, 41), recorded in the guest state file, and creation readback accepted them.
- **Window persistence:** a window moved onto XR-1 stayed accepted by the guard. Normal teardown completed; Omarchy renumbered its workspace into the fallback's block (12) and the window stayed alive.
- **Return:** on the next run, Omarchy returned that workspace to XR-1 as 21, creation accepted the returning window, and teardown evacuated it again.
- **Registry:** the second and later runs added no state-file entries.
- **Crash paths:** the renderer SIGKILL run completed through supervisor recovery. The controller SIGKILL run completed through `isolated-recover`.
- **Final state:** Virtual-1 still showed 1. Virtual-2 showed one workspace of its own block (12, the one that had received the window, as with an unplugged Omarchy monitor). Nothing remained on an XR output or outside a block. The compositor stayed alive with no config errors, and host QEMU FD audits were clean.

Two consequences:
- After teardown, the fallback may show the evacuated window's workspace rather than the one it showed before. This is Omarchy's normal unplug behaviour, not residue.
- The first attempt (`isolated-vm-omarchy-first-live.json`) passed every behaviour and failed only my over-strict final check, which expected workspace 11.

## Review of `0725797` (no blocking findings) and follow-ups

The review confirmed that identity stays UUID/run/session/output-object bound. One run cannot address another run's `XR-1`; this is now pinned by a registry test. Changes made:

- **Readback strictness:** reject duplicate or out-of-range workspace ids, require the active workspace to be among the output's workspaces, and require exact window records.
- **Legacy names:** recovery still treats legacy `XR-SPIKE-*` names as unprovable orphans.
- **Tests:** the stale `NativeModel` adopt path was updated, and stale test names were fixed.

Known design limits:
- **Omarchy scratchpad (Super+S)** left on an `XR-n` is a special workspace. The guard stops the run and evacuation refuses it, so teardown stays blocked (fail-closed) until the user closes it. Later, the app should close or relocate special workspaces before teardown.
- **Existing workspaces in a new block:** at creation, Omarchy moves workspaces with windows that already sit in an `XR-n` block onto that output. Blocks are fresh (first free), so this only affects workspaces deliberately placed in those numbers.
- **Multiple returning workspaces** on one output are handled by production `evacuate`, traced by review, but covered only in the VM, not offline.
