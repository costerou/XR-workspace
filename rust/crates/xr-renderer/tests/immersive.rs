//! Twin of spike/tests/immersive_test.cpp (ctest `immersive`), immersive 3D's renderer side:
//!   * the `immersive` frame: every valid fixture payload decodes, every invalid one is dropped with `invalid_immersive`,
//!     which the renderer ignores without stopping, like a malformed `roll`;
//!   * the 3D button's place (Connected Tail): 2 px right of the level bar, a 40 px segment and a 28 px tail, on neither
//!     content nor a corner, and absent on a desktop too narrow to hold it;
//!   * its pixels: the mockup's M3 roles, four states of each control in the handle atlas, none over the OLED cap,
//!     nothing leaking out of their cells, the small inner corners and the tail's round end.

use std::collections::BTreeSet;

use serde_json::{Value as Json, json};
use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::glass::{
    AtlasBox, CAP_INNER, HANDLE_ATLAS_SCALE, IMMERSIVE_MAIN_W, IMMERSIVE_TAIL_W, ImmersivePart, LEVEL_BAR_H,
    LEVEL_BAR_PAD, LEVEL_EDGE_CLEAR, immersive_main_box, immersive_main_rect, immersive_part, immersive_tail_box,
    immersive_tail_rect, level_bar_rect, level_bar_scale,
};
use xr_renderer::glass_raster::render_handle_atlas;
use xr_renderer::glass_theme::glass_theme_scheme;
use xr_renderer::hud::{HUD_FONT_FAMILY, HudImage, HudTextMetrics};
use xr_renderer::hud_font::HudFont;
use xr_renderer::material::MdRgb;
use xr_renderer::view::FRect;
use xr_wire::control::{self, Op, Payload};

const FIXTURE: &str =
    concat!(env!("CARGO_MANIFEST_DIR"), "/../../../tests/native_spike/fixtures/immersive_payloads.json");

fn frame(payload: &Json) -> String {
    let f = json!({"schema": 1, "request_id": 7, "run_id": "r1", "session": "s1", "op": "immersive", "payload": payload});
    format!("{f}\n")
}

#[test]
fn frames() {
    let data: Json = serde_json::from_str(&std::fs::read_to_string(FIXTURE).unwrap()).unwrap();
    let valid = data["valid"].as_array().unwrap();
    let invalid = data["invalid"].as_array().unwrap();
    assert!(!valid.is_empty() && !invalid.is_empty());
    for v in valid {
        let m = control::decode(frame(&v["payload"]).as_bytes()).unwrap_or_else(|e| panic!("{v}: {e:?}"));
        assert_eq!(m.op, Op::Immersive);
        let Payload::Immersive(p) = m.payload else { panic!("{:?}", m.payload) };
        for i in 0..MAX_DESKTOPS {
            assert_eq!(Json::from(p.desktops[i]), v["payload"]["desktops"][i], "{v}");
        }
        assert_eq!(Json::from(p.stereo), v["stereo"], "{v}");
        assert_eq!(Json::from(p.hover), v["hover"], "{v}");
        assert_eq!(Json::from(p.tail), v["tail"], "{v}");
        assert_eq!(Json::from(p.global), v["every"], "{v}");
    }
    for v in invalid {
        let e = control::decode(frame(v).as_bytes()).err().unwrap_or_else(|| panic!("accepted: {v}"));
        assert!(e.code == "invalid_immersive" && e.ignorable(), "{v}: {e:?}");
    }
}

#[test]
fn place() {
    let r = FRect::new;
    for t in [
        r(0.0, 500.0, 1920.0, 1080.0),
        r(100.0, 300.0, 586.67, 330.0),
        r(100.0, 300.0, 440.0, 247.0),
        r(-200.5, 410.25, 812.0, 457.0),
    ] {
        let (m, l, bar) = (immersive_main_rect(&t), immersive_tail_rect(&t), level_bar_rect(&t));
        let s = level_bar_scale(&t);
        let pad = LEVEL_BAR_PAD * s;
        let near = |a: f32, b: f32| (a - b).abs() < 1e-3;
        assert!(m.width > 0.0 && l.width > 0.0, "{t:?}");
        assert!(near(m.y, bar.y) && near(m.height, bar.height) && near(l.y, bar.y) && near(l.height, bar.height));
        assert!(near(m.x, bar.x + bar.width + 2.0 * s) && near(m.width, 40.0 * s));
        assert!(near(l.x, m.x + m.width + 2.0 * s) && near(l.width, 28.0 * s));
        assert!(l.x + l.width <= t.x + t.width - CAP_INNER * s + 1e-3, "short of the corner's cap");
        assert!(m.y + m.height + pad <= t.y - LEVEL_EDGE_CLEAR * s + 1e-3, "clear of the edge's band");
        let cy = m.y + m.height / 2.0;
        let (gap1, gap2) = (bar.x + bar.width + s, m.x + m.width + s);
        let part = |x: f32, y: f32| immersive_part(&t, x, y);
        assert_eq!(part(m.x + m.width / 2.0, cy), ImmersivePart::Main);
        assert_eq!(part(l.x + l.width / 2.0, cy), ImmersivePart::Tail);
        assert_eq!(part(gap1 + 0.1 * s, cy), ImmersivePart::Main);
        assert_eq!(part(gap1 - 0.1 * s, cy), ImmersivePart::None);
        assert_eq!(part(gap2 - 0.1 * s, cy), ImmersivePart::Main);
        assert_eq!(part(gap2 + 0.1 * s, cy), ImmersivePart::Tail);
        assert_eq!(part(l.x + l.width + pad - 0.1 * s, cy), ImmersivePart::Tail);
        assert_eq!(part(l.x + l.width + pad + 0.1 * s, cy), ImmersivePart::None);
        assert_eq!(part(m.x + 1.0, m.y - pad + 0.1 * s), ImmersivePart::Main);
        assert_eq!(part(m.x + 1.0, m.y - pad - 0.1 * s), ImmersivePart::None);
        assert_eq!(part(m.x + m.width / 2.0, t.y + 5.0), ImmersivePart::None, "on the desktop: its content's");
    }
    for t in [r(10.0, 20.0, 120.0, 60.0), r(5.0, 5.0, 40.0, 30.0), r(0.0, 0.0, 0.0, 0.0)] {
        assert!(!(immersive_main_rect(&t).width > 0.0) && !(immersive_tail_rect(&t).width > 0.0), "{t:?}");
        assert_eq!(immersive_part(&t, t.x + t.width / 2.0, t.y - 20.0), ImmersivePart::None);
    }
}

