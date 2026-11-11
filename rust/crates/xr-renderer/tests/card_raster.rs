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

// --- The glass rasterisers against the C++ (golden/glass_cards.cpp) ----------------------------------------------------

mod glass_cards {
    use xr_renderer::glass_raster::{render_glass_panel, render_handle_atlas, render_options_panel};
    use xr_renderer::hud::{HUD_FONT_FAMILY, HudImage, HudTextMetrics, PLACEMENT_MENU_BRIGHTNESS, PlacementMenuState};
    use xr_renderer::hud_font::HudFont;
    use xr_wire::control::{OptionsMenu, OptionsRow};

    fn fnv1a(bytes: &[u8]) -> u64 {
        bytes.iter().fold(0xcbf2_9ce4_8422_2325, |h, &b| (h ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3))
    }
    fn line(name: &str, real: bool, im: &HudImage) -> String {
        format!("{name} {} {} {} {}", if real { "real" } else { "pixel" }, im.width, im.height, fnv1a(&im.rgba))
    }

    type Accent = [f32; 3];
    const DEFAULT: Accent = [0.45, 0.65, 0.8];

    fn panel_cases() -> Vec<(&'static str, PlacementMenuState, Accent)> {
        let base = PlacementMenuState { open: true, ..PlacementMenuState::default() };
        let mut out = vec![("panel_default", base.clone(), [0.0; 3])];
        let mut with = |name, edit: &dyn Fn(&mut PlacementMenuState), accent: Accent| {
            let mut s = base.clone();
            edit(&mut s);
            out.push((name, s, accent));
        };
        let z = [0.0; 3];
        with("panel_values", &|s| (s.desktop, s.field, s.placements[1]) = (1, 1, [-455, 123, 140, 45]), z);
        with("panel_field2", &|s| (s.field, s.placements[0]) = (2, [1800, -900, 10, 120]), z);
        with("panel_field3", &|s| (s.field, s.placements[0]) = (3, [5000, -5000, 999, -7]), z);
        with("panel_nofield", &|s| (s.field, s.desktop, s.numbers) = (9, 7, [4, 9, 2, 200]), z);
        with(
            "panel_brightness",
            &|s| (s.field, s.background, s.brightness) = (PLACEMENT_MENU_BRIGHTNESS as u32, 1, 37),
            z,
        );
        with("panel_smoke", &|s| (s.background, s.brightness) = (2, 200), z);
        with("panel_bg_clamped", &|s| (s.background, s.brightness) = (9, 5), z);
        with("panel_wide", &|s| (s.wide, s.count) = (true, 4), z);
        with("panel_armed", &|s| (s.armed, s.count) = (true, 1), z);
        with("panel_busy", &|s| (s.busy, s.scale, s.resolution_w, s.resolution_h) = (true, 150, 1920, 1080), z);
        with("panel_scale", &|s| (s.scale, s.scales) = (125, 0x7f), z);
        with("panel_scale_picker", &|s| (s.scale, s.scales, s.picker) = (150, 0x2d, true), [0.9, 0.3, 0.2]);
        with("panel_scale_picker_busy", &|s| (s.scale, s.scales, s.picker, s.busy) = (300, 0x7f, true, true), z);
        with("panel_res", &|s| (s.resolution_w, s.resolution_h) = (2560, 1440), z);
        with(
            "panel_res_picker",
            &|s| (s.resolution_w, s.resolution_h, s.resolution_picker, s.field) = (3440, 1440, true, 0),
            z,
        );
        with(
            "panel_res_picker_odd",
            &|s| (s.resolution_w, s.resolution_h, s.resolution_picker, s.busy) = (1234, 567, true, true),
            z,
        );
        with("panel_bg_picker", &|s| (s.background, s.background_picker) = (1, true), [0.48, 0.64, 0.97]);
        with("panel_accent", &|s| s.placements[0] = [300, 200, 80, 30], [0.1, 0.8, 0.3]);
        out
    }

    #[allow(clippy::too_many_arguments)]
    fn row(id: &str, label: &str, kind: &str, value: &str, unit: &str, lo: f64, hi: f64, enabled: bool) -> OptionsRow {
        OptionsRow {
            id: id.into(),
            label: label.into(),
            scope: "monitor".into(),
            kind: kind.into(),
            value: value.into(),
            unit: unit.into(),
            minimum: lo,
            maximum: hi,
            step: 1.0,
            enabled,
            choices: Vec::new(),
        }
    }
    fn r(id: &str, label: &str, kind: &str, value: &str) -> OptionsRow {
        row(id, label, kind, value, "", 0.0, 0.0, true)
    }
    fn range(id: &str, label: &str, value: &str, unit: &str, lo: f64, hi: f64) -> OptionsRow {
        row(id, label, "range", value, unit, lo, hi, true)
    }

