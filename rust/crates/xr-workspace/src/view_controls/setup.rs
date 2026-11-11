//! A saved setup the broker refused goes back to the default: `managed._setup_failed` and `UNCREATABLE`
//! (tools/native_spike/managed.py) with `profiles.discard_unusable` (tools/native_spike/profiles.py), called by
//! `_run_owned` when `create_outputs` fails and the run's desktops are a saved setup (not the default three, on a
//! glasses profile). Never in place of the create failure itself.
//!
//! Not ported: [`discard_unusable`] does nothing, so the next start tries the same setup again.

use crate::managed::Error;

/// `managed.UNCREATABLE`: the broker's verdicts on an output itself; any other failure keeps the saved setup.
pub const UNCREATABLE: [&str; 4] = [
    "creation incomplete",
    "missing readback",
    "actual geometry/workspace mismatch",
    "unusable output",
];

/// `_setup_failed(store, desktops)(exc)`: the saved setup of `model` back to the default when `error` is an
/// [`UNCREATABLE`] refusal.
pub fn discard_unusable(_model: &str, _error: &Error) -> std::io::Result<()> {
    Ok(())
}
