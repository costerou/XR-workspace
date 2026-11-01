# Managed CLI/supervisor integration — offline report

## Outcome and scope

Implemented the missing managed test-pattern orchestration in `tools/native_spike`,
including durable restart authority, explicit recovery consent/instance binding,
lifetime exclusion and an active broker guard. **Offline integration passed;
production end-to-end compositor/renderer acceptance has NOT passed.**

Base: `dedab60292fe3fee9468356ae152607ee063cd9b` on `feat/native-spike`.
The approved native plan Task 3B and its lifecycle-broker amendment, the design's
narrow broker amendment, `managed-output-next.md`, the broker report and the latest
ordinary-window/controller-crash reports authorize this bounded implementation.
No new compositor operation, privileged authority, raw allowlist entry, renderer,
capture, input-device or SDK path was introduced. “Production” here means the
actual spike CLI/supervisor modules, not approval to build/release the full product.

The previous isolated controller trial retained an external epoch/fallback witness;
its success did not prove the production CLI could reopen independently. This
change records that witness in the real journal before any create request.

No VM was booted, plugin loaded, host compositor queried/mutated, device opened,
or capture/input/SDK action performed. No subagents were used. Parent live evidence,
previous reports, parent ledger, scratch trial directories and the reviewed module
were not edited. The native sources are unchanged. No physical runtime is started
or enabled automatically; `run` remains an explicit command requiring exact P2
consent and a separately loaded broker. Runtime authorization/qualification is
still required before invoking it on a real compositor.

## Exact changed contracts

### Durable journal

- Schema remains integer **1**. A new optional top-level `broker` object has exactly
  `instance`, `output`, `recovery_output`, `epoch`, `fallback`. Every value is a
  validated token; instance uses the existing exact signature grammar and the
  presentation/recovery names must differ. Existing run/session identity and
  pending/confirmed/done record semantics are unchanged.
- `Journal.bind_broker(context)` uses the existing file fsync → atomic replace →
  directory fsync transaction. It cannot replace an existing context or attach new
  authority to legacy output intents. An identical context is an idempotent no-op.
- `Journal.open(..., existing_only=True)` refuses a missing recovery journal rather
  than creating a success-looking empty one. Safe lock metadata can still be made.
- `managed_owner(path)` holds a separate owned, single-link, mode-0600 no-follow
  `.owner` file with nonblocking exclusive flock across the full managed lifecycle.
  Short journal transactions retain their existing lock. The owner file is not
  unlinked; process exit releases ownership. Spawned children do not inherit it.
- Legacy four-field journals still load for existing low-level APIs, but the new
  managed restart path refuses absent authority. Older software rejects the new
  journal shape: do not downgrade controllers while a managed run is pending.
  Runtime-directory persistence is controller-crash persistence, **not reboot or
  power-loss durability**.

### Creation, supervision and cleanup

`run --instance INSTANCE --output OUTPUT --consent-file FILE` retains the exact P2
schema and both enabled, DPMS-on, nonmirrored physical connector requirements.
Missing consent is rejected before client construction. Pair validation and broker
hello precede journal creation. The normal journal path is:

`$XDG_RUNTIME_DIR/xr-workspace-spike/RUN/journal.json`

`managed.run_managed` plans the existing three 1920x1080/120000-mHz outputs, acquires
lifetime ownership, fsyncs the broker context, and calls the existing
`create_outputs` path: each pending intent is durable before mutation; independent
registry inspection and exact geometry/workspace checks precede confirmation.
A previously bound/nonempty run is recovery-only, including a fully done journal.
No name/monitor-ID adoption, create replay or deletion fallback was added.

After creation, `ManagedGuard` checks the physical pair and all exact owned UUIDs
against their planned specifications. The shared launcher receives this guard and
runs it in the existing spawn-only watchdog worker. Its copied broker remains
pinned to the original epoch/fallback. Authority loss, output invalidation, mirror
changes, a failed worker or guard deadline expiry stop the renderer. The existing
2-second heartbeat expiry and child reaping do not wait on broker requests. The
existing 4.25-second guard deadline is **not widened** for the additional checks;
a slow healthy compositor may conservatively stop this experiment.

