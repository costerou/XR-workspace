# Ownership broker — offline implementation report

## Outcome and authorization boundary

Implemented an opt-in C++23 lifecycle module and Python broker/journal integration.
The module was compiled and linked against the installed Hyprland/Aquamarine;
its registry and wire validator were exercised offline. No plugin was loaded,
no isolated or live compositor was started/queried, and no device, SDK, capture
or input actions were performed. Renderer/pattern/capture/tracking sources are
unchanged. Existing untracked live-evidence documents were not edited or staged.

The user approved the small compositor-resident ownership boundary, accepting
ABI coupling and compositor-crash exposure. This is **not runtime approval or
three-desktop acceptance**. Plan/spec record only that narrow amendment.

## Built artifact and dependencies

Artifact (not committed):
`/home/cody/Projects/viture-workspace/.worktrees/native-spike/build/native-spike/libxr-ownership.so`

Final SHA-256:
`b4761b38cf73c65196822af67bcf592cdeaaee81da75c2382d9c78a241a1b610`

Build commands actually run:

```sh
cmake -S spike -B build/native-spike -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/native-spike -j2
ctest --test-dir build/native-spike --output-on-failure
```

`XR_ENABLE_OWNERSHIP_PLUGIN` defaults OFF. The module and registry use C++23,
PIC, Release optimization and `-Wall -Wextra -Wpedantic -Werror`. The ownership
test uses throwing CHECKs, not assertions disabled by `NDEBUG`. No FetchContent,
hyprpm, Ninja requirement, package installation, autoloader or config edit.

Installed versions read from package metadata:
- Hyprland 0.56.2-4; pinned headers commit
  `efb50993780079460b0cbed1363e2166a2de1d9f`.
- Aquamarine 0.15.1-1; hyprutils 0.14.2-1; hyprlang 0.6.8-5;
  hyprgraphics 0.5.1-4.
- GCC 16.2.1+r23+gd564253eb6c8-1, CMake 4.4.4-1, pkgconf 3.0.10-1.
- No missing dependency was encountered.

The real module link succeeded with pkg-config libraries, including
`libaquamarine.so.14`, `libhyprutils.so.13`, `libhyprgraphics.so.4`,
`libhyprlang.so.2` and `libwayland-server.so.0`. `readelf` inspected DT_NEEDED.
A static `nm`/`readelf` closure check against the installed Hyprland ELF and
libraries inspected 143 ELF files and resolved all 96 strong module imports.
This did **not** use dlopen/ldd or call PLUGIN_INIT. A successful shared-module
link and symbol lookup do not establish runtime ABI or behavior safety.

PLUGIN_INIT compares `__hyprland_api_get_hash()` exactly with
`__hyprland_api_get_client_hash()` and checks the pinned Hyprland commit;
PLUGIN_API_VERSION returns the installed API version. Package changes require
rebuild/requalification. Actual loader rejection remains an isolated-runtime test.

## Implemented contracts

### Native identity and lifecycle

`spike/ownership/registry.{hpp,cpp}` owns a bounded session/run/UUID registry
against an injected Backend. Entries precede synchronous creation. Exactly one
selected-backend emission must bind the pending UUID; missing/wrong/duplicate
emissions, destruction, collisions and reentrancy cannot configure a replacement.
Retries of the same UUID/spec return its retained lifecycle state; conflicting
specs or reuse of a run slot fail. Dead entries remain tombstones. Unknown UUIDs
are unproven, never absent. Partial failures retain captured objects/entries.

`plugin.cpp` implements the actual installed APIs:
- Select the existing `AQ_BACKEND_HEADLESS` implementation; use `createOutput`.
- Capture `newOutput`'s actual `IOutput` while the operation is active; retain an
  idempotent destruction listener. Resolve the monitor by `m_output` identity,
  not monitor name or reusable numeric ID.
