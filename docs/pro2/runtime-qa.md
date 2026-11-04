# Independent PRO 2 runtime QA

Latest artifact: corrected source `910cabc198e14b5e18910ebb5005bf0c9b74127a`
independently audited with307 manifest file hashes and a fresh extraction.
Packaged --preflight and actual packaged BundleClient request-spy dispatch pass.
No SDK/client/hardware activation; old92 bundle is superseded and cannot retry.

Latest: RQ6 release-path blocker CLOSED on actual integrated head
`aa73af455c0790b9943fe982e7690aa83c36e03d`:9 independent path cases
and4 lead path cases pass; no SDK/client/hardware/retry or private-log access.
Historical native/wrapper/controller and bundle acceptance was GREEN on
`92f01a712da4403a77f6d58b74c842549a4744f8`. RQ1–RQ5 closed;
117 final tests passed and 287 bundle file hashes verified. No SDK/hardware/GPU
or live compositor activation performed. Earlier pending/open states below are
audit history, superseded by the final recheck at the end.
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

## Final frozen artifact recheck: 92f01a7 — PASS

Final functional and bundle source SHA:
`92f01a712da4403a77f6d58b74c842549a4744f8`. Integration HEAD read before
and after checks. QA branch incorporated this exact committed code before testing.
Changes since c31 inspected: native SDK/status descriptor type/distinctness checks,
sanitation of identity filesystem error status, private process metadata, SIGTERM
interrupt path and explicit provisional projection cache invalidation/status label.
No runtime code authored by QA.

Fresh final results (exit0 for each):

* `python tests/pro2_runtime_qa/run.py`: **28 passed**, 13.021s.
* `python tests/pro2_runtime/run.py`: **6 passed**, 3.929s.
* The exact83-case namespaced legacy command above: **83 passed**, 2.319s.

**117 final tests passed** on the actual runtime/controller source, not just the
replay adapter. No current QA blocker remains from this scope. RQ1–RQ5 red logs
and green regressions remain inspectable. Rendering/GPU/physical comfort and
installation are explicitly outside these results; native reader/PoseState
fixtures and mock compositor orchestration do not prove live XR acceptance.

Read-only actual final bundle audit command:

```
python tests/pro2_runtime_qa/audit_bundle.py /home/cody/.t3/worktrees/viture-workspace/pro2-integrate/build/pro2-runtime-ready --source 92f01a7
/home/cody/.t3/worktrees/viture-workspace/pro2-integrate/build/pro2-runtime-ready/bin/pro2-supervised-test --help
```

**PASS:287 manifest file hashes**. Packaged bridge/launcher/controller/managed/
supervisor sources exactly match git at92f01a7. Bridge and renderer are ELF;
production bridge contains neither --fixture-root nor --fixture-hash; renderer
contains both explicit test-route flags. No tests, synthetic .so or vendor
libglasses.so ships in the manifest. Bundle help exits0 before consent/execution,
with no SDK load or client construction. Hash audit never executes binaries or
loads libraries. Help executes only the Python argument-parser path.

Manifest SHA256:
`38d0b7edc2939b990065a6bbab1a5d1429a00c7944d0f83ccd5cf1df43bc33d3`.
Production bridge SHA256:
`359bf8c807b5aef1fcc1ea63bee930ad41f446d88dc7c0328cda2b67d3086a9d`.
Renderer SHA256:
`c4af50d0667362f56b71168c8eb7f157b952c78f34452b9a5f4b968a42d07334`.
Ownership plugin SHA256:
`45ade12a6af7c80d695d33f8d334ca517b135174e661c7283e786a98b04db7f9`.

This accepts the frozen uninstalled review artifact for parent review under the
explicit supervised route. It does not authorize installation or SDK/hardware
activation. Next physical gate remains separate parent consent and the bounded
supervised XR/window recovery/comfort check described in runtime-runbook.md.
No extra hardware axis session, forced unplug or mandatory long soak is required
by this QA work. Normal Pro2 production guard stays closed; HOLD L1 and SBS
disabled, independent per-model settings, and no guessed optical correction remain
in effect. Existing runtime hotplug/opaque SDK limits above remain truthful.

