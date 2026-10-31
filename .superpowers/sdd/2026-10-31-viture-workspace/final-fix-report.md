# Final-review fix wave

## Outcome and scope

All seven findings I1/I2/M1–M5 are addressed in one consolidated diagnostic/tests/documentation wave based on `abb10c398f7e5817b33849c0a7e523949803b1fb`, exclusively in `/home/cody/Projects/viture-workspace/.worktrees/feasibility`. Initial tracked worktree was clean. Read the complete final review, original milestone-A plan, approved design, native-spike plan, backend decision, pair observations and relevant diagnostic/CLI/test implementation.

**Final canonical suite: 56 tests passed in 1.674s; exit 0. `git diff --check`: exit 0.** The canonical suite was run once at the finish, after focused RED/GREEN and mutation checks. No outstanding failing tests. No native spike, SDK download/loading/terms acceptance, input/device monitoring, live compositor probes, hardware transition, host-service action, installation or configuration change occurred. Synthetic subprocess tests created only their own bounded process trees.

## Finding-to-change mapping

| Finding | Changes | Evidence / disposition |
| --- | --- | --- |
| **I1: initial input synchronization** | Future native plan Task 5 now defines `begin_sync`, per-device `synchronize` and device-qualified `consume`; starts unreliable; requires current held-state queries such as EVIOCGKEY, queued-event reconciliation at SYN_REPORT boundaries, bounded retries and coherent coverage before reliability. Reuses synchronization after dropped events/reopen; keys/buttons held before open remain suppressors, aggregated across devices. Missing/unapproved/new/synthetic sources without equivalent coverage block global reliability. Raw codes/sets remain ephemeral, never exported. | Named future tests cover button/key held before open, release after sync, second-device hold, failed sync, reopen, uncovered sources and stale queued events. Manual acceptance review confirms no head focus before synchronization or during held drag; held keys keep `key_activity` true on gate updates and release starts the inactivity interval. **Plan-only resolution; none of these native tests or input operations was implemented/executed now.** |
| **I2: executable transition checkpoints** | Added reusable parent-owned six-step hardware transition checklist; referenced by Tasks 6/7 and backend-decision B6. Explicit named disconnect acknowledgement → observed USB/output absence → reconnect/next-pair acknowledgement → identity/session/output/mode/permissions/ownership/new-epoch revalidation. Ordinary swaps require verified teardown first; live-loss fault injection requires separate approval and recovery verification before reattachment. Each run ends with a safe-to-disconnect prompt after verified restoration. | Walked Luma → teardown → disconnect confirmation → disappearance → Beast confirmation → revalidation, plus deliberate loss/reconnect, initial/already-connected cases and refusal paths. Missing acknowledgement, unexpected identity and recovery failure stop; declining safe-finish removal ends the run with an inactive attached pair, not permission to swap. **Future plan only; no hardware requests issued.** |
| **M1: oversized refresh integer** | `tools/xr_probe/session.py` converts normalization `OverflowError` into the existing malformed-monitor `ProbeResult('error', None, ...)`. New tests in `test_session.py` and `test_cli.py` inject JSON `10**400` alongside valid output data. | RED: discovery overflow assertion failed; CLI emitted generic collection error instead of discovery category. GREEN: same focused tests pass, null candidates, CLI exit 3 with discovery error, selection/collectors forbidden, no report/output/capabilities produced. |
| **M2: DRM link error fidelity** | `hardware.py` distinguishes `permission_denied`, `missing`, and generic `error` at `gpu_device` field level while preserving null value, sanitized source and top-level successful inventory. | New `test_drm_link_failures_preserve_field_error_category` checks readlink and resolve separately for EIO, permission and missing exceptions; private exception text never escapes. RED: four subcases incorrectly returned unknown; permission cases already passed. GREEN: all six subcases pass. Existing report sanitizer accepts these field statuses. |
| **M3: README status/navigation** | README separates baseline-time limitation from current completed pair enumeration/backend decision; links Luma, connected Beast, unsuccessful initial Beast snapshot, backend decision and future plan. | Compared to actual stored observation text: Luma active 1080p60, separately advertised 1200p120, not 1080p120; Beast active 1080p120, not measured cadence; supplemental tokens were not collected by CLI. Runtime/tracking/performance remain untested/unapproved. Local link targets verified (README five, backend one). Historical reports/observations unchanged. |
| **M4: serialization alias assertion** | `test_commands.py` now mutates both the serialized output list and a nested mode dictionary, then checks `report.session['outputs']` directly as well as the earlier candidate. | Existing production `asdict` path passes. An in-memory shallow-session serialization mutant fails directly on the new report-session assertion. No production serialization change needed. |
| **M5(a): physical USB aliases** | `test_hardware.py` now has two non-interface aliases to the same physical node. Separate interface test uses a distinct interface directory, so interface skipping cannot be satisfied incidentally by resolved-path deduplication. | Real code yields one physical row and skips the distinct interface. In-memory removal of deduplication yields three rows and fails the strengthened assertion. No production USB change needed. |
| **M5(b): descendant cleanup** | `test_commands.py` adds a real synthetic forked descendant inheriting stdout/stderr, covering timeout/overflow with parent alive and parent already exited/reaped. Readiness handshake ensures the exited-parent case actually sets `returncode` before runner cleanup. | All four subcases pass with existing production runner. In-memory replacement of group kill with direct-child kill fails all four; removing the timeout/truncation cleanup condition fails both exited-parent subcases. **No actual production process-cleanup defect reproduced; `commands.py` unchanged.** |

