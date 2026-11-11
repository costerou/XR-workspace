//! Port of spike/tests/free_pointer_test.cpp (ctest free_pointer) beyond its pointer_raster checks, which are in
//! tests/pointer_beam.rs: the `overview_pointer` frame decodes strictly against the shared fixture, the
//! `overview_geometry` line is the supervisor's, the dot is a closed disc where asked (or turned to its direction), its
//! look's marks and the Serif Beam vertex for vertex, the drag ghost's outline, the drop frame, and the OLED levels.

use serde_json::Value as Json;
use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::view::*;
use xr_wire::control::{self, Op, Payload, PointerLook as WireLook, Stream};

const FIXTURES: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../tests/native_spike/fixtures/"
);
const CANVAS: Canvas = Canvas::new(1920.0, 1200.0, 0.36, 0.225);
const EMPTY: Canvas = Canvas::new(0.0, 0.0, 0.36, 0.225);
const SEGMENTS: i32 = FREE_POINTER_SEGMENTS;
const PI: f64 = std::f64::consts::PI;

fn load(name: &str) -> Json {
    serde_json::from_str(&std::fs::read_to_string(format!("{FIXTURES}{name}")).unwrap()).unwrap()
}

fn frame(payload: &Json, id: u64) -> String {
    format!(
        "{{\"schema\":1,\"request_id\":{id},\"run_id\":\"run\",\"session\":\"session\",\"op\":\"overview_pointer\",\"payload\":{payload}}}\n"
    )
}

/// Canvas pixel of a point on the canvas plane (z = -1).
fn pixel(c: &Canvas, v: &Vec3) -> (f32, f32) {
    (
        (v.x / c.half_w + 1.0) * c.view_w / 2.0,
        (1.0 - v.y / c.half_h) * c.view_h / 2.0,
    )
}

/// The direction of a canvas point on CANVAS: turns nothing.
fn own(x: f32, y: f32) -> (f32, f32) {
    let c = &CANVAS;
    let wx = (2.0 * f64::from(x) / f64::from(c.view_w) - 1.0) * f64::from(c.half_w);
    let wy = (1.0 - 2.0 * f64::from(y) / f64::from(c.view_h)) * f64::from(c.half_h);
    (wx.atan2(1.0) as f32, wy.atan2(wx.hypot(1.0)) as f32)
}

fn dir(v: &Vec3) -> (f64, f64) {
    let (x, y, z) = (f64::from(v.x), f64::from(v.y), f64::from(v.z));
    (x.atan2(-z), y.atan2(x.hypot(z)))
}

fn angle(a: &Vec3, b: &Vec3) -> f64 {
    let (ax, ay, az) = (f64::from(a.x), f64::from(a.y), f64::from(a.z));
    let (bx, by, bz) = (f64::from(b.x), f64::from(b.y), f64::from(b.z));
    let d = (ax * bx + ay * by + az * bz)
        / ((ax * ax + ay * ay + az * az).sqrt() * (bx * bx + by * by + bz * bz).sqrt());
    d.clamp(-1.0, 1.0).acos()
}

/// C++ std::remainder.
fn remainder(x: f64, y: f64) -> f64 {
    x - (x / y).round_ties_even() * y
}