## Parent first-attempt path blocker: RQ6 (reopened acceptance scope)

Parent reports first XR attempt rejected before plugin load. This is parent-supplied
incident evidence, not hardware validation or an instruction to retry. No private
SDK logs inspected. Baseline for independent reproduction:
`614667ed6cd9adba5fa06b149e485a3656e89574`.

BundleClient._plugin_command incorrectly applies the normal installed plugin
regex requiring `/viture-workspace/bin/<12hex>/libxr-ownership.so` to the dedicated
review bundle layout. Hyphens are accepted by that regex; the layout is the issue.
Exact parent root:
`/home/cody/.hermes/profiles/coderbotxr/cache/scratch/pro2-release-review-z3nsa4vu/pro2-runtime-release/runtime`.

Independent reproduction uses actual BundleClient.__new__, actual plugin_load/
unload dispatch and a request spy. Exact parent location uses inert mocked pinned
FD (no parent file opened/changed); realistic QA-owned temporary release layout
uses real inert-file hash checking. Both reject before request. Unsafe-space
bundle also reaches injected client before path preflight.

```
python tests/pro2_runtime_qa/run.py discover -s tests/pro2_runtime_qa -p test_bundle_plugin_path.py -v
# baseline:6 tests, 3 pass / 2 errors / 1 failure, 0.006s
```

Additional owned cases assert wrong suffix/verb/traversal/newline, changed hash,
symlink plugin, symlink root/bin are denied before request; normal PLUGIN_PATH
pattern and installed-layout acceptance remain unchanged. Lead owns dedicated
canonical ASCII bundle/path/hash preflight fix; QA changes tests/report only.
No SDK, compositor client, plugin or controller is actually started or contacted.
No hardware/retry/install, no private logs read. RQ6 remains open until committed
fix is independently retested. Historical92f01a7 acceptance did not cover this
real BundleClient path dispatch; injected compositor clients bypassed that check.

## RQ6 independently CLOSED — actual integrated aa73af4

Fix source commit `42416e7` merged into own QA branch. Rechecked actual integration
worktree with HEAD read immediately before and after execution:
`aa73af455c0790b9943fe982e7690aa83c36e03d` both times.

Actual integration-worktree command (external QA-owned test file, no sibling edits):

```
unshare -Ur -i -n -- python -m unittest discover -s /home/cody/.t3/worktrees/viture-workspace/pro2-qa/tests/pro2_runtime_qa -p test_bundle_plugin_path.py -v
# 9 tests passed, 0.007s
```

Also executed from own QA branch:

```
python tests/pro2_runtime_qa/run.py discover -s tests/pro2_runtime_qa -p test_bundle_plugin_path.py -v
# 9 tests passed, 0.007s
PYTHONPATH=.:tests/native_spike unshare -Ur -i -n -- python -m unittest discover -s tests/pro2_runtime -p test_bundle_path.py -v
# 4 lead tests passed, 0.006s
```

Actual BundleClient plugin_load/unload methods now permit the exact parent release
layout with only `_request` mocked. Parent-location pin operation uses duplicated
inert temporary FD: no parent plugin/SDK content opened. Separate QA-owned bundle
uses real pinned_fd digest check on inert plugin bytes, dispatches expected load/
unload request strings to spy, and rejects mutated bytes before request. Both
root/bin/plugin symlinks and wrong verb/suffix/traversal/newline reject. Normal
installed PLUGIN_PATH pattern is identical; old installed-layout acceptance and
review-layout exclusion under that normal rule are unchanged.

