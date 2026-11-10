//! What counts as an incident: `tools/xr_controller/incident_detect.py`, as pure code with no I/O.
//!
//! The thresholds and the reasons for them are the Python module's (from the logs of 2026-11-03, when the
//! glasses' USB hub dropped over and over); this file follows it line for line so that the same observations
//! give the same signals. Observations, consents and statuses stay JSON values, as they are dicts in Python.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use super::py::{self, as_int, as_num, as_str, get, iso_timestamp, py_eq, py_str, truthy};

/// Every kind, most telling first: an incident is named after the highest of its signals.
pub const KINDS: [&str; 18] = [
    "hyprland-crash",
    "renderer-crash",
    "control-closed",
    "usb-drop",
    "video-lost",
    "plugin-failed",
    "watchdog",
    "controller-restart",
    "run-killed",
    "run-failed",
    "capture-failed",
    "present-stall",
    "tracking-lost",
    "renderer-exit",
    "control-refused",
    "supervisor-fault",
    "recovery-failed",
    "start-failed",
];

pub const STALL_S: f64 = 5.0;
pub const COLLAPSE_RATIO: f64 = 0.4;
pub const COLLAPSE_S: f64 = 10.0;
pub const RATE_WARMUP_S: f64 = 5.0;
pub const RATE_DECAY: f64 = 0.999;
pub const MODE_CHANGE_S: f64 = 5.0;
pub const OUTPUT_GONE_S: f64 = 3.0;
pub const PLUGIN_GONE_S: f64 = 3.0;
pub const TRACKING_LOST_S: f64 = 3.0;
pub const TRACKING_NEVER_S: f64 = 30.0;
pub const DRIVER_GONE_S: f64 = 3.0;
pub const USB_GONE_S: f64 = 2.0;
pub const UNRESPONSIVE_S: f64 = 15.0;
pub const CAPTURE_STUCK: i64 = 30;
pub const GAP_S: f64 = 5.0;
pub const USB_DEDUPE_S: f64 = 5.0;
pub const USB_FAILS: usize = 2;
pub const USB_FAILS_WINDOW_S: f64 = 30.0;
pub const RESUME_QUIET_S: f64 = 30.0;

pub const QUIET_S: f64 = 25.0;
pub const MAX_HOLD_S: f64 = 60.0;
pub const AFTER_END_S: f64 = 5.0;
pub const MAX_SIGNALS: usize = 64;

/// `tracking.SCOPES` and `tracking.PRO2_TRACKING`: a consent's `tracking`.
pub const SCOPES: [&str; 2] = ["xrlinuxdriver", "luma"];
pub const PRO2_TRACKING: &str = "pro2-sdk";
/// `faults.RUN`: the `where` of an exception that ended the run.
pub const RUN_FAULT: &str = "the run";
/// The supervisor gave up on a USB drop: its limit, not a crash.
pub const TRACKING_TIMEOUT: &str = "tracking-timeout";

/// Where a kind ranks in `KINDS`; an unknown kind (from a hand-edited report) ranks last.
pub fn rank(kind: &str) -> usize {
    KINDS.iter().position(|k| *k == kind).unwrap_or(KINDS.len())
}

