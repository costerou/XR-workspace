# Scene wiring: one module per feature

The logic and rasters are ported (`pose.rs`, `predict.rs`, `hud.rs`, `glass.rs`, `video/`, …) and `GlCompositor`
draws every layer. What is left is the wiring from `Presentation` to them. This note splits that wiring into ten
modules under `src/shell/`. Each feature can then be wired in its own thread without editing the same lines as
another thread.

## The rule

A feature thread changes **its own module** (and its own tests). It does not edit `main.rs`, `scene.rs`, `captures.rs`
or `shell.rs` unless this note names that edit for the feature (see "Shared edits" below).

- **Ops** are already routed. `main.rs` `apply` calls one `Presentation` method per op, as `spike/src/main.cpp` does.
  The method's stub calls `self.not_drawn(Op::…)`, which logs `control: <op> received; not drawn in this build` once
  per op. The log text is the same as before the scaffold. Replace the stub's body.
- **Launch flags** are parsed and checked in `main.rs` as `spike/src/main.cpp` checks them: `--pose-feed` must be
  `xrld` and needs `--capture`, the descriptors must be distinct, and the continuity file must be a sealable regular
  file of this user, at most 64 KiB. Each flag is gated by its module's `*_supported()` function, which returns
  `false` today, so the flag still exits 2 before any descriptor is touched (pinned by `tests/launch_flags.rs`). To
  take the flag, return `true` and replace your case in `tests/launch_flags.rs`. After a successful open, `main.rs`
  then calls `set_depth_fd`, `enable_tracking(POSE_PREFIX, 0, 0, false)` and `restore_continuity`, in the C++ order.
  It calls `save_continuity` before `close`.
- **Frame**: `captures.rs` `draw_gpu` builds one `Scene` (`src/shell/scene.rs`) per GPU frame:
  1. The core fills the context: `now_ms`, the canvas size, `half_w`/`half_h`, `overview`, `zoom`, `focus`, `sizes`.
     It also fills the defaults: `head`/`world`/`environment_world` are the identity, `tiles` is the view's blended
     layout, and `own` is the default placements.
  2. `Features::contribute` calls each module's `contribute(&mut self, &mut Scene)` in wayland.cpp's order: tracking,
     scene_layout, roll, cards, handles, menus, immersive, pointer.
  3. The core computes the zoomed placements from `scene.tiles` and `scene.own`. It writes the maximize geometry and
     sets the background with `scene.environment_world`. Then `Scene::apply` hands the feature inputs to
     `GlCompositor`.
  4. `video_shell::VideoShell::draw` may draw the frame instead (a VR video). If it does not, the core calls
     `draw_and_swap(scene.tiles, placements, …, scene.head, …)`.

  An `Option` input left `None` leaves that `GlCompositor` input untouched. This is how the frame stays unchanged
  while a feature is unwired. A feature that sets an input sets it **every** frame, as the C++ does. Several
  features share the glass frame: each one writes its own fields through `scene.glass()`.
- **Redraw**: each module's `animating(now_ms)` is ORed into `draw_desktops`' redraw test. A setter that changes
  what is drawn also sets `self.state.capture.dirty = true`.
- **Status datagram**: `Presentation::status_extras` fills the capture fields. It then calls `Features::status`,
  which calls `video_shell` (`video`) and `tracking` (`tracking`, `pose_age_ns`). `StatusExtras::pose_age_ns` is sent
  as `pose_age_ms` with three decimals, in the C++ position.
- **Exit report** (`capture_report`): the module hooks write `,"key":{…}` in wayland.cpp's order:
  - Before `surface`: help, binds, restore_band, placement_menu, options_menu, resize_handle, move_handle,
    overview_pointer, theme, tracking.
  - Before `composition`: latency, view, popup_font, video.
  - After `composition`: depth.

  The Rust report writes these sections only when there are captures, as before.
- **Supervisor lines**: `main.rs` already sends `take_roll_geometry` and `take_help_geometry` in the C++ order
  (overview, roll, help, maximize, owner readback). The owner readback's `video` value comes from
  `VideoShell::owner_readback`, which writes `null` today.

