//! The gate for the Hyprland client: every scenario in rust/corpus/hyprland/transcripts.json (recorded from the Python
//! HyprlandClient against a scripted compositor by tests/native_spike/lua_corpus.py) sends the same request bytes in the
//! same order, one request per connection, and ends with the same result or the same refusal.
//!
//! The stand-in here answers the n-th connection with the corpus's n-th reply, so the scripted compositor's state
//! (its monitor list after a mode rule) needs no re-implementation. Below the replay: the connect checks the corpus
//! cannot show (symlinks, permissions, a changed peer process).

use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::{Value as Json, json};
use xr_wire::json::Value;
use xr_workspace::hyprland::{self, Client, Error, Phase, PluginPin, Query};
use xr_workspace::lua::{BindCommand, fit_windows, restore, send, view, window_memory};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/hyprland/transcripts.json"
));
const INSTANCE: &str = "0123456789abcdef0123456789abcdef01234567_1700000000_1";
const PLUGIN: &str = "/home/user/.local/share/viture-workspace/bin/0123456789ab/libxr-ownership.so";
const BIND: BindCommand<'static> = BindCommand {
    python: "/usr/bin/python3",
    client: "/home/user/.local/share/viture-workspace/bin/0123456789ab/xr_control.py",
    control_socket: "/run/user/1000/viture-workspace/run-1/control.sock",
    run_id: "run-1",
    session: "a1b2c3",
};
const PANEL_BINDS: &[(&str, &str)] = &[
    ("CTRL + ALT + K", "zoom_in"),
    ("SHIFT + SUPER + T", "typing_toggle"),
];
const PANEL_SEND_BINDS: &[(&str, &str)] = &[
    ("CTRL + ALT + Y", "send_glasses"),
    ("CTRL + ALT + code:10", "send_glasses_1"),
];

fn address(i: u64) -> String {
    format!("0x{:x}", 0x55d0c0de0000u64 + i)
}

/// A private runtime directory with `hypr/<INSTANCE>/`, all 0700, as Hyprland lays it out.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-hypr-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("hypr").join(INSTANCE)).unwrap();
        for dir in [
            root.clone(),
            root.join("hypr"),
            root.join("hypr").join(INSTANCE),
        ] {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Runtime(root)
    }

    fn instance_dir(&self) -> PathBuf {
        self.0.join("hypr").join(INSTANCE)
    }

    /// A listener on `dir/name`, bound through /proc/self/fd so a long TMPDIR still fits in sun_path.
    fn listen(dir: &Path, name: &str) -> UnixListener {
        let handle = fs::File::open(dir).unwrap();
        let listener =
            UnixListener::bind(format!("/proc/self/fd/{}/{name}", handle.as_raw_fd())).unwrap();
        listener.set_nonblocking(true).unwrap();
        listener
    }

    fn client(&self) -> Client {
        Client::new(INSTANCE, &self.0).unwrap()
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// The stand-in compositor: answers each connection with the next scripted reply (then "unknown request") and keeps
/// every request. It reads a request until it has `expected` bytes or the client pauses, as the recorder did.
struct Compositor {
    requests: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Compositor {
    fn serve(listener: UnixListener, script: Vec<(String, String)>) -> Compositor {
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let (seen, done) = (requests.clone(), stop.clone());
        let thread = thread::spawn(move || {
            let mut n = 0;
            while !done.load(Ordering::SeqCst) {
                let mut conn = match listener.accept() {
                    Ok((c, _)) => c,
                    Err(e) if e.kind() == ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(1));
                        continue;
                    }
                    Err(e) => panic!("accept: {e}"),
                };
                conn.set_nonblocking(false).unwrap();
                conn.set_read_timeout(Some(Duration::from_millis(50)))
                    .unwrap();
                let (expected, reply) = script
                    .get(n)
                    .cloned()
                    .unwrap_or((String::new(), "unknown request".into()));
                let mut request = Vec::new();
                let mut buf = [0u8; 65536];
                while request.len() < expected.len().max(1) {
                    match conn.read(&mut buf) {
                        Ok(0) => break,
                        Ok(k) => request.extend_from_slice(&buf[..k]),
                        Err(_) => break,
                    }
                }
                seen.lock()
                    .unwrap()
                    .push(String::from_utf8(request).unwrap());
                let _ = conn.write_all(reply.as_bytes());
                n += 1;
            }
        });
        Compositor {
            requests,
            stop,
            thread: Some(thread),
        }
    }

    fn finish(mut self) -> Vec<String> {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap();
        self.requests.lock().unwrap().clone()
    }
}