- Add a narrowly named runtime `CMonitorRule`, then apply it to that monitor
  object: 1920x1080, 120 Hz, scale 1, explicit offset, normal transform,
  enabled/nonmirrored. Never use stock keyword monitor (this host uses Lua).
- Allocate an unused positive named workspace through WorkspaceState, retain
  its object, activate it with no mouse move/focus request, and verify placement.
- Inspect actual geometry and `m_currentMode->refreshRate == 120000` mHz, not
  a rounded advertised rate. Also require monitor refresh 120 Hz. Initial
  headless 60 Hz is not accepted as success.
- Retain narrowly scoped rules across deferred reload; the rule-manager reload
  listener latches mismatches instead of reapplying. Remove only unchanged
  entries. A destruction callback removes an unchanged owned rule while the
  object binding still identifies its owner. It never clears the manager.
- Capture the approved fallback object at hello, require a usable DRM-backed,
  enabled, DPMS-on, nonmirrored output, and thereafter use its epoch-scoped token.
- Evacuate source workspaces through `moveWorkspaceToMonitor(..., true)`, check
  source/fallback liveness after synchronous calls, and verify mapped windows
  reference registered, reachable workspace/monitor objects. Refuse affected
  special/pinned cases. Recheck dependencies before direct retained-object
  `IOutput::destroy()`. Require destruction notification, monitor absence and
  continued reachability; a boolean destroy return is not sufficient.

Lifecycle handlers are confined to the load/event-loop thread with a reentrancy
guard. They do not read/write journals, wait on network/supervisor work or use
shell commands. Entropy reads are nonblocking. Exit unregisters the command and
detaches listeners; it does not blindly delete unresolved outputs.

### Private wire

One nonexact `xrspike ` handler accepts a strict JSON envelope:
`schema=1, session, run_id, request_id, epoch, op, payload`.

- `hello`: `{fallback_name}`; initial epoch `unknown`; returns random load epoch,
  fallback token and protocol-qualified flag (not a live-acceptance claim).
- `create`: exact planned UUID/name/index/geometry/workspace fields.
- `inspect`: `{id}`; returns registry state, identity, actual observation,
  workspace/window associations and fallback usability.
- `evacuate_remove`: `{id, name, fallback}`; name is a consistency check only,
  never the deletion target.

Requests including prefix are at most 8192 bytes; replies at most 1 MiB. Reject
unknown fields/operations, duplicate keys, deep nesting, noncanonical UUIDs,
invalid tokens, booleans-as-integers and signed/unsigned overflow. Capacity is
32 runs/96 retained entries, with desktop inspection caps of 128 monitors and
4096 workspaces/windows. Errors return bounded failure objects, not raw commands.

### Python and supervisor

`OwnershipBrokerClient` uses `j/xrspike <JSON>` over the existing verified
SO_PEERCRED/session transport, with its existing two-second response deadline
and one-MiB cap. Raw query allowlists are unchanged; stock output creation,
removal and legacy monitor-keyword mutations were removed from the raw mutation
allowlist. Missing plugin fails before journal prepare or output mutation.

`create_outputs(specs, journal, broker)` performs hello, then for each output:
real schema-1 journal prepare (file+directory fsync) → create → separate inspect
and exact geometry/workspace/window checks → confirm. An existing journal is
recovery-only, not permission to replay creation after epoch loss. Creation
errors propagate with durable intents intact; callers invoke `recover` for
partial rollback, as exercised by the tests.

`RecoveryBackend.evacuate_remove(UUID, name)` is now mandatory for output undo.
The old Python snapshot/workspace-dispatch/name-removal sequence is gone; there
is no compatibility fallback. The broker verifies evacuation and mapped-window
reachability inside the identity-validating handler. Python independently
inspects the tombstone before journal completion. Settings restoration remains
separate and unenabled by this broker; workspace operations are inside native
create/evacuation, not separate raw dispatches.

