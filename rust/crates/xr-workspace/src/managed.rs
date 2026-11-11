//! The order of a managed run: start, stop, and recovery after a crash or a compositor exit.
//!
//! A port of the orchestration in `tools/native_spike/managed.py` (`_run_managed_impl`, `_run_owned`,
//! `recover_restoring`, `recover_managed`, `recover_ended`). Only the order lives here, with which failures stop a
//! run and which are kept going past. What each step does to the compositor, the broker or the disk is the [`Host`]'s,
//! so the journal, the Hyprland client and the run loop can be ported on their own and plugged in.
//!
//! The invariants the order carries (docs/rust-rebuild-plan.md, "Invariants the rewrite must not weaken"):
//!
//! - Nothing is changed before the broker is open and owns the outputs, and the journal is bound before the first
//!   output is created.
//! - Once anything was created, recovery runs whatever ended the run: a failed step, a failed renderer or a Stop.
//! - Recovery wakes the fallback display first (privacy mode may have left it DPMS-off), then asks the glasses out of
//!   side-by-side, then puts the windows back, and only then undoes the journal, whose outputs go last.
//!
//! `rust/corpus/supervisor/run_order.json` holds the Python's ordered steps for each path and each failure point;
//! the tests below replay every case and must give the same steps and the same result.

use std::path::{Path, PathBuf};

/// A step's failure, or a refusal, in the words the Python gives.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);

impl Error {
    fn new(text: &str) -> Self {
        Error(text.to_owned())
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

pub const PRO2_UNSUPPORTED: &str =
    "VITURE PRO 2 driver and display support unverified; managed operations unavailable";
pub const EXISTING_RUN: &str = "existing run requires explicit recovery; use a fresh run id";
pub const NOT_CANONICAL: &str = "managed recovery requires the canonical runtime journal";
pub const SCOPE_MISMATCH: &str = "consent does not match saved broker scope";
pub const INVALID_IDENTIFIER: &str = "invalid identifier";
pub const TRACKING_TIMEOUT_TEXT: &str =
    "Head tracking did not come back, so XR stopped. Your windows are back on your monitor.";

/// The consent fields the order reads.
#[derive(Debug, Clone)]
pub struct Consent {
    pub run_id: String,
    pub model: String,
    pub instance: String,
    pub output: String,
    pub recovery_output: String,
    /// A capture (panel) run: its windows are adopted and its desktops have pills.
    pub capture: bool,
}

/// The broker scope a journal is bound to: which compositor, glasses output and fallback.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scope {
    pub instance: String,
    pub output: String,
    pub recovery_output: String,
}

impl Consent {
    fn scope(&self) -> Scope {
        Scope {
            instance: self.instance.clone(),
            output: self.output.clone(),
            recovery_output: self.recovery_output.clone(),
        }
    }
}

/// One XR output the run creates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    pub id: String,
    pub name: String,
}

/// What the journal's recovery left, and why.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Recovery {
    pub complete: bool,
    pub remaining: Vec<String>,
    pub reasons: Vec<String>,
}

/// A run that ended and was recovered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunOutcome {
    pub renderer_returncode: i32,
    pub stop_reason: Option<String>,
    pub recovery: Recovery,
}

/// Options of a start that are not in the consent.
#[derive(Debug, Clone, Copy, Default)]
pub struct RunOptions {
    /// The desktops are a saved setup: one the broker cannot create goes back to the default.
    pub saved_setup: bool,
}

/// Every step the order takes. Steps that return `Result` can stop the order; the others never fail.
pub trait Host {
    /// The pre-XR window layout.
    type Snapshot;

