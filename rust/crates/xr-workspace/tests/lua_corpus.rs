//! The gate for the Lua generators: every case in rust/corpus/hyprland/lua.json (dumped from the Python generators by
//! tests/native_spike/lua_corpus.py) gives byte-identical Lua, or the same refusal.
//!
//! A refusal Python makes from a value of the wrong JSON type (a float or bool for an int, a number for a string, a
//! missing or extra key) cannot reach the typed Rust API: those cases fail here at decode instead, and must be listed
//! in TYPE_REFUSALS. Every other refusal must carry Python's message exactly.

use xr_workspace::lua::{
    self, BindCommand, Refused, adopt, columns, fit_windows, free_pointer, input_lock, keybinds,
    overview_cursor, privacy, resize, restore, sbs, send, unified_cursor, video, view,
    window_memory,
};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/hyprland/lua.json"
));

/// Refused by Python for a JSON type the Rust signature cannot express.
const TYPE_REFUSALS: &[&str] = &[
    "adopt.refuse_shape",
    "columns.scroll.refuse_type",
    "fit_windows.refuse_list",
    "free_pointer.grab_pointer_lua.refuse_unsafe_path_type",
    "free_pointer.grab_pointer_lua.refuse_unsafe_token_type",
    "free_pointer.grab_pointer_lua.refuse_position",
    "input_lock.lock_binds_lua.refuse_unsafe_path_type",
    "input_lock.lock_binds_lua.refuse_unsafe_token_type",
    "keybinds.panel_on_lua.refuse_unsafe_path_type",
    "keybinds.panel_on_lua.refuse_unsafe_token_type",
    "resize.install_resize_binds_lua.refuse_unsafe_path_type",
    "resize.install_resize_binds_lua.refuse_unsafe_token_type",
    "sbs.wide.refuse_rate_type",
    "sbs.wide.refuse_x",
    "sbs.wide.refuse_y",
    "sbs.wide.refuse_scale_type",
    "send.land.refuse_address_type",
    "send.land.refuse_workspace_type",
    "send.land.refuse_position_type",
    "unified_cursor.click_binds_lua.refuse_unsafe_path_type",
    "unified_cursor.click_binds_lua.refuse_unsafe_token_type",
    "unified_cursor.enter_desktop.refuse_monitor_type",
    "unified_cursor.confine.refuse_name_type",
    "video.install_video_binds_lua.refuse_unsafe_path_type",
    "video.install_video_binds_lua.refuse_unsafe_token_type",
    "view.warp_cursor.refuse_x_type",
    "view.install_binds_lua.refuse_unsafe_path_type",
    "view.install_binds_lua.refuse_unsafe_token_type",
    "view.install_binds_lua.refuse_chord_type",
    "view.install_send_binds_lua.refuse_unsafe_path_type",
    "view.install_send_binds_lua.refuse_unsafe_token_type",
    "view.install_drag_binds_lua.refuse_unsafe_path_type",
    "view.install_drag_binds_lua.refuse_unsafe_token_type",
    "view.install_menu_binds_lua.refuse_unsafe_path_type",
    "view.install_menu_binds_lua.refuse_unsafe_token_type",
    "view.install_options_binds_lua.refuse_unsafe_path_type",
    "view.install_options_binds_lua.refuse_unsafe_token_type",
    "window_memory.moves.refuse_shape",
];

/// The corpus as read: ints kept apart from floats, as Python's `type(v) is int` checks need.
#[derive(Clone, Debug, PartialEq)]
enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

fn convert(v: serde_json::Value) -> Json {
    use serde_json::Value;
    match v {
        Value::Null => Json::Null,
        Value::Bool(b) => Json::Bool(b),
        Value::Number(n) => n
            .as_i64()
            .map_or_else(|| Json::Float(n.as_f64().expect("number")), Json::Int),
        Value::String(s) => Json::Str(s),
        Value::Array(a) => Json::Arr(a.into_iter().map(convert).collect()),
        Value::Object(o) => Json::Obj(o.into_iter().map(|(k, v)| (k, convert(v))).collect()),
    }
}

fn parse(text: &str) -> Json {
    convert(serde_json::from_str(text).expect("corpus JSON"))
}

impl Json {
    fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(o) => o.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    fn keys(&self) -> Option<Vec<&str>> {
        match self {
            Json::Obj(o) => Some(o.iter().map(|(k, _)| k.as_str()).collect()),
            _ => None,
        }
    }

    fn int(&self) -> Option<i64> {
        if let Json::Int(i) = self {
            Some(*i)
        } else {
            None
        }
    }

