//! Head-locked HUD (`spike/include/xr/hud.hpp`, `spike/src/hud.cpp`): the status pop-ups' text and timing, the keyboard
//! help card's fit, pages and click targets, the eased card fade, the placement panel's state and value formatting, the
//! built-in 5x7 pixel font's glyphs and metrics, the backgrounds list, and the CPU rasterisers that draw the pop-up, help
//! and VR video cards ([`render_hud_card`], [`render_help_card`], [`render_video_card`]).
//!
//! The pop-ups show the viewing mode (anchor / follow / locked), cursor follow, prediction and typing mode. Text uses an
//! installed system font when one is given (a [`HudTextMetrics`], in practice [`crate::hud_font::HudFont`]); without one,
//! the built-in 5x7 pixel font. The overlays keep state only: the renderer draws their lines or rows with the
//! rasterisers.
//!
//! All maths is `f32` / `f64` in the C++ order, so results match it bit for bit, and the cards match it byte for byte
//! (`tests/card_raster.rs`).

use std::cell::Cell;

use crate::desktops::MAX_DESKTOPS;
use crate::glass_theme::glass_theme_scheme;
use crate::material::{MD_DEFAULT_SEED, MD_SHAPE_EXTRA_LARGE, MaterialScheme, clampf};
use crate::material_draw::{cmax, cmin};
use crate::pose::{project, transpose};

// --- Text lines ----------------------------------------------------------------------------------------------------

/// A line's colour can be a role of the card's Material scheme (from the theme accent the card is drawn with) instead of
/// its own `color`: the cards follow the Omarchy theme and stay under the OLED cap.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum HudRole {
    /// The line's own `color`.
    #[default]
    None,
    OnSurface,
    OnSurfaceVariant,
    Outline,
    Primary,
    OnSecondaryContainer,
}

/// One line of a card. `scale` is a relative size step (1 small, 2 normal, 4 large); `bold` uses the font's bold face.
/// `px`, when set, is the exact em size in pixels instead (the pixel font takes the nearest whole zoom, at least 1).
#[derive(Clone, Debug, PartialEq)]
pub struct HudLine {
    pub text: String,
    pub scale: u32,
    pub color: [f32; 3],
    pub bold: bool,
    pub px: u32,
    pub role: HudRole,
}

impl Default for HudLine {
    fn default() -> Self {
        Self { text: String::new(), scale: 1, color: [1.0, 1.0, 1.0], bold: false, px: 0, role: HudRole::None }
    }
}

pub(crate) fn hud_line(text: impl Into<String>, scale: u32, color: [f32; 3], bold: bool, px: u32, role: HudRole) -> HudLine {
    HudLine { text: text.into(), scale, color, bold, px, role }
}

/// The colour a line is drawn in with `scheme`.
pub fn hud_line_color(line: &HudLine, scheme: &MaterialScheme) -> [f32; 3] {
    match line.role {
        HudRole::OnSurface => scheme.on_surface,
        HudRole::OnSurfaceVariant => scheme.on_surface_variant,
        HudRole::Outline => scheme.outline,
        HudRole::Primary => scheme.primary,
        HudRole::OnSecondaryContainer => scheme.on_secondary_container,
        HudRole::None => line.color,
    }
}

/// An installed font's line metrics, in pixels.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FontMetrics {
    pub ascent: u32,
    pub descent: u32,
}

/// What a card needs from an installed scalable font ([`crate::hud_font::HudFont`], the C++ `HudFont`: fontconfig +
/// FreeType): the metrics and widths it is laid out with, and the text it is drawn with. Without one, the built-in pixel
/// font.
pub trait HudTextMetrics {
    fn metrics(&self, px: u32, bold: bool) -> FontMetrics;
    /// Advance width of `utf8` in pixels.
    fn measure(&self, utf8: &str, px: u32, bold: bool) -> u32;
    /// Rasterises `utf8` with its baseline at `baseline`, starting at `left`: `plot(x, y, coverage)` once per covered
    /// pixel, coverage in (0, 1].
    fn draw(&self, utf8: &str, px: u32, bold: bool, left: i32, baseline: i32, plot: &mut dyn FnMut(i32, i32, f32));
}

/// Omarchy's monospace font.
pub const HUD_FONT_FAMILY: &str = "JetBrainsMono Nerd Font";

// --- The built-in pixel font -----------------------------------------------------------------------------------------

pub const GLYPH_W: u32 = 5;
pub const GLYPH_H: u32 = 7;

/// One glyph: a row per line, bit 4 the leftmost column.
pub type Glyph = [u8; GLYPH_H as usize];

