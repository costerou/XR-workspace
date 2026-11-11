//! CPU mirror of how the compositor fills the dot's flat triangles (gl.rs, no anti-aliasing): a pixel is covered when its
//! centre is inside a triangle. Port of spike/tests/pointer_raster.hpp, the same f64 maths in the same order. Shared by
//! the geometry test (pointer_beam) and the pixel test (gl_frame), so what the GL draws is compared with the same model
//! the geometry is measured on.

#![allow(dead_code)]

use xr_renderer::view::{Canvas, Vec3};

pub struct PointerMask {
    /// Canvas px of the mask's top-left corner, and its size.
    pub x0: i32,
    pub y0: i32,
    pub w: i32,
    pub h: i32,
    /// w * h, top row first.
    pub covered: Vec<u8>,
}

impl PointerMask {
    pub fn at(&self, x: i32, y: i32) -> bool {
        x >= self.x0
            && y >= self.y0
            && x < self.x0 + self.w
            && y < self.y0 + self.h
            && self.covered[(y - self.y0) as usize * self.w as usize + (x - self.x0) as usize] != 0
    }

    pub fn count(&self) -> usize {
        self.covered.iter().filter(|&&c| c == 1).count()
    }
}

/// `tris`: three points per triangle in view space, projected as the compositor's XR_PROJECT does for an unturned view.
pub fn pointer_mask(c: &Canvas, tris: &[Vec3], x0: i32, y0: i32, w: i32, h: i32) -> PointerMask {
    let mut m = PointerMask {
        x0,
        y0,
        w,
        h,
        covered: vec![0; w as usize * h as usize],
    };
    let project = |v: &Vec3| {
        let u = f64::from(v.x) / -f64::from(v.z) / f64::from(c.half_w);
        let t = f64::from(v.y) / -f64::from(v.z) / f64::from(c.half_h);
        (
            (u + 1.0) * f64::from(c.view_w) / 2.0,
            (1.0 - t) * f64::from(c.view_h) / 2.0,
        )
    };
    for tri in tris.chunks_exact(3) {
        let (ax, ay) = project(&tri[0]);
        let (bx, by) = project(&tri[1]);
        let (cx, cy) = project(&tri[2]);
        let area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
        if area == 0.0 {
            continue;
        }
        let sign = if area > 0.0 { 1.0 } else { -1.0 };
        for y in 0..h {
            for x in 0..w {
                let px = f64::from(x0 + x) + 0.5;
                let py = f64::from(y0 + y) + 0.5;
                let e0 = ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) * sign;
                let e1 = ((cx - bx) * (py - by) - (cy - by) * (px - bx)) * sign;
                let e2 = ((ax - cx) * (py - cy) - (ay - cy) * (px - cx)) * sign;
                if e0 >= 0.0 && e1 >= 0.0 && e2 >= 0.0 {
                    m.covered[y as usize * w as usize + x as usize] = 1;
                }
            }
        }
    }
    m
}