/// An xr-wire value as serde_json, for comparison with the corpus (object key order is not part of the result).
fn plain(v: &Value) -> Json {
    match v {
        Value::Null => Json::Null,
        Value::Bool(b) => Json::Bool(*b),
        Value::Number(n) => Json::Number(n.clone()),
        Value::String(s) => Json::String(s.clone()),
        Value::Array(a) => Json::Array(a.iter().map(plain).collect()),
        Value::Object(o) => Json::Object(o.iter().map(|(k, v)| (k.clone(), plain(v))).collect()),
    }
}

fn unit(r: hyprland::Result<()>) -> hyprland::Result<Json> {
    r.map(|()| Json::Null)
}

fn sample_placement() -> window_memory::Placement {
    let (a, b) = (address(1), address(2));
    window_memory::Placement {
        whole: vec![(1, "XR-1".into(), 61)],
        windows: vec![
            (a.clone(), "XR-2".into(), 71, 0),
            (b.clone(), "XR-3".into(), 81, 1),
        ],
        layouts: vec![(a.clone(), "scrolling".into())],
        state: vec![(a.clone(), 1, 10, 20, 300, 400), (b.clone(), 0, 0, 0, 0, 0)],
        order: vec![vec![(a.clone(), 0, 300, 400), (b, 1, 300, 0)]],
        fullscreen: vec![(a.clone(), 2, 2)],
        shown: vec![("XR-1".into(), a.clone())],
        focus: Some(a),
        monitor: Some("DP-1".into()),
    }
}

fn snapshot() -> restore::Snapshot {
    restore::Snapshot {
        schema: 1,
        run_id: "run-1".into(),
        monitor: "DP-1".into(),
        active: Some(2),
        focus: Some(address(1)),
        focus_monitor: Some("DP-1".into()),
        workspaces: vec![(1, true), (2, false)],
        windows: vec![
            restore::Window {
                address: address(1),
                workspace: 1,
                floating: false,
                at: (0, 30),
                size: (960, 1050),
                fullscreen: 0,
                client: 0,
            },
            restore::Window {
                address: address(2),
                workspace: 2,
                floating: true,
                at: (100, 200),
                size: (800, 600),
                fullscreen: 2,
                client: 1,
            },
        ],
    }
}

