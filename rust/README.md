# Rust rebuild workspace

The Cargo workspace for the Rust rebuild planned in
[docs/rust-rebuild-plan.md](../docs/rust-rebuild-plan.md). Nothing here ships yet: the live system
still runs the Python and C++ stack, and no installer step builds or installs these crates. The one
exception is opt-in: `xr-workspace install` is a Rust port of the installer, run by hand (see
[Build and install](../docs/technical.md#the-rust-installer-xr-workspace-install)).

| Crate | Kind | Replaces |
| --- | --- | --- |
| `xr-wire` | library | Control and status envelopes, the ownership plugin's `xrspike` requests and replies (`plugin`) and the renderer's status datagrams (`status`), strict JSON (`strict_json`, `decode_control`), the renderer control wire (`control`: the 23 op payloads, the stream and the encoders), and the on-disk formats: journal, consent, progress, result, recovery and install pins, read and written byte-for-byte as the Python writers produce them through private atomic writes (0600, no-follow, fsync and rename) |
| `xr-pose` | library | Pose shared memory: SysV feed, seqlock channels, `PredictionWire` |
| `xr-luma-driver` | binary + library (`rt`) | The C++ Luma Ultra driver |
| `xr-pro2-bridge` | binary | The Pro 2 bridge host (VITURE SDK through FFI) |
| `xr-renderer` | binary + library | The C++ renderer; the library so far holds the Wayland shell, EGL, the video player and the logic: layout checks, view animator, glass, HUD, options and binds panel geometry |
| `xr-workspace` | binaries + library | Controller, supervisor and installer (`xr-workspace`) and the key helper (`xr-control`); the supervisor's Lua generators are in `xr_workspace::lua`, its window placement decisions (`adopt`, `window_memory`, `desktops`, `fit_windows`, `send`, `resize`, `geometry`) beside them |

## Gates

```sh
cd rust
cargo build --locked
cargo test --locked
cargo deny check licenses bans sources
```

`xr-pose`'s `tests/cpp_interop.rs` builds a small C++ helper from the driver's own `rt.cpp` and
`predict.cpp` with `c++` (or `$CXX`), so `cargo test` needs a C++20 compiler. It checks the
`PoseRecord` seqlock object and `PredictionWire` against the C++ layout and code, both ways.
`tests/feed_cpp_interop.rs` builds `spike/tests/pose_test.cpp` with the renderer's `pose.cpp` the
same way and runs it against the Rust SysV feed, both ways, including a lock holder that died.
`xr-wire`'s `tests/plugin_cpp_interop.rs` builds `ownership_test` from `spike/tests/ownership_test.cpp`
and the plugin's `registry.cpp` (C++23, nlohmann_json headers) and feeds it every case of
`spike/tests/ownership_pair_wire_test.py`, encoded by `xr_wire::plugin`, plus geometry and bounds
sweeps: the plugin's `parse_request` and the Rust mirror must give the same verdict on each.

`xr_pose::extrapolate` is `luma::extrapolate` (`spike/driver/src/predict.cpp`): the driver's velocity and
acceleration carried over a horizon, with the stop clamp, the still gate and the `max_deg` limit, in the C++ f32
order with `std::min` and `std::clamp` mirrored. Its `PredictConfig` holds only the fields `extrapolate` reads;
`Predictor`'s filters are not ported. `tests/extrapolate.rs` ports the `luma_predict` ctest's extrapolation checks
on hand-built states, and compares the Rust with `golden/extrapolate.txt` line by line: `golden/extrapolate.cpp`
links the C++ `predict.cpp` unchanged and prints the IEEE bits of every result over a grid of configs, states
(still, slow, fast, braking, turning back, NaN, invalid, uncalibrated, a wrapping clock) and horizons (NaN,
negative, zero, tiny, long). Both match bit for bit. The one gap is `target_ns` for a horizon of 2^64 ns or more,
undefined in the C++ and saturating in Rust.

`Cargo.lock` is committed. `deny.toml` allows permissive third-party licences only (MIT, Apache-2.0,
BSD, ISC, Zlib, Unicode-3.0); adding any other licence is a deliberate edit to that list. Our own
crates are `publish = false` and are skipped by the licence check. `bans` denies wildcard version requirements, but `allow-wildcard-paths = true` lets
through the versionless path dependencies between our own crates, which cargo-deny accepts only because
they are `publish = false`.

`tests/run_all.sh rust` runs these gates from the repository root, and a bare `tests/run_all.sh` runs them together
with ctest, the Python suites and the QML tests.

## Supervisor

`xr-workspace::supervisor` ports the run loop of `tools/native_spike/supervisor.py`:

- the 2 s heartbeat (only `H` bytes count)
- the guard worker in its own process, failing closed 4.25 s after its last `V`
- the 0.5 s grace from SIGTERM to SIGKILL, and recovery only after the renderer is reaped
- the renderer launch, which passes exactly the supervised, Wayland, status and optional depth or
  continuity descriptors and sends the Python's start frame byte for byte

`tests/supervisor_faults.rs` drives each fault (slow, hung, failing and crashing guards, silence,
interrupts, a foreign Wayland peer) against shell stand-ins and checks the timings and that no
worker or renderer is left unreaped. The control socket, Hyprland events and the physical-pair
guard check are later tasks.

The tracking side ports `tools/native_spike/tracking.py`, `survive.py` and `tools/pro2_runtime/scope.py`:

- `supervisor::tracking`: the Luma and XRLinuxDriver lifecycles in their namespaces, the pinned quiet shim for the
  standard driver, and start, reuse, swap and stop on the frozen `tracking.json` (byte-equal to Python's `json.dump`)
- `supervisor::luma_watch`: a crashed Luma driver comes back at most 3 times in 300 s, then the person is told
- `supervisor::survive`: what counts as a USB drop, the hold (120 s limit, 3 s settle, at most 8 holds in 600 s) and
  reviving a dead driver by its record
- `supervisor::pro2`: the supervised Pro 2 scope's health check and renderer arguments

`tests/tracking.rs` runs fake drivers in real namespaces against a temporary tree, and checks that the shipped Python
reads a state file this wrote.

### Switching runs to the Rust supervisor

The Python controller picks the supervisor at every Start (`tools/xr_controller/supervisor_switch.py`), from one
setting, `$XDG_CONFIG_HOME/viture-workspace/supervisor.json` (`{"schema": 1, "supervisor": "rust" | "python"}`). With no
file, runs use the Python runner, as before.

```sh
python3 -m tools.xr_controller supervisor set rust     # the next Start runs `xr-workspace supervisor`
python3 -m tools.xr_controller supervisor set python   # the way back, from the next Start; `clear` does the same
python3 -m tools.xr_controller supervisor status       # the setting, and whether a Rust start would pass its pin check
```

Rust runs the binary `xr-workspace install` pinned: `workspace.pin.json` names the bundle in `workspace/<sha12>/`, and
before every Rust start the controller checks the manifest's hash against the pin and the binary's hash against the
manifest. If the setting says rust and the check fails, the Start fails with a message naming `supervisor set python`.
It never falls back to Python without saying so, because a run that was meant to prove Rust must not run on Python
unnoticed. Some things stay on Python whatever the setting:

- Recover (`recover-run`), since the journal is frozen and the Python recovery cleans up a Rust run.
- A Pro 2 run, since the Rust supervisor does not port that route.

The Python supervisor stays installed for two releases (plan, phase 6). `tests/xr_controller/test_supervisor_switch.py`
covers the setting, the pin checks and the spawn command. With a debug build (`cargo build -p xr-workspace`), it also
starts the real binary on its loopback backend, using the exact command and environment `Host.spawn` passes to
systemd-run.

### Switching runs to the Rust renderer

The renderer a run launches is picked at every Start, too (`tools/xr_controller/renderer_switch.py`), from
`$XDG_CONFIG_HOME/viture-workspace/renderer.json` (`{"schema": 1, "renderer": "rust" | "cpp"}`). With no file, runs
launch the C++ `xr-native-spike`, as before. The Rust renderer has to be pinned first:

```sh
cargo build --locked --release -p xr-renderer
python3 -m tools.xr_controller install --build build/release --rust-renderer rust/target/release/xr-renderer
python3 -m tools.xr_controller renderer set rust     # the next Start launches bin/<hash>/rust/xr-renderer
python3 -m tools.xr_controller renderer set cpp      # the way back, from the next Start; `clear` does the same
python3 -m tools.xr_controller renderer status       # the setting, and whether a Rust start would pass its pin check
```

`xr-workspace install --rust-renderer PATH` pins it the same way, byte for byte. The pin is its own file,
`renderer-rust.pin.json` beside `installed.json`, which keeps the C++ renderer and its old keys, so every reader and an
older controller load it unchanged. The controller puts the Rust pin into the consent, so the confirm box names the
renderer that will launch; the Python runner and the Rust supervisor (`runner::renderer_switch`) read the setting again,
require that same pin and hash the binary just before the launch. If the setting says rust and the Rust renderer is not
installed, its pin is broken or its bytes changed, the Start fails with a message naming `renderer set cpp`. It never
falls back to C++ without saying so. A Pro 2 run and Recover stay on C++ and their own path whatever the setting, and a
Rust Start offers no 3D. The two switches are independent: both runners pick the renderer the same way, though the
Rust supervisor's real backend still refuses the managed run (`MANAGED_NOT_PORTED`), so only its fake one launches.

`tests/xr_controller/test_renderer_switch.py` covers the setting, the pin checks and, once `xr-renderer` is built,
the spawn command carrying its pinned path. `tests/install.rs` checks both installers' bytes and rolling back by
restoring the previous pins, and `tests/renderer_switch.rs` runs the Rust supervisor on its fake backend and compares
its refusals with the Python's. This is only the switch: the Rust renderer still draws the test pattern only (see
[Renderer](#renderer)), so a Rust run cannot show desktops yet.

## Key helper (`xr-control`)

`xr-control` is the Rust port of `tools/native_spike/xr_control.py`, the helper Hyprland runs once per bound key press. It
takes the same command line and sends the same control frames, the same fixed Lua to Hyprland when no run is listening,
and runs zenity, wl-paste and notify-send with the same arguments. Nothing binds it yet: the live binds still run the
Python helper.

`crates/xr-workspace/tests/control_parity.rs` is the parity gate. It runs both helpers on the same fakes for every op
the Python accepts, the bad command lines, the dead-run cleanups, the picker and the clipboard, and compares exit
status, frames byte for byte (request ids blanked after a range check), Lua evals and tool command lines. It needs
`/usr/bin/python3` and never touches the live compositor or a run.

Key press to frame, through the real Python supervisor and a fake renderer:

```sh
cargo build --locked --release -p xr-workspace
TMPDIR=/tmp /usr/bin/python3 -m tools.native_spike.bench_control --only overview,control_op \
  --overview-seconds 125 --press-every 0.4 --helper rust/target/release/xr-control
```

One knowing difference: Python's `isdigit` and `int` also take non-ASCII digits in `binds_key` and `options_scroll`
arguments; the Rust helper takes ASCII digits only and treats anything else as a bad command line. Hyprland's binds only
pass ASCII numbers.

## Renderer

`xr-renderer` so far is the presentation shell of `spike/src/wayland.cpp` and `main.cpp`: the
supervised launch (same arguments, descriptor checks and start frame), an overlay layer surface on
the named output that takes no input, frame-callback pacing, `wp_presentation` feedback and the
`generated_pattern_presentation_timing` report, and the fractional scale through `wp_viewporter`. It
draws the generated test pattern only (SHM, static or `--animate-pattern-ms`). `--capture`,
`--pose-feed`, `--depth-fd` and `--continuity-fd` exit 2 until those parts are ported.

The scene wiring has one module per feature under `crates/xr-renderer/src/shell/` (tracking, scene_layout, roll, handles,
pointer, cards, menus, video_shell, immersive, continuity_shell). Each module holds its state and its `Presentation`
calls, and puts its share of a GPU frame into one `Scene` (`shell/scene.rs`), which the composition hands to
`GlCompositor`. Each module also has a hook for its status and exit-report fields. `main.rs` routes every op and launch
flag to its module, as `spike/src/main.cpp` does. The modules are stubs so far: an op they get is logged once as not
drawn, and `--pose-feed`, `--depth-fd` and `--continuity-fd` are parsed and checked as the C++ does, then refused (exit
2). [`crates/xr-renderer/SCENE_WIRING.md`](crates/xr-renderer/SCENE_WIRING.md) lists, per module, the C++ to port, its
gate tests and the `GlCompositor` inputs it feeds.

Head tracking (`shell/tracking.rs`) is wired: `--pose-feed xrld` is taken, and a GPU frame reads the pose late (the
feed and the motion wire opened and retried once a second), stabilizes, predicts and smooths it, and gives the frame its
camera through `ModeController` (anchor, follow and locked, the focus aim, the landed switch turns and the anchor-mode
view turn). The FOV comes from the driver when it publishes one (46 degrees otherwise). A feed that stops holds the last
pose, raises the "Reconnecting tracking..." pop-up, reports `tracking:false` in the status datagram and keeps
presenting. The exit report carries the `tracking` and `latency` sections (software read to present, fed from the
`wp_presentation` feedback in `shell.rs`). `tests/level_frame_reference.rs` ports the `level_frame` ctest. Gates:
`capture_e2e_rust_tracked`, `_feed_drop`, `_feed_gone_video_loss`, `_bad_control_fatal` and `_control_closed`.

`scene_layout` is wired: `placement` (each desktop's placement, eased toward the latest at the display's rate or glided
along one smootherstep, as `view.rs` `step_placements`), `style` (the GL accent, and the exit report's `theme`),
`edge_pan` (`ViewAnimator::set_pan`, and the hand's dot drawn as the free pointer while it pans, `pan_dot`) and
`desktops` (captures stop, start and restart; placements follow their desktops; a Ctrl+Alt+R change eases the tiles,
`TileEase`). The exit report's `view` section lists the placements. Captures key their Wayland proxies by a stable id,
since `desktops` moves their indices. `pan_dot` is checked frame by frame against `spike/tests/edge_pan_trace.cpp`
(`tests/fixtures/edge_pan_dot.txt`); `capture_e2e_rust_edge_pan_gl-dmabuf` is its end-to-end gate.

`shell/roll.rs` is wired: the `roll` op sets each desktop's levelling roll (`scene.own[i].roll`) and the level bars,
hover, segment and active of the glass frame every GPU frame, and `take_roll_geometry` hands the supervisor its
`roll_geometry` line (rate-limited to `ROLL_GEOMETRY_MS` once measured, never the same line twice, resent after a
failed send). The line is byte-equal to the C++ for the same inputs: `golden/roll_geometry.cpp` prints the C++ lines
for fixed references, anchors, layouts and placements (`roll_geometry_lines_match_the_cpp` in `shell/roll.rs`).
Until tracking is wired every tilt is `null`, as the C++ sends untracked; the measured branch takes its reference
from the tracking module. `tests/roll_level.rs` ports every check of the `roll_level` ctest; `level_frame`'s view half
(the tilts reported for a fixed reference) is in `shell/roll.rs`, its pose half is tracking's.

`handles` is wired: `resize_handle`, `move_handle` and `restore_band` light the corner caps, ease the glass pills' and
gears' hover levels and fade the Step-Back Band's Restore pill on the maximized desktop, and the exit report carries
`restore_band`, `resize_handle` and `move_handle` as the C++ does. Its twins are `tests/resize_handle.rs` and
`tests/restore_band.rs`; its gate is `capture_e2e_rust_restore_band`.

`shell/pointer.rs` draws the overview free pointer: the `overview_pointer` op's dot (or, with a direction, the dot turned
there), its look's marks or the Serif Beam, and a window drag's ghost with its drop target. It is drawn in the overview
and through the overview pan's landing, and hidden while the view switches. The exit report's `overview_pointer` counts
how many times it was shown. The edge pan's hand dot, which takes the pointer's place during an edge pan, comes with
`scene_layout`'s `pan_dot`.

`shell/cards.rs` is wired: the `help` and `binds` ops and the mode pop-ups of `view` (viewing mode, cursor follow,
prediction, typing; a maximize or un-maximize changes the mode without a pop-up) are drawn head-locked through
`GlCompositor::set_popup` / `set_help` / `set_binds`. Each card is rasterised by the ported `hud.rs` /
`binds_panel.rs` code the first time a frame needs its generation, from the overlay state the C++ draws it from, so its
pixels are the C++ ones. The fonts load when the GPU composition comes up (one instance per card, as the C++ does) and
the exit report's `popup_font` names the family or the pixel-font fallback; `help` and `binds` report their counters,
page and scroll window. The `help_geometry` line goes to the supervisor with the C++ rules: at once on a show or hide,
then at most every `HELP_GEOMETRY_MS` (40 ms) while the card is up on the overview, null while the FOV split has not
been worked out since a configure. `tests/native_help.rs` is the `native_help` twin.

`menus` is wired: `options_menu` and `placement_menu` open the options panel and the placement panel from the overview's
glass (`set_options_menu`, `set_placement_menu`, the render path's glass block, `options_overlay.hpp` and
`options_evidence.hpp`), and the exit report carries `placement_menu` and `options_menu`. Its gates are
`capture_e2e_rust_options_roundtrip`, `capture_e2e_rust_open_panel` and the `options_evidence` twin (a unit test in
`shell/menus.rs`).

Its IPC (`xr_renderer::ipc`) is the C++ `ControlStream` and `main.cpp` socket handling on
`xr_wire::control`: every one of the 23 ops is decoded and receipted (`control_applied` or
`control_refused` on the status socket) exactly as the C++ does, `--control-errors fatal` stops
on any refusal except a malformed display-only payload, refused frames are logged redacted to
their protocol words, and the `SCM_RIGHTS` descriptors a `video` open brings are kept (at most two,
close-on-exec) and handed to its open. `stop`, `view` (overview and maximize, through
`ViewAnimator`; `ModeController` is not ported yet, so focus aim, prediction and stabilization are
ignored) and `environment` (the grid and smoke backgrounds, faded as in C++) act; the other ops are
logged once and a video file is closed unread until the player is wired in. While a desktop is
maximized the status socket carries the `maximize_geometry` line. `control_wire_check` is the Rust twin
of `spike/tests/control_wire_check.cpp`; the control suites run against it with

    cargo build -p xr-renderer --bins
    CONTROL_WIRE_CHECK=$PWD/rust/target/debug/control_wire_check PYTHONPATH=.:tests/native_spike \
      python3 -m unittest test_control_wire test_control_panel_fuzz test_control_corpus

(`test_strict_json_corpus`'s regeneration check records the C++ tool's verdicts, so it needs the
C++ build; the Rust tool gives the corpus's `rust` column.)

`golden/metrics.cpp` and `golden/pattern.cpp` print the C++ timing reports and pattern frame hashes
the unit tests compare against byte for byte.
`binds_panel::render_bind_panel` draws the key bind panel's card byte-equal to the C++ in the built-in
5x7 font and the installed font: `golden/binds_panel.cpp` prints the C++ cards' FNV-1a hashes
(`tests/binds_panel.rs`; the installed-font lines come from this machine's font file).
`glass_raster` draws the placement panel (`render_glass_panel`), the options menu (`render_options_panel`), the handle
atlas (`render_handle_atlas`) and CPU lit glass (`glass_draw`) byte-equal to `spike/src/glass.cpp` in both fonts:
`golden/glass_cards.cpp` prints the C++ images' FNV-1a hashes for 43 cases per font (`tests/card_raster.rs`; the
glass ctest's raster checks are in `tests/glass.rs`). `tests/glass_render.rs` ports the `glass_render` ctest: the
glass shader against `glass_shade` on surfaceless GLES, and the open panels it renders with `render_glass_panel`
composited over the glass under the 191/255 cap, in the default and seven theme colours. The loopback compositor drives the renderer end to end:

```sh
capture_e2e_bench --check --mode pattern --seconds 5 --warmup 1 \
  --renderer rust/target/release/xr-renderer [--fractional-scale 150]
```

Its output carries the renderer's `timing_report`, so the C++ and Rust reports can be compared
field by field.

### Benches against the Rust renderer

The C++ loopback compositor and benches stay the harness (plan decision: an independent harness
while the renderer is replaced). Configure the spike with the Rust binary and the `capture_e2e_rust_*` ctests run it:

```sh
cargo build --release -p xr-renderer
cmake -S spike -B build/release -DCMAKE_BUILD_TYPE=Release -DXR_RUST_RENDERER=$PWD/rust/target/release/xr-renderer
ninja -C build/release capture_e2e_bench xr_malloc_count
ctest --test-dir build/release -R '^capture_e2e_rust_'
```

- `capture_e2e_rust_pattern` and `capture_e2e_rust_scaled_pattern` (`--fractional-scale 150`):
  the `pattern` mode.
- `capture_e2e_rust_{cpu,gl-shm,gl-dmabuf}`, their `capture_e2e_rust_scaled_*` twins and
  `capture_e2e_rust_churn_{bounce,resize}_*`: the capture modes. `cpu` composes on the CPU into
  SHM (`cpu-blit`); `gl-shm` uploads SHM captures (`gles2-shm-upload`, fallback `session offers no
  dmabuf`); `gl-dmabuf` imports dmabuf captures (`gles2-dmabuf-import`). Both GL modes present
  through a three-buffer GBM swapchain wrapped as zwp_linux_dmabuf_v1 buffers (no wayland-egl).
  Each output's `fallback` in the exit `managed_output_capture` line is the C++ reason string.
- `capture_e2e_rust_maximize_{1,2,3,4}_gl-dmabuf`, `capture_e2e_rust_maximize_4_gl-shm` and
  `capture_e2e_rust_environment[_smoke]_{gl-shm,gl-dmabuf}`: the maximize and background modes,
  with the C++ tests' arguments. `tests/environment_render.rs` is the Rust twin of
  `environment_render_test.cpp` (all but the VR video sphere, which needs the scene path). Its
  level-bar and 3D button part always runs: the test renders the handle atlas with
  `glass_raster::render_handle_atlas` (HudFont when it loads) and passes it by `set_handle_atlas`.
  Without that override `GlCompositor` renders its own atlas on a theme change, as the C++ does
  (`tests/handle_atlas.rs`).
- `capture_e2e_rust_malloc_{pattern,cpu,gl-shm,gl-dmabuf}`: the `malloc_count.c` shim (`--malloc-shim`) in the Rust
  process. Rust uses glibc malloc, so the `LD_PRELOAD` counter sees every allocation, and
  `--check` now fails when a requested shim did not load. `--malloc-trace FILE` plus
  `spike/bench/malloc_report.py` names the call sites, demangled. On 2026-11-10 the pattern
  path made 987 allocations/s (8 per frame at 120 Hz, all in `wayland-client` request and
  event handling, `Presentation::pump`'s poll list included) against 33,849/s for the C++ renderer
  in the same mode. The capture modes: `cpu` 5,064/s (C++ 96,111/s), `gl-shm` 20,856/s (C++
  248,699/s), `gl-dmabuf` 21,715/s (C++ 256,308/s).

`gl_bench`'s bench mode is ported as a cargo bench over the same scenarios (the 21 drawn without a
background or video) with the same table, column for column: CPU, GL-minus-flush, thread CPU,
flush, GPU time (`GL_EXT_disjoint_timer_query`), frame time, frames over 8.3 ms and the GL call
counts.

```sh
cargo bench -p xr-renderer --bench gl_bench -- [--frames N] [--size WxH]
build/release/gl_bench --bench [--frames N] [--size WxH]   # the C++ one, to compare
```

Not ported from the C++ bench: its `report:` line and the card and font CPU timings (no font in
Rust yet). `tests/gl_parity.rs` still checks the call counts against the C++ ones
(`XR_GL_CPP_BENCH`).

The scenarios (`tests/support/gl_scenarios.rs`, shared by `gl_frame`, `gl_parity`, the bench and
`environment_render`) draw the real cards: the pop-up, help card, placement panel, its 81
slider-drag frames and the handle atlas that `gl_bench --dump` writes, rendered by the Rust rasters
in the installed HUD font (else the pixel font, as the C++ then does). With `XR_GL_CARDS` set to a
`gl_bench --dump DIR` directory every one of them, and the video card, must also equal the dumped
C++ image byte for byte; `gl_parity` always checks against its fresh dump. `tests/overlay_residue.rs`
draws the help card the C++ `HelpOverlay` draws for its rows (`render_help_card`, unbounded, page 1).

`tests/gl_frame.rs` ports `gl_bench`'s check mode, including the Serif Beam against its CPU mirror:
`tests/support/pointer_raster.rs` is `spike/tests/pointer_raster.hpp` (the flat fill rule, the same
f64 maths in the same order), and the GL beam must match it within 1 per channel on the shape and its
ring and exactly off it, at both grey levels. `tests/pointer_beam.rs` ports `free_pointer_test`'s
checks on that mirror (the beam's 14 x 28 px extent, its 2 px stem, its symmetry).

## Driver real-time layer

`xr-luma-driver`'s `rt` module ports the C++ `luma::rt` pipeline (`spike/driver/include/luma/rt.hpp`):
a reader thread hands each sample through a lock-free SPSC ring to a fusion thread (ring mode), or
runs the sink itself (inline mode). Thread policy (SCHED_FIFO with the RLIMIT_RTTIME guard, else
nice, else a short EEVDF slice), memory locking and the latency histogram come across unchanged.

`luma_rt_bench` is the port of the C++ `luma_rt_bench`, with the same options, report and `json`
line, so the two can be run side by side. `tests/rt_alloc.rs` and the bench count every `malloc` in
the process, so "0 allocations" covers libc as well as Rust.

```sh
cargo build --release -p xr-luma-driver
target/release/luma_rt_bench --mode ring --seconds 10      # or --mode inline, --check, --quick
```

Like the C++ bench's default build, it uses the stand-in Mahony filter rather than `fusion::Fusion`, and
publishes through `xr_pose::PoseWriter` to its own `/luma-rt-bench-<pid>` object. `--publish mutex`
and `--spin-reader` are not ported.

Gate result (2026-11-10, shared 16-CPU machine at load 14–48, no real-time limits so both run
SCHED_OTHER with a short slice): two sets of 20 runs of 10 s each, Rust and C++ started together so
both see the same load. Medians:

| Mode | Rust p50 / p99 (us) | C++ p50 / p99 (us) | Lost (Rust / C++) | Allocations |
| --- | --- | --- | --- | --- |
| ring, set 1 | 8.7 / 1171 | 9.0 / 1761 | 0 / 14 | 0 / 0 |
| ring, set 2 | 8.55 / 844 | 8.4 / 844 | 0 / 0 | 0 / 0 |
| inline, set 1 | 5.1 / 232 | 5.05 / 329 | 0 / 45 | 0 / 0 |
| inline, set 2 | 4.95 / 262 | 4.6 / 311 | 0 / 0 | 0 / 0 |

Every loss on either side was a kernel queue overflow, where the reader was descheduled for more
than 64 ms. There were no ring overruns. On a loaded machine, judge loss with both benches running
at the same time.

The p50 values are bucket tops, and at 5 us a bucket is about 0.15 us wide, so a 0.05–0.35 us
difference is within one or two buckets. Part of the inline gap is the fake sensor, not the
pipeline. LLVM compiles the rig's table CRC about 17 ns slower than GCC, and it runs twice per
sample: once in the sender, once in the parse.

## Pro 2 bridge

`xr-pro2-bridge` is a port of `tools/pro2_runtime/bridge.cpp`: the same arguments, checks, SDK
call order, status lines and feed. It opens `/proc/self/fd/<sdk-fd>` with `libloading` only after
the `sha2` hash of that fd matches, and never links the SDK at build time. The `synthetic` feature
adds `--fixture-root` and `--fixture-hash`, as `-DPRO2_SYNTHETIC` does for the C++ one; a production
build refuses them.

Three opt-in flags do the duties `tools/pro2_runtime/launch.py` otherwise does around the bridge,
so a parent other than the Python launcher can run it. The launcher passes none of them and still
does all three itself.

- `--unshare` gives the host its own user and network namespace, as `unshare -Urn` does: this user
  maps to root, and the network has only `lo`.
- `--scan-competitors` refuses to start, with `competing-provider`, while another process of this
  user is a known provider by name (`xrDriver`, `xr-luma-driver`, `pro2-sdk-bridge`,
  `xr-pro2-bridge`) or has `libglasses.so` mapped. It then looks again once a second and ends the
  run if one appears. The rescan runs in a child process forked before `--unshare`, because from
  inside a new user namespace the kernel refuses to show other processes' `maps`. If the child dies,
  the run ends with `provider-scan`.
- `--health <path> --nonce <n>` writes the heartbeat `scope.TestScope.healthy` reads:
  `{"nonce", "ready", "monotonic"}`, mode 0600, through `health.tmp` and a rename, every 20 ms once
  the run is ready. Its last write is `ready: false`, with the run's error as `reason`. The path
  must be absolute, and the nonce is 1 to 64 letters or digits.

The Pro 2 suites run in private namespaces where cargo cannot work, so build the bridge first and
point them at it:

```sh
/usr/bin/python3 tests/pro2_bridge.py <dir>
PRO2_BRIDGE_RUST_DIR=<dir> python3 tests/pro2_runtime_qa/run.py
PRO2_BRIDGE_RUST_DIR=<dir> python3 tests/pro2_runtime/run.py
PRO2_BRIDGE_RUST_DIR=<dir> /usr/bin/python3 tests/pro2_smooth_qa/run.py
```

With `PRO2_BRIDGE_RUST_DIR` set, `tests/pro2_runtime_qa/test_rust_bridge.py` also runs both bridges
side by side and compares them; without it the suites use the C++ bridge as before.
`tests/pro2_runtime_qa/test_rust_host_duties.py` covers the three flags. Each host runs as pid 1 of
its own PID namespace, so the scan sees only the test's stand-in competitors.

## Corpus

`corpus/` holds golden files taken from the shipped Python and C++ code. The phase 6 parity gate
holds the Rust ports to them byte for byte.

- `corpus/hyprland/lua.json` lists every generated-Lua string the supervisor and the key helper send
  to Hyprland's `/eval`, each with its inputs. It has at least one case per branch of each
  generator. Inputs the Python refuses are recorded too, as `refused` with the error text.
- `corpus/hyprland/transcripts.json` records the request bytes `HyprlandClient` sends and the replies
  it gets, for each public call that talks to the compositor. A scripted stand-in compositor on a
  temporary socket gives the replies; the real Hyprland is never used.
- `corpus/luma/transport_trace.txt` lists every usbdevfs request with the allowlist's verdict, then
  each open, close and ioctl of one scripted session on a fake kernel. The C++ transport test writes
  it (`LUMA_TRACE_WRITE=1`) and checks it; `crates/xr-luma-driver/tests/transport.rs` holds the Rust
  transport to the same calls in the same order.
- `corpus/journal/cases.json` holds the run journal (`tools/native_spike/journal.py`): the journal file
  each scripted run leaves, then the Python recovery of it against a scripted compositor, clean and
  with one fault (an error, a disconnect, a session change, a refused or an ignored call) injected
  at every call it makes, then a clean retry. Journals the Python refuses are listed with the error
  text. `tests/native_spike/journal_corpus.py` makes it and is also the live Python oracle for
  `crates/xr-workspace/tests/cross_recovery.rs`, which holds the Rust journal to both directions:
  Rust recovers the Python runs, and the Python recovers runs the Rust wrote.

- `corpus/luma-fusion/` holds the C++ `luma::Fusion`'s output for every sample of the two replay
  fixtures (`real.bin`, `upside_down.bin`, from `spike/driver/tests/fixtures/`) and for a faults run
  of the real one (`real_faults.bin`: warm-start bias, 10 lost samples, a 400 ms gap, a clock jump of
  1 s, a duplicate). `xr-luma-driver`'s `tests/fusion_parity.rs` replays them and requires every bit
  to match. `tests/protocol.rs` holds the Rust packet parser and sequence tracker to the same files:
  parsing either fixture must give exactly their input samples, bit for bit.

  Format, little endian: `LUMAFUS1`, `u32` sample count, `u32` status interval (100). Then per
  sample the input (`u64 t_ns`, `u32 seq`, `f32` gyro x3, `f32` accel x3) and the `PoseOut`
  (`f32` q w,x,y,z, `u64 t_ns`, `u32 seq`, `u8 calibrated`). After every 100th sample and the last
  one, the `FusionStatus` (`u8` quality, `u8` still, `f32` x3 each for bias, omega body, omega
  world and up, `f32 calibrated_after_s`, `u64` samples, rejected, gaps, time jumps) and
  `predict(0.017)` (`f32` q x4).

  `spike/driver/tools/fusion_golden.cpp` writes them from the samples the daemon would feed Fusion
  (parse, sequence tracker, accepted only). The ctests `luma_fusion_golden_*` fail when fusion.cpp's
  output moves. After a deliberate change to fusion.cpp, rewrite them and port the change:

  ```sh
  G=build/release/driver/luma_fusion_golden F=spike/driver/tests/fixtures C=rust/corpus/luma-fusion
  $G write plain $F/luma_imu_real.bin $C/real.bin
  $G write plain $F/luma_imu_upside_down.bin $C/upside_down.bin
  $G write faults $F/luma_imu_real.bin $C/real_faults.bin
  ```

  The Rust fusion uses plain `f64` arrays, not `glam`: bit parity needs the C++ operation order
  exactly, and `glam`'s quaternion maths does not promise it.

  Calibration lives in `fusion::Fusion`; `xr-luma-driver`'s `bias` module ports the C++
  `wiring::BiasStore` and the bias half of `FusionEstimator`: `luma-gyro-bias` in the state
  directory warm-starts Fusion, the estimator notes a `Good` bias once a minute and when it goes,
  and `BiasStore::tick` rewrites the file every 5 minutes. The file is the C++ one byte for byte
  (three `%.9g` floats, spaces, `\n`). `tests/calibration.rs` replays both fixtures the way
  `luma_replay_report` does and requires its calibration times: 2.60 s (real, moving) and 1.50 s
  (upside down, still).

- `corpus/json/strict_json.json` holds 3442 JSON texts (hand-picked edges and a seeded fuzz), each
  with the verdict of Python's `strict_json` and of C++ `decode_control`, and the verdict
  `xr_wire::json::parse` must give. Where Python and C++ differ (nesting past 32, a lone surrogate
  escape, a number past the `f64` range, a byte order mark, a raw NUL byte), the case names the rule followed; the
  Rust layer always takes the stricter verdict. `tests/native_spike/strict_json_corpus.py` makes it
  (with `CONTROL_WIRE_CHECK` set to the built `control_wire_check`); `test_strict_json_corpus.py`
  and `cargo test -p xr-wire` check it.

- `tests/native_spike/wire_fuzz.py` is the cross-language fuzz: generated frames through Python, C++
  and Rust, every verdict compared on the JSON layer, the renderer stream and the run socket. Build
  `examples/wire_fuzz_verdicts` (`cargo build --release -p xr-wire --example wire_fuzz_verdicts`)
  and `control_wire_check`, then run
  `CONTROL_WIRE_CHECK=../build/release/control_wire_check python3 ../tests/native_spike/wire_fuzz.py --frames 1000000`
  from `rust/`. It exits 1 on any difference.

`tests/native_spike/lua_corpus.py` makes the two Hyprland files. `test_lua_corpus.py` fails when they no
longer match the Python, or when a generator branch has no case. After a deliberate change to a
generator, regenerate them:

```sh
TMPDIR=/tmp /usr/bin/python3 tests/native_spike/lua_corpus.py --write
```

`crates/xr-workspace/tests/lua_corpus.rs` holds `xr_workspace::lua` to `lua.json`: every case gives the
same bytes, or the same refusal message. Python also refuses some values for their JSON type (a float
for an int, a number for a path, a missing key). The typed Rust API cannot be given those, so the
test fails them while decoding the arguments. It lists each such case by name in `TYPE_REFUSALS`, and
fails on any case it does not list. The Python helper keeps copies of a few strings
(`xr_control.REMOVE_MENU_BINDS` and others), because it runs with `-I` and imports nothing. Rust has
one source for each, and the test checks those cases against it.

- `corpus/placement/plans.json` lists what the supervisor's window placement code decides: adoption,
  window memory (capture, validate, match, plan), desktop actions, fitting windows into a smaller
  desktop, the send keys, resize outputs and desktop geometry. Each case holds a function's arguments
  (Hyprland replies taken from fixtures) and its result, or its refusal message. The fixtures hold
  every kind of window that never leaves the physical monitor: pinned windows, special workspaces and
  the XR Workspace panel under both of its titles.

`tests/native_spike/placement_corpus.py` makes it, and `test_placement_corpus.py` fails when it no
longer matches the Python or a function that can refuse has no refusal case.
`crates/xr-workspace/tests/placement_corpus.rs` replays every case on the Rust port and compares the
results as JSON. It also checks the gate directly: no window that stays is ever adopted, remembered
or moved back, and none of them appears in the evals `xr_workspace::lua` makes from those plans.
After a deliberate change to the Python, regenerate it:

```sh
TMPDIR=/tmp /usr/bin/python3 tests/native_spike/placement_corpus.py --write
```

- `corpus/supervisor/run_order.json` lists the ordered steps `managed.py` takes on a start, a stop, a
  recovery and a recovery after the compositor exited, with the result. Each path has one case per
  failure point: every step that can fail is made to fail once at each of its calls. The steps are
  recorded through fakes, so nothing touches a compositor or the disk.

`tests/native_spike/run_order_corpus.py` makes it, and `test_run_order_corpus.py` fails when it no
longer matches `managed.py` or a step that can fail has no case. The test in
`crates/xr-workspace/src/managed/tests.rs` replays every case through the Rust order. After a
deliberate change to the order, regenerate it:

```sh
TMPDIR=/tmp /usr/bin/python3 tests/native_spike/run_order_corpus.py --write
```

`corpus/drift/` holds the two real-glasses recordings that heading-drift work (plan D6) replays:
`desk-still.lcap.xz` and `warm-up.lcap.xz`, each with a `.json` summary. See
[corpus/drift/README.md](corpus/drift/README.md).

`corpus/supervisor/` holds the supervisor modules ported in `crates/xr-workspace/src/supervisor/`:
`keybinds.json` (the key bind panel, including scripted panel sessions), `bind_check.json`,
`options_text.json` (key presses through the system libxkbcommon, so the Rust port uses the same
library through the `xkbcommon` crate) and `environment.json`. `tests/native_spike/keybinds_corpus.py`
makes them and `test_keybinds_corpus.py` fails when they go stale. Regenerate with:

```sh
TMPDIR=/tmp /usr/bin/python3 tests/native_spike/keybinds_corpus.py --write
```

- `corpus/view/view.json` holds the supervisor's view frames (`view.py`): each encoder's exact
  line for its inputs, scripted `ViewState` key sequences with the view, panel, settings and
  placements after every step, and the `layout.json` that `load_settings` reads and `save_settings`
  writes. Each refusal says whether the Rust types already rule the input out (`"by": "type"`) or
  the port must refuse it with the same message (`"by": "value"`). `src/supervisor/corpus_tests.rs`
  replays it, along with the frames in `tests/native_spike/fixtures/control_frames.json`.

`tests/native_spike/view_corpus.py --write` makes the view file, and `test_view_corpus.py` fails
when it is stale.

`corpus/incidents/scenarios.json` replays whole sessions through the incident monitor: the
controller's observations, the passes of the monitor and the changes to a fake host (logs, DRM, USB,
core dumps, results). For each scenario it records what the Python `IncidentMonitor` left: the report
`.json` and `.md` files and `.monitor-state` byte for byte, the toasts, the reports handed to the
on-incident hook and the `incident:` and `mode-switch:` journal lines. The first scenario is the RR-55
gate, an injected renderer SIGSEGV with its core dump and exit report.
`tests/xr_controller/incident_corpus.py` makes it and `test_incident_corpus.py` fails when it goes
stale. Regenerate with:

```sh
TMPDIR=/tmp /usr/bin/python3 -m tests.xr_controller.incident_corpus --write
```

## Controller runs

`xr-workspace` holds the controller's run half of the host probe (`src/host.rs`) and its run units
(`src/units.rs`): the same `viture-xr-<run_id>` transient units, `systemctl` and `systemd-run` arguments
and folders as `tools/xr_controller`, so a Rust controller adopts runs the Python one started.
`xr-workspace runs` prints what a controller starting now would adopt; it only reads.

The adoption gate runs against the real user systemd, so it is ignored by default:

```sh
cargo test --locked -p xr-workspace -- --ignored adoption_after_restart --nocapture
```

It starts and removes one short-lived `viture-xr-rstest…` unit and touches no XR session.

### Panel socket

`src/controller_server.rs` ports `tools/xr_controller/server.py`: one `poll(2)` loop with at most
32 clients, each a same-UID peer (`SO_PEERCRED`). A frame is at most 16 KiB, and each client may
hold at most 64 KiB of unsent output. A bad or oversize line ends that connection. The controller
ticks at 1 Hz, and every changed snapshot is pushed as `event: "state"`. The socket is bound
through the private directory's fd, made 0600, and unlinked on close only while it is still the
bound inode. Replies go out on the next `POLLOUT`, as in Python. Nothing runs it yet: the service
binary that wires it to the real host is a later task. `tests/controller_server.rs` holds the cases
of `test_server.py`, plus the caps. `tests/xr_controller/test_rust_panel_client.py` runs the
panel's unchanged QML client test against `examples/controller_socket.rs`, which is the Rust
state machine behind this server with the original test's offline host:

```sh
TMPDIR=/tmp /usr/bin/python3 -m unittest tests.xr_controller.test_rust_panel_client -v
```

### D12: eventfd wake

The C++ bridge's owner thread sleeps 2 ms whenever the ring is empty. The Rust one does not: the
SDK callback pushes into the ring and bumps an eventfd, and the owner `poll`s that eventfd and the
display pipe. It still wakes every 20 ms with nothing to do, so the identity and stop checks run
while no pose comes. The signal handlers bump the same eventfd, so a stop does not wait out a poll.
The synthetic build prints `wake_us n=.. p50=.. p99=.. max=..` to stderr at the end of a run:
callback to publish, per sample. Gate: `test_callback_to_publish_p99_under_200_us` runs 240 Hz for
5 s and needs p99 < 200 us. On a 16-core desk machine at load average 15, three runs gave p99 63,
74 and 82 us (p50 about 30 us). No real-time policy is applied. At load average 19 one run had a
3.7 ms p99, which is the scheduler; the short EEVDF slice the Luma driver uses is the fix if that
matters live.

### D12: 240 Hz and the motion wire

`--pose-rate 240` (SDK code 3) is the request. The launcher passes it from the consent's `pose_rate_hz`, so the default stays 60.
On every published sample the bridge also writes `<prefix>pose_prediction` (`Publisher::publish_motion`): the pose, its arrival
time, and a velocity from `src/motion.rs` (see docs/technical.md, "Motion beside the pose"). The renderer's Pro 2 smoother uses
it while it is live.

- `cargo test -p xr-pro2-bridge` covers the estimator: rate and cadence, the 60 Hz equivalence, brake, still gate and resets.
- `examples/motion_replay.rs` reads `host_ms device_ms x y z w` rows and adds `wx wy wz` (rad/s). It is what
  `smooth_acceptance.py --bridge-motion` and `tests/pro2_runtime/test_smooth_acceptance.py` (`PRO2_BRIDGE_MOTION`) replay.
- `tools/pro2_runtime/package.py --rust-bridge <production xr-pro2-bridge>` builds an uninstalled supervised-test bundle with it,
  for the live check in docs/pro2/smooth-live-test.md (Run C).

## Incident monitor

`src/incident/` ports `tools/xr_controller/incident_detect.py`, `incident_report.py` and
`incident_monitor.py`: the detectors, the report bundle and its Markdown, the scrubber, the rate
limit, the private incidents folder, the on-incident hook and the monitor thread that ties them
together. It is not wired into a running controller yet; that comes with the controller state
machine. `corpus_tests.rs` replays `corpus/incidents/scenarios.json`. Where it differs from the Python:

- The scrubber uses the `regex` crate, which has no lookbehind, so the base64 rule is matched by
  hand. The scrubber tests hold it to strings checked against the Python.
- An exit report line holding `NaN` is skipped: serde_json does not read it, Python's `json.loads` does.
- Reading the tracking state never creates its folder (the Python `private_dir(create=True)` does).
- The hook runs in its own process group (`process_group(0)`) and a timeout kills that group; the
  Python starts a new session. Both take the hook's children with it.
- `incident monitor: <source> failed: ...` lines name the error in Rust's words, so the corpus
  leaves them out.
- The reports keep Python's dict order through serde_json's `preserve_order`. That feature is a
  dev-dependency for now: as a normal dependency it would unify into the renderer's serde_json in
  workspace builds and reorder its output. Wiring the monitor into the controller has to settle
  that, either by turning the feature on for the controller binary alone or by writing the reports
  in a fixed order.

### D12: reconnect after a USB drop

The C++ bridge ends the run when the glasses go away. Once a run is ready, the Rust one waits for
them instead. Any of these counts as a drop: the glasses leave sysfs, they change bus address, or
no pose comes for 100 ms. On a drop the bridge ends the SDK session in its order (close, stop,
shutdown, destroy), pauses the feed so the renderer holds the last pose, and writes
`{"event":"lost","reason":"unplugged"|"stale"|"no-pose","teardown_errors":n}`. When exactly one
Pro 2 is present again, the bridge brings up a fresh session (create through open), retrying
every 250 ms, and asks for the last display mode again. The first published pose writes
`{"event":"reconnected","down_ms":x,"attempts":n}`. The run ends with `device-lost` in two cases:
the glasses are not back within 30 s, or the run ends while they are down. Before ready, every
fault still ends the run, as in the C++ bridge. `launch.py` accepts both events from either bridge
and stays healthy through an outage.

Offline gates (Rust build): the reconnect tests in `tests/pro2_runtime_qa/test_rust_bridge.py`, and
`test_session_survives_unplug_and_replug_with_tracking_back` in `tests/pro2_runtime/test_pipeline.py`.
The second one runs the real launcher and a native reader through a 0.6 s unplug. Offline tests
cannot show that the vendor SDK accepts a destroy and a new create in one process. Only a live
unplug and replug proves that.

## Renderer logic parity

`xr-renderer`'s library is the C++ renderer's logic with no GL: `validate_layout`, the view animator
and placement maths (`view.cpp`), glass pill and panel geometry (`glass.cpp`), the HUD's layout
(`hud.cpp` without FreeType or pixels), the options panel geometry and the binds panel's fit. Its
tests port every non-raster check of the matching ctests (`layout`, `view`, `hud`, `glass`,
`binds_panel`, `options_geometry`). The raster parts (atlases, card pixels, fonts) come with the GL
port.

`late_latch` is `late_latch.cpp` (RR-72): the late-latch schedule, adaptive margin, miss boost and
summary, with `parse_guard` going through the C library's `strtod` as the C++ does. `tests/late_latch.rs`
ports every check of the `late_latch` ctest, and checks that a late frame boosts the margin and the
frame after it is not late too. Wiring it into the shell is a later task.

`predict` is `predict.cpp` (RR-72): the look-ahead config, angular velocity from the feed's two newest samples
(with the whole-ms timestamp mean), the bounded delta, `LatencyTracker` with its read-phase model, `PoseAge`,
`VsyncMeter` and `prediction_horizon`, same fields and constants. `tests/predict.rs` ports every check of the
`predict` ctest but the D3 ones (`motion_delta`, `motion_age_ms`, `MotionFeed::open`), which go with `motion_feed`.
`VsyncSnapshot` is declared in `pose`, next to `PoseFeed::vsync` as in `pose.hpp`, and re-exported from `predict`.

`motion_smooth` is `motion_smooth.cpp` and `motion_feed` is `motion_feed.cpp` (RR-72): `MotionSmoother` (the Pro 2's
frame-rate motion) and `MotionFeed` (the driver's `pose_prediction` wire, read through `xr_pose::prediction`, with
`motion_delta` and `motion_age_ms`). `motion_delta` runs `xr_pose::extrapolate`, as the C++ runs `luma::extrapolate`.
`tests/motion_smooth.rs` ports every check of the `motion_smooth` ctest, and replays `pro2_smooth_replay.cpp`'s per-frame
pose path (stabilizer, pose state, pose age, horizon, smoother, view) over `tests/fixtures/pro2_smooth_replay{,_wire}.in`:
byte-equal with `golden/motion_smooth.txt`, which `golden/motion_smooth.cpp` writes from the C++ replay itself.
`tests/motion_feed.rs` ports the D3 checks of the `predict` ctest and reads a wire xr-pose's `FeedWriter` publishes.
It checks `motion_delta` exactly: bit-equal with `golden/motion_feed.txt`, which `golden/motion_feed.cpp` writes from
the C++ `motion_delta` over a grid of poses, velocities, accelerations, horizons and limits.

`stabilization` is `stabilization.hpp` and `continuity` is `continuity.hpp` (RR-72). `Stabilizer` mirrors libstdc++'s
three-argument `std::hypot`. `tests/stabilization.rs` ports the `stabilization` ctest and is byte-equal with
`golden/stabilization_trace.txt`, the C++ `stabilization_trace` output on `tests/fixtures/stabilization_trace.in`.
`ViewContinuity` encodes, decodes and resumes the C++ blob in its x86-64 GCC byte layout, padding written as zeros.
`save`/`restore` work over the `--continuity-fd` file, which is consumed once. `tests/continuity.rs` ports the
`continuity` ctest. It restores `golden/continuity_{mid,end}.bin`, which `golden/continuity.cpp` writes from the C++,
and matches every byte except padding and the hash. It also keeps `golden/continuity_{mid,end}_rust.bin` current
(`XR_WRITE_GOLDENS=1`), and `continuity.cpp read` restores those in C++ with the same continuation.

`pose` is `pose.hpp` and `pose.cpp` (RR-72): the quaternion maths, `PoseState` (reference, recentre, level, stale
tracking), `ModeController` (anchor, follow and locked, with the maximize hold and restore) and `PoseFeed`. `PoseFeed`
reads the driver's `/xr-luma-pose` seqlock first (100 ms freshness, a 16-entry history and a 16 ms look-back). Otherwise
it reads xr-pose's mutex `FeedReader`, with the 2 ms lock wait, the backoff and the `EOWNERDEAD` recovery. While the
lock is held it serves the newest sample of either source again with its device time unchanged, so a feed that stops
holds the last pose and `PoseState` reports tracking lost. `stats()` gives the C++ `PoseReaderStats`. `retries` counts
the copies `PoseReader::read_counted` made again because the writer was mid-write, as the C++ counts them. `vsync()`
decodes the `pose_vsync` segment. `tests/pose.rs` ports every check of the `pose` and `level_reference` ctests and all
of `focus_center`.

The VR video controls card (`video::card::render_video_card`) is ported with its pixels: `tests/video_card.rs` holds
`video_test.cpp`'s card checks and compares every case of `golden/video_card.cpp` (sizes and FNV-1a of the RGBA bytes,
`golden/video_card.txt`) byte for byte, in the built-in pixel font and the installed `HUD_FONT_FAMILY`; the installed-font
lines follow this machine's font file, so regenerate them (the command is in the `.cpp`) when it changes.

The HUD cards are ported with their pixels too: `hud::render_hud_card` (the mode, cursor, prediction, typing and
tracking pop-ups) and `hud::render_help_card` (every page, its click targets and `help_card_fit`). `tests/hud.rs`
(`cards_match_cpp_golden`) replays every case of `golden/hud_cards.cpp`, the overlays' `image()` after a call sequence
included, and matches `golden/hud_cards.txt` byte for byte in both fonts. `tests/help_card.rs` ports `help_card_test.cpp`.

The font and the Material drawing under them are checked on their own. `tests/hud_font.rs` ports the `hud_font` ctest and
matches `golden/hud_font.txt` (`golden/hud_font.cpp`: metrics, measures and every plot of the ctest's queries and of
single glyphs at five sizes in both faces) byte for byte. It also checks the no-font path: a missing family, and the system
family with `FONTCONFIG_FILE` pointing at an empty configuration (in a child process), fail with the C++ reasons, and the
pop-ups fall back to the 5x7 pixel font, byte-equal to the C++ fallback cards. `tests/material_draw.rs` matches
`golden/material_draw.txt` (`golden/material_draw.cpp`: rounded containers, outlines, discs, every icon at five sizes, the
corner caps' and icons' distance fields, the blend) byte for byte.

Three dump binaries print what their C++ namesakes in `spike/tests/` print:
`glass_geometry_dump`, `placement_geometry_dump` and `options_geometry_dump` (default mode,
`--layout` and `--band`). `cargo test` runs `glass_contract_test.py` and
`placement_contract_test.py` against the Rust dumps. To compare them byte for byte with a C++ build
of `spike/`:

```sh
cmake -S spike -B /path/to/build -DCMAKE_BUILD_TYPE=Release
cmake --build /path/to/build --target glass_geometry_dump placement_geometry_dump options_geometry_dump
rust/crates/xr-renderer/cpp_parity.sh /path/to/build
```

The maths stays in `f32` in the C++ order, with `std::min`/`std::max`/`std::remainder` mirrored, so
the numbers match bit for bit; `--layout` reproduces nlohmann's number format.
