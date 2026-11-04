# Luma capture

Records what a driver says to the glasses over USB. It answers what only the wire can: what starts the IMU stream,
which endpoint and layout it uses, how evenly it arrives, what a reconnect does, what preceded a disconnect, and
whether a driver keeps the zero-drop rules. Whether a soak passed is `tools/luma_soak`'s job.

The recorder only listens to the kernel's usbmon tap. It never opens the glasses, and nothing it does can send them
anything.

```sh
python3 -m tools.luma_capture check                 # ready? if not, the sudo steps
python3 -m tools.luma_capture record                # rolling capture until Ctrl+C
python3 -m tools.luma_capture mark still level      # note what you just did, for a running recorder
python3 -m tools.luma_capture report PATH           # everything; --audit exits 1 if a wire rule fails
python3 -m tools.luma_capture compare OLD NEW       # two captures side by side
python3 -m tools.luma_capture show FILE --limit 50  # raw records
python3 -m tools.luma_capture merge DIR -o one.lcap # one file for --replay
python3 -m tools.luma_capture pcap DIR -o one.pcap  # for Wireshark
```

Run these from the repository root. File format: `docs/capture-format.md`. Tests:
`python -m unittest discover -s tests/luma_capture -v`.

Captures go to `~/.local/state/viture-workspace/luma-capture`, readable only by you:

- `start/`: the first minute, which holds the driver's start-up commands
- `rolling/`: the last five minutes, overwritten as it goes
- `drops/<time>/`: for each disconnect, the five minutes before it and 30 s after

It stops by itself after 10 saved disconnects or 4 GiB. It keeps data for the glasses, for the companion's HID reports
and for USB housekeeping. It never keeps audio, and it keeps nothing from the pass-through camera or your headset. It
does record head motion, and the first 4 KiB of each side-camera transfer if those run on the glasses' own endpoints.

## Capture plan for the next drop

Nothing here starts, stops or changes XR. Capture happens alongside a normal session.

**Your call first.** The session being captured runs VITURE's closed SDK, and its licence has not been accepted. A
capture only watches USB traffic to your own device, but whether that fits the "open knowledge only" rule is yours to
decide. Until you do, the plan stays on paper.

**1. Grant access, once.** This lasts until the next reboot. Glasses plugged in:

```sh
sudo modprobe usbmon
sudo udevadm settle
sudo setfacl -m u:cody:r /dev/usbmon5
```

`5` is the glasses' USB bus today. `python3 -m tools.luma_capture check` prints the same lines with the current bus,
so go by that if it differs.

**2. Check.** `python3 -m tools.luma_capture check` should say `Capture access: ready`.

**3. Start the recorder before XR,** in its own terminal, so the start-up commands are caught:

```sh
python3 -m tools.luma_capture record --stop-after-drops 3
```

**4. Use XR as normal.** Start it from the panel. When a drop happens the terminal prints `drop detected`, then
`saved ...` 30 s after the last one in the burst. Restart XR as you normally would, and leave the recorder running.

**5. Sanity check the first session,** after a minute or so, in another terminal:

```sh
python3 -m tools.luma_capture report ~/.local/state/viture-workspace/luma-capture/rolling
```

Expect thousands of events, an endpoint marked `stream`, and no `usbmon lost` warning. If it says `0 USB events`
with XR running, stop and report that: the live path has never been run on real hardware.

**6. Read the result.** It stops after three drops, or press Ctrl+C:

```sh
python3 -m tools.luma_capture report ~/.local/state/viture-workspace/luma-capture/drops
python3 -m tools.luma_capture report ~/.local/state/viture-workspace/luma-capture/start
```

**7. Take the access back,** or just reboot:

```sh
sudo setfacl -x u:cody /dev/usbmon5
sudo modprobe -r usbmon
```

**Optional control.** Ten minutes with `record --duration 600` and XR not running shows what the bus does with the
glasses as a plain display, the state in which they never drop.

## Capture plan for the driver itself

These give `parse_imu`, the start command and the features what they are waiting for. Use a separate folder for each,
so they stay apart: `--out ~/.local/state/viture-workspace/luma-capture/imu`, and the same `--out` on each `mark`.

**IMU layout, about two minutes.** Start the recorder, then XR. Once tracking is up, take the glasses off and set them
down so they cannot move. Then:

```sh
python3 -m tools.luma_capture mark still level --out FOLDER
```

