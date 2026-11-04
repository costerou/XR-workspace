# Independent PRO 2 runtime QA

Status: independent offline native/wrapper/controller acceptance GREEN on
`c31cc3180071fca0feee823273d3a713997b2ff8`; RQ1–RQ5 closed.
Final rebuilt package review pending. No hardware/runtime activation performed.
QA branch safely fast-forwarded to evidence head
`1d141f87097cf122a7b035f4324b67c747bfd26a`. No source edits outside QA tests/docs.

User confirms H1 yaw/pitch/roll evidence and normal physical picture during roll.
These are parent-supplied observations. No new hardware test is performed here.
Pinned SDK SHA256 remains
`8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e`,
firmware `30.0.00.002_20260804`. This QA never loads the installed SDK.

## Acceptance matrix

| Boundary | Required independent check | State |
| --- | --- | --- |
| Admission | wrong VID/PID/product, duplicate/mixed devices, changed/missing identity, bad hash reject before SDK load or publication | native identity/hash rejection verified |
| SDK ABI | compiled synthetic exported functions, pointer width, callback signature, family/capability/firmware and nonzero API results | native fixture tests passed on c31cc31 |
| Lifetime | exactly one owner, errors at every init/open/close/stop/shutdown stage, callbacks racing cleanup, idempotent teardown | native fixture tests passed on c31cc31 |
| Bounded delivery | queue overflow terminal, no callback IPC or blocking, receipt/stamp clocks valid, no implicit recovery | native fixture tests passed on c31cc31 |
| Readiness | disabled before required samples; no pose, invalid pose, stale receipt/clock reversal fail closed and disable feed | native fixture tests passed on c31cc31 |
| Native bridge | actual compiled publisher -> existing reader/PoseState in fresh user+IPC namespace, zero unproven correction, private keys/cleanup | actual native worker + independent reader passed |
| Controller test route | explicit supervised-test authorization; default denial; scoped projection override only; no normal production bypass | actual wrapper/controller tests passed |
| Isolation | simulated SDK only, fresh user+IPC+network namespace, no host IPC, no compositor/GPU/services or devices | all execution commands explicitly isolated |
| Legacy models | Luma/Beast guards, defaults, saved settings, wire/calibration semantics unchanged | 83 legacy/managed regressions passed |

## Reviewed baseline limits

`tools/pro2_diagnostic/adapter.py` is expressly replay-only. H1 maps wxyz to
[w,-z,-x,y]; malformed/nonfinite/nonnormalized quaternion, duplicate/backward
uint64 or reversed/stale receipt closes the adapter. Relative timestamp scaling
is an explicit candidate assumption; no new optical correction is justified.
Native replay sets mount pitch 0, unknown FOV, no lookahead and private segments.
It is not SDK readiness or the runtime bridge. PublisherOptions defaults remain
Luma-specific (-8.5 degrees, 52 FOV); the actual worker must override explicitly.

`candidate.py` is a standalone diagnostic and must not be adopted as-is for runtime:
firmware record is emitted rather than gated, queue overflow is counted rather
than terminal, callback may enqueue during cleanup, and no-pose capture timeout
is diagnosed in main rather than by disabling a native feed. These are runtime
requirements for the new owner, not claims that diagnostic capture violates its
narrow existing contract. SDK callbacks must remain retained until safe teardown.

## Commands

```
unshare -Ur -i -n -- python -m unittest discover -s tests/pro2_diagnostic -v
unshare -Ur -i -n -- python -m unittest discover -s tests/pro2_runtime_qa -v
```

Private capture opt-ins are unset. No private raw data is copied or emitted.
Final SHA/results and defect reproductions will be appended after committed runtime
and controller route artifacts arrive; no final runtime SHA exists in this report yet.

