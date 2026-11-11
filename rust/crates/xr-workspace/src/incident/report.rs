//! Incident reports: `tools/xr_controller/incident_report.py`. The bundle, its redaction and size, how it is
//! written, and the hook that hands it on (docs/technical.md, Incident reports).
//!
//! Privacy works in two layers, as in Python. Structured fields are built from named values only. Text that comes
//! from logs goes through `Scrubber`, every string of the finished bundle included. Nothing here reads a capture
//! or a core file, only coredumpctl's text backtrace.

use std::collections::HashSet;
use std::ffi::CString;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::LazyLock;
use std::time::{Duration, Instant};

use regex::{Captures, Regex};
use serde_json::{Map, Value, json};

use super::detect::{self, Episode, Signal, faults_of, is_exit_report, signal_name};
use super::py::{
    NULL, as_int, as_str, decode, dumps, get, gm_stamp, iso_timestamp, iso_utc, py_eq, py_str,
    strip, truthy,
};
use crate::private_file::{invalid, private_dir, read_private_json};

pub const MAX_BUNDLE: usize = 256 * 1024;
pub const MAX_SUMMARY: usize = 4096;
pub const LINE_MAX: usize = 400;
pub const MAX_CORES: usize = 4;
pub const MAX_FRAMES: usize = 32;
/// Beside the reports, but no `*.json` a watcher of this folder would take for one.
pub const STATE: &str = ".monitor-state";
/// The same trouble again in a session that carries on.
pub const SAME_LIVE_S: f64 = 600.0;
/// A crash loop: each end is news, but not faster than this.
pub const SAME_ENDED_S: f64 = 60.0;
pub const MAX_PER_HOUR: usize = 12;
pub const HOOK_TIMEOUT: Duration = Duration::from_secs(30);
pub const MAX_HOOK_OUTPUT: usize = 64 * 1024;
pub const MAX_HOOK_LOG: u64 = 256 * 1024;
pub const REMOVED_LINE: &str = "<line removed: window or clipboard text>";
pub const PRIVACY: &str =
    "No screen content, window titles, clipboard text or video links. Logs are redacted.";

/// `tracking.USB_DENIED` and `tracking.CRASHED`: errors the run reports in words a person can act on.
pub const USB_DENIED: &str = "XR can't open the glasses (USB permission). Unplug and replug the glasses' USB, then try again.";
pub const TRACKING_CRASHED: &str = "The glasses' head tracking couldn't connect and the tracking driver crashed. Unplug the glasses' USB, wait 10 seconds, plug it back in and try again.";

/// Home paths a report may name; anything else in a home folder becomes `<private-path>`.
const APP_PATHS: [&str; 8] = [
    "/.local/share/viture-workspace",
    "/.local/state/viture-workspace",
    "/.local/state/xr_driver",
    "/.config/viture-workspace",
    "/.config/xr_driver",
    "/.config/systemd/user/viture-",
    "/.t3/worktrees/viture-workspace",
    "/Projects/viture-workspace",
];

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("a fixed pattern")
}

