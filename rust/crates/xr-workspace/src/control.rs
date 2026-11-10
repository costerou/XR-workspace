//! The bind helper: sends one view operation to a running managed capture run.
//!
//! A port of `tools/native_spike/xr_control.py`, frame for frame: the same command line, the same control frames, the
//! same fixed Lua to Hyprland when no run is listening, the same tool command lines (zenity, wl-paste, notify-send)
//! and the same exit codes. `tests/control_parity.rs` runs both helpers side by side and compares all of that.
//!
//! ```text
//! xr-control CONTROL_SOCKET RUN_ID SESSION OP
//! xr-control CONTROL_SOCKET RUN_ID SESSION binds_key MODS CODE
//! xr-control CONTROL_SOCKET RUN_ID SESSION options_scroll DELTA
//! ```
//!
//! Exit status: 0 sent (or nothing to send), 1 no run listening, 2 a bad command line.
//!
//! One knowing difference: where Python's `str.isdigit` and `int` also take non-ASCII digits (`"٣"`, `"²"`), this
//! helper takes ASCII digits only, so such an argument is a bad command line (2). Hyprland's binds and the key bind
//! panel only ever pass ASCII decimal numbers.

use std::ffi::{OsStr, OsString};
use std::io::{self, Read};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::lua::{keybinds, overview_cursor, privacy, resize, video, view};

/// Legacy, cleanup only: the menu "mouse mode" of builds before the glass pills. The run no longer accepts them; they
/// stay so a bind left behind by an older run that died in that mode finds no run listening and removes itself.
pub const LEGACY_OPS: &[&str] = &["menu_mouse", "menu_mouse_press", "menu_mouse_release"];
/// `xr_control.OPS` without its legacy tail (view.USER_OPS + MENU_OPS + resize.RESIZE_OPS + view.SUPER_OPS +
/// send.SEND_OPS + view.TYPING_OP + HELP_PAGE_OPS + privacy.PRIVACY_OP + view.PLAYBACK_MENU_OP + view.BINDS_PANEL_OP +
/// environment.TOGGLE_OP + immersive.TOGGLE_OP).
pub const VIEW_OPS: &[&str] = &[
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
    "environment_toggle",
    "immersive_toggle",
];
/// VR video ops (video.VIDEO_OPS).
pub const VIDEO_OPS: &[&str] = &[
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
/// Handled here: video_paste becomes video_open with the clipboard's web video; resize_cancel is resize.CANCEL_OP.
pub const HELPER_OPS: &[&str] = &["video_paste", "resize_cancel"];
/// The key bind panel's key subscription: `binds_key MODS CODE`, Hyprland's modmask and the xkb keycode.
pub const KEY_OP: &str = "binds_key";
pub const SCROLL_OP: &str = "options_scroll";

// The fixed Lua sent when no run is listening comes from `crate::lua`, the same strings as xr_control.py's constants
// (`tests/lua_corpus.rs` and `tests/control_parity.rs` hold them to the Python).

/// A send key with no run listening: say so; nothing moves.
pub const NO_SESSION: &str = "No XR session is running; the window stays where it is.";
pub const MAX_CLIPBOARD: usize = 16384; // web_video.MAX_CLIPBOARD (the control frame limit)
pub const VIDEO_FILTER: &str =
    "Videos | *.mp4 *.MP4 *.mkv *.MKV *.webm *.mov *.MOV *.m4v *.avi *.ts *.m2ts";
const CLIPBOARD_EMPTY: &str = "The clipboard is empty. Copy a video link first.";
const CLIPBOARD_LONG: &str = "The clipboard holds too much text to be a video link.";
const CLIPBOARD_NO_LINK: &str =
    "The clipboard holds no video link. Copy the video page or video link first.";
const SOCKET_TIMEOUT: Duration = Duration::from_millis(500);

/// What the helper reaches outside its command line: the tools it runs and its environment.
pub struct Host<'a> {
    pub zenity: &'a Path,
    pub wl_paste: &'a Path,
    pub notify_send: &'a Path,
    pub env: &'a dyn Fn(&str) -> Option<OsString>,
}

