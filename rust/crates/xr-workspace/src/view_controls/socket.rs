//! The run's private control socket: `ipc.PrivateServer` (tools/native_spike/ipc.py), and the loop's side of it in
//! tools/native_spike/supervisor.py (`control_request`, `serve_without_renderer`, `Supervisor._control_ready`).
//!
//! `$XDG_RUNTIME_DIR/xr-workspace-spike/<run_id>/control.sock`, one frame per connection, the peer's UID checked, the
//! frames queued per run and session (`xr_wire::run_control::{socket_path, Frame, Queue}`). A `status` is answered on
//! the connection with the body the caller gives ([`super::view::ViewControl::status_reply`], in a
//! `xr_wire::run_control::StatusReply`); any other op goes back to the loop for [`super::ViewControls::on_message`].
//! Never fatal: a bad peer, frame or replay is dropped, and so is a connection that stalls or closes early.
//!
//! As in Python:
//! - The runtime directory must be private already; `xr-workspace-spike` and `<run_id>` are made 0700 when missing
//!   and must be private (`ipc.private_dir`). The socket is bound through `/proc/self/fd/<dir>/`, made 0600, and an
//!   existing file there is never replaced: the bind fails, and so does the run's start of its view controls.
//! - A read takes what has arrived only up to its newline (`MSG_PEEK`); what follows stays unread. Each read waits
//!   at most 0.25 s, as does the reply's write.
//! - The wire's checks are `xr_wire`'s, with its one documented difference: the payloads another module owns
//!   (`options_*`, `video_*`, `environment`, `immersive`, `placement`, `stabilization`) are that module's to check, so
//!   a frame Python's `decode_control` refuses for its payload alone is taken here (and its request id with it).
//! - On close the socket file is unlinked only while it is still the socket this server bound.
//!
//! `tests/native_spike/control_server_corpus.py` records the Python's `control_request` and `serve_without_renderer`
//! on the cases of `rust/corpus/sockets/frames.json` and a few of its own (`rust/corpus/sockets/server.json`);
//! `tests/control_server_corpus.rs` replays them here.

