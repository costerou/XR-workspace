//! The daemon against a simulated device and clock: every failure mode, in moments, with no USB and no IPC. A port of
//! `spike/driver/tests/daemon_test.cpp`, scenario for scenario and check for check. The replay tests use the fake
//! decoder and estimator, as the real packet parser and fusion are not in this crate yet; the last test hands the log
//! lines to the Python readers that depend on them (tracking.py's readiness rule, driverlog.classify).

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::{Cell, RefCell, RefMut};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, UNIX_EPOCH};
use xr_luma_driver::capture::{CaptureKind, CaptureRecord, ReplayLink, load_capture, save_capture};
use xr_luma_driver::daemon::{
    Daemon, DaemonConfig, DaemonEnd, DaemonParts, Decoder, Estimator, Link, ReadResult, Sink, Stats,
};
use xr_luma_driver::instance::{stamped, take_instance_lock};
use xr_luma_driver::options::{LIVE_IPC_PREFIX, Options, Start, parse_options};
use xr_luma_driver::types::{ImuSample, PoseOut, Quatf};

// ---- counting allocations, per thread: the tests run side by side ------------------------------------------------
struct Counting;
thread_local! { static ALLOCATED: Cell<u64> = const { Cell::new(0) }; }
// SAFETY: every call goes straight to the system allocator; the count is a thread-local with no destructor.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let _ = ALLOCATED.try_with(|n| n.set(n.get() + 1));
        // SAFETY: the caller's contract is System's.
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: as above.
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        let _ = ALLOCATED.try_with(|n| n.set(n.get() + 1));
        // SAFETY: as above.
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}
#[global_allocator]
static COUNTING: Counting = Counting;
fn allocations() -> u64 {
    ALLOCATED.with(Cell::get)
}

const MS: u64 = 1_000_000;
fn ms(n: u64) -> Duration {
    Duration::from_millis(n)
}

#[derive(Clone, Copy)]
struct Span(u64, u64);
fn within(spans: &[Span], t_ms: u64) -> bool {
    spans.iter().any(|s| t_ms >= s.0 && t_ms < s.1)
}

/// The glasses at 1 kHz. The script says when they are off the bus, up but silent, sending junk, or refusing commands.
struct Device {
    /// Clock, ns; simulated time starts at t=1 s.
    now: u64,
    base: u64,
    /// ms since start of the test
    absent: Vec<Span>,
    silent: Vec<Span>,
    junk: Vec<Span>,
    refuse: Vec<Span>,
    restart_clock_on_return: bool,
    /// Needs a start; a drop ends it, closing the link does not.
    streaming: bool,
    /// The command that starts it.
    stream_byte: u8,
    clock_zero: u64,
    /// Raw device stamps for the first samples, then the clock.
    stamp_override: Vec<u64>,
    run_ms: u64,
    why_absent: String,
    stop: Rc<AtomicBool>,
}

impl Device {
    fn new() -> Self {
        Device {
            now: 1000 * MS,
            base: 1000 * MS,
            absent: vec![],
            silent: vec![],
            junk: vec![],
            refuse: vec![],
            restart_clock_on_return: false,
            streaming: false,
            stream_byte: 0x01,
            clock_zero: 0,
            stamp_override: vec![],
            run_ms: 0,
            why_absent: "absent".into(),
            stop: Rc::new(AtomicBool::new(false)),
        }
    }
    fn ms(&self) -> u64 {
        (self.now - self.base) / MS
    }
    fn tick(&self) {
        if self.ms() >= self.run_ms {
            self.stop.store(true, Ordering::Relaxed);
        }
    }
    fn step(&mut self) {
        self.now += MS;
        self.tick();
    }
}

const PACKET: usize = 12;

struct FakeLink {
    d: Rc<RefCell<Device>>,
    sent: Vec<Vec<u8>>,
    sent_ms: Vec<u64>,
    opens: u32,
    closes: u32,
    open_now: bool,
    hotplug: bool,
    allocations_in_window: u64,
    at_window_start: u64,
    reopened_ms: u64,
    seq: u32,
    stamp_next: usize,
    was_gone: bool,
}

impl Link for FakeLink {
    fn open(&mut self) -> Result<(), String> {
        assert!(!self.open_now);
        self.opens += 1;
        let mut d = self.d.borrow_mut();
        d.tick();
        if within(&d.absent, d.ms()) {
            d.streaming = false;
            self.was_gone = true;
            return Err(d.why_absent.clone());
        }
        if self.was_gone {
            self.was_gone = false;
            if d.restart_clock_on_return {
                d.clock_zero = d.now;
            }
        }
        self.open_now = true;
        self.reopened_ms = d.ms();
        Ok(())
    }

    fn read(&mut self, into: &mut [u8], timeout: Duration) -> ReadResult {
        assert!(self.open_now);
        let mut d = self.d.borrow_mut();
        let next = d.ms() + 1;
        if within(&d.absent, next) {
            d.step();
            self.was_gone = true;
            d.streaming = false;
            return ReadResult::GONE;
        }
        if within(&d.silent, next) || !d.streaming {
            for _ in 0..timeout.as_millis() {
                // a wait ends the moment the device goes
                if within(&d.absent, d.ms() + 1) {
                    break;
                }
                d.step();
            }
            return ReadResult::TIMEOUT;
        }
        d.step();
        if d.ms() == 2000 {
            self.at_window_start = allocations();
        }
        if d.ms() == 3000 {
            self.allocations_in_window = allocations() - self.at_window_start;
        }
        if within(&d.junk, d.ms()) {
            into[0] = 0xff;
            return ReadResult::data(1, 0);
        }
        let mut t = d.now - d.clock_zero;
        if self.stamp_next < d.stamp_override.len() {
            t = d.stamp_override[self.stamp_next];
            self.stamp_next += 1;
        }
        into[..8].copy_from_slice(&t.to_le_bytes());
        into[8..12].copy_from_slice(&self.seq.to_le_bytes());
        self.seq += 1;
        ReadResult::data(PACKET, 0)
    }

    fn write(&mut self, bytes: &[u8]) -> bool {
        assert!(self.open_now);
        let mut d = self.d.borrow_mut();
        self.sent.push(bytes.to_vec());
        self.sent_ms.push(d.ms());
        if within(&d.refuse, d.ms()) {
            return false;
        }
        if bytes == [d.stream_byte] {
            d.streaming = true;
        }
        true
    }

    fn close(&mut self) {
        assert!(self.open_now);
        self.open_now = false;
        self.closes += 1;
    }

    fn wait_for_device(&mut self, timeout: Duration) -> bool {
        // A real hotplug event would end this wait at the moment the device arrives.
        let mut d = self.d.borrow_mut();
        for _ in 0..timeout.as_millis() {
            d.step();
            if self.hotplug && !within(&d.absent, d.ms()) {
                return true;
            }
        }
        false
    }
}

/// Little-endian u64 stamp, u32 seq.
#[derive(Default)]
struct FakeDecoder {
    connections: u32,
}
impl Decoder for FakeDecoder {
    fn decode(&mut self, packet: &[u8]) -> Option<ImuSample> {
        if packet.len() != PACKET {
            return None;
        }
        Some(ImuSample {
            t_ns: u64::from_le_bytes(packet[..8].try_into().unwrap()),
            seq: u32::from_le_bytes(packet[8..12].try_into().unwrap()),
            ..ImuSample::default()
        })
    }
    fn new_connection(&mut self) {
        self.connections += 1;
    }
    fn lost_samples(&self) -> u64 {
        7
    }
}

