//! Controller, supervisor, key helper and installer.
//!
//! The supervisor's window handling is ported from tools/native_spike: where windows go when a run starts (`adopt`,
//! `window_memory`), what the desktops become when the panel changes them (`desktops`), keeping floating windows inside
//! a desktop that shrank (`fit_windows`), the send keys (`send`) and the outputs a corner resize works on (`resize`).
//! Each of those modules is the pure part: plans from Hyprland's JSON replies, the same as the Python makes
//! (rust/corpus/placement), which `lua` turns into the evals that carry them out. Talking to Hyprland and the broker is
//! the run's.

pub mod adopt;
pub mod control;
pub mod controller;
pub mod desktops;
pub mod fit_windows;
pub mod geometry;
pub mod hyprland;
pub mod install;
pub mod journal;
pub mod json;
pub mod lua;
pub mod managed;
pub mod monitor_rules;
pub mod pro2_route;
pub mod private_file;
mod py;
pub mod pyjson;
pub mod recovery;
pub mod resize;
pub mod runner;
pub mod send;
pub mod supervisor;
// Shared with the binary's tests, which use all of it.
#[cfg(test)]
#[allow(dead_code)]
mod testdir;
pub mod window_memory;
