//! `sbs.ButtonSbs` (tools/native_spike/sbs.py): the glasses' side-by-side mode in a run with the consent's `sbs_mode`
//! scope, which [`crate::supervisor::immersive::ImmersiveRun`] drives through its [`Sbs`] interface. Nothing here
//! touches USB: a person holds the glasses' button, and this follows what Hyprland then says about the output (its
//! offered modes), asks for the wide mode with one rule (read back), and parks the output after each change of width.
//!
//! The compositor side is [`Display`]: the run's own [`Client`] (pinned to the run's compositor, as the other view
//! controls' workers are), or a recording stand-in in the corpus replay (`tests/sbs_corpus.rs`).
//!
//! Differences from the Python class, all deliberate:
//! - No `glasses` switch: a Pro 2 bridge's SDK is not part of a Rust run, so every change asks for the button.
//! - A mode Hyprland lists that matches `sbs.MODE` but whose rate is not a number (`1.2.3Hz`) is no mode here; the
//!   Python's `float()` raises out of `offered` instead.

use std::sync::{Mutex, MutexGuard};
use std::time::Duration;

use crate::hyprland::{self, Client, Monitor, Query};
use crate::supervisor::immersive::{Sbs, SbsError};

/// `sbs.ENTER_WAIT_S`: to find the button, wearing the glasses.
pub const ENTER_WAIT_S: f64 = 45.0;
/// `sbs.POLL_S`.
pub const POLL_S: f64 = 0.25;
/// `sbs.EXPECT_S`: a renderer ending this soon after a change of mode is that change, not a fault.
pub const EXPECT_S: f64 = 20.0;
/// `sbs.PROMPT_ON` / `sbs.PROMPT_OFF`.
pub const PROMPT_OFF: &str = "Hold R1 on the glasses to go back to 2D and 120 Hz.";
const PRO2_PROMPT_OFF: &str = "Hold L1 on the glasses to go back to 2D.";
const HAND_WIDE: &str = "The glasses are in 3D. Click 3D to enable desktop depth.";

/// `sbs.trace`: one stderr line per step of a 2D/3D switch, on CLOCK_MONOTONIC.
pub fn trace(step: &str) {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid clock id and a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    let t = ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9;
    eprintln!("xr-workspace: switch: t={t:.3} {step}");
}

/// What `ButtonSbs` does to the compositor: reads the monitor list, asks for one mode, parks the output. An error is
/// its message (Python's `str(exc)`), or its class's name when that is empty.
pub trait Display: Send {
    /// `monitors(client.query('monitors'))`; `None` when the query or its check failed.
    fn monitors(&mut self) -> Option<Vec<Monitor>>;
    fn apply_wide_mode(&mut self, output: &str, rate: f64, model: &str) -> Result<(), String>;
    fn apply_pro2_mode(&mut self, output: &str, rate: f64) -> Result<(), String>;
    fn apply_session_mode(&mut self, output: &str) -> Result<(), String>;
    fn park_presentation(&mut self, output: &str) -> Result<(), String>;
}

fn message(e: hyprland::Error) -> String {
    let text = e.to_string();
    if !text.is_empty() {
        return text;
    }
    match e {
        hyprland::Error::Value(_) => "ValueError",
        hyprland::Error::Permission(_) => "PermissionError",
        hyprland::Error::Timeout(_) => "TimeoutError",
        hyprland::Error::Io(_) => "OSError",
    }
    .to_owned()
}

impl Display for Client {
    fn monitors(&mut self) -> Option<Vec<Monitor>> {
        let raw = self.query(Query::Monitors, hyprland::DEADLINE).ok()?;
        hyprland::monitors(&raw).ok()
    }
    fn apply_wide_mode(&mut self, output: &str, rate: f64, model: &str) -> Result<(), String> {
        Client::apply_wide_mode(self, output, rate, model)
            .map(drop)
            .map_err(message)
    }
    fn apply_pro2_mode(&mut self, output: &str, rate: f64) -> Result<(), String> {
        Client::apply_pro2_mode(self, output, rate)
            .map(drop)
            .map_err(message)
    }
    fn apply_session_mode(&mut self, output: &str) -> Result<(), String> {
        Client::apply_session_mode(self, output)
            .map(drop)
            .map_err(message)
    }
    fn park_presentation(&mut self, output: &str) -> Result<(), String> {
        Client::park_presentation(self, output)
            .map(drop)
            .map_err(message)
    }
}

// --- the modes (sbs.offered, best_wide, is_wide, narrow_rate, pro2_2d_rate) --------------------------------------