fn process_env(name: &str) -> Option<OsString> {
    std::env::var_os(name)
}

impl Host<'static> {
    /// The installed tools at their fixed paths and this process's environment.
    pub fn system() -> Self {
        Host {
            zenity: Path::new("/usr/bin/zenity"),
            wl_paste: Path::new("/usr/bin/wl-paste"),
            notify_send: Path::new("/usr/bin/notify-send"),
            env: &process_env,
        }
    }
}

/// One control frame's payload, in the key order Python's dicts give.
#[derive(Debug, Clone, PartialEq)]
pub enum Payload {
    Empty,
    Key { mods: u32, code: u32 },
    Delta(i32),
    Path(String),
    Url(String),
}

/// `xr_control._is_token`: one leading letter or digit, then up to 127 of `[A-Za-z0-9_.:-]`.
pub fn is_token(value: &str) -> bool {
    let b = value.as_bytes();
    (1..=128).contains(&b.len())
        && b[0].is_ascii_alphanumeric()
        && b.iter()
            .all(|&c| c.is_ascii_alphanumeric() || b"_.:-".contains(&c))
}

/// Python's `str.isspace`: Rust's White_Space plus the four information separators U+001C..U+001F.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `json.dumps(s, ensure_ascii=False)`.
fn push_json_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// The control frame: `json.dumps(..., separators=(',', ':'), ensure_ascii=False)` plus a newline.
pub fn control_frame(
    run_id: &str,
    session: &str,
    op: &str,
    payload: &Payload,
    request_id: i128,
) -> String {
    let mut out = format!("{{\"schema\":1,\"request_id\":{request_id},\"run_id\":");
    push_json_str(&mut out, run_id);
    out.push_str(",\"session\":");
    push_json_str(&mut out, session);
    out.push_str(",\"op\":");
    push_json_str(&mut out, op);
    out.push_str(",\"payload\":");
    match payload {
        Payload::Empty => out.push_str("{}"),
        Payload::Key { mods, code } => {
            out.push_str(&format!("{{\"mods\":{mods},\"code\":{code}}}"))
        }
        Payload::Delta(d) => out.push_str(&format!("{{\"delta\":{d}}}")),
        Payload::Path(p) => {
            out.push_str("{\"path\":");
            push_json_str(&mut out, p);
            out.push('}');
        }
        Payload::Url(u) => {
            out.push_str("{\"url\":");
            push_json_str(&mut out, u);
            out.push('}');
        }
    }
    out.push_str("}\n");
    out
}

/// `time.time_ns()`.
fn time_ns() -> i128 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(d) => d.as_nanos() as i128,
        Err(e) => -(e.duration().as_nanos() as i128),
    }
}

fn os_err() -> io::Error {
    io::Error::last_os_error()
}

/// Waits up to `deadline` for `events` on `fd`; a timeout is an error, as Python's socket timeout is.
fn wait(fd: &OwnedFd, events: libc::c_short, deadline: Instant) -> io::Result<()> {
    loop {
        let left = deadline.saturating_duration_since(Instant::now());
        let mut p = libc::pollfd {
            fd: fd.as_raw_fd(),
            events,
            revents: 0,
        };
        let ms = left.as_millis().min(i32::MAX as u128) as libc::c_int;
        // SAFETY: one valid pollfd for the duration of the call.
        let n = unsafe { libc::poll(&mut p, 1, ms) };
        if n > 0 {
            return Ok(());
        }
        if n == 0 {
            return Err(io::Error::new(io::ErrorKind::TimedOut, "timed out"));
        }
        let e = os_err();
        if e.kind() != io::ErrorKind::Interrupted {
            return Err(e);
        }
    }
}

