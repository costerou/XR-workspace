//! A saved setup the broker refused goes back to the default: `managed._setup_failed` and `UNCREATABLE`
//! (tools/native_spike/managed.py) with `profiles.discard_unusable`, `load_setup`, `default_setup`, `profiles_path` and
//! `PlacementStore.reset` (tools/native_spike/profiles.py), called by `_run_owned` when `create_outputs` fails and the
//! run's desktops are a saved setup (not the default three, on a glasses profile). Never in place of the create failure
//! itself.
//!
//! The reset rewrites the model's profile as `load_profile` cleans it, with the default setup, and keeps every other
//! model's profile as it was read: the bytes `save_profile` writes (`json.dump`, an atomic replace). Two differences
//! from the Python, on files a save never writes: `json.loads` takes NaN and Infinity, which [`Json`] refuses (the file
//! then reads as corrupt and nothing is reset), and an integer past i64 is held as a float.

use std::fs;
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

use serde::Serialize;
use serde::ser::SerializeMap;

use crate::geometry::{
    DEFAULT_DESKTOP, DEFAULT_PLACEMENT, Desktop, MAX_DESKTOPS, MAX_ROLL, PLACEMENT_KEYS, Placement,
};
use crate::managed::Error;
use crate::private_file::token;
use crate::pyjson::{self, Json};
use crate::supervisor::view::{self, MAX_SPACING, Settings};

/// `managed.UNCREATABLE`: the broker's verdicts on an output itself; any other failure keeps the saved setup.
pub const UNCREATABLE: [&str; 4] = [
    "creation incomplete",
    "missing readback",
    "actual geometry/workspace mismatch",
    "unusable output",
];

/// `profiles.MODELS`.
const MODELS: [&str; 4] = ["beast", "luma-ultra", "pro2", "unknown"];
/// `profiles.SCHEMA`.
const SCHEMA: i64 = 2;
/// `profiles.DEFAULT_SETUP_DESKTOPS`.
const DEFAULT_SETUP_DESKTOPS: usize = 3;
/// `profiles.NOTHING_SAVED`.
pub const NOTHING_SAVED: &str = "nothing saved yet";
/// `geometry.REMOVED_PLACEMENT_KEYS`.
const REMOVED_PLACEMENT_KEYS: [&str; 3] = ["yaw", "pitch", "roll"];
/// `tracking.DRIVERS`, `XRDRIVER`, `LUMA`, `LUMA_MODEL`.
const DRIVERS: [&str; 3] = ["xrdriver", "3dof", "luma"];
const XRDRIVER: &str = "xrdriver";

/// A monitor setup (`profiles.SETUP_KEYS`), in the profile's key order.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Setup {
    pub desktops: Vec<Desktop>,
    pub placements: Vec<Placement>,
    pub rolls: Vec<i64>,
}

/// `profiles.default_setup`: three flat 1920x1080 desktops at 100%, level.
pub fn default_setup() -> Setup {
    let n = DEFAULT_SETUP_DESKTOPS;
    Setup {
        desktops: vec![DEFAULT_DESKTOP; n],
        placements: vec![DEFAULT_PLACEMENT; n],
        rolls: vec![0; n],
    }
}

/// `profiles.profiles_path`: `$XDG_CONFIG_HOME/viture-workspace/profiles.json`, else under `~/.config`. None without
/// either.
pub fn profiles_path() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("viture-workspace").join("profiles.json"))
}

/// `_setup_failed(store, desktops)` is not None: the run's desktops are a saved setup, not the default three. (The
/// runner's store is always the glasses profile.)
pub fn is_saved_setup(desktops: &[Desktop]) -> bool {
    desktops != default_setup().desktops
}

