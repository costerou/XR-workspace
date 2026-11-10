//! The driver's daemon: bytes from the glasses in, pose feed out, and what happens when the USB link misbehaves. A port of
//! `spike/driver/src/daemon.cpp`, with the same behaviour and the same log lines.
//!
//! It knows the other pieces only through the small traits below, so every failure can be replayed against fakes with a
//! simulated clock (`tests/daemon.rs`). The binary is the one place that plugs the real transport, packet parser, fusion
//! and publisher into them.
//!
//! Zero USB drops comes first, so the command traffic is as small as it can be. Sessions with XRLinuxDriver drop the
//! glasses and as a plain display they never do; what the evidence shows is in `spike/driver/docs/upstream-findings.md`,
//! and nothing proves the cause yet. So the daemon sends at most one start per connection and nothing else while it runs:
//! no polling, no keep-alive, no display-mode query, never a device reset. Three rules from those findings:
//!   - Listen first. If samples are already arriving after an open, the stream is on and no command goes out at all.
//!   - Let the glasses settle. 18 of 53 drops came within 30 s of the one before, and a reconnect may be what caused the
//!     second, so a device that just came back is left alone for `settle_first`; if it dies again soon after a start, for
//!     twice as long, up to `settle_max`. A device that drops during the wait never received a command.
//!   - Leave a silent device alone. In some drops the IMU stopped 7-12 s before the hub left, so a stalled stream is not
//!     pushed at: retries double up to one every 8 s.
//!
//! `Stats::commands` counts every command, and the `luma: stats` line shows it.
//!
//! Log lines, one event per line, `luma: <event> key=value ...` (the binary adds ` at=<epoch s>.<ms>`):
//!
//! ```text
//! luma: connected                         the device is open (first time only)
//! luma: waiting reason=WHY                the device cannot be opened, whenever WHY changes (absent; another driver has it)
//! luma: calibrating                       a new calibration began: hold the glasses still
//! luma: calibrated restored=0|1 [heading=kept]
//!                                         the pose is usable; restored=1: a reconnect kept the earlier calibration;
//!                                         heading=kept: a new calibration, turned to carry on from the last heading
//! luma: stale ms=N                        the link is up but no samples for N ms; the feed is marked disabled
//! luma: fresh gap_ms=N                    samples came back on the same connection
//! luma: disconnected reason=gone|silent|write-failed
//! luma: retry attempt=N reason=silent|write-failed
//!                                         another try at a device that opens but stays silent or refuses the start
//! luma: reconnected gap_ms=N calibration=kept|lost|none
//! luma: stats samples=N ... commands=N    once a minute and at exit
//! luma: stopped reason=signal|replay-end
//! ```
//!
//! Readiness is "the last `calibrated` comes after the last `disconnected` and `calibrating`", as
//! `tools/native_spike/tracking.py` reads it: that is why a kept calibration logs `calibrated restored=1` again after its
//! `reconnected`.

use crate::latency::LatencyHistogram;
use crate::types::{ImuSample, PoseOut, Quatf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

const MS: u64 = 1_000_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReadStatus {
    /// `size` bytes arrived.
    Data,
    /// Nothing in time, or a signal cut the wait short.
    Timeout,
    /// The device went away (read error or removal).
    Gone,
    /// A replay ran out; the live device never says this.
    End,
}

/// `t_ns`: CLOCK_MONOTONIC when the transfer completed (the URB was reaped), or the record time in a replay. 0: not known.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ReadResult {
    pub status: ReadStatus,
    pub size: usize,
    pub t_ns: u64,
}

impl ReadResult {
    pub const TIMEOUT: ReadResult = ReadResult {
        status: ReadStatus::Timeout,
        size: 0,
        t_ns: 0,
    };
    pub const GONE: ReadResult = ReadResult {
        status: ReadStatus::Gone,
        size: 0,
        t_ns: 0,
    };
    pub const END: ReadResult = ReadResult {
        status: ReadStatus::End,
        size: 0,
        t_ns: 0,
    };
    pub fn data(size: usize, t_ns: u64) -> ReadResult {
        ReadResult {
            status: ReadStatus::Data,
            size,
            t_ns,
        }
    }
}

