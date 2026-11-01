# VITURE Workspace Settings Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Omarchy shell panel plus bar widget that starts, stops and recovers XR sessions through a background controller, and sets each XR desktop's resolution and scale before starting.

**Architecture:** Five layers, built bottom-up. (1) Per-desktop geometry through the planner, broker plugin and renderer. (2) A per-glasses settings store and a `P2-panel` consent gate. (3) A pinned install of the binaries, plus a runner that executes one consented run inside its own `systemd-run --user` unit. (4) A `systemd --user` controller service that owns detection, state and the panel socket. (5) A QML plugin `cody.viture-workspace` that is a thin client over that socket. The controller is Python stdlib-only, like `tools/native_spike`.

**Tech Stack:** Python 3 stdlib (`unittest`), C++20 + CMake/CTest, Hyprland plugin API, Quickshell/Qt Quick (QML, `Quickshell.Io` `Socket`), `qmllint`/`qmltestrunner` from `/usr/lib/qt6/bin`, systemd user units.

**Spec:** `docs/superpowers/specs/2026-10-31-settings-panel-design.md` (parent: `docs/superpowers/specs/2026-10-31-viture-workspace-design.md`). Read both.

## Global Constraints

- The editor only shows controls that work; unimplemented ones are greyed out and labelled "coming".
- Nothing touches the glasses or the live desktop without the panel confirmation click. The controller never starts a run on plug-in, login or its own restart.
- No run is a child of the Omarchy shell. Each run is its own `systemd-run --user` transient unit.
- Capture is limited to the run's own XR-n outputs, in memory. Tracking is XRLinuxDriver only, with network blocked. `input`/`sdk` are always `false`.
- Resolution and scale are fixed at start. They are not changed mid-session.
- Resolution presets: 16:9 `1920×1080`, `2560×1440`; 21:9 `2560×1080`, `3440×1440`; 32:9 `3840×1080`, `5120×1440`; plus custom. Scales: `1.0, 1.25, 1.5, 1.75, 2.0`.
- A scale is offered only if `width/scale` and `height/scale` are both whole numbers. The broker's exact readback stays the final check.
- Luma session mode `1920×1200@120` is applied session-only via the existing `hl.monitor` Lua path, never as a saved config edit. The Beast line reports the rate and switches nothing.
- The existing CLI `P2` gate, `load_consent`, and normal (schema 1, 1920×1080) runs behave exactly as before.
- Binaries are loaded only from the pinned install, with their sha256 re-checked immediately before load/launch. Never load from `build/`.
- Only measured figures are shown. Nothing is estimated.
- Parallel sessions edit this worktree: `git add` named files only, and never revert or commit others' changes.
- Claims always say which kind of evidence backs them: offline test, VM trial, or physical run. Physical tasks need Cody's go-ahead for each session.

## Review Focus

1. **Shell restarts or the panel closes mid-run.** The run keeps going, and the panel shows `running` again when reopened. Covered in Task 8 (`test_status_survives_client_disconnect`) and Task 12.
2. **The controller restarts while a run unit is alive.** It rediscovers `running`, not `recovery-needed`, and never starts a second run. Covered in Task 8 (`test_rediscovers_live_run`).
3. **Glasses unplugged while idle or confirming.** The panel goes to `no-glasses`, and a pending confirmation is cancelled, not started. Covered in Task 8 (`test_unplug_cancels_confirmation`).
4. **A saved profile has an invalid desktop**, such as hand-edited 175% at 3840×1080, or an unknown preset. That desktop falls back to 1920×1080 @ 1.0 and the others are kept. Covered in Task 4 (`test_invalid_desktop_falls_back_alone`).
5. **Start pressed twice, or Start and Recover racing.** Exactly one consent file and one unit are created, and the second request gets `busy`. Covered in Task 8 (`test_double_start_is_single_run`).

---

## File Structure

- `tools/native_spike/geometry.py` (new): desktop presets, scale validity, per-desktop validation.
- `tools/native_spike/hyprland.py` (modify): `plan_outputs` places mixed desktops; client gains `plugin_*` and `apply_session_mode`.
- `spike/ownership/registry.cpp`, `spike/ownership/plugin.cpp` (modify): accept and verify per-desktop width/height/scale.
- `spike/src/view.cpp`, `spike/include/xr/view.hpp`, `spike/src/capture.cpp`, `spike/include/xr/capture.hpp`, `spike/src/wayland.cpp` (modify): mixed-size tile layouts.
- `tools/native_spike/profiles.py` (new): per-model profile store `profiles.json`.
- `tools/native_spike/panel_consent.py` (new): `P2-panel` gate.
- `tools/native_spike/managed.py`, `supervisor.py` (modify): accept desktops, settings store, renderer path, status fd.
- `tools/xr_controller/` (new package): `install.py`, `runner.py`, `detect.py`, `state.py`, `server.py`, `__main__.py`.
- `packaging/viture-workspace-controller.service` (new).
- `omarchy-plugin/cody.viture-workspace/` (new): `manifest.json`, `qmldir`, `Model.js`, `ControllerClient.qml`, `BarWidget.qml`, `Panel.qml`, `Launcher.qml`, `DesktopEditor.qml`, `RunningEditor.qml`, `ConfirmDialog.qml`, `tests/tst_model.qml`.
- `spike/src/metrics.cpp`, `spike/src/main.cpp` (modify): live status frames.
- Tests: `tests/native_spike/test_geometry.py`, `test_profiles.py`, `test_panel_consent.py`, `tests/xr_controller/test_*.py`, additions to `spike/tests/{ownership_test,view_test,capture_test,metrics_test}.cpp`.

