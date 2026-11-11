//! The glass pixels: the placement panel ([`render_glass_panel`]), the options menu ([`render_options_panel`]), the
//! handle atlas ([`render_handle_atlas`]) and lit glass on the CPU ([`glass_draw`]). Ported from `spike/src/glass.cpp`,
//! f32 maths in the C++ order, so every image is byte-equal to the C++ one for the same state and font, with the system
//! font or the built-in 5x7 pixel font (golden/glass_cards.txt). The geometry they draw at is glass.rs's.

// `!(x > 0.0)` is the C++'s own test and is true for NaN, where `x <= 0.0` is not: kept as written.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

use xr_wire::control::{OptionsMenu, OptionsRow};

use crate::desktops::{
    RESOLUTION_COUNT, RESOLUTION_GROUP_COUNT, RESOLUTION_GROUPS, RESOLUTIONS, SCALE_PERCENTS, SCALE_STEPS,
};
use crate::glass::{
    ATLAS_BAR_H, ATLAS_CAP, ATLAS_GAP, ATLAS_MAIN_W, ATLAS_MAIN_X, ATLAS_OLD_H, ATLAS_PILL_W, ATLAS_PILL_Y,
    ATLAS_TAIL_W, ATLAS_TAIL_X, AtlasCell, CAP_BEND, CAP_CORNERS, CAP_GAP, CAP_INNER, CAP_LEG, CAP_LEG_HOT, CAP_STROKE,
    CAP_STROKE_HOT, GLASS_HOVER, GLASS_IDLE, GLASS_RIM_PX, GRABBER_H, GRABBER_TOP, GlassLook, HANDLE_ATLAS_H,
    HANDLE_ATLAS_SCALE, HANDLE_ATLAS_W, IMMERSIVE_INNER_R, IMMERSIVE_TEXT_PX, IMMERSIVE_TICK_GAP, IMMERSIVE_TICK_H,
    IMMERSIVE_TICK_R, IMMERSIVE_TICK_W, LEVEL_SEGMENTS, LevelSegment, PANEL_ACTION_W, PANEL_ACTIONS,
    PANEL_BACKGROUND_TOP, PANEL_BRIGHTNESS_TOP, PANEL_FOOTER_TOP, PANEL_H, PANEL_HEADER_H, PANEL_HINT_PX,
    PANEL_KNOB_HI, PANEL_KNOB_LO, PANEL_KNOB_R, PANEL_PAD, PANEL_RESOLUTION_TOP, PANEL_ROW_H, PANEL_ROWS_TOP,
    PANEL_SCALE_TOP, PANEL_TEXT_PX, PANEL_TRACK_X0, PANEL_TRACK_X1, PANEL_W, RES_ITEM_H, RES_LIST_X, bar_cell, bar_row,
    button_row, cap_cell, cell, glass_background_box, glass_background_item, glass_background_switch, glass_lit_look,
    glass_panel_action, glass_panel_action_label, glass_panel_button_style, glass_panel_hint, glass_resolution_box,
    glass_resolution_enabled, glass_resolution_heading, glass_resolution_item, glass_resolution_label,
    glass_resolution_list, glass_scale_box, glass_scale_enabled, glass_scale_item, glass_scale_label, glass_shade,
};
use crate::glass_theme::glass_theme_scheme;
use crate::hud::{
    BACKGROUND_COUNT, BACKGROUNDS, BRIGHTNESS_DEFAULT, BRIGHTNESS_MAX, BRIGHTNESS_MIN, GLYPH_H, GLYPH_W, HudImage,
    HudTextMetrics, PLACEMENT_MENU_BRIGHTNESS, PLACEMENT_MENU_DESKTOP_COUNT, PLACEMENT_MENU_FIELDS, PlacementMenuState,
    hud_glyph, hud_text_width, placement_menu_bounds, placement_menu_flat, placement_menu_rows,
};
use crate::material::{MD_DISABLED_CONTENT, MD_SHAPE_EXTRA_SMALL, MD_STATE_FOCUS, MaterialScheme, MdRgb, md_layer};
use crate::material_draw::{
    MdIcon, md_corner_distance, md_disc, md_fill_rounded, md_icon, md_over, md_rounded_distance, md_stroke_rounded,
};
use crate::options_geometry::{
    OPTIONS_CLOSE_X0, OPTIONS_CLOSE_X1, OPTIONS_NEXT_X0, OPTIONS_SCOPE_X0, OPTIONS_TAB_PITCH, OPTIONS_TAB_W,
    OPTIONS_TABS_PER_PAGE, OPTIONS_TABS_X, OPTIONS_TRACK_X0, OPTIONS_TRACK_X1,
};
use crate::view::{FRect, cclamp, cmax, cmin};

type Font<'a> = Option<&'a dyn HudTextMetrics>;

/// `std::lround` of a float: half away from zero.
fn lround(v: f32) -> i32 {
    v.round() as i32
}

/// `std::isnan(v) ? 0 : std::clamp(v, 0, 1)`.
fn clamp01(v: f32) -> f32 {
    if v.is_nan() { 0.0 } else { cclamp(v, 0.0, 1.0) }
}

