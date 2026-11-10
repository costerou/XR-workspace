//! Circle only: in the overview the free pointer's dot is the only pointer shown, and the real cursor is hidden.
//!
//! A port of `tools/native_spike/overview_cursor.py` `OverviewCursor`. The Lua is [`crate::lua::overview_cursor`]. The
//! sampler thread makes these calls (see [`super::sampler`]), and only when the wanted state changes. The state starts
//! unknown, so a run's first sample always sends one call, which puts back a cursor that a killed run left hidden. A
//! config reload resets `cursor.invisible`, so the next sample sends again, at most once per [`RELOAD_REAPPLY_S`]. Later
//! reloads wait and are never dropped, so the call can never loop if hiding the cursor itself reloads. Run end shows it.
//!
//! `rust/corpus/pointer/sampler.json` holds the Python's answers and `sampler_corpus_tests.rs` replays them.

use super::sampler::{CallError, Clock};

/// Config reloads: at most one forced re-send per this long (free mode's regrab limit).
pub const RELOAD_REAPPLY_S: f64 = 2.0;

/// The two compositor calls this needs. [`CallError::Missing`] is a client without the call (the Python's
/// `getattr(client, ..., None)`): nothing is sent and nothing counts as an error.
pub trait CursorClient {
    fn hide_overview_cursor(&mut self) -> Result<(), CallError>;
    fn show_overview_cursor(&mut self) -> Result<(), CallError>;
}

/// The real cursor's visibility for one run, applied from the sampler thread. `hidden` is what was last applied: `None`
/// is unknown (nothing yet, or a config reload since).
#[derive(Clone, Debug, PartialEq)]
pub struct OverviewCursor {
    reloads: i64,
    reload_due: bool,
    forced_at: Option<f64>,
    pub hidden: Option<bool>,
    pub errors: u64,
}

impl OverviewCursor {
    /// `reloads`: the control's config reload count now (`getattr(control, 'reloads', 0)`).
    pub fn new(reloads: i64) -> Self {
        OverviewCursor {
            reloads,
            reload_due: false,
            forced_at: None,
            hidden: None,
            errors: 0,
        }
    }

    /// Hide (`true`) or show (`false`) the cursor unless it already is; a failed call is retried on the next step.
    /// `reloads` is the control's reload count now; the clock is read only when a reload is due.
    pub fn step(
        &mut self,
        client: &mut (impl CursorClient + ?Sized),
        hidden: bool,
        reloads: i64,
        clock: &mut (impl Clock + ?Sized),
    ) {
        if reloads != self.reloads {
            self.reloads = reloads;
            self.reload_due = true;
        }
        if self.reload_due && self.hidden == Some(false) {
            self.reload_due = false; // shown: the reload changed nothing
        }
        if self.reload_due {
            let now = clock.now();
            if self.forced_at.is_none_or(|at| now - at >= RELOAD_REAPPLY_S) {
                // It put the config value back.
                self.reload_due = false;
                self.forced_at = Some(now);
                self.hidden = None;
            }
        }
        if self.hidden == Some(hidden) {
            return;
        }
        let sent = if hidden {
            client.hide_overview_cursor()
        } else {
            client.show_overview_cursor()
        };
        match sent {
            Ok(()) => self.hidden = Some(hidden),
            Err(CallError::Missing) => {}
            Err(CallError::Failed) => self.errors += 1,
        }
    }

    /// Run end, after the free pointer's binds are removed: show it (best effort) unless it is known shown.
    pub fn stop(&mut self, client: &mut (impl CursorClient + ?Sized)) {
        if self.hidden == Some(false) {
            return;
        }
        match client.show_overview_cursor() {
            Ok(()) => self.hidden = Some(false),
            Err(CallError::Missing) => {}
            Err(CallError::Failed) => self.errors += 1,
        }
    }

    /// The corpus's view of the state (the Python's attributes).
    #[cfg(test)]
    pub(crate) fn state(&self) -> serde_json::Value {
        serde_json::json!({
            "_reloads": self.reloads,
            "_reload_due": self.reload_due,
            "_forced_at": self.forced_at,
            "hidden": self.hidden,
            "errors": self.errors,
        })
    }
}

/// `test_overview_cursor.CursorStateTests`.
#[cfg(test)]
mod tests {
    use super::*;

    struct Now(f64);

