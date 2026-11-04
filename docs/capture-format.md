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
| 0x10 | capped | the recorder kept only the first `--max-payload` bytes |

Data follows direction, as it does on the wire: host-to-device bytes are on the `S`, device-to-host bytes on the `C`.
USB times come from the kernel's wall clock in microseconds. USB records are in the order the kernel saw them, but
times can jump if the system clock is stepped. Notes may arrive a little late, so a file is not strictly in time
order: sort by time before lining notes up with USB events.

A file cut off by a crash is still valid up to its last whole record. Stop at the first record whose length fields
disagree or that runs past the end of the file, and tell the user. Do not try to resynchronise.

## What is kept

Payloads are kept for the glasses, for hubs and for control transfers. The same bus carries the camera and two
microphones, and for those the recorder stores a header with no data, with flag 0x08 set. Isochronous data, which
is how audio and video travel, is never kept for any device.

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
| `glasses_gone` | | the glasses left sysfs |
| `usbmon_stats` | `dropped`, `since_last` | the kernel lost this many events because the recorder fell behind. Treat the capture as having a hole |
| `kernel_log_unavailable` | `why` | the kernel log could not be followed |

## Replay

`--replay FILE` stands in for `luma::Transport`, so everything above it runs on recorded data with no device.

1. Use a single file. `python3 -m tools.luma_capture merge FOLDER -o FILE` joins a run's segments.
2. `Transport::read` returns, in order, the data of each record that passes `Record::replayable()`: kind USB, event
   `C`, endpoint bit 7 set, flag 0x02 set, status 0, transfer bulk or interrupt, none of flags 0x04, 0x08, 0x10.
   Partial records are skipped because they would feed a parser bytes that never existed as sent.
3. Filter by endpoint. Which endpoint carries the IMU has not been established (see below), so take it as an option
   until a real capture settles it.
4. Pacing: either wait for the difference in `time` between records, or run flat out for tests. Do not compare
   against the wall clock: the capture's times are from when it was recorded.
5. `Transport::write` must not fail or send anything. `Record::host_write()` selects what the real driver sent, so a
   test can compare it with what the new driver would have sent.
6. A disconnect in the data appears as a `kernel` or `glasses_gone` note, or a `C` with status -19 or -108 on a
   glasses endpoint. That status alone is not proof: the kernel also flushes endpoints with -108 on
   `SET_CONFIGURATION`. A reconnect appears as the same endpoints at a new device address. Replay these as transport
   disconnect and reconnect events.

One read is one URB, not one USB packet. If the glasses batch samples, a single record holds several, which is how
`Transport::read` will see them too.

## Known limits

- usbmon sees the host's side of each transfer, not the electrical wire. A corrupted packet appears only as the
  error status it produced.
- On xHCI, address assignment happens inside the controller, so `SET_ADDRESS` never appears.
- For a moment after a reconnect, until sysfs lists the device, its events can lack the glasses flag. Their payloads
  are kept, but they are not replayable.
- The MsgID field's position in a command is not publicly documented for this device. See
  `tools/luma_capture/protocol.py`.
- The endpoint roles are unknown. `/sys/bus/usb/devices/5-2.1` shows one vendor-specific interface with bulk OUT 0x04
  and 0x06, interrupt IN 0x81 (1024 bytes, every 1 ms at high speed) and 0x83 (512 bytes, every 1 ms), and bulk IN
  0x82, 0x85, 0x87. 0x81 looks like a 1 kHz stream on paper, but that is unconfirmed.
