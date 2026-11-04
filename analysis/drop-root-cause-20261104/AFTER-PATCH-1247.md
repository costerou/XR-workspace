# After-patch loss and black-picture investigation

November 4, 2026; times below are America/Detroit (UTC−05). Follow-up to PRECURSOR-1116.md, not a replacement for its evidence.

## What Cody needs to know

The patched driver was running, but the first new hub loss happened during healthy streaming, without the retry loop the patch addresses. The original cause remains unresolved. The later black picture is a separate observation: at 12:52, a read-only capture of DP-2 contained a complete, correctly duplicated left/right desktop image while Cody reported no picture in the glasses. That narrows the current picture problem beyond the captured compositor image; it does not rule out host display software or establish a hardware fault.

No live state was changed by this investigation. The parent subsequently reported a guarded DP-2-only DPMS off/on, with successful readback; Cody subsequently confirmed the picture remained black. At12:55:31 the parent still observed3840×1200@60 and only3840×1080/1200@60 advertised. Do not repeat DPMS or start another renderer/driver.

## Current picture: evidence and supported recovery

At 12:51:54, the run's status socket reported tracking=true, presented_hz=59.9987, dmabuf capture, capture_failure_run=0. Controller status independently agreed. Renderer PID2709787 remained alive. The full-screen XR layer on DP-2 was at (-3072,9344), size3072×960 logical, opacity1. DP-2 was3840×1200@60, scale1.25, DPMS on. XR-1..3 were present. See black-picture-state.json.

The view was zoomed/follow, selected desktop1; immersive phase=off, stereo=false, and all desktop depth overrides=false. This is not inherently an invalid combination: active source `spike/src/gl.cpp`'s `stereo_eyes()` draws both eyes whenever the framebuffer is at least3:1, independently of depth activation. The screenshot confirms the duplication actually occurred.

Read-only `grim -o DP-2` used the existing renderer's WAYLAND_DISPLAY/XDG_RUNTIME_DIR. Local screenshot `/tmp/xr-black-current-20261104-1253.png` (captured about12:52, filename is not its timestamp), SHA256 `351b59de601adf26753dc9f16465321217f44c1545a61e47cf28991d824b7f64`, shows nonblack browser content in both eyes. It is deliberately not committed: it contains private desktop content. Parent independently saved equivalent evidence at `/home/cody/.hermes/profiles/coder/cache/scratch/black-picture-DP2.png` and `black-picture-status.json`.

Consequences:

- Offscreen pose, zero opacity, and an entirely black rendered scene do not explain the captured instant. Recenter is supported but now a low-value test, not a demonstrated fix.
- A captured image and compositor presentation feedback do not verify physical scanout, link delivery, cable conversion, glasses firmware state, or panel illumination. Sound is a separate path and does not establish picture delivery.
- If the parent's DPMS test does not restore the picture, the supported next step is **Cody holds R1 to return the glasses to2D**. Observe the advertised modes and actual mode, then obtain Cody's physical confirmation. `tools/native_spike/sbs.py` documents this path; the supervisor follows the mode transition. Do not force1920×1200 while the EDID still advertises wide modes only. No USB mode command is implemented or justified.
- This is a recovery experiment, not a guaranteed fix. If it works, record whether the narrow-mode transition and picture return coincide. If it does not, preserve the new status/capture and stop blind resets. A software-only recovery proven to restore this exact dark state has not yet been demonstrated.

The parent's DPMS change occurred after our preserved snapshot and did not recover the picture. The parent is coordinating the R1 transition; its result is pending. No further mutation was made here. A `modetest -D /dev/dri/card1 -c` read-only query failed to resolve its device; no DRM link-status claim is made from it.

## Loaded patch and event ordering

PID2535679 started12:21:10. `/proc/2535679/exe` and installed `bin/4971a4705669/xr-luma-driver` independently hash to `917a766dda31eb549aa0f68be44d1f5a442ef0512f2fa058728099629a1e6322`. Manifest and session consent agree. Incident `20261104T174756Z-usb-drop.json` records sourceff7b26a. The reported12:08 installation is distinct from this run's12:21 start.

The four retained folders124730/124813/124823/124837 contain10 unique chunks, duplicate filenames verified by SHA256:782,166 API0 records covering12:45:41.030821–12:49:01.042759. Each record was parsed as48-byte header plus captured length with bounds checks. No kernel loss counter is available. Raw payloads remain private.

For equal windows ending at root GET_STATUS submission12:47:30.424950:

| Window | Successful Luma IMU completions | Largest completion gap |
|---|---:|---:|
| Earlier stable40s (−100 to−60s) |39,999|1.127ms|
| Last60s |59,999|1.502ms|
| Last30s |29,999|1.102ms|

Changing the endpoint to the rounded journal time can move a boundary record; these numbers use the exact stated endpoint. There were **zero nonzero completion/error statuses anywhere on captured bus1 before root detection**. No Luma outbound command or MCU0x85 submission/completion occurred in that retained pre-loss window. Eight pre-existing MCU reads subsequently ended during teardown, so absence of new records does not mean the endpoint was unarmed.

