# Placement menu: integration checklist and glasses test script

Automated coverage lives in three places:

- `tests/native_spike/test_placement_menu.py`: Python, contract level.
- `spike/tests/placement_menu_ipc_test.cpp`: the renderer's `decode_control`. It shares its payload cases with the
  Python tests through `tests/native_spike/fixtures/placement_menu_payloads.json`.
- `spike/tests/placement_menu_style_test.cpp`: whether the menu card matches the help card's look and fits the glasses
  at 1920×1200 @1.25.
 Passing tests do not count as a glasses pass. Only the
script at the end does.

## Integration checklist (merging MENU-UI, MENU-WIRING and MENU-TESTS into `feature/placement-menu`)

1. Merge all three branches, then run:
   ```sh
   python -m unittest discover -s tests/native_spike -v
   python -m unittest discover -s tests/xr_controller -v
   python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -v
   cmake --build build/release -j && ctest --test-dir build/release --output-on-failure
   ```
2. Before the merge, `SentinelTests.test_menu_wiring_landed` and `test_menu_renderer_landed` are expected failures.
   After the merge they report **unexpected success** and the suite fails. That is deliberate. Delete both
   `@unittest.expectedFailure` decorators. The tests skipped with "needs MENU-WIRING" (34) or "needs MENU-UI" (2) now run and must pass.
3. `ctest` must show `placement_menu_ipc` and `placement_menu_style` as **Passed**. Skipped or "Not Run (Disabled)"
   means the renderer still lacks `placement_menu` decoding or `render_placement_menu_card`. "Rejected" in the ipc test
   means either an `Error` or a decoded message with an empty payload. MENU-UI ignores a well-framed message with a bad
   payload rather than ending the session.
   - The style test compares the menu card with the help card pixel by pixel: identical corners, border, background
     and padding, and only the help card's colours (text white, text dim, accent). It also checks that the pixel font
     has a glyph for every character the menu prints, that the title, desktop selector, 7 rows and every hint line are
     drawn, and that no text reaches the padding. Fit is checked against the 1536×960 logical-pixel surface that
     1920×1200 @1.25 gives the renderer (cards are drawn 1:1 and never scaled down), including the 18 px fade-in drop.
     It covers every desktop/field selection with extreme values, in the pixel font and in JetBrainsMono Nerd Font
     when fontconfig finds it.
   - Against MENU-UI `f43fef7` it passes. The largest menu card is 965×734 with the installed font and 1036×503 with
     the pixel font.
   - It prints one **warning**: with the Ctrl+Alt+M row added, the help card is 945 px tall with the installed font.
     That fits at rest but runs 3 px off the bottom during its fade-in drop. Decide whether that matters; see the open
     question in the status message.
   - `MenuStyleSourceTests` (Python) checks the two things pixels can't show: the menu overlay loads the same
     `kHudFontFamily` as the help card, and passes the same accent.
4. Bind size. Watch `BindSafetyTests.test_run_start_bind_install_fits_one_eval`. With the live checkout path, a
   token_hex run id and a sha256 session, the 11 current binds already take ~4.0 KB of the 4096-byte `_eval` cap. One
   more global bind (Ctrl+Alt+M) makes it 4.37 KB, and **every run would then fail at start**. MENU-WIRING has to
   shorten the bind commands (for example a short per-run helper path, or fewer repeated arguments), split the eval,
   or install the in-menu keys separately while the menu is open. Raising the cap on its own only hides the problem.
5. Pre-existing, not caused by the menu: `test_reload_bind_reinstall_fits_one_eval` is an expected failure.
   Remove-and-reinstall after a Hyprland config reload is ~4.15 KB today, so the run's hotkeys are lost after an Omarchy
   theme switch. Whatever fixes item 4 should fix this too. Then remove that decorator.
6. Existing tests MENU-WIRING may legitimately need to update:
   - `test_view.ViewStateTests.test_user_ops_decode_and_others_rejected` asserts `xr_control.OPS == view.USER_OPS`. If
     menu actions go into `xr_control.OPS` but not `USER_OPS` (needed to keep `capabilities` and the panel buttons
     unchanged, see `NoScopeChangeTests.test_panel_capabilities_unchanged`), change that assertion to
     `OPS == USER_OPS + MENU actions`.
   - `test_view.ConfigReloadTests.test_reinstall_removes_then_binds_in_one_eval` counts `hl.bind(` against `len(BINDS)`.
7. Keys in three places must agree: wiring's in-menu binds, the menu card's footer hints (`placement_menu_hints()`
   in `hud.cpp`) and the README table. MENU-UI's footer says "Esc close". The README doesn't list Esc yet, and Esc
   isn't one of the contract actions (it would have to be bound to `menu_toggle` while the menu is open).
