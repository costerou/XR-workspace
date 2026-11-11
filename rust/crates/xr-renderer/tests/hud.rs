//! `spike/tests/hud_test.cpp`, plus a dump compared with the C++ one byte for byte. The overlays keep state only; where
//! the C++ checks an overlay's `image()`, these render its lines (or rows) and accent the way the renderer does. Pixel
//! parity of the cards with the C++ is in `cards_match_cpp_golden` (golden/hud_cards.cpp) and `tests/card_raster.rs`.

use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::hud::*;
use xr_renderer::hud_font::HudFont;
use xr_renderer::material::{
    MD_DEFAULT_SEED, MdRgb, md_color, md_contrast, md_elevation, md_hct, md_layer, md_tone, material_scheme,
};

fn joined(lines: &[HudLine]) -> String {
    lines.iter().map(|l| format!("{}|", l.text)).collect()
}

fn row(keys: &str, action: &str) -> HelpRow {
    HelpRow::new(keys, action)
}

fn lit(im: &HudImage, x: u32, y: u32) -> bool {
    im.rgba[(y as usize * im.width as usize + x as usize) * 4 + 3] != 0
}
fn premultiplied(im: &HudImage) -> bool {
    im.rgba.chunks_exact(4).all(|p| p[..3].iter().all(|&c| c <= p[3]))
}
fn brightest(im: &HudImage) -> u8 {
    im.rgba.chunks_exact(4).flat_map(|p| p[..3].iter().copied()).max().unwrap_or(0)
}
/// The C++ `StatusPopups::image()` (pop-ups draw in the pixel font here, as the C++ ones do without `set_font`).
fn card(p: &StatusPopups) -> HudImage {
    render_hud_card(p.lines(), p.accent(), None)
}
/// The C++ `HelpOverlay::image()` of an overlay without a viewport.
fn help_image(h: &HelpOverlay, font: Option<&dyn HudTextMetrics>) -> HudImage {
    render_help_card(h.rows(), h.accent(), font, 0, 0, h.page()).0
}
fn installed_font() -> Option<HudFont> {
    match HudFont::load(HUD_FONT_FAMILY) {
        Ok(font) => Some(font),
        Err(why) => {
            eprintln!("skipped: {why}");
            None
        }
    }
}

// Every interactive menu uses a quick ease-out on both entrance and exit. A reversal or an unseen interval must preserve
// the opacity, and a settled card must stop requesting animation frames.
#[test]
fn card_fade_eases_reverses_and_holds() {
    let mut fade = CardFade::new(180.0, 120.0);
    assert!(fade.set(true, 100.0));
    assert!((fade.alpha(190.0) - 0.875).abs() < 1e-5);
    assert!(fade.alpha(280.0) == 1.0 && !fade.animating(280.0));
    assert!(fade.set(false, 300.0));
    assert!((fade.alpha(360.0) - 0.125).abs() < 1e-5);
    let mid = fade.alpha(360.0);
    assert!(fade.set(true, 360.0) && fade.alpha(360.0) == mid);
    assert!(fade.alpha(450.0) == 1.0 && !fade.animating(450.0));
    assert!(fade.set(false, 500.0));
    let held = fade.alpha(530.0);
    fade.hold(530.0, 1000.0);
    assert_eq!(fade.alpha(1000.0), held);
    assert!(fade.alpha(1090.0) == 0.0 && !fade.animating(1090.0));
    let mut instant = CardFade::new(0.0, 0.0);
    assert!(instant.set(true, 0.0) && instant.alpha(0.0) == 1.0 && !instant.animating(0.0));
    assert!(instant.set(false, 0.0) && instant.alpha(0.0) == 0.0 && !instant.animating(0.0));
}

// Glyphs: case-insensitive; unknown characters render '?' instead of nothing.
#[test]
fn glyphs_and_text_width() {
    assert!(std::ptr::eq(hud_glyph(b'a'), hud_glyph(b'A')) && std::ptr::eq(hud_glyph(b'~'), hud_glyph(b'?')));
    assert_eq!(*hud_glyph(b' '), [0u8; GLYPH_H as usize]);
    for c in b'A'..=b'Z' {
        assert!(hud_glyph(c).iter().all(|&r| r < 0x20));
        assert!(hud_glyph(c).iter().any(|&r| r != 0));
    }
    assert!(hud_text_width("", 3) == 0 && hud_text_width("A", 1) == 5 && hud_text_width("AB", 2) == 22);
    // Brackets have real pixel glyphs, and '%' (placement menu units).
    for c in *b"[]%" {
        assert!(!std::ptr::eq(hud_glyph(c), hud_glyph(b'?')));
    }
}

// Fade: in, hold at exactly 1, out, then gone.
#[test]
fn popup_alpha_fades_in_holds_and_out() {
    assert!(popup_alpha(-1.0) == 0.0 && popup_alpha(0.0) == 0.0);
    assert!(popup_alpha(POPUP_FADE_IN_MS / 2.0) > 0.4 && popup_alpha(POPUP_FADE_IN_MS / 2.0) < 0.6);
    assert!(popup_alpha(POPUP_FADE_IN_MS) == 1.0 && popup_alpha(POPUP_FADE_IN_MS + POPUP_HOLD_MS - 1.0) == 1.0);
    let mut t = POPUP_FADE_IN_MS + POPUP_HOLD_MS;
    while t + 10.0 < POPUP_TOTAL_MS {
        assert!(popup_alpha(t + 10.0) <= popup_alpha(t));
        t += 10.0;
    }
    assert!(popup_alpha(POPUP_TOTAL_MS) == 0.0 && popup_alpha(1e9) == 0.0);
}

// Text for each mode and cursor state, in Material roles.
#[test]
fn popup_lines_say_what_changed() {
    assert!(joined(&view_mode_lines(0)).contains("|Anchor|"));
    assert!(joined(&view_mode_lines(1)).contains("|Locked|"));
    assert!(joined(&view_mode_lines(2)).contains("|Follow|") && view_mode_lines(2)[1].bold);
    // Each section in Material roles: what it is, the new state in the theme's primary, what it means, its key.
    let m = material_scheme([0.9, 0.55, 0.05]);
    for lines in [view_mode_lines(0), cursor_follow_lines(true), prediction_lines(false), typing_lines(true)] {
        assert_eq!(lines.len(), 4);
        assert_eq!(
            [lines[0].role, lines[1].role, lines[2].role, lines[3].role],
            [HudRole::OnSurfaceVariant, HudRole::Primary, HudRole::OnSurfaceVariant, HudRole::Outline]
        );
        assert!(hud_line_color(&lines[1], &m) == m.primary && hud_line_color(&lines[3], &m) == m.outline);
    }
    let own = HudLine { text: "x".into(), color: [0.2, 0.3, 0.4], ..HudLine::default() };
    assert_eq!(hud_line_color(&own, &material_scheme([1.0, 0.0, 0.0])), [0.2, 0.3, 0.4]);
    assert!(joined(&cursor_follow_lines(true)).starts_with("Cursor follow|On|"));
    assert!(joined(&cursor_follow_lines(false)).starts_with("Cursor follow|Off|"));
    assert!(joined(&prediction_lines(true)).starts_with("Prediction|On|"));
    assert!(joined(&prediction_lines(false)).starts_with("Prediction|Off|"));
    assert!(joined(&typing_lines(true)).starts_with("Keys|Typing|") && typing_lines(true)[1].bold);
    assert!(joined(&typing_lines(false)).starts_with("Keys|XR control|"));
    assert!(joined(&typing_lines(true)).contains("Ctrl+Alt+T"));
    // The mode card says what each mode means now: the overview anchored, a maximized screen following or locked.
    assert!(joined(&view_mode_lines(0)).contains("maximized screen follows you"));
    assert!(joined(&view_mode_lines(2)).contains("Maximized screen"));
    assert!(joined(&view_mode_lines(2)).contains("Ctrl+Alt+V for locked"));
    assert!(joined(&view_mode_lines(1)).contains("Ctrl+Alt+V for follow"));
}