/// `_setup_failed(store, desktops)(exc)`: when `error` is an [`UNCREATABLE`] refusal, the saved setup at `path` goes
/// back to the default ([`discard_unusable`]). A Rust error has no type, so the broker's verdict is its message alone:
/// only `ownership`'s verify gives these. `log`: `profiles.log`'s lines, without its `xr-workspace: ` prefix.
pub fn setup_failed(
    path: &Path,
    model: &str,
    desktops: &[Desktop],
    error: &Error,
    log: &mut dyn FnMut(&str),
) -> bool {
    UNCREATABLE.contains(&error.0.as_str())
        && discard_unusable(path, model, desktops, &error.0, log)
}

/// `profiles.discard_unusable`: unless the profile changed meanwhile (its setup is no longer `desktops`), its setup goes
/// back to the default (`PlacementStore.reset`), so the next start falls back instead of failing the same way; `log`
/// gets the setup it replaced. True if it reset.
pub fn discard_unusable(
    path: &Path,
    model: &str,
    desktops: &[Desktop],
    error: &str,
    log: &mut dyn FnMut(&str),
) -> bool {
    if !MODELS.contains(&model) {
        return false; // `_model` refuses it; a consent never names one
    }
    let (setup, reason) = load_setup(path, model);
    if reason.is_some() || setup.desktops != desktops {
        return false;
    }
    log(&format!(
        "the saved monitor setup could not be created ({error}); the next start uses three flat desktops. It was: {}",
        pyjson::compact(&setup)
    ));
    if let Err(e) = reset(path, model) {
        log(&format!(
            "the saved monitor setup could not be reset ({})",
            e.0
        ));
        return false;
    }
    true
}

/// `profiles.load_setup`: the setup the next start of `model` restores, or the default and why.
pub fn load_setup(path: &Path, model: &str) -> (Setup, Option<String>) {
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let text = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == io::ErrorKind::NotFound => {
            return (default_setup(), Some(NOTHING_SAVED.into()));
        }
        Err(e) => {
            return (
                default_setup(),
                Some(format!("{name} could not be read ({})", strerror(&e))),
            );
        }
    };
    let Some(data) = String::from_utf8(text)
        .ok()
        .and_then(|t| Json::parse(&t).ok())
    else {
        return (
            default_setup(),
            Some(format!("{name} is corrupt (not JSON)")),
        );
    };
    match profiles(&data) {
        Some(profiles) => setup_from(profiles.iter().find(|(k, _)| k == model).map(|(_, v)| v)),
        None => (
            default_setup(),
            Some(format!("{name} is corrupt (no schema {SCHEMA} profiles)")),
        ),
    }
}

/// `exc.strerror or exc`: the OS's message without Rust's " (os error N)".
fn strerror(e: &io::Error) -> String {
    let text = e.to_string();
    match text.rfind(" (os error ") {
        Some(i) => text[..i].to_owned(),
        None => text,
    }
}

/// The `profiles` object of a schema-2 file.
fn profiles(data: &Json) -> Option<&[(String, Json)]> {
    let schema_ok = match data.get("schema") {
        Some(Json::Int(n)) => *n == SCHEMA,
        Some(Json::Float(f)) => *f == SCHEMA as f64, // Python's ==
        _ => false,
    };
    if data.as_object().is_none() || !schema_ok {
        return None;
    }
    data.get("profiles")?.as_object()
}

/// `profiles.setup_from`: (setup, None) when `entry` holds a usable monitor setup, else (default, why).
pub fn setup_from(entry: Option<&Json>) -> (Setup, Option<String>) {
    let fail = |why: String| (default_setup(), Some(why));
    let Some(entry) = entry else {
        return fail(NOTHING_SAVED.into());
    };
    if entry.as_object().is_none() {
        return fail("the saved profile is not an object".into());
    }
    let Some(desktops) = entry.get("desktops") else {
        return fail(NOTHING_SAVED.into());
    };
    let desktops = match validate_desktops(desktops) {
        Ok(d) => d,
        Err(e) => return fail(format!("the saved desktops are invalid ({e})")),
    };
    let placements = match entry.get("placements") {
        None | Some(Json::Null) => Ok(vec![DEFAULT_PLACEMENT; desktops.len()]), // saved before placements existed
        Some(Json::Array(items)) => validate_placements(items, desktops.len()),
        Some(_) => Err("not a list".into()),
    };
    let placements = match placements {
        Ok(p) => p,
        Err(e) => return fail(format!("the saved placements are invalid ({e})")),
    };
    let rolls = match entry.get("rolls") {
        None | Some(Json::Null) => Ok(vec![0; desktops.len()]), // saved before rolls were kept: level
        Some(r) => validate_rolls(r, desktops.len()),
    };
    match rolls {
        Ok(rolls) => (
            Setup {
                desktops,
                placements,
                rolls,
            },
            None,
        ),
        Err(e) => fail(format!("the saved rolls are invalid ({e})")),
    }
}

