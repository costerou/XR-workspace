# luma::Transport

The byte pipe to the glasses. It knows nothing about what the bytes mean. Code in
`include/luma/transport.hpp` and `src/transport.cpp`; tests in `tests/transport_test.cpp`.

## Primary path: usbfs bulk

`35ca:1104` has one vendor interface and no kernel driver, so no hidraw node exists for it. The
transport talks to `/dev/bus/usb/BBB/DDD` with usbdevfs ioctls and needs no libusb. The default
`TransportConfig` is that path: interface 0, bulk IN `0x87`, bulk OUT `0x06`.

| Endpoint | Type | Max packet | Use here |
|---|---|---|---|
| `0x87` | bulk IN | 512 | Default read: the data channel's replies |
| `0x06` | bulk OUT | 512 | Default write: the data channel's requests |
| `0x83`, `0x81` | interrupt IN | 512, 1024 | Candidates for the IMU stream, unconfirmed |
| `0x82` | bulk IN | 512 | Possibly camera frames. Leave idle |
| `0x04`, `0x85` | bulk | 512 | MCU channel, also flashes firmware. Refused on this device |

Which endpoint carries the IMU stream is unknown until a usbmon capture exists
(`protocol.md`). Add it to `in_endpoints` and nothing else changes: URB type and size are read from
sysfs per endpoint, and `read()` says which endpoint each packet came from.

`Backend::Hidraw` stays for the HID interfaces on the `35ca:1102` mic device, on request only.
`Backend::Auto` takes a matching hidraw node, otherwise usbfs.

## How it behaves

**Reads stay queued.** `urbs_per_endpoint` (8) reads wait in the kernel on every listed IN
endpoint. Every call to `read()` refills the queue, including one that hands over a packet already
in the ring, so the queue does not run dry while the caller works through a backlog. While our
thread is off the CPU the host keeps collecting frames until the queue runs out: 8 URBs last 8 ms at
1 kHz. The queue is always either full or closed. Packets go into a preallocated ring, and
the read path does not touch the heap.

**One URB, one packet.** A URB is exactly the endpoint's `wMaxPacketSize`. USB ends a transfer on a
short packet or a full buffer, so a larger URB would hold each 512-byte frame back until the next
one arrived, and a smaller one would be babble. `packet_size` below an endpoint's size is refused.

**Writes go out once.** One packet, no retry, no queue. A write that times out is cancelled and
reported, and the link stays up. A write that fails ends the link. One thread drives `read()` and
`write()`.

**Any failed transfer closes everything.** That is `protocol.md`'s rule, and it may only be relaxed
with a soak result. The host has already used its own retries by the time we see a failure, the
other queued reads tend to fail with it, and closing is what actually stops the polling. The cost
is that one bus glitch costs a reconnect, including the settle below.

**Losing the link.** `POLLHUP`, `POLLERR` or a failed transfer. `read()` hands over what already
arrived, then reports `Disconnected` once. The `Disconnected` event gives the first cause, how long
the stream had been silent and the last transfer error. Another program's reset looks like a
failed transfer with no hang-up, and the interface is claimed again on reopening.

**Finding it again.** While down, `read()` rescans sysfs every 25 ms. A scan took about 0.3 ms on
this machine and sends nothing over USB. There is no libudev or netlink monitor because uevents
do not reach the driver: side by side, a listener in a normal session got 73 events and one in
`tracking.py`'s sandbox got none.

**With `--event-reopen` (D8, off by default).** inotify is a filesystem event, not a uevent, so it does
reach that sandbox. While down, an inotify watch on `/dev/bus/usb` and each bus directory under it
(`IN_CREATE`, `IN_DELETE`, `IN_ATTRIB`) wakes `read()` the moment a node appears, goes or gets its ACL.
The kernel makes the node in `device_add`, before the configuration is set and sysfs lists the
interfaces, so one event starts a burst of scans every 5 ms for 500 ms. The 25 ms timed rescan stays
as the fallback, and a failed `inotify_init1` leaves only it. The watch fd exists only while down and
never goes through `Sys`: it touches nothing on USB, so the shared ioctl trace is unchanged.

**Settling before touching it.** Once the device is back, `arrival_settle` (2 s) passes with no
open, no claim and no queued reads, and the wait restarts if it vanishes again. Queued reads start
the host polling, and 12 of 20 reconnects right after a drop were followed by another drop, against
0 of 160 fresh starts. Causation is unproven, so a soak should tune this. The result is that a
reconnect takes the kernel's re-enumeration plus 2 s, not the ~1 s first asked for, because zero
drops comes first. A first open never waits. With `--event-reopen`, a device back after a link
that had sustained traffic (`healthy_after`) skips this hold, and the daemon skips its own 200 ms
`--settle-ms` too, listening 20 ms before the start instead. A link that died young keeps both holds
exactly as before, so the flapping guard is unchanged. On the fake kernel the first pose comes 21 to
27 ms after the node returns, against about 2.2 s without the flag (`luma_transport_test`, `d8` lines).

