//! The frame scenarios of spike/tests/gl_bench.cpp, shared by the GlCompositor tests (gl_frame, gl_parity): the same
//! synthetic desktops, the same scenario list (the ones drawn without a background or video), the same per-frame inputs.
//!
//! The cards (pop-up, help, glass panels, handle atlas) are the ones gl_bench.cpp renders and `--dump` writes, drawn by
//! the Rust rasters (byte-equal to the C++ ones) in the installed HUD font, else the pixel font. With XR_GL_CARDS set to
//! a `gl_bench --dump DIR` directory each card is also checked equal to the dumped C++ one.

#![allow(dead_code)]

use std::f64::consts::PI;
use std::path::Path;
use std::rc::Rc;

use xr_renderer::gl::GlCompositor;
use xr_renderer::glass::{GlassFrame, LevelSegment, GLASS_HOVER};
use xr_renderer::glass_raster::{render_glass_panel, render_handle_atlas};
use xr_renderer::glass_theme::GLASS_THEME_DEFAULT;
use xr_renderer::hud::{
    prediction_lines, render_help_card, render_hud_card, view_mode_lines, HelpRow, HudImage,
    HudTextMetrics, PlacementMenuState, HUD_FONT_FAMILY,
};
use xr_renderer::hud_font::HudFont;
use xr_renderer::pose::Mat3;
use xr_renderer::view::{
    blended_layout, Placement, PointerLook, Size, FREE_POINTER_HELD_LEVEL, FREE_POINTER_LEVEL,
};

pub const DESKTOP_W: u32 = 1920;
pub const DESKTOP_H: u32 = 1080;
pub const ACCENT: [u8; 3] = [0x7a, 0xa2, 0xf7];
pub const DEFAULT_W: u32 = 1536;
pub const DEFAULT_H: u32 = 960;

/// gl_bench.cpp desktop_pixels: a busy, non-repeating desktop (gradients, a grid and text-like noise), BGRA.
pub fn desktop_pixels(index: u32, variant: u32) -> Vec<u8> {
    let mut px = vec![0u8; (DESKTOP_W * DESKTOP_H * 4) as usize];
    let mut seed = 0x9e37_79b9u32
        .wrapping_mul(index + 1)
        .wrapping_add(0x85eb_ca6bu32.wrapping_mul(variant + 1));
    for y in 0..DESKTOP_H {
        for x in 0..DESKTOP_W {
            seed = seed.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            let p = &mut px[((y * DESKTOP_W + x) * 4) as usize..][..4];
            let grid = x % 64 < 2 || y % 48 < 2;
            p[0] = if grid {
                220
            } else {
                ((x * 255 / DESKTOP_W + variant * 40) & 0xff) as u8
            };
            p[1] = if grid {
                220
            } else {
                ((y * 255 / DESKTOP_H + index * 70) & 0xff) as u8
            };
            p[2] = (((seed >> 24) & 0x3f) + index * 60) as u8;
            p[3] = 0xff;
        }
    }
    px
}

#[derive(Clone, Copy)]
pub struct Scenario {
    pub name: &'static str,
    pub zoom: f32,
    pub position: f32,
    pub placements: [Placement; 3],
    pub head_motion: bool,
    pub pills: bool,
    pub hover: i32,
    pub expand: f32,
    pub panel: bool,
    pub panel_drag: bool,
    pub help: bool,
    pub popup: bool,
    pub handle: bool,
    pub pointer: bool,
    pub drag_target: i32,
    pub ghost: bool,
    pub look: PointerLook,
    pub held: bool,
    pub upload_each_frame: bool,
    pub roll: bool,
    pub surface: bool,
}

impl Scenario {
    pub fn new(name: &'static str) -> Self {
        Self {
            name,
            zoom: 0.0,
            position: 1.0,
            placements: [Placement::default(); 3],
            head_motion: false,
            pills: false,
            hover: -1,
            expand: 0.0,
            panel: false,
            panel_drag: false,
            help: false,
            popup: false,
            handle: false,
            pointer: false,
            drag_target: -1,
            ghost: false,
            look: PointerLook::Plain,
            held: false,
            upload_each_frame: false,
            roll: false,
            surface: false,
        }
    }
}

