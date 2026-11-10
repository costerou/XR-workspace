//! The supervisor's requests to the Hyprland ownership plugin and the plugin's replies.
//!
//! A request is the hyprctl command `xrspike ` followed by one compact JSON object with exactly
//! seven keys: `schema`, `session`, `run_id`, `request_id`, `epoch`, `op` and `payload`.
//! [`Request::encode`] writes the bytes `tools/native_spike/ownership.py` (`_call`) writes, and
//! refuses anything the plugin would refuse: it checks its own output with [`parse_request`], a
//! mirror of `parse_request` in `spike/ownership/registry.cpp`. The hyprctl transport adds its
//! `j/` flag in front; that is not part of this wire.
//!
//! Schema 1 is the legacy run (`hello`, `cursor_shape` and the lifecycle ops). Schema 2 is the
//! isolated-VM pair (`pair_hello`, `pair_observe` and the lifecycle ops); every schema-2 payload
//! carries `"mode":"isolated-vm"`, added last as `IsolatedBrokerClient._payload` does.
//!
//! The epoch is the plugin's random token for its own lifetime. Until the hello (`hello` or
//! `pair_hello`) has been answered a request carries `"unknown"`; after that it carries the epoch
//! the hello's reply gave, and a reply with any other epoch means the plugin was reloaded: the run's
//! authority is lost and the journal must be kept. [`decode_reply`] applies those rules.

use crate::json::{self, Limits, Style, Value};
use serde_json::Number;
use std::fmt;

/// The hyprctl command, with its separating space.
pub const COMMAND: &str = "xrspike ";
/// The most bytes a request may have, command included (`parse_request`).
pub const MAX_REQUEST_BYTES: usize = 8192;
/// The most bytes a reply may have (`Broker::handle`, `ownership.py` `LIMIT`).
pub const MAX_REPLY_BYTES: usize = 1 << 20;
/// The highest request id the plugin takes (`parse_request`; `_call`'s "request capacity").
pub const MAX_REQUEST_ID: u32 = i32::MAX as u32;
/// The epoch a request carries before the hello has been answered.
pub const UNKNOWN_EPOCH: &str = "unknown";
/// The schema-2 payload mode.
pub const MODE: &str = "isolated-vm";
/// `parse_request`'s nesting limit: nothing may sit inside more than 4 arrays or objects.
const MAX_DEPTH: usize = 4;

/// Why a request or reply was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

fn fail<T>(why: &str) -> Result<T, Error> {
    Err(Error(why.to_owned()))
}

/// `ipc.TOKEN` and the plugin's token rule: `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`.
pub fn is_token(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=128).contains(&b.len())
        && b[0].is_ascii_alphanumeric()
        && b[1..].iter().all(|&c| c.is_ascii_alphanumeric() || b"_.:-".contains(&c))
}

/// A pair handle: 48 lowercase hex digits.
fn is_handle(s: &str) -> bool {
    s.len() == 48 && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

/// A lowercase UUID, `8-4-4-4-12`.
fn is_uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => c == b'-',
            _ => matches!(c, b'0'..=b'9' | b'a'..=b'f'),
        })
}

/// `validDesktop` in registry.cpp (`geometry.validate_desktop`): even sizes within 640x480 ..
/// 7680x4320, a scale from `geometry.SCALES` that divides both sides exactly.
pub fn valid_desktop(width: i64, height: i64, scale: f64) -> bool {
    if !(640..=7680).contains(&width) || !(480..=4320).contains(&height) || width % 2 != 0 || height % 2 != 0 {
        return false;
    }
    let n = (scale * 4.0).round() as i64;
    if !(4..=12).contains(&n) || (n > 8 && n % 2 != 0) || n as f64 / 4.0 != scale {
        return false;
    }
    (width * 4) % n == 0 && (height * 4) % n == 0
}

/// Which plugin protocol a run speaks.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Schema {
    /// A legacy run: name-based `hello` and the read-only `cursor_shape`.
    Legacy = 1,
    /// An isolated-VM pair run: `pair_hello` and `pair_observe`.
    Isolated = 2,
}

impl Schema {
    /// The op whose reply sets the epoch.
    pub fn hello(self) -> &'static str {
        match self {
            Schema::Legacy => "hello",
            Schema::Isolated => "pair_hello",
        }
    }
}

