//! Sensor packet to published pose, measured: a fake 1 kHz transport drives the real
//! `xr_luma_driver::rt::Pipeline` through a stand-in for fusion and xr-pose's seqlock writer. The port of
//! `spike/driver/bench/rt_bench.cpp`, with the same options, report and `json` line, so the two
//! can be run side by side; `docs/performance.md` explains what it does and does not prove.
//!   luma_rt_bench                       30 s, ring mode, real-time if permitted
//!   luma_rt_bench --check               the same, and exits 1 if a target is missed
//!                                       (p99 < 0.5 ms, < 1% of a core, nothing lost)
//!   luma_rt_bench --quick               1 s; asserts only that nothing is lost or allocated
//!   --mode ring|inline  --seconds N  --rate HZ  --rt auto|off  --no-slice  --lock
//!   --reader-cpu N  --fusion-cpu N  --sender-cpu N  --load N (N busy threads)
//! Not ported yet: `--publish mutex` and `--spin-reader`.

mod alloc_probe;
mod fake_transport;
mod stub_fusion;

use fake_transport::{FakeReader, FakeTransport, PACKET_BYTES, parse_fake};
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use stub_fusion::{ImuSample, StubFusion};
use xr_luma_driver::rt::{
    LatencyHistogram, Options, Pipeline, PipelineMode, Sink, Source, SourceStatus, lock_memory,
    monotonic_raw_ns, raw_to_monotonic_offset_ns,
};
use xr_pose::{POSE_CALIBRATED, PoseRecord, PoseWriter};

struct Args {
    seconds: f64,
    warmup_s: f64,
    rate: u32,
    reader_cpu: Option<usize>,
    fusion_cpu: Option<usize>,
    sender_cpu: Option<usize>,
    load: usize,
    inline_mode: bool,
    rt: bool,
    slice: bool,
    lock: bool,
    check: bool,
    quick: bool,
}

fn parse() -> Option<Args> {
    let mut a = Args {
        seconds: 30.0,
        warmup_s: 1.0,
        rate: 1000,
        reader_cpu: None,
        fusion_cpu: None,
        sender_cpu: None,
        load: 0,
        inline_mode: false,
        rt: true,
        slice: true,
        lock: false,
        check: false,
        quick: false,
    };
    let mut it = std::env::args().skip(1);
    while let Some(s) = it.next() {
        let mut value = || it.next().unwrap_or_else(|| "0".into());
        let cpu = |v: String| v.parse::<i64>().ok().and_then(|c| usize::try_from(c).ok());
        match s.as_str() {
            "--seconds" => a.seconds = value().parse().unwrap_or(0.0),
            "--rate" => a.rate = value().parse().unwrap_or(0),
            "--mode" => a.inline_mode = value() == "inline",
            "--rt" => a.rt = value() != "off",
            "--no-slice" => a.slice = false,
            "--load" => a.load = value().parse().unwrap_or(0),
            "--lock" => a.lock = true,
            "--reader-cpu" => a.reader_cpu = cpu(value()),
            "--fusion-cpu" => a.fusion_cpu = cpu(value()),
            "--sender-cpu" => a.sender_cpu = cpu(value()),
            "--check" => a.check = true,
            "--quick" => {
                a.quick = true;
                a.seconds = 1.0;
                a.warmup_s = 0.2;
            }
            "--publish" | "--spin-reader" => {
                eprintln!("{s} needs the shared-memory feed (xr-pose), not ported yet");
                return None;
            }
            _ => {
                eprintln!("unknown option {s}");
                return None;
            }
        }
    }
    (a.seconds > 0.0 && a.rate > 0).then_some(a)
}

#[derive(Debug, Clone, Copy, Default)]
struct Item {
    s: ImuSample,
    t_sent: u64,
    t_read: u64,
}

#[derive(Debug, Clone, Copy, Default)]
struct ThreadUse {
    cpu_ns: u64,
    faults: i64,
    forced: i64,
}

fn thread_use() -> ThreadUse {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: both out-parameters are valid and writable.
    let ru = unsafe {
        libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut ts);
        let mut ru: libc::rusage = std::mem::zeroed();
        libc::getrusage(libc::RUSAGE_THREAD, &mut ru);
        ru
    };
    ThreadUse {
        cpu_ns: ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64,
        faults: ru.ru_minflt + ru.ru_majflt,
        forced: ru.ru_nivcsw,
    }
}