/// `sbs.offered`: every (width, height, hz) Hyprland lists for this monitor (`MODE`: `WxH@RHz`, whole string).
pub fn offered(monitor: Option<&Monitor>) -> Vec<(u64, u64, f64)> {
    let Some(xr_wire::json::Value::Array(modes)) = monitor.and_then(|m| m.raw.get("availableModes")) else {
        return Vec::new();
    };
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let mut out = Vec::new();
    for text in modes.iter().filter_map(xr_wire::json::Value::as_str) {
        let Some((size, hz)) = text.strip_suffix("Hz").and_then(|b| b.split_once('@')) else {
            continue;
        };
        let Some((w, h)) = size.split_once('x') else {
            continue;
        };
        if !digits(w) || !digits(h) || hz.is_empty() || !hz.bytes().all(|b| b.is_ascii_digit() || b == b'.') {
            continue;
        }
        if let (Ok(w), Ok(h), Ok(hz)) = (w.parse(), h.parse(), hz.parse()) {
            out.push((w, h, hz));
        }
    }
    out
}

/// `sbs._mode`: the model's wide shape and its rates, best first.
fn mode(model: &str) -> Option<((u64, u64), &'static [f64])> {
    let ((w, h), rates) = crate::lua::sbs::mode(model).ok()?;
    Some(((u64::from(w), u64::from(h)), rates))
}

/// `sbs.best_wide`: the rate to ask for, or None while the glasses do not offer the model's side-by-side mode.
pub fn best_wide(monitor: Option<&Monitor>, model: &str) -> Option<f64> {
    let (wide, rates) = mode(model)?;
    let modes = offered(monitor);
    rates.iter().copied().find(|rate| {
        modes
            .iter()
            .any(|&(w, h, hz)| (w, h) == wide && (hz - rate).abs() < 0.5)
    })
}

/// `sbs.is_wide`: being driven at the model's side-by-side size now.
pub fn is_wide(monitor: Option<&Monitor>, model: &str) -> bool {
    match (monitor, mode(model)) {
        (Some(m), Some((wide, _))) => (m.width, m.height) == (wide.0 as i64, wide.1 as i64),
        _ => false,
    }
}

/// `sbs.narrow_rate`: the observed, supported 2D refresh, never a guessed rate for a wide or absent output.
pub fn narrow_rate(monitor: Option<&Monitor>, model: &str) -> Option<f64> {
    let (wide, _) = mode(model)?;
    let m = monitor?;
    if (m.width, m.height) != ((wide.0 / 2) as i64, wide.1 as i64) {
        return None;
    }
    let allowed: &[f64] = if model == "pro2" {
        &[60.0, 90.0, 120.0]
    } else {
        &[120.0]
    };
    let rate = m.refresh_rate;
    (rate.is_finite() && allowed.iter().any(|hz| (rate - hz).abs() < 0.5)).then_some(rate)
}

/// `sbs.pro2_2d_rate`: the 1920x1080 rate a Pro 2 back in 2D is to be driven at, or None while it is not back.
pub fn pro2_2d_rate(monitor: Option<&Monitor>, prefer: Option<f64>) -> Option<f64> {
    let m = monitor?;
    if best_wide(Some(m), "pro2").is_some() {
        return None;
    }
    let rates: Vec<f64> = offered(Some(m))
        .into_iter()
        .filter(|&(w, h, _)| (w, h) == (1920, 1080))
        .map(|(_, _, hz)| hz)
        .collect();
    let allowed: Vec<f64> = [120.0, 90.0, 60.0]
        .into_iter()
        .filter(|hz: &f64| rates.iter().any(|r| (r - hz).abs() < 0.5))
        .collect();
    if let Some(p) = prefer
        && allowed.iter().any(|hz| (p - hz).abs() < 0.5)
    {
        return Some(p);
    }
    narrow_rate(Some(m), "pro2").or(allowed.first().copied())
}

/// Python's `f'{rate:g}'` for the rates here.
fn g(rate: f64) -> String {
    crate::lua::py_g(rate)
}

// --- ButtonSbs ----------------------------------------------------------------------------------------------------

#[derive(Default)]
struct State {
    narrow_refresh: Option<f64>,
    /// When leave() last asked for the button: the glasses are still wide, until settle() sees otherwise.
    left_at: Option<f64>,
    expect_until: f64,
    hand_wide: bool,
}

/// The clock and the sleep, the Python's `clock` and `sleep` (seconds).
pub type Clock = Box<dyn Fn() -> f64 + Send + Sync>;
pub type Sleep = Box<dyn Fn(f64) + Send + Sync>;
pub type Tell = Box<dyn Fn(&str) + Send + Sync>;