#[derive(Default)]
struct FusionLog {
    made: usize,
    steps_ns: Vec<u64>,
    last_t: u64,
}

struct FakeEstimator {
    log: Rc<RefCell<FusionLog>>,
    settle: i32,
    nan_at: i32,
    lose_at: i32,
    count: i32,
    q: Quatf,
}
impl Estimator for FakeEstimator {
    fn update(&mut self, s: &ImuSample) -> PoseOut {
        let mut log = self.log.borrow_mut();
        if log.last_t != 0 {
            let step = s.t_ns.wrapping_sub(log.last_t);
            log.steps_ns.push(step);
        }
        log.last_t = s.t_ns;
        self.count += 1;
        let mut out = PoseOut {
            q: self.q,
            t_ns: s.t_ns,
            seq: s.seq,
            calibrated: self.count >= self.settle && self.count != self.lose_at,
        };
        if self.count == self.nan_at {
            out.q.w = f32::NAN;
        }
        out
    }
}

#[derive(Default)]
struct FakeSink {
    published: u64,
    last_t: u64,
    last_q: Quatf,
    backwards: bool,
    stale: bool,
    published_while_stale: bool,
    changes: Vec<bool>,
}
impl Sink for FakeSink {
    fn publish(&mut self, p: &PoseOut) {
        assert!(p.calibrated);
        if p.t_ns <= self.last_t {
            self.backwards = true;
        }
        if self.stale {
            self.published_while_stale = true;
        }
        self.last_t = p.t_ns;
        self.last_q = p.q;
        self.published += 1;
    }
    fn set_stale(&mut self, s: bool) {
        self.stale = s;
        self.changes.push(s);
    }
}

struct Rig {
    device: Rc<RefCell<Device>>,
    link: FakeLink,
    decoder: FakeDecoder,
    /// What each new estimator reports, in order.
    poses: Vec<Quatf>,
    sink: FakeSink,
    fusion: Rc<RefCell<FusionLog>>,
    lines: Vec<String>,
    settle: i32,
    nan_at: i32,
    lose_at: i32,
    stop_command: Vec<u8>,
    start_command: Vec<u8>,
    before_start: Vec<Vec<u8>>,
    config: DaemonConfig,
    stats: Stats,
    end: Option<DaemonEnd>,
}

impl Rig {
    fn new() -> Self {
        let device = Rc::new(RefCell::new(Device::new()));
        Rig {
            link: FakeLink {
                d: Rc::clone(&device),
                sent: vec![],
                sent_ms: vec![],
                opens: 0,
                closes: 0,
                open_now: false,
                hotplug: false,
                allocations_in_window: 0,
                at_window_start: 0,
                reopened_ms: 0,
                seq: 0,
                stamp_next: 0,
                was_gone: false,
            },
            device,
            decoder: FakeDecoder::default(),
            poses: vec![],
            sink: FakeSink::default(),
            fusion: Rc::default(),
            lines: vec![],
            settle: 100,
            nan_at: -1,
            lose_at: -1,
            stop_command: vec![0x00],
            start_command: vec![0x01],
            before_start: vec![],
            config: DaemonConfig::default(),
            stats: Stats::default(),
            end: None,
        }
    }

    fn dev(&self) -> RefMut<'_, Device> {
        self.device.borrow_mut()
    }

    fn run(&mut self, run_ms: u64) {
        self.dev().run_ms = run_ms;
        let stop = Rc::clone(&self.device.borrow().stop);
        let device = Rc::clone(&self.device);
        let fusion = Rc::clone(&self.fusion);
        let poses = self.poses.clone();
        let (settle, nan_at, lose_at) = (self.settle, self.nan_at, self.lose_at);
        let lines = &mut self.lines;
        let parts = DaemonParts {
            link: &mut self.link,
            decoder: &mut self.decoder,
            make_estimator: Box::new(move || {
                let made = fusion.borrow().made;
                let q = poses.get(made).copied().unwrap_or_default();
                fusion.borrow_mut().made += 1;
                Box::new(FakeEstimator {
                    log: Rc::clone(&fusion),
                    settle,
                    nan_at,
                    lose_at,
                    count: 0,
                    q,
                })
            }),
            sink: &mut self.sink,
            now_ns: Box::new(move || device.borrow().now),
            log: Box::new(|l| lines.push(l.to_owned())),
            start_command: self.start_command.clone(),
            stop_command: self.stop_command.clone(),
            before_start: self.before_start.clone(),
            on_link: None,
            on_tick: None,
        };
        let mut daemon = Daemon::new(parts, self.config.clone());
        self.end = Some(daemon.run(&stop));
        self.stats = daemon.stats().clone();
    }

    fn count(&self, prefix: &str) -> usize {
        count(&self.lines, prefix)
    }
    fn index(&self, prefix: &str) -> Option<usize> {
        self.index_from(prefix, 0)
    }
    fn index_from(&self, prefix: &str, from: usize) -> Option<usize> {
        (from..self.lines.len()).find(|&i| self.lines[i].starts_with(prefix))
    }
    fn line(&self, prefix: &str) -> &str {
        &self.lines[self
            .index(prefix)
            .unwrap_or_else(|| panic!("no {prefix} in {:#?}", self.lines))]
    }
    fn starts(&self) -> usize {
        self.link
            .sent
            .iter()
            .filter(|c| c.as_slice() == [0x01])
            .count()
    }
    /// What tracking.py does: ready when the last `calibrated` comes after the last `disconnected` and `calibrating`.
    fn ready(&self) -> bool {
        ready(&self.lines)
    }
}

fn count(lines: &[String], prefix: &str) -> usize {
    lines.iter().filter(|l| l.starts_with(prefix)).count()
}
fn ready(lines: &[String]) -> bool {
    let mut ready = None;
    let mut lost = None;
    for (i, l) in lines.iter().enumerate() {
        if l.contains("luma: calibrated") {
            ready = Some(i);
        }
        if l.contains("luma: disconnected") || l.contains("luma: calibrating") {
            lost = Some(i);
        }
    }
    ready > lost
}

fn spans(list: &[(u64, u64)]) -> Vec<Span> {
    list.iter().map(|&(a, b)| Span(a, b)).collect()
}

#[test]
fn clean_run() {
    let mut r = Rig::new();
    r.run(3000);
    assert_eq!(r.count("luma: connected"), 1);
    assert_eq!(r.count("luma: calibrating"), 1);
    assert_eq!(r.count("luma: calibrated restored=0"), 1);
    assert!(r.index("luma: connected") < r.index("luma: calibrating"));
    assert!(r.index("luma: calibrating") < r.index("luma: calibrated"));
    assert_eq!(r.starts(), 1); // once, in three thousand samples
    assert_eq!(r.link.sent.len(), 2); // and the stop, and nothing else
    assert_eq!(r.link.sent.last().unwrap(), &vec![0x00]);
    assert!(r.link.closes == 1 && !r.link.open_now);
    assert_eq!(r.fusion.borrow().made, 1);
    assert!(r.ready());
    assert!(r.stats.commands == 2 && r.stats.disconnects == 0);
    assert!(r.sink.published > 2800 && !r.sink.backwards);
    assert_eq!(r.end, Some(DaemonEnd::StopRequested));
    assert_eq!(r.count("luma: stopped reason=signal"), 1);
    assert!(r.sink.changes[0] && *r.sink.changes.last().unwrap()); // stale before any pose and after the last
    assert!(!r.sink.published_while_stale);
}

