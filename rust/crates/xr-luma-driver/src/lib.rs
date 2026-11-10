//! Luma Ultra head-tracking driver over raw usbdevfs: the library behind the `xr-luma-driver`
//! binary. A port of `spike/driver/`; see docs/rust-rebuild-plan.md, phase 2.

pub mod fusion;
pub mod types;
