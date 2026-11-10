//! The replay of free_pointer.py's, options_geometry.py's and unified_cursor.py's module-level functions (the ports in
//! [`free_pointer`], [`options_geometry`], [`unified_cursor`]). Each answer is built in the Python's types from the
//! inputs' types, never from the answer it is held to.

use super::*;
use crate::lua::BindCommand;
use crate::supervisor::free_pointer::{self as fp, Handle, Hit, HitOptions};
use crate::supervisor::options_geometry::{self as og, RangeRow};
use crate::supervisor::python::Invalid;
use crate::supervisor::sampler::Side;
use crate::supervisor::unified_cursor as uc;

const INVALID_CURSOR: &str = "ValueError: invalid cursor position";

fn raises(e: Invalid) -> Outcome {
    Outcome::Raises(format!("ValueError: {}", e.0))
}

fn refused(e: crate::lua::Refused) -> Outcome {
    Outcome::Raises(format!("ValueError: {}", e.0))
}

fn maybe(j: Option<&Json>) -> Option<&Json> {
    j.filter(|j| **j != Json::Null)
}

fn opt_pair(j: Option<&Json>) -> Option<[f64; 2]> {
    maybe(j).map(fixed::<2>)
}

fn opt_int(j: Option<&Json>) -> Option<i64> {
    maybe(j).map(i)
}

fn point(j: &Json) -> uc::Point {
    pair(j)
}

fn outputs(j: &Json) -> Vec<OutputRect> {
    arr(j).iter().map(output).collect()
}

fn side_of(j: &Json) -> Side {
    let name = j.as_str().expect("a side");
    Side::parse(name).unwrap_or_else(|| panic!("an unknown side: {name:?}"))
}

fn ints(p: (i64, i64)) -> Json {
    Json::Array(vec![int(p.0), int(p.1)])
}

fn s(v: &str) -> Json {
    Json::Str(v.into())
}

fn bytes(line: String) -> Json {
    obj(vec![("$bytes", Json::Str(line))])
}

fn is_int(j: &Json) -> bool {
    matches!(j, Json::Int(_))
}

/// A name the Python's hit tuple carries, as the port's `&'static str`.
fn known(name: &str) -> &'static str {
    let names = fp::EDGES
        .iter()
        .chain(&crate::resize::CORNERS)
        .chain(&fp::LEVEL_SEGMENTS)
        .chain(&fp::PANEL_ACTIONS)
        .chain(&fp::HELP_RECTS)
        .chain(&crate::geometry::PLACEMENT_KEYS)
        .chain(&["main", "tail", crate::supervisor::environment::BRIGHTNESS_FIELD])
        .chain(crate::supervisor::environment::BACKGROUNDS.iter().map(|(n, _)| n));
    names.copied().find(|n| *n == name).unwrap_or_else(|| Box::leak(name.to_owned().into_boxed_str()))
}

/// A hit tuple as the Python passes it.
fn hit_of(j: &Json) -> Option<Hit> {
    let v = match j {
        Json::Null => return None,
        j => arr(j),
    };
    let kind = v[0].as_str().expect("a hit kind");
    if kind == "help" {
        return Some(Hit::Help(known(v[1].as_str().expect("a help rect"))));
    }
    let at = u(&v[1]);
    let name = || known(v[2].as_str().expect("a name"));
    Some(match kind {
        "pill" => Hit::Pill(at),
        "panel" => Hit::Panel(at),
        "slider" => Hit::Slider(at, name(), n(&v[3])),
        "scale" => Hit::Scale(at, i(&v[2])),
        "background" => Hit::Background(at, name()),
        "resolution" => Hit::Resolution(at, ipair(&v[2])),
        "resolution_box" => Hit::ResolutionBox(at),
        "scale_box" => Hit::ScaleBox(at),
        "background_box" => Hit::BackgroundBox(at),
        "background_switch" => Hit::BackgroundSwitch(at),
        "action" => Hit::Action(at, name()),
        "corner" => Hit::Corner(at, name()),
        "edge" => Hit::Edge(at, name()),
        "desktop" => Hit::Desktop(at, fixed(&v[2])),
        "maximize" => Hit::Maximize(at),
        "options_gear" => Hit::OptionsGear(at),
        "immersive" => Hit::Immersive(at, name()),
        "roll" => Hit::Roll(at, name()),
        other => panic!("an unknown hit kind {other:?}"),
    })
}