/// `std::clamp` of doubles: `v < lo ? lo : hi < v ? hi : v`.
fn dclamp(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

// The panel's Material parts, panel px. The selected row's band (an M3 active indicator: fully rounded) is inset
// BAND_INSET from the panel's sides and 2 px from its row; slider tracks are TRACK_H tall, the inactive part the primary
// at TRACK_ALPHA (M3); the selected slider's handle has a focus halo of HALO_R. Buttons: M3 label large (bold) at
// BUTTON_LABEL_PX with a BUTTON_ICON_PX icon BUTTON_ICON_GAP before it, when every button's icon and label fit inside
// BUTTON_PAD_MIN of padding (with the pixel font they do not: labels only).
const BAND_LIGHT: f32 = 0.55;
const BAND_INSET: f32 = 10.0;
const TRACK_H: f32 = 4.0;
const TRACK_ALPHA: f32 = 0.24;
const HALO_R: f32 = 2.0 * PANEL_KNOB_R;
const _: () = assert!(RES_LIST_X <= BAND_INSET, "the open Resolution list hides the selected row's band under it");
const BUTTON_LABEL_PX: u32 = 18;
const BUTTON_ICON_PX: f32 = 16.0;
const BUTTON_ICON_GAP: f32 = 6.0;
const BUTTON_PAD_MIN: f32 = 8.0;
const ACTION_ICONS: [MdIcon; PANEL_ACTIONS] = [MdIcon::Width, MdIcon::Add, MdIcon::Remove];
// The Resolution and Scale dropdowns (M3 exposed dropdown menus): a filled text field (extra small corners; disabled:
// on-surface at FIELD_DISABLED) with its value FIELD_PAD_X in and a CARET_PX drop-down arrow CARET_INSET from its right
// end, its active indicator INDICATOR_H tall (INDICATOR_FOCUS_H while its list is open); the lists are M3 menus, their
// items' text FIELD_PAD_X in.
const FIELD_DISABLED: f32 = 0.04;
const FIELD_PAD_X: f32 = 12.0;
const CARET_PX: f32 = 24.0;
const CARET_INSET: f32 = 16.0;
const INDICATOR_H: f32 = 1.0;
const INDICATOR_FOCUS_H: f32 = 2.0;
// Short labels: the label column is PANEL_TRACK_X0 - PANEL_PAD wide.
const LABELS: [&str; PLACEMENT_MENU_FIELDS] = ["Left/right", "Down/up", "Distance", "Curvature"];
// The Background switch's handle radius (M3: larger while on).
const SWITCH_HANDLE_ON: f32 = 9.0;
const SWITCH_HANDLE_OFF: f32 = 6.0;

/// A panel's focused or selected row band: lit glass (glass_lit_look, hovered) on `container`, its rim and sheen in `on`
/// toned down to BAND_LIGHT toward it, so they stay quieter than a pill's against the dark panel.
fn lit_band(container: MdRgb, on: MdRgb, height: f32) -> GlassLook {
    glass_lit_look(container, md_layer(container, on, BAND_LIGHT), GLASS_HOVER, height, GLASS_RIM_PX)
}

/// Text at an em size: the system font, or the built-in pixel font (ASCII, zoom 2) without one.
fn text_width(font: Font, s: &str, px: u32, bold: bool) -> u32 {
    match font {
        Some(f) => f.measure(s, px, bold),
        None => hud_text_width(s, 2),
    }
}

/// Draws `s` with its left edge at `left`, vertically centred on `cy` (capitals centred), `alpha` opaque.
#[allow(clippy::too_many_arguments)]
fn text(im: &mut HudImage, font: Font, s: &str, px: u32, bold: bool, left: f32, cy: f32, c: MdRgb, alpha: f32) {
    if let Some(f) = font {
        let baseline = lround(cy + 0.36 * px as f32); // JetBrains Mono capitals: ~0.73 em
        f.draw(s, px, bold, lround(left), baseline, &mut |x, y, cov| md_over(im, x, y, c, cov * alpha));
        return;
    }
    const Z: u32 = 2;
    let mut x = lround(left);
    let top = lround(cy - (GLYPH_H * Z) as f32 / 2.0);
    for &ch in s.as_bytes() {
        let g = hud_glyph(ch);
        for row in 0..GLYPH_H {
            for col in 0..GLYPH_W {
                if g[row as usize] & (0x10u8 >> col) != 0 {
                    for sy in 0..Z {
                        for sx in 0..Z {
                            md_over(im, x + (col * Z + sx) as i32, top + (row * Z + sy) as i32, c, alpha);
                        }
                    }
                }
            }
        }
        x += ((GLYPH_W + 1) * Z) as i32;
    }
}

/// A panel row with an M3 exposed dropdown menu (the Resolution and Scale rows): the row's label, then a filled text field
/// (surface container highest) at `bx` showing `value` in label large (bold, `label_px`), a drop-down arrow (pointing up,
/// in the primary, while its list is open) and an active indicator under it (on-surface-variant; 2 px of primary while
/// open); M3 disabled (on-surface at 4% / 38%) unless `usable`.
#[allow(clippy::too_many_arguments)]
fn dropdown_row(
    im: &mut HudImage,
    font: Font,
    m: &MaterialScheme,
    label: &str,
    row_top: f32,
    bx: &FRect,
    value: &str,
    label_px: u32,
    open: bool,
    usable: bool,
) {
    let cy = row_top + PANEL_ROW_H / 2.0;
    let x1 = bx.x + bx.width;
    let y1 = bx.y + bx.height;
    let content_alpha = if usable { 1.0 } else { MD_DISABLED_CONTENT };
    text(im, font, label, PANEL_TEXT_PX, false, PANEL_PAD, cy, m.on_surface, 1.0);
    md_fill_rounded(
        im,
        bx.x,
        bx.y,
        x1,
        y1,
        MD_SHAPE_EXTRA_SMALL,
        if usable { m.surface_container_highest } else { m.on_surface },
        if usable { 1.0 } else { FIELD_DISABLED },
    );
    let indicator = if open { INDICATOR_FOCUS_H } else { INDICATOR_H };
    let line = if open {
        m.primary
    } else if usable {
        m.on_surface_variant
    } else {
        m.on_surface
    };
    md_fill_rounded(im, bx.x, y1 - indicator, x1, y1, 0.0, line, content_alpha);
    text(im, font, value, label_px, true, bx.x + FIELD_PAD_X, cy, m.on_surface, content_alpha);
    md_icon(
        im,
        if open { MdIcon::ArrowDropUp } else { MdIcon::ArrowDropDown },
        x1 - CARET_INSET,
        cy,
        CARET_PX,
        line,
        content_alpha,
    );
}

/// Lit glass on the CPU: [`glass_shade`] over `r` (no shadow), premultiplied over what `im` holds.
pub fn glass_draw(im: &mut HudImage, look: &GlassLook, r: &FRect, radius: f32) {
    let mut k = *look;
    k.shadow = 0.0;
    let x0 = 0.max(r.x.floor() as i32);
    let y0 = 0.max(r.y.floor() as i32);
    let x1 = (im.width as i32).min((r.x + r.width).ceil() as i32);
    let y1 = (im.height as i32).min((r.y + r.height).ceil() as i32);
    for y in y0..y1 {
        for x in x0..x1 {
            let c = glass_shade(&k, r, radius, x as f32 + 0.5, y as f32 + 0.5, 1.0);
            if !(c[3] > 0.0) {
                continue;
            }
            let at = (y as usize * im.width as usize + x as usize) * 4;
            let p = &mut im.rgba[at..at + 4];
            for i in 0..4 {
                // premultiplied source over premultiplied destination
                p[i] = lround(cclamp(c[i] * 255.0 + f32::from(p[i]) * (1.0 - c[3]), 0.0, 255.0)) as u8;
            }
        }
    }
}

// --- The handle atlas --------------------------------------------------------------------------------------------------

fn main_cell(lit: bool, hot: bool) -> AtlasCell {
    cell(ATLAS_MAIN_X, button_row(lit, hot), ATLAS_MAIN_W, ATLAS_BAR_H)
}

fn tail_cell(lit: bool, hot: bool) -> AtlasCell {
    cell(ATLAS_TAIL_X, button_row(lit, hot), ATLAS_TAIL_W, ATLAS_BAR_H)
}

const K: f32 = HANDLE_ATLAS_SCALE as f32;

fn draw_bar(im: &mut HudImage, c: &AtlasCell, m: &MaterialScheme, hot: LevelSegment, active: bool) {
    let (x, y, w, h) = (c.x as f32, c.y as f32, c.w as f32, c.h as f32);
    let third = w / LEVEL_SEGMENTS as f32;
    md_fill_rounded(im, x, y, x + w, y + h, h / 2.0, m.primary_container, 1.0);
    for line in [x + third, x + 2.0 * third] {
        md_fill_rounded(
            im,
            line - K / 2.0,
            y + 7.0 * K,
            line + K / 2.0,
            y + h - 7.0 * K,
            K / 2.0,
            m.on_primary_container,
            0.3,
        );
    }
    if hot != LevelSegment::None {
        let seg = hot as i32;
        let x0 = x + seg as f32 * third + if seg == 0 { 2.0 * K } else { K };
        let x1 = x + (seg + 1) as f32 * third - if seg == 2 { 2.0 * K } else { K };
        let fill = if active { m.primary_bright } else { m.primary };
        md_fill_rounded(im, x0, y + 2.0 * K, x1, y + h - 2.0 * K, (h - 4.0 * K) / 2.0, fill, 1.0);
    }
    const ICONS: [(MdIcon, f32); LEVEL_SEGMENTS] =
        [(MdIcon::RotateLeft, 18.0), (MdIcon::Level, 23.0), (MdIcon::RotateRight, 18.0)];
    for (seg, (icon, size)) in ICONS.iter().enumerate() {
        let ink = if seg as i32 == hot as i32 { m.on_primary } else { m.on_primary_container };
        md_icon(im, *icon, x + (seg as f32 + 0.5) * third, y + h / 2.0, size * K, ink, 1.0);
    }
}

/// A 3D button control's container and ink: glass.rs immersive_main_box.
fn button_roles(m: &MaterialScheme, lit: bool, hot: bool) -> (MdRgb, MdRgb) {
    if lit {
        return (if hot { m.primary_bright } else { m.primary }, m.on_primary);
    }
    if hot { (m.primary_container, m.on_primary_container) } else { (m.secondary_container, m.on_secondary_container) }
}

fn draw_button_main(im: &mut HudImage, c: &AtlasCell, m: &MaterialScheme, font: Font, lit: bool, hot: bool) {
    let (x, y, w, h) = (c.x as f32, c.y as f32, c.w as f32, c.h as f32);
    let (fill, ink) = button_roles(m, lit, hot);
    md_fill_rounded(im, x, y, x + w, y + h, IMMERSIVE_INNER_R * K, fill, 1.0);
    let Some(font) = font else {
        // the pixel font: drawn centred on its 5 x 7 cells
        const Z: u32 = 5;
        let px_w = ((2 * GLYPH_W + 1) * Z) as i32;
        let left = lround(x + w / 2.0) - px_w / 2;
        let top = lround(y + h / 2.0) - (GLYPH_H * Z) as i32 / 2;
        for (n, &ch) in b"3D".iter().enumerate() {
            let g = hud_glyph(ch);
            for row in 0..GLYPH_H * Z {
                for col in 0..GLYPH_W * Z {
                    if (g[(row / Z) as usize] >> (GLYPH_W - 1 - col / Z)) & 1 != 0 {
                        md_over(
                            im,
                            left + n as i32 * ((GLYPH_W + 1) * Z) as i32 + col as i32,
                            top + row as i32,
                            ink,
                            1.0,
                        );
                    }
                }
            }
        }
        return;
    };
    // Centred on its ink, not its advance, as the design is: where the glyphs really land, measured by drawing them.
    const LABEL: &str = "3D";
    let px = lround(IMMERSIVE_TEXT_PX * K) as u32;
    let (mut x0, mut x1, mut y0, mut y1) = (1 << 20, -(1 << 20), 1 << 20, -(1 << 20));
    font.draw(LABEL, px, true, 0, 0, &mut |gx, gy, coverage| {
        if coverage > 0.0 {
            x0 = x0.min(gx);
            x1 = x1.max(gx);
            y0 = y0.min(gy);
            y1 = y1.max(gy);
        }
    });
    let left = lround(x + w / 2.0 - (x0 + x1 + 1) as f32 / 2.0);
    let baseline = lround(y + h / 2.0 - (y0 + y1 + 1) as f32 / 2.0);
    font.draw(LABEL, px, true, left, baseline, &mut |gx, gy, coverage| md_over(im, gx, gy, ink, coverage));
}

fn draw_button_tail(im: &mut HudImage, c: &AtlasCell, m: &MaterialScheme, lit: bool, hot: bool) {
    let (x, y, w, h) = (c.x as f32, c.y as f32, c.w as f32, c.h as f32);
    let (fill, ink) = button_roles(m, lit, hot);
    // One shape: the small inner corners on its left half, the capsule's end on its right. Both are flat where they meet.
    let cx = x + w / 2.0;
    let cy0 = y + h / 2.0;
    for py in c.y..c.y + c.h {
        for px in c.x..c.x + c.w {
            let fx = px as f32 + 0.5;
            let fy = py as f32 + 0.5;
            let d = md_rounded_distance(
                fx,
                fy,
                cx,
                cy0,
                w / 2.0,
                h / 2.0,
                if fx < cx { IMMERSIVE_INNER_R * K } else { h / 2.0 },
            );
            let cover = cclamp(0.5 - d, 0.0, 1.0);
            if cover > 0.0 {
                md_over(im, px as i32, py as i32, fill, cover);
            }
        }
    }
    let tw = IMMERSIVE_TICK_W * K;
    let th = IMMERSIVE_TICK_H * K;
    let gap = IMMERSIVE_TICK_GAP * K;
    let cy = y + h / 2.0;
    let left = x + w / 2.0 - 1.0 * K - 1.5 * tw - gap; // a tick left of centre, for the round end's weight
    for i in 0..3 {
        let l = left + i as f32 * (tw + gap);
        md_fill_rounded(
            im,
            l,
            cy - th / 2.0,
            left + i as f32 * (tw + gap) + tw,
            cy + th / 2.0,
            IMMERSIVE_TICK_R * K,
            ink,
            1.0,
        );
    }
}

fn draw_cap(im: &mut HudImage, c: &AtlasCell, corner: u32, leg: f32, stroke: f32, colour: MdRgb) {
    let inner = CAP_INNER * K;
    let right = corner == 1 || corner == 3;
    let bottom = corner >= 2;
    for py in 0..c.h {
        for px in 0..c.w {
            // The cell from its corner, x and y pointing away from the desktop: the same L in every corner.
            let u = if right { px as f32 + 0.5 - inner } else { c.w as f32 - inner - px as f32 - 0.5 };
            let v = if bottom { py as f32 + 0.5 - inner } else { c.h as f32 - inner - py as f32 - 0.5 };
            let cov = clamp01(0.5 - md_corner_distance(u, v, CAP_GAP * K, leg * K, CAP_BEND * K, stroke * K));
            if cov > 0.0 {
                md_over(im, (c.x + px) as i32, (c.y + py) as i32, colour, cov);
            }
        }
    }
}

/// The handle atlas (glass.rs's cells and boxes): the level bar's seven states, the corner caps, the 3D button, the
/// Maximize button and hint, the band's Restore pill and the options button's mark. `font` sets the button's 3D, bold
/// (HUD_FONT_FAMILY); none: the built-in pixel font.
pub fn render_handle_atlas(accent: MdRgb, font: Font) -> HudImage {
    let m = glass_theme_scheme(accent);
    let mut out = blank(HANDLE_ATLAS_W, HANDLE_ATLAS_H);
    let im = &mut out;
    draw_bar(im, &bar_cell(bar_row(LevelSegment::None, false)), &m, LevelSegment::None, false);
    for segment in [LevelSegment::Ccw, LevelSegment::Level, LevelSegment::Cw] {
        for active in [false, true] {
            draw_bar(im, &bar_cell(bar_row(segment, active)), &m, segment, active);
        }
    }
    for corner in 0..CAP_CORNERS {
        draw_cap(im, &cap_cell(corner, 0), corner, CAP_LEG, CAP_STROKE, m.primary);
        draw_cap(im, &cap_cell(corner, 1), corner, CAP_LEG_HOT, CAP_STROKE_HOT, m.primary_bright);
        draw_cap(im, &cap_cell(corner, 2), corner, CAP_LEG_HOT, CAP_STROKE_HOT, m.primary_bright);
    }
    for lit in [false, true] {
        for hot in [false, true] {
            draw_button_main(im, &main_cell(lit, hot), &m, font, lit, hot);
            draw_button_tail(im, &tail_cell(lit, hot), &m, lit, hot);
        }
    }
    let rim = GLASS_RIM_PX * HANDLE_ATLAS_SCALE as f32;
    for state in [0, 2] {
        let x = (2 + state * 84) as f32;
        let y = ATLAS_OLD_H as f32 + 2.0;
        // Maximize: a round button in the pill family, lit glass under the dot (glass_lit_look, the pill's hover), with
        // four corner brackets that point outward.
        let hot = state >= 2;
        let ink = m.on_primary_container;
        let look = glass_lit_look(
            m.primary_container,
            m.primary_bright,
            if hot { GLASS_HOVER } else { GLASS_IDLE },
            78.0,
            rim,
        );
        glass_draw(im, &look, &FRect { x, y, width: 78.0, height: 78.0 }, 39.0);
        let (a, b, c, d, t, l) = (x + 22.0, x + 56.0, y + 22.0, y + 56.0, 5.0f32, 12.0f32); // the 34 px square, centred
        for cx in [a, b - l] {
            for cy in [c, d - t] {
                md_fill_rounded(im, cx, cy, cx + l, cy + t, t / 2.0, ink, 1.0);
            }
        }
        for cx in [a, b - t] {
            for cy in [c, d - l] {
                md_fill_rounded(im, cx, cy, cx + t, cy + l, t / 2.0, ink, 1.0);
            }
        }
    }
    {
        let x = 2.0;
        let y = ATLAS_OLD_H as f32 + 84.0;
        md_fill_rounded(im, x, y, x + 250.0, y + 60.0, 30.0, m.surface_container_high, 1.0);
        text(im, font, "Maximize", 40, true, x + 24.0, y + 30.0, m.on_surface, 1.0);
    }
    // The band's Restore pill: two windows and the word, in the bar's colours, lit glass under the dot.
    for state in 0..2 {
        let x = 2.0 + state as f32 * (ATLAS_PILL_W + 4.0);
        let y = ATLAS_PILL_Y;
        let cy = y + 39.0;
        let gx = x + 60.0;
        let look = glass_lit_look(
            m.primary_container,
            m.primary_bright,
            if state != 0 { GLASS_HOVER } else { GLASS_IDLE },
            78.0,
            rim,
        );
        glass_draw(im, &look, &FRect { x, y, width: ATLAS_PILL_W, height: 78.0 }, 39.0);
        let ink = m.on_primary_container;
        md_stroke_rounded(im, gx - 10.0, cy - 19.0, gx + 20.0, cy + 10.0, 2.0, 4.0, ink, 1.0);
        md_stroke_rounded(im, gx - 20.0, cy - 10.0, gx + 10.0, cy + 19.0, 2.0, 4.0, ink, 1.0);
        text(im, font, "Restore", if font.is_some() { 54 } else { 0 }, true, x + 108.0, cy, ink, 1.0);
    }
    // The options button's mark (28x14 at 3x): a sliders glyph, legible where a gear is not. Only the glyph: the renderer
    // draws the button itself as a glass pill, so it looks, hovers and opens like a placement pill.
    let gx = 360.0;
    let gy = ATLAS_OLD_H as f32 + 2.0;
    md_fill_rounded(im, gx + 20.0, gy + 13.0, gx + 64.0, gy + 17.0, 2.0, m.on_primary_container, 1.0);
    md_fill_rounded(im, gx + 20.0, gy + 25.0, gx + 64.0, gy + 29.0, 2.0, m.on_primary_container, 1.0);
    md_disc(im, gx + 50.0, gy + 15.0, 6.0, m.on_primary_container, 1.0);
    md_disc(im, gx + 34.0, gy + 27.0, 6.0, m.on_primary_container, 1.0);
    out
}

const _: () = assert!(ATLAS_CAP > 0 && ATLAS_GAP > 0, "the atlas has cells");

// --- The options menu --------------------------------------------------------------------------------------------------
// Options menu v2: drawing only. Every rect below is options_geometry.rs's: header 0..44 (focus targets scope 346..440,
// Next 440..530, Close 530..594), tabs 44..80 at x = 16 + 142 i (w 136), body 88..392 in 38 px rows (x 16..584), range
// track x 300..450, footer 400..440.

/// The categories, in tab order: (wire id, label).
const OPTIONS_TABS: [(&str, &str); 11] = [
    ("3d", "3D"),
    ("stabilization", "Stability"),
    ("layout", "Layout"),
    ("view", "View"),
    ("video", "Video"),
    ("input", "Typing"),
    ("keybinds", "Keys"),
    ("windows", "Windows"),
    ("privacy", "Privacy"),
    ("help", "Help"),
    ("session", "Session"),
];
const TABS_PER_PAGE: usize = OPTIONS_TABS_PER_PAGE as usize;

/// UTF-8 when the system font draws it, ASCII for the built-in pixel font (it has no glyphs past 0x7e).
fn opt_pick(font: Font, utf8: &'static str, ascii: &'static str) -> &'static str {
    if font.is_some() { utf8 } else { ascii }
}

