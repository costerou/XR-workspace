//! The gate for the window sender (`view_controls::send::WindowSender`, VIEW_CONTROLS.md `send`, level 1): every
//! scenario in rust/corpus/send/sender.json (recorded from the Python `send.WindowSender` through a real
//! HyprlandClient against a scripted compositor by tests/native_spike/send_corpus.py) sends the same request bytes in
//! the same order, one request per connection, notifies the same text, hands back the same results and counts the
//! same moves. Each call is let finish before the next, as the recorder did.
//!
//! Below the replay: the worker's own timing, which a corpus cannot show (test_send_window.WorkerTests): a move in
//! flight lands before `close` returns and none starts after it, and `close` is bounded when the compositor hangs.

use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, channel};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::{Value as Json, json};
use xr_workspace::view_controls::send::{Origin, WindowSender};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/send/sender.json"
));
const INSTANCE: &str = "0123456789abcdef0123456789abcdef01234567_1700000000_1";

/// A private runtime directory with `hypr/<INSTANCE>/`, all 0700, as Hyprland lays it out.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-send-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let dir = root.join("hypr").join(INSTANCE);
        fs::create_dir_all(&dir).unwrap();
        for d in [root.clone(), root.join("hypr"), dir] {
            fs::set_permissions(&d, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Runtime(root)
    }

    /// The request socket, bound through /proc/self/fd so a long TMPDIR still fits in sun_path.
    fn listen(&self) -> UnixListener {
        let handle = fs::File::open(self.0.join("hypr").join(INSTANCE)).unwrap();
        let listener =
            UnixListener::bind(format!("/proc/self/fd/{}/.socket.sock", handle.as_raw_fd()))
                .unwrap();
        listener.set_nonblocking(true).unwrap();
        listener
    }

    fn origin(&self) -> Origin {
        Origin {
            instance: INSTANCE.into(),
            runtime: self.0.clone(),
            identity: None,
            session: String::new(),
        }
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// The stand-in compositor: answers the n-th connection with the n-th scripted reply (then "unknown request") and keeps
/// every request. It reads a request until it has the scripted request's length or the client pauses. A `/dispatch`
/// waits on `gate`, when there is one, before it is answered.
struct Compositor {
    requests: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Compositor {
    fn serve(
        listener: UnixListener,
        script: Vec<(String, String)>,
        gate: Option<Receiver<()>>,
    ) -> Compositor {
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
                let request = String::from_utf8(request).unwrap();
                if request.starts_with("/dispatch ")
                    && let Some(gate) = gate.as_ref()
                {
                    let _ = gate.recv_timeout(Duration::from_secs(5));
                }
                seen.lock().unwrap().push(request);
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

    fn requests(&self) -> Vec<String> {
        self.requests.lock().unwrap().clone()
    }

    fn finish(mut self) -> Vec<String> {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap();
        self.requests()
    }
}

type Notes = Arc<Mutex<Vec<String>>>;

fn sender(runtime: &Runtime, names: &[String], fallback: Option<&str>) -> (WindowSender, Notes) {
    let notes: Notes = Arc::default();
    let seen = notes.clone();
    let sender = WindowSender::new(
        runtime.origin(),
        names,
        fallback,
        Some(Box::new(move |text: &str| {
            seen.lock().unwrap().push(text.to_owned())
        })),
        Duration::from_secs(2),
    );
    (sender, notes)
}

fn point(v: &Json) -> Option<(i64, i64)> {
    Some((v[0].as_i64()?, v[1].as_i64()?))
}

fn strings(v: &Json) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn every_scenario_matches_the_python() {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    assert_eq!(corpus["instance"], INSTANCE);
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 50);
    for (i, scenario) in scenarios.iter().enumerate() {
        let name = scenario["name"].as_str().unwrap();
        let runtime = Runtime::new(&i.to_string());
        let script: Vec<(String, String)> = scenario["exchanges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| {
                (
                    e["request"].as_str().unwrap().to_owned(),
                    e["reply"].as_str().unwrap().to_owned(),
                )
            })
            .collect();
        let compositor = Compositor::serve(runtime.listen(), script.clone(), None);
        let (mut s, notes) = sender(
            &runtime,
            &strings(&scenario["names"]),
            scenario["fallback"].as_str(),
        );
        let mut results = Vec::new();
        for call in scenario["calls"].as_array().unwrap() {
            let got: Arc<Mutex<Option<Json>>> = Arc::default();
            let put = got.clone();
            let a = call.as_array().unwrap();
            match a[0].as_str().unwrap() {
                "request" => {
                    let done = a[3].as_bool().unwrap().then(|| {
                        Box::new(move |ok: bool, detail: String| {
                            *put.lock().unwrap() = Some(json!([ok, detail]));
                        }) as Box<dyn FnOnce(bool, String) + Send>
                    });
                    s.request(a[1].as_str().unwrap(), a[2].as_str().unwrap(), done);
                }
                "lookup" => s.lookup(
                    a[1].as_str().unwrap(),
                    point(&a[2]).unwrap(),
                    Box::new(move |w| *put.lock().unwrap() = Some(w)),
                ),
                "under_cursor" => {
                    s.under_cursor(Box::new(move |w| *put.lock().unwrap() = Some(w)));
                }
                "land" => s.land(a[1].as_str().unwrap(), a[2].as_str().unwrap()),
                "drop" => s.drop(
                    Some(a[1].clone()).filter(|w| !w.is_null()),
                    a[2].as_str().unwrap(),
                    point(&a[3]),
                    a[4].as_str().unwrap(),
                ),
                "set_names" => s.set_names(&strings(&a[1])),
                "close" => s.close(None),
                other => panic!("{name}: unknown call {other}"),
            }
            s.settle();
            results.push(got.lock().unwrap().take().unwrap_or(Json::Null));
        }
        s.close(None);
        let requests = compositor.finish();
        let expected: Vec<&str> = script.iter().map(|(r, _)| r.as_str()).collect();
        assert_eq!(requests, expected, "{name}: requests");
        assert_eq!(
            *notes.lock().unwrap(),
            strings(&scenario["notes"]),
            "{name}: notes"
        );
        assert_eq!(Json::Array(results), scenario["results"], "{name}: results");
        assert_eq!(
            s.moves() as u64,
            scenario["moves"].as_u64().unwrap(),
            "{name}: moves"
        );
    }
}

const WINDOW: &str = r#"{"address":"0x55aa01","monitor":0,"pinned":false,"workspace":{"id":1}}"#;
const MONITORS: &str = r#"[{"id":0,"name":"DP-1","activeWorkspace":{"id":1}},{"id":2,"name":"XR-1","activeWorkspace":{"id":31}},{"id":3,"name":"XR-2","activeWorkspace":{"id":41}}]"#;
const MOVE: &str = r#"/dispatch hl.dsp.window.move({ workspace = "31", follow = true, window = "address:0x55aa01" })"#;

fn one_move() -> Vec<(String, String)> {
    vec![
        ("j/activewindow".into(), WINDOW.into()),
        ("j/monitors all".into(), MONITORS.into()),
        (MOVE.into(), "ok".into()),
    ]
}

fn wait_for(what: impl Fn() -> bool) {
    let until = Instant::now() + Duration::from_secs(3);
    while !what() && Instant::now() < until {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(what());
}

#[test]
fn close_waits_for_a_move_in_flight_and_no_move_starts_after() {
    let runtime = Runtime::new("inflight");
    let (open, gate) = channel();
    let compositor = Compositor::serve(runtime.listen(), one_move(), Some(gate));
    let names = vec!["XR-1".to_owned(), "XR-2".to_owned()];
    let (mut s, notes) = sender(&runtime, &names, Some("DP-1"));
    s.request("send_glasses", "XR-1", None);
    // The move is sent and held by the compositor.
    wait_for(|| compositor.requests().len() == 2);
    thread::sleep(Duration::from_millis(100));
    let closer = thread::spawn(move || {
        s.close(None);
        s
    });
    thread::sleep(Duration::from_millis(100));
    assert!(!closer.is_finished(), "close waits on the move");
    open.send(()).unwrap();
    let mut s = closer.join().unwrap();
    assert!(s.closed());
    assert_eq!(s.moves(), 1);
    s.request("send_glasses", "XR-2", None);
    s.settle();
    assert_eq!(compositor.finish().len(), 3, "nothing after close");
    assert!(notes.lock().unwrap().is_empty());
}

#[test]
fn close_is_bounded_when_the_compositor_hangs() {
    let runtime = Runtime::new("hang");
    let (open, gate) = channel();
    let compositor = Compositor::serve(runtime.listen(), one_move(), Some(gate));
    let names = vec!["XR-1".to_owned()];
    let (mut s, _) = sender(&runtime, &names, Some("DP-1"));
    s.request("send_glasses", "XR-1", None);
    wait_for(|| compositor.requests().len() == 2);
    thread::sleep(Duration::from_millis(100));
    let t0 = Instant::now();
    s.close(Some(Duration::from_millis(200)));
    assert!(t0.elapsed() < Duration::from_millis(1500));
    assert!(s.closed());
    open.send(()).unwrap();
    s.settle();
    compositor.finish();
}

#[test]
fn a_compositor_that_is_not_there_is_one_notification() {
    let runtime = Runtime::new("gone"); // no socket
    let names = vec!["XR-1".to_owned()];
    let (mut s, notes) = sender(&runtime, &names, Some("DP-1"));
    s.request("send_glasses", "XR-1", None);
    s.settle();
    assert_eq!(*notes.lock().unwrap(), ["Could not move the window."]);
}
