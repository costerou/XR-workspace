# Placement pills: test coverage and glasses test script

On 2026-11-02 (`feature/glass-pills`) the head-locked placement menu card and its mouse mode (Ctrl+Alt+M) were replaced
by a glass pill under each desktop in the overview (since 2026-11-03 drawn in Material Design 3: a tonal pill in the
theme's colours). Clicking a pill with the overview free pointer, or pressing
Ctrl+Alt+L for the selected desktop, expands it into that desktop's own placement panel with the menu's sliders and
keys: four sliders since the yaw, pitch and roll settings were removed later on 2026-11-02 (`3dof-free-placement`,
every desktop now faces you), seven before. The pill is also the desktop's move handle: it replaces the thin grey bar
that sat above each desktop. The menu card's script (steps 1–36), its integration checklist and trial-merge notes, and
its tests `placement_menu_style` and `menu_geometry_contract` went with the card. They are in git history before
`feature/glass-pills`.

Passing tests do not count as a glasses pass. Only the scripts below do. Nothing in the pill script (G1–G38), the free
pointer script (F1–F22) or the round-you script (O1–O19) has run in an isolated VM or been tried in the glasses.

## Placement pills: automated coverage

Renderer (ctest, offline):

- `spike/tests/glass_test.cpp` (`glass`): the pill's geometry (centred 10 px under the desktop, 24% of its width within
  64–160 px, never wider than the desktop, 14 px tall); the panel (480×220, four rows) sharing the pill's top and
  centre; the morph between them; the panel layout (rows under the 42 px header, sliders between the label and value
  columns, footer inside); every look state (idle, hover, drag, pill to panel) at or under 191/255 and premultiplied,
  with any accent; the panel content (readable but under the cap, the selected row's band, a knob at a field's bounds
  exactly on the slider's ends); the animation (one open at a time, opening another collapses the first, the content
  fades in over the last quarter, value changes re-render once per frame however many arrive, without restarting
  anything); a panel opened from a zoomed-in view (Ctrl+Alt+L zooms out in the same write) holding its morph until the
  overview is reached, so the first frame that can show it starts as the pill and the whole 260 ms expansion is seen,
  also after a long zoomed-in pause, while a collapse is never held; the draw order (the open panel's desktop last, over
  other pills); a held slider's press-time surface (drawn on it while open, dropped on release or collapse, no content
  re-render).
- `spike/tests/glass_render_test.cpp` (`glass_render`): on a real GLES2 context (surfaceless EGL: no display, window or
  compositor) the fragment shader matches its CPU mirror `glass_shade()` within rounding over black and over a bright
  (0.9) desktop, and over black the glass alone, and the glass with the panel content on top, stays at or under
  191/255, also in saturated, pale and deep theme colours. Reports skipped (exit 77) without EGL or GLES.
- `spike/tests/glass_theme_test.cpp` (`glass_theme`): for the border colour of every theme Omarchy ships (and pure
  colours, black and white), the pill is the M3 scheme's primary container: the border colour's hue and chroma at its own
  tone (Vantablack, White, Lumon and Kanagawa give four different pills), hover and drag lay M3's state layer over it,
  every state stays under 191/255, grey themes give grey glass and the open panel is the dark surface container behind
  its text; the panel's band is the secondary container and its tracks, fills and handles the primary, in every theme;
  `spike/tests/material_test.cpp` (`material`) checks the scheme itself (M3 tones, hue, contrast, the cap);
  `GlassPills::set_accent` re-renders an open (or collapsing) panel once per frame under a new generation, without
  restarting its animation.
- `glass_contract` (`spike/tests/glass_contract_test.py`, which runs `glass_geometry_dump`): the supervisor's copy of the
  geometry in `tools/native_spike/free_pointer.py` (`PILL_*`, `PANEL_*`, `pill_rect`, `panel_rect`) matches the
  renderer's constants and layouts.
