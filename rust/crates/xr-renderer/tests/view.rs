//! `spike/tests/view_test.cpp`, check for check, with the same tolerances; then byte and bit checks against the C++
//! build for what that file does not cover (overview_geometry_json, the pointer's marks and ghost, glass, tilt).

use std::f32::consts::PI;

use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::view::*;

fn near(a: f32, b: f32, eps: f32) -> bool {
    (a - b).abs() <= eps
}

fn lerp_check(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

fn same(r: &FRect, e: &FRect) -> bool {
    near(r.x, e.x, 1e-3) && near(r.y, e.y, 1e-3) && near(r.width, e.width, 1e-3) && near(r.height, e.height, 1e-3)
}

/// `std::remainder` on floats.
fn remainder(x: f32, y: f32) -> f32 {
    let (x, y) = (f64::from(x), f64::from(y));
    (x - (x / y).round_ties_even() * y) as f32
}

fn sz(w: f32, h: f32) -> Size {
    Size::new(w, h)
}

fn rect(x: f32, y: f32, w: f32, h: f32) -> FRect {
    FRect::new(x, y, w, h)
}

/// A C++ four-field `Placement{x, y, distance, curvature}` (roll 0).
fn p4(x: f32, y: f32, distance: f32, curvature: f32) -> Placement {
    Placement::new(x, y, distance, curvature, 0.0)
}

fn dflt() -> Placement {
    Placement::default()
}

fn cut() -> TargetOptions {
    TargetOptions { cut: true, ..TargetOptions::default() }
}

fn landing() -> TargetOptions {
    TargetOptions { pan: true, ..TargetOptions::default() }
}

const EQ: [Size; 3] = [Size::new(1920.0, 1080.0), Size::new(1920.0, 1080.0), Size::new(1920.0, 1080.0)];

// ---------------------------------------------------------------------------------------------------------------------
// Ctrl+Alt+R's tiles (TileEase): the same layouts the renderer draws, through a remove, a new aspect and an add.

fn is_target(t: Tiles<'_>, v: &[FRect]) -> bool {
    t.is_target() && std::ptr::eq(t.as_slice(), v)
}

struct EaseFixture {
    four: [Size; 4],
    three: [Size; 3],
    even: [Size; 3],
    two: [Size; 2],
    from: Vec<FRect>,
    mid: Vec<FRect>,
    to: Vec<FRect>,
}

fn ease_fixture() -> EaseFixture {
    let four = [sz(2560.0, 1080.0), sz(1920.0, 1080.0), sz(1920.0, 1080.0), sz(2560.0, 1080.0)];
    let three = [sz(2560.0, 1080.0), sz(1920.0, 1080.0), sz(1920.0, 1080.0)];
    let even = [sz(1920.0, 1080.0), sz(1920.0, 1080.0), sz(1920.0, 1080.0)];
    let two = [sz(1920.0, 1080.0), sz(1920.0, 1080.0)];
    let from = overview_layout(&four, 1920.0, 1200.0, 40.0);
    let mid = overview_layout(&three, 1920.0, 1200.0, 40.0);
    let to = overview_layout(&even, 1920.0, 1200.0, 40.0);
    EaseFixture { four, three, even, two, from, mid, to }
}

#[test]
fn tile_ease_unarmed_and_expired() {
    let f = ease_fixture();
    {
        // Unarmed: the layout itself, the very same slice, whatever changes (a pill's Add / Remove / Ultrawide jump as before).
        let mut e = TileEase::new();
        assert!(is_target(e.tiles(&f.three, &f.mid, true, 1000.0), &f.mid));
        assert!(is_target(e.tiles(&f.four, &f.from, true, 1016.0), &f.from));
        assert!(!e.active() && !e.armed(1016.0));
    }
    {
        // Armed, nothing changed yet: the layout as it is; a change after the arm has run out jumps.
        let mut e = TileEase::new();
        e.arm(&f.three, &f.mid, 1000.0);
        assert!(e.armed(1000.0 + f64::from(TILE_EASE_ARM_MS)) && !e.active());
        assert!(is_target(e.tiles(&f.three, &f.mid, true, 1100.0), &f.mid));
        assert!(is_target(e.tiles(&f.even, &f.to, true, 1000.0 + f64::from(TILE_EASE_ARM_MS) + 1.0), &f.to));
        assert!(!e.active());
    }
}

#[test]
fn tile_ease_remove() {
    // Remove the last of four: the three left go from their tiles to the wider ones, along one smootherstep.
    let f = ease_fixture();
    let (from, mid) = (&f.from, &f.mid);
    let mut e = TileEase::new();
    e.arm(&f.four, from, 1000.0);
    e.reindex(&[0, 1, 2]);
    let start = e.tiles(&f.three, mid, true, 2000.0); // the first frame after the change: not moved at all
    assert!(start.len() == 3 && !start.is_target());
    let start = start.to_vec();
    assert!(e.active());
    for i in 0..3 {
        assert!(same(&start[i], &from[i]));
    }
    let half = e.tiles(&f.three, mid, true, 2000.0 + f64::from(TILE_EASE_MS) / 2.0).to_vec(); // t 0.5: halfway on the curve
    for i in 0..3 {
        assert!(near(half[i].x, (from[i].x + mid[i].x) / 2.0, 1e-2) && near(half[i].width, (from[i].width + mid[i].width) / 2.0, 1e-2));
        assert!(near(half[i].y, (from[i].y + mid[i].y) / 2.0, 1e-2) && near(half[i].height, (from[i].height + mid[i].height) / 2.0, 1e-2));
    }
    let early = e.tiles(&f.three, mid, true, 2000.0 + f64::from(TILE_EASE_MS) * 0.1).to_vec(); // eased in: still slow at 10%
    assert!((early[0].width - from[0].width).abs() < 0.02 * (mid[0].width - from[0].width).abs());
    assert!(is_target(e.tiles(&f.three, mid, true, 2000.0 + f64::from(TILE_EASE_MS)), mid)); // lands exactly, and stops asking for frames
    assert!(!e.active());
}

#[test]
fn tile_ease_second_change_and_add() {
    let f = ease_fixture();
    {
        // A second change on the way (a new frame's aspect) starts from where the tiles are, never from a jump.
        let mut e = TileEase::new();
        e.arm(&f.four, &f.from, 1000.0);
        e.reindex(&[0, 1, 2]);
        e.tiles(&f.three, &f.mid, true, 2000.0);
        let at = e.tiles(&f.three, &f.mid, true, 2200.0).to_vec();
        let again = e.tiles(&f.even, &f.to, true, 2200.0).to_vec();
        for i in 0..3 {
            assert!(same(&again[i], &at[i]));
        }
        assert!(e.active());
        assert!(is_target(e.tiles(&f.even, &f.to, true, 2200.0 + f64::from(TILE_EASE_MS)), &f.to));
    }
    {
        // Add: a new desktop grows out of the middle of its slot; the others go from their tiles, by name, not by index.
        let before = overview_layout(&f.two, 1920.0, 1200.0, 40.0);
        let to = &f.to;
        let mut e = TileEase::new();
        e.arm(&f.two, &before, 1000.0);
        e.reindex(&[-1, 0, 1]); // XR-1 is new: XR-2 and XR-3 were indices 0 and 1
        let start = e.tiles(&f.even, to, true, 1100.0).to_vec();
        assert!(same(&start[1], &before[0]) && same(&start[2], &before[1]));
        assert!(start[0].width > 0.0 && near(start[0].width, to[0].width * TILE_EASE_BORN_SCALE, 1e-2));
        assert!(near(start[0].x + start[0].width / 2.0, to[0].x + to[0].width / 2.0, 1e-2));
        assert!(near(start[0].y + start[0].height / 2.0, to[0].y + to[0].height / 2.0, 1e-2));
    }
}

#[test]
fn tile_ease_rearm_and_zoom() {
    let f = ease_fixture();
    {
        // A frame re-arming mid-way keeps following the tiles (the removal's frame, then the finished change's).
        let mut e = TileEase::new();
        e.arm(&f.four, &f.from, 1000.0);
        e.reindex(&[0, 1, 2]);
        e.tiles(&f.three, &f.mid, true, 1000.0);
        let at = e.tiles(&f.three, &f.mid, true, 1200.0).to_vec();
        e.arm(&f.three, &f.mid, 1200.0);
        e.reindex(&[0, 1, 2]);
        let on = e.tiles(&f.three, &f.mid, true, 1200.0).to_vec();
        for i in 0..3 {
            assert!(same(&on[i], &at[i]));
        }
        // The same desktops again: the ease goes on on its own clock, it does not start over (no stutter mid-glide).
        assert!(e.active());
        assert!(is_target(e.tiles(&f.three, &f.mid, true, 1000.0 + f64::from(TILE_EASE_MS)), &f.mid));
    }
    {
        // Overview only: a zoom drops the ease and the arm.
        let mut e = TileEase::new();
        e.arm(&f.four, &f.from, 1000.0);
        e.reindex(&[0, 1, 2]);
        e.tiles(&f.three, &f.mid, true, 1000.0);
        assert!(is_target(e.tiles(&f.three, &f.mid, false, 1100.0), &f.mid));
        assert!(!e.active() && !e.armed(1100.0));
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Every desktop of every run maximizes: the view selects, zooms on, highlights and aims at the one the supervisor names, by
// its index in the run's desktops. Until 2026-11-03 the animator stopped at the third, so with four desktops the glasses
// maximized XR-3 while the keyboard and mouse went to XR-4.

const LAST: u32 = MAX_DESKTOPS as u32 - 1;

#[test]
fn every_desktop_maximizes() {
    let canvas = Canvas::new(1920.0, 1200.0, 0.8, 0.5);
    for n in 1..=MAX_DESKTOPS as u32 {
        let sizes = vec![sz(1920.0, 1080.0); n as usize];
        let overview = overview_layout(&sizes, 1920.0, 1200.0, 40.0);
        for i in 0..n {
            let iu = i as usize;
            let mut v = ViewAnimator::with_duration(200.0);
            v.set_target(false, i, 40, 0.0);
            v.advance(1000.0); // selected in the overview: the highlight is on it
            assert!(v.selected() == i && v.focus() == i && v.zoom() == 0.0);
            v.set_target(true, i, 40, 1000.0);
            v.advance(2000.0); // Ctrl+Alt+Up
            assert!(!v.animating() && v.zoom() == 1.0 && v.selected() == i && v.focus() == i && v.position() == i as f32);
            let tiles = blended_layout(&sizes, 1920.0, 1200.0, 40.0, v.zoom(), v.position()); // it fills the view
            assert!(tiles.len() == n as usize && near(tiles[iu].x + tiles[iu].width / 2.0, 960.0, 0.5) && near(tiles[iu].width, 1920.0, 0.5));
            let placed = zoomed_placements(&canvas, &overview, &tiles, &[], v.zoom(), v.position());
            assert!(placed.len() == n as usize && near(placed[iu].distance, 1.0, 1e-4));
            assert!(zoom_weight(v.zoom(), v.position(), iu) == 1.0 && focus_weight(&v) == 1.0);
            // Follow and locked aim at it, and it is drawn on top of anything dragged nearer.
            let aim = focus_direction(&canvas, &overview, &[], v.focus() as usize);
            let x = (2.0 * (overview[iu].x + overview[iu].width / 2.0) / 1920.0 - 1.0) * canvas.half_w;
            assert!(near(aim[0], x.atan2(1.0), 1e-5) && near(aim[1], 0.0, 1e-5));
            let order = zoom_draw_order(draw_order(&canvas, &tiles, &placed), v.zoom(), v.focus() as usize);
            assert_eq!(*order.last().unwrap(), iu);
        }
    }
}

#[test]
fn every_desktop_switches_and_cuts() {
    // Maximized, Ctrl+Alt+Left / Right's animated switch along every desktop and back, each landing where it was sent.
    let mut v = ViewAnimator::with_duration(200.0);
    v.set_target(true, 0, 40, 0.0);
    v.advance(1000.0);
    let mut t = 1000.0;
    let walk = |v: &mut ViewAnimator, t: &mut f64, to: u32| {
        v.set_target(true, to, 40, *t);
        assert!(v.switching() && v.selected() == to);
        *t += 2000.0;
        v.advance(*t);
        assert!(!v.switching() && v.zoom() == 1.0 && v.focus() == to && v.position() == to as f32);
        let turn = v.take_landed_turn();
        assert!(turn.is_some_and(|turn| turn[1] == to));
    };
    for to in 1..=LAST {
        walk(&mut v, &mut t, to);
    }
    for to in (0..LAST).rev() {
        walk(&mut v, &mut t, to);
    }
    // Sent on to the last one while zooming out of the first (a retarget), then while turning (it queues).
    v.set_target(true, 1, 40, t);
    v.advance(t + 50.0);
    assert!(v.switching() && v.zoom() > 0.0);
    v.set_target(true, LAST, 40, t + 50.0);
    t += 2000.0;
    v.advance(t);
    assert!(!v.switching() && v.focus() == LAST && v.zoom() == 1.0);
    v.set_target(true, 0, 40, t);
    v.advance(t + 300.0);
    assert!(v.switching() && v.zoom() == 0.0);
    v.set_target(true, LAST, 40, t + 300.0);
    t += 4000.0;
    v.advance(t);
    assert!(!v.switching() && v.focus() == LAST && v.zoom() == 1.0);
    // The cut (a maximized cursor switch or key: follow and locked): straight there, to the last one and from it.
    v.set_target_with(true, 0, 40, t, cut());
    assert!(!v.switching() && v.focus() == 0);
    v.set_target_with(true, LAST, 40, t, cut());
    assert!(!v.switching() && v.focus() == LAST && v.zoom() == 1.0);
}

#[test]
fn every_desktop_selected_last_removed() {
    // The selected last desktop removed: its `desktops` frame comes just before the `view` frame that moves the selection
    // (view.py), so for a moment the animator names a desktop that is gone. Nothing it feeds goes out of bounds.
    let canvas = Canvas::new(1920.0, 1200.0, 0.8, 0.5);
    let mut v = ViewAnimator::with_duration(200.0);
    v.set_target(true, LAST, 40, 0.0);
    v.advance(1000.0);
    let fewer = vec![sz(1920.0, 1080.0); LAST as usize];
    let overview = overview_layout(&fewer, 1920.0, 1200.0, 40.0);
    let tiles = blended_layout(&fewer, 1920.0, 1200.0, 40.0, v.zoom(), v.position());
    assert!(tiles.len() == fewer.len() && tiles.iter().all(|r| r.x.is_finite() && r.width > 0.0));
    assert!(zoomed_placements(&canvas, &overview, &tiles, &[], v.zoom(), v.position()).len() == fewer.len());
    assert!(focus_direction(&canvas, &overview, &[], v.focus() as usize) == [0.0, 0.0]);
    let order = vec![0usize, 1, 2];
    assert!(zoom_draw_order(order.clone(), v.zoom(), v.focus() as usize) == order);
    v.set_target_with(true, LAST - 1, 40, 1000.0, cut());
    v.advance(2000.0);
    assert!(v.selected() == LAST - 1 && v.focus() == LAST - 1 && v.zoom() == 1.0);
}

// ---------------------------------------------------------------------------------------------------------------------
// The hand's pan past a maximized desktop's edge (view.hpp EdgePan, tools/native_spike/edge_pan.py).

fn pan_frame(peek: f32, cross: f32, turn: f32, zoom: f32, from: u32, to: u32) -> EdgePan {
    EdgePan { active: true, from, to, side: 0, exit: 0.3, entry: 0.3, peek, cross, turn, way: PI, zoom }
}

/// pan_frame with the C++ defaults past `turn`: zoom 0.2, from 0, to 1.
fn frame(peek: f32, cross: f32, turn: f32) -> EdgePan {
    pan_frame(peek, cross, turn, 0.2, 0, 1)
}

fn maximized_on(desktop: u32) -> ViewAnimator {
    let mut v = ViewAnimator::default();
    v.set_target(false, desktop, 40, 0.0);
    v.set_target(true, desktop, 40, 0.0);
    v.advance(1000.0);
    assert!(v.zoom() == 1.0 && !v.animating() && v.focus() == desktop);
    v
}

/// `for (double t = from; t < to; t += step) v.advance(t);`
fn run(v: &mut ViewAnimator, from: f64, to: f64, step: f64) {
    let mut t = from;
    while t < to {
        v.advance(t);
        t += step;
    }
}

#[test]
fn edge_pan_hand_draws_it() {
    // The hand draws it: the zoom falls with the peek and comes back with it, the desktop never changes.
    let mut v = maximized_on(0);
    v.set_pan(&frame(1.0, 0.0, 0.0), 1000.0);
    assert!(v.pan_active() && v.switching() && v.animating());
    run(&mut v, 1000.0, 1600.0, 8.0);
    assert!(near(v.zoom(), 0.2, 1e-3) && v.focus() == 0 && v.selected() == 0);
    v.set_pan(&frame(0.5, 0.0, 0.1), 1600.0);
    run(&mut v, 1600.0, 2100.0, 8.0);
    assert!(near(v.zoom(), lerp_check(1.0, 0.2, smoothstep(0.5)), 1e-2));
    assert!(near(v.pan_turn(), 0.1, 1e-2));
    v.set_pan(&EdgePan::default(), 2100.0); // pulled back
    let mut last = f64::from(v.zoom());
    let mut t = 2100.0;
    while t < 2800.0 {
        v.advance(t);
        assert!(f64::from(v.zoom()) >= last - 1e-5);
        last = f64::from(v.zoom());
        t += 8.0;
    }
    assert!(v.zoom() == 1.0 && !v.pan_active() && !v.switching() && !v.animating() && v.focus() == 0);
    assert!(v.take_landed_turns().is_empty());
}

#[test]
fn edge_pan_zoom_eased() {
    // The hand takes the zoom only as far as the pair needs; a jump of it reaches the screen eased, at most the whole zoom
    // in VIEW_EASE_MS.
    let mut v = maximized_on(0);
    v.set_pan(&frame(1.0, 1.0, 0.3), 1000.0);
    let mut was = v.zoom();
    let mut t = 1008.0;
    while t < 1100.0 {
        v.advance(t);
        assert!(was - v.zoom() <= 8.0 / VIEW_EASE_MS + 1e-4);
        was = v.zoom();
        t += 8.0;
    }
    assert!(v.zoom() > 0.4);
    run(&mut v, 1100.0, 1800.0, 8.0);
    assert!(near(v.zoom(), 0.2, 1e-3) && near(v.pan_cross(), 1.0, 1e-3) && near(v.pan_turn(), 0.3, 1e-3));
}

#[test]
fn edge_pan_landing_is_one_motion() {
    // The landing is the rest of one motion: at once, all together, from what the hand left, and on the target at
    // PAN_SETTLE_MS.
    let mut v = maximized_on(0);
    let mut way = frame(1.0, 1.0, 0.3);
    way.way = 0.9;
    v.set_pan(&way, 1000.0);
    run(&mut v, 1000.0, 1800.0, 8.0);
    let from = v.zoom();
    v.set_target_with(true, 1, 40, 1800.0, landing());
    assert!(v.pan_landing() && v.selected() == 1 && v.switching() && v.focus() == 1);
    // the zoom is the target's now; the other goes on down from there
    assert!(v.zoom() == 0.0 && near(v.left_zoom(), from, 1e-5) && v.left() == 0);
    let (mut z, mut left, mut turned) = (0.0f32, from, v.pan_turn());
    let mut landed = 0.0f64;
    let mut t = 1804.0;
    while t < 2600.0 {
        v.advance(t);
        if v.pan_landing() {
            assert!(v.zoom() >= z && v.left_zoom() <= left && v.pan_turn() >= turned); // none of it ever goes back
            assert!((v.zoom() - z).abs() < 0.04 && (v.pan_turn() - turned).abs() < 0.02);
            if t > 1830.0 {
                assert!(v.zoom() > z && v.left_zoom() < left && v.pan_turn() > turned); // and none waits for the rest
            }
            z = v.zoom();
            left = v.left_zoom();
            turned = v.pan_turn();
        } else if landed == 0.0 {
            landed = t;
        }
        t += 4.0;
    }
    assert!(landed >= 1800.0 + f64::from(PAN_SETTLE_MS) - 4.0 && landed <= 1800.0 + f64::from(PAN_SETTLE_MS) + 4.0);
    assert!(v.zoom() == 1.0 && v.focus() == 1 && v.left_zoom() == 0.0 && !v.pan_active() && !v.animating() && v.pan_layers().is_none());
    assert_eq!(v.take_landed_turn(), Some([0, 1]));
}

#[test]
fn edge_pan_handover_keeps_speed() {
    // The view does not stop at the handover: its speed the frame after is the hand's, whatever the hand's speed, gentle
    // ones too.
    for px_per_ms in [0.02f32, 0.2, 1.0, 4.0] {
        let mut v = maximized_on(0);
        let (rad_per_px, end) = (0.0004f32, 0.5f32);
        let mut t = 1000.0;
        let (mut turn, mut before, mut was) = (0.0f32, 0.0f32, 0.0f32);
        while turn < end {
            let next = turn + px_per_ms * 4.0 * rad_per_px;
            turn = if end < next { end } else { next };
            let mut f = frame(1.0, 1.0, turn);
            f.way = 1.2;
            v.set_pan(&f, t);
            before = v.pan_turn() - was;
            was = v.pan_turn();
            t += 4.0;
        }
        t -= 4.0;
        v.set_target_with(true, 1, 40, t, landing());
        v.advance(t + 4.0);
        let after = v.pan_turn() - was;
        assert!(near(after / before, 1.0, 0.25), "{px_per_ms} px/ms: {after} after {before}");
    }
}

#[test]
fn edge_pan_fast_hand() {
    // A hand too fast for the landing's time takes less of it, so the speed still carries straight on.
    let mut v = maximized_on(0);
    let mut t = 1000.0;
    let mut f = frame(1.0, 1.0, 0.0);
    f.way = 0.6;
    let (mut was, mut before) = (0.0f32, 0.0f32);
    let mut turn = 0.0f32;
    while turn < 0.5 {
        turn += 0.02;
        f.turn = turn;
        v.set_pan(&f, t);
        before = v.pan_turn() - was;
        was = v.pan_turn();
        t += 4.0;
    }
    t -= 4.0;
    v.set_target_with(true, 1, 40, t, landing());
    v.advance(t + 4.0);
    assert!(near((v.pan_turn() - was) / before, 1.0, 0.25));
    run(&mut v, t + 8.0, t + 200.0, 8.0);
    assert!(near(v.pan_turn(), 0.6, 1e-4) && v.pan_landing()); // the view is there; the sizes carry on
    run(&mut v, t + 200.0, t + 700.0, 8.0);
    assert!(!v.pan_active() && v.zoom() == 1.0);
}

#[test]
fn edge_pan_flick() {
    // A flick: the hand arrives before the zoom has come down. Still whole, in the same time, the desktop being left no
    // bigger than it was.
    let mut v = maximized_on(0);
    v.set_pan(&frame(1.0, 1.0, 0.3), 1000.0);
    v.advance(1016.0);
    let z = v.zoom();
    v.set_target_with(true, 1, 40, 1016.0, landing());
    let mut landed = 0.0f64;
    let mut t = 1020.0;
    while t < 2000.0 && landed == 0.0 {
        v.advance(t);
        assert!(v.left_zoom() <= z);
        if !v.pan_landing() {
            landed = t;
        }
        t += 4.0;
    }
    assert!(landed != 0.0 && landed - 1016.0 <= f64::from(PAN_SETTLE_MS) + 8.0);
    assert!(v.focus() == 1 && v.zoom() == 1.0);
}

#[test]
fn edge_pan_layers() {
    // Who is in front. The hand: the desktop on top of the target. Landing: the target comes on top once they are clear,
    // and no sooner.
    let mut v = maximized_on(0);
    assert!(v.pan_layers().is_none());
    v.set_pan(&pan_frame(0.5, 0.0, 0.1, 0.5, 0, 1), 1000.0);
    let l = v.pan_layers().unwrap();
    assert!(l.over == 0 && l.under == 1 && l.left_alpha == 0.0);
    v.set_pan(&pan_frame(1.0, 1.0, 0.3, 0.5, 0, 1), 1010.0);
    v.advance(1030.0); // a flick: not down to the pair's 0.5 yet
    assert!(v.zoom() > 0.5);
    v.set_target_with(true, 1, 40, 1030.0, landing());
    let l = v.pan_layers().unwrap();
    assert!(l.over == 0 && l.under == 1);
    let mut t = 1034.0;
    while t < 1600.0 {
        v.advance(t);
        if v.left_zoom() <= 0.5 + 1e-3 {
            break;
        }
        assert!(v.pan_layers().unwrap().over == 0);
        t += 4.0;
    }
    v.advance(t + 8.0);
    let l = v.pan_layers().unwrap();
    assert!(l.over == 1 && l.under == 0);
    let depth = vec![2usize, 0, 1, 3];
    // in front of everything, the other just behind
    assert!(pan_draw_order(depth, &PanLayers { over: 1, under: 0, left: 0, left_alpha: 0.0 }) == vec![2, 3, 0, 1]);
}

#[test]
fn edge_pan_border_hands_over() {
    // The border goes over from the one to the other with no flash: both add up to what the pan had, the target's fades as
    // it grows.
    let mut v = maximized_on(0);
    v.set_pan(&pan_frame(1.0, 1.0, 0.3, 0.2, 0, 1), 1000.0);
    run(&mut v, 1000.0, 1800.0, 8.0);
    let had = v.highlight_alpha();
    assert!(near(had, 0.8, 1e-3));
    v.set_target_with(true, 1, 40, 1800.0, landing());
    assert!(near(v.highlight_alpha(), 0.0, 1e-4) && near(v.pan_layers().unwrap().left_alpha, had, 1e-4));
    let (mut a, mut b) = (0.0f32, had);
    let mut t = 1804.0;
    while t < 2316.0 {
        v.advance(t);
        let (na, nb) = (v.highlight_alpha(), v.pan_layers().unwrap().left_alpha);
        assert!((na - a).abs() < 0.04 && (nb - b).abs() < 0.04 && na <= had + 1e-4 && nb <= b);
        a = na;
        b = nb;
        t += 4.0;
    }
    v.advance(2330.0);
    assert!(v.highlight_alpha() == 0.0);
}

#[test]
fn edge_pan_unmaximized_mid_landing() {
    // Un-maximized during a landing: the target zooms out from where it is and the desktop being left finishes its own way
    // down.
    let mut v = maximized_on(0);
    v.set_pan(&pan_frame(1.0, 1.0, 0.3, 0.6, 0, 1), 1000.0);
    run(&mut v, 1000.0, 1800.0, 8.0);
    v.set_target_with(true, 1, 40, 1800.0, landing());
    run(&mut v, 1800.0, 2000.0 + 8.0, 8.0); // t <= 2000
    let (z, left) = (v.zoom(), v.left_zoom());
    assert!(z > 0.1 && left > 0.05);
    v.set_target(false, 1, 40, 2000.0);
    assert!(
        !v.pan_active()
            && near(v.zoom(), z, 1e-4)
            && near(v.left_zoom(), left, 1e-4)
            && v.left() == 0
            && v.animating()
            && v.switching()
    );
    let mut was = left;
    let mut t = 2008.0;
    while t < 2600.0 {
        v.advance(t);
        assert!(v.left_zoom() <= was && was - v.left_zoom() < 0.03);
        was = v.left_zoom();
        t += 8.0;
    }
    assert!(v.zoom() == 0.0 && v.left_zoom() == 0.0 && !v.animating());
}

#[test]
fn edge_pan_cuts() {
    {
        // A key during a landing is a cut: nothing of it is left.
        let mut v = maximized_on(0);
        v.set_pan(&frame(1.0, 1.0, 0.3), 1000.0);
        run(&mut v, 1000.0, 1800.0, 8.0);
        v.set_target_with(true, 1, 40, 1800.0, landing());
        v.advance(1900.0);
        v.set_target_with(true, 2, 40, 1900.0, cut());
        assert!(!v.pan_active() && v.zoom() == 1.0 && v.focus() == 2 && v.left_zoom() == 0.0 && !v.animating());
    }
    {
        // A key during a pan is a cut: straight there, the pan gone.
        let mut v = maximized_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.0), 1000.0);
        run(&mut v, 1000.0, 1300.0, 8.0);
        v.set_target_with(true, 2, 40, 1300.0, cut());
        assert!(!v.pan_active() && v.zoom() == 1.0 && v.focus() == 2 && !v.switching());
    }
    {
        // Un-maximized during a pan: the zoom carries on from where the pan had it.
        let mut v = maximized_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.0), 1000.0);
        run(&mut v, 1000.0, 1300.0, 8.0);
        let z = v.zoom();
        v.set_target(false, 0, 40, 1300.0);
        assert!(!v.pan_active() && near(v.zoom(), z, 1e-4));
        v.advance(1700.0);
        assert!(v.zoom() == 0.0);
    }
}

