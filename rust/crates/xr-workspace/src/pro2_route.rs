//! The Pro 2's route from the panel's normal Start (`tools/xr_controller/pro2_route.py`): the installed, hash-pinned SDK
//! bridge bundle and the Pro 2's own settings.
//!
//! - `pro2-bundle.pin.json`, beside `installed.json`, names one bundle under the data directory's `pro2/` folder by its
//!   manifest hash. Only the installer writes it ([`crate::install`]). Absent or invalid, the Pro 2 stays blocked
//!   ([`probe`]); nothing falls back to XRLinuxDriver.
//! - `pro2-settings.json` (in the profiles' folder) holds the projection, the mount pitch and the pose rate. Each value
//!   is read on its own, and one that cannot be read is this model's default ([`load_settings`]).
//! - [`require`] checks, before a run does anything, that the bundle is still exactly the one the start was confirmed
//!   with, and verifies every file of its manifest (`tools/pro2_runtime/launch.py` `verify_manifest`).
//!
//! Python and Rust read and write the same files; `tests.rs` runs the Python readers against what this writes and the
//! other way round. Differences, none of them in what is accepted: an OS error's text is Rust's, an unreadable JSON
//! file's parse message is serde's, a pin whose bundle is not text says "Pro 2 pin file is invalid", a route always says schema 1 (Python echoes
//! a pin's `true`, which it takes as 1), and
//! `pro2-settings.json` keeps the keys it had in a fixed order (schema, projection, mount_pitch_deg, pose_rate_hz)
//! rather than the file's.
//!
//! Not ported yet: `execute` and `recover` (the panel session and its recovery), which wait on the Rust run loop and
//! the Pro 2 test session; `write_pin` is the installer's, already in [`crate::install`].

use std::fs;
use std::path::{Component, Path, PathBuf};

use serde::ser::{Serialize, SerializeMap, Serializer};
use serde_json::{Value, json};

use crate::install::{self, PLUGIN, PRO2_DIR, PRO2_PIN, PRO2_SDK_SHA, RENDERER};
use crate::private_file::{DEFAULT_LIMIT, read_private_json};

pub const SETTINGS_FILE: &str = "pro2-settings.json";
/// panel_consent.PRO2_KEY: the consent's Pro 2 block.
pub const PRO2_KEY: &str = "pro2_route";
const PIN_KEYS: [&str; 4] = ["schema", "model", "bundle", "manifest_sha256"];

// display_policy.py and panel_consent.py.
pub const DEFAULT_PROJECTION: f64 = 44.5;
pub const PROJECTIONS: [f64; 7] = [44.5, 45.5, 46.5, 47.5, 48.5, 49.5, 50.0];
pub const MOUNT_PITCH: f64 = -8.5;
pub const MOUNT_BOUNDS: (f64, f64) = (-30.0, 30.0);
pub const POSE_RATE: i64 = 60;
pub const RATES: [i64; 4] = [60, 90, 120, 240];

const PLUGIN_FILE: &str = "bin/libxr-ownership.so";
const RENDERER_FILE: &str = "bin/xr-native-spike";
const REQUIRED: [&str; 3] = ["bin/pro2-sdk-bridge", RENDERER_FILE, PLUGIN_FILE];

const NOT_INSTALLED: &str = "The Pro 2 bridge bundle is not installed, so these glasses cannot start. Run the install again.";

/// One pinned artifact of the bundle.
#[derive(Clone, Debug, PartialEq)]
pub struct Pinned {
    pub path: String,
    pub sha256: String,
}

impl Pinned {
    pub fn to_json(&self) -> Value {
        json!({"path": self.path, "sha256": self.sha256})
    }
}

/// The installed bundle (`load_route`'s dict).
#[derive(Clone, Debug, PartialEq)]
pub struct Route {
    /// The pin's own text, as consents carry it.
    pub bundle: String,
    pub manifest_sha256: String,
    pub plugin: Pinned,
    pub renderer: Pinned,
}

impl Route {
    pub fn to_json(&self) -> Value {
        json!({"schema": 1, "model": "pro2", "bundle": self.bundle, "manifest_sha256": self.manifest_sha256,
               "plugin": self.plugin.to_json(), "renderer": self.renderer.to_json()})
    }
}

