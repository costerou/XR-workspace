//! Replays `rust/corpus/pointer/math.json` (the Python's answers, from `tests/native_spike/pointer_math_corpus.py`)
//! against the ports of orbit.py, neighbors.py, restore_band.py, bump.py and edge_pan.py, and of free_pointer.py's,
//! options_geometry.py's and unified_cursor.py's module-level functions ([`fp_cases`]), byte for byte.
//!
//! Every case is a call (its arguments, and the bytes `json.dumps` wrote of its result or the exception it raised);
//! every trace is an instance's life (the constructor, then each call with its result and the instance's state after
//! it). The Python keeps ints and floats apart, so the answers here are built with the Python's types: where a result's
//! type depends on its inputs (an echo, a `min` that returns its int bound) the rule is spelt out beside it. A name the
//! replay does not know fails the test.

use super::bump::{self, Num, Patch, Pusher, State};
use super::edge_pan::{self, PanFrame, Plan};
use super::neighbors::{self, Direction, Frame};
use super::orbit::{self, Footprint, Geometry, Mat3, PillDrag, Pose, Tile, Vec3, Y_LIMIT};
use super::restore_band::Dwell;
use crate::geometry::Placement;
use crate::pyjson::{Json, float_repr};
use crate::resize::OutputRect;
use std::fmt::Write as _;

mod fp_cases;

const CORPUS: &str = include_str!("../../../../corpus/pointer/math.json");

// --- the Python's json.dumps(separators=(',', ':')) ------------------------------------------------------------------

fn dump(j: &Json) -> String {
    let mut out = String::new();
    write_json(j, &mut out);
    out
}

fn write_json(j: &Json, out: &mut String) {
    match j {
        Json::Null => out.push_str("null"),
        Json::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Json::Int(v) => {
            let _ = write!(out, "{v}");
        }
        Json::Float(v) => out.push_str(&float_repr(*v)),
        Json::Str(s) => write_str(s, out),
        Json::Array(items) => {
            out.push('[');
            for (i, x) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_json(x, out);
            }
            out.push(']');
        }
        Json::Object(entries) => {
            out.push('{');
            for (i, (k, x)) in entries.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_str(k, out);
                out.push(':');
                write_json(x, out);
            }
            out.push('}');
        }
    }
}

/// A string as `json.dumps` writes it (ensure_ascii).
fn write_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if c.is_ascii() && (c as u32) >= 0x20 => out.push(c),
            c => {
                let mut units = [0u16; 2];
                for u in c.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{u:04x}");
                }
            }
        }
    }
    out.push('"');
}

// --- answers, in the Python's types ------------------------------------------------------------------------------------

fn fl(v: f64) -> Json {
    Json::Float(v)
}

fn int(v: i64) -> Json {
    Json::Int(v)
}

fn index(v: usize) -> Json {
    Json::Int(v as i64)
}

fn floats(v: &[f64]) -> Json {
    Json::Array(v.iter().map(|&x| fl(x)).collect())
}

fn vecs(v: &[Vec3]) -> Json {
    Json::Array(v.iter().map(|x| floats(x)).collect())
}

fn matrix(m: &Mat3) -> Json {
    Json::Array(m.iter().map(|row| floats(row)).collect())
}

fn opt<T>(v: Option<T>, f: impl FnOnce(T) -> Json) -> Json {
    v.map_or(Json::Null, f)
}

fn obj(entries: Vec<(&str, Json)>) -> Json {
    Json::Object(entries.into_iter().map(|(k, v)| (k.to_owned(), v)).collect())
}

fn num(n: Num) -> Json {
    match n {
        Num::Int(v) => int(v),
        Num::Float(v) => fl(v),
    }
}

/// `v` in the type the Python's `orig` had: an int stays an int.
fn typed(orig: &Json, v: f64) -> Json {
    match orig {
        Json::Int(_) => int(v as i64),
        _ => fl(v),
    }
}

fn placement_json(p: &Placement) -> Json {
    obj(vec![("x", int(p.x)), ("y", int(p.y)), ("distance", int(p.distance)), ("curvature", int(p.curvature))])
}

fn placements_json(ps: &[Placement]) -> Json {
    Json::Array(ps.iter().map(placement_json).collect())
}

fn state_json(s: &State) -> Json {
    obj(vec![("x", int(s.x)), ("y", int(s.y)), ("distance", num(s.distance)), ("curvature", num(s.curvature))])
}

fn footprint_json(f: &Footprint) -> Json {
    obj(vec![(
        "$footprint",
        obj(vec![
            ("vertices", opt(f.vertices.as_ref(), |v| vecs(v))),
            ("normals", opt(f.normals.as_ref(), |v| vecs(v))),
            ("centre", floats(&f.centre)),
            ("radius", fl(f.radius)),
            ("handles", Json::Array(f.handles.iter().map(footprint_json).collect())),
        ]),
    )])
}

fn frame_json(f: &Frame) -> Json {
    Json::Array(vec![floats(&f.d), floats(&f.r), floats(&f.u)])
}

fn pusher_state(p: &Pusher) -> Json {
    obj(vec![
        ("reached", opt(p.reached.as_ref(), state_json)),
        ("pushed", Json::Array(p.pushed.iter().map(|&i| index(i)).collect())),
        ("blocked", Json::Bool(p.blocked)),
        ("steps", int(p.steps as i64)),
        ("memory", Json::Array(p.memory.iter().map(|(&k, &v)| Json::Array(vec![index(k), index(v)])).collect())),
    ])
}

