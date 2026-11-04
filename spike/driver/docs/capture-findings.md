# What a real session looks like on the wire

A passive usbmon capture of XRLinuxDriver 2.11.9 driving Cody's Luma Ultra through a live XR session, with no USB drop.
This replaces the guesses in `protocol.md` wherever the two differ. Everything here was read from the wire. No SDK code was
read or run for it.

| | |
|---|---|
| Recorded | 2026-11-04, 00:19:46 to 00:43:17, `cat /dev/usbmon5` |
| Session | Glasses enumerated as device 13 at 00:22:44.8, stream from 00:22:50.3, recording ended with it still running |
| Raw dump | `~/.local/state/viture-workspace/usb-captures/usbmon5-xr-20261104-001946.bin`, 24 GB. Holds the serial and camera images. Never commit it |
| Sanitised | `usbmon5-xr-20261104-001946.lcap` beside it, 323 MB, no calibration, no serial, no image data (`python3 -m tools.luma_capture import-usbmon`) |
| In the repo | `spike/driver/tests/fixtures/luma_imu_real.bin`: 8 s of IMU packets. Golden packets in `tests/protocol_test.cpp` |

Times below count from the first descriptor read of device 13 unless they say otherwise.

**Lost events.** `cat` reads the kernel's default 300 KiB ring, which overflowed about 100 times, so the capture has holes:
1441 IMU samples, 0.12%. These are recorder losses, not device losses: 88 of the 89 IMU gaps over 6.5 ms line up with the
same silence on an unrelated USB audio device, which cannot share a cause with the glasses. Next time use
`tools.luma_capture record`, which sets a 16 MiB ring and reports drops.

## Endpoints

| Endpoint | Type | What it carries | Rate |
|---|---|---|---|
| `0x81` | interrupt IN, 1024 B, 500 µs | **The IMU.** 28-byte packets, one sample each | 1000 Hz |
| `0x83` | interrupt IN, 512 B | 16-byte type `0x91` packets: display refresh events | 60 Hz, then 120 Hz |
| `0x82` | bulk IN, 512 B | Stereo camera tiles and metadata, read in 16 KiB transfers | 19 MB/s, 25 frames/s |
| `0x04` / `0x85` | bulk | `FA 55` MCU commands. **The stream start is here** | First 11 s only |
| `0x06` / `0x87` | bulk | `FF FE` / `FF FD` data channel | `0x0007` once a second |

`protocol.md`'s first guess for the IMU, `0x83`, was wrong. The descriptors alone could not have settled it.

## The IMU packet

28 bytes, little-endian, one sample per packet:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 3 | `AA 8F 00` | Shared by every stream endpoint |
| 3 | 1 | type | `0x88` |
| 4 | 2 | sequence | +1 per packet. Wraps at 65536, every 65.5 s |
| 6 | 2 | payload length | 16 |
| 8 | 4 | device time | 100 µs ticks, from the device's boot |
| 12 | 6 | gyro x, y, z | i16, 16.4 LSB per deg/s |
| 18 | 6 | accel x, y, z | i16, 8192 LSB per g |
| 24 | 4 | u32, most likely temperature | Climbs 1760 to 2910 over the warm-up |

There is no checksum. No magnetometer either: the driver cannot get an absolute heading from this stream.

### How each number was established

- **Rate.** 1,225,307 packets over 1226.8 s. Without the recorder's holes it is 1000 Hz: 1.00 ms median spacing, 99.9% under
  1.5 ms. Sample spacing matches the host clock to 8 ppm. The device's own stamp runs 65 ppm fast and jitters by a tick, so
  the driver counts samples instead of trusting the stamp.
- **Accelerometer scale.** At rest the vector is 8270 LSB, 1.01 g at 8192 LSB/g, which is the ±4 g range.
- **Gyro scale.** Rotating the gravity reading between 13 pairs of still moments by the integrated gyro: the error falls to
  0.36 degrees at 16.4 LSB per deg/s, and is 2.3 at 26.2, 3.0 at 32.8 and 6.1 at 8.2. So ±2000 deg/s.
