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

/// `--pose-feed` is taken (the capture_e2e_rust_tracked and _feed_drop gates run it), as spike/src/main.cpp checks it:
/// XRLinuxDriver's feed only, and with capture only.
#[test]
fn pose_feed_takes_xrld_with_capture_only() {
    assert_eq!(exit_code(&["--pose-feed", "luma"]), Some(2));
    let base = ["--supervised-fd", "3", "--wayland-fd", "4", "--output", "DP-1", "--run-id", "r", "--session", "s"];
    let status = Command::new(env!("CARGO_BIN_EXE_xr-renderer"))
        .args(base)
        .args(["--pose-feed", "xrld"])
        .status()
        .expect("run xr-renderer");
    assert_eq!(status.code(), Some(2));
}

/// `--depth-fd` is taken (shell/immersive.rs), checked as spike/src/main.cpp checks it: a number, and none of the other
/// descriptors. Whether the memory is a valid XRD1 block is the depth link's to find out, at the first 3D frame.
#[test]
fn depth_fd_must_be_a_descriptor_of_its_own() {
    assert_eq!(exit_code(&["--depth-fd", "x"]), Some(2));
    assert_eq!(exit_code(&["--depth-fd", "-1"]), Some(2));
    assert_eq!(exit_code(&["--depth-fd", "3"]), Some(2)); // the control socket
    assert_eq!(exit_code(&["--depth-fd", "4"]), Some(2)); // the Wayland socket
}

/// `--continuity-fd` is taken (tests/continuity_launch.rs runs it); a value spike/src/main.cpp refuses still exits 2
/// before any descriptor is touched: not a number, or another flag's descriptor.
#[test]
fn continuity_fd_refuses_what_the_cpp_refuses() {
    assert_eq!(exit_code(&["--continuity-fd", "x"]), Some(2));
    assert_eq!(exit_code(&["--continuity-fd", "3"]), Some(2));
    assert_eq!(exit_code(&["--continuity-fd", "4"]), Some(2));
}
