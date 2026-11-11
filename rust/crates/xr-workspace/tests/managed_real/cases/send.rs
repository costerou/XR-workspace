//! `view_controls::send`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md). None
//! yet: the sender is ported (`tests/send_corpus.rs` holds it to the Python `WindowSender`), but its ops reach it only
//! through the control socket and the view, which are stubs, and the oracle still takes the whole `view_controls` out.
//!
//! The case, once the `socket` port's helpers land (a case action that sends a control frame once the renderer is up):
//!
//! 1. Ctrl+Alt+G (`send_glasses`, XR-1 selected): both send `j/activewindow`, `j/monitors all`, then
//!    `/dispatch hl.dsp.window.move({ workspace = "61", follow = true, window = "address:0x1" })`. `reply_send` in
//!    `compositor.py` answers it and puts the window on XR-1.
//! 2. Ctrl+Alt+B (`send_back`): the same two reads, then the move to workspace 1 (DP-1, the fallback).
//! 3. Stop: no send request after the run's first `remove_view_binds` eval in the trace (the sender closes first).
//!
//! Then delete `send.WindowSender`'s line from `oracle.py` in the same commit.