static PRIVATE_LINE: LazyLock<Regex> = LazyLock::new(|| {
    regex(
        r#"(?i)"(?:initial)?title"|\b(?:initial)?title\s*[:=]|\bwindow[ _-]?title\b|\bclipboard\b|\bwl-(?:copy|paste)\b"#,
    )
});
static PEM: LazyLock<Regex> = LazyLock::new(|| {
    regex(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\z)")
});
static URL: LazyLock<Regex> = LazyLock::new(|| regex(r"(?i)\b[a-z][a-z0-9+.-]{1,20}://\S+"));
static BARE_URL: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)\b(?:www\.\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*)"));
static BEARER: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]{6,}"));
static NAMED: LazyLock<Regex> = LazyLock::new(|| {
    regex(
        r#"(?i)\b([\w.-]*(?:password|passwd|passphrase|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|cookie|authorization|auth)[\w.-]*)(["']?\s*[=:]\s*)("[^"]*"|'[^']*'|\S+)"#,
    )
});
static TOKENS: LazyLock<Regex> = LazyLock::new(|| {
    regex(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abeoprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})",
    )
});
static MEDIA: LazyLock<Regex> = LazyLock::new(|| {
    regex(
        r#"(?i)(?:~|/)[^\n"']*?\.(?:mp4|mkv|webm|avi|mov|m4v|mpg|mpeg|wmv|flv|m2ts|mp3|flac|wav|ogg|opus|m3u8?)\b"#,
    )
});
// Python's `~(?=/)`: the regex crate has no lookahead, so a lone `~` matches here and is given back unchanged.
static HOME: LazyLock<Regex> =
    LazyLock::new(|| regex(r#"(?P<base>~|/home/[^/\s"']+|/root\b)(?P<rest>/[^\s"',;:)\]}]*)?"#));
static HEX: LazyLock<Regex> = LazyLock::new(|| regex(r"\b[0-9a-fA-F]{24,}\b"));
static EMAIL: LazyLock<Regex> = LazyLock::new(|| regex(r"[\w.+-]+@[\w-]+\.[\w.-]+"));
static IPV4: LazyLock<Regex> = LazyLock::new(|| regex(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"));
static MAC: LazyLock<Regex> = LazyLock::new(|| regex(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"));
static SERIAL: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)\b(serial\s*(?:number)?\s*[:=]\s*)\S+"));
static CONTROL: LazyLock<Regex> = LazyLock::new(|| regex(r"[\x00-\x1f\x7f]+"));

/// `[A-Za-z0-9+_-]`, what a token is made of.
fn base64_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b"+_-".contains(&b)
}

/// Makes any string fit for a report. `keep`: exact strings it would otherwise hide (the run's id, the build's hashes).
#[derive(Clone, Debug)]
pub struct Scrubber {
    home: String,
    user: String,
    host: String,
    keep: HashSet<String>,
    masks: Vec<(Regex, &'static str)>,
}

impl Scrubber {
    pub fn new(home: &str, user: &str, host: &str) -> Scrubber {
        let masks = [(user, "<user>"), (host, "<host>")]
            .into_iter()
            .filter(|(word, _)| !word.is_empty())
            .map(|(word, mask)| (regex(&format!(r"\b{}\b", regex::escape(word))), mask))
            .collect();
        Scrubber {
            home: home.trim_end_matches('/').into(),
            user: user.into(),
            host: host.into(),
            keep: HashSet::new(),
            masks,
        }
    }

    /// `Scrubber()`: this user's home, login name and host name, as Python finds them.
    pub fn for_this_host() -> Scrubber {
        let env = |name: &str| std::env::var(name).ok().filter(|v| !v.is_empty());
        // SAFETY: getpwuid returns a pointer into static storage or null; it is read at once, on this thread.
        let passwd = unsafe { libc::getpwuid(libc::getuid()).as_ref() };
        let field = |ptr: *const libc::c_char| {
            // SAFETY: a non-null passwd field is a NUL-terminated string.
            (!ptr.is_null()).then(|| {
                unsafe { std::ffi::CStr::from_ptr(ptr) }
                    .to_string_lossy()
                    .into_owned()
            })
        };
        let home = env("HOME")
            .or_else(|| passwd.and_then(|p| field(p.pw_dir)))
            .unwrap_or_default();
        let user = ["LOGNAME", "USER", "LNAME", "USERNAME"]
            .into_iter()
            .find_map(env)
            .or_else(|| passwd.and_then(|p| field(p.pw_name)))
            .unwrap_or_default();
        let mut name = [0u8; 256];
        // SAFETY: the buffer is writable for its whole length.
        let host = if unsafe { libc::gethostname(name.as_mut_ptr().cast(), name.len()) } == 0 {
            let end = name.iter().position(|b| *b == 0).unwrap_or(name.len());
            String::from_utf8_lossy(&name[..end]).into_owned()
        } else {
            String::new()
        };
        Scrubber::new(&home, &user, &host)
    }

    pub fn keeping<'a>(&self, more: impl IntoIterator<Item = &'a Value>) -> Scrubber {
        let mut out = self.clone();
        out.keep.extend(
            more.into_iter()
                .filter_map(as_str)
                .filter(|k| !k.is_empty())
                .map(str::to_owned),
        );
        out
    }

    pub fn keep(mut self, word: &str) -> Scrubber {
        if !word.is_empty() {
            self.keep.insert(word.into());
        }
        self
    }

    fn home(&self, m: &Captures) -> String {
        let base = &m["base"];
        let Some(rest) = m.name("rest").map(|r| r.as_str()) else {
            return if base == "~" || base == self.home {
                "~".into()
            } else {
                "/home/<user>/<private-path>".into()
            };
        };
        if base == self.home || base == "~" {
            if APP_PATHS.iter().any(|p| rest.starts_with(p)) {
                format!("~{rest}")
            } else {
                "~/<private-path>".into()
            }
        } else {
            "/home/<user>/<private-path>".into()
        }
    }

    /// `(?<![A-Za-z0-9+/_-])[A-Za-z0-9+_-]{32,}={0,2}`, by hand: the regex crate has no lookbehind.
    fn base64(&self, text: &str) -> String {
        let bytes = text.as_bytes();
        let mut out = String::with_capacity(text.len());
        let (mut copied, mut pos) = (0, 0);
        while pos < bytes.len() {
            let before_ok = pos == 0 || !(base64_char(bytes[pos - 1]) || bytes[pos - 1] == b'/');
            if !base64_char(bytes[pos]) {
                pos += 1;
                continue;
            }
            let mut end = pos;
            while end < bytes.len() && base64_char(bytes[end]) {
                end += 1;
            }
            if !before_ok || end - pos < 32 {
                pos = end;
                continue;
            }
            let mut stop = end;
            while stop < bytes.len() && stop - end < 2 && bytes[stop] == b'=' {
                stop += 1;
            }
            let word = &text[pos..stop];
            // Needs all three character classes; a mangled C++ name in a backtrace is what a fix needs most.
            let plain = !(word.bytes().any(|b| b.is_ascii_lowercase())
                && word.bytes().any(|b| b.is_ascii_uppercase())
                && word.bytes().any(|b| b.is_ascii_digit()));
            out.push_str(&text[copied..pos]);
            out.push_str(
                if self.keep.contains(word) || word.starts_with("_Z") || plain {
                    word
                } else {
                    "<token>"
                },
            );
            copied = stop;
            pos = stop;
        }
        out.push_str(&text[copied..]);
        out
    }

    pub fn text(&self, value: &str) -> String {
        let text = PEM.replace_all(value, "<private-key>");
        if PRIVATE_LINE.is_match(&text) {
            return REMOVED_LINE.into();
        }
        let text = CONTROL.replace_all(&text, " ");
        let text = URL.replace_all(&text, "<url>");
        let text = BARE_URL.replace_all(&text, "<url>");
        let text = BEARER.replace_all(&text, |m: &Captures| format!("{} <secret>", &m[1]));
        let text = TOKENS.replace_all(&text, "<token>");
        let text = MEDIA.replace_all(&text, "<media-file>");
        // Before NAMED, or `.aws/credentials: denied` loses the word that matters.
        let text = HOME.replace_all(&text, |m: &Captures| self.home(m));
        let text = NAMED.replace_all(&text, |m: &Captures| format!("{}{}<secret>", &m[1], &m[2]));
        let text = self.base64(&text);
        let text = HEX.replace_all(&text, |m: &Captures| {
            if self.keep.contains(&m[0]) {
                m[0].to_owned()
            } else {
                "<token>".into()
            }
        });
        let text = EMAIL.replace_all(&text, "<email>");
        let text = MAC.replace_all(&text, "<mac>");
        let text = IPV4.replace_all(&text, "<ip>");
        let mut text = SERIAL
            .replace_all(&text, |m: &Captures| format!("{}<serial>", &m[1]))
            .into_owned();
        for (word, mask) in &self.masks {
            text = word.replace_all(&text, *mask).into_owned();
        }
        match text.char_indices().nth(LINE_MAX) {
            Some((cut, _)) => format!("{}…", &text[..cut]),
            None => text,
        }
    }

    pub fn tree(&self, value: &Value) -> Value {
        match value {
            Value::String(s) => Value::String(self.text(s)),
            Value::Object(map) => {
                Value::Object(map.iter().map(|(k, v)| (k.clone(), self.tree(v))).collect())
            }
            Value::Array(items) => Value::Array(items.iter().map(|v| self.tree(v)).collect()),
            other => other.clone(),
        }
    }

    pub fn user(&self) -> &str {
        &self.user
    }

    pub fn host(&self) -> &str {
        &self.host
    }
}

fn summary_text(kind: &str) -> Option<&'static str> {
    Some(match kind {
        "hyprland-crash" => "Hyprland crashed or restarted",
        "renderer-crash" => "the XR renderer crashed",
        "control-closed" => "the XR renderer lost its control channel to the supervisor",
        "control-refused" => "the XR renderer refused a control message from the supervisor",
        "supervisor-fault" => "the XR supervisor hit an internal error",
        "usb-drop" => "the glasses' USB connection dropped",
        "video-lost" => "the video link to the glasses was lost",
        "plugin-failed" => "the XR Hyprland plugin failed",
        "watchdog" => "the XR session stopped responding",
        "controller-restart" => "the XR controller restarted and found an unfinished session",
        "run-killed" => "the XR session was killed",
        "run-failed" => "the XR session failed",
        "capture-failed" => "capturing your XR desktops failed",
        "present-stall" => "the picture in the glasses stalled",
        "tracking-lost" => "head tracking was lost",
        "renderer-exit" => "the XR renderer stopped unexpectedly",
        "recovery-failed" => "Recover did not finish",
        "start-failed" => "the XR session could not start",
        _ => return None,
    })
}

/// The line already says what became of the session.
const NO_TAIL: [&str; 4] = [
    "controller-restart",
    "run-killed",
    "recovery-failed",
    "start-failed",
];

/// `summary_line(kind, ended)`; None for a kind Python has no words for (it raises).
pub fn summary_line(kind: &str, ended: Option<&str>) -> Option<String> {
    let line = summary_text(kind)?;
    if NO_TAIL.contains(&kind) {
        return Some(line.into());
    }
    let tail = match ended {
        Some("ended") => " and the session ended",
        Some("stopped") => " and the session was stopped",
        _ => " but the session kept running",
    };
    Some(format!("{line}{tail}"))
}

pub fn notify_text(summary: &str) -> String {
    format!("XR Workspace hit a problem: {summary}. A report was saved.")
}

static FRAME: LazyLock<Regex> = LazyLock::new(|| regex(r"\A#\d+\s+0x[0-9a-f]+\s+(\S+)"));
static GPU_RESET: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)GPU reset|ring \S+ timeout|\bGPU fault\b|amdgpu.*\*ERROR\*"));

fn list(value: &Value) -> &[Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}

fn top_frame(core: &Value) -> Option<String> {
    list(get(core, "frames")).iter().find_map(|frame| {
        let m = FRAME.captures(strip(as_str(frame)?))?;
        (&m[1] != "n/a").then(|| m[1].to_owned())
    })
}

/// What a recovery left and why: the last Recover's own report, else what the run's result says at Stop.
fn recovery_left(ev: &Value) -> (Vec<Value>, Vec<Value>) {
    let (saved, result) = (get(ev, "recovery"), get(ev, "result"));
    let pick = |a: &Value, b: &Value| -> Vec<Value> {
        let value = if truthy(a) { a } else { b };
        list(value).to_vec()
    };
    (
        pick(get(saved, "remaining"), get(result, "remaining")),
        pick(get(saved, "reasons"), get(result, "recovery")),
    )
}

fn recovery_value(ev: &Value) -> Value {
    let (remaining, reasons) = recovery_left(ev);
    json!({"remaining": remaining, "reasons": reasons})
}

fn join(items: &[Value], sep: &str) -> String {
    items.iter().map(py_str).collect::<Vec<_>>().join(sep)
}

/// A first guess from the evidence: what a person would look at first, and how sure it is. Rules in order of weight.
pub fn guess_cause<C>(episode: &Episode<C>, ev: &Value) -> Value {
    let has = |kind: &str| episode.signals.iter().any(|s| s.kind == kind);
    let usb: Vec<&Signal> = episode
        .signals
        .iter()
        .filter(|s| s.kind == "usb-drop")
        .collect();
    let logs = get(ev, "logs");
    let (result, unit) = (get(ev, "result"), get(ev, "unit"));
    let guess = |text: String, confidence: &str| json!({"text": text, "confidence": confidence});

    // Before the core and USB rules: both are symptoms of these, and these say what to do.
    let error = get(result, "error");
    for known in [USB_DENIED, TRACKING_CRASHED] {
        if as_str(error).is_some_and(|e| e.contains(known)) {
            return guess(known.into(), "high");
        }
    }
    if let Some(core) = list(get(ev, "cores")).first() {
        let mut text = format!(
            "{} crashed with {}",
            py_str(get(core, "exe")),
            py_str(get(core, "signal"))
        );
        if let Some(top) = top_frame(core) {
            text += &format!(" in {top}");
        }
        text += ". The backtrace is in core_dumps.";
        if !usb.is_empty() {
            text += " The glasses' USB connection dropped around the same time, which may have set it off.";
        }
        return guess(text, "high");
    }
    if list(get(logs, "kernel"))
        .iter()
        .any(|line| as_str(line).is_some_and(|l| GPU_RESET.is_match(l)))
    {
        return guess(
            "The graphics driver reported a reset or a fault; see the kernel lines.".into(),
            "high",
        );
    }
    if as_str(get(unit, "result")) == Some("oom-kill") {
        return guess(
            "The system ran out of memory and killed the session.".into(),
            "high",
        );
    }
    if has("hyprland-crash") {
        return guess(
            "Hyprland went away with no crash record, so a logout or a restart is likely.".into(),
            "medium",
        );
    }
    if !usb.is_empty() {
        let text = if usb
            .iter()
            .any(|s| s.source == "kernel" || s.source == "usb")
        {
            "USB disconnect confirmed; underlying cause undetermined."
        } else {
            "The driver lost communication; USB disconnect unconfirmed and underlying cause undetermined."
        };
        return guess(text.into(), "low");
    }
    if has("control-closed") {
        return guess(
            "The renderer and the supervisor lost the control channel between them, which ends a session. That is a software fault in \
             XR Workspace: what tracking or the driver log says after that moment follows from it and is not the cause. \
             The run log has the renderer's last words."
                .into(),
            "high",
        );
    }
    if has("video-lost") {
        return guess(
            "The display link to the glasses dropped with no USB trouble: check the cable and the display output.".into(),
            "medium",
        );
    }
    if as_str(get(result, "stop_reason")) == Some("heartbeat-timeout") {
        return guess(
            "The renderer froze for over 2 seconds, which usually means a stuck GPU or compositor."
                .into(),
            "medium",
        );
    }
    let (remaining, reasons) = recovery_left(ev);
    if !reasons.is_empty() {
        let steps = if remaining.is_empty() {
            "none listed".to_owned()
        } else {
            format!("{} left ({})", remaining.len(), join(&remaining, ", "))
        };
        // As lists too: every string of a report stops at LINE_MAX, and no reason may be lost to a long sentence.
        let mut out = guess(
            format!(
                "Recovery could not finish. Journal steps: {steps}. Why: {}",
                join(&reasons, " ")
            ),
            "high",
        );
        out["remaining"] = Value::Array(remaining);
        out["reasons"] = Value::Array(reasons);
        return out;
    }
    if list(get(logs, "run"))
        .iter()
        .any(|line| as_str(line).is_some_and(|l| l.contains("dispatch: closed")))
    {
        return guess("Hyprland closed the renderer's surface or connection, usually because its output went away.".into(), "medium");
    }
    if truthy(error) {
        return guess(format!("The run stopped with: {}", py_str(error)), "medium");
    }
    if has("control-refused") {
        let refused = get(get(get(ev, "metrics"), "control"), "refused");
        return guess(
            format!(
                "The renderer refused {} control message(s) from the supervisor and dropped them. The session carried on, but \
                 that is a bug: the run log says which ones.",
                py_str(refused)
            ),
            "high",
        );
    }
    if let Some(fault) = faults_of(result).first() {
        let last = list(get(fault, "traceback"))
            .last()
            .map_or("no details".to_owned(), py_str);
        return guess(
            format!(
                "The supervisor hit an internal error in {}: {last}. The session carried on; the traceback is under faults.",
                py_str(get(fault, "where"))
            ),
            "high",
        );
    }
    guess(
        "No clear cause: read the timeline and the logs.".into(),
        "low",
    )
}

pub fn serialize(bundle: &Value) -> String {
    dumps(bundle, Some(1))
}

fn chars(line: &Value) -> usize {
    as_str(line).map_or(0, |s| s.chars().count())
}

/// Trim to `cap`: the oldest lines of the biggest log first, as the newest are nearest the incident; then backtraces,
/// then the tail of the timeline. What cannot be cut (the facts) is a few kB.
pub fn fit(mut bundle: Value, cap: usize) -> Value {
    while serialize(&bundle).len() > cap {
        let Some(out) = bundle.as_object_mut() else {
            break;
        };
        let biggest = out
            .get_mut("logs")
            .and_then(Value::as_object_mut)
            .into_iter()
            .flat_map(|logs| logs.values_mut())
            .filter_map(Value::as_array_mut)
            .filter(|section| !section.is_empty())
            .fold(None::<(usize, &mut Vec<Value>)>, |best, section| {
                let size = section.iter().map(chars).sum();
                match best {
                    Some((most, _)) if most >= size => best,
                    _ => Some((size, section)),
                }
            });
        if let Some((_, section)) = biggest {
            let cut = (section.len() / 4).max(1);
            section.drain(..cut);
        } else if let Some(cores) = out
            .get_mut("core_dumps")
            .and_then(Value::as_array_mut)
            .filter(|c| !c.is_empty())
        {
            cores.pop();
        } else if let Some(timeline) = out
            .get_mut("timeline")
            .and_then(Value::as_array_mut)
            .filter(|t| !t.is_empty())
        {
            timeline.pop();
        } else {
            break;
        }
        out.insert("truncated".into(), Value::Bool(true));
    }
    bundle
}

/// A consent's pinned binary, as an object whatever the file held: no odd consent may cost a report.
pub fn pin_of<'a>(consent: &'a Value, key: &str) -> &'a Value {
    let pin = get(consent, key);
    if pin.is_object() { pin } else { &EMPTY }
}

