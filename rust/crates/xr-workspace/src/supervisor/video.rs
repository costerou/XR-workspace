//! VR video in a capture run, supervisor side (tools/native_spike/video.py, docs/vr-video.md): control requests,
//! renderer payloads, the file checks, the ffprobe of the opened descriptor, format detection and limits.
//!
//! The file is opened here and probed through `/proc/self/fd` (never the path again, so it cannot be swapped in
//! between); the renderer gets the open descriptor over the run's control socket and never sees a path.

use std::ffi::CString;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::time::Duration;

use serde_json::{Map, Value};

use super::process::{self, Invocation, RunError, Runner};
use super::quality::{self, Quality};
use super::error::{Error, Result};
use super::{json, web_video};

/// The video control ops (control socket and binds).
pub const VIDEO_OPS: [&str; 12] = [
    "video_open", "video_pause", "video_seek_back", "video_seek_fwd", "video_seek_back_big", "video_seek_fwd_big",
    "video_format", "video_controls", "video_exit", "video_quality", "video_up", "video_down",
];
/// The ops without a payload sent by the video's keys.
pub const KEY_OPS: [&str; 8] = [
    "video_pause", "video_seek_back", "video_seek_fwd", "video_seek_back_big", "video_seek_fwd_big", "video_format",
    "video_controls", "video_exit",
];
/// Seconds each seek key moves.
pub fn seek_seconds(op: &str) -> Option<i64> {
    match op {
        "video_seek_back" => Some(-10),
        "video_seek_fwd" => Some(10),
        "video_seek_back_big" => Some(-60),
        "video_seek_fwd_big" => Some(60),
        _ => None,
    }
}

pub const FOV_RANGE: (i64, i64) = (180, 220);
pub const VIDEO_STATES: [&str; 6] = ["off", "loading", "playing", "paused", "ended", "error"];
/// ipc.hpp kMaxHelpText.
pub const MAX_TITLE: usize = 48;
pub const MAX_PATH: usize = 4096;
/// Larger frames are refused (beyond GL texture limits).
pub const MAX_SIDE: i64 = 16384;
/// Larger frames are scaled down by the renderer (video.hpp kVideoMaxTextureSide).
pub const DISPLAY_SIDE: i64 = 8192;
pub const FFPROBE_TIMEOUT: Duration = Duration::from_secs(10);
pub const FFPROBE_MAX_OUTPUT: usize = 1 << 20;
/// Renderer: kMaxQualityRows.
pub const MAX_MENU_ROWS: usize = 8;

