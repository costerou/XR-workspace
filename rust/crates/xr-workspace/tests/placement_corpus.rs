//! The placement decisions, exactly what the Python makes: every rust/corpus/placement/plans.json case (adopt,
//! window_memory, desktops, fit_windows, send, resize, geometry), replayed on the Rust port and compared as JSON,
//! refusals included (the Python's `ValueError: <message>`). Then the gate on its own: pinned windows, windows on
//! special workspaces and the panel are never adopted, remembered or moved back.

use serde_json::{Value, json};
use xr_workspace::desktops::{self, Action, Spec};
use xr_workspace::fit_windows::{self, Output};
use xr_workspace::geometry::{self, Desktop, Placement};
use xr_workspace::lua::{self, Refused};
use xr_workspace::{adopt, resize, send, window_memory};

fn corpus() -> Vec<Value> {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/placement/plans.json"
    );
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(data["schema"], 1);
    data["cases"].as_array().unwrap().clone()
}

fn case<'a>(cases: &'a [Value], name: &str) -> &'a Value {
    cases
        .iter()
        .find(|c| c["name"] == name)
        .unwrap_or_else(|| panic!("no case {name}"))
}

fn refused(e: Refused) -> String {
    format!("ValueError: {e}")
}

fn strs(v: &Value) -> Vec<&str> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap())
        .collect()
}

fn desktop(v: &Value) -> Result<Desktop, String> {
    Desktop::from_value(v).map_err(refused)
}

fn output(spec: &Value) -> Output<'_> {
    let desktop =
        Desktop::from_parts(spec.get("width"), spec.get("height"), spec.get("scale")).unwrap();
    Output {
        name: spec["name"].as_str().unwrap(),
        x: spec["x"].as_i64().unwrap(),
        y: spec["y"].as_i64().unwrap(),
        desktop,
    }
}

fn occupied(r: Vec<adopt::Occupied>) -> Value {
    r.into_iter()
        .map(|g| json!([g.id, g.whole, g.addresses]))
        .collect()
}

fn plan_value(p: desktops::Plan) -> Value {
    let specs = |s: &[Spec]| s.iter().map(Spec::to_value).collect::<Vec<_>>();
    json!({
        "action": p.action.name(), "specs": specs(&p.specs), "remove": p.remove.as_ref().map(Spec::to_value),
        "reshape": specs(&p.reshape), "create": specs(&p.create),
        "placements": p.placements.iter().map(|q| q.to_value()).collect::<Vec<_>>(), "focus": p.focus,
        "resized": p.resized, "windows_to": p.windows_to, "shrunk": p.shrunk,
    })
}

fn desktops_plan(a: &[Value]) -> Result<Value, String> {
    let specs: Vec<Spec> = a[0]
        .as_array()
        .unwrap()
        .iter()
        .map(Spec::from_value)
        .collect::<Result<_, _>>()
        .map_err(refused)?;
    let placements: Vec<Placement> = a[1]
        .as_array()
        .unwrap()
        .iter()
        .map(Placement::from_value)
        .collect::<Result<_, _>>()
        .map_err(refused)?;
    let action = match a[2].as_str().unwrap() {
        "ultrawide" => Action::Ultrawide,
        "add" => Action::Add,
        "remove" => Action::Remove,
        "scale" => Action::Scale(a[6].as_f64().unwrap()),
        "resolution" => Action::Resolution(a[7][0].as_i64().unwrap(), a[7][1].as_i64().unwrap()),
        "reset" => Action::Reset,
        other => panic!("action {other}"),
    };
    // A negative index is past every desktop for the typed API, as for the Python.
    let index = usize::try_from(a[3].as_i64().unwrap()).unwrap_or(usize::MAX);
    let origin = a[4]
        .as_array()
        .map(|o| (o[0].as_i64().unwrap(), o[1].as_i64().unwrap()));
    let held = strs(&a[5]);
    let mut n = 0;
    let mut new_id = || {
        n += 1;
        format!("00000000-0000-4000-8000-{n:012}")
    };
    desktops::plan(
        &specs,
        &placements,
        action,
        index,
        origin,
        &held,
        &mut new_id,
    )
    .map(plan_value)
    .map_err(refused)
}

fn adopt_value(m: &adopt::Moves) -> Value {
    json!({"workspaces": m.workspaces.iter().map(|(i, o)| json!([i, o])).collect::<Vec<_>>(),
           "windows": m.windows.iter().map(|(a, o)| json!([a, o])).collect::<Vec<_>>()})
}