impl std::ops::Sub for ThreadUse {
    type Output = ThreadUse;
    fn sub(self, b: ThreadUse) -> ThreadUse {
        ThreadUse {
            cpu_ns: self.cpu_ns - b.cpu_ns,
            faults: self.faults - b.faults,
            forced: self.forced - b.forced,
        }
    }
}

/// Unlinks the bench's own pose object on every way out of `main`.
struct ShmName(std::ffi::CString);

impl Drop for ShmName {
    fn drop(&mut self) {
        // SAFETY: NUL-terminated name.
        unsafe { libc::shm_unlink(self.0.as_ptr()) };
    }
}

/// Everything the sink learns, on the thread that publishes.
struct BenchSink {
    fusion: StubFusion,
    slot: PoseWriter,
    raw_to_mono: i64,
    first: u32,
    last: u32,
    period_ns: u64,
    total: LatencyHistogram,
    transport: LatencyHistogram,
    handoff: LatencyHistogram,
    work: LatencyHistogram,
    interval_error: LatencyHistogram,
    gaps: u64,
    previous_publish: u64,
    window_start: u64,
    window_end: u64,
    allocs_start: u64,
    allocs_end: u64,
    previous_seq: u32,
    use_start: ThreadUse,
    use_end: ThreadUse,
    finished: Arc<AtomicBool>,
}

impl Sink<Item> for Box<BenchSink> {
    fn publish(&mut self, i: &Item) {
        let seq = i.s.seq;
        if seq == self.first {
            self.use_start = thread_use();
            self.allocs_start = alloc_probe::alloc_count();
            self.window_start = monotonic_raw_ns();
        }
        let t_start = monotonic_raw_ns();
        let pose = self.fusion.update(&i.s);
        self.slot.publish(&PoseRecord {
            w: pose.q.w,
            x: pose.q.x,
            y: pose.q.y,
            z: pose.q.z,
            t_raw_ns: i.t_sent,
            raw_to_mono_ns: self.raw_to_mono,
            seq: pose.seq,
            flags: if pose.calibrated { POSE_CALIBRATED } else { 0 },
        });
        let t_done = monotonic_raw_ns();
        if seq != self.previous_seq.wrapping_add(1) {
            self.gaps += 1;
        }
        self.previous_seq = seq;
        if seq >= self.first && seq < self.last {
            self.total.record(t_done - i.t_sent);
            self.transport.record(i.t_read - i.t_sent);
            self.handoff.record(t_start - i.t_read);
            self.work.record(t_done - t_start);
            if seq > self.first {
                let gap = t_done - self.previous_publish;
                self.interval_error.record(gap.abs_diff(self.period_ns));
            }
        }
        self.previous_publish = t_done;
        if seq == self.last {
            self.allocs_end = alloc_probe::alloc_count();
            self.window_end = monotonic_raw_ns();
            self.use_end = thread_use();
            self.finished.store(true, Ordering::Release);
        }
    }
}

struct BenchSource {
    reader: FakeReader,
    first: u32,
    last: u32,
    bad: u64,
    use_start: ThreadUse,
    use_end: ThreadUse,
}

impl Source for BenchSource {
    type Item = Item;
    fn next(&mut self, item: &mut Item) -> SourceStatus {
        let mut buffer = [0u8; PACKET_BYTES];
        let n = self.reader.read(&mut buffer, 100);
        let t_read = monotonic_raw_ns();
        if n == 0 {
            return SourceStatus::Idle;
        }
        if n < 0 {
            return SourceStatus::Stop;
        }
        let Some(packet) = parse_fake(&buffer[..n as usize]) else {
            self.bad += 1;
            return SourceStatus::Idle;
        };
        if packet.seq == self.first {
            self.use_start = thread_use();
        }
        if packet.seq == self.last {
            self.use_end = thread_use();
        }
        item.t_sent = packet.t_sent_ns;
        item.t_read = t_read;
        item.s = ImuSample {
            t_ns: t_read,
            seq: packet.seq,
            gyro_rad_s: packet.gyro,
            accel_m_s2: packet.accel,
        };
        SourceStatus::Item
    }
}

