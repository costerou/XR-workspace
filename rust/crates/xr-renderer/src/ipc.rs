//! The renderer's end of the supervisor's sockets, as `spike/src/main.cpp` and `ControlStream` in
//! `spike/src/ipc.cpp` run them.
//!
//! - [`ControlStream`]: `xr_wire::control::Stream` (framing, identity, replay floor, overflow) with
//!   the renderer's `--control-errors fatal` rule: a refused frame stops the stream unless it is a
//!   malformed display-only payload ([`Refusal::ignorable`]).
//! - [`Descriptors`] and [`receive`]: the control socket read with `recvmsg`, keeping the
//!   `SCM_RIGHTS` descriptors a `video` open frame brings (at most two wait; extras are closed).
//! - [`status_dict`]: the status sample, the metrics' fields and then the presentation's, in the
//!   C++ order. Receipts are `xr_wire::status::encode_applied` and `encode_refused`.

use std::collections::VecDeque;
use std::io::IoSliceMut;
use std::mem::MaybeUninit;
use std::os::fd::{AsFd, OwnedFd};

use rustix::io::Errno;
use rustix::net::{RecvAncillaryBuffer, RecvAncillaryMessage, RecvFlags, recvmsg};
use serde_json::{Map, Value};
use xr_wire::control::{self, Message, Refused};
use xr_wire::status::{CapturePath, Video};

/// The supervisor's control frames for one run, frame by frame. One bad frame costs that frame,
/// never the session, except under `fatal`.
pub struct ControlStream {
    stream: control::Stream,
    fatal: bool,
    stopped: bool,
    accepted: u64,
    refused: u64,
}

impl ControlStream {
    /// `None` unless both are tokens.
    pub fn new(run_id: &str, session: &str, fatal: bool) -> Option<ControlStream> {
        Some(ControlStream {
            stream: control::Stream::new(run_id, session)?,
            fatal,
            stopped: false,
            accepted: 0,
            refused: 0,
        })
    }

    /// The start frame main has already validated: it sets the replay floor and is not counted.
    pub fn start(&mut self, frame: &[u8]) {
        let _ = self.stream.feed(frame);
    }

    /// Bytes as they arrive, each frame's verdict to `each` in order. False: stopped (`fatal` and a
    /// refusal it does not excuse); the frames after that one are neither delivered nor reported.
    pub fn feed(&mut self, bytes: &[u8], each: &mut dyn FnMut(Result<Message, &Refused>)) -> bool {
        if self.stopped {
            return false;
        }
        for result in self.stream.feed(bytes) {
            match result {
                Ok(message) => {
                    self.accepted += 1;
                    each(Ok(message));
                }
                Err(r) => {
                    self.refused += 1;
                    each(Err(&r));
                    if self.fatal && !r.refusal.ignorable() {
                        self.stopped = true;
                        return false;
                    }
                }
            }
        }
        true
    }

    pub fn stopped(&self) -> bool {
        self.stopped
    }
    pub fn accepted(&self) -> u64 {
        self.accepted
    }
    pub fn refused(&self) -> u64 {
        self.refused
    }
}

/// Video files that arrived on the control socket, oldest first. Each `video` open takes one; a
/// refused `video` (or unreadable) frame drops one, since only its own open may play that file.
#[derive(Debug, Default)]
pub struct Descriptors(VecDeque<OwnedFd>);

/// The most descriptors that wait for their open frame.
pub const MAX_WAITING: usize = 2;

impl Descriptors {
    fn push(&mut self, fd: OwnedFd) {
        if self.0.len() < MAX_WAITING {
            self.0.push_back(fd);
        } // else dropped, which closes it
    }

