# luma::Transport

The byte pipe to the glasses. It knows nothing about what the bytes mean. Code in
`include/luma/transport.hpp` and `src/transport.cpp`; tests in `tests/transport_test.cpp`.

## Primary path: usbfs

`35ca:1104` has one vendor interface and no kernel driver, so no hidraw node exists for it. The
transport talks to `/dev/bus/usb/BBB/DDD` with usbdevfs ioctls and needs no libusb.

The IMU is on an interrupt endpoint and commands are on bulk, so both kinds are handled the same way:
URB type and size come from sysfs per endpoint, and `read()` says which endpoint each packet came
from. `wiring.cpp`'s `live_config()` is what the driver opens the glasses with.
`capture-findings.md` has what the endpoints carry, read from a usbmon capture.

| Endpoint | Type | Max packet | Use here |
|---|---|---|---|
| `0x81` | interrupt IN | 1024 | The IMU, 28 bytes at 1 kHz. Read by `live_config()` |
| `0x85` | bulk IN | 512 | MCU replies. Read by `live_config()` |
| `0x04` | bulk OUT | 512 | MCU commands, also flashes firmware. Only `allowed_mcu_frame()` bytes pass |
| `0x83` | interrupt IN | 512 | Display refresh events. Optional |
| `0x82` | bulk IN | 512 | Camera frames. Left idle unless `drain_camera` asks |
| `0x87`, `0x06` | bulk | 512 | The data channel. The bare `TransportConfig{}` default |

On the Ultra `0x04` and `0x85` are refused unless the config sets `allow_mcu_channel`.
`Backend::Hidraw` stays for the HID interfaces on the `35ca:1102` mic device, on request only.
`Backend::Auto` takes a matching hidraw node, otherwise usbfs.

## How it behaves

The rules in this section marked with **usbfs** do not apply to hidraw, which has no URBs.

**Reads stay queued (usbfs).** `urbs_per_endpoint` reads wait in the kernel on every listed IN
endpoint, 8 by default and at least 2. Every call to `read()` refills the queue, including one that
hands over a packet already in the ring, so it does not run dry while the caller works through a
backlog. While our thread is off the CPU the host keeps collecting until the queue runs out: 8 URBs
last 8 ms at 1 kHz. No read is ever parked: the queue is refilled at each reap, or closed. Packets go
into a preallocated ring, and the read path does not touch the heap.

**One URB, one packet (usbfs).** A URB is exactly the endpoint's `wMaxPacketSize`. USB ends a
transfer on a short packet or a full buffer, so a larger URB would hold each 512-byte frame back
until the next one arrived, and a smaller one would be babble. `packet_size` below an endpoint's
size is refused.

**Writes go out once.** One packet, no retry, no queue. On usbfs a write that times out is cancelled
and reported, and the link stays up. If it finished as the timeout fired, that is reported as `Ok`.
One thread drives `read()` and `write()`.

**A failed transfer closes everything (usbfs).** The host has already used its own retries by the
time we see a failure, the other queued reads tend to fail with it, and closing is what stops the
polling. The cost is that one bus glitch costs a reconnect, including the hold below. A failed
hidraw write returns `Error` and keeps the link, because it can mean the device refused one report.

**Losing the link.** `POLLHUP`, `POLLERR` or a failed transfer. `read()` hands over what already
arrived, then reports `Disconnected` once. The `Disconnected` event gives the first cause, how long
the stream had been silent and the last transfer error. Another program's reset looks like a failed
transfer with no hang-up, and the interface is claimed again on reopening.

**Finding it again.** While down, `read()` rescans sysfs every 25 ms. A scan took about 0.3 ms on
this machine and sends nothing over USB. There is no libudev or netlink monitor because uevents do
not reach the driver: side by side, a listener in a normal session got 73 events and one in
`tracking.py`'s sandbox got none.

**Settling before touching it.** Once the device is back, `arrival_settle` (2 s) passes with no open,
no claim and no queued reads, and the wait restarts if it vanishes again. Queued reads start the host
polling, and 12 of 20 reconnects right after a drop were followed by another drop, against 0 of 160
fresh starts. Causation is unproven, so a soak should tune this. A reconnect therefore takes the
kernel's re-enumeration plus 2 s, not the ~1 s first asked for, because zero drops comes first.

A device that turns up after `open()` found nothing is held the same way. One already there at
`open()` is not. `close()` does not skip a hold: `close()` then `open()` after a lost link waits it
out from the start, and a caller that loops them faster than the hold never connects.

**Limits on retrying (usbfs).** Reopening cannot clear a halted endpoint: nothing on the close or
claim path sends `CLEAR_FEATURE(HALT)`, and we refuse to. So each link cut short with the device
still there doubles the hold, up to 16 times `arrival_settle`, and never less than `open_retry_max`.
A link counts as cut short unless `healthy_session` (2 s) passed between opening and its last packet,
because a few packets say little: a halted OUT can sit beside a flowing IN. A longer session clears
the doubling, and so does the device leaving. A device that is there but will not open (busy, no
permission) is retried at a doubling interval up to 1 s.