#[test]
fn nothing_published_before_calibration() {
    let mut r = Rig::new();
    r.settle = 1000;
    r.run(500);
    assert_eq!(r.sink.published, 0);
    assert_eq!(r.count("luma: calibrated"), 0);
    assert_eq!(r.sink.changes, vec![true]);
    assert!(!r.ready());
}

#[test]
fn short_drop_keeps_calibration() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(3000, 5000)]);
    r.run(7500);
    assert_eq!(r.fusion.borrow().made, 1); // the same estimator: calibration and orientation survive
    assert_eq!(r.count("luma: disconnected reason=gone"), 1);
    assert_eq!(r.count("luma: reconnected"), 1);
    assert!(r.index("luma: reconnected gap_ms=2").is_some());
    assert_eq!(
        r.index("luma: reconnected gap_ms=2"),
        r.index("luma: reconnected")
    );
    assert!(r.line("luma: reconnected").contains("calibration=kept"));
    assert_eq!(r.count("luma: calibrating"), 1); // no second calibration
    assert_eq!(r.count("luma: calibrated restored=1"), 1);
    assert!(r.index("luma: disconnected") < r.index("luma: reconnected"));
    assert!(r.index("luma: reconnected") < r.index("luma: calibrated restored=1"));
    assert!(r.ready());
    assert_eq!(r.starts(), 2); // one per connection
    assert!(
        (5000..=5100).contains(&r.link.reopened_ms),
        "{}",
        r.link.reopened_ms
    ); // found within one rescan
    assert!(
        (5200..=5320).contains(&r.link.sent_ms[1]),
        "{:?}",
        r.link.sent_ms
    ); // and left alone to settle first
    assert_eq!(r.decoder.connections, 2);
    assert!(!r.sink.backwards);
}

#[test]
fn ready_goes_false_during_a_drop() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(2000, 100_000)]);
    r.run(3000);
    assert!(!r.ready());
    assert!(r.sink.stale);
}

#[test]
fn hotplug_event_ends_the_wait() {
    let mut r = Rig::new();
    r.link.hotplug = true;
    r.dev().absent = spans(&[(1000, 8000)]);
    r.run(9000);
    assert!(
        (8000..=8002).contains(&r.link.reopened_ms),
        "{}",
        r.link.reopened_ms
    );
}

#[test]
fn long_drop_recalibrates() {
    let mut r = Rig::new();
    r.config.calibration_keep = ms(5000);
    r.dev().absent = spans(&[(2000, 9000)]);
    r.run(12000);
    assert_eq!(r.fusion.borrow().made, 2);
    assert!(r.line("luma: reconnected").contains("calibration=lost"));
    assert_eq!(r.count("luma: calibrating"), 2);
    assert_eq!(r.count("luma: calibrated restored=0"), 2);
    assert_eq!(r.count("luma: calibrated restored=1"), 0);
    assert!(r.ready());
    assert!(!r.sink.backwards);
}

#[test]
fn keep_window_boundary() {
    let mut kept = Rig::new();
    kept.config.calibration_keep = ms(5000);
    kept.dev().absent = spans(&[(2000, 6500)]);
    kept.run(8000);
    assert_eq!(kept.fusion.borrow().made, 1);
    let mut lost = Rig::new();
    lost.config.calibration_keep = ms(5000);
    lost.dev().absent = spans(&[(2000, 7500)]);
    lost.run(9000);
    assert_eq!(lost.fusion.borrow().made, 2);
    let mut standard = Rig::new();
    standard.dev().absent = spans(&[(2000, 200_000)]); // over three minutes away: still kept
    standard.run(202_000);
    assert!(standard.fusion.borrow().made == 1 && standard.ready());
    let mut too_long = Rig::new();
    too_long.dev().absent = spans(&[(2000, 400_000)]);
    too_long.run(403_000);
    assert_eq!(too_long.fusion.borrow().made, 2);
}

#[test]
fn drop_during_calibration_starts_it_over() {
    let mut r = Rig::new();
    r.settle = 1000;
    r.dev().absent = spans(&[(500, 800)]);
    r.run(3000);
    assert_eq!(r.fusion.borrow().made, 2);
    assert!(r.line("luma: reconnected").contains("calibration=none"));
    assert_eq!(r.count("luma: calibrated restored=0"), 1);
    assert!(r.ready());
}

#[test]
fn glasses_absent_at_start() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(0, 3000)]);
    r.run(5000);
    assert_eq!(r.count("luma: connected"), 1);
    assert!(r.count("luma: disconnected") == 0 && r.count("luma: reconnected") == 0);
    assert_eq!(r.starts(), 1); // opening costs nothing on the wire; only the one that worked sent a command
    assert!(r.link.opens > 20 && r.link.opens < 50, "{}", r.link.opens); // looking, not talking
    assert_eq!(r.count("luma: waiting reason=absent"), 1); // said once, not thirty times
    assert!(r.link.sent_ms[0] >= 3200); // it only just arrived: it settles too
    assert!(r.ready());
}

#[test]
fn stale_then_fresh_without_reopening() {
    let mut r = Rig::new();
    r.dev().silent = spans(&[(2000, 2300)]);
    r.run(4000);
    assert_eq!(r.count("luma: stale"), 1);
    assert_eq!(r.count("luma: fresh"), 1);
    assert!(r.index("luma: stale") < r.index("luma: fresh"));
    assert_eq!(r.count("luma: disconnected"), 0);
    assert!(r.starts() == 1 && r.link.opens == 1);
    assert!(!r.sink.published_while_stale);
    assert_eq!(r.sink.changes.iter().filter(|&&c| c).count(), 3); // start, the silence, exit
}

#[test]
fn stale_threshold_is_the_flag() {
    let mut quick = Rig::new();
    quick.config.stale_after = ms(40);
    quick.dev().silent = spans(&[(2000, 2060)]);
    quick.run(3000);
    assert_eq!(quick.count("luma: stale"), 1);
    let mut slow = Rig::new();
    slow.dev().silent = spans(&[(2000, 2060)]);
    slow.run(3000);
    assert_eq!(slow.count("luma: stale"), 0);
}

#[test]
fn silent_device_is_reopened_and_recovers() {
    let mut r = Rig::new();
    r.dev().silent = spans(&[(2000, 5000)]);
    r.run(8000);
    assert_eq!(r.count("luma: stale"), 1);
    assert_eq!(r.count("luma: disconnected reason=silent"), 1);
    assert_eq!(r.count("luma: reconnected"), 1);
    assert_eq!(r.fusion.borrow().made, 1);
    assert!(r.ready());
    assert!((1..=3).contains(&r.starts()));
    assert!(r.stats.starts_skipped >= 1); // it came back by itself: the last open needed no command
    assert_eq!(r.link.closes, r.link.opens);
}

