# Live test plan: our driver on the real glasses

**Status: nothing here has been run.** No step has touched the glasses. Every result in this file
so far comes from tests with fakes, or from reading sysfs.

Written 2026-11-03 for the orchestrator to run with Cody. The orchestrator runs the commands. Cody
does everything physical, and everything with `sudo`.

## Rules for the whole plan

- **Ask Cody before each stage.** A yes to one stage does not cover the next.
- **Never** start, stop or restart XR, a `viture-xr-*` unit, the controller, Hyprland, or an
  `xrDriver` you did not start in this plan.
- **Never reset the device.** No `usbreset`, no writing to sysfs, no `authorized` or `unbind`.
- Run only the commands written here. If a step fails, stop and report it. Do not try variations,
  and above all do not try other command bytes on the glasses.
- Keep these apart when reporting: tests, this plan's tracking-only runs, and a real XR session.

### Stop at once if

- A `usb <hub>: USB disconnect` line appears in any stage except R, unless the stage is counting them.
- The picture in the glasses changes, flickers or goes black, or the glasses get unusually warm.
- The driver logs `luma: error`, or dies.

To stop: `kill -TERM "$PID"`, wait 3 s, and only if it is still there `kill -KILL "$PID"`. Use only
the pid this plan started. If the glasses do not come back, Cody replugs them. That is the only
recovery.

## Two findings that shape this plan

**The glasses have no hidraw node.** `35ca:1104` has one interface, class 00, with no kernel driver
and seven endpoints. Our driver reaches it through usbfs, `/dev/bus/usb/BBB/DDD`. The only VITURE
hidraw nodes belong to the microphone device, `35ca:1102`, interfaces 3 and 4. Endpoint `0x83`
(interrupt in, 512 B, polled every 1 ms) fits a 1 kHz IMU, but that is a guess from the descriptor.

**After a reboot with the glasses plugged in, nothing can open them.** The installed rule,
`/usr/lib/udev/rules.d/70-viture-xr.rules`, has `ACTION=="add"` on its usb line. After the reboot at
23:31 on 2026-11-03 the node had `TAGS=:uaccess:seat:`, `CURRENT_TAGS=:seat:` and no ACL, while the
hidraw nodes, whose rule has no such limit, had both. At 23:33 XRLinuxDriver logged
`Cannot open device, may not connect`, then segfaulted, twice. A hot-plug works, which is why this
stayed hidden. One boot was observed, so the cause is very likely but not proven. Step P4 fixes it.

## How much a soak can prove

From the journal for 2026-11-03: 37 disconnect lines, which are 22 separate drops (more than 60 s
apart), in 6.9 hours of XR sessions: about 3.2 drops per running hour. `acceptance.md` counts drops
in bursts and gets 1.1 to 1.8 per hour. The lower rate is the cautious one, so both are shown.

| Zero drops in total | Old driver does that by luck, at 1.1/h | At 3.2/h |
|---|---|---|
| 30 min | 58% | 20% |
| 1 h | 33% | 4% |
| 1.5 h | 19% | under 1% |
| 3 h | 3.7% | under 0.01% |

So this plan uses the gate in `spike/driver/docs/acceptance.md`: **30 minutes with no drop is the
first go/no-go, and 3 hours in total with no drop is what lets us say the problem is fixed.** Any
drop restarts the 3 hour count. Deliberate unplugs in stage R do not count.

**A bigger catch:** all those drops happened during XR sessions, with video on the same cable. A
soak with XR off may never drop, on either driver. So step S1 measures the old driver with XR off
first. If it does not drop, S2 is only a smoke test, and stage X is the real proof.

**A second catch:** XRLinuxDriver runs the glasses with their cameras on, and our driver reads only
the IMU. If drops stop, that may be the reason, not anything clever in our driver. It would still
be a good result, but say so when reporting it.

## What this plan needs from other pieces

Checked against the sibling worktrees on 2026-11-03. None of it is on master yet.

| Needed | From | Expected |
|---|---|---|
| Binary `build/release/driver/xr-luma-driver` | drv-daemon | Flags `--ipc-prefix`, `--replay` |
| Log lines | drv-daemon | `luma: connected`, `calibrating`, `calibrated`, `disconnected`, `reconnected gap_ms=`, `stats`, `stopped`, `error` |
| IMU start command and packet format, **not public** | drv-protocol-impl, from a usbmon capture | Sends once per connection, nothing else |
| usbfs transport, no reset, hotplug without netlink | drv-transport | Works inside the network namespace |
| Soak gate | drv-soak-monitor | `python3 -m tools.luma_soak gate` |
| Wire capture (optional) | drv-recorder | `python3 -m tools.luma_capture` |
| `tracking_driver: luma` | drv-integration | Stage X only |

If a flag or log line has changed, fix this file before running it, not during.

## Setup for every stage