// Pop-up triggers: first update shows the viewing mode; repeats (zoom/select/spacing frames) do not re-show it; a mode
// change or cursor toggle shows a new card.
#[test]
fn status_popups_show_only_changes() {
    let mut p = StatusPopups::new();
    assert!(!p.active(0.0) && p.alpha(0.0) == 0.0 && p.generation() == 0);
    assert!(p.update(0, false, 1000.0) && p.generation() == 1 && p.active(1000.0));
    assert_eq!(p.alpha(1000.0 + POPUP_FADE_IN_MS), 1.0);
    let mode_card = p.lines().to_vec();
    let mode_width = card(&p).width;
    assert!(!p.update(0, false, 1200.0) && p.generation() == 1);
    assert!(!p.active(1000.0 + POPUP_TOTAL_MS) && p.alpha(1000.0 + POPUP_TOTAL_MS) == 0.0);
    assert!(p.update(0, true, 5000.0) && p.generation() == 2 && p.lines() != mode_card.as_slice()); // cursor card
    assert_ne!(card(&p).width, mode_width);
    assert_eq!(joined(p.lines()), joined(&cursor_follow_lines(true)));
    assert_eq!(p.alpha(5000.0 + POPUP_FADE_IN_MS), 1.0);
    assert!(p.update(2, true, 5100.0) && p.generation() == 3 && p.alpha(5100.0 + POPUP_FADE_IN_MS) == 1.0); // restarts
    let single = card(&p).height;
    assert!(p.update(1, false, 9000.0) && card(&p).height > single); // both changed: both sections
    assert_eq!(p.lines().len(), 9);
    assert_eq!(p.lines()[4], HudLine { text: String::new(), scale: 1, color: CARD_DIM, ..HudLine::default() });
    assert!(!p.update(1, false, 9001.0));
    // Prediction toggles show their own card; unchanged prediction does not re-show anything.
    assert!(p.update_prediction(1, false, true, 12000.0) && p.generation() == 5);
    assert!(!p.update_prediction(1, false, true, 12001.0));
    assert!(p.update_prediction(1, false, false, 13000.0) && p.generation() == 6);
    let mut quiet = StatusPopups::new();
    assert!(quiet.update_prediction(0, false, true, 0.0) && quiet.generation() == 1); // first: mode card only
    assert_eq!(joined(quiet.lines()), joined(&view_mode_lines(0)));
    let mut bad = StatusPopups::new();
    assert!(bad.update(9, false, 0.0)); // out-of-range mode is treated as anchor
    assert_eq!(joined(bad.lines()), joined(&view_mode_lines(0)));
}

// Typing mode (Ctrl+Alt+T): its own short card on every switch, nothing for unchanged frames; the 3- and 4-argument
// updates keep the current typing state. It auto-hides like every pop-up (POPUP_TOTAL_MS).
#[test]
fn typing_mode_popups() {
    let mut t = StatusPopups::new();
    assert!(t.update_all(0, false, true, false, 0.0) && t.generation() == 1);
    assert!(t.update_all(0, false, true, true, 3000.0) && t.generation() == 2 && t.active(3000.0));
    assert!(!t.active(3000.0 + POPUP_TOTAL_MS) && t.alpha(3000.0 + POPUP_TOTAL_MS) == 0.0); // gone by itself
    assert!(!t.update_all(0, false, true, true, 4000.0));
    assert!(!t.update_prediction(0, false, true, 4001.0) && !t.update(0, false, 4002.0));
    assert!(t.update_all(0, false, true, false, 6000.0) && t.generation() == 3);
    let mut started_typing = StatusPopups::new();
    assert!(started_typing.update_all(0, false, true, true, 0.0));
    // First update in typing mode: mode card plus the typing section.
    assert!(card(&started_typing).height > card(&t).height);
}

// Maximize-follow (2026-11-03): a maximize switches to follow and an un-maximize back to anchor by themselves; only a
// Ctrl+Alt+V change gets the mode card. track_mode takes the mode quietly; before the first update it changes nothing.
#[test]
fn track_mode_is_quiet() {
    let mut q = StatusPopups::new();
    q.track_mode(2);
    assert!(q.update_all(0, false, true, false, 0.0) && q.generation() == 1); // the start: anchor, shown
    q.track_mode(2);
    assert!(!q.update_all(2, false, true, false, 3000.0) && q.generation() == 1); // maximized: follow, quietly
    assert!(q.update_all(1, false, true, false, 3100.0) && q.generation() == 2); // Ctrl+Alt+V: locked, shown
    q.track_mode(0);
    assert!(!q.update_all(0, false, true, false, 6000.0) && q.generation() == 2); // un-maximized: anchor, quietly
    q.track_mode(2);
    assert!(q.update_all(2, false, false, false, 9000.0) && q.generation() == 3); // a prediction toggle still shows
}

// A theme switch: a card still showing is redrawn in place (new generation, same fade); a gone one is not; the next
// card is drawn in the new theme either way.
#[test]
fn accent_changes_redraw_showing_cards() {
    let mut t = StatusPopups::new();
    assert!(!t.set_accent([0.48, 0.64, 0.97], 0.0) && t.generation() == 0); // nothing shown yet
    assert!(t.update(0, false, 1000.0) && t.generation() == 1);
    let before = card(&t).rgba;
    let a = t.alpha(1000.0 + POPUP_FADE_IN_MS + 100.0);
    assert!(t.set_accent([0.9, 0.55, 0.05], 1000.0 + POPUP_FADE_IN_MS + 100.0) && t.generation() == 2);
    assert!(card(&t).rgba != before);
    assert_eq!(t.accent(), [0.9, 0.55, 0.05]);
    assert_eq!(t.alpha(1000.0 + POPUP_FADE_IN_MS + 100.0), a);
    assert!(!t.set_accent([0.9, 0.55, 0.05], 1200.0) && t.generation() == 2); // the same colour: nothing
    assert!(!t.set_accent([0.2, 0.8, 0.4], 1000.0 + POPUP_TOTAL_MS) && t.generation() == 2); // gone: not redrawn
    assert!(t.update(1, false, 9000.0) && t.accent() == [0.2, 0.8, 0.4] && card(&t).rgba != before);
    let mut h = HelpOverlay::new();
    let one = [row("Ctrl+Alt+H", "Help")];
    assert!(!h.set_accent([0.9, 0.55, 0.05]) && h.generation() == 0);
    assert!(h.set(true, &one, 0.0, 0) && h.generation() == 1);
    let help = help_image(&h, None).rgba;
    assert!(h.set_accent([0.2, 0.8, 0.4]) && h.generation() == 2 && h.visible());
    assert!(help_image(&h, None).rgba != help);
    assert!(!h.set_accent([0.2, 0.8, 0.4]) && h.generation() == 2);
}

fn help_rows() -> Vec<HelpRow> {
    vec![row("Ctrl+Alt+Up", "Zoom in"), row("Ctrl+Alt+[", "Less space"), row("Ctrl+Alt+H", "Show / hide this help")]
}

