//! Key bind panel logic (`spike/include/xr/binds_panel.hpp`, `spike/src/binds_panel.cpp`) without the rasterisation:
//! the panel's state, its fit for the output, the text steps and card chrome, the scroll window, the text cut to a
//! column, and the overlay's fade, scroll and redraw bookkeeping.
//!
//! The panel (Ctrl+Alt+K by default; tools/native_spike/keybinds.py) is a head-locked Material Design 3 card listing
//! every XR Workspace bind with its keys, display only (the supervisor owns the state, the keyboard and the binds).
//!
//! Look. An extra-large rounded surface container (high) with a 1 px outline-variant edge, like the help card, in the M3
//! scheme of the Omarchy theme colour (`material`, `glass_theme::glass_theme_scheme`), every role under the OLED cap
//! (`hud::CARD_MAX_LEVEL`):
//!   * a headline ("Key binds") with the picked row's place ("7 / 25") on the right;
//!   * one M3 list item per bind: its action, and its keys in a chip at the right (outlined; filled primary-container
//!     when the keys are not the default). The picked row is on a secondary-container band; a row with a conflict is on
//!     an error-container band with its note (what it conflicts with) as a second line; the row being changed shows
//!     "Press keys..." in a primary-outlined chip, like a focused text field;
//!   * "N more" above / below when rows are scrolled out, a divider, the key hint and a status line (on-error-container
//!     when it is an alert).
//!
//! Fit. The card takes the roomiest text step ([`BIND_STYLES`]) at which [`BIND_PANEL_SLOTS`] rows (fewer if the panel
//! has fewer) fit the output with `HUD_FIT_MARGIN` free on every side; at the smallest it shows fewer rows, at least
//! [`BIND_PANEL_MIN_SLOTS`]. The rows scroll: the window keeps the picked row in view and moves only when it would leave
//! it ([`bind_panel_first`]). Its height counts one note line for each conflict, up to one per row shown, so it changes
//! only when conflicts do.
//!
//! All `u32` sums wrap like the C++ unsigned maths.

use crate::hud::{
    CardFade, EM_PER_SCALE, GLYPH_H, HELP_FADE_IN_MS, HELP_FADE_OUT_MS, HELP_RISE_PX, HUD_FIT_MARGIN, HudTextMetrics,
    LineBox, PIXEL_FONT_ZOOM, hud_text_width,
};
use crate::material::{MD_DEFAULT_SEED, MdRgb};

// --- State ---------------------------------------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum BindRowState {
    #[default]
    Normal,
    Custom,
    Conflict,
    Capture,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct BindRow {
    pub action: String,
    pub keys: String,
    /// What the keys conflict with (empty: nothing).
    pub note: String,
    pub state: BindRowState,
}

impl BindRow {
    pub fn new(
        action: impl Into<String>,
        keys: impl Into<String>,
        note: impl Into<String>,
        state: BindRowState,
    ) -> Self {
        Self { action: action.into(), keys: keys.into(), note: note.into(), state }
    }

    /// A conflict with a note: the row gets a second line.
    pub fn has_note(&self) -> bool {
        self.state == BindRowState::Conflict && !self.note.is_empty()
    }
}

/// Limits of the `binds` control op's payload (ipc): `{"visible":false}`, or `{"visible":true,"title":t,
/// "rows":[{"action":a,"keys":k,"note":n,"state":"normal"|"custom"|"conflict"|"capture"},...],"selected":i,"hint":h,
/// "status":s,"alert":b}`. title, action and keys 1..MAX_BIND_LABEL printable ASCII characters, hint 1..MAX_BIND_TEXT,
/// note and status 0..MAX_BIND_TEXT; 1..MAX_BIND_ROWS rows; selected < rows. Malformed: rejected and dropped.
pub const MAX_BIND_ROWS: usize = 64;
pub const MAX_BIND_LABEL: usize = 48;
pub const MAX_BIND_TEXT: usize = 96;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct BindPanelState {
    pub visible: bool,
    pub title: String,
    pub hint: String,
    pub status: String,
    pub rows: Vec<BindRow>,
    pub selected: u32,
    pub alert: bool,
}

// --- Fit -----------------------------------------------------------------------------------------------------------

