# Native test-pattern qualification runbook

**Status: partial offline implementation; Task 3 live acceptance pending and
managed-output runtime qualification pending. Do not execute a live run from this document.**
The parent must obtain selected-session/run-scoped P2 consent first. Consent
alone does not qualify the new native ownership broker.

## Offline build

From the repository root:

```sh
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/probe -v
cmake -S spike -B build/native-spike -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike
ctest --test-dir build/native-spike --output-on-failure
build/native-spike/xr-native-spike --help
```

The executable links actual scanner-generated layer-shell/xdg-shell descriptors
and libwayland-client. It implements a named-output overlay with keyboard
interactivity NONE, empty input region and exclusive zone 0. No seat, virtual
keyboard/pointer, focus, capture or SDK interface is bound. It draws three colored
panels and a spatial grid using **generated SHM pixels**, not captured desktops.
This test-pattern transport is not the planned GPU capture path, is not an
implicit capture fallback, and establishes no frame-rate/performance result.
Required globals: compositor >=4, shm >=1, named wl_output >=4, layer-shell >=1;
layer-shell binds at most **v4**, the pinned XML's maximum (within the v5-or-lower
requirement). Missing globals, missing/ambiguous target, closed layer, output
removal, invalid configure size or disconnect stops presentation. Buffer memory
is bounded and busy buffers are not reused before release. Configure is acked
before buffer attach, and resize uses fresh/released storage.

## Opt-in offline ownership broker

The compositor-resident lifecycle-only architecture is approved; loading is not.
Build without connecting to any compositor:

```sh
cmake -S spike -B build/native-spike -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/native-spike
ctest --test-dir build/native-spike --output-on-failure
```

Artifact: `build/native-spike/libxr-ownership.so`. No autoloader/config edits are
provided. Rebuild after Hyprland/Aquamarine changes; initialization checks the
exact server/client API hash and pinned Hyprland commit. Do not unload/reload an
active managed run. Unload detaches listeners and leaves unresolved resources.

The internal Python API now supports an explicitly constructed
`OwnershipBrokerClient` and real schema-1 journals. `create_outputs(specs, journal,
broker)` requires hello before prepare, then pending fsync → create → inspect →
confirm. `recover(journal, broker)` invokes the UUID/fallback-handle transaction,
then registry readback before marking done. It never emits name-based output
removal. Missing plugin, unknown UUID, epoch loss, unreachable windows, changed
rules or unusable fallback preserve unresolved journal records. A fresh client
may recover only UUIDs retained in the surviving registry; it may not replay
creation from an existing journal. The public `run`/`recover` plumbing now exists
behind explicit P2 consent and the unchanged two-physical-output checks. This is
offline-tested code, **not permission to run on the host or runtime acceptance**.
See [managed-integration-report.md](managed-integration-report.md) for the exact
restart contract, evidence and remaining qualification gates.

Next test requires **new explicit isolated-compositor authorization**: load the
built module into a disposable matching compositor, use a qualified physical/DRM
fallback (the broker deliberately rejects a headless fallback), create three
outputs and verify deferred rule reload, exact active 120000 mHz and workspace
objects. Test normal evacuation, partial creation, lost reply/supervisor restart,
stranded/special/pinned windows, same-name/ID replacement and plugin epoch loss.
Only afterward seek physical-session P2 and three-window routing/visual recovery
checks. No capture, SDK, device transition or live three-desktop success is implied.

## Exact consent record

A user-created, owned, single-link regular file, mode **0600**, maximum 16384
bytes, duplicate keys forbidden. No unknown fields. Example **template**, not
approval; replace INSTANCE/RUN/output names with parent-approved values:

```json
{
  "schema": 1,
  "gate": "P2",
  "run_id": "RUN",
  "instance": "INSTANCE",
  "output": "DP-2",
  "recovery_output": "DP-1",
  "allow": ["headless-outputs", "test-pattern"],
  "capture": false,
  "input": false,
  "sdk": false,
  "mode_switch": false
}
```

`schema` must be integer 1, not boolean. `instance` must match the explicit
40-lowercase-hex + `_decimal_decimal` Hyprland signature. `run_id` is an ASCII
Task 2 token, at most 80 characters, and must be new per approved run. The
presentation and independently approved physical recovery output must differ.
The `allow` array above is exact and ordered. All four false fields must remain
boolean false; this CLI cannot authorize those later gates. A record documents
external user approval; the software does not infer consent from connected
hardware, previous runs, or a file it fabricated itself.

## Future runtime invocation (NOT executed during implementation)

```sh
python -m tools.native_spike preflight --instance INSTANCE
python -m tools.native_spike run --instance INSTANCE --output DP-2 --consent-file /absolute/private/consent.json
python -m tools.native_spike recover --journal "$XDG_RUNTIME_DIR/xr-workspace-spike/RUN/journal.json" --instance INSTANCE --output DP-2 --consent-file /absolute/private/consent.json
```