    fn num(&self) -> Option<f64> {
        match self {
            Json::Int(i) => Some(*i as f64),
            Json::Float(f) => Some(*f),
            _ => None,
        }
    }

    fn str(&self) -> Option<&str> {
        if let Json::Str(s) = self {
            Some(s)
        } else {
            None
        }
    }

    fn bool(&self) -> Option<bool> {
        if let Json::Bool(b) = self {
            Some(*b)
        } else {
            None
        }
    }

    fn arr(&self) -> Option<&[Json]> {
        if let Json::Arr(a) = self {
            Some(a)
        } else {
            None
        }
    }

    /// An n-element array.
    fn tuple(&self, n: usize) -> Option<&[Json]> {
        self.arr().filter(|a| a.len() == n)
    }
}

/// Exactly these keys (Python compares the key set).
fn shape(v: &Json, keys: &[&str]) -> Option<()> {
    let mut have = v.keys()?;
    let mut want = keys.to_vec();
    have.sort();
    want.sort();
    (have == want).then_some(())
}

/// A key that is absent or null: None; present: decoded, or the case fails at decode.
fn opt<'a, T>(args: &'a Json, key: &str, f: impl Fn(&'a Json) -> Option<T>) -> Option<Option<T>> {
    match args.get(key) {
        None | Some(Json::Null) => Some(None),
        Some(v) => f(v).map(Some),
    }
}

fn int(args: &Json, key: &str) -> Option<i64> {
    args.get(key)?.int()
}

fn string<'a>(args: &'a Json, key: &str) -> Option<&'a str> {
    args.get(key)?.str()
}

fn cmd(args: &Json) -> Option<BindCommand<'_>> {
    Some(BindCommand {
        python: string(args, "python")?,
        client: string(args, "client")?,
        control_socket: string(args, "control_socket")?,
        run_id: string(args, "run_id")?,
        session: string(args, "session")?,
    })
}

fn pairs(v: &Json) -> Option<Vec<(&str, &str)>> {
    v.arr()?
        .iter()
        .map(|p| Some((p.tuple(2)?[0].str()?, p.tuple(2)?[1].str()?)))
        .collect()
}

enum Out {
    One(String),
    Many(Vec<String>),
}

type Outcome = Result<Out, Refused>;

fn one(r: lua::Result<String>) -> Outcome {
    r.map(Out::One)
}

fn many(r: lua::Result<Vec<String>>) -> Outcome {
    r.map(Out::Many)
}

fn snapshot(v: &Json) -> Option<restore::Snapshot> {
    shape(
        v,
        &[
            "schema",
            "run_id",
            "monitor",
            "active",
            "focus",
            "focus_monitor",
            "workspaces",
            "windows",
        ],
    )?;
    let pair = |v: &Json| Some((v.tuple(2)?[0].int()?, v.tuple(2)?[1].int()?));
    Some(restore::Snapshot {
        schema: v.get("schema")?.int()?,
        run_id: v.get("run_id")?.str()?.to_string(),
        monitor: v.get("monitor")?.str()?.to_string(),
        active: opt(v, "active", Json::int)?,
        focus: opt(v, "focus", Json::str)?.map(str::to_string),
        focus_monitor: opt(v, "focus_monitor", Json::str)?.map(str::to_string),
        workspaces: v
            .get("workspaces")?
            .arr()?
            .iter()
            .map(|s| {
                shape(s, &["id", "whole"])?;
                Some((s.get("id")?.int()?, s.get("whole")?.bool()?))
            })
            .collect::<Option<_>>()?,
        windows: v
            .get("windows")?
            .arr()?
            .iter()
            .map(|w| {
                shape(
                    w,
                    &[
                        "address",
                        "workspace",
                        "floating",
                        "at",
                        "size",
                        "fullscreen",
                        "client",
                    ],
                )?;
                Some(restore::Window {
                    address: w.get("address")?.str()?.to_string(),
                    workspace: w.get("workspace")?.int()?,
                    floating: w.get("floating")?.bool()?,
                    at: pair(w.get("at")?)?,
                    size: pair(w.get("size")?)?,
                    fullscreen: w.get("fullscreen")?.int()?,
                    client: w.get("client")?.int()?,
                })
            })
            .collect::<Option<_>>()?,
    })
}

/// A table of n-element rows, each decoded by `f`.
fn table<'a, T>(v: &'a Json, n: usize, f: impl Fn(&'a [Json]) -> Option<T>) -> Option<Vec<T>> {
    v.arr()?.iter().map(|r| f(r.tuple(n)?)).collect()
}