/// `geometry.validate_desktops`.
fn validate_desktops(value: &Json) -> Result<Vec<Desktop>, String> {
    match value {
        Json::Array(items) if (1..=MAX_DESKTOPS).contains(&items.len()) => items
            .iter()
            .map(|d| Desktop::from_value(&d.to_value()).map_err(|e| e.0.to_owned()))
            .collect(),
        _ => Err(format!("1 to {MAX_DESKTOPS} desktops required")),
    }
}

/// `geometry.validate_placement(legacy_placement(p))`.
fn placement(p: &Json) -> Result<Placement, String> {
    let mut value = p.to_value();
    if let Some(map) = value.as_object_mut() {
        let legacy = PLACEMENT_KEYS.iter().chain(&REMOVED_PLACEMENT_KEYS);
        if map.len() == PLACEMENT_KEYS.len() + REMOVED_PLACEMENT_KEYS.len()
            && legacy.clone().all(|k| map.contains_key(*k))
        {
            map.retain(|k, _| PLACEMENT_KEYS.contains(&k.as_str()));
        }
    }
    Placement::from_value(&value).map_err(|e| e.0.to_owned())
}

/// `geometry.validate_placements([legacy_placement(p) for p in items], count)`.
fn validate_placements(items: &[Json], count: usize) -> Result<Vec<Placement>, String> {
    if items.len() != count {
        return Err(format!("exactly {count} placements required"));
    }
    items.iter().map(placement).collect()
}

/// `geometry.validate_rolls`.
fn validate_rolls(value: &Json, count: usize) -> Result<Vec<i64>, String> {
    let Json::Array(items) = value else {
        return Err(format!("exactly {count} rolls required"));
    };
    if items.len() != count {
        return Err(format!("exactly {count} rolls required"));
    }
    items
        .iter()
        .map(|r| match r.as_int() {
            Some(r) if (-MAX_ROLL..=MAX_ROLL).contains(&r) => Ok(r),
            _ => Err(format!("roll must be an integer -{MAX_ROLL}..{MAX_ROLL}")),
        })
        .collect()
}

/// A profile as `load_profile` cleans it, in `_clean`'s key order.
#[derive(Serialize)]
struct Profile {
    #[serde(flatten)]
    view: Settings,
    #[serde(flatten)]
    setup: Setup,
    previous_setup: Option<Previous>,
    tracking: bool,
    mode_switch: bool,
    tracking_driver: String,
    fallback: Option<String>,
}

/// `profiles._previous`: a setup plus its spacing.
#[derive(Serialize)]
struct Previous {
    #[serde(flatten)]
    setup: Setup,
    spacing: i64,
}

/// `profiles._previous`.
fn previous(value: Option<&Json>) -> Option<Previous> {
    let value = value.filter(|v| v.as_object().is_some())?;
    let (setup, reason) = setup_from(Some(value));
    let spacing = value
        .get("spacing")
        .and_then(Json::as_int)
        .filter(|s| (0..=MAX_SPACING).contains(s));
    match (reason, spacing) {
        (None, Some(spacing)) => Some(Previous { setup, spacing }),
        _ => None,
    }
}

