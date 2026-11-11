//! Port of `spike/include/xr/late_latch.hpp` and `spike/src/late_latch.cpp` (RR-72).
//!
//! Late latch (D4, docs/rust-rebuild-plan.md): a head-tracked frame is drawn as late as the compositor's next latch
//! allows, so the pose it reads is that much younger when the frame is shown. Off unless a guard is given
//! (`--late-latch-guard-ms`).
//!
//! The compositor sends our frame callback when it takes our last commit for the next refresh (its latch point), so the
//! next latch is one refresh period after that callback. Instead of drawing at the callback (about one period of
//! waiting in front of the latch), the renderer waits until
//!     target = callback + period - margin,   margin = the slowest draw of the last `COST_WINDOW` frames + guard + boost
//! and then reads the pose and draws. "Draw" is from the target (or the draw start when it was later) to the commit, so
//! a late wake-up counts against the margin too. Before any draw is measured the margin is the widest one (three
//! quarters of a period), so the first frames start early instead of guessing.
//!
//! A late frame is worse than a slow one, so the margin adapts and never probes downwards on purpose: a frame drawn on
//! this schedule whose own callback comes back half a period or more after the one it aimed for (the callback it
//! followed + a period) missed its latch (it is shown one refresh late, and the refresh before it repeats the old
//! frame). Each miss adds `MISS_BOOST_MS` to the margin, up to `MAX_BOOST_MS`; the boost only drains
//! (`BOOST_DECAY_MS_PER_S`) after `BOOST_QUIET_S` without a miss, so the margin never goes below the slowest recent
//! draw plus the guard. The guard is the one tuning value: what the compositor needs between our commit and its latch
//! beyond our own draw (its own latch-to-callback delay, the GPU finishing our frame).
//!
//! Pure: times are CLOCK_MONOTONIC ns passed in, no system calls and no allocation. Unsigned arithmetic wraps as the
//! C++ `std::uint64_t` does; `std::min` / `std::max` are mirrored by [`min`] / [`max`].

use std::ffi::CString;

const NS_PER_MS: f64 = 1e6;

fn ms(ns: u64) -> f64 {
    ns as f64 / NS_PER_MS
}

/// `std::min(a, b)`: `b < a ? b : a`.
fn min<T: PartialOrd>(a: T, b: T) -> T {
    if b < a { b } else { a }
}

/// `std::max(a, b)`: `a < b ? b : a`.
fn max<T: PartialOrd>(a: T, b: T) -> T {
    if a < b { b } else { a }
}