/// `std::isspace` in the C locale.
fn c_space(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | 0x0b | 0x0c | b'\r')
}

/// `std::stod`: glibc strtod's longest numeric prefix after leading space (decimal, hex, inf, nan); `None` where stod
/// throws (nothing converted, or out of range).
fn stod(s: &str) -> Option<f64> {
    let b = s.as_bytes();
    let mut i = 0;
    while i < b.len() && c_space(b[i]) {
        i += 1;
    }
    let negative = b.get(i) == Some(&b'-');
    if matches!(b.get(i), Some(b'+' | b'-')) {
        i += 1;
    }
    let sign = if negative { -1.0 } else { 1.0 };
    let rest = &b[i..];
    let starts = |word: &[u8]| rest.len() >= word.len() && rest[..word.len()].eq_ignore_ascii_case(word);
    if starts(b"inf") {
        return Some(sign * f64::INFINITY);
    }
    if starts(b"nan") {
        return Some(if negative { -f64::NAN } else { f64::NAN });
    }
    if rest.len() >= 2 && rest[0] == b'0' && (rest[1] | 0x20) == b'x' {
        if let Some(v) = hex_float(&rest[2..]) {
            return v.map(|v| sign * v);
        }
    }
    // Decimal: digits, an optional point and digits (at least one digit in all), then an optional exponent.
    let start = i;
    let mut digits = 0;
    let mut nonzero = false;
    while i < b.len() && b[i].is_ascii_digit() {
        nonzero |= b[i] != b'0';
        i += 1;
        digits += 1;
    }
    if b.get(i) == Some(&b'.') {
        i += 1;
        while i < b.len() && b[i].is_ascii_digit() {
            nonzero |= b[i] != b'0';
            i += 1;
            digits += 1;
        }
    }
    if digits == 0 {
        return None;
    }
    let mantissa_end = i;
    let mut end = mantissa_end;
    if matches!(b.get(i), Some(b'e' | b'E')) {
        let mut j = i + 1;
        if matches!(b.get(j), Some(b'+' | b'-')) {
            j += 1;
        }
        if b.get(j).is_some_and(u8::is_ascii_digit) {
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            end = j;
        }
    }
    let v: f64 = s[start..end].parse().ok()?;
    if v.is_infinite() || (nonzero && (v == 0.0 || v.abs() < f64::MIN_POSITIVE)) {
        return None; // ERANGE
    }
    Some(sign * v)
}