Run commands (from the worktree root):
- Python: `python -m unittest discover -s tests/native_spike -v`, `python -m unittest discover -s tests/xr_controller -t . -v`
- Native: `cmake --build build/native-spike && ctest --test-dir build/native-spike --output-on-failure`
- QML: `/usr/lib/qt6/bin/qmllint omarchy-plugin/cody.viture-workspace/*.qml` and `/usr/lib/qt6/bin/qmltestrunner -input omarchy-plugin/cody.viture-workspace/tests`

---

## Phase A: per-desktop geometry

### Task 1: Geometry model and mixed planner

**Files:**
- Create: `tools/native_spike/geometry.py`, `tests/native_spike/test_geometry.py`
- Modify: `tools/native_spike/hyprland.py:260-279` (`plan_outputs`)
- Test: `tests/native_spike/test_hyprland.py`

**Interfaces:**
- Produces:
  - `PRESETS: dict[str, tuple[tuple[int,int], ...]]` keyed `'16:9'`, `'21:9'`, `'32:9'`.
  - `SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)`.
  - `CUSTOM_BOUNDS = {'width': (640, 7680), 'height': (480, 4320)}`. Custom values must be even.
  - `DEFAULT_DESKTOP = {'width': 1920, 'height': 1080, 'scale': 1.0}`.
  - `exact_scale(width: int, height: int, scale: float) -> bool`
  - `valid_scales(width, height) -> list[float]`
  - `validate_desktop(d: object) -> dict` raises `ValueError`, returns a normalized `{'width','height','scale'}`.
  - `logical_width(d) -> int`
  - `plan_outputs(existing, workspaces, run_id, desktops: list[dict] | None = None) -> list[dict]`. Same spec keys as today. `desktops=None` means three defaults, byte-identical to the current output.

- [ ] **Step 1: Write failing tests** in `test_geometry.py`:
  - `test_exact_scale_table`: `exact_scale(1920,1080,1.25)` and `exact_scale(3440,1440,1.25)` are True. `exact_scale(3840,1080,1.75)` and `exact_scale(1920,1080,1.75)` are False. `valid_scales(5120,1440) == [1.0,1.25,2.0]`, `valid_scales(3840,1080) == [1.0,1.25,1.5,2.0]`.
  - `test_validate_desktop_rejects`: odd custom width, width 7682, a scale not in `SCALES`, a non-exact scale, `True` as a width, and extra keys all raise `ValueError`.
  - `test_presets_exact`: the `PRESETS` values equal the six resolutions in Global Constraints.
- [ ] **Step 2: Write failing tests** in `test_hyprland.py`:
  - `test_plan_default_unchanged`: `plan_outputs(existing, ws, 'r1')` equals the current fixture output except the uuids.
  - `test_plan_mixed_positions`: desktops `[1920×1080@1, 3840×1080@1, 3440×1440@1.25]` placed right of a 2560-wide monitor give x = `2560, 4480, 8320`, and each spec has its own width/height/scale.
  - `test_plan_rejects_wrong_count`: 2 or 4 desktops raise `ValueError` (three only, this milestone).
- [ ] **Step 3: Run.** `python -m unittest tests.native_spike.test_geometry tests.native_spike.test_hyprland -v`. Expected: FAIL (module missing / new tests fail).
- [ ] **Step 4: Implement** `geometry.py` and extend `plan_outputs`. Each next x is the previous x plus that desktop's `logical_width` (`width // scale`, exact by construction). Keep the overflow check.
- [ ] **Step 5: Run.** The same command, then the full `tests/native_spike` suite. Expected: all PASS.
- [ ] **Step 6: Commit** `tools/native_spike/geometry.py tools/native_spike/hyprland.py tests/native_spike/test_geometry.py tests/native_spike/test_hyprland.py`: `feat: per-desktop geometry model and mixed planner`.

### Task 2: Broker accepts per-desktop geometry

