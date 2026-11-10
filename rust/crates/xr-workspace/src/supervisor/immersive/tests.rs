//! Ports of tests/native_spike/test_immersive.py and test_immersive_strength.py for the parts that live here: the
//! setting, the frame (over the shared fixture), the looks, the run's worker on fake adapters, the engine process, the
//! memfd, and the real Python depth engine over a memfd made here.

use super::*;
use crate::supervisor::fixture;
use crate::supervisor::process::{Invocation, run as run_program, tool_env};
use crate::testdir::TestDir;
use std::os::unix::fs::PermissionsExt;
use std::sync::atomic::{AtomicBool, Ordering};

const NAMES: [&str; 3] = ["XR-1", "XR-2", "XR-3"];
const WAIT: Duration = Duration::from_secs(5);

fn full(every: bool, desktops: &[(&str, bool)]) -> Setting {
    Setting { global: every, desktops: desktops.iter().map(|(n, v)| (n.to_string(), *v)).collect(), ..Setting::default() }
}

fn off() -> Setting {
    full(false, &[])
}

fn all() -> Setting {
    full(true, &[])
}

fn names(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

fn wait_for(what: &str, mut test: impl FnMut() -> bool) {
    let deadline = Instant::now() + WAIT;
    while Instant::now() < deadline {
        if test() {
            return;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    panic!("timed out: {what}");
}

// --- the setting --------------------------------------------------------------------------------------------------

#[test]
fn off_by_default_and_anything_invalid_reads_as_off() {
    assert_eq!(Setting::default(), off());
    assert_eq!(off().to_value(), json!({"global": false, "desktops": {}, "preset": "desktop", "strength": 100, "convergence": 92}));
    assert!(on_names(&off(), &NAMES).is_empty());
    for bad in [json!(null), json!(3), json!([]), json!("on"), json!({"global": 1}), json!({"desktops": "XR-1"}), json!({"global": null, "desktops": []})] {
        assert_eq!(clean(&bad), off(), "{bad}");
    }
    let mut noisy = all().to_value();
    noisy["desktops"] = json!({"XR-9": false, "HDMI-A-1": false, "3": false, "XR-2": "no"});
    assert_eq!(clean(&noisy), all());
}

#[test]
fn an_exception_that_says_nothing_is_dropped() {
    assert_eq!(clean(&full(true, &[("XR-1", true), ("XR-2", false)]).to_value()), full(true, &[("XR-2", false)]));
    let mut v = all().to_value();
    v["desktops"] = json!({"XR-1": true});
    assert!(!is_valid(&v));
    assert_eq!(validate(&json!({"global": false})), Err(Error::Invalid("invalid immersive")));
    let mut extra = off().to_value();
    extra["extra"] = json!(1);
    assert!(validate(&extra).is_err());
    assert!(is_valid(&off().to_value()));
    // Strict JSON: Python's 0 == False and 100.0 == 100 are not valid settings here.
    let mut zero = off().to_value();
    zero["global"] = json!(0);
    assert!(!is_valid(&zero));
    let mut float = off().to_value();
    float["strength"] = json!(100.0);
    assert!(!is_valid(&float));
}

#[test]
fn one_desktop_and_all_of_them() {
    let one = toggled_desktop(&off(), "XR-2");
    assert_eq!(on_names(&one, &NAMES), ["XR-2"]);
    assert_eq!(toggled_desktop(&one, "XR-2"), off()); // no leftover exception
    assert_eq!(toggled_all(&off(), &NAMES), all());
    let most = toggled_desktop(&all(), "XR-1");
    assert_eq!(on_names(&most, &["XR-1", "XR-2", "XR-3", "XR-4"]), ["XR-2", "XR-3", "XR-4"]);
    // The toggle for all turns anything on off first.
    assert_eq!(toggled_all(&one, &NAMES), off());
    assert_eq!(toggled_all(&all(), &NAMES), off());
    assert_eq!(toggled_all(&most, &NAMES), off());
    // The tail lights everything unless everything is lit.
    assert_eq!(toggled_every(&off(), &NAMES), all());
    assert_eq!(toggled_every(&one, &NAMES), all());
    assert_eq!(toggled_every(&most, &NAMES), all());
    assert_eq!(toggled_every(&all(), &NAMES), off());
    // Only the desktops that exist count.
    let only_gone = toggled_desktop(&off(), "XR-4");
    assert!(on_names(&only_gone, &NAMES).is_empty());
    assert_eq!(toggled_all(&only_gone, &NAMES), all());
    assert_eq!(immersive(&json!({"immersive": all().to_value()})), all());
    assert_eq!(immersive(&json!({})), off());
}

#[test]
fn per_desktop_strengths() {
    let mut v = off().to_value();
    v["strengths"] = json!({"XR-2": 63});
    assert!(is_valid(&v));
    let s = clean(&v);
    assert_eq!(s.to_value(), v);
    assert_eq!(wire_params(&s).strengths, Some([100, 63, 100, 100]));
    assert_eq!(toggled_desktop(&s, "XR-1").strengths, s.strengths);
    for bad in [json!(-1), json!(201), json!(true), json!("60")] {
        v["strengths"] = json!({"XR-2": bad});
        assert!(!is_valid(&v), "{bad}");
    }
}

// --- the looks ----------------------------------------------------------------------------------------------------

#[test]
fn the_presets_are_the_comfort_studys() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tools/stereo_eval/presets.json");
    let study: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let study_names: Vec<&String> = study.as_object().unwrap().keys().filter(|k| *k != "_comment" && *k != "naive").collect();
    assert_eq!(study_names.len(), PRESETS.len());
    for p in &PRESETS {
        let s = &study[p.name];
        assert_eq!(
            (json!(p.near), json!(p.far), json!(p.convergence), json!(p.range_ms)),
            (s["near_arcmin"].clone(), s["far_arcmin"].clone(), s["anchor_pct"].clone(), s["range_ms"].clone()),
            "{}",
            p.name
        );
        let pinned = js::truthy(&s["text_pin"]);
        assert_eq!(p.text, if pinned { 0 } else { 100 }, "{}", p.name);
        // The renderer's wire limits.
        let w = wire_params(&with_preset(&off(), p.name).unwrap());
        assert!(w.near <= 90 && w.far <= 60 && (0..=100).contains(&w.text) && w.range_ms <= 10000, "{}", p.name);
    }
}

#[test]
fn a_preset_brings_its_plane_and_bad_values_fall_back_one_at_a_time() {
    let p = wire_params(&off());
    assert_eq!(p.to_json(), r#"{"strength":100,"convergence":92,"near":4,"far":14,"text":0,"range_ms":3000}"#);
    let video = with_preset(&all(), "video").unwrap();
    assert_eq!((video.preset.name, video.convergence, video.global), ("video", 70, true));
    assert_eq!(with_preset(&all(), "cinema"), Err(Error::Invalid("invalid immersive preset")));
    let look = Setting { preset: &PRESETS[2], strength: 150, convergence: 60, ..off() };
    for changed in [toggled_all(&look, &NAMES), toggled_every(&look, &NAMES), toggled_desktop(&look, "XR-2")] {
        assert_eq!((changed.preset.name, changed.strength, changed.convergence), ("games", 150, 60));
    }
    assert_eq!(clean(&json!({"global": true, "preset": "cinema", "strength": 900, "convergence": -1})), all());
    assert_eq!(clean(&json!({"strength": true})).strength, 100);
    assert_eq!(clean(&json!({"convergence": 50.5})).convergence, 92);
    assert_eq!(clean(&json!({"preset": "video"})).convergence, 70);
    assert_eq!(immersive(&json!({"immersive": {"global": true, "desktops": {"XR-1": false}}})), full(true, &[("XR-1", false)]));
    let strong = Setting { strength: 150, convergence: 70, preset: &PRESETS[1], ..all() };
    assert_eq!(
        wire_params(&strong).to_json(),
        r#"{"strength":150,"convergence":70,"near":14,"far":36,"text":100,"range_ms":1000}"#
    );
}

// --- the frame ----------------------------------------------------------------------------------------------------

#[test]
fn the_fixture_is_what_the_supervisor_sends() {
    let cases = fixture("immersive_payloads.json");
    let valid = cases["valid"].as_array().unwrap();
    assert!(!valid.is_empty());
    for case in valid {
        let flags: Vec<bool> = case["flags"].as_array().unwrap().iter().map(|v| v.as_bool().unwrap()).collect();
        let frame = Frame {
            flags: &flags,
            stereo: case["stereo"].as_bool().unwrap(),
            hover: case["hover"].as_i64().unwrap(),
            tail: case["tail"].as_bool().unwrap(),
            every: case["every"].as_bool().unwrap(),
            params: None,
        };
        let line = renderer_frame("run1", "s1", 7, &frame);
        let sent: Value = serde_json::from_slice(&line[..line.len() - 1]).unwrap();
        assert_eq!(line.last(), Some(&b'\n'));
        assert_eq!((sent["op"].as_str(), sent["request_id"].as_i64()), (Some("immersive"), Some(7)));
        assert_eq!(sent["payload"], case["payload"], "{case}");
    }
    let five = [true; 5];
    let payload = renderer_payload(&Frame { flags: &five, stereo: true, hover: 0, tail: false, every: true, params: None });
    assert!(payload.starts_with(r#"{"desktops":[true,true,true,true],"global":true,"#), "{payload}");
}

// --- the run's worker ---------------------------------------------------------------------------------------------

type Log = Arc<Mutex<Vec<String>>>;

fn entries(log: &Log) -> Vec<String> {
    log.lock().unwrap().clone()
}

#[derive(Clone, Copy, PartialEq)]
enum Fail {
    No,
    Error,
    Panic,
}

struct FakeEngine {
    log: Log,
    fail: Fail,
    gate: Option<Arc<AtomicBool>>,
    up: AtomicBool,
    /// Called inside stop, before it records itself.
    on_stop: Option<Box<dyn Fn() + Send + Sync>>,
}

impl FakeEngine {
    fn new(log: &Log) -> FakeEngine {
        FakeEngine { log: log.clone(), fail: Fail::No, gate: None, up: AtomicBool::new(false), on_stop: None }
    }
}

impl Engine for FakeEngine {
    fn start(&self, desktops: &[String], cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, EngineError> {
        self.log.lock().unwrap().push(format!("start {}", desktops.join(",")));
        while let Some(gate) = &self.gate {
            if gate.load(Ordering::SeqCst) {
                break;
            }
            if cancelled() {
                return Ok(false);
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        match self.fail {
            Fail::Error => return Err(EngineError("it is not installed".into())),
            Fail::Panic => panic!("bug"),
            Fail::No => {}
        }
        self.up.store(true, Ordering::SeqCst);
        Ok(true)
    }

    fn set_desktops(&self, desktops: &[String]) -> std::result::Result<(), EngineError> {
        self.log.lock().unwrap().push(format!("set {}", desktops.join(",")));
        Ok(())
    }

    fn alive(&self) -> bool {
        self.up.load(Ordering::SeqCst)
    }

    fn stop(&self) {
        if let Some(hook) = &self.on_stop {
            hook();
        }
        self.log.lock().unwrap().push("stop".into());
        self.up.store(false, Ordering::SeqCst);
    }
}

struct FakeSbs {
    log: Log,
    enter: Fail,
    leave_fails: bool,
    /// enter blocks until set.
    stuck: Option<Arc<AtomicBool>>,
}

impl FakeSbs {
    fn new(log: &Log) -> FakeSbs {
        FakeSbs { log: log.clone(), enter: Fail::No, leave_fails: false, stuck: None }
    }
}

impl Sbs for FakeSbs {
    fn enter(&self, _cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, SbsError> {
        if let Some(gate) = &self.stuck {
            let deadline = Instant::now() + WAIT;
            while !gate.load(Ordering::SeqCst) && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(1));
            }
            return Ok(true);
        }
        self.log.lock().unwrap().push("enter".into());
        match self.enter {
            Fail::Error => Err(SbsError("no reply".into())),
            Fail::Panic => panic!("bug"),
            Fail::No => Ok(true),
        }
    }

    fn leave(&self) -> std::result::Result<(), SbsError> {
        self.log.lock().unwrap().push("leave".into());
        if self.leave_fails { Err(SbsError("no reply".into())) } else { Ok(()) }
    }
}

fn run_of(engine: FakeEngine, sbs: FakeSbs) -> (ImmersiveRun, Arc<FakeEngine>) {
    let engine = Arc::new(engine);
    (ImmersiveRun::new(engine.clone(), Arc::new(sbs)), engine)
}

fn simple() -> (ImmersiveRun, Arc<FakeEngine>, Log) {
    let log = Log::default();
    let (run, engine) = run_of(FakeEngine::new(&log), FakeSbs::new(&log));
    (run, engine, log)
}

fn events(run: &ImmersiveRun, n: usize) -> Vec<Event> {
    let mut got = Vec::new();
    wait_for(&format!("{n} events"), || {
        got.extend(run.take_events());
        got.len() >= n
    });
    got
}

fn failed(kind: &'static str, why: &str) -> Event {
    Event::Failed { kind, why: why.into() }
}

#[test]
fn nothing_runs_until_something_is_asked_for() {
    let (run, _, log) = simple();
    run.request(&[], None, None);
    assert!(!run.has_worker());
    run.close(CLOSE_WAIT);
    assert!(entries(&log).is_empty());
}

#[test]
fn up_is_the_engine_then_the_glasses_and_down_the_reverse() {
    let (run, _, log) = simple();
    run.request(&names(&["XR-1"]), None, None);
    assert_eq!(events(&run, 1), [Event::Active(names(&["XR-1"]))]);
    assert_eq!(entries(&log), ["start XR-1", "enter"]);
    assert_eq!(run.phase(), "on");
    log.lock().unwrap().clear();
    run.request(&names(&["XR-1", "XR-3"]), None, None);
    assert_eq!(events(&run, 1), [Event::Active(names(&["XR-1", "XR-3"]))]);
    assert_eq!(entries(&log), ["set XR-1,XR-3"]); // the engine and the mode stay
    log.lock().unwrap().clear();
    run.request(&[], None, None);
    assert_eq!(events(&run, 1), [Event::Off]);
    assert_eq!(entries(&log), ["leave", "stop"]);
    assert_eq!(run.phase(), "off");
}

#[test]
fn failures_fall_back_from_every_point() {
    // An engine that will not start never touches the glasses.
    let log = Log::default();
    let (run, _) = run_of(FakeEngine { fail: Fail::Error, ..FakeEngine::new(&log) }, FakeSbs::new(&log));
    run.request(&names(&["XR-1"]), None, None);
    assert_eq!(events(&run, 1), [failed("engine", "it is not installed")]);
    assert_eq!(entries(&log), ["start XR-1", "stop"]);
    assert_eq!(run.error(), "engine: it is not installed");

    // Glasses that will not switch stop the engine.
    let log = Log::default();
    let (run, engine) = run_of(FakeEngine::new(&log), FakeSbs { enter: Fail::Error, ..FakeSbs::new(&log) });
    run.request(&names(&["XR-1"]), None, None);
    assert_eq!(events(&run, 1), [failed("sbs", "no reply")]);
    let got = entries(&log);
    assert_eq!(&got[..2], ["start XR-1", "enter"]);
    assert_eq!(got.last().unwrap(), "stop");
    assert!(!engine.alive());

    // An engine that dies takes the glasses out of 3D.
    let (run, engine, log) = simple();
    run.request(&names(&["XR-1"]), None, None);
    events(&run, 1);
    log.lock().unwrap().clear();
    engine.up.store(false, Ordering::SeqCst);
    assert_eq!(events(&run, 1), [failed("died", "it quit")]);
    assert_eq!(entries(&log), ["leave", "stop"]);

    // A refused leave still stops the engine, and says so.
    let log = Log::default();
    let (run, engine) = run_of(FakeEngine::new(&log), FakeSbs { leave_fails: true, ..FakeSbs::new(&log) });
    run.request(&names(&["XR-1"]), None, None);
    events(&run, 1);
    run.request(&[], None, None);
    assert_eq!(events(&run, 2), [Event::Leave("no reply".into()), Event::Off]);
    assert!(!engine.alive());
}

#[test]
fn a_broken_adapter_is_a_failure_not_a_dead_worker() {
    let log = Log::default();
    let (run, _) = run_of(FakeEngine { fail: Fail::Panic, ..FakeEngine::new(&log) }, FakeSbs::new(&log));
    run.request(&names(&["XR-1"]), None, None);
    assert_eq!(events(&run, 1), [failed("engine", INTERNAL)]);
    assert!(run.worker_running());
    let log = Log::default();
    let (run, _) = run_of(FakeEngine::new(&log), FakeSbs { enter: Fail::Panic, ..FakeSbs::new(&log) });
    run.request(&names(&["XR-1"]), None, None);
    assert_eq!(events(&run, 1), [failed("sbs", INTERNAL)]);
    assert!(run.worker_running());
}

#[test]
fn a_failure_latches_until_the_loop_takes_it_then_the_next_press_retries() {
    let log = Log::default();
    let (stopped, release) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicBool::new(false)));
    let (s, r) = (stopped.clone(), release.clone());
    let engine = FakeEngine {
        fail: Fail::Error,
        on_stop: Some(Box::new(move || {
            s.store(true, Ordering::SeqCst);
            let deadline = Instant::now() + Duration::from_secs(2);
            while !r.load(Ordering::SeqCst) && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(1));
            }
        })),
        ..FakeEngine::new(&log)
    };
    let (run, _) = run_of(engine, FakeSbs::new(&log));
    run.request(&names(&["XR-1"]), None, None);
    wait_for("the failure's cleanup", || stopped.load(Ordering::SeqCst));
    run.request(&names(&["XR-1"]), None, None); // the loop has not taken the failure yet
    release.store(true, Ordering::SeqCst);
    assert_eq!(events(&run, 1), [failed("engine", "it is not installed")]);
    std::thread::sleep(Duration::from_millis(30));
    assert_eq!(run.starts(), 1);
    assert!(run.take_events().is_empty());
    // Still latched for a press until an explicit off.
    run.request(&names(&["XR-1"]), None, None);
    std::thread::sleep(Duration::from_millis(30));
    assert_eq!(run.starts(), 1);
    run.request(&[], None, None);
    run.request(&names(&["XR-1"]), None, None);
    wait_for("the retry", || run.starts() == 2);
}

#[test]
fn turning_it_off_while_it_loads_ends_the_wait() {
    let log = Log::default();
    let gate = Arc::new(AtomicBool::new(false));
    let (run, _) = run_of(FakeEngine { gate: Some(gate.clone()), ..FakeEngine::new(&log) }, FakeSbs::new(&log));
    run.request(&names(&["XR-1"]), None, None);
    wait_for("the start", || entries(&log) == ["start XR-1"]);
    run.request(&[], None, None);
    wait_for("the load to end", || run.phase() == "off");
    std::thread::sleep(Duration::from_millis(50));
    assert!(!entries(&log).contains(&"enter".to_string()));
    assert!(run.current().is_empty());
}

#[test]
fn a_reload_says_sbs_again_only_if_it_is_on() {
    let (run, _, log) = simple();
    run.reassert();
    run.request(&names(&["XR-1"]), None, None);
    events(&run, 1);
    log.lock().unwrap().clear();
    run.reassert();
    wait_for("enter again", || entries(&log) == ["enter"]);
}

#[test]
fn the_end_of_a_run_brings_it_all_down() {
    let (run, _, log) = simple();
    run.request(&names(&["XR-1"]), None, None);
    events(&run, 1);
    log.lock().unwrap().clear();
    run.close(CLOSE_WAIT);
    assert_eq!(entries(&log), ["leave", "stop"]);
    assert!(!run.worker_running());
    run.request(&names(&["XR-2"]), None, None); // nothing starts after the end
    std::thread::sleep(Duration::from_millis(20));
    assert_eq!(entries(&log), ["leave", "stop"]);

    // During a load: the wait ends.
    let log = Log::default();
    let gate = Arc::new(AtomicBool::new(false));
    let (run, _) = run_of(FakeEngine { gate: Some(gate), ..FakeEngine::new(&log) }, FakeSbs::new(&log));
    run.request(&names(&["XR-1"]), None, None);
    wait_for("the start", || entries(&log) == ["start XR-1"]);
    let started = Instant::now();
    run.close(CLOSE_WAIT);
    assert!(started.elapsed() < Duration::from_secs(1));
    assert!(!run.worker_running());
    assert!(!entries(&log).contains(&"enter".to_string()));
}

#[test]
fn an_adapter_that_hangs_cannot_hold_recovery_up() {
    let log = Log::default();
    let gate = Arc::new(AtomicBool::new(false));
    let (run, engine) = run_of(FakeEngine::new(&log), FakeSbs { stuck: Some(gate.clone()), ..FakeSbs::new(&log) });
    run.request(&names(&["XR-1"]), None, None);
    wait_for("the engine", || engine.alive());
    let started = Instant::now();
    run.close(Duration::from_millis(50));
    assert!(started.elapsed() < Duration::from_secs(1));
    assert!(!engine.alive());
    gate.store(true, Ordering::SeqCst);
}

#[test]
fn completions_wait_for_the_renderers_readback() {
    let (run, _, _) = simple();
    let results: Arc<Mutex<Vec<(bool, String)>>> = Arc::default();
    let done = |r: &Arc<Mutex<Vec<(bool, String)>>>| -> Done {
        let r = r.clone();
        Box::new(move |ok, why| r.lock().unwrap().push((ok, why)))
    };
    let setting = all();
    run.request(&names(&["XR-1"]), Some(done(&results)), Some(&setting));
    events(&run, 1);
    assert!(results.lock().unwrap().is_empty()); // up, but the renderer has not said so
    let p = |strength: f64| {
        json!({"enabled": true, "physical_sbs": true, "available": true,
               "params": {"strength": strength, "convergence": 0.92, "near_deg": 4.0 / 60.0, "far_deg": 14.0 / 60.0, "text_keep": 0.0},
               "desktops": [{"monitor": "XR-1", "drawing": true, "depth_ready": true, "strength": strength}]})
    };
    run.observe_renderer(p(0.5)); // the wrong look
    assert!(results.lock().unwrap().is_empty());
    run.observe_renderer(p(1.0));
    assert_eq!(*results.lock().unwrap(), [(true, VERIFIED.to_string())]);
    // Already there: at once.
    run.request(&names(&["XR-1"]), Some(done(&results)), Some(&setting));
    assert_eq!(results.lock().unwrap().len(), 2);
    // A renderer error fails the waiting one.
    run.request(&names(&["XR-1", "XR-2"]), Some(done(&results)), None);
    events(&run, 1);
    run.observe_renderer(json!({"error": "no depth"}));
    assert_eq!(results.lock().unwrap()[2], (false, "renderer: no depth".to_string()));
    // A stopping run refuses at once.
    run.close(CLOSE_WAIT);
    run.request(&names(&["XR-1"]), Some(done(&results)), None);
    assert!(!results.lock().unwrap()[3].0);
    // A callback that panics hurts nothing.
    let (run, _, _) = simple();
    run.close(CLOSE_WAIT);
    run.request(&names(&["XR-1"]), Some(Box::new(|_, _| panic!("callback"))), None);
}

#[test]
fn no_device_refuses_and_recovery_never_fails() {
    assert!(NoDeviceSbs.enter(&|| false).is_err());
    assert!(NoDeviceSbs.leave().is_ok());
    let log = Log::default();
    restore_2d(&FakeSbs::new(&log));
    assert_eq!(entries(&log), ["leave"]);
    restore_2d(&FakeSbs { leave_fails: true, ..FakeSbs::new(&log) });
    restore_2d(&NoDeviceSbs);
    assert_eq!(toast("engine", "it quit").unwrap(), "3D is off: the depth engine could not start (it quit). Back to 2D.");
    assert!(toast("renderer", "x").is_none());
}

// --- the engine process -------------------------------------------------------------------------------------------

/// A stand-in engine: a shell script that logs to `out.log` and acts out `mode`.
fn fake_engine(dir: &TestDir, mode: &str) -> PathBuf {
    let out = dir.path().join("out.log");
    let before = match mode {
        "quit" => "exit 3",
        "slow" => "sleep 30",
        "noise" => "echo loading model; echo still",
        "hang" => "trap '' TERM",
        _ => ":",
    };
    let after = match mode {
        "die" => "exit 1",
        "hang" => "while read -r line; do :; done; echo eof >> \"$OUT\"; sleep 30",
        _ => "while read -r line; do echo \"line $line\" >> \"$OUT\"; done; echo eof >> \"$OUT\"",
    };
    let script = format!(
        "#!/bin/sh\nOUT='{}'\necho \"args $*\" >> \"$OUT\"\n{before}\necho ready\n{after}\n",
        out.display()
    );
    let binary = dir.path().join(ENGINE_NAME);
    std::fs::write(&binary, script).unwrap();
    std::fs::set_permissions(&binary, std::fs::Permissions::from_mode(0o700)).unwrap();
    binary
}

fn lines(dir: &TestDir) -> Vec<String> {
    std::fs::read_to_string(dir.path().join("out.log")).unwrap_or_default().lines().map(str::to_owned).collect()
}

fn process_engine(binary: &Path, ready_timeout: Duration, stop_wait: Duration) -> ProcessEngine {
    let mut e = ProcessEngine::new(Some(binary.to_owned()), None);
    e.ready_timeout = ready_timeout;
    e.stop_wait = stop_wait;
    e
}

#[test]
fn the_engine_is_found_only_beside_the_pinned_renderer() {
    let dir = TestDir::new("engine-find");
    let binary = fake_engine(&dir, "ok");
    assert_eq!(find_engine(Some(&binary.with_file_name("xr-native-spike"))), Some(binary.clone()));
    assert_eq!(find_engine(None), None);
    assert_eq!(find_engine(Some(&dir.path().join("none/xr-native-spike"))), None);
    std::fs::set_permissions(&binary, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(find_engine(Some(&binary)), None);
    assert_eq!(ProcessEngine::new(None, None).start(&names(&["XR-1"]), &|| false), Err(EngineError("it is not installed".into())));
    let missing = ProcessEngine::new(Some(dir.path().join("missing")), None);
    assert_eq!(missing.start(&names(&["XR-1"]), &|| false), Err(EngineError("it would not run: FileNotFoundError".into())));
}

#[test]
fn it_starts_on_ready_changes_and_stops_on_end_of_input() {
    let dir = TestDir::new("engine-ok");
    let e = process_engine(&fake_engine(&dir, "ok"), READY_TIMEOUT, STOP_WAIT);
    assert_eq!(e.start(&names(&["XR-1", "XR-3"]), &|| false), Ok(true));
    assert!(e.alive());
    assert_eq!(lines(&dir)[0], "args --desktops XR-1,XR-3");
    e.set_desktops(&names(&["XR-2"])).unwrap();
    wait_for("the line", || lines(&dir).contains(&r#"line {"desktops": ["XR-2"]}"#.to_string()));
    e.stop();
    assert!(!e.alive());
    assert_eq!(lines(&dir).last().unwrap(), "eof");
    assert_eq!(e.last_exit(), Some(0));
    e.stop(); // twice is fine
    assert_eq!(e.set_desktops(&names(&["XR-2"])), Err(EngineError("it stopped listening".into())));

    let dir = TestDir::new("engine-noise");
    let e = process_engine(&fake_engine(&dir, "noise"), READY_TIMEOUT, STOP_WAIT);
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Ok(true));
}

#[test]
fn engines_that_do_not_come_up_leave_nothing_running() {
    let dir = TestDir::new("engine-quit");
    let e = process_engine(&fake_engine(&dir, "quit"), READY_TIMEOUT, STOP_WAIT);
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Err(EngineError("it quit while starting".into())));
    assert_eq!(e.pid(), None);

    let dir = TestDir::new("engine-slow");
    let e = process_engine(&fake_engine(&dir, "slow"), Duration::from_millis(300), Duration::from_millis(200));
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Err(EngineError("it took too long to load".into())));
    assert_eq!(e.pid(), None);

    let dir = TestDir::new("engine-cancel");
    let e = process_engine(&fake_engine(&dir, "slow"), READY_TIMEOUT, Duration::from_millis(200));
    let out = dir.path().join("out.log");
    assert_eq!(e.start(&names(&["XR-1"]), &|| out.exists()), Ok(false));
    assert_eq!(e.pid(), None);
}

#[test]
fn one_that_ignores_eof_and_sigterm_is_killed() {
    let dir = TestDir::new("engine-hang");
    let e = process_engine(&fake_engine(&dir, "hang"), READY_TIMEOUT, Duration::from_millis(200));
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Ok(true));
    e.stop();
    assert_eq!(e.last_exit(), Some(-libc::SIGKILL));
    assert_eq!(lines(&dir).last().unwrap(), "eof");
}

#[test]
fn it_notices_dying() {
    let dir = TestDir::new("engine-die");
    let e = process_engine(&fake_engine(&dir, "die"), READY_TIMEOUT, STOP_WAIT);
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Ok(true));
    wait_for("it to die", || !e.alive());
    let mut failed = false;
    for _ in 0..200 {
        if e.set_desktops(&names(&["XR-2"])).is_err() {
            failed = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    assert!(failed);
}

// --- the memory ---------------------------------------------------------------------------------------------------

#[test]
fn the_memory_is_the_renderers_size_with_no_name() {
    assert_eq!(DepthMemory::size(), 10_735_616);
    let header = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../spike/include/xr/depth.hpp")).unwrap();
    assert!(header.contains("kDepthLinkShm{4, kDepthLongBalanced, kDepthMinTall, kDepthLongBalanced, kDepthMinTall}"));
    let m = DepthMemory::new().unwrap();
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    // SAFETY: `st` is a valid stat buffer.
    assert_eq!(unsafe { libc::fstat(m.fd(), &mut st) }, 0);
    assert_eq!((st.st_size as usize, st.st_nlink), (DepthMemory::size(), 0));
    let link = std::fs::read_link(format!("/proc/self/fd/{}", m.fd())).unwrap();
    assert!(link.to_string_lossy().starts_with("/memfd:"), "{link:?}");
    // SAFETY: fcntl on our own descriptor.
    let flags = unsafe { libc::fcntl(m.fd(), libc::F_GETFD) };
    assert_ne!(flags & libc::FD_CLOEXEC, 0); // only ever passed on purpose
}

fn write_beat(m: &DepthMemory, ns: u64) {
    // SAFETY: 8 valid bytes written to our own descriptor.
    let n = unsafe { libc::pwrite(m.fd(), ns.to_le_bytes().as_ptr().cast(), 8, DepthMemory::HEARTBEAT_AT) };
    assert_eq!(n, 8);
}

#[test]
fn the_heartbeat_and_a_hung_engine() {
    let m = Arc::new(DepthMemory::new().unwrap());
    assert_eq!(m.heartbeat_age(None), None); // no engine yet
    write_beat(&m, 5_000_000_000);
    assert_eq!(m.heartbeat_age(Some(7_000_000_000)), Some(2.0));
    write_beat(&m, 0);

    let dir = TestDir::new("engine-hung");
    let mut e = ProcessEngine::new(Some(fake_engine(&dir, "ok")), Some(m.clone()));
    e.stop_wait = Duration::from_millis(500);
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Ok(true));
    let fd = m.fd();
    assert!(lines(&dir)[0].ends_with(&format!("--shm-fd {fd}")), "{:?}", lines(&dir));
    assert!(e.alive()); // still loading: no heartbeat yet
    write_beat(&m, monotonic_ns());
    assert!(e.alive());
    write_beat(&m, monotonic_ns() - ((HEARTBEAT_LIMIT_S + 1.0) * 1e9) as u64);
    assert!(!e.alive());
}

// --- the real depth engine ----------------------------------------------------------------------------------------

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap()
}

fn real_engine(dir: &TestDir, wait: &str) -> PathBuf {
    let binary = dir.path().join(ENGINE_NAME);
    let script = format!(
        "#!/bin/sh\nexec /usr/bin/python3 -c \"import sys,runpy; sys.path.insert(0,'{}'); sys.argv[0]='xr-depth-engine'; \
         runpy.run_module('tools.depth_engine',run_name='__main__')\" serve --supervised --backend fake --wait {wait} \"$@\"\n",
        root().display()
    );
    std::fs::write(&binary, script).unwrap();
    std::fs::set_permissions(&binary, std::fs::Permissions::from_mode(0o700)).unwrap();
    binary
}

/// What the renderer does on its first 3D frame: the header, then the magic last.
fn lay_out(m: &DepthMemory) {
    let mut header = Vec::new();
    for v in [0u32, 1, 4, 3, 420, 266, 420, 266] {
        header.extend_from_slice(&v.to_le_bytes());
    }
    for v in [2_682_880u64, DepthMemory::size() as u64] {
        header.extend_from_slice(&v.to_le_bytes());
    }
    // SAFETY: valid buffers written to our own descriptor.
    unsafe {
        assert_eq!(libc::pwrite(m.fd(), header.as_ptr().cast(), header.len(), 0), header.len() as isize);
        assert_eq!(libc::pwrite(m.fd(), 0x3144_5258u32.to_le_bytes().as_ptr().cast(), 4, 0), 4);
    }
}

/// The renderer's side, in Python over the same memory: publish a picture, wait for the engine's depth map.
const RENDERER: &str = r#"
import sys, time
sys.path.insert(0, sys.argv[1])
import numpy as np
from tools.depth_engine import shm as S
m = S.DepthShm.open('/proc/self/fd/' + sys.argv[2])
m.set_control(0, S.ENABLED)
px = np.full((266, 420, 4), 200, np.uint8); px[:133, :, :3] = 40
assert m.publish(S.INPUT, 0, px, S.FMT_RGBA8, 1, time.monotonic_ns())
deadline = time.monotonic() + 5
out = None
while out is None and time.monotonic() < deadline:
    out = m.fetch(S.OUTPUT, 0)
    time.sleep(0.01)
print(out.w, out.h, out.format == S.FMT_R16F)
"#;

#[test]
fn the_real_engine_serves_over_this_memory_and_goes_at_end_of_input() {
    let dir = TestDir::new("real-engine");
    let memory = Arc::new(DepthMemory::new().unwrap());
    let e = ProcessEngine::new(Some(real_engine(&dir, "5")), Some(memory.clone()));
    let m = memory.clone();
    let renderer = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200)); // the engine starts first, as in a run
        lay_out(&m);
    });
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Ok(true));
    renderer.join().unwrap();
    assert!(e.alive());
    assert!(memory.heartbeat_age(None).is_some());
    let out = run_program(&Invocation {
        argv: vec!["/usr/bin/python3".into(), "-c".into(), RENDERER.into(), root().display().to_string(), memory.fd().to_string()],
        env: tool_env(),
        pass_fd: Some(memory.fd()),
        timeout: Duration::from_secs(20),
        max_output: 4096,
    })
    .unwrap();
    assert_eq!(
        (out.returncode, String::from_utf8_lossy(&out.stdout).trim()),
        (0, "420 266 True"),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    e.set_desktops(&names(&["XR-1", "XR-2"])).unwrap(); // says so, and stays up
    assert!(e.alive());
    e.stop();
    assert_eq!(e.last_exit(), Some(0)); // end of input: gone, by itself
}