/// Rows shown at once.
pub const BIND_PANEL_SLOTS: u32 = 10;
pub const BIND_PANEL_MIN_SLOTS: u32 = 3;
/// The columns' width, in characters (monospace).
pub const BIND_ACTION_CHARS: u32 = 34;
pub const BIND_KEYS_CHARS: u32 = 20;
/// Row text em size, roomiest / smallest.
pub const BIND_ROW_PX_MAX: u32 = 26;
pub const BIND_ROW_PX_MIN: u32 = 18;

/// One text step: headline, row, note (a conflicting row's second line), hint / status, and spacing.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct BindStyle {
    pub title_px: u32,
    pub row_px: u32,
    pub note_px: u32,
    pub hint_px: u32,
    pub row_pad: u32,
    pub gap: u32,
}

const fn style(title_px: u32, row_px: u32, note_px: u32, hint_px: u32, row_pad: u32, gap: u32) -> BindStyle {
    BindStyle { title_px, row_px, note_px, hint_px, row_pad, gap }
}

/// Text steps, roomiest first.
pub const BIND_STYLES: [BindStyle; 5] = [
    style(30, 26, 19, 18, 7, 14),
    style(28, 24, 18, 17, 6, 13),
    style(26, 22, 17, 16, 6, 12),
    style(24, 20, 16, 15, 5, 11),
    style(22, 18, 15, 14, 4, 10),
];
const _: () = assert!(
    BIND_STYLES[0].row_px == BIND_ROW_PX_MAX && BIND_STYLES[BIND_STYLES.len() - 1].row_px == BIND_ROW_PX_MIN
);

/// Card chrome (output px; the card is drawn 1:1): padding, a row's inner inset, a chip's side padding and its vertical
/// padding, the space between rows; the help card's background opacity and divider.
pub const BIND_PAD: f32 = 32.0;
pub const BIND_INSET: f32 = 16.0;
pub const BIND_CHIP_PAD: f32 = 12.0;
pub const BIND_ROW_SPACING: f32 = 4.0;
pub const BIND_CHIP_PAD_V: u32 = 4;
pub const BIND_BACKGROUND_ALPHA: f32 = 0.9;
pub const BIND_DIVIDER_ALPHA: f32 = 0.8;

/// The card's size, its row text size and how many rows it shows.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct BindPanelFit {
    pub width: u32,
    pub height: u32,
    pub row_px: u32,
    pub slots: u32,
    /// False only if even the smallest text with BIND_PANEL_MIN_SLOTS rows does not fit.
    pub fits: bool,
}

/// The built-in pixel font's zoom for an em size, as hud draws it: the nearest whole zoom, at least 1.
pub fn bind_text_zoom(px: u32) -> u32 {
    (px.wrapping_mul(PIXEL_FONT_ZOOM).wrapping_add(EM_PER_SCALE / 2) / EM_PER_SCALE).max(1)
}

/// `s`'s box in `font`, or in the pixel font without one.
pub fn bind_measure(font: Option<&dyn HudTextMetrics>, s: &str, px: u32, bold: bool) -> LineBox {
    let Some(font) = font else {
        let z = bind_text_zoom(px);
        return LineBox { width: hud_text_width(s, z), ascent: GLYPH_H.wrapping_mul(z), descent: 0 };
    };
    let m = font.metrics(px, bold);
    LineBox { width: font.measure(s, px, bold), ascent: m.ascent, descent: m.descent }
}

/// A line box with no width.
pub fn bind_line(font: Option<&dyn HudTextMetrics>, px: u32, bold: bool) -> LineBox {
    LineBox { width: 0, ..bind_measure(font, "", px, bold) }
}

/// `s`, or as much of it as fits `max_w` with "..." after it. The C++ drops a byte at a time; the payload is printable
/// ASCII, so dropping a char is the same.
pub fn bind_fit_text(font: Option<&dyn HudTextMetrics>, s: &str, px: u32, bold: bool, max_w: u32) -> String {
    if bind_measure(font, s, px, bold).width <= max_w {
        return s.to_string();
    }
    let mut t = s.to_string();
    while !t.is_empty() && bind_measure(font, &(t.clone() + "..."), px, bold).width > max_w {
        t.pop();
    }
    while t.ends_with(' ') {
        t.pop();
    }
    t + "..."
}

