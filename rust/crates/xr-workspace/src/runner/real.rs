//! The backend a release build runs: the ported Hyprland client and tracking wrappers, and the managed run on them
//! (`managed::real`).

use std::path::Path;

use crate::hyprland::{Client, PluginPin};
use crate::install::Pin;
use crate::supervisor::luma_watch::{self, LumaWatch};
use crate::supervisor::tracking::{Paths, Tracking, Want};

use super::{Backend, PanelConsent, Res, Run};

#[derive(Default)]
pub struct Real {
    client: Option<Client>,
    watch: Option<LumaWatch>,
}

impl Real {
    fn client(&mut self) -> Res<&mut Client> {
        self.client.as_mut().ok_or_else(|| "no Hyprland client".to_string())
    }
}

impl Backend for Real {
    fn connect(&mut self, instance: &str, runtime: &Path) -> Res<()> {
        self.client = Some(Client::new(instance, runtime).map_err(|e| e.to_string())?);
        Ok(())
    }

    fn tracking_luma(&mut self, runtime: &Path, luma: &Path) -> Res<()> {
        let tracking = Tracking::new(Paths::from_env(runtime));
        tracking.ensure_ready_luma(luma).map_err(|e| e.to_string())?;
        let watched = Tracking::new(Paths::from_env(runtime));
        self.watch = Some(LumaWatch::spawn(watched, luma.into(), super::toast, luma_watch::POLL));
        Ok(())
    }

    fn tracking_driver(&mut self, runtime: &Path, binary: &Path) -> Res<()> {
        let tracking = Tracking::new(Paths::from_env(runtime));
        let timeout = tracking.timing.start;
        tracking.ensure_ready(&Want::XrDriver(Some(binary.into())), timeout).map(drop).map_err(|e| e.to_string())
    }

    fn finish_watch(&mut self) {
        if let Some(watch) = self.watch.take() {
            watch.finish();
        }
    }

    fn plugins_loaded(&mut self) -> Res<Vec<String>> {
        self.client()?.plugins_loaded().map_err(|e| e.to_string())
    }

    fn plugin_load(&mut self, path: &str) -> Res<()> {
        self.client()?.plugin_load(path, PluginPin::Installed(path)).map_err(|e| e.to_string())
    }

    fn plugin_unload(&mut self, path: &str, recovery_pin: &Pin, data_home: &Path) -> Res<()> {
        let pin = PluginPin::Recovery { pin: recovery_pin, data_home };
        self.client()?.plugin_unload(path, pin).map_err(|e| e.to_string())
    }

    fn apply_session_mode(&mut self, output: &str) -> Res<()> {
        self.client()?.apply_session_mode(output).map(drop).map_err(|e| e.to_string())
    }

    fn park_presentation(&mut self, output: &str) -> Res<()> {
        self.client()?.park_presentation(output).map(drop).map_err(|e| e.to_string())
    }

    fn run_managed(&mut self, consent: &PanelConsent, runtime: &Path, renderer: &Pin) -> Res<Run> {
        let client = self.client()?;
        crate::managed::real::run(client, consent, runtime, renderer).map_err(|e| e.0)
    }

    fn toast(&mut self, body: &str) {
        super::toast(body);
    }
}
