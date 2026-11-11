//! Port of every check of ctest `roll_level` (spike/tests/roll_level_test.cpp). Level bar, renderer side: the levelling
//! roll (view.rs Placement::roll) turns a desktop about its own line of sight and keeps it facing you; desktop_tilt
//! measures a desktop's roll from the world's up (pose.rs canvas_up); the level bar (glass.rs level_bar_scale /
//! level_bar_rect / level_bar_segment) is a segmented capsule above the top edge and the corner caps (corner_cap_rect)
//! wrap the corners, a share of the desktop in tile px, so they move, scale and roll with it; their pixels
//! (render_handle_atlas) are in the theme's M3 roles and only what is hot lights; the `roll` control frame (xr-wire Roll)
//! and its malformed-frame handling.

use xr_renderer::glass::*;
use xr_renderer::glass_raster::render_handle_atlas;
use xr_renderer::glass_theme::{GLASS_THEME_DEFAULT, glass_theme_scheme};
use xr_renderer::hud::HudImage;
use xr_renderer::pose::{Quat, apply, canvas_up, quat_mul, quat_to_mat3, transpose};
use xr_renderer::view::*;
use xr_wire::control::{Op, Payload, RollSide, decode};

const PI: f32 = 3.14159265358979;

fn rad(deg: f32) -> f32 {
    deg * PI / 180.0
}
fn near(a: f32, b: f32, eps: f32) -> bool {
    (a - b).abs() <= eps
}
fn dist(a: Vec3, b: Vec3) -> f32 {
    ((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z)).sqrt()
}
fn axis_angle(x: f32, y: f32, z: f32, deg: f32) -> Quat {
    let h = rad(deg) / 2.0;
    let s = h.sin();
    Quat {
        x: x * s,
        y: y * s,
        z: z * s,
        w: h.cos(),
    }
}
fn pl(x: f32, y: f32, distance: f32, curvature: f32, roll: f32) -> Placement {
    Placement {
        x,
        y,
        distance,
        curvature,
        roll,
    }
}
fn up3(u: [f32; 3]) -> Vec3 {
    Vec3::new(u[0], u[1], u[2])
}
fn rolled(roll: f32) -> Placement {
    Placement {
        roll,
        ..Placement::default()
    }
}

const CANVAS: Canvas = Canvas::new(1920.0, 1080.0, 0.41421356, 0.23300762);
const CENTRED: FRect = FRect::new(560.0, 315.0, 800.0, 450.0);
const SIDE: FRect = FRect::new(1400.0, 300.0, 459.0, 258.0);
const UPRIGHT: Vec3 = Vec3::new(0.0, 1.0, 0.0);

#[test]
fn the_roll_turns_a_desktop_about_its_line_of_sight() {
    // The roll turns the desktop about the line through the eye and its centre: the centre stays, every point keeps its
    // distance from the centre and from the eye, and a quarter turn counter-clockwise brings its right edge to the top.
    let origin = Vec3::default();
    for t in [CENTRED, SIDE] {
        let (cx, cy) = (t.x + t.width / 2.0, t.y + t.height / 2.0);
        for base in [Placement::default(), pl(0.3, -0.2, 1.4, 0.5, 0.0)] {
            let rolled = Placement {
                roll: rad(37.0),
                ..base
            };
            let c0 = place_point(&CANVAS, &t, &base, cx, cy);
            let c1 = place_point(&CANVAS, &t, &rolled, cx, cy);
            assert!(dist(c0, c1) < 1e-5);
            for (px, py) in [(t.x, t.y), (t.x + t.width, t.y + t.height), (cx + 40.0, cy - 7.0)] {
                let a = place_point(&CANVAS, &t, &base, px, py);
                let b = place_point(&CANVAS, &t, &rolled, px, py);
                assert!(near(dist(a, c0), dist(b, c0), 1e-4));
                assert!(near(dist(a, origin), dist(b, origin), 1e-4));
                assert!(dist(a, b) > 1e-3);
            }
        }
        let quarter = rolled(rad(90.0));
        let right = place_point(&CANVAS, &t, &quarter, cx + 100.0, cy);
        let top = place_point(&CANVAS, &t, &Placement::default(), cx, cy - 100.0);
        // At its slot the unrolled desktop's point 100 px above the centre: the same as the rolled one's right point
        // (flat desktops; equal pixel scales in x and y on this canvas within float error).
        assert!(dist(right, top) < 2e-3);
    }
}