/// The card's layout at one text step: what the fit and the drawing share.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct BindPanelLayout {
    pub style: BindStyle,
    /// Rows shown.
    pub slots: u32,
    /// Note lines the height counts.
    pub notes: u32,
    pub width: u32,
    pub height: u32,
    /// Line boxes.
    pub title: LineBox,
    pub row: LineBox,
    pub note: LineBox,
    pub hint: LineBox,
    /// The columns (keys: the widest chip).
    pub action_w: u32,
    pub keys_w: u32,
    pub chip_h: u32,
    pub row_h: u32,
    pub note_h: u32,
    pub list_h: u32,
}

impl BindPanelLayout {
    /// With HUD_FIT_MARGIN free on every side of `max_w` x `max_h` (0 = unbounded).
    pub fn fits(&self, max_w: u32, max_h: u32) -> bool {
        (max_w == 0 || self.width.wrapping_add(2 * HUD_FIT_MARGIN) <= max_w)
            && (max_h == 0 || self.height.wrapping_add(2 * HUD_FIT_MARGIN) <= max_h)
    }
}

/// The layout of `state` at text step `st` with `slots` rows shown.
pub fn bind_panel_layout_at(
    state: &BindPanelState,
    font: Option<&dyn HudTextMetrics>,
    st: BindStyle,
    slots: u32,
) -> BindPanelLayout {
    let mut l = BindPanelLayout {
        style: st,
        slots,
        title: bind_line(font, st.title_px, true),
        row: bind_line(font, st.row_px, false),
        note: bind_line(font, st.note_px, false),
        hint: bind_line(font, st.hint_px, false),
        ..BindPanelLayout::default()
    };
    l.action_w = bind_measure(font, &"M".repeat(BIND_ACTION_CHARS as usize), st.row_px, false).width;
    l.keys_w = bind_measure(font, &"M".repeat(BIND_KEYS_CHARS as usize), st.row_px, true)
        .width
        .wrapping_add(2 * BIND_CHIP_PAD as u32);
    l.chip_h = l.row.height().max(bind_line(font, st.row_px, true).height()).wrapping_add(2 * BIND_CHIP_PAD_V);
    l.row_h = l.chip_h.wrapping_add(2u32.wrapping_mul(st.row_pad));
    l.note_h = l.note.height().wrapping_add(st.row_pad);
    let conflicts = state.rows.iter().filter(|r| r.has_note()).count() as u32;
    l.notes = slots.min(conflicts);
    l.list_h = slots
        .wrapping_mul(l.row_h)
        .wrapping_add((if slots != 0 { slots - 1 } else { 0 }).wrapping_mul(BIND_ROW_SPACING as u32))
        .wrapping_add(l.notes.wrapping_mul(l.note_h));
    l.width = (2 * (BIND_PAD + BIND_INSET) as u32).wrapping_add(l.action_w).wrapping_add(st.gap).wrapping_add(l.keys_w);
    let hint_h = l.hint.height();
    l.height = (2 * BIND_PAD as u32)
        .wrapping_add(l.title.height())
        .wrapping_add(2u32.wrapping_mul(st.gap)) // headline, divider
        .wrapping_add(2u32.wrapping_mul(hint_h.wrapping_add(st.gap / 2)))
        .wrapping_add(l.list_h) // "N more" above and below the rows
        .wrapping_add(2u32.wrapping_mul(st.gap))
        .wrapping_add(hint_h)
        .wrapping_add(st.gap / 2)
        .wrapping_add(hint_h); // divider, hint, status
    l
}

/// The layout the card takes for an output of `max_width` x `max_height` (0 = unbounded): the roomiest step that fits
/// every wanted row, else the smallest with fewer rows, at least BIND_PANEL_MIN_SLOTS (fits or not).
pub fn bind_panel_layout(
    state: &BindPanelState,
    font: Option<&dyn HudTextMetrics>,
    max_width: u32,
    max_height: u32,
) -> BindPanelLayout {
    let want = (state.rows.len() as u32).clamp(1, BIND_PANEL_SLOTS);
    for st in BIND_STYLES {
        let l = bind_panel_layout_at(state, font, st, want);
        if l.fits(max_width, max_height) {
            return l;
        }
    }
    let smallest = BIND_STYLES[BIND_STYLES.len() - 1];
    let mut n = want;
    while n > BIND_PANEL_MIN_SLOTS {
        n -= 1;
        let l = bind_panel_layout_at(state, font, smallest, n);
        if l.fits(max_width, max_height) {
            return l;
        }
    }
    bind_panel_layout_at(state, font, smallest, want.min(BIND_PANEL_MIN_SLOTS))
}