#[test]
fn control_frame_decodes_strictly() {
    let cases = load("overview_pointer_payloads.json");
    let looks = [
        ("resize_ew", WireLook::ResizeEw),
        ("resize_ns", WireLook::ResizeNs),
        ("resize_nwse", WireLook::ResizeNwse),
        ("resize_nesw", WireLook::ResizeNesw),
        ("move", WireLook::Move),
        ("rotate", WireLook::Rotate),
        ("text", WireLook::Text),
    ];
    let mut id = 1;
    for p in cases["valid"].as_array().unwrap() {
        let m = control::decode(frame(p, id).as_bytes()).unwrap_or_else(|e| panic!("{p}: {e:?}"));
        id += 1;
        assert_eq!(m.op, Op::OverviewPointer);
        let Payload::OverviewPointer(v) = &m.payload else {
            panic!("{p}")
        };
        assert_eq!(Json::from(v.visible), p["visible"]);
        assert_eq!(Json::from(v.held), p["held"]);
        assert_eq!(Json::from(v.x), p["x"]);
        assert_eq!(Json::from(v.y), p["y"]);
        assert_eq!(v.direction.is_some(), p.get("az").is_some(), "{p}");
        if let Some((az, el)) = v.direction {
            assert_eq!(
                (Json::from(az), Json::from(el)),
                (p["az"].clone(), p["el"].clone())
            );
        }
        // window drag: the ghost's size and the desktop it would land on
        assert_eq!(v.drag.is_some(), p.get("drag").is_some(), "{p}");
        if let Some(g) = v.drag {
            let d = &p["drag"];
            assert_eq!(
                (Json::from(g.w), Json::from(g.h), Json::from(g.target)),
                (d["w"].clone(), d["h"].clone(), d["target"].clone())
            );
        }
        match p.get("look") {
            None => assert_eq!(v.look, WireLook::Plain),
            Some(l) => {
                let want = looks
                    .iter()
                    .find(|(name, _)| l == name)
                    .unwrap_or_else(|| panic!("look {l}"));
                assert_eq!(v.look, want.1);
            }
        }
    }
    for c in cases["invalid"].as_array().unwrap() {
        let r = match control::decode(frame(&c["payload"], id).as_bytes()) {
            Ok(_) => panic!("accepted invalid payload ({})", c["why"]),
            Err(r) => r,
        };
        id += 1;
        if !c["payload"].is_object() {
            continue; // the envelope itself requires an object: that rejection is fatal
        }
        assert!(r.ignorable(), "{}", c["why"]);
        assert_eq!(r.code, "invalid_overview_pointer");
    }
    // Bad framing around the op is still fatal; a dropped frame does not use up its request id.
    let mut unframed = frame(&cases["valid"][0], id);
    unframed.pop();
    assert!(control::decode(unframed.as_bytes()).is_err_and(|r| !r.ignorable()));
    let mut q = Stream::new("run", "session").unwrap();
    let dropped = q.feed(frame(&cases["invalid"][0]["payload"], 9).as_bytes());
    assert!(matches!(&dropped[..], [Err(r)] if r.refusal.ignorable()));
    let taken = q.feed(frame(&cases["valid"][1], 9).as_bytes());
    assert!(matches!(&taken[..], [Ok(_)]));
}