fn fit_value(moves: Vec<fit_windows::Move>) -> Value {
    moves
        .into_iter()
        .map(|m| json!({"address": m.address, "x": m.x, "y": m.y, "w": m.w, "h": m.h}))
        .collect()
}

fn memory_plan_value(p: window_memory::Plan) -> Value {
    let m = &p.moves;
    json!({
        "whole": m.whole.iter().map(|(i, o, w)| json!([i, o, w])).collect::<Vec<_>>(),
        "windows": m.windows.iter().map(|(a, o, w, moved)| json!([a, o, w, moved])).collect::<Vec<_>>(),
        "layouts": m.layouts.iter().map(|(a, l)| json!([a, l])).collect::<Vec<_>>(),
        "state": m.state.iter().map(|(a, f, x, y, w, h)| json!([a, f, x, y, w, h])).collect::<Vec<_>>(),
        "order": m.order.iter().map(|g| g.iter().map(|(a, c, w, h)| json!([a, c, w, h])).collect::<Vec<_>>()).collect::<Vec<_>>(),
        "fullscreen": m.fullscreen.iter().map(|(a, s, c)| json!([a, s, c])).collect::<Vec<_>>(),
        "shown": m.shown.iter().map(|(o, a)| json!([o, a])).collect::<Vec<_>>(),
        "placed": p.placed.iter().map(|p| json!({
            "record": p.record, "client": p.client, "address": p.address, "output": p.output, "moved": p.moved,
            "group": [p.group.0, p.group.1, p.group.2],
        })).collect::<Vec<_>>(),
        "focus": m.focus, "monitor": m.monitor,
    })
}

/// empty() looks only at the moves' whole workspaces and windows.
fn moves_of(v: &Value) -> window_memory::Plan {
    let mut p = window_memory::Plan::default();
    for w in v["whole"].as_array().unwrap() {
        p.moves.whole.push((
            w[0].as_i64().unwrap(),
            w[1].as_str().unwrap().to_owned(),
            w[2].as_i64().unwrap(),
        ));
    }
    for w in v["windows"].as_array().unwrap() {
        p.moves.windows.push((
            w[0].as_str().unwrap().to_owned(),
            w[1].as_str().unwrap().to_owned(),
            w[2].as_i64().unwrap(),
            w[3].as_i64().unwrap(),
        ));
    }
    p
}

