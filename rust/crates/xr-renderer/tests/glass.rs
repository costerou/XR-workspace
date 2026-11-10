//! `spike/tests/glass_test.cpp`, check for check, with the same tolerances, but for the panel rasteriser's pixels
//! (render_glass_panel, HudFont): geometry, the morph, the look under the OLED cap, the expand animation, the draw
//! order, and the glass's size and gap through a resize. GlassPills' image() is ensure() here.

use xr_renderer::desktops::*;
use xr_renderer::glass::*;
use xr_renderer::glass_theme::{GLASS_THEME_DEFAULT, glass_theme_scheme};
use xr_renderer::hud::{
    BACKGROUND_COUNT, CARD_MAX_LEVEL, CardFade, MenuPlacement, PLACEMENT_MENU_FIELDS, PlacementMenuState, hud_text_width,
    placement_menu_bounds, placement_menu_flat, placement_menu_rows,
};
use xr_renderer::material::{MD_STATE_DRAGGED, MD_STATE_HOVER, md_layer};
use xr_renderer::view::*;

fn near(a: f32, b: f32, eps: f32) -> bool {
    (a - b).abs() <= eps
}

fn n3(a: f32, b: f32) -> bool {
    near(a, b, 1e-3)
}

const TILE: FRect = FRect { x: 100.0, y: 300.0, width: 459.0, height: 258.0 };

fn open_on(desktop: u32) -> PlacementMenuState {
    PlacementMenuState { open: true, desktop, ..PlacementMenuState::default() }
}

#[test]
fn pill_panel_and_morph() {
    // The pill is centred GLASS_PILL_GAP under the desktop, a share of its width within bounds, never wider than it; the
    // panel shares the pill's top and centre; the morph runs from one to the other.
    let pill = glass_pill(&TILE);
    assert!(n3(pill.y, TILE.y + TILE.height + GLASS_PILL_GAP) && n3(pill.height, GLASS_PILL_H));
    assert!(n3(pill.x + pill.width / 2.0, TILE.x + TILE.width / 2.0) && n3(pill.width, GLASS_PILL_SHARE * TILE.width));
    assert!(n3(glass_pill(&FRect::new(0.0, 0.0, 4000.0, 2000.0)).width, GLASS_PILL_MAX_W));
    assert!(n3(glass_pill(&FRect::new(0.0, 0.0, 120.0, 60.0)).width, GLASS_PILL_MIN_W));
    assert!(glass_pill(&FRect::new(0.0, 0.0, 40.0, 30.0)).width <= 40.0);
    assert_eq!(glass_pill(&FRect::new(0.0, 0.0, 0.0, 30.0)).width, 0.0);
    let panel = glass_panel(&TILE);
    assert!(n3(panel.width, PANEL_W) && n3(panel.height, PANEL_H) && n3(panel.y, pill.y));
    assert!(n3(panel.x + panel.width / 2.0, pill.x + pill.width / 2.0));
    for t in [0.0f32, 0.25, 0.5, 1.0] {
        let m = glass_morph(&TILE, t);
        assert!(n3(m.y, pill.y) && n3(m.x + m.width / 2.0, pill.x + pill.width / 2.0));
        assert!(glass_radius(&TILE, t) <= m.width.min(m.height) / 2.0 + 1e-4);
        if t > 0.0 {
            let half = glass_morph(&TILE, t / 2.0);
            assert!(m.width > half.width && m.height > half.height);
        }
    }
    assert!(n3(glass_morph(&TILE, 0.0).width, pill.width) && n3(glass_morph(&TILE, 1.0).height, PANEL_H));
    assert!(n3(glass_radius(&TILE, 0.0), GLASS_PILL_H / 2.0) && n3(glass_radius(&TILE, 1.0), PANEL_RADIUS));
    assert!(n3(glass_morph(&TILE, -1.0).width, pill.width) && n3(glass_morph(&TILE, 2.0).width, PANEL_W)); // clamped
}

#[test]
fn panel_layout() {
    // Rows below the header, sliders between the label and value columns, footer inside.
    const { assert!(PANEL_ROWS_TOP >= PANEL_HEADER_H && PANEL_KNOB_LO < PANEL_KNOB_HI) };
    const { assert!(PANEL_TRACK_X0 > PANEL_PAD && PANEL_TRACK_X1 < PANEL_W - PANEL_PAD) };
    assert!(PANEL_FOOTER_TOP + PANEL_HINT_PX as f32 <= PANEL_H && GRABBER_TOP + GRABBER_H < PANEL_HEADER_H);
    // Four rows (left / right, down / up, distance, curvature: every desktop faces you, so there is no facing setting).
    assert_eq!(PLACEMENT_MENU_FIELDS, 4);
    // Then the Resolution and Scale rows (one row each: label and dropdown box), the Background and Brightness rows,
    // then the desktop row (Ultrawide / Add / Remove) between them and the footer, inside the paddings.
    assert_eq!(PANEL_RESOLUTION_TOP, PANEL_ROWS_TOP + 4.0 * PANEL_ROW_H);
    assert_eq!(PANEL_SCALE_TOP, PANEL_RESOLUTION_TOP + PANEL_ROW_H);
    assert_eq!(PANEL_BACKGROUND_TOP, PANEL_SCALE_TOP + PANEL_ROW_H);
    assert_eq!(PANEL_BRIGHTNESS_TOP, PANEL_BACKGROUND_TOP + PANEL_ROW_H);
    assert_eq!(PANEL_ACTIONS_TOP, PANEL_BRIGHTNESS_TOP + PANEL_ROW_H + 6.0);
    assert_eq!(PANEL_FOOTER_TOP, PANEL_ACTIONS_TOP + PANEL_ACTION_H + 8.0);
    const { assert!(PANEL_FOOTER_TOP == 364.0 && PANEL_H == 394.0) };
}

#[test]
fn background_row() {
    // The Background box and its switch share a row, right of the label column up to the padding; its list above it.
    let bx = glass_background_box();
    let sw = glass_background_switch();
    assert!(bx.x == PANEL_TRACK_X0 && bx.y >= PANEL_BACKGROUND_TOP && bx.y + bx.height <= PANEL_BACKGROUND_TOP + PANEL_ROW_H);
    assert!(sw.x >= bx.x + bx.width && n3(sw.x + sw.width, PANEL_W - PANEL_PAD) && sw.y == bx.y && sw.height == bx.height);
    for i in 0..BACKGROUND_COUNT {
        let r = glass_background_item(i);
        assert!(r.x == bx.x && r.width == bx.width && r.height == BACKGROUND_ITEM_H && r.y >= PANEL_HEADER_H);
    }
    assert!(n3(glass_background_item(BACKGROUND_COUNT - 1).y + BACKGROUND_ITEM_H + BACKGROUND_LIST_GAP, bx.y));
    assert_eq!(glass_background_item(BACKGROUND_COUNT).width, 0.0);
}

