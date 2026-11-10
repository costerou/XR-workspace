//! GlCompositor frames on surfaceless EGL (no display, window or compositor); port of the check mode of
//! spike/tests/gl_bench.cpp (ctest gl_frame): every scenario frame drawn by one long-lived compositor, in a mixed order, is
//! pixel-identical to the same frame drawn by a fresh compositor (no stale caches, uploads or GL state carried between
//! frames); the dot's looks draw round the dot only and under the cap; the window drag draws its ghost and target frame;
//! resizing and back matches fresh compositors; level bars and caps ride on their desktop's live placement.
//!
//! Not ported: the Serif Beam against its CPU mirror (pointer_raster is not in Rust yet). Cards come from XR_GL_CARDS
//! (`gl_bench --dump DIR`) when set, else are synthetic. Exit 77 (skipped) without EGL/GLES.

#[path = "support/gl_scenarios.rs"]
mod gl_scenarios;

use std::sync::atomic::{AtomicUsize, Ordering};

use gl_scenarios::*;
use xr_renderer::glass::{CAP_GAP, CAP_LEG, level_bar_rect, level_bar_scale};
use xr_renderer::view::{
    Canvas, FREE_POINTER_LEVEL, FREE_POINTER_RADIUS_PX, FREE_POINTER_RING_PX, POINTER_MARK_GAP_PX,
    POINTER_MARK_HALF_PX, POINTER_MARK_LEN_PX, Placement, Size, blended_layout, place_point,
};

static FAILURES: AtomicUsize = AtomicUsize::new(0);

macro_rules! check {
    ($c:expr) => {{
        let ok: bool = $c;
        if !ok {
            eprintln!("{}:{}: check!({})", file!(), line!(), stringify!($c));
            FAILURES.fetch_add(1, Ordering::Relaxed);
        }
    }};
}

const FRAMES: i32 = 3;