/// The scenario's call, as lua_corpus.transcript_scenarios makes it.
fn call(name: &str, c: &mut Client) -> hyprland::Result<Json> {
    let w1 = address(1);
    let w2 = address(2);
    let q = |c: &mut Client, kind| c.query(kind, hyprland::DEADLINE).map(|v| plain(&v));
    let text = |r: hyprland::Result<String>| r.map(Json::String);
    let mode = |r: hyprland::Result<hyprland::Mode>| {
        r.map(|m| json!({"name": m.name, "x": m.x, "y": m.y, "scale": m.scale}))
    };
    match name {
        "query.monitors" => q(c, Query::Monitors),
        "query.workspaces" => q(c, Query::Workspaces),
        "query.clients" => q(c, Query::Clients),
        "query.activewindow" => q(c, Query::ActiveWindow),
        "query.cursorpos" => q(c, Query::CursorPos),
        "query.getoption_follow_mouse" => q(c, Query::FollowMouse),
        "query.getoption_active_border" => q(c, Query::ActiveBorder),
        "mutate.follow_mouse" => text(c.mutate("keyword input:follow_mouse 2")),
        "mutate.move_workspace" => text(c.mutate("dispatch moveworkspacetomonitor 4 XR-2")),
        "mutate.focus_desktop" => text(c.mutate(&view::focus_command("XR-1")?)),
        "mutate.send_window" => text(c.mutate(&send::move_command(&w1, 7, true)?)),
        "mutate.drop_window" => text(c.mutate(&send::move_command(&w1, 7, false)?)),
        "binds_text" => text(c.binds_text(hyprland::DEADLINE)),
        "reload_config" => unit(c.reload_config()),
        "plugins_loaded" => c.plugins_loaded().map(|n| json!(n)),
        "plugin_load" => unit(c.plugin_load(PLUGIN, PluginPin::Installed(PLUGIN))),
        "plugin_unload" => unit(c.plugin_unload(PLUGIN, PluginPin::Installed(PLUGIN))),
        "install_view_binds"
        | "install_view_binds.rejected"
        | "install_view_binds.send_rejected" => unit(c.install_view_binds(&BIND, None, None)),
        "install_view_binds.panel_binds" => {
            unit(c.install_view_binds(&BIND, Some(PANEL_BINDS), Some(PANEL_SEND_BINDS)))
        }
        "reinstall_view_binds.panel_binds" => {
            unit(c.reinstall_view_binds(&BIND, false, Some(PANEL_BINDS), Some(PANEL_SEND_BINDS)))
        }
        "reinstall_view_binds" => unit(c.reinstall_view_binds(&BIND, false, None, None)),
        "reinstall_view_binds.typing" => unit(c.reinstall_view_binds(&BIND, true, None, None)),
        "remove_view_binds" => unit(c.remove_view_binds()),
        "land_window" => unit(c.land_window(&w1, 2147483647, Some(-100), Some(1_000_000))),
        "adopt_windows" => unit(c.adopt_windows(&[(4, "XR-2")], &[(&w1, "XR-1")])),
        "place_windows.moves" => unit(c.place_windows(&sample_placement(), Phase::Moves)),
        "place_windows.settle" => unit(c.place_windows(&sample_placement(), Phase::Settle)),
        "fit_windows" => unit(c.fit_windows(&[
            fit_windows::Move {
                address: &w1,
                x: -40,
                y: 30,
                w: 0,
                h: 0,
            },
            fit_windows::Move {
                address: &w2,
                x: 3800,
                y: 30,
                w: 1920,
                h: 1050,
            },
        ])),
        "restore_layout" => unit(c.restore_layout(&snapshot())),
        "unpin_xr_windows" => unit(c.unpin_xr_windows()),
        "install_options_binds" => unit(c.install_options_binds(&BIND)),
        "open_key_panel" => unit(c.open_key_panel(&BIND)),
        "close_key_panel" => unit(c.close_key_panel()),
        "set_dpms.off" => unit(c.set_dpms("eDP-1", false, Some("DP-2"))),
        "set_dpms.on" => unit(c.set_dpms("HDMI-A-1", true, None)),
        "apply_session_mode" => mode(c.apply_session_mode("DP-2")),
        "restore_session_mode" => unit(c.restore_session_mode(&view::SessionMode {
            name: "DP-2",
            x: 1920,
            y: 0,
            scale: 1.0,
        })),
        "apply_wide_mode" | "apply_wide_mode.rejected" => c
            .apply_wide_mode("DP-2", 90.0, "luma-ultra")
            .map(|v| plain(&v)),
        "apply_pro2_mode" => c.apply_pro2_mode("DP-2", 60.0).map(|v| plain(&v)),
        "park_presentation" => c.park_presentation("DP-2").map(|v| plain(&v)),
        "install_video_binds" => unit(c.install_video_binds(&BIND)),
        "remove_video_binds" => unit(c.remove_video_binds()),
        "install_menu_binds" => unit(c.install_menu_binds(&BIND)),
        "remove_menu_binds" => unit(c.remove_menu_binds()),
        "install_resize_binds" => unit(c.install_resize_binds(&BIND)),
        "remove_resize_binds" => unit(c.remove_resize_binds()),
        "grab_pointer" => unit(c.grab_pointer(&BIND, 960, 540)),
        "release_pointer" => unit(c.release_pointer(None, None)),
        "release_pointer.at" => unit(c.release_pointer(Some(-1920), Some(1200))),
        "warp_cursor" => unit(c.warp_cursor(-1_000_000, 1_000_000)),
        "hide_overview_cursor" => unit(c.hide_overview_cursor()),
        "show_overview_cursor" => unit(c.show_overview_cursor()),
        "lock_pointer" => unit(c.lock_pointer(&BIND, 4800, 600)),
        "enter_desktop" => unit(c.enter_desktop(&BIND, Some(2880), Some(600), Some("XR-2"))),
        "arm_click_binds" => unit(c.arm_click_binds(&BIND)),
        "confine_cursor" => unit(c.confine_cursor(960, 600, "XR-1")),
        "scroll_to_window" => unit(c.scroll_to_window(&w1)),
        "verified_session.not_hyprland" => text(c.verified_session()),
        other => panic!("scenario {other} has no call here"),
    }
}

