//! restore.py's snapshot side: the physical monitor's arrangement taken at run start, kept in memory and in the state
//! directory (`$XDG_STATE_HOME/viture-workspace/layouts/<run_id>.json`, 0600), so Stop and a crash recovery put it back.
//!
//! The eval that puts it back is [`crate::lua::restore`]'s; [`Snapshot`] is its type. The file is the Python's,
//! `json.dumps(snapshot, sort_keys=True)`, so either supervisor restores a layout the other saved.

use std::fs;
use std::io;
use std::os::unix::fs::DirBuilderExt;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use xr_wire::files::PrivateDir;

use crate::adopt::{occupied, regular, stays};
pub use crate::lua::restore::{Snapshot, Window};
use crate::lua::{self, restore as lua_restore};
use crate::private_file::strict_json;
use crate::py;
use crate::runner::dumps_sorted;

pub const FILE_LIMIT: usize = 262144;

fn pair(v: Option<&Value>) -> Option<(i64, i64)> {
    match py::list(v).map(Vec::as_slice) {
        Some([a, b])
            if [a, b]
                .iter()
                .all(|x| py::int_in(Some(x), -lua_restore::COORD, lua_restore::COORD)) =>
        {
            Some((py::int(Some(a))?, py::int(Some(b))?))
        }
        _ => None,
    }
}

fn refused(r: lua::Refused) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, r.0)
}

fn bad(why: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, why)
}

/// `capture`: the snapshot of the fallback `fallback`, from Hyprland's `workspaces`, `clients`, `monitors` and
/// `activewindow` replies.
pub fn capture(
    workspaces: &Value,
    clients: &Value,
    monitors: &Value,
    active: &Value,
    fallback: &str,
    run_id: &str,
) -> io::Result<Snapshot> {
    let groups = occupied(workspaces, clients, fallback).map_err(refused)?;
    let moving: Vec<&str> = groups
        .iter()
        .flat_map(|g| g.addresses.iter().map(String::as_str))
        .collect();
    let mut ids: Vec<i64> = workspaces
        .as_array()
        .into_iter()
        .flatten()
        .filter(|w| regular(w) && py::text(w.get("monitor")) == Some(fallback))
        .filter_map(|w| py::int(w.get("id")))
        .collect();
    ids.sort_unstable();
    ids.dedup();
    let mut windows = Vec::new();
    for c in clients.as_array().into_iter().flatten() {
        let Some(address) = py::text(c.get("address")).filter(|a| moving.contains(a)) else {
            continue;
        };
        if !c.is_object() || stays(c) {
            continue;
        }
        let workspace = c
            .get("workspace")
            .and_then(|w| py::int(w.get("id")))
            .ok_or_else(|| bad("invalid window"))?;
        let size = pair(c.get("size"))
            .filter(|s| s.0.min(s.1) > 0)
            .unwrap_or((0, 0));
        let small = |k: &str| {
            if py::int_in(c.get(k), 0, 3) {
                py::int(c.get(k)).unwrap_or(0)
            } else {
                0
            }
        };
        windows.push(Window {
            address: address.to_owned(),
            workspace,
            floating: py::is_true(c.get("floating")),
            at: pair(c.get("at")).unwrap_or((0, 0)),
            size,
            fullscreen: small("fullscreen"),
            client: small("fullscreenClient"),
        });
    }
    let monitors = monitors.as_array().map(Vec::as_slice).unwrap_or_default();
    let shown = monitors
        .iter()
        .find(|m| m.is_object() && py::text(m.get("name")) == Some(fallback))
        .and_then(|m| m.get("activeWorkspace"))
        .filter(|w| w.is_object())
        .and_then(|w| w.get("id"));
    let focused = monitors
        .iter()
        .find(|m| m.is_object() && py::is_true(m.get("focused")))
        .and_then(|m| py::text(m.get("name")));
    let focus = active
        .is_object()
        .then(|| py::text(active.get("address")))
        .flatten();
    let snapshot = Snapshot {
        schema: 1,
        run_id: run_id.to_owned(),
        monitor: fallback.to_owned(),
        active: if py::int_in(shown, 1, 2147483647) {
            py::int(shown)
        } else {
            None
        },
        focus: focus.filter(|f| lua::address(f)).map(str::to_owned),
        focus_monitor: focused.filter(|m| lua::token(m)).map(str::to_owned),
        workspaces: ids
            .iter()
            .map(|&i| (i, groups.iter().any(|g| g.id == i && g.whole)))
            .collect(),
        windows,
    };
    lua_restore::validate(&snapshot).map_err(refused)?;
    Ok(snapshot)
}

