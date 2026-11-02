# Placement menu: integration checklist and glasses test script

Automated coverage lives in three places:

- `tests/native_spike/test_placement_menu.py`: Python, contract level, plus checks that no permission scope widens.
- `spike/tests/placement_menu_ipc_test.cpp`: the renderer's `decode_control`. It shares its payload cases with the
  Python tests through `tests/native_spike/fixtures/placement_menu_payloads.json`.
- `spike/tests/placement_menu_style_test.cpp`: whether the menu card matches the help card's look and fits the glasses
  at 1920×1200 @1.25.

Passing tests do not count as a glasses pass. Only the script at the end does.

## Trial merge (2026-11-01)

I computed a three-way merge of `feature/placement-menu-ui` (`a034eb7`), `feature/placement-menu-wiring` (`b74eda7`)
and this branch in memory with `git merge-tree`, exported it to `/tmp`, and ran everything on it. No branch, ref or
worktree was changed.

- The merge has no conflicts. An earlier README conflict was fixed by leaving the keybind tables to MENU-WIRING.
- ctest: 12/12 pass, including `placement_menu_ipc` and `placement_menu_style`.
- Python `tests/native_spike`: 327 run, no failures or errors. The only non-passing results are 3 deliberate unexpected
  successes (item 2 below). `tests/xr_controller`: 73 OK. Plugin tests: OK.
- Largest menu card with the renderer's own fitting: 969×733 with JetBrainsMono, 1040×502 with the pixel font, within
  the 1536×960 surface.

## Integration checklist (merging MENU-UI, MENU-WIRING and MENU-TESTS into `feature/placement-menu`)

1. Merge all three branches, then run:
   ```sh
   python -m unittest discover -s tests/native_spike -v
   python -m unittest discover -s tests/xr_controller -v
   python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -v
   cmake --build build/release -j && ctest --test-dir build/release --output-on-failure
   ```
2. After the merge, three expected failures turn into **unexpected successes** and fail the suite on purpose. Delete
   their `@unittest.expectedFailure` decorators in `test_placement_menu.py`:
   - `SentinelTests.test_menu_wiring_landed` and `test_menu_renderer_landed`: their job is to tell you the
     previously skipped tests now run.
   - `BindSafetyTests.test_reload_bind_reinstall_fits_one_eval`. MENU-WIRING's shared-prefix Lua (`local c = "…"`)
     fixed the existing bug where the reinstall after a config reload went over the 4096-byte eval cap.

   After that the suite must be fully OK, with every "needs MENU-WIRING" / "needs MENU-UI" test running.

   Done on `feature/placement-menu-integrate` (2026-11-01, merging wiring `120b719`, UI `7df6c33`, tests `0bf1e4e`):
   the three decorators are gone, and `test_menu_card_uses_the_help_card_accent` now requires both cards to pass the
   shared `kAccent` constant. The style test needed one fix: UI `460fa86` renamed the hint-segment helper to
   `placement_menu_hint_segments()`.
3. `ctest` must show `placement_menu_ipc` and `placement_menu_style` as **Passed**. Skipped or "Not Run (Disabled)"
   means the build is missing MENU-UI. "Rejected" in the ipc test means either an `Error` or a decoded message with an
   empty payload: MENU-UI ignores a well-framed message with a bad payload rather than ending the session.
4. What the style test checks. It compares the menu card with the help card pixel by pixel:
   - identical corners, border, background and padding;
   - only the help card's colours (text white, text dim, accent);
   - a pixel-font glyph for every character, in both hint wordings;
   - the title, the desktop selector, all 7 rows and every footer line are drawn;
   - no text reaches the padding.

   Fit is checked the way the renderer lays the card out (`set_viewport(1536, 960)`), including the 18 px fade-in
   drop, for every desktop and field selection at extreme values, in the pixel font and in JetBrainsMono when
   fontconfig finds it. `MenuStyleSourceTests` checks what pixels can't: the menu loads the same `kHudFontFamily` as
   the help card, and both cards get the same accent.
5. Known warning, accepted: the style test prints that the 12-row help card (now including Ctrl+Alt+L) is 945 px tall
   with JetBrainsMono. It fits at rest but starts its fade-in 18 px low, so its bottom 3 px are off-screen for the first
   frames of the fade, at low opacity. This is a warning, not a failure, because it isn't the menu and isn't visible at
   rest. Suggested fix for MENU-UI: limit the drop to the free margin (`min(kHelpRisePx, (height - card) / 2)`).