#[test]
fn tilt_is_level_at_every_slot_and_adds_the_roll() {
    // Tilt: level at every slot when the world's up is the canvas's; rolling the desktop adds its roll; the measured
    // tilt after the levelling roll is zero; straight overhead it is not measured.
    for t in [CENTRED, SIDE] {
        let level = desktop_tilt(&CANVAS, &t, &Placement::default(), &UPRIGHT);
        assert!(level.is_some_and(|l| near(l, 0.0, 1e-4)));
        for deg in [-30.0f32, -3.0, 4.5, 60.0] {
            let tilt = desktop_tilt(&CANVAS, &t, &rolled(rad(deg)), &UPRIGHT);
            assert!(tilt.is_some_and(|v| near(v, rad(deg), 2e-4)), "{deg}: {tilt:?}");
        }
    }
    let overhead = Placement {
        y: rad(89.5),
        ..Placement::default()
    };
    assert!(desktop_tilt(&CANVAS, &CENTRED, &overhead, &UPRIGHT).is_none());
    assert!(desktop_tilt(&CANVAS, &CENTRED, &Placement::default(), &Vec3::new(0.0, 0.0, 0.0)).is_none());
    assert!(desktop_tilt(&CANVAS, &FRect::default(), &Placement::default(), &UPRIGHT).is_none());
}

#[test]
fn the_worlds_up_in_the_canvas_frame() {
    // The world's up in the canvas frame: identity is upright; a recentre with the head rolled 20 degrees to the right
    // (about the driver's forward axis) leaves the canvas turned clockwise by 20 as you see it, so a desktop at its slot
    // measures -20 and a +20 levelling roll levels it; a turn round the vertical changes nothing.
    let id = Quat::default();
    let up0 = canvas_up(id, id);
    assert!(near(up0[0], 0.0, 1e-4) && near(up0[1], 1.0, 1e-4) && near(up0[2], 0.0, 1e-4));
    let yawed = canvas_up(axis_angle(0.0, 0.0, 1.0, 70.0), id);
    assert!(near(yawed[0], 0.0, 1e-4) && near(yawed[1], 1.0, 1e-4) && near(yawed[2], 0.0, 1e-4));
    let u = up3(canvas_up(axis_angle(1.0, 0.0, 0.0, 20.0), id));
    let tilt = desktop_tilt(&CANVAS, &CENTRED, &Placement::default(), &u);
    assert!(tilt.is_some_and(|t| near(t, rad(-20.0), 2e-4)));
    let after = desktop_tilt(&CANVAS, &CENTRED, &rolled(rad(20.0)), &u);
    assert!(after.is_some_and(|t| near(t, 0.0, 2e-4)));
    // A pitched reference (head 25 degrees down at the recentre) rolls a desktop orbited 80 degrees to the side.
    let pitched = up3(canvas_up(axis_angle(0.0, 1.0, 0.0, 25.0), id));
    let turned = Placement {
        x: rad(80.0),
        ..Placement::default()
    };
    let side_tilt = desktop_tilt(&CANVAS, &CENTRED, &turned, &pitched);
    assert!(side_tilt.is_some_and(|t| t.abs() > rad(15.0)));
    let ahead = desktop_tilt(&CANVAS, &CENTRED, &Placement::default(), &pitched);
    assert!(ahead.is_some_and(|t| near(t, 0.0, 2e-4)));
}

