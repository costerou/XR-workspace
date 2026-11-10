//! The run half of the controller's host probe (`Host.probe` in `tools/xr_controller/__main__.py`) and run
//! adoption: what a controller that has just started finds left by runs it, or an earlier controller, started.
//!
//! Runs are found on disk, never in memory, so a restarted controller sees exactly what the one before it saw:
//! - a journal `<runtime>/xr-workspace-spike/<run_id>/journal.json` with an operation not `done` is an unfinished
//!   run. It is ours when the controller wrote its consent `<state_dir>/<run_id>.json`; otherwise it is external
//!   (a supervised test or the CLI) and only keeps Start closed.
//! - a consent with no journal yet, whose unit is alive, is a run between launch and its first journal write.
//!
//! `adopt` turns that into the run's state: running when its unit is alive and its renderer listens, starting
//! before it has a session, recovery-needed when its unit is gone and its journal is unfinished.

use std::io;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::private_file::{DEFAULT_LIMIT, invalid, read_private_json, token};
use crate::units::{Units, control_listening};

/// Journals are read with a 1 MiB limit, as the Python probe does.
const JOURNAL_LIMIT: u64 = 1 << 20;

/// A run the probe found.
#[derive(Clone, Debug, PartialEq)]
pub struct Run {
    pub run_id: String,
    /// The renderer control session, from the journal. None before the first journal write.
    pub session: Option<String>,
    /// The journal's path. None before the first journal write.
    pub journal_path: Option<PathBuf>,
    /// Before the first journal write: the runner's last progress phase (`<run_id>.progress.json`), if any.
    pub phase: Option<String>,
    /// The journal as read.
    pub journal: Option<Value>,
}

/// Unfinished runs: the controller's own, and those someone else started in the same runtime folder.
#[derive(Debug, Default, PartialEq)]
pub struct Runs {
    pub journals: Vec<Run>,
    pub external: Vec<Run>,
}

pub fn spike_root(runtime: &Path) -> PathBuf {
    runtime.join("xr-workspace-spike")
}

/// What `<runtime>/xr-workspace-spike` and `<state_dir>` say about unfinished runs. A journal whose `run_id` is not
/// its folder's name, or that is not a journal, fails the whole probe, as in Python: the controller then shows
/// `failed` rather than guess. An unreadable consent costs only that run.
pub fn probe_runs(runtime: &Path, state_dir: &Path, units: &Units) -> io::Result<Runs> {
    let mut runs = Runs::default();
    for name in sorted_names(&spike_root(runtime))? {
        let path = spike_root(runtime).join(&name).join("journal.json");
        if path.symlink_metadata().is_err() {
            continue;
        }
        let data = read_private_json(&path, JOURNAL_LIMIT)?;
        if data.get("run_id").and_then(Value::as_str) != Some(name.as_str()) {
            return Err(invalid("journal identity mismatch"));
        }
        let operations = data
            .get("operations")
            .and_then(Value::as_array)
            .ok_or_else(|| invalid("journal operations"))?;
        let mut unfinished = false;
        for op in operations {
            let op = op.as_object().ok_or_else(|| invalid("journal operation"))?;
            unfinished |= op.get("state").and_then(Value::as_str) != Some("done");
        }
        if !unfinished {
            continue;
        }
        let own = state_dir
            .join(format!("{name}.json"))
            .symlink_metadata()
            .is_ok();
        let run = Run {
            session: data
                .get("session")
                .and_then(Value::as_str)
                .map(str::to_owned),
            run_id: name,
            journal_path: Some(path),
            phase: None,
            journal: Some(data),
        };
        if own {
            runs.journals.push(run)
        } else {
            runs.external.push(run)
        }
    }

    // A controller restart can fall between launch and the first journal write, or between completed recovery
    // and plugin unload. Both still own a unit, and only the unit says the run is there.
    let mut waiting = Vec::new();
    for name in sorted_names(state_dir)? {
        let Some(stem) = name.strip_suffix(".json") else {
            continue;
        };
        if [".result", ".progress", ".recovery"]
            .iter()
            .any(|s| stem.ends_with(s))
        {
            continue;
        }
        let Ok(raw) = read_private_json(&state_dir.join(&name), DEFAULT_LIMIT) else {
            continue;
        };
        let Some(run_id) = raw.get("run_id").and_then(Value::as_str) else {
            continue;
        };
        if run_id == stem
            && token(run_id).is_ok()
            && !runs.journals.iter().any(|j| j.run_id == run_id)
        {
            waiting.push(run_id.to_owned());
        }
    }
    if !waiting.is_empty() {
        for (run_id, alive) in waiting.iter().zip(units.alive_many(&waiting)?) {
            if alive {
                let phase = progress_phase(state_dir, run_id)?;
                runs.journals.push(Run {
                    run_id: run_id.clone(),
                    session: None,
                    journal_path: None,
                    phase,
                    journal: None,
                });
            }
        }
    }
    Ok(runs)
}

