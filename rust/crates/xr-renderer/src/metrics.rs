//! Presentation timing: the bounded window behind the exit timing report and the status datagram.
//!
//! A port of `spike/src/metrics.cpp`. The report has the same fields, the same values and the same
//! (alphabetical) key order, so the two renderers can be compared line for line. No host receipt
//! timestamps and no clock-domain conversion: only what `wp_presentation` reports.

use serde_json::{Map, Value, json};

/// The clock id `wp_presentation` names for CLOCK_MONOTONIC.
const CLOCK_MONOTONIC: u32 = 1;
const BOUNDS: [u64; 7] = [
    1_000_000,
    4_000_000,
    8_000_000,
    9_000_000,
    17_000_000,
    34_000_000,
    100_000_000,
];
/// Valid presentation timestamps of the last second, at most this many (a power of two).
const RECENT_CAPACITY: usize = 4096;
/// Intervals over 34 ms: slots 0..32 keep the first ones, 32..64 a ring of the latest.
const STALL_CAPACITY: usize = 64;
const STALL_HEAD: usize = 32;

fn increment(n: &mut u64) {
    *n = n.saturating_add(1);
}

/// The counters a report is made from (`TimingSnapshot` in the C++).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TimingSnapshot {
    pub protocol_version: u32,
    pub clock_id: Option<u32>,
    pub submitted: u64,
    pub presented: u64,
    pub discarded: u64,
    pub abandoned: u64,
    pub pending: u64,
    pub invalid_timing: u64,
    pub valid_presented: u64,
    pub interval_count: u64,
    pub interval_sum_ns: u64,
    pub interval_min_ns: Option<u64>,
    pub interval_max_ns: Option<u64>,
    pub intervals: [u64; 8],
    pub flags: [u64; 4],
    pub last_refresh_prediction_ns: u32,
}

/// One presented event, as `wp_presentation_feedback.presented` carries it.
#[derive(Clone, Copy, Debug, Default)]
pub struct Presented {
    pub sec_hi: u32,
    pub sec_lo: u32,
    pub nsec: u32,
    pub refresh: u32,
    pub seq_hi: u32,
    pub seq_lo: u32,
    pub flags: u32,
}

/// Bounded presentation window. Recording a presentation never allocates.
#[derive(Clone)]
pub struct Metrics {
    state: TimingSnapshot,
    pending: [u64; Metrics::CAPACITY],
    previous_ns: Option<u64>,
    previous_sequence: Option<u64>,
    first_valid_ns: Option<u64>,
    last_valid_ns: Option<u64>,
    recent: Box<[u64; RECENT_CAPACITY]>,
    recent_head: usize,
    recent_size: usize,
    clock_conflict: bool,
    interval_anchor: bool,
    stalls: [[u64; 2]; STALL_CAPACITY],
    stall_total: u64,
}

impl Default for Metrics {
    fn default() -> Self {
        Self {
            state: TimingSnapshot::default(),
            pending: [0; Self::CAPACITY],
            previous_ns: None,
            previous_sequence: None,
            first_valid_ns: None,
            last_valid_ns: None,
            recent: Box::new([0; RECENT_CAPACITY]),
            recent_head: 0,
            recent_size: 0,
            clock_conflict: false,
            interval_anchor: false,
            stalls: [[0; 2]; STALL_CAPACITY],
            stall_total: 0,
        }
    }
}

impl Metrics {
    /// Feedback requests in flight at most; a full window holds the next commit back.
    pub const CAPACITY: usize = 32;

    /// The protocol version to bind: version 1 semantics, even when a later one is advertised.
    pub fn bind(&mut self, advertised: u32) {
        self.state.protocol_version = advertised.min(1);
    }

    pub fn clock(&mut self, id: u32) {
        if self.state.clock_id.is_some_and(|c| c != id) {
            self.clock_conflict = true;
            self.previous_ns = None;
            self.previous_sequence = None;
        }
        self.state.clock_id = Some(id);
    }

    pub fn can_submit(&self) -> bool {
        self.state.submitted != u64::MAX
            && (self.state.protocol_version == 0 || self.state.pending < Self::CAPACITY as u64)
    }