/// The Pro 2's settings for a start.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Settings {
    pub projection: f64,
    pub mount_pitch_deg: f64,
    pub pose_rate_hz: i64,
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// Python's `value == 1` for a JSON value (`True == 1` and `1.0 == 1` hold there).
fn equals_one(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() == Some(1.0),
        _ => false,
    }
}

/// `KeyError`'s text.
fn key_error(key: &str) -> String {
    format!("'{key}'")
}

fn text<E: ToString>(e: E) -> String {
    e.to_string()
}

fn manifest_sha(root: &Path) -> Result<String, String> {
    Ok(install::sha_hex(
        &fs::read(root.join("manifest.json")).map_err(text)?,
    ))
}

fn read_manifest(root: &Path) -> Result<Value, String> {
    serde_json::from_slice(&fs::read(root.join("manifest.json")).map_err(text)?).map_err(text)
}

fn files(manifest: &Value) -> Result<&serde_json::Map<String, Value>, String> {
    match manifest {
        Value::Object(m) => match m.get("files") {
            Some(Value::Object(files)) => Ok(files),
            Some(_) => Err("manifest files are not an object".into()),
            None => Err(key_error("files")),
        },
        _ => Err("manifest is not an object".into()),
    }
}

/// `launch.validate_bundle_location`: an absolute, canonical path of plain ASCII components that is a directory with
/// no symlink on the way. The path goes into Hyprland plugin requests, which are tokenized text.
pub fn validate_bundle_location(root: &str) -> Result<PathBuf, String> {
    let path = Path::new(root);
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map_err(text)?.join(path)
    };
    let plain = path.components().skip(1).all(|c| match c {
        Component::Normal(part) => part.to_str().is_some_and(|s| {
            s != "."
                && s != ".."
                && s.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        }),
        _ => false,
    });
    if !plain {
        return Err("unsupported supervised bundle location: canonical ASCII path without whitespace/control tokens \
                    required"
            .into());
    }
    // Collected again so that `a//b` and `a/./b` read as `a/b`, as pathlib reads them.
    let path: PathBuf = path.components().collect();
    if !path.is_dir() || fs::canonicalize(&path).ok().as_deref() != Some(path.as_path()) {
        return Err(
            "unsupported supervised bundle location: directory and ancestors must not be symlinks"
                .into(),
        );
    }
    Ok(path)
}

/// `launch.pinned_fd`, closed again: the file opened no-follow has `digest`.
fn check_pinned(path: &Path, digest: &Value) -> Result<(), String> {
    let actual = install::sha_file(path).map_err(text)?;
    if digest.as_str() != Some(actual.as_str()) {
        return Err("artifact hash mismatch".into());
    }
    Ok(())
}

/// `launch.validate_bundle_plugin` (no path to check): the bundle's ownership plugin, no symlink, by its pin.
pub fn validate_bundle_plugin(root: &Path, manifest: &Value) -> Result<PathBuf, String> {
    let root = validate_bundle_location(root.to_str().ok_or("bundle path is not text")?)?;
    let expected = root.join(PLUGIN_FILE);
    if fs::canonicalize(&expected).map_err(text)? != expected {
        return Err("supervised bundle plugin must not traverse symlinks".into());
    }
    let digest = manifest.get("files").and_then(|f| f.get(PLUGIN_FILE));
    let Some(digest) = digest.filter(|d| d.as_str().is_some_and(is_hex64)) else {
        return Err("supervised plugin manifest pin missing".into());
    };
    check_pinned(&expected, digest)?;
    Ok(expected)
}

/// `launch.verify_manifest`: every file the manifest names is there, no symlink, with its hash, and the bridge,
/// renderer and plugin are among them. (root, manifest).
pub fn verify_manifest(root: &str) -> Result<(PathBuf, Value), String> {
    let root = validate_bundle_location(root)?;
    let manifest = read_manifest(&root)?;
    if !manifest.get("schema").is_some_and(equals_one)
        || manifest.get("sdk_sha256").and_then(Value::as_str) != Some(PRO2_SDK_SHA)
    {
        return Err("runtime manifest scope".into());
    }
    let files = files(&manifest)?;
    for (name, digest) in files {
        let p = Path::new(name);
        if p.is_absolute() || p.components().any(|c| c == Component::ParentDir) {
            return Err("manifest path".into());
        }
        let path = root.join(p);
        if fs::canonicalize(&path).map_err(text)? != path {
            return Err("bundle artifacts must not traverse symlinks".into());
        }
        check_pinned(&path, digest)?;
    }
    if REQUIRED.iter().any(|name| !files.contains_key(*name)) {
        return Err("required artifact missing".into());
    }
    validate_bundle_plugin(&root, &manifest)?;
    Ok((root, manifest))
}

