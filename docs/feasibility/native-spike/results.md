# Native spike results — Task 3 offline only

**Partial implementation. Live Task 3 acceptance pending. Managed ownership and
lifecycle remain blocked, even if the parent grants P2.** No live query, mutation,
presentation launch, capture, input/device, SDK or mode action was performed.

Actual final command excerpts:

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
1/4 Test #1: layout ...........................   Passed    0.00 sec
2/4 Test #2: ipc ..............................   Passed    0.00 sec
3/4 Test #3: presentation .....................   Passed    0.00 sec
4/4 Test #4: native_help ......................   Passed    0.00 sec
100% tests passed out of 4
$ build/native-spike/xr-native-spike --help
xr-native-spike: native output-bound layer-shell test pattern (no capture/SDK/input).
Launched only by the permission-gated Python supervisor with inherited capability sockets.
Internal: --supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION
Use python -m tools.native_spike run --instance INSTANCE --output OUTPUT --consent-file FILE.
Managed output ownership qualification is currently BLOCKED. No 120 Hz claim.
```

A fresh Release configure/build with hardware/SDK OFF in
`build/native-spike-task3-release` also generated layer-shell and xdg-shell code,
linked the native executable, and passed **4/4** CTests. `git diff --check` was
clean. Tests use synthetic data/socket peers; they are not live-success evidence.
No runtime package was missing or installed.

The output ownership barrier is described in [runbook.md](runbook.md).
`create_outputs` refuses before mutation; partial creation rollback and managed
workspace/window migration are not implemented/qualified. Internal supervisor
launcher wiring is tested at a synthetic child boundary, but the CLI does not
reach it. The native renderer is compiled, not visually/runtime-qualified. It
uses generated SHM pixels only; GPU capture, timing and focus coexistence remain
unmeasured. No advertised mode, timer or heartbeat establishes 120 Hz.

Detailed RED/GREEN evidence, exact interfaces, consent schema and self-review:
[Task 3 report](../../../.superpowers/sdd/2026-10-31-viture-native-spike/task-3-report.md).
