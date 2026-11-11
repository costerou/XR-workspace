//! hyprland.py: the supervisor's Hyprland client. Bounded raw IPC, fail-closed normalization, no command shell.
//!
//! A port of `tools/native_spike/hyprland.py` `HyprlandClient`. `tests/hyprland_transcripts.rs` holds it to
//! `rust/corpus/hyprland/transcripts.json`: for every public call that talks to the compositor, the same request bytes
//! in the same order, and the same result or the same refusal.
//!
//! Connections. Hyprland's request socket (`.socket.sock`) answers one request per connection and then closes it, so
//! there is no persistent request connection to keep: every request connects again, and every connect repeats all the
//! checks (no symlink anywhere on the path, our uid, no group or other write, the peer's credentials, and the peer's
//! kernel start time, so a reused PID is refused as "compositor peer changed"). What does persist across requests is the
//! verified peer identity and session id. The persistent connection is the read-only event socket (`.socket2.sock`,
//! [`EventStream`]), opened with the same checks.
//!
//! Allow-lists. Queries are a closed enum ([`Query`]); a mutation string must match one of four fixed shapes
//! ([`Client::mutate`]); every eval is generated Lua from [`crate::lua`], never free-form. Arguments are typed where the
//! Python refuses a wrong type, as in [`crate::lua`]; every value check and its message is the Python's.

use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::thread::sleep;
use std::time::{Duration, Instant};

use xr_wire::json::{self, Value, python_float_repr};

use crate::install;
use crate::lua::{
    self, BindCommand, Refused, adopt, columns, fit_windows, free_pointer, input_lock, keybinds,
    overview_cursor, privacy, resize, restore, sbs, send, unified_cursor, video, view,
    window_memory,
};
use crate::supervisor::Peer;

/// The largest reply read, in bytes.
pub const LIMIT: usize = 1 << 20;
/// The whole-request deadline (connect, send and the whole reply), and the mode read-backs' deadline.
pub const DEADLINE: Duration = Duration::from_secs(2);
/// Read-backs of a monitor change: every READBACK_FAST for the first READBACK_FAST_FOR, then every READBACK. Hyprland
/// applies a rule on a following frame, so the first read after the eval nearly always misses.
pub const READBACK_FAST: Duration = Duration::from_millis(10);
pub const READBACK_FAST_FOR: Duration = Duration::from_millis(250);
pub const READBACK: Duration = Duration::from_millis(50);
/// The longest mutation command, in characters.
pub const MUTATION_LIMIT: usize = 512;
/// Our broker's PLUGIN_INIT name. `plugin list` has no path field, so it is matched by name.
pub const OWNERSHIP_PLUGIN_NAME: &str = "xr-ownership";
const MAX_WORKSPACE: u64 = 2147483647;

const REQUEST_SOCKET: &str = ".socket.sock";
const EVENT_SOCKET: &str = ".socket2.sock";

/// Why a call failed. The text is the Python exception's message, word for word; the variant is its class.
#[derive(Debug)]
pub enum Error {
    /// `ValueError`: a refused value, a refused reply, or the compositor's own refusal.
    Value(String),
    /// `PermissionError`: an unsafe path or peer.
    Permission(&'static str),
    /// `TimeoutError`: a deadline passed.
    Timeout(&'static str),
    /// Any other `OSError`.
    Io(io::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Value(m) => f.write_str(m),
            Error::Permission(m) | Error::Timeout(m) => f.write_str(m),
            Error::Io(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for Error {}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        match e.kind() {
            io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => {
                Error::Timeout("whole-response deadline exceeded")
            }
            _ => Error::Io(e),
        }
    }
}

impl From<Refused> for Error {
    fn from(r: Refused) -> Self {
        Error::Value(r.0.to_string())
    }
}

impl From<json::Error> for Error {
    fn from(e: json::Error) -> Self {
        Error::Value(e.0)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

fn value<T>(message: &str) -> Result<T> {
    Err(Error::Value(message.to_string()))
}

/// The allow-listed queries (hyprland.QUERY, with `getoption`'s two allowed options).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Query {
    Monitors,
    Workspaces,
    Clients,
    ActiveWindow,
    CursorPos,
    /// `getoption input:follow_mouse`.
    FollowMouse,
    /// `getoption general:col.active_border`.
    ActiveBorder,
}

impl Query {
    /// The request as sent.
    pub fn request(self) -> &'static str {
        match self {
            Query::Monitors => "j/monitors all",
            Query::Workspaces => "j/workspaces",
            Query::Clients => "j/clients",
            Query::ActiveWindow => "j/activewindow",
            Query::CursorPos => "j/cursorpos",
            Query::FollowMouse => "j/getoption input:follow_mouse",
            Query::ActiveBorder => "j/getoption general:col.active_border",
        }
    }
}

/// The compositor process behind the socket: its credentials and its kernel start time (field 22 of
/// `/proc/<pid>/stat`), which tells a reused PID apart.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    pub pid: i32,
    pub uid: u32,
    pub gid: u32,
    pub start: String,
}

impl Identity {
    /// Python's `repr()` of the identity tuple, as the session id hashes it.
    fn python_repr(&self) -> String {
        format!(
            "({}, {}, {}, '{}')",
            self.pid, self.uid, self.gid, self.start
        )
    }
}

/// Which pin a plugin path must match.
#[derive(Clone, Copy, Debug)]
pub enum PluginPin<'a> {
    /// The plugin path installed.json pins now (`install.load_installed(data_home)['plugin']['path']`).
    Installed(&'a str),
    /// The plugin a crashed run pinned in its consent: unload only, after the pin is checked against the install
    /// folder under `data_home` and its hash (`install.verify_recovery_pin`). An install may have replaced
    /// installed.json since; unloading that run's own (verified) broker is still allowed.
    Recovery {
        pin: &'a install::Pin,
        data_home: &'a Path,
    },
}

/// A placement phase of [`Client::place_windows`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// The windows onto their workspaces.
    Moves,
    /// Floating, order, fullscreen and focus.
    Settle,
}

/// The approved output's placement in the granted session mode (`view.session_mode`'s dict).
#[derive(Clone, Debug, PartialEq)]
pub struct Mode {
    pub name: String,
    pub x: i64,
    pub y: i64,
    pub scale: f64,
}

impl Mode {
    pub fn lua(&self) -> view::SessionMode<'_> {
        view::SessionMode {
            name: &self.name,
            x: self.x,
            y: self.y,
            scale: self.scale,
        }
    }

    fn python_repr(&self) -> String {
        format!(
            "{{'name': '{}', 'x': {}, 'y': {}, 'scale': {}}}",
            self.name,
            self.x,
            self.y,
            python_float_repr(self.scale)
        )
    }
}

/// One monitor of a `monitors all` reply, after [`monitors`] checked it. `raw` is the reply's own object.
#[derive(Clone, Debug)]
pub struct Monitor {
    pub id: i128,
    pub name: String,
    pub width: i64,
    pub height: i64,
    pub x: i64,
    pub y: i64,
    pub scale: f64,
    pub refresh_rate: f64,
    pub transform: i64,
    pub disabled: bool,
    pub dpms: bool,
    pub mirror_of: String,
    pub raw: Value,
}

/// The monitor list if every entry has the full, unambiguous shape the client relies on, else refused with the first
/// failing check's message. A missing key, a `KeyError` in Python, is refused with that key's check message.
pub fn monitors(raw: &Value) -> Result<Vec<Monitor>> {
    let Value::Array(entries) = raw else {
        return value("invalid monitor response");
    };
    let mut out: Vec<Monitor> = Vec::with_capacity(entries.len());
    for m in entries {
        if !matches!(m, Value::Object(_)) {
            return value("invalid monitor");
        }
        let name = match m.get("name").and_then(Value::as_str) {
            Some(n) if lua::token(n) => n.to_string(),
            _ => return value("invalid identifier"),
        };
        let id = m.get("id").and_then(Value::as_int);
        let id = match id {
            Some(id) if id >= 0 && !out.iter().any(|o| o.name == name || o.id == id) => id,
            _ => return value("ambiguous monitor"),
        };
        let int = |key: &str, low: i128, high: i128, message: &str| -> Result<i64> {
            match m.get(key).and_then(Value::as_int) {
                Some(v) if (low..=high).contains(&v) => Ok(v as i64),
                _ => value(message),
            }
        };
        let width = int("width", 1, 32768, "invalid dimensions")?;
        let height = int("height", 1, 32768, "invalid dimensions")?;
        let x = int("x", -2147483648, 2147483647, "invalid position")?;
        let y = int("y", -2147483648, 2147483647, "invalid position")?;
        let positive = |key: &str| -> Result<f64> {
            match m.get(key).and_then(number) {
                Some(v) if v.is_finite() && v > 0.0 => Ok(v),
                _ => value("invalid mode"),
            }
        };
        let scale = positive("scale")?;
        let refresh_rate = positive("refreshRate")?;
        let transform = int("transform", 0, 7, "invalid transform")?;
        let mirror_of = match m.get("mirrorOf").and_then(Value::as_str) {
            Some(s) if mirror(s) => s.to_string(),
            _ => return value("missing or invalid mirror evidence"),
        };
        let flag = |key: &str| -> Result<bool> {
            match m.get(key) {
                Some(Value::Bool(b)) => Ok(*b),
                _ => value("incomplete monitor state"),
            }
        };
        let disabled = flag("disabled")?;
        let dpms = flag("dpmsStatus")?;
        out.push(Monitor {
            id,
            name,
            width,
            height,
            x,
            y,
            scale,
            refresh_rate,
            transform,
            disabled,
            dpms,
            mirror_of,
            raw: m.clone(),
        });
    }
    Ok(out)
}

