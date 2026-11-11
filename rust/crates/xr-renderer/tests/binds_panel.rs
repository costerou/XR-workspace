//! `spike/tests/binds_panel_test.cpp` in Rust: the fit, the scroll window and the overlay, the raster checks on the
//! rendered card (in the pixel font and, when fontconfig finds it, the installed font), plus a dump compared with the
//! C++ one byte for byte. Pixel parity with the C++ card is in `tests/card_raster.rs`.

use xr_renderer::binds_panel::*;
use xr_renderer::glass_theme::glass_theme_scheme;
use xr_renderer::hud::{
    CARD_MAX_LEVEL, FontMetrics, HELP_FADE_IN_MS, HELP_FADE_OUT_MS, HELP_RISE_PX, HUD_FIT_MARGIN, HUD_FONT_FAMILY, HudImage,
    HudTextMetrics,
};
use xr_renderer::hud_font::HudFont;
use xr_renderer::material::MdRgb;

/// 1920x1200 at scale 1.25, logical px.
const SURFACE_W: u32 = 1536;
const SURFACE_H: u32 = 960;
const SCALES: [f32; 6] = [1.0, 1.25, 1.5, 1.6, 1.75, 2.0];

/// A monospace stand-in for the installed font (the C++ test's second font): em-tall lines, 0.6 em advances.
struct Mono;

impl HudTextMetrics for Mono {
    fn metrics(&self, px: u32, _bold: bool) -> FontMetrics {
        FontMetrics { ascent: px * 3 / 4, descent: px / 4 }
    }
    fn measure(&self, utf8: &str, px: u32, bold: bool) -> u32 {
        utf8.chars().count() as u32 * (px * 3 / 5 + u32::from(bold))
    }
    fn draw(&self, _: &str, _: u32, _: bool, _: i32, _: i32, _: &mut dyn FnMut(i32, i32, f32)) {}
}

/// The panel as keybinds.py sends it at a start with two conflicts, one custom bind, row 4 picked.
fn sample(selected: u32, capture: bool, conflicts: bool) -> BindPanelState {
    let actions = [
        "Maximize the selected desktop",
        "Overview",
        "Previous desktop",
        "Next desktop",
        "Less space between desktops",
        "More space between desktops",
        "Recenter the view",
        "Maximized: follow / locked",
        "Toggle pose prediction",
        "Typing mode: XR keys off / on",
        "Privacy: main display off / on",
        "Placement pill of the selected desktop",
        "Play a 180/360 VR video",
        "Play the browser video you copied",
        "VR video: hide / show playback controls",
        "Help card (every key)",
        "Previous help page",
        "Next help page",
        "Key binds (this panel)",
        "Send window to the selected desktop",
        "Send window to XR-1",
        "Send window to XR-2",
        "Send window to XR-3",
        "Send window to XR-4",
        "Send window back to the main display",
    ];
    let keys = [
        "Ctrl+Alt+Up",
        "Ctrl+Alt+Down",
        "Ctrl+Alt+Left",
        "Ctrl+Alt+Right",
        "Ctrl+Alt+[",
        "Ctrl+Alt+]",
        "Ctrl+Alt+Space",
        "Ctrl+Alt+V",
        "Ctrl+Alt+P",
        "Ctrl+Alt+T",
        "Ctrl+Alt+D",
        "Ctrl+Alt+L",
        "Ctrl+Alt+O",
        "Ctrl+Alt+U",
        "Ctrl+Alt+C",
        "Ctrl+Alt+H",
        "Ctrl+Alt+PgUp",
        "Ctrl+Alt+PgDn",
        "Ctrl+Alt+K",
        "Ctrl+Alt+G",
        "Ctrl+Alt+1",
        "Ctrl+Alt+2",
        "Ctrl+Alt+3",
        "Ctrl+Alt+4",
        "Ctrl+Alt+Shift+Super+bracketright",
    ];
    let mut s = BindPanelState {
        visible: true,
        title: "Key binds".into(),
        hint: "Up/Down pick   Enter change   R reset   Shift+R reset all   Esc close".into(),
        status: if conflicts { "2 binds with conflicts (marked). Change them, or R to reset." } else { "No conflicts." }
            .into(),
        alert: conflicts,
        ..BindPanelState::default()
    };
    s.rows = actions.iter().zip(keys).map(|(a, k)| BindRow::new(*a, k, "", BindRowState::Normal)).collect();
    s.rows[7].state = BindRowState::Custom;
    if conflicts {
        s.rows[3] = BindRow::new(
            "Next desktop",
            "Ctrl+Alt+Delete",
            "Off: Hyprland binds Ctrl+Alt+Delete to \"Close all windows\"",
            BindRowState::Conflict,
        );
        s.rows[19] = BindRow::new(
            "Send window to the selected desktop",
            "Ctrl+Alt+Tab",
            "Off: Hyprland binds Ctrl+Alt+Tab to \"Focus on next monitor\", and this note is long enough to be cut",
            BindRowState::Conflict,
        );
    }
    s.selected = selected;
    if capture {
        let r = &mut s.rows[selected as usize];
        r.state = BindRowState::Capture;
        r.keys = "Press keys...".into();
    }
    s
}

