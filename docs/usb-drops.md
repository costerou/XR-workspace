# USB drops of the glasses

The VITURE Luma Ultra falls off USB during XR. This page is what was measured, what was changed, and how to check it on the
glasses. Numbers are from the journals and driver log of 2026-11-01 to 2026-11-03 unless a step says otherwise.

## What is known

| | |
|---|---|
| Time the glasses were connected | 25.5 h: 10.3 h in XR runs, 15.2 h idle |
| Drops in that time | 54: 33 in a run, 21 outside one |
| Rate in a run | 3.2 an hour |
| Outside a run | about 7 came 5 to 14 s after a run ended, several are a person replugging (gaps of minutes), about 3 are unexplained |
| Host events in the 5 s before a first drop | nothing recurring, against a control window |
| Port 5-1, on the same root hub | never dropped: the host's USB did not reset |

**The driver's USB errors are effects.** Every `get_display_mode ... returned -7` and `Failed to send USB command - MsgID: 0x0007`
comes after the kernel's disconnect. MsgID 0x0007 is XRLinuxDriver's once-a-second display mode poll, and every one succeeds while
the device is there.

**Drops come in pairs, and the second is the one that hurts.** 18 of 54 were the second of a pair, 2 to 28 s after the first. The
first comes back in about a second and leaves the display on; the second takes the display (DP-2) too, and 12 of the 16 session
ends near a drop came at it.

**The second follows the reconnect.** The driver reconnects by itself within about 3 s of a drop:

| Connection | Count | Followed by a drop within 13 s |
|---|---|---|
| Fresh driver start | 160 | 0 |
| Reconnect within 30 s of a drop | 20 | 12 |

This could be the reconnect causing the second drop, or glasses that are unstable for a while after the first: the logs cannot tell
those apart. Holding the reconnect back tests it (step U8).

**The driver runs far more than a run uses.** It never calls `xr_device_provider_set_dof_type_carina`, and the SDK's default is
6DoF. Measured on the real glasses, idle on a desk, with the SDK log level raised, 2026-11-03:

| | Default | 3DoF (quiet driver) |
|---|---|---|
| SDK says | `6DOF=true` | `6DOF=false` |
| Threads | 38, among them `MsckfVio`, `LocalMapping`, `LoopClosing` | 32, none of those |
| Driver CPU | about 33 % of a core | about 5 % |
| Pose feed | 1000 samples/s | 1000 samples/s |
| USB requests to 5-2.1 | 2272/s | 2271/s |

Nothing here reads position, so the SLAM does nothing for a run. **But the USB request rate did not change**, so 3DoF has not been
shown to turn the cameras off or to lighten the link. Step U2 measures the bytes.

**Not known:** what makes the first drop. The disconnects are clean (no over-current, no xHCI error) and the hub re-enumerates
itself, so the reset is on the glasses' side. That leaves a firmware reset under the driver's streaming, or a brief dip in the 5 V
from the 2 m cable's USB-A plug, which XR loads more (120 Hz, cameras, 1000 Hz IMU) than a plain display at 60 Hz. The first drop
leaving the display on argues a little against power, without ruling it out. Steps U9 and U10 separate them.

**Ruled out, or as good as:** a newer driver (2.11.9 is the latest release; the only later commit is a README edit); USB autosuspend
(the hub was never seen suspended, and the glasses' `power/control` is already `on`); the magnetic adapter upstream blames in issue
#122 (not used here); theme changes and test suites (measured earlier against every test run that day).

## What changed

**The session survives** (`tools/native_spike/survive.py`). The XR desktops and their windows belong to the compositor, so a drop
now ends the renderer and not the run. The session keeps the desktops, tells you, waits for the glasses, puts their 120 Hz mode and
placement back, and starts a new renderer, which rebuilds from the supervisor's state as at any start. The view does not exist
meanwhile: with the display gone there is nothing to look at.

- Stop works during the wait. The panel keeps its status.
- 120 s without the glasses ends the run the ordinary way, windows back on the monitor. 8 holds in 10 minutes does too.
- A person stopping, a clean end, and a renderer crash with the glasses fine are never held.
- A live incident report already says `the session kept running`, and `.result.json` gets `usb_holds`.
- A playing video does not survive: it ends, and its keys are released. Head tracking restarts relative to where you are looking.

**The quiet driver** (`spike/quiet_driver/quiet_driver.c`), preloaded into the installed `/usr/bin/xrDriver` when the install carries
one. It adds one call into the library that driver already loads, and links nothing itself.

| Setting in `~/.config/viture-workspace/quiet-driver.json` | Default | Does |
|---|---|---|
| `enabled` | `true` | `false`: the plain driver |
| `reconnect_hold_ms` | `20000` | A reconnect waits this long before touching the glasses. 0: off. |
| `quiet_polls` | `true` | The display mode goes to the glasses once per connection, not every second |
| `dof3` | `true` | 3DoF. `false` if head tracking drifts. |

**A clear refusal.** With glasses this user cannot open, Start says to replug them, where the driver used to segfault in the SDK.

That segfault, `Cannot open device` then `jcx start failed` then `carina_a1088_read_custom_data`, is not a 6DoF or camera failure and
says nothing about the drops. It is the device being unopenable: every instance since the 23:31 boot of 2026-11-03 has no ACL on the
device node, and it needs no streaming to happen. `read_custom_data` is the SDK's USB command reader, used in 3DoF too (`SendData`
calls ran in the 3DoF probe), and upstream's own backtrace in issue #122 is the same frame. Its incidents said "not network-isolated"
only because a dead process has no network to read; that message is fixed too.

