//! The panel's socket: a port of `tools/xr_controller/server.py`.
//!
//! One thread, one `poll(2)` loop, at most [`MAX_CLIENTS`] same-UID clients on
//! `$XDG_RUNTIME_DIR/viture-workspace/controller.sock`. The wire format and the line rules are
//! `xr_wire::controller`; this module owns only the listener and the connections:
//!
//! - The socket is bound through `/proc/self/fd/<dir>/` of the private (owned, 0700) directory,
//!   so a renamed or symlinked parent cannot redirect it, and made 0600.
//! - A peer whose `SO_PEERCRED` UID is not ours is closed unanswered, as is the 33rd client.
//! - A line over [`MAX_FRAME`] (newline included), [`MAX_FRAME`] bytes with no newline, or a line
//!   the envelope refuses ends that connection. So does a reply over [`MAX_FRAME`], or a client
//!   whose unsent output would pass [`MAX_OUTPUT`]: a stuck panel cannot grow the controller.
//! - Each request is answered in order. The controller ticks once a second, and whenever its
//!   snapshot changes (after a request or a tick) every client gets an `event: "state"` push.
//! - On close the socket file is unlinked only if it is still the one this server bound.

use std::ffi::{CString, OsString};
use std::fs::{DirBuilder, File};
use std::io::{self, ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use rustix::event::{PollFd, PollFlags, Timespec, poll};
use xr_wire::controller::{Lines, MAX_CLIENTS, MAX_FRAME, Message, Request};
use xr_wire::json::Value;

use crate::controller::{Controller, ControllerHost};
use crate::private_file::{invalid, private_dir};

/// The most unsent bytes one client may hold before it is dropped (`4 * MAX_FRAME`, as the
/// Python server).
pub const MAX_OUTPUT: usize = 4 * MAX_FRAME;

/// How often the controller ticks.
pub const TICK: Duration = Duration::from_secs(1);

/// What the server drives. [`Controller`] is the one the service runs; tests wrap it to watch the
/// requests go by.
pub trait Service {
    /// Answer one request.
    fn handle(&mut self, request: &Request) -> Message;
    /// The 1 Hz tick.
    fn tick(&mut self);
    /// The snapshot when it changed since the last call.
    fn take_changed(&mut self) -> Option<Value>;
}

impl<H: ControllerHost> Service for Controller<H> {
    fn handle(&mut self, request: &Request) -> Message {
        Controller::handle(self, request)
    }
    fn tick(&mut self) {
        Controller::tick(self)
    }
    fn take_changed(&mut self) -> Option<Value> {
        Controller::take_changed(self)
    }
}

struct Client {
    stream: UnixStream,
    lines: Lines,
    output: Vec<u8>,
}

/// The listener and its clients.
pub struct Server {
    pub path: PathBuf,
    listener: UnixListener,
    clients: Vec<Client>,
    directory: File,
    name: CString,
    identity: u64,
    next_tick: Instant,
}

impl Server {
    /// Bind `path` (its parent is created 0700 when missing, then must be private). An existing
    /// file at `path` is never replaced: the bind fails. `service`'s current snapshot is taken as
    /// already known, so the first push is a real change.
    pub fn bind(path: &Path, service: &mut impl Service) -> io::Result<Server> {
        let parent = path.parent().ok_or_else(|| invalid("unsafe path"))?;
        let name = path.file_name().ok_or_else(|| invalid("unsafe path"))?;
        match DirBuilder::new().mode(0o700).create(parent) {
            Err(e) if e.kind() != ErrorKind::AlreadyExists => return Err(e),
            _ => {}
        }
        let directory = private_dir(parent)?;
        let anchored = PathBuf::from(format!("/proc/self/fd/{}", directory.as_raw_fd())).join(name);
        let listener = UnixListener::bind(&anchored)?;
        let name = CString::new(name.as_bytes()).map_err(|_| invalid("unsafe path"))?;
        let identity = (|| {
            // SAFETY: `name` is NUL-terminated and `directory` is an open directory.
            let r = unsafe {
                libc::fchmodat(
                    directory.as_raw_fd(),
                    name.as_ptr(),
                    0o600,
                    libc::AT_SYMLINK_NOFOLLOW,
                )
            };
            if r < 0 {
                return Err(io::Error::last_os_error());
            }
            listener.set_nonblocking(true)?;
            stat_ino(&directory, &name)
        })();
        let identity = match identity {
            Ok(ino) => ino,
            Err(e) => {
                // The file is ours (just bound); do not leave it behind.
                // SAFETY: as above.
                unsafe { libc::unlinkat(directory.as_raw_fd(), name.as_ptr(), 0) };
                return Err(e);
            }
        };
        service.take_changed();
        Ok(Server {
            path: path.to_path_buf(),
            listener,
            clients: Vec::new(),
            directory,
            name,
            identity,
            next_tick: Instant::now() + TICK,
        })
    }

    /// How many clients are connected.
    pub fn clients(&self) -> usize {
        self.clients.len()
    }

    /// Close every client. Their unsent replies are lost.
    pub fn drop_all(&mut self) {
        self.clients.clear();
    }

    /// Wait up to `timeout` (never past the next tick) for the sockets, serve what is ready, tick
    /// when due, then push a changed snapshot.
    pub fn step(&mut self, service: &mut impl Service, timeout: Duration) -> io::Result<()> {
        let wait = timeout.min(self.next_tick.saturating_duration_since(Instant::now()));
        let ready = {
            let mut fds = Vec::with_capacity(self.clients.len() + 1);
            fds.push(PollFd::new(&self.listener, PollFlags::IN));
            for c in &self.clients {
                let mut events = PollFlags::IN;
                if !c.output.is_empty() {
                    events |= PollFlags::OUT;
                }
                fds.push(PollFd::new(&c.stream, events));
            }
            let timespec = Timespec {
                tv_sec: wait.as_secs() as _,
                tv_nsec: wait.subsec_nanos() as _,
            };
            match poll(&mut fds, Some(&timespec)) {
                Ok(_) => {}
                Err(rustix::io::Errno::INTR) => {}
                Err(e) => return Err(e.into()),
            }
            fds.iter().map(PollFd::revents).collect::<Vec<_>>()
        };
        // Clients first (their indices match `ready`), the listener last, so a new client only
        // joins after this round.
        let mut keep = Vec::with_capacity(self.clients.len());
        let clients = std::mem::take(&mut self.clients);
        for (mut c, events) in clients.into_iter().zip(ready[1..].iter().copied()) {
            if events.is_empty() || serve(&mut c, events, service) {
                keep.push(c);
            }
        }
        self.clients = keep;
        if ready[0].contains(PollFlags::IN) {
            self.accept();
        }
        if Instant::now() >= self.next_tick {
            service.tick();
            self.next_tick = Instant::now() + TICK;
        }
        if let Some(snapshot) = service.take_changed() {
            let line = Message::State { snapshot }.encode();
            self.clients.retain_mut(|c| queue(c, &line));
        }
        Ok(())
    }

    fn accept(&mut self) {
        loop {
            let stream = match self.listener.accept() {
                Ok((stream, _)) => stream,
                Err(e) if e.kind() == ErrorKind::Interrupted => continue,
                // WouldBlock ends the round; a failed accept (EMFILE, ECONNABORTED, ...) skips it.
                Err(_) => return,
            };
            let same_uid = rustix::net::sockopt::socket_peercred(&stream)
                .is_ok_and(|cred| cred.uid == rustix::process::getuid());
            if !same_uid
                || self.clients.len() >= MAX_CLIENTS
                || stream.set_nonblocking(true).is_err()
            {
                continue; // dropped: closed unanswered
            }
            self.clients.push(Client {
                stream,
                lines: Lines::default(),
                output: Vec::new(),
            });
        }
    }

    /// Serve until the process ends (`serve` in `server.py`).
    pub fn run(&mut self, service: &mut impl Service) -> io::Result<()> {
        loop {
            self.step(service, TICK)?;
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.clients.clear();
        if stat_ino(&self.directory, &self.name).is_ok_and(|ino| ino == self.identity) {
            // SAFETY: `name` is NUL-terminated and `directory` is an open directory.
            unsafe { libc::unlinkat(self.directory.as_raw_fd(), self.name.as_ptr(), 0) };
        }
    }
}

/// The inode of `name` in `directory`, not following a symlink.
fn stat_ino(directory: &File, name: &CString) -> io::Result<u64> {
    let anchored = PathBuf::from(format!("/proc/self/fd/{}", directory.as_raw_fd()))
        .join(OsString::from(std::ffi::OsStr::from_bytes(name.as_bytes())));
    Ok(std::fs::symlink_metadata(anchored)?.ino())
}

/// Handle one client's readiness. `false` drops it.
fn serve(c: &mut Client, events: PollFlags, service: &mut impl Service) -> bool {
    if events.intersects(PollFlags::IN | PollFlags::HUP | PollFlags::ERR) {
        let mut buffer = [0u8; MAX_FRAME + 1];
        match c.stream.read(&mut buffer) {
            Ok(0) => return false,
            Ok(n) => c.lines.extend(&buffer[..n]),
            Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::Interrupted) => {}
            Err(_) => return false,
        }
        loop {
            let line = match c.lines.next_line() {
                Ok(Some(line)) => line,
                Ok(None) => break,
                Err(_) => return false,
            };
            let Ok(request) = Request::decode(&line) else {
                return false;
            };
            let reply = service.handle(&request).encode();
            if !queue(c, &reply) {
                return false;
            }
        }
    }
    // Only on POLLOUT, as the Python server: what this round queued goes out next round, so
    // anything the service does after `step` (a test cutting the transport) comes first.
    if events.contains(PollFlags::OUT) && !c.output.is_empty() {
        match c.stream.write(&c.output) {
            Ok(n) => {
                c.output.drain(..n);
            }
            Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::Interrupted) => {}
            Err(_) => return false,
        }
    }
    true
}

/// Queue one line for a client; `false` when it must be dropped (the line or the backlog too big).
fn queue(c: &mut Client, line: &str) -> bool {
    if line.len() > MAX_FRAME || c.output.len() + line.len() > MAX_OUTPUT {
        return false;
    }
    c.output.extend_from_slice(line.as_bytes());
    true
}