#[test]
fn overview_geometry_line() {
    // The status line the supervisor parses (free_pointer.parse_overview_geometry is tested on the same `line`).
    let g = load("overview_pointer_payloads.json")["geometry"].clone();
    let f = |v: &Json| v.as_f64().unwrap() as f32;
    let tiles: Vec<FRect> = g["tiles"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| FRect::new(f(&t[0]), f(&t[1]), f(&t[2]), f(&t[3])))
        .collect();
    let (w, h) = (
        g["w"].as_u64().unwrap() as u32,
        g["h"].as_u64().unwrap() as u32,
    );
    let line = overview_geometry_json(&tiles, w, h, f(&g["half"][0]), f(&g["half"][1]));
    assert_eq!(line, g["line"].as_str().unwrap());
    let parsed: Json = serde_json::from_str(&line).unwrap();
    assert_eq!(
        parsed["overview_geometry"]["tiles"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    // ... and it matches the overview layout the renderer draws (a 1920x1200 view, three 16:9 desktops, spacing 40).
    let sizes = [Size::new(1920.0, 1080.0); 3];
    let layout = overview_layout(&sizes, 1920.0, 1200.0, 40.0);
    assert_eq!(
        overview_geometry_json(&layout, 1920, 1200, 0.36, 0.225),
        line
    );
    // Unusable inputs give no line rather than a wrong one.
    assert!(overview_geometry_json(&[], 1920, 1200, 0.36, 0.225).is_empty());
    assert!(overview_geometry_json(&layout, 0, 1200, 0.36, 0.225).is_empty());
    assert!(overview_geometry_json(&layout, 1920, 1200, 0.0, 0.225).is_empty());
    assert!(overview_geometry_json(&layout, 1920, 1200, f32::NAN, 0.225).is_empty());
    assert!(
        overview_geometry_json(&[FRect::new(0.0, 0.0, -1.0, 10.0)], 1920, 1200, 0.36, 0.225)
            .is_empty()
    );
    // Every desktop count the run allows gets its line, never just three.
    for n in 1..=MAX_DESKTOPS {
        let row = overview_layout(&vec![Size::new(1920.0, 1200.0); n], 1920.0, 1200.0, 20.0);
        let json = overview_geometry_json(&row, 1920, 1200, 0.41360, 0.25850);
        let parsed: Json =
            serde_json::from_str(&json).unwrap_or_else(|_| panic!("{n} desktops: no line"));
        assert_eq!(
            parsed["overview_geometry"]["tiles"]
                .as_array()
                .unwrap()
                .len(),
            n
        );
    }
    // more than the run ever has: no line
    let too_many = overview_layout(
        &vec![Size::new(1920.0, 1200.0); MAX_DESKTOPS + 1],
        1920.0,
        1200.0,
        20.0,
    );
    assert!(overview_geometry_json(&too_many, 1920, 1200, 0.36, 0.225).is_empty());
    // The settled spacing, not the eased one, defines the overview.
    let mut view = ViewAnimator::default();
    view.set_target(false, 0, 120, 0.0);
    assert!((view.target_spacing() - 120.0).abs() < 1e-4);
}

#[test]
fn dot_is_a_closed_disc_where_asked() {
    for (x, y) in [(960.0f32, 600.0f32), (-40.0, -12.0), (2100.0, 300.0)] {
        let fan = canvas_disc(&CANVAS, x, y, FREE_POINTER_RADIUS_PX, SEGMENTS);
        assert_eq!(fan.len(), SEGMENTS as usize + 2);
        for v in &fan {
            assert!((v.z + 1.0).abs() < 1e-6);
        }
        let (cx, cy) = pixel(&CANVAS, &fan[0]);
        assert!((cx - x).abs() < 1e-2 && (cy - y).abs() < 1e-2);
        for v in &fan[1..] {
            let (px, py) = pixel(&CANVAS, v);
            assert!(((px - x).hypot(py - y) - FREE_POINTER_RADIUS_PX).abs() < 1e-2);
        }
        assert!(fan[1].x == fan.last().unwrap().x && fan[1].y == fan.last().unwrap().y); // closed
        let again = canvas_disc(&CANVAS, x, y, FREE_POINTER_RADIUS_PX, SEGMENTS);
        for (a, b) in fan.iter().zip(&again) {
            assert!(a.x == b.x && a.y == b.y);
        }
    }
    assert!(canvas_disc(&EMPTY, 1.0, 1.0, 6.0, SEGMENTS).is_empty());
    assert!(canvas_disc(&CANVAS, 1.0, 1.0, 0.0, SEGMENTS).is_empty());
    assert!(canvas_disc(&CANVAS, 1.0, 1.0, 6.0, 2).is_empty());
}

#[test]
fn dot_turned_to_its_direction() {
    for (x, y, az, el) in [
        (2880.0f32, 600.0f32, PI as f32, 0.0f32),
        (2400.0, 100.0, 2.1, 0.5),
        (-700.0, -500.0, -2.8, 1.45),
        (960.0, 1700.0, 0.0, -1.3),
        // inside the view: orbit.pointer_direction's (u * half_w, v * half_h)
        (1500.0, 400.0, 0.2025, 0.075),
        (100.0, 1150.0, -0.3225, -0.20625),
    ] {
        let fan = direction_disc(&CANVAS, x, y, az, el, FREE_POINTER_RADIUS_PX, SEGMENTS);
        assert_eq!(fan.len(), SEGMENTS as usize + 2);
        let (a, e) = dir(&fan[0]);
        assert!(
            remainder(a - f64::from(az), 2.0 * PI).abs() < 1e-4 || e.abs() > 1.5,
            "({x},{y})"
        );
        assert!((e - f64::from(el)).abs() < 1e-4, "({x},{y})");
        let flat = canvas_disc(
            &CANVAS,
            x.clamp(0.0, 1920.0),
            y.clamp(0.0, 1200.0),
            FREE_POINTER_RADIUS_PX,
            SEGMENTS,
        );
        for i in 1..fan.len() {
            assert!((angle(&fan[0], &fan[i]) - angle(&flat[0], &flat[i])).abs() < 1e-5);
        }
        assert!(fan[1].x == fan.last().unwrap().x && fan[1].y == fan.last().unwrap().y); // closed
    }
    for (x, y) in [(1920.0f32, 600.0f32), (0.0, 0.0), (700.0, 1200.0)] {
        let wx = (2.0 * f64::from(x) / 1920.0 - 1.0) * 0.36;
        let wy = (1.0 - 2.0 * f64::from(y) / 1200.0) * 0.225;
        let at_edge = direction_disc(
            &CANVAS,
            x,
            y,
            wx.atan2(1.0) as f32,
            wy.atan2(wx.hypot(1.0)) as f32,
            FREE_POINTER_RADIUS_PX,
            SEGMENTS,
        );
        let flat = canvas_disc(&CANVAS, x, y, FREE_POINTER_RADIUS_PX, SEGMENTS);
        for (a, b) in at_edge.iter().zip(&flat) {
            assert!(
                (a.x - b.x).abs() < 1e-5 && (a.y - b.y).abs() < 1e-5 && (a.z - b.z).abs() < 1e-5
            );
        }
    }
    assert!(direction_disc(&EMPTY, 1.0, 1.0, 0.0, 0.0, 6.0, SEGMENTS).is_empty());
}

#[test]
fn look_marks_round_the_dot() {
    let reach = FREE_POINTER_RADIUS_PX + FREE_POINTER_RING_PX + POINTER_MARK_GAP_PX;
    let (own_az, own_el) = own(700.0, 500.0);
    // on the canvas plane: each mark's tip relative to the dot, canvas px
    let tips = |look: PointerLook| {
        let tris = direction_marks(&CANVAS, 700.0, 500.0, own_az, own_el, look, 0.0);
        assert_eq!(tris.len() % 3, 0);
        let mut out = Vec::new();
        for t in tris.chunks(3) {
            let (tx, ty) = pixel(&CANVAS, &t[0]);
            for v in t {
                // on the plane, clear of the ring, never far from the dot
                let (px, py) = pixel(&CANVAS, v);
                assert!((v.z + 1.0).abs() < 1e-3);
                let r = (px - 700.0).hypot(py - 500.0);
                assert!(
                    r >= reach - 1e-1
                        && r <= reach + 2.0 * POINTER_MARK_HALF_PX + POINTER_MARK_LEN_PX,
                    "{look:?}: {r}"
                );
            }
            out.push((tx - 700.0, ty - 500.0));
        }
        out
    };
    let near = |t: (f32, f32), x: f32, y: f32| (t.0 - x).abs() < 0.1 && (t.1 - y).abs() < 0.1;
    let tip = reach + POINTER_MARK_LEN_PX;
    let diag = tip * std::f32::consts::FRAC_1_SQRT_2;
    assert!(direction_marks(&CANVAS, 700.0, 500.0, 0.0, 0.0, PointerLook::Plain, 0.0).is_empty());
    let (ew, ns) = (tips(PointerLook::ResizeEw), tips(PointerLook::ResizeNs));
    assert!(ew.len() == 2 && near(ew[0], tip, 0.0) && near(ew[1], -tip, 0.0));
    assert!(ns.len() == 2 && near(ns[0], 0.0, tip) && near(ns[1], 0.0, -tip));
    let (nwse, nesw) = (tips(PointerLook::ResizeNwse), tips(PointerLook::ResizeNesw));
    assert!(nwse.len() == 2 && near(nwse[0], diag, diag) && near(nwse[1], -diag, -diag)); // down-right and up-left
    assert!(nesw.len() == 2 && near(nesw[0], diag, -diag) && near(nesw[1], -diag, diag)); // up-right and down-left
    let mv = tips(PointerLook::Move);
    assert!(
        mv.len() == 4
            && near(mv[0], tip, 0.0)
            && near(mv[1], -tip, 0.0)
            && near(mv[2], 0.0, tip)
            && near(mv[3], 0.0, -tip)
    );
    let rotate = tips(PointerLook::Rotate); // left going up, right going down: clockwise
    let side = reach + POINTER_MARK_HALF_PX;
    assert!(
        rotate.len() == 2
            && near(rotate[0], -side, -POINTER_MARK_LEN_PX)
            && near(rotate[1], side, POINTER_MARK_LEN_PX)
    );
    // Turned with the dot anywhere round you, as one rigid piece: each mark keeps its angle from the dot's centre.
    let flat = direction_marks(
        &CANVAS,
        700.0,
        500.0,
        own_az,
        own_el,
        PointerLook::Move,
        0.0,
    );
    let flat_disc = canvas_disc(&CANVAS, 700.0, 500.0, FREE_POINTER_RADIUS_PX, SEGMENTS);
    let turned = direction_marks(&CANVAS, 700.0, 500.0, 2.1, 0.5, PointerLook::Move, 0.0);
    let disc = direction_disc(
        &CANVAS,
        700.0,
        500.0,
        2.1,
        0.5,
        FREE_POINTER_RADIUS_PX,
        SEGMENTS,
    );
    assert_eq!(turned.len(), flat.len());
    for (t, f) in turned.iter().zip(&flat) {
        assert!((angle(&disc[0], t) - angle(&flat_disc[0], f)).abs() < 1e-5);
    }
    // The outline pass is the same marks, fatter: it shows round the grey on every side.
    let fat = direction_marks(
        &CANVAS,
        700.0,
        500.0,
        0.0,
        0.0,
        PointerLook::ResizeEw,
        FREE_POINTER_RING_PX,
    );
    let thin = direction_marks(&CANVAS, 700.0, 500.0, 0.0, 0.0, PointerLook::ResizeEw, 0.0);
    assert_eq!(fat.len(), thin.len());
    let area = |t: &[Vec3]| {
        let ((ax, ay), (bx, by), (cx, cy)) = (
            pixel(&CANVAS, &t[0]),
            pixel(&CANVAS, &t[1]),
            pixel(&CANVAS, &t[2]),
        );
        ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)).abs() / 2.0
    };
    assert!(area(&fat) > area(&thin) * 1.5);
    assert!(direction_marks(&EMPTY, 1.0, 1.0, 0.0, 0.0, PointerLook::Move, 0.0).is_empty());
}