#[test]
fn levelling_roll_levels_in_the_world_both_ways() {
    // Both directions, checked against the world rather than desktop_tilt itself: after a recentre with the head tipped
    // left or right (and pitched), every desktop of a four-desktop overview measures a tilt of that side's sign, and its
    // levelling roll (-tilt, what the supervisor applies on a click) leaves its horizontal centre line level in the
    // world (driver z up), where before it was not.
    let wide = Canvas::new(1920.0, 1200.0, 0.41421356, 0.25888);
    let four = [Size::new(1920.0, 1080.0); 4];
    let tiles = overview_layout(&four, 1920.0, 1200.0, 20.0);
    assert_eq!(tiles.len(), 4);
    let gl_to_driver = transpose(&[0.0, -1.0, 0.0, 0.0, 0.0, 1.0, -1.0, 0.0, 0.0]); // pose.cpp kDriverToGl
    for head_roll in [-20.0f32, 20.0] {
        for pitch in [0.0f32, -20.0, 25.0] {
            let reference = quat_mul(axis_angle(0.0, 1.0, 0.0, pitch), axis_angle(1.0, 0.0, 0.0, head_roll));
            let up = up3(canvas_up(reference, Quat::default()));
            let height = |v: Vec3| apply(&quat_to_mat3(reference), apply(&gl_to_driver, [v.x, v.y, v.z]))[2];
            for t in &tiles {
                let tilt = desktop_tilt(&wide, t, &Placement::default(), &up);
                assert!(tilt.is_some_and(|v| if head_roll > 0.0 { v < -rad(5.0) } else { v > rad(5.0) }));
                let Some(tilt) = tilt else { continue };
                let (cx, cy) = (t.x + t.width / 2.0, t.y + t.height / 2.0);
                let levelled = rolled(-tilt);
                let flat = Placement::default();
                let before = height(place_point(&wide, t, &flat, cx + 100.0, cy))
                    - height(place_point(&wide, t, &flat, cx - 100.0, cy));
                let after = height(place_point(&wide, t, &levelled, cx + 100.0, cy))
                    - height(place_point(&wide, t, &levelled, cx - 100.0, cy));
                assert!(
                    before.abs() > 5e-3 && after.abs() < 1e-5,
                    "{head_roll} {pitch}: {before} {after}"
                );
            }
        }
    }
}

#[test]
fn easing_carries_the_roll() {
    // Easing carries the roll and lands on it; same_placement sees it; fading keeps it.
    let from = Placement::default();
    let to = rolled(rad(12.0));
    assert!(!same_placement(&from, &to));
    let mut p = from;
    for _ in 0..200 {
        p = ease_toward(&p, &to, 8.3, PLACEMENT_EASE_MS);
    }
    assert!(same_placement(&p, &to));
    assert_eq!(faded(&to, 1.0).roll, to.roll);
}

