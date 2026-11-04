# Independent PRO 2 runtime QA

Status: runtime bridge acceptance pending; this is not a replay-only signoff.
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
| Admission | wrong VID/PID/product, duplicate/mixed devices, changed/missing identity, bad hash reject before SDK load or publication | diagnostic baseline covered; actual bridge pending |
| SDK ABI | compiled synthetic exported functions, pointer width, callback signature, family/capability/firmware and nonzero API results | actual bridge pending |
| Lifetime | exactly one owner, errors at every init/open/close/stop/shutdown stage, callbacks racing cleanup, idempotent teardown | actual bridge pending |
| Bounded delivery | queue overflow terminal, no callback IPC or blocking, receipt/stamp clocks valid, no implicit recovery | actual bridge pending |
| Readiness | disabled before required samples; no pose, invalid pose, stale receipt/clock reversal fail closed and disable feed | actual bridge pending |
| Native bridge | actual compiled publisher -> existing reader/PoseState in fresh user+IPC namespace, zero unproven correction, private keys/cleanup | replay baseline only; bridge pending |
| Controller test route | explicit supervised-test authorization; default denial; scoped projection override only; no normal production bypass | actual route pending |
| Isolation | simulated SDK only, fresh user+IPC+network namespace, no host IPC, no compositor/GPU/services or devices | all execution commands explicitly isolated |
| Legacy models | Luma/Beast guards, defaults, saved settings, wire/calibration semantics unchanged | baseline regressions; integrated route pending |

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
