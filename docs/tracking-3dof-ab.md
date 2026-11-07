# Tracking driver A/B: standard versus 3DoF

The question: do the USB drops stop when the glasses stay in 3DoF with the tracking cameras off?

XRLinuxDriver never asks the VITURE SDK for 3DoF, and the SDK's header says the default is 6DoF. So the Luma Ultra probably
runs its cameras and camera tracking through every session, though we only use orientation. If that load is what takes the
glasses' hub down, the 3DoF test build should stop the drops. Background: `spike/driver/docs/upstream-findings.md` on branch
`drv-upstream-issues`, and `spike/driver/docs/protocol.md` on `drv-protocol-research`.

Needs Cody, because it runs the glasses. The 3DoF build has never run on real glasses. It might be refused, tracking might feel
different, and yaw will probably drift more without the cameras.

## What is being compared

The panel no longer has a driver choice (2026-11-06, [own-drivers-default.md](own-drivers-default.md)). Choose an arm from a terminal, on a Luma Ultra, with no session running: `python -m tools.xr_controller tracking-override set xrdriver` for A, `set 3dof` for B, and `clear` when finished.

| Arm | Override | Binary |
|---|---|---|
| A, baseline | `xrdriver` | `/usr/bin/xrDriver` (2.11.9, commit `36e2139`) |
| B, 3DoF | `3dof` | `~/.local/share/viture-workspace/xrdriver-3dof/2.11.9-958908-pr147-pr148-3dof/xrDriver` |

Arm B is built by `tools/xrdriver_3dof/build.sh`. Its `manifest.json` records the upstream commit, both PR commits, the source
commit, and the patch and binary hashes. `tools/xr_controller/install.py` pins the binary's sha256.

What differs between the arms:

- **The 3DoF request.** `set_dof_type_carina(handle, 0)` after `create` and before `initialize` (`tools/xrdriver_3dof/3dof.patch`).
  The argument is `is_6dof`, so 0 is 3DoF. The disassembly shows the call and the zero.
- **PR #148.** It changes `src/connection_pool.c`, how a connection is freed when the device leaves during a blocking worker.
  It can only matter after a drop has begun, and it cannot prevent one. It could change what happens after a drop, so a
  difference in recovery between the arms may come from it. A difference in how many drops there are should not.
- **PR #147.** udev rule and tests only. The rule is not installed by this build, so the binary is the same with or without it.

The same in both: VITURE SDK code (`.text` of `libglasses.so` and `libcarina_vio.so` is byte-identical; the installed copies are
stripped, so the file hashes differ), `config.ini`, `driver.log`, the namespaces, the fake MAC and the readiness checks.

**Do not check the build by running it.** `xrDriver` reads no command line: `xrDriver --help` starts the driver against the
glasses. That is why it has only been inspected with `readelf`, `ldd`, `nm`, `objdump` and `strings`.

## After a reboot: replug first

From the 23:31 reboot on 11-03 until 00:22 on 11-04, every driver start crashed: `Cannot open device`,
`jcx start failed[35ca,1104]`, `Failed to send USB command - MsgID: 0x100B`, then a segfault in `carina_a1088_read_custom_data` in
`libcarina_vio.so`. Examples: incidents `20261104T051931Z` and `20261104T052212Z`.

That was the boot ACL bug, and it is **not evidence for or against the cameras**. The USB node was `root:root 0660` with no ACL, so
Cody's user could not open it, in any tracking mode. The system rule grants access only on `ACTION=="add"`, and a boot with the
glasses plugged in does not give the session that event. The crash is the SDK not checking that failed open.

The hub came back at 00:22:43, the node then had `user:cody:rw-`, and the same standard 6DoF driver calibrated at 00:23:02. So
permissions, not load, caused those crashes, and they say nothing about what causes the drops. Whether the 3DoF build would crash
the same way on a failed open is unknown.

A start now refuses with "XR can't open the glasses (USB permission)" instead of crashing (`tracking._check_usb_access`). The lasting fix is
PR #147, which grants access on every event but `remove`. It lives in the system's udev rule, needs sudo, and this build does not
install it.

## Before the first arm

Merged and installed by the orchestrator, with Cody's OK. The renderer does not change, but the controller and panel do, so restart
the controller and run `omarchy-restart-shell`, with XR stopped (see Build and install in `technical.md`).

Stay on the same cable, port and desk. Do not unplug during an arm: nothing in the log can tell an unplug from a drop.

Check the port first, because the checks below assume the glasses' hub is `5-2`. This should print a path ending in `5-2.1`:

```sh
grep -l 35ca /sys/bus/usb/devices/*/idVendor
```

Check the node can be opened. This looks at permissions only and touches nothing on the glasses:

```sh
d=$(dirname $(grep -l 35ca /sys/bus/usb/devices/*/idVendor | head -1)); n=$(printf '/dev/bus/usb/%03d/%03d' $(cat $d/busnum) $(cat $d/devnum))
test -r $n -a -w $n && echo ok || echo 'replug the glasses'
```

Write the start time of each arm down. `<start>` below is that time, as `"2026-11-04 10:00:00"`.

## Running an arm