#[test]
fn a_maximized_desktop_sits_level() {
    // A maximized desktop sits level (Cody, 2026-11-03): zoomed_placements fades its roll out with the zoom, in every
    // mode, and the overview gets every roll back bit for bit. The roll itself is never touched, so there is nothing to
    // restore and nothing levelled to save: only what is drawn changes.
    let sizes = [Size::new(1920.0, 1080.0); 3];
    let overview = overview_layout(&sizes, 1920.0, 1080.0, 40.0);
    let own = [
        pl(rad(30.0), rad(12.0), 1.4, 0.5, rad(20.0)),
        pl(0.0, 0.0, 1.0, 0.0, rad(-35.0)),
        pl(rad(140.0), rad(-5.0), 1.0, 0.0, rad(7.5)),
    ];
    let drawn = |zoom: f32, position: f32, keep: bool| {
        let tiles = blended_layout(&sizes, 1920.0, 1080.0, 40.0, zoom, position);
        let options = ZoomOptions {
            keep_size: keep,
            ..ZoomOptions::default()
        };
        zoomed_placements_with(&CANVAS, &overview, &tiles, &own, zoom, position, options)
    };
    for target in 0..3usize {
        for keep in [false, true] {
            // keep_size (an anchor zoom) levels as well
            let overview_drawn = drawn(0.0, target as f32, keep);
            for i in 0..3 {
                assert!(same_placement(&overview_drawn[i], &own[i])); // exact
            }
            for zoom in [0.1f32, 0.25, 0.5, 0.75, 0.9] {
                // on the way: part of it, with the zoom
                let z = drawn(zoom, target as f32, keep);
                for i in 0..3 {
                    let want = if i == target {
                        own[i].roll * (1.0 - zoom)
                    } else {
                        own[i].roll
                    };
                    assert!(near(z[i].roll, want, 1e-5));
                }
            }
            let z = drawn(1.0, target as f32, keep);
            for i in 0..3 {
                assert_eq!(z[i].roll, if i == target { 0.0 } else { own[i].roll }); // exactly level
            }
            assert_eq!(z[target].curvature, own[target].curvature); // its shape, not a rotation
            assert!(z[target].x != own[target].x || target == 1); // and still where zooming puts it (view_test)
        }
    }

    // Four desktops at the widest spacing leave the overview's tiles no height: the one maximized still sits level.
    {
        let four = [Size::new(1920.0, 1080.0); 4];
        let squeezed = overview_layout(&four, 1920.0, 1080.0, 400.0);
        assert!(squeezed.len() == 4 && squeezed[0].height == 0.0);
        let rolled = [own[0], own[1], own[2], Placement::default()];
        let tiles = blended_layout(&four, 1920.0, 1080.0, 400.0, 1.0, 1.0);
        let z = zoomed_placements(&CANVAS, &squeezed, &tiles, &rolled, 1.0, 1.0);
        assert!(z[1].roll == 0.0 && z[0].roll == own[0].roll && z[2].roll == own[2].roll);
    }

    // Through the animator a real maximize uses. Maximizing the first desktop eases its roll to 0 with no jump...
    let mut view = ViewAnimator::default();
    let mut now = 0.0f64;
    view.set_target(false, 0, 40, now);
    now += 1000.0;
    view.advance(now);
    let frame_now = |view: &mut ViewAnimator, now: f64| {
        view.advance(now);
        drawn(view.zoom(), view.position(), false)
    };
    assert!(same_placement(&frame_now(&mut view, now)[0], &own[0]));
    view.set_target(true, 0, 40, now);
    let mut last = own[0].roll;
    while view.animating() {
        now += 8.0;
        let z = frame_now(&mut view, now);
        assert!(z[0].roll <= last && last - z[0].roll < rad(2.0));
        assert!(z[1].roll == own[1].roll && z[2].roll == own[2].roll);
        last = z[0].roll;
    }
    assert_eq!(frame_now(&mut view, now)[0].roll, 0.0);
    // ...a switch straight to another maximized desktop (every switch is a cut there): that one is level at once, and
    // the one it left has its own roll back...
    now += 100.0;
    view.set_target_with(
        true,
        1,
        40,
        now,
        TargetOptions {
            center: false,
            cut: true,
            ..TargetOptions::default()
        },
    );
    {
        let z = frame_now(&mut view, now);
        assert!(z[1].roll == 0.0 && z[0].roll == own[0].roll && z[2].roll == own[2].roll);
    }
    // ...an un-maximize gets it back with no jump, and the overview is bit for bit what it was, however often...
    now += 100.0;
    view.set_target(false, 1, 40, now);
    last = 0.0;
    while view.animating() {
        now += 8.0;
        let z = frame_now(&mut view, now);
        assert!(z[1].roll <= last && last - z[1].roll < rad(4.0));
        last = z[1].roll;
    }
    for _round in 0..2 {
        let z = frame_now(&mut view, now);
        for i in 0..3 {
            assert!(same_placement(&z[i], &own[i]));
        }
        view.set_target(true, 2, 40, now);
        now += 90.0;
        view.advance(now); // and when the maximize is cut short halfway
        let r = frame_now(&mut view, now)[2].roll;
        assert!(r != own[2].roll && r != 0.0);
        view.set_target(false, 2, 40, now);
        now += 1000.0;
        view.advance(now);
    }
}

#[test]
fn bar_size() {
    // The bar's size: LEVEL_BAR_SHARE of the desktop's width, at most LEVEL_BAR_W, at least LEVEL_BAR_MIN_W (never wider
    // than the desktop), the whole design scaling with it. All tile px: nothing depends on the desktop's distance.
    let usual = FRect::new(40.0, 435.0, 586.67, 330.0);
    let wide = FRect::new(0.0, 0.0, 4000.0, 2000.0);
    let four = FRect::new(20.0, 450.0, 440.0, 247.5);
    let small = FRect::new(10.0, 40.0, 120.0, 60.0);
    let tiny = FRect::new(5.0, 5.0, 40.0, 30.0);
    assert!(
        near(level_bar_scale(&wide), 1.0, 1e-4)
            && near(level_bar_scale(&FRect::new(0.0, 0.0, 592.0, 333.0)), 1.0, 1e-4)
    );
    assert!(near(
        level_bar_scale(&usual),
        LEVEL_BAR_SHARE * 586.67 / LEVEL_BAR_W,
        1e-3
    ));
    assert!(near(level_bar_scale(&four), LEVEL_BAR_MIN_W / LEVEL_BAR_W, 1e-4)); // 110 px asked: the floor
    assert!(
        near(level_bar_scale(&small), 120.0 / LEVEL_BAR_W, 1e-4)
            && near(level_bar_scale(&tiny), 40.0 / LEVEL_BAR_W, 1e-4)
    );
    let empty = FRect::default();
    assert!(
        level_bar_scale(&empty) == 0.0
            && level_bar_rect(&empty).width == 0.0
            && level_bar_segment(&empty, 0.0, 0.0) == LevelSegment::None
    );
    let flat = FRect::new(0.0, 0.0, 100.0, 0.0);
    assert!(level_bar_scale(&flat) == 0.0 && level_bar_rect(&flat).width == 0.0);
    assert!(corner_cap_rect(&empty, 0).width == 0.0 && corner_cap_rect(&usual, 4).width == 0.0);
}