const LETTERS: [Glyph; 26] = [
    [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11], // A
    [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E], // B
    [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E], // C
    [0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C], // D
    [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F], // E
    [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10], // F
    [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F], // G
    [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11], // H
    [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E], // I
    [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C], // J
    [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11], // K
    [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F], // L
    [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11], // M
    [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11], // N
    [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E], // O
    [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10], // P
    [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D], // Q
    [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11], // R
    [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E], // S
    [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04], // T
    [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E], // U
    [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04], // V
    [0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A], // W
    [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11], // X
    [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04], // Y
    [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F], // Z
];
const DIGITS: [Glyph; 10] = [
    [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
    [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
    [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
    [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
];
static SPACE: Glyph = [0; 7];
static COLON: Glyph = [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00];
static DASH: Glyph = [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00];
static PLUS: Glyph = [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00];
static DOT: Glyph = [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C];
static SLASH: Glyph = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x00];
static PERCENT: Glyph = [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03];
static OPEN: Glyph = [0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E];
static CLOSE: Glyph = [0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E];
static UNKNOWN: Glyph = [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04];
// Statics, so each glyph has one address (the C++ test compares references).
static LETTER_TABLE: [Glyph; 26] = LETTERS;
static DIGIT_TABLE: [Glyph; 10] = DIGITS;

/// Uppercase-only glyph rows (bit 4 = leftmost column) of byte `c`; lowercase maps to uppercase, unknown to '?'.
pub fn hud_glyph(c: u8) -> &'static Glyph {
    let c = c.to_ascii_uppercase();
    match c {
        b'A'..=b'Z' => &LETTER_TABLE[usize::from(c - b'A')],
        b'0'..=b'9' => &DIGIT_TABLE[usize::from(c - b'0')],
        b' ' => &SPACE,
        b':' => &COLON,
        b'-' => &DASH,
        b'+' => &PLUS,
        b'.' => &DOT,
        b'/' => &SLASH,
        b'[' => &OPEN,
        b']' => &CLOSE,
        b'%' => &PERCENT,
        _ => &UNKNOWN,
    }
}

/// Width in texels of `text` (one glyph per byte) at `scale`: one blank column between glyphs, none trailing.
pub fn hud_text_width(text: &str, scale: u32) -> u32 {
    if text.is_empty() {
        return 0;
    }
    // uint32_t(size * (kGlyphW + 1) - 1) * scale: unsigned, so it wraps like the C++.
    ((text.len() * (GLYPH_W as usize + 1) - 1) as u32).wrapping_mul(scale)
}

// --- Card chrome and type ----------------------------------------------------------------------------------------------

/// Lines without a role keep their own colours; these are the roles' look-alikes for them, under the cap: text at ~70%
/// and dim text at ~46% read on the near-black card.
pub const CARD_TEXT: [f32; 3] = [0.70, 0.70, 0.70];
pub const CARD_DIM: [f32; 3] = [0.46, 0.46, 0.46];
/// Material Design 3 card chrome, in output pixels (the card is drawn 1:1 in the glasses): an extra-large rounded
/// surface container (high) at this alpha (a hint of the desktop behind), with a 1 px outline-variant edge.
pub const BACKGROUND_ALPHA: f32 = 0.9;
pub const PAD: f32 = 22.0;
pub const LINE_GAP: f32 = 8.0;
pub const RADIUS: f32 = MD_SHAPE_EXTRA_LARGE;
pub const BORDER: f32 = 1.0;
/// Built-in font: each font texel is 2x2 pixels.
pub const PIXEL_FONT_ZOOM: u32 = 2;
/// Real font: em size per [`HudLine::scale`] step, chosen so capitals match the pixel font's height.
pub const EM_PER_SCALE: u32 = 19;
/// Help card: roomier padding, gaps between title / rows / footer, between rows and columns.
pub const HELP_PAD: f32 = 34.0;
pub const SECTION_GAP: f32 = 22.0;
pub const ROW_GAP: f32 = 12.0;
pub const COLUMN_GAP: f32 = 48.0;
/// M3 dividers (outline variant) under the card title and between sections, a little translucent so they never count
/// as ink.
pub const DIVIDER_ALPHA: f32 = 0.8;
/// The video's progress is M3's linear indicator, this tall, fully rounded.
pub const PROGRESS_H: u32 = 4;
/// A fainter divider between help sections than under the title.
pub const SECTION_RULE_ALPHA: f32 = 0.5;

/// A line's size: its advance width and how far it reaches above and below its baseline.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct LineBox {
    pub width: u32,
    pub ascent: u32,
    pub descent: u32,
}

impl LineBox {
    pub fn height(&self) -> u32 {
        self.ascent + self.descent
    }
}

/// Installed-font em size of a line: from `px` when set, else from its scale step.
pub fn em_px(line: &HudLine) -> u32 {
    if line.px != 0 { line.px.clamp(6, 160) } else { line.scale.clamp(1, 8) * EM_PER_SCALE }
}

/// Pixel-font zoom of a line: from its scale step, or the nearest whole zoom to `px` (at least 1) so capitals match.
pub fn pixel_zoom(line: &HudLine) -> u32 {
    if line.px == 0 {
        return line.scale.clamp(1, 8) * PIXEL_FONT_ZOOM;
    }
    ((em_px(line) * PIXEL_FONT_ZOOM + EM_PER_SCALE / 2) / EM_PER_SCALE).max(1)
}

/// A line's box in `font`, or in the pixel font without one.
pub fn line_box(line: &HudLine, font: Option<&dyn HudTextMetrics>) -> LineBox {
    let Some(font) = font else {
        let z = pixel_zoom(line);
        return LineBox { width: hud_text_width(&line.text, z), ascent: GLYPH_H * z, descent: 0 };
    };
    let m = font.metrics(em_px(line), line.bold);
    LineBox { width: font.measure(&line.text, em_px(line), line.bold), ascent: m.ascent, descent: m.descent }
}

// --- Card rasterisation ------------------------------------------------------------------------------------------------

// Premultiplied "over": paint colour `c` with coverage `a` on top of what is there. (Not md_over: this one lets a NaN
// coverage through, as the C++ `over` does.)
fn over(im: &mut HudImage, x: i32, y: i32, c: [f32; 3], a: f32) {
    if x < 0 || y < 0 || x as u32 >= im.width || y as u32 >= im.height || a <= 0.0 {
        return;
    }
    let a = cmin(a, 1.0);
    let i = (y as usize * im.width as usize + x as usize) * 4;
    let p = &mut im.rgba[i..i + 4];
    for k in 0..3 {
        p[k] = clampf(c[k] * a * 255.0 + f32::from(p[k]) * (1.0 - a), 0.0, 255.0).round() as u8;
    }
    p[3] = clampf(a * 255.0 + f32::from(p[3]) * (1.0 - a), 0.0, 255.0).round() as u8;
}

// Signed distance (pixels, negative inside) from pixel centre to a rounded rectangle.
fn rounded_distance(px: f32, py: f32, w: f32, h: f32, r: f32) -> f32 {
    let qx = (px - w / 2.0).abs() - (w / 2.0 - r);
    let qy = (py - h / 2.0).abs() - (h / 2.0 - r);
    let (ox, oy) = (cmax(qx, 0.0), cmax(qy, 0.0));
    (ox * ox + oy * oy).sqrt() + cmin(cmax(qx, qy), 0.0) - r
}

// A `width` x `height` image painted with the rounded translucent card (M3 surface container high) and its
// outline-variant edge.
pub(crate) fn allocate_card(width: u32, height: u32, m: &MaterialScheme) -> HudImage {
    let back = m.surface_container_high;
    let rim = m.outline_variant;
    let mut im = HudImage { width, height, rgba: vec![0; width as usize * height as usize * 4] };
    let (w, h) = (width as f32, height as f32);
    // Most of the card is plain translucent background. Outside the corner squares the signed distance is minus the
    // distance to the nearest edge, so a pixel at least BORDER + 0.5 inside is exactly the background: fill it directly
    // instead of evaluating the rounded rectangle (the same bytes; ~20x faster on a menu-sized card).
    let mut fill = [0u8; 4];
    for i in 0..3 {
        fill[i] = (back[i] * BACKGROUND_ALPHA * 255.0).round() as u8;
    }
    fill[3] = (BACKGROUND_ALPHA * 255.0).round() as u8;
    for y in 0..height {
        for x in 0..width {
            let (cx, cy) = (x as f32 + 0.5, y as f32 + 0.5);
            let edge = cmin(cmin(cmin(cx, cy), w - cx), h - cy);
            let band = (cx >= RADIUS && cx <= w - RADIUS) || (cy >= RADIUS && cy <= h - RADIUS);
            let i = (y as usize * width as usize + x as usize) * 4;
            if band && edge >= BORDER + 0.5 {
                im.rgba[i..i + 4].copy_from_slice(&fill);
                continue;
            }
            let d = rounded_distance(cx, cy, w, h, RADIUS);
            let outer = clampf(0.5 - d, 0.0, 1.0); // anti-aliased card edge
            let inner = clampf(0.5 - (d + BORDER), 0.0, 1.0); // inside the border
            if outer <= 0.0 {
                continue;
            }
            over(&mut im, x as i32, y as i32, rim, outer);
            if inner > 0.0 {
                // Replace the edge colour with the translucent background inside the border.
                let p = &mut im.rgba[i..i + 4];
                for k in 0..3 {
                    p[k] = ((back[k] * BACKGROUND_ALPHA * inner + rim[k] * outer * (1.0 - inner)) * 255.0).round() as u8;
                }
                p[3] = ((BACKGROUND_ALPHA * inner + outer * (1.0 - inner)) * 255.0).round() as u8;
            }
        }
    }
    im
}

// Draw one line in `color` (its role's colour: hud_line_color) with its top edge at `top` and left edge at `left`.
// Unsigned sums cast to int wrap as in the C++.
fn draw_line(
    im: &mut HudImage,
    line: &HudLine,
    color: [f32; 3],
    b: &LineBox,
    left: i32,
    top: u32,
    font: Option<&dyn HudTextMetrics>,
) {
    if let Some(font) = font {
        let baseline = top.wrapping_add(b.ascent) as i32;
        font.draw(&line.text, em_px(line), line.bold, left, baseline, &mut |x, y, coverage| over(im, x, y, color, coverage));
        return;
    }
    let z = pixel_zoom(line);
    let mut x = left;
    for ch in line.text.bytes() {
        let g = hud_glyph(ch);
        for row in 0..GLYPH_H {
            for col in 0..GLYPH_W {
                if u32::from(g[row as usize]) & (0x10 >> col) == 0 {
                    continue;
                }
                for sy in 0..z {
                    for sx in 0..z {
                        let px = x.wrapping_add(col.wrapping_mul(z).wrapping_add(sx) as i32);
                        let py = top.wrapping_add(row.wrapping_mul(z)).wrapping_add(sy) as i32;
                        over(im, px, py, color, 1.0);
                    }
                }
            }
        }
        x = x.wrapping_add(((GLYPH_W + 1) * z) as i32);
    }
}

/// A pop-up card: `lines` centred one under another on the rounded card, sized to the widest line plus padding, in the
/// Material scheme of `accent` (via [`glass_theme_scheme`]), in `font` or (None) the pixel font.
pub fn render_hud_card(lines: &[HudLine], accent: [f32; 3], font: Option<&dyn HudTextMetrics>) -> HudImage {
    let m = glass_theme_scheme(accent);
    let boxes: Vec<LineBox> = lines.iter().map(|line| line_box(line, font)).collect();
    let (mut content_w, mut content_h) = (0u32, 0u32);
    for (i, b) in boxes.iter().enumerate() {
        content_w = content_w.max(b.width);
        content_h += b.height() + if i > 0 { LINE_GAP as u32 } else { 0 };
    }
    let mut im = allocate_card(content_w + 2 * PAD as u32, content_h + 2 * PAD as u32, &m);
    let mut top = PAD as u32;
    for (line, b) in lines.iter().zip(&boxes) {
        let left = (im.width.wrapping_sub(b.width) / 2) as i32;
        draw_line(&mut im, line, hud_line_color(line, &m), b, left, top, font);
        top += b.height() + LINE_GAP as u32;
    }
    im
}

// Help and video card building blocks: both cards are drawn from these, so type, colours, sizes and spacing cannot
// drift apart.

/// A card text cell: its line (colour from its role at draw time) and the line's box.
#[derive(Clone, Debug, Default)]
pub(crate) struct TextCell {
    pub(crate) line: HudLine,
    pub(crate) b: LineBox,
}

pub(crate) fn text_cell(line: HudLine, font: Option<&dyn HudTextMetrics>) -> TextCell {
    let b = line_box(&line, font);
    TextCell { line, b }
}

// Draw `c` on the baseline `ascent` below `top` (cells sharing one baseline: row_ascent).
pub(crate) fn draw_cell(im: &mut HudImage, c: &TextCell, m: &MaterialScheme, left: i32, top: u32, ascent: u32, font: Option<&dyn HudTextMetrics>) {
    draw_line(im, &c.line, hud_line_color(&c.line, m), &c.b, left, top.wrapping_add(ascent).wrapping_sub(c.b.ascent), font);
}

pub(crate) fn draw_centered(im: &mut HudImage, c: &TextCell, m: &MaterialScheme, top: u32, font: Option<&dyn HudTextMetrics>) {
    let left = (im.width.wrapping_sub(c.b.width) / 2) as i32;
    draw_line(im, &c.line, hud_line_color(&c.line, m), &c.b, left, top, font);
}

// Divider across the card's padded width, centred in the section gap that ends at `section_top`.
pub(crate) fn draw_divider(im: &mut HudImage, section_top: u32, m: &MaterialScheme) {
    let y = section_top.wrapping_sub(SECTION_GAP as u32 / 2) as i32;
    for x in HELP_PAD as u32..im.width.saturating_sub(HELP_PAD as u32) {
        over(im, x as i32, y, m.outline_variant, DIVIDER_ALPHA);
    }
}

// Arrow pointing left (dir -1) or right (+1) in the square at (x, y) of side `size`, anti-aliased (4x4 samples).
fn draw_arrow(im: &mut HudImage, x: i32, y: i32, size: u32, dir: i32, color: [f32; 3]) {
    let s = size as f32;
    let half = s / 2.0;
    let (tip, base) = if dir < 0 { (0.0, s * 0.8) } else { (s, s * 0.2) };
    for py in 0..size {
        for px in 0..size {
            let mut inside = 0;
            for sy in 0..4 {
                for sx in 0..4 {
                    let fx = px as f32 + (sx as f32 + 0.5) / 4.0;
                    let fy = py as f32 + (sy as f32 + 0.5) / 4.0;
                    let t = (fx - base) / (tip - base); // 0 at the base, 1 at the tip
                    if (0.0..=1.0).contains(&t) && (fy - half).abs() <= half * (1.0 - t) {
                        inside += 1;
                    }
                }
            }
            if inside != 0 {
                over(im, x.wrapping_add(px as i32), y.wrapping_add(py as i32), color, inside as f32 / 16.0);
            }
        }
    }
}

// --- Status pop-ups ----------------------------------------------------------------------------------------------------

/// Pop-up opacity timing: fade in, hold, fade out.
pub const POPUP_FADE_IN_MS: f64 = 120.0;
pub const POPUP_HOLD_MS: f64 = 1500.0;
pub const POPUP_FADE_OUT_MS: f64 = 380.0;
pub const POPUP_TOTAL_MS: f64 = POPUP_FADE_IN_MS + POPUP_HOLD_MS + POPUP_FADE_OUT_MS;

/// Pop-up opacity `t` ms after it was shown: fade in, hold, fade out; 0 outside.
pub fn popup_alpha(t: f64) -> f32 {
    if t < 0.0 || t >= POPUP_TOTAL_MS {
        return 0.0;
    }
    if t < POPUP_FADE_IN_MS {
        return (t / POPUP_FADE_IN_MS) as f32;
    }
    if t < POPUP_FADE_IN_MS + POPUP_HOLD_MS {
        return 1.0;
    }
    let out = (t - POPUP_FADE_IN_MS - POPUP_HOLD_MS) / POPUP_FADE_OUT_MS;
    (1.0 - out * out * (3.0 - 2.0 * out)) as f32 // smooth fade out
}

// A pop-up section's lines: label, value, hint, key.
fn popup_label(text: &str) -> HudLine {
    hud_line(text, 2, CARD_DIM, false, 0, HudRole::OnSurfaceVariant)
}
fn popup_value(text: &str) -> HudLine {
    hud_line(text, 4, CARD_TEXT, true, 0, HudRole::Primary)
}
fn popup_hint(text: &str) -> HudLine {
    hud_line(text, 2, CARD_DIM, false, 0, HudRole::OnSurfaceVariant)
}
fn popup_key(text: &str) -> HudLine {
    hud_line(text, 1, CARD_DIM, false, 0, HudRole::Outline)
}

/// The viewing-mode section. mode: 0 anchor, 1 locked, 2 follow (the ViewPayload encoding). Each pop-up section: what it
/// is (on-surface-variant), its new state (large, bold, the theme's primary), what that means (on-surface-variant) and
/// its key (outline).
pub fn view_mode_lines(mode: u8) -> Vec<HudLine> {
    // Maximize-follow (2026-11-03): the overview is always anchor; a maximized screen follows you, or is locked by choice
    // (Ctrl+Alt+V there, kept for the session). Only the start (anchor; a fresh start maximizes in follow) and Ctrl+Alt+V
    // show this card: a maximize changes the mode quietly.
    let (name, hint, how) = match mode {
        1 => ("Locked", "Maximized screen moves with your head", "Ctrl+Alt+V for follow"),
        2 => ("Follow", "Maximized screen eases in front of you", "Ctrl+Alt+V for locked"),
        _ => ("Anchor", "Screens stay fixed in the room", "A maximized screen follows you"),
    };
    vec![popup_label("View mode"), popup_value(name), popup_hint(hint), popup_key(how)]
}

pub fn cursor_follow_lines(on: bool) -> Vec<HudLine> {
    vec![
        popup_label("Cursor follow"),
        popup_value(if on { "On" } else { "Off" }),
        popup_hint(if on { "View follows the pointer between screens" } else { "View stays on the selected screen" }),
        popup_key("Ctrl+Alt+F to change"),
    ]
}

pub fn prediction_lines(on: bool) -> Vec<HudLine> {
    vec![
        popup_label("Prediction"),
        popup_value(if on { "On" } else { "Off" }),
        popup_hint(if on { "Screens lead your head motion slightly" } else { "Screens use the latest sensor pose" }),
        popup_key("Ctrl+Alt+P to change"),
    ]
}

/// Typing mode (Ctrl+Alt+T). Every pop-up is drawn in its M3 scheme's roles, under the OLED cap ([`CARD_MAX_LEVEL`]).
pub fn typing_lines(on: bool) -> Vec<HudLine> {
    vec![
        popup_label("Keys"),
        popup_value(if on { "Typing" } else { "XR control" }),
        popup_hint(if on { "XR keys off: arrows / Space / Tab / Esc work as usual" } else { "XR keys back on" }),
        popup_key(if on { "Ctrl+Alt+T for XR control" } else { "Ctrl+Alt+T for typing" }),
    ]
}

/// The tracking hint's one line (head tracking is being reconnected after a USB drop).
pub fn tracking_hint_lines() -> Vec<HudLine> {
    vec![popup_hint("Reconnecting tracking...")]
}

/// The status pop-ups' state: which card shows, when, and in which theme colour. The C++ also rasterises the card
/// (`image()`); here the lines it would draw are kept instead ([`StatusPopups::lines`]), and the generation bumps exactly
/// where the C++ draws a new card.
#[derive(Clone, Debug)]
pub struct StatusPopups {
    seen: bool,
    cursor_follow: bool,
    prediction: bool,
    typing: bool,
    mode: u8,
    shown_ms: f64,
    generation: u64,
    lines: Vec<HudLine>,
    accent: [f32; 3],
}

impl Default for StatusPopups {
    fn default() -> Self {
        Self {
            seen: false,
            cursor_follow: false,
            prediction: false,
            typing: false,
            mode: 0,
            shown_ms: 0.0,
            generation: 0,
            lines: Vec::new(),
            accent: MD_DEFAULT_SEED,
        }
    }
}

impl StatusPopups {
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed every view update. The first shows the viewing mode; afterwards a pop-up appears only for what changed
    /// (both sections if both changed). Returns true if a pop-up (re)started. Keeps the current prediction and typing.
    pub fn update(&mut self, mode: u8, cursor_follow: bool, now_ms: f64) -> bool {
        self.update_prediction(mode, cursor_follow, self.prediction, now_ms)
    }

    /// Prediction toggles get their own section; the initial prediction state is not announced. Keeps the typing state.
    pub fn update_prediction(&mut self, mode: u8, cursor_follow: bool, prediction: bool, now_ms: f64) -> bool {
        self.update_all(mode, cursor_follow, prediction, self.typing, now_ms)
    }

    /// Typing mode switches get their own section; every run starts in control mode, which is not announced. A card that
    /// shows only the typing section uses the dimmed card accent too, so nothing on it exceeds [`CARD_MAX_LEVEL`].
    pub fn update_all(&mut self, mode: u8, cursor_follow: bool, prediction: bool, typing: bool, now_ms: f64) -> bool {
        let mode = if mode > 2 { 0 } else { mode };
        let first = !self.seen;
        let mode_changed = first || mode != self.mode;
        let cursor_changed = !first && cursor_follow != self.cursor_follow;
        let prediction_changed = !first && prediction != self.prediction;
        let typing_changed = if first { typing } else { typing != self.typing }; // a first update in typing mode says so
        self.seen = true;
        self.mode = mode;
        self.cursor_follow = cursor_follow;
        self.prediction = prediction;
        self.typing = typing;
        if !mode_changed && !cursor_changed && !prediction_changed && !typing_changed {
            return false;
        }
        let mut lines = Vec::new();
        if mode_changed {
            lines = view_mode_lines(mode);
        }
        let section = |lines: &mut Vec<HudLine>, more: Vec<HudLine>| {
            if !lines.is_empty() {
                lines.push(hud_line("", 1, CARD_DIM, false, 0, HudRole::None));
            }
            lines.extend(more);
        };
        if cursor_changed {
            section(&mut lines, cursor_follow_lines(cursor_follow));
        }
        if prediction_changed {
            section(&mut lines, prediction_lines(prediction));
        }
        if typing_changed {
            section(&mut lines, typing_lines(typing));
        }
        self.lines = lines;
        self.shown_ms = now_ms;
        self.generation += 1;
        true
    }

    /// A viewing-mode change that is no news: a maximize switches to follow and an un-maximize back to anchor by
    /// themselves (maximize-follow), so only Ctrl+Alt+V's changes get the mode card. Call before update(): the mode is
    /// taken without a pop-up. Before the first update it does nothing (the first update still shows the starting mode).
    pub fn track_mode(&mut self, mode: u8) {
        if self.seen {
            self.mode = if mode > 2 { 0 } else { mode };
        }
    }

    /// The theme colour the cards are drawn in. A change redraws a card that is still showing, in place (a new
    /// generation, the same fade), and returns true.
    pub fn set_accent(&mut self, accent: [f32; 3], now_ms: f64) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        if !self.active(now_ms) {
            return false; // the next card uses it
        }
        self.generation += 1;
        true
    }

    pub fn accent(&self) -> [f32; 3] {
        self.accent
    }

    pub fn alpha(&self, now_ms: f64) -> f32 {
        if self.generation != 0 { popup_alpha(now_ms - self.shown_ms) } else { 0.0 }
    }

    pub fn active(&self, now_ms: f64) -> bool {
        self.generation != 0 && now_ms - self.shown_ms < POPUP_TOTAL_MS
    }

    /// The card shown last (what the C++ rasterises into `image()`).
    pub fn lines(&self) -> &[HudLine] {
        &self.lines
    }

    /// Bumps when the card changes.
    pub fn generation(&self) -> u64 {
        self.generation
    }
}

