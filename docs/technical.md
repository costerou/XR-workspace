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
| Overview free pointer (the mouse leaves the desktops in the overview: drag a desktop's pill to move it, its corner to resize it, click it to maximize it) | Offline tests only (`tests/native_spike/test_free_pointer.py`, `test_resize.py`, `test_grab_pill.py`; `spike/tests/free_pointer_test.cpp`, `resize_handle_test.cpp`). Not yet tried in the glasses; its new cursor Lua has not been run under `Hyprland --verify-config`. Steps F1–F22 in [placement-menu-test-plan.md](placement-menu-test-plan.md); they replace the earlier corner (R) and bar (P) steps, which assumed the pointer was confined to the desktops. The move steps now use the glass pill under the desktop instead of the old bar above it. |
| Glass placement pills (a Material Design 3 tonal pill under each desktop in the overview; a click or Ctrl+Alt+L expands it into that desktop's placement panel; replaces the head-locked placement menu card and its Ctrl+Alt+M mouse mode) | Offline tests only. Renderer (ctest): `glass` (geometry, morph, look under the 191/255 cap, panel content, animation), `glass_render` (the GLSL shader against its CPU mirror on a surfaceless EGL context, no display), `glass_contract` (the renderer's pill and panel geometry against `free_pointer.py`), and the updated `hud`, `placement_menu_ipc`, `overlay_residue` and `resize_handle`. Supervisor: `tests/native_spike/test_glass_pills.py`, `test_free_pointer.py`, `test_placement_menu.py`, `test_view_menu_wiring.py`. Not run in an isolated VM and not tried in the glasses: the look, readability on the OLED panels, the expand animation and how clicks and drags feel are unverified. Steps G1–G38 in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Desktops anywhere around you, always facing you (3DoF placement: Left/right and Down/up turn a desktop round your eye; every desktop faces the eye, upright, and the yaw, pitch and roll settings are removed; a pill drag keeps the desktop under the dot, and in anchor mode the dot, and so the drag, reaches all the way round you; desktops never overlap as seen from the eye) | Offline tests only: `tests/native_spike/test_orbit.py`, `test_free_pointer.py`, `test_glass_pills.py`, `test_grab_pill.py`, `test_placement_menu.py`, `test_profiles.py`; ctest `view`, `hud`, `glass`, `ipc`, `placement_menu_ipc`, `placement_contract`. Not run in an isolated VM and not tried in the glasses: how the side desktops look turned toward you, how the drag past the view's edge feels, re-grabbing a pill you cannot see the dot for, the guard's stop and slide, and comfort with desktops behind or above you are unverified. The wire and the profile changed: a placement has four fields (`x`, `y`, `distance`, `curvature`), and Left/right and Down/up are tenths of a degree where they were percent of the desktop's size. A placement saved before 2026-11-02 reads as at most 10°, about where it was, and loses its yaw, pitch and roll. The renderer, the Python supervisor and controller, and the panel QML must be installed together (see [Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)). Steps O1–O19 in [placement-menu-test-plan.md](placement-menu-test-plan.md). |
| Roll-level icons (every desktop in the overview has two small curved arrows (no button behind them, in the theme's M3 primary) above its top corners, counter-clockwise left and clockwise right; a click turns it that way, straight to level when level lies that way (in anchor mode, judged against gravity from the head tracker), else 2°; a drag from either rolls it either way) | Offline tests only: ctest `roll_level` (the roll about the line of sight, the tilt from level, the icon's size, side, arrow and colours, the `roll` frame), `glass_contract` and `placement_contract` (the icon rect and the rolled surface, renderer against supervisor); `tests/native_spike/test_roll_level.py`. Not run in an isolated VM and not tried in the glasses: whether XRLinuxDriver's frame is gravity-level on the Luma Ultra (the tilt assumes its z axis is up), how the icon reads on the OLED panels and how the drag feels are unverified. See [Roll-level icons](#roll-level-icons). |
| Monitor scale from the glass pill (the open panel's Scale dropdown under its sliders, in Material Design 3: an exposed dropdown and its menu, in the buttons' 18 px font; 100, 125, 150, 175, 200, 250 or 300% (250 and 300 since 2026-11-03), only the steps that divide the desktop's resolution exactly; a pick reshapes that XR desktop's Hyprland output scale live through the broker, like Ultrawide, and the profile keeps it for the next start; `desktops.SCALE`, `ViewControl.scale_box` / `scale_pick`) | Offline tests only: `tests/native_spike/test_scale_dropdown.py` (steps, plan, payload, the pick end to end through the fake broker into the saved profile, pointer hits), ctest `glass` (layout, the box, its indicator and the list in M3 roles), `material` (the drop-down arrows), `glass_contract` (box and list geometry, the steps), `placement_menu_ipc` (the `scale` / `scales` / `picker` fixture cases), the panel's `tst_model.qml` (its desktop editor offers the same steps). Not run in an isolated VM or in the glasses: how the list reads on the OLED panels (seven 20 px items over the sliders), how windows re-lay out after a scale change and how 250% / 300% look are unverified. The panel grew one row (292 px tall). The broker's bound (`spike/ownership/registry.cpp` validDesktop) now takes 2.5 and 3, so the ownership plugin, renderer, supervisor, controller and panel must be installed together. |
| Live desktops from the glass pill (Ultrawide, Add new, Remove; 1..4 desktops; `tools/native_spike/desktops.py`, the renderer's `desktops` control op, the broker's `reshape` op) | Offline tests only: `tests/native_spike/test_desktops.py` (plans, journaled broker steps with the real journal and the C++ request validator, the supervisor loop, pointer hits, profiles), ctest `ipc`, `glass`, `glass_contract`, `capture`, `ownership`. Not run in an isolated VM or in the glasses: whether Hyprland keeps a capture session through a reshape (the renderer restarts it if not), the new panel row's look, and the guard swap are unverified. The ownership plugin, renderer, supervisor, controller and panel must be installed together. |
| Seamless workspace layout switches (master / scrolling / floating; Omarchy's toggle rewrites a layout file the config requires, so each switch is a Hyprland config reload) | Offline tests only: ctest `ownership_reload` runs the plugin's `keptThroughReload` against the pinned reload sequence (clear, parse, `ensureMonitorStatus`): during a run a reload keeps the XR-n rules and the rule in effect for every output the config does not name (the glasses' session mode), so it modesets nothing, and the config still wins wherever it names an output. The plugin applies it by hooking `CMonitorRuleManager::clear()`; that hook has not been loaded in an isolated VM or in the glasses. Takes effect only with the new `libxr-ownership.so` installed. |
| Material Design 3 look (the pills, the placement panel, the roll arrows, the resize handle, the pop-ups and the help and video cards in one M3 dark scheme seeded by the Omarchy theme colour, every role under the 191/255 OLED cap; see [The look](#moving-desktops-in-the-overview-the-pill-under-the-desktop)) | Offline tests only: ctest `material` (the palette for every shipped theme: M3 tones, hue, contrast, the cap; tokens; icons), `glass`, `glass_theme`, `glass_render` (the new shader against its CPU mirror on a GLES context), `hud`, `help_card`, `roll_level`, `video`, `video_render`, `gl_frame`. Not run in an isolated VM and not tried in the glasses: how the tonal surfaces, the 8% / 16% state layers, the elevation shadows and the dimmed roles read on the OLED panels is unverified. |
| Not built yet | Changing a desktop's resolution while running other than the Ultrawide toggle (its scale: the pill's Scale dropdown); head-direction focus. |

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

Open the XR Workspace panel with the glasses connected and press **Start XR**, then **Start** in the box that lists the run's scope. Before a session the panel shows only the logo, Start and a one-line status (`Launcher.qml`); the few settings that cannot change during a run (each desktop's startup shape, resolution and scale, *Head tracking*, the Luma *120 Hz mode switch*, the fallback output when there is more than one) sit under a collapsed **Startup options**. When `begin_start` would refuse because the fallback is ambiguous (`pick_fallback` returns `choose`), the *Return windows to* choice shows next to Start, which stays disabled until a monitor is picked; with no physical fallback at all Start is disabled and says why. That confirmation click is the consent for one run, including any temporary display-mode switch (for example Luma 1920×1200@120). **Stop** returns every window to the physical fallback output. **Recover** appears if an earlier run left resources behind.

At start, a panel run moves the fallback output's windows onto the XR desktops, once, after the desktops are created and before the renderer starts (`tools/native_spike/adopt.py`). The fallback's occupied regular workspaces go, in id order, to XR-1, XR-2, ... one each, whole, so their windows stay together in their layout. A gap in the numbers skips no desktop. A run always starts with its own three desktops (`profiles.startup_desktops`); with more occupied workspaces than that, the rest go to the last desktop, each still its own workspace. A moved workspace joins its desktop's Omarchy workspace block under the same slot (as Omarchy's own move-workspace-to-monitor does), so Omarchy's reconcile on a pill's Add or a theme switch leaves it there. Special (scratchpad) workspaces, pinned windows and the panel itself (`org.quickshell`, title "XR Workspace", or "VITURE Workspace" from a shell still running the panel QML from before the rename) stay on the physical monitor. A workspace holding one of those stays with it, and its other windows move one by one, in layout order, onto the workspace their desktop shows. All the moves are one generated Lua eval, so one compositor tick, with no intermediate layout drawn. If it fails, the run goes on and a notification says so. The CLI test-pattern run (no capture) moves nothing.

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
| Ctrl+Alt+Left / Right | run | Previous / next desktop |
| Ctrl+Alt+[ / ] | run | Less / more space between desktops |
| Ctrl+Alt+Space | run | Recenter |
| Ctrl+Alt+V | run | Maximized: follow / locked (0DoF). In the overview (always anchor, 3DoF) nothing changes; a toast says why |
| Ctrl+Alt+P | run | Toggle pose prediction (on by default) |
| Ctrl+Alt+H | run | Show the help card in the glasses |
| Ctrl+Alt+PgUp / PgDn | run | Previous / next help card page (wraps round; nothing while the card is closed) |
| Ctrl+Alt+T | run, both modes | Typing mode on / off (see [Typing mode](#typing-mode)) |
| Ctrl+Alt+L | run | Open the selected desktop's placement pill (back to the overview first if maximized), or close the open one |
| Ctrl+Alt+G | run | Send the focused window to the glasses: the desktop selected in the view |
| Ctrl+Alt+1 / 2 / 3 | run | Send the focused window to XR-1 / XR-2 / XR-3 |
| Ctrl+Alt+B | run | Send the focused window back from the glasses to the physical monitor |
| Ctrl+Alt+D | run | Privacy mode: main physical display off / on (DPMS), so only the glasses show content (see [Privacy mode](#privacy-mode-main-display-off)) |
| Ctrl+Alt+E | run | The TRON grid behind the desktops on / off, with a fade (see [The TRON grid](#the-tron-grid)) |
| Ctrl+Alt+Shift+E | run | The TRON grid's colour: cyan / the theme accent; turns it on if it was off |
| Tab / Shift+Tab | pill open | Open the next / previous desktop's pill instead (only one is open) |
| Up / Down | pill open | Previous / next field: left/right, down/up, distance, curvature |
| Left / Right | pill open | Decrease / increase the field by a small step (Shift for a big step). Left/right: 0.2°, Shift 15°, and it carries on past 180° to the other side. Down/up: 0.2°, Shift 10°, and it stops at ±90°. Not stopped by other desktops |
| R | pill open | Reset the desktop to flat |
| Escape | pill open | Close the pill |
| Mouse past a desktop's edge | overview | Free pointer: the cursor hides and a dim dot moves through the space around the desktops |
| Left click a pill | free pointer on a pill, or on the open panel's top strip | Open that desktop's panel (closing any other), or close it if it is the open one |
| Left-drag a pill | free pointer on a pill, or on the open panel's top strip (in anchor mode anywhere round you, see [Moving desktops](#moving-desktops-in-the-overview-the-pill-under-the-desktop)) | Turn that desktop round you, like a title bar, the grabbed point staying under the dot; in anchor mode all the way round you. It never overlaps another desktop. Nothing moves until the pointer is more than 4 px from the press, so a click never nudges it |
| Left click / drag a slider | free pointer on a slider of the open panel | Select that field and snap it to the pointer; a drag moves it live; release saves |
| Left click outside the open panel | free pointer | Close the panel (saved), then the click does what it would anyway: open or drag another pill, maximize a desktop, or nothing in empty space |
| Left-drag a desktop corner | free pointer on a corner (inside or outside) | Resize that desktop: outward is bigger, inward is smaller. The open desktop's own corners do not close its panel |
| Left click a desktop | free pointer on a desktop | Maximize it (as Ctrl+Alt+Up; follow) on the release: selected, focused, out of the overview; the cursor shows at that spot |
| Left press-hold (`HOLD_S` 0.35 s) or drag (> `WINDOW_DRAG_PX` 12 canvas px) on a desktop, then release on another desktop | free pointer on a desktop, or holding still over the selected desktop | Move the window under the press onto the workspace that desktop shows (`send.WindowSender.drop`, Hyprland move with `follow = false`): no zoom, no selection change. A ghost outline follows the dot and the target desktop is framed (`overview_pointer` frame `drag`). Released anywhere else: nothing moves |
| Left click a help card arrow or its "2 / 3" | free pointer on the open help card | Previous page (left arrow) or next page (right arrow or the page number). Elsewhere on the card a click does nothing, and it never closes an open pill |

Changes made with an open pill apply immediately and are saved to the glasses profile half a second after the last change, at once when a slider is released, and as soon as the pill closes. The pills, the placement panel and their wiring are covered by offline tests only; nothing about them has been tried in the glasses.

Each desktop also has a placement in the glasses: left/right and down/up (a turn round you, in degrees: see [Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)), distance (size in space) and horizontal curvature. Every desktop always faces you, upright, so there is no setting for which way it faces (the yaw, pitch and roll settings were removed on 2026-11-02). Set it in the desktop editor before starting, or under **Place** while a session runs; live changes apply when you release a slider. Resolution stays fixed during a run (but for the pill's Ultrawide toggle); the scale can change from the pill's Scale dropdown. Zooming in and back out never moves a desktop, in every viewing mode (anchor, locked and follow) and however far it was dragged: the zoomed desktop keeps the direction round you it was dragged to, comes to distance 100 there so it fills the view, keeps its curvature and is drawn on top; every other desktop stays exactly where and as large as it was in the overview. Nothing snaps straight ahead or back to its default place (`zoomed_placements`, since 2026-11-02). Ctrl+Alt+Left / Right in the overview just move the selection. The viewing mode goes with the view (**Maximized and the overview** in [Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop), since 2026-11-03): the overview is always anchor and a maximized desktop follow or locked, where every switch (keys and a cursor push) is a `cut`, straight onto the desktop. Zoomed in, in anchor mode (no longer reachable), they animate the switch: the view zooms out of the current desktop to the overview, turns to look at the target, then zooms into it where it was dragged (about 0.7 s; `ViewAnimator`, since 2026-11-02). The turn is the camera's only: no placement changes, and once the switch lands the view is not turned. A free-pointer click on a desktop is the main way to pick one. Overlapping desktops are drawn farthest first and are not split where they intersect. The CPU fallback renderer, used when GPU composition is unavailable, ignores placements.

Settings, including placements, are saved per glasses model.

### Typing mode

**Ctrl+Alt+T** switches between XR control (the default each run) and typing mode. Typing mode hands the keyboard back
without stopping the run: an open placement pill and the help card close, and the run's bind table
(`xr_workspace_binds`) is reinstalled with only the Ctrl+Alt+T bind in it. Every other run bind is released: the view
keys, the send-window keys, the open pill's keys, the overview free pointer (its left-button bind; the hidden cursor
comes back, and the dot and the pills hide), and a playing video's bare Space, arrow, Tab and Escape keys. Arrows, Space, Tab, Esc and other apps' Ctrl+Alt shortcuts then reach the focused window.
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

### The TRON grid

**Ctrl+Alt+E** fades an optional 360° environment in and out behind the desktops (0.6 s); **Ctrl+Alt+Shift+E** crossfades
its colour between classic cyan and the Omarchy theme accent (0.4 s) and turns it on if it was off, so the key always
shows what it did. Offline tests only: nothing about it has been tried in the glasses.

*What is drawn.* `spike/src/environment_gl.cpp`: one fullscreen triangle whose fragment shader casts the view ray into a
procedural scene (no textures, no extra framebuffer, GLES2 with `OES_standard_derivatives`). A black sky; a floor plane
1.6 m below the eye with lines every 2 m and a brighter one every 10 m, anti-aliased from the screen derivatives (lines
finer than a pixel fade to their average instead of shimmering), a soft halo for the bloom and a distance fade; a thin
bright horizon with a haze either side; a ring of ridge outlines up to about 5° high, with gaps between the ranges; and on
about one line in five a light trail at 8, 12 or 16 m/s. The animation clock wraps every 32 s, a common multiple of the
trails' periods, so the wrap never shows.

*World-locked, always.* The renderer gives the pass `environment_view(relative, reference)`
(`spike/include/xr/environment.hpp`): the head pose, prediction included, relative to the recentre reference turned to
level. It takes only the reference's heading about gravity (the pose feed's z axis), so a recentre with the head tipped
leaves the floor flat. The desktops' `view` and the mode controller's anchor are never involved, so anchor, follow, locked
and a maximized desktop all see the same room. A recentre turns the grid to the new heading, as it does the desktops.

*Over it.* The pass replaces the frame's black clear. Desktops are opaque and drawn after it, so their pixels are the same
as without it (`environment_render` compares them). The pills and cards already blend, premultiplied. The highlighted
desktop's glow and border were drawn opaque "over black": with the grid up the glow blends with `GL_MAX` (added, where the
GL has neither ES 3 nor `EXT_blend_minmax`) and the border is laid over it, so neither blacks out a box round the selected
desktop. With level 0 the pass is skipped and that path is as it was: `gl_bench --hashes` gives the same frame hashes as
before the feature. A VR video replaces the grid as it replaces the desktops; the setting stays.

*The glasses.* The shader scales each pixel so that its brightest channel stays under 191/255 (hue kept), and the sky and
the floor between the lines stay exactly black, so see-through (`environment_render` checks both, looking every way
round). A slight dither breaks up the dim haze's banding, never on black.

*The setting.* `tools/native_spike/environment.py`: `{"on": false, "color": "cyan"}` by default, saved with the view
settings (`environment` in layout.json for CLI runs and in the glasses model's profile for panel runs), and restored at each
XR start. An older file without it loads as off. The renderer gets an `environment` frame (`{"on":bool,"color":"cyan"|"theme"}`,
`spike/include/xr/ipc.hpp`) on each change and at a start only if it is on, so an older renderer never sees one while the
grid stays off; a malformed one is dropped without stopping the renderer. The panel's `set_settings` reaches a running
session as those same two ops (`environment.operations`). A theme switch's `style` frame recolours a grid in the theme colour.
The quality menu's last row (Q, while a video plays) is the same setting: Off, TRON cyan, Theme colour.

*The binds* are in `view.BINDS`, so the help card (one row, "Ctrl+Alt+E / Shift+E"), `bind_check` and typing mode treat them
like any run bind. Omarchy binds Super+Ctrl+E, Super+Shift+E, Super+Ctrl+Shift+E and Super+Ctrl+Alt+E, all different chords.

*Cost*, measured with `gl_bench --bench --frames 1000 --size 1920x1200` (the glasses' native buffer; GPU time from
`EXT_disjoint_timer_query`, CPU the draw call), 2026-11-03, on surfaceless EGL, so no compositor or capture is included.
The glasses render on the RX 7800 XT (`DRI_PRIME=pci-0000_03_00_0`); the integrated GPU, gl_bench's default here, is the
worst case:

| GPU | Scenario | GPU off | GPU on | CPU off (p50) | CPU on (p50) | Frames over 8.3 ms |
| --- | --- | --- | --- | --- | --- | --- |
| RX 7800 XT | maximized | 22 µs | 70 µs | 1.9 µs | 2.6 µs | 0 |
| RX 7800 XT | overview, pills, dot | 26 µs | 71 µs | 4.9 µs | 5.6 µs | 0 |
| RX 7800 XT | overview, head moving | 25 µs | 70 µs | 4.8 µs | 5.5 µs | 0 |
| Radeon 610M (the Ryzen's iGPU) | overview, pills, dot | 371 µs | 2573 µs | 8.2 µs | 19.7 µs | 0 |

So about 0.05 ms of the 8.3 ms frame on the glasses' GPU, the same in every scenario: the pass shades each pixel once
whatever is in front of it. Its shader compiles at renderer start, not on the first frame it shows.

Through the real renderer (`capture_e2e_bench --mode gl-dmabuf --seconds 8 --warmup 2 --hz 120`, zero-copy on the RX 7800 XT
against the loopback compositor, so a stand-in for Hyprland and no glasses; `--environment cyan` for on, `--tracking` for
a head-tracked run):

| Run | Grid | Commits/s | Capture to present p50 / p99 | Blank frames |
| --- | --- | --- | --- | --- |
| Head tracked | off | 120.0 | 13.12 / 16.47 ms | 0 |
| Head tracked | on | 120.0 | 13.13 / 16.48 ms | 0 |
| No head tracking | off | 119.8 | 5.11 / 8.40 ms | 0 |
| No head tracking | on | 119.8 | 5.14 / 8.43 ms | 0 |

The grid asks for frames only while it fades. A settled grid needs none of its own in a head-tracked run, which draws every
frame anyway. Without head tracking a renderer drawing on every callback would show new desktop frames a refresh later
(5 -> 13 ms: the two rows above differ for that reason), so there its pulses move only when something else redraws.

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

Moving the dot 4 px (glasses pixels) into a desktop hands the pointer back: the cursor reappears at that spot, visible,
and that desktop becomes the selected one (the highlight moves; Ctrl+Alt+Up zooms into it). The run itself does not do
this for every desktop: it uses [the unified cursor](#the-unified-overview-cursor), where only the selected desktop takes
the cursor back and the cursor never changes monitor. Leaving needs the edge
pixel and coming back needs 4 px inside, so the two modes never flap at an edge. A keyboard switch that warps the
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
- **Resize a desktop: drag a corner.** Each corner's grab zone is a square centred on the corner (6% of the desktop's
  shorter side, 10–28 px each way), so it reaches outside the desktop. A small dim L-bracket shows inside that corner.
  Drag it: the corner follows the dot along its diagonal, outward bigger, inward smaller. A corner wins where it meets
  the pill's zone.
- **Maximize a desktop: click it.** The desktop is selected, its monitor gets Hyprland's focus and the view zooms into
  it (the Ctrl+Alt+Up path). The cursor comes back at the clicked spot, on the clicked monitor. Over a desktop the click
  also reaches the window under the dot ([the unified overview cursor](#the-unified-overview-cursor)).
- A click in empty space does nothing, apart from closing an open panel.

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

The free pointer is off in typing mode (Ctrl+Alt+T: the cursor stays a normal cursor everywhere), while zoomed in and
while a VR video plays (`resize_press` / `resize_release` are in `IGNORED_WHILE_PLAYING`). It stays on while a
placement panel is open: it is how you use the panel. Leaving the overview removes the left-button binds and shows the
cursor again. A drag in progress (pill, corner or slider) ends and is saved when a video opens, on zoom-in and after
30 s without pointer movement. While the dot is free, right clicks and scrolling are swallowed (no window is under
it); over a desktop they reach the window there ([the unified overview cursor](#the-unified-overview-cursor)).

The dot, pills and bracket protect the micro-OLED like the cards. The dot is a grey disc (170/255, 184 while dragging)
in a black ring, 16 px across. The pills are dark glass (see [Placement pills](#placement-pills)) that brightens on
hover and while dragged, never above 191/255; the bracket is 140, 184 while dragging (cap 191). None of them moves on
its own. They hide after 30 s without pointer movement (`OVERLAY_IDLE_CLOSE_S`); a click while hidden only brings them
back. An open panel is still drawn while the pills are hidden, and closes by itself after its own 30 s without input.
The CPU fallback renderer draws none of them, and the run reports no `overview_geometry` there, so the free pointer
stays off.

### Resizing desktops in the overview

A corner drag (above) changes the desktop's size in space. The desktop scales about its own centre and always keeps its
shape.

The size has limits and is saved like the other placements:

- It goes from half to ten times the default size (well past the field of view). A resize is the desktop's **Distance**
  placement seen from the other side (apparent size = 100 / Distance), so the limits are Distance's 200 and 10. Past
  the edge of the view the drag speeds up (`free_pointer.RESIZE_OVERSHOOT_GAIN`): the pointer stops half a view past
  each edge, so a corner that followed it 1:1 could not reach ten times. Once a desktop is bigger than the view its
  corners and pill are out of sight; shrink it with the open panel's keys (Ctrl+Alt+L, Down to Distance, then Right / Shift+Right, or R to reset). The panel's **Place** tab and an
  open placement pill show the new Distance. For example, 1.5× bigger is Distance 67.
- A resize never changes the glass pill's (or its open panel's) size: it moves with the desktop's bottom edge and stays
  the same size on screen (`view.hpp glass_to_desktop`, mirrored by `free_pointer.glass_to_desktop` for hit testing).
- It is saved to the glasses model's profile half a second after the last change and again on release, so it carries
  over to the next session.
- Maximizing a desktop brings it full size where it is (its dragged place round you), then follow brings it in front of
  you; the others stay where they are. The bracket and the pills only show in the overview.

Why it never changes pixels: a resize changes size in space, not the desktop's resolution or Hyprland scale. In the
overview all desktops share one height, so pixel count only changes sharpness, never size. A free-aspect resize could
only stretch the captured image, which is why the shape is always kept. Changing a running output's mode would also
need a new ownership-broker operation in the accepted plugin. Resolution, shape and scale stay in the desktop editor
(the panel's **Startup options**) and apply at the next start, through the journaled output creation that reads every
value back; only the pill's Ultrawide toggle and its Scale dropdown change a running desktop (a broker `reshape`). Corner resizing adds no compositor output change and no new journal record.

### The unified overview cursor

Offline tests only (`tests/native_spike/test_unified_cursor.py`, `test_directional_switch.py`, plus `bind_check`'s `--verify-config` load of its
binds); not run in a live compositor or tried in the glasses. Since 2026-11-02 (`dot-unified-cursor`) the dot is the
one real cursor, and it only ever controls the selected desktop ("home"). The real Hyprland cursor stays on home's
monitor, zoomed in and in the overview, and never crosses to another monitor in Hyprland's layout by hand. Switching
desktops is a key (Ctrl+Alt+Left / Right), in the overview a click on another desktop, which zooms into it, or, zoomed in,
a push past home's edge toward a desktop placed that way (below).
`tools/native_spike/unified_cursor.py` wraps the free pointer above and replaces the input lock, which held the cursor
still for the whole overview and let no click or wheel reach a window.

- **Zoomed in** (and in typing mode or during a VR video): the run samples the cursor at 250 Hz while it is on an XR
  monitor. If the hand pushes it onto another XR monitor, or off home onto the physical monitor, the run puts it straight
  back at the nearest point 2 px inside home and gives home Hyprland's monitor focus again (`confine_lua`). A jump of
  more than 400 px onto the physical monitor is a warp, not the hand (a window sent back with Ctrl+Alt+B), and is left
  there. A cursor already on the physical monitor is left alone until it comes onto an XR monitor, and then goes to home.
- **Overview, over home:** the real cursor is the pointer. Hand motion moves it, and the dot is drawn where it maps onto
  home's surface, in anchor, locked and follow mode. It stays hidden, but hover, focus-follows-mouse, every button and
  the wheel reach the window under it. The left button is bound *non-consuming* (`non_consuming = true`), so the window
  gets the click as usual. A press and release less than 4 canvas px apart is also a click that zooms into home. A drag
  or a selection does not zoom. While the button is held the dot never goes free and the cursor stays on home: if
  Hyprland lets it onto another monitor, it is pulled back, so the window gets its release.
- **Overview, off home:** at home's edge pixel the dot goes free. The hidden cursor is parked just inside home where it
  left, at least 100 px (`PARK_MARGIN_PX`) from the edges, and put back once it strays 32 px (`LEASH_PX`). Its motion
  moves the dot through space. Free mode uses the input lock's grab (`input_lock.lock_pointer_lua`): the left button
  drives pills, corners, sliders, the help card and desktop clicks, the right, middle and side buttons only end a drag,
  and the wheel is bound to nothing (with `binds.scroll_event_delay` at 0, restored afterwards). Over another desktop the
  dot is only the dot: it does not select that desktop or move the cursor there. A click on it zooms into it, and the
  cursor then shows there, under the dot. Moving the dot 4 px back into home hands the real cursor back under it.
- **Keys:** Ctrl+Alt+Left / Right change the selected desktop. Home moves with it, and the cursor and dot go to the
  middle of the new home. Any other jump off home in the overview is pulled back home, or let go if it landed on the
  physical monitor. Cursor follow (Ctrl+Alt+F) was removed: monitor focus events never switch the view.
- **Zoomed in, a push past an edge switches** (`dot-directional-switch`, 2026-11-03; `unified_cursor.EdgePush`,
  `tools/native_spike/neighbors.py`; not in typing mode or during a VR video). The desktop it goes to is the one placed
  past that edge round you, not the next in the row: each desktop is a direction from the eye (its centre as drawn,
  `orbit.place_point` on the renderer's overview layout, with its orbit, distance and levelling roll) with its own right
  and up axes. A candidate's bearing is the great-circle direction from home's centre to its centre, read in home's
  right / up axes, so a rolled home turns the directions with it. It must lie within 60° (`neighbors.CONE`) of the push,
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
push the mouse past every edge of the desktop (it switches to the desktop placed that way, or stays put if none is); in the overview, move the dot across the gaps and over the
other desktops (nothing there reacts), scroll and right-click inside home (the window reacts), drag a window inside home
(no zoom), click another desktop (it zooms in there), and repeat after Ctrl+Alt+Right and in each viewing mode.

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

**No overlap.** Desktops never overlap as seen from your eye. Each desktop's footprint is the set of directions from
the eye that it covers: its true placed outline (distance, orbit and curvature included; a curved desktop uses its
convex hull, so the guard errs early), widened by a 6 canvas-px margin all round (`MARGIN_PX`, the width of the
highlight frame) and by the 34 px strip under it that holds its pill and the pill's grab padding (`PILL_ZONE_PX`). A
drag moves along the straight path toward where the pointer wants it and stops before the first step that would make
two footprints share a direction, so it never jumps through a neighbour, however fast the pointer moves. If that path
is blocked it slides along the neighbour, sideways first or up and down first, whichever ends closer to the target. The
corner of that slide is sent first, so the renderer's straight-line easing does not cut through the neighbour. Each
pair is judged on its own: a desktop that already overlaps another (set from the panel) may move anywhere it overlaps
that one no more than now and every other one not at all, so it is never stuck. To pass a neighbour, lift it clear of
the row first, or carry it round behind you. The sliders and keys (in the glass panel and in the settings panel) and
the corner resize are not guarded.

**Keys.** With a pill open, Left / Right change Left/right by 0.2° and Shift+Left / Shift+Right by 15°
(`view.MENU_STEPS`); it wraps past 180° to the other side (`orbit.wrap`) and never stops. Down/up steps by 0.2° and
10° and stops at ±90°. The glass panel shows these two in whole degrees, rounded ("+13°", "-180°"; "+13deg" with the
pixel font, for width), Distance in percent and Curvature in degrees. The settings panel's **Place** tab shows tenths
("12.5°"), and its one-line summary reads like "moved 12.5° right, 3° up". Its preview keeps a desktop beside or behind
you at the end of the row.

**Maximized and the overview: the viewing modes.** Since 2026-11-03 (maximize-follow, after the INAIR Pod) the viewing
mode is no longer a setting of its own: it follows from the view (`ViewState.mode`, a property of `zoomed`). The
overview is always anchor (3DoF, the desktops fixed in the room). Maximizing (the `zoomed` state; Ctrl+Alt+Up or a
free-pointer click on a desktop) switches to follow or locked: `ViewState.maximized_mode`, which Ctrl+Alt+V toggles
while maximized (`MAXIMIZED_MODES`). The choice sticks for the session: every later maximize uses it until Ctrl+Alt+V
changes it again. It is never saved, so each XR start (a new `ViewState`) maximizes in follow. In the overview
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
the view there, curvature kept, and from halfway in it is drawn on top (`zoom_draw_order`). Every other desktop's
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
  itself (moving the cursor into it, clicking it to focus it) are not. The keyboard works: Ctrl+Alt+Left / Right
  selects it, Ctrl+Alt+L opens its panel, the arrows change it and R resets it to flat.
- Mixed installs, as above: the renderer, Python and QML must come from the same build. A Python-only rollback resets
  placements.

The pill, the open panel and the corner bracket share one left-button bind (`resize_press` / `resize_release`, bound
only while the pointer is free) and never compete: the open panel is tested first, and a corner zone wins over a pill
where they meet. If the run dies while the pointer is free, the next left click runs the bind helper, which removes the
bind and shows the cursor again where free mode found it.

Until 2026-11-02 the move handle was a thin grey bar 4 px tall, drawn above each desktop. The glass pill under the
desktop replaced it; the wire op (`move_handle`) stayed the same and only the 4 px click threshold was new. Later the
same day the flat offset's move maths gave way to the orbit and guard above: its 4% snap, its allowance of 10% overlap
of the smaller desktop's area (judged on approximate overview rectangles) and its reach of one desktop width or height
are gone, and `resize.allowed_move` became `orbit.allowed_move`. After that every desktop was made to face you, and the
yaw, pitch and roll settings were removed.

### Roll-level icons

Every desktop faces you, upright in the canvas frame. That frame is the head's orientation at the last recentre, so a
recentre with the head tipped to one side leaves every desktop rolled as you see it, and a pitched one leaves desktops
turned off to the side rolled. In anchor mode, while head tracking runs and no mode blend is in progress, the renderer
measures each desktop's roll from level (`view.hpp desktop_tilt`: the world's up from the pose feed, `pose.hpp
canvas_up`, projected onto the desktop at its centre) and reports it on the status socket as
`{"roll_geometry":{"tilt":[t,...]}}` (hundredths of a degree, counter-clockwise positive, `null` where it is not
measured; on change, at most every 100 ms). The tilt leaves out the levelling roll, so the supervisor owns that alone.

`free_pointer.OverviewPointer` keeps a levelling roll per desktop for the session (not saved in profiles, not part of
the wire placement) and sends it with the icons in the `roll` op: `{"roll":[r,r,r,r],"icons":["none"|"ccw"|"cw"|"both",...],
"hover":-1..3,"side":"none"|"ccw"|"cw","active":bool}` (`side`: which of the hovered desktop's arrows). The renderer turns
each desktop by its roll about its own line of sight (`Placement::roll`, eased like a placement, at every zoom), so it
still faces you. Every desktop in the overview shows both icons (`"both"`), level, tilted or not measured (follow mode,
no tracking): above the top-left corner a counter-clockwise arrow, above the top-right a clockwise one (`glass.hpp
roll_icon`). Each is 22 px across and 8 px above the corner as you see it whatever the desktop's size or
distance: one plain curved arrow (a thick half-turn arc and a large head) with no button behind it, in the
theme's M3 primary (from the Omarchy theme accent, following theme changes live like the pills), lifted toward tone 98
while hovered or dragged (only the arrow under the dot). Its hit box is that 22 px square plus 4 px of padding up and
toward the middle of the top edge (`free_pointer.roll_hit_rect`); the gap under it and everything beyond the desktop's
side stay with the corner's resize zone, so a corner drag from outside still resizes. The icons show and hide with the
pills. A click turns the desktop the arrow's way: straight to level when level lies that way (more than 0.5° off, tilt
measured), else 2° further (`free_pointer.roll_click`). A press and drag on either arrow turns it with the dot round its
centre, either way, up to 90°, resting at level within 0.5° (`roll_drag`). Until 2026-11-03 a desktop showed only the
arrow that levels it, only when more than 0.5° off and measured, and a drag was clamped between its roll at the press
and level, so a desktop could only ever be turned toward level. The icons are drawn over every desktop, so they are
hit-tested before any desktop's corners, pill or surface (a nearer neighbour in a tight overview never takes an icon's
click).
Hit testing and the cursor mapping use the rolled surface; the pill drag and the no-overlap guard ignore the roll.

### Help card pages

The help card (Ctrl+Alt+H) sizes its text from the output height: row text 30 px at 1920x1200 @1.25, where today's 13
rows sit on one page, stepping down to 18 px on smaller outputs. It keeps the card chrome (padding, border, radius,
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
collapses it. Only one pill is open at a time: opening another collapses the first, and Tab / Shift+Tab move the open
panel to the next / previous desktop. Pressed while zoomed in, Ctrl+Alt+L first zooms out to the overview so the pill is
visible; the pill only starts growing once the overview is reached, so the whole expansion is seen. Zooming in
(Ctrl+Alt+Up) closes an open panel first, saved, with its keys released. Everything here is covered by offline tests
only. It has not been run in an isolated VM or tried in the glasses.

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
Tab next, R flat). Desktop and setting selection wrap round. R puts every setting of the open desktop back to flat.

**With the pointer.** Press on a slider (its row, from 12 px before the knob's lowest position to 12 px past its highest,
`PANEL_KNOB_LO` / `PANEL_KNOB_HI` and `PANEL_SLOP_PX` in `free_pointer.py`: about 5 px past each end of the drawn track)
and that setting becomes the selected one and jumps to the pointer. Hold and drag to move it live; the desktop follows.
The drag is measured on the panel as it was at the press, so a setting that moves its own desktop does not chase
itself. Until the release the renderer keeps drawing the panel on that same press-time placement while the desktop
moves live, so the knob stays under the dot. On release the panel joins its desktop again. Release ends the drag and
saves. A key that collapses the panel or moves it to another desktop during the drag ends what the drag does. A press
elsewhere inside the panel does nothing and the panel stays open. A press anywhere else while a panel is open closes it
first (saved, its keys released), then carries on as usual: on another desktop's pill it starts that pill's click or
drag, on a desktop it focuses that desktop, and in empty space it does nothing more. Pressing the open desktop's own
corners resizes it without closing the panel. A click inside a desktop while the real cursor is there (desktop mode, not
the dot) goes to the window under it. The run never sees that click, so it does not close the panel.

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
the roll arrows, the resize handle, the pop-ups, the help and video cards, the border and the glow together in the
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
labels only. The roll arrows are the primary, lifted along the primary palette toward tone 98 while hovered or dragged;
the resize handle is on-surface-variant, the primary while held. The pop-ups and the help and video cards are extra-large
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
`glass_geometry_dump` and checks that `tools/native_spike/free_pointer.py` uses the same pill and panel geometry) and
`glass_theme` (for every Omarchy theme's border colour: the pill's hue and brightness, the state layers and the cap in
every state, the panel's parts, the roll arrow and resize handle colours, and an open panel re-rendered once on a theme
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

The TRON grid adds two ctest tests. `environment` (`spike/tests/environment_test.cpp`): the line colours under the cap for
every kind of theme, the fade and crossfade, the levelled world-locked view from any recentre reference, and the `environment`
frame over `tests/native_spike/fixtures/environment_payloads.json`, which `tests/native_spike/test_environment.py` runs the
supervisor over too. `environment_render` (`spike/tests/environment_render_test.cpp`) draws the grid with the real compositor
on surfaceless EGL behind three synthetic desktops at 1920x1200: off is bit for bit a compositor that never had it; on, the
desktops' pixels do not change and the glow leaves no black halo; looking every way round nothing passes 191/255, most of the
view stays dark and the sky stays black; the horizon sits where the pitch puts it, a whole turn gives the same frame, and in locked
mode the grid turns while the desktops do not; the fade, the theme colour and the seamless clock wrap. It also writes preview
PNGs (zlib) to `build/release/environment-previews/`, or to the directory given as its argument. `gl_bench`'s `*_env` scenarios
add the grid to the fresh-compositor comparison, so it can leave no GL state behind, and to `--bench`. `capture_e2e_environment_*`
send the real renderer its `environment` frame on both GL paths and check that frames keep flowing and no desktop goes blank.
`test_environment.py` covers the setting, the keys, saving and restoring and the quality menu's row.

The placement round you, with every desktop facing you (see
[Moving desktops in the overview](#moving-desktops-in-the-overview-the-pill-under-the-desktop)), adds
`tests/native_spike/test_orbit.py` for `tools/native_spike/orbit.py`, and the `placement_contract` ctest, which compares
`orbit.place_point` with the renderer's `place_point`. `test_free_pointer.py`, `test_glass_pills.py`,
`test_grab_pill.py`, `test_placement_menu.py`, `test_profiles.py` (older profiles that still carry yaw, pitch and roll)
and the `view`, `hud`, `glass`, `ipc` and `placement_menu_ipc` ctests were updated for it. All of these are offline.

Check that this project's run, send-window and placement-pill keys clash with nothing in your Hyprland config (exit 0 no clash, 1 clash, 2 could not check):

```sh
python -m tools.native_spike.bind_check
```

It loads `~/.config/hypr/hyprland.lua` with `Hyprland --verify-config`, which runs the Lua config without starting a compositor, in a temporary HOME holding a copy of `~/.config/hypr` and the Omarchy state the config reads. It records every chord the config binds, runs this project's generated bind Lua the same way, and compares them. A digit and its keycode (`1` and `code:10`, the way Omarchy binds workspace digits) count as the same key. It never contacts the running compositor. Hyprland 0.56.2's `--verify-config` crashes on exit when a config has made timers, so the check replaces `hl.timer` with an inert stand-in while it loads.

Tests use synthetic collectors, fake sockets and temporary directories. They never open a real display or device, and their output is never published as host evidence. Build and link success does not show that the plugin loads at runtime against the installed compositor.

## Safety boundaries

- Nothing touches the glasses or the live desktop without explicit consent for that run.
- Intent is journaled before any mutation, and actual state is read back before a step counts as confirmed. Recovery keeps ambiguous or user-modified resources and reports incomplete recovery instead of deleting them.
- Capture is limited to the run's own `XR-n` outputs and stays in memory. There is no general desktop capture, input injection or vendor SDK.
- Display-mode switches are runtime only. Saved monitor configuration is never edited.
- The send-window keys (Ctrl+Alt+G, 1-3, B) move only the focused window, by address, onto the active regular workspace
  of one of the run's XR outputs or of the run's physical fallback. That one Hyprland dispatch is allowlisted; pinned
  windows are refused, so recovery can always move the XR desktops' windows back.
- Run binds use Ctrl+Alt chords. The only unmodified binds are modal: an open placement pill's keys, and the left
  button while the overview's free pointer is outside the desktops (pill click and move, panel sliders, corner resize,
  click to focus). Both are removed when no longer needed. The free pointer hides the cursor and re-centres it with
  fixed Lua, and shows it again when the pointer goes back into a desktop, when the overview ends, at run end, or on
  the first click after a dead run. It warps the cursor only onto the run's own XR desktops.
- The isolated VM test path (`isolated-run`, `isolated-recover`) needs separate VM consent and refuses to run on the physical session.
- The unified overview cursor adds unmodified binds in the overview, in the free pointer's bind table and removed with
  it (see [The unified overview cursor](#the-unified-overview-cursor)): over the selected desktop a non-consuming
  left-button press/release (the window still gets the click); while the dot is free, the right, middle and side buttons
  and the wheel. It sets `binds.scroll_event_delay` to 0 while free and puts it back with those binds. It warps and
  holds the cursor only on the selected XR desktop's monitor, and moves monitor focus only back to it.

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