#[test]
fn silent_device_gets_almost_no_commands() {
    let mut r = Rig::new();
    r.dev().silent = spans(&[(2000, 1_000_000)]);
    r.run(302_000);
    // 10 in the first minute while it backs off, then one every 9 s: never polling a device that may be in trouble.
    assert!((30..=45).contains(&r.starts()), "{}", r.starts());
    assert_eq!(r.count("luma: disconnected"), 1);
    assert_eq!(r.count("luma: retry attempt="), r.starts() - 1); // each start after the first, and the silence that ended it
    let at = &r.link.sent_ms;
    assert!(at[at.len() - 2] - at[at.len() - 3] >= 8900); // the last two starts (the very last command is the stop)
}

#[test]
fn device_that_opens_but_never_streams() {
    let mut r = Rig::new();
    r.dev().silent = spans(&[(0, 1_000_000)]);
    r.run(20000);
    assert!(r.link.sent_ms[0] <= 40); // nothing to wait for on a device that was already there
    assert_eq!(r.count("luma: connected"), 1);
    assert!(r.count("luma: calibrating") == 0 && r.count("luma: calibrated") == 0);
    assert_eq!(r.count("luma: reconnected"), 0);
    assert!(r.sink.published == 0 && r.sink.stale);
    assert!(r.starts() <= 5);
    assert!(!r.ready());
}

#[test]
fn junk_counts_as_silence() {
    let mut r = Rig::new();
    r.dev().junk = spans(&[(2000, 3500)]);
    r.run(6000);
    assert_eq!(r.count("luma: stale"), 1);
    assert_eq!(r.count("luma: disconnected reason=silent"), 1);
    assert!(r.stats.bad_packets > 500);
    assert!(r.ready());
}

#[test]
fn one_bad_packet_is_nothing() {
    let mut r = Rig::new();
    r.dev().junk = spans(&[(2000, 2003)]);
    r.run(3000);
    assert_eq!(r.stats.bad_packets, 3);
    assert!(r.count("luma: stale") == 0 && r.count("luma: disconnected") == 0);
}

#[test]
fn refused_command_backs_off() {
    let mut r = Rig::new();
    r.dev().refuse = spans(&[(0, 20000)]);
    r.run(25000);
    assert_eq!(r.count("luma: disconnected reason=write-failed"), 1);
    assert!(r.stats.commands < 12, "{}", r.stats.commands); // a device refusing commands is not asked again and again
    assert!(r.ready()); // and it recovers when it takes one
    assert_eq!(r.link.closes, r.link.opens);
}

#[test]
fn flapping_device_is_rate_limited() {
    let mut r = Rig::new();
    for t in (1000..61000).step_by(700) {
        r.dev().absent.push(Span(t + 400, t + 700)); // up 400 ms, gone 300 ms, for a minute
    }
    r.run(65000);
    assert!(r.starts() <= 4, "{}", r.starts()); // the wait after a return outgrows its time up
    assert!(!r.sink.backwards);
    assert!(r.ready()); // and once it holds, it comes back
}

#[test]
fn flicker_too_short_to_settle_gets_no_command() {
    let mut r = Rig::new();
    for t in (3000..8000).step_by(300) {
        r.dev().absent.push(Span(t + 100, t + 300));
    }
    r.run(8050);
    assert_eq!(r.starts(), 1); // only the first, before the flicker began
    assert_eq!(r.link.sent.len(), 1); // and no stop into a link that is still settling
}

#[test]
fn settle_grows_after_a_quick_death_and_resets_when_healthy() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(1000, 1100), (1400, 1500), (1900, 2000), (9000, 9100)]);
    r.run(12000);
    // The first drop comes 1 s in, unhealthy, so the wait grows from 200 ms to 400; gone again at 1400 it never reached a
    // command, and the same again at 1900. Once it holds, the start goes out 400 ms after the last return.
    let at = &r.link.sent_ms;
    assert_eq!(at.len(), 4, "{at:?}"); // first start, the one after the flicker, the one after 9100, the stop
    assert!((2400..=2520).contains(&at[1]), "{at:?}");
    assert!((9300..=9420).contains(&at[2]), "{at:?}"); // healthy for 6 s: back to 200 ms
}

#[test]
fn settling_is_not_silence() {
    let mut r = Rig::new();
    r.config.settle_first = ms(1500);
    r.dev().absent = spans(&[(3000, 4000)]);
    r.run(7000);
    assert_eq!(r.count("luma: retry"), 0); // longer than the watchdog's second, and nothing was asked of the device yet
    assert_eq!(r.link.sent_ms.len(), 3);
    assert!(
        (5500..=5620).contains(&r.link.sent_ms[1]),
        "{:?}",
        r.link.sent_ms
    );
    assert!(r.ready());
}

#[test]
fn stream_already_on_needs_no_command() {
    let mut r = Rig::new();
    r.dev().streaming = true;
    r.run(2000);
    assert_eq!(r.starts(), 0);
    assert_eq!(r.stats.starts_skipped, 1);
    assert_eq!(r.stats.commands, 1); // the stop
    assert!(r.ready());
}

#[test]
fn no_stop_after_listening_only_when_settling() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(1000, 1100)]);
    r.run(1200); // the stop request lands inside the settling wait
    assert_eq!(r.link.sent.len(), 1);
    assert!(!r.link.open_now);
}

#[test]
fn says_why_it_waits() {
    let mut r = Rig::new();
    r.dev().why_absent = "busy held by another driver".into();
    r.dev().absent = spans(&[(0, 2000)]);
    r.run(3000);
    assert_eq!(
        r.count("luma: waiting reason=busy-held-by-another-driver"),
        1
    );
    assert_eq!(r.count("luma: connected"), 1);
}

fn yawed(rad: f32) -> Quatf {
    Quatf {
        w: (rad / 2.0).cos(),
        x: 0.0,
        y: 0.0,
        z: (rad / 2.0).sin(),
    }
}
fn yaw_of(q: &Quatf) -> f32 {
    2.0 * q.z.atan2(q.w)
}

#[test]
fn new_calibration_keeps_the_heading() {
    let mut r = Rig::new();
    r.config.calibration_keep = ms(500);
    r.poses = vec![yawed(0.7), yawed(-1.1)];
    r.dev().absent = spans(&[(2000, 3000)]);
    r.run(5000);
    assert_eq!(r.fusion.borrow().made, 2);
    assert!((yaw_of(&r.sink.last_q) - 0.7).abs() < 1e-3); // not -1.1: the desktops stay where they were
    assert_eq!(r.count("luma: calibrated restored=0 heading=kept"), 1);
    let mut off = Rig::new();
    off.config.calibration_keep = ms(500);
    off.config.keep_heading = false;
    off.poses = vec![yawed(0.7), yawed(-1.1)];
    off.dev().absent = spans(&[(2000, 3000)]);
    off.run(5000);
    assert!((yaw_of(&off.sink.last_q) + 1.1).abs() < 1e-3);
    assert!(
        off.count("luma: calibrated restored=0") == 2
            && off.count("luma: calibrated restored=0 heading=kept") == 0
    );
}

