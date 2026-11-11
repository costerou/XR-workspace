//! The CPU rasterisers: Material drawing (the drawing half of `spike/tests/material_test.cpp`), and every card, atlas
//! and panel compared byte for byte with the C++ ones (see `pixel_parity` below).

use xr_renderer::hud::HudImage;
use xr_renderer::material::material_scheme;
use xr_renderer::material_draw::{
    MD_ICON_PX, MdIcon, md_corner_distance, md_disc, md_fill_rounded, md_icon, md_icon_distance, md_over, md_stroke_rounded,
};

fn near(a: f32, b: f32, eps: f32) -> bool {
    (a - b).abs() <= eps
}
fn rgb(v: u32) -> [f32; 3] {
    [((v >> 16) & 255) as f32 / 255.0, ((v >> 8) & 255) as f32 / 255.0, (v & 255) as f32 / 255.0]
}
fn brightest(im: &HudImage) -> u8 {
    im.rgba.chunks_exact(4).flat_map(|p| p[..3].iter().copied()).max().unwrap_or(0)
}
fn premultiplied(im: &HudImage) -> bool {
    im.rgba.chunks_exact(4).all(|p| p[..3].iter().all(|&c| c <= p[3]))
}
fn blank(w: u32, h: u32) -> HudImage {
    HudImage { width: w, height: h, rgba: vec![0; w as usize * h as usize * 4] }
}
fn alpha_at(im: &HudImage, x: u32, y: u32) -> u8 {
    im.rgba[(y as usize * im.width as usize + x as usize) * 4 + 3]
}

#[test]
fn material_icons() {
    let ink = material_scheme(rgb(0x8d8d8d)).on_surface;
    let d = md_icon_distance;
    for icon in [MdIcon::Add, MdIcon::Remove, MdIcon::Width, MdIcon::ArrowDropDown, MdIcon::ArrowDropUp, MdIcon::Level] {
        // Mirror-symmetric left to right, and inked at the centre.
        assert!(d(icon, 0.0, 0.0, 24.0) < 0.0, "{icon:?}");
        let mut y = -11.0f32;
        while y <= 11.0 {
            let mut x = 0.5f32;
            while x <= 11.0 {
                assert!(near(d(icon, x, y, 24.0), d(icon, -x, y, 24.0), 1e-3), "{icon:?} {x} {y}");
                x += 1.5;
            }
            y += 1.5;
        }
        assert!(d(icon, 11.0, 11.0, 24.0) > 0.0 && d(icon, -11.0, -11.0, 24.0) > 0.0); // corners empty
        // Size scales the icon down to MD_ICON_PX; below it the strokes get heavier.
        assert!(near(d(icon, 6.0, 8.0, 48.0) / 2.0, d(icon, 3.0, 4.0, 24.0), 1e-3));
        assert!(d(icon, 1.5, 2.0, 12.0) < d(icon, 3.0, 4.0, 24.0) / 2.0 - 0.05);
        let mut im = blank(40, 40);
        md_icon(&mut im, icon, 20.0, 20.0, MD_ICON_PX, ink, 1.0);
        assert!(premultiplied(&im) && brightest(&im) <= ((ink[0] * 255.0).round() as u8) + 1 && brightest(&im) > 120);
        let (mut x0, mut x1, mut y0, mut y1) = (40, 0, 40, 0);
        for y in 0..40 {
            for x in 0..40 {
                if alpha_at(&im, x, y) != 0 {
                    (x0, x1, y0, y1) = (x0.min(x), x1.max(x), y0.min(y), y1.max(y));
                }
            }
        }
        assert!(x0 >= 20 - 9 - 1 && x1 <= 20 + 9 && y0 >= 20 - 9 - 1 && y1 <= 20 + 9, "{icon:?} within its 18 px square");
    }
    assert!(d(MdIcon::Add, 0.0, -6.0, 24.0) < 0.0 && d(MdIcon::Remove, 0.0, -6.0, 24.0) > 0.0); // the plus's upright
    assert!(d(MdIcon::Width, -9.0, -5.0, 24.0) < 0.0 && d(MdIcon::Width, 9.0, 5.0, 24.0) < 0.0); // the two bars
    assert!(d(MdIcon::Width, -2.0, -3.0, 24.0) < 0.0 && d(MdIcon::Width, 0.0, -3.0, 24.0) > 0.0); // a head, above the shaft
    assert!(d(MdIcon::ArrowDropDown, 0.0, 2.5, 24.0) < 0.0 && d(MdIcon::ArrowDropDown, 0.0, -2.5, 24.0) > 0.0);
    assert!(d(MdIcon::ArrowDropUp, 0.0, -2.0, 24.0) < 0.0 && d(MdIcon::ArrowDropUp, 0.0, 2.5, 24.0) > 0.0);
    let mut y = -4.0f32;
    while y <= 4.0 {
        let mut x = -6.0f32;
        while x <= 6.0 {
            assert!(near(d(MdIcon::ArrowDropUp, x, y, 24.0), d(MdIcon::ArrowDropDown, x, -y, 24.0), 1e-3));
            x += 0.5;
        }
        y += 0.5;
    }
    // The level icon: a dot on a horizon broken round it.
    assert!(d(MdIcon::Level, 8.0, 0.0, 24.0) < 0.0 && d(MdIcon::Level, 3.0, 0.0, 24.0) > 0.0 && d(MdIcon::Level, 0.0, 6.0, 24.0) > 0.0);
    // The turn icons: each other's mirror image, a ring round an empty centre, open at the upper right of rotate_right.
    let mut y = -11.0f32;
    while y <= 11.0 {
        let mut x = -11.0f32;
        while x <= 11.0 {
            assert!(near(d(MdIcon::RotateLeft, x, y, 24.0), d(MdIcon::RotateRight, -x, y, 24.0), 1e-3));
            x += 1.5;
        }
        y += 1.5;
    }
    let deg = 3.141_592_65f32 / 180.0;
    let r = 8.2f32;
    let on_ring = |degrees: f32| d(MdIcon::RotateRight, r * (degrees * deg).cos(), r * (degrees * deg).sin(), 24.0);
    assert!(d(MdIcon::RotateRight, 0.0, 0.0, 24.0) > 0.0);
    for degrees in [60.0, 90.0, 135.0, 180.0, 225.0, 270.0, 300.0] {
        assert!(on_ring(degrees) < 0.0, "the arc at {degrees}");
    }
    assert!(on_ring(25.0) > 0.0); // the opening
    assert!(d(MdIcon::RotateRight, 7.0, -3.0, 24.0) < 0.0 && d(MdIcon::RotateRight, 9.3, -6.5, 24.0) < 0.0); // the head
    assert!(d(MdIcon::RotateRight, 11.0, 11.0, 24.0) > 0.0 && d(MdIcon::RotateRight, -11.0, -11.0, 24.0) > 0.0);
    let mut im = blank(40, 40);
    md_icon(&mut im, MdIcon::RotateRight, 20.0, 20.0, MD_ICON_PX, ink, 1.0);
    assert!(premultiplied(&im) && brightest(&im) <= ((ink[0] * 255.0).round() as u8) + 1 && brightest(&im) > 120);
}