## RED/GREEN and test execution details

### Diagnostic behavior regressions

Ran before either production fix:

```sh
PYTHONPATH=tests/probe python -m unittest -v \
  test_session.SessionTests.test_huge_refresh_returns_error_without_partial_candidates \
  test_cli.CliTests.test_huge_refresh_stops_at_discovery_before_selection_or_report \
  test_hardware.HardwareTests.test_drm_link_failures_preserve_field_error_category
```

- RED: 3 test methods, 6 assertion failures, exit 1. Discovery showed `OverflowError: int too large to convert to float`; CLI was caught by its broad fallback; DRM EIO/missing were unknown for both operations.
- Applied only the discovery exception-boundary and DRM classification changes.
- Same command GREEN: 3 tests, 0 failures, exit 0 (0.003s).

### Strengthened existing-contract tests

```sh
PYTHONPATH=tests/probe python -m unittest -v \
  test_commands.CommandTests.test_inherited_pipe_descendants_die_on_timeout_and_overflow \
  test_commands.CommandTests.test_models_are_frozen_and_serialize_nested_results \
  test_hardware.HardwareTests.test_usb_exact_vendor_and_physical_dedup \
  test_hardware.HardwareTests.test_usb_interface_is_skipped_even_with_distinct_path
```

The first run exposed a **test fixture** race: `/proc/<fixture-pid>/stat` raised `ProcessLookupError` when a killed process disappeared during read, instead of `FileNotFoundError`. Updated the fixture observer to treat both as gone. This was not a runner leak or product change. Rerun: 4 tests passed in 1.047s.

Then ran four one-test in-memory mutation experiments using copied function globals and `inspect.getsource`/`exec`, or a scoped mock of the serializer; no mutated production source was written to disk:

1. Shallow-copy only the serialized report session: **1 expected failure**, directly inspecting `report.session`.
2. Disable physical-path deduplication: **1 expected failure**, actual rows 3 versus expected 1.
3. Replace `os.killpg(child.pid, SIGKILL)` with `child.kill()`: **4 expected subcase failures**, descendants still running.
4. Remove `or status in ('timeout', 'truncated')` from cleanup condition: **2 expected subcase failures**, both parent-already-exited cases.

The mutation harness asserted failures existed and no test errors occurred, then exited 0 with `All four in-memory mutations detected; production files untouched`. These are deliberate negative controls, not outstanding product failures. The full final suite subsequently passed against unmutated production functions.

### Bounded fixture / cleanup guarantees

- Only an injected Python subprocess and its fork are used; no host commands/services are targets.
- Parent and descendant each have a five-second SIGALRM lifetime bound (alarms are explicitly rearmed after fork).
- Readiness polling is bounded; runner deadline is 0.5 seconds and retained output cap 128 bytes. Elapsed time and direct-child reaping are checked.
- Descendant liveness is checked before fallback cleanup; missing/X/Z states are not executing. A killed orphan may remain a zombie until the system reaper runs; that does not retain inherited pipes or mean it is running.
- A `finally` always SIGKILLs the fixture process group, waits for the direct child with a bounded timeout and closes its pipe objects, including assertion/readiness failures and the intentional mutation runs.
- This protects the current inherited-pipe/same-session contract, not arbitrary descendants escaping into new sessions or uninterruptible kernel tasks.

### Final canonical verification

```sh
python -m unittest discover -s tests/probe -v
# Ran 56 tests in 1.674s — OK, exit 0

git diff --check
# no output, exit 0
```

Also verified local Markdown link targets, and `git diff --exit-code` for historical host-report files, pair observations, original approved spec and original milestone-A plan: no changes. No historical snapshots regenerated.

## Self-review and residual boundaries

Reviewed the complete focused diff before the final suite. Changes remain limited to two small diagnostic error-handling paths, strengthened offline tests, current README/decision navigation and future-plan safety contracts. No command allowlist widening, native implementation, schema redesign or availableModes implementation was included. Existing serialization, physical dedup and process-group cleanup were correct under the new tests, so were not rewritten.

I1/I2 are resolved as **planning findings**, not as claims that future runtime safeguards work. P0–P5, SDK/license/privacy/isolation decisions, real input coverage, package qualification, tracking/comfort and measured presentation remain open. Global passive coverage may prove infeasible; the revised plan explicitly keeps automatic head focus disabled rather than treating a nominated subset as global safety. Actual native implementation must prove the synchronization ordering with the named offline tests before any authorized hardware use.

No unresolved finding from this review wave remains. The temporary `/proc` fixture race was corrected and verified. The editor's asynchronous import diagnostic for `tools.xr_probe.session` was not a runtime import failure: the targeted and canonical repository-root commands imported and exercised it successfully; no editor/system configuration was changed.

## Files changed

- `tools/xr_probe/session.py`
- `tools/xr_probe/hardware.py`
- `tests/probe/test_session.py`
- `tests/probe/test_cli.py`
- `tests/probe/test_hardware.py`
- `tests/probe/test_commands.py`
- `README.md`
- `docs/feasibility/backend-decision.md`
- `docs/superpowers/plans/2026-10-31-viture-native-spike.md`
- This new report: `.superpowers/sdd/2026-10-31-viture-workspace/final-fix-report.md`

Commit this wave with command-local `Hermes Agent <hermes@localhost>` identity. No unrelated files or historical reports belong in that commit.
