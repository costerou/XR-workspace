//! `view_controls::desktops`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! The manager is ported and held to the Python's on its own by tests/desktops_corpus.rs (level 1: every action, the
//! reset, a refused create, remove and reshape, and the view's side of a change on a stand-in view). Its ops reach it
//! only through the view: `menu_toggle` opens the selected desktop's panel and `menu_add` asks for the change
//! (`view.MENU_DESKTOP_OPS`), `layout_reset` is Ctrl+Alt+R. Until the `view` port implements
//! `view_controls::desktops::View` on `ViewControl` (and routes `MENU_OPS` and `RESET_OP` to the manager), the Rust run
//! takes neither, so these cases are ignored; once it does, drop the `ignore` and delete `desktops`'s line from
//! oracle.py's `STUBS` in the same commit (the reset's window mover then runs on both sides).

use super::*;

/// Opens the selected desktop's panel and presses its Add (N); the change runs on the desktops worker, and the
/// renderer gets the new desktop set (`desktops` frame, compared by `parity`) when it lands.
fn add(s: &Session, who: Who) -> Value {
    let sent = [
        s.control(who, "menu_toggle", &[]),
        s.control(who, "menu_add", &[]),
    ];
    std::thread::sleep(Duration::from_secs(2));
    json!(sent)
}

fn reset_twice(s: &Session, who: Who) -> Value {
    let sent = [
        s.control(who, "layout_reset", &[]),
        s.control(who, "layout_reset", &[]),
    ];
    std::thread::sleep(Duration::from_secs(2)); // the ease, then the (empty) plan
    json!(sent)
}

/// A pill's Add mid-run: the same `create` through the broker (journaled write-ahead), the same `desktops` frame to the
/// renderer, and recovery removes XR-4 with the others. (A later renderer's `--capture` of XR-4 needs a relaunch after
/// the change: `RealHost::launch` takes `ViewControls::live_specs`, held to `manager.specs` by desktops_corpus.rs.)
#[test]
#[ignore = "needs the view port: ViewControl implementing desktops::View and routing MENU_OPS; see the module doc"]
fn menu_add_creates_xr4_through_the_broker() {
    let (run, _, sent) = parity_acting("stop", Some(add));
    assert_eq!(sent[0], sent[1]);
    let creates = run
        .trace
        .iter()
        .filter(|r| r.contains("\"op\":\"create\""))
        .count();
    assert_eq!(creates, 4, "three at the start, one from the pill");
    assert!(
        run.frames.iter().any(|f| f.contains("XR-4")),
        "{:?}",
        run.frames
    );
}

/// Ctrl+Alt+R twice on the default three: the ease, no broker step, the default saved.
#[test]
#[ignore = "needs the view port: ViewControl implementing desktops::View and routing RESET_OP; see the module doc"]
fn layout_reset_twice_on_the_default_changes_no_output() {
    let (run, _, sent) = parity_acting("stop", Some(reset_twice));
    assert_eq!(sent[0], sent[1]);
    assert!(!run.trace.iter().any(|r| r.contains("\"op\":\"reshape\"")));
}

/// A pill's Add the broker does not make live: both runs say so, keep three desktops and leave the same journal (its
/// record pending, for recovery).
#[test]
#[ignore = "needs the view port: ViewControl implementing desktops::View and routing MENU_OPS; see the module doc"]
fn a_refused_create_leaves_the_same_journal() {
    let (run, _, sent) = parity_acting("desktops-refused", Some(add));
    assert_eq!(sent[0], sent[1]);
    assert!(run.journal.is_some());
}