    impl Clock for Now {
        fn now(&mut self) -> f64 {
            self.0
        }
    }

    /// The Python's `RecordingClient`: the calls in order; `fail_hide` refusals before a hide goes through.
    #[derive(Default)]
    struct Recording {
        calls: Vec<&'static str>,
        fail_hide: u32,
        broken: bool,
        missing: bool,
    }

    impl CursorClient for Recording {
        fn hide_overview_cursor(&mut self) -> Result<(), CallError> {
            if self.missing {
                return Err(CallError::Missing);
            }
            self.calls.push("hide");
            if self.fail_hide > 0 {
                self.fail_hide -= 1;
                return Err(CallError::Failed);
            }
            Ok(())
        }

        fn show_overview_cursor(&mut self) -> Result<(), CallError> {
            if self.missing {
                return Err(CallError::Missing);
            }
            if self.broken {
                return Err(CallError::Failed);
            }
            self.calls.push("show");
            Ok(())
        }
    }

    #[test]
    fn only_changes_are_sent_and_the_first_step_always_is() {
        let (mut c, mut client, mut clock) = (OverviewCursor::new(0), Recording::default(), Now(0.0));
        for hidden in [false, false, true, true, false] {
            c.step(&mut client, hidden, 0, &mut clock);
        }
        assert_eq!(client.calls, ["show", "hide", "show"]); // the first: a killed run's hidden cursor comes back
    }

    #[test]
    fn a_refused_call_is_retried() {
        let (mut c, mut clock) = (OverviewCursor::new(0), Now(0.0));
        let mut client = Recording { fail_hide: 1, ..Default::default() };
        c.step(&mut client, true, 0, &mut clock);
        assert_eq!((c.hidden, c.errors), (None, 1));
        c.step(&mut client, true, 0, &mut clock);
        assert_eq!(c.hidden, Some(true));
        assert_eq!(client.calls, ["hide", "hide"]);
    }

    #[test]
    fn a_client_without_it_is_left_alone() {
        let (mut c, mut clock) = (OverviewCursor::new(0), Now(0.0));
        let mut client = Recording { missing: true, ..Default::default() };
        c.step(&mut client, true, 0, &mut clock);
        c.stop(&mut client);
        assert_eq!((c.hidden, c.errors), (None, 0));
    }

    #[test]
    fn a_config_reload_sends_again_but_never_loops() {
        let (mut c, mut client, mut clock) = (OverviewCursor::new(0), Recording::default(), Now(0.0));
        let mut reloads = 0;
        c.step(&mut client, true, reloads, &mut clock);
        reloads += 1;
        c.step(&mut client, true, reloads, &mut clock);
        assert_eq!(client.calls, ["hide", "hide"]); // the reload showed it
        reloads += 1;
        clock.0 += RELOAD_REAPPLY_S / 2.0;
        for _ in 0..3 {
            c.step(&mut client, true, reloads, &mut clock);
        }
        assert_eq!(client.calls, ["hide", "hide"]); // at most once per RELOAD_REAPPLY_S...
        clock.0 += RELOAD_REAPPLY_S / 2.0;
        c.step(&mut client, true, reloads, &mut clock);
        assert_eq!(client.calls, ["hide", "hide", "hide"]); // ...but a reload is never dropped
        c.step(&mut client, false, reloads, &mut clock);
        reloads += 1;
        clock.0 += RELOAD_REAPPLY_S;
        c.step(&mut client, false, reloads, &mut clock);
        assert_eq!(client.calls.last(), Some(&"show")); // shown: a reload changes nothing, nothing more is sent
        assert_eq!(client.calls.len(), 4);
    }

    #[test]
    fn stop_shows_it_unless_it_is_known_shown() {
        let mut c = OverviewCursor::new(0);
        for (state, sent) in [(Some(true), &["show"][..]), (None, &["show"][..]), (Some(false), &[][..])] {
            let mut client = Recording::default();
            c.hidden = state;
            c.stop(&mut client);
            assert_eq!(client.calls, sent, "{state:?}");
            assert_eq!(c.hidden, Some(false));
        }
        let mut broken = Recording { broken: true, ..Default::default() };
        c.hidden = Some(true);
        c.stop(&mut broken); // best effort: never fails the run end
        assert_eq!(c.hidden, Some(true));
    }
}
