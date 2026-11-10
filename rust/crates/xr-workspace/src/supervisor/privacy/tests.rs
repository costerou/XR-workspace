//! privacy.rs against a fake compositor: the main display choice, the toggle and its messages, the watcher's re-blank,
//! close's wake, and the recovery wake.

use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use xr_wire::json::{Value, parse_strict};

use super::*;

/// A compositor holding each monitor's DPMS state; `stuck` outputs ignore DPMS changes.
#[derive(Clone, Default)]
struct Fake {
    monitors: Arc<Mutex<Vec<(String, bool)>>>,
    calls: Arc<Mutex<Vec<String>>>,
    stuck: Arc<Mutex<Vec<String>>>,
    failing: Arc<Mutex<bool>>,
}

impl Fake {
    fn new(monitors: &[(&str, bool)]) -> Fake {
        let fake = Fake::default();
        *fake.monitors.lock().unwrap() = monitors
            .iter()
            .map(|(n, on)| ((*n).to_owned(), *on))
            .collect();
        fake
    }

    fn on(&self, name: &str) -> bool {
        self.monitors
            .lock()
            .unwrap()
            .iter()
            .any(|(n, on)| n == name && *on)
    }

    fn set(&self, name: &str, on: bool) {
        for m in self
            .monitors
            .lock()
            .unwrap()
            .iter_mut()
            .filter(|(n, _)| n == name)
        {
            m.1 = on;
        }
    }

    fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }
}