    /// The oldest, for a `video` open.
    pub fn take(&mut self) -> Option<OwnedFd> {
        self.0.pop_front()
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// For a refused frame: a `video` frame's descriptor must not reach the next open.
    pub fn refused(&mut self, r: &Refused) {
        if r.op.as_deref().is_none_or(|op| op == "video") {
            self.0.pop_front();
        }
    }
}

/// One nonblocking read of the control socket: its bytes into `buffer`, its `SCM_RIGHTS`
/// descriptors (close-on-exec) into `fds`. Never opens anything by path.
pub fn receive(
    socket: impl AsFd,
    buffer: &mut [u8],
    fds: &mut Descriptors,
) -> Result<usize, Errno> {
    let mut space = [MaybeUninit::<u8>::uninit(); rustix::cmsg_space!(ScmRights(4))];
    let mut ancillary = RecvAncillaryBuffer::new(&mut space);
    let got = recvmsg(
        socket,
        &mut [IoSliceMut::new(buffer)],
        &mut ancillary,
        RecvFlags::DONTWAIT | RecvFlags::CMSG_CLOEXEC,
    )?;
    for message in ancillary.drain() {
        if let RecvAncillaryMessage::ScmRights(received) = message {
            for fd in received {
                fds.push(fd);
            }
        }
    }
    Ok(got.bytes)
}

/// What the presentation adds to a status sample (`Presentation::status_json`); `None` leaves a
/// field out.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct StatusExtras {
    /// Left out while the player is off.
    pub video: Option<Video>,
    /// Only when tracking was asked for.
    pub tracking: Option<bool>,
    /// Only when there are captures: the longest run of failed frames.
    pub capture_failure_run: Option<u64>,
    /// Only when every capture has a frame and all read the same way.
    pub capture_path: Option<CapturePath>,
}

