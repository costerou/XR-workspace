//! The panel's socket to the controller (`tools/xr_controller/server.py`): newline-delimited JSON on
//! `$XDG_RUNTIME_DIR/viture-workspace/controller.sock`, owner-only, same UID, at most
//! [`MAX_CLIENTS`] connections.
//!
//! The panel (`ControllerClient.qml`) sends a [`Request`]: its own keys, then `"schema": 1` and a
//! `request_id`, written by `JSON.stringify` (compact, non-ASCII as is). The controller answers each
//! request with a [`Message::Reply`] (`{"schema": 1, ...reply, "request_id": id}`) and pushes a
//! [`Message::State`] (`{"schema": 1, "event": "state", ...snapshot}`) to every client whenever the
//! snapshot changes; both are `json.dumps(..., separators=(',', ':'))`, so ASCII only.
//!
//! The envelope is all the wire checks: a line within [`MAX_FRAME`], strict JSON, an object,
//! `schema` the integer 1 and a `request_id` that is absent, `null` or an integer in
//! `1..=u64::MAX`. Anything else ends the connection. The op and the rest of the request are the
//! controller's: an op it does not know is answered with `ok: false`, not refused here.
//!
//! `rust/corpus/sockets/frames.json` (`tests/xr_controller/socket_corpus.py`) holds what the shipped
//! panel and controller wrote and what the shipped server made of wrong lines.

use std::path::{Path, PathBuf};

use crate::json::{self, Error, Limits, MAX_DEPTH, Style, Value};

/// The longest line, its newline included.
pub const MAX_FRAME: usize = 16384;
/// The most panels connected at once; the next connection is closed unanswered.
pub const MAX_CLIENTS: usize = 32;

/// The socket under the user's runtime directory (`$XDG_RUNTIME_DIR`).
pub fn socket_path(runtime_dir: &Path) -> PathBuf {
    runtime_dir.join("viture-workspace").join("controller.sock")
}

/// The ops the controller takes (`state.Controller._request`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Status,
    GetSettings,
    SetSettings,
    SetPro2Projection,
    BeginStart,
    ConfirmStart,
    BeginRecover,
    ConfirmRecover,
    Cancel,
    Stop,
    VideoOpen,
    ViewOp,
}

impl Op {
    pub const ALL: [Op; 12] = [
        Op::Status,
        Op::GetSettings,
        Op::SetSettings,
        Op::SetPro2Projection,
        Op::BeginStart,
        Op::ConfirmStart,
        Op::BeginRecover,
        Op::ConfirmRecover,
        Op::Cancel,
        Op::Stop,
        Op::VideoOpen,
        Op::ViewOp,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Op::Status => "status",
            Op::GetSettings => "get_settings",
            Op::SetSettings => "set_settings",
            Op::SetPro2Projection => "set_pro2_projection",
            Op::BeginStart => "begin_start",
            Op::ConfirmStart => "confirm_start",
            Op::BeginRecover => "begin_recover",
            Op::ConfirmRecover => "confirm_recover",
            Op::Cancel => "cancel",
            Op::Stop => "stop",
            Op::VideoOpen => "video_open",
            Op::ViewOp => "view_op",
        }
    }

    pub fn from_name(name: &str) -> Option<Op> {
        Op::ALL.into_iter().find(|op| op.name() == name)
    }
}

/// The snapshot's keys in the order the controller writes them (`state.Controller.snapshot`).
/// `pro2` is there only for a Pro 2 with its bridge installed.
pub const SNAPSHOT_KEYS: [&str; 22] = [
    "fallback",
    "fallback_candidates",
    "pending",
    "state",
    "model",
    "output",
    "reason",
    "run_id",
    "journal",
    "capabilities",
    "view",
    "status",
    "placements",
    "desktops",
    "names",
    "video",
    "tracking_in_use",
    "start_steps",
    "start_phase",
    "pro2",
    "immersive",
    "incident",
];