The existing managed supervisor helper now gates on the supplied adapter and
accepts the broker backend. Its raw peer checks and renderer code are unchanged.
A socketpair/synthetic-child test exercises recovery of the real journal after
child exit. A restarted supervisor can reconcile UUIDs in a surviving registry;
a fresh plugin epoch/unknown UUID preserves unresolved journal records.

## RED / GREEN evidence

Observed RED runs included:
- Missing `registry.hpp` and missing Python ownership module on initial tests.
- A retry incorrectly returning live after readback/rule mismatch.
- A dead-entry retry incorrectly skipping reachability checks.
- Max-uint64 coordinate accepted after signed narrowing (including an initial
  insufficient cross-signed JSON comparison fix).
- Reuse of a tombstoned run slot under a new UUID.
- Invalid handshake leaving a usable-looking client epoch.
- Boolean workspace IDs passing readback equality.
- Owned-name raw removal reaching the socket instead of being rejected.
- Managed supervisor missing the new broker adapter boundary.

Those regression tests passed after fixes. The first module compile also exposed
C++23/nlohmann string-comparison overload issues; explicit conversions fixed
these. Warnings-as-errors found misleading one-line conditionals; these were
split and the real module rebuilt.

Final offline suite results:
- `python -m unittest discover -s tests/native_spike -v`: **90 passed**.
- `python -m unittest discover -s tests/probe -v`: **62 passed**.
- CMake Release build: module linked successfully.
- CTest: **6/6 passed**, including ownership, layout, IPC, metrics,
  presentation-offline and native-help.
- `git diff --check`: passed.

Ownership coverage includes three distinct output/workspace objects, partial
creation at output 2/3, missing/wrong emission, synchronous destruction,
idempotent destruction/tombstones, replacement objects and workspace IDs,
lost-reply UUID retry, spec collision, registry loss, rule/readback invalidation,
reentrancy, fallback loss during migration, stranded windows, special/pinned
refusal and repeated recovery. These are injected-backend conditions, not a
running Hyprland. Python tests use real journal files, cover before-mutation
pending intent and before-confirm readback, partial handle rollback, lost reply,
supervisor restart and epoch loss. Python-generated requests for all operations
also passed the **actual C++ wire validator** via the offline test executable.

## Limitations and next authorized test

1. Native API execution, deferred monitor reload ordering, custom headless mode
   application, actual workspace/window migration and loader hash rejection
   are **not runtime-qualified**. The injected backend does not replace this
   requirement. Exact mode mismatch blocks confirmation rather than downgrading.
2. The public managed `run` and filename-only `recover` CLI remain gated. The
   explicit broker API and managed supervisor helper are integrated; the CLI
   needs a separately reviewed runtime consent/instance/fallback workflow before
   enabling. No automatic plugin load or unattended reload is provided.
3. A missing emission, already-dead ambiguous source, unavailable fallback,
   changed rule or failed migration can leave durable incomplete recovery.
   No absence-by-name inference or forced deletion is attempted. Recovery is
   target-identity safe, not an atomic transaction over the entire desktop.
4. Active-run plugin unload loses registry authority and leaves unresolved
   resources/rules; do not unload/reload/upgrade an active run. A malicious
   same-UID compositor plugin is outside this ownership guarantee.
5. A pure headless/nested fallback is deliberately refused. The next isolated
   test needs an explicitly authorized disposable DRM-backed environment (for
   example a suitable isolated VM/display setup), not a silent fallback-policy
   relaxation and not the user's live compositor.

Next: obtain fresh isolated-runtime approval, then load this exact build into a
matching disposable compositor. Verify three distinct 1920x1080@120000 mHz
outputs/workspaces after deferred reload; verify independent test windows,
normal removal, partial failure, lost reply/restart, same-name/ID replacement,
rule replacement, fallback loss, stranded/special/pinned windows, and epoch loss.
Only after that qualification seek physical-session P2 and visual/routing,
renderer-crash and supervisor-crash recovery acceptance. No capture/input-device/
SDK permissions, sustained 120 FPS or live three-desktop success are claimed.

