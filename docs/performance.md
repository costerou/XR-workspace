# Head-tracking driver performance

How a sensor packet becomes a published pose in our own Luma driver (`spike/driver`), what it costs, and the one-time step that
turns on real-time scheduling. Code: `spike/driver/include/luma/rt.hpp`, `spike/driver/src/rt.cpp`. Tests and benchmark:
`spike/driver/bench/`.

Measured 2026-11-03 on a Ryzen 7 9700X, kernel 7.2.15, while the other driver threads were building and testing on the same machine
(load average 12 to 28). No real glasses took part, and **real-time scheduling has not been measured yet**: it needs the setup below.

## Targets and results

| Target | Result | |
|---|---|---|
| Added latency p99 under 0.5 ms | 23 to 40 us with real fusion, in the normal setup (ring, short slice, not pinned) | Met in 3 of 3 runs; the wider matrix below has misses |
| Under 1% of one core | 0.5 to 0.7% ring, 0.3 to 0.6% inline | Met |
| Nothing lost | 0 of about 288,000 samples in 22 runs | Met |
| No allocation in the hot path | 0, counting every `malloc` in the process | Met |

The p99 target is met by the design but not guaranteed on a busy machine: see "Without real-time" below. Real-time is what is meant to
close that gap, and that is still to be proved.

## The path

```
sensor --> kernel queue --> reader thread --> SPSC ring --> fusion thread --> seqlock in /dev/shm --> any reader
                            (stamps the time)   (no locks)   (fusion, publish)   (the writer never waits)
```

- **Reader thread.** Sleeps in the transport, wakes on a packet, stamps `CLOCK_MONOTONIC_RAW` at once (never slewed by NTP) and hands
  the sample over. It never waits for fusion: if the ring were full, the sample is counted in `ring_overruns`, because the reader's
  only job is to keep the kernel's queue drained.
- **Ring.** 256 slots, one producer, one consumer, no locks. Fusion sleeps on a futex; the reader makes a system call only if fusion
  really is asleep.
- **Fusion thread.** One publish per sample, in order. Nothing batches: a backlog is published sample by sample.
- **Seqlock.** A reader that is preempted or killed holds nothing, so it cannot stall the driver. A reader never hangs either: if the
  driver dies mid-write it gives up after a bounded number of tries, and the next driver repairs the sequence. The feed is
  `/dev/shm/xr-luma-pose` (`PoseRecord`, a fixed 40 bytes). Two writers are refused. Today's renderer does not read it yet; it reads
  `luma::Publisher`'s XRLinuxDriver-compatible feed.
- **`inline_` mode.** `Options::mode` also offers a single thread that runs fusion itself: one wake-up instead of two.

## Numbers

All with the fake transport at 1 kHz: a thread writes a 64-byte packet to a packet-mode pipe with hidraw's 64-packet queue, and the
reader pays a real `poll()`, scheduler wake-up and `read()`. Latency runs from just before that write to the end of the publish, and
is what the driver adds. It leaves out the glasses themselves and USB. 15 s each, two rounds, run interleaved so that a swing in the
machine's load hits every configuration alike. Times in microseconds. Each row used the stand-in fusion unless it says otherwise.

| Configuration | p50 | p99 | p99.9 | max | CPU % |
|---|---:|---:|---:|---:|---:|
| Ring | 7.6, 10.5 | 24, 262 | 1933, 2753 | 13243, 6616 | 0.51, 0.74 |
| Inline | 4.7, 5.0 | 16, 20 | 115, 426 | 1302, 1642 | 0.31, 0.33 |
| Ring, no short slice | 7.9, 14.3 | 672, 918 | 1901, 4588 | 2944, 5439 | 0.73, 1.23 |
| Inline, no short slice | 3.2, 5.1 | 14, 34 | 320, 1081 | 3016, 5511 | 0.23, 0.40 |
| Ring, mutex instead of seqlock | 4.7, 9.0 | 74, 451 | 950, 1606 | 6056, 8011 | 0.62, 0.68 |
| Ring, both threads pinned to one CPU | 5.9, 9.2 | 1704, 4850 | 9699, 21496 | 22486, 34878 | 0.72, 1.02 |
| Inline, pinned | 4.5, 5.8 | 82, 3473 | 688, 14156 | 1900, 24676 | 0.35, 0.42 |
| Ring, with a process reading the seqlock | 3.7, 9.2 | 26, 721 | 623, 15991 | 3688, 17010 | 0.59 |

Real fusion from `drv-fusion` (three rounds of 8 s, trial merge, not on any branch yet):