/// A consent's `tracking` is one of the tracked scopes (`in SCOPES + (PRO2_TRACKING,)`).
pub fn tracked_scope(tracking: &Value) -> bool {
    as_str(tracking).is_some_and(|t| SCOPES.contains(&t) || t == PRO2_TRACKING)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Signal {
    /// Wall clock.
    pub at: f64,
    /// One of `KINDS`.
    pub kind: String,
    /// Where it was seen: renderer, drm, hyprland, kernel, driver-log, unit, controller.
    pub source: String,
    /// One short line in our own words, never log text.
    pub detail: String,
}

impl Signal {
    pub fn new(at: f64, kind: &str, source: &str, detail: impl Into<String>) -> Signal {
        Signal {
            at,
            kind: kind.into(),
            source: source.into(),
            detail: detail.into(),
        }
    }

    fn same(&self, other: &Signal) -> bool {
        self.kind == other.kind && self.source == other.source && self.detail == other.detail
    }
}

/// `f'{x:.0f}'`: round half to even, as Python formats.
fn whole(x: f64) -> String {
    format!("{:.0}", x)
}

/// True once a condition has held `hold` seconds, then quiet until it clears.
#[derive(Clone, Debug)]
pub struct Sustained {
    hold: f64,
    since: Option<f64>,
    fired: bool,
}

impl Sustained {
    pub fn new(hold: f64) -> Sustained {
        Sustained {
            hold,
            since: None,
            fired: false,
        }
    }

    pub fn update(&mut self, at: f64, on: bool) -> bool {
        if !on {
            self.since = None;
            self.fired = false;
            return false;
        }
        let since = *self.since.get_or_insert(at);
        if self.fired || at - since < self.hold {
            return false;
        }
        self.fired = true;
        true
    }

    pub fn restart(&mut self) {
        self.since = None;
    }
}

/// `_mode(monitor)`: `(width, height, round(refresh or 0))`, compared as Python compares tuples.
#[derive(Clone, Debug)]
pub struct Mode {
    pub width: Value,
    pub height: Value,
    pub rate: i64,
}

impl PartialEq for Mode {
    fn eq(&self, other: &Mode) -> bool {
        py_eq(&self.width, &other.width)
            && py_eq(&self.height, &other.height)
            && self.rate == other.rate
    }
}

impl Mode {
    fn of(monitor: &Value) -> Mode {
        let refresh = get(monitor, "refresh");
        let rate = if truthy(refresh) {
            as_num(refresh).map_or(0, |r| r.round_ties_even() as i64)
        } else {
            0
        };
        Mode {
            width: get(monitor, "width").clone(),
            height: get(monitor, "height").clone(),
            rate,
        }
    }

    fn size_is(&self, w: i64, h: i64) -> bool {
        py_eq(&self.width, &json!(w)) && py_eq(&self.height, &json!(h))
    }

    fn text(&self) -> String {
        format!(
            "{}x{}@{}",
            py_str(&self.width),
            py_str(&self.height),
            self.rate
        )
    }
}

fn mode_text(mode: &Option<Mode>) -> Value {
    mode.as_ref()
        .map_or(Value::Null, |m| Value::String(m.text()))
}

/// `sbs.scope_ok(value, model='pro2')`.
fn pro2_scope_ok(value: &Value) -> bool {
    let expected = json!({"width": 3840, "height": 1080, "refresh_hz": [60]});
    value.is_object()
        && py_eq(value, &expected)
        && as_int(get(value, "width")).is_some()
        && as_int(get(value, "height")).is_some()
        && get(value, "refresh_hz")
            .as_array()
            .is_some_and(|rates| rates.iter().all(|r| as_int(r).is_some()))
}

/// One run's live detectors. `observe` takes one observation a second and says only what is wrong.
#[derive(Clone, Debug)]
pub struct RunWatch {
    pub run_id: String,
    pub consent: Value,
    pub created: f64,
    pub tracking: bool,
    pub driver_process: bool,
    /// A Stop was asked for, so the end is a normal one.
    pub stopped: bool,
    pub running_since: Value,
    pub last_at: Option<f64>,
    pub settle_until: f64,
    /// The last metrics, for the report.
    pub last_status: Value,
    pub mode: Option<Mode>,
    pub seen_mode: Option<Mode>,
    pub switch_started: Option<f64>,
    pub mode_switches: u64,
    pub switch_done: bool,
    pub drm: Value,
    pub usual_rate: Option<f64>,
    pub capture_path: Value,
    pub tracked: bool,
    pub usb_seen: bool,
    pub driver_seen: bool,
    video: Sustained,
    mode_changed: Sustained,
    output_gone: Sustained,
    stall: Sustained,
    collapse: Sustained,
    tracking_lost: Sustained,
    never_tracked: Sustained,
    capture_stuck: Sustained,
    driver_gone: Sustained,
    usb_gone: Sustained,
    hyprland_gone: Sustained,
    plugin_gone: Sustained,
    silent: Sustained,
}

impl RunWatch {
    pub fn new(run_id: &str, consent: &Value, at: f64) -> RunWatch {
        let consent = if truthy(consent) {
            consent.clone()
        } else {
            Value::Object(Map::new())
        };
        let tracking = get(&consent, "tracking");
        RunWatch {
            run_id: run_id.into(),
            tracking: tracked_scope(tracking),
            driver_process: as_str(tracking).is_some_and(|t| SCOPES.contains(&t)),
            consent,
            created: at,
            stopped: false,
            running_since: Value::Null,
            last_at: None,
            settle_until: 0.0,
            last_status: Value::Null,
            mode: None,
            seen_mode: None,
            switch_started: None,
            mode_switches: 0,
            switch_done: false,
            drm: Value::Null,
            usual_rate: None,
            capture_path: Value::Null,
            tracked: false,
            usb_seen: false,
            driver_seen: false,
            video: Sustained::new(0.0),
            mode_changed: Sustained::new(MODE_CHANGE_S),
            output_gone: Sustained::new(OUTPUT_GONE_S),
            stall: Sustained::new(STALL_S),
            collapse: Sustained::new(COLLAPSE_S),
            tracking_lost: Sustained::new(TRACKING_LOST_S),
            never_tracked: Sustained::new(0.0),
            capture_stuck: Sustained::new(0.0),
            driver_gone: Sustained::new(DRIVER_GONE_S),
            usb_gone: Sustained::new(USB_GONE_S),
            hyprland_gone: Sustained::new(0.0),
            plugin_gone: Sustained::new(PLUGIN_GONE_S),
            silent: Sustained::new(UNRESPONSIVE_S),
        }
    }

    pub fn mode_text(&self) -> Value {
        mode_text(&self.seen_mode)
    }

    pub fn expected_mode_text(&self) -> Value {
        mode_text(&self.mode)
    }

    fn running_since(&self) -> f64 {
        as_num(&self.running_since).unwrap_or(0.0)
    }

    pub fn observe(&mut self, o: &Value) -> Vec<Signal> {
        let at = as_num(get(o, "at")).unwrap_or(0.0);
        let state = as_str(get(o, "state")).unwrap_or("");
        let mut out = Vec::new();
        if self.last_at.is_some_and(|last| at - last > GAP_S) {
            for timer in [
                &mut self.mode_changed,
                &mut self.output_gone,
                &mut self.stall,
                &mut self.collapse,
                &mut self.tracking_lost,
                &mut self.driver_gone,
                &mut self.usb_gone,
                &mut self.plugin_gone,
                &mut self.silent,
            ] {
                timer.restart();
            }
            self.settle_until = at + RESUME_QUIET_S;
        }
        self.last_at = Some(at);
        if state != "running" && state != "failed" {
            return out;
        }
        if self
            .hyprland_gone
            .update(at, *get(o, "hyprland_alive") == Value::Bool(false))
        {
            out.push(Signal::new(
                at,
                "hyprland-crash",
                "hyprland",
                "the Hyprland process is gone",
            ));
        }
        if at < self.settle_until {
            return out;
        }
        if state == "failed" {
            if self.silent.update(at, true) {
                out.push(Signal::new(
                    at,
                    "watchdog",
                    "controller",
                    format!(
                        "the run stopped answering for {} seconds while its unit stayed up",
                        whole(UNRESPONSIVE_S)
                    ),
                ));
            }
            return out;
        }
        self.silent.update(at, false);
        if self.running_since.is_null() {
            self.running_since = py::float(at);
        }
        let status = get(o, "status");
        if !status.is_null() {
            self.last_status = status.clone();
        }
        self.display(at, o, &mut out);
        self.hardware(at, o, &mut out);
        if !status.is_null() {
            self.presentation(at, o, status, &mut out);
            self.capture(at, status, &mut out);
            self.head(at, status, &mut out);
        }
        out
    }

    fn display(&mut self, at: f64, o: &Value, out: &mut Vec<Signal>) {
        let output = get(&self.consent, "output").clone();
        if !truthy(&output) {
            return;
        }
        let immersive = get(o, "immersive");
        let immersive = if truthy(immersive) {
            immersive
        } else {
            get(get(o, "status"), "immersive")
        };
        let pending = truthy(get(immersive, "switching"));
        let monitor = get(o, "monitor");
        if !pending {
            self.switch_done = false;
        }
        if pending
            && self.switch_started.is_none()
            && !self.switch_done
            && (as_str(get(o, "drm")) == Some("disconnected")
                || monitor.is_null()
                || self
                    .mode
                    .as_ref()
                    .is_some_and(|mode| Mode::of(monitor) != *mode))
        {
            self.switch_started = Some(at);
        }
        if let Some(started) = self.switch_started {
            let mode = if truthy(monitor) {
                Some(Mode::of(monitor))
            } else {
                None
            };
            let supported = match &mode {
                None => false,
                Some(m) if as_str(get(&self.consent, "model")) == Some("pro2") => {
                    pro2_scope_ok(get(&self.consent, "sbs_mode"))
                        && ((m.size_is(3840, 1080) && m.rate == 60)
                            || (m.size_is(1920, 1080) && [60, 90, 120].contains(&m.rate)))
                }
                Some(m) => {
                    (m.size_is(3840, 1200) && [60, 90].contains(&m.rate))
                        || (m.size_is(1920, 1200) && m.rate == 120)
                }
            };
            if supported && mode != self.mode {
                self.mode_switches += 1;
                self.mode = mode.clone();
                self.seen_mode = mode;
                self.switch_started = None;
                self.video.update(at, false);
                self.output_gone.update(at, false);
                self.mode_changed.update(at, false);
                self.stall.restart();
                return;
            }
            if at - started < 8.0 {
                return;
            }
            self.switch_started = None;
            self.switch_done = true;
        }
        self.drm = get(o, "drm").clone();
        if self
            .video
            .update(at, as_str(&self.drm) == Some("disconnected"))
        {
            out.push(Signal::new(
                at,
                "video-lost",
                "drm",
                format!(
                    "the glasses' display output {} disconnected",
                    py_str(&output)
                ),
            ));
        }
        if !truthy(get(o, "monitors_known")) {
            return;
        }
        if self.output_gone.update(at, monitor.is_null()) {
            out.push(Signal::new(
                at,
                "video-lost",
                "hyprland",
                format!(
                    "the glasses' display output {} left Hyprland",
                    py_str(&output)
                ),
            ));
        }
        if monitor.is_null() {
            return;
        }
        let seen = Mode::of(monitor);
        self.seen_mode = Some(seen.clone());
        if self.mode.is_none() {
            self.mode = Some(seen.clone());
        }
        if self
            .mode_changed
            .update(at, self.mode.as_ref() != Some(&seen))
        {
            out.push(Signal::new(
                at,
                "video-lost",
                "hyprland",
                format!(
                    "the display mode changed from {} to {}",
                    py_str(&mode_text(&self.mode)),
                    py_str(&mode_text(&self.seen_mode))
                ),
            ));
        }
    }

    fn hardware(&mut self, at: f64, o: &Value, out: &mut Vec<Signal>) {
        let usb = get(o, "usb_present");
        let driver = get(o, "driver_alive");
        self.usb_seen = self.usb_seen || *usb == Value::Bool(true);
        self.driver_seen = self.driver_seen || *driver == Value::Bool(true);
        if self
            .usb_gone
            .update(at, self.usb_seen && *usb == Value::Bool(false))
        {
            out.push(Signal::new(
                at,
                "usb-drop",
                "usb",
                "the glasses left the USB bus",
            ));
        }
        if truthy(get(o, "monitors_known"))
            && self
                .plugin_gone
                .update(at, py_eq(get(o, "plugins"), &json!(0)))
        {
            out.push(Signal::new(
                at,
                "plugin-failed",
                "hyprland",
                "the ownership plugin is no longer loaded in Hyprland",
            ));
        }
        if self.driver_process
            && self
                .driver_gone
                .update(at, self.driver_seen && *driver == Value::Bool(false))
        {
            out.push(Signal::new(
                at,
                "tracking-lost",
                "tracking",
                "the head tracking driver process is gone",
            ));
        }
    }

    fn presentation(&mut self, at: f64, o: &Value, status: &Value, out: &mut Vec<Signal>) {
        let monitor = get(o, "monitor");
        let asleep = !monitor.is_null() && *get(monitor, "dpms") == Value::Bool(false);
        let rate = get(status, "presented_hz");
        let watching = self.tracking && !asleep;
        if self.stall.update(at, watching && rate.is_null()) {
            out.push(Signal::new(
                at,
                "present-stall",
                "renderer",
                format!(
                    "no frames reached the glasses for {} seconds",
                    whole(STALL_S)
                ),
            ));
        }
        let rate = as_num(rate);
        let Some(rate) = rate.filter(|_| watching && at - self.running_since() >= RATE_WARMUP_S)
        else {
            self.collapse.update(at, false);
            return;
        };
        let low = self
            .usual_rate
            .is_some_and(|usual| rate < COLLAPSE_RATIO * usual);
        if !low {
            let decayed = self.usual_rate.unwrap_or(0.0) * RATE_DECAY;
            self.usual_rate = Some(if decayed > rate { decayed } else { rate });
        }
        if self.collapse.update(at, low) {
            out.push(Signal::new(
                at,
                "present-stall",
                "renderer",
                format!(
                    "the frame rate collapsed to {} Hz from about {} Hz",
                    whole(rate),
                    whole(self.usual_rate.unwrap_or(0.0))
                ),
            ));
        }
    }

    fn capture(&mut self, at: f64, status: &Value, out: &mut Vec<Signal>) {
        let path = get(status, "capture_path");
        if truthy(path) {
            if as_str(&self.capture_path) == Some("dmabuf") && as_str(path) == Some("shm") {
                out.push(Signal::new(
                    at,
                    "capture-failed",
                    "renderer",
                    "capture fell back from dmabuf to shared memory",
                ));
            }
            self.capture_path = path.clone();
        }
        if let Some(failing) = as_int(get(status, "capture_failure_run"))
            && self.capture_stuck.update(at, failing >= CAPTURE_STUCK)
        {
            out.push(Signal::new(
                at,
                "capture-failed",
                "renderer",
                format!("desktop capture failed {failing} times in a row"),
            ));
        }
    }

    fn head(&mut self, at: f64, status: &Value, out: &mut Vec<Signal>) {
        if !self.tracking {
            return;
        }
        let tracking = get(status, "tracking");
        if *tracking == Value::Bool(true) {
            self.tracked = true;
        }
        let lost = *tracking == Value::Bool(false);
        if self.tracked {
            if self.tracking_lost.update(at, lost) {
                out.push(Signal::new(
                    at,
                    "tracking-lost",
                    "renderer",
                    format!(
                        "no head tracking samples for {} seconds",
                        whole(TRACKING_LOST_S)
                    ),
                ));
            }
        } else if self
            .never_tracked
            .update(at, lost && at - self.running_since() >= TRACKING_NEVER_S)
        {
            out.push(Signal::new(
                at,
                "tracking-lost",
                "renderer",
                format!(
                    "head tracking had not started {} seconds into the session",
                    whole(TRACKING_NEVER_S)
                ),
            ));
        }
    }
}

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("a fixed pattern")
}