fn run(function: &str, a: &[Value]) -> Result<Value, String> {
    let ok = |v: Value| Ok(v);
    match function {
        "adopt.stays" => ok(adopt::stays(&a[0]).into()),
        "adopt.occupied" => adopt::occupied(&a[0], &a[1], a[2].as_str().unwrap()).map(occupied).map_err(refused),
        "adopt.plan" => adopt::plan(&a[0], &a[1], a[2].as_str().unwrap(), &strs(&a[3])).map(|m| adopt_value(&m)).map_err(refused),
        "window_memory.capture" => {
            window_memory::capture(&a[0], &a[1], &a[2], &strs(&a[3]), a[4].as_str().unwrap(), Some(&a[5]).filter(|c| !c.is_null()))
                .map_err(refused)
        }
        "window_memory.validate" => window_memory::validate(a[0].clone()).map_err(refused),
        "window_memory.match" => {
            let (records, clients): (Vec<&Value>, Vec<&Value>) =
                (a[0].as_array().unwrap().iter().collect(), a[1].as_array().unwrap().iter().collect());
            let pairs = window_memory::pair_up(&records, &clients, a[2].as_bool().unwrap(), a[3].as_bool().unwrap());
            ok(pairs.into_iter().map(|(r, c)| json!([r, c])).collect())
        }
        "window_memory.plan" => window_memory::plan(
            &a[0],
            &a[1],
            &a[2],
            &a[3],
            &strs(&a[4]),
            a[5].as_str().unwrap(),
            a[6].as_str().unwrap(),
            Some(&a[7]).filter(|v| !v.is_null()),
            a[8].as_bool().unwrap(),
        )
        .map(memory_plan_value)
        .map_err(refused),
        "window_memory.empty" => ok(window_memory::empty(&moves_of(&a[0]).moves).into()),
        "desktops.plan" => desktops_plan(a),
        "fit_windows.free_area" => {
            let (whole, free) = fit_windows::free_area(output(&a[0]), a[1].as_array().unwrap());
            ok(json!([[whole.0, whole.1, whole.2, whole.3], [free.0, free.1, free.2, free.3]]))
        }
        "fit_windows.plan" => fit_windows::plan(&a[0], &a[1], &a[2], output(&a[3])).map(fit_value).map_err(refused),
        "send.landing" => send::landing(&a[0], &a[1]).map(|(x, y)| json!([x, y])).map_err(refused),
        "send.window_at" => {
            send::window_at(&a[0], &a[1], a[2].as_str().unwrap(), a[3].as_f64().unwrap(), a[4].as_f64().unwrap())
                .map(|w| w.unwrap_or(Value::Null))
                .map_err(refused)
        }
        // The Python takes the run's names as well and does not use them.
        "send.target_for" => send::target_for(a[0].as_str().unwrap(), a[2].as_str(), a[3].as_str()).map(Value::from).map_err(refused),
        "send.move_command" => {
            send::move_command(a[0].as_str().unwrap(), a[1].as_i64().unwrap(), a[2].as_bool().unwrap()).map(Value::from).map_err(refused)
        }
        "resize.outputs_from_monitors" => ok(resize::outputs_from_monitors(&a[0], &strs(&a[1]))
            .into_iter()
            .map(|o| json!({"index": o.index, "x": o.x, "y": o.y, "width": o.width, "height": o.height}))
            .collect()),
        "geometry.toggle_ultrawide" => desktop(&a[0]).map(|d| d.toggle_ultrawide().to_value()),
        "geometry.with_scale" => desktop(&a[0])?.with_scale(a[1].as_f64().unwrap()).map(Desktop::to_value).map_err(refused),
        "geometry.with_resolution" => desktop(&a[0])?
            .with_resolution(a[1].as_i64().unwrap(), a[2].as_i64().unwrap())
            .map(Desktop::to_value)
            .map_err(refused),
        "geometry.valid_scales" => ok(geometry::valid_scales(a[0].as_i64().unwrap(), a[1].as_i64().unwrap()).into()),
        "geometry.nearest_scale" => {
            ok(geometry::nearest_scale(a[0].as_i64().unwrap(), a[1].as_i64().unwrap(), a[2].as_f64().unwrap()).into())
        }
        "geometry.validate_desktop" => desktop(&a[0]).map(Desktop::to_value),
        "geometry.validate_placement" => Placement::from_value(&a[0]).map(Placement::to_value).map_err(refused),
        other => panic!("no Rust for {other}"),
    }
}

#[test]
fn every_placement_case_matches_the_python() {
    let cases = corpus();
    for c in &cases {
        let (name, function) = (c["name"].as_str().unwrap(), c["function"].as_str().unwrap());
        let want = match c["refused"].as_str() {
            Some(r) => Err(r.to_owned()),
            None => Ok(c["result"].clone()),
        };
        let got = run(function, c["args"].as_array().unwrap());
        assert_eq!(got, want, "{function} {name}");
    }
    assert!(cases.len() >= 120, "{} cases", cases.len());
}

/// Every address in a JSON value (the windows a plan moves, wherever it names them).
fn addresses(v: &Value, out: &mut Vec<String>) {
    match v {
        Value::String(s) if s.starts_with("0x") => out.push(s.clone()),
        Value::Array(a) => a.iter().for_each(|x| addresses(x, out)),
        Value::Object(o) => o.values().for_each(|x| addresses(x, out)),
        _ => {}
    }
}

/// The fixture's windows that must stay where they are: pinned (or no `pinned` key), on a special workspace, the panel.
fn staying(clients: &Value, workspaces: &Value) -> Vec<String> {
    let special: Vec<&Value> = workspaces
        .as_array()
        .unwrap()
        .iter()
        .filter(|w| w["name"].as_str().unwrap().starts_with("special"))
        .map(|w| &w["id"])
        .collect();
    let out: Vec<String> = clients
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| adopt::stays(c) || special.contains(&&c["workspace"]["id"]))
        .map(|c| c["address"].as_str().unwrap().to_owned())
        .collect();
    out
}

