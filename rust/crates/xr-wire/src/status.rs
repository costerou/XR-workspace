//! The renderer's status datagrams to the supervisor: one JSON object per line.
//!
//! [`decode`] applies the rules of `Supervisor.status_bytes` in `tools/native_spike/supervisor.py`.
//! A line is either a one-key message (`control_applied`, `control_refused`, `owner_readback` and
//! the four `*_geometry` messages) or a status object whose keys are a subset of the measured
//! values. The one-key messages whose bodies other modules check (`owner_readback` and the
//! geometries) come back as their raw JSON for those modules.
//!
//! Integers are refused past `u64`, which Python would take as a big integer in a measured value;
//! the renderer never sends one.

use crate::json::{self, Limits, Style, Value};
use std::fmt;

/// A line longer than this (newline included) is dropped whole (`status_bytes`).
pub const LINE_LIMIT: usize = 16384;
/// The most UTF-8 bytes a refusal's `op` may have.
pub const REFUSED_OP_LIMIT: usize = 64;
/// The most UTF-8 bytes a refusal's `detail` may have.
pub const REFUSED_DETAIL_LIMIT: usize = 256;

/// Why a line was refused. The supervisor ignores a refused line.
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

/// How the renderer reads the desktops.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CapturePath {
    Dmabuf,
    Shm,
}

/// The VR video player's state (`video.VIDEO_STATES`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Video {
    Off,
    Loading,
    Playing,
    Paused,
    Ended,
    Error,
}

const VIDEO: [(&str, Video); 6] = [
    ("off", Video::Off),
    ("loading", Video::Loading),
    ("playing", Video::Playing),
    ("paused", Video::Paused),
    ("ended", Video::Ended),
    ("error", Video::Error),
];

/// A status object. Every field is optional; an empty object is a valid status.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Status {
    pub presented_hz: Option<f64>,
    /// Frames missed so far. Its presence marks the first status (`startup.mark`).
    pub missed: Option<u64>,
    pub capture_path: Option<CapturePath>,
    pub pose_age_ms: Option<f64>,
    pub video: Option<Video>,
    pub tracking: Option<bool>,
    pub capture_failure_run: Option<u64>,
    /// Our driver's scan-out against wp_presentation (`--read-events`): the horizon error p95.
    pub vsync_error_ms: Option<f64>,
    /// Scan-out minus the drawn sample's time: a clock offset can make it negative, so only finite.
    pub sensor_to_vsync_ms: Option<f64>,
    /// The renderer's GPU time per frame since the last sample (GL_EXT_disjoint_timer_query; the Rust renderer only).
    pub gpu_p50_ms: Option<f64>,
    pub gpu_p95_ms: Option<f64>,
    pub gpu_max_ms: Option<f64>,
}

/// One decoded line.
#[derive(Clone, Debug, PartialEq)]
pub enum Datagram {
    /// The renderer applied the control request with this id.
    ControlApplied(u64),
    /// The renderer refused the control request with this id.
    ControlRefused { request_id: u64, op: String, detail: String },
    /// `options_menu.validate_owner_readback` checks the body.
    OwnerReadback(Value),
    /// `set_maximize_geometry` checks the body.
    MaximizeGeometry(Value),
    /// `free_pointer.parse_overview_geometry` checks the body.
    OverviewGeometry(Value),
    /// `free_pointer.parse_roll_geometry` checks the body.
    RollGeometry(Value),
    /// `free_pointer.parse_help_geometry` checks the body.
    HelpGeometry(Value),
    Status(Status),
}

/// A positive request id below 2^64.
fn request_id(v: &Value) -> Option<u64> {
    v.as_int().and_then(|n| u64::try_from(n).ok()).filter(|&n| n > 0)
}

fn bounded(v: &Value, limit: usize) -> Option<String> {
    v.as_str().filter(|s| s.len() <= limit).map(str::to_owned)
}

fn count(v: &Value) -> Option<u64> {
    v.as_int().and_then(|n| u64::try_from(n).ok())
}

/// A finite non-negative number, never a bool.
fn measure(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) if v.as_int().is_none_or(|i| u64::try_from(i).is_ok()) => {
            n.as_f64().filter(|x| x.is_finite() && *x >= 0.0)
        }
        _ => None,
    }
}

/// A finite number of either sign, never a bool.
fn finite(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64().filter(|x| x.is_finite()),
        _ => None,
    }
}

