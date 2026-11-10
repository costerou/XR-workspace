//! The bridge's velocity estimate (src/motion.rs) over a pose capture, offline, for
//! `tools/pro2_runtime/smooth_acceptance.py --bridge-motion`.
//!
//! stdin: `host_ms device_ms x y z w` per sample, the rows `pro2_smooth_replay` reads. stdout: the
//! same rows with `wx wy wz` appended, rad/s, body frame: what the bridge would publish on
//! `pose_prediction` at that sample, for `pro2_smooth_replay --mode wire`.

#[path = "../src/motion.rs"]
mod motion;

use std::io::{BufRead, Write};

fn main() {
    let mut estimator = motion::MotionEstimator::default();
    let stdout = std::io::stdout();
    let mut out = std::io::BufWriter::new(stdout.lock());
    for line in std::io::stdin().lock().lines() {
        let line = line.expect("stdin");
        let f: Vec<f64> = line
            .split_whitespace()
            .map(|v| v.parse().expect("a number"))
            .collect();
        assert_eq!(f.len(), 6, "host_ms device_ms x y z w: {line}");
        let q = [f[5] as f32, f[2] as f32, f[3] as f32, f[4] as f32];
        let w = estimator.update(q, (f[1] * 1e6).round().max(0.0) as u64);
        writeln!(out, "{line} {:.9} {:.9} {:.9}", w[0], w[1], w[2]).expect("stdout");
    }
}