## Review fix round 1 — synchronous ownership semantics

Addressed I1/I2/I3 against base `0f17176b88a1555fb813689e8c35ff2ec76743d2`.
This section supersedes the original artifact hash and the stronger workspace
claims above. No plugin was loaded; no compositor was started or queried.
The parent’s untracked live evidence, runtime gates and Python contracts are
unchanged. No name-addressed deletion or compatibility fallback was added.

### Pinned source evidence read before implementation

Sources fetched from Hyprland commit
`efb50993780079460b0cbed1363e2166a2de1d9f` (raw GitHub), with installed headers
used for compilation:

- `src/state/MonitorState.cpp:109–130`: `add` calls `onConnect` synchronously,
  then schedules monitor-rule reload.
- `src/output/Monitor.cpp:1276–1331`: lowest-unused default ID selection;
  `setupDefaultWS` can reuse an existing workspace or create a new object,
  then assigns `m_activeWorkspace`. Therefore ID equality cannot establish
  provenance and the planner's free ID can already be occupied on return.
- `src/state/WorkspacePlacementController.cpp:239–379`, especially 257–290:
  moving the last active workspace creates a source replacement and activates
  it **before** moving the original. Clearing an active reference only during
  terminal disconnect avoids this branch for the proven empty replacement.
- `src/output/Monitor.cpp:392–505`: `onDisconnect` explicitly tolerates a null
  active workspace (416, 492), moves remaining source workspaces (465–484),
  then resets the active reference (494). This is the source-retirement path,
  not an assumed API for deleting arbitrary workspaces.
- `src/state/WorkspaceState.cpp:38–50,58–78`: workspace snapshot/reference
  semantics and creation; a configured binding can override requested monitor.
- `src/desktop/Workspace.cpp:29–87,399–404,532–545`: workspace lifetime,
  persistence/creation hooks, inert state and rename semantics. Rename preserves
  the positive ID, but emits callbacks; final native verification remains required.
- `src/config/shared/monitor/MonitorRule.cpp:7–33` and installed
  `MonitorRule.hpp:34–70`: `compare` omits VRR/EOTF (and name); it is a modeset
  decision, not complete ownership equality.

### Changed contracts and production wiring

`spike/ownership/lifecycle.hpp` now supplies the shared decisions used by the
actual `NativeBackend`, not just the injected backend:

1. **I1:** snapshot workspace objects before `createOutput`; retain the exact
   new active default object on the captured output's resolved monitor after
   synchronous creation. Require absent-before/present-now identity, ordinary
   positive workspace and exact monitor/active-object placement. If this proven
   object has the planned ID, rename that object rather than reject its own
   allocation or adopt by ID. Revalidate after monitor-rule application.
   Existing or replaced objects still block. Snapshot history uses weak native
   references so it does not prolong unrelated desktop workspace lifetimes.
2. **I2:** snapshot evacuation inputs and retain only the fresh last-active gap
   when it is the sole remaining source workspace, ordinary, nonpersistent,
   created empty, still numerically named, and has **no window references,
   mapped or unmapped**. Recheck its exact identity and properties on every
   reachability pass and again immediately before destruction. A renamed,
   replaced, occupied, persistent or otherwise unknown gap blocks cleanup.
   The strict mapped-window checks are unchanged: every mapped window must be
   registered and on a reachable non-source monitor/workspace pair.
   Immediately before retained-object `IOutput::destroy`, `disconnectRetiring`
   clears **only** that proven empty active gap. Native `onDisconnect` can then
   migrate it as an inactive workspace without creating another source gap.
   If destruction fails/throws while the source is still live and the original
   gap is still valid on it, the active reference is restored. There is no
   yield or arbitrary workspace deletion. Post-destruction checks allow **no**
   source workspace exception, require monitor absence and recheck all mapped
   windows. Registry tombstone/retry semantics remain unchanged.