struct Cell {
    x0: u32,
    y0: u32,
    x1: u32,
    y1: u32,
}

fn cell_of(atlas: &HudImage, b: &AtlasBox) -> Cell {
    let (w, h) = (atlas.width as f32, atlas.height as f32);
    let r = |v: f32| v.round() as u32;
    Cell { x0: r(b.u0 * w), y0: r(b.v0 * h), x1: r(b.u1 * w), y1: r(b.v1 * h) }
}

fn at(im: &HudImage, x: u32, y: u32) -> &[u8] {
    let i = (y as usize * im.width as usize + x as usize) * 4;
    &im.rgba[i..i + 4]
}

/// A pixel of the container: its colour exactly, as md_over rounds it.
fn is(im: &HudImage, x: u32, y: u32, c: MdRgb) -> bool {
    let p = at(im, x, y);
    p[3] == 255 && (0..3).all(|n| (i32::from(p[n]) - (c[n] * 255.0).round() as i32).abs() <= 1)
}

#[test]
fn pixels() {
    let accent: MdRgb = [0.48, 0.64, 0.97];
    let m = glass_theme_scheme(accent);
    let font = HudFont::load(HUD_FONT_FAMILY).ok();
    // The pixel font, and the real one where it is installed.
    let fonts: [Option<&dyn HudTextMetrics>; 2] = [None, font.as_ref().map(|f| f as &dyn HudTextMetrics)];
    for f in fonts {
        let atlas = render_handle_atlas(accent, f);
        let mut looks = BTreeSet::new();
        for tail in [false, true] {
            for lit in [false, true] {
                for hot in [false, true] {
                    let c = cell_of(&atlas, &if tail { immersive_tail_box(lit, hot) } else { immersive_main_box(lit, hot) });
                    let (w, h) = (c.x1 - c.x0, c.y1 - c.y0);
                    let want_w = if tail { IMMERSIVE_TAIL_W } else { IMMERSIVE_MAIN_W } as u32 * HANDLE_ATLAS_SCALE;
                    assert!(w == want_w && h == LEVEL_BAR_H as u32 * HANDLE_ATLAS_SCALE);
                    assert!(c.x1 < atlas.width && c.y1 < atlas.height);
                    let fill = match (lit, hot) {
                        (true, true) => m.primary_bright,
                        (true, false) => m.primary,
                        (false, true) => m.primary_container,
                        (false, false) => m.secondary_container,
                    };
                    let mut cell = Vec::new();
                    let mut ink = 0u32;
                    for y in c.y0..c.y1 {
                        for x in c.x0..c.x1 {
                            let p = at(&atlas, x, y);
                            assert!(p[..3].iter().all(|&v| v <= 191), "the OLED cap");
                            cell.extend_from_slice(p);
                            ink += u32::from(p[3] == 255 && !is(&atlas, x, y, fill));
                        }
                    }
                    assert!(is(&atlas, c.x0 + 6, c.y0 + h / 2, fill), "the container's role, left of its content");
                    assert!(ink > 200, "the 3D, or the tail's three desktops, on it");
                    for x in c.x0..c.x1 {
                        // The gutter stays empty, so bilinear filtering stays in the cell.
                        assert!(at(&atlas, x, c.y0 - 1)[3] == 0 && at(&atlas, x, c.y1)[3] == 0);
                    }
                    // Small inner corners on the left; on the right the same for the segment, the capsule's end for the
                    // tail.
                    assert!(at(&atlas, c.x0, c.y0)[3] < 64);
                    assert!(at(&atlas, c.x0 + 12, c.y0)[3] == 255 && at(&atlas, c.x0, c.y0 + 12)[3] == 255);
                    if tail {
                        for x in c.x0 + 12..c.x0 + w / 2 {
                            assert!(at(&atlas, x, c.y0)[3] == 255, "its top edge is unbroken");
                        }
                        assert!(at(&atlas, c.x1 - 12, c.y0)[3] < 64 && at(&atlas, c.x1 - 1, c.y0 + h / 2)[3] >= 250);
                    } else {
                        assert!(at(&atlas, c.x1 - 13, c.y0)[3] == 255);
                    }
                    looks.insert(cell);
                }
            }
        }
        assert_eq!(looks.len(), 8);
    }
}