impl Displays for Fake {
    fn monitors(&mut self, _: Duration) -> Result<Value, String> {
        if *self.failing.lock().unwrap() {
            return Err("compositor busy".into());
        }
        let items: Vec<String> = self
            .monitors
            .lock()
            .unwrap()
            .iter()
            .map(|(n, on)| format!(r#"{{"name":"{n}","description":"","dpmsStatus":{on},"disabled":false,"mirrorOf":"none"}}"#))
            .collect();
        Ok(parse_strict(format!("[{}]", items.join(",")).as_bytes()).unwrap())
    }

    fn set_dpms(&mut self, name: &str, on: bool, keep: Option<&str>) -> Result<(), String> {
        self.calls
            .lock()
            .unwrap()
            .push(format!("{name} {} {keep:?}", if on { "on" } else { "off" }));
        if *self.failing.lock().unwrap() {
            return Err("compositor busy".into());
        }
        if !self.stuck.lock().unwrap().iter().any(|s| s == name) {
            self.set(name, on);
            if let Some(keep) = keep {
                self.set(keep, true);
            }
        }
        Ok(())
    }
}

fn json(text: &str) -> Value {
    parse_strict(text.as_bytes()).unwrap()
}

const SLOW: Duration = Duration::from_secs(3600);

fn privacy(fake: &Fake, watch: Duration) -> (PrivacyDisplay<Fake>, mpsc::Receiver<String>) {
    let (tx, rx) = mpsc::channel();
    let tx = Mutex::new(tx);
    let notify: Notify = Box::new(move |m: &str| {
        let _ = tx.lock().unwrap().send(m.to_owned());
    });
    let p = PrivacyDisplay::new(
        fake.clone(),
        "DP-2",
        Some("DP-1"),
        Some(notify),
        DEADLINE,
        watch,
    );
    (p, rx)
}

/// One press, waited for: (success, detail).
fn press(p: &PrivacyDisplay<Fake>) -> (bool, String) {
    let (tx, rx) = mpsc::channel();
    p.request(Some(Box::new(move |ok, detail| {
        tx.send((ok, detail)).unwrap()
    })));
    rx.recv_timeout(Duration::from_secs(5))
        .expect("the press completes")
}

#[test]
fn the_main_display_is_the_fallback_or_the_only_physical_monitor() {
    let monitors = json(
        r#"[{"name":"DP-2"},{"name":"XR-1"},{"name":"HEADLESS-1"},{"name":"DP-3","description":"VITURE Pro"},
            {"name":"DP-4","disabled":true},{"name":"DP-5","mirrorOf":"DP-1"},{"name":5},7,
            {"name":"HDMI-A-1","mirrorOf":"none"},{"name":"DP-1","mirrorOf":null}]"#,
    );
    assert_eq!(
        main_display(&monitors, "DP-2", Some("DP-1"))
            .unwrap()
            .as_deref(),
        Some("DP-1")
    );
    assert_eq!(
        main_display(&monitors, "DP-2", None).unwrap(),
        None,
        "two candidates, no fallback"
    );
    assert_eq!(
        main_display(&monitors, "DP-2", Some("DP-4")).unwrap(),
        None,
        "a disabled fallback does not count"
    );
    let one = json(r#"[{"name":"DP-2"},{"name":"eDP-1","description":"Laptop"}]"#);
    assert_eq!(
        main_display(&one, "DP-2", Some("DP-1")).unwrap().as_deref(),
        Some("eDP-1")
    );
    assert_eq!(
        main_display(&json("{}"), "DP-2", None).unwrap_err(),
        "invalid monitors"
    );
}

#[test]
fn dpms_status_needs_exactly_one_monitor_with_a_bool() {
    let m = json(
        r#"[{"name":"DP-1","dpmsStatus":true},{"name":"DP-3","dpmsStatus":1},{"name":"DP-4","dpmsStatus":false},{"name":"DP-4","dpmsStatus":false}]"#,
    );
    assert_eq!(dpms_status(&m, "DP-1"), Some(true));
    assert_eq!(dpms_status(&m, "DP-3"), None);
    assert_eq!(dpms_status(&m, "DP-4"), None, "two of a name");
    assert_eq!(dpms_status(&m, "DP-9"), None);
    assert_eq!(dpms_status(&json("{}"), "DP-1"), None);
}

#[test]
fn a_press_blanks_the_main_display_and_the_next_wakes_it() {
    let fake = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    let (p, notes) = privacy(&fake, SLOW);
    let on = press(&p);
    assert_eq!(
        on,
        (
            true,
            "Privacy on: DP-1 is off, only the glasses show content. Ctrl+Alt+D turns it back on."
                .into()
        )
    );
    assert!(!fake.on("DP-1") && fake.on("DP-2"));
    assert_eq!(
        fake.calls(),
        ["DP-1 off Some(\"DP-2\")"],
        "one eval: off for the target, on for the glasses"
    );
    assert_eq!(p.blanked().as_deref(), Some("DP-1"));
    assert_eq!(press(&p), (true, "Privacy off: DP-1 is back on.".into()));
    assert!(fake.on("DP-1"));
    assert_eq!(p.blanked(), None);
    // The notification follows the completion, on the worker.
    let heard: Vec<String> = (0..2)
        .map(|_| notes.recv_timeout(Duration::from_secs(5)).unwrap())
        .collect();
    assert_eq!(heard, [on.1, "Privacy off: DP-1 is back on.".to_owned()]);
}

#[test]
fn a_press_follows_the_run_state_not_the_display() {
    let fake = Fake::new(&[("DP-1", false), ("DP-2", true)]);
    let (p, _) = privacy(&fake, SLOW);
    assert!(press(&p).0, "an already sleeping display is still blanked");
    assert_eq!(p.blanked().as_deref(), Some("DP-1"));
}

#[test]
fn failures_say_what_went_wrong() {
    let none = Fake::new(&[("DP-2", true)]);
    assert_eq!(
        press(&privacy(&none, SLOW).0),
        (false, "No single main display found to turn off.".into())
    );

    let stuck = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    stuck.stuck.lock().unwrap().push("DP-1".into());
    let (p, _) = privacy(&stuck, SLOW);
    assert_eq!(press(&p), (false, "DP-1 did not turn off.".into()));
    assert_eq!(p.blanked(), None);

    let fake = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    let (p, _) = privacy(&fake, SLOW);
    assert!(press(&p).0);
    fake.stuck.lock().unwrap().push("DP-1".into());
    assert_eq!(press(&p), (false, "DP-1 did not turn on.".into()));
    assert_eq!(
        p.blanked().as_deref(),
        Some("DP-1"),
        "still blanked, so close and recovery wake it"
    );

    let busy = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    *busy.failing.lock().unwrap() = true;
    assert_eq!(
        press(&privacy(&busy, SLOW).0),
        (false, "compositor busy".into())
    );
}

#[test]
fn the_watcher_blanks_a_display_something_else_woke() {
    let fake = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    let (p, _) = privacy(&fake, Duration::from_millis(10));
    assert!(press(&p).0);
    fake.set("DP-1", true); // an idle daemon or a hotplug
    let until = Instant::now() + Duration::from_secs(5);
    while fake.on("DP-1") && Instant::now() < until {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(!fake.on("DP-1"), "re-blanked");
    assert!(fake.calls().len() >= 2);
    // A busy compositor does not end the watch.
    *fake.failing.lock().unwrap() = true;
    thread::sleep(Duration::from_millis(50));
    *fake.failing.lock().unwrap() = false;
    fake.set("DP-1", true);
    let until = Instant::now() + Duration::from_secs(5);
    while fake.on("DP-1") && Instant::now() < until {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(!fake.on("DP-1"), "re-blanked after the busy spell");
    p.close(None);
}

#[test]
fn close_wakes_the_display_and_refuses_later_presses() {
    let fake = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    let (p, _) = privacy(&fake, Duration::from_millis(10));
    assert!(press(&p).0);
    p.close(None);
    assert!(fake.on("DP-1"), "close turns it back on");
    assert_eq!(p.blanked(), None);
    assert_eq!(press(&p), (false, "Privacy owner closed".into()));
    let calls = fake.calls().len();
    thread::sleep(Duration::from_millis(50));
    fake.set("DP-1", true);
    assert_eq!(fake.calls().len(), calls, "the watcher stopped");

    let idle = Fake::new(&[("DP-1", true), ("DP-2", true)]);
    privacy(&idle, SLOW).0.close(None);
    assert!(idle.calls().is_empty(), "nothing blanked, nothing to wake");
}

#[test]
fn recovery_wakes_only_a_sleeping_physical_display() {
    let fake = Fake::new(&[("DP-1", false), ("DP-2", true)]);
    assert!(wake(&mut fake.clone(), "DP-1", DEADLINE));
    assert!(fake.on("DP-1"));
    assert!(!wake(&mut fake.clone(), "DP-1", DEADLINE), "already on");
    assert!(
        !wake(&mut fake.clone(), "XR-1", DEADLINE),
        "not a physical output"
    );
    assert!(!wake(&mut fake.clone(), "DP-9", DEADLINE), "not there");
    fake.set("DP-1", false);
    fake.stuck.lock().unwrap().push("DP-1".into());
    assert!(
        !wake(&mut fake.clone(), "DP-1", DEADLINE),
        "did not come on"
    );
    *fake.failing.lock().unwrap() = true;
    assert!(
        !wake(&mut fake.clone(), "DP-1", DEADLINE),
        "errors are swallowed"
    );
    assert_eq!(fake.calls(), ["DP-1 on None", "DP-1 on None"]);
}