// --- The eased card fade -----------------------------------------------------------------------------------------------

/// Eased show/hide fade shared by the centred cards: toggling mid-fade reverses from the current opacity instead of
/// jumping.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CardFade {
    in_ms: f64,
    out_ms: f64,
    visible: bool,
    /// Linear level at the last toggle; hold pauses its clock.
    from: f32,
    /// Eased opacity at the last toggle, for continuous reversal.
    from_alpha: f32,
    changed_ms: f64,
}

impl CardFade {
    pub const fn new(in_ms: f64, out_ms: f64) -> Self {
        Self { in_ms, out_ms, visible: false, from: 0.0, from_alpha: 0.0, changed_ms: 0.0 }
    }

    /// True if visibility changed.
    pub fn set(&mut self, visible: bool, now_ms: f64) -> bool {
        if visible == self.visible {
            return false;
        }
        self.from_alpha = self.alpha(now_ms);
        self.from = self.level(now_ms); // reverse from wherever the fade is
        self.visible = visible;
        self.changed_ms = now_ms;
        true
    }

    pub fn visible(&self) -> bool {
        self.visible
    }

    /// Linear 0..1.
    pub fn level(&self, now_ms: f64) -> f32 {
        let d = now_ms - self.changed_ms;
        let dt = if 0.0 < d { d } else { 0.0 }; // std::max(0.0, d)
        if (if self.visible { self.in_ms } else { self.out_ms }) <= 0.0 {
            return if self.visible { 1.0 } else { 0.0 };
        }
        if self.visible {
            let v = self.from + (dt / self.in_ms) as f32;
            return if v < 1.0 { v } else { 1.0 }; // std::min(1.f, v)
        }
        let v = self.from - (dt / self.out_ms) as f32;
        if 0.0 < v { v } else { 0.0 } // std::max(0.f, v)
    }