#[test]
fn serif_beam_vertex_for_vertex() {
    let beam = load("serif_beam_shape.json");
    let (own_az, own_el) = own(700.0, 500.0);
    for (key, grow) in [("ring", FREE_POINTER_RING_PX), ("ink", 0.0)] {
        let want = beam[key].as_array().unwrap();
        let got = direction_marks(
            &CANVAS,
            700.0,
            500.0,
            own_az,
            own_el,
            PointerLook::Text,
            grow,
        );
        assert!(got.len() == want.len() && got.len() % 3 == 0, "{key}");
        for (g, w) in got.iter().zip(want) {
            let (px, py) = pixel(&CANVAS, g);
            assert!((g.z + 1.0).abs() < 1e-3);
            let (wx, wy) = (w[0].as_f64().unwrap() as f32, w[1].as_f64().unwrap() as f32);
            assert!(
                (px - 700.0 - wx).abs() < 0.01 && (py - 500.0 - wy).abs() < 0.01,
                "{key}: ({px},{py}) vs {w}"
            );
        }
    }
    // A rigid shape, the dot's turn: every vertex keeps its angle from the click point, anywhere round you.
    let ring = direction_marks(
        &CANVAS,
        700.0,
        500.0,
        own_az,
        own_el,
        PointerLook::Text,
        FREE_POINTER_RING_PX,
    );
    let ink = direction_marks(
        &CANVAS,
        700.0,
        500.0,
        own_az,
        own_el,
        PointerLook::Text,
        0.0,
    );
    let hub = canvas_disc(&CANVAS, 700.0, 500.0, FREE_POINTER_RADIUS_PX, SEGMENTS);
    let spun = direction_disc(
        &CANVAS,
        700.0,
        500.0,
        2.1,
        0.5,
        FREE_POINTER_RADIUS_PX,
        SEGMENTS,
    );
    let turned = direction_marks(
        &CANVAS,
        700.0,
        500.0,
        2.1,
        0.5,
        PointerLook::Text,
        FREE_POINTER_RING_PX,
    );
    assert_eq!(turned.len(), ring.len());
    for (t, r) in turned.iter().zip(&ring) {
        assert!((angle(&spun[0], t) - angle(&hub[0], r)).abs() < 1e-5);
    }
    // Past the view's edge it is clamped with the dot, and an empty canvas draws nothing.
    let (edge_az, edge_el) = own(1920.0, 600.0);
    let clamped = direction_marks(
        &CANVAS,
        2100.0,
        600.0,
        edge_az,
        edge_el,
        PointerLook::Text,
        0.0,
    );
    assert_eq!(clamped.len(), ink.len());
    for v in &clamped {
        let (px, py) = pixel(&CANVAS, v);
        assert!((px - 1920.0).abs() <= 5.01 && (py - 600.0).abs() <= 12.01);
    }
    assert!(direction_marks(&EMPTY, 1.0, 1.0, 0.0, 0.0, PointerLook::Text, 0.0).is_empty());
    // The dot's grey and held grey stay under the OLED cap, so the beam does (gl draws it in the dot's own level).
    assert!((FREE_POINTER_HELD_LEVEL * 255.0 + 0.5) as i32 <= 191);
}