#[test]
fn transcript_corpus_replays_identically() {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    assert_eq!(corpus["instance"], INSTANCE);
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 60, "{} scenarios", scenarios.len());
    let mut failures = Vec::new();
    for s in scenarios {
        let name = s["name"].as_str().unwrap();
        let script: Vec<(String, String)> = s["exchanges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| {
                (
                    e["request"].as_str().unwrap().to_string(),
                    e["reply"].as_str().unwrap().to_string(),
                )
            })
            .collect();
        let runtime = Runtime::new("replay");
        let compositor = Compositor::serve(
            Runtime::listen(&runtime.instance_dir(), ".socket.sock"),
            script.clone(),
        );
        let mut client = runtime.client();
        let result = call(name, &mut client);
        let requests = compositor.finish();
        let expected: Vec<&String> = script.iter().map(|(r, _)| r).collect();
        if requests.iter().collect::<Vec<_>>() != expected {
            failures.push(format!(
                "{name}: requests\n  got  {requests:?}\n  want {expected:?}"
            ));
        }
        let outcome = match (&result, s.get("returned"), s.get("raised")) {
            (Ok(got), Some(want), None) if got == want => None,
            (Err(Error::Value(m)), None, Some(want)) if format!("ValueError: {m}") == *want => None,
            _ => Some(format!(
                "{name}: result\n  got  {result:?}\n  want {:?}",
                s.get("returned").or(s.get("raised"))
            )),
        };
        failures.extend(outcome);
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
    println!(
        "{} transcript scenarios replayed identically",
        scenarios.len()
    );
}