/// The hex digits (and point and binary exponent) after "0x": `None` when there are none (strtod then reads the "0"),
/// `Some(None)` out of range.
fn hex_float(b: &[u8]) -> Option<Option<f64>> {
    let hex = |c: u8| (c as char).to_digit(16);
    let mut i = 0;
    let mut mantissa: u64 = 0;
    let mut exp: i64 = 0;
    let mut any = false;
    let mut sticky = false;
    let mut point = false;
    loop {
        match b.get(i) {
            Some(&b'.') if !point => point = true,
            Some(&c) if hex(c).is_some() => {
                any = true;
                let d = u64::from(hex(c).unwrap_or(0));
                if mantissa >> 60 == 0 {
                    mantissa = mantissa << 4 | d;
                    if point {
                        exp -= 4;
                    }
                } else {
                    sticky |= d != 0;
                    if !point {
                        exp += 4;
                    }
                }
            }
            _ => break,
        }
        i += 1;
    }
    if !any {
        return None;
    }
    if matches!(b.get(i), Some(b'p' | b'P')) {
        let mut j = i + 1;
        let negative = b.get(j) == Some(&b'-');
        if matches!(b.get(j), Some(b'+' | b'-')) {
            j += 1;
        }
        if b.get(j).is_some_and(u8::is_ascii_digit) {
            let mut e: i64 = 0;
            while let Some(c) = b.get(j).filter(|c| c.is_ascii_digit()) {
                e = (e * 10 + i64::from(c - b'0')).min(1 << 20);
                j += 1;
            }
            exp += if negative { -e } else { e };
        }
    }
    if mantissa == 0 {
        return Some(Some(0.0));
    }
    let m = (mantissa | u64::from(sticky)) as f64; // rounds once, to nearest even, the sticky bit below the 53
    let v = m * 2f64.powi(exp.clamp(-2000, 2000) as i32);
    if v.is_infinite() || v.abs() < f64::MIN_POSITIVE {
        return Some(None); // ERANGE
    }
    Some(Some(v))
}

/// `printf("%.*f")`, trailing zeros (and the point) dropped, "-0" read as "0".
fn opt_num(v: f64, decimals: usize) -> String {
    let mut s = if v.is_nan() {
        if v.is_sign_negative() { "-nan".to_string() } else { "nan".to_string() }
    } else {
        format!("{v:.decimals$}")
    };
    if s.contains('.') {
        while s.ends_with('0') {
            s.pop();
        }
        if s.ends_with('.') {
            s.pop();
        }
    }
    if s == "-0" { "0".into() } else { s }
}

