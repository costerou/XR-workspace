//! Conservative, readback-driven recovery of a run's journal, a port of `journal.recover`.
//!
//! Records are undone in reverse order with outputs last, so a workspace goes back to its monitor and a setting to its
//! value before the output they referred to is removed. A record is marked done only when the compositor's readback
//! shows the undo (or shows that nothing is left to undo), and only while the journal's own session is still the one
//! connected. Anything else (a disconnect, a refusal, an ambiguous readback) keeps the record, with a reason, for the
//! next attempt.

use crate::journal::{Error, Journal, Operation, State};
use std::collections::BTreeMap;
use std::fmt;

/// A failed compositor call, in the words the caller reports (the Python's exception text).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BackendError(pub String);

impl fmt::Display for BackendError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// One output, as the normalized recovery view reports it. `id` is a proven ownership UUID, never derived from the name.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OutputView {
    pub id: String,
    pub name: String,
    pub physical: bool,
}

/// One workspace and the monitor it is on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkspaceView {
    pub id: i64,
    pub output: String,
}

/// The normalized recovery view of the compositor (`journal.RecoveryBackend`), not the raw Hyprland transport.
pub trait RecoveryBackend {
    /// The id of the compositor session connected now, verified against the instance.
    fn session(&mut self) -> Result<String, BackendError>;
    fn outputs(&mut self) -> Result<Vec<OutputView>, BackendError>;
    fn workspaces(&mut self) -> Result<Vec<WorkspaceView>, BackendError>;
    fn settings(&mut self) -> Result<BTreeMap<String, i64>, BackendError>;
    /// A command from the recovery allowlist; `"ok"` when the compositor accepted it.
    fn mutate(&mut self, command: &str) -> Result<String, BackendError>;
    /// Moves the windows off owned output `id` (named `name`) and removes it, as one broker transaction. True when it
    /// is gone, including when it never existed.
    fn evacuate_remove(&mut self, id: &str, name: &str) -> Result<bool, BackendError>;
    /// Why the last `evacuate_remove` refused, when it said.
    fn refusal(&self) -> Option<String>;
}

/// The end of a recovery: `remaining` lists the records left, `reasons` says, one line each, what stopped them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecoveryResult {
    pub complete: bool,
    pub remaining: Vec<String>,
    pub reasons: Vec<String>,
}

fn check_session(session: &str, hypr: &mut dyn RecoveryBackend) -> Result<(), BackendError> {
    if hypr.session()? != session {
        return Err(BackendError("session disconnected or changed".into()));
    }
    Ok(())
}

fn mutate(
    session: &str,
    hypr: &mut dyn RecoveryBackend,
    command: &str,
) -> Result<(), BackendError> {
    check_session(session, hypr)?;
    if hypr.mutate(command)? != "ok" {
        return Err(BackendError("mutation failed".into()));
    }
    Ok(())
}

fn setting(settings: &BTreeMap<String, i64>, key: &str) -> Result<i64, BackendError> {
    // Python's KeyError text.
    settings
        .get(key)
        .copied()
        .ok_or_else(|| BackendError(format!("'{key}'")))
}

/// `_undo`: Ok(true) when the record is undone or proven to need nothing, Ok(false) when it is left.
fn undo(
    session: &str,
    hypr: &mut dyn RecoveryBackend,
    op: &Operation,
) -> Result<bool, BackendError> {
    check_session(session, hypr)?;
    match op {
        Operation::Setting {
            key,
            before,
            applied,
        } => {
            if setting(&hypr.settings()?, key)? != *applied {
                return Ok(true); // The user changed it, or the change never happened.
            }
            mutate(session, hypr, &format!("keyword {key} {before}"))?;
            Ok(setting(&hypr.settings()?, key)? == *before)
        }
        Operation::Workspace {
            workspace,
            before,
            applied,
        } => {
            let workspaces = hypr.workspaces()?;
            let matches: Vec<_> = workspaces.iter().filter(|w| w.id == *workspace).collect();
            match matches.as_slice() {
                [] => return Ok(true),
                [one] if one.output != *applied => return Ok(true),
                [_] => {}
                _ => return Ok(false),
            }
            if !hypr
                .outputs()?
                .iter()
                .any(|o| o.name == *before && o.physical)
            {
                return Ok(false);
            }
            mutate(
                session,
                hypr,
                &format!("dispatch moveworkspacetomonitor {workspace} {before}"),
            )?;
            Ok(hypr
                .workspaces()?
                .iter()
                .any(|w| w.id == *workspace && w.output == *before))
        }
        // Identity, evacuation and mapped-window reachability are one broker transaction; no name-addressed fallback.
        Operation::Output { id, name, .. } => hypr.evacuate_remove(id, name),
    }
}

fn label(op: &Operation) -> String {
    match op {
        Operation::Output { name, .. } => name.clone(),
        Operation::Setting { key, .. } => key.clone(),
        Operation::Workspace { workspace, .. } => format!("workspace {workspace}"),
    }
}

/// `recover`: undoes what `journal` holds, in one transaction. A storage failure is the only error: it is never
/// reported as a recovery.
pub fn recover(
    journal: &mut Journal,
    hypr: &mut dyn RecoveryBackend,
) -> Result<RecoveryResult, Error> {
    let tx = journal.begin()?;
    let session = journal.session.clone();
    let mut order: Vec<usize> = (0..journal.records.len()).rev().collect();
    order.sort_by_key(|&i| journal.records[i].operation.is_output());
    let mut reasons = Vec::new();
    for i in order {
        if journal.records[i].state == State::Done {
            continue;
        }
        let op = journal.records[i].operation.clone();
        let done = undo(&session, hypr, &op).and_then(|done| {
            if done {
                check_session(&session, hypr)?;
            }
            Ok(done)
        });
        match done {
            Err(e) => reasons.push(format!("{}: {e}", label(&op))),
            Ok(true) => {
                journal.records[i].state = State::Done;
                journal.save(&tx)?;
            }
            Ok(false) => {
                let why = hypr
                    .refusal()
                    .unwrap_or_else(|| "the compositor did not confirm it".into());
                reasons.push(format!("{}: {why}", label(&op)));
            }
        }
    }
    let remaining: Vec<String> = journal
        .records
        .iter()
        .filter(|r| r.state != State::Done)
        .map(|r| r.id.clone())
        .collect();
    Ok(RecoveryResult {
        complete: remaining.is_empty(),
        remaining,
        reasons,
    })
}
