# Supervisor test fates

Written 2026-11-10 for the Rust rebuild ([rust-rebuild-plan.md](rust-rebuild-plan.md), phase 6, and the
"Test migration" risk). The plan counted about 2,594 supervisor tests. The suite in `tests/native_spike/`
holds 2,654 today, in 105 test files and 478 test classes (counting this record's own gate). Every one of them has a recorded fate.

The record is [`tests/native_spike/test_fates.json`](../tests/native_spike/test_fates.json). Its gate is
[`tests/native_spike/test_fates.py`](../tests/native_spike/test_fates.py), which runs with the rest of the suite:

```sh
TMPDIR=/tmp /usr/bin/python3 -m unittest discover -s tests/native_spike
TMPDIR=/tmp /usr/bin/python3 tests/native_spike/test_fates.py --summary   # prints the totals row below
```

## The fates

| Fate | Meaning | What the record holds |
|---|---|---|
| `port` | The test checks Python behaviour from inside the process. It is rewritten as a Rust test of the same cases when that module is ported. | `target`: the `xr-workspace` module (named after the Python module it covers). `rust_test`: empty until the Rust test exists. |
| `wire` | The test checks bytes at a boundary that already has a golden file: the Lua sent to Hyprland, a renderer frame fixture, the run files on disk. It stays, and the Rust side is held to the same file. | `via`: the golden file. `how`: what has to happen. |
| `keep` | The code under test is not ported in phase 6 (the C++ ownership plugin, the measurement harness, an offline tool). The test stays as it is. | `reason` on the file. |
| drop | One named test whose subject exists only in Python. Each has a reason, and says what still covers the risk. | `drop`: test name and reason, inside its class. |

Until phase 8 removes the Python supervisor, every Python test keeps running against the Python code,
whatever its fate. The fates say what will cover the Rust supervisor.

## Totals

| port | wire | keep | drop | all |
|---|---|---|---|---|
| 2501 | 132 | 23 | 4 | 2660 |

The `port` count is large because almost every supervisor test imports a Python module and calls it
directly. Few tests drive the supervisor through a socket or a process the Rust binary would also serve,
so only the golden-file tests can be kept unchanged. That is the honest finding of this triage, and it
matches the plan's phase 6 estimate (8–12 weeks).

The biggest port targets: `free_pointer` 301, `view` 297, `unified_cursor` 192, `bump` 145,
`window_memory` 139, `tracking` 115, `immersive` 109. Seven tests that check the key helper
(`xr_control.py`) go to the phase 5 helper port instead, which is held frame for frame to the Python helper.

## Wire tests

- **Lua run through an interpreter or Hyprland's `hl` (91 tests).** `test_window_memory_lua`, and the
  `LuaRunTests` / `LuaTests` classes in `test_adopt`, `test_fit_windows`, `test_free_pointer`,
  `test_glass_pills`, `test_input_lock`, `test_overview_cursor`, `test_privacy`, `test_restore`,
  `test_scroll_before_switch`. When a generator is ported, every input these tests use goes into
  `rust/corpus/hyprland/lua.json` (`lua_corpus.py`), so the Rust generator is held to the bytes the
  interpreter tests proved.
- **Renderer payload fixtures (33 tests).** The classes that compare the Python encoders with
  `fixtures/*_payloads.json` and `cursor_shape_names.json`. The renderer's C++ tests read the same files;
  the Rust encoders are held to them.
- **The phase 0 corpora (8 tests).** `test_control_corpus`, `test_lua_corpus`, `test_state_file_corpus`
  keep the corpora true to the Python. The phase 6 parity and cross-recovery gates hold Rust to them.

## Dropped tests

| Test | Why it goes | What still covers the risk |
|---|---|---|
| `test_control_plane_perf` `RequestPathTests.test_read_text_matches_path_read_text` | Proves a Python speed-up equals `Path.read_text`. Rust has one way to read `/proc`. | The rest of the class (the session check) is ported. |
| `test_control_plane_perf` `LazyImportTests.test_watchdog_module_leaves_multiprocessing_to_runs_with_a_guard` | Python import cost. A Rust binary has no imports. | Start-up time in `bench_baseline` (kept). |
| `test_control_plane_perf` `BindHelperTests.test_a_key_press_imports_no_json_re_or_socket` | Python import time of the key helper. | Key-press latency in `bench_baseline` (kept), against the phase 0 baseline. |
| `test_leftover_rule` `CodeChangedUnderTheSession.test_stop_reads_nothing_more_from_a_tree_that_has_moved_on` | Python imports lazily from the worktree, so a Stop could load newer code. The Rust supervisor is one binary pinned by hash. | The class's pinned-plugin and refused-pin tests are ported. |

Tests that read Python source (for example `test_sbs`'s "nothing here can reach USB") are not dropped. The
invariant is real, so the port checks the Rust module the same way.

## Rules while the port runs

- A new or removed test fails `test_fates.py` until the record changes with it. Give a new test its fate in
  the same commit.
- When a Rust port of a class lands, set its `rust_test` to the Rust file (optionally `file::test`). The
  gate checks the file exists.
- Phase 8 may delete a Python test file only by giving its record a `retired` reason. The gate refuses
  it while any `port` class in that file has no `rust_test`.
- A new drop needs a reason that says what still covers the risk. Update the totals row above
  (`--summary` prints it); the gate checks it.