/// The USB side. `open` must not send anything to the device: the daemon decides what goes out.
pub trait Link {
    /// `Err` says why the device cannot be opened (it is logged as the `waiting` reason).
    fn open(&mut self) -> Result<(), String>;
    fn read(&mut self, into: &mut [u8], timeout: Duration) -> ReadResult;
    fn write(&mut self, bytes: &[u8]) -> bool;
    fn close(&mut self);
    /// Wait at most `timeout` for the device to appear. True: it did (a hotplug event), so try now, whatever the back-off
    /// says.
    fn wait_for_device(&mut self, timeout: Duration) -> bool;
    /// A replay with nothing left: stop waiting for the device.
    fn exhausted(&self) -> bool {
        false
    }
}

pub trait Estimator {
    fn update(&mut self, sample: &ImuSample) -> PoseOut;
}

impl Estimator for crate::fusion::Fusion {
    fn update(&mut self, sample: &ImuSample) -> PoseOut {
        crate::fusion::Fusion::update(self, sample)
    }
}

pub trait Sink {
    fn publish(&mut self, pose: &PoseOut);
    /// Stale: readers must not trust the feed (the `disabled` flag of XRLinuxDriver's layout; the renderer then holds the
    /// last good pose).
    fn set_stale(&mut self, stale: bool);
}

