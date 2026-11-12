//! Frame-for-frame parity between the Rust bind helper (`xr-control`) and `tools/native_spike/xr_control.py`.
//!
//! Every case runs both helpers against the same fakes: a control socket standing in for the run, a Hyprland socket
//! standing in for the compositor (it answers `ok`), and fake zenity, wl-paste and notify-send scripts that log their
//! command line. The two must agree on the exit status, every control frame byte for byte (the request id, a
//! wall-clock stamp, is checked for range and order and then blanked), every Lua eval and every tool command line.
//!
//! The ops that need no fixed-path tool run as real processes on both sides (`python3 -I xr_control.py`, and the
//! built `xr-control`). video_open and video_paste run their tools from fixed `/usr/bin` paths: the Python side runs
//! under `control_parity_harness.py`, which points those paths at the fakes, and the Rust side runs
//! `control::main` in process with a `Host` naming the fakes. Nothing here touches the live compositor or a real run.

use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{Read, Write};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

#[path = "support/script.rs"]
mod script;

use xr_workspace::{control, lua};

const INSTANCE: &str = "0123456789abcdef0123456789abcdef01234567_1700000000_42";
const RUN: &str = "run1";
const SESSION: &str = "sess.2:x-y_z";
const TOOLS: [&str; 3] = ["zenity", "wl-paste", "notify-send"];

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap()
}

fn helper_py() -> PathBuf {
    repo().join("tools/native_spike/xr_control.py")
}

fn harness_py() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/control_parity_harness.py")
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum Listen {
    Yes,
    No,
    /// A socket file nobody listens on (ECONNREFUSED).
    Stale,
}

