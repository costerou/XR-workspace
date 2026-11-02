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

## Overview grab pill (move): automated coverage and glasses test script

Automated coverage (offline only):

- `tests/native_spike/test_grab_pill.py`: the grab strip and its precedence below the corners, move math (direction,
  gain, clamp, snap), the overlap guard and slide, the drag state machine (live frames, warp filtering, save, panel
  sync), off while zoomed or with the menu open, idle hide, the hidden-cursor Lua and its cleanup (run end, dead run),
  and the poller's re-centring.
- `spike/tests/resize_handle_test.cpp`: `move_handle` decoding (malformed payloads dropped), pill geometry, its levels at
  or under 191/255, and the renderer's placement easing.
- The cursor Lua also loads under `Hyprland --verify-config`, run with this project's `bind_check` harness. That is not a live test.

Same setup and go-ahead as the corner script. Start in the overview with every desktop flat (Place tab: all zero).

| # | Do | Expect |
|---|----|--------|
| P1 | Look at the overview | A thin, dim grey bar is centred just above each desktop. It never drifts. |
| P2 | Move the pointer to the top centre of XR-1 | XR-1's bar gets brighter. The other bars stay dim. No corner bracket. |
| P3 | Move along the top edge toward a corner | The bar dims again before the corner bracket appears; the two never show together. |
| P4 | On XR-1's bar, hold the left button and drag down | The cursor disappears and XR-1 moves down smoothly with the mouse. The window under the bar gets no click. |
| P5 | Keep holding: drag up a long way, then left | XR-1 follows up past where it started (the cursor never gets stuck at the top), then left. It stops at one desktop height up / width left. |
| P6 | Release | The cursor reappears on XR-1's bar, where you grabbed. Place tab: Left/right and Down/up show the new values. |
| P7 | Drag XR-1 right toward XR-2 at the same height | It stops when it just touches XR-2 (a small overlap at most). It never covers XR-2. |
| P8 | Keep the button held, drag up until XR-1 is above the row, then right | XR-1 passes over XR-2 once it is clear of the row. |
| P9 | Drag XR-1 back to roughly where it started | It snaps into its slot in the row when close. Place tab: 0 and 0. |
| P10 | Hover the bar without moving for 30 s | All bars fade off; moving the pointer brings them back. |
| P11 | Open the placement menu (Ctrl+Alt+L) | The bars disappear and a click on the top centre goes to the window. Close the menu: they return. |
| P12 | Zoom in (Ctrl+Alt+Up) | No bars while zoomed or zooming. |
| P13 | Move XR-3, stop the session, start a new one | XR-3 comes back where you left it. |
| P14 | During a drag, press nothing else and stop the session from the panel | The cursor reappears where you grabbed, visible. A left click on DP-1 works. |
| P15 | If VR video is merged: play a video, click the top centre of where a desktop was | Nothing moves (the video gate ignores the click ops). |
| P16 | Resize a desktop by a corner, then move it by its bar | Each does only its own thing; a move never changes size and a resize never moves. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

## Sliders and mouse mode

Each setting in the menu is drawn as a slider. With the menu open, **Ctrl+Alt+M** turns on mouse mode: a round pointer
moves freely over the whole menu card, 1:1 with the mouse, a click snaps the slider under it, and a drag moves it live.
Ctrl+Alt+M again or Escape leaves mouse mode.

How the pointer gets to the glasses (rebuilt 2026-11-02 after the first glasses trial felt laggy and jumpy):

- A sampler thread reads Hyprland's `cursorpos` every 4 ms (250 Hz) while mouse mode is on, adds up the motion, and
  wakes the supervisor loop through a socket. The hidden cursor is put back where mouse mode started whenever it strays
  200 px, so no screen edge ever stops the pointer.
- The loop moves the pointer in card pixels and sends one small `menu_pointer` frame per wake with the latest position.
- The renderer draws the pointer as its own 26×26 overlay on the next frame. It wakes on the control socket instead of
  waiting out its 50 ms Wayland poll. Moving the pointer never re-renders the menu card.
- The renderer reports the card's layout (tab and slider rectangles, in card pixels) on its status socket
  (`menu_geometry`, only when it changes). The supervisor hit-tests clicks against it, so only a click or drag on a
  slider changes a value.
- A drag changes values, and so the card: that is rasterised at most once per displayed frame (lazily), in about 2 ms.
  The first version took about 24 ms per pointer move, on the render thread.

