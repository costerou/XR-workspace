//! Window memory: every window goes back to the XR desktop, workspace and place it had when XR last stopped:
//! tools/native_spike/window_memory.py (its module docstring is the design).
//!
//! The pure part, over Hyprland's JSON replies: what is remembered (`capture`, `validate`), the file it lives in
//! (`Store`), which open window is which remembered one (`pair_up`) and what putting them back takes (`plan`);
//! lua::window_memory makes the evals that do it. Only the windows adoption would take are ever touched: never a
//! special workspace, a pinned window or the XR Workspace panel (adopt::stays). The tracker that keeps the memory
//! current and its worker are the run's.

use crate::adopt::{self, output_number, regular, stays};
use crate::fit_windows::{self, Output};
use crate::geometry::{Desktop, MAX_DESKTOPS, MAX_ROLL, Placement};
use crate::lua::{Refused, Result, window_memory::Placement as Moves};
use crate::py;
use serde_json::{Map, Value, json};
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};

pub const SCHEMA: i64 = 1;
pub const FILE_NAME: &str = "window-memory.json";
/// A file past this is not read.
pub const FILE_LIMIT: u64 = 4 << 20;
/// Class and titles are cut to this many characters, when saved and when compared.
pub const TEXT_LIMIT: usize = 256;
/// Windows remembered per glasses model; any more behave as if unmatched.
pub const MAX_WINDOWS: usize = 256;
pub const COORD: i64 = 1 << 20;
pub const INSTANCE_LIMIT: usize = 128;
/// Tiled windows this close in x share a scrolling column.
pub const COLUMN_TOLERANCE_PX: i64 = 1;
/// profiles.MODELS: one memory per glasses model.
pub const MODELS: [&str; 4] = ["beast", "luma-ultra", "pro2", "unknown"];
const WINDOW_KEYS: [&str; 18] = [
    "address",
    "class",
    "title",
    "initial_title",
    "pid",
    "output",
    "index",
    "workspace",
    "layout",
    "floating",
    "at",
    "size",
    "fullscreen",
    "client",
    "order",
    "column",
    "parked",
    "landing",
];
const DESKTOP_KEYS: [&str; 6] = ["width", "height", "scale", "active", "placement", "roll"];
/// pair_up(): each pass the fields that must agree, after the address.
const PASSES: [&[&str]; 5] = [
    &["class", "title"],
    &["class", "initial_title", "pid"],
    &["class", "initial_title"],
    &["class", "pid"],
    &["class"],
];
const MAX_ID: i64 = 2147483647;

fn pair(v: Option<&Value>) -> bool {
    py::list(v)
        .is_some_and(|l| l.len() == 2 && l.iter().all(|x| py::int_in(Some(x), -COORD, COORD)))
}

fn text(v: Option<&Value>) -> String {
    py::cut(v, TEXT_LIMIT)
}

/// A coordinate as Hyprland's JSON gives it, as an int, or None.
fn whole(v: &Value) -> Option<i64> {
    match py::int(Some(v)) {
        Some(i) => Some(i),
        None => match v {
            Value::Number(n) if n.is_f64() => n
                .as_f64()
                .filter(|f| f.abs() < COORD as f64)
                .map(|f| py::round(f) as i64),
            _ => None,
        },
    }
}

fn coords(v: Option<&Value>) -> Option<[i64; 2]> {
    let [a, b] = py::list(v)?.as_slice() else {
        return None;
    };
    let out = [whole(a)?, whole(b)?];
    out.iter()
        .all(|c| (-COORD..=COORD).contains(c))
        .then_some(out)
}

fn int_or_none(v: Option<&Value>, low: i64, high: i64) -> bool {
    matches!(v, Some(Value::Null)) || py::int_in(v, low, high)
}