#[test]
fn heading_carries_through_two_recalibrations() {
    let mut r = Rig::new();
    r.config.calibration_keep = ms(300);
    r.poses = vec![yawed(0.5), yawed(2.0), yawed(-2.0)];
    r.dev().absent = spans(&[(1000, 2000), (4000, 5000)]);
    r.run(7000);
    assert_eq!(r.fusion.borrow().made, 3);
    assert!((yaw_of(&r.sink.last_q) - 0.5).abs() < 1e-3);
}

#[test]
fn heading_leaves_tilt_alone() {
    // Pitched 30 degrees about y, heading 0 then 1 rad: the second estimator's tilt is true gravity and must survive.
    let pitched = Quatf {
        w: 0.2618_f32.cos(),
        x: 0.0,
        y: 0.2618_f32.sin(),
        z: 0.0,
    };
    let y = yawed(1.0);
    let turned = Quatf {
        w: y.w * pitched.w,
        x: -y.z * pitched.y,
        y: y.w * pitched.y,
        z: y.z * pitched.w,
    };
    let mut r = Rig::new();
    r.config.calibration_keep = ms(300);
    r.poses = vec![pitched, turned];
    r.dev().absent = spans(&[(1000, 2000)]);
    r.run(3000);
    let q = r.sink.last_q;
    assert!(
        (q.w - pitched.w).abs() < 1e-3 && (q.y - pitched.y).abs() < 1e-3 && q.z.abs() < 1e-3,
        "{q:?}"
    );
}

#[test]
fn no_heading_looking_straight_up() {
    let up = Quatf {
        w: std::f32::consts::FRAC_PI_4.cos(),
        x: 0.0,
        y: -std::f32::consts::FRAC_PI_4.sin(),
        z: 0.0,
    }; // forward is world up
    let mut r = Rig::new();
    r.config.calibration_keep = ms(300);
    r.poses = vec![up, yawed(1.0)];
    r.dev().absent = spans(&[(1000, 2000)]);
    r.run(3000);
    assert!((yaw_of(&r.sink.last_q) - 1.0).abs() < 1e-3);
    assert_eq!(r.count("luma: calibrated restored=0 heading=kept"), 0);
}

/// The steady per-sample path allocates nothing: 1 kHz with no room for surprises.
#[test]
fn hot_path_allocates_nothing() {
    let mut r = Rig::new();
    r.fusion.borrow_mut().steps_ns.reserve(8000); // the fake's own log must not be what is counted
    r.run(4000);
    assert_eq!(r.link.allocations_in_window, 0);
    assert!(r.stats.samples > 3900);
}

#[test]
fn healthy_connection_resets_the_back_off() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(1000, 3000), (8000, 8010)]); // the long outage caps the back-off; 5 s of samples clears it
    r.run(9000);
    assert!(
        (8010..=8100).contains(&r.link.reopened_ms),
        "{}",
        r.link.reopened_ms
    );
}

#[test]
fn stop_while_disconnected_sends_nothing() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(1000, 100_000)]);
    r.run(2000);
    assert_eq!(r.link.sent.len(), 1); // only the start: there is no device to tell
    assert!(!r.link.open_now);
    assert_eq!(r.count("luma: stopped"), 1);
    assert!(r.sink.stale);
}

#[test]
fn no_stop_command_configured() {
    let mut r = Rig::new();
    r.stop_command.clear();
    r.run(500);
    assert_eq!(r.link.sent.len(), 1);
}

#[test]
fn device_clock_restarts_after_a_drop() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(3000, 4000)]);
    r.dev().restart_clock_on_return = true;
    r.run(6000);
    assert_eq!(r.stats.time_jumps, 0); // a reconnect's rebase is expected, not an anomaly
    assert!(!r.sink.backwards);
    assert_eq!(r.stats.bad_poses, 0);
    // Time keeps running for the estimator and the feed alike: a gap is a gap.
    let largest = r.fusion.borrow().steps_ns.iter().copied().max().unwrap();
    assert!((900 * MS..=1400 * MS).contains(&largest), "{largest}");
    assert!(r.sink.last_t > 5900 * MS + 1000 * MS); // 6 s of real time on the published clock, not 5
}

#[test]
fn timestamp_glitches_mid_connection() {
    let mut r = Rig::new();
    {
        let mut d = r.dev();
        d.stamp_override = (0..300).map(|i| 1000 * MS + i * MS).collect();
        d.stamp_override.push(500 * MS); // backwards
        d.stamp_override.push(500 * MS); // repeated
        d.stamp_override.push(90_000_000 * MS); // far ahead
    }
    r.run(1000);
    assert_eq!(r.stats.time_jumps, 4); // the three, and the step back down to normal stamps after the last
    assert!(!r.sink.backwards && r.stats.bad_poses == 0);
}

#[test]
fn bad_pose_never_reaches_the_feed() {
    let mut r = Rig::new();
    r.nan_at = 500;
    r.run(1000);
    assert_eq!(r.stats.bad_poses, 1);
    assert!(r.sink.published > 800);
}

#[test]
fn estimator_gives_up_calibration() {
    let mut r = Rig::new();
    r.lose_at = 500;
    r.run(1000);
    assert_eq!(r.count("luma: calibrating"), 2);
    assert_eq!(r.count("luma: calibrated"), 2);
    assert_eq!(r.fusion.borrow().made, 1);
    assert!(r.ready());
    assert!(!r.sink.published_while_stale);
}

#[test]
fn stats_line() {
    let mut r = Rig::new();
    r.config.stats_every = ms(1000);
    r.run(3500);
    assert!(r.count("luma: stats") >= 4);
    let second = r
        .index_from("luma: stats", r.index("luma: stats").unwrap() + 1)
        .unwrap(); // the first holds the start-up
    let line = &r.lines[second];
    assert!(line.contains("commands=1 "), "{line}");
    assert!(
        line.contains("rate_hz=1000 ") || line.contains("rate_hz=999 "),
        "{line}"
    );
    assert!(line.contains("lost=7 "), "{line}");
    assert!(line.contains("max_process_us="), "{line}");
    // The C++ order, field for field: tools read these by name, and people read them in order.
    let keys: Vec<&str> = line["luma: stats ".len()..]
        .split(' ')
        .map(|kv| kv.split('=').next().unwrap())
        .collect();
    assert_eq!(
        keys,
        [
            "samples",
            "rate_hz",
            "lost",
            "repeats",
            "published",
            "bad_packets",
            "bad_poses",
            "commands",
            "starts_skipped",
            "opens",
            "connections",
            "disconnects",
            "reconnects",
            "stale",
            "time_jumps",
            "max_process_us",
            "p99_process_us",
        ]
    );
}

fn lifecycle_lines() -> Vec<String> {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(2000, 2500)]);
    r.dev().silent = spans(&[(3000, 5000)]);
    r.run(6000);
    r.lines
}

#[test]
fn every_line_is_parseable() {
    for l in lifecycle_lines() {
        assert!(l.starts_with("luma: "), "{l}");
        assert!(!l.contains('\n'), "{l}");
        for pair in l[6..].split(' ').skip(1) {
            assert!(pair.contains('='), "{l}"); // after the event: key=value pairs only
        }
    }
}