/// Hardware decode width limit of this machine's AMD VCN 4 decoder per codec. Advisory only (warnings).
pub fn hw_decode_max_width(codec: &str) -> Option<i64> {
    match codec {
        "h264" => Some(4096),
        "hevc" | "vp9" | "av1" => Some(8192),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Projection {
    P360,
    P180,
    Fisheye,
}

impl Projection {
    pub fn as_str(self) -> &'static str {
        match self {
            Projection::P360 => "360",
            Projection::P180 => "180",
            Projection::Fisheye => "fisheye",
        }
    }
    fn parse(text: &str) -> Option<Projection> {
        match text {
            "360" => Some(Projection::P360),
            "180" => Some(Projection::P180),
            "fisheye" => Some(Projection::Fisheye),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stereo {
    Mono,
    Sbs,
    Tb,
}

impl Stereo {
    pub fn as_str(self) -> &'static str {
        match self {
            Stereo::Mono => "mono",
            Stereo::Sbs => "sbs",
            Stereo::Tb => "tb",
        }
    }
    fn parse(text: &str) -> Option<Stereo> {
        match text {
            "mono" => Some(Stereo::Mono),
            "sbs" => Some(Stereo::Sbs),
            "tb" => Some(Stereo::Tb),
            _ => None,
        }
    }
}

/// A video format: projection, stereo layout and (fisheye) lens field of view in degrees.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Format {
    pub projection: Projection,
    pub stereo: Stereo,
    pub fov: i64,
}

impl Format {
    pub const fn new(projection: Projection, stereo: Stereo, fov: i64) -> Format {
        Format { projection, stereo, fov }
    }

    fn fields(self) -> [(&'static str, String); 3] {
        [
            ("projection", json::string(self.projection.as_str())),
            ("stereo", json::string(self.stereo.as_str())),
            ("fov", self.fov.to_string()),
        ]
    }

    pub fn to_json(self) -> String {
        json::object(&self.fields())
    }
}

use Projection::{Fisheye, P180, P360};
use Stereo::{Mono, Sbs, Tb};

/// Tab cycles through these, starting after the current format.
pub const FORMAT_CYCLE: [Format; 10] = [
    Format::new(P360, Mono, 180),
    Format::new(P360, Tb, 180),
    Format::new(P360, Sbs, 180),
    Format::new(P180, Sbs, 180),
    Format::new(P180, Tb, 180),
    Format::new(P180, Mono, 180),
    Format::new(Fisheye, Sbs, 180),
    Format::new(Fisheye, Sbs, 190),
    Format::new(Fisheye, Sbs, 200),
    Format::new(Fisheye, Mono, 180),
];

/// The new format a `video_open` or renderer payload names. Python: validate_format.
pub fn validate_format(value: &Value) -> Result<Format> {
    const BAD: Error = Error::Invalid("invalid video format");
    let Value::Object(map) = value else { return Err(BAD) };
    if !json::has_exact_keys(map, &["projection", "stereo", "fov"]) {
        return Err(BAD);
    }
    format_from(map).ok_or(BAD)
}

fn format_from(map: &Map<String, Value>) -> Option<Format> {
    let projection = Projection::parse(map.get("projection")?.as_str()?)?;
    let stereo = Stereo::parse(map.get("stereo")?.as_str()?)?;
    let fov = json::int(map.get("fov")?)?;
    (FOV_RANGE.0..=FOV_RANGE.1).contains(&fov).then_some(Format { projection, stereo, fov })
}

/// A checked control-socket video request.
#[derive(Debug, Clone, PartialEq)]
pub enum Request {
    /// A key op: no payload.
    Key,
    /// A local file.
    Open { path: String, format: Option<Format> },
    /// A web video (web_video.rs): `{"url"}` or `{"web"}` as sent; local hosts are refused only when it opens.
    OpenWeb { url: Option<Value>, web: Option<Value>, format: Option<Format> },
}

/// Control-socket payload of a video op. video_open: `{"path": absolute path[, "format": {...}]}` or a web form.
pub fn validate_request(op: &str, payload: &Value) -> Result<Request> {
    let Value::Object(map) = payload else { return Err(Error::Invalid("invalid video request")) };
    if !VIDEO_OPS.contains(&op) {
        return Err(Error::Invalid("invalid video request"));
    }
    if op != "video_open" {
        return if map.is_empty() { Ok(Request::Key) } else { Err(Error::Invalid("unexpected payload")) };
    }
    let targets = ["path", "url", "web"].iter().filter(|k| map.contains_key(**k)).count();
    if map.keys().any(|k| !["path", "url", "web", "format"].contains(&k.as_str())) || targets != 1 {
        return Err(Error::Invalid("invalid video_open"));
    }
    // The target is checked before the format, as Python does, so a request bad in both names the target.
    let format = || map.get("format").map(validate_format).transpose();
    if !map.contains_key("path") {
        web_video::web_request(map, true)?;
        return Ok(Request::OpenWeb { url: map.get("url").cloned(), web: map.get("web").cloned(), format: format()? });
    }
    match &map["path"] {
        Value::String(path) if path.starts_with('/') && (2..=MAX_PATH).contains(&path.len()) && !path.contains('\0') => {
            Ok(Request::Open { path: path.clone(), format: format()? })
        }
        _ => Err(Error::Invalid("video_open needs an absolute path")),
    }
}

/// A `video` frame payload for the renderer (spike/src/video_ipc.cpp).
#[derive(Debug, Clone, PartialEq)]
pub enum Payload {
    Open { format: Format, title: String },
    Format(Format),
    Close,
    Pause,
    Seek(i64),
    Controls(bool),
    Quality(Quality),
    Menu { visible: bool, selected: i64, rows: Vec<quality::MenuRow> },
}

impl Payload {
    pub fn to_json(&self) -> String {
        let action = |a: &str| ("action", json::string(a));
        match self {
            Payload::Open { format, title } => {
                let mut fields = vec![action("open")];
                fields.extend(format.fields());
                fields.push(("title", json::string(title)));
                json::object(&fields)
            }
            Payload::Format(format) => {
                let mut fields = vec![action("format")];
                fields.extend(format.fields());
                json::object(&fields)
            }
            Payload::Close => json::object(&[action("close")]),
            Payload::Pause => json::object(&[action("pause")]),
            Payload::Seek(s) => json::object(&[action("seek"), ("seconds", s.to_string())]),
            Payload::Controls(v) => json::object(&[action("controls"), ("visible", v.to_string())]),
            Payload::Quality(q) => quality::renderer_payload(*q),
            Payload::Menu { visible, selected, rows } => {
                let rows = json::array(rows.iter().map(|r| {
                    json::object(&[("label", json::string(&r.label)), ("value", json::string(&r.value))])
                }));
                json::object(&[action("menu"), ("visible", visible.to_string()), ("selected", selected.to_string()), ("rows", rows)])
            }
        }
    }

    /// The renderer decoder's checks on a payload built here.
    pub fn validate(&self) -> Result<()> {
        let value: Value = serde_json::from_str(&self.to_json()).map_err(|_| Error::Invalid("invalid video payload"))?;
        validate_renderer_payload(&value).map(|_| ())
    }
}

fn text_ok(value: &Value, max: usize) -> bool {
    value.as_str().is_some_and(|t| json::printable_ascii(t, 1, max))
}

/// Mirror of the renderer's decoder (spike/src/video_ipc.cpp); tests run both over the same fixture.
pub fn validate_renderer_payload(p: &Value) -> Result<Payload> {
    const BAD: Error = Error::Invalid("invalid video payload");
    let Value::Object(map) = p else { return Err(BAD) };
    let Some(action) = map.get("action").and_then(Value::as_str) else { return Err(BAD) };
    let keys: &[&str] = match action {
        "open" => &["action", "projection", "stereo", "fov", "title"],
        "format" => &["action", "projection", "stereo", "fov"],
        "close" | "pause" => &["action"],
        "seek" => &["action", "seconds"],
        "controls" => &["action", "visible"],
        "quality" => &["action", "filter", "deband"],
        "menu" => &["action", "visible", "selected", "rows"],
        _ => return Err(BAD),
    };
    if !json::has_exact_keys(map, keys) {
        return Err(BAD);
    }
    let format = || format_from(map).ok_or(Error::Invalid("invalid video format"));
    Ok(match action {
        "open" => {
            let format = format()?;
            if !text_ok(&map["title"], MAX_TITLE) {
                return Err(Error::Invalid("invalid video title"));
            }
            Payload::Open { format, title: map["title"].as_str().unwrap_or_default().to_owned() }
        }
        "format" => Payload::Format(format()?),
        "close" => Payload::Close,
        "pause" => Payload::Pause,
        "seek" => match json::int(&map["seconds"]) {
            Some(s) if s != 0 && (-3600..=3600).contains(&s) => Payload::Seek(s),
            _ => return Err(Error::Invalid("invalid seek")),
        },
        "controls" => match map["visible"] {
            Value::Bool(v) => Payload::Controls(v),
            _ => return Err(Error::Invalid("invalid controls")),
        },
        "quality" => {
            let q: Map<String, Value> = map.iter().filter(|(k, _)| *k != "action").map(|(k, v)| (k.clone(), v.clone())).collect();
            Payload::Quality(quality::validate(&Value::Object(q))?)
        }
        _ => validate_menu(map)?,
    })
}

fn validate_menu(map: &Map<String, Value>) -> Result<Payload> {
    const BAD: Error = Error::Invalid("invalid quality menu");
    let (Value::Bool(visible), Value::Array(rows)) = (&map["visible"], &map["rows"]) else { return Err(BAD) };
    if !(1..=MAX_MENU_ROWS).contains(&rows.len()) {
        return Err(BAD);
    }
    let mut out = Vec::new();
    for row in rows {
        let Value::Object(r) = row else { return Err(BAD) };
        if !json::has_exact_keys(r, &["label", "value"]) || !text_ok(&r["label"], quality::MAX_LABEL) || !text_ok(&r["value"], quality::MAX_LABEL) {
            return Err(BAD);
        }
        out.push(quality::MenuRow { label: r["label"].as_str().unwrap_or_default().into(), value: r["value"].as_str().unwrap_or_default().into() });
    }
    match json::int(&map["selected"]) {
        Some(s) if s >= 0 && (s as usize) < out.len() => Ok(Payload::Menu { visible: *visible, selected: s, rows: out }),
        _ => Err(BAD),
    }
}

/// The `video` control frame carrying `payload`, newline-terminated. The payload is checked first.
pub fn video_frame(run_id: &str, session: &str, request_id: i64, payload: &Payload) -> Result<Vec<u8>> {
    payload.validate()?;
    Ok(json::frame(run_id, session, request_id, "video", &payload.to_json()))
}

/// The file name as the card shows it: printable ASCII only (the pixel font), at most MAX_TITLE characters.
pub fn ascii_title(path: &str) -> String {
    let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    ascii_text(&name)
}

pub fn ascii_text(name: &str) -> String {
    let mut text = String::new();
    for c in name.chars() {
        let c = if (' '..='~').contains(&c) { c } else { '?' };
        if !(c == ' ' && text.ends_with(' ')) {
            text.push(c);
        }
    }
    let mut text = text.trim().to_owned();
    if text.len() > MAX_TITLE {
        text.truncate(MAX_TITLE - 3);
        text = format!("{}...", text.trim_end());
    }
    if text.is_empty() { String::from("VR video") } else { text }
}

pub fn format_label(fmt: &Format) -> String {
    let head = match fmt.projection {
        P360 => String::from("360°"),
        P180 => String::from("180°"),
        Fisheye => format!("Fisheye {}°", fmt.fov),
    };
    let tail = match fmt.stereo {
        Mono => " mono",
        Sbs => " side-by-side 3D",
        Tb => " top-bottom 3D",
    };
    head + tail
}

pub fn next_format(fmt: &Format) -> Format {
    let next = FORMAT_CYCLE.iter().position(|f| f == fmt).map_or(0, |i| i + 1);
    FORMAT_CYCLE[next % FORMAT_CYCLE.len()]
}

// --- the file: open, probe, detect ---------------------------------------------------------------------------------

/// Read-only descriptor (close-on-exec, blocking) for a regular, non-empty file: no FIFOs, devices or directories.
pub fn open_video_file(path: &str) -> Result<OwnedFd> {
    if !path.starts_with('/') {
        return Err(Error::rejected("needs an absolute path"));
    }
    let c = CString::new(path).map_err(|_| Error::Invalid("embedded null byte"))?;
    let flags = libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOCTTY | libc::O_NONBLOCK;
    // SAFETY: `c` is a valid NUL-terminated path.
    let raw = unsafe { libc::open(c.as_ptr(), flags) };
    if raw < 0 {
        let err = std::io::Error::last_os_error();
        return Err(Error::Rejected(match err.raw_os_error() {
            Some(libc::ENOENT) => String::from("file not found"),
            Some(libc::EACCES | libc::EPERM) => String::from("file not readable"),
            Some(libc::EISDIR) => String::from("that is a folder, not a video file"),
            _ => err.to_string(),
        }));
    }
    // SAFETY: `raw` is a descriptor just opened here and owned by nothing else.
    let fd = unsafe { OwnedFd::from_raw_fd(raw) };
    let file = std::fs::File::from(fd);
    let meta = file.metadata().map_err(|e| Error::Rejected(e.to_string()))?;
    if !meta.file_type().is_file() {
        return Err(Error::rejected("not a regular file"));
    }
    if meta.len() == 0 {
        return Err(Error::rejected("the file is empty"));
    }
    let fd = OwnedFd::from(file);
    // SAFETY: fcntl on a descriptor this function owns.
    unsafe {
        let fl = libc::fcntl(fd.as_raw_fd(), libc::F_GETFL);
        if fl < 0 || libc::fcntl(fd.as_raw_fd(), libc::F_SETFL, fl & !libc::O_NONBLOCK) < 0 {
            return Err(Error::Rejected(std::io::Error::last_os_error().to_string()));
        }
    }
    Ok(fd)
}

pub fn ffprobe_path() -> Option<String> {
    process::find_tool("ffprobe").map(|p| p.to_string_lossy().into_owned())
}

/// What ffprobe says about the video stream. `projection` and `stereo` are None when the file does not say.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ProbeInfo {
    pub width: i64,
    pub height: i64,
    pub codec: String,
    pub fps: Option<f64>,
    pub duration: Option<f64>,
    /// The spherical mapping as ffprobe names it, lowercased.
    pub projection: Option<String>,
    /// Degrees covered horizontally, from the spherical bounds.
    pub coverage: Option<f64>,
    pub stereo: Option<Stereo>,
    /// The container (web streams only: ffprobe's format_name).
    pub container: Option<String>,
}

/// ffprobe the already opened file (`/proc/self/fd/N` in the child, file protocol only). None if ffprobe is missing.
/// `ffprobe`: the program, or None to look it up.
pub fn probe_fd(fd: RawFd, ffprobe: Option<&str>, run: Runner) -> Result<Option<ProbeInfo>> {
    let exe = match ffprobe {
        Some(e) => Some(e.to_owned()),
        None => ffprobe_path(),
    };
    let Some(exe) = exe.filter(|e| !e.is_empty()) else { return Ok(None) };
    let argv = [
        exe.as_str(), "-v", "error", "-hide_banner", "-protocol_whitelist", "file", "-print_format", "json",
        "-show_streams", "-show_format", &format!("file:/proc/self/fd/{fd}"),
    ];
    let inv = Invocation {
        argv: argv.iter().map(|s| s.to_string()).collect(),
        env: process::tool_env(),
        pass_fd: Some(fd),
        timeout: FFPROBE_TIMEOUT,
        max_output: FFPROBE_MAX_OUTPUT,
    };
    let out = match run(&inv) {
        Ok(out) => out,
        Err(RunError::Timeout) => return Err(Error::rejected("the file took too long to read")),
        Err(RunError::Spawn(_)) => return Ok(None),
    };
    if out.returncode != 0 || out.stdout_over || out.stdout.len() > FFPROBE_MAX_OUTPUT {
        return Err(Error::rejected("not a playable video file"));
    }
    let data: Value = serde_json::from_slice(&out.stdout).map_err(|_| Error::rejected("not a playable video file"))?;
    parse_probe(&data).map(Some)
}

/// Python `int(v)` for what ffprobe prints: ints, floats (truncated) and decimal text. None if it is not a number.
pub(crate) fn py_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => {
            let t = s.trim();
            let digits = t.strip_prefix(['+', '-']).unwrap_or(t);
            let clean: String = digits.chars().filter(|c| *c != '_').collect();
            if clean.is_empty() || !clean.chars().all(|c| c.is_ascii_digit()) || digits.starts_with('_') || digits.ends_with('_') || digits.contains("__") {
                return None;
            }
            let n: i64 = clean.parse().ok()?;
            Some(if t.starts_with('-') { -n } else { n })
        }
        _ => None,
    }
}