/// `sbs.ButtonSbs`: everything it does to Hyprland is reading the monitor list, and one mode rule.
pub struct ButtonSbs {
    display: Mutex<Box<dyn Display>>,
    output: String,
    model: String,
    wide: (u64, u64),
    button: &'static str,
    tell: Tell,
    clock: Clock,
    sleep: Sleep,
    enter_wait: f64,
    state: Mutex<State>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

pub fn monotonic() -> f64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid clock id and a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

impl ButtonSbs {
    /// `ButtonSbs(client, output, tell, model=model)` on the real clock. `None` for a model with no 3D mode (the
    /// Python's `ValueError`).
    pub fn new(display: Box<dyn Display>, output: &str, tell: Tell, model: &str) -> Option<ButtonSbs> {
        ButtonSbs::with_clock(
            display,
            output,
            tell,
            model,
            Box::new(monotonic),
            Box::new(|s| std::thread::sleep(Duration::from_secs_f64(s))),
            ENTER_WAIT_S,
        )
    }

    /// [`ButtonSbs::new`] with its clock, sleep and wait given (the Python's `clock`, `sleep`, `enter_wait`).
    pub fn with_clock(
        display: Box<dyn Display>,
        output: &str,
        tell: Tell,
        model: &str,
        clock: Clock,
        sleep: Sleep,
        enter_wait: f64,
    ) -> Option<ButtonSbs> {
        let (wide, _) = mode(model)?;
        Some(ButtonSbs {
            display: Mutex::new(display),
            output: output.to_owned(),
            model: model.to_owned(),
            wide,
            button: if model == "pro2" { "L1" } else { "R1" },
            tell,
            clock,
            sleep,
            enter_wait,
            state: Mutex::new(State::default()),
        })
    }

    /// `_monitor`: the output, or None while it is away (a press may do that).
    fn monitor(&self) -> Option<Monitor> {
        let monitors = lock(&self.display).monitors()?;
        let mut found = monitors.into_iter().filter(|m| m.name == self.output);
        let first = found.next()?;
        found.next().is_none().then_some(first)
    }

    fn expect(&self) {
        let until = (self.clock)() + EXPECT_S;
        lock(&self.state).expect_until = until;
    }

    fn park(&self) -> Result<(), String> {
        lock(&self.display).park_presentation(&self.output)
    }

    /// `_apply`: the wide rule, read back, then parked.
    fn apply(&self, rate: f64) -> Result<(), SbsError> {
        trace(&format!("enter: wide rule {}x{}@{}", self.wide.0, self.wide.1, g(rate)));
        let model = if self.model == "pro2" { "pro2" } else { "luma-ultra" };
        let applied = lock(&self.display).apply_wide_mode(&self.output, rate, model);
        applied.map_err(SbsError)?;
        trace("enter: wide mode read back");
        self.park().map_err(SbsError)?;
        trace("enter: parked");
        Ok(())
    }

    fn is_switching(&self, st: &State) -> bool {
        let now = (self.clock)();
        st.left_at.is_some_and(|at| now - at < ENTER_WAIT_S) || now < st.expect_until
    }

    /// `settle`: glasses still wide after leave(): true once they have gone back, with the session mode on again.
    pub fn settle_now(&self) -> Result<bool, SbsError> {
        let Some(monitor) = self.monitor() else {
            return Ok(false); // the press took the output: survive.py is on it
        };
        let m = Some(&monitor);
        if self.model == "pro2" {
            let prefer = lock(&self.state).narrow_refresh;
            let Some(rate) = pro2_2d_rate(m, prefer) else {
                return Ok(false);
            };
            let current = narrow_rate(m, &self.model);
            let shown = current.map_or_else(|| "None".to_owned(), crate::pyjson::float_repr);
            trace(&format!("settle: 2D offered, driven {shown}, want {}", g(rate)));
            if current.is_none_or(|c| (c - rate).abs() >= 0.5) {
                let applied = lock(&self.display).apply_pro2_mode(&self.output, rate);
                if let Err(e) = applied {
                    trace(&format!("settle: 2D rule failed: {e}"));
                    return Ok(false);
                }
                trace("settle: 2D mode read back");
            }
            self.park().map_err(SbsError)?;
            trace("settle: parked");
            lock(&self.state).left_at = None;
            return Ok(true);
        }
        if best_wide(m, &self.model).is_some() || is_wide(m, &self.model) {
            return Ok(false);
        }
        lock(&self.state).left_at = None;
        // survive.py's restore tries as well; a run does not end here.
        let applied = lock(&self.display).apply_session_mode(&self.output);
        if applied.is_ok() {
            let _ = self.park();
        }
        Ok(true)
    }
}

impl Sbs for ButtonSbs {
    /// Until the glasses are wide and driven wide; false if `cancelled()` came true first. An error when the button
    /// was not held in time or the mode would not take.
    fn enter(&self, cancelled: &dyn Fn() -> bool) -> Result<bool, SbsError> {
        lock(&self.state).left_at = None;
        let mut monitor = self.monitor();
        if let Some(n) = narrow_rate(monitor.as_ref(), &self.model) {
            lock(&self.state).narrow_refresh = Some(n);
        }
        let mut rate = best_wide(monitor.as_ref(), &self.model);
        if rate.is_none() {
            (self.tell)(&format!("Hold {} on the glasses to switch them to 3D.", self.button));
            self.expect();
            let deadline = (self.clock)() + self.enter_wait;
            let mut narrow: Vec<(u64, u64)> = Vec::new();
            while rate.is_none() {
                if cancelled() {
                    return Ok(false);
                }
                if (self.clock)() >= deadline {
                    let shape = format!("{}x{}", self.wide.0, self.wide.1);
                    return Err(SbsError(if narrow.is_empty() {
                        format!("the glasses never offered {shape}: was {} held?", self.button)
                    } else {
                        let list: Vec<String> = narrow.iter().map(|(w, h)| format!("{w}x{h}")).collect();
                        format!("they offered only {}, and 3D here needs {shape}", list.join(", "))
                    }));
                }
                (self.sleep)(POLL_S);
                self.expect();
                monitor = self.monitor();
                rate = best_wide(monitor.as_ref(), &self.model);
                if rate.is_some() {
                    trace("enter: wide mode offered");
                }
                let mut seen: Vec<(u64, u64)> = offered(monitor.as_ref())
                    .into_iter()
                    .filter(|&(w, h, _)| w >= 2 * h)
                    .map(|(w, h, _)| (w, h))
                    .collect();
                seen.sort_unstable();
                seen.dedup();
                if !seen.is_empty() {
                    narrow = seen;
                }
            }
        }
        let rate = rate.expect("the loop ends with a rate");
        self.expect();
        let m = monitor.as_ref().expect("a rate is offered by a monitor");
        if !is_wide(Some(m), &self.model) || (m.refresh_rate - rate).abs() >= 0.5 {
            self.apply(rate)?;
        }
        lock(&self.state).hand_wide = true;
        Ok(true)
    }

