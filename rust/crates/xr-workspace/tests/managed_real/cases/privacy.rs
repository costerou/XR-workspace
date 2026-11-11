//! `view_controls::privacy`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//! `reply_privacy` in compositor.py keeps each physical monitor's `dpmsStatus` and applies privacy.py's Lua to it.
//!
//! - Recovery's wake: the fallback goes DPMS-off mid-run (as privacy mode, or a crash in it, leaves it), the guard
//!   tolerates that, and after Stop both supervisors' recovery turns it back on before it removes the desktops. (A
//!   run cannot start with it asleep: both refuse, "presentation requires an independent usable desktop".)
//!
//! Not yet: Ctrl+Alt+D during the run (DP-1 blanked, the glasses kept on, DP-1 woken in `exit` before the binds go),
//! which needs the `socket` port's control-frame action and the shipped `view_controls` in oracle.py, minus its
//! `privacy.PrivacyDisplay` patch (VIEW_CONTROLS.md, "Narrowing the oracle"). Until then the Python side builds no
//! `PrivacyDisplay`, and the Rust one builds it and, with no press, sends nothing.

use super::*;

/// A run stopped as `stop` is (SIGINT 300 ms after the renderer is up), with DP-1 put DPMS-off before the Stop.
fn asleep(who: Who) -> (Session, i32, String) {
    let s = Session::new("privacy-asleep", who);
    let child = s.runner(who).spawn().unwrap();
    if wait_for(
        &s.root.join("run/renderer-started"),
        Duration::from_secs(20),
    ) {
        fs::write(s.root.join("run/fake-privacy-asleep"), "").unwrap();
        std::thread::sleep(Duration::from_millis(300));
        unsafe { libc::kill(child.id() as i32, libc::SIGINT) };
    }
    let out = wait_bounded(child, Duration::from_secs(40));
    let err = String::from_utf8_lossy(&out.stderr).into_owned();
    (s, out.status.code().unwrap_or(-1), err)
}

#[test]
fn recovery_wakes_a_fallback_left_asleep() {
    let (py, py_code, py_err) = asleep(Who::Python);
    let (python, _) = observe(&py);
    drop(py);
    let (rs, rs_code, rs_err) = asleep(Who::Rust);
    let (rust, _) = observe(&rs);
    let context = format!("python stderr:\n{py_err}\nrust stderr:\n{rs_err}");
    assert_eq!(
        python.trace,
        rust.trace,
        "the requests differ\n{}\n{context}",
        diff(&python.trace, &rust.trace)
    );
    assert_eq!(python.journal, rust.journal, "journal: {context}");
    assert_eq!(python.layout, rust.layout, "layout snapshot: {context}");
    assert_eq!(python.result, rust.result, "result: {context}");
    assert_eq!(python.renderers, rust.renderers, "renderers: {context}");
    assert_eq!(py_code, rs_code, "exit status: {context}");
    assert_eq!(rust.result["stop_reason"], "interrupted", "{context}");
    assert_eq!(rust.result["complete"], true, "{context}");
    let wake = rust
        .trace
        .iter()
        .position(|r| r.contains(r#"local t="DP-1""#) && r.contains("hl.dsp.dpms("))
        .unwrap_or_else(|| panic!("recovery woke DP-1: {:?}", rust.trace));
    let restore = rust
        .trace
        .iter()
        .rposition(|r| r.contains("evacuate_remove"))
        .unwrap_or_else(|| panic!("recovery removed the outputs: {:?}", rust.trace));
    assert!(
        wake < restore,
        "DP-1 wakes before recovery: {:?}",
        rust.trace
    );
}
