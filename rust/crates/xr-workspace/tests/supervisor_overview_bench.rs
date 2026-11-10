//! RR-64's CPU bench: the supervisor process with the overview open, the real sampler thread on a fake compositor,
//! and the run loop hosting the pointer through `supervisor::hook`. Ignored (it runs for tens of seconds and spawns
//! Python fakes); run it in release:
//!
//! ```text
//! cd rust && TMPDIR=/tmp cargo test --release -p xr-workspace --test supervisor_overview_bench -- \
//!     --ignored --nocapture --test-threads=1
//! ```
//!
//! `XR_BENCH_SECONDS` sets the measured window (default 10 s, after a 1 s warm-up).
//!
//! The fakes are `tools/native_spike/bench_control.py`'s, the ones its `overview` bench uses, run as their own
//! processes so their CPU is not the supervisor's: `FAKE_HYPRLAND` in `oscillate` mode with a 0.02 s period (the cursor
//! moves 10 px every 20 ms; Hyprland's request socket, one request per connection) under an interpreter copy named
//! `Hyprland` in a private runtime directory, and `FAKE_RENDERER` sending 120 Hz heartbeats and reading every frame.
//! Nothing touches the live compositor: the client is given the fake's instance and runtime directory explicitly.
//!
//! The pointer is a stand-in until the overview pointer's port lands: in the overview it turns each sample that moved
//! the cursor into one `overview_pointer` frame (what `OverviewPointer.feed` and `poll` do for the dot), signalling the
//! loop's wake fd from `feed`. Its want is constant, so the sampler makes no binds or warps.
//!
//! Measured: the whole process's CPU (`getrusage(RUSAGE_SELF)`, user + system) over the window, as % of one core;
//! per thread from `/proc/self/task/*/schedstat` (the loop thread and `xr-sampler`).

use std::fs;
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use xr_workspace::hyprland;
use xr_workspace::resize::OutputRect;
use xr_workspace::supervisor::sampler::live::Live;
use xr_workspace::supervisor::sampler::{
    Mode, Monotonic, PanPlan, SAMPLE_S, SampledPointer, Sampler, Side, Variant, Want, run,
};
use xr_workspace::supervisor::{LoopHook, POLL_INTERVAL, Renderer, Supervisor, WakeFd};

const INSTANCE: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_1700000000_1";
const NAMES: [&str; 3] = ["XR-1", "XR-2", "XR-3"];
const PYTHON: &str = "/usr/bin/python3";
const WARMUP: Duration = Duration::from_secs(1);

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap()
}

/// `bench_control.<name>` run with the argv after it, `extra` appended (Python source, evaluated with `b` the module).
fn fake_script(name: &str, extra: &str) -> String {
    format!(
        "import sys, json; sys.path.insert(0, {repo:?}); from tools.native_spike import bench_control as b; \
         sys.argv += {extra}; exec(b.{name})",
        repo = repo().to_str().unwrap()
    )
}