Paste into the shell that runs the plan. Run it from the worktree holding the test build.

```sh
export RUN="$HOME/.local/state/viture-workspace/luma-hil/$(date +%F-%H%M)"; mkdir -p "$RUN"
export BIN="$PWD/build/release/driver/xr-luma-driver"
export HUB=5-2   # P2 confirms this
luma() {  # as tracking.py runs it: no network. Each log line gets a time, comparable with the kernel log
  unshare --user --map-current-user --net -- "$BIN" --ipc-prefix "$RUN/feed_" "$@" \
    2> >(while IFS= read -r line; do printf '%s %s\n' "$(date +%T.%6N)" "$line"; done >> "$RUN/driver.log") &
  PID=$!
}
drops() {  # usage: drops "$T0". Only this boot, which is all journalctl -k ever shows
  journalctl -k -q --since "$1" --grep "usb $HUB(\.[0-9]+)?: USB disconnect" -o short-precise --no-pager
}
```

## Stage P: prerequisites

No step here opens the glasses.

**P1. Build.** The orchestrator builds the merged test branch. It installs nothing.

```sh
cmake -S spike -B build/release -G Ninja -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
ninja -C build/release xr-luma-driver; echo "ninja=$?"
"$BIN" --help
```

Pass: `ninja=0`, and `--help` lists `--ipc-prefix`.

**P2. The cable.** Cody plugs the glasses' USB cable into the computer.

```sh
python3 -m tools.luma_probe
```

Pass: the report names `35ca:1104` at 480 Mbit/s. Set `HUB` to the name on its `behind` line.

**P3. XR off, no driver.** Cody turns XR off from the panel. The orchestrator only checks.

```sh
systemctl --user list-units 'viture-xr-*' --no-legend
pgrep -x xrDriver
```

Pass: both print nothing, and the controller may stay running. Then run the probe. It must not say
`BUSY`. If it does, it names the process. Stop and ask Cody; do not kill anything.

**P4. Fix access.** Only if the probe says `ACTION NEEDED` or warns `rule-add-only`. It prints these
commands, and Cody runs them. The rule file passes `udevadm verify`.

```sh
sudo tee /etc/udev/rules.d/71-xr-luma.rules > /dev/null <<'EOF'
# XR Workspace: the logged-in user may reach the VITURE glasses without root.
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="35ca", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
```

Then Cody unplugs the glasses and plugs them back in.

Pass: `python3 -m tools.luma_probe; echo "exit=$?"` prints `READY` and `exit=0`. A warning that hub
`5-2` may autosuspend is expected. The usb-drop-fix thread owns that.

**P5. Clean start.** Note the time and check nothing dropped in the last five minutes.

```sh
export T0="$(date '+%F %T')"; drops "$(date -d '-5 min' '+%F %T')" | wc -l
```

Pass: `0`. If not, wait until the glasses have been stable for five minutes.

## Stage D: detection only

Reads sysfs. Nothing opens the glasses.

**D1. Record the facts.**

```sh
python3 -m tools.luma_probe --json > "$RUN/probe-before.json"; echo "exit=$?"
```

Pass: `exit=0`.

**D2. Compare the endpoints.** The report's interface 0 must list `0x81`, `0x82`, `0x83`, `0x04`,
`0x85`, `0x06`, `0x87`. Pass: the same seven. A different list means different firmware. Stop.

**D3. Nothing holds it.** Pass: interface 0 shows `driver none`.

## Stage I: first open, read the IMU for 60 s

**This is the first time our code opens the glasses. Ask Cody first.** The glasses lie flat and
still on the desk. Nobody is wearing them.

**Do not start this stage until the start command and packet format come from a real capture.** The
Luma Ultra's IMU packet and start command are not public, and Cody has yet to decide on the usbmon
capture of the old driver (drv-recorder has the steps). Never guess: firmware commands may share
this channel.

**I1. Start.**

```sh
luma; echo "pid=$PID"
```

Pass: `luma: connected` in `$RUN/driver.log` within 5 s. If no samples arrive within 5 s, stop. The
start command or endpoint is wrong, and that goes back to the protocol thread.

**I2. Calibration.** Pass: `luma: calibrated` within 30 s, with the glasses untouched.

**I3. Read for 60 s.** Wait 60 s, then read the newest `luma: stats` line.

```sh
tail -n 20 "$RUN/driver.log"; drops "$T0" | wc -l
```

Pass: `samples` grew by about 60 000 in the 60 s (1 kHz), `bad_packets=0`, `bad_poses=0`,
`commands` at the number the protocol thread expects, and `0` drops.

**I4. Clean stop.**

```sh
kill -TERM "$PID"; wait "$PID"; echo "status=$?"; tail -n 3 "$RUN/driver.log"
python3 -m tools.luma_probe; echo "exit=$?"
```