#[test]
fn material_corner_cap() {
    // A corner cap's L: on the stroke along both legs and round the elbow, empty inside and past the legs' ends, rounded.
    let (gap, leg, bend, w) = (8.5f32, 12.0f32, 5.0f32, 2.4f32);
    let e = gap - bend;
    let c = |x: f32, y: f32| md_corner_distance(x, y, gap, leg, bend, w);
    assert!(near(c(gap, gap - leg + 2.0), -w / 2.0, 1e-3) && near(c(gap - leg + 2.0, gap), -w / 2.0, 1e-3));
    assert!(near(c(e + bend * 0.7071, e + bend * 0.7071), -w / 2.0, 1e-2)); // on the bend's arc
    assert!(c(gap, gap) > 0.0); // the square corner is cut off
    assert!(c(0.0, 0.0) > 0.0 && c(gap, gap - leg - 2.0) > 0.0);
    assert!(near(c(gap, gap - leg - 1.2), 0.0, 1e-3)); // a round cap past the leg's end
    let mut x = -4.0f32;
    while x <= 12.0 {
        let mut y = -4.0f32;
        while y <= 12.0 {
            assert!(near(c(x, y), c(y, x), 1e-4));
            y += 0.7;
        }
        x += 0.7;
    }
}

#[test]
fn material_containers() {
    let ink = material_scheme(rgb(0x8d8d8d)).on_surface;
    let mut im = blank(60, 30);
    md_fill_rounded(&mut im, 5.0, 5.0, 55.0, 25.0, 10.0, ink, 1.0);
    assert!(alpha_at(&im, 30, 15) == 255 && alpha_at(&im, 5, 5) < 64 && alpha_at(&im, 0, 0) == 0 && premultiplied(&im));
    let mut ring = blank(60, 30);
    md_stroke_rounded(&mut ring, 5.0, 5.0, 55.0, 25.0, 10.0, 1.0, ink, 1.0);
    assert!(alpha_at(&ring, 30, 15) == 0 && alpha_at(&ring, 30, 5) > 200 && alpha_at(&ring, 30, 7) == 0 && premultiplied(&ring));
    let mut dot = blank(20, 20);
    md_disc(&mut dot, 10.0, 10.0, 6.0, ink, 0.5);
    assert!(near(f32::from(alpha_at(&dot, 10, 10)), 127.5, 1.0) && alpha_at(&dot, 10, 2) == 0);
    md_over(&mut dot, -1, 3, ink, 1.0); // outside / NaN: nothing
    md_over(&mut dot, 20, 3, ink, 1.0);
    md_over(&mut dot, 3, 3, ink, f32::NAN);
    assert_eq!(alpha_at(&dot, 3, 3), 0);
}