    /// Call only right before the content commit it belongs to. Without the protocol the submission
    /// is counted but has no ticket; a full window gives `None` and the caller must not commit.
    pub fn submit(&mut self) -> Option<u64> {
        if !self.can_submit() {
            return None;
        }
        self.state.submitted += 1;
        if self.state.protocol_version == 0 {
            return None;
        }
        let slot = self
            .pending
            .iter_mut()
            .find(|t| **t == 0)
            .expect("can_submit leaves a free slot");
        *slot = self.state.submitted;
        self.state.pending += 1;
        Some(self.state.submitted)
    }

    fn finish(&mut self, ticket: u64) -> bool {
        if ticket == 0 {
            return false;
        }
        let Some(slot) = self.pending.iter_mut().find(|t| **t == ticket) else {
            return false;
        };
        *slot = 0;
        self.state.pending -= 1;
        true
    }

    pub fn presented(&mut self, ticket: u64, p: Presented, selected_output: bool) {
        if !self.finish(ticket) {
            return;
        }
        increment(&mut self.state.presented);
        for i in 0..4 {
            if p.flags & (1 << i) != 0 {
                increment(&mut self.state.flags[i]);
            }
        }
        self.state.last_refresh_prediction_ns = p.refresh;
        let seconds = (u64::from(p.sec_hi) << 32) | u64::from(p.sec_lo);
        let sequence = (u64::from(p.seq_hi) << 32) | u64::from(p.seq_lo);
        let mut valid = selected_output
            && !self.clock_conflict
            && self.state.clock_id == Some(CLOCK_MONOTONIC)
            && p.nsec < 1_000_000_000
            && seconds <= (i64::MAX as u64 - u64::from(p.nsec)) / 1_000_000_000
            && sequence != 0;
        let timestamp = if valid {
            seconds * 1_000_000_000 + u64::from(p.nsec)
        } else {
            0
        };
        valid = valid
            && self.previous_ns.is_none_or(|prev| timestamp > prev)
            && self.previous_sequence.is_none_or(|prev| sequence > prev);
        if !valid {
            increment(&mut self.state.invalid_timing);
            self.interval_anchor = false;
            return;
        }
        increment(&mut self.state.valid_presented);
        self.first_valid_ns.get_or_insert(timestamp);
        self.last_valid_ns = Some(timestamp);
        self.remember(timestamp);
        if let (Some(previous), true) = (self.previous_ns, self.interval_anchor) {
            let delta = timestamp - previous;
            let index = BOUNDS.partition_point(|&b| b < delta);
            increment(&mut self.state.intervals[index]);
            if index >= 6 {
                // over 34 ms
                let tail = (STALL_CAPACITY - STALL_HEAD) as u64;
                let slot = if self.stall_total < STALL_CAPACITY as u64 {
                    self.stall_total as usize
                } else {
                    STALL_HEAD + ((self.stall_total - STALL_HEAD as u64) % tail) as usize
                };
                self.stalls[slot] = [timestamp, delta];
                increment(&mut self.stall_total);
            }
            increment(&mut self.state.interval_count);
            // Disjoint increasing timestamp intervals sum to at most i64::MAX.
            self.state.interval_sum_ns += delta;
            self.state.interval_min_ns =
                Some(self.state.interval_min_ns.unwrap_or(delta).min(delta));
            self.state.interval_max_ns =
                Some(self.state.interval_max_ns.unwrap_or(delta).max(delta));
        }
        self.previous_ns = Some(timestamp);
        self.previous_sequence = Some(sequence);
        self.interval_anchor = true;
    }

    fn remember(&mut self, timestamp: u64) {
        const MASK: usize = RECENT_CAPACITY - 1;
        if self.recent_size == RECENT_CAPACITY {
            // full: drop the oldest
            self.recent_head = (self.recent_head + 1) & MASK;
            self.recent_size -= 1;
        }
        self.recent[(self.recent_head + self.recent_size) & MASK] = timestamp;
        self.recent_size += 1;
        while timestamp - self.recent[self.recent_head] > 1_000_000_000 {
            self.recent_head = (self.recent_head + 1) & MASK;
            self.recent_size -= 1;
        }
    }