#[test]
fn resolution_row() {
    // The Resolution box: the Scale box's twin one row up; its list above it over the slider rows and their band.
    let bx = glass_resolution_box();
    let scale = glass_scale_box();
    assert!(bx.x == scale.x && bx.width == scale.width && bx.height == scale.height && n3(bx.y + PANEL_ROW_H, scale.y));
    assert!(bx.y >= PANEL_RESOLUTION_TOP && bx.y + bx.height <= PANEL_RESOLUTION_TOP + PANEL_ROW_H);
    let list = glass_resolution_list();
    assert!(list.x == 10.0 && n3(list.x + list.width, PANEL_W - 10.0) && n3(list.y, PANEL_ROWS_TOP) && list.y >= PANEL_HEADER_H);
    assert!(n3(list.y + list.height + RES_LIST_GAP, bx.y) && n3(list.height, 6.0 * RES_ITEM_H)); // headings + five rows
    // One column per shape, left to right: its heading on top, its sizes under it in RESOLUTIONS order.
    let mut i = 0;
    for (g, group) in RESOLUTION_GROUPS.iter().enumerate() {
        let h = glass_resolution_heading(g);
        assert!(n3(h.y, list.y) && n3(h.width, list.width / 3.0) && n3(h.x, list.x + g as f32 * h.width) && h.height == RES_ITEM_H);
        for row in 0..group.count {
            let r = glass_resolution_item(i);
            assert!(r.x == h.x && r.width == h.width && r.height == RES_ITEM_H && n3(r.y, h.y + (1 + row) as f32 * RES_ITEM_H));
            assert!(r.y + r.height <= list.y + list.height + 1e-3);
            i += 1;
        }
    }
    assert_eq!(i, RESOLUTION_COUNT);
    assert_eq!(glass_resolution_item(RESOLUTION_COUNT).width, 0.0);
    assert_eq!(glass_resolution_heading(RESOLUTION_GROUP_COUNT).width, 0.0);
    // Cody's sizes (2026-11-03): 16:9, 16:10 and ultrawide, every one a desktop the broker takes at 100%.
    let res = |width, height| Resolution { width, height };
    assert_eq!(RESOLUTIONS[0], res(1280, 720));
    assert_eq!(RESOLUTIONS[4], res(3840, 2160));
    assert_eq!(RESOLUTIONS[5], res(1680, 1050));
    assert_eq!(RESOLUTIONS[7], res(2560, 1600));
    assert_eq!(RESOLUTIONS[8], res(2560, 1080));
    assert_eq!(RESOLUTIONS[10], res(3840, 1080));
    assert_eq!(RESOLUTIONS[11], res(5120, 1440));
    for r in &RESOLUTIONS {
        assert!((DESKTOP_MIN_W..=DESKTOP_MAX_W).contains(&r.width) && (DESKTOP_MIN_H..=DESKTOP_MAX_H).contains(&r.height));
        assert!(r.width % 2 == 0 && r.height % 2 == 0);
    }
    assert!(RESOLUTION_GROUPS[0].name == "16:9" && RESOLUTION_GROUPS[2].name == "Ultrawide");
    let mut st = PlacementMenuState::default();
    for k in 0..RESOLUTION_COUNT {
        assert!(glass_resolution_enabled(k, &st));
    }
    assert!(!glass_resolution_enabled(RESOLUTION_COUNT, &st));
    st.busy = true;
    for k in 0..RESOLUTION_COUNT {
        assert!(!glass_resolution_enabled(k, &st));
    }
    assert!(glass_resolution_label(1920, 1080) == "1920x1080" && glass_resolution_label(0, 0) == "--");
    // The widest size fits its column with the text's padding, and the box (pixel font); "Resolution" fits the label
    // column.
    for r in &RESOLUTIONS {
        let w = hud_text_width(&glass_resolution_label(r.width, r.height), 2) as f32;
        assert!(w + 12.0 + 4.0 <= list.width / 3.0 && w + 12.0 + 28.0 <= RES_BOX_W);
    }
    assert!(hud_text_width("Resolution", 2) + 8 <= (PANEL_TRACK_X0 - PANEL_PAD) as u32);
}

#[test]
fn scale_row() {
    // The Scale box sits in its row, right of the label column up to the padding; its list above it, under the header.
    let bx = glass_scale_box();
    assert!(bx.x == PANEL_TRACK_X0 && n3(bx.x + bx.width, PANEL_W - PANEL_PAD));
    assert!(bx.y >= PANEL_SCALE_TOP && bx.y + bx.height <= PANEL_SCALE_TOP + PANEL_ROW_H);
    for i in 0..SCALE_STEPS {
        let r = glass_scale_item(i);
        assert!(r.x == bx.x && r.width == bx.width && r.height == SCALE_ITEM_H && r.y >= PANEL_HEADER_H);
        if i > 0 {
            assert!(n3(r.y, glass_scale_item(i - 1).y + SCALE_ITEM_H));
        }
    }
    assert!(n3(glass_scale_item(SCALE_STEPS - 1).y + SCALE_ITEM_H + SCALE_LIST_GAP, bx.y));
    assert_eq!(glass_scale_item(SCALE_STEPS).width, 0.0);
    // 100..200% in quarter steps, then 250% and 300%: the broker's own (spike/ownership/registry.cpp validDesktop).
    assert_eq!(SCALE_PERCENTS, [100, 125, 150, 175, 200, 250, 300]);
    assert!(glass_scale_item(0).y >= PANEL_HEADER_H && n3(glass_scale_item(0).y, PANEL_ROWS_TOP + PANEL_ROW_H - 2.0)); // all seven fit
    let mut st = PlacementMenuState { scale: 100, scales: 0b111_0111, ..PlacementMenuState::default() }; // 1920x1080: 175% does not divide it
    assert!(glass_scale_enabled(0, &st) && glass_scale_enabled(2, &st) && !glass_scale_enabled(3, &st) && glass_scale_enabled(4, &st));
    assert!(glass_scale_enabled(5, &st) && glass_scale_enabled(6, &st));
    assert!(!glass_scale_enabled(SCALE_STEPS, &st));
    st.busy = true;
    for i in 0..SCALE_STEPS {
        assert!(!glass_scale_enabled(i, &st));
    }
    assert!(glass_scale_label(125) == "125%" && glass_scale_label(0) == "--");
}