**Files:**
- Modify: `spike/ownership/registry.cpp:46-50`, `spike/ownership/plugin.cpp:185-217` (`configure`, `verified`)
- Test: `spike/tests/ownership_test.cpp`. Also update any literal-1920 expectations in `spike/tests/extract_ownership_methods.py` and `ownership_native_test.cpp` (find them with `grep -n 1920 spike/tests/*`).

**Interfaces:**
- Consumes: the Task 1 spec dicts (`width`, `height`, `scale`, `refresh_millihz=120000`) on the wire.
- Produces: broker `create` accepts any desktop that passes the same rules as `validate_desktop`, mirrored in C++ as `bool validDesktop(int w, int h, double scale)` in `registry.cpp`. `configure` builds the rule from the payload. `verified` compares the payload's pixel size and scale.

- [ ] **Step 1: Write failing tests** in `ownership_test.cpp`:
  - `create accepts 3840x1080 scale 1` and `create accepts 3440x1440 scale 1.25`: these pass registry validation.
  - `create rejects 3840x1080 scale 1.75`, `odd width`, `width 7682`, `scale 1.1`, `refresh 60000`: each throws `"geometry"`.
- [ ] **Step 2: Run.** `cmake --build build/native-spike && ctest --test-dir build/native-spike -R ownership --output-on-failure`. Expected: FAIL.
- [ ] **Step 3: Implement** `validDesktop` and replace the literal check at `registry.cpp:49`. In `plugin.cpp`, `configure` sets `rule.m_resolution={w,h}` and `rule.m_scale=scale` from `p`. `verified` compares `pixelSize`/`m_pixelSize` to `(w,h)` and `m_scale` to `scale`, with an exact float match against the payload double. The reload re-apply path reuses `o->rule`, so it inherits each desktop's own rule. Add an ownership test that a reload restores a 3440×1440@1.25 rule, if the existing reload test harness supports it. Otherwise this is covered in Task 12.
- [ ] **Step 4: Run** the full `ctest`. Expected: all PASS, including the existing 1920×1080 cases.
- [ ] **Step 5: Commit** the changed `spike/ownership/*.cpp` and `spike/tests/*` files: `feat(ownership): per-desktop resolution and scale`.

### Task 3: Renderer lays out mixed-size desktops

**Files:**
- Modify: `spike/include/xr/view.hpp`, `spike/src/view.cpp:30-52` (`overview_layout`, `zoomed_layout`, `blended_layout`), `spike/include/xr/capture.hpp:67`, `spike/src/capture.cpp:32` (`side_by_side`), `spike/src/wayland.cpp:684,728`
- Test: `spike/tests/view_test.cpp`, `spike/tests/capture_test.cpp`

**Interfaces:**
- Produces:
  - `struct Size { float w, h; };`
  - `blended_layout(std::span<const Size> sources, float view_w, float view_h, float spacing, float zoom, float position)`
  - `side_by_side(std::span<const Size> sources, std::uint32_t dst_w, std::uint32_t dst_h)`. Same return types as today.