The launcher still transfers only the private heartbeat and peer-validated Wayland
FDs, closes the parent's Wayland reference, and reaps its child/worker. The managed
wrapper then invokes recovery exactly once. Partial creation, connection and spawn
failures also attempt handle-addressed cleanup; authority loss retains unresolved
intents, and journal-storage failure is fatal rather than retried. Settings and
raw workspace mutations remain disabled in the broker. The presentation is the
existing static generated pattern, **not a view of the three desktops**.

Normal `run` returns JSON with `journal`, `renderer_returncode`, `complete`, and
`remaining`. CLI success requires renderer exit zero and complete recovery; that
exit status is not visual, performance or end-to-end qualification.

### Restart recovery

The filename-only CLI refusal is replaced by an explicit workflow:

```sh
python -m tools.native_spike recover --journal FILE --instance INSTANCE \
  --output OUTPUT --consent-file FILE
```

Use separate actual paths for the journal and consent. Consent must identify the
same run and saved instance/presentation/recovery pair. No session scanning or
filename inference is performed. The CLI revalidates the two-output prerequisite,
opens only an existing journal, obtains lifetime ownership and checks the verified
compositor session against the journal.

`OwnershipBrokerClient.from_journal(transport, journal)` restores the exact saved
epoch and fallback handle. It sends **no new hello** and cannot acquire replacement
authority. Registry inspections/removals use those saved tokens; lost epochs,
unknown UUIDs and failed reachability preserve unresolved records. Only the
broker's verified tombstone plus separate readback permits `done`. Repeated
recovery issues no extra removals. Restart recovery does not recreate outputs,
resume a renderer or load a plugin.

The manual CLI conservatively requires both approved physical outputs. Automatic
teardown still uses the saved fallback transaction if presentation disappears.
If the presentation output remains absent after a controller crash, manual CLI
recovery is blocked rather than silently weakening the two-output policy.

## RED / GREEN and execution

All new behavior was introduced through sequential failing-test slices using:

```sh
python -m unittest discover -s tests/native_spike -p test_managed.py -v
```

Observed RED failures, followed by GREEN after their corresponding implementation:

1. `missing durable broker binding` — no journal restart context.
2. `missing pinned restart` — no restoration from saved authority.
3. `2 != 0 : production managed CLI still blocked` — public run used the refusing
   raw client rather than broker-backed orchestration.
4. `recover CLI has no explicit consent/instance workflow` — argparse rejected the
   new explicit recovery fields.
5. `no active broker authority watchdog` — no managed guard.
6. `no lifetime exclusion` — no owner lock across creation/run/recovery.

Additional controls cover partial creation, connection/spawn errors, context
storage failure, absent/malformed legacy context, scope mismatch, missing journal,
missing broker/physical pair, no second controller, and real spawned watchdog/child
teardown on epoch loss. The real-child case checks reaping, worker absence and
unchanged unresolved journal bytes; its compositor transport is synthetic.

Final explicit suites:

- Native Python: **102 tests passed**, 16.425 seconds.
- Probe Python: **62 tests passed**, 1.663 seconds.
- Fresh Release CMake build: actual renderer, ownership module and tests built.
- CTest: **11/11 passed** (ownership, native lifecycle cases, offset, layout, IPC,
  metrics, presentation and help).
- A separate Python creation → fresh journal reopen → pinned recovery → repeated
  recovery check passed **16 generated requests through the freshly built C++
  validator**. It used synthetic responses and real private journal files, not a
  native compositor.
- CLI help and recovery help ran offline. `git diff --check` passed.

Commands actually exercised, from the repository root:

```sh
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/probe -v
cmake -S spike -B build/native-spike-managed-integration \
  -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON \
  -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike-managed-integration -j2
ctest --test-dir build/native-spike-managed-integration --output-on-failure
PYTHONPATH=. python /home/cody/.hermes/cache/scratch/managed-integration-wire-check.py
python -m tools.native_spike --help
python -m tools.native_spike recover --help
git diff --check
```