pub fn to_value(s: &Snapshot) -> Value {
    json!({
        "schema": s.schema, "run_id": s.run_id, "monitor": s.monitor, "active": s.active, "focus": s.focus,
        "focus_monitor": s.focus_monitor,
        "workspaces": s.workspaces.iter().map(|(id, whole)| json!({"id": id, "whole": whole})).collect::<Vec<_>>(),
        "windows": s.windows.iter().map(|w| json!({
            "address": w.address, "workspace": w.workspace, "floating": w.floating, "at": [w.at.0, w.at.1],
            "size": [w.size.0, w.size.1], "fullscreen": w.fullscreen, "client": w.client,
        })).collect::<Vec<_>>(),
    })
}

/// `validate` of a loaded snapshot: exactly capture()'s shape, else refused.
pub fn from_value(v: &Value) -> io::Result<Snapshot> {
    const KEYS: [&str; 8] = [
        "schema",
        "run_id",
        "monitor",
        "active",
        "focus",
        "focus_monitor",
        "workspaces",
        "windows",
    ];
    if !py::has_exactly(v, &KEYS) || !py::eq_int(v.get("schema"), 1) {
        return Err(bad("invalid layout snapshot"));
    }
    let optional_text = |k: &str, why: &'static str| match v.get(k) {
        Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.clone())),
        _ => Err(bad(why)),
    };
    let active = match v.get("active") {
        Some(Value::Null) => None,
        a if py::int(a).is_some() => py::int(a),
        _ => return Err(bad("invalid active workspace")),
    };
    let mut workspaces = Vec::new();
    for s in py::list(v.get("workspaces")).ok_or_else(|| bad("invalid workspaces"))? {
        let whole = match s.get("whole") {
            Some(Value::Bool(b)) => *b,
            _ => return Err(bad("invalid workspace")),
        };
        if !py::has_exactly(s, &["id", "whole"]) {
            return Err(bad("invalid workspace"));
        }
        workspaces.push((
            py::int(s.get("id")).ok_or_else(|| bad("invalid workspace"))?,
            whole,
        ));
    }
    let mut windows = Vec::new();
    for w in py::list(v.get("windows")).ok_or_else(|| bad("invalid windows"))? {
        const WKEYS: [&str; 7] = [
            "address",
            "workspace",
            "floating",
            "at",
            "size",
            "fullscreen",
            "client",
        ];
        let floating = match w.get("floating") {
            Some(Value::Bool(b)) => *b,
            _ => return Err(bad("invalid window")),
        };
        let (Some(address), Some(workspace), Some(at), Some(size), Some(fullscreen), Some(client)) = (
            py::text(w.get("address")),
            py::int(w.get("workspace")),
            pair(w.get("at")),
            pair(w.get("size")),
            py::int(w.get("fullscreen")),
            py::int(w.get("client")),
        ) else {
            return Err(bad("invalid window"));
        };
        if !py::has_exactly(w, &WKEYS) {
            return Err(bad("invalid window"));
        }
        windows.push(Window {
            address: address.to_owned(),
            workspace,
            floating,
            at,
            size,
            fullscreen,
            client,
        });
    }
    let snapshot = Snapshot {
        schema: 1,
        run_id: py::text(v.get("run_id"))
            .ok_or_else(|| bad("invalid identifier"))?
            .to_owned(),
        monitor: py::text(v.get("monitor"))
            .ok_or_else(|| bad("invalid layout monitor"))?
            .to_owned(),
        active,
        focus: optional_text("focus", "invalid focus")?,
        focus_monitor: optional_text("focus_monitor", "invalid focus monitor")?,
        workspaces,
        windows,
    };
    lua_restore::validate(&snapshot).map_err(refused)?;
    Ok(snapshot)
}