3. **I3:** `sameRule` explicitly compares every public pinned rule field,
   including `m_vrr`, `m_sdrEotf`, name and each DRM mode member/name byte (not
   padding). Both `verified` and `eraseRule` use it; the latter also covers the
   destruction listener. VRR/EOTF edits invalidate inspection and prevent rule
   erasure/output deletion. No call to incomplete `compare` remains in production.

### Actual RED/GREEN commands and output

Configured with:

```sh
cmake -S spike -B build/native-spike -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/native-spike --target ownership_native_test -j2
ctest --test-dir build/native-spike -R ownership_native --output-on-failure
```

Before implementing the shared predicates, the legacy rejection/comparison
behavior was represented in the extraction scaffold. The first compiled RED run
returned exit 8, **0/4 passed**:

```text
ownership_native_default: freshDefault(allocated,before,after,source)
ownership_native_retirement: retirementGap(gap,before,after,source,true,empty)
ownership_native_vrr: !sameRule(original,edited)
ownership_native_eotf: !sameRule(original,edited)
```

After those predicates were implemented, an additional retirement test required
that disconnect see a null active source reference. Running the same build/test
commands with the old direct-destroy behavior returned exit 8, **3/4 passed**;
retirement failed at `!source->m_activeWorkspace`. The shared terminal-retirement
helper then made it pass, including failed-destroy and exception restoration.

Additional conservative renamed/nonempty-origin gap assertions were added
before their guards. These commands returned exit 8, **0/1 passed**, failing at
`!retirementGap(gap,before,after,source,true,empty)`:

```sh
cmake --build build/native-spike --target ownership_native_test -j2
ctest --test-dir build/native-spike -R ownership_native_retirement --output-on-failure
```

The final tests also exercise production `Registry` with a source-backed API
model: synchronous lowest-unused allocation, last-active replacement creation,
terminal disconnect, mapped-window movement, repeated recovery, stranded windows,
same-ID gap replacement, and user VRR/EOTF changes preserving the rule. This is
an offline semantic model plus shared production decisions, **not** execution of
Hyprland's methods. The actual module separately compiles those same decisions
with installed native pointer/rule types.

Final GREEN commands/output:

```text
cmake --build build/native-spike -j2
  [100%] Linking CXX shared module libxr-ownership.so
  [100%] Built target xr-ownership
ctest --test-dir build/native-spike -R ownership --output-on-failure
  100% tests passed out of 5
ctest --test-dir build/native-spike --output-on-failure
  100% tests passed out of 10
python -m unittest discover -s tests/native_spike -v
  Ran 90 tests in 16.195s — OK
python -m unittest discover -s tests/probe -v
  Ran 62 tests in 1.659s — OK
git diff --check
  exit 0, no output
```

Build issues encountered and resolved: the new standalone test initially lacked
libdrm include paths (added the existing HYPRLAND pkg-config target); the new
header needed its own `<iterator>` include for DRM name-array comparison. Neither
compile error was counted as behavioral RED evidence. Final GCC Release build
with warnings-as-errors passed; editor LSP diagnostics use an incomplete header
configuration and are not the actual compiler result.

Final artifact: `build/native-spike/libxr-ownership.so`, SHA-256
`3c315236b3b80aa6789497b64da79cd2536d4a58beb73f463ab8946ec408ee38`.

### Remaining risks / next qualification

Native callback ordering, actual disconnect side effects and external workspace
rules still require the separately authorized disposable-compositor test. In
particular, `onDisconnect` chooses its own backup monitor for the **empty** gap;
all window-bearing workspaces have already moved to the approved fallback.
Customized/renamed/persistent replacement gaps deliberately leave recovery
blocked. Failed destruction or callbacks changing state retain conservative
failure semantics; no forced recovery is attempted. Offline success is not
runtime approval, ABI qualification or live three-desktop acceptance.
