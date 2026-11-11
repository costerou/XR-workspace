//! `view_controls::socket`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md): the
//! panel's `status` on the run's private control socket, answered the same by both supervisors while the renderer
//! runs (`Supervisor._control_ready`) and while a USB drop holds the run (`serve_without_renderer`), and a key's op
//! sent through each run's own key helper reaching the run (both helpers exit 0: a run listened).
//!
//! The answer's body is the view's (`status_reply`), stubbed on both sides until `view` is ported (oracle.py), so
//! the replies are the four header keys today; the view's port makes them whole without changing these cases.

use std::fs;
use std::time::Duration;

use serde_json::{Value, json};

use super::{Session, Who, parity_acting, wait_for};

fn status_during_the_run(s: &Session, who: Who) -> Value {
    let status = s.status(Duration::from_secs(5));
    let helper = s.control(who, "recenter", &[]);
    json!({"status": status, "helper": helper})
}

#[test]
fn the_status_and_a_key_while_the_renderer_runs() {
    let (run, _, [python, rust]) = parity_acting("stop", Some(status_during_the_run));
    assert_eq!(python, rust);
    assert_eq!(rust["helper"], 0, "a run listened");
    assert_eq!(
        rust["status"],
        json!({"schema": 1, "run_id": "r1", "session": "SESSION"})
    );
    assert_eq!(run.result["stop_reason"], "interrupted");
}

/// The glasses go (the fake renderer ends as a USB drop ends it); the status during the hold; the glasses back, the
/// renderer again; the status during that run. Then Stop.
fn status_through_a_hold(s: &Session, _: Who) -> Value {
    let run = s.root.join("run");
    assert!(wait_for(
        &run.join("fake-glasses-gone"),
        Duration::from_secs(10)
    ));
    std::thread::sleep(Duration::from_secs(1)); // the renderer reaped, the hold begun
    let held = s.status(Duration::from_secs(5));
    fs::remove_file(run.join("renderer-started")).unwrap();
    fs::remove_file(run.join("fake-glasses-gone")).unwrap();
    // survive.SETTLE_S of the glasses back, then the next renderer.
    assert!(wait_for(
        &run.join("renderer-started"),
        Duration::from_secs(20)
    ));
    std::thread::sleep(Duration::from_millis(300));
    let back = s.status(Duration::from_secs(5));
    json!({"held": held, "back": back})
}

#[test]
fn the_status_during_a_hold_and_after_it() {
    let (run, _, [python, rust]) = parity_acting("hold", Some(status_through_a_hold));
    assert_eq!(python, rust);
    assert!(
        rust["held"].is_object() && rust["back"].is_object(),
        "{rust}"
    );
    assert_eq!(run.renderers.len(), 2, "a second renderer after the hold");
    assert_eq!(run.result["stop_reason"], "interrupted");
}