fn placement(v: &Json) -> Option<window_memory::Placement> {
    shape(
        v,
        &[
            "whole",
            "windows",
            "layouts",
            "state",
            "order",
            "fullscreen",
            "shown",
            "placed",
            "focus",
            "monitor",
        ],
    )?;
    let s = |j: &Json| j.str().map(str::to_string);
    let i = Json::int;
    Some(window_memory::Placement {
        whole: table(v.get("whole")?, 3, |r| {
            Some((i(&r[0])?, s(&r[1])?, i(&r[2])?))
        })?,
        windows: table(v.get("windows")?, 4, |r| {
            Some((s(&r[0])?, s(&r[1])?, i(&r[2])?, i(&r[3])?))
        })?,
        layouts: table(v.get("layouts")?, 2, |r| Some((s(&r[0])?, s(&r[1])?)))?,
        state: table(v.get("state")?, 6, |r| {
            Some((
                s(&r[0])?,
                i(&r[1])?,
                i(&r[2])?,
                i(&r[3])?,
                i(&r[4])?,
                i(&r[5])?,
            ))
        })?,
        order: v
            .get("order")?
            .arr()?
            .iter()
            .map(|o| table(o, 4, |r| Some((s(&r[0])?, i(&r[1])?, i(&r[2])?, i(&r[3])?))))
            .collect::<Option<_>>()?,
        fullscreen: table(v.get("fullscreen")?, 3, |r| {
            Some((s(&r[0])?, i(&r[1])?, i(&r[2])?))
        })?,
        shown: table(v.get("shown")?, 2, |r| Some((s(&r[0])?, s(&r[1])?)))?,
        focus: opt(v, "focus", s)?,
        monitor: opt(v, "monitor", s)?,
    })
}

fn constant(s: &str) -> Outcome {
    Ok(Out::One(s.to_string()))
}

