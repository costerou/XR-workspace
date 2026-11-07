# Exact PRO 2 acceptance gates

QA scope: offline fixtures/tests and review only. No hardware/services were accessed.
Canonical identity is `pro2`, `35ca:1301`, exact `VITURE PRO 2 XR Glasses`.
The user-supplied Nov 4 14:33 EST inventory (bus 1 device 13,
card1-DP-2, 1920x1080 modes, XR off) is context, not a QA capture.
Bus/device/connector numbers are transient. Enumeration is not IMU support.

## Gate sequence for the parent to supervise later

Each gate records exact revision/provider, identity, action, expected/observed result,
pass/block/unknown and sanitized evidence. Unknown blocks advancement. A mock pass
never closes a live gate. No mandatory long soak: use normal work and passive incident
reports; stop on an incident and preserve evidence. No installation or execution of
these live steps is authorized in this QA thread.

| Gate | Prerequisites and bounded observation | Pass evidence / stop condition |
| --- | --- | --- |
| G0 passive identity/access | Parent reads passive USB/product/udev/DRM metadata only; checks exact triple and effective access without opening/claiming interfaces. | Exact match, unambiguous device; distinguish microphone/interface records. Access metadata cannot prove successful IMU access. Conflict/multiple candidates blocks. |
| G1 plain 2D/audio | G0; parent uses existing physical 1920x1080 presentation without XR or proprietary writes. Inspect text/orientation, normal audio playback and selected sink/mic. | Readable upright image and stable normal-use audio, no disconnects or unintended routing. No assumed sample rates, audio capabilities or inferred 3D. |
| G2 provider-only IMU desk | Official exact-model/provider revision, source coordinates and correction/calibration contract reconciled first. Parent explicitly authorizes provider only, stationary desk, no XR compositor. | Fresh finite normalized pose/timestamps with documented units and calibration; no unsupported fallback, Luma start, guessed command or unproven -8.5° correction. Enumeration alone fails. |
| G3 upright four directions | G2; parent supervises small left/right/up/down motions, returns to center, checks roll upright; preserve raw/provider/published reference frames. | Expected signed yaw/pitch for all four motions, no mirrored/inverted motion, stable center/calibration. Do not guess signs from a generic quaternion convention. Failure blocks XR. |
| G4 supervised XR lifecycle | G1–G3 and offline recovery green; recoverable physical fallback chosen; parent authorizes start/stop. | Normal start, usable windows, stop restores windows/outputs and ownership; repeat start/stop once. Renderer failure recovery leaves windows reachable; no stale hooks/session. |
| G5 disconnect recovery | Offline replay first. A physical unplug requires separate parent authorization after G4. | Simulated removal at startup, calibration, running, stop and recovery is bounded; no auto restart loop or mode write while absent; reconnect cooldown preserved, explicit safe restart, windows reachable. Simulation is not physical USB validation. |
| G6 optional high Hz / 3D | Deferred until G0–G5 pass and exact-model evidence supports it. Parent operates physical switch and authorizes each experiment. | Advertised actual mode and successful rollback to baseline verified independently; saved settings isolated. Failure/timeout restores prior mode. Never send Luma switching commands to PRO 2. |

## Offline integration acceptance

`python -m unittest discover -s tests/pro2 -v` exercises synthetic identity conflicts,
old model preservation and per-model profile isolation. It deliberately stays red on
the baseline until platform support is merged; no expected-failure markers hide blockers.

Regression selection (all adapters/fakes, no service or hardware launches):

```
python -m pytest -q tests/pro2 tests/xr_controller tests/native_spike/test_profiles.py tests/native_spike/test_tracking.py tests/native_spike/test_tracking_luma.py tests/native_spike/test_control_wire.py tests/native_spike/test_recovery.py tests/native_spike/test_mode_switch_gap.py tests/luma_capture/test_protocol.py
```

Existing protocol tests validate existing Luma capture decoding only. They do not
establish a PRO 2 packet format. Native driver wire tests must also pass unchanged
when the integrator builds offline; QA has not built native components here.

Before clearing integration, require tests against the merged provider entry point
which spy on spawn/interface/command boundaries and assert **zero calls** for pro2
when support is unknown, selected driver unavailable, model/product mismatched,
stale saved Luma choice present, or installed-provider evidence absent. A consent
label or UI disabled state alone cannot enforce this. No parser/command bytes may
be added without cited exact-model evidence. Check NaN/truncation/version/length
rejection, timestamps, calibration reset and publisher coordinate semantics at the
existing native wire boundary; do not fabricate a new PRO 2 wire layout.

Profile tests must preserve every existing Luma/Beast field across PRO 2 writes.
FOV needs exact-model evidence or explicit unavailable status; a generic default is
not measured optics. Pending proof, PRO 2 defaults must disable tracking and mode
switching. Recovery tests must verify previous physical mode, output ownership,
window placement and cooldown, including failures during rollback.

Privacy review: incident output must exclude serials, workspace/window titles,
application content, network identifiers and raw proprietary payloads by default;
keep run IDs, timestamps, bounded status and sanitized failure reasons. Existing
privacy/protocol tests are regressions, not a declaration that future provider
telemetry is safe. Review new fields and exports explicitly.

## Initial review / blockers for integrator

