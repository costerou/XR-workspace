# Native test-pattern qualification runbook

**Status: partial offline implementation; Task 3 live acceptance pending and
managed-output ownership blocked. Do not execute a live run from this document.**
The parent must obtain selected-session/run-scoped P2 consent first. Consent
alone does not remove the ownership blocker.

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
python -m tools.native_spike recover --journal /absolute/private/journal.json
```

`XDG_RUNTIME_DIR` must identify the approved user's runtime directory.
`preflight` is read-only but is **a live compositor query**, not an offline test.
It verifies actual IPC peer/process/lock identity and prints the snapshot and
explicit blocked ownership status. It never discovers an arbitrary default
instance. `run` rejects missing/invalid consent before any connection or write;
with valid consent it snapshots/checks the selected outputs and then stops before
journal/output creation or renderer launch. `recover` currently returns exit 2
and preserves the journal/resources: no independent incarnation continuity
survives process restart. It does not infer an instance from filenames or scan
sessions. Do not bypass this gate by manually removing names.

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

Managed creation/configuration, named workspace allocation/migration readbacks,
window reachability checks, last-output exit and partial-creation rollback are
**not implemented/qualified beyond offline planning and refusal**. The requested
`test_partial_creation_rolls_back` is not claimed as passing; the test proves
creation is blocked before a partial mutation instead.

The internal `run_presentation` helper wires a fixed native executable to the
Task 2 watchdog with private inherited startup/heartbeat and preconnected Wayland
sockets, validates identical IPC/Wayland peer credentials, and recovers with
`RecoveryAdapter` only after child exit. Production ownership checks currently
block it before spawn; it is not reached by CLI run. Its offline test uses a
synthetic child boundary, not the renderer. Public control dispatch is not wired
to a live lifecycle; no arbitrary public command is accepted.

After the ownership boundary is resolved, finish the lifecycle and CLI wiring,
then obtain P2 and separately verify three distinguishable local windows, one per
managed workspace; keyboard/mouse reachability without presentation focus;
normal exit and renderer-crash restoration against the journal. No capture,
input-device access, tracking, SDK, mode change or sustained 120 Hz claim follows
from an offline pass. Missing recovery display or failed restoration is a stop,
not permission to continue or declare a pair safe to disconnect.