/// A private directory under TMPDIR (or /tmp), removed on drop, holding the fake runtime directory.
struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let base = std::env::var_os("TMPDIR").map_or_else(|| PathBuf::from("/tmp"), PathBuf::from);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .subsec_nanos();
        let dir = base.join(format!("xrb-{}-{nanos:x}", std::process::id()));
        fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
        Scratch(dir)
    }
    fn runtime(&self) -> PathBuf {
        self.0.join("run")
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// `bench_control.FakeCompositor('oscillate', 0.02)`, as its own process.
struct Compositor {
    proc: Child,
    socket: PathBuf,
}

impl Compositor {
    fn start(scratch: &Scratch) -> Self {
        let runtime = scratch.runtime();
        for dir in [
            runtime.clone(),
            runtime.join("hypr"),
            runtime.join("hypr").join(INSTANCE),
        ] {
            fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
        }
        let directory = runtime.join("hypr").join(INSTANCE);
        // The client checks the peer's executable is named Hyprland (as FakeCompositor does: an interpreter copy).
        let exe = scratch.0.join("Hyprland");
        fs::copy(fs::canonicalize(PYTHON).unwrap(), &exe).unwrap();
        fs::set_permissions(&exe, fs::Permissions::from_mode(0o700)).unwrap();
        let home = Command::new(PYTHON)
            .args(["-I", "-c", "import sys; print(sys.base_prefix)"])
            .output()
            .unwrap();
        let home = String::from_utf8(home.stdout).unwrap().trim().to_string();
        let proc = Command::new(&exe)
            .env("PYTHONHOME", home)
            .arg("-I")
            .arg("-c")
            .arg(fake_script("FAKE_HYPRLAND", "[json.dumps(b.MONITORS)]"))
            .arg(&directory)
            .args(["oscillate", "0.02"])
            .stdin(Stdio::null())
            .spawn()
            .unwrap();
        let socket = directory.join(".socket.sock");
        let deadline = Instant::now() + Duration::from_secs(10);
        while !socket.exists() {
            assert!(
                Instant::now() < deadline,
                "the fake compositor did not start"
            );
            thread::sleep(Duration::from_millis(10));
        }
        Compositor { proc, socket }
    }

    /// The fake's request counts (its `stats` request).
    fn counts(&self) -> serde_json::Value {
        let mut s = UnixStream::connect(&self.socket).unwrap();
        s.write_all(b"stats").unwrap();
        let mut data = Vec::new();
        s.read_to_end(&mut data).unwrap();
        serde_json::from_slice::<serde_json::Value>(&data).unwrap()["counts"].clone()
    }
}

impl Drop for Compositor {
    fn drop(&mut self) {
        let _ = self.proc.kill();
        let _ = self.proc.wait();
    }
}

/// The fake renderer's process, as the supervisor sees it.
struct Proc(Child);

impl Renderer for Proc {
    fn poll(&mut self) -> Option<ExitStatus> {
        self.0.try_wait().ok().flatten()
    }
    fn terminate(&mut self) {
        unsafe { libc::kill(self.0.id() as i32, libc::SIGTERM) };
    }
    fn kill(&mut self) {
        let _ = self.0.kill();
    }
}

fn start_renderer(out: &Path) -> (UnixStream, Child) {
    let (parent, child) = UnixStream::pair().unwrap();
    let fd = child.as_raw_fd();
    let mut cmd = Command::new(PYTHON);
    cmd.arg("-I")
        .arg("-c")
        .arg(fake_script("FAKE_RENDERER", "[]"))
        .arg("3")
        .arg(out)
        .arg("120")
        .stdin(Stdio::null());
    // SAFETY: dup2 is async-signal-safe; fd 3 in the child is the renderer's end (not close-on-exec).
    unsafe {
        cmd.pre_exec(move || {
            if libc::dup2(fd, 3) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let proc = cmd.spawn().unwrap();
    drop(child);
    (parent, proc)
}

struct NoPlan;

impl PanPlan for NoPlan {
    type Frame = ();
    fn home(&self) -> i64 {
        0
    }
    fn target(&self) -> i64 {
        0
    }
    fn side(&self) -> Side {
        Side::Left
    }
    fn exit(&self) -> f64 {
        0.0
    }
    fn entry(&self) -> f64 {
        0.0
    }
    fn total_px(&self) -> f64 {
        0.0
    }
    fn frame(&self, _: f64) {}
}

type Point = Option<(i64, i64)>;

/// What the stand-in pointer's two sides share.
#[derive(Default)]
struct Dot {
    open: AtomicBool,
    /// The newest cursor fed that the loop has not drawn yet, and the last one fed.
    pending: Mutex<(Point, Point)>,
    fed: AtomicU64,
    moved: AtomicU64,
    frames: AtomicU64,
}

/// The sampler's side: in the overview, a sample that moved the cursor is queued for the loop and signals it.
struct Pointer {
    dot: Arc<Dot>,
    wake: Arc<WakeFd>,
}

impl SampledPointer for Pointer {
    type Plan = NoPlan;
    fn overview(&mut self) -> bool {
        self.dot.open.load(Ordering::SeqCst)
    }
    fn restore_sampling(&mut self) -> bool {
        false
    }
    fn restore_release(&mut self) {}
    fn want(&mut self) -> Want {
        Want {
            serial: 0,
            kind: Mode::Away,
            point: None,
        }
    }
    fn binds_dropped(&mut self) -> bool {
        false
    }
    fn clear_binds_dropped(&mut self) {}
    fn mode(&mut self) -> Mode {
        Mode::Away
    }
    fn feed(&mut self, cursor: Option<(i64, i64)>, _: (f64, f64), _: &[OutputRect], _: i64) {
        self.dot.fed.fetch_add(1, Ordering::Relaxed);
        if !self.dot.open.load(Ordering::SeqCst) || cursor.is_none() {
            return;
        }
        let mut p = self.dot.pending.lock().unwrap();
        if p.1 != cursor {
            p.1 = cursor;
            p.0 = cursor;
            drop(p);
            self.dot.moved.fetch_add(1, Ordering::Relaxed);
            self.wake.signal();
        }
    }
    fn handles_on(&mut self) -> bool {
        false
    }
    fn sync(&mut self) -> i64 {
        0
    }
    fn shape_gated(&mut self) -> bool {
        false
    }
    fn shape_pushed(&mut self) -> bool {
        false
    }
    fn shape_serial(&mut self) -> i64 {
        0
    }
    fn feed_shape(&mut self, _: Option<&str>, _: i64) {}
    fn set_shape_wired(&mut self, _: bool) {}
    fn reloads(&mut self) -> i64 {
        0
    }
    fn selected(&mut self) -> i64 {
        0
    }
    fn set_pan(&mut self, _: Option<&()>) {}
    fn request_switch(&mut self, _: i64, _: i64, _: bool) {}
    fn pan_plan(&mut self, _: i64, _: Side, _: f64, _: &OutputRect) -> Option<NoPlan> {
        None
    }
    fn neighbour(&mut self, _: Side, _: f64) -> Option<i64> {
        None
    }
    fn can_switch(&mut self) -> bool {
        false
    }
    fn can_scroll(&mut self) -> bool {
        false
    }
}

/// The loop's side: one `overview_pointer` frame per queued cursor.
struct Hook {
    dot: Arc<Dot>,
    wake: Arc<WakeFd>,
}

impl LoopHook for Hook {
    fn wake_fd(&self) -> &WakeFd {
        &self.wake
    }
    fn poll(&mut self, _: Instant, out: &mut Vec<u8>) {
        if let Some((x, y)) = self.dot.pending.lock().unwrap().0.take() {
            writeln!(
                out,
                r#"{{"op":"overview_pointer","payload":{{"x":{x},"y":{y},"visible":true}}}}"#
            )
            .unwrap();
            self.dot.frames.fetch_add(1, Ordering::Relaxed);
        }
    }
}

fn process_cpu() -> Duration {
    let mut r: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut r) };
    let tv = |t: libc::timeval| Duration::new(t.tv_sec as u64, t.tv_usec as u32 * 1000);
    tv(r.ru_utime) + tv(r.ru_stime)
}

/// One thread's time on a CPU (`schedstat`'s first field, ns).
fn thread_cpu(tid: i32) -> Duration {
    let text = fs::read_to_string(format!("/proc/self/task/{tid}/schedstat")).unwrap_or_default();
    Duration::from_nanos(
        text.split_whitespace()
            .next()
            .and_then(|v| v.parse().ok())
            .unwrap_or(0),
    )
}

fn tid_named(name: &str) -> Option<i32> {
    fs::read_dir("/proc/self/task")
        .ok()?
        .flatten()
        .find_map(|e| {
            let comm = fs::read_to_string(e.path().join("comm")).ok()?;
            (comm.trim() == name)
                .then(|| e.file_name().to_str()?.parse().ok())
                .flatten()
        })
}

struct Marks {
    at: Instant,
    process: Duration,
    looping: Duration,
    sampler: Duration,
    samples: u64,
    passes: u64,
}

#[derive(Debug)]
struct Report {
    seconds: f64,
    process_pct: f64,
    loop_pct: f64,
    sampler_pct: f64,
    samples_per_s: f64,
    frames_per_s: f64,
    loop_passes_per_s: f64,
    renderer_frames: u64,
    frames_sent: u64,
    cursor_requests: u64,
    other_requests: serde_json::Value,
}

fn window() -> Duration {
    let s = std::env::var("XR_BENCH_SECONDS")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(10.0);
    Duration::from_secs_f64(s)
}

fn bench(variant: Variant, open: bool) -> Report {
    let scratch = Scratch::new();
    let fake = Compositor::start(&scratch);
    let out = scratch.0.join("renderer.json");
    let (parent, renderer) = start_renderer(&out);

    let dot = Arc::new(Dot::default());
    dot.open.store(open, Ordering::SeqCst);
    let wake = Arc::new(WakeFd::new().unwrap());
    let pointer = Pointer {
        dot: Arc::clone(&dot),
        wake: Arc::clone(&wake),
    };
    let mut hook = Hook {
        dot: Arc::clone(&dot),
        wake,
    };
    let client = hyprland::Client::new(INSTANCE, &scratch.runtime()).unwrap();
    let bind_args = ["python", "client", "control.sock", "bench", "session"]
        .map(String::from)
        .to_vec();
    let sampler = Sampler::new(
        variant,
        Live::new(client),
        NAMES.map(String::from).to_vec(),
        pointer,
        bind_args,
        SAMPLE_S,
        None,
        Monotonic,
    );
    let handle = run::start(sampler);
    let framed = Arc::new(AtomicU64::new(0));

    let interrupt = Arc::new(AtomicBool::new(false));
    let loop_tid = unsafe { libc::gettid() };
    let marks: Arc<Mutex<Vec<Marks>>> = Arc::default();
    let mut watchdog = Supervisor::new(Proc(renderer), |_| (), Instant::now());
    let passes = Arc::new(AtomicU64::new(0));
    let timer = {
        let (interrupt, marks, dot) =
            (Arc::clone(&interrupt), Arc::clone(&marks), Arc::clone(&dot));
        let (framed, passes) = (Arc::clone(&framed), Arc::clone(&passes));
        thread::spawn(move || {
            let sampler_tid = tid_named("xr-sampler").expect("the sampler thread");
            let mark = || Marks {
                at: Instant::now(),
                process: process_cpu(),
                looping: thread_cpu(loop_tid),
                sampler: thread_cpu(sampler_tid),
                samples: dot.fed.load(Ordering::Relaxed),
                passes: passes.load(Ordering::Relaxed),
            };
            thread::sleep(WARMUP);
            let start = mark();
            let frames0 = dot.frames.load(Ordering::Relaxed);
            thread::sleep(window());
            let end = mark();
            framed.store(
                dot.frames.load(Ordering::Relaxed) - frames0,
                Ordering::Relaxed,
            );
            marks.lock().unwrap().extend([start, end]);
            interrupt.store(true, Ordering::SeqCst);
        })
    };
    // The loop's pass count is read by the timer thread through `passes`: a small hook wrapper keeps it current.
    struct Counting<'a> {
        inner: &'a mut Hook,
        passes: &'a AtomicU64,
    }
    impl LoopHook for Counting<'_> {
        fn wake_fd(&self) -> &WakeFd {
            self.inner.wake_fd()
        }
        fn deadline(&mut self) -> Option<Instant> {
            // Read once per pass, before the wait: counts the passes without changing them.
            self.passes.fetch_add(1, Ordering::Relaxed);
            None
        }
        fn poll(&mut self, now: Instant, out: &mut Vec<u8>) {
            self.inner.poll(now, out)
        }
    }
    let mut counting = Counting {
        inner: &mut hook,
        passes: &passes,
    };
    watchdog
        .run_hosting(&parent, None, &mut counting, &interrupt, POLL_INTERVAL)
        .unwrap();
    timer.join().unwrap();
    let sampler = handle.stop().expect("the sampler thread ended cleanly");
    let counts = fake.counts();
    drop(fake);
    let record: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&out).expect("the renderer's record")).unwrap();
    let renderer_frames = record["counts"]["overview_pointer"].as_u64().unwrap_or(0);

    let m = marks.lock().unwrap();
    let (a, b) = (&m[0], &m[1]);
    let secs = (b.at - a.at).as_secs_f64();
    let pct = |x: Duration, y: Duration| (x - y).as_secs_f64() / secs * 100.0;
    let report = Report {
        seconds: secs,
        process_pct: pct(b.process, a.process),
        loop_pct: pct(b.looping, a.looping),
        sampler_pct: pct(b.sampler, a.sampler),
        samples_per_s: (b.samples - a.samples) as f64 / secs,
        frames_per_s: framed.load(Ordering::Relaxed) as f64 / secs,
        loop_passes_per_s: (b.passes - a.passes) as f64 / secs,
        renderer_frames,
        frames_sent: dot.frames.load(Ordering::Relaxed),
        cursor_requests: counts["j/cursorpos"].as_u64().unwrap_or(0),
        other_requests: counts,
    };
    println!(
        "{variant:?} overview {}: process {:.3} % of a core over {:.1} s (loop thread {:.3} %, sampler thread {:.3} %); \
         {:.1} samples fed/s, {:.1} frames/s, {:.1} loop passes/s; frames sent {} / renderer read {}; sampler {} samples, \
         {} errors; fake compositor: {} cursorpos requests in all, {}",
        if open { "open" } else { "closed" },
        report.process_pct,
        report.seconds,
        report.loop_pct,
        report.sampler_pct,
        report.samples_per_s,
        report.frames_per_s,
        report.loop_passes_per_s,
        report.frames_sent,
        report.renderer_frames,
        sampler.samples,
        sampler.errors,
        report.cursor_requests,
        report.other_requests,
    );
    assert_eq!(
        watchdog.stop_reason,
        Some(xr_workspace::supervisor::StopReason::Interrupted)
    );
    report
}