Initial independent results on 1d141f8: diagnostic/native suite **29 discovered,
27 passed, 2 skipped** (private capture opt-ins unset), 1.338s; independent
QA boundary suite **5 passed**, 0.001s. These commands ran in fresh user+IPC+network
namespaces. ABI fixture is locally compiled C with no SDK/device dependencies.
Actual runtime acceptance remains pending the lead's committed implementation.

Independent adversarial ABI fixture `tests/pro2_runtime_qa/sdk_fixture.c` compiles
with C11, -Wall -Wextra -Werror and pthreads. It has no vendor dependencies.
Scenarios cover per-API nonzero results, wrong capability/family/firmware/length,
NULL create/callback, callback during close, asynchronous stream/no-pose/stale,
duplicate/reversed clocks, nonfinite/invalid norm and 1024-callback overflow.
Lifecycle trace records SDK calls and flags calls from a non-owner thread. Compilation
alone is not execution evidence; fixture will drive committed native runtime tests.

`python tests/pro2_runtime_qa/run.py` is the independent harness launcher. It removes
private-capture/desktop/preload opt-ins, gives tests a temporary runtime directory,
records host user/IPC/network namespace IDs and launches unittest inside fresh
user+IPC+network namespaces. Native-runtime tests must verify those IDs differ
before loading the synthetic SDK or publishing. It never installs dependencies.

## Actual native bridge audit: 86e20dd

Merged committed bridge head `86e20ddad91eccadb1f0cdadaece7513aca9911a`
into own QA branch. Compiled synthetic bridge using actual bridge.cpp/publish.cpp,
plus independent C ABI fixture and native PoseFeed observer. Dedicated inherited
SDK/status FDs, temporary fake sysfs, private prefix, fresh user/IPC/net namespaces.
No hardware, real SDK or compositor used. Wrapper/controller not yet present at
this SHA, so full-route acceptance remains pending.

Initial command `python tests/pro2_runtime_qa/run.py`: **12 tests passed**, 3.805s.
Native scenarios exercise all init/API/cleanup errors, owner-thread trace, async
callback during close, signal shutdown, 1024-callback overflow, null callback,
clock duplicate/reversal, invalid quaternion/nonfinite, no pose/stale, topology
change, wrong identities/multiple/hash before dynamic load. Native observer sees
explicit provisional FOV44.5, feed enabled then disabled, and no private files
left on teardown. Tightened observer also verifies a nonidentity H1 pose to detect
unproven optical pitch inheritance.

### RQ1 — invalid firmware response length accepted (open)

At bridge.cpp firmware validation, memchr searches the entire zero-initialized
256-byte buffer rather than the returned length. A synthetic SDK writes the
correct complete version but reports length=1. Runtime accepts and emits ready.
Expected fail-closed rejection of inconsistent ABI metadata before publication.

Red reproduction command (fresh namespaces and fake SDK only):

```
python tests/pro2_runtime_qa/run.py discover -s tests/pro2_runtime_qa -v
```

Result after adding firmware_short_length: **12 tests run, 1 failing subcase**
(4.062s). `test_sdk_errors_and_cleanup_continue` expected nonzero, observed exit0,
ready and done ready=true/cleanup_errors0. Exact version alone must not justify
reading bytes outside declared response scope. Lead notified promptly to fix;
QA does not edit runtime. Full output: qa-evidence/runtime/native-rq1-red.txt.

Expanded executable tests additionally cover missing cleanup ABI symbol rejection
before create, only two callbacks never ready/zero published samples, and the full
3-second no-pose startup deadline with cleanup. Production compile is separately
checked to reject synthetic fixture flags before dynamic loading. These are
bounded unit cases, not a mandatory soak or new live test.

Expanded suite result on unchanged committed86e20dd: **16 tests run, 1 failure**
(known RQ1 firmware_short_length), 8.388s. Missing cleanup symbol, two-sample
readiness, production flag rejection and full3-second timeout checks passed.
The lead reports a fix in progress; uncommitted fixes are not treated as closed.

## Actual wrapper/controller audit: fb68ace