fn main() {
    let (w, h) = (DEFAULT_W, DEFAULT_H);
    let r = load_resources();
    let list = scenarios();
    let named = |name: &str| list.iter().position(|s| s.name == name).unwrap();

    // References: each frame of each scenario from a fresh compositor (one at a time: each makes its context current).
    let mut reference: Vec<Vec<Vec<u8>>> = vec![Vec::new(); list.len()];
    for (i, s) in list.iter().enumerate() {
        for f in 0..FRAMES {
            let mut gl = match make(w, h, &r) {
                Ok(gl) => gl,
                Err(why) => {
                    println!("SKIP: {why}");
                    std::process::exit(77);
                }
            };
            check!(draw_frame(&mut gl, &r, s, f, w, h, s.upload_each_frame));
            reference[i].push(gl.read_rgb());
            check!(gl.gl().get_error() == glow::NO_ERROR);
        }
    }

    // The dot's look really draws: each differs from the plain dot only right round it, in the dot's own grey or black
    // (so at or under the cards' cap), and each look differs from the others.
    {
        let base = Scenario {
            pills: true,
            pointer: true,
            ..Scenario::new("plain_dot")
        };
        let mut gl = make(w, h, &r).expect("compositor");
        check!(draw_frame(&mut gl, &r, &base, 0, w, h, false));
        let dot = gl.read_rgb();
        let (cx, cy) = (w as f32 * 0.55, h as f32 * 0.42);
        let reach = FREE_POINTER_RADIUS_PX
            + FREE_POINTER_RING_PX
            + POINTER_MARK_GAP_PX
            + POINTER_MARK_LEN_PX
            + 3.0 * POINTER_MARK_HALF_PX;
        let cap = (FREE_POINTER_LEVEL * 255.0 + 0.5) as i32;
        let mut drawn: Vec<&Vec<u8>> = Vec::new();
        for name in [
            "look_resize_ew",
            "look_resize_nwse",
            "look_move",
            "look_rotate",
        ] {
            let px = &reference[named(name)][0];
            let mut changed = 0usize;
            let (mut far, mut bright) = (0usize, 0usize);
            for y in 0..h {
                for x in 0..w {
                    let i = ((y * w + x) * 3) as usize;
                    if px[i..i + 3] == dot[i..i + 3] {
                        continue;
                    }
                    changed += 1;
                    let (dx, dy) = (x as f32 - cx, (h - 1 - y) as f32 - cy); // GL rows run bottom up
                    far += usize::from(dx.hypot(dy) > reach * 2.0);
                    bright += usize::from(i32::from(*px[i..i + 3].iter().max().unwrap()) > cap + 1);
                }
            }
            if far > 0 || bright > 0 {
                eprintln!(
                    "{name}: {far} pixels changed far from the dot, {bright} brighter than it"
                );
            }
            check!(far == 0 && bright == 0);
            check!(changed > 20);
            for other in &drawn {
                check!(*other != px);
            }
            drawn.push(px);
        }
        // The text beam has no disc (the desktop shows where the dot was), so only that it differs from the others.
        for other in &drawn {
            check!(**other != reference[named("look_text")][0]);
        }
    }

    // One long-lived compositor: forward, then backward, then interleaved, so every scenario follows several others.
    let mut order = Vec::new();
    for i in 0..list.len() {
        for f in 0..FRAMES {
            order.push((i, f));
        }
    }
    for i in (0..list.len()).rev() {
        for f in 0..FRAMES {
            order.push((i, f));
        }
    }
    for f in 0..FRAMES {
        for i in (0..list.len()).step_by(2) {
            order.push((i, f));
        }
    }
    for f in 0..FRAMES {
        for i in (1..list.len()).step_by(2) {
            order.push((i, f));
        }
    }
    let mut gl = make(w, h, &r).expect("compositor");
    let mut shown = 0; // the desktop variant the long-lived compositor holds
    for &(i, f) in &order {
        // The reference drew its frame's variant; upload scenarios upload every frame, the rest only when the variant
        // held differs, so the inputs match.
        let s = &list[i];
        let upload = s.upload_each_frame || variant(s, f) != shown;
        check!(draw_frame(&mut gl, &r, s, f, w, h, upload));
        shown = variant(s, f);
        let px = gl.read_rgb();
        let want = &reference[i][f as usize];
        if px != *want {
            let n = px.iter().zip(want).filter(|(a, b)| a != b).count();
            eprintln!(
                "{} frame {f}: {n} bytes differ from a fresh compositor",
                s.name
            );
            FAILURES.fetch_add(1, Ordering::Relaxed);
        }
    }

    // The window drag draws: the ghost's grey outline round the dot, and the target desktop's accent frame.
    {
        let a = &reference[named("window_drag")][0];
        let b = &reference[named("overview_curved_pills")][0];
        let gx = (w as f32 * 0.55) as i64;
        let gy = i64::from(h) - 1 - (h as f32 * 0.42) as i64; // GL rows: bottom-up
        let (mut ring, mut elsewhere) = (0usize, 0usize);
        for y in 0..h {
            for x in 0..w {
                let k = ((y * w + x) * 3) as usize;
                if a[k..k + 3] == b[k..k + 3] {
                    continue;
                }
                let (dx, dy) = ((i64::from(x) - gx).abs(), (i64::from(y) - gy).abs());
                if dx <= 10 && dy <= 10 {
                    // the held dot's own level
                } else if dx <= 38 && dy <= 25 {
                    ring += 1; // the 72 x 45 ghost outline
                } else {
                    elsewhere += 1; // the target's frame
                }
            }
        }
        if ring <= 100 || elsewhere <= 200 {
            eprintln!("window drag: ghost {ring} px, frame {elsewhere} px");
        }
        check!(ring > 100);
        check!(elsewhere > 200);
    }

    // Resize: drawing at a new size then back matches fresh compositors at each size.
    let overview_pills = &list[5];
    gl.resize(w / 2, h / 2, 0, 0);
    check!(draw_frame(
        &mut gl,
        &r,
        overview_pills,
        0,
        w / 2,
        h / 2,
        false
    ));
    let small = gl.read_rgb();
    gl.resize(w, h, 0, 0);
    check!(draw_frame(&mut gl, &r, overview_pills, 0, w, h, false));
    check!(gl.read_rgb() == reference[5][0]);
    drop(gl);
    {
        let mut fresh = make(w / 2, h / 2, &r).expect("compositor");
        check!(draw_frame(
            &mut fresh,
            &r,
            overview_pills,
            0,
            w / 2,
            h / 2,
            false
        ));
        check!(fresh.read_rgb() == small);
    }

    // Level bars and corner caps ride on their desktop's live placement, not on a held panel's press-time surface: with
    // desktop 1 resized (bigger) under a held slider, its bar and caps are drawn where its live top edge and corners
    // are. And every desktop's content covers them: desktop 0's inner cap, now under desktop 1, is not seen, while its
    // outer one and its bar, in empty space, are.
    {
        let at = named("level_bars_resized");
        let bare = Scenario {
            roll: false,
            ..list[at]
        };
        let mut fresh = make(w, h, &r).expect("compositor");
        check!(draw_frame(&mut fresh, &r, &bare, 0, w, h, false));
        let without = fresh.read_rgb();
        let with = &reference[at][0];
        let (half_w, half_h) = half_fov(w, h);
        let canvas = Canvas::new(w as f32, h as f32, half_w, half_h);
        let sizes = [Size {
            w: DESKTOP_W as f32,
            h: DESKTOP_H as f32,
        }; 3];
        let tiles = blended_layout(&sizes, w as f32, h as f32, 40.0, 0.0, 1.0);
        // Pixels within 2 px of where tile point (x, y) of desktop k lands with placement p that the bars and caps changed.
        let changed_at = |k: usize, x: f32, y: f32, p: &Placement| {
            let v = place_point(&canvas, &tiles[k], p, x, y);
            let gx = ((v.x / -v.z / half_w + 1.0) * w as f32 / 2.0).round() as i64;
            let gy =
                i64::from(h) - 1 - ((1.0 - v.y / -v.z / half_h) * h as f32 / 2.0).round() as i64;
            let mut n = 0;
            for y2 in gy - 2..=gy + 2 {
                for x2 in gx - 2..=gx + 2 {
                    if x2 < 0 || y2 < 0 || x2 >= i64::from(w) || y2 >= i64::from(h) {
                        continue;
                    }
                    let i = ((y2 * i64::from(w) + x2) * 3) as usize;
                    n += i32::from(with[i..i + 3] != without[i..i + 3]);
                }
            }
            n
        };
        // The side leg of a top cap (side -1 left, 1 right), and a turn segment of the bar.
        let cap = |k: usize, side: f32, p: &Placement| {
            let t = &tiles[k];
            let s = level_bar_scale(t);
            let x = if side > 0.0 { t.x + t.width } else { t.x } + side * CAP_GAP * s;
            changed_at(k, x, t.y - (CAP_GAP - CAP_LEG / 2.0) * s, p)
        };
        let bar = |k: usize, side: f32, p: &Placement| {
            let b = level_bar_rect(&tiles[k]);
            changed_at(
                k,
                b.x + b.width / 2.0 + side * b.width / 3.0,
                b.y + b.height / 2.0,
                p,
            )
        };
        let live = Placement {
            distance: 0.6,
            ..Placement::default()
        };
        let press = Placement::default();
        for side in [-1.0, 1.0] {
            let (cap_live, cap_press, bar_live, bar_press) = (
                cap(1, side, &live),
                cap(1, side, &press),
                bar(1, side, &live),
                bar(1, side, &press),
            );
            if cap_live < 5 || bar_live < 5 || cap_press != 0 || bar_press != 0 {
                eprintln!(
                    "desktop 1 side {side}: cap {cap_live} px live, {cap_press} at the press; bar {bar_live}, {bar_press}"
                );
            }
            check!(cap_live >= 5 && bar_live >= 5);
            check!(cap_press == 0 && bar_press == 0);
        }
        let (covered, open, own) = (
            cap(0, 1.0, &press),
            cap(0, -1.0, &press),
            bar(0, -1.0, &press),
        );
        if covered != 0 || open < 5 || own < 5 {
            eprintln!(
                "desktop 0: inner cap {covered} px under desktop 1, outer {open}, bar {own} in the open"
            );
        }
        check!(covered == 0);
        check!(open >= 5 && own >= 5);
    }

    let failures = FAILURES.load(Ordering::Relaxed);
    if failures > 0 {
        eprintln!("{failures} failures");
        std::process::exit(1);
    }
    println!(
        "gl frame check passed: {} scenarios, {} frames vs fresh compositors ({} cards)",
        list.len(),
        order.len() + 2,
        if r.from_cpp { "C++" } else { "synthetic" }
    );
}
