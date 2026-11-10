> **Status (2026-11-10):** "Left to do" item 1 is done. Both changes landed on master in
> `a03b8ce` ("Remove the panel view shift; options overlay refactor and Pro 2 WIP"); the
> privacy patch reverse-applies cleanly to master. Items 2 to 5 are still open. This is the
> `handoff.md` that sat untracked at the repo root, kept here for its open items.

# Handoff: privacy mode live-tested; a pinned window no longer ends a run

Date: 2026-11-08. Replaces the earlier handoff for privacy mode (Ctrl+Alt+D).
Status: both changes are installed, and Cody confirmed both work in the glasses (run `535c760c` ended with a clean
Stop: no guard errors, no new incidents). **Nothing is committed yet.**

## 1. Privacy mode (Ctrl+Alt+D) during a run: done

**Root cause.** Privacy mode turns DP-1 off with DPMS, but the monitor keeps its workspaces and windows. The plugin's
`usable()` (`spike/ownership/plugin.cpp`) required DPMS on. So while privacy was on, `inspect` reported
`fallback_usable: false`, the guard raised `creation incomplete` and stopped the run, and Add desktop, Ultrawide and
Remove desktop were refused.

**Fix:**
- `usable()` no longer checks DPMS for a normal (schema-1) run. The fallback must still be a DRM output that is
  enabled, not mirrored, and has the same identity. An isolated pair run keeps DPMS in `observePairOutput`.
- `reachable()` lets windows sit on a DPMS-off monitor only if it is the run's fallback.
- Python: `ManagedGuard` → `validate_physical_pair(..., recovery_asleep_ok=True)`, then a plain `verify`. The interim
  `fallback_asleep` change is reverted, and `supervisor.py` has no diff against HEAD. The removal refusal text says
  "disabled, mirrored or unplugged".
- Tests: C++ `legacyFallbackAsleep` in `spike/tests/ownership_pair_boundary_test.cpp`, and 2 guard tests in
  `tests/native_spike/test_privacy.py`.
- Docs: the privacy paragraph in `docs/technical.md`.

**This session:**
- Installed the plugin and Python together (`install --build build/release`, then restarted the controller). The
  plugin hash is `9415b909…`.
- Cody's live test passed: privacy holds the run, and Add desktop, Ultrawide and Remove desktop work with privacy on.
- Run `c74b4a44…` recovery: already finished. The failed Recover at 20:11 predates `hyprland.instance_ended` (added
  20:15). The Recover at 20:22:59 took the `recover_ended` path, all 3 journal steps are `done`, and `result.json`
  says `complete: true`.

## 2. A pinned window on an XR desktop: new fix

**Symptom.** Run `9c54d211`, 20:43:16. The guard raised `OwnershipBlocked: unsupported or unreachable mapped window …
{"pinned": true, "workspace": 21}` and stopped the run. Recover then refused with "chromium is still on XR-1 (pinned:
unpin it first)". It was not a crash, and privacy mode was not involved. That Chromium window was unpinned and the run
recovered through the controller socket (`begin_recover` / `confirm_recover`), which completed.

**Cody's decision:** keep the run going, refuse Remove for that desktop, and unpin at Stop/Recover.

**Change:**
- `tools/native_spike/ownership.py` `verify`: windows may be pinned. `pinned` must still be a bool, and the window
  must still be on one of the desktop's workspaces.
- `tools/native_spike/restore.py`: `UNPIN_LUA` (fixed Lua that unpins every pinned window whose workspace is on an
  `XR-n` monitor) and `unpin(client)`, which is best effort and never raises.
- `tools/native_spike/hyprland.py`: `HyprlandClient.unpin_xr_windows()`.
- `tools/native_spike/managed.py` `recover_restoring`: `restore.unpin(client)` runs before the layout restore and the
  removal. That covers both Stop and Recover.