- `spike/tests/placement_menu_ipc_test.cpp` (`placement_menu_ipc`): the `placement_menu` frame (now "this desktop's pill
  is expanded") from the shared fixture `tests/native_spike/fixtures/placement_menu_payloads.json`: valid payloads
  decode exactly, including a held slider's optional `surface` (open panels only), malformed ones are dropped without
  ending the run. `menu_pointer` is now rejected.
- `hud_test.cpp`, `overlay_residue_test.cpp`, `resize_handle_test.cpp`: updated for the removed card. A closed help card
  still leaves nothing in the frame and stays under the cap; the pill checks moved from `resize_handle_test` to
  `glass_test`.

Supervisor (Python, offline), in `tests/native_spike/`:

- `test_pill_theme.py`: the theme colour reaches the renderer as the `style` frame at the start and on each theme switch
  (each border shape Omarchy themes use), also with a pill open, which stays as it was; nothing pill-specific is sent.
  Through the installed wiring (Supervisor, VideoControl, ViewControl, real sockets), each `configreloaded` on the
  event socket, also split across reads, sends one `style` frame to the renderer socket.
- `test_glass_pills.py`: the pill and panel geometry mirror; pill and panel hit testing (the open panel first, even
  where it covers another desktop: its top strip, a slider row from 12 px before the knob's lowest position to 12 px
  past its highest (`PANEL_KNOB_LO` / `PANEL_KNOB_HI` and `PANEL_SLOP_PX`: about 5 px past each end of the drawn track),
  the rest of the panel; then corners over pills, then desktops; curved and moved panels on their own surface); click or
  drag on a pill (the 4 px threshold, so a click never nudges the desktop); a click toggles that desktop's panel and
  closes any other; Escape, Tab or Ctrl+Alt+L pressed while the button is held are not undone by the release; a press
  outside closes the open panel and then carries on (another pill's click or drag, a desktop's focus, empty space); the
  open desktop's own corners leave it open and resize it; slider snap, live drag (measured on the panel as it was at the
  press, and the panel drawn on that same press-time `surface` until the release, so the knob stays within 1.5 px of the
  pointer where a live-placed panel would drift away from it), the debounced save and the save on release; a key that
  collapses or moves the panel mid-drag ending what that drag does; pointer opens and closes going through the same path
  as Ctrl+Alt+L (keys installed and removed, frame sent); Ctrl+Alt+L, Tab, Escape and zoom-in on a clicked panel; typing
  mode (panel closed, pills hidden, pointer off); the 30 s idle close and pointer motion postponing it; the free pointer
  and its sampler thread staying on while a panel is open; Ctrl+Alt+M and mouse mode gone; the legacy `menu_mouse_*` op
  names still accepted by `xr_control.py` for cleanup only and rejected by the run; `view.remove_menu_binds_lua()` still
  equal to `xr_control.REMOVE_MENU_BINDS`, run by a Lua interpreter against a stub `hl` to show it removes an older
  run's mouse-mode bind and gives the cursor back; the bind checker (no Ctrl+Alt+M, the free pointer's left button,
  `Hyprland --verify-config` on throwaway configs).
- `test_free_pointer.py`: the pill zone under the desktop, pill and corner drags, and the free pointer staying on while
  a panel is open (it turns off for zoom, typing mode, video and idle).
- `test_placement_menu.py`, `test_view_menu_wiring.py`: the keyboard contract (Ctrl+Alt+L toggles the selected desktop's
  pill; Tab / Shift+Tab move the open panel, wrapping; the four fields, value keys, clamps, Left/right wrapping past
  behind you, and reset; the live `placement` and `placement_menu` frames; saving, debounced and on close; the modal
  keys installed and removed; the 30 s idle close); the open panel's footer keys are bound and named in the README; the
  panel loads the help card's font and uses its dimmed colours. `test_typing_mode.py`: typing mode closes an open panel
  and hides the pills.

Not checked offline: how the glass looks on the micro-OLED panels (the tonal surfaces, state layers and shadows under the
191/255 cap), whether the panel text is readable at overview size (the panel is drawn on the desktop's surface, so it
shrinks as Distance grows), whether the panel hanging below the desktop row stays in view at the default spacing,
whether the morph looks smooth at 120 Hz, how the 4 px click threshold feels with a real mouse, and the shader's frame
time. The glasses steps below cover them.

Before the glasses run, run `python -m tools.native_spike.bind_check`. It must report no clash, and its list must no
longer name Ctrl+Alt+M or a mouse-mode left button. Then rebuild the renderer and record its sha256 (other sessions
rebuild in shared worktrees).

## Glass pills: glasses test script (Cody)

This needs your usual go-ahead for a live run. Note the controller worktree HEAD, `git status`, the controller MainPID
and the renderer sha256 before you start, and check them again afterwards.

Setup: install this branch the usual way, with no XR session running while you switch. The renderer, the Python
supervisor and controller, and the panel must all come from this branch (the placement wire changed). Use your
1920×1200, scale 1.25 session mode. Start tracking, open the panel and start a session with all three desktops. Before
starting, give XR-2 a visible curve and size in the panel (for example Curvature 40 and Distance 150), so a reset has
something to undo. Use anchor mode (Ctrl+Alt+V until the pop-up says so), so you can turn your head to follow a desktop
that moves out of view. Then press Ctrl+Alt+Down.

