//! privacy.py: Ctrl+Alt+D during a run blanks the main physical display, so only the glasses show content.
//!
//! A port of `tools/native_spike/privacy.py` (the Lua it sends is [`crate::lua::privacy`]'s). The main display is the
//! physical monitor that is neither the glasses' output, nor an XR desktop, nor one whose description names VITURE:
//! the run's fallback when it qualifies, else the only monitor that does ([`main_display`]).
//!
//! - DPMS, never disabling the monitor: a disabled monitor loses its workspaces, and the guard and recovery need the
//!   fallback connected and enabled. The blank is one eval with two dispatches, `off` for the main display and `on`
//!   for the glasses (already on), so the next key press does not wake every monitor again.
//! - A press follows the run's own state, not the display's: privacy off blanks (even an already sleeping display),
//!   privacy on wakes. Each change is told through `notify`, which lands in the glasses.
//! - While privacy is on, a watcher reads the display's `dpmsStatus` every [`WATCH`] and blanks it again if something
//!   else (an idle daemon, a hotplug) turned it on.
//! - [`PrivacyDisplay::close`] (run end, before the binds go and recovery starts) turns it back on, and recovery itself
//!   wakes the fallback first ([`wake`]; `managed::recover_restoring`), so a crash never leaves it dark.
//!
//! One difference from the Python: when `close` cannot take the lock within its timeout (a toggle stuck past three
//! compositor deadlines), the Python wakes the display without the lock; here the client is behind the lock, so close
//! leaves the wake to recovery, which does it first in every path.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use xr_wire::json::Value;

use super::immersive::{Done, complete};
use crate::hyprland::{self, Query};
use crate::lua::connector;

pub const PRIVACY_OP: &str = "privacy_display";
/// How often the watcher checks that a blanked display is still dark (one `monitors` query, only while privacy is on).
pub const WATCH: Duration = Duration::from_millis(250);
pub const PRIVACY_BIND: (&str, &str) = ("CTRL + ALT + D", PRIVACY_OP);
/// The compositor deadline of each query.
pub const DEADLINE: Duration = Duration::from_secs(2);

/// What privacy mode needs of the compositor: the monitors, and a DPMS change.
pub trait Displays: Send + 'static {
    /// Hyprland's `monitors all` JSON.
    fn monitors(&mut self, timeout: Duration) -> Result<Value, String>;
    /// `name` on, or off with `keep` (the glasses' output) kept on in the same eval.
    fn set_dpms(&mut self, name: &str, on: bool, keep: Option<&str>) -> Result<(), String>;
}

impl Displays for hyprland::Client {
    fn monitors(&mut self, timeout: Duration) -> Result<Value, String> {
        self.query(Query::Monitors, timeout)
            .map_err(|e| e.to_string())
    }

    fn set_dpms(&mut self, name: &str, on: bool, keep: Option<&str>) -> Result<(), String> {
        hyprland::Client::set_dpms(self, name, on, keep).map_err(|e| e.to_string())
    }
}

/// `(DP|HDMI-A|eDP|DVI-D)-[0-9]{1,3}`: supervisor.validate_physical_pair's connectors.
fn physical(name: &str) -> bool {
    connector(name, &["DP", "HDMI-A", "eDP", "DVI-D"], 1, 3)
}

/// `XR-[0-9]+`: a run's virtual desktop.
fn virtual_desktop(name: &str) -> bool {
    name.strip_prefix("XR-")
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
}

/// The main physical display's output name (module docs), or None. `monitors`: Hyprland's `monitors` JSON.
pub fn main_display(
    monitors: &Value,
    glasses: &str,
    fallback: Option<&str>,
) -> Result<Option<String>, String> {
    let Value::Array(monitors) = monitors else {
        return Err("invalid monitors".into());
    };
    let mut found = Vec::new();
    for m in monitors {
        let Some(name) = m.get("name").and_then(Value::as_str) else {
            continue;
        };
        if name == glasses || virtual_desktop(name) || !physical(name) {
            continue;
        }
        if m.get("description")
            .and_then(Value::as_str)
            .is_some_and(|d| d.to_lowercase().contains("viture"))
        {
            continue;
        }
        let mirrored = match m.get("mirrorOf") {
            None | Some(Value::Null) => false,
            Some(Value::String(s)) => s != "none",
            Some(_) => true,
        };
        if matches!(m.get("disabled"), Some(Value::Bool(true))) || mirrored {
            continue;
        }
        found.push(name.to_owned());
    }
    if let Some(fallback) = fallback.filter(|f| found.iter().any(|n| n == f)) {
        return Ok(Some(fallback.to_owned()));
    }
    Ok(if found.len() == 1 { found.pop() } else { None })
}

