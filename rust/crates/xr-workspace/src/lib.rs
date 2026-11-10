//! Controller, supervisor, key helper and installer.

pub mod control;
pub mod install;
pub mod json;
pub mod lua;
pub mod managed;
pub mod private_file;
pub mod supervisor;
// Shared with the binary's tests, which use all of it.
#[cfg(test)]
#[allow(dead_code)]
mod testdir;
