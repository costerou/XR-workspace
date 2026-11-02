# VITURE Workspace

A local XR desktop for Omarchy/Hyprland on VITURE Beast and Luma Ultra glasses. It creates independent virtual desktops (three 1080p outputs by default), captures only those outputs, and renders them in the glasses with 3DoF head tracking. You start and control a session from an Omarchy shell panel, or with Ctrl+Alt keybinds while a session runs.

## Status

Every claim in this repo says whether it comes from offline tests, an isolated VM, or the physical glasses. Passing tests or a VM trial is not physical acceptance.

| Area | Evidence |
| --- | --- |
| Managed virtual desktops, capture, rendering, viewing modes, zoom/switch controls, pose prediction, theme-reload survival | Physical runs on Luma Ultra through the `tools.native_spike` CLI. The `mod1` run presented ~119.9 Hz over 168 s on the zero-copy dmabuf path. The `trk1` run held ~119.8 Hz for 214 s with 0 tracking losses. These measure presentation cadence only, not latency or long-session comfort. |
| Settings panel and controller service | Physical panel acceptance on Luma Ultra (2026-11-01; controller source `d685884`, which includes `8538468`, `4cfa04d` and `d9fbb28`; pinned renderer `62989b6e…` built from `822495c`). Run `d3adc32d` (on controller code a peer session had loaded but not yet committed; the later run and the recovery used committed `d685884`), started from the panel, calibrated tracking, switched DP-2 to 1920×1200@120 and presented ~120.0 Hz on dmabuf with 0 missed frames. Cody confirmed in the glasses that zoom/overview, desktop switching, spacing, recenter, viewing modes, cursor follow, prediction, the help card and live window content all worked. Panel Stop exited cleanly (renderer rc 0, nothing left over). In run `88b7e850`, the session kept running through a controller restart, and the new controller picked it up again. After a deliberate SIGKILL of the run unit, the panel showed Recover, and panel Recover put the desktop back to its baseline (XR outputs removed, plugin unloaded, DP-2 back to 1080@60). Earlier physical attempts failed before the renderer launched (mode readback, then `Errno 11`). Beast panel sessions are untested. The ~120 Hz figure is presentation cadence only. |
| Per-desktop placement (position, distance, yaw/pitch/roll, curvature) | Physical acceptance on Luma Ultra (2026-11-01; `feature/per-desktop-transform` at `f593574`, which is `ba3ead2` plus `d685884`; pinned renderer `28d934fa…`; ownership plugin `a68bd142…`, the same binary as the panel acceptance). In runs `cf73a329` and `2af10af8`, started from the panel, Cody angled XR-1 and XR-3 with yaw and curved XR-2 from the placement editor. He confirmed in the glasses that the angles and curvature applied correctly in the running session. Only yaw and curvature were reported on; the offset, distance, pitch and roll sliders are covered by offline tests only. Frame-time cost of curved desktops and comfort are unmeasured. Beast is untested. |
| Beast | Presentation-only physical trials (generated pattern, normal exit, controlled crash). Managed sessions, tracking and capture on Beast are untested. |
| Overview corner resize (drag a desktop's corner to make it bigger or smaller in space) | Offline tests only (`tests/native_spike/test_resize.py`, `spike/tests/resize_handle_test.cpp`). Not yet tried in the glasses; the steps are in `docs/placement-menu-test-plan.md` (R1–R15). |
| Not built yet | More or fewer than three desktops; changing a desktop's resolution or scale while running; head-direction focus. Panel controls for these are greyed out and labelled "coming". |

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

# Pin the renderer and plugin by hash under ~/.local/share/viture-workspace/bin/
# and write the user unit. The controller only launches pinned copies.
python -m tools.xr_controller install --build build/release

ln -s "$PWD/omarchy-plugin/cody.viture-workspace" ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins
```

To switch an existing install to another checkout or build, do it with no XR session running. First back up `~/.local/share/viture-workspace/installed.json`, `~/.config/systemd/user/viture-workspace-controller.service` and the panel symlink's target. Run `install` from the checkout the controller should use; to keep an accepted plugin binary, stage the new renderer next to the currently pinned `libxr-ownership.so`. Point the panel symlink at that checkout, restart the controller, then **run `omarchy-restart-shell`**. `rescanPlugins` does not reload panel QML the shell has already loaded, so without a shell restart the panel keeps its old UI. To roll back, restore the two backed-up files, run `systemctl --user daemon-reload`, point the symlink back, restart the controller and run `omarchy-restart-shell` again.

`install` writes `viture-workspace-controller.service` but does not enable it. Start it with `systemctl --user start viture-workspace-controller.service`, or run `python -m tools.xr_controller serve` in the foreground. The service never starts an XR session by itself.

## Use

Open the VITURE Workspace panel with the glasses connected. Choose the shape, resolution and scale of each desktop, then press **Start XR session**. That confirmation click is the consent for one run, including any temporary display-mode switch (for example Luma 1920×1200@120). **Stop** returns every window to the physical fallback output. **Recover** appears if an earlier run left resources behind.

Other Hyprland plugins may stay loaded during a run. Omarchy keeps `hyprbars` loaded all session for its titlebars workspace mode. Start and recovery refuse only if the VITURE ownership plugin (`xr-ownership`) is already loaded. If an XR desktop's workspace is in Omarchy's floating mode, its windows get title bars inside the glasses, as they would on any monitor. That changes nothing about what the run captures or controls.

Head tracking must be running before a session starts:

```sh
python -m tools.native_spike tracking start   # also: status, stop
```

**VR video** (offline tests only, not yet run in the glasses): during a session, **Ctrl+Alt+O** (or the panel's *VR video* section) plays a 360° or 180° video, mono or 3D (side-by-side, top-bottom), equirectangular or fisheye, head-tracked around you in place of the desktops. Space pauses, the arrows seek, Tab changes the format, Ctrl+Alt+Space recenters and Esc returns to the desktops. While a video plays, the placement menu, its mouse mode and the overview corner resize are off. The renderer decodes with libmpv (`mpv` package; without it the renderer builds without video). Design, formats, limits and performance: [docs/vr-video.md](docs/vr-video.md).

These keybinds exist only during a run. The keys marked "menu open" are bound only while the placement menu is open, and released when it closes or the run ends; the left-button binds marked "mouse mode" exist only while mouse mode is on. If the run dies with the menu open, the first menu key (or, in mouse mode, the first click) pressed afterwards releases them and shows the cursor again.

| Keys | When | Action |
| --- | --- | --- |
| Ctrl+Alt+Up / Down | run | Zoom into the selected desktop / back to the overview |
| Ctrl+Alt+Left / Right | run | Previous / next desktop |
| Ctrl+Alt+[ / ] | run | Less / more space between desktops |
| Ctrl+Alt+Space | run | Recenter |
| Ctrl+Alt+V | run | Cycle viewing mode: anchor (3DoF), follow, locked (0DoF) |
| Ctrl+Alt+F | run | Toggle cursor follow |
| Ctrl+Alt+P | run | Toggle pose prediction (on by default) |
| Ctrl+Alt+H | run | Show the help card in the glasses |
| Ctrl+Alt+L | run | Open / close the placement menu in the glasses |
| Ctrl+Alt+G | run | Send the focused window to the glasses: the desktop selected in the view |
| Ctrl+Alt+1 / 2 / 3 | run | Send the focused window to XR-1 / XR-2 / XR-3 |
| Ctrl+Alt+B | run | Send the focused window back from the glasses to the physical monitor |
| Tab / Shift+Tab | menu open | Next / previous desktop |
| Up / Down | menu open | Previous / next field: left/right, down/up, distance, yaw, pitch, roll, curvature |
| Left / Right | menu open | Decrease / increase the field by a small step (Shift for a big step) |
| R | menu open | Reset the desktop to flat |
| Ctrl+Alt+M | menu open | Turn mouse mode on / off |
| Escape | menu open | Leave mouse mode; otherwise close the menu |
| Left click | mouse mode | Jump the slider under the pointer there, or pick the desktop under it |
| Left drag | mouse mode | Drag that slider; the desktop follows live |
| Left-drag a desktop corner | overview, menu closed, pointer on a corner | Resize that desktop: outward is bigger, inward is smaller |

Menu changes apply immediately and are saved to the glasses profile half a second after the last change, or as soon as the menu closes. The menu wiring and mouse mode are covered by offline tests only.

Each desktop also has a placement in the glasses: left/right and down/up offset, distance (size in space), yaw, pitch, roll and horizontal curvature. Set it in the desktop editor before starting, or under **Place** while a session runs; live changes apply when you release a slider. Resolution and scale stay fixed during a run. Zooming into a desktop brings it flat in front of you and keeps its curvature. Overlapping desktops are drawn farthest first and are not split where they intersect. The CPU fallback renderer, used when GPU composition is unavailable, ignores placements.

Settings, including placements, are saved per glasses model.

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

The help card has no room for a 13th row (12 rows are 945 px of the 960 px surface), so its Ctrl+Alt+H row reads
"Help; Ctrl+Alt+G/B: window to glasses/back". The numbered keys are listed only here.

### Resizing desktops in the overview

In the overview (Ctrl+Alt+Down), move the pointer into a corner of a desktop: the last few percent of its width and
height (8% of its shorter side, 32–160 px). A small dim L-bracket appears just inside that corner. Hold the left button
and drag. Outward makes the desktop bigger and inward makes it smaller, live while you drag. Either direction is
enough, because the pointer stops at the edge of the monitor layout: dragging outward by about the desktop's average
side length doubles its size. Release to finish. The desktop scales about its own centre and always keeps its shape.

The size has limits and is saved like the other placements:

- It goes from half to twice the default size. A resize is the desktop's **Distance** placement seen from the other
  side (apparent size = 100 / Distance), so the limits are Distance's 200 and 50. The panel's **Place** tab and an
  open placement menu show the new Distance while you drag. For example, 1.5× bigger is Distance 67.
- It is saved to the glasses model's profile half a second after the last change and again on release, so it carries
  over to the next session.
- Zooming into a desktop still brings it flat and full size in front of you. The bracket only shows in the overview.

Why it never changes pixels: a resize changes size in space, not the desktop's resolution or Hyprland scale. In the
overview all desktops share one height, so pixel count only changes sharpness, never size. A free-aspect resize could
only stretch the captured image, which is why the shape is always kept. Changing a running output's mode would also
need a new ownership-broker operation in the accepted plugin. Resolution, shape and scale stay in the desktop editor
and apply at the next start, through the journaled output creation that reads every value back. Corner resizing adds
no compositor output change and no new journal record.

How clicks are handled: while the pointer is on a corner, or during a drag, the run binds the left button to the
resize (press and release), so the window under that corner does not get the click. Everywhere else, and whenever the
bracket is hidden, clicks go to windows as usual. The pointer is read about 30 times a second, only in the overview,
with the read-only `cursorpos` query. The binds go away when the pointer leaves the corner, after 30 s without pointer
movement, on zoom-in, when the placement menu opens, and at run end. If the run dies while they are bound, the first left click afterwards removes them.
Corner resize is off while the placement menu is open. The menu's mouse mode uses the left button, and the menu has its own
Distance setting. A drag in progress ends and is saved when the menu opens.

The bracket protects the micro-OLED the way the cards do, by dimming and auto-closing. It is grey at about 55%
brightness, and 72% while you drag (184/255, under the cards' 191 cap). It hides after 30 s without pointer movement
(`OVERLAY_IDLE_CLOSE_S`). A drag still held at that point ends and is saved. It does not drift or orbit.
The CPU fallback renderer draws no bracket.

### Placement menu

You can also place desktops from inside the glasses, without the panel. Press **Ctrl+Alt+L** during a run to open the placement menu, and Ctrl+Alt+L or Escape to close it. (Ctrl+Alt+P was already taken by pose prediction.) The menu uses the help card's font, colours and spacing. It shows the desktop selector (XR-1, XR-2, XR-3), the seven settings as sliders with their current values, and the keys at the bottom. The menu keys are listed in the keybind table above. They work only while the menu is open, so the arrows, Tab, R and Escape type normally once it is closed.

The settings, in order: **Left/right**, **Down/up**, **Distance**, **Yaw** (turn left or right), **Pitch** (tilt up or down), **Roll** (tilt sideways) and **Curvature**. Each is a row with its label, a slider and its current value. The slider's ends are the setting's limits, the same as the panel's sliders, and the stretch from the flat value to the knob is coloured. The menu opens on the desktop you are looking at. Desktop and setting selection wrap round. R puts every setting of the selected desktop back to flat.

**Mouse mode.** With the menu open, press **Ctrl+Alt+M** to use the mouse on the sliders in the glasses. A ring pointer appears on the selected slider's knob and the desktop cursor is hidden. Moving the mouse sideways moves the pointer along a slider. Moving it up or down steps one row at a time, up to the XR-1/XR-2/XR-3 selector above the sliders. Click anywhere on a slider to jump it there, hold the button and drag to move it, and the desktop follows live. Clicking a desktop tab selects that desktop. While mouse mode is on, left clicks go to the menu, not to your windows, and cursor follow is paused. The keyboard keeps working: the arrows, Shift, Tab and R act as usual. Press Ctrl+Alt+M again or Escape to leave mouse mode. The cursor comes back where it was when mouse mode started, and the menu stays open. Escape again closes the menu; Ctrl+Alt+L closes it at once, mouse mode included. Any mouse movement or click in mouse mode counts as input for the 30 s idle close described below. The sliders and the pointer use only the cards' dimmed colours, nothing brighter than their text. Ctrl+Alt+M is bound only while the menu is open. It clashes with none of Omarchy's binds or this project's; `python -m tools.native_spike.bind_check` checks that against your Hyprland config with `Hyprland --verify-config` (see Tests).

Each key press moves the desktop straight away. There is no Save or Apply step. Changes are saved to the glasses model's profile shortly after the last key press, and again when the menu closes. The menu changes placement only. It does not change the run's consent, capture or the panel's buttons.

The help card and the placement menu protect the glasses' micro-OLED panels from image retention (a faint copy of bright, static text that stays for a while after it closes). Their text and accent are dimmed to about 70% and 75% of full brightness. Both cards stay perfectly still while shown. Both close by themselves after 30 s without input: a run key or a panel placement change. The help card's footer says so. Typing in a window doesn't count. Ctrl+Alt+L and Ctrl+Alt+H reopen them. The timeout is `OVERLAY_IDLE_CLOSE_S` in `tools/native_spike/view.py`. Its copy `kOverlayIdleCloseS` in `spike/include/xr/hud.hpp` feeds the footer note, and a test keeps the two equal.

## Tests

Bare `python -m unittest` from the root finds nothing. Run each suite explicitly:

```sh
python -m unittest discover -s tests/probe -v
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/xr_controller -v
python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -v
ctest --test-dir build/release --output-on-failure
```

Check that this project's run, send-window, menu and mouse-mode binds clash with nothing in your Hyprland config (exit 0 no clash, 1 clash, 2 could not check):

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
- Run binds use Ctrl+Alt chords. The only unmodified binds are modal: the placement menu's keys while it is open, and
  the left button while the pointer is on a desktop corner in the overview (corner resize). Both are removed when no
  longer needed.
- The isolated VM test path (`isolated-run`, `isolated-recover`) needs separate VM consent and refuses to run on the physical session.

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