Pass: `luma: stopped reason=signal`, and the probe says `READY`, with interface 0 back to
`driver none`. The picture never changed.

**I5. What went on the wire (optional).** Needs Cody's one-time usbmon setup from
`python3 -m tools.luma_capture check --bus 5`. Repeat I1 to I4 with the recorder running.

Pass: the capture shows the start command once and no display-mode command (`0x0007`).

## Stage F: compare with XRLinuxDriver

Only one driver can hold the glasses, so each gets the same 60 s routine, one after the other.

Put something on the desk that the glasses can be pressed against, such as a book, so they go back
to exactly the same place. Routine for both runs, cued by desktop notifications:

- 0 to 20 s: still against the book.
- 20 to 40 s: Cody moves them by hand, turning them about 90 degrees left, right, up and down.
- 40 to 60 s: back against the book, hands off.

```sh
cue() { ( sleep 20; notify-send 'MOVE THE GLASSES'; sleep 20; notify-send 'PUT BACK, HANDS OFF'; sleep 20; notify-send DONE ) & }
```

**F1. Start the old driver.** Glasses still against the book.

```sh
python3 -m tools.native_spike tracking start
```

Pass: JSON with `"calibrated": true`. It runs without network, as in a normal session.

**F2. Record it.**

```sh
cue; python3 -m tools.luma_probe.feed record --seconds 60 --out "$RUN/old.csv"
```

Pass: about 60 000 samples.

**F3. Stop it.**

```sh
python3 -m tools.native_spike tracking stop; python3 -m tools.luma_probe; echo "exit=$?"
```

Pass: `{"stopped": true}`, and the probe says `READY`.

**F4. Start ours.** Same place.

```sh
luma
```

Pass: `luma: calibrated`.

**F5. Record it.**

```sh
cue; python3 -m tools.luma_probe.feed record --seconds 60 --feed "$RUN/feed_pose_orientation" --out "$RUN/new.csv"
kill -TERM "$PID"; wait "$PID"
```

Pass: about 60 000 samples.

**F6. Compare.**

```sh
python3 -m tools.luma_probe.feed report "$RUN/old.csv"; python3 -m tools.luma_probe.feed report "$RUN/new.csv"
python3 -m tools.luma_probe.feed compare "$RUN/old.csv" "$RUN/new.csv"; echo "exit=$?"
```

Pass: `exit=0`, which means rate at least 90% of the old driver's, no gap over 100 ms, still drift
no more than 0.5 deg/min worse, and return error no more than 1 degree worse. These margins are
first guesses, because no real numbers exist yet. Keep them unless Cody agrees to change them.

The recorder polls from Python, so it can lag by a millisecond or so. Rates near 1 kHz read a little
low for both drivers alike.

## Stage S: soak, counting drops

**S1. Old driver, XR off, 60 min (the control).** Cody wears the glasses and moves their head
normally, or uses the computer with them on.

```sh
export T1="$(date '+%F %T')"; python3 -m tools.native_spike tracking start
python3 -m tools.luma_soak gate --duration 3600 --driver-name xrDriver; echo "gate=$?"
python3 -m tools.native_spike tracking stop; drops "$T1" | wc -l
```

Pass: the full 60 min ran and the drop count is recorded. Any count is a valid result here, because
this run measures the old driver, and `gate=1` is expected if it drops. The count decides what S2 means:

- Old driver dropped: XR-off soaks do reproduce the fault, so S2 is real evidence.
- Old driver did not drop: S2 is only a smoke test. Say so in the report.

**S2. Our driver, 30 min, head motion.** Same activity.

```sh
export T2="$(date '+%F %T')"; luma; sleep 40; export CPU0="$(ps -o cputimes= -p "$PID")" RSS0="$(ps -o rss= -p "$PID")"
python3 -m tools.luma_soak gate --duration 1800 --driver-log "$RUN/driver.log" --driver-name xr-luma-driver \
  --feed "$RUN/feed_pose_orientation" --max-feed-gap-ms 100; echo "gate=$?"
echo "cpu $CPU0 -> $(ps -o cputimes= -p "$PID") rss $RSS0 -> $(ps -o rss= -p "$PID")"
kill -TERM "$PID"; wait "$PID"; drops "$T2" | wc -l
```

Pass: `gate=0` and `0` drops, the target. `gate=2` means the soak proved nothing, so find out why and
repeat it. A drop ends the run: keep `$RUN`, and do not rerun until the cause is looked at.

If `luma_soak` is not merged yet, `sleep 1800` and the final `drops` count still answer the question.

**C1. CPU and memory.** Read from the S2 output.

CPU % = (second cputime − first cputime) ÷ 1800 × 100. Pass: under 1% of one core, the performance
thread's target, and lower than the old driver's. Measure the old one the same way during S1 if
Cody wants the comparison. Pass for memory: `rss` grew by less than 1 MB.