/// A Plan's `$plan`, each field in the type `kind` gives it.
fn plan_json(p: &Plan, kind: impl Fn(&str, f64) -> Json) -> Json {
    let fields = [
        ("exit", p.exit),
        ("entry", p.entry),
        ("zoom", p.zoom),
        ("rate", p.rate),
        ("way", p.way),
        ("spin", p.spin),
        ("peek_px", p.peek_px),
        ("gap_px", p.gap_px),
        ("total_px", p.total_px),
    ];
    let mut entries = vec![("home", index(p.home)), ("target", index(p.target)), ("side", Json::Str(p.side.name().into()))];
    entries.extend(fields.iter().map(|&(k, v)| (k, kind(k, v))));
    obj(vec![("$plan", obj(entries))])
}

fn pan_frame_json(f: &PanFrame) -> Json {
    obj(vec![
        ("active", Json::Bool(f.active)),
        ("from", index(f.from)),
        ("to", index(f.to)),
        ("side", Json::Str(f.side.name().into())),
        ("exit", int(f.exit)),
        ("entry", int(f.entry)),
        ("peek", int(f.peek)),
        ("cross", int(f.cross)),
        ("turn", int(f.turn)),
        ("way", int(f.way)),
        ("zoom", int(f.zoom)),
    ])
}

// --- arguments ---------------------------------------------------------------------------------------------------------

fn n(j: &Json) -> f64 {
    match j {
        Json::Int(v) => *v as f64,
        Json::Float(v) => *v,
        other => panic!("not a number: {other:?}"),
    }
}

fn i(j: &Json) -> i64 {
    j.as_int().unwrap_or_else(|| panic!("not an int: {j:?}"))
}

fn u(j: &Json) -> usize {
    usize::try_from(i(j)).unwrap_or_else(|_| panic!("not an index: {j:?}"))
}

fn arr(j: &Json) -> &[Json] {
    match j {
        Json::Array(v) => v,
        other => panic!("not a list: {other:?}"),
    }
}

fn key<'a>(j: &'a Json, k: &str) -> &'a Json {
    j.get(k).unwrap_or_else(|| panic!("no {k:?} in {j:?}"))
}

fn fixed<const N: usize>(j: &Json) -> [f64; N] {
    let v = arr(j);
    assert_eq!(v.len(), N, "{j:?}");
    std::array::from_fn(|k| n(&v[k]))
}

fn pair(j: &Json) -> (f64, f64) {
    let [a, b] = fixed::<2>(j);
    (a, b)
}

fn ipair(j: &Json) -> (i64, i64) {
    let v = arr(j);
    (i(&v[0]), i(&v[1]))
}

fn v3s(j: &Json) -> Vec<Vec3> {
    arr(j).iter().map(fixed::<3>).collect()
}

fn mat(j: &Json) -> Mat3 {
    let v = arr(j);
    [fixed::<3>(&v[0]), fixed::<3>(&v[1]), fixed::<3>(&v[2])]
}

fn geometry(j: &Json) -> Geometry {
    Geometry {
        w: i(key(j, "w")),
        h: i(key(j, "h")),
        half: fixed::<2>(key(j, "half")),
        tiles: j.get("tiles").map_or_else(Vec::new, |t| arr(t).iter().map(fixed::<4>).collect()),
    }
}

fn pose(j: &Json) -> Pose {
    Pose {
        x: n(key(j, "x")),
        y: n(key(j, "y")),
        distance: n(key(j, "distance")),
        curvature: n(key(j, "curvature")),
        roll: j.get("roll").map_or(0.0, n),
    }
}

fn poses(j: &Json) -> Vec<Pose> {
    arr(j).iter().map(pose).collect()
}

fn placement(j: &Json) -> Placement {
    Placement { x: i(key(j, "x")), y: i(key(j, "y")), distance: i(key(j, "distance")), curvature: i(key(j, "curvature")) }
}

fn placements(j: &Json) -> Vec<Placement> {
    arr(j).iter().map(placement).collect()
}

fn patch(j: &Json) -> Patch {
    let get = |k: &str| j.get(k).map(i);
    Patch { x: get("x"), y: get("y"), distance: get("distance"), curvature: get("curvature") }
}

fn num_of(j: &Json) -> Num {
    match j {
        Json::Int(v) => Num::Int(*v),
        Json::Float(v) => Num::Float(*v),
        other => panic!("not a number: {other:?}"),
    }
}

/// A `_between` / `_settle` state tuple: (x, y, distance, curvature).
fn state(j: &Json) -> State {
    let v = arr(j);
    State { x: i(&v[0]), y: i(&v[1]), distance: num_of(&v[2]), curvature: num_of(&v[3]) }
}

fn footprint(j: &Json) -> Footprint {
    let f = key(j, "$footprint");
    let list = |k: &str| match key(f, k) {
        Json::Null => None,
        v => Some(v3s(v)),
    };
    Footprint {
        vertices: list("vertices"),
        normals: list("normals"),
        centre: fixed::<3>(key(f, "centre")),
        radius: n(key(f, "radius")),
        handles: arr(key(f, "handles")).iter().map(footprint).collect(),
    }
}

