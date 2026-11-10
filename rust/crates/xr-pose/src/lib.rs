//! Pose shared memory: the SysV feed, seqlock channels and PredictionWire.
//!
//! `feed` is the XRLinuxDriver-compatible SysV feed (plan, section 1, item 7), checked against
//! the C++ `PoseFeed` and `pose_test` by `tests/feed_cpp_interop.rs`.
//!
//! Both lock-free channels here are byte-compatible with the C++ Luma driver
//! (`spike/driver/include/luma/rt.hpp` and `predict.hpp`). The layouts are wire formats: they are
//! asserted at compile time below and checked against the C++ compiler by `tests/cpp_interop.rs`.

pub mod feed;
pub mod prediction;
pub mod seqlock;

pub use prediction::{MotionState, PREDICTION_IPC_NAME, PREDICTION_MAGIC, PredictionWire};
pub use seqlock::{POSE_CALIBRATED, POSE_SHM_NAME, PoseReader, PoseRecord, PoseWriter};