    fn verified_session(&mut self) -> Result<String>;
    /// Read-only check of the glasses and fallback connectors. `asleep_ok`: the fallback may be DPMS-off.
    fn validate_physical_pair(
        &mut self,
        session: &str,
        output: &str,
        fallback: &str,
        asleep_ok: bool,
    ) -> Result<()>;
    fn plan_outputs(&mut self, run_id: &str) -> Result<Vec<Spec>>;
    fn broker_open(&mut self, run_id: &str, session: &str, fallback: &str) -> Result<()>;
    fn broker_from_journal(&mut self) -> Result<()>;
    fn require_output_ownership(&mut self) -> Result<()>;
    /// A private directory under the runtime directory, made if missing.
    fn private_dir(&mut self, relative: &Path) -> Result<()>;
    fn lock_owner(&mut self) -> Result<()>;
    fn unlock_owner(&mut self);

    fn journal_open(&mut self, run_id: &str, session: &str, existing_only: bool) -> Result<()>;
    /// The session saved in an existing journal.
    fn journal_read_session(&mut self) -> Result<String>;
    /// No operations and no broker yet.
    fn journal_is_fresh(&mut self) -> Result<bool>;
    fn journal_bind_broker(&mut self, scope: &Scope) -> Result<()>;
    /// The scope the journal is bound to, if any. A read of what is already loaded.
    fn journal_scope(&self) -> Option<Scope>;
    fn journal_output_names(&mut self) -> Result<Vec<String>>;
    /// Undo the journal: reverse order, outputs last.
    fn journal_recover(&mut self) -> Result<Recovery>;
    fn journal_compositor_ended(&mut self) -> Result<()>;

    fn create_outputs(&mut self, specs: &[Spec]) -> Result<()>;
    /// A saved setup the broker refused goes back to the default.
    fn discard_unusable_setup(&mut self, error: &Error) -> Result<()>;
    fn desktop_manager(&mut self, specs: &[Spec]) -> Result<()>;
    fn broker_verify(&mut self, spec: &Spec) -> Result<()>;
    fn take_layout(&mut self) -> Option<Self::Snapshot>;
    /// Window memory for these desktops; false when there is none.
    fn window_memory(&mut self, names: &[String]) -> Result<bool>;
    fn window_memory_place(&mut self, names: &[String]) -> Result<()>;
    fn adopt(&mut self, names: &[String]) -> Result<()>;
    fn window_memory_finish(&mut self) -> Result<()>;
    fn cursor_reader(&mut self) -> Result<()>;
    fn view_controls_enter(&mut self) -> Result<()>;
    fn view_controls_exit(&mut self) -> Result<()>;
    fn continuity_open(&mut self) -> Result<()>;
    fn continuity_close(&mut self) -> Result<()>;
    /// The renderer, relaunched as the run loop decides, until the run ends: its last return code. On a capture run
    /// each launch captures the desktops as they are then, guarded by the desktop manager's live desktops; otherwise
    /// it captures nothing and the start's guard holds.
    fn keep_renderer(&mut self, capture: bool) -> Result<i32>;
    /// Why the supervisor ended the renderer, if it did.
    fn stop_reason(&self) -> Option<String>;

    fn wake(&mut self, output: &str);
    fn restore_2d(&mut self, output: &str);
    fn clear_monitor_rules(&mut self, names: &[String]) -> Vec<String>;
    fn named_monitor_rules(&mut self, names: &[String]) -> Vec<String>;
    fn unpin(&mut self);
    fn load_layout(&mut self) -> Result<Option<Self::Snapshot>>;
    fn restore_layout(&mut self, snapshot: &Self::Snapshot);
    fn discard_layout(&mut self) -> Result<()>;
    fn notify(&mut self, text: &str);
    /// The windows still on `names`, for the reasons of a recovery that did not finish.
    fn left_on(&mut self, names: &[String]) -> Vec<String>;
}

fn supported_model(consent: &Consent) -> Result<()> {
    if consent.model == "pro2" {
        Err(Error::new(PRO2_UNSUPPORTED))
    } else {
        Ok(())
    }
}