/// A JSON number as Python's `float()` of it (an int or a float; never a bool).
fn number(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// `none|0|[1-9][0-9]*`.
fn mirror(s: &str) -> bool {
    s == "none" || s == "0" || nonzero_digits(s)
}

/// `[1-9][0-9]*`.
fn nonzero_digits(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b[0] != b'0' && b.iter().all(u8::is_ascii_digit)
}

/// Python's `str()` of a JSON number: the integer's digits, or the float's `repr`.
fn python_number(v: &Value) -> String {
    match v {
        Value::Number(n) if n.is_f64() => python_float_repr(n.as_f64().unwrap_or(f64::NAN)),
        Value::Number(n) => n.to_string(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => format!("'{s}'"),
        other => format!("{other:?}"),
    }
}

/// A physical connector, `(?:DP|HDMI-A|eDP|DVI-D)-[0-9]+`.
fn connector(s: &str) -> bool {
    lua::connector(s, &["DP", "HDMI-A", "eDP", "DVI-D"], 1, usize::MAX)
}

/// The rendering output's placement below and left of the interactive outputs, outside their row (the monitor with
/// `x` and `y` replaced). Its right edge cannot extend the virtual desktop origin, even when the glasses are wider than
/// the recovery screen. Existing managed outputs are ignored, so reloads and repeated starts give the same placement.
pub fn presentation_placement(raw: &Value, output: &str) -> Result<Monitor> {
    if !lua::token(output) {
        return value("invalid identifier");
    }
    if !connector(output) {
        return value("presentation must be a physical connector");
    }
    let snapshot = monitors(raw)?;
    let usable: Vec<&Monitor> = snapshot
        .iter()
        .filter(|m| !m.disabled && m.dpms && m.mirror_of == "none")
        .collect();
    let matches: Vec<&&Monitor> = usable.iter().filter(|m| m.name == output).collect();
    let others: Vec<&&Monitor> = usable
        .iter()
        .filter(|m| {
            m.name != output && !m.name.starts_with("XR-") && !m.name.starts_with("HEADLESS-")
        })
        .collect();
    if matches.len() != 1 || others.is_empty() {
        return value("presentation requires an independent usable desktop");
    }
    let target = matches[0];
    let logical = |pixels: i64, scale: f64| -> i128 {
        // math.ceil(int / float): a float divide, then an exact integer (saturating far outside any layout).
        ((pixels as f64) / scale).ceil() as i128
    };
    let sideways = |m: &Monitor| m.transform % 2 == 1;
    let width = logical(
        if sideways(target) {
            target.height
        } else {
            target.width
        },
        target.scale,
    );
    let x = others
        .iter()
        .map(|m| m.x as i128)
        .min()
        .unwrap_or(0)
        .saturating_sub(width);
    let y = others
        .iter()
        .map(|m| {
            (m.y as i128).saturating_add(logical(
                if sideways(m) { m.width } else { m.height },
                m.scale,
            ))
        })
        .max()
        .unwrap_or(0)
        .saturating_add(8192);
    let range = -2147483648i128..=2147483647;
    if !range.contains(&x) || !range.contains(&y) {
        return value("presentation layout overflow");
    }
    let mut placed = (*target).clone();
    placed.x = x as i64;
    placed.y = y as i64;
    if let Value::Object(entries) = &mut placed.raw {
        for (k, v) in entries.iter_mut() {
            match k.as_str() {
                "x" => *v = Value::Number(placed.x.into()),
                "y" => *v = Value::Number(placed.y.into()),
                _ => {}
            }
        }
    }
    Ok(placed)
}

/// How many names of a [`Client::plugins_loaded`] list are our broker. Unrelated plugins (Omarchy's hyprbars, say) do
/// not count.
pub fn ownership_plugin_count(names: &[String]) -> usize {
    names.iter().filter(|n| *n == OWNERSHIP_PLUGIN_NAME).count()
}

/// `[0-9a-f]{40}_[0-9]+_[0-9]+`: a full Hyprland instance signature.
pub fn instance_ok(s: &str) -> bool {
    let mut parts = s.split('_');
    let (Some(hash), Some(a), Some(b), None) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return false;
    };
    hash.len() == 40
        && hash.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
        && [a, b]
            .iter()
            .all(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
}

/// True only when Hyprland `instance` has provably exited: its request socket is gone, and its lock is gone or names
/// no running Hyprland. Anything less (a malformed name, an unreadable lock, a process we may not inspect) proves
/// nothing.
pub fn instance_ended(runtime_dir: &Path, instance: &str) -> bool {
    if !instance_ok(instance) {
        return false;
    }
    let directory = runtime_dir.join("hypr").join(instance);
    if fs::symlink_metadata(directory.join(REQUEST_SOCKET)).is_ok() {
        return false;
    }
    let text = match fs::read_to_string(directory.join("hyprland.lock")) {
        Ok(t) => t,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return true,
        Err(_) => return false,
    };
    let Some(pid) = python_lines(&text)
        .first()
        .and_then(|l| l.trim().parse::<i64>().ok())
    else {
        return false;
    };
    match fs::read_link(format!("/proc/{pid}/exe")) {
        Ok(exe) => exe.file_name().is_none_or(|n| n != "Hyprland"),
        Err(e) if e.kind() == io::ErrorKind::NotFound => true,
        Err(_) => false,
    }
}

/// Python's `str.splitlines()` for ASCII text.
fn python_lines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let bytes = text.as_bytes();
    let (mut start, mut i) = (0, 0);
    while i < bytes.len() {
        match bytes[i] {
            b'\r' if bytes.get(i + 1) == Some(&b'\n') => {
                out.push(&text[start..i]);
                i += 2;
                start = i;
            }
            b'\n' | b'\r' | 0x0b | 0x0c | 0x1c | 0x1d | 0x1e => {
                out.push(&text[start..i]);
                i += 1;
                start = i;
            }
            _ => i += 1,
        }
    }
    if start < bytes.len() {
        out.push(&text[start..]);
    }
    out
}

/// Python's `str.strip()`.
fn python_strip(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c))
}

/// The compositor's refusal as a diagnostic: its first 160 characters, printable ones only (`str.isprintable`, for
/// the characters a reply can plausibly hold).
fn detail(reply: &str) -> String {
    reply
        .chars()
        .take(160)
        .filter(|&c| {
            if c.is_ascii() {
                (' '..='~').contains(&c)
            } else {
                !(c.is_whitespace()
                    || c.is_control()
                    || matches!(c, '\u{ad}' | '\u{200b}'..='\u{200f}' | '\u{2028}'..='\u{202e}'
                        | '\u{2060}'..='\u{2064}' | '\u{feff}' | '\u{e000}'..='\u{f8ff}'))
            }
        })
        .collect()
}

fn uid() -> u32 {
    // SAFETY: getuid has no preconditions and cannot fail.
    unsafe { libc::getuid() }
}

/// `path` opened as a directory, never through a symlink.
fn open_directory(path: &Path) -> io::Result<File> {
    fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
}

/// A stream socket connected to `name` inside the already open `directory`, through `/proc/self/fd`, so no path
/// component is looked up again. `retry`: a full accept queue (EAGAIN; nothing was sent) is retried every 10 ms until
/// `deadline`, as the request socket does; otherwise it fails at once.
fn connect_at(
    directory: BorrowedFd<'_>,
    name: &str,
    deadline: Instant,
    retry: bool,
) -> Result<UnixStream> {
    let stream = connect_to(&socket_address(directory, name)?, deadline, retry)?;
    stream.set_nonblocking(false)?;
    Ok(stream)
}

/// The address of `name` inside the open `directory`, through `/proc/self/fd`.
fn socket_address(directory: BorrowedFd<'_>, name: &str) -> Result<libc::sockaddr_un> {
    let path = format!("/proc/self/fd/{}/{name}", directory.as_raw_fd());
    // SAFETY: sockaddr_un is plain old data; all zeroes is a valid value.
    let mut addr: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    addr.sun_family = libc::AF_UNIX as libc::sa_family_t;
    if path.len() >= addr.sun_path.len() {
        return Err(Error::Io(io::Error::from(io::ErrorKind::InvalidInput)));
    }
    for (dst, src) in addr.sun_path.iter_mut().zip(path.bytes()) {
        *dst = src as libc::c_char;
    }
    Ok(addr)
}