    /// Eased 0..1.
    pub fn alpha(&self, now_ms: f64) -> f32 {
        let target: f32 = if self.visible { 1.0 } else { 0.0 };
        let duration = (if self.visible { self.in_ms } else { self.out_ms }) * f64::from((target - self.from).abs());
        if duration <= 0.0 {
            return target;
        }
        let x = (now_ms - self.changed_ms) / duration;
        // std::clamp(x, 0.0, 1.0); f64::clamp has the same comparisons (a NaN stays NaN).
        let t = x.clamp(0.0, 1.0) as f32;
        let remaining = 1.0 - t;
        self.from_alpha + (target - self.from_alpha) * (1.0 - remaining * remaining * remaining)
    }

    /// Mid-fade.
    pub fn animating(&self, now_ms: f64) -> bool {
        let l = self.level(now_ms);
        if self.visible { l < 1.0 } else { l > 0.0 }
    }

    /// The fade did not run from `since_ms` to `now_ms` (its card could not be seen): at now_ms it is where it was at
    /// since_ms, or where its last toggle left it if that came later. Call it on every unseen frame to keep it still.
    pub fn hold(&mut self, since_ms: f64, now_ms: f64) {
        if now_ms <= since_ms {
            return;
        }
        // Move the clock, preserving both the curve and its progress. A toggle after since_ms starts at its own time.
        let start = if since_ms < self.changed_ms { self.changed_ms } else { since_ms };
        self.changed_ms += now_ms - start;
    }
}

// --- Keyboard help card ----------------------------------------------------------------------------------------------
//
// Keyboard help (Ctrl+Alt+H): a centred card listing every bind, toggled by the supervisor. Fades in/out with an eased
// curve and a small rise; toggling mid-fade reverses from the current opacity instead of jumping.
//
// Fit: the card keeps the menu's chrome (padding, border, radius, colours) but has its own, more compact text sizes and
// gaps. Given the output surface (`max_width` x `max_height`, 0 = unbounded) it takes the largest text size (row em
// HELP_ROW_PX_MAX down to HELP_ROW_PX_MIN) whose card, with every row on it, leaves HUD_FIT_MARGIN free on every side.
// Pages: if even the smallest text does not fit every row, the card keeps that smallest text and splits the rows into
// pages instead (any number of rows): each page holds what fits the height, a section (rows from one `section` row up to
// the next) is kept on one page when it fits one, and every page has the same card size, a "2 / 3" indicator between a
// previous and a next arrow, and a hint naming the page keys. The supervisor turns pages (HelpOverlay::set `turn`); the
// arrows and the indicator are also clicked with the overview free pointer (HelpCardControls, help_geometry_json).
// fits=false only if a single row is wider or taller than the surface allows.

/// A rendered card: `width` x `height` premultiplied RGBA8, row 0 the top row (hud.hpp HudImage).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HudImage {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

impl HudImage {
    /// The pixels are exactly width x height x 4 bytes: what the GL draws need before they upload.
    pub fn complete(&self) -> bool {
        self.width != 0
            && self.height != 0
            && self.rgba.len() == self.width as usize * self.height as usize * 4
    }
}

/// A centred card's top-left, output px.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct CardOrigin {
    pub x: f32,
    pub y: f32,
}

/// One help row. `section`: this row starts a new section.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HelpRow {
    pub keys: String,
    pub action: String,
    pub section: bool,
}

impl HelpRow {
    pub fn new(keys: impl Into<String>, action: impl Into<String>) -> Self {
        Self { keys: keys.into(), action: action.into(), section: false }
    }
}

/// Free space the help card leaves on every side of the output (= its padding).
pub const HUD_FIT_MARGIN: u32 = 34;
/// Row text em size in pixels, roomiest / smallest.
pub const HELP_ROW_PX_MAX: u32 = 30;
pub const HELP_ROW_PX_MIN: u32 = 18;
/// Page keys named by the hint on a paged card (tools/native_spike/view.py BINDS help_prev / help_next; a test compares).
pub const HELP_PAGE_HINT: &str = "Ctrl+Alt+PgUp / PgDn or click an arrow for more";

/// The help card's size and pages for a surface.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HelpCardFit {
    pub width: u32,
    pub height: u32,
    pub row_px: u32,
    pub fits: bool,
    /// Rows on each page, in order (one entry: everything on one page).
    pub page_rows: Vec<u32>,
}

impl HelpCardFit {
    pub fn pages(&self) -> u32 {
        self.page_rows.len() as u32
    }
}

/// A clickable part of a paged card, card pixels from its top-left.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct HelpRect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl HelpRect {
    pub fn is_empty(&self) -> bool {
        self.w <= 0 || self.h <= 0
    }
}

/// Clickable parts of a paged card (all empty on a one-page card). The indicator turns to the next page like the next
/// arrow.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct HelpCardControls {
    pub prev: HelpRect,
    pub indicator: HelpRect,
    pub next: HelpRect,
}

// Help card text and gaps, roomiest first. The card's chrome (padding, border, radius, colours) is the menu's; its text
// is smaller and its gaps tighter so every bind fits at 1920x1200 @1.25 with room for a few more rows. The layout takes
// the first step that fits every row on one page; when none does, the smallest step split into pages. Row text stays at
// least HELP_ROW_PX_MIN (capitals ~13 px) on the glasses.
#[derive(Clone, Copy, Debug, Default)]
struct HelpStyle {
    title_px: u32,
    row_px: u32,
    footer_px: u32,
    row_gap: u32,
    section_gap: u32,
    column_gap: u32,
}

const fn style(title_px: u32, row_px: u32, footer_px: u32, row_gap: u32, section_gap: u32, column_gap: u32) -> HelpStyle {
    HelpStyle { title_px, row_px, footer_px, row_gap, section_gap, column_gap }
}

