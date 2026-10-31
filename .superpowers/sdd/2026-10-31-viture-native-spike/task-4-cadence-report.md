# Task 4 bounded generated-pattern cadence slice — offline only

## Outcome

Implemented a finite, explicit generated-SHM animation option through the permission-gated Python CLI/supervisor to the real native Wayland renderer. Default remains static. Real native default and Release builds and all offline suites pass. **No live renderer, compositor query, device access, capture, input, SDK call or mode action was performed.** This does not complete Task 4 capture/GPU requirements, qualify sustained cadence, or authorize runtime. Parent review and new run-scoped approval remain required.

## Exact controls (documentation, not executed)

Static invocation and consent schema remain unchanged:

```sh
python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE
```

Explicit finite animation:

```sh
python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE --animate-pattern-ms 10000
```

`--animate-pattern-ms` accepts canonical decimal integers **1..60000 milliseconds** only. Zero, negatives, leading zeros, signs, floats, infinities and overflow are rejected. It is offered only on `present`, not managed `run`. The existing owned, single-link, no-follow regular mode-0600 consent file must contain exactly its previous fields plus **`"animate_pattern_ms": 10000`**, matching the requested integer exactly. Keep `gate: "P2-presentation-only"`, `allow: ["test-pattern"]`, selected instance/output/run and independent recovery output, and capture/input/sdk/mode_switch all false. A static consent does not authorize animation; an animation consent alone cannot silently enable it. Consent mismatch stops before runtime lookup or client construction. The supervisor independently rejects invalid Python types/ranges and consumes/closes the supplied Wayland socket even on that rejection.

The supervised native argv gains only `--animate-pattern-ms 10000`, in addition to the existing private control/Wayland FDs, output, run ID and session. Native parsing validates the same canonical range before using capability sockets. It remains impossible to launch a normal renderer using this switch alone. Existing peer/session/start-frame validation, heartbeat watchdog, physical-pair guard, nonfocus policy and FD transfer remain intact.

## Scheduling, lifetime and limits

- `PatternCadence` is the pure scheduling gate actually used by the adapter: first configure permits one submission, commit consumes readiness, a real `wl_surface.frame` completion permits the next animation submission. The callback timestamp is ignored. Static callbacks never create redraw work; configure invalidation still does. At most one frame callback is outstanding.
- A moving bright vertical stripe is generated over the existing color/grid pattern. Phase advances with committed content, not a fabricated physical time/pose. No captured pixels or tracking are used. The initial frame retains the original pattern.
- Free SHM memory is selected only after `wl_buffer.release`; the existing maximum of three buffers is retained, including resize backpressure. Busy memory is never repainted. Missing release or full 32-entry presentation feedback storage retains pending redraw readiness but cannot submit. The dispatch loop polls for protocol events; no sleep/spin redraw pacing was introduced.
- Duration starts immediately after successful surface initialization, before the first draw, using the host steady clock solely for the stop deadline. Initialization retains its existing bounded roundtrips/configure waits and watchdog; these are outside the requested animation duration. Deadline checking is independent of callback arrival and is rechecked after CPU pixel generation, before committing. A very short duration can legitimately produce zero content submissions.
- Finite expiry takes the normal close path. SIGINT/SIGTERM, compositor/output removal, dispatch failure, heartbeat disconnect and supervisor shutdown retain their stop behavior. Deadline exit is not a hard-real-time guarantee: the loop uses at most 50 ms dispatch polls, plus CPU work/scheduling delays; no deadline commit is deliberately scheduled. Signals during pixel generation are handled when control returns to the main loop. The existing independent two-second watchdog remains the failsafe for a stalled renderer.
- Teardown stops the cadence gate, destroys outstanding frame/feedback proxies, abandons unresolved feedback rather than inventing discards, and closes surface/buffers/display. No end-of-run feedback drain is added: the final outstanding commit may be abandoned. Final report is emitted after explicit teardown, not guaranteed after SIGKILL/crash.

## Report semantics

The existing stderr JSON line retains schema 1, kind `generated_pattern_presentation_timing`, actual submitted/presented/discarded/abandoned/pending counts, clock validation, flags and fixed histogram. New scalar fields:

