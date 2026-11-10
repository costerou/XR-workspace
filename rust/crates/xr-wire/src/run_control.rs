//! A run's control socket (`tools/native_spike/ipc.py`): one frame per connection on
//! `$XDG_RUNTIME_DIR/xr-workspace-spike/<run_id>/control.sock`, from the controller
//! (`__main__.Units.control`) and the key helper (`xr_control.send`) to the run's supervisor.
//!
//! A [`Frame`] is exactly `{"schema": 1, "request_id", "run_id", "session", "op", "payload"}` and a
//! newline, within [`MAX_FRAME`] bytes. The controller writes it with `json.dumps(...,
//! separators=(',', ':'))` (ASCII only), the key helper with `ensure_ascii=False`. The supervisor
//! answers `status` alone, with a [`StatusReply`] in `json.dumps` defaults; every other op is taken
//! without an answer. A [`Queue`] holds the frames of one run and session, each `request_id`
//! larger than the last, at most [`MAX_QUEUED`] waiting.
//!
//! What the wire checks (`ipc.decode_control`): the framing, strict JSON, the six keys, `schema`,
//! `request_id`, `run_id` and `session` tokens, a known op, and that the payload is an object. It
//! checks a payload in full only where the wire alone defines it: `select`'s output token,
//! `binds_key`'s modmask and keycode, and an empty payload for every other plain op. The payloads
//! of `options_*` (`options_menu.validate_intent`), `video_*` (`video.validate_request`),
//! `environment`, `immersive`, `placement` (`geometry.validate_placement`) and `stabilization`
//! (`view.validate_stabilization`) belong to those modules; they come through here as a [`Value`]
//! for the code that applies them to check.
//!
//! One place Rust is stricter than `decode_control`: `-0` is a float to serde_json, so it is refused
//! where an integer is required (Python reads the integer 0). No writer sends `-0`.
//!
//! `rust/corpus/sockets/frames.json` (`tests/xr_controller/socket_corpus.py`) holds the frames the
//! shipped writers sent, the status replies, and the shipped verdicts on frames wrong on purpose.

use std::collections::{BTreeSet, VecDeque};
use std::path::{Path, PathBuf};

use crate::controller::{int, is_schema, request_id};
use crate::json::{self, Error, Limits, MAX_DEPTH, Style, Value};

/// The longest frame, its newline included.
pub const MAX_FRAME: usize = 16384;
/// The most frames a [`Queue`] holds.
pub const MAX_QUEUED: usize = 8;

/// The ops a run takes whose payload is the wire's to check.
pub const PLAIN_OPS: [&str; 55] = [
    "status",
    "start",
    "stop",
    "select",
    "recover",
    "zoom_in",
    "zoom_out",
    "next",
    "prev",
    "spacing_up",
    "spacing_down",
    "recenter",
    "mode_next",
    "prediction",
    "help",
    "resize_cancel",
    "placement",
    "menu_toggle",
    "menu_desktop_next",
    "menu_desktop_prev",
    "menu_field_next",
    "menu_field_prev",
    "menu_inc",
    "menu_dec",
    "menu_inc_big",
    "menu_dec_big",
    "menu_reset",
    "menu_back",
    "menu_ultrawide",
    "menu_add",
    "menu_delete",
    "layout_reset",
    "resize_press",
    "resize_release",
    "super_left_press",
    "super_right_press",
    "super_release",
    "send_glasses",
    "send_glasses_1",
    "send_glasses_2",
    "send_glasses_3",
    "send_glasses_4",
    "send_back",
    "typing_toggle",
    "help_prev",
    "help_next",
    "privacy_display",
    "playback_menu_toggle",
    "binds_toggle",
    "binds_key",
    "environment_toggle",
    "environment",
    "immersive_toggle",
    "immersive",
    "stabilization",
];
/// The options menu's intents (`options_menu.OPS`).
pub const OPTIONS_OPS: [&str; 10] = [
    "options_open",
    "options_close",
    "options_scope",
    "options_category",
    "options_focus",
    "options_scroll",
    "options_activate",
    "options_set",
    "options_confirm",
    "options_cancel",
];
/// VR video's ops (`video.VIDEO_OPS`).
pub const VIDEO_OPS: [&str; 12] = [
    "video_open",
    "video_pause",
    "video_seek_back",
    "video_seek_fwd",
    "video_seek_back_big",
    "video_seek_fwd_big",
    "video_format",
    "video_controls",
    "video_exit",
    "video_quality",
    "video_up",
    "video_down",
];
/// Ops whose payload another module checks (see the module docs).
const OWNED_PAYLOADS: [&str; 4] = ["environment", "immersive", "placement", "stabilization"];

/// The socket of run `run_id` under the user's runtime directory. `run_id` must be a token.
pub fn socket_path(runtime_dir: &Path, run_id: &str) -> Result<PathBuf, Error> {
    check_token(run_id)?;
    Ok(runtime_dir
        .join("xr-workspace-spike")
        .join(run_id)
        .join("control.sock"))
}