Merged actual runtime+controller commit
`fb68acea1e6f4460cbd39e2791d121604b580496`. **RQ1 CLOSED**: exact firmware
response length reproduction now rejects. Whole independent suite initially
**20 passed in11.851s**. This includes real RuntimeSession launching compiled
synthetic native bridge via inherited pinned SDK/status FDs, current health
heartbeat/nonce validation, and actual pro2_test.execute using that wrapper with
only an injected fake compositor client/managed-run function. Inert hash-checked
renderer/plugin fixture bytes are never loaded/executed. Assertions check explicit
Pro2 consent/model/profile, mode_switch=False/SBS=None, fake client unload, private
600 log, native private-file removal, stale/wrong-nonce/future health denial.

Synthetic injection changes only SDK path/digest/inventory/competing fixture and
adds fixture root/hash args to the actual wrapper Popen command; real wrapper
lifetime/status monitor/native owner run unchanged in fresh namespaces. No live
SDK/client/compositor/renderer/GPU call occurs. Native reader H1/nonzero-pose and
projection/teardown check is separate from mocked managed compositor work.

### RQ2 — ready+done batch falsely returned as ready (open)

RuntimeSession.start polls status, then returns on self.ready without checking
self.done, child liveness or failure. Synthetic SDK sends three valid samples and
one nonfinite sample synchronously during open. Worker emits ready then done with
error=nonfinite and exits. Both arrive in one status read; wrapper start returns
success and writes healthy. Repro:
`test_terminal_event_in_same_batch_cannot_return_ready_session`.
Expected reject terminal session before success/any compositor operations.

### RQ3 — pinned SDK descriptor leaked after executable hash rejection (open)

After RuntimeSession constructor verifies bundle, modify fake executable bytes.
start pins synthetic SDK descriptor, executable hash fails, SDK FD never closed.
`test_artifact_rejection_closes_already_pinned_sdk_fd` snapshots live descriptors
(excluding transient proc-list descriptor), asserts unchanged, and reclaims any
leak owned by this reproduction. Expected acquisition cleanup on every failure,
including Popen failure; no actual SDK loaded in this case.

Commands/results on fb68ace:

```
python tests/pro2_runtime_qa/run.py
# 22 tests, 2 failures (RQ2/RQ3), 12.534s
python tests/pro2_runtime_qa/run.py discover -s tests/pro2_runtime_qa -p test_controller_route.py -v
# 6 tests, same 2 failures, 3.580s (descriptor snapshot tightened)
```

Lead notified immediately; fixes reported in worktree, awaiting committed SHA.
Red evidence stored in qa-evidence/runtime/wrapper-rq2-rq3-red.txt. Full route is
not cleared until these retest green; no hardware activation approval inferred.

## Recovery audit: be41233

Merged committed `be41233` launcher fixes and package builder. RQ2 and RQ3
independent reproductions now pass. Full23-case suite at this point has one new
recovery failure (11.827s); adding plugin recovery assertion yields focused route
suite **8 tests, 2 failures** (3.142s). No runtime edits by QA.

### RQ4 — Pro2 recovery still invokes nested legacy SBS restoration (open)

`recover_pro2_test_managed` calls `_recover_managed_impl(pro2_test=True)`, which
skips only its outer immersive.restore_2d call. It then calls recover_restoring,
which unconditionally invokes immersive.restore_2d(ButtonSbs) for saved output.
The normal `_run_owned` finally block also calls that same recovery function.
Thus dedicated Pro2 teardown/recovery can enter the legacy R1 prompt path if the
monitor is wide. Pro2 requires HOLD L1 and SBS disabled; no legacy SBS handling
should run. Reproduction calls actual recover_pro2_test_managed and actual nested
recover_restoring, with fake client/journal/broker/recovery and spies on all
external operations. `test_pro2_recovery_never_enters_legacy_sbs_restore` observes
one restore_2d call where zero is required. Fix should propagate the Pro2 no-SBS
scope into both normal teardown and explicit recovery; preserve other models.