/// One managed output, as `create` and `reshape` send it.
#[derive(Clone, Debug, PartialEq)]
pub struct OutputSpec {
    /// The output's lowercase UUID.
    pub id: String,
    /// `XR-<index + 1>`.
    pub name: String,
    /// 0..=3.
    pub index: i64,
    pub width: i64,
    pub height: i64,
    /// Always 120000.
    pub refresh_millihz: i64,
    /// One of `geometry.SCALES`; written as a float, as Python writes it.
    pub scale: f64,
    pub x: i64,
    pub y: i64,
}

/// A request's operation and its payload.
#[derive(Clone, Debug, PartialEq)]
pub enum Op {
    /// Schema 1: adopt the named physical monitor as the run's fallback.
    Hello { fallback_name: String },
    /// Schema 2: prove the target and fallback outputs of an isolated pair.
    PairHello { target_name: String, fallback_name: String },
    /// Schema 2: re-prove the pair by the handles `pair_hello` gave.
    PairObserve { target: String, fallback: String },
    /// Schema 1: the cursor shape the app under the pointer asked for.
    CursorShape,
    Create(OutputSpec),
    Reshape(OutputSpec),
    Inspect { id: String },
    /// Move everything off the output, then remove it. `fallback` is the hello's fallback handle.
    EvacuateRemove { id: String, name: String, fallback: String },
}

fn string(s: &str) -> Value {
    Value::String(s.to_owned())
}

fn int(n: i64) -> Value {
    Value::Number(n.into())
}

fn object(entries: Vec<(&str, Value)>) -> Value {
    Value::Object(entries.into_iter().map(|(k, v)| (k.to_owned(), v)).collect())
}

impl Op {
    /// The op's name on the wire.
    pub fn name(&self) -> &'static str {
        match self {
            Op::Hello { .. } => "hello",
            Op::PairHello { .. } => "pair_hello",
            Op::PairObserve { .. } => "pair_observe",
            Op::CursorShape => "cursor_shape",
            Op::Create(_) => "create",
            Op::Reshape(_) => "reshape",
            Op::Inspect { .. } => "inspect",
            Op::EvacuateRemove { .. } => "evacuate_remove",
        }
    }

    fn payload(&self, schema: Schema) -> Value {
        let mut entries = match self {
            Op::Hello { fallback_name } => vec![("fallback_name", string(fallback_name))],
            Op::PairHello { target_name, fallback_name } => {
                vec![("target_name", string(target_name)), ("fallback_name", string(fallback_name))]
            }
            Op::PairObserve { target, fallback } => vec![("target", string(target)), ("fallback", string(fallback))],
            Op::CursorShape => vec![],
            Op::Create(spec) | Op::Reshape(spec) => {
                let scale = Number::from_f64(spec.scale).map_or(Value::Null, Value::Number);
                vec![
                    ("id", string(&spec.id)),
                    ("name", string(&spec.name)),
                    ("index", int(spec.index)),
                    ("width", int(spec.width)),
                    ("height", int(spec.height)),
                    ("refresh_millihz", int(spec.refresh_millihz)),
                    ("scale", scale),
                    ("x", int(spec.x)),
                    ("y", int(spec.y)),
                ]
            }
            Op::Inspect { id } => vec![("id", string(id))],
            Op::EvacuateRemove { id, name, fallback } => {
                vec![("id", string(id)), ("name", string(name)), ("fallback", string(fallback))]
            }
        };
        if schema == Schema::Isolated {
            entries.push(("mode", string(MODE)));
        }
        object(entries)
    }
}

/// One request to the plugin.
#[derive(Clone, Debug, PartialEq)]
pub struct Request {
    pub schema: Schema,
    pub session: String,
    pub run_id: String,
    /// 1..=[`MAX_REQUEST_ID`], strictly increasing per run.
    pub request_id: u32,
    /// The epoch the hello's reply gave; `None` before that (sent as `"unknown"`).
    pub epoch: Option<String>,
    pub op: Op,
}

impl Request {
    /// The seven-key envelope, in `_call`'s key order.
    pub fn envelope(&self) -> Value {
        object(vec![
            ("schema", int(self.schema as i64)),
            ("session", string(&self.session)),
            ("run_id", string(&self.run_id)),
            ("request_id", int(i64::from(self.request_id))),
            ("epoch", string(self.epoch.as_deref().unwrap_or(UNKNOWN_EPOCH))),
            ("op", string(self.op.name())),
            ("payload", self.op.payload(self.schema)),
        ])
    }