/// The case's generator run on its args; None when the args cannot be decoded into the typed API.
fn run(generator: &str, a: &Json) -> Option<Outcome> {
    Some(match generator {
        "adopt.lua" => {
            let m = a.get("moves")?;
            shape(m, &["workspaces", "windows"])?;
            let ws = table(m.get("workspaces")?, 2, |r| {
                Some((r[0].int()?, r[1].str()?))
            })?;
            let wins = table(m.get("windows")?, 2, |r| Some((r[0].str()?, r[1].str()?)))?;
            many(adopt::lua(&ws, &wins))
        }
        "columns.scroll_lua" => one(columns::scroll_lua(string(a, "address")?)),
        "fit_windows.lua" => {
            let moves = a
                .get("moves")?
                .arr()?
                .iter()
                .map(|m| {
                    shape(m, &["address", "x", "y", "w", "h"])?;
                    Some(fit_windows::Move {
                        address: string(m, "address")?,
                        x: int(m, "x")?,
                        y: int(m, "y")?,
                        w: int(m, "w")?,
                        h: int(m, "h")?,
                    })
                })
                .collect::<Option<Vec<_>>>()?;
            many(fit_windows::lua(&moves))
        }
        "free_pointer.grab_pointer_lua" => one(free_pointer::grab_pointer_lua(
            &cmd(a)?,
            int(a, "x")?,
            int(a, "y")?,
        )),
        "free_pointer.release_pointer_lua" => one(free_pointer::release_pointer_lua(
            opt(a, "x", Json::int)?,
            opt(a, "y", Json::int)?,
        )),
        "input_lock.lock_binds_lua" => one(input_lock::lock_binds_lua(&cmd(a)?)),
        "input_lock.lock_pointer_lua" => one(input_lock::lock_pointer_lua(
            &cmd(a)?,
            int(a, "x")?,
            int(a, "y")?,
        )),
        "keybinds.panel_on_lua" => one(keybinds::panel_on_lua(&cmd(a)?)),
        "privacy.blank_lua" => one(privacy::blank_lua(
            string(a, "target")?,
            string(a, "glasses")?,
        )),
        "privacy.wake_lua" => one(privacy::wake_lua(string(a, "target")?)),
        "resize.install_resize_binds_lua" => one(resize::install_resize_binds_lua(&cmd(a)?)),
        "resize.remove_resize_binds_lua" => constant(&resize::remove_resize_binds_lua()),
        "restore.lua" => one(restore::lua(&snapshot(a.get("snapshot")?)?)),
        "sbs.wide_mode_lua" => one(sbs::wide_mode_lua(
            string(a, "name")?,
            int(a, "x")?,
            int(a, "y")?,
            a.get("scale")?.num()?,
            a.get("rate")?.num()?,
            opt(a, "model", Json::str)?.unwrap_or("luma-ultra"),
        )),
        "sbs.pro2_mode_lua" => one(sbs::pro2_mode_lua(
            string(a, "name")?,
            int(a, "x")?,
            int(a, "y")?,
            a.get("scale")?.num()?,
            a.get("rate")?.num()?,
        )),
        "send.land_lua" => one(send::land_lua(
            string(a, "address")?,
            int(a, "workspace")?,
            opt(a, "x", Json::int)?,
            opt(a, "y", Json::int)?,
        )),
        "unified_cursor.click_binds_lua" => one(unified_cursor::click_binds_lua(&cmd(a)?)),
        "unified_cursor.enter_desktop_lua" => one(unified_cursor::enter_desktop_lua(
            &cmd(a)?,
            opt(a, "x", Json::int)?,
            opt(a, "y", Json::int)?,
            opt(a, "monitor", Json::str)?,
        )),
        "unified_cursor.confine_lua" => one(unified_cursor::confine_lua(
            int(a, "x")?,
            int(a, "y")?,
            string(a, "name")?,
        )),
        "video.install_video_binds_lua" => one(video::install_video_binds_lua(&cmd(a)?)),
        "video.remove_video_binds_lua" => constant(&video::remove_video_binds_lua()),
        "view.warp_cursor_lua" => one(view::warp_cursor_lua(int(a, "x")?, int(a, "y")?)),
        "view.restore_mode_lua" => {
            let m = a.get("mode")?;
            shape(m, &["name", "x", "y", "scale"])?;
            let mode = view::SessionMode {
                name: string(m, "name")?,
                x: int(m, "x")?,
                y: int(m, "y")?,
                scale: m.get("scale")?.num()?,
            };
            one(view::restore_mode_lua(&mode))
        }
        "view.install_binds_lua" => {
            let binds = opt(a, "binds", pairs)?;
            let typing = opt(a, "typing", Json::bool)?.unwrap_or(false);
            one(view::install_binds_lua(&cmd(a)?, typing, binds.as_deref()))
        }
        "view._binds_lua" => constant(&view::binds_lua(
            string(a, "table")?,
            &pairs(a.get("binds")?)?,
            string(a, "base")?,
        )),
        "view.remove_binds_lua" => constant(&view::remove_binds_lua()),
        "view.install_send_binds_lua" => {
            let binds = opt(a, "binds", pairs)?;
            one(view::install_send_binds_lua(&cmd(a)?, binds.as_deref()))
        }
        "view.remove_send_binds_lua" => constant(&view::remove_send_binds_lua()),
        "view.install_drag_binds_lua" => one(view::install_drag_binds_lua(&cmd(a)?)),
        "view.remove_drag_binds_lua" => constant(&view::remove_drag_binds_lua()),
        "view.install_menu_binds_lua" => one(view::install_menu_binds_lua(&cmd(a)?)),
        "view.install_options_binds_lua" => one(view::install_options_binds_lua(&cmd(a)?)),
        "view.remove_menu_binds_lua" => constant(&view::remove_menu_binds_lua()),
        "window_memory.moves_lua" => {
            many(window_memory::moves_lua(&placement(a.get("placement")?)?))
        }
        "window_memory.settle_lua" => {
            many(window_memory::settle_lua(&placement(a.get("placement")?)?))
        }
        "window_memory.verify_lua" => Ok(Out::Many(window_memory::verify_lua())),
        "const:keybinds.PANEL_OFF_LUA" | "const:xr_control.LEAVE_KEY_PANEL" => {
            constant(keybinds::PANEL_OFF_LUA)
        }
        "const:privacy.WAKE_ALL_LUA" | "const:xr_control.WAKE_ALL_DISPLAYS" => {
            constant(privacy::WAKE_ALL_LUA)
        }
        "const:restore.UNPIN_LUA" => constant(restore::UNPIN_LUA),
        "const:restore._LUA" => constant(restore::LUA),
        "const:resize.GRAB_LEAVE_LUA" => constant(resize::GRAB_LEAVE_LUA),
        "const:overview_cursor.HIDE_LUA" => constant(overview_cursor::HIDE_LUA),
        "const:overview_cursor.SHOW_LUA" | "const:xr_control.SHOW_OVERVIEW_CURSOR" => {
            constant(overview_cursor::SHOW_LUA)
        }
        "const:input_lock._PIN_HOME_LUA" => constant(input_lock::PIN_HOME_LUA),
        "const:free_pointer._HIDE_LUA" => constant(free_pointer::HIDE_LUA),
        "const:free_pointer._SHOW_LUA" => constant(free_pointer::SHOW_LUA),
        "const:view._LEGACY_MOUSE_CLEANUP_LUA" => constant(view::LEGACY_MOUSE_CLEANUP_LUA),
        "const:fit_windows._LUA" => constant(fit_windows::LUA),
        // xr_control.py keeps copies because the bind helper runs with -I and imports nothing; Rust shares the source.
        "const:xr_control.REMOVE_VIDEO_BINDS" => constant(&video::remove_video_binds_lua()),
        "const:xr_control.REMOVE_MENU_BINDS" => constant(&view::remove_menu_binds_lua()),
        "const:xr_control.REMOVE_RESIZE_BINDS" => constant(&resize::remove_resize_binds_lua()),
        "const:xr_control.REMOVE_DRAG_BINDS" => constant(&view::remove_drag_binds_lua()),
        other => panic!("corpus generator with no Rust counterpart: {other}"),
    })
}

