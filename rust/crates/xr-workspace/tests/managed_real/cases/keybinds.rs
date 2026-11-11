//! `view_controls::keybinds`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! The module is ported and held to the Python's on its own by tests/keybinds_run.rs (level 1). The run case below holds
//! the panel's start to the Python's: the desktop's binds read first, the run's binds installed without the one a
//! desktop bind holds. Once the renderer is up it presses Ctrl+Alt+K through each run's own helper, then Stops the run;
//! `parity` compares the renderer frames that follow (none while the view is a stub on both sides: the view routes
//! the panel's ops) and the removal.

use super::*;

/// The `/eval` that installs the run's view binds (`xr_workspace_binds = { ... }`), from the run's own trace.
fn view_binds_install(trace: &[String]) -> Option<&String> {
    trace
        .iter()
        .find(|r| r.starts_with("/eval ") && r.contains("xr_workspace_binds = {"))
}

/// `compositor.py`'s `keybinds` case: a described desktop bind holds Ctrl+Alt+P (`reply_keybinds`). Both runs read
/// the desktop's binds before they install their own, and leave the prediction toggle out.
/// Ctrl+Alt+K, through `who`'s own key helper: its exit status (0: the run took it).
fn press_ctrl_alt_k(s: &Session, who: Who) -> Value {
    json!(s.control(who, "binds_toggle", &[]))
}

#[test]
fn a_desktop_bind_on_a_run_key_leaves_that_bind_out() {
    let (run, _, acted) = parity_acting("keybinds", Some(press_ctrl_alt_k));
    assert_eq!(acted, [json!(0), json!(0)], "both runs took Ctrl+Alt+K");
    let read = run.trace.iter().position(|r| r == "binds");
    let install = view_binds_install(&run.trace);
    let read = read.expect("the desktop's binds were read");
    let at = run
        .trace
        .iter()
        .position(|r| Some(r) == install)
        .expect("the view binds were installed");
    assert!(read < at, "the desktop's binds are read first");
    let install = install.unwrap();
    assert!(!install.contains("\"CTRL + ALT + P\""), "{install:.400}");
    assert!(!install.contains("\"prediction\""), "{install:.400}");
    assert!(install.contains("\"zoom_in\""), "{install:.400}");
}