// Help card: brackets have real pixel glyphs; the card grows with rows, is premultiplied and rounded, and is the size its
// fit says.
#[test]
fn help_card_grows_with_rows() {
    assert!(hud_glyph(b'[') != hud_glyph(b'?') && hud_glyph(b']') != hud_glyph(b'?') && hud_glyph(b'%') != hud_glyph(b'?'));
    let rows = help_rows();
    let (hc, controls) = render_help_card(&rows, [0.5, 0.6, 0.9], None, 0, 0, 0);
    let hc1 = render_help_card(&rows[..1], [0.5, 0.6, 0.9], None, 0, 0, 0).0;
    assert!(hc.height > hc1.height && hc.width >= hc1.width && premultiplied(&hc) && !lit(&hc, 0, 0));
    assert!(hc.complete() && controls == HelpCardControls::default()); // one page: nothing to click
    let fit = help_card_fit(&rows, None, 0, 0);
    assert!(fit.width == hc.width && fit.height == hc.height);
    assert!(render_help_card(&[], [1.0, 1.0, 1.0], None, 0, 0, 0).0.width > 0);
}

// Pixel-font card (1:1 output pixels, font texels doubled): sized to the widest line plus padding, rounded (transparent
// corners), an opaque outline-variant edge, premultiplied, text present.
#[test]
fn pixel_font_card() {
    let white = [1.0, 1.0, 1.0];
    let lines = [
        HudLine { text: "VIEW MODE".into(), scale: 2, color: white, ..HudLine::default() },
        HudLine { text: "FOLLOW".into(), scale: 4, color: white, ..HudLine::default() },
    ];
    let card = render_hud_card(&lines, [0.5, 0.6, 0.9], None);
    assert!(card.width == hud_text_width("FOLLOW", 8) + 44 && card.height == 7 * 4 + 8 + 7 * 8 + 44);
    assert!(card.complete());
    assert!(!lit(&card, 0, 0) && !lit(&card, card.width - 1, card.height - 1));
    let edge = (card.height / 2) as usize * card.width as usize * 4; // left edge, mid height
    assert_eq!(card.rgba[edge + 3], 255);
    assert!(premultiplied(&card));
    // The edge is the scheme's outline variant (Material Design 3, from the theme colour the card is drawn with).
    let m = material_scheme([0.5, 0.6, 0.9]);
    for c in 0..3 {
        assert!((i32::from(card.rgba[edge + c]) - (m.outline_variant[c] * 255.0).round() as i32).abs() <= 1);
    }
    let text_row = |im: &HudImage, y: u32| (22..im.width - 22).any(|x| im.rgba[(y as usize * im.width as usize + x as usize) * 4] > 200);
    assert!(text_row(&card, 22 + 28 + 8 + 3 * 8)); // a line without a role keeps its own colour (white here)
    assert_eq!(render_hud_card(&[], white, None).width, 44);
}

// The installed font (JetBrainsMono Nerd Font), when there is one: anti-aliased (partial coverage), wider than tall,
// premultiplied. Absent font: reported, not fatal.
#[test]
fn installed_font_cards() {
    let Some(font) = installed_font() else { return };
    let f: Option<&dyn HudTextMetrics> = Some(&font);
    assert!(!font.description().is_empty());
    let fc = render_hud_card(&view_mode_lines(2), [0.5, 0.6, 0.9], f);
    assert!(fc.width > fc.height && fc.height > 100 && premultiplied(&fc));
    assert!(fc.rgba.chunks_exact(4).any(|p| p[0] > 30 && p[0] < 200 && p[3] > 200));
    assert!(font.measure("Follow", 76, true) >= font.measure("Follow", 76, false) && font.measure("", 76, false) == 0);
    let fh = render_help_card(&help_rows(), [0.5, 0.6, 0.9], f, 0, 0, 0).0;
    assert!(fh.width > fh.height && premultiplied(&fh));
}

// Image-retention protection. The help card and every pop-up stay well below full white everywhere (text, edge,
// dividers: Material roles under the cap), with the pixel font and with the installed font, in any theme colour.
#[test]
fn cards_stay_under_the_oled_cap() {
    let rows = [row("Ctrl+Alt+H", "Show / hide this help"), row("Ctrl+Alt+L", "Placement pill of the selected desktop")];
    let installed = installed_font();
    let mut fonts: Vec<Option<&dyn HudTextMetrics>> = vec![None];
    if let Some(font) = &installed {
        fonts.push(Some(font));
    }
    for font in fonts {
        let mut help = HelpOverlay::new();
        help.set(true, &rows, 0.0, 0);
        let b = brightest(&help_image(&help, font));
        assert!((140..=CARD_MAX_LEVEL).contains(&b), "help {b}"); // still clearly readable
        // Typing mode pop-up (Ctrl+Alt+T): a switch alone draws only dimmed card colours, either way.
        let mut typing = StatusPopups::new();
        let typing_card = |p: &StatusPopups| render_hud_card(p.lines(), p.accent(), font);
        assert!(typing.update_all(0, false, true, false, 0.0)); // first: the viewing mode card
        assert!(typing.update_all(0, false, true, true, 5000.0));
        let b = brightest(&typing_card(&typing));
        assert!((140..=CARD_MAX_LEVEL).contains(&b), "typing {b}");
        assert!(typing.update_all(0, false, true, false, 9000.0));
        assert!(brightest(&typing_card(&typing)) <= CARD_MAX_LEVEL);
        for accent in [[1.0, 1.0, 1.0], [0.48, 0.64, 0.97], [1.0, 0.0, 0.0]] {
            let mut p = StatusPopups::new();
            p.set_accent(accent, 0.0);
            assert!(p.update_all(2, true, true, false, 0.0));
            let b = brightest(&typing_card(&p));
            assert!((120..=CARD_MAX_LEVEL).contains(&b), "pop-up {accent:?} {b}");
            let mut themed = HelpOverlay::new(); // the C++ one has no font
            themed.set_accent(accent);
            themed.set(true, &rows, 0.0, 0);
            let b = brightest(&help_image(&themed, None));
            assert!((140..=CARD_MAX_LEVEL).contains(&b), "help {accent:?} {b}");
        }
    }
    const _: () = assert!(CARD_MAX_LEVEL as f32 <= 0.75 * 255.0);
}

