# USB hub drop analysis — 4 November 2026

All times below are EST (UTC−05:00). Analysis is offline/read-only; no device, service, driver, compositor, controller or sysfs changes. Raw captures and serial-bearing logs are not included.

## Finding for Cody

The evidence strongly locates the failure at the glasses' upstream USB hub/link, rather than the XR application's traffic. It does **not** distinguish a brief loss of USB-leg power, a cable/contact/adapter fault, or an internal hub reset. My leading hypothesis is the cable/adapter/WJESOG USB power path (moderate confidence as a group; low confidence assigning one component). Internal QinHeng hub firmware/hardware is the next plausible explanation (low-to-moderate confidence). A shared-controller quirk with the Razer remains possible (low confidence), but bandwidth exhaustion and a misbehaving Razer are poorly supported. A DP/USB-C mode interaction is possible but currently low confidence: the observed SBS transition is later, not simultaneous.

**Experiment started by Cody at 10:45 EST on Nov 4:** the Razer dongle moved from bus 5 to **1-1 on bus 1, controller 0000:10:00.0**. The glasses remain **5-2 on controller 0000:12:00.4**, alone on their USB2 root. This is a different PCI USB controller, so it implements the proposed isolation test. This topology and start time are supplied by Cody; no hardware action was performed by this analysis.

**Interpretation boundary: 2026-11-04 10:45 EST (15:45 UTC).** Any subsequent glasses drop, while this arrangement remains in place, occurs with **no Razer on bus 5**. Such a drop rules out sharing that USB2 root/controller with the Razer as a necessary trigger for that event and shifts attention toward the glasses cable, adapter/USB power path, and internal hub. It does not exclude system-wide electrical or software effects from a dongle on another controller. Keep the same workload and SBS transitions for several hours and record drop timestamps and any intervening topology/cable/power changes. One clean 30-minute run is weak evidence for a rare failure; a longer drop-free run strengthens, but does not prove, the sharing hypothesis. A later matched comparison with the original arrangement is optional if Cody wants to establish causality.

If it still drops: change only the WJESOG USB feed to a separately powered USB hub, keeping DP and the glasses cable identical. Then test a known-good glasses cable separately. A powered hub changes both electrical supply and USB topology, so improvement alone is not proof of undervoltage. Definitive power diagnosis requires voltage recording at the adapter's USB leg during a drop. The Razer move was performed by Cody; the powered-hub and cable tests remain pending. No experiment was performed by this analysis.

## Capture validation and limits

These are **48-byte API0 read() headers**, followed by len_cap bytes, not 64-byte ioctl headers. tools/luma_capture/rawmon.py documents the same layout. A 64-byte parser loses alignment immediately. summarize.py validates event type, timestamps and record bounds, walks every record, aggregates headers/iso descriptors, and never outputs packet payloads. Seven chunks in the main drop folder cover 10:37:12.292581–10:39:32.298937 (140.006 s), 556,794 events. Every file parses exactly to EOF. There is no recorder loss counter in this API0 dump; complete record framing does not prove the kernel lost no events. URB payload rates are not electrical bus utilization or reserved periodic bandwidth.

The nominal drop-free file covers 00:19:46.227287–00:43:17.119107 (1,410.892 s), 8,938,253 events, **and includes the kernel-confirmed 00:22:38 disconnect/re-enumeration**. Use 00:23:30–00:43:10 (1,180 s) as its stable comparison interval. Do not describe the entire file as drop-free.

## Main drop: devices, endpoints and rates

Address mapping comes from the boot kernel enumeration: root hub 1; Razer 2; QinHeng hub 34; Sonix camera 35; VITURE microphone/keys 36; Luma Ultra 37. They become 38/40/41/39 respectively after the drop. Both Razer and microphone are full-speed; the hub, camera and Luma are high-speed. Bus 5 is xHCI 0000:12:00.4, USB2, with two root ports.

