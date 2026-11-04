# VITURE Luma Ultra USB protocol

Research notes for the `xr-luma-driver` swarm. Written 2026-11-03 against XRLinuxDriver
2.11.9 (commit 36e2139, the installed package) on one Luma Ultra. Source ids such as [S4]
are listed in [sources.md](sources.md).

Every fact carries a tag:

| Tag | Meaning |
|---|---|
| **C** | Confirmed from source: read in a named file, or observed on this machine in sysfs, the kernel log or the driver log |
| **I** | Inferred: follows from confirmed facts, but nothing states it directly |
| **U** | Unknown |

This research did not open the glasses, did not send them a byte, and did not disassemble
or run `libglasses.so` or `libcarina_vio.so`.

## Read this first

1. **The IMU packet layout and the command that starts the stream are not public.** No open
   source documents them for the Luma Ultra. `luma::parse_imu` and the start command cannot
   be written from open knowledge alone. One USB capture of a normal session should answer
   both ([capture plan](#capture-plan)), and Cody has to decide whether to take it.
2. **The Luma Ultra has no HID interface.** It has one vendor interface with seven bulk and
   interrupt endpoints, and no hidraw node. `luma::Transport` has to use usbfs.
3. **`0x0007` is `get_display_mode`, sent on the bulk data pair in 512-byte frames.** It is
   sent because XRLinuxDriver's state thread asks about side-by-side mode on every loop. It
   has nothing to do with tracking.
4. **The `0x0007` errors probably follow the drops and do not cause them.** No run of driver
   retries in the logs started without a hub disconnect just before it. The log cannot show
   whether the polling helped provoke the drop.
5. **XRLinuxDriver runs the Luma Ultra in 6DOF by default.** That means the side cameras and
   host-side VIO are running, not just an IMU. This is the largest difference from "plain
   display" that the sources show, and it is the first thing to rule out.
6. **Endpoints 0x04/0x85 also carry firmware flashing.** Nothing may send an unknown opcode
   there. See [Never send](#never-send).

## Device

### USB tree

Read from sysfs, which holds the kernel's cached copy and does not touch the device.

| Port | ID | Name | Speed | Class | **C** notes |
|---|---|---|---|---|---|
| 5-2 | `1a86:8091` | USB HUB | 480 | hub | QinHeng hub, `bcdDevice` 13.10. **I**: inside the glasses, since their camera and microphone hang off it |
| 5-2.1 | `35ca:1104` | VITURE Luma Ultra XR GLASSES | 480 | vendor | The device this document is about |
| 5-2.2 | `0c45:636b` | USB 2.0 Camera | 480 | UVC | The pass-through camera. [S9] gives the same VID:PID |
| 5-2.3 | `35ca:1102` | VITURE Microphone | 12 | audio + HID | See [The 1102 companion](#the-1102-companion) |

All of it hangs off AMD xHCI `0000:12:00.4`, bus 5. Device numbers change on every
re-enumeration, so code must match on VID:PID.

### 35ca:1104 descriptors

**C** (sysfs `descriptors`, raw bytes below):

```
device    12 01 10 02 00 00 00 40 ca 35 04 11 01 00 10 20 00 01
config    09 02 43 00 01 01 30 c0 01
interface 09 04 00 00 07 00 00 00 40
ep        07 05 81 03 00 04 03
ep        07 05 82 02 00 02 00
ep        07 05 83 03 00 02 04
ep        07 05 04 02 00 02 00
ep        07 05 85 02 00 02 00
ep        07 05 06 02 00 02 00
ep        07 05 87 02 00 02 00
```

- USB 2.10, EP0 packet size 64, `bcdDevice` 0x0001, no serial number string.
- One configuration, value 1, self-powered, `bMaxPower` 2 mA.
- One interface, number 0, alternate 0, class/subclass/protocol 00/00/00, string "VITURE XR
  GLASSES". No kernel driver binds to it.
- The udev rule `70-viture-xr.rules` from the xr-driver package gives the logged-in user
  access through `uaccess`. The ACL shows on the `/dev/bus/usb/005/*` node.

| Address | Type | Max packet | Interval | Purpose | Tag |
|---|---|---|---|---|---|
| `0x04` | bulk OUT | 512 | | MCU commands, flashing | **C** [S4] |
| `0x85` | bulk IN | 512 | | MCU replies | **C** [S4] |
| `0x06` | bulk OUT | 512 | | Data envelope requests (the `FF FE` frames) | **C** [S4] |
| `0x87` | bulk IN | 512 | | Data envelope replies (the `FF FD` frames) | **C** [S4] |
| `0x81` | interrupt IN | 1024 | 500 µs | Unused by every open source | **U** |
| `0x82` | bulk IN | 512 | | Unused by every open source | **U** |
| `0x83` | interrupt IN | 512 | 1 ms | Unused by every open source | **U** |

**I**: a 1 ms interrupt endpoint fits a 1 kHz IMU, so `0x83` is the first candidate for the
stream, then `0x81`. Bulk `0x82` is the first candidate for the side cameras' 25 Hz frames.
Nothing confirms any of these guesses. Treat them as a reading list for the capture.

**C** [S4]: the interface is exclusive. VITURE's SpaceWalker holds it while running, and a
second client's open fails. **I**: the same holds on Linux, because claiming a usbfs
interface is exclusive. So `xrDriver` and `xr-luma-driver` cannot run together.

### The 1102 companion

**C** (sysfs): `35ca:1102` has five interfaces. 1.0 and 1.1 are audio. 1.2 is HID with an
8-byte IN endpoint. 1.3 and 1.4 are HID with 64-byte IN and OUT interrupt endpoints, and
their report descriptors sit on the Consumer page (0x0C), with one 64-byte report each way
and no report ids.

The sources disagree about it. [S10] says the IMU stream and MCU are on 1.3/1.4, but the same
page says those endpoints do not speak the classic protocol. [S4] calls 1102 an audio-key set
and warns that its name has misled investigations. [S6] says the same of a Beast's 1102
siblings: microphone and buttons, on the Consumer page.

**I**: it is not the IMU path. [S4] found the live protocol on 1104 at 0x04 to 0x87,
and classic VITURE IMU interfaces use usage page 0xFF00, not 0x0C. The capture settles it,
because usbmon shows whether anything reads those endpoints during a session.

## Generations

The Luma Ultra is called Carina in the SDK, and it does not behave like any other VITURE.

| | One, Lite, Pro ("Gen1") | Pro 2, Beast ("Gen2") | Luma Ultra ("Carina") |
|---|---|---|---|
| USB interface | Vendor HID, 64-byte reports **C** [S7] | One vendor HID, 64-byte reports **C** [S5] | One vendor interface, bulk and interrupt **C** |
| Envelope | `FF FE`/`FF FD`/`FF FC` **C** [S7] | `10 00`, MsgID, length, sum **C** [S5] | `FF FE`/`FF FD`, plus `FA 55` **C** [S4] |
| Checksum | CRC-16 variants, sources differ, see below | Plain byte sum **C** [S5] | CRC-16/XMODEM **C** [S4] |
| IMU start | Message `0x15`, payload 1/0 **C** [S7] [S8] | Message `0x0301`, `[stream, rate]` **C** [S5] | **U** |
| `open_imu` API | Used | Used | "No effect for Carina device" **C** [S2] |
| IMU reports | Unsolicited `FF FC` frames **C** [S7] | Events `0x7308`, `0x7309` **C** [S5] | **U** |
| Fusion | On the glasses, Euler angles **C** [S7] | On the glasses (pose) or host (raw) **C** [S5] | On the host, in `libcarina_vio` **C** [S10] |
| Cameras in tracking | No | No | Yes in 6DOF, 25 Hz **C** [S2] |

The Luma and Luma Pro are **U** in this table. [S7] lists them with Gen1, and [S6] says the Gen1
protocol is confirmed only up to the Pro.

**C** [L1]: the SDK logs "Creating V1 protocol instances for Gen1/Carina device". So the
Carina data envelope is the Gen1 frame shape, carried as 512-byte bulk packets through
`send_custom_data` in place of 64-byte HID reports.

Gen1 and Gen2 models all share VID `0x35CA`. PIDs for reference: One `0x1011 0x1013 0x1017`,
One Lite `0x1015 0x101B`, Pro `0x1019 0x101D`, Luma `0x1131`, Luma Pro `0x1121 0x1141` [S7];
Pro 2 `0x1301` [S5]; Beast `0x1201` [S6]; Luma Ultra `0x1104` **C** here, and `0x1101` is also
listed by [S11] (**U** on hardware; only match it after seeing one).

## Data envelope (0x06 out, 0x87 in)

This is the channel `0x0007` travels on. The layout is **C** [S4], which checks it against the
frames VITURE's web updater emits. Our tests repeat that check. The log agrees:
`PacketLen: 512`, `DataLen: 12` for an empty message [L1].

Every packet is exactly 512 bytes, padded with zeros. Multi-byte fields are little-endian.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | Sync | `FF FE` host to device, `FF FD` device to host |
| 2 | 2 | CRC | CRC-16/XMODEM over bytes `4 .. 6+pklen`: poly 0x1021, init 0, no reflection, no final xor |
| 4 | 2 | `pklen` | `12 + payload length` on a request |
| 6 | 8 | Counter | Zero on a request. Device-filled on a reply |
| 14 | 2 | Message id | Echoed in the reply |
| 16 | 2 | Reserved | Zero on a request |
| 18 | n | Request payload | Replies have a status byte here instead, 0 meaning success |
| 19 | n | Reply payload | ASCII in the known replies, NUL-padded fixed fields |

The padding after `6+pklen` is outside the CRC.

The eight reply bytes at offset 6: the three captured replies in [S4] have `00 00 00 00`
followed by `76 3e 42 00`, `77 3e 42 00` and `78 3e 42 00`. **I**: a little-endian 32-bit count
at offset 10 that rose by one per message. Whether it counts messages or time is **U**. Do not
build on it.

### Known messages

| Id | Meaning | Payload | Direction | Tag |
|---|---|---|---|---|
| `0x0001` | App firmware version, e.g. `12.0.01.101_20260605` | none | read | **C** [S4] |
| `0x0007` | `get_display_mode` | none | read | **C** [L1] |
| `0x000b` | DP version | none | read | **C** [S4] |
| `0x0010` | Board serial | none | read | **C** [S4] |
| `0x0011` | Package serial | none | read | **C** [S4] |
| `0x0063` | OSD version | none | read | **C** [S4] |
| `0x100B` | Enable status reports | 1 byte, value **U** | write | **C** [L1] for existence, **I** for its name's meaning |

The `0x100B` name comes from the log line straight after it: "enabled status report for
Carina device". **I**: a `0x1000` bit may mark a write, but `0x000b` reads the DP version, so
that reading is shaky.

**C** [L1]: the only message ids in the two info-logged sessions are `0x100B`, once per session,
and `0x0007`, repeatedly. Gen1's `0x15` IMU command is never logged on this device.

The reply to `0x0007` is not captured anywhere. **I**: `status` then one byte holding a
`VITURE_DISPLAY_MODE_*` value [S2], as with `0x3141` on Gen2 [S5].

### Example frames

The first two are vendor bytes. `0x0007` is computed by our tool and unconfirmed on the wire,
because nobody has published one. Trailing zeros are omitted.

```
0x0001 request  ff fe 7a 60 0c 00 00 00 00 00 00 00 00 00 01 00
0x0010 request  ff fe 69 0d 0c 00 00 00 00 00 00 00 00 00 10 00
0x0007 request  ff fe e3 47 0c 00 00 00 00 00 00 00 00 00 07 00      (computed)
reply to 0x0001 ff fd af 0f 21 00 00 00 00 00 76 3e 42 00 01 00 00 00 00 "12.0.01.101_20260605"
```

## MCU envelope (0x04 out, 0x85 in)

**C** [S4], measured on a Luma Ultra 2026-09-06:

```
Request: FA 55 | command | length (16-bit BIG-endian) | payload

fa 55 80 00 00  ->  fa 55 80 00 00 02 01 09
fa 55 c3 00 00  ->  fa 55 c3 01 00 00
```

- The reply echoes the command byte, and there is no sequence number.
- The two bytes after the command in a reply have no settled meaning. [S4] keeps them raw.
- `0x80` returns the chip version, not the product version. `0xc3` is a second frame of the
  same read and its meaning is **U**.
- VITURE's own code drains the inbound pipe before each command: 4 reads of 100 ms each, then
  a 1500 ms command timeout. Stale answers look valid otherwise.
- VITURE's web updater uses this channel for `MT_FW_START`, `MT_FW_WRITE` and `MT_FW_READ`.
- [S4] reports 25 further command bytes and withholds them, because their effects are unknown.

**I**: the IMU start is likely on this channel or inside `carina_a1088_*` routines, because it
does not appear among the data-envelope messages. Nothing confirms that.

## Older-model envelopes

These are for orientation only. **Do not send them to a Luma Ultra.**

Gen1, on 64-byte HID reports:

```
0x00 2 sync: FF FE command, FF FD ack, FF FC IMU
0x02 2 CRC-16
0x04 2 length
0x06 4 timestamp on IMU frames [S7]
0x0A 4 reserved
0x0E 2 command id
0x10 2 message counter
0x12 n payload
last 1 0x03 end marker [S6] [S7]
```

The IMU frame holds three big-endian floats at offset 18, in the order Z, X, Y [S6] [S7].

The Gen1 sources conflict on the checksum: [S6] and [S7] use CRC-16/CCITT-FALSE (init 0xFFFF,
big-endian in the frame), and [S8] uses init 0 and little-endian. On the Luma Ultra's data
envelope init 0 and little-endian is proven, and the end marker is absent: the frame's `pklen`
for a 1-byte payload is 13 [L1].

Gen2 is fully described in [S5]. It shares nothing with Carina. Its `0x0301`, `0x7308` and
`0x7309` must not be assumed to exist here.

## IMU stream

The wire format is **U**. Everything below comes from the SDK's API surface, which shows what
the stream carries once the SDK has parsed it.

**Without a start command the device says nothing.** **C** [S4]: a 75-second listen on all five
inbound pipes, buttons pressed and head moving, gave twelve identical frames in the first 10 ms
(a flushed buffer) and then silence. [S6] saw the same kind of silence on a Beast.

**C** [L1]: the device does speak unprompted once `0x100B` has been sent. The log has 79
`m_StateCallback is not initialized` errors. **I**: those are status events reaching an SDK with
no callback set, since XRLinuxDriver left its registration commented out [S1] `viture.c:889`.
[S1] `viture.c:647` notes the SDK does not reliably fire that callback.

| Item | Value | Tag |
|---|---|---|
| Sample array | `[ax, ay, az, gx, gy, gz]`. Note accel first, the reverse of Gen2 | **C** [S2] `viture_device_carina.h` |
| Sample timestamp | `double`, seconds | **C** [S2] |
| Timebase | Pose and VSync are documented "monotonic"; for IMU it is not stated | **U** |
| Rate | 1000 Hz: XRLinuxDriver hardcodes it for Carina | **C** [S1] `viture.c:38` |
| Magnetometer, temperature | Not exposed by the Carina callback | **C** [S10], and the array has no slot |
| Gyro units | rad/s on Gen2 raw | **C** [S1] `viture.c:48`; **U** for Carina |
| Accel units | g on the Pro 2 wire | **C** [S5]; **U** for Carina. The contract's `accel_m_s2` must convert once known |
| Axis frame of raw samples | Gen2 is East-Down-North [S1] `viture.c:575`; for Carina | **U** |
| Pose rate | 25 Hz via the pose callback, polled up to 1 kHz via `get_gl_pose_carina` | **C** [S2] [S9] |

XRLinuxDriver does not use the IMU values at all on this device. Its callback treats a sample as
a clock tick, then calls `get_gl_pose_carina` and publishes the fused pose
[S1] `viture.c:472`. So no open project has ever shown what raw Carina samples look like.

### Pose axes

**C** [S1] `viture.c:481-489`. The SDK returns `[px, py, pz, qw, qx, qy, qz]` in OpenGL axes (x
right, y up, z back). XRLinuxDriver converts it to North-West-Up:

```
quat: x = -pose[6], y = -pose[4], z = pose[5], w = pose[3]
pos:  x = -pose[2], y = -pose[0], z = pose[1]   (then scaled)
```

**C** [S2]: pitch and roll are gravity-anchored in the SDK's pose. **C**: the published feed is not,
because XRLinuxDriver makes it relative to the pose captured at calibration (see the
`viture-feed-no-gravity` note and `src/driver.c` `driver_handle_pose`). `luma::Publisher` must
reproduce the relative feed, not the gravity-aligned one, unless `pose.cpp` changes too.

### Values the publisher should copy for the Luma Ultra

**C** [S1] `viture.c:62-118` and `viture.c:180-196`: FOV 52.0°, pitch adjustment -8.5°, resolution
height 1200, `lens_distance_ratio` 0.05, calibration wait 10 s, look-ahead constant 10, frametime
multiplier 0.6, scanline adjust 10.0, look-ahead cap 40.0 ms, IMU buffer `1000 / 60 = 16` samples.

### Timestamps in the feed

**C** [S1] `viture.c:491-499`: `device_ms = (uint32)((ts - ts0) * 1000)`, in double, rebased just
below 2^24 ms. The first sample sets `ts0`, and it is never reset on reconnect, because it is a
function-local `static`.

## What XRLinuxDriver sends

### Startup order

**C** from [L1], session 23:28:51, with [S1] for the calls.

| Step | Call | On the wire | Tag |
|---|---|---|---|
| 1 | `xr_device_provider_create`, callbacks | Nothing logged | **C** |
| 2 | `initialize` | `get exp flag status: Host Command Invalid` | **C**; the bytes are **U** |
| 3 | | Either `remain sync data transfer :15872 15872` or `do sync data transfer :-7 512 0` | **C**; the bytes are **U** |
| 4 | | `init open_vst_str: off` | **C**; the bytes are **U** |
| 5 | | `0x100B`, 1 byte, acknowledged | **C** |
| 6 | `sleep(1)`, then `start` | "CarinaDeviceProvider started"; **stream start would be here or in step 2-4** | **I** |
| 7 | `open_imu` skipped on Carina | Nothing | **C** [S1] `viture.c:827` |
| 8 | Display mode refresh | `0x0007`, acknowledged | **C** |
| 9 | Wait 10 s, centre, publish | IMU flowing, `0x0007` polling | **C** |

From "Found device" to "Device connected" took 1.45 s in that session.

Step 3 is a 16 KiB transfer in 512-byte pieces: `15872 = 16384 - 512` (**I**). In `do sync data
transfer :-7 512 0` the -7 is **I** libusb's timeout, on a transfer of 512 bytes with 0 moved. It
came about 408 ms after step 2, and the other form about 208 ms after (**C**, from the `[CARINA]`
microsecond stamps).

### The 0x0007 poll

**C**, the chain in the source:

1. `manage_state_thread_func` loops with `sleep(1)` [S1] `driver.c:541-551`.
2. It calls `update_state_from_device`, which, for a device with `sbs_mode_supported`, calls
   `device_is_sbs_mode_func` [S1] `state.c:125-127`.
3. `sbs_mode_supported` is true on the Luma Ultra. Only Pro 2 and Beast clear it
   [S1] `viture.c:725`.
4. `viture_device_is_sbs_mode` calls `viture_refresh_sbs_state_locked`, which calls
   `xr_device_provider_get_display_mode` [S1] `viture.c:302` and `viture.c:1052`.
5. Carina has no native mode, so the native getters send nothing [S1] `viture.c:203`.

There is one further call at connect, and one per control-flags update [S1] `driver.c:567`.

**C**, measured [L1]: 23 frames in 45.7 s and 24 frames in 64.9 s, one every 2.0 to 2.7 s. These
are the only two sessions with the SDK's info log on, and they were short. The source predicts a
little under 1 Hz. The gap is **U**: the loop could be spending about a second elsewhere, or each
round trip could be slow.

Nothing else repeats on the data channel in those logs. Traffic inside `libcarina_vio` is not
logged, so that claim stops at the envelope (**U** for the rest).

`0x0007` does nothing for tracking. Removing it is safe in logic. Whether the firmware treats any
periodic traffic as a keep-alive is **U**, and only a run without it can show that.

### Reconnect

**C** [S1] `viture.c:1041-1050` and `driver.c:359-362`: `block_on_device` polls `is_imu_alive` every
second. When that fails it does a soft disconnect, which keeps the provider handle alive, and the
driver retries after 1 s. `viture_device_connect` reuses the handle, and only a failed connection
leads to a hard disconnect [S1] `viture.c:1017-1025`.

## Minimum command set

**The honest answer today: unknown.** Nothing public names a Carina IMU start.

| Command | Needed for IMU? | Reason | Tag |
|---|---|---|---|
| The IMU start | Yes | The device is silent without a session | **C** that one exists in some form, **U** the bytes |
| `0x100B` status reports | Unknown | Could be unrelated, or part of the session | **U** |
| `0x0007` display mode | No | Used only for SBS detection | **C** |
| `get exp flag`, the 16 KiB read, `open_vst_str` | Unknown | Could be VIO setup that 3DOF does not need | **U** |
| Any `set_display_mode`, film, brightness | No | Carina takes no display override [S1] `viture.c:326` | **C** |
| USB reset, `SET_CONFIGURATION`, clear-halt loops | Avoid | Not needed, and the contract forbids resets | **I** |

The target remains a start command, a stop command and nothing else, in 3DOF.

## Suspicious behaviours

Ranked by how much each is worth testing. The drop itself belongs to the `usb-drop-fix` thread.
This list is what the protocol reading adds. None of these is proven to be the cause.

What the kernel log shows for 2026-10-30 to 2026-11-03 [L3], and how to read it:

- 70 of 70 drops are `usb 5-2: USB disconnect`: **the hub**, followed at once by its three
  children. On the last boot, with 54 of those drops, there is no `reset` line for 5-2 or 5-2.1, and the
  drops I read had no xHCI error beside them. **I**: the host did not reset the device; everything behind the hub vanished together.
  That looks like the glasses' own electronics restarting or losing power, more than a software
  error in one USB function. A firmware crash on 5-2.1 alone would leave the hub up, unless the
  firmware also controls hub power, which is **U**.
- All 117 `Retrying driver connection` lines follow a drop. The 25 on the last boot fall 1.4 to
  20.1 s after one, in clusters at about 2, 11 and 20 s. The other 92, on 2026-10-31, include long
  chains about 9 s apart, up to 574 s after the last disconnect, each starting about 2 s after a
  drop. So no retry chain began on its own. Lines tagged `[libglasses]` have no timestamp, so the
  log cannot show whether a timed-out `0x0007` came slightly before a drop.

| # | Behaviour | Evidence | Tag |
|---|---|---|---|
| 1 | **6DOF by default.** `set_dof_type_carina` is never called, and the default is 6DOF. Side cameras stream at 25 Hz, plus 1 kHz IMU, on a USB 2 hub also carrying the UVC camera. Power draw, heat or firmware load all rise over plain display. | [S2]; absent from [S1]; log says `6DOF=true` | **C** the fact, **I** the link to drops |
| 2 | **Power.** The descriptor says self-powered, 2 mA, so the real supply is outside USB descriptors and nothing here shows its limit. A brown-out under camera load would drop the whole hub, which matches the hub-level pattern. | sysfs; drop pattern | **C** the descriptor, **I** the link |
| 3 | **A rejected command at every start.** `get exp flag status: Host Command Invalid` in all 70 logged connections. The SDK sends something this firmware refuses. | [L1] | **C** the fact, **U** the effect |
| 4 | **A transfer that fails at every start.** The 16 KiB step never completes: 33 connections time out and 37 stop after 512 bytes. Neither form predicts a later drop (10 of 33 and 8 of 37 driver runs had a retry). | [L1] | **C** |
| 5 | **Polling on a stale handle.** After a drop the soft disconnect keeps the provider and retries on it, and 0x0007 keeps going out through a dead handle: 439 `-7` errors and 453 failed sends against 117 retries. Probably harmless to firmware, but it delays recovery. | [S1], [L1] | **C** the code, **I** the rest |
| 6 | **Reconnect hammering.** The retry loop reopens during re-enumeration, 1 s after a failure. Drops come in pairs 2 to 23 s apart, so a re-init could be provoking the second one. | [S1]; `viture-usb-drop-ends-run` note | **I** |
| 7 | **Two threads on one pipe pair.** A monitor thread reads `0x87` while a send queue writes `0x06`. The published crash backtrace runs through `carina_a1088_read_custom_data` from that thread, and the maintainer calls the SDK a known source of segfaults. | [S3] | **C** the crash, **I** that it touches firmware |
| 8 | **Unconsumed reports.** If the host reads `0x81`, `0x82` or `0x83` late, the device's buffers fill, and how firmware handles that is unknown. A stalled host on a 1 kHz stream is a plausible firmware edge case. | Reasoning only | **U** |
| 9 | **Slow polling round trips.** If `0x0007` really takes about 1 s to answer while streaming, the firmware's command path may be under load. | Measured rate gap above | **I** |
| 10 | **Status events with nowhere to go.** 0x100B turns on unsolicited events, and the SDK then logs each as an error. The pipe is drained, so this seems harmless. | [L1] | **I** |

The meaning of `-7` is itself ambiguous. In `libglasses` headers, -7 is
`VITURE_GLASSES_ERROR_DEVICE_REJECTED` [S2]. In libusb, -7 is `LIBUSB_ERROR_TIMEOUT`.
`do sync data transfer :-7 512 0` is the libusb sense (**I**). For `get_display_mode ... returned
-7` it could be either (**U**), so the log does not prove the firmware refused anything.

**Issue #122** is the only public report of Luma Ultra trouble in XRLinuxDriver [S3]. One user had
"10 minutes then it will crash", and the maintainer asked about a slipping magnetic connector.
Neither was confirmed. No public report matches 37 drops a day.

**Tests that would separate these** (for `usb-drop-fix` or Cody): a long 3DOF run against a long
6DOF run on the same cable (a `[shim] set_dof_type_carina(3)` line in the log at 23:28:51 shows
someone already started on this; that session logged `6DOF=false`, and [S12] says 3DOF needs no
cameras or VIO); then a different power source if the 3DOF run helps.

## Never send

These rules bind every thread that writes to the device.

- Nothing on `0x04` except an opcode seen in a capture of VITURE's own software in the same session
  state. That pipe carries flashing, so an unknown byte there could damage the glasses.
- No `MT_FW_*`. No bytes copied from a firmware updater.
- No fuzzing, no id sweeps and no "try `0x15`" on any pipe.
- No `set_display_mode`, film, brightness or display size. Tracking needs none of them.
- No USB reset (`USBDEVFS_RESET`), which the contract already bans.
- Older-model frames are never sent to this device.
- Writes come from one thread, one at a time, with the drain described above where it applies.

## Capture plan

The capture answers the two big unknowns, shows which endpoint streams, and records the last
frames before a drop. **Cody's call first**: the session under capture runs VITURE's SDK, and
that SDK's licence has not been accepted. A capture only watches USB traffic to Cody's own
device, the same method [S5] used, but whether that fits the "open knowledge only" rule is
Cody's to decide. No licence text could be found on VITURE's SDK page, so we have not seen what
it says about this.

Commands are for Cody, because they need root. Run them before starting XR.

```bash
sudo modprobe usbmon
HEADSET=$(cat /sys/bus/usb/devices/5-1/devnum)
sudo cat /sys/kernel/debug/usb/usbmon/5u \
  | grep --line-buffered -v -E ":5:0*$HEADSET:" > ~/luma-usbmon.txt
```

The `grep` leaves out the Razer Barracuda headset on port 5-1, which shares bus 5. The file still
gets the first bytes of each camera and microphone transfer from the glasses, so treat it as
private and delete it after use. Keep the Barracuda in the same port, because the filter uses its
device number.

Then start XR normally, wait about two minutes, and press Ctrl+C. A run that reaches a drop is more
valuable still, because the end of the file then holds the last frames. Afterwards:

```bash
sudo rmmod usbmon
DEV=$(cat /sys/bus/usb/devices/5-2.1/devnum)   # add --dev for each number if it re-enumerated
python3 spike/driver/docs/tools/luma_wire.py summarize ~/luma-usbmon.txt --dev "$DEV"
```

A second capture of a 3DOF session, using whatever set `6DOF=false` in the log, would show which commands VIO needs and which it does not.

Limits to expect:

- The text format keeps the first 32 bytes of each transfer. That is enough for commands and
  headers. If IMU frames are longer, the full layout needs `wireshark-cli` and
  `sudo dumpcap -i usbmon5`.
- usbmon only watches, but it does add a little CPU load on a stream this fast.
- The device number changes on every drop. Pass the number that was live at the time.

## Changes the contract needs

| Contract line | Change |
|---|---|
| `Transport`: "hidraw or usbfs" | usbfs only: claim interface 0 of `35ca:1104` by number. Reads must cover several endpoints, one of which is not known yet, so `read(span, timeout)` needs an endpoint argument |
| `parse_imu`, start and stop commands | Blocked on the capture. Code against a fake until then |
| `ImuSample.mag` | Will be `nullopt` on this device |
| `ImuSample.accel_m_s2`, `gyro_rad_s` | Units and axes must be confirmed from the capture before fusion is trusted |
| `Publisher` | Must not run alongside `xrDriver`: the USB interface is exclusive |
| Reconnect | Reopen from scratch on every drop. The device number changes, and session state is presumably lost |

## Open questions

| Question | Way to answer |
|---|---|
| Stream's endpoint and start bytes | Capture |
| IMU frame layout, units, axes, timebase | Capture, then moving the glasses by a known amount |
| Stop command, and whether close alone stops it | Capture across a clean stop |
| Is any keep-alive needed? | Run with no traffic after start |
| Does 3DOF need cameras or `0x100B`? | Compare 3DOF and 6DOF captures |
| What are `get exp flag`, `open_vst_str`, the 16 KiB read? | Capture |
| Does `0x0007` come before the drop? | Capture through a drop |
| Reply format of `0x0007` | Capture |
| Are there calibration tables the host must load (bias, axes)? | Capture; the 16 KiB read is a candidate |
| Does the firmware version on Cody's unit change any of this? | `0x0001` in the capture; the log never recorded it |

[S1]: sources.md#s1
[S2]: sources.md#s2
[S3]: sources.md#s3
[S4]: sources.md#s4
[S5]: sources.md#s5
[S6]: sources.md#s6
[S7]: sources.md#s7
[S8]: sources.md#s8
[S9]: sources.md#s9
[S10]: sources.md#s10
[S11]: sources.md#s11
[L1]: sources.md#l1
[L3]: sources.md#l3
[S12]: sources.md#s12