// Help fade: hidden until shown; eases in, holds while visible, eases out; monotonic both ways; toggling mid-fade
// reverses from the current opacity (no jump); rows re-render only on change.
#[test]
fn help_overlay_fades() {
    let rows = help_rows();
    let mut h = HelpOverlay::new();
    assert!(h.alpha(0.0) == 0.0 && !h.animating(0.0) && h.generation() == 0 && !h.set(false, &rows, 0.0, 0));
    assert!(h.set(true, &rows, 1000.0, 0) && h.visible() && h.generation() == 1);
    assert!(h.animating(1000.0) && h.alpha(1000.0) == 0.0);
    assert!(h.shown_count() == 1 && h.hidden_count() == 0);
    assert_eq!(h.offset_px(1000.0), HELP_RISE_PX);
    let mut t = 1000.0;
    while t < 1000.0 + HELP_FADE_IN_MS {
        assert!(h.alpha(t + 10.0) >= h.alpha(t));
        t += 10.0;
    }
    let up = 1000.0 + HELP_FADE_IN_MS;
    assert!(h.alpha(up) == 1.0 && h.offset_px(up) == 0.0 && !h.animating(up));
    assert_eq!(h.alpha(1e9), 1.0); // stays up while visible
    assert!(!h.set(true, &rows, 2000.0, 0) && h.generation() == 1); // same rows: no re-render
    assert!(h.set(false, &rows, 3000.0, 0) && h.animating(3000.0) && h.alpha(3000.0) == 1.0);
    let mut t = 3000.0;
    while t < 3000.0 + HELP_FADE_OUT_MS {
        assert!(h.alpha(t + 10.0) <= h.alpha(t));
        t += 10.0;
    }
    assert!(h.alpha(3000.0 + HELP_FADE_OUT_MS) == 0.0 && !h.animating(3000.0 + HELP_FADE_OUT_MS));
    assert!(h.shown_count() == 1 && h.hidden_count() == 1);
    assert!(h.set(true, &rows, 5000.0, 0));
    let mid = h.alpha(5000.0 + HELP_FADE_IN_MS / 2.0);
    assert!((mid - 0.875).abs() < 1e-5);
    let half = 5000.0 + HELP_FADE_IN_MS / 2.0;
    assert!(h.set(false, &rows, half, 0) && h.alpha(half) == mid); // reverses, no jump
    assert_eq!(h.alpha(half + HELP_FADE_OUT_MS), 0.0);
    assert!(h.shown_count() == 2 && h.hidden_count() == 2 && !h.visible());
    assert!(h.set(true, &rows[..1], 7000.0, 0) && h.generation() == 2); // new rows: new card
}

fn many_rows(n: usize, sect: usize) -> Vec<HelpRow> {
    (0..n)
        .map(|i| HelpRow {
            keys: format!("Ctrl+Alt+{}", "K".repeat(1 + i % 4)),
            action: format!("Do thing {i}{}", "x".repeat(i % 7 * 3)),
            section: sect > 0 && i % sect == 0,
        })
        .collect()
}

// Pages: a card too tall for the viewport splits into pages of one size; turning wraps round, reopening starts on page
// 1, and a smaller page count after a viewport change keeps the page in range.
#[test]
fn help_overlay_pages() {
    let rows = many_rows(60, 5);
    let mut h = HelpOverlay::new();
    assert!(!h.set_viewport(1920, 1200)); // before the first render: nothing to redo
    assert!(h.set(true, &rows, 0.0, 0) && h.pages() > 1 && h.page() == 0);
    assert!(!h.controls().prev.is_empty() && !h.controls().indicator.is_empty() && !h.controls().next.is_empty());
    assert!(h.set(true, &rows, 10.0, -1) && h.page() == h.pages() - 1 && h.turns() == 1); // wraps round
    assert!(h.set(true, &rows, 20.0, 1) && h.page() == 0 && h.turns() == 2);
    assert!(h.set(true, &rows, 30.0, 1) && h.page() == 1);
    assert!(h.set(false, &rows, 40.0, 1) && h.page() == 1 && h.turns() == 3); // hiding: the turn is ignored
    assert!(h.set(true, &rows, 500.0, 0) && h.page() == 0); // reopening starts on page 1
    assert!(h.set(true, &rows, 510.0, 1) && h.page() == 1);
    assert!(h.set_viewport(0, 0) && h.pages() == 1 && h.page() == 0 && h.controls().prev.is_empty());
    assert!(!h.set_viewport(0, 0));
    let one = HelpOverlay::new();
    assert!(one.controls().next.is_empty() && one.pages() == 1);
}

// The help card's fit and click targets for a paged card, and their place on the canvas, as the C++ computes them.
#[test]
fn help_fit_controls_and_geometry_match_cpp() {
    let rows = many_rows(12, 0);
    let fit = help_card_fit(&rows, None, 1920, 300);
    assert_eq!(fit, HelpCardFit { width: 580, height: 216, row_px: 18, fits: true, page_rows: vec![5, 5, 2] });
    let r = |x, y, w, h| HelpRect { x, y, w, h };
    for page in [0, 1, 2, 4] {
        assert_eq!(
            help_card_controls(&rows, None, 1920, 300, page),
            HelpCardControls { prev: r(229, 148, 34, 18), indicator: r(269, 148, 41, 18), next: r(316, 148, 34, 18) }
        );
    }
    assert_eq!(help_card_controls(&rows, None, 0, 0, 3), HelpCardControls::default());
    let rows = many_rows(60, 5);
    let fit = help_card_fit(&rows, None, 1920, 1200);
    let controls = help_card_controls(&rows, None, 1920, 1200, 1);
    let origin = centered_card_origin(1920, 1200, fit.width, fit.height, 0.0);
    let view = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    assert_eq!(
        help_geometry_json(&view, 1920, 1200, 0.5, 0.3125, origin, fit.width, fit.height, &controls),
        r#"{"help_geometry":{"card":[664,65,592,1071],"prev":[899,1068,34,18],"indicator":[939,1068,41,18],"next":[986,1068,34,18]}}"#
    );
    assert_eq!(
        help_geometry_json(&view, 1920, 1200, 0.5, 0.3125, origin, fit.width, fit.height, &HelpCardControls::default()),
        r#"{"help_geometry":{"card":[664,65,592,1071]}}"#
    );
    assert_eq!(help_geometry_json(&view, 0, 1200, 0.5, 0.3, origin, 1, 1, &controls), "");
    // Looking away from the canvas: a corner does not land on it.
    let back = [-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0];
    assert_eq!(help_geometry_json(&back, 1920, 1200, 0.5, 0.3125, origin, fit.width, fit.height, &controls), "");
}

