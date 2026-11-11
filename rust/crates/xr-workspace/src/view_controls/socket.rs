//! The run's private control socket: `ipc.PrivateServer` (tools/native_spike/ipc.py), and the loop's side of it in
//! tools/native_spike/supervisor.py (`control_request`, `serve_without_renderer`, `Supervisor._control_ready`).
//!
//! `$XDG_RUNTIME_DIR/xr-workspace-spike/<run_id>/control.sock`, one frame per connection, the peer's UID checked, the
//! frames queued per run and session (`xr_wire::run_control::{socket_path, Frame, Queue}`). A `status` is answered on
//! the connection with [`super::view::ViewControl::status_reply`] (`xr_wire::run_control::StatusReply`); any other op
//! goes back to the loop for [`super::ViewControls::on_message`]. Never fatal: a bad peer, frame or replay is dropped.
//!
//! Not ported: [`Server::open`] gives `None`, so the loop waits on no socket and the key helper (`xr-control`) finds no
//! run listening (its fallback Lua runs, as for a run without view controls).

use std::os::fd::RawFd;
use std::time::Duration;

use xr_wire::run_control::Frame;

use super::Run;
use super::view::ViewControl;

/// `ipc.PrivateServer`: the bound socket, its path and its frame queue.
pub struct Server {
    _private: (),
}

impl Server {
    /// `PrivateServer(client.runtime_dir, run_id, session)`.
    pub fn open(_run: &Run<'_>) -> Option<Server> {
        None
    }

    /// `server.path`: the bind helper's first argument (`bind_args`).
    pub fn path(&self) -> &std::path::Path {
        std::path::Path::new("")
    }

    /// `server.socket`, for the loop's poll.
    pub fn fd(&self) -> RawFd {
        -1
    }

    /// `control_request(control, status)`: accept one connection, read one frame; answer a `status` here
    /// (`view.status_reply`), give any other op back.
    pub fn control_request(&mut self, _view: Option<&ViewControl>) -> Option<Frame> {
        None
    }

    /// `serve_without_renderer(control, seconds)`: wait up to `seconds` for one request; a `status` is answered with
    /// an empty measured status, a view op is dropped.
    pub fn serve_without_renderer(&mut self, _view: Option<&mut ViewControl>, seconds: Duration) {
        std::thread::sleep(seconds);
    }

    /// `server.close()`: the socket file removed.
    pub fn close(&mut self) {}
}
