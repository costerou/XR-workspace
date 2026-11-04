# Display control without XRLinuxDriver

**Recommendation: send no display commands over USB.** XR Workspace already gets 1920x1200@120 by setting
the DisplayPort mode, and XRLinuxDriver has never told a Luma Ultra to change mode or brightness. Run
`xr-luma-driver` with none of the `--display-*` flags, which is the default.

`spike/driver/include/luma/display.hpp` exists as a safe place to put a command if one turns out to be
needed. Today it cannot send anything: the Luma Ultra's set commands are not publicly known, so
`UnmappedWire` refuses every command.

## Against the bar: zero drops first, then latency, then features

| Goal | What this piece does |
|---|---|
| Zero USB drops | The safest display command is none, so the default sends zero display bytes, ever. The flagged path waits 5 minutes after every connect and steps back for good after a drop |
| Lowest latency | Off the pose path entirely. `tick()` does no I/O until the one command is due, and with no flag it returns at once |
| Highest refresh | 120 Hz comes from DisplayPort, the panel's top mode, with no USB involved |
| Features | Mode choice works today without USB: Hyprland offers 1920x1200 at 60, 90 and 120 and 1920x1080 at 60 and 90. Brightness and dimming are in `drv-features` |

## What XRLinuxDriver does on a Luma Ultra

Source: `github.com/wheaney/XRLinuxDriver` at `9589084` (2.11.9). The log prints `[CARINA]`, and
`viture_supports_native_dof()` is false for that device type, so the whole "capture and override" path in
`src/devices/viture.c` (the only place it sets 1920x1200@120) stops early on these glasses. That path is for
the Beast.

| Job | SDK call | Does it run on a Luma Ultra? | Needs a reply? | Retry | Risk |
|---|---|---|---|---|---|
| Read display mode | `xr_device_provider_get_display_mode`, seen on the wire as MsgID `0x0007` | **Yes.** Once at every connect, then **once a second for as long as connected** (`manage_state_thread_func` in `src/driver.c`, `update_state_from_device` in `src/state.c`, `viture_device_is_sbs_mode`), plus once before any SBS change | Yes: `execute_usb_command_with_response` | None of its own; the 1 Hz loop simply asks again, with no backoff | Highest of the four, see below. There is no config option to turn it off |
| Set 120 Hz / 1920x1200 | `xr_device_provider_set_display_mode` (`0x44`) | **Never called, by any device path** | Unknown. Its documented errors omit `NO_DATA` and `DEVICE_REJECTED`, which suggests it may not wait for one | n/a | High and unmeasured: changes the DisplayPort signal under a running session |
| SBS / 3D off | `xr_device_provider_switch_dimension(0)` | Only if a user asks for SBS through the control file. XR Workspace does not | Unknown | None. The stream is stopped and restarted around it | High: the header says 2D here means **1920x1080@60**, so it would knock us off 120 Hz. It also fails while the IMU streams (`viture.c:929`), and the driver warns "glasses can be sensitive to rapid mode changes" (`driver.c:565`) |
| Brightness | `xr_device_provider_set_brightness_level` (0 to 8 on Luma), `set_duty_cycle` | **Never called** | Unknown | n/a | Low in principle, but no known frame and no need: the glasses have buttons. Now owned by `drv-features` |

`set_default_display_mode` is documented as not supported on the Luma Ultra, so a mode cannot be saved in
the glasses either.

## Is MsgID 0x0007 causing the drops?

**It is mostly a witness, not proven innocent, and we do not need it either way.** Timeline below merges
`~/.local/state/xr_driver/driver.log` with the kernel log for 2026-11-03 (37 disconnects, 29 driver starts).
`usb-drop-fix` owns the root cause; this is only what bears on the command.

For the failures:
- It works: about 10,700 once-a-second reads succeeded before the 11 drops that started a retry loop, some
  sessions lasting over an hour.
- In all 11, exactly one 0x0007 failure appears before the driver's "Retrying" line, which comes 1.2 to 2.1 s
  **after** the kernel's disconnect. A read every second would land in that gap and fail. That is what you
  would see if the read is hitting a device that has already gone.
- Most failures are a side effect of that: after a drop the driver keeps its old SDK handle (`disconnect(true)`
  is "soft") and goes round a 9-second loop of Retrying, connected, failed reads. On 2026-11-03 nearly every
  repeat failure sits inside one of those loops.

Against clearing it completely:
- The `[E]` lines carry no timestamp, so "the read failed after the drop" is inferred from the spacing, not
  seen directly.
- Of 11 second drops that hit during recovery, **4 fall within 0.35 s of a reconnect** (-0.32, -0.20, +0.05,
  +0.08 s), when reconnects come 9 s apart. By chance you would expect under one. That is a small sample
  looked at after the fact, but it says a burst of commands on freshly re-enumerated glasses may knock them
  off again. The burst includes a 0x0007 read, but also stream start and other SDK traffic, and the log
  cannot separate them.
- 0x0007 failed for seconds at a time with no disconnect logged (for example 22:59:55 to 23:00:04). The stale
  handle would explain that, but it could also mean the read was failing against a device that was present.

The error text is ambiguous too: `-7` is `DEVICE_REJECTED` in VITURE's `viture_result.h` and `TIMEOUT` in
libusb, and the log does not say which layer produced it.

What this means for us: never poll, stay quiet after a reconnect, and treat a USB command as guilty until
tested. `DisplayControl` does all three.

## What is known about the wire format

`docs/protocol.md` from `drv-protocol-research` is the reference, and it corrects an earlier version of this
section. In short:

- `35ca:1104` is not HID. It has one vendor interface with bulk and interrupt endpoints, so only usbfs can
  reach it. No hidraw node exists.