fn desktop_of(monitor: &Value, shown: Option<&Value>, context: Option<&Value>) -> Result<Value> {
    let (w, h, s) = (
        monitor.get("width"),
        monitor.get("height"),
        monitor.get("scale"),
    );
    Desktop::from_parts(w, h, s)?;
    let shown = shown
        .filter(|v| py::int_in(Some(v), 1, MAX_ID))
        .cloned()
        .unwrap_or(Value::Null);
    let context = context.filter(|c| c.is_object());
    let get = |k: &str| {
        context
            .and_then(|c| c.get(k))
            .cloned()
            .unwrap_or(Value::Null)
    };
    Ok(
        json!({"width": w, "height": h, "scale": s, "active": shown, "placement": get("placement"), "roll": get("roll")}),
    )
}

fn tiled(c: &Value) -> bool {
    !py::is_true(c.get("floating"))
        && !py::truthy(c.get("grouped"))
        && py::off(c.get("fullscreen"))
        && py::off(c.get("fullscreenClient"))
}

/// The memory of the desktops `names`, from Hyprland's j/clients, j/workspaces and j/monitors replies. `context`:
/// optional `{name: {"placement", "roll"}}`, as the view has them now. A malformed window is skipped.
pub fn capture(
    clients: &Value,
    workspaces: &Value,
    monitors: &Value,
    names: &[&str],
    instance: &str,
    context: Option<&Value>,
) -> Result<Value> {
    let (Some(clients), Some(workspaces), Some(monitors)) = (
        clients.as_array(),
        workspaces.as_array(),
        monitors.as_array(),
    ) else {
        return Err(Refused("invalid compositor state"));
    };
    let names = adopt::outputs(names)?;
    let index = |n: &str| names.iter().position(|x| x == n);
    // {name: monitor}, the last one of a name winning, in first-seen order.
    let mut found: Vec<(&str, &Value)> = Vec::new();
    for m in monitors.iter().filter(|m| m.is_object()) {
        let Some(name) = py::text(m.get("name")).filter(|n| index(n).is_some()) else {
            continue;
        };
        match found.iter_mut().find(|(n, _)| *n == name) {
            Some(slot) => slot.1 = m,
            None => found.push((name, m)),
        }
    }
    let mut desktops = Map::new();
    for (name, m) in &found {
        let shown = m
            .get("activeWorkspace")
            .filter(|a| a.is_object())
            .map(|a| a.get("id").unwrap_or(&Value::Null));
        let context = context
            .filter(|c| c.is_object())
            .and_then(|c| c.get(*name))
            .filter(|c| py::truthy(Some(c)));
        desktops.insert((*name).to_owned(), desktop_of(m, shown, context)?);
    }
    let monitor_of = |name: &str| found.iter().find(|(n, _)| *n == name).map(|(_, m)| *m);
    // {id: workspace}, regular ones on those desktops.
    let mut spaces: HashMap<i64, &Value> = HashMap::new();
    for w in workspaces {
        if regular(w) && py::text(w.get("monitor")).is_some_and(|m| monitor_of(m).is_some()) {
            spaces.insert(py::int(w.get("id")).unwrap_or_default(), w);
        }
    }
    type Row<'a> = (&'a Value, [i64; 2], [i64; 2]); // the client, its at and size
    let mut on: HashMap<i64, Vec<Row>> = HashMap::new();
    for c in clients {
        let Some(wid) = c
            .get("workspace")
            .filter(|w| w.is_object())
            .and_then(|w| py::int(w.get("id")))
        else {
            continue;
        };
        if !c.is_object()
            || !spaces.contains_key(&wid)
            || !py::text(c.get("address")).is_some_and(py::is_address)
        {
            continue;
        }
        if stays(c) || py::is_false(c.get("mapped")) {
            continue;
        }
        let (Some(at), Some(size)) = (coords(c.get("at")), coords(c.get("size"))) else {
            continue;
        };
        if size[0].min(size[1]) < 0 {
            continue;
        }
        on.entry(wid).or_default().push((c, at, size));
    }
    let monitor_name = |wid: &i64| py::text(spaces[wid].get("monitor")).unwrap_or_default();
    let mut ids: Vec<i64> = on.keys().copied().collect();
    ids.sort_by_key(|i| (index(monitor_name(i)), *i));
    let mut windows = Vec::new();
    for wid in ids {
        let output = monitor_name(&wid);
        let layout = py::text(spaces[&wid].get("tiledLayout"))
            .filter(|l| py::is_layout(l))
            .unwrap_or("");
        let entries = &on[&wid];
        let mut rows: Vec<&(&Value, [i64; 2], [i64; 2])> =
            entries.iter().filter(|r| tiled(r.0)).collect();
        rows.sort_by(|a, b| {
            (a.1[0], a.1[1], py::text(a.0.get("address"))).cmp(&(
                b.1[0],
                b.1[1],
                py::text(b.0.get("address")),
            ))
        });
        let mut order: HashMap<&str, (i64, i64)> = HashMap::new();
        let (mut column, mut last) = (-1i64, None);
        for (k, (c, at, _)) in rows.iter().enumerate() {
            if last.is_none_or(|l: i64| (at[0] - l).abs() > COLUMN_TOLERANCE_PX) {
                column += 1;
            }
            last = Some(at[0]);
            let k = k as i64;
            order.insert(
                py::text(c.get("address")).unwrap_or_default(),
                (k, if layout == "scrolling" { column } else { k }),
            );
        }
        let origin = monitor_of(output).unwrap_or(&Value::Null);
        let placed = rows
            .into_iter()
            .chain(entries.iter().filter(|r| !tiled(r.0)));
        for (c, at, size) in placed {
            // A monitor whose corner is not whole: the window's place is not either (_pair refuses it).
            let (Some(ox), Some(oy)) = (py::int(origin.get("x")), py::int(origin.get("y"))) else {
                continue;
            };
            let at = [at[0] - ox, at[1] - oy];
            if !at.iter().all(|v| (-COORD..=COORD).contains(v)) {
                continue;
            }
            let address = py::text(c.get("address")).unwrap_or_default();
            let small = |k: &str| {
                c.get(k)
                    .filter(|v| py::int_in(Some(v), 0, 3))
                    .cloned()
                    .unwrap_or(json!(0))
            };
            let pid = c
                .get("pid")
                .filter(|v| py::int_in(Some(v), 0, 1 << 31))
                .cloned()
                .unwrap_or(json!(0));
            let (o, col) = order
                .get(address)
                .map_or((Value::Null, Value::Null), |(o, col)| {
                    (json!(o), json!(col))
                });
            windows.push(json!({
                "address": address, "class": text(c.get("class")), "title": text(c.get("title")),
                "initial_title": text(c.get("initialTitle")), "pid": pid, "output": output,
                "index": index(output), "workspace": wid, "layout": layout, "floating": py::is_true(c.get("floating")),
                "at": at, "size": size, "fullscreen": small("fullscreen"), "client": small("fullscreenClient"),
                "order": o, "column": col, "parked": false, "landing": null,
            }));
        }
    }
    windows.truncate(MAX_WINDOWS);
    validate(json!({"instance": instance, "desktops": desktops, "windows": windows}))
}