#[test]
fn desktop_row() {
    for a in 0..PANEL_ACTIONS {
        let r = glass_panel_action(a);
        assert!(r.x >= PANEL_PAD && r.x + r.width <= PANEL_W - PANEL_PAD + 1e-3 && r.y == PANEL_ACTIONS_TOP && r.height == PANEL_ACTION_H);
        if a > 0 {
            assert!(n3(r.x, glass_panel_action(a - 1).x + PANEL_ACTION_W + PANEL_ACTION_GAP));
        }
    }
    assert_eq!(glass_panel_action(PANEL_ACTIONS).width, 0.0);
    // Which buttons do anything: Add stops at MAX_DESKTOPS, Remove keeps one desktop, nothing while busy.
    let mut st = PlacementMenuState { count: 3, ..PlacementMenuState::default() };
    assert!(glass_panel_action_enabled(0, &st) && glass_panel_action_enabled(1, &st) && glass_panel_action_enabled(2, &st));
    st.count = MAX_DESKTOPS as u32;
    assert!(!glass_panel_action_enabled(1, &st) && glass_panel_action_enabled(2, &st));
    st.count = 1;
    assert!(glass_panel_action_enabled(1, &st) && !glass_panel_action_enabled(2, &st));
    st.busy = true;
    for a in 0..PANEL_ACTIONS {
        assert!(!glass_panel_action_enabled(a, &st));
    }
    assert!(glass_panel_action_label(2, true) == "Remove?" && glass_panel_action_label(2, false) == "Remove");
    for a in 0..PANEL_ACTIONS {
        assert!(glass_panel_action_label(a, false).bytes().all(|c| (0x20..0x7f).contains(&c))); // pixel font: ASCII
        assert!(hud_text_width(glass_panel_action_label(a, true), 2) + 8 <= PANEL_ACTION_W as u32);
    }
}

#[test]
fn panel_button_style() {
    // M3: filled tonal, the primary while Ultrawide is on, the error container while Remove is armed, on-surface at
    // 12% / 38% when it does nothing now.
    let m = glass_theme_scheme(GLASS_THEME_DEFAULT);
    let st = PlacementMenuState { count: 3, ..PlacementMenuState::default() };
    let s = glass_panel_button_style(1, &st, GLASS_THEME_DEFAULT);
    assert!(s.container == m.secondary_container && s.content == m.on_secondary_container && s.container_alpha == 1.0);
    let wide = PlacementMenuState { wide: true, ..st.clone() };
    assert_eq!(glass_panel_button_style(0, &wide, GLASS_THEME_DEFAULT).container, m.primary);
    let armed = PlacementMenuState { armed: true, ..st.clone() };
    assert_eq!(glass_panel_button_style(2, &armed, GLASS_THEME_DEFAULT).container, m.error_container);
    let busy = PlacementMenuState { busy: true, ..st };
    let d = glass_panel_button_style(0, &busy, GLASS_THEME_DEFAULT);
    assert!(d.container == m.on_surface && near(d.container_alpha, 0.12, 1e-6) && near(d.content_alpha, 0.38, 1e-6));
}

#[test]
fn every_look_stays_under_the_cap() {
    // Every pixel of every state stays at or under the cards' 191/255 cap, premultiplied, with any accent.
    for accent in [[1.0f32, 1.0, 1.0], [0.55, 0.55, 0.55], [1.0, 0.0, 0.3]] {
        for level in [GLASS_IDLE, GLASS_HOVER, GLASS_ACTIVE, 5.0] {
            for t in [0.0f32, 0.3, 1.0] {
                let look = glass_look(accent, level, t);
                let r = glass_morph(&TILE, t);
                let radius = glass_radius(&TILE, t);
                let mut top = 0.0f32;
                let mut alpha = 0.0f32;
                let mut y = r.y - GLASS_SHADOW_MARGIN;
                while y < r.y + r.height + GLASS_SHADOW_MARGIN {
                    let mut x = r.x - 10.0;
                    while x < r.x + r.width + 10.0 {
                        let c = glass_shade(&look, &r, radius, x + 0.5, y + 0.5, 1.0);
                        for v in &c[..3] {
                            top = top.max(*v);
                            assert!(*v <= c[3] + 1e-5);
                        }
                        alpha = alpha.max(c[3]);
                        assert!(c[3] <= 1.0 + 1e-5 && c[3] >= 0.0);
                        x += 3.0;
                    }
                    y += 1.0;
                }
                assert!(top <= GLASS_CAP + 1e-5 && top > 0.15 && alpha > 0.8);
            }
        }
    }
    assert!(near(GLASS_CAP * 255.0, f32::from(CARD_MAX_LEVEL), 0.01));
}

#[test]
fn open_panel_look() {
    // The open panel: an M3 surface container (high), flat, an outline-variant edge, elevation 3.
    let accent = [0.5f32, 0.5, 0.5];
    let m = glass_theme_scheme(accent);
    let look = glass_look(accent, GLASS_IDLE, 1.0);
    let r = glass_panel(&TILE);
    let radius = PANEL_RADIUS;
    let cx = r.x + r.width / 2.0;
    let cy = r.y + r.height / 2.0;
    let mid = glass_shade(&look, &r, radius, cx, cy, 1.0);
    assert!(near(mid[3], PANEL_FILL, 1e-4) && mid[0] < 0.25); // nearly opaque, dark behind the text
    for (v, high) in mid.iter().zip(m.surface_container_high) {
        assert!(near(*v, high * PANEL_FILL, 1e-5));
    }
    let high = glass_shade(&look, &r, radius, cx, r.y + 2.0, 1.0);
    let low = glass_shade(&look, &r, radius, cx, r.y + r.height - 4.0, 1.0);
    assert!(high == mid && low == mid); // flat: no gloss, no gradient
    let rim = glass_shade(&look, &r, radius, cx, r.y + 0.5, 1.0);
    assert!(rim[0] > mid[0] && near(rim[0], m.outline_variant[0], 1e-4)); // the outlined edge
    let corner = glass_shade(&look, &r, radius, r.x + 1.0, r.y + 1.0, 1.0);
    assert!(corner[3] < 0.5); // rounded corner: mostly outside (only shadow)
    let shadow = glass_shade(&look, &r, radius, cx, r.y + r.height + 4.0, 1.0);
    let far = glass_shade(&look, &r, radius, cx, r.y + r.height + 200.0, 1.0);
    assert!(shadow[3] > 0.1 && shadow[0] == 0.0 && far[3] < 1e-4); // a black soft shadow below, nothing far away
    assert_eq!(glass_shade(&look, &r, radius, cx, cy, 0.5)[3], mid[3] * 0.5);
    // Hovering or dragging its header lays the state layer (on-surface, 8% / 16%) on the header strip only.
    for (level, opacity) in [(GLASS_HOVER, MD_STATE_HOVER), (GLASS_ACTIVE, MD_STATE_DRAGGED)] {
        let hovered = glass_look(accent, level, 1.0);
        assert!(near(hovered.state_alpha, opacity, 1e-6) && hovered.state_px == PANEL_HEADER_H && hovered.state == m.on_surface);
        let header = glass_shade(&hovered, &r, radius, cx, r.y + PANEL_HEADER_H / 2.0, 1.0);
        let want = md_layer(m.surface_container_high, m.on_surface, opacity);
        for c in 0..3 {
            assert!(near(header[c], want[c] * PANEL_FILL, 1e-5));
        }
        assert_eq!(glass_shade(&hovered, &r, radius, cx, r.y + PANEL_HEADER_H + 1.0, 1.0), mid); // the rows: untouched
    }
    assert!(glass_look(accent, GLASS_ACTIVE, 1.0).shadow_sigma > look.shadow_sigma); // dragged: raised
    let g = grabber_look(GLASS_IDLE); // M3's drag handle: on-surface-variant at 40%, no shadow, no edge
    assert!(g.shadow == 0.0 && g.outline_alpha == 0.0 && g.state_alpha == 0.0 && near(g.fill, 0.4, 1e-6));
    assert!(grabber_look(GLASS_HOVER).fill > g.fill && grabber_look(GLASS_ACTIVE).fill > grabber_look(GLASS_HOVER).fill);
}