| Configuration | p50 | p99 | max | CPU % | fusion + publish p50 |
|---|---:|---:|---:|---:|---:|
| Ring | 8.2 to 10.0 | 23, 31, 40 | 1059 to 1963 | 0.54 to 0.66 | 0.3 to 0.4 |
| Inline | 5.4 to 6.8 | 24, 25, **852** | 2128 to 7025 | 0.38 to 0.58 | 0.4 to 0.8 |

A second process reading the seqlock saw a new pose 9 us after the sensor at p50 and 30 us at p99 in the quieter of its two runs.

What the numbers say:

- **The work is not the cost.** Fusion and publish take under 1 us. The cost is two scheduler wake-ups, so what matters is getting
  scheduled, and the worst cases are other programs on the same CPU.
- **Ring or inline is still open.** Inline wins on median latency and CPU, as one wake-up should. Its tail did not hold up: one real
  fusion run of three reached 852 us. Run this comparison again with real-time on, and let that decide. The default stays `ring`.
- **The short slice is worth having.** See below.
- **Do not pin without real-time.** A pinned normal thread cannot move off a CPU that something else is using. This was the worst
  thing tried, at 2 to 5 ms p99. Pinning is for after the setup, and is optional even then.
- **Seqlock against mutex is not about speed.** In this run nothing else used the mutex, and the difference is lost in the noise. The
  seqlock is about what a reader can do to the driver.
- **The margin against lost samples.** The kernel queue holds 64 ms of samples, and the longest stall seen, 35 ms, stayed inside it:
  no overflow in any run. That is under a scheduling stall, not a USB one: USB drops are a separate matter, and a real one ends the
  session whatever this layer does.

## Without real-time

Until the setup is done this machine refuses both `SCHED_FIFO` and nice -10, so the driver falls back to a normal thread with a
**short EEVDF slice**: since Linux 6.12 any thread may ask for one, with no privilege, and it runs sooner after waking.

In a direct comparison at load 20, each pair run back to back, it cut ring-mode p99 from 2228 to 26 us, 5898 to 1507 us and 1638 to
385 us. That is better in every pair, and also shows the limit: it can still miss 0.5 ms on a very busy machine (1507 us there).
Inline mode gained little, being already fast. `ThreadPolicy::short_slice` turns it off.

## Setting up real-time (once)

The driver needs permission to use real-time priority. It never asks for sudo, and tracks without this, just with worse worst cases.

```bash
sudo install -d -m 755 /etc/security/limits.d   # not there on a stock Arch install; tee will not create it
sudo tee /etc/security/limits.d/90-xr-luma-driver.conf >/dev/null <<'EOF'
cody - rtprio 20
cody - nice -10
cody - memlock 131072
EOF
```