/// Whether `snapshot`'s keys are [`SNAPSHOT_KEYS`] in order, `pro2` there or not.
pub fn is_snapshot(snapshot: &Value) -> bool {
    let Value::Object(entries) = snapshot else {
        return false;
    };
    let keys: Vec<&str> = entries.iter().map(|(k, _)| k.as_str()).collect();
    let all: Vec<&str> = SNAPSHOT_KEYS.to_vec();
    let without: Vec<&str> = SNAPSHOT_KEYS.into_iter().filter(|k| *k != "pro2").collect();
    keys == all || keys == without
}

/// Splits what a connection sends into lines, as the server reads them.
#[derive(Debug, Default)]
pub struct Lines {
    buffer: Vec<u8>,
}

impl Lines {
    /// Add what arrived.
    pub fn extend(&mut self, bytes: &[u8]) {
        self.buffer.extend_from_slice(bytes);
    }

    /// The next whole line without its newline, `None` until one has arrived. An error ends the
    /// connection: a line longer than [`MAX_FRAME`] with its newline, or [`MAX_FRAME`] bytes
    /// waiting with no newline.
    pub fn next_line(&mut self) -> Result<Option<Vec<u8>>, Error> {
        match self.buffer.iter().position(|&b| b == b'\n') {
            Some(end) => {
                let line: Vec<u8> = self.buffer.drain(..=end).take(end).collect();
                if line.len() + 1 > MAX_FRAME {
                    return Err(Error("oversize frame".into()));
                }
                Ok(Some(line))
            }
            None if self.buffer.len() >= MAX_FRAME => Err(Error("oversize frame".into())),
            None => Ok(None),
        }
    }
}

/// A request from the panel. `message` is the whole object as sent, `schema` and `request_id`
/// included, so writing it again gives the same bytes.
#[derive(Clone, Debug, PartialEq)]
pub struct Request {
    /// The id the reply carries back; `None` when absent or `null` (the reply then has none).
    pub request_id: Option<u64>,
    pub message: Value,
}

impl Request {
    /// A request as the panel builds it: `fields` (the op and its arguments), then `schema` and
    /// `request_id`.
    pub fn new(fields: Vec<(String, Value)>, request_id: u64) -> Request {
        let mut entries: Vec<(String, Value)> = fields
            .into_iter()
            .filter(|(k, _)| k != "schema" && k != "request_id")
            .collect();
        entries.push(("schema".into(), int(1)));
        entries.push(("request_id".into(), int(request_id)));
        Request {
            request_id: Some(request_id),
            message: Value::Object(entries),
        }
    }

    /// Check one line (without its newline) as the server does.
    pub fn decode(line: &[u8]) -> Result<Request, Error> {
        if line.len() + 1 > MAX_FRAME {
            return Err(Error("oversize frame".into()));
        }
        let message = json::parse(
            line,
            Limits {
                max_bytes: MAX_FRAME,
                max_depth: MAX_DEPTH,
            },
        )?;
        if !matches!(message, Value::Object(_)) || !is_schema(message.get("schema")) {
            return Err(Error("bad schema".into()));
        }
        let request_id = match message.get("request_id") {
            None | Some(Value::Null) => None,
            Some(id) => Some(request_id(id).ok_or_else(|| Error("bad request_id".into()))?),
        };
        Ok(Request {
            request_id,
            message,
        })
    }

    /// The op, when it is one the controller knows. `None` is answered `ok: false`.
    pub fn op(&self) -> Option<Op> {
        self.message
            .get("op")
            .and_then(Value::as_str)
            .and_then(Op::from_name)
    }

    /// The line as the panel writes it, newline included.
    pub fn encode(&self) -> String {
        json::encode(&self.message, Style::COMPACT_UNICODE) + "\n"
    }
}

/// What the controller sends a panel.
#[derive(Clone, Debug, PartialEq)]
pub enum Message {
    /// The answer to a request. `body` is the reply object between `schema` and `request_id`:
    /// `ok`, the snapshot, and the op's result or `error`.
    Reply {
        request_id: Option<u64>,
        body: Vec<(String, Value)>,
    },
    /// The snapshot, pushed whenever it changes.
    State { snapshot: Value },
}

