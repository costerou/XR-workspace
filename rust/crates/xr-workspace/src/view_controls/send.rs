//! Window sending (Ctrl+Alt+G, Ctrl+Alt+1-4, Ctrl+Alt+B): `send.WindowSender` (tools/native_spike/send.py:
//! `request`, `close`, the worker that runs one move at a time off the loop) and `send.notify`, as
//! `managed.view_controls` builds it (`WindowSender(client, capture, fallback)`, `control.notify = notify_text`) and
//! closes it (`control.sender.close()`, before privacy, 3D and the binds).
//!
//! The plan and the Lua are ported (`crate::send`, `crate::lua::send`). What is left is the worker: its own compositor
//! client, the queue, and `close` letting a move in flight land before recovery.
//!
//! Not ported: [`Sender::open`] gives `None`; the send keys do nothing.

use super::Run;

/// `send.WindowSender`.
pub struct Sender {
    /// What [`Sender::request`] was given, in a [`Sender::recording`] sender (the routing tests).
    recorded: Option<Vec<(String, String)>>,
}

impl Sender {
    /// `WindowSender(client, capture, fallback)`.
    pub fn open(_run: &Run<'_>) -> Option<Sender> {
        None
    }

    /// A sender that only records its requests.
    pub fn recording() -> Sender {
        Sender {
            recorded: Some(Vec::new()),
        }
    }

    /// The requests a [`Sender::recording`] sender took.
    pub fn requests(&self) -> &[(String, String)] {
        self.recorded.as_deref().unwrap_or_default()
    }

    /// `sender.request(op, selected)`: queued for the worker; never blocks the loop.
    pub fn request(&mut self, op: &str, selected: &str) {
        if let Some(recorded) = self.recorded.as_mut() {
            recorded.push((op.to_owned(), selected.to_owned()));
        }
    }

    /// `sender.close()`: no move starts after this; one in flight lands first.
    pub fn close(&mut self) {}
}