/// `ipc.TOKEN`: an ASCII letter or digit, then up to 127 of those or `_.:-`.
pub fn is_token(value: &str) -> bool {
    let b = value.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && b[0].is_ascii_alphanumeric()
        && b[1..]
            .iter()
            .all(|&c| c.is_ascii_alphanumeric() || b"_.:-".contains(&c))
}

fn check_token(value: &str) -> Result<(), Error> {
    if is_token(value) {
        Ok(())
    } else {
        Err(Error("invalid identifier".into()))
    }
}

fn token(value: Option<&Value>) -> Result<String, Error> {
    let s = value
        .and_then(Value::as_str)
        .ok_or_else(|| Error("invalid identifier".into()))?;
    check_token(s)?;
    Ok(s.to_string())
}

/// Whether `op` is one a run takes.
pub fn is_op(op: &str) -> bool {
    PLAIN_OPS.contains(&op) || OPTIONS_OPS.contains(&op) || VIDEO_OPS.contains(&op)
}

/// The wire's check of `op`'s payload (see the module docs).
pub fn check_payload(op: &str, payload: &Value) -> Result<(), Error> {
    let fail = |why: &str| Err(Error(why.into()));
    let Value::Object(entries) = payload else {
        return fail("invalid payload");
    };
    if OPTIONS_OPS.contains(&op) || VIDEO_OPS.contains(&op) || OWNED_PAYLOADS.contains(&op) {
        return Ok(());
    }
    let keys = payload.keys().unwrap_or_default();
    match op {
        "select" => {
            if keys != BTreeSet::from(["output_id"]) {
                return fail("invalid select");
            }
            token(payload.get("output_id")).map(|_| ())
        }
        "binds_key" => {
            let field = |k| payload.get(k).and_then(Value::as_int);
            let ok = keys == BTreeSet::from(["mods", "code"])
                && field("mods").is_some_and(|m| (0..=255).contains(&m))
                && field("code").is_some_and(|c| (8..=255).contains(&c));
            if ok { Ok(()) } else { fail("invalid key") }
        }
        _ if !entries.is_empty() => fail("unexpected payload"),
        _ => Ok(()),
    }
}

/// A control frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Frame {
    pub request_id: u64,
    pub run_id: String,
    pub session: String,
    pub op: String,
    /// Always an object; checked by the wire as far as the module docs say.
    pub payload: Value,
}

impl Frame {
    /// Check a frame, its newline included, as `ipc.decode_control` does.
    pub fn decode(raw: &[u8]) -> Result<Frame, Error> {
        let framed = raw.len() <= MAX_FRAME
            && raw.last() == Some(&b'\n')
            && !raw[..raw.len() - 1].contains(&b'\n');
        if !framed {
            return Err(Error("invalid frame".into()));
        }
        let message = json::parse(
            raw,
            Limits {
                max_bytes: MAX_FRAME,
                max_depth: MAX_DEPTH,
            },
        )?;
        let fields = ["schema", "request_id", "run_id", "session", "op", "payload"];
        if message.keys() != Some(BTreeSet::from(fields)) {
            return Err(Error("invalid fields".into()));
        }
        if !is_schema(message.get("schema")) {
            return Err(Error("invalid schema".into()));
        }
        let request_id = message
            .get("request_id")
            .and_then(request_id)
            .ok_or_else(|| Error("invalid request id".into()))?;
        let run_id = token(message.get("run_id"))?;
        let session = token(message.get("session"))?;
        let op = match message.get("op").and_then(Value::as_str) {
            Some(op) if is_op(op) => op.to_string(),
            _ => return Err(Error("invalid operation".into())),
        };
        let payload = message.get("payload").cloned().unwrap_or(Value::Null);
        check_payload(&op, &payload)?;
        Ok(Frame {
            request_id,
            run_id,
            session,
            op,
            payload,
        })
    }

    /// The frame as its writer sends it, newline included: `ensure_ascii` as the controller
    /// writes, without it as the key helper writes.
    pub fn encode(&self, ensure_ascii: bool) -> String {
        let message = Value::Object(vec![
            ("schema".into(), int(1)),
            ("request_id".into(), int(self.request_id)),
            ("run_id".into(), Value::String(self.run_id.clone())),
            ("session".into(), Value::String(self.session.clone())),
            ("op".into(), Value::String(self.op.clone())),
            ("payload".into(), self.payload.clone()),
        ]);
        let style = if ensure_ascii {
            Style::COMPACT
        } else {
            Style::COMPACT_UNICODE
        };
        json::encode(&message, style) + "\n"
    }
}

/// The frames of one run and session waiting to be applied (`ipc.ControlQueue`).
#[derive(Debug)]
pub struct Queue {
    run_id: String,
    session: String,
    last: u64,
    frames: VecDeque<Frame>,
}