static KERNEL: LazyLock<Regex> = LazyLock::new(|| regex(r"\A(?:(\S+) \S+ kernel: (.*))\z"));
static USB_GONE: LazyLock<Regex> = LazyLock::new(|| regex(r"\Ausb (\d+-[\d.]+): USB disconnect\b"));
static USB_NEW: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)\Ausb (\d+-[\d.]+): New USB device found, idVendor=35ca\b"));

/// Is `port` one of the glasses' ports, or a hub above one?
pub fn glasses_family(port: &str, ports: &BTreeSet<String>) -> bool {
    ports
        .iter()
        .any(|p| p == port || p.starts_with(&format!("{port}.")))
}

/// `journalctl -k -o short-iso` lines, newest last. The port is learned from the vendor id, never assumed.
#[derive(Clone, Debug, Default)]
pub struct KernelLog {
    pub ports: BTreeSet<String>,
    quiet_until: f64,
    last_drop: Option<f64>,
}

impl KernelLog {
    pub fn note_ports<'a>(&mut self, ports: impl IntoIterator<Item = &'a str>) {
        self.ports.extend(ports.into_iter().map(str::to_owned));
    }

    pub fn feed(&mut self, lines: &[String]) -> Vec<Signal> {
        let mut out = Vec::new();
        for line in lines {
            let Some(m) = KERNEL.captures(line.trim_end_matches('\n')) else {
                continue;
            };
            let Some(at) = iso_timestamp(&m[1]) else {
                continue;
            };
            let text = &m[2];
            if text.contains("PM: suspend entry") {
                self.quiet_until = f64::INFINITY;
            } else if text.contains("PM: suspend exit") {
                self.quiet_until = at + RESUME_QUIET_S;
            }
            if let Some(new) = USB_NEW.captures(text) {
                self.ports.insert(new[1].to_owned());
            }
            let Some(gone) = USB_GONE.captures(text) else {
                continue;
            };
            if !glasses_family(&gone[1], &self.ports) || at < self.quiet_until {
                continue;
            }
            if self.last_drop.is_none_or(|last| at - last >= USB_DEDUPE_S) {
                out.push(Signal::new(
                    at,
                    "usb-drop",
                    "kernel",
                    format!(
                        "the kernel saw the glasses' USB port {} disconnect",
                        &gone[1]
                    ),
                ));
            }
            self.last_drop = Some(at);
        }
        out
    }
}