#[derive(Clone)]
struct Case {
    name: String,
    /// The command line after the socket path; `None` puts the fixture's control socket first.
    args: Vec<OsString>,
    socket: Option<OsString>,
    listen: Listen,
    hypr: bool,
    /// Environment changes on top of the fixture's: a value, or None to unset.
    env: Vec<(&'static str, Option<OsString>)>,
    zenity: Option<(Vec<u8>, i32)>,
    paste: Option<(Vec<u8>, i32)>,
    notify: bool,
    videos_dir: bool,
    /// video_open / video_paste: run through the harness (Python) and in process (Rust).
    tools: bool,
    expect: i32,
}

fn case(name: impl Into<String>, args: &[&str], listen: Listen, expect: i32) -> Case {
    Case {
        name: name.into(),
        args: args.iter().map(OsString::from).collect(),
        socket: None,
        listen,
        hypr: true,
        env: Vec::new(),
        zenity: None,
        paste: None,
        notify: true,
        videos_dir: false,
        tools: false,
        expect,
    }
}

#[derive(Debug, PartialEq)]
struct Outcome {
    code: i32,
    frames: Vec<String>,
    evals: Vec<Vec<u8>>,
    tools: Vec<Vec<Vec<u8>>>,
}

struct Fixture {
    dir: PathBuf,
    control: PathBuf,
    hypr: PathBuf,
    tools: PathBuf,
    log: PathBuf,
    env: HashMap<String, OsString>,
}

static SEQ: AtomicUsize = AtomicUsize::new(0);

impl Fixture {
    fn new(c: &Case) -> Fixture {
        let mut base = std::env::temp_dir();
        if base.as_os_str().len() > 24 {
            base = PathBuf::from("/tmp"); // AF_UNIX paths stop at 108 bytes
        }
        let dir = base.join(format!(
            "xrc-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let tools = dir.join("bin");
        let home = dir.join("home");
        let hypr_dir = dir.join("rt/hypr").join(INSTANCE);
        for d in [&tools, &home, &hypr_dir, &dir.join("empty")] {
            std::fs::create_dir_all(d).unwrap();
        }
        if c.videos_dir {
            std::fs::create_dir_all(home.join("Videos")).unwrap();
        }
        let log = dir.join("tools.log");
        for name in TOOLS {
            let wanted = match name {
                "zenity" => c.zenity.as_ref(),
                "wl-paste" => c.paste.as_ref(),
                _ => None,
            };
            if name == "notify-send" && !c.notify || name != "notify-send" && wanted.is_none() {
                continue;
            }
            let (out, rc) = wanted.cloned().unwrap_or((Vec::new(), 0));
            std::fs::write(dir.join(format!("{name}.out")), out).unwrap();
            let script = tools.join(name);
            script::write_script(
                &script,
                &format!(
                    "#!/bin/sh\n{{ printf '%s\\0%s\\0' {name} \"$#\"; for a in \"$@\"; do printf '%s\\0' \"$a\"; done; }} >> '{log}'\n\
                     /usr/bin/cat '{out}'\nexit {rc}\n",
                    log = log.display(),
                    out = dir.join(format!("{name}.out")).display(),
                ),
            );
        }
        let mut env = HashMap::new();
        env.insert("XDG_RUNTIME_DIR".into(), dir.join("rt").into_os_string());
        env.insert("HYPRLAND_INSTANCE_SIGNATURE".into(), INSTANCE.into());
        env.insert("HOME".into(), home.into_os_string());
        env.insert("PATH".into(), tools.clone().into_os_string());
        for (k, v) in &c.env {
            match v {
                Some(v) => env.insert((*k).into(), v.clone()),
                None => env.remove(*k),
            };
        }
        Fixture {
            control: dir.join("control.sock"),
            hypr: hypr_dir.join(".socket.sock"),
            dir,
            tools,
            log,
            env,
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

/// A listener on `path` recording what each connection sent, until a connection sends `STOP`.
type Received = Arc<Mutex<Vec<Vec<u8>>>>;

fn serve(path: &Path, reply: Option<&'static [u8]>) -> (Received, JoinHandle<()>) {
    let listener = UnixListener::bind(path).unwrap();
    let got = Arc::new(Mutex::new(Vec::new()));
    let sink = got.clone();
    let handle = std::thread::spawn(move || {
        for conn in listener.incoming() {
            let mut conn = conn.unwrap();
            let mut data = Vec::new();
            match reply {
                None => {
                    conn.read_to_end(&mut data).unwrap();
                }
                Some(reply) => {
                    let mut buf = vec![0u8; 1 << 16];
                    let n = conn.read(&mut buf).unwrap_or(0);
                    data.extend_from_slice(&buf[..n]);
                    if data != b"STOP" {
                        let _ = conn.write_all(reply);
                    }
                }
            }
            if data == b"STOP" {
                break;
            }
            sink.lock().unwrap().push(data);
        }
    });
    (got, handle)
}

fn stop(path: &Path, handle: JoinHandle<()>) {
    let mut s = UnixStream::connect(path).unwrap();
    s.write_all(b"STOP").unwrap();
    s.shutdown(std::net::Shutdown::Write).unwrap();
    handle.join().unwrap();
}

/// The clock both helpers stamp request ids with (`time.monotonic_ns()`).
fn now_ns() -> i128 {
    xr_workspace::runner::monotonic_ns() as i128
}

/// Blanks each frame's request id after checking it is a wall-clock stamp inside the run, rising frame to frame.
fn normalise(frames: Vec<Vec<u8>>, t0: i128, t1: i128, name: &str) -> Vec<String> {
    let mut last = i128::MIN;
    frames
        .into_iter()
        .map(|f| {
            let s = String::from_utf8(f).unwrap_or_else(|e| panic!("{name}: frame not UTF-8: {e}"));
            let key = "\"request_id\":";
            let at = s
                .find(key)
                .unwrap_or_else(|| panic!("{name}: no request id in {s}"))
                + key.len();
            let end = at + s[at..].find(',').unwrap();
            let id: i128 = s[at..end].parse().unwrap();
            assert!(
                t0 <= id && id <= t1 && id > last,
                "{name}: request id {id} outside {t0}..{t1} or not rising"
            );
            last = id;
            format!("{}0{}", &s[..at], &s[end..])
        })
        .collect()
}

fn read_log(log: &Path, fx: &Fixture) -> Vec<Vec<Vec<u8>>> {
    let data = std::fs::read(log).unwrap_or_default();
    let mut fields = data.split(|&b| b == 0);
    let mut calls = Vec::new();
    while let Some(name) = fields.next() {
        if name.is_empty() {
            break;
        }
        let n: usize = std::str::from_utf8(fields.next().unwrap())
            .unwrap()
            .parse()
            .unwrap();
        let mut call = vec![name.to_vec()];
        for _ in 0..n {
            // The fixture folder differs run to run: name it the same on both sides.
            let arg = fields.next().unwrap();
            let dir = fx.dir.as_os_str().as_bytes();
            let at = arg.windows(dir.len()).position(|w| w == dir);
            call.push(match at {
                Some(i) => [&arg[..i], b"@DIR", &arg[i + dir.len()..]].concat(),
                None => arg.to_vec(),
            });
        }
        calls.push(call);
    }
    calls
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum Side {
    Python,
    Rust,
}

fn run(c: &Case, side: Side) -> Outcome {
    let fx = Fixture::new(c);
    let (control, control_thread) = match c.listen {
        Listen::Yes => {
            let (got, h) = serve(&fx.control, None);
            (Some(got), Some(h))
        }
        Listen::Stale => {
            drop(UnixListener::bind(&fx.control).unwrap());
            (None, None)
        }
        Listen::No => (None, None),
    };
    let (evals, hypr_thread) = if c.hypr {
        let (got, h) = serve(&fx.hypr, Some(b"ok"));
        (Some(got), Some(h))
    } else {
        (None, None)
    };
    let mut args = vec![
        c.socket
            .clone()
            .unwrap_or_else(|| fx.control.clone().into_os_string()),
    ];
    args.extend(c.args.iter().cloned());
    let t0 = now_ns();
    let code = match (side, c.tools) {
        (Side::Rust, true) => {
            let env = fx.env.clone();
            let lookup = move |k: &str| env.get(k).cloned();
            let (z, w, n) = (
                fx.tools.join("zenity"),
                fx.tools.join("wl-paste"),
                fx.tools.join("notify-send"),
            );
            let host = control::Host {
                zenity: &z,
                wl_paste: &w,
                notify_send: &n,
                env: &lookup,
            };
            let mut argv = vec![OsString::from("xr-control")];
            argv.extend(args);
            control::main(&argv, &host)
        }
        _ => {
            let mut cmd = match side {
                Side::Rust => Command::new(env!("CARGO_BIN_EXE_xr-control")),
                Side::Python => {
                    let mut cmd = Command::new("/usr/bin/python3");
                    cmd.arg("-I");
                    if c.tools {
                        cmd.arg(harness_py()).arg(helper_py()).arg(&fx.tools);
                    } else {
                        cmd.arg(helper_py());
                    }
                    cmd
                }
            };
            cmd.args(&args)
                .env_clear()
                .envs(&fx.env)
                .env("LC_ALL", "C.UTF-8");
            let status = cmd.status().unwrap();
            status
                .code()
                .unwrap_or_else(|| panic!("{}: {side:?} helper killed: {status}", c.name))
        }
    };
    let t1 = now_ns();
    if let Some(h) = control_thread {
        stop(&fx.control, h);
    }
    if let Some(h) = hypr_thread {
        stop(&fx.hypr, h);
    }
    let frames = control
        .map(|g| std::mem::take(&mut *g.lock().unwrap()))
        .unwrap_or_default();
    Outcome {
        code,
        frames: normalise(frames, t0, t1, &c.name),
        evals: evals
            .map(|g| std::mem::take(&mut *g.lock().unwrap()))
            .unwrap_or_default(),
        tools: read_log(&fx.log, &fx),
    }
}

fn check(cases: &[Case]) {
    let mut failed = Vec::new();
    for c in cases {
        let py = run(c, Side::Python);
        let rs = run(c, Side::Rust);
        if py != rs || py.code != c.expect {
            failed.push(format!(
                "{}:\n  expect code {}\n  python {py:?}\n  rust   {rs:?}",
                c.name, c.expect
            ));
        }
    }
    assert!(
        failed.is_empty(),
        "{} of {} cases differ:\n{}",
        failed.len(),
        cases.len(),
        failed.join("\n")
    );
    eprintln!("{} cases equal", cases.len());
}

fn python_lists() -> HashMap<String, String> {
    // The harness prints JSON; std has no JSON reader, so Python flattens it for us: one `name\tvalue` line each,
    // lists joined by commas.
    let out = Command::new("/usr/bin/python3")
        .arg("-I")
        .arg("-c")
        .arg(
            "import json,subprocess,sys;d=json.loads(subprocess.run([sys.executable,'-I',sys.argv[1],sys.argv[2],'--lists'],\
             capture_output=True,check=True).stdout)\n\
             for k,v in d.items(): print(k+'\\t'+(','.join(v) if isinstance(v,(list,tuple)) else str(v)))",
        )
        .arg(harness_py())
        .arg(helper_py())
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout)
        .unwrap()
        .lines()
        .map(|l| {
            let (k, v) = l.split_once('\t').unwrap();
            (k.to_owned(), v.to_owned())
        })
        .collect()
}

/// Every op Python accepts, in Python's order (so a new op there fails here until the port has it too).
fn python_ops(lists: &HashMap<String, String>) -> Vec<String> {
    let mut ops: Vec<String> = Vec::new();
    for k in ["OPS", "VIDEO_OPS", "HELPER_OPS"] {
        ops.extend(lists[k].split(',').map(str::to_owned));
    }
    ops
}

#[test]
fn op_lists_and_fixed_lua_match_python() {
    let lists = python_lists();
    let join = |v: &[&str]| v.join(",");
    let ours = [
        (
            "OPS",
            format!("{},{}", join(control::VIEW_OPS), join(control::LEGACY_OPS)),
        ),
        ("LEGACY_OPS", join(control::LEGACY_OPS)),
        ("VIDEO_OPS", join(control::VIDEO_OPS)),
        ("HELPER_OPS", join(control::HELPER_OPS)),
        ("KEY_OP", control::KEY_OP.to_owned()),
        ("LEAVE_KEY_PANEL", lua::keybinds::PANEL_OFF_LUA.to_owned()),
        ("REMOVE_VIDEO_BINDS", lua::video::remove_video_binds_lua()),
        ("WAKE_ALL_DISPLAYS", lua::privacy::WAKE_ALL_LUA.to_owned()),
        ("REMOVE_MENU_BINDS", lua::view::remove_menu_binds_lua()),
        (
            "REMOVE_RESIZE_BINDS",
            lua::resize::remove_resize_binds_lua(),
        ),
        ("REMOVE_DRAG_BINDS", lua::view::remove_drag_binds_lua()),
        (
            "SHOW_OVERVIEW_CURSOR",
            lua::overview_cursor::SHOW_LUA.to_owned(),
        ),
        ("NO_SESSION", control::NO_SESSION.to_owned()),
        ("MAX_CLIPBOARD", control::MAX_CLIPBOARD.to_string()),
        ("VIDEO_FILTER", control::VIDEO_FILTER.to_owned()),
    ];
    for (name, value) in ours {
        assert_eq!(lists[name], value, "{name}");
    }
    assert_eq!(lists.len(), 15);
}

#[test]
fn every_op_with_and_without_a_run() {
    let lists = python_lists();
    let mut cases = Vec::new();
    for op in python_ops(&lists) {
        if op == "video_open" || op == "video_paste" {
            continue; // tools: video_cases
        }
        cases.push(case(
            format!("{op} sent"),
            &[RUN, SESSION, &op],
            Listen::Yes,
            0,
        ));
        cases.push(case(
            format!("{op} no run"),
            &[RUN, SESSION, &op],
            Listen::No,
            1,
        ));
        cases.push(case(
            format!("{op} stale socket"),
            &[RUN, SESSION, &op],
            Listen::Stale,
            1,
        ));
    }
    // A send key with notify-send missing from PATH; the dead-run cleanups with no compositor to reach.
    let mut quiet = case(
        "send_glasses no notify-send",
        &[RUN, SESSION, "send_glasses"],
        Listen::No,
        1,
    );
    quiet.notify = false; // PATH stays the fixture's own bin folder: never fall back to the real /usr/bin
    cases.push(quiet);
    for (name, env) in [
        (
            "relative runtime",
            ("XDG_RUNTIME_DIR", Some(OsString::from("rt"))),
        ),
        ("no runtime", ("XDG_RUNTIME_DIR", None)),
        (
            "bad instance",
            (
                "HYPRLAND_INSTANCE_SIGNATURE",
                Some(OsString::from("ABC_1_2")),
            ),
        ),
        ("no instance", ("HYPRLAND_INSTANCE_SIGNATURE", None)),
    ] {
        for op in ["menu_toggle", "binds_key", "privacy_display"] {
            let args: Vec<&str> = if op == "binds_key" {
                vec![RUN, SESSION, op, "4", "36"]
            } else {
                vec![RUN, SESSION, op]
            };
            let mut c = case(format!("{op} {name}"), &args, Listen::No, 1);
            c.env.push(env.clone());
            cases.push(c);
        }
    }
    for op in [
        "video_exit",
        "resize_press",
        "super_release",
        "binds_toggle",
    ] {
        let mut c = case(
            format!("{op} no compositor"),
            &[RUN, SESSION, op],
            Listen::No,
            1,
        );
        c.hypr = false;
        cases.push(c);
    }
    check(&cases);
}

#[test]
fn key_and_scroll_arguments() {
    let mut cases = Vec::new();
    for (mods, code, expect) in [
        ("0", "8", 0),
        ("255", "255", 0),
        ("64", "36", 0),
        ("000", "008", 0),
        ("256", "10", 2),
        ("1", "7", 2),
        ("1", "256", 2),
        ("1234", "9", 2),
        ("", "9", 2),
        ("a", "9", 2),
        ("-1", "9", 2),
        ("+1", "9", 2),
        (" 1", "9", 2),
        ("1", "9 ", 2),
    ] {
        cases.push(case(
            format!("binds_key {mods:?} {code:?}"),
            &[RUN, SESSION, "binds_key", mods, code],
            Listen::Yes,
            expect,
        ));
    }
    cases.push(case(
        "binds_key no run",
        &[RUN, SESSION, "binds_key", "4", "36"],
        Listen::No,
        1,
    ));
    cases.push(case(
        "binds_key without numbers",
        &[RUN, SESSION, "binds_key"],
        Listen::Yes,
        2,
    ));
    cases.push(case(
        "binds_key one number",
        &[RUN, SESSION, "binds_key", "4"],
        Listen::Yes,
        2,
    ));
    cases.push(case(
        "binds_key three numbers",
        &[RUN, SESSION, "binds_key", "4", "36", "1"],
        Listen::Yes,
        2,
    ));
    cases.push(case(
        "recenter with numbers",
        &[RUN, SESSION, "recenter", "4", "36"],
        Listen::Yes,
        2,
    ));
    for (delta, expect) in [
        ("12", 0),
        ("-4096", 0),
        ("4096", 0),
        ("0", 0),
        ("-0", 0),
        ("4097", 2),
        ("-4097", 2),
        (" +5 ", 0),
        ("\t-7\n", 0),
        ("1_0", 0),
        ("_1", 2),
        ("1_", 2),
        ("1__0", 2),
        ("x", 2),
        ("", 2),
        ("0x1", 2),
        ("1.0", 2),
        ("\u{1c}3", 2),
        ("\u{85}3\u{3000}", 0),
        ("99999999999999999999999", 2),
    ] {
        cases.push(case(
            format!("options_scroll {delta:?}"),
            &[RUN, SESSION, "options_scroll", delta],
            Listen::Yes,
            expect,
        ));
    }
    cases.push(case(
        "options_scroll no run",
        &[RUN, SESSION, "options_scroll", "3"],
        Listen::No,
        1,
    ));
    cases.push(case(
        "options_scroll without delta",
        &[RUN, SESSION, "options_scroll"],
        Listen::Yes,
        2,
    ));
    cases.push(case(
        "options_scroll bad run id",
        &["_r", SESSION, "options_scroll", "3"],
        Listen::Yes,
        2,
    ));
    cases.push(case(
        "options_scroll bad session",
        &[RUN, "s s", "options_scroll", "3"],
        Listen::Yes,
        2,
    ));
    let mut rel = case(
        "options_scroll relative socket",
        &[RUN, SESSION, "options_scroll", "3"],
        Listen::Yes,
        2,
    );
    rel.socket = Some("control.sock".into());
    cases.push(rel);
    check(&cases);
}

#[test]
fn bad_command_lines() {
    let long = "a".repeat(128);
    let too_long = "a".repeat(129);
    let mut cases = vec![
        case("no op", &[RUN, SESSION], Listen::Yes, 2),
        case("unknown op", &[RUN, SESSION, "bogus"], Listen::Yes, 2),
        case(
            "extra argument",
            &[RUN, SESSION, "recenter", "x"],
            Listen::Yes,
            2,
        ),
        case("run id 128", &[&long, SESSION, "recenter"], Listen::Yes, 0),
        case(
            "run id 129",
            &[&too_long, SESSION, "recenter"],
            Listen::Yes,
            2,
        ),
        case(
            "run id leading underscore",
            &["_a", SESSION, "recenter"],
            Listen::Yes,
            2,
        ),
        case("run id empty", &["", SESSION, "recenter"], Listen::Yes, 2),
        case(
            "run id unicode",
            &["aé", SESSION, "recenter"],
            Listen::Yes,
            2,
        ),
        case("session slash", &[RUN, "a/b", "recenter"], Listen::Yes, 2),
        case("op case", &[RUN, SESSION, "Recenter"], Listen::Yes, 2),
    ];
    let mut bytes = case("run id not UTF-8", &[], Listen::Yes, 2);
    bytes.args = vec![
        OsString::from_vec(b"r\xff".to_vec()),
        SESSION.into(),
        "recenter".into(),
    ];
    cases.push(bytes);
    let mut rel = case(
        "relative socket",
        &[RUN, SESSION, "recenter"],
        Listen::Yes,
        2,
    );
    rel.socket = Some("control.sock".into());
    cases.push(rel);
    let mut odd = case(
        "socket path not UTF-8, no run",
        &[RUN, SESSION, "menu_back"],
        Listen::No,
        1,
    );
    odd.socket = Some(OsString::from_vec(b"/nonexistent/\xff.sock".to_vec()));
    cases.push(odd);
    let mut huge = case(
        "socket path past 108 bytes",
        &[RUN, SESSION, "resize_release"],
        Listen::No,
        1,
    );
    huge.socket = Some(OsString::from(format!("/{}", "s".repeat(120))));
    cases.push(huge);
    check(&cases);
}

fn video(name: &str, op: &str, listen: Listen, expect: i32) -> Case {
    let mut c = case(name, &[RUN, SESSION, op], listen, expect);
    c.tools = true;
    c
}

#[test]
fn video_open_picker() {
    let mut cases = Vec::new();
    let picks: Vec<(&str, Vec<u8>, i32, i32)> = vec![
        ("plain", b"/v/a.mp4\n".to_vec(), 0, 0),
        ("no newline", b"/v/a.mp4".to_vec(), 0, 0),
        ("several newlines", b"/v/a.mp4\n\n\n".to_vec(), 0, 0),
        (
            "escapes",
            "/v/\"q\\\t\u{1}\u{1b}\u{7f} é\u{2028}\r\u{8}\u{c}.mkv\n"
                .as_bytes()
                .to_vec(),
            0,
            0,
        ),
        ("cancelled", Vec::new(), 1, 0),
        ("cancelled with output", b"/v/a.mp4\n".to_vec(), 1, 0),
        ("relative", b"a.mp4\n".to_vec(), 0, 0),
        ("inner newline", b"/a\n/b\n".to_vec(), 0, 0),
        ("empty", b"\n".to_vec(), 0, 0),
        (
            "4096 bytes",
            [b"/".to_vec(), vec![b'a'; 4095], b"\n".to_vec()].concat(),
            0,
            0,
        ),
        (
            "4097 bytes",
            [b"/".to_vec(), vec![b'a'; 4096]].concat(),
            0,
            0,
        ),
        ("not UTF-8", b"/v/\xff.mp4\n".to_vec(), 0, 0),
    ];
    for (name, out, rc, expect) in picks {
        let mut c = video(
            &format!("video_open {name}"),
            "video_open",
            Listen::Yes,
            expect,
        );
        c.zenity = Some((out, rc));
        cases.push(c);
    }
    let mut c = video("video_open home with Videos", "video_open", Listen::Yes, 0);
    c.zenity = Some((b"/x.mp4\n".to_vec(), 0));
    c.videos_dir = true;
    cases.push(c);
    for (name, home) in [
        ("relative home", Some(OsString::from("home"))),
        ("no home", None),
    ] {
        let mut c = video(&format!("video_open {name}"), "video_open", Listen::Yes, 0);
        c.zenity = Some((b"/x.mp4\n".to_vec(), 0));
        c.env.push(("HOME", home));
        cases.push(c);
    }
    cases.push(video("video_open no zenity", "video_open", Listen::Yes, 0));
    let mut c = video("video_open no run", "video_open", Listen::No, 1);
    c.zenity = Some((b"/x.mp4\n".to_vec(), 0));
    cases.push(c);
    check(&cases);
}

#[test]
fn video_paste_clipboard() {
    let mut cases = Vec::new();
    let big = format!("https://{}", "a".repeat(control::MAX_CLIPBOARD - 8));
    let bigger = format!("https://{}", "a".repeat(control::MAX_CLIPBOARD - 7));
    let huge = format!("https://{}", "é".repeat(control::MAX_CLIPBOARD * 2));
    let clips: Vec<(&str, Vec<u8>, i32)> = vec![
        ("link", b"https://example.com/watch?v=1".to_vec(), 0),
        ("spaced link", b"  HTTPS://x/y\n".to_vec(), 0),
        ("long s", "httpſ://x".as_bytes().to_vec(), 0),
        ("http", b"http://x".to_vec(), 0),
        (
            "escapes",
            "https://a/\"\\\u{1f}\u{7f}é\u{2028}".as_bytes().to_vec(),
            0,
        ),
        (
            "ideographic space",
            "https://x\u{3000}".as_bytes().to_vec(),
            0,
        ),
        ("separator", b"\x1c https://x \x1f".to_vec(), 0),
        ("empty", Vec::new(), 0),
        ("spaces", b"   \n".to_vec(), 0),
        ("separator only", b"\x1c".to_vec(), 0),
        ("words", b"hello".to_vec(), 0),
        ("inner space", b"https://a b".to_vec(), 0),
        ("nothing after scheme", b"https://".to_vec(), 0),
        ("ftp", b"ftp://x".to_vec(), 0),
        ("16384 characters", big.into_bytes(), 0),
        ("16385 characters", bigger.into_bytes(), 0),
        ("past 65536 bytes", huge.into_bytes(), 0),
        ("not UTF-8", b"https://x/\xff".to_vec(), 0),
        ("wl-paste fails", b"https://x".to_vec(), 1),
    ];
    for (name, out, rc) in clips {
        let mut c = video(
            &format!("video_paste {name}"),
            "video_paste",
            Listen::Yes,
            0,
        );
        c.paste = Some((out, rc));
        cases.push(c);
    }
    cases.push(video(
        "video_paste no wl-paste",
        "video_paste",
        Listen::Yes,
        0,
    ));
    let mut c = video("video_paste no notify-send", "video_paste", Listen::Yes, 0);
    c.paste = Some((b"hello".to_vec(), 0));
    c.notify = false;
    cases.push(c);
    let mut c = video("video_paste no run", "video_paste", Listen::No, 1);
    c.paste = Some((b"https://x".to_vec(), 0));
    cases.push(c);
    let mut c = video("video_paste no run, no link", "video_paste", Listen::No, 0);
    c.paste = Some((b"hello".to_vec(), 0));
    cases.push(c);
    check(&cases);
}

#[test]
fn harness_paths_stay_inside_the_fixture() {
    // The fakes stand in for the fixed /usr/bin tools; a missing fake must never fall through to the real one.
    let c = video("probe", "video_paste", Listen::Yes, 0);
    let fx = Fixture::new(&c);
    for name in ["zenity", "wl-paste"] {
        assert!(!fx.tools.join(name).exists());
    }
    assert_eq!(fx.env["PATH"], fx.tools.as_os_str());
}