/// Python `float(text)`.
fn py_float(text: &str) -> Option<f64> {
    let t = text.trim();
    if t.contains("__") || t.starts_with('_') || t.ends_with('_') {
        return None;
    }
    t.replace('_', "").parse::<f64>().ok()
}

/// "30000/1001" or "12.5" as a number; None if not one or divided by zero.
fn rate(value: Option<&Value>) -> Option<f64> {
    let text = json::text(value.unwrap_or(&Value::Null));
    let (n, d) = text.split_once('/').unwrap_or((&text, ""));
    let n = py_float(n)?;
    let d = if d.is_empty() { 1.0 } else { py_float(d)? };
    if d == 0.0 { None } else { Some(n / d) }
}

fn stereo_type(text: &str) -> Option<Stereo> {
    match text {
        "side by side" | "sidebyside" => Some(Sbs),
        "top and bottom" | "topbottom" => Some(Tb),
        "2d" => Some(Mono),
        _ => None,
    }
}

fn mkv_stereo(text: &str) -> Option<Stereo> {
    match text {
        "left_right" | "right_left" | "block_lr" | "block_rl" => Some(Sbs),
        "top_bottom" | "bottom_top" => Some(Tb),
        "mono" => Some(Mono),
        _ => None,
    }
}

fn get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.as_object().and_then(|m| m.get(key))
}