static DRIVER_TIME: LazyLock<Regex> =
    LazyLock::new(|| regex(r"\A(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?) "));
static LUMA_TIME: LazyLock<Regex> = LazyLock::new(|| regex(r" at=(\d+\.\d+)\z"));

/// New text from the tracking driver's log, in whatever pieces it is read; the caller seeks.
#[derive(Clone, Debug, Default)]
pub struct DriverLog {
    rest: String,
    failures: Vec<f64>,
}

impl DriverLog {
    pub fn feed(&mut self, text: &str, at: f64) -> Vec<Signal> {
        let joined = std::mem::take(&mut self.rest) + text;
        let mut lines: Vec<&str> = joined.split('\n').collect();
        self.rest = lines.pop().unwrap_or("").to_owned();
        let mut out = Vec::new();
        for line in lines {
            let mut when = at;
            if let Some(m) = DRIVER_TIME.captures(line)
                && let Some(t) = iso_timestamp(&m[1])
            {
                when = t;
            }
            if let Some(m) = LUMA_TIME.captures(line)
                && let Ok(t) = m[1].parse::<f64>()
            {
                when = t;
            }
            let mut say =
                |kind: &str, detail: &str| out.push(Signal::new(when, kind, "driver-log", detail));
            if line.contains("luma: disconnected") {
                say(
                    "usb-drop",
                    "the XR Luma driver lost the glasses and is reconnecting",
                );
            } else if line.contains("luma: stale") {
                say(
                    "tracking-lost",
                    "the XR Luma driver stopped getting samples",
                );
            } else if line.contains("luma: calibrating") {
                say("tracking-lost", "the XR Luma driver is calibrating again");
            } else if line.contains("luma: error") {
                say("tracking-lost", "the XR Luma driver reported an error");
            } else if line.contains("Retrying driver connection") {
                say(
                    "usb-drop",
                    "the tracking driver lost the glasses and is reconnecting",
                );
            } else if line.contains("Failed to send USB command")
                || line.contains("open_imu failed")
            {
                self.failures.retain(|t| at - t < USB_FAILS_WINDOW_S);
                self.failures.push(at);
                if self.failures.len() >= USB_FAILS {
                    say(
                        "usb-drop",
                        "the tracking driver's USB commands to the glasses are failing",
                    );
                    self.failures.clear();
                }
            } else if line.contains("Waiting on device calibration") {
                say("tracking-lost", "the tracking driver is calibrating again");
            } else if line.contains("Starting up XR driver") {
                say("tracking-lost", "the tracking driver restarted");
            }
        }
        out
    }
}