#[test]
fn bar_and_caps_place_and_hit() {
    // Drawn centred above the top edge, LEVEL_BAR_GAP off it, the same shape at every size, never wider than the desktop
    // and never on it. Hit: the drawn bar padded by LEVEL_BAR_PAD, split at its thirds: left turns counter-clockwise, the
    // middle levels, right turns clockwise; the padding goes with the segment it borders; never in the band
    // LEVEL_EDGE_CLEAR wide along the edge (the selected desktop's border, the edge's resize zone), let alone on the
    // desktop.
    use LevelSegment::{Ccw, Cw, Level, None as Off};
    for t in [
        FRect::new(40.0, 435.0, 586.67, 330.0),
        FRect::new(0.0, 0.0, 4000.0, 2000.0),
        FRect::new(10.0, 40.0, 120.0, 60.0),
        FRect::new(1400.0, 300.0, 459.0, 258.0),
        FRect::new(-200.5, 410.25, 812.0, 457.0),
        FRect::new(5.0, 5.0, 40.0, 30.0),
    ] {
        let s = level_bar_scale(&t);
        let pad = LEVEL_BAR_PAD * s;
        let r = level_bar_rect(&t);
        let seg = |x: f32, y: f32| level_bar_segment(&t, x, y);
        assert!(near(r.width, LEVEL_BAR_W * s, 1e-3) && near(r.height, LEVEL_BAR_H * s, 1e-3));
        assert!(
            near(r.x + r.width / 2.0, t.x + t.width / 2.0, 1e-2) && near(r.y + r.height, t.y - LEVEL_BAR_GAP * s, 1e-3)
        );
        assert!(r.width <= t.width + 1e-3 && r.y + r.height < t.y);
        let (third, mid_y, eps) = (r.width / 3.0, r.y + r.height / 2.0, 0.05 * s);
        assert_eq!(seg(r.x + third / 2.0, mid_y), Ccw);
        assert!(seg(r.x + third + eps, mid_y) == Level && seg(r.x + third - eps, mid_y) == Ccw);
        assert_eq!(seg(r.x + r.width / 2.0, mid_y), Level);
        assert!(seg(r.x + 2.0 * third - eps, mid_y) == Level && seg(r.x + 2.0 * third + eps, mid_y) == Cw);
        assert_eq!(seg(r.x + r.width - third / 2.0, mid_y), Cw);
        assert!(seg(r.x - pad + eps, mid_y) == Ccw && seg(r.x - pad - eps, mid_y) == Off);
        assert!(seg(r.x + r.width + pad - eps, mid_y) == Cw && seg(r.x + r.width + pad + eps, mid_y) == Off);
        assert!(seg(r.x + r.width / 2.0, r.y - pad + eps) == Level && seg(r.x + r.width / 2.0, r.y - pad - eps) == Off);
        let bottom = r.y + r.height + pad;
        assert!(seg(r.x + r.width / 2.0, bottom - eps) == Level && seg(r.x + r.width / 2.0, bottom + eps) == Off);
        assert!(bottom <= t.y - LEVEL_EDGE_CLEAR * s + 1e-3); // the band along the edge stays the edge's
        for x in [r.x, r.x + r.width / 2.0, r.x + r.width] {
            assert_eq!(seg(x, t.y + 1.0), Off);
        }
        // The caps: a box round each corner holding every state, the cap past the corner on both axes.
        for c in 0..4u32 {
            let (right, bottom_corner) = (c == 1 || c == 3, c >= 2);
            let b = corner_cap_rect(&t, c);
            let cx = if right { t.x + t.width } else { t.x };
            let cy = if bottom_corner { t.y + t.height } else { t.y };
            assert!(near(b.width, (CAP_OUTER + CAP_INNER) * s, 1e-3) && near(b.height, b.width, 1e-3));
            let out_x = if right { b.x + b.width - cx } else { cx - b.x };
            let out_y = if bottom_corner { b.y + b.height - cy } else { cy - b.y };
            assert!(near(out_x, CAP_OUTER * s, 1e-2) && near(out_y, CAP_OUTER * s, 1e-2));
            let in_x = if right { cx - b.x } else { b.x + b.width - cx };
            let in_y = if bottom_corner { cy - b.y } else { b.y + b.height - cy };
            assert!(near(in_x, CAP_INNER * s, 1e-2) && near(in_y, CAP_INNER * s, 1e-2));
        }
    }
}