/// The value as people read it. The wire value stays the backend's own number (the slider position is computed from it).
fn options_value_text(r: &OptionsRow, font: Font) -> String {
    let deg = opt_pick(font, "\u{b0}", " deg");
    let unit = if r.unit.is_empty() { String::new() } else { format!(" {}", r.unit) };
    if r.kind == "toggle" {
        return match r.value.as_str() {
            "true" => "on".into(),
            "false" => "off".into(),
            _ => r.value.clone(),
        };
    }
    if r.kind == "range" {
        let Some(v) = stod(&r.value) else {
            return format!("{}{unit}", r.value);
        };
        if r.unit == "0.1 deg" {
            let a = opt_num(v.abs() / 10.0, 1);
            if v.abs() < 0.5 {
                return if r.id == "placement_y" { "level" } else { "ahead" }.into();
            }
            if r.id == "placement_x" {
                return format!("{a}{deg}{}", if v > 0.0 { " right" } else { " left" });
            }
            if r.id == "placement_y" {
                return format!("{a}{deg}{}", if v > 0.0 { " up" } else { " down" });
            }
            return format!("{}{deg}", opt_num(v / 10.0, 1));
        }
        if r.unit == "deg" {
            return format!("{}{deg}", opt_num(v, 1));
        }
        if r.unit == "%" {
            return format!("{}%", opt_num(v, 1));
        }
        return format!("{}{unit}", opt_num(v, 2));
    }
    if r.kind == "choice" && r.id == "scale" {
        if let Some(v) = stod(&r.value) {
            return format!("{}%", opt_num(v * 100.0, 0));
        }
    }
    if r.kind == "choice" && !r.value.is_empty() && r.value.bytes().all(|c| c.is_ascii_lowercase() || c == b' ') {
        let mut v = r.value.clone().into_bytes();
        v[0] = v[0].to_ascii_uppercase();
        return String::from_utf8(v).unwrap_or_default();
    }
    if r.kind == "choice" {
        // 1920x1080 -> 1920 x 1080
        if let Some(x) = r.value.find('x') {
            let (w, h) = (&r.value[..x], &r.value[x + 1..]);
            if x > 0
                && x + 1 < r.value.len()
                && w.bytes().all(|c| c.is_ascii_digit())
                && h.bytes().all(|c| c.is_ascii_digit())
            {
                return format!("{w}{}{h}", opt_pick(font, " \u{d7} ", " x "));
            }
        }
    }
    if r.value.is_empty() { String::new() } else { format!("{}{unit}", r.value) }
}

/// Rows that remove or stop something: drawn in the error role so they never read as an ordinary action.
fn options_destructive(id: &str) -> bool {
    matches!(id, "remove_monitor" | "stop" | "layout_reset" | "menu_reset")
}

/// `s` cut with an ellipsis so it fits `room` px.
fn opt_fit(font: Font, s: &str, px: u32, bold: bool, room: f32) -> String {
    if text_width(font, s, px, bold) as f32 <= room {
        return s.into();
    }
    let dots = opt_pick(font, "\u{2026}", "..");
    let mut s = s.to_string();
    while !s.is_empty() && text_width(font, &format!("{s}{dots}"), px, bold) as f32 > room {
        s.pop(); // a whole UTF-8 sequence
    }
    s + dots
}

/// A right-pointing chevron drawn as a stroke, the same with or without the system font.
fn opt_chevron(im: &mut HudImage, cx: f32, cy: f32, h: f32, c: MdRgb, alpha: f32) {
    let mut t = 0.0f32;
    while t <= 1.0001 {
        md_disc(im, cx - h / 4.0 + t * h / 2.0, cy - h / 2.0 + t * h / 2.0, 1.1, c, alpha);
        md_disc(im, cx - h / 4.0 + t * h / 2.0, cy + h / 2.0 - t * h / 2.0, 1.1, c, alpha);
        t += 0.04;
    }
}

/// A small key cap: outline-variant border on the surface, label in on-surface-variant. Returns its right edge.
fn opt_key(im: &mut HudImage, font: Font, m: &MaterialScheme, label: &str, left: f32, cy: f32) -> f32 {
    let w = text_width(font, label, 11, true) as f32 + 10.0;
    md_fill_rounded(im, left, cy - 9.0, left + w, cy + 9.0, 4.0, m.primary, 0.14);
    md_stroke_rounded(im, left, cy - 9.0, left + w, cy + 9.0, 4.0, 1.0, m.outline_variant, 1.0);
    text(im, font, label, 11, true, left + 5.0, cy, m.on_surface_variant, 1.0);
    left + w
}

fn blank(width: u32, height: u32) -> HudImage {
    HudImage { width, height, rgba: vec![0; width as usize * height as usize * 4] }
}