| # | Do | Expect |
|---|----|--------|
| G1 | Look under each desktop | A small flat pill sits centred just under each of XR-1, XR-2 and XR-3 (Material Design 3): a solid tonal capsule in the theme's border colour, as light or dark as that colour, fully rounded ends and a soft shadow under it over a desktop. Hovering it with the dot tints it slightly; dragging it tints it a little more and deepens its shadow. Nothing on it is white or glaring. |
| G2 | **OLED.** Compare the pills under a dark desktop and under a bright one (a white web page) | The pills read as solid glass shapes in both cases, not as holes, and are never brighter than the help card's grey text. They don't flicker, drift or shimmer. |
| G3 | Push the mouse down past XR-1's bottom edge so the grey dot shows, and move the dot onto XR-1's pill | That pill gets brighter; the others don't. No corner bracket. |
| G4 | Click the pill without moving the mouse | It grows smoothly (about a quarter of a second) into XR-1's placement panel under XR-1: "XR-1" and "Placement" at the top with a small grabber, four rows (Left/right, Down/up, Distance, Curvature) with a slider and value each, the selected row on a faint band, and the key hints at the bottom. There is no Yaw, Pitch or Roll row. The text fades in at the end. XR-1 does not move. |
| G5 | **Readability.** Read every row and the footer | The text is sharp and readable in the help card's dimmed colours. Nothing clips at the panel's edges or at the bottom of the view. Nothing in the panel is brighter than the help card's text. |
| G6 | Click the panel's top strip | It shrinks back into the pill smoothly, a little faster than it opened. |
| G7 | **Animation.** Click the pill, and click it again before it has finished growing. Repeat a few times quickly | It turns round and shrinks back with no jump, flash or leftover pixels. |
| G8 | Open XR-1's pill, then click XR-3's pill | XR-1's panel collapses while XR-3's grows. Only one is ever open. |
| G9 | With XR-3 open, click in empty space between the desktops | The panel collapses. Nothing else happens. |
| G10 | Open XR-3, then bring the dot just onto XR-2's edge (as in F13) and click without moving | The panel collapses, XR-2 is selected and the cursor comes back there. |
| G11 | Open XR-3 again. Move the dot into XR-3 so the normal cursor shows, and click a window there | The window gets the click and the panel stays open: clicks inside a desktop go to its windows, and the run never sees them. |
| G12 | Press Escape | The panel collapses. In a terminal, the arrows, Tab, R and Escape type normally again. |
| G13 | Select XR-1 (Ctrl+Alt+Left), press Ctrl+Alt+L, then Ctrl+Alt+L again | XR-1's pill opens, then closes. |
| G14 | Zoom into XR-2 (Ctrl+Alt+Right, Ctrl+Alt+Up) and press Ctrl+Alt+L | The view zooms out to the overview, and only then XR-2's pill grows into its panel: the whole morph is seen, as in G4. It does not pop in nearly full size. |
| G15 | With XR-2 open, press Tab three times, then Shift+Tab once | The open panel moves XR-2 → XR-3 → XR-1 → XR-2, then back to XR-1, one pill collapsing as the next grows. Nothing moves in space. |
| G16 | Press Down four times, then Up once | The selected row cycles Left/right → Down/up → Distance → Curvature → Left/right, then goes back to Curvature. |
| G17 | On XR-1, select Distance. Press Right a few times, then Shift+Right | The desktop moves farther away with each press (no Apply). Shift takes a clearly bigger step. The value updates each time, and the panel stays attached under the desktop as it shrinks. |
| G18 | Hold Shift+Right until it stops, then Shift+Left until it stops | Stops at 200, then at 50. Nothing jumps or flickers at a limit. The panel is still readable at 200. |
| G19 | Repeat G18 for Curvature and Down/up, turning your head to follow XR-1. Then select Left/right and hold Shift+Right for a while. Press R at the end | Curvature stops at 0° and 120°, Down/up at −90° (underfoot) and +90° (overhead). Left/right never stops: it passes +180° (directly behind you) and carries on from −180°. The directions match the labels. The panel moves and curves with its desktop, faces you with it and stays readable. The keys do not stop at other desktops, so XR-1 may pass over XR-2 and XR-3. R puts it back. |
| G20 | Open XR-2's pill (curved and smaller in setup) and press R | XR-2 snaps flat (all defaults: in its slot, Distance 100%, Curvature 0°). XR-1 and XR-3 do not move. |
| G21 | **Slider click.** On XR-1's open panel, click near the right end of the Curvature slider | Curvature becomes the selected row and jumps to about 120°; the desktop curves at once. A click on a label or value changes nothing and the panel stays open. |
| G22 | **Slider drag.** Hold the button on the Curvature knob, drag left past the end, then release. Repeat with the Left/right and Distance sliders | The desktop flattens (turns round you, recedes) continuously while the panel holds still where it was when you pressed, so the knob stays under the dot; it stops at the limit (Left/right at ±180°). On release the panel joins its desktop again at the new placement. After release, moving the dot changes nothing. The settings panel's Place tab shows the new values. |
| G23 | **Move with the pill.** Close the panel. Hold the button on XR-1's pill and drag down, then left, then release. Then drag it back and drop it close to its slot | XR-1 follows with the pill under the dot (brightest while dragged), facing you all the way. It is not stopped one desktop height or width from its slot. Dropped within half a degree of its slot, it snaps back in. No panel opens on release. |
| G24 | Open XR-1's pill and drag its top strip | XR-1 moves the same way, with the panel open and following it. |
| G25 | Press a closed pill and release with only a tiny, shaky movement. Then hold the button on the open panel's top strip, press Escape, and release; open it again, hold the strip, press Tab, and release | The first counts as a click: the pill opens and the desktop does not nudge. With the key pressed while the button was down, the key wins: the panel stays closed after Escape, and stays on the next desktop after Tab. |
| G26 | **Corner resize.** With XR-1's panel open, drag XR-1's bottom-right corner outward and back in | XR-1 grows and shrinks about its centre, keeping its shape. Its panel stays open and the Distance row follows. |
| G27 | With XR-1's panel open, drag a corner of XR-2 | XR-1's panel closes first, then XR-2 resizes as usual. |
| G28 | **Typing mode.** Open a pill and press Ctrl+Alt+T | The panel closes, the pills and the dot disappear, and the cursor is a normal cursor everywhere. Ctrl+Alt+L and the arrows reach the focused app. Ctrl+Alt+T again: the pills are back (move the mouse if they don't show), none open. |
| G29 | **Zoom-in closes.** Open XR-2's pill, change a value, press Ctrl+Alt+Up | The panel closes first, then the view zooms in. No pill or panel shows while zoomed. Zoom out: the pills are back, none open. The change was kept (Place tab). |
| G30 | **Idle.** Open a pill and leave the mouse and keyboard alone for 30 s | The panel collapses by itself; the pills and the dot hide too. Moving the mouse brings the pills back, closed. |
| G31 | Open a pill and click one of its sliders every 20 s for a minute. Then do the same only moving the dot a little every 20 s | The panel stays open both times. |
| G32 | Change XR-3 with its pill, wait 2 s **without closing**, then open the settings panel's **Place** tab | The Place tab shows XR-3's new values. |
| G33 | Change XR-1 and close its pill at once (Escape). Stop the session and start a new one | XR-1 comes back where you left it (saved on close). |
| G34 | With a pill open, move a slider in the settings panel's **Place** tab | The desktop and the open panel's value both update. The next arrow press continues from the Place tab's value. |
| G35 | With a pill open, press Ctrl+Alt+H, then Ctrl+Alt+P | The help card shows in front as usual and the panel stays open behind it. Its Ctrl+Alt+L row names the placement pill. Ctrl+Alt+P toggles prediction (its pop-up shows) and opens nothing. |
| G36 | Switch the Omarchy theme with a pill open, then press an arrow and click a slider. Repeat with a pale (Lumon), a saturated (Matte Black) and a deep blue (Catppuccin Latte) theme | Both still work (the binds were reinstalled). The open panel, its selected-row band, slider tracks and handles and its buttons, the other pills, the roll arrows, the highlight border and the glow all change to the new theme colour together; the panel text stays readable. In every theme the pills read as solid pills in that colour, as light or dark as the border colour (Lumon pale, Vantablack mid grey, White darker grey), and nothing is glaring. No restart: the running session recolours, and the run's final status shows `theme.updates` counting each switch. |
| G37 | With a pill open, play a VR video (Ctrl+Alt+O), then press Esc | The panel closes when the video opens. No pills or dot during the video, and Ctrl+Alt+L does nothing. Back on the desktops, the pills are back and none is open. |
| G38 | Press Ctrl+Alt+M in a terminal, with and without a pill open. Then stop the session with a pill open (and once in the middle of a slider drag) | Ctrl+Alt+M reaches the terminal and nothing happens in the glasses. After the stop, windows return to the physical output, the cursor is visible and clicks work. `profiles.json` is still mode 0600 and holds the placements. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

## Overview free pointer (move, resize, focus): automated coverage and glasses test script

Since 2026-11-02 the overview's mouse can leave the desktops (`tools/native_spike/free_pointer.py`). This replaces the
earlier corner-resize (R1–R15) and grab-bar (P1–P16) scripts: they assumed the pointer was stuck inside the desktops,
with the bar's grab strip inside the desktop's top edge and corners only reachable from inside. Since
`feature/glass-pills` the move handle is the glass pill under each desktop, not the thin bar above it, so F6–F9, F15,
F16, F19 and F21 now say pill.

Automated coverage (offline only):

- `tests/native_spike/test_free_pointer.py`: the `overview_geometry` / `overview_pointer` contract (shared fixture with
  the renderer), projection and hit testing with the renderer's placement maths (the orbit round you, distance and
  curvature, every desktop facing you; front desktop first), corner and pill zones outside the desktops, drag maths (the
  pill stays under the pointer, the corner follows its diagonal, clamps, snap, bumping), the desktop / free mode
  switch (edge, monitor crossing, keyboard jumps, the 4 px return margin, samples before a hand-back), clicks (pill,
  corner, focus, empty space, lost release), gates (zoom, typing, video, idle 30 s), the 250 Hz sampler (grab,
  re-centre, hand-back, errors, reload, cleanup), the generated Lua run by a Lua interpreter against a stub `hl`, and
  the run wiring.
