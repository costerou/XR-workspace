# Controlled 60 Hz follow-up

November 4, 2026, America/Detroit (UTC−05). This extends AFTER-PATCH-1247.md. No live services, USB devices, display settings, or installations were changed by this investigation.

## Conclusions

The glasses' whole hub disconnected during full XR at1920×1080@60 with depth3D off and consent `mode_switch=false`. Thus120Hz, stereo3D, and XR's consented automatic mode switch are not required for this observed disconnect. Other host display/workload involvement remains possible. **Cable handling at13:40:59 is unknown**, so the first event must not yet be labelled a confirmed spontaneous failure.

The prior no-host-tracking interval13:03:14–approximately13:05:36 was only about2m22s and ended deliberately for usability. It is not evidence of stability or proof that host tracking I/O is necessary. The tracking60Hz run lasted approximately35m22s before its first recorded hub loss.

The later session end was automatic: the persisted result says `stop_reason=tracking-timeout`, `renderer_returncode=0`, `complete=true`, `error=null`. Renderer metrics were emitted at13:41:59.610; the unit finished13:42:00.232. This is stronger evidence than merely noticing that the end was60seconds after the first loss. It is not a renderer crash or evidence of user Stop.

## What was controlled and observed

Run `aca483725400c828e0a5c9c8cb821b00` started13:05:37. Its consent names tracking=luma, mode_switch=false, and no SBS scope. Trial files are under `~/.local/state/viture-workspace/controlled-tracking-60hz-20261104-130537/`. Startup status and early observations show1920×1080@60, depth phaseoff, trackingtrue and no capture failures. The exit report still has a1920×1080 framebuffer, approximately60Hz presentation timing, and zero capture failures. These are software observations, not physical picture confirmation at failure time. Saved main startup preferences were reported unchanged.

The driver was the previously verified installed hash `917a766dda31eb549aa0f68be44d1f5a442ef0512f2fa058728099629a1e6322`, process2825743 in the early trial observations. It is no longer a live process to rehash. This trial's log at13:40:37 shows `commands=0 starts_skipped=1 opens=1 connections=1 disconnects=0 stale=0`: the device was already streaming at startup. No new stream-start command was required before the first loss.

The prior dark-picture event recovered after normal Stop restored1920×1080@60 at12:58; Cody confirmed picture, then picture in both no-host-tracking XR and tracking60Hz XR. Stop changes several things, so this is a supported observed recovery, not isolation of one cause. The earlier successful compositor screenshot still does not exclude host display/link software.

## Passive wire evidence

Folders134059/134105/134114/134125/134130 contain8 unique chunks after filename/SHA256 duplicate checks,520,860 API0 records, coverage13:39:21.143442–13:42:01.158855. Parsing used the existing48-byte-header reader and validated record boundaries. No kernel ring-loss counter was available. Raw peripheral payloads remain private. `controlled-60hz-audit.json` contains hashes, timing and count metadata.

Windows ending at13:40:59.222753:

| Window | Successful IMU65 completions | Largest gap |
|---|---:|---:|
|Earlier stable30s (−90 to−60s)|29,999|1.504ms|
|Last60s|59,999|1.506ms|
|Last30s|29,999|1.506ms|

Razer9 also had59,999 successful completions in the final60seconds, largest gap1.161ms. There were no nonzero completion/error statuses anywhere on the captured bus before the first root disconnect report, no new Luma OUT/control command, and no MCU85 submission/completion in that retained pre-loss period. Pending reads can exist without new records; eight MCU reads subsequently terminated. Other observed control traffic was routine Razer HID requests. No captured mic/camera transaction supplies a first-loss precursor.

The exact first-loss sequence:

1.13:40:59.221801 — last successful IMU completion.
2.13:40:59.222753 — root reports connection gone with port-power bit retained (`00010100`).
3.13:40:59.223191–.223194 — eight IMU requests complete−108.
4.13:40:59.223203–.223206 — eight MCU85 requests complete−108.
5.13:40:59.223240 — journal parent-hub disconnect.

The first errors therefore follow root detection. The port-power bit is a controller status bit, not a voltage measurement. No saved historical sysfs trace proves physical power continuity or the absence of every host power transition.

## Recovery is a different sequence

| Luma address | Stream-start OUT | Next root disconnect | Interval |
|---|---|---|---:|
|69|13:41:05.344926|13:41:05.402031|57.105ms|
|74|13:41:14.236433|13:41:14.289737|53.304ms|
|77|13:41:24.874420|13:41:24.927113|52.693ms|
|84|13:41:29.620113|13:41:29.670984|50.871ms|