#[test]
fn drag_ghost_outline() {
    let ghost = Size::new(80.0, 50.0);
    let strip = direction_outline(&CANVAS, 700.0, 500.0, 0.0, 0.0, ghost, GHOST_THICKNESS_PX);
    assert_eq!(strip.len(), 10);
    assert!(strip[0].x == strip[8].x && strip[0].y == strip[8].y && strip[1].x == strip[9].x); // closed
    let (a, e) = own(700.0, 500.0);
    let flat = direction_outline(&CANVAS, 700.0, 500.0, a, e, ghost, GHOST_THICKNESS_PX); // its own direction: flat
    let (mut lo_x, mut hi_x, mut lo_y, mut hi_y) = (1e9f32, -1e9f32, 1e9f32, -1e9f32);
    for v in flat.iter().step_by(2) {
        // the outer corners
        assert!((v.z + 1.0).abs() < 1e-5);
        let (px, py) = pixel(&CANVAS, v);
        lo_x = lo_x.min(px);
        hi_x = hi_x.max(px);
        lo_y = lo_y.min(py);
        hi_y = hi_y.max(py);
    }
    assert!(
        (lo_x - 660.0).abs() < 1e-2
            && (hi_x - 740.0).abs() < 1e-2
            && (lo_y - 475.0).abs() < 1e-2
            && (hi_y - 525.0).abs() < 1e-2
    );
    let (ix, iy) = pixel(&CANVAS, &flat[1]);
    assert!(
        (ix - (660.0 + GHOST_THICKNESS_PX)).abs() < 1e-2
            && (iy - (475.0 + GHOST_THICKNESS_PX)).abs() < 1e-2
    );
    // Past the view (behind you, up): drawn round the dot there, every corner as far from the dot's centre, as seen
    // from the eye, as on the plane at the clamped point.
    let dot = direction_disc(
        &CANVAS,
        2400.0,
        100.0,
        2.1,
        0.5,
        FREE_POINTER_RADIUS_PX,
        SEGMENTS,
    );
    let turned = direction_outline(&CANVAS, 2400.0, 100.0, 2.1, 0.5, ghost, GHOST_THICKNESS_PX);
    let edge_dot = canvas_disc(&CANVAS, 1920.0, 100.0, FREE_POINTER_RADIUS_PX, SEGMENTS);
    let (ex, ey): (f64, f64) = (0.36, (1.0 - 2.0 * 100.0 / 1200.0) * 0.225); // the clamped point (1920, 100)'s own direction
    let edge = direction_outline(
        &CANVAS,
        1920.0,
        100.0,
        ex.atan2(1.0) as f32,
        ey.atan2(ex.hypot(1.0)) as f32,
        ghost,
        GHOST_THICKNESS_PX,
    );
    for (t, g) in turned.iter().zip(&edge) {
        assert!((angle(&dot[0], t) - angle(&edge_dot[0], g)).abs() < 1e-5);
    }
    assert!(direction_outline(&CANVAS, 1.0, 1.0, 0.0, 0.0, Size::new(0.0, 50.0), 2.0).is_empty());
    assert!(direction_outline(&CANVAS, 1.0, 1.0, 0.0, 0.0, ghost, 0.0).is_empty());
    assert!(direction_outline(&EMPTY, 1.0, 1.0, 0.0, 0.0, ghost, 2.0).is_empty());
    assert!(GHOST_LEVEL > 0.0 && GHOST_LEVEL <= 191.0 / 255.0);
}