/// A video stream that is not cover art.
pub(crate) fn is_video_stream(s: &Value) -> bool {
    s.is_object()
        && get(s, "codec_type").and_then(Value::as_str) == Some("video")
        && !get(s, "disposition").filter(|d| json::truthy(d)).and_then(|d| get(d, "attached_pic")).is_some_and(json::truthy)
}

/// ffprobe JSON -> ProbeInfo; the first video stream that is not cover art.
pub fn parse_probe(data: &Value) -> Result<ProbeInfo> {
    let Value::Object(_) = data else { return Err(Error::rejected("not a playable video file")) };
    let streams: &[Value] = get(data, "streams").and_then(Value::as_array).map_or(&[], Vec::as_slice);
    let Some(v) = streams.iter().find(|s| is_video_stream(s)) else {
        return Err(Error::rejected("no video stream in the file"));
    };
    let (w, h) = (get(v, "width").and_then(py_int), get(v, "height").and_then(py_int));
    let (Some(w), Some(h)) = (w, h) else { return Err(Error::rejected("the video has no picture size")) };
    if w <= 0 || h <= 0 {
        return Err(Error::rejected("the video has no picture size"));
    }
    let codec = get(v, "codec_name").filter(|c| json::truthy(c)).map_or_else(|| String::from("unknown"), json::text);
    let nonzero = |f: f64| f != 0.0;
    let mut info = ProbeInfo {
        width: w,
        height: h,
        codec: json::truncate_chars(&codec, 32),
        fps: rate(get(v, "avg_frame_rate")).filter(|f| nonzero(*f)).or_else(|| rate(get(v, "r_frame_rate"))),
        duration: rate(get(data, "format").filter(|f| json::truthy(f)).and_then(|f| get(f, "duration"))),
        ..ProbeInfo::default()
    };
    let sides: &[Value] = get(v, "side_data_list").and_then(Value::as_array).map_or(&[], Vec::as_slice);
    for side in sides.iter().filter(|s| s.is_object()) {
        match get(side, "side_data_type").and_then(Value::as_str) {
            Some("Spherical Mapping") => {
                info.projection = Some(get(side, "projection").map_or_else(String::new, json::text).to_lowercase());
                let bound = |k| get(side, k).and_then(py_int).unwrap_or(0);
                let (left, right) = (bound("bound_left"), bound("bound_right"));
                if left != 0 || right != 0 {
                    info.coverage = Some(360.0 * (1.0 - (left + right) as f64 / 4294967296.0).max(0.0));
                }
            }
            Some("Stereo 3D") => info.stereo = stereo_type(&get(side, "type").map_or_else(String::new, json::text).to_lowercase()),
            _ => {}
        }
    }
    let tag = get(v, "tags").filter(|t| json::truthy(t)).and_then(|t| get(t, "stereo_mode")).map_or_else(String::new, json::text);
    if info.stereo.is_none() {
        info.stereo = mkv_stereo(&tag.to_lowercase());
    }
    Ok(info)
}

fn near(a: f64, b: f64) -> bool {
    (a - b).abs() <= 0.08 * b
}

const LENSES: [(&str, i64); 8] = [
    ("fisheye", 180), ("fisheye180", 180), ("fisheye190", 190), ("fisheye200", 200), ("mkx200", 200),
    ("mkx220", 220), ("rf52", 190), ("vrca220", 220),
];

