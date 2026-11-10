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

## What the driver does at start (D9)

Live runs only, unless `--no-rt`: `lock_memory()` (declines below a 128 MiB `RLIMIT_MEMLOCK`, so `MCL_FUTURE` can never make an
mmap fail), a 256 KiB stack prefault, then `apply_thread_policy` on the daemon's one thread: `SCHED_FIFO` 20 if permitted, else
nice -10, else a normal thread with the short slice. It always starts; it never asks for sudo. One line says what it got:

```
luma: scheduling class=fifo priority=20 cpu=any memory=locked
luma: scheduling class=normal short_slice=yes cpu=any memory=unlocked setup=docs/performance.md#setting-up-real-time-once
```

The second is what this machine prints without the limits file (checked 2026-11-10, with `chrt -p` showing `SCHED_OTHER` and a
100000 ns runtime). Under `--load 16` with the machine's load average near 30, that fallback measured p99.9 143 ms and lost 89
samples to kernel queue overflow: the gap the limits file is for. The FIFO numbers are still to be measured.

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

With real-time granted, `--check` also requires p99.9 at most 100 us (D9's target). To compare pinning, add `--reader-cpu N`
to the bench; the driver's own switch is `--rt-cpu N`, which pins only once `SCHED_FIFO` is granted and is off by default until
that comparison says pinning helps.

Safety: priority 20 sits above normal programs and below the kernel's interrupt threads, so USB itself cannot be starved. A FIFO thread
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

## Renderer pose handoff (D2)

`luma_pose_channel_bench` (`spike/driver/bench/pose_channel_bench.cpp`) runs the real `Publisher` at 1 kHz and the renderer's
real `PoseFeed` against it, on a private prefix and channel. The writer thread gets the driver's thread policy. In two of the
three seqlock scenarios, and in the mutex scenario, a third thread holds the mutex feed's lock 3 ms in every 10, as a slow reader
of the old feed would. Pose age is read time minus the sample's `CLOCK_MONOTONIC` time. *Behind* counts reads that got an
older sample than one the writer had already finished publishing: the handoff's own lag. *Writer late* is how far after its
1 ms deadline the writer stamped a sample.

Measured 2026-11-10 on the same machine, while other builds ran (load average 28 to 37). The writer ran on the short slice,
without real-time, for 10 s a scenario. These are the pose age p99, the writer late p99 in brackets, and the result:

| Scenario | Run A | Run B | Run C | Run D |
|---|---|---|---|---|
| Seqlock, 120 Hz reader, mutex held | 1.02 (-) pass | 1.08 (0.22) pass | 0.79 (0.02) pass | 3.08 (3.47) miss |
| Seqlock, reader flat out, mutex held | 1.00 (-) pass | 1.00 (0.30) pass | 1.00 (0.05) pass | 2.23 (2.03) miss |
| Seqlock, 120 Hz reader, no contention | 0.77 (-) pass | 0.77 (0.03) pass | 1.51 (0.43) miss | 1.44 (0.85) pass |
| Channel absent: mutex feed, mutex held | 12.3 | 12.3 | 27.8 | 30.4 |

Run A was before *writer late* was recorded. In every seqlock run, every read came from the seqlock, with 0 reader waits and 0
behind. The ring of history worked, and a read took 1.5 to 7.6 us at p99. The old path, under the same contention, waited on
about one read in three, for up to the 2 ms limit. Its writer also skipped about 30% of its writes on the held lock.
Percentiles are the upper bounds of 3% buckets: run C's 1.51 is the bucket just over 1.5.

So the handoff adds nothing to the age but the read's place between two samples (0 to 1 ms at 1 kHz). Every miss follows a writer
stamping late: the age p99 is about the writer's lateness p99 plus that 1 ms. The gate holds when the driver thread runs on time.
On a busy machine without real-time, it does not, and the same is true of the live driver. A first run at load average 58,
before the writer had the thread policy, reached a p99 of 11 to 59 ms. There, the flat-out reader fell back to the mutex feed
when the writer stalled for more than 100 ms. Real-time scheduling is what is meant to make this hold under any load, and it is
still not set up here.

The ctest smoke run, `luma_pose_channel_bench_smoke` (2 s a scenario), checks the waits, the behind count and the fallback. It
does not check timing, for the same reason as `luma_rt_bench_smoke`.

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


## Real glasses: display vsync

`docs/rust-rebuild-plan.md`, D5. The Luma sends a 16-byte display event (`aa 8f 00 91`) on endpoint 0x83 at every panel
scan-out. The event carries the glasses' own time in 100 µs ticks, on the same clock as the IMU. With `--read-events` the driver
now uses those events to work out when the panel scans out. It publishes that time, on the host's clock and the IMU's timebase,
so the renderer can predict to the real scan-out instead of guessing.

### How it works

- **Parsing.** `parse_luma_display_event` (`spike/driver/src/protocol.cpp`) reads the event. The decoder widens its tick count
  with the IMU's, so a tick wrap or a reconnect treats both alike. Without `--read-events` the events are not read at all, and in a
  replay they count as `bad_packets`, as before.
- **The fit.** `VsyncClock` (`spike/driver/src/vsync.cpp`) fits a least-squares line of frame number against device time over the
  last 120 events. That line gives the period and the phase. It locks after 8 events with an rms under 300 µs and a period of
  4 to 50 ms. Missed events are bridged by rounding to the nearest frame. Four or more lost in a row, an event more than 15% of a
  period off the line, or a clock that goes back each start the fit again. That covers a 60 to 120 Hz mode change.
- **Onto the host clock.** Each IMU packet gives one sample of host time minus device time. The lowest of these over the newest
  one to two seconds is the link's fastest delivery. It follows a link that gets faster, and drift, within a second. The anchor
  is the fitted scan-out plus that floor, in `CLOCK_MONOTONIC` and in the feed's steady time.
- **Publishing.** A `pose_vsync` segment (64 bytes, seqlock, magic `LVS1`) next to the pose feed carries the anchor, the period,
  the anchor on the feed's IMU timebase, the event and reset counts, and the fit rms. It is only made with `--read-events`. A
  reconnect, a clock rebase or a lost device publishes "unlocked" at once, so a reader never uses an old anchor.
  `xr::PoseFeed::vsync()` reads it. It returns nothing when the segment is missing, unlocked, torn or nonsense.
- **Measuring.** `xr::VsyncMeter` (`spike/src/predict.cpp`) scores every presented frame against its `wp_presentation` time.
  The horizon error is how far the presentation lies from the nearest predicted scan-out. The meter splits it into:
  - a constant offset: the circular mean, which is the panel's and the link's fixed delay, reported as `offset_ms`;
  - the error about that offset, whose p95 is the gate.
  
  Sensor-to-vsync is the time from the drawn pose's IMU sample to the scan-out it went out on.

### Where the numbers show

- **Session status.** The renderer's status line carries `vsync_error_ms` (horizon error p95 about the offset) and
  `sensor_to_vsync_ms` (p50). Options → Session shows them as "Vsync error ms" and "Sensor to vsync ms". Both keys are only
  sent while the driver publishes `pose_vsync`, so the standard driver's sessions look as before.
- **Exit report.** The report has `latency.vsync`: `frames`, `skipped`, `period_ms`, `offset_ms`, `horizon_error` (`p50_ms`,
  `p95_ms`, `max_ms`) and `sensor_to_vsync` (`count`, `p50_ms`, `p95_ms`, `max_ms`).
- **Driver log.** The driver's periodic stats add
  `luma: vsync events=N locked=0|1 hz=H resets=N fit_us=R`.

### Offline proof

`luma_vsync_bench` (`spike/driver/tools/vsync_bench.cpp`) builds a recording whose true scan-out times are known. It plays the
recording through the real daemon and scores what the daemon publishes with `xr::VsyncMeter`, and also against the truth. The
recording has:

- a device clock 65 ppm fast;
- 1 kHz IMU;
- a 60 Hz panel, then a 1.52 s silent mode change to 120 Hz;
- the glasses dropping off the bus for 400 ms at half time, coming back with a new clock and phase;
- 0.5% of events lost;
- bus delay of 150 to 650 µs, plus a tail, plus a 2 to 5 ms stall one time in 200, held back in order.

The bench fakes the renderer: one present at each true scan-out, with 20 µs of jitter.

Results for 60 s, seeds 1 to 3:

| | Value |
|---|---|
| Locks | 3 (60 Hz, 120 Hz after the mode change, after the reconnect), 1 reset |
| Frames scored | about 6,046, 24 without a usable state |
| Horizon error about offset | p50 0.018 ms, **p95 0.049 to 0.050 ms**, max 0.103 ms or less |
| Offset | −0.108 ms |
| Against truth | p95 0.129 to 0.130 ms, max 0.146 ms or less |
| Final fit | 8.3333 ms period, 27 µs rms |

The tests, in `build/release`:

- `ctest -R luma_vsync` runs:
  - `luma_vsync`: the unit tests;
  - `luma_vsync_bench`: 60 s, seed 1;
  - `luma_vsync_bench_seed2`: 30 s;
  - `luma_vsync_replay`: writes the bench's recording and plays it through `xr-luma-driver --replay --read-events`, which must
    print `luma: vsync events=18xx locked=1 hz=120.00 resets=1` with no `bad_packets`.
- `luma_publish` checks the segment and the meter.
- `luma_daemon` checks the event clock through the decoder.

### What this does not prove

- **The constant offset.** The panel's own delay from event to light, and the link's fixed delay, cannot be seen offline. The
  meter reports them as `offset_ms` and leaves them out of the gate. Only real glasses give the number.
- **The real bus.** The bench's bus is a model. The real latency floor, and how often the Ultra drops events, come from the
  glasses (see USB latency above).
- **Nearest scan-out.** Sensor-to-vsync pairs each frame with the scan-out nearest its presentation. That is right while the
  offset is well under half a period.

### Running it on the glasses

1. Add `--read-events` to `~/.config/viture-workspace/luma-driver-flags` and keep the flags already there. For example:
   `--start prepared --read-events`.
2. Start a normal XR session with the Luma driver.
3. `tail -n 20 "$HOME/.local/state/viture-workspace/luma-driver.log"` must show `luma: vsync ... locked=1 hz=120.00` (or
   `60.00`) with `resets` steady while the session runs.
4. Options → Session must show "Vsync error ms" at 1 or less, and a "Sensor to vsync ms" value.
5. After Stop, the run's exit report (`journalctl --user -u 'viture-xr-*' | grep -o '"vsync":{"frames"[^}]*}[^}]*}}' | tail -n 1`) must have
   `horizon_error.p95_ms` at 1 or less and `frames` close to the session's presented frames. Record `offset_ms`: it should be
   the same across runs. Record `sensor_to_vsync.p50_ms` and `p95_ms` here.

| Date | Hz | Frames | Horizon error p95 | Offset | Sensor to vsync p50 | p95 |
|---|---|---|---|---|---|---|
| | | | | | | |