fn percentile(buckets: &[u64; LateLatch::SLACK_BUCKETS], p: f64) -> f64 {
    let mut total: u64 = 0;
    for &n in buckets {
        total = total.wrapping_add(n);
    }
    if total == 0 {
        return 0.0;
    }
    let want = (p * total as f64).ceil() as u64;
    let mut seen: u64 = 0;
    for (i, &n) in buckets.iter().enumerate() {
        seen = seen.wrapping_add(n);
        if seen >= max(want, 1) {
            return (i as f64 + 0.5) * LateLatch::SLACK_BUCKET_MS;
        }
    }
    (buckets.len() as f64 - 0.5) * LateLatch::SLACK_BUCKET_MS
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct LateLatchSummary {
    pub enabled: bool,
    pub guard_ms: f64,
    pub margin_ms: f64,
    pub draw_max_ms: f64,
    pub boost_ms: f64,
    /// Frames drawn on the late-latch schedule (a fresh frame callback before them).
    pub scheduled: u64,
    /// Of those, frames that had time left to wait for.
    pub waited: u64,
    /// ... whose callback came back in time: they made the latch they aimed at.
    pub latched: u64,
    /// ... whose callback came back half a period or more after the one aimed for.
    pub missed: u64,
    /// Frames drawn with no fresh callback (the first, after an idle view): drawn at once.
    pub immediate: u64,
    /// Commit to callback on latched frames: the room left (0.1 ms buckets).
    pub slack_p5_ms: f64,
    pub slack_p50_ms: f64,
    /// Every scheduled frame's draw (what the margin covers), whole run.
    pub draw_p50_ms: f64,
    pub draw_p99_ms: f64,
    pub draw_worst_ms: f64,
}

#[derive(Debug, Clone)]
pub struct LateLatch {
    enabled: bool,
    guard_ms: f64,
    boost_ms: f64,
    last_miss_ns: u64,
    last_decay_ns: u64,
    /// The newest frame callback, 0 none or used.
    callback_ns: u64,
    /// The start time `schedule()` gave the frame being drawn, 0 none.
    target_ns: u64,
    /// The frame being drawn follows a fresh callback.
    scheduled_frame: bool,
    /// The last commit, while its callback is due.
    commit_ns: u64,
    commit_scheduled: bool,
    /// The callback the frame being drawn aims for: the one it follows + a period.
    frame_aim_ns: u64,
    /// The same for the last commit.
    aim_ns: u64,
    costs_ms: [f64; Self::COST_WINDOW],
    cost_next: usize,
    cost_count: usize,
    draw_worst_ms: f64,
    slack: [u64; Self::SLACK_BUCKETS],
    draws: [u64; Self::SLACK_BUCKETS],
    scheduled: u64,
    waited: u64,
    latched: u64,
    missed: u64,
    immediate: u64,
}

impl Default for LateLatch {
    /// Off.
    fn default() -> Self {
        Self {
            enabled: false,
            guard_ms: 0.0,
            boost_ms: 0.0,
            last_miss_ns: 0,
            last_decay_ns: 0,
            callback_ns: 0,
            target_ns: 0,
            scheduled_frame: false,
            commit_ns: 0,
            commit_scheduled: false,
            frame_aim_ns: 0,
            aim_ns: 0,
            costs_ms: [0.0; Self::COST_WINDOW],
            cost_next: 0,
            cost_count: 0,
            draw_worst_ms: 0.0,
            slack: [0; Self::SLACK_BUCKETS],
            draws: [0; Self::SLACK_BUCKETS],
            scheduled: 0,
            waited: 0,
            latched: 0,
            missed: 0,
            immediate: 0,
        }
    }
}

impl LateLatch {
    /// About 2 s at 120 Hz.
    pub const COST_WINDOW: usize = 256;
    pub const MIN_MARGIN_MS: f64 = 0.25;
    pub const MAX_MARGIN_PERIODS: f64 = 0.75;
    pub const MISS_BOOST_MS: f64 = 1.0;
    pub const MAX_BOOST_MS: f64 = 4.0;
    pub const BOOST_QUIET_S: f64 = 10.0;
    pub const BOOST_DECAY_MS_PER_S: f64 = 0.1;
    pub const MAX_GUARD_MS: f64 = 8.0;
    pub const SLACK_BUCKET_MS: f64 = 0.1;
    /// 0..10 ms; more lands in the last bucket.
    pub const SLACK_BUCKETS: usize = 100;

    /// The guard as given on the command line: finite, 0..`MAX_GUARD_MS`. `None` for anything else (`None` text is the
    /// C++ `nullptr`). Parsed by the C library's `strtod`, so what it accepts (leading blanks, hex) matches the C++.
    pub fn parse_guard(text: Option<&str>) -> Option<f64> {
        let text = text?;
        if text.is_empty() {
            return None;
        }
        // A NUL inside cannot come from argv; the C++ would parse the part before it, which is never the whole text.
        let c = CString::new(text).ok()?;
        let mut end: *mut libc::c_char = std::ptr::null_mut();
        // SAFETY: `c` is a valid NUL-terminated string that outlives the call; `end` is a valid out pointer.
        let value = unsafe { libc::strtod(c.as_ptr(), &mut end) };
        // SAFETY: strtod sets `end` to a position inside `c` (at most its terminating NUL).
        if end.is_null() || unsafe { *end } != 0 || !value.is_finite() || value < 0.0 || value > Self::MAX_GUARD_MS {
            return None;
        }
        Some(value)
    }

    pub fn new(guard_ms: f64) -> Self {
        Self { enabled: true, guard_ms, ..Self::default() }
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    fn decay(&mut self, now_ns: u64) {
        if self.boost_ms <= 0.0 {
            return;
        }
        let quiet_from = self.last_miss_ns.wrapping_add((Self::BOOST_QUIET_S * 1e9) as u64);
        if now_ns <= quiet_from {
            return;
        }
        let from = max(quiet_from, self.last_decay_ns);
        self.boost_ms = max(0.0, self.boost_ms - Self::BOOST_DECAY_MS_PER_S * now_ns.wrapping_sub(from) as f64 / 1e9);
        self.last_decay_ns = now_ns;
    }

    /// A frame callback arrived at `now_ns`, the compositor refreshing every `period_ns` (0: unknown). Scores the frame
    /// it belongs to, if that one was drawn on the schedule.
    pub fn frame_done(&mut self, now_ns: u64, period_ns: u64) {
        if !self.enabled {
            return;
        }
        if self.commit_ns != 0 && self.commit_scheduled && now_ns >= self.commit_ns {
            let after = now_ns - self.commit_ns;
            if period_ns != 0 && now_ns.wrapping_mul(2) >= self.aim_ns.wrapping_mul(2).wrapping_add(period_ns) {
                self.missed = self.missed.wrapping_add(1);
                self.boost_ms = min(Self::MAX_BOOST_MS, self.boost_ms + Self::MISS_BOOST_MS);
                self.last_miss_ns = now_ns;
                self.last_decay_ns = now_ns;
            } else {
                self.latched = self.latched.wrapping_add(1);
                let bucket = (ms(after) / Self::SLACK_BUCKET_MS) as usize;
                self.slack[min(bucket, Self::SLACK_BUCKETS - 1)] += 1;
            }
        }
        self.commit_ns = 0;
        self.commit_scheduled = false;
        self.callback_ns = now_ns;
        self.decay(now_ns);
    }

    /// About to draw a head-tracked frame at `now_ns`: the time to start it at if that is later than now (wait, then
    /// call again), `None` to draw now. Always `None` when off, with no period, or with no fresh callback.
    pub fn schedule(&mut self, now_ns: u64, period_ns: u64) -> Option<u64> {
        if !self.enabled {
            return None;
        }
        if period_ns == 0 || self.callback_ns == 0 || now_ns >= self.callback_ns.wrapping_add(period_ns) {
            // No refresh period yet, or no callback to aim from (the first frame, a frame after the view sat idle, a
            // callback whose latch has already gone by): draw now, unscored.
            if !self.scheduled_frame && self.target_ns == 0 {
                self.immediate = self.immediate.wrapping_add(1);
            }
            self.callback_ns = 0;
            self.target_ns = 0;
            return None;
        }
        let margin_ns = (self.margin_ms(period_ns) * NS_PER_MS) as u64;
        let target = self.callback_ns.wrapping_add(period_ns).wrapping_sub(min(margin_ns, period_ns));
        if now_ns < target {
            self.target_ns = target;
            return Some(target);
        }
        self.scheduled = self.scheduled.wrapping_add(1);
        if self.target_ns != 0 {
            self.waited = self.waited.wrapping_add(1);
        }
        self.scheduled_frame = true;
        self.frame_aim_ns = self.callback_ns.wrapping_add(period_ns);
        self.callback_ns = 0;
        None
    }

    /// The frame started at `start_ns` (the time `schedule()` said draw now) was committed at `commit_ns`.
    pub fn committed(&mut self, start_ns: u64, commit_ns: u64) {
        if !self.enabled {
            return;
        }
        if self.scheduled_frame {
            // From the planned start when the frame waited (a late wake-up counts), else from when it really started.
            let from = if self.target_ns != 0 { min(self.target_ns, start_ns) } else { start_ns };
            let cost = if commit_ns > from { ms(commit_ns - from) } else { 0.0 };
            self.costs_ms[self.cost_next] = cost;
            self.draw_worst_ms = max(self.draw_worst_ms, cost);
            self.draws[min((cost / Self::SLACK_BUCKET_MS) as usize, Self::SLACK_BUCKETS - 1)] += 1;
            self.cost_next = (self.cost_next + 1) % Self::COST_WINDOW;
            self.cost_count = min(self.cost_count + 1, Self::COST_WINDOW);
        }
        self.commit_ns = commit_ns;
        self.commit_scheduled = self.scheduled_frame;
        self.aim_ns = self.frame_aim_ns;
        self.scheduled_frame = false;
        self.target_ns = 0;
    }

    pub fn margin_ms(&self, period_ns: u64) -> f64 {
        // No draw measured yet: aim early (the widest margin) rather than guess, so the first frame does not miss.
        if self.cost_count == 0 && period_ns != 0 {
            return Self::MAX_MARGIN_PERIODS * ms(period_ns);
        }
        let mut draw_max = 0.0;
        for &c in &self.costs_ms[..self.cost_count] {
            draw_max = max(draw_max, c);
        }
        let mut margin = max(Self::MIN_MARGIN_MS, draw_max + self.guard_ms + self.boost_ms);
        if period_ns != 0 {
            margin = min(margin, Self::MAX_MARGIN_PERIODS * ms(period_ns));
        }
        margin
    }

    pub fn summary(&self, period_ns: u64) -> LateLatchSummary {
        let mut s = LateLatchSummary { enabled: self.enabled, ..LateLatchSummary::default() };
        if !self.enabled {
            return s;
        }
        s.guard_ms = self.guard_ms;
        s.margin_ms = self.margin_ms(period_ns);
        for &c in &self.costs_ms[..self.cost_count] {
            s.draw_max_ms = max(s.draw_max_ms, c);
        }
        s.boost_ms = self.boost_ms;
        s.scheduled = self.scheduled;
        s.waited = self.waited;
        s.latched = self.latched;
        s.missed = self.missed;
        s.immediate = self.immediate;
        s.slack_p5_ms = percentile(&self.slack, 0.05);
        s.slack_p50_ms = percentile(&self.slack, 0.50);
        s.draw_p50_ms = percentile(&self.draws, 0.50);
        s.draw_p99_ms = percentile(&self.draws, 0.99);
        s.draw_worst_ms = self.draw_worst_ms;
        s
    }
}
