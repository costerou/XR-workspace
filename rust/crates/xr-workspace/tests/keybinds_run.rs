//! The run's key bind panel (`view_controls::keybinds::Keys`) against the Python's: every scenario in
//! rust/corpus/supervisor/keybinds_run.json (recorded by tests/native_spike/keybinds_run_corpus.py from
//! `managed.view_controls`'s key bind block and `BindControl` on the real `HyprlandClient`) sends the same requests in
//! the same order, step for step, with the same frames, toasts and saved `keybinds.json`.
//!
//! The stand-in compositor answers the n-th connection with the corpus's n-th reply, as tests/hyprland_transcripts.rs
//! does. Each step's worker jobs and toasts are joined before the next (the corpus joins the Python's threads).

use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde_json::Value as Json;
use xr_wire::json::Value;
use xr_wire::run_control::Frame;
use xr_workspace::hyprland::Client;
use xr_workspace::lua::BindCommand;
use xr_workspace::view_controls::keybinds::{Host, Keys, Notify};
use xr_workspace::view_controls::{Frames, Run};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/supervisor/keybinds_run.json"
));

/// A private runtime directory with `hypr/<instance>/`, all 0700, as Hyprland lays it out.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str, instance: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-keys-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("hypr").join(instance)).unwrap();
        for dir in [
            root.clone(),
            root.join("hypr"),
            root.join("hypr").join(instance),
        ] {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Runtime(root)
    }

    /// A listener on `dir/name`, bound through /proc/self/fd so a long TMPDIR still fits in sun_path.
    fn listen(dir: &Path, name: &str) -> UnixListener {
        let handle = fs::File::open(dir).unwrap();
        let listener =
            UnixListener::bind(format!("/proc/self/fd/{}/{name}", handle.as_raw_fd())).unwrap();
        listener.set_nonblocking(true).unwrap();
        listener
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// The stand-in compositor: each connection gets the next scripted reply (then "unknown request"); every request kept.
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
                        Ok(0) | Err(_) => break,
                        Ok(k) => request.extend_from_slice(&buf[..k]),
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

    fn count(&self) -> usize {
        self.requests.lock().unwrap().len()
    }

    fn since(&self, from: usize) -> Vec<String> {
        self.requests.lock().unwrap()[from..].to_vec()
    }

    fn finish(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap();
    }
}

/// The view control the corpus drives the panel in (`view.ViewControl` with nothing else open).
struct View {
    run_id: String,
    session: String,
    now: f64,
    next_id: u64,
}

impl Host for View {
    fn now(&self) -> f64 {
        self.now
    }
    fn typing(&self) -> bool {
        false
    }
    fn video_active(&self) -> bool {
        false
    }
    fn help_visible(&self) -> bool {
        false
    }
    fn close_help(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn menu_open(&self) -> bool {
        false
    }
    fn close_menu(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn keyboard_owned(&self) -> bool {
        false
    }
    fn run_id(&self) -> &str {
        &self.run_id
    }
    fn session(&self) -> &str {
        &self.session
    }
    fn next_request_id(&mut self) -> u64 {
        self.next_id += 1;
        self.next_id - 1
    }
    fn set_last_input(&mut self, _now: f64) {}
    fn binds_reinstalled(&mut self, _typing: bool, _menu_binds: bool, _options_binds: bool) {}
}

fn frame(op: &str, payload: Json) -> Frame {
    fn wire(v: &Json) -> Value {
        match v {
            Json::Null => Value::Null,
            Json::Bool(b) => Value::Bool(*b),
            Json::Number(n) => Value::Number(n.clone()),
            Json::String(s) => Value::String(s.clone()),
            Json::Array(a) => Value::Array(a.iter().map(wire).collect()),
            Json::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), wire(v))).collect()),
        }
    }
    Frame {
        request_id: 1,
        run_id: "run".into(),
        session: "session".into(),
        op: op.into(),
        payload: wire(&payload),
    }
}