/// A hit as the Python's tuple; a slider's `lx` in `lx`'s type.
fn hit_json(h: &Hit, lx: impl Fn(f64) -> Json) -> Json {
    let k = s(h.kind());
    Json::Array(match *h {
        Hit::Pill(at)
        | Hit::Panel(at)
        | Hit::ResolutionBox(at)
        | Hit::ScaleBox(at)
        | Hit::BackgroundBox(at)
        | Hit::BackgroundSwitch(at)
        | Hit::Maximize(at)
        | Hit::OptionsGear(at) => vec![k, index(at)],
        Hit::Slider(at, field, x) => vec![k, index(at), s(field), lx(x)],
        Hit::Scale(at, p) => vec![k, index(at), int(p)],
        Hit::Resolution(at, size) => vec![k, index(at), ints(size)],
        Hit::Background(at, name)
        | Hit::Action(at, name)
        | Hit::Corner(at, name)
        | Hit::Edge(at, name)
        | Hit::Immersive(at, name)
        | Hit::Roll(at, name) => vec![k, index(at), s(name)],
        Hit::Desktop(at, q) => vec![k, index(at), floats(&q)],
        Hit::Help(name) => vec![k, s(name)],
    })
}

fn maximize(j: &Json) -> fp::MaximizeGeometry {
    fp::parse_maximize_geometry(j).expect("a maximize geometry").expect("not None")
}

fn bind<'a>(a: &'a Args) -> BindCommand<'a> {
    let t = |k: usize| a.at(k).as_str().expect("a string");
    BindCommand { python: t(0), client: t(1), control_socket: t(2), run_id: t(3), session: t(4) }
}

/// An int position for the Lua, or the Python's refusal of any other.
fn position(j: Option<&Json>) -> Result<Option<i64>, Outcome> {
    match maybe(j) {
        None => Ok(None),
        Some(Json::Int(v)) => Ok(Some(*v)),
        Some(_) => Err(Outcome::Raises(INVALID_CURSOR.into())),
    }
}

fn lua(r: crate::lua::Result<String>) -> Result<Json, Outcome> {
    r.map(Json::Str).map_err(refused)
}

/// The echo of a parsed status line: the Python returns the very value, checked against the port's numbers.
fn echo(j: &Json, numbers: &[f64]) -> Json {
    fn walk(j: &Json, out: &mut Vec<f64>) {
        match j {
            Json::Int(_) | Json::Float(_) => out.push(n(j)),
            Json::Array(v) => v.iter().for_each(|x| walk(x, out)),
            Json::Object(v) => v.iter().for_each(|(_, x)| walk(x, out)),
            _ => {}
        }
    }
    let mut given = Vec::new();
    walk(j, &mut given);
    assert_eq!(given, numbers, "the port parsed other numbers");
    j.clone()
}

pub(super) fn call(name: &str, a: &Args) -> Outcome {
    match answer(name, a) {
        Ok(out) => Outcome::Out(dump(&out)),
        Err(o) => o,
    }
}