- **Same frame, right-handed.** Negating the gyro in that fit gives 12 degrees of error against 0.36.
- **Up.** Gravity is on +z: mean specific force (-0.16, 0.29, 9.82) m/s².
- **Which horizontal axis.** Head rotation splits by principal axes into z (7.1 deg/s rms, yaw), x (3.2, pitch) and y (2.0,
  roll), so y runs along the nose. Its sign comes from physics. The sensor sits ahead of the neck, so turning the head
  throws it about, and a rigid-body fit of acceleration against rotation puts it 8.2 ± 0.5 cm along **-y** and 8 cm above
  the pivot. All 300 of 300 bootstrap resamples agree on the sign.
- **Temperature.** `value / 132.48 + 25` gives 38.3 °C rising to 47.0 °C and levelling off, the shape of a warm-up. That
  formula is the common one for this class of IMU, not something measured against a thermometer, so treat the absolute
  numbers as a guess and the trend as real.

So on the wire **forward = -y, left = +x, up = +z**, and `parse_imu` returns forward, left, up. A wrong sign here would swap
pitch and roll direction, which the first live stage checks directly (step G6).

## The stream start

The order on the wire, as the old driver brought the stream up:

| Time | Direction | What |
|---|---|---|
| 0.000 | | Device 13 enumerates |
| 3.680 | control | **CLEAR_HALT** on `0x82`, `0x04`, `0x85` |
| 3.781 | `0x04` | `FA 55 E4`, then 50 × `FA 55 E5`: reads a 12,699-byte block with the factory calibration and serial |
| 3.898 | `0x04` | `FA 55 9F` → status 1, no payload |
| 3.998 | `0x04` | `FA 55 80` → `01 0B`, a version |
| 4.099 | `0x04` | `FA 55 ED` → 64 bytes holding the serial |
| 4.107 | `0x82` | Short reads: 16 identical 64-byte packets, then a timeout |
| 4.307 | control | CLEAR_HALT on `0x82` |
| 4.308 | `0x81`, `0x83` | Reads queued. **Nothing arrives for 1.2 s** |
| 4.317 | `0x06` | `0x100B` payload `01`, status 0 |
| 5.320 | `0x06` | First `0x0007` |
| 5.426 | `0x04` | **`FA 55 97 00 00`** → `FA 55 97 00 00 00` |
| 5.526 | `0x04` | **`FA 55 95 00 00`** |
| 5.5268 | `0x81`, `0x83` | **First IMU packet, 0.74 ms later** |
| 5.569 | `0x85` | Reply to `0x95`, 43 ms later. 16 KiB reads on `0x82` begin at once |
| 6.13 to 11.06 | `0x04` | 39 × `FA 55 90` and 3 × `FA 55 92`, every 120 ms: a value climbing in steps, which looks like camera exposure |

Requests are `FA 55`, command, big-endian length, payload. Replies are `FA 55`, command, status, big-endian length, payload.

What this proves and what it does not:

- `FA 55 95` is what opens the stream. That is **inferred from timing**, though the timing is tight: reads sat unanswered
  for 1.2 s, through `0x100B`, and data arrived 0.74 ms after this command.
- It very probably starts the cameras too, because `0x82` runs from the same moment. Whether the IMU can run without them is
  the most important open question: see stage G in `live-test-plan.md`.
- `0x97`'s job is unknown. It may be a required step or a "stop anything running" before the start.
- **No stop command has been seen.** The recording ended with the session still up. Nothing here sends one.
- The SDK's gaps of about 100 ms between commands are its own 100 ms flush read on `0x85`, not something the firmware asked for.

The first 63 IMU packets each arrive twice, 500 µs apart, in the first 60 ms. The parser's tracker drops the repeats.

## What the old driver sends, and how often