6. The keys must agree in three places: wiring's `MENU_BINDS`, the menu card's footer (`kMenuHints` in `hud.cpp`) and
   the README. `MenuHintAgreementTests` checks this, and `MenuBindTests.test_readme_documents_the_real_toggle_chord`
   checks the README names the real toggle chord (Ctrl+Alt+L).
7. Check that the help card (Ctrl+Alt+H) lists Ctrl+Alt+L and still has 24 rows or fewer (tested).
8. Rebuild the renderer, then record its sha256 before any live run. Other sessions rebuild in shared worktrees.

## Manual glasses test script (Cody)

This needs your usual go-ahead for a live run. Note the controller worktree HEAD, `git status`, the controller MainPID
and the renderer sha256 before you start, and check them again afterwards.

Setup: install the merged branch the usual way. No XR session should be running while you switch. Use your
1920×1200, scale 1.25 session mode. Start tracking, open the panel and start a session with all three desktops.
Before starting, give XR-2 a visible tilt in the panel (for example yaw 25), so a reset has something to undo.

| # | Do | Expect |
|---|----|--------|
| 1 | Look at XR-1 and press Ctrl+Alt+L | The menu fades in, centred, on XR-1. It shows the "Placement" title, the XR-1/XR-2/XR-3 selector with XR-1 underlined, the seven settings with plain-English hints and current values, one setting highlighted, and the key hints at the bottom. |
| 2 | **Style.** Close the menu (Escape), press Ctrl+Alt+H, look at the help card, close it, reopen the menu | Both cards share the font (JetBrainsMono, not blocky pixel letters), the dark translucent background, the blue accent border, corner radius and divider, white and grey text, padding and row spacing. They fade and rise the same way. |
| 3 | **Fit.** With the menu open and centred, look at every edge | Nothing clips at any edge: title, selector, all seven rows (Left/right, Down/up, Distance, Yaw, Pitch, Roll, Curvature) with hints and values, and every footer line. There's clear space between the card and the edge of the view. |
| 4 | **Fit, longest values.** On one desktop set Left/right −100, Down/up −100, Yaw −60, Pitch −45, Roll −45, Curvature 120, then select each row in turn | The card stays inside the view. Nothing clips or wraps, and the highlight stays inside the card. Close and reopen: no edge clips during the fade-in. |
| 5 | **Help card fade.** Close the menu, press Ctrl+Alt+H and watch the fade-in | Ctrl+Alt+L is listed. Note whether the bottom edge clips for a moment at the start of the fade (checklist item 5 predicts 3 px). |
| 6 | Press Ctrl+Alt+L, then Escape, then Ctrl+Alt+L | It opens, closes, and opens again. The highlighted setting is kept. |
| 7 | Press Tab three times, then Shift+Tab once | XR-1 → XR-2 → XR-3 → XR-1, then back to XR-3. Nothing moves in space. |
| 8 | Press Down seven times, then Up once | The setting cycles Left/right → Down/up → Distance → Yaw → Pitch → Roll → Curvature → Left/right, then goes back to Curvature. |
| 9 | Choose XR-1, Distance. Press Right a few times, then Shift+Right | The desktop moves farther away straight away with each press (no Apply). Shift takes a clearly bigger step. The value in the menu updates each time. |
| 10 | Hold Shift+Right until it stops, then Shift+Left until it stops | Stops at 200, then at 50. Nothing jumps or flickers at a limit. |
| 11 | Repeat step 10 for Yaw (±60), Pitch (±45), Roll (±45), Curvature (0 to 120), Left/right and Down/up (±100) | Each stops at its limit. The directions match the labels: yaw turns, pitch tilts up and down, roll tilts sideways. |
| 12 | Choose XR-2 (tilted in setup), press R | XR-2 snaps flat (all defaults). XR-1 and XR-3 do not move. |
| 13 | Close the menu, look at XR-3 (Ctrl+Alt+Right), press Ctrl+Alt+L | The menu opens on XR-3. |
| 14 | With the menu closed, type in a terminal using the arrows, Tab, R and Escape | They type normally. The menu keys are bound only while it's open. |
| 15 | Open the menu, change XR-3, wait 2 s **without closing**, then open the panel's **Place** tab | The panel shows XR-3's new values. |
| 16 | Change XR-1 again and close the menu at once. Stop the session, start a new one | XR-1 comes back where you left it (saved on close). |
| 17 | With the menu open, move a slider in the panel's **Place** tab | The desktop and the menu's value both update. The next arrow press continues from the panel's value. |
| 18 | With the menu open, press Ctrl+Alt+H | The menu stays on top: the help card does not show while the menu is open (by design, the menu replaces it). Close the menu: the help card appears if it was toggled on. |
| 19 | Press Ctrl+Alt+P | Pose prediction still toggles (its pop-up shows). The menu does not open. |
| 20 | Switch the Omarchy theme during the run, then press Ctrl+Alt+L and an arrow | The menu still opens and adjusts (the binds were reinstalled). The cards keep their own blue accent; only the desktop highlight follows the theme. |
| 21 | Stop the session | Windows return to the physical output as usual. `profiles.json` is still mode 0600 and holds the placements. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