/// gl_bench.cpp scenarios(), in its order, up to the first one with a video or a background (not ported here).
pub fn scenarios() -> Vec<Scenario> {
    let curved = Placement {
        curvature: 1.0,
        ..Placement::default()
    };
    let tilted_left = Placement {
        x: -0.05,
        curvature: 0.7,
        distance: 1.2,
        ..Placement::default()
    };
    let tilted_right = Placement {
        x: 0.05,
        y: 0.08,
        curvature: 0.7,
        ..Placement::default()
    };
    let s = Scenario::new;
    let mut out = vec![
        Scenario {
            zoom: 1.0,
            ..s("zoomed")
        },
        Scenario {
            zoom: 1.0,
            placements: [Placement::default(), curved, Placement::default()],
            ..s("zoomed_curved")
        },
        Scenario {
            zoom: 1.0,
            popup: true,
            ..s("zoomed_popup")
        },
        Scenario {
            zoom: 1.0,
            upload_each_frame: true,
            ..s("zoomed_shm_upload")
        },
        s("overview"),
        Scenario {
            pills: true,
            hover: 1,
            pointer: true,
            handle: true,
            ..s("overview_pills")
        },
        Scenario {
            pills: true,
            pointer: true,
            placements: [tilted_left, curved, tilted_right],
            ..s("overview_curved_pills")
        },
        Scenario {
            pills: true,
            pointer: true,
            head_motion: true,
            ..s("overview_head_motion")
        },
        Scenario {
            pills: true,
            expand: 0.6,
            pointer: true,
            ..s("panel_morph")
        },
        Scenario {
            pills: true,
            expand: 1.0,
            panel: true,
            pointer: true,
            ..s("panel_open")
        },
        Scenario {
            pills: true,
            expand: 1.0,
            panel: true,
            panel_drag: true,
            pointer: true,
            ..s("panel_slider_drag")
        },
        Scenario {
            pills: true,
            expand: 1.0,
            panel: true,
            panel_drag: true,
            pointer: true,
            placements: [tilted_left, curved, tilted_right],
            ..s("panel_curved_drag")
        },
        Scenario {
            pills: true,
            help: true,
            pointer: true,
            ..s("help_card")
        },
    ];
    for (name, look) in [
        ("look_resize_ew", PointerLook::ResizeEw),
        ("look_resize_nwse", PointerLook::ResizeNwse),
        ("look_move", PointerLook::Move),
        ("look_rotate", PointerLook::Rotate),
        ("look_text", PointerLook::Text),
    ] {
        out.push(Scenario {
            pills: true,
            pointer: true,
            look,
            ..s(name)
        });
    }
    out.push(Scenario {
        pills: true,
        pointer: true,
        look: PointerLook::Text,
        held: true,
        ..s("look_text_held")
    });
    out.push(Scenario {
        pills: true,
        pointer: true,
        ghost: true,
        drag_target: 2,
        placements: [tilted_left, curved, tilted_right],
        ..s("window_drag")
    });
    let rolled = Placement {
        roll: 0.14,
        ..tilted_right
    };
    out.push(Scenario {
        pills: true,
        pointer: true,
        roll: true,
        placements: [tilted_left, curved, rolled],
        ..s("level_bars")
    });
    out.push(Scenario {
        pills: true,
        pointer: true,
        handle: true,
        placements: [tilted_left, curved, rolled],
        ..s("level_bars_cap_hot")
    });
    let resized = Placement {
        distance: 0.6,
        ..Placement::default()
    };
    out.push(Scenario {
        pills: true,
        expand: 1.0,
        panel: true,
        pointer: true,
        roll: true,
        surface: true,
        placements: [Placement::default(), resized, Placement::default()],
        ..s("level_bars_resized")
    });
    out
}

