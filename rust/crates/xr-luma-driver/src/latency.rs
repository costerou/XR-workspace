//! A fixed-size latency histogram that never allocates after construction, ported from `rt::LatencyHistogram` in
//! `spike/driver/include/luma/rt.hpp`. Values below 64 have a bucket each; above that, every power of two is split into
//! 32 buckets, so a percentile is within about 3 % of the truth and always rounds up.

const LINEAR: usize = 64;
const SUB: usize = 32;
const BUCKETS: usize = LINEAR + (64 - 6) * SUB;

#[derive(Clone)]
pub struct LatencyHistogram {
    buckets: [u64; BUCKETS],
    count: u64,
    min: u64,
    max: u64,
}

impl Default for LatencyHistogram {
    fn default() -> Self {
        LatencyHistogram {
            buckets: [0; BUCKETS],
            count: 0,
            min: u64::MAX,
            max: 0,
        }
    }
}

impl LatencyHistogram {
    pub fn record(&mut self, ns: u64) {
        self.buckets[index(ns)] += 1;
        self.count += 1;
        self.min = self.min.min(ns);
        self.max = self.max.max(ns);
    }
    pub fn count(&self) -> u64 {
        self.count
    }
    pub fn min(&self) -> u64 {
        if self.count == 0 { 0 } else { self.min }
    }
    pub fn max(&self) -> u64 {
        self.max
    }
    /// `p` is a percentage, 0 to 100. The answer is the top of the bucket holding it, capped at the largest value seen.
    pub fn percentile(&self, p: f64) -> u64 {
        if self.count == 0 {
            return 0;
        }
        let target = ((self.count as f64 * p / 100.0).ceil() as u64).max(1);
        let mut seen = 0;
        for (i, n) in self.buckets.iter().enumerate() {
            seen += n;
            if seen >= target {
                return bucket_top(i).min(self.max);
            }
        }
        self.max
    }
}

fn index(v: u64) -> usize {
    if v < LINEAR as u64 {
        return v as usize;
    }
    let msb = 63 - v.leading_zeros() as usize;
    LINEAR + (msb - 6) * SUB + ((v >> (msb - 5)) as usize - SUB)
}

fn bucket_top(i: usize) -> u64 {
    if i < LINEAR {
        return i as u64;
    }
    let octave = (i - LINEAR) / SUB;
    let sub = (i - LINEAR) % SUB;
    let msb = octave + 6;
    // The very top bucket wraps to all ones, which is right.
    (((sub + SUB) as u64 + 1) << (msb - 5)).wrapping_sub(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles_round_up_and_stay_close() {
        let mut h = LatencyHistogram::default();
        for v in 1..=100_000u64 {
            h.record(v);
        }
        let p50 = h.percentile(50.0) as f64;
        let p99 = h.percentile(99.0) as f64;
        assert!((50_000.0..=50_000.0 * 1.04).contains(&p50), "{p50}");
        assert!((99_000.0..=99_000.0 * 1.04).contains(&p99), "{p99}");
        assert_eq!(h.percentile(100.0), 100_000);
        assert_eq!(h.min(), 1);
    }

    #[test]
    fn small_and_empty() {
        let empty = LatencyHistogram::default();
        assert_eq!(
            (empty.percentile(99.0), empty.min(), empty.max()),
            (0, 0, 0)
        );
        let mut h = LatencyHistogram::default();
        for v in 0..64 {
            h.record(v);
        }
        assert_eq!(h.percentile(25.0), 15);
        let mut top = LatencyHistogram::default();
        top.record(u64::MAX);
        assert_eq!(top.percentile(99.0), u64::MAX);
    }
}
