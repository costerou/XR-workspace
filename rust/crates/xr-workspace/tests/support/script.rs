//! Executables for integration tests, written by a child process. A writable descriptor held in the
//! test process could leak into another test's fork and make the exec fail with ETXTBSY.
//!
//! Include with `#[path = "support/script.rs"] mod script;`.
#![allow(dead_code)]

use std::path::Path;
use std::process::Command;

/// `sh -c script` with `args` as `$1`, `$2`, ..., asserted to succeed.
fn sh(script: &str, args: &[&std::ffi::OsStr]) {
    let status = Command::new("/bin/sh")
        .args(["-c", script, "sh"])
        .args(args)
        .status()
        .unwrap();
    assert!(status.success(), "{script}: {status}");
}

/// An executable script (0755) at `path` with `body`, replacing any file there.
pub fn write_script(path: &Path, body: &str) {
    sh(
        "printf '%s' \"$1\" > \"$2\" && chmod 755 \"$2\"",
        &[body.as_ref(), path.as_os_str()],
    );
}

/// A private (0700) copy of the executable `from` at `to`, replacing any file there.
pub fn copy_executable(from: &Path, to: &Path) {
    sh(
        "cp -- \"$1\" \"$2\" && chmod 700 \"$2\"",
        &[from.as_os_str(), to.as_os_str()],
    );
}