| What | Count | When |
|---|---|---|
| `0x0007` get display mode | 1224 | Every 1.001 s (1.0004 to 1.0019), from 5.3 s to the end. **It never stops** |
| `0x100B` | 1 | 4.317 s |
| `FA 55` commands | 98 | All inside the first 11.1 s |
| CLEAR_HALT | 4 | At start |

After 11.1 s, `0x0007` is the only command on the wire. All 1224 succeeded, median round trip 0.87 ms, 99% under 1.39 ms,
worst 84 ms. In this healthy session the polling does nothing visibly wrong. That neither convicts nor clears it: the logged
`0x0007` failures come from sessions that dropped, and this one did not. The reply's mode byte went from `0x31` to `0x44`
when the display switched to 120 Hz.

Bus load is the bigger difference. The cameras put 19 MB/s, about 150 Mbit/s, through a hub shared with the microphone and a
camera, and as far as this capture shows they start only when a driver starts them. A plain display, which never drops, has
none of that traffic.

## Display events

`0x83` packets carry only a type and a device-clock time. Their spacing is 16.7 ms until the display switches to 120 Hz, then
8.3 ms, which makes them a vsync clock on the same timebase as the IMU. Nothing uses this yet. It could later phase the
pose publish to the display.

## The one real stall

At 17.51 s, 00:23:02.279, the display changed from 60 to 120 Hz. Display events stopped for 1.52 s and **the IMU stopped for
124 ms**. Its clock jumped 124 ms while its sequence went up by one, and reads kept being answered, so this was the firmware
pausing, not the host. It happened once in 20 minutes. It exceeds the 100 ms stale limit, so the daemon marks the feed stale
and then fresh, and tracking carries on.

Any other long gap in the capture is a recorder hole (see above).

## Replay through the real daemon

`xr-luma-driver --replay` on the sanitised capture, with real parser, tracker, Fusion and Publisher:

| Measure | Result | Target |
|---|---|---|
| Samples | 1,225,244, 0 bad packets, 63 repeats | |
| Rate | 1000 Hz outside recorder holes | L4: 1 kHz |
| Time per packet, bytes in to pose out | 0.12 µs median, 0.14 µs p99.9, 10 to 70 µs worst over four runs | L1: p99 under 500 µs. This is CPU only: USB and scheduling come on top |
| Calibrated | 2.60 s from the first sample, with the glasses moving | C2: within the 2.5 s timeout. The 0.1 s over is one 100 ms block. Old driver: about 11.5 s |
| Tilt against gravity at rest | 0.30° median, 0.66° p95 | C4: 1° |
| Heading at rest | See below | D1: 0.5°/min |

A whole 20-minute session replays in 0.2 s.

### Heading

An IMU has no reference for heading, so a real recording has no truth for it. Fusion used to report 1.7°/min at rest. Most
of that turned out to be the head really moving, which no filter can see. To separate them, an offline reference integrates
the gyro with the best bias the whole recording can give, a fit against temperature of per-window medians.

Against that reference, online Fusion drifts about **0.5°/min**, with a worst moment of 11° and 3° at the end of 20 minutes.

- The bias **tracks temperature**: yaw axis 0.25°/s at 39 °C, 0.36°/s at 47 °C, about 0.016°/s per °C. Left uncorrected, that bias
  alone is 21°/min.
- The first 100 s are the worst, before the bias settles. Saving the bias between runs is for this. It has not been measured
  on a real restart yet.
- A slow head turn wrecks a mean of "quiet" moments. 60-s means moved up to 0.5°/s, medians 0.05°/s. So Fusion now takes a
  median, with a gain that starts fast and settles.

**This is the driver's biggest known weakness against the old one.** XRLinuxDriver runs in 6DoF with the cameras on, so its
heading probably has a visual reference. Ours has none. Expect a slow turn of about 10 to 30° in 20 minutes. Whether that is
acceptable is Cody's call. The fix, if needed, is a recentre or a camera-based reference, and the latter brings back the
camera traffic.

The sim's drift figures improved too (worn desk work 3.0 to about 1.4°/min). All this was tuned on one 20-minute recording, so
expect to revisit it.