    fn options_cases() -> Vec<(String, OptionsMenu, Accent)> {
        let empty = OptionsMenu {
            open: false,
            namespace: String::new(),
            monitor: String::new(),
            scope: String::new(),
            category: String::new(),
            focus: -1,
            scroll: 0,
            revision: 0,
            status: String::new(),
            message: String::new(),
            rows: Vec::new(),
        };
        let mut out = vec![("options_empty".to_string(), empty.clone(), DEFAULT)];
        let base = OptionsMenu {
            open: true,
            monitor: "XR-1".into(),
            scope: "monitor".into(),
            category: "3d".into(),
            status: "idle".into(),
            rows: vec![
                range("placement_x", "Left / right", "-455", "0.1 deg", -1800.0, 1800.0),
                range("placement_y", "Down / up", "3", "0.1 deg", -900.0, 900.0),
                range("curve", "Curvature", "45", "deg", 0.0, 120.0),
                range("distance", "Distance", "140", "%", 10.0, 200.0),
                r("depth", "Depth", "toggle", "true"),
                row("stereo", "Stereo", "toggle", "false", "", 0.0, 0.0, false),
                r("scale", "Scale", "choice", "1.5"),
                r("filter", "Texture filter", "choice", "catmull rom"),
                r("resolution", "Resolution", "choice", "1920x1080"),
                r("remove_monitor", "Remove this monitor", "action", ""),
                r("version", "Version", "readonly", "0.9.1"),
            ],
            ..empty
        };
        out.push(("options_base".into(), base.clone(), DEFAULT));
        let mut with = |name: &str, edit: &dyn Fn(&mut OptionsMenu), accent: Accent| {
            let mut s = base.clone();
            edit(&mut s);
            out.push((name.into(), s, accent));
        };
        for focus in [-4, -3, -2, -1, 0, 2, 4, 6, 9] {
            with(&format!("options_focus{focus}"), &|s| s.focus = focus, DEFAULT);
        }
        with("options_scroll57", &|s| (s.scroll, s.focus) = (57, 3), DEFAULT);
        with("options_scroll_end", &|s| s.scroll = 114, [0.9, 0.3, 0.2]);
        with("options_scroll_far", &|s| s.scroll = 5000, DEFAULT);
        with(
            "options_workspace",
            &|s| {
                (s.scope, s.status, s.message, s.category) =
                    ("workspace".into(), "applied".into(), "Saved".into(), "windows".into())
            },
            DEFAULT,
        );
        with(
            "options_pending",
            &|s| {
                (s.status, s.category, s.monitor) =
                    ("pending".into(), "session".into(), "a-very-long-monitor-name".into())
            },
            [0.48, 0.64, 0.97],
        );
        with(
            "options_error",
            &|s| {
                (s.status, s.category, s.message) = (
                    "error".into(),
                    "nope".into(),
                    "the backend refused this change because it is far too long to fit".into(),
                )
            },
            DEFAULT,
        );
        with(
            "options_message",
            &|s| (s.monitor, s.category, s.message) = (String::new(), "privacy".into(), "Hello".into()),
            DEFAULT,
        );
        with(
            "options_ranges",
            &|s| {
                s.category = "video".into();
                s.focus = 1;
                s.rows = vec![
                    range("a", "Bad number", "abc", "mm", 0.0, 10.0),
                    range("b", "Leading space", "  2.5e0xyz", "", 0.0, 10.0),
                    range("c", "Ties", "0.125", "", 0.0, 1.0),
                    range("d", "No unit tie", "-0.001", "", -1.0, 1.0),
                    range("placement_x", "Ahead", "0.2", "0.1 deg", -1800.0, 1800.0),
                    range("placement_y", "Up", "250", "0.1 deg", -900.0, 900.0),
                    range("roll", "Roll", "-37", "0.1 deg", -900.0, 900.0),
                    range("flat", "Equal ends", "5", "%", 3.0, 3.0),
                    range("big", "Huge", "1e400", "", 0.0, 1.0),
                    range("inf", "Infinity", "-Infinity", "deg", -1.0, 1.0),
                    range("hex", "Hex", "0x1.8p1", "", 0.0, 10.0),
                    range("nan", "NaN", "nan", "%", 0.0, 10.0),
                ];
            },
            DEFAULT,
        );
        with(
            "options_kinds",
            &|s| {
                s.category = "input".into();
                s.focus = 5;
                s.rows = vec![
                    r("t", "Toggle odd", "toggle", "maybe"),
                    r("scale", "Scale bad", "choice", "big"),
                    r("scale", "Scale 2", "choice", "2"),
                    row("m", "Mixed", "choice", "Mixed-Case", "px", 0.0, 0.0, true),
                    r("x", "Sizes", "choice", "x1080"),
                    r("y", "Sizes 2", "choice", "1920x"),
                    r("z", "Sizes 3", "choice", "12x34x5"),
                    r("stop", "Stop the session", "action", ""),
                    r("layout_reset", "Reset layout", "action", "now"),
                    row(
                        "menu_reset",
                        "A label that is far too long for the room it is given in a row",
                        "action",
                        "",
                        "",
                        0.0,
                        0.0,
                        false,
                    ),
                    r(
                        "ro",
                        "Read only with a value that will not fit in the space",
                        "readonly",
                        "a value long enough to be cut short here",
                    ),
                    row("u", "Unit", "readonly", "", "ms", 0.0, 0.0, true),
                    r("v", "Unicode \u{e9}t\u{e9}", "choice", "caf\u{e9}"),
                ];
            },
            [0.2, 0.7, 0.5],
        );
        out
    }