#[test]
fn bar_and_caps_move_and_scale_with_their_desktop() {
    // It moves and scales with its desktop: on the desktop's own surface, a resize (distance), a move (orbit), curvature
    // and the levelling roll all carry the bar and caps along, so on that surface they keep their share of the desktop's
    // width (measured in space: how big a span looks falls off toward the view's edge) and the bar its place above the
    // top edge's middle, turned with it.
    let angle = |a: Vec3, b: Vec3| {
        let dot = a.x * b.x + a.y * b.y + a.z * b.z;
        let n = ((a.x * a.x + a.y * a.y + a.z * a.z) * (b.x * b.x + b.y * b.y + b.z * b.z)).sqrt();
        (dot / n).clamp(-1.0, 1.0).acos()
    };
    for t in [CENTRED, SIDE] {
        let r = level_bar_rect(&t);
        let cap = corner_cap_rect(&t, 3);
        let (want, want_cap, mid) = (r.width / t.width, cap.width / t.width, t.x + t.width / 2.0);
        for d in [0.5f32, 1.0, 2.0] {
            for base in [
                Placement::default(),
                pl(0.25, -0.15, 1.0, 0.0, 0.0),
                pl(0.0, 0.0, 1.0, 0.6, 0.0),
                pl(-0.4, 0.2, 1.0, 0.3, rad(14.0)),
            ] {
                let p = Placement { distance: d, ..base };
                let pp = |x: f32, y: f32| place_point(&CANVAS, &t, &p, x, y);
                let across = dist(pp(t.x, t.y), pp(t.x + t.width, t.y));
                let got = dist(pp(r.x, r.y), pp(r.x + r.width, r.y)) / across;
                let got_cap = dist(pp(cap.x, cap.y), pp(cap.x + cap.width, cap.y)) / across;
                assert!(
                    (got - want).abs() <= 0.05 * want,
                    "bar share {got} want {want} (d {d} x {})",
                    p.x
                );
                assert!((got_cap - want_cap).abs() <= 0.05 * want_cap);
                // ...and it stays beyond the top edge from the desktop's centre, however it is turned.
                let centre = pp(mid, t.y + t.height / 2.0);
                let top = pp(mid, t.y);
                assert!(angle(centre, pp(mid, r.y + r.height / 2.0)) > angle(centre, top));
            }
        }
    }
}

#[derive(Clone, Copy)]
struct Cell {
    x: u32,
    y: u32,
    w: u32,
    h: u32,
}

fn cell_of(atlas: &HudImage, b: &AtlasBox) -> Cell {
    let (aw, ah) = (atlas.width as f32, atlas.height as f32);
    Cell {
        x: (b.u0 * aw).round() as u32,
        y: (b.v0 * ah).round() as u32,
        w: ((b.u1 - b.u0) * aw).round() as u32,
        h: ((b.v1 - b.v0) * ah).round() as u32,
    }
}
fn at(atlas: &HudImage, c: &Cell, x: u32, y: u32) -> [u8; 4] {
    let i = ((c.y + y) as usize * atlas.width as usize + (c.x + x) as usize) * 4;
    atlas.rgba[i..i + 4].try_into().unwrap()
}
fn alpha(atlas: &HudImage, x: u32, y: u32) -> u8 {
    atlas.rgba[(y as usize * atlas.width as usize + x as usize) * 4 + 3]
}
fn gutter_clear(atlas: &HudImage, c: &Cell) {
    for x in 0..c.w {
        assert!(alpha(atlas, c.x + x, c.y - 1) == 0 && alpha(atlas, c.x + x, c.y + c.h) == 0);
    }
    for y in 0..c.h {
        assert!(alpha(atlas, c.x - 1, c.y + y) == 0 && alpha(atlas, c.x + c.w, c.y + y) == 0);
    }
}

