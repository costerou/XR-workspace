//! The VR video controls card (`spike/include/xr/video.hpp` `VideoCardState` / `render_video_card`, drawn in
//! `spike/src/hud.cpp`): the help card's parts, so the OLED dimming cannot drift from it. Title, state and time, M3's
//! linear progress indicator, the format, an optional error line, then the key hints and the idle note; while the
//! quality menu is open, its rows instead of the format and the hints.

use super::math::{Format, State};
use crate::glass_theme::glass_theme_scheme;
use crate::hud::{
    CARD_DIM, CARD_TEXT, COLUMN_GAP, HELP_PAD, HudImage, HudLine, HudRole, HudTextMetrics, OVERLAY_IDLE_CLOSE_S,
    PROGRESS_H, ROW_GAP, SECTION_GAP, TextCell, allocate_card, card_size, draw_cell, draw_centered, draw_divider,
    hud_line, text_cell,
};
use crate::material::{MD_DEFAULT_SEED, clampf};
use crate::material_draw::md_fill_rounded;

/// A quality menu row: label and value from the supervisor (printable ASCII).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MenuRow {
    pub label: String,
    pub value: String,
}

/// What the controls card shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VideoCardState {
    pub title: String,
    pub format: Format,
    pub state: State,
    /// Whole seconds: the card re-renders at most once a second.
    pub position_s: u32,
    pub duration_s: u32,
    /// Error text (printable ASCII), empty otherwise.
    pub detail: String,
    /// The quality menu (Q): when open the card shows `menu_rows`, `menu_selected` highlighted, instead of its body.
    pub menu: bool,
    pub menu_rows: Vec<MenuRow>,
    pub menu_selected: u32,
}

impl Default for VideoCardState {
    fn default() -> Self {
        Self {
            title: String::new(),
            format: Format::default(),
            state: State::Loading,
            position_s: 0,
            duration_s: 0,
            detail: String::new(),
            menu: false,
            menu_rows: Vec::new(),
            menu_selected: 0,
        }
    }
}

/// "0:07", "4:05", "1:02:03"; negative or non-finite: "0:00".
pub fn format_clock(seconds: f64) -> String {
    let seconds = if !seconds.is_finite() || seconds < 0.0 { 0.0 } else { seconds };
    let total = seconds as u64;
    let (h, m, s) = (total / 3600, (total / 60) % 60, total % 60);
    if h != 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") }
}

/// Key hints on the card's footer (shared with the help text in docs/vr-video.md); `ascii` for the pixel font.
pub fn video_card_hints(ascii: bool) -> Vec<String> {
    [
        "Space  play / pause",
        if ascii { "Left/Right  10 s" } else { "←/→  10 s" },
        if ascii { "Shift+Left/Right  60 s" } else { "Shift+←/→  60 s" },
        "Tab  format",
        "Q  quality",
        "Ctrl+Alt+Space  recenter",
        "Esc  back to desktops",
    ]
    .map(String::from)
    .to_vec()
}

/// The hints while the quality menu is open.
pub fn video_menu_hints(ascii: bool) -> Vec<String> {
    [
        if ascii { "Up/Down  setting" } else { "↑/↓  setting" },
        if ascii { "Left/Right  change" } else { "←/→  change" },
        "Q / Esc  close",
        "Space  play / pause",
        "Changes apply now and are saved.",
    ]
    .map(String::from)
    .to_vec()
}

/// Between hints on one footer line.
const HINT_GAP: &str = "     ";

fn card_title(text: &str) -> HudLine {
    hud_line(text, 2, CARD_TEXT, true, 0, HudRole::OnSurface)
}
fn card_key(text: &str) -> HudLine {
    hud_line(text, 2, CARD_TEXT, true, 0, HudRole::OnSurface) // left column: bold
}
fn card_detail(text: &str) -> HudLine {
    hud_line(text, 2, CARD_DIM, false, 0, HudRole::OnSurfaceVariant) // descriptions
}
fn card_footer(text: &str) -> HudLine {
    hud_line(text, 1, CARD_DIM, false, 0, HudRole::Outline) // key hints: small
}

fn row_ascent(a: &TextCell, b: &TextCell) -> u32 {
    a.b.ascent.max(b.b.ascent)
}
fn row_descent(a: &TextCell, b: &TextCell) -> u32 {
    a.b.descent.max(b.b.descent)
}

