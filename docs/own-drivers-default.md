# Our own head tracking drivers are the default

Cody's decision, 6 November 2026: our own driver on both pairs of glasses, and XRLinuxDriver gone from the panel but left installed as a hidden backup. Branch `own-drivers-default`. Nothing on this page has been tried in the glasses. The offline tests say the code does what is written here. They do not say how it feels.

## What each pair runs

| Glasses | USB id | Driver | If it cannot start |
| --- | --- | --- | --- |
| Luma Ultra | `35ca:1104` | `xr-luma-driver` (consent `tracking: "luma"`) | XR stops with a plain message. Nothing has moved yet, so the windows are where they were. No fallback |
| Pro 2 | `35ca:1301` | Our Pro 2 SDK bridge, from the installed bundle (`tracking: "pro2-sdk"`) | XR stops and the windows come back. No fallback |
| Beast `35ca:1201`, original Pro, anything else | any other | XRLinuxDriver, automatically and silently. Our drivers do not support these | As before |

There is no choice in the panel. The panel shows one line, on every page, naming the driver in use. During a session that line comes from the run's consent. Before one it says what a start would run, and it turns amber if the driver is not installed or the recovery setting is on.

A Pro 2 needs both the id and the product string `VITURE PRO 2 XR Glasses`. A `35ca:1301` with any other name reads as `unknown`, and `tracking.py` still refuses to start a driver on it.

