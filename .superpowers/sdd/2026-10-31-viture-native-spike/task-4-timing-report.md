# Task 4 bounded timing foundation — offline only

## Outcome and boundary

Implemented and compiled real `wp_presentation` integration in the existing generated-SHM-pattern renderer, plus an offline metrics reducer. **Task 4 is not complete.** No capture, DMA-BUF/EGL import, meshes, continuous redraw, GPU fences, input, devices, SDK, compositor connection, or live runtime qualification was performed. Managed-output ownership-safe lifecycle remains blocked. Parent live-evidence documents were left untouched and excluded from the commit.

Local installed dependencies sufficed: wayland-client 1.26.0 and wayland-protocols 1.49. CMake generates presentation-time bindings from the installed stable XML, without downloads. The installed XML supports v2; the implementation deliberately binds `min(advertised, 1)` and uses v1 refresh semantics. Missing XML fails configuration with its required local path. No packages installed.

## Implementation and ownership

- `spike/include/xr/metrics.hpp`, `spike/src/metrics.cpp`: fixed-size metrics state, submission tickets, terminal-event deduplication, bounded histogram, clock/sequence validation and aggregate JSON.
- `spike/src/wayland.cpp`: optional registry bind, `clock_id` listener, one feedback request immediately before each content commit, `sync_output` association, `presented` and `discarded` listeners. The initial empty policy/configure commit is not a content submission. No swap, buffer release, dispatch iteration or frame callback increments presented counts.
- A fixed 32-slot feedback array owns proxy/listener state at stable addresses. Both terminal callbacks destroy the local proxy and free the slot. A full queue backpressures further content submission, not feedback observation. Close cancels outstanding proxies before destroying the surface/display; unresolved observations become `abandoned`, not compositor discard. Close is idempotent. Removal of a bound presentation global stops this run rather than silently replacing its capability. Missing protocol at startup permits the existing pattern path but cannot produce presentation timing.
- `spike/include/xr/presentation.hpp` and `spike/src/main.cpp`: snapshot survives close; normal signal-driven shutdown and handled renderer errors emit one aggregate JSON line after teardown. Capability/startup argument rejection before renderer construction still exits without a timing report. No promise of a final report on SIGKILL/crash.
- `spike/CMakeLists.txt`, `spike/tests/metrics_test.cpp`: actual renderer linkage and offline CTest target. Checks are explicit, not disabled by `NDEBUG`.

## Invocation and report semantics

Only this renderer invocation was executed:

```sh
build/native-spike/xr-native-spike --help
```

Future runtime use remains parent-owned and requires new run-scoped approval. The existing gated invocation is unchanged (shown as documentation, **not executed**):

```sh
python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE
```

That supervisor launches the renderer with inherited capability sockets and these internal arguments:

```text
--supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION
```

The supervisor currently inherits stderr; therefore the child timing JSON appears on the same stderr stream as diagnostic text, not in the heartbeat socket or a new metrics file. Select JSON lines by `schema=1` and `kind=generated_pattern_presentation_timing`. Serialization/output occurs only after rendering teardown, not in feedback callbacks. There is no new runtime timing switch.

Fields:

- `protocol_version`: 0 if unavailable/not bound, otherwise negotiated 1; not the compositor's maximum supported version.
- `clock_id`: raw uint32 from the protocol, or JSON null until received. `clock_conflict` permanently records an illegal domain change. No host receipt time or `steady_clock` epoch is substituted for protocol time.
- `submitted`: actual content commit calls queued by this client, not successful display flush/scanout. Feedback allocation failure is checked before counting. With missing protocol this can increase while all terminal counts remain zero.
- `presented`: unique pending-ticket `presented` notifications, **including** notifications with unusable timing. It is not a count of unique hardware retraces or proof of hardware scanout.
- `discarded`: unique compositor discard notifications. `abandoned`: feedback locally canceled on teardown without a terminal event. `pending`: unresolved tickets (zero after explicit close). Duplicate/late terminal events cannot increment these counters twice.
- `invalid_timing`: presented notifications excluded from interval calculation. This slice accepts only `CLOCK_MONOTONIC`, a verified selected-output `sync_output` association with no conflicting output, valid nanoseconds and signed-64-bit-representable timestamp, and strictly increasing nonzero retrace sequence/timestamps. REALTIME, unknown or missing clock IDs are retained but not mapped. No wall-clock or sensor-clock subtraction occurs. Timestamp/retrace high-water marks survive invalid observations; intervals do not bridge an invalid observation.
- `interval_histogram`: eight fixed bins of differences between accepted presentation timestamps in callback order. Inclusive upper bounds in `interval_upper_bounds_ns` are 1,000,000; 4,000,000; 8,000,000; 9,000,000; 17,000,000; 34,000,000; 100,000,000; then overflow (`null` bound). Long valid gaps remain in the overflow bin. Discard does not manufacture a timestamp or success; the next valid interval can span the gap. No raw event log, percentiles, missed-deadline estimate or full-run expected-frame denominator is implemented.
- `flag_counts_vsync_hw_clock_hw_completion_zero_copy`: four independent counters for those v1 bits over presented notifications. Absence of a hardware flag is not upgraded to hardware evidence. Histogram membership does not require hardware flags and must not be interpreted as independently verified scanout.
- `last_refresh_prediction_ns`: the last compositor prediction, including zero/unknown. **Not** an advertised mode, negotiated exact mode token, measured average interval, or sustained refresh result. Existing probe/parent evidence owns exact mode advertisements and active mode; this reducer does not collect or round those tokens.
- `sustained_cadence_qualified`: always false. The pattern remains static and commits only when configured/dirty. A normal unchanged surface can have one submission and one presentation observation, not 120 observations per second.

Storage is constant-sized: 32 pending ticket slots, 32 adapter feedback slots, eight histogram counters, four flag counters and scalar state. Aggregate counters saturate at uint64 maximum; submission refuses counter exhaustion rather than reusing a ticket.

## TDD and verification evidence

RED before implementation:

```sh
c++ -std=c++20 -I spike/include spike/tests/metrics_test.cpp -o build/metrics-red
```

Exited 1 because `xr/metrics.hpp` did not exist. This was the initial missing-interface RED, not a runtime assertion failure. After implementation, build caught the C++ collision between the generated function and struct named `wp_presentation_feedback`; explicit `struct` qualification fixed it.

Additional behavior-first regressions were then observed failing with:

```sh
cmake --build build/native-spike -j2
ctest --test-dir build/native-spike -R metrics --output-on-failure
```

1. `repeated retraces remain invalid after rejection` failed (CTest exit 8). Fix: retain sequence high water across invalid feedback.
2. `timestamp high water survives repeated regressions` failed (CTest exit 8). Fix: retain timestamp high water independently of interval continuity.

GREEN final commands, from repository root:

```sh
cmake -S spike -B build/native-spike -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike -j2
ctest --test-dir build/native-spike --output-on-failure
build/native-spike/xr-native-spike --help
cmake -S spike -B build/native-spike-timing-release -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike-timing-release -j2
ctest --test-dir build/native-spike-timing-release --output-on-failure
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/probe -v
git diff --check
```

Results: actual renderer built in both configurations; **5/5 CTests passed** in each (`layout`, `ipc`, `metrics`, `presentation`, `native_help`); **76 native-spike Python tests passed**, **62 probe Python tests passed**; help exited 0; diff whitespace check passed. Expected negative-permission test diagnostics were present; no suite failures remained.

Metrics fixtures cover missing capability, submitted-not-presented, domain uncertainty/conflict, presented/discarded separation, duplicate terminal events, repeated retraces, backward/overflow/malformed timestamps, output association, 32-slot backpressure, idempotent abandonment/late events, flags, and a 10,000-event synthetic trace using fixed storage including long gaps. These are reducer tests, not a wire-level fake-compositor or live-protocol acceptance test. Actual protocol adapter wiring was compiled, inspected, and linked, but callback delivery/proxy teardown against a compositor remains unexecuted and requires later authorized qualification. No timing results for Beast or Luma are claimed by this change.
