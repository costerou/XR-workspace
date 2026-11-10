//! `LumaWatch` (`tracking.py`): brings a crashed Luma driver back during a session, at most
//! `RESTARTS` times in `RESTART_WINDOW_S`, then says so.
//!
//! Only for a crash: `Tracking::stop` removes the state file and a swap changes its driver, and
//! either ends the watch. The renderer holds its last pose meanwhile and picks up the feed's
//! segments again, which a new driver reuses.

use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use super::tracking::{Kind, Tracking, Want};

/// A crashed driver comes back this many times in this many seconds. Past that it is not a one-off.
pub const RESTARTS: usize = 3;
pub const RESTART_WINDOW_S: f64 = 300.0;
pub const POLL: Duration = Duration::from_millis(500);

/// The restarts in the last `RESTART_WINDOW_S`.
#[derive(Debug, Default)]
pub struct RestartBudget {
    at: Vec<f64>,
}

impl RestartBudget {
    /// A crash at `now` (seconds): `Some(n)` for restart n of `RESTARTS`, `None` once past the limit.
    /// A restart exactly `RESTART_WINDOW_S` ago no longer counts.
    pub fn spend(&mut self, now: f64) -> Option<usize> {
        self.at.retain(|&at| now - at < RESTART_WINDOW_S);
        self.at.push(now);
        (self.at.len() <= RESTARTS).then_some(self.at.len())
    }
}

/// What one look at the state file found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Seen {
    /// No record, or another driver's: a stop or a swap.
    Gone,
    Alive,
    Crashed,
}

/// How a watch ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum End {
    Finished,
    Gone,
    GaveUp,
    RestartFailed(String),
}

pub fn crash_message(log: &str) -> String {
    format!("The XR Luma driver keeps crashing, so head tracking has stopped for this session. See {log}.")
}

pub fn restart_failed_message(log: &str) -> String {
    format!("The XR Luma driver keeps crashing and could not restart, so head tracking has stopped for this session. See {log}.")
}

/// The watch loop, every effect passed in. `stop` ends it when a message arrives or its sender is
/// dropped; `clock` is seconds; `log` is named in the messages.
pub fn watch(
    stop: &Receiver<()>,
    poll: Duration,
    mut clock: impl FnMut() -> f64,
    mut see: impl FnMut() -> Seen,
    mut restart: impl FnMut() -> Result<(), String>,
    mut notify: impl FnMut(&str),
    log: &str,
) -> End {
    let mut budget = RestartBudget::default();
    loop {
        match stop.recv_timeout(poll) {
            Err(RecvTimeoutError::Timeout) => {}
            _ => return End::Finished,
        }
        match see() {
            Seen::Gone => return End::Gone,
            Seen::Alive => continue,
            Seen::Crashed => {}
        }
        let Some(n) = budget.spend(clock()) else {
            notify(&crash_message(log));
            return End::GaveUp;
        };
        eprintln!("xr-workspace: the XR Luma driver crashed; restart {n} of {RESTARTS}");
        if let Err(e) = restart() {
            eprintln!("xr-workspace: the restart failed: {e}");
            // start() removes its record after a failure. Without saying so here, the next look
            // would take that for a deliberate stop and end without telling anyone.
            notify(&restart_failed_message(log));
            return End::RestartFailed(e);
        }
    }
}

/// The watch on its own thread.
pub struct LumaWatch {
    stop: Option<Sender<()>>,
    done: Receiver<()>,
    thread: Option<JoinHandle<End>>,
}