/// `name`'s DPMS state; None unless exactly one monitor of that name says.
pub fn dpms_status(monitors: &Value, name: &str) -> Option<bool> {
    let Value::Array(monitors) = monitors else {
        return None;
    };
    let mut found = monitors
        .iter()
        .filter(|m| m.get("name").and_then(Value::as_str) == Some(name));
    match (found.next(), found.next()) {
        (Some(m), None) => match m.get("dpmsStatus") {
            Some(Value::Bool(on)) => Some(*on),
            _ => None,
        },
        _ => None,
    }
}

/// Recovery: `name` back on if it is DPMS-off. Best effort, never fails; true if it was turned on.
pub fn wake(client: &mut impl Displays, name: &str, deadline: Duration) -> bool {
    if !physical(name) {
        return false;
    }
    let mut attempt = || -> Result<bool, String> {
        if dpms_status(&client.monitors(deadline)?, name) != Some(false) {
            return Ok(false);
        }
        client.set_dpms(name, true, None)?;
        Ok(dpms_status(&client.monitors(deadline)?, name) == Some(true))
    };
    attempt().unwrap_or(false)
}

/// Who hears a change: a desktop notification in the run.
pub type Notify = Box<dyn Fn(&str) + Send + Sync>;

struct State<C> {
    client: C,
    /// Privacy on: the output this run turned off.
    blanked: Option<String>,
    watching: bool,
}

struct Shared<C> {
    /// Held for a whole toggle or watcher check; close() waits on it.
    state: Mutex<State<C>>,
    closed: AtomicBool,
    stop: Mutex<bool>,
    stopped: Condvar,
    glasses: String,
    fallback: Option<String>,
    notify: Option<Notify>,
    deadline: Duration,
    watch: Duration,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// The supervisor's side of Ctrl+Alt+D: one toggle at a time on a worker thread; close() turns the display back on.
pub struct PrivacyDisplay<C: Displays> {
    shared: Arc<Shared<C>>,
}

impl<C: Displays> PrivacyDisplay<C> {
    pub fn new(
        client: C,
        glasses: &str,
        fallback: Option<&str>,
        notify: Option<Notify>,
        deadline: Duration,
        watch: Duration,
    ) -> Self {
        PrivacyDisplay {
            shared: Arc::new(Shared {
                state: Mutex::new(State {
                    client,
                    blanked: None,
                    watching: false,
                }),
                closed: AtomicBool::new(false),
                stop: Mutex::new(false),
                stopped: Condvar::new(),
                glasses: glasses.to_owned(),
                fallback: fallback.map(str::to_owned),
                notify,
                deadline,
                watch,
            }),
        }
    }

    /// From the supervisor loop: never blocks on the compositor.
    pub fn request(&self, done: Option<Done>) {
        if self.shared.closed.load(Ordering::SeqCst) {
            if let Some(done) = done {
                complete(done, false, "Privacy owner closed");
            }
            return;
        }
        let shared = Arc::clone(&self.shared);
        thread::spawn(move || work(&shared, done));
    }

    /// The output privacy mode has off, if it is on.
    pub fn blanked(&self) -> Option<String> {
        lock(&self.shared.state).blanked.clone()
    }

