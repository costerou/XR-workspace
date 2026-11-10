//! What the panel's desktop row does to the run's outputs: tools/native_spike/desktops.py `plan`.
//!
//! Ultrawide, Add, Remove, Scale, Resolution and Ctrl+Alt+R's reset, as broker steps: remove at most one output, reshape
//! (resize or slide) the ones that stay, create the new ones. The outputs stay side by side in name order from the
//! first one's corner, and two outputs never share layout space while the steps run: what moves left or shrinks goes
//! first, left to right, then what moves right or grows, right to left. The worker that sends the steps is the run's.

use crate::adopt::output_number;
use crate::geometry::{DEFAULT_DESKTOP, DEFAULT_PLACEMENT, Desktop, MAX_DESKTOPS, Placement};
use crate::lua::{Refused, Result};
use crate::py;
use serde_json::{Value, json};

/// Ctrl+Alt+R leaves this many desktops (profiles.DEFAULT_SETUP_DESKTOPS).
pub const RESET_COUNT: usize = 3;
// The refusals spell these out (Refused holds a static string).
const _: () = assert!(RESET_COUNT == 3 && MAX_DESKTOPS == 4);
/// The Remove button's second press must come this soon after the first.
pub const REMOVE_ARM_S: f64 = 4.0;
pub const REFRESH_MILLIHZ: i64 = 120000;

/// One of the run's outputs as the broker creates it.
#[derive(Clone, Debug, PartialEq)]
pub struct Spec {
    pub id: String,
    pub name: String,
    pub index: i64,
    pub desktop: Desktop,
    pub refresh_millihz: i64,
    pub x: i64,
    pub y: i64,
}

impl Spec {
    /// A broker create spec: exactly id, name, index, width, height, refresh_millihz, scale, x, y.
    pub fn from_value(v: &Value) -> Result<Spec> {
        let keys = [
            "id",
            "name",
            "index",
            "width",
            "height",
            "refresh_millihz",
            "scale",
            "x",
            "y",
        ];
        let bad = || Refused("invalid output spec");
        if !py::has_exactly(v, &keys) {
            return Err(bad());
        }
        let name = py::text(v.get("name"))
            .filter(|n| py::is_output(n))
            .ok_or_else(bad)?;
        let int = |k: &str| py::int(v.get(k)).ok_or_else(bad);
        Ok(Spec {
            id: py::text(v.get("id")).ok_or_else(bad)?.to_owned(),
            name: name.to_owned(),
            index: int("index")?,
            desktop: Desktop::from_parts(v.get("width"), v.get("height"), v.get("scale"))?,
            refresh_millihz: int("refresh_millihz")?,
            x: int("x")?,
            y: int("y")?,
        })
    }

    pub fn to_value(&self) -> Value {
        json!({"id": self.id, "name": self.name, "index": self.index, "width": self.desktop.width,
               "height": self.desktop.height, "refresh_millihz": self.refresh_millihz, "scale": self.desktop.scale,
               "x": self.x, "y": self.y})
    }