/// Decode one line, with or without its newline.
pub fn decode(line: &[u8]) -> Result<Datagram, Error> {
    if line.len() > LINE_LIMIT {
        return fail("status line too long");
    }
    let value = json::parse(line, Limits { max_bytes: LINE_LIMIT, max_depth: json::MAX_DEPTH })
        .map_err(|e| Error(e.0))?;
    let Value::Object(entries) = &value else { return fail("invalid status") };
    if let [(key, body)] = entries.as_slice() {
        let one = match key.as_str() {
            "control_applied" => Some(Datagram::ControlApplied(request_id(body).ok_or(Error("invalid receipt".into()))?)),
            "control_refused" => Some(refused(body)?),
            "owner_readback" => Some(Datagram::OwnerReadback(body.clone())),
            "maximize_geometry" => Some(Datagram::MaximizeGeometry(body.clone())),
            "overview_geometry" => Some(Datagram::OverviewGeometry(body.clone())),
            "roll_geometry" => Some(Datagram::RollGeometry(body.clone())),
            "help_geometry" => Some(Datagram::HelpGeometry(body.clone())),
            _ => None,
        };
        if let Some(one) = one {
            return Ok(one);
        }
    }
    let mut status = Status::default();
    for (key, v) in entries {
        let ok = match key.as_str() {
            "presented_hz" => measure(v).map(|x| status.presented_hz = Some(x)),
            "pose_age_ms" => measure(v).map(|x| status.pose_age_ms = Some(x)),
            "vsync_error_ms" => measure(v).map(|x| status.vsync_error_ms = Some(x)),
            "sensor_to_vsync_ms" => finite(v).map(|x| status.sensor_to_vsync_ms = Some(x)),
            "gpu_p50_ms" => measure(v).map(|x| status.gpu_p50_ms = Some(x)),
            "gpu_p95_ms" => measure(v).map(|x| status.gpu_p95_ms = Some(x)),
            "gpu_max_ms" => measure(v).map(|x| status.gpu_max_ms = Some(x)),
            "missed" => count(v).map(|n| status.missed = Some(n)),
            "capture_failure_run" => count(v).map(|n| status.capture_failure_run = Some(n)),
            "capture_path" => match v.as_str() {
                Some("dmabuf") => Some(CapturePath::Dmabuf),
                Some("shm") => Some(CapturePath::Shm),
                _ => None,
            }
            .map(|p| status.capture_path = Some(p)),
            "video" => v
                .as_str()
                .and_then(|s| VIDEO.iter().find(|(name, _)| *name == s))
                .map(|(_, state)| status.video = Some(*state)),
            "tracking" => match v {
                Value::Bool(b) => {
                    status.tracking = Some(*b);
                    Some(())
                }
                _ => None,
            },
            _ => return fail("invalid status"),
        };
        if ok.is_none() {
            return fail("invalid measured value");
        }
    }
    Ok(Datagram::Status(status))
}

fn refused(body: &Value) -> Result<Datagram, Error> {
    let keys = body.keys().ok_or(Error("invalid refusal".into()))?;
    if keys.len() != 3 || !["request_id", "op", "detail"].iter().all(|k| keys.contains(k)) {
        return fail("invalid refusal");
    }
    let field = |key| body.get(key).unwrap();
    match (
        request_id(field("request_id")),
        bounded(field("op"), REFUSED_OP_LIMIT),
        bounded(field("detail"), REFUSED_DETAIL_LIMIT),
    ) {
        (Some(request_id), Some(op), Some(detail)) => Ok(Datagram::ControlRefused { request_id, op, detail }),
        _ => fail("invalid refusal"),
    }
}

/// The renderer's receipt for an applied control request, newline included.
pub fn encode_applied(request_id: u64) -> String {
    format!("{{\"control_applied\":{request_id}}}\n")
}