static EMPTY: LazyLock<Value> = LazyLock::new(|| Value::Object(Map::new()));

fn or_empty(value: &Value) -> Value {
    if truthy(value) {
        value.clone()
    } else {
        Value::Object(Map::new())
    }
}

fn observations<C>(episode: &Episode<C>) -> Value {
    let earliest = |at: &mut dyn Iterator<Item = f64>| {
        at.fold(None, |m: Option<f64>, t| Some(m.map_or(t, |m| m.min(t))))
    };
    let usb = earliest(
        &mut episode
            .signals
            .iter()
            .filter(|s| s.kind == "usb-drop" && (s.source == "kernel" || s.source == "usb"))
            .map(|s| s.at),
    );
    let video = earliest(
        &mut episode
            .signals
            .iter()
            .filter(|s| s.kind == "video-lost")
            .map(|s| s.at),
    );
    let order = match (usb, video) {
        (Some(u), Some(v)) if u < v => "usb-first",
        (Some(u), Some(v)) if v < u => "video-first",
        (Some(_), Some(_)) => "same-timestamp",
        _ => "undetermined",
    };
    json!({"usb_disconnect": if usb.is_some() { "confirmed" } else { "unconfirmed" }, "link_order": order})
}

/// `Path(path).parent.name`.
fn parent_name(path: &str) -> String {
    let parts: Vec<&str> = path
        .split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect();
    if parts.len() >= 2 {
        parts[parts.len() - 2].into()
    } else {
        String::new()
    }
}

