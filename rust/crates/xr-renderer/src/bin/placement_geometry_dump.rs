//! Prints world points of placed desktops (view.rs place_point on glass.rs placement_from_wire, so the wire units are
//! part of it) as one JSON line, for placement_contract_test.py, which checks that tools/native_spike/orbit.py
//! place_point (the supervisor's pill drag, hit testing and no-overlap guard) mirrors them: every canvas x tile x wire
//! placement x point. Every desktop always faces you (a billboard toward the eye), so off-centre tiles are where the two
//! could differ most. Byte for byte the output of spike/tests/placement_geometry_dump.cpp.

use std::f32::consts::PI;
use std::fmt::Write as _;

use xr_renderer::cfmt;
use xr_renderer::glass::{glass_panel, glass_pill, placement_from_wire};
use xr_renderer::hud::MenuPlacement;
use xr_renderer::pose::{aim_rotation, quat_angle};
use xr_renderer::view::{
    Canvas, EdgePan, FRect, Placement, Size, edge_pan_path, focus_direction, glass_to_desktop, overview_layout,
    place_point,
};

/// printf "%.9g": round-trips a float.
fn num(v: f32) -> String {
    cfmt::g(f64::from(v), 9)
}

fn main() {
    // Canvases (px and the half-FOV tangents) and their tiles: the overview's real row of three, and hand-made ones off
    // the canvas's grid (fractional): partly off the left edge, up and right, down and left, centred, and far off to the
    // right and above (a zoomed layout's neighbour).
    let canvases = [
        Canvas::new(1920.0, 1080.0, 0.414_213_56, 0.233_007_62),
        Canvas::new(3840.0, 1080.0, 0.8, 0.225),
        Canvas::new(1280.0, 800.0, 0.5, 0.3125),
    ];
    let sources = [Size::new(1920.0, 1080.0), Size::new(2560.0, 1440.0), Size::new(1080.0, 1920.0)];
    // Wire placements: the orbit's x / y (tenths of a degree) at the slot, small, large, both poles and behind you,
    // combined with the rest of the placement (distance and curvature).
    let xs: [i32; 8] = [0, 37, -450, 900, -900, 1800, -1800, 1234];
    let ys: [i32; 4] = [0, 300, -899, 900];
    let rests: [MenuPlacement; 8] = [
        [0, 0, 100, 0],
        [0, 0, 50, 120],
        [0, 0, 200, 0],
        [0, 0, 137, 45],
        [0, 0, 200, 120],
        [0, 0, 73, 7],
        [0, 0, 10, 0],
        [0, 0, 10, 120], // the distance floor: ten times the size
    ];
    let mut out = String::from("{\"cases\":[");
    let mut first = true;
    for c in &canvases {
        let mut tiles = overview_layout(&sources, c.view_w, c.view_h, 40.0);
        tiles.push(FRect::new(-200.5, 410.25, 812.0, 457.0));
        tiles.push(FRect::new(c.view_w * 0.6 + 0.5, 20.25, 300.75, 168.5));
        tiles.push(FRect::new(33.3, c.view_h - 150.7, 410.1, 230.6));
        tiles.push(FRect::new(c.view_w / 2.0 - 400.0, c.view_h / 2.0 - 225.0, 800.0, 450.0));
        tiles.push(FRect::new(c.view_w + 40.5, -c.view_h * 0.4, c.view_w, c.view_h * 0.9));
        for t in &tiles {
            // Corners, centre, and points outside the tile (its frame / halo, the pill strip under it, far off).
            let points = [
                (t.x, t.y),
                (t.x + t.width, t.y),
                (t.x, t.y + t.height),
                (t.x + t.width, t.y + t.height),
                (t.x + t.width / 2.0, t.y + t.height / 2.0),
                (t.x + t.width * 0.3, t.y + t.height * 0.8),
                (t.x - 50.0, t.y + t.height / 3.0),
                (t.x + t.width + 30.0, t.y + t.height + 24.0),
                (t.x + t.width / 4.0, t.y - 100.0),
                (0.0, 0.0),
                (c.view_w, c.view_h),
            ];
            // The levelling roll (hundredths of a degree, orbit.py's optional 'roll'): none for every rest, then a few
            // on the first two rests.
            let rolls: [i32; 4] = [0, 1500, -4250, 9000];
            for &roll in &rolls {
                for (r, rest) in rests.iter().enumerate() {
                    for &x in &xs {
                        for &y in &ys {
                            if roll != 0 && r > 1 {
                                continue;
                            }
                            let mut wire = *rest;
                            wire[0] = x;
                            wire[1] = y;
                            let mut p = placement_from_wire(&wire);
                            p.roll = roll as f32 / 100.0 * PI / 180.0;
                            out += if first { "{" } else { ",{" };
                            first = false;
                            let _ = write!(
                                out,
                                "\"canvas\":[{},{},{},{}]",
                                num(c.view_w),
                                num(c.view_h),
                                num(c.half_w),
                                num(c.half_h)
                            );
                            let _ = write!(
                                out,
                                ",\"tile\":[{},{},{},{}]",
                                num(t.x),
                                num(t.y),
                                num(t.width),
                                num(t.height)
                            );
                            out += ",\"placement\":{";
                            let keys: [&str; 4] = ["x", "y", "distance", "curvature"]; // one per MenuPlacement field
                            for (k, key) in keys.iter().enumerate() {
                                let _ = write!(out, "{}\"{key}\":{}", if k != 0 { "," } else { "" }, wire[k]);
                            }
                            if roll != 0 {
                                let _ = write!(out, ",\"roll\":{roll}");
                            }
                            out += "},\"points\":[";
                            let mut first_point = true;
                            for &(px, py) in &points {
                                let w = place_point(c, t, &p, px, py);
                                let _ = write!(
                                    out,
                                    "{}{},{},{},{},{}]",
                                    if first_point { "[" } else { ",[" },
                                    num(px),
                                    num(py),
                                    num(w.x),
                                    num(w.y),
                                    num(w.z)
                                );
                                first_point = false;
                            }
                            // Glass (the pill and panel corners, glass.rs): drawn through view.rs glass_to_desktop,
                            // fixed size.
                            out += "],\"glass\":[";
                            let pill = glass_pill(t);
                            let panel = glass_panel(t);
                            let glass = [
                                (pill.x, pill.y),
                                (pill.x + pill.width, pill.y + pill.height),
                                (panel.x, panel.y + panel.height),
                                (panel.x + panel.width, panel.y),
                            ];
                            first_point = true;
                            for &(gx, gy) in &glass {
                                let [dx, dy] = glass_to_desktop(t, &p, gx, gy);
                                let w = place_point(c, t, &p, dx, dy);
                                let _ = write!(
                                    out,
                                    "{}{},{},{},{},{}]",
                                    if first_point { "[" } else { ",[" },
                                    num(gx),
                                    num(gy),
                                    num(w.x),
                                    num(w.y),
                                    num(w.z)
                                );
                                first_point = false;
                            }
                            out += "]}";
                        }
                    }
                }
            }
        }
    }
    // Edge pans (view.rs edge_pan_path; edge_pan.py point and drawn_home mirror it): E at each zoom on the desktop being
    // left, P on the target as placed, every side, on row, lifted, far-round and curved placements.
    out += "],\"pans\":[";
    first = true;
    let sets: [[MenuPlacement; 3]; 3] = [
        [[0, 0, 100, 0], [0, 0, 100, 0], [0, 0, 100, 0]],
        [[120, 0, 150, 0], [-90, 300, 100, 0], [900, -200, 70, 0]],
        [[-300, 100, 100, 45], [1500, 0, 120, 90], [0, 450, 100, 0]],
    ];
    let side_names = ["right", "left", "up", "down"];
    for c in &canvases {
        let overview = overview_layout(&sources, c.view_w, c.view_h, 40.0);
        for set in &sets {
            let placed: Vec<Placement> = set.iter().map(placement_from_wire).collect();
            for zoom in [0.0f32, 0.2, 0.5, 0.8] {
                for side in 0u8..4 {
                    let p = EdgePan { active: true, from: 0, to: 2, side, exit: 0.3, entry: 0.7, zoom, ..EdgePan::default() };
                    let Some(path) = edge_pan_path(c, &sources, 40.0, &overview, &placed, &p) else {
                        continue;
                    };
                    out += if first { "{" } else { ",{" };
                    first = false;
                    let _ = write!(
                        out,
                        "\"canvas\":[{},{},{},{}],\"tiles\":[",
                        num(c.view_w),
                        num(c.view_h),
                        num(c.half_w),
                        num(c.half_h)
                    );
                    for (i, t) in overview.iter().enumerate() {
                        let _ = write!(
                            out,
                            "{}{},{},{},{}]",
                            if i != 0 { ",[" } else { "[" },
                            num(t.x),
                            num(t.y),
                            num(t.width),
                            num(t.height)
                        );
                    }
                    out += "],\"placements\":[";
                    for (i, w) in set.iter().enumerate() {
                        let _ = write!(
                            out,
                            "{}\"x\":{},\"y\":{},\"distance\":{},\"curvature\":{}}}",
                            if i != 0 { ",{" } else { "{" },
                            w[0],
                            w[1],
                            w[2],
                            w[3]
                        );
                    }
                    let _ = write!(
                        out,
                        "],\"side\":\"{}\",\"zoom\":{},\"exit\":{},\"entry\":{}",
                        side_names[usize::from(side)],
                        num(zoom),
                        num(p.exit),
                        num(p.entry)
                    );
                    let _ = write!(out, ",\"e\":[{},{},{}]", num(path.from.x), num(path.from.y), num(path.from.z));
                    let _ = write!(out, ",\"p\":[{},{},{}]", num(path.to.x), num(path.to.y), num(path.to.z));
                    let a = focus_direction(c, &overview, &placed, 0);
                    let b = focus_direction(c, &overview, &placed, 2);
                    let _ = write!(
                        out,
                        ",\"way\":{}}}",
                        num(quat_angle(aim_rotation(a[0], a[1]), aim_rotation(b[0], b[1])))
                    );
                }
            }
        }
    }
    println!("{out}]}}");
}