/// One status sample, without its newline: the metrics' object (`missed`, `presented_hz`) and then
/// `video`, `tracking`, `capture_failure_run` and `capture_path`, as the C++ appends them.
pub fn status_dict(metrics: &str, extras: &StatusExtras) -> String {
    let mut j: Map<String, Value> = match serde_json::from_str(metrics) {
        Ok(Value::Object(o)) => o.into_iter().collect(),
        _ => Map::new(),
    };
    let mut fields: Vec<(String, Value)> = std::mem::take(&mut j).into_iter().collect();
    if let Some(v) = extras.video.filter(|v| *v != Video::Off) {
        let name = match v {
            Video::Off => "off",
            Video::Loading => "loading",
            Video::Playing => "playing",
            Video::Paused => "paused",
            Video::Ended => "ended",
            Video::Error => "error",
        };
        fields.push(("video".into(), name.into()));
    }
    if let Some(t) = extras.tracking {
        fields.push(("tracking".into(), t.into()));
    }
    if let Some(run) = extras.capture_failure_run {
        fields.push(("capture_failure_run".into(), run.into()));
    }
    if let Some(path) = extras.capture_path {
        let name = match path {
            CapturePath::Dmabuf => "dmabuf",
            CapturePath::Shm => "shm",
        };
        fields.push(("capture_path".into(), name.into()));
    }
    // Written by hand so the order holds whatever serde_json's map keeps.
    let body: Vec<String> = fields
        .iter()
        .map(|(k, v)| format!("{}:{v}", Value::String(k.clone())))
        .collect();
    format!("{{{}}}", body.join(","))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::fd::AsRawFd;
    use std::os::unix::net::UnixStream;
    use xr_wire::control::Op;
    use xr_wire::status::{self, Datagram, Status};

    fn frame(id: u64, op: &str, payload: &str) -> String {
        format!(
            r#"{{"schema":1,"request_id":{id},"run_id":"run","session":"s1","op":"{op}","payload":{payload}}}"#
        ) + "\n"
    }

    fn feed(s: &mut ControlStream, bytes: &str) -> (bool, Vec<(u64, Op)>, Vec<Refused>) {
        let (mut got, mut refusals) = (Vec::new(), Vec::new());
        let open = s.feed(bytes.as_bytes(), &mut |v| match v {
            Ok(m) => got.push((m.request_id, m.op)),
            Err(r) => refusals.push(r.clone()),
        });
        (open, got, refusals)
    }

    #[test]
    fn the_start_frame_sets_the_floor_and_is_not_counted() {
        let mut s = ControlStream::new("run", "s1", false).unwrap();
        s.start(frame(5, "start", "{}").as_bytes());
        let (open, got, refusals) = feed(
            &mut s,
            &(frame(5, "stop", "{}") + &frame(6, "recenter", "{}")),
        );
        assert!(open);
        assert_eq!(got, [(6, Op::Recenter)]);
        assert_eq!(refusals[0].refusal.code, "control_replay");
        assert_eq!((s.accepted(), s.refused()), (1, 1));
        assert!(ControlStream::new("-bad", "s1", false).is_none());
    }

    #[test]
    fn fatal_stops_on_a_real_refusal_but_not_a_display_payload() {
        let mut s = ControlStream::new("run", "s1", true).unwrap();
        s.start(frame(1, "start", "{}").as_bytes());
        let menu = frame(2, "placement_menu", r#"{"open":"yes"}"#);
        let (open, _, refusals) = feed(&mut s, &(menu + &frame(3, "recenter", "{}")));
        assert!(open && !s.stopped());
        assert_eq!(refusals[0].refusal.code, "invalid_menu");
        let bytes = frame(4, "view", r#"{"zoomed":1}"#) + &frame(5, "recenter", "{}");
        let (open, got, refusals) = feed(&mut s, &bytes);
        assert!(!open && s.stopped());
        assert!(
            got.is_empty(),
            "nothing after the stopping frame is delivered"
        );
        assert_eq!(refusals.len(), 1);
        assert_eq!((s.accepted(), s.refused()), (1, 2));
        assert!(!s.feed(frame(6, "recenter", "{}").as_bytes(), &mut |_| {}));
    }

    #[test]
    fn descriptors_ride_with_their_frame() {
        let (a, b) = UnixStream::pair().unwrap();
        let files: Vec<OwnedFd> = (0..3)
            .map(|_| OwnedFd::from(std::fs::File::open("/dev/null").unwrap()))
            .collect();
        let line = b"hello\n";
        let mut space = [MaybeUninit::<u8>::uninit(); rustix::cmsg_space!(ScmRights(3))];
        let mut out = rustix::net::SendAncillaryBuffer::new(&mut space);
        let borrowed: Vec<_> = files.iter().map(AsFd::as_fd).collect();
        assert!(out.push(rustix::net::SendAncillaryMessage::ScmRights(&borrowed)));
        rustix::net::sendmsg(
            &a,
            &[std::io::IoSlice::new(line)],
            &mut out,
            rustix::net::SendFlags::empty(),
        )
        .unwrap();
        let (mut buffer, mut fds) = ([0u8; 64], Descriptors::default());
        assert_eq!(receive(&b, &mut buffer, &mut fds).unwrap(), line.len());
        assert_eq!(&buffer[..line.len()], line);
        assert_eq!(fds.len(), MAX_WAITING, "the third is closed");
        let fd = fds.take().unwrap();
        let flags = rustix::io::fcntl_getfd(&fd).unwrap();
        assert!(flags.contains(rustix::io::FdFlags::CLOEXEC));
        assert_ne!(fd.as_raw_fd(), files[0].as_raw_fd());
        assert_eq!(receive(&b, &mut buffer, &mut fds), Err(Errno::AGAIN));

        let refused = |op: Option<&str>| Refused {
            refusal: control::Refusal {
                code: "invalid_control",
                detail: String::new(),
            },
            op: op.map(str::to_owned),
            request_id: 0,
            frame: String::new(),
        };
        fds.refused(&refused(Some("view")));
        assert_eq!(fds.len(), 1);
        fds.refused(&refused(None));
        assert!(fds.is_empty());
    }

    #[test]
    fn receipts_and_status_decode_as_the_supervisor_reads_them() {
        assert_eq!(
            status::decode(status::encode_applied(7).as_bytes()).unwrap(),
            Datagram::ControlApplied(7)
        );
        let line = status_dict(
            r#"{"missed":2,"presented_hz":59.9}"#,
            &StatusExtras {
                video: Some(Video::Playing),
                tracking: Some(true),
                capture_failure_run: Some(0),
                capture_path: Some(CapturePath::Dmabuf),
            },
        );
        assert_eq!(
            line,
            r#"{"missed":2,"presented_hz":59.9,"video":"playing","tracking":true,"capture_failure_run":0,"capture_path":"dmabuf"}"#
        );
        let want = Status {
            presented_hz: Some(59.9),
            missed: Some(2),
            capture_path: Some(CapturePath::Dmabuf),
            pose_age_ms: None,
            video: Some(Video::Playing),
            tracking: Some(true),
            capture_failure_run: Some(0),
        };
        assert_eq!(
            status::decode(line.as_bytes()).unwrap(),
            Datagram::Status(want)
        );
        let off = StatusExtras {
            video: Some(Video::Off),
            ..Default::default()
        };
        assert_eq!(status_dict("{}", &off), "{}");
        assert_eq!(
            status_dict(r#"{"missed":0}"#, &StatusExtras::default()),
            r#"{"missed":0}"#
        );
    }
}