/// Bytes to samples. Stateful, because counting lost samples and spotting a restarted device counter needs history.
pub trait Decoder {
    fn decode(&mut self, packet: &[u8]) -> Option<ImuSample>;
    fn new_connection(&mut self) {}
    fn lost_samples(&self) -> u64 {
        0
    }
    /// The last packet `decode` turned down was not junk: the Ultra sends the first 60 or so twice, and the first of a
    /// connection may be left over from whoever read last.
    fn last_was_repeat(&self) -> bool {
        false
    }
    /// From the newest packet, when it has one.
    fn temperature_c(&self) -> Option<f32> {
        None
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct DaemonConfig {
    /// Also how soon a stop request is noticed.
    pub read_timeout: Duration,
    /// Matches PoseState's default in `spike/include/xr/pose.hpp`.
    pub stale_after: Duration,
    /// No samples this long on an open link: close and try again.
    pub silent_reopen_after: Duration,
    /// Device gone: looking for it costs no USB traffic.
    pub retry_first: Duration,
    pub retry_max: Duration,
    /// Device silent: each try sends a command.
    pub silent_retry_first: Duration,
    pub silent_retry_max: Duration,
    /// After an open, before any command: a running stream needs none (20 samples).
    pub listen_first: Duration,
    /// Between the commands of `before_start`, as VITURE's own software spaces them.
    pub start_spacing: Duration,
    /// The same for a device that just came back: see the top of this file.
    pub settle_first: Duration,
    pub settle_max: Duration,
    /// Back within this long: same calibration, same orientation. Fusion needs no reset for a gap (it re-acquires gravity
    /// and the bias still holds), while a new calibration means holding still. The window only guards against a bias
    /// gone stale, which fusion goes on correcting anyway. Drops cluster, so a short window would lose it for nothing.
    pub calibration_keep: Duration,
    /// A connection this long ends the back-off; a flapping one does not.
    pub healthy_after: Duration,
    /// Device time stepping further than this is a new time base.
    pub time_jump: Duration,
    pub stats_every: Duration,
    /// A new calibration carries on from the last published heading.
    pub keep_heading: bool,
}

impl Default for DaemonConfig {
    fn default() -> Self {
        let ms = Duration::from_millis;
        DaemonConfig {
            read_timeout: ms(20),
            stale_after: ms(100),
            silent_reopen_after: ms(1000),
            retry_first: ms(20),
            retry_max: ms(100),
            silent_retry_first: ms(2000),
            silent_retry_max: ms(8000),
            listen_first: ms(20),
            start_spacing: ms(100),
            settle_first: ms(200),
            settle_max: ms(3200),
            calibration_keep: ms(300_000),
            healthy_after: ms(2000),
            time_jump: ms(500),
            stats_every: ms(60_000),
            keep_heading: true,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    pub samples: u64,
    pub bad_packets: u64,
    pub bad_poses: u64,
    pub published: u64,
    pub repeats: u64,
    pub commands: u64,
    pub opens: u64,
    pub connections: u64,
    pub disconnects: u64,
    pub reconnects: u64,
    pub stale_events: u64,
    pub time_jumps: u64,
    pub fresh_calibrations: u64,
    /// Connections that found the stream already on, and sent nothing.
    pub starts_skipped: u64,
    /// The slowest packet, bytes in to pose out, since the last stats line.
    pub max_process_ns: u64,
}

pub type EstimatorFactory<'a> = Box<dyn FnMut() -> Box<dyn Estimator + 'a> + 'a>;

pub struct DaemonParts<'a> {
    pub link: &'a mut dyn Link,
    pub decoder: &'a mut dyn Decoder,
    /// A new estimator means a new calibration.
    pub make_estimator: EstimatorFactory<'a>,
    pub sink: &'a mut dyn Sink,
    /// Monotonic, in ns.
    pub now_ns: Box<dyn Fn() -> u64 + 'a>,
    pub log: Box<dyn FnMut(&str) + 'a>,
    pub start_command: Vec<u8>,
    /// Empty: none is sent at exit.
    pub stop_command: Vec<u8>,
    /// Sent in order ahead of `start_command`, `start_spacing` apart. A sample arriving meanwhile cancels the rest. An
    /// empty `start_command` with nothing here only listens.
    pub before_start: Vec<Vec<u8>>,
    /// Off the hot path: link changes. None: nothing is called.
    pub on_link: Option<Box<dyn FnMut(bool, u64) + 'a>>,
    /// Off the hot path: about ten times a second while running.
    pub on_tick: Option<Box<dyn FnMut(u64) + 'a>>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DaemonEnd {
    StopRequested,
    ReplayEnded,
}

fn ns(d: Duration) -> u64 {
    d.as_nanos() as u64
}

fn finite(q: &Quatf) -> bool {
    q.w.is_finite() && q.x.is_finite() && q.y.is_finite() && q.z.is_finite()
}

fn multiply(a: &Quatf, b: &Quatf) -> Quatf {
    Quatf {
        w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
        x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
    }
}

/// The heading of the forward axis (body x) in a world with z up. Looking straight up or down there is none.
fn heading_of(q: &Quatf) -> Option<f32> {
    let forward_x = 1.0 - 2.0 * (q.y * q.y + q.z * q.z);
    let forward_y = 2.0 * (q.x * q.y + q.w * q.z);
    if forward_x.hypot(forward_y) < 0.1 {
        return None;
    }
    Some(forward_y.atan2(forward_x))
}

fn doubled(first: Duration, times: u32, cap: Duration) -> Duration {
    let mut delay = first;
    for _ in 0..times {
        if delay >= cap {
            break;
        }
        delay *= 2;
    }
    delay.min(cap)
}

pub struct Daemon<'a> {
    parts: DaemonParts<'a>,
    config: DaemonConfig,
    stats: Stats,
    process_ns: LatencyHistogram,
    before_start_sent: usize,
    estimator: Option<Box<dyn Estimator + 'a>>,
    buffer: [u8; 1024],

    /// The link is open (the start command may still be pending).
    up: bool,
    ever_connected: bool,
    /// Between `disconnected` and the first sample after it.
    outage: bool,
    /// The last sample before the outage.
    down_since: u64,
    next_open: u64,
    retries: u32,
    silent_retries: u32,
    settle_level: u32,
    /// It went away since we last had it: it gets to settle.
    device_left: bool,
    /// Connected, listening, nothing sent yet.
    start_pending: bool,
    /// On this connection.
    start_sent: bool,
    start_at: u64,
    /// The last `waiting` reason logged.
    waiting_for: Option<String>,
    /// The last sample, or the moment this connection opened.
    watch_from: u64,
    connection_first_sample: u64,
    connection_has_sample: bool,

    stale: bool,
    /// `calibrated` was logged since the last outage.
    announced: bool,
    /// The estimator has reported calibrated.
    calibrated: bool,
    /// The next `calibrated` is a kept calibration.
    restored: bool,
    announce_calibrating: bool,

    // Device time, made to run forward: see steady_time.
    have_time: bool,
    resume_time: bool,
    last_raw: u64,
    last_steady: u64,
    last_sample_now: u64,
    offset: u64,
    last_published_t: u64,
    // A new calibration's heading is the world it happened to face: see carry_heading.
    heading_pending: bool,
    heading_fixed: bool,
    heading_kept: bool,
    last_q: Option<Quatf>,
    heading_fix: Quatf,
    next_stats: u64,
    stats_samples: u64,
    stats_since: u64,
    next_tick: u64,
}

impl<'a> Daemon<'a> {
    pub fn new(parts: DaemonParts<'a>, config: DaemonConfig) -> Self {
        Daemon {
            parts,
            config,
            stats: Stats::default(),
            process_ns: LatencyHistogram::default(),
            before_start_sent: 0,
            estimator: None,
            buffer: [0; 1024],
            up: false,
            ever_connected: false,
            outage: false,
            down_since: 0,
            next_open: 0,
            retries: 0,
            silent_retries: 0,
            settle_level: 0,
            device_left: false,
            start_pending: false,
            start_sent: false,
            start_at: 0,
            waiting_for: None,
            watch_from: 0,
            connection_first_sample: 0,
            connection_has_sample: false,
            stale: true,
            announced: false,
            calibrated: false,
            restored: false,
            announce_calibrating: false,
            have_time: false,
            resume_time: false,
            last_raw: 0,
            last_steady: 0,
            last_sample_now: 0,
            offset: 0,
            last_published_t: 0,
            heading_pending: false,
            heading_fixed: false,
            heading_kept: false,
            last_q: None,
            heading_fix: Quatf::default(),
            next_stats: 0,
            stats_samples: 0,
            stats_since: 0,
            next_tick: 0,
        }
    }

    pub fn stats(&self) -> &Stats {
        &self.stats
    }

    /// The whole run, bytes in to pose out.
    pub fn process_times(&self) -> &LatencyHistogram {
        &self.process_ns
    }

    fn log(&mut self, line: &str) {
        (self.parts.log)(line);
    }

    fn now(&self) -> u64 {
        (self.parts.now_ns)()
    }

    /// Until `stop` is set (a signal handler may set it; it is noticed within `read_timeout`) or a replay ends.
    pub fn run(&mut self, stop: &AtomicBool) -> DaemonEnd {
        self.parts.sink.set_stale(true); // nothing to trust until the first calibrated pose
        self.stale = true;
        self.new_estimator();
        let started = self.now();
        self.next_open = started;
        self.next_stats = started + ns(self.config.stats_every);
        self.stats_since = started;
        let mut ended = false;
        while !stop.load(Ordering::Relaxed) && !ended {
            let now = self.now();
            if now >= self.next_stats {
                self.log_stats();
                self.next_stats = now + ns(self.config.stats_every);
            }
            if now >= self.next_tick {
                if let Some(tick) = self.parts.on_tick.as_mut() {
                    tick(now);
                    self.next_tick = now + 100 * MS;
                }
            }
            if self.up {
                let r = self
                    .parts
                    .link
                    .read(&mut self.buffer, self.config.read_timeout);
                let after = self.now();
                let work_began = Instant::now(); // a real clock: a replay's now_ns is simulated
                match r.status {
                    ReadStatus::Data => {
                        let n = r.size.min(self.buffer.len());
                        match self.parts.decoder.decode(&self.buffer[..n]) {
                            Some(sample) => self.on_sample(sample, after),
                            None if self.parts.decoder.last_was_repeat() => self.stats.repeats += 1,
                            None => self.stats.bad_packets += 1,
                        }
                        let spent = work_began.elapsed().as_nanos() as u64;
                        self.stats.max_process_ns = self.stats.max_process_ns.max(spent);
                        self.process_ns.record(spent);
                    }
                    ReadStatus::Timeout => {}
                    ReadStatus::Gone => self.go_down("gone", false, after),
                    ReadStatus::End => ended = true,
                }
                if self.up && !ended && self.start_pending && after >= self.start_at {
                    self.send_start(after);
                }
                if self.up && !ended && !self.start_pending {
                    self.watchdog(after);
                }
            } else if self.parts.link.exhausted() {
                ended = true;
            } else {
                self.connect(now);
            }
        }
        // A stop asks the glasses to stop streaming, once, if they are still there. A replay that ran out has nobody to
        // tell.
        if self.up {
            // Only if we know the link was in use: a stop during the settling wait would be the very traffic that wait
            // avoids.
            if !ended && !self.start_pending && !self.parts.stop_command.is_empty() {
                self.stats.commands += 1;
                self.parts.link.write(&self.parts.stop_command);
            }
            self.parts.link.close();
            self.up = false;
        }
        self.set_stale(true);
        self.log_stats();
        self.log(if ended {
            "luma: stopped reason=replay-end"
        } else {
            "luma: stopped reason=signal"
        });
        if ended {
            DaemonEnd::ReplayEnded
        } else {
            DaemonEnd::StopRequested
        }
    }

    fn log_stats(&mut self) {
        let now = self.now();
        let window = now.wrapping_sub(self.stats_since);
        let rate = if window != 0 {
            (self.stats.samples - self.stats_samples) as f64 * 1e9 / window as f64
        } else {
            0.0
        };
        let s = &self.stats;
        let line = format!(
            "luma: stats samples={} rate_hz={:.0} lost={} repeats={} published={} bad_packets={} bad_poses={} commands={} \
             starts_skipped={} opens={} connections={} disconnects={} reconnects={} stale={} time_jumps={} max_process_us={:.1} \
             p99_process_us={:.1}",
            s.samples,
            rate,
            self.parts.decoder.lost_samples(),
            s.repeats,
            s.published,
            s.bad_packets,
            s.bad_poses,
            s.commands,
            s.starts_skipped,
            s.opens,
            s.connections,
            s.disconnects,
            s.reconnects,
            s.stale_events,
            s.time_jumps,
            s.max_process_ns as f64 / 1000.0,
            self.process_ns.percentile(99.0) as f64 / 1000.0,
        );
        self.log(&line);
        self.stats_samples = self.stats.samples;
        self.stats_since = now;
        self.stats.max_process_ns = 0;
    }

    fn set_stale(&mut self, stale: bool) {
        if stale == self.stale {
            return;
        }
        self.stale = stale;
        self.parts.sink.set_stale(stale);
    }

    fn new_estimator(&mut self) {
        self.estimator = Some((self.parts.make_estimator)());
        self.stats.fresh_calibrations += 1;
        self.calibrated = false;
        self.announced = false;
        self.restored = false;
        self.announce_calibrating = true;
        self.heading_pending = true;
    }

    fn connect(&mut self, now: u64) {
        if now < self.next_open {
            let wait = Duration::from_millis((self.next_open - now).div_ceil(MS))
                .min(self.config.read_timeout);
            if self.parts.link.wait_for_device(wait) {
                self.next_open = now;
            }
            return;
        }
        self.stats.opens += 1;
        if let Err(why) = self.parts.link.open() {
            self.device_left = true;
            self.next_open = now
                + ns(doubled(
                    self.config.retry_first,
                    self.retries,
                    self.config.retry_max,
                ));
            self.retries += 1;
            // A device held by another driver would otherwise look like no device, for ever.
            if self.waiting_for.as_deref() != Some(why.as_str()) {
                let reason = if why.is_empty() {
                    "unknown".to_owned()
                } else {
                    why.replace(' ', "-")
                };
                self.waiting_for = Some(why);
                self.log(&format!("luma: waiting reason={reason}"));
            }
            return;
        }
        self.waiting_for = None;
        self.up = true;
        self.start_pending = true;
        self.start_sent = false;
        self.before_start_sent = 0;
        self.stats.connections += 1;
        self.connection_has_sample = false;
        self.watch_from = now;
        let wait = if self.device_left {
            doubled(
                self.config.settle_first,
                self.settle_level,
                self.config.settle_max,
            )
        } else {
            self.config.listen_first
        };
        self.start_at = now + ns(wait);
        self.device_left = false;
        self.parts.decoder.new_connection();
        if !self.ever_connected {
            self.ever_connected = true;
            self.log("luma: connected");
        }
        if let Some(on_link) = self.parts.on_link.as_mut() {
            on_link(true, now);
        }
    }

    fn send_start(&mut self, now: u64) {
        if self.before_start_sent < self.parts.before_start.len() {
            let i = self.before_start_sent;
            self.before_start_sent += 1;
            self.start_sent = true;
            self.stats.commands += 1;
            self.start_at = now + ns(self.config.start_spacing);
            if !self.parts.link.write(&self.parts.before_start[i]) {
                self.go_down("write-failed", true, now);
            }
            return;
        }
        self.start_pending = false;
        self.watch_from = now; // the silence that counts starts here
        if self.parts.start_command.is_empty() {
            return; // listening only
        }
        self.start_sent = true;
        self.stats.commands += 1; // counted even if it fails: it may have reached the wire
        if !self.parts.link.write(&self.parts.start_command) {
            self.go_down("write-failed", true, now);
        }
    }

    fn go_down(&mut self, reason: &str, silent: bool, now: u64) {
        self.parts.link.close();
        self.up = false;
        if let Some(on_link) = self.parts.on_link.as_mut() {
            on_link(false, now);
        }
        let healthy = self.connection_has_sample
            && self.watch_from.wrapping_sub(self.connection_first_sample)
                >= ns(self.config.healthy_after);
        if healthy {
            self.retries = 0;
            self.silent_retries = 0;
            self.settle_level = 0;
        } else if !silent && self.start_sent {
            self.settle_level += 1; // it died soon after we spoke to it: speak later next time
        }
        if !silent {
            self.device_left = true;
        }
        if !self.outage {
            self.outage = true;
            self.stats.disconnects += 1;
            self.down_since = if self.connection_has_sample {
                self.watch_from
            } else {
                now
            };
            self.log(&format!("luma: disconnected reason={reason}"));
        } else if silent {
            self.log(&format!(
                "luma: retry attempt={} reason={reason}",
                self.silent_retries + 1
            ));
        }
        self.set_stale(true);
        self.announced = false;
        if silent {
            self.next_open = now
                + ns(doubled(
                    self.config.silent_retry_first,
                    self.silent_retries,
                    self.config.silent_retry_max,
                ));
            self.silent_retries += 1;
        } else {
            self.next_open = now
                + ns(doubled(
                    self.config.retry_first,
                    self.retries,
                    self.config.retry_max,
                ));
            self.retries += 1;
        }
    }

    fn watchdog(&mut self, now: u64) {
        // Only a valid sample feeds it: a link that returns nothing but junk is as dead as a silent one.
        let quiet = now.wrapping_sub(self.watch_from);
        if !self.stale && quiet >= ns(self.config.stale_after) {
            self.stats.stale_events += 1;
            self.log(&format!("luma: stale ms={}", quiet / MS));
            self.set_stale(true);
        }
        if quiet >= ns(self.config.silent_reopen_after) {
            self.go_down("silent", true, now);
        }
    }

    fn begin_after_outage(&mut self, now: u64) {
        let gap = now.wrapping_sub(self.down_since);
        let calibration = if !self.calibrated {
            self.new_estimator();
            "none"
        } else if gap > ns(self.config.calibration_keep) {
            self.new_estimator();
            "lost"
        } else {
            self.restored = true;
            "kept"
        };
        self.stats.reconnects += 1;
        self.outage = false;
        self.resume_time = true;
        self.log(&format!(
            "luma: reconnected gap_ms={} calibration={calibration}",
            gap / MS
        ));
    }

    /// The device clock is not ours to trust across a drop: the glasses may restart it from zero. Everything downstream
    /// gets one clock that only runs forward. Across a reconnect or a jump it advances by the time that really passed, so
    /// the estimator sees the gap as a gap (fusion then skips that motion and re-acquires gravity, where hiding it would
    /// keep it integrating as if nothing had happened) and readers of the feed age and predict correctly.
    /// The arithmetic is modular on purpose: `offset` may "go negative".
    fn steady_time(&mut self, mut sample: ImuSample, now: u64) -> ImuSample {
        let raw = sample.t_ns;
        if self.have_time {
            let jumped = raw <= self.last_raw || raw - self.last_raw > ns(self.config.time_jump);
            if self.resume_time || jumped {
                if !self.resume_time {
                    self.stats.time_jumps += 1;
                }
                let passed = now.wrapping_sub(self.last_sample_now).max(1);
                self.offset = self.last_steady.wrapping_add(passed).wrapping_sub(raw);
            }
        }
        self.resume_time = false;
        self.have_time = true;
        self.last_raw = raw;
        self.last_sample_now = now;
        self.last_steady = raw.wrapping_add(self.offset);
        sample.t_ns = self.last_steady;
        sample
    }

    /// A new estimator's world x is wherever the glasses faced when it calibrated, so without this every desktop would
    /// jump to a new place. Turning its world about up, by the heading difference, carries on from the last pose
    /// published; tilt is left alone, as that is true gravity. Whatever the head really did while nothing was measured
    /// stays unknown, as with any gap.
    fn carry_heading(&mut self, pose: &mut PoseOut) {
        if self.heading_pending {
            self.heading_pending = false;
            self.heading_fixed = false;
            let was = self.last_q.as_ref().and_then(heading_of);
            let is = heading_of(&pose.q);
            if let (true, Some(was), Some(is)) = (self.config.keep_heading, was, is) {
                let half = (was - is) / 2.0;
                self.heading_fix = Quatf {
                    w: half.cos(),
                    x: 0.0,
                    y: 0.0,
                    z: half.sin(),
                };
                self.heading_fixed = true;
                self.heading_kept = true;
            }
        }
        if self.heading_fixed {
            pose.q = multiply(&self.heading_fix, &pose.q);
        }
    }

    fn on_sample(&mut self, sample: ImuSample, now: u64) {
        self.stats.samples += 1;
        let was_quiet = self.stale && self.announced;
        let quiet = now.wrapping_sub(self.watch_from);
        self.watch_from = now;
        if !self.connection_has_sample {
            self.connection_has_sample = true;
            self.connection_first_sample = now;
        }
        if self.start_pending {
            // The stream is on: no command needed.
            self.start_pending = false;
            self.stats.starts_skipped += 1;
        }
        if self.outage {
            self.begin_after_outage(now);
        }
        if self.announce_calibrating {
            self.announce_calibrating = false;
            self.log("luma: calibrating");
        }

        let steady = self.steady_time(sample, now);
        let estimator = self
            .estimator
            .as_mut()
            .expect("run() makes the estimator first");
        let mut pose = estimator.update(&steady);
        if !pose.calibrated {
            if self.calibrated {
                // The estimator gave up its calibration (a new one is its call, not ours).
                self.calibrated = false;
                self.announced = false;
                self.log("luma: calibrating");
                self.set_stale(true);
            }
            return; // a pose from before calibration would swing the desktops about: the feed holds until it is ready
        }
        self.calibrated = true;
        if !finite(&pose.q) || pose.t_ns <= self.last_published_t {
            self.stats.bad_poses += 1;
            return;
        }
        self.carry_heading(&mut pose);
        if !self.announced {
            self.announced = true;
            let line = if self.heading_kept {
                if self.restored {
                    "luma: calibrated restored=1 heading=kept"
                } else {
                    "luma: calibrated restored=0 heading=kept"
                }
            } else if self.restored {
                "luma: calibrated restored=1"
            } else {
                "luma: calibrated restored=0"
            };
            self.log(line);
            self.restored = false;
            self.heading_kept = false;
        } else if was_quiet {
            self.log(&format!("luma: fresh gap_ms={}", quiet / MS));
        }
        self.set_stale(false);
        self.parts.sink.publish(&pose);
        self.last_published_t = pose.t_ns;
        self.last_q = Some(pose.q);
        self.stats.published += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn back_off_doubles_to_its_cap() {
        let ms = Duration::from_millis;
        assert_eq!(doubled(ms(200), 0, ms(3200)), ms(200));
        assert_eq!(doubled(ms(200), 3, ms(3200)), ms(1600));
        assert_eq!(doubled(ms(200), 4, ms(3200)), ms(3200));
        assert_eq!(doubled(ms(200), 40, ms(3200)), ms(3200));
        assert_eq!(doubled(ms(2000), 3, ms(8000)), ms(8000));
    }

    #[test]
    fn heading_is_none_looking_straight_up() {
        let up = Quatf {
            w: std::f32::consts::FRAC_PI_4.cos(),
            x: 0.0,
            y: -std::f32::consts::FRAC_PI_4.sin(),
            z: 0.0,
        };
        assert!(heading_of(&up).is_none());
        let yawed = Quatf {
            w: 0.35_f32.cos(),
            x: 0.0,
            y: 0.0,
            z: 0.35_f32.sin(),
        };
        assert!((heading_of(&yawed).unwrap() - 0.7).abs() < 1e-5);
    }
}