/// A non-blocking stream socket connected to `addr` (see [`connect_at`] for `retry`).
fn connect_to(addr: &libc::sockaddr_un, deadline: Instant, retry: bool) -> Result<UnixStream> {
    // SAFETY: plain socket(2); the result is checked and owned at once.
    let raw: RawFd = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if raw < 0 {
        return Err(io::Error::last_os_error().into());
    }
    // SAFETY: `raw` is a fresh descriptor nothing else owns.
    let socket = unsafe { OwnedFd::from_raw_fd(raw) };
    let timeout = || Error::Timeout("Hyprland connect deadline exceeded");
    loop {
        if Instant::now() >= deadline {
            return Err(timeout());
        }
        // SAFETY: `addr` is a valid sockaddr_un of the size given; `socket` is open.
        let rc = unsafe {
            libc::connect(
                socket.as_raw_fd(),
                (addr as *const libc::sockaddr_un).cast(),
                std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t,
            )
        };
        if rc == 0 {
            break;
        }
        let e = io::Error::last_os_error();
        match e.raw_os_error() {
            Some(libc::EINTR) => continue,
            Some(libc::EAGAIN) if retry => {
                let now = Instant::now();
                if now >= deadline {
                    return Err(timeout());
                }
                sleep((deadline - now).min(Duration::from_millis(10)));
            }
            _ => return Err(Error::Io(e)),
        }
    }
    if Instant::now() >= deadline {
        return Err(timeout());
    }
    Ok(UnixStream::from(socket))
}

/// Field 22 of `/proc/<pid>/stat`: the process's start time in clock ticks since boot.
fn start_time(pid: i32) -> Result<String> {
    let stat = fs::read(format!("/proc/{pid}/stat"))?;
    let after = stat
        .iter()
        .rposition(|&b| b == b')')
        .map(|i| &stat[i + 1..])
        .ok_or_else(|| Error::Io(io::Error::from(io::ErrorKind::InvalidData)))?;
    let field = after
        .split(|b| b.is_ascii_whitespace())
        .filter(|f| !f.is_empty())
        .nth(19)
        .ok_or_else(|| Error::Io(io::Error::from(io::ErrorKind::InvalidData)))?;
    Ok(String::from_utf8_lossy(field).into_owned())
}

/// The read-only Hyprland event stream (socket2): one persistent, non-blocking connection, opened with the request
/// socket's checks. Lines are split as the run's view splits them: a buffer over 64 KiB keeps only its last 4 KiB and
/// resyncs on the next newline.
pub struct EventStream {
    stream: UnixStream,
    buffer: Vec<u8>,
}

impl EventStream {
    /// The complete lines that arrived with one read: empty when nothing was ready, `None` once Hyprland closed the
    /// stream.
    pub fn read_lines(&mut self) -> io::Result<Option<Vec<Vec<u8>>>> {
        let mut chunk = vec![0u8; 65536];
        let n = match self.stream.read(&mut chunk) {
            Ok(0) => return Ok(None),
            Ok(n) => n,
            Err(e)
                if matches!(
                    e.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                ) =>
            {
                return Ok(Some(Vec::new()));
            }
            Err(e) => return Err(e),
        };
        Ok(Some(self.push(&chunk[..n])))
    }

    fn push(&mut self, data: &[u8]) -> Vec<Vec<u8>> {
        self.buffer.extend_from_slice(data);
        if self.buffer.len() > 65536 {
            self.buffer.drain(..self.buffer.len() - 4096);
        }
        let Some(last) = self.buffer.iter().rposition(|&b| b == b'\n') else {
            return Vec::new();
        };
        let rest = self.buffer.split_off(last + 1);
        let done = std::mem::replace(&mut self.buffer, rest);
        done[..done.len() - 1]
            .split(|&b| b == b'\n')
            .map(<[u8]>::to_vec)
            .collect()
    }
}

impl AsFd for EventStream {
    fn as_fd(&self) -> BorrowedFd<'_> {
        self.stream.as_fd()
    }
}

/// The Hyprland client of one compositor instance.
pub struct Client {
    instance: String,
    runtime_dir: PathBuf,
    directory: PathBuf,
    peer: Option<Identity>,
    session: Option<String>,
    wayland_name: Option<String>,
}

impl Client {
    /// The client of Hyprland `instance` (its full signature) under the absolute `runtime_dir`. Nothing is opened yet.
    pub fn new(instance: &str, runtime_dir: &Path) -> Result<Client> {
        if !instance_ok(instance) {
            return value("explicit full Hyprland instance signature required");
        }
        if !runtime_dir.is_absolute() {
            return value("absolute runtime directory required");
        }
        Ok(Client {
            instance: instance.to_string(),
            runtime_dir: runtime_dir.to_path_buf(),
            directory: runtime_dir.join("hypr").join(instance),
            peer: None,
            session: None,
            wayland_name: None,
        })
    }

    pub fn instance(&self) -> &str {
        &self.instance
    }

    /// A client already bound to `identity` and `session`, as a run's guard worker gets the run's client: any other
    /// compositor process behind the socket is refused ("compositor peer changed", "session changed").
    pub fn pinned(instance: &str, runtime_dir: &Path, identity: Identity, session: &str) -> Result<Client> {
        let mut client = Client::new(instance, runtime_dir)?;
        client.peer = Some(identity);
        client.session = Some(session.to_string());
        Ok(client)
    }

    /// The compositor identity every request so far was checked against.
    pub fn identity(&self) -> Option<&Identity> {
        self.peer.as_ref()
    }

    /// The verified compositor's credentials, for the renderer launch's Wayland peer check.
    pub fn peer(&self) -> Option<Peer> {
        self.peer.as_ref().map(|p| Peer {
            pid: p.pid,
            uid: p.uid,
            gid: p.gid,
        })
    }

    /// No symlink traversal for the selected endpoint or its parents: each is a directory of ours that no group or
    /// other may write.
    fn check_directories(&self) -> Result<()> {
        let uid = uid();
        for path in [
            self.runtime_dir.clone(),
            self.runtime_dir.join("hypr"),
            self.directory.clone(),
        ] {
            let st = fs::symlink_metadata(&path).map_err(Error::Io)?;
            if !st.file_type().is_dir() || st.uid() != uid || st.mode() & 0o022 != 0 {
                return Err(Error::Permission("unsafe Hyprland socket directory"));
            }
        }
        Ok(())
    }

    fn connect(&mut self, deadline: Instant, name: &str) -> Result<UnixStream> {
        self.check_directories()?;
        let uid = uid();
        if name != REQUEST_SOCKET && name != EVENT_SOCKET {
            return value("unknown Hyprland socket");
        }
        let st = fs::symlink_metadata(self.directory.join(name)).map_err(Error::Io)?;
        if !st.file_type().is_socket() || st.uid() != uid {
            return Err(Error::Permission("unsafe IPC socket"));
        }
        let directory = open_directory(&self.directory).map_err(Error::Io)?;
        let stream = connect_at(directory.as_fd(), name, deadline, true)?;
        drop(directory);
        let cred = Peer::of(&stream).map_err(Error::Io)?;
        if cred.uid != uid || cred.pid <= 0 {
            return Err(Error::Permission("wrong compositor peer"));
        }
        // The kernel's process start tick also protects against PID reuse.
        let identity = Identity {
            pid: cred.pid,
            uid: cred.uid,
            gid: cred.gid,
            start: start_time(cred.pid)?,
        };
        if self.peer.as_ref().is_some_and(|p| *p != identity) {
            return value("compositor peer changed");
        }
        self.peer = Some(identity);
        Ok(stream)
    }

