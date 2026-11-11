//! `spike/tests/hud_font_test.cpp` in Rust: `HudFont` caches what FreeType returns, and the cache must never change what
//! is drawn or measured.
//!  - Cold vs warm: each query drawn first by a freshly loaded font equals the same query replayed from a long-lived
//!    font's caches, including after a flood of distinct glyphs past the cache bound and with `metrics` calls at other
//!    sizes interleaved.
//!  - UTF-8: any byte string draws and measures exactly like its decoding by the reference decoder below (invalid,
//!    truncated or non-continuation bytes give U+FFFD, one byte at a time; overlong forms, surrogates and values up to
//!    0x1FFFFF pass through), re-encoded as plain UTF-8.
//!
//!  - Parity with the C++ `HudFont` on the installed font: metrics, measures and drawings (every plot) of the ctest's
//!    queries and of single glyphs at several sizes in both faces print the lines of `golden/hud_font.txt`
//!    (`golden/hud_font.cpp`), byte for byte.
//!  - No font: a family that is not installed, and the system family with fontconfig reading an empty configuration
//!    (no font at all, in a child process: `FONTCONFIG_FILE` is process-wide), fail with the C++ reasons, and the
//!    pop-up cards then fall back to the built-in pixel font, byte-equal to the C++ fallback cards.
//!
//! The checks that need the installed font are skipped, with a note, when it is missing. The load failure reasons are
//! checked whatever is installed.

use std::process::Command;

use xr_renderer::hud::{FontMetrics, HUD_FONT_FAMILY, HudTextMetrics, render_hud_card, view_mode_lines};
use xr_renderer::hud_font::HudFont;
use xr_renderer::material::MD_DEFAULT_SEED;

#[derive(Debug, PartialEq)]
struct Query {
    plots: Vec<(i32, i32, f32)>,
    width: u32,
    metrics: FontMetrics,
}

fn query(font: &HudFont, text: &[u8], px: u32, bold: bool) -> Query {
    let metrics = font.metrics(px, bold);
    let width = font.measure_bytes(text, px, bold);
    let mut plots = Vec::new();
    font.draw_bytes(text, px, bold, 5, 50, &mut |x, y, c| plots.push((x, y, c)));
    Query { plots, width, metrics }
}

fn reference_decode(s: &[u8]) -> Vec<u32> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < s.len() {
        let c = s[i];
        let n: i32 = if c < 0x80 {
            0
        } else if c >> 5 == 6 {
            1
        } else if c >> 4 == 14 {
            2
        } else if c >> 3 == 30 {
            3
        } else {
            -1
        };
        if n < 0 || i + n as usize >= s.len() + usize::from(n == 0) {
            out.push(0xFFFD);
            i += 1;
            continue;
        }
        let mut cp = u32::from(match n {
            0 => c,
            1 => c & 0x1F,
            2 => c & 0x0F,
            _ => c & 0x07,
        });
        let mut ok = true;
        for k in 1..=n as usize {
            let d = s[i + k];
            if d >> 6 != 2 {
                ok = false;
                break;
            }
            cp = (cp << 6) | u32::from(d & 0x3F);
        }
        out.push(if ok { cp } else { 0xFFFD });
        i += if ok { n as usize + 1 } else { 1 };
    }
    out
}

/// Shortest form; the decoder accepts every value it produced (surrogates and values past U+10FFFF included).
fn encode(cps: &[u32]) -> Vec<u8> {
    let mut out = Vec::new();
    for &cp in cps {
        if cp < 0x80 {
            out.push(cp as u8);
        } else if cp < 0x800 {
            out.extend([0xC0 | (cp >> 6) as u8, 0x80 | (cp & 0x3F) as u8]);
        } else if cp < 0x10000 {
            out.extend([0xE0 | (cp >> 12) as u8, 0x80 | ((cp >> 6) & 0x3F) as u8, 0x80 | (cp & 0x3F) as u8]);
        } else {
            out.extend([
                0xF0 | (cp >> 18) as u8,
                0x80 | ((cp >> 12) & 0x3F) as u8,
                0x80 | ((cp >> 6) & 0x3F) as u8,
                0x80 | (cp & 0x3F) as u8,
            ]);
        }
    }
    out
}

/// The C++ test's queries; the last three are the same glyphs and sizes in the other face.
const QUERIES: [(&[u8], u32, bool); 12] = [
    (b"XR-2", 24, true),
    (b"Placement", 24, false),
    (b"Curvature  +12\xc2\xb0", 18, false),
    (b"\xe2\x86\x91\xe2\x86\x93 field  \xe2\x86\x90\xe2\x86\x92 adjust", 18, false),
    (b"\xef\x80\x81 nerd \xe4\xb8\xad", 30, true),
    (b"bad \xff\xfe trunc \xe2\x86", 24, false),
    (b"over \xc0\xaf \xf7\xbf\xbf\xbf \xed\xa0\x80", 12, true),
    (b"", 24, false),
    (b"Mm", 160, false),
    (b"XR-2", 24, false),
    (b"Placement", 24, true),
    (b"Mm", 160, true),
];

