# Rust rebuild workspace

The Cargo workspace for the Rust rebuild planned in
[docs/rust-rebuild-plan.md](../docs/rust-rebuild-plan.md). Nothing here ships yet: the live system
still runs the Python and C++ stack, and no installer step builds or installs these crates. The one
exception is opt-in: `xr-workspace install` is a Rust port of the installer, run by hand (see
[Build and install](../docs/technical.md#the-rust-installer-xr-workspace-install)).

| Crate | Kind | Replaces |
| --- | --- | --- |
| `xr-wire` | library | Control and status envelopes, strict JSON (`strict_json`, `decode_control`) |
| `xr-pose` | library | Pose shared memory: SysV feed, seqlock channels, `PredictionWire` |
| `xr-luma-driver` | binary | The C++ Luma Ultra driver |
| `xr-pro2-bridge` | binary | The Pro 2 bridge host (VITURE SDK through FFI) |
| `xr-renderer` | binary | The C++ renderer |
| `xr-workspace` | binary | Controller, supervisor, key helper and installer |

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

## Corpus

`corpus/` holds golden files taken from the shipped Python and C++ code. The phase 6 parity gate
holds the Rust ports to them byte for byte.

- `corpus/hyprland/lua.json` lists every generated-Lua string the supervisor and the key helper send
  to Hyprland's `/eval`, each with its inputs. It has at least one case per branch of each
  generator. Inputs the Python refuses are recorded too, as `refused` with the error text.
- `corpus/hyprland/transcripts.json` records the request bytes `HyprlandClient` sends and the replies
  it gets, for each public call that talks to the compositor. A scripted stand-in compositor on a
  temporary socket gives the replies; the real Hyprland is never used.

`tests/native_spike/lua_corpus.py` makes both files. `test_lua_corpus.py` fails when they no
longer match the Python, or when a generator branch has no case. After a deliberate change to a
generator, regenerate them:

```sh
TMPDIR=/tmp /usr/bin/python3 tests/native_spike/lua_corpus.py --write
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
