//! The Serif Beam as the rasteriser sees it: port of the pointer_raster.hpp checks of spike/tests/free_pointer_test.cpp
//! (ctest free_pointer), measured on the CPU mirror (support/pointer_raster.rs) that gl_frame compares the GL with.
//! 14 x 28 px with its ring, 10 x 24 without, centred on the click point, a stem 2 px wide, mirror symmetric both ways.
//! The rest of free_pointer_test (vertices, turns, clamping) is covered by the C++ geometry dump in tests/view.rs.

#[path = "support/pointer_raster.rs"]
mod pointer_raster;

use pointer_raster::{pointer_mask, PointerMask};
use xr_renderer::view::{direction_marks, Canvas, PointerLook, FREE_POINTER_RING_PX};

const CANVAS: Canvas = Canvas::new(1920.0, 1200.0, 0.36, 0.225);

/// The direction of a canvas point: turns nothing.
fn own(x: f32, y: f32) -> (f32, f32) {
    let c = &CANVAS;
    let wx = (2.0 * f64::from(x) / f64::from(c.view_w) - 1.0) * f64::from(c.half_w);
    let wy = (1.0 - 2.0 * f64::from(y) / f64::from(c.view_h)) * f64::from(c.half_h);
    (wx.atan2(1.0) as f32, wy.atan2(wx.hypot(1.0)) as f32)
}

/// Pixel edges (lo x, hi x, lo y, hi y) of what the mask covers.
fn extent(m: &PointerMask) -> (i32, i32, i32, i32) {
    let (mut lo_x, mut hi_x, mut lo_y, mut hi_y) = (1 << 20, -(1 << 20), 1 << 20, -(1 << 20));
    for y in m.y0..m.y0 + m.h {
        for x in m.x0..m.x0 + m.w {
            if m.at(x, y) {
                lo_x = lo_x.min(x);
                hi_x = hi_x.max(x);
                lo_y = lo_y.min(y);
                hi_y = hi_y.max(y);
            }
        }
    }
    (lo_x, hi_x + 1, lo_y, hi_y + 1)
}

fn masks() -> (PointerMask, PointerMask) {
    let (az, el) = own(700.0, 500.0);
    let ring = direction_marks(
        &CANVAS,
        700.0,
        500.0,
        az,
        el,
        PointerLook::Text,
        FREE_POINTER_RING_PX,
    );
    let ink = direction_marks(&CANVAS, 700.0, 500.0, az, el, PointerLook::Text, 0.0);
    (
        pointer_mask(&CANVAS, &ring, 680, 480, 40, 40),
        pointer_mask(&CANVAS, &ink, 680, 480, 40, 40),
    )
}

#[test]
fn beam_extent_and_stem() {
    let (ring_mask, ink_mask) = masks();
    assert_eq!(extent(&ring_mask), (693, 707, 486, 514));
    assert_eq!(extent(&ink_mask), (695, 705, 488, 512));
    for y in 495..505 {
        let wide = (680..720).filter(|&x| ink_mask.at(x, y)).count();
        assert!(
            wide == 2 && ink_mask.at(699, y) && ink_mask.at(700, y),
            "row {y}: stem {wide} px"
        );
    }
}

#[test]
fn beam_mirror_symmetric() {
    // The middle of what is drawn is the click point on either axis.
    let (ring_mask, _) = masks();
    for y in 480..520 {
        for x in 680..720 {
            assert_eq!(ring_mask.at(x, y), ring_mask.at(1399 - x, y), "({x},{y})");
            assert_eq!(ring_mask.at(x, y), ring_mask.at(x, 999 - y), "({x},{y})");
        }
    }
}