| Time | Observation |
|---|---|
|12:47:30.424002|Last successful IMU completion|
|12:47:30.424950|Root port5 GET_STATUS submission|
|12:47:30.424954|Status `00010100`: connection gone, port-power bit retained|
|12:47:30.425372–.425375|Eight IMU requests complete−108|
|12:47:30.425387–.425389|Eight MCU0x85 requests complete−108|
|12:47:30.429|Driver logs gone|
|12:47:33.742|Tracking reconnects, calibration kept, gap3318ms|
|12:48:07.298|Original renderer's display connection ends, about37s after first USB loss|
|12:48:13.527308|Next root disconnected status; journal13.528|
|12:48:23.235772|Next root disconnected status; journal23.236|
|12:48:26.412|Open of newly enumerated001/054 denied temporarily|
|12:48:28.095|Tracking reconnects, calibration kept, gap4860ms|
|12:48:36.769354|Next root disconnected status; journal36.770|
|12:48:40.112|Tracking reconnects again|

The first errors follow root disconnect detection: they are teardown evidence, not an observed initiating protocol error. Root power-bit readback is not an electrical measurement.

Razer address9 remains steady:59,999 successful ISO completions in the final60s, maximum gap1.056ms; routine HID reports continue. No captured hub/camera/mic traffic precedes the first loss. This does not measure every electrical packet or internal hub transaction.

Later recovery streams have genuine123–124ms gaps in successful IMU completions:

| Address | Last completion → resumed | Resume before next hub loss |
|---|---|---:|
|46|12:48:11.817535 →11.941041|1.586s|
|50|12:48:21.178769 →21.302276|1.933s|
|54|12:48:34.635103 →34.759103|2.010s|

Each gap had124 successful Razer ISO completions; no new Luma control/OUT/MCU activity or error completions in the surrounding sampled300ms. These are real stream interruptions, not just decoder rejection or a bus-wide recorder pause. They are shorter than the silent-reopen watchdog and do not initiate reopen loops. The first loss has no similar precursor. Mic−71 enumeration failures and the temporary permissions problem occur during recovery, not before the initial trigger.

## What the patch did and did not test

Before first loss, the driver log has one connection, one open attempt, one start command, no EPIPE/EPROTO recovery, and no silent-watchdog disconnect for approximately26m20s. Compare11:15, where initial silence and halt preceded repeated eight-plus-eight queue generations and nine MCU EPROTO episodes. That signature is absent here, like the earlier10:57 first drop.

ff7b26a's `transport.cpp` retains escalating4/8/16/32s cooldown through short read-error sessions, clearing failures after sustained health. Actual observed disappearance clears this history. A healthy connection therefore does not acquire a long retry hold just because the whole hub subsequently disappears. The new event does not exercise the patch's intended repeated-short-error path.

Known limitation remains: `daemon.cpp`'s silent watchdog closes an up-marked TransportLink, reaching `Transport::close()` and clearing transport cooldown history. Its independent silent retry is2–8s. Write-side close paths can also reset history. Neither limitation was an observed precursor to this first loss. Increasing those delays cannot explain/prevent an already occurring healthy-stream hub disappearance.

The temporary EACCES follows enumeration; failed `open()` cannot claim interfaces or submit URBs. It uses the existing separate open-retry backoff. `opens149` counts daemon open attempts, not149 successful USB opens: there were5 actual connections and5 start commands by12:49:10. Four recovery start commands are visible on wire at12:47:33.741101,12:48:17.003866,12:48:28.094441,12:48:40.111686.

## Next decisive software experiment, after picture recovery

Prefer a matched **full-XR run with tracking disabled and no tracking process present**, versus the same full-XR run with the verified patched driver. Keep physical2D mode1920×1200@120, depth off, topology, desktop content and workload identical. Use repeated equal windows of at least45minutes, alternate order, and compare first physical hub loss separately from recovery clusters. Do not run this in the currently blocked session.

Use the normal panel to stop the current run and select tracking off for the experimental run, coordinated by the parent with Cody. Before starting, inspection `pgrep -af 'xr-luma-driver|xrDriver|xr-native-spike'` must confirm no old tracking process remains. Merely selecting tracking off skips `ensure_ready` (`runner.py`) and feed consumption; it does **not** stop a persistent old driver. The parent must use the known lifecycle for that particular process. Do not kill arbitrary matches or claim an isolation test while USB reads continue.

Verify the no-driver condition in raw capture: no continuing tracking submissions/commands from the host. Closing the driver does not guarantee the device's internal stream engine stops; this test isolates **host tracking I/O**, not necessarily all internal streaming state. If the experimental claim requires an internally idle device, establish that independently with Cody before the trial. Existing EXPERIMENTS.md contains the recording/mode-readback procedure; use this newly verified installed hash, not its older driver pin.

If first drops persist without host tracking I/O, prioritize the display/workload/host path. If only tracking-enabled trials fail repeatedly, a tested separate build that omits continuous0x85 reads is the next narrower software experiment; no existing runtime flag does that. Neither a single clean run nor an absent MCU completion proves causality. No additional production patch, installation, merge, or live reset is justified by the present evidence.

Confidence: high that first12:47 loss was not preceded by the11:15 retry storm; high that compositor output was nonblack in the preserved current state; low on the underlying cause of either physical hub loss or dark panels. No hardcoded hardware verdict is adopted.