fn answer(name: &str, a: &Args) -> Result<Json, Outcome> {
    let g = || geometry(a.at(0));
    let tile = |k: usize| fixed::<4>(a.at(k));
    let out = match name {
        // --- free_pointer: the status lines -------------------------------------------------------------------------
        "free_pointer.parse_overview_geometry" => {
            let g = fp::parse_overview_geometry(a.at(0)).map_err(raises)?;
            let mut numbers = vec![g.w as f64, g.h as f64, g.half[0], g.half[1]];
            numbers.extend(g.tiles.iter().flatten());
            echo(a.at(0), &numbers)
        }
        "free_pointer.parse_maximize_geometry" => match fp::parse_maximize_geometry(a.at(0)).map_err(raises)? {
            None => Json::Null,
            Some(m) => {
                let mut numbers: Vec<f64> = [&m.canvas[..], &m.tile, &m.placement, &m.rect, &m.picture].concat();
                numbers.extend(m.hit_mesh.iter().flatten());
                numbers.extend(m.view);
                // the Python's dict order: monitor, maximized, then the numbers in the order checked
                let mut keyed: Vec<f64> = Vec::new();
                for k in ["canvas", "tile", "placement", "rect", "picture", "hit_mesh", "view"] {
                    let mut one = Vec::new();
                    echo_numbers(key(a.at(0), k), &mut one);
                    keyed.extend(one);
                }
                assert_eq!(keyed, numbers, "the port parsed other numbers");
                assert_eq!(Some(m.monitor.as_str()), key(a.at(0), "monitor").as_str());
                a.at(0).clone()
            }
        },
        "free_pointer.parse_roll_geometry" => {
            Json::Array(fp::parse_roll_geometry(a.at(0)).map_err(raises)?.into_iter().map(|t| opt(t, int)).collect())
        }
        "free_pointer.parse_help_geometry" => match fp::parse_help_geometry(a.at(0)).map_err(raises)? {
            None => Json::Null,
            Some(h) => {
                let mut numbers = Vec::new();
                for (k, _) in a.at(0).as_object().expect("a dict") {
                    numbers.extend(h.rect(k).expect("a parsed rect"));
                }
                echo(a.at(0), &numbers)
            }
        },
        "free_pointer.band_ray" => floats(&fp::band_ray(&maximize(a.at(0)), n(a.at(1)), n(a.at(2)))),
        "free_pointer.restore_mesh_hit" => {
            Json::Bool(fp::restore_mesh_hit(&maximize(a.at(0)), maybe(a.get(1, "ray")).map(fixed::<3>)))
        }
        // --- the level bar, the 3D button and the roll ----------------------------------------------------------------
        "free_pointer.level_bar_scale" => fl(fp::level_bar_scale(&tile(0))),
        "free_pointer.level_bar_rect" => floats(&fp::level_bar_rect(&tile(0))),
        "free_pointer.maximize_rect" => floats(&fp::maximize_rect(&tile(0))),
        "free_pointer.maximize_hit" => Json::Bool(fp::maximize_hit(&tile(0), opt_pair(a.get(1, "q")))),
        "free_pointer.level_bar_segment" => opt(fp::level_bar_segment(&tile(0), opt_pair(a.get(1, "q"))), s),
        "free_pointer._immersive_rects" => {
            opt(fp::immersive_rects(&tile(0)), |(m, t)| Json::Array(vec![floats(&m), floats(&t)]))
        }
        "free_pointer.immersive_main_rect" => floats(&fp::immersive_main_rect(&tile(0))),
        "free_pointer.immersive_tail_rect" => floats(&fp::immersive_tail_rect(&tile(0))),
        "free_pointer.immersive_part" => opt(fp::immersive_part(&tile(0), opt_pair(a.get(1, "q"))), s),
        "free_pointer.roll_side" => opt(fp::roll_side(opt_int(a.get(0, "tilt")), i(a.at(1))), s),
        "free_pointer.roll_click" => {
            let side = a.at(2).as_str().unwrap_or("");
            int(fp::roll_click(opt_int(a.get(0, "tilt")), i(a.at(1)), side).map_err(raises)?)
        }
        "free_pointer.roll_drag" => int(fp::roll_drag(opt_int(a.get(0, "tilt")), n(a.at(1)), n(a.at(2)))),
        "free_pointer.level_roll" => int(fp::level_roll(opt_int(a.get(0, "tilt")))),
        "free_pointer.roll_frame" => {
            let rid = u64::try_from(i(a.at(2))).expect("request_id");
            let run = a.at(0).as_str().expect("run_id");
            bytes(fp::roll_frame(run, a.at(1).as_str().expect("session"), rid, a.at(3)).map_err(raises)?)
        }
        "free_pointer.restore_band_frame" => {
            let Some(open) = a.at(3).as_bool() else {
                return Err(Outcome::Raises("ValueError: invalid restore band".into()));
            };
            let rid = u64::try_from(i(a.at(2))).expect("request_id");
            bytes(fp::restore_band_frame(a.at(0).as_str().expect("run_id"), a.at(1).as_str().expect("session"), rid, open))
        }
        "free_pointer.overview_pointer_frame" => {
            let rid = u64::try_from(i(a.at(2))).expect("request_id");
            let run = a.at(0).as_str().expect("run_id");
            bytes(fp::overview_pointer_frame(run, a.at(1).as_str().expect("session"), rid, a.at(3)).map_err(raises)?)
        }
        // --- the help card --------------------------------------------------------------------------------------------
        "free_pointer.help_hit" => {
            // the help dict as given (help_hit reads it unchecked)
            let help = maybe(Some(a.at(0))).map(|h| fp::HelpGeometry {
                card: fixed(key(h, "card")),
                prev: h.get("prev").map(fixed),
                indicator: h.get("indicator").map(fixed),
                next: h.get("next").map(fixed),
            });
            let margin = a.get(2, "margin").map_or(0.0, n);
            opt(fp::help_hit(help.as_ref(), opt_pair(a.get(1, "pointer")), margin), |h| hit_json(&h, fl))
        }
        // --- the canvas and a desktop's surface -----------------------------------------------------------------------
        "free_pointer.place_point" => {
            floats(&fp::place_point(&g(), &tile(1), &pose(a.at(2)), n(a.at(3)), n(a.at(4))))
        }
        "free_pointer.to_canvas" => opt(fp::to_canvas(&g(), &fixed(a.at(1))), |p| floats(&p)),
        "free_pointer.project" => floats(&fp::project(&g(), &tile(1), &pose(a.at(2)), n(a.at(3)), n(a.at(4)))),
        "free_pointer._flat" => {
            opt(fp::flat(&g(), &tile(1), &pose(a.at(2)), n(a.at(3)), n(a.at(4))), |p| floats(&p))
        }
        "free_pointer._onto_edges" => floats(&fp::onto_edges(&tile(0), fixed(a.at(1)))),
        "free_pointer.unproject" => {
            opt(fp::unproject(&g(), &tile(1), &pose(a.at(2)), fixed(a.at(3))), |p| floats(&p))
        }
        "free_pointer.surface_to_layout" => {
            let inset = a.get(3, "inset").map_or(fp::INSET_PX, n);
            ints(fp::surface_to_layout(&tile(0), &output(a.at(1)), fixed(a.at(2)), inset))
        }
        "free_pointer.layout_to_surface" => {
            floats(&fp::layout_to_surface(&tile(0), &output(a.at(1)), n(a.at(2)), n(a.at(3))))
        }
        // --- the hit zones ---------------------------------------------------------------------------------------------
        "free_pointer.corner_zone" => fl(fp::corner_zone(&tile(0))),
        "free_pointer.border_handle" => opt(fp::border_handle(&tile(0), fixed(a.at(1))), |h| match h {
            Handle::Corner(c) => Json::Array(vec![s("corner"), s(c)]),
            Handle::Edge(e) => Json::Array(vec![s("edge"), s(e)]),
        }),
        "free_pointer.zone_of" => s(fp::zone_of(hit_of(a.at(0)).as_ref())),
        "free_pointer.look_for" => opt(fp::look_for(hit_of(a.at(0)).as_ref()), s),
        "free_pointer.is_text_shape" => Json::Bool(fp::is_text_shape(a.at(0).as_str())),
        // --- the glass ---------------------------------------------------------------------------------------------
        "free_pointer.glass_anchor" => {
            let (x, y) = fp::glass_anchor(&tile(0));
            floats(&[x, y])
        }
        "free_pointer.glass_to_desktop" => {
            let (x, y) = fp::glass_to_desktop(&tile(0), &pose(a.at(1)), n(a.at(2)), n(a.at(3)));
            floats(&[x, y])
        }
        "free_pointer.desktop_to_glass" => {
            opt(fp::desktop_to_glass(&tile(0), &pose(a.at(1)), opt_pair(a.get(2, "q"))), |p| floats(&p))
        }
        "free_pointer.project_glass" => {
            floats(&fp::project_glass(&g(), &tile(1), &pose(a.at(2)), n(a.at(3)), n(a.at(4))))
        }
        "free_pointer.pill_rect" => floats(&fp::pill_rect(&tile(0))),
        "free_pointer.panel_rect" => floats(&fp::panel_rect(&tile(0))),
        "free_pointer.action_rect" => floats(&fp::action_rect(i(a.at(0)))),
        "free_pointer.scale_item_rect" => floats(&fp::scale_item_rect(i(a.at(0)))),
        "free_pointer.background_item_rect" => floats(&fp::background_item_rect(i(a.at(0)))),
        "free_pointer.resolution_list_rect" => floats(&fp::resolution_list_rect()),
        "free_pointer.resolution_item_rect" => floats(&fp::resolution_item_rect(i(a.at(0)))),
        "free_pointer.slider_fraction" => fl(fp::slider_fraction(n(a.at(0)))),
        "free_pointer.panel_hit" => {
            let flag = |k: usize, name: &str| a.get(k, name).and_then(Json::as_bool).unwrap_or(false);
            let h = fp::panel_hit(
                n(a.at(0)),
                n(a.at(1)),
                u(a.at(2)),
                flag(3, "picker"),
                flag(4, "resolution_picker"),
                flag(5, "background_picker"),
            );
            let lx = a.at(0);
            hit_json(&h, |v| typed(lx, v))
        }
        // --- what is under the dot ------------------------------------------------------------------------------------
        "free_pointer.front_order" => {
            Json::Array(fp::front_order(&g(), &poses(a.at(1))).into_iter().map(index).collect())
        }
        "free_pointer.hit" => {
            let g = maybe(Some(a.at(0))).map(geometry);
            let icons: Option<Vec<Option<&str>>> =
                maybe(a.get(4, "icons")).map(|v| arr(v).iter().map(Json::as_str).collect());
            let flag = |k: usize, name: &str| a.get(k, name).and_then(Json::as_bool).unwrap_or(false);
            let opts = HitOptions {
                open: opt_int(a.get(3, "open_")),
                icons: icons.as_deref(),
                picker: flag(5, "picker"),
                resolution_picker: flag(6, "resolution_picker"),
                background_picker: flag(7, "background_picker"),
            };
            let h = fp::hit(g.as_ref(), &poses(a.at(1)), opt_pair(a.get(2, "pointer")), &opts);
            opt(h, |h| hit_json(&h, fl))
        }
        "free_pointer.on_pill" => Json::Bool(fp::on_pill(&tile(0), &pose(a.at(1)), opt_pair(a.get(2, "q")))),
        "free_pointer.on_drawn_pill" => Json::Bool(fp::on_drawn_pill(&tile(0), opt_pair(a.get(1, "q")))),
        "free_pointer.in_front_of" => {
            Json::Bool(fp::in_front_of(&g(), &poses(a.at(1)), u(a.at(2)), u(a.at(3)), fixed(a.at(4))))
        }
        "free_pointer.inside_by" => fl(fp::inside_by(&tile(0), fixed(a.at(1)))),
        "free_pointer.resize_distance" => {
            let corner = a.at(1).as_str().unwrap_or("");
            int(fp::resize_distance(n(a.at(0)), corner, n(a.at(2)), n(a.at(3)), &tile(4)).map_err(raises)?)
        }
        "free_pointer.resize_reach" => {
            let p = arr(a.at(1));
            let g = g();
            let r = fp::resize_reach(&g, fixed(a.at(1)));
            // an axis inside the view is the very value; past an edge a float
            let axis = |k: usize, size: i64| {
                let v = n(&p[k]);
                if 0.0 <= v && v <= size as f64 { p[k].clone() } else { fl(r[k]) }
            };
            Json::Array(vec![axis(0, g.w), axis(1, g.h)])
        }
        "free_pointer.desktop_at" => {
            let g = maybe(Some(a.at(0))).map(geometry);
            opt(fp::desktop_at(g.as_ref(), &poses(a.at(1)), opt_pair(a.get(2, "pointer"))), index)
        }
        "free_pointer.ghost_size" => {
            let window = maybe(Some(a.at(0))).map(|w| [n(key(w, "w")), n(key(w, "h"))]);
            let out = maybe(Some(a.at(1))).map(output);
            ints(fp::ghost_size(window, out.as_ref(), &tile(2), &pose(a.at(3))))
        }
        // --- free_pointer's Lua ---------------------------------------------------------------------------------------
        "free_pointer.grab_pointer_lua" => {
            let (x, y) = (position(Some(a.at(5)))?, position(Some(a.at(6)))?);
            lua(fp::grab_pointer_lua(&bind(a), x.expect("x"), y.expect("y")))?
        }
        "free_pointer.release_pointer_lua" => {
            let (x, y) = (position(a.get(0, "x"))?, position(a.get(1, "y"))?);
            lua(fp::release_pointer_lua(x, y))?
        }
        // --- options_geometry -------------------------------------------------------------------------------------------
        "options_geometry.inside" => Json::Bool(og::inside(&tile(0), opt_pair(a.get(1, "point")))),
        "options_geometry.gear_hit" => Json::Bool(og::gear_hit(&tile(0), opt_pair(a.get(1, "point")))),
        "options_geometry.gear_rect" => {
            let p = arr(a.at(0));
            let r = og::gear_rect(&tile(0));
            let x = if is_int(&p[0]) && is_int(&p[2]) { int(r[0] as i64) } else { fl(r[0]) };
            Json::Array(vec![x, p[1].clone(), int(og::GEAR_W), int(og::GEAR_H)])
        }
        "options_geometry.range_value" => {
            let row = a.at(0);
            let row = RangeRow {
                minimum: num_of(key(row, "minimum")),
                maximum: num_of(key(row, "maximum")),
                step: row.get("step").map_or(Num::Int(0), num_of),
            };
            num(og::range_value(&row, num_of(a.at(1))))
        }
        "options_geometry.row_at" => opt(og::row_at(n(a.at(0)), n(a.at(1)), n(a.at(2)), i(a.at(3))), int),
        "options_geometry.tab_at" => opt(og::tab_at(n(a.at(0)), n(a.at(1)), i(a.at(2)), i(a.at(3))), int),
        "options_geometry.header_at" => opt(og::header_at(n(a.at(0)), n(a.at(1))), s),
        // --- unified_cursor -----------------------------------------------------------------------------------------------
        "unified_cursor._output_at" => {
            let list = outputs(a.at(0));
            let found = uc::output_at(&list, point(a.at(1)));
            opt(found.map(|o| list.iter().position(|x| std::ptr::eq(x, o)).expect("one of them")), |k| {
                arr(a.at(0))[k].clone()
            })
        }
        "unified_cursor.inside" => {
            let inset = a.get(2, "inset").map_or(fp::INSET_PX, n);
            ints(uc::inside(&output(a.at(0)), point(a.at(1)), inset))
        }
        "unified_cursor.confine_target" => {
            let previous = maybe(a.get(3, "previous")).map(point);
            let cursor = maybe(Some(a.at(2))).map(point);
            opt(uc::confine_target(&outputs(a.at(0)), i(a.at(1)), cursor, previous), ints)
        }
        "unified_cursor._contact" => {
            Json::Array(uc::contact(&output(a.at(0)), point(a.at(1))).into_iter().map(|d| s(d.as_str())).collect())
        }
        "unified_cursor._crossing" => {
            let o = output(a.at(0));
            let c = arr(a.at(1));
            let (side, by) = uc::crossing(&o, point(a.at(1)));
            // int only when the axis's coordinate is and the clamp kept it (not the far edge's width - 1)
            let k = usize::from(!side.horizontal());
            let local = n(&c[k]) - if k == 0 { o.x as f64 } else { o.y as f64 };
            let size = if k == 0 { o.width } else { o.height };
            let by = if is_int(&c[k]) && local.max(0.0) <= size - 1.0 { int(by as i64) } else { fl(by) };
            Json::Array(vec![s(side.as_str()), by])
        }
        "unified_cursor._nudge" => ints(uc::nudge(&output(a.at(0)), point(a.at(1)), side_of(a.at(2)))),
        "unified_cursor.along" => fl(uc::along(&output(a.at(0)), point(a.at(1)), side_of(a.at(2)))),
        "unified_cursor._band" => opt(uc::band(&output(a.at(0)), point(a.at(1))), |d| s(d.as_str())),
        "unified_cursor.hold_point" => ints(uc::hold_point(&output(a.at(0)), point(a.at(1)), side_of(a.at(2)))),
        "unified_cursor.pan_point" => {
            ints(uc::pan_point(&output(a.at(0)), side_of(a.at(1)), n(a.at(2)), n(a.at(3))))
        }
        "unified_cursor.entry_point" => {
            let inset = a.get(3, "inset").map_or(uc::ENTRY_PX, n);
            ints(uc::entry_point(&output(a.at(0)), side_of(a.at(1)), n(a.at(2)), inset))
        }
        "unified_cursor.click_binds_lua" => lua(uc::click_binds_lua(&bind(a)))?,
        "unified_cursor.enter_desktop_lua" => {
            let (x, y) = (position(a.get(5, "x"))?, position(a.get(6, "y"))?);
            let monitor = maybe(a.get(7, "monitor")).map(|m| m.as_str().expect("a monitor"));
            lua(uc::enter_desktop_lua(&bind(a), x, y, monitor))?
        }
        "unified_cursor.confine_lua" => {
            let Some(name) = a.at(2).as_str() else {
                return Err(Outcome::Raises("ValueError: confinement is limited to managed outputs".into()));
            };
            let (x, y) = (position(Some(a.at(0)))?, position(Some(a.at(1)))?);
            lua(uc::confine_lua(x.expect("x"), y.expect("y"), name))?
        }
        other => panic!("no replay for {other:?}"),
    };
    Ok(out)
}

fn echo_numbers(j: &Json, out: &mut Vec<f64>) {
    match j {
        Json::Int(_) | Json::Float(_) => out.push(n(j)),
        Json::Array(v) => v.iter().for_each(|x| echo_numbers(x, out)),
        _ => panic!("not numbers: {j:?}"),
    }
}

/// free_pointer.TextHold's state as the corpus keeps it, `left` and `until` in `now`'s type.
pub(super) fn text_hold_state(t: &fp::TextHold) -> Json {
    obj(vec![
        ("on", Json::Bool(t.on)),
        ("until", opt(t.until, fl)),
        ("left", opt(t.left, fl)),
        ("sweep", fl(t.sweep)),
    ])
}