fn frame_of(j: &Json) -> Option<Frame> {
    match j {
        Json::Null => None,
        v => {
            let v = arr(v);
            Some(Frame { d: fixed::<3>(&v[0]), r: fixed::<3>(&v[1]), u: fixed::<3>(&v[2]) })
        }
    }
}

fn side(j: &Json) -> Direction {
    let name = j.as_str().unwrap_or_else(|| panic!("not a side: {j:?}"));
    Direction::parse(name).unwrap_or_else(|| panic!("an unknown side: {name:?}"))
}

fn plan_of(j: &Json) -> Plan {
    let p = key(j, "$plan");
    let f = |k: &str| n(key(p, k));
    Plan {
        home: u(key(p, "home")),
        target: u(key(p, "target")),
        side: side(key(p, "side")),
        exit: f("exit"),
        entry: f("entry"),
        zoom: f("zoom"),
        rate: f("rate"),
        way: f("way"),
        spin: f("spin"),
        peek_px: f("peek_px"),
        gap_px: f("gap_px"),
        total_px: f("total_px"),
    }
}

fn pan_frame(j: &Json) -> PanFrame {
    let g = |k: &str| i(key(j, k));
    PanFrame {
        active: key(j, "active").as_bool().expect("active"),
        from: u(key(j, "from")),
        to: u(key(j, "to")),
        side: side(key(j, "side")),
        exit: g("exit"),
        entry: g("entry"),
        peek: g("peek"),
        cross: g("cross"),
        turn: g("turn"),
        way: g("way"),
        zoom: g("zoom"),
    }
}

fn output(j: &Json) -> OutputRect {
    OutputRect {
        index: u(key(j, "index")),
        x: i(key(j, "x")),
        y: i(key(j, "y")),
        width: n(key(j, "width")),
        height: n(key(j, "height")),
    }
}

fn rolls(j: Option<&Json>) -> Option<Vec<f64>> {
    match j {
        None | Some(Json::Null) => None,
        Some(v) => Some(arr(v).iter().map(n).collect()),
    }
}

/// A call's arguments: positional, else by keyword.
struct Args<'a> {
    pos: &'a [Json],
    kw: &'a Json,
}

impl<'a> Args<'a> {
    fn get(&self, at: usize, name: &str) -> Option<&'a Json> {
        self.pos.get(at).or_else(|| self.kw.get(name))
    }

    fn at(&self, at: usize) -> &'a Json {
        &self.pos[at]
    }
}

/// `_target`'s y after `min(max(y, -Y_LIMIT), Y_LIMIT)`: the int bound when it was clamped (|y| exactly at the pole).
fn pole_y(y: f64) -> Json {
    if y.abs() == Y_LIMIT as f64 { int(y as i64) } else { fl(y) }
}

/// What one call gave: the bytes of its result, or the exception's text.
#[derive(Debug, PartialEq)]
enum Outcome {
    Out(String),
    Raises(String),
}

const INVALID_DIRECTION: &str = "ValueError: invalid direction";

fn direction_or_raise(j: &Json) -> Result<Direction, Outcome> {
    j.as_str().and_then(Direction::parse).ok_or_else(|| Outcome::Raises(INVALID_DIRECTION.into()))
}