Automated coverage:

- `tests/native_spike/test_placement_mouse.py`: the pointer state machine (on/off, Escape leaves mouse mode first,
  Ctrl+Alt+L and the idle close end it, nothing shows before the layout is known); free 1:1 motion in card pixels
  (sub-pixel motion adds up, every corner of the card is reachable, clamped to the card with no build-up past an
  edge); hovering changes nothing; one pointer frame per tick; a click on a slider snaps it (both bounds of every
  field, with a pointer-radius slop at the ends), a click elsewhere does nothing; live drag, clamp and release; the
  desktop tabs; debounced save; activity for the 30 s idle close (motion and clicks count, a still cursor does not);
  bind order; the sampler (motion summed, the loop woken, more than 120 samples a second, stops with mouse mode); the
  hidden-cursor re-centring (nothing lost to the warps); bad cursor replies; cursor follow paused; the reload rate limit;
  the supervisor woken by motion within 30 ms and routing `menu_geometry` (malformed lines ignored, run status
  untouched); the run wiring; the wire formats; the generated Lua and the bind clash checker, including real
  `Hyprland --verify-config` runs on throwaway configs when Hyprland is installed.
- `spike/tests/placement_menu_ipc_test.cpp`: the `menu_pointer` op from the shared fixture's `menu_pointer_valid` /
  `menu_pointer_invalid` cases (x and y 0..8192 card pixels; a malformed object is dropped without ending the run), and
  that `placement_menu` no longer accepts a pointer.
- `spike/tests/hud_test.cpp`: the geometry matches the card for both fonts and four output sizes (one slider per visible
  row, bands stacked, tabs above, independent of values); a knob at a field's low or high bound is centred on that
  slider's reported ends; the pointer image is round, hard-edged, in the text grey only, at most `kCardMaxLevel`; the
  card origin formula; pointer moves never re-render the card; several card changes in one frame cost one render.
- `spike/tests/overlay_residue_test.cpp` (real GLES on a surfaceless EGL context): the pointer lands exactly on the card
  pixel it names (a 1 px error fails), moves 1:1 with no card re-render, stays under the brightness cap, and leaves
  nothing behind when hidden or when the menu closes.
- `menu_geometry_contract` (ctest): the renderer's real `menu_geometry` lines for six layouts parse in the supervisor,
  and its hit test maps both ends of a slider to the field's bounds.
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
`/eval` the way it does at config load (the re-centring depends on the latter), how the hidden cursor interacts with
Omarchy's startup-cursor handling, the end-to-end latency in the glasses, and how the 1:1 speed feels. The glasses
steps below cover them.

Glasses steps (continue after step 21, with a new run started the usual way):

| # | Do | Expect |
|---|----|--------|
| 22 | Press Ctrl+Alt+L | Every setting shows a slider between its label and value. The knob sits at the value; the stretch from flat to the knob is tinted blue. Nothing is brighter than the text. |
| 23 | Press Ctrl+Alt+M | A ring appears around the selected setting's knob. The desktop cursor disappears from every XR desktop. |
| 24 | Move the mouse slowly, then quickly, in circles and diagonals all over the menu | The ring follows the hand smoothly with no visible lag, stutter or jumps, glides between rows (no snapping) and reaches the title, labels, values and every corner of the card. It never sticks at a screen edge, however far the mouse travels. Nothing moves in space and no value changes. Cursor follow does not switch desktops. |
| 25 | On Yaw, click near the left end of the slider | The desktop turns at once; the value reads about −60°. The ring fills while the button is held. A click on a label or empty space changes nothing. |
| 26 | Hold the button and drag right, then release | The desktop turns continuously with the drag and the knob stays under the ring; the value updates. Dragging past the end stops at +60°. After release, moving the mouse moves only the ring. |
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

## Sending windows to the glasses (Ctrl+Alt+G, Ctrl+Alt+1/2/3, Ctrl+Alt+B)

Run binds, installed and removed with the other run keys (`tools/native_spike/send.py`):

| Keys | Sends the focused window to |
| --- | --- |
| Ctrl+Alt+G | the desktop selected in the glasses view (zoomed in, or highlighted in the overview) |
| Ctrl+Alt+1 / 2 / 3 | XR-1 / XR-2 / XR-3 (bound by keycode, `code:10..12`) |
| Ctrl+Alt+B | the run's physical fallback monitor, only if the window is on an XR desktop |

