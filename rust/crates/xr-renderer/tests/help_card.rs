//! `spike/tests/help_card_test.cpp` in Rust: the help card (Ctrl+Alt+H) is laid out from the output surface so it never
//! clips, with any number of rows. Checked on the rendered pixels, with the pixel font and (when fontconfig finds it) the
//! installed `HUD_FONT_FAMILY`:
//!  - at 1920x1200 @1.25 the real rows sit on one page at the roomiest text, and with three more rows still on one page,
//!    leaving `HUD_FIT_MARGIN` free on every side, every row its own text band and no ink in the card's padding;
//!  - any row count at every 1920x1200 scale from 1 to 2: the card fits, rows that do not fit flow onto further pages
//!    (every row on exactly one page, in order, none empty), every page is the same size and none puts ink in the padding,
//!    and a paged card keeps the smallest text;
//!  - a section stays on one page when it fits one; a section taller than a page is split;
//!  - `HelpOverlay` re-lays the card out when `set_viewport` changes the size, to the size `render_help_card` draws;
//!  - the page arrows and indicator are drawn inside their click rects;
//!  - text gets smaller only when it has to.
//!
//! The overlay's page turning and `help_geometry_json` (the rest of the C++ test) are in `tests/hud.rs`.

use xr_renderer::hud::{
    HELP_RISE_PX, HELP_ROW_PX_MAX, HELP_ROW_PX_MIN, HUD_FIT_MARGIN, HUD_FONT_FAMILY, HelpOverlay, HelpRect, HelpRow, HudImage,
    HudTextMetrics, help_card_fit, render_help_card,
};
use xr_renderer::hud_font::HudFont;

const SURFACE_W: u32 = 1536; // 1920x1200 output at scale 1.25, logical pixels
const SURFACE_H: u32 = 960;
const PAD: u32 = 34; // help card chrome (HELP_PAD, BORDER, RADIUS: M3 extra large)
const BORDER: u32 = 1;
const RADIUS: u32 = 28;
const ACCENT: [f32; 3] = [0.48, 0.64, 0.97];
const SCALES: [f32; 6] = [1.0, 1.25, 1.5, 1.6, 1.75, 2.0];

type Px = [i32; 4];

fn px(im: &HudImage, x: u32, y: u32) -> Px {
    let i = (y as usize * im.width as usize + x as usize) * 4;
    let p = &im.rgba[i..i + 4];
    [p[0].into(), p[1].into(), p[2].into(), p[3].into()]
}

fn near(a: Px, b: Px, tol: i32) -> bool {
    a.iter().zip(b).all(|(&x, y)| (x - y).abs() <= tol)
}

/// Left pad, mid height: plain card.
fn background(im: &HudImage) -> Px {
    px(im, PAD / 2, im.height / 2)
}

/// Rows y (in the interior, between the pads) that carry ink: a fully opaque pixel that is not the border.
fn ink_bands(im: &HudImage) -> Vec<(u32, u32)> {
    let mut bands: Vec<(u32, u32)> = Vec::new();
    let mut inside = false;
    let mut y = PAD;
    while y + PAD < im.height {
        let mut ink = false;
        let mut x = PAD;
        while x + PAD < im.width && !ink {
            ink = px(im, x, y)[3] == 255;
            x += 1;
        }
        if ink && !inside {
            bands.push((y, y));
        }
        if ink {
            bands.last_mut().expect("a band").1 = y;
        }
        inside = ink;
        y += 1;
    }
    bands
}

/// Opaque (ink) pixels inside card rect `r`.
fn ink_in(im: &HudImage, r: &HelpRect) -> usize {
    let mut n = 0;
    for y in r.y.max(0)..(r.y + r.h).min(im.height as i32) {
        for x in r.x.max(0)..(r.x + r.w).min(im.width as i32) {
            n += usize::from(px(im, x as u32, y as u32)[3] == 255);
        }
    }
    n
}

/// No ink inside the padding: everything between the border and the content area matches the plain background.
fn check_pads_clean(im: &HudImage, what: &str) {
    let bg = background(im);
    let (inner, outer, corner) = (BORDER + 2, PAD - 4, RADIUS + BORDER + 2);
    let mut bad = 0;
    for y in inner..im.height - inner {
        for x in inner..im.width - inner {
            let in_band = x < outer || y < outer || x >= im.width - outer || y >= im.height - outer;
            let in_corner = (x < corner || x >= im.width - corner) && (y < corner || y >= im.height - corner);
            if in_band && !in_corner && !near(px(im, x, y), bg, 3) {
                bad += 1;
            }
        }
    }
    assert_eq!(bad, 0, "{what}: {bad} pixels of ink inside the card padding (clipped or overflowing content)");
}

