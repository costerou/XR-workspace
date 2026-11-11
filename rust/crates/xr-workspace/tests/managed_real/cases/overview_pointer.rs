//! `view_controls::overview_pointer`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! The module is held to the Python's on its own by tests/overview_pointer_run.rs (level 1). Here the whole run: both
//! supervisors start the sampler with the run's bind helper, the cursor moves onto XR-1, and Stop. The sampler's
//! clocked reads and its Lua are compared apart from the loop's trace (`set_aside_sampler` in managed_real.rs), and the
//! sampler is stopped (its binds removed, the cursor shown) before the view binds go. Until the view is ported neither
//! run has the overview's geometry, so the dot never enters a desktop here: the sampler reads the cursor on XR-1 and
//! keeps the overview cursor's state; tests/overview_pointer_run.rs covers the entry.

use std::fs;
use std::time::Duration;

use serde_json::Value;

use super::{Session, Who, parity_acting};

/// The cursor moves onto XR-1 (`reply_overview_pointer`), and the sampler has a second and a half to enter it.
fn cursor_onto_xr1(s: &Session, _: Who) -> Value {
    fs::write(s.root.join("run/fake-cursor-xr"), "").unwrap();
    std::thread::sleep(Duration::from_millis(1500));
    Value::Null
}

#[test]
fn the_sampler_runs_with_the_cursor_on_xr1_and_stops_before_the_view_binds_go() {
    let (run, _, _) = parity_acting("stop", Some(cursor_onto_xr1));
    // The sampler's Lua is set aside only up to the view binds' removal: none is left in the trace after it.
    let sampler_lua = ["xr_workspace_overview_cursor", "xr_workspace_resize_binds"];
    let remove = format!("/eval {}", xr_workspace::lua::view::remove_binds_lua());
    assert!(run.trace.contains(&remove), "the view binds were removed");
    assert!(
        !run.trace.iter().any(|r| *r != remove && sampler_lua.iter().any(|m| r.contains(m))),
        "sampler.stop() before remove_view_binds: {:?}",
        run.sampler
    );
    assert!(run.sampler.iter().any(|r| r == "read j/cursorpos"), "{:?}", run.sampler);
    assert!(
        run.sampler.iter().any(|r| r.contains("xr_workspace_overview_cursor")),
        "the sampler's cursor Lua: {:?}",
        run.sampler
    );
    assert_eq!(run.result["stop_reason"], "interrupted");
}
