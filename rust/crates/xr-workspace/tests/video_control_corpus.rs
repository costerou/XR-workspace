//! Replays `rust/corpus/video/control.json` (tests/native_spike/video_control_corpus.py records it from the shipped
//! `video.VideoControl`) against `view_controls::video::Control`: the same steps with the same stand-ins (the view, the
//! probes, the relay, the owners), and each step's record compared: the frames, the descriptors sent with them, the
//! panel's `video` entry, the bind calls, the toasts, what the view was handed, the owners' completions and frame
//! registrations, the saves and the relays.

use std::cell::RefCell;
use std::collections::HashMap;
use std::os::fd::{FromRawFd, OwnedFd};
use std::os::unix::fs::FileTypeExt;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};
use xr_workspace::supervisor::environment::{self, Environment};
use xr_workspace::supervisor::error::Error;
use xr_workspace::supervisor::keybinds;
use xr_workspace::supervisor::quality::{self, Quality};
use xr_workspace::supervisor::video::ProbeInfo;
use xr_workspace::supervisor::web_video::{Relay, RelayFactory};
use xr_workspace::view_controls::Frames;
use xr_workspace::view_controls::video::{self as video, BindFn, Control, Done, Notify, Opener};

fn corpus() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../corpus/video/control.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn marker(name: &str, fields: &[(&str, &str)]) -> Vec<u8> {
    let mut text = format!("{{\"marker\":\"{name}\"");
    for (k, v) in fields {
        text.push_str(&format!(",\"{k}\":\"{v}\""));
    }
    text.push_str("}\n");
    text.into_bytes()
}

/// The corpus's `View`.
struct View {
    clock: f64,
    next_id: u64,
    typing: bool,
    menu_open: bool,
    help_visible: bool,
    keys_open: bool,
    video_active: bool,
    last_input: f64,
    quality: Quality,
    environment: Environment,
    readback: (u64, Option<Value>),
    told: Vec<String>,
    markers: Vec<String>,
    saves: Vec<Value>,
    save_fails: Option<String>,
}

impl keybinds::Host for View {
    fn now(&self) -> f64 {
        self.clock
    }
    fn typing(&self) -> bool {
        self.typing
    }
    fn video_active(&self) -> bool {
        self.video_active
    }
    fn help_visible(&self) -> bool {
        self.help_visible
    }
    fn close_help(&mut self) -> Vec<u8> {
        self.help_visible = false;
        marker("help_closed", &[])
    }
    fn menu_open(&self) -> bool {
        self.menu_open
    }
    fn close_menu(&mut self) -> Vec<u8> {
        self.menu_open = false;
        marker("menu_closed", &[])
    }
    fn keyboard_owned(&self) -> bool {
        false
    }
    fn run_id(&self) -> &str {
        "run1"
    }
    fn session(&self) -> &str {
        "s1"
    }
    fn next_request_id(&mut self) -> u64 {
        self.next_id += 1;
        self.next_id - 1
    }
    fn set_last_input(&mut self, now: f64) {
        self.last_input = now;
    }
    fn binds_reinstalled(&mut self, _: bool, _: bool, _: bool) {}
}

impl video::Host for View {
    fn video_quality(&self) -> Quality {
        self.quality
    }
    fn set_video_quality(&mut self, quality: Quality) {
        self.quality = quality;
    }
    fn save_settings(&mut self) -> Result<(), String> {
        if let Some(e) = &self.save_fails {
            return Err(e.clone());
        }
        self.saves
            .push(json!({"video_quality": quality::to_value(self.quality)}));
        Ok(())
    }
    fn environment(&self) -> Value {
        self.environment.to_json()
    }
    fn set_environment(&mut self, env: Environment) -> Vec<u8> {
        self.environment = env;
        marker("environment", &[("background", env.background)])
    }
    fn set_video_active(&mut self, on: bool) {
        self.video_active = on;
    }
    fn keys_open(&self) -> bool {
        self.keys_open
    }
    fn close_keys(&mut self) -> Vec<u8> {
        self.keys_open = false;
        marker("keys_closed", &[])
    }
    fn tell(&mut self, text: &str) {
        self.told.push(text.into());
    }
    fn owner_readback(&self) -> (u64, Option<Value>) {
        self.readback.clone()
    }
}

type Log = Rc<RefCell<Vec<Value>>>;

struct Owner {
    name: String,
    log: Log,
    retain: Rc<RefCell<HashMap<String, Value>>>,
    legacy: bool,
}

impl Done for Owner {
    fn complete(&self, success: bool, detail: &str) {
        self.log
            .borrow_mut()
            .push(json!([self.name, success, detail]));
    }
    fn retain_frames(&self, frames: &[u8]) -> Option<bool> {
        if self.legacy {
            return None;
        }
        self.log.borrow_mut().push(json!([
            self.name,
            "retain",
            String::from_utf8_lossy(frames)
        ]));
        let mode = self
            .retain
            .borrow()
            .get(&self.name)
            .cloned()
            .unwrap_or(json!(true));
        Some(match mode {
            Value::String(m) if m == "preflight" => frames.is_empty(),
            Value::Bool(b) => b,
            _ => true,
        })
    }
}