/// The C++ check_fit on the card's size: HUD_FIT_MARGIN free on every side, and room for the fade-in drop.
fn card_fits(fit: &BindPanelFit, w: u32, h: u32) -> bool {
    fit.width + 2 * HUD_FIT_MARGIN <= w && fit.height + 2 * HUD_FIT_MARGIN <= h && fit.height + HELP_RISE_PX as u32 <= h
}

#[test]
fn scroll_window() {
    assert_eq!(bind_panel_first(0, 0, 25, 10), 0);
    assert_eq!(bind_panel_first(0, 9, 25, 10), 0);
    assert_eq!(bind_panel_first(0, 10, 25, 10), 1); // one past the bottom: the window moves one row
    assert_eq!(bind_panel_first(1, 5, 25, 10), 1); // inside the window: it stays
    assert_eq!(bind_panel_first(11, 4, 25, 10), 4); // above it: the picked row becomes the first
    assert_eq!(bind_panel_first(30, 24, 25, 10), 15); // never past the last full window
    assert_eq!(bind_panel_first(7, 3, 5, 10), 0); // everything fits: no scrolling
    assert_eq!(bind_panel_first(3, 2, 25, 0), 0);
}

fn check_fits(font: Option<&dyn HudTextMetrics>, name: &str) {
    let s = sample(4, false, true);
    let fit = bind_panel_fit(&s, font, SURFACE_W, SURFACE_H);
    assert!(fit.fits && fit.slots == BIND_PANEL_SLOTS, "{name}: {fit:?}");
    assert!((BIND_ROW_PX_MIN..=BIND_ROW_PX_MAX).contains(&fit.row_px), "{name}: {fit:?}");
    assert!(card_fits(&fit, SURFACE_W, SURFACE_H), "{name}: {fit:?}");
    // Without conflicts the card is shorter by the two note lines (or, with that room, takes roomier text).
    let calm = bind_panel_fit(&sample(4, false, false), font, SURFACE_W, SURFACE_H);
    if calm.row_px == fit.row_px {
        assert!(calm.width == fit.width && calm.height < fit.height, "{name}: {calm:?}");
    } else {
        assert!(calm.row_px > fit.row_px, "{name}: {calm:?}");
    }
    // The picked row and the scroll do not change the size.
    for sel in [0, 12, 19, 24] {
        assert_eq!(bind_panel_fit(&sample(sel, false, true), font, SURFACE_W, SURFACE_H), fit, "{name}: picked {sel}");
    }
    // Every scale from 1 to 2: it fits, with at least the minimum rows.
    for scale in SCALES {
        let (w, h) = ((1920.0 / scale) as u32, (1200.0 / scale) as u32);
        let at_scale = bind_panel_fit(&s, font, w, h);
        assert!(at_scale.fits && at_scale.slots >= BIND_PANEL_MIN_SLOTS, "{name} at {w}x{h}: {at_scale:?}");
        assert!(card_fits(&at_scale, w, h), "{name} at {w}x{h}: {at_scale:?}");
    }
    // A panel with fewer rows than slots shows them all.
    let mut few = sample(1, false, false);
    few.rows.truncate(4);
    assert_eq!(bind_panel_fit(&few, font, SURFACE_W, SURFACE_H).slots, 4, "{name}");
}

