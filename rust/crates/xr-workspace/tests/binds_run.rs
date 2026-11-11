//! The run's Ctrl+Alt binds (`view_controls::binds::Binds`) against the Python's: every scenario in
//! rust/corpus/supervisor/binds_run.json (recorded by tests/native_spike/binds_run_corpus.py from
//! `managed.view_controls`'s bind closures and `ViewControl`'s bind work on the real `HyprlandClient`) sends the same
//! requests in the same order, step for step: the session mode's read and the install at the start, the work each
//! step's view asked for (the corpus's `calls`: a panel sync, a typing swap, a config reload), and the removal at the end.
//!
//! The stand-in compositor answers the n-th connection with the corpus's n-th reply, as tests/hyprland_transcripts.rs
//! does. Each step's work is settled before the next (the corpus joins the Python's threads).

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
use xr_workspace::hyprland::Client;
use xr_workspace::view_controls::Run;
use xr_workspace::view_controls::binds::{Binds, MenuState};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/supervisor/binds_run.json"
));

/// A private runtime directory with `hypr/<instance>/`, all 0700, as Hyprland lays it out.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str, instance: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-binds-{name}-{}", std::process::id()));
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

fn replay(name: &str, scenario: &Json, corpus: &Json) {
    let instance = corpus["instance"].as_str().unwrap();
    let bind_args: Vec<String> = serde_json::from_value(corpus["bind_args"].clone()).unwrap();
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
    let mut client = Client::new(instance, &runtime.0).unwrap();
    let capture = ["XR-1".to_string(), "XR-2".into(), "XR-3".into()];
    let renderer = PathBuf::from("/nonexistent/xr-renderer");
    let mut run = Run {
        client: &mut client,
        runtime: &runtime.0,
        run_id: corpus["run_id"].as_str().unwrap(),
        session: corpus["session"].as_str().unwrap(),
        capture: &capture,
        output: "DP-2",
        fallback: "DP-1",
        model: "luma-ultra",
        renderer: &renderer,
        sbs_mode: scenario["sbs_mode"].as_bool().unwrap(),
    };
    let check = |what: &str, step: &Json, from: usize| {
        let wire: Vec<String> = step["wire"]
            .as_array()
            .unwrap()
            .iter()
            .map(|w| w[0].as_str().unwrap().to_string())
            .collect();
        assert_eq!(compositor.since(from), wire, "{name} {what}: requests");
    };

    // managed.view_controls: bind_args and the session mode, then the install (no key bind panel here).
    let mut binds = Binds::open_with(&mut run, bind_args.clone());
    assert_eq!(binds.bind_args(), bind_args);
    binds.install(&mut run, None).unwrap();
    assert!(binds.settle());
    check("open", &steps[0], 0);

    for step in &steps[1..steps.len() - 1] {
        let from = compositor.count();
        for call in step["calls"].as_array().unwrap() {
            match call[0].as_str().unwrap() {
                "menu" => {
                    let s = &call[2];
                    let menu = MenuState {
                        menu_open: s["menu_open"].as_bool().unwrap(),
                        menu_keys_off: s["menu_keys_off"].as_bool().unwrap(),
                        options_owned: s["options_owned"].as_bool().unwrap(),
                    };
                    binds.sync_menu(menu, call[1].as_bool().unwrap());
                }
                "typing" => binds.typing(call[1].as_bool().unwrap(), None),
                "reload" => binds.after_reload(None, None),
                other => panic!("{name}: unknown call {other}"),
            }
        }
        assert!(binds.settle(), "{name}: the work settles");
        check(&format!("{}", step["step"]), step, from);
    }

    let from = compositor.count();
    binds.stop_menu_binds();
    binds.remove(&mut run);
    check("close", steps.last().unwrap(), from);
    compositor.finish();
}

#[test]
fn the_run_s_binds_send_what_the_python_sends() {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    let scenarios = corpus["scenarios"].as_object().unwrap();
    for name in [
        "typing",
        "menu",
        "presentation",
        "no-session-mode",
        "wide",
        "end-typing",
    ] {
        assert!(scenarios.contains_key(name), "{name}");
    }
    for (name, scenario) in scenarios {
        replay(name, scenario, &corpus);
    }
}

/// A reload's reassert (3D) runs off the loop once the mode is back, and the key bind panel's work that the view asks
/// for after the reload lands after it: both workers take their turns in the order the loop asked.
#[test]
fn a_reload_reasserts_3d_and_lands_before_work_asked_for_after_it() {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    let scenario = &corpus["scenarios"]["no-session-mode"];
    let instance = corpus["instance"].as_str().unwrap();
    let runtime = Runtime::new("reassert", instance);
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
    let mut client = Client::new(instance, &runtime.0).unwrap();
    let capture = ["XR-1".to_string()];
    let renderer = PathBuf::from("/nonexistent/xr-renderer");
    let mut run = Run {
        client: &mut client,
        runtime: &runtime.0,
        run_id: "run-1",
        session: "a1b2c3",
        capture: &capture,
        output: "DP-2",
        fallback: "DP-1",
        model: "luma-ultra",
        renderer: &renderer,
        sbs_mode: false,
    };
    let args: Vec<String> = serde_json::from_value(corpus["bind_args"].clone()).unwrap();
    let mut binds = Binds::open_with(&mut run, args);
    binds.install(&mut run, None).unwrap();
    let order = Arc::new(Mutex::new(Vec::new()));
    let seen = order.clone();
    let (counted, before) = (compositor.requests.clone(), compositor.count());
    binds.after_reload(
        None,
        Some(Box::new(move || {
            seen.lock().unwrap().push(format!(
                "reassert after {}",
                counted.lock().unwrap().len() - before
            ));
        })),
    );
    // Work of the key bind panel's kind, asked for after the reload, on a thread of its own.
    let ticket = binds.lock().ticket();
    let seen = order.clone();
    let other = thread::spawn(move || {
        ticket.wait();
        seen.lock().unwrap().push("after".to_string());
    });
    other.join().unwrap();
    assert!(binds.settle());
    let reload = steps[2]["wire"].as_array().unwrap().len();
    assert_eq!(
        *order.lock().unwrap(),
        [format!("reassert after {reload}"), "after".to_string()]
    );
    binds.stop_menu_binds();
    binds.remove(&mut run);
    compositor.finish();
}