/// (format, source). The source is 'metadata' (spherical or stereo tags in the file), 'filename', 'shape' or
/// 'default'; when several gave a part, the weakest is reported.
///
/// File metadata wins, then file-name conventions (_360, _180/VR180, _LR/_SBS/_3dh, _TB/_OU/_3dv, _FISHEYE190,
/// _MKX200, _RF52), then the picture's shape (2:1 -> 360° mono, 1:1 -> 360° top-bottom, 4:1 -> 360° side-by-side),
/// else 360° mono.
pub fn detect_format(name: &str, info: Option<&ProbeInfo>) -> Result<(Format, &'static str)> {
    let (mut projection, mut stereo, mut fov) = (None, None, None);
    let mut sources: Vec<&'static str> = Vec::new();
    if let Some(meta) = info.and_then(|i| i.projection.as_deref()).filter(|m| !m.is_empty()) {
        if ["cubemap", "mesh", "rectilinear"].contains(&meta) {
            return Err(Error::Rejected(format!("{meta} projection is not supported (equirectangular and fisheye only)")));
        }
        projection = if meta == "fisheye" {
            Some(Fisheye)
        } else if meta == "half equirectangular" {
            Some(P180)
        } else if meta.contains("equirectangular") {
            Some(if info.and_then(|i| i.coverage).is_some_and(|c| c <= 200.0) { P180 } else { P360 })
        } else {
            None
        };
        if projection.is_some() {
            sources.push("metadata");
        }
    }
    if let Some(s) = info.and_then(|i| i.stereo) {
        stereo = Some(s);
        sources.push("metadata");
    }
    let lower = name.to_lowercase();
    let mut tokens: Vec<&str> = lower.split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit())).collect();
    tokens.sort_unstable();
    tokens.dedup();
    let any = |set: &[&str]| tokens.iter().any(|t| set.contains(t));
    if projection.is_none() {
        if let Some((_, f)) = tokens.iter().find_map(|t| LENSES.iter().find(|(l, _)| l == t)) {
            (projection, fov) = (Some(Fisheye), Some(*f));
        } else if any(&["360", "vr360", "360x180", "360vr", "mono360", "tb360", "sbs360"]) {
            projection = Some(P360);
        } else if any(&["180", "vr180", "180x180", "180vr", "180sbs", "180lr", "180tb"]) {
            projection = Some(P180);
        }
        if projection.is_some() {
            sources.push("filename");
        }
    }
    if stereo.is_none() {
        if any(&["sbs", "lr", "rl", "3dh", "leftright", "halfsbs", "hsbs", "180sbs", "180lr", "sbs360"]) {
            stereo = Some(Sbs);
        } else if any(&["tb", "ou", "ab", "3dv", "tab", "topbottom", "overunder", "htab", "180tb", "tb360"]) {
            stereo = Some(Tb);
        } else if any(&["mono", "2d", "mono360"]) {
            stereo = Some(Mono);
        }
        if stereo.is_some() {
            sources.push("filename");
        }
    }
    let ratio = info.filter(|i| i.width != 0 && i.height != 0).map(|i| i.width as f64 / i.height as f64);
    if let Some(ratio) = ratio.filter(|r| *r != 0.0) {
        let mut shaped = false;
        match (projection, stereo) {
            (None, Some(s)) => {
                let eye = match s {
                    Sbs => ratio / 2.0,
                    Tb => ratio * 2.0,
                    Mono => ratio,
                };
                projection = if near(eye, 2.0) { Some(P360) } else if near(eye, 1.0) { Some(P180) } else { None };
                shaped = projection.is_some();
            }
            (Some(P180 | Fisheye), None) => {
                stereo = if near(ratio, 2.0) {
                    Some(Sbs)
                } else if near(ratio, 0.5) {
                    Some(Tb)
                } else if near(ratio, 1.0) {
                    Some(Mono)
                } else {
                    None
                };
                shaped = stereo.is_some();
            }
            (Some(P360), None) => {
                stereo = if near(ratio, 2.0) {
                    Some(Mono)
                } else if near(ratio, 1.0) {
                    Some(Tb)
                } else if near(ratio, 4.0) {
                    Some(Sbs)
                } else {
                    None
                };
                shaped = stereo.is_some();
            }
            (None, None) => {
                stereo = [(Mono, 2.0), (Tb, 1.0), (Sbs, 4.0)].iter().find(|(_, r)| near(ratio, *r)).map(|(s, _)| *s);
                projection = stereo.map(|_| P360);
                shaped = stereo.is_some();
            }
            _ => {}
        }
        if shaped {
            sources.push("shape");
        }
    }
    if projection.is_none() || stereo.is_none() {
        sources.push("default");
    }
    let fmt = Format::new(projection.unwrap_or(P360), stereo.unwrap_or(Mono), fov.unwrap_or(180));
    let source = ["default", "shape", "filename", "metadata"].into_iter().find(|s| sources.contains(s)).unwrap_or("default");
    Ok((fmt, source))
}

/// Refuse what cannot be shown; return warnings (shown in the panel) for what may play badly.
pub fn check_limits(info: Option<&ProbeInfo>) -> Result<Vec<String>> {
    let Some(info) = info else { return Ok(vec![String::from("format not checked (ffprobe unavailable)")]) };
    let (w, h, codec) = (info.width, info.height, info.codec.as_str());
    if w.max(h) > MAX_SIDE {
        return Err(Error::Rejected(format!("{w}x{h} is larger than {MAX_SIDE} px")));
    }
    let mut warnings = Vec::new();
    if w.max(h) > DISPLAY_SIDE {
        warnings.push(format!("{w}x{h} is shown scaled down to {DISPLAY_SIDE} px"));
    }
    if hw_decode_max_width(codec).is_none_or(|limit| w > limit) {
        warnings.push(format!("{codec} {w}x{h} decodes in software here and may stutter"));
    }
    if let Some(fps) = info.fps.filter(|f| *f != 0.0 && *f > 61.0 && w * h > 3840 * 2160) {
        warnings.push(format!("{fps:.0} fps above 4K may drop frames"));
    }
    Ok(warnings)
}