#[test]
fn fits_every_scale_in_the_pixel_font() {
    check_fits(None, "pixel font");
}

#[test]
fn fits_every_scale_in_an_installed_font() {
    check_fits(Some(&Mono), "installed font");
    let s = sample(4, false, true);
    assert_ne!(bind_panel_fit(&s, Some(&Mono), SURFACE_W, SURFACE_H), bind_panel_fit(&s, None, SURFACE_W, SURFACE_H));
}

// --- Raster checks ---------------------------------------------------------------------------------------------------

const ACCENT: [f32; 3] = [0.48, 0.64, 0.97];

fn px(im: &HudImage, x: u32, y: u32) -> &[u8] {
    let i = (y as usize * im.width as usize + x as usize) * 4;
    &im.rgba[i..i + 4]
}
/// Pixels of exactly `c` (an opaque fill, +-2 per channel).
fn count(im: &HudImage, c: MdRgb) -> usize {
    im.rgba
        .chunks_exact(4)
        .filter(|p| p[3] == 255 && (0..3).all(|i| (i32::from(p[i]) - (c[i] * 255.0).round() as i32).abs() <= 2))
        .count()
}
fn check_cap(im: &HudImage, what: &str) {
    let over = im.rgba.chunks_exact(4).flat_map(|p| p[..3].iter()).filter(|&&c| c > CARD_MAX_LEVEL).count();
    let unpremultiplied = im.rgba.chunks_exact(4).map(|p| p[..3].iter().filter(|&&c| c > p[3]).count()).sum::<usize>();
    assert!(over == 0 && unpremultiplied == 0, "{what}: {over} channels over the cap, {unpremultiplied} not premultiplied");
}
fn check_fit(im: &HudImage, w: u32, h: u32, what: &str) {
    let ok = im.width + 2 * HUD_FIT_MARGIN <= w && im.height + 2 * HUD_FIT_MARGIN <= h && im.height + HELP_RISE_PX as u32 <= h;
    assert!(ok, "{what}: card {}x{} does not fit {w}x{h}", im.width, im.height);
}
/// The right padding (inside the card's edge, outside every row) holds nothing but the card's own surface.
fn check_right_pad(im: &HudImage, what: &str) {
    let r = px(im, im.width - 12, im.height / 2).to_vec();
    let mut bad = 0;
    for y in 40..im.height - 40 {
        for x in im.width - 28..im.width - 4 {
            bad += px(im, x, y).iter().zip(&r).filter(|(a, b)| (i32::from(**a) - i32::from(**b)).abs() > 3).count();
        }
    }
    assert_eq!(bad, 0, "{what}: pixels of ink in the right padding");
}