Evidence logs: `/home/cody/.hermes/cache/scratch/managed-integration-native-final2.log`
and `managed-integration-probe-final2.log`; prior failed/full-suite logs were kept.
A fresh build directory avoided touching reviewed binaries. New compiled module
SHA-256: `a6adede6834e035c6d29f381b981c28f178c2a3a25b02008a10fc740ed6cfdb1`.
The preserved `build/native-spike-offset-fix/libxr-ownership.so` still hashes to
`2be43536ebd358f94851ec927857a7305bbef20a880427fcfef4b19afc168e31`.
The new build was not loaded and does not inherit runtime qualification by hash.
The launcher still resolves `build/native-spike/xr-native-spike`; no benchmark or
claim about that binary's live performance is made by building a separate tree.

Issues encountered: an initial test fixture omitted the raw refusing method;
that setup error was corrected before counting behavioral RED. The first full
suite exposed a presentation-only test patching the removed CLI `Journal` import;
it now patches the real journal module and retains the no-journal assertion.
An attempted validator probe in the tool kernel collided with its `tools` package;
it was rerun successfully in a separate project Python process. Editor Pyright
import/dynamic-fixture diagnostics are not the actual passing Python execution.

## Next qualification — runnable, not already accepted

The offline commands above are immediately repeatable and do not need hardware.
For the next **separately authorized isolated runtime** run, use the production
commands rather than another scratch controller:

```sh
# Only inside the reviewed disposable guest, after its prerequisite checks,
# exact module review/load, and fresh guest-scoped P2 consent:
python -m tools.native_spike run --instance "$INSTANCE" --output "$OUTPUT" \
  --consent-file "$CONSENT"
# Following an explicitly approved controller-only crash, with same-run consent:
python -m tools.native_spike recover --journal "$JOURNAL" --instance "$INSTANCE" \
  --output "$OUTPUT" --consent-file "$CONSENT"
```

**Environment prerequisite not solved here:** the prior single-`Virtual-1` virtio
fixture cannot qualify this public CLI. It lacks two independently usable outputs
accepted by the unchanged production physical-connector guard. Do not rename a
fixture, patch out that guard or infer authorization from its DRM backing. A
reviewed compatible two-output guest topology is needed; otherwise return this
specific qualification blocker to the parent. This task did not boot a guest or
invent that topology. Stage the intended renderer at the launcher's actual fixed
path and verify its build/hash, not merely this separate offline build.

Required next observations: actual CLI-created confirmed context/intents; exact
output/workspace geometry after deferred turns; generated renderer on only the
approved presentation output; three ordinary test windows; normal and
renderer-crash teardown; controller-only kill and **production recovery CLI in a
fresh process without an external authority witness**; original window identity
and reachability; repeated recovery; renderer/worker/process and surface cleanup;
unchanged independent desktop geometry. Then qualify active epoch loss and
partial-create/reply-loss paths. Never turn the existing scratch-controller
success into acceptance of these new paths.

Still open: native adversarial replacement/rule/fallback/special-pinned/stranded
window gates, broker unload/reload and compositor restart behavior, complete
production-supervisor crash semantics, physical P2/visual routing/restoration,
capture/GPU, input safeguards, SDK/tracking, both-pair comfort and performance.
No remaining gate is marked passed by this change.

## Review and rulings

Self-review only (subagents prohibited): checked changed contracts, errors before
and after creation, no authority-refresh path, owner lock scope, worker teardown,
FD lifetime and legacy-journal behavior against the approved plan. A fresh
independent review remains advisable before runtime qualification.

Rulings: implement the already approved Task 3B plumbing, not a new architecture;
keep additive schema-1 broker context and fail closed on legacy/mixed-version
recovery; keep manual two-output checks and the conservative guard deadline.
Costs: older controllers cannot read new bound journals, presentation-loss manual
recovery may remain blocked, and slow healthy validation can stop a run. None of
these costs grants weaker ownership or runtime authority.

Files: new `tools/native_spike/managed.py`, `tests/native_spike/test_managed.py`,
this report; modified CLI, journal, broker client, presentation-only test patch
location and runbook. A short reusable restart-authority lesson was also added to
the default `native-desktop-integration` skill outside the repository. Parent live
files remain untracked and untouched.