/// One case through the port.
fn call(name: &str, a: &Args) -> Outcome {
    let out = match name {
        // --- orbit -----------------------------------------------------------------------------------------------
        "orbit.wrap" => int(orbit::wrap(i(a.at(0)))),
        "orbit._diff" => int(orbit::diff(i(a.at(0)), i(a.at(1)))),
        "orbit._snap" => int(orbit::snap(i(a.at(0)))),
        "orbit.slot_azimuth" => fl(orbit::slot_azimuth(&geometry(a.at(0)), &fixed(a.at(1)))),
        "orbit.rotation" => matrix(&orbit::rotation(n(a.at(0)), n(a.at(1)), n(a.at(2)))),
        "orbit._mul" => matrix(&orbit::mul(&mat(a.at(0)), &mat(a.at(1)))),
        "orbit.apply" => floats(&orbit::apply(&mat(a.at(0)), &fixed(a.at(1)))),
        "orbit.apply_t" => floats(&orbit::apply_t(&mat(a.at(0)), &fixed(a.at(1)))),
        "orbit.place_point" => floats(&orbit::place_point(
            &geometry(a.at(0)),
            &fixed(a.at(1)),
            &pose(a.at(2)),
            n(a.at(3)),
            n(a.at(4)),
        )),
        "orbit.pointer_direction" => {
            let (x, y) = orbit::pointer_direction(&geometry(a.at(0)), fixed(a.at(1)));
            floats(&[x, y])
        }
        "orbit.pointer_ray" => floats(&orbit::pointer_ray(&geometry(a.at(0)), fixed(a.at(1)))),
        "orbit.pointer_at" => floats(&orbit::pointer_at(&geometry(a.at(0)), &fixed(a.at(1)))),
        "orbit.plane_at" => opt(orbit::plane_at(&geometry(a.at(0)), &fixed(a.at(1))), |p| floats(&p)),
        "orbit.from_plane" => floats(&orbit::from_plane(&geometry(a.at(0)), fixed(a.at(1)))),
        "orbit.reach" => {
            let (x, y) = orbit::reach(&geometry(a.at(0)), a.at(1).as_bool().expect("around"));
            floats(&[x, y])
        }
        "orbit.unorbit_ray" => opt(
            orbit::unorbit_ray(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)), fixed(a.at(3))),
            |p| floats(&p),
        ),
        "orbit.slot_direction" => {
            let (x, y) = orbit::slot_direction(&geometry(a.at(0)), &fixed(a.at(1)));
            floats(&[x, y])
        }
        "orbit.grab_point" => {
            floats(&orbit::grab_point(&geometry(a.at(0)), &fixed(a.at(1)), pair(a.at(2)), fixed(a.at(3))))
        }
        "orbit._nearest" => fl(orbit::nearest(n(a.at(0)), n(a.at(1)))),
        "orbit.solve" => {
            let (x, y) =
                orbit::solve(&geometry(a.at(0)), &fixed(a.at(1)), &fixed(a.at(2)), fixed(a.at(3)), pair(a.at(4)));
            floats(&[x, y])
        }
        "orbit._target" => {
            let ((x, y), (sx, sy)) =
                orbit::target(&geometry(a.at(0)), &fixed(a.at(1)), &fixed(a.at(2)), pair(a.at(3)), fixed(a.at(4)));
            Json::Array(vec![Json::Array(vec![fl(x), pole_y(y)]), Json::Array(vec![int(sx), int(sy)])])
        }
        "orbit.drag_target" => {
            let (x, y) = orbit::drag_target(
                &geometry(a.at(0)),
                &fixed(a.at(1)),
                ipair(a.at(2)),
                fixed(a.at(3)),
                fixed(a.at(4)),
            );
            Json::Array(vec![int(x), int(y)])
        }
        "orbit._norm" => opt(orbit::norm(&fixed(a.at(0))), |v| floats(&v)),
        "orbit._dot" => fl(orbit::dot(&fixed(a.at(0)), &fixed(a.at(1)))),
        "orbit._cross" => floats(&orbit::cross(&fixed(a.at(0)), &fixed(a.at(1)))),
        "orbit._angle" => fl(orbit::angle(&fixed(a.at(0)), &fixed(a.at(1)))),
        "orbit._outline" => vecs(&orbit::outline(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)))),
        "orbit._strips" => Json::Array(
            orbit::strips(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2))).iter().map(|s| vecs(s)).collect(),
        ),
        "orbit.Footprint" => {
            let vertices = match a.at(0) {
                Json::Null => None,
                v => Some(v3s(v)),
            };
            let handles = a.get(3, "handles").map_or_else(Vec::new, |h| arr(h).iter().map(footprint).collect());
            footprint_json(&Footprint::new(vertices, fixed(a.at(1)), n(a.at(2)), handles))
        }
        "orbit.Footprint.rotated" => {
            let m = match a.at(1) {
                Json::Null => None,
                m => Some(mat(m)),
            };
            footprint_json(&footprint(a.at(0)).rotated(m.as_ref()))
        }
        "orbit._hull" => {
            let points: Vec<[f64; 2]> = arr(a.at(0)).iter().map(fixed::<2>).collect();
            Json::Array(orbit::hull(&points).into_iter().map(index).collect())
        }
        "orbit.footprint" => footprint_json(&orbit::footprint(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)))),
        "orbit._footprint" => {
            let k = arr(a.at(0));
            let g = Geometry { w: i(&k[0]), h: i(&k[1]), half: fixed(&k[2]), tiles: Vec::new() };
            footprint_json(&orbit::unorbited_footprint(&g, &fixed(&k[3]), n(&k[4]), n(&k[5]), n(&k[6])))
        }
        "orbit._clip" => vecs(&orbit::clip(&v3s(a.at(0)), &fixed(a.at(1)))),
        "orbit._area" => fl(orbit::area(&v3s(a.at(0)))),
        "orbit._shared" => fl(orbit::shared(&footprint(a.at(0)), &footprint(a.at(1)))),
        "orbit.overlap" => fl(orbit::overlap(&footprint(a.at(0)), &footprint(a.at(1)))),

        // --- bump --------------------------------------------------------------------------------------------------
        "bump.turn" => matrix(&bump::turn(n(a.at(0)), n(a.at(1)), n(a.at(2)))),
        "bump.apart" => Json::Bool(bump::apart(&footprint(a.at(0)), &footprint(a.at(1)))),
        "bump._separated" => Json::Bool(bump::separated(&footprint(a.at(0)), &footprint(a.at(1)))),
        "bump._between" => {
            // a tuple: (x, y, distance, curvature)
            let s = bump::between(state(a.at(0)), state(a.at(1)), n(a.at(2)));
            Json::Array(vec![int(s.x), int(s.y), num(s.distance), num(s.curvature)])
        }
        "bump._toward" => int(bump::toward(num_of(a.at(0)), num_of(a.at(1)))),
        "bump._wire" => placement_json(&bump::wire(&State::from(placement(a.at(0))))),
        "bump.settle" => {
            let g = match a.at(0) {
                Json::Null => None,
                g => Some(geometry(g)),
            };
            let first: Vec<i64> = match a.get(2, "first") {
                None | Some(Json::Null) => Vec::new(),
                Some(f) => arr(f).iter().map(i).collect(),
            };
            let (out, moved) = bump::settle(g.as_ref(), &placements(a.at(1)), &first, rolls(a.get(3, "rolls")));
            Json::Array(vec![placements_json(&out), Json::Array(moved.into_iter().map(index).collect())])
        }
        "bump.push" => {
            let g = match a.at(0) {
                Json::Null => None,
                g => Some(geometry(g)),
            };
            let (out, pusher) =
                bump::push(g, u(a.at(1)), &placements(a.at(2)), &patch(a.at(3)), rolls(a.get(4, "rolls")));
            Json::Array(vec![placements_json(&out), obj(vec![("$pusher", pusher_state(&pusher))])])
        }

        // --- neighbors ---------------------------------------------------------------------------------------------
        "neighbors._sub" => floats(&neighbors::sub(&fixed(a.at(0)), &fixed(a.at(1)))),
        "neighbors._scale" => floats(&neighbors::scale(&fixed(a.at(0)), n(a.at(1)))),
        "neighbors.frame" => opt(neighbors::frame(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2))), |f| frame_json(&f)),
        "neighbors.frames" => Json::Array(
            neighbors::frames(&geometry(a.at(0)), &poses(a.at(1))).iter().map(|f| opt(f.as_ref(), frame_json)).collect(),
        ),
        "neighbors.bearing" => {
            let (home, other) = (frame_of(a.at(0)).expect("home"), frame_of(a.at(1)).expect("other"));
            opt(neighbors::bearing(&home, &other), |(angle, (x, y))| Json::Array(vec![fl(angle), floats(&[x, y])]))
        }
        "neighbors.pick" => {
            let d = match direction_or_raise(a.at(2)) {
                Ok(d) => d,
                Err(e) => return e,
            };
            let fs: Vec<Option<Frame>> = arr(a.at(0)).iter().map(frame_of).collect();
            let cone = a.get(3, "cone").map_or(neighbors::CONE, n);
            let home = i(a.at(1));
            let found = usize::try_from(home).ok().and_then(|h| neighbors::pick(&fs, h, d, cone));
            opt(found, index)
        }
        "neighbors.edge_frame" => opt(
            neighbors::edge_frame(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)), side(a.at(3)), n(a.at(4))),
            |f| frame_json(&f),
        ),
        "neighbors.pick_at" => {
            let d = match direction_or_raise(a.at(3)) {
                Ok(d) => d,
                Err(e) => return e,
            };
            let cone = a.get(5, "cone").map_or(neighbors::PAN_CONE, n);
            let home = i(a.at(2));
            let (g, ps) = (geometry(a.at(0)), poses(a.at(1)));
            let found = usize::try_from(home).ok().and_then(|h| neighbors::pick_at(&g, &ps, h, d, n(a.at(4)), cone));
            opt(found, index)
        }
        "neighbors.row_fallback" => {
            let d = match direction_or_raise(a.at(2)) {
                Ok(d) => d,
                Err(e) => return e,
            };
            let (count, home) = (i(a.at(0)), i(a.at(1)));
            let found = match (usize::try_from(count), usize::try_from(home)) {
                (Ok(c), Ok(h)) => neighbors::row_fallback(c, h, d),
                _ => None,
            };
            opt(found, index)
        }

        // --- edge_pan ----------------------------------------------------------------------------------------------
        "edge_pan._unit" => opt(edge_pan::unit(&fixed(a.at(0))), |v| floats(&v)),
        "edge_pan._angle" => fl(edge_pan::angle(&fixed(a.at(0)), &fixed(a.at(1)))),
        "edge_pan.edge_pixel" => {
            let (x, y) = edge_pan::edge_pixel(&fixed(a.at(0)), side(a.at(1)), n(a.at(2)));
            floats(&[x, y])
        }
        "edge_pan.point" => opt(
            edge_pan::point(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)), side(a.at(3)), n(a.at(4))),
            |v| floats(&v),
        ),
        "edge_pan._slot" => {
            let (az, el, r) = edge_pan::slot(&geometry(a.at(0)), &fixed(a.at(1)));
            floats(&[az, el, r])
        }
        "edge_pan.fit_tile" => {
            let (g, tile): (Geometry, Tile) = (geometry(a.at(0)), fixed(a.at(1)));
            let fit = edge_pan::fit_tile(&g, &tile);
            // min(H, W * t3 / t2): the int H unless the other is smaller
            let h = if (g.w as f64 * tile[3] / tile[2]) < g.h as f64 { fl(fit[3]) } else { int(g.h) };
            Json::Array(vec![fl(fit[0]), fl(fit[1]), fl(fit[2]), h])
        }
        "edge_pan.zoom_growth" => fl(edge_pan::zoom_growth(n(a.at(0)), n(a.at(1)))),
        "edge_pan.grown" => fl(edge_pan::grown(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)))),
        "edge_pan.drawn_home" => {
            let zoom = n(a.at(3));
            if zoom <= 0.0 {
                // as given, the very objects
                Json::Array(vec![a.at(1).clone(), a.at(2).clone()])
            } else {
                let (t, q) = edge_pan::drawn_home(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)), zoom);
                let curvature = typed(key(a.at(2), "curvature"), q.curvature);
                Json::Array(vec![
                    floats(&t),
                    obj(vec![("x", fl(q.x)), ("y", fl(q.y)), ("distance", fl(q.distance)), ("curvature", curvature)]),
                ])
            }
        }
        "edge_pan.way" => opt(
            edge_pan::way(
                &geometry(a.at(0)),
                &fixed(a.at(1)),
                &pose(a.at(2)),
                &fixed(a.at(3)),
                &pose(a.at(4)),
                side(a.at(5)),
            ),
            |(t, s)| floats(&[t, s]),
        ),
        "edge_pan.overview_rate" => opt(
            edge_pan::overview_rate(&geometry(a.at(0)), &fixed(a.at(1)), &pose(a.at(2)), side(a.at(3)), pair(a.at(4))),
            fl,
        ),
        "edge_pan.rate" => fl(edge_pan::rate(&geometry(a.at(0)), &fixed(a.at(1)), side(a.at(2)), pair(a.at(3)))),
        "edge_pan.entry_fraction" => opt(
            edge_pan::entry_fraction(
                &geometry(a.at(0)),
                &fixed(a.at(1)),
                &pose(a.at(2)),
                &fixed(a.at(3)),
                &pose(a.at(4)),
                side(a.at(5)),
                n(a.at(6)),
            ),
            fl,
        ),
        "edge_pan._apart" => Json::Bool(edge_pan::apart(&footprint(a.at(0)), &footprint(a.at(1)))),
        "edge_pan.pan_zoom" => fl(edge_pan::pan_zoom(
            &geometry(a.at(0)),
            &fixed(a.at(1)),
            &pose(a.at(2)),
            &fixed(a.at(3)),
            &pose(a.at(4)),
            side(a.at(5)),
            n(a.at(6)),
            n(a.at(7)),
            a.get(8, "home_roll").map_or(0.0, n),
            a.get(9, "target_roll").map_or(0.0, n),
        )),
        "edge_pan.Plan" => {
            let f = |k: usize| n(a.at(k));
            let plan = Plan::new(u(a.at(0)), u(a.at(1)), side(a.at(2)), f(3), f(4), f(5), f(6), f(7), f(8), f(9), f(10));
            // each field as it was given; total_px an int only if both its parts are
            let given = |k: &str| match k {
                "exit" => a.at(3),
                "entry" => a.at(4),
                "zoom" => a.at(5),
                "rate" => a.at(6),
                "way" => a.at(7),
                "spin" => a.at(8),
                "peek_px" => a.at(9),
                _ => a.at(10),
            };
            plan_json(&plan, |k, v| match k {
                "total_px" => match (a.at(9), a.at(10)) {
                    (Json::Int(_), Json::Int(_)) => int(v as i64),
                    _ => fl(v),
                },
                k => typed(given(k), v),
            })
        }
        "edge_pan.Plan.frame" => pan_frame_json(&plan_of(a.at(0)).frame(n(a.at(1)))),
        "edge_pan.plan" => {
            let (g, ps) = (geometry(a.at(0)), poses(a.at(1)));
            let (home, target) = (usize::try_from(i(a.at(2))).ok(), usize::try_from(i(a.at(3))).ok());
            let d = a.at(4).as_str().and_then(Direction::parse);
            let rolls = rolls(a.get(7, "rolls"));
            let overview = a.get(8, "overview").is_some_and(|o| o.as_bool().expect("overview"));
            let found = match (home, target, d) {
                (Some(h), Some(t), Some(d)) => {
                    edge_pan::plan(&g, &ps, h, t, d, n(a.at(5)), pair(a.at(6)), rolls.as_deref(), overview)
                }
                _ => None,
            };
            // max(PEEK_PX, ...): the int 48 unless the other is larger
            opt(found, |p| {
                plan_json(&p, |k, v| if k == "peek_px" && v == edge_pan::PEEK_PX { int(v as i64) } else { fl(v) })
            })
        }
        "edge_pan.frame_bytes" => {
            let payload = match a.at(3) {
                Json::Null => None,
                p => Some(pan_frame(p)),
            };
            let line = edge_pan::frame_bytes(
                a.at(0).as_str().expect("run_id"),
                a.at(1).as_str().expect("session"),
                u64::try_from(i(a.at(2))).expect("request_id"),
                payload.as_ref(),
            );
            obj(vec![("$bytes", Json::Str(String::from_utf8(line).expect("ASCII")))])
        }
        other => return fp_cases::call(other, a),
    };
    Outcome::Out(dump(&out))
}