/// `Path(path).name`.
pub fn file_name(path: &str) -> String {
    path.split('/')
        .rfind(|p| !p.is_empty() && *p != ".")
        .unwrap_or("")
        .into()
}

const LOG_NAMES: [&str; 4] = ["run", "controller", "driver", "kernel"];

/// The report for a closed Episode. `ev` is what the monitor gathered. Every string is scrubbed, then it is fitted.
pub fn build_bundle<C>(episode: &Episode<C>, ev: &Value, now: f64, scrub: &Scrubber) -> Value {
    let consent = or_empty(get(ev, "consent"));
    let result = or_empty(get(ev, "result"));
    let source = or_empty(get(ev, "source"));
    let (renderer, plugin) = (pin_of(&consent, "renderer"), pin_of(&consent, "plugin"));
    let kind = episode.kind().to_owned();
    let rc = get(&result, "renderer_returncode");
    let mut logs = Map::new();
    for name in LOG_NAMES {
        let lines = list(get(get(ev, "logs"), name))
            .iter()
            .filter(|line| !as_str(line).is_some_and(is_exit_report));
        logs.insert(name.into(), Value::Array(lines.cloned().collect()));
    }
    let cores: Vec<Value> = list(get(ev, "cores"))
        .iter()
        .take(MAX_CORES)
        .map(|core| {
            let mut core = core.as_object().cloned().unwrap_or_default();
            let frames: Vec<Value> = list(core.get("frames").unwrap_or(&NULL))
                .iter()
                .take(MAX_FRAMES)
                .cloned()
                .collect();
            core.insert("frames".into(), Value::Array(frames));
            Value::Object(core)
        })
        .collect();
    let mut order: Vec<(&Signal, i64)> = episode
        .signals
        .iter()
        .zip(episode.repeats.iter().copied())
        .collect();
    order.sort_by(|a, b| a.0.at.total_cmp(&b.0.at));
    let timeline: Vec<Value> = order
        .into_iter()
        .map(|(s, n)| {
            let mut entry = json!({"at": iso_utc(s.at), "source": s.source, "kind": s.kind, "detail": s.detail});
            if n != 0 {
                entry["repeats"] = n.into();
            }
            entry
        })
        .collect();
    let mut cause_ev = ev.clone();
    if let Some(map) = cause_ev.as_object_mut() {
        map.insert("logs".into(), Value::Object(logs.clone()));
        map.insert("cores".into(), Value::Array(cores.clone()));
    }
    let tracking = get(&consent, "tracking");
    let renderer_path = get(renderer, "path");
    let id = format!("{}-{kind}", gm_stamp(now));
    let bundle = json!({
        "schema": 1,
        "id": id,
        "kind": kind,
        "summary": summary_line(&kind, episode.ended.as_deref()).unwrap_or_default(),
        "created_at": iso_utc(now),
        "first_signal_at": iso_utc(episode.first),
        "session": episode.ended.as_deref().unwrap_or("kept-running"),
        "session_kept_alive": episode.ended.is_none(),
        "run_id": episode.run_id,
        "cause": guess_cause(episode, &cause_ev),
        "observations": observations(episode),
        "timeline": timeline,
        "versions": {
            "source_sha": get(&source, "sha"),
            "source_dirty": get(&source, "dirty"),
            "bin": if truthy(renderer_path) { Value::String(parent_name(&py_str(renderer_path))) } else { Value::Null },
            "renderer_sha256": get(renderer, "sha256"),
            "plugin_sha256": get(plugin, "sha256"),
        },
        "glasses": {
            "model": get(&consent, "model"),
            "output": get(&consent, "output"),
            "recovery_output": get(&consent, "recovery_output"),
            "tracking": detect::tracked_scope(tracking),
            "tracking_driver": if as_str(tracking) == Some(detect::PRO2_TRACKING) {
                Value::String(detect::PRO2_TRACKING.into())
            } else {
                get(ev, "tracking_driver").clone()
            },
        },
        "display": {"mode": get(ev, "mode"), "expected_mode": get(ev, "expected_mode"), "drm": get(ev, "drm")},
        "renderer": {
            "returncode": rc,
            "signal": as_int(rc).filter(|rc| *rc < 0).map_or(Value::Null, |rc| Value::String(signal_name(-rc))),
            "stop_reason": get(&result, "stop_reason"),
            "complete": get(&result, "complete"),
            "phase": get(&result, "phase"),
            "error": get(&result, "error"),
        },
        "recovery": recovery_value(ev),
        "faults": faults_of(&result),
        "unit": or_empty(get(ev, "unit")),
        "metrics": {"last": or_empty(get(ev, "status")), "exit_report": or_empty(get(ev, "metrics"))},
        "core_dumps": cores,
        "logs": logs,
        "suppressed_since_last": or_empty(get(ev, "suppressed")),
        "truncated": false,
        "privacy": PRIVACY,
    });
    // The id too: a long kind makes it look like a token, and it is the file's name.
    let clean = scrub.keeping([
        &bundle["id"],
        &Value::String(episode.run_id.clone()),
        get(&source, "sha"),
        get(renderer, "sha256"),
        get(plugin, "sha256"),
    ]);
    fit(clean.tree(&bundle), MAX_BUNDLE)
}