impl Message {
    /// Check one line from the controller (without its newline).
    pub fn decode(line: &[u8]) -> Result<Message, Error> {
        if line.len() + 1 > MAX_FRAME {
            return Err(Error("oversize frame".into()));
        }
        let Value::Object(mut entries) = json::parse(
            line,
            Limits {
                max_bytes: MAX_FRAME,
                max_depth: MAX_DEPTH,
            },
        )?
        else {
            return Err(Error("not an object".into()));
        };
        match entries.first() {
            Some((k, v)) if k == "schema" && is_schema(Some(v)) => {}
            _ => return Err(Error("bad schema".into())),
        }
        entries.remove(0);
        if let Some(event) = take(&mut entries, "event") {
            if event.as_str() != Some("state") {
                return Err(Error("unknown event".into()));
            }
            return Ok(Message::State {
                snapshot: Value::Object(entries),
            });
        }
        let request_id = match take(&mut entries, "request_id") {
            None => None,
            Some(id) => Some(request_id(&id).ok_or_else(|| Error("bad request_id".into()))?),
        };
        if !matches!(
            entries.iter().find(|(k, _)| k == "ok"),
            Some((_, Value::Bool(_)))
        ) {
            return Err(Error("reply without ok".into()));
        }
        Ok(Message::Reply {
            request_id,
            body: entries,
        })
    }

    /// The line as the controller writes it, newline included.
    pub fn encode(&self) -> String {
        let mut entries = vec![("schema".to_string(), int(1))];
        match self {
            Message::Reply { request_id, body } => {
                entries.extend(body.iter().cloned());
                if let Some(id) = request_id {
                    entries.push(("request_id".into(), int(*id)));
                }
            }
            Message::State { snapshot } => {
                entries.push(("event".into(), Value::String("state".into())));
                if let Value::Object(fields) = snapshot {
                    entries.extend(fields.iter().cloned());
                }
            }
        }
        json::encode(&Value::Object(entries), Style::COMPACT) + "\n"
    }
}

fn take(entries: &mut Vec<(String, Value)>, key: &str) -> Option<Value> {
    let at = entries.iter().position(|(k, _)| k == key)?;
    Some(entries.remove(at).1)
}

/// `type(schema) is int and schema == 1`.
pub(crate) fn is_schema(value: Option<&Value>) -> bool {
    value.and_then(Value::as_int) == Some(1)
}

/// An integer in `1..=u64::MAX`, never a bool or a float.
pub(crate) fn request_id(value: &Value) -> Option<u64> {
    value
        .as_int()
        .filter(|&id| id > 0)
        .and_then(|id| u64::try_from(id).ok())
}

pub(crate) fn int(n: impl Into<serde_json::Number>) -> Value {
    Value::Number(n.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_request_puts_schema_and_id_last() {
        let request = Request::new(
            vec![
                ("op".into(), Value::String("view_op".into())),
                ("operation".into(), Value::String("recenter".into())),
            ],
            7,
        );
        assert_eq!(
            request.encode(),
            "{\"op\":\"view_op\",\"operation\":\"recenter\",\"schema\":1,\"request_id\":7}\n"
        );
        assert_eq!(
            Request::decode(request.encode().trim_end().as_bytes()).unwrap(),
            request
        );
        assert_eq!(request.op(), Some(Op::ViewOp));
    }

    #[test]
    fn lines_split_and_bound_what_arrives() {
        let mut lines = Lines::default();
        lines.extend(b"{\"a\":1}\n{\"b\"");
        assert_eq!(lines.next_line().unwrap(), Some(b"{\"a\":1}".to_vec()));
        assert_eq!(lines.next_line().unwrap(), None);
        lines.extend(b":2}\n");
        assert_eq!(lines.next_line().unwrap(), Some(b"{\"b\":2}".to_vec()));
        lines.extend(&vec![b' '; MAX_FRAME - 1]);
        assert_eq!(lines.next_line().unwrap(), None);
        lines.extend(b" ");
        assert!(lines.next_line().is_err());
    }

    #[test]
    fn socket_path_is_under_the_runtime_dir() {
        assert_eq!(
            socket_path(Path::new("/run/user/1000")),
            PathBuf::from("/run/user/1000/viture-workspace/controller.sock")
        );
    }
}