const HELP_STYLES: [HelpStyle; 7] = [
    style(32, 30, 20, 8, 16, 36),
    style(30, 28, 19, 7, 15, 34),
    style(28, 26, 18, 6, 14, 32),
    style(26, 24, 17, 6, 12, 30),
    style(24, 22, 16, 5, 11, 28),
    style(22, 20, 15, 4, 10, 26),
    style(20, 18, 14, 4, 9, 24),
];
const _: () = assert!(HELP_STYLES[0].row_px == HELP_ROW_PX_MAX && HELP_STYLES[HELP_STYLES.len() - 1].row_px == HELP_ROW_PX_MIN);

fn help_footer_text() -> String {
    format!("Ctrl+Alt+H to close. Help and menu close after {OVERLAY_IDLE_CLOSE_S} s idle.")
}

/// A help-card text cell: one line at `px` in its role (the colours do not change the layout).
fn cell(text: &str, color: [f32; 3], bold: bool, px: u32, role: HudRole, font: Option<&dyn HudTextMetrics>) -> TextCell {
    text_cell(hud_line(text, 1, color, bold, px, role), font)
}

#[derive(Clone, Debug, Default)]
struct HelpLayout {
    style: HelpStyle,
    title: TextCell,
    footer: TextCell,
    hint: TextCell,
    table: Vec<(TextCell, TextCell)>,
    /// Row i starts a section (never row 0).
    starts: Vec<bool>,
    /// [begin, end) rows per page.
    pages: Vec<(usize, usize)>,
    keys_w: u32,
    table_w: u32,
    content_w: u32,
    content_h: u32,
    /// Paged footer: indicator line height, arrow size, spacing.
    nav_h: u32,
    arrow: u32,
    nav_gap: u32,
}

pub(crate) fn card_size(content: u32) -> u32 {
    content + 2 * HELP_PAD as u32
}

// Cells sharing one baseline: tallest ascent above it, deepest descent below it.
fn row_height(l: &HelpLayout, i: usize) -> u32 {
    let (k, a) = &l.table[i];
    k.b.ascent.max(a.b.ascent) + k.b.descent.max(a.b.descent)
}

fn gap_before(l: &HelpLayout, i: usize) -> u32 {
    if l.starts[i] { l.style.section_gap } else { l.style.row_gap }
}

fn rows_height(l: &HelpLayout, b: usize, e: usize) -> u32 {
    let mut h = 0;
    for i in b..e {
        h += row_height(l, i) + if i > b { gap_before(l, i) } else { 0 };
    }
    h
}

fn page_indicator(l: &HelpLayout, page: usize, font: Option<&dyn HudTextMetrics>) -> TextCell {
    cell(&format!("{} / {}", page + 1, l.pages.len()), CARD_TEXT, true, l.style.footer_px, HudRole::OnSurface, font)
}

// Rows below the title and above the footer: one page if `avail` (0 = unbounded) holds them all, else greedy pages that
// keep a section together when it fits a page; a section taller than a page is split row by row. Never an empty page.
fn paginate(l: &mut HelpLayout, avail: u32) {
    let n = l.table.len();
    l.pages.clear();
    if avail == 0 || rows_height(l, 0, n) <= avail {
        l.pages.push((0, n));
        return;
    }
    let mut i = 0;
    while i < n {
        let b = i;
        let mut used = 0;
        while i < n {
            let mut e = i + 1;
            while e < n && !l.starts[e] {
                e += 1; // the section (or its rest) from row i
            }
            let add = if i > b { gap_before(l, i) } else { 0 } + rows_height(l, i, e);
            if used + add <= avail {
                used += add;
                i = e;
                continue;
            }
            if i > b {
                break; // the next page takes this section
            }
            // Too tall for any page.
            while i < n && (i == b || used + gap_before(l, i) + row_height(l, i) <= avail) {
                used += if i > b { gap_before(l, i) } else { 0 } + row_height(l, i);
                i += 1;
            }
            break;
        }
        l.pages.push((b, i));
    }
}

// Fixed parts around the rows: title + gap above, gap + footer below (a paged card adds the indicator line and the hint).
fn head_height(l: &HelpLayout) -> u32 {
    l.title.b.height() + l.style.section_gap
}

fn foot_height(l: &HelpLayout, paged: bool) -> u32 {
    let mut h = l.style.section_gap + l.footer.b.height();
    if paged {
        h += l.nav_h + l.style.row_gap + l.hint.b.height() + l.style.row_gap / 2;
    }
    h
}

fn layout_help(rows: &[HelpRow], font: Option<&dyn HudTextMetrics>, st: HelpStyle, max_h: u32) -> HelpLayout {
    let mut l = HelpLayout {
        style: st,
        title: cell("Keyboard shortcuts", CARD_TEXT, true, st.title_px, HudRole::OnSurface, font),
        footer: cell(&help_footer_text(), CARD_DIM, false, st.footer_px, HudRole::Outline, font),
        hint: cell(HELP_PAGE_HINT, CARD_DIM, false, st.footer_px, HudRole::Outline, font),
        ..HelpLayout::default()
    };
    let mut actions_w = 0;
    for (i, r) in rows.iter().enumerate() {
        let k = cell(&r.keys, CARD_TEXT, true, st.row_px, HudRole::OnSurface, font);
        let a = cell(&r.action, CARD_DIM, false, st.row_px, HudRole::OnSurfaceVariant, font);
        l.keys_w = l.keys_w.max(k.b.width);
        actions_w = actions_w.max(a.b.width);
        l.table.push((k, a));
        l.starts.push(i > 0 && r.section);
    }
    l.table_w = if l.table.is_empty() { 0 } else { l.keys_w + st.column_gap + actions_w };
    // Paged footer line: "<  12 / 34  >" with drawn arrows sized to the indicator's capitals.
    let probe = cell("0 / 0", CARD_TEXT, true, st.footer_px, HudRole::None, font).b;
    l.arrow = (probe.ascent * 4 / 5).max(10);
    l.nav_h = probe.height().max(l.arrow);
    l.nav_gap = st.column_gap;
    let chrome = 2 * HUD_FIT_MARGIN + 2 * HELP_PAD as u32;
    let single = head_height(&l) + rows_height(&l, 0, l.table.len()) + foot_height(&l, false);
    let one_page = max_h == 0 || single + chrome <= max_h;
    let fixed = head_height(&l) + foot_height(&l, true) + chrome;
    paginate(&mut l, if one_page { 0 } else if max_h > fixed { max_h - fixed } else { 1 });
    let paged = l.pages.len() > 1;
    let tallest = l.pages.iter().map(|&(b, e)| rows_height(&l, b, e)).fold(0, u32::max);
    l.content_w = l.title.b.width.max(l.footer.b.width).max(l.table_w);
    if paged {
        let nav_w = 2 * l.arrow + 2 * l.nav_gap + page_indicator(&l, l.pages.len() - 1, font).b.width;
        l.content_w = l.content_w.max(l.hint.b.width).max(nav_w);
    }
    l.content_h = head_height(&l) + tallest + foot_height(&l, paged);
    l
}

fn help_fits(l: &HelpLayout, max_w: u32, max_h: u32) -> bool {
    (max_w == 0 || card_size(l.content_w) + 2 * HUD_FIT_MARGIN <= max_w)
        && (max_h == 0 || card_size(l.content_h) + 2 * HUD_FIT_MARGIN <= max_h)
}

fn choose_help_layout(rows: &[HelpRow], font: Option<&dyn HudTextMetrics>, max_w: u32, max_h: u32) -> HelpLayout {
    let mut l = HelpLayout::default();
    for st in HELP_STYLES {
        l = layout_help(rows, font, st, max_h);
        if l.pages.len() == 1 && help_fits(&l, max_w, max_h) {
            return l;
        }
    }
    l // the smallest text, in pages (or, if a row alone is too big, centred and clipped: fits=false)
}

/// The help card's size, row text size and pages for an output surface of `max_width` x `max_height` (0 = unbounded),
/// in `font` or (None) the pixel font.
pub fn help_card_fit(
    rows: &[HelpRow],
    font: Option<&dyn HudTextMetrics>,
    max_width: u32,
    max_height: u32,
) -> HelpCardFit {
    let l = choose_help_layout(rows, font, max_width, max_height);
    HelpCardFit {
        width: card_size(l.content_w),
        height: card_size(l.content_h),
        row_px: l.style.row_px,
        fits: help_fits(&l, max_width, max_height),
        page_rows: l.pages.iter().map(|&(b, e)| (e - b) as u32).collect(),
    }
}

// The paged footer's navigation line for page `shown`: arrows either side of the indicator, centred on a `width` card
// whose footer starts at `top`. int(...) of unsigned sums: modular, as in the C++.
struct HelpNav {
    indicator: TextCell,
    nav_left: i32,
    arrow_top: i32,
    text_left: i32,
    next_left: i32,
}

