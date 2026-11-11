//! The VR video controls card (`render_video_card`) against the C++ one, and the card checks of
//! `spike/tests/video_test.cpp` (`labels`, `card`):
//!  - pixel parity: every case of `golden/video_card.cpp` renders byte-equal to the C++ card (same size, same FNV-1a of
//!    the RGBA bytes) in the built-in pixel font and, when fontconfig finds `HUD_FONT_FAMILY`, in the installed font
//!    (the "real" golden lines come from this machine's font file: regenerate them when it changes);
//!  - `format_clock` and the key hints;
//!  - every state, in both fonts, is dimmed like the help card (nothing above `CARD_MAX_LEVEL`) and still readable;
//!  - the quality menu keeps those limits and re-renders when the selection or the rows change.

use xr_renderer::hud::{CARD_MAX_LEVEL, HUD_FONT_FAMILY, HudImage, HudTextMetrics};
use xr_renderer::hud_font::HudFont;
use xr_renderer::video::card::{MenuRow, VideoCardState, format_clock, render_video_card, video_card_hints, video_menu_hints};
use xr_renderer::video::math::{Format, Projection, State, Stereo};

fn row(label: &str, value: &str) -> MenuRow {
    MenuRow { label: label.into(), value: value.into() }
}

fn base() -> VideoCardState {
    VideoCardState {
        title: "beach_360.mp4".into(),
        format: Format::new(Projection::Equirect180, Stereo::Sbs, 180),
        state: State::Playing,
        position_s: 75,
        duration_s: 300,
        ..VideoCardState::default()
    }
}

/// The cases of `golden/video_card.cpp`, in its order.
fn cases() -> Vec<(&'static str, VideoCardState, [f32; 3])> {
    let mut out = vec![("default", VideoCardState::default(), [0.0; 3])];
    for (name, st) in [
        ("off", State::Off),
        ("loading", State::Loading),
        ("playing", State::Playing),
        ("paused", State::Paused),
        ("ended", State::Ended),
        ("error", State::Error),
    ] {
        let mut s = base();
        s.state = st;
        if st == State::Error {
            s.detail = "unrecognized file format".into();
        }
        out.push((name, s, [0.0; 3]));
    }
    let with = |f: &dyn Fn(&mut VideoCardState)| {
        let mut s = base();
        f(&mut s);
        s
    };
    out.push(("no_duration", with(&|s| (s.position_s, s.duration_s) = (3723, 0)), [0.0; 3]));
    out.push(("past_end", with(&|s| (s.position_s, s.duration_s) = (4000, 3723)), [0.0; 3]));
    out.push(("sliver", with(&|s| (s.position_s, s.duration_s) = (1, 7200)), [0.0; 3]));
    out.push(("full", with(&|s| s.position_s = 300), [0.0; 3]));
    out.push((
        "long_fisheye",
        with(&|s| {
            s.title = "a much longer title for a video that widens the card past its minimum width.mkv".into();
            s.format = Format::new(Projection::Fisheye, Stereo::Tb, 190);
        }),
        [0.0; 3],
    ));
    out.push(("accent", with(&|s| s.format = Format::default()), [0.48, 0.64, 0.97]));
    let mut m = base();
    m.menu = true;
    m.menu_rows = vec![row("Texture filter", "Catmull-Rom"), row("Deband", "Off")];
    out.push(("menu0", m.clone(), [0.0; 3]));
    m.menu_selected = 1;
    out.push(("menu1", m.clone(), [0.0; 3]));
    m.menu_rows.push(row("Third", "On"));
    out.push(("menu3", m.clone(), [0.9, 0.3, 0.2]));
    m.menu_selected = 7;
    out.push(("menu_none", m.clone(), [0.0; 3]));
    m.detail = "network error".into();
    m.state = State::Error;
    out.push(("menu_error", m, [0.0; 3]));
    out.push(("menu_empty", with(&|s| s.menu = true), [0.0; 3]));
    out
}

fn fnv1a(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf2_9ce4_8422_2325, |h, &b| (h ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3))
}

fn font() -> Option<HudFont> {
    match HudFont::load(HUD_FONT_FAMILY) {
        Ok(f) => Some(f),
        Err(why) => {
            eprintln!("{HUD_FONT_FAMILY} not found ({why}): pixel font only");
            None
        }
    }
}