- `tests/native_spike/test_resize.py`, `test_grab_pill.py`: the shared left-button binds, their removal after a dead
  run, a smoke test of bumping, and the help card row.
- `spike/tests/free_pointer_test.cpp`: `overview_pointer` decoding (malformed payloads dropped, never fatal), the
  `overview_geometry` line, the dot's shape and its levels at or under 191/255.
  `spike/tests/resize_handle_test.cpp` still covers the bracket; the pills are in `glass_test.cpp` (above).
- Not done: the new cursor Lua has not been loaded under `Hyprland --verify-config`, and nothing has run in the glasses.

The steps below need your usual go-ahead for a live run, the same setup as above, and the renderer built from this
branch. Note the controller worktree HEAD, the controller MainPID and the renderer sha256 before and after. Start with
every desktop flat (Place tab: all zero, Distance 100) and press Ctrl+Alt+Down.

| # | Do | Expect |
|---|----|--------|
| F1 | Move the mouse around the middle of XR-1 | The normal cursor, on XR-1. No dot. Clicks, hover and window drags work as usual. |
| F2 | Resize a tiled window by its border, and drag a window by its title with Super | Works like on a monitor. |
| F3 | Push the mouse up past XR-1's top edge | The cursor disappears and a small grey dot with a dark ring carries on above XR-1 with no jump. It is dim, never white. |
| F4 | Move the dot around above, beside and between the desktops, and past the outer edges | It goes everywhere smoothly, as fast as the cursor did on the desktop, with no lag you can see. |
| F5 | Hold the dot still for 10 s | It stays on the same pixels (no drift or orbit). |
| F6 | Bring the dot round XR-1's side (or push the mouse down past XR-1's bottom edge) onto the pill under XR-1 | That pill gets brighter. No corner bracket. |
| F7 | Hold the left button on the pill and drag up, then down a long way, then left | Nothing moves for the first few pixels, then XR-1 follows with the pill staying under the dot, facing you all the way. It is not stopped one desktop height down or width left of its slot: it goes as far as the dot goes (in anchor mode on round you, see O8). |
| F8 | Release | No panel opens (it was a drag, not a click). Place tab: Left/right and Down/up show the new values in degrees. The dot stays where it is. |
| F9 | Drag XR-1's pill right toward XR-2 at the same height | XR-1 keeps following the dot and bumps XR-2 out of the way (and XR-3 after it), with a small gap kept between them (XR-1's pill strip counts as part of XR-1). Drag back: XR-2 and XR-3 stay where they were pushed. Lift it above the row first and it passes over without bumping. Dropped within half a degree of its slot it snaps in. See B1–B12. |
| F10 | Move the dot just outside XR-1's bottom-right corner | A small grey L-bracket appears inside that corner. |
| F11 | Drag the corner outward, then far inward | XR-1 grows and shrinks about its centre with the corner under the dot, keeping its shape. It stops at twice and at half its size. Place tab: Distance changes (bigger = lower). Growing bumps XR-2 out of the way; shrinking leaves it there. |
| F12 | Move the dot down onto the middle of XR-2 | The normal cursor reappears on XR-2 where the dot was, the dot goes away, and XR-2 becomes the highlighted desktop. |
| F13 | Highlight XR-1 (Ctrl+Alt+Left), then on XR-2 push the mouse just past its top edge so the dot shows on the edge, and click without moving | XR-2 is highlighted and the cursor appears there. That click does not reach the window; the next one does. |
| F14 | Click with the dot in empty space | Nothing happens. No window gets the click. |
| F15 | Leave the mouse still for 30 s with the dot showing | The dot, pills and bracket fade off. A click then does nothing but bring them back. |
| F16 | With the dot showing, open the selected desktop's pill with Ctrl+Alt+L, then close it with Escape | The dot stays and keeps working while the pill is open (the free pointer drives the panel). After closing, the overview works as before. |
| F17 | With the dot showing, zoom in (Ctrl+Alt+Up) | The cursor is visible and normal again. Zoom out: the free pointer is back. |
| F18 | With the mouse on DP-1, press Ctrl+Alt+Down | The cursor jumps into the middle of the highlighted desktop. |
| F19 | Switch the Omarchy theme while the dot shows | Still free afterwards: the cursor stays hidden and the pill can still be dragged. |
| F20 | Stop the session from the panel while the dot shows (or mid-drag), then left-click on DP-1 | The cursor is visible and the click works. The next session starts with the three desktops flat in their row again: moves and sizes are not kept. |
| F21 | If VR video is merged: play a video, move the mouse past a desktop's edge where it was | No dot, no pills; nothing moves (the video gate ignores the click ops). |
| F22 | Check the desktops' resolution and scale in the panel and with `hyprctl monitors` | Unchanged: moving and resizing never change pixels or scale. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