/// The options menu, 600 x 440: header (title, scope switch, page dots, Close), the category tabs, the scrolled rows
/// and the footer (status chip, message, keys). The C++'s default accent is `[0.45, 0.65, 0.8]`.
pub fn render_options_panel(state: &OptionsMenu, font: Font, accent: MdRgb) -> HudImage {
    let m = glass_theme_scheme(accent);
    let mut out = blank(600, 440);
    let im = &mut out;
    md_fill_rounded(im, 0.0, 0.0, 600.0, 440.0, 18.0, m.surface_container_high, 1.0);
    md_stroke_rounded(im, 0.0, 0.0, 600.0, 440.0, 18.0, 1.0, m.outline_variant, 1.0);
    let monitor_scope = state.scope == "monitor";

    // Header: the title, then the scope switch, the page dots and Close at their contract focus rects.
    {
        let title = if monitor_scope { state.monitor.as_str() } else { "All monitors" };
        text(im, font, title, 18, true, 16.0, 22.0, m.on_surface, 1.0);
        let left = 16.0 + text_width(font, title, 18, true) as f32 + 8.0;
        text(
            im,
            font,
            if monitor_scope { "this monitor" } else { "workspace" },
            12,
            false,
            left,
            23.0,
            m.on_surface_variant,
            1.0,
        );
    }
    {
        // Scope: a two-way switch, the current scope filled with the primary colour.
        md_fill_rounded(im, OPTIONS_SCOPE_X0, 8.0, OPTIONS_NEXT_X0, 36.0, 14.0, m.surface_container, 1.0);
        if state.focus == -4 {
            md_stroke_rounded(im, 344.0, 6.0, 442.0, 38.0, 16.0, 2.0, m.primary, 1.0);
        }
        let (x0, x1) = if monitor_scope { (348.0, 393.0) } else { (393.0, 438.0) };
        md_fill_rounded(im, x0, 10.0, x1, 34.0, 12.0, m.primary, 1.0);
        let left = opt_fit(font, if state.monitor.is_empty() { "One" } else { &state.monitor }, 11, true, 41.0);
        let right = "All";
        let (on, off) = (m.on_primary, m.on_surface_variant);
        let lx = 348.0 + (45.0 - text_width(font, &left, 11, monitor_scope) as f32) / 2.0;
        text(im, font, &left, 11, monitor_scope, lx, 22.0, if monitor_scope { on } else { off }, 1.0);
        let rx = 393.0 + (45.0 - text_width(font, right, 11, !monitor_scope) as f32) / 2.0;
        text(im, font, right, 11, !monitor_scope, rx, 22.0, if monitor_scope { off } else { on }, 1.0);
    }
    let current = OPTIONS_TABS.iter().position(|t| state.category == t.0).unwrap_or(0);
    let page = current / TABS_PER_PAGE;
    let pages = OPTIONS_TABS.len().div_ceil(TABS_PER_PAGE);
    {
        // Next: which page of categories is up, as dots (the current one long), then a chevron.
        if state.focus == -2 {
            md_fill_rounded(im, OPTIONS_NEXT_X0, 4.0, OPTIONS_CLOSE_X0, 40.0, 8.0, m.secondary_container, 1.0);
        }
        let mut x = 456.0f32;
        for p in 0..pages {
            let w = if p == page { 12.0 } else { 4.0 };
            md_fill_rounded(im, x, 20.0, x + w, 24.0, 2.0, if p == page { m.primary } else { m.outline_variant }, 1.0);
            x += w + 4.0;
        }
        opt_chevron(im, x + 10.0, 22.0, 10.0, m.on_surface_variant, 1.0);
    }
    {
        // Close: a cross and its key.
        if state.focus == -1 {
            md_fill_rounded(im, OPTIONS_CLOSE_X0, 4.0, OPTIONS_CLOSE_X1, 40.0, 8.0, m.secondary_container, 1.0);
        }
        text(im, font, opt_pick(font, "\u{d7}", "x"), 18, true, 538.0, 22.0, m.on_surface_variant, 1.0);
        opt_key(im, font, &m, "Esc", 556.0, 22.0);
    }

    // Tabs: four to a page, the current one a filled pill, its label bold.
    for (i, tab) in OPTIONS_TABS
        .iter()
        .enumerate()
        .take(((page + 1) * TABS_PER_PAGE).min(OPTIONS_TABS.len()))
        .skip(page * TABS_PER_PAGE)
    {
        let x = OPTIONS_TABS_X + (i - page * TABS_PER_PAGE) as f32 * OPTIONS_TAB_PITCH;
        let on = i == current;
        if on {
            md_fill_rounded(im, x, 44.0, x + OPTIONS_TAB_W, 80.0, 10.0, m.secondary_container, 1.0);
            if state.focus == -3 {
                md_stroke_rounded(im, x, 44.0, x + OPTIONS_TAB_W, 80.0, 10.0, 2.0, m.primary, 1.0);
            }
        }
        let c = if on { m.on_secondary_container } else { m.on_surface_variant };
        text(im, font, tab.1, 13, on, x + 12.0, 62.0, c, 1.0);
    }
    for x in 16..584 {
        md_over(im, x, 84, m.outline_variant, 1.0);
    }

    // Body: clipped to 88..392, scrolled by state.scroll exactly as options_row_at hit-tests it.
    let mut body = blank(600, 304);
    let body_im = &mut body;
    md_fill_rounded(body_im, 0.0, 0.0, 600.0, 304.0, 0.0, m.surface_container_high, 1.0);
    for (i, r) in state.rows.iter().enumerate() {
        let y = i as f32 * 38.0 - state.scroll as f32;
        if y + 38.0 <= 0.0 || y >= 304.0 {
            continue;
        }
        options_row(body_im, font, &m, r, y, i as i32 == state.focus);
    }
    let content = state.rows.len() as f32 * 38.0;
    if content > 304.0 {
        // a thin scroll thumb on the right edge
        let h = cmax(24.0, 304.0 * 304.0 / content);
        let top = (304.0 - h) * cclamp(state.scroll as f32 / (content - 304.0), 0.0, 1.0);
        md_fill_rounded(body_im, 591.0, top + 2.0, 595.0, top + h - 2.0, 2.0, m.outline, 1.0);
    }
    im.rgba[88 * 2400..392 * 2400].copy_from_slice(&body.rgba);

    // Footer: a status chip (pending / applied / error), the message, and the keys when there is room.
    for x in 16..584 {
        md_over(im, x, 396, m.outline_variant, 1.0);
    }
    let mut x = 16.0f32;
    let chip = match state.status.as_str() {
        "applied" => Some("Applied"),
        "pending" => Some("Pending"),
        "error" => Some("Error"),
        _ => None,
    };
    let error = state.status == "error";
    if let Some(chip) = chip {
        let applied = state.status == "applied";
        let bg = if error {
            m.error_container
        } else if applied {
            m.primary_container
        } else {
            m.secondary_container
        };
        let fg = if error {
            m.on_surface
        } else if applied {
            m.on_primary_container
        } else {
            m.on_secondary_container
        };
        let w = text_width(font, chip, 12, true) as f32 + 16.0;
        md_fill_rounded(im, x, 409.0, x + w, 431.0, 11.0, bg, 1.0);
        text(im, font, chip, 12, true, x + 8.0, 420.0, fg, 1.0);
        x += w + 8.0;
    }
    let hint = opt_pick(font, "\u{2191}\u{2193} row  \u{2190}\u{2192} adjust", "Up/Dn row  L/R adjust");
    let hint_w = text_width(font, hint, 12, false) as f32;
    let message = if state.message.is_empty() && chip.is_none() { "Ready" } else { state.message.as_str() };
    let message_w = text_width(font, message, 12, false) as f32;
    let show_hint = x + message_w + 16.0 + hint_w <= 584.0;
    let room = (if show_hint { 584.0 - hint_w - 16.0 } else { 584.0 }) - x;
    let fitted = opt_fit(font, message, 12, false, room);
    text(im, font, &fitted, 12, false, x, 420.0, if error { m.on_surface } else { m.on_surface_variant }, 1.0);
    if show_hint {
        text(im, font, hint, 12, false, 584.0 - hint_w, 420.0, m.on_surface_variant, 1.0);
    }
    out
}

/// One options row at `y` in the body.
fn options_row(body: &mut HudImage, font: Font, m: &MaterialScheme, r: &OptionsRow, y: f32, focused: bool) {
    let alpha = if r.enabled { 1.0 } else { MD_DISABLED_CONTENT };
    let cy = y + 19.0;
    if focused {
        // lit glass
        let look = lit_band(m.secondary_container, m.on_secondary_container, 36.0);
        glass_draw(body, &look, &FRect { x: 16.0, y: y + 1.0, width: 568.0, height: 36.0 }, 10.0);
    }
    let danger = options_destructive(&r.id);
    let readonly = r.kind == "readonly";
    let label_c = if danger {
        m.on_error_container
    } else if readonly {
        m.on_surface_variant
    } else if focused {
        m.on_secondary_container
    } else {
        m.on_surface
    };
    let value = options_value_text(r, font);
    let mut value_right = 576.0f32;
    if r.kind == "range" && r.maximum > r.minimum {
        let v = stod(&r.value).unwrap_or(r.minimum); // presentation text; never executed or changed
        let px = |x: f64| {
            OPTIONS_TRACK_X0
                + (OPTIONS_TRACK_X1 - OPTIONS_TRACK_X0)
                    * dclamp((x - r.minimum) / (r.maximum - r.minimum), 0.0, 1.0) as f32
        };
        let knob = px(v);
        let origin = px(dclamp(0.0, r.minimum, r.maximum)); // active track grows from zero
        md_fill_rounded(body, OPTIONS_TRACK_X0, cy - 2.0, OPTIONS_TRACK_X1, cy + 2.0, 2.0, m.primary, 0.24 * alpha);
        if (knob - origin).abs() >= 1.0 {
            md_fill_rounded(body, cmin(knob, origin), cy - 2.0, cmax(knob, origin), cy + 2.0, 2.0, m.primary, alpha);
        }
        if focused {
            md_disc(body, knob, cy, 13.0, m.primary, 0.3 * alpha);
        }
        let (radius, c) = if focused { (8.0, m.primary_bright) } else { (7.0, m.primary) };
        md_disc(body, knob, cy, radius, c, alpha);
    } else if r.kind == "toggle" {
        let on = r.value == "true";
        md_fill_rounded(
            body,
            302.0,
            y + 9.0,
            338.0,
            y + 29.0,
            10.0,
            if on { m.primary } else { m.surface_container_highest },
            alpha,
        );
        if !on {
            md_stroke_rounded(body, 302.0, y + 9.0, 338.0, y + 29.0, 10.0, 2.0, m.outline, alpha);
        }
        let (hx, hr, hc) = if on { (328.0, 7.0, m.on_primary) } else { (312.0, 5.0, m.outline) };
        md_disc(body, hx, cy, hr, hc, alpha);
    } else if r.kind == "choice" {
        md_icon(body, MdIcon::ArrowDropDown, 572.0, cy, 16.0, m.primary, alpha);
        value_right = 560.0;
    } else if r.kind == "action" && value.is_empty() {
        opt_chevron(body, 570.0, cy, 10.0, if danger { m.on_error_container } else { m.on_surface_variant }, alpha);
    }
    let value_left = if r.kind == "range" {
        462.0
    } else if r.kind == "toggle" {
        350.0
    } else {
        310.0
    };
    let label_room = (if r.kind == "range" || r.kind == "toggle" {
        296.0
    } else if value.is_empty() {
        540.0
    } else {
        300.0
    }) - 26.0;
    // The same weight focused or not: the band shows the focus, the text does not jump.
    text(body, font, &opt_fit(font, &r.label, 14, false, label_room), 14, false, 26.0, cy, label_c, alpha);
    if !value.is_empty() {
        let shown = opt_fit(font, &value, 14, false, value_right - value_left);
        let c = if readonly {
            m.on_surface
        } else if focused {
            m.on_secondary_container
        } else {
            m.on_surface_variant
        };
        text(body, font, &shown, 14, false, value_right - text_width(font, &shown, 14, false) as f32, cy, c, alpha);
    }
}