/// `profiles._read_profiles`: the file's profiles, or none when it cannot be read.
fn read_profiles(path: &Path) -> Vec<(String, Json)> {
    fs::read_to_string(path)
        .ok()
        .and_then(|t| Json::parse(&t).ok())
        .and_then(|data| profiles(&data).map(<[_]>::to_vec))
        .unwrap_or_default()
}

/// `profiles._clean`: each field validated on its own; anything invalid or missing falls back to its default.
fn clean(data: &Json) -> Profile {
    let raw_desktops = match data.get("desktops") {
        Some(Json::Array(d)) if (1..=MAX_DESKTOPS).contains(&d.len()) => d.clone(),
        _ => vec![Json::Null; DEFAULT_SETUP_DESKTOPS],
    };
    let n = raw_desktops.len();
    let desktops = raw_desktops
        .iter()
        .map(|d| Desktop::from_value(&d.to_value()).unwrap_or(DEFAULT_DESKTOP))
        .collect();
    let saved = match data.get("placements") {
        Some(Json::Array(p)) if p.len() == n => p.as_slice(),
        _ => &[],
    };
    let placements = (0..n)
        .map(|i| {
            saved.get(i).map_or(DEFAULT_PLACEMENT, |p| {
                placement(p).unwrap_or(DEFAULT_PLACEMENT)
            })
        })
        .collect();
    let rolls = data
        .get("rolls")
        .and_then(|r| validate_rolls(r, n).ok())
        .unwrap_or_else(|| vec![0; n]); // missing (saved before rolls were kept), or invalid: level
    let flag = |key| data.get(key).and_then(Json::as_bool).unwrap_or(true);
    Profile {
        view: view::settings_from(data),
        setup: Setup {
            desktops,
            placements,
            rolls,
        },
        previous_setup: previous(data.get("previous_setup")),
        tracking: flag("tracking"),
        mode_switch: flag("mode_switch"),
        tracking_driver: data
            .get("tracking_driver")
            .and_then(Json::as_str)
            .filter(|d| DRIVERS.contains(d))
            .unwrap_or(XRDRIVER)
            .to_owned(),
        fallback: data
            .get("fallback")
            .and_then(Json::as_str)
            .filter(|f| token(f).is_ok())
            .map(str::to_owned),
    }
}

/// `profiles.load_profile`.
fn load_profile(path: &Path, model: &str) -> Profile {
    let raw = read_profiles(path)
        .into_iter()
        .find(|(k, _)| k == model)
        .map(|(_, v)| v);
    let raw = raw.filter(|r| r.as_object().is_some());
    if model == "pro2" {
        // No legacy layout migration or Luma startup choices before exact-model proof.
        let mut p = clean(raw.as_ref().unwrap_or(&Json::Object(Vec::new())));
        (p.tracking, p.tracking_driver, p.mode_switch) = (false, XRDRIVER.into(), false);
        return p;
    }
    let mut p = match raw {
        Some(raw) => clean(&raw),
        // Seeded from the v1 file, else defaults.
        None => {
            let settings = view::load_settings(&path.with_file_name("layout.json"));
            let seeded =
                Json::parse(&pyjson::compact(&settings)).expect("settings serialize as JSON");
            clean(&seeded)
        }
    };
    p.tracking_driver = if model == "luma-ultra" {
        "luma"
    } else {
        XRDRIVER
    }
    .into(); // tracking.default_driver
    p
}

/// `PlacementStore(path, model).reset()`: the profile's setup becomes the default; every other field is kept, and so is
/// every other model's profile. Written as `save_profile` writes it.
fn reset(path: &Path, model: &str) -> Result<(), OsError> {
    let profile = Profile {
        setup: default_setup(),
        ..load_profile(path, model)
    };
    write_profile(path, model, &profile)
}