/// The controls card for `v` in the Material scheme of `accent` (the Omarchy theme colour; all zero: the default seed),
/// in `font` or (None) the pixel font.
pub fn render_video_card(v: &VideoCardState, accent: [f32; 3], font: Option<&dyn HudTextMetrics>) -> HudImage {
    let accent = if accent == [0.0; 3] { MD_DEFAULT_SEED } else { accent }; // no theme colour given
    let scheme = glass_theme_scheme(accent);
    let ascii = font.is_none();
    let state = match v.state {
        State::Playing => "Playing",
        State::Paused => "Paused",
        State::Ended => "Ended",
        State::Error => "Cannot play this video",
        State::Off | State::Loading => "Loading",
    };
    let duration = if v.duration_s != 0 { format_clock(f64::from(v.duration_s)) } else { "--:--".to_string() };
    let time = format!("{} / {duration}", format_clock(f64::from(v.position_s)));
    let cell = |line: HudLine| text_cell(line, font);
    let title = cell(card_title(if v.title.is_empty() { "VR video" } else { &v.title }));
    let status = cell(card_key(state));
    let clock = cell(card_detail(&time));
    let format = cell(card_detail(&v.format.label(ascii)));
    let detail = cell(card_detail(&v.detail));
    // The quality menu (Q) replaces the format line and the key hints: one row per setting, the selected one on M3's
    // active indicator (a fully rounded secondary-container band, its text on-secondary-container) with its value
    // between arrows.
    let mut menu: Vec<(TextCell, TextCell)> = Vec::new();
    if v.menu {
        let on_band = |mut l: HudLine| {
            l.role = HudRole::OnSecondaryContainer;
            l
        };
        for (i, r) in v.menu_rows.iter().enumerate() {
            let sel = i == v.menu_selected as usize;
            menu.push(if sel {
                (cell(on_band(card_key(&r.label))), cell(on_band(card_key(&format!("< {} >", r.value)))))
            } else {
                (cell(card_detail(&r.label)), cell(card_detail(&r.value)))
            });
        }
    }
    let mut hints: Vec<TextCell> = Vec::new();
    if v.menu {
        let all = video_menu_hints(ascii); // keys two per line, then the closing note
        let mut i = 0;
        while i + 2 < all.len() {
            hints.push(cell(card_footer(&format!("{}{HINT_GAP}{}", all[i], all[i + 1]))));
            i += 2;
        }
        hints.push(cell(card_footer(all.last().expect("menu hints"))));
    } else {
        let all = video_card_hints(ascii);
        let mut line = String::new();
        for (i, h) in all.iter().enumerate() {
            if !line.is_empty() {
                line += HINT_GAP;
            }
            line += h;
            if i % 3 == 2 || i + 1 == all.len() {
                hints.push(cell(card_footer(&line)));
                line.clear();
            }
        }
        hints.push(cell(card_footer(&format!("Closes after {OVERLAY_IDLE_CLOSE_S} s idle. Any video key shows it."))));
    }
    let (row_gap, section_gap, column_gap) = (ROW_GAP as u32, SECTION_GAP as u32, COLUMN_GAP as u32);
    let bar_h = PROGRESS_H;
    let status_w = status.b.width + column_gap + clock.b.width;
    let mut content_w = title.b.width.max(status_w).max(format.b.width).max(detail.b.width).max(560);
    for h in &hints {
        content_w = content_w.max(h.b.width);
    }
    for (l, val) in &menu {
        content_w = content_w.max(l.b.width + column_gap + val.b.width);
    }
    let status_h = row_ascent(&status, &clock) + row_descent(&status, &clock);
    let menu_row_h = |(l, val): &(TextCell, TextCell)| row_ascent(l, val) + row_descent(l, val) + row_gap;
    let mut body_h = format.b.height();
    if v.menu {
        body_h = menu.iter().map(menu_row_h).sum();
    }
    let mut content_h = title.b.height() + section_gap + status_h + row_gap + bar_h + row_gap + body_h + section_gap;
    if !v.detail.is_empty() {
        content_h += detail.b.height() + row_gap;
    }
    for (i, h) in hints.iter().enumerate() {
        content_h += h.b.height() + if i + 1 < hints.len() { row_gap / 2 } else { 0 };
    }
    let mut im = allocate_card(card_size(content_w), card_size(content_h), &scheme);
    let left = HELP_PAD as i32;
    let mut top = HELP_PAD as u32;
    draw_centered(&mut im, &title, &scheme, top, font);
    top += title.b.height() + section_gap;
    draw_divider(&mut im, top, &scheme);
    let ascent = row_ascent(&status, &clock);
    draw_cell(&mut im, &status, &scheme, left, top, ascent, font);
    draw_cell(&mut im, &clock, &scheme, left.wrapping_add(content_w.wrapping_sub(clock.b.width) as i32), top, ascent, font);
    top += status_h + row_gap;
    // Progress: M3's linear indicator, the primary up to the position over a secondary-container track, fully rounded.
    let progress =
        if v.duration_s != 0 { clampf(v.position_s as f32 / v.duration_s as f32, 0.0, 1.0) } else { 0.0 };
    let filled = (progress * content_w as f32).round() as u32;
    let (bar_top, bar_bottom, r) = (top as f32, (top + bar_h) as f32, bar_h as f32 / 2.0);
    let x0 = left as f32;
    md_fill_rounded(&mut im, x0, bar_top, x0 + content_w as f32, bar_bottom, r, scheme.secondary_container, 1.0);
    if filled != 0 {
        md_fill_rounded(&mut im, x0, bar_top, x0 + filled.max(bar_h) as f32, bar_bottom, r, scheme.primary, 1.0);
    }
    top += bar_h + row_gap;
    if v.menu {
        for (i, m) in menu.iter().enumerate() {
            let h = menu_row_h(m);
            let a = row_ascent(&m.0, &m.1);
            if i == v.menu_selected as usize {
                let bx = left as f32 - ROW_GAP / 2.0;
                let by = top as f32 - ROW_GAP / 2.0;
                let (bx1, by1) = (bx + content_w as f32 + ROW_GAP, by + h as f32);
                md_fill_rounded(&mut im, bx, by, bx1, by1, h as f32 / 2.0, scheme.secondary_container, 1.0);
            }
            draw_cell(&mut im, &m.0, &scheme, left, top, a, font);
            let value_left = left.wrapping_add(content_w.wrapping_sub(m.1.b.width) as i32);
            draw_cell(&mut im, &m.1, &scheme, value_left, top, a, font);
            top += h;
        }
    } else {
        draw_cell(&mut im, &format, &scheme, left, top, format.b.ascent, font);
        top += format.b.height();
    }
    if !v.detail.is_empty() {
        top += row_gap;
        draw_cell(&mut im, &detail, &scheme, left, top, detail.b.ascent, font);
        top += detail.b.height();
    }
    top += section_gap;
    draw_divider(&mut im, top, &scheme);
    for h in &hints {
        draw_centered(&mut im, h, &scheme, top, font);
        top += h.b.height() + row_gap / 2;
    }
    im
}
