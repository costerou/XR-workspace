# Task 2 — ownership journal, private IPC, independent recovery

## Outcome and execution evidence

Implemented offline Python control transport, write-ahead journal/recovery, effective
watchdog loop, and real C++ control decoder/queue linked to installed nlohmann_json.
No live compositor/device/SDK actions, package installs, or diagnostic allowlist changes.

Executed from this worktree:

- Initial Python RED: `python -m unittest discover -s tests/native_spike -v` failed
  with three missing `tools.native_spike` imports, as expected before implementation.
- Initial native RED: `c++ -std=c++20 -Ispike/include -fsyntax-only spike/tests/ipc_test.cpp`
  failed on missing `xr/ipc.hpp`. This was a compile-time missing-implementation RED,
  not a claim that CTest assertions ran before the implementation existed.
- Focused regression REDs reproduced repeated SIGKILL, boolean confirmation accepted
  as integer, malformed operation kind raising TypeError, and swallowed recovery fsync
  failures. Fixed and reran GREEN.
- Final `python -m unittest discover -s tests/native_spike -v`: **29 passed**.
- Final `python -m unittest discover -s tests/probe -v`: **62 passed**.
- Bare `python -m unittest discover -v` discovers **zero tests** in this repository;
  it is not a verification substitute. Both explicit test directories above ran.
- `cmake -S spike -B build/native-spike -DXR_ENABLE_IPC=ON -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF`,
  build, and CTest: **2/2 passed**, layout and IPC, compiler warnings treated as errors.
- Fresh Release/default-IPC build in `build/native-spike-task2-release`: **2/2 passed**.
- `pkg-config --modversion nlohmann_json`: **3.12.0**. No substitute JSON library.
- `git diff --check`: clean before report creation.

Tests use fake Hyprland state, actual private Unix sockets, a fake child-process
interface, and one actual synthetic Python child that ignores SIGTERM and sleeps.
The independent watchdog observed the missing heartbeat for at least two seconds,
escalated to SIGKILL, observed exit `-9`, and invoked recovery only after exit.
No renderer, shell command dispatch, hardware, input events, or display was accessed.

## Exact control contract (Task 3 must consume, not broaden)

One UTF-8 JSON object per newline-terminated frame, at most 16384 bytes **including
newline**. Exactly these fields:

```json
{"schema":1,"request_id":1,"run_id":"run","session":"session","op":"status","payload":{}}
```

- `schema`: integer 1 (not boolean/float).
- `request_id`: integer 1 through uint64 max, strictly increasing for the lifetime
  of the run's `ControlQueue`, across connections. Queue rejection does not consume
  the ID. Queue capacity eight; overflow is rejected rather than blocking.
- `run_id`, `session`, and `output_id`: ASCII `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`.
- `status`, `start`, `stop`, `recover`: empty payload only.
- `select`: exactly `{"output_id":"<stable-output-id>"}`. Task 3 must resolve this
  against the approved run's owned outputs; parsing does not authorize any mutation.
- Unknown fields/ops, duplicate keys at any object depth, nonfinite numbers,
  malformed UTF-8, missing newline, oversize frames, and replay/wrong run/session
  are rejected. No path, shell, raw Hyprland command, or arbitrary setting field.
- `start` starts only an already-approved local configuration; it cannot carry
  configuration commands or grant P2/P3/P4/P5 permissions.

Python: `decode_control(bytes) -> dict`, errors are `ValueError`;
`ControlQueue(run_id, session).push(bytes) -> dict`, `.pop() -> dict`.
C++: `decode_control(std::string_view) -> Result<ControlMessage>`, reusing types.hpp;
`ControlMessage` contains schema/request_id/run_id/session, `ControlOp` enum and
`variant<monostate, SelectPayload>`; `SelectPayload` contains `OutputId output_id`.
C++ queue push returns `Status`, pop returns `optional<ControlMessage>`.
Queues are single-owner; caller must serialize access, not share them unsynchronized.

`PrivateServer(runtime: Path, run_id, session)` creates
`runtime/xr-workspace-spike/run_id/control.sock`; directories 0700, socket 0600.
Runtime must already be owned mode 0700. All directory components are opened
no-follow, binding is anchored through the directory FD. Existing sockets are
never silently unlinked. `receive(conn)` checks Linux SO_PEERCRED against current
UID, reads one bounded frame with a 250ms socket timeout, and enqueues it. Caller
accepts connections and consumes the queue in a dedicated control owner, **not the
render thread**. Other frames remain in the socket for subsequent receive calls.
Wrong-UID rejection is exercised against a real socket's credentials with a
mismatched expected UID, not by changing local users. No response/status dispatch
or renderer event loop is implemented in this task.

## Journal and Task 3 adapter contract

`Journal.open(Path, run_id, session) -> Journal`,
`prepare(operation: dict) -> operation_id`,
`confirm(operation_id, observed: dict) -> None`,
`recover(journal, hypr) -> RecoveryResult(complete: bool, remaining: list[str])`.
Remaining entries are operation UUIDs, not output names.

The journal's parent must already be private mode 0700. Journal and lock are
owned, regular, single-link mode 0600 files opened no-follow. Advisory flock
serializes cooperating writers; each transaction reloads durable state to avoid
stale-object lost updates. Atomic replacement follows file fsync and precedes
parent-directory fsync. Records are pending/confirmed/done, with durable intent
written **before** caller mutation. Storage failures propagate; never report
successful recovery after failed journal persistence. Journal size is capped at
1 MiB. Keep the journal on incomplete recovery; do not delete it automatically.