## Overview corner resize: automated coverage and glasses test script

Automated coverage (offline only):

- `tests/native_spike/test_resize.py`: corner hit test, drag math and clamping, the modal left-button binds and their
  removal (including after a dead run), the hover and drag state machine, the 30 s idle hide, the profile save, panel
  sync through the status reply, the pointer poller thread, and the supervisor wake-up.
- `spike/tests/resize_handle_test.cpp`: the renderer's `resize_handle` decoding (a malformed payload is dropped, never
  fatal), the handle geometry in each corner (fixed, no orbit), and its grey levels at or under 191/255.

The steps below need your usual go-ahead for a live run, the same setup as above (any desktop shapes), and the
renderer built from this branch. Note the controller worktree HEAD, the controller MainPID and the renderer sha256
before and after. Start in the overview (Ctrl+Alt+Down) with every desktop at Distance 100.

| # | Do | Expect |
|---|----|--------|
| R1 | Move the pointer around the middle of XR-1 and across its edges | No handle. Clicks in windows work as usual. |
| R2 | Move the pointer into XR-1's bottom-right corner (the last few percent of its width and height) | A small grey L-bracket appears just inside that corner. It is dim, never white. |
| R3 | Visit the other three corners of XR-1, then a corner of XR-2 and XR-3 | The bracket follows to the corner the pointer is in, on the right desktop. It disappears between corners. |
| R4 | Rest the pointer in a corner without moving for 30 s | The bracket fades off by itself (OLED care). A left click there now reaches the window again. Moving the pointer brings the bracket back. |
| R5 | Watch the bracket for 10 s while moving the pointer slightly inside the corner | It stays put on the same pixels (no drift or orbit) and stays dim. |
| R6 | In XR-1's bottom-right corner, hold the left button and drag right and down | XR-1 grows smoothly while you drag, about its own centre, keeping its shape. The bracket is a little brighter while you drag. Nothing in the window under the corner gets the click. |
| R7 | Keep dragging far outward, then far inward | It stops growing at twice its size and stops shrinking at half. Nothing jumps or flickers at a limit. |
| R8 | Release, then open the panel's **Place** tab for XR-1 | Distance shows the new value (bigger = lower Distance, for example 67 % for 1.5× size). The other fields are unchanged. |
| R9 | Drag XR-3 from its top-left corner inward | XR-3 shrinks. XR-1 and XR-2 do not change. |
| R10 | Open the placement menu (Ctrl+Alt+L) on XR-1, move the pointer into XR-1's corner, then close the menu | While the menu is open there is no bracket and a corner click reaches the window (resize is off while the menu is open; its mouse mode owns the left button, and Distance is in the menu). After the menu closes, the bracket comes back at the corner. |
| R11 | Zoom in (Ctrl+Alt+Up) with the pointer on a corner | No bracket while zoomed or zooming. Left clicks at the corner go to the window. |
| R12 | Zoom back out, resize XR-2, stop the session, start a new one | XR-2 comes back at the size you left it (saved per glasses model, like the other placements). |
| R13 | Resize a desktop, then switch the Omarchy theme and resize again | Resizing still works after the reload (the bind is reinstalled while the pointer is on a corner). |
| R14 | Check the desktops' resolution and scale in the panel and with `hyprctl monitors` | Unchanged: resizing never changes pixels or scale. Those still change only in the desktop editor, at the next start. |
| R15 | Stop the session with the pointer resting in a corner, then left-click on DP-1 | The click works normally (the run removed its corner binds). |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

## Sliders and mouse mode

Each setting in the menu is drawn as a slider. With the menu open, **Ctrl+Alt+M** turns on mouse mode: the mouse moves a
ring pointer over the sliders in the glasses, a click jumps a slider, and a drag moves it live. Ctrl+Alt+M again or
Escape leaves mouse mode.

Automated coverage:

- `tests/native_spike/test_placement_mouse.py`: the pointer state machine (on/off, Escape leaves mouse mode first,
  Ctrl+Alt+L and the idle close end it), pointer motion (per mille along a slider, one row per `MOUSE_ROW_PX`, clamps,
  warp-sized jumps only re-baseline), click-to-jump at both bounds of every field, live drag and release, the desktop
  selector, debounced save, activity for the 30 s idle close (motion and clicks count, a still cursor does not), bind
  order (menu keys, then mouse; mouse off first), the cursor sampler, cursor follow paused, the reload rate limit, the
  supervisor's faster tick, the run wiring, the generated Lua (guarded, release bind, cursor hidden and restored on
  every removal path, the 4096-byte cap) and the bind clash checker, including real `Hyprland --verify-config` runs on
  throwaway configs when Hyprland is installed.
- `spike/tests/placement_menu_ipc_test.cpp`: the optional `pointer` key, from the shared fixture's `pointer_valid` /
  `pointer_invalid` cases (only on an open menu; row -1..6; x 0..1000; drag a bool).
- `spike/tests/hud_test.cpp`: a knob at a field's low or high bound sits centred in the pointer ring at per mille 0 or
  1000, for every field and both fonts; mouse mode never resizes the card; every pointer state stays at or below
  `kCardMaxLevel` and (pixel font) uses only the help card's colours; the slider width stays within
  `kSliderMinW..kSliderMaxW` and gives up width before anything else on smaller outputs.
- `spike/tests/placement_menu_style_test.cpp`: the card with sliders still matches the help card's look and fits
  1920×1200 @1.25 (1229×733 with JetBrainsMono). It now uses the help card's real menu row, "Placement menu
  (Ctrl+Alt+M: mouse)": the help card is 1220×945.

Bind check, run on this machine on 2026-11-01 (Hyprland 0.56.2, the live `~/.config/hypr` copied into a temporary HOME):

```
$ python -m tools.native_spike.bind_check
Hyprland --verify-config: config ok (0)
desktop config: 250 chords bound
no clash: 12 run binds, menu binds (Ctrl+Alt+M, Down, Esc, Left, R, Right, Shift+Left, Shift+Right, Shift+Tab, Tab, Up) and the mouse-mode left button
```

Not checked offline: whether Hyprland 0.56.2 applies `cursor.invisible` and `hl.dsp.cursor.move` from a runtime
`/eval` the way it does at config load, how the hidden cursor interacts with Omarchy's startup-cursor handling, and
how the pointer feels at `MOUSE_TRACK_PX` / `MOUSE_ROW_PX`. The glasses steps below cover them.

Glasses steps (continue after step 21, with a new run started the usual way):

| # | Do | Expect |
|---|----|--------|
| 22 | Press Ctrl+Alt+L | Every setting shows a slider between its label and value. The knob sits at the value; the stretch from flat to the knob is tinted blue. Nothing is brighter than the text. |
| 23 | Press Ctrl+Alt+M | A ring appears around the selected setting's knob. The desktop cursor disappears from every XR desktop. |
| 24 | Move the mouse slowly sideways, then up and down | The ring slides along the slider (about 600 px of mouse travel end to end) and steps one row per ~48 px up or down, up to the XR-1/2/3 tabs. Nothing moves in space yet. Cursor follow does not switch desktops. |
| 25 | On Yaw, click near the left end of the slider | The desktop turns at once; the value reads about −60°. The ring fills while the button is held. |
| 26 | Hold the button and drag right, then release | The desktop turns continuously with the drag; the value updates. After release, moving the mouse moves only the ring. |
| 27 | Move up to the tabs and click XR-3 | XR-3 is selected; the sliders show its values. |
| 28 | Press Right, Shift+Right, Tab, R | The keyboard still adjusts, switches desktop and resets while mouse mode is on. |
| 29 | Leave the mouse still for 30 s | The menu fades out; the cursor is visible again where it was before step 23. Left clicks reach windows again. |
| 30 | Reopen the menu, Ctrl+Alt+M, move the mouse a little every 20 s for a minute | The menu stays open. |
| 31 | Press Escape | Mouse mode ends: the ring goes, the cursor is back where it was, the menu stays open. Escape again closes the menu. |
| 32 | Ctrl+Alt+M, then Ctrl+Alt+M again | Same as step 31's first Escape. |
| 33 | Ctrl+Alt+M, then Ctrl+Alt+L | The menu closes and the cursor comes back. |
| 34 | Ctrl+Alt+M, switch the Omarchy theme | Mouse mode still works after the reload: clicks still go to the menu and the cursor stays hidden. |
| 35 | With the menu closed, press Ctrl+Alt+M in a terminal | Nothing happens in the glasses; the key reaches the terminal (it is bound only while the menu is open). |
| 36 | Stop the session from mouse mode | The cursor is visible and clicks work after the run ends. |