    pub fn discarded(&mut self, ticket: u64) {
        if self.finish(ticket) {
            increment(&mut self.state.discarded);
        }
    }

    /// Every request still in flight is abandoned, never counted as a discard.
    pub fn abandon(&mut self) {
        for ticket in &mut self.pending {
            if *ticket != 0 {
                *ticket = 0;
                increment(&mut self.state.abandoned);
            }
        }
        self.state.pending = 0;
    }

    pub fn snapshot(&self) -> &TimingSnapshot {
        &self.state
    }

    /// The supervisor's status sample: `missed` and `presented_hz` over the last second.
    pub fn status_json(&self, now_ms: f64) -> String {
        let mut j = Map::new();
        if self.state.presented != 0 || self.state.discarded != 0 {
            j.insert("missed".into(), json!(self.state.discarded));
        }
        if !self.clock_conflict && now_ms.is_finite() && now_ms >= 0.0 {
            let (mut first, mut last, mut count) = (0u64, 0u64, 0usize);
            for i in 0..self.recent_size {
                let ns = self.recent[(self.recent_head + i) & (RECENT_CAPACITY - 1)];
                let ms = ns as f64 / 1e6;
                if ms <= now_ms && ms >= now_ms - 1000.0 {
                    if count == 0 {
                        first = ns;
                    }
                    last = ns;
                    count += 1;
                }
            }
            if count >= 2 {
                j.insert(
                    "presented_hz".into(),
                    json!((count - 1) as f64 * 1e9 / (last - first) as f64),
                );
            }
        }
        // nlohmann's sorted order, whichever map serde_json was built with (xr-workspace turns on
        // preserve_order for the whole workspace).
        j.sort_keys();
        Value::Object(j).to_string()
    }