// --- traces ------------------------------------------------------------------------------------------------------------

/// One instance of a traced class, built from its constructor's arguments.
enum Traced {
    PillDrag(Box<PillDrag>),
    Pusher(Box<Pusher>),
    Dwell(Dwell),
    TextHold(super::free_pointer::TextHold),
}

impl Traced {
    fn new(name: &str, a: &Args) -> Traced {
        match name {
            "orbit.PillDrag" => Traced::PillDrag(Box::new(PillDrag::new(
                geometry(a.at(0)),
                u(a.at(1)),
                ipair(a.at(2)),
                fixed(a.at(3)),
                rolls(a.get(4, "rolls")),
            ))),
            "bump.Pusher" => {
                let g = match a.at(0) {
                    Json::Null => None,
                    g => Some(geometry(g)),
                };
                Traced::Pusher(Box::new(Pusher::new(g, u(a.at(1)), rolls(a.get(2, "rolls")))))
            }
            "restore_band.Dwell" => Traced::Dwell(Dwell::new()),
            "free_pointer.TextHold" => Traced::TextHold(super::free_pointer::TextHold::new()),
            other => panic!("no replay for the class {other:?}"),
        }
    }

    /// One method call: (its result, the instance's state after it), as the corpus keeps them.
    fn call(&mut self, method: &str, a: &Args) -> (String, String) {
        let (out, state) = match (self, method) {
            (Traced::PillDrag(d), "follow") => {
                let out = placements_json(&d.follow(fixed(a.at(0)), &placements(a.at(1))));
                let state = obj(vec![
                    ("at", Json::Array(vec![int(d.at.0), int(d.at.1)])),
                    ("near", Json::Array(vec![fl(d.near.0), pole_y(d.near.1)])),
                    ("local", floats(&d.local)),
                ]);
                (out, state)
            }
            (Traced::Pusher(p), "move") => {
                let start = a.get(2, "start").map(patch);
                let out = placements_json(&p.move_to(&placements(a.at(0)), &patch(a.at(1)), start.as_ref()));
                (out, pusher_state(p))
            }
            (Traced::Dwell(d), m) => {
                let out = match m {
                    "reset" => {
                        d.reset();
                        Json::Null
                    }
                    "restart" => {
                        d.restart();
                        Json::Null
                    }
                    "disarm" => {
                        d.disarm();
                        Json::Null
                    }
                    "step" => {
                        let cursor = match a.at(0) {
                            Json::Null => None,
                            c => Some(ipair(c)),
                        };
                        let rect = match a.at(1) {
                            Json::Null => None,
                            o => Some(output(o)),
                        };
                        let blocked = a.at(2).as_bool().expect("blocked");
                        Json::Bool(d.step(cursor, rect.as_ref(), blocked, n(a.at(3))))
                    }
                    other => panic!("no replay for Dwell.{other}"),
                };
                let state = obj(vec![
                    ("touched", Json::Bool(d.touched)),
                    ("anchor", opt(d.anchor, |(x, y)| Json::Array(vec![int(x), int(y)]))),
                    ("since", opt(d.since, fl)),
                    ("armed", Json::Bool(d.armed)),
                ]);
                (out, state)
            }
            (Traced::TextHold(t), "step") => {
                let out = Json::Bool(t.step(a.at(0).as_bool().expect("text"), n(a.at(1))));
                (out, fp_cases::text_hold_state(t))
            }
            (Traced::TextHold(t), "clear") => {
                t.clear();
                (Json::Null, fp_cases::text_hold_state(t))
            }
            (_, other) => panic!("no replay for the method {other:?}"),
        };
        (dump(&out), dump(&state))
    }
}