#[test]
fn edge_pan_frames_that_do_not_fit() {
    {
        // Frames that do not fit are ignored: another desktop, mid switch; an unchanged view frame changes nothing. (The
        // overview takes them now, as its own pan: overview_pan_*.)
        let mut o = ViewAnimator::default();
        o.set_target(false, 0, 40, 0.0);
        o.advance(500.0);
        o.set_pan(&pan_frame(1.0, 0.0, 0.0, 0.2, 1, 2), 500.0); // not from the desktop it is on
        assert!(!o.pan_active());
        o.set_pan(&frame(1.0, 0.0, 0.0), 500.0);
        assert!(o.pan_active() && o.pan_overview());
        let mut v = maximized_on(0);
        v.set_pan(&pan_frame(1.0, 0.0, 0.0, 0.2, 1, 2), 1000.0);
        assert!(!v.pan_active());
        v.set_pan(&frame(1.0, 0.0, 0.0), 1000.0);
        v.set_target(true, 0, 40, 1050.0);
        assert!(v.pan_active());
    }
    {
        // `pan` with no pan under way is a cut, and an end of pan after the landing is nothing.
        let mut v = maximized_on(0);
        v.set_target_with(true, 1, 40, 1000.0, landing());
        assert!(v.zoom() == 1.0 && v.focus() == 1 && !v.switching());
        v.set_pan(&EdgePan::default(), 1010.0);
        assert!(!v.pan_active());
    }
    {
        // The supervisor stopped mid-pan: the view does not stay turned.
        let mut v = maximized_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.2), 1000.0);
        run(&mut v, 1000.0, 1000.0 + f64::from(PAN_STALE_MS) + 900.0, 16.0);
        assert!(!v.pan_active() && v.zoom() == 1.0);
    }
    {
        // Rapid pushes: a new pan takes over from the eased-back one with no jump; none can start while one lands.
        let mut v = maximized_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.2), 1000.0);
        run(&mut v, 1000.0, 1200.0, 8.0);
        v.set_pan(&EdgePan::default(), 1200.0);
        v.advance(1216.0);
        let z = v.zoom();
        let other = pan_frame(0.5, 0.0, 0.05, 0.2, 0, 2);
        v.set_pan(&other, 1216.0);
        assert!(near(v.zoom(), z, 1e-4) && v.pan().to == 2);
        run(&mut v, 1216.0, 1600.0, 8.0);
        v.set_pan(&pan_frame(1.0, 1.0, 0.2, 0.2, 0, 2), 1600.0);
        run(&mut v, 1600.0, 2200.0, 8.0);
        v.set_target_with(true, 2, 40, 2200.0, landing());
        v.set_pan(&pan_frame(1.0, 0.0, 0.1, 0.2, 2, 1), 2210.0);
        assert!(v.pan_landing() && v.pan().to == 2);
    }
}