Then **reboot**. Logging out is not enough: the limits reach services through the systemd user manager, and lingering keeps that
running across a log-out. Afterwards this must print 20, 30 (the kernel's way of writing nice -10) and 131072:

```bash
systemd-run --user --wait --pipe -q sh -c 'ulimit -Hr; ulimit -He; ulimit -Hl'
```

To undo it: `sudo rm /etc/security/limits.d/90-xr-luma-driver.conf`, and reboot.

Why these and not `setcap`: `tracking.py` runs the driver inside `unshare --user`, and `sched_setscheduler` checks capabilities against
the initial user namespace, so a capability does nothing there. The limits are checked without one, and they survive the namespace.

Then measure, with XR off because `--load` keeps every CPU busy:

```bash
cd spike && cmake -S . -B ../build/release -G Ninja -DCMAKE_BUILD_TYPE=Release && ninja -C ../build/release luma_rt_bench
B=../build/release/driver/bench/luma_rt_bench
$B --check                         # targets, quiet machine
$B --check --mode inline           # the same, one thread
$B --check --load 24               # targets with every CPU busy: the case real-time is for
$B --check --load 24 --mode inline
```

`--check` exits 1 if a target is missed. The first line of the output says `reader fifo` if real-time was granted.

Safety: priority 10 sits above normal programs and below the kernel's interrupt threads, so USB itself cannot be starved. A FIFO thread
that stops blocking gets SIGXCPU after 200 ms of CPU, and SIGKILL after 1 s, so a bug cannot freeze a core; FIFO is refused if that
guard cannot be set. Children never inherit real-time.

## Plugging it in

`Pipeline<Item>` takes a Source and a Sink. `Item` is `ImuSample` plus whatever else travels with it.

```cpp
struct Source {
    luma::rt::SourceStatus next(Item& item);  // read with a timeout, stamp monotonic_raw_ns() at once, parse
};
struct Sink {
    void operator()(const Item& item) noexcept;  // fusion.update(), publisher.publish(), seqlock publish; must never block
};
```

Call `lock_memory()` once at startup and log what `reader_policy()` and `fusion_policy()` report. If `needs_setup` is set, log
`rt_setup_commands(user)` once. Reconnects belong in the Source (return `idle` while the link is down) or in stopping and restarting
the pipeline, which is supported.

## What this does not prove

- **Real glasses and USB.** The fake transport is not hidraw. The kernel's USB path, the glasses' own delay and the USB polling interval
  all come on top. "Real glasses: USB latency" below is how that is measured; its numbers are still to be recorded.
- **Real-time.** The FIFO path has only run against a fake `SysOps`. `apply_real_thread` will check it for real once the limits exist.
- **Busy-CPU results.** `--load` has not been run: XR, or something that looked like it, was up when its turn came, so that was skipped.
  "Busy" above means the other work running that night, which varied.
- **The renderer's reading.** The `/dev/shm/xr-luma-pose` feed has no real consumer yet.
- **CPU %** counts the driver's two threads in user and kernel time. Interrupt work for USB is on top, and would be there with any driver.

## Running the tests

```bash
ctest --test-dir build/release -R luma_rt   # 46 checks and a 1 s benchmark smoke run; no hardware, no privilege
```

The smoke run checks that nothing is lost or allocated and nothing about timing, which a busy machine would fail. ThreadSanitizer, ASan
and UBSan runs are clean; to repeat one, compile `rt_test.cpp` and `rt.cpp` with `-fsanitize=thread`.

## On the real capture

`xr-luma-driver --replay` on the 20-minute session in `spike/driver/docs/capture-findings.md`, parse through publish, on one
thread: 0.12 µs median, 10 to 70 µs worst, 1.2 million packets in 0.2 s. So the driver's own code is a rounding error next to USB
and scheduling, which is why the daemon runs inline rather than through the ring. USB and scheduling have still only been
measured against a fake transport.

## Wake-ups

`docs/rust-rebuild-plan.md` D10 targets 1,100 context switches per second or fewer and 0.3 % of one core or less, with no added
latency. The plan's "about 2,000 wakes per second" is the two-thread ring above (reader and fusion each wake per sample). The
live daemon is not that: it runs on one thread, and `Transport::read` already reaps every finished URB on each wake, in a loop
that ends on `EAGAIN`. So it wakes once per host completion, and a burst the host hands over together costs one wake-up.

What D10 changed (2026-11-10): a packet that the wake's own reap just put in the queue is handed over without a second reap.
That reap had ended on `EAGAIN` microseconds before, so it could only find nothing. One packet now costs one `poll`, two
`REAPURBNDELAY` (the one that gets it and the one that ends the loop) and one `SUBMITURB`, instead of three reaps. The refill
on a backlog stays: a `read()` that starts with packets queued still reaps first, so the kernel's queue never runs dry. Latency
is unchanged: the stamp is still taken at the reap, and nothing waits.

### Offline proof

`luma_transport` runs the whole live path (TransportLink, Daemon, real decoder and fusion) on the fake kernel, which wakes `poll`
through a real eventfd. It measures the daemon thread's context switches from `/proc/self/task/TID/status` over 3.2 s:

| Feed | Switches per second | Wakes per packet | CPU % (thread, fake kernel included) |
|---|---:|---:|---:|
| 1 packet per 1 ms | 929 to 991 | 0.96 to 0.985 | 0.65 to 0.75 |
| 2 packets per 2 ms, handed over together | 477 to 504 | 0.49 to 0.50 | 0.35 to 0.67 |

Measured at load average 30 to 45 on 16 CPUs. The test checks at most 1.05 voluntary wakes per packet and 0.55 for pairs, and
that 50 packets cost exactly 100 reaps and 50 submits (150 before the change). The CPU figures include the fake kernel's mutex
and eventfd work and a busy machine, so the CPU target can only be judged on the glasses.

### On the glasses

With XR stopped, the glasses plugged in and still on the desk for the first seconds (a fresh bias folder means a calibration):

```bash
cmake -S spike -B build/release -DCMAKE_BUILD_TYPE=Release
make -C build/release -j"$(nproc)" xr-luma-driver
spike/driver/tools/wake_check.sh build/release 60
```

It runs the driver with a private feed and bias folder, waits 15 s, then counts the process's context switches and CPU over
60 s. It prints `PASS` when switches/s is 1,100 or fewer and CPU is 0.3 % or less, with `samples/s` near 1,000. Then run
`spike/driver/tools/latency_baseline.sh build/release 120` and compare its excess p99 with the D1 table below: "no added latency"
means no worse.

| Date | Switches/s | Voluntary/s | CPU % | Samples/s | Latency excess p99 |
|---|---:|---:|---:|---:|---:|
| | | | | | |

## Real glasses: USB latency

The baseline every later driver change is judged against (`docs/rust-rebuild-plan.md`, D1). It measures how late each IMU packet
reaches the driver, from the glasses' own stamp to the moment the transport reaps the finished URB.

### Results

Not recorded yet: it needs the glasses, with XR stopped. Run the steps below and fill this table from the `json` lines.

| Machine | Packets | Rate | Lost | Excess p50 | p99 | p99.9 | Max | > 1 ms | > 8 ms | Inter-arrival p99 | Drift |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Idle | | | | | | | | | | | |
| Loaded (one busy loop per CPU) | | | | | | | | | | | |

Record the date, the kernel, and the `luma: scheduling` line each run printed (it says whether real-time was granted).

### How it is measured

`xr-luma-driver --latency-log FILE` works live and in replay. For every IMU packet it appends 24 bytes: the host time, the device
time, and the sequence number.

- **Host time** is `CLOCK_MONOTONIC`, stamped when the transport reaps the URB (usbdevfs) or when `read` returns (hidraw). It is
  the time the packet first reached our code. In a replay it is the capture's time. A `.lcap` replay uses usbmon's kernel
  completion time, which leaves out our own wake-up.
- **Device time** is the stamp in the packet, in 100 µs ticks.
- Each successful open adds a marker record.

The log runs in the read path, on the daemon's thread. It keeps 4,096 records (about 4 s) in memory and writes each block with
one `write(2)`. If a write fails, the driver logs it once and stops the log. Tracking carries on.

The two clocks share no zero and tick at slightly different rates, so absolute latency cannot be measured here. What
`luma_latency_report` measures is each packet's delay beyond the fastest packets around it:

1. For each packet, d = host time − device time.
2. Within 10 s windows, the lowest line under every (device time, d) point is fitted. This is the lower convex hull edge across
   the window's mean time. Its slope is the clock drift.
3. A packet's **excess** is its height above that line. The fastest packets have 0.

Some packets are dropped before the percentiles are taken:

- The first packet after each open: it can be one the previous reader left in the device.
- A packet whose device time is not ahead of the last one: the Ultra sends its first ~60 packets twice.
- A device restart, a reopen, or a silence over 1 s starts a new segment with its own line.

Lost packets are counted from the 16-bit sequence numbers.

The device stamps in 100 µs ticks, so about 100 µs of the excess is rounding, not delay. Read results below that as "within a
tick". The excess also hides any delay every packet shares, such as the polling interval's average. It is the jitter on top that
later changes can move.

### Offline proof

`ctest --test-dir build/release -R luma_latency_log` (`spike/driver/tests/latency_log_test.cpp`) runs synthetic streams with a
known delay on every packet. The report recovers them within 1 µs at p50, p99, p99.9 and max, and the drift within 0.1 ppm, at
+75, −40 and 0 ppm. The streams include the Ultra's opening resends, a sequence wrap, lost packets, a device restart, a silence
and a reopen. With 100 µs stamp rounding added, the results stay within one tick. `luma_daemon`'s `test_latency_log_binary` does
the same through the real `xr-luma-driver` binary, with a replay that drops the device and brings it back.

### Running it on the glasses

With XR stopped and the glasses plugged in:

```bash
pgrep -x xr-luma-driver || pgrep -x xrDriver || echo "no driver: go"
cmake -S spike -B build/release -DCMAKE_BUILD_TYPE=Release
make -C build/release -j"$(nproc)" xr-luma-driver luma_latency_report
spike/driver/tools/latency_baseline.sh build/release 120
```

The script runs the driver for 120 s with the machine idle, then 120 s with one busy loop per CPU. The driver writes a private
feed and keeps its gyro bias in the run's own folder, so the live feed and the saved bias are not touched. The script prints a
report for each run and the folder that holds the logs. `luma_latency_report FILE` reads any log again.

A run counts if:

- Both reports print a `json` line.
- `packets` is close to 1,000 per second of the run.
- `segments` is 1 and `lost` is 0, or each loss is explained by a USB message in `journalctl -k`.