fn installed() -> Option<HudFont> {
    match HudFont::load(HUD_FONT_FAMILY) {
        Ok(font) => Some(font),
        Err(why) => {
            eprintln!("skipped: {why}");
            None
        }
    }
}

#[test]
fn the_installed_font_describes_its_faces() {
    let Some(font) = installed() else { return };
    let d = font.description();
    assert!(d == format!("{HUD_FONT_FAMILY} (regular + bold)") || d == format!("{HUD_FONT_FAMILY} (regular)"), "{d}");
}

#[test]
fn the_cache_never_changes_what_is_drawn_or_measured() {
    let Some(font) = installed() else { return };
    // Cold: a fresh font per query (nothing cached when it is drawn).
    let cold: Vec<Query> = QUERIES
        .iter()
        .map(|&(text, px, bold)| query(&HudFont::load(HUD_FONT_FAMILY).expect("load again"), text, px, bold))
        .collect();
    assert!(!cold[0].plots.is_empty() && cold[0].width > 0);
    // Coverage is FreeType's 8-bit grey level over 255, and only covered pixels are plotted.
    for r in &cold {
        for &(_, _, c) in &r.plots {
            let k = (c * 255.0).round();
            assert!((1.0..=255.0).contains(&k) && c == k / 255.0, "coverage {c} is not k/255");
        }
    }
    // Warm: one font, queries repeated with other sizes in between and a flood of glyphs past the cache bound.
    let all: Vec<u8> = (0x21u8..0x7f).collect();
    for round in 0..3 {
        for (i, &(text, px, bold)) in QUERIES.iter().enumerate() {
            font.metrics(px + 7, !bold); // a different size set on the face just before
            let warm = query(&font, text, px, bold);
            assert!(warm == cold[i], "round {round} query {i} ({:?} {px}{}) differs from a cold font", String::from_utf8_lossy(text), if bold { " bold" } else { "" });
        }
        for px in 8u32..60 {
            font.measure_bytes(&all, px, px & 1 == 1);
            font.draw_bytes(&all, px, px & 1 == 1, 0, 0, &mut |_, _, _| {});
            font.metrics(px, false);
        }
    }
}

#[test]
fn any_byte_string_decodes_like_the_reference() {
    let Some(font) = installed() else { return };
    // Random byte strings, biased toward lead and continuation bytes. (A small LCG instead of the C++ mt19937: the
    // property holds for any string.)
    let pool: [u8; 21] =
        [b'a', b'Z', b' ', b'%', 0x7f, 0x80, 0x8f, 0xbf, 0xc0, 0xc2, 0xdf, 0xe0, 0xe2, 0xed, 0xef, 0xf0, 0xf4, 0xf7, 0xf8, 0xfe, 0xff];
    let mut state: u64 = 20_261_102;
    let mut next = || {
        state = state.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
        (state >> 33) as usize
    };
    for t in 0..400 {
        let n = next() % 12;
        let raw: Vec<u8> = (0..n).map(|_| pool[next() % pool.len()]).collect();
        let clean = encode(&reference_decode(&raw));
        let bold = t & 1 == 1;
        assert!(query(&font, &raw, 20, bold) == query(&font, &clean, 20, bold), "utf-8 case {t} ({raw:x?}) decodes differently from the reference");
    }
    // The &str entry points, and the layout's view of the font, are the byte ones.
    let layout: &dyn HudTextMetrics = &font;
    assert_eq!(layout.measure("Placement", 24, false), font.measure_bytes(b"Placement", 24, false));
    assert_eq!(font.measure("Placement", 24, true), font.measure_bytes(b"Placement", 24, true));
    assert_eq!(layout.metrics(24, true), font.metrics(24, true));
}

const GOLDEN: &str = include_str!("../golden/hud_font.txt");

fn fnv(bytes: &[u8], mut h: u64) -> u64 {
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}
const FNV_START: u64 = 0xcbf29ce484222325;

/// "<plots> <hash>" of drawing `text` with its pen at `left` and its baseline at `baseline` (golden/hud_font.cpp).
fn drawing(font: &HudFont, text: &[u8], px: u32, bold: bool, left: i32, baseline: i32) -> String {
    let (mut h, mut n) = (FNV_START, 0usize);
    font.draw_bytes(text, px, bold, left, baseline, &mut |x, y, c| {
        for v in [x as u32, y as u32, c.to_bits()] {
            h = fnv(&v.to_le_bytes(), h);
        }
        n += 1;
    });
    format!("{n} {h}")
}

fn hex(s: &[u8]) -> String {
    if s.is_empty() { "-".into() } else { s.iter().map(|b| format!("{b:02x}")).collect() }
}