#[test]
fn collapsed_pill_look() {
    // The collapsed pill: the primary container, opaque; hover lights a 2 px rim and a top sheen, pressing dims it.
    let accent = [0.48f32, 0.64, 0.97];
    let m = glass_theme_scheme(accent);
    let r = glass_morph(&TILE, 0.0);
    let radius = glass_radius(&TILE, 0.0);
    let cx = r.x + r.width / 2.0;
    let lum = |p: [f32; 4]| p[0] + p[1] + p[2];
    let mut sigma = 0.0f32;
    let mut mids = [[0.0f32; 4]; 3];
    let mut tops = [[0.0f32; 4]; 3];
    let mut rims = [[0.0f32; 4]; 3];
    for (n, level) in [GLASS_IDLE, GLASS_HOVER, GLASS_ACTIVE].into_iter().enumerate() {
        let look = glass_look(accent, level, 0.0);
        assert!(look.fill == 1.0 && near(look.rim_px, GLASS_RIM_PX, 1e-6) && look.outline == m.primary_bright && look.state == m.primary_bright);
        assert!(near(look.outline_alpha, glass_rim_opacity(level), 1e-6) && near(look.state_alpha, glass_sheen_opacity(level), 1e-6));
        mids[n] = glass_shade(&look, &r, radius, cx, r.y + r.height - 4.0, 1.0); // below the sheen, inside the rim
        tops[n] = glass_shade(&look, &r, radius, cx, r.y + 3.0, 1.0); // under the sheen, inside the rim
        rims[n] = glass_shade(&look, &r, radius, cx, r.y + 1.0, 1.0); // on the rim
        assert!(near(mids[n][3], 1.0, 1e-6));
        assert!(look.shadow_sigma > sigma); // elevation 1, then 2 hovered, 4 dragged
        sigma = look.shadow_sigma;
    }
    for c in 0..3 {
        // idle: the plain container, edge to edge
        assert!(near(mids[0][c], m.primary_container[c], 1e-5));
        assert!(near(tops[0][c], m.primary_container[c], 1e-5));
        assert!(near(rims[0][c], m.primary_container[c], 1e-5));
    }
    assert!(lum(mids[1]) > lum(mids[0]) && lum(tops[1]) > lum(mids[1]) && lum(rims[1]) > lum(tops[1])); // hover: lifted, sheen, rim
    assert!(lum(mids[2]) < lum(mids[0]) && near(lum(tops[2]), lum(mids[2]), 1e-5) && lum(rims[2]) > lum(mids[2])); // pressed: no sheen
    assert!(glass_rim_opacity(GLASS_ACTIVE) > glass_rim_opacity(GLASS_HOVER) && glass_sheen_opacity(GLASS_ACTIVE) == 0.0);
    assert!(glass_state_opacity(GLASS_IDLE) == 0.0 && glass_state_opacity(0.0) == 0.0 && glass_state_opacity(9.0) == MD_STATE_DRAGGED);
    assert_eq!(glass_state_opacity(f32::NAN), 0.0); // NaN: idle
    // Midway through the morph the container, its on-colour and the state strip are between the pill's and the panel's.
    let half = glass_look(accent, GLASS_IDLE, 0.5);
    assert!(near(half.state_px, (GLASS_SHEEN_END * GLASS_PILL_H / 2.0 + PANEL_HEADER_H) / 2.0, 1e-4) && near(half.outline_alpha, 0.5, 1e-6));
    for c in 0..3 {
        assert!(near(half.container[c], (m.primary_container[c] + m.surface_container_high[c]) / 2.0, 1e-6));
    }
}

#[test]
fn style_uniform_and_shader() {
    // The shader's style uniform carries every look input, in the documented order.
    let k = GlassLook {
        container: [0.1, 0.2, 0.3],
        fill: 0.4,
        outline: [0.5, 0.6, 0.7],
        outline_alpha: 0.8,
        state: [0.9, 0.11, 0.12],
        state_alpha: 0.13,
        state_px: 14.0,
        shadow: 0.15,
        shadow_sigma: 16.0,
        shadow_dy: 17.0,
        rim_px: 2.0,
        state_soft: 18.0,
    };
    assert_eq!(
        glass_style(&k),
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.11, 0.12, 0.13, 14.0, 0.15, 16.0, 17.0, 2.0, 18.0, 0.0, 0.0]
    );
    let shader = glass_fragment_shader();
    assert!(shader.contains("0.74901961") && shader.contains("gl_FragColor"));
    assert!(shader.contains("uniform vec4 style[5]") && GLASS_STYLE_VECS == 5);
}

#[test]
fn glass_levels_ease() {
    // Each desktop's pill eases to its target over GLASS_HOVER_MS, ease-out, and retargets from where it is.
    let mut l = GlassLevels::default();
    assert!(l.level(0, 0.0) == GLASS_IDLE && !l.animating(0.0) && l.level(99, 0.0) == GLASS_IDLE);
    l.set(1, GLASS_HOVER, 1000.0);
    assert!(l.animating(1000.0) && l.level(1, 1000.0) == GLASS_IDLE && l.level(0, 1050.0) == GLASS_IDLE);
    let mid = l.level(1, 1000.0 + GLASS_HOVER_MS / 2.0);
    assert!(mid > (GLASS_IDLE + GLASS_HOVER) / 2.0 && mid < GLASS_HOVER); // ease-out: past halfway at half time
    l.set(1, GLASS_HOVER, 1000.0 + GLASS_HOVER_MS / 2.0); // the same target: no restart
    assert!(l.level(1, 1000.0 + GLASS_HOVER_MS) == GLASS_HOVER && !l.animating(1000.0 + GLASS_HOVER_MS));
    l.set(1, GLASS_IDLE, 2000.0);
    l.set(1, GLASS_ACTIVE, 2000.0 + GLASS_HOVER_MS / 4.0); // retargeted mid-ease: continuous, no jump back to idle
    let at = l.level(1, 2000.0 + GLASS_HOVER_MS / 4.0);
    assert!(at < GLASS_HOVER && at > GLASS_IDLE);
    assert_eq!(l.level(1, 3000.0), GLASS_ACTIVE);
}