#[test]
fn handle_atlas_pixels() {
    // Its pixels: one image per theme colour. Bilinear filtering stays in a cell (a gutter of clear pixels round each),
    // every pixel is premultiplied and under the OLED cap, the same colour gives the same image and another colour
    // another.
    use LevelSegment::{Ccw, Cw, Level, None as Off};
    for accent in [
        [1.0f32, 1.0, 1.0],
        [0.48, 0.64, 0.97],
        GLASS_THEME_DEFAULT,
        [0.0, 0.0, 0.0],
        [0.9, 0.2, 0.1],
    ] {
        let m = glass_theme_scheme(accent);
        let atlas = render_handle_atlas(accent, None);
        assert!(atlas.rgba == render_handle_atlas(accent, None).rgba);
        assert!(
            atlas.width > 0 && atlas.height > 0 && atlas.rgba.len() == atlas.width as usize * atlas.height as usize * 4
        );
        for p in atlas.rgba.chunks_exact(4) {
            assert!(p[0] <= p[3] && p[1] <= p[3] && p[2] <= p[3]);
            assert!(p[0].max(p[1]).max(p[2]) <= 192); // 191/255, and a rounding where two anti-aliased edges meet
        }
        let idle = cell_of(&atlas, &level_bar_box(Off, false));
        assert!(idle.w == LEVEL_BAR_W as u32 * HANDLE_ATLAS_SCALE && idle.h == LEVEL_BAR_H as u32 * HANDLE_ATLAS_SCALE);
        gutter_clear(&atlas, &idle);
        // Only the hot segment lights: outside its third every pixel is the idle bar's. Hovered and dragged differ, in
        // the lit segment only. A third of the bar's own width is the seam: the hot capsule stays inside it.
        for seg in [Ccw, Level, Cw] {
            for active in [false, true] {
                let hot = cell_of(&atlas, &level_bar_box(seg, active));
                gutter_clear(&atlas, &hot);
                assert!(hot.w == idle.w && hot.h == idle.h);
                let k = seg as i32 as u32;
                let (lo, hi) = (k * idle.w / 3, (k + 1) * idle.w / 3);
                let (mut lit, mut elsewhere) = (0, 0);
                for y in 0..idle.h {
                    for x in 0..idle.w {
                        let differs = at(&atlas, &hot, x, y) != at(&atlas, &idle, x, y);
                        if x >= lo && x < hi {
                            lit += differs as i32;
                        } else {
                            elsewhere += differs as i32;
                        }
                    }
                }
                assert!(
                    lit > (idle.w * idle.h) as i32 / 12 && elsewhere == 0,
                    "{seg:?} {active}: {lit} {elsewhere}"
                );
            }
        }
        // The bar's silhouette: a capsule, the same in every state; its ends round, its middle solid.
        assert!(
            at(&atlas, &idle, 0, 0)[3] == 0
                && at(&atlas, &idle, idle.w - 1, idle.h - 1)[3] == 0
                && at(&atlas, &idle, idle.w / 2, 1)[3] == 255
        );
        for corner in 0..4u32 {
            let (right, down) = (corner == 1 || corner == 3, corner == 2 || corner == 3);
            let quiet = cell_of(&atlas, &corner_cap_box(corner, false, false));
            let lit = cell_of(&atlas, &corner_cap_box(corner, true, false));
            let held = cell_of(&atlas, &corner_cap_box(corner, true, true));
            gutter_clear(&atlas, &quiet);
            gutter_clear(&atlas, &lit);
            gutter_clear(&atlas, &held);
            let (k, n) = (HANDLE_ATLAS_SCALE, quiet.w);
            let inner = CAP_INNER as u32 * k;
            // A point of the cell by its distance past the corner along x and y (tile px): the same L in every corner.
            let spot = |out_x: f32, out_y: f32| {
                let ax = ((inner as f32 + out_x * k as f32) + 0.5) as u32;
                let ay = ((inner as f32 + out_y * k as f32) + 0.5) as u32;
                (if right { ax } else { n - 1 - ax }, if down { ay } else { n - 1 - ay })
            };
            let (lx, ly) = spot(CAP_GAP, 0.0); // on its side leg, by the corner
            let q = at(&atlas, &quiet, lx, ly);
            assert!(q[3] == 255 && near(q[0] as f32 / 255.0, m.primary[0], 2.0 / 255.0));
            let l = at(&atlas, &lit, lx, ly);
            assert!(l[3] == 255 && near(l[0] as f32 / 255.0, m.primary_bright[0], 2.0 / 255.0));
            let (tx, ty) = spot(CAP_GAP, CAP_GAP - CAP_LEG_HOT + 3.0); // near the end of a lit leg, past a quiet one's
            assert!(
                at(&atlas, &quiet, tx, ty)[3] == 0
                    && at(&atlas, &lit, tx, ty)[3] == 255
                    && at(&atlas, &held, tx, ty)[3] == 255
            );
            let (ix, iy) = spot(-3.0, -3.0); // on the desktop's side of the corner: nothing, whatever the state
            assert!(at(&atlas, &quiet, ix, iy)[3] == 0 && at(&atlas, &lit, ix, iy)[3] == 0);
        }
    }
    let a = render_handle_atlas([0.9, 0.2, 0.1], None);
    let b = render_handle_atlas([0.1, 0.5, 0.9], None);
    assert!(a.rgba != b.rgba); // another theme, another bar
}

