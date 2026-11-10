//! Control, plugin and status wire envelopes with strict JSON decoding, the panel's controller
//! socket and a run's control socket, and the files a run leaves on disk.

pub mod control;
pub mod controller;
pub mod disk;
pub mod files;
pub mod json;
pub mod plugin;
pub mod run_control;
pub mod status;