    /// The command text: `xrspike ` and the compact envelope. Refused unless the plugin would
    /// take it.
    pub fn encode(&self) -> Result<String, Error> {
        if self.request_id == 0 {
            return fail("request id starts at 1");
        }
        let wire = encode_envelope(&self.envelope());
        parse_request(wire.as_bytes())?;
        Ok(wire)
    }
}

/// `xrspike ` and `envelope` as `json.dumps(separators=(',', ':'), ensure_ascii=True)` writes it.
pub fn encode_envelope(envelope: &Value) -> String {
    format!("{COMMAND}{}", json::encode(envelope, Style::COMPACT))
}

fn str_of(v: Option<&Value>) -> Option<&str> {
    v.and_then(Value::as_str)
}

/// A non-negative integer literal (nlohmann `is_number_unsigned`).
fn unsigned(v: &Value) -> Option<u64> {
    match v {
        Value::Number(n) => n.as_u64(),
        _ => None,
    }
}

/// An integer literal of either sign (nlohmann `is_number_integer`), never a float.
fn integer(v: &Value) -> Option<i128> {
    v.as_int()
}

/// `parse_request` in `spike/ownership/registry.cpp`: the envelope and payload rules the plugin
/// checks before it acts. Returns the parsed envelope.
///
/// Strict JSON is `xr_wire::json`'s, which is stricter than nlohmann in a few corners
/// (a byte-order mark, a lone surrogate escape, `-0` read as an integer): none of them is ever
/// encoded here.
pub fn parse_request(wire: &[u8]) -> Result<Value, Error> {
    if wire.len() > MAX_REQUEST_BYTES || !wire.starts_with(COMMAND.as_bytes()) {
        return fail("request bounds/prefix");
    }
    let j = json::parse(&wire[COMMAND.len()..], Limits { max_bytes: MAX_REQUEST_BYTES, max_depth: MAX_DEPTH })
        .map_err(|e| Error(e.0))?;
    const FIELDS: [&str; 7] = ["schema", "session", "run_id", "request_id", "epoch", "op", "payload"];
    match j.keys() {
        Some(keys) if keys.len() == FIELDS.len() && FIELDS.iter().all(|k| keys.contains(k)) => {}
        _ => return fail("envelope"),
    }
    let schema = match j.get("schema").and_then(integer) {
        Some(1) => Schema::Legacy,
        Some(2) => Schema::Isolated,
        _ => return fail("types"),
    };
    let p = j.get("payload").unwrap();
    if !matches!(j.get("request_id").and_then(unsigned), Some(id) if id <= u64::from(MAX_REQUEST_ID))
        || !matches!(p, Value::Object(_))
    {
        return fail("types");
    }
    for key in ["session", "run_id", "epoch"] {
        if !str_of(j.get(key)).is_some_and(is_token) {
            return fail("token");
        }
    }
    let Some(op) = str_of(j.get("op")) else { return fail("operation") };
    let mut wanted: Vec<&str> = match (op, schema) {
        ("hello", Schema::Legacy) => vec!["fallback_name"],
        ("pair_hello", Schema::Isolated) => vec!["target_name", "fallback_name"],
        ("pair_observe", Schema::Isolated) => vec!["target", "fallback"],
        ("cursor_shape", Schema::Legacy) => vec![],
        ("inspect", _) => vec!["id"],
        ("evacuate_remove", _) => vec!["id", "name", "fallback"],
        ("create" | "reshape", _) => {
            vec!["id", "name", "index", "width", "height", "refresh_millihz", "scale", "x", "y"]
        }
        _ => return fail("operation"),
    };
    if schema == Schema::Isolated {
        if str_of(p.get("mode")) != Some(MODE) {
            return fail("pair mode");
        }
        wanted.push("mode");
    }
    let keys = p.keys().unwrap();
    if keys.len() != wanted.len() || !wanted.iter().all(|k| keys.contains(k)) {
        return fail("payload shape");
    }
    for key in ["id", "name", "fallback", "fallback_name", "target", "target_name"] {
        if let Some(v) = p.get(key) {
            if !v.as_str().is_some_and(is_token) {
                return fail("payload token");
            }
        }
    }
    if schema == Schema::Isolated {
        for key in ["target", "fallback"] {
            if str_of(p.get(key)).is_some_and(|s| !is_handle(s)) {
                return fail("pair handle");
            }
        }
    }
    if str_of(p.get("id")).is_some_and(|s| !is_uuid(s)) {
        return fail("UUID");
    }
    if op == "create" || op == "reshape" {
        let field = |key| p.get(key).and_then(integer).filter(|n| i128::from(i32::MIN) <= *n && *n <= i128::from(i32::MAX));
        let mut n = [0i64; 6];
        for (slot, key) in n.iter_mut().zip(["index", "width", "height", "refresh_millihz", "x", "y"]) {
            *slot = match field(key) {
                Some(v) => v as i64,
                None => return fail("integer"),
            };
        }
        let [index, width, height, refresh, _, _] = n;
        let scale = match p.get("scale") {
            Some(Value::Number(s)) => s.as_f64().unwrap_or(f64::NAN),
            _ => return fail("geometry"),
        };
        // XR-1..XR-4 (geometry.MAX_DESKTOPS); a fixed 120 Hz.
        if !(0..=3).contains(&index) || refresh != 120000 || !valid_desktop(width, height, scale) {
            return fail("geometry");
        }
        if str_of(p.get("name")) != Some(format!("XR-{}", index + 1).as_str()) {
            return fail("output name");
        }
    }
    Ok(j)
}