fn help_nav(l: &HelpLayout, shown: usize, width: u32, top: u32, font: Option<&dyn HudTextMetrics>) -> HelpNav {
    let indicator = page_indicator(l, shown, font);
    let nav_w = 2 * l.arrow + 2 * l.nav_gap + indicator.b.width;
    let nav_left = (width.wrapping_sub(nav_w) / 2) as i32;
    let arrow_top = top.wrapping_add(l.nav_h.wrapping_sub(l.arrow) / 2) as i32;
    let text_left = nav_left.wrapping_add((l.arrow + l.nav_gap) as i32);
    let next_left = text_left.wrapping_add((indicator.b.width + l.nav_gap) as i32);
    HelpNav { indicator, nav_left, arrow_top, text_left, next_left }
}

// Generous click targets: each half of the gap, and the line's height plus half a gap either side.
fn help_controls(l: &HelpLayout, nav: &HelpNav, top: u32) -> HelpCardControls {
    let st = l.style;
    let pad = (l.nav_gap / 2) as i32;
    let y = (top as i32).wrapping_sub(st.row_gap as i32);
    let h = (l.nav_h + 2 * st.row_gap) as i32;
    HelpCardControls {
        prev: HelpRect { x: nav.nav_left - pad, y, w: l.arrow as i32 + 2 * pad, h },
        indicator: HelpRect { x: nav.text_left - pad / 2, y, w: nav.indicator.b.width as i32 + pad, h },
        next: HelpRect { x: nav.next_left - pad, y, w: l.arrow as i32 + 2 * pad, h },
    }
}

// The footer sits at the bottom: on a short last page the rows end higher, the card keeps its size.
fn footer_top(l: &HelpLayout, height: u32, paged: bool) -> u32 {
    height.wrapping_sub(HELP_PAD as u32).wrapping_sub(foot_height(l, paged)).wrapping_add(l.style.section_gap)
}

/// The keyboard help card for an output surface of `max_width` x `max_height` (0 = unbounded): `rows` at the largest
/// text that fits on one page, else in pages with `page` (modulo the page count) shown, in the Material scheme of
/// `accent`, in `font` or (None) the pixel font. Also returns the page arrows' click targets (all empty on a one-page
/// card), card pixels from its top-left.
pub fn render_help_card(
    rows: &[HelpRow],
    accent: [f32; 3],
    font: Option<&dyn HudTextMetrics>,
    max_width: u32,
    max_height: u32,
    page: u32,
) -> (HudImage, HelpCardControls) {
    let m = glass_theme_scheme(accent);
    let l = choose_help_layout(rows, font, max_width, max_height);
    let st = l.style;
    let pages = l.pages.len();
    let shown = page as usize % pages;
    let paged = pages > 1;
    let mut controls = HelpCardControls::default();
    let mut im = allocate_card(card_size(l.content_w), card_size(l.content_h), &m);
    let mut top = HELP_PAD as u32;
    draw_centered(&mut im, &l.title, &m, top, font);
    top += l.title.b.height() + st.section_gap;
    let rule_y = top.wrapping_sub(st.section_gap / 2) as i32;
    for x in HELP_PAD as u32..im.width.saturating_sub(HELP_PAD as u32) {
        over(&mut im, x as i32, rule_y, m.outline_variant, DIVIDER_ALPHA); // divider in the gap's middle
    }
    let left = (im.width.wrapping_sub(l.table_w) / 2) as i32;
    let (b, e) = l.pages[shown];
    for i in b..e {
        let (k, a) = &l.table[i];
        if i > b {
            top += gap_before(&l, i);
            if l.starts[i] {
                // A fainter divider between sections, across the table.
                let y = top.wrapping_sub(st.section_gap / 2) as i32;
                for x in 0..l.table_w {
                    over(&mut im, left.wrapping_add(x as i32), y, m.outline_variant, SECTION_RULE_ALPHA);
                }
            }
        }
        let ascent = k.b.ascent.max(a.b.ascent);
        draw_cell(&mut im, k, &m, left, top, ascent, font);
        draw_cell(&mut im, a, &m, left.wrapping_add((l.keys_w + st.column_gap) as i32), top, ascent, font);
        top += ascent + k.b.descent.max(a.b.descent);
    }
    top = footer_top(&l, im.height, paged);
    if paged {
        let nav = help_nav(&l, shown, im.width, top, font);
        // M3 icon buttons (arrow_left / arrow_right) either side of the indicator.
        draw_arrow(&mut im, nav.nav_left, nav.arrow_top, l.arrow, -1, m.on_surface_variant);
        let indicator_top = top.wrapping_add(l.nav_h.wrapping_sub(nav.indicator.b.height()) / 2);
        draw_cell(&mut im, &nav.indicator, &m, nav.text_left, indicator_top, nav.indicator.b.ascent, font);
        draw_arrow(&mut im, nav.next_left, nav.arrow_top, l.arrow, 1, m.on_surface_variant);
        controls = help_controls(&l, &nav, top);
        top += l.nav_h + st.row_gap;
        draw_centered(&mut im, &l.hint, &m, top, font);
        top += l.hint.b.height() + st.row_gap / 2;
    }
    draw_centered(&mut im, &l.footer, &m, top, font);
    (im, controls)
}

/// The click targets [`render_help_card`] reports for `page` (taken modulo the page count), without drawing: all empty
/// on a one-page card. Card pixels from its top-left.
pub fn help_card_controls(
    rows: &[HelpRow],
    font: Option<&dyn HudTextMetrics>,
    max_width: u32,
    max_height: u32,
    page: u32,
) -> HelpCardControls {
    let l = choose_help_layout(rows, font, max_width, max_height);
    let pages = l.pages.len();
    if pages <= 1 {
        return HelpCardControls::default();
    }
    let (width, height) = (card_size(l.content_w), card_size(l.content_h));
    let top = footer_top(&l, height, true);
    let nav = help_nav(&l, page as usize % pages, width, top, font);
    help_controls(&l, &nav, top)
}

/// The supervisor's free-pointer hit testing for the help card: one status line
/// `{"help_geometry":{"card":[x,y,w,h],"prev":[...],"indicator":[...],"next":[...]}}` (prev / indicator / next only on
/// a paged card), canvas px (the overview's plane, as overview_geometry). The card is head-locked and the canvas
/// world-locked, so each rect is where the card's rect (output px, top-left `origin`) lands on the canvas under head
/// rotation `view` (row-major), as its bounding box. Empty if the canvas is unusable.
#[allow(clippy::too_many_arguments)]
pub fn help_geometry_json(
    view: &[f32; 9],
    width: u32,
    height: u32,
    half_w: f32,
    half_h: f32,
    origin: CardOrigin,
    card_w: u32,
    card_h: u32,
    controls: &HelpCardControls,
) -> String {
    if width == 0 || height == 0 || half_w <= 0.0 || half_h <= 0.0 {
        return String::new();
    }
    let inverse = transpose(view); // rotations: the inverse is the transpose
    let (w_px, h_px) = (width as f32, height as f32);
    let mut out = String::from("{\"help_geometry\":{");
    let mut first = true;
    let mut rect = |out: &mut String, name: &str, x: f32, y: f32, w: f32, h: f32| -> bool {
        let (mut lo_x, mut lo_y, mut hi_x, mut hi_y) = (1e9_f32, 1e9_f32, -1e9_f32, -1e9_f32);
        for (cx, cy) in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)] {
            // Output px -> head-locked NDC -> the canvas plane (world, z = -1) -> canvas px.
            let Some(c) = project(&inverse, 2.0 * cx / w_px - 1.0, 1.0 - 2.0 * cy / h_px, half_w, half_h) else {
                return false;
            };
            let px = (c[0] + 1.0) / 2.0 * w_px;
            let py = (1.0 - c[1]) / 2.0 * h_px;
            // std::min / std::max: a NaN corner leaves the box as it was.
            if px < lo_x {
                lo_x = px;
            }
            if py < lo_y {
                lo_y = py;
            }
            if hi_x < px {
                hi_x = px;
            }
            if hi_y < py {
                hi_y = py;
            }
        }
        // std::clamp then lround (half away from zero).
        let clamp = |v: f32| -> i64 { v.clamp(-8192.0, 8192.0).round() as i64 };
        let (l, t, r, bo) = (clamp(lo_x), clamp(lo_y), clamp(hi_x), clamp(hi_y));
        out.push_str(&format!(
            "{}\"{name}\":[{l},{t},{},{}]",
            if first { "" } else { "," },
            (r - l).max(1),
            (bo - t).max(1)
        ));
        first = false;
        true
    };
    if !rect(&mut out, "card", origin.x, origin.y, card_w as f32, card_h as f32) {
        return String::new();
    }
    for (name, r) in [("prev", controls.prev), ("indicator", controls.indicator), ("next", controls.next)] {
        if !r.is_empty()
            && !rect(&mut out, name, origin.x + r.x as f32, origin.y + r.y as f32, r.w as f32, r.h as f32)
        {
            return String::new();
        }
    }
    out + "}}"
}

