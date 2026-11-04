# Luma capture

Records what the current driver says to the glasses over USB, so we can see what was happening just before a
disconnect and so the new driver can be tested on real data. The recorder only listens to the kernel's usbmon tap.
It never opens the glasses, and nothing it does can send them anything.

```sh
python3 -m tools.luma_capture check                 # ready? if not, the sudo steps
python3 -m tools.luma_capture record                # rolling capture until Ctrl+C
python3 -m tools.luma_capture report PATH           # rates, and what preceded each disconnect
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

It stops by itself after 10 saved disconnects or 4 GiB. It keeps data only for the glasses and for USB housekeeping,
never audio or video from the camera or microphones. It does record head motion.

## Capture plan for the next drop

Nothing here starts, stops or changes XR. Capture happens alongside a normal session.

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

These are starting points. One drop proves little, so compare several.

`MsgID 0x0007` is counted only when the capture itself shows where the MsgID lives: replies must repeat the field.
Otherwise the report says it cannot be counted and shows candidates, while the `Commands` table still counts every
transfer by shape. Once the layout is known, pass it with `--framing offset=N,endian=little,magic=hex`.

## What it cannot tell you

usbmon adds a small amount of work to every USB transfer. If drops vanish while recording, that is itself a clue
about timing, and worth saying. It sees transfers on the host's side, not electrical signals on the cable, so a bad
cable shows up only as errors and lost ports.