/// The card fits the surface at rest and during its fade-in drop.
fn check_fit(im: &HudImage, w: u32, h: u32, what: &str) {
    let at_rest = im.width + 2 * HUD_FIT_MARGIN <= w && im.height + 2 * HUD_FIT_MARGIN <= h;
    assert!(at_rest, "{what}: card {}x{} does not fit {w}x{h} with its margin", im.width, im.height);
    assert!(im.height + HELP_RISE_PX as u32 <= h, "{what}: card runs off the surface during its fade-in drop");
}

/// Exactly view.help_rows(), sections marked (tests/native_spike/test_resize.py checks).
fn help_rows() -> Vec<HelpRow> {
    let r = |k: &str, a: &str, section: bool| HelpRow { keys: k.into(), action: a.into(), section };
    vec![
        r("Ctrl+Alt+Up", "Maximize the selected desktop", false),
        r("Ctrl+Alt+Down", "Overview: any desktop; pills, bar, corners", false),
        r("Ctrl+Alt+Left / Right", "Previous / next desktop", false),
        r("Ctrl+Alt+[ / ]", "Less / more space between desktops", false),
        r("Ctrl+Alt+Space / R", "Recenter / reset to 3 flat desktops", false),
        r("Ctrl+Alt+V", "Maximized: follow / locked", true),
        r("Ctrl+Alt+P", "Toggle pose prediction", false),
        r("Ctrl+Alt+T", "Typing mode: XR keys off / on", false),
        r("Ctrl+Alt+D", "Privacy: main display off / on", false),
        r("Ctrl+Alt+E / I", "Background / immersive 3D: on or off", false),
        r("Ctrl+Alt+L", "Placement pill; R in it resets, levels", true),
        r("Ctrl+Alt+O / U / C", "VR video: file / web link / hide controls", false),
        r("Ctrl+Alt+H / K", "Help/binds; Ctrl+Alt+G/B: window to XR/back", true),
        r("Ctrl+Alt+PgUp / PgDn", "Help: previous / next page", false),
    ]
}

/// `n` rows in sections of `section` rows, distinct text per row.
fn many_rows(n: usize, section: usize) -> Vec<HelpRow> {
    (0..n)
        .map(|i| HelpRow {
            keys: format!("Ctrl+Alt+{}", i % 10),
            action: format!("Example action number {}", i + 1),
            section: i > 0 && i % section == 0,
        })
        .collect()
}

/// Every page of `rows` at w x h: fits, same size, clean pads, every row once (pixel font: one ink band per row).
fn check_pages(rows: &[HelpRow], f: Option<&dyn HudTextMetrics>, w: u32, h: u32, what: &str) {
    let fit = help_card_fit(rows, f, w, h);
    assert!(fit.fits && fit.pages() >= 1, "{what}: does not fit {w}x{h}");
    let mut total = 0;
    for &n in &fit.page_rows {
        assert!(n >= 1 || rows.is_empty(), "{what}: an empty page");
        total += n as usize;
    }
    assert_eq!(total, rows.len(), "{what}");
    if fit.pages() > 1 {
        assert_eq!(fit.row_px, HELP_ROW_PX_MIN, "{what}: paging keeps the smallest text; it never shrinks further");
    }
    for page in 0..fit.pages() {
        let (hc, controls) = render_help_card(rows, ACCENT, f, w, h, page);
        let name = format!("{what} page {}/{}", page + 1, fit.pages());
        assert!(hc.width == fit.width && hc.height == fit.height, "{name}");
        check_fit(&hc, w, h, &name);
        check_pads_clean(&hc, &name);
        assert!(controls.prev.is_empty() == (fit.pages() == 1) && controls.next.is_empty() == (fit.pages() == 1), "{name}");
        if f.is_none() {
            // Title, the page's rows, (indicator line, hint), footer: each its own band.
            let bands = ink_bands(&hc).len();
            let want = 1 + fit.page_rows[page as usize] as usize + if fit.pages() > 1 { 2 } else { 0 } + 1;
            assert_eq!(bands, want, "{name}: text bands");
        }
        if fit.pages() > 1 {
            // The arrows are drawn inside their click rects, inside the card.
            for r in [&controls.prev, &controls.indicator, &controls.next] {
                assert!(r.x >= 0 && r.y >= 0 && r.x + r.w <= hc.width as i32 && r.y + r.h <= hc.height as i32, "{name}: {r:?}");
                assert!(ink_in(&hc, r) > 0, "{name}: no ink in {r:?}");
            }
            assert!(controls.prev.x + controls.prev.w <= controls.next.x, "{name}");
        }
    }
    // The page number wraps: page `pages` is page 1 again.
    assert!(render_help_card(rows, ACCENT, f, w, h, fit.pages()).0.rgba == render_help_card(rows, ACCENT, f, w, h, 0).0.rgba);
}