impl LumaWatch {
    pub fn spawn(tracking: Tracking, luma: PathBuf, notify: impl FnMut(&str) + Send + 'static, poll: Duration) -> Self {
        let (stop_tx, stop_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel::<()>();
        let thread = std::thread::Builder::new()
            .name("luma-watch".into())
            .spawn(move || {
                let _done = done_tx; // dropped when the watch ends, which wakes finish()
                let begun = Instant::now();
                let log = tracking.paths.luma_log.display().to_string();
                let see = || match tracking.read_state() {
                    Some(r) if r.kind() == Kind::Luma => {
                        if Tracking::ours(Some(&r)) { Seen::Alive } else { Seen::Crashed }
                    }
                    _ => Seen::Gone,
                };
                let restart = || {
                    tracking
                        .ensure_ready(&Want::Luma(luma.clone()), tracking.timing.luma_timeout)
                        .map(drop)
                        .map_err(|e| e.to_string())
                };
                watch(&stop_rx, poll, || begun.elapsed().as_secs_f64(), see, restart, notify, &log)
            })
            .expect("spawn luma-watch");
        LumaWatch { stop: Some(stop_tx), done: done_rx, thread: Some(thread) }
    }

    /// End the watch. A restart in progress gets a second to finish and is otherwise left to it:
    /// the driver outlives a run anyway. Returns how it ended, if it did.
    pub fn finish(mut self) -> Option<End> {
        drop(self.stop.take());
        match self.done.recv_timeout(Duration::from_secs(1)) {
            Err(RecvTimeoutError::Timeout) => None,
            _ => self.thread.take().and_then(|t| t.join().ok()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    #[test]
    fn three_restarts_in_five_minutes_then_it_gives_up() {
        let mut b = RestartBudget::default();
        assert_eq!(b.spend(0.0), Some(1));
        assert_eq!(b.spend(100.0), Some(2));
        assert_eq!(b.spend(299.9), Some(3));
        assert_eq!(b.spend(299.95), None);
    }

    #[test]
    fn a_restart_five_minutes_old_no_longer_counts() {
        let mut b = RestartBudget::default();
        for t in [0.0, 1.0, 2.0] {
            b.spend(t);
        }
        // 300 s after the first: it has left the window, so this is restart 3 again (1, 2 and now).
        assert_eq!(b.spend(300.0), Some(3));
        // Spaced 150 s apart there is never more than two in any window.
        let mut b = RestartBudget::default();
        for i in 0..20 {
            assert!(b.spend(f64::from(i) * 150.0).is_some_and(|n| n <= 2));
        }
    }

    fn run(seen: Vec<Seen>, times: Vec<f64>, restart_ok: bool) -> (End, usize, Vec<String>) {
        let (tx, rx) = mpsc::channel::<()>();
        let seen = RefCell::new(seen.into_iter());
        let times = RefCell::new(times.into_iter());
        let restarts = Cell::new(0);
        let notes = RefCell::new(Vec::new());
        let end = watch(
            &rx,
            Duration::ZERO,
            || times.borrow_mut().next().expect("clock"),
            || seen.borrow_mut().next().unwrap_or(Seen::Gone),
            || {
                restarts.set(restarts.get() + 1);
                if restart_ok { Ok(()) } else { Err("startup crash".into()) }
            },
            |m| notes.borrow_mut().push(m.to_string()),
            "/log",
        );
        drop(tx);
        (end, restarts.get(), notes.into_inner())
    }

    #[test]
    fn the_watch_restarts_three_times_then_says_so() {
        let crashes = vec![Seen::Crashed, Seen::Alive, Seen::Crashed, Seen::Crashed, Seen::Crashed];
        let (end, restarts, notes) = run(crashes, vec![0.0, 10.0, 20.0, 30.0], true);
        assert_eq!(end, End::GaveUp);
        assert_eq!(restarts, 3);
        assert_eq!(notes, [crash_message("/log")]);
    }

    #[test]
    fn crashes_spread_over_more_than_five_minutes_keep_coming_back() {
        let crashes = vec![Seen::Crashed; 6];
        let (end, restarts, notes) = run(crashes, vec![0.0, 200.0, 400.0, 600.0, 800.0, 1000.0], true);
        assert_eq!(end, End::Gone);
        assert_eq!(restarts, 6);
        assert!(notes.is_empty());
    }

    #[test]
    fn a_failed_restart_is_reported_not_mistaken_for_a_stop() {
        let (end, restarts, notes) = run(vec![Seen::Crashed], vec![0.0], false);
        assert_eq!(end, End::RestartFailed("startup crash".into()));
        assert_eq!(restarts, 1);
        assert_eq!(notes, [restart_failed_message("/log")]);
    }

    #[test]
    fn a_stop_or_swap_ends_the_watch_quietly() {
        let (end, restarts, notes) = run(vec![Seen::Alive, Seen::Gone], vec![], true);
        assert_eq!((end, restarts, notes.len()), (End::Gone, 0, 0));
    }

    #[test]
    fn finish_ends_a_waiting_watch() {
        let (tx, rx) = mpsc::channel::<()>();
        drop(tx);
        let end = watch(&rx, Duration::from_secs(60), || 0.0, || Seen::Crashed, || Ok(()), |_| {}, "/log");
        assert_eq!(end, End::Finished);
    }
}