| Device/endpoint | Final five complete seconds, 10:38:53–58 | Status |
|---|---:|---|
| Razer 2 / iso IN 0x81 | 5,000 completions, 480,000 bytes; 1,000/s, 96,000 B/s | All URBs and all 5,000 iso descriptors successful |
| Razer 2 / control OUT 0 | Two 64-byte HID SET_REPORTs; 128 bytes | Both successful; ordinary pair in repeating ~6.01 s cadence |
| Razer 2 / iso OUT 0x01 | No events | Playback endpoint inactive before drop |
| Luma 37 / interrupt IN 0x81 | 5,000 completions, 140,000 bytes; 1,000/s, 28,000 B/s | All successful |
| Luma 37 / bulk IN 0x85 | No completion until teardown | Eight pending requests later shut down; absence of completions is not absent outstanding URBs |
| Luma commands OUT 0x04/0x06, camera bulk IN 0x82, event IN 0x83 | No events | No camera stream or command burst |
| Hub 34 / control and interrupt 0x81 | No events in final five seconds | Pending hub interrupt later shut down |
| Camera 35 / control, interrupt 0x83 | No events in final five seconds | Earlier power-management activity described below |
| Microphone/keys 36 | No events anywhere in the retained 140 s | No active stream visible |

Across 105 complete pre-drop seconds (10:37:13–10:38:58), Razer IN counts are 999–1,000/s, 104,996 total; Luma IMU counts 999–1,001/s, 104,998 total. The final seconds do not show increasing traffic, retries, protocol errors or IMU degradation. The final 30 complete seconds have 29,999 successful completions each on Razer IN and Luma IMU.

Approximate active payload immediately before failure is 124 kB/s total (0.992 Mbit/s), excluding protocol overhead and the sparse control traffic. Razer's 96 kB/s alone is 0.768 Mbit/s on its full-speed link. This is not evidence of USB2 saturation. Separate root ports do not necessarily share a single 12 Mbit/s wire budget. usbmon does not expose the controller's periodic schedule/reservations; exact bandwidth admission accounting cannot be reconstructed here.

### Error chronology and local USB power management

- **10:38:46.544–46.585**, hub 34 port 2 (the camera): GET_STATUS reports 0x0507 (connected/enabled/suspended/high-speed/powered). CLEAR_FEATURE PORT_SUSPEND follows; status becomes 0x0503 with suspend-change set, then the change is cleared. This is a camera-port resume, not the parent hub disappearing.
- **10:38:46.596–46.597**, camera GET_STATUS succeeds (0200), then device CLEAR_FEATURE(REMOTE_WAKEUP) stalls: one **-EPIPE (-32)** on ep0. Three camera interrupt requests complete **-ENOENT (-2)** immediately afterward. No camera video bulk stream appears. Similar control stalls and interrupt cancellations occur during setup in the older capture; this is not a unique pre-drop signature.
- **10:38:49.097**, hub SET_FEATURE(PORT_SUSPEND) on camera port 2 succeeds. Thus camera runtime suspend/resume happened despite cameras never streaming. This is the only visible local power-management precursor; causal significance is unproven, and it affects a child port, not the whole hub.
- **10:38:58.510539**, root hub port 2 GET_STATUS returns **00010100**: port power remains reported, connection is clear, connection-change is set. This confirms host-observed link loss, not a host request to remove power. A reported power bit cannot measure voltage at the glasses.
- **10:38:58.511432 onward**, Luma 37: eight ep0x81 and eight ep0x85 completions **-ESHUTDOWN (-108)**. Hub 34 ep0x81 has one -108. These precede the printed disconnect by less than 1 ms and are consistent with teardown, not a preceding protocol-error storm.
- Kernel parent disconnect **10:38:58.512235**; Luma child .512698; camera .541235; microphone .573041. Children are removed in sequence as a consequence of losing their common parent.
- Re-enumeration starts **10:38:58.855319** (343.084 ms after parent disconnect); Luma enumerates at 10:38:59.368232, its descriptor at .454247. Driver reports reconnect **10:39:01.770**, gap **3,260 ms**, calibration kept; session remains alive. Exactly one six-byte ep0x04 command and one six-byte ep0x85 reply occur after reconnect.
- Post-reconnect camera initialization produces 136 -ENOENT interrupt cancellations; microphone ep0 has one -EPIPE. These are after the failure.
- Razer playback ep0x01 starts later and has three **-ECONNRESET (-104)** completions at **10:39:20.3886**, plus 12 iso descriptor **-EXDEV (-18)** statuses in the full capture. These are ~22 s after the USB drop, during playback cancellation/alternate-setting activity. Razer IN remains successful through the hub drop: 1,000 completions in 10:38:58–59.
- **No -EPROTO (-71)** anywhere in the retained main capture. No Razer error or iso packet error before the disconnect. No submission-error events indicative of -ENOSPC bandwidth rejection.