/// `ipc.token`: a run id that is safe as a path component.
fn token(value: &str) -> Result<&str> {
    let mut chars = value.chars();
    let first = chars.next().is_some_and(|c| c.is_ascii_alphanumeric());
    let rest = chars.all(|c| c.is_ascii_alphanumeric() || "_.:-".contains(c));
    if first && rest && value.len() <= 128 {
        Ok(value)
    } else {
        Err(Error::new(INVALID_IDENTIFIER))
    }
}

fn canonical_journal(runtime_dir: &Path, consent: &Consent, path: &Path) -> Result<PathBuf> {
    let canonical = runtime_dir
        .join("xr-workspace-spike")
        .join(token(&consent.run_id)?)
        .join("journal.json");
    match std::path::absolute(path) {
        Ok(given) if given == canonical => Ok(canonical),
        _ => Err(Error::new(NOT_CANONICAL)),
    }
}

/// Start a managed run, keep its renderer until it ends, then recover.
pub fn run_managed<H: Host>(
    host: &mut H,
    consent: &Consent,
    options: RunOptions,
) -> Result<RunOutcome> {
    supported_model(consent)?;
    let session = host.verified_session()?;
    host.validate_physical_pair(&session, &consent.output, &consent.recovery_output, false)?;
    let specs = host.plan_outputs(&consent.run_id)?;
    host.broker_open(&consent.run_id, &session, &consent.recovery_output)?;
    host.require_output_ownership()?; // no journal or output change without a broker
    let directory = PathBuf::from("xr-workspace-spike");
    host.private_dir(&directory)?;
    host.private_dir(&directory.join(&consent.run_id))?;
    host.lock_owner()?;
    let run = host
        .journal_open(&consent.run_id, &session, false)
        .and_then(|()| run_owned(host, consent, &session, &specs, options));
    host.unlock_owner();
    run
}

fn run_owned<H: Host>(
    host: &mut H,
    consent: &Consent,
    session: &str,
    specs: &[Spec],
    options: RunOptions,
) -> Result<RunOutcome> {
    if !host.journal_is_fresh()? {
        return Err(Error::new(EXISTING_RUN));
    }
    host.journal_bind_broker(&consent.scope())?;
    // From here on, recovery runs whatever happens.
    let mut snapshot = None;
    let ran = start_and_keep(host, consent, session, specs, options, &mut snapshot);
    let recovery = recover_restoring(host, snapshot, &[]);
    let stop_reason = host.stop_reason();
    if recovery.is_ok() && stop_reason.as_deref() == Some("tracking-timeout") {
        host.notify(TRACKING_TIMEOUT_TEXT);
    }
    let recovery = recovery?; // a recovery error stands in place of the run's own
    Ok(RunOutcome {
        renderer_returncode: ran?,
        stop_reason,
        recovery,
    })
}

fn start_and_keep<H: Host>(
    host: &mut H,
    consent: &Consent,
    session: &str,
    specs: &[Spec],
    options: RunOptions,
    snapshot: &mut Option<H::Snapshot>,
) -> Result<i32> {
    if let Err(error) = host.create_outputs(specs) {
        if options.saved_setup {
            let _ = host.discard_unusable_setup(&error); // never in place of the create failure itself
        }
        return Err(error);
    }
    let capture = consent.capture;
    if capture {
        host.desktop_manager(specs)?; // the pills' Ultrawide / Add / Remove
    }
    // The guard. The fallback may be DPMS-off during a run (privacy mode), so its readback holds asleep too.
    host.validate_physical_pair(session, &consent.output, &consent.recovery_output, true)?;
    for spec in specs {
        host.broker_verify(spec)?;
    }
    if capture {
        // Once, before the renderer starts: the pre-XR layout, then the remembered windows, then adoption.
        *snapshot = host.take_layout();
        let names: Vec<String> = specs.iter().map(|s| s.name.clone()).collect();
        let memory = host.window_memory(&names)?;
        if memory {
            host.window_memory_place(&names)?;
        }
        host.adopt(&names)?;
        if memory {
            host.window_memory_finish()?;
        }
    }
    host.cursor_reader()?;
    host.view_controls_enter()?;
    let kept = keep(host, capture);
    host.view_controls_exit()?; // whatever the renderer did; its own failure stands in place of the renderer's
    kept
}

