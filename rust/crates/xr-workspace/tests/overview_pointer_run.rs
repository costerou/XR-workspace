//! The run's overview pointer (`view_controls::overview_pointer::Pointer`) against the Python's: every scenario in
//! rust/corpus/pointer/overview_pointer_run.json (recorded by tests/native_spike/overview_pointer_run_corpus.py from
//! `UnifiedPointer` and `UnifiedSampler` as `managed.view_controls` wires them, on the real `HyprlandClient`) gives the
//! same compositor calls in the same order, the same reads, the same broker read, the same calls at `stop`, and the
//! same last `overview_pointer` payload.
//!
//! The sampler is a thread on a clock, so the number of reads is timing; both sides run until the sampler has settled
//! (no call but a read for half a second) and compare what does not depend on timing. The stand-in compositor answers
//! by request as the corpus's does: the monitors, the cursor (which a warp moves), the broker's reply, `ok`.

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

use serde_json::{Value as Json, json};
use xr_workspace::hyprland::Client;
use xr_workspace::ownership::Broker;
use xr_workspace::pyjson;
use xr_workspace::supervisor::free_pointer::parse_overview_geometry;
use xr_workspace::view_controls::overview_pointer::{Pointer, cursor_reader};
use xr_workspace::view_controls::{Frames, Run};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/pointer/overview_pointer_run.json"
));
const INSTANCE: &str = "0123456789abcdef0123456789abcdef01234567_1700000000_1";
const QUIET: Duration = Duration::from_millis(500);
const LIMIT: Duration = Duration::from_secs(15);

/// A private runtime directory with `hypr/<instance>/`, all 0700, as Hyprland lays it out.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-pointer-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("hypr").join(INSTANCE)).unwrap();
        for dir in [root.clone(), root.join("hypr"), root.join("hypr").join(INSTANCE)] {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Runtime(root)
    }

    /// A listener on the request socket, bound through /proc/self/fd so a long TMPDIR still fits in sun_path.
    fn listen(&self) -> UnixListener {
        let handle = fs::File::open(self.0.join("hypr").join(INSTANCE)).unwrap();
        let listener =
            UnixListener::bind(format!("/proc/self/fd/{}/.socket.sock", handle.as_raw_fd())).unwrap();
        listener.set_nonblocking(true).unwrap();
        listener
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// The corpus's `answer`: the scripted compositor's reply to one request, a warp moving the cursor.
struct Script {
    monitors: String,
    cursor: Mutex<(i64, i64)>,
    shape: Option<String>,
    epoch: String,
    warp: regex::Regex,
}

impl Script {
    fn answer(&self, request: &str) -> String {
        if request == "j/monitors" || request == "j/monitors all" {
            return self.monitors.clone();
        }
        if request == "j/cursorpos" {
            let (x, y) = *self.cursor.lock().unwrap();
            return json!({"x": x, "y": y}).to_string();
        }
        if let Some(wire) = request.strip_prefix("j/xrspike ") {
            let j: Json = serde_json::from_str(wire).unwrap();
            let mut body = serde_json::Map::new();
            for k in ["schema", "session", "run_id", "request_id"] {
                body.insert(k.into(), j[k].clone());
            }
            body.insert("epoch".into(), json!(self.epoch));
            body.insert("result".into(), json!({"shape": self.shape.clone().unwrap_or_default()}));
            return Json::Object(body).to_string();
        }
        if let Some(c) = self.warp.captures(request) {
            *self.cursor.lock().unwrap() = (c[1].parse().unwrap(), c[2].parse().unwrap());
        }
        "ok".into()
    }
}

/// The stand-in compositor: each connection's request read whole (the client writes it, then waits), answered, kept.
struct Compositor {
    requests: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Compositor {
    fn serve(listener: UnixListener, script: Script) -> Compositor {
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let (seen, done) = (requests.clone(), stop.clone());
        let thread = thread::spawn(move || {
            while !done.load(Ordering::SeqCst) {
                let mut conn = match listener.accept() {
                    Ok((c, _)) => c,
                    Err(e) if e.kind() == ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_micros(200));
                        continue;
                    }
                    Err(e) => panic!("accept: {e}"),
                };
                conn.set_nonblocking(false).unwrap();
                let mut request = Vec::new();
                let mut buf = [0u8; 65536];
                conn.set_read_timeout(Some(Duration::from_millis(200))).unwrap();
                loop {
                    match conn.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(k) => request.extend_from_slice(&buf[..k]),
                    }
                    // The rest of a request comes at once: a short wait after each part ends it.
                    conn.set_read_timeout(Some(Duration::from_millis(3))).unwrap();
                }
                let request = String::from_utf8(request).unwrap();
                let reply = script.answer(&request);
                seen.lock().unwrap().push(request);
                let _ = conn.write_all(reply.as_bytes());
            }
        });
        Compositor { requests, stop, thread: Some(thread) }
    }

    fn take(&self) -> Vec<String> {
        std::mem::take(&mut *self.requests.lock().unwrap())
    }

    fn effects(&self) -> usize {
        self.requests.lock().unwrap().iter().filter(|r| !is_read(r)).count()
    }

    fn finish(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap();
    }
}