    /// One request on a fresh, checked connection; the whole reply, within `timeout` and LIMIT.
    fn request(&mut self, request: &str, timeout: Duration) -> Result<Vec<u8>> {
        let deadline = Instant::now() + timeout;
        let mut conn = self.connect(deadline, REQUEST_SOCKET)?;
        if !request.is_ascii() {
            return value("request is not ASCII");
        }
        let left = || deadline.saturating_duration_since(Instant::now());
        conn.set_write_timeout(Some(left().max(Duration::from_millis(1))))?;
        conn.write_all(request.as_bytes())?;
        let mut result = Vec::new();
        let mut buf = vec![0u8; 65536];
        loop {
            let remaining = left();
            if remaining.is_zero() {
                return Err(Error::Timeout("whole-response deadline exceeded"));
            }
            conn.set_read_timeout(Some(remaining))?;
            let want = buf.len().min(LIMIT + 1 - result.len());
            let n = match conn.read(&mut buf[..want]) {
                Ok(n) => n,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e.into()),
            };
            if n == 0 {
                return Ok(result);
            }
            result.extend_from_slice(&buf[..n]);
            if result.len() > LIMIT {
                return value("Hyprland response exceeds 1 MiB");
            }
        }
    }

    /// One ownership plugin request (`ownership._call`'s `transport._request`): `wire` is the `xrspike` command; the raw
    /// reply, within `timeout` and LIMIT.
    pub fn broker_request(&mut self, wire: &str, timeout: Duration) -> Result<Vec<u8>> {
        self.request(&format!("j/{wire}"), timeout)
    }

    /// The reply as text, invalid UTF-8 replaced, stripped.
    fn request_text(&mut self, request: &str) -> Result<String> {
        let reply = self.request(request, DEADLINE)?;
        Ok(python_strip(&String::from_utf8_lossy(&reply)).to_string())
    }

    /// The read-only event stream (socket2), with the same peer checks; the caller reads and closes it.
    pub fn event_stream(&mut self) -> Result<EventStream> {
        let stream = self.connect(Instant::now() + DEADLINE, EVENT_SOCKET)?;
        stream.set_nonblocking(true)?;
        Ok(EventStream {
            stream,
            buffer: Vec::new(),
        })
    }

    /// An allow-listed query, its JSON reply parsed strictly. `timeout`: at most DEADLINE.
    pub fn query(&mut self, kind: Query, timeout: Duration) -> Result<Value> {
        if timeout.is_zero() || timeout > DEADLINE {
            return value("invalid timeout");
        }
        let reply = self.request(kind.request(), timeout)?;
        Ok(json::parse_strict(&reply)?)
    }

    /// A [`Lane`] to the compositor every request so far was checked against: the socket directories checked again,
    /// the directory opened, and a pidfd of that process taken (its start time read again after, so the pidfd is of
    /// that process and not of one that reused its PID). Refused before any request verified a peer.
    pub fn lane(&self) -> Result<Lane> {
        let Some(identity) = self.peer.clone() else {
            return value("no verified compositor peer yet");
        };
        self.check_directories()?;
        let directory: OwnedFd = open_directory(&self.directory).map_err(Error::Io)?.into();
        let addr = socket_address(directory.as_fd(), REQUEST_SOCKET)?;
        let pid = rustix::process::Pid::from_raw(identity.pid)
            .ok_or(Error::Permission("wrong compositor peer"))?;
        let pidfd = rustix::process::pidfd_open(pid, rustix::process::PidfdFlags::empty())
            .map_err(|e| Error::Io(e.into()))?;
        if start_time(identity.pid)? != identity.start {
            return value("compositor peer changed");
        }
        Ok(Lane {
            directory,
            addr,
            identity,
            pidfd,
            reply: Vec::new(),
            chunk: vec![0u8; 4096].into_boxed_slice(),
        })
    }

    /// An allow-listed mutation (internal API only: never reached from the public control wire); Hyprland's reply.
    pub fn mutate(&mut self, command: &str) -> Result<String> {
        check_mutation(command)?;
        let reply = self.request(&format!("/{command}"), DEADLINE)?;
        let text =
            String::from_utf8(reply).map_err(|_| Error::Value("invalid UTF-8 reply".into()))?;
        Ok(python_strip(&text).to_string())
    }

    fn eval(&mut self, code: &str, limit: usize) -> Result<()> {
        if code.chars().count() > limit {
            return value("bind code too large");
        }
        let reply = self.request_text(&format!("/eval {code}"))?;
        if reply != "ok" {
            return Err(Error::Value(format!(
                "compositor rejected bind change: {}",
                detail(&reply)
            )));
        }
        Ok(())
    }

    fn eval4k(&mut self, code: &str) -> Result<()> {
        self.eval(code, lua::EVAL_LIMIT)
    }

    /// Runtime-only Ctrl+Alt binds for the glasses view. `binds` / `send_binds`: the key bind panel's keys, else
    /// view.BINDS / send.SEND_BINDS. If the send or drag binds fail, the binds are removed again (the run will not
    /// start: no half-installed binds left behind) and the first failure is returned.
    pub fn install_view_binds(
        &mut self,
        cmd: &BindCommand,
        binds: Option<&[(&str, &str)]>,
        send_binds: Option<&[(&str, &str)]>,
    ) -> Result<()> {
        self.eval4k(&view::install_binds_lua(cmd, false, binds)?)?;
        let rest = (|| {
            self.eval4k(&view::install_send_binds_lua(cmd, send_binds)?)?;
            self.eval4k(&view::install_drag_binds_lua(cmd)?)
        })();
        if rest.is_err() {
            let _ = self.eval4k(&view::remove_binds_lua());
        }
        rest
    }

    /// After a config reload, a typing-mode switch or a key bind panel change: drop any surviving handles, then bind
    /// again (never doubled). `typing`: only the Ctrl+Alt+T toggle, and no send binds. The Super drag binds stay in both.
    pub fn reinstall_view_binds(
        &mut self,
        cmd: &BindCommand,
        typing: bool,
        binds: Option<&[(&str, &str)]>,
        send_binds: Option<&[(&str, &str)]>,
    ) -> Result<()> {
        let install = view::install_binds_lua(cmd, typing, binds)?;
        self.eval4k(&format!("{} {install}", view::remove_binds_lua()))?;
        if !typing {
            self.eval4k(&view::install_send_binds_lua(cmd, send_binds)?)?;
        }
        self.eval4k(&view::install_drag_binds_lua(cmd)?)
    }

    pub fn remove_view_binds(&mut self) -> Result<()> {
        self.eval4k(&view::remove_binds_lua())
    }

    /// A Super drag's drop: the window onto a workspace and, floating, to layout pixel (x, y).
    pub fn land_window(
        &mut self,
        address: &str,
        workspace: i64,
        x: Option<i64>,
        y: Option<i64>,
    ) -> Result<()> {
        self.eval4k(&send::land_lua(address, workspace, x, y)?)
    }

    /// Run start: the physical monitor's windows and workspaces onto the XR desktops.
    pub fn adopt_windows(
        &mut self,
        workspaces: &[(i64, &str)],
        windows: &[(&str, &str)],
    ) -> Result<()> {
        for code in adopt::lua(workspaces, windows)? {
            self.eval4k(&code)?;
        }
        Ok(())
    }

    /// The remembered windows back on their desktops, in one phase.
    pub fn place_windows(
        &mut self,
        placement: &window_memory::Placement,
        phase: Phase,
    ) -> Result<()> {
        let codes = match phase {
            Phase::Moves => window_memory::moves_lua(placement)?,
            Phase::Settle => window_memory::settle_lua(placement)?,
        };
        for code in codes {
            self.eval4k(&code)?;
        }
        Ok(())
    }

    /// A desktop shrank: its floating windows that would stick out back inside it.
    pub fn fit_windows(&mut self, moves: &[fit_windows::Move]) -> Result<()> {
        for code in fit_windows::lua(moves)? {
            self.eval4k(&code)?;
        }
        Ok(())
    }

    /// Stop / Recover: the adopted windows back to their pre-XR layout, in one eval.
    pub fn restore_layout(&mut self, snapshot: &restore::Snapshot) -> Result<()> {
        self.eval(&restore::lua(snapshot)?, restore::EVAL_LIMIT)
    }

    pub fn unpin_xr_windows(&mut self) -> Result<()> {
        self.eval4k(restore::UNPIN_LUA)
    }

    /// Every bind, as plain `hyprctl binds` prints it; read only. `timeout`: at most DEADLINE.
    pub fn binds_text(&mut self, timeout: Duration) -> Result<String> {
        if timeout.is_zero() || timeout > DEADLINE {
            return value("invalid timeout");
        }
        Ok(String::from_utf8_lossy(&self.request("binds", timeout)?).into_owned())
    }

    pub fn install_options_binds(&mut self, cmd: &BindCommand) -> Result<()> {
        self.eval4k(&view::install_options_binds_lua(cmd)?)
    }

    pub fn open_key_panel(&mut self, cmd: &BindCommand) -> Result<()> {
        self.eval4k(&keybinds::panel_on_lua(cmd)?)
    }

    pub fn close_key_panel(&mut self) -> Result<()> {
        self.eval4k(keybinds::PANEL_OFF_LUA)
    }

    /// Privacy mode: physical output `name` DPMS off (`keep`: the glasses' output, kept on in the same eval) or back on.
    pub fn set_dpms(&mut self, name: &str, on: bool, keep: Option<&str>) -> Result<()> {
        if on {
            return self.eval4k(&privacy::wake_lua(name)?);
        }
        let Some(keep) = keep else {
            return value("blanking needs the glasses output");
        };
        self.eval4k(&privacy::blank_lua(name, keep)?)
    }

    /// The file Omarchy's monitor scale writes to (monitor_rules.rs), from this process's environment.
    pub fn monitors_config(&self) -> Option<PathBuf> {
        crate::monitor_rules::config_path(
            std::env::var_os("XDG_CONFIG_HOME").as_deref(),
            std::env::var_os("HOME").as_deref(),
        )
    }

    /// Hyprland reads its config again; returns once it has.
    pub fn reload_config(&mut self) -> Result<()> {
        let reply = self.request_text("/reload")?;
        if reply != "ok" {
            return Err(Error::Value(format!(
                "config reload rejected: {}",
                detail(&reply)
            )));
        }
        Ok(())
    }

    /// The loaded plugins' names.
    pub fn plugins_loaded(&mut self) -> Result<Vec<String>> {
        let raw = json::parse_strict(&self.request("j/plugin list", DEADLINE)?)?;
        let names = match &raw {
            Value::Array(entries) => entries
                .iter()
                .map(|p| match p {
                    Value::Object(_) => p.get("name").and_then(Value::as_str).map(str::to_string),
                    _ => None,
                })
                .collect::<Option<Vec<_>>>(),
            _ => None,
        };
        names.ok_or_else(|| Error::Value("invalid plugin list response".into()))
    }

    pub fn plugin_load(&mut self, path: &str, pin: PluginPin) -> Result<()> {
        self.plugin_command("load", path, pin)
    }

    pub fn plugin_unload(&mut self, path: &str, pin: PluginPin) -> Result<()> {
        self.plugin_command("unload", path, pin)
    }

    fn plugin_command(&mut self, verb: &str, path: &str, pin: PluginPin) -> Result<()> {
        const PINNED: &str = "plugin path must be the pinned install";
        if !plugin_path(path) {
            return value(PINNED);
        }
        match pin {
            PluginPin::Recovery { pin, data_home } => {
                if verb != "unload" {
                    return value("recovery pin only permits unload");
                }
                verify_recovery_pin(pin, data_home)?;
                if Path::new(path) != pin.path {
                    return value(PINNED);
                }
            }
            PluginPin::Installed(installed) => {
                if path != installed {
                    return value(PINNED);
                }
            }
        }
        let reply = self.request_text(&format!("/plugin {verb} {path}"))?;
        if reply != "ok" {
            return Err(Error::Value(format!(
                "plugin {verb} rejected: {}",
                detail(&reply)
            )));
        }
        Ok(())
    }

    /// `output`, read now, refused with `missing` unless exactly one monitor has that name.
    fn current(&mut self, output: &str, timeout: Duration, missing: &str) -> Result<Monitor> {
        let raw = self.query(Query::Monitors, timeout)?;
        let mut found: Vec<Monitor> = monitors(&raw)?
            .into_iter()
            .filter(|m| m.name == output)
            .collect();
        if found.len() != 1 {
            return value(missing);
        }
        Ok(found.remove(0))
    }

    /// Session-only 1920x1200@120 on the approved output at its current placement; verified by readback.
    pub fn apply_session_mode(&mut self, output: &str) -> Result<Mode> {
        const MISSING: &str = "session mode output not found";
        let m = self.current(output, DEADLINE, MISSING)?;
        let mode = Mode {
            name: m.name.clone(),
            x: m.x,
            y: m.y,
            scale: m.scale,
        };
        self.eval4k(&view::restore_mode_lua(&mode.lua())?)?;
        // The eval only schedules the rule; Hyprland applies it on a following frame. Its acknowledgement is not a
        // mode-application barrier.
        let started = Instant::now();
        let deadline = started + DEADLINE;
        let mut last: Option<Monitor> = None;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break;
            }
            match self.current(output, remaining, MISSING) {
                Ok(m) => last = Some(m),
                Err(Error::Timeout(_)) => break,
                Err(e) => return Err(e),
            }
            if last.as_ref().and_then(session_mode).as_ref() == Some(&mode)
                && Instant::now() <= deadline
            {
                return Ok(mode);
            }
            if !pause(started, deadline) {
                break;
            }
        }
        let observed = match &last {
            None => "None".to_string(),
            Some(m) => {
                let fields: Vec<String> =
                    ["name", "width", "height", "refreshRate", "x", "y", "scale"]
                        .iter()
                        .map(|k| {
                            format!(
                                "'{k}': {}",
                                m.raw.get(k).map_or("None".into(), python_number)
                            )
                        })
                        .collect();
                format!("{{{}}}", fields.join(", "))
            }
        };
        let (w, h, r) = view::SESSION_MODE;
        Err(Error::Value(format!(
            "session mode ({w}, {h}, {r}) at {} not observed within 2s; last readback: {observed}",
            mode.python_repr()
        )))
    }

    /// The glasses' side-by-side mode at `rate`, on the output at its current placement; verified by readback. Asked
    /// for only after the glasses themselves offer it. `model`: `luma-ultra` unless the glasses are a Pro 2.
    pub fn apply_wide_mode(&mut self, output: &str, rate: f64, model: &str) -> Result<Value> {
        let (wide, _) = sbs::mode(model)?;
        let m = self.current(output, DEADLINE, "wide mode output not found")?;
        let rule = sbs::wide_mode_lua(&m.name, m.x, m.y, m.scale, rate, model)?;
        if model == "pro2" && best_wide(&m, model)? != Some(rate) {
            return value("Pro 2 side-by-side mode is not offered");
        }
        self.apply_mode_rule(output, &rule, wide, rate)
    }

    /// Restore an observed Pro 2 1080p refresh once the glasses offer it again (back in 2D), also from the stale wide
    /// mode a wide rule leaves Hyprland driving.
    pub fn apply_pro2_mode(&mut self, output: &str, rate: f64) -> Result<Value> {
        let m = self.current(output, DEADLINE, "Pro 2 mode output not found")?;
        let rule = sbs::pro2_mode_lua(&m.name, m.x, m.y, m.scale, rate)?;
        let offered_now = offered(&m)?
            .iter()
            .any(|&(w, h, hz)| (w, h) == (1920, 1080) && (hz - rate).abs() < 0.5);
        if pro2_2d_rate(&m, None)?.is_none() || !offered_now {
            return value("Pro 2 2D mode is not offered on the current narrow display");
        }
        self.apply_mode_rule(output, &rule, (1920, 1080), rate)
    }

    /// Apply an already validated display rule and wait for the requested dimensions and refresh.
    fn apply_mode_rule(
        &mut self,
        output: &str,
        rule: &str,
        dimensions: (u32, u32),
        rate: f64,
    ) -> Result<Value> {
        self.eval4k(rule)?;
        let started = Instant::now();
        let deadline = started + DEADLINE;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break;
            }
            let last = match self.current(output, remaining, "display mode output not found") {
                Ok(m) => m,
                Err(Error::Timeout(_)) => break,
                Err(e) => return Err(e),
            };
            if (last.width, last.height) == (i64::from(dimensions.0), i64::from(dimensions.1))
                && (last.refresh_rate - rate).abs() < 0.5
            {
                return Ok(last.raw);
            }
            if !pause(started, deadline) {
                break;
            }
        }
        Err(Error::Value(format!(
            "{}x{}@{} not observed within 2s",
            dimensions.0,
            dimensions.1,
            lua::py_g(rate)
        )))
    }

    /// Keep the rendering output away from the interactive row, preserving its mode. Runtime-only, deliberately kept
    /// after stop, like the session mode; no personal monitor config is written.
    pub fn park_presentation(&mut self, output: &str) -> Result<Value> {
        let raw = self.query(Query::Monitors, DEADLINE)?;
        let desired = presentation_placement(&raw, output)?;
        let (w, h, hz) = (desired.width, desired.height, desired.refresh_rate);
        let scale = desired
            .raw
            .get("scale")
            .map_or_else(String::new, python_number);
        self.eval4k(&format!(
            "hl.monitor({{ output = \"{}\", mode = \"{w}x{h}@{hz:.6}\", position = \"{}x{}\", scale = {scale}, transform = {} }})",
            desired.name, desired.x, desired.y, desired.transform
        ))?;
        let started = Instant::now();
        let deadline = started + DEADLINE;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break;
            }
            let snapshot = monitors(&self.query(Query::Monitors, remaining)?)?;
            if let Some(found) = snapshot.into_iter().find(|m| m.name == output) {
                let same = found.id == desired.id
                    && (found.width, found.height, found.x, found.y)
                        == (w, h, desired.x, desired.y)
                    && found.scale == desired.scale
                    && found.transform == desired.transform
                    && (found.disabled, found.dpms) == (desired.disabled, desired.dpms)
                    && found.mirror_of == desired.mirror_of;
                if same && (found.refresh_rate - hz).abs() < 0.01 && Instant::now() <= deadline {
                    return Ok(found.raw);
                }
            }
            pause(started, deadline);
        }
        value("presentation placement was not observed before deadline")
    }

    pub fn restore_session_mode(&mut self, mode: &view::SessionMode) -> Result<()> {
        self.eval4k(&view::restore_mode_lua(mode)?)
    }

    pub fn install_video_binds(&mut self, cmd: &BindCommand) -> Result<()> {
        self.eval4k(&video::install_video_binds_lua(cmd)?)
    }

    pub fn remove_video_binds(&mut self) -> Result<()> {
        self.eval4k(&video::remove_video_binds_lua())
    }

    pub fn install_menu_binds(&mut self, cmd: &BindCommand) -> Result<()> {
        self.eval4k(&view::install_menu_binds_lua(cmd)?)
    }

    pub fn remove_menu_binds(&mut self) -> Result<()> {
        self.eval4k(&view::remove_menu_binds_lua())
    }

    pub fn install_resize_binds(&mut self, cmd: &BindCommand) -> Result<()> {
        self.eval4k(&resize::install_resize_binds_lua(cmd)?)
    }

    pub fn remove_resize_binds(&mut self) -> Result<()> {
        self.eval4k(&resize::remove_resize_binds_lua())
    }

    pub fn grab_pointer(&mut self, cmd: &BindCommand, x: i64, y: i64) -> Result<()> {
        self.eval4k(&free_pointer::grab_pointer_lua(cmd, x, y)?)
    }

    pub fn release_pointer(&mut self, x: Option<i64>, y: Option<i64>) -> Result<()> {
        self.eval4k(&free_pointer::release_pointer_lua(x, y)?)
    }

    pub fn warp_cursor(&mut self, x: i64, y: i64) -> Result<()> {
        self.eval4k(&view::warp_cursor_lua(x, y)?)
    }

    pub fn hide_overview_cursor(&mut self) -> Result<()> {
        self.eval4k(overview_cursor::HIDE_LUA)
    }

    pub fn show_overview_cursor(&mut self) -> Result<()> {
        self.eval4k(overview_cursor::SHOW_LUA)
    }

    pub fn lock_pointer(&mut self, cmd: &BindCommand, x: i64, y: i64) -> Result<()> {
        self.eval4k(&input_lock::lock_pointer_lua(cmd, x, y)?)
    }

    pub fn enter_desktop(
        &mut self,
        cmd: &BindCommand,
        x: Option<i64>,
        y: Option<i64>,
        monitor: Option<&str>,
    ) -> Result<()> {
        self.eval4k(&unified_cursor::enter_desktop_lua(cmd, x, y, monitor)?)
    }

    pub fn arm_click_binds(&mut self, cmd: &BindCommand) -> Result<()> {
        self.eval4k(&unified_cursor::click_binds_lua(cmd)?)
    }

    pub fn confine_cursor(&mut self, x: i64, y: i64, name: &str) -> Result<()> {
        self.eval4k(&unified_cursor::confine_lua(x, y, name)?)
    }

    pub fn scroll_to_window(&mut self, address: &str) -> Result<()> {
        self.eval4k(&columns::scroll_lua(address)?)
    }

    /// The session id: the socket's actual peer (SO_PEERCRED and start time, from a request), which must be the
    /// selected instance's Hyprland and match its lock file, hashed with the boot id. A different id than last time is
    /// refused.
    pub fn verified_session(&mut self) -> Result<String> {
        // Any allow-listed query establishes the peer (checked on connect); the reply is not used, so the smallest.
        self.query(Query::CursorPos, DEADLINE)?;
        let peer = self.peer.clone().expect("a request sets the peer");
        let exe = fs::read_link(format!("/proc/{}/exe", peer.pid)).map_err(Error::Io)?;
        if exe.file_name().is_none_or(|n| n != "Hyprland") {
            return value("socket peer is not the selected Hyprland process");
        }
        let mut lock = fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(self.directory.join("hyprland.lock"))
            .map_err(Error::Io)?;
        let st = lock.metadata().map_err(Error::Io)?;
        if !st.file_type().is_file() || st.uid() != uid() || st.mode() & 0o022 != 0 {
            return Err(Error::Permission("unsafe compositor lock"));
        }
        let mut raw = Vec::new();
        (&mut lock)
            .take(1024)
            .read_to_end(&mut raw)
            .map_err(Error::Io)?;
        let text = String::from_utf8(raw)
            .ok()
            .filter(|t| t.is_ascii())
            .ok_or_else(|| Error::Value("lock/peer mismatch".into()))?;
        let fields = python_lines(&text);
        let wayland_ok = fields.get(1).is_some_and(|f| {
            f.strip_prefix("wayland-")
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        });
        if fields.len() != 2 || fields[0] != peer.pid.to_string() || !wayland_ok {
            return value("lock/peer mismatch");
        }
        let boot = fs::read_to_string("/proc/sys/kernel/random/boot_id").map_err(Error::Io)?;
        let identity = install::sha_hex(
            format!(
                "{}:{}:{}",
                python_strip(&boot),
                self.instance,
                peer.python_repr()
            )
            .as_bytes(),
        );
        if self.session.as_ref().is_some_and(|s| *s != identity) {
            return value("session changed");
        }
        self.session = Some(identity.clone());
        self.wayland_name = Some(fields[1].to_string());
        Ok(identity)
    }

    /// An owned connection to the verified lock's Wayland endpoint (never the ambient display), from the same process
    /// as the IPC peer.
    pub fn connect_wayland(&mut self, session: &str) -> Result<UnixStream> {
        if self.verified_session()? != session {
            return value("Wayland session mismatch");
        }
        let name = self
            .wayland_name
            .clone()
            .expect("a verified session sets the name");
        let st = fs::symlink_metadata(self.runtime_dir.join(&name)).map_err(Error::Io)?;
        if !st.file_type().is_socket() || st.uid() != uid() {
            return Err(Error::Permission("unsafe Wayland socket"));
        }
        let directory = open_directory(&self.runtime_dir).map_err(Error::Io)?;
        let conn = connect_at(directory.as_fd(), &name, Instant::now() + DEADLINE, false)?;
        drop(directory);
        let cred = Peer::of(&conn).map_err(Error::Io)?;
        if cred.uid != uid() || Some(cred) != self.peer() {
            return Err(Error::Permission("Wayland/IPC peer mismatch"));
        }
        Ok(conn)
    }

    /// Always refused: Hyprland's socket2 cannot prove an output's incarnation, so stock IPC never creates or removes
    /// managed outputs.
    pub fn require_output_ownership(&self) -> Result<()> {
        value(
            "Hyprland socket2 has no creation UUID, transaction acknowledgement, snapshot/event barrier or replay \
             cursor; independent incarnation continuity cannot be proven. No outputs were created or removed.",
        )
    }
}