#[test]
fn start_sequence_is_spaced_and_stops_when_samples_come() {
    let mut r = Rig::new();
    r.stop_command.clear();
    r.before_start = vec![vec![0x02]];
    r.run(1000);
    assert_eq!(r.link.sent, vec![vec![0x02], vec![0x01]]);
    assert!(r.link.sent_ms[1] - r.link.sent_ms[0] >= 100);
    let mut r = Rig::new();
    r.stop_command.clear();
    r.before_start = vec![vec![0x02]];
    r.dev().stream_byte = 0x02;
    r.run(1000);
    assert_eq!(r.link.sent.len(), 1); // what came first was enough: nothing more goes out
    assert_eq!(r.stats.starts_skipped, 1);
}

#[test]
fn listen_only_sends_nothing() {
    let mut r = Rig::new();
    r.start_command.clear();
    r.stop_command.clear();
    r.run(4000);
    assert!(r.link.sent.is_empty());
    assert_eq!(r.stats.commands, 0);
    assert_eq!(r.count("luma: disconnected reason=silent"), 1); // and it says so: silence on an open link is reported
}

#[test]
fn on_link_and_on_tick_are_called() {
    let mut r = Rig::new();
    r.dev().absent = spans(&[(1000, 1500)]);
    r.dev().run_ms = 3000;
    let stop = Rc::clone(&r.device.borrow().stop);
    let device = Rc::clone(&r.device);
    let links = RefCell::new(Vec::new());
    let ticks = Cell::new(0u32);
    let mut lines = Vec::new();
    let parts = DaemonParts {
        link: &mut r.link,
        decoder: &mut r.decoder,
        make_estimator: Box::new(|| {
            Box::new(FakeEstimator {
                log: Rc::default(),
                settle: 1,
                nan_at: -1,
                lose_at: -1,
                count: 0,
                q: Quatf::default(),
            })
        }),
        sink: &mut r.sink,
        now_ns: Box::new(move || device.borrow().now),
        log: Box::new(|l| lines.push(l.to_owned())),
        start_command: vec![0x01],
        stop_command: vec![],
        before_start: vec![],
        on_link: Some(Box::new(|up, _| links.borrow_mut().push(up))),
        on_tick: Some(Box::new(|_| ticks.set(ticks.get() + 1))),
    };
    Daemon::new(parts, DaemonConfig::default()).run(&stop);
    assert_eq!(*links.borrow(), vec![true, false, true]);
    assert!((25..=35).contains(&ticks.get()), "{}", ticks.get()); // about ten a second, for 3 s
}

// ---- capture and replay ------------------------------------------------------------------------------------------
fn packet(t_ns: u64, seq: u32) -> Vec<u8> {
    let mut p = t_ns.to_le_bytes().to_vec();
    p.extend_from_slice(&seq.to_le_bytes());
    p
}

fn capture(run_ms: u64, silent: &[(u64, u64)], gone: &[(u64, u64)]) -> Vec<CaptureRecord> {
    let (silent, gone) = (spans(silent), spans(gone));
    let mut out = vec![CaptureRecord {
        t_ns: 0,
        kind: CaptureKind::ToDevice,
        payload: vec![0x01],
    }];
    let mut away = false;
    for t in 1..=run_ms {
        let in_gone = within(&gone, t);
        if in_gone != away {
            away = in_gone;
            let kind = if away {
                CaptureKind::DeviceGone
            } else {
                CaptureKind::DeviceBack
            };
            out.push(CaptureRecord {
                t_ns: t * MS,
                kind,
                payload: vec![],
            });
        }
        if in_gone || within(&silent, t) {
            continue;
        }
        out.push(CaptureRecord {
            t_ns: t * MS,
            kind: CaptureKind::FromDevice,
            payload: packet(t * MS, t as u32),
        });
    }
    out
}

#[derive(Default)]
struct Replayed {
    lines: Vec<String>,
    commands: u64,
    stats: Stats,
    sink: FakeSink,
    end: Option<DaemonEnd>,
}

fn replay(records: Vec<CaptureRecord>) -> Replayed {
    let mut out = Replayed::default();
    let mut link = ReplayLink::new(records, false);
    let clock = link.clock();
    let mut decoder = FakeDecoder::default();
    let stop = AtomicBool::new(false);
    let lines = &mut out.lines;
    let parts = DaemonParts {
        link: &mut link,
        decoder: &mut decoder,
        make_estimator: Box::new(|| {
            Box::new(FakeEstimator {
                log: Rc::default(),
                settle: 1500,
                nan_at: -1,
                lose_at: -1,
                count: 0,
                q: Quatf::default(),
            })
        }),
        sink: &mut out.sink,
        now_ns: Box::new(move || clock.get()),
        log: Box::new(|l| lines.push(l.to_owned())),
        start_command: vec![0x01],
        stop_command: vec![0x00],
        before_start: vec![],
        on_link: None,
        on_tick: None,
    };
    let mut daemon = Daemon::new(parts, DaemonConfig::default());
    out.end = Some(daemon.run(&stop));
    out.stats = daemon.stats().clone();
    drop(daemon);
    out.commands = link.commands_swallowed();
    out
}

#[test]
fn replay_clean() {
    let r = replay(capture(3000, &[], &[]));
    assert_eq!(r.end, Some(DaemonEnd::ReplayEnded));
    assert_eq!(r.stats.samples, 3000); // the fake decoder keeps a connection's first packet; the real one drops it
    assert!(
        (1400..=1510).contains(&r.sink.published),
        "{}",
        r.sink.published
    ); // 1.5 s to calibrate, then everything
    assert_eq!(r.commands, 0); // samples from the first moment: listened, sent nothing. No stop when a replay runs out
    assert_eq!(count(&r.lines, "luma: stopped reason=replay-end"), 1);
}

#[test]
fn replay_reproduces_a_drop_and_a_stall() {
    let r = replay(capture(8000, &[(3000, 3200)], &[(5000, 5500)]));
    assert_eq!(count(&r.lines, "luma: stale"), 1);
    assert_eq!(count(&r.lines, "luma: fresh"), 1);
    assert_eq!(count(&r.lines, "luma: disconnected reason=gone"), 1);
    assert_eq!(count(&r.lines, "luma: reconnected"), 1);
    assert_eq!(count(&r.lines, "luma: calibrated restored=1"), 1);
    assert!(r.commands <= 2);
    assert!(!r.sink.backwards);
}

#[test]
fn replay_ending_in_a_drop_ends() {
    let r = replay(capture(2000, &[], &[(1500, 99999)]));
    assert_eq!(r.end, Some(DaemonEnd::ReplayEnded));
    assert_eq!(count(&r.lines, "luma: disconnected"), 1);
}

#[test]
fn replay_of_nothing_ends() {
    let r = replay(vec![]);
    assert_eq!(r.end, Some(DaemonEnd::ReplayEnded));
    assert_eq!(r.stats.samples, 0);
}

