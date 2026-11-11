//! The launch flags the features take (shell/tracking.rs, immersive.rs, continuity_shell.rs). Until a feature is
//! wired its flag exits 2 at the argument check, before any inherited descriptor is touched, as an unknown flag did.
//! A feature that starts taking its flag replaces its own case here.

use std::process::Command;

/// The renderer's exit code for the supervised arguments plus `extra`. The descriptor numbers are never opened: every
/// case here stops at the argument check.
fn exit_code(extra: &[&str]) -> Option<i32> {
    let base = ["--supervised-fd", "3", "--wayland-fd", "4", "--output", "DP-1", "--run-id", "r", "--session", "s"];
    Command::new(env!("CARGO_BIN_EXE_xr-renderer"))
        .args(base)
        .args(["--capture", "XR-1"])
        .args(extra)
        .output()
        .expect("run xr-renderer")
        .status
        .code()
}

#[test]
fn pose_feed_is_refused_until_tracking_is_wired() {
    assert_eq!(exit_code(&["--pose-feed", "xrld"]), Some(2));
}

#[test]
fn depth_fd_is_refused_until_immersive_is_wired() {
    assert_eq!(exit_code(&["--depth-fd", "5"]), Some(2));
}

/// `--continuity-fd` is taken (tests/continuity_launch.rs runs it); a value spike/src/main.cpp refuses still exits 2
/// before any descriptor is touched: not a number, or another flag's descriptor.
#[test]
fn continuity_fd_refuses_what_the_cpp_refuses() {
    assert_eq!(exit_code(&["--continuity-fd", "x"]), Some(2));
    assert_eq!(exit_code(&["--continuity-fd", "3"]), Some(2));
    assert_eq!(exit_code(&["--continuity-fd", "4"]), Some(2));
}