## Old driver comparison

Stable interval 00:23:30–00:43:10, with Luma address 13:

| Endpoint | Successful/observed completion rate | Payload rate |
|---|---:|---:|
| Razer iso IN 0x81 | 998.75/s | 95,880 B/s |
| Razer iso OUT 0x01 | 187.27 URBs/s, multi-packet | 191,759 B/s |
| Luma camera bulk IN 0x82 | 1,158.08/s | 18,974,005 B/s |
| Luma IMU interrupt IN 0x81 | 998.77/s | 27,965 B/s |
| Luma event interrupt IN 0x83 | 119.85/s | 1,918 B/s |
| Luma bulk OUT 0x06 / IN 0x87 | ~1/s each | ~511 / 510 B/s |
| Luma command OUT 0x04 / reply IN 0x85 | None in selected stable interval | 0 |

The full file has 98 ep0x04 command completions, 98 successful ep0x85 replies plus 48 cancelled replies, concentrated before the stable interval; camera and IMU rates quoted above are captured rates, not guarantees of no capture loss. All Razer iso URB and packet statuses in the full old file are successful. No -EPROTO. The old file's startup has control -EPIPEs and camera interrupt -ENOENTs, so those alone are weak fault indicators. The stable interval carries ~19.29 MB/s aggregate payload versus ~0.124 MB/s immediately before the new drop. Camera load and frequent driver commands are not necessary for the new failure.

## Kernel, GPU, display and power evidence

journalctl -k -b supplies the complete boot kernel chronology; boot begins Nov 3 at 23:31. Around 10:35–10:41 there are no logged xHCI controller faults/resets, hub overcurrent, amdgpu reset/timeout, or system suspend/resume. Normal re-enumeration messages are present. No detailed xHCI port debug trace is recorded. Reading dmesg debug failed with “Operation not permitted”; no privilege escalation or debug enabling was attempted. Absence of logged overcurrent is not evidence of stable USB voltage.

RX 7800 XT is confirmed by renderer exit telemetry (radeonsi/navi32). The renderer exiting at 10:39:14 reports buffer **1920×1200**, stereo **frames=0**, depth_updates=1, draw/swap mean 136 µs (max 29,109 µs) over its lifetime. That supports a 2D renderer immediately before the later transition, and does **not** establish a depth inference workload at 10:38:58. The short renderer exiting at 10:39:24 has buffer **3840×1200**, consistent with the intervening SBS interval. Depth updates=1 is not proof of continuous inference. No time-resolved GPU utilization, board watts, clocks, thermal or PSU/USB voltage trace was found in the available incident/journal evidence. Those historical quantities cannot be recovered by reading current device state.

The supplied DP observations place 3840×1200@60 at ~10:39:14 and return to 2D at ~10:39:24, respectively ~15.5 and ~25.5 seconds **after** USB loss. The existing Hyprland log corroborates the ordered DP-2 disconnect/reconnect, modeset to 3840×1200@60, then 1920×1200@120, but has no timestamps. The incident's synthesized timeline labels a mode transition at 10:39:24 in the opposite direction and should not be used as an authoritative modeset trace. Its earlier 'tracking-lost' labels correspond to ~123–124 ms stale intervals that recovered, not further kernel USB drops. Exact DP link training parameters and continuous pre-drop 3D state were not logged; mode intent in consent files is not proof of actual link state.

## Every usb 5-2 parent disconnect since boot

Exact kernel timestamps; all Nov 4 EST. Child removal events are not counted as separate parent failures.