## Desktops anywhere around you, always facing you: automated coverage and glasses test script

Since 2026-11-02 (`3dof-free-placement`) Left/right and Down/up turn a desktop round you in tenths of a degree (shown in
degrees), every desktop always faces you, upright, and the yaw, pitch and roll settings are gone from the settings
panel, the glass panel, the wire and the profiles. In anchor mode a pill drag can carry a desktop anywhere round you,
and desktops never overlap as seen from your eye. Details:
[technical.md](technical.md#moving-desktops-in-the-overview-the-pill-under-the-desktop). G4, G16, G19–G23 and F7–F9
above were changed for this.

Automated coverage (offline only):

- `tests/native_spike/test_orbit.py`: `tools/native_spike/orbit.py`: the placement maths, the dot's linear reach past the
  view and the re-grab of a pill round you, the drag target (wrap, snap, poles) and the footprints. The no-overlap guard
  that stopped a drag at a neighbour gave way to bumping on 2026-11-03 (`test_bump.py`, below).
- `test_free_pointer.py`, `test_glass_pills.py`, `test_grab_pill.py`: hit testing and drags on desktops that are moved
  round you and face you; the four-row panel.
- `test_placement_menu.py`: the four fields and their keys, Left/right wrapping past behind you.
- `test_profiles.py`: a profile saved with yaw, pitch and roll loads without them and keeps its other settings.
- ctest `view`, `hud`, `glass`, `ipc`, `placement_menu_ipc` and `placement_contract` (the renderer's `place_point`
  against `orbit.place_point`).
- Not done: nothing here has run in an isolated VM or in the glasses.

Not checked offline: how the side desktops look now that they turn toward you, whether text stays readable on a desktop
overhead or behind you, how the dot's steady angle per mouse pixel feels past the view's edge, whether a pill can be found again without seeing
the dot, how big the guard's gap looks, the frame time with desktops all round you, and comfort. The steps below cover
them.

The steps need your usual go-ahead for a live run, the same setup as the pill script, and the renderer, the Python
supervisor and controller, and the panel all installed from this branch. Note the controller worktree HEAD, the
controller MainPID and the renderer sha256 before and after. O2 needs a profile saved by an older build in which a
desktop is moved and has yaw, pitch or roll set; skip it if you have none. Start with every other desktop flat (Place
tab: all zero, Distance 100%), use anchor mode (Ctrl+Alt+V) unless a step says otherwise, and press Ctrl+Alt+Down.

| # | Do | Expect |
|---|----|--------|
| O1 | Look at the row straight ahead, then turn your head to look straight at XR-1, then at XR-3 | XR-2 is flat in front of you. XR-1 and XR-3 are each turned a little toward you, not flat in one plane with XR-2. Looked at straight on, each is square to you. Text on all three is upright. |
| O2 | (Older profile.) Start a session with it, open the Place section and that desktop's pill | It loads. The moved desktop is about where it was; Left/right and Down/up now show degrees, at most 10°. Its distance and curvature are as before. Its yaw, pitch and roll are gone and it faces you. |
| O3 | Open XR-1's pill. Then look at the settings panel's **Place** section | The glass panel has four rows: Left/right, Down/up, Distance, Curvature, reading 0°, 0°, 100%, 0°. It is shorter than the old seven-row panel and the key hints fit at the bottom. The Place section has the same four sliders. No Yaw, Pitch or Roll anywhere. |
| O4 | Select Left/right. Press Right five times, then Shift+Right once | Each press turns XR-1 a little to the right round you. The pill's whole-degree value changes only every few presses; the Place section shows tenths (1° after five presses). Shift+Right turns it 15° more. XR-1 keeps facing you. The keys don't stop at XR-2, so XR-1 may cover it. |
| O5 | Keep pressing Shift+Right, turning your head to follow XR-1 round to your right and behind you | It carries on round you, facing you all the way, and never stops: past +180° (directly behind you) the value goes on from −180°. Press R: it goes back into its slot. |
| O6 | Select Down/up. Hold Shift+Up until it stops, and look up. Then Shift+Down until it stops, and look down. Press R | It stops at +90°: XR-1 is straight overhead, facing down at you, and reads upright as you tilt your head back. At −90° it is underfoot, facing up at you. R puts it back. |
| O7 | Close the panel. Drag XR-1's pill slowly left and up, inside the view | The pill stays under the dot. XR-1 moves round you and keeps facing you, upright, the whole way. It does not skew or lean as it goes. |
| O8 | Keep dragging left past the edge of the view, to the end of the dot's reach, and release | Past the edge XR-1 speeds up smoothly, with no jump at the edge. At the end of the reach it is directly behind you. Turn round: it faces you. |
| O9 | Drag XR-3's pill up past the top of the view, to the end of the reach, and release | XR-3 is straight overhead, facing down at you. |
| O10 | **Re-grab.** Turn round to look at XR-1 behind you. Move the mouse left past the edge of the view, the way you dragged it, until XR-1's pill lights up. Hold the button and drag back right, then release near its slot | The pill lights up although you can't see the dot. XR-1 follows the drag back round to the row. Dropped within half a degree of its slot, it snaps in. XR-1's corners and the desktop itself never light up this way. |
| O11 | **No overlap.** Drag XR-1's pill right toward XR-2 at the same height | XR-1 bumps XR-2 (and then XR-3) out of the way, with a small gap kept (XR-1's pill strip counts as part of XR-1). The desktops never cover each other as you see them. |
| O12 | Lift XR-1 so that it is half above XR-2, then drag it right | XR-2 is pushed sideways ahead of it, steadily, never jumping or switching to being pushed down and back. Lift XR-1 almost clear: XR-2 is nudged down a little and XR-1 passes over. |
| O13 | Bring XR-1 back beside XR-2 at the same height and flick the mouse fast far to the right | XR-2 (and XR-3) are pushed ahead of XR-1 all the way. XR-1 never jumps to XR-2's other side and never covers it. |
| O14 | **Existing overlap.** Make XR-1 and XR-2 overlap with the settings panel of an older build, if you have one (with this build every way of moving bumps). Drag XR-1's pill left, then right | Left, away from XR-2, moves freely. Right pushes XR-2 so the overlap never gets deeper. It never gets stuck. |
| O15 | **Zoom.** Put XR-1 behind you and XR-3 overhead again (O8, O9). Select XR-2 and zoom in (Ctrl+Alt+Up), then zoom out. Then select XR-1 and zoom in | Zoomed into XR-2, it is flat in front of you and XR-1 and XR-3 are back at their row slots beside it. On zoom-out they go back behind you and overhead. Zoomed into XR-1, it comes flat in front of you. |
| O16 | **Follow and locked.** Back in the overview with XR-1 behind you, switch to follow mode (Ctrl+Alt+V), drag XR-2's pill out past the edge of the view, then do the same in locked mode | XR-1 stays out of sight in both modes. XR-2 only goes as far as the dot: no speed-up past the edge, and no pill lights up except under the dot. Selecting XR-1 (Ctrl+Alt+Left / Right) and zooming in (Ctrl+Alt+Up) shows it. Zoom out, switch back to anchor mode and reset XR-2 (select it, Ctrl+Alt+L, R). |
| O17 | **Out of reach.** With XR-1 behind you in anchor mode, try to reach its corners and the desktop itself with the mouse. Then select it with Ctrl+Alt+Left / Right and press Ctrl+Alt+L | Its corners and the desktop don't react. Ctrl+Alt+L opens its panel (turn round to see it), the arrows change it, and R puts it back in its slot. |
| O18 | **Not saved.** Put XR-1 behind you and XR-3 up and to the right. Stop the session and start a new one | All three are back flat in their row: placements last one session (XR starts fresh). |
| O19 | **Comfort.** Spend a few minutes looking round at desktops beside, behind and above you | Note any shimmer, seams, flicker, judder or discomfort, and whether the side desktops are easier to read now that they face you. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