/// The request socket of one verified compositor, for a caller that queries it many times a second (the overview
/// pointer's cursor samples). Hyprland answers one request per connection, so each request still connects anew; what
/// [`Client::request`] repeats per request and this does once is the path walk with its three directory checks, the
/// directory open, and the read of `/proc/<pid>/stat`. Per request: the socket is checked (`fstatat` in the open
/// directory, never following a symlink: a socket of ours), the peer's credentials must be the kept identity's, and
/// the kept pidfd must not be readable (the process has not exited, so its PID was not reused). Any error leaves the
/// lane unusable for the caller's purposes: drop it and take a new one from [`Client::lane`] after a full request.
pub struct Lane {
    directory: OwnedFd,
    addr: libc::sockaddr_un,
    identity: Identity,
    pidfd: OwnedFd,
    reply: Vec<u8>,
    chunk: Box<[u8]>,
}

impl Lane {
    /// The compositor this lane talks to.
    pub fn identity(&self) -> &Identity {
        &self.identity
    }

    /// [`Client::query`] on this lane: the same request bytes, the same strict parse, the same limits.
    pub fn query(&mut self, kind: Query, timeout: Duration) -> Result<Value> {
        if timeout.is_zero() || timeout > DEADLINE {
            return value("invalid timeout");
        }
        let deadline = Instant::now() + timeout;
        self.check_socket()?;
        let conn = connect_to(&self.addr, deadline, true)?;
        let cred = Peer::of(&conn).map_err(Error::Io)?;
        let id = &self.identity;
        if (cred.pid, cred.uid, cred.gid) != (id.pid, id.uid, id.gid) || self.exited()? {
            return value("compositor peer changed");
        }
        self.exchange(&conn, kind.request().as_bytes(), deadline)?;
        Ok(json::parse_strict(&self.reply)?)
    }

