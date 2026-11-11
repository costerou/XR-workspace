//! Twin of the C++ ctest `native_help` (`xr-native-spike --help` exits 0): the renderer's `--help` prints its usage
//! and exits 0, touching no descriptor. Since every feature is wired, the usage lists nothing as missing.

use std::process::Command;

fn help() -> (Option<i32>, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_xr-renderer")).arg("--help").output().expect("run xr-renderer");
    (out.status.code(), String::from_utf8(out.stdout).expect("utf-8 usage"))
}

#[test]
fn help_exits_zero_with_the_usage() {
    let (code, usage) = help();
    assert_eq!(code, Some(0));
    assert!(usage.starts_with("xr-renderer: "), "{usage}");
    assert!(usage.contains("--supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION"));
    assert!(usage.ends_with('\n'));
}

#[test]
fn help_lists_nothing_as_not_yet_in_this_build() {
    let (_, usage) = help();
    assert!(!usage.to_lowercase().contains("not yet in this build"), "{usage}");
    assert!(!usage.contains("receipted but not drawn"), "{usage}");
}