## Bumping desktops out of the way: automated coverage and glasses test script

Since 2026-11-03 (`monitor-collision-bump`) a desktop moved or resized in any way pushes the desktops it runs into out of
its way, and they stay where they were pushed (`tools/native_spike/bump.py`; see
[technical.md](technical.md#bumping-desktops-out-of-the-way)). Automated (offline) coverage:
`tests/native_spike/test_bump.py` (collision detection on the footprints, push resolution, cascades, no room near the
poles and in a full ring, hysteresis, a jittering pointer, determinism, random edits never leaving a new overlap, timing,
and the run's keys, slider press and drag, settings panel and glide), `test_free_pointer.py` and `test_glass_pills.py`
(the pill drag bumps), `test_fresh_start.py` (a run that moved and bumped its desktops writes nothing), and the ctests
`view` (`glide_toward`, `step_placements`) and `ipc` (`glide`). Not yet tried in an isolated VM or in the glasses.

| # | Do | Expect |
|---|----|--------|
| B1 | In the overview, drag XR-2's pill slowly right into XR-3 | XR-3 slides away ahead of XR-2, in step with it, a small gap between them. No overlap at any moment, no jump. |
| B2 | Keep dragging right, then back left to XR-2's slot, and release | XR-3 stays where it was pushed (no spring back). XR-2 snaps into its slot. |
| B3 | Drag XR-3's pill left into XR-2 and on into XR-1 | XR-2 is pushed, and then pushes XR-1: all three move left together, in order. |
| B4 | Lift XR-1 above the row, put it right over XR-2 and bring it down onto it | XR-2 is pushed down, not aside. |
| B5 | Drag a corner of XR-2 outward until it is much bigger than the view | XR-1 and XR-3 slide outward on both sides as it grows, evenly. Shrink it again: they stay where they are. |
| B6 | Open XR-1's pill, select Left/right and press Shift+Right a few times | XR-1 and the desktops it runs into glide together, about a quarter of a second each press, with no snap. A press that bumps nothing moves as before. |
| B7 | Still in the panel, press R | XR-1 goes back to its slot and pushes aside whatever is there now. |
| B8 | Click far along the Distance slider (smaller number = bigger), then drag the knob | The click glides; the drag follows the knob, the neighbours moving with it. |
| B9 | In the settings panel's Place section, set a desktop's left/right so it would land on another and Apply | The other desktop is pushed aside in the glasses (and the Place section shows its new values after a moment). |
| B10 | Lift XR-2 high (Down/up near +80°), lift XR-1 under it and push XR-1 up into XR-2 | XR-2 never goes past straight overhead. XR-1 stops where it still fits; nothing overlaps. Drag on sideways: XR-1 slides along. |
| B11 | Flick a pill fast across the whole row | Every desktop in the way is pushed ahead; none ends up on the far side of the one you move, none overlaps. |
| B12 | Stop the session and start again | Three flat desktops in the row: nothing of the moves or pushes is kept. |
| B13 | Comfort | Note any judder, lag between the moved desktop and the ones it pushes, or a glide that feels too slow or too fast. |

Record any failure with the step number and what you saw. Keep it apart from automated test results.

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
  off the surface is unchanged. (This test was removed on 2026-11-02 with the placement menu card.)

Bind check, run on this machine on 2026-11-02 (Hyprland 0.56.2, the live `~/.config/hypr` copied into a temporary HOME):

```
$ python -m tools.native_spike.bind_check
Hyprland --verify-config: config ok (0)
desktop config: 250 chords bound
no clash: 12 run binds, send-window binds (Ctrl+Alt+G, Ctrl+Alt+1, Ctrl+Alt+2, Ctrl+Alt+3, Ctrl+Alt+B), menu binds (Ctrl+Alt+M, Down, Esc, Left, R, Right, Shift+Left, Shift+Right, Shift+Tab, Tab, Up) and the mouse-mode left button
```

That run predates the glass pills. Ctrl+Alt+M and the mouse-mode left button are no longer bound; the pill keys are the
same list without Ctrl+Alt+M.

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
