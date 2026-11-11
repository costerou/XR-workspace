# The glasses' view controls: hand-off for the feature ports

A Rust-supervised run (`xr-workspace supervisor`, `managed::real`) presents the desktops but has none of the glasses'
view controls. `src/view_controls.rs` is the scaffold for them: one submodule per feature, each a stub with the Python's
entry points, called from the run loop at the Python's points in the Python's order. Until a feature is ported its
`open` gives `None`, and the run behaves exactly as before. A feature's port should only have to edit its own files,
listed under each module below. Where it must edit a shared file, the module that owns that edit is named.

The Python originals are in `tools/native_spike/`. All the rules of the remaining-work plan still hold. Wire protocols
and file formats do not change. No new crates. Fakes only: no real run, no device, never the live compositor. Python
runs with `/usr/bin/python3` and `TMPDIR=/tmp`, and pytest suites run through `uv`.

## Where the Python calls in, and where the Rust does

The managed order already exists, in `managed.rs` (`run_managed` / `run_owned` / `start_and_keep`), with a `Host` step for
each point. `managed::real::RealHost` calls the stubs from those steps.

| Python (`tools/native_spike/managed.py`, `supervisor.py`) | Rust call site | Stub |
|---|---|---|
| `_run_owned`: `create_outputs` fails → `create_failed(exc)` (`_setup_failed`) | `Host::discard_unusable_setup` | `setup::discard_unusable` |
| `_run_owned`: `DesktopManager(broker, journal, specs, ...)` before `guard()` | `Host::desktop_manager` | `desktops::Manager::open` |
| `_run_owned`: `tracker_for(...)`, `memory.place(names)` | `Host::window_memory`, `window_memory_place` | `window_memory::Tracker::{open, place}` |
| `_run_owned`: `memory.finish()` after `adopt_windows` | `Host::window_memory_finish` | `window_memory::Tracker::finish` |
| `_run_owned`: `broker.cursor_reader()` | `Host::cursor_reader` | `overview_pointer::cursor_reader` |
| `with view_controls(...)`: the body, before `yield` | `Host::view_controls_enter` | `ViewControls::enter` |
| `view_controls`'s `finally` | `Host::view_controls_exit` | `ViewControls::exit` |
| `_run_owned`'s `launch()`: `manager.specs`, `manager.guard()` | `RealHost::launch` | `ViewControls::live_specs` |
| `_run_owned`: `depth_fd = depth_memory.fd` to every `_launch` | `RealHost::launch` (`Launch::depth_fd`) | `ViewControls::depth_fd` → `immersive::Immersive::depth_fd` |
| `_launch`: `parent.sendall(control.initial_frame())` | `RealHost::launch` (`Launch::initial_frames`) | `ViewControls::initial_frame` → `view::ViewControl::initial_frame` |
| `Supervisor(control=...)`: the selector on `control.server.socket`, `control.events`, `control.wake` | `RealHost::launch` (`Launch::hook`) | `impl LoopHook for ViewControls` |
| `Supervisor._control_ready` → `control_request`, `control.on_message` | `LoopHook::ready` | `socket::Server::control_request`, `ViewControls::on_message` |
| `Supervisor._events_ready` → `control.on_events` | `LoopHook::ready` | `view::ViewControl::events_ready` |
| `Supervisor.status_bytes`: `control_applied`, `owner_readback`, the geometry datagrams, `self.status` | `LoopHook::status` | `view::ViewControl::status`, `video::Video::status` |
| `Supervisor._poll` → `control.poll(now)` | `LoopHook::poll` / `deadline` | `view::ViewControl::{poll, deadline}` |
| `_send_frame` with a `video.FdFrame` | `LoopHook::take_fds` | `Frames::fds` |
| `ViewControl.supervisor.pause_guard()` / `replace_guard(desktops.guard())` (`view.py` `_start_change`, `_desktops_done`) | `LoopHook::guard_change` | `desktops::Manager::guard_change` |
| `keep_renderer(switching=run_3d.switching)` | `RealHost::keep_renderer` (`expected`) | `ViewControls::switching` → `immersive::Immersive::switching` |
| `keep_renderer`: `serve_without_renderer(control, seconds)` | `RealHost::keep_renderer` (the hold's wait) | `ViewControls::serve_without_renderer` → `socket::Server` |
| `keep_renderer`: `control.renderer_restarted()` | `RealHost::keep_renderer` (after `back`) | `ViewControls::renderer_restarted` |

The loop's seam is `supervisor::hook::LoopHook`, which `supervisor.rs` waits on. Besides `wake_fd`, `deadline` and `poll`,
it now has default methods: `sources` and `ready` for descriptors (the control socket and the event stream), `status` for
status datagrams, `take_fds` for descriptors that go with a frame, and `guard_change` for a desktop change's
`pause_guard` / `replace_guard`. `tests/supervisor_hook.rs`
(`a_source_its_status_and_a_frame_with_a_descriptor`) holds the loop to them.

`ViewControls::enter` runs in `view_controls`'s order:

1. `socket`
2. `binds`
3. `view` (the placements restored)
4. `overview_pointer`
5. `send`
6. `privacy`
7. `desktops`, attached to the view
8. `immersive`
9. `window_memory`, attached to the view
10. the event stream
11. `keybinds`
12. `binds.install` with the keys, then `keys.installed()` and `keys.startup_toast()`
13. `video`
14. the options text and binds

`ViewControls::exit` runs in its `finally`'s order:

1. options
2. desktops
3. video
4. send
5. privacy
6. immersive
7. `flush`, then `save_setup`
8. the window memory's last capture
9. `stop_menu_binds`
10. keys
11. the sampler
12. `remove_view_binds`
13. events
14. pointer
15. socket

`ViewControls::on_message` gives VR video the first look (`VideoControl` wraps the view), then the view.
`view::ViewControl::on_message` already routes the other features' ops in the Python's order: `resize_cancel`, the
typing-mode gate, `layout_reset`, the resize ops, the Super ops, the send ops, 3D, privacy, and the key bind panel. Each
of these calls the feature's stub. The view's own arms are marked by comments where they go.

## Suggested threads

There are about eight threads. Each owns the modules listed and their files.

1. `socket` + `view`
2. `binds` + `keybinds`
3. `overview_pointer`
4. `send` + `privacy`
5. `immersive`
6. `video`
7. `desktops` + `setup`
8. `window_memory`

The end-to-end cases need thread 1, because ops reach a feature only through the socket and the view's routing. Until
thread 1 lands, test each feature's module directly against its Python class.

## Shared edits, and who owns each

| Shared file | Owner | What |
|---|---|---|
| `tests/managed_real.rs` helpers (`Session`, `parity`, `observe`) | `socket` | A case action that sends a control frame once the renderer is up (through the Python `xr_control.py` and the Rust `xr-control` alike), and the renderer's received frames in `Observed`. |
| `tests/managed_real/renderer.py` | `socket` | Record every frame after `start` (and the descriptors that came with it) to `$XDG_RUNTIME_DIR/renderer-frames.jsonl`. |
| `tests/managed_real/oracle.py` | `socket` | "Narrowing the oracle" (below). After that, each port deletes its own line. |
| `src/managed/real.rs` `keep_renderer` | `immersive` | An expected exit: the "Switching display mode" notice, `settle=0.0`, continuity kept while the USB identity holds. |
| `src/managed/real.rs` `run` | `setup` | `RunOptions::saved_setup` from the consent's desktops (not `profiles.default_setup`), and the glasses profile path. |
| `src/managed/real.rs` `guard` | `desktops` | The guard command for a mid-run `replace_guard` (`Manager::guard_change`) is `RealHost::guard(&live)`; hand the manager a way to build it. A later renderer already captures and guards `live_specs` (`manager.guard()` is `ManagedGuard(broker, output, live)`). |
| `rust/README.md`, managed-run section | each port | Remove the feature from "Not ported" and say what was ported. |

Each port also owns these per-feature files, which exist already so that no two ports edit the same lines:

- `tests/managed_real/cases/<module>.rs`: the parity cases, a module of the `managed_real` test.
- `reply_<module>` in `tests/managed_real/compositor.py`: the fake compositor's answers to that feature's requests. It is
  asked before the built-in replies. `None` means the request is not this feature's.

## Narrowing the oracle

Done (the `socket` port). `oracle.py` runs the shipped `view_controls`, with its real `PrivateServer` and the
supervisor's `control_request` / `serve_without_renderer`, and takes each feature still a stub out with one entry in
`STUBS`, keyed by module:

| Entry | What it does on the Python side |
|---|---|
| `view` | `ViewControl.on_message`, `initial_frame`, `poll`, `on_events` send nothing; `status_reply` is `{}`; `window_context` is `{}` (the window memory's captures have no placements); `flush`, `save_setup`, `renderer_restarted` do nothing; `event_stream` and `install_options_binds` hidden |
| `binds` | `install_view_binds` / `remove_view_binds` silenced, `reinstall_view_binds` hidden, the session mode's `monitors` read in `view_controls`'s body answered `[]` without a request, and `install_video_binds` hidden (the Rust video keys need `bind_args`) |
| `overview_pointer` | `unified_cursor.UnifiedSampler` a stand-in (the `UnifiedPointer` stays: it sends nothing by itself) |
| `immersive` | `immersive.find_engine` gives `None` |
| `keybinds` | `binds_text` and `open_key_panel` hidden, `keybinds.BindControl` a stand-in (`bind_kwargs()` is `{}`). Ported, but the Rust run opens it only with `binds`'s `bind_args`: delete with `binds`. |
| `desktops` | `adopt_windows` hidden (the pills' window mover) |

`send`, `privacy`, `window_memory` and `video` have no entry: they are ported and run on both sides. The helpers are
`patch(module, name, value)`, and `hide(*methods)` and `quiet(*methods)` on the client wrapper (`Client`). A port deletes its own entry, and, if a helper it used has
no other user, may leave the helper. If its feature then needs more than deleting (a stand-in the next feature relied
on), it says so in its report. `tests/managed_real.rs`'s module doc lists the implicit exclusions: no
`window-memory.json` and no saved `profiles.json` in a `Session`. The `window_memory` and `setup` ports add the files
their cases need.

## Helpers for a run case (`tests/managed_real.rs`)

- `parity_acting(case, Some(act))` is `parity(case)` with `act(&Session, Who) -> serde_json::Value` called on each run
  once its renderer is up (`run/renderer-started`), before the case's own Stop or unplug. It returns
  `(Observed, Session, [python, rust])`: the Rust run's observation, its session, and what `act` gave back on each run.
  The case compares the two values. Use a case name that already stops the run (`stop`, `hold`) unless `act` ends it.
- `Session::control(who, op, args)` sends one op the way a bound key sends it: through `who`'s own key helper
  (`tools/native_spike/xr_control.py` for Python, the `xr-control` binary for Rust), with the run's socket, `r1` and the
  session. `args` follow the op (`binds_key`'s `MODS CODE`, `options_scroll`'s delta). It gives the helper's exit
  status: 0 for sent, 1 for no run listening.
- `Session::status(limit)` asks the panel's `status` on the control socket, the way the controller asks it, and retries
  until something answers or `limit` passes. The reply comes back as JSON, checked to answer the request, with its
  `request_id` removed and its session renamed `SESSION`.
- `Session::session_id()` and `Session::control_socket()`: the run's session (from its journal) and socket path.
- `Observed::frames`: every frame the renderers got after their start frame, as `<launch> <frame> <descriptors>`, with
  the same normalizations as the trace. `renderer.py` writes them to `run/renderer-frames.jsonl` with the descriptors
  that came with each frame's first byte (`{"type": "file", "size": n}`, ...). `parity` compares them, so a case that
  sends an op checks the renderer's frames without extra code.
- The `hold` case: the fake renderer's first launch takes the glasses away (`run/fake-glasses-gone`; `reply_socket`
  hides DP-2 from the monitor reads while it exists) and ends with 5, as a USB drop ends a renderer. The run holds until
  `act` removes the file; then `survive.SETTLE_S` passes, a second renderer starts, and `renderer-started` is written
  again. In this case, a run of identical monitor reads in the trace counts as one, and `usb_holds[].seconds` is only
  compared for being positive. `cases/socket.rs` (`status_through_a_hold`) is the example.

Fake executables still go through `write_script`.

## How to compare Rust against the Python on fakes

There are two levels. Use both.

1. **The module, against its Python class.** The Python tests drive each class with a recording fake client. To port a
   case, record what the Python class sends, then replay it in Rust:
   - Record the Python's requests and results with a corpus script, as `tests/native_spike/lua_corpus.py` records
     `rust/corpus/hyprland/transcripts.json` and `pointer_trace_corpus.py` records `rust/corpus/pointer/traces-*.json`.
   - Replay them against the Rust module. Answer its `Client` with a stand-in socket, as `tests/hyprland_transcripts.rs`
     does (the n-th connection gets the corpus's n-th reply).
   - Compare the request bytes in order, the frames to the renderer, and the toasts.
   - For threads, give the module a worker you can join, and compare after `close` (the Python's `close(timeout)`).
2. **The run, against the Python runner.** Add a case to `tests/managed_real/cases/<module>.rs` using the `socket`
   port's helpers:
   - Start both supervisors on `compositor.py`.
   - Once the renderer is up, send the feature's op through the bind helper.
   - Compare the request trace, the renderer frames, the journal, the files the feature writes, and the result, as
     `parity()` does.
   - Delete the feature's oracle patch in the same commit.

   Fake scripts go through a child-process writer (`write_script` in `managed_real.rs`): an in-process write races
   sibling forks into ETXTBSY.

---

## Modules

### `socket`: the private control socket

- **Python**:
  - `ipc.py`: `PrivateServer` (`__init__`, `receive`, `close`, `path`, `socket`, `queue`), `ControlQueue`,
    `decode_control`, `check_peer`, `private_dir`
  - `supervisor.py`: `control_request`, `serve_without_renderer`, `Supervisor._control_ready`
- **Ported already**:
  - `xr_wire::run_control` (`socket_path`, `Frame`, `Queue`, `StatusReply`; the corpus is `rust/corpus/sockets/frames.json`)
  - `crate::control` (the `xr-control` helper; `tests/control_parity.rs`)
  - `controller_server.rs`, a same-UID socket loop to model this on
- **Renderer ops**: none. It answers `status` on the control socket with a `StatusReply`, whose body comes from
  `view::ViewControl::status_reply`.
- **Python tests**:
  - `test_ipc.py`
  - `test_control_wire.py`
  - `test_control_plane_perf.py`
  - `test_supervisor.py`, the control request cases
  - `test_survive.py` and `test_two_drops.py` (`serve_without_renderer` during a hold)
  - `test_supervisor_faults.py` (a bad peer or frame is never fatal)
- **Parity** (ported):
  - `tests/control_server_corpus.rs` replays `rust/corpus/sockets/server.json`
    (`tests/native_spike/control_server_corpus.py`): the Python's `PrivateServer` serving every run frame case of
    `frames.json` and the socket's own (split, stalled and early-closed clients, a hold). The verdicts, the request ids
    taken and the status replies match byte for byte. A payload another module checks (`owner`) is taken here.
  - `tests/managed_real/cases/socket.rs`: the panel's `status` during the run and during a hold, answered the same by
    both, and a key sent through each run's own helper.
  - The status body is `view::ViewControl::status_reply(held)`. `held` is true during a hold
    (`serve_without_renderer`'s `{}`), and the reply has no body while the view is a stub.
- **Owns** (done):
  - the `managed_real.rs` helpers ("Helpers for a run case")
  - `renderer.py` frame recording
  - narrowing `oracle.py`

### `view`: `ViewControl`

- **Python**:
  - `view.py`, from `class ViewControl`: `on_message` and everything it calls for its own ops, `initial_frame`,
    `status_reply`, `poll`, `on_events`, `_reload_work`, `flush`, `save_setup`, `renderer_restarted`, `set_*_geometry`,
    `set_owner_readback`, the options menu (`options_*`), help, environment, and placements and rolls (`_rolls`,
    `_watch_rolls`)
  - `managed.py`: `store.load()`, `_restored`, `_initial_placements`, `client.event_stream()`
  - `supervisor.py` `status_bytes`: the datagrams named in the module doc
  - `options_text.RuntimeText`
  - `profiles.py`: `LayoutStore`, `ProfileStore`, `PlacementStore`, `save_setup`
- **Ported already**: `supervisor::view` (`ViewState`, the frame encoders, `layout.json`), `environment`,
  `options_geometry`, `options_text`, `bump`, `orbit`, `restore_band`
- **Renderer ops**: `view`, `recenter`, `style`, `help`, `placement`, `placement_menu`, `options_menu`, `environment`
- **Python tests**:
  - `test_view.py`
  - `test_view_corpus.py` (`rust/corpus/view/view.json`, replayed by `supervisor/corpus_tests.rs`)
  - `test_view_menu_wiring.py`
  - `test_placement.py`, `test_placement_menu.py`
  - `test_help_pages.py`
  - `test_environment.py`
  - `test_stabilization.py`
  - `test_options_*.py`
  - `test_panel_follows_focus.py`
  - `test_bump.py` (the saves)
  - `test_remember_setup.py` (`_restored`)
  - `test_profiles.py`
- **Parity**:
  - Drive `ViewControl` with the Python test's frames and compare the renderer bytes. Extend `view.json` with
    `on_message` sequences.
  - Run cases: Ctrl+Alt+Up and Ctrl+Alt+Left frames, and the saved `profiles.json` at the end.
- **Owns**: nothing outside its file. It is the hub: the other modules' arms in `on_message` already call them.

### `binds`: the Ctrl+Alt binds

- **Python**:
  - `managed.view_controls`: `bind_args`, `view.session_mode`, `after_reload`, `menu_binds`, `typing_binds`, and in the
    `finally` `stop_menu_binds` and `remove_view_binds`
  - `view.py`: `_reload_work`, `_sync_menu_binds`, `_menu_bind_work`, `_typing_toggle`, `_sync_typing_binds`,
    `_typing_bind_work`, `stop_menu_binds`
- **Ported already**: `lua::view`, and `Client::{install,reinstall,remove}_view_binds`, `install_menu_binds`,
  `remove_menu_binds`, `install_options_binds`, `apply_session_mode`, `apply_wide_mode` and `park_presentation`
- **Renderer ops**: none. The style frame after a reload is the view's.
- **Python tests**:
  - `test_view.py`, the bind install cases
  - `test_typing_mode.py` (`test_run_wires_the_swap_and_a_reload_reinstalls_the_current_mode`)
  - `test_view_menu_wiring.py` (`test_run_wires_modal_binds...`)
  - `test_presentation_layout.py` (`test_reload_preserves_120hz_after_parking`)
  - `test_lua_corpus.py` (already `tests/lua_corpus.rs`)
- **Parity**:
  - A run case comparing the `/eval` trace: the binds go in at the start and out before recovery.
  - A `configreloaded>>` event, which needs `reply_binds` and the event socket's line, and then reinstalls.
- **Owns**: nothing shared.

### `keybinds`: the key bind panel

- **Python**:
  - `keybinds.py`: `BindControl` (`bind_kwargs`, `installed`, `startup_toast`, `on_message`, `stop`), `load`,
    `config_path`, `DesktopBinds.from_text`
  - `managed.view_controls`: `keys_path`, `client.binds_text()`
- **Ported already**: `supervisor::keybinds` (`BindControl`, which queues `Job`s for a `BindWorker`), `bind_check`, and
  `rust/corpus/supervisor/{keybinds,bind_check}.json`
- **Renderer ops**: `binds`
- **Python tests**:
  - `test_keybinds.py` (`test_the_run_reads_the_desktops_binds_first...`)
  - `test_keybinds_corpus.py`
- **Parity**:
  - A run whose `j/binds` holds one of the run's keys (from `reply_keybinds`): both leave that bind out.
  - Ctrl+Alt+K frames.
- **Owns**: nothing shared.

### `overview_pointer`: the overview free pointer

- **Python**:
  - `managed.view_controls`: `UnifiedPointer(control)`, `rolls`, `control.wake`, and
    `UnifiedSampler(...).start()` / `set_names` / `stop()`, then `pointer.close()`
  - `ownership.OwnershipBrokerClient.cursor_reader`
  - `unified_cursor.py`, `free_pointer.py`, and `input_lock.py`
- **Ported already**: `supervisor::{overview_pointer, unified_pointer, pointer_host, sampler, edge_pan, neighbors, bump,
  free_pointer, overview_cursor, unified_cursor, columns}`. `pointer_host` already implements `LoopHook`:
  `overview_pointer::Pointer::wake_fd` should hand over its `WakeFd`, and its timers should feed `view::deadline`.
- **Renderer ops**: `overview_pointer`, `roll`, `edge_pan`, `restore_band`, `resize_handle`, `move_handle`
- **Python tests**:
  - `test_free_pointer.py` (`test_view_controls_starts_the_sampler_and_cleans_up_before_the_view_binds`,
    `test_the_supervisor_loop_wakes_on_a_sample_and_sends_the_dot`)
  - `test_unified_cursor.py` (`test_the_run_uses_the_unified_cursor`)
  - `test_overview_cursor.py`
  - `test_text_cursor.py` (`test_a_managed_run_reads_through_its_own_broker`)
  - `test_super_drag.py`, `test_resize.py`, `test_input_lock.py`
  - the trace corpora `rust/corpus/pointer/*`, already replayed
- **Parity**:
  - A run case where `reply_overview_pointer` moves `j/cursorpos` over a desktop: both send the same
    `overview_pointer` frames and cursor Lua.
  - The teardown order (sampler stop before `remove_view_binds`) in the trace.
- **Owns**: nothing shared.

### `send`: window sending

- **Python**:
  - `send.py`: `WindowSender` (`__init__`, `request`, `close`, the worker), `notify`
  - `managed.view_controls`: `control.sender`, `control.notify`
- **Ported already**: `crate::send` (the pure plan) and `lua::send`
- **Renderer ops**: none. The Python sends no frame for a move (`test_view_control_routes_with_the_selected_desktop_and_sends_no_frame`).
- **Python tests**: `test_send_window.py`
  - `test_managed_run_closes_the_sender_before_the_binds_go`
  - `test_run_owned_passes_the_fallback_and_recovers_after_the_controls_close`
  - `test_ops_on_the_wire_and_in_the_helper`
- **Parity**:
  - A run case: Ctrl+Alt+G with a focused window in `reply_send`'s clients gives the same move Lua.
  - Ctrl+Alt+B back to the fallback.
  - The sender is closed before the binds go.
- **Owns**: nothing shared.

### `privacy`: privacy mode

- **Python**:
  - `privacy.py`: `PrivacyDisplay` (`request`, `close`, the worker, the watcher)
  - `managed.view_controls`: `control.privacy`, gated on `set_dpms`
- **Ported already**: `supervisor::privacy` (`main_display`, the decisions, `wake`, which recovery already uses) and
  `lua::privacy`
- **Renderer ops**: none
- **Python tests**: `test_privacy.py`
  - `test_view_controls_builds_and_closes_it`
  - `test_the_op_reaches_the_worker_and_not_in_typing_mode`
  - `test_it_works_while_a_video_plays`
- **Parity**:
  - A run case: Ctrl+Alt+D blanks DP-1.
  - `reply_privacy` reports its `dpmsStatus`.
  - Stop wakes it inside `exit` and again in recovery's `wake`.
- **Owns**: nothing shared.

### `immersive`: 3D

- **Python**:
  - `immersive.py`: `find_engine`, `DepthMemory`, `ImmersiveRun` (`switching`, `reassert`, `close`), `ProcessEngine`,
    `make_sbs`
  - `view.py`: `_immersive_wanted`, `_immersive_sync`, `_immersive_tick`, `set_immersive`, `immersive_toggle`,
    `immersive_click`, `immersive_tail_click`, `immersive_pointer`, `_immersive_status`
  - `managed.py`: `depth_fd`, `switching`, `after_reload`'s `reassert`
  - `sbs.py` (`ButtonSbs`)
- **Ported already**: `supervisor::immersive` (the setting, the frame, the memory, the engine, the worker)
- **Renderer ops**: `immersive`, plus `--depth-fd` on every renderer's command line
- **Python tests**:
  - `test_immersive.py` (`test_the_engine_is_gone_and_the_glasses_flat_when_the_run_ends`,
    `test_a_run_as_built_today_falls_back_because_nothing_is_installed`,
    `test_a_saved_setting_comes_up_at_the_next_start`)
  - `test_sbs.py`
  - `test_immersive_strength.py`
- **Parity**:
  - A run case with a fake `xr-depth-engine` beside the fake renderer, written by a child process: the same renderer
    arguments (`--depth-fd`, normalized), `immersive` frames, and the engine's stdin lines.
  - A renderer exit while switching is held as expected.
- **Owns**: `RealHost::keep_renderer`'s expected-exit branch.

### `video`: VR video

- **Python**:
  - `video.py`: `VideoControl` (`on_message`, `close`, `attach_supervisor`, `FdFrame`), `notify_send`
  - `managed.view_controls`: `video_binds`, `control.options_video`
  - `web_video.py`
- **Ported already**: `supervisor::{video, web_video, quality}`
- **Renderer ops**: `video`, with the file's descriptor sent through `Frames::fds` and `LoopHook::take_fds`
- **Python tests**:
  - `test_video.py`
  - `test_web_video.py`
  - `test_options_deferred_video.py`
  - `test_typing_mode.py` (`test_typing_mode_releases_a_playing_videos_keys...`)
  - the traces `rust/corpus/pointer/traces-{video,web_video}.json`
- **Parity**:
  - A run case: `video_open` on a small file in the session gives the same `video` frame.
  - One descriptor arrives at the fake renderer.
  - The same `install_video_binds` Lua.
  - `close` removes them.
- **Owns**: nothing shared.

### `desktops`: the pills (Ultrawide, Add, Remove, Scale, Resolution) and Ctrl+Alt+R

- **Python**:
  - `desktops.py`: `DesktopManager` (`specs`, `guard`, `wide`, `desktops`, `close`, the worker)
  - `managed.py`: `_run_owned`'s construction, `make_guard`, `_window_mover`, and the `view_controls` wiring
  - `view.py`: `desktop_action`, `_start_change`, `_desktops_frame`, `_desktops_changed`, `_desktops_done`, `layout_reset`,
    `_reset_tick`, `_reset_desktops`, `_reset_done`
- **Ported already**: `crate::desktops` (`plan`), `fit_windows`, and the broker's create and remove in `ownership`
- **Renderer ops**: `desktops`, plus `view` and `placement` after a change
- **Python tests**:
  - `test_desktops.py`
  - `test_glass_pills.py`
  - `test_layout_reset.py`
  - `test_scale_dropdown.py`, `test_resolution_dropdown.py`
  - `test_dot_desktop_changes.py`
  - `test_fit_windows.py`
- **Parity**:
  - A run case: `menu_add` adds XR-3 through the broker (the same `j/xrspike` creates, journal entries and `--capture`
    of the next renderer).
  - `layout_reset` pressed twice.
  - A refused create.
- **Owns**: `RealHost::launch`'s guard.

### `window_memory`: putting remembered windows back, and the captures

- **Python**:
  - `window_memory.py`: `tracker_for`, `WindowTracker` (`place`, `finish`, `set_names`, `mark`, `leaving`, `context`,
    `close`), `Store`, `memory_path`
  - `managed.py`: `_run_owned` around adoption, and the `view_controls` wiring
- **Ported already**: `crate::window_memory` (`capture`, `validate`, `Store`, `pair_up`, `plan`), `lua::window_memory`,
  and `Client::place_windows`
- **Renderer ops**: none
- **Python tests**:
  - `test_window_memory_run.py` (`test_the_arrangement_is_saved_then_memory_then_adoption_then_settling_then_the_renderer`,
    `test_view_controls_takes_the_last_capture_after_the_setup_and_wires_the_tracker`,
    `test_nothing_remembered_is_exactly_as_before`)
  - `test_window_memory.py`, `test_window_memory_lua.py`
  - `rust/corpus/pointer/traces-window_memory_run.json`
- **Parity**:
  - A run case with a `window-memory.json` beside the session's `profiles.json`, written before the run: the same
    `place_windows` Lua before adoption.
  - The same file after Stop (the last capture).
- **Owns**: nothing shared.

### `setup`: resetting a saved setup the broker refused

- **Python**:
  - `managed.py`: `_setup_failed`, `UNCREATABLE`
  - `profiles.py`: `discard_unusable`, `load_setup`, `PlacementStore.reset`, `default_setup`, `profiles_path`
- **Ported already**: nothing of the store. `pro2_route.rs` reads `profiles.json` (a reader to share).
- **Renderer ops**: none
- **Python tests**: `test_remember_setup.py` (the `_setup_failed` cases, with lines around 284-323), `test_profiles.py`
- **Parity**:
  - The `create-fail` case with a saved four-desktop setup in the session's `profiles.json`: both leave the same file
    (reset to the default, the log line kept).
  - With the default three desktops, nothing changes.
- **Owns**: `managed::real::run`'s `RunOptions::saved_setup`.