- Layout rule (my call, recorded in the spec's "left to builder"):
  - All tiles share one height, and each width is that height × its source aspect. Resolution affects sharpness, not apparent size.
  - Overview: the common height is the largest that fits the summed widths plus spacing in `view_w` and is ≤ `view_h`.
  - Zoomed: the selected tile fits the view, aspect preserved. Its neighbours share its height and sit `spacing` apart.
  - A fractional `position` moves the row so that the point `lerp(center[floor p], center[ceil p], frac p)` lands at the view centre.

- [ ] **Step 1: Write failing tests**:
  - `view_test`: `three equal sources match old layout`. The span version with 3×(1920,1080) equals the previous outputs for spacing 40, zoom 0/1, and positions 0/1/1.5 (captured from the current implementation before changing it).
  - `view_test`: `mixed overview shares height`. For sources (1920,1080), (3840,1080), (3440,1440), all heights are equal, widths have ratio 16:9 : 32:9 : 3440:1440 (±0.5 px), and the total ≤ view_w.
  - `view_test`: `zoomed mixed centres selected`. With position 1, tile 1's centre equals view_w/2 (±0.5) and it fits inside the view.
  - `capture_test`: `side_by_side mixed`. For the same three sources in 1920×1200, the rects don't overlap and the aspect error is ≤ 1 px.
- [ ] **Step 2: Run.** `ctest --test-dir build/native-spike -R 'view|capture' --output-on-failure`. Expected: FAIL (signature).
- [ ] **Step 3: Implement** the span overloads. Update `wayland.cpp` to build the sizes from each capture's own `width/height`. If a capture has no frame yet, use its output spec size, never the first capture's size.
- [ ] **Step 4: Run** the full `ctest`. Expected: PASS.
- [ ] **Step 5: Commit** the view/capture/wayland sources and tests: `feat(renderer): mixed-size desktop layout`.

---

## Phase B: settings and consent

### Task 4: Per-glasses profile store

**Files:**
- Create: `tools/native_spike/profiles.py`, `tests/native_spike/test_profiles.py`
- Modify: `tools/native_spike/managed.py:65-111` (`view_controls` takes a store)

**Interfaces:**
- Consumes:
  - `geometry.validate_desktop` and `DEFAULT_DESKTOP` (Task 1).
  - `view.DEFAULT_SETTINGS`, `view.MODES` and `view.MAX_SPACING`, existing.
- Produces:
  - `MODELS = ('beast', 'luma-ultra', 'unknown')`
  - `profiles_path() -> Path`, which is `$XDG_CONFIG_HOME/viture-workspace/profiles.json`.
  - `DEFAULT_PROFILE`: the `view.DEFAULT_SETTINGS` keys, plus `'desktops': [DEFAULT_DESKTOP]*3`, `'tracking': True`, `'mode_switch': True` and `'fallback': None`.
  - `load_profile(path, model) -> dict`
  - `save_profile(path, model, profile) -> None`, an atomic write (temp + fsync + rename, 0600).
  - `class ProfileStore(path, model)` with `.load() -> dict` (view keys only) and `.save(view_settings) -> None`.
  - `class LayoutStore(path)` wraps the existing `view.load_settings` and `view.save_settings` (the CLI default).
  - `view_controls(client, run_id, session, capture, output=None, store=None)`. `None` means `LayoutStore(view.settings_path())`, today's behaviour.
- File shape: `{"schema": 2, "profiles": {model: profile}}`.
  - A missing file, or a missing model, seeds from `layout.json` (v1) when present, otherwise from defaults.
  - Each field is validated on its own, as `load_settings` does.

- [ ] **Step 1: Write failing tests**:
  - `test_defaults_when_missing`
  - `test_seeds_from_layout_v1`: a v1 file with spacing 80 means every model loads spacing 80.
  - `test_invalid_desktop_falls_back_alone`: desktop 1 = 3840×1080@1.75 becomes `DEFAULT_DESKTOP`, and desktops 0 and 2 are kept.
  - `test_models_independent`: saving `beast` leaves `luma-ultra` unchanged.
  - `test_atomic_mode_0600`: the saved file is mode 0600 and no temp file remains.
  - `test_profile_store_roundtrip_view_keys`: `ProfileStore.save` changes only the view keys.
  - `test_view_controls_default_store_unchanged`: the existing `test_managed` cases still pass with `store=None`.
- [ ] **Step 2: Run.** `python -m unittest tests.native_spike.test_profiles -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `profiles.py`. In `view_controls`, replace the direct `view.load_settings` and `view.save_settings` path use with `store.load()` and `store.save()`. Trace `ViewControl`'s `settings` argument and pass the store through.
- [ ] **Step 4: Run** the full `tests/native_spike` suite. Expected: PASS.
- [ ] **Step 5: Commit** `profiles.py`, `managed.py`, `view.py` if touched, and the tests: `feat: per-glasses profile store`.

### Task 5: `P2-panel` consent gate

**Files:**
- Create: `tools/native_spike/panel_consent.py`, `tests/native_spike/test_panel_consent.py`

**Interfaces:**
- Consumes:
  - `supervisor.CAPTURE_SCOPE` and `CAPTURE_ALLOW`.
  - `geometry.validate_desktop`.
  - `ipc.read_private_json`, `ipc.token`.
- Produces:
  - `PANEL_GATE = 'P2-panel'`
  - `PANEL_KEYS`, exactly: `{'schema','gate','run_id','instance','output','recovery_output','model','allow','capture','tracking','mode_switch','plugin','renderer','desktops','input','sdk'}`.
  - `build_consent(scope: dict) -> dict`. `scope` is the controller's confirmed scope.
  - `write_consent(directory_fd: int, consent: dict) -> str`. It writes `<run_id>.json` with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600, and an fsync, then returns the filename.
  - `load_panel_consent(path, instance, output) -> dict`.
- Values:
  - `schema` is int 1.
  - `mode_switch` is `False` or exactly `{'width':1920,'height':1200,'refresh_hz':120}`.
  - `tracking` is `False` or `'xrlinuxdriver'`.
  - `plugin` and `renderer` are each `{'path': str, 'sha256': 64-hex}`.
  - `desktops` is a list of 3 validated desktops.
  - `input` and `sdk` are `False`.

- [ ] **Step 1: Write failing tests**:
  - `test_roundtrip_exact`: `load_panel_consent` of a written `build_consent(scope)` equals it.
  - `test_cli_gate_refuses_panel_record`: `__main__.load_consent` raises on a panel record.
  - `test_panel_gate_refuses_cli_record`: `load_panel_consent` raises on a valid `P2` record.
  - `test_rejects_extra_or_missing_keys`
  - `test_rejects_mode_switch_other_mode`: `{1920,1080,120}` raises.
  - `test_rejects_input_true`
  - `test_rejects_bad_sha`
  - `test_write_refuses_existing_file`
  - `test_write_mode_0600_single_link`
- [ ] **Step 2: Run.** `python -m unittest tests.native_spike.test_panel_consent -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Do not modify `load_consent`.
- [ ] **Step 4: Run** the full `tests/native_spike` suite. Expected: PASS.
- [ ] **Step 5: Commit**: `feat: P2-panel consent gate`.

---

## Phase C: install, runner, controller

### Task 6: Pinned install

**Files:**
- Create: `tools/xr_controller/__init__.py`, `tools/xr_controller/install.py`, `tests/xr_controller/__init__.py`, `tests/xr_controller/test_install.py`
- Modify: `tools/native_spike/supervisor.py:248`. The renderer path becomes a `renderer: Path` parameter of `_launch`, defaulting to today's build path, threaded through `run_managed(..., renderer=None)`.

**Interfaces:**
- Produces:
  - `install(build_dir: Path, data_home: Path) -> dict`. It copies `xr-native-spike` and `libxr-ownership.so` (check the actual CMake output name for target `xr-ownership`) into `data_home/viture-workspace/bin/<first 12 hex of the combined sha>/`, mode 0500, then atomically writes `data_home/viture-workspace/installed.json` = `{'plugin': {'path','sha256'}, 'renderer': {'path','sha256'}}`.
  - `load_installed(data_home) -> dict`
  - `verify_pinned(entry: dict) -> None` raises `ValueError('pinned binary changed')`.
  - CLI: `python -m tools.xr_controller install --build build/native-spike`.

- [ ] **Step 1: Write failing tests** (temp dirs, fake binaries):
  - `test_install_copies_and_hashes`
  - `test_verify_detects_modification`
  - `test_install_never_points_into_build_dir`
  - `test_reinstall_same_bytes_same_dir`
  - `test_launch_uses_given_renderer`: in `tests/native_spike/test_supervisor.py`, a fake `popen` receives the passed renderer path.
- [ ] **Step 2: Run.** `python -m unittest discover -s tests/xr_controller -t . -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both suites. Expected: PASS.
- [ ] **Step 5: Commit**: `feat(controller): pinned binary install`.

### Task 7: Runner, one consented run per unit

**Files:**
- Create: `tools/xr_controller/runner.py`, `tests/xr_controller/test_runner.py`
- Modify: `tools/native_spike/hyprland.py`. The client gains `plugins_loaded() -> list[str]`, `plugin_load(path)`, `plugin_unload(path)` and `apply_session_mode(output) -> dict`.
  - The plugin methods accept only paths under the installed `bin/` dir, matched by regex against `load_installed`. They use Hyprland's `plugin load|unload|list` socket commands. Confirm the exact wire string against `hyprctl` source or the VM before Task 12; the unit tests use a fake transport.
  - `apply_session_mode` reads the output's x/y/scale and evals `restore_mode_lua`.
- Modify: `tools/native_spike/managed.py` `run_managed(client, consent, runtime, *, desktops=None, store=None, renderer=None)`.

**Interfaces:**
- Consumes: Tasks 1, 4, 5 and 6.
- Produces:
  - `main(argv) -> int`, invoked as `python -I -m tools.xr_controller.runner --consent <path> --state-dir <dir>`.
  - Writes `<state-dir>/<run_id>.result.json` = `{'run_id','phase','complete','renderer_returncode','remaining','error'}`.
  - `phase` is one of `'verify','plugin','mode','run','teardown','done'`.
- Sequence:
  1. `load_panel_consent`, then `verify_pinned` for both binaries.
  2. If `plugins_loaded()` is non-empty, block with "ownership plugin already loaded".
  3. `plugin_load`, then confirm by `plugins_loaded()` readback.
  4. If `mode_switch`, `apply_session_mode` and confirm 1920×1200 @ ~120 by readback.
  5. `run_managed` with `desktops`, `ProfileStore(profiles_path(), model)` and the pinned renderer.
  6. Always `plugin_unload` the path it loaded in `finally`, after recovery, and confirm it's gone.
  7. Write the result.
- [ ] **Step 1: Write failing tests** (fake client):
  - `test_order_load_mode_run_unload`: the call order is exactly the sequence above.
  - `test_refuses_when_plugin_already_loaded`: no load, and `error` set.
  - `test_unloads_after_run_failure`
  - `test_does_not_unload_foreign_plugin`
  - `test_pin_mismatch_blocks_before_any_client_call`
  - `test_no_mode_switch_when_false`
  - `test_result_written_on_every_exit`
  - In `test_hyprland.py`: `test_plugin_path_must_be_installed`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both suites. Expected: PASS.
- [ ] **Step 5: Commit**: `feat(controller): consented runner with plugin and mode lifecycle`.

### Task 8: Controller service, detection, state machine, socket

**Files:**
- Create: `tools/xr_controller/detect.py`, `state.py`, `server.py`, `__main__.py`, `packaging/viture-workspace-controller.service`, `tests/xr_controller/test_detect.py`, `test_state.py`, `test_server.py`

**Interfaces:**
- Consumes:
  - `tools.xr_probe.hardware.probe_usb` and `probe_drm` (the latter for EDID model). On master these live in `tools/xr_probe/`; confirm they're importable from this branch.
  - Tasks 4–7.
- Produces:
  - `detect.identify(usb, drm, monitors) -> {'model', 'output' | None, 'reason' | None}`. The device table is exactly the spec's ("Decisions settled" #1): `('VITURE Beast XR Glasses', '1201') → beast`, `('VITURE Luma Ultra XR GLASSES', '1104') → luma-ultra`, other vendor `35ca` → unknown, and `1102` ignored. The output is the single connector whose EDID model is `VITURE Beast`, or `VITURE` for the Luma.
  - `detect.pick_fallback(monitors, output, saved) -> name | None | 'choose'`
    - `'choose'` makes the idle screen (Task 10) show a fallback selector; the choice is saved in the profile's `fallback`.
  - `state.Controller(clock, probe, spawn, units, store, consent_dir_fd, state_dir)`. Methods: `snapshot() -> dict`, `request(msg: dict) -> dict`, `tick()`.
  - States: `no-glasses, idle, confirming, starting, running, stopping, recovery-needed, cli-recovery, failed`.
  - Requests:
    - `status`
    - `get_settings`
    - `set_settings {settings}`: allowed when idle; while running only view keys, forwarded.
    - `begin_start`: returns the exact scope to show.
    - `confirm_start {scope_id, optional:{tracking,mode_switch}}`
    - `cancel`
    - `stop`
    - `begin_recover`
    - `confirm_recover {scope_id}`
    - `view_op {op}`: forwarded to the run's control socket with `xr_control` framing.
  - Replies are `{'ok':bool, 'state':..., 'error'?:str}`.
  - Push: every state change sends `{'event':'state', ...snapshot}` to all connected clients.
  - `server.serve(controller, path)`: Unix socket at `$XDG_RUNTIME_DIR/viture-workspace/controller.sock` (dir 0700, socket 0600). Peer uid is checked with the existing `ipc.check_peer`. Frames are newline JSON with `schema: 1`, max 16 KiB.
  - `units`: an adapter over `systemd-run --user --unit=viture-xr-<run_id> --collect` and `systemctl --user is-active`.
  - `__main__`: `python -m tools.xr_controller serve` and `install`.
  - Unit file: `ExecStart=/usr/bin/python3 -I -m tools.xr_controller serve`, `WorkingDirectory=` the repo, `Restart=on-failure`. `install` also copies the unit to `~/.config/systemd/user/` and runs `daemon-reload`; enabling/starting it is left to Cody.
- [ ] **Step 1: Write failing tests**:
  - `test_detect`: both recorded devices are identified, the microphone is ignored, an unknown pid gives `unknown`, two VITURE connectors give `output None` with a reason, and a product string match with the wrong pid gives `unknown`.
  - `test_fallback`: one other output means it's picked, none means `None`, two means `'choose'` unless a saved one is present.
  - `test_state`:
    - `test_no_start_without_confirm`: `spawn` is never called for `begin_start` alone.
    - `test_confirm_writes_consent_matching_scope`: `load_panel_consent` of the written file equals `build_consent(shown scope ± optional toggles)`.
    - `test_confirm_with_stale_scope_id_refused`
    - `test_unplug_cancels_confirmation`
    - `test_double_start_is_single_run`
    - `test_rediscovers_live_run`: a unit is active and a consent exists, so the state is `running`.
    - `test_journal_without_unit_is_recovery_needed`
    - `test_cli_journal_is_cli_recovery`: a journal whose run_id has no consent in `consent_dir` gives the `cli-recovery` state with the journal path, and `begin_recover` is refused.
    - `test_recover_requires_confirm`
    - `test_resolution_locked_while_running`: `set_settings` with desktops while running gives `error`.
    - `test_never_autostarts_on_plug`: `tick()` with glasses appearing spawns nothing.
  - `test_server`:
    - `test_status_survives_client_disconnect`
    - `test_rejects_other_uid`: mock `check_peer`.
    - `test_rejects_oversize_and_bad_schema`
    - `test_push_on_state_change`
- [ ] **Step 2: Run.** `python -m unittest discover -s tests/xr_controller -t . -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Use a single-threaded `selectors` loop. Probe on a 1 s tick. `scope_id` is a random token issued by `begin_start` and valid only until the next state change.
- [ ] **Step 4: Run** all Python suites. Expected: PASS.
- [ ] **Step 5: Commit**: `feat(controller): service, detection and panel socket`.

---

## Phase D: panel

### Task 9: Panel model and controller client

**Files:**
- Create: `omarchy-plugin/cody.viture-workspace/manifest.json`, `qmldir`, `Model.js`, `ControllerClient.qml`, `tests/tst_model.qml`

**Interfaces:**
- Consumes: the Task 8 protocol.
- Produces:
  - `manifest.json`: `{"schemaVersion":1,"id":"cody.viture-workspace","name":"VITURE Workspace","version":"0.1.0","author":"Cody","license":"MIT","kinds":["panel","bar-widget"],"keepLoaded":true,"entryPoints":{"panel":"Panel.qml","barWidget":"BarWidget.qml"},"barWidget":{"displayName":"XR","category":"Hardware","allowMultiple":false,"defaultSection":"right"}}`
  - `Model.js` pure functions, which mirror Task 1 (and the controller still re-validates):
    - `validScales(w,h)`
    - `presetsFor(shape)`
    - `shapeOf(w,h)`: `'16:9'|'21:9'|'32:9'|'custom'`.
    - `previewRects(desktops, width, height, spacing)`, the same rule as Task 3's overview.
    - `largeLayoutWarning(desktops)`: true when the total pixels exceed 3×1920×1080.
    - `barColorRole(state)`: `'muted'|'accent'|'warning'`.
  - `ControllerClient.qml` (a `Socket` from `Quickshell.Io` on the controller path):
    - properties `connected`, `snapshot`.
    - `send(obj)` returns a request id; `replied(id, reply)` and `stateChanged()` signals.
    - It reconnects every 2 s while disconnected and shows "controller not running" (never starts it).
- [ ] **Step 1: Write failing `tst_model.qml`** (`TestCase`) covering:
  - The `validScales` table from Task 1.
  - `shapeOf(3440,1440) === '21:9'` and `shapeOf(1000,800) === 'custom'`.
  - `previewRects` has equal heights and aspect-proportional widths.
  - `barColorRole('recovery-needed') === 'warning'`.
  - `largeLayoutWarning` is true for `[1920×1080, 5120×1440, 1920×1080]`.
- [ ] **Step 2: Run.** `/usr/lib/qt6/bin/qmltestrunner -input omarchy-plugin/cody.viture-workspace/tests`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `qmltestrunner` and `qmllint` on all `.qml`. Expected: tests PASS, and no lint errors (unresolved `qs.*` imports are allowed only if qmllint can't see the shell's import path; pass `-I /usr/share/omarchy/shell` to resolve them).
- [ ] **Step 5: Commit**: `feat(panel): model and controller client`.

### Task 10: Panel screens and bar widget

**Files:**
- Create: `BarWidget.qml`, `Panel.qml`, `Launcher.qml`, `DesktopEditor.qml`, `RunningEditor.qml`, `ConfirmDialog.qml` in `omarchy-plugin/cody.viture-workspace/`
- Modify: `tools/xr_controller/install.py`. Add `install_plugin(repo, config_home)`, which symlinks `omarchy-plugin/cody.viture-workspace` into `~/.config/omarchy/plugins/` and refuses to replace an existing non-symlink. Test it in `test_install.py::test_install_plugin_symlink_refuses_real_dir`.

**Interfaces:**
- Consumes: `ControllerClient`, `Model.js`, and the shell's `qs.Commons`/`qs.Ui` (`BarWidget`, `WidgetButton`, `Style`, `Commons.Color`), following `~/.config/omarchy/plugins/cody.barracuda-battery/BarWidget.qml` for the bar widget. The panel follows the first-party panel contract (`property var shell`, `property var manifest`, `open(payload)`, `close()`, and dismissal via `shell.hide(id)`) as in `/usr/share/omarchy/shell/plugins/panels/disk-speedtest/Panel.qml`.
- Screens map 1:1 to the approved mockups (`launcher-flow.html`, `launcher-desktops.html`, `running-editor.html` in the spec's mockup dir):
  - `no-glasses`: message only.
  - `idle`: Start, summary, preview, and `DesktopEditor` for the clicked desktop.
  - `confirming`: `ConfirmDialog` shows the scope lines from `begin_start`. The required lines can't be unticked; tracking and 120 Hz can (the Beast's 120 Hz line is a static report).
  - `recovery-needed`: Recover goes through its own confirm.
  - `cli-recovery`: a read-only notice with the journal path.
  - `running`: `RunningEditor` with controls built from `snapshot.capabilities`, read-only desktop geometry, and the stats section showing only keys present in `snapshot.status`.
  - Greyed "coming" rows: per-desktop position/size-in-space/curvature, and add/remove.
- [ ] **Step 1: Write failing `tst_model.qml` additions** for any new pure helpers you extract (e.g. `scopeLines(scope)` with required/optional flags). Keep logic out of the view files.
- [ ] **Step 2: Run** `qmltestrunner`. Expected: FAIL.
- [ ] **Step 3: Implement** the screens. No logic decides safety in QML: every button sends a request and renders the reply/state.
- [ ] **Step 4: Run** `qmltestrunner`, `qmllint -I /usr/share/omarchy/shell`, and the Python suites. Expected: PASS.
- [ ] **Step 5: Offline render check.** Load the panel with a fake controller socket (`tests/xr_controller/fake_controller.py` serving canned snapshots for each state) in a nested `quickshell -p` instance pointed at a scratch config, never the live Omarchy shell. Screenshot each state and compare against the mockups by eye. Record it as "offline render", not as tested behaviour.
- [ ] **Step 6: Commit**: `feat(panel): launcher, editors, confirmation and bar widget`.

---

## Phase E: live status, qualification

### Task 11: Live measured status

**Files:**
- Modify: `spike/src/main.cpp` (optional `--status-fd N`, argc +2 for each existing form), `spike/src/metrics.cpp` + `spike/include/xr/metrics.hpp` (`std::string status_json(double now_ms) const`), `tools/native_spike/supervisor.py` (create a second socketpair, read status lines in the existing selector loop, keep the latest), `tools/native_spike/view.py` (`ViewControl` answers a `status` op on its control socket with `{capabilities, view, status}`), and `tools/xr_controller/state.py` (poll a running run once a second and put the result in the snapshot).
- Test: `spike/tests/metrics_test.cpp`, `tests/native_spike/test_supervisor.py`, `test_view.py`, `tests/xr_controller/test_state.py`

**Interfaces:**
- The heartbeat channel stays `b'H'`-only and unchanged. Status uses the separate fd.
- The renderer sends at most one line per second, non-blocking, and drops it on `EAGAIN`.
- `status_json` keys, emitted only when measured: `presented_hz` (from presentation timestamps over the last second), `missed` (cumulative discarded feedback), `capture_path` (`'dmabuf'|'shm'`), `pose_age_ms`.
- `capabilities` lists the supervisor's `USER_OPS` plus the toggle names, so `prediction` appears automatically.
- [ ] **Step 1: Write failing tests**:
  - `metrics_test`: `status omits unmeasured`. With no presented frames, there's no `presented_hz` key.
  - `metrics_test`: `status rate from timestamps`. 120 synthetic presentations 8.333 ms apart give `presented_hz` ≈ 120 ±0.5.
  - `test_supervisor`: `test_heartbeat_channel_still_H_only` (existing behaviour is untouched) and `test_status_lines_kept_latest`.
  - `test_view`: `test_status_op_reply_shape`.
  - `test_state`: `test_snapshot_includes_only_reported_status`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** all suites (Python, ctest, qml). Expected: PASS.
- [ ] **Step 5: Commit**: `feat: live measured status to the panel`.

### Task 12: VM qualification (isolated VM only)

Use the existing disposable VM envelope and the explicit isolated mode, both already approved. Never use the live desktop. Record evidence in `docs/feasibility/native-spike/isolated-vm-panel-live.json` and a short report.

- [ ] **Step 1:** Mixed geometry: three outputs at `1920×1080@1`, `3840×1080@1` and `3440×1440@1.25`. Each must read back exactly (size, scale, position) at 120000 mHz, and the renderer presents all three.
- [ ] **Step 2:** For every resolution preset × `valid_scales`, check Hyprland keeps the exact scale. Any mismatch means the `exact_scale` rule is wrong: stop and fix Task 1, Task 2 and `Model.js` together.
- [ ] **Step 3:** Config reload mid-run with mixed rules: the outputs keep their own geometry.
- [ ] **Step 4:** Through the controller in the guest: start, stop, renderer crash, then `recovery-needed` → Recover. Then restart the controller mid-run (it rediscovers `running`), and kill the shell or panel mid-run (the run is unaffected).
- [ ] **Step 5:** Plugin load/unload sequencing and readback, using the real wire command.
- [ ] **Step 6:** Commit the evidence files by name: `docs: VM evidence for settings panel`.

### Task 13: Physical acceptance (each session needs Cody's go-ahead)

- [ ] **Step 1:** Ask Cody before each live session. Record the pinned sha256s and confirm they match both before and after the run.
- [ ] **Step 2:** Luma Ultra: panel start (with 120 Hz switch and tracking), live controls, stop. Then a crash and Recover. Cody confirms visually.
- [ ] **Step 3:** Beast: the same, with the 120 Hz line report-only.
- [ ] **Step 4:** One mixed/ultrawide layout: record the measured `presented_hz` and missed frames. Don't claim 120 Hz unless that's what was measured.
- [ ] **Step 5:** Commit the evidence by name: `docs: physical panel acceptance`.
