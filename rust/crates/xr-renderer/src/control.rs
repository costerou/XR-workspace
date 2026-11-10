//! The supervisor's control frames, as far as the presentation shell needs them: `start` and `stop`.
//!
//! The envelope rules, the replay floor and the stream framing follow `decode_control`,
//! `ControlQueue` and `ControlStream` in `spike/src/ipc.cpp`. Every other op is refused (counted and
//! logged, never acted on) until the full `decode_control` lands in `xr-wire`, which this module
//! then gives way to. The strict JSON layer is already xr-wire's.

use serde_json::Value;
use xr_wire::json::{self, Limits, MAX_DEPTH};

/// Bytes with no newline before the stream gives the frame up.
pub const MAX_PENDING: usize = 524_288;
/// A start or stop frame is at most this long, newline included.
pub const MAX_FRAME: usize = 16_384;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub code: &'static str,
    pub detail: String,
}

fn invalid(why: impl Into<String>) -> Error {
    Error {
        code: "invalid_control",
        detail: why.into(),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Start,
    Stop,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Message {
    pub request_id: u64,
    pub run_id: String,
    pub session: String,
    pub op: Op,
}

/// A run id, session or op: 1..=128 of `[A-Za-z0-9_.:-]`, starting alphanumeric.
pub fn token(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && b[0].is_ascii_alphanumeric()
        && b.iter()
            .all(|&c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.' | b':' | b'-'))
}

/// One frame, newline included.
pub fn decode(frame: &[u8]) -> Result<Message, Error> {
    let body = match frame.split_last() {
        Some((b'\n', body)) if frame.len() <= 524_288 && !body.contains(&b'\n') => body,
        _ => return Err(invalid("bad framing")),
    };
    // xr-wire's strict layer, with decode_control's detail words for what it refuses.
    let j = json::parse(
        body,
        Limits {
            max_bytes: MAX_PENDING,
            max_depth: MAX_DEPTH,
        },
    )
    .map_err(|e| {
        invalid(match e.0 {
            m if m.starts_with("nested deeper") => "nesting limit",
            m if m.starts_with("invalid JSON: duplicate key") => "duplicate key",
            _ if frame.len() > MAX_FRAME => "bad framing",
            _ => "not JSON",
        })
    })?;
    let envelope = || invalid("bad envelope");
    let o = match &j {
        json::Value::Object(o) if o.len() == 6 => &j,
        _ => return Err(envelope()),
    };
    let field = |k: &str| o.get(k).ok_or_else(envelope);
    let (schema, request_id) = (field("schema")?, field("request_id")?);
    let (run_id, session, op, payload) = (
        field("run_id")?,
        field("session")?,
        field("op")?,
        field("payload")?,
    );
    let request_id = request_id
        .as_int()
        .and_then(|id| u64::try_from(id).ok())
        .filter(|&id| id != 0);
    let (Some(1), Some(request_id)) = (schema.as_int(), request_id) else {
        return Err(envelope());
    };
    let (Some(run_id), Some(session), Some(name)) =
        (run_id.as_str(), session.as_str(), op.as_str())
    else {
        return Err(envelope());
    };
    if !token(run_id)
        || !token(session)
        || frame.len() > MAX_FRAME
        || !matches!(payload, json::Value::Object(_))
    {
        return Err(envelope());
    }
    let op = match name {
        "start" => Op::Start,
        "stop" => Op::Stop,
        _ => {
            return Err(invalid(format!(
                "op {name:?} is not handled by this renderer"
            )));
        }
    };
    if !matches!(payload, json::Value::Object(p) if p.is_empty()) {
        return Err(invalid(format!("bad {name} payload")));
    }
    Ok(Message {
        request_id,
        run_id: run_id.into(),
        session: session.into(),
        op,
    })
}

/// A frame the stream refused: why, and its op and request id where it has them. Never its text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub error: Error,
    pub op: String,
    pub request_id: u64,
}

/// The bytes off the control socket, frame by frame, replay-protected.
pub struct Stream {
    run_id: String,
    session: String,
    fatal: bool,
    last: u64,
    pending: Vec<u8>,
    skipping: bool,
    stopped: bool,
    accepted: u64,
    refused: u64,
}

impl Stream {
    /// `start`: the start frame main has already validated; it sets the replay floor.
    pub fn new(run_id: &str, session: &str, fatal: bool, start: &[u8]) -> Self {
        let mut s = Self {
            run_id: run_id.into(),
            session: session.into(),
            fatal,
            last: 0,
            pending: Vec::new(),
            skipping: false,
            stopped: false,
            accepted: 0,
            refused: 0,
        };
        let _ = s.push(start);
        s
    }

    fn push(&mut self, frame: &[u8]) -> Result<Message, Error> {
        let m = decode(frame)?;
        if m.run_id != self.run_id || m.session != self.session {
            return Err(Error {
                code: "control_wrong_run",
                detail: "frame is for another run or session".into(),
            });
        }
        if m.request_id <= self.last {
            return Err(Error {
                code: "control_replay",
                detail: format!("request id {} is not above {}", m.request_id, self.last),
            });
        }
        self.last = m.request_id;
        Ok(m)
    }

    fn refuse(&mut self, error: Error, frame: &[u8], refused: &mut dyn FnMut(Refusal)) -> bool {
        self.refused += 1;
        let mut r = Refusal {
            error,
            op: String::new(),
            request_id: 0,
        };
        if let Ok(Value::Object(o)) = serde_json::from_slice::<Value>(frame) {
            if let Some(op) = o.get("op").and_then(Value::as_str).filter(|op| token(op)) {
                r.op = op.into();
            }
            r.request_id = o.get("request_id").and_then(Value::as_u64).unwrap_or(0);
        }
        refused(r);
        if self.fatal {
            self.stopped = true;
        }
        !self.stopped
    }

    /// False: a refused frame under `fatal`.
    pub fn feed(
        &mut self,
        mut bytes: &[u8],
        deliver: &mut dyn FnMut(Message),
        refused: &mut dyn FnMut(Refusal),
    ) -> bool {
        if self.skipping {
            let Some(end) = bytes.iter().position(|&c| c == b'\n') else {
                return true;
            };
            bytes = &bytes[end + 1..];
            self.skipping = false;
        }
        self.pending.extend_from_slice(bytes);
        while let Some(nl) = self.pending.iter().position(|&c| c == b'\n') {
            let frame: Vec<u8> = self.pending.drain(..=nl).collect();
            match self.push(&frame) {
                Ok(m) => {
                    self.accepted += 1;
                    deliver(m);
                }
                Err(e) => {
                    if !self.refuse(e, &frame, refused) {
                        return false;
                    }
                }
            }
        }
        if self.pending.len() > MAX_PENDING {
            let start: Vec<u8> = self.pending[..201].to_vec();
            self.pending.clear();
            self.skipping = true;
            let error = Error {
                code: "control_overflow",
                detail: format!("no newline in {MAX_PENDING} bytes"),
            };
            return self.refuse(error, &start, refused);
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

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(id: u64, run: &str, op: &str, payload: &str) -> Vec<u8> {
        format!(r#"{{"schema":1,"request_id":{id},"run_id":"{run}","session":"s1","op":"{op}","payload":{payload}}}"#)
            .into_bytes()
            .into_iter()
            .chain(*b"\n")
            .collect()
    }

    #[test]
    fn start_and_stop_decode() {
        let m = decode(&frame(1, "bench1", "start", "{}")).unwrap();
        assert_eq!(
            (m.request_id, m.op, m.run_id.as_str()),
            (1, Op::Start, "bench1")
        );
        assert_eq!(
            decode(&frame(9, "bench1", "stop", "{}")).unwrap().op,
            Op::Stop
        );
    }

    #[test]
    fn bad_frames_are_refused() {
        let bad: [&[u8]; 10] = [
            b"",
            b"{}",
            b"not json\n",
            b"{\"a\":1}\n",
            br#"{"schema":2,"request_id":1,"run_id":"r","session":"s1","op":"start","payload":{}}
"#,
            br#"{"schema":1,"request_id":0,"run_id":"r","session":"s1","op":"start","payload":{}}
"#,
            br#"{"schema":1,"request_id":1,"run_id":"-r","session":"s1","op":"start","payload":{}}
"#,
            br#"{"schema":1,"request_id":1,"run_id":"r","session":"s1","op":"start","payload":{"x":1}}
"#,
            br#"{"schema":1,"request_id":1,"request_id":2,"run_id":"r","session":"s1","op":"start"}
"#,
            br#"{"schema":1,"request_id":1,"run_id":"r","session":"s1","op":"start","payload":{},"extra":0}
"#,
        ];
        for f in bad {
            assert!(decode(f).is_err(), "{}", String::from_utf8_lossy(f));
        }
        let dup = br#"{"schema":1,"request_id":1,"run_id":"r","session":"s1","op":"start","payload":{"a":1,"a":2}}
"#;
        assert_eq!(decode(dup).unwrap_err().detail, "duplicate key");
        let deep = format!("{}\n", "[".repeat(40) + &"]".repeat(40));
        assert_eq!(decode(deep.as_bytes()).unwrap_err().detail, "nesting limit");
        assert_eq!(
            decode(&frame(1, "r", "view", "{}")).unwrap_err().code,
            "invalid_control"
        );
        let mut big = frame(1, "r", "stop", "{}");
        big.splice(1..1, std::iter::repeat_n(b' ', MAX_FRAME));
        assert_eq!(decode(&big).unwrap_err().detail, "bad envelope");
    }

    #[test]
    fn stream_frames_replays_and_other_runs() {
        let mut s = Stream::new("bench1", "s1", false, &frame(1, "bench1", "start", "{}"));
        let (mut got, mut refusals) = (Vec::new(), Vec::new());
        let mut bytes = frame(2, "bench1", "view", "{}");
        bytes.extend(frame(1, "bench1", "stop", "{}"));
        bytes.extend(frame(3, "other", "stop", "{}"));
        bytes.extend(b"junk\n");
        let stop = frame(4, "bench1", "stop", "{}");
        bytes.extend(&stop[..10]);
        assert!(s.feed(&bytes, &mut |m| got.push(m), &mut |r| refusals.push(r)));
        assert!(got.is_empty());
        assert!(s.feed(&stop[10..], &mut |m| got.push(m), &mut |r| refusals.push(r)));
        assert_eq!(
            got.iter().map(|m| (m.request_id, m.op)).collect::<Vec<_>>(),
            [(4, Op::Stop)]
        );
        let codes: Vec<_> = refusals
            .iter()
            .map(|r| (r.error.code, r.op.as_str(), r.request_id))
            .collect();
        assert_eq!(
            codes,
            [
                ("invalid_control", "view", 2),
                ("control_replay", "stop", 1),
                ("control_wrong_run", "stop", 3),
                ("invalid_control", "", 0)
            ]
        );
        assert_eq!((s.accepted(), s.refused()), (1, 4));
    }

    #[test]
    fn overflow_skips_to_the_next_newline_and_fatal_stops() {
        let mut s = Stream::new("r", "s1", false, &frame(1, "r", "start", "{}"));
        let mut refusals = Vec::new();
        assert!(s.feed(&vec![b'x'; MAX_PENDING + 1], &mut |_| {}, &mut |r| {
            refusals.push(r)
        }));
        let mut got = Vec::new();
        let mut rest = b"xx\n".to_vec();
        rest.extend(frame(2, "r", "stop", "{}"));
        assert!(s.feed(&rest, &mut |m| got.push(m), &mut |r| refusals.push(r)));
        assert_eq!(
            (got.len(), refusals.len(), refusals[0].error.code),
            (1, 1, "control_overflow")
        );
        let mut fatal = Stream::new("r", "s1", true, &frame(1, "r", "start", "{}"));
        assert!(!fatal.feed(b"junk\n", &mut |_| {}, &mut |_| {}));
        assert!(fatal.stopped());
    }
}