## Modules

C++ references are to `spike/src/wayland.cpp` unless a file is named. The gate tests are from plan
`2026-11-10-rust-rebuild-remaining.md`, tasks 4 and 5. Each `capture_e2e_rust_<name>` runs the C++ test
`capture_e2e_<name>` with its arguments plus `--renderer "${XR_RUST_RENDERER}"`. A "twin" is a Rust test in
`tests/` that ports a C++ ctest.

### `tracking.rs`: tracking (task 4)

- **Calls:** `enable_tracking(prefix, supervised_projection, smooth_ms, level_gravity)`, `recenter`, and
  `view_tracking(&View)`. `view_tracking` is called from `set_view` and takes mode, cursor follow, prediction, typing
  and stabilization. The launch gate is `pose_feed_supported()`; `POSE_PREFIX` is the pose prefix.
- **C++ to port:**
  - `Presentation::enable_tracking` (1378), `recenter` (1656), and the tracking half of `set_view` (1662).
  - The tracked block of `draw_test_pattern`'s GPU path (1738-1800): `PoseFeed`/`MotionFeed` open and retry,
    stabilization, `pose.update`, the prediction horizon, and the smoother.
  - The ModeController parts: `modes.update`, `land_maximize`, and `focus_aim` (389).
  - `update_fov` (567), `turned` (480), `land_turns` (444) and `capture_centre` (437).
  - The `tracking_hint` pop-up. It is drawn as `scene.popup` when no mode pop-up is up (1822-1826); coordinate with
    `cards`.
  - `status_json`'s `tracking` and `pose_age_ms` (1107, 1121).
  - `capture_report`'s `tracking` (1193) and `latency` sections (1204-1253), with `pose_reader_json` (1142).