fn frame(payload: &str) -> String {
    format!(r#"{{"schema":1,"request_id":7,"run_id":"run","session":"s","op":"roll","payload":{payload}}}"#) + "\n"
}

#[test]
fn roll_frame() {
    // The `roll` frame: four rolls and icons ("bar" on every desktop in the overview), a hover with its segment and
    // active; anything else is dropped without stopping.
    let good = decode(
        frame(r#"{"roll":[-300,0,9000,-9000],"icons":["bar","none","bar","bar"],"hover":0,"side":"cw","active":true}"#)
            .as_bytes(),
    )
    .expect("a good roll frame");
    assert_eq!(good.op, Op::Roll);
    let Payload::Roll(r) = good.payload else {
        panic!("not a roll payload")
    };
    assert_eq!(r.roll, [-300, 0, 9000, -9000]);
    assert!(r.bars == [true, false, true, true] && r.hover == 0 && r.side == RollSide::Cw && r.active);
    let none = decode(
        frame(r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false}"#)
            .as_bytes(),
    )
    .expect("a quiet roll frame");
    let Payload::Roll(r) = none.payload else {
        panic!("not a roll payload")
    };
    assert_eq!(r.side, RollSide::None);
    for (name, side) in [("ccw", RollSide::Ccw), ("level", RollSide::Level), ("cw", RollSide::Cw)] {
        let payload = format!(
            r#"{{"roll":[0,0,0,0],"icons":["bar","bar","bar","bar"],"hover":3,"side":"{name}","active":false}}"#
        );
        let one = decode(frame(&payload).as_bytes()).expect("a roll frame");
        let Payload::Roll(r) = one.payload else {
            panic!("not a roll payload")
        };
        assert!(r.bars == [true; 4] && r.hover == 3 && r.side == side);
    }
    for bad in [
        r#"{"roll":[0,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false}"#,
        r#"{"roll":[0,0,0,9001],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false}"#,
        r#"{"roll":[0,0,0,18446744073709551615],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false}"#,
        r#"{"roll":[0,0.5,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","left","none","none"],"hover":-1,"side":"none","active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","both","none","none"],"hover":-1,"side":"none","active":false}"#, // the brackets' old value
        r#"{"roll":[0,0,0,0],"icons":["ccw","none","none","none"],"hover":-1,"side":"none","active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":4,"side":"cw","active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-2,"side":"none","active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":0}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none"}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-1,"active":false}"#, // no side (the old 4-key frame)
        r#"{"roll":[0,0,0,0],"icons":["bar","bar","bar","bar"],"hover":0,"side":"none","active":false}"#, // hover without a side
        r#"{"roll":[0,0,0,0],"icons":["bar","bar","bar","bar"],"hover":-1,"side":"level","active":false}"#, // a side without a hover
        r#"{"roll":[0,0,0,0],"icons":["bar","bar","bar","bar"],"hover":0,"side":"up","active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["bar","bar","bar","bar"],"hover":0,"side":1,"active":false}"#,
        r#"{"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false,"x":1}"#,
    ] {
        let e = decode(frame(bad).as_bytes()).expect_err(bad);
        assert!(e.code == "invalid_roll" && e.ignorable(), "{bad}: {e:?}");
    }
}