use std::fs::{DirBuilder, File};
use std::io::{self, ErrorKind, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

use rustix::event::{PollFd, PollFlags, Timespec, poll};
use xr_wire::json::Value;
use xr_wire::run_control::{Frame, MAX_FRAME, Queue, StatusReply};

use super::Run;
use crate::private_file::{invalid, private_dir};

/// `conn.settimeout(0.25)`: each read of a request, and the reply's write.
const IO_TIMEOUT: Duration = Duration::from_millis(250);

/// A `status` answer's body, after its four header keys.
pub type Body = Vec<(String, Value)>;

/// `ipc.PrivateServer`: the bound socket, its path and its frame queue.
pub struct Server {
    path: PathBuf,
    listener: Option<UnixListener>,
    directory: File,
    identity: u64,
    queue: Queue,
}

const NAME: &std::ffi::CStr = c"control.sock";

impl Server {
    /// `PrivateServer(client.runtime_dir, run_id, session)`; none for a run without desktops (`view_controls` yields
    /// nothing then).
    pub fn open(run: &Run<'_>) -> io::Result<Option<Server>> {
        if run.capture.is_empty() {
            return Ok(None);
        }
        Server::bind(run.runtime, run.run_id, run.session).map(Some)
    }

    /// The socket of `run_id` under `runtime`, its frames for `run_id` and `session` alone.
    pub fn bind(runtime: &Path, run_id: &str, session: &str) -> io::Result<Server> {
        let queue = Queue::new(run_id, session).map_err(|e| invalid(e.0))?;
        let root = runtime.join("xr-workspace-spike");
        drop(private_dir(runtime)?);
        drop(private(&root)?);
        let directory = private(&root.join(run_id))?;
        let anchored =
            PathBuf::from(format!("/proc/self/fd/{}", directory.as_raw_fd())).join("control.sock");
        let listener = UnixListener::bind(&anchored)?;
        let identity = (|| {
            // SAFETY: NAME is NUL-terminated and `directory` is an open directory.
            let r = unsafe {
                libc::fchmodat(
                    directory.as_raw_fd(),
                    NAME.as_ptr(),
                    0o600,
                    libc::AT_SYMLINK_NOFOLLOW,
                )
            };
            if r < 0 {
                return Err(io::Error::last_os_error());
            }
            listener.set_nonblocking(true)?;
            Ok(std::fs::symlink_metadata(&anchored)?.ino())
        })();
        let identity = match identity {
            Ok(ino) => ino,
            Err(e) => {
                // Python leaves the file it bound; nothing else can have it yet, so it goes with the error.
                // SAFETY: as above.
                unsafe { libc::unlinkat(directory.as_raw_fd(), NAME.as_ptr(), 0) };
                return Err(e);
            }
        };
        Ok(Server {
            path: root.join(run_id).join("control.sock"),
            listener: Some(listener),
            directory,
            identity,
            queue,
        })
    }

    /// `server.path`: the bind helper's first argument (`bind_args`).
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// `server.socket`, for the loop's poll (-1 once closed).
    pub fn fd(&self) -> RawFd {
        self.listener.as_ref().map_or(-1, AsRawFd::as_raw_fd)
    }

    /// `control_request(control, status)`: accept one connection, read one frame; answer a `status` here with
    /// `body()`, give any other op back. None for nothing waiting, and for whatever was refused.
    pub fn control_request(&mut self, body: impl FnOnce() -> Body) -> Option<Frame> {
        let (mut conn, _) = self.listener.as_ref()?.accept().ok()?;
        let frame = self.receive(&conn).ok()?;
        if frame.op != "status" {
            return Some(frame);
        }
        let reply = StatusReply::to(&frame, body()).encode();
        let _ = conn
            .set_write_timeout(Some(IO_TIMEOUT))
            .and_then(|()| conn.write_all(reply.as_bytes()));
        None
    }

    /// `serve_without_renderer(control, seconds)`: wait up to `seconds` for one request, then serve it: a `status`
    /// is answered with `body()`, any other op is dropped (no renderer to act on it).
    pub fn serve_without_renderer(&mut self, seconds: Duration, body: impl FnOnce() -> Body) {
        let Some(listener) = self.listener.as_ref() else {
            std::thread::sleep(seconds);
            return;
        };
        let mut fds = [PollFd::new(listener, PollFlags::IN)];
        let timeout = Timespec {
            tv_sec: seconds.as_secs() as _,
            tv_nsec: seconds.subsec_nanos() as _,
        };
        if poll(&mut fds, Some(&timeout)).is_ok_and(|n| n > 0) {
            self.control_request(body);
        }
    }

    /// `PrivateServer.receive` and the queue's `pop`: the peer's UID, one frame up to its newline.
    fn receive(&mut self, conn: &UnixStream) -> io::Result<Frame> {
        let same_uid = rustix::net::sockopt::socket_peercred(conn)
            .is_ok_and(|cred| cred.uid == rustix::process::getuid());
        if !same_uid {
            return Err(io::Error::new(
                ErrorKind::PermissionDenied,
                "wrong peer UID",
            ));
        }
        conn.set_nonblocking(false)?;
        conn.set_read_timeout(Some(IO_TIMEOUT))?;
        let mut raw = Vec::with_capacity(256);
        let mut chunk = vec![0u8; MAX_FRAME];
        while raw.len() < MAX_FRAME {
            let room = MAX_FRAME - raw.len();
            let seen = recv(conn, &mut chunk[..room], rustix::net::RecvFlags::PEEK)?;
            if seen == 0 {
                return Err(invalid("truncated message"));
            }
            let end = chunk[..seen].iter().position(|&b| b == b'\n');
            let mut want = end.map_or(seen, |e| e + 1);
            while want > 0 {
                let taken = recv(conn, &mut chunk[..want], rustix::net::RecvFlags::empty())?;
                if taken == 0 {
                    return Err(invalid("truncated message"));
                }
                raw.extend_from_slice(&chunk[..taken]);
                want -= taken;
            }
            if end.is_some() {
                self.queue.push(&raw).map_err(|e| invalid(e.0))?;
                return self.queue.pop().ok_or_else(|| invalid("queue empty"));
            }
        }
        Err(invalid("oversized message"))
    }

    /// `server.close()`: the socket closed, its file removed while it is still this server's.
    pub fn close(&mut self) {
        if self.listener.take().is_none() {
            return;
        }
        let anchored = PathBuf::from(format!("/proc/self/fd/{}", self.directory.as_raw_fd()))
            .join("control.sock");
        if std::fs::symlink_metadata(anchored)
            .is_ok_and(|m| m.ino() == self.identity && m.file_type().is_socket())
        {
            // SAFETY: NAME is NUL-terminated and `directory` is an open directory.
            unsafe { libc::unlinkat(self.directory.as_raw_fd(), NAME.as_ptr(), 0) };
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.close();
    }
}

/// `private_dir(path, create=True)`: the leaf made 0700 when missing, then checked.
fn private(path: &Path) -> io::Result<File> {
    match DirBuilder::new().mode(0o700).create(path) {
        Err(e) if e.kind() != ErrorKind::AlreadyExists => return Err(e),
        _ => {}
    }
    private_dir(path)
}

/// One `recv(2)`, a timeout or a reset counted as an unfinished message (Python's `TimeoutError` / `ConnectionError`).
fn recv(conn: &UnixStream, buf: &mut [u8], flags: rustix::net::RecvFlags) -> io::Result<usize> {
    loop {
        match rustix::net::recv(conn, &mut *buf, flags) {
            Ok((n, _)) => return Ok(n),
            Err(rustix::io::Errno::INTR) => continue,
            Err(e) => return Err(invalid(format!("incomplete message: {e}"))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn a_status_is_answered_and_an_op_given_back_then_the_file_goes() {
        let dir = crate::testdir::TestDir::new("socket");
        let rt = dir.path();
        let mut server = Server::bind(rt, "r1", "s").unwrap();
        assert_eq!(server.path(), rt.join("xr-workspace-spike/r1/control.sock"));
        let mode = std::fs::metadata(server.path()).unwrap().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert!(
            server.control_request(Vec::new).is_none(),
            "nothing waiting"
        );
        let mut c = UnixStream::connect(server.path()).unwrap();
        c.write_all(b"{\"schema\":1,\"request_id\":1,\"run_id\":\"r1\",\"session\":\"s\",\"op\":\"status\",\"payload\":{}}\n")
            .unwrap();
        assert!(
            server
                .control_request(|| vec![("view".into(), Value::Object(Vec::new()))])
                .is_none()
        );
        let mut reply = String::new();
        c.read_to_string(&mut reply).unwrap();
        assert_eq!(
            reply,
            "{\"schema\": 1, \"request_id\": 1, \"run_id\": \"r1\", \"session\": \"s\", \"view\": {}}\n"
        );
        let mut c = UnixStream::connect(server.path()).unwrap();
        c.write_all(b"{\"schema\":1,\"request_id\":2,\"run_id\":\"r1\",\"session\":\"s\",\"op\":\"recenter\",\"payload\":{}}\nmore")
            .unwrap();
        assert_eq!(server.control_request(Vec::new).unwrap().op, "recenter");
        assert!(
            Server::bind(rt, "r1", "s").is_err(),
            "never replaces a socket"
        );
        let path = server.path().to_path_buf();
        server.close();
        assert!(!path.exists());
        assert_eq!(server.fd(), -1);
    }
}
