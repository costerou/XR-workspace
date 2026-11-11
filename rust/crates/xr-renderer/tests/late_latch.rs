//! Port of ctest `late_latch` (spike/tests/late_latch_test.cpp): the schedule, the adaptive margin and the miss count,
//! on a simulated compositor. Plus Review Focus 5: a late frame boosts the margin and the frame after it is not
//! presented late as well.

use xr_renderer::late_latch::LateLatch;

const MS: u64 = 1_000_000;
const PERIOD: u64 = 8_333_333; // 120 Hz

fn near(a: f64, b: f64, eps: f64) -> bool {
    (a - b).abs() <= eps
}
fn near6(a: f64, b: f64) -> bool {
    near(a, b, 1e-6)
}

/// A compositor latching at k * period + phase and sending the frame callback `delay` after the latch; a renderer that
/// takes `draw(k)` from start to commit and wakes `late` after the time it asked for. Leaves the counts in the latch.
struct Sim {
    latch: LateLatch,
    delay: u64,
    late: u64,
    now: u64,
    next_latch: u64,
    worst_read_to_latch: u64,
    best_read_to_latch: u64,
    /// Per frame: did its callback count as a miss, and the margin it was scheduled with.
    missed_frames: Vec<bool>,
    margins: Vec<f64>,
}

impl Sim {
    fn new(guard: f64) -> Self {
        Self {
            latch: LateLatch::new(guard),
            delay: 200_000,
            late: 50_000,
            now: 1000 * MS,
            next_latch: 1000 * MS + PERIOD - 200_000, // the first callback came `delay` after a latch
            worst_read_to_latch: 0,
            best_read_to_latch: u64::MAX,
            missed_frames: Vec::new(),
            margins: Vec::new(),
        }
    }

    fn run(&mut self, frames: i32, draw: impl Fn(i32) -> u64) {
        self.latch.frame_done(self.now, PERIOD); // the first callback
        for k in 0..frames {
            let callback = self.now;
            self.margins.push(self.latch.margin_ms(PERIOD));
            if let Some(at) = self.latch.schedule(self.now, PERIOD) {
                self.now = at + self.late;
                assert!(self.latch.schedule(self.now, PERIOD).is_none());
            }
            let start = self.now;
            let commit = start + draw(k);
            self.latch.committed(start, commit);
            // The commit is taken at the first latch at or after it, the callback follows that latch.
            let mut at = self.next_latch;
            while at < commit {
                at += PERIOD;
            }
            if k >= 10 {
                // past the first frames, which start early on purpose (no draw measured yet)
                self.worst_read_to_latch = self.worst_read_to_latch.max(at - start);
                self.best_read_to_latch = self.best_read_to_latch.min(at - start);
            }
            self.now = at + self.delay;
            self.next_latch = at + PERIOD;
            assert!(self.now > callback);
            let before = self.latch.summary(PERIOD).missed;
            self.latch.frame_done(self.now, PERIOD);
            self.missed_frames.push(self.latch.summary(PERIOD).missed > before);
        }
    }
}

#[test]
fn parse_guard_is_a_finite_0_to_8_ms() {
    assert!(LateLatch::parse_guard(Some("1")).is_some());
    assert!(near6(LateLatch::parse_guard(Some("1.5")).unwrap(), 1.5));
    assert!(LateLatch::parse_guard(Some("0")).is_some());
    assert!(LateLatch::parse_guard(Some("-0.1")).is_none());
    assert!(LateLatch::parse_guard(Some("8.5")).is_none());
    assert!(LateLatch::parse_guard(Some("nan")).is_none());
    assert!(LateLatch::parse_guard(Some("1ms")).is_none());
    assert!(LateLatch::parse_guard(Some("")).is_none());
    assert!(LateLatch::parse_guard(None).is_none());
    // strtod's own forms, as the C++ accepts them.
    assert_eq!(LateLatch::parse_guard(Some("8")), Some(8.0));
    assert_eq!(LateLatch::parse_guard(Some(" 2")), Some(2.0));
    assert_eq!(LateLatch::parse_guard(Some("0x1p1")), Some(2.0));
    assert!(LateLatch::parse_guard(Some("inf")).is_none());
    assert!(LateLatch::parse_guard(Some("1 ")).is_none());
}

#[test]
fn off_never_waits_and_counts_nothing() {
    let mut off = LateLatch::default();
    assert!(!off.enabled());
    off.frame_done(10 * MS, PERIOD);
    assert!(off.schedule(10 * MS, PERIOD).is_none());
    off.committed(10 * MS, 11 * MS);
    assert!(!off.summary(PERIOD).enabled);
    assert_eq!(off.summary(PERIOD).scheduled, 0);
}

#[test]
fn no_period_or_no_callback_draws_at_once_unscored() {
    let mut l = LateLatch::new(1.0);
    assert!(l.schedule(5 * MS, PERIOD).is_none()); // no callback yet
    l.committed(5 * MS, 6 * MS);
    l.frame_done(20 * MS, PERIOD); // 14 ms after the commit, but the frame was not on the schedule
    assert!(l.schedule(20 * MS, 0).is_none()); // period unknown
    let s = l.summary(PERIOD);
    assert!(s.immediate == 2 && s.scheduled == 0 && s.missed == 0 && s.latched == 0);
}

