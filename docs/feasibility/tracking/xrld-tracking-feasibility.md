# Head tracking feasibility: XRLinuxDriver, network-blocked (Luma Ultra)

Date 2026-10-31. Backend chosen by the user: the installed XRLinuxDriver (`xr-driver-git` 2.11.9, GPL-3.0-or-later, https://github.com/wheaney/XRLinuxDriver). It bundles VITURE's proprietary `libGlassSDK.so` and `libcarina_vio.so`. The driver runs outside its systemd unit, which remains disabled.

## Privacy constraint and how it is met

`xrDriver` POSTs a MAC-derived hardware ID to `https://eu.driver-backend.xronlinux.com/licenses/v1` (bundled libcurl) at start and daily. At the user's direction it runs with no network access:

```sh
unshare --user --map-current-user --net -- timeout ... /usr/bin/xrDriver
```

The namespace holds only `lo`, and outbound connections fail. With no non-loopback interface the driver logs `Failed to get hardwareId` six times over ~30 s, then `No hardwareId found, not retrieving license`, and carries on. Startup is therefore ~30 s slower. USB access works through the device ACL. USB hotplug events are probably not delivered inside the namespace (untested); the glasses must be connected before start.

## Configuration (`~/.config/xr_driver/config.ini`, new file)

`disabled=false`, `output_mode=external_only` (no mouse or joystick output), `metrics_disabled=true`. `dead_zone_threshold_deg` keeps its default of 0, so the published orientation is unsmoothed.

## Pose interface used

- **Used:** SysV shared memory keyed by `ftok("/tmp/shader_runtime_pose_orientation", 0)`. It holds 16 floats: three quaternions `x,y,z,w` (current, then about 16 ms and 32 ms earlier) and their device timestamps in ms. A process-shared mutex lives in `/tmp/shader_runtime_pose_orientation_mutex`, and a `disabled` flag sits in `/tmp/shader_runtime_disabled`. Upstream source writes it whenever the IMU is calibrated, with no licence check.
- **Not used:** the `/dev/shm/breezy_desktop_imu` feed, which needs the licensed productivity tier. Position (6DoF) is also licence-gated; the spec only needs 3DoF.

## Live result (`xrld-luma-pose-live.json`)

The glasses calibrated still on the desk (~12 s after connect), then the user wore them and moved for 20 s:

| Movement | Time | Result |
|---|---|---|
| — | whole window | ~1,000 distinct updates/s, `disabled` false |
| Look left/right | 2–8 s | yaw swept ~23° → 132° (~110°) |
| Look up/down | 11–14 s | pitch +31° → −48° |
| Tilt | 16–19 s | roll moved through about ±40° around its offset |

The large rest offsets (yaw ~70°, roll ~160°) come from the driver centring on the desk pose at calibration. The app must recenter, and must establish axis conventions against its own frame.

- **Wrong-glasses control:** an earlier attempt (`xrld-wrong-glasses-flat-live.json`) stayed within ±0.2°, because the user was wearing a different pair. It still shows the feed carries no false motion while the glasses rest.
- **Not measured:** motion-to-photon latency, drift over time, prediction, Beast behaviour, reconnect, or comfort. The ~1 kHz figure is the IPC update rate, not a rendered rate.

## Next for the tracking adapter

- **Reader:** a C++ reader for these segments in the renderer process. It reads under the mutex, checks timestamps, and treats stale timestamps or `disabled=true` as tracking loss.
- **Lifecycle:** a supervised driver lifecycle (network-blocked start, readiness on calibration, clean stop).
- **Frames:** recenter, and axis and frame conventions. Late pose sampling in the presentation loop.