impl Queue {
    pub fn new(run_id: &str, session: &str) -> Result<Queue, Error> {
        check_token(run_id)?;
        check_token(session)?;
        Ok(Queue {
            run_id: run_id.into(),
            session: session.into(),
            last: 0,
            frames: VecDeque::new(),
        })
    }

    /// Check `raw` and queue it: this run and session, a request id larger than the last taken,
    /// and room in the queue.
    pub fn push(&mut self, raw: &[u8]) -> Result<&Frame, Error> {
        let frame = Frame::decode(raw)?;
        if frame.run_id != self.run_id || frame.session != self.session {
            return Err(Error("wrong run/session".into()));
        }
        if frame.request_id <= self.last {
            return Err(Error("replayed request".into()));
        }
        if self.frames.len() >= MAX_QUEUED {
            return Err(Error("queue full".into()));
        }
        self.last = frame.request_id;
        self.frames.push_back(frame);
        Ok(self.frames.back().expect("just pushed"))
    }

    pub fn pop(&mut self) -> Option<Frame> {
        self.frames.pop_front()
    }
}

/// The supervisor's answer to `status`: `{"schema": 1, "request_id", "run_id", "session", ...}`
/// with the run's view, status, placements and the rest in `body`.
#[derive(Clone, Debug, PartialEq)]
pub struct StatusReply {
    pub request_id: u64,
    pub run_id: String,
    pub session: String,
    pub body: Vec<(String, Value)>,
}

impl StatusReply {
    /// The reply to `frame` carrying `body`.
    pub fn to(frame: &Frame, body: Vec<(String, Value)>) -> StatusReply {
        StatusReply {
            request_id: frame.request_id,
            run_id: frame.run_id.clone(),
            session: frame.session.clone(),
            body,
        }
    }

    /// Check a reply line, its newline included.
    pub fn decode(raw: &[u8]) -> Result<StatusReply, Error> {
        if raw.len() > MAX_FRAME
            || raw.last() != Some(&b'\n')
            || raw[..raw.len() - 1].contains(&b'\n')
        {
            return Err(Error("invalid frame".into()));
        }
        let Value::Object(entries) = json::parse(
            raw,
            Limits {
                max_bytes: MAX_FRAME,
                max_depth: MAX_DEPTH,
            },
        )?
        else {
            return Err(Error("not an object".into()));
        };
        let head: Vec<&str> = entries.iter().take(4).map(|(k, _)| k.as_str()).collect();
        if head != ["schema", "request_id", "run_id", "session"] {
            return Err(Error("invalid fields".into()));
        }
        let mut entries = entries.into_iter();
        let mut next = || entries.next().map(|(_, v)| v);
        let schema = next();
        if !is_schema(schema.as_ref()) {
            return Err(Error("invalid schema".into()));
        }
        let request_id = next()
            .as_ref()
            .and_then(request_id)
            .ok_or_else(|| Error("invalid request id".into()))?;
        let run_id = token(next().as_ref())?;
        let session = token(next().as_ref())?;
        Ok(StatusReply {
            request_id,
            run_id,
            session,
            body: entries.collect(),
        })
    }

    /// Whether this answers `frame` (the controller's check before it takes the status).
    pub fn answers(&self, frame: &Frame) -> bool {
        self.request_id == frame.request_id
            && self.run_id == frame.run_id
            && self.session == frame.session
    }

    /// The line as the supervisor writes it, newline included.
    pub fn encode(&self) -> String {
        let mut entries = vec![
            ("schema".to_string(), int(1)),
            ("request_id".into(), int(self.request_id)),
            ("run_id".into(), Value::String(self.run_id.clone())),
            ("session".into(), Value::String(self.session.clone())),
        ];
        entries.extend(self.body.iter().cloned());
        json::encode(&Value::Object(entries), Style::DEFAULT) + "\n"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn op_lists_have_no_overlap_and_no_repeat() {
        let mut all: Vec<&str> = PLAIN_OPS
            .iter()
            .chain(&OPTIONS_OPS)
            .chain(&VIDEO_OPS)
            .copied()
            .collect();
        let n = all.len();
        all.sort();
        all.dedup();
        assert_eq!(all.len(), n);
    }

    #[test]
    fn socket_path_needs_a_token() {
        assert_eq!(
            socket_path(Path::new("/run/user/1000"), "r1").unwrap(),
            PathBuf::from("/run/user/1000/xr-workspace-spike/r1/control.sock")
        );
        assert!(socket_path(Path::new("/run/user/1000"), "../x").is_err());
    }

    #[test]
    fn a_frame_round_trips_in_both_styles() {
        let raw = "{\"schema\":1,\"request_id\":3,\"run_id\":\"r\",\"session\":\"s\",\"op\":\"video_open\",\"payload\":{\"path\":\"/v/é\"}}\n";
        let frame = Frame::decode(raw.as_bytes()).unwrap();
        assert_eq!(frame.encode(false), raw);
        assert_eq!(frame.encode(true), raw.replace('é', "\\u00e9"));
    }
}
