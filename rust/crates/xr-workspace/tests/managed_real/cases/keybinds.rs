//! `view_controls::keybinds`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! The module is ported and held to the Python's on its own by tests/keybinds_run.rs (level 1). The run case below
//! waits on the `socket`, `view` and `binds` ports: until then the Rust run installs no binds (so it opens no panel)
//! and oracle.py replaces all of `view_controls`. The case also needs the `socket` port's case actions: the fake
//! renderer ends by itself only in `success`, so a `keybinds` run needs a Stop once the renderer is up (and Ctrl+Alt+K
//! frames need the control-frame action). Once they land and oracle.py runs the shipped `view_controls`, drop the
//! `ignore`, and delete the `keybinds.BindControl` patch from oracle.py in the same commit.

use super::*;

/// The `/eval` that installs the run's view binds (`xr_workspace_binds = { ... }`), from the run's own trace.
fn view_binds_install(trace: &[String]) -> Option<&String> {
    trace
        .iter()
        .find(|r| r.starts_with("/eval ") && r.contains("xr_workspace_binds = {"))
}

/// `compositor.py`'s `keybinds` case: a described desktop bind holds Ctrl+Alt+P (`reply_keybinds`). Both runs read
/// the desktop's binds before they install their own, and leave the prediction toggle out.
#[test]
#[ignore = "needs the socket, view and binds ports and the socket's case actions; see the module doc"]
fn a_desktop_bind_on_a_run_key_leaves_that_bind_out() {
    let (run, _) = parity("keybinds");
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
