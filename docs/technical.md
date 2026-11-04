# XR Workspace: technical reference

The [README](../README.md) is the friendly guide. This page keeps the full detail: what has been tested and how,
how the pieces fit together, build and install internals, the exact keybind rules, the safety model, the tests and the
host diagnostic. Feature deep dives live in [vr-video.md](vr-video.md), [web-video.md](web-video.md) and
[placement-menu-test-plan.md](placement-menu-test-plan.md).

## Summary

A local XR desktop for Omarchy/Hyprland on VITURE Beast and Luma Ultra glasses. It creates independent virtual desktops
(three 1080p outputs by default), captures only those outputs, and renders them in the glasses with 3DoF head tracking.
You start and control a session from an Omarchy shell panel, or with Ctrl+Alt keybinds while a session runs.

## Status

Every claim in this repo says whether it comes from offline tests, an isolated VM, or the physical glasses. Passing tests or a VM trial is not physical acceptance.

| Area | Evidence |
| --- | --- |
| Managed virtual desktops, capture, rendering, viewing modes, zoom/switch controls, pose prediction, theme-reload survival | Physical runs on Luma Ultra through the `tools.native_spike` CLI. The `mod1` run presented ~119.9 Hz over 168 s on the zero-copy dmabuf path. The `trk1` run held ~119.8 Hz for 214 s with 0 tracking losses. These measure presentation cadence only, not latency or long-session comfort. |
| Settings panel and controller service | Physical panel acceptance on Luma Ultra (2026-11-01; controller source `d685884`, which includes `8538468`, `4cfa04d` and `d9fbb28`; pinned renderer `62989b6e…` built from `822495c`). Run `d3adc32d` (on controller code a peer session had loaded but not yet committed; the later run and the recovery used committed `d685884`), started from the panel, calibrated tracking, switched DP-2 to 1920×1200@120 and presented ~120.0 Hz on dmabuf with 0 missed frames. Cody confirmed in the glasses that zoom/overview, desktop switching, spacing, recenter, viewing modes, cursor follow, prediction, the help card and live window content all worked. Panel Stop exited cleanly (renderer rc 0, nothing left over). In run `88b7e850`, the session kept running through a controller restart, and the new controller picked it up again. After a deliberate SIGKILL of the run unit, the panel showed Recover, and panel Recover put the desktop back to its baseline (XR outputs removed, plugin unloaded, DP-2 back to 1080@60). Earlier physical attempts failed before the renderer launched (mode readback, then `Errno 11`). Beast panel sessions are untested. The ~120 Hz figure is presentation cadence only. |
| Per-desktop placement (position, distance, curvature; until 2026-11-02 also yaw, pitch and roll) | Physical acceptance on Luma Ultra (2026-11-01; `feature/per-desktop-transform` at `f593574`, which is `ba3ead2` plus `d685884`; pinned renderer `28d934fa…`; ownership plugin `a68bd142…`, the same binary as the panel acceptance). In runs `cf73a329` and `2af10af8`, started from the panel, Cody angled XR-1 and XR-3 with yaw and curved XR-2 from the placement editor. He confirmed in the glasses that the angles and curvature applied correctly in the running session. Only yaw and curvature were reported on; the offset, distance, pitch and roll sliders are covered by offline tests only. That run predates two changes of 2026-11-02, neither tried in the glasses (the "Desktops anywhere around you" row below): Left/right and Down/up are now a turn round you instead of a flat offset, and every desktop now faces you, so the yaw, pitch and roll settings are gone. Of what Cody checked, only curvature is still a setting. Frame-time cost of curved desktops and comfort are unmeasured. Beast is untested. |
| Cursor-follow retest | After the startup-state fix (`b309827`, in this branch), Cody reported a cursor-follow retest on the Luma Ultra (start XR, wait for calibration, fullscreen YouTube) with no blackout. That is a human-reported result, and the earlier blackout's root cause is unproven. Details: [feasibility/native-spike/luma-cursor-follow-retest.md](feasibility/native-spike/luma-cursor-follow-retest.md). |
| Beast | Presentation-only physical trials (generated pattern, normal exit, controlled crash). Managed sessions, tracking and capture on Beast are untested. |
| Overview free pointer (the mouse leaves the desktops in the overview: drag a desktop's pill to move it, the handles just outside its edges and corners to resize it; since 2026-11-03 every desktop's windows take the mouse and keyboard as on a normal screen, a click never maximizes (Ctrl+Alt+Up does), and the dot is one cursor whose look shows what a press would do: see [Handles and content](#handles-and-content), offline tests in `tests/native_spike/test_full_interaction.py`, `test_pointer_zones.py` and the `free_pointer` ctest) | Offline tests only (`tests/native_spike/test_free_pointer.py`, `test_resize.py`, `test_grab_pill.py`; `spike/tests/free_pointer_test.cpp`, `resize_handle_test.cpp`). Not yet tried in the glasses; its new cursor Lua has not been run under `Hyprland --verify-config`. Steps F1–F22 in [placement-menu-test-plan.md](placement-menu-test-plan.md); they replace the earlier corner (R) and bar (P) steps, which assumed the pointer was confined to the desktops. The move steps now use the glass pill under the desktop instead of the old bar above it. |
| Glass placement pills (a Material Design 3 tonal pill under each desktop in the overview; a click or Ctrl+Alt+L expands it into that desktop's placement panel; replaces the head-locked placement menu card and its Ctrl+Alt+M mouse mode) | Offline tests only. Renderer (ctest): `glass` (geometry, morph, look under the 191/255 cap, panel content, animation), `glass_render` (the GLSL shader against its CPU mirror on a surfaceless EGL context, no display), `glass_contract` (the renderer's pill and panel geometry against `free_pointer.py`), and the updated `hud`, `placement_menu_ipc`, `overlay_residue` and `resize_handle`. Supervisor: `tests/native_spike/test_glass_pills.py`, `test_free_pointer.py`, `test_placement_menu.py`, `test_view_menu_wiring.py`. Not run in an isolated VM and not tried in the glasses: the look, readability on the OLED panels, the expand animation and how clicks and drags feel are unverified. Steps G1–G38 in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Desktops anywhere around you, always facing you (3DoF placement: Left/right and Down/up turn a desktop round your eye; every desktop faces the eye, upright, and the yaw, pitch and roll settings are removed; a pill drag keeps the desktop under the dot, and in anchor mode the dot, and so the drag, reaches all the way round you; desktops never overlap as seen from the eye) | Offline tests only: `tests/native_spike/test_orbit.py`, `test_free_pointer.py`, `test_glass_pills.py`, `test_grab_pill.py`, `test_placement_menu.py`, `test_profiles.py`; ctest `view`, `hud`, `glass`, `ipc`, `placement_menu_ipc`, `placement_contract`. Not run in an isolated VM and not tried in the glasses: how the side desktops look turned toward you, how the drag past the view's edge feels, re-grabbing a pill you cannot see the dot for, the guard's stop and slide, and comfort with desktops behind or above you are unverified. The wire and the profile changed: a placement has four fields (`x`, `y`, `distance`, `curvature`), and Left/right and Down/up are tenths of a degree where they were percent of the desktop's size. A placement saved before 2026-11-02 reads as at most 10°, about where it was, and loses its yaw, pitch and roll. The renderer, the Python supervisor and controller, and the panel QML must be installed together (see [Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)). Steps O1–O19 in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Bumping (`monitor-collision-bump`, 2026-11-03: a desktop moved or resized in the overview, by its pill, a corner or its open panel's sliders and keys, pushes every desktop it runs into out of its way, cascading, judged on the footprints as seen from the eye; pushed desktops stay where they were pushed and are saved with the layout like any other move; a discrete edit that bumps glides; a changed or restored layout pushes overlapping desktops apart; the settings panel's Apply applies as given; Super going down stops a monitor drag) | Offline tests only: `tests/native_spike/test_bump.py` (collision detection against the footprints' overlap, push resolution, cascades, no room, stability and hysteresis, determinism, timing, settling, the handles, Super + drag, the run's keys / sliders / panel / glide, the normal save path), `test_free_pointer.py`, `test_glass_pills.py`, `test_grab_pill.py`, `test_orbit.py`, `test_resize.py`; ctest `view` (`glide_toward`, `step_placements`) and `ipc` (the `placement` frame's `glide`); `test_bump.py` also loads the Super keys' binds through the real `Hyprland --verify-config`. Not run in an isolated VM and not tried in the glasses: how a bump feels while dragging, the quarter-second glide, pushes near the poles or with big curved desktops, and whether the Super keys' binds fire as Hyprland's source suggests are unverified. The `placement` frame gained an optional `glide`, so install the renderer and the Python supervisor together. See [Bumping](#bumping-desktops-out-of-the-way). |
| Level bar and corner caps (Cody's pick of the handle mockups, 2026-11-03, replacing the roll brackets and the corner L-bracket: every desktop in the overview has one segmented capsule above its top edge, the pill's counterpart, turn left / level / turn right, and a quiet rounded cap round each corner, all in the theme's M3 colours; they move, scale and roll with the desktop; a click on a turn segment turns it that way, straight to level when level lies that way (in anchor mode, judged against gravity from the head tracker), else 2°; a drag from either rolls it either way; the middle segment levels it and moves nothing else; R in its open pill levels it as it puts it back flat; only the segment or cap under the dot lights; they never take a press from a desktop's content, the 6 px edge band, a resize corner or a pill) | Offline tests only: ctest `roll_level` (the roll about the line of sight, the tilt from level, the bar's size, place, segments and hit region, its share of the desktop through resize, move, curvature and roll, every pixel of the atlas: premultiplied, under the cap, only the hot segment changed, the same L in every corner, the `roll` frame), `glass_theme` (the roles in every theme), `material` (the turn and level icons, the cap's shape), `resize_handle` (the caps' boxes, the `resize_handle` frame), `glass_contract` (constants, rects and a grid of segment probes, renderer against supervisor; the idle caps inside the corners' zones), `placement_contract` (the rolled surface), `gl_frame` (bars and caps in the frame cache check, drawn at a desktop's live top edge and corners while a held Distance slider resizes it, and covered by another desktop's content); `tests/native_spike/test_roll_level.py` (hits through resize, move and roll; content, the edge band, corners and pills always first, checked on dense grids over every bar; tight layouts; the middle segment, its cancelled press and Super; only what the dot is on lit; R levelling). Not run in an isolated VM and not tried in the glasses: whether XRLinuxDriver's frame is gravity-level on the Luma Ultra (the tilt assumes its z axis is up), how the bar and the thin caps read on the OLED panels next to the border, and how the click and drag feel are unverified. The `roll` frame's `icons` and `side` changed, so install the renderer and the Python supervisor together. See [Level bar and corner caps](#level-bar-and-corner-caps). |
| Maximized desktops sit level (the maximized desktop's levelling roll fades to 0 with the zoom and comes back with it, in follow and locked, drawn only: the supervisor's roll, the `roll` frame and the saved layout never change. See [Maximized and the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)) | Offline tests only: ctest `roll_level` (the fade through the real `ViewAnimator`, a cut between maximized desktops, the overview back bit for bit), `focus_center` (level and square to the view in locked and in follow, head tipped, Ctrl+Alt+V across them); `tests/native_spike/test_maximize_level.py`, `test_directional_switch.py`, `test_layout_reset.py` (no roll changes or is sent levelled, the saved layout and the backup keep the overview's, no rotation works while maximized, a push goes the way you see it, a Place tab change waits for the overview). Not run in an isolated VM and not tried in the glasses: how the desktop looks levelling as it grows, and a held change gliding home as it shrinks. Steps L1 to L10 in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Monitor scale from the glass pill (the open panel's Scale dropdown under its sliders, in Material Design 3: an exposed dropdown and its menu, in the buttons' 18 px font; 100, 125, 150, 175, 200, 250 or 300% (250 and 300 since 2026-11-03), only the steps that divide the desktop's resolution exactly; a pick reshapes that XR desktop's Hyprland output scale live through the broker, like Ultrawide, and the profile keeps it for the next start; `desktops.SCALE`, `ViewControl.scale_box` / `scale_pick`) | Offline tests only: `tests/native_spike/test_scale_dropdown.py` (steps, plan, payload, the pick end to end through the fake broker into the saved profile, pointer hits), ctest `glass` (layout, the box, its indicator and the list in M3 roles), `material` (the drop-down arrows), `glass_contract` (box and list geometry, the steps), `placement_menu_ipc` (the `scale` / `scales` / `picker` fixture cases), the panel's `tst_model.qml` (its desktop editor offers the same steps). Not run in an isolated VM or in the glasses: how the list reads on the OLED panels (seven 20 px items over the sliders), how windows re-lay out after a scale change and how 250% / 300% look are unverified. The panel grew one row (292 px tall). The broker's bound (`spike/ownership/registry.cpp` validDesktop) now takes 2.5 and 3, so the ownership plugin, renderer, supervisor, controller and panel must be installed together. |
| Live desktops from the glass pill (Ultrawide, Add new, Remove; 1..4 desktops; `tools/native_spike/desktops.py`, the renderer's `desktops` control op, the broker's `reshape` op) | Offline tests only: `tests/native_spike/test_desktops.py` (plans, journaled broker steps with the real journal and the C++ request validator, the supervisor loop, pointer hits, profiles), ctest `ipc`, `glass`, `glass_contract`, `capture`, `ownership`. Not run in an isolated VM or in the glasses: whether Hyprland keeps a capture session through a reshape (the renderer restarts it if not), the new panel row's look, and the guard swap are unverified. The ownership plugin, renderer, supervisor, controller and panel must be installed together. |
| Maximize on every desktop of a four-desktop run (until 2026-11-03 XR-4 could not be maximized: the glasses showed XR-3 while the keyboard and mouse went to XR-4, and the overview highlight stayed on XR-3, from a clamp in `ViewAnimator::set_target` left from when a run had three desktops) | Offline tests only: ctest `capture_e2e_maximize_*` (the real renderer with four captured outputs, the stamp in the middle of the view naming the desktop shown), `view`, `ipc`, `tests/native_spike/test_view.py`. Not run in an isolated VM or in the glasses. Steps X1–X10 in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Remembered monitor setup and the Ctrl+Alt+R reset (every start restores the last session's desktops: count, resolution, scale, placement and levelling roll, with a logged fallback to three flat desktops; Ctrl+Alt+R, pressed twice within 3 s, resets a live run to three flat 1920×1080 desktops at 100% with the default spacing, animated throughout, and replaces the save, keeping the old layout as a one-step backup; see [Remembered monitor setup](#remembered-monitor-setup-and-ctrlaltr)) | Offline tests only: `tests/native_spike/test_remember_setup.py` (round trip, every fallback with its reason, older saves, atomic writes, a run's restore and its end-of-run save, roll saves, a refused create resetting the save, the backup field), `test_layout_reset.py` (the bind in `view.BINDS`, its chord against the run's and Omarchy's binds, help row, docs, the `RESET` plan, the confirm press, the live reset with the fake broker and real journal, inline and threaded: backup, spacing, ease, drop-away, window move before removal, broker steps, frames, save, toast, maximized, video, typing and failure cases), `tests/xr_controller/test_state.py` (the start scope restores or falls back, the real store's strict read), ctest `view` (`TileEase`), `ipc` (`ease`), `help_card`. Not run in an isolated VM or in the glasses: how the two eases look together, how a tile looks while its aspect changes, how long the reshapes and capture restarts take, which workspace XR-3 shows after XR-4's move, and where the toasts show are unverified. |
| Seamless workspace layout switches (master / scrolling / floating; Omarchy's toggle rewrites a layout file the config requires, so each switch is a Hyprland config reload) | Offline tests only: ctest `ownership_reload` runs the plugin's `keptThroughReload` against the pinned reload sequence (clear, parse, `ensureMonitorStatus`): during a run a reload keeps the XR-n rules and the rule in effect for every output the config does not name (the glasses' session mode), so it modesets nothing, and the config still wins wherever it names an output. The plugin applies it by hooking `CMonitorRuleManager::clear()`; that hook has not been loaded in an isolated VM or in the glasses. Takes effect only with the new `libxr-ownership.so` installed. |
| Scroll before switch (maximized on a workspace in Hyprland's scrolling layout, a push at the desktop's left or right edge scrolls its columns into view first, one at a time, and switches to the desktop placed that way only once no tiled window is left past that edge and the push has rested; `tools/native_spike/columns.py`) | Offline tests only: `tests/native_spike/test_scroll_before_switch.py` (the end-of-columns test against fake `j/monitors`, `j/workspaces` and `j/clients`; scroll, rest and switch timing; master, dwindle, floating, fullscreen and the up and down edges compared sample for sample with the push as it was; a model of the desktop's `scroll-edge.py` scrolling while the run yields; the generated Lua against a stub `hl`; the sampler thread). Not run in a live compositor or tried in the glasses: whether `j/clients` shows the new column positions right after the focus (assumed; if not, the next read 0.1 s later still finds them), how the 0.15 s dwell, the 0.35 s step and the 0.6 s rest feel, and the run alongside the live `scroll-edge.py` are unverified. |
| Material Design 3 look (the pills, the placement panel, the level bars, the corner caps, the pop-ups and the help and video cards in one M3 dark scheme seeded by the Omarchy theme colour, every role under the 191/255 OLED cap; see [The look](#moving-desktops-in-the-overview-the-pill-under-the-desktop)) | Offline tests only: ctest `material` (the palette for every shipped theme: M3 tones, hue, contrast, the cap; tokens; icons), `glass`, `glass_theme`, `glass_render` (the new shader against its CPU mirror on a GLES context), `hud`, `help_card`, `roll_level`, `video`, `video_render`, `gl_frame`. Not run in an isolated VM and not tried in the glasses: how the tonal surfaces, the 8% / 16% state layers, the elevation shadows and the dimmed roles read on the OLED panels is unverified. |
| Key bind panel (Ctrl+Alt+K: every run key listed with its keys, changed live in the glasses, saved in `keybinds.json`; conflicts with each other and with Hyprland / Omarchy binds found on load and on every change, refused or left off, shown on red rows and in a toast; see [Key bind panel](#key-bind-panel)) | Offline tests only: `tests/native_spike/test_keybinds.py` (chords, the saved file, `hyprctl binds` parsing, conflicts, every panel key, live rebinding through a fake compositor client, the help card, the helper, the wire, the start-up check in `managed.view_controls`), ctest `binds_panel` (fit at every scale, scrolling, the row states' M3 roles, the cap, the fade) and `ipc`, and `bind_check` (Hyprland's own Lua loader takes the panel's submap, catch-all and key subscription). Not run in an isolated VM and not tried in the glasses: Hyprland's `input.keyboard.key` event, `hl.is_key_down` and `hl.exec_cmd` from an event callback have never run on a live compositor here, so whether the keys reach the panel is unverified (see the failsafes there). |
| Monitor resolution from the glass pill (the open panel's Resolution dropdown, above Scale, in the same Material Design 3 parts and 18 px font; its menu lists 16:9 1280x720, 1600x900, 1920x1080, 2560x1440, 3840x2160, 16:10 1680x1050, 1920x1200, 2560x1600 and ultrawide 2560x1080, 3440x1440, 3840x1080, 5120x1440 in three columns, the current one marked; a pick resizes that XR desktop's own Hyprland output live and in place through the broker's `reshape`, like Ultrawide: no other output modeset, no XR restart, its windows stay on it, its placement stays; a new shape eases the tile's width and its neighbours' shift (also for Ultrawide); a desktop that shrinks pulls its floating windows back inside it (also for Ultrawide off and a larger Scale); a scale that no longer divides the size becomes the nearest one that does, and the Scale list greys out the rest; the profile keeps the size for the next start; `desktops.RESOLUTION`, `geometry.with_resolution`, `ViewControl.resolution_box` / `resolution_pick`, `fit_windows.py`, `SizeEase`; see [Live resolution changes](#live-resolution-changes-the-pills-resolution-dropdown)) | Offline tests only: `tests/native_spike/test_resolution_dropdown.py` (the list against the renderer's, the nearest-scale rule, plans, payload, the pick end to end through the fake broker into the saved profile with only `reshape` and its read-back on the wire, the C++ request validator for every size, pointer hits), `tests/native_spike/test_fit_windows.py` (which windows move and where, what counts as shrinking, the worker's order and failures, the generated Lua run in a real interpreter against a stand-in), ctest `view` (`SizeEase`: same smoothstep and time as zoom, neighbours between the two layouts), `glass` (layout, the box, the three-column menu in M3 roles, every label fitting), `glass_contract` (box, menu and item geometry, the sizes and groups), `placement_menu_ipc` (the `resolution` / `resolution_picker` fixture cases), `capture` (`ResizeSettle`) and `capture_e2e_churn_resize_*` (a resized capture waits before re-creating its buffers and never goes blank, and on the GPU paths its new shape eases; a reload's bounce does neither). Not run in an isolated VM or in the glasses: whether the swap is free of any visible glitch on the real compositor (wallpaper, bar and windows redrawing within the 100 ms the renderer waits), how windows re-lay out, whether Hyprland carries out the floating-window move and resize on a shown or hidden workspace as restore.py's use of them suggests, how the ease looks (the new frame is squeezed to the old shape as it starts), and how the three-column menu reads on the OLED panels are unverified. The panel grew another row (326 px tall). No broker change: every listed size was already inside `validDesktop`; the renderer, supervisor and controller must be installed together. |
| Window memory (`remember-window-placement`, 2026-11-03: when XR stops every window on an XR desktop is remembered, with its desktop, workspace, tile order, scrolling column, floating geometry and fullscreen state, in `window-memory.json` beside `profiles.json`; the next start puts each still-open window back after the remembered monitors exist; kept current through Super drags, parked and brought back when a removed desktop is added again in the run, untouched by Ctrl+Alt+R) | Offline tests only: `tests/native_spike/test_window_memory.py`, `test_window_memory_lua.py` (the generated evals in a real Lua interpreter, against a stand-in for Hyprland's API written from the 0.56.2 source) and `test_window_memory_run.py` (the real `_run_owned`, `ViewControl` and `DesktopManager` with a fake broker and compositor). `bind_check` loads the evals and each dispatcher call in the real `Hyprland --verify-config`. Not run against a live Hyprland, in an isolated VM or in the glasses. Glasses steps W1–W19 are in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Not built yet | Head-direction focus. |

## How it fits together

- `spike/` is the C++20 renderer (`xr-native-spike`), a layer-shell client that draws the captured desktops in the glasses. `spike/ownership/` is a small Hyprland plugin (`libxr-ownership.so`). It creates and removes the managed `XR-n` outputs through object-bound handles and epochs, never by name alone.
- `tools/native_spike/` holds the Python supervisor: consent, durable journal, managed output lifecycle, recovery, view controls and the tracking driver wrapper.
- `tools/xr_controller/` is a `systemd --user` controller service. It owns glasses detection, per-glasses profiles, consent and the run lifecycle. Each run is a separate transient unit, so a shell restart cannot kill it.
- `omarchy-plugin/cody.viture-workspace/` is the Quickshell panel and bar widget. It is a thin client of the controller socket.
- `tools/xr_probe/` is the original read-only host diagnostic (see below).

Head tracking comes from XRLinuxDriver (`/usr/bin/xrDriver`), run in an unprivileged network namespace in `external_only` mode so it cannot reach the network or move the mouse.

## Build and install

The ownership plugin is ABI-pinned to the installed Hyprland and Aquamarine. Rebuild it after either one updates.

```sh
cmake -S spike -B build/release -DCMAKE_BUILD_TYPE=Release \
  -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/release -j
ctest --test-dir build/release --output-on-failure

# Faster rebuilds: add -G Ninja on first configure (wipe the dir to switch
# generator). ccache and mold (else lld) are picked up automatically when
# installed (`pacman -S ninja ccache mold`); -DXR_FAST_BUILD_TOOLS=OFF opts out.
# One ccache shared by all worktrees: ~/.config/ccache/ccache.conf with
#   max_size = 20G, base_dir = ~/.t3/worktrees (absolute), hash_dir = false

# Pin the renderer and plugin by hash under ~/.local/share/viture-workspace/bin/
# and write the user unit. The controller only launches pinned copies.
python -m tools.xr_controller install --build build/release

ln -s "$PWD/omarchy-plugin/cody.viture-workspace" ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins

# App icon and launcher entry
install -Dm644 assets/icon.svg ~/.local/share/icons/hicolor/scalable/apps/viture-workspace.svg
install -Dm644 packaging/viture-workspace.desktop ~/.local/share/applications/
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

The panel is a window of the Omarchy shell, so Hyprland reports its class as `org.quickshell`, the same as every other shell window. Quickshell 0.3.2 sets the Wayland app_id once per process (`QS_APP_ID`), and its windows have no per-window app_id, so the panel cannot get its own class. The desktop entry works around that: its `StartupWMClass=XR Workspace` matches the panel's window title. Widgets that look shell-hosted windows up by title, such as the Spaces bar widget (`tornikegomareli.spaces`, checked with 1.6.0), show the XR Workspace icon without any change to their code. Anything that maps windows to icons by app_id alone, like Omarchy's dock, still shows the Quickshell icon. The bar widget draws `icon-symbolic.svg`, a one-colour copy of the mark, tinted by state.

To switch an existing install to another checkout or build, do it with no XR session running. First back up `~/.local/share/viture-workspace/installed.json`, `~/.config/systemd/user/viture-workspace-controller.service` and the panel symlink's target. Run `install` from the checkout the controller should use; to keep an accepted plugin binary, stage the new renderer next to the currently pinned `libxr-ownership.so`. Point the panel symlink at that checkout, restart the controller, then **run `omarchy-restart-shell`**. `rescanPlugins` does not reload panel QML the shell has already loaded, so without a shell restart the panel keeps its old UI. To roll back, restore the two backed-up files, run `systemctl --user daemon-reload`, point the symlink back, restart the controller and run `omarchy-restart-shell` again.

`install` writes `viture-workspace-controller.service` but does not enable it. Start it with `systemctl --user start viture-workspace-controller.service`, or run `python -m tools.xr_controller serve` in the foreground. The service never starts an XR session by itself.

## Sessions, keybinds and placement (full detail)

Open the XR Workspace panel with the glasses connected and press **Start XR**, then **Start** in the box that lists the run's scope. Before a session the panel shows only the logo, Start and a one-line status (`Launcher.qml`); the few settings a run starts with (each desktop's startup shape, resolution and scale, which the glass pill can also change during a run, *Head tracking*, the Luma *120 Hz mode switch*, the fallback output when there is more than one) sit under a collapsed **Startup options**. When `begin_start` would refuse because the fallback is ambiguous (`pick_fallback` returns `choose`), the *Return windows to* choice shows next to Start, which stays disabled until a monitor is picked; with no physical fallback at all Start is disabled and says why. That confirmation click is the consent for one run, including any temporary display-mode switch (for example Luma 1920×1200@120). **Stop** returns every window to the physical fallback output. **Recover** appears if an earlier run left resources behind.

At start, a panel run moves the fallback output's windows onto the XR desktops, once, after the desktops are created and before the renderer starts (`tools/native_spike/adopt.py`). The fallback's occupied regular workspaces go, in id order, to XR-1, XR-2, ... one each, whole, so their windows stay together in their layout. A gap in the numbers skips no desktop. A run starts with the last session's desktops (`profiles.load_setup`, else three: see [Remembered monitor setup](#remembered-monitor-setup-and-ctrlaltr)) and never adds any for adoption; with more occupied workspaces than desktops, the rest go to the last desktop, each still its own workspace. A moved workspace joins its desktop's Omarchy workspace block under the same slot (as Omarchy's own move-workspace-to-monitor does), so Omarchy's reconcile on a pill's Add or a theme switch leaves it there. Special (scratchpad) workspaces, pinned windows and the panel itself (`org.quickshell`, title "XR Workspace", or "VITURE Workspace" from a shell still running the panel QML from before the rename) stay on the physical monitor. A workspace holding one of those stays with it, and its other windows move one by one, in layout order, onto the workspace their desktop shows. All the moves are one generated Lua eval, so one compositor tick, with no intermediate layout drawn. If it fails, the run goes on and a notification says so. The CLI test-pattern run (no capture) moves nothing.

Just before adopting, the run snapshots the fallback's arrangement (`tools/native_spike/restore.py`): its regular workspaces by id, every window adoption moves (workspace, floating, position and size, fullscreen), the workspace it shows and the focused window and monitor. The snapshot is kept in memory and in `$XDG_STATE_HOME/viture-workspace/layouts/<run_id>.json` (0600), so a crash recovery (the CLI `recover`, the panel's Recover) has it too; it is removed once recovery completes. Stop and Recover put it back first, while the XR outputs still exist, in one generated Lua eval (one compositor tick, every value validated): each workspace that moved whole is found by its windows, wherever adoption, Omarchy's block renumbering or a later reconcile left it, and takes its own id again (an empty workspace holding that id steps aside), so its layout tree (order, split orientation, ratios) comes back with it; every window moved one by one, or moved during the run, returns to its own workspace; all of them return to the fallback; tiled windows are swapped back into their old slots, floating windows get their old size and position, fullscreen and floating state are reapplied; the fallback shows the workspace it showed and focus returns. Windows opened during the run stay on the workspace they are on, tiled after the restored ones; a workspace opened on an XR desktop comes to the fallback under the same slot of its block when that is free, else the first free one. Hyprland reports a fullscreen window at the monitor origin rather than its tiled slot, so a window fullscreen at start keeps the slot it has and goes fullscreen again. Recovery then evacuates and removes the XR outputs as before, which also catches anything the restore could not place; a restore that fails says so and recovery still runs.

Other Hyprland plugins may stay loaded during a run. Omarchy keeps `hyprbars` loaded all session for its titlebars workspace mode. Start and recovery refuse only if this project's ownership plugin (`xr-ownership`) is already loaded. If an XR desktop's workspace is in Omarchy's floating mode, its windows get title bars inside the glasses, as they would on any monitor. That changes nothing about what the run captures or controls.

Head tracking must be running and calibrated before the renderer starts. A panel start with *Head tracking* ticked
does that itself; see [Head tracking from the command line](#head-tracking-from-the-command-line).

**VR video** (offline tests only, not yet run in the glasses): during a session, **Ctrl+Alt+O** (or the panel's *VR video* section) plays a 360° or 180° video, mono or 3D (side-by-side, top-bottom), equirectangular or fisheye, head-tracked around you in place of the desktops. Space pauses, the arrows seek, Tab changes the format, Q opens the quality menu (texture filter, deband; saved with the view settings), Ctrl+Alt+Space recenters and Esc returns to the desktops. Opening a video closes an open placement pill; while it plays, Ctrl+Alt+L, the pills and the overview free pointer are off. The renderer decodes with libmpv (`mpv` package; without it the renderer builds without video). Design, formats, limits and performance: [vr-video.md](vr-video.md).

**Web VR video** (offline tests only): copy a stream link or a video page link, then press **Ctrl+Alt+U** in the glasses (or the panel's *Paste from browser*). Page links are resolved with yt-dlp; the run streams the video through an ffmpeg relay into the same VR video player; no download, no browser changes, renderer unchanged. Details and limits (no DRM, no cookie-gated links): [web-video.md](web-video.md).

These keybinds exist only during a run. The keys marked "pill open" are bound only while a placement pill is open (expanded into its panel), and released when it closes or the run ends; the left-button binds marked "free pointer" exist only while the overview's pointer is outside the desktops. If the run dies with a pill open, the first of those keys pressed afterwards releases them all; if it dies with the free pointer out, the first click releases the left button and shows the cursor again. A run from an older build that died in the old placement menu's mouse mode is cleaned up the same way: its left-button bind still calls the bind helper (`xr_control.py` keeps the `menu_mouse_press` / `menu_mouse_release` op names for that cleanup only), which removes it.

| Keys | When | Action |
| --- | --- | --- |
| Ctrl+Alt+Up / Down | run | Maximize the selected desktop (follow) / back to the overview (anchor) |
| Ctrl+Alt+Left / Right | run | Previous / next desktop; an open placement panel goes with the selection |
| Ctrl+Alt+[ / ] | run | Less / more space between desktops |
| Ctrl+Alt+Space | run | Recenter |
| Ctrl+Alt+V | run | Maximized: follow / locked (0DoF). In the overview (always anchor, 3DoF) nothing changes; a toast says why |
| Ctrl+Alt+P | run | Toggle pose prediction (on by default) |
| Ctrl+Alt+H | run | Show the help card in the glasses |
| Ctrl+Alt+K | run | The key bind panel: every run key, changed live (see [Key bind panel](#key-bind-panel)) |
| Up / Down, PgUp / PgDn, Home / End | key bind panel open | Pick a bind |
| Enter | key bind panel open | Change the picked bind: the next key combination becomes it (Esc cancels) |
| R / Shift+R twice | key bind panel open | The picked bind / every bind back to its default |
| Esc, or the panel's own keys | key bind panel open | Close the panel (Esc three times always gives the keyboard back) |
| Ctrl+Alt+PgUp / PgDn | run | Previous / next help card page (wraps round; nothing while the card is closed) |
| Ctrl+Alt+T | run, both modes | Typing mode on / off (see [Typing mode](#typing-mode)) |
| Ctrl+Alt+L | run | Open the selected desktop's placement pill (back to the overview first if maximized), or close the open one |
| Ctrl+Alt+R, twice within 3 s | run | Reset every desktop to the default three flat 1920×1080 desktops at 100% with the default spacing, animated, and replace the saved setup with them, keeping the old layout as a one-step backup (see [Remembered monitor setup](#remembered-monitor-setup-and-ctrlaltr)) |
| Ctrl+Alt+G | run | Send the focused window to the glasses: the desktop selected in the view |
| Ctrl+Alt+1 / 2 / 3 | run | Send the focused window to XR-1 / XR-2 / XR-3 |
| Ctrl+Alt+B | run | Send the focused window back from the glasses to the physical monitor |
| Ctrl+Alt+D | run | Privacy mode: main physical display off / on (DPMS), so only the glasses show content (see [Privacy mode](#privacy-mode-main-display-off)) |
| Ctrl+Alt+E | run | The background behind the desktops: None / the last one used, with a fade (see [The background](#the-background)) |
| Tab / Shift+Tab | pill open | Open the next / previous desktop's pill instead (only one is open) |
| Up / Down | pill open | Previous / next field: left/right, down/up, distance, curvature |
| Left / Right | pill open | Decrease / increase the field by a small step (Shift for a big step). Left/right: 0.2°, Shift 15°, and it carries on past 180° to the other side. Down/up: 0.2°, Shift 10°, and it stops at ±90°. Not stopped by other desktops |
| R | pill open | Reset the desktop: flat in its slot, and level (its roll, see [Level bar and corner caps](#level-bar-and-corner-caps)) |
| Escape | pill open | Close the pill |
| Mouse past a desktop's edge | overview | Free pointer: the cursor hides and a dim dot moves through the space around the desktops |
| Mouse onto any desktop | overview, free pointer onto a desktop's content | That desktop takes the real cursor under the dot and becomes the selected one; its monitor gets Hyprland's focus. Every button, the wheel, drags and the keyboard are its windows' (since 2026-11-03) |
| Left click a pill | free pointer on a pill, or on the open panel's top strip | Open that desktop's panel (closing any other), or close it if it is the open one |
| Left-drag a pill | free pointer on a pill, or on the open panel's top strip (in anchor mode anywhere round you, see [Moving desktops](#moving-desktops-in-the-overview-the-pill-under-the-desktop)) | Turn that desktop round you, like a title bar, the grabbed point staying under the dot; in anchor mode all the way round you. It never overlaps another desktop. Nothing moves until the pointer is more than 4 px from the press, so a click never nudges it |
| Left click / drag a slider | free pointer on a slider of the open panel | Select that field and snap it to the pointer; a drag moves it live; release saves |
| Left click outside the open panel | free pointer | Close the panel (saved), then the click does what it would anyway: open or drag another pill, or nothing in empty space. A click on a desktop is not a click outside: the panel goes to that desktop |
| Left-drag a desktop edge or corner | free pointer on a resize handle, just outside the desktop ([Handles and content](#handles-and-content)); the dot shows two arrows | Resize that desktop about its centre, keeping its shape: outward is bigger, inward is smaller. An edge follows the dot along its normal (new 2026-11-03), a corner along its diagonal. The open desktop's own edges and corners do not close its panel. A click there does nothing |
| Left click a desktop | over its content the cursor is the real one: every click is the window's, and an open pill comes to that desktop with its keys off the keyboard. Only next to the open help card is the dot still free there | There, select it on the release and hand it the real cursor at that spot (its monitor focused); this click reaches no window. No click maximizes (since 2026-11-03; Ctrl+Alt+Up does) |
| Left press-hold (`HOLD_S` 0.35 s) or drag (> `WINDOW_DRAG_PX` 12 canvas px) on a desktop, then release on another desktop | **Off since 2026-11-03** (`OverviewPointer.window_drag`): a press, hold or drag on a desktop is its window's. With it on: holding still over a desktop | Move the window under the press onto the workspace that desktop shows (`send.WindowSender.drop`, Hyprland move with `follow = false`): no zoom, no selection change. A ghost outline follows the dot and the target desktop is framed (`overview_pointer` frame `drag`). Released anywhere else: nothing moves |
| Super + left-drag | a window, over a desktop | Hyprland's own window move (Omarchy's bind), across desktops too: [Super drags](#super-drags). Handles do nothing while it lasts |
| Super + right-drag | a window, over a desktop | Hyprland's own window resize. The mouse stays on that desktop until you let go |
| Left click a help card arrow or its "2 / 3" | free pointer on the open help card | Previous page (left arrow) or next page (right arrow or the page number). Elsewhere on the card a click does nothing, and it never closes an open pill |

Changes made with an open pill apply immediately and are saved to the glasses profile half a second after the last change, at once when a slider is released, and as soon as the pill closes. The pills, the placement panel and their wiring are covered by offline tests only; nothing about them has been tried in the glasses.

Each desktop also has a placement in the glasses: left/right and down/up (a turn round you, in degrees: see [Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)), distance (size in space) and horizontal curvature. Every desktop always faces you, upright, so there is no setting for which way it faces (the yaw, pitch and roll settings were removed on 2026-11-02). Set it in the desktop editor before starting, or under **Place** while a session runs; live changes apply when you release a slider. During a run the resolution and the scale change from the pill's Resolution and Scale dropdowns (and its Ultrawide toggle). Zooming in and back out never moves a desktop, in every viewing mode (anchor, locked and follow) and however far it was dragged: the zoomed desktop keeps the direction round you it was dragged to, comes to distance 100 there so it fills the view, keeps its curvature, sits level and is drawn on top; every other desktop stays exactly where and as large as it was in the overview. Nothing snaps straight ahead or back to its default place (`zoomed_placements`, since 2026-11-02). Ctrl+Alt+Left / Right in the overview just move the selection. The viewing mode goes with the view (**Maximized and the overview** in [Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop), since 2026-11-03): the overview is always anchor and a maximized desktop follow or locked, where every switch (keys and a cursor push) is a `cut`, straight onto the desktop. Zoomed in, in anchor mode (no longer reachable), they animate the switch: the view zooms out of the current desktop to the overview, turns to look at the target, then zooms into it where it was dragged (about 0.7 s; `ViewAnimator`, since 2026-11-02). The turn is the camera's only: no placement changes, and once the switch lands the view is not turned. A free-pointer click on a desktop is the main way to pick one. Overlapping desktops are drawn farthest first and are not split where they intersect. The CPU fallback renderer, used when GPU composition is unavailable, ignores placements.

Settings, including placements, are saved per glasses model.

### Remembered monitor setup and Ctrl+Alt+R

Offline tests only (`tests/native_spike/test_remember_setup.py`, `test_layout_reset.py`, `tests/xr_controller/test_state.py`);
not run in an isolated VM or tried in the glasses. Since 2026-11-03 (`remember-layout-reset-bind`, which undid
`xr-default-three-monitors`' fresh three-flat start, `17b6238`) every XR start restores the last session's monitor setup
(`profiles.SETUP_KEYS`): the desktops (count 1..4, each one's resolution and scale), each desktop's placement (left/right,
down/up, distance, curvature) and its levelling roll (the overview's level bar). They live in the glasses model's
profile in `~/.config/viture-workspace/profiles.json` (schema 2, mode 0600): `desktops`, `placements` and `rolls` (one
per desktop, hundredths of a degree, ±9000, `geometry.MAX_ROLL`).

Written on every change and at the end, always atomically (`save_profile`: a temp file in the same directory, fsync,
`os.replace`; a failed write leaves the old file whole):

- a desktop change from a pill (Add, Remove, Ultrawide, Scale) or Ctrl+Alt+R: `DesktopManager.save` writes the desktops,
  their placements and rolls (by desktop name, so a removed desktop's roll goes with it);
- a placement change (pill drag, corner resize, slider, panel keys, the panel's **Place**) and a level bar's click or
  drag: `ViewControl._save_placements`, 0.5 s after the last change and at once on release or when a pill closes (rolls
  are watched on the supervisor loop, `_watch_rolls`, so `free_pointer.py` needed no change);
- the run's end: `managed.view_controls` calls `ViewControl.save_setup`, which writes the broker's live desktops with the
  placements and rolls as they are (a reset still running then saves the default it was heading for).

Read strictly at start, whole or not at all (`profiles.load_setup` / `setup_from`; `load_profile`, used by the panel,
still cleans field by field). The controller's `begin_start` puts the restored desktops into the consent scope
(`Controller._startup_desktops`); the run then restores the placements and rolls when the saved desktops are exactly the
run's (`managed._restored`). Anything else starts with the default (`profiles.default_setup`: three flat 1920×1080
desktops at 100%, level) and logs why on stderr (the controller's and the run's journals):

- `start: no saved monitor setup yet; three flat desktops`: no file, no entry for this model, or no desktops in it;
- `start: saved monitor setup not used: <reason>; three flat desktops instead`: the file could not be read, is not
  JSON or not schema 2, or a desktop, placement or roll is invalid (count, bounds, odd size, a scale that does not divide
  the resolution);
- `run: the saved placements are for other desktops (...); flat placements`: the profile changed after the start's
  consent, or a CLI run;
- `the saved monitor setup could not be created (<broker verdict>); the next start uses three flat desktops. It was:
  <json>`: the broker refused a restored setup's outputs or did not read them back (`managed.UNCREATABLE`: `creation
  incomplete`, `missing readback`, `actual geometry/workspace mismatch`, `unusable output`). That run fails and recovers
  as before; `profiles.discard_unusable` replaces the save with the default (unless the profile changed meanwhile) so
  the next start does not fail the same way. A transport error or a lost broker session keeps the save.

Two older shapes still restore: placements saved with the removed yaw / pitch / roll (`geometry.legacy_placement`) and
profiles from before rolls were kept (level). Adoption never adds desktops for extra occupied workspaces (they go to the
last desktop). Not part of the setup: spacing, prediction and the VR video quality (view settings, saved as before), and
the maximized follow / locked choice, which is never saved, so every start maximizes in follow.

**Ctrl+Alt+R** (`view.RESET_OP` = `layout_reset`, a row of `view.BINDS`, the table every run bind and the key bind
panel come from) resets a live run to the default (`ViewControl.layout_reset`). The first press only shows the toast
`Press Ctrl+Alt+R again to reset all monitors`; a second within `RESET_ARM_S` (3 s) resets, and otherwise nothing happens
(a later press asks again). Then:

1. An open pill collapses (saved) and a maximized desktop un-maximizes to the overview; `maximized_mode` is untouched.
2. The saved setup is replaced by the default at once (`PlacementStore.reset`). The layout it replaces, live desktops,
   placements, rolls and spacing, goes into the profile's `previous_setup`, a one-step backup that the next real reset
   overwrites. Resetting a layout that already is the default leaves it, so a repeated reset cannot wipe it. Nothing reads
   it back yet. The spacing returns to its default (`DEFAULT_SPACING`), which the renderer eases on its own 0.22 s curve.
3. Every desktop eases to flat and level over `RESET_ANIM_S` (0.45 s, smootherstep; the turn round you goes the short
   way): the supervisor loop sends a `placement` frame per tick and the pointer's `roll` frame carries the rolls, with
   the renderer's own 35 ms placement easing smoothing between ticks. A desktop the plan will remove eases to
   `RESET_LEAVING_PLACEMENT` instead, straight down at half size, out of the view, so it leaves rather than vanishes.
4. One `desktops.RESET` plan through the pill buttons' path (`_start_change`: guard paused, the renderer told first about
   a removal, broker steps on the worker, guard replaced, profile saved): the three lowest-numbered desktops stay and
   are reshaped to 1920×1080 at 100% (only those whose pixel size changes restart their capture), and missing ones are
   added with the lowest free numbers. A fourth is removed, and just before that its windows go to the last desktop that
   stays (`DesktopManager.move_windows`, `adopt.adopt`: whole workspaces, each still its own there, with Omarchy's block
   renumbering, so Hyprland may leave XR-3 showing one of them). A pinned window stays, so the removal is refused as with
   Remove; anything else that could not move is evacuated to the fallback by the removal, the safety net of every
   removal. Already the default three: no broker step.
5. This plan's `desktops` frames carry `ease` (ipc.hpp `DesktopsPayload`), so the tiles do not jump: the overview fits
   its row to the view, so a desktop more or less, or one new aspect, changes every tile at once. `TileEase` (view.hpp)
   takes every tile from where it is drawn to the new layout along the same smootherstep over `kTileEaseMs` (0.45 s, the
   same number as `RESET_ANIM_S`, a test compares). A capture's size changes when its first new frame lands, so each such
   change starts again from where the tiles are, for as long as the last frame's `kTileEaseArmMs` (2 s) arms it. A new
   desktop grows out of the middle of its slot. A zoom drops the ease. A tile's picture stretches while its aspect
   changes. No other `desktops` frame asks for this, so a pill's Add, Remove, Ultrawide and Scale jump as before.
   The overview pointer hit-tests the final layout during the ease, and the CPU fallback renderer does not ease.
6. The live setup is saved and a toast says `All monitors reset to three flat 1920x1080 desktops at 100%.`, naming where a
   removed desktop's windows went, or why the reset stopped. A desktop a refused removal left underfoot comes back flat.

It is ignored, with a toast, while a VR video plays or a desktop change is running (neither counts as the first press),
and ignored while one reset is already running and in typing mode (its bind is released there). Ctrl+Alt+R is free in
Omarchy (its Ctrl+Alt binds are Delete, Tab and Shift+Tab; its reminder keys Super+Ctrl+R, Super+Ctrl+Alt+R and
Super+Shift+Ctrl+R are other chords) and in the run's binds; `bind_check` checks the real config. The help card shows it
on the recenter row (`Ctrl+Alt+Space / R`: both put things back) so the card keeps 14 rows at its roomiest text. The
`ease` key is new on the wire, so the renderer and the Python side must come from the same build.

### Window memory

Offline tests only (see the status table). `tools/native_spike/window_memory.py` keeps, for each glasses model, where every
window on the XR desktops is, and the next start puts each still-open window back. Its docstring has the full rules. No
new bind, no renderer change, nothing new on the wire: installing it needs no new renderer pin.

**The file.** `~/.config/viture-workspace/window-memory.json`, beside `profiles.json`, schema 1, mode 0600, one entry per
glasses model, always replaced atomically (temp file in the same directory, fsync, `os.replace`; a failed write leaves the
old file whole). It is a separate file so nothing that rewrites a profile can touch it. It holds window titles as Hyprland
reports them, cut to 256 characters, and at most 256 windows per model. A file that is corrupt, too large or invalid in any
field reads as nothing remembered, and the windows go through adoption as they would without it.

| Key | What it holds |
| --- | --- |
| `instance` | The Hyprland instance signature it was saved under. An address only means something within one instance. |
| `desktops` | Per XR output: `width`, `height`, `scale`, the workspace it showed (`active`), and its `placement` and levelling `roll` round you. |
| `windows` | Per window: `address`, `class`, `title`, `initial_title`, `pid`, `output`, `index` (position among the run's desktops), `workspace`, `layout` (the workspace's `tiledLayout`), `floating`, `at` (relative to the desktop), `size`, `fullscreen`, `client`, `order`, `column`, `parked`, `landing`. |

Only the desktops' regular workspaces count: never a special workspace, a pinned window, the XR Workspace panel or an
unmapped window (`adopt.stays`), the rule adoption and recovery keep. A run that ends with nothing on the glasses leaves the
memory as it was.

**Keeping it current.** `ViewControl.on_events` marks the memory dirty on `openwindow`, `closewindow`, `movewindow`,
`movewindowv2`, `changefloatingmode`, `fullscreen`, `workspace`, `workspacev2`, `moveworkspace`, `moveworkspacev2` and
`renameworkspace`, and on a Super drag's release (a drag inside a workspace sends no event). `CAPTURE_DELAY_S` (1 s) later a
worker reads `j/clients`, `j/workspaces` and `j/monitors`, never on the supervisor loop and never during a desktop change, and
saves only if something changed. Titles and focus do not mark it: they change all the time, and `view_controls` takes a last
capture at the run's end, after the sender closes and before recovery. So a resize or swap made with no event reaches the
file at Stop; after a crash the file has the last marked state.

**A start** (`managed._run_owned`): the pre-XR snapshot (`restore.take`), then `WindowTracker.place`, then adoption as ever,
then `WindowTracker.finish`. By then the broker has created the remembered desktops. Which windows: only windows that are open
now, and only those adoption would take, on the fallback's regular workspaces and not staying (`adopt.occupied`). Nothing is
ever launched or recreated: a remembered window that has closed has nothing to match, and its record goes with the next
capture. Which is which (`match`, each pass over what the last left, nothing used twice): same address, only in the same
Hyprland instance and with the same class; class and title; class, initial title and pid; class and initial title; class and
pid; class alone. Where: the desktop at the same position, because `hyprland.plan_outputs` numbers a start's desktops from
XR-1, so after removing XR-2 the old XR-3 is the new XR-2. Past the last one, the last one (XR-4 to XR-3), each workspace
still its own there: `home` and `pick` take the same slot of that desktop's block if free, else the first free one.

How it moves (`plan`, pure; `moves_lua`, `settle_lua`, generated):

1. A physical workspace holding exactly what one remembered workspace held moves whole and takes its id. Stop moved it back
   whole, so this is the normal case. Tile tree, column widths and split ratios come with it, and nothing is rebuilt.
2. Anything else moves window by window (`follow=false`) onto the remembered id, made on its desktop if needed. Ids are kept
   because Omarchy's blocks follow a monitor's identity (`monitor-workspaces.lua`, so XR-1 keeps its block across runs) and its
   saved layouts are keyed by id. Without Omarchy a free id is used as is; one held on another monitor gives way to the
   workspace the desktop shows.
3. Adoption takes what is left, so unmatched windows behave as without this. With nothing remembered, a run does exactly what
   it did.
4. Settling: the workspace's layout if it differs (`hl.workspace_rule`, runtime only), floating state and geometry (kept inside
   a desktop that changed size, as `fit_windows` does), tile order, fullscreen, the workspace each desktop showed, then focus
   and the cursor as they were.

Tile order goes by slots: `window.swap` exchanges two windows in place whatever the layout (scrolling: `nodeA->target = b`, so
a column keeps its width), so the remembered windows are sorted into the live slots, left to right then top to bottom, with
windows the memory does not know after them. Scrolling then checks the columns. Only if they differ: `expel` until every
window is alone, the slots again, `consume` to form the stacks. Widths go by `colresize`, a share scaled from the live one,
because `window.resize` clamps a column to the view and would shrink one scrolled off it
(`CScrollingAlgorithm::resizeTarget`). Master and dwindle tiles go by `window.resize`, only on a desktop of the same logical
size. `consume`, `expel` and `colresize` act on the focused window of the focused monitor's shown workspace
(`CLayoutManager::layoutMsg`), so that step, and only that step, moves focus, and each eval puts the cursor back. Anything that
already fits is not dispatched, so a whole workspace moves with no focus change at all. `scrolling:direction` left, up or down
keeps the order only: columns are read left to right from `at`, because the column data (`w.layout.column`) is in Lua and `/eval`
returns nothing. Every eval is under the client's 4096-byte limit (`_batches`), a group of windows stays in one eval while it
fits, and a workspace with more tiled windows than one eval holds (about 50) gets the first ones in order.

A window-by-window rebuild cannot bring back the heights inside a stacked column, a dwindle tree's exact shape or tab groups
(their members move on their own). A workspace that moves whole keeps all three.

**A desktop that goes and comes back.** `DesktopManager._run` calls `leaving` before a removal: the tracker captures and parks
that desktop's records with where the windows are going (`landing`: XR-3 for Ctrl+Alt+R, the fallback for Remove). A parked
record stays as it is while its window sits on `landing`. If the window moves anywhere else it was moved on purpose and is
remembered there; if it closes it is forgotten. When the same name is added in the run, `set_names` has the worker bring those
very windows back (`exact`: by address only, so a closed window can never pull in another), normally as a whole workspace.
A later start applies the XR-4 to XR-3 rule to a parked window whose desktop is missing, and the record stays parked for
that run too.

**Other monitors.** A window on DP-2 or any regular monitor other than the fallback is never touched: no pass of `match` sees
it, whether it got there between sessions or during one. Stop agrees: `restore.py`'s `off` leaves a window or workspace the
user moved to such a monitor during the run as it is, under the number it has, while anything on an XR output or the fallback
still comes back. Until 2026-11-03 Stop carried it back to the fallback, where the next start's adoption would have taken it
into XR. A parked window moved to another monitor is forgotten, so re-adding its desktop leaves it alone.

**Ctrl+Alt+R and the memory.** The reset rewrites the profile only. The window memory keeps every window: a fourth desktop's
windows go to XR-3 as they always have and stay remembered for XR-4, and the three desktops that stay keep their windows. A
collision bump changes placements only, so it reaches the memory as context. Everything here fails open: a failed read or
write is one line in the run's journal (`xr-workspace: run: ...`), and a successful start logs how many windows came back.

### Typing mode

**Ctrl+Alt+T** switches between XR control (the default each run) and typing mode. Typing mode hands the keyboard back
without stopping the run: an open placement pill and the help card close, and the run's bind table
(`xr_workspace_binds`) is reinstalled with only the Ctrl+Alt+T bind in it. Every other run bind is released: the view
keys, the send-window keys, the open pill's keys, the overview free pointer (its left-button bind; the hidden cursor
comes back, and the dot and the pills hide), and a playing video's bare Space, arrow, Tab and Escape keys. Arrows, Space, Tab, Esc and other apps' Ctrl+Alt shortcuts then reach the focused window. The Super drag binds stay: they take no key, and
[Super drags](#super-drags) work in typing mode too.
Ctrl+Alt+T again reinstalls the full set. The binds use the same runtime `hl.bind` tables as the rest of the run
(`view.install_binds_lua(..., typing=True)`, `HyprlandClient.reinstall_view_binds(..., typing=...)`); no Hyprland
submap and no config file is involved. A config reload (theme switch) reinstalls whichever set matches the current mode.
Panel buttons keep working in both modes. Key-only ops that arrive late from a released bind (pill keys, send) are
ignored in typing mode.

The mode is part of the renderer's `view` payload (`typing`). Each switch shows the usual 2 s status pop-up (fade in,
1.5 s hold, fade out), drawn in the cards' dimmed colours and accent so nothing on it passes 191/255. Nothing stays
on screen in either mode. The mode is never saved. Ctrl+Alt+T is free in Omarchy, which binds Super+Ctrl+Alt+T (a
different chord), and in this project's binds; `python -m tools.native_spike.bind_check` checks it against your config.
Ctrl+Alt+P has its own help card row. Cursor follow (Ctrl+Alt+F: the view switched to the monitor the cursor crossed
onto) was removed on 2026-11-02; a `cursor_follow` saved in layout.json or a profile is ignored, and the renderer's
`view` payload still carries `cursor_follow: false` because the installed renderer's decoder requires the key.

### The background

An optional 360° scene behind the desktops and behind a VR video. It is chosen in the placement panel's **Background** dropdown
(the same in every desktop's panel) or the VR video's quality menu, and **Ctrl+Alt+E** or the switch beside the dropdown goes
between None and the last background used, with a 0.6 s fade. **Grid** is the TRON grid and **Smoke** is drifting fog. Offline
tests only: nothing about either has been tried in the glasses.

*Adding a background.* A row in `tools/native_spike/environment.py` `BACKGROUNDS` and in `spike/include/xr/environment.hpp`
`kBackgrounds` (`glass_contract` compares them), and a `scene()` in `spike/src/environment_gl.cpp`. The dropdown, the quality
menu, the wire and the saved setting follow from the tables.

*What Grid draws.* `spike/src/environment_gl.cpp`: one fullscreen triangle whose fragment shader casts the view ray into a
procedural scene (no textures, no extra framebuffer, GLES2 with `OES_standard_derivatives`). A black sky; a floor plane
1.6 m below the eye with lines every 2 m and a brighter one every 10 m, anti-aliased from the screen derivatives (lines
finer than a pixel fade to their average instead of shimmering), a soft halo for the bloom and a distance fade; a thin
bright horizon with a haze either side; a ring of ridge outlines up to about 5° high, with gaps between the ranges; and on
about one line in five a light trail at 8, 12 or 16 m/s. The animation clock wraps every 512 s, a common multiple of the
trails' periods, so the wrap never shows.

*What Smoke draws.* The same fullscreen triangle with another `scene()`. The view direction is folded four times by sines of
itself, each fold's phase running a whole number of cycles per clock loop, then read through three gyroids (a smooth periodic
field): one says where the banks of smoke are, and ridges of the two finer ones are the strands inside them. Working in direction
space gives the whole sphere with no seam or pole, and since the head only turns, no depth is lost by leaving out a raymarch. The
field is squashed in y so wisps lie along the horizon, and it thins toward straight up and down. Density ends at exactly zero,
so the air between the banks is black, at any brightness. At 100% about a quarter to three quarters of the view is exactly black
depending on where you look. The folds slide past each other, so the wisps change shape as they drift: 512 s until the same
shapes come round.

*Colour.* The Omarchy theme's: `GlCompositor::draw_environment` reads the same accent the highlight border and glow are
drawn in (`set_accent`), so a theme switch recolours them in one frame, and every lit pixel of the grid has that colour's
proportions. `environment_line_color` only scales it: a theme colour brighter than the OLED cap comes down to it, all three
channels together, and a near-black one, which would not show on the see-through panels, is lifted to 72/255.

*Brightness.* 10..200%, 100 by default, in the placement panel's slider (or Up / Down to its row, Left / Right 5%, Shift 25%).
It scales the scene before the cap, so below 100 everything dims and above it the dim parts come up while the brightest stay
on the cap. It multiplies, and the halo ends at a finite distance, so black stays exactly black (only the far floor's
faint average of its lines, under half a level at 100%, reaches one level at 200%). The row is M3 disabled with None. A
drag applies live and saves on release.

*World-locked, always.* The renderer gives the pass `environment_view(relative, reference)`: the head pose, prediction
included, relative to the recentre reference turned to level. It takes only the reference's heading about gravity (the pose
feed's z axis), so a recentre with the head tipped leaves the floor flat. The desktops' `view` and the mode controller's anchor
are never involved, so anchor, follow, locked and a maximized desktop all see the same room. A recentre turns it to the new
heading, as it does the desktops.

*Over it.* The pass replaces the frame's black clear. Desktops are opaque and drawn after it, so their pixels are the same
as without it (`environment_render` compares them). The pills and cards already blend, premultiplied. The highlighted
desktop's glow and border were drawn opaque "over black": with a background the glow blends with `GL_MAX` (added, where the
GL has neither ES 3 nor `EXT_blend_minmax`) and the border is laid over it, so neither blacks out a box round the selected
desktop. With None (or level 0) the pass is skipped and that path is as it was: `gl_bench --hashes` gives the same frame hashes
as before the feature, but for the open panel, which grew.

*Behind a VR video.* The background is drawn first in each output eye's viewport (`VideoMode::draw`'s `background`), and the
projection pass then leaves the pixels outside the picture alone (`VideoSphere::draw`'s `behind`) instead of painting them black.
So it shows round a 180° or fisheye picture, and while a video loads. A 360° picture covers everything, so the pass is skipped
for one. Both eyes get the same image in the side-by-side 3D mode.

*The glasses.* The shader scales each pixel so that its brightest channel stays under 191/255 (hue kept), and the sky and
the floor between the lines stay exactly black, so see-through (`environment_render` checks both, looking every way
round). A slight dither breaks up the dim haze's banding, never on black.

*The setting.* `tools/native_spike/environment.py`: `{"background": "none", "last": "grid", "brightness": 100}` by default, saved
with the view settings (`environment` in layout.json for CLI runs and in the glasses model's profile for panel runs), and
restored at each XR start. A file without it, or with an invalid field, loads that field's default. The renderer gets an
`environment` frame (`{"background":name,"brightness":percent}`, `spike/include/xr/ipc.hpp`) on each change and at a start only
if there is a background, so an older renderer never sees one with None; a malformed one is dropped without stopping the
renderer. The open panel shows it through `placement_menu`'s `background`, `brightness` and `background_picker` keys and a fifth
`field`, `brightness`. The panel's `set_settings` reaches a running session as one absolute `environment` op, like `placement`.

*The bind* is in `view.BINDS`, so the help card, `bind_check` and typing mode treat it like any run bind. Omarchy binds
Super+Ctrl+E, Super+Shift+E, Super+Ctrl+Shift+E and Super+Ctrl+Alt+E, all different chords.

*Panel geometry.* The two rows make the panel 360 px tall (it was 292), so it would run past the bottom of the view with your
head level. While it is open the view shifts down to fit it ([Placement pills](#placement-pills)).

*Cost*, measured with `gl_bench --bench --frames 1000 --size 1920x1200` (the glasses' native buffer; GPU time from
`EXT_disjoint_timer_query`, CPU the draw call), 2026-11-03, on surfaceless EGL, so no compositor or capture is included.
The glasses render on the RX 7800 XT (`DRI_PRIME=pci-0000_03_00_0`); the integrated GPU, gl_bench's default here, is the
worst case:

| GPU | Scenario | GPU none | GPU Grid | CPU none (p50) | CPU Grid (p50) | Frames over 8.3 ms |
| --- | --- | --- | --- | --- | --- | --- |
| RX 7800 XT | maximized | 22 µs | 69 µs | 1.8 µs | 2.5 µs | 0 |
| RX 7800 XT | overview, pills, dot | 26 µs | 70 µs | 5.0 µs | 5.6 µs | 0 |
| RX 7800 XT | overview, head moving | 25 µs | 69 µs | 4.8 µs | 5.7 µs | 0 |
| RX 7800 XT | video frame (without the video's own pass) | 17 µs | 54 µs | 2.1 µs | 2.7 µs | 0 |
| Radeon 610M (the Ryzen's iGPU) | overview, pills, dot | 361 µs | 2591 µs | 5.1 µs | 23.4 µs | 0 |

So about 0.05 ms of the 8.3 ms frame on the glasses' GPU, the same in every scenario: the pass shades each pixel once
whatever is in front of it. Its shader compiles at renderer start, not on the first frame it shows.

Smoke costs a little less than Grid: on the Radeon 610M the overview takes 2.34 ms of GPU time against Grid's 2.56 ms and 0.35 ms
with no background (`gl_bench --bench --frames 300`). It has not been measured on the RX 7800 XT, because an XR session was
using that GPU at the time; by Grid's ratio between the two GPUs it should be about 0.04 ms.

Through the real renderer (`capture_e2e_bench --mode gl-dmabuf --seconds 8 --warmup 2 --hz 120`, zero-copy on the RX 7800 XT
against the loopback compositor, so a stand-in for Hyprland and no glasses; `--environment grid` for Grid, `--tracking` for
a head-tracked run):

| Run | Background | Commits/s | Capture to present p50 / p99 | Blank frames |
| --- | --- | --- | --- | --- |
| Head tracked | None | 120.0 | 13.15 / 16.47 ms | 0 |
| Head tracked | Grid | 120.0 | 13.14 / 16.48 ms | 0 |
| No head tracking | None | 119.8 | 5.72 / 9.90 ms | 0 |
| No head tracking | Grid | 119.8 | 5.23 / 8.47 ms | 0 |

The background asks for frames only while it fades. Settled it needs none of its own in a head-tracked run, which draws every
frame anyway. Without head tracking a renderer drawing on every callback would show new desktop frames a refresh later
(the 5 -> 13 ms between the rows above), so there its trails move only when something else redraws.

### Key bind panel

**Ctrl+Alt+K** (a run bind like the others, so it can be changed too) opens a head-locked Material Design 3 card in the
glasses listing every run bind the panel can change: the view keys (`view.BINDS`) and the send-window keys
(`send.SEND_BINDS`), 25 in all, each with its action and its keys (`tools/native_spike/keybinds.py`; the renderer's
`binds` op, `spike/include/xr/binds_panel.hpp`). The modal keys of an open pill or a playing video are fixed and not
listed. Ctrl+Alt+K was picked because it is free: Omarchy's only Ctrl+Alt binds are Delete (close all windows), Tab and
Shift+Tab (focus the next / previous monitor), its K binds are Super+Ctrl+K and Super+Alt+K, and no XR bind used it
(checked against `hyprctl binds` on 2026-11-03 and by `bind_check`). K for keys.

**Keys while it is open.** The panel is modal: every key goes to it and nothing else sees it (no window, no Omarchy or XR
bind) until it closes. Up / Down, PgUp / PgDn (8 rows) and Home / End pick a row; Enter waits for the next key
combination, which becomes the bind, saved and reinstalled at once (`HyprlandClient.reinstall_view_binds` with the
panel's keys; no XR restart). A bind needs Ctrl, Alt or Super (Shift may join them) and a letter, digit, F-key, arrow,
navigation key, Space, Tab, Enter, Backspace or punctuation; Esc cancels the change (and stays the panel's). R puts the
picked row back to its default, Shift+R twice within 4 s every row. Esc, or the panel's own keys, close it. It closes by
itself after `OVERLAY_IDLE_CLOSE_S` (30 s) without a key, like the help card, and a change waits at most 15 s. Typing
mode, a VR video, the help card and a placement pill each close it; while it is open the overview free pointer gives the
mouse back (`free_pointer.OverviewPointer.overview`). The help card's Ctrl+Alt+H row is now **Ctrl+Alt+H / K**, and it
shows the keys as the panel set them.

**How the keys reach it.** Hyprland 0.56's `Keybind:remove()` erases every bind with the same modifiers and key, in every
submap (`src/config/lua/objects/LuaKeybind.cpp keybindRemove`): binding each possible combination to catch the next one
and removing them afterwards would delete Omarchy's binds on the same keys. So the panel binds no keys at all. Opening it
(`keybinds.panel_on_lua`) enters a Hyprland submap of its own, `xr_workspace_keys`, holding one catch-all bind (made once
per Hyprland config and never removed) that swallows every key while the submap is active, and subscribes to Hyprland's
`input.keyboard.key` event, which reports every key press, with its keycode, before any bind runs. For each press the
subscription reads which of Ctrl, Alt, Shift and Super are held (`hl.is_key_down` on their keycodes) and runs the bind
helper (`xr_control.py ... binds_key MODS CODE`); the run decides what the key means. Keys are named from their keycode
with the US layout (Omarchy's default and this host's `/etc/vconsole.conf`), digits as keycodes (`code:10`...) the way
Omarchy and the send keys bind them. Closing (`keybinds.PANEL_OFF_LUA`) drops the subscription and leaves the submap,
never a submap of the user's; it is part of `view.remove_binds_lua()`, so a run's end, a typing-mode switch and a config
reload all leave the keyboard free (a reload keeps Hyprland's current submap while it drops every runtime bind; an open
panel's subscription is then put back).

Failsafes, in case the keyboard is held: Esc three times in a row closes the panel in the compositor itself, whatever
the run and the helper do; the subscription closes it after 90 s without a key (a Lua timer); the run closes it after
30 s idle; if the run is gone, the next key's helper finds no run and gives the keyboard back (`xr_control.
LEAVE_KEY_PANEL`); and if the subscription cannot be made, the panel never takes the keyboard and a toast says so.

**Conflicts** (`keybinds.find_conflicts`). Two XR binds on one chord would both run, and an XR bind on a chord the
desktop also binds would delete the desktop's bind when the run removes its own at the end (the removal rule above). So
the desktop's binds are read live from plain `hyprctl binds` (`HyprlandClient.binds_text`; the JSON form leaves a
keycode bind's key empty in 0.56) at every start, before the run binds go in, and whenever the panel opens. The run's
own binds are told apart there by count.
- A new bind on keys another XR bind already uses, or that a Hyprland / Omarchy bind holds, is refused: the panel keeps
  waiting, the row (the other XR bind's, or the one being changed) turns red and says what holds those keys, and a toast
  repeats it.
- On load, a saved or default bind whose keys a described Hyprland bind holds (every Omarchy bind has a description) is
  not installed ("off"), and a later XR bind on the same keys as an earlier one is off too. Those rows are red with their
  note, the status line counts them, and a toast at the start names the first one. An undescribed Hyprland bind on the
  same keys (a user's plain `hl.bind`, or the leftovers of a run that crashed, which a Hyprland reload clears) is flagged
  the same way but still installed, as before the panel existed.

**Saved** in `~/.config/viture-workspace/keybinds.json` (next to `layout.json` / `profiles.json`, mode 0600):
`{"schema": 1, "binds": {"next": "CTRL + ALT + J"}}`, only the binds that differ from their default. A missing,
unreadable or invalid file or entry means the defaults. With every default in place the generated bind Lua is exactly
what it was before the panel. `bind_check` checks the saved keys too (`--keybinds FILE`).

**Look** (`binds_panel.hpp`). The help card's surface (an extra-large rounded surface container with a 1 px outline-variant
edge) in the M3 scheme of the Omarchy theme colour, every role under the 191/255 OLED cap. A headline with the picked
row's place ("7 / 25"); one list item per bind, its keys in a chip on the right (outlined; filled primary-container when
not the default); the picked row on a secondary-container band, a conflict on an error-container band with its note as a
second line (and a primary focus ring when picked), the row being changed with "Press keys..." in a primary-outlined
chip; "N more" above and below when rows are scrolled out; the key hint and a status line (on-error-container when it is
an alert). Ten rows at a time at 1920×1200 @1.25 (fewer at the smallest text at scale 2); the window scrolls only when
the picked row would leave it. It fades like the help card, in the same place, on top of it.

To try it in the glasses (needs the renderer, supervisor and panel QML from this branch installed together):

| Step | Do | Expect |
| --- | --- | --- |
| K1 | Start XR, press Ctrl+Alt+K | The panel fades in with 26 rows ("1 / 26", 10 shown, "16 more"); status says no conflicts (or names them) |
| K2 | Type letters, press Super+Q | Nothing reaches the focused window and no Omarchy bind runs; the status says Esc closes the panel |
| K3 | Down to *Next desktop*, Enter, then Ctrl+Alt+J | The chip shows "Press keys..." then Ctrl+Alt+J (filled); close the panel; Ctrl+Alt+J switches desktops, Ctrl+Alt+Right does nothing |
| K4 | Ctrl+Alt+H | The help card lists Ctrl+Alt+J for *Next desktop* |
| K5 | Enter on any row, then Ctrl+Alt+L | Refused: the *Placement pill* row turns red with its note, a toast says so, the panel keeps waiting |
| K6 | Then Ctrl+Alt+Delete | Refused: "Hyprland binds Ctrl+Alt+Delete to Close all windows" on the row and in a toast; no window closes |
| K7 | Esc, Esc | The change is cancelled, then the panel closes; Super binds work again |
| K8 | Reopen, R on *Next desktop*; Shift+R twice | Back to Ctrl+Alt+Right; then every bind back; `keybinds.json` holds `"binds": {}` |
| K9 | Reopen, Esc three times quickly | The panel is gone in the compositor at once (the third Esc is never sent) |
| K10 | Reopen and wait 30 s; reopen and switch the Omarchy theme | It closes by itself; after the theme reload it is still open and still takes the keys |
| K11 | Stop XR with the panel open | Keys and Super binds work normally afterwards (`hyprctl submap` is empty) |

### Privacy mode (main display off)

**Ctrl+Alt+D** during a run turns the main physical display off, so only the glasses show content. Press it again to
turn the display back on. The main display is the run's physical fallback (the monitor Stop and Recover return windows to,
e.g. the Odyssey on DP-1). Failing that, it is the only monitor that is not the glasses' output, not an XR-n desktop and
has no VITURE in its description. The display is blanked with DPMS, not disabled. Disabling it would make Hyprland
move its workspaces and windows onto another output, and the run's guard and recovery need it connected. DPMS leaves
the layout, the workspaces and XR capture as they are: the renderer captures only the headless XR-n outputs and
presents on the glasses' own output. With Omarchy's `key_press_enables_dpms` / `mouse_move_enables_dpms`, Hyprland
0.56.2 would turn every monitor back on at the next key or mouse move. To stop that, the blank is followed in the same
eval by a no-op `dpms on` for the glasses' output, which resets Hyprland's global DPMS flag (`tools/native_spike/privacy.py`).

Each switch shows a desktop notification, which appears on the focused desktop, so in the glasses. The display comes
back on when the run ends, and recovery turns the fallback on before anything else: Stop, a renderer or controller
crash, the panel's Recover. If the run died and its binds survived, Ctrl+Alt+D turns every monitor's DPMS on. The run's
guard accepts a DPMS-off fallback (it must still be connected and enabled). Ctrl+Alt+D is off in typing mode like the
other run binds, and works while a VR video plays.

Limits: windows on the blanked display still take input, and Ctrl+Alt+B still sends windows there. An idle daemon's
resume, or a config reload that re-lights the panel, can turn it back on; one press of Ctrl+Alt+D blanks it again
(the key reads the display's current state). Offline tests only (`tests/native_spike/test_privacy.py`); not yet tried
on the live desktop.

### Head tracking from the command line

A panel start with *Head tracking* ticked starts the tracking driver itself (`tools.native_spike.tracking.ensure_ready`):
it reuses an already calibrated, network-isolated driver this app started, or (re)starts its own and waits up to 120 s
for calibration. It refuses to compete with an XRLinuxDriver it did not start. For manual and CLI runs:

```sh
python -m tools.native_spike tracking start   # also: status, stop
```

### CLI recovery

If the panel reports "Needs CLI recovery" (for example several unfinished runs), recover each run's journal with the
supervisor CLI: `python -m tools.native_spike recover --journal JOURNAL --instance INSTANCE --output OUTPUT
--consent-file CONSENT`. The panel shows the journal path.

### Sending windows to the glasses

During a run, focus a window on your monitor and press **Ctrl+Alt+G**. It moves to the desktop selected in the glasses
view: the one you are zoomed into, or the one highlighted in the overview (Ctrl+Alt+Left / Right change it). **Ctrl+Alt+1**,
**2** or **3** send it to XR-1, XR-2 or XR-3 instead, whichever is selected. The digits are bound by keycode, like
Omarchy's workspace keys, so they work on any keyboard layout. **Ctrl+Alt+B** sends the focused window back from the
glasses to the physical monitor, the same output Stop and Recover return windows to. In every case the window lands on
the workspace that output is showing, and keyboard focus goes with it.

Pinned windows are refused with a short desktop notification: a pinned window on an XR desktop would stop recovery from
moving that desktop's windows back. Unpin it first. Pressing a send key for a desktop the session doesn't have
(Ctrl+Alt+3 with two desktops), with no focused window, or Ctrl+Alt+B on a window that isn't in the glasses also only
shows a notification. Like the other run keys, these are bound only while a session runs and are removed when it ends.
Stop and Recover still return every window, including the ones you sent, to the physical monitor. A move already in
flight when you press Stop finishes before recovery starts, and none starts after. If a run dies and leaves its binds
behind, a send key shows "No XR session is running" and moves nothing.

The help card's Ctrl+Alt+H row reads "Help; Ctrl+Alt+G/B: window to glasses/back". The numbered keys are listed only
here.

### The overview free pointer

In the overview (Ctrl+Alt+Down) the mouse is no longer confined to the desktops. Hyprland keeps the real cursor inside
the monitor layout, so on its own it could not reach a desktop's glass pill (drawn under it), an open placement panel
or the outside of its corners. The pointer has two modes, and switches by itself:

- **Inside a desktop** the real cursor is the pointer. It is visible (the capture paints it), and clicks, hover, window
  drags and window-border resizes go to the window under it exactly as on a monitor. The run only reads the cursor's
  position and maps it onto that desktop's surface in space.
- **Free**, as soon as the cursor touches the edge of its desktop (its outermost pixel) or crosses onto another monitor
  in Hyprland's layout. The cursor is hidden and parked in the middle of its monitor, put back there whenever it strays
  200 px, and its motion moves a small dim dot through the space around the desktops at the speed the cursor had on
  that desktop. Between desktops, above and below them, past their corners: anywhere within half a view beyond the
  edges. The left button belongs to the run while free.

Moving the dot onto a desktop's content hands the pointer back: the cursor reappears at that spot, visible,
and that desktop becomes the selected one (the highlight moves; Ctrl+Alt+Up zooms into it). The run does this for
every desktop too ([the unified cursor](#the-unified-overview-cursor); from 2026-11-02 until `anchor-no-click-maximize`
on 2026-11-03 only the selected desktop took the cursor back). Leaving needs the edge
pixel and puts the dot 2 logical px past it (`EXIT_PX`), on that edge's resize handle, and coming back needs 0.5 tile px
inside (`ENTER_PX`), so the two modes never flap at an edge and the dot is never left free over content (until
2026-11-03 the re-entry took 4 px, a band where a click only selected the desktop). A keyboard switch that warps the
cursor to another desktop (focus, send-window) is followed, not treated as motion. A cursor on the physical monitor is
left alone; Ctrl+Alt+Down brings it into the middle of the selected desktop.

Only the dot is drawn. While the pointer is on the desktops, in either mode, the real cursor is hidden and the dot is
the one pointer in the glasses: inside a desktop it sits where the cursor is on that desktop's surface, so where the
list above says the cursor is visible, the dot is what you see. Only what is drawn changes: what the cursor does inside
a desktop is as described above. The normal cursor comes back on the physical monitor, when the view zooms in, in typing mode, during a VR video and
when the run ends (`tools/native_spike/overview_cursor.py`). A run that is killed rather than stopped can leave it
hidden: the next run's start, a Hyprland config reload, or a left click or panel key the dead run left bound brings it
back.

With the dot:

- **Move a desktop: drag its pill.** The grab zone is the drawn pill with 10 px around it (`PILL_PAD_X`, `PILL_PAD_Y`)
  and the gap up to the desktop's bottom edge. The pill under the dot gets brighter. Hold the left button and drag:
  nothing moves until the dot is more than 4 canvas px (`CLICK_PX`) from where you pressed, then the desktop turns
  round you so that, inside the view, the pill stays under the dot. In anchor mode it carries on round you past the
  view's edge, and a pill anywhere round you can be grabbed again (see
  [Moving desktops](#moving-desktops-in-the-overview-the-pill-under-the-desktop)). Release to drop it. An open panel's
  top strip is the same pill and drags the same way.
- **Open a pill: click it.** A press and release that never moved more than 4 px is a click, not a drag: it expands that
  desktop's pill into its placement panel, closing any other, or collapses it if it is the open one. A key that changes
  the open panel while the button is down (Escape, Tab, Ctrl+Alt+L) wins: that release is dropped, so it never undoes
  the key. See [Placement pills](#placement-pills) for the panel.
- **Resize a desktop: drag an edge or corner.** The border round a desktop is its resize handles (since 2026-11-03: see
  [Handles and content](#handles-and-content)). The dot shows two arrows the way it resizes, and a corner's cap lights up
  (a quiet rounded L round each corner, shown with the level bars). Drag: a corner follows the dot along its diagonal, an edge along its normal, outward bigger,
  inward smaller, about the desktop's centre, so it keeps its shape (`resize_distance`; only `distance` changes). A
  corner wins where it meets the pill's zone; the pill's zone wins over the bottom edge. A click on a handle does nothing.
- **Into a desktop: move onto it.** The desktop takes the real cursor under the dot (above). Only next to the open help
  card, where the dot enters a desktop 4 px clear of the card (`HELP_CLEAR_PX`), is it still free over content: a click
  there selects the desktop and hands it the cursor (`ViewControl.select_desktop`, the same view frame as
  Ctrl+Alt+Left / Right there). Since 2026-11-03 (`anchor-no-click-maximize`) no click maximizes: only Ctrl+Alt+Up (and
  the panel's Maximize) does.
- A click in empty space does nothing, apart from closing an open panel.

#### Handles and content

Since 2026-11-03 (`anchor-no-click-maximize`, Cody: full interaction with every monitor in the overview, the dot a full
cursor of its own) every desktop in the overview is a normal screen and the dot is one cursor. This is the zone contract,
shared with the sibling branches: the level bar sits above the top edge, resize at edges and corners, content goes to apps.
A desktop's surface rectangle is its content: with the dot on it the real cursor is on that desktop's monitor under the
dot, so clicks, drags inside apps, right-clicks, the wheel and the keyboard all go to the window there, natively. Only
these keep XR actions, each on the desktop's own surface, in tile px (canvas px at distance 100; glass keeps its size
whatever the desktop's). `free_pointer.hit` tests them in this order, `zone_of` names them, and the dot's look shows them:

| Zone | Hit region | Action | The dot's look (`look`) |
| --- | --- | --- | --- |
| Control | the help card, then the open placement panel's body (`panel_rect`, 480 x 326 under the desktop, drawn over everything else) | sliders, Resolution, Scale, Ultrawide / Add / Remove; page arrows | plain |
| Resize, corner | the corner's square (`corner_zone`, 10–28 px half-size) within the border | drag along the diagonal | `resize_nwse` (top-left, bottom-right), `resize_nesw` (top-right, bottom-left); also lights that corner's cap |
| Move | a pill zone: the drawn pill padded 10 px (`PILL_PAD_X`, `PILL_PAD_Y`), plus the 10 px gap up to the bottom edge (`on_pill`); an open panel's header strip is the pill | click: open / close the panel; drag: move the desktop round you | `move`: four arrows |
| Rotate | a level bar (`level_bar_segment`): the drawn bar padded by 4 px, split at its thirds, wherever no corner, pill or desktop's content is; it takes the edge's border from 8 px out | left and right segments: click: level / 2° step; drag: roll. Middle segment: click: level; a drag off it does nothing | `rotate`: two arrows chasing round the dot |
| Resize, edge | the border along each edge: 10 px outside it (`EDGE_OUT_PX`) to 0.5 px inside it (`ENTER_PX`), the nearest edge (`border_handle`) | drag along the edge's normal | `resize_ew` (left, right), `resize_ns` (top, bottom) |
| Content | the rest of the rectangle; in desktop mode up to the very edge pixel | the window's | plain |
| Space | anything else | nothing | plain |

**One cursor.** The dot is the only thing drawn, and nothing about it says "mode". It moves at the same speed either side
of an edge (`_gain`). Pushing it against an edge from inside takes it 2 px past the edge (`_exit`), onto that edge's or
corner's handle, with its arrows; any move back inside gives the real cursor back under it. Its look (the
`overview_pointer` frame's `look`, marks round the disc in the dot's own grey with a black outline: `view.hpp` `PointerLook`,
`direction_marks`; `ipc.hpp` checks it strictly) always says what a press would do. It holds through a drag, wherever the
dot goes, and when a resize or roll ends over content the real cursor takes over at once. The dot brightens while a button
is held, on a window as on a handle. Marks are drawn upright: on a rolled desktop the arrows do not turn with it. Install
the renderer and Python together: an older renderer drops an `overview_pointer` frame with a `look` as malformed, so the
dot would stop moving over the handles.

Hit testing tests the open panel first (it is drawn over the desktops), then front desktop first its corners, its pill
zone, its edges and its content, and only where none of those took the point the level bars, which are drawn under the
desktops (`free_pointer.hit`): panel, corner, pill, content, bar, edge, space. On the handles, between desktops and on
the help card the dot stays free: the run's grab has every button and the wheel there, so nothing is forwarded to a
window. `test_full_interaction.py` sweeps placed, curved, resized and rolled desktops to check that no handle covers
content; `test_pointer_zones.py` checks every zone, its limits, its precedence, the looks, the one-cursor hand-over and
the edge and corner drags.

The mapping: the dot's ray meets the placed desktop's surface (`free_pointer.unproject`, which undoes the orbit round
you, the distance, the curvature and the levelling roll) at a tile pixel, and `surface_to_layout` turns that into the
monitor's Hyprland layout pixel by its share of the output's logical size (`resize.outputs_from_monitors` divides the
mode by the output scale), at least 2 px inside. `layout_to_surface` and `project` go back, for the dot drawn over the
cursor. Logical pixels are integers, so at 200 % the cursor lands within one physical pixel of the dot's spot.

Entering a desktop (`unified_cursor.enter_desktop_lua`, one eval) focuses its monitor first, then warps the cursor
under the dot, then installs the left button's non-consuming binds. The focus makes the keyboard go to that monitor's
focused window whatever the focus-follows-mouse setting; the warp after it puts the cursor back under the dot (a
monitor focus alone moves the cursor to that monitor's last window) and, with focus-follows-mouse, focuses the window
under the dot. The pointer binds no key. An open placement pill's modal keys (arrows, Tab, R, U, N, Delete, Esc) would
take typing meant for a window, so a left press on a window (desktop mode) takes them off the keyboard
(`ViewControl.desktop_press`). The pill stays open, on that desktop, and a press on the panel or its pill brings its keys back.
Window drag (hold a window to lift it onto another desktop) is off (`OverviewPointer.window_drag = False`): it took
long presses and slow drags from apps.

Hit testing order: an open panel is drawn on top (the renderer draws its desktop's glass last, over every other pill),
so it is tested first (the pointer is mapped onto the open desktop's surface, where the panel is drawn). Then, front
desktop first, each desktop's corners, its pill zone and the desktop itself. Everything is hit along the dot's own
direction, the one the renderer draws the dot in (below).

Hit testing uses the same maths the renderer draws with (`spike/src/view.cpp` `place_point`, mirrored in
`tools/native_spike/orbit.py`), including the orbit round you, distance and curvature, with every desktop facing you,
front desktop first. For an orbited desktop the pointer's ray is first turned back by its orbit
(`orbit.unorbit_ray`). The renderer reports the overview's canvas, field of view and settled desktop slots on its
status socket (`overview_geometry`) whenever they change. The dot is world-locked on the overview's plane like the
desktops' slots. The cursor is read 250 times a second in the overview with the read-only `cursorpos` query, and each
change wakes the supervisor loop at once; the renderer draws the dot on the next frame (`overview_pointer` op) and eases
placements (35 ms time constant), so drags look smooth at the display's rate.

The free pointer is off in typing mode (Ctrl+Alt+T: the cursor stays a normal cursor everywhere, but for the length of a [Super move](#super-drags)), while zoomed in and
while a VR video plays (`resize_press` / `resize_release` are in `IGNORED_WHILE_PLAYING`). It stays on while a
placement panel is open: it is how you use the panel. Leaving the overview removes the left-button binds and shows the
cursor again. A drag in progress (pill, corner or slider) ends and is saved when a video opens, on zoom-in and after
30 s without pointer movement. While the dot is free, right clicks and scrolling are swallowed (no window is under
it: a handle, empty space or the help card); over any desktop's content they reach the
window there ([the unified overview cursor](#the-unified-overview-cursor)).

The dot, pills, level bars and corner caps protect the micro-OLED like the cards. The dot is a grey disc (170/255, 184 while dragging)
in a black ring, 16 px across. The pills are dark glass (see [Placement pills](#placement-pills)) that brightens on
hover and while dragged, never above 191/255, as the bars and caps stay (their colours are M3 roles). None of them moves on
its own. They hide after 30 s without pointer movement (`OVERLAY_IDLE_CLOSE_S`); a click while hidden only brings them
back. An open panel is still drawn while the pills are hidden, and closes by itself after its own 30 s without input.
The CPU fallback renderer draws none of them, and the run reports no `overview_geometry` there, so the free pointer
stays off.

### Resizing desktops in the overview

A drag on an edge or corner (above) changes the desktop's size in space. The desktop scales about its own centre and
always keeps its shape, so an edge drag grows it the other way too.

The size has limits and is saved like the other placements:

- It goes from half to ten times the default size (well past the field of view). A resize is the desktop's **Distance**
  placement seen from the other side (apparent size = 100 / Distance), so the limits are Distance's 200 and 10. Past
  the edge of the view the drag speeds up (`free_pointer.RESIZE_OVERSHOOT_GAIN`): the pointer stops half a view past
  each edge, so a corner that followed it 1:1 could not reach ten times. Once a desktop is bigger than the view its
  corners and pill are out of sight; shrink it with the open panel's keys (Ctrl+Alt+L, Down to Distance, then Right / Shift+Right, or R to reset). The panel's **Place** tab and an
  open placement pill show the new Distance. For example, 1.5× bigger is Distance 67.
- A resize never changes the glass pill's (or its open panel's) size: it moves with the desktop's bottom edge and stays
  the same size on screen (`view.hpp glass_to_desktop`, mirrored by `free_pointer.glass_to_desktop` for hit testing).
  It stays 4 px, as seen, under the selected desktop's border. The border is tile px, so it grows with the desktop (60 px
  at ten times the size), which is why glass hangs from the border's outer edge rather than the desktop's bottom edge:
  from there the gap is the same at every size, and nothing else changes at the default size. Every desktop's glass
  hangs there, selected or not, so selecting a desktop never moves its pill: a bigger desktop that isn't selected shows
  the empty strip where its border would be. The pill's grab zone runs up to the desktop's bottom edge over that strip
  and stops there (`free_pointer.on_pill`), and beside it the edge still resizes. While a slider is held the open panel
  stays on its press-time surface, so a dragged Distance moves the desktop's border past it until you let go.
- It is saved to the glasses model's profile half a second after the last change and again on release, so it carries
  over to the next session.
- Growing pushes the desktops round it out of its way ([Bumping](#bumping-desktops-out-of-the-way)); with no room left
  it stops growing.
- Maximizing a desktop brings it full size where it is (its dragged place round you), then follow brings it in front of
  you; the others stay where they are. The bars, caps and pills only show in the overview.

Why it never changes pixels: a resize changes size in space, not the desktop's resolution or Hyprland scale. In the
overview all desktops share one height, so pixel count only changes sharpness, never size. A free-aspect resize could
only stretch the captured image, which is why the shape is always kept. The desktop editor (the panel's **Startup
options**) sets resolution, shape and scale for the next start, through the journaled output creation that reads every
value back; only the pill's Ultrawide toggle and its Resolution and Scale dropdowns change a running desktop (a broker
`reshape`, below). Corner resizing adds no compositor output change and no new journal record.

### Live resolution changes (the pill's Resolution dropdown)

A pick in the open pill's Resolution list (`ViewControl.resolution_pick`) is a `desktops.RESOLUTION` plan, the same
broker path as Ultrawide and Scale (`desktops.py`): the run's guard pauses, the renderer is told nothing yet, and the
worker asks the broker to `reshape` that output and, if it grows or shrinks, the outputs to its right to slide along
the row (what moves left or shrinks first, left to right; then the rest right to left, so no two outputs ever share
layout space). Why it is seamless:

- **Nothing is modeset but that one virtual output.** `reshape` (`spike/ownership/plugin.cpp`) swaps the broker's own
  monitor rule for that headless XR-n output and applies it to that monitor alone (`applyMonitorRule` and an arrange):
  a headless commit at the new size. The glasses' and your other displays' modes are not touched, there is no config
  reload, and since the rule in the rule manager is the new one, a later reload keeps it (`keptThroughReload`, as for
  layout switches). The run goes on: no XR restart, no new consent, no journal record (the output keeps its id).
- **Windows stay.** The output is never removed or re-created, so its workspaces and windows stay on it: tiled windows
  re-tile to the new size and floating ones keep their place on it (Hyprland moves the floating windows of an output
  that slides along with it, `CMonitor::moveTo`). Hyprland does nothing for a floating window on an output that got
  smaller, so whenever a change shrinks a desktop's logical size (a smaller Resolution, Ultrawide off, a larger Scale:
  `Plan.shrunk`), the worker pulls those windows in once the broker steps are done (`fit_windows.py`). Every floating
  window on that output's regular workspaces, shown or not, that sticks out moves the least it takes to be inside, into
  the part the bar leaves free; one larger than that also shrinks to fit. Windows already inside, fullscreen ones and
  windows hidden in a group are left alone. It runs inside the renderer's hold below, and a failure here never fails
  the change. It is generated Lua from validated addresses and whole pixels, the same absolute `hl.dsp.window.resize`
  and `move` Stop uses to put floating windows back (`restore.py`).
- **No blank or half-drawn frame in the glasses.** Hyprland answers the mode change with new capture constraints. The
  renderer keeps showing the last frame, at its old size, and re-creates that capture's buffers only
  `kResizeSettleMs` (100 ms) after the new size was first seen (`ResizeSettle` in `spike/include/xr/capture.hpp`), so
  the output's own clients (wallpaper, bar, windows) have drawn themselves at the new size before the first new frame
  is shown; black there would be see-through on the OLED panels. When the worker is done the supervisor sends the
  `desktops` frame with that output in `renew` (its capture restarts, held the same way), then the view, placement and
  panel frames.
- **It stays where it is in the glasses.** Placements are not touched. The overview gives every desktop the same
  height, so a change within one shape (1920x1080 to 2560x1440) changes only sharpness and what fits and nothing moves.
- **A new shape eases.** When the first frame at a new shape lands, that desktop's aspect in the layout eases from the
  old one to the new over 220 ms with smoothstep, the same easing and time as zoom and spacing (`SizeEase` and
  `kViewEaseMs` in `spike/include/xr/view.hpp`). Every layout is computed from those sizes, so the tile widens or
  narrows, its neighbours slide along and, when the row is limited by the view's width, all of them share the new height,
  in step. It works for any live change of shape, so Ultrawide does the same. The new frame is drawn squeezed to the old
  shape as it starts. The supervisor's hit testing keeps the settled layout (`take_overview_geometry`), so it is
  ahead of the picture for those 220 ms. The CPU fallback renderer lays the new size out at once.

The scale stays if it still divides the new size, else it becomes the nearest step that does (`geometry.nearest_scale`;
a tie goes to the larger step), and the panel's Scale list re-evaluates which steps are greyed out. The profile keeps
the new size in the run's per-desktop `desktops` entries (`DesktopManager.save` -> `PlacementStore.save_desktops`:
`{width, height, scale}` per desktop, next to its placement), which every start restores (`profiles.load_setup`). The sizes are one table in `geometry.RESOLUTION_GROUPS` / `RESOLUTIONS`, mirrored by
`spike/include/xr/desktops.hpp` `kResolutions` / `kResolutionGroups` (the `glass_contract` ctest compares them); every
one is inside the broker's `validDesktop`. The `placement_menu` payload carries the open desktop's `resolution`
(`[width, height]`, even, 640..7680 x 480..4320) and `resolution_picker` (its list open; only on an open panel, never with
the Scale list's `picker`). On the panel the Resolution row sits above Scale (the panel is 326 px tall); its menu opens
upward over the four slider rows, three columns (16:9, 16:10, Ultrawide) of 23 px items.

### The unified overview cursor

Offline tests only (`tests/native_spike/test_unified_cursor.py`, `test_full_interaction.py`, `test_pointer_zones.py`, `test_super_drag.py`, `test_directional_switch.py`,
plus `bind_check`'s `--verify-config` load of its binds); not run in a live compositor or tried in the glasses. Since
2026-11-02 (`dot-unified-cursor`) the dot is the one real cursor, and the real Hyprland cursor stays on the selected
desktop's monitor ("home"), zoomed in and in the overview; it never crosses to another monitor in Hyprland's layout by
hand (that layout is not where the desktops are round you). Since 2026-11-03 (`anchor-no-click-maximize`) home follows
the dot in the overview: the dot moving onto any desktop's content makes it home and hands it the real cursor, so every
desktop's windows work as on a normal screen ([Handles and content](#handles-and-content)). Switching desktops is that,
a key (Ctrl+Alt+Left / Right), or, zoomed in, a push past home's edge toward a desktop placed that way (below).
`tools/native_spike/unified_cursor.py` wraps the free pointer above and replaces the input lock, which held the cursor
still for the whole overview and let no click or wheel reach a window.

- **Zoomed in** (and in typing mode or during a VR video): the run samples the cursor at 250 Hz while it is on an XR
  monitor. If the hand pushes it onto another XR monitor, or off home onto the physical monitor, the run puts it straight
  back at the nearest point 2 px inside home and gives home Hyprland's monitor focus again (`confine_lua`). A jump of
  more than 400 px onto the physical monitor is a warp, not the hand (a window sent back with Ctrl+Alt+B), and is left
  there. A cursor already on the physical monitor is left alone until it comes onto an XR monitor, and then goes to home.
- **Overview, over a desktop:** the real cursor is the pointer. Hand motion moves it, and the dot is drawn where it
  maps onto that desktop's surface, in anchor, locked and follow mode. It stays hidden, but hover, focus-follows-mouse,
  every button, the wheel and the keyboard reach the window under it. The left button is bound *non-consuming*
  (`non_consuming = true`), so the window gets the click as usual. The run sees it too: a press brings an open pill to
  that desktop and takes its modal keys off the keyboard, and that is all; it never zooms in (until 2026-11-03,
  `anchor-no-click-maximize`, a press and release less than 4 canvas px apart did). While the button is held the dot
  never goes free and the cursor stays on that desktop: if Hyprland lets it onto another monitor, it is pulled back, so
  a drag inside an app keeps going and the window gets its release.
- **Overview, off the desktops:** at the desktop's edge pixel the dot goes free, 2 px past the edge, on its resize handle. The hidden cursor is parked just
  inside that desktop where it left, at least 100 px (`PARK_MARGIN_PX`) from the edges, and put back once it strays
  32 px (`LEASH_PX`). Its motion moves the dot through space. Free mode uses the input lock's grab
  (`input_lock.lock_pointer_lua`): the left button drives pills, edges, corners, level bars, sliders and the help card,
  the right, middle and side buttons only end a drag, and the wheel is bound to nothing (with
  `binds.scroll_event_delay` at 0, restored afterwards). Moving the dot onto any desktop's content hands that
  desktop the real cursor under the dot and makes it home (`enter_desktop_lua`: its monitor focused, then the warp,
  then the click binds; the grab and its wheel and button binds go).
- **Super drags:** see [below](#super-drags).
- **Keys:** Ctrl+Alt+Left / Right change the selected desktop. Home moves with it, and the cursor and dot go to the
  middle of the new home. The pointer binds no key: the keyboard is the focused window's. Any other jump off home in the overview is pulled back home, or let go if it landed on the
  physical monitor. Cursor follow (Ctrl+Alt+F) was removed: monitor focus events never switch the view.
- **Zoomed in, a push past an edge switches** (`dot-directional-switch`, 2026-11-03; `unified_cursor.EdgePush`,
  `tools/native_spike/neighbors.py`; not in typing mode or during a VR video). The desktop it goes to is the one placed
  past that edge round you, not the next in the row: each desktop is a direction from the eye (its centre as drawn,
  `orbit.place_point` on the renderer's overview layout, with its orbit and distance; home sits level while maximized,
  so no roll comes in) with its own right and up axes. A candidate's bearing is the great-circle direction from home's
  centre to its centre, read in home's right / up axes. It must lie within 60° (`neighbors.CONE`) of the push,
  and the lowest angle / cos(off-axis) wins: the nearest, an off-axis one counting as further. A desktop within 1° of
  directly behind has no bearing. With no overview layout reported yet, right and left are the next and previous in the
  row and up and down nothing. Once the push reaches 48 px (`PUSH_PX`, each part within 0.25 s of the last, on one
  edge), the loop selects that desktop (`ViewControl.switch_desktop`, the Ctrl+Alt+Left / Right path, so the renderer
  zooms out, turns and zooms in and no desktop moves; its view frame carries `"center": true`, so in anchor mode the turn
  starts from the centre of the view and the camera pans straight to the new desktop and leaves it centred, world-locked
  there, and pushing back pans to and centres the other one; a maximized desktop is follow or locked since
  maximize-follow, so in practice it gets `"cut": true`, straight onto the desktop with no zoom out / turn / zoom in,
  and the aim centres it) and the sampler puts the cursor 24 px (`ENTRY_PX`) inside the
  opposite edge of the new home, as far along it as it left, and focuses that monitor (`confine_lua`). Then no push
  counts for 0.8 s. Over an edge Hyprland lets the cursor cross (onto the next XR monitor or the physical one) the
  overshoot counts and the cursor goes back as above; a jump over 400 px is a warp and never counts. At an edge
  Hyprland clamps it at (the layout has nothing beyond, e.g. the top of the row), the hand's push is not visible, so
  with a desktop placed beyond it each touch of the outermost pixel counts 12 px (`NUDGE_PX`) and puts the cursor
  12 px back in. With nothing placed that way the cursor is only confined and stays at the edge, unnudged.
- **Scroll before switch** (`scroll-before-switch`, 2026-11-03; `tools/native_spike/columns.py`; zoomed in, typing
  mode included, not during a VR video). When the active workspace of home's monitor has `tiledLayout` `"scrolling"`
  in `j/workspaces` and no fullscreen window, a push at home's left or right edge scrolls its columns first. The end
  of the columns is an explicit test: home's logical rectangle from `j/monitors` (width / scale, width and height
  swapped for a 90° or 270° transform) and the workspace's tiled clients from `j/clients` (that workspace, not
  floating, mapped, not hidden). A client is past the right edge when `at.x + size.x > right + 1`, past the left one
  when `at.x < left - 1`. That is exactly the test in the desktop's `~/.config/hypr/scripts/scroll-edge.py`. While a
  client is past that edge the push counts nothing toward a switch, and the cursor is held in the last 2 px
  (`columns.EDGE_PX`; a cursor pushed onto the next monitor comes back 1 px inside the outermost pixel). The nearest
  client past the edge gets the focus with `cursor.no_warps` for that one focus (`columns.scroll_lua`), so the layout
  brings it into view and the cursor stays put: 0.15 s (`SCROLL_DWELL_S`) after the cursor reached the edge, then
  every 0.35 s (`SCROLL_REPEAT_S`) while it stays there. Once none is left, the push rests 0.6 s (`END_REST_S`)
  after the layout last changed and then counts from nothing like any push (48 px). Pushing on, or pushing again
  later, switches. Right before it switches, the layout is read once more, and a client found past the edge then
  holds the push again. A view already at its end switches on the first push, as before. Reads happen only with the
  cursor at home's left or right edge: `j/monitors` and `j/workspaces` (and `j/clients` on a scrolling workspace) at
  most every 0.1 s, plus one read before and one after each scroll and one before a switch. Master, dwindle, a
  floating-only workspace, a fullscreen window, the up and down edges and a failed read behave exactly as before.
- **With `scroll-edge.py`** (the desktop's own edge scroller, outside this repo). It focuses the nearest column past a
  scrolling workspace's left or right edge while the cursor rests within 2 px of it, every 0.35 s, on every output
  including XR-n. The hold keeps the cursor inside that band, so while it runs it does the scrolling and the run
  yields: any change to the layout that the run did not make counts as the step, and the run then waits 0.8 s
  (`HELPER_GRACE_S`) without another before it scrolls itself. If it is stopped, or has
  [scroll-edge-skip-xr.patch](scroll-edge-skip-xr.patch) applied (its edge loop then leaves XR-n edges to the run;
  click-to-reveal still acts there), the run scrolls. Both use the same end test, so the run never switches while
  either one still sees a column. On a loaded machine where `scroll-edge.py` takes longer than 0.15 s to react, the
  first step can scroll two columns; after that the run yields to it. The patch also stops `scroll-edge.py` from
  scrolling the neighbouring XR desktop when the hand briefly pushes the cursor onto that desktop's edge before the
  run puts it back, which can happen without this change too.
- **One bind table.** Whichever set is live goes in the free pointer's table (`xr_workspace_resize_binds`): the click
  binds over home, the lock grab while free. Each switch replaces it in one eval (`enter_desktop_lua`,
  `lock_pointer_lua`). Run end, a config reload (the set is re-applied) and a dead run all clean it up through the
  existing paths. After a dead run, the next left click still reaches the window and runs the bind helper, which
  removes the whole table (`xr_control.py` `REMOVE_RESIZE_BINDS`).

Not covered: how the push past an edge feels in the glasses (the 48 px threshold, the 12 px nudge at a clamped edge
next to a top bar, the 0.8 s rest against the renderer's switch animation); `tests/native_spike/test_directional_switch.py`
checks the neighbour picking, the push and the sampler offline only. The zoomed-in confinement is reactive, so for up to one 4 ms sample the cursor (and focus-follows-mouse)
can be on the neighbouring monitor before it is put back; touchpad scrolling while free, which Hyprland cannot bind; a
desktop config with `binds.pass_mouse_when_bound`; and a starved sampler thread. To check in the glasses: zoomed in,
push the mouse past every edge of the desktop (it switches to the desktop placed that way, or stays put if none is); in
the overview, move the dot across the gaps (nothing reacts) and onto each desktop in turn (it is highlighted and the
cursor is right under the dot), then on each one click, right-click, scroll, drag inside a window, select text and type
(all reach the window under the dot, the keyboard goes to that desktop, nothing is maximized), including on a moved,
curved, resized, rolled and 150 % / 200 % scaled desktop and right in its corners; check the pills, level bars and the
handles just outside the edges and corners still do their XR actions and never reach a window, and that the dot's look
changes on each and it feels like one cursor across an edge; open a pill, click a window and
type (the pill closes, the letters reach the window); press Ctrl+Alt+Up (the desktop under the dot is maximized); and
repeat after Ctrl+Alt+Right.

### Super drags

Offline tests only (`tests/native_spike/test_super_drag.py`, plus `bind_check`'s `--verify-config` load of the binds); not run
in a live compositor or tried in the glasses. Since 2026-11-03 Hyprland's own window move and resize work through the dot,
including dropping a window on another XR desktop. On this machine they are Omarchy's `SUPER + mouse:272` ("Move window") and
`SUPER + mouse:273` ("Resize window") in `default/hypr/bindings/tiling.lua`. `hyprctl binds -j` lists them as modmask 64,
`__lua` binds with `mouse: false`: on Hyprland 0.56.2 `hl.dsp.window.drag()` is a Lua dispatcher that ends itself on the
button's release, not a classic `bindm`.

The drag stays Hyprland's; the run keeps out of its way and carries it across the gaps. What follows about Hyprland is read
from its source at the installed commit (`efb5099`), not observed.

**Seeing it.** `view.install_drag_binds_lua` adds its own table (`xr_workspace_drag_binds`) with the run binds (not in typing
mode). `SUPER + mouse:272` and `:273` are bound *non-consuming*: Hyprland runs every bind matching a chord, so the user's
still fire, and `bind_check` loads these but does not count them as clashes. The same two chords are bound again on release
with `ignore_mods`, because a release bind otherwise needs the press's modifiers and Super may be let go first; they run
nothing unless a Super press came first, so a plain click spawns no helper. The ops are `view.SUPER_OPS`. A lost release
cannot strand the dot: a plain press ends the drag in the run, and so do 30 s without pointer movement.

**These binds are never removed.** `Keybind:remove()` erases every bind with the same modifiers and key (see
[Key bind panel](#key-bind-panel)), so removing the run's `SUPER + mouse:272` would delete Omarchy's "Move window" until the
next config reload. They are made once per Hyprland config and armed by `xr_workspace_drag_run`, the bind helper's command
line: a run's end, a reinstall and a stale run's cleanup (`view.remove_drag_binds_lua`, `xr_control.REMOVE_DRAG_BINDS`) only
clear it, and without it they do nothing, the helper included. A config reload drops them, and the next install makes them
again. The release binds carry the Super chords, not the bare buttons, because `ignore_mods` keeps the modmask: bare ones
would go every time the free pointer's or input lock's binds on those buttons are removed. Read from Hyprland's source at
`efb5099`; not run on a live compositor.

**A move across desktops.** Inside a desktop nothing changes. At its edge the pointer goes to `carry`, not free:

- The dot goes on through space from just past the edge, as ever, but nothing is grabbed, so the button stays Hyprland's.
  The cursor, and the window with it, waits 12 px inside the edge (`CARRY_INSET_PX`) and is put back once it strays 6 px
  (`CARRY_LEASH_PX`): that is how hand motion past an edge Hyprland clamps at still shows, and the window shakes by no more
  than that.
- Pills, level bars, panels and resize handles do nothing, and do not light up. The dot shows the `move` look throughout.
- Onto any desktop's content (`_carry`) the cursor warps to the pixel under the dot, through the same mapping as any entry
  (rolled, curved, resized, placed, scaled). That desktop becomes home, by a plain `view` frame with no `cut` or `center`: in
  the overview that only moves the highlight, at once. No focus call is made: `moveCursor` already focuses the monitor, and
  a monitor focus would take the cursor to its last window mid drag.
- In Hyprland a warp during a drag moves the window (`moveCursor` -> `simulateMouseMovement` -> `moveMouse`: start position +
  the cursor's delta) and, while it is floating, gives it to the active workspace of the monitor under its **middle**
  (`assignToSpace`). A dragged tiled window floats, centred on the cursor, so it changes monitor with the cursor and re-tiles
  there on release. A floating window keeps its offset from the cursor, so held far from its middle it can still belong to
  the old monitor at the drop: the landing below takes it over.
- Dropped between desktops, the window stays where it waited and the dot is free there.

**The landing.** A drag that crossed desktops and was dropped with the cursor on one ends with the window on that monitor,
even if its middle had not crossed (Cody, 2026-11-03). The press asks which window is under the cursor (`WindowSender.under_cursor`,
off the loop). The drop asks for the landing (`_land`, `WindowSender.land_now`, off the loop), which does nothing if the window is
there already, and moves nothing unless that window is the active one: Hyprland focuses the window it was dragging when a drag
ends, so a Super drag that held nothing, or a lookup that found a different window, can never move an innocent one. No answer
by the drop, a pinned window, or a lost release: no landing.

Otherwise `send.land_lua`, in one eval, moves the window to that monitor's active workspace (silently) and, if it floats, to
`send.landing`. Hyprland's workspace move keeps a floating window's offset from its monitor (`moveWindowToWorkspace`: `POSTOMON`
+ the new monitor's position), which would land it the monitors' distance from where it was dropped, so the absolute move after it
cancels that shift. `landing` keeps the window as near as it can be to where the drag left it, which is still under the cursor,
but whole on that monitor, since the rest of the layout is some other desktop elsewhere round you. Wider or taller than the
monitor, it starts at the monitor's left or top edge. In every case the cursor stays on the window. The silent move hands the
focus on and a window focus warps the cursor to the window, so the eval focuses it and puts the cursor straight back where it
was (`hl.get_cursor_pos`). A tiled window gets the workspace move alone and tiles there.

**In typing mode** the drag binds stay through the bind swap. A move turns the pointer on for as long as it lasts
(`UnifiedPointer.overview`): the dot and its `move` look show, the pills and level bars do not (`_handles_on`), it crosses
and lands as above, and at the drop the pointer goes off and the plain confinement takes over, on the desktop the window was
dropped on. A resize needs no pointer: the confinement keeps its cursor on home. Maximized in typing mode the
pointer stays off and a drag stays on its desktop.

**A resize** is monitor-bound: the window keeps the cursor, as for a held left button. It stays on that desktop, pulled back if
Hyprland lets it onto another monitor, and the dot never goes free. The dot shows `resize_nwse`.

**Super is never an XR action.** The run's own left-button binds have no modifier, so with Super held they do not fire and
no pill or corner drag can start. With the dot off the desktops nothing is under it, but Hyprland has just begun dragging
whatever is under the parked cursor: the bind sees the free grab and a 1 ms timer ends that drag (outside a bind,
`hl.dsp.window.drag()` is the drag's release). A tiled window there may flicker for a frame. Maximized, a Super drag stops the
push past an edge from switching desktops (`can_switch`): the cursor just stays on home.

### Moving desktops in the overview (the pill under the desktop)

A pill drag (above) changes the desktop's **Left/right** and **Down/up** placement. Since 2026-11-02
(`3dof-free-placement`) these turn the desktop round you, so it can go anywhere around you. Everything in this section
is covered by offline tests only. It has not been run in an isolated VM or tried in the glasses.

**The orbit.** Left/right (`x`) and Down/up (`y`) are whole tenths of a degree on the wire and in the profile
(`geometry.PLACEMENT_BOUNDS`, `ipc.cpp` `kPlacementBounds` and `Model.js` `PLACEMENT_BOUNDS` hold the same ranges). `x`
runs from −1800 to 1800, positive to the right; −1800 and 1800 are the same place, directly behind you. `y` runs from
−900 to 900, positive up; ±900 is straight overhead or underfoot. `x = y = 0` is the desktop's own slot in the row.
The renderer eases `x` the short way round (`ease_toward`), so a desktop crossing behind you never swings back through
the front. For scale, a 16:9 desktop is about 13° wide in the overview.

**Always facing you.** Every desktop is a billboard toward your eye, which in 3DoF is where your head is. At its slot,
while it is dragged and wherever it ends up, it is square to your line of sight and upright along its meridian. There
is no setting for which way it faces. `spike/src/view.cpp` `place_point` places each canvas pixel (px, py) of a desktop
this way, in float, and `tools/native_spike/orbit.py` `place_point` mirrors it exactly for hit testing and the guard
(the `placement_contract` ctest compares the two):

- The canvas is W × H px with half-FOV tangents hw, hh, so sx = 2hw / W and sy = 2hh / H. The desktop's slot is its
  tile (tx, ty, tw_px, th_px) with centre (cx, cy); tw = tw_px · sx. d is Distance as a fraction (1 = the slot), or 1
  if it is not above 0.
- The slot's centre on the overview's plane (z = −1) is wx = (2cx / W − 1) · hw, wy = (1 − 2cy / H) · hh. Its direction
  from the eye is a0 = atan2(wx, 1) and e0 = atan2(wy, √(1 + wx²)), and the desktop's centre is
  r = √(1 + wx² + wy²) · d away.
- The pixel's offset is u = (px − cx) · sx, v = (cy − py) · sy. Its local point is (u, v, 0), or on a curved desktop
  (rc = tw / curvature, φ = u / rc) (rc sin φ, v, rc (1 − cos φ)), the edges toward you. The local point is pushed
  straight ahead, q = (local.x, local.y, local.z − r), lifted by E = e0 + y about the horizontal axis,
  q = (q.x, cos E · q.y − sin E · q.z, sin E · q.y + cos E · q.z), and turned by A = a0 + x about the vertical axis
  through the eye: world = (cos A · q.x − sin A · q.z, q.y, sin A · q.x + cos A · q.z). Here x and y are in radians,
  the wire's tenths of a degree times π / 1800.

So `x = y = 0` is no longer the flat canvas plane for a desktop off the middle of the view: the side desktops of the
default row now turn to face you. A centred desktop at Distance 100 is still exactly in the plane z = −1, and zooming
brings the zoomed desktop to that distance in the direction it was dragged to, in every viewing mode, and leaves the others
where they are (`zoomed_placements`). Moving a desktop from its slot is the rigid
rotation about the eye Az(a0 + x) · El(y) · Az(−a0) (`orbit.rotation`), which hit testing (`orbit.unorbit_ray`)
and the guard's footprints use. The yaw, pitch and roll settings, which used to turn a desktop in place, were removed
on 2026-11-02 from the settings panel, the glass panel, the wire and the profiles.

**The dot round you.** The dot's direction is linear in the pointer (`orbit.pointer_direction`): every mouse pixel
turns it the same angle, across the whole view and all the way round, at the overview plane's own rate at the view's
centre (`half / (canvas / 2)` radians per canvas pixel, about 0.02° in the default layout, times the pointer's gain).
In anchor mode (the desktops fixed in the room) its reach (`OverviewPointer._clamp`, `orbit.reach`) goes on to
directly behind you (±180°) and straight up and down (±90°); follow and locked modes turn the view with your head, and
there it goes half a view past each edge. The renderer always draws the dot in that direction: the `overview_pointer`
frame carries `az` / `el` (hundredths of a degree) and the dot is drawn there (`direction_disc`, the flat disc turned
round the eye). Everything else reads the same direction: the hit test (`free_pointer.hit`, the ray met on the
overview's plane after the desktop's orbit is turned back), the help card (reported on the plane, so the ray is met
there), and in desktop mode the cursor's spot on a desktop wherever it is placed, so what the dot is drawn on is what
it hovers and clicks.

**The drag.** The point you grabbed stays exactly under the dot (`orbit.drag_target`, `orbit.PillDrag`): at the
press the dot's ray is turned back into the desktop's own frame (`orbit.grab_point`), and every move solves for the
x / y orbit that turns that point onto the dot's ray (`orbit.solve`). In anchor mode that takes a desktop all the way
round you, behind you and over you, under the dot. It is off only by the wire's tenth of a degree, by the snap (each
direction snaps back to the slot within 0.5°, `orbit.SNAP`), and with the dot right at a pole, where an upright desktop
brings a point off its middle only so close. Left/right wraps round behind you; Down/up stops at ±90°.

- Until 2026-11-02 (drag-cursor-sync) the drag read the stretched direction while the dot was drawn on the overview's
  plane, so once the dot left the view the desktop ran ahead of it, faster the further out.
- Until 2026-11-02 (drag-speed-linear) the dot itself was the pointer's ray through the plane inside the view (an
  `atan`) and, in anchor mode, a quadratic stretch from the view's edge to behind you over only half a view, so past
  the edge each mouse pixel turned the dot and the dragged desktop up to about 16 times as far as at the edge: the
  drag sped up and stopped following the mouse. Now it is the same angle per pixel everywhere.
- The drag's reach (anchor mode's or not) is fixed when it starts. Its target is followed unwrapped and only wrapped for the wire,
  so a desktop held against a neighbour does not flip round the other way when the target passes behind you.

**No overlap.** Desktops never go through each other as seen from your eye: a desktop moved or resized bumps the
ones in its way (`tools/native_spike/bump.py`, [Bumping](#bumping-desktops-out-of-the-way) below). Each desktop's
footprint is the set of directions from the eye that it covers: its true placed outline (distance, orbit and curvature
included; a curved desktop uses its convex hull, so it errs early), widened by a 6 canvas-px margin all round
(`MARGIN_PX`, the width of the highlight frame) and by the 34 px strip under it that holds its pill and the pill's
grab padding (`PILL_ZONE_PX`). Until 2026-11-03 a drag stopped before the first step that would make two footprints
share a direction and slid along the neighbour (`orbit.allowed_move`, removed), and the sliders, keys and the corner
resize were not guarded.

**Keys.** With a pill open, Left / Right change Left/right by 0.2° and Shift+Left / Shift+Right by 15°
(`view.MENU_STEPS`); it wraps past 180° to the other side (`orbit.wrap`) and never stops. Down/up steps by 0.2° and
10° and stops at ±90°. The glass panel shows these two in whole degrees, rounded ("+13°", "-180°"; "+13deg" with the
pixel font, for width), Distance in percent and Curvature in degrees. The settings panel's **Place** tab shows tenths
("12.5°"), and its one-line summary reads like "moved 12.5° right, 3° up". Its preview keeps a desktop beside or behind
you at the end of the row.

**Maximized and the overview: the viewing modes.** Since 2026-11-03 (maximize-follow, after the INAIR Pod) the viewing
mode is no longer a setting of its own: it follows from the view (`ViewState.mode`, a property of `zoomed`). The
overview is always anchor (3DoF, the desktops fixed in the room). Maximizing (the `zoomed` state; Ctrl+Alt+Up or the
panel's Maximize; since 2026-11-03 a free-pointer click on a desktop only selects it) switches to follow or locked:
`ViewState.maximized_mode`, which Ctrl+Alt+V toggles while maximized (`MAXIMIZED_MODES`). The choice sticks for the
session: every later maximize uses it until Ctrl+Alt+V changes it again. It is never saved, so each XR start (a new `ViewState`) maximizes in follow. In the overview
Ctrl+Alt+V changes nothing and `MODE_OVERVIEW_TOAST` says why.
Un-maximizing (Ctrl+Alt+Down, Ctrl+Alt+L, a pill click) returns to anchor. Every run starts in the overview, anchored:
`managed.view_controls` no longer passes the saved mode, and `ViewState.settings()` always saves `mode: "anchor"` (the
key stays for the profile schema and an installed panel; it is never read at start). Spacing, prediction and the VR
video quality are still saved and restored. The renderer (`Presentation::set_view`) recognises a
maximize and an un-maximize by the `zoomed` change that comes with the mode change (`ModeController::Change`):
- maximize: the room (`pinned_`, where anchor holds the canvas) is kept, and the canvas is held exactly there while the
  desktop zooms in (`holding`), so it grows in place. Once the zoom has landed (`land_maximize`) follow starts from that
  pose (`follow_ * aim = here`), with no jump, and its spring brings the desktop in front of the head. Without the hold
  the aim moves faster than the spring, so the desktop swung toward the room's centre and back (`focus_center_test`).
  A maximize straight into locked (the sticky choice) holds the same way, then blends (300 ms) onto the head's view.
- un-maximize: the canvas blends (300 ms) back to the room as it was left, instead of being pinned where it is, so in
  the overview every desktop is exactly where it was before the maximize, wherever the head turned meanwhile, from
  follow or from locked. A recentre while maximized moves the room as any recentre does: the overview comes back with
  the desktop that was maximized straight ahead.
- level: the maximized desktop's levelling roll, the one per-desktop rotation there is, fades to 0 with the zoom
  (`zoomed_placements`), so with the aim (`focus_direction`) it sits level and square to the glasses, in follow and in
  locked, and comes back with the zoom out. Only what is drawn changes. The supervisor's roll stays the overview's the
  whole time, so nothing has to be saved or restored and it cannot go stale: the `roll` frame carries the same value, a
  run ending while maximized saves the overview's roll, and so does the Ctrl+Alt+R backup, and the overview is bit for bit
  what it was. Every other desktop keeps its roll while one is maximized. A switch between maximized desktops is a cut:
  the new one is level at once and the one it left has its roll back. Nothing rotates a desktop while maximized: the
  level bars and pills are overview-only, R needs an open panel and no panel is open while maximized (one a desktop change
  would reopen stays shut, `ViewState.set_desktops`), and a press on a bar cut off by a maximize is dropped, every segment.
  Ctrl+Alt+R from a maximized desktop rises part way to its roll as it zooms out, then eases to level with the rest.
  Left/right and down/up are where a desktop is round you, not a rotation of it: the aim brings it straight ahead and they
  stay as they are. With head tracking off there is no aim: the roll still levels, but a desktop away from straight ahead
  stays there, off to the side, as it always has.
- the settings panel's Place tab, any desktop's: a change while maximized waits for the overview, where it can be seen
  (`ViewState.hold_placements`; Cody, 2026-11-03). Nothing moves or is saved yet and a toast says so (`PLACE_HELD_TOAST`,
  once per waiting change). It is held by desktop name, only what differs, so a desktop change meanwhile leaves the others
  alone. `status_reply` reports the held values, so the panel keeps showing them and a further edit, which sends every
  desktop's, adds to them; changing it back leaves nothing waiting. Any return to the overview applies it as given, gliding
  there (`GLIDE_MS`), and saves it (`_release_placements`, on Ctrl+Alt+Down, Ctrl+Alt+L, a pill or Ctrl+Alt+R, where
  it goes into the backup). A run ending while it waits saves it (`save_setup`), the next start being in the overview; a
  crash loses it.
- neither shows the viewing-mode pop-up (`StatusPopups::track_mode`); the start (anchor) and Ctrl+Alt+V's changes do.
- the zoomed desktop always fits the view (`keep_size` is for an anchor zoom only, and is off on the way back to the
  overview, where it would make a desktop resized bigger than the view jump to its size as the zoom out starts).
The panel's View row shows "Overview · anchor" or "Maximized · follow / locked", and its follow / locked buttons work
while maximized only (the controller sends one `mode_next` from the live view, and refuses otherwise). Ctrl+Alt+A
(`cursor_switch_animate`, anchor-mode cursor switch animation) was removed with this change: it only ever shaped an
anchor-mode cursor switch, and a maximized desktop is never anchor. Its bind, help row, toasts, op and saved setting
are gone; a `cursor_switch_animate` still in a saved layout.json or profile is ignored without an error, and the next
save drops it. This replaces overview-anchor-only (2026-11-03), where follow and locked had no overview and
Ctrl+Alt+Down there moved to the next desktop.

**Zoom.** Since 2026-11-02 zooming never moves a desktop round you, in any viewing mode (`zoomed_placements`). Each
desktop's orbit is corrected for its slot's move between the overview and the zoomed layout, so its centre keeps its
overview direction. The zoomed desktop's distance blends to 1 (`zoom_weight`), so it grows where it is until it fills
the view there, curvature kept, its roll fades to 0 the same way, and from halfway in it is drawn on top
(`zoom_draw_order`). Every other desktop's
distance scales with its tile (times the ratio of the slots' radii), a uniform scale about the eye, so it looks exactly
as in the overview. Zooming out returns every placement unchanged. The earlier 60° cutoff in anchor mode, the snap
straight ahead in locked and follow modes and the row the others used to follow are gone. Switching desktops while zoomed in
animates in three phases (`ViewAnimator`): zoom out of the current desktop (`position()` stays on it), a 300 ms turn at
the overview (`look()` 0 to 1: `look_turn` rotates the camera so the target's dragged direction comes where the current
desktop's was, upright), then zoom into the target while the turn eases back to none, so it lands where it was dragged.
A press while zooming out re-aims the switch; later presses queue. Zooming out mid-switch eases zoom and turn back.

**Centring the focused desktop.** In follow and locked modes the canvas is aimed at the focused desktop: the canvas sits
at the head (follow: the spring's head) times `aim_rotation` of the focused desktop's overview direction
(`focus_direction`, slot plus orbit), weighted by `focus_weight` (zoom plus look, at most 1). Zoomed in, the desktop is
straight ahead wherever it was dragged; in the overview the canvas centre is straight ahead as before. A switch zooms out
toward the overview's centre, pans to the target during the turn and stays centred on it while zooming in; anchor mode
keeps the camera-only `look_turn` instead. In anchor mode the room stays put, and a recentre puts the aim straight ahead
(zoomed in: the focused desktop; overview: the canvas centre). No placement changes.
- From 2026-11-02 (`zoomed_placements`, keeping a dragged desktop's direction) until 2026-11-03 (focus-centering), follow
  and locked glued the canvas centre to the head, so a zoomed desktop outside the middle slot, or dragged anywhere, stayed
  off centre however you turned. Head-tracking calibration was not involved: the recentre reference, the driver's
  calibration wait and the follow spring were correct; the regression test is `spike/tests/focus_center_test.cpp`.

The position is saved to the glasses model's profile like a size (half a second after the last change, and on
release), and the panel's **Place** tab shows it.

**Fields, units and installing.** A placement now has exactly four integer fields, in this order: `x`, `y`,
`distance` (percent, 10 to 200) and `curvature` (degrees, 0 to 120) (`geometry.PLACEMENT_KEYS`). A wire placement
object with any other keys, the old seven included, is malformed. Until 2026-11-02 a placement also had `yaw`, `pitch`
and `roll`, and `x` and `y` were a flat offset in percent of the desktop's own width and height, from −100 to 100: at
most one desktop from its slot. A profile saved before then still loads (`profiles._placement`,
`geometry.legacy_placement`): its yaw, pitch and roll are dropped, its distance and curvature are kept, and its `x` /
`y` now read as tenths of a degree, so at most 10°, about where they were. Install the renderer, the Python supervisor
and controller, and the panel QML together (see [Build and install](#build-and-install)). An older renderer rejects
the new supervisor's four-field `placement` and `placement_menu` frames. An older panel shows four-field placements as
flat, and the controller refuses the seven-field placements it sends. A Python-only rollback resets to flat, when the
profile loads, every placement the new version saved (they have four fields).

**Limitations.**

- The overview is always anchor (since 2026-11-03, maximize-follow), so the pills, panels and free pointer are anchor
  mode only. Maximizing centres any desktop in front of you, wherever it is placed: select it with Ctrl+Alt+Left /
  Right, then Ctrl+Alt+Up.
- For a desktop far round you, only its pill is in the pointer's reach. Its open panel, its corners and the desktop
  itself (moving the cursor into it, clicking it to select it) are not. The keyboard works: Ctrl+Alt+Left / Right
  selects it, Ctrl+Alt+L opens its panel, the arrows change it and R resets it to flat.
- Mixed installs, as above: the renderer, Python and QML must come from the same build. A Python-only rollback resets
  placements.

The pill, the open panel and the corners share one left-button bind (`resize_press` / `resize_release`, bound
only while the pointer is free) and never compete: the open panel is tested first, and a corner zone wins over a pill
where they meet. If the run dies while the pointer is free, the next left click runs the bind helper, which removes the
bind and shows the cursor again where free mode found it.

Until 2026-11-02 the move handle was a thin grey bar 4 px tall, drawn above each desktop. The glass pill under the
desktop replaced it; the wire op (`move_handle`) stayed the same and only the 4 px click threshold was new. Later the
same day the flat offset's move maths gave way to the orbit and guard above: its 4% snap, its allowance of 10% overlap
of the smaller desktop's area (judged on approximate overview rectangles) and its reach of one desktop width or height
are gone, and `resize.allowed_move` became `orbit.allowed_move`. After that every desktop was made to face you, and the
yaw, pitch and roll settings were removed. On 2026-11-03 bumping (`bump.py`) replaced `orbit.allowed_move`.

### Bumping desktops out of the way

Since 2026-11-03 (`monitor-collision-bump`, after Cody: "Upon resizing and moving windows, instead of it going through
other windows have it bump them out of the way smoothly if they collide"), every way of moving or resizing one desktop
in the overview pushes the desktops it runs into out of its way: the pill drag (`orbit.PillDrag`), the corner resize
(`free_pointer.OverviewPointer._apply_drag`) and the open panel's sliders and keys, R included
(`view.ViewControl._menu_update`). An Apply from the settings panel's **Place** section is the exception: it applies
exactly as given, even over another desktop (`ViewControl._on_placement`; while a desktop is maximized it waits for the
overview, see **Maximized and the overview**). It is all `tools/native_spike/bump.py`
(`Pusher`, `settle`), pure maths on the overview layout the renderer reports; without that layout, or with one stale
after a desktop change, an edit applies as given. Everything here is covered by offline tests only.

- **Collision.** Two desktops collide when their footprints (above) share a direction: `bump.apart`, a separating great
  circle through an edge of either polygon (the spherical form of the separating-axis test, exact for two convex
  polygons in one hemisphere; wider ones fall back to `orbit.overlap`). Touching is allowed. This is the view from your
  eye, so size, distance and curvature all count (curving keeps the arc's length: at the slot's distance a curved
  desktop is narrower, close up its edges come round toward you), and a far desktop that would show through a near one
  collides with it.
- **Walking.** The edited desktop (the mover) goes from where it is to where it was asked along the straight path,
  every field together (the apparent size, 100 / Distance, changes evenly), in steps of at most `STEP_TENTHS` (0.4°) of
  edge travel, or longer ones past `MAX_WALK_STEPS` steps, and strides while its bounding cone is clear of every other
  desktop's. However far one update takes it, it never jumps through a desktop. The short way round; half a turn is as
  far either way, so both are tried and the one that pushes least wins (R from directly behind you comes back round the
  free side).
- **Pushing.** After each step every desktop the mover covers is moved by the least turn round you or lift (one of
  ±x, ±y, whole tenths of a degree) that clears it, never toward the pusher: the minimum translation of a physics
  engine, on the sphere. So a desktop hit from the side goes sideways and one hit from above goes down. A pushed desktop
  that now covers another pushes that one the same way (cascading), and the train moves with the mover step for step.
  A pushed desktop stays where it was pushed; moving the mover back does not pull it back.
- **Stable.** A push is at most `PUSH_REACH` (3) times the step's own travel plus `PUSH_SLACK`, so nothing is flung; a
  desktop keeps the way it was last pushed while that costs at most `HYSTERESIS` (0.2°) more than the cheapest (no
  flip-flopping between sideways and down at a corner; a drag keeps one `Pusher` to its end); the same edit always
  gives the same result; a still pointer moves nothing.
- **No room.** A push may not take a desktop past straight overhead or underfoot (±90°, the placement bounds and the
  pointer's reach) or into the mover, and a step may make at most `MAX_PUSHES` (12) pushes. Then the mover stops at its
  last step that fitted (a resize at the largest whole Distance that fits) and what it pushed stays where it got to;
  an orbit move then slides along what cannot give way, sideways first or up and down first, whichever ends closer,
  the slide's corner first (as the old guard did). A full ring round you still turns: the last desktop comes round
  behind the mover, which has moved on by as much.
- **A changed or restored layout settles** (`bump.settle`, `ViewControl._settle`). The renderer reports the overview's
  layout when it changes: at a run's start, with a restored setup too, after a spacing change, and after a desktop is
  added, removed or widened. Desktops can overlap then with no mover to push them, also in an old saved layout, so the
  next `poll` pushes them apart, once no drag and no desktop change is under way. Desktop by desktop (the open panel's and
  the selected desktop first, so they stay put, then in order) each one that covers a desktop already gone through goes
  to the nearest clear spot, straight along x or y; the rest stay exactly where they are. It glides, and it is saved like
  any move. Once clear nothing moves, so it never jitters. The row itself is no overlap: two desktops both flat in their
  slots are left alone, though at spacing 0 their frames touch. A desktop with no clear spot anywhere stays where it is.
- **Overlaps that remain** (an Apply, until the next layout change; no room to settle) are tolerated: that pair may only
  get less deep, so nothing is ever stuck.
- **Smooth.** The pushed desktops go to the renderer in the same `placement` frame as the mover. While you drag (pill,
  corner, a held slider) the frames stream at the pointer's rate and the renderer eases every desktop alike
  (`ease_toward`, 35 ms), so they move with your hand. A discrete edit that pushed something (a key, a slider press)
  and a settle add `"glide": 240` (`view.GLIDE_MS`; `ipc.hpp` takes 1..`kMaxPlacementGlideMs` = 1000): the renderer
  then takes every desktop from where it is drawn to its new placement along one smootherstep curve over 240 ms
  (`view.hpp glide_toward`, `step_placements`), starting and landing without a jump in speed, never overshooting, all
  with the same timing, so they slide out of each other's way together. Any later frame without a glide, a roll change
  and a desktop change end it, eased on from where it got to. An edit that bumps nothing eases as before.
- **Saved like any move.** A pushed desktop is a placement like any other. A bump only changes `ViewState.placements`,
  which the normal save path writes whole (`ViewControl.flush`: half a second after the last change, at once on a
  release or a panel collapsing, an Apply at once), so a bumped position is saved with the layout and comes back with
  it.
- **Only from the handles.** Bumping hooks into the shared hit-zone contract's handles (`free_pointer.hit`, whose zones
  the overview branches agree on: content to the apps, resize at the edges and corners, moves by the pill and the open
  panel's header, rotate on the level bar). A drag started on a resize handle resizes through
  `OverviewPointer._resize_to` (`free_pointer.RESIZE_DRAGS`: a corner drag, or the contract's `resize` drag with a
  corner or edge `handle`), one on a move handle moves through `orbit.PillDrag`, and the open panel's sliders and keys
  through `ViewControl._bump`; all of them bump. A press on content is the apps' and never starts one
  (`test_bump.py` `HandleContractTests`, for every point round a desktop whatever its zone).
- **Super + drag is the window's.** Super + left or right drag is Hyprland's own `bindm` (movewindow, resizewindow): a
  window move or resize, never a monitor's. The free pointer's press bind has no modifier and Hyprland matches a bind's
  modifiers exactly, so with Super (or any modifier) held the press never reaches the run, and no monitor move, resize
  or bump can start. The run binds no Super chord and the press no `ignore_mods` (`test_bump.py` `BindRuleTests`). A
  monitor drag that began before Super went down stops when it does (`OverviewPointer.super_down`, every drag of a
  monitor: `MONITOR_DRAGS`): the two Super keys are bound in the free pointer's own table, non-consuming so they still do
  everything they did and with `ignore_mods`, to `resize_cancel`, so only while the left button is the run's, and every
  removal of the table takes them too (a dead run's cleans up on the next Super press, as on a click). The drag ends
  where it is and is saved as on a release; the button's later release finds nothing held, so it is no click. `hyprctl
  devices` reports no modifiers, which is why this takes a bind. The release bind has `ignore_mods` as well, so however
  a drag's button comes up it ends and never stays on the dot; with no drag held a release does nothing.
- **The roll** (roll-level icons) is ignored by bumping, as by the guard before it.

### Level bar and corner caps

Every desktop faces you, upright in the canvas frame. That frame is the head's orientation at the last recentre, so a
recentre with the head tipped to one side leaves every desktop rolled as you see it, and a pitched one leaves desktops
turned off to the side rolled. In anchor mode, while head tracking runs and no mode blend is in progress, the renderer
measures each desktop's roll from level (`view.hpp desktop_tilt`: the world's up from the pose feed, `pose.hpp
canvas_up`, projected onto the desktop at its centre) and reports it on the status socket as
`{"roll_geometry":{"tilt":[t,...]}}` (hundredths of a degree, counter-clockwise positive, `null` where it is not
measured; on change, at most every 100 ms). The tilt leaves out the levelling roll, so the supervisor owns that alone.

`free_pointer.OverviewPointer` keeps a levelling roll per desktop (not part of the wire placement; since 2026-11-03 saved
in the profile's `rolls` with the monitor setup and restored at start, see
[Remembered monitor setup](#remembered-monitor-setup-and-ctrlaltr)) and sends it with the bars in the `roll` op:
`{"roll":[r,r,r,r],"icons":["none"|"bar",...],"hover":-1..3,"side":"none"|"ccw"|"level"|"cw","active":bool}` (`side`: which
segment of the hovered desktop's bar; the wire still calls the bars icons). The renderer turns each desktop by its roll
about its own line of sight (`Placement::roll`, eased like a placement), so it still faces you; a maximized desktop's fades
out with the zoom (**Maximized and the overview**).

Every desktop in the overview shows a level bar, level, tilted or not measured (follow mode, no tracking): one segmented
capsule 148 x 26 px, 12 px above its top edge, clear of the selected desktop's 6 px border, the counterpart of the pill
under it (`glass.hpp level_bar_rect`). On a desktop narrower than 592 px the whole design scales down to a quarter of its
width, not under 120 px, and on one narrower than that to its width (`level_bar_scale`). Round each corner, with the bar
and scaled with it, is a corner cap (`corner_cap_rect`): a rounded L 8.5 px outside the edges, its legs 12 px long, the
resize corners made visible. All of it is tile px on the desktop's own surface, unlike the glass, so it moves, scales,
curves and rolls with its desktop: a resize, by a corner or by the Distance slider, keeps it the same share of the desktop,
and a move carries it along. The renderer draws it on the desktop's live placement, never on a held panel's press-time
surface.

Their pixels are one premultiplied image per theme colour (`render_handle_atlas`, drawn from signed distances with the M3
icons `rotate_left`, `level` and `rotate_right`, three pixels per tile pixel), holding every state of each, uploaded once
per theme and drawn with the panel's content shader, so a frame costs one texture and no new shader, and a theme switch
recolours them with the pills. Rendering it takes about 7 ms on the render thread, on the first overview frame and once
per theme switch; a frame after that does none of it. Only what the dot is on or holds lights up: the segment under it, a capsule in the theme's
primary (the primary bright while dragged), or the corner under it, whose cap grows to 22 px legs in the primary bright
(the `resize_handle` frame says which). They are drawn before every desktop, so any desktop's content covers them (a
bigger desktop after a resize covers its neighbours' bars and caps where it reaches over them), and the glass goes over
them all.

The hit zones round a desktop, from the inside out, shared with the other overview drags: its content (its apps); a band
6 px wide just outside every edge (the selected desktop's border, `free_pointer.LEVEL_EDGE_CLEAR` / `glass.hpp
kLevelEdgeClear`), left to the edge's resize zone, and the corners' resize zones round each corner, inside and out; then
the bar; then empty space. Hit region (`free_pointer.level_bar_segment`, the same as `glass.hpp level_bar_segment`; the
`glass_contract` ctest compares them): the drawn bar padded by 4 px, split at its thirds, the padding going with the
segment it borders, and only where nothing else is. A press on any desktop's content goes to that content (its apps:
clicks, drags and scrolling, with `anchor-no-click-maximize`), never to a bar, whoever's bar is drawn there; a resize
corner's zone (`free_pointer.corner_zone`), which reaches round the corner inside and out and covers the idle caps, and the
pills win too. The bar reaches a corner's zone only on a desktop about as narrow as it. What is drawn is what can be
pressed. Where two bars overlap (desktops moved over each other) the nearer desktop's wins. The bars and caps show and hide
with the pills.

A click on a turn segment turns the desktop that way: straight to level when level lies that way (more than 0.5° off, tilt
measured), else 2° further (`free_pointer.roll_click`). A press and drag on either turns it with the dot round its centre,
either way, up to 90°, resting at level within 0.5° (`roll_drag`); the bar is nearer the centre than the old corner
brackets were, so the first 4 px (`CLICK_PX`) of a drag are about a degree. The middle segment is a button: a click levels
the desktop (`OverviewPointer.level`, `level_roll`) and moves nothing else, and a press dragged off it does nothing. R on
its open pill (placement mode) puts it back flat in its slot and levels it the same way: the roll that cancels its
measured tilt, or no roll where the tilt is not measured. The desktops have no yaw or pitch to reset (they always face
you). A reset may move the desktop and so change its tilt, which the renderer reports only when it changes, so its
measured tilts in the next half second (`LEVEL_SETTLE_S`) level it again; pressing a segment of its bar takes over at once.
Super going down stops a press on any segment, as any monitor drag.

Until 2026-11-03 a desktop showed only the arrow that levels it, only when more than 0.5° off and measured, and a drag was
clamped between its roll at the press and level, so a desktop could only ever be turned toward level; two arrows above the
top corners then became arrow brackets round them, and those and the dim L-bracket inside a hovered corner became the
level bar and the corner caps, Cody's pick of ten mockups (`tools/handle_mockups`).
Hit testing and the cursor mapping use the rolled surface; the pill drag and bumping ignore the roll.

### Help card pages

The help card (Ctrl+Alt+H) sizes its text from the output height: row text 30 px at 1920x1200 @1.25, where today's 13
rows sit on one page, stepping down to 18 px on smaller outputs. Its Ctrl+Alt+Down row reads "Overview: any desktop;
pills, bar, corners" since 2026-11-03: a click on a desktop in the overview selects it, and only Ctrl+Alt+Up ("Maximize the
selected desktop") maximizes. It keeps the card chrome (padding, border, radius,
dimmed colours) and is laid out again whenever the output is reconfigured.

Rows are grouped in sections (`view.HELP_SECTIONS`: moving around, modes, placement and video, this card), sent as the
`help` frame's `sections` (row counts) and drawn with a faint rule between them. When even 18 px text cannot fit every
row, the card keeps 18 px and splits the rows into pages (`hud.cpp` `paginate`): each page takes what fits the height,
a section moves whole to the next page when it fits one, and a section taller than a page is split. Every page has the
same card size; the footer gains a "2 / 3" indicator between drawn left and right arrows and the hint "Ctrl+Alt+PgUp /
PgDn or click an arrow for more". There is no row limit beyond the wire's `kMaxHelpRows` (64); the old 15-row cap is gone.

The renderer owns the page, because only it knows how many pages fit. **Ctrl+Alt+PgUp / PgDn** (`help_prev` /
`help_next`, run binds; they do nothing while the card is closed) send a `help` frame with `"turn": -1` or `1`, and the
renderer flips, wrapping round. Showing a closed card always starts on page 1. Page turns count as input for the 30 s
idle close.

Free-pointer clicks: the card is head-locked and the overview's canvas world-locked, so while the card is up in the
overview the renderer sends a `help_geometry` status line with the card and its prev arrow, page indicator and next
arrow mapped onto the canvas under the current head rotation (bounding boxes, canvas px; re-sent at most every 40 ms
while the head moves, and `{"help_geometry":null}` when the card closes or the view zooms in). The supervisor treats the
open card as the pointer's top layer: a cursor inside a desktop that moves onto it, or that it opens or moves over,
turns into the free dot (the
renderer draws the dot over the card), the dot enters a desktop again only 4 px clear of the card, a click on the left
arrow is `help_prev`, on the right arrow or the indicator `help_next`, and anywhere else on the card nothing.

Offline only: none of the paging has been tried in the glasses.

### Placement pills

You can also place desktops from inside the glasses, without the settings panel. In the overview, each desktop has a
small pill in the theme's colour under it (Material Design 3; see The look below). Click a pill with the free pointer's dot, or press **Ctrl+Alt+L** for the selected
desktop's pill, and it expands into that desktop's own placement panel. (Ctrl+Alt+P was already taken by pose
prediction.) Ctrl+Alt+L again, Escape, a click on the panel's top strip (the pill itself) or a click outside the panel
(on a pill or in empty space; a desktop is not outside, the panel goes there) collapses it. Only one pill is open at a time: opening another collapses the first, and Tab / Shift+Tab move the open
panel to the next / previous desktop. Pressed while zoomed in, Ctrl+Alt+L first zooms out to the overview so the pill is
visible; the pill only starts growing once the overview is reached, so the whole expansion is seen. Zooming in
(Ctrl+Alt+Up) closes an open panel first, saved, with its keys released. Everything here is covered by offline tests
only. It has not been run in an isolated VM or tried in the glasses.

**The view shifts while a panel is open.** The panel hangs under its desktop, further than the view reaches with your head
level, so the renderer pitches the camera down just enough for its bottom edge to end 12 canvas px inside the view
(`glass.hpp` `glass_panel_shift`: 1.7° for the middle desktop of the default row and 2.0° for the outer two, at most 15°), easing over the panel's 260 ms morph and back
when it closes. It covers looking straight at the desktop, and looking level at its height from straight ahead, where the
row's outer desktops sit lower in the view. It is worked out from the panel's size, not from your head, so it stays put while
you look round and does not change as the panel moves along the row. It is a camera offset at draw time (`glass_view_shift`):
nothing moves on the canvas, so the supervisor's hit testing needs no change, and the help card's hit box is worked out with
the shifted view. Desktops, pills, panel and dot shift together. The background does not, since it is world-locked.

**An open panel follows the focus.** When the selection moves to another desktop in the overview (Ctrl+Alt+Left / Right, or the
pointer selecting one), `ViewControl.follow_focus` opens that desktop's panel and closes the old one, saved at once, as Tab does:
the modal keys stay, the highlighted setting stays, an open list closes. With every pill collapsed a selection change opens nothing.
A key that moves no selection (the end of the row) moves no panel. Tab / Shift+Tab still move the panel without moving the selection.

This replaces the earlier placement menu: a head-locked card in the middle of the view, with a mouse mode (Ctrl+Alt+M)
that drew its own pointer over the card. The card, Ctrl+Alt+M, mouse mode, the renderer's `menu_pointer` op and its
`menu_geometry` status line are gone. The overview free pointer does that job now.

**The placement panel.** It is drawn on the desktop's own surface, so it faces you with its desktop and a curved desktop
curves its panel. It is 480 × 220 canvas px with 18 px corners, centred under the desktop, its top where the pill's top
is (10 px under the desktop). The top strip, 42 px tall, shows "XR-n" and "Placement" with a small grabber where the
pill was. That strip is still the pill: drag it to move the desktop, click it to collapse the panel. Below it are the
four settings, in order: **Left/right**, **Down/up**, **Distance** and **Curvature** (`kPlacementMenuFields`; there were
seven, with Yaw, Pitch and Roll, and the panel was 322 px tall, until 2026-11-02). Each row has its label, a slider and
the current value with units: whole degrees for Left/right and Down/up, percent for Distance, degrees for Curvature. The
selected row sits on a faint band in the theme colour. The slider's ends are the setting's limits, the same as the settings panel's
sliders, and the stretch from the flat value to the knob is in the theme colour too. The footer lists the keys (↑↓ field, ←→ adjust,
Tab next, R reset). Desktop and setting selection wrap round. R puts every setting of the open desktop back to flat and
levels it (`free_pointer.OverviewPointer.level`).

**With the pointer.** Press on a slider (its row, from 12 px before the knob's lowest position to 12 px past its highest,
`PANEL_KNOB_LO` / `PANEL_KNOB_HI` and `PANEL_SLOP_PX` in `free_pointer.py`: about 5 px past each end of the drawn track)
and that setting becomes the selected one and jumps to the pointer. Hold and drag to move it live; the desktop follows.
The drag is measured on the panel as it was at the press, so a setting that moves its own desktop does not chase
itself. Until the release the renderer keeps drawing the panel on that same press-time placement while the desktop
moves live, so the knob stays under the dot. On release the panel joins its desktop again. Release ends the drag and
saves. A key that collapses the panel or moves it to another desktop during the drag ends what the drag does. A press
elsewhere inside the panel does nothing and the panel stays open. A press anywhere else while a panel is open closes it
first (saved, its keys released), then carries on as usual: on another desktop's pill it starts that pill's click or
drag, on a desktop it selects that desktop (no maximize), and in empty space it does nothing more. Pressing the open desktop's own
corners resizes it without closing the panel. A click inside a desktop while the real cursor is there (desktop mode, not
the dot) goes to the window under it and, since 2026-11-03, also closes the panel (the run sees it through the
non-consuming bind), so the panel's modal keys stop taking what you type into that window.

**With the keyboard.** The keys are in the keybind table above. They are bound only while a pill is open, so the arrows,
Tab, R and Escape type normally once it is closed. Opening or closing a pill with the pointer installs and removes the
same keys as Ctrl+Alt+L.

**Expanding.** The pill morphs into the panel over 260 ms and back over 200 ms, eased. The panel's text fades in over
the last quarter of the expansion. Moving the open panel to another desktop collapses one pill while the other expands,
the new one drawn on top. An expansion that starts while the view is zoomed in or still zooming out is held where it is
until the overview is reached (`GlassPills::frame`), so a panel opened with Ctrl+Alt+L from a zoomed-in view does not
pop in nearly full size; a collapse is never held.

**The look.** Material Design 3 (`spike/include/xr/material.hpp`). The colours are an M3 dark scheme whose seed is the
highlight border's and glow's colour (`general:col.active_border`, sent as the `style` frame at the start of a run and
after every config reload, which `omarchy-theme-set` does), so switching the theme recolours the pills, an open panel,
the level bars, the corner caps, the pop-ups, the help and video cards, the border and the glow together in the
running session; the renderer's status reports the colour it has (`theme.accent`) and how many switches reached it
(`theme.updates`). It is M3's *fidelity* variant: the primary palette keeps the border colour's own hue and chroma, and
the primary container is the border colour at its own tone, pressed into tones 28–82 so a near-black border still shows
on the see-through OLED and a white one does not glare. So a pale theme gives a pale pill, a dark one a darker pill, the
grey themes (Vantablack, White, Lumon, Kanagawa) each give their own grey, and grey themes give a grey scheme. The other
roles take M3's dark-scheme tones (primary 80, on primary 20, secondary container 30, surface containers 4–22, on surface
90, outline 60, outline variant 30), in OKLCH hue and chroma with CIE L* tone. Every role is then scaled by 191/255, so
the brightest possible role lands exactly on the OLED cap and nothing drawn from the scheme passes it.

The collapsed pill is a flat tonal capsule in the primary container at elevation 1, 14 px tall and 24% of the desktop's
width (64–160 px). Hovering it lays M3's state layer over it (its on-colour at 8%) and lifts it to elevation 2; dragging
it, 16% and elevation 4. The open panel is a surface container (high) with a 1 px outline-variant edge at elevation 3 and
M3's drag handle (on-surface-variant at 40%); hovering or dragging its header puts the state layer on the header strip
only. Its rows are an M3 list of sliders: labels on-surface, values on-surface-variant, the selected row on a fully
rounded secondary-container band, each slider a primary track (24% where inactive), fill and handle, the selected
handle with a 10% focus halo. The desktop row's buttons are fully rounded filled tonal buttons with a Material icon
before the label (width, add, remove): the primary while Ultrawide is on, the error container while Remove asks for its
second click, M3's disabled look (on-surface at 12% / 38%) when they do nothing now; with the pixel font they have
labels only. A level bar is the pill's primary container with on-primary-container icons, its lit segment a capsule in the
primary with on-primary icons, the primary bright while dragged; a corner cap is the primary, the primary bright when lit. The pop-ups and the help and video cards are extra-large
rounded (28 px) surface containers with an outline-variant edge: a pop-up's new state in the primary, the help card's
dividers in the outline variant, the video's progress M3's linear indicator. The renderer draws the glass with one GLSL
fragment shader (`glass_fragment_shader` in `spike/src/glass.cpp`, one `style` uniform block from `glass_style`) whose
CPU mirror (`glass_shade`) is checked against it on a real GLES context. On the glasses' micro-OLED panels black is
see-through, so the glass is mostly its own colour; the panel stays dark behind its text. Nothing on it moves on its
own. Pills and panels are drawn only in the overview; any zoom hides them. Whether the look reads well on the OLED
panels has not been checked in the glasses.

Each change moves the desktop straight away. There is no Save or Apply step. Changes are saved to the glasses model's
profile half a second after the last change, at once when a slider is released, and again when the panel closes. The
placement panel changes placement only. It does not change the run's consent, capture or the settings panel's buttons.

**Wire.** The supervisor owns the state and the hit testing. `tools/native_spike/free_pointer.py` mirrors the renderer's
pill and panel geometry from `spike/include/xr/glass.hpp`, and the `glass_contract` ctest compares the two. Two
display-only frames drive the renderer:

- `placement_menu` `{open, desktop, field, placements}` now means "this desktop's pill is expanded into its panel";
  `open: false` collapses it. `field` is one of `x`, `y`, `distance`, `curvature`, and each placement object has exactly
  those four keys (see [Fields, units and installing](#moving-desktops-in-the-overview-the-pill-under-the-desktop)).
  While the free pointer holds one of the open panel's sliders the payload also carries `surface`, the open desktop's
  placement at the press: the renderer draws the panel on it (the desktop itself follows its live placement) until a
  frame without it. `surface` is only valid with `open: true`.
- `move_handle` `{visible, desktop, hover, active}` shows the pills under every desktop and brightens the hovered or
  dragged one. The renderer draws an open panel even when `visible` is false.

A malformed payload of either is dropped and the run goes on. The renderer now rejects `menu_pointer` like any unknown
op, and its status `placement_menu` object is `{open, desktop, shown, hidden, renders}` (no `visible` or `fits`).

The help card and an open placement panel protect the glasses' micro-OLED panels from image retention (a faint copy of
bright, static text that stays for a while after it closes). Their text and accent are dimmed to about 70% and 75% of
full brightness. Neither moves on its own while shown. Both close by themselves after 30 s without input: a run key, a
pill key, a free-pointer click or slider press, moving the dot while a panel is open, or a placement change in the
settings panel. The help card's footer says so. Typing in a window doesn't count. Ctrl+Alt+L and Ctrl+Alt+H reopen them.
The timeout is `OVERLAY_IDLE_CLOSE_S` in `tools/native_spike/view.py`. Its copy `kOverlayIdleCloseS` in
`spike/include/xr/hud.hpp` feeds the footer note, and a test keeps the two equal.

## Tests

Bare `python -m unittest` from the root finds nothing. Run each suite explicitly:

```sh
python -m unittest discover -s tests/probe -v
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/xr_controller -v
python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -v
QT_QPA_PLATFORM=offscreen /usr/lib/qt6/bin/qmltestrunner -input omarchy-plugin/cody.viture-workspace/tests
ctest --test-dir build/release --output-on-failure
```

The `qmltestrunner` line runs the panel's QML tests offscreen: `tst_model.qml` (the model, the consent box and the
editors) and `tst_launcher.qml` (the pre-start page: one big Start, the logo, the startup options closed until asked
for, the *Return windows to* choice that Start waits for, and the desktop preview tiles actually drawing).
`test_branding.py` checks that the product shows as XR Workspace (window title, desktop entry, adoption's panel title,
manifest, toasts, logo) while install and recovery identifiers keep their `viture-workspace` names.

The glass pills add four ctest tests: `glass` (geometry, morph, look under the 191/255 cap, panel content, the expand
animation, held until the overview is reached, the draw order and a held slider's surface), `glass_render` (the shader
against its CPU mirror over black and over a bright desktop, and the cap over black, also in theme colours, on a
surfaceless EGL context; it reports skipped, exit 77, where EGL or GLES is missing), `glass_contract` (runs
`glass_geometry_dump` and checks that `tools/native_spike/free_pointer.py` uses the same pill, panel and level bar
geometry) and
`glass_theme` (for every Omarchy theme's border colour: the pill's hue and brightness, the state layers and the cap in
every state, the panel's parts, the level bar and corner cap colours, and an open panel re-rendered once on a theme
switch). `material` checks the M3 palette itself for every theme (each role's tone, the seed's hue, greys staying grey,
contrast between each role and what it sits on, the cap), the state layer, shape and elevation tokens, and the icons. `tests/native_spike/test_pill_theme.py`
covers the supervisor side: the theme colour reaches the renderer as the `style` frame, with a pill open too, and
through the installed wiring (Supervisor, VideoControl, real sockets) on every `configreloaded`. `menu_geometry_contract` and
`placement_menu_style` were removed with the head-locked menu card. `help_card` (`spike/tests/help_card_test.cpp`) took over the help card's fit checks: the real rows fit 1920×1200 @1.25
on one page at full size (and with three more rows still on one page); 1 to 64 rows page without clipping at every
scale from 1 to 2 (every row on one page, in order, pages the same size, nothing in the padding); sections stay whole
when they fit a page; the overlay turns and wraps pages and reopens on page 1; the arrows sit in their click rects and
`help_geometry_json` maps them onto the canvas; text only shrinks as the output gets shorter, and the card is laid out
again when the output size changes. `tests/native_spike/test_help_pages.py` covers the page keys, the `sections` /
`turn` frame, the `help_geometry` line and the free pointer's clicks on the card.

The background adds these tests. `environment` (`spike/tests/environment_test.cpp`): the backgrounds' table, the line colour
(the theme's own, scaled only for the cap or to show at all), the fade, the levelled world-locked view from any recentre
reference, the `environment` frame over `tests/native_spike/fixtures/environment_payloads.json`, which
`tests/native_spike/test_environment.py` runs the supervisor over too, and the panel's fields on a `placement_menu` frame.
`environment_render` (`spike/tests/environment_render_test.cpp`) draws the backgrounds with the real compositor on surfaceless EGL behind
three synthetic desktops at 1920x1200: None is bit for bit a compositor that never had it; with Grid the desktops' pixels do not
change and the glow leaves no black halo; looking every way round nothing passes 191/255, most of the view stays dark and the sky
stays black; the horizon sits where the pitch puts it, a whole turn gives the same frame, and in locked mode the grid turns while
the desktops do not; every lit pixel has the theme colour's proportions, on the frame after a theme switch too; the fade and the
brightness, black staying black at each end; the seamless clock wrap; and behind a 180° video the picture is as it was without it
and the rest of the view is exactly the grid. It also writes preview PNGs (zlib) to `build/release/environment-previews/`, or
to the directory given as its argument, the panel with its Background list open and its Brightness row selected among them.
`video_render` adds VideoMode's side with a real decoded clip: one call per output eye, and none behind a 360° picture. `glass`
covers the panel's new rows, `glass_contract` their geometry and the table against `free_pointer.py` and `environment.py`.
`gl_bench`'s `*_env` scenarios add the grid to the fresh-compositor comparison, so it can leave no GL state behind, and to
`--bench`. `capture_e2e_environment_*` send the real renderer its `environment` frame on both GL paths and check that frames keep
flowing and no desktop goes blank. `test_environment.py` covers the setting, the key, saving and restoring, the panel's dropdown,
switch and slider down to their hit tests, and the quality menu's row.
Smoke gets its own section there: the desktops' pixels do not change, nothing passes the cap every way round, clear air stays
exactly black at any brightness, every lit pixel has the theme's hue, no seam shows behind you or straight up or down, a whole
turn gives the same frame, a frame at 120 Hz changes under a level on average, and neither the loop nor the clock's wrap shows.

`capture_e2e_open_panel` opens a panel in the real renderer and requires the view to have shifted (`view_shift_mrad` in the
renderer's exit report); every other `capture_e2e_*` run requires it to be 0. The shift's geometry and ease are in `glass`.
`tests/native_spike/test_panel_follows_focus.py` covers the panel following the selection.

The placement round you, with every desktop facing you (see
[Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)), adds
`tests/native_spike/test_orbit.py` for `tools/native_spike/orbit.py`, and the `placement_contract` ctest, which compares
`orbit.place_point` with the renderer's `place_point`. `test_free_pointer.py`, `test_glass_pills.py`,
`test_grab_pill.py`, `test_placement_menu.py`, `test_profiles.py` (older profiles that still carry yaw, pitch and roll)
and the `view`, `hud`, `glass`, `ipc` and `placement_menu_ipc` ctests were updated for it. Bumping
([Bumping desktops out of the way](#bumping-desktops-out-of-the-way)) adds `tests/native_spike/test_bump.py`, and the
`view` (the glide) and `ipc` (the frame's `glide`) ctests. All of these are offline.

The key bind panel adds `tests/native_spike/test_keybinds.py` (chords and labels, the saved file, plain `hyprctl binds`
parsing and counting the run's own binds out, conflicts and which binds go off, every panel key, live rebinding through a
fake compositor client, refusals and toasts, the idle close and the change timeout, the help card, the bind helper's
`binds_key`, the control wire, the generated Lua and the eval limit with the longest keys, the start-up check in
`managed.view_controls`, and the payload limits against `binds_panel.hpp`) and the `binds_panel` ctest
(`spike/tests/binds_panel_test.cpp`: ten rows fit 1920×1200 @1.25 and every scale up to 2 fits, also during the fade-in
drop; nothing passes the cap and everything is premultiplied; the picked, conflict, custom and capture rows use their M3
roles; the size depends on the conflicts only; long text stays out of the padding; the scroll window; the overlay's
fade, redraws, viewport and theme colour). `XR_BINDS_PANEL_DUMP=dir` makes it write the sample cards as PPMs.

The pill's Resolution dropdown adds `tests/native_spike/test_resolution_dropdown.py` (the size table against the
renderer's, the nearest-scale rule, plans, the payload, a pick end to end through the fake broker into the saved
profile, pointer hits, and every size through the C++ request validator when the ownership build is present),
`tests/native_spike/test_fit_windows.py` (floating windows pulled inside a desktop that shrinks: the plan, the worker,
and the generated Lua in a real interpreter when one is installed) and checks in the `view`, `glass`, `glass_contract`,
`placement_menu_ipc` and `capture` ctests; `capture_e2e_churn_resize_*` now also require each resize to wait
(`resize_settles`) and, on the GPU paths, its new shape to ease (`shape_eases`), and `capture_e2e_churn_bounce_*` that a
reload's bounce does neither.

Window memory adds `tests/native_spike/test_window_memory.py` (capture, the file, matching, the plan, the tracker: debounce,
off-loop reads, the last capture, an empty capture, a start, parking and coming home, the view's events and Super drags),
`test_window_memory_lua.py` (every generated eval in a real Lua interpreter against a stand-in for Hyprland's API, with layouts,
columns, focus-only layout messages and swap as the 0.56.2 source has them; batches under 4096 bytes; hostile values; capture,
Stop's restore and the next start round trips) and `test_window_memory_run.py` (`_run_owned`'s order, `view_controls`' last
capture, Ctrl+Alt+R and a pill's Remove and Add through the real `ViewControl` and `DesktopManager`). The stand-in is written from
reading the source, so a wrong reading would pass there: the glasses steps W1–W19 are what check it.

Check that this project's run, send-window and placement-pill keys clash with nothing in your Hyprland config (exit 0 no clash, 1 clash, 2 could not check):

```sh
python -m tools.native_spike.bind_check
```

It loads `~/.config/hypr/hyprland.lua` with `Hyprland --verify-config`, which runs the Lua config without starting a compositor, in a temporary HOME holding a copy of `~/.config/hypr` and the Omarchy state the config reads. It records every chord the config binds, runs this project's generated bind Lua the same way, and compares them. The window memory's generated evals and each dispatcher they call load there too, so Hyprland's own loader and argument checks see them (`window_memory.verify_lua`). A digit and its keycode (`1` and `code:10`, the way Omarchy binds workspace digits) count as the same key. It never contacts the running compositor. Hyprland 0.56.2's `--verify-config` crashes on exit when a config has made timers, so the check replaces `hl.timer` with an inert stand-in while it loads; it has no event manager either, so a submap switch would crash it, and the check makes `hl.dispatch` inert while this project's Lua loads (keeping the key bind panel's submap switch in Lua, so the panel's own check that it entered its submap still runs). The run and send keys are the key bind panel's saved ones (`--keybinds FILE`), and the panel's submap, catch-all and key subscription are loaded too.

Tests use synthetic collectors, fake sockets and temporary directories. They never open a real display or device, and their output is never published as host evidence. Build and link success does not show that the plugin loads at runtime against the installed compositor.

## Safety boundaries

- Nothing touches the glasses or the live desktop without explicit consent for that run.
- Intent is journaled before any mutation, and actual state is read back before a step counts as confirmed. Recovery keeps ambiguous or user-modified resources and reports incomplete recovery instead of deleting them.
- Capture is limited to the run's own `XR-n` outputs and stays in memory. There is no general desktop capture, input injection or vendor SDK.
- Display-mode switches are runtime only. Saved monitor configuration is never edited.
- The send-window keys (Ctrl+Alt+G, 1-3, B) move only the focused window, by address, onto the active regular workspace
  of one of the run's XR outputs or of the run's physical fallback. That one Hyprland dispatch is allowlisted; pinned
  windows are refused, so recovery can always move the XR desktops' windows back.
- When a change from a glass pill shrinks a desktop, its floating windows that would stick out are moved and, if need
  be, resized by address to stay on that same output (`fit_windows.py`). It touches no window on any other output, uses
  generated Lua from validated addresses and whole pixels, and runs only while the run is live.
- Run binds use Ctrl+Alt chords. The only unmodified binds are modal: an open placement pill's keys, and the left
  button while the overview's free pointer is outside the desktops (pill click and move, panel sliders, corner resize,
  click to select). Both are removed when no longer needed. The free pointer hides the cursor and re-centres it with
  fixed Lua, and shows it again when the pointer goes back into a desktop, when the overview ends, at run end, or on
  the first click after a dead run. It warps the cursor only onto the run's own XR desktops.
- The isolated VM test path (`isolated-run`, `isolated-recover`) needs separate VM consent and refuses to run on the physical session.
- The unified overview cursor adds unmodified binds in the overview, in the free pointer's bind table and removed with
  it (see [The unified overview cursor](#the-unified-overview-cursor)): over a desktop a non-consuming left-button
  press/release (the window still gets the click); while the dot is free, the right, middle and side buttons and the
  wheel. With the run's binds, non-consuming Super + left / right presses and the release of those buttons
  ([Super drags](#super-drags)); they never stop the desktop's own binds. It sets `binds.scroll_event_delay` to 0 while free and puts it back with those binds. It warps and holds the
  cursor only on the run's XR desktops' monitors (the one under the dot in the overview, the selected one zoomed in),
  and gives monitor focus only to them.

## Host diagnostic (`xr_probe`)

The read-only probe from the feasibility phase. It needs Python 3.10+ and only the standard library, and it installs nothing.

```sh
python -m tools.xr_probe --list-sessions
python -m tools.xr_probe --instance INSTANCE --output-dir OUTPUT_DIR
```

Pass the literal identifier from `--list-sessions`. Only a session classified `physical` can be selected. Without `--instance`, collection runs only when there is exactly one physical candidate and no unknown one. The probe writes sanitized `host-report.json` and `host-report.md`: no window titles, serial numbers, process or socket identifiers, raw errors or environment.

| Exit | Meaning |
| --- | --- |
| `0` | Reports written with successful USB/DRM inventories, or listing/help succeeded. This is not a support or performance verdict, and missing glasses can still return 0. |
| `2` | Bad arguments, ambiguous or nonphysical selection, an existing report without `--overwrite`, an unsafe target, or a held lock. |
| `3` | Discovery, required inventory, host collection or report writing failed. A sanitized partial report may still be written. |

Existing reports are refused unless you pass `--overwrite`. Commit the old evidence first, or write later collections to a separate directory. The two report files are replaced one at a time, not as one transaction, so an interrupted run can leave a mixed pair. If rollback fails, backups stay in the `.xr-probe.lock` directory; recover them before removing it.