fn valid_window(w: &Value) -> bool {
    let s = |k: &str| py::text(w.get(k));
    py::has_exactly(w, &WINDOW_KEYS)
        && s("address").is_some_and(py::is_address)
        && ["class", "title", "initial_title"]
            .iter()
            .all(|k| s(k).is_some_and(|t| t.chars().count() <= TEXT_LIMIT))
        && py::int_in(w.get("pid"), 0, 1 << 31)
        && s("output").is_some_and(py::is_output)
        && int_or_none(w.get("index"), 0, MAX_DESKTOPS as i64 - 1)
        && py::int_in(w.get("workspace"), 1, MAX_ID)
        && s("layout").is_some_and(|l| l.is_empty() || py::is_layout(l))
        && w["floating"].is_boolean()
        && pair(w.get("at"))
        && pair(w.get("size"))
        && w["size"]
            .as_array()
            .is_some_and(|s| s.iter().all(|v| v.as_i64().is_some_and(|v| v >= 0)))
        && py::int_in(w.get("fullscreen"), 0, 3)
        && py::int_in(w.get("client"), 0, 3)
        && int_or_none(w.get("order"), 0, MAX_WINDOWS as i64)
        && int_or_none(w.get("column"), 0, MAX_WINDOWS as i64)
        && w["parked"].is_boolean()
        && (w["landing"].is_null() || s("landing").is_some_and(py::is_token))
}