/// Correlate by exact run identity, without claiming the earlier incident caused the later end. An Err is where
/// Python raises: a report file without the shape this module writes.
pub fn amend_bundle<C>(
    previous: &Value,
    episode: &Episode<C>,
    ev: &Value,
    now: f64,
    scrub: &Scrubber,
) -> Result<Value, String> {
    let mut merged: Episode<()> = Episode::new(&episode.run_id, episode.first);
    let mut add_counted = |signal: Signal, repeats: i64| {
        let found = merged.matching(&signal);
        let before = merged.signals.len();
        merged.add(signal, None);
        if let Some(i) = found {
            merged.repeats[i] += repeats;
        } else if merged.signals.len() > before
            && let Some(last) = merged.repeats.last_mut()
        {
            *last = repeats;
        }
        // A full bounded timeline did not retain this signal or its repeats.
    };
    let timeline = get(previous, "timeline")
        .as_array()
        .ok_or("a report without a timeline")?;
    for entry in timeline {
        let field = |key: &str| {
            entry
                .get(key)
                .map(py_str)
                .ok_or_else(|| format!("a timeline entry without {key}"))
        };
        let at = iso_timestamp(&field("at")?).ok_or("a timeline entry with a bad time")?;
        let repeats = as_int(get(entry, "repeats")).unwrap_or(0);
        add_counted(
            Signal {
                at,
                kind: field("kind")?,
                source: field("source")?,
                detail: field("detail")?,
            },
            repeats,
        );
    }
    for (signal, repeats) in episode.signals.iter().zip(&episode.repeats) {
        add_counted(signal.clone(), *repeats);
    }
    merged.ended = episode.ended.clone();
    merged.ended_at = episode.ended_at;
    let fresh = build_bundle(&merged, ev, now, scrub);
    let mut out = previous
        .as_object()
        .cloned()
        .ok_or("a report that is not an object")?;
    for key in ["timeline", "cause", "observations", "first_signal_at"] {
        out.insert(key.into(), fresh[key].clone());
    }
    if let Some(ended) = episode.ended.as_deref() {
        for key in [
            "session",
            "session_kept_alive",
            "renderer",
            "unit",
            "recovery",
            "faults",
            "metrics",
        ] {
            out.insert(key.into(), fresh[key].clone());
        }
        let kind = py_str(get(previous, "kind"));
        out.insert(
            "summary".into(),
            summary_line(&kind, Some(ended))
                .ok_or("a report of an unknown kind")?
                .into(),
        );
        out.insert(
            "session_outcome_at".into(),
            iso_utc(episode.ended_at.unwrap_or(now)).into(),
        );
    }
    out.insert("updated_at".into(), iso_utc(now).into());
    let logs = out
        .get_mut("logs")
        .and_then(Value::as_object_mut)
        .ok_or("a report without logs")?;
    for (name, lines) in fresh["logs"].as_object().into_iter().flatten() {
        let old = logs
            .get(name)
            .and_then(Value::as_array)
            .ok_or("a report without a log")?;
        let mut seen = HashSet::new();
        let joined: Vec<Value> = old
            .iter()
            .chain(list(lines))
            .filter(|line| seen.insert(dumps(line, None)))
            .cloned()
            .collect();
        let keep = joined.len().saturating_sub(200);
        logs.insert(name.clone(), Value::Array(joined[keep..].to_vec()));
    }
    Ok(fit(Value::Object(out), MAX_BUNDLE))
}

/// The short version for a person, beside the JSON.
pub fn markdown(bundle: &Value) -> String {
    let at = |path: &[&str]| path.iter().fold(bundle, |value, key| get(value, key));
    let text = |path: &[&str]| py_str(at(path));
    let summary = text(&["summary"]);
    let mut first = summary.chars();
    let summary = first
        .next()
        .map_or(String::new(), |c| c.to_uppercase().chain(first).collect());
    let sha = at(&["versions", "source_sha"]);
    let sha: String = (if truthy(sha) {
        py_str(sha)
    } else {
        "unknown".into()
    })
    .chars()
    .take(12)
    .collect();
    let mut out = vec![
        format!("# XR Workspace incident: {}", text(&["kind"])),
        String::new(),
        format!("{summary}."),
        String::new(),
        format!("- When: {}", text(&["first_signal_at"])),
        format!("- Session: {}", text(&["session"])),
        format!(
            "- Kept alive: {}",
            if truthy(at(&["session_kept_alive"])) {
                "yes"
            } else {
                "no"
            }
        ),
        format!("- Run: {}", text(&["run_id"])),
        format!(
            "- Glasses: {} on {}, {}",
            text(&["glasses", "model"]),
            text(&["glasses", "output"]),
            text(&["display", "mode"])
        ),
        format!("- Build: source {sha}, bin {}", text(&["versions", "bin"])),
        String::new(),
        format!(
            "Likely cause ({} confidence): {}",
            text(&["cause", "confidence"]),
            text(&["cause", "text"])
        ),
        String::new(),
    ];
    if truthy(at(&["cause", "reasons"])) {
        out.extend(["## Why recovery stopped".into(), String::new()]);
        out.extend(
            list(at(&["cause", "remaining"]))
                .iter()
                .map(|step| format!("- step left: {}", py_str(step))),
        );
        out.extend(
            list(at(&["cause", "reasons"]))
                .iter()
                .map(|reason| format!("- {}", py_str(reason))),
        );
        out.push(String::new());
    }
    out.extend(["## What happened".into(), String::new()]);
    let timeline = list(at(&["timeline"]));
    out.extend(timeline.iter().take(12).map(|t| {
        format!(
            "- {} ({}): {}",
            py_str(get(t, "at")),
            py_str(get(t, "source")),
            py_str(get(t, "detail"))
        )
    }));
    if timeline.len() > 12 {
        out.push(format!(
            "- and {} more in the JSON report",
            timeline.len() - 12
        ));
    }
    let faults = list(at(&["faults"]));
    if !faults.is_empty() {
        out.extend([String::new(), "## Supervisor errors".into(), String::new()]);
        for fault in faults {
            let count = get(fault, "count");
            let plural = if py_eq(count, &json!(1)) { "" } else { "s" };
            out.push(format!(
                "- {} ({} time{plural})",
                py_str(get(fault, "where")),
                py_str(count)
            ));
            let traceback = list(get(fault, "traceback"));
            out.extend(
                traceback[traceback.len().saturating_sub(5)..]
                    .iter()
                    .map(|line| format!("  - {}", py_str(line))),
            );
        }
    }
    let text = out.join("\n");
    let bytes = &text.as_bytes()[..text.len().min(MAX_SUMMARY)];
    // `.decode(errors='ignore')` of a cut: only a character split at the end can be broken.
    let valid = match std::str::from_utf8(bytes) {
        Ok(s) => s,
        Err(e) => std::str::from_utf8(&bytes[..e.valid_up_to()]).unwrap_or_default(),
    };
    format!("{valid}\n")
}

