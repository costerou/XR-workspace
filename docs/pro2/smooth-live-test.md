# Pro 2 smoothness: the short live comparison

For the orchestrator, with Cody. This is one supervised run of the new bundle, 60 seconds, then optionally one 120 Hz desk test. Cody agrees to each run. Nothing here installs anything or touches the controller. Same preflight, consent and launch as the first test. The script needs no sudo.

## Before

XR must be stopped: no `viture-xr-*` unit, no XR-n output. The script checks and refuses otherwise. Replace `RELEASE` with the absolute `pro2-runtime-release` path.

    cd RELEASE
    sha256sum -c SHA256SUMS --quiet
    runtime/bin/pro2-supervised-test --preflight

Expect the source in `release.json`, `plugin_command_path:"validated"`, `sdk_started:false`, `compositor_contacted:false`.

## The 60 second lean check

    RELEASE/runtime/bin/pro2-supervised-parent --bundle RELEASE/runtime

It uses mount pitch -11 and stops by itself after 60 seconds. Cody sits facing forward with the glasses on, and does each step once. Watch the **middle of the desktop**, such as a line of text, and not the edges.

1. Looking straight ahead: turn about 20 degrees right at a normal pace and stop. Hold for two seconds. Come back. Do the same to the left.
2. Chin down a little, about 10 degrees, and do the same turns.
3. At one held turn, look at the top edge, then the bottom edge. Do they lean the same way, or opposite ways?

Stop at once on any discomfort: take the glasses off, and press Ctrl+C in the script's terminal.

Ask Cody four things:

| Question | Answer | Means |
| --- | --- | --- |
| While moving, does the picture lean? | No | -11 is right in motion |
| | With the turn, as last time | The mount is too large |
| | Against the turn | The mount is too small |
| When held at the end of the turn? | The same as moving | One frame fits |
| | Leans only when held | The capture's +1.13: report it |
| Top and bottom edges at a held turn? | Opposite ways | Perspective of a flat desktop. The Luma does it too. Not a fault |
| | The same way | A true lean: go by the first answer |
| Does chin down change it? | No | Turn axis fixed to the head, as the Luma's capture says |
| | Yes, it flips | Some head pitch is in it. Report it |

Note which session each report belongs to: the bundle, or a normal panel session, which on master runs the Pro 2 through XRLinuxDriver at mount -8.5.

Then read `MEASURED`, printed at the end and saved as `measured.json`:

| Field | Expect | If not |
| --- | --- | --- |
| `smoothing.turn_axis_forward_deg` | Within 1 of 0, with `turn_seconds` over 1.5 | **New mount = -11 + this value.** Run again with `--mount-pitch` at that value, rounded to 0.5 |
| `complete`, `stop_reason` | `true`, `interrupted` | Report them, with `guard_reason` and `provider.failure` |
| `mount_pitch_deg`, `levelled_deg` | -11, 0: gravity levelling is off | Wrong run |
| `predicted_frames` | Near `presented` | Stop: prediction not running |
| `smoothing.snaps` | 0 | Report it |
| `desktops` | `["XR-1"]` | Report it |
| `fov_source`, `diagonal_fov_deg` | `pro2-supervised-approximate`, `44.5` | Stop: wrong projection |

If the measured value and what Cody sees disagree, report both. To see the old behaviour for comparison: `--level-gravity --mount-pitch 0`.

## Run B: the pose rate desk test (optional, only after the lean check is good)

One question: does the Pro 2 really deliver a higher pose rate in this firmware, and on what cadence? XRLinuxDriver already asks for 240 Hz in pose mode, and its `pose_age` of 3.1 ms suggests it gets near that, so try 240 first, then 120.

    RELEASE/runtime/bin/pro2-supervised-parent --bundle RELEASE/runtime --pose-rate 240 --stop-after 40

Cody sits still, then turns slowly. Read `provider` in `MEASURED`:

- `error: capability` or a run that ends early: the SDK refused. Nothing is lost; stay at 60.
- `samples / seconds` near the rate asked for: it works. At 240 Hz nearly every gap is in slot 0, under 7 ms, so the slots say little beyond that. At 120, look at slots 1 and 2: an uneven cadence (for example 5 and 10 ms) matters as much as the rate.
- Near 60: the rate was accepted and ignored.

Only if the rate looks right and Cody finds it better: report it, and the default can change in a later branch. Nothing here changes the default.

## After

If the lean check is good and Cody agrees, the orchestrator can turn on the panel's Start for this exact bundle, once the branch is merged and installed:

    python -m tools.xr_controller pro2-route enable --bundle RELEASE/runtime --evidence STATE/pro2-test-result.json

`pro2-route status` shows it; `pro2-route disable` turns it off. This needs that bundle to stay where it is.

While this runs the panel shows it as an external run and Start stays closed: the controller did not start it. A panel route session, by contrast, keeps its consent in the controller's state directory and shows as the controller's own.
