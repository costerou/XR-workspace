# Pro 2 smoothness: the short live comparison

For the orchestrator, with Cody. This is one supervised run of the new bundle, about 90 seconds, then optionally one 120 Hz desk test. Cody agrees to each run. Nothing here installs anything or touches the controller. Same preflight, consent and launch as the first test. The script needs no sudo.

## Before

XR must be stopped: no `viture-xr-*` unit, no XR-n output. The script checks and refuses otherwise. Replace `RELEASE` with the absolute `pro2-runtime-release` path.

    cd RELEASE
    sha256sum -c SHA256SUMS --quiet
    runtime/bin/pro2-supervised-test --preflight

Expect the source in `release.json`, `plugin_command_path:"validated"`, `sdk_started:false`, `compositor_contacted:false`.

## Run A: tilt and duration (60 Hz)

    RELEASE/runtime/bin/pro2-supervised-parent --bundle RELEASE/runtime

Cody puts the glasses on within the 30 seconds and sits facing forward, looking straight ahead as it starts: that moment sets the level. Then:

1. Turn slowly about 30 degrees right, back, 30 degrees left, back, a few times. Watch the desktop's top and bottom edges: do they stay level, or lean?
2. Leave it running until it stops by itself at 90 seconds. Nothing else is needed. The run must last the whole time.

Stop at once on any discomfort: take the glasses off, and press Ctrl+C in the script's terminal.

Expected in `MEASURED`, printed at the end and saved as `measured.json`:

| Field | Expect | If not |
| --- | --- | --- |
| `complete` | `true` | Recovery is in runtime-runbook.md |
| `stop_reason` | `interrupted`: the parent's own stop at 90 s | Report it with the next two |
| `guard_reason`, `provider.failure` | `null`, absent | They now say what stopped the run: report them exactly |
| `provider.seconds` | about 90 | |
| `levelled_deg` | about 15 to 30 | Near 0: levelling did not run |
| `predicted_frames` | near `presented` | Stop: prediction not running |
| `smoothing.snaps` | 0 | Report it |
| `smoothing.max_predicted_deg`, `peak_speed_dps`, `capped_frames` | Up to 6 is normal. At the cap, `peak_speed_dps` should be over about 200, and `capped_frames` a small share | A cap with a low peak speed: report it |
| `desktops` | `["XR-1"]` | Report it |
| `fov_source`, `diagonal_fov_deg` | `pro2-supervised-approximate`, `44.5` | Stop: wrong projection |
| `provider.gap_ms_counts` | about two thirds in slot 3, one third in slot 4 | Report it |

Then ask Cody: do the edges stay level through a turn? A small lean when the head itself tips is expected and is real. Is it still as smooth?

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

While this runs the panel shows it as an external run and Start stays closed: the controller did not start it. A panel route session, by contrast, keeps its consent in the controller's state directory and shows as the controller's own.