fn c_name(name: &str) -> io::Result<CString> {
    CString::new(name).map_err(|_| invalid("unsafe name"))
}

fn check(rc: libc::c_int) -> io::Result<()> {
    if rc < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn random_hex() -> io::Result<String> {
    let mut bytes = [0u8; 8];
    // SAFETY: the buffer is writable for its whole length.
    let got = unsafe { libc::getrandom(bytes.as_mut_ptr().cast(), bytes.len(), 0) };
    if got != bytes.len() as isize {
        return Err(io::Error::last_os_error());
    }
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// `os.mkdir(path, 0o700)`, a folder that is already there being fine; `private_dir` then checks it.
fn make_private_dir(path: &Path) -> io::Result<File> {
    use std::os::unix::fs::DirBuilderExt;
    match std::fs::DirBuilder::new().mode(0o700).create(path) {
        Err(e) if e.kind() != io::ErrorKind::AlreadyExists => return Err(e),
        _ => {}
    }
    private_dir(path)
}

/// The incidents folder: private (0700, files 0600), written atomically, the .md before its .json so that a watcher
/// of .json files always finds both.
#[derive(Clone, Debug)]
pub struct Reports {
    pub directory: PathBuf,
}

impl Reports {
    pub fn new(directory: impl Into<PathBuf>) -> Reports {
        Reports {
            directory: directory.into(),
        }
    }

    fn put(dir: &File, name: &str, data: &[u8]) -> io::Result<()> {
        let fd = dir.as_raw_fd();
        let tmp = c_name(&format!(".{name}.{}.tmp", random_hex()?))?;
        let target = c_name(name)?;
        // SAFETY: `tmp` is NUL-terminated and `fd` is an open directory.
        let out = unsafe {
            libc::openat(
                fd,
                tmp.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        check(out)?;
        // SAFETY: `out` was just opened and is owned by nobody else.
        let mut stream = unsafe { File::from_raw_fd(out) };
        let written = (|| {
            // SAFETY: fchmod on an open descriptor.
            check(unsafe { libc::fchmod(stream.as_raw_fd(), 0o600) })?;
            stream.write_all(data)?;
            stream.sync_all()?;
            // SAFETY: both names are NUL-terminated and `fd` is an open directory.
            check(unsafe { libc::renameat(fd, tmp.as_ptr(), fd, target.as_ptr()) })
        })();
        if written.is_err() {
            // SAFETY: as above; a failure to clean up leaves only a dot file.
            unsafe { libc::unlinkat(fd, tmp.as_ptr(), 0) };
        }
        written
    }

    fn taken(dir: &File, name: &str) -> io::Result<bool> {
        let name = c_name(name)?;
        // SAFETY: struct stat is plain data; fstatat fills it.
        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        // SAFETY: `name` is NUL-terminated, `st` is writable and the descriptor is an open directory.
        if unsafe {
            libc::fstatat(
                dir.as_raw_fd(),
                name.as_ptr(),
                &mut st,
                libc::AT_SYMLINK_NOFOLLOW,
            )
        } == 0
        {
            return Ok(true);
        }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::NotFound {
            Ok(false)
        } else {
            Err(error)
        }
    }

    pub fn write(&self, bundle: &Value, summary: &str) -> io::Result<PathBuf> {
        let dir = make_private_dir(&self.directory)?;
        let id = as_str(get(bundle, "id")).ok_or_else(|| invalid("a report without an id"))?;
        let mut stem = id.to_owned();
        for n in 2.. {
            if !Self::taken(&dir, &format!("{stem}.json"))?
                && !Self::taken(&dir, &format!("{stem}.md"))?
            {
                break;
            }
            stem = format!("{id}-{n}");
        }
        Self::put(&dir, &format!("{stem}.md"), summary.as_bytes())?;
        let mut named = bundle.clone();
        named["id"] = stem.clone().into();
        Self::put(&dir, &format!("{stem}.json"), serialize(&named).as_bytes())?;
        dir.sync_all()?;
        Ok(self.directory.join(format!("{stem}.json")))
    }

    pub fn read(&self, stem: &str) -> io::Result<Value> {
        static STEM: LazyLock<Regex> =
            LazyLock::new(|| regex(r"\A[0-9]{8}T[0-9]{6}Z-[a-z0-9-]+\z"));
        if !STEM.is_match(stem) {
            return Err(invalid("invalid incident id"));
        }
        read_private_json(
            &self.directory.join(format!("{stem}.json")),
            MAX_BUNDLE as u64,
        )
    }

    pub fn update(&self, bundle: &Value) -> io::Result<PathBuf> {
        let stem = as_str(get(bundle, "id")).ok_or_else(|| invalid("invalid incident id"))?;
        self.read(stem)?; // validate the name and refuse missing, unsafe or oversized files
        let dir = private_dir(&self.directory)?;
        Self::put(&dir, &format!("{stem}.md"), markdown(bundle).as_bytes())?;
        Self::put(&dir, &format!("{stem}.json"), serialize(bundle).as_bytes())?;
        dir.sync_all()?;
        Ok(self.directory.join(format!("{stem}.json")))
    }

    pub fn save_state(&self, state: &Value) -> io::Result<()> {
        let dir = make_private_dir(&self.directory)?;
        Self::put(&dir, STATE, dumps(state, None).as_bytes())
    }

    pub fn load_state(&self) -> Map<String, Value> {
        match read_private_json(&self.directory.join(STATE), 1 << 20) {
            Ok(Value::Object(state)) => state,
            _ => Map::new(),
        }
    }
}

/// How often the same trouble may be reported. A session that ends is news even just after a drop it survived, so
/// the two are limited apart. What it holds back is counted, and the next report says how many. The values stay
/// JSON numbers so that the state file reads back as Python wrote it.
#[derive(Clone, Debug, Default)]
pub struct RateLimit {
    last: Map<String, Value>,
    hour: Vec<Value>,
    suppressed: Map<String, Value>,
}

fn number(value: &Value) -> f64 {
    value.as_f64().unwrap_or(0.0)
}

impl RateLimit {
    pub fn allow(&mut self, kind: &str, ended: bool, now: f64) -> bool {
        let key = format!("{kind}|{}", if ended { "ended" } else { "live" });
        self.hour.retain(|t| now - number(t) < 3600.0);
        let wait = if ended { SAME_ENDED_S } else { SAME_LIVE_S };
        if self.last.get(&key).is_some_and(|t| now - number(t) < wait)
            || self.hour.len() >= MAX_PER_HOUR
        {
            let held = as_int(self.suppressed.get(kind).unwrap_or(&NULL)).unwrap_or(0);
            self.suppressed.insert(kind.into(), (held + 1).into());
            return false;
        }
        self.last.insert(key, super::py::float(now));
        self.hour.push(super::py::float(now));
        true
    }

    pub fn take_suppressed(&mut self) -> Value {
        Value::Object(std::mem::take(&mut self.suppressed))
    }

    pub fn save(&self) -> Value {
        json!({"last": self.last, "hour": self.hour, "suppressed": self.suppressed})
    }

    pub fn restore(saved: &Value) -> RateLimit {
        let mut limit = RateLimit::default();
        let plain = |v: &Value| v.is_number();
        if let Some(last) = get(saved, "last")
            .as_object()
            .filter(|m| m.values().all(plain))
        {
            limit.last = last.clone();
        }
        if let Some(hour) = get(saved, "hour")
            .as_array()
            .filter(|h| h.iter().all(plain))
        {
            limit.hour = hour.clone();
        }
        if let Some(held) = get(saved, "suppressed")
            .as_object()
            .filter(|m| m.values().all(|v| as_int(v).is_some()))
        {
            limit.suppressed = held.clone();
        }
        limit
    }
}

pub fn hook_path(config_home: &Path) -> PathBuf {
    config_home.join("viture-workspace").join("on-incident")
}

/// What became of a hook: its exit code (minus the signal that ended it), or why it never ran to the end.
#[derive(Clone, Debug, PartialEq)]
pub enum HookOutcome {
    Code(i32),
    Timeout,
    Refused,
    Failed,
}

impl std::fmt::Display for HookOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            HookOutcome::Code(rc) => write!(f, "{rc}"),
            HookOutcome::Timeout => f.write_str("timeout"),
            HookOutcome::Refused => f.write_str("refused"),
            HookOutcome::Failed => f.write_str("failed"),
        }
    }
}

fn append_log(path: &Path, text: &str) -> io::Result<()> {
    match std::fs::symlink_metadata(path) {
        Ok(meta) if meta.len() > MAX_HOOK_LOG => {
            let mut rotated = path.as_os_str().to_owned();
            rotated.push(".1");
            std::fs::rename(path, PathBuf::from(rotated))?;
        }
        Ok(_) => {}
        Err(e) if e.kind() == io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    let mut out = File::options()
        .append(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    out.write_all(text.as_bytes())
}

fn memfd() -> io::Result<File> {
    // SAFETY: the name is NUL-terminated; the descriptor returned is owned by nobody else.
    let fd = unsafe { libc::memfd_create(c"xr-incident-hook".as_ptr(), libc::MFD_CLOEXEC) };
    check(fd)?;
    // SAFETY: `fd` was just created.
    Ok(unsafe { File::from_raw_fd(fd) })
}

/// Hand one report on: `hook <bundle path>`, with no shell, in a process group of its own so a timeout takes its
/// children too. None when there is no hook. Output goes to the private log, cut short. (Python starts a new
/// session; a process group is what the kill needs, and the hook has no terminal to lose either way.)
pub fn run_hook(
    hook: &Path,
    bundle: &Path,
    log_path: &Path,
    timeout: Duration,
) -> io::Result<Option<HookOutcome>> {
    let hook_name = hook
        .file_name()
        .map_or_else(String::new, |n| n.to_string_lossy().into_owned());
    let record = |rc: HookOutcome, output: &str| -> io::Result<Option<HookOutcome>> {
        let mut output = output.to_owned();
        if !output.is_empty() && !output.ends_with('\n') {
            output.push('\n');
        }
        let name = bundle
            .file_name()
            .map_or_else(String::new, |n| n.to_string_lossy().into_owned());
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |d| d.as_secs_f64());
        append_log(
            log_path,
            &format!("{} {name} rc={rc}\n{output}", iso_utc(now)),
        )?;
        Ok(Some(rc))
    };
    let meta = match std::fs::metadata(hook) {
        Ok(meta) => meta,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return record(HookOutcome::Refused, &format!("{hook_name}: {e}")),
    };
    let path = CString::new(hook.as_os_str().as_bytes()).map_err(|_| invalid("unsafe path"))?;
    // SAFETY: `path` is NUL-terminated.
    let executable = unsafe { libc::access(path.as_ptr(), libc::X_OK) } == 0;
    if !meta.file_type().is_file() || !executable {
        return record(
            HookOutcome::Refused,
            &format!("{hook_name}: not executable"),
        );
    }
    // SAFETY: getuid has no preconditions and cannot fail.
    if (meta.uid() != unsafe { libc::getuid() } && meta.uid() != 0) || meta.mode() & 0o022 != 0 {
        return record(
            HookOutcome::Refused,
            &format!("{hook_name}: others could change it"),
        );
    }
    let mut output = memfd()?;
    let spawned = Command::new(hook)
        .arg(bundle)
        .current_dir(bundle.parent().unwrap_or(Path::new("/")))
        .stdin(Stdio::null())
        .stdout(output.try_clone()?)
        .stderr(output.try_clone()?)
        .process_group(0)
        .spawn();
    let mut child = match spawned {
        Ok(child) => child,
        Err(e) => return record(HookOutcome::Failed, &format!("{hook_name}: {e}")),
    };
    let deadline = Instant::now() + timeout;
    let rc = loop {
        if let Some(status) = child.try_wait()? {
            use std::os::unix::process::ExitStatusExt;
            break HookOutcome::Code(
                status
                    .code()
                    .unwrap_or_else(|| -status.signal().unwrap_or(0)),
            );
        }
        if Instant::now() >= deadline {
            let group = libc::pid_t::try_from(child.id()).map_err(|_| invalid("pid"))?;
            // SAFETY: the group is the hook's own; a group already gone gives ESRCH, which is fine.
            unsafe { libc::killpg(group, libc::SIGKILL) };
            child.wait()?;
            break HookOutcome::Timeout;
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    output.seek(SeekFrom::Start(0))?;
    let mut data = Vec::new();
    output.take(MAX_HOOK_OUTPUT as u64).read_to_end(&mut data)?;
    record(rc, &decode(&data))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testdir::{TestDir, write_script};

    fn scrubber() -> Scrubber {
        Scrubber::new("/home/testuser", "testuser", "testhost")
    }

    #[test]
    fn scrubs_what_python_scrubs() {
        let s = scrubber();
        for (raw, clean) in [
            (
                "open /home/testuser/Videos/holiday.mkv failed",
                "open <media-file> failed",
            ),
            (
                "/home/testuser/.local/state/viture-workspace/x.json",
                "~/.local/state/viture-workspace/x.json",
            ),
            (
                "/home/testuser/secret/notes.txt and ~/a/b",
                "~/<private-path> and ~/<private-path>",
            ),
            ("/home/other/x", "/home/<user>/<private-path>"),
            ("~ alone", "~ alone"),
            ("cd ~", "cd ~"),
            (
                "Authorization: Bearer abcdef123456",
                "Authorization: <secret> <secret>",
            ),
            ("Bearer abcdef123456", "Bearer <secret>"),
            ("/home/testuser", "~"),
            ("/root/x", "/home/<user>/<private-path>"),
            (
                "see https://example.com/a?b and www.example.org",
                "see <url> and <url>",
            ),
            (
                "token=hunter2 password: \"x y\"",
                "token=<secret> password: <secret>",
            ),
            (
                "mail me@example.com from 10.0.0.1 at 00:11:22:aa:bb:cc",
                "mail <email> from <ip> at <mac>",
            ),
            ("serial number: ABC123", "serial number: <serial>"),
            ("testuser on testhost", "<user> on <host>"),
            ("a\tb\x01c", "a b c"),
            ("window title: secret", REMOVED_LINE),
        ] {
            assert_eq!(s.text(raw), clean, "{raw}");
        }
        let token = "xY9".repeat(11);
        assert_eq!(s.text(&format!("k {token}== x")), "k <token> x");
        assert_eq!(s.text(&format!("/{token}")), format!("/{token}"));
        assert_eq!(
            s.keeping([&Value::String(token.clone())]).text(&token),
            token
        );
        assert_eq!(s.text(&format!("_ZN{token}")), format!("_ZN{token}"));
        assert_eq!(s.text(&"xy".repeat(20)), "xy".repeat(20));
        assert_eq!(s.text(&format!("a{token}")), "<token>");
        assert_eq!(s.text(&"deadbeef".repeat(4)), "<token>");
        let long = "x ".repeat(300);
        assert_eq!(s.text(&long).chars().count(), LINE_MAX + 1);
    }

    #[test]
    fn rate_limit_keeps_live_and_ended_apart() {
        let mut limit = RateLimit::default();
        assert!(limit.allow("usb-drop", false, 0.0));
        assert!(!limit.allow("usb-drop", false, 10.0));
        assert!(limit.allow("usb-drop", true, 10.0));
        assert_eq!(limit.take_suppressed(), json!({"usb-drop": 1}));
        let restored = RateLimit::restore(&limit.save());
        assert_eq!(restored.save(), limit.save());
        assert_eq!(
            RateLimit::restore(&json!({"last": {"a": "x"}})).save()["last"],
            json!({})
        );
    }

    #[test]
    fn markdown_cuts_at_a_character() {
        let bundle = json!({"summary": "é".repeat(5000), "timeline": []});
        let text = markdown(&bundle);
        assert!(text.len() <= MAX_SUMMARY + 1 && text.ends_with('\n'));
    }

    fn script(dir: &Path, name: &str, body: &str) -> PathBuf {
        let path = dir.join(name);
        write_script(&path, body);
        path
    }

    #[test]
    fn hook_runs_and_logs() {
        let dir = TestDir::new("incident-hook");
        let bundle = dir.path().join("20261103T235320Z-usb-drop.json");
        std::fs::write(&bundle, "{}").unwrap();
        let log = dir.path().join("hook.log");
        assert_eq!(
            run_hook(&dir.path().join("none"), &bundle, &log, HOOK_TIMEOUT).unwrap(),
            None
        );
        let ok = script(
            dir.path(),
            "ok",
            "#!/bin/sh\necho \"got $1 in $(pwd)\"\nexit 3\n",
        );
        assert_eq!(
            run_hook(&ok, &bundle, &log, HOOK_TIMEOUT).unwrap(),
            Some(HookOutcome::Code(3))
        );
        let slow = script(dir.path(), "slow", "#!/bin/sh\nsleep 30 &\nsleep 30\n");
        let started = Instant::now();
        assert_eq!(
            run_hook(&slow, &bundle, &log, Duration::from_millis(200)).unwrap(),
            Some(HookOutcome::Timeout)
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        let open = script(dir.path(), "open", "#!/bin/sh\n");
        std::fs::set_permissions(&open, std::os::unix::fs::PermissionsExt::from_mode(0o777))
            .unwrap();
        assert_eq!(
            run_hook(&open, &bundle, &log, HOOK_TIMEOUT).unwrap(),
            Some(HookOutcome::Refused)
        );
        let text = std::fs::read_to_string(&log).unwrap();
        let lines: Vec<&str> = text.lines().collect();
        assert!(
            lines[0].ends_with(" 20261103T235320Z-usb-drop.json rc=3"),
            "{text}"
        );
        assert_eq!(
            lines[1],
            format!("got {} in {}", bundle.display(), dir.path().display())
        );
        assert!(lines[2].ends_with("rc=timeout"));
        assert!(
            lines[3].ends_with("rc=refused") && lines[4] == "open: others could change it",
            "{text}"
        );
    }

    #[test]
    fn reports_are_private_and_never_overwritten() {
        use std::os::unix::fs::PermissionsExt;
        let dir = TestDir::new("incident-reports");
        let reports = Reports::new(dir.path().join("incidents"));
        let bundle = json!({"id": "20261103T235320Z-usb-drop", "run_id": "r"});
        let first = reports.write(&bundle, "one\n").unwrap();
        let second = reports.write(&bundle, "two\n").unwrap();
        assert_eq!(
            second.file_name().unwrap(),
            "20261103T235320Z-usb-drop-2.json"
        );
        assert_eq!(
            std::fs::metadata(&first).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            std::fs::metadata(&reports.directory)
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(
            reports.read("20261103T235320Z-usb-drop-2").unwrap()["id"],
            "20261103T235320Z-usb-drop-2"
        );
        assert!(reports.read("../x").is_err());
        reports.save_state(&json!({"schema": 1})).unwrap();
        assert_eq!(reports.load_state().get("schema"), Some(&json!(1)));
    }
}