fn replay(name: &str, scenario: &Json, instance: &str, bind_args: &[String]) {
    let runtime = Runtime::new(name, instance);
    let steps = scenario["steps"].as_array().unwrap();
    let script: Vec<(String, String)> = steps
        .iter()
        .flat_map(|s| s["wire"].as_array().unwrap())
        .map(|w| (w[0].as_str().unwrap().into(), w[1].as_str().unwrap().into()))
        .collect();
    let compositor = Compositor::serve(
        Runtime::listen(&runtime.0.join("hypr").join(instance), ".socket.sock"),
        script,
    );
    let path = runtime.0.join("config").join("keybinds.json");
    if let Some(saved) = scenario["saved"].as_object() {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let text = serde_json::json!({"schema": 1, "binds": saved}).to_string();
        fs::write(&path, text).unwrap();
    }
    let toasts: Arc<Mutex<Vec<String>>> = Arc::default();
    let heard = toasts.clone();
    let notify: Notify = Arc::new(move |t: &str| heard.lock().unwrap().push(t.to_string()));
    let mut client = Client::new(instance, &runtime.0).unwrap();
    let mut view = View {
        run_id: bind_args[3].clone(),
        session: bind_args[4].clone(),
        now: scenario["clock"].as_f64().unwrap(),
        next_id: scenario["first_request_id"].as_u64().unwrap(),
    };
    let capture = ["XR-1".to_string(), "XR-2".into(), "XR-3".into()];
    let renderer = PathBuf::from("/nonexistent/xr-renderer");
    let mut run = Run {
        client: &mut client,
        runtime: &runtime.0,
        run_id: &bind_args[3],
        session: &bind_args[4],
        capture: &capture,
        specs: &[],
        output: "DP-2",
        fallback: "DP-1",
        model: "luma-ultra",
        renderer: &renderer,
        sbs_mode: false,
    };
    let check = |what: &str, step: &Json, from: usize, frames: &[u8], toasts: &[String]| {
        let wire: Vec<String> = step["wire"]
            .as_array()
            .unwrap()
            .iter()
            .map(|w| w[0].as_str().unwrap().to_string())
            .collect();
        assert_eq!(compositor.since(from), wire, "{name} {what}: requests");
        assert_eq!(
            String::from_utf8_lossy(frames),
            step["frames"].as_str().unwrap(),
            "{name} {what}: frames"
        );
        let want: Vec<String> = serde_json::from_value(step["toasts"].clone()).unwrap();
        assert_eq!(toasts, want.as_slice(), "{name} {what}: toasts");
    };

    // managed.view_controls: the panel, then the binds' install with its keys (Binds::install), then installed().
    let mut keys = Keys::open_at(&mut run, bind_args.to_vec(), &path, notify);
    let cmd = BindCommand {
        python: &bind_args[0],
        client: &bind_args[1],
        control_socket: &bind_args[2],
        run_id: &bind_args[3],
        session: &bind_args[4],
    };
    let (run_binds, send_binds) = keys.bind_kwargs();
    let (binds, send) = (pairs(&run_binds), pairs(&send_binds));
    run.client
        .install_view_binds(&cmd, binds.as_deref(), send.as_deref())
        .unwrap();
    keys.installed();
    keys.startup_toast();
    keys.join(&mut view);
    let start = toasts.lock().unwrap().clone();
    check("open", &steps[0], 0, &[], &start);
    let mut heard = start.len();

    for step in &steps[1..steps.len() - 1] {
        let from = compositor.count();
        let s = step["step"].as_array().unwrap();
        let mut out = Frames::default();
        match s[0].as_str().unwrap() {
            "op" => keys.on_message(
                &frame(s[1].as_str().unwrap(), serde_json::json!({})),
                &mut view,
                &mut out,
            ),
            "key" => keys.on_message(
                &frame("binds_key", serde_json::json!({"mods": s[1], "code": s[2]})),
                &mut view,
                &mut out,
            ),
            _ => {
                view.now += s[1].as_f64().unwrap();
                let now = view.now;
                keys.poll(now, &mut view, &mut out);
            }
        }
        keys.join(&mut view);
        let all = toasts.lock().unwrap().clone();
        check(&format!("{s:?}"), step, from, &out.bytes, &all[heard..]);
        heard = all.len();
    }

    let from = compositor.count();
    keys.stop();
    check("stop", steps.last().unwrap(), from, &[], &[]);
    let saved = fs::read_to_string(&path).ok();
    assert_eq!(
        saved.as_deref(),
        scenario["saved_after"].as_str(),
        "{name}: keybinds.json"
    );
    compositor.finish();
}

fn pairs(b: &Option<Vec<(String, String)>>) -> Option<Vec<(&str, &str)>> {
    b.as_ref()
        .map(|v| v.iter().map(|(c, o)| (c.as_str(), o.as_str())).collect())
}

#[test]
fn the_run_s_panel_sends_what_the_python_sends() {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    let instance = corpus["instance"].as_str().unwrap();
    let bind_args: Vec<String> = serde_json::from_value(corpus["bind_args"].clone()).unwrap();
    let scenarios = corpus["scenarios"].as_object().unwrap();
    assert!(scenarios.contains_key("conflict") && scenarios.contains_key("defaults"));
    for (name, scenario) in scenarios {
        replay(name, scenario, instance, &bind_args);
    }
}

#[test]
fn no_bind_helper_no_panel() {
    let runtime = Runtime::new(
        "none",
        "0123456789abcdef0123456789abcdef01234567_1700000000_1",
    );
    let mut client = Client::new(
        "0123456789abcdef0123456789abcdef01234567_1700000000_1",
        &runtime.0,
    )
    .unwrap();
    let capture: [String; 0] = [];
    let renderer = PathBuf::from("/nonexistent/xr-renderer");
    let mut run = Run {
        client: &mut client,
        runtime: &runtime.0,
        run_id: "run",
        session: "session",
        capture: &capture,
        specs: &[],
        output: "DP-2",
        fallback: "DP-1",
        model: "luma-ultra",
        renderer: &renderer,
        sbs_mode: false,
    };
    // No binds installed (the binds module gave no helper): no panel, and nothing asked of the compositor.
    assert!(Keys::open(&mut run, None).is_none());
}