    /// Every case's image hashes to the C++ one, with the pixel font and (when installed) the system font.
    #[test]
    fn glass_cards_match_cpp() {
        let real = match HudFont::load(HUD_FONT_FAMILY) {
            Ok(f) => Some(f),
            Err(why) => {
                eprintln!("{HUD_FONT_FAMILY} not found ({why}): pixel font only");
                None
            }
        };
        let golden: Vec<&str> = include_str!("../golden/glass_cards.txt").lines().collect();
        let panels = panel_cases();
        let options = options_cases();
        let atlas_accents: [Accent; 3] = [DEFAULT, [0.9, 0.2, 0.1], [0.1, 0.5, 0.9]];
        let mut lines = Vec::new();
        for pass in [false, true] {
            let font: Option<&dyn HudTextMetrics> = if pass {
                match &real {
                    Some(f) => Some(f),
                    None => break,
                }
            } else {
                None
            };
            for (name, s, accent) in &panels {
                lines.push(line(name, pass, &render_glass_panel(s, font, *accent)));
            }
            for (name, s, accent) in &options {
                lines.push(line(name, pass, &render_options_panel(s, font, *accent)));
            }
            for (i, accent) in atlas_accents.iter().enumerate() {
                lines.push(line(&format!("atlas{i}"), pass, &render_handle_atlas(*accent, font)));
            }
        }
        let want: Vec<&str> = golden.iter().copied().filter(|l| real.is_some() || l.contains(" pixel ")).collect();
        let bad: Vec<String> =
            lines.iter().zip(&want).filter(|(a, b)| a != b).map(|(a, b)| format!("rust {a}\n c++ {b}")).collect();
        assert_eq!(lines.len(), want.len());
        assert!(bad.is_empty(), "{} of {} differ:\n{}", bad.len(), lines.len(), bad.join("\n"));
    }
}

// --- The atlas and the options menu: determinism and the theme (roll_level_test, options_overlay_test) -----------------

#[test]
fn atlas_and_options_follow_the_accent() {
    use xr_renderer::glass::{HANDLE_ATLAS_H, HANDLE_ATLAS_W};
    use xr_renderer::glass_raster::{render_handle_atlas, render_options_panel};
    use xr_wire::control::OptionsMenu;
    let accent = [0.45, 0.65, 0.8];
    let atlas = render_handle_atlas(accent, None);
    assert!(atlas.width == HANDLE_ATLAS_W && atlas.height == HANDLE_ATLAS_H && premultiplied(&atlas));
    assert_eq!(atlas.rgba, render_handle_atlas(accent, None).rgba); // pure
    assert_ne!(render_handle_atlas([0.9, 0.2, 0.1], None).rgba, render_handle_atlas([0.1, 0.5, 0.9], None).rgba);
    let state = OptionsMenu {
        open: true,
        namespace: String::new(),
        monitor: "XR-2".into(),
        scope: "monitor".into(),
        category: "display".into(),
        focus: -1,
        scroll: 0,
        revision: 0,
        status: String::new(),
        message: String::new(),
        rows: Vec::new(),
    };
    let menu = render_options_panel(&state, None, accent);
    assert!(menu.width == 600 && menu.height == 440 && premultiplied(&menu));
    assert_eq!(menu.rgba, render_options_panel(&state, None, accent).rgba);
    assert_ne!(render_options_panel(&state, None, [0.8, 0.2, 0.1]).rgba, menu.rgba); // a theme switch redraws it
}
