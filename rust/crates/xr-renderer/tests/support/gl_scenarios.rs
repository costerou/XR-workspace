//! The frame scenarios of spike/tests/gl_bench.cpp, shared by the GlCompositor tests (gl_frame, gl_parity): the same
//! synthetic desktops, the same scenario list (the ones drawn without a background or video), the same per-frame inputs.
//!
//! The cards (pop-up, help, glass panels, handle atlas) are rendered on the CPU by code not ported yet (the font). With
//! XR_GL_CARDS set to a directory written by `gl_bench --dump DIR` the scenarios draw the C++ cards themselves, so the
//! frames can be compared with the C++ ones; without it they draw synthetic cards of the same kind.

#![allow(dead_code)]

use std::f64::consts::PI;
use std::path::Path;
use std::rc::Rc;

use xr_renderer::gl::GlCompositor;
use xr_renderer::glass::{GLASS_HOVER, GlassFrame, HANDLE_ATLAS_H, HANDLE_ATLAS_W, LevelSegment};
use xr_renderer::hud::HudImage;
use xr_renderer::pose::Mat3;
use xr_renderer::view::{
    FREE_POINTER_HELD_LEVEL, FREE_POINTER_LEVEL, Placement, PointerLook, Size, blended_layout,
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
    /// The cards are the C++ ones (XR_GL_CARDS), not synthetic.
    pub from_cpp: bool,
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

/// A premultiplied card: a dim rounded-ish block with a lighter band at row `band` (a slider's position, a text line).
fn synthetic_card(width: u32, height: u32, band: u32, tint: [u8; 3]) -> HudImage {
    let mut rgba = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let edge = x.min(width - 1 - x).min(y).min(height - 1 - y);
            let a: u32 = if edge == 0 { 90 } else { 210 };
            let lit = (y / 4 == band / 4 && x > width / 8 && x < width * 7 / 8)
                || ((x / 6 + y / 9) % 7 == 0);
            let p = &mut rgba[((y * width + x) * 4) as usize..][..4];
            for c in 0..3 {
                let v = if lit {
                    u32::from(tint[c])
                } else {
                    u32::from(tint[c]) / 5
                };
                p[c] = (v * a / 255) as u8;
            }
            p[3] = a as u8;
        }
    }
    HudImage {
        width,
        height,
        rgba,
    }
}

/// The cards from XR_GL_CARDS when set, else synthetic ones.
pub fn load_resources() -> Resources {
    load_resources_from(std::env::var_os("XR_GL_CARDS").as_deref().map(Path::new))
}

pub fn load_resources_from(cards: Option<&Path>) -> Resources {
    let desktops = [0, 1, 2].map(|i| [desktop_pixels(i, 0), desktop_pixels(i, 1)]);
    if let Some(dir) = cards {
        let get = |name: &str| match read_image(&dir.join(format!("{name}.rgba"))) {
            Ok(im) => Rc::new(im),
            Err(e) => panic!("cards: {e}"),
        };
        return Resources {
            desktops,
            popup: get("popup"),
            help: get("help"),
            panel_static: get("panel"),
            panel_drag: (0..81).map(|f| get(&format!("panel_drag{f}"))).collect(),
            handles: get("handles"),
            from_cpp: true,
        };
    }
    let tint = [0xc8, 0xd2, 0xe6];
    Resources {
        desktops,
        popup: Rc::new(synthetic_card(420, 96, 40, tint)),
        help: Rc::new(synthetic_card(980, 520, 120, tint)),
        panel_static: Rc::new(synthetic_card(480, 300, 60, tint)),
        panel_drag: (0..81)
            .map(|f| Rc::new(synthetic_card(480, 300, 60 + f * 2, tint)))
            .collect(),
        handles: Rc::new(synthetic_card(HANDLE_ATLAS_W, HANDLE_ATLAS_H, 30, ACCENT)),
        from_cpp: false,
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
    let mut ok = true;
    if upload {
        let v = variant(s, frame) as usize;
        for (i, d) in r.desktops.iter().enumerate() {
            ok &= gl.upload(i, &d[v], DESKTOP_W, DESKTOP_H, DESKTOP_W * 4);
        }
    }
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
    ok &= gl.draw_and_swap(
        &tiles,
        &s.placements,
        s.position as i32,
        1.0 - s.zoom,
        &head(frame, s.head_motion),
        half_w,
        half_h,
        None,
    );
    ok
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