8. README: `MenuBindTests.test_readme_documents_the_real_toggle_chord` checks that the `### Placement menu` section
   names the toggle chord the code really binds. The README assumes **Ctrl+Alt+M** and the in-menu keys
   Tab/Shift+Tab, Up/Down, Left/Right, Shift+Left/Right and R. If MENU-WIRING picked different keys, fix the README table.
9. Check that the help card (Ctrl+Alt+H) lists the menu toggle and still has 24 rows or fewer.
10. Rebuild the renderer, then record its sha256 before any live run (see the parallel-session note: other sessions
   rebuild in shared worktrees).

## Manual glasses test script (Cody)

This needs your usual go-ahead for a live run. Note the controller worktree HEAD, `git status`, the controller MainPID
and the renderer sha256 before you start, and check them again afterwards.

Setup: install the merged branch the usual way. No XR session should be running while you switch. Start tracking,
open the panel and start a session with all three desktops. Before starting, give XR-2 a visible tilt in the panel
(for example yaw 25), so a reset has something to undo.

| # | Do | Expect |
|---|----|--------|
| 1 | Press Ctrl+Alt+M | The menu appears in the glasses in the help card's style. It shows a desktop (XR-1/2/3), the seven settings with plain-English labels, the current values, and the selected desktop and setting highlighted. |
| 1a | With the menu open, compare it with the help card (Ctrl+Alt+H, then close it and reopen the menu) | Same font (JetBrainsMono, not blocky pixel letters), same dark translucent background, same accent border and corner radius, same white/grey text and the same padding and row spacing. It fades and rises in the same way. |
| 1b | Check the whole menu at your 1920×1200, scale 1.25 session mode, centred in front of you | You can read all of it without clipping at any edge: the "Placement" title, the XR-1/XR-2/XR-3 selector with the current one underlined, all seven rows (Left/right, Down/up, Distance, Yaw, Pitch, Roll, Curvature) with their hints and values, and both footer hint lines. |
| 1c | Set a desktop to the longest values (Left/right −100, Down/up −100, Yaw −60, Pitch −45, Roll −45, Curvature 120) and select each row in turn | The card stays inside the view. Nothing clips or wraps, and the highlight band stays inside the card. Close and reopen with Ctrl+Alt+M and watch the fade-in: no edge clips while it rises. |
| 1d | Open the help card (Ctrl+Alt+H) and watch its fade-in | Its new Ctrl+Alt+M row is listed. Note whether the bottom edge clips at the start of the fade (the automated test predicts a 3 px overrun). |
| 2 | Press Ctrl+Alt+M again, then once more | It hides, then comes back with the same desktop and setting selected. |
| 3 | Press Tab three times, then Shift+Tab once | XR-1 → XR-2 → XR-3 → XR-1, then back to XR-3. Nothing moves in space. |
| 4 | Press Down seven times | The setting cycles Left/right → Down/up → Distance → Yaw → Pitch → Roll → Curvature → Left/right. Up goes backwards. |
| 5 | Choose XR-1, Distance. Press Right a few times, then Shift+Right | The desktop moves farther away with each press, straight away (no Apply). Shift moves it a clearly bigger step. The menu value updates with each press. |
| 6 | Hold Shift+Right until it stops | The value stops at 200 and the desktop stops moving. Nothing jumps or flickers at the limit. Do the same with Shift+Left and check it stops at 50. |
| 7 | Do step 6 for Yaw (±60), Pitch (±45), Roll (±45), Curvature (0 to 120), Left/right and Down/up (±100) | Each stops at its limit. The direction matches the label: yaw turns, pitch tilts up/down, roll tilts sideways. |
| 8 | Choose XR-2 (tilted in setup), press R | XR-2 snaps flat (all defaults). XR-1 and XR-3 do not move. |
| 9 | With the menu closed, type in a terminal using the arrows, Tab and R | They type normally. The menu keys are inactive while it's closed. |
| 10 | Open the menu, change XR-3, wait 2 s **without closing**, then open the panel's **Place** tab | The panel shows XR-3's new values (the saved profile / live status). |
| 11 | Change XR-1 again and close the menu at once. Stop the session, start a new one | XR-1 comes back where you left it (saved on close). |
| 12 | While the menu is open, move a slider in the panel's **Place** tab | The desktop and the value shown in the menu both update. The next arrow press continues from the panel's value. |
| 13 | Press Ctrl+Alt+H with the menu open | The help card still toggles and lists Ctrl+Alt+M. |
| 14 | Press Ctrl+Alt+P | Pose prediction still toggles (its pop-up shows). The menu does not open. |
| 15 | Switch the Omarchy theme during the run | The menu's highlight follows the theme, and Ctrl+Alt+M still works afterwards (bind reinstall, checklist item 5). |
| 16 | Stop the session | Windows return to the physical output as usual. `profiles.json` is still mode 0600 and holds the placements. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.