// Placement menu rows: four settings (every desktop always faces you: no yaw / pitch / roll), friendly labels, signed
// values with units, the selected field highlighted, values from the selected desktop; ASCII units for the pixel font.
#[test]
fn placement_menu_rows_format_values() {
    const _: () = assert!(PLACEMENT_MENU_FIELDS == 4 && PLACEMENT_MENU_DESKTOP_COUNT == MAX_DESKTOPS);
    let mut ms = PlacementMenuState { open: true, desktop: 1, field: 3, ..PlacementMenuState::default() };
    ms.placements[0] = [0, 0, 100, 0];
    ms.placements[1] = [125, -70, 150, 90];
    ms.placements[2] = [-1800, 900, 200, 120];
    let mr = placement_menu_rows(&ms, false);
    assert_eq!(mr.len(), PLACEMENT_MENU_FIELDS);
    let labels: Vec<_> = mr.iter().map(|r| r.label.as_str()).collect();
    assert_eq!(labels, ["Left / right", "Down / up", "Distance", "Curvature"]);
    assert!(mr.iter().all(|r| r.hint.is_empty()));
    // The orbit's x / y are tenths of a degree, shown as whole degrees rounded half away from zero (12.5 -> 13).
    let values: Vec<_> = mr.iter().map(|r| r.value.as_str()).collect();
    assert_eq!(values, ["+13\u{b0}", "-7\u{b0}", "150%", "90\u{b0}"]);
    let shown = |x: i32, ascii: bool| {
        let mut orbit = ms.clone();
        orbit.placements[1][0] = x;
        placement_menu_rows(&orbit, ascii)[0].value.clone()
    };
    // No "+0" / "-0".
    assert!(shown(0, false) == "0\u{b0}" && shown(4, false) == "0\u{b0}" && shown(-4, false) == "0\u{b0}");
    assert!(shown(5, false) == "+1\u{b0}" && shown(-5, false) == "-1\u{b0}" && shown(-124, false) == "-12\u{b0}");
    assert!(shown(1795, false) == "+180\u{b0}" && shown(-1799, false) == "-180\u{b0}" && shown(1794, false) == "+179\u{b0}");
    assert!(shown(-1795, true) == "-180deg" && shown(4, true) == "0deg" && shown(15, true) == "+2deg");
    for (i, r) in mr.iter().enumerate() {
        assert_eq!(r.selected, i == 3);
    }
    let ascii = placement_menu_rows(&ms, true);
    // The orbit: no space, to fit.
    let values: Vec<_> = ascii.iter().map(|r| r.value.as_str()).collect();
    assert_eq!(values, ["+13deg", "-7deg", "150%", "90 deg"]);
    for r in &ascii {
        assert!(format!("{}{}{}", r.label, r.hint, r.value).bytes().all(|c| (0x20..0x7f).contains(&c)));
    }
    ms.desktop = 2;
    assert!(placement_menu_rows(&ms, true)[0].value == "-180deg" && placement_menu_rows(&ms, true)[3].value == "120 deg");
    let values: Vec<_> = placement_menu_rows(&ms, false).into_iter().map(|r| r.value).collect();
    assert_eq!(values, ["-180\u{b0}", "+90\u{b0}", "200%", "120\u{b0}"]);
    ms.desktop = 0;
    assert!(placement_menu_rows(&ms, false)[2].value == "100%" && placement_menu_rows(&ms, false)[3].value == "0\u{b0}");
    // Out of range: clamped to the last of PLACEMENT_MENU_DESKTOP_COUNT, no crash.
    let last = PlacementMenuState { desktop: (PLACEMENT_MENU_DESKTOP_COUNT - 1) as u32, ..ms.clone() };
    ms.desktop = 9;
    assert_eq!(placement_menu_rows(&ms, true)[0].value, placement_menu_rows(&last, true)[0].value);
    ms.field = 4;
    assert!(placement_menu_rows(&ms, true).iter().all(|r| !r.selected)); // past the last field: none
    assert_eq!(placement_menu_flat(), [0, 0, 100, 0]);
    let state = PlacementMenuState::default();
    assert!(state.count == 3 && state.numbers == [1, 2, 3, 4] && state.brightness == BRIGHTNESS_DEFAULT);
}

// Image-retention protection constants; the brightness checks of the rendered cards are raster checks.
#[test]
fn retention_limits() {
    const _: () = assert!(CARD_MAX_LEVEL as f64 <= 0.75 * 255.0);
    // The footer note's value (tests/native_spike pins it).
    const _: () = assert!(OVERLAY_IDLE_CLOSE_S >= 10 && OVERLAY_IDLE_CLOSE_S <= 300);
}

// Slider bounds (the panel's sliders end at these) and the card origin GlHud centres the help card with.
#[test]
fn slider_bounds_and_card_origin() {
    assert_eq!(placement_menu_bounds(), [(-1800, 1800), (-900, 900), (10, 200), (0, 120)]);
    // Card origin: the formula GlHud draws centred cards with (bottom edge floor((H - h) / 2 - drop) from below).
    for (ow, oh, w, h, drop) in
        [(1536u32, 960u32, 1229u32, 733u32, 0.0f32), (1536, 960, 1228, 734, 18.0), (1536, 960, 1229, 733, 7.5), (1280, 720, 1212, 640, 3.0)]
    {
        let o = centered_card_origin(ow, oh, w, h, drop);
        assert_eq!(o.x, ((ow as f32 - w as f32) / 2.0).floor());
        assert_eq!(oh as f32 - o.y - h as f32, ((oh as f32 - h as f32) / 2.0 - drop).floor());
        if drop == 0.0 {
            assert!((o.y - (oh as f32 - h as f32) / 2.0).abs() <= 1.0);
        }
    }
}

// The tracking hint: one small line, up only while tracking is being reconnected, and never mistaken for a pop-up card.
#[test]
fn tracking_hint() {
    assert_eq!(joined(&tracking_hint_lines()), "Reconnecting tracking...|");
    let mut hint = TrackingHint::new();
    assert_eq!(hint.alpha(0.0), 0.0);
    assert!(!hint.update(false, 0.0));
    assert!(hint.update(true, 100.0));
    assert!(!hint.update(true, 110.0));
    assert_eq!(hint.alpha(100.0 + TRACKING_HINT_FADE_IN_MS), 1.0);
    // One small line, never mistaken for a pop-up card.
    let image = render_hud_card(&tracking_hint_lines(), hint.accent(), None);
    assert!(image.height > 0 && image.complete());
    assert!(image.height * 2 < render_hud_card(&view_mode_lines(0), MD_DEFAULT_SEED, None).height);
    let mut popups = StatusPopups::new();
    popups.update(0, false, 0.0);
    assert_ne!(hint.generation(), popups.generation()); // they share the renderer's pop-up texture
    let before = hint.generation();
    assert_eq!(hint.generation(), before); // nothing changed: the same card
    assert!(hint.set_accent([0.2, 0.8, 0.3]));
    assert_ne!(hint.generation(), before);
    hint.font_changed();
    assert_eq!(hint.generation(), before + 2);
    assert!(hint.update(false, 5000.0));
    assert_eq!(hint.alpha(5000.0 + TRACKING_HINT_FADE_OUT_MS + 1.0), 0.0);
    assert!(!hint.animating(5000.0 + TRACKING_HINT_FADE_OUT_MS + 1.0));
}

#[test]
fn backgrounds() {
    let names: Vec<_> = BACKGROUNDS.iter().map(|b| (b.name, b.label)).collect();
    assert_eq!(names, [("none", "None"), ("grid", "Grid"), ("smoke", "Smoke")]);
    assert_eq!(background_index("smoke"), Background::Smoke as usize);
    assert_eq!(background_index("none"), Background::None as usize);
    assert_eq!(background_index("Grid"), BACKGROUND_COUNT);
    const _: () = assert!(BRIGHTNESS_MIN < BRIGHTNESS_DEFAULT && BRIGHTNESS_DEFAULT < BRIGHTNESS_MAX);
}

// A font's metrics drive the layout instead of the pixel font's.
#[test]
fn installed_font_metrics_are_used() {
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
    let line = HudLine { text: "Follow".into(), scale: 4, bold: true, ..HudLine::default() };
    assert_eq!(line_box(&line, Some(&Mono)), LineBox { width: 6 * (76 * 3 / 5 + 1), ascent: 57, descent: 19 });
    assert_eq!(line_box(&line, None), LineBox { width: hud_text_width("Follow", 8), ascent: 56, descent: 0 });
    let px = HudLine { text: "A".into(), px: 30, ..HudLine::default() };
    assert_eq!(pixel_zoom(&px), 3); // (30 * 2 + 9) / 19
    assert_eq!(pixel_zoom(&HudLine { px: 2, ..HudLine::default() }), 1); // em 6 px: at least zoom 1
    let rows = many_rows(30, 5);
    let fit = help_card_fit(&rows, Some(&Mono), 1920, 1200);
    assert!(fit.fits && fit.width != help_card_fit(&rows, None, 1920, 1200).width);
    let mut h = HelpOverlay::new();
    h.set_font(Some(Box::new(Mono)));
    h.set_viewport(1920, 1200);
    assert!(h.set(true, &rows, 0.0, 0));
    assert_eq!(h.fit(), fit);
}

// --- Byte parity with the C++ ----------------------------------------------------------------------------------------

struct Dump(String);