- `valid_presented`: observations accepted under the existing MONOTONIC, selected-output, representable timestamp and strictly increasing timestamp/retrace checks.
- `interval_count`: number of valid adjacent intervals included in the histogram, **not** number of presented events, callbacks or predicted refreshes.
- `interval_sum_ns`, `interval_min_ns`, `interval_max_ns`, `interval_mean_ns`: validated interval summary; mean is sum divided by **interval_count**. Min/max/mean are null with no intervals. Increasing disjoint accepted intervals bound the integer sum by the representable timestamp span.
- `observation_duration_ns`: last minus first accepted presentation timestamp, null until at least two valid observations. It is the observed protocol timestamp span, **not requested duration, full-run elapsed time, or coverage proof**. It can span invalid gaps that are excluded from the interval summary. Discard contributes no timestamp; a subsequent valid interval retains the real gap.

No FPS is calculated. Refresh prediction remains labeled as prediction. Frame callbacks, releases and content commits never increment presentation counts. `sustained_cadence_qualified` remains unconditionally false. A generated CPU/SHM workload cannot qualify production GPU import, capture, tracked motion, or exact 120 Hz performance. No expected-frame denominator, missed-deadline verdict, quantiles, full-run timing coverage or benchmark threshold is claimed. Requested duration/mode remain recorded in the run's consent/argv; they are not new JSON timing fields.

Initialization was deliberately not refactored. **Retained prior limitation:** if `Presentation::open` fails after binding presentation capability but before installing its Impl, the final default metrics snapshot loses that partial capability state. Zero protocol fields on an open failure must be treated as initialization-incomplete, not evidence of unavailable compositor capability. There can be no content submission in that path. A separate initialization-lifetime test/fix remains appropriate.

## TDD evidence

1. Added CLI-to-consent-to-child animation tests first. `python -m unittest discover -s tests/native_spike -p test_presentation_only.py -k animation -v` exited 1: both tests raised argparse `SystemExit: 2` because the new option was absent. After implementing the explicit option/consent/forwarding boundary, the same command passed 2 tests.
2. Added native duration/scheduling/animation tests first. `cmake --build build/native-spike -j2` exited 2 because `parse_pattern_duration`, `PatternCadence` and `animated_pattern_pixel` were missing. After implementation, `ctest --test-dir build/native-spike -R presentation --output-on-failure` exited 8 at `generated animation changes pixels`: the stripe moved away from the test pixel. Corrected stripe direction; the same build/test passed.
3. Added interval denominator/span tests first. `cmake --build build/native-spike -j2` exited 2 on missing valid-presented/interval-summary fields. Added the reducer state and serialization; metrics and full CTest passed.
4. Expanded regression coverage: default launcher omits animation argv; malformed duration and mismatched/type-invalid consent never construct a client; rejected supervisor duration closes its FD without spawn; static and duplicate callbacks do not animate; configure cannot bypass an outstanding callback; busy-buffer and full-feedback readiness; stalled-callback deadline; cancellation/idempotence/late callbacks; animate-before-open rejected. Existing teardown/abandonment/watchdog/session/FD tests also remain green.

The initial native REDs were missing-interface compile failures, not runtime assertions; the CLI RED was the absent-option parser failure. No synthetic metrics trace is presented as live evidence. Native tests exercise the production pure scheduling/reducer classes, not a wire-level fake compositor; real adapter callback delivery, buffer release order and animated visual teardown remain unexecuted and parent-owned.

## Final verification (all exit 0)

Executed from the native worktree:

```sh
cmake -S spike -B build/native-spike -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike -j2
ctest --test-dir build/native-spike --output-on-failure
build/native-spike/xr-native-spike --help
python -m tools.native_spike present --help
cmake -S spike -B build/native-spike-cadence-release -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike-cadence-release -j2
ctest --test-dir build/native-spike-cadence-release --output-on-failure
python -m unittest discover -s tests/native_spike -q
python -m unittest discover -s tests/probe -q
git diff --check
```

Results: actual renderer linked in both configurations; **5/5 CTests in each**, **80 native-spike Python tests**, **62 probe Python tests** passed. Help shows the new explicit option and static default. Expected `blocked:` messages come from negative permission/validation fixtures. The compiler build completed without warnings/errors; editor language-server diagnostics do not resolve this project's build include configuration and were not used as build evidence. No dependencies installed or downloaded. Parent live-evidence files were not modified or staged.

## Scope of changes

`spike/include/xr/{presentation,metrics}.hpp`, `spike/src/{presentation,wayland,main,metrics}.cpp`, corresponding native tests, `tools/native_spike/{__main__,supervisor}.py`, Python presentation-only tests, and this report. Managed-output ownership, capture/GPU workload and live qualification remain blocked or unexecuted. Parent reviews this commit before any live trial.