/// A Unix stream socket connected the way Python's `settimeout(0.5); connect(path)` connects: non-blocking, so a full
/// backlog fails at once (EAGAIN) and an in-progress connect waits up to the timeout. Paths up to 108 bytes, as Python.
fn connect(path: &[u8]) -> io::Result<OwnedFd> {
    // SAFETY: plain syscall; the descriptor is owned right away.
    let raw = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if raw < 0 {
        return Err(os_err());
    }
    // SAFETY: `raw` is a fresh descriptor nothing else owns.
    let fd = unsafe { OwnedFd::from_raw_fd(raw) };
    // SAFETY: sockaddr_un is plain old data; all zeroes is valid.
    let mut addr: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    if path.len() > addr.sun_path.len() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "AF_UNIX path too long",
        ));
    }
    if path.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "embedded null byte",
        ));
    }
    addr.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (dst, &src) in addr.sun_path.iter_mut().zip(path) {
        *dst = src as libc::c_char;
    }
    let len = (std::mem::size_of::<libc::sa_family_t>() + path.len()) as libc::socklen_t;
    let deadline = Instant::now() + SOCKET_TIMEOUT;
    // SAFETY: `addr` is a valid sockaddr_un of at least `len` bytes.
    let r = unsafe { libc::connect(fd.as_raw_fd(), (&raw const addr).cast(), len) };
    if r < 0 {
        let e = os_err();
        match e.raw_os_error() {
            Some(libc::EINPROGRESS) | Some(libc::EINTR) => {
                wait(&fd, libc::POLLOUT, deadline)?;
                let mut err: libc::c_int = 0;
                let mut n = std::mem::size_of::<libc::c_int>() as libc::socklen_t;
                // SAFETY: SO_ERROR writes one c_int into `err`.
                let r = unsafe {
                    libc::getsockopt(
                        fd.as_raw_fd(),
                        libc::SOL_SOCKET,
                        libc::SO_ERROR,
                        (&raw mut err).cast(),
                        &mut n,
                    )
                };
                if r < 0 {
                    return Err(os_err());
                }
                if err != 0 {
                    return Err(io::Error::from_raw_os_error(err));
                }
            }
            _ => return Err(e),
        }
    }
    Ok(fd)
}

/// Python's `sendall` under a 0.5 s timeout: the whole send within one deadline.
fn send_all(fd: &OwnedFd, mut data: &[u8]) -> io::Result<()> {
    let deadline = Instant::now() + SOCKET_TIMEOUT;
    while !data.is_empty() {
        // SAFETY: `data` is a valid buffer of its length.
        let n = unsafe {
            libc::send(
                fd.as_raw_fd(),
                data.as_ptr().cast(),
                data.len(),
                libc::MSG_NOSIGNAL,
            )
        };
        if n >= 0 {
            data = &data[n as usize..];
            continue;
        }
        let e = os_err();
        match e.kind() {
            io::ErrorKind::WouldBlock => wait(fd, libc::POLLOUT, deadline)?,
            io::ErrorKind::Interrupted => {}
            _ => return Err(e),
        }
    }
    Ok(())
}

/// Python's `recv(64)` under a 0.5 s timeout.
fn recv64(fd: &OwnedFd) -> io::Result<Vec<u8>> {
    let deadline = Instant::now() + SOCKET_TIMEOUT;
    let mut buf = [0u8; 64];
    loop {
        // SAFETY: `buf` is a valid 64-byte buffer.
        let n = unsafe { libc::recv(fd.as_raw_fd(), buf.as_mut_ptr().cast(), buf.len(), 0) };
        if n >= 0 {
            return Ok(buf[..n as usize].to_vec());
        }
        let e = os_err();
        match e.kind() {
            io::ErrorKind::WouldBlock => wait(fd, libc::POLLIN, deadline)?,
            io::ErrorKind::Interrupted => {}
            _ => return Err(e),
        }
    }
}

