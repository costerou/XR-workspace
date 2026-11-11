//! `view_controls::overview_pointer`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! The module is ported and held to the Python's on its own by tests/overview_pointer_run.rs (level 1). The run case
//! below waits on two things:
//!
//! * the `binds` port: the sampler binds the left button with the bind helper's command line
//!   (`binds::Binds::bind_args`), and until `Binds::open` gives one the Rust run starts no sampler;
//! * a trace for a sampler: the Python's and the Rust's sampler threads read the cursor on a clock, in the run's own
//!   process, between the loop's requests. The trace then holds a timing-dependent number of `j/cursorpos` (and
//!   `j/xrspike` `cursor_shape`) reads, interleaved with the loop's. `observe` (managed_real.rs, the `socket` port's)
//!   has to take the sampler's requests out of the trace and compare them as tests/overview_pointer_run.rs does: the
//!   calls that are not reads, in order, and the distinct reads.
//!
//! oracle.py's `overview_pointer` entry stays until then: without it every Python case would sample (the unified
//! sampler reads the cursor outside the overview too), and no Rust case would. Once both land, drop the `ignore` and
//! delete the entry in the same commit.

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
#[ignore = "needs the binds port's bind_args and a sampler-aware trace in managed_real.rs; see the module doc"]
fn the_dot_over_a_desktop_enters_it_and_the_binds_go_before_the_view_binds() {
    let (run, _, _) = parity_acting("stop", Some(cursor_onto_xr1));
    let lua = |needle: &str| run.trace.iter().rposition(|r| r.starts_with("/eval ") && r.contains(needle));
    let resize = lua("xr_workspace_resize_binds = nil").expect("the sampler's binds were removed");
    let shown = lua("xr_workspace_overview_cursor = nil").expect("the cursor was shown");
    let view = run
        .trace
        .iter()
        .rposition(|r| r.strip_prefix("/eval ") == Some(&xr_workspace::lua::view::remove_binds_lua()))
        .expect("the view binds were removed");
    assert!(resize < view && shown < view, "sampler.stop() before remove_view_binds");
    assert!(
        run.trace.iter().any(|r| r.contains("xr_workspace_resize_binds = t") && r.contains("control.sock")),
        "the sampler bound the left button with the run's helper"
    );
    assert_eq!(run.result["stop_reason"], "interrupted");
}