/// The card's fit for an output of `max_width` x `max_height` (0 = unbounded), in `font` or (None) the pixel font.
pub fn bind_panel_fit(
    state: &BindPanelState,
    font: Option<&dyn HudTextMetrics>,
    max_width: u32,
    max_height: u32,
) -> BindPanelFit {
    let l = bind_panel_layout(state, font, max_width, max_height);
    BindPanelFit {
        width: l.width,
        height: l.height,
        row_px: l.style.row_px,
        slots: l.slots,
        fits: l.fits(max_width, max_height),
    }
}

/// The first row of the window of `slots` rows: `previous` moved only as far as keeps `selected` in it, within the
/// rows.
pub fn bind_panel_first(previous: u32, selected: u32, rows: u32, slots: u32) -> u32 {
    if slots == 0 || rows <= slots {
        return 0;
    }
    let mut first = previous.min(rows - slots);
    if selected < first {
        first = selected;
    } else if selected >= first.wrapping_add(slots) {
        first = selected - slots + 1;
    }
    first.min(rows - slots)
}

/// The headline's right side: the picked row's place ("7 / 25"; empty with no rows). A selection past the rows shows
/// the last.
pub fn bind_panel_place(state: &BindPanelState) -> String {
    let rows = state.rows.len() as u32;
    if rows == 0 {
        return String::new();
    }
    format!("{} / {}", state.selected.min(rows - 1).wrapping_add(1), rows)
}

// --- Overlay -------------------------------------------------------------------------------------------------------

/// The panel as the renderer shows it: fades like the help card (CardFade, HELP_FADE_IN_MS / HELP_FADE_OUT_MS, rising
/// HELP_RISE_PX), keeps its content while fading out, and scrolls with the picked row. The C++ also rasterises the
/// card; here the generation bumps exactly where the C++ draws a new one, and [`BindPanelOverlay::fit`] is that card's
/// size.
pub struct BindPanelOverlay {
    fade: CardFade,
    /// The content shown last (kept while fading out).
    state: BindPanelState,
    max_w: u32,
    max_h: u32,
    first: u32,
    slots: u32,
    generation: u64,
    shown: u64,
    hidden: u64,
    fit: BindPanelFit,
    accent: MdRgb,
    font: Option<Box<dyn HudTextMetrics>>,
}

impl Default for BindPanelOverlay {
    fn default() -> Self {
        Self {
            fade: CardFade::new(HELP_FADE_IN_MS, HELP_FADE_OUT_MS),
            state: BindPanelState::default(),
            max_w: 0,
            max_h: 0,
            first: 0,
            slots: 0,
            generation: 0,
            shown: 0,
            hidden: 0,
            fit: BindPanelFit::default(),
            accent: MD_DEFAULT_SEED,
            font: None,
        }
    }
}

impl BindPanelOverlay {
    pub fn new() -> Self {
        Self::default()
    }

    /// True if visibility or what it shows changed.
    pub fn set(&mut self, state: &BindPanelState, now_ms: f64) -> bool {
        let mut redraw = false;
        // Hidden: the last content stays while it fades out.
        if state.visible && (self.generation == 0 || *state != self.state) {
            self.state = state.clone();
            self.render();
            redraw = true;
        }
        if !self.fade.set(state.visible, now_ms) {
            return redraw;
        }
        if state.visible {
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

    /// Re-renders a rendered card (true).
    pub fn set_accent(&mut self, accent: MdRgb) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        if self.generation == 0 {
            return false;
        }
        self.render();
        true
    }

    pub fn accent(&self) -> MdRgb {
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

    /// The content shown last.
    pub fn state(&self) -> &BindPanelState {
        &self.state
    }

    /// The last rendered card's fit (its size: the C++ image's width and height).
    pub fn fit(&self) -> BindPanelFit {
        self.fit
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }
    pub fn first(&self) -> u32 {
        self.first
    }
    pub fn slots(&self) -> u32 {
        self.slots
    }
    pub fn shown_count(&self) -> u64 {
        self.shown
    }
    pub fn hidden_count(&self) -> u64 {
        self.hidden
    }

    // The fit and the scroll window for the viewport; bumps the generation.
    fn render(&mut self) {
        self.fit = bind_panel_fit(&self.state, self.font.as_deref(), self.max_w, self.max_h);
        self.slots = self.fit.slots;
        self.first = bind_panel_first(self.first, self.state.selected, self.state.rows.len() as u32, self.slots);
        self.generation += 1;
    }
}