#[test]
fn repeats_are_not_junk() {
    struct Repeating {
        inner: FakeDecoder,
        last_seq: Option<u32>,
        repeat: bool,
    }
    impl Decoder for Repeating {
        fn decode(&mut self, packet: &[u8]) -> Option<ImuSample> {
            let s = self.inner.decode(packet)?;
            self.repeat = self.last_seq == Some(s.seq);
            self.last_seq = Some(s.seq);
            (!self.repeat).then_some(s)
        }
        fn last_was_repeat(&self) -> bool {
            self.repeat
        }
    }
    let mut records = Vec::new();
    for t in 1..=3000u64 {
        let p = packet(t * MS, t as u32);
        records.push(CaptureRecord {
            t_ns: t * MS,
            kind: CaptureKind::FromDevice,
            payload: p.clone(),
        });
        if t <= 60 {
            // the Ultra's first 60 or so come twice
            records.push(CaptureRecord {
                t_ns: t * MS + 500_000,
                kind: CaptureKind::FromDevice,
                payload: p,
            });
        }
    }
    let mut link = ReplayLink::new(records, false);
    let clock = link.clock();
    let mut decoder = Repeating {
        inner: FakeDecoder::default(),
        last_seq: None,
        repeat: false,
    };
    let mut sink = FakeSink::default();
    let stop = AtomicBool::new(false);
    let parts = DaemonParts {
        link: &mut link,
        decoder: &mut decoder,
        make_estimator: Box::new(|| {
            Box::new(FakeEstimator {
                log: Rc::default(),
                settle: 1,
                nan_at: -1,
                lose_at: -1,
                count: 0,
                q: Quatf::default(),
            })
        }),
        sink: &mut sink,
        now_ns: Box::new(move || clock.get()),
        log: Box::new(|_| {}),
        start_command: vec![0x01],
        stop_command: vec![],
        before_start: vec![],
        on_link: None,
        on_tick: None,
    };
    let mut daemon = Daemon::new(parts, DaemonConfig::default());
    daemon.run(&stop);
    assert_eq!(daemon.stats().samples, 3000);
    assert_eq!(daemon.stats().repeats, 60);
    assert_eq!(daemon.stats().bad_packets, 0);
}

