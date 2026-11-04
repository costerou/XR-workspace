# Optional device features

`include/luma/features.hpp`, `src/features.cpp`, test `luma_features`. Everything
here is written and tested against a stand-in for the glasses. None of it has run
on a real Luma Ultra.

## The rules

1. **No polling.** A command goes out once per connection, or when explicitly
   requested. Nothing repeats on a timer. A failed command is not retried.
2. **No guessing.** A message id that no public source gives stays empty, and the
   feature reports `unavailable`. Unknown ids are never tried on the glasses.
3. **Commands back off first.** Commands wait for a connection to survive 2 s, run
   one at a time at least 250 ms apart, and stop at 16 per connection.
4. **A timeout stops commands for that connection.** The drop log shows timed-out
   commands right before drops, so the driver does not push on.
5. **A USB drop within 3 s of a command stops commands until the daemon restarts.**
   That cannot prove the command caused the drop. At 37 drops a day a drop lands in
   any given 3 s window about 0.1% of the time, so a false stop is rare and costs
   only an optional feature.
6. **Tracking comes first.** `on_message()`, `tick()`, `pop_event()` and
   `StillWatch::update()` do no I/O and allocate nothing, and the test counts
   allocations to prove it. Features cannot reach the IMU stream: the ids that
   start or stop it (`0x0015`, `0x0301`) are refused as send ids.
7. **Wear actions fail open.** Losing the connection always sends `resume` and
   `saver_off`, so XR is never left paused by a cable.

## What the Luma Ultra is known to expose

Checked on 2026-11-03 from kernel-cached sysfs data and the driver log. The glasses
were not opened.

| Fact | Evidence |
|---|---|
| `35ca:1104` has one vendor-specific interface, held by xrDriver through usbfs. No HID. | sysfs, and pejakovic1/viture-webxr-extension |
| `35ca:1102` "VITURE Microphone" (12 Mbit/s) has HID interfaces 2, 3 and 4. Interfaces 3 and 4 each have a 64-byte IN and OUT report. | sysfs report descriptors |
| Commands on this device use protocol **V1**. | driver.log: `UsbProtocolController created with protocol version: V1` |
| Only two command ids have been seen: `0x0007` (449 failures, the display-mode read) and `0x100B` (4 failures, purpose unknown). | driver.log |

**The published id tables are for V2, and this device speaks V1.** V2 message ids
must not be assumed to work here. That is why the `carina` profile has no command
routes at all.

Interfaces 3 and 4 look like the two-interface MCU and IMU layout that older VITURE
glasses use, which makes them the likely command channel. That is an inference, and
nothing has confirmed it.

## Feature status

| Feature | Status on Luma Ultra | Needs before it can work |
|---|---|---|
| Button and state events | Code done. Ids `0x0301` brightness, `0x0302` display mode, `0x0304` volume come from the older SDK and are unproven here. | A discovery run |
| Wear events | Code done, no id known. | A discovery run |
| Wear guess from stillness | Works from IMU data alone once the IMU stream exists. Thresholds are untested on real data. | A recording to tune it |
| Auto-pause, OLED saver | Code done. They emit events and send no USB. Something has to act on them. | A wear signal, plus a controller or renderer listener |
| Brightness, dimming | Code done, **no id known**, payload format assumed to be one byte. | A usbmon capture of a set command |
| Firmware version | Code done, no V1 id known. The USB `bcdDevice` is available now, but it is not the firmware string. | A usbmon capture |
| Temperature | Code done, **no source on this device**. Older models put it in the raw IMU callback. The Luma Ultra callback has six values and none is temperature. | A protocol finding, or it stays silent |
| USB identity | Works now in tests. The link speed check flags a downgraded connection. | The daemon calling it |

Buttons need a caveat: nothing public describes a raw button press. What public
sources describe is the state change a press causes, such as brightness moving from
3 to 4. A press that changes nothing shows nothing. XRLinuxDriver also says of the
same callback in the vendor SDK: "the SDK doesn't actually reliably call this yet".

## Flags

Prefix `--feature-` turns a flag on, `--no-feature-` turns it off. `flag_table()` is
the source for this table and a test checks the doc against it.

| Flag | Default | USB traffic | Risk |
|---|---|---|---|
| `--feature-state-events` | on | none | None: listens for what the glasses already report. |
| `--feature-wear-events` | on | none | None: listens only. |
| `--feature-temperature` | on | none | None: reads a value already in the stream, if there is one. |
| `--feature-usb-identity` | on | none | None: reads the kernel's cached descriptors. The serial number is deliberately skipped. |
| `--feature-discovery` | off | none | Log noise only. Capped at 8 lines per id and 32 ids per connection. |
| `--feature-still-wear` | off | none | A guess. Glasses on a stand read as off, and so might a very still head. 120 s of stillness by default. |
| `--feature-auto-pause` | off | none | XR could pause while worn if the wear signal is wrong. |
| `--feature-oled-saver` | off | none | The picture could blank while worn if the wear signal is wrong. Blanking is done by the renderer, so it costs no command. |
| `--feature-ipc` | off | none | A dropped datagram loses one event. |
| `--feature-firmware-query` | off | 1 per connection | Commands are the suspect in the USB drops. |
| `--feature-wear-query` | off | 1 per connection | Same. |
| `--feature-control` | off | on request | Lets the daemon pass on explicit requests, paced and capped. |
| `--feature-brightness=N` | off | 1 per connection | Changes the panel. 0 to 8. |
| `--feature-dimming=N` | off | 1 per connection | Changes the lens tint. 0 to `film_levels`. |