    fn right(&self) -> i64 {
        self.x + self.desktop.logical_width()
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Action {
    Ultrawide,
    Add,
    Remove,
    /// The panel's Scale dropdown.
    Scale(f64),
    /// The panel's Resolution dropdown: width, height.
    Resolution(i64, i64),
    /// Ctrl+Alt+R: every desktop back to the default, RESET_COUNT of them.
    Reset,
}

impl Action {
    pub fn name(self) -> &'static str {
        match self {
            Action::Ultrawide => "ultrawide",
            Action::Add => "add",
            Action::Remove => "remove",
            Action::Scale(_) => "scale",
            Action::Resolution(..) => "resolution",
            Action::Reset => "reset",
        }
    }
}

/// What one action does. `specs`: the run's outputs afterwards, in name order. Steps, in order: `remove`, `reshape`
/// (already ordered), `create`. `placements`: one per spec. `focus`: the index in `specs` the panel should be on
/// afterwards (None: collapse it).
#[derive(Clone, Debug, PartialEq)]
pub struct Plan {
    pub action: Action,
    pub specs: Vec<Spec>,
    pub remove: Option<Spec>,
    pub reshape: Vec<Spec>,
    pub create: Vec<Spec>,
    pub placements: Vec<Placement>,
    pub focus: Option<usize>,
    /// Names whose pixel size changes (their capture restarts).
    pub resized: Vec<String>,
    /// Reset: where the removed desktop's windows go first.
    pub windows_to: Option<String>,
    /// Names whose logical size gets smaller (fit_windows pulls their windows in).
    pub shrunk: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Step<'a> {
    Remove(&'a Spec),
    Reshape(&'a Spec),
    Create(&'a Spec),
}

impl Plan {
    pub fn steps(&self) -> Vec<Step<'_>> {
        let remove = self.remove.iter().map(Step::Remove);
        remove
            .chain(self.reshape.iter().map(Step::Reshape))
            .chain(self.create.iter().map(Step::Create))
            .collect()
    }
}

/// `(name, desktop)` pairs in any order as create specs (ids empty), side by side in name order from `origin`.
pub fn lay_out(desktops: &[(String, Desktop)], origin: (i64, i64)) -> Result<Vec<Spec>> {
    let mut sorted: Vec<&(String, Desktop)> = desktops.iter().collect();
    sorted.sort_by_key(|(name, _)| output_number(name));
    let (mut x, y) = origin;
    let mut out = Vec::new();
    for (name, d) in sorted {
        out.push(Spec {
            id: String::new(),
            name: name.clone(),
            index: output_number(name) - 1,
            desktop: *d,
            refresh_millihz: REFRESH_MILLIHZ,
            x,
            y,
        });
        x += d.logical_width();
        if x > 2147483647 {
            return Err(Refused("layout overflow"));
        }
    }
    Ok(out)
}

/// The first desktop name neither in use nor `held`.
fn free_name(desktops: &[(String, Option<Desktop>)], held: &[&str]) -> Result<String> {
    (1..=MAX_DESKTOPS)
        .map(|n| format!("XR-{n}"))
        .find(|n| !desktops.iter().any(|(d, _)| d == n) && !held.contains(&n.as_str()))
        .ok_or(Refused("no free desktop name until the session restarts"))
}

/// The broker steps for `action` on desktop `index` of `specs` (the run's live outputs, with ids). `placements`: one
/// per spec. `held`: names Add must not take besides those of `specs` (a failed create's journal record holds its name
/// until recovery). `new_id`: the id of each output to create (a random UUID in a run). Refused if the action cannot be
/// done: Add at MAX_DESKTOPS or with no free name, Remove of the last one, a scale that does not divide the desktop
/// exactly, a size the broker would refuse. Reset ignores `index` beyond checking it.
pub fn plan(
    specs: &[Spec],
    placements: &[Placement],
    action: Action,
    index: usize,
    origin: Option<(i64, i64)>,
    held: &[&str],
    new_id: &mut dyn FnMut() -> String,
) -> Result<Plan> {
    if index >= specs.len() {
        return Err(Refused("no such desktop"));
    }
    if placements.len() != specs.len() {
        return Err(Refused("one placement per desktop required"));
    }
    let mut specs: Vec<&Spec> = specs.iter().collect();
    specs.sort_by_key(|s| output_number(&s.name));
    let origin = origin.unwrap_or((specs[0].x, specs[0].y));
    let by_name = |name: &str| specs.iter().copied().find(|s| s.name == name);
    // In the Python's dict order: the specs' names in name order, then any added.
    let mut placed: Vec<(String, Placement)> = specs
        .iter()
        .zip(placements)
        .map(|(s, p)| (s.name.clone(), *p))
        .collect();
    let mut desktops: Vec<(String, Option<Desktop>)> = specs
        .iter()
        .map(|s| (s.name.clone(), Some(s.desktop)))
        .collect();
    let mut target = specs[index].name.clone();
    let (mut removed, mut windows_to) = (None, None);
    let at = |desktops: &[(String, Option<Desktop>)], name: &str| {
        desktops.iter().position(|(n, _)| n == name)
    };
    match action {
        Action::Ultrawide => {
            let i = index;
            desktops[i].1 = desktops[i].1.map(Desktop::toggle_ultrawide);
        }
        Action::Scale(scale) => {
            let i = index;
            desktops[i].1 = Some(specs[i].desktop.with_scale(scale)?);
        }
        Action::Resolution(width, height) => {
            let i = index;
            desktops[i].1 = Some(specs[i].desktop.with_resolution(width, height)?);
        }
        Action::Reset => {
            let names: Vec<String> = desktops.iter().map(|(n, _)| n.clone()).collect();
            let extra = &names[RESET_COUNT.min(names.len())..];
            if extra.len() > 1 {
                // Outputs are XR-1..XR-9, so at most 6 over.
                return Err(Refused(match extra.len() {
                    2 => "a reset removes one desktop at most (2 over 3)",
                    3 => "a reset removes one desktop at most (3 over 3)",
                    4 => "a reset removes one desktop at most (4 over 3)",
                    5 => "a reset removes one desktop at most (5 over 3)",
                    _ => "a reset removes one desktop at most (6 over 3)",
                }));
            }
            if let Some(name) = extra.first() {
                removed = by_name(name).cloned();
                windows_to = Some(names[RESET_COUNT - 1].clone());
                desktops.retain(|(n, _)| n != name);
                placed.retain(|(n, _)| n != name);
            }
            while desktops.len() < RESET_COUNT {
                let name = free_name(&desktops, held)?;
                desktops.push((name, None));
            }
            for (name, d) in &mut desktops {
                *d = Some(DEFAULT_DESKTOP);
                match placed.iter_mut().find(|(n, _)| n == name) {
                    Some((_, p)) => *p = DEFAULT_PLACEMENT,
                    None => placed.push((name.clone(), DEFAULT_PLACEMENT)),
                }
            }
        }
        Action::Add => {
            if specs.len() >= MAX_DESKTOPS {
                return Err(Refused("at most 4 desktops"));
            }
            let name = free_name(&desktops, held)?;
            let model = specs[index].desktop;
            target = name.clone();
            desktops.push((
                name.clone(),
                Some(if model.is_ultrawide() {
                    model.toggle_ultrawide()
                } else {
                    model
                }),
            ));
            placed.push((name, DEFAULT_PLACEMENT));
        }
        Action::Remove => {
            if specs.len() <= 1 {
                return Err(Refused("a session keeps at least one desktop"));
            }
            removed = Some(specs[index].clone());
            if let Some(i) = at(&desktops, &target) {
                desktops.remove(i);
            }
            placed.retain(|(n, _)| *n != target);
        }
    }
    let desktops: Vec<(String, Desktop)> = desktops
        .into_iter()
        .map(|(n, d)| (n, d.unwrap_or(DEFAULT_DESKTOP)))
        .collect();
    let laid = lay_out(&desktops, origin)?;
    let (mut after, mut moves, mut creates, mut resized, mut shrunk) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new(), Vec::new());
    for mut s in laid {
        match by_name(&s.name) {
            None => {
                s.id = new_id();
                creates.push(s.clone());
            }
            Some(old) => {
                s.id = old.id.clone();
                let (d, o) = (s.desktop, old.desktop);
                let size_changed = d.width != o.width || d.height != o.height || d.scale != o.scale;
                if size_changed || s.x != old.x || s.y != old.y {
                    moves.push((old, s.clone()));
                }
                if size_changed {
                    resized.push(s.name.clone());
                }
                if d.logical_width() < o.logical_width() || d.logical_height() < o.logical_height()
                {
                    shrunk.push(s.name.clone());
                }
            }
        }
        after.push(s);
    }
    let (mut first, mut rest): (Vec<_>, Vec<_>) = moves
        .into_iter()
        .partition(|(o, n)| n.x <= o.x && n.right() <= o.right());
    first.sort_by_key(|(_, n)| n.x);
    rest.sort_by_key(|(_, n)| -n.x);
    let names: Vec<&str> = after.iter().map(|s| s.name.as_str()).collect();
    let focus = match action {
        Action::Remove | Action::Reset => None,
        _ => names.iter().position(|n| *n == target),
    };
    let placements = names
        .iter()
        .map(|n| {
            placed
                .iter()
                .find(|(p, _)| p == n)
                .map(|(_, p)| *p)
                .unwrap_or(DEFAULT_PLACEMENT)
        })
        .collect();
    Ok(Plan {
        action,
        reshape: first.into_iter().chain(rest).map(|(_, n)| n).collect(),
        specs: after,
        remove: removed,
        create: creates,
        placements,
        focus,
        resized,
        windows_to,
        shrunk,
    })
}

/// A random version 4 UUID, from the kernel (each output a run creates gets one).
pub fn random_id() -> std::io::Result<String> {
    Ok(std::fs::read_to_string("/proc/sys/kernel/random/uuid")?
        .trim()
        .to_owned())
}
