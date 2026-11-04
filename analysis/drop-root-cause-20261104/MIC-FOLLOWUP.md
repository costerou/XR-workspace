**Follow-up for Cody: no evidence that host software is hammering the VITURE microphone/keys device.** The strongest direct evidence is that the retained recordings show no1102 submissions or completions in the final five seconds before any of seven old-port drops. This does not establish the cause of the newer bus1 drops: those failures were not captured on bus1 in the files available for inspection.

Read-only inspection approximately11:12–11:15 EST, November4. No driver, service, device, input or power settings changed. Analysis artifacts only were written.

**Controller sharing correction**

The glasses at1-5 and Razer at1-1 share controller10:00.0 again. The new-port failures therefore are not a clean experiment excluding shared-controller scheduling. They still show that failure is not confined to controller12:00.4. Earlier failures at10:46:04,10:46:28,10:46:43,10:50:06 and10:57:33 occurred with glasses on5-2 and Razer on1-1, on different controllers: Razer controller sharing is not necessary for those events.

The kernel places the Razer move earlier than the approximate10:45 report:5-1 disconnect10:42:24; Razer1532:0574 identified at1-1 at10:42:38 and again10:43:15. This does not change the classification of the10:46–10:57 drops.

**10:58:08.326 was the G-Wolves receiver, not the glasses.** Port1-5 device2 was33e4:5317 from boot; the receiver subsequently appears on5-2. The QinHeng glasses hub first arrives at1-5 at10:58:15.656. The existing timeline correctly excludes the receiver disconnect from glasses failures.

The microphone runs at12Mbps behind the480Mbps QinHeng hub; hub `bDeviceProtocol=02` advertises multi-TT capability. Its full-speed transactions use that hub's transaction translator. The Razer at root port1-1 is **not downstream of the QinHeng hub**, so sharing an xHCI controller does not mean sharing the glasses' TT. This distinction follows the physical sysfs paths and the [xHCI transaction-translator definition](https://cdrdv2-public.intel.com/625472/625472_xHCI_Rev1_2b.pdf). Shared controller scheduling and QinHeng-specific translation behavior remain different hypotheses. A child EPROTO does not by itself identify a TT defect.

**Recovered missing error text and order**

- 11:00:07.003253: parent1-5 disconnect.
- 11:00:08.502227: `usb 1-5-port3: cannot reset (err = -71)`.
- 11:00:08.905238,09.144227,09.778231,10.016300: `Device not responding to setup address.`
- 11:00:09.357232 and10.229238: `device not accepting address 17/18, error -71`, with invalid-context warnings.
- 11:00:10.235227: `usb 1-5-port3: attempt power cycle`.
- 11:00:10.912239: microphone successfully identified as35ca:1102, address19.
- 11:00:11.077230: malformed HID report descriptor, then probe failure-22.

Thus the kernel's child-port power-cycle is a recovery action more than three seconds **after** the first parent loss. It cannot explain that earlier11:00:07 event. It could still be relevant to later instability; the trace does not rule that out. The DP disconnect observed at11:00:31 follows the next USB event11:00:30.507, but the existing DP timestamp is a sampled observation, not an exact electrical edge.

**HID/input audit**

The full missing probe text is `unbalanced collection at end of report description`, followed by `probe with driver hid-generic failed with error -22`. Upstream HID parsing reports this when collection nesting remains unbalanced; this is descriptor parsing, not a report-rate or CPU-load message ([Linux HID parser](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/05e96e96a315fa49faca4da2aedd1761a218b616/drivers/hid/hid-core.c)).

The Nov3 portion of the previous boot has36 microphone enumerations,36 malformed-descriptor failures and72 successful hidraw interfaces. The current boot has19,19 and38 respectively. Each failed HID instance fails once; there is no same-instance retry storm. These totals include boot/setup/manual events, not just spontaneous drops.

Current bindings at1-5.3:

- Interfaces0/1 bind `snd-usb-audio` for audio control/streaming.
- Interface2 binds USB `usbhid`, but its HID child has no HID driver after parsing fails.
- Interfaces3/4 bind `usbhid` and `hid-generic`, exposing hidraw7/hidraw8 without input/event nodes.
- The glasses subtree's event24 belongs to the Sonix camera, not1102. Hyprland has the camera's event node open; this is not evidence of microphone-key traffic.

No readable userspace FD owns either1102 hidraw node or a USBFS node. No keyd service/config/process or input-remapper/xremap/kmonad/kanata/interception process was found. Scoped Omarchy/Hyprland/udev/input configuration searches found no mic-specific handler. This is not an audit of inaccessible root process FDs, and it is a snapshot with XR stopped.

**Audio audit**

VITURE is ALSA card4: `/proc/asound/card4/pcm0c/sub0/status` is `closed`. WirePlumber holds `/dev/snd/controlC4`, which is an audio-control handle, not an active microphone recording stream. PipeWire is capturing the Razer microphone at card0; no VITURE PCM FD is open. The local WirePlumber VITURE-named rule changes display audio labels and does not introduce a polling loop. Historical opens while XR ran cannot be reconstructed from this later snapshot alone.

**Direct capture check**

[mic-audit.py](mic-audit.py) joins current-boot kernel identities to the retained bus5 captures, checks48-byte API0 record framing, and retains metadata only. It refuses address reuse across different product identities. [mic-audit.json](mic-audit.json) records the result.

For10:38:58,10:46:04,10:50:06 and10:57:33: **zero microphone events in the preceding30seconds** (last10ms excluded to separate teardown). For10:46:28,10:46:43 and10:57:44:48 microphone events each, all control submissions/completions from the preceding re-enumeration, with one stalled control completion. That is24 transfers, not48 independent commands. There is no microphone iso/audio or HID-interrupt stream in those windows, and no microphone event in the final five seconds for any of the seven drops.

usbmon records URB submissions/completions, not every electrical packet, NAK, split transaction or outstanding hardware poll. Absence of these events excludes a visible command/completion storm, not every possible low-level TT interaction. The retained windows do not show all earlier device state changes.

The recorder now actually runs `usb_rolling_capture.py 1 1-5`, confirmed from its command line. Its code selects bus/hub from those arguments. At inspection the newest saved drop folder was still10:57:45; no bus1 failure snapshot was available. Two closed bus1 rolling chunks from11:12:40 and11:13:00 span40seconds and have no microphone-address26 events; [bus1-idle-audit.json](bus1-idle-audit.json) preserves aggregate headers only. XR was stopped, so this is a baseline, not evidence about11:00. Rolling source files can rotate away.

**What to do next**

Keep the existing bus1 recorder running under the orchestrator's control. At the next spontaneous drop, verify its captured bus header and kernel address mapping, then inspect1102 control, interrupt and iso traffic plus hub port-status/TT requests *before* the first loss. Separate enumeration after one failure from the next failure's possible precursor.

The priority remains the tracking-versus-display split in [EXPERIMENTS.md](EXPERIMENTS.md). If a new trace implicates1102, a separate approved trial can disable only its audio or HID binding, one at a time, keeping Razer capture/topology and XR mode fixed. Do not blanket-stop PipeWire or key/input daemons: that changes unrelated Razer traffic too. Such driver-binding experiments need a concrete restoration plan and can change power management; they are not equivalent to removing the device's TT electrically. No such change was made or is justified as a fix by the current evidence.
