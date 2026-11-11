//! `view_controls::window_memory::Tracker` against the Python `window_memory.WindowTracker`: every scenario of
//! rust/corpus/window_memory/tracker.json (recorded by tests/native_spike/window_memory_tracker_corpus.py, the Python
//! tracker on the Python HyprlandClient against a scripted compositor) sends the same request bytes in the same order,
//! step by step, leaves the same memory file after each step and the same counts.
//!
//! The stand-in answers the n-th connection with the corpus's n-th reply, as tests/hyprland_transcripts.rs does. A
//! capture runs on the tracker's worker thread with its own client, pinned to the compositor the run's client
//! verified; one warm-up query (left out of the comparison) gives the run's client that identity, as a run's earlier
//! requests do.

use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use xr_workspace::hyprland::{Client, DEADLINE, Query};
use xr_workspace::view_controls::Run;
use xr_workspace::view_controls::window_memory::{Counts, Tracker};
use xr_workspace::window_memory::Store;

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/window_memory/tracker.json"
));
const WARM_UP: &str = "j/monitors all";

/// A private runtime directory with `hypr/<instance>/` and `config/`, all 0700.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str, instance: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-wm-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("hypr").join(instance)).unwrap();
        fs::create_dir_all(root.join("config")).unwrap();
        for dir in [
            root.clone(),
            root.join("hypr"),
            root.join("hypr").join(instance),
            root.join("config"),
        ] {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Runtime(root)
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// A listener on `dir/name`, bound through /proc/self/fd so a long TMPDIR still fits in sun_path.
fn listen(dir: &Path, name: &str) -> UnixListener {
    let handle = fs::File::open(dir).unwrap();
    let listener =
        UnixListener::bind(format!("/proc/self/fd/{}/{name}", handle.as_raw_fd())).unwrap();
    listener.set_nonblocking(true).unwrap();
    listener
}

/// The stand-in compositor: each connection gets the next scripted reply; every request is kept.
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

    fn seen(&self) -> Vec<String> {
        self.requests.lock().unwrap().clone()
    }

    fn finish(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap();
    }
}

fn file(path: &Path) -> Value {
    match fs::read_to_string(path) {
        Err(_) => Value::Null,
        Ok(text) => serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text})),
    }
}

fn counts(c: Counts) -> Value {
    json!({"captures": c.captures, "writes": c.writes, "failures": c.failures, "restored": c.restored})
}

fn replay(corpus: &Value, scenario: &Value) {
    let name = scenario["name"].as_str().unwrap();
    let instance = corpus["instance"].as_str().unwrap();
    let runtime = Runtime::new(name, instance);
    let path = runtime.0.join("config").join("window-memory.json");
    match &scenario["before"] {
        Value::Null => {}
        Value::String(raw) => fs::write(&path, raw).unwrap(),
        before => fs::write(&path, serde_json::to_vec(before).unwrap()).unwrap(),
    }
    let steps = scenario["steps"].as_array().unwrap();
    let mut script = vec![(WARM_UP.to_owned(), "[]".to_owned())];
    for step in steps {
        for x in step["exchanges"].as_array().unwrap() {
            script.push((
                x["request"].as_str().unwrap().to_owned(),
                x["reply"].as_str().unwrap().to_owned(),
            ));
        }
    }
    let compositor = Compositor::serve(
        listen(&runtime.0.join("hypr").join(instance), ".socket.sock"),
        script,
    );
    let mut client = Client::new(instance, &runtime.0).unwrap();
    client.query(Query::Monitors, DEADLINE).unwrap();
    let names: Vec<String> = corpus["names"]
        .as_array()
        .unwrap()
        .iter()
        .map(|n| n.as_str().unwrap().to_owned())
        .collect();
    let model = corpus["model"].as_str().unwrap();
    let fallback = corpus["fallback"].as_str().unwrap();
    macro_rules! run {
        () => {
            Run {
                client: &mut client,
                runtime: &runtime.0,
                run_id: "run",
                session: "session",
                capture: &names,
                output: "glasses",
                fallback,
                model,
                renderer: Path::new("/nonexistent/renderer"),
                sbs_mode: false,
            }
        };
    }
    let mut tracker = Tracker::new(Store::new(&path, model).unwrap(), &mut run!());
    tracker.delay = Duration::ZERO;
    if !scenario["context"].is_null() {
        let context = scenario["context"].clone();
        tracker.set_context(Arc::new(move || Some(context.clone())));
    }
    let mut sent = 1;
    for (k, step) in steps.iter().enumerate() {
        match step["do"].as_str().unwrap() {
            "place" => tracker.place(&mut run!()),
            "finish" => tracker.finish(&mut run!()),
            "capture" => {
                tracker.mark();
                tracker.poll(Instant::now() + Duration::from_secs(1));
                tracker.join();
            }
            "close" => tracker.close(&mut run!()),
            other => panic!("{name}: unknown step {other}"),
        }
        let seen = compositor.seen();
        let want: Vec<&str> = step["exchanges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x["request"].as_str().unwrap())
            .collect();
        assert_eq!(
            seen.get(sent..).unwrap_or_default(),
            want.as_slice(),
            "{name}, step {k}: the requests"
        );
        sent = seen.len();
        assert_eq!(file(&path), step["file"], "{name}, step {k}: the file");
        assert_eq!(
            counts(tracker.counts()),
            step["counts"],
            "{name}, step {k}: the counts"
        );
    }
    compositor.finish();
}

#[test]
fn every_scenario_matches_the_python_tracker() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 15);
    for scenario in scenarios {
        replay(&corpus, scenario);
    }
}

#[test]
fn marks_inside_the_delay_are_one_capture_and_nothing_marked_reads_nothing() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let instance = corpus["instance"].as_str().unwrap();
    let runtime = Runtime::new("marks", instance);
    let compositor = Compositor::serve(
        listen(&runtime.0.join("hypr").join(instance), ".socket.sock"),
        vec![(WARM_UP.into(), "[]".into())],
    );
    let mut client = Client::new(instance, &runtime.0).unwrap();
    client.query(Query::Monitors, DEADLINE).unwrap();
    let names = vec!["XR-1".to_owned()];
    let path = runtime.0.join("config").join("window-memory.json");
    let mut tracker = Tracker::new(
        Store::new(&path, "luma-ultra").unwrap(),
        &mut Run {
            client: &mut client,
            runtime: &runtime.0,
            run_id: "run",
            session: "session",
            capture: &names,
            output: "glasses",
            fallback: "DP-1",
            model: "luma-ultra",
            renderer: Path::new("/nonexistent/renderer"),
            sbs_mode: false,
        },
    );
    let start = Instant::now();
    tracker.poll(start + Duration::from_secs(100));
    assert_eq!(tracker.deadline(), None);
    tracker.mark();
    let due = tracker.deadline().unwrap();
    tracker.mark();
    tracker.mark();
    assert_eq!(
        tracker.deadline(),
        Some(due),
        "later marks keep the first due time"
    );
    assert!(due >= start + Duration::from_millis(900));
    tracker.poll(due - Duration::from_millis(10));
    tracker.join();
    assert_eq!(compositor.seen().len(), 1, "nothing before the delay");
    tracker.poll(due);
    tracker.join();
    tracker.poll(due + Duration::from_secs(1));
    tracker.join();
    assert_eq!(tracker.counts().captures, 1, "one capture for three marks");
    assert_eq!(tracker.deadline(), None);
    compositor.finish();
}