- `0x0007` travels in a 512-byte `FF FE` frame on bulk pipes `0x06` out and `0x87` in, with CRC-16/XMODEM.
  That layout comes from [go-viture/luma](https://github.com/go-viture/luma) and the driver log. The log calls
  this "V1", so the V2 ids such as `0x3141` do not apply here.
- **Knowing the frame shape does not give us the commands.** No message id for setting mode, 3D or brightness
  on this device is public. The only ids seen in use are `0x0007` and `0x100B`.
- **Guessing ids is off the table.** The neighbouring pipes `0x04`/`0x85` also carry firmware flashing, and 25
  further command bytes are known to exist with unknown effects. Trying ids against glasses that already drop
  off USB could do far worse than a drop. So `UnmappedWire` refuses, and `protocol.md`'s "Never send" list
  bans all of these commands.

Other sources, as read on 2026-11-03: [viture-v2](https://github.com/elasticjava/viture-v2) `8b29df0` (MIT),
[viture-webxr-extension](https://github.com/bfvogel/viture-webxr-extension) `7b27af0`,
[go-macos/iokit](https://github.com/go-macos/iokit) `ad6ef75`,
[viture_virtual_display](https://github.com/mgschwan/viture_virtual_display) `6d50d39` (MIT).
The mode bytes in `display.hpp` come from the constants headers in the XRLinuxDriver repo. Those headers are
marked VITURE copyright, so if the licence decision covers them too, those few numbers need a second look.
No VITURE library was linked, run or disassembled for this work.

## What XR should do

| Need | Do this | USB traffic |
|---|---|---|
| 1920x1200@120 | Keep `apply_session_mode` in `tools/native_spike/hyprland.py`: it sets the DisplayPort mode and verifies by reading it back | None |
| SBS off | Do not command it. If the glasses are in SBS, the mode XR asks for will not be available and the readback fails, which is the signal. Show a message | None |
| Brightness | Glasses' buttons. The scene already has its own brightness in the Background menu | None |
| Knowing the mode | Read Hyprland's monitor list, never the glasses | None |

## If a command is ever needed

`DisplayControl` only sends when someone passes a flag **and** a real wire exists:

| Flag | Effect |
|---|---|
| `--display-mode=1920x1200@120` | Also 1920x1200@60/90 and 1920x1080@60/90, the modes the glasses advertise |
| `--display-exit-3d` | Ignored when a mode is given, since a 2D mode already leaves SBS. Last resort: tracking has to wait with it, see below |

Rules, each with a test in `spike/driver/tests/display_test.cpp`:

- At most one command is in play, and it sets something. There is no get to call.
- `DisplaySink` can only write. Nothing reads a reply, so nothing can time out and retry.
- It gets one attempt per connection, even if the write fails.
- Nothing goes out until the link has been up for **5 minutes**. On 2026-11-03, 5 of the 11 sessions that
  dropped did so sooner, at 18, 30, 33, 87 and 135 s. The first version waited 30 s, and replaying those
  sessions showed it firing in three of them. XR restarts the driver after a drop, so the process cannot
  remember a bad spell, and the wait has to apply to every connect.
- If a drop comes within 60 s of a command, this process never sends it again. A false block only costs an
  optional command that nothing needs.
- `exit_3d` is never sent while the stream runs, and `holds_stream()` tells the daemon to wait. That would
  hold tracking back for the whole 5 minutes, which is why a mode is the better choice if one is ever needed.

Daemon wiring (`drv-daemon`):

```cpp
luma::DisplayOptions display;                       // parse_display_flag() on each argv entry
luma::UnmappedWire wire;
luma::DisplayControl control(display, wire, sink);  // sink wraps Transport::write, on the bulk OUT pipe
// on connect: control.on_connected(now);  on drop: control.on_disconnected(now);
// in the main loop: if (auto e = control.tick(now, stream_running)) log(format_display_event(*e));
// start the IMU stream only once !control.holds_stream()
```

## Open questions

1. **Does 120 Hz hold with no driver at all?** Very likely: the source shows no mode command on this device.
   But `libglasses` is closed, and the 1920x1200@119.999 reading on DP-2 (also listing @60 and @90, and
   1920x1080@60/90) was taken in a normal session; `xrDriver` was running at the one reading I could pair with
   a process check. To settle it: with no XR run and no `xrDriver`, in a normal session, run
   `hyprctl monitors all | grep -A2 DP-2`, apply 1920x1200@120 the way XR does, and read it back. Then look at
   the glasses for a clean picture.
2. **How do the glasses leave SBS by hand on a Luma Ultra?** The button-lock call is "Luma / Luma Pro only",
   which does not say whether the Ultra has such a button. Nor is it known what mode list Hyprland shows in SBS.
3. **Real set commands.** The `usbmon` capture in `protocol.md` would show `0x0007` and how tracking starts. It
   would **not** show mode or brightness sets, because XRLinuxDriver never sends them, and triggering them
   means running the SDK, which is not licensed for us. So unless a public source appears, `UnmappedWire` stays.
4. **Who else needs to know:**
   - `usb-drop-fix`: the once-a-second read has no off switch, and the driver reuses a dead handle after a drop.
   - `drv-features`: its command gate waits 2 s after connect and blames a drop within 3 s. The timeline here
     argues for far longer: follow-up drops came 3 to 22 s after the glasses returned, and 5 of 11 sessions
     dropped inside 5 minutes. Under a zero-drop bar, brightness should use the same 5 minutes and 60 s.
   - `drv-daemon`: the module only needs linking if a flag is ever wanted. Leaving it out is also fine.