/// SIGSEGV, SIGABRT, SIGBUS, SIGILL, SIGFPE, SIGSYS, SIGTRAP, SIGQUIT.
const CRASHES: [i64; 8] = [11, 6, 7, 4, 8, 31, 5, 3];
/// SIGTERM, SIGKILL, SIGINT.
const STOP_SIGNALS: [i64; 3] = [15, 9, 2];

fn watchdog_text(reason: &str) -> Option<&'static str> {
    Some(match reason {
        "heartbeat-timeout" => {
            "the renderer sent no heartbeat for 2 seconds, so the watchdog stopped it"
        }
        "heartbeat-invalid" => "the renderer sent a bad heartbeat, so the watchdog stopped it",
        "guard-failed" => "the run guard found an output missing or changed, and stopped the run",
        "guard-timeout" => "the run guard did not answer in time, and stopped the run",
        "send-failed" => "the supervisor could not reach the renderer, and stopped it",
        _ => return None,
    })
}

/// `signal.Signals(number).name`, or `signal N` for a number Linux does not name.
pub fn signal_name(number: i64) -> String {
    let name = match number {
        1 => "SIGHUP",
        2 => "SIGINT",
        3 => "SIGQUIT",
        4 => "SIGILL",
        5 => "SIGTRAP",
        6 => "SIGABRT",
        7 => "SIGBUS",
        8 => "SIGFPE",
        9 => "SIGKILL",
        10 => "SIGUSR1",
        11 => "SIGSEGV",
        12 => "SIGUSR2",
        13 => "SIGPIPE",
        14 => "SIGALRM",
        15 => "SIGTERM",
        16 => "SIGSTKFLT",
        17 => "SIGCHLD",
        18 => "SIGCONT",
        19 => "SIGSTOP",
        20 => "SIGTSTP",
        21 => "SIGTTIN",
        22 => "SIGTTOU",
        23 => "SIGURG",
        24 => "SIGXCPU",
        25 => "SIGXFSZ",
        26 => "SIGVTALRM",
        27 => "SIGPROF",
        28 => "SIGWINCH",
        29 => "SIGIO",
        30 => "SIGPWR",
        31 => "SIGSYS",
        34 => "SIGRTMIN",
        64 => "SIGRTMAX",
        _ => return format!("signal {number}"),
    };
    name.into()
}

/// Will a core dump appear? (The monitor waits for it.)
pub fn crash_expected(result: &Value) -> bool {
    as_int(get(result, "renderer_returncode")).is_some_and(|rc| rc < 0 && CRASHES.contains(&-rc))
}

fn unit_text(unit: &Value) -> String {
    if as_str(get(unit, "result")) == Some("oom-kill") {
        return "the system ran out of memory and killed the run's unit".into();
    }
    let status = get(unit, "status");
    if truthy(status) {
        return format!(
            "the run's unit ended ({}, {}) without reporting a result",
            py_str(get(unit, "code")),
            py_str(status)
        );
    }
    "the run's unit ended without reporting a result".into()
}