`verify_manifest` now runs dedicated canonical ASCII location and exact plugin
manifest/digest checks before any client/SDK factory. Packager rejects an unsafe
location before compiler call. CLI `--preflight` validates own inert bundle with
no consent/state arguments, no read_private_json, BundleClient or RuntimeSession
calls, and reports sdk_started=False/compositor_contacted=False. No plugin really
loaded or unloaded: request spies only. No SDK library (real or synthetic) loaded
in this path recheck, no hardware, renderer/GPU, services, installation or XR retry.

This closes the reproduced path/dispatch blocker, not the parent's physical XR
acceptance. The earlier frozen92f01a7 artifact pin is historical and must not be
mistaken for validation of a rebuilt path-fix release. Parent/lead must retain the
new release's own source/hash provenance and use its no-SDK `--preflight` before
any separately authorized retry. Private SDK logs were not read.

## Corrected frozen artifact910cabc — independent PASS

Parent-provided frozen source:
`910cabc198e14b5e18910ebb5005bf0c9b74127a`, read from actual integration HEAD
before and after audit. Safely fast-forwarded own branch to the same commit.
Corrected bundle:
`/home/cody/.t3/worktrees/viture-workspace/pro2-integrate/build/pro2-runtime-pathfix-ready`.

Exact independent command (exit0):

```
python tests/pro2_runtime_qa/audit_extracted_bundle.py /home/cody/.t3/worktrees/viture-workspace/pro2-integrate/build/pro2-runtime-pathfix-ready --source 910cabc
```

Read-only original bundle audit verifies **307 manifest file hashes**, SDK/firmware
pin, source_commit, key packaged source files versus git910cabc, native ELF route
flags and no synthetic fixture flags in production bridge. No vendor SDK or test
fixture .so appears in manifest. Manifest SHA256:
`795a709543abb5cf5a3583185c66088e0496fe24faf3c2f3863d59241a2b8783`.
Bridge/renderer/plugin hashes are unchanged from the92 native artifact hashes
recorded above; the corrected Python source/path validation is now bundled and
independently matched to910cabc. Old92 bundle remains superseded, not retryable.

Independent harness creates an archive from the reviewed bundle and extracts it
to a new private temporary `unpacked/pro2-runtime-release/runtime` tree. Re-audit
confirms the same307 manifest hashes and source pin after extraction. It runs the
**packaged** bin/pro2-supervised-test --preflight there in new user/IPC/network
namespaces: reports plugin_command_path=validated, source910cabc,
files_verified307, sdk_started=False, compositor_contacted=False.
No consent or state directory is supplied or needed.

A separate isolated Python -I process imports only the extracted packaged tools
(asserts module paths belong to the extraction). It constructs BundleClient with
__new__ and supplies a request spy. Actual packaged plugin_load and plugin_unload
validate the real extracted plugin file/hash and emit exactly two expected request
strings to the spy. No plugin is loaded or unloaded. ctypes.CDLL and real
HyprlandClient.__init__ are patched to fail on any call and assert zero calls;
thus no SDK/library is loaded and no real client constructed/contacted. This
checks the dispatch implementation missed by the former injected-client tests.
Artifact hash/source audit is repeated after the packaged operations and still
matches. All temporary extraction/spy state is removed by the QA harness.

Evidence: qa-evidence/runtime/pathfix-final-extracted-audit.json. It records
operational source/hash/count/spy results only, no private SDK output. This turn
performs path/artifact audits only; parent reports37 full-QA and10 pipeline tests
green on frozen source, but those counts are parent evidence, not a fresh QA-run
SDK/native-pipeline claim for this artifact audit.

**Disposition:** no remaining artifact-readiness blocker found for corrected
910cabc release within this offline scope. RQ6 is closed by independent real
packaged dispatch and preflight. This is not a hardware fix/acceptance claim or
retry authorization: installation/staging and supervised physical XR/window/
comfort acceptance remain explicit parent actions. No hardware, SDK session,
controller/unit/service change, GPU, live IPC/compositor or private SDK log access
occurred. Preserve normal production guards, exact identity, SBS disabled and
provisional projection limits. Parent must use the corrected source/hash artifact
and its preflight, never the failed92 bundle.