Only these operation dictionaries are supported (no extra keys):

| kind | Fields beyond kind | confirm observation |
|---|---|---|
| setting | `key="input:follow_mouse"`, integer `before`, `applied` in 0..3 | `{"value": applied}` |
| workspace | positive int32 `workspace`, token output names `before`, `applied` | `{"output": applied}` |
| output | canonical UUID string `id`, `name="XR-" + id`, `before=null` | `{"id": id,"name": name,"physical":false}` |

Task 3 must query pre-state, verify output-name absence before preparing creation,
then `prepare`, mutate using its selected-session adapter, read back, and `confirm`.
Do not confirm merely from a command's success response. An unconfirmed operation
may have happened: recovery reconciles live state rather than skipping it.
Prepare only owned-resource mutations, not arbitrary user outputs/workspaces.

`HyprlandClient` protocol lives in journal.py:
`query(kind: str) -> dict | list`, `mutate(command: str) -> str`.
It uses **normalized adapter responses**, not raw Hyprland JSON:

- `query("session") -> {"id": selected_session}`; must reflect actual connected
  instance, not merely echo constructor configuration.
- `query("outputs") -> [{"id": stable_identity, "name": output_name,
  "physical": bool}, ...]`. For owned outputs identity must independently establish
  the journal UUID's binding to the actual output incarnation in this session.
  Never assign ownership from an `XR-` name/prefix alone. If identity cannot be
  proved after a crash, expose ambiguity or raise and preserve the resource.
- `query("workspaces") -> [{"id": positive_int32, "output": output_name}, ...]`.
- `query("settings") -> {"input:follow_mouse": integer}`.
- Fail disconnected/malformed queries explicitly; never turn a failed query into
  an empty output/workspace list. Include disabled/disconnected state as necessary
  to avoid falsely proving absence. `physical=true` must mean a usable, approved
  recovery output, not merely a non-headless connector.

Recovery emits only these internal command strings; adapter returns exactly `ok`
on success and must itself validate/translate commands without a shell:

- `keyword input:follow_mouse <0..3>`
- `dispatch moveworkspacetomonitor <positive-int32> <validated-output-name>`
- `output remove XR-<canonical-UUID>`

Task 3 may implement other internal creation/configuration operations, but they
must not become public wire commands. No changes to tools/xr_probe/commands.py.

Recovery restores settings/workspace placement only if current state still equals
our applied value, preserving observed user changes. Workspace restoration targets
only a currently usable physical prior output. Owned-output removal requires exact
identity, physical fallback, migration readback, no remaining workspace on that
output, and repeated identity/fallback/session checks. Failed migration retains the
output; wrong-session recovery performs no mutation. Missing output with stranded
workspace is incomplete rather than fabricated success. Repeated recovery is
idempotent. Outputs are processed after setting/workspace rollback.

## Independent supervisor integration

`Supervisor(child, recovery_callable, now=None, grace=0.5)` owns exactly one Popen-
compatible child (`poll`, `terminate`, `kill`). `tick(monotonic_seconds)` handles
exit and two-second heartbeat expiry, sends TERM once and KILL once after grace,
waits for observed exit, then invokes recovery. No process-name search, global
kill, process-group signal, or PID-file resurrection. `disconnect(now)` begins the
same shutdown. `heartbeat(now)` cannot revive a stopping child.

`run(heartbeat_socket, interval=0.05)` is an effective selectors loop, intended in
an independent supervisor process that spawned the renderer. Heartbeats are `H`
bytes on a **private inherited socketpair**, not public JSON control/pose traffic.
EOF/invalid heartbeat starts shutdown. Do not expose that FD through public IPC.
Task 3/later launcher must create the socketpair and Popen child and pass
`lambda: recover(journal, selected_session_adapter)`. The run result is the recovery
callable's return value; incomplete recovery must be surfaced to the user, not
silently called safe. SIGTERM/kill of the supervisor itself is not a persistence
service: later startup must explicitly reopen the retained journal for recovery.

## Self-review findings and remaining constraints

- Fixed repeated KILL before reaping, bool/int observation confusion, malformed
  operation validation, and swallowed durability errors with regression RED/GREEN.
- Real Hyprland lacks an atomic compare-and-swap across query/mutate. Re-query and
  readback narrow races but cannot guarantee preservation of a change arriving
  between the final query and mutation. Task 3 must fail closed on event-detected
  identity/user-change ambiguity and document this limitation; do not claim an
  atomic compositor transaction or physical recovery proof from these fake tests.
- Adapter output-incarnation ownership proof is a Task 3 obligation. Never derive
  an owned UUID solely by stripping `XR-` from a live output name.
- A same-UID process is inside the IPC trust boundary; permission checks do not
  defend against malicious peers already able to rewrite the user's private files.
- No pose-worker protocol implementation, runtime launcher, or live adapter here;
  those are later tasks. Public parsing itself never performs a desktop mutation.
- Editor clang diagnostics lacked include/C++20 configuration; actual GNU C++20
  Debug/default and Release builds passed with `-Werror`.

## Files

Created `tools/native_spike/{__init__,ipc,journal,supervisor}.py`,
`tests/native_spike/test_{ipc,journal,recovery}.py`, `spike/include/xr/ipc.hpp`,
`spike/src/ipc.cpp`, `spike/tests/ipc_test.cpp`, and this report.
Modified `spike/CMakeLists.txt`, `spike/README.md`, `spike/dependencies.md`.
Shared types and existing diagnostic implementation/tests are unchanged.