    fn check_socket(&self) -> Result<()> {
        let st = rustix::fs::statat(
            &self.directory,
            REQUEST_SOCKET,
            rustix::fs::AtFlags::SYMLINK_NOFOLLOW,
        )
        .map_err(|e| Error::Io(e.into()))?;
        if rustix::fs::FileType::from_raw_mode(st.st_mode) != rustix::fs::FileType::Socket
            || st.st_uid != uid()
        {
            return Err(Error::Permission("unsafe IPC socket"));
        }
        Ok(())
    }

    /// The kept compositor has exited (its pidfd is readable).
    fn exited(&self) -> Result<bool> {
        Ok(wait_fd(self.pidfd.as_fd(), libc::POLLIN, 0)?)
    }

    /// `request` written and the whole reply read into `self.reply`, within `deadline` and LIMIT.
    fn exchange(&mut self, conn: &UnixStream, request: &[u8], deadline: Instant) -> Result<()> {
        let left = || deadline.saturating_duration_since(Instant::now());
        let mut sent = 0;
        while sent < request.len() {
            match (&*conn).write(&request[sent..]) {
                Ok(n) => sent += n,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    if !wait_fd(conn.as_fd(), libc::POLLOUT, poll_ms(left()))? {
                        return Err(Error::Timeout("whole-response deadline exceeded"));
                    }
                }
                Err(e) => return Err(e.into()),
            }
        }
        self.reply.clear();
        loop {
            let want = self.chunk.len().min(LIMIT + 1 - self.reply.len());
            match (&*conn).read(&mut self.chunk[..want]) {
                Ok(0) => return Ok(()),
                Ok(n) => {
                    self.reply.extend_from_slice(&self.chunk[..n]);
                    if self.reply.len() > LIMIT {
                        return value("Hyprland response exceeds 1 MiB");
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    let remaining = left();
                    if remaining.is_zero()
                        || !wait_fd(conn.as_fd(), libc::POLLIN, poll_ms(remaining))?
                    {
                        return Err(Error::Timeout("whole-response deadline exceeded"));
                    }
                }
                Err(e) => return Err(e.into()),
            }
        }
    }
}