/// A file ready to hand to the renderer.
#[derive(Debug)]
pub struct Opened {
    pub fd: OwnedFd,
    pub title: String,
    pub format: Format,
    /// How the format was found: detect_format's sources, 'chosen' (the request named it) or 'player' (web).
    pub source: &'static str,
    pub warnings: Vec<String>,
}

/// Open, probe and detect a local file (VideoControl._prepare's worker body). `probe`: probe_fd or a stand-in.
pub fn open_file(path: &str, chosen: Option<Format>, probe: &dyn Fn(RawFd) -> Result<Option<ProbeInfo>>) -> Result<Opened> {
    let fd = open_video_file(path)?;
    let info = probe(fd.as_raw_fd())?;
    let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let (mut format, mut source) = detect_format(&name, info.as_ref())?;
    if let Some(chosen) = chosen {
        (format, source) = (chosen, "chosen");
    }
    let warnings = check_limits(info.as_ref())?;
    Ok(Opened { fd, title: ascii_title(path), format, source, warnings })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::process::Output;
    use crate::testdir::TestDir;
    use serde_json::json;

    fn info(w: i64, h: i64, codec: &str) -> ProbeInfo {
        ProbeInfo { width: w, height: h, codec: codec.into(), fps: Some(30.0), duration: Some(60.0), ..ProbeInfo::default() }
    }

    fn fmt(p: Projection, s: Stereo, f: i64) -> Format {
        Format::new(p, s, f)
    }

    #[test]
    fn shared_fixture_matches_the_renderer_decoder() {
        let data = crate::supervisor::fixture("video_payloads.json");
        let valid = data["valid"].as_array().unwrap();
        assert!(!valid.is_empty());
        for p in valid {
            let payload = validate_renderer_payload(p).unwrap_or_else(|e| panic!("{p}: {e}"));
            let frame = video_frame("run1", "s1", 3, &payload).unwrap();
            let back: Value = serde_json::from_slice(&frame).unwrap();
            assert_eq!(&back["payload"], p);
            assert_eq!((back["op"].as_str(), back["request_id"].as_i64()), (Some("video"), Some(3)));
            assert!(frame.ends_with(b"\n"));
        }
        for p in data["invalid"].as_array().unwrap() {
            assert!(validate_renderer_payload(p).is_err(), "{p}");
        }
    }

    #[test]
    fn requests_on_the_control_socket() {
        assert_eq!(
            validate_request("video_open", &json!({"path": "/home/me/Videos/a_360.mp4"})).unwrap(),
            Request::Open { path: "/home/me/Videos/a_360.mp4".into(), format: None }
        );
        let chosen = json!({"projection": "180", "stereo": "sbs", "fov": 180});
        assert_eq!(
            validate_request("video_open", &json!({"path": "/v.mkv", "format": chosen})).unwrap(),
            Request::Open { path: "/v.mkv".into(), format: Some(fmt(P180, Sbs, 180)) }
        );
        for op in KEY_OPS {
            assert_eq!(validate_request(op, &json!({})).unwrap(), Request::Key);
        }
        let long = format!("/{}", "x".repeat(4096));
        let bad = [
            ("video_open", json!({})),
            ("video_open", json!({"path": "relative.mp4"})),
            ("video_open", json!({"path": "/a\u{0}b"})),
            ("video_open", json!({"path": long})),
            ("video_open", json!({"path": "/v.mp4", "extra": 1})),
            ("video_open", json!({"path": "/v.mp4", "format": {"projection": "cubemap", "stereo": "mono", "fov": 180}})),
            ("video_pause", json!({"x": 1})),
            ("video_play", json!({})),
            ("video_open", json!({"url": "https://a.example/x.mp4", "path": "/x.mp4"})),
            ("video_open", json!({"url": "ftp://a/x"})),
            ("video_open", json!({"web": {"sources": []}})),
        ];
        for (op, payload) in bad {
            assert!(validate_request(op, &payload).is_err(), "{op} {payload}");
        }
        // The shape check accepts local hosts; the run refuses them when it opens.
        assert!(matches!(validate_request("video_open", &json!({"url": "http://127.0.0.1/a.mp4"})), Ok(Request::OpenWeb { .. })));
        // Bad in both target and format: the target's error, as video.py checks it first.
        let both = validate_request("video_open", &json!({"path": "rel.mp4", "format": {"projection": "x"}}));
        assert!(matches!(both, Err(Error::Invalid("video_open needs an absolute path"))), "{both:?}");
        let both = validate_request("video_open", &json!({"url": "ftp://a/x", "format": {"projection": "x"}}));
        assert!(!matches!(both, Err(Error::Invalid("invalid video format"))), "{both:?}");
    }

    #[test]
    fn titles_formats_and_cycle() {
        assert_eq!(ascii_title("/x/Concert VR180_LR.mp4"), "Concert VR180_LR.mp4");
        assert_eq!(ascii_title("/x/café_360.mp4"), "caf?_360.mp4");
        let long = ascii_title(&format!("/x/{}.mp4", "a".repeat(80)));
        assert_eq!(long.len(), MAX_TITLE);
        assert!(long.ends_with("..."));
        assert_eq!(ascii_text("  a \t b  "), "a ? b");
        assert_eq!(ascii_text(""), "VR video");
        assert_eq!(format_label(&fmt(Fisheye, Sbs, 190)), "Fisheye 190° side-by-side 3D");
        assert_eq!(format_label(&fmt(P360, Tb, 180)), "360° top-bottom 3D");
        let mut f = FORMAT_CYCLE[0];
        let mut seen = Vec::new();
        for _ in FORMAT_CYCLE {
            f = next_format(&f);
            seen.push(f);
        }
        assert_eq!(*seen.last().unwrap(), FORMAT_CYCLE[0]);
        seen.sort_by_key(|f| (f.projection as u8, f.stereo as u8, f.fov));
        seen.dedup();
        assert_eq!(seen.len(), FORMAT_CYCLE.len());
        assert_eq!(next_format(&fmt(Fisheye, Tb, 211)), FORMAT_CYCLE[0]);
    }

    fn detect(name: &str, i: ProbeInfo) -> (Format, &'static str) {
        detect_format(name, Some(&i)).unwrap()
    }

    #[test]
    fn metadata_wins() {
        let full = ProbeInfo { projection: Some("equirectangular".into()), stereo: Some(Tb), ..info(3840, 1920, "hevc") };
        assert_eq!(detect("trip_180_LR.mp4", full), (fmt(P360, Tb, 180), "metadata"));
        let tiled = ProbeInfo { projection: Some("tiled equirectangular".into()), coverage: Some(180.0), stereo: Some(Sbs), ..info(5760, 2880, "hevc") };
        assert_eq!(detect("x.mp4", tiled).0, fmt(P180, Sbs, 180));
        let half = ProbeInfo { projection: Some("half equirectangular".into()), stereo: Some(Sbs), ..info(8192, 4096, "hevc") };
        assert_eq!(detect("x.mp4", half).0.projection, P180);
        let fish = ProbeInfo { projection: Some("fisheye".into()), ..info(8192, 4096, "hevc") };
        assert_eq!(detect("x.mp4", fish), (fmt(Fisheye, Sbs, 180), "shape"));
        for unsupported in ["cubemap", "mesh", "rectilinear"] {
            let i = ProbeInfo { projection: Some(unsupported.into()), ..info(3840, 1920, "hevc") };
            assert!(matches!(detect_format("x.mp4", Some(&i)), Err(Error::Rejected(_))));
        }
    }

    #[test]
    fn file_name_conventions() {
        let cases = [
            ("beach_360_TB.mp4", fmt(P360, Tb, 180)),
            ("beach_360_LR.mp4", fmt(P360, Sbs, 180)),
            ("show_180_sbs.mkv", fmt(P180, Sbs, 180)),
            ("show_180x180_3dh_LR.mp4", fmt(P180, Sbs, 180)),
            ("clip_VR180_OU.mp4", fmt(P180, Tb, 180)),
            ("canon_FISHEYE190.mp4", fmt(Fisheye, Sbs, 190)),
            ("x_MKX200.mp4", fmt(Fisheye, Sbs, 200)),
            ("cam_RF52_LR.mov", fmt(Fisheye, Sbs, 190)),
            ("x_mono_360.mp4", fmt(P360, Mono, 180)),
        ];
        for (name, want) in cases {
            let (got, source) = detect(name, info(4096, 2048, "hevc"));
            assert_eq!(got, want, "{name}");
            assert!(["filename", "shape"].contains(&source), "{name}");
        }
    }

    #[test]
    fn shape_and_default() {
        assert_eq!(detect("a.mp4", info(3840, 1920, "hevc")), (fmt(P360, Mono, 180), "shape"));
        assert_eq!(detect("a.mp4", info(4096, 4096, "hevc")), (fmt(P360, Tb, 180), "shape"));
        assert_eq!(detect("a.mp4", info(7680, 1920, "hevc")).0.stereo, Sbs);
        assert_eq!(detect("a_180.mp4", info(5760, 2880, "hevc")).0, fmt(P180, Sbs, 180));
        assert_eq!(detect("a_180.mp4", info(2880, 2880, "hevc")).0.stereo, Mono);
        assert_eq!(detect("a_360.mp4", info(4096, 4096, "hevc")).0.stereo, Tb);
        assert_eq!(detect("a_sbs.mp4", info(5760, 2880, "hevc")).0.projection, P180);
        assert_eq!(detect("a_tb.mp4", info(3840, 3840, "hevc")).0.projection, P360);
        assert_eq!(detect("a.mp4", info(1920, 1080, "hevc")), (fmt(P360, Mono, 180), "default"));
        assert_eq!(detect_format("a.mp4", None).unwrap().1, "default");
    }

    #[test]
    fn probe_parsing() {
        let data = json!({"streams": [{"codec_type": "audio"}, {"codec_type": "video", "disposition": {"attached_pic": 1}, "width": 300, "height": 300},
            {"codec_type": "video", "codec_name": "hevc", "width": 5760, "height": 2880, "avg_frame_rate": "30000/1001",
             "side_data_list": [{"side_data_type": "Spherical Mapping", "projection": "equirectangular", "bound_left": 1073741824, "bound_right": 1073741824},
                                {"side_data_type": "Stereo 3D", "type": "side by side", "inverted": 0}]}],
            "format": {"duration": "12.5"}});
        let i = parse_probe(&data).unwrap();
        assert_eq!((i.width, i.height, i.codec.as_str(), (i.fps.unwrap() * 100.0).round() / 100.0, i.duration), (5760, 2880, "hevc", 29.97, Some(12.5)));
        assert_eq!((i.projection.as_deref(), i.coverage, i.stereo), (Some("equirectangular"), Some(180.0), Some(Sbs)));
        assert_eq!(detect_format("x.mp4", Some(&i)).unwrap(), (fmt(P180, Sbs, 180), "metadata"));
        let mkv = json!({"streams": [{"codec_type": "video", "codec_name": "vp9", "width": 4096, "height": 4096, "tags": {"stereo_mode": "top_bottom"}}]});
        assert_eq!(parse_probe(&mkv).unwrap().stereo, Some(Tb));
        let r = json!({"streams": [{"codec_type": "video", "width": "640", "height": 480.7, "r_frame_rate": "25/1", "avg_frame_rate": "0/0"}]});
        let i = parse_probe(&r).unwrap();
        assert_eq!((i.width, i.height, i.codec.as_str(), i.fps, i.duration), (640, 480, "unknown", Some(25.0), None));
        for bad in [json!({}), json!({"streams": []}), json!({"streams": [{"codec_type": "video", "width": 0, "height": 10}]}), json!([])] {
            assert!(matches!(parse_probe(&bad), Err(Error::Rejected(_))), "{bad}");
        }
    }

    #[test]
    fn limits() {
        assert!(check_limits(Some(&info(16385, 8192, "hevc"))).is_err());
        assert_eq!(check_limits(Some(&info(7680, 3840, "hevc"))).unwrap(), Vec::<String>::new());
        assert_eq!(check_limits(Some(&info(3840, 1920, "h264"))).unwrap(), Vec::<String>::new());
        assert_eq!(check_limits(Some(&info(11520, 5760, "hevc"))).unwrap(), ["11520x5760 is shown scaled down to 8192 px", "hevc 11520x5760 decodes in software here and may stutter"]);
        assert!(check_limits(Some(&info(7680, 3840, "h264"))).unwrap().iter().any(|w| w.contains("software")));
        assert!(check_limits(Some(&info(3840, 1920, "prores"))).unwrap().iter().any(|w| w.contains("software")));
        let fast = ProbeInfo { fps: Some(90.0), ..info(7680, 3840, "hevc") };
        assert_eq!(check_limits(Some(&fast)).unwrap(), ["90 fps above 4K may drop frames"]);
        assert_eq!(check_limits(None).unwrap(), ["format not checked (ffprobe unavailable)"]);
    }

    #[test]
    fn only_readable_regular_files() {
        let tmp = TestDir::new("video-files");
        let good = tmp.path().join("a_360.mp4");
        std::fs::write(&good, b"xxxxxxxxxx").unwrap();
        let fd = open_video_file(good.to_str().unwrap()).unwrap();
        // SAFETY: fcntl reads the flags of a descriptor this test owns.
        let (fdfl, fl) = unsafe { (libc::fcntl(fd.as_raw_fd(), libc::F_GETFD), libc::fcntl(fd.as_raw_fd(), libc::F_GETFL)) };
        assert_ne!(fdfl & libc::FD_CLOEXEC, 0);
        assert_eq!(fl & libc::O_NONBLOCK, 0);
        let empty = tmp.path().join("empty.mp4");
        std::fs::write(&empty, b"").unwrap();
        let fifo = tmp.path().join("pipe.mp4");
        let c = CString::new(fifo.to_str().unwrap()).unwrap();
        // SAFETY: `c` is a valid path.
        assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
        let missing = tmp.path().join("missing.mp4");
        let cases = [
            (missing.to_str().unwrap(), "file not found"),
            (tmp.path().to_str().unwrap(), "not a regular file"),
            (empty.to_str().unwrap(), "the file is empty"),
            (fifo.to_str().unwrap(), "not a regular file"),
            ("relative.mp4", "needs an absolute path"),
            ("/dev/null", "not a regular file"),
        ];
        for (path, why) in cases {
            assert_eq!(open_video_file(path).unwrap_err(), Error::rejected(why), "{path}");
        }
    }

    #[test]
    fn probe_uses_the_descriptor_not_the_path() {
        let calls = std::cell::RefCell::new(Vec::new());
        let ok = |inv: &Invocation| {
            calls.borrow_mut().push(inv.clone());
            Ok(Output {
                stdout: br#"{"streams": [{"codec_type": "video", "codec_name": "av1", "width": 4096, "height": 2048}]}"#.to_vec(),
                ..Output::default()
            })
        };
        assert_eq!(probe_fd(7, Some("/usr/bin/ffprobe"), &ok).unwrap().unwrap().codec, "av1");
        let inv = calls.borrow()[0].clone();
        assert_eq!(inv.argv.last().unwrap(), "file:/proc/self/fd/7");
        assert_eq!(inv.pass_fd, Some(7));
        assert!(inv.argv.iter().any(|a| a == "-protocol_whitelist"));
        assert_eq!(inv.env, process::tool_env());
        assert_eq!(probe_fd(7, Some(""), &ok).unwrap(), None);
        let timeout = |_: &Invocation| Err(RunError::Timeout);
        assert_eq!(probe_fd(7, Some("/usr/bin/ffprobe"), &timeout).unwrap_err(), Error::rejected("the file took too long to read"));
        let failed = |_: &Invocation| Ok(Output { returncode: 1, stderr: b"Invalid data".to_vec(), ..Output::default() });
        assert_eq!(probe_fd(7, Some("/usr/bin/ffprobe"), &failed).unwrap_err(), Error::rejected("not a playable video file"));
        let missing = |_: &Invocation| Err(RunError::Spawn(std::io::Error::from(std::io::ErrorKind::NotFound)));
        assert_eq!(probe_fd(7, Some("/usr/bin/ffprobe"), &missing).unwrap(), None);
    }

    /// The real ffmpeg and ffprobe on a generated clip, as RealProbeTests does.
    #[test]
    fn probes_the_opened_descriptor_with_the_real_ffprobe() {
        let (Some(ffmpeg), Some(_)) = (process::find_tool("ffmpeg"), ffprobe_path()) else {
            eprintln!("skipped: ffmpeg/ffprobe needed to generate and probe a clip");
            return;
        };
        let tmp = TestDir::new("video-probe");
        let clip = tmp.path().join("holiday.mkv");
        let status = std::process::Command::new(ffmpeg)
            .args(["-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=512x512:r=30:d=1", "-c:v", "libx264",
                   "-preset", "ultrafast", "-metadata:s:v:0", "stereo_mode=top_bottom"])
            .arg(&clip)
            .status()
            .unwrap();
        assert!(status.success());
        let real = |fd: RawFd| probe_fd(fd, None, &process::run);
        let opened = open_file(clip.to_str().unwrap(), None, &real).unwrap();
        assert_eq!((opened.format, opened.source, opened.title.as_str()), (fmt(P360, Tb, 180), "shape", "holiday.mkv"));
        let fd = open_video_file(clip.to_str().unwrap()).unwrap();
        let i = real(fd.as_raw_fd()).unwrap().unwrap();
        assert_eq!((i.width, i.height, i.codec.as_str(), i.stereo), (512, 512, "h264", Some(Tb)));
        assert_eq!(i.fps, Some(30.0));
        let chosen = open_file(clip.to_str().unwrap(), Some(fmt(P180, Sbs, 180)), &real).unwrap();
        assert_eq!((chosen.format, chosen.source), (fmt(P180, Sbs, 180), "chosen"));
        let junk = tmp.path().join("notes.mp4");
        std::fs::write(&junk, "not a video").unwrap();
        assert_eq!(open_file(junk.to_str().unwrap(), None, &real).unwrap_err(), Error::rejected("not a playable video file"));
    }
}