#[test]
fn the_real_engine_killed_outright_is_dead() {
    let dir = TestDir::new("real-engine-kill");
    let memory = Arc::new(DepthMemory::new().unwrap());
    let e = ProcessEngine::new(Some(real_engine(&dir, "5")), Some(memory.clone()));
    let m = memory.clone();
    let renderer = std::thread::spawn(move || lay_out(&m));
    assert_eq!(e.start(&names(&["XR-1"]), &|| false), Ok(true));
    renderer.join().unwrap();
    // SAFETY: killpg on the engine's own process group.
    unsafe { libc::killpg(e.pid().unwrap() as i32, libc::SIGKILL) };
    wait_for("it to be dead", || !e.alive());
}

#[test]
fn memory_never_laid_out_is_a_failure_to_start_not_a_hang() {
    let dir = TestDir::new("real-engine-blank");
    let memory = Arc::new(DepthMemory::new().unwrap());
    let mut e = ProcessEngine::new(Some(real_engine(&dir, "0.1")), Some(memory));
    e.ready_timeout = Duration::from_secs(8);
    let got = e.start(&names(&["XR-1"]), &|| false);
    assert!(matches!(&got, Err(EngineError(why)) if why.contains("quit")), "{got:?}");
}

/// Parked glasses whose settle panics (review fix: Python's `_settle` reports any exception as a leave).
struct PanickySettle;