- Remove: unchanged. The plugin's `evacuate` refuses a pinned window before it moves anything, and the desktop stays
  (`test_desktops.test_a_refused_removal_changes_nothing`).
- `docs/technical.md`: a new paragraph, "A pinned window on an XR desktop", under "When a recovery cannot finish".
- Tests: `test_ownership` (pinned accepted; a non-bool or foreign workspace is still rejected) and `test_restore`
  (`UnpinTests` runs the real Lua in the harness: XR windows unpinned, a fallback window stays pinned; the wiring tests
  check the order unpin → restore → recover, and that a failed unpin still recovers). The harness gained a
  `hl.dsp.window.pin` stub.

**Proof:**
- The new tests failed first, then passed.
- `tests/native_spike` ran 2604 tests; the only error is the `test_stabilization` one that was already there (pytest
  is not installed for `/usr/bin/python3`). `tests/xr_controller` ran 660 tests, OK.
- Live Hyprland check of `UNPIN_LUA` on a throwaway `foot` window: it unpins on a matching monitor and leaves a
  window pinned on DP-1 alone.
- Reinstalled and restarted the controller. The Pro 2 bundle `pro2/829cf2f27af2` has the new Python and the same
  plugin.
- Cody's live run worked.

## Left to do

1. **Commit.** Your working tree also has other uncommitted work, so commit these hunks only, not whole files:
   - Privacy: `/tmp/privacy-fix.patch` (verified to apply to HEAD and pass alone: ctest ownership 10/10, 88 focused
     Python tests). It has all of `plugin.cpp`, the boundary test, the refusal text in `ownership.py`, the 2 guard
     tests in `test_privacy.py`, the `ManagedGuard` hunk of `managed.py`, and the privacy paragraph of
     `docs/technical.md`. `/tmp` may not survive a reboot; regenerate the patch if it's gone.
   - Pinned window: the `verify` hunk in `ownership.py`, all of `restore.py`'s diff, the `unpin_xr_windows` hunk in
     `hyprland.py`, the `restore.unpin(client)` line in `managed.py`, the changes in `test_ownership.py` and
     `test_restore.py`, and the pinned-window paragraph in `docs/technical.md`.
   - How: build each commit in a temporary index (`GIT_INDEX_FILE=… git read-tree HEAD; git apply --cached <patch>`),
     then `commit-tree` / `update-ref`, then `git reset -q -- <those paths>`. That leaves your index and other changes
     alone.
2. **USB drops (19:55:37 and 20:24:13): still not explained.** Each came 23–32 s after the guard stopped a run
   (19:55:14, 20:23:41), and each was near an amdgpu `REG_WAIT timeout … dcn32_program_compbuf_size`. Against either
   being the cause alone: the guard stops at 00:48 and 01:50 had no drop, and run `535c760c` had two `REG_WAIT`
   timeouts (20:54:38, 20:55:24) and no drop. If it happens again, compare `journalctl -k` (usb 1-5, amdgpu) with the
   run's guard and stall lines.
3. **Hyprland segfaults at 19:56:55 and 19:57:00** in `libaquamarine.so.0.15.1` (same offset `bc5e1`), right after
   the second USB disconnect. A coredump is available (`coredumpctl info 1579`). Not investigated.
4. **Misleading text, not changed:** a refused Remove still adds "recovery required for unverified outputs"
   (`desktops.py`), even when the broker refused before touching anything.
5. **Stale journals** in `/run/user/1000/xr-workspace-spike/` (`2b73a7b9`, `3d458f70`, `44cebef2`, `cf4f0cb8`): not
   looked at.

## Notes

- Tests: use `/usr/bin/python3` with `TMPDIR=/tmp`, and run from `tests/native_spike`.
- Pro 2 runs use the installed bundle's copy of `tools/`, so re-run `install` (with no run active) after Python
  changes, then restart the controller.
- `hyprctl` takes Lua here: `hyprctl eval "hl.dispatch(hl.dsp.window.pin({action='disable', window='address:…'}))"`.