`XDG_RUNTIME_DIR` must identify the approved user's runtime directory.
`preflight` is read-only but is **a live compositor query**, not an offline test.
It verifies actual IPC peer/process/lock identity and prints the snapshot and
explicit blocked ownership status. It never discovers an arbitrary default
instance. `run` rejects missing/invalid consent before any connection or write.
It validates the independent physical pair and broker, then creates a private
`$XDG_RUNTIME_DIR/xr-workspace-spike/RUN/journal.json`. Before output mutation it
fsyncs the exact instance, presentation/fallback names, broker epoch and fallback
handle. Each output still has its own pending intent before create and separate
inspect before confirmation. A nonblocking lifetime owner lock excludes another
managed run/recovery for that journal. Existing bound runs require recovery, not
creation replay, even when all operations are done.

The supervisor launches the generated static pattern, not captured desktops.
Its spawn-only validation worker checks the pair and all owned outputs without
blocking heartbeat expiry/reaping. Guard failure or deadline expiry stops the
renderer; cleanup follows renderer and worker teardown. Partial create,
connection and spawn failures also attempt handle-addressed recovery. Incomplete
recovery retains the journal; storage errors are fatal, not silently retried.

`recover` requires explicit instance/output and a private P2 consent file for the
same run and saved output pair; it never launches a renderer or creates outputs.
`--journal` must name the canonical runtime path above, with `RUN` matching the
consented run ID. Copied/renamed journals and symlink aliases are refused, even
after the controller exits: they must not select a different owner lock or leave
the canonical records stale. Recovery writes only the canonical journal. The
existing no-follow and single-link checks still apply at that location.
It requires an existing journal and the same verified compositor session, and
uses the **saved epoch/fallback token without a new hello**. Missing legacy
context cannot be reconstructed from names or a fresh broker handshake. A lost
epoch, unknown UUID or failed evacuation preserves pending records. The manual
CLI conservatively retains the two-output prerequisite; automatic teardown can
still use the exact surviving fallback if the presentation output disappears.
No path infers an instance from filenames, scans sessions or loads a plugin.

## Ownership blocker and stop behavior

Inspected Hyprland commit:
`efb50993780079460b0cbed1363e2166a2de1d9f`.

- `src/debug/HyprCtl.cpp`, `dispatchOutput`: creates by name, returns `ok`, no
  opaque creation token or caller-owned UUID.
- `src/output/Monitor.cpp`: socket2 `monitoraddedv2`/`monitorremovedv2` payload is
  numeric ID, name, description. Numeric IDs and names can be reused.
- `src/managers/EventManager.cpp`: event strings have no sequence/replay cursor
  or query-snapshot barrier; queue overflow removes the subscriber at 64 queued
  events. Connection loss must invalidate any attempted continuous history.
- `src/Compositor.cpp`: lock file contains PID and Wayland socket name; the
  instance signature is generated from commit/time/random, not a journal UUID.

SO_PEERCRED plus process-start/boot identity can bind IPC to a compositor
incarnation, **not a monitor incarnation**. A continuous event subscriber could
invalidate known bindings on removals/disconnects, but no independent mechanism
has been qualified here to associate the create transaction and all subsequent
snapshots with the journal UUID in the presence of same-name recreation. After
restart there is no event history to replay. No names/IDs are promoted to UUIDs.
Consequently no live event tracker or optimistic create/rollback is shipped as
fake proof. A qualified stronger mechanism or explicit parent/user revision of
the ownership contract is needed, not just a new consent file.

## Remaining acceptance work

Managed creation/configuration and evacuation now have a compiled native backend,
injected lifecycle tests, and Python real-journal integration tests. These are
**offline evidence only**, not native API runtime qualification. Deferred reload,
headless custom-mode behavior, workspace/window migration and visual recovery
must still be exercised in the separately approved isolated/physical sessions.
See the ownership broker report for concrete coverage and limitations.

The managed CLI uses the shared fixed-executable launcher and Task 2 watchdog
with private inherited startup/heartbeat and preconnected Wayland sockets. It
validates identical IPC/Wayland peer credentials and recovers only after launcher
teardown. The older low-level `run_presentation` helper remains available but is
not the CLI orchestration entry point. Offline synthetic transports/socketpairs
and harmless subprocess tests do not qualify the native renderer/compositor.
Public control dispatch is not wired to a live lifecycle; no arbitrary public
command is accepted.

After isolated managed-CLI/supervisor qualification and the remaining adversarial
gates, obtain P2 and separately verify three distinguishable local windows, one per
managed workspace; keyboard/mouse reachability without presentation focus;
normal exit and renderer-crash restoration against the journal. No capture,
input-device access, tracking, SDK, mode change or sustained 120 Hz claim follows
from an offline pass. Missing recovery display or failed restoration is a stop,
not permission to continue or declare a pair safe to disconnect.