#[test]
fn restore_step_eases() {
    let mut s = RestoreStep::default();
    assert!(s.value(0.0) == 0.0 && !s.animating(0.0));
    s.set(true, 1000.0);
    assert!(s.value(1000.0) == 0.0 && s.animating(1000.0) && near(s.value(1110.0), 0.5, 1e-6));
    assert!(s.value(1220.0) == 1.0 && !s.animating(1220.0));
    s.set(true, 1300.0); // the same target: no restart
    assert!(!s.animating(1300.0));
    s.set(false, 2000.0);
    s.set(true, 2110.0); // reversed half way: from where it is
    assert!(near(s.value(2110.0), 0.5, 1e-6) && s.value(2330.0) == 1.0);
}

#[test]
fn texts_fit() {
    assert!(glass_panel_hint(true).contains("TAB") && glass_panel_hint(false).contains("Tab"));
    assert!(glass_panel_hint(true).bytes().all(|c| (0x20..0x7f).contains(&c)));
    assert!(hud_text_width(glass_panel_hint(true), 2) <= (PANEL_W - 2.0 * PANEL_PAD) as u32);
    // Every field's value, at both bounds and one step in from the low one, drawn right-aligned at PANEL_W - PANEL_PAD,
    // starts right of the slider track (pixel font).
    let bounds = placement_menu_bounds();
    let mut st = open_on(1);
    st.field = 3;
    for p in &mut st.placements {
        *p = placement_menu_flat();
    }
    st.placements[1] = [125, -70, 150, 90];
    for (field, &(lo, hi)) in bounds.iter().enumerate() {
        for v in [lo, hi, lo + 1] {
            let mut at = st.clone();
            at.placements[1][field] = v;
            let value = &placement_menu_rows(&at, true)[field].value;
            let left = PANEL_W - PANEL_PAD - hud_text_width(value, 2) as f32;
            assert!(left >= PANEL_TRACK_X1, "field {field} value {value}");
        }
    }
}

#[test]
fn one_open_at_a_time() {
    // One open at a time; opening another collapses the first; the content fades in over the last part of the expansion;
    // value changes re-render lazily (once per frame however many arrive) without restarting anything.
    let mut g = GlassPills::default();
    assert!(!g.any_expanded(0.0) && !g.animating(0.0) && g.content_desktop() == -1 && g.content_alpha(0.0) == 0.0);
    let mut st = open_on(1);
    assert!(g.set(&st, 1000.0, None) && g.opened() == 1 && g.animating(1000.0) && g.expand(1, 1000.0) == 0.0);
    assert!(g.expand(1, 1000.0 + GLASS_EXPAND_MS) == 1.0 && !g.animating(1000.0 + GLASS_EXPAND_MS) && g.expand(0, 2000.0) == 0.0);
    assert!(g.content_desktop() == 1 && g.content_alpha(1000.0 + GLASS_EXPAND_MS) == 1.0);
    assert_eq!(g.content_alpha(1000.0 + GLASS_EXPAND_MS * 0.3), 0.0);
    let mut t = 1000.0;
    while t < 1000.0 + GLASS_EXPAND_MS {
        assert!(g.expand(1, t + 10.0) >= g.expand(1, t));
        t += 10.0;
    }
    assert!(g.ensure() && g.renders() == 1);
    assert!(!g.set(&st, 1500.0, None) && g.renders() == 1);
    for v in 10..20 {
        st.placements[1][3] = v;
        assert!(g.set(&st, 1500.0, None));
    }
    assert_eq!(g.renders(), 1);
    g.ensure();
    assert_eq!(g.renders(), 2); // ten values, one render
    assert!(!g.ensure());
    let next = PlacementMenuState { desktop: 2, ..st.clone() };
    assert!(g.set(&next, 2000.0, None) && g.content_desktop() == 2 && g.opened() == 2 && g.closed() == 1);
    assert!(g.expand(1, 2000.0 + GLASS_COLLAPSE_MS) == 0.0 && g.expand(2, 2000.0 + GLASS_EXPAND_MS) == 1.0);
    let shut = PlacementMenuState { open: false, ..next.clone() };
    assert!(g.set(&shut, 3000.0, None) && !g.open() && g.closed() == 2 && g.content_desktop() == 2); // the content stays for the collapse
    assert!(g.content_alpha(3000.0) == 1.0 && g.content_alpha(3000.0 + GLASS_COLLAPSE_MS) == 0.0);
    assert!(!g.any_expanded(3000.0 + GLASS_COLLAPSE_MS));
    assert!(!g.set(&shut, 3100.0, None));
    // Reversing mid-way does not jump.
    assert!(g.set(&next, 4000.0, None));
    let mid = g.expand(2, 4000.0 + GLASS_EXPAND_MS / 2.0);
    assert!(near(mid, 0.875, 1e-3) && g.set(&shut, 4000.0 + GLASS_EXPAND_MS / 2.0, None));
    assert_eq!(g.expand(2, 4000.0 + GLASS_EXPAND_MS / 2.0), mid);
}

#[test]
fn accent_and_font_rerender() {
    let mut g = GlassPills::default();
    assert!(!g.set_accent([0.1, 0.2, 0.3])); // no content yet: the first render uses it
    assert_eq!(g.accent(), [0.1, 0.2, 0.3]);
    g.font_changed();
    assert!(!g.ensure());
    g.set(&open_on(0), 0.0, None);
    assert!(g.ensure() && g.generation() == 1);
    assert!(!g.set_accent([0.1, 0.2, 0.3]) && g.set_accent([0.3, 0.2, 0.1]) && g.generation() == 2 && g.ensure());
    g.font_changed(); // the same generation, drawn again
    assert!(g.ensure() && g.generation() == 2 && g.renders() == 3);
}

#[test]
fn frame_reports_content_to_draw() {
    let mut g = GlassPills::default();
    g.set(&open_on(1), 0.0, None);
    assert!(!g.frame(true, 10.0).content_dirty); // not visible yet: not drawn
    let f = g.frame(true, GLASS_EXPAND_MS);
    assert!(f.content == 1 && f.content_dirty && f.generation == 1 && f.content_alpha == 1.0);
    assert!(!g.frame(true, GLASS_EXPAND_MS + 10.0).content_dirty && g.renders() == 1);
    assert_eq!(g.rendered(), &open_on(1));
}