/// gl_bench.cpp head: Ry * Rx, row-major; identity without motion.
pub fn head(frame: i32, motion: bool) -> Mat3 {
    if !motion {
        return xr_renderer::pose::IDENTITY;
    }
    let yaw = 0.06 * (frame as f32 * 0.05).sin();
    let pitch = 0.03 * (frame as f32 * 0.04).cos();
    let (cy, sy, cp, sp) = (yaw.cos(), yaw.sin(), pitch.cos(), pitch.sin());
    [cy, sy * sp, sy * cp, 0.0, cp, -sp, -sy, cy * sp, cy * cp]
}

/// The renderer's FOV fallback (46 degrees diagonal), as tan of the half angles.
pub fn half_fov(w: u32, h: u32) -> (f32, f32) {
    let d = (46.0 * PI / 360.0).tan();
    let a = f64::from(w) / f64::from(h);
    (
        (d * a / (1.0 + a * a).sqrt()) as f32,
        (d / (1.0 + a * a).sqrt()) as f32,
    )
}

pub fn variant(s: &Scenario, frame: i32) -> u32 {
    if s.upload_each_frame {
        (frame & 1) as u32
    } else {
        0
    }
}

pub struct Resources {
    pub desktops: [[Vec<u8>; 2]; 3],
    pub popup: Rc<HudImage>,
    pub help: Rc<HudImage>,
    pub panel_static: Rc<HudImage>,
    pub panel_drag: Vec<Rc<HudImage>>,
    pub handles: Rc<HudImage>,
}

/// A `u32 width, u32 height, RGBA` file from `gl_bench --dump`.
pub fn read_image(path: &Path) -> Result<HudImage, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    if bytes.len() < 8 {
        return Err(format!("{}: too short", path.display()));
    }
    let width = u32::from_ne_bytes(bytes[0..4].try_into().unwrap());
    let height = u32::from_ne_bytes(bytes[4..8].try_into().unwrap());
    let im = HudImage {
        width,
        height,
        rgba: bytes[8..].to_vec(),
    };
    if im.complete() {
        Ok(im)
    } else {
        Err(format!("{}: not {width}x{height} RGBA", path.display()))
    }
}

/// gl_bench.cpp kAccent: the cards' theme colour (ACCENT as floats).
pub fn accent() -> [f32; 3] {
    ACCENT.map(|c| f32::from(c) / 255.0)
}

/// gl_bench.cpp panel_state: desktop 1's placement panel open on the x field, at `x` tenths of a degree.
pub fn panel_state(x: i32) -> PlacementMenuState {
    let mut st = PlacementMenuState {
        open: true,
        desktop: 1,
        field: 0,
        ..PlacementMenuState::default()
    };
    st.placements[1] = [x, 0, 100, 0];
    st
}

/// gl_bench.cpp drag_x: the x slider (tenths of a degree) swept back and forth.
pub fn drag_x(frame: u32) -> i32 {
    -40 + (frame as i32 * 7) % 81
}

/// gl_bench.cpp load_resources's help rows.
pub fn help_rows() -> Vec<HelpRow> {
    let mut rows = vec![
        HelpRow::new("Ctrl+Alt+H", "Show / hide this help"),
        HelpRow::new("Ctrl+Alt+L", "Placement pill of the selected desktop"),
        HelpRow::new("Ctrl+Alt+Z", "Zoom in / out"),
        HelpRow::new("Ctrl+Alt+Left/Right", "Select a desktop"),
        HelpRow::new("Ctrl+Alt+R", "Recentre"),
        HelpRow::new("Ctrl+Alt+M", "Viewing mode"),
        HelpRow::new("Ctrl+Alt+T", "Typing mode"),
    ];
    rows[5].section = true;
    rows
}

