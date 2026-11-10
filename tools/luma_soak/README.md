# Luma soak: proving zero USB drops

Watches for the glasses (35ca:1104) or their hub (1a86:8091) leaving USB, works out what the driver was doing in the 5 s
before each drop, and passes or fails a soak. Python standard library only. Run it from the repo root.

It only reads: `journalctl -k`, sysfs, `/proc`, the driver's log, and the pose feed through a read-only attachment that
never touches the feed's mutex. It sends nothing to the glasses and starts or stops nothing.

## The 30-minute gate

Start tracking first, then:

    python3 -m tools.luma_soak gate --duration 1800 --driver-name xr-luma-driver \
        --driver-log ~/.local/state/xr-luma-driver.log

| Exit | Result | Meaning |
|---|---|---|
| 0 | PASS | No drop, and the soak really tested something |
| 1 | FAIL | At least one drop (or a `--strict` / `--max-feed-gap-ms` limit) |
| 2 | INCONCLUSIVE | No drop seen, but the soak proved nothing; see the reasons printed |

`--driver-log` defaults to XRLinuxDriver's `~/.local/state/xr_driver/driver.log`, and `--driver-name` to both drivers.
Name the driver under test: other swarm threads' test doubles use the same process names. The path above is only an
example: the session runner must send the daemon's stderr to a file, because `tracking.py` sends it to /dev/null today.

Zero drops alone is not a pass. As a plain display these glasses never drop, so all of these give exit 2: too short, glasses
not on USB at the start or the end, the driver not running for 95% of the soak, the pose feed not advancing for 95% of it,
the kernel log unreadable at the end, a suspend, a disconnect on a port nothing identified, or the soak stopped early. A
bug in the tool also gives exit 2. `--no-require-feed` and `--no-require-driver` lift those two checks on purpose.

Other options: `--fail-fast` stops at the first drop. `--strict` also fails on a camera or mic disconnect, a kernel USB
warning or a driver restart. `--max-feed-gap-ms 100` also fails on a long tracking freeze. `--budget N` allows N drops.

The session file (default `~/.local/state/luma_soak/`) is written line by line, so a killed monitor still leaves evidence:

    python3 -m tools.luma_soak gate --session FILE --min-duration 1800
    python3 -m tools.luma_soak report --session FILE [--json]

## Saved logs

    python3 -m tools.luma_soak report --kernel-log kernel.log --driver-log driver.log

Make `kernel.log` with `journalctl -k -b -1 -o short-iso-precise`. Take the whole boot, or pass `--port 5-2`: a disconnect line
names only a port, and the device is learned from its enumeration lines. `gate --kernel-log` can fail but never pass.

## How a drop is found

Four witnesses, so one going quiet cannot read as zero: the `journalctl -k -f` stream; a full re-read of the kernel log at
the end; sysfs device numbers polled every second (a new number means the device left and came back, even if the log says
nothing); and the pose feed. Disconnect lines within 1 s on related ports are one drop. On the 54 real drops of
2026-11-01 to 03, a hub and its children went within 0.35 s, and the closest two drops were 2.4 s apart.

Ports are never assumed. A disconnect line names no device, and the layout depends on what is plugged in: the Luma Ultra
sat on 5-2.1 for all 50 of its drops, while what enumerated as VITURE Beast (35ca:1201) sat on 5-2.3.

## Reading the correlation

Each drop lists commands sent and failed, sample gaps, pose feed gaps, CPU, how long the driver waited for a CPU, and kernel
warnings from the 5 s before it. Only what is certainly before the drop counts. A failure after it is a result, not a cause.
XRLinuxDriver's libglasses lines carry no time, so in a saved log they are known only to lie between two dated lines; those
that may fall on either side are counted as "uncertain", never as evidence. A live session stamps each line as it arrives.

The report sets each signal against ordinary windows of the same length, because drops can line up with something by chance.

## Logging from xr-luma-driver

The C++ driver writes `luma: <word> key=value ... at=<epoch s>` lines (`spike/driver/src/main.cpp`), and `tracking.py`
sends them to `~/.local/state/viture-workspace/luma-driver.log`. `stale ms=` is read as a sample gap, `disconnected` as a
USB error, `retry` as a reconnect attempt and `reconnected gap_ms=` as a finished reconnect, summed in `driver_events`.
`at=` dates each line, so a saved log can be replayed too. The Rust driver writes the same lines. A test checks every `luma:` word in either driver's source is read.

A future driver may instead write one event per line, JSON or logfmt, with an `event` key:

    event=cmd_sent cmd=0x15           event=cmd_failed cmd=0x15 rc=-7
    event=sample_gap ms=38.2          event=usb_error rc=-7
    event=reconnect                   event=state state=calibrated

`unix_ms=` on any line gives its own time; without it the line is stamped on arrival. Other events are ignored.

## The C++ baseline and the Rust soak (D13)

`tools/luma_soak/soak.sh cpp` (also `cpp_baseline.sh`) runs the 3-hour soak of the C++ `xr-luma-driver`, and
`soak.sh rust` that of the Rust one; each stores its report in `docs/soak/`. Any number of drops is a valid measurement
there. `soak.sh rust` then runs `compare` against the stored baseline: no more drops than the C++ driver over equal hours,
exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE. See `docs/soak/README.md`.

Both builds run as `xr-luma-driver`, so the soak records each driver process's binary (`coverage.driver_exes`), and
`gate --driver-build cpp|rust` makes a soak of the other build, or of both, inconclusive.

## Fixtures

`tests/luma_soak/fixtures/` holds real logs passed through `python3 -m tools.luma_soak sanitize`, which removes the host
name, serial numbers, the hardware id and raw heap bytes. Tests: `python3 -m unittest discover -s tests/luma_soak -t .`