// --- the replay --------------------------------------------------------------------------------------------------------

fn corpus() -> Json {
    load(CORPUS)
}

/// `json.loads`, exactly. `Json::parse` goes through serde_json's fast float parser, which can land a float one ulp
/// off the Python's (serde_json's `float_roundtrip` is off); the corpus needs the very doubles, so its numbers go
/// through `str::parse::<f64>`, which rounds correctly.
fn load(text: &str) -> Json {
    let mut p = Loader { s: text.as_bytes(), at: 0 };
    let v = p.value();
    p.space();
    assert_eq!(p.at, p.s.len(), "trailing text in the corpus");
    v
}

struct Loader<'a> {
    s: &'a [u8],
    at: usize,
}

impl Loader<'_> {
    fn space(&mut self) {
        while self.s.get(self.at).is_some_and(|c| c.is_ascii_whitespace()) {
            self.at += 1;
        }
    }

    fn peek(&mut self) -> u8 {
        self.space();
        self.s[self.at]
    }

    fn eat(&mut self, c: u8) {
        assert_eq!(self.peek(), c, "at byte {}", self.at);
        self.at += 1;
    }

    fn word(&mut self, w: &str, v: Json) -> Json {
        assert!(self.s[self.at..].starts_with(w.as_bytes()), "at byte {}", self.at);
        self.at += w.len();
        v
    }

    fn value(&mut self) -> Json {
        match self.peek() {
            b'{' => {
                self.at += 1;
                let mut entries = Vec::new();
                if self.peek() == b'}' {
                    self.at += 1;
                    return Json::Object(entries);
                }
                loop {
                    let Json::Str(k) = self.value() else { panic!("a key that is not a string") };
                    self.eat(b':');
                    entries.push((k, self.value()));
                    if self.peek() == b',' {
                        self.at += 1;
                    } else {
                        self.eat(b'}');
                        return Json::Object(entries);
                    }
                }
            }
            b'[' => {
                self.at += 1;
                let mut items = Vec::new();
                if self.peek() == b']' {
                    self.at += 1;
                    return Json::Array(items);
                }
                loop {
                    items.push(self.value());
                    if self.peek() == b',' {
                        self.at += 1;
                    } else {
                        self.eat(b']');
                        return Json::Array(items);
                    }
                }
            }
            b'"' => Json::Str(self.string()),
            b't' => self.word("true", Json::Bool(true)),
            b'f' => self.word("false", Json::Bool(false)),
            b'n' => self.word("null", Json::Null),
            _ => self.number(),
        }
    }

    fn number(&mut self) -> Json {
        let start = self.at;
        while self.s.get(self.at).is_some_and(|c| c.is_ascii_digit() || b"+-.eE".contains(c)) {
            self.at += 1;
        }
        let text = std::str::from_utf8(&self.s[start..self.at]).expect("ASCII");
        if text.bytes().any(|c| b".eE".contains(&c)) {
            Json::Float(text.parse().unwrap_or_else(|_| panic!("not a float: {text:?}")))
        } else {
            Json::Int(text.parse().unwrap_or_else(|_| panic!("not an int: {text:?}")))
        }
    }

    fn string(&mut self) -> String {
        self.eat(b'"');
        let mut units: Vec<u16> = Vec::new();
        let mut out = String::new();
        loop {
            let c = self.s[self.at];
            self.at += 1;
            if c == b'\\' {
                let e = self.s[self.at];
                self.at += 1;
                if e == b'u' {
                    let hex = std::str::from_utf8(&self.s[self.at..self.at + 4]).expect("ASCII");
                    units.push(u16::from_str_radix(hex, 16).expect("hex"));
                    self.at += 4;
                    continue;
                }
                out.extend(char::decode_utf16(units.drain(..)).map(|c| c.expect("UTF-16")));
                out.push(match e {
                    b'n' => '\n',
                    b'r' => '\r',
                    b't' => '\t',
                    b'b' => '\u{8}',
                    b'f' => '\u{c}',
                    other => other as char,
                });
                continue;
            }
            out.extend(char::decode_utf16(units.drain(..)).map(|c| c.expect("UTF-16")));
            if c == b'"' {
                return out;
            }
            // raw UTF-8: copy the whole character
            let len = match c {
                0..0x80 => 1,
                0xe0..0xf0 => 3,
                0xf0.. => 4,
                _ => 2,
            };
            out.push_str(std::str::from_utf8(&self.s[self.at - 1..self.at - 1 + len]).expect("UTF-8"));
            self.at += len - 1;
        }
    }
}