#[test]
fn target_is_callback_plus_period_minus_margin() {
    let mut l = LateLatch::new(1.0);
    l.frame_done(100 * MS, PERIOD);
    let at = l.schedule(100 * MS, PERIOD).unwrap();
    assert_eq!(at, 100 * MS + PERIOD - (0.75 * PERIOD as f64) as u64);
    assert!(l.schedule(at, PERIOD).is_none());
    l.committed(at, at + 300_000); // 0.3 ms draw
    assert!(near6(l.margin_ms(PERIOD), 1.3));
    // A wake-up 0.2 ms late counts against the draw: 0.5 ms from the target.
    l.frame_done(at + MS, PERIOD);
    let at2 = l.schedule(at + MS, PERIOD).unwrap();
    assert!(l.schedule(at2 + 200_000, PERIOD).is_none());
    l.committed(at2 + 200_000, at2 + 500_000);
    assert!(near6(l.margin_ms(PERIOD), 1.5));
    // The margin is capped at three quarters of a period and floored at MIN_MARGIN_MS.
    let big = LateLatch::new(LateLatch::MAX_GUARD_MS);
    assert!(near6(big.margin_ms(PERIOD), 0.75 * PERIOD as f64 / 1e6));
    assert!(near6(big.margin_ms(0), LateLatch::MAX_GUARD_MS));
    let zero = LateLatch::new(0.0);
    assert!(near6(zero.margin_ms(0), LateLatch::MIN_MARGIN_MS));
}

#[test]
fn a_callback_whose_latch_went_by_is_not_aimed_from() {
    let mut l = LateLatch::new(1.0);
    l.frame_done(100 * MS, PERIOD);
    assert!(l.schedule(100 * MS + PERIOD, PERIOD).is_none());
    assert_eq!(l.summary(PERIOD).immediate, 1);
}

#[test]
fn steady_state_makes_every_latch_about_2_ms_before_it() {
    // Draws of 0.4..0.9 ms, a 1 ms guard. Every frame makes the latch it aims at, a pose read about 2 ms before the
    // latch instead of a period before it.
    let mut sim = Sim::new(1.0);
    sim.run(3000, |k| 400_000 + (k % 6) as u64 * 100_000);
    let s = sim.latch.summary(PERIOD);
    assert_eq!(s.missed, 0);
    assert!(s.latched == 3000 && s.scheduled == 3000 && s.waited == 3000);
    assert!(sim.worst_read_to_latch < 2 * MS + 500_000);
    assert!(sim.best_read_to_latch > 500_000);
    assert!(near(s.draw_max_ms, 0.95, 1e-9) && near(s.margin_ms, 1.95, 1e-9));
    assert!(s.slack_p5_ms > 0.9 && s.slack_p50_ms < 2.0);
    assert!(s.draw_p50_ms > 0.5 && s.draw_p50_ms < 0.8 && s.draw_p99_ms > 0.9 && s.draw_p99_ms < 1.0);
}

#[test]
fn a_draw_spike_misses_once_then_the_boost_holds() {
    // A draw spike past the margin misses once; the boost and the cost window keep the next ones from missing.
    let mut sim = Sim::new(0.5);
    sim.run(1000, |k| if k == 900 { 3 * MS } else { 400_000 });
    let s = sim.latch.summary(PERIOD);
    assert_eq!(s.missed, 1);
    assert!(near(s.boost_ms, LateLatch::MISS_BOOST_MS, 0.01)); // under 10 s since: no decay yet
    assert!(s.draw_max_ms >= 3.0 && s.draw_worst_ms >= 3.0);
    assert_eq!(s.latched, 999);
    // Review Focus 5: the late frame is 900, the one after it is not late too, and its margin grew by the spike and
    // the boost.
    assert!(sim.missed_frames[900]);
    assert!(sim.missed_frames.windows(2).all(|w| !(w[0] && w[1])));
    assert!(sim.margins[901] >= sim.margins[900] + LateLatch::MISS_BOOST_MS);
}

#[test]
fn the_boost_is_capped_and_drains_only_after_quiet() {
    let mut l = LateLatch::new(0.5);
    let mut t = 1000 * MS;
    for _ in 0..10 {
        l.frame_done(t, PERIOD);
        let at = l.schedule(t, PERIOD).unwrap();
        let _ = l.schedule(at, PERIOD);
        l.committed(at, at + 100_000);
        t = at + 2 * PERIOD; // a refresh after the one aimed for: missed
    }
    l.frame_done(t, PERIOD);
    let s = l.summary(PERIOD);
    assert_eq!(s.missed, 10);
    assert!(near6(s.boost_ms, LateLatch::MAX_BOOST_MS));
    l.frame_done(t + 5_000 * MS, PERIOD); // 5 s quiet: no drain
    assert!(near6(l.summary(PERIOD).boost_ms, LateLatch::MAX_BOOST_MS));
    l.frame_done(t + 20_000 * MS, PERIOD); // 20 s: 10 s of drain at 0.1 ms/s
    assert!(near(l.summary(PERIOD).boost_ms, LateLatch::MAX_BOOST_MS - 1.0, 1e-6));
    l.frame_done(t + 200_000 * MS, PERIOD);
    assert!(near6(l.summary(PERIOD).boost_ms, 0.0));
}
