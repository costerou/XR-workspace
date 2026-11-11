//! The HUD icon set (`MdIcon`, drawn by `md_icon`): every icon rasterised at 16, 20 and 24 px is inked, stays inside
//! its square, never passes the OLED cap, and is mirror-symmetric wherever its reference shape (`.i-<name>` in the
//! design system's `bundle.css`) is.

use xr_renderer::hud::HudImage;
use xr_renderer::material_draw::{MdIcon, md_icon};

const SIDE: u32 = 40;
const CENTRE: f32 = 20.0;
/// Just under 191/255, the brightest any HUD pixel may be.
const INK: [f32; 3] = [0.749, 0.749, 0.749];

/// Every icon, with the mirror symmetries of its reference shape: (left-right, top-bottom).
const ICONS: [(MdIcon, bool, bool); 27] = [
    (MdIcon::Add, true, true),
    (MdIcon::Remove, true, true),
    (MdIcon::Width, true, true),
    (MdIcon::ArrowDropDown, true, false),
    (MdIcon::ArrowDropUp, true, false),
    (MdIcon::RotateLeft, false, false),
    (MdIcon::RotateRight, false, false),
    (MdIcon::Level, true, true),
    (MdIcon::Maximize, true, true),
    (MdIcon::Restore, true, true),
    (MdIcon::Options, false, false),
    (MdIcon::Close, true, true),
    (MdIcon::ChevronLeft, false, true),
    (MdIcon::ChevronRight, false, true),
    (MdIcon::ChevronDown, true, false),
    (MdIcon::Check, false, false),
    (MdIcon::Play, false, true),
    (MdIcon::Pause, true, true),
    (MdIcon::Stop, true, true),
    (MdIcon::Recenter, true, true),
    (MdIcon::Keys, true, false),
    (MdIcon::Place, true, true),
    (MdIcon::Display, true, false),
    (MdIcon::View, true, true),
    (MdIcon::Video, false, true),
    (MdIcon::Session, true, false),
    (MdIcon::Cube, true, false),
];
const SIZES: [f32; 3] = [16.0, 20.0, 24.0];

fn draw(icon: MdIcon, size: f32) -> HudImage {
    let mut im = HudImage { width: SIDE, height: SIDE, rgba: vec![0; (SIDE * SIDE * 4) as usize] };
    md_icon(&mut im, icon, CENTRE, CENTRE, size, INK, 1.0);
    im
}

fn px(im: &HudImage, x: u32, y: u32) -> &[u8] {
    let at = ((y * SIDE + x) * 4) as usize;
    &im.rgba[at..at + 4]
}

/// Largest per-channel difference between the image and its reflection (`flip_x`: left-right, else top-bottom).
fn asymmetry(a: &HudImage, b: &HudImage, flip_x: bool) -> u8 {
    let mut worst = 0;
    for y in 0..SIDE {
        for x in 0..SIDE {
            let (mx, my) = if flip_x { (SIDE - 1 - x, y) } else { (x, SIDE - 1 - y) };
            for (p, q) in px(a, x, y).iter().zip(px(b, mx, my)) {
                worst = worst.max(p.abs_diff(*q));
            }
        }
    }
    worst
}

#[test]
fn every_icon_inked_inside_its_box_and_capped() {
    for (icon, _, _) in ICONS {
        for size in SIZES {
            let im = draw(icon, size);
            let (lo, hi) = ((CENTRE - size / 2.0) as u32, (CENTRE + size / 2.0) as u32);
            let mut inked = 0;
            for y in 0..SIDE {
                for x in 0..SIDE {
                    let p = px(&im, x, y);
                    if p[3] == 0 {
                        continue;
                    }
                    inked += 1;
                    assert!((lo..hi).contains(&x) && (lo..hi).contains(&y), "{icon:?} at {size} px inks ({x}, {y})");
                    assert!(p[..3].iter().all(|&c| c <= 191 && c <= p[3]), "{icon:?} at {size} px: {p:?}");
                }
            }
            // A real glyph, not a dot: at least as many pixels as the box is wide.
            assert!(inked as f32 >= size, "{icon:?} at {size} px inks only {inked} pixels");
            // A stroke centred on a pixel edge covers two pixels partly, so "solid" is over half covered.
            assert!(im.rgba.chunks_exact(4).any(|p| p[3] >= 128), "{icon:?} at {size} px has no solid pixel");
        }
    }
}

#[test]
fn symmetric_icons_mirror() {
    for (icon, lr, tb) in ICONS {
        for size in SIZES {
            let im = draw(icon, size);
            // f32 rounding may differ by a step on either side of the axis.
            if lr {
                assert!(asymmetry(&im, &im, true) <= 2, "{icon:?} at {size} px not left-right symmetric");
            }
            if tb {
                assert!(asymmetry(&im, &im, false) <= 2, "{icon:?} at {size} px not top-bottom symmetric");
            }
        }
    }
}

#[test]
fn paired_icons_mirror_each_other() {
    let pairs = [(MdIcon::ChevronLeft, MdIcon::ChevronRight, true), (MdIcon::RotateLeft, MdIcon::RotateRight, true)];
    for (a, b, flip_x) in pairs {
        for size in SIZES {
            assert!(asymmetry(&draw(a, size), &draw(b, size), flip_x) <= 2, "{a:?} / {b:?} at {size} px");
        }
    }
    // Options' two sliders swap under a half turn: left-right then top-bottom.
    for size in SIZES {
        let im = draw(MdIcon::Options, size);
        let mut worst = 0;
        for y in 0..SIDE {
            for x in 0..SIDE {
                for (p, q) in px(&im, x, y).iter().zip(px(&im, SIDE - 1 - x, SIDE - 1 - y)) {
                    worst = worst.max(p.abs_diff(*q));
                }
            }
        }
        assert!(worst <= 2, "Options at {size} px not symmetric under a half turn");
    }
}