/// One cursorpos request against a fresh runtime, after `setup` alters it.
fn connect_after(name: &str, setup: impl FnOnce(&Runtime)) -> hyprland::Result<Value> {
    let runtime = Runtime::new(name);
    let compositor = Compositor::serve(
        Runtime::listen(&runtime.instance_dir(), ".socket.sock"),
        vec![("j/cursorpos".into(), r#"{"x":1,"y":2}"#.into())],
    );
    setup(&runtime);
    let result = runtime.client().query(Query::CursorPos, hyprland::DEADLINE);
    let requests = compositor.finish();
    if result.is_err() {
        assert!(
            requests.is_empty(),
            "a refused connect sent nothing: {requests:?}"
        );
    }
    result
}

fn permission(r: hyprland::Result<Value>) -> &'static str {
    match r {
        Err(Error::Permission(m)) => m,
        other => panic!("expected a permission refusal, got {other:?}"),
    }
}

#[test]
fn a_checked_connect_answers() {
    assert_eq!(
        plain(&connect_after("ok", |_| {}).unwrap()),
        json!({"x": 1, "y": 2})
    );
}

#[test]
fn symlinked_directories_are_refused() {
    // The instance directory, then its parent, swapped for a symlink to the real one.
    let swap = |path: PathBuf| {
        let real = path.with_extension("real");
        fs::rename(&path, &real).unwrap();
        symlink(&real, &path).unwrap();
    };
    assert_eq!(
        permission(connect_after("link-instance", |r| swap(r.instance_dir()))),
        "unsafe Hyprland socket directory"
    );
    assert_eq!(
        permission(connect_after("link-hypr", |r| swap(r.0.join("hypr")))),
        "unsafe Hyprland socket directory"
    );
}

#[test]
fn writable_directories_are_refused() {
    for (name, mode) in [("group-write", 0o720), ("other-write", 0o702)] {
        assert_eq!(
            permission(connect_after(name, |r| {
                fs::set_permissions(r.0.join("hypr"), fs::Permissions::from_mode(mode)).unwrap()
            })),
            "unsafe Hyprland socket directory"
        );
    }
    assert_eq!(
        permission(connect_after("runtime-write", |r| {
            fs::set_permissions(&r.0, fs::Permissions::from_mode(0o770)).unwrap()
        })),
        "unsafe Hyprland socket directory"
    );
}

#[test]
fn a_symlinked_or_non_socket_endpoint_is_refused() {
    assert_eq!(
        permission(connect_after("link-socket", |r| {
            let dir = r.instance_dir();
            fs::rename(dir.join(".socket.sock"), dir.join("real.sock")).unwrap();
            symlink(dir.join("real.sock"), dir.join(".socket.sock")).unwrap();
        })),
        "unsafe IPC socket"
    );
    assert_eq!(
        permission(connect_after("file-socket", |r| {
            let dir = r.instance_dir();
            fs::remove_file(dir.join(".socket.sock")).unwrap();
            fs::write(dir.join(".socket.sock"), b"").unwrap();
        })),
        "unsafe IPC socket"
    );
}

/// A Python child serving one request on `dir/.socket.sock`: a different process behind the same endpoint.
fn python_compositor(dir: &Path) -> std::process::Child {
    let script = r#"
import os, socket, sys
os.chdir(sys.argv[1])
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind('.socket.sock'); s.listen(1)
print('ready', flush=True)
c, _ = s.accept()
try: c.recv(65536); c.sendall(b'{"x":3,"y":4}')
except OSError: pass  # a refused client closes without sending
c.close()
"#;
    let mut child = std::process::Command::new("/usr/bin/python3")
        .args(["-I", "-c", script])
        .arg(dir)
        .stdout(std::process::Stdio::piped())
        .spawn()
        .expect("python3 for the second compositor process");
    let mut ready = [0u8; 6];
    child
        .stdout
        .as_mut()
        .unwrap()
        .read_exact(&mut ready)
        .unwrap();
    assert_eq!(&ready, b"ready\n");
    child
}

#[test]
fn a_changed_compositor_process_is_refused() {
    // The first request pins this process as the peer; the same endpoint then served by another process (a restarted
    // compositor, or a reused PID with another start time) is refused, and the pin is not replaced.
    let runtime = Runtime::new("peer-changed");
    let dir = runtime.instance_dir();
    let compositor = Compositor::serve(
        Runtime::listen(&dir, ".socket.sock"),
        vec![("j/cursorpos".into(), r#"{"x":1,"y":2}"#.into())],
    );
    let mut client = runtime.client();
    client.query(Query::CursorPos, hyprland::DEADLINE).unwrap();
    compositor.finish();
    let first = client.identity().cloned().unwrap();
    assert_eq!(first.pid, std::process::id() as i32);
    fs::remove_file(dir.join(".socket.sock")).unwrap();

    let mut child = python_compositor(&dir);
    let result = client.query(Query::CursorPos, hyprland::DEADLINE);
    child.wait().unwrap();
    assert!(
        matches!(&result, Err(Error::Value(m)) if m == "compositor peer changed"),
        "{result:?}"
    );
    assert_eq!(client.identity(), Some(&first));

    // A fresh client of the new process accepts it; its identity carries that process's start time.
    fs::remove_file(dir.join(".socket.sock")).unwrap();
    let mut child = python_compositor(&dir);
    let mut fresh = runtime.client();
    let started = Instant::now();
    assert_eq!(
        plain(&fresh.query(Query::CursorPos, hyprland::DEADLINE).unwrap()),
        json!({"x": 3, "y": 4})
    );
    assert!(started.elapsed() < hyprland::DEADLINE);
    let other = fresh.identity().cloned().unwrap();
    assert_eq!(other.pid, child.id() as i32);
    assert_ne!(other, first);
    assert!(!other.start.is_empty() && other.start.bytes().all(|b| b.is_ascii_digit()));
    child.wait().unwrap();
}

#[test]
fn an_oversized_reply_is_refused() {
    let runtime = Runtime::new("oversized");
    let big = "x".repeat(hyprland::LIMIT + 1);
    let compositor = Compositor::serve(
        Runtime::listen(&runtime.instance_dir(), ".socket.sock"),
        vec![("j/cursorpos".into(), big)],
    );
    let result = runtime.client().query(Query::CursorPos, hyprland::DEADLINE);
    compositor.finish();
    assert!(
        matches!(&result, Err(Error::Value(m)) if m == "Hyprland response exceeds 1 MiB"),
        "{result:?}"
    );
}

#[test]
fn ended_instances() {
    let runtime = Runtime::new("ended");
    let dir = runtime.instance_dir();
    // No socket and no lock: ended.
    assert!(hyprland::instance_ended(&runtime.0, INSTANCE));
    assert!(!hyprland::instance_ended(&runtime.0, "not-an-instance"));
    // A lock naming a live process that is not Hyprland: ended.
    fs::write(
        dir.join("hyprland.lock"),
        format!("{}\nwayland-1\n", std::process::id()),
    )
    .unwrap();
    assert!(hyprland::instance_ended(&runtime.0, INSTANCE));
    // An unreadable lock proves nothing.
    fs::write(dir.join("hyprland.lock"), "not a pid\n").unwrap();
    assert!(!hyprland::instance_ended(&runtime.0, INSTANCE));
    // The request socket is still there: not ended.
    let _listener = Runtime::listen(&dir, ".socket.sock");
    assert!(!hyprland::instance_ended(&runtime.0, INSTANCE));
}

#[test]
fn event_stream_lines() {
    let runtime = Runtime::new("events");
    let listener = Runtime::listen(&runtime.instance_dir(), ".socket2.sock");
    let mut client = runtime.client();
    let server = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            match listener.accept() {
                Ok((mut c, _)) => {
                    c.write_all(b"workspace>>2\nmonitoradded>>XR-1\npartial")
                        .unwrap();
                    return;
                }
                Err(_) if Instant::now() < deadline => thread::sleep(Duration::from_millis(1)),
                Err(e) => panic!("{e}"),
            }
        }
    });
    let mut events = client.event_stream().unwrap();
    server.join().unwrap();
    let mut lines = Vec::new();
    // Non-blocking: empty batches until the bytes arrive, then None once the compositor side closed.
    while let Some(batch) = events.read_lines().unwrap() {
        lines.extend(batch);
    }
    assert_eq!(
        lines,
        [b"workspace>>2".to_vec(), b"monitoradded>>XR-1".to_vec()]
    );
}