Baseline `ff7b26a`:

* B1: `tools/xr_controller/detect.py:14` exact map lacks PRO 2;
  `tools/native_spike/profiles.py:47` excludes its independent key. Acceptance
  reproduction: 7 tests, 5 pass, 1 failure (unknown identity), 1 error (unknown profile).
* B2: `tools/xr_controller/state.py:167` generic tracking fallback and
  `tools/xr_controller/runner.py:91` runtime fallback need exact-model guard review.
  Controller boundary test rejects all three saved legacy driver choices before
  consent/launch today; this currently relies on unsupported model rejection and
  must remain green when platform recognition lands. It does not exercise direct
  provider launch or prove command-level safety; driver-owned tests must cover that.
* B3: protocol sibling's preliminary report identifies official Gen2 NWU versus
  provider-specific conversion/-8.5° correction and TBD FOV. Treat as unresolved
  provider contract, not clearance to launch. Source claims are sibling findings,
  not independently verified by QA. No unproven correction or FOV inheritance.
* B4: display sibling flags mode restoration paths in managed.py even when
  mode_switch=False. Integrator must demonstrate startup AND teardown exclude
  Luma 1920x1200 writes for PRO 2. High Hz/SBS remains deferred.
* Hardware sibling's short passive capture records exact identity, access ACL,
  1080p60 presentation and DisplayPort LPCM ELD. This supports inventory only;
  audio playback, IMU, axes, XR and disconnect recovery remain unaccepted.

Read sibling reports `pro2-platform.md`, `pro2-protocol.md`, `pro2-display.md`,
`pro2-hardware.md` in the shared report directory. No sibling code merged or edited.

Validation: controller regressions via `python -m unittest discover -s
 tests/xr_controller -q`: **530 passed**, 4.730s. Pytest is unavailable in the system
Python; no packages installed. Supplemental unittest regression command below
covers profiles/tracking/wire/recovery/mode/capture using offline test doubles:

```
python -m unittest tests.native_spike.test_profiles tests.native_spike.test_tracking tests.native_spike.test_tracking_luma tests.native_spike.test_control_wire tests.native_spike.test_recovery tests.native_spike.test_mode_switch_gap tests.luma_capture.test_protocol -q
```

Supplemental result: 149 discovered, 147 passed, two import errors because these
test modules import sibling helpers as top-level modules (`control_wire`, `synth`).
Recheck those two modules with `PYTHONPATH=.:tests/native_spike:tests/luma_capture`;
use that prefix for the combined unittest command above. This is harness setup,
not a PRO 2 product failure. Existing subprocess fixture tests emitted ResourceWarnings.
Driver's early report now describes a passive preflight and five fake-sysfs cases
covering rejection before start/reuse/fallback. QA has reviewed that report only;
its branch/code and integration test result remain the integrator's merge gate.

Helper-path recheck: **56 tests passed**, 5.899s. Thus controller 530 and
supplemental 147 + 56 = **733 offline regression tests passed** across the stated
commands; the new 7-case PRO 2 acceptance suite remains intentionally red at this
baseline (5 pass, 1 failure, 1 error). No live acceptance claimed.

Merge disposition: QA-owned docs/tests can be cherry-picked independently;
red identity/profile checks must turn green after platform integration. Provider
command boundary, native wire, recovery and live gate requirements above remain
explicit gates, not completed QA claims. Next safe live gate is parent-supervised
G0 passive re-identification; provider and XR gates remain blocked.

## Parent integration update

Parent reports all six stream heads merged, with direct `managed.run_managed` and
`recover_managed` PRO 2 rejection before client calls. Parent also reports native
CTest **103/103 passed** using explicit renderD129 and fake runtime; this is
parent-supplied offline evidence, not a QA rerun or live acceptance. Integrated
Python suites are being run with pytest 9.2.0 and dependencies from existing uv
cache on sys.path, without installation. Earlier missing pytest was an interpreter
dependency issue, not an assertion failure; do not count it as product failure or
assume the integrated Python run has completed.

PRO 2 physical control is **HOLD L1, never R1**. Keep SBS disabled throughout the
initial gates. Exact USB/product identity and independent `pro2` profiles remain
mandatory. Future hardware activation is achievable through the staged provider,
axis and lifecycle gates with parent consent once blockers are resolved; current
refusal is not a permanent unsupported verdict.

QD1 remains present in driver source at reviewed committed head `9785c1c`: PID
read failure skips exact product inspection. Integrator should close this in the
integrated source and rerun its regression; managed entrypoint guards add defense
but do not repair the independent tracking entrypoint's incomplete-identity check.
See qa-driver-review.md. Driver report now explicitly acknowledges preflight's
runtime hotplug confinement limitation and reports 113 lifecycle tests passed;
those are sibling results, distinct from QA's six-case rerun.


Latest QA recheck supersedes prior QD1-open status: **QD1 CLOSED** at actual
integrated head `40f1dd5de9b7b363a58eca30817cfe75784f6b2c`. Exact original
missing-PID reproduction now rejects; seven provider tests passed (0.008s),
including 18 incomplete/conflicting-identity entrypoint subcases with zero
status/config/log/stop/spawn calls. See qa-driver-review.md for independent
execution evidence. No live setup changes; hardware activation remains staged.