## Stage R: reconnect

Unplugging is the test, so a drop is expected here. Cody does each plug. **If the same cable carries
the picture, windows on the glasses' monitor may move to the other screen.** Tell Cody first.

The driver stays running through R1 to R3, in its namespace, as it would in a real session.

**R1. Normal drop, 3 times.** Start `luma`, wait for `luma: calibrated`, and note the time. Cody
unplugs at the computer, waits 5 s, plugs back in. Wait 15 s between rounds.

```sh
tail -n 8 "$RUN/driver.log"
journalctl -k -q --since "$T0" --grep 'idVendor=35ca, idProduct=1104' -o short-precise --no-pager | tail -n 3
```

Pass, each round: `luma: disconnected`, then `luma: reconnected ... calibration=` showing it kept
its calibration, within about 1 s of the kernel's `New USB device found` line for `1104`. Measure
from that line. The hub and the other devices come up first: that took about 2 s on 2026-11-03, so
the time from the plug going in will be longer, and that part is not the driver's. `acceptance.md`
measured the kernel at 0.9 s at best and 4.2 s typically.

**R2. Fast replug, 3 times.** As R1, but back in after about 1 s. Pass: the same.

**R3. Long outage.** Unplug for 60 s. Pass: the driver stays alive, reconnects, and recalibrates
(`luma: calibrating`, then `luma: calibrated`) because too much time has passed to trust the old
calibration. Keep the glasses still after plugging in.

**R4. Start with nothing plugged in.** Stop the driver. Cody unplugs. Run `luma`, wait 10 s, Cody
plugs in. Pass: it waits without errors or crashes, then connects and calibrates.

**R5. Stop during an outage.** Unplug with the driver running, then `kill -TERM "$PID"`.
Pass: it exits within 1 s, status 0. Plug back in.

**R6. Afterwards.** Pass: `python3 -m tools.luma_probe` says `READY`, `coredumpctl list xr-luma-driver`
shows nothing new, and `luma: stats` shows `reconnects` matching the plugs.

An error on the first open after a replug would suggest the driver got there before udev set the
ACL. It must retry quietly. Report it if it gives up.

## Stage L: latency

Host and glasses share no clock, so latency from sensor to pose cannot be read directly here. This
is what can be measured, and what cannot.

**L1. Time added by our code.** The performance thread's bench, with a fake 1 kHz transport. It does
not need the glasses. Pass: p99 under 0.5 ms added.

**L2. Steadiness of arrival, live.** From F6, read `arrival_spread_ms` and `p99_gap_ms` for both
drivers. Pass: ours is no worse than the old driver's. It shows jitter, not absolute delay.

**L3. Real motion to picture.** Not measured in this plan. It needs XR running and a high-speed
camera on the lenses, tapping the glasses. Do it in stage X if Cody wants it.

## Stage X: the real proof, a real XR session

Needs Cody's acceptance, because it means the installed app uses our driver. Do it only after every
stage above passes, as a separate request. The orchestrator handles merge and install.

**X1.** Cody sets `tracking_driver: luma` and starts XR from the panel, working normally.

**X2.** Watch for drops with XR running: first 30 min, then on to 3 h in total on the same build,
across sessions if needed. Same cable, port and display mode as the old driver's numbers, and no
usb-drop-fix change during the series. Run this once per session, setting `--duration`:

```sh
python3 -m tools.luma_soak gate --duration 1800 --driver-name xr-luma-driver \
  --driver-log "$HOME/.local/state/viture-workspace/luma-driver.log"; echo "gate=$?"
```

Pass: `gate=0` in every session, adding up to 3 h. By the table above that is about 95% confidence
that the problem is fixed, even at the lower rate.

**X3.** Pass: head tracking feels at least as good to Cody, with no drift Cody notices.

**X4. Going back.** Set `tracking_driver: xrdriver`. Nothing else changes. Pass: the next XR start
uses `xrDriver`, seen with `pgrep -x xrDriver`.

## Stage B: the next reboot

Do not reboot for this. The next time the machine restarts anyway, with the glasses plugged in:

**B1.** Before plugging or unplugging anything, run the probe. Pass: `READY`. It proves the P4 rule
lasts through a boot, which nothing else in this plan can show. If it says `ACTION NEEDED`, the
cause above was wrong. Report the tags from `udevadm info /dev/bus/usb/BBB/DDD`.

## Results

Fill in as stages finish. Keep `$RUN` for each run.

| Stage | Date | Result | Notes |
|---|---|---|---|
| P | | | |
| D | | | |
| I | | | |
| F | | | |
| S1 old driver, drops in 60 min | | | |
| S2 our driver, drops in 30 min | | | |
| C CPU %, memory growth | | | |
| R | | | |
| L | | | |
| X hours with no drop, out of 3 | | | |
| B | | | |
