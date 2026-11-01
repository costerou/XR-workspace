# Live run fol2: a theme switch ended the XR session (2026-10-31)

**What happened.**
- The user switched Omarchy themes during managed run `fol2`, testing the theme-coloured highlight from `feat/follow-theme`.
- `omarchy-theme-set` runs `hyprctl reload`. In Hyprland 0.56, a reload re-runs the Lua config, which:
  - drops the monitor rules the ownership plugin added for XR-1..3, so they fell back to 1080@60 with scale 1.25;
  - resets DP-2 from the session-only 1200@120 to 1080@60.
- The plugin's `verified()` check then failed (`ruleLost`), and the broker marked all three outputs `invalid`.
- The guard stopped the renderer (exit 0), as designed. Recovery then failed closed: `evacuate_remove` returned not-dead for all three outputs (`complete: false`, exit 2). The likely cause is `cleanup` needing the lost rule.

**Effect on the user:** the glasses dropped back to a single 60 Hz mirror. Hyprland did not crash, and no windows were on the XR outputs.

**Recovery (manual).**
1. Removed the three empty XR outputs with Hyprland's own `hyprctl output remove`.
2. Unloaded the plugin.
3. After that, monitors, workspaces, clients, layers and Omarchy state all match the pre-run baseline (`fol2-recovered`).
4. The tracking driver was stopped.
5. The `fol2` journal stays incomplete as a record.

**Follow-ups.**
1. Have the plugin re-apply its monitor rules on Hyprland's config-reloaded hook, so a reload no longer invalidates live outputs.
2. Make broker removal work for outputs whose rule was lost (still requiring evacuation and reachability).
3. Have the supervisor re-apply the session DP-2 mode after a reload.
4. Add a VM regression for a reload during a run, before the next live theme test.

**Confirmed in the same run:** the new always-on spring follow mode feels right to the user. The theme colour change itself could not be observed before the session ended.

## Fix (b5c59d0, branch feat/follow-theme)

**Plugin.**
- Inside `stateReloaded` (the reload's own synchronous rule pass), the plugin restores and re-applies exactly its rule when no rule of that name exists. A same-name user rule still wins and latches invalid.
- Cleanup treats an already-dropped rule as erased.

**Supervisor.** On `configreloaded`, off the watchdog loop, it:
- re-installs the run-only binds, removing old handles first;
- re-applies the granted 1920x1200@120 session mode, if the run started in it.

**VM regression** (`isolated-vm-reload-live.json`), two reloads mid-run:
- **Fixed plugin:** the run survives, XR-1..3 keep 1920x1080@120 at the same positions, the binds stay exactly once, controls work, and teardown is complete.
- **Previous plugin:** fails with "run ended after reload 1", reproducing the incident.

**Not yet covered:** the session-mode restore and the on-screen colour change need the live run.

## Live verification (run thm1, merge 91d7892)

- The user switched Omarchy themes mid-run. The session kept running and the border and glow changed to the new theme colour (`#8d8d8d` → `#e68e0d`).
- DP-2 was back at 1200p120, XR-1..3 stayed at 120 Hz, and the Ctrl+Alt binds were present exactly once.
- Teardown was complete and matches the baseline. Evidence: `physical-theme-thm1-live.json`.
