# Task 3A — presentation-only offline delivery

Base: `dfc8706b5b1d74b46ec027d783d3c5e3a8e48114`.

## Outcome and authorization boundary

Implemented `present`, a distinct consent-gated CLI that launches the existing
native test-pattern renderer on an existing approved physical output. No output,
workspace, mode or settings mutation; no journal creation/recovery or ownership
proof is required for this path. Managed `run`, creation and recovery remain
blocked, with their existing ownership contract unchanged. No cooperative
name-based deletion was added. Prior Task 3 code and this addition still require
parent review together.

**No live compositor queries/presentation, device/input/capture/SDK execution,
package installations or hardware transitions occurred.** Glasses were not
connected or accessed. Runtime approval remains pending. Only offline suites,
local synthetic sockets/harmless child processes, builds and CLI/native help ran.
This is not three-display acceptance, a live rendering result or a 120 Hz claim.

## Implementation

- Consent is checked before runtime lookup/client construction or any connection.
  `present` rejects the managed P2 record; `run` rejects presentation-only consent.
- The selected Hyprland process/session is verified through existing bounded IPC,
  peer credentials, process-start/boot identity and its selected instance lock.
  `connect_wayland` uses that verified lock's endpoint, never ambient
  `WAYLAND_DISPLAY`, and verifies the actual Wayland peer equals the IPC peer.
- Target and independent recovery connector must be different, enabled, DPMS-on
  entries in a validated snapshot. Conservative connector names are DP, HDMI-A,
  eDP or DVI-D plus decimal connector number, as in existing physical recovery
  classification. This is not DRM identity or adversarial same-UID attestation.
- The child watchdog repeats read-only session/physical-pair validation initially
  and every 0.5 seconds after each completed guard. Failure stops the exact child.
  Queries retain existing two-second per-request bounds; this is not a hard
  0.5-second disappearance guarantee or an event-history/ownership proof.
- The shared launcher consumes the Wayland socket, closing the parent's duplicate
  immediately after spawn and on rejection/spawn failure. Child exit now releases
  the last inherited connection rather than leaving parent-held surfaces alive.
  Heartbeat EOF/timeout, Ctrl-C and transport/selector errors terminate/reap the
  exact child; cleanup does not retry failed managed recovery storage writes.
- Existing C++ renderer remains noninteractive: keyboard NONE, empty input
  region, exclusive zone zero, no seats/injection/focus/capture/SDK requests.
  It binds the selected `wl_output` proxy once; its global removal closes the
  presentation and never retargets a replacement, including a same-name output.
  This existing renderer behavior was inspected, not newly proven on-wire here.

## Exact consent schema and future launch

Template only — **not approval**. Parent must obtain explicit run-scoped user
approval for the selected instance, output and independent recovery desktop.
Create the record only after that approval, as an owned regular single-link
mode-0600 file, no symlink leaf, at most 16384 bytes. Strict JSON rejects duplicate
or unknown fields. All fields below are required:

```json
{"schema":1,"gate":"P2-presentation-only","run_id":"RUN","instance":"INSTANCE","output":"DP-2","recovery_output":"DP-1","allow":["test-pattern"],"capture":false,"input":false,"sdk":false,"mode_switch":false}
```

`schema` is integer 1 (not boolean). `gate` and the ordered `allow` array are exact.
The four false values are booleans. `run_id` is a fresh Task 2 ASCII token of at
most 80 characters. Instance matches `[0-9a-f]{40}_[0-9]+_[0-9]+` and the command
exactly; output also matches the command exactly. Outputs must differ.
`XDG_RUNTIME_DIR` must select the approved user's runtime; ambient display and
instance variables do not select a target.

From repository root, **only after parent review and explicit runtime consent**:

```sh
python -m tools.native_spike present --instance INSTANCE --output DP-2 --consent-file /absolute/private/presentation-consent.json
```

The fixed renderer path is `build/native-spike/xr-native-spike`. No renderer path
or arbitrary command comes from the consent record. Stop with Ctrl-C in this
supervisor terminal. CLI returns 0 for child status 0, otherwise 2; negative
child signal codes do not become successful CLI results. EOF can race normal
child exit and result in supervised termination rather than exit 0. Verify
actual visible teardown before claiming live success. No public control socket
or background service was added. Do not call managed `recover` for this path:
there are no managed resources to restore. Any later glasses connection remains
a separate parent-owned named-pair transition, not implied by this command.

## RED / GREEN evidence

Actual RED commands and observations:

- `python -m unittest discover -s tests/native_spike -p test_presentation_only.py -v`:
  initial 9 tests: 6 assertion failures (`presentation-only launcher missing`),
  3 argparse errors (`present` not an available command).
- Same discovery with `-k connects -v`: 1 assertion failure,
  `selected Wayland connector missing`, before connector implementation.
- With `-k recovery_loss -v`: 1 failure, observed 2.002671 seconds rather than
  less than 0.5; only heartbeat timeout stopped the synthetic child before the
  read-only guard was added.
- With `-k watchdog_error -v`: 1 failure, `child.stopped` false after an injected
  control error; added exceptional-path reaping.
- Real harmless-child test initially assumed status 0, observed -15 because EOF
  precedes process reaping. Corrected the test to accept 0/-15 while still
  requiring actual peer EOF and the closed parent descriptor; not a live result.

GREEN commands (real execution, hardware/SDK OFF):

```text
python -m unittest discover -s tests/native_spike -v
Ran 67 tests in 4.350s
OK
python -m unittest discover -s tests/probe -v
Ran 62 tests in 1.659s
OK
cmake -S spike -B build/native-spike -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
-- Configuring done
-- Generating done
cmake --build build/native-spike -j4
[100%] Built target xr-native-spike
ctest --test-dir build/native-spike --output-on-failure
100% tests passed out of 4
python -m tools.native_spike --help
{preflight,run,present,recover}
python -m tools.native_spike present --help
--instance INSTANCE --output OUTPUT --consent-file CONSENT_FILE
build/native-spike/xr-native-spike --help
Presentation-only: python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE.
git diff --check
(exit 0)
```

13 new offline tests cover CLI-to-launcher wiring with zero mutations/no journal,
missing/wrong consent, selected endpoint and peer mismatch, session mismatch,
missing/lost recovery display, parent FD closure at spawn and on failure, actual
harmless-child connection release, interrupt shutdown and exceptional reaping.
Existing suites retain managed-ownership refusal and watchdog timeout coverage.
Expected negative-test diagnostics are not suite failures. Editor LSP lacked
project/generated C++ context; actual compiler and CTest passed.

## Files and remaining acceptance

Modified `tools/native_spike/{__main__,hyprland,supervisor}.py`, native help in
`spike/src/main.cpp`, canonical plan Task 3, Task 3 brief and shared context.
Added `tests/native_spike/test_presentation_only.py` and this report. Historical
`task-3-report.md` remains a record of the prior blocked CLI state; this report
supersedes only its presentation-only wiring status.

Still pending: parent review, explicit runtime consent, visible test pattern,
real non-focusability and independent desktop access, real global-loss behavior,
and normal/crash live teardown. No CTest here connects a real renderer to a
compositor. IPC monitor polling does not prove continuous output incarnation;
only the renderer's retained proxy identifies the selected presentation global.
Managed three-output lifecycle, atomic ownership-safe deletion, capture,
tracking, input and performance qualification remain blocked/unqualified.