fn valid_desktop(d: &Value) -> bool {
    py::has_exactly(d, &DESKTOP_KEYS)
        && Desktop::from_parts(d.get("width"), d.get("height"), d.get("scale")).is_ok()
        && (d["placement"].is_null() || Placement::from_value(&d["placement"]).is_ok())
        && int_or_none(d.get("active"), 1, MAX_ID)
        && int_or_none(d.get("roll"), -MAX_ROLL, MAX_ROLL)
}

/// The memory if it has exactly the shape capture() makes. Loaded ones are checked again.
pub fn validate(memory: Value) -> Result<Value> {
    if !py::has_exactly(&memory, &["instance", "desktops", "windows"])
        || !py::text(memory.get("instance")).is_some_and(|i| i.chars().count() <= INSTANCE_LIMIT)
    {
        return Err(Refused("invalid window memory"));
    }
    if !memory["desktops"]
        .as_object()
        .is_some_and(|d| d.iter().all(|(n, d)| py::is_output(n) && valid_desktop(d)))
    {
        return Err(Refused("invalid desktops in the window memory"));
    }
    let Some(windows) = memory["windows"]
        .as_array()
        .filter(|w| w.len() <= MAX_WINDOWS && w.iter().all(valid_window))
    else {
        return Err(Refused("invalid windows in the window memory"));
    };
    let mut addresses: Vec<&str> = windows
        .iter()
        .filter_map(|w| w["address"].as_str())
        .collect();
    addresses.sort_unstable();
    addresses.dedup();
    if addresses.len() != windows.len() {
        return Err(Refused("a window remembered twice"));
    }
    Ok(memory)
}

/// The memory file beside the profiles file.
pub fn memory_path(profiles: &Path) -> PathBuf {
    profiles.with_file_name(FILE_NAME)
}

/// One glasses model's memory in the shared file; the other models' are preserved.
pub struct Store {
    pub path: PathBuf,
    pub model: String,
}

impl Store {
    pub fn new(path: impl Into<PathBuf>, model: &str) -> Result<Store> {
        if !MODELS.contains(&model) {
            return Err(Refused("unknown model"));
        }
        Ok(Store {
            path: path.into(),
            model: model.to_owned(),
        })
    }

    fn models(&self) -> Map<String, Value> {
        let read = || -> Option<Map<String, Value>> {
            if std::fs::metadata(&self.path).ok()?.len() > FILE_LIMIT {
                return None;
            }
            let data: Value = serde_json::from_slice(&std::fs::read(&self.path).ok()?).ok()?;
            if !data.is_object() || !py::eq_int(data.get("schema"), SCHEMA) {
                return None;
            }
            data.get("models")?.as_object().cloned()
        };
        read().unwrap_or_default()
    }

    /// The saved memory, or None if there is none or it is unusable (which reads as nothing remembered).
    pub fn load(&self) -> Option<Value> {
        let raw = self.models().remove(&self.model).filter(|v| !v.is_null())?;
        validate(raw).ok()
    }

    /// Replaces this model's memory, atomically: a 0600 temporary file beside it, fsync, rename.
    pub fn save(&self, memory: Value) -> std::io::Result<()> {
        use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
        let memory = validate(memory)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, e))?;
        let mut models: Map<String, Value> = self
            .models()
            .into_iter()
            .filter(|(k, _)| MODELS.contains(&k.as_str()) && *k != self.model)
            .collect();
        models.insert(self.model.clone(), memory);
        let dir = self
            .path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)?;
        let body = serde_json::to_vec(&json!({"schema": SCHEMA, "models": models}))?;
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        for attempt in 0..100u32 {
            let tmp = dir.join(format!(
                ".window-memory.{}.{stamp:x}.{attempt}.tmp",
                std::process::id()
            ));
            let mut f = match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&tmp)
            {
                Ok(f) => f,
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            };
            let done = f
                .write_all(&body)
                .and_then(|()| f.sync_all())
                .and_then(|()| std::fs::rename(&tmp, &self.path));
            if done.is_err() {
                let _ = std::fs::remove_file(&tmp);
            }
            return done;
        }
        Err(std::io::Error::new(
            std::io::ErrorKind::AlreadyExists,
            "no free temporary name",
        ))
    }
}