fn args_of(j: &Json) -> Args<'_> {
    Args { pos: arr(key(j, "args")), kw: key(j, "kwargs") }
}

#[test]
fn every_case_gives_the_python_bytes() {
    let corpus = corpus();
    assert_eq!(key(&corpus, "schema").as_int(), Some(1));
    let (mut total, mut wrong, mut by_name) = (0, Vec::new(), std::collections::BTreeMap::new());
    for (name, cases) in key(&corpus, "cases").as_object().expect("cases") {
        for (k, case) in arr(cases).iter().enumerate() {
            let want = match (case.get("out"), case.get("raises")) {
                (Some(out), None) => Outcome::Out(out.as_str().expect("out").to_owned()),
                (None, Some(raises)) => Outcome::Raises(raises.as_str().expect("raises").to_owned()),
                _ => panic!("{name}[{k}]: neither out nor raises"),
            };
            let got = call(name, &args_of(case));
            total += 1;
            if got != want {
                *by_name.entry(name.as_str()).or_insert(0) += 1;
                wrong.push(format!("{name}[{k}]: {}\n  got  {got:?}\n  want {want:?}", dump(key(case, "args"))));
            }
        }
    }
    assert!(total > 2000, "only {total} cases");
    assert!(
        wrong.is_empty(),
        "{} of {total} cases differ ({by_name:?}):\n{}",
        wrong.len(),
        wrong[..wrong.len().min(10)].join("\n")
    );
}