#[test]
fn edge_pan_paths() {
    // The way across, in every direction, on three desktops: left, middle, right. Unit vectors.
    let sizes = EQ;
    let c = Canvas::new(1920.0, 1200.0, 0.42, 0.27);
    let overview = overview_layout(&sizes, 1920.0, 1200.0, 40.0);
    let rad = |deg: f32| deg * PI / 180.0;
    let way = |placed: &[Placement], from: u32, to: u32, side: u8| {
        let mut p = pan_frame(1.0, 0.0, 0.0, 0.0, from, to);
        p.side = side;
        p.exit = 0.5;
        p.entry = 0.5;
        edge_pan_path(&c, &sizes, 40.0, &overview, placed, &p)
    };
    let dir_of = |v: &Vec3| [v.x.atan2(-v.z), v.y.atan2(v.x.hypot(v.z))];
    {
        // Up and down, to a desktop lifted over its slot: E under P.
        let up = [dflt(), p4(rad(-8.0), rad(40.0), 1.0, 0.0), p4(rad(90.0), 0.0, 1.0, 0.0)];
        let u = way(&up, 0, 1, 2).unwrap();
        assert!(u.angle > 0.1 && dir_of(&u.to)[1] > dir_of(&u.from)[1]);
    }
    {
        // The dot goes straight from E to P, each share of the way that share of the angle.
        let far = [dflt(), p4(rad(60.0), 0.0, 1.0, 0.0), dflt()];
        let w = way(&far, 0, 1, 0).unwrap();
        assert!(w.angle > 0.5);
        let (start, end) = (edge_pan_dot(&w, &w.from, 0.0), edge_pan_dot(&w, &w.from, 1.0));
        assert!(near(start.x, w.from.x, 1e-4) && near(end.x, w.to.x, 1e-6));
        for s in [0.25f32, 0.5, 0.9] {
            let q = edge_pan_dot(&w, &w.from, s);
            let a = (q.x * w.from.x + q.y * w.from.y + q.z * w.from.z).clamp(-1.0, 1.0).acos();
            assert!(near(a, w.angle * s, 2e-3));
        }
    }
    {
        // Edges, and what cannot be drawn.
        let t = rect(10.0, 20.0, 100.0, 50.0);
        let (a, b, u, d) = (edge_point(&t, 0, 0.3), edge_point(&t, 1, 0.3), edge_point(&t, 2, 0.3), edge_point(&t, 3, 2.0));
        assert!(near(a[0], 110.0, 1e-3) && near(a[1], 35.0, 1e-3) && near(b[0], 10.0, 1e-3) && near(u[0], 40.0, 1e-3) && near(u[1], 20.0, 1e-3));
        assert!(near(d[0], 110.0, 1e-3) && near(d[1], 70.0, 1e-3));
        let flat = vec![dflt(); 3];
        assert!(way(&flat, 0, 0, 0).is_none() && way(&flat, 0, 7, 0).is_none());
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// One curve for the landing, one for a desktop's size: smooth, and never past either end, whatever they are given.

#[test]
fn settle_curve_shape() {
    for t in [0.0f32, 0.1, 0.33, 0.5, 0.8, 1.0] {
        assert!(near(settle_curve(t, 0.0), smootherstep(t), 1e-6)); // from rest: the clip's own curve
    }
    let mut v = 0.0f32;
    while v <= 2.5 {
        assert!(settle_curve(0.0, v) == 0.0 && near(settle_curve(1.0, v), 1.0, 1e-5));
        let h = 1e-3f32;
        assert!(near(settle_curve(h, v) / h, v, 0.02)); // leaves at that speed
        assert!(near((1.0 - settle_curve(1.0 - h, v)) / h, 0.0, 0.02)); // and arrives at rest
        let mut was = 0.0f32;
        let mut t = 0.01f32;
        while t <= 1.0 {
            let x = settle_curve(t, v);
            assert!(x >= was - 1e-5 && x <= 1.0 + 1e-5);
            was = x;
            t += 0.01;
        }
        v += 0.1;
    }
    assert!(settle_curve(0.5, 9.0) == settle_curve(0.5, SETTLE_MAX_SPEED) && settle_curve(0.5, -1.0) == settle_curve(0.5, 0.0));
    assert!(settle_curve(-1.0, 1.0) == 0.0 && near(settle_curve(2.0, 1.0), 1.0, 1e-5));
}

#[test]
fn zoom_growth_shape() {
    // The default desktop's curve exactly (distance 1, centre slot: g / (g (1 - w) + w), g the tile's own growth), and for
    // any other ratio the same shape, always from 1 to the ratio with no turn.
    for w in [0.0f32, 0.2, 0.5, 0.8, 1.0] {
        let g = 1.0 + 2.0 * w;
        assert!(near(zoom_growth(w, 3.0), g / (g * (1.0 - w) + w), 1e-5));
    }
    for grown in [0.1f32, 0.3, 0.8, 1.0, 1.14, 2.0, 3.0, 6.0, 20.0] {
        assert!(near(zoom_growth(0.0, grown), 1.0, 1e-6) && near(zoom_growth(1.0, grown), grown, 1e-5));
        let mut was = 1.0f32;
        let mut w = 0.02f32;
        while w <= 1.0 {
            let k = zoom_growth(w, grown);
            assert!(if grown >= 1.0 { k >= was - 1e-5 } else { k <= was + 1e-5 });
            was = k;
            w += 0.02;
        }
    }
}

const CURVE_SIZES: [Size; 3] = [Size::new(1920.0, 1080.0), Size::new(1920.0, 1200.0), Size::new(2560.0, 1080.0)];
const CURVE_CANVAS: Canvas = Canvas::new(1920.0, 1200.0, 0.40, 0.25);

#[test]
fn zoom_seen_size_is_monotone() {
    // Through zoomed_placements, as seen, every size, every slot: straight from the placed size to the fit.
    let (sizes, c) = (&CURVE_SIZES, &CURVE_CANVAS);
    let overview = overview_layout(sizes, 1920.0, 1200.0, 40.0);
    let seen = |t: &FRect, p: &Placement| {
        let m = place_point(c, t, p, t.x + t.width / 2.0, t.y + t.height / 2.0);
        t.height / (m.x * m.x + m.y * m.y + m.z * m.z).sqrt()
    };
    for d in [2.0f32, 1.0, 0.71, 0.38, 0.2, 0.1] {
        for i in 0..3 {
            let mut placed = vec![dflt(); 3];
            placed[i] = Placement::new(0.2, -0.1, d, 0.5, 0.3);
            let start = seen(&overview[i], &placed[i]);
            let full = blended_layout(sizes, 1920.0, 1200.0, 40.0, 1.0, i as f32);
            let end = full[i].height; // distance 1, centred
            let mut was = start;
            let mut z = 0.02f32;
            while z <= 1.0001 {
                let tiles = blended_layout(sizes, 1920.0, 1200.0, 40.0, z, i as f32);
                let drawn = zoomed_placements(c, &overview, &tiles, &placed, z, i as f32);
                let now = seen(&tiles[i], &drawn[i]);
                assert!(now >= start.min(end) - 0.5 && now <= start.max(end) + 0.5); // never past either end
                assert!(if end >= start { now >= was - 0.05 } else { now <= was + 0.05 }); // never back
                was = now;
                z += 0.02;
            }
            assert!(near(was, end, 0.5));
        }
    }
}

#[test]
fn two_zoomed_at_once() {
    // Two zoomed at once: the desktop left as though the zoom were on it, the others untouched.
    let (sizes, c) = (&CURVE_SIZES, &CURVE_CANVAS);
    let overview = overview_layout(sizes, 1920.0, 1200.0, 40.0);
    let leaving = Leaving { zoom: 0.3, index: 0 };
    let with = ZoomOptions { keep_size: false, leaving };
    let placed = [Placement::new(0.1, 0.1, 0.4, 0.0, 0.2), Placement::new(-0.1, 0.0, 0.6, 0.0, -0.2), dflt()];
    let alone = blended_layout(sizes, 1920.0, 1200.0, 40.0, 0.3, 0.0);
    let both = blended_layout_leaving(sizes, 1920.0, 1200.0, 40.0, 0.6, 1.0, leaving);
    let target = blended_layout(sizes, 1920.0, 1200.0, 40.0, 0.6, 1.0);
    assert!(same(&both[0], &alone[0]) && same(&both[1], &target[1]) && same(&both[2], &target[2]));
    let a = zoomed_placements(c, &overview, &alone, &placed, 0.3, 0.0);
    let b = zoomed_placements_with(c, &overview, &both, &placed, 0.6, 1.0, with);
    let d = zoomed_placements(c, &overview, &target, &placed, 0.6, 1.0);
    assert!(near(b[0].distance, a[0].distance, 1e-5) && near(b[0].x, a[0].x, 1e-5) && near(b[0].roll, a[0].roll, 1e-6));
    assert!(near(b[1].distance, d[1].distance, 1e-5) && near(b[1].roll, d[1].roll, 1e-6));
    // zoom 0 with one on its way out is still a zoom: the first frame of a landing.
    let first_tiles = blended_layout_leaving(sizes, 1920.0, 1200.0, 40.0, 0.0, 1.0, leaving);
    let first = zoomed_placements_with(c, &overview, &first_tiles, &placed, 0.0, 1.0, with);
    assert!(near(first[0].distance, a[0].distance, 1e-5));
    assert!(same(
        &blended_layout(sizes, 1920.0, 1200.0, 40.0, 0.5, 1.0)[0],
        &blended_layout_leaving(sizes, 1920.0, 1200.0, 40.0, 0.5, 1.0, Leaving { zoom: 0.0, index: 0 })[0]
    ));
    // Maximized again while still on its way out: whichever is further in draws it, so it never jumps.
    let again = blended_layout_leaving(sizes, 1920.0, 1200.0, 40.0, 0.6, 0.0, leaving);
    assert!(same(&again[0], &blended_layout(sizes, 1920.0, 1200.0, 40.0, 0.6, 0.0)[0]));
    assert!(near(
        zoomed_placements_with(c, &overview, &again, &placed, 0.6, 0.0, with)[0].distance,
        zoomed_placements(c, &overview, &again, &placed, 0.6, 0.0)[0].distance,
        1e-5
    ));
}

// ---------------------------------------------------------------------------------------------------------------------
// The overview's edge pan (overview-edge-pan, 2026-11-07; tools/native_spike/edge_pan.py: Overview).

fn overview_on(desktop: u32) -> ViewAnimator {
    let mut v = ViewAnimator::default();
    v.set_target(false, desktop, 40, 0.0);
    v.advance(1000.0);
    assert!(v.zoom() == 0.0 && !v.animating() && v.focus() == desktop);
    v
}

#[test]
fn overview_pan_draws_and_serials() {
    {
        // Taken at rest in the overview: nothing zooms, the hand's turn is drawn, the pills and dot hide (switching).
        let mut v = overview_on(0);
        v.set_pan(&pan_frame(0.5, 0.0, 0.2, 0.7, 0, 1), 1000.0); // whatever zoom it says: the overview's is 0
        assert!(v.pan_active() && v.pan_overview() && v.switching() && v.pan().zoom == 0.0);
        assert!(v.pan_layers().is_none() && v.highlight_alpha() == 1.0); // the room as placed, and the border where it was
        let mut t = 1000.0;
        while t < 1300.0 {
            v.advance(t);
            assert!(v.zoom() == 0.0 && v.focus() == 0);
            t += 8.0;
        }
        assert!(near(v.pan_turn(), 0.2, 1e-3));
    }
    {
        // A new pan has a centred serial of its own (the room turns from the centre of the view as it is).
        let mut v = overview_on(0);
        let s = v.serial();
        v.set_pan(&frame(0.2, 0.0, 0.05), 1000.0);
        assert!(v.serial() == s + 1 && v.turn_centred());
        v.set_pan(&frame(0.4, 0.0, 0.1), 1010.0); // the same pan goes on: no new serial
        assert!(v.serial() == s + 1);
    }
    {
        // Pulled back: it eases out and ends, the selection never moved, nothing landed.
        let mut v = overview_on(0);
        v.set_pan(&frame(1.0, 0.5, 0.3), 1000.0);
        run(&mut v, 1000.0, 1200.0, 8.0);
        v.set_pan(&EdgePan::default(), 1200.0);
        run(&mut v, 1200.0, 1600.0, 8.0);
        assert!(!v.pan_active() && !v.switching() && v.focus() == 0 && v.take_landed_turns().is_empty());
    }
    {
        // An unchanged overview view frame (same desktop) keeps it going; another desktop, a cut or a maximize ends it.
        let mut v = overview_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.3), 1000.0);
        v.set_target(false, 0, 40, 1050.0);
        assert!(v.pan_active());
        v.set_target(false, 2, 40, 1060.0);
        assert!(!v.pan_active() && v.focus() == 2 && v.take_landed_turns().is_empty());
        let mut m = overview_on(0);
        m.set_pan(&frame(1.0, 0.0, 0.3), 1000.0);
        m.set_target(true, 0, 40, 1050.0);
        assert!(!m.pan_active());
    }
}

