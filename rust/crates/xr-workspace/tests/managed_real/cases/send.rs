//! `view_controls::send`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md):
//! window sending through each run's own key helper, the control socket and the view's routing. `reply_send` in
//! `compositor.py` answers the moves and puts the one window where each move sends it.
//!
//! 1. Ctrl+Alt+G (`send_glasses`, XR-1 selected): `j/activewindow`, `j/monitors all`, then
//!    `/dispatch hl.dsp.window.move({ workspace = "61", follow = true, window = "address:0x1" })`.
//! 2. Ctrl+Alt+B (`send_back`): the same two reads, then the move to workspace 1 (DP-1, the fallback).
//! 3. Stop: the sender is closed first in `exit`, so no send request comes after recovery begins. (The run binds are
//!    the `binds` port's: neither run installs or removes them yet, so recovery's first removal is the mark.)

use std::time::Duration;

use serde_json::{Value, json};

use super::{Session, Who, parity_acting};

fn send_and_back(s: &Session, who: Who) -> Value {
    // The window memory's capture a second after adoption (`window_memory::CAPTURE_DELAY`) first: the keys never race it.
    std::thread::sleep(Duration::from_millis(1500));
    let glasses = s.control(who, "send_glasses", &[]);
    std::thread::sleep(Duration::from_millis(300)); // the worker's move done before the next
    let back = s.control(who, "send_back", &[]);
    std::thread::sleep(Duration::from_millis(300));
    json!([glasses, back])
}

#[test]
fn a_window_goes_to_the_glasses_and_back_and_the_sender_closes_before_recovery() {
    let (run, _, [python, rust]) = parity_acting("stop", Some(send_and_back));
    assert_eq!(python, rust);
    assert_eq!(rust, json!([0, 0]), "a run listened");
    let moves: Vec<(usize, &String)> = run
        .trace
        .iter()
        .enumerate()
        .filter(|(_, r)| r.starts_with("/dispatch hl.dsp.window.move("))
        .collect();
    let workspaces: Vec<bool> = moves
        .iter()
        .map(|(_, r)| r.contains(r#"workspace = "61""#))
        .collect();
    assert_eq!(workspaces, [true, false], "to XR-1, then back: {moves:?}");
    assert!(
        moves[1].1.contains(r#"workspace = "1""#),
        "back to DP-1's workspace: {}",
        moves[1].1
    );
    let recovery = run
        .trace
        .iter()
        .position(|r| r.contains("\"op\":\"evacuate_remove\""))
        .unwrap_or_else(|| panic!("recovery removed the outputs: {:?}", run.trace));
    assert!(
        moves.iter().all(|(i, _)| *i < recovery),
        "no move after recovery began: {:?}",
        run.trace
    );
    assert!(
        run.frames.iter().all(|f| !f.contains("send_")),
        "a move sends the renderer nothing"
    );
}
