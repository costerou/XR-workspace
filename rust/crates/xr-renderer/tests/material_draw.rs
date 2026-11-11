//! Pixel parity of the Material drawing (`src/material_draw.rs`) with the C++ (`spike/src/material_draw.cpp`): every
//! case of `golden/material_draw.cpp` (rounded containers and their outlines, discs, every icon at several sizes, the
//! corner caps' and icons' signed distances on a grid, the "over" blend) prints the same line here, byte for byte.
//! The behaviour checks of the same functions are in `tests/card_raster.rs`.

use xr_renderer::hud::HudImage;
use xr_renderer::material::MdRgb;
use xr_renderer::material_draw::{
    MdIcon, md_corner_distance, md_disc, md_fill_rounded, md_icon, md_icon_distance, md_over, md_rounded_distance,
    md_stroke_rounded,
};

fn fnv(bytes: &[u8], mut h: u64) -> u64 {
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}
const FNV_START: u64 = 0xcbf29ce484222325;

fn background(w: u32, h: u32) -> HudImage {
    let n = w as usize * h as usize * 4;
    HudImage { width: w, height: h, rgba: (0..n).map(|i| ((i * 37 + (i >> 2) * 11) & 0xff) as u8).collect() }
}

fn image(out: &mut Vec<String>, name: &str, w: u32, h: u32, draw: impl FnOnce(&mut HudImage)) {
    let mut im = background(w, h);
    draw(&mut im);
    out.push(format!("{name} {} {} {}", im.width, im.height, fnv(&im.rgba, FNV_START)));
}

/// The distance at every point of the grid [-r, r] x [-r, r] in steps of `step`, row by row.
fn grid(out: &mut Vec<String>, name: &str, r: f32, step: f32, d: impl Fn(f32, f32) -> f32) {
    let (mut h, mut n) = (FNV_START, 0usize);
    let mut j = 0;
    while -r + j as f32 * step <= r {
        let mut i = 0;
        while -r + i as f32 * step <= r {
            h = fnv(&d(-r + i as f32 * step, -r + j as f32 * step).to_bits().to_le_bytes(), h);
            n += 1;
            i += 1;
        }
        j += 1;
    }
    out.push(format!("{name} {n} {h}"));
}

/// The cases of `golden/material_draw.cpp`, in its order.
fn cases() -> Vec<String> {
    let mut out = Vec::new();
    let ink: MdRgb = [0.71, 0.42, 0.13];
    let pale: MdRgb = [0.62, 0.66, 0.7];
    let boxes: [(&str, f32, f32, f32, f32, f32, f32); 8] = [
        ("whole", 5.0, 5.0, 55.0, 25.0, 10.0, 1.0),
        ("fraction", 3.3, 4.7, 51.6, 27.2, 7.5, 1.0),
        ("pill", 2.0, 6.0, 58.0, 22.0, 40.0, 0.8),
        ("square", 10.0, 8.0, 30.0, 20.0, 0.0, 1.0),
        ("off", -12.5, -6.2, 20.4, 18.9, 9.0, 1.0),
        ("faint", 6.0, 3.0, 50.0, 28.0, 12.0, 0.35),
        ("thin", 4.0, 14.6, 56.0, 15.4, 3.0, 1.0),
        ("empty", 20.0, 10.0, 20.0, 30.0, 4.0, 1.0),
    ];
    for (name, x0, y0, x1, y1, r, alpha) in boxes {
        image(&mut out, &format!("fill_{name}"), 60, 32, |im| md_fill_rounded(im, x0, y0, x1, y1, r, ink, alpha));
        for width in [1.0f32, 2.5] {
            image(&mut out, &format!("stroke_{name}_{width:.1}"), 60, 32, |im| {
                md_stroke_rounded(im, x0, y0, x1, y1, r, width, pale, alpha)
            });
        }
    }
    let discs: [(&str, f32, f32, f32, f32); 4] =
        [("dot", 10.0, 10.0, 6.0, 0.5), ("fraction", 9.4, 11.3, 4.25, 1.0), ("tiny", 5.5, 5.5, 0.7, 1.0), ("edge", 1.0, 18.0, 7.0, 0.9)];
    for (name, cx, cy, r, alpha) in discs {
        image(&mut out, &format!("disc_{name}"), 20, 20, |im| md_disc(im, cx, cy, r, ink, alpha));
    }
    let icons = [
        ("add", MdIcon::Add),
        ("remove", MdIcon::Remove),
        ("width", MdIcon::Width),
        ("arrow_drop_down", MdIcon::ArrowDropDown),
        ("arrow_drop_up", MdIcon::ArrowDropUp),
        ("rotate_left", MdIcon::RotateLeft),
        ("rotate_right", MdIcon::RotateRight),
        ("level", MdIcon::Level),
    ];
    let placements: [(&str, f32, f32, f32, f32); 5] = [
        ("12", 20.0, 20.0, 12.0, 1.0),
        ("14.5", 19.3, 20.6, 14.5, 1.0),
        ("18", 20.0, 20.0, 18.0, 1.0),
        ("24", 20.5, 19.5, 24.0, 0.6),
        ("37", 20.2, 20.7, 37.0, 1.0),
    ];
    for (name, icon) in icons {
        for (p, cx, cy, size, alpha) in placements {
            image(&mut out, &format!("icon_{name}_{p}"), 40, 40, |im| md_icon(im, icon, cx, cy, size, ink, alpha));
        }
        for size in [12.0f32, 18.0, 24.0] {
            grid(&mut out, &format!("icon_distance_{name}_{}", size as i32), 13.0, 0.37, |x, y| md_icon_distance(icon, x, y, size));
        }
    }
    grid(&mut out, "corner_8.5_12_5_2.4", 14.0, 0.29, |x, y| md_corner_distance(x, y, 8.5, 12.0, 5.0, 2.4));
    grid(&mut out, "corner_6_9_0_3", 14.0, 0.29, |x, y| md_corner_distance(x, y, 6.0, 9.0, 0.0, 3.0));
    grid(&mut out, "corner_10_10_10_1", 14.0, 0.29, |x, y| md_corner_distance(x, y, 10.0, 10.0, 10.0, 1.0));
    grid(&mut out, "rounded_distance", 20.0, 0.43, |x, y| md_rounded_distance(x, y, 1.5, -2.0, 12.0, 7.0, 5.0));
    grid(&mut out, "rounded_distance_clamped", 20.0, 0.43, |x, y| md_rounded_distance(x, y, 0.0, 0.0, 6.0, 9.0, 30.0));
    image(&mut out, "over", 16, 16, |im| {
        for i in 0..256 {
            md_over(im, i % 16, i / 16, ink, i as f32 / 200.0);
        }
        md_over(im, -1, 3, pale, 1.0);
        md_over(im, 16, 3, pale, 1.0);
        md_over(im, 3, 16, pale, 1.0);
        md_over(im, 3, 3, pale, f32::NAN);
    });
    out
}

#[test]
fn every_primitive_matches_the_cpp_byte_for_byte() {
    let want: Vec<&str> = include_str!("../golden/material_draw.txt").lines().collect();
    let got = cases();
    assert_eq!(got.len(), want.len(), "case count");
    let differ: Vec<String> =
        got.iter().zip(&want).filter(|(g, w)| g != *w).map(|(g, w)| format!("  rust {g}\n  c++  {w}")).collect();
    assert!(differ.is_empty(), "{} of {} cases differ from golden/material_draw.txt:\n{}", differ.len(), want.len(), differ.join("\n"));
}
