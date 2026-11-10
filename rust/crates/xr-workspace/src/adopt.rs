//! Taking the physical monitor's windows onto the run's desktops at run start: tools/native_spike/adopt.py.
//!
//! The occupied regular workspaces of the fallback monitor, in id order, go to XR-1, XR-2, ... in turn (past the last
//! desktop, to the last). A workspace moves whole unless something on it stays: special (scratchpad) workspaces, pinned
//! windows and the XR Workspace panel never leave the physical monitor (`stays`); the other windows of such a workspace
//! move one by one, top to bottom, left to right. lua::adopt makes the evals.

use crate::lua::{self, Refused, Result};
use crate::py;
use serde_json::Value;

pub const PANEL_CLASS: &str = "org.quickshell";
pub const PANEL_TITLE: &str = "XR Workspace";
/// The panel's title before the rename.
pub const LEGACY_PANEL_TITLES: [&str; 1] = ["VITURE Workspace"];

/// A regular workspace: a positive id and a name that is not a special one.
pub(crate) fn regular(workspace: &Value) -> bool {
    py::int_in(workspace.get("id"), 1, 2147483647)
        && py::text(workspace.get("name")).is_some_and(|n| !n.starts_with("special"))
}

/// A window that never leaves the physical monitor: pinned (anything but an explicit `"pinned": false`), or the XR
/// Workspace panel itself.
pub fn stays(window: &Value) -> bool {
    !py::is_false(window.get("pinned"))
        || (py::text(window.get("class")) == Some(PANEL_CLASS)
            && py::text(window.get("title"))
                .is_some_and(|t| t == PANEL_TITLE || LEGACY_PANEL_TITLES.contains(&t)))
}

/// One of the fallback's workspaces with windows to move.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Occupied {
    pub id: i64,
    /// The workspace moves as one (nothing on it stays); otherwise only `addresses` move.
    pub whole: bool,
    /// The windows that move, in layout order.
    pub addresses: Vec<String>,
}

/// The fallback `monitor`'s workspaces with windows to move, in id order, from Hyprland's `workspaces` and `clients`.
pub fn occupied(workspaces: &Value, clients: &Value, monitor: &str) -> Result<Vec<Occupied>> {
    let (Some(workspaces), Some(clients)) = (workspaces.as_array(), clients.as_array()) else {
        return Err(Refused("invalid snapshot"));
    };
    let mut ids: Vec<i64> = workspaces
        .iter()
        .filter(|w| regular(w) && py::text(w.get("monitor")) == Some(monitor))
        .filter_map(|w| py::int(w.get("id")))
        .collect();
    ids.sort_unstable();
    ids.dedup();
    let mut out = Vec::new();
    for wid in ids {
        let on: Vec<&Value> = clients
            .iter()
            .filter(|c| c.is_object() && c.get("workspace").is_some_and(Value::is_object))
            .filter(|c| py::eq_int(c["workspace"].get("id"), wid))
            .collect();
        if on
            .iter()
            .any(|c| !py::text(c.get("address")).is_some_and(py::is_address))
        {
            return Err(Refused("invalid window address"));
        }
        let mut movable: Vec<&Value> = on.iter().copied().filter(|c| !stays(c)).collect();
        if movable.is_empty() {
            continue;
        }
        movable.sort_by_key(|c| layout_order(c));
        out.push(Occupied {
            id: wid,
            whole: movable.len() == on.len(),
            addresses: movable
                .iter()
                .map(|c| c["address"].as_str().unwrap_or_default().to_owned())
                .collect(),
        });
    }
    Ok(out)
}

/// Top to bottom, left to right; a window without a whole-pixel position first.
fn layout_order(c: &Value) -> (i64, i64) {
    match py::list(c.get("at")).map(Vec::as_slice) {
        Some([x, y]) => match (py::int(Some(x)), py::int(Some(y))) {
            (Some(x), Some(y)) => (y, x),
            _ => (0, 0),
        },
        _ => (0, 0),
    }
}

pub(crate) fn output_number(name: &str) -> i64 {
    name.split_once('-')
        .and_then(|(_, n)| n.parse().ok())
        .unwrap_or(0)
}

/// `names` checked (XR-1..XR-9, at least one) and in number order.
pub(crate) fn outputs(names: &[&str]) -> Result<Vec<String>> {
    if names.is_empty() || !names.iter().all(|n| py::is_output(n)) {
        return Err(Refused("invalid outputs"));
    }
    let mut names: Vec<String> = names.iter().map(|n| (*n).to_owned()).collect();
    names.sort_by_key(|n| output_number(n));
    Ok(names)
}

/// What adoption moves: whole workspaces and loose windows, each with the output it goes to.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Moves {
    /// `(workspace id, output)`.
    pub workspaces: Vec<(i64, String)>,
    /// `(address, output)`.
    pub windows: Vec<(String, String)>,
}

impl Moves {
    /// The eval(s) that make the moves (lua::adopt::lua).
    pub fn lua(&self) -> Result<Vec<String>> {
        let workspaces: Vec<(i64, &str)> = self
            .workspaces
            .iter()
            .map(|(i, o)| (*i, o.as_str()))
            .collect();
        let windows: Vec<(&str, &str)> = self
            .windows
            .iter()
            .map(|(a, o)| (a.as_str(), o.as_str()))
            .collect();
        lua::adopt::lua(&workspaces, &windows)
    }
}

/// The moves: the k-th occupied workspace goes to the k-th desktop of `names` (in name order); the ones past the last
/// desktop go to the last.
pub fn plan(workspaces: &Value, clients: &Value, monitor: &str, names: &[&str]) -> Result<Moves> {
    let names = outputs(names)?;
    let mut moves = Moves::default();
    for (k, o) in occupied(workspaces, clients, monitor)?
        .into_iter()
        .enumerate()
    {
        let target = &names[k.min(names.len() - 1)];
        if o.whole {
            moves.workspaces.push((o.id, target.clone()));
        } else {
            moves
                .windows
                .extend(o.addresses.into_iter().map(|a| (a, target.clone())));
        }
    }
    Ok(moves)
}