impl Sbs for PanickySettle {
    fn enter(&self, _cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, SbsError> {
        Ok(true)
    }
    fn leave(&self) -> std::result::Result<(), SbsError> {
        Ok(())
    }
    fn parked(&self) -> bool {
        true
    }
    fn settle(&self) -> std::result::Result<(), SbsError> {
        panic!("bug")
    }
}

#[test]
fn a_settle_that_panics_is_still_a_leave() {
    let log = Log::default();
    let run = ImmersiveRun::new(Arc::new(FakeEngine::new(&log)), Arc::new(PanickySettle));
    run.request(&names(&["XR-1"]), None, None);
    events(&run, 1);
    run.request(&[], None, None); // down, then parked: the worker settles the glasses
    let mut got = Vec::new();
    wait_for("the leave", || {
        got.extend(run.take_events());
        got.contains(&Event::Leave("an internal error".into()))
    });
    run.close(CLOSE_WAIT);
}

#[test]
fn an_abandoned_worker_is_not_waited_for_or_stopped_again() {
    let log = Log::default();
    let gate = Arc::new(AtomicBool::new(false));
    let (run, engine) = run_of(FakeEngine::new(&log), FakeSbs { stuck: Some(gate.clone()), ..FakeSbs::new(&log) });
    run.request(&names(&["XR-1"]), None, None);
    wait_for("the engine", || engine.alive());
    run.close(Duration::from_millis(50));
    let stops = entries(&log).iter().filter(|e| *e == "stop").count();
    let started = Instant::now();
    drop(run); // Drop closes again
    assert!(started.elapsed() < Duration::from_millis(500));
    assert_eq!(entries(&log).iter().filter(|e| *e == "stop").count(), stops);
    gate.store(true, Ordering::SeqCst);
}

#[test]
fn an_empty_renderer_path_finds_no_engine() {
    assert_eq!(find_engine(Some(Path::new(""))), None);
}
