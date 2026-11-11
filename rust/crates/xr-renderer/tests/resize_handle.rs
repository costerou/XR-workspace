//! `spike/tests/resize_handle_test.cpp`, check for check: the `resize_handle` and `move_handle` control frames decode
//! strictly and a malformed payload is the ignorable kind (dropped); corner_cap_rect() puts a cap's box round the
//! requested corner at a fixed spot; placement easing is frame-rate independent and lands exactly.

use xr_renderer::glass::{CAP_INNER, CAP_OUTER, corner_cap_rect, level_bar_scale};
use xr_renderer::view::{FRect, PLACEMENT_EASE_MS, Placement, ease_toward, same_placement};
use xr_wire::control::{Corner, Op, Payload, Stream, decode};

fn frame(payload: &str, id: u64) -> String {
    format!(
        "{{\"schema\":1,\"request_id\":{id},\"run_id\":\"run\",\"session\":\"session\",\"op\":\"resize_handle\",\"payload\":{payload}}}\n"
    )
}

fn pill(payload: &str) -> String {
    format!(
        "{{\"schema\":1,\"request_id\":8,\"run_id\":\"run\",\"session\":\"session\",\"op\":\"move_handle\",\"payload\":{payload}}}\n"
    )
}

#[test]
fn resize_handle_decodes_strictly() {
    let corners = [
        ("tl", Corner::TopLeft),
        ("tr", Corner::TopRight),
        ("bl", Corner::BottomLeft),
        ("br", Corner::BottomRight),
    ];
    for (name, corner) in corners {
        let p = format!("{{\"visible\":true,\"desktop\":2,\"corner\":\"{name}\",\"active\":false}}");
        let m = decode(frame(&p, 7).as_bytes()).expect(name);
        assert_eq!(m.op, Op::ResizeHandle);
        let Payload::ResizeHandle(h) = m.payload else {
            panic!("{name}")
        };
        assert!(h.visible && h.desktop == 2 && h.corner == corner && !h.active);
    }
    let hidden = decode(frame(r#"{"visible":false,"desktop":0,"corner":"br","active":true}"#, 7).as_bytes()).unwrap();
    let Payload::ResizeHandle(h) = hidden.payload else {
        panic!()
    };
    assert!(!h.visible && h.active && h.corner == Corner::BottomRight);
    for bad in [
        r#"{}"#,
        r#"{"visible":true,"desktop":4,"corner":"br","active":false}"#,
        r#"{"visible":true,"desktop":-1,"corner":"br","active":false}"#,
        r#"{"visible":true,"desktop":0,"corner":"center","active":false}"#,
        r#"{"visible":1,"desktop":0,"corner":"br","active":false}"#,
        r#"{"visible":true,"desktop":0,"corner":"br","active":"no"}"#,
        r#"{"visible":true,"desktop":0,"corner":"br"}"#,
        r#"{"visible":true,"desktop":0,"corner":"br","active":false,"size":3}"#,
        r#"{"visible":true,"desktop":0.5,"corner":"br","active":false}"#,
    ] {
        let e = decode(frame(bad, 7).as_bytes()).expect_err(bad);
        assert!(e.ignorable(), "{bad}");
        assert_eq!(e.code, "invalid_resize_handle", "{bad}");
    }
}

#[test]
fn bad_framing_is_fatal_and_a_dropped_frame_keeps_its_request_id() {
    let mut unframed = frame(r#"{"visible":true,"desktop":0,"corner":"br","active":false}"#, 7);
    unframed.pop();
    assert!(!decode(unframed.as_bytes()).expect_err("unframed").ignorable());
    let mut q = Stream::new("run", "session").unwrap();
    let dropped = q.feed(frame(r#"{"visible":true,"desktop":9,"corner":"br","active":false}"#, 9).as_bytes());
    assert!(matches!(&dropped[..], [Err(r)] if r.refusal.ignorable()));
    let taken = q.feed(frame(r#"{"visible":true,"desktop":1,"corner":"tl","active":false}"#, 9).as_bytes());
    assert!(matches!(&taken[..], [Ok(_)]));
}

#[test]
fn each_corner_gets_its_cap() {
    let tile = FRect::new(100.0, 200.0, 600.0, 340.0);
    for c in 0..4u32 {
        let b = corner_cap_rect(&tile, c);
        let (right, bottom) = (c == 1 || c == 3, c >= 2);
        let cx = if right { tile.x + tile.width } else { tile.x };
        let cy = if bottom { tile.y + tile.height } else { tile.y };
        assert!(b.x < cx && cx < b.x + b.width && b.y < cy && cy < b.y + b.height, "{c}");
        let ox = if right { b.x + b.width - cx } else { cx - b.x };
        let oy = if bottom { b.y + b.height - cy } else { cy - b.y };
        assert!((ox - CAP_OUTER).abs() < 1e-3 && (oy - CAP_OUTER).abs() < 1e-3, "{c}");
        assert_eq!(corner_cap_rect(&tile, c), b);
    }
    let small = FRect::new(0.0, 0.0, 240.0, 135.0);
    assert!((corner_cap_rect(&small, 0).width - (CAP_OUTER + CAP_INNER) * level_bar_scale(&small)).abs() < 1e-3);
    assert!(level_bar_scale(&small) < 1.0);
    // Degenerate input draws nothing.
    assert_eq!(corner_cap_rect(&FRect::new(0.0, 0.0, 0.0, 10.0), 0).width, 0.0);
    assert_eq!(corner_cap_rect(&tile, 4).width, 0.0);
}

#[test]
fn move_handle_decodes_strictly() {
    let m =
        decode(pill(r#"{"visible":true,"desktop":1,"hover":true,"active":false,"gear":false}"#).as_bytes()).unwrap();
    assert_eq!(m.op, Op::MoveHandle);
    let Payload::MoveHandle(p) = m.payload else { panic!() };
    assert!(p.visible && p.desktop == 1 && p.hover && !p.active && !p.gear);
    let g =
        decode(pill(r#"{"visible":true,"desktop":2,"hover":false,"active":false,"gear":true}"#).as_bytes()).unwrap();
    let Payload::MoveHandle(q) = g.payload else { panic!() };
    assert!(q.desktop == 2 && q.gear && !q.hover);
    // Regression: the four-key frame (no "gear") is what resize.py sends whenever the gear is not lit.
    let legacy = decode(pill(r#"{"visible":true,"desktop":3,"hover":false,"active":true}"#).as_bytes()).unwrap();
    let Payload::MoveHandle(l) = legacy.payload else {
        panic!()
    };
    assert!(l.visible && l.desktop == 3 && l.active && !l.hover && !l.gear);
    for bad in [
        r#"{}"#,
        r#"{"visible":true,"desktop":4,"hover":false,"active":false,"gear":false}"#,
        r#"{"visible":true,"desktop":-1,"hover":false,"active":false,"gear":false}"#,
        r#"{"visible":"yes","desktop":0,"hover":false,"active":false,"gear":false}"#,
        r#"{"visible":true,"desktop":0,"hover":false,"gear":false}"#,
        r#"{"visible":true,"desktop":0,"hover":false,"active":false,"x":5}"#,
        r#"{"visible":true,"desktop":0,"hover":false,"active":false,"gear":1}"#,
        r#"{"visible":true,"desktop":0,"hover":false,"active":false,"gear":false,"x":5}"#,
    ] {
        let e = decode(pill(bad).as_bytes()).expect_err(bad);
        assert!(e.ignorable(), "{bad}");
        assert_eq!(e.code, "invalid_move_handle", "{bad}");
    }
}

#[test]
fn placement_easing() {
    let from = Placement::new(0.0, 0.0, 1.0, 0.0, 0.0);
    let to = Placement::new(0.3, -0.2, 0.6, 0.5, 0.0);
    assert!(same_placement(&ease_toward(&from, &to, 1e9, PLACEMENT_EASE_MS), &to));
    assert!(same_placement(&ease_toward(&from, &to, 0.0, PLACEMENT_EASE_MS), &from));
    let half = ease_toward(&from, &to, PLACEMENT_EASE_MS, PLACEMENT_EASE_MS);
    assert!(half.x > 0.0 && half.x < to.x && half.distance < 1.0 && half.distance > to.distance);
    assert!(half.y < 0.0 && half.y > to.y && half.curvature > 0.0 && half.curvature < to.curvature);
    let (mut a, mut b) = (from, from);
    for _ in 0..8 {
        a = ease_toward(&a, &to, 4.0, PLACEMENT_EASE_MS); // 8 x 4 ms
    }
    for _ in 0..2 {
        b = ease_toward(&b, &to, 16.0, PLACEMENT_EASE_MS); // 2 x 16 ms
    }
    assert!((a.x - b.x).abs() < 1e-4 && (a.distance - b.distance).abs() < 1e-4);
    let (mut c, mut frames) = (from, 0);
    while !same_placement(&c, &to) && frames < 1000 {
        c = ease_toward(&c, &to, 1000.0 / 120.0, PLACEMENT_EASE_MS);
        frames += 1;
    }
    assert!(same_placement(&c, &to) && frames < 120); // settles in well under a second
}