`3DoF test` left the panel too. It is a build of XRLinuxDriver, so keeping it would keep the other driver on the panel, and its question (do the SDK's cameras cause the drops?) does not apply to our driver, which never reads the cameras. It stays reachable for the A/B in [tracking-3dof-ab.md](tracking-3dof-ab.md) through the recovery setting below.

## The hidden way back to XRLinuxDriver

Luma Ultra only, from a terminal, with no session running. It takes effect at the next Start:

    python -m tools.xr_controller tracking-override set xrdriver    # or 3dof, or luma-cpp
    python -m tools.xr_controller tracking-override status
    python -m tools.xr_controller tracking-override clear

This writes `~/.config/viture-workspace/tracking-override.json`. It does not appear in `--help`. `luma-cpp` is not XRLinuxDriver: it keeps our Luma driver but runs the C++ build instead of a pinned Rust one (`install --rust-driver`, see technical.md, Which driver runs). The panel shows an amber line while it is on, so it cannot be forgotten. A broken or world-readable file counts as no override, because a typo must never be what replaces our driver.

It does nothing on a Pro 2. Those glasses run the bridge or nothing, and `tracking.py` refuses every driver on them. The way back to XRLinuxDriver for a Pro 2 is the rollback below.

## Saved choices

`load_profile` gives each model its driver at every read, whatever `profiles.json` says:

| Saved | On a Luma Ultra | On Beast, unknown |
| --- | --- | --- |
| `xrdriver` (Standard) | `luma` | `xrdriver` |
| `3dof` | `luma` | `xrdriver` |
| `luma` | `luma` | `xrdriver` |
| Missing or invalid | `luma` | `xrdriver` |

Reading changes nothing on disk. The next save writes the new value through, and the schema stays 2, so a rollback reads the file without trouble. A saved `xrdriver` on a Luma Ultra therefore becomes ours without any migration step.

The `unknown` row fixes a real bug. Cody's `unknown` profile holds `luma`, so a Pro 2 on master first ran `xr-luma-driver`, which waited 30 s for a device that was not there, and only then fell back to XRLinuxDriver.

A Pro 2 starts with its own profile, three flat desktops. Tonight's layout stays under `unknown`.

## Pro 2 settings

`pro2-settings.json`, beside `profiles.json`. Each value is read on its own, and one that cannot be read is the default.

| Setting | Default | Change with |
| --- | --- | --- |
| Mount pitch | **-8.5** degrees: what Cody wore for 13 minutes with no tilting. -11 is the alternative, from the bridge's own tilt runs, two usable turns. Do not pick a third value without a run's `turn_axis_forward_deg` | `python -m tools.xr_controller pro2-settings --mount-pitch -11` |
| Pose rate | **60** Hz, until the desk test below proves more | `pro2-settings --pose-rate 120` |
| Projection | 44.5 degrees, approximate | The panel, or `pro2-settings --projection 46.5` |
| Smoothing | On: every bridge session passes `--pro2-test-smooth-ms 35` | Ctrl+Alt+P flips it live for a comparison |

`pro2-settings` with no flags prints what is saved. A change applies at the next Start.

## What the Luma gets from XRLinuxDriver today, and from ours

| Feature | With `xr-luma-driver` | State |
| --- | --- | --- |
| 3D, following the glasses to side-by-side | Follows DisplayPort (`sbs.py`). No driver takes part, and neither one is asked to switch. The consent keeps its mode switch and `sbs_mode` | Same |
| Recenter | The renderer's own (`pose.cpp`, `view.py`) | Same |
| Prediction config | The publisher writes `look_ahead_cfg` 10, 0.6, 10, 40, the Luma Ultra's upstream values (`viture.c`) | Same. Tested in `publish_test` |
| FOV | The publisher writes `display_fov` 52, upstream's value | Same. Tested in `publish_test` |
| Pitch correction | The publisher applies -8.5, as upstream does | Same |
| USB drop during a session | `survive.py` holds the view, then the driver reconnects by itself and keeps its calibration: `luma: reconnected ... calibration=kept`, seen 34 times in the real log. `LumaWatch` restarts a crashed driver 3 times in 5 minutes | Better on paper: the quiet shim is not needed, because there is no SDK to hold back |
| Incident monitor | `luma` is in `tracking.SCOPES`, and its log lines are read (`disconnected`, `stale`, `calibrating`, `error`). Reports say which driver ran | Same |
| Start time | Calibrated in about 1.5 s on the desk, against about 15 s | Better |
| Heading | **Worse, and open.** The SDK's cameras probably give XRLinuxDriver a reference. Ours has none, and drifts about 0.5 degrees a minute, 10 to 30 degrees in 20 minutes, with gyro bias that follows temperature. Recenter corrects it by hand | **Gap** |
| Long-run proof | **Open.** The acceptance gate is 3 hours in total. `tools/luma_soak` has never been run on a real session, and the 162 minutes of stats in the current log are not that gate. Its 38 disconnects, 34 reconnects and 11 clean stops are from mixed use, including unplugging | **Gap** |
| Controls with no public ids | Brightness, dimming and firmware version have no known id. XRLinuxDriver does not set them for us either | Same |

## The Pro 2's gaps against tonight's XRLinuxDriver session

- **A USB drop ends a Pro 2 session.** The windows come back, with no hold and no reconnect. A display drop on tonight's path could hold, because `survive.py` is not Pro 2 aware and the driver reconnects itself. The bridge has no reconnect yet.
- **60 Hz pose against 240 Hz asked for.** That is why the desk test exists. Pose age p50 was 3.1 ms through XRLinuxDriver. Smoothing covers the 15, 15, 20 ms cadence offline, but nobody has compared the two by eye at this mount pitch.
- **The bundle is new.** This exact build has never run in the glasses. Run the desk test before Cody relies on it.

The incident monitor now watches a Pro 2 session's head tracking and frame rate through the renderer's status, as it does other sessions. It does not look for a driver process, because there is none. Route sessions remain controller runs, and supervised bundle runs remain external (`tests/xr_controller/test_pro2_route_ownership.py`).

Swapping glasses is handled. A driver outlives its run, so after a Luma session its driver is still waiting for its glasses, and the bridge would refuse to start beside it. A Pro 2 start now stops that driver first, but only the one this app started, by pid and start tick (`pro2_route.release_tracking_driver`).

## Install

For the orchestrator. It needs XR stopped, and no sudo. Every step here is the orchestrator's: this branch installed nothing. `BRANCH` is the worktree at the commit being installed.

Check nothing is running, in its own step, and read the answer:

    systemctl --user list-units 'viture-xr-*' --no-pager --no-legend
    pgrep -x xrDriver; pgrep -x xr-luma-driver

Keep what rollback needs:

    STAMP=$(date +%Y%m%d-%H%M%S); D=~/.local/share/viture-workspace; C=~/.config/viture-workspace
    cp -p "$D/installed.json" "$D/installed.json.bak-pre-own-drivers-$STAMP"
    cp -p "$D/quiet-driver.pin.json" "$D/quiet-driver.pin.json.bak-pre-own-drivers-$STAMP"
    cp -p ~/.config/systemd/user/viture-workspace-controller.service "$D/controller.service.bak-pre-own-drivers-$STAMP"
    cp -p "$C/profiles.json" "$C/profiles.json.bak-pre-own-drivers-$STAMP"
    readlink ~/.config/omarchy/plugins/cody.viture-workspace > "$D/plugin-link.bak-pre-own-drivers-$STAMP"

Install. This one command pins the renderer, the plugin, `xr-luma-driver` and the quiet shim, builds and pins the Pro 2 bundle, and writes the unit. It refuses a build without `xr-luma-driver`, and a bundle that fails to build leaves the install as it was:

    cd "$BRANCH"
    /usr/bin/python3 -m tools.xr_controller install --build build/release
    /usr/bin/python3 -m tools.xr_controller pro2-route status

Expect `installed: true` and a bundle under `~/.local/share/viture-workspace/pro2/`. The plugin hash is unchanged from the install, so no plugin accept is needed unless the build says otherwise: compare `sha256sum build/release/libxr-ownership.so` with `9587ca18…`.

Then, with XR still stopped, point the panel at the checkout and restart:

    ln -sfn "$BRANCH/omarchy-plugin/cody.viture-workspace" ~/.config/omarchy/plugins/cody.viture-workspace
    systemctl --user daemon-reload
    systemctl --user restart viture-workspace-controller.service
    omarchy-restart-shell

The install is the only change needed for the Pro 2: nothing has to be enabled. Its plugin has the same bytes as the installed one, at a new path, so if Hyprland asks to allow it at the first Pro 2 start, that is expected.

## Rollback

With XR stopped. `D`, `C`, `STAMP` as above. The bundle folders under `$D/pro2` and the old `bin/` folders stay, so nothing needs rebuilding.

    cp -p "$D/installed.json.bak-pre-own-drivers-$STAMP" "$D/installed.json"
    cp -p "$D/quiet-driver.pin.json.bak-pre-own-drivers-$STAMP" "$D/quiet-driver.pin.json"
    rm -f "$D/pro2-bundle.pin.json"
    cp -p "$D/controller.service.bak-pre-own-drivers-$STAMP" ~/.config/systemd/user/viture-workspace-controller.service
    ln -sfn "$(cat "$D/plugin-link.bak-pre-own-drivers-$STAMP")" ~/.config/omarchy/plugins/cody.viture-workspace
    systemctl --user daemon-reload
    systemctl --user restart viture-workspace-controller.service
    omarchy-restart-shell

`profiles.json` needs no restore: the old code reads it. If the new code saved over it, a Luma Ultra simply stays on New driver, with the old fallback. Restore the backup to get back the exact old file. The old code ignores `pro2-settings.json` keys it does not know and `tracking-override.json`.

After a rollback a Pro 2 runs through XRLinuxDriver again, as `unknown`, with the 30 second wait described above unless `unknown`'s saved `luma` is changed.

For a Luma Ultra alone, use the override instead: it needs no rollback.

## Desk test: pose rate, for the orchestrator with Cody

Three runs of the installed bundle, 40 seconds each, glasses on, same chair, nothing else changing. It needs XR stopped, and Cody agrees to each run. Stop on any USB disconnect or picture fault. Between runs, wait for the script to return. The script refuses to start beside any provider, so stop a driver left over from a Luma session first: `python -m tools.native_spike tracking stop`.

    B=$(/usr/bin/python3 -m tools.xr_controller pro2-route status | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["bundle"])')
    export PATH=/usr/bin:$PATH
    "$B/bin/pro2-supervised-parent" --bundle "$B" --pose-rate 60 --stop-after 40
    "$B/bin/pro2-supervised-parent" --bundle "$B" --pose-rate 120 --stop-after 40
    "$B/bin/pro2-supervised-parent" --bundle "$B" --pose-rate 240 --stop-after 40

Each ends with a `MEASURED` line, also saved as `measured.json` in the run's folder. Read `provider`: `samples / seconds` is the delivered rate, and `gap_ms_counts` the cadence. At 120, an uneven cadence matters as much as the rate. Then:

| Result | Meaning |
| --- | --- |
| Delivered rate near 60 at a higher setting | The rate was accepted and ignored. Stay at 60 |
| Near the rate asked for, even gaps, and Cody finds it smoother | Raise it: `pro2-settings --pose-rate N`. That needs no rebuild |
| A refusal, a fault, or no visible gain | Stay at 60 |

Change the default only if the numbers and Cody's eyes agree. All three use mount pitch -8.5 unless `--mount-pitch` is given.

## Test plan, in the glasses

Each step needs Cody. D1 to D5 are the Luma Ultra, D6 to D10 the Pro 2.

| Step | Do | Pass |
| --- | --- | --- |
| D1 | Luma: open the panel | "Head tracking: XR Luma driver", and no Tracking driver box under Startup options |
| D2 | Luma: Start | The confirm box says it does not fall back, `luma: calibrated` in a few seconds, and the picture tracks |
| D3 | Luma: start with the glasses' USB unplugged at the moment of Start, if Cody wants to see it | After 30 s a notification and the panel say head tracking did not start, last waiting on `device-not-found`, and the windows never moved |
| D4 | Luma: hold R1, recenter, change the prediction toggle, in one session | All as before |
| D5 | Luma: `tracking-override set xrdriver`, Start, then `clear` | The panel's line is amber, XRLinuxDriver runs, and the next Start is ours |
| D6 | Pro 2: open the panel | "Head tracking: Pro 2 SDK bridge", and the Startup options note says -8.5, 60 Hz, smoothing on |
| D7 | Pro 2: Start from the panel, with no enable step | The picture holds with no tilt on turns. Stop returns the windows |
| D8 | Luma session, Stop, swap to the Pro 2, Start | Starts without "competing provider" |
| D9 | Pro 2, tilt check at -11, only if -8.5 leans: `pro2-settings --mount-pitch -11` | Better or worse on turns, in Cody's words |
| D10 | Beast, if it comes out | "XRLinuxDriver. Automatic: ...", and a normal session |

## Code

`tracking.default_driver`, `tracking.ensure_ready_luma`, `Controller._tracking_scope` and `_driver_in_use`, `install.install_pro2_bundle`, `pro2_route.py`. Tests: `tests/xr_controller/test_own_drivers_default.py`, `test_state.py`, `test_pro2_route.py`, `tests/native_spike/test_profiles.py`, `test_tracking_luma.py`, the panel's `tst_model.qml` and `tst_launcher.qml`.