/// Shared menu motion, matching Omarchy: fast ease-out entrance, shorter exit.
pub const MENU_FADE_IN_MS: f64 = 180.0;
pub const MENU_FADE_OUT_MS: f64 = 120.0;
pub const HELP_FADE_IN_MS: f64 = MENU_FADE_IN_MS;
pub const HELP_FADE_OUT_MS: f64 = MENU_FADE_OUT_MS;
/// Image-retention protection for the centred help card and the placement panels on the glasses' micro-OLED panels:
/// Material roles scaled under the cap and an idle auto-close. The cards never move while shown. The supervisor closes
/// both cards after this many seconds without a run key or panel change. The value lives in tools/native_spike/view.py
/// OVERLAY_IDLE_CLOSE_S; this copy is only for the help card's footer note (a test pins them).
pub const OVERLAY_IDLE_CLOSE_S: u32 = 30;
/// Brightest channel (0..255) any pixel of the help card or a placement panel may reach; full white is 255.
pub const CARD_MAX_LEVEL: u8 = 191;
/// The help card rises this far while fading in (and sinks fading out).
pub const HELP_RISE_PX: f32 = 18.0;

/// Top-left (from the top of the output) of a card drawn centred, lowered by `drop_px` while it fades in: the one
/// formula GlHud uses for the help card.
pub fn centered_card_origin(output_w: u32, output_h: u32, card_w: u32, card_h: u32, drop_px: f32) -> CardOrigin {
    // GlHud works bottom-up: bottom edge at floor((H - h) / 2 - drop). From the top that is H - bottom - h.
    let bottom = ((output_h as f32 - card_h as f32) / 2.0 - drop_px).floor();
    CardOrigin { x: ((output_w as f32 - card_w as f32) / 2.0).floor(), y: output_h as f32 - bottom - card_h as f32 }
}

/// The help card's state: shown or not, its rows, page and fit for the viewport. The C++ also rasterises the card; here
/// the generation bumps exactly where the C++ draws a new one, and the click targets come from [`help_card_controls`].
pub struct HelpOverlay {
    fade: CardFade,
    // Actual visibility changes and page turns, diagnostic only.
    shown: u64,
    hidden: u64,
    turns: u64,
    rows: Vec<HelpRow>,
    max_w: u32,
    max_h: u32,
    page: u32,
    pages: u32,
    controls: HelpCardControls,
    generation: u64,
    accent: [f32; 3],
    font: Option<Box<dyn HudTextMetrics>>,
}

impl Default for HelpOverlay {
    fn default() -> Self {
        Self {
            fade: CardFade::new(HELP_FADE_IN_MS, HELP_FADE_OUT_MS),
            shown: 0,
            hidden: 0,
            turns: 0,
            rows: Vec::new(),
            max_w: 0,
            max_h: 0,
            page: 0,
            pages: 1,
            controls: HelpCardControls::default(),
            generation: 0,
            accent: MD_DEFAULT_SEED,
            font: None,
        }
    }
}

impl HelpOverlay {
    pub fn new() -> Self {
        Self::default()
    }

    /// Show/hide; rows are laid out when shown with different rows (or the first time). Showing a hidden card starts it
    /// on page 1. `turn` (-1 / +1) flips a shown card to the previous / next page, wrapping round; ignored when hidden.
    /// Returns true if visibility changed or a shown card was redrawn (new page or new rows).
    pub fn set(&mut self, visible: bool, rows: &[HelpRow], now_ms: f64, turn: i32) -> bool {
        let showing = visible && !self.fade.visible();
        let new_rows = self.generation == 0 || rows != self.rows.as_slice();
        let mut page = self.page;
        if showing {
            page = 0; // reopening starts on page 1
        } else if visible && turn != 0 && self.pages > 1 {
            let step: i64 = if turn < 0 { -1 } else { 1 };
            page = ((i64::from(self.page) + step + i64::from(self.pages)) % i64::from(self.pages)) as u32;
        }
        let turned = page != self.page;
        let redraw = visible && (new_rows || turned);
        if redraw {
            if new_rows {
                self.rows = rows.to_vec();
            }
            self.page = page;
            self.render();
            if turned && !showing {
                self.turns += 1;
            }
        }
        if !self.fade.set(visible, now_ms) {
            return redraw; // a new page or new rows on a shown card: draw it
        }
        if visible {
            self.shown += 1;
        } else {
            self.hidden += 1;
        }
        true
    }

    /// The installed font the card is laid out in (None: the pixel font). Applies at the next layout.
    pub fn set_font(&mut self, font: Option<Box<dyn HudTextMetrics>>) {
        self.font = font;
    }

    /// The font the card is laid out in (None: the pixel font).
    pub fn font(&self) -> Option<&dyn HudTextMetrics> {
        self.font.as_deref()
    }

    /// The theme colour the card is drawn in; a change re-renders a rendered card (true).
    pub fn set_accent(&mut self, accent: [f32; 3]) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        if self.generation == 0 {
            return false; // the first render uses it
        }
        self.render();
        true
    }

    pub fn accent(&self) -> [f32; 3] {
        self.accent
    }

    /// Output surface size the card must fit (0 = unbounded); a rendered card is laid out again when it changes.
    pub fn set_viewport(&mut self, width: u32, height: u32) -> bool {
        if width == self.max_w && height == self.max_h {
            return false;
        }
        self.max_w = width;
        self.max_h = height;
        if self.generation == 0 {
            return false;
        }
        self.render();
        true
    }

    pub fn visible(&self) -> bool {
        self.fade.visible()
    }
    pub fn shown_count(&self) -> u64 {
        self.shown
    }
    pub fn hidden_count(&self) -> u64 {
        self.hidden
    }
    pub fn turns(&self) -> u64 {
        self.turns
    }
    pub fn page(&self) -> u32 {
        self.page
    }
    pub fn pages(&self) -> u32 {
        self.pages
    }
    pub fn rows(&self) -> &[HelpRow] {
        &self.rows
    }
    /// Card px; empty on a one-page card.
    pub fn controls(&self) -> &HelpCardControls {
        &self.controls
    }

    /// Eased 0..1.
    pub fn alpha(&self, now_ms: f64) -> f32 {
        if self.generation != 0 { self.fade.alpha(now_ms) } else { 0.0 }
    }

    /// Downward offset in output pixels, 0 when fully shown.
    pub fn offset_px(&self, now_ms: f64) -> f32 {
        (1.0 - self.alpha(now_ms)) * HELP_RISE_PX
    }

    /// Mid-fade: redraw every frame.
    pub fn animating(&self, now_ms: f64) -> bool {
        self.generation != 0 && self.fade.animating(now_ms)
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// The fit of the current rows for the viewport.
    pub fn fit(&self) -> HelpCardFit {
        help_card_fit(&self.rows, self.font.as_deref(), self.max_w, self.max_h)
    }

    // The rows and page for the viewport; bumps the generation.
    fn render(&mut self) {
        let font = self.font.as_deref();
        self.pages = help_card_fit(&self.rows, font, self.max_w, self.max_h).pages().max(1);
        self.page %= self.pages; // a new viewport or new rows can have fewer pages
        self.controls = help_card_controls(&self.rows, font, self.max_w, self.max_h, self.page);
        self.generation += 1;
    }
}

// --- Tracking hint ---------------------------------------------------------------------------------------------------

/// Head tracking is being reconnected after a USB drop: one small line where the pop-ups appear, held for as long as
/// that lasts. A pill would be the natural place, but a collapsed one is 14 px tall and a maximized desktop has none,
/// which is when a frozen view needs explaining most.
pub const TRACKING_HINT_FADE_IN_MS: f64 = 120.0;
pub const TRACKING_HINT_FADE_OUT_MS: f64 = 380.0;

/// The tracking hint's state. The C++ draws its card lazily when asked for it (or its generation); here the generation
/// bumps at the same points.
#[derive(Clone, Debug)]
pub struct TrackingHint {
    fade: CardFade,
    accent: [f32; 3],
    dirty: Cell<bool>,
    generation: Cell<u64>,
}

impl Default for TrackingHint {
    fn default() -> Self {
        Self {
            fade: CardFade::new(TRACKING_HINT_FADE_IN_MS, TRACKING_HINT_FADE_OUT_MS),
            accent: MD_DEFAULT_SEED,
            dirty: Cell::new(true),
            generation: Cell::new(0),
        }
    }
}

impl TrackingHint {
    /// Far from any StatusPopups generation: the two share one texture (GlCompositor::set_popup).
    pub const GENERATION_BASE: u64 = 1 << 62;

    pub fn new() -> Self {
        Self::default()
    }

    /// True when it starts or stops showing.
    pub fn update(&mut self, lost: bool, now_ms: f64) -> bool {
        self.fade.set(lost, now_ms)
    }

    /// The card's font changed: it is drawn again.
    pub fn font_changed(&mut self) {
        self.dirty.set(true);
    }

    /// True if the colour changed.
    pub fn set_accent(&mut self, accent: [f32; 3]) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        self.dirty.set(true);
        true
    }

    pub fn accent(&self) -> [f32; 3] {
        self.accent
    }

    pub fn alpha(&self, now_ms: f64) -> f32 {
        self.fade.level(now_ms)
    }

    pub fn animating(&self, now_ms: f64) -> bool {
        self.fade.animating(now_ms)
    }

    /// Bumps when the card changes.
    pub fn generation(&self) -> u64 {
        if self.dirty.get() {
            self.dirty.set(false);
            self.generation.set(self.generation.get() + 1);
        }
        Self::GENERATION_BASE + self.generation.get()
    }
}