/// A reply that passed [`decode_reply`].
#[derive(Clone, Debug, PartialEq)]
pub struct Reply {
    /// The plugin's epoch. After a hello the caller keeps it for every later request.
    pub epoch: String,
    /// The op's result object.
    pub result: Value,
}

/// Check the plugin's reply to `request` to the letter, as `_call` does: at most 1 MiB, strict
/// JSON, exactly `schema`, `session`, `run_id`, `request_id`, `epoch` and `result`, the first four
/// equal to the request's (same JSON type too), an epoch token equal to the request's once one is
/// known, and an object result. A plugin refusal (`{"error": ...}`) is an error.
pub fn decode_reply(raw: &[u8], request: &Request) -> Result<Reply, Error> {
    if raw.len() > MAX_REPLY_BYTES {
        return fail("broker reply exceeds 1 MiB");
    }
    let reply = json::parse(raw, Limits { max_bytes: MAX_REPLY_BYTES, max_depth: json::MAX_DEPTH })
        .map_err(|e| Error(e.0))?;
    const FIELDS: [&str; 6] = ["schema", "session", "run_id", "request_id", "epoch", "result"];
    match reply.keys() {
        Some(keys) if keys.len() == FIELDS.len() && FIELDS.iter().all(|k| keys.contains(k)) => {}
        _ => return fail("missing/invalid ownership broker"),
    }
    let sent = request.envelope();
    for key in ["schema", "session", "run_id", "request_id"] {
        // Python compares `type(...)` and value: an integer stays an integer, never 1.0 or true.
        let (got, want) = (reply.get(key).unwrap(), sent.get(key).unwrap());
        let same = match (got, want) {
            (Value::String(a), Value::String(b)) => a == b,
            (Value::Number(_), Value::Number(_)) => got.as_int().is_some() && got.as_int() == want.as_int(),
            _ => false,
        };
        if !same {
            return fail("broker identity/reply mismatch");
        }
    }
    let Some(epoch) = str_of(reply.get("epoch")).filter(|e| is_token(e)) else {
        return fail("invalid identifier");
    };
    match &request.epoch {
        Some(known) if known != epoch => return fail("broker epoch lost; preserve journal"),
        None if request.op.name() != request.schema.hello() => return fail("handshake required"),
        _ => {}
    }
    let result = reply.get("result").unwrap();
    if !matches!(result, Value::Object(_)) {
        return fail("invalid broker result");
    }
    Ok(Reply { epoch: epoch.to_owned(), result: result.clone() })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hello() -> Request {
        Request {
            schema: Schema::Legacy,
            session: "guest".into(),
            run_id: "run".into(),
            request_id: 1,
            epoch: None,
            op: Op::Hello { fallback_name: "DP-1".into() },
        }
    }

    #[test]
    fn encodes_the_python_bytes() {
        assert_eq!(
            hello().encode().unwrap(),
            r#"xrspike {"schema":1,"session":"guest","run_id":"run","request_id":1,"epoch":"unknown","op":"hello","payload":{"fallback_name":"DP-1"}}"#
        );
        let create = Request {
            schema: Schema::Isolated,
            epoch: Some("a".repeat(48)),
            op: Op::Create(OutputSpec {
                id: "12345678-1234-1234-1234-123456789abc".into(),
                name: "XR-1".into(),
                index: 0,
                width: 1920,
                height: 1080,
                refresh_millihz: 120000,
                scale: 1.0,
                x: 1280,
                y: 0,
            }),
            ..hello()
        };
        let wire = create.encode().unwrap();
        assert!(
            wire.ends_with(r#""payload":{"id":"12345678-1234-1234-1234-123456789abc","name":"XR-1","index":0,"width":1920,"height":1080,"refresh_millihz":120000,"scale":1.0,"x":1280,"y":0,"mode":"isolated-vm"}}"#),
            "{wire}"
        );
    }

    #[test]
    fn refuses_what_the_plugin_refuses() {
        assert!(Request { request_id: 0, ..hello() }.encode().is_err());
        assert!(Request { schema: Schema::Isolated, ..hello() }.encode().is_err());
        assert!(Request { session: "not a token".into(), ..hello() }.encode().is_err());
        assert!(Request { op: Op::CursorShape, schema: Schema::Isolated, ..hello() }.encode().is_err());
        let over = Op::Hello { fallback_name: "x".repeat(129) };
        assert!(Request { op: over, ..hello() }.encode().is_err());
        assert!(parse_request(b"xrspike {}").is_err());
        assert!(parse_request(b"xrspike{}").is_err());
        let deep = r#"xrspike {"schema":1,"session":"s","run_id":"r","request_id":1,"epoch":"e","op":"hello","payload":{"fallback_name":"D","x":[[[1]]]}}"#;
        assert!(parse_request(deep.as_bytes()).unwrap_err().0.contains("nested"));
        let dup = r#"xrspike {"schema":1,"schema":1,"session":"s","run_id":"r","request_id":1,"epoch":"e","op":"hello","payload":{"fallback_name":"D"}}"#;
        assert!(parse_request(dup.as_bytes()).is_err());
    }

    #[test]
    fn desktops_match_the_geometry_rules() {
        assert!(valid_desktop(1920, 1080, 1.0));
        assert!(valid_desktop(1920, 1080, 1.5));
        assert!(!valid_desktop(1920, 1080, 1.75)); // 1080*4 % 7
        assert!(!valid_desktop(1920, 1080, 2.25));
        assert!(valid_desktop(1920, 1080, 2.5) == (1080 * 4 % 10 == 0));
        assert!(!valid_desktop(1921, 1080, 1.0));
        assert!(!valid_desktop(638, 480, 1.0));
    }

    fn reply(epoch: &str, request_id: &str) -> String {
        format!(
            r#"{{"schema":1,"session":"guest","run_id":"run","request_id":{request_id},"epoch":"{epoch}","result":{{"fallback":"t","qualified":true}}}}"#
        )
    }

    #[test]
    fn replies_follow_the_epoch_rules() {
        let e = "a".repeat(48);
        let got = decode_reply(reply(&e, "1").as_bytes(), &hello()).unwrap();
        assert_eq!(got.epoch, e);
        assert_eq!(got.result.get("qualified"), Some(&Value::Bool(true)));
        // Once known, the epoch must not change.
        let known = Request { epoch: Some("b".repeat(48)), ..hello() };
        assert!(decode_reply(reply(&e, "1").as_bytes(), &known).unwrap_err().0.contains("epoch lost"));
        // No epoch yet: only the hello may be answered.
        let inspect = Request { op: Op::Inspect { id: "x".into() }, ..hello() };
        assert!(decode_reply(reply(&e, "1").as_bytes(), &inspect).unwrap_err().0.contains("handshake"));
        assert!(decode_reply(reply(&e, "2").as_bytes(), &hello()).is_err());
        assert!(decode_reply(reply(&e, "1.0").as_bytes(), &hello()).is_err());
        assert!(decode_reply(reply("not a token", "1").as_bytes(), &hello()).is_err());
        assert!(decode_reply(br#"{"error":"ownership blocked"}"#, &hello()).is_err());
        let dup = reply(&e, "1").replacen("\"schema\":1,", "\"schema\":1,\"schema\":1,", 1);
        assert!(decode_reply(dup.as_bytes(), &hello()).is_err());
        let list = reply(&e, "1").replace(r#"{"fallback":"t","qualified":true}"#, "[]");
        assert!(decode_reply(list.as_bytes(), &hello()).is_err());
        assert!(decode_reply(&vec![b' '; MAX_REPLY_BYTES + 1], &hello()).is_err());
    }
}