#[test]
fn drop_frame_bands() {
    // The target desktop's frame: four bands DROP_FRAME_PX wide just inside its edge, covering the edge exactly once.
    let t = FRect::new(40.0, 435.0, 586.67, 330.0);
    let f = drop_frame(&t);
    assert!(
        f[0].x == t.x && f[0].y == t.y && f[0].width == t.width && f[0].height == DROP_FRAME_PX
    );
    assert!((f[1].y + f[1].height - (t.y + t.height)).abs() < 1e-3 && f[1].height == DROP_FRAME_PX);
    assert!(
        f[2].x == t.x
            && f[2].width == DROP_FRAME_PX
            && f[2].y == t.y + DROP_FRAME_PX
            && (f[2].height - (t.height - 2.0 * DROP_FRAME_PX)).abs() < 1e-3
    );
    assert!((f[3].x + f[3].width - (t.x + t.width)).abs() < 1e-3);
    for r in drop_frame(&FRect::new(0.0, 0.0, 6.0, 4.0)) {
        assert!(r.width >= 0.0 && r.height >= 0.0);
    }
}

#[test]
fn oled_levels() {
    // Dim greys at or under the cards' cap; the ring is black, so the dot is never surrounded by light.
    let cap = 191.0 / 255.0;
    assert!(FREE_POINTER_LEVEL > 0.0 && FREE_POINTER_LEVEL <= cap);
    assert!((FREE_POINTER_LEVEL..=cap).contains(&FREE_POINTER_HELD_LEVEL));
    assert!(
        FREE_POINTER_RADIUS_PX > 0.0
            && FREE_POINTER_RING_PX > 0.0
            && FREE_POINTER_RADIUS_PX + FREE_POINTER_RING_PX <= 12.0
    );
}