**Limits on retrying (experimental mitigation, November 4).** Reopening does not clear a
halted endpoint: nothing on the close or claim path sends `CLEAR_FEATURE(HALT)`, and we refuse to.
Each link that dies before sustained payload traffic doubles the hold while the device remains
present: 4, 8, 16, then 32 s with defaults. The hold is bounded by the existing rule of 16 times
`arrival_settle`, with `open_retry_max` as a floor for penalized sessions. The counter saturates.
Brief successful packets no longer erase this history. Same-device retry, including after EPIPE,
is still allowed; the transport cannot guarantee that it will repair an endpoint halt.

`TransportConfig::healthy_after` defaults to 2 s (minimum 1 ms; no new CLI option). Nonempty
payloads must span that interval in one session, with no observed receipt gap greater than
`min(100 ms, healthy_after)`. A longer gap restarts the interval at the next packet. Once sustained
traffic qualifies, history clears and a subsequent loss in that session gets only the base 2 s
hold. This measures transport payload receipt, not valid IMU samples or device timestamps; junk
or traffic on another configured nondrain endpoint can qualify. Scheduler stalls can delay
qualification. A first open is still immediate. Observed disappearance clears the history and
rearrival gets the base hold. A disappearance entirely between scans is not detected; this patch
adds no identity tracking. Failed opens retain their separate doubling retry interval up to 1 s.

The daemon calls `Link::close()` on loss. After a read error, `TransportLink::read()` has already
set `link_up_ = false`, so that close is a no-op and Transport's penalty survives. For watchdog
silence on an otherwise open link, wrapper close still calls `Transport::close()`, clearing its
history; the daemon's separate silent retry policy applies. **This patch does not solve that
silent-close path**, nor all command/write failure paths that close a still-marked-up wrapper.

This is a bounded mitigation experiment for recurring short error sessions, not a root-cause fix.
The supplied incident describes valid `0x81` traffic between repeated `0x85` EPROTO errors on the
same enumeration. It does not establish that retries cause downstream hub loss. No live validation
was performed for this patch; only fake USB, pty, and replay tests ran. No install, driver/service
control, USB reset, clear-halt, configuration change, or endpoint removal is part of the patch.
See [drop-error-backoff-experiment.md](drop-error-backoff-experiment.md) for red/green evidence.

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

Reconnecting and refilling the queue happen inside `read()`, so keep calling it, and never stall
that thread. Only `interrupt()` may be called from another thread. Timeouts and `interrupt()` bound
the wait in `poll()`, not the kernel's device lock, which ioctls wait on during a reset or removal.

## Never

An allowlist blocks every ioctl except claim, release, submit, reap and discard, so these cannot
happen by accident: USB reset, set configuration, set interface, clear halt, control transfers,
kernel driver detach. Only the configured interface is claimed. If xrDriver holds it, the claim
fails with `EBUSY` and we wait; we never take it.

## Not proven

- **The usbfs path has never met a real kernel.** Tests run against `FakeUsbfs`. Its behaviour was
  checked line by line against kernel and libusb source (master, not the running 7.2.15), but it is
  still a model. Hidraw runs through real syscalls on a pty.
- **Which endpoint streams.** The default reads `0x87` only, and until a capture names the stream,
  nothing says IMU packets arrive there.
- **Reconnect time.** Our part starts once the device is back. The kernel took 0.91 s at best and
  3.25 s at the median to bring the glasses back over the last boot's 19 drops, and no driver can
  shorten that. Add `arrival_settle`, whose 2 s is `protocol.md`'s starting value.
- **The numbers are judgement, not measurement:** 8 URBs, the 2 s settle, the doubling limits. A soak
  should tune them.
- **One packet can vanish after a failed transfer.** The xHCI hard reset clears the host's data
  toggle, and only `CLEAR_HALT` clears the device's. We refuse that: it is a control transfer with a
  5 s timeout on a device that has just shown errors. Reopening does not resynchronise either. The
  device then acknowledges a packet and drops it, once, so `write()` returning `Ok` does not prove
  arrival, and anything that must arrive needs a reply or a sequence check above this class. This
  is from the source and has not been seen happening.
- **After a cold boot the glasses' node can have no user ACL.** The installed rule matches
  `ACTION=="add"` only, so `bind` clears the `uaccess` tag and logind skips the node at login. See below.

## Failed opens

Incident `20261104T051931Z-start-failed`: every xrDriver start after a reboot printed
`Cannot open device`, then `Failed to send USB command - MsgID: 0x100B`, then crashed in
`carina_a1088_read_custom_data`. The node had no ACL for the user, the phrase appears only after that
boot in a 4,074-line log, and the glasses had not re-enumerated since. The SDK went on using a
handle it never got.

Here a failed open ends all contact with the device: no ioctl, no URB, and `write()` returns
`Disconnected` without a system call. `read()` keeps retrying with back-off and returns `Timeout`, so a replug
or a new rule recovers without a restart. `last_error()` says what to do, and `stats().last_open_errno` gives
a daemon or incident report the reason as `EACCES` or `EBUSY`.

The fix for the machine is a replug, or `packaging/udev/71-xr-workspace-glasses.rules` on branch
`usb-drop-fix`.