fn peer_uid(fd: &OwnedFd) -> io::Result<libc::uid_t> {
    let mut cred = libc::ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut n = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: SO_PEERCRED writes one ucred into `cred`.
    let r = unsafe {
        libc::getsockopt(
            fd.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&raw mut cred).cast(),
            &mut n,
        )
    };
    if r < 0 { Err(os_err()) } else { Ok(cred.uid) }
}

fn getuid() -> libc::uid_t {
    // SAFETY: getuid cannot fail.
    unsafe { libc::getuid() }
}

/// One frame to the run's control socket; the request id is taken just before the send (the run drops an older id).
pub fn send(
    path: &OsStr,
    run_id: &str,
    session: &str,
    op: &str,
    payload: &Payload,
) -> io::Result<()> {
    let data = control_frame(run_id, session, op, payload, time_ns());
    let fd = connect(path.as_bytes())?;
    send_all(&fd, data.as_bytes())
}

/// `[0-9a-f]{40}_[0-9]+_[0-9]+\Z`, Hyprland's instance signature.
fn is_instance(value: &[u8]) -> bool {
    if value.len() < 44
        || !value[..40]
            .iter()
            .all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
        || value[40] != b'_'
    {
        return false;
    }
    let mut parts = value[41..].split(|&c| c == b'_');
    let ok =
        |p: Option<&[u8]>| p.is_some_and(|p| !p.is_empty() && p.iter().all(u8::is_ascii_digit));
    ok(parts.next()) && ok(parts.next()) && parts.next().is_none()
}

/// One fixed-Lua eval on the compositor that ran this bind (its own environment); true if it answered ok.
pub fn eval_stale(host: &Host, code: &str) -> bool {
    let runtime = (host.env)("XDG_RUNTIME_DIR").unwrap_or_default().into_vec();
    let instance = (host.env)("HYPRLAND_INSTANCE_SIGNATURE")
        .unwrap_or_default()
        .into_vec();
    if !runtime.starts_with(b"/") || !is_instance(&instance) {
        return false;
    }
    let mut path = runtime;
    path.extend_from_slice(b"/hypr/");
    path.extend_from_slice(&instance);
    path.extend_from_slice(b"/.socket.sock");
    let attempt = || -> io::Result<bool> {
        let fd = connect(&path)?;
        if peer_uid(&fd)? != getuid() {
            return Ok(false);
        }
        send_all(&fd, format!("/eval {code}").as_bytes())?;
        Ok(recv64(&fd)?.trim_ascii() == b"ok")
    };
    attempt().unwrap_or(false)
}

/// A panel key with no run listening: remove its modal binds; every cleanup also shows a cursor the dead run hid.
pub fn drop_stale_menu_binds(host: &Host, code: &str) -> bool {
    eval_stale(host, &format!("{code} {}", overview_cursor::SHOW_LUA))
}

/// `os.access(path, X_OK)`.
fn executable(path: &Path) -> bool {
    let Ok(c) = std::ffi::CString::new(path.as_os_str().as_bytes()) else {
        return false;
    };
    // SAFETY: a valid NUL-terminated path.
    unsafe { libc::access(c.as_ptr(), libc::X_OK) == 0 }
}

/// `shutil.which(name, path=PATH or '/usr/bin:/bin')`.
fn which(name: &str, path: Option<OsString>) -> Option<PathBuf> {
    let path = path
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| OsString::from("/usr/bin:/bin"));
    let mut seen: Vec<&[u8]> = Vec::new();
    for dir in path.as_bytes().split(|&c| c == b':') {
        if seen.contains(&dir) {
            continue;
        }
        seen.push(dir);
        let file = Path::new(OsStr::from_bytes(dir)).join(name);
        if file.exists() && executable(&file) && !file.is_dir() {
            return Some(file);
        }
    }
    None
}