// --- Placement panel state -------------------------------------------------------------------------------------------
//
// Placement state (supervisor-owned, ipc.hpp PlacementMenuPayload): which desktop's glass pill is expanded into its
// placement panel (`open`, `desktop`), the selected field, and every desktop's placement. The panel itself is drawn by
// glass; these are the field table and formatting it shares with the tests.

pub const PLACEMENT_MENU_FIELDS: usize = 4;
pub const PLACEMENT_MENU_DESKTOP_COUNT: usize = MAX_DESKTOPS;
/// `field` value: the background's Brightness row is selected.
pub const PLACEMENT_MENU_BRIGHTNESS: usize = PLACEMENT_MENU_FIELDS;

/// {x, y, distance, curvature} in the wire units of ipc.hpp WirePlacement (every desktop always faces you: no facing
/// field).
pub type MenuPlacement = [i32; PLACEMENT_MENU_FIELDS];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlacementMenuState {
    pub open: bool,
    /// 0..count-1.
    pub desktop: u32,
    /// 0..3 or [`PLACEMENT_MENU_BRIGHTNESS`] (out of range: drawn with nothing selected).
    pub field: u32,
    pub placements: [MenuPlacement; PLACEMENT_MENU_DESKTOP_COUNT],
    /// The panel's desktop row (Ultrawide / Add / Remove): how many desktops the run has, each one's output number (the
    /// n of XR-n, in capture order: a removed desktop leaves a gap), whether the open desktop is ultrawide, the Remove
    /// button armed (a second click removes), and a desktop change in progress (every button dimmed).
    pub count: u32,
    pub numbers: [u8; PLACEMENT_MENU_DESKTOP_COUNT],
    pub wide: bool,
    pub armed: bool,
    pub busy: bool,
    /// The panel's Scale dropdown: the open desktop's Hyprland output scale in percent (one of SCALE_PERCENTS; 0 =
    /// unknown: the box is drawn empty and dimmed), the steps it can take (bit i = SCALE_PERCENTS[i] divides its
    /// resolution exactly), and its list open.
    pub scale: u16,
    pub scales: u8,
    pub picker: bool,
    /// The Resolution dropdown above it: the open desktop's pixel size (0 x 0 = unknown: the box is drawn empty and
    /// dimmed) and its list open (never together with the Scale list).
    pub resolution_w: u16,
    pub resolution_h: u16,
    pub resolution_picker: bool,
    /// The Background dropdown and Brightness slider, the same in every desktop's panel: the background (index into
    /// [`BACKGROUNDS`]), its brightness in percent, and the dropdown's list open.
    pub background: u8,
    pub brightness: u16,
    pub background_picker: bool,
}

impl Default for PlacementMenuState {
    fn default() -> Self {
        Self {
            open: false,
            desktop: 0,
            field: 0,
            placements: [[0; PLACEMENT_MENU_FIELDS]; PLACEMENT_MENU_DESKTOP_COUNT],
            count: 3,
            numbers: [1, 2, 3, 4],
            wide: false,
            armed: false,
            busy: false,
            scale: 0,
            scales: 0,
            picker: false,
            resolution_w: 0,
            resolution_h: 0,
            resolution_picker: false,
            background: 0,
            brightness: 100,
            background_picker: false,
        }
    }
}

// Placement menu: field order matches ipc.hpp WirePlacement / geometry.py PLACEMENT_KEYS.
// lo/hi: the slider's ends (ipc.cpp kPlacementBounds); flat: the flat-default value the slider fills from. tenths: the
// value is in tenths of a degree (the orbit round you, tools/native_spike/orbit.py), shown as whole degrees.
struct MenuField {
    label: &'static str,
    hint: &'static str,
    percent: bool,
    is_signed: bool,
    tenths: bool,
    lo: i32,
    hi: i32,
    flat: i32,
}

const MENU_FIELDS: [MenuField; PLACEMENT_MENU_FIELDS] = [
    MenuField { label: "Left / right", hint: "", percent: false, is_signed: true, tenths: true, lo: -1800, hi: 1800, flat: 0 },
    MenuField { label: "Down / up", hint: "", percent: false, is_signed: true, tenths: true, lo: -900, hi: 900, flat: 0 },
    MenuField { label: "Distance", hint: "", percent: true, is_signed: false, tenths: false, lo: 10, hi: 200, flat: 100 },
    MenuField { label: "Curvature", hint: "", percent: false, is_signed: false, tenths: false, lo: 0, hi: 120, flat: 0 },
];

/// Slider range of each field, (lo, hi): the same bounds the renderer accepts (ipc.cpp kPlacementBounds, tested).
pub fn placement_menu_bounds() -> [(i32, i32); PLACEMENT_MENU_FIELDS] {
    MENU_FIELDS.map(|f| (f.lo, f.hi))
}

/// The flat (default) value of each field: sliders fill from it to the current value.
pub fn placement_menu_flat() -> MenuPlacement {
    MENU_FIELDS.map(|f| f.flat)
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PlacementMenuRow {
    pub label: String,
    pub hint: String,
    pub value: String,
    pub selected: bool,
}

/// Label, plain-English hint and formatted value ("100%", "60°"; " deg" with the pixel font, which is ASCII-only) for
/// each field of the selected desktop (out of range: the last). The orbit's x / y (tenths of a degree) show rounded to
/// whole degrees, half away from zero, so they fit the value column: "+13°", "-180°", "0°" ("+13deg" with the pixel
/// font).
pub fn placement_menu_rows(state: &PlacementMenuState, ascii_units: bool) -> Vec<PlacementMenuRow> {
    let p = &state.placements[(state.desktop as usize).min(PLACEMENT_MENU_DESKTOP_COUNT - 1)];
    MENU_FIELDS
        .iter()
        .enumerate()
        .map(|(i, f)| {
            let v = p[i];
            let value = if f.tenths {
                // Whole degrees, rounded half away from zero ("+13°", "-180°"): "-179.9°" would not fit the value column
                // (kPanelW - kPanelPad - kPanelTrackX1) with the system font, nor "+180 deg" with the pixel font.
                // ((v < 0 ? -v : v) + 5) / 10 in int: -v wraps for i32::MIN, as the C++ (UB there) does on x86.
                let whole = (if v < 0 { v.wrapping_neg() } else { v }).wrapping_add(5) / 10;
                let sign = if whole == 0 {
                    ""
                } else if v < 0 {
                    "-"
                } else if f.is_signed {
                    "+"
                } else {
                    ""
                };
                format!("{sign}{whole}{}", if ascii_units { "deg" } else { "\u{b0}" })
            } else {
                let sign = if f.is_signed && v > 0 { "+" } else { "" };
                // U+00B0 DEGREE SIGN
                let unit = if f.percent {
                    "%"
                } else if ascii_units {
                    " deg"
                } else {
                    "\u{b0}"
                };
                format!("{sign}{v}{unit}")
            };
            PlacementMenuRow {
                label: f.label.into(),
                hint: f.hint.into(),
                value,
                selected: state.field as usize == i,
            }
        })
        .collect()
}

// --- Backgrounds (environment.hpp) -------------------------------------------------------------------------------------
//
// The background behind the desktops and behind a VR video: an optional 360° environment, chosen in the placement
// panel's Background dropdown or the VR video's quality menu. BACKGROUNDS lists them; None draws nothing. Adding one: a
// row here (name on the wire, label in the menus), the same row in environment.py BACKGROUNDS (a test compares them)
// and a scene in the GL pass.

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(u8)]
pub enum Background {
    #[default]
    None = 0,
    Grid = 1,
    Smoke = 2,
}

/// A background's wire name (ipc.hpp EnvironmentPayload) and menu label.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct BackgroundInfo {
    pub name: &'static str,
    pub label: &'static str,
}

pub const BACKGROUNDS: [BackgroundInfo; 3] = [
    BackgroundInfo { name: "none", label: "None" },
    BackgroundInfo { name: "grid", label: "Grid" },
    BackgroundInfo { name: "smoke", label: "Smoke" },
];
pub const BACKGROUND_COUNT: usize = BACKGROUNDS.len();

/// Index into [`BACKGROUNDS`] of wire name `name`, [`BACKGROUND_COUNT`] if there is no such background.
pub fn background_index(name: &str) -> usize {
    BACKGROUNDS.iter().position(|b| b.name == name).unwrap_or(BACKGROUND_COUNT)
}

/// Brightness in percent: it scales the background before the cap, so 100 is the scene as designed, below it dims
/// evenly, and above it lifts the dim parts while the brightest stay on the cap. Black stays black. Mirrors
/// environment.py BRIGHTNESS_BOUNDS and DEFAULT_BRIGHTNESS.
pub const BRIGHTNESS_MIN: u16 = 10;
pub const BRIGHTNESS_MAX: u16 = 200;
pub const BRIGHTNESS_DEFAULT: u16 = 100;
const _: () = assert!(BRIGHTNESS_DEFAULT == 100, "a panel with no brightness sent shows the default");