impl Dump {
    fn f(&mut self, v: f32) {
        self.0 += &format!("{:08x} ", v.to_bits());
    }
    fn rgb(&mut self, c: MdRgb) {
        for v in c {
            self.f(v);
        }
    }
    fn rect(&mut self, r: HelpRect) {
        self.0 += &format!("{},{},{},{} ", r.x, r.y, r.w, r.h);
    }
}

/// The same dump as the C++ parity program (pixel font; `render_help_card`'s controls for the click targets).
fn cpp_dump() -> String {
    let mut d = Dump(String::new());
    for t in ["", "A", "Keyboard shortcuts", "0 / 0", "Ctrl+Alt+PgUp"] {
        for s in [0u32, 1, 2, 7, 4_000_000_000] {
            d.0 += &format!("{} ", hud_text_width(t, s));
        }
    }
    d.0 += "\n";
    let sizes = [(0, 0), (1920, 1200), (1920, 1080), (1280, 720), (800, 400), (300, 2000), (1920, 300), (100, 100), (4000, 0), (0, 500)];
    for n in [0, 1, 12, 30, 60, 120] {
        for s in [0, 5, 25] {
            for (w, h) in sizes {
                let rs = many_rows(n, s);
                let fit = help_card_fit(&rs, None, w, h);
                d.0 += &format!("{} {} {} {} [", fit.width, fit.height, fit.row_px, if fit.fits { "1" } else { "0" });
                for p in &fit.page_rows {
                    d.0 += &format!("{p},");
                }
                d.0 += "]";
                if fit.pages() > 1 {
                    for page in [0, 1, fit.pages() - 1, fit.pages() + 1] {
                        let c = help_card_controls(&rs, None, w, h, page);
                        d.0 += &format!(" p{page}:");
                        d.rect(c.prev);
                        d.rect(c.indicator);
                        d.rect(c.next);
                    }
                }
                d.0 += "\n";
            }
        }
    }
    const R: f32 = std::f32::consts::FRAC_1_SQRT_2; // the C++ 0.7071068f
    let views: [[f32; 9]; 5] = [
        [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        [0.955_336_5, 0.0, 0.295_520_22, 0.0, 1.0, 0.0, -0.295_520_22, 0.0, 0.955_336_5],
        [0.955_336_5, 0.058_710_8, 0.289_629_4, 0.0, 0.980_066_6, -0.198_669_33, -0.295_520_22, 0.189_796_06, 0.936_293_36],
        [-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0],
        [R, 0.0, R, 0.0, 1.0, 0.0, -R, 0.0, R],
    ];
    let canvases =
        [(1920, 1200, 0.5, 0.3125), (3840, 1080, 1.1, 0.3), (1920, 1200, 0.42, 0.27), (0, 1200, 0.5, 0.3), (1920, 1200, 0.0, 0.3), (1920, 1200, 0.5, -1.0)];
    let rs = many_rows(60, 5);
    for (w, h, hw, hh) in canvases {
        let fit = help_card_fit(&rs, None, w, h);
        let c = help_card_controls(&rs, None, w, h, 1);
        for drop in [0.0, 7.5, 18.0] {
            let o = centered_card_origin(w, h, fit.width, fit.height, drop);
            d.f(o.x);
            d.f(o.y);
            d.0 += "\n";
            for v in &views {
                d.0 += &help_geometry_json(v, w, h, hw, hh, o, fit.width, fit.height, &c);
                d.0 += "|\n";
                d.0 += &help_geometry_json(v, w, h, hw, hh, o, fit.width, fit.height, &HelpCardControls::default());
                d.0 += "|\n";
            }
        }
    }
    let odd = HelpCardControls {
        prev: HelpRect { x: 1, y: 2, w: 3, h: 4 },
        indicator: HelpRect { x: 0, y: 0, w: 0, h: 5 },
        next: HelpRect { x: -5, y: -6, w: 7, h: 8 },
    };
    d.0 += &help_geometry_json(&views[0], 1920, 1200, 0.5, 0.3125, CardOrigin { x: -30000.5, y: 1e6 }, 70000, 3, &odd);
    d.0 += "|\n";
    for ow in [0u32, 1, 1920, 3841] {
        for h in [0u32, 333, 1200, 5000] {
            for drop in [0.0, 0.5, 17.25, -3.0] {
                let o = centered_card_origin(ow, 1201, h, h, drop);
                d.f(o.x);
                d.f(o.y);
            }
        }
    }
    d.0 += "\n";
    let seeds = [
        MD_DEFAULT_SEED,
        [0.9, 0.55, 0.05],
        [0.0, 0.0, 0.0],
        [1.0, 1.0, 1.0],
        [0.2, 0.4, 1.0],
        [0.0, 1.0, 0.3],
        [1.5, -0.2, 0.5],
        [0.48, 0.12, 0.73],
        [0.01, 0.012, 0.009],
    ];
    for s in seeds {
        let m = material_scheme(s);
        for c in [
            m.primary,
            m.on_primary,
            m.primary_container,
            m.on_primary_container,
            m.primary_bright,
            m.secondary_container,
            m.on_secondary_container,
            m.error_container,
            m.on_error_container,
            m.surface,
            m.surface_container_low,
            m.surface_container,
            m.surface_container_high,
            m.surface_container_highest,
            m.on_surface,
            m.on_surface_variant,
            m.outline,
            m.outline_variant,
        ] {
            d.rgb(c);
        }
        d.f(m.seed_tone);
        d.f(m.container_tone);
        let h = md_hct(s);
        d.f(h.hue);
        d.f(h.chroma);
        d.f(h.tone);
        d.f(md_tone(s));
        d.f(md_contrast(s, m.surface));
        d.0 += "\n";
    }
    for hue in [0.0, 29.0, 140.5, 265.0, 359.9] {
        for c in [0.0, 0.05, 0.2, 0.5] {
            for t in [0.0, 4.0, 30.0, 50.0, 90.0, 100.0] {
                d.rgb(md_color(hue, c, t));
            }
        }
    }
    d.0 += "\n";
    for l in [-1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 4.75, 5.0, 9.0] {
        let e = md_elevation(l);
        d.f(e.alpha);
        d.f(e.sigma);
        d.f(e.dy);
    }
    d.rgb(md_layer([0.1, 0.2, 0.3], [0.9, 0.8, 0.7], 0.16));
    d.0 += "\n";
    for t in [-1.0, 0.0, 60.0, 119.9, 120.0, 900.0, 1620.0, 1700.0, 1810.0, 1999.0, 2000.0] {
        d.f(popup_alpha(t));
    }
    d.0 += "\n";
    let mut fd = CardFade::new(180.0, 120.0);
    for (v, t) in [(true, 0.0), (true, 40.0), (false, 90.0), (true, 130.0), (false, 400.0), (true, 401.0)] {
        fd.set(v, t);
        for dt in [0.0, 10.0, 33.3, 77.0, 200.0] {
            d.f(fd.level(t + dt));
            d.f(fd.alpha(t + dt));
        }
        fd.hold(t + 5.0, t + 25.0);
        d.f(fd.alpha(t + 30.0));
    }
    d.0 += "\n";
    let mut st = PlacementMenuState {
        placements: [[0, -4, 100, 0], [125, -1800, 10, 120], [-5, 5, 200, 37], [-1795, 900, -3, -7]],
        ..PlacementMenuState::default()
    };
    for desk in [0u32, 1, 2, 3, 9] {
        for ascii in [true, false] {
            st.desktop = desk;
            st.field = desk;
            for r in placement_menu_rows(&st, ascii) {
                d.0 += &format!("{}={}{};", r.label, r.value, if r.selected { "*" } else { "" });
            }
            d.0 += "\n";
        }
    }
    d.0
}

#[test]
fn hud_and_material_match_cpp_bit_for_bit() {
    let dump = cpp_dump();
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in dump.bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    assert_eq!((format!("{h:016x}"), dump.len()), ("3799bc4a60fb3e35".to_string(), 35_140));
}

// --- Pixel parity with the C++ rasterisers ----------------------------------------------------------------------------
//
// golden/hud_cards.txt is printed by golden/hud_cards.cpp (spike/src/hud.cpp's render_hud_card, help_card_fit,
// render_help_card and the overlays' image()). The same cases here must print the same lines, byte-equal cards included.
// The overlays keep state only; like the renderer, the replay draws a card again only when the overlay's generation bumps.

const HUD_CARDS: &str = include_str!("../golden/hud_cards.txt");

fn fnv(im: &HudImage) -> u64 {
    im.rgba.iter().fold(0xcbf2_9ce4_8422_2325_u64, |h, &b| (h ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3))
}
fn line(text: &str, scale: u32, color: [f32; 3], bold: bool, px: u32, role: HudRole) -> HudLine {
    HudLine { text: text.into(), scale, color, bold, px, role }
}
fn rect_text(r: &HelpRect) -> String {
    format!(" {},{},{},{}", r.x, r.y, r.w, r.h)
}
fn controls_text(c: &HelpCardControls) -> String {
    rect_text(&c.prev) + &rect_text(&c.indicator) + &rect_text(&c.next)
}
/// golden/hud_cards.cpp's many_rows.
fn golden_many_rows(n: usize, section: usize) -> Vec<HelpRow> {
    (0..n)
        .map(|i| HelpRow {
            keys: format!("Ctrl+Alt+{}", i % 10),
            action: format!("Example action number {}", i + 1),
            section: i > 0 && i % section == 0,
        })
        .collect()
}
fn golden_help_rows() -> Vec<HelpRow> {
    let sect = |k: &str, a: &str| HelpRow { keys: k.into(), action: a.into(), section: true };
    vec![
        row("Ctrl+Alt+Up", "Maximize the selected desktop"),
        row("Ctrl+Alt+Down", "Overview: any desktop; pills, bar, corners"),
        row("Ctrl+Alt+Left / Right", "Previous / next desktop"),
        row("Ctrl+Alt+[ / ]", "Less / more space between desktops"),
        row("Ctrl+Alt+Space / R", "Recenter / reset to 3 flat desktops"),
        sect("Ctrl+Alt+V", "Maximized: follow / locked"),
        row("Ctrl+Alt+P", "Toggle pose prediction"),
        row("Ctrl+Alt+T", "Typing mode: XR keys off / on"),
        row("Ctrl+Alt+D", "Privacy: main display off / on"),
        row("Ctrl+Alt+E / I", "Background / immersive 3D: on or off"),
        sect("Ctrl+Alt+L", "Placement pill; R in it resets, levels"),
        row("Ctrl+Alt+O / U / C", "VR video: file / web link / hide controls"),
        sect("Ctrl+Alt+H / K", "Help/binds; Ctrl+Alt+G/B: window to XR/back"),
        row("Ctrl+Alt+PgUp / PgDn", "Help: previous / next page"),
    ]
}

struct GoldenOut {
    font: &'static str,
    out: Vec<String>,
}
impl GoldenOut {
    fn put(&mut self, name: &str, im: &HudImage, extra: &str) {
        self.out.push(format!("{name} {} {} {} {}{extra}", self.font, im.width, im.height, fnv(im)));
    }
}

fn golden_hud_cards(g: &mut GoldenOut, font: Option<&dyn HudTextMetrics>) {
    let accents = [MD_DEFAULT_SEED, [0.5, 0.6, 0.9], [0.9, 0.55, 0.05], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]];
    for (a, &accent) in accents.iter().enumerate() {
        let s = format!("_a{a}");
        for mode in 0..4u8 {
            g.put(&format!("view_mode{mode}{s}"), &render_hud_card(&view_mode_lines(mode), accent, font), "");
        }
        for on in [false, true] {
            let n = u8::from(on);
            g.put(&format!("cursor{n}{s}"), &render_hud_card(&cursor_follow_lines(on), accent, font), "");
            g.put(&format!("prediction{n}{s}"), &render_hud_card(&prediction_lines(on), accent, font), "");
            g.put(&format!("typing{n}{s}"), &render_hud_card(&typing_lines(on), accent, font), "");
        }
        g.put(&format!("tracking{s}"), &render_hud_card(&tracking_hint_lines(), accent, font), "");
    }
    use HudRole as R;
    let w = [1.0, 1.0, 1.0];
    g.put("empty", &render_hud_card(&[], w, font), "");
    g.put("blank_line", &render_hud_card(&[line("", 1, [0.46, 0.46, 0.46], false, 0, R::None)], MD_DEFAULT_SEED, font), "");
    let own = [
        line("VIEW MODE", 2, w, false, 0, R::None),
        line("FOLLOW", 4, w, false, 0, R::None),
        line("tiny 1", 1, [0.2, 0.9, 0.4], false, 0, R::None),
        line("Bold 3", 3, [0.9, 0.1, 0.6], true, 0, R::None),
        line("huge", 8, [0.5, 0.5, 1.0], false, 0, R::None),
    ];
    g.put("own_colours", &render_hud_card(&own, [0.5, 0.6, 0.9], font), "");
    let px = [
        line("px 2", 1, w, false, 2, R::None),
        line("px 13", 1, w, true, 13, R::None),
        line("px 29", 1, w, false, 29, R::None),
        line("px 300", 1, w, false, 300, R::Primary),
    ];
    g.put("px_sizes", &render_hud_card(&px, MD_DEFAULT_SEED, font), "");
    let glyphs = [
        line("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 1, w, false, 0, R::None),
        line("abcdefghijklmnopqrstuvwxyz 0123456789", 1, w, false, 0, R::None),
        line(" :-+./[]% ?!@#~,;'\"", 2, w, false, 0, R::Outline),
        line("60\u{b0} caf\u{e9} \u{2190}\u{2192}", 2, w, true, 0, R::OnSecondaryContainer),
    ];
    g.put("glyphs", &render_hud_card(&glyphs, [0.48, 0.64, 0.97], font), "");
    let z = [0.0; 3];
    let roles = [
        line("on surface", 2, z, false, 0, R::OnSurface),
        line("variant", 2, z, false, 0, R::OnSurfaceVariant),
        line("outline", 2, z, false, 0, R::Outline),
        line("primary", 2, z, true, 0, R::Primary),
        line("on secondary container", 2, z, false, 0, R::OnSecondaryContainer),
    ];
    g.put("roles", &render_hud_card(&roles, [0.2, 0.8, 0.4], font), "");
}

fn golden_help_cards(g: &mut GoldenOut, font: Option<&dyn HudTextMetrics>) {
    let sets: Vec<(&str, Vec<HelpRow>)> = vec![
        ("none", vec![]),
        ("one", vec![row("Ctrl+Alt+H", "Show / hide this help")]),
        ("real", golden_help_rows()),
        ("r16", golden_many_rows(16, 5)),
        ("r40", golden_many_rows(40, 5)),
        ("r64", golden_many_rows(64, 7)),
        ("s30", golden_many_rows(30, 30)),
        (
            "wide",
            vec![row(
                "Ctrl+Alt+Shift+Super+F12",
                "An action described in far more words than any surface can hold in one line of text",
            )],
        ),
    ];
    let surfaces = [(0, 0), (1536, 960), (1920, 1200), (960, 600), (1280, 800), (1920, 400), (300, 200)];
    let accent = [0.48, 0.64, 0.97];
    for (set, rows) in &sets {
        for &(w, h) in &surfaces {
            let fit = help_card_fit(rows, font, w, h);
            let pages = fit.page_rows.iter().map(u32::to_string).collect::<Vec<_>>().join(",");
            let name = format!("help_{set}_{w}x{h}");
            g.out.push(format!("{name}_fit {} {} {} {} {} {pages}", g.font, fit.width, fit.height, fit.row_px, i32::from(fit.fits)));
            let n = fit.pages();
            // The C++ list is unsigned: pages() - 1 wraps on a fit without pages.
            let shown = [0, 1, n.wrapping_sub(1), n, n + 1];
            for i in 0..shown.len() {
                if i > 0 && shown[i] == shown[i - 1] {
                    continue;
                }
                if n == 1 && i > 1 {
                    break;
                }
                let (im, c) = render_help_card(rows, accent, font, w, h, shown[i]);
                g.put(&format!("{name}_p{}", shown[i]), &im, &controls_text(&c));
            }
        }
    }
    g.put("help_accent_white", &render_help_card(&golden_help_rows(), [1.0, 1.0, 1.0], font, 1536, 960, 0).0, "");
    g.put("help_accent_black", &render_help_card(&golden_help_rows(), [0.0, 0.0, 0.0], font, 960, 600, 1).0, "");
}

fn golden_overlays(g: &mut GoldenOut, font: Option<&dyn HudTextMetrics>) {
    let load = || -> Option<Box<dyn HudTextMetrics>> {
        font?;
        Some(Box::new(HudFont::load(HUD_FONT_FAMILY).expect("the font loaded once")))
    };
    {
        let mut p = StatusPopups::new();
        let (mut image, mut drawn) = (HudImage::default(), 0);
        let mut step = 0;
        let mut shot = |g: &mut GoldenOut, p: &StatusPopups, changed: bool| {
            if p.generation() != drawn {
                image = render_hud_card(p.lines(), p.accent(), font);
                drawn = p.generation();
            }
            g.put(&format!("popups{step}"), &image, &format!(" {} {}", u8::from(changed), p.generation()));
            step += 1;
        };
        let c = p.update(0, false, 1000.0);
        shot(g, &p, c);
        let c = p.update(0, true, 5000.0);
        shot(g, &p, c);
        let c = p.update(2, true, 5100.0);
        shot(g, &p, c);
        let c = p.update(1, false, 9000.0);
        shot(g, &p, c);
        let c = p.set_accent([0.9, 0.55, 0.05], 9100.0);
        shot(g, &p, c);
        let c = p.update_prediction(1, false, true, 9200.0);
        shot(g, &p, c);
        let c = p.update_all(1, false, true, true, 9300.0);
        shot(g, &p, c);
        let c = p.update_all(0, true, false, false, 9400.0);
        shot(g, &p, c);
        let c = p.set_accent([0.2, 0.8, 0.4], 99999.0); // gone: not redrawn
        shot(g, &p, c);
        let c = p.update_all(2, true, false, false, 100_000.0);
        shot(g, &p, c);
    }
    {
        let mut t = StatusPopups::new();
        t.update_all(0, false, true, true, 0.0);
        g.put("popups_typing_first", &render_hud_card(t.lines(), t.accent(), font), "");
    }
    {
        let mut h = HelpOverlay::new();
        h.set_font(load());
        let (mut image, mut drawn) = (HudImage::default(), 0);
        let mut viewport;
        let mut step = 0;
        let mut shot = |g: &mut GoldenOut, h: &HelpOverlay, viewport: (u32, u32), changed: bool| {
            if h.generation() != drawn {
                let (im, c) = render_help_card(h.rows(), h.accent(), font, viewport.0, viewport.1, h.page());
                assert_eq!(&c, h.controls(), "overlay{step}: the overlay's controls are the drawn card's");
                image = im;
                drawn = h.generation();
            }
            let extra = format!(" {} {} {}{}", u8::from(changed), h.page(), h.pages(), controls_text(h.controls()));
            g.put(&format!("overlay{step}"), &image, &extra);
            step += 1;
        };
        let rows40 = golden_many_rows(40, 5);
        viewport = (960, 600);
        let c = h.set_viewport(viewport.0, viewport.1);
        shot(g, &h, viewport, c);
        let c = h.set(true, &rows40, 0.0, 0);
        shot(g, &h, viewport, c);
        let c = h.set(true, &rows40, 10.0, 1);
        shot(g, &h, viewport, c);
        let c = h.set(true, &rows40, 20.0, -1);
        shot(g, &h, viewport, c);
        let c = h.set(true, &rows40, 30.0, -1);
        shot(g, &h, viewport, c);
        let c = h.set_accent([0.9, 0.55, 0.05]);
        shot(g, &h, viewport, c);
        viewport = (1920, 1200);
        let c = h.set_viewport(viewport.0, viewport.1);
        shot(g, &h, viewport, c);
        let rows = golden_help_rows();
        let c = h.set(true, &rows, 40.0, 0);
        shot(g, &h, viewport, c);
        let c = h.set(false, &rows, 50.0, 0);
        shot(g, &h, viewport, c);
        let c = h.set(true, &rows, 60.0, 0);
        shot(g, &h, viewport, c);
    }
    {
        let mut t = TrackingHint::new();
        if font.is_some() {
            t.font_changed();
        }
        let card = |t: &TrackingHint| render_hud_card(&tracking_hint_lines(), t.accent(), font);
        t.generation();
        g.put("tracking_hint", &card(&t), "");
        t.set_accent([0.9, 0.55, 0.05]);
        t.generation();
        g.put("tracking_hint_accent", &card(&t), "");
    }
}

#[test]
fn cards_match_cpp_golden() {
    let real_desc = HUD_CARDS.lines().find_map(|l| l.strip_prefix("font ")).map(str::to_owned);
    let mut g = GoldenOut { font: "pixel", out: Vec::new() };
    golden_hud_cards(&mut g, None);
    golden_help_cards(&mut g, None);
    golden_overlays(&mut g, None);
    let mut want: Vec<&str> = HUD_CARDS.lines().take_while(|l| !l.starts_with("font ")).collect();
    let real = installed_font();
    match (&real, &real_desc) {
        (Some(f), Some(d)) if f.description() == d => {
            g.out.push(format!("font {d}"));
            g.font = "real";
            golden_hud_cards(&mut g, Some(f));
            golden_help_cards(&mut g, Some(f));
            golden_overlays(&mut g, Some(f));
            want = HUD_CARDS.lines().collect();
        }
        (f, d) => eprintln!(
            "real-font cards not compared: golden font {d:?}, here {:?}",
            f.as_ref().map(HudFont::description)
        ),
    }
    let bad: Vec<String> = want
        .iter()
        .zip(&g.out)
        .filter(|(w, o)| *w != o)
        .map(|(w, o)| format!("  cpp:  {w}\n  rust: {o}"))
        .collect();
    assert!(bad.is_empty(), "{} of {} cards differ from the C++:\n{}", bad.len(), want.len(), bad.join("\n"));
    assert_eq!(g.out.len(), want.len(), "the replay printed a different number of lines");
}