Wait 5 s. Lay them on their left side, wedged so they stay put, and run `mark still left side`. Wait 5 s. Stand them
on their nose bridge or lenses, again wedged, and run `mark still nose down`. Wait 5 s, then Ctrl+C. Any mark containing
"still" counts. The report then prints `ACCELEROMETER, by still poses`, plus the device clock and how many samples share
a transfer.

**Features, about five minutes.** Run `mark about to press brightness up`, press it, wait 5 s, and repeat for each
button, for putting the glasses on and off, and for changing the display mode from the glasses. The report lists what
crossed the wire in the 3 s after each mark. This only shows what the glasses report. Commands that set things
need software that sends them, and that is a further decision for you.

**3DoF against 6DoF.** Run one capture of each, then `compare` them, to see what the cameras cost on the wire.

**Our driver, when it reaches the glasses.** Capture it too, then:

```sh
python3 -m tools.luma_capture compare OLD_CAPTURE NEW_CAPTURE
```

That prints both drivers' rates and spacing side by side, then the wire rules for the new one, with anything it sent
that the old one did not.

## Reading a disconnect

`What stopped first` is the part to read. Times are relative to the disconnect.

| What you see | What it points to |
|---|---|
| The hub reports the port lost, and every failure comes after | The link went first; failed commands are a result, not the cause |
| A command is cancelled, or the stream stops, well before the port goes | The glasses stopped answering, then fell off |
| `hub SET RESET port N` just before | Software on this computer reset the device |
| `hub SET SUSPEND` nearby | Power management, not the driver |
| `over-current` in a port status | The port shut off for power reasons |
| `changed: enable` without `connection` | The hub or controller disabled the port after link errors |
| Commands per second jump shortly before each drop | Traffic pressure is worth suspecting |
| Command rate flat and unremarkable | Rate alone is not the cause |
| A second disconnect soon after a `Back:` line | The reconnect may have provoked it; look at what the reconnect sent |
| `UNCONFIRMED` | Transfers failed but the device never left. Probably a driver reconfiguring it |

These are starting points. One drop proves little, so compare several. Use `--window 30` to see back past a reconnect.

## Reading the rest

**MsgIDs** come from the documented frames, and each frame's CRC proves the layout on your own traffic: the report says
`proven here`. If no frame passes, it falls back to looking for a field that replies repeat, and if that settles nothing
it says the count is impossible instead of printing zero. A layout can be given with
`--framing offset=N,endian=little,magic=hex`.

**Stream** sections show how evenly transfers arrived, on the host's side. `possible clock` and `possible accelerometer`
are leads to check. `ACCELEROMETER, by still poses` has survived every pose you marked, and it takes all three axes
to leave one answer; after two poses it may list a neighbouring window as well. A clock's `ppm` comes from a few
seconds, so treat it as rough.

**Start-up** lists what each connection sent before the stream began, which is where the start command will be.

**Wire rules** are the zero-drop rules from `spike/driver/docs/protocol.md`, judged from the capture. `FAIL` on
XRLinuxDriver is expected and makes it the baseline. `?` means this capture cannot judge that rule, which is not a pass.
None of the rules is proven to stop drops. Pass `--baseline` a capture of VITURE's own software to check for anything
the driver sends that VITURE's does not, which matters most on the pipe that also carries flashing.

## What it cannot tell you

usbmon adds a small amount of work to every USB transfer. If drops vanish while recording, that is itself a clue
about timing, and worth saying. It sees transfers on the host's side, not electrical signals on the cable, so a bad
cable shows up only as errors and lost ports.

## A raw dump from `cat /dev/usbmonN`

`cat` needs no recorder, but its ring is the kernel's default 300 KiB, which a session with the cameras on overflows, so the
dump has holes. `record` is better when it can be used. To turn a dump into something safe to keep:

```sh
python3 -m tools.luma_capture import-usbmon dump.bin -o session.lcap
python3 -m tools.luma_capture imu-fixture session.lcap --start 666 --seconds 8 -o fixture.bin
build/release/driver/xr-luma-driver --replay session.lcap --ipc-prefix /tmp/some-folder/feed_
build/release/driver/luma_replay_report session.lcap --predict-ms 25
```

`docs/capture-format.md` says what the import keeps and drops. `--replay` always makes its own shared memory, because ftok
keys hold only 16 bits of the inode and a borrowed one could land on the live feed.