#[test]
fn card_fade_hold() {
    // An unseen fade does not run; a toggle after `since` holds at the toggle's own level.
    let mut f = CardFade::new(100.0, 100.0);
    f.set(true, 0.0);
    assert!(near(f.level(50.0), 0.5, 1e-3));
    f.hold(50.0, 1000.0); // nothing ran from 50 to 1000
    assert!(near(f.level(1000.0), 0.5, 1e-3) && near(f.level(1050.0), 1.0, 1e-3) && f.animating(1000.0));
    let mut g = CardFade::new(100.0, 100.0);
    g.hold(-5000.0, 10.0); // a stale `since` from long before the toggle...
    g.set(true, 20.0);
    g.hold(-5000.0, 900.0);
    assert!(g.level(900.0) == 0.0 && near(g.level(950.0), 0.5, 1e-3)); // ...holds at the toggle's level, never stalls past it
    g.hold(950.0, 950.0);
    assert!(near(g.level(950.0), 0.5, 1e-3)); // no time passed: no change
}

#[test]
fn morph_waits_for_the_overview() {
    // Opening a panel from a zoomed-in view (Ctrl+Alt+L zooms out in the same write): the morph waits for the overview,
    // so the first frame that can show it starts near the pill, and the whole expansion is seen.
    for idle_before in [0.0f64, 5000.0] {
        // frames right up to the open, or none for a while (stale hidden time)
        let mut g = GlassPills::default();
        let mut view = ViewAnimator::new(220.0, 300.0);
        view.set_target(true, 1, 40, 0.0); // zoomed in on XR-2
        let frame_ms = 1000.0 / 120.0;
        let mut t = 0.0;
        while t < 400.0 {
            view.advance(t);
            g.frame(view.zoom() < 0.01, t); // settled, zoomed: unseen frames
            t += frame_ms;
        }
        let open_at = t + idle_before;
        view.set_target(false, 1, 40, open_at);
        assert!(g.set(&open_on(1), open_at, None));
        let mut first = GlassFrame::default();
        let mut first_at = -1.0;
        t = open_at + 1.0;
        while t < open_at + 1000.0 {
            view.advance(t);
            let f = g.frame(view.zoom() < 0.01, t);
            if view.zoom() >= 0.01 {
                assert!(f.expand[1] == 0.0 && f.content_alpha == 0.0 && f.content == -1 && f.top == -1); // nothing drawn yet
                t += frame_ms;
                continue;
            }
            first = f;
            first_at = t;
            break;
        }
        assert!(first_at > open_at + 150.0); // the zoom-out takes most of 220 ms
        assert!(first.expand[1] < 0.05 && first.content_alpha == 0.0 && first.top == 1); // starts as (nearly) the pill
        assert!(g.animating(first_at) && near(g.expand(1, first_at + GLASS_EXPAND_MS), 1.0, 1e-3)); // and runs its full length
        assert!(g.frame(true, first_at + GLASS_EXPAND_MS / 2.0).expand[1] > 0.2);
        assert!(near(g.frame(true, first_at + GLASS_EXPAND_MS + 1.0).content_alpha, 1.0, 1e-3));
    }
    // In the overview nothing is held, and a collapse while zooming in just runs out (it is not saved for later).
    let mut g = GlassPills::default();
    let mut st = open_on(0);
    g.set(&st, 0.0, None);
    assert!(n3(g.frame(true, 130.0).expand[0], g.expand(0, 130.0)) && n3(g.expand(0, 260.0), 1.0));
    st.open = false;
    g.set(&st, 300.0, None);
    let mut t = 300.0;
    while t < 600.0 {
        g.frame(false, t);
        t += 8.0;
    }
    assert!(g.expand(0, 600.0) == 0.0 && !g.any_expanded(600.0) && !g.animating(600.0));
}

#[test]
fn draw_order() {
    // The open panel's desktop last; while collapsing, the most expanded one; otherwise unchanged.
    assert_eq!(glass_draw_order(vec![2, 0, 1], 0), [2, 1, 0]);
    assert_eq!(glass_draw_order(vec![2, 0, 1], 1), [2, 0, 1]);
    assert_eq!(glass_draw_order(vec![2, 0, 1], -1), [2, 0, 1]);
    assert_eq!(glass_draw_order(vec![2, 0], 1), [2, 0]);
    let mut g = GlassPills::default();
    assert_eq!(g.frame(true, 0.0).top, -1);
    let mut st = open_on(0);
    g.set(&st, 0.0, None);
    assert_eq!(g.frame(true, 0.0).top, 0); // open, even before it has grown
    st.desktop = 2;
    g.set(&st, 1000.0, None);
    assert_eq!(g.frame(true, 1000.0).top, 2); // Tab: the new one is on top of the one collapsing
    st.open = false;
    g.set(&st, 2000.0, None);
    assert!(g.frame(true, 2050.0).top == 2 && g.frame(true, 2000.0 + GLASS_COLLAPSE_MS).top == -1);
}

