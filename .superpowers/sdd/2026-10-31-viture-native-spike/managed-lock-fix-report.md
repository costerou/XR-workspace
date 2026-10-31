# Managed lifetime-lock correction — offline qualification

## Outcome

Fixed the copied-journal lifetime-lock bypass on base
`791c85aec0b27bb35f9de897ac20308f4d46b1b5` using a canonical recovery-path restriction.
The production correction is confined to `tools/native_spike/managed.py`.
No journal schema, lock implementation, broker protocol, connector classification,
consent, renderer, watchdog or native source changed.

## Root cause and chosen contract

`recover_managed` previously passed the supplied filename directly to both
`managed_owner` and `Journal.open`. `managed_owner` selects `<filename>.owner`.
A private byte-for-byte copy therefore acquired a different lifetime lock, issued
handle-addressed removals while the original controller held `journal.json.owner`,
and wrote done states only into the copy. The canonical records remained confirmed.
The short transaction lock does not solve this identity divergence.

The smallest safe correction is to accept only:

`CLIENT_RUNTIME_DIR/xr-workspace-spike/CONSENT_RUN_ID/journal.json`

The real client obtains its absolute runtime directory from the same CLI runtime
used by managed creation. The run ID is token-validated, not inferred from the
supplied filename. Before session/display queries, owner-lock creation or broker
requests, recovery compares the supplied absolute lexical path with that canonical
path and refuses a mismatch with `OwnershipBlocked`. All subsequent locking and
journal writes use the canonical path itself.

- Renamed copies, copies named `journal.json` in other directories, file/directory
  symlink aliases and `..` spellings are refused. Copies remain refused after the
  owner exits; there is no copy import, authority merge or recovery writeback to a
  second file, so accepted copies cannot leave the canonical journal stale.
- Harmless lexical `./` spelling collapses to the same canonical path and lock.
  Relative paths are accepted only when `Path.absolute()` produces that canonical
  lexical path; no `resolve()`/`realpath()` follows symlinks.
- At the canonical location, existing no-follow component/file opens and
  single-link, owner and permission checks remain authoritative. Canonical file
  symlinks, hardlinks and symlinked parent directories still fail closed.
- The existing nonblocking lifetime flock, owner-file retention, short transaction
  locks, atomic journal replacement and fsync ordering are unchanged.
- This is cooperative controller exclusion, not protection against a same-UID
  adversary replacing the entire private runtime directory or deleting lock files.

Reviewed contracts: approved native plan Task 2/3B and broker amendment,
`docs/feasibility/native-spike/managed-integration-report.md`, and the managed
runbook. The old integration report is historical; this report and the updated
runbook narrow its arbitrary `FILE` recovery contract. The referenced
`managed-output-next.md` was not present at the reported documentation path; no
contract was invented from that missing reference.

Preserved: exact P2 consent and saved instance/output/recovery scope; independently
verified compositor session; both strict physical, enabled, DPMS-on, nonmirrored
outputs; saved epoch/fallback without new hello; no name/ID adoption; tombstone
readback before done; missing/legacy journal refusal; no creation replay.

## RED / GREEN

Before changing production code, added
`test_copied_journal_cannot_bypass_active_canonical_owner`. It creates three
confirmed output intents using real private journal files and a recording synthetic
broker transport, holds a real canonical flock, proves a second canonical owner
cannot acquire it, and attempts recovery through a mode-0600 copied journal.

RED command:

```sh
python -m unittest discover -s tests/native_spike -p test_managed.py -k copied_journal -v
```

Observed exit 1: `AssertionError: (<class 'ValueError'>, <class 'OSError'>) not raised`.
The old production recovery incorrectly accepted the copy while canonical ownership
was held. After the restriction, the same test passed (1 test, 0.017 seconds).
Its passing assertions require zero broker requests, live output registry entries,
unchanged canonical/copy bytes, stopped-copy refusal, successful stopped canonical
recovery with all three persisted done states, exactly three removals and no extra
removal on repeated recovery.

Additional controls cover relocated copies, file/directory aliases, `..`, safe
lexical spelling sharing the active lock, and canonical file hardlink/symlink and
parent-symlink rejection. Existing managed tests now use the actual canonical
runtime/run layout, including explicit CLI restart recovery and missing canonical
journal behavior. No real flock or journal behavior is mocked.

## Verified execution

Commands run from the worktree root:

```sh
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/probe -v
cmake -S spike -B build/native-spike-managed-lock-fix \
  -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON \
  -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike-managed-lock-fix -j2
ctest --test-dir build/native-spike-managed-lock-fix --output-on-failure
git diff --check
```

- Native Python: **105 tests passed**, 16.457 seconds, no skips.
- Probe Python: **62 tests passed**, 1.662 seconds.
- Fresh Release build: renderer, ownership module and offline test executables built.
- CTest: **11/11 passed**. The native checks use explicit failure checks, not
  Release-disabled assertions. The module was compiled, never loaded.
- `git diff --check`: passed.

Logs are in `/home/cody/.hermes/cache/scratch/`:
`managed-lock-fix-red.log`, `managed-lock-fix-green.log`,
`managed-lock-fix-native.log`, `managed-lock-fix-probe.log`,
`managed-lock-fix-build.log`.

## Scope, files and remaining gates

Repository files changed: `tools/native_spike/managed.py`,
`tests/native_spike/test_managed.py`, `docs/feasibility/native-spike/runbook.md`,
and this report. Build artifacts are isolated in
`build/native-spike-managed-lock-fix`; reviewed binaries were not overwritten.
A reusable canonical-lock/copy regression lesson was added to the default
`native-desktop-integration` skill outside the repository.

No VM boot, plugin load, host compositor query/mutation, device action, capture,
input, SDK operation or subagent was used. Parent live evidence and earlier
reports were not edited. Tests use synthetic compositor responses, real filesystem
locks/journals and existing harmless-child watchdog tests: they do not establish
production compositor/renderer or physical runtime acceptance.

The two-display VM prerequisite remains blocked by `Virtual-*` rejection under
the unchanged strict physical-connector guard. No topology change or guard
relaxation is bundled. Runtime qualification remains a separate gate.

Implementation issues: one multi-hunk test patch was rejected atomically for an
ambiguous repeated assertion and reapplied with precise edits; no partial edit
landed. Editor Pyright emitted project-import/dynamic-fixture diagnostics, while
actual explicit Python suites passed. No test/build blocker remains.