static CONTROL_CLOSED: LazyLock<Regex> =
    LazyLock::new(|| regex(r"control: supervisor channel closed"));

fn control_closed_at(lines: &[String], at: f64) -> Option<f64> {
    let line = lines
        .iter()
        .rev()
        .find(|line| CONTROL_CLOSED.is_match(line))?;
    Some(iso_timestamp(line.split(' ').next().unwrap_or("")).unwrap_or(at))
}

/// The supervisor faults a run's result holds, in one shape whatever the file held.
pub fn faults_of(result: &Value) -> Vec<Value> {
    let Some(faults) = get(result, "faults").as_array() else {
        return Vec::new();
    };
    faults
        .iter()
        .filter(|f| as_str(get(f, "where")).is_some() && get(f, "traceback").is_array())
        .map(|f| {
            let traceback: Vec<Value> = get(f, "traceback")
                .as_array()
                .into_iter()
                .flatten()
                .map(|line| Value::String(py_str(line)))
                .collect();
            json!({"where": get(f, "where"), "count": get(f, "count"), "traceback": traceback})
        })
        .collect()
}

/// How a run ended, for `classify_end`.
#[derive(Clone, Copy, Debug)]
pub struct End<'a> {
    pub started: bool,
    pub stopped: bool,
    pub stranded: bool,
    pub unit: &'a Value,
    pub log: &'a [String],
}

/// Signals for a run that is over: none for every normal end. `result` is its `.result.json`, or null.
pub fn classify_end(at: f64, result: &Value, end: End) -> Vec<Signal> {
    let mut out = Vec::new();
    if result.is_null() {
        if end.stranded || !end.stopped {
            let kind = if end.started || end.stranded {
                "run-killed"
            } else {
                "start-failed"
            };
            out.push(Signal::new(at, kind, "unit", unit_text(end.unit)));
        }
        return out;
    }
    let rc_value = get(result, "renderer_returncode");
    let rc = as_int(rc_value);
    let reason = as_str(get(result, "stop_reason"));
    let error = get(result, "error");
    let interrupted = end.stopped || reason == Some("interrupted");
    let own_exit = rc.is_some_and(|rc| rc > 0 || CRASHES.contains(&-rc));
    if reason == Some(TRACKING_TIMEOUT) {
        out.push(Signal::new(
            at,
            "tracking-lost",
            "supervisor",
            "head tracking did not come back in time, so the session ended",
        ));
    } else if let Some(text) = reason
        .and_then(watchdog_text)
        .filter(|_| !(reason == Some("send-failed") && own_exit))
    {
        out.push(Signal::new(at, "watchdog", "supervisor", text));
    } else if let Some(rc) = rc.filter(|rc| *rc < 0) {
        let number = -rc;
        if CRASHES.contains(&number) {
            out.push(Signal::new(
                at,
                "renderer-crash",
                "renderer",
                format!("the renderer crashed ({})", signal_name(number)),
            ));
        } else if !(interrupted && STOP_SIGNALS.contains(&number)) {
            out.push(Signal::new(
                at,
                "renderer-crash",
                "renderer",
                format!("something killed the renderer ({})", signal_name(number)),
            ));
        }
    } else if let Some(rc) = rc.filter(|rc| *rc > 0 && !interrupted) {
        match control_closed_at(end.log, at) {
            Some(closed) => out.push(Signal::new(
                closed,
                "control-closed",
                "renderer",
                "the renderer stopped: it lost its control channel to the supervisor",
            )),
            None => out.push(Signal::new(
                at,
                "renderer-exit",
                "renderer",
                format!("the renderer exited with code {rc}"),
            )),
        }
    }
    let metrics = exit_metrics(end.log);
    if let Some(refused) = as_int(get(get(&metrics, "control"), "refused"))
        && refused > 0
    {
        out.push(Signal::new(
            at,
            "control-refused",
            "renderer",
            format!(
                "the renderer refused {refused} control message{} from the supervisor and carried on",
                if refused > 1 { "s" } else { "" }
            ),
        ));
    }
    let carried: Vec<String> = faults_of(result)
        .iter()
        .filter_map(|f| {
            as_str(get(f, "where"))
                .filter(|w| *w != RUN_FAULT)
                .map(str::to_owned)
        })
        .collect();
    if !carried.is_empty() {
        out.push(Signal::new(
            at,
            "supervisor-fault",
            "supervisor",
            format!(
                "the supervisor hit an internal error and carried on: {}",
                carried.join(", ")
            ),
        ));
    }
    if as_str(error) == Some("KeyboardInterrupt") {
        return out;
    }
    if truthy(error) {
        let phase = get(result, "phase");
        let error_text = py_str(error);
        let kind = if as_str(phase) == Some("plugin") || error_text.starts_with("unload:") {
            "plugin-failed"
        } else if rc_value.is_null() && !end.started {
            "start-failed"
        } else {
            "run-failed"
        };
        out.push(Signal::new(
            at,
            kind,
            "runner",
            format!("the run stopped during {}: {}", py_str(phase), error_text),
        ));
    } else if *get(result, "complete") != Value::Bool(true) {
        out.push(Signal::new(
            at,
            "run-failed",
            "runner",
            "the run ended without finishing its recovery",
        ));
    }
    out
}