#[test]
fn a_resize_keeps_the_glass_size() {
    // From half to ten times the desktop's size (distance 2 .. 0.1, flat or curved, at its slot or turned round you)
    // the pill and the panel appear exactly as big as at distance 1, and the pill stays GLASS_PILL_GAP - BORDER_PX (as
    // seen) under the selected desktop's border, which moves and grows with the resize.
    let cv = Canvas::new(1920.0, 1200.0, 0.36, 0.225);
    let tile = FRect::new(666.67, 435.0, 586.67, 330.0);
    let seen = |p: &Placement, q: [f32; 2]| {
        // tile surface px -> where it appears, canvas px
        let w = place_point(&cv, &tile, p, q[0], q[1]);
        [(w.x / -w.z / cv.half_w + 1.0) * cv.view_w / 2.0, (1.0 - w.y / -w.z / cv.half_h) * cv.view_h / 2.0]
    };
    // Apparent size: a glass rect's world width and height (through its centre lines) over the desktop centre's
    // distance from the eye.
    let size_of = |p: &Placement, r: &FRect| {
        let cx = r.x + r.width / 2.0;
        let cy = r.y + r.height / 2.0;
        let world = |gx, gy| {
            let q = glass_to_desktop(&tile, p, gx, gy);
            place_point(&cv, &tile, p, q[0], q[1])
        };
        let dist = |a: Vec3, b: Vec3| ((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z)).sqrt();
        let c = place_point(&cv, &tile, p, tile.x + tile.width / 2.0, tile.y + tile.height / 2.0);
        let eye = (c.x * c.x + c.y * c.y + c.z * c.z).sqrt();
        [dist(world(r.x, cy), world(r.x + r.width, cy)) / eye, dist(world(cx, r.y), world(cx, r.y + r.height)) / eye]
    };
    let canvas_size = |p: &Placement, r: &FRect| {
        let a = seen(p, glass_to_desktop(&tile, p, r.x, r.y));
        let b = seen(p, glass_to_desktop(&tile, p, r.x + r.width, r.y + r.height));
        [b[0] - a[0], b[1] - a[1]]
    };
    let pill = glass_pill(&tile);
    let panel = glass_panel(&tile);
    let unit = Placement::default();
    let pill1 = canvas_size(&unit, &pill);
    assert!(near(pill1[0], pill.width, 0.05) && near(pill1[1], GLASS_PILL_H, 0.05)); // the default: as laid out
    for d in [0.1f32, 0.25, 0.5, 1.5, 2.0] {
        for p in [Placement::new(0.0, 0.0, d, 0.0, 0.0), Placement::new(0.0, 0.0, d, 1.0, 0.0), Placement::new(0.3, -0.1, d, 0.0, 0.0)] {
            let at1 = Placement::new(p.x, p.y, 1.0, p.curvature, 0.0);
            let a = size_of(&p, &pill);
            let a1 = size_of(&at1, &pill);
            assert!(near(a[0], a1[0], 0.02 * a1[0]) && near(a[1], a1[1], 0.02 * a1[1]));
            if p.curvature == 0.0 {
                // the panel is wide: on a curved desktop its chord shrinks a little as it wraps
                let b = size_of(&p, &panel);
                let b1 = size_of(&at1, &panel);
                assert!(near(b[0], b1[0], 1e-3 * b1[0]) && near(b[1], b1[1], 1e-3 * b1[1]));
            }
            if p.x == 0.0 && p.curvature == 0.0 {
                // flat at its slot: exactly the same on the canvas, and the gap under the (resized) bottom edge too
                let c = canvas_size(&p, &pill);
                let e = canvas_size(&p, &panel);
                let e1 = canvas_size(&unit, &panel);
                assert!(near(c[0], pill1[0], 0.05) && near(c[1], pill1[1], 0.05) && near(e[0], e1[0], 0.1) && near(e[1], e1[1], 0.1));
                let border = seen(&p, [tile.x + tile.width / 2.0, tile.y + tile.height + BORDER_PX])[1];
                let top = seen(&p, glass_to_desktop(&tile, &p, pill.x + pill.width / 2.0, pill.y))[1];
                assert!(near(top - border, GLASS_PILL_GAP - BORDER_PX, 0.05));
            }
        }
    }
    // The desktop itself does scale (ten times at the floor) while its glass does not.
    let w1 = seen(&unit, [tile.x + tile.width, 600.0])[0] - seen(&unit, [tile.x, 600.0])[0];
    let huge = Placement::new(0.0, 0.0, 0.1, 0.0, 0.0);
    assert!(near(seen(&huge, [tile.x + tile.width, 600.0])[0] - seen(&huge, [tile.x, 600.0])[0], 10.0 * w1, 0.01 * w1));
    // The mesh the compositor draws: glass px kept for the shader, world points through glass_to_desktop.
    for v in glass_mesh(&cv, &tile, &huge, &pill) {
        let at = glass_to_desktop(&tile, &huge, v.px, v.py);
        let w = place_point(&cv, &tile, &huge, at[0], at[1]);
        assert!(near(v.world.x, w.x, 1e-5) && near(v.world.y, w.y, 1e-5) && near(v.world.z, w.z, 1e-5));
        assert!(near(v.px, pill.x, 1e-2) || near(v.px, pill.x + pill.width, 1e-2));
        assert!(near(v.py, pill.y, 1e-2) || near(v.py, pill.y + pill.height, 1e-2));
    }
    assert_eq!(glass_to_desktop(&tile, &unit, 700.0, 800.0), [700.0, 800.0]); // distance 1: the tile's own px
}

#[test]
fn the_glass_hangs_under_the_border() {
    // The border is tile px, so it grows with its desktop; the glass keeps its apparent size, gap included. So the glass
    // hangs from the border's outer edge: at every size, curvature, roll and place, and all along the morph, its top is
    // the same gap (as seen) under that edge, which is tile px BORDER_PX under the desktop.
    let cv = Canvas::new(1536.0, 960.0, 0.36, 0.225);
    let hangs_under_the_border = |tile: &FRect, p: &Placement| {
        let edge = tile.y + tile.height + BORDER_PX;
        let want = (GLASS_PILL_GAP - BORDER_PX) * p.distance;
        for e in [0.0f32, 0.25, 0.5, 0.75, 1.0] {
            let r = glass_morph(tile, e);
            for gx in [r.x, r.x + r.width / 2.0, r.x + r.width] {
                let q = glass_to_desktop(tile, p, gx, r.y);
                assert!(q[1] > edge && near(q[1] - edge, want, 2e-3));
            }
        }
    };
    // The real rows, one to four desktops, 16:9 to 32:9: the drawn pill stays out of every corner's cap, in any state.
    let clear_of_the_caps = |tile: &FRect, p: &Placement| {
        let pill = glass_pill(tile);
        let a = glass_to_desktop(tile, p, pill.x, pill.y);
        let b = glass_to_desktop(tile, p, pill.x + pill.width, pill.y + pill.height);
        for corner in [2u32, 3] {
            let c = corner_cap_rect(tile, corner);
            assert!(b[0] <= c.x || a[0] >= c.x + c.width || b[1] <= c.y || a[1] >= c.y + c.height);
        }
    };
    for desktops in 1..=4 {
        for shape in [Size::new(1920.0, 1080.0), Size::new(2560.0, 1080.0), Size::new(3840.0, 1080.0)] {
            let sizes = vec![shape; desktops];
            for tile in &overview_layout(&sizes, 1536.0, 960.0, 40.0) {
                for d in [0.1f32, 0.15, 0.25, 0.4, 0.55, 0.6, 0.65, 0.8, 1.0, 1.25, 1.5, 2.0] {
                    for curve in [0.0f32, 1.0, 2.0944] {
                        #[allow(clippy::approx_constant)] // glass_test.cpp's own values, not quite pi / 2
                        for roll in [0.0f32, 0.3, -0.74, 1.5708] {
                            for orbit in [[0.0f32, 0.0], [0.3, -0.1]] {
                                let p = Placement::new(orbit[0], orbit[1], d, curve, roll);
                                hangs_under_the_border(tile, &p);
                                clear_of_the_caps(tile, &p);
                            }
                        }
                    }
                }
            }
        }
    }
    // The last moment of a zoom, while the pills still show (below 1%: wayland.cpp): the same on what is drawn then.
    let sizes = vec![Size::new(1920.0, 1080.0); 3];
    let overview = overview_layout(&sizes, 1536.0, 960.0, 40.0);
    let now = blended_layout(&sizes, 1536.0, 960.0, 40.0, 0.009, 1.0);
    let resized =
        [Placement::new(0.0, 0.0, 0.6, 0.0, 0.0), Placement::new(0.0, 0.0, 0.3, 1.0, 0.2), Placement::new(0.1, 0.0, 1.5, 0.0, 0.0)];
    let drawn = zoomed_placements(&cv, &overview, &now, &resized, 0.009, 1.0);
    for (tile, p) in now.iter().zip(&drawn) {
        hangs_under_the_border(tile, p);
    }
}