/// `subprocess.run(..., timeout=...)`: the exit status and stdout (when piped), or None if it could not start or ran
/// out of time (it is then killed and reaped).
fn run(mut cmd: Command, timeout: Duration) -> Option<(ExitStatus, Vec<u8>)> {
    let mut child: Child = cmd.spawn().ok()?;
    let reader = child.stdout.take().map(|mut out| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = out.read_to_end(&mut buf);
            buf
        })
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(2)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };
    let out = reader
        .map(|r| r.join().unwrap_or_default())
        .unwrap_or_default();
    status.map(|s| (s, out))
}

/// Best-effort desktop notification for a send key with no run listening.
pub fn notify_no_session(host: &Host) -> bool {
    let Some(binary) = which("notify-send", (host.env)("PATH")) else {
        return false;
    };
    let mut cmd = Command::new(binary);
    cmd.args([
        "--app-name",
        "XR Workspace",
        "--expire-time",
        "4000",
        "XR Workspace",
        NO_SESSION,
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());
    run(cmd, Duration::from_secs(2));
    true
}

/// Best effort desktop notification about VR video (the focused desktop is in the glasses).
pub fn notify(host: &Host, text: &str) {
    if !executable(host.notify_send) {
        return;
    }
    let mut cmd = Command::new(host.notify_send);
    cmd.args([
        "--app-name=XR Workspace",
        "--expire-time=8000",
        "VR video",
        text,
    ])
    .stdout(Stdio::null())
    .stderr(Stdio::null());
    run(cmd, Duration::from_secs(5));
}

/// File picker for Ctrl+Alt+O: an absolute path, or None if cancelled or no picker is installed.
pub fn pick_video(host: &Host) -> Option<String> {
    if !executable(host.zenity) {
        return None;
    }
    let home = (host.env)("HOME").unwrap_or_default().into_vec();
    let mut start = b"/".to_vec();
    if home.starts_with(b"/") {
        for sub in [&b"/Videos/"[..], b"/"] {
            let dir = [&home[..], sub].concat();
            if Path::new(OsStr::from_bytes(&dir)).is_dir() {
                start = dir;
                break;
            }
        }
    }
    let mut filename = OsString::from("--filename=");
    filename.push(OsStr::from_bytes(&start));
    let mut cmd = Command::new(host.zenity);
    cmd.args(["--file-selection", "--title=Play a VR video (180 / 360)"])
        .arg(format!("--file-filter={VIDEO_FILTER}"))
        .arg("--file-filter=All files | *")
        .arg(filename)
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let (status, out) = run(cmd, Duration::from_secs(600))?;
    let text = if status.success() {
        String::from_utf8(out).ok()?
    } else {
        String::new()
    };
    let path = text.trim_end_matches('\n');
    (path.starts_with('/') && !path.contains('\n') && path.len() <= 4096).then(|| path.to_owned())
}

/// The clipboard's text (wl-paste, once), or None. Never more than MAX_CLIPBOARD characters are accepted.
pub fn read_clipboard(host: &Host) -> Option<String> {
    if !executable(host.wl_paste) {
        return None;
    }
    let mut cmd = Command::new(host.wl_paste);
    cmd.args(["--no-newline", "--type", "text"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let (status, out) = run(cmd, Duration::from_secs(3))?;
    if !status.success() || out.len() > MAX_CLIPBOARD * 4 {
        return None;
    }
    String::from_utf8(out).ok()
}

/// `re.match(r'https?://\S+\Z', t, re.IGNORECASE)`, with Python's case folds (`s` also matches `ſ`).
fn is_link(t: &str) -> bool {
    let mut chars = t.chars().peekable();
    for want in ['h', 't', 't', 'p'] {
        if chars.next().map(|c| c.to_ascii_lowercase()) != Some(want) {
            return false;
        }
    }
    if chars.peek().is_some_and(|&c| matches!(c, 's' | 'S' | 'ſ')) {
        chars.next();
    }
    for want in [':', '/', '/'] {
        if chars.next() != Some(want) {
            return false;
        }
    }
    let mut any = false;
    for c in chars {
        if py_space(c) {
            return false;
        }
        any = true;
    }
    any
}

/// The clipboard as a video_open payload; Err(reason) if it holds no link. Only the shape is checked here; the run
/// validates everything.
pub fn clipboard_payload(text: Option<&str>) -> Result<Payload, &'static str> {
    let t = text.unwrap_or("").trim_matches(py_space);
    if t.is_empty() {
        return Err(CLIPBOARD_EMPTY);
    }
    if t.chars().count() > MAX_CLIPBOARD {
        return Err(CLIPBOARD_LONG);
    }
    if is_link(t) {
        Ok(Payload::Url(t.to_owned()))
    } else {
        Err(CLIPBOARD_NO_LINK)
    }
}

/// `binds_key MODS CODE`'s payload: two small decimal numbers (the run checks their range again).
fn key_payload(mods: &OsStr, code: &OsStr) -> Option<Payload> {
    let num = |a: &OsStr| {
        let b = a.as_bytes();
        (!b.is_empty() && b.len() <= 3 && b.iter().all(u8::is_ascii_digit))
            .then(|| b.iter().fold(0u32, |n, d| n * 10 + u32::from(d - b'0')))
    };
    let (mods, code) = (num(mods)?, num(code)?);
    (mods <= 255 && (8..=255).contains(&code)).then_some(Payload::Key { mods, code })
}

/// Python's `int(s)` for ASCII decimal text: surrounding whitespace (`int` strips White_Space only, not the U+001C..U+001F
/// that `str.strip` also takes), a sign, digits with single underscores between
/// them. None where `int` raises (and for non-ASCII digits, see the module notes), or past i64.
fn py_int(s: &str) -> Option<i64> {
    let s = s.trim_matches(char::is_whitespace);
    let (neg, digits) = match s.as_bytes().first() {
        Some(b'-') => (true, &s[1..]),
        Some(b'+') => (false, &s[1..]),
        _ => (false, s),
    };
    let b = digits.as_bytes();
    if b.is_empty()
        || !b[0].is_ascii_digit()
        || !b[b.len() - 1].is_ascii_digit()
        || digits.contains("__")
    {
        return None;
    }
    let mut n: i64 = 0;
    for &c in b {
        match c {
            b'_' => {}
            b'0'..=b'9' => n = n.checked_mul(10)?.checked_add(i64::from(c - b'0'))?,
            _ => return None,
        }
    }
    Some(if neg { -n } else { n })
}

/// The helper's whole run: `args` is the command line with the program name first; returns the exit status.
pub fn main(args: &[OsString], host: &Host) -> i32 {
    let text = |i: usize| args.get(i).and_then(|a| a.to_str());
    if args.len() == 6 && args[4] == SCROLL_OP {
        let Some(delta) = text(5).and_then(py_int) else {
            return 2;
        };
        let (path, run_id, session) = (&args[1], text(2).unwrap_or(""), text(3).unwrap_or(""));
        if !(-4096..=4096).contains(&delta)
            || !path.as_bytes().starts_with(b"/")
            || !is_token(run_id)
            || !is_token(session)
        {
            return 2;
        }
        return match send(
            path,
            run_id,
            session,
            SCROLL_OP,
            &Payload::Delta(delta as i32),
        ) {
            Ok(()) => 0,
            Err(_) => 1,
        };
    }
    let key = if args.len() == 7 && args[4] == KEY_OP {
        key_payload(&args[5], &args[6])
    } else {
        None
    };
    if args.len() != 5 && key.is_none() {
        return 2;
    }
    let (path, run_id, session) = (&args[1], text(2).unwrap_or(""), text(3).unwrap_or(""));
    let Some(op) = text(4) else { return 2 };
    let known = VIEW_OPS.contains(&op)
        || LEGACY_OPS.contains(&op)
        || VIDEO_OPS.contains(&op)
        || HELPER_OPS.contains(&op)
        || op == KEY_OP;
    if !known || !is_token(run_id) || !is_token(session) || !path.as_bytes().starts_with(b"/") {
        return 2;
    }
    if op == KEY_OP && key.is_none() {
        return 2;
    }
    let mut op = op;
    let mut payload = key.unwrap_or(Payload::Empty);
    if op == "video_open" {
        // A playing video hides the desktop the picker opens on; no run listening: no picker for nothing.
        if send(path, run_id, session, "video_exit", &Payload::Empty).is_err() {
            return 1;
        }
        let Some(chosen) = pick_video(host) else {
            return 0;
        };
        payload = Payload::Path(chosen);
    } else if op == "video_paste" {
        match clipboard_payload(read_clipboard(host).as_deref()) {
            Ok(p) => payload = p,
            Err(why) => {
                notify(host, why);
                return 0;
            }
        }
        op = "video_open";
    }
    if send(path, run_id, session, op, &payload).is_ok() {
        return 0;
    }
    if op.starts_with("video_") {
        eval_stale(host, &video::remove_video_binds_lua()); // incl. Escape (video_exit)
    }
    if op.starts_with("menu_") {
        drop_stale_menu_binds(host, &view::remove_menu_binds_lua()); // incl. Escape (menu_toggle)
    }
    if op.starts_with("resize_") {
        drop_stale_menu_binds(host, &resize::remove_resize_binds_lua()); // give the left button back
    }
    if op.starts_with("super_") {
        drop_stale_menu_binds(host, &view::remove_drag_binds_lua());
    }
    if op.starts_with("send_") {
        notify_no_session(host); // nothing moves
    }
    if op == "privacy_display" {
        eval_stale(host, privacy::WAKE_ALL_LUA); // never leave the main display dark
    }
    if op == KEY_OP || op == "binds_toggle" {
        eval_stale(host, keybinds::PANEL_OFF_LUA); // give the keyboard back
    }
    if op == KEY_OP {
        drop_stale_menu_binds(host, &view::remove_menu_binds_lua()); // the options menu's keys
    }
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens() {
        assert!(is_token("a"));
        assert!(is_token(&format!("a{}", "_".repeat(127))));
        assert!(!is_token(&format!("a{}", "_".repeat(128))));
        assert!(!is_token("_a"));
        assert!(!is_token(""));
        assert!(!is_token("aé"));
    }

    #[test]
    fn python_int() {
        for (s, v) in [
            ("12", Some(12)),
            (" +5\t", Some(5)),
            ("-0", Some(0)),
            ("1_000", Some(1000)),
            ("007", Some(7)),
            ("\u{1c}3", None),
            ("\u{85}3\u{3000}", Some(3)),
            ("_1", None),
            ("1_", None),
            ("1__0", None),
            ("0x1", None),
            ("", None),
            ("-", None),
            ("+-1", None),
            ("99999999999999999999", None),
        ] {
            assert_eq!(py_int(s), v, "{s:?}");
        }
    }

    #[test]
    fn instance_signature() {
        let hex = "0123456789abcdef0123456789abcdef01234567";
        assert!(is_instance(format!("{hex}_1_2").as_bytes()));
        assert!(!is_instance(format!("{hex}_1").as_bytes()));
        assert!(!is_instance(format!("{hex}_1_2_3").as_bytes()));
        assert!(!is_instance(format!("{hex}__2").as_bytes()));
        assert!(!is_instance(
            format!("{}_1_2", hex.to_uppercase()).as_bytes()
        ));
    }

    #[test]
    fn links() {
        assert!(is_link("https://x"));
        assert!(is_link("HTTPſ://x"));
        assert!(!is_link("https://"));
        assert!(!is_link("https://a b"));
        assert!(!is_link("ftp://a"));
        assert!(!is_link("https:/a"));
    }
}