The command is the same five-byte `fa55950000`, once per new enumeration. These closely repeated intervals are a useful recovery hypothesis, not proof of causation: all commands occur in an already failing/re-enumerating episode. They cannot cause the initial command-free loss. Previous12:47 recovery commands generally preceded much longer stable intervals, so this is not a universal command→reset rule.

Addresses69/74/77 each briefly delivered IMU completions;84 delivered no successful IMU but did return the six-byte MCU reply at13:41:29.663533, approximately7.45ms before its root disconnect. Camera−71 errors appear after the first loss. Mic setup failures, port3 power cycling at13:41:22.316, and temporary EACCES opening001/077 at13:41:22.559 are recovery events; they do not establish the initiating cause.

Later address90 received a command at13:41:45.044084 and little sustained IMU data. Driver logs show silent retry13:41:46.052, brief reconnections13:41:48.047 and13:41:53.085, stale indications and silent closes13:41:49.089/54.092. This later sequence reaches the previously documented silent-watchdog limitation; the read-error-backoff patch does not govern every silent-close path. It still does not explain the first loss.

## Offline recovery replay

Before considering a recovery behavior change, the real Python `Supervisor` was exercised with a fake child/clock and simulated measured status. `recovery-replay-1341.json` records three cases:

- Previously healthy tracking, then continuously false status: one clean termination at60s after first false, reasontracking-timeout, one recovery, no forced kill.
- One actual true status during the outage: timeout clock resets; the later outage receives its full60s.
- Explicit Stop: reasoninterrupted, distinct from tracking-timeout.

The existing USB-drop, two-drop, and survive suites passed66tests offline. The actual deployed Supervisor's timeout/status logic matches this checkout; its diff adds continuity-fd plumbing to launch, not changes to that logic. These simulations reproduce the policy, not missing live status datagrams. The renderer emits tracking status about once per second; brief driver reconnection logs or raw IMU bursts do not prove a true renderer status was emitted. The recorded terminal reason establishes that its timeout fired, but the exact sampled tracking history was not retained. No policy change is justified merely from `reconnected` log lines.

The incident bundle `20261104T184159Z-usb-drop.json` was created just before termination and incorrectly remains `session=kept-running`, with null terminal fields and a hardcoded high-confidence hardware attribution. The isolated reporter correction addresses those evidence/reporting defects, not the underlying hub loss. Candidate branch `drop-incident-evidence` is pushed at `c19f4f9` (initial implementation `e4f2cb6`). It separates confirmed disconnect observations from low-confidence/undetermined cause, preserves USB/video ordering, amends exact-run reports after termination, and adds suppressed evidence without duplicate notifications/hooks. Parent review added test-first fixes preserving subsecond ordering and matching repeat counts. Controller suite535passed; delegated supervisor/drop suites91passed; parent recovery replay suites66passed. No install or merge.

## Next decisive experiment

After coordinating with Cody, repeat **full XR at the same1920×1080@60 with host tracking disabled**, for a matched45–60minute window, preferably alternating/repeating with tracking enabled. Keep depthoff, mode_switch=false, scene/topology/workload unchanged. Do not reuse the earlier2m22 interval as the control.

Before that trial, use normal lifecycle controls to ensure the old tracking process is gone. Read-only verification: `pgrep -af 'xr-luma-driver|xrDriver|xr-native-spike'`; consent must say trackingoff, DP-2 readback must be1920×1080@60, and the rolling capture must show no host tracking submissions/commands. Selecting trackingoff alone does not stop an old persistent driver. Closing it also does not prove the glasses' internal stream engine has stopped: this comparison isolates host I/O, not all internal firmware activity. Record handling/contact changes explicitly and classify such events separately.

For the narrower recovery question, a future separately pinned/tested recovery-listen-only condition could withhold the95 start after a physical return while preserving the other variables. Successful no-command recovery would argue the command is unnecessary in that condition; continued hub losses before any command would argue it is not required for the cascade. A silent-but-connected result would simply show a start is needed to resume streaming. `--start listen` is not USB-free and does not make a clean initial-loss control. This is a proposed experiment only, not implemented or run here. Do not blindly remove0x85 reads or lengthen all delays as a claimed root-cause fix.


Reporter implementation evidence and limitations: `../drop-incident-evidence/REPORT.md` in the separate worktree `/home/cody/.t3/worktrees/viture-workspace/t3-3603d49f`. Saved report associations permit terminal amendments after a controller restart, but old reports without associations, missing state, reboot, or a crash between recording end and flushing amendments can prevent updates. The existing live incident is intentionally untouched. This reporting fix does not stop USB drops.