#[derive(Default)]
struct RelayLog {
    argv: Vec<String>,
    stopped: bool,
    why: Option<String>,
}

struct FakeRelay {
    log: Arc<Mutex<Vec<RelayLog>>>,
    index: usize,
}

impl Relay for FakeRelay {
    fn start(&mut self) -> std::io::Result<OwnedFd> {
        let mut fds = [0; 2];
        // SAFETY: a valid out array of two descriptors.
        if unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) } < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: both were just opened and are owned here.
        let (r, w) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
        drop(w);
        Ok(r)
    }
    fn stop(&mut self) {
        self.log.lock().unwrap()[self.index].stopped = true;
    }
    fn failure(&self) -> Option<String> {
        self.log.lock().unwrap()[self.index].why.clone()
    }
}

fn probe_info(v: &Value) -> Option<ProbeInfo> {
    if v.is_null() {
        return None;
    }
    assert!(v["stereo"].is_null());
    Some(ProbeInfo {
        width: v["width"].as_i64().unwrap(),
        height: v["height"].as_i64().unwrap(),
        codec: v["codec"].as_str().unwrap().into(),
        fps: v["fps"].as_f64(),
        duration: v["duration"].as_f64(),
        projection: v["projection"].as_str().map(Into::into),
        coverage: v["coverage"].as_f64(),
        stereo: None,
        container: None,
    })
}

fn plain(v: &xr_wire::json::Value) -> Value {
    use xr_wire::json::Value as V;
    match v {
        V::Null => Value::Null,
        V::Bool(b) => Value::Bool(*b),
        V::Number(n) => Value::Number(n.clone()),
        V::String(s) => Value::String(s.clone()),
        V::Array(a) => Value::Array(a.iter().map(plain).collect()),
        V::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), plain(v))).collect()),
    }
}

fn fd_kind(fd: &OwnedFd) -> &'static str {
    let file = std::fs::File::from(fd.try_clone().unwrap());
    if file.metadata().unwrap().file_type().is_fifo() {
        "pipe"
    } else {
        "file"
    }
}

struct Replay {
    tmp: PathBuf,
    view: View,
    control: Control,
    binds: Arc<Mutex<Vec<bool>>>,
    notified: Arc<Mutex<Vec<String>>>,
    relays: Arc<Mutex<Vec<RelayLog>>>,
    done: Log,
    retain: Rc<RefCell<HashMap<String, Value>>>,
    owners: HashMap<String, Rc<dyn Done>>,
}

impl Replay {
    fn new(tmp: PathBuf, probe: &Value, web_info: &Value) -> Replay {
        let binds = Arc::new(Mutex::new(Vec::new()));
        let notified = Arc::new(Mutex::new(Vec::new()));
        let relays: Arc<Mutex<Vec<RelayLog>>> = Arc::new(Mutex::new(Vec::new()));
        let file_info = probe_info(probe);
        let web = probe_info(web_info);
        let log = relays.clone();
        let opener = Opener {
            threads: false,
            probe: Arc::new(move |_| Ok(file_info.clone())),
            web_probe: Arc::new(move |url, _, _| {
                if url.contains("forbidden") {
                    return Err(Error::rejected("the site refused the stream (HTTP 403)"));
                }
                Ok(web.clone())
            }),
            resolve: Arc::new(|_| Err(Error::rejected("no page here"))),
            relays: Arc::new(move || -> RelayFactory {
                let log = log.clone();
                Box::new(move |argv| {
                    let mut l = log.lock().unwrap();
                    l.push(RelayLog {
                        argv,
                        ..RelayLog::default()
                    });
                    Box::new(FakeRelay {
                        log: log.clone(),
                        index: l.len() - 1,
                    })
                })
            }),
            allow_local: false,
        };
        let b = binds.clone();
        let bind_fn: BindFn = Box::new(move |on| {
            b.lock().unwrap().push(on);
            Ok(())
        });
        let n = notified.clone();
        let notify: Notify = Arc::new(move |t: &str| n.lock().unwrap().push(t.into()));
        Replay {
            tmp,
            view: View {
                clock: 100.0,
                next_id: 2,
                typing: false,
                menu_open: false,
                help_visible: false,
                keys_open: false,
                video_active: false,
                last_input: 0.0,
                quality: Quality::default(),
                environment: environment::DEFAULTS,
                readback: (0, None),
                told: Vec::new(),
                markers: Vec::new(),
                saves: Vec::new(),
                save_fails: None,
            },
            control: Control::new(opener, Some(bind_fn), Some(notify)),
            binds,
            notified,
            relays,
            done: Rc::new(RefCell::new(Vec::new())),
            retain: Rc::new(RefCell::new(HashMap::new())),
            owners: HashMap::new(),
        }
    }

    fn owner(&mut self, name: &str) -> Rc<dyn Done> {
        let legacy = self.retain.borrow().get(name) == Some(&json!("legacy"));
        let (log, retain) = (self.done.clone(), self.retain.clone());
        self.owners
            .entry(name.into())
            .or_insert_with(|| {
                Rc::new(Owner {
                    name: name.into(),
                    log,
                    retain,
                    legacy,
                })
            })
            .clone()
    }