#[test]
fn presentation_placement_matches_the_python() {
    // The corpus's own park: DP-2 left of eDP-1 (2880x1800 at scale 1.5 is 1200 logical rows) and 8192 below it.
    let raw = xr_wire::json::parse_strict(
        br#"[{"id":0,"name":"eDP-1","width":2880,"height":1800,"refreshRate":60.0,"x":0,"y":0,"scale":1.5,"transform":0,"disabled":false,"dpmsStatus":true,"mirrorOf":"none"},
            {"id":1,"name":"DP-2","width":1920,"height":1200,"refreshRate":120.0,"x":1920,"y":0,"scale":1.0,"transform":0,"disabled":false,"dpmsStatus":true,"mirrorOf":"none"},
            {"id":2,"name":"XR-1","width":1920,"height":1080,"refreshRate":60.0,"x":-90000,"y":90000,"scale":1.0,"transform":0,"disabled":false,"dpmsStatus":true,"mirrorOf":"none"}]"#,
    )
    .unwrap();
    let placed = hyprland::presentation_placement(&raw, "DP-2").unwrap();
    assert_eq!((placed.x, placed.y), (-1920, 9392));
    assert_eq!(plain(placed.raw.get("x").unwrap()), json!(-1920));
    for (output, message) in [
        ("XR-1", "presentation must be a physical connector"),
        (
            "eDP-1",
            "presentation requires an independent usable desktop",
        ),
    ] {
        let only = xr_wire::json::parse_strict(
            br#"[{"id":0,"name":"eDP-1","width":2880,"height":1800,"refreshRate":60.0,"x":0,"y":0,"scale":1.5,"transform":0,"disabled":false,"dpmsStatus":true,"mirrorOf":"none"}]"#,
        )
        .unwrap();
        let r = hyprland::presentation_placement(&only, output);
        assert!(
            matches!(&r, Err(Error::Value(m)) if m == message),
            "{output}: {r:?}"
        );
    }
}