#[test]
fn byte_equal_to_the_generated_lua_corpus() {
    let corpus = parse(CORPUS);
    assert_eq!(corpus.get("schema"), Some(&Json::Int(1)));
    let cases = corpus.get("cases").and_then(Json::arr).expect("cases");
    let (mut failures, mut decoded_refusals, mut ran) = (Vec::new(), Vec::new(), 0);
    for case in cases {
        let name = case.get("name").and_then(Json::str).expect("name");
        let generator = case
            .get("generator")
            .and_then(Json::str)
            .expect("generator");
        let args = case.get("args").unwrap_or(&Json::Null);
        let refused = case
            .get("refused")
            .and_then(Json::str)
            .map(|r| r.strip_prefix("ValueError: ").expect("ValueError"));
        let want: Option<Vec<&str>> = match case.get("lua") {
            Some(Json::Str(s)) => Some(vec![s]),
            Some(Json::Arr(a)) => Some(a.iter().map(|v| v.str().expect("lua string")).collect()),
            _ => None,
        };
        ran += 1;
        match (run(generator, args), refused) {
            (None, Some(_)) => {
                decoded_refusals.push(name);
                if !TYPE_REFUSALS.contains(&name) {
                    failures.push(format!("{name}: refused at decode, not in TYPE_REFUSALS"));
                }
            }
            (None, None) => failures.push(format!("{name}: args do not decode")),
            (Some(Err(Refused(got))), Some(msg)) if got == msg => {}
            (Some(Err(Refused(got))), Some(msg)) => {
                failures.push(format!("{name}: refused {got:?}, Python {msg:?}"))
            }
            (Some(Err(Refused(got))), None) => {
                failures.push(format!("{name}: refused {got:?}, Python gave Lua"))
            }
            (Some(Ok(_)), Some(msg)) => {
                failures.push(format!("{name}: gave Lua, Python refused {msg:?}"))
            }
            (Some(Ok(out)), None) => {
                let want = want.expect("lua or refused");
                let got: Vec<String> = match out {
                    Out::One(s) => vec![s],
                    Out::Many(v) => v,
                };
                let single = matches!(case.get("lua"), Some(Json::Str(_)));
                if single && got.len() != 1 {
                    failures.push(format!("{name}: list for a single string"));
                } else if got != want {
                    let at = got.iter().zip(&want).position(|(g, w)| g != w);
                    let detail = match at {
                        Some(i) => {
                            let (g, w) = (&got[i], want[i]);
                            let k = g
                                .bytes()
                                .zip(w.bytes())
                                .position(|(x, y)| x != y)
                                .unwrap_or(g.len().min(w.len()));
                            let lo = k.saturating_sub(40);
                            format!(
                                "eval {i} differs at byte {k}: rust {:?} python {:?}",
                                &g[lo..g.len().min(k + 40)],
                                &w[lo..w.len().min(k + 40)]
                            )
                        }
                        None => format!("{} evals, Python {}", got.len(), want.len()),
                    };
                    failures.push(format!("{name}: {detail}"));
                }
            }
        }
    }
    for listed in TYPE_REFUSALS {
        if !decoded_refusals.contains(listed) {
            failures.push(format!(
                "{listed}: in TYPE_REFUSALS but not refused at decode"
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {ran} corpus cases differ:\n{}",
        failures.len(),
        failures.join("\n")
    );
    assert_eq!(ran, 181, "corpus size changed: re-check TYPE_REFUSALS");
}
