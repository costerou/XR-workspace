# Task 3 — partial offline delivery; live acceptance pending

Base: `0b341d8c6176bbeb8e998b192ea45808d32cb378`.

## Outcome

Built a real native Wayland layer-shell test-pattern executable with actual
scanner-generated protocol code and added raw Unix-socket IPC, conservative
normalization, consent gates, offline layout planning and internal supervisor
launcher wiring. **This is NOT full Task 3 completion, including the offline
managed lifecycle: ownership proof is blocked.** No live compositor queries or
mutations, renderer presentation launch, device/input/capture/SDK actions,
package installation or subagents were used. Only native `--help` was invoked
outside offline CTests. Source retrieval was limited to pinned public protocol
and compositor source.

### Delivered interfaces

- `HyprlandClient(instance, runtime_dir)`: direct Unix socket requests, explicit
  full instance signature, SO_PEERCRED/current UID and process-start binding,
  two-second **whole-response** deadline, 1 MiB response cap, strict JSON.
  Raw query allowlist remains exactly monitors/workspaces/clients/activewindow/
  getoption/cursorpos. Monitors uses `j/monitors all`; only
  `getoption input:follow_mouse` is allowed. No normalized pseudo-queries.
  Internal create/remove/configure/migrate/follow_mouse strings are constrained;
  no shell or public arbitrary command field exists.
- `verified_session()` verifies actual peer executable, PID/start tick, boot ID
  and selected-instance lock/PID, then computes an incarnation fingerprint.
  It does not echo the constructor's configured string as session proof.
  Same-UID peers are the existing trust boundary; this is not protection against
  malicious code already able to modify the user's files/processes.
- `RecoveryAdapter`: normalized session/workspaces/settings and conservative
  monitor mapping. An observed XR-SPIKE name raises `OwnershipBlocked`, never a
  derived UUID. Empty/malformed snapshots fail closed. Adapter mutations remain
  blocked; `recover(journal, adapter)` retains unresolved resources/records.
- `plan_outputs`: separate UUIDs, exact run/index names, three 1920x1080 at scale
  1.0 and requested 120000 mHz, unused workspace IDs/names, placement to the right
  of scaled/transformed existing rectangles. `verify_output` rejects `ok` with
  no output, mismatched geometry, and 119.88 instead of 120. No timing claim.
- `create_outputs(specs, journal, hypr)` has the agreed signature but **refuses
  before journaling/mutation** because no qualified incarnation proof exists.
  It is a safety stop, not an implemented three-output lifecycle.
- Native `Presentation::open`, `draw_test_pattern`, `dispatch`, `close`: real
  libwayland implementation, explicit output binding, overlay, NONE keyboard,
  empty input region, exclusive zone 0, configure/ack/resize, bounded SHM buffer
  storage/release lifetime and teardown. Required globals fail closed. No seats,
  virtual input, focus, capture or SDK bindings. Test pixels are generated SHM,
  not captured desktops; the later EGL/GPU capture path is not qualified.
- `run_presentation` in supervisor.py: fixed executable path, private inherited
  start/heartbeat and preconnected Wayland sockets, same actual IPC/Wayland peer,
  Task 2 start-frame decoder on native side, watchdog owns/reaps the exact child,
  recovery receives the adapter. Tests exercise this with a synthetic child
  boundary and real socketpairs, **not a renderer launch or ownership proof**.
  Production ownership gate blocks before spawn. CLI run does not yet reach this
  helper; remaining lifecycle/Wayland connection/public-control dispatch wiring
  is explicitly unfinished, not secretly bypassed.
- CLI `preflight`, `run`, `recover`: help/import is inert; run validates consent
  before connecting/writing; preflight is a future live read-only request; run
  stops before creation/launch; standalone recovery refuses restart ambiguity.
  Journals, control schemas and diagnostic allowlists remain unchanged.

## Concrete ownership limitation

Read the pinned `efb50993780079460b0cbed1363e2166a2de1d9f` sources by HTTPS only:

1. `src/debug/HyprCtl.cpp:1760–1812`: output create returns textual success, not
   a creation UUID or independently verifiable transaction receipt.
2. `src/output/Monitor.cpp:387–398`: monitoradded[v2]/monitorremoved[v2] expose
   name or numeric ID/name/description, not a unique incarnation token.
3. `src/managers/EventManager.cpp:126–131,163–185`: event format has no sequence,
   snapshot barrier or replay cursor; a subscriber overflowing 64 queued events
   is removed. A reconnect cannot reconstruct independently continuous history.
4. `src/Compositor.cpp:206–210,765–772`: generated instance signature and lock PID/
   Wayland socket support compositor identity checks, not per-output UUID proof.

A live subscriber could detect removals/disconnects and invalidate bindings, but
this implementation has not established an independently sound create/event/
snapshot association under same-name recreation. Socket peer credentials alone
prove only the compositor process. **No live tracker was implemented and then
represented as proof**; no usable monitor identity was invented from a name/ID.
After supervisor restart, replay is unavailable in this protocol. Resolve the
mechanism or obtain an explicit parent/user contract revision before continuing.

Consequently these remain blocked/unimplemented: verified three-output creation,
configuration/workspace assignment readbacks, migration/window reachability
verification, owned removal and last-output exit, partial-creation rollback, and
full CLI lifecycle integration. The requested `test_partial_creation_rolls_back`
is **not delivered/passed**; `test_creation_is_blocked_before_partial_mutation`
proves the safer current boundary. Do not report this task complete because its
other tests pass. Runtime P2 and manual normal/crash acceptance both remain pending.

## Exact consent and invocation