/// Python's `str(v)` for the values an address can be.
fn py_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".to_owned(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.to_owned(),
        Some(other) => other.to_string(),
    }
}

fn identity(c: &Value) -> Map<String, Value> {
    let pid = c
        .get("pid")
        .filter(|v| py::int_in(Some(v), 1, 1 << 31))
        .cloned()
        .unwrap_or(json!(0));
    let mut m = Map::new();
    m.insert(
        "address".into(),
        c.get("address").cloned().unwrap_or(Value::Null),
    );
    m.insert("class".into(), json!(text(c.get("class"))));
    m.insert("title".into(), json!(text(c.get("title"))));
    m.insert("initial_title".into(), json!(text(c.get("initialTitle"))));
    m.insert("pid".into(), pid);
    m
}

fn slot_key(c: &Value) -> Value {
    let id = match c.get("workspace") {
        Some(w @ Value::Object(_)) => w.get("id").cloned().unwrap_or(json!(0)),
        _ => json!(0),
    };
    let at = c
        .get("at")
        .filter(|a| a.is_array())
        .cloned()
        .unwrap_or(json!([]));
    json!([id, at, py_str(c.get("address"))])
}

/// Which window is which: `(record, client)` pairs, each used once. Same address first (only if `same_instance`, the
/// memory was made in this Hyprland session, and only with the same class), then the PASSES unless `exact` (a parked
/// window coming home: only that very window may move).
pub fn pair_up<'a>(
    records: &[&'a Value],
    clients: &[&'a Value],
    same_instance: bool,
    exact: bool,
) -> Vec<(&'a Value, &'a Value)> {
    let mut left: Vec<(&'a Value, Map<String, Value>)> = clients
        .iter()
        .filter(|c| c.is_object())
        .map(|c| (*c, identity(c)))
        .collect();
    left.sort_by(|a, b| py::cmp(&slot_key(a.0), &slot_key(b.0)));
    let mut todo: Vec<&'a Value> = records.to_vec();
    let mut pairs = Vec::new();
    let mut take = |todo: &mut Vec<&'a Value>,
                    left: &mut Vec<(&'a Value, Map<String, Value>)>,
                    r: &'a Value,
                    c: &'a Value| {
        pairs.push((r, c));
        if let Some(i) = todo.iter().position(|x| std::ptr::eq(*x, r)) {
            todo.remove(i);
        }
        if let Some(i) = left.iter().position(|x| std::ptr::eq(x.0, c)) {
            left.remove(i);
        }
    };
    if same_instance {
        let mut by_address: HashMap<&str, (&'a Value, Value)> = HashMap::new();
        for (c, who) in &left {
            if let Some(a) = c.get("address").and_then(Value::as_str) {
                by_address.insert(a, (*c, who["class"].clone()));
            }
        }
        for r in todo.clone() {
            let Some((c, class)) = r["address"].as_str().and_then(|a| by_address.get(a)) else {
                continue;
            };
            if *class == r["class"] && left.iter().any(|x| std::ptr::eq(x.0, *c)) {
                take(&mut todo, &mut left, r, c);
            }
        }
    }
    for fields in if exact { &[][..] } else { &PASSES[..] } {
        let key =
            |m: &dyn Fn(&str) -> Value| -> Vec<Value> { fields.iter().map(|f| m(f)).collect() };
        let mut buckets: Vec<(Vec<Value>, std::collections::VecDeque<&'a Value>)> = Vec::new();
        for (c, who) in &left {
            let k = key(&|f| who[f].clone());
            if !k.iter().all(|v| py::truthy(Some(v))) {
                continue;
            }
            match buckets.iter_mut().find(|(b, _)| *b == k) {
                Some((_, q)) => q.push_back(c),
                None => buckets.push((k, [*c].into())),
            }
        }
        for r in todo.clone() {
            let k = key(&|f| r[f].clone());
            if !k.iter().all(|v| py::truthy(Some(v))) {
                continue;
            }
            if let Some(c) = buckets
                .iter_mut()
                .find(|(b, _)| *b == k)
                .and_then(|(_, q)| q.pop_front())
            {
                take(&mut todo, &mut left, r, c);
            }
        }
    }
    pairs
}

fn record_output(r: &Value) -> &str {
    r["output"].as_str().unwrap_or_default()
}

/// (output, moved): the desktop a record goes to in a run with `names`, and whether that is not its own.
fn target(record: &Value, names: &[String]) -> (String, bool) {
    let last = names[names.len() - 1].clone();
    let own = record_output(record);
    match record["index"].as_u64() {
        Some(i) if !py::is_true(record.get("parked")) => match names.get(i as usize) {
            Some(n) => (n.clone(), false),
            None => (last, true),
        },
        _ if names.iter().any(|n| n == own) => (own.to_owned(), false),
        _ => (last, true),
    }
}

fn place_key(r: &Value) -> (i64, i64, bool, i64, String) {
    let order = r["order"].as_i64();
    (
        output_number(record_output(r)),
        r["workspace"].as_i64().unwrap_or_default(),
        order.is_none(),
        order.unwrap_or(0),
        r["address"].as_str().unwrap_or_default().to_owned(),
    )
}

fn bad_state() -> Refused {
    Refused("invalid compositor state")
}

/// A floating window's (x, y, w, h) on `monitor`: as remembered, or the least move that puts it inside (fit_windows).
fn floating(record: &Value, monitor: &Value, monitors: &[Value]) -> Result<[i64; 4]> {
    let desktop = Desktop::from_parts(
        monitor.get("width"),
        monitor.get("height"),
        monitor.get("scale"),
    )?;
    let corner = |k: &str| monitor.get(k).and_then(whole).ok_or_else(bad_state);
    let name = py::text(monitor.get("name")).unwrap_or_default();
    let output = Output {
        name,
        x: corner("x")?,
        y: corner("y")?,
        desktop,
    };
    let ((dx, dy, dw, dh), (ux, uy, uw, uh)) = fit_windows::free_area(output, monitors);
    let n = |v: &Value, i: usize| v[i].as_i64().unwrap_or_default();
    let (x, y) = (dx + n(&record["at"], 0), dy + n(&record["at"], 1));
    let (w, h) = (n(&record["size"], 0), n(&record["size"], 1));
    if w <= 0 || h <= 0 {
        return Ok([x, y, 0, 0]);
    }
    if dx <= x && dy <= y && x + w <= dx + dw && y + h <= dy + dh {
        return Ok([x, y, w, h]);
    }
    let (w, h) = (w.min(uw), h.min(uh));
    Ok([ux.max(x.min(ux + uw - w)), uy.max(y.min(uy + uh - h)), w, h])
}

/// geometry.logical_width/height on a desktop's raw numbers.
fn logical(width: f64, height: f64, scale: f64) -> (i64, i64) {
    ((width / scale) as i64, (height / scale) as i64)
}

fn logical_of(d: &Value) -> Option<(i64, i64)> {
    Some(logical(
        py::number(d.get("width"))?,
        py::number(d.get("height"))?,
        py::number(d.get("scale"))?,
    ))
}

/// (w, h) a tiled window should end up: 0 for what is left to the layout. A scrolling column keeps its share of the
/// desktop's width; a fixed tile size only means something on a desktop of the same logical size.
fn tile_size(record: &Value, old: Option<&Value>, new: (i64, i64), moved: bool) -> (i64, i64) {
    let Some(old) = old.and_then(logical_of) else {
        return (0, 0);
    };
    let (w, h) = (
        record["size"][0].as_i64().unwrap_or_default(),
        record["size"][1].as_i64().unwrap_or_default(),
    );
    let same = !moved && old == new;
    if record["layout"] == "scrolling" {
        return (
            py::round(w as f64 * new.0 as f64 / old.0 as f64) as i64,
            if same { h } else { 0 },
        );
    }
    if same { (w, h) } else { (0, 0) }
}

type Group = (String, i64, bool);
/// A tiled window's row in `order`: address, column, width, height.
type Tile = (String, i64, i64, i64);

fn order_key(g: &Group) -> (bool, i64, i64) {
    (g.2, output_number(&g.0), g.1)
}

/// One remembered window paired with an open one that goes back.
#[derive(Clone, Debug, PartialEq)]
pub struct Placed {
    pub record: Value,
    pub client: Value,
    pub address: String,
    pub output: String,
    /// Its own output is gone: it goes to the last desktop.
    pub moved: bool,
    /// (output, workspace, moved): the windows that go together.
    pub group: (String, i64, bool),
}

/// What putting the windows back takes: the moves (lua::window_memory makes their evals) and the windows placed.
#[derive(Clone, Debug, Default)]
pub struct Plan {
    pub moves: Moves,
    pub placed: Vec<Placed>,
}

/// What putting the windows back takes, for the windows on output `source`: `{"whole", "windows", "layouts", "state",
/// "order", "fullscreen", "shown", "placed", "focus", "monitor"}`, lists in the order things happen; `placed` says
/// which record went where. `active`: `{"address", "monitor"}` to hand focus back to. Only windows adoption would
/// take (adopt::occupied) are candidates.
#[allow(clippy::too_many_arguments)]
pub fn plan(
    memory: &Value,
    clients: &Value,
    workspaces: &Value,
    monitors: &Value,
    names: &[&str],
    source: &str,
    instance: &str,
    active: Option<&Value>,
    exact: bool,
) -> Result<Plan> {
    let names = adopt::outputs(names)?;
    let groups = adopt::occupied(workspaces, clients, source)?;
    let memory = validate(memory.clone())?;
    let monitors = monitors.as_array().ok_or_else(bad_state)?;
    let movable: Vec<&str> = groups
        .iter()
        .flat_map(|g| g.addresses.iter().map(String::as_str))
        .collect();
    let candidates: Vec<&Value> = clients
        .as_array()
        .into_iter()
        .flatten()
        .filter(|c| {
            c.is_object() && py::text(c.get("address")).is_some_and(|a| movable.contains(&a))
        })
        .collect();
    let mut records: Vec<&Value> = memory["windows"].as_array().into_iter().flatten().collect();
    records.sort_by_key(|r| place_key(r));
    let same = !instance.is_empty() && memory["instance"] == instance;
    let mut pairs = pair_up(&records, &candidates, same, exact);
    pairs.sort_by_key(|(r, _)| place_key(r));
    let mut monitor_of: HashMap<&str, &Value> = HashMap::new();
    for m in monitors.iter().filter(|m| m.is_object()) {
        if let Some(n) = py::text(m.get("name")).filter(|n| names.iter().any(|x| x == n)) {
            monitor_of.insert(n, m);
        }
    }
    let mut placed = Vec::new();
    for (record, client) in pairs {
        let (output, moved) = target(record, &names);
        if monitor_of.contains_key(output.as_str()) {
            let group = (
                output.clone(),
                record["workspace"].as_i64().unwrap_or_default(),
                moved,
            );
            let address = py::text(client.get("address"))
                .unwrap_or_default()
                .to_owned();
            placed.push(Placed {
                record: record.clone(),
                client: client.clone(),
                address,
                output,
                moved,
                group,
            });
        }
    }
    let mut group_of: HashMap<&str, &Group> = HashMap::new();
    for p in &placed {
        group_of.insert(&p.address, &p.group);
    }
    // A workspace all of whose windows go to one remembered workspace moves whole; two for one target: the larger one.
    let mut claims: Vec<(&Group, Vec<(usize, i64)>)> = Vec::new();
    for g in &groups {
        let keys: Vec<Option<&Group>> = g
            .addresses
            .iter()
            .map(|a| group_of.get(a.as_str()).copied())
            .collect();
        let Some(Some(first)) = keys.first() else {
            continue;
        };
        if g.whole && keys.iter().all(|k| *k == Some(*first)) {
            let option = (g.addresses.len(), -g.id);
            match claims.iter_mut().find(|(k, _)| *k == *first) {
                Some((_, options)) => options.push(option),
                None => claims.push((first, vec![option])),
            }
        }
    }
    let mut chosen: Vec<(i64, &Group)> = claims
        .iter()
        .map(|(key, options)| (-options.iter().max().map_or(0, |o| o.1), *key))
        .collect();
    chosen.sort_by_key(|(_, g)| order_key(g));
    let gone: Vec<&str> = groups
        .iter()
        .filter(|g| chosen.iter().any(|(wid, _)| *wid == g.id))
        .flat_map(|g| g.addresses.iter().map(String::as_str))
        .collect();
    let mut by_group: Vec<&Placed> = placed.iter().collect();
    by_group.sort_by_key(|p| order_key(&p.group));
    let mut out = Moves {
        whole: chosen
            .iter()
            .map(|(wid, g)| (*wid, g.0.clone(), g.1))
            .collect(),
        windows: by_group
            .iter()
            .filter(|p| !gone.contains(&p.address.as_str()))
            .map(|p| {
                (
                    p.address.clone(),
                    p.group.0.clone(),
                    p.group.1,
                    i64::from(p.moved),
                )
            })
            .collect(),
        ..Moves::default()
    };
    let (mut layouts, mut state, mut fullscreen) = (Vec::new(), Vec::new(), Vec::new());
    let mut rows: Vec<(&Group, Vec<Tile>)> = Vec::new();
    let mut heads: Vec<&Group> = Vec::new();
    let mut shown: Vec<(&str, &str)> = Vec::new();
    let desktops = &memory["desktops"];
    for p in &placed {
        let (record, client) = (&p.record, &p.client);
        let monitor = monitor_of[p.output.as_str()];
        let new = logical_of(monitor).ok_or_else(bad_state)?;
        let layout = record["layout"].as_str().unwrap_or_default();
        if !layout.is_empty() && !heads.contains(&&p.group) {
            heads.push(&p.group);
            layouts.push((p.address.clone(), layout.to_owned()));
        }
        let is_floating = py::is_true(record.get("floating"));
        if is_floating {
            let [x, y, w, h] = floating(record, monitor, monitors)?;
            state.push((p.address.clone(), 1, x, y, w, h));
        } else if py::is_true(client.get("floating")) || !py::off(client.get("fullscreen")) {
            state.push((p.address.clone(), 0, 0, 0, 0, 0));
        }
        let (fs, fc) = (
            record["fullscreen"].as_i64().unwrap_or(0),
            record["client"].as_i64().unwrap_or(0),
        );
        if fs != 0 || fc != 0 {
            fullscreen.push((p.address.clone(), fs, fc));
        }
        let old = desktops.get(record_output(record));
        if !record["order"].is_null() && !is_floating {
            let (w, h) = tile_size(record, old, new, p.moved);
            let row = (
                p.address.clone(),
                record["column"].as_i64().unwrap_or(0),
                w,
                h,
            );
            match rows.iter_mut().find(|(g, _)| **g == p.group) {
                Some((_, r)) => r.push(row),
                None => rows.push((&p.group, vec![row])),
            }
        }
        if !p.moved
            && old.is_some_and(|o| py::eq(o.get("active"), record.get("workspace")))
            && !shown.iter().any(|(o, _)| *o == p.output)
        {
            shown.push((&p.output, &p.address));
        }
    }
    rows.sort_by_key(|(g, _)| order_key(g));
    shown.sort_by_key(|(o, _)| output_number(o));
    out.layouts = layouts;
    out.state = state;
    out.order = rows.into_iter().map(|(_, r)| r).collect();
    out.fullscreen = fullscreen;
    out.shown = shown
        .iter()
        .map(|(o, a)| ((*o).to_owned(), (*a).to_owned()))
        .collect();
    if let Some(active) = active.filter(|a| a.is_object()) {
        out.focus = py::text(active.get("address"))
            .filter(|a| py::is_address(a))
            .map(str::to_owned);
        out.monitor = py::text(active.get("monitor"))
            .filter(|m| py::is_token(m))
            .map(str::to_owned);
    }
    Ok(Plan { moves: out, placed })
}

/// Nothing to move.
pub fn empty(moves: &Moves) -> bool {
    moves.whole.is_empty() && moves.windows.is_empty()
}