/// Milliseconds for poll(2), rounded up so a wait never ends before `d`.
fn poll_ms(d: Duration) -> i32 {
    d.as_micros().div_ceil(1000).min(i32::MAX as u128) as i32
}

/// `fd` became ready for `events` (or hung up) within `ms`.
fn wait_fd(fd: BorrowedFd<'_>, events: libc::c_short, ms: i32) -> io::Result<bool> {
    let mut p = libc::pollfd {
        fd: fd.as_raw_fd(),
        events,
        revents: 0,
    };
    loop {
        // SAFETY: one valid pollfd for the length given.
        let rc = unsafe { libc::poll(&mut p, 1, ms) };
        if rc >= 0 {
            return Ok(rc > 0);
        }
        let e = io::Error::last_os_error();
        if e.kind() != io::ErrorKind::Interrupted {
            return Err(e);
        }
    }
}

/// Sleep one read-back step, never past `deadline`; false once it has passed.
fn pause(started: Instant, deadline: Instant) -> bool {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return false;
    }
    let step = if started.elapsed() < READBACK_FAST_FOR {
        READBACK_FAST
    } else {
        READBACK
    };
    sleep(step.min(remaining));
    true
}

/// `view.session_mode`: the output's placement if it is in the granted session mode on a physical connector.
pub fn session_mode(m: &Monitor) -> Option<Mode> {
    let (w, h, r) = view::SESSION_MODE;
    let physical = lua::connector(&m.name, &["DP", "HDMI-A", "eDP"], 1, usize::MAX);
    (physical
        && (m.width, m.height) == (i64::from(w), i64::from(h))
        && (m.refresh_rate - f64::from(r)).abs() <= 0.5
        && (0.25..=4.0).contains(&m.scale))
    .then(|| Mode {
        name: m.name.clone(),
        x: m.x,
        y: m.y,
        scale: m.scale,
    })
}

/// `sbs.offered`: every (width, height, hz) Hyprland lists for the monitor. An entry it does not parse is not a mode;
/// a rate `float()` cannot read is refused as Python refuses it.
fn offered(m: &Monitor) -> Result<Vec<(u64, u64, f64)>> {
    let mut out = Vec::new();
    let Some(Value::Array(modes)) = m.raw.get("availableModes") else {
        return Ok(out);
    };
    for text in modes.iter().filter_map(Value::as_str) {
        let Some(body) = text.strip_suffix("Hz") else {
            continue;
        };
        let Some((size, hz)) = body.split_once('@') else {
            continue;
        };
        let Some((w, h)) = size.split_once('x') else {
            continue;
        };
        let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
        if !digits(w)
            || !digits(h)
            || hz.is_empty()
            || !hz.bytes().all(|b| b.is_ascii_digit() || b == b'.')
        {
            continue;
        }
        let hz: f64 = hz
            .parse()
            .map_err(|_| Error::Value(format!("could not convert string to float: '{hz}'")))?;
        let int = |s: &str| s.parse::<u64>().unwrap_or(u64::MAX);
        out.push((int(w), int(h), hz));
    }
    Ok(out)
}

/// `sbs.best_wide`: the rate to ask for, or None while the glasses do not offer the model's side-by-side mode.
pub fn best_wide(m: &Monitor, model: &str) -> Result<Option<f64>> {
    let Ok(((w, h), rates)) = sbs::mode(model) else {
        return Ok(None);
    };
    let modes = offered(m)?;
    Ok(rates.iter().copied().find(|rate| {
        modes.iter().any(|&(mw, mh, hz)| {
            (mw, mh) == (u64::from(w), u64::from(h)) && (hz - rate).abs() < 0.5
        })
    }))
}

/// `sbs.pro2_2d_rate`: the 1920x1080 rate a Pro 2 back in 2D is to be driven at, or None while it is not back.
fn pro2_2d_rate(m: &Monitor, prefer: Option<f64>) -> Result<Option<f64>> {
    if best_wide(m, "pro2")?.is_some() {
        return Ok(None);
    }
    let rates: Vec<f64> = offered(m)?
        .into_iter()
        .filter(|&(w, h, _)| (w, h) == (1920, 1080))
        .map(|(_, _, hz)| hz)
        .collect();
    let allowed: Vec<f64> = [120.0, 90.0, 60.0]
        .into_iter()
        .filter(|hz: &f64| rates.iter().any(|r| (r - hz).abs() < 0.5))
        .collect();
    if let Some(p) = prefer
        && allowed.iter().any(|hz| (p - hz).abs() < 0.5)
    {
        return Ok(Some(p));
    }
    // sbs.narrow_rate(m, 'pro2'): the observed, supported 2D refresh.
    let current = ((m.width, m.height) == (1920, 1080)
        && [60.0, 90.0, 120.0]
            .iter()
            .any(|hz: &f64| (m.refresh_rate - hz).abs() < 0.5))
    .then_some(m.refresh_rate);
    Ok(current.or(allowed.first().copied()))
}

/// `(?:/[A-Za-z0-9_.-]+)+/viture-workspace/bin/[0-9a-f]{12}/libxr-ownership\.so`.
fn plugin_path(path: &str) -> bool {
    let Some(rest) = path.strip_suffix(&format!("/{}", install::PLUGIN)) else {
        return false;
    };
    let Some((rest, hash)) = rest.rsplit_once('/') else {
        return false;
    };
    let Some(prefix) = rest.strip_suffix("/viture-workspace/bin") else {
        return false;
    };
    hash.len() == 12
        && hash.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
        && prefix.strip_prefix('/').is_some_and(|p| {
            p.split('/').all(|seg| {
                !seg.is_empty()
                    && seg
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-'))
            })
        })
}

/// `install.verify_recovery_pin`: the pin must name a file under our install bin folder that still hash-matches.
fn verify_recovery_pin(pin: &install::Pin, data_home: &Path) -> Result<()> {
    let hex = pin.sha256.len() == 64
        && pin
            .sha256
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'));
    if !hex {
        return value("malformed installed.json");
    }
    let bin = install::root(data_home).join("bin");
    let normal = pin.path.components().collect::<PathBuf>() == pin.path
        && !pin.path.components().any(|c| {
            matches!(
                c,
                std::path::Component::ParentDir | std::path::Component::CurDir
            )
        });
    let text = pin.path.to_str().unwrap_or("");
    if !pin.path.is_absolute()
        || !normal
        || text.ends_with('/')
        || !pin.path.starts_with(&bin)
        || pin.path == bin
    {
        return value("pinned path outside install directory");
    }
    if !install::verify_pinned(pin) {
        return value("pinned binary changed");
    }
    Ok(())
}

/// The internal mutation allow-list: follow_mouse, a workspace onto a monitor, the glasses view's focus on a managed
/// output, and one window by address onto one regular workspace. Never exposed through the public control wire; the
/// managed output lifecycle is broker-addressed only, and the legacy `keyword monitor` is not a fallback.
pub fn check_mutation(command: &str) -> Result<()> {
    if command.chars().count() > MUTATION_LIMIT {
        return value("invalid mutation");
    }
    let follow_mouse = command
        .strip_prefix("keyword input:follow_mouse ")
        .is_some_and(|v| matches!(v, "0" | "1" | "2" | "3"));
    let move_workspace = command
        .strip_prefix("dispatch moveworkspacetomonitor ")
        .and_then(|r| r.split_once(' '))
        .is_some_and(|(n, name)| nonzero_digits(n) && lua::token(name));
    let focus = command
        .strip_prefix("dispatch hl.dsp.focus({monitor=\"")
        .and_then(|r| r.strip_suffix("\"})"))
        .is_some_and(lua::managed);
    let window = window_move_workspace(command);
    if !(follow_mouse || move_workspace || focus || window.is_some()) {
        return value("mutation not allowlisted");
    }
    for word in command.split([' ', ',']) {
        if word.starts_with("XR-") && !word.strip_prefix("XR-").is_some_and(nonzero_digits) {
            return value("invalid managed output name");
        }
    }
    let over = |n: &str| n.len() > 10 || n.parse::<u64>().map_or(true, |v| v > MAX_WORKSPACE);
    if command.starts_with("dispatch moveworkspacetomonitor")
        && command.split_whitespace().nth(2).is_some_and(over)
    {
        return value("invalid workspace");
    }
    if command.starts_with("dispatch hl.dsp.window.move") && window.is_some_and(over) {
        return value("invalid workspace");
    }
    Ok(())
}