fn check_raster(f: Option<&dyn HudTextMetrics>, name: &str) {
    let m = glass_theme_scheme(ACCENT);
    let s = sample(4, false, true);
    let fit = bind_panel_fit(&s, f, SURFACE_W, SURFACE_H);
    let im = render_bind_panel(&s, ACCENT, f, SURFACE_W, SURFACE_H, 0);
    assert!(fit.fits && fit.slots == BIND_PANEL_SLOTS && fit.width == im.width && fit.height == im.height, "{name}");
    check_fit(&im, SURFACE_W, SURFACE_H, name);
    check_cap(&im, name);
    check_right_pad(&im, name);
    // Roles: a picked band, error bands for the conflicts in view (row 3; row 19 is scrolled out), a custom chip.
    assert!(count(&im, m.secondary_container) > 1000, "{name}");
    assert!(count(&im, m.error_container) > 1000, "{name}");
    assert!(count(&im, m.primary_container) > 100, "{name}");
    // Without conflicts there is no error colour at all.
    let calm = render_bind_panel(&sample(4, false, false), ACCENT, f, SURFACE_W, SURFACE_H, 0);
    assert_eq!(count(&calm, m.error_container), 0, "{name}");
    check_cap(&calm, &format!("{name}, no conflicts"));
    // The picked row and the scroll do not change the size; a scrolled window still draws (row 19's long note: cut).
    for sel in [0u32, 12, 19, 24] {
        let first = bind_panel_first(0, sel, 25, fit.slots);
        let scrolled = render_bind_panel(&sample(sel, false, true), ACCENT, f, SURFACE_W, SURFACE_H, first);
        assert!(scrolled.width == im.width && scrolled.height == im.height, "{name}, picked {sel}");
        check_cap(&scrolled, &format!("{name}, picked {sel}"));
        check_right_pad(&scrolled, &format!("{name}, picked {sel}"));
    }
    // Waiting for keys: the chip's primary outline.
    let capture = render_bind_panel(&sample(2, true, true), ACCENT, f, SURFACE_W, SURFACE_H, 0);
    assert!(count(&capture, m.primary) > count(&im, m.primary), "{name}");
    check_cap(&capture, &format!("{name}, capture"));
    for scale in SCALES {
        let (w, h) = ((1920.0 / scale) as u32, (1200.0 / scale) as u32);
        let card = render_bind_panel(&s, ACCENT, f, w, h, 0);
        check_fit(&card, w, h, &format!("{name} at {w}x{h}"));
        check_cap(&card, &format!("{name} at scale {scale}"));
    }
}

#[test]
fn rendered_card_in_the_pixel_font() {
    check_raster(None, "pixel font");
}

#[test]
fn rendered_card_in_the_installed_font() {
    match HudFont::load(HUD_FONT_FAMILY) {
        Ok(font) => check_raster(Some(&font), "installed font"),
        Err(why) => eprintln!("note: {HUD_FONT_FAMILY} not available ({why}); real-font raster not checked"),
    }
}

// The overlay: shown, unchanged, scrolled, hidden (content kept while it fades), re-laid out, recoloured.
#[test]
fn overlay_fades_scrolls_and_redraws_on_change() {
    let mut o = BindPanelOverlay::new();
    assert!(!o.set_viewport(SURFACE_W, SURFACE_H)); // nothing rendered yet
    assert!(o.set(&sample(0, false, true), 0.0) && o.visible() && o.generation() == 1 && o.shown_count() == 1);
    assert!(!o.set(&sample(0, false, true), 10.0)); // same content: nothing
    assert!(o.set(&sample(15, false, true), 20.0) && o.generation() == 2 && o.first() == 15 - o.slots() + 1);
    assert_eq!(o.slots(), BIND_PANEL_SLOTS);
    assert!(o.set(&sample(14, false, true), 30.0) && o.first() == 15 - o.slots() + 1); // still in the window: it stays
    assert!(o.alpha(30.0) > 0.0 && o.alpha(30.0 + HELP_FADE_IN_MS) == 1.0 && o.offset_px(30.0 + HELP_FADE_IN_MS) == 0.0);
    let shown = o.generation();
    assert!(o.set(&BindPanelState::default(), 1000.0) && !o.visible() && o.hidden_count() == 1);
    assert!(o.generation() == shown && o.fit().width > 0); // fades out with the same card
    assert_eq!(o.state(), &sample(14, false, true));
    assert!(o.animating(1000.0 + HELP_FADE_OUT_MS / 2.0) && o.alpha(1000.0 + HELP_FADE_OUT_MS + 1.0) == 0.0);
    assert!(o.set_viewport(960, 600) && o.generation() == shown + 1 && o.fit().height + 2 * HUD_FIT_MARGIN <= 600);
    assert!(o.set_accent([0.9, 0.4, 0.3]) && !o.set_accent([0.9, 0.4, 0.3]));
    assert!(o.set(&sample(14, false, true), 2000.0) && o.visible() && o.shown_count() == 2);
}

