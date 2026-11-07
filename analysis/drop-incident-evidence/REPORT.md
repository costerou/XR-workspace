# Incident evidence correction — 2026-11-04

Scope: offline reporter changes in `drop-incident-evidence`. No installation, merge, master changes, live service operations, USB probes, sysfs access, or device writes. Host evidence was read from saved JSON and the user journal. All tests use simulated host sources or existing offline fixtures.

## Source reasoning

Run `aca483725400c828e0a5c9c8cb821b00` started **13:05:37 America/Detroit** (18:05:37Z), confirmed by its systemd user journal. Its saved consent identifies Luma tracking and `mode_switch: false`. The supplied configuration is full XR, patched Luma, 1920×1080@60, depth off; the saved renderer exit metrics also show a 1920×1080 surface and roughly 60 Hz presentation intervals. These metrics do not prove that the physical panels showed a picture.

Read-only sources:

- `~/.local/state/viture-workspace/incidents/20261104T184159Z-usb-drop.json`
- `~/.local/state/viture-workspace/aca483725400c828e0a5c9c8cb821b00.json`
- `~/.local/state/viture-workspace/aca483725400c828e0a5c9c8cb821b00.result.json`
- `journalctl --user -u viture-xr-aca483725400c828e0a5c9c8cb821b00.service`, bounded start and end windows
- Existing investigation documents `analysis/drop-root-cause-20261104/AFTER-PATCH-1247.md` and `PRECURSOR-1116.md`.

The incident records the first hub 1-5 disconnect at **13:40:59**, with repeated loss/re-enumeration clusters around 13:41:05, :14, :25 and :30. Its retained kernel lines put the latter disconnect edges at **:24 and :29**; :25 and :30 include re-enumeration. Driver and detector timestamps are not interchangeable with kernel disconnect timestamps. The first :14 root event is also missing from the incident's compact timeline because of kernel deduplication after a :10 child disconnect, but remains in its kernel log. This correction does not change kernel deduplication.

The original incident was emitted at **13:41:59** with no renderer result and a “kept running” summary. Renderer exit metrics appeared at **13:41:59**; systemd recorded the unit ending at **13:42:00**. The final result explicitly records `stop_reason: tracking-timeout`, `renderer_returncode: 0`, `complete: true`, `phase: done`, and no error. `Supervisor.tick()` calls `disconnect(..., 'tracking-timeout')` on the tracking-loss deadline; the interrupt path records `interrupted`. This supports **automatic tracking timeout**, not an assumed user Stop. Clean renderer exit and successful teardown do not mean the XR session remained running.

**USB disconnect is confirmed; its underlying cause is undetermined.** Cable handling at 13:40:59 is unknown. Neither a cable/hardware diagnosis nor exclusion of host software follows from these logs.

The earlier `988f…` run is a distinct sequence: first USB loss at 12:47:30, original renderer display-connection exit around 12:48:07, and later live status with dark physical panels despite a nonblack compositor capture. A live process or captured compositor image does not establish panel illumination. The 13:03:14–13:05:approximately36 no-host-tracking trial is too short to infer stability.

## Implementation

- `observations.usb_disconnect` distinguishes kernel/bus observations from driver-only communication loss. `cause.confidence` for USB loss is low, with the initiating cause explicitly undetermined.
- `observations.link_order` records USB-first, video-first, equal timestamps, or undetermined from observed event times, separately from incident-kind priority. Timeline sorting is unchanged. Missing video evidence does not establish USB-first.
- Reports written during a run save their IDs in that run's monitor state. Suppressed episodes append their timestamped evidence to the latest associated report while normal suppression counters and notification limits remain intact.
- The terminal outcome updates all associated reports for that exact run, including after a monitor restart. JSON and Markdown retain their original names/IDs; amendments do not notify again or rerun the hook. New reports after the normal rate window remain possible.
- A known Stop/interruption is labelled stopped. A clean end without Stop evidence is labelled ended. Terminal results replace the provisional kept-running fields. `updated_at` and `session_outcome_at` identify the amendment and when the monitor learned the outcome.

## Association limits

Run identity establishes a shared lifecycle, **not causation** between an earlier disconnect and the end. Observed times retain existing detector debounce, kernel deduplication, clock resolution and bounded-timeline limits (64 signals, 256 KiB bundle). `session_outcome_at` is the controller observation time, not a claimed exact renderer-exit time. “Kept running” on an unfinished report describes the observation at reporting time, not proof of recovery or visible panels.

Old reports without saved run/report associations are not retrospectively discovered or rewritten. Missing/corrupt monitor state, reboot, deleted reports, or a monitor crash between persisting the ended state and flushing the final amendment can prevent an exact update. The live incident supplied for this investigation remains untouched. Hook consumers receive the initial report once; they must reread that path to see subsequent amendments. File replacements are individually atomic, with Markdown written before JSON, as for initial reports; the pair is not a filesystem transaction.

## Offline test-first evidence

Before implementation, replayed both USB-first/video-first timestamps and the Nov 4 cluster/timeout sequence, delayed clean end, repeated suppression, restart and multiple reports in a run. `python -m unittest tests.xr_controller.test_incident_evidence` ran **7 tests, 8 assertion failures** (two ordering subcases), with no errors. See `red.txt`. Failures included high hardware confidence, missing observed-order fields, duplicate terminal reports, discarded suppressed evidence and stale kept-running state.

The seven original regressions then passed; two boundary tests were added for cross-run isolation and a restart discovering the ended run. A tenth regression then failed because a later Stop request overrode a recorded automatic timeout; `stop-red.txt` preserves that failure. Recorded shutdown reasons now take precedence over a later Stop flag. Relevant existing expectations were corrected to require low USB cause confidence and one amended report instead of a duplicate. Verification results and commands are retained in `controller-green.txt` and `supervisor-green.txt`.

`pytest` was unavailable; no dependency was installed. Repository documentation prescribes per-directory unittest discovery. The full controller suite and relevant native supervisor/drop suites were used. Unrelated full native/compositor suites were not run because some invoke Hyprland or device-oriented launch paths, outside the strict read-only host scope. No live validation or installed behavior change is claimed.

Final verification:

- `python -m unittest discover -s tests/xr_controller`: **533 passed**, including 10 new regression tests.
- `PYTHONPATH=tests/native_spike:. python -m unittest test_supervisor test_supervisor_faults test_usb_drop test_survive`: **91 passed**. Existing unclosed-socket `ResourceWarning`s are retained in the log.
- `git diff --check`: passed.

The first native invocation used package-qualified names without the suite directory on `PYTHONPATH`. It produced two fixture-import errors (`test_launch_uses_given_renderer` and `test_launcher_interrupt_and_error_reap_renderer_and_started_worker`, both importing `test_hyprland`). The corrected command above resolves those imports and passes both tests. No test implementation was changed to hide those errors or warnings.

## Parent review follow-up

Two further regression tests failed before correction: (1) a saved USB event at1000.1 followed by video at1000.2 became same-timestamp after amendment, and (2) a duplicate USB event with three repeats incorrectly added those repeats to the later video entry. Timestamps now retain fractional seconds when supplied; repeat counts follow the matched signal and respect the bounded timeline. Historical seconds-only timestamps cannot regain missing precision.

Independent verification after these fixes: `python3 -m unittest discover -s tests/xr_controller` — **535 passed** in4.861seconds. The previous91 supervisor/drop tests remain applicable: this follow-up changes only report timestamp formatting and amendment aggregation.