1. Stop any XR session. The option is locked while one runs.
2. Set the arm with `tracking-override` (above). The panel's line names it. Head tracking must be ticked.
3. Start. The confirm box says "Head tracking via the 3DoF test build of XRLinuxDriver" for arm B and "Head tracking via
   XRLinuxDriver" for arm A. Keep the glasses still until it calibrates.
4. Use the desktop normally for 20 to 30 minutes, with real head movement: looking between desktops, turning, leaning. Keep both
   arms alike, so an idle arm does not get compared with a busy one.
5. Note how the tracking feels: yaw drift, jumps, how often Recenter is needed.
6. Stop from the panel.

A driver outlives its session, and switching arms restarts it with the chosen binary. It never reuses the other arm's driver.

## Checks

**Which arm really ran**, while it is running:

```sh
readlink /proc/$(pgrep -x xrDriver)/exe
journalctl --user --since "<start>" | grep 'head tracking driver:'
```

**Did the 3DoF request land** (arm B only). This line appears at each connect, and status 0 is success:

```sh
grep 'requested 3DoF' ~/.local/state/xr_driver/driver.log | tail -5
```

If the line is missing or the status is not 0, the arm is void: say so and stop. Arm A must have no such line after `<start>`.

**Are the cameras really off** (arm B, about a minute, safe during a session). Status 0 only says the SDK took the call.
`session_probe.py` reads `/proc`, sysfs and the shared memory, and opens nothing in `/dev`. It is on `drv-6dof-research`:

```sh
git show origin/drv-6dof-research:spike/driver/research/session_probe.py > /tmp/session_probe.py
python3 -I /tmp/session_probe.py --seconds 60 --out /tmp/probe-<arm>.json   # move your head while it runs
```

Run it in both arms. With the cameras off, expect `position_moved` false, a lower `glasses_urbs_per_s`, and no image-processing
thread. If arm B looks the same as arm A, the cameras stayed on: the arm says nothing about them and is void.

**Drops**, after each arm. The hub line is the count, and child devices such as `5-2.1` do not match it:

```sh
journalctl -k --since "<start>" --until "<end>" | grep 'usb 5-2: USB disconnect'
journalctl -k --since "<start>" --until "<end>" | grep -c 'usb 5-2: USB disconnect'
```

The hub drops in pairs 2 to 23 s apart, so count clusters: lines less than a minute apart are one drop.

**Driver log**, after each arm. Read the time around any drop, and which came first:

```sh
grep -nE 'get_display_mode|Failed to send USB command|Retrying driver connection|Device calibration complete|requested 3DoF' \
  ~/.local/state/xr_driver/driver.log | tail -40
```

**How the session ended.** A drop usually ends it with exit status 2 and "closed, removed or disconnected" in the unit's journal.
An incident report lands in `~/.local/state/viture-workspace/incidents/`.

**Faults that void an arm:** `xrDriver` crashing (`grep -a 'Segmentation fault' ~/.local/state/xr_driver/driver.log`; the driver catches
the signal itself, so `coredumpctl` shows nothing), or a run that never calibrates. A crash in `libcarina_vio.so` after `Cannot open
device` is a permissions failure, not a result. Reconnects after a drop may hit the same crash path; note it, because it is about
surviving a drop, not causing one.

## Reading the result

The day's baseline was 22 drop clusters in 6.9 session hours, one every 19 minutes, on the old kernel. At that rate, **25 clean
minutes happen by luck about one time in four** (e^(-25/19) = 27%). One clean arm B is an encouraging sign and proves nothing. The
longest clean run seen under the standard driver was 71 minutes, so `drv-review-qa`'s 3 hours total stays the real gate.

Run A, then B, and go by this:

| A | B | Meaning | Next |
|---|---|---|---|
| Drops | No drops | Promising, not proven | Repeat B. Three clean 25 minute arms in a row (75 minutes) would be about 2% by luck, a strong sign the 6DoF session is the trigger. Then a longer soak, and plan the own driver as IMU-only |
| Drops | Drops, about as often | The cameras are cleared | Look at firmware, the link, power or IMU streaming itself. Tell the driver swarm: its own driver may drop too |
| Drops | Drops, clearly fewer | Load may be part of it | Longer arms for both before concluding |
| No drops | Either | The condition changed: the machine moved from kernel 7.2.8 to 7.2.15 at 23:31 on 11-03 | Measure A again for longer before reading anything into B |

Success means: arm B valid by the checks above, A still dropping in the same room on the same day, and at least 75 clean minutes of
B. Tracking must also still be usable. If yaw drift makes it unusable, the build is still evidence about the cause, but not a stopgap.

## Switching back

1. Stop the session.
2. `python -m tools.xr_controller tracking-override clear`. The next start stops the test driver and starts our own.
3. To check: the panel's line says XR Luma driver.

If XR will not start (for example, the build folder went away), the same command fixes it: only the driver asked for is ever
started, and a missing or changed build refuses to start rather than run another one.

Rolling the code back needs no step of its own. Older code ignores a `tracking_driver` it does not know and uses the standard driver, and
`installed.json` never held the pin. Do it with XR stopped, since an older controller refuses the consent of a 3DoF run still in
progress. The build folder can be deleted at any time; nothing outside it changed. Nothing here touched `/usr`.
