//! `view_controls::binds`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! The module is held to the Python's on its own by tests/binds_run.rs (level 1: the reload, the panel's and typing
//! mode's work). Here, the whole run: both read the session mode and install the binds at the start, with the same
//! `/eval`s, and remove them before recovery puts the windows back. A config reload during the run (the view's event
//! stream, `on_events`) reinstalls them and parks the glasses again.

use std::fs;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use super::{Session, Who, parity, parity_acting};

/// The view binds' install (`xr_workspace_binds = { ... }`) and their removal, in a run's trace.
fn installs(trace: &[String]) -> Vec<usize> {
    positions(trace, |r| {
        r.starts_with("/eval ") && r.contains("xr_workspace_binds = {")
    })
}

fn removals(trace: &[String]) -> Vec<usize> {
    positions(trace, |r| {
        r.starts_with("/eval for _, b in ipairs(xr_workspace_binds or {})")
            && r.contains("xr_workspace_binds = nil")
            && !r.contains("xr_workspace_binds = {")
    })
}

fn positions(trace: &[String], pick: impl Fn(&str) -> bool) -> Vec<usize> {
    trace
        .iter()
        .enumerate()
        .filter(|(_, r)| pick(r))
        .map(|(i, _)| i)
        .collect()
}

#[test]
fn the_binds_go_in_at_the_start_and_out_before_recovery() {
    let (run, _) = parity("stop");
    let install = installs(&run.trace);
    assert_eq!(install.len(), 1, "one install: {install:?}");
    let install = install[0];
    assert!(
        run.trace[install].contains(
            "/usr/bin/python3 -I HELPER ROOT/run/xr-workspace-spike/r1/control.sock r1 SESSION"
        ),
        "the bind helper's command line: {:.300}",
        run.trace[install]
    );
    // The session mode is read in view_controls' body, before the key bind panel reads the desktop's binds.
    let read = run
        .trace
        .iter()
        .position(|r| r == "binds")
        .expect("the desktop's binds read");
    assert_eq!(
        run.trace[read - 1],
        "j/monitors all",
        "the session mode read first"
    );
    assert!(read < install);
    let removal = removals(&run.trace);
    assert_eq!(removal.len(), 1, "one removal: {removal:?}");
    let unpin = run
        .trace
        .iter()
        .position(|r| r.starts_with("/eval for _,w in ipairs(hl.get_windows())"))
        .expect("recovery's unpin");
    assert!(
        install < removal[0] && removal[0] < unpin,
        "out before recovery"
    );
}

/// A config reload once the renderer is up (`compositor.py`'s `events_binds`), then the reinstall waited for.
fn reload(s: &Session, _: Who) -> Value {
    fs::write(s.root.join("run/fake-config-reload"), "").unwrap();
    let end = Instant::now() + Duration::from_secs(5);
    let reinstalled = loop {
        let wire = s.wire();
        let trace: Vec<String> = wire
            .iter()
            .filter_map(|e| e["request"].as_str().map(str::to_owned))
            .collect();
        if installs(&trace).len() >= 2 {
            break true;
        }
        if Instant::now() > end {
            break false;
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    json!({"reinstalled": reinstalled})
}

#[test]
fn a_config_reload_reinstalls_the_binds_and_parks_the_glasses() {
    let (run, _, acted) = parity_acting("binds-reload", Some(reload));
    assert_eq!(acted[0], acted[1]);
    assert_eq!(acted[0], json!({"reinstalled": true}));
    let install = installs(&run.trace);
    assert_eq!(install.len(), 2, "the start's install and the reload's");
    let parked = run.trace[install[1]..]
        .iter()
        .position(|r| r.starts_with("/eval hl.monitor({ output = \"DP-2\""));
    assert!(parked.is_some(), "the reload parks the glasses again");
}