/// The installed-font lines of golden/hud_font.cpp, in its order.
fn font_lines(font: &HudFont) -> Vec<String> {
    let mut out = vec![format!("load {}", font.description())];
    for px in [8u32, 12, 18, 20, 24, 30, 48, 160] {
        for bold in [false, true] {
            let m = font.metrics(px, bold);
            out.push(format!("metrics {px} {} {} {}", u8::from(bold), m.ascent, m.descent));
        }
    }
    for &(text, px, bold) in &QUERIES {
        out.push(format!(
            "text {} {px} {} {} {}",
            hex(text),
            u8::from(bold),
            font.measure_bytes(text, px, bold),
            drawing(font, text, px, bold, 5, 50)
        ));
    }
    let cps: Vec<u32> = (0x20..0x7f)
        .chain([0xB0, 0xB7, 0x2190, 0x2191, 0x2192, 0x2193, 0x2026, 0xF001, 0xE0B0, 0x4E2D, 0xFFFD, 0x1F600])
        .collect();
    for px in [12u32, 18, 24, 30, 160] {
        for bold in [false, true] {
            for &cp in &cps {
                let s = encode(&[cp]);
                out.push(format!(
                    "glyph {px} {} {cp:x} {} {}",
                    u8::from(bold),
                    font.measure_bytes(&s, px, bold),
                    drawing(font, &s, px, bold, 3, px as i32)
                ));
            }
        }
    }
    out
}

#[test]
fn glyphs_and_metrics_match_the_cpp_font() {
    let Some(font) = installed() else { return };
    let want: Vec<&str> = GOLDEN.lines().take_while(|l| !l.starts_with("missing ")).collect();
    if !want[0].starts_with("load ") {
        eprintln!("skipped: the golden was written without the font ({})", want[0]);
        return;
    }
    let got = font_lines(&font);
    assert_eq!(got.len(), want.len(), "line count");
    let differ: Vec<String> =
        got.iter().zip(&want).filter(|(g, w)| g != *w).map(|(g, w)| format!("  rust {g}\n  c++  {w}")).collect();
    assert!(differ.is_empty(), "{} of {} lines differ from golden/hud_font.txt:\n{}", differ.len(), want.len(), differ.join("\n"));
}

/// The golden line that starts with `key `.
fn golden(key: &str) -> &'static str {
    GOLDEN.lines().find(|l| l.strip_prefix(key).is_some_and(|r| r.starts_with(' '))).unwrap_or_else(|| panic!("no {key} line"))
}

#[test]
fn a_missing_family_fails_with_the_cpp_reason() {
    let family = "XR Workspace No Such Font Family";
    let why = HudFont::load(family).err().expect("loaded");
    assert_eq!(why, format!("font not installed: {family}"));
    assert_eq!(format!("missing {why}"), golden("missing"));
}

const CHILD: &str = "XR_HUD_FONT_TEST_EMPTY_FONTCONFIG";

#[test]
fn no_font_at_all_falls_back_to_the_pixel_font() {
    if std::env::var_os(CHILD).is_some() {
        // In the child, fontconfig reads the empty configuration: no font is found and the cards use the pixel font.
        let font = HudFont::load(HUD_FONT_FAMILY);
        let why = font.as_ref().err().cloned().unwrap_or_default();
        assert_eq!(format!("empty_fontconfig {} {why}", if font.is_ok() { "loaded" } else { "failed" }), golden("empty_fontconfig"));
        let font = font.ok();
        for mode in 0u8..3 {
            let card = render_hud_card(&view_mode_lines(mode), MD_DEFAULT_SEED, font.as_ref().map(|f| f as &dyn HudTextMetrics));
            assert!(card.complete(), "mode {mode}: the renderer still gets a card");
            assert_eq!(card, render_hud_card(&view_mode_lines(mode), MD_DEFAULT_SEED, None), "mode {mode}: the pixel font");
            let want = GOLDEN.lines().filter(|l| l.starts_with("fallback_card ")).nth(mode.into()).expect("fallback_card line");
            assert_eq!(format!("fallback_card {mode} {} {} {}", card.width, card.height, fnv(&card.rgba, FNV_START)), want);
        }
        return;
    }
    let dir = std::env::temp_dir().join(format!("xr-hud-font-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let conf = dir.join("fonts.conf");
    std::fs::write(&conf, "<?xml version=\"1.0\"?>\n<fontconfig></fontconfig>\n").expect("fonts.conf");
    let out = Command::new(std::env::current_exe().expect("test binary"))
        .args(["--exact", "no_font_at_all_falls_back_to_the_pixel_font", "--nocapture", "--test-threads=1"])
        .env(CHILD, "1")
        .env("FONTCONFIG_FILE", &conf)
        .output()
        .expect("run the child");
    let _ = std::fs::remove_dir_all(&dir);
    let log = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    assert!(out.status.success() && log.contains("1 passed"), "child failed:\n{log}");
}