/// `save_profile(path, model, profile)`'s write: every other model's profile kept as it was read.
fn write_profile(path: &Path, model: &str, profile: &Profile) -> Result<(), OsError> {
    let others: Vec<(String, Json)> = read_profiles(path)
        .into_iter()
        .filter(|(k, _)| k != model && MODELS.contains(&k.as_str()))
        .collect();
    save(
        path,
        &File {
            others: &others,
            model,
            profile,
        },
    )
}

/// The view's stores of one glasses model's profile (`profiles.ProfileStore`, `PlacementStore`), for
/// `view_controls::view`: each write keeps every other field of the profile and every other model's profile. An error
/// is the Python's `ValueError` / `OSError` text, which the view drops (its saves are best effort).
pub mod store {
    use super::*;

    fn model_ok(model: &str) -> Result<(), String> {
        if MODELS.contains(&model) {
            Ok(())
        } else {
            Err("unknown model".into())
        }
    }

    /// `geometry.validate_rolls(rolls, len(rolls))`.
    fn rolls_ok(rolls: &[i64]) -> Result<(), String> {
        if rolls.iter().all(|r| (-MAX_ROLL..=MAX_ROLL).contains(r)) {
            Ok(())
        } else {
            Err(format!("roll must be an integer -{MAX_ROLL}..{MAX_ROLL}"))
        }
    }

    fn write(path: &Path, model: &str, profile: &Profile) -> Result<(), String> {
        write_profile(path, model, profile).map_err(|e| e.0)
    }

    /// `ProfileStore.load()`: the profile's view settings.
    pub fn load_view(path: &Path, model: &str) -> Settings {
        load_profile(path, model).view
    }

    /// `ProfileStore.save(view_settings)`.
    pub fn save_view(path: &Path, model: &str, settings: &Settings) -> Result<(), String> {
        model_ok(model)?;
        if !settings.is_valid() {
            return Err("invalid settings".into());
        }
        let profile = Profile {
            view: settings.clone(),
            ..load_profile(path, model)
        };
        write(path, model, &profile)
    }

    /// `PlacementStore.save_layout(placements, rolls)`: one placement per desktop of the profile (more dropped,
    /// missing ones kept as saved), and the rolls cut or padded the same way (None: the saved ones stay).
    pub fn save_layout(
        path: &Path,
        model: &str,
        placements: &[Placement],
        rolls: Option<&[i64]>,
    ) -> Result<(), String> {
        model_ok(model)?;
        let mut profile = load_profile(path, model);
        let n = profile.setup.desktops.len();
        let placements =
            crate::geometry::validate_placements(placements).map_err(|e| e.0.to_owned())?;
        let saved = &profile.setup.placements;
        let mut full: Vec<Placement> = placements
            .iter()
            .chain(saved.iter().skip(placements.len()))
            .copied()
            .take(n)
            .collect();
        full.resize(n, DEFAULT_PLACEMENT);
        if let Some(rolls) = rolls {
            rolls_ok(rolls)?;
            let mut out: Vec<i64> = rolls
                .iter()
                .chain(profile.setup.rolls.iter().skip(rolls.len()))
                .copied()
                .collect();
            out.resize(out.len().max(n), 0);
            out.truncate(n);
            profile.setup.rolls = out;
        }
        profile.setup.placements = full;
        write(path, model, &profile)
    }

    /// `PlacementStore.save_desktops(desktops, placements, rolls)`: the run's whole monitor setup (None: level).
    pub fn save_desktops(
        path: &Path,
        model: &str,
        desktops: &[Desktop],
        placements: &[Placement],
        rolls: Option<&[i64]>,
    ) -> Result<(), String> {
        model_ok(model)?;
        let n = desktops.len();
        if !(1..=MAX_DESKTOPS).contains(&n) {
            return Err(format!("1 to {MAX_DESKTOPS} desktops required"));
        }
        let placements =
            crate::geometry::validate_placements(placements).map_err(|e| e.0.to_owned())?;
        if placements.len() != n {
            return Err(format!("exactly {n} placements required"));
        }
        let rolls = rolls.map_or_else(|| vec![0; n], <[i64]>::to_vec);
        if rolls.len() != n {
            return Err(format!("exactly {n} rolls required"));
        }
        rolls_ok(&rolls)?;
        let profile = Profile {
            setup: Setup {
                desktops: desktops.to_vec(),
                placements,
                rolls,
            },
            ..load_profile(path, model)
        };
        write(path, model, &profile)
    }