fn sched_name(tid: libc::pid_t) -> &'static str {
    if tid == 0 {
        return "none";
    }
    // SAFETY: plain query; a thread that has exited gives -1.
    match unsafe { libc::sched_getscheduler(tid) } & !libc::SCHED_RESET_ON_FORK {
        libc::SCHED_FIFO => "fifo",
        libc::SCHED_RR => "rr",
        libc::SCHED_OTHER => "other",
        _ => "?",
    }
}

fn show(name: &str, h: &LatencyHistogram) {
    println!(
        "  {name:<22} p50 {:8.1}  p99 {:8.1}  p99.9 {:8.1}  max {:9.1}  stddev {:7.1} us",
        h.percentile(50.0) as f64 / 1e3,
        h.percentile(99.0) as f64 / 1e3,
        h.percentile(99.9) as f64 / 1e3,
        h.max() as f64 / 1e3,
        h.stddev() / 1e3
    );
}

fn main() -> ExitCode {
    let Some(a) = parse() else {
        return ExitCode::from(2);
    };
    if a.lock {
        let m = lock_memory();
        if !m.locked {
            println!("{}", m.detail);
        }
    }
    let first = (a.warmup_s * a.rate as f64) as u32;
    let last = first + (a.seconds * a.rate as f64) as u32;

    // The C++ bench's seqlock object, named for this process so a live driver's is never touched.
    let shm = format!("/luma-rt-bench-{}", std::process::id());
    let writer = match PoseWriter::create(&shm) {
        Ok(w) => w,
        Err(why) => {
            eprintln!("seqlock writer: {why}");
            return ExitCode::from(2);
        }
    };
    let _unlink = ShmName(std::ffi::CString::new(shm).expect("no NUL in the name"));

    let finished = Arc::new(AtomicBool::new(false));
    let sink = Box::new(BenchSink {
        fusion: StubFusion::default(),
        slot: writer,
        raw_to_mono: raw_to_monotonic_offset_ns(),
        first,
        last,
        period_ns: 1_000_000_000 / a.rate as u64,
        total: LatencyHistogram::new(),
        transport: LatencyHistogram::new(),
        handoff: LatencyHistogram::new(),
        work: LatencyHistogram::new(),
        interval_error: LatencyHistogram::new(),
        gaps: 0,
        previous_publish: 0,
        window_start: 0,
        window_end: 0,
        allocs_start: 0,
        allocs_end: 0,
        previous_seq: u32::MAX,
        use_start: ThreadUse::default(),
        use_end: ThreadUse::default(),
        finished: Arc::clone(&finished),
    });
    // Its histograms are about 75 KiB of memory: their first use must not be what the page-fault
    // count below reports.
    let at = &*sink as *const BenchSink as usize;
    // SAFETY: advice only, on whole pages around memory this process owns.
    unsafe {
        libc::madvise(
            (at & !4095) as *mut libc::c_void,
            std::mem::size_of::<BenchSink>() + 4096,
            libc::MADV_POPULATE_WRITE,
        )
    };

    let load_stop = Arc::new(AtomicBool::new(false));
    let load: Vec<_> = (0..a.load)
        .map(|_| {
            let stop = Arc::clone(&load_stop);
            std::thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    std::hint::spin_loop();
                }
            })
        })
        .collect();

    let mut transport = match FakeTransport::open(a.rate, last + 20, a.sender_cpu) {
        Ok(t) => t,
        Err(why) => {
            eprintln!("{why}");
            return ExitCode::from(2);
        }
    };
    let source = BenchSource {
        reader: transport.reader(),
        first,
        last,
        bad: 0,
        use_start: ThreadUse::default(),
        use_end: ThreadUse::default(),
    };
    let mut o = Options {
        mode: if a.inline_mode {
            PipelineMode::Inline
        } else {
            PipelineMode::Ring
        },
        ..Options::default()
    };
    o.reader.realtime = a.rt;
    o.fusion.realtime = a.rt;
    o.reader.short_slice = a.slice;
    o.fusion.short_slice = a.slice;
    o.reader.cpu = a.reader_cpu;
    o.fusion.cpu = a.fusion_cpu;
    let pipeline: Pipeline<_, _> = match Pipeline::start(source, sink, &o) {
        Ok(p) => p,
        Err(why) => {
            eprintln!("{why}");
            return ExitCode::from(2);
        }
    };
    let reader_sched = sched_name(pipeline.reader_tid());
    let fusion_sched = sched_name(pipeline.fusion_tid());
    let reader_policy = pipeline.reader_policy().clone();
    transport.start();

    let deadline = Instant::now() + Duration::from_secs_f64(a.seconds + a.warmup_s + 10.0);
    while !finished.load(Ordering::Acquire) && !transport.finished() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    transport.stop();
    load_stop.store(true, Ordering::SeqCst);
    for t in load {
        let _ = t.join();
    }
    if !finished.load(Ordering::SeqCst) {
        println!("the last sample never arrived: overruns or a stall");
    }
    std::thread::sleep(Duration::from_millis(150));
    let (source, sink, stats) = pipeline.stop();

    let wall_s = sink.window_end.saturating_sub(sink.window_start) as f64 / 1e9;
    let reader = source.use_end - source.use_start;
    let fusion = if a.inline_mode {
        ThreadUse::default()
    } else {
        sink.use_end - sink.use_start
    };
    let cpu_pct = if wall_s > 0.0 {
        (reader.cpu_ns + fusion.cpu_ns) as f64 / 1e9 / wall_s * 100.0
    } else {
        0.0
    };
    let allocs = sink.allocs_end - sink.allocs_start;
    let lost = transport.sent() - stats.items_published;
    let mode = if a.inline_mode { "inline" } else { "ring" };
    let samples = sink.total.count().max(1);

    println!(
        "stand-in fusion, {mode}, seqlock, {} Hz, {wall_s:.1} s, reader {reader_sched}{}, fusion thread {fusion_sched}, {} busy threads",
        a.rate,
        if reader_policy.short_slice {
            " short-slice"
        } else {
            ""
        },
        a.load
    );
    if !reader_policy.detail.is_empty() {
        println!("  reader: {}", reader_policy.detail);
    }
    if reader_policy.needs_setup && !a.quick {
        println!("  one-time setup for real-time: see docs/performance.md");
    }
    show("sensor -> published", &sink.total);
    show("  queue + wake reader", &sink.transport);
    show("  hand-over to fusion", &sink.handoff);
    show("  fusion + publish", &sink.work);
    show("interval error", &sink.interval_error);
    println!(
        "  cpu {cpu_pct:.3}% of a core (reader {} ns/sample, fusion {} ns/sample)",
        reader.cpu_ns / samples,
        fusion.cpu_ns / samples
    );
    println!(
        "  sent {}, published {}, kernel queue overflows {}, ring overruns {}, gaps {}, worst backlog {}",
        transport.sent(),
        stats.items_published,
        transport.queue_overflows(),
        stats.ring_overruns,
        sink.gaps,
        stats.max_backlog
    );
    println!(
        "  allocations {allocs}, page faults {}, forced context switches {}, late sends (rig) {}",
        reader.faults + fusion.faults,
        reader.forced + fusion.forced,
        transport.late_sends()
    );
    println!(
        "json {{\"mode\":\"{mode}\",\"publish\":\"seqlock\",\"reader_sched\":\"{reader_sched}\",\"short_slice\":{},\"load\":{},\"p50_us\":{:.1},\"p99_us\":{:.1},\"p999_us\":{:.1},\"max_us\":{:.1},\"cpu_pct\":{cpu_pct:.3},\"lost\":{lost},\"allocs\":{allocs}}}",
        reader_policy.short_slice,
        a.load,
        sink.total.percentile(50.0) as f64 / 1e3,
        sink.total.percentile(99.0) as f64 / 1e3,
        sink.total.percentile(99.9) as f64 / 1e3,
        sink.total.max() as f64 / 1e3,
    );

    let mut failed = 0;
    let mut require = |ok: bool, what: &str| {
        if !ok {
            println!("FAILED: {what}");
            failed += 1;
        }
    };
    require(
        finished.load(Ordering::SeqCst),
        "the run reached its last sample",
    );
    require(
        sink.total.count() == (last - first) as u64,
        "every sample in the window was measured",
    );
    require(source.bad == 0, "no packet failed its CRC");
    require(
        stats.items_published + stats.ring_overruns + transport.queue_overflows()
            == transport.sent(),
        "every packet is accounted for",
    );
    require(allocs == 0, "no allocation in the hot path");
    if a.check {
        require(lost == 0, "nothing lost");
        require(sink.total.percentile(99.0) < 500_000, "p99 under 0.5 ms");
        require(cpu_pct < 1.0, "under 1% of a core");
    }
    if failed > 0 {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}
