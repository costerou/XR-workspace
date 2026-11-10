//! Luma Ultra head-tracking driver over raw usbdevfs. A port of `spike/driver/src/main.cpp`: the same options, exit codes
//! and log lines. Exit codes: 0 stopped, 2 usage or feature flag, 3 replay file, 4 feed or log file, 5 another driver.
//!
//! Refused for now, rather than run differently from the C++ driver (spike/driver/docs/acceptance.md S7: an optional
//! feature sends nothing unless asked): `--feature-*` and `--latency-log`. Not done yet: the saved gyro bias (the
//! calibration task), the VsyncState feed and PredictionWire's motion state. See `src/wiring.rs`.

use std::cell::Cell;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use xr_luma_driver::capture::{ReplayLink, load_capture};
use xr_luma_driver::daemon::{Daemon, DaemonParts, Estimator, Link, Sink};
use xr_luma_driver::fusion::{Fusion, FusionConfig};
use xr_luma_driver::instance::{log_line, take_instance_lock};
use xr_luma_driver::options::{LIVE_IPC_PREFIX, Start, USAGE, parse_options};
use xr_luma_driver::protocol::LumaDecoder;
use xr_luma_driver::rt::{RealSysOps, ThreadPolicy, apply_thread_policy};
use xr_luma_driver::transport::Transport;
use xr_luma_driver::wiring::{self, PoseLog, TransportLink};

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}

fn fail(code: i32, reason: &str) -> i32 {
    log_line(&format!("luma: error reason={reason}"));
    code
}

fn monotonic_ns() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: clock_gettime writes one timespec through a valid pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// No SA_RESTART: a signal ends a blocking read at once. SIGPIPE: stderr's reader going away must not end tracking.
fn install_signals() {
    // SAFETY: the handler only stores to an atomic, which is async-signal-safe; the sigaction is fully initialised.
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
        libc::sigemptyset(&mut action.sa_mask);
        for sig in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
            libc::sigaction(sig, &action, std::ptr::null_mut());
        }
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
}

fn main() {
    std::process::exit(run());
}

fn run() -> i32 {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let options = match parse_options(&args) {
        Ok(o) => o,
        Err(why) => {
            eprint!("luma: error reason=usage {why}\n{USAGE}");
            return 2;
        }
    };
    if options.help {
        print!("{USAGE}");
        return 0;
    }
    install_signals();

    let _lock = match take_instance_lock(&options.ipc_prefix) {
        Ok(file) => file,
        Err(refused) => return fail(5, &refused),
    };
    if let Some(flag) = options.feature_flags.first() {
        return fail(2, &format!("feature {flag} is not in the Rust driver yet"));
    }
    if options.latency_log.is_some() {
        return fail(4, "latency-log is not in the Rust driver yet");
    }

    // Live only. It helps when it can and costs nothing when it cannot: acceptance P3, works without privileges.
    if options.replay.is_none() && !options.no_rt {
        let result = apply_thread_policy(&ThreadPolicy::default(), &RealSysOps);
        if !result.detail.is_empty() {
            log_line(&format!("luma: scheduling {}", result.detail));
        }
    }

    let mut replay_clock: Option<Rc<Cell<u64>>> = None;
    let mut link: Box<dyn Link> = match &options.replay {
        Some(path) => match load_capture(std::path::Path::new(path)) {
            Ok(records) => {
                let replay = ReplayLink::new(records, options.replay_realtime);
                replay_clock = Some(replay.clock());
                Box::new(replay)
            }
            Err(why) => return fail(3, &format!("replay {why}")),
        },
        None => Box::new(TransportLink::new(
            Transport::new(wiring::live_config(&options)),
            Box::new(log_line),
        )),
    };

    let sink = match wiring::feed_sink(&options.ipc_prefix, options.ipc_prefix != LIVE_IPC_PREFIX) {
        Ok(s) => s,
        Err(why) => return fail(4, &format!("feed {why}")),
    };
    let mut sink: Box<dyn Sink> = match &options.pose_log {
        Some(path) => match PoseLog::create(sink, path, options.pose_log_every) {
            Ok(s) => Box::new(s),
            Err(why) => return fail(4, &why),
        },
        None => Box::new(sink),
    };

    let mut decoder = LumaDecoder::default();
    let now_ns: Box<dyn Fn() -> u64> = match replay_clock {
        Some(clock) => Box::new(move || clock.get()),
        None => Box::new(monotonic_ns),
    };
    let stop_command = if options.stop_command && options.start != Start::Listen {
        wiring::stop_command()
    } else {
        Vec::new()
    };
    let parts = DaemonParts {
        link: link.as_mut(),
        decoder: &mut decoder,
        make_estimator: Box::new(|| Box::new(Fusion::new(FusionConfig::default())) as Box<dyn Estimator>),
        sink: sink.as_mut(),
        now_ns,
        log: Box::new(log_line),
        start_command: wiring::start_for(options.start),
        stop_command,
        before_start: wiring::before_start(options.start),
        on_link: None,
        on_tick: None,
    };
    let mut daemon = Daemon::new(parts, options.config.clone());
    daemon.run(&STOP);
    if options.replay.is_some() {
        let times = daemon.process_times();
        let us = |ns: u64| ns as f64 / 1000.0;
        log_line(&format!(
            "luma: replay process_us p50={:.2} p99={:.2} p999={:.2} max={:.2} packets={}",
            us(times.percentile(0.5)),
            us(times.percentile(0.99)),
            us(times.percentile(0.999)),
            us(times.max()),
            times.count()
        ));
    }
    0
}
