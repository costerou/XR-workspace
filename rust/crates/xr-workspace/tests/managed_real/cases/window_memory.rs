//! `view_controls::window_memory`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! `window-memory`: a `window-memory.json` beside the session's `profiles.json`, written before the run, remembers the
//! fallback's window 0x1 on XR-2. Both supervisors put it back with the same `place_windows` Lua before adoption, and
//! after Stop leave the same file: the last capture, taken while XR-2 still holds the window (`compositor.py`'s
//! `reply_window_memory` moves it there when the placement lands).

use std::fs;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{Value, json};
use xr_workspace::window_memory::{Store, capture};

use super::{INSTANCE, Session, Who, diff, observe, wait_bounded, wait_for};

const CASE: &str = "window-memory";

fn memory_file(s: &Session) -> PathBuf {
    s.root.join("config/viture-workspace/window-memory.json")
}

/// The memory a run left with 0x1 on XR-2, 20 px further in than the compositor will show it.
fn remember(s: &Session) {
    let monitor = |name: &str, x: i64, active: i64| {
        json!({"name": name, "x": x, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
               "activeWorkspace": {"id": active, "name": active.to_string()}})
    };
    let monitors = json!([monitor("DP-1", 0, 1), monitor("XR-1", 3840, 61), monitor("XR-2", 5760, 71)]);
    let workspaces = json!([{"id": 71, "name": "71", "monitor": "XR-2", "windows": 1}]);
    let clients = json!([{"address": "0x1", "workspace": {"id": 71, "name": "71"}, "at": [5790, 30],
                          "size": [800, 600], "class": "kitty", "title": "t", "initialTitle": "t", "pid": 4242,
                          "floating": false, "mapped": true, "pinned": false, "fullscreen": 0, "fullscreenClient": 0}]);
    let memory = capture(&clients, &workspaces, &monitors, &["XR-1", "XR-2"], INSTANCE, None).unwrap();
    assert_eq!(memory["windows"].as_array().map(Vec::len), Some(1), "{memory}");
    let path = memory_file(s);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    Store::new(&path, "luma-ultra").unwrap().save(memory).unwrap();
}

/// One supervisor's run on the case, stopped (SIGINT) once the renderer is up, as `Session::run`'s `stop`.
fn stopped(who: Who) -> (super::Observed, Value, Value, i32, String) {
    let s = Session::new(CASE, who);
    remember(&s);
    let before: Value = serde_json::from_slice(&fs::read(memory_file(&s)).unwrap()).unwrap();
    let child = s.runner(who).spawn().unwrap();
    if wait_for(&s.root.join("run/renderer-started"), Duration::from_secs(20)) {
        std::thread::sleep(Duration::from_millis(300));
        unsafe { libc::kill(child.id() as i32, libc::SIGINT) };
    }
    let out = wait_bounded(child, Duration::from_secs(40));
    let after: Value = serde_json::from_slice(&fs::read(memory_file(&s)).unwrap()).unwrap();
    let (observed, _) = observe(&s);
    (
        observed,
        before,
        after,
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

#[test]
fn the_remembered_window_goes_back_and_the_last_capture_is_saved() {
    let (python, py_before, py_after, py_code, py_err) = stopped(Who::Python);
    let (rust, rs_before, rs_after, rs_code, rs_err) = stopped(Who::Rust);
    let context = format!("python stderr:\n{py_err}\nrust stderr:\n{rs_err}");
    assert_eq!(
        python.trace,
        rust.trace,
        "{CASE}: the requests differ\n{}\n{context}",
        diff(&python.trace, &rust.trace)
    );
    let placed = |t: &[String]| t.iter().filter(|r| r.starts_with("/eval ") && r.contains("{{1,\"XR-2\",")).count();
    assert!(placed(&rust.trace) > 0, "the placement went out: {:#?}", rust.trace);
    assert_eq!(python.journal, rust.journal, "journal: {context}");
    assert_eq!(python.layout, rust.layout, "layout snapshot: {context}");
    assert_eq!(python.result, rust.result, "result: {context}");
    assert_eq!(python.renderers, rust.renderers, "renderer arguments: {context}");
    assert_eq!(py_code, rs_code, "exit status: {context}");
    assert_eq!(rust.result["stop_reason"], "interrupted");
    assert_eq!(py_before, rs_before);
    assert_eq!(py_after, rs_after, "the memory after Stop: {context}");
    let window = &rs_after["models"]["luma-ultra"]["windows"][0];
    assert_eq!(
        (&window["output"], &window["at"]),
        (&json!("XR-2"), &json!([10, 10])),
        "the last capture replaced the old memory: {rs_after}"
    );
    assert_ne!(rs_before, rs_after);
}
