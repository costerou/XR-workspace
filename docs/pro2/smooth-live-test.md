# Pro 2 smoothness: the short live comparison

For the orchestrator, with Cody. This is one supervised run of the new bundle, about 90 seconds, then optionally one 120 Hz desk test. Cody agrees to each run. Nothing here installs anything or touches the controller. Same preflight, consent and launch as the first test. The script needs no sudo.

## Before

XR must be stopped: no `viture-xr-*` unit, no XR-n output. The script checks and refuses otherwise. Replace `RELEASE` with the absolute `pro2-runtime-release` path.

    cd RELEASE
    sha256sum -c SHA256SUMS --quiet
    runtime/bin/pro2-supervised-test --preflight

Expect the source in `release.json`, `plugin_command_path:"validated"`, `sdk_started:false`, `compositor_contacted:false`.

## Run A: the comparison (60 Hz)

    RELEASE/runtime/bin/pro2-supervised-parent --bundle RELEASE/runtime

It gives 30 seconds to put the glasses on, and stops the run after 90 seconds. Ask Cody to:

1. Turn slowly left and right for about 20 seconds, then at normal speed, and look at text.
2. Press **Ctrl+Alt+P**. The pop-up says prediction is off: that is how the first test looked. Turn the same way for about 15 seconds.
3. Press **Ctrl+Alt+P** again. Turn, and stop sharply a few times: does the picture run on past the stop and come back?
4. Try the desktop panel's Add once. It should refuse with a notice, and stay at one desktop.

Stop at once on any discomfort: take the glasses off, and press Ctrl+C in the script's terminal.

The last lines print `MEASURED` and `FINAL_STATE`. `measured.json` in that directory holds the same aggregates. Private logs stay there; do not commit them.

Expected in `MEASURED`:

| Field | Expect | If not |
| --- | --- | --- |
| `complete` | `true` | Follow the run's result; recovery is in runtime-runbook.md |
| `look_ahead_cfg` | `[0.0, 0.0, 0.0, 40.0]` or the same as text | The bundle is the wrong one |
| `predicted_frames` | well over 0; share near 1 less the time prediction was off. Read with `smoothing.max_predicted_deg`, which must be over 0 | Stop: root cause not fixed live |
| `unpredicted_frames` | about 0 | As above |
| `smoothing.snaps` | 0 | Report it: motion is being read as a jump |
| `smoothing.max_predicted_deg` | under about 2.5 | Report it |
| `desktops` | `["XR-1"]` | Report it: the lock failed |
| `fov_source`, `diagonal_fov_deg` | `pro2-supervised-approximate`, `44.5` | Stop: wrong projection |
| `provider.pose_rate_hz` | 60 | Wrong run |
| `provider.gap_ms_counts` | about two thirds in slot 3 (13..18 ms), one third in slot 4 (18..25) | Report it: the cadence changed |
| `interval_histogram` | nearly all in slot 3, as the first test | Report it: frames really were lost |

Then ask Cody: does it now feel as smooth as the other glasses? Any swimming, overshoot or wobble at stops? Was the off period clearly worse?

## Run B: the pose rate desk test (optional, only after Run A is good)

One question: does the Pro 2 really deliver 120 Hz pose in this firmware, and on what cadence?

    RELEASE/runtime/bin/pro2-supervised-parent --bundle RELEASE/runtime --pose-rate 120 --stop-after 40

Cody sits still, then turns slowly. Read `provider` in `MEASURED`:

- `error: capability` or a run that ends early: the SDK refused. Nothing is lost; stay at 60.
- `samples / seconds` near 120, and `gap_ms_counts` mostly in slots 1 and 2: it works. Report the slots: an uneven cadence (for example 5 and 10 ms) matters as much as the rate.
- Near 60: the rate was accepted and ignored.

Only if 120 looks right and Cody finds it better: report it, and the default can change in a later branch. Nothing here changes the default. 240 Hz is accepted by the SDK's check but is not part of this test.

## After

If Run A is good and Cody agrees, the orchestrator can turn on the panel's Start for this exact bundle, once the branch is merged and installed:

    python -m tools.xr_controller pro2-route enable --bundle RELEASE/runtime --evidence STATE/pro2-test-result.json

`pro2-route status` shows it; `pro2-route disable` turns it off. This needs that bundle to stay where it is.

Expect one false `run-killed` incident per supervised run: the installed incident monitor does not know a supervised run's consent. It was seen on the first test at 19:12 and means nothing here.