/// The renderer's refusal of a control request, newline included. `op` and `detail` are cut to
/// their limits on a character boundary.
pub fn encode_refused(request_id: u64, op: &str, detail: &str) -> String {
    fn cut(s: &str, limit: usize) -> &str {
        let mut end = s.len().min(limit);
        while !s.is_char_boundary(end) {
            end -= 1;
        }
        &s[..end]
    }
    let body = Value::Object(vec![
        ("request_id".into(), Value::Number(request_id.into())),
        ("op".into(), Value::String(cut(op, REFUSED_OP_LIMIT).into())),
        ("detail".into(), Value::String(cut(detail, REFUSED_DETAIL_LIMIT).into())),
    ]);
    let line = Value::Object(vec![("control_refused".into(), body)]);
    format!("{}\n", json::encode(&line, Style::COMPACT))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(line: &str) -> Datagram {
        decode(line.as_bytes()).unwrap_or_else(|e| panic!("{line}: {e}"))
    }

    fn bad(line: &str) {
        assert!(decode(line.as_bytes()).is_err(), "{line}");
    }

    #[test]
    fn decodes_the_one_key_messages() {
        assert_eq!(ok("{\"control_applied\":7}\n"), Datagram::ControlApplied(7));
        assert_eq!(ok(&encode_applied(u64::MAX)), Datagram::ControlApplied(u64::MAX));
        for line in ["{\"control_applied\":0}", "{\"control_applied\":18446744073709551616}", "{\"control_applied\":1.0}", "{\"control_applied\":true}"] {
            bad(line);
        }
        assert_eq!(
            ok(&encode_refused(3, "options_move", "no such desktop")),
            Datagram::ControlRefused { request_id: 3, op: "options_move".into(), detail: "no such desktop".into() }
        );
        let long = encode_refused(3, &"é".repeat(40), &"x".repeat(300));
        let Datagram::ControlRefused { op, detail, .. } = ok(&long) else { panic!() };
        assert_eq!((op.len(), detail.len()), (64, 256));
        bad(r#"{"control_refused":{"request_id":3,"op":"o"}}"#);
        bad(r#"{"control_refused":{"request_id":0,"op":"o","detail":"d"}}"#);
        bad(&format!(r#"{{"control_refused":{{"request_id":1,"op":"{}","detail":"d"}}}}"#, "o".repeat(65)));
        bad(r#"{"control_refused":[]}"#);
        assert_eq!(ok(r#"{"owner_readback":{"a":1}}"#), Datagram::OwnerReadback(json::parse_strict(br#"{"a":1}"#).unwrap()));
        assert!(matches!(ok(r#"{"help_geometry":null}"#), Datagram::HelpGeometry(Value::Null)));
    }

    #[test]
    fn decodes_status_objects() {
        assert_eq!(ok("{}\n"), Datagram::Status(Status::default()));
        let line = r#"{"presented_hz":119.9,"missed":0,"capture_path":"dmabuf","pose_age_ms":3,"video":"paused","tracking":true,"capture_failure_run":2}"#;
        assert_eq!(
            ok(line),
            Datagram::Status(Status {
                presented_hz: Some(119.9),
                missed: Some(0),
                capture_path: Some(CapturePath::Dmabuf),
                pose_age_ms: Some(3.0),
                video: Some(Video::Paused),
                tracking: Some(true),
                capture_failure_run: Some(2),
                ..Status::default()
            })
        );
        let vsync = r#"{"tracking":true,"vsync_error_ms":0.476935,"sensor_to_vsync_ms":-3.500000}"#;
        assert_eq!(
            ok(vsync),
            Datagram::Status(Status {
                tracking: Some(true),
                vsync_error_ms: Some(0.476935),
                sensor_to_vsync_ms: Some(-3.5),
                ..Status::default()
            })
        );
        for line in [
            r#"{"presented_hz":-1}"#,
            r#"{"presented_hz":true}"#,
            r#"{"presented_hz":"1"}"#,
            r#"{"missed":1.5}"#,
            r#"{"missed":-1}"#,
            r#"{"capture_path":"gpu"}"#,
            r#"{"video":"stopped"}"#,
            r#"{"tracking":1}"#,
            r#"{"gpu_p50_ms":-0.1}"#,
            r#"{"gpu_max_ms":"1"}"#,
            r#"{"fps":1}"#,
            r#"{"missed":1,"missed":2}"#,
            r#"{"presented_hz":NaN}"#,
            r#"{"vsync_error_ms":-0.1}"#,
            r#"{"vsync_error_ms":true}"#,
            r#"{"sensor_to_vsync_ms":false}"#,
            r#"{"sensor_to_vsync_ms":"1"}"#,
            "[]",
            "{",
        ] {
            bad(line);
        }
        // A one-key message name inside a bigger object is not that message.
        bad(r#"{"control_applied":1,"missed":0}"#);
        bad(&format!("{{\"video\":\"off\"{}}}", " ".repeat(LINE_LIMIT)));
    }
}