The window goes onto the active workspace of the target output and focus follows it.

Automated coverage (offline only):

- `tests/native_spike/test_send_window.py`: target choice (selected desktop, numbered, a desktop the run lacks),
  refusals (pinned, no window, malformed address, special or missing target workspace, Ctrl+Alt+B on a window not in the
  glasses or with no fallback), already-there no-op, Hyprland refusing the move, errors never escaping the worker,
  close-before-recovery (a move in flight lands first, none starts after, the wait is bounded), the mutation allowlist
  (only this move shape, by address, follow on, workspace 1..2^31-1), the wire and helper op lists, routing from the
  supervisor with the selected desktop, the run wiring order (sender closed before the binds are removed, recovery
  after the controls close), the generated Lua (guarded, keycodes, removed at run end, the reload reinstall still one
  eval for the view binds), the no-session notification, the help row and the bind clash checker, including
  `Hyprland --verify-config` runs on throwaway configs (a desktop `CTRL + ALT + 3` is found as a clash with
  `code:12`; Hyprland builds the move dispatcher).
- `spike/tests/placement_menu_style_test.cpp`: the help card with the new Ctrl+Alt+H row still fits 1920×1200 @1.25:
  1404×945 with JetBrainsMono (was 1220×945), still 12 rows. The existing warning that its 18 px fade-in drop runs 3 px
  off the surface is unchanged.

Bind check, run on this machine on 2026-11-02 (Hyprland 0.56.2, the live `~/.config/hypr` copied into a temporary HOME):

```
$ python -m tools.native_spike.bind_check
Hyprland --verify-config: config ok (0)
desktop config: 250 chords bound
no clash: 12 run binds, send-window binds (Ctrl+Alt+G, Ctrl+Alt+1, Ctrl+Alt+2, Ctrl+Alt+3, Ctrl+Alt+B), menu binds (Ctrl+Alt+M, Down, Esc, Left, R, Right, Shift+Left, Shift+Right, Shift+Tab, Tab, Up) and the mouse-mode left button
```

Not checked offline: that Hyprland 0.56.2 carries out `hl.dsp.window.move({ workspace, follow, window })` from a runtime
`/dispatch` the way Omarchy's own bind does, where floating and fullscreen windows land on an XR desktop, and whether
the notifications reach the desktop from the run's systemd unit. The glasses steps below cover them.

Glasses steps (a new run started the usual way, three desktops, a terminal and a floating window open on DP-1):

| # | Do | Expect |
|---|----|--------|
| S1 | In the overview, select XR-2 (Ctrl+Alt+Right), focus the terminal on DP-1, press Ctrl+Alt+G | The terminal appears on XR-2 in the glasses and has keyboard focus. DP-1 no longer shows it. |
| S2 | Zoom into XR-1 (Ctrl+Alt+Left, Ctrl+Alt+Up), focus another DP-1 window, press Ctrl+Alt+G | It lands on XR-1. |
| S3 | Focus a DP-1 window, press Ctrl+Alt+3 | It lands on XR-3, whichever desktop is selected. Repeat with Ctrl+Alt+1 and 2. |
| S4 | Send the floating window with Ctrl+Alt+G | It appears on the target desktop, inside its bounds. |
| S5 | With a window on XR-2 focused, press Ctrl+Alt+B | It goes back to DP-1, on the workspace DP-1 shows, with focus. |
| S6 | Focus a window on DP-1, press Ctrl+Alt+B | Nothing moves. A notification says the window is not on the glasses. |
| S7 | Pin a window on DP-1 (Omarchy's pin key), press Ctrl+Alt+G | Nothing moves. A notification says to unpin it first. |
| S8 | Press Ctrl+Alt+G with the window already on the selected desktop | Nothing happens. |
| S9 | Switch the Omarchy theme, then press Ctrl+Alt+G on a DP-1 window | It still moves (the binds were reinstalled). |
| S10 | With windows sent to XR-1 and XR-3, stop the session | Every window, including the sent ones, is back on DP-1. Recovery reports complete. |
| S11 | After the session ends, press Ctrl+Alt+G and Ctrl+Alt+B in a terminal | Nothing moves; the keys reach the terminal (no run, no binds). |
| S12 | Start a session, send a window to XR-2, kill the run unit (as in the Recover test), press Ctrl+Alt+G, then Recover from the panel | The key shows "No XR session is running" and moves nothing. Recover returns the window on XR-2 to DP-1. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.