#[test]
fn notes_text_cut_and_place() {
    let mut r = BindRow::new("a", "b", "", BindRowState::Conflict);
    assert!(!r.has_note());
    r.note = "x".into();
    assert!(r.has_note());
    r.state = BindRowState::Custom;
    assert!(!r.has_note());
    // 6 px per char at zoom 1 (px 10): "abcdefgh" is 47 wide; "abcd..." is 41.
    assert_eq!(bind_fit_text(None, "abcdefgh", 10, false, 47), "abcdefgh");
    assert_eq!(bind_fit_text(None, "abcdefgh", 10, false, 46), "abcd...");
    assert_eq!(bind_fit_text(None, "ab  cdefgh", 10, false, 40), "ab...");
    assert_eq!(bind_fit_text(None, "abcdefgh", 10, false, 0), "...");
    let s = sample(4, false, true);
    assert_eq!(bind_panel_place(&s), "5 / 25");
    assert_eq!(bind_panel_place(&BindPanelState { selected: 99, ..s }), "25 / 25");
    assert_eq!(bind_panel_place(&BindPanelState::default()), "");
}

// --- Byte parity with the C++ ----------------------------------------------------------------------------------------

/// The C++ parity program's panel: `n` rows with varied text, notes on every third row, conflicts on rows whose index
/// mod 7 is under `conflicts`, custom binds elsewhere on every fourth from 1, row `selected` capturing when asked.
fn parity_sample(n: u32, selected: u32, capture: bool, conflicts: i32) -> BindPanelState {
    let mut s = BindPanelState {
        visible: true,
        title: "Key binds".into(),
        hint: "Up/Down pick   Enter change".into(),
        status: "ok".into(),
        selected,
        ..BindPanelState::default()
    };
    for i in 0..n {
        let state = if conflicts > 0 && ((i % 7) as i32) < conflicts {
            BindRowState::Conflict
        } else if i % 4 == 1 {
            BindRowState::Custom
        } else {
            BindRowState::Normal
        };
        let note = if i % 3 != 0 { String::new() } else { format!("Off: conflicts with {i}") };
        s.rows.push(BindRow::new(
            format!("Action {i}{}", "a".repeat((i % 9 * 4) as usize)),
            format!("Ctrl+Alt+{}", "K".repeat((1 + i % 5) as usize)),
            note,
            state,
        ));
    }
    if capture && selected < n {
        let r = &mut s.rows[selected as usize];
        r.state = BindRowState::Capture;
        r.keys = "Press keys...".into();
    }
    s
}

fn u(out: &mut String, v: impl Into<u64>) {
    *out += &format!("{} ", v.into());
}