static UNIT_EXIT: LazyLock<Regex> =
    LazyLock::new(|| regex(r"Main process exited, code=(\S+), status=(\S+)"));
static UNIT_RESULT: LazyLock<Regex> = LazyLock::new(|| regex(r"Failed with result '([^']+)'"));

/// systemd's own last words on a unit, from its journal.
pub fn parse_unit_exit(lines: &[String]) -> Value {
    let mut out = Map::new();
    for line in lines {
        if let Some(m) = UNIT_EXIT.captures(line) {
            out = Map::new();
            out.insert("code".into(), m[1].into());
            out.insert("status".into(), m[2].into());
        }
        if let Some(m) = UNIT_RESULT.captures(line) {
            out.insert("result".into(), m[1].into());
        }
    }
    Value::Object(out)
}

static JOURNAL_LINE: LazyLock<Regex> =
    LazyLock::new(|| regex(r"\A(?:(?:\S+ ){1,2}?[^:\s]+: (.*))\z"));
const PRESENTATION: [&str; 11] = [
    "submitted",
    "presented",
    "discarded",
    "abandoned",
    "pending",
    "invalid_timing",
    "interval_count",
    "interval_mean_ns",
    "interval_min_ns",
    "interval_max_ns",
    "observation_duration_ns",
];
const CAPTURE: [&str; 5] = ["name", "frames", "failures", "reallocations", "path"];
const TRACKING: [&str; 6] = [
    "feed",
    "tracking",
    "samples",
    "lost_events",
    "reanchors",
    "hints",
];
const CONTROL: [&str; 2] = ["accepted", "refused"];
const REPORT_KINDS: [&str; 3] = [
    "generated_pattern_presentation_timing",
    "managed_output_capture",
    "control_stream",
];

/// The renderer exit report on one journal line, or None.
pub fn exit_report(line: &str) -> Option<Value> {
    let trimmed = line.trim_end_matches('\n');
    let body = JOURNAL_LINE
        .captures(trimmed)
        .map_or(line, |m| m.get(1).map_or(line, |b| b.as_str()));
    if !body.starts_with('{') {
        return None;
    }
    // json.loads, not strict: a repeated key keeps its last value. (Python also reads NaN here; serde_json does not.)
    let value: Value = serde_json::from_str(body).ok()?;
    as_str(get(&value, "kind")).filter(|k| REPORT_KINDS.contains(k))?;
    Some(value)
}

pub fn is_exit_report(line: &str) -> bool {
    exit_report(line).is_some()
}

fn pick(value: &Value, keys: &[&str]) -> Value {
    let mut out = Map::new();
    for key in keys {
        if let Some(v) = value.get(*key) {
            out.insert((*key).into(), v.clone());
        }
    }
    Value::Object(out)
}

/// The renderer's exit reports in a run's journal, the numbers only.
pub fn exit_metrics(lines: &[String]) -> Value {
    let mut out = Map::new();
    for line in lines {
        let Some(report) = exit_report(line) else {
            continue;
        };
        let kind = as_str(get(&report, "kind")).unwrap_or("");
        if kind == REPORT_KINDS[0] {
            out.insert("presentation".into(), pick(&report, &PRESENTATION));
        } else if kind == REPORT_KINDS[2] {
            out.insert("control".into(), pick(&report, &CONTROL));
        } else {
            let outputs = get(&report, "outputs");
            let capture: Vec<Value> = if truthy(outputs) {
                outputs
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|o| o.is_object())
                    .map(|o| pick(o, &CAPTURE))
                    .collect()
            } else {
                Vec::new()
            };
            out.insert("capture".into(), Value::Array(capture));
            let tracking = get(&report, "tracking");
            if tracking.is_object() {
                out.insert("tracking".into(), pick(tracking, &TRACKING));
            }
        }
    }
    Value::Object(out)
}

/// One incident: the signals that belong together. `C` is the run's context, for the report.
#[derive(Clone, Debug)]
pub struct Episode<C> {
    pub run_id: String,
    pub first: f64,
    pub last: f64,
    pub arrived_first: f64,
    pub arrived_last: f64,
    pub signals: Vec<Signal>,
    pub repeats: Vec<i64>,
    /// None: the session carried on; "ended", or "stopped" by a person.
    pub ended: Option<String>,
    pub ended_at: Option<f64>,
    /// Not before this: a core dump is on its way.
    pub hold_until: f64,
    pub context: Option<C>,
}

impl<C> Episode<C> {
    pub fn new(run_id: &str, at: f64) -> Episode<C> {
        Episode {
            run_id: run_id.into(),
            first: at,
            last: at,
            arrived_first: at,
            arrived_last: at,
            signals: Vec::new(),
            repeats: Vec::new(),
            ended: None,
            ended_at: None,
            hold_until: 0.0,
            context: None,
        }
    }

    /// The index of a signal seen at the same moment, if any.
    pub fn matching(&self, signal: &Signal) -> Option<usize> {
        self.signals
            .iter()
            .position(|seen| seen.same(signal) && (seen.at - signal.at).abs() < 1.0)
    }

    pub fn add(&mut self, signal: Signal, now: Option<f64>) {
        self.last = self.last.max(signal.at);
        self.first = self.first.min(signal.at);
        self.arrived_last = now.unwrap_or(signal.at);
        if let Some(i) = self.matching(&signal) {
            self.repeats[i] += 1;
            return;
        }
        if self.signals.len() < MAX_SIGNALS {
            self.signals.push(signal);
            self.repeats.push(0);
        }
    }