    fn untmp(&self, text: &str) -> String {
        text.replace(self.tmp.to_str().unwrap(), "{tmp}")
    }

    fn step(&mut self, step: &Value) -> Value {
        let mut frames = Frames::default();
        if let Some(op) = step["op"].as_str() {
            let mut payload = step["payload"].clone();
            if let Some(p) = payload.get("path").and_then(Value::as_str) {
                payload["path"] = json!(p.replace("{tmp}", self.tmp.to_str().unwrap()));
            }
            let done = step["owner"].as_str().map(|n| self.owner(n));
            match self.control.on_message(&mut self.view, op, &payload, done) {
                Some(out) => frames.bytes = out,
                None => {
                    self.view.markers.push(op.into());
                    if op == "typing_toggle" {
                        self.view.typing = !self.view.typing;
                    }
                    frames.bytes = marker("view", &[("op", op)]);
                    self.control.after_view(&mut self.view, op);
                }
            }
        } else if let Some(dt) = step["poll"].as_f64() {
            self.view.clock += dt;
            let now = self.view.clock;
            self.control.poll(&mut self.view, now, &mut frames);
        } else if let Some(status) = step.get("status") {
            self.control
                .set_measured(status.get("video").and_then(Value::as_str));
        } else if let Some(fields) = step["view"].as_object() {
            for (k, v) in fields {
                let v = v.as_bool().unwrap();
                match k.as_str() {
                    "typing" => self.view.typing = v,
                    "menu_open" => self.view.menu_open = v,
                    "help_visible" => self.view.help_visible = v,
                    "keys_open" => self.view.keys_open = v,
                    _ => panic!("{k}"),
                }
            }
        } else if let Some(readback) = step.get("readback") {
            self.view.readback = (self.view.readback.0 + 1, Some(readback.clone()));
        } else if step.get("restart").is_some() {
            self.control.renderer_restarted(&mut self.view);
            self.view.markers.push("renderer_restarted".into());
        } else if step.get("reload").is_some() {
            self.control.config_reloaded(&mut self.view);
        } else if step.get("close").is_some() {
            self.control.close();
        } else if let Some(why) = step["relay_fail"].as_str() {
            self.relays.lock().unwrap().last_mut().unwrap().why = Some(why.into());
        } else if let Some(q) = step.get("quality") {
            let done = step["owner"].as_str().map(|n| self.owner(n));
            frames.bytes = self.control.set_quality(&mut self.view, q, done);
        } else if let Some(fails) = step.get("save_fails") {
            self.view.save_fails = fails.as_str().map(Into::into);
        } else if let Some(retain) = step["retain"].as_object() {
            self.retain
                .borrow_mut()
                .extend(retain.iter().map(|(k, v)| (k.clone(), v.clone())));
        } else {
            panic!("{step}");
        }
        let fds: Vec<&str> = frames.fds.iter().map(fd_kind).collect();
        let snapshot: Value = serde_json::from_str(
            &self.untmp(&plain(&self.control.snapshot(&self.view)).to_string()),
        )
        .unwrap();
        json!({
            "out": String::from_utf8(frames.bytes).unwrap(),
            "fds": fds,
            "snapshot": snapshot,
            "binds": *self.binds.lock().unwrap(),
            "notified": self.notified.lock().unwrap().iter().map(|t| self.untmp(t)).collect::<Vec<_>>(),
            "told": self.view.told,
            "markers": self.view.markers,
            "done": *self.done.borrow(),
            "saves": self.view.saves,
            "video_active": self.view.video_active,
            "last_input": self.view.last_input,
            "relays": self.relays.lock().unwrap().iter().map(|r| json!({"argv": r.argv, "stopped": r.stopped})).collect::<Vec<_>>(),
        })
    }
}

fn temp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("xr-video-corpus-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    for f in ["Beach_360_TB.mp4", "Trip VR180_LR.mkv"] {
        std::fs::write(dir.join(f), [b'v'; 100]).unwrap();
    }
    std::fs::write(dir.join("empty.mp4"), b"").unwrap();
    dir
}

#[test]
fn every_session_replays_step_for_step() {
    let data = corpus();
    let sessions = data.as_object().unwrap();
    assert!(sessions.len() >= 20, "{} sessions", sessions.len());
    for (name, session) in sessions {
        let tmp = temp_dir(name);
        let mut replay = Replay::new(tmp.clone(), &session["probe"], &session["web_info"]);
        let steps = session["steps"].as_array().unwrap();
        let records = session["records"].as_array().unwrap();
        assert_eq!(steps.len(), records.len(), "{name}");
        for (i, (step, want)) in steps.iter().zip(records).enumerate() {
            let got = replay.step(step);
            for key in want.as_object().unwrap().keys() {
                assert_eq!(got[key], want[key], "{name} step {i} {step}: {key}");
            }
        }
        replay.control.close();
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