#[test]
fn a_held_slider_draws_on_the_press_surface() {
    // The open panel is drawn on the press-time surface, the desktop's own placement is untouched.
    let wire: MenuPlacement = [20, -10, 150, 60];
    let p = placement_from_wire(&wire);
    let rad = std::f32::consts::PI / 180.0;
    assert!(n3(p.x, 2.0 * rad) && n3(p.y, -rad) && n3(p.distance, 1.5) && n3(p.curvature, 60.0 * rad) && p.roll == 0.0);
    let edge = placement_from_wire(&[-1800, 900, 50, 120]);
    assert!(n3(edge.x, -180.0 * rad) && n3(edge.y, 90.0 * rad) && n3(edge.distance, 0.5) && n3(edge.curvature, 120.0 * rad));
    assert!(n3(placement_from_wire(&[0, 0, 10, 0]).distance, 0.1)); // the distance floor: ten times the size
    let mut g = GlassPills::default();
    let st = open_on(1);
    assert!(g.set(&st, 0.0, None) && g.frame(true, 300.0).surface.is_none() && g.frame(true, 300.0).surface_desktop == -1);
    let generation = g.generation();
    let renders = g.renders();
    assert!(g.set(&st, 400.0, Some(wire)));
    let held = g.frame(true, 400.0);
    assert!(g.generation() == generation && g.renders() == renders); // the surface alone does not re-render the content
    let surface = held.surface.expect("held surface");
    assert!(held.surface_desktop == 1 && n3(surface.x, 2.0 * rad) && n3(surface.distance, 1.5));
    assert!(!g.set(&st, 410.0, Some(wire)));
    assert!(g.set(&st, 500.0, None) && g.frame(true, 500.0).surface.is_none() && g.surface().is_none()); // released
    let shut = PlacementMenuState { open: false, ..st.clone() };
    g.set(&st, 600.0, Some(wire));
    assert!(g.set(&shut, 700.0, Some(wire)) && g.surface().is_none() && g.frame(true, 700.0).surface.is_none()); // never without an open panel
}

#[test]
fn level_bar_and_caps() {
    // The bar is centred LEVEL_BAR_GAP above the top edge; it scales down on a narrow desktop, never wider than it. Not in
    // glass_test.cpp: glass_contract_test.py checks these against free_pointer.py; this pins the shape here too.
    const WIDE: FRect = FRect { x: 100.0, y: 300.0, width: 800.0, height: 450.0 };
    let r = level_bar_rect(&WIDE);
    assert!(level_bar_scale(&WIDE) == 1.0 && r.width == LEVEL_BAR_W && r.height == LEVEL_BAR_H);
    assert!(n3(r.x + r.width / 2.0, WIDE.x + WIDE.width / 2.0) && n3(r.y + r.height + LEVEL_BAR_GAP, WIDE.y));
    assert!(n3(level_bar_scale(&FRect::new(0.0, 0.0, 300.0, 200.0)), LEVEL_BAR_MIN_W / LEVEL_BAR_W));
    assert!(n3(level_bar_scale(&FRect::new(0.0, 0.0, 40.0, 30.0)), 40.0 / LEVEL_BAR_W));
    for empty in [FRect::new(0.0, 0.0, 0.0, 30.0), FRect::new(0.0, 0.0, f32::NAN, 30.0)] {
        assert_eq!(level_bar_scale(&empty), 0.0);
        assert_eq!(level_bar_rect(&empty), FRect::default());
        assert_eq!(corner_cap_rect(&empty, 0), FRect::default());
        assert_eq!(level_bar_segment(&empty, 0.0, 0.0), LevelSegment::None);
    }
    let cy = r.y + r.height / 2.0;
    assert_eq!(level_bar_segment(&WIDE, r.x + 1.0, cy), LevelSegment::Ccw);
    assert_eq!(level_bar_segment(&WIDE, r.x + r.width / 2.0, cy), LevelSegment::Level);
    assert_eq!(level_bar_segment(&WIDE, r.x + r.width - 1.0, cy), LevelSegment::Cw);
    assert_eq!(level_bar_segment(&WIDE, r.x - LEVEL_BAR_PAD, cy), LevelSegment::Ccw); // the padding is its segment's
    assert_eq!(level_bar_segment(&WIDE, r.x - LEVEL_BAR_PAD - 0.1, cy), LevelSegment::None);
    assert_eq!(corner_cap_rect(&WIDE, 4), FRect::default());
    let c = corner_cap_rect(&WIDE, 3);
    assert!(n3(c.x, WIDE.x + WIDE.width - CAP_INNER) && n3(c.y, WIDE.y + WIDE.height - CAP_INNER) && n3(c.width, CAP_OUTER + CAP_INNER));
    // The 3D button: right of the bar, both parts on its line; none on a desktop too narrow for it.
    let main = immersive_main_rect(&WIDE);
    let tail = immersive_tail_rect(&WIDE);
    assert!(n3(main.x, r.x + r.width + IMMERSIVE_GAP) && main.y == r.y && main.width == IMMERSIVE_MAIN_W);
    assert!(n3(tail.x, main.x + main.width + IMMERSIVE_GAP) && tail.width == IMMERSIVE_TAIL_W);
    assert_eq!(immersive_part(&WIDE, main.x + 1.0, cy), ImmersivePart::Main);
    assert_eq!(immersive_part(&WIDE, tail.x + 1.0, cy), ImmersivePart::Tail);
    assert_eq!(immersive_part(&WIDE, tail.x + tail.width + LEVEL_BAR_PAD + 0.1, cy), ImmersivePart::None);
    let narrow = FRect::new(0.0, 0.0, 160.0, 90.0);
    assert!(immersive_main_rect(&narrow).width == 0.0 && immersive_tail_rect(&narrow).width == 0.0);
    assert_eq!(immersive_part(&narrow, 100.0, -20.0), ImmersivePart::None);
}