/// The installed HUD font as gl_bench.cpp loads it, else None (the cards use the pixel font, as the C++ ones then do).
pub fn hud_font() -> Option<HudFont> {
    match HudFont::load(HUD_FONT_FAMILY) {
        Ok(f) => Some(f),
        Err(why) => {
            eprintln!("note: {why}; cards use the pixel font");
            None
        }
    }
}

/// The cards, checked against XR_GL_CARDS when set.
pub fn load_resources() -> Resources {
    load_resources_from(std::env::var_os("XR_GL_CARDS").as_deref().map(Path::new))
}

/// gl_bench.cpp load_resources at DEFAULT_W x DEFAULT_H: the desktops and every card its `--dump` writes for these
/// scenarios, by the Rust rasters. With `cards` (a `gl_bench --dump DIR` directory) each card must equal the dumped
/// C++ one byte for byte (both draw in the installed font, or both in the pixel font); a difference panics.
pub fn load_resources_from(cards: Option<&Path>) -> Resources {
    let desktops = [0, 1, 2].map(|i| [desktop_pixels(i, 0), desktop_pixels(i, 1)]);
    let font = hud_font();
    let f = font.as_ref().map(|f| f as &dyn HudTextMetrics);
    let a = accent();
    let check = |name: &str, im: HudImage| {
        if let Some(dir) = cards {
            let cpp = read_image(&dir.join(format!("{name}.rgba")))
                .unwrap_or_else(|e| panic!("cards: {e}"));
            assert!(
                cpp.width == im.width && cpp.height == im.height && cpp.rgba == im.rgba,
                "card {name}: Rust {}x{} differs from the C++ {}x{} in {}",
                im.width,
                im.height,
                cpp.width,
                cpp.height,
                dir.display()
            );
        }
        Rc::new(im)
    };
    let popup = check("popup", render_hud_card(&view_mode_lines(0), a, f));
    check("video", render_hud_card(&prediction_lines(true), a, f));
    let help = check(
        "help",
        render_help_card(&help_rows(), a, f, DEFAULT_W, DEFAULT_H, 0).0,
    );
    let panel_static = check(
        "panel",
        render_glass_panel(&panel_state(15), f, GLASS_THEME_DEFAULT),
    );
    let panel_drag = (0..81)
        .map(|i| {
            check(
                &format!("panel_drag{i}"),
                render_glass_panel(&panel_state(drag_x(i)), f, GLASS_THEME_DEFAULT),
            )
        })
        .collect();
    let handles = check("handles", render_handle_atlas(a, f));
    Resources {
        desktops,
        popup,
        help,
        panel_static,
        panel_drag,
        handles,
    }
}

/// gl_bench.cpp panel_image: the static panel (generation 1), or the slider drag's frame `frame` (100 + frame).
pub fn panel_image(r: &Resources, s: &Scenario, frame: i32) -> (Rc<HudImage>, u64) {
    if !s.panel_drag {
        return (r.panel_static.clone(), 1);
    }
    (
        r.panel_drag[frame as usize % r.panel_drag.len()].clone(),
        100 + frame as u64,
    )
}

/// gl_bench.cpp draw_frame: everything wayland.cpp hands the compositor for one frame, then the draw. `upload`
/// re-uploads the desktops (variant(s, frame)) first. Returns whether every call succeeded.
pub fn draw_frame(
    gl: &mut GlCompositor,
    r: &Resources,
    s: &Scenario,
    frame: i32,
    w: u32,
    h: u32,
    upload: bool,
) -> bool {
    draw_frame_timed(gl, r, s, frame, w, h, upload).ok
}

/// What draw_frame_timed measured: the gl_bench.cpp draw_frame return (wall time of the draw call alone) and its
/// `upload_us` out value (the uploads before it), in microseconds.
pub struct Timed {
    pub ok: bool,
    pub upload_us: f64,
    pub draw_us: f64,
}