// --- The placement panel -----------------------------------------------------------------------------------------------

/// The expanded panel's content, PANEL_W x PANEL_H: the desktop and title, the four placement sliders (the selected one
/// on a lit band), the Resolution, Scale and Background dropdowns, the background switch and Brightness slider, the
/// desktop row's buttons, the key hint, and an open list over them. The GPU draws the glass under it and the grabber.
pub fn render_glass_panel(state: &PlacementMenuState, font: Font, accent: MdRgb) -> HudImage {
    let m = glass_theme_scheme(accent);
    let mut out = blank(PANEL_W as u32, PANEL_H as u32);
    let im = &mut out;
    let w = PANEL_W;
    let desktop = (state.desktop as usize).min(PLACEMENT_MENU_DESKTOP_COUNT - 1);
    // Header: the desktop on the left (title: on-surface, bold), what this is on the right (on-surface-variant); the drag
    // handle (drawn by the GPU) sits between them, an M3 divider (outline variant) under them.
    let header_cy = (GRABBER_TOP + GRABBER_H + PANEL_HEADER_H) / 2.0;
    let name = format!("XR-{}", state.numbers[desktop]);
    text(im, font, &name, PANEL_TEXT_PX, true, PANEL_PAD, header_cy, m.on_surface, 1.0);
    let title = "Placement";
    let tx = w - PANEL_PAD - text_width(font, title, PANEL_TEXT_PX, false) as f32;
    text(im, font, title, PANEL_TEXT_PX, false, tx, header_cy, m.on_surface_variant, 1.0);
    for x in PANEL_PAD as i32..(w - PANEL_PAD) as i32 {
        md_over(im, x, PANEL_HEADER_H as i32 - 1, m.outline_variant, 1.0);
    }
    // Rows: an M3 list of sliders. The selected row is the active indicator (secondary container, fully rounded) with its
    // text in on-secondary-container; the others' labels on-surface, their values on-surface-variant.
    let rows = placement_menu_rows(state, font.is_none());
    let bounds = placement_menu_bounds();
    let flat = placement_menu_flat();
    let values = &state.placements[desktop];
    for i in 0..PLACEMENT_MENU_FIELDS {
        let slider = Slider { lo: bounds[i].0, hi: bounds[i].1, flat: flat[i], value: values[i] };
        let top = PANEL_ROWS_TOP + i as f32 * PANEL_ROW_H;
        slider_row(im, font, &m, top, LABELS[i], &slider, &rows[i].value, rows[i].selected, 1.0);
    }
    // Everything clickable under the sliders is in M3 label large (bold) at BUTTON_LABEL_PX: the Resolution and Scale
    // boxes, their lists and the desktop row's buttons.
    let label_px = if font.is_some() { BUTTON_LABEL_PX } else { 0 };
    // The Resolution row, then the Scale row: M3 exposed dropdown menus (dropdown_row), each M3 disabled while its value
    // is unknown or a desktop change is busy.
    dropdown_row(
        im,
        font,
        &m,
        "Resolution",
        PANEL_RESOLUTION_TOP,
        &glass_resolution_box(),
        &glass_resolution_label(state.resolution_w, state.resolution_h),
        label_px,
        state.resolution_picker,
        state.resolution_w != 0 && state.resolution_h != 0 && !state.busy,
    );
    dropdown_row(
        im,
        font,
        &m,
        "Scale",
        PANEL_SCALE_TOP,
        &glass_scale_box(),
        &glass_scale_label(state.scale),
        label_px,
        state.picker,
        state.scale != 0 && !state.busy,
    );
    // The background: its dropdown, an M3 switch beside it (on: the primary track and an on-primary handle at its end;
    // off: an outlined track and a smaller outline handle), then the Brightness slider, M3 disabled while there is no
    // background.
    let background = usize::from(state.background).min(BACKGROUND_COUNT - 1);
    dropdown_row(
        im,
        font,
        &m,
        "Background",
        PANEL_BACKGROUND_TOP,
        &glass_background_box(),
        BACKGROUNDS[background].label,
        label_px,
        state.background_picker,
        true,
    );
    {
        let sw = glass_background_switch();
        let on = background != 0;
        let x1 = sw.x + sw.width;
        let y1 = sw.y + sw.height;
        let cy = sw.y + sw.height / 2.0;
        let end = sw.height / 2.0;
        md_fill_rounded(im, sw.x, sw.y, x1, y1, end, if on { m.primary } else { m.surface_container_highest }, 1.0);
        if !on {
            md_stroke_rounded(im, sw.x, sw.y, x1, y1, end, 2.0, m.outline, 1.0);
        }
        let (hx, hr, hc) =
            if on { (x1 - end, SWITCH_HANDLE_ON, m.on_primary) } else { (sw.x + end, SWITCH_HANDLE_OFF, m.outline) };
        md_disc(im, hx, cy, hr, hc, 1.0);
    }
    let brightness = Slider {
        lo: i32::from(BRIGHTNESS_MIN),
        hi: i32::from(BRIGHTNESS_MAX),
        flat: i32::from(BRIGHTNESS_DEFAULT),
        value: i32::from(state.brightness),
    };
    slider_row(
        im,
        font,
        &m,
        PANEL_BRIGHTNESS_TOP,
        "Brightness",
        &brightness,
        &format!("{}%", state.brightness),
        state.field as usize == PLACEMENT_MENU_BRIGHTNESS,
        if background != 0 { 1.0 } else { MD_DISABLED_CONTENT },
    );
    // Desktop row: fully rounded M3 buttons (glass_panel_button_style), each an icon and its label (M3 label large) when
    // every button has room for both.
    let mut icons = true;
    for a in 0..PANEL_ACTIONS {
        let lw = text_width(font, glass_panel_action_label(a, state.armed), label_px, true) as f32;
        icons = icons && BUTTON_ICON_PX + BUTTON_ICON_GAP + lw + 2.0 * BUTTON_PAD_MIN <= PANEL_ACTION_W;
    }
    for (a, icon) in ACTION_ICONS.iter().enumerate() {
        let r = glass_panel_action(a);
        let st = glass_panel_button_style(a, state, accent);
        md_fill_rounded(im, r.x, r.y, r.x + r.width, r.y + r.height, r.height / 2.0, st.container, st.container_alpha);
        let label = glass_panel_action_label(a, state.armed);
        let lw = text_width(font, label, label_px, true) as f32;
        let content = if icons { BUTTON_ICON_PX + BUTTON_ICON_GAP + lw } else { lw };
        let left = r.x + cmax(0.0, (r.width - content) / 2.0);
        let cy = r.y + r.height / 2.0;
        if icons {
            md_icon(im, *icon, left + BUTTON_ICON_PX / 2.0, cy, BUTTON_ICON_PX, st.content, st.content_alpha);
        }
        let lx = if icons { left + BUTTON_ICON_PX + BUTTON_ICON_GAP } else { left };
        text(im, font, label, label_px, true, lx, cy, st.content, st.content_alpha);
    }
    // Footer: the keys that work while a panel is open (Esc and the pill itself close it), in the outline colour.
    let hint = glass_panel_hint(font.is_none());
    let hint_px = if font.is_some() { PANEL_HINT_PX } else { 0 };
    let hint_w = text_width(font, hint, hint_px, false) as f32;
    let hx = cmax(PANEL_PAD, (w - hint_w) / 2.0);
    text(im, font, hint, hint_px, false, hx, PANEL_FOOTER_TOP + PANEL_HINT_PX as f32 / 2.0, m.outline, 1.0);
    // An open list, last: an M3 menu over the rows above its box (surface container, extra small corners, an
    // outline-variant edge; opaque, so what is under it does not show), one item per choice in label large: the current
    // one on the secondary container, ones that cannot be picked M3 disabled (on-surface at 38%).
    if state.picker {
        list_frame(im, &m, &glass_scale_item(0), &glass_scale_item(SCALE_STEPS - 1));
        for (i, &percent) in SCALE_PERCENTS.iter().enumerate() {
            let item = glass_scale_item(i);
            list_item(
                im,
                font,
                &m,
                &item,
                &glass_scale_label(percent),
                label_px,
                percent == state.scale,
                glass_scale_enabled(i, state),
            );
        }
    }
    if state.background_picker {
        list_frame(im, &m, &glass_background_item(0), &glass_background_item(BACKGROUND_COUNT - 1));
        for (i, b) in BACKGROUNDS.iter().enumerate() {
            list_item(im, font, &m, &glass_background_item(i), b.label, label_px, i == background, true);
        }
    }
    // The open Resolution list, likewise an M3 menu over the slider rows (and their selected band, BAND_INSET in): a
    // column per shape, its name on top (on-surface-variant, regular) over an outline-variant divider, then its sizes in
    // label large; the current size on the secondary container, every size M3 disabled while busy.
    if state.resolution_picker {
        let list = glass_resolution_list();
        let x0 = list.x;
        let y0 = list.y - 1.0;
        let x1 = list.x + list.width;
        let y1 = list.y + list.height + 1.0;
        md_fill_rounded(im, x0, y0, x1, y1, MD_SHAPE_EXTRA_SMALL, m.surface_container, 1.0);
        md_stroke_rounded(im, x0, y0, x1, y1, MD_SHAPE_EXTRA_SMALL, 1.0, m.outline_variant, 1.0);
        for (g, group) in RESOLUTION_GROUPS.iter().enumerate().take(RESOLUTION_GROUP_COUNT) {
            let h = glass_resolution_heading(g);
            text(
                im,
                font,
                group.name,
                label_px,
                false,
                h.x + FIELD_PAD_X,
                h.y + h.height / 2.0,
                m.on_surface_variant,
                1.0,
            );
        }
        for x in (x0 + FIELD_PAD_X / 2.0) as i32..(x1 - FIELD_PAD_X / 2.0) as i32 {
            md_over(im, x, (list.y + RES_ITEM_H) as i32 - 1, m.outline_variant, 1.0);
        }
        for (i, res) in RESOLUTIONS.iter().enumerate().take(RESOLUTION_COUNT) {
            let r = glass_resolution_item(i);
            let current = res.width == state.resolution_w && res.height == state.resolution_h;
            let enabled = glass_resolution_enabled(i, state);
            if current {
                md_fill_rounded(
                    im,
                    r.x + 2.0,
                    r.y + 1.0,
                    r.x + r.width - 2.0,
                    r.y + r.height - 1.0,
                    MD_SHAPE_EXTRA_SMALL,
                    m.secondary_container,
                    1.0,
                );
            }
            let c = if current { m.on_secondary_container } else { m.on_surface };
            let label = glass_resolution_label(res.width, res.height);
            let a = if enabled || current { 1.0 } else { MD_DISABLED_CONTENT };
            text(im, font, &label, label_px, true, r.x + FIELD_PAD_X, r.y + r.height / 2.0, c, a);
        }
    }
    out
}