/// The installed bundle with its pinned artifacts; None when none is installed; Err when it cannot be trusted.
pub fn load_route(data_home: &Path) -> Result<Option<Route>, String> {
    let install = install::root(data_home);
    let path = install.join(PRO2_PIN);
    if !path.exists() {
        return Ok(None);
    }
    let data = read_private_json(&path, DEFAULT_LIMIT).map_err(text)?;
    let invalid = || "Pro 2 pin file is invalid".to_owned();
    let Value::Object(pin) = &data else {
        return Err(invalid());
    };
    if pin.len() != PIN_KEYS.len()
        || !PIN_KEYS.iter().all(|k| pin.contains_key(*k))
        || !equals_one(&pin["schema"])
        || pin["model"] != "pro2"
    {
        return Err(invalid());
    }
    let sha = pin["manifest_sha256"]
        .as_str()
        .filter(|s| is_hex64(s))
        .ok_or_else(invalid)?;
    let bundle = pin["bundle"].as_str().ok_or_else(invalid)?;
    let root = validate_bundle_location(bundle)?;
    if root.parent() != Some(install.join(PRO2_DIR).as_path()) {
        return Err("the Pro 2 bundle is not one the installer manages".into());
    }
    if manifest_sha(&root)? != sha {
        return Err("the Pro 2 bundle changed since it was installed".into());
    }
    let manifest = read_manifest(&root)?;
    let files = files(&manifest)?;
    let pinned = |name: &str| -> Result<Pinned, String> {
        let sha256 = files.get(name).ok_or_else(|| key_error(name))?;
        Ok(Pinned {
            path: root.join(name).to_string_lossy().into_owned(),
            sha256: sha256
                .as_str()
                .ok_or("manifest pin is not text")?
                .to_owned(),
        })
    };
    debug_assert!(PLUGIN_FILE.ends_with(PLUGIN) && RENDERER_FILE.ends_with(RENDERER));
    Ok(Some(Route {
        bundle: bundle.to_owned(),
        manifest_sha256: sha.to_owned(),
        plugin: pinned(PLUGIN_FILE)?,
        renderer: pinned(RENDERER_FILE)?,
    }))
}

/// For the controller's probe: (route, why not). A broken pin leaves the Pro 2 blocked.
pub fn probe(data_home: &Path) -> (Option<Route>, Option<String>) {
    match load_route(data_home) {
        Ok(Some(route)) => (Some(route), None),
        Ok(None) => (None, Some(NOT_INSTALLED.into())),
        Err(why) => (None, Some(format!("Pro 2 route is off: {why}"))),
    }
}

fn number(v: Option<&Value>) -> Option<f64> {
    match v {
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

fn valid_projection(v: Option<&Value>) -> bool {
    number(v).is_some_and(|p| PROJECTIONS.contains(&p))
}

fn valid_mount(v: Option<&Value>) -> bool {
    number(v).is_some_and(|m| (MOUNT_BOUNDS.0..=MOUNT_BOUNDS.1).contains(&m))
}

fn valid_rate(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Number(n)) => n.as_i64().is_some_and(|r| RATES.contains(&r)),
        _ => false,
    }
}

/// The saved settings object, or empty when it cannot be read.
fn saved(config_dir: &Path) -> serde_json::Map<String, Value> {
    match read_private_json(&config_dir.join(SETTINGS_FILE), DEFAULT_LIMIT) {
        Ok(Value::Object(m)) => m,
        _ => serde_json::Map::new(),
    }
}

/// The Pro 2's settings, each on its own, each this model's default if it cannot be read. `config_dir`: the folder
/// of `profiles.json`.
pub fn load_settings(config_dir: &Path) -> Settings {
    let saved = saved(config_dir);
    Settings {
        projection: Some(saved.get("projection"))
            .filter(|v| valid_projection(*v))
            .and_then(number)
            .unwrap_or(DEFAULT_PROJECTION),
        mount_pitch_deg: Some(saved.get("mount_pitch_deg"))
            .filter(|v| valid_mount(*v))
            .and_then(number)
            .unwrap_or(MOUNT_PITCH),
        pose_rate_hz: saved
            .get("pose_rate_hz")
            .filter(|v| valid_rate(Some(v)))
            .and_then(Value::as_i64)
            .unwrap_or(POSE_RATE),
    }
}