### RQ5 — successful explicit recovery leaves retained plugin loaded (open)

Controller execute(recover=True) returns recover_pro2_test_managed result directly.
It never unloads the pinned bundle plugin that incomplete run intentionally retained.
A successful recover therefore cannot return to a clean next-start state: the
next start refuses an already-present ownership plugin. Reproduction uses actual
controller recover branch and validated fixture manifest, fake client and a
successful managed recovery return. `test_successful_explicit_recovery_unloads_retained_bundle_plugin`
expects one pinned bundle unload and observes zero. No provider/session starts.
Incomplete recovery must retain ownership; successful recovery must unload and
truthfully surface unload failures.

```
python tests/pro2_runtime_qa/run.py discover -s tests/pro2_runtime_qa -p test_controller_route.py -v
# 8 tests, 2 failures RQ4/RQ5; RQ2/RQ3 pass
```

Lead notified promptly. Evidence: qa-evidence/runtime/recovery-rq4-rq5-red.txt.

## Independent final functional recheck: c31cc31

Exact committed functional head:
`c31cc3180071fca0feee823273d3a713997b2ff8` (read from integration branch).
Merged committed fixes into own QA branch before execution. **RQ1–RQ5 CLOSED**.
RQ4 no-SBS scope propagates into explicit recovery and normal teardown;
RQ5 successful recovery confirms ownership presence and unloads pinned bundle plugin.
Legacy default restoration remains enabled for other models.

Legitimate fixture updates: successful fake managed run now includes
renderer_returncode=0 as real managed results do; explicit recovery fake client
reports exact ownership plugin present. Missing/nonzero renderer code must not
claim success. No production checks were weakened.

Independent final commands/results:

```
python tests/pro2_runtime_qa/run.py
# 28 tests passed, 13.612s
python tests/pro2_runtime/run.py
# 6 tests passed, 4.115s; actual native PoseState pipeline
PYTHONPATH=.:tests/native_spike unshare -Ur -i -n -- python -m unittest tests.pro2.test_managed_boundary tests.xr_controller.test_pro2_platform tests.native_spike.test_panel_consent tests.native_spike.test_profiles tests.native_spike.test_managed tests.native_spike.test_recovery -q
# 83 tests passed, 2.335s
```

The 28-case independent suite now also verifies failed Popen acquisition cleanup,
SDK stdout cannot spoof ready on dedicated status FD (fixture text stays private),
NoDeviceSbs enter denial/reassert/switching/close and VideoControl.close never create
button adapters, and actual supervisor argv includes only the explicit private
prefix/projection on test scope while legacy argv remains unchanged. Existing
normal managed Pro2 start/recovery still reject before client calls. Neither
Luma/Beast defaults nor profiles are silently migrated by this test route.

All commands use fresh user/IPC/network namespaces for any SDK fixture/native IPC.
The real SDK is never loaded; renderer/plugin test bytes in the independent
controller test are inert and hash-checked. Compositor run is injected; real native
reader/PoseState pipeline is CPU-only. No live compositor/GPU/mode/USB/controller
or service operation occurred. Readiness is transport readiness, not field
calibration. H1 and ns-scale remain pinned candidate scope; optical projection
is explicitly provisional and consent-bound, no -8.5 correction or automatic46
fallback. Snapshot topology polling does not atomically confine future SDK
hotplug; exclusive bounded test topology remains a condition for parent approval.
Opaque SDK hangs still rely on wrapper TERM/kill deadlines; these tests do not
claim graceful cleanup of every proprietary hang or hardware firmware failure.

Package check pending: current `build/pro2-runtime-review/manifest.json` observed
source_commit=be41233, predating RQ4/RQ5. Lead notified to rebuild after fixes/QA
merge. That older bundle is **not** the final accepted artifact. Do not install or
activate it on the strength of code/test acceptance above. Fresh bundle hashes,
source pin and provenance must be verified before final artifact handoff.
