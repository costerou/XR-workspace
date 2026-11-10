//! The Rust controller (state machine and socket server) behind a socket path, for the panel's
//! client test: `tests/xr_controller/test_rust_panel_client.py` runs the unchanged QML script of
//! `omarchy-plugin/cody.viture-workspace/tests/test_controller_client.py` against it.
//!
//! `controller_socket SOCKET`: serves until stdin closes. The host is that test's offline one: a
//! fixed observation (`beast` on `DP-2`), no units, no spawns, the default settings. Every handled
//! request is printed to stdout as `request <json>`; on exit, `state <state>` and `spawns <n>`.
//! After the second `begin_start` is handled, before its reply is written, every client is cut,
//! the glasses unplugged and the controller ticked, as the Python test does.

use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use xr_wire::controller::{Message, Op, Request};
use xr_wire::json::{Style, Value, encode, parse_strict};
use xr_workspace::controller::{Controller, ControllerHost, set};
use xr_workspace::controller_server::{Server, Service};

fn j(text: &str) -> Value {
    parse_strict(text.as_bytes()).unwrap()
}

struct Host {
    observed: Value,
    tokens: u64,
    runs: u64,
    spawns: usize,
    started: std::time::Instant,
}

impl ControllerHost for Host {
    fn now(&mut self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }
    fn probe(&mut self) -> Result<Value, String> {
        Ok(self.observed.clone())
    }
    fn alive(&mut self, _run_id: &str) -> Result<bool, String> {
        panic!("unexpected unit lookup")
    }
    fn control(
        &mut self,
        _: &str,
        _: &Value,
        _: Option<&str>,
        _: Option<&Value>,
    ) -> Result<Option<Value>, String> {
        panic!("unexpected run control")
    }
    fn stop(&mut self, _run_id: &str) -> Result<(), String> {
        panic!("unexpected stop")
    }
    fn spawn(&mut self, _: &str, _: &str, _: Option<&str>, _: &[String]) -> Result<(), String> {
        self.spawns += 1;
        Ok(())
    }
    fn read_consent(&mut self, _run_id: &str) -> Result<Value, String> {
        Err("no consent".into())
    }
    fn build_consent(&mut self, scope: &Value) -> Result<Value, String> {
        Ok(scope.clone())
    }
    fn write_consent(&mut self, _consent: &Value) -> Result<String, String> {
        Err("unexpected consent".into())
    }
    fn state_path(&self, name: &str) -> String {
        format!("/offline/{name}")
    }
    fn read_result(&mut self, _run_id: &str) -> Result<Option<Value>, String> {
        Ok(None)
    }
    fn recovery_reason(&mut self, _run_id: &str) -> Option<String> {
        None
    }
    fn token(&mut self) -> String {
        self.tokens += 1;
        format!("{:032x}", self.tokens)
    }
    fn load_settings(&mut self, _model: &str) -> Value {
        j(r#"{"spacing":100,"prediction":true,"mode":"follow","fallback":"auto"}"#)
    }
    fn check_settings(&mut self, settings: &Value) -> Result<Value, String> {
        Ok(settings.clone())
    }
    fn save_settings(&mut self, _model: &str, _settings: &Value) -> Result<(), String> {
        Ok(())
    }
    fn default_tracking_driver(&mut self, _model: &str) -> Value {
        Value::String("luma".into())
    }
    fn pick_fallback(&mut self, monitors: &Value, output: &Value, _preferred: &Value) -> Value {
        let Value::Array(monitors) = monitors else {
            return Value::Null;
        };
        monitors
            .iter()
            .filter_map(|m| m.get("name"))
            .find(|name| *name != output)
            .cloned()
            .unwrap_or(Value::Null)
    }
    fn ownership_plugin_loaded(&mut self, _observed: &Value) -> bool {
        false
    }
    fn start_scope(
        &mut self,
        observed: &Value,
        _profile: &Value,
        fallback: &Value,
    ) -> Result<Value, String> {
        self.runs += 1;
        let mut scope = j(r#"{"tracking":true,"mode_switch":true}"#);
        for key in ["model", "output", "instance"] {
            set(
                &mut scope,
                key,
                observed.get(key).cloned().unwrap_or(Value::Null),
            );
        }
        set(&mut scope, "fallback", fallback.clone());
        set(
            &mut scope,
            "run_id",
            Value::String(format!("run-{}", self.runs)),
        );
        Ok(scope)
    }
    fn tracking_wait(&mut self, _journal: &Value) -> i64 {
        30
    }
    fn tracking_in_use(&mut self, _observed: &Value, _running: Option<&Value>) -> Value {
        Value::Null
    }
    fn pro2_snapshot(&mut self, _observed: &Value) -> Option<Value> {
        None
    }
    fn save_projection(&mut self, _projection: Option<&Value>) -> Result<(), String> {
        Ok(())
    }
    fn validate_placements(&mut self, placements: &Value) -> Result<Value, String> {
        Ok(placements.clone())
    }
    fn validate_video(&mut self, request: &Value) -> Result<Value, String> {
        Ok(request.clone())
    }
    fn incident(&mut self) -> Value {
        Value::Null
    }
}

/// The controller, printing every request it answers.
struct Logged {
    controller: Controller<Host>,
    begin_starts: usize,
}

impl Service for Logged {
    fn handle(&mut self, request: &Request) -> Message {
        println!(
            "request {}",
            encode(&request.message, Style::COMPACT_UNICODE)
        );
        if request.op() == Some(Op::BeginStart) {
            self.begin_starts += 1;
        }
        self.controller.handle(request)
    }
    fn tick(&mut self) {
        self.controller.tick()
    }
    fn take_changed(&mut self) -> Option<Value> {
        self.controller.take_changed()
    }
}

fn main() {
    let path = PathBuf::from(
        std::env::args()
            .nth(1)
            .expect("usage: controller_socket SOCKET"),
    );
    let installed = r#"{"plugin":{"path":"/fake/plugin","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"renderer":{"path":"/fake/renderer","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}"#;
    let host = Host {
        observed: j(&format!(
            r#"{{"model":"beast","output":"DP-2","reason":null,"instance":"offline","monitors":[{{"name":"DP-2"}},{{"name":"DP-1"}}],"journals":[],"plugins":[],"installed":{installed}}}"#
        )),
        tokens: 0,
        runs: 0,
        spawns: 0,
        started: std::time::Instant::now(),
    };
    let mut service = Logged {
        controller: Controller::new(host),
        begin_starts: 0,
    };
    let mut server = Server::bind(&path, &mut service).expect("bind");
    let stop = Arc::new(AtomicBool::new(false));
    {
        let stop = stop.clone();
        std::thread::spawn(move || {
            let _ = std::io::stdin().read_to_end(&mut Vec::new());
            stop.store(true, Ordering::SeqCst);
        });
    }
    println!("ready");
    let _ = std::io::stdout().flush();
    let mut dropped = false;
    while !stop.load(Ordering::SeqCst) {
        server
            .step(&mut service, Duration::from_millis(10))
            .expect("step");
        if !dropped && service.begin_starts == 2 {
            server.drop_all();
            set(&mut service.controller.host.observed, "model", Value::Null);
            set(&mut service.controller.host.observed, "output", Value::Null);
            service.controller.tick();
            dropped = true;
        }
        let _ = std::io::stdout().flush();
    }
    drop(server);
    println!("state {}", service.controller.state);
    println!("spawns {}", service.controller.host.spawns);
}