fn brightest(im: &HudImage) -> u8 {
    im.rgba.chunks_exact(4).flat_map(|p| p[..3].iter().copied()).max().unwrap_or(0)
}

#[test]
fn matches_cpp_card_bytes() {
    let real = font();
    let golden = include_str!("../golden/video_card.txt");
    let mut checked = 0;
    for line in golden.lines() {
        let f: Vec<&str> = line.split(' ').collect();
        let font: Option<&dyn HudTextMetrics> = match f[1] {
            "pixel" => None,
            _ => match &real {
                Some(r) => Some(r),
                None => continue,
            },
        };
        let (_, s, accent) = cases().into_iter().find(|c| c.0 == f[0]).unwrap_or_else(|| panic!("no case {}", f[0]));
        let im = render_video_card(&s, accent, font);
        let got = format!("{} {} {} {} {}", f[0], f[1], im.width, im.height, fnv1a(&im.rgba));
        assert_eq!(got, line, "card {} in the {} font differs from the C++ one", f[0], f[1]);
        checked += 1;
    }
    assert!(checked >= cases().len(), "pixel-font goldens missing");
}

#[test]
fn clock_and_hints() {
    assert_eq!(format_clock(0.0), "0:00");
    assert_eq!(format_clock(7.9), "0:07");
    assert_eq!(format_clock(245.0), "4:05");
    assert_eq!(format_clock(3723.0), "1:02:03");
    assert_eq!(format_clock(-3.0), "0:00");
    assert_eq!(format_clock(f64::NAN), "0:00");
    assert_eq!(format_clock(f64::INFINITY), "0:00");
    for ascii in [true, false] {
        assert_eq!(video_card_hints(ascii).len(), 7);
        assert_eq!(video_menu_hints(ascii).len(), 5);
        assert_eq!(video_menu_hints(ascii).last().map(String::as_str), Some("Changes apply now and are saved."));
    }
    assert!(video_card_hints(true).iter().chain(&video_menu_hints(true)).all(|h| h.is_ascii()));
    assert_eq!(video_card_hints(false)[1], "←/→  10 s");
}

#[test]
fn every_state_is_dimmed_and_readable() {
    let real = font();
    let fonts: Vec<Option<&dyn HudTextMetrics>> =
        std::iter::once(None).chain(real.as_ref().map(|f| Some(f as &dyn HudTextMetrics))).collect();
    let mut s = base();
    for &font in &fonts {
        for st in [State::Loading, State::Playing, State::Paused, State::Ended, State::Error] {
            s.state = st;
            s.detail = if st == State::Error { "unrecognized file format".into() } else { String::new() };
            let im = render_video_card(&s, [0.0; 3], font);
            assert!(im.width > 300 && im.height > 100);
            assert_eq!(im.rgba.len(), im.width as usize * im.height as usize * 4);
            let b = brightest(&im);
            assert!(b <= CARD_MAX_LEVEL, "{st:?}: {b} above the OLED cap"); // micro-OLED rule: dimmed like the help card
            assert!(b > 80, "{st:?}: {b} unreadable");
        }
    }
    // The quality menu: the same OLED limits, rows in place of the format line and hints, and a re-render when the
    // selection or a value changes.
    let mut m = base();
    let plain = render_video_card(&m, [0.0; 3], None);
    m.menu = true;
    m.menu_rows = vec![row("Texture filter", "Catmull-Rom"), row("Deband", "Off")];
    for &font in &fonts {
        let b = brightest(&render_video_card(&m, [0.0; 3], font));
        assert!(b <= CARD_MAX_LEVEL && b > 80, "menu: {b}");
    }
    let first = render_video_card(&m, [0.0; 3], None);
    assert_ne!(first.rgba, plain.rgba);
    m.menu_selected = 1;
    let second = render_video_card(&m, [0.0; 3], None);
    assert_ne!(second.rgba, first.rgba);
    m.menu_rows.push(row("Third", "On"));
    assert!(render_video_card(&m, [0.0; 3], None).height > second.height); // a row more: a taller card
    assert_ne!(m, s);
}