fn check_font(font: Option<&HudFont>) {
    let f = font.map(|f| f as &dyn HudTextMetrics);
    let name = if f.is_some() { "installed font" } else { "pixel font" };
    // The real rows, and three more, on one page at full size at 1920x1200 @1.25.
    let real = help_rows();
    let mut more_rows = real.clone();
    for i in 0..3 {
        more_rows.push(HelpRow::new(format!("Ctrl+Alt+{}", i + 1), format!("Send the focused window to XR-{}", i + 1)));
    }
    for (rs, is_real) in [(&real, true), (&more_rows, false)] {
        let rows_name = format!("help card ({} rows), {name}", rs.len());
        let fit = help_card_fit(rs, f, SURFACE_W, SURFACE_H);
        let (hc, _) = render_help_card(rs, ACCENT, f, SURFACE_W, SURFACE_H, 0);
        println!("{rows_name}: {}x{} of {SURFACE_W}x{SURFACE_H}, row text {} px, {} page(s)", hc.width, hc.height, fit.row_px, fit.pages());
        assert!(fit.fits && fit.pages() == 1 && fit.width == hc.width && fit.height == hc.height, "{rows_name}");
        if is_real {
            assert_eq!(fit.row_px, HELP_ROW_PX_MAX, "the real rows keep the roomiest text");
        }
        check_pages(rs, f, SURFACE_W, SURFACE_H, &rows_name);
    }
    // Any row count, every scale from 1 to 2: pages, never clipped.
    for scale in SCALES {
        let (w, h) = ((1920.0 / scale) as u32, (1200.0 / scale) as u32);
        for n in [1, 5, 16, 24, 40, 64] {
            check_pages(&many_rows(n, 5), f, w, h, &format!("{n} rows at {w}x{h}, {name}"));
        }
        check_pages(&help_rows(), f, w, h, &format!("real rows at {w}x{h}, {name}"));
        assert!(help_card_fit(&many_rows(64, 5), f, w, h).pages() > 1);
        // As the renderer drives it: viewport on configure, re-laid out when it changes, to the size it draws.
        let mut overlay = HelpOverlay::new();
        if font.is_some() {
            overlay.set_font(Some(Box::new(HudFont::load(HUD_FONT_FAMILY).expect("load again"))));
        }
        overlay.set(true, &help_rows(), 0.0, 0);
        let before = overlay.generation();
        assert!(overlay.set_viewport(w, h) && overlay.generation() > before);
        let (hc, controls) = render_help_card(overlay.rows(), ACCENT, f, w, h, overlay.page());
        let fit = overlay.fit();
        assert!(fit.width == hc.width && fit.height == hc.height && *overlay.controls() == controls);
    }
    // Sections: with room for about one and a half sections per page, every page starts at a section; a section taller
    // than a page is split across pages.
    let rows = many_rows(30, 5);
    let one = help_card_fit(&rows[..5], f, 0, 0);
    let seven = help_card_fit(&rows[..7], f, 0, 0);
    // A surface holding a 7-row card (incl. its paged footer, about two lines more) but not 10 rows.
    let h = seven.height + 2 * HUD_FIT_MARGIN + 2 * (one.height / 7);
    let fit = help_card_fit(&rows, f, 1920, h);
    assert!(fit.fits && fit.pages() > 1);
    let mut start = 0;
    for &n in &fit.page_rows {
        assert_eq!(start % 5, 0, "a page starts mid-section at row {start} ({name})");
        start += n;
    }
    let split = help_card_fit(&many_rows(30, 30), f, 1920, h); // one 30-row section: split, pages full
    assert!(split.pages() > 1 && split.page_rows[0] > 5);
    let mut last_px = HELP_ROW_PX_MAX;
    let mut h = 1200;
    while h >= 500 {
        let px_now = help_card_fit(&help_rows(), f, 1920, h).row_px;
        assert!(px_now <= last_px && px_now >= HELP_ROW_PX_MIN);
        last_px = px_now;
        h -= 20;
    }
}

#[test]
fn pixel_font_help_card_pages_without_clipping() {
    check_font(None);
}

#[test]
fn installed_font_help_card_pages_without_clipping() {
    match HudFont::load(HUD_FONT_FAMILY) {
        Ok(font) => check_font(Some(&font)),
        Err(why) => eprintln!("note: {HUD_FONT_FAMILY} not available ({why}); real-font fit not checked"),
    }
}