## A departure from protocol.md, for Cody to decide

`protocol.md` says: "After any failed transfer, close everything and wait for re-enumeration", and
"Relax a rule only with a soak result". There is no soak result yet. This class does the first half.
It relaxes the second: with the device still present it reopens after the hold, without waiting for
a re-enumeration.

The reason is that a re-enumeration may never come. Nothing else makes a device that stays attached
enumerate again, resets are banned, and an unbounded wait would leave tracking dead after one transient
error, with the glasses fine. The rule's reason, polling a dead handle, is still met: a fresh open
is not a stale handle. The hold and its doubling keep it from being a loop.

The strict version is a small change: after a failure with the device still present, do not reopen
until it has been seen gone. Say so if that is wanted, or let a soak settle it.

## Using it

```cpp
using namespace std::chrono_literals;
using luma::IoStatus;

luma::Transport link{luma::TransportConfig{}};
link.open();                              // False is not fatal: read() keeps looking.
std::array<std::uint8_t, 1024> buffer;
for (;;) {
    auto r = link.read(buffer, 100ms);
    switch (r.status) {
    case IoStatus::Connected: /* reset the parser; the daemon decides when to send commands */ break;
    case IoStatus::Ok: /* parse buffer[0..r.bytes), from r.endpoint */ break;
    case IoStatus::Disconnected: /* pause the feed */ break;
    default: break;
    }
    while (auto event = link.next_event()) { /* log it */ }
}
```

`Connected` always comes before the first packet of its link. Reconnecting and refilling the queue
happen inside `read()`, so keep calling it, and never stall that thread. Only `interrupt()` may be
called from another thread. Timeouts and `interrupt()` bound the wait in `poll()`, not the kernel's
device lock, which ioctls wait on during a reset or removal.

## Never

An allowlist blocks every ioctl except claim, release, submit, reap and discard, and every URB that
is not bulk or interrupt, in two places. So these cannot happen by accident: USB reset, set
configuration, set interface, clear halt, control transfers, kernel driver detach. Only the
configured interface is claimed. If xrDriver holds it, the claim fails with `EBUSY` and we wait; we
never take it.

## Not proven

- **The usbfs path has never met a real kernel.** Tests run against `FakeUsbfs`. Its behaviour was
  checked line by line against kernel and libusb source (Linux 7.3 and master, not the running
  7.2.15), but it is still a model. Hidraw runs through real syscalls on a pty.
- **Reconnect time.** Our part starts once the device is back. The kernel took 0.91 s at best and
  3.25 s at the median to bring the glasses back over the last boot's 19 drops, and no driver can
  shorten that. Add `arrival_settle`, whose 2 s is `protocol.md`'s starting value.
- **The numbers are judgement, not measurement:** 8 URBs, the 2 s settle, the 2 s healthy session,
  the doubling limits. A soak should tune them.
- **One packet can vanish after a failed transfer.** The xHCI hard reset clears the host's data
  toggle on that endpoint, and nothing here clears the device's short of re-enumeration. Reopening
  does not resynchronise them. We refuse `CLEAR_HALT`: it is a control transfer with a 5 s timeout on a
  device that has just shown errors. So the receiver can acknowledge one packet and drop it. On OUT
  that means `write()` returning `Ok` proves nothing, and on IN `read()` misses one frame with no sign
  of it. Anything that must arrive needs a reply or a sequence check above this class. This comes
  from reading the source and has not been seen happening.
- **After a cold boot the glasses' node can have no user ACL.** The installed rule matches
  `ACTION=="add"` only, so `bind` clears the `uaccess` tag and logind skips the node at login. See below.

## Failed opens

Incident `20261104T051931Z-start-failed`: every xrDriver start after a reboot printed
`Cannot open device`, then `Failed to send USB command - MsgID: 0x100B`, then crashed in
`carina_a1088_read_custom_data`. The node had no ACL for the user, the phrase appears only after that
boot in a 4,074-line log, and the glasses had not re-enumerated since. The SDK went on using a
handle it never got. A replug gave the node its ACL, and the next xrDriver start connected and
calibrated.

Here a failed open ends all contact with the device: no ioctl, no URB, and `write()` returns
`Disconnected` without a system call. `read()` keeps retrying with back-off and returns `Timeout`, so a
replug or a new rule recovers without a restart. `last_error()` says what to do, and
`stats().last_open_errno` gives a daemon or incident report the reason as `EACCES` or `EBUSY`.

The fix for the machine is a replug, or `packaging/udev/71-xr-workspace-glasses.rules` on branch
`usb-drop-fix`.