fn progress_phase(state_dir: &Path, run_id: &str) -> io::Result<Option<String>> {
    match read_private_json(
        &state_dir.join(format!("{run_id}.progress.json")),
        DEFAULT_LIMIT,
    ) {
        Ok(p) if p.get("run_id").and_then(Value::as_str) == Some(run_id) => {
            Ok(p.get("phase").and_then(Value::as_str).map(str::to_owned))
        }
        Ok(_) => Ok(None),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

/// The visible entries of a folder, sorted (`Path.glob('*')`); none when it does not exist.
fn sorted_names(dir: &Path) -> io::Result<Vec<String>> {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    let mut names = Vec::new();
    for entry in entries {
        if let Ok(name) = entry?.file_name().into_string() {
            if !name.starts_with('.') {
                names.push(name);
            }
        }
    }
    names.sort();
    Ok(names)
}

/// What the controller makes of the runs it found.
#[derive(Debug, PartialEq)]
pub enum Adoption {
    /// No unfinished run of ours.
    None,
    /// More than one unfinished run of ours: only the CLI may untangle them.
    CliRecovery,
    /// Unit alive and its renderer control listener answers: the run is adopted as running.
    Running(Run),
    /// Unit alive, no session yet: still starting (its progress phase says how far).
    Starting(Run),
    /// Unit alive with a session, but no listener: it has to be stopped before anything else starts.
    ControlUnavailable(Run),
    /// Unit gone, journal unfinished: the desktop may still be changed, so Recover is offered. `reason` says why the
    /// last Recover stopped, if one ran.
    RecoveryNeeded { run: Run, reason: Option<String> },
}

impl Adoption {
    /// The controller state this is, and the run it is about.
    pub fn describe(&self) -> String {
        match self {
            Adoption::None => "idle (no unfinished run of ours)".into(),
            Adoption::CliRecovery => "cli-recovery (more than one unfinished run)".into(),
            Adoption::Running(r) => format!("running {}", r.run_id),
            Adoption::Starting(r) => format!(
                "starting {} (phase {})",
                r.run_id,
                r.phase.as_deref().unwrap_or("unknown")
            ),
            Adoption::ControlUnavailable(r) => {
                format!("failed {}: XR renderer control unavailable", r.run_id)
            }
            Adoption::RecoveryNeeded { run, reason } => {
                format!(
                    "recovery-needed {}{}",
                    run.run_id,
                    reason
                        .as_deref()
                        .map(|r| format!(": {r}"))
                        .unwrap_or_default()
                )
            }
        }
    }
}

/// The state a just-started controller takes for the runs `probe_runs` found (`Controller._tick` from idle).
/// A controller already mid-start or mid-stop keeps its own grace periods on top of this.
pub fn adopt(runs: &Runs, runtime: &Path, state_dir: &Path, units: &Units) -> io::Result<Adoption> {
    let run = match runs.journals.as_slice() {
        [] => return Ok(Adoption::None),
        [run] => run.clone(),
        _ => return Ok(Adoption::CliRecovery),
    };
    if units.alive(&run.run_id)? {
        return Ok(match run.session {
            None => Adoption::Starting(run),
            Some(_) if control_listening(runtime, &run.run_id) => Adoption::Running(run),
            Some(_) => Adoption::ControlUnavailable(run),
        });
    }
    let reason = recovery_reason(state_dir, &run.run_id);
    Ok(Adoption::RecoveryNeeded { run, reason })
}

/// Why the last Recover of this run stopped (`<run_id>.recovery.json`, written by `report_recovery`).
fn recovery_reason(state_dir: &Path, run_id: &str) -> Option<String> {
    let record = read_private_json(
        &state_dir.join(format!("{run_id}.recovery.json")),
        DEFAULT_LIMIT,
    )
    .ok()?;
    if record.get("run_id").and_then(Value::as_str) != Some(run_id) {
        return None;
    }
    let reasons = record
        .get("reasons")?
        .as_array()
        .filter(|r| !r.is_empty())?;
    let text: Vec<String> = reasons
        .iter()
        .map(|r| r.as_str().map_or_else(|| r.to_string(), str::to_owned))
        .collect();
    Some(format!("Recover could not finish. {}", text.join(" ")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testdir::{TestDir, write_private};
    use serde_json::json;
    use std::os::unix::fs::PermissionsExt;

    struct Layout {
        _dir: TestDir,
        runtime: PathBuf,
        state: PathBuf,
    }

    fn layout() -> Layout {
        let dir = TestDir::new("host");
        let runtime = dir.path().join("runtime");
        let state = dir.path().join("state");
        std::fs::create_dir_all(spike_root(&runtime)).unwrap();
        std::fs::create_dir_all(&state).unwrap();
        Layout {
            _dir: dir,
            runtime,
            state,
        }
    }

    impl Layout {
        fn journal(&self, run_id: &str, data: Value) -> PathBuf {
            let dir = spike_root(&self.runtime).join(run_id);
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
            write_private(&dir.join("journal.json"), &data);
            dir.join("journal.json")
        }
        fn consent(&self, run_id: &str) {
            write_private(
                &self.state.join(format!("{run_id}.json")),
                &json!({"run_id": run_id}),
            );
        }
    }

    /// A stand-in `systemctl`: alive exactly for the run ids listed, one line per unit for `is-active`.
    fn units_alive(dir: &Path, alive: &[&str]) -> Units {
        let path = dir.join("systemctl");
        let cases: String = alive
            .iter()
            .map(|r| format!("viture-xr-{r}) echo active; ok=0;;\n"))
            .collect();
        let script = format!(
            "#!/bin/sh\nok=3\nfor u in \"$@\"; do case \"$u\" in\n{cases}viture-xr-*) echo inactive;;\nesac; done\nexit $ok\n"
        );
        std::fs::write(&path, script).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        Units {
            systemctl: path.clone(),
            systemd_run: path,
        }
    }

    fn unfinished(run_id: &str, session: &str) -> Value {
        json!({"run_id": run_id, "session": session, "operations": [{"state": "done"}, {"state": "applied"}]})
    }

    #[test]
    fn own_and_external_journals() {
        let l = layout();
        let units = units_alive(&l.state, &[]);
        l.journal("a", unfinished("a", "s"));
        l.consent("a");
        l.journal("b", unfinished("b", "s"));
        l.journal(
            "c",
            json!({"run_id": "c", "operations": [{"state": "done"}]}),
        );
        l.consent("c");
        let runs = probe_runs(&l.runtime, &l.state, &units).unwrap();
        assert_eq!(
            runs.journals
                .iter()
                .map(|r| &r.run_id[..])
                .collect::<Vec<_>>(),
            ["a"]
        );
        assert_eq!(runs.journals[0].session.as_deref(), Some("s"));
        assert_eq!(
            runs.external
                .iter()
                .map(|r| &r.run_id[..])
                .collect::<Vec<_>>(),
            ["b"]
        );
    }

    #[test]
    fn a_journal_in_the_wrong_folder_fails_the_probe() {
        let l = layout();
        l.journal("a", unfinished("b", "s"));
        let err = probe_runs(&l.runtime, &l.state, &units_alive(&l.state, &[])).unwrap_err();
        assert_eq!(err.to_string(), "journal identity mismatch");
    }

    #[test]
    fn a_consent_with_no_journal_counts_only_while_its_unit_is_alive() {
        let l = layout();
        l.consent("up");
        l.consent("gone");
        write_private(
            &l.state.join("up.progress.json"),
            &json!({"run_id": "up", "phase": "tracking"}),
        );
        write_private(
            &l.state.join("gone.result.json"),
            &json!({"run_id": "gone"}),
        );
        std::fs::write(l.state.join("bad.json"), b"{}").unwrap(); // not 0600: skipped, not fatal
        let runs = probe_runs(&l.runtime, &l.state, &units_alive(&l.state, &["up"])).unwrap();
        assert_eq!(
            runs.journals,
            [Run {
                run_id: "up".into(),
                session: None,
                journal_path: None,
                phase: Some("tracking".into()),
                journal: None
            }]
        );
    }

    #[test]
    fn adoption_decisions() {
        let l = layout();
        // No journal yet, unit alive: starting.
        l.consent("r");
        let units = units_alive(&l.state, &["r"]);
        let runs = probe_runs(&l.runtime, &l.state, &units).unwrap();
        assert!(matches!(
            adopt(&runs, &l.runtime, &l.state, &units).unwrap(),
            Adoption::Starting(_)
        ));
        // A session, unit alive, nobody listening: control unavailable.
        l.journal("r", unfinished("r", "s"));
        let runs = probe_runs(&l.runtime, &l.state, &units).unwrap();
        assert!(matches!(
            adopt(&runs, &l.runtime, &l.state, &units).unwrap(),
            Adoption::ControlUnavailable(_)
        ));
        // The renderer listens: running.
        let _listener =
            std::os::unix::net::UnixListener::bind(spike_root(&l.runtime).join("r/control.sock"))
                .unwrap();
        assert!(matches!(
            adopt(&runs, &l.runtime, &l.state, &units).unwrap(),
            Adoption::Running(_)
        ));
        // Unit gone: recovery needed, with the last Recover's reasons once there are some.
        let dead = units_alive(&l.state, &[]);
        let Adoption::RecoveryNeeded { reason, .. } =
            adopt(&runs, &l.runtime, &l.state, &dead).unwrap()
        else {
            panic!()
        };
        assert_eq!(reason, None);
        write_private(
            &l.state.join("r.recovery.json"),
            &json!({"run_id": "r", "reasons": ["HDMI-A-1 is off.", 2]}),
        );
        let Adoption::RecoveryNeeded { reason, .. } =
            adopt(&runs, &l.runtime, &l.state, &dead).unwrap()
        else {
            panic!()
        };
        assert_eq!(
            reason.as_deref(),
            Some("Recover could not finish. HDMI-A-1 is off. 2")
        );
        // Two of ours: CLI only.
        l.journal("t", unfinished("t", "s"));
        l.consent("t");
        let runs = probe_runs(&l.runtime, &l.state, &dead).unwrap();
        assert_eq!(
            adopt(&runs, &l.runtime, &l.state, &dead).unwrap(),
            Adoption::CliRecovery
        );
    }

    /// The gate, against the real user systemd: a run started as a transient unit is adopted as running by a
    /// controller that starts afterwards, and becomes recovery-needed when its unit dies with its journal
    /// unfinished. Creates and removes one short-lived `viture-xr-rstest…` unit; touches no XR session.
    /// `cargo test -p xr-workspace -- --ignored adoption_after_restart`
    #[test]
    #[ignore = "starts a transient unit in the user's systemd"]
    fn adoption_after_restart_with_real_systemd() {
        let l = layout();
        let run_id = format!(
            "rstest{}{}",
            std::process::id(),
            std::time::SystemTime::UNIX_EPOCH
                .elapsed()
                .unwrap()
                .as_nanos()
        );
        let run_dir = spike_root(&l.runtime).join(&run_id);
        std::fs::create_dir_all(&run_dir).unwrap();
        std::fs::set_permissions(&run_dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        l.consent(&run_id);
        write_private(
            &l.state.join(format!("{run_id}.progress.json")),
            &json!({"run_id": run_id, "phase": "tracking"}),
        );

        // The controller before the restart: spawn the run. It stands in for the runner: it listens on its control
        // socket until SIGINT.
        // It binds relative to the run folder: the full path can pass the 108-byte socket-address limit.
        let listener = "import os,socket,sys\nos.chdir(sys.argv[1])\ns=socket.socket(socket.AF_UNIX)\ns.bind('control.sock')\ns.listen()\ntry:\n  while True: s.accept()[0].close()\nexcept KeyboardInterrupt: pass\n";
        let argv = [
            "/usr/bin/python3".into(),
            "-c".into(),
            listener.into(),
            run_dir.clone().into_os_string(),
        ];
        Units::default()
            .spawn(&run_id, &l.state, &l.runtime, &l.state, &argv)
            .unwrap();
        struct Cleanup(String);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::process::Command::new("systemctl")
                    .args(["--user", "stop", &format!("viture-xr-{}", self.0)])
                    .stderr(std::process::Stdio::null())
                    .status();
            }
        }
        let _cleanup = Cleanup(run_id.clone());
        let wait = |what: &str, ok: &dyn Fn() -> bool| {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while !ok() {
                assert!(
                    std::time::Instant::now() < deadline,
                    "timed out waiting for {what}"
                );
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        };
        let controller = || {
            // A fresh controller each time: nothing carried over but the folders and the unit.
            let units = Units::default();
            let runs = probe_runs(&l.runtime, &l.state, &units).unwrap();
            adopt(&runs, &l.runtime, &l.state, &units).unwrap()
        };

        // Restarted before the first journal write: the live unit alone makes it a starting run.
        let Adoption::Starting(run) = controller() else {
            panic!("expected starting")
        };
        assert_eq!(run.phase.as_deref(), Some("tracking"));
        eprintln!("before the first journal write: starting ({:?})", run.phase);

        // Restarted while the run is up: adopted as running.
        write_private(&run_dir.join("journal.json"), &unfinished(&run_id, "s1"));
        wait("the control socket", &|| {
            run_dir.join("control.sock").exists()
        });
        let Adoption::Running(run) = controller() else {
            panic!("expected running")
        };
        assert_eq!(
            (run.run_id.as_str(), run.session.as_deref()),
            (run_id.as_str(), Some("s1"))
        );
        eprintln!("live run {run_id}: re-adopted as running");

        // The run dies with its journal unfinished: the next controller sees recovery-needed.
        Units::default().stop(&run_id).unwrap();
        wait("the unit to end", &|| {
            !Units::default().alive(&run_id).unwrap()
        });
        let Adoption::RecoveryNeeded { run, reason } = controller() else {
            panic!("expected recovery-needed")
        };
        assert_eq!((run.run_id.as_str(), reason), (run_id.as_str(), None));
        eprintln!("dead run {run_id}: recovery-needed");

        // Once its journal is finished there is nothing left to adopt.
        write_private(
            &run_dir.join("journal.json"),
            &json!({"run_id": run_id, "session": "s1", "operations": [{"state": "done"}]}),
        );
        assert_eq!(controller(), Adoption::None);
    }
}
