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