    /// Run end, before the binds go and recovery starts: no toggle starts after this, and the display comes back on.
    /// `timeout`: how long to wait for a toggle under way (default three compositor deadlines and half a second).
    pub fn close(&self, timeout: Option<Duration>) {
        let shared = &self.shared;
        shared.closed.store(true, Ordering::SeqCst);
        *lock(&shared.stop) = true;
        shared.stopped.notify_all();
        let until =
            Instant::now() + timeout.unwrap_or(shared.deadline * 3 + Duration::from_millis(500));
        let mut state = loop {
            match shared.state.try_lock() {
                Ok(state) => break state,
                Err(std::sync::TryLockError::Poisoned(p)) => break p.into_inner(),
                Err(std::sync::TryLockError::WouldBlock) if Instant::now() < until => {
                    thread::sleep(Duration::from_millis(5));
                }
                // A toggle stuck past its deadlines: recovery wakes the display first (module docs).
                Err(std::sync::TryLockError::WouldBlock) => return,
            }
        };
        if let Some(target) = state.blanked.take() {
            let _ = state.client.set_dpms(&target, true, None);
        }
    }
}

fn work<C: Displays>(shared: &Arc<Shared<C>>, done: Option<Done>) {
    let (success, message) = {
        let mut state = lock(&shared.state);
        if shared.closed.load(Ordering::SeqCst) {
            if let Some(done) = done {
                complete(done, false, "Privacy owner closed");
            }
            return;
        }
        match toggle(shared, &mut state) {
            Ok(result) => result,
            Err(why) if why.is_empty() => (false, "Could not change the main display.".to_owned()),
            Err(why) => (false, why),
        }
    };
    if let Some(done) = done {
        complete(done, success, &message);
    }
    if let Some(notify) = &shared.notify {
        if !message.is_empty() {
            notify(&message);
        }
    }
}

/// One press, now (the caller holds the lock): (success, the message for the user).
fn toggle<C: Displays>(
    shared: &Arc<Shared<C>>,
    state: &mut State<C>,
) -> Result<(bool, String), String> {
    let deadline = shared.deadline;
    let Some(target) = state.blanked.clone() else {
        let monitors = state.client.monitors(deadline)?;
        let Some(target) = main_display(&monitors, &shared.glasses, shared.fallback.as_deref())?
        else {
            return Ok((false, "No single main display found to turn off.".into()));
        };
        if dpms_status(&monitors, &target).is_none() {
            return Ok((false, format!("{target} is not available.")));
        }
        state
            .client
            .set_dpms(&target, false, Some(&shared.glasses))?;
        if dpms_status(&state.client.monitors(deadline)?, &target) != Some(false) {
            return Ok((false, format!("{target} did not turn off.")));
        }
        state.blanked = Some(target.clone());
        if !state.watching {
            state.watching = true;
            let shared = Arc::clone(shared);
            thread::spawn(move || watch(&shared));
        }
        return Ok((
            true,
            format!(
                "Privacy on: {target} is off, only the glasses show content. Ctrl+Alt+D turns it back on."
            ),
        ));
    };
    state.client.set_dpms(&target, true, None)?;
    if dpms_status(&state.client.monitors(deadline)?, &target) != Some(true) {
        return Ok((false, format!("{target} did not turn on.")));
    }
    state.blanked = None;
    Ok((true, format!("Privacy off: {target} is back on.")))
}

/// While privacy is on: a display something else turned on is blanked again.
fn watch<C: Displays>(shared: &Arc<Shared<C>>) {
    loop {
        {
            let stop = lock(&shared.stop);
            let (stop, _) = shared
                .stopped
                .wait_timeout_while(stop, shared.watch, |stop| !*stop)
                .unwrap_or_else(|p| p.into_inner());
            if *stop {
                return;
            }
        }
        let mut state = lock(&shared.state);
        let Some(target) = state
            .blanked
            .clone()
            .filter(|_| !shared.closed.load(Ordering::SeqCst))
        else {
            state.watching = false;
            return;
        };
        // The compositor busy or the monitor gone for now: the next check tries again.
        if let Ok(monitors) = state.client.monitors(shared.deadline) {
            if dpms_status(&monitors, &target) == Some(true) {
                let _ = state.client.set_dpms(&target, false, Some(&shared.glasses));
            }
        }
    }
}

#[cfg(test)]
mod tests;