#[test]
#[ignore = "CPU bench: tens of seconds, spawns the Python fakes; run in --release"]
fn overview_open_costs_at_most_one_percent_of_a_core() {
    let r = bench(Variant::Unified, true);
    assert!(
        r.samples_per_s > 150.0,
        "the sampler ran at its 250 Hz: {r:?}"
    );
    assert!(
        r.frames_per_s > 30.0,
        "one frame per cursor move (50 a second): {r:?}"
    );
    assert_eq!(
        r.renderer_frames, r.frames_sent,
        "every frame reached the renderer"
    );
    assert!(
        r.process_pct <= 1.0,
        "{:.3} % of a core with the overview open",
        r.process_pct
    );
}

#[test]
#[ignore = "CPU bench: tens of seconds, spawns the Python fakes; run in --release"]
fn overview_closed_unified_cursor_on_an_xr_monitor() {
    // The unified sampler keeps the cursor on home outside the overview: with the cursor on XR-1 it samples on.
    let r = bench(Variant::Unified, false);
    assert_eq!(r.frames_sent, 0);
    assert!(r.process_pct <= 1.0, "{:.3} % of a core", r.process_pct);
}

#[test]
#[ignore = "CPU bench: tens of seconds, spawns the Python fakes; run in --release"]
fn overview_closed_locked_sampler_blocks() {
    let r = bench(Variant::Locked, false);
    assert_eq!(r.frames_sent, 0);
    assert!(
        r.samples_per_s < 1.0,
        "a closed overview is not sampled: {r:?}"
    );
    assert!(r.process_pct <= 1.0, "{:.3} % of a core", r.process_pct);
}