fn keep<H: Host>(host: &mut H, capture: bool) -> Result<i32> {
    host.continuity_open()?;
    let kept = host.keep_renderer(capture);
    host.continuity_close()?;
    kept
}

/// Stop and Recover: the fallback awake and the glasses out of 3D first, the run's monitor rules, the windows back
/// while the XR outputs still exist, then the journal. `clear_rules`: the desktops whose monitor rule a person agreed
/// to have taken out.
pub fn recover_restoring<H: Host>(
    host: &mut H,
    snapshot: Option<H::Snapshot>,
    clear_rules: &[String],
) -> Result<Recovery> {
    if let Some(saved) = host.journal_scope() {
        host.wake(&saved.recovery_output);
        if !saved.output.is_empty() {
            host.restore_2d(&saved.output);
        }
    }
    let names = host.journal_output_names()?;
    let (agreed, others): (Vec<String>, Vec<String>) =
        names.iter().cloned().partition(|n| clear_rules.contains(n));
    let mut unsolved = host.clear_monitor_rules(&agreed);
    unsolved.extend(host.named_monitor_rules(&others));
    host.unpin();
    let snapshot = match snapshot {
        Some(snapshot) => Some(snapshot),
        None => host.load_layout().unwrap_or(None),
    };
    if let Some(snapshot) = &snapshot {
        host.restore_layout(snapshot);
    }
    let mut result = host.journal_recover()?;
    if result.complete {
        let _ = host.discard_layout();
        for text in &unsolved {
            host.notify(text);
        }
    } else {
        let mut reasons = unsolved;
        reasons.extend(host.left_on(&names));
        reasons.append(&mut result.reasons);
        result.reasons = reasons;
    }
    Ok(result)
}

/// Recovery from a run's journal while its compositor is still running.
pub fn recover_managed<H: Host>(
    host: &mut H,
    consent: &Consent,
    runtime_dir: &Path,
    journal: &Path,
    clear_rules: &[String],
) -> Result<Recovery> {
    supported_model(consent)?;
    canonical_journal(runtime_dir, consent, journal)?;
    let session = host.verified_session()?;
    host.wake(&consent.recovery_output); // a crash in privacy mode left it DPMS-off
    host.restore_2d(&consent.output); // and a crash in 3D the glasses side by side
    host.validate_physical_pair(&session, &consent.output, &consent.recovery_output, false)?;
    host.lock_owner()?;
    let recovered = (|| {
        host.journal_open(&consent.run_id, &session, true)?;
        host.broker_from_journal()?;
        if host.journal_scope() != Some(consent.scope()) {
            return Err(Error::new(SCOPE_MISMATCH));
        }
        recover_restoring(host, None, clear_rules)
    })();
    host.unlock_owner();
    recovered
}

/// Recovery of a run whose compositor has exited: its outputs and changes went with it, so the journal is closed
/// and the layout snapshot discarded.
pub fn recover_ended<H: Host>(
    host: &mut H,
    consent: &Consent,
    runtime_dir: &Path,
    journal: &Path,
) -> Result<Recovery> {
    canonical_journal(runtime_dir, consent, journal)?;
    host.lock_owner()?;
    let ended = (|| {
        let session = host.journal_read_session()?;
        host.journal_open(&consent.run_id, &session, true)?;
        if host.journal_scope() != Some(consent.scope()) {
            return Err(Error::new(SCOPE_MISMATCH));
        }
        host.journal_compositor_ended()
    })();
    host.unlock_owner();
    ended?;
    host.discard_layout()?;
    Ok(Recovery {
        complete: true,
        ..Recovery::default()
    })
}

pub mod real;
#[cfg(test)]
mod tests;