const SETTING_KEYS: [&str; 3] = ["projection", "mount_pitch_deg", "pose_rate_hz"];

fn check_setting(key: &str, value: &Value) -> Result<Value, String> {
    let fmt = |p: &f64| {
        crate::pyjson::float_repr(*p)
            .trim_end_matches(".0")
            .to_owned()
    };
    match key {
        "projection" if valid_projection(Some(value)) => Ok(json!(number(Some(value)))),
        "projection" => Err(format!(
            "projection must be one of {}",
            PROJECTIONS.iter().map(fmt).collect::<Vec<_>>().join(", ")
        )),
        "mount_pitch_deg" if valid_mount(Some(value)) => Ok(json!(number(Some(value)))),
        "mount_pitch_deg" => Err(format!(
            "mount pitch must be {} to {} degrees",
            fmt(&MOUNT_BOUNDS.0),
            fmt(&MOUNT_BOUNDS.1)
        )),
        "pose_rate_hz" if valid_rate(Some(value)) => Ok(value.clone()),
        "pose_rate_hz" => Err(format!(
            "pose rate must be one of {}",
            RATES
                .iter()
                .map(i64::to_string)
                .collect::<Vec<_>>()
                .join(", ")
        )),
        _ => Err("unknown Pro 2 setting".into()),
    }
}

/// An object written in the order given.
struct Ordered<'a>(&'a [(&'a str, Value)]);

impl Serialize for Ordered<'_> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut map = s.serialize_map(Some(self.0.len()))?;
        for (k, v) in self.0 {
            map.serialize_entry(k, v)?;
        }
        map.end()
    }
}

/// Only what `changes` names moves. The whole file is written: a value someone set is kept (even one that cannot be
/// read) and one nobody set is left out, so a default that later changes does not stay behind.
pub fn save_settings(changes: &[(&str, Value)], config_dir: &Path) -> Result<Settings, String> {
    if changes.is_empty() || changes.iter().any(|(k, _)| !SETTING_KEYS.contains(k)) {
        return Err("unknown Pro 2 setting".into());
    }
    let mut checked = Vec::new();
    for (key, value) in changes {
        checked.push((*key, check_setting(key, value)?));
    }
    let mut kept = saved(config_dir);
    for (key, value) in checked {
        kept.insert(key.to_owned(), value);
    }
    let mut out = vec![("schema", json!(1))];
    out.extend(
        SETTING_KEYS
            .iter()
            .filter_map(|k| kept.get(*k).map(|v| (*k, v.clone()))),
    );
    fs::create_dir_all(config_dir).map_err(text)?;
    let path = config_dir.join(SETTINGS_FILE);
    install::atomic_write(
        &path,
        crate::pyjson::spaced(&Ordered(&out)).as_bytes(),
        0o600,
    )
    .map_err(text)?;
    Ok(load_settings(config_dir))
}

/// The consent's Pro 2 block for a confirmed start.
pub fn consent_block(route: &Route, settings: &Settings) -> Value {
    json!({"bundle": route.bundle, "manifest_sha256": route.manifest_sha256, "projection": settings.projection,
           "pose_rate_hz": settings.pose_rate_hz, "mount_pitch_deg": settings.mount_pitch_deg})
}

/// Before a run does anything: this is still exactly the bundle installed when `consent` was given. (root, manifest).
pub fn require(consent: &Value, data_home: &Path) -> Result<(PathBuf, Value), String> {
    let route = load_route(data_home)?;
    let block = consent.get(PRO2_KEY).ok_or_else(|| key_error(PRO2_KEY))?;
    let Some(route) = route else {
        return Err("the Pro 2 bridge bundle is not installed".into());
    };
    if block.get("bundle").and_then(Value::as_str) != Some(route.bundle.as_str())
        || block.get("manifest_sha256").and_then(Value::as_str)
            != Some(route.manifest_sha256.as_str())
    {
        return Err("the Pro 2 route changed since this start was confirmed".into());
    }
    if consent.get("plugin") != Some(&route.plugin.to_json())
        || consent.get("renderer") != Some(&route.renderer.to_json())
    {
        return Err("Pro 2 consent is not the pinned bundle".into());
    }
    verify_manifest(&route.bundle)
}

#[cfg(test)]
mod tests;
