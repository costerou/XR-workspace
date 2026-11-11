//! `renderer_switch.py`'s Start half: which pinned renderer a run launches, the C++ `xr-native-spike` (the default) or
//! the Rust `xr-renderer`, in the Python runner's words.
//!
//! The setting is `$XDG_CONFIG_HOME/viture-workspace/renderer.json`, `{"schema": 1, "renderer": "rust" | "cpp"}`, read
//! at every Start; no file is C++. The Python CLI writes it (`python -m tools.xr_controller renderer set rust|cpp`),
//! this only reads it. With rust set, the run launches the binary `install --rust-renderer` pinned
//! (`install::RUST_RENDERER_PIN`), its bytes hashed now; with no trustworthy one the Start fails naming the way back,
//! and never launches the C++ renderer instead. A Pro 2 consent is refused before this is read (`PRO2_NOT_PORTED`).

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::install::{self, Pin, RUST_RENDERER_PIN};
use crate::private_file::{DEFAULT_LIMIT, read_private_json};

use super::{Installed, Res, check_entry, config_home, py_os_error, verify_pinned};

pub const SETTING_NAME: &str = "renderer.json";
const CHOICES: [&str; 2] = ["rust", "cpp"];
pub const BACK: &str = "switch back with `python -m tools.xr_controller renderer set cpp`";

/// `renderer_switch.setting_path()`.
pub fn setting_path() -> PathBuf {
    config_home().join("viture-workspace").join(SETTING_NAME)
}

/// `renderer_switch.choice`: "rust" or "cpp" (also with no file). A file that cannot be trusted fails the Start
/// rather than guessing which renderer was meant: JSON that is not exactly the setting is invalid, a file others can
/// read or that will not open is unreadable.
pub fn choice(path: &Path) -> Res<&'static str> {
    if !path.exists() {
        return Ok("cpp");
    }
    let data = match read_private_json(path, DEFAULT_LIMIT) {
        Ok(data) => Some(data),
        // read_private_json's ValueError: not JSON, duplicate keys, too large.
        Err(e) if e.kind() == io::ErrorKind::InvalidData => None,
        Err(e) => return Err(format!("{SETTING_NAME} unreadable: {}; {BACK}", py_os_error(&e, path))),
    };
    let picked = data
        .as_ref()
        .and_then(Value::as_object)
        .filter(|m| m.len() == 2 && m.get("schema").and_then(Value::as_u64) == Some(1))
        .and_then(|m| m.get("renderer")?.as_str())
        .and_then(|r| CHOICES.into_iter().find(|c| *c == r));
    picked.ok_or_else(|| format!("{SETTING_NAME} is invalid; {BACK} or clear it"))
}

/// `install.load_rust_renderer`: the Rust renderer's pin, checked for shape and place but not hashed, or `None` when
/// none is installed.
pub fn load_rust_renderer(data_home: &Path) -> Res<Option<Pin>> {
    let root = install::root(data_home);
    let path = root.join(RUST_RENDERER_PIN);
    let raw = match fs::read(&path) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("{RUST_RENDERER_PIN} unreadable: {}", py_os_error(&e, &path))),
        Ok(raw) => raw,
    };
    let pin: Value = serde_json::from_slice(&raw).map_err(|e| format!("{RUST_RENDERER_PIN} unreadable: {e}"))?;
    check_entry(&pin, &root.join("bin")).map(Some).map_err(|e| format!("{RUST_RENDERER_PIN}: {e}"))
}

/// `renderer_switch.rust_renderer`: the pinned Rust renderer, its bytes checked against its pin now.
pub fn rust_renderer(data_home: &Path) -> Res<Pin> {
    let pin = load_rust_renderer(data_home)
        .map_err(|e| format!("the Rust renderer cannot start: {e}; {BACK}"))?
        .ok_or_else(|| {
            format!(
                "the Rust renderer is selected but not installed (no {RUST_RENDERER_PIN}: \
                 run `install --build <dir> --rust-renderer <xr-renderer>`); {BACK}"
            )
        })?;
    if !install::verify_pinned(&pin) {
        return Err(format!("the Rust renderer cannot start: {} changed since it was installed; {BACK}", pin.path.display()));
    }
    Ok(pin)
}

/// `renderer_switch.start_renderer`: the renderer pin a run launches. The consent's, which must be the one the setting
/// picks now, its bytes unchanged; `installed.renderer` is the C++ one.
pub fn start_renderer(consent: &Pin, installed: &Installed, data_home: &Path) -> Res<Pin> {
    if choice(&setting_path())? == "rust" {
        let pin = rust_renderer(data_home)?;
        if *consent != pin {
            return Err(format!("consent renderer is not the pinned Rust renderer; {BACK}"));
        }
        return Ok(pin);
    }
    if *consent != installed.renderer {
        return Err("consent renderer is not the pinned install".into());
    }
    verify_pinned(&installed.renderer)?;
    Ok(installed.renderer.clone())
}