    /// The exit timing report, one JSON line.
    pub fn report(&self) -> String {
        let s = &self.state;
        let mut j = Map::new();
        let mut put = |k: &str, v: Value| {
            j.insert(k.into(), v);
        };
        put("schema", json!(1));
        put("kind", json!("generated_pattern_presentation_timing"));
        put("protocol_version", json!(s.protocol_version));
        put("clock_id", s.clock_id.map_or(Value::Null, |c| json!(c)));
        put("clock_conflict", json!(self.clock_conflict));
        put("submitted", json!(s.submitted));
        put("presented", json!(s.presented));
        put("discarded", json!(s.discarded));
        put("abandoned", json!(s.abandoned));
        put("pending", json!(s.pending));
        put("invalid_timing", json!(s.invalid_timing));
        put("interval_histogram", json!(s.intervals));
        let mut bounds: Vec<Value> = BOUNDS.iter().map(|b| json!(b)).collect();
        bounds.push(Value::Null);
        put("interval_upper_bounds_ns", Value::Array(bounds));
        put(
            "flag_counts_vsync_hw_clock_hw_completion_zero_copy",
            json!(s.flags),
        );
        put(
            "last_refresh_prediction_ns",
            json!(s.last_refresh_prediction_ns),
        );
        put("sustained_cadence_qualified", json!(false));
        put("valid_presented", json!(s.valid_presented));
        put("interval_count", json!(s.interval_count));
        put("interval_sum_ns", json!(s.interval_sum_ns));
        let counted = s.interval_count != 0;
        put(
            "interval_min_ns",
            if counted {
                json!(s.interval_min_ns)
            } else {
                Value::Null
            },
        );
        put(
            "interval_max_ns",
            if counted {
                json!(s.interval_max_ns)
            } else {
                Value::Null
            },
        );
        put(
            "interval_mean_ns",
            if counted {
                json!(s.interval_sum_ns as f64 / s.interval_count as f64)
            } else {
                Value::Null
            },
        );
        // First-to-last accepted presentation timestamps, not requested run length, callback
        // arrival time, refresh prediction, or an FPS denominator.
        put(
            "observation_duration_ns",
            match (
                s.valid_presented >= 2,
                self.first_valid_ns,
                self.last_valid_ns,
            ) {
                (true, Some(first), Some(last)) => json!(last - first),
                _ => Value::Null,
            },
        );
        put("first_presented_ns", json!(self.first_valid_ns));
        put("last_presented_ns", json!(self.last_valid_ns));
        put("stall_threshold_ns", json!(34_000_000));
        let kept = self.stall_total.min(STALL_CAPACITY as u64) as usize;
        let tail = (STALL_CAPACITY - STALL_HEAD) as u64;
        let stalls: Vec<Value> = (0..kept)
            .map(|k| {
                // Past capacity the ring's oldest entry sits where the next write goes.
                let slot = if self.stall_total <= STALL_CAPACITY as u64 || k < STALL_HEAD {
                    k
                } else {
                    STALL_HEAD
                        + ((self.stall_total - STALL_HEAD as u64 + (k - STALL_HEAD) as u64) % tail)
                            as usize
                };
                json!(self.stalls[slot])
            })
            .collect();
        put("stall_intervals", Value::Array(stalls));
        put(
            "stall_intervals_dropped",
            json!(self.stall_total - kept as u64),
        );
        // nlohmann's sorted order, whichever map serde_json was built with (xr-workspace turns on
        // preserve_order for the whole workspace).
        j.sort_keys();
        Value::Object(j).to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NS: u64 = 1_000_000_000;

    fn at(ns: u64, seq: u64) -> Presented {
        Presented {
            sec_hi: ((ns / NS) >> 32) as u32,
            sec_lo: (ns / NS) as u32,
            nsec: (ns % NS) as u32,
            refresh: 8_333_333,
            seq_hi: (seq >> 32) as u32,
            seq_lo: seq as u32,
            flags: 0b0111,
        }
    }

    fn bound() -> Metrics {
        let mut m = Metrics::default();
        m.bind(2);
        m.clock(CLOCK_MONOTONIC);
        m
    }

    /// The C++ report's keys, alphabetical as nlohmann::json writes them.
    const KEYS: [&str; 28] = [
        "abandoned",
        "clock_conflict",
        "clock_id",
        "discarded",
        "first_presented_ns",
        "flag_counts_vsync_hw_clock_hw_completion_zero_copy",
        "interval_count",
        "interval_histogram",
        "interval_max_ns",
        "interval_mean_ns",
        "interval_min_ns",
        "interval_sum_ns",
        "interval_upper_bounds_ns",
        "invalid_timing",
        "kind",
        "last_presented_ns",
        "last_refresh_prediction_ns",
        "observation_duration_ns",
        "pending",
        "presented",
        "protocol_version",
        "schema",
        "stall_intervals",
        "stall_intervals_dropped",
        "stall_threshold_ns",
        "submitted",
        "sustained_cadence_qualified",
        "valid_presented",
    ];

    fn present(m: &mut Metrics, ticket: u64, ns: u64, seq: u64, selected: bool) {
        m.presented(ticket, at(ns, seq), selected);
    }

    /// The scenarios of golden/metrics.cpp, line for line.
    fn golden_lines() -> Vec<String> {
        let mut out = vec![Metrics::default().report()];
        let mut m = bound();
        let start = 100 * NS;
        for i in 0..121u64 {
            let t = m.submit().unwrap();
            present(&mut m, t, start + i * 8_333_333, i + 1, true);
        }
        let t = m.submit().unwrap();
        m.discarded(t);
        m.submit();
        m.abandon();
        out.push(m.report());
        out.push(m.status_json((start + 120 * 8_333_333) as f64 / 1e6));
        let mut m = Metrics::default();
        m.bind(1);
        m.clock(CLOCK_MONOTONIC);
        let mut t = NS;
        let first = m.submit().unwrap();
        present(&mut m, first, t, 1, true);
        for i in 0..100u64 {
            t += 50_000_000 + i;
            let k = m.submit().unwrap();
            present(&mut m, k, t, i + 2, true);
        }
        let k = m.submit().unwrap();
        present(&mut m, k, t + 1000, 200, false);
        let k = m.submit().unwrap();
        present(&mut m, k, t + 2 * NS, 201, true);
        m.clock(4);
        out.push(m.report());
        out.push(m.status_json(t as f64 / 1e6));
        out
    }

    #[test]
    fn reports_match_the_cpp_byte_for_byte() {
        let want: Vec<&str> = include_str!("../golden/metrics.txt").lines().collect();
        assert_eq!(golden_lines(), want);
        let parsed: Value = serde_json::from_str(want[0]).unwrap();
        assert_eq!(
            parsed
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            KEYS
        );
    }

    #[test]
    fn steady_120hz_window() {
        let mut m = bound();
        let period = 8_333_333;
        let start = 100 * NS;
        for i in 0..121u64 {
            let t = m.submit().unwrap();
            m.presented(t, at(start + i * period, i + 1), true);
        }
        let s = m.snapshot();
        assert_eq!(
            (
                s.submitted,
                s.presented,
                s.valid_presented,
                s.interval_count
            ),
            (121, 121, 121, 120)
        );
        assert_eq!(s.intervals, [0, 0, 0, 120, 0, 0, 0, 0]);
        assert_eq!(s.flags, [121, 121, 121, 0]);
        let last_ms = (start + 120 * period) as f64 / 1e6;
        let status: Value = serde_json::from_str(&m.status_json(last_ms)).unwrap();
        assert_eq!(status["missed"], json!(0));
        let hz = status["presented_hz"].as_f64().unwrap();
        assert!((hz - 120.0).abs() < 1e-4, "{hz}");
    }

    #[test]
    fn invalid_presentations_break_the_interval_chain() {
        let mut m = bound();
        let t1 = m.submit().unwrap();
        m.presented(t1, at(NS, 1), true);
        let t2 = m.submit().unwrap();
        m.presented(t2, at(2 * NS, 2), false); // another output
        let t3 = m.submit().unwrap();
        m.presented(t3, at(3 * NS, 3), true); // no interval: the anchor went
        let t4 = m.submit().unwrap();
        m.presented(t4, at(3 * NS, 4), true); // not later
        let s = m.snapshot();
        assert_eq!(
            (s.valid_presented, s.invalid_timing, s.interval_count),
            (2, 2, 0)
        );
        m.presented(t4, at(4 * NS, 5), true); // a ticket already finished is ignored
        assert_eq!(m.snapshot().presented, 4);
    }

    #[test]
    fn clock_conflict_invalidates_everything_after() {
        let mut m = bound();
        m.clock(4);
        let t = m.submit().unwrap();
        m.presented(t, at(NS, 1), true);
        assert_eq!(m.snapshot().invalid_timing, 1);
        let r: Value = serde_json::from_str(&m.report()).unwrap();
        assert_eq!(r["clock_conflict"], json!(true));
        assert_eq!(m.status_json(1000.0), r#"{"missed":0}"#);
    }

    #[test]
    fn window_is_bounded_and_abandon_counts_pending() {
        let mut m = bound();
        for _ in 0..Metrics::CAPACITY {
            assert!(m.submit().is_some());
        }
        assert!(!m.can_submit());
        assert_eq!(m.submit(), None);
        m.discarded(1);
        assert!(m.can_submit());
        m.abandon();
        let s = m.snapshot();
        assert_eq!(
            (s.discarded, s.abandoned, s.pending, s.submitted),
            (1, 31, 0, 32)
        );
        let mut none = Metrics::default();
        assert_eq!(none.submit(), None);
        assert_eq!(none.snapshot().submitted, 1);
    }

    #[test]
    fn stalls_keep_the_first_and_the_latest() {
        let mut m = bound();
        let mut t = NS;
        let first = m.submit().unwrap();
        m.presented(first, at(t, 1), true);
        for i in 0..100u64 {
            t += 50_000_000 + i; // each one a stall, told apart by length
            let k = m.submit().unwrap();
            m.presented(k, at(t, i + 2), true);
        }
        let r: Value = serde_json::from_str(&m.report()).unwrap();
        let stalls = r["stall_intervals"].as_array().unwrap();
        assert_eq!(stalls.len(), 64);
        assert_eq!(r["stall_intervals_dropped"], json!(36));
        let lengths: Vec<u64> = stalls
            .iter()
            .map(|s| s[1].as_u64().unwrap() - 50_000_000)
            .collect();
        let want: Vec<u64> = (0..32).chain(68..100).collect();
        assert_eq!(lengths, want);
        assert_eq!(m.snapshot().intervals[6], 100);
    }
}