/// draw_frame with the draw call and the uploads timed apart, as gl_bench.cpp's bench mode reads them.
pub fn draw_frame_timed(
    gl: &mut GlCompositor,
    r: &Resources,
    s: &Scenario,
    frame: i32,
    w: u32,
    h: u32,
    upload: bool,
) -> Timed {
    let mut ok = true;
    let t = std::time::Instant::now();
    if upload {
        let v = variant(s, frame) as usize;
        for (i, d) in r.desktops.iter().enumerate() {
            ok &= gl.upload(i, &d[v], DESKTOP_W, DESKTOP_H, DESKTOP_W * 4);
        }
    }
    let upload_us = if upload {
        t.elapsed().as_secs_f64() * 1e6
    } else {
        0.0
    };
    let (half_w, half_h) = half_fov(w, h);
    gl.set_popup(s.popup.then(|| r.popup.clone()), 1, 1.0);
    let sizes = [Size {
        w: DESKTOP_W as f32,
        h: DESKTOP_H as f32,
    }; 3];
    let tiles = blended_layout(&sizes, w as f32, h as f32, 40.0, s.zoom, s.position);
    gl.set_help(s.help.then(|| r.help.clone()), 1, 1.0, 0.0);
    let mut glass = GlassFrame {
        pills: s.pills,
        ..GlassFrame::default()
    };
    if s.hover >= 0 {
        glass.pill_levels[s.hover as usize] = GLASS_HOVER;
    }
    glass.expand[1] = s.expand;
    if s.expand > 0.0 {
        glass.top = 1;
    }
    let mut image = None;
    if s.panel {
        let (im, generation) = panel_image(r, s, frame);
        glass.content = 1;
        glass.generation = generation;
        glass.content_alpha = 1.0;
        image = Some(im);
    }
    if s.roll || s.handle {
        glass.bars = [true, true, true, false];
    }
    if s.roll {
        glass.bar_hover = 1;
        glass.bar_segment = LevelSegment::Ccw;
    }
    if s.handle {
        glass.cap_hover = 1;
        glass.cap_corner = 3;
    }
    if s.surface {
        glass.surface_desktop = 1;
        glass.surface = Some(Placement::default());
    }
    gl.set_glass(&glass, image, None);
    let level = if s.ghost || s.held {
        FREE_POINTER_HELD_LEVEL
    } else {
        FREE_POINTER_LEVEL
    };
    gl.set_free_pointer(
        s.pointer,
        w as f32 * 0.55,
        h as f32 * 0.42,
        level,
        false,
        0.0,
        0.0,
        s.look,
    );
    gl.set_window_drag(
        if s.ghost { 72.0 } else { 0.0 },
        if s.ghost { 45.0 } else { 0.0 },
        s.drag_target,
    );
    let t = std::time::Instant::now();
    let view = head(frame, s.head_motion);
    ok &= gl.draw_and_swap(
        &tiles,
        &s.placements,
        s.position as i32,
        1.0 - s.zoom,
        &view,
        half_w,
        half_h,
        None,
    );
    Timed {
        ok,
        upload_us,
        draw_us: t.elapsed().as_secs_f64() * 1e6,
    }
}

/// gl_bench.cpp make: an offscreen compositor with the accent, the handle atlas and the three desktops (variant 0).
pub fn make(w: u32, h: u32, r: &Resources) -> Result<GlCompositor, String> {
    let mut gl = GlCompositor::create_offscreen(w, h, 3)?;
    gl.set_accent(ACCENT[0], ACCENT[1], ACCENT[2]);
    gl.set_handle_atlas(r.handles.clone());
    for (i, d) in r.desktops.iter().enumerate() {
        gl.upload(i, &d[0], DESKTOP_W, DESKTOP_H, DESKTOP_W * 4);
    }
    Ok(gl)
}

/// gl_bench.cpp fnv: FNV-1a, 64 bit.
pub fn fnv(bytes: &[u8], mut h: u64) -> u64 {
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(1_099_511_628_211);
    }
    h
}
pub const FNV_OFFSET: u64 = 1_469_598_103_934_665_603;