Consent file: owned regular single-link mode 0600, no symlink leaf, <=16384 bytes,
strict JSON without duplicates/unknown fields. Template only, not approval:

```json
{"schema":1,"gate":"P2","run_id":"RUN","instance":"INSTANCE","output":"DP-2","recovery_output":"DP-1","allow":["headless-outputs","test-pattern"],"capture":false,"input":false,"sdk":false,"mode_switch":false}
```

`schema` is integer 1; run ID is a Task 2 ASCII token <=80 chars, new per run;
instance must match `[0-9a-f]{40}_[0-9]+_[0-9]+`; exact output/instance match the
command. Presentation and independent physical recovery outputs must differ.
The allow array is exact/ordered; the four false values are booleans. This CLI
cannot authorize capture/P3/P4/P5. File presence records externally obtained
approval, not a substitute for the parent's user confirmation.

Future commands (NOT run against a live compositor during this task):

```sh
python -m tools.native_spike preflight --instance INSTANCE
python -m tools.native_spike run --instance INSTANCE --output DP-2 --consent-file /absolute/private/consent.json
python -m tools.native_spike recover --journal /absolute/private/journal.json
```

`XDG_RUNTIME_DIR` must select the approved user's runtime. Even with valid consent,
run currently exits blocked before mutations/launch. Recover exits 2 without
changing resources/journal. See `docs/feasibility/native-spike/runbook.md` for
remaining acceptance and stop behavior. Do not bypass ownership by manually
launching the renderer or deleting matching names.

## Test execution evidence

Actual RED observations:

- Initial `python -m unittest discover -s tests/native_spike -p test_hyprland.py -v`:
  missing `hyprland` import (one loader error before implementation).
- Initial native syntax check: missing `xr/presentation.hpp` (compile-time RED,
  not a claim that assertions ran).
- First socket fixture exposed AF_UNIX path length overflow under the long
  scratch directory. Fixed fixture and production connection with anchored
  `/proc/self/fd/<directory-fd>/.socket.sock` addressing; actual socket tests pass.
- Regression `test_recovery_cannot_prove_absence_from_empty_snapshot`: `True is
  not false`; fixed empty snapshot fail-closed behavior.
- Regression `test_rotated_output_reserves_transformed_logical_rectangle`:
  `1080 != 1920`; fixed transformed logical rectangle planning.
- Regression native CTest: `version never exceeds pinned XML v4`; fixed cap to
  actual vendored v4 rather than compositor v5.
- Launcher tests initially missing `run_presentation`; then storage-error test
  observed `2 != 1` recovery invocations. Narrowed exception handling so recovery
  storage failure is propagated, never retried as a socket startup failure.

Final actual command results:

```text
$ python -m unittest discover -s tests/native_spike -v
Ran 54 tests in 4.241s
OK
$ python -m unittest discover -s tests/probe -v
Ran 62 tests in 1.662s
OK
$ cmake --build build/native-spike -j4
[100%] Built target xr-native-spike
$ ctest --test-dir build/native-spike --output-on-failure
100% tests passed out of 4
$ build/native-spike/xr-native-spike --help
xr-native-spike: native output-bound layer-shell test pattern (no capture/SDK/input).
...
Managed output ownership qualification is currently BLOCKED. No 120 Hz claim.
```

Fresh Release configure/build in `build/native-spike-task3-release` with hardware
and SDK OFF also generated both protocols, compiled/linked the native executable,
and passed **4/4 CTests**. `git diff --check` passed. Final test logs contain the
expected missing-consent diagnostic emitted by its negative test, not a failure.
No test connects a valid native presentation to any compositor. Presentation
unit checks cover policy-request arguments, version cap, pattern pixels, buffer
size validation and inert error/close behavior. They do **not** prove on-wire
focus behavior or actual configure/render/resize against a compositor.

## Self-review, provenance and dependencies

- Pinned wlr XML is **v4**, not v5; negotiate at most that version. Older v1/v2
  shell teardown uses local proxy destroy because the destructor request begins
  at v3. Kept original XML byte-identical with complete notices. Generated C
  header's `namespace` parameter needs a scoped C++ spelling adaptation.
- XML SHA-256:
  `996b8c66e1c2276c443336d4cb174634e8e5948823779042f244930525b79cc2`.
  xdg-shell descriptor comes from installed wayland-protocols, only to resolve
  layer-shell's popup interface symbols; no xdg-shell/popup requests are used.
- Build found wayland-client 1.26.0, wayland-protocols 1.49 and local scanner;
  GNU C/C++ 16.2.1. No missing packages; no installs or build auto-fetches.
- Editor LSP diagnostics lacked the build-generated include/C++20 context;
  authoritative default and fresh Release compiler builds passed with `-Werror`.
- No atomic compare-and-swap is claimed; recovery is not safe merely because
  a monitor still has the expected name. Same-UID file/process manipulation
  remains within the trust boundary, as in Task 2.
- Deferred Task 2 public IPC whole-frame deadline/two-frame receive minors were
  not changed: public control loop integration is still blocked. New raw IPC
  whole-response deadline is separately implemented/tested.

## Files

Created `tools/native_spike/{hyprland,__main__}.py`,
`tests/native_spike/test_hyprland.py`, `spike/include/xr/presentation.hpp`,
`spike/src/{wayland,presentation,main}.cpp`, `spike/tests/presentation_test.cpp`,
`spike/protocols/{wlr-layer-shell-unstable-v1.xml,PROVENANCE.md}`,
`docs/feasibility/native-spike/{runbook,results}.md`, and this report.
Modified `tools/native_spike/supervisor.py`, `spike/{CMakeLists.txt,README.md,dependencies.md}`.