#[test]
fn every_trace_gives_the_python_bytes() {
    let corpus = corpus();
    let (mut total, mut wrong) = (0, Vec::new());
    for (name, traces) in key(&corpus, "traces").as_object().expect("traces") {
        for (t, trace) in arr(traces).iter().enumerate() {
            let mut obj = Traced::new(name, &args_of(key(trace, "init")));
            for (k, c) in arr(key(trace, "calls")).iter().enumerate() {
                let method = key(c, "method").as_str().expect("method");
                let (out, state) = obj.call(method, &args_of(c));
                total += 1;
                let want = (key(c, "out").as_str().expect("out"), key(c, "state").as_str().expect("state"));
                if (out.as_str(), state.as_str()) != want {
                    wrong.push(format!("{name}[{t}].{method}#{k}\n  got  {out} {state}\n  want {} {}", want.0, want.1));
                    break; // the rest of the trace follows from this call
                }
            }
        }
    }
    assert!(total > 500, "only {total} trace calls");
    assert!(wrong.is_empty(), "{} traces differ:\n{}", wrong.len(), wrong[..wrong.len().min(10)].join("\n"));
}

#[test]
fn an_unknown_name_fails_the_replay() {
    let empty = Json::Array(Vec::new());
    let kw = Json::Object(Vec::new());
    let a = Args { pos: arr(&empty), kw: &kw };
    assert!(std::panic::catch_unwind(|| call("orbit.no_such_function", &a)).is_err());
    assert!(std::panic::catch_unwind(|| call("free_pointer.no_such_function", &a)).is_err());
    assert!(std::panic::catch_unwind(|| call("unified_cursor.no_such_function", &a)).is_err());
    assert!(std::panic::catch_unwind(|| Traced::new("orbit.NoSuchClass", &a)).is_err());
}

#[test]
fn the_dump_writes_what_python_writes() {
    let j = load("{\"a\":[1,1.0,-0.0,1e-05,null,true,\"x\\\"\\n\u{e9}\"]}");
    assert_eq!(dump(&j), r#"{"a":[1,1.0,-0.0,1e-05,null,true,"x\"\n\u00e9"]}"#);
}