#[test]
fn a_lane_sends_the_same_requests_and_parses_the_same() {
    let runtime = Runtime::new("lane");
    let replies = [
        ("j/cursorpos", r#"{"x":1,"y":2.5}"#),
        ("j/cursorpos", r#"{"x":1,"y":2.5}"#),
        ("j/monitors all", r#"[{"name":"XR-1","x":0}]"#),
        ("j/cursorpos", "{\"x\":1,\"x\":2}"),
    ];
    let compositor = Compositor::serve(
        Runtime::listen(&runtime.instance_dir(), ".socket.sock"),
        replies.iter().map(|(q, r)| (q.to_string(), r.to_string())).collect(),
    );
    let mut client = runtime.client();
    assert!(matches!(client.lane(), Err(Error::Value(m)) if m == "no verified compositor peer yet"));
    let full = client.query(Query::CursorPos, hyprland::DEADLINE).unwrap();
    let mut lane = client.lane().unwrap();
    assert_eq!(Some(lane.identity()), client.identity());
    assert_eq!(lane.query(Query::CursorPos, hyprland::DEADLINE).unwrap(), full);
    assert_eq!(
        plain(&lane.query(Query::Monitors, hyprland::DEADLINE).unwrap()),
        json!([{"name": "XR-1", "x": 0}])
    );
    // The strict parse: a duplicate key is refused as Client::query refuses it.
    assert!(lane.query(Query::CursorPos, hyprland::DEADLINE).is_err());
    assert!(matches!(
        lane.query(Query::CursorPos, Duration::ZERO),
        Err(Error::Value(m)) if m == "invalid timeout"
    ));
    let requests = compositor.finish();
    assert_eq!(requests, replies.map(|(q, _)| q.to_string()));
}

#[test]
fn a_lane_refuses_a_changed_peer_or_endpoint() {
    let runtime = Runtime::new("lane-refused");
    let dir = runtime.instance_dir();
    let compositor = Compositor::serve(
        Runtime::listen(&dir, ".socket.sock"),
        vec![("j/cursorpos".into(), r#"{"x":1,"y":2}"#.into())],
    );
    let mut client = runtime.client();
    client.query(Query::CursorPos, hyprland::DEADLINE).unwrap();
    let mut lane = client.lane().unwrap();
    compositor.finish();

    // Another process behind the same endpoint: refused before anything is sent.
    fs::remove_file(dir.join(".socket.sock")).unwrap();
    let mut child = python_compositor(&dir);
    let result = lane.query(Query::CursorPos, hyprland::DEADLINE);
    child.wait().unwrap();
    assert!(
        matches!(&result, Err(Error::Value(m)) if m == "compositor peer changed"),
        "{result:?}"
    );

    // The endpoint swapped for a symlink, or for a file.
    fs::remove_file(dir.join(".socket.sock")).unwrap();
    let real = Runtime::listen(&dir, "real.sock");
    symlink(dir.join("real.sock"), dir.join(".socket.sock")).unwrap();
    assert!(matches!(
        lane.query(Query::CursorPos, hyprland::DEADLINE),
        Err(Error::Permission("unsafe IPC socket"))
    ));
    drop(real);
    fs::remove_file(dir.join(".socket.sock")).unwrap();
    fs::write(dir.join(".socket.sock"), b"").unwrap();
    assert!(matches!(
        lane.query(Query::CursorPos, hyprland::DEADLINE),
        Err(Error::Permission("unsafe IPC socket"))
    ));
}

#[test]
fn a_lane_refuses_an_oversized_reply() {
    let runtime = Runtime::new("lane-oversized");
    let compositor = Compositor::serve(
        Runtime::listen(&runtime.instance_dir(), ".socket.sock"),
        vec![
            ("j/cursorpos".into(), r#"{"x":1,"y":2}"#.into()),
            ("j/cursorpos".into(), "x".repeat(hyprland::LIMIT + 1)),
        ],
    );
    let mut client = runtime.client();
    client.query(Query::CursorPos, hyprland::DEADLINE).unwrap();
    let result = client.lane().unwrap().query(Query::CursorPos, hyprland::DEADLINE);
    compositor.finish();
    assert!(
        matches!(&result, Err(Error::Value(m)) if m == "Hyprland response exceeds 1 MiB"),
        "{result:?}"
    );
}