    pub fn kind(&self) -> &str {
        self.signals
            .iter()
            .map(|s| s.kind.as_str())
            .min_by_key(|k| rank(k))
            .unwrap_or("run-failed")
    }

    pub fn ready(&self, now: f64) -> bool {
        if now < self.hold_until {
            return false;
        }
        if let Some(ended_at) = self.ended_at {
            return now - ended_at >= AFTER_END_S;
        }
        now - self.arrived_last >= QUIET_S || now - self.arrived_first >= MAX_HOLD_S
    }
}

/// Turns a stream of signals into one incident per real event.
#[derive(Debug)]
pub struct Correlator<C> {
    pub open: Option<Episode<C>>,
    pub closed: Vec<Episode<C>>,
}

impl<C> Default for Correlator<C> {
    fn default() -> Self {
        Correlator {
            open: None,
            closed: Vec::new(),
        }
    }
}

impl<C> Correlator<C> {
    pub fn add(&mut self, signal: Signal, run_id: &str, context: Option<C>, now: Option<f64>) {
        if self.open.as_ref().is_some_and(|e| e.run_id != run_id) {
            self.closed.extend(self.open.take());
        }
        let open = self.open.get_or_insert_with(|| {
            let mut e = Episode::new(run_id, signal.at);
            e.arrived_first = now.unwrap_or(signal.at);
            e
        });
        if context.is_some() {
            open.context = context;
        }
        open.add(signal, now);
    }

    pub fn run_ended(&mut self, run_id: &str, at: f64, ended: &str, hold: f64) {
        if let Some(e) = self.open.as_mut().filter(|e| e.run_id == run_id) {
            e.ended = Some(ended.into());
            e.ended_at = Some(at);
            e.hold_until = at + hold;
        }
    }

    pub fn due(&mut self, now: f64) -> Vec<Episode<C>> {
        let mut done = std::mem::take(&mut self.closed);
        if self.open.as_ref().is_some_and(|e| e.ready(now)) {
            done.extend(self.open.take());
        }
        done
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::incident::py::NULL;

    #[test]
    fn whole_numbers_round_half_to_even_as_python_formats() {
        assert_eq!(whole(0.5), "0");
        assert_eq!(whole(2.5), "2");
        assert_eq!(whole(3.5), "4");
        assert_eq!(whole(59.6), "60");
    }

    #[test]
    fn signal_names_follow_linux() {
        assert_eq!(signal_name(11), "SIGSEGV");
        assert_eq!(signal_name(6), "SIGABRT");
        assert_eq!(signal_name(40), "signal 40");
    }

    #[test]
    fn kernel_log_learns_the_port_and_dedupes() {
        let mut log = KernelLog::default();
        let lines: Vec<String> = [
            "2026-11-03T23:53:20+00:00 h kernel: usb 5-2.1: New USB device found, idVendor=35ca, idProduct=1104",
            "2026-11-03T23:53:30+00:00 h kernel: usb 1-4: USB disconnect, device number 3",
            "2026-11-03T23:53:40+00:00 h kernel: usb 5-2: USB disconnect, device number 4",
            "2026-11-03T23:53:41+00:00 h kernel: usb 5-2.1: USB disconnect, device number 5",
        ]
        .map(String::from)
        .to_vec();
        let out = log.feed(&lines);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].at, 1793750020.0);
        assert_eq!(
            out[0].detail,
            "the kernel saw the glasses' USB port 5-2 disconnect"
        );
    }

    #[test]
    fn driver_log_needs_two_failures() {
        let mut log = DriverLog::default();
        assert!(
            log.feed("Failed to send USB command - MsgID: 0x100B\n", 10.0)
                .is_empty()
        );
        let out = log.feed("Failed to send USB command\nluma: disconn", 11.0);
        assert_eq!(out.len(), 1);
        let out = log.feed("ected at=12.5\n", 12.0);
        assert_eq!((out[0].at, out[0].kind.as_str()), (12.5, "usb-drop"));
    }

    #[test]
    fn exit_reports_from_journal_lines() {
        let line = r#"2026-11-03T22:33:20+00:00 host xr-renderer[12]: {"kind": "control_stream", "accepted": 4, "refused": 2, "x": 1}"#;
        let metrics = exit_metrics(&[line.to_owned()]);
        assert_eq!(metrics, json!({"control": {"accepted": 4, "refused": 2}}));
        assert!(!is_exit_report(
            "2026-11-03T22:33:20+00:00 host xr[1]: {\"kind\": \"other\"}"
        ));
    }

    #[test]
    fn a_crash_and_a_stop() {
        let log: Vec<String> = Vec::new();
        let end = End {
            started: true,
            stopped: false,
            stranded: false,
            unit: &NULL,
            log: &log,
        };
        let crash = classify_end(
            1.0,
            &json!({"renderer_returncode": -11, "complete": true}),
            end,
        );
        assert_eq!(
            crash,
            [Signal::new(
                1.0,
                "renderer-crash",
                "renderer",
                "the renderer crashed (SIGSEGV)"
            )]
        );
        let stopped = End {
            stopped: true,
            ..end
        };
        assert!(
            classify_end(
                1.0,
                &json!({"renderer_returncode": -15, "complete": true}),
                stopped
            )
            .is_empty()
        );
        let killed = classify_end(1.0, &NULL, end);
        assert_eq!(killed[0].kind, "run-killed");
    }
}