fn is_read(request: &str) -> bool {
    request.starts_with("j/")
}

/// The loop's frames, one JSON object a line.
fn frames(bytes: &[u8]) -> Vec<Json> {
    String::from_utf8_lossy(bytes)
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

/// The corpus's `summary`: every call but a read in order, the distinct reads, the first broker read.
fn summary(wire: &[String]) -> (Vec<String>, Vec<String>, Option<String>) {
    let effects = wire.iter().filter(|r| !is_read(r)).cloned().collect();
    let mut reads: Vec<String> = wire
        .iter()
        .filter(|r| is_read(r))
        .map(|r| if r.starts_with("j/xrspike ") { "j/xrspike".to_owned() } else { r.clone() })
        .collect();
    reads.sort();
    reads.dedup();
    let shape = wire.iter().find(|r| r.starts_with("j/xrspike ")).cloned();
    (effects, reads, shape)
}

fn strings(v: &Json) -> Vec<String> {
    v.as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_owned()).collect()
}

fn scenario(name: &str) {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    let expected = &corpus["scenarios"][name];
    let names = strings(&corpus["names"]);
    let args = strings(&corpus["bind_args"]);
    let (run_id, session) = (args[3].clone(), args[4].clone());
    let cursor = &expected["cursor"];
    let shape = expected["shape"].as_str().map(str::to_owned);
    let epoch = corpus["epoch"].as_str().unwrap().to_owned();

    let runtime = Runtime::new(name);
    let compositor = Compositor::serve(
        runtime.listen(),
        Script {
            monitors: corpus["monitors"].to_string(),
            cursor: Mutex::new((cursor[0].as_i64().unwrap(), cursor[1].as_i64().unwrap())),
            shape: shape.clone(),
            epoch: epoch.clone(),
            warp: regex::Regex::new(r"hl\.dsp\.cursor\.move\(\{ x = (-?\d+), y = (-?\d+) \}\)").unwrap(),
        },
    );
    let mut client = Client::new(INSTANCE, &runtime.0).unwrap();
    let reader = shape.as_ref().and_then(|_| {
        let mut broker = Broker::new(&run_id, &session, "DP-1").unwrap();
        broker.epoch = Some(epoch.clone()); // the handshake, taken as done (the corpus's too)
        cursor_reader(&broker)
    });
    let run = Run {
        client: &mut client,
        runtime: &runtime.0,
        run_id: &run_id,
        session: &session,
        capture: &names,
        output: "DP-2",
        fallback: "DP-1",
        model: "luma-ultra",
        renderer: Path::new("/nonexistent/xr-native-spike"),
        sbs_mode: false,
        specs: &[],
    };
    let mut pointer = Pointer::open(&run, None, Some(args.clone()), reader).expect("a pointer on the run's desktops");
    let geometry = pyjson::Json::parse(&corpus["geometry"].to_string()).unwrap();
    pointer.set_geometry(Some(parse_overview_geometry(&geometry).unwrap()));

    let mut last: Option<Json> = None;
    let (start, mut changed, mut seen) = (Instant::now(), Instant::now(), 0);
    loop {
        assert!(start.elapsed() < LIMIT, "{name}: the sampler never settled");
        let mut out = Frames::default();
        pointer.poll(Instant::now(), &mut out);
        for f in frames(&out.bytes) {
            if f["op"] == "overview_pointer" {
                last = Some(f["payload"].clone());
            }
        }
        let effects = compositor.effects();
        if effects != seen {
            (seen, changed) = (effects, Instant::now());
        }
        let ready = shape.is_none() || last.as_ref().and_then(|p| p["look"].as_str()) == shape.as_deref();
        if ready && changed.elapsed() >= QUIET {
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    let (effects, reads, first) = summary(&compositor.take());
    pointer.stop_sampler();
    pointer.close();
    // A sample in flight when the snapshot above was taken lands after it: `stop` itself reads nothing.
    let stop: Vec<String> = compositor.take().into_iter().filter(|r| !is_read(r)).collect();
    compositor.finish();

    assert_eq!(effects, strings(&expected["effects"]), "{name}: the calls");
    assert_eq!(reads, strings(&expected["reads"]), "{name}: the reads");
    assert_eq!(first.as_deref(), expected["first_shape_read"].as_str(), "{name}: the broker read");
    assert_eq!(stop, strings(&expected["stop"]), "{name}: stop");
    assert_eq!(last.as_ref(), Some(&expected["pointer"]), "{name}: the dot");
}

#[test]
fn the_dot_over_a_desktop_enters_it_with_the_runs_binds() {
    scenario("desktop");
}

#[test]
fn the_cursor_at_a_desktops_edge_is_held_where_it_was() {
    scenario("edge");
}

#[test]
fn the_broker_names_the_cursor_and_the_dot_looks_like_text() {
    scenario("text");
}

#[test]
fn no_handshake_no_cursor_read() {
    let broker = Broker::new("run", "session", "DP-1").unwrap();
    assert!(cursor_reader(&broker).is_none());
}
