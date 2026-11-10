//! The loopback backend: a whole session with no compositor, no glasses and no renderer, for the tests.
//!
//! Debug builds only, and only when `XR_WORKSPACE_SUPERVISOR_FAKE` names a script (JSON). A release build has no such
//! backend at all, so nothing in the environment can switch a real run onto it.
//!
//! The script, every key optional:
//! `{"log": path, "fail": step, "error": text, "plugins": [names], "renderer_returncode": int,
//!   "recovery": {"complete": bool, "remaining": [..], "reasons": [..]}, "stop_reason": text, "journal": bool}`.
//! Each step, the runner's and `managed::run_managed`'s, appends its name to `log`; the step named `fail` fails with
//! `error`. `journal` writes the canonical journal file when the broker opens, as a real run would.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::hyprland::OWNERSHIP_PLUGIN_NAME;
use crate::install::Pin;
use crate::managed::{self, Recovery, RunOptions, Scope, Spec};

use super::{Backend, PanelConsent, Res, Run};

pub const ENV: &str = "XR_WORKSPACE_SUPERVISOR_FAKE";

pub struct Fake {
    script: Value,
    plugins: Vec<String>,
    journal: Option<PathBuf>,
    scope: Option<Scope>,
}

impl Fake {
    /// The fake the environment asks for, if any.
    pub fn from_env() -> Option<Result<Fake, String>> {
        let path = std::env::var_os(ENV)?;
        Some(Fake::load(Path::new(&path)))
    }

    pub fn load(path: &Path) -> Result<Fake, String> {
        let text = fs::read(path).map_err(|e| format!("fake script: {e}"))?;
        let script: Value = serde_json::from_slice(&text).map_err(|e| format!("fake script: {e}"))?;
        let plugins = script["plugins"]
            .as_array()
            .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
            .unwrap_or_default();
        Ok(Fake { script, plugins, journal: None, scope: None })
    }

    fn step(&mut self, name: &str) -> Res<()> {
        if let Some(log) = self.script["log"].as_str() {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(log) {
                let _ = writeln!(f, "{name}");
            }
        }
        if self.script["fail"].as_str() == Some(name) {
            return Err(self.script["error"].as_str().unwrap_or("fake failure").to_string());
        }
        Ok(())
    }

    fn managed(&mut self, name: &str) -> managed::Result<()> {
        self.step(name).map_err(managed::Error)
    }

    fn strings(&self, v: &Value) -> Vec<String> {
        v.as_array().map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default()
    }
}

impl Backend for Fake {
    fn connect(&mut self, instance: &str, _runtime: &Path) -> Res<()> {
        self.step(&format!("connect {instance}"))
    }
    fn tracking_luma(&mut self, _runtime: &Path, luma: &Path) -> Res<()> {
        self.step(&format!("tracking.luma {}", luma.display()))
    }
    fn tracking_driver(&mut self, _runtime: &Path, binary: &Path) -> Res<()> {
        self.step(&format!("tracking.driver {}", binary.display()))
    }
    fn finish_watch(&mut self) {
        let _ = self.step("tracking.watch_finish");
    }
    fn plugins_loaded(&mut self) -> Res<Vec<String>> {
        self.step("plugins_loaded")?;
        Ok(self.plugins.clone())
    }
    fn plugin_load(&mut self, _path: &str) -> Res<()> {
        self.step("plugin_load")?;
        self.plugins.push(OWNERSHIP_PLUGIN_NAME.into());
        Ok(())
    }
    fn plugin_unload(&mut self, _path: &str, _pin: &Pin, _data_home: &Path) -> Res<()> {
        self.step("plugin_unload")?;
        self.plugins.retain(|p| p != OWNERSHIP_PLUGIN_NAME);
        Ok(())
    }
    fn apply_session_mode(&mut self, output: &str) -> Res<()> {
        self.step(&format!("session_mode {output}"))
    }
    fn park_presentation(&mut self, output: &str) -> Res<()> {
        self.step(&format!("park {output}"))
    }
    fn run_managed(&mut self, consent: &PanelConsent, runtime: &Path, _renderer: &Pin) -> Res<Run> {
        if self.script["journal"] == true {
            self.journal = Some(runtime.join("xr-workspace-spike").join(&consent.run_id).join("journal.json"));
        }
        let order = managed::Consent {
            run_id: consent.run_id.clone(),
            model: consent.model.clone(),
            instance: consent.instance.clone(),
            output: consent.output.clone(),
            recovery_output: consent.recovery_output.clone(),
            capture: true,
        };
        let desktops = consent.desktops.len();
        self.script["desktops"] = desktops.into();
        let outcome = managed::run_managed(self, &order, RunOptions::default()).map_err(|e| e.0)?;
        Ok(Run {
            complete: outcome.recovery.complete,
            renderer_returncode: Some(outcome.renderer_returncode),
            remaining: outcome.recovery.remaining,
            stop_reason: outcome.stop_reason,
            holds: Vec::new(),
            reasons: outcome.recovery.reasons,
        })
    }
    fn toast(&mut self, body: &str) {
        let _ = self.step(&format!("toast {body}"));
    }
}