    /// `PlacementStore.reset(previous)` (Ctrl+Alt+R): the setup and the spacing become the default, and the layout it
    /// replaces (`desktops`: None or empty, the profile's) is kept as `previous_setup` unless it is the default.
    pub fn reset(
        path: &Path,
        model: &str,
        desktops: Option<&[Desktop]>,
        placements: &[Placement],
        rolls: &[i64],
        spacing: i64,
    ) -> Result<(), String> {
        model_ok(model)?;
        let mut profile = load_profile(path, model);
        let desktops = desktops
            .filter(|d| !d.is_empty())
            .unwrap_or(&profile.setup.desktops);
        let value = serde_json::json!({"desktops": desktops, "placements": placements, "rolls": rolls,
                                       "spacing": spacing});
        let kept = Json::parse(&value.to_string())
            .ok()
            .and_then(|v| previous(Some(&v)))
            .filter(|k| k.setup != default_setup() || k.spacing != view::DEFAULT_SPACING);
        profile.setup = default_setup();
        profile.view.spacing = view::DEFAULT_SPACING;
        if kept.is_some() {
            profile.previous_setup = kept;
        }
        write(path, model, &profile)
    }
}

/// `{'schema': SCHEMA, 'profiles': {**others, model: profile}}`.
struct File<'a> {
    others: &'a [(String, Json)],
    model: &'a str,
    profile: &'a Profile,
}

impl Serialize for File<'_> {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        struct Profiles<'a>(&'a File<'a>);
        impl Serialize for Profiles<'_> {
            fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
                let mut map = s.serialize_map(Some(self.0.others.len() + 1))?;
                for (k, v) in self.0.others {
                    map.serialize_entry(k, v)?;
                }
                map.serialize_entry(self.0.model, self.0.profile)?;
                map.end()
            }
        }
        let mut map = s.serialize_map(Some(2))?;
        map.serialize_entry("schema", &SCHEMA)?;
        map.serialize_entry("profiles", &Profiles(self))?;
        map.end()
    }
}

/// An `OSError` as Python prints it: `[Errno 13] Permission denied: 'path'`.
#[derive(Debug)]
struct OsError(String);

impl OsError {
    fn new(e: io::Error, paths: &[&Path]) -> OsError {
        let Some(errno) = e.raw_os_error() else {
            return OsError(e.to_string());
        };
        let mut text = format!("[Errno {errno}] {}", strerror(&e));
        for (i, p) in paths.iter().enumerate() {
            text += &format!("{}'{}'", if i == 0 { ": " } else { " -> " }, p.display());
        }
        OsError(text)
    }
}