- **GlCompositor inputs:** `scene.head` (the `draw_and_swap` view), `scene.world`, `scene.environment_world` and the
  `half_w`/`half_h` FOV (the driver's FOV).
- **Gates:** `capture_e2e_rust_tracked`, `_feed_drop` and `_feed_gone_video_loss` (the last one together with
  `video_shell`). The ctest twin is `level_frame` (PoseState::reference, canvas up).
- **Shared edit (yours alone):** the latency tracker needs the read time per feedback ticket and the presented time.
  Add those hooks in `shell.rs` (`commit` and the `WpPresentationFeedback` dispatch). Late latch (plan task 6) comes
  after you, in the same place.

### `scene_layout.rs`: placements, accent, edge pan, desktops, tile ease (task 4)

- **Calls:** `set_placements(&[[i32; 4]], glide_ms)`, `set_accent(r, g, b)`, `set_edge_pan(Option<&EdgePanMove>)`,
  `set_desktops(outputs, renew)` and `ease_tiles()`.
- **C++ to port:**
  - `set_placements` (1472), `set_accent` (1392), `set_edge_pan` (1657), `set_desktops` (1366), `ease_tiles` (1554).
  - `ease_placements` (350), `ease_sizes` (255), `target_placement`, `resolved_placements`, the `tile_ease` in the
    render path (1810), and `pan_dot` (1899, shared with `pointer`).
  - `capture_report`'s `theme` (1190) and `view` (1255) sections. The `view` section's `roll_updates` comes from the
    `RollState` that `report_view` is handed.
- **GlCompositor inputs:** `scene.own` (placements), `scene.tiles` (tile ease), `scene.accent` (`set_accent`), and the
  pills of `scene.glass()` (`pills`, `pill_levels`, together with `handles`).
- **Gates:** `capture_e2e_rust_edge_pan_gl-dmabuf`.
- **Shared edit (yours alone):** `set_desktops` starts and stops captures. That is `CaptureState` in `captures.rs`
  (`Capture::new`, `GlCompositor::insert_source` / `remove_source`). The edge pan's `ViewAnimator::set_pan` is on
  `self.state.capture.view`.

### `roll.rs`: level bars and roll geometry (task 4)

- **Calls:** `set_roll(&[i32; 4], &[bool; 4], hover, side, active)`, where side is -1 none, 0 ccw, 1 level, 2 cw.
  Also `take_roll_geometry(now_ms)` and `resend_roll_geometry`, with `ROLL_GEOMETRY_MS`.
- **C++ to port:** `set_roll` (1508), `take_roll_geometry` (1526), `resend_roll_geometry` (1553), and the glass
  `bars` lines of the render path (1877-1878).
- **GlCompositor inputs:** `scene.own[i].roll`, and `bars`, `bar_hover`, `bar_segment` and `bar_active` of
  `scene.glass()`.
- **Gates:** twins of `roll_level` and `level_frame`. The second is shared with `tracking`: the tilt needs the pose
  reference.

### `handles.rs`: corner caps, glass pills, Step-Back Band (task 4)

- **Calls:** `set_resize_handle(visible, desktop, corner, active)`, where corner is 0 tl, 1 tr, 2 bl, 3 br. Also
  `set_move_handle(visible, desktop, hover, active, gear)` and `set_restore_band(open)`.
- **C++ to port:** `set_resize_handle` (1479), `set_move_handle` (1492), `set_restore_band` (1486), and the
  render-path lines for `restore_alpha`, `pill_levels`, `gear_levels` and `cap_*` (1863-1882). Also
  `capture_report`'s `restore_band` (1180), `resize_handle` and `move_handle` (1187-1188).
- **GlCompositor inputs:** `scene.glass()`: `restore_alpha`, `pills`, `pill_levels`, `gear_levels`, `cap_hover`,
  `cap_corner` and `cap_active`.
- **Gates:** `capture_e2e_rust_restore_band`, plus twins of `resize_handle` and `restore_band`.

### `pointer.rs`: overview free pointer (task 4)

- **Calls:** `set_overview_pointer(visible, x, y, held, directed, az, el, ghost_w, ghost_h, target, look)`. These
  are the C++ arguments. A missing `direction` comes as `directed = false`, and a missing `drag` as `0, 0, -1`.
- **C++ to port:** `set_overview_pointer` (1499), the render path's free pointer and window drag (1896-1906), and
  `capture_report`'s `overview_pointer` (1189).
- **GlCompositor inputs:** `scene.pointer` (`set_free_pointer`) and `scene.drag` (`set_window_drag`).
- **Gates:** `capture_e2e_rust_pointer_look_gl-shm`, plus the `free_pointer` twin.

### `cards.rs`: help card, bind panel, mode pop-ups (task 5)

- **Calls:** `set_help(visible, rows, sections, turn)`, `take_help_geometry(now_ms)`, `resend_help_geometry` (with
  `HELP_GEOMETRY_MS`), `set_binds(&Binds)`, and `view_popups(&View)`. `view_popups` is called from `set_view`.
- **C++ to port:**
  - `set_help` (1446), `set_binds` (1458), `take_help_geometry` (1594), `resend_help_geometry` (1621), and the
    pop-up half of `set_view` (1662).
  - The render path's `set_popup`, `set_help` and `set_binds` (1822-1857).
  - `capture_report`'s `help` and `binds` (1173-1179), and `popup_font` (1265).
- **GlCompositor inputs:** `scene.popup`, `scene.help` and `scene.binds`.
- **Gates:** the `native_help` twin. In it, `main.rs`'s `HELP` stops listing anything as "not yet in this build".
  That is the one `main.rs` edit, and it is yours, made last.

### `menus.rs`: options panel and placement panel (task 5)

- **Calls:** `set_options_menu(&OptionsMenu)` and `set_placement_menu(&PlacementMenu)`. The wire payload carries
  both of what `spike/src/main.cpp` splits into `PlacementMenuState` and the held slider's `surface`. Convert it to
  `hud::PlacementMenuState` here.
- **C++ to port:**
  - `set_options_menu` (1461) and `set_placement_menu` (1468).
  - The render path's glass and options overlay block (1858-1895): `glass.frame`, `options_overlay`,
    `options_surface` and `options_evidence`.
  - `capture_report`'s `placement_menu` and `options_menu` (1182-1186), and `options_evidence.hpp` and
    `options_overlay.hpp`.
- **GlCompositor inputs:** `scene.glass()`: `expand`, `content`, `top`, `surface` and every `options*` field, plus
  `scene.glass().image` and `options_image`.
- **Gates:** `capture_e2e_rust_options_roundtrip` and `_open_panel`, plus the `options_evidence` twin.

### `video_shell.rs`: VR video (task 5)

- **Calls:** `video_open(OwnedFd, &VideoFormat, title)`, `video_close`, `video_pause`, `video_seek(seconds)`,
  `video_format(&VideoFormat)`, `video_controls(visible)`, `video_quality(filter, deband)` and
  `video_menu(visible, rows, selected)`. `video_open` owns the descriptor; today the stub closes it.
- **C++ to port:**
  - The `video_*` calls (1417-1445) and the render path's video branch (1834-1850).
  - `status_json`'s `video` (1104), `capture_report`'s `video` (1271), and the owner readback's `video`
    (`video_readback.hpp`).
  - `video_mode.cpp`, `video_player.cpp`, `video_gl.cpp` and `video_ipc.cpp`.
- **GlCompositor inputs:** `VideoShell::draw(gl, scene)` draws and swaps in place of the desktops and returns
  `Some(ok)`. It reads `scene.world`, `half_w` and `half_h`, and draws the background through
  `GlCompositor::draw_environment`. A `draw_scene_and_swap` equivalent goes into `gl.rs` if the video thread needs
  one.
- **Gates:** `capture_e2e_rust_feed_gone_video_loss` (with `tracking`), plus the `video_owner_events` and
  `owner_readback` twins.

### `immersive.rs`: immersive 3D and depth (task 5)

- **Calls:** `set_immersive(&[bool; 4], global, stereo, hover, tail, &ImmersiveParams)` and
  `set_depth_fd(OwnedFd)`. The launch gate is `depth_fd_supported()`.
- **C++ to port:**
  - `set_immersive` (1517) and `set_depth_fd` (1525).
  - `depth_tick` (307), called before the pose is read, and the stereo settings (337).
  - The render path's glass `immersive*` lines (1879-1880), and `capture_report`'s `depth` (1274).
  - The owner readback's `immersive` comes from `GlCompositor::stereo_readback` already.
- **GlCompositor inputs:**
  - `scene.stereo` (`set_stereo`), and `immersive`, `immersive_global`, `immersive_hover` and `immersive_tail` of
    `scene.glass()`.
  - `ImmersiveState::before_draw(gl)` is called before the frame is laid out. Use it for `set_depth` and
    `keep_depth`.
- **Gates:** `capture_e2e_rust_immersive_<mode>`, `_immersive_scaled` and `_immersive_engine_dies`, plus the
  `immersive` twin.

### `continuity_shell.rs`: continuity (task 5)

- **Calls:** `restore_continuity(Option<BorrowedFd>) -> bool`, after which `main.rs` logs `continuity: restored`,
  and `save_continuity(Option<BorrowedFd>)`. The launch gate is `continuity_fd_supported()`.
- **C++ to port:** `restore_continuity` (1622) and `save_continuity` (1641). `continuity.rs` holds the format.
- **GlCompositor inputs:** none. It restores the view's, the placements' and the pose's state into the other modules'
  state. Read that state through `self.state.features`.
- **Gates:** `tests/native_spike/test_continuity.py` against the Rust binary. There is no `capture_e2e_rust_*` test.

### Routed in `main.rs`, no module

`select`, `recover` and `status` are receipted only, as in the C++ (`Presentation::receipted_only`). The plan's task 4
gates `capture_e2e_rust_bad_control`, `_bad_control_fatal` and `_control_closed` exercise this routing and the control
stream. The tracking thread registers them with its batch.

## Registering the gate tests

`spike/CMakeLists.txt` lists the Rust twins inside `if(XR_RUST_RENDERER)` (around line 734). Every feature thread
adds its own lines there. Keep them in a block of your own, headed `# rust: <module>`, appended after the existing
ones, so two threads' additions merge as plain appends.
