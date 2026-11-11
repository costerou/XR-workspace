//! Privacy mode against the Python's: every scenario in rust/corpus/supervisor/privacy.json (recorded from the Python
//! `privacy.PrivacyDisplay` and `privacy.wake` on the shipped HyprlandClient, by tests/native_spike/privacy_corpus.py)
//! replayed against `supervisor::privacy` on `hyprland::Client`: the same request bytes in the same order, one request
//! per connection, the same completions, the same notifications and the same recovery results.
//!
//! The stand-in answers the n-th connection with the corpus's n-th reply, as tests/hyprland_transcripts.rs does, so the
//! recorder's DPMS state (and the display "something else" lit in the watcher scenario) comes with the replies.

use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::Value as Json;
use xr_workspace::hyprland::Client;
use xr_workspace::supervisor::privacy::{DEADLINE, Notify, PrivacyDisplay, wake};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/supervisor/privacy.json"
));

/// A private runtime directory with `hypr/<instance>/`, all 0700, as Hyprland lays it out.
struct Runtime(PathBuf);

impl Runtime {
    fn new(name: &str, instance: &str) -> Runtime {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-privacy-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let dirs = [
            root.clone(),
            root.join("hypr"),
            root.join("hypr").join(instance),
        ];
        fs::create_dir_all(&dirs[2]).unwrap();
        for dir in dirs {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Runtime(root)
    }

    /// The request socket, bound through /proc/self/fd so a long TMPDIR still fits in sun_path.
    fn listen(&self, instance: &str) -> UnixListener {
        let handle = fs::File::open(self.0.join("hypr").join(instance)).unwrap();
        let listener =
            UnixListener::bind(format!("/proc/self/fd/{}/.socket.sock", handle.as_raw_fd()))
                .unwrap();
        listener.set_nonblocking(true).unwrap();
        listener
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Answers each connection with the next scripted reply (then "unknown request") and keeps every request.
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

    fn seen(&self) -> usize {
        self.requests.lock().unwrap().len()
    }

    fn finish(mut self) -> Vec<String> {
        self.stop.store(true, Ordering::SeqCst);
        self.thread.take().unwrap().join().unwrap();
        self.requests.lock().unwrap().clone()
    }
}

fn wait_for(what: &str, mut ready: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(5);
    while !ready() {
        assert!(Instant::now() < end, "{what}");
        thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn privacy_mode_sends_what_the_python_sends() {
    let corpus: Json = serde_json::from_str(CORPUS).unwrap();
    let instance = corpus["instance"].as_str().unwrap();
    let glasses = corpus["glasses"].as_str().unwrap();
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 10);
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let exchanges = scenario["exchanges"].as_array().unwrap();
        let script: Vec<(String, String)> = exchanges
            .iter()
            .map(|e| {
                (
                    e["request"].as_str().unwrap().to_owned(),
                    e["reply"].as_str().unwrap().to_owned(),
                )
            })
            .collect();
        let runtime = Runtime::new(name, instance);
        let compositor = Compositor::serve(runtime.listen(instance), script.clone());

        let (tx, notes) = mpsc::channel::<String>();
        let tx = Mutex::new(tx);
        let notify: Notify = Box::new(move |m: &str| {
            let _ = tx.lock().unwrap().send(m.to_owned());
        });
        let watch = Duration::from_secs_f64(scenario["watch"].as_f64().unwrap());
        let display = PrivacyDisplay::new(
            Client::new(instance, &runtime.0).unwrap(),
            glasses,
            scenario["fallback"].as_str(),
            Some(notify),
            DEADLINE,
            watch,
        );
        let mut recovery = Client::new(instance, &runtime.0).unwrap();
        let mut notified = Vec::new();
        for step in scenario["steps"].as_array().unwrap() {
            let what = step["step"].as_str().unwrap();
            match what {
                "press" => {
                    let (done_tx, done) = mpsc::channel();
                    display.request(Some(Box::new(move |ok, detail| {
                        let _ = done_tx.send((ok, detail));
                    })));
                    let (ok, detail) = done
                        .recv_timeout(Duration::from_secs(5))
                        .unwrap_or_else(|_| panic!("{name}: the press completes"));
                    let want = step["done"].as_array().unwrap();
                    assert_eq!(
                        (ok, detail.as_str()),
                        (want[0].as_bool().unwrap(), want[1].as_str().unwrap()),
                        "{name}: the press's completion"
                    );
                    if detail != "Privacy owner closed" {
                        notified.push(
                            notes
                                .recv_timeout(Duration::from_secs(5))
                                .unwrap_or_else(|_| panic!("{name}: the press is told")),
                        );
                    }
                }
                "close" => display.close(None),
                w if w.starts_with("wake") => {
                    let output = w.split_once(':').map_or("DP-1", |(_, n)| n);
                    assert_eq!(
                        wake(&mut recovery, output, DEADLINE),
                        step["returned"].as_bool().unwrap(),
                        "{name}: recovery's wake"
                    );
                }
                // The display something else lit is in the next scripted `monitors` reply.
                w if w.starts_with("external_on:") => {}
                "watch_reblank" => {
                    let n = step["requests"].as_u64().unwrap() as usize;
                    wait_for(&format!("{name}: the watcher blanks it again"), || {
                        compositor.seen() >= n
                    });
                }
                other => panic!("{name}: unknown step {other}"),
            }
        }
        let requests = compositor.finish();
        let want: Vec<&str> = script.iter().map(|(r, _)| r.as_str()).collect();
        assert_eq!(requests, want, "{name}: the requests, in order");
        let want: Vec<&str> = scenario["notifications"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n.as_str().unwrap())
            .collect();
        assert_eq!(notified, want, "{name}: the notifications");
        assert!(notes.try_recv().is_err(), "{name}: nothing more is told");
    }
}
