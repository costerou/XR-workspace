# Rust rebuild workspace

The Cargo workspace for the Rust rebuild planned in
[docs/rust-rebuild-plan.md](../docs/rust-rebuild-plan.md). Nothing here ships yet: the live system
still runs the Python and C++ stack, and no installer step builds or installs these crates. The one
exception is opt-in: `xr-workspace install` is a Rust port of the installer, run by hand (see
[Build and install](../docs/technical.md#the-rust-installer-xr-workspace-install)).

| Crate | Kind | Replaces |
| --- | --- | --- |
| `xr-wire` | library | Control and status envelopes, strict JSON (`strict_json`, `decode_control`), and the on-disk formats: journal, consent, progress, result, recovery and install pins, read and written byte-for-byte as the Python writers produce them through private atomic writes (0600, no-follow, fsync and rename) |
| `xr-pose` | library | Pose shared memory: SysV feed, seqlock channels, `PredictionWire` |
| `xr-luma-driver` | binary | The C++ Luma Ultra driver |
| `xr-pro2-bridge` | binary | The Pro 2 bridge host (VITURE SDK through FFI) |
| `xr-renderer` | binary | The C++ renderer |
| `xr-workspace` | binaries + library | Controller, supervisor and installer (`xr-workspace`) and the key helper (`xr-control`); the supervisor's Lua generators are in `xr_workspace::lua` |

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

`Cargo.lock` is committed. `deny.toml` allows permissive third-party licences only (MIT, Apache-2.0,
BSD, ISC, Zlib, Unicode-3.0); adding any other licence is a deliberate edit to that list. Our own
crates are `publish = false` and are skipped by the licence check.

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
draws the generated test pattern only (SHM, static or `--animate-pattern-ms`). Control frames other
than `start` and `stop` are refused, and `--capture`, `--pose-feed`, `--depth-fd` and
`--continuity-fd` exit 2, until those parts are ported.

`golden/metrics.cpp` and `golden/pattern.cpp` print the C++ timing reports and pattern frame hashes
the unit tests compare against byte for byte. The loopback compositor drives it end to end:

```sh
capture_e2e_bench --check --mode pattern --seconds 5 --warmup 1 \
  --renderer rust/target/release/xr-renderer [--fractional-scale 150]
```

Its output carries the renderer's `timing_report`, so the C++ and Rust reports can be compared
field by field.

## Corpus

`corpus/` holds golden files taken from the shipped Python and C++ code. The phase 6 parity gate
holds the Rust ports to them byte for byte.

- `corpus/hyprland/lua.json` lists every generated-Lua string the supervisor and the key helper send
  to Hyprland's `/eval`, each with its inputs. It has at least one case per branch of each
  generator. Inputs the Python refuses are recorded too, as `refused` with the error text.
- `corpus/hyprland/transcripts.json` records the request bytes `HyprlandClient` sends and the replies
  it gets, for each public call that talks to the compositor. A scripted stand-in compositor on a
  temporary socket gives the replies; the real Hyprland is never used.

- `corpus/luma-fusion/` holds the C++ `luma::Fusion`'s output for every sample of the two replay
  fixtures (`real.bin`, `upside_down.bin`, from `spike/driver/tests/fixtures/`) and for a faults run
  of the real one (`real_faults.bin`: warm-start bias, 10 lost samples, a 400 ms gap, a clock jump of
  1 s, a duplicate). `xr-luma-driver`'s `tests/fusion_parity.rs` replays them and requires every bit
  to match.

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

- `corpus/json/strict_json.json` holds 3435 JSON texts (hand-picked edges and a seeded fuzz), each
  with the verdict of Python's `strict_json` and of C++ `decode_control`, and the verdict
  `xr_wire::json::parse` must give. Where Python and C++ differ (nesting past 32, a lone surrogate
  escape, a number past the `f64` range, a byte order mark), the case names the rule followed; the
  Rust layer always takes the stricter verdict. `tests/native_spike/strict_json_corpus.py` makes it
  (with `CONTROL_WIRE_CHECK` set to the built `control_wire_check`); `test_strict_json_corpus.py`
  and `cargo test -p xr-wire` check it.

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