/// send.MOVE_PATTERN's workspace, if `command` matches it.
fn window_move_workspace(command: &str) -> Option<&str> {
    let rest = command.strip_prefix("dispatch hl.dsp.window.move({ workspace = \"")?;
    let (workspace, rest) = rest.split_once('"')?;
    if !nonzero_digits(workspace) || workspace.len() > 10 {
        return None;
    }
    let rest = rest.strip_prefix(", follow = ")?;
    let rest = rest
        .strip_prefix("true")
        .or_else(|| rest.strip_prefix("false"))?;
    let addr = rest
        .strip_prefix(", window = \"address:")?
        .strip_suffix("\" })")?;
    lua::address(addr).then_some(workspace)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mutation_allow_list() {
        for ok in [
            "keyword input:follow_mouse 0",
            "keyword input:follow_mouse 3",
            "dispatch moveworkspacetomonitor 4 XR-2",
            "dispatch moveworkspacetomonitor 2147483647 DP-1",
            "dispatch hl.dsp.focus({monitor=\"XR-4\"})",
            "dispatch hl.dsp.window.move({ workspace = \"7\", follow = true, window = \"address:0x55d0c0de0001\" })",
            "dispatch hl.dsp.window.move({ workspace = \"2147483647\", follow = false, window = \"address:0x1\" })",
        ] {
            assert!(check_mutation(ok).is_ok(), "{ok}");
        }
        for (bad, message) in [
            ("keyword input:follow_mouse 4", "mutation not allowlisted"),
            ("keyword monitor DP-1,disable", "mutation not allowlisted"),
            ("dispatch exec foot", "mutation not allowlisted"),
            (
                "dispatch moveworkspacetomonitor 0 DP-1",
                "mutation not allowlisted",
            ),
            (
                "dispatch moveworkspacetomonitor 4 XR-0",
                "invalid managed output name",
            ),
            (
                "dispatch moveworkspacetomonitor 4 XR-SPIKE-1",
                "invalid managed output name",
            ),
            (
                "dispatch moveworkspacetomonitor 2147483648 DP-1",
                "invalid workspace",
            ),
            (
                "dispatch moveworkspacetomonitor 99999999999999999999999 DP-1",
                "invalid workspace",
            ),
            (
                "dispatch hl.dsp.focus({monitor=\"XR-5\"})",
                "mutation not allowlisted",
            ),
            (
                "dispatch hl.dsp.window.move({ workspace = \"2147483648\", follow = true, window = \"address:0x1\" })",
                "invalid workspace",
            ),
            (
                "dispatch hl.dsp.window.move({ workspace = \"12345678901\", follow = true, window = \"address:0x1\" })",
                "mutation not allowlisted",
            ),
            (
                "dispatch hl.dsp.window.move({ workspace = \"7\", follow = true, window = \"address:0xG\" })",
                "mutation not allowlisted",
            ),
        ] {
            match check_mutation(bad) {
                Err(Error::Value(m)) => assert_eq!(m, message, "{bad}"),
                other => panic!("{bad}: {other:?}"),
            }
        }
        let long = format!("dispatch moveworkspacetomonitor 4 {}", "a".repeat(600));
        assert!(matches!(check_mutation(&long), Err(Error::Value(m)) if m == "invalid mutation"));
    }

    #[test]
    fn plugin_paths() {
        assert!(plugin_path(
            "/home/user/.local/share/viture-workspace/bin/0123456789ab/libxr-ownership.so"
        ));
        for bad in [
            "/viture-workspace/bin/0123456789ab/libxr-ownership.so",
            "/home/user/viture-workspace/bin/0123456789AB/libxr-ownership.so",
            "/home/user/viture-workspace/bin/0123456789a/libxr-ownership.so",
            "/home//viture-workspace/bin/0123456789ab/libxr-ownership.so",
            "/home/a b/viture-workspace/bin/0123456789ab/libxr-ownership.so",
            "home/viture-workspace/bin/0123456789ab/libxr-ownership.so",
            "/home/viture-workspace/bin/0123456789ab/libxr-other.so",
        ] {
            assert!(!plugin_path(bad), "{bad}");
        }
    }

    #[test]
    fn instances() {
        let good = "0123456789abcdef0123456789abcdef01234567_1700000000_1";
        assert!(instance_ok(good));
        assert!(!instance_ok(&good.replace('a', "A")));
        assert!(!instance_ok(&format!("{good}_2")));
        assert!(!instance_ok("0123456789abcdef0123456789abcdef01234567__1"));
        assert!(matches!(
            Client::new(good, Path::new("relative")),
            Err(Error::Value(m)) if m == "absolute runtime directory required"
        ));
        assert!(matches!(
            Client::new("../x", Path::new("/run")),
            Err(Error::Value(m)) if m == "explicit full Hyprland instance signature required"
        ));
    }

    #[test]
    fn helpers() {
        assert_eq!(python_lines("1\nwayland-1\n"), ["1", "wayland-1"]);
        assert_eq!(python_lines("1\r\nwayland-1"), ["1", "wayland-1"]);
        assert_eq!(python_lines("1\n\nx"), ["1", "", "x"]);
        assert_eq!(python_strip("\x1c ok\n"), "ok");
        assert_eq!(detail("error:\tbad\x07 bind\u{200b}"), "error:bad bind");
        assert_eq!(detail(&"x".repeat(200)).len(), 160);
    }

    #[test]
    fn event_lines() {
        let (a, _b) = UnixStream::pair().unwrap();
        let mut events = EventStream {
            stream: a,
            buffer: Vec::new(),
        };
        assert!(events.push(b"configrel").is_empty());
        assert_eq!(
            events.push(b"oaded>>\nwindow>>x\npart"),
            [b"configreloaded>>".to_vec(), b"window>>x".to_vec()]
        );
        assert_eq!(events.buffer, b"part");
        events.push(&vec![b'z'; 70000]);
        assert_eq!(events.buffer.len(), 4096);
    }

    fn monitor(over: &[(&str, Value)]) -> Value {
        let n = |v: i64| Value::Number(v.into());
        let f = |v: f64| Value::Number(serde_json::Number::from_f64(v).unwrap());
        let mut entries = vec![
            ("id".to_string(), n(0)),
            ("name".to_string(), Value::String("DP-2".into())),
            ("width".to_string(), n(1920)),
            ("height".to_string(), n(1200)),
            ("refreshRate".to_string(), f(120.0)),
            ("x".to_string(), n(0)),
            ("y".to_string(), n(0)),
            ("scale".to_string(), f(1.0)),
            ("transform".to_string(), n(0)),
            ("disabled".to_string(), Value::Bool(false)),
            ("dpmsStatus".to_string(), Value::Bool(true)),
            ("mirrorOf".to_string(), Value::String("none".into())),
        ];
        for (k, v) in over {
            match entries.iter_mut().find(|(key, _)| key == k) {
                Some(e) => e.1 = v.clone(),
                None => entries.push((k.to_string(), v.clone())),
            }
        }
        entries.retain(|(_, v)| *v != Value::Null);
        Value::Object(entries)
    }

    #[test]
    fn monitor_checks() {
        let one = |over: &[(&str, Value)]| monitors(&Value::Array(vec![monitor(over)])).map(|_| ());
        let refused = |over: &[(&str, Value)], message: &str| match one(over) {
            Err(Error::Value(m)) => assert_eq!(m, message, "{over:?}"),
            other => panic!("{over:?}: {other:?}"),
        };
        assert!(one(&[]).is_ok());
        refused(
            &[("name", Value::String("a b".into()))],
            "invalid identifier",
        );
        refused(&[("id", Value::Number((-1).into()))], "ambiguous monitor");
        refused(&[("id", Value::Bool(true))], "ambiguous monitor");
        refused(&[("width", Value::Number(0.into()))], "invalid dimensions");
        refused(
            &[("height", Value::Number(32769.into()))],
            "invalid dimensions",
        );
        refused(
            &[("x", Value::Number(2147483648i64.into()))],
            "invalid position",
        );
        refused(&[("scale", Value::Number(0.into()))], "invalid mode");
        refused(&[("refreshRate", Value::Null)], "invalid mode");
        refused(
            &[("transform", Value::Number(8.into()))],
            "invalid transform",
        );
        refused(
            &[("mirrorOf", Value::String("01".into()))],
            "missing or invalid mirror evidence",
        );
        refused(
            &[("dpmsStatus", Value::Number(1.into()))],
            "incomplete monitor state",
        );
        let twice = Value::Array(vec![
            monitor(&[]),
            monitor(&[("id", Value::Number(1.into()))]),
        ]);
        assert!(matches!(monitors(&twice), Err(Error::Value(m)) if m == "ambiguous monitor"));
        assert!(
            matches!(monitors(&Value::Null), Err(Error::Value(m)) if m == "invalid monitor response")
        );
    }
}