fn cpp_dump() -> String {
    let mut out = String::new();
    let mut states = Vec::new();
    for n in [0u32, 1, 2, 4, 10, 11, 25, 64] {
        for c in [0, 1, 3, 7] {
            states.push(parity_sample(n, n / 2, c == 1, c));
        }
    }
    let sizes: [[u32; 2]; 19] = [
        [0, 0],
        [1536, 960],
        [1920, 1200],
        [1536, 960],
        [1280, 800],
        [1200, 750],
        [1097, 685],
        [960, 600],
        [800, 500],
        [640, 400],
        [300, 200],
        [100, 100],
        [4_000_000_000, 10],
        [1920, 0],
        [0, 700],
        [1000, 4_000_000_000],
        [900, 530],
        [900, 470],
        [900, 420],
    ];
    for s in &states {
        for [w, h] in sizes {
            let l = bind_panel_layout(s, None, w, h);
            let st = l.style;
            for v in [st.title_px, st.row_px, st.note_px, st.hint_px, st.row_pad, st.gap, l.slots, l.notes, l.width, l.height]
            {
                u(&mut out, v);
            }
            for b in [l.title, l.row, l.note, l.hint] {
                for v in [b.width, b.ascent, b.descent] {
                    u(&mut out, v);
                }
            }
            for v in [l.action_w, l.keys_w, l.chip_h, l.row_h, l.note_h, l.list_h] {
                u(&mut out, v);
            }
            let f = bind_panel_fit(s, None, w, h);
            for v in [f.width, f.height, f.row_px, f.slots] {
                u(&mut out, v);
            }
            u(&mut out, f.fits);
            out += &bind_panel_place(s);
            out += "|\n";
        }
    }
    for t in ["", "A", "Key binds", "Send window to the selected desktop", "Ctrl+Alt+Shift+Super+bracketright", "a   b   c", "      "]
    {
        for px in [6u32, 14, 18, 26, 30] {
            for bold in [false, true] {
                for w in [0u32, 1, 30, 100, 250, 600] {
                    out += &bind_fit_text(None, t, px, bold, w);
                    out += "|";
                }
            }
        }
    }
    out += "\n";
    for px in [0u32, 1, 4, 5, 9, 10, 19, 28, 29, 4_000_000_000] {
        u(&mut out, bind_text_zoom(px));
    }
    out += "\n";
    for prev in [0u32, 1, 3, 7, 15, 30, 4_000_000_000] {
        for sel in [0u32, 2, 5, 9, 10, 14, 24, 30, 4_000_000_000] {
            for rows in [0u32, 3, 5, 10, 25, 64] {
                for slots in [0u32, 1, 3, 10] {
                    u(&mut out, bind_panel_first(prev, sel, rows, slots));
                }
            }
        }
    }
    out += "\n";
    let mut o = BindPanelOverlay::new();
    let st = |o: &BindPanelOverlay, out: &mut String| {
        u(out, o.generation());
        u(out, o.first());
        u(out, o.slots());
        u(out, o.shown_count());
        u(out, o.hidden_count());
        u(out, o.visible());
        // A rendered card's image is the fit's size; before the first render the C++ image is empty.
        let fit = if o.generation() != 0 { o.fit() } else { BindPanelFit::default() };
        u(out, fit.width);
        u(out, fit.height);
        *out += "\n";
    };
    let r = o.set_viewport(1536, 960);
    u(&mut out, r);
    st(&o, &mut out);
    for (s, t) in [(parity_sample(25, 0, false, 1), 0.0), (parity_sample(25, 0, false, 1), 10.0)] {
        let r = o.set(&s, t);
        u(&mut out, r);
        st(&o, &mut out);
    }
    for (s, t) in [(parity_sample(25, 20, false, 1), 20.0), (parity_sample(25, 14, false, 1), 30.0)] {
        let r = o.set(&s, t);
        u(&mut out, r);
        st(&o, &mut out);
    }
    let r = o.set(&parity_sample(25, 3, true, 1), 40.0);
    u(&mut out, r);
    st(&o, &mut out);
    for (w, h) in [(960, 600), (300, 200)] {
        let r = o.set_viewport(w, h);
        u(&mut out, r);
        st(&o, &mut out);
    }
    for (s, t) in [(parity_sample(25, 24, false, 7), 50.0), (BindPanelState::default(), 60.0)] {
        let r = o.set(&s, t);
        u(&mut out, r);
        st(&o, &mut out);
    }
    let r = o.set_accent([0.9, 0.4, 0.3]);
    u(&mut out, r);
    st(&o, &mut out);
    let r = o.set_viewport(0, 0);
    u(&mut out, r);
    st(&o, &mut out);
    for (s, t) in [(parity_sample(4, 1, false, 0), 70.0), (parity_sample(64, 63, false, 3), 80.0)] {
        let r = o.set(&s, t);
        u(&mut out, r);
        st(&o, &mut out);
    }
    out
}

#[test]
fn layout_text_scroll_and_overlay_match_cpp() {
    let dump = cpp_dump();
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in dump.bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    assert_eq!((format!("{h:016x}"), dump.len()), ("2d701882a52626dd".to_string(), 67_774));
}