/// `save_profile`'s write: `json.dump` into a fresh `.profiles.*.tmp` (mode 0600) beside the file, synced, then
/// replaced over it. A missing folder is made, mode 0700.
fn save(path: &Path, file: &File) -> Result<(), OsError> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(parent)
        .map_err(|e| OsError::new(e, &[parent]))?;
    let body = pyjson::spaced(file);
    let (tmp, mut f) = (0u32..)
        .map(|i| parent.join(format!(".profiles.{}-{i}.tmp", std::process::id())))
        .find_map(|tmp| {
            let opened = fs::File::options()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_NOFOLLOW)
                .mode(0o600)
                .open(&tmp);
            match opened {
                Ok(f) => Some(Ok((tmp, f))),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => None,
                Err(e) => Some(Err(OsError::new(e, &[&tmp]))),
            }
        })
        .expect("an unused name")?;
    let written = f
        .write_all(body.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| OsError::new(e, &[]))
        .and_then(|()| fs::rename(&tmp, path).map_err(|e| OsError::new(e, &[&tmp, path])));
    if written.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    written
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_three_are_not_a_saved_setup() {
        assert!(!is_saved_setup(&[DEFAULT_DESKTOP; 3]));
        assert!(is_saved_setup(&[DEFAULT_DESKTOP; 2]));
    }

    #[test]
    fn a_failure_that_is_not_the_brokers_verdict_keeps_the_file() {
        let dir = crate::testdir::TestDir::new("setup-failed");
        let path = dir.path().join("profiles.json");
        fs::write(&path, r#"{"schema": 2, "profiles": {"luma-ultra": {"desktops": [{"width": 1920, "height": 1080, "scale": 1.0}]}}}"#).unwrap();
        let before = fs::read(&path).unwrap();
        let one = [DEFAULT_DESKTOP];
        assert!(!setup_failed(
            &path,
            "luma-ultra",
            &one,
            &Error("broker epoch lost; preserve journal".into()),
            &mut |_| {}
        ));
        assert_eq!(fs::read(&path).unwrap(), before);
        assert!(setup_failed(
            &path,
            "luma-ultra",
            &one,
            &Error("missing readback".into()),
            &mut |_| {}
        ));
        assert_ne!(fs::read(&path).unwrap(), before);
    }

    /// `PlacementStore.save_desktops` then `reset(previous)` twice, the second of the default (which keeps the
    /// backup): the file the Python leaves (tools/native_spike/profiles.py, recorded 2026-11-11).
    #[test]
    fn a_reset_keeps_the_layout_it_replaces_as_the_python_does() {
        let dir = crate::testdir::TestDir::new("setup-reset");
        let path = dir.path().join("profiles.json");
        let d = [
            Desktop {
                width: 2560,
                height: 1080,
                scale: 1.0,
            },
            Desktop {
                width: 1920,
                height: 1080,
                scale: 1.25,
            },
        ];
        let moved = Placement {
            x: 10,
            y: -5,
            distance: 120,
            curvature: 30,
        };
        let p = [moved, DEFAULT_PLACEMENT];
        store::save_desktops(&path, "luma-ultra", &d, &p, Some(&[300, -40])).unwrap();
        store::reset(&path, "luma-ultra", Some(&d), &p, &[300, -40], 140).unwrap();
        let flat = [DEFAULT_PLACEMENT; 3];
        let spacing = view::DEFAULT_SPACING;
        store::reset(&path, "luma-ultra", None, &flat, &[0; 3], spacing).unwrap();
        let python = r#"{"schema": 2, "profiles": {"luma-ultra": {"spacing": 40, "mode": "anchor", "prediction": true, "stabilization": 25, "video_quality": {"filter": "catmull-rom", "deband": false}, "environment": {"background": "none", "last": "grid", "brightness": 100}, "immersive": {"global": false, "desktops": {}, "preset": "desktop", "strength": 100, "convergence": 92}, "desktops": [{"width": 1920, "height": 1080, "scale": 1.0}, {"width": 1920, "height": 1080, "scale": 1.0}, {"width": 1920, "height": 1080, "scale": 1.0}], "placements": [{"x": 0, "y": 0, "distance": 100, "curvature": 0}, {"x": 0, "y": 0, "distance": 100, "curvature": 0}, {"x": 0, "y": 0, "distance": 100, "curvature": 0}], "rolls": [0, 0, 0], "previous_setup": {"desktops": [{"width": 2560, "height": 1080, "scale": 1.0}, {"width": 1920, "height": 1080, "scale": 1.25}], "placements": [{"x": 10, "y": -5, "distance": 120, "curvature": 30}, {"x": 0, "y": 0, "distance": 100, "curvature": 0}], "rolls": [300, -40], "spacing": 140}, "tracking": true, "mode_switch": true, "tracking_driver": "luma", "fallback": null}}}"#;
        assert_eq!(fs::read_to_string(&path).unwrap(), python);
    }
}