/// `layout_dir`.
pub fn layout_dir() -> PathBuf {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/state")
        });
    base.join("viture-workspace").join("layouts")
}

fn name(run_id: &str) -> io::Result<String> {
    if py::is_token(run_id) {
        Ok(format!("{run_id}.json"))
    } else {
        Err(bad("invalid identifier"))
    }
}

/// `save`: atomically, 0600, in a 0700 directory (created).
pub fn save(snapshot: &Snapshot, directory: &Path) -> io::Result<PathBuf> {
    lua_restore::validate(snapshot).map_err(refused)?;
    if let Some(parent) = directory.parent() {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(parent)?;
    }
    let file = name(&snapshot.run_id)?;
    PrivateDir::open(directory, true)?
        .replace(&file, dumps_sorted(&to_value(snapshot)).as_bytes())?;
    Ok(directory.join(file))
}

/// `load`: the run's saved snapshot, or None if there is none. A file that is not a valid one for this run fails.
pub fn load(run_id: &str, directory: &Path) -> io::Result<Option<Snapshot>> {
    let dir = match PrivateDir::open(directory, false) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        other => other?,
    };
    let raw = match dir.read(&name(run_id)?, FILE_LIMIT, true) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        other => other?,
    };
    let snapshot = from_value(&strict_json(&raw)?)?;
    if snapshot.run_id != run_id {
        return Err(bad("layout snapshot belongs to another run"));
    }
    Ok(Some(snapshot))
}

/// `discard`.
pub fn discard(run_id: &str, directory: &Path) -> io::Result<()> {
    match fs::remove_file(directory.join(name(run_id)?)) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_snapshot_saved_loads_back_and_goes() {
        let dir = std::env::temp_dir().join(format!("xr-restore-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let layouts = dir.join("state/layouts");
        let workspaces = json!([{"id": 1, "name": "1", "monitor": "DP-1"}, {"id": 3, "name": "3", "monitor": "DP-1"}]);
        let clients = json!([{"address": "0x1", "workspace": {"id": 1}, "pinned": false, "at": [10, 20],
                              "size": [800, 600], "floating": false, "fullscreen": 0, "fullscreenClient": 0}]);
        let monitors = json!([{"name": "DP-1", "activeWorkspace": {"id": 1}, "focused": true}]);
        let s = capture(
            &workspaces,
            &clients,
            &monitors,
            &json!({"address": "0x1"}),
            "DP-1",
            "r1",
        )
        .unwrap();
        assert_eq!(s.workspaces, vec![(1, true), (3, false)]);
        assert_eq!(
            (s.active, s.focus.as_deref(), s.focus_monitor.as_deref()),
            (Some(1), Some("0x1"), Some("DP-1"))
        );
        let path = save(&s, &layouts).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        assert!(
            text.starts_with(
                r#"{"active": 1, "focus": "0x1", "focus_monitor": "DP-1", "monitor": "DP-1""#
            ),
            "{text}"
        );
        let back = load("r1", &layouts).unwrap().unwrap();
        assert_eq!(dumps_sorted(&to_value(&back)), text);
        assert!(load("r2", &layouts).unwrap().is_none());
        discard("r1", &layouts).unwrap();
        assert!(load("r1", &layouts).unwrap().is_none());
        let _ = fs::remove_dir_all(&dir);
    }
}