| Parent disconnect | Next enumeration | XR / driver correlation | 3D and DP evidence |
|---|---|---|---|
| 00:22:38.273278 | 00:22:43.115232 | Prior XR attempts failed by 00:22:06; next long session 0272 starts ~00:22:48 using xrlinuxdriver. Capture contains this setup drop. | No proven active SBS; intended 1920×1200@120. Actual mode at instant unknown. |
| 00:57:58.363230 | 00:57:58.700235 | Session 0272, xrlinuxdriver, still running until 00:58:17.93. | Renderer exit at 00:58:17 has 1920×1200 buffer, no stereo metrics. Exact DP event times unavailable. |
| 00:58:17.522237 | 00:58:21.816237 | Same session; exits ~408 ms later with status 2. | 2D buffer at exit; exact DP chronology unavailable. |
| 02:39:24.088264 | 02:40:52.498233 | Cody unplugging (user attribution); session 605b using xr-luma-driver d97faff8878f exits .514. | 1920×1200 exit buffer, no stereo metrics. |
| 02:42:16.827230 | 02:42:19.279233 | Cody unplugging; **Beast**, session 7c6b using xrlinuxdriver, exits 02:42:17.217. Do not classify all boot drops as Luma failures. | 1920×1200 exit buffer; consent mode_switch=false. |
| 02:43:44.348230 | 02:46:12.936232 | Beast hub 26; prior session already ended; another manual-swap-period event, intent not independently proven. | No proven running XR/SBS session at instant. |
| 03:23:08.715242 | 10:06:58.616230 | Unknown cause; session 15cd using xr-luma-driver 6cde3037906f had already exited **03:21:48.455**. Same driver SHA as morning build. | 2D exit buffer ~80 s earlier; no proof of SBS at disconnect. |
| 10:38:58.512235 | 10:38:58.855319 | Session ba3e, xr-luma-driver 6744ab90ce82; reconnect/calibration/session survived. | 2D supported by subsequent exit telemetry; observed SBS transition ~15.5 s later. |

Older raw folders were also parsed. The deliberate 02:39:24 unplug produces the same pattern of successful traffic followed by -ESHUTDOWN on pending IMU/reply/hub requests, demonstrating that this error pattern cannot identify the physical cause. At 03:23, the Luma transfers were already cancelled (-ENOENT: eight reply and seven IMU requests) when XR exited; only the parent hub's pending interrupt shuts down at the later disconnect. There is no recorded return until 10:06:58. This is compatible with glasses being left disconnected, but does not prove intent.

## Confidence and unresolved alternatives

- **High:** loss of the common parent hub/link; not solely an IMU endpoint stall, bad pose, application crash, or camera-stream overload.
- **Moderate:** cable/adapter/USB-leg power path as the leading physical explanation, taken together. No voltage measurement or controlled cable comparison identifies the component.
- **Low-to-moderate:** internal hub reset/firmware/hardware. Fits whole-hub loss and quick return, indistinguishable from a brief electrical interruption in these logs.
- **Low for the 10:38 event:** Razer/controller interaction. Razer was stable through failure and heavier traffic previously worked. Cody isolated it onto controller 0000:10:00.0 at 10:45 EST; any subsequent drop with that topology is not a shared-bus-5 Razer event. No post-move outcome is asserted here.
- **Low:** DP/USB-C mode interaction for this specific event. Known transitions are later. Continuous load or undocumented adapter behavior remains unexcluded.
- **Very low:** ordinary bandwidth exhaustion as the direct cause. Much heavier traffic survived, no admission errors, no pre-drop iso/protocol error storm.

Camera streaming and command frequency are ruled out as necessary triggers, **not all firmware interactions**: the idle camera did undergo ordinary USB port suspend/resume before the drop. Do not patch the XR driver based on this capture alone.

## Reproduce the offline summaries

From the repository root (outputs deliberately outside the repository):

```sh
python analysis/usb-drop-20261104/summarize.py \
  ~/.local/state/viture-workspace/usb-captures/drops/20261104-103858/*.bin \
  --out /tmp/drop-summary.json
python analysis/usb-drop-20261104/summarize.py \
  ~/.local/state/viture-workspace/usb-captures/usbmon5-xr-20261104-001946.bin \
  --out /tmp/baseline-summary.json
```

JSON rows are `[epoch_second, device_address, endpoint, transfer_type, event, count, urb_bytes, status_counts, iso_packet_bytes, iso_status_counts]`. Transfer types 0/1/2/3 are iso/interrupt/control/bulk. Submission status -115 is normal pending state and is excluded from completion-error lists. For rates, select completion rows and divide by the selected window duration; do not add submission and completion bytes. Listed individual error/control events are capped at 10,000; aggregate rows are uncapped. Neither cap was reached on the captures analyzed. The main pre-drop 105-second window is `[1793806633,1793806738)`; the stable baseline is `[1793769810,1793770990)`.