#[test]
fn overview_pan_lands() {
    {
        // The hand got there: `pan` on the target lands it. The highlight is there at once; pan_land eases 0 -> 1 over
        // turn_ms (300), then one centred turn lands with the pan's serial, in the same advance the pan ends.
        let mut v = overview_on(0);
        v.set_pan(&frame(1.0, 1.0, 0.4), 1000.0);
        run(&mut v, 1000.0, 1200.0, 8.0);
        let s = v.serial();
        v.set_target_with(false, 1, 40, 1200.0, landing());
        // the maximized landing's border fade is not the overview's
        assert!(v.pan_landing() && v.focus() == 1 && v.highlight_alpha() == 1.0 && v.pan_layers().is_none());
        assert!(v.pan_landing() && v.pan_overview() && v.focus() == 1 && v.selected() == 1 && v.zoom() == 0.0);
        let mut last = 0.0f32;
        let mut ended = 0.0f64;
        let mut t = 1204.0;
        while t < 1700.0 && ended == 0.0 {
            v.advance(t);
            if !v.pan_active() {
                ended = t;
                break;
            }
            assert!(v.pan_land() >= last);
            last = v.pan_land();
            t += 4.0;
        }
        assert!(ended != 0.0 && ended - 1200.0 >= 296.0 && ended - 1200.0 <= 304.0);
        let landed = v.take_landed_turns();
        assert!(landed.len() == 1 && landed[0].centred && landed[0].serial == s && landed[0].from == 0 && landed[0].to == 1);
        assert!(!v.switching() && v.focus() == 1 && v.zoom() == 0.0);
        v.set_pan(&EdgePan::default(), 1800.0); // the supervisor's late end of pan is nothing now
        assert!(!v.pan_active());
    }
    {
        // During the landing frames are ignored and a same view frame keeps it; maximizing cuts it short and the room
        // still gets the whole turn.
        let mut v = overview_on(0);
        v.set_pan(&frame(1.0, 1.0, 0.4), 1000.0);
        v.set_target_with(false, 1, 40, 1100.0, landing());
        v.set_pan(&frame(0.1, 0.0, 0.0), 1150.0);
        v.set_target(false, 1, 40, 1160.0);
        assert!(v.pan_landing());
        v.set_target(true, 1, 40, 1200.0);
        assert!(!v.pan_active());
        let landed = v.take_landed_turns();
        assert!(landed.len() == 1 && landed[0].centred && landed[0].to == 1);
    }
    {
        // `pan` for a desktop that is not the pan's target is a plain selection that ends it unlanded.
        let mut v = overview_on(0);
        v.set_pan(&frame(1.0, 1.0, 0.4), 1000.0);
        v.set_target_with(false, 2, 40, 1100.0, landing());
        assert!(!v.pan_active() && v.focus() == 2 && v.take_landed_turns().is_empty());
    }
    {
        // Stale: no frame for PAN_STALE_MS and it goes back.
        let mut v = overview_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.2), 1000.0);
        run(&mut v, 1000.0, 1000.0 + f64::from(PAN_STALE_MS) + 600.0, 16.0);
        assert!(!v.pan_active());
    }
    {
        // The maximized pan is untouched by all of it: not the overview's.
        let mut v = maximized_on(0);
        v.set_pan(&frame(1.0, 0.0, 0.0), 1000.0);
        assert!(v.pan_active() && !v.pan_overview() && v.pan_land() == 0.0);
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Layouts.

#[test]
fn layouts() {
    // Overview: 3 x 16:9 desktops in 1920x1200 with 40 px gaps and margins.
    let o = overview_layout(&EQ, 1920.0, 1200.0, 40.0);
    assert!(o.len() == 3);
    let w = (1920.0f32 - 4.0 * 40.0) / 3.0;
    assert!(near(o[0].x, 40.0, 0.5) && near(o[0].width, w, 0.5) && near(o[0].height, w * 1080.0 / 1920.0, 0.5));
    assert!(near(o[1].x, 40.0 + w + 40.0, 0.5) && near(o[2].x + o[2].width, 1880.0, 0.5));
    assert!(near(o[0].y, (1200.0 - o[0].height) / 2.0, 0.5));
    let wide = overview_layout(&EQ, 1920.0, 1200.0, 0.0);
    assert!(near(wide[0].x, 0.0, 0.5) && near(wide[0].width, 640.0, 0.5));
    // Spacing is bounded; absurd spacing never yields negative tiles.
    for r in overview_layout(&EQ, 1920.0, 1200.0, 5000.0) {
        assert!(r.width >= 0.0 && r.height >= 0.0);
    }

    // Zoomed: selected fills (aspect-fit), neighbours offset by one view width plus spacing.
    let z = zoomed_layout(&EQ, 1920.0, 1200.0, 40.0, 1.0);
    assert!(near(z[1].x, 0.0, 0.5) && near(z[1].width, 1920.0, 0.5) && near(z[1].height, 1080.0, 0.5) && near(z[1].y, 60.0, 0.5));
    assert!(near(z[0].x, -1960.0, 0.5) && near(z[2].x, 1960.0, 0.5));
    let mid = zoomed_layout(&EQ, 1920.0, 1200.0, 40.0, 1.5); // sliding between 1 and 2
    assert!(near(mid[1].x, -980.0, 0.5) && near(mid[2].x, 980.0, 0.5));

    // Blend endpoints and monotone easing.
    let (b0, b1) = (blended_layout(&EQ, 1920.0, 1200.0, 40.0, 0.0, 1.0), blended_layout(&EQ, 1920.0, 1200.0, 40.0, 1.0, 1.0));
    assert!(near(b0[1].x, o[1].x, 0.5) && near(b1[1].width, 1920.0, 0.5));

    // three equal sources match old layout (outputs captured from the pre-span implementation).
    let ov = [rect(40.0, 435.0, 586.6667, 330.0), rect(666.6667, 435.0, 586.6667, 330.0), rect(1293.3334, 435.0, 586.6667, 330.0)];
    let zoomed_old: [(f32, [FRect; 3]); 3] = [
        (0.0, [rect(0.0, 60.0, 1920.0, 1080.0), rect(1960.0, 60.0, 1920.0, 1080.0), rect(3920.0, 60.0, 1920.0, 1080.0)]),
        (1.0, [rect(-1960.0, 60.0, 1920.0, 1080.0), rect(0.0, 60.0, 1920.0, 1080.0), rect(1960.0, 60.0, 1920.0, 1080.0)]),
        (1.5, [rect(-2940.0, 60.0, 1920.0, 1080.0), rect(-980.0001, 60.0, 1920.0, 1080.0), rect(980.0, 60.0, 1920.0, 1080.0)]),
    ];
    for (pos, r) in &zoomed_old {
        let (z0, z1) = (blended_layout(&EQ, 1920.0, 1200.0, 40.0, 0.0, *pos), blended_layout(&EQ, 1920.0, 1200.0, 40.0, 1.0, *pos));
        assert!(z0.len() == 3 && z1.len() == 3);
        for i in 0..3 {
            assert!(same(&z0[i], &ov[i]) && same(&z1[i], &r[i]));
        }
    }
}

#[test]
fn mixed_layouts() {
    // mixed overview shares height: 16:9, 32:9 and 3440x1440 side by side within the view.
    let mixed = [sz(1920.0, 1080.0), sz(3840.0, 1080.0), sz(3440.0, 1440.0)];
    let mo = blended_layout(&mixed, 1920.0, 1200.0, 40.0, 0.0, 0.0);
    assert!(mo.len() == 3 && mo[0].height > 0.0);
    for i in 0..3 {
        assert!(near(mo[i].height, mo[0].height, 1e-3) && near(mo[i].width, mo[i].height * mixed[i].w / mixed[i].h, 0.5));
    }
    assert!(near(mo[1].width, 2.0 * mo[0].width, 0.5) && mo[0].x >= 0.0 && mo[2].x + mo[2].width <= 1920.0 + 1e-3);
    assert!(mo[0].x + mo[0].width <= mo[1].x && mo[1].x + mo[1].width <= mo[2].x);
    let mut total = 0.0f32;
    for r in &mo {
        total += r.width;
    }
    assert!(total <= 1920.0);

    // zoomed mixed centres selected: tile 1 (32:9) centred and fitted; neighbours share its height.
    let mz = blended_layout(&mixed, 1920.0, 1200.0, 40.0, 1.0, 1.0);
    assert!(mz.len() == 3 && near(mz[1].x + mz[1].width / 2.0, 960.0, 0.5));
    assert!(mz[1].x >= -0.5 && mz[1].x + mz[1].width <= 1920.5 && mz[1].y >= -0.5 && mz[1].y + mz[1].height <= 1200.5);
    assert!(near(mz[1].width, 1920.0, 0.5) && near(mz[0].height, mz[1].height, 1e-3) && near(mz[2].height, mz[1].height, 1e-3));
    assert!(near(mz[0].x + mz[0].width + 40.0, mz[1].x, 0.5) && near(mz[1].x + mz[1].width + 40.0, mz[2].x, 0.5));
    // Fractional position: the lerp of the two centres sits at the view centre.
    let mf = zoomed_layout(&mixed, 1920.0, 1200.0, 40.0, 1.25);
    let (c1, c2) = (mf[1].x + mf[1].width / 2.0, mf[2].x + mf[2].width / 2.0);
    assert!(near(c1 + (c2 - c1) * 0.25, 960.0, 0.5));
    assert!(overview_layout(&[], 1920.0, 1200.0, 40.0).is_empty());
    let bad = [sz(1920.0, 1080.0), sz(0.0, 1080.0)];
    assert!(blended_layout(&bad, 1920.0, 1200.0, 40.0, 0.5, 0.0).is_empty());

    assert!(
        smoothstep(0.0) == 0.0
            && smoothstep(1.0) == 1.0
            && near(smoothstep(0.5), 0.5, 1e-3)
            && smoothstep(-1.0) == 0.0
            && smoothstep(2.0) == 1.0
    );
    let mut t = 0.0f32;
    while t < 1.0 {
        assert!(smoothstep(t + 0.1) >= smoothstep(t));
        t += 0.1;
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// The animator.

#[test]
fn animator_time_based() {
    // Animator: time-based, reaches target exactly, retargets from the current state.
    let mut a = ViewAnimator::with_duration(200.0);
    a.set_target(false, 0, 40, 0.0);
    a.advance(1000.0);
    assert!(!a.animating() && a.zoom() == 0.0 && a.position() == 0.0);
    a.set_target(true, 2, 40, 1000.0);
    assert!(a.position() == 2.0); // zooming in from the overview: on the selection at once; the zoom eases
    a.advance(1100.0);
    assert!(a.animating() && a.zoom() > 0.0 && a.zoom() < 1.0 && a.position() == 2.0);
    a.advance(1200.0);
    assert!(!a.animating() && a.zoom() == 1.0 && a.position() == 2.0 && a.selected() == 2);
    a.set_target(true, 1, 120, 1200.0);
    a.advance(1300.0); // zoomed: the switch animates (zooming out of 2 first)
    assert!(a.switching() && a.position() == 2.0 && a.selected() == 1 && a.zoom() < 1.0 && a.spacing() > 40.0 && a.spacing() < 120.0);
    a.advance(2000.0);
    assert!(a.zoom() == 1.0 && a.position() == 1.0 && a.spacing() == 120.0);
    a.set_target(false, 1, 120, 2000.0);
    a.advance(2100.0);
    assert!(a.zoom() > 0.0 && a.zoom() < 1.0 && a.position() == 1.0);
    a.advance(3000.0);
    assert!(a.zoom() == 0.0 && !a.animating());
    a.set_target(true, 9, 9999, 3000.0);
    a.advance(4000.0); // selection and spacing are clamped
    assert!(a.selected() == MAX_DESKTOPS as u32 - 1 && a.spacing() == MAX_SPACING as f32);
}

#[test]
fn animator_cut() {
    // Cursor switch with its animation off (anchor mode, 2026-11-03): zoomed in, a cut shows the target at once at its
    // placement: no zoom out, no turn, nothing landed for anchor mode's room. A later plain frame changes nothing.
    let mut v = ViewAnimator::default();
    v.set_target(false, 0, 40, 0.0);
    v.advance(1000.0);
    v.set_target(true, 0, 40, 1000.0);
    v.advance(2000.0);
    v.set_target_with(true, 2, 40, 2000.0, cut());
    assert!(
        !v.switching()
            && !v.animating()
            && v.zoom() == 1.0
            && v.look() == 0.0
            && v.turn() == 0.0
            && v.position() == 2.0
            && v.focus() == 2
            && v.selected() == 2
            && v.take_landed_turns().is_empty()
    );
    let mut t = 2000.0;
    while t <= 3000.0 {
        v.advance(t);
        assert!(v.zoom() == 1.0 && v.look() == 0.0 && v.turn() == 0.0 && v.focus() == 2 && !v.switching());
        // Follow / locked switch with a cut too (overview-anchor-only): their aim stays fully on the desktop, never
        // easing to the overview's centre on the way (focus_weight 1 throughout).
        assert!(focus_weight(&v) == 1.0);
        t += 10.0;
    }
    v.set_target(true, 2, 40, 3000.0);
    v.advance(3010.0);
    assert!(!v.animating() && v.focus() == 2);
    // A key switch in flight (mid-turn) is dropped by a cut, unlanded, which lands on its own target.
    v.set_target(true, 0, 40, 4000.0);
    v.advance(4300.0);
    assert!(v.switching() && v.zoom() == 0.0 && v.turn() > 0.0);
    v.set_target_with(true, 1, 40, 4300.0, cut());
    assert!(!v.switching() && v.zoom() == 1.0 && v.look() == 0.0 && v.turn() == 0.0 && v.focus() == 1 && !v.turn_centred());
    v.advance(5000.0);
    assert!(!v.animating() && v.focus() == 1 && v.take_landed_turns().is_empty());
    // The keys still animate after a cut, and in the overview a cut is a plain selection.
    v.set_target(true, 2, 40, 6000.0);
    v.advance(6100.0);
    assert!(v.switching());
    v.advance(8000.0);
    assert!(!v.switching() && v.focus() == 2);
    v.set_target(false, 2, 40, 9000.0);
    v.advance(10000.0);
    v.set_target_with(false, 0, 40, 10000.0, cut());
    assert!(v.zoom() == 0.0 && v.position() == 0.0 && !v.switching());
}

#[test]
fn animator_eases_never_snaps() {
    // Regression (2026-11-02, animations back): zoom in and out ease over the duration and never snap; in the overview
    // Ctrl+Alt+Left / Right just moves the selection (nothing zoomed to animate).
    assert!(smootherstep(0.0) == 0.0 && smootherstep(1.0) == 1.0 && near(smootherstep(0.5), 0.5, 1e-3));
    let mut v = ViewAnimator::default(); // defaults: 220 ms zoom, 300 ms turn
    v.set_target(false, 0, 40, 0.0);
    v.advance(1000.0);
    v.set_target(true, 0, 40, 1000.0);
    let mut last = 0.0f32;
    let mut t = 1000.0;
    while t <= 1220.0 {
        v.advance(t);
        assert!(v.zoom() >= last && v.zoom() - last < 0.12 && v.look() == 0.0 && !v.switching());
        last = v.zoom();
        t += 10.0;
    }
    assert!(v.zoom() == 1.0 && !v.animating());
    v.set_target(false, 0, 40, 2000.0);
    v.advance(2110.0);
    assert!(v.zoom() > 0.3 && v.zoom() < 0.7);
    v.advance(2220.0);
    assert!(v.zoom() == 0.0 && !v.animating());
    for to in [1u32, 2, 1, 0] {
        v.set_target(false, to, 40, 3000.0);
        assert!(v.position() == to as f32 && !v.switching() && v.zoom() == 0.0);
    }
}

#[test]
fn animator_three_phase_switch() {
    // Ctrl+Alt+Left / Right zoomed in (every viewing mode: the animator has no mode): zoom out of the current desktop, turn
    // the view toward the target, zoom into it. Continuous throughout; at rest the view is never turned.
    let mut v = ViewAnimator::new(220.0, 300.0);
    v.set_target(true, 1, 40, 0.0);
    v.advance(1000.0);
    assert!(v.zoom() == 1.0 && v.position() == 1.0 && !v.animating());
    v.set_target(true, 2, 40, 1000.0);
    assert!(v.switching() && v.selected() == 2 && v.position() == 1.0 && v.zoom() == 1.0 && v.look() == 0.0);
    assert!(v.look_from() == 1 && v.look_to() == 2);
    let (mut pz, mut pl) = (1.0f32, 0.0f32);
    let mut phase = 1;
    let mut t = 1000.0;
    while t <= 1730.0 {
        v.advance(t);
        let (z, l) = (v.zoom(), v.look());
        assert!((z - pz).abs() < 0.06 && (l - pl).abs() < 0.06); // no jumps
        if t < 1220.0 {
            assert!(phase == 1 && v.position() == 1.0 && l == 0.0 && z <= pz); // 1: zoom out of 1
        } else if t < 1520.0 {
            phase = 2;
            assert!(v.position() == 2.0 && z == 0.0 && l >= pl && l <= 1.0); // 2: turn toward 2
        } else if t < 1740.0 {
            phase = 3;
            assert!(v.position() == 2.0 && z >= pz && l <= pl + 1e-3); // 3: zoom into 2
        }
        if t == 1370.0 {
            assert!(l > 0.3 && l < 0.7); // mid-turn
        }
        pz = z;
        pl = l;
        t += 5.0;
    }
    assert_eq!(phase, 3);
    v.advance(1740.0);
    assert!(!v.switching() && !v.animating() && v.zoom() == 1.0 && v.look() == 0.0 && v.position() == 2.0);
    // Retargeted while still zooming out: aims the same switch elsewhere.
    v.set_target(true, 1, 40, 2000.0);
    v.advance(2100.0);
    v.set_target(true, 0, 40, 2100.0);
    assert!(v.look_from() == 2 && v.look_to() == 0);
    v.advance(2900.0);
    assert!(!v.switching() && v.position() == 0.0 && v.zoom() == 1.0);
    // Back to where it came from before the turn: it zooms straight back in.
    v.set_target(true, 1, 40, 3000.0);
    v.advance(3100.0);
    let back = v.zoom();
    v.set_target(true, 0, 40, 3100.0);
    assert!(!v.switching() && v.zoom() == back && v.position() == 0.0 && v.look() == 0.0);
    v.advance(3400.0);
    assert!(v.zoom() == 1.0 && !v.animating());
    // A press mid-turn queues: the switch lands, then the next runs from there.
    v.set_target(true, 1, 40, 4000.0);
    v.advance(4300.0);
    v.set_target(true, 2, 40, 4300.0);
    assert!(v.switching() && v.look_to() == 1);
    v.advance(4800.0);
    assert!(v.switching() && v.look_from() == 1 && v.look_to() == 2);
    v.advance(5500.0);
    assert!(!v.switching() && v.position() == 2.0 && v.zoom() == 1.0 && v.look() == 0.0);
    // Zooming out mid-turn: zoom and turn ease back to the plain overview from where they are.
    v.set_target(true, 0, 40, 6000.0);
    v.advance(6350.0);
    let turn = v.look();
    assert!(turn > 0.0 && v.zoom() == 0.0);
    v.set_target(false, 0, 40, 6350.0);
    assert!(!v.switching() && v.look() == turn && v.zoom() == 0.0);
    v.advance(6460.0);
    assert!(v.look() > 0.0 && v.look() < turn);
    v.advance(6600.0);
    assert!(v.look() == 0.0 && v.zoom() == 0.0 && !v.animating() && v.position() == 0.0);
    // Zooming out mid-zoom-out of a switch keeps the zoom on the desktop being left (no jump), then shows the selection.
    v.set_target(true, 1, 40, 7000.0);
    v.advance(7500.0);
    v.set_target(true, 2, 40, 8000.0);
    v.advance(8100.0);
    let leaving = v.zoom();
    v.set_target(false, 2, 40, 8100.0);
    assert!(v.position() == 1.0 && v.zoom() == leaving);
    v.advance(8400.0);
    assert!(v.zoom() == 0.0 && v.position() == 2.0);
}

#[test]
fn animator_clone_is_a_checkpoint() {
    // The C++ checkpoint visitor (same-build restart state) is Clone here: a copy taken mid-switch carries on exactly.
    let mut v = ViewAnimator::new(220.0, 300.0);
    v.set_target(true, 0, 40, 0.0);
    v.advance(1000.0);
    v.set_target(true, 2, 40, 1000.0);
    v.advance(1300.0);
    let mut w = v.clone();
    for t in [1400.0, 1550.0, 1700.0, 1800.0] {
        v.advance(t);
        w.advance(t);
        assert!(v.zoom().to_bits() == w.zoom().to_bits() && v.look().to_bits() == w.look().to_bits() && v.focus() == w.focus());
    }
    assert_eq!(v.take_landed_turns(), w.take_landed_turns());
    // pause_clock: every clock moves with the pause, so the motion resumes where it was.
    let mut p = ViewAnimator::new(220.0, 300.0);
    p.set_target(false, 0, 40, 0.0);
    p.advance(1000.0);
    p.set_target(true, 0, 40, 1000.0);
    p.advance(1100.0);
    let z = p.zoom();
    p.pause_clock(5000.0);
    p.advance(6100.0);
    assert!(p.zoom() == z && p.animating());
}

// ---------------------------------------------------------------------------------------------------------------------
// Per-desktop placement (orbit round you, distance, curvature). Every desktop always faces you.

const CV: Canvas = Canvas::new(1920.0, 1200.0, 0.5, 0.3125); // square canvas pixels: half_w/half_h == width/height
const CENTRED: FRect = FRect::new(480.0, 330.0, 960.0, 540.0); // its centre is the canvas centre: straight ahead
const TILE: FRect = FRect::new(240.0, 300.0, 960.0, 540.0); // left of centre and a little up
const PX_WORLD: f32 = 2.0 * 0.5 / 1920.0; // world units per canvas pixel
const HALF_PI: f32 = PI / 2.0;

fn plane(px: f32, py: f32) -> Vec3 {
    Vec3::new((2.0 * px / 1920.0 - 1.0) * 0.5, (1.0 - 2.0 * py / 1200.0) * 0.3125, -1.0)
}
fn close3(a: Vec3, b: Vec3, eps: f32) -> bool {
    near(a.x, b.x, eps) && near(a.y, b.y, eps) && near(a.z, b.z, eps)
}
fn sub(a: Vec3, b: Vec3) -> Vec3 {
    Vec3::new(a.x - b.x, a.y - b.y, a.z - b.z)
}
fn dot(a: Vec3, b: Vec3) -> f32 {
    a.x * b.x + a.y * b.y + a.z * b.z
}
fn cross(a: Vec3, b: Vec3) -> Vec3 {
    Vec3::new(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}
fn len(a: Vec3) -> f32 {
    dot(a, a).sqrt()
}
fn unit(a: Vec3) -> Vec3 {
    let l = len(a);
    Vec3::new(a.x / l, a.y / l, a.z / l)
}
fn dist(a: Vec3, b: Vec3) -> f32 {
    len(sub(a, b))
}
fn azimuth(a: Vec3) -> f32 {
    a.x.atan2(-a.z)
}
fn elevation(a: Vec3) -> f32 {
    a.y.atan2((a.x * a.x + a.z * a.z).sqrt())
}
fn pp(t: &FRect, p: &Placement, px: f32, py: f32) -> Vec3 {
    place_point(&CV, t, p, px, py)
}
fn centre_of(t: &FRect, p: &Placement) -> Vec3 {
    pp(t, p, t.x + t.width / 2.0, t.y + t.height / 2.0)
}

/// The billboard: wherever it is, a desktop is square to the line of sight at its centre (its normal points at the eye)
/// and upright along its meridian (its right edge level and heading round you, its top up toward the zenith), its centre
/// in its slot's direction turned by the orbit, at the slot centre's distance times `distance`. Its shape never changes:
/// the corners keep their distances from its centre.
fn check_facing(t: &FRect, p: &Placement) {
    let cv = &CV;
    let (cx, cy) = (t.x + t.width / 2.0, t.y + t.height / 2.0);
    let (wx, wy) = ((2.0 * cx / cv.view_w - 1.0) * cv.half_w, (1.0 - 2.0 * cy / cv.view_h) * cv.half_h);
    let a = wx.atan2(1.0) + p.x;
    let e = wy.atan2((1.0 + wx * wx).sqrt()) + p.y;
    let r = (1.0 + wx * wx + wy * wy).sqrt() * p.distance;
    let c = pp(t, p, cx, cy);
    assert!(close3(c, Vec3::new(r * e.cos() * a.sin(), r * e.sin(), -r * e.cos() * a.cos()), 1e-5 * r), "{t:?} {p:?}");
    // Tangents from points symmetric about the centre (on a cylinder their chord is parallel to the centre's tangent), far
    // apart for precision: the surface goes on past the tile's edges.
    let du = if p.curvature > 0.0 { t.width / 2.0 } else { 400.0 };
    let dv = 400.0;
    let right = unit(sub(pp(t, p, cx + du, cy), pp(t, p, cx - du, cy)));
    let up = unit(sub(pp(t, p, cx, cy - dv), pp(t, p, cx, cy + dv)));
    let (n, eye) = (cross(right, up), unit(Vec3::new(-c.x, -c.y, -c.z)));
    assert!(len(cross(n, eye)) < 3e-5 && dot(n, eye) > 0.0, "{t:?} {p:?}"); // faces the eye
    assert!(close3(right, Vec3::new(a.cos(), 0.0, a.sin()), 3e-5), "{t:?} {p:?}"); // level: never rolled
    assert!(close3(up, Vec3::new(-a.sin() * e.sin(), e.cos(), a.cos() * e.sin()), 3e-5), "{t:?} {p:?}"); // its meridian
    if p.curvature == 0.0 {
        assert!(near(
            dist(pp(t, p, t.x, t.y), pp(t, p, t.x + t.width, t.y + t.height)),
            t.width.hypot(t.height) * PX_WORLD,
            1e-5 * r
        ));
        for (px, py) in [(t.x, t.y), (t.x + t.width, t.y + t.height), (t.x - 40.0, cy)] {
            assert!(near(dot(sub(pp(t, p, px, py), c), eye), 0.0, 1e-5 * r)); // flat: one plane, square to the eye
        }
    } else {
        // curved: the edges bend toward you, symmetrically
        let l = dot(sub(pp(t, p, t.x, cy), c), eye);
        let rr = dot(sub(pp(t, p, t.x + t.width, cy), c), eye);
        assert!(l > 0.0 && near(l, rr, 1e-5 * r));
    }
}

#[test]
fn placement_flat_canvas_and_mesh() {
    // A centred desktop with the default placement is the old flat canvas plane, exactly (same vertices as the
    // pre-placement quads), one column; so is anything past its edge (its frame / halo).
    for px in [480.0f32, 960.0, 1440.0, 100.0] {
        for py in [330.0f32, 870.0, 10.0] {
            assert!(close3(pp(&CENTRED, &dflt(), px, py), plane(px, py), 1e-6));
        }
    }
    let flat_mesh = tile_mesh(&CV, &CENTRED, &dflt(), &CENTRED);
    assert!(flat_mesh.len() == 4 && close3(flat_mesh[0].world, plane(480.0, 330.0), 1e-5) && close3(flat_mesh[3].world, plane(1440.0, 870.0), 1e-5));
    assert!(near(flat_mesh[0].u, 0.0, 1e-6) && near(flat_mesh[0].v, 0.0, 1e-6) && near(flat_mesh[3].u, 1.0, 1e-6) && near(flat_mesh[3].v, 1.0, 1e-6));
    assert!(near(flat_mesh[1].py, 870.0, 1e-4) && near(flat_mesh[2].px, 1440.0, 1e-4)); // top, bottom per column
}

#[test]
fn placement_billboard_everywhere() {
    let pi = PI;
    let tiles = [
        CENTRED,
        TILE,
        rect(1300.5, 700.25, 512.3, 288.7),
        rect(0.0, 0.0, 300.0, 200.0),
        rect(1700.0, 1100.0, 220.0, 100.0),
        rect(-1960.0, 60.0, 1920.0, 1080.0),
        rect(3900.75, -400.5, 640.0, 360.0),
    ];
    let placements = [
        dflt(),
        p4(pi, 0.0, 1.0, 0.0),
        p4(-pi, 0.0, 1.0, 0.0),
        p4(HALF_PI, 0.0, 1.0, 0.0),
        p4(-2.2, 0.0, 1.0, 0.0),
        p4(0.0, HALF_PI, 1.0, 0.0),
        p4(0.0, -HALF_PI, 1.0, 0.0),
        p4(0.0, 0.7, 1.0, 0.0),
        p4(pi, HALF_PI, 1.0, 0.0),
        p4(-0.7, 0.3, 1.6, 1.0),
        p4(2.9, -1.2, 0.5, 2.0 * pi / 3.0),
        p4(0.05, -0.02, 1.37, 0.3),
        p4(-pi, -0.6, 2.0, 0.0),
        p4(1.0, 1.4, 0.75, HALF_PI),
    ];
    for t in &tiles {
        for p in &placements {
            check_facing(t, p);
        }
    }
}

#[test]
fn placement_off_centre_and_curved() {
    let tile = &TILE;
    let centred = &CENTRED;
    // An off-centre desktop at its slot already faces you: its centre stays where the plane put it (the same direction and
    // distance) and it turns toward you about it. This one is left of centre and a little up, so in depth its left edge
    // comes forward and its right edge goes back, and it tips to face down at you (top forward, bottom back).
    let c0 = pp(tile, &dflt(), 720.0, 570.0);
    assert!(close3(c0, plane(720.0, 570.0), 1e-5));
    assert!(pp(tile, &dflt(), 240.0, 570.0).z > -1.0 && pp(tile, &dflt(), 1200.0, 570.0).z < -1.0);
    assert!(pp(tile, &dflt(), 720.0, 300.0).z > -1.0 && pp(tile, &dflt(), 720.0, 840.0).z < -1.0);
    assert!(!close3(pp(tile, &dflt(), 240.0, 300.0), plane(240.0, 300.0), 1e-3)); // so no longer on the canvas plane
    // Curvature zero is flat; any curvature keeps the centre and its tangent frame in place.
    let curved = Placement { curvature: HALF_PI, ..dflt() };
    let k0 = pp(centred, &curved, 960.0, 600.0);
    assert!(close3(k0, plane(960.0, 600.0), 1e-5));
    let k1 = pp(centred, &curved, 960.01, 600.0);
    assert!(near((k1.x - k0.x) / (0.01 * PX_WORLD), 1.0, 1e-2) && near(k1.z - k0.z, 0.0, 1e-6)); // tangent along +x
    assert!(close3(pp(tile, &curved, 720.0, 570.0), c0, 1e-5)); // off-centre too
    // Edges bend toward the viewer symmetrically, aspect not stretched: arc length == flat width, height unchanged.
    let (left, right) = (pp(centred, &curved, 480.0, 600.0), pp(centred, &curved, 1440.0, 600.0));
    assert!(left.z > -1.0 && near(left.z, right.z, 1e-6) && near(left.x - k0.x, -(right.x - k0.x), 1e-6));
    let tw = 960.0 * PX_WORLD;
    assert!(near(right.x - left.x, 2.0 * (tw / HALF_PI) * (HALF_PI / 2.0).sin(), 1e-5)); // chord of a 90 degree arc
    for t in [centred, tile] {
        let mesh = tile_mesh(&CV, t, &curved, t);
        assert!(mesh.len() > 4 && mesh.len() % 2 == 0);
        let mut arc = 0.0f32;
        for i in (2..mesh.len()).step_by(2) {
            arc += dist(mesh[i].world, mesh[i - 2].world);
        }
        assert!(near(arc, tw, tw * 2e-3));
        assert!(near(dist(mesh[0].world, mesh[1].world), 540.0 * PX_WORLD, 1e-5)); // vertical edges stay straight and full height
        for i in (2..mesh.len()).step_by(2) {
            assert!(mesh[i].u > mesh[i - 2].u); // texture runs monotonically along the arc
        }
    }
    // A frame/halo rect past the tile continues on the same cylinder.
    let beyond = pp(centred, &curved, 1480.0, 600.0);
    assert!(beyond.z > right.z && beyond.x > right.x);
}

#[test]
fn placement_orbit_and_distance() {
    let (tile, centred, pi) = (&TILE, &CENTRED, PI);
    let c0 = pp(tile, &dflt(), 720.0, 570.0);
    let tile_diagonal = dist(plane(240.0, 300.0), plane(1200.0, 840.0));
    // Position: x / y orbit the desktop round the eye (a rigid rotation about it): x turns it round you about the vertical,
    // y lifts it along its own meridian; the shape and the distance never change.
    let a0 = azimuth(c0); // the slot's own azimuth (this tile is left of centre)
    assert!(a0 < 0.0);
    let round = Placement { x: HALF_PI, ..dflt() }; // a quarter turn to the right
    let r0 = pp(tile, &round, 720.0, 570.0);
    assert!(near(azimuth(r0), a0 + HALF_PI, 1e-5) && near(r0.y, c0.y, 1e-6) && near(len(r0), len(c0), 1e-6));
    assert!(near(dist(pp(tile, &round, 240.0, 300.0), pp(tile, &round, 1200.0, 840.0)), tile_diagonal, 1e-5));
    // It keeps facing you as at its slot: the angle between the view ray and the desktop's edges is unchanged.
    let angle = |a: Vec3, b: Vec3| (dot(a, b) / (len(a) * len(b))).clamp(-1.0, 1.0).acos();
    assert!(near(angle(r0, pp(tile, &round, 240.0, 570.0)), angle(c0, pp(tile, &dflt(), 240.0, 570.0)), 1e-5));
    let behind = Placement { x: pi, ..dflt() }; // directly behind you, whatever the slot
    assert!(near(azimuth(pp(tile, &behind, 720.0, 570.0)).abs(), pi - a0.abs(), 1e-5) && pp(tile, &behind, 720.0, 570.0).z > 0.0);
    let lifted = Placement { y: pi / 4.0, ..dflt() }; // up along its own meridian
    let l0 = pp(tile, &lifted, 720.0, 570.0);
    assert!(near(elevation(l0), elevation(c0) + pi / 4.0, 1e-5) && near(azimuth(l0), a0, 1e-5) && near(len(l0), len(c0), 1e-6));
    let overhead = Placement { y: HALF_PI - elevation(c0), ..dflt() }; // straight up: still facing you (down at you), still level
    let o0 = pp(tile, &overhead, 720.0, 570.0);
    assert!(near(o0.x, 0.0, 1e-5) && near(o0.z, 0.0, 1e-5) && near(o0.y, len(c0), 1e-5));
    let both = p4(-0.7, 0.3, 1.0, 1.0); // the orbit is a rigid rotation about the eye of the whole (curved) desktop
    let unmoved = p4(0.0, 0.0, 1.0, 1.0);
    for px in [240.0f32, 720.0, 1200.0] {
        let (a, b) = (pp(tile, &unmoved, px, 300.0), pp(tile, &both, px, 300.0));
        assert!(near(len(a), len(b), 1e-5));
        let opposite = 1200.0 - (px - 240.0);
        assert!(near(dist(a, pp(tile, &unmoved, opposite, 840.0)), dist(b, pp(tile, &both, opposite, 840.0)), 1e-5));
    }
    // Distance: twice as far along the same direction, same physical size -> half the apparent size.
    let far = Placement { distance: 2.0, ..dflt() };
    let f0 = pp(tile, &far, 720.0, 570.0);
    assert!(close3(f0, Vec3::new(2.0 * c0.x, 2.0 * c0.y, -2.0), 1e-5));
    assert!(near(dist(pp(tile, &far, 240.0, 300.0), pp(tile, &far, 1200.0, 840.0)), tile_diagonal, 1e-5));
    let (g0, gr) = (pp(centred, &far, 960.0, 600.0), pp(centred, &far, 1440.0, 600.0));
    assert!(close3(g0, Vec3::new(0.0, 0.0, -2.0), 1e-5) && near(gr.z, -2.0, 1e-6));
    assert!(near((gr.x / -gr.z) - (g0.x / -g0.z), 0.5 * (plane(1440.0, 600.0).x - plane(960.0, 600.0).x), 1e-6));
    // Zooming into a desktop fades its placement to flat-in-front, keeping curvature; neighbours unaffected.
    assert!(near(zoom_weight(1.0, 1.0, 1), 1.0, 1e-6) && near(zoom_weight(1.0, 1.0, 0), 0.0, 1e-6) && near(zoom_weight(0.0, 1.0, 1), 0.0, 1e-6));
    assert!(near(zoom_weight(1.0, 1.25, 1), 0.75, 1e-6) && near(zoom_weight(0.5, 1.0, 1), 0.5, 1e-6));
    let mixed = p4(0.4, -0.2, 1.6, 1.0);
    let (gone, half, kept) = (faded(&mixed, 1.0), faded(&mixed, 0.5), faded(&mixed, 0.0));
    assert!(gone.x == 0.0 && gone.y == 0.0 && gone.distance == 1.0 && gone.curvature == 1.0);
    assert!(near(half.x, 0.2, 1e-6) && near(half.y, -0.1, 1e-6) && near(half.distance, 1.3, 1e-6) && half.curvature == 1.0);
    assert!(same_placement(&kept, &mixed));
}

#[test]
fn zooming_keeps_every_desktop_where_dragged() {
    // Drawn while zooming (regression, 2026-11-02: zooming in from the overview and back out must keep every desktop where
    // it was dragged, in every viewing mode; zoomed_placements takes no mode). However far a desktop was dragged (beside
    // you, overhead, behind you), at every zoom: every desktop keeps its overview direction round you; the zoomed one comes
    // to distance 1 there (filling the view, curvature kept); every other one is drawn exactly as in the overview. Nothing
    // goes back to its default slot or snaps straight ahead.
    let cv = &CV;
    let pi = PI;
    let ov = overview_layout(&EQ, 1920.0, 1200.0, 40.0);
    let other = p4(-0.4, 0.1, 1.3, 0.5);
    {
        let own = vec![other, p4(0.5, 0.2, 1.6, 1.0), dflt()];
        assert!(zoomed_placements(cv, &ov, &ov, &own, 0.0, 1.0).len() == 3);
        for i in 0..3 {
            assert!(same_placement(&zoomed_placements(cv, &ov, &ov, &own, 0.0, 1.0)[i], &own[i])); // overview: unchanged
            assert!(same_placement(&zoomed_placements(cv, &ov, &ov, &own, -1.0, 0.4)[i], &own[i])); // clamped
        }
        assert!(zoomed_placements(cv, &ov, &ov, &own[..1], 1.0, 1.0).len() == 3); // short list
    }
    // Dragged near ahead, beside you (past the old 60 deg cutoff), overhead, behind you, and to the far side; with the
    // undragged default in the mix (it must not move either: the regression that reset the others to their slots).
    let drags = [
        p4(0.5, 0.2, 1.6, 1.0),
        p4(1.2, 0.3, 1.6, 1.0),
        p4(0.2, 1.3, 1.0, 0.0),
        p4(3.0, 0.0, 1.0, 0.0),
        p4(-1.4, -0.5, 0.5, 0.3),
        dflt(),
    ];
    for target in 0..3usize {
        for drag in &drags {
            let mut own = vec![other, p4(0.3, -0.2, 0.5, 0.0), p4(-0.9, 0.4, 1.2, 0.0)];
            own[target] = *drag;
            for z in [0.0f32, 0.25, 0.5, 0.75, 1.0] {
                let tl = blended_layout(&EQ, 1920.0, 1200.0, 40.0, z, target as f32);
                let pl = zoomed_placements(cv, &ov, &tl, &own, z, target as f32);
                for i in 0..3 {
                    let (was, now) = (centre_of(&ov[i], &own[i]), centre_of(&tl[i], &pl[i]));
                    assert!(near(remainder(azimuth(now) - azimuth(was), 2.0 * pi), 0.0, 1e-4) || near(now.y.abs(), len(now), 1e-4));
                    assert!(near(elevation(now), elevation(was), 1e-4)); // never moves round you
                    assert!(pl[i].curvature == own[i].curvature);
                    check_facing(&tl[i], &pl[i]);
                    if i == target {
                        continue;
                    }
                    // Not zoomed: seen exactly as in the overview (every corner's direction from the eye, so size and
                    // curvature too); only scaled about the eye, so it is never nearer than it was.
                    for (u, v) in [(0.0f32, 0.0f32), (1.0, 0.0), (0.5, 1.0), (1.0, 1.0)] {
                        assert!(close3(
                            unit(pp(&tl[i], &pl[i], tl[i].x + u * tl[i].width, tl[i].y + v * tl[i].height)),
                            unit(pp(&ov[i], &own[i], ov[i].x + u * ov[i].width, ov[i].y + v * ov[i].height)),
                            1e-4
                        ));
                    }
                    assert!(len(now) >= len(was) * (1.0 - 1e-5));
                }
            }
            // Full zoom: the zoomed one is at distance 1 where it was dragged (not straight ahead unless it was), the full
            // view's size; zooming back out gives every desktop its own placement back.
            let zl = zoomed_layout(&EQ, 1920.0, 1200.0, 40.0, target as f32);
            let inn = zoomed_placements(cv, &ov, &zl, &own, 1.0, target as f32);
            assert!(inn[target].distance == 1.0 && near(len(centre_of(&zl[target], &inn[target])), 1.0, 1e-5));
            if !same_placement(drag, &dflt()) || target != 1 {
                assert!(!close3(centre_of(&zl[target], &inn[target]), Vec3::new(0.0, 0.0, -1.0), 1e-2));
            }
            let flat = Placement { curvature: 0.0, ..inn[target] };
            let t = &zl[target];
            assert!(near(
                dist(pp(t, &flat, t.x, t.y), pp(t, &flat, t.x + t.width, t.y + t.height)),
                dist(plane(t.x, t.y), plane(t.x + t.width, t.y + t.height)),
                1e-5
            ));
            let out = zoomed_placements(cv, &ov, &ov, &own, 0.0, target as f32);
            for i in 0..3 {
                assert!(same_placement(&out[i], &own[i]));
            }
        }
    }
    // Zoomed in, the zoomed desktop is drawn on top of one dragged nearer; the overview keeps back-to-front order.
    let back_to_front = vec![2usize, 1, 0];
    assert!(zoom_draw_order(back_to_front.clone(), 1.0, 1) == vec![2, 0, 1]);
    assert!(zoom_draw_order(back_to_front.clone(), 0.5, 2) == vec![1, 0, 2]);
    assert!(zoom_draw_order(back_to_front.clone(), 0.49, 1) == back_to_front && zoom_draw_order(back_to_front.clone(), 0.0, 2) == back_to_front);
    assert!(zoom_draw_order(back_to_front.clone(), 1.0, 7) == back_to_front);
}

#[test]
fn anchor_mode_keeps_a_resized_size() {
    // Anchor mode keeps a resized desktop's size (regression, 2026-11-03: a desktop resized bigger than the zoom-fit size
    // shrank back to it on zoom-in). keep_size: a zoomed desktop seen bigger than the fit is seen exactly as in the overview
    // at every zoom (each corner's direction from the eye), like every other desktop. Without it (follow / locked) it fits.
    // Zooming in must still zoom (regression, 2026-11-03: keeping every size made overview -> zoom a no-op in anchor mode):
    // a desktop at its default size, or resized but still smaller than the fit, grows to fill the view (distance 1).
    let cv = &CV;
    let pi = PI;
    let keep = ZoomOptions { keep_size: true, ..ZoomOptions::default() };
    let ov = overview_layout(&EQ, 1920.0, 1200.0, 40.0);
    let width_deg = |t: &FRect, p: &Placement| {
        let cy = t.y + t.height / 2.0;
        let (l, r) = (unit(pp(t, p, t.x, cy)), unit(pp(t, p, t.x + t.width, cy)));
        dot(l, r).clamp(-1.0, 1.0).acos() * 180.0 / pi
    };
    for target in 0..3usize {
        let tf = target as f32;
        for d in [0.2f32, 0.25, 0.5, 1.0, 1.6] {
            // bigger than the fit (0.2, 0.25); smaller (0.5, 1, 1.6)
            let bigger = d < 0.3;
            let mut own = vec![p4(-0.4, 0.1, 1.3, 0.0), dflt(), p4(0.3, -0.1, 0.8, 0.0)];
            own[target] = p4(0.2, 0.1, d, 0.0);
            for z in [0.0f32, 0.5, 1.0] {
                let tl = blended_layout(&EQ, 1920.0, 1200.0, 40.0, z, tf);
                let kept = zoomed_placements_with(cv, &ov, &tl, &own, z, tf, keep);
                for i in 0..3 {
                    if i == target && !bigger && z > 0.0 {
                        continue; // it grows (below)
                    }
                    for (u, v) in [(0.0f32, 0.0f32), (1.0, 0.0), (0.5, 0.5), (1.0, 1.0)] {
                        assert!(close3(
                            unit(pp(&tl[i], &kept[i], tl[i].x + u * tl[i].width, tl[i].y + v * tl[i].height)),
                            unit(pp(&ov[i], &own[i], ov[i].x + u * ov[i].width, ov[i].y + v * ov[i].height)),
                            1e-4
                        ));
                    }
                }
            }
            let zl = zoomed_layout(&EQ, 1920.0, 1200.0, 40.0, tf);
            let kept = zoomed_placements_with(cv, &ov, &zl, &own, 1.0, tf, keep);
            let fit = zoomed_placements(cv, &ov, &zl, &own, 1.0, tf);
            let was = width_deg(&ov[target], &own[target]);
            assert!(fit[target].distance == 1.0);
            if bigger {
                assert!(kept[target].distance < 1.0 && near(width_deg(&zl[target], &kept[target]), was, 1e-2));
                assert!(width_deg(&zl[target], &fit[target]) < was - 1.0); // fitting would have shrunk it
            } else {
                assert!(same_placement(&kept[target], &fit[target])); // zoomed in, it fills the view
                assert!(width_deg(&zl[target], &kept[target]) > was + 1.0); // it grew: the zoom zooms
            }
            // Halfway in, it is on its way: never smaller than in the overview, never past the fit.
            let hl = blended_layout(&EQ, 1920.0, 1200.0, 40.0, 0.5, tf);
            let half = zoomed_placements_with(cv, &ov, &hl, &own, 0.5, tf, keep);
            let h = width_deg(&hl[target], &half[target]);
            assert!(h > was - 1e-2 && (bigger || h < width_deg(&zl[target], &kept[target]) + 1e-2));
            for i in 0..3 {
                if i == target {
                    continue;
                }
                assert!(same_placement(&kept[i], &fit[i])); // the others are the same in every mode
            }
        }
    }
}

#[test]
fn look_turn_is_a_camera_rotation() {
    // The switch's view turn (look_turn) is a camera rotation only: identity at rest, a proper rotation, and at look 1 the
    // target's direction (slot plus where it was dragged) comes exactly where the desktop being left was, upright.
    let cv = &CV;
    let ov = overview_layout(&EQ, 1920.0, 1200.0, 40.0);
    let mul = |m: &[f32; 9], p: Vec3| {
        Vec3::new(m[0] * p.x + m[1] * p.y + m[2] * p.z, m[3] * p.x + m[4] * p.y + m[5] * p.z, m[6] * p.x + m[7] * p.y + m[8] * p.z)
    };
    let identity = [1.0f32, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    let own = vec![p4(-0.4, 0.1, 1.3, 0.5), p4(0.2, -0.1, 1.0, 0.0), p4(1.2, 0.3, 1.6, 1.0)];
    assert!(look_turn(cv, &ov, &own, 1, 2, 0.0) == identity && look_turn(cv, &ov, &own, 1, 7, 1.0) == identity);
    assert!(look_turn(&Canvas::default(), &ov, &own, 1, 2, 1.0) == identity);
    for (from, to) in [(1usize, 2usize), (2, 0), (0, 1)] {
        for u in [0.25f32, 0.5, 1.0] {
            let m = look_turn(cv, &ov, &own, from, to, u);
            let (ex, ey, ez) = (mul(&m, Vec3::new(1.0, 0.0, 0.0)), mul(&m, Vec3::new(0.0, 1.0, 0.0)), mul(&m, Vec3::new(0.0, 0.0, 1.0)));
            assert!(near(len(ex), 1.0, 1e-5) && near(len(ey), 1.0, 1e-5) && near(dot(ex, ey), 0.0, 1e-5) && near(dot(ex, ez), 0.0, 1e-5));
            assert!(close3(cross(ex, ey), ez, 1e-5)); // no mirror
            if u < 1.0 {
                continue;
            }
            let (a, b) = (unit(centre_of(&ov[from], &own[from])), unit(centre_of(&ov[to], &own[to])));
            assert!(close3(mul(&m, b), a, 1e-4));
            // Upright: the target's up (toward the zenith along its meridian) lands on the left desktop's up.
            let up = |d: Vec3| unit(sub(Vec3::new(0.0, 1.0, 0.0), Vec3::new(d.x * d.y, d.y * d.y, d.z * d.y)));
            assert!(close3(mul(&m, up(b)), up(a), 1e-4));
        }
    }
    // Every frame of a switch draws every desktop where it was dragged (the turn is the camera's, never a placement): the
    // placements the renderer resolves keep each desktop's overview direction and land back on its own placement.
    let mut v = ViewAnimator::new(220.0, 300.0);
    v.set_target(true, 0, 40, 0.0);
    v.advance(1000.0);
    v.set_target(true, 2, 40, 1000.0);
    let mut t = 1000.0;
    while t <= 1750.0 {
        v.advance(t);
        let tl = blended_layout(&EQ, 1920.0, 1200.0, 40.0, v.zoom(), v.position());
        let pl = zoomed_placements(cv, &ov, &tl, &own, v.zoom(), v.position());
        for i in 0..3 {
            let (was, now) = (unit(centre_of(&ov[i], &own[i])), unit(centre_of(&tl[i], &pl[i])));
            assert!(close3(was, now, 1e-4));
        }
        if v.zoom() == 0.0 {
            for i in 0..3 {
                assert!(same_placement(&pl[i], &own[i]));
            }
        }
        t += 25.0;
    }
    assert!(!v.switching() && look_turn(cv, &ov, &own, v.look_from() as usize, v.look_to() as usize, v.look()) == identity);
}

#[test]
fn ease_toward_short_way() {
    // The orbit's x eases the short way round: a desktop crossing behind you (+-pi) stays behind you.
    let from_behind = Placement { x: 3.1, ..dflt() };
    let to_behind = Placement { x: -3.1, ..dflt() };
    let mid = ease_toward(&from_behind, &to_behind, PLACEMENT_EASE_MS, PLACEMENT_EASE_MS);
    assert!(mid.x < -3.1 && mid.x > -3.1 - 0.1); // past -pi from below: the 0.08 rad gap, not the 6.2 rad way round
    assert!(pp(&TILE, &mid, 720.0, 570.0).z > 0.0);
    assert!(ease_toward(&from_behind, &to_behind, 100.0 * PLACEMENT_EASE_MS, PLACEMENT_EASE_MS).x == -3.1); // lands exactly
    let near_front = Placement { x: 0.3, ..dflt() };
    assert!(near(ease_toward(&near_front, &dflt(), PLACEMENT_EASE_MS, PLACEMENT_EASE_MS).x, 0.3 * (-1.0f32).exp(), 1e-6)); // as before
    // Every field eases (and lands); same_placement compares all four.
    let to = p4(-0.4, 0.2, 1.5, 0.8);
    let e = ease_toward(&dflt(), &to, PLACEMENT_EASE_MS, PLACEMENT_EASE_MS);
    let k = 1.0 - (-1.0f32).exp();
    assert!(near(e.x, -0.4 * k, 1e-6) && near(e.y, 0.2 * k, 1e-6) && near(e.distance, 1.0 + 0.5 * k, 1e-6) && near(e.curvature, 0.8 * k, 1e-6));
    assert!(same_placement(&ease_toward(&e, &to, 1e9, PLACEMENT_EASE_MS), &to));
    for f in 0..4 {
        let mut other = to;
        match f {
            0 => other.x += 0.01,
            1 => other.y += 0.01,
            2 => other.distance += 0.01,
            _ => other.curvature += 0.01,
        }
        assert!(!same_placement(&other, &to));
    }
}

#[test]
fn glide_toward_curve() {
    // A glide (bumping, view.py GLIDE_MS): one smootherstep curve from where a desktop was drawn to its new placement.
    let from = Placement::new(-0.2, 0.1, 1.0, 0.0, 0.0);
    let to = Placement::new(0.4, -0.3, 0.5, 1.0, 0.1);
    assert!(same_placement(&glide_toward(&from, &to, 0.0), &from)); // starts where it was drawn: no jump
    assert!(same_placement(&glide_toward(&from, &to, 1.0), &to) && same_placement(&glide_toward(&from, &to, 7.0), &to)); // lands exactly
    assert!(same_placement(&glide_toward(&from, &to, -1.0), &from));
    let half = glide_toward(&from, &to, 0.5);
    assert!(
        near(half.x, 0.1, 1e-6)
            && near(half.y, -0.1, 1e-6)
            && near(half.distance, 0.75, 1e-6)
            && near(half.curvature, 0.5, 1e-6)
            && near(half.roll, 0.05, 1e-6)
    ); // halfway at half time
    let mut last = from.x;
    for i in 1..=100 {
        // every field moves one way only, never past the end (no overshoot)
        let p = glide_toward(&from, &to, i as f32 / 100.0);
        assert!(p.x >= last - 1e-7 && p.x <= to.x + 1e-7 && p.y <= from.y + 1e-7 && p.y >= to.y - 1e-7);
        last = p.x;
    }
    // Eased in and out: it starts and lands with no jump in speed (smootherstep's slope is 0 at both ends).
    assert!(glide_toward(&from, &to, 0.01).x - from.x < 0.01 * (to.x - from.x) * 0.01);
    assert!(to.x - glide_toward(&from, &to, 0.99).x < 0.01 * (to.x - from.x) * 0.01);
    // Every desktop of the frame takes the same t, so the pair's configuration moves along one straight line between two
    // that were apart: a pushed desktop and the one pushing it slide out of each other's way together.
    let (a0, a1, b0, b1) = (p4(0.0, 0.0, 1.0, 0.0), p4(0.3, 0.0, 1.0, 0.0), p4(0.25, 0.0, 1.0, 0.0), p4(0.55, 0.0, 1.0, 0.0));
    for i in 0..=10 {
        let t = i as f32 / 10.0;
        assert!(glide_toward(&b0, &b1, t).x - glide_toward(&a0, &a1, t).x >= 0.25 - 1e-6);
    }
    // The short way round, as ease_toward: behind you it stays behind you, and lands on the exact value.
    let behind_a = Placement { x: 3.1, ..dflt() };
    let behind_b = Placement { x: -3.1, ..dflt() };
    assert!(glide_toward(&behind_a, &behind_b, 0.5).x > 3.1 && glide_toward(&behind_a, &behind_b, 1.0).x == -3.1);
}

#[test]
fn step_placements_glides_then_eases() {
    // The renderer's per-frame step (wayland.cpp ease_placements): a glide frame takes every desktop along one curve, over
    // exactly its time, then easing goes on as before; a frame without one eases on from wherever the glide got to.
    let mut glide = PlacementGlide::default();
    let mut drawn = vec![p4(0.0, 0.0, 1.0, 0.0), p4(0.25, 0.0, 1.0, 0.0)];
    let pushed = vec![p4(0.3, 0.0, 1.0, 0.0), p4(0.55, 0.0, 1.0, 0.0)]; // the mover and the desktop it bumped
    assert!(!start_glide(&mut glide, &[], 0.0, 240) && glide.ms == 0.0); // nothing drawn yet: nothing to glide from
    assert!(!start_glide(&mut glide, &drawn, 1000.0, 0) && glide.ms == 0.0);
    assert!(start_glide(&mut glide, &drawn, 1000.0, 240) && glide.ms == 240.0);
    step_placements(&mut drawn, &pushed, 8.0, 1120.0, &mut glide); // half the time: half the way, both together
    assert!(near(drawn[0].x, 0.15, 1e-5) && near(drawn[1].x, 0.4, 1e-5) && glide.ms == 240.0);
    step_placements(&mut drawn, &pushed, 8.0, 1008.0, &mut glide); // early on it has barely started: eased in, not a jump
    assert!(drawn[0].x < 0.3 * 0.01);
    step_placements(&mut drawn, &pushed, 8.0, 1240.0, &mut glide); // its time is up: exactly there, and the glide is over
    assert!(same_placement(&drawn[0], &pushed[0]) && same_placement(&drawn[1], &pushed[1]) && glide.ms == 0.0);
    let next = vec![p4(0.4, 0.0, 1.0, 0.0), p4(0.65, 0.0, 1.0, 0.0)];
    step_placements(&mut drawn, &next, PLACEMENT_EASE_MS, 1300.0, &mut glide); // no glide: the quick exponential easing again
    assert!(near(drawn[0].x, 0.4 - 0.1 * (-1.0f32).exp(), 1e-5));
    assert!(start_glide(&mut glide, &drawn, 2000.0, 240)); // a glide that a plain frame interrupts: eased on from where it is
    step_placements(&mut drawn, &next, 8.0, 2060.0, &mut glide);
    let at = drawn[0];
    glide.ms = 0.0; // (set_placements without a glide, a roll change or a desktop change end it)
    step_placements(&mut drawn, &next, 8.0, 2068.0, &mut glide);
    assert!(drawn[0].x >= at.x && drawn[0].x <= next[0].x); // on toward the target, from there: no jump back
    let mut grown = vec![p4(0.4, 0.0, 1.0, 0.0)];
    step_placements(&mut grown, &next, 8.0, 2100.0, &mut glide); // a desktop added meanwhile starts at its target
    assert!(grown.len() == 2 && same_placement(&grown[1], &next[1]));
}

const ROW3: [FRect; 3] = [FRect::new(0.0, 330.0, 600.0, 540.0), FRect::new(660.0, 330.0, 600.0, 540.0), FRect::new(1320.0, 330.0, 600.0, 540.0)];

#[test]
fn draw_order_and_degenerate_input() {
    let cv = &CV;
    // Draw order: farthest first; equal-distance ties keep layout order; missing placements are flat.
    let pushed = [dflt(), p4(0.0, 0.0, 1.8, 0.0)];
    let order = draw_order(cv, &ROW3, &pushed);
    assert!(order.len() == 3 && order[0] == 1);
    let same_tiles = [rect(660.0, 330.0, 600.0, 540.0), rect(660.0, 330.0, 600.0, 540.0)];
    let tied = draw_order(cv, &same_tiles, &[]);
    assert!(tied[0] == 0 && tied[1] == 1);
    // Degenerate input yields nothing rather than NaNs.
    assert!(tile_mesh(cv, &rect(0.0, 0.0, 0.0, 10.0), &dflt(), &TILE).is_empty() && tile_mesh(cv, &TILE, &dflt(), &rect(0.0, 0.0, 0.0, 0.0)).is_empty());
    for m in tile_mesh(cv, &TILE, &p4(0.0, 0.0, 1.0, PI * 2.0 / 3.0), &rect(190.0, 250.0, 1060.0, 640.0)) {
        assert!(m.world.x.is_finite() && m.world.y.is_finite() && m.world.z.is_finite() && m.world.z < 0.0);
    }
    assert!(close3(pp(&CENTRED, &p4(0.0, 0.0, 0.0, 0.0), 480.0, 330.0), plane(480.0, 330.0), 1e-6)); // distance <= 0 reads as 1
}

#[test]
fn huge_desktop() {
    // Huge: the distance floor (wire 10, factor 0.1) shows a desktop ten times its size, far past the view.
    let huge = p4(0.0, 0.0, 0.1, 0.0);
    let seen = |a: Vec3| (a.x / -a.z, a.y / -a.z); // on the canvas plane, as projected
    for (px, py) in [(480.0f32, 330.0f32), (1440.0, 870.0), (960.0, 600.0)] {
        let (x, y) = seen(pp(&CENTRED, &huge, px, py));
        let flat = plane(px, py);
        assert!(near(x, 10.0 * flat.x, 1e-4) && near(y, 10.0 * flat.y, 1e-4));
    }
    assert!(near(seen(pp(&CENTRED, &huge, 1440.0, 870.0)).0, 2.5, 1e-4)); // its right edge: 5x the view's
    check_facing(&CENTRED, &huge);
    check_facing(&TILE, &p4(0.4, -0.2, 0.1, 0.0));
    check_facing(&TILE, &p4(-2.0, 0.3, 0.1, PI / 3.0));
    // The curved, closest desktop wraps round the eye (its edges may pass behind you: GL clips them); the mesh stays finite
    // and dense enough, and its centre stays straight ahead.
    let mesh = tile_mesh(&CV, &TILE, &p4(0.0, 0.0, 0.1, PI * 2.0 / 3.0), &rect(190.0, 250.0, 1060.0, 640.0));
    assert!(mesh.len() >= 10);
    for m in &mesh {
        assert!(m.world.x.is_finite() && m.world.y.is_finite() && m.world.z.is_finite());
    }
    assert!(pp(&CENTRED, &p4(0.0, 0.0, 0.1, PI * 2.0 / 3.0), 960.0, 600.0).z < 0.0);
    // The nearest is drawn last (over the others); zooming in still brings it to distance 1.
    let mixed = [dflt(), huge, p4(0.0, 0.0, 2.0, 0.0)];
    let o = draw_order(&CV, &ROW3, &mixed);
    assert!(o.len() == 3 && o[0] == 2 && o[2] == 1);
    assert!(near(faded(&huge, 1.0).distance, 1.0, 1e-6) && near(faded(&huge, 0.0).distance, 0.1, 1e-6));
}

// ---------------------------------------------------------------------------------------------------------------------
// A desktop changing shape live (SizeEase): the tile eases with the same smoothstep over the same time as zoom and spacing,
// and the layout built from it slides the neighbours along.

#[test]
fn size_ease() {
    const { assert!(VIEW_EASE_MS == 220.0) };
    let mut v = ViewAnimator::default(); // zoom goes the same way over the same time
    v.set_target(true, 0, 40, 1000.0);
    v.advance(1000.0);
    let d = VIEW_EASE_MS;
    let (fhd, uw, uhd) = (sz(1920.0, 1080.0), sz(2560.0, 1080.0), sz(3840.0, 2160.0));
    let mut e = SizeEase::default();
    assert!(e.value().is_none() && !e.animating() && e.eases() == 0);
    e.advance(fhd, 1000.0, false); // its first size just applies
    assert!(e.value().unwrap().w == 1920.0 && e.value().unwrap().h == 1080.0 && !e.animating());
    e.advance(fhd, 1100.0, true); // no change: nothing to do
    assert!(!e.animating() && e.eases() == 0);
    e.advance(uhd, 1200.0, true); // 16:9 to 16:9: same shape, nothing moves
    assert!(e.value().unwrap().w == 3840.0 && e.value().unwrap().h == 2160.0 && !e.animating() && e.eases() == 0);
    e.advance(fhd, 1300.0, true);
    assert!(!e.animating() && e.eases() == 0);
    e.advance(uw, 2000.0, true); // to 21:9: starts from where it is
    assert!(e.animating() && e.eases() == 1 && near(e.value().unwrap().w, 1920.0, 1e-3) && e.value().unwrap().h == 1080.0);
    let shape_at = |e: &mut SizeEase, t: f64| {
        e.advance(uw, t, true);
        let s = e.value().unwrap();
        s.w / s.h
    };
    let (a0, a1) = (1920.0f32 / 1080.0, 2560.0f32 / 1080.0);
    let mut last = a0;
    let mut t = 2010.0;
    while t < 2000.0 + f64::from(2000.0f32 + d) - 2000.0 {
        let a = shape_at(&mut e, t);
        assert!(a > last && a < a1); // always on its way, never past it
        assert!(near(a, a0 + (a1 - a0) * smoothstep(((t - 2000.0) / f64::from(d)) as f32), 1e-4));
        v.advance(1000.0 + (t - 2000.0));
        assert!(near((a - a0) / (a1 - a0), v.zoom(), 1e-3)); // in step with a zoom started at the same moment
        last = a;
        t += 10.0;
    }
    shape_at(&mut e, f64::from(2000.0 + d));
    assert!(!e.animating() && e.value().unwrap().w == 2560.0 && e.value().unwrap().h == 1080.0); // lands exactly on the size
    // A change mid-way carries on from what is drawn, with no jump.
    e.advance(fhd, 3000.0, true);
    e.advance(fhd, f64::from(3000.0 + d / 2.0), true);
    let mid = e.value().unwrap().w / e.value().unwrap().h;
    assert!(near(mid, (a0 + a1) / 2.0, 1e-4));
    e.advance(uw, f64::from(3000.0 + d / 2.0), true);
    assert!(e.animating() && e.eases() == 3 && near(e.value().unwrap().w / e.value().unwrap().h, mid, 1e-4));
    e.advance(uw, f64::from(3000.0 + 2.0 * d), true);
    assert!(!e.animating() && e.value().unwrap().w == 2560.0);
    // animate false: the new shape applies at once. A zero duration never eases.
    e.advance(fhd, 4000.0, false);
    assert!(!e.animating() && e.value().unwrap().w == 1920.0 && e.eases() == 3);
    let mut none = SizeEase::new(0.0);
    none.advance(fhd, 0.0, true);
    none.advance(uw, 1.0, true);
    assert!(!none.animating() && none.value().unwrap().w == 2560.0);
    // The middle desktop widening: the row is laid out from the eased sizes, so every drawn frame lies between the two
    // layouts: the tile widens, the right neighbour slides right, and (the row being limited by the view's width) all three
    // share a lower height, each step the eased share of the whole move.
    let mut m = SizeEase::default();
    m.advance(fhd, 0.0, false);
    let layout = |mid_size: Size| overview_layout(&[fhd, mid_size, fhd], 1920.0, 1200.0, 40.0);
    let (a, b) = (layout(fhd), layout(uw));
    assert!(b[1].width > a[1].width && b[2].x > a[2].x && b[0].height < a[0].height);
    let dd = f64::from(d);
    let mut t = 0.0f64;
    while t <= dd {
        m.advance(uw, t, true);
        let now = layout(m.value().unwrap());
        let ws = (now[1].width / now[1].height - a[1].width / a[1].height) / (b[1].width / b[1].height - a[1].width / a[1].height);
        assert!((-1e-4..=1.0 + 1e-4).contains(&ws));
        for i in 0..3 {
            assert!(now[i].x >= a[i].x.min(b[i].x) - 0.5 && now[i].x <= a[i].x.max(b[i].x) + 0.5);
            assert!(now[i].width >= a[i].width.min(b[i].width) - 0.5 && now[i].width <= a[i].width.max(b[i].width) + 0.5);
        }
        if t + 22.0 <= dd {
            assert!(near(ws, smoothstep((t / dd) as f32), 1e-3));
        }
        t += 22.0;
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Against the C++ build, for what view_test.cpp does not cover. The strings are the C++ overview_geometry_json's own
// output; the hash is FNV-1a 64 of a dump of every float's bits from the pointer's marks, disc and ghost, glass_mesh,
// glass_to_desktop, desktop_tilt, tile_mesh and drop_frame over the same inputs (a scratch C++ program built with
// `g++ -O3 -std=c++20 -Ispike/include dump.cpp spike/src/view.cpp`, that writes the dump the way `cpp_dump` does).

const ROW_TILES: [FRect; 3] = [
    FRect::new(40.0, 435.0, 586.6667, 330.0),
    FRect::new(666.6667, 435.0, 586.6667, 330.0),
    FRect::new(1293.3334, 435.0, 586.6667, 330.0),
];
const HALVES: [[f32; 2]; 5] = [[0.5, 0.3125], [0.123455, 1e-6], [3e38, 2.5e37], [1e20, 0.000005], [0.42, 0.27]];

fn json_sets() -> Vec<Vec<FRect>> {
    vec![
        ROW_TILES.to_vec(),
        vec![rect(0.125, 0.375, 2.5, 1.005)],
        vec![rect(-8191.995, 8191.995, 8192.0, 0.005), rect(1.0, 2.0, 3.0, 4.0), rect(0.015, 0.025, 0.035, 0.045), rect(100.125, 200.875, 0.625, 7.995)],
        vec![rect(1.0, 2.0, 3.0, 0.0)],
        vec![rect(1.0, 2.0, 3.0, 4.0); 5],
    ]
}

#[test]
fn overview_geometry_json_matches_cpp() {
    let row = r#"[[40.00,435.00,586.67,330.00],[666.67,435.00,586.67,330.00],[1293.33,435.00,586.67,330.00]]}}"#;
    let one = r#"[[0.12,0.38,2.50,1.00]]}}"#; // printf's ties to even on the exact value: 0.125 is 0.12, 0.375 0.38
    let four = r#"[[-8192.00,8192.00,8192.00,0.00],[1.00,2.00,3.00,4.00],[0.01,0.03,0.04,0.05],[100.12,200.88,0.62,7.99]]}}"#;
    let heads = [
        r#"{"overview_geometry":{"w":1920,"h":1200,"half":[0.50000,0.31250],"tiles":["#,
        r#"{"overview_geometry":{"w":1920,"h":1200,"half":[0.12346,0.00000],"tiles":["#,
        // snprintf into char[96]: the 95 bytes kept end mid-number, and the tiles follow straight on
        r#"{"overview_geometry":{"w":1920,"h":1200,"half":[300000000549775575777803994281145270272.00000,2"#,
        r#"{"overview_geometry":{"w":1920,"h":1200,"half":[100000002004087734272.00000,0.00000],"tiles":["#,
        r#"{"overview_geometry":{"w":1920,"h":1200,"half":[0.42000,0.27000],"tiles":["#,
    ];
    let sets = json_sets();
    for (set, tail) in sets.iter().zip([Some(row), Some(one), Some(four), None, None]) {
        for (h, head) in HALVES.iter().zip(heads) {
            let got = overview_geometry_json(set, 1920, 1200, h[0], h[1]);
            match tail {
                Some(tail) => assert_eq!(got, format!("{head}{}", &tail[1..])),
                None => assert_eq!(got, ""), // a zero-height tile; five desktops
            }
        }
    }
    assert_eq!(overview_geometry_json(&ROW_TILES, 0, 1200, 0.5, 0.3), "");
    assert_eq!(overview_geometry_json(&ROW_TILES, 8193, 1200, 0.5, 0.3), "");
    assert_eq!(
        overview_geometry_json(&ROW_TILES, 8192, 8192, 0.5, 0.3),
        format!(r#"{{"overview_geometry":{{"w":8192,"h":8192,"half":[0.50000,0.30000],"tiles":{row}"#)
    );
}

struct Dump(String);

impl Dump {
    fn f(&mut self, v: f32) {
        self.0 += &format!("{:08x} ", v.to_bits());
    }
    fn v3(&mut self, vs: &[Vec3]) {
        for v in vs {
            self.f(v.x);
            self.f(v.y);
            self.f(v.z);
        }
        self.0 += "\n";
    }
    fn mesh(&mut self, ms: &[MeshVertex]) {
        for m in ms {
            for v in [m.world.x, m.world.y, m.world.z, m.u, m.v, m.px, m.py] {
                self.f(v);
            }
        }
    }
}

fn cpp_dump() -> String {
    const LOOKS: [PointerLook; 8] = [
        PointerLook::Plain,
        PointerLook::ResizeEw,
        PointerLook::ResizeNs,
        PointerLook::ResizeNwse,
        PointerLook::ResizeNesw,
        PointerLook::Move,
        PointerLook::Rotate,
        PointerLook::Text,
    ];
    let mut d = Dump(String::new());
    for set in json_sets() {
        for h in HALVES {
            d.0 += &overview_geometry_json(&set, 1920, 1200, h[0], h[1]);
            d.0 += "\n";
        }
    }
    d.0 += &overview_geometry_json(&ROW_TILES, 0, 1200, 0.5, 0.3);
    d.0 += "|\n";
    d.0 += &overview_geometry_json(&ROW_TILES, 8193, 1200, 0.5, 0.3);
    d.0 += "|\n";
    d.0 += &overview_geometry_json(&ROW_TILES, 8192, 8192, 0.5, 0.3);
    d.0 += "|\n";
    let canvases = [Canvas::new(1920.0, 1200.0, 0.5, 0.3125), Canvas::new(1920.0, 1200.0, 0.42, 0.27), Canvas::new(3840.0, 1080.0, 1.1, 0.3)];
    let tiles = [rect(480.0, 330.0, 960.0, 540.0), rect(240.0, 300.0, 960.0, 540.0), rect(-1960.0, 60.0, 1920.0, 1080.0), rect(1300.5, 700.25, 512.3, 288.7)];
    let placements = [
        dflt(),
        Placement::new(0.4, -0.2, 1.6, 1.0, 0.3),
        Placement::new(-2.9, 1.2, 0.1, 2.094_395_1, -0.7),
        Placement::new(3.1, 0.0, 0.5, 0.0, 1.0),
        Placement::new(0.0, 0.0, 0.0, 0.0, 0.0),
    ];
    let dirs = [[0.0f32, 0.0], [0.7, -0.3], [-3.0, 1.4], [2.0, 0.2]];
    let pts = [[960.0f32, 600.0], [10.0, 1190.0], [-50.0, 3000.0], [1919.5, 0.25]];
    for c in &canvases {
        for p in pts {
            for dir in dirs {
                for look in LOOKS {
                    for grow in [0.0f32, 0.5, 1.0] {
                        d.v3(&direction_marks(c, p[0], p[1], dir[0], dir[1], look, grow));
                    }
                }
                d.v3(&direction_disc(c, p[0], p[1], dir[0], dir[1], 7.5, 24));
                d.v3(&direction_outline(c, p[0], p[1], dir[0], dir[1], sz(960.0, 540.0), 3.0));
                d.v3(&direction_outline(c, p[0], p[1], dir[0], dir[1], sz(0.5, 2.0), 9.0));
            }
        }
        for t in &tiles {
            for pl in &placements {
                for up in [Vec3::new(0.0, 1.0, 0.0), Vec3::new(0.3, 0.9, -0.1), Vec3::new(0.0, 0.0, 0.0)] {
                    match desktop_tilt(c, t, pl, &up) {
                        Some(v) => d.f(v),
                        None => d.0 += "none ",
                    }
                }
                let g = glass_to_desktop(t, pl, 700.0, 450.0);
                d.f(g[0]);
                d.f(g[1]);
                d.mesh(&glass_mesh(c, t, pl, &rect(t.x + 20.0, t.y - 10.0, t.width * 0.7, t.height * 1.1)));
                d.mesh(&tile_mesh(c, t, pl, &rect(t.x - 40.0, t.y - 40.0, t.width + 80.0, t.height + 80.0)));
                d.0 += "\n";
            }
        }
    }
    for t in tiles.iter().chain([rect(0.0, 0.0, 3.0, 50.0)].iter()) {
        for r in drop_frame(t) {
            for v in [r.x, r.y, r.width, r.height] {
                d.f(v);
            }
        }
    }
    d.0 += "\n";
    d.0
}

#[test]
fn pointer_glass_and_tilt_match_cpp_bit_for_bit() {
    let dump = cpp_dump();
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in dump.bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    assert_eq!((format!("{h:016x}"), dump.len()), ("9da4227e8061ab53".to_string(), 929_063));
}