impl managed::Host for Fake {
    type Snapshot = ();

    fn verified_session(&mut self) -> managed::Result<String> {
        self.managed("session.verify")?;
        Ok("loopback".into())
    }
    fn validate_physical_pair(&mut self, _s: &str, _o: &str, _f: &str, asleep_ok: bool) -> managed::Result<()> {
        self.managed(if asleep_ok { "physical.guard" } else { "physical.validate" })
    }
    fn plan_outputs(&mut self, run_id: &str) -> managed::Result<Vec<Spec>> {
        self.managed("outputs.plan")?;
        let n = self.script["desktops"].as_u64().unwrap_or(1);
        Ok((1..=n).map(|i| Spec { id: format!("{run_id}-{i}"), name: format!("XR-{i}") }).collect())
    }
    fn broker_open(&mut self, _r: &str, _s: &str, _f: &str) -> managed::Result<()> {
        self.managed("broker.open")?;
        if let Some(journal) = &self.journal {
            let made = journal.parent().map(fs::create_dir_all).transpose().and_then(|_| fs::write(journal, b"{}"));
            made.map_err(|e| managed::Error(e.to_string()))?;
        }
        Ok(())
    }
    fn broker_from_journal(&mut self) -> managed::Result<()> {
        self.managed("broker.from_journal")
    }
    fn require_output_ownership(&mut self) -> managed::Result<()> {
        self.managed("broker.require_ownership")
    }
    fn private_dir(&mut self, relative: &Path) -> managed::Result<()> {
        self.managed(&format!("private_dir {}", relative.display()))
    }
    fn lock_owner(&mut self) -> managed::Result<()> {
        self.managed("owner.lock")
    }
    fn unlock_owner(&mut self) {
        let _ = self.step("owner.unlock");
    }
    fn journal_open(&mut self, _r: &str, _s: &str, _e: bool) -> managed::Result<()> {
        self.managed("journal.open")
    }
    fn journal_read_session(&mut self) -> managed::Result<String> {
        self.managed("journal.read_session")?;
        Ok("loopback".into())
    }
    fn journal_is_fresh(&mut self) -> managed::Result<bool> {
        self.managed("journal.is_fresh")?;
        Ok(true)
    }
    fn journal_bind_broker(&mut self, scope: &Scope) -> managed::Result<()> {
        self.managed("journal.bind_broker")?;
        self.scope = Some(scope.clone());
        Ok(())
    }
    fn journal_scope(&self) -> Option<Scope> {
        self.scope.clone()
    }
    fn journal_output_names(&mut self) -> managed::Result<Vec<String>> {
        self.managed("journal.output_names")?;
        Ok(Vec::new())
    }
    fn journal_recover(&mut self) -> managed::Result<Recovery> {
        self.managed("journal.recover")?;
        let r = &self.script["recovery"];
        Ok(Recovery {
            complete: r.get("complete").and_then(Value::as_bool).unwrap_or(true),
            remaining: self.strings(&r["remaining"]),
            reasons: self.strings(&r["reasons"]),
        })
    }
    fn journal_compositor_ended(&mut self) -> managed::Result<()> {
        self.managed("journal.compositor_ended")
    }
    fn create_outputs(&mut self, _specs: &[Spec]) -> managed::Result<()> {
        self.managed("outputs.create")
    }
    fn discard_unusable_setup(&mut self, _e: &managed::Error) -> managed::Result<()> {
        self.managed("setup.discard")
    }
    fn desktop_manager(&mut self, _specs: &[Spec]) -> managed::Result<()> {
        self.managed("desktop_manager")
    }
    fn broker_verify(&mut self, spec: &Spec) -> managed::Result<()> {
        self.managed(&format!("broker.verify {}", spec.name))
    }
    fn take_layout(&mut self) -> Option<()> {
        let _ = self.step("layout.take");
        Some(())
    }
    fn window_memory(&mut self, _n: &[String]) -> managed::Result<bool> {
        self.managed("window_memory")?;
        Ok(false)
    }
    fn window_memory_place(&mut self, _n: &[String]) -> managed::Result<()> {
        self.managed("window_memory.place")
    }
    fn adopt(&mut self, _n: &[String]) -> managed::Result<()> {
        self.managed("adopt")
    }
    fn window_memory_finish(&mut self) -> managed::Result<()> {
        self.managed("window_memory.finish")
    }
    fn cursor_reader(&mut self) -> managed::Result<()> {
        self.managed("cursor_reader")
    }
    fn view_controls_enter(&mut self) -> managed::Result<()> {
        self.managed("view_controls.enter")
    }
    fn view_controls_exit(&mut self) -> managed::Result<()> {
        self.managed("view_controls.exit")
    }
    fn continuity_open(&mut self) -> managed::Result<()> {
        self.managed("continuity.open")
    }
    fn continuity_close(&mut self) -> managed::Result<()> {
        self.managed("continuity.close")
    }
    fn keep_renderer(&mut self, _capture: bool) -> managed::Result<i32> {
        self.managed("renderer.keep")?;
        Ok(self.script["renderer_returncode"].as_i64().unwrap_or(0) as i32)
    }
    fn stop_reason(&self) -> Option<String> {
        self.script["stop_reason"].as_str().map(str::to_string)
    }
    fn wake(&mut self, output: &str) {
        let _ = self.step(&format!("wake {output}"));
    }
    fn restore_2d(&mut self, output: &str) {
        let _ = self.step(&format!("restore_2d {output}"));
    }
    fn clear_monitor_rules(&mut self, _n: &[String]) -> Vec<String> {
        let _ = self.step("monitor_rules.clear");
        Vec::new()
    }
    fn named_monitor_rules(&mut self, _n: &[String]) -> Vec<String> {
        let _ = self.step("monitor_rules.named");
        Vec::new()
    }
    fn unpin(&mut self) {
        let _ = self.step("unpin");
    }
    fn load_layout(&mut self) -> managed::Result<Option<()>> {
        self.managed("layout.load")?;
        Ok(None)
    }
    fn restore_layout(&mut self, _s: &()) {
        let _ = self.step("layout.restore");
    }
    fn discard_layout(&mut self) -> managed::Result<()> {
        self.managed("layout.discard")
    }
    fn notify(&mut self, text: &str) {
        let _ = self.step(&format!("notify {text}"));
    }
    fn left_on(&mut self, _n: &[String]) -> Vec<String> {
        let _ = self.step("left_on");
        Vec::new()
    }
}
