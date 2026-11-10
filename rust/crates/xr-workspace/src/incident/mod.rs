//! The incident monitor: `tools/xr_controller/incident_*.py` in Rust.
//!
//! `detect` turns observations and logs into signals, `report` writes, scrubs and rate-limits the reports,
//! `sources` reads the host, and `monitor` ties them together on its own thread.

pub mod detect;
pub mod monitor;
pub mod py;
pub mod report;
pub mod sources;

#[cfg(test)]
mod corpus_tests;