fn scratch(name: &str) -> PathBuf {
    let dir =
        std::env::temp_dir().join(format!("xr-luma-driver-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn capture_file() {
    let dir = scratch("capture");
    let path = dir.join("round.cap");
    let records = capture(50, &[], &[(20, 25)]);
    save_capture(&path, &records).unwrap();
    assert_eq!(load_capture(&path).unwrap(), records);

    assert!(
        load_capture(&dir.join("absent"))
            .unwrap_err()
            .starts_with("cannot open")
    );
    let write_bytes = |name: &str, bytes: &[u8]| -> PathBuf {
        let p = dir.join(name);
        std::fs::write(&p, bytes).unwrap();
        p
    };
    assert!(
        load_capture(&write_bytes("empty", b""))
            .unwrap_err()
            .starts_with("not a capture")
    );
    assert!(
        load_capture(&write_bytes("wrong", b"NOTACAPTURE....."))
            .unwrap_err()
            .starts_with("not a capture")
    );
    assert_eq!(
        load_capture(&write_bytes("only-magic", b"LUMACAP0")).unwrap(),
        vec![]
    );
    let whole = std::fs::read(&path).unwrap();
    let cut = load_capture(&write_bytes("cut.cap", &whole[..whole.len() - 3])).unwrap_err();
    assert!(cut.starts_with("capture cut short"), "{cut}");
    let huge = b"LUMACAP0\0\0\0\0\0\0\0\0\xff\xff\xff\xff\0";
    assert!(
        load_capture(&write_bytes("huge", huge))
            .unwrap_err()
            .starts_with("capture record invalid")
    );
    let bad_kind = b"LUMACAP0\0\0\0\0\0\0\0\0\0\0\0\0\x09";
    assert!(
        load_capture(&write_bytes("kind", bad_kind))
            .unwrap_err()
            .starts_with("capture record invalid")
    );
    let mut backwards = b"LUMACAP0".to_vec();
    backwards.extend_from_slice(b"\x05\0\0\0\0\0\0\0\0\0\0\0\0");
    backwards.extend_from_slice(b"\x01\0\0\0\0\0\0\0\0\0\0\0\0");
    assert!(
        load_capture(&write_bytes("backwards", &backwards))
            .unwrap_err()
            .starts_with("capture goes back in time")
    );
    let header_cut = b"LUMACAP0\0\0\0\0\0\0\0\0\0\0";
    assert!(
        load_capture(&write_bytes("header", header_cut))
            .unwrap_err()
            .starts_with("capture cut short in a record header")
    );
    assert!(
        load_capture(&write_bytes("v1", b"LUMACAP1\0\0\0\0"))
            .unwrap_err()
            .contains("LUMACAP1")
    );
    let _ = std::fs::remove_dir_all(&dir);
}

// ---- command line -------------------------------------------------------------------------------------------------
fn parse(args: &[&str]) -> Result<Options, String> {
    parse_options(args)
}

#[test]
fn options() {
    let plain = parse(&[]).unwrap(); // how tracking.py starts it
    assert!(plain.ipc_prefix == LIVE_IPC_PREFIX && plain.replay.is_none());
    let all = parse(&[
        "--ipc-prefix",
        "/run/x_",
        "--replay",
        "f",
        "--replay-realtime",
        "--stale-ms",
        "50",
        "--reopen-ms",
        "700",
        "--keep-calibration-ms",
        "3000",
    ])
    .unwrap();
    assert!(
        all.ipc_prefix == "/run/x_" && all.replay.as_deref() == Some("f") && all.replay_realtime
    );
    assert!(
        all.config.stale_after == ms(50)
            && all.config.silent_reopen_after == ms(700)
            && all.config.calibration_keep == ms(3000)
    );
    assert!(parse(&["--help"]).unwrap().help);
    assert!(parse(&["-h"]).unwrap().help);
    assert_eq!(
        parse(&["--replay", "f"]).unwrap_err(),
        "--replay needs its own --ipc-prefix: the default is the live feed"
    );
    assert_eq!(
        parse(&["--replay", "f", "--ipc-prefix", "/tmp/shader_runtime_"]).unwrap_err(),
        "--replay must not write the live feed"
    );
    assert_eq!(
        parse(&["--replay-realtime"]).unwrap_err(),
        "--replay-realtime needs --replay"
    );
    assert_eq!(
        parse(&["--ipc-prefix"]).unwrap_err(),
        "--ipc-prefix needs a value"
    );
    assert_eq!(
        parse(&["--ipc-prefix", ""]).unwrap_err(),
        "--ipc-prefix must not be empty"
    );
    for bad in ["abc", "0", "-5", "10x", "+5", "", "3600001"] {
        assert_eq!(
            parse(&["--stale-ms", bad]).unwrap_err(),
            "--stale-ms needs a whole number of ms, 1 to 3600000",
            "{bad}"
        );
    }
    assert!(parse(&["--stale-ms", "3600000", "--reopen-ms", "3600000"]).is_err());
    assert_eq!(
        parse(&["--stale-ms", "2000"]).unwrap_err(),
        "--reopen-ms must be longer than --stale-ms"
    );
    assert_eq!(
        parse(&["--frobnicate"]).unwrap_err(),
        "unknown option: --frobnicate"
    );
    let flags = parse(&[
        "--no-keep-heading",
        "--no-stop-command",
        "--settle-ms",
        "5000",
    ])
    .unwrap();
    assert!(
        !flags.config.keep_heading && !flags.stop_command && flags.config.settle_first == ms(5000)
    );
    assert_eq!(flags.config.settle_max, ms(5000));
    assert!(plain.config.keep_heading && plain.stop_command);
    assert_eq!(plain.config.calibration_keep, ms(300_000));
    assert_eq!(plain.config.settle_max, ms(3200));
    // What the live plan runs.
    assert!(plain.start == Start::Stream && !plain.read_events && !plain.drain_camera); // tracking.py's default
    assert_eq!(parse(&["--start", "listen"]).unwrap().start, Start::Listen);
    assert_eq!(
        parse(&["--start", "prepared"]).unwrap().start,
        Start::Prepared
    );
    assert_eq!(parse(&["--start", "full"]).unwrap().start, Start::Full);
    assert_eq!(
        parse(&["--start", "guess"]).unwrap_err(),
        "--start needs listen, stream, prepared or full"
    );
    assert!(parse(&["--start"]).is_err());
    let live = parse(&[
        "--start",
        "stream",
        "--read-events",
        "--drain-camera",
        "--no-rt",
        "--state-dir",
        "/x",
        "--feature-discovery",
        "--no-feature-temperature",
    ])
    .unwrap();
    assert!(live.read_events && live.drain_camera && live.no_rt && live.state_dir == "/x");
    assert_eq!(
        live.feature_flags,
        ["--feature-discovery", "--no-feature-temperature"]
    );
    assert_eq!(
        parse(&["--pose-log", "f"]).unwrap_err(),
        "--pose-log needs --replay"
    ); // replay only
    let logged = parse(&[
        "--replay",
        "f",
        "--ipc-prefix",
        "/run/x_",
        "--pose-log",
        "p",
        "--pose-log-every",
        "5",
    ])
    .unwrap();
    assert!(logged.pose_log.as_deref() == Some("p") && logged.pose_log_every == 5);
    assert_eq!(
        parse(&["--latency-log", "l"])
            .unwrap()
            .latency_log
            .as_deref(),
        Some("l")
    ); // live too
    assert!(parse(&["--latency-log"]).is_err());
}

// ---- one driver per feed -------------------------------------------------------------------------------------------
#[test]
fn instance_lock_is_single_writer() {
    let dir = scratch("lock");
    let prefix = format!("{}/x_", dir.display());
    let held = take_instance_lock(&prefix).unwrap();
    assert_eq!(take_instance_lock(&prefix).unwrap_err(), "already-running"); // a second open file is a second owner
    drop(held); // the kernel drops it with the descriptor, however the process ends
    let again = take_instance_lock(&prefix).unwrap();
    drop(again);
    use std::os::unix::fs::PermissionsExt;
    let mode = std::fs::metadata(format!("{prefix}luma-driver.lock"))
        .unwrap()
        .permissions()
        .mode();
    assert_eq!(mode & 0o777, 0o600);

    // A symlink planted at the lock's path is not followed.
    let planted = format!("{}/y_", dir.display());
    std::os::unix::fs::symlink(dir.join("elsewhere"), format!("{planted}luma-driver.lock"))
        .unwrap();
    let refused = take_instance_lock(&planted).unwrap_err();
    assert!(
        refused.starts_with(&format!("lock {planted}luma-driver.lock ")),
        "{refused}"
    );
    assert!(!dir.join("elsewhere").exists());

    let missing = take_instance_lock(&format!("{}/no-such-folder/x_", dir.display())).unwrap_err();
    assert!(
        missing.ends_with("luma-driver.lock No such file or directory"),
        "{missing}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

// ---- the Python readers of these lines -----------------------------------------------------------------------------
fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .unwrap()
}

/// tracking.py's readiness rule and driverlog.classify, run by the real Python on the lines as the binary writes them
/// (with `at=`). The log format is a wire protocol: this is what holds it to the C++ driver's.
#[test]
fn python_readers_understand_the_lines() {
    let python = Path::new("/usr/bin/python3");
    let root = repo_root();
    if !python.exists() || !root.join("tools/native_spike/tracking.py").exists() {
        eprintln!("skipped: no /usr/bin/python3 or no tools/ next to rust/");
        return;
    }
    let mut lines = lifecycle_lines();
    let mut waiting = Rig::new();
    waiting.dev().why_absent = "device not found".into();
    waiting.dev().absent = spans(&[(0, 500)]);
    waiting.run(1500);
    lines.splice(
        0..0,
        waiting
            .lines
            .iter()
            .filter(|l| l.starts_with("luma: waiting"))
            .cloned(),
    );
    let at = UNIX_EPOCH + Duration::from_millis(1_793_700_000_123);
    let text: String = lines.iter().map(|l| stamped(l, at)).collect();
    let dir = scratch("python");
    let log = dir.join("luma-driver.log");
    std::fs::write(&log, &text).unwrap();
    let script = r#"
import json, re, sys
sys.path.insert(0, sys.argv[1])
from tools.native_spike import tracking
from tools.luma_soak import driverlog
current = open(sys.argv[2], encoding='utf-8').read()
calibrated = current.rfind(tracking.LUMA_READY) > max(current.rfind(m) for m in tracking.LUMA_LOST)
cut = current[:current.rfind('luma: disconnected')]
waiting = re.findall(r'luma: waiting reason=(\S+)', current)
lines = current.splitlines()
print(json.dumps({'calibrated': calibrated,
                  'calibrated_before_drop': cut.rfind(tracking.LUMA_READY) > max(cut.rfind(m) for m in tracking.LUMA_LOST),
                  'waiting': waiting,
                  'kinds': [driverlog.classify(l) for l in lines],
                  'times': [driverlog.line_time(l) for l in lines]}))
"#;
    let out = std::process::Command::new(python)
        .arg("-I")
        .arg("-c")
        .arg(script)
        .arg(root)
        .arg(&log)
        .env("TMPDIR", "/tmp")
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let json = String::from_utf8(out.stdout).unwrap();
    let _ = std::fs::remove_dir_all(&dir);

    assert!(json.contains(r#""calibrated": true"#), "{json}");
    assert!(json.contains(r#""calibrated_before_drop": true"#), "{json}");
    assert!(
        json.contains(r#""waiting": ["device-not-found", "absent"]"#),
        "{json}"
    );
    // Every line is one the soak tools know, except the stats line, which driverlog leaves to the reader for the C++
    // driver too; nothing comes back None.
    assert_eq!(json.matches(r#""kind": "other""#).count(), 1, "{json}");
    assert!(
        json.contains(r#"{"kind": "other", "event": "luma stats"}"#) && !json.contains("null"),
        "{json}"
    );
    for want in [
        r#"{"kind": "state", "state": "waiting"}"#,
        r#"{"kind": "state", "state": "connected"}"#,
        r#"{"kind": "state", "state": "calibrating"}"#,
        r#"{"kind": "state", "state": "calibrated"}"#,
        r#"{"kind": "usb_error", "cmd": "disconnected", "reason": "gone"}"#,
        r#"{"kind": "usb_error", "cmd": "disconnected", "reason": "silent"}"#,
        r#""kind": "sample_gap", "ms": "#,
        r#""kind": "state", "state": "reconnected", "ms": "#,
        r#""calibration": "kept""#,
        r#"{"kind": "state", "state": "stopped"}"#,
    ] {
        assert!(json.contains(want), "missing {want} in {json}");
    }
    assert_eq!(
        json.matches("1793700000.123").count(),
        lines.len(),
        "{json}"
    );
}