    /// Never waits: the renderer is flat by now. Asks for the button; settle() follows. Glasses that are not wide:
    /// nothing.
    fn leave(&self) -> Result<(), SbsError> {
        let monitor = self.monitor();
        if best_wide(monitor.as_ref(), &self.model).is_none() && !is_wide(monitor.as_ref(), &self.model) {
            return Ok(());
        }
        let ask = {
            let mut st = lock(&self.state);
            let ask = st.left_at.is_none();
            if ask {
                st.left_at = Some((self.clock)());
            }
            ask
        };
        if ask {
            (self.tell)(if self.model == "pro2" { PRO2_PROMPT_OFF } else { PROMPT_OFF });
        }
        self.expect();
        Ok(())
    }

    fn parked(&self) -> bool {
        lock(&self.state).left_at.is_some()
    }

    fn settle(&self) -> Result<(), SbsError> {
        self.settle_now().map(drop)
    }

    /// 3D is up, but the mode button was held: the glasses are back in 2D on their own. False while the output is
    /// away: that is a hold.
    fn left_by_hand(&self) -> bool {
        let monitor = self.monitor();
        let m = monitor.as_ref();
        let (left, narrow) = if self.model == "pro2" {
            let back = pro2_2d_rate(m, None).is_some();
            (back, back)
        } else {
            (
                m.is_some() && best_wide(m, &self.model).is_none() && !is_wide(m, &self.model),
                narrow_rate(m, &self.model).is_some(),
            )
        };
        if left && narrow {
            self.expect();
            let now = (self.clock)();
            let mut st = lock(&self.state);
            st.hand_wide = false;
            st.left_at.get_or_insert(now);
        }
        left
    }

    /// A change of mode is under way or just happened, so the output going away is that and not a drop.
    fn switching(&self) -> bool {
        self.is_switching(&lock(&self.state))
    }

    /// The glasses changed mode with their own button while 3D was off: the 2D rate is kept, and the change expected.
    fn follow_by_hand(&self) {
        let Some(monitor) = self.monitor() else {
            return;
        };
        let wide = is_wide(Some(&monitor), &self.model);
        let narrow_refresh = narrow_rate(Some(&monitor), &self.model);
        if !wide && narrow_refresh.is_none() {
            return;
        }
        let (hand_wide, switching) = {
            let st = lock(&self.state);
            (st.hand_wide, self.is_switching(&st))
        };
        if !hand_wide && !switching && narrow_refresh.is_some() {
            lock(&self.state).narrow_refresh = narrow_refresh;
        }
        if wide && !hand_wide && !switching {
            (self.tell)(HAND_WIDE);
        }
        if wide != hand_wide {
            self.expect();
        }
        lock(&self.state).hand_wide = wide;
    }

    fn button(&self) -> &str {
        self.button
    }
}