#[test]
fn pinned_special_and_panel_windows_are_never_adopted() {
    let cases = corpus();
    let plan = case(&cases, "plan_1_desktops");
    let (workspaces, clients) = (&plan["args"][0], &plan["args"][1]);
    let stay = staying(clients, workspaces);
    // The fixture has each kind: pinned, no pinned key, both panel titles, a special workspace's window.
    let kinds = clients.as_array().unwrap();
    assert!(kinds.iter().any(|c| c["pinned"] == true));
    assert!(kinds.iter().any(|c| c.get("pinned").is_none()));
    assert!(
        kinds
            .iter()
            .any(|c| c["class"] == "org.quickshell" && c["title"] == "XR Workspace")
    );
    assert!(
        kinds
            .iter()
            .any(|c| c["class"] == "org.quickshell" && c["title"] == "VITURE Workspace")
    );
    assert!(kinds.iter().any(|c| {
        c["workspace"]["name"]
            .as_str()
            .unwrap()
            .starts_with("special")
    }));
    assert_eq!(stay.len(), 5, "{stay:?}");
    for names in [
        vec!["XR-1"],
        vec!["XR-1", "XR-2"],
        vec!["XR-1", "XR-2", "XR-3", "XR-4"],
    ] {
        let plan = adopt::plan(workspaces, clients, "DP-1", &names).unwrap();
        let moves = adopt_value(&plan);
        let mut moved = Vec::new();
        addresses(&moves["windows"], &mut moved);
        // A whole workspace moves only when nothing on it stays.
        for w in moves["workspaces"].as_array().unwrap() {
            let on = kinds.iter().filter(|c| c["workspace"]["id"] == w[0]);
            on.for_each(|c| moved.push(c["address"].as_str().unwrap().to_owned()));
        }
        assert!(!moved.is_empty());
        for a in &stay {
            assert!(!moved.contains(a), "{a} adopted onto {names:?}");
        }
        for evals in plan.lua().unwrap() {
            assert!(stay.iter().all(|a| !evals.contains(a.as_str())), "{evals}");
        }
    }
    // A workspace whose only window stays is not taken at all, and neither is a special workspace.
    let taken: Vec<i64> = adopt::occupied(workspaces, clients, "DP-1")
        .unwrap()
        .iter()
        .map(|g| g.id)
        .collect();
    assert_eq!(taken, [1, 2, 4, 6, 8]);
}

#[test]
fn pinned_special_and_panel_windows_are_never_remembered_or_moved_back() {
    let cases = corpus();
    let capture = case(&cases, "capture_live");
    let (clients, workspaces) = (&capture["args"][0], &capture["args"][1]);
    let stay = staying(clients, workspaces);
    assert_eq!(stay.len(), 5, "{stay:?}");
    let memory = run("window_memory.capture", capture["args"].as_array().unwrap()).unwrap();
    let mut remembered = Vec::new();
    addresses(&memory["windows"], &mut remembered);
    assert!(!remembered.is_empty());
    for a in &stay {
        assert!(!remembered.contains(a), "{a} remembered");
    }

    // Put back after a restart, the fallback's staying windows are never candidates, however well they match a record.
    let back = case(&cases, "plan_whole_workspace");
    let (clients, workspaces) = (&back["args"][1], &back["args"][2]);
    let stay = staying(clients, workspaces);
    let a = back["args"].as_array().unwrap();
    let plan = window_memory::plan(
        &a[0],
        &a[1],
        &a[2],
        &a[3],
        &strs(&a[4]),
        a[5].as_str().unwrap(),
        a[6].as_str().unwrap(),
        Some(&a[7]).filter(|v| !v.is_null()),
        a[8].as_bool().unwrap(),
    )
    .unwrap();
    let placement = memory_plan_value(plan.clone());
    let mut moved = Vec::new();
    for key in [
        "windows",
        "layouts",
        "state",
        "order",
        "fullscreen",
        "shown",
    ] {
        addresses(&placement[key], &mut moved);
    }
    for w in placement["whole"].as_array().unwrap() {
        let on = clients
            .as_array()
            .unwrap()
            .iter()
            .filter(|c| c["workspace"]["id"] == w[0]);
        on.for_each(|c| moved.push(c["address"].as_str().unwrap().to_owned()));
    }
    assert!(!placement["whole"].as_array().unwrap().is_empty());
    for p in placement["placed"].as_array().unwrap() {
        moved.push(p["address"].as_str().unwrap().to_owned());
    }
    assert!(!moved.is_empty());
    for a in &stay {
        assert!(!moved.contains(a), "{a} moved back");
    }
    for evals in [
        lua::window_memory::moves_lua(&plan.moves).unwrap(),
        lua::window_memory::settle_lua(&plan.moves).unwrap(),
    ] {
        for e in evals {
            assert!(stay.iter().all(|a| !e.contains(a.as_str())), "{e}");
        }
    }
}