**udev rules for you to install** (`packaging/udev/71-xr-workspace-glasses.rules`). After a boot with the glasses already plugged in,
the driver package's rule loses its access tag and the driver cannot open them until a replug. On 2026-11-03: `TAGS=:uaccess:seat:`,
`CURRENT_TAGS=:seat:`, no ACL for the user. This is upstream issue #129. It also keeps the hub and the tracking device from
suspending, as one thing fewer to wonder about.

```
sudo install -m 644 packaging/udev/71-xr-workspace-glasses.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --action=add --attr-match=idVendor=35ca
```

To undo: `sudo rm /etc/udev/rules.d/71-xr-workspace-glasses.rules`, then the last two commands.

## Tests

Offline: `quiet_driver` in ctest (the shim against a fake SDK), `tests/native_spike/test_survive.py` (including the real `_launch` and
`ViewControl` run twice in one session), `test_tracking.py`, `tests/xr_controller/test_install.py`.

On the glasses so far: only the 3DoF switch and the table above. **The hold has never met a real drop, and the reconnect hold and
the poll cache have never run on the glasses.**

## Steps on the glasses

U1 to U2 need no XR. Everything else needs this branch installed. Before each: `journalctl -k | grep -c 'usb 5-2: USB disconnect'`.

1. **U1.** Replug the USB plug or run the udev commands above. `getfacl /dev/bus/usb/005/* | grep -c user:cody` is not 0.
2. **U2.** With XR off: `python3 -m tools.usb_probe.probe --seconds 10`, then the same with
   `--shim build/release/libxr-quiet-driver.so --dof3`. Compare `kB_per_s`. A large drop on a bulk endpoint: the cameras stopped.
   No change: 3DoF saves CPU only, and the link is as loaded as before.
3. **U3.** Start XR. In `~/.local/state/xr_driver/driver.log`: `[quiet-driver] 3DoF: 0`. `ps -T -p $(pgrep -x xrDriver) | grep -c
   MsckfVio` prints 0.
4. **U4.** Head tracking in 3DoF. Look around for a minute, then face forward for 5 minutes. Desktops that creep sideways are drift:
   set `dof3` to `false` and report it.
5. **U5.** A drop by hand. In XR, pull the cable's USB-A plug for 5 s and put it back. Expected: a notification at once, the
   desktops still in `hyprctl monitors`, the picture back about 5 s after the plug, head tracking about 30 s after that, windows
   where they were, another notification.
6. **U6.** Pull it again and press Stop in the panel while it waits. Expected: a normal stop, windows on the monitor, no Recover.
7. **U7.** Pull it and leave it out for over 2 minutes. Expected: "did not come back", the run ends, windows on the monitor.
   Replug after, and start XR once to confirm nothing is left.
8. **U8.** Soak: XR for at least 2 hours of ordinary use, comparing drops per hour with the 3.2 above. Then
   `grep -a 'reconnect held' ~/.local/state/xr_driver/driver.log | wc -l` against the second drops in the kernel log: if a first drop
   now stands alone, the reconnect was causing the second.
9. **U9.** If first drops continue: the same soak with the USB-A plug in a powered hub or another port. A big fall points at power.
10. **U10.** If it did not, the same soak with `{"dof3": false}` and with `{"enabled": false}`, to see what the quiet driver bought.

Also worth a try once U5 passes: add a desktop, then drop (a new renderer has four); a drop with the overview open and a level roll
set; a drop during a video.