/// One slider's range: its ends, the flat value its active track fills from, and its value.
struct Slider {
    lo: i32,
    hi: i32,
    flat: i32,
    value: i32,
}

/// One slider row: `alpha` < 1 is M3 disabled.
#[allow(clippy::too_many_arguments)]
fn slider_row(
    im: &mut HudImage,
    font: Font,
    m: &MaterialScheme,
    top: f32,
    label: &str,
    s: &Slider,
    shown: &str,
    selected: bool,
    alpha: f32,
) {
    let w = PANEL_W;
    let cy = top + PANEL_ROW_H / 2.0;
    if selected {
        // lit glass, fully rounded
        let look = lit_band(m.secondary_container, m.on_secondary_container, PANEL_ROW_H - 4.0);
        let band = FRect { x: BAND_INSET, y: top + 2.0, width: w - 2.0 * BAND_INSET, height: PANEL_ROW_H - 4.0 };
        glass_draw(im, &look, &band, (PANEL_ROW_H - 4.0) / 2.0);
    }
    let ink = if selected { m.on_secondary_container } else { m.on_surface };
    text(im, font, label, PANEL_TEXT_PX, selected, PANEL_PAD, cy, ink, alpha);
    // The slider: inactive track (primary at 24%), active track from the flat value to the value, handle (primary).
    let at = |v: i32| {
        PANEL_KNOB_LO + (PANEL_KNOB_HI - PANEL_KNOB_LO) * (v.clamp(s.lo, s.hi) - s.lo) as f32 / (s.hi - s.lo) as f32
    };
    md_fill_rounded(
        im,
        PANEL_TRACK_X0,
        cy - TRACK_H / 2.0,
        PANEL_TRACK_X1,
        cy + TRACK_H / 2.0,
        TRACK_H / 2.0,
        m.primary,
        TRACK_ALPHA * alpha,
    );
    let a = at(s.flat);
    let b = at(s.value);
    if (b - a).abs() >= 1.0 {
        md_fill_rounded(
            im,
            cmin(a, b),
            cy - TRACK_H / 2.0,
            cmax(a, b),
            cy + TRACK_H / 2.0,
            TRACK_H / 2.0,
            m.primary,
            alpha,
        );
    }
    if selected {
        md_disc(im, b, cy, HALO_R, m.primary, MD_STATE_FOCUS * alpha); // the focused handle's state layer
    }
    md_disc(im, b, cy, PANEL_KNOB_R, m.primary, alpha);
    let vc = if selected { m.on_secondary_container } else { m.on_surface_variant };
    let vx = w - PANEL_PAD - text_width(font, shown, PANEL_TEXT_PX, selected) as f32;
    text(im, font, shown, PANEL_TEXT_PX, selected, vx, cy, vc, alpha);
}

fn list_frame(im: &mut HudImage, m: &MaterialScheme, first: &FRect, last: &FRect) {
    let x0 = first.x;
    let y0 = first.y - 1.0;
    let x1 = first.x + first.width;
    let y1 = last.y + last.height + 1.0;
    md_fill_rounded(im, x0, y0, x1, y1, MD_SHAPE_EXTRA_SMALL, m.surface_container, 1.0);
    md_stroke_rounded(im, x0, y0, x1, y1, MD_SHAPE_EXTRA_SMALL, 1.0, m.outline_variant, 1.0);
}

#[allow(clippy::too_many_arguments)]
fn list_item(
    im: &mut HudImage,
    font: Font,
    m: &MaterialScheme,
    r: &FRect,
    label: &str,
    label_px: u32,
    current: bool,
    enabled: bool,
) {
    if current {
        md_fill_rounded(
            im,
            r.x + 2.0,
            r.y + 1.0,
            r.x + r.width - 2.0,
            r.y + r.height - 1.0,
            MD_SHAPE_EXTRA_SMALL,
            m.secondary_container,
            1.0,
        );
    }
    let c = if current { m.on_secondary_container } else { m.on_surface };
    let a = if enabled || current { 1.0 } else { MD_DISABLED_CONTENT };
    text(im, font, label, label_px, true, r.x + FIELD_PAD_X, r.y + r.height / 2.0, c, a);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stod_reads_like_glibc() {
        assert_eq!(stod("  2.5e0xyz"), Some(2.5));
        assert_eq!(stod("abc"), None);
        assert_eq!(stod("1e400"), None);
        assert_eq!(stod("1e-400"), None);
        assert_eq!(stod("0e-400"), Some(0.0));
        assert_eq!(stod("-Infinity"), Some(f64::NEG_INFINITY));
        assert_eq!(stod("0x1.8p1"), Some(3.0));
        assert_eq!(stod("0x"), Some(0.0));
        assert_eq!(stod("5e"), Some(5.0));
        assert_eq!(stod(".5"), Some(0.5));
        assert_eq!(stod("."), None);
        assert!(stod("nan").is_some_and(f64::is_nan));
    }

    #[test]
    fn opt_num_is_printf() {
        assert_eq!(opt_num(0.125, 2), "0.12");
        assert_eq!(opt_num(-0.001, 2), "0");
        assert_eq!(opt_num(45.0, 1), "45");
        assert_eq!(opt_num(150.0, 0), "150");
        assert_eq!(opt_num(f64::NEG_INFINITY, 1), "-inf");
    }
}