Settings: `--feature-profile=carina|gen2` (put it before any map entry),
`--feature-map=KEY=ID`, `--feature-wear-off-ms=N`, `--feature-saver-delay-ms=N`,
`--feature-still-off-s=N`.

Map keys: `ev_brightness`, `ev_display_mode`, `ev_volume`, `ev_film`, `ev_wear`,
`firmware_send`, `firmware_reply`, `wear_send`, `wear_reply`, `brightness_send`,
`brightness_reply`, `dimming_send`, `dimming_reply`, `reply_status` (0 or 1),
`film_levels`.

Only put ids in the map that came from a capture. Firmware update commands live on
the same channel and their ids are not public, so a guessed id is a real hazard.

## Finding the missing ids

Both steps are for Cody to run. Neither is done by any code here.

**Discovery run** (no commands sent). Once `xr-luma-driver` runs, start it with
`--feature-discovery`, then use the rocker, press each button and take the glasses
off and put them on. Each unknown message prints once with its bytes:

    {"src":"luma-feature","ev":"discovered","t_ns":123,"id":"0x0305","len":1,"data":"00"}

**usbmon capture** of the current driver (listening only). This shows the real bytes
for `0x0007` and `0x100B`. It only records. It needs root and an XR session on bus 5:

    sudo modprobe usbmon
    sudo sh -c 'cat /sys/kernel/debug/usb/usbmon/5u > "$HOME/luma-usbmon.txt"'

Press Ctrl+C to stop. Capturing a brightness or dimming change takes something that
sends one, such as the official app. Capturing that way is the only safe route to
those ids.

## Events

One JSON line each, from `format_event()`:

    {"src":"luma-feature","ev":"brightness","t_ns":123,"value":4}
    {"src":"luma-feature","ev":"wear","t_ns":123,"worn":false,"source":"hardware"}
    {"src":"luma-feature","ev":"commands_stopped","t_ns":123,"scope":"until_restart","request":"query-firmware"}

Other names: `volume`, `display_mode`, `dimming`, `pause`, `resume`, `saver_on`,
`saver_off`, `firmware`, `temperature`, `identity`, `link_degraded`, `unavailable`,
`sent`, `rejected`, `timed_out`, `discovered`, `events_dropped`.

With `--feature-ipc` the same lines go as datagrams to
`$XDG_RUNTIME_DIR/xr-luma/events.sock`. A listener binds that path. The socket is a
file path, so it reaches out of the tracking network namespace. With no listener the
line is dropped and the driver never waits.

## Wiring it into the daemon

`Manager` has one owner, the control thread. `StillWatch` lives on the IMU thread.

    Manager features(config);
    if (auto id = find_usb_identity(kSysfsUsbDevices, 0x35ca, 0x1104))
        features.on_identity(*id, now);          // at connect
    features.on_connect(now);                    // on hotplug arrive
    features.on_disconnect(now);                 // on hotplug remove
    features.on_message(id, payload, now);       // every decoded non-IMU frame

    // control loop, 10 Hz or so; this is not USB polling
    features.tick(now, still.last_motion_ns());
    if (auto r = features.next_request(now))
        features.on_write_result(write_frame(*r), now);
    while (auto e = features.pop_event()) log_and_send(*e);

Never call `next_request()` on the IMU thread: the write can block.

## Sources

Reverse-engineered sources are marked. Their authors worked from their own hardware.

- XRLinuxDriver, github.com/wheaney/XRLinuxDriver: `src/devices/viture.c` (state
  callback, firmware log), `include/sdks/viture_protocol_public.h` (callback ids,
  value ranges), `include/sdks/viture_device_carina.h` (IMU callback layout),
  `include/sdks/viture_one.h` at 321cc88 (event ids).
- elasticjava/viture-v2 `PROTOCOL.md`, MIT, reverse engineered with usbmon on a Pro 2:
  V2 frame, query ids, wear reply.
- bfvogel/viture-webxr-extension `VITURE_PROTOCOL.md`: V1 frame, two HID interfaces.
- mgschwan/viture_virtual_display `viture_connection.c`, MIT, reverse engineered from
  the SDK by its author: V1 replies and events.
- pejakovic1/viture-webxr-extension, MIT: the Luma Ultra has no HID IMU.
- Gaming Nexus review of the Luma Ultra: the right-temple button handles dimming, 3D
  and light strips.

The two V1 sources disagree on the checksum (start value and byte order) and on an
end marker, which matters for `protocol.hpp`.
