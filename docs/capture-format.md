# Capture format (`.lcap`)

A capture is what the host and the glasses said to each other, as the kernel's usbmon saw it, plus timed notes
from the logs. `tools/luma_capture` writes these files. The driver daemon reads the same files under `--replay`,
through `spike/driver/include/luma/capture_file.hpp` (header-only, standard library only). The test
`tests/luma_capture/test_cpp_reader.py` compiles that reader and checks it against the Python one byte for byte.

Everything is little endian. There is no padding and no compression.

## File

A 32 byte header, then records until the end of the file.

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 8 | magic | `LUMACAP1` |
| 8 | 2 | version | 1 |
| 10 | 2 | file header size | 32 |
| 12 | 2 | record header size | 48 |
| 14 | 2 | reserved | 0 |
| 16 | 8 | start time | ns since the Unix epoch |
| 24 | 4 | bus | USB bus number |
| 28 | 4 | reserved | 0 |

A reader skips to `file header size` before the first record and takes `record header size` from here, so a later
version can grow either header. A reader refuses a version it does not know. Compatible changes (a longer header,
a new record kind, a new flag, a new note type) do not change the version.

## Record

Every record has the same 48 byte header, followed by `data length` bytes.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 4 | record length | header size + data length |
| 4 | 1 | kind | 1 USB, 2 note; skip any other kind using record length |
| 5 | 1 | event | ASCII `S` submitted, `C` completed, `E` error; 0 on a note |
| 6 | 1 | transfer | 0 isochronous, 1 interrupt, 2 control, 3 bulk |
| 7 | 1 | endpoint | bit 7 set means device to host |
| 8 | 8 | time | ns since the Unix epoch |
| 16 | 8 | URB id | pairs an `S` with its `C` |
| 24 | 2 | bus | |
| 26 | 1 | device | USB address. It changes at every reconnect, so never match on it across a drop |
| 27 | 1 | flags | see below |
| 28 | 4 | status | signed errno on a `C`, 0 ok; -115 on an `S` means pending |
| 32 | 4 | URB length | bytes asked for (`S`) or actually moved (`C`) |
| 36 | 4 | data length | bytes following this header |
| 40 | 8 | setup | the control request, when flag 0x01 is set |

Flags:

| Bit | Name | Meaning |
|---|---|---|
| 0x01 | setup | `setup` holds a control request |
| 0x02 | glasses | this event belongs to the glasses (35ca:1104) |
| 0x04 | kernel truncated | usbmon delivered fewer bytes than the transfer held |
| 0x08 | policy dropped | the recorder refused to keep this payload |
| 0x10 | capped | the recorder kept only the first part of this payload |
| 0x20 | companion | this event belongs to the glasses' companion device (35ca:1102) |

Data follows direction, as it does on the wire: host-to-device bytes are on the `S`, device-to-host bytes on the `C`.
USB times come from the kernel's wall clock in microseconds. USB records are in the order the kernel saw them, but
times can jump if the system clock is stepped. Notes may arrive a little late, so a file is not strictly in time
order: sort by time before lining notes up with USB events.

A file cut off by a crash is still valid up to its last whole record. Stop at the first record whose length fields
disagree or that runs past the end of the file, and tell the user. Do not try to resynchronise.

## What is kept

Payloads are kept for the glasses, for the companion device, for hubs and for control transfers. The companion
holds the microphone but also HID reports that may be the buttons. The same bus carries a pass-through camera and a
USB headset, and for those the recorder stores a header with no data, with flag 0x08 set. Isochronous data, which is
how audio and video travel, is never kept for any device.

A transfer up to `--max-payload` (64 KiB) is kept whole, because the 16 KiB start-up read may hold factory calibration.
Once one endpoint has moved more than 1 MiB in a second it is treated as a camera, and each further transfer that
second is cut to its first 4 KiB, with flag 0x10 set. A 6DoF session's side cameras would hit this, if they use this
device. Those records keep their true URB length, so rates stay right.

## Notes

The data of a note is one UTF-8 JSON object with a `type`. Readers ignore types they do not know.

| Type | Fields | Meaning |
|---|---|---|
| `clock` | `realtime_ns`, `monotonic_ns` | taken together, first in each file, to relate the clocks |
| `device_map` | `devices`, `port` | every device on the bus. First in each file and again at each change. `port` is the root port the assembly hangs off, such as `5-2` |
| `start` | `bus`, `ring_bytes`, `uname` | the recorder began |
| `stop` | `reason` | the recorder ended |
| `kernel` | `text` | a kernel log line about USB, stamped with the journal's own time |
| `driver_log` | `text` | a line added to `driver.log`, stamped on arrival, which is within about 50 ms |
| `glasses_gone` | `why` (optional) | the glasses left sysfs, or came back with a new device number between two looks |
| `glasses_back` | `dev` | the glasses are listed again, at this device number |
| `mark` | `text` | something a person noted with `tools.luma_capture mark`, stamped on arrival |
| `usbmon_stats` | `dropped`, `since_last` | the kernel lost this many events because the recorder fell behind. Treat the capture as having a hole |
| `kernel_log_unavailable` | `why` | the kernel log could not be followed |

## Replay

`--replay FILE` stands in for the glasses, so everything above the transport runs on recorded data. Use a single
file: `python3 -m tools.luma_capture merge FOLDER -o FILE` joins a run's segments.

`luma::capture::load_replay` in `capture_file.hpp` turns a file into events in time order. Its kinds have the same
numbers as `luma::CaptureKind` in `daemon.hpp`:

| Kind | From | Replay does |
|---|---|---|
| `from_device` | `Record::replayable()`: kind USB, event `C`, endpoint bit 7, flag 0x02, status 0, bulk or interrupt, none of flags 0x04, 0x08, 0x10 | Returns it from a read |
| `to_device` | `Record::host_write()`: what the recorded driver sent | Nothing is sent. A test can compare it with what the new driver sends |
| `device_gone` | a `glasses_gone` note | Reports a disconnect |
| `device_back` | a `glasses_back` note | Reports a reconnect |

- Partial records are left out, because they would feed a parser bytes that never existed as sent.
- A failed transfer is not `device_gone`. Status -19 or -108 alone proves nothing: the kernel also flushes endpoints
  with -108 on `SET_CONFIGURATION`.
- By default every endpoint's data is played, and the parser rejects what is not IMU data. Pass an endpoint to keep one.
- Pace by the difference in `time`, or run flat out for tests. Never compare with the wall clock.

The bridge from `load_capture` in the daemon is one loop over `load_replay`. It was tried on a copy of `drv-daemon` with
a synthetic session taken through the recorder and `merge`: 4999 samples, no bad packets, one disconnect, one reconnect.

One read is one URB, not one USB packet. If the glasses batch samples, a single record holds several, which is how a
real read will see them too.

## Known limits

- usbmon sees the host's side of each transfer, not the electrical wire. A corrupted packet appears only as the
  error status it produced.
- On xHCI, address assignment happens inside the controller, so `SET_ADDRESS` never appears.
- For a moment after a reconnect, until sysfs lists the device, its events can lack the glasses flag. Their payloads
  are kept, but they are not replayable.
- The IMU stream's endpoint and layout are not public. `tools/luma_capture/streams.py` looks for them in a capture.
- `spike/driver/docs/protocol.md` on branch `drv-protocol-research` gives the command endpoints: 0x06/0x87 for `FF FE`
  frames, 0x04/0x85 for `FA 55` frames. Interrupt IN 0x81 (1024 bytes, every 500 us at high speed), interrupt IN 0x83
  (512 bytes, every 1 ms) and bulk IN 0x82 have no known role yet.
