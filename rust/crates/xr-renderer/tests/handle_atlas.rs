//! The handle atlas follows the theme the way the C++ GlCompositor's does (gl.cpp upload_handles): after set_accent, the
//! next frame that draws a handle renders glass render_handle_atlas for that colour and uploads it, once; a frame that
//! draws none uploads nothing, and the same colour again renders nothing. Drawn by GlCompositor::draw_and_swap on
//! surfaceless EGL against compositors given the same atlas through set_handle_atlas. Skipped without EGL/GLES.

use std::f64::consts::PI;
use std::rc::Rc;

use xr_renderer::gl::GlCompositor;
use xr_renderer::glass::{GlassFrame, HANDLE_ATLAS_H, HANDLE_ATLAS_W};
use xr_renderer::glass_raster::render_handle_atlas;
use xr_renderer::hud::{HudImage, HudTextMetrics, HUD_FONT_FAMILY, PLACEMENT_MENU_DESKTOP_COUNT};
use xr_renderer::hud_font::HudFont;
use xr_renderer::pose::IDENTITY;
use xr_renderer::view::{FRect, Placement};

const W: u32 = 960;
const H: u32 = 600;
const DESKTOP_W: u32 = 64;
const DESKTOP_H: u32 = 36;
const TOKYO: [u8; 3] = [0x7a, 0xa2, 0xf7];
const ROSE: [u8; 3] = [0xeb, 0x6f, 0x92];
const TILE: FRect = FRect {
    x: 300.0,
    y: 180.0,
    width: 360.0,
    height: 203.0,
};

fn colour(c: [u8; 3]) -> [f32; 3] {
    c.map(|v| f32::from(v) / 255.0)
}

/// The atlas the compositor renders for `accent`: the same font lookup (none: the pixel font).
fn atlas(accent: [u8; 3], font: &Option<HudFont>) -> Rc<HudImage> {
    let font = font.as_ref().map(|f| f as &dyn HudTextMetrics);
    Rc::new(render_handle_atlas(colour(accent), font))
}

fn make(accent: [u8; 3], injected: Option<Rc<HudImage>>) -> Option<GlCompositor> {
    let mut gl = match GlCompositor::create_offscreen(W, H, 1) {
        Ok(gl) => gl,
        Err(why) => {
            println!("SKIP: no offscreen compositor: {why}");
            return None;
        }
    };
    gl.resize(W, H, W, H);
    gl.set_accent(accent[0], accent[1], accent[2]);
    if let Some(a) = injected {
        gl.set_handle_atlas(a);
    }
    let grey = vec![0x40u8; (DESKTOP_W * DESKTOP_H * 4) as usize];
    assert!(gl.upload(0, &grey, DESKTOP_W, DESKTOP_H, DESKTOP_W * 4));
    Some(gl)
}

/// One frame of the overview with the pill, level bar and caps (`handles`), or with none of them; its RGB and the
/// texture uploads it made.
fn draw(gl: &mut GlCompositor, handles: bool) -> (Vec<u8>, u64, u64) {
    let frame = GlassFrame {
        pills: handles,
        bars: [handles; PLACEMENT_MENU_DESKTOP_COUNT],
        ..GlassFrame::default()
    };
    gl.set_glass(&frame, None, None);
    let t = (46.0 * PI / 360.0).tan();
    let a = f64::from(W) / f64::from(H); // the renderer's FOV fallback
    let (half_w, half_h) = (
        (t * a / (1.0 + a * a).sqrt()) as f32,
        (t / (1.0 + a * a).sqrt()) as f32,
    );
    gl.gl().reset_counts();
    let placement = Placement::new(0.0, 0.0, 1.0, 0.0, 0.0);
    assert!(gl.draw_and_swap(
        &[TILE],
        &[placement],
        0,
        1.0,
        &IDENTITY,
        half_w,
        half_h,
        None
    ));
    let c = gl.gl().counts();
    (gl.read_rgb(), c.uploads, c.upload_bytes)
}

#[test]
fn set_accent_renders_the_atlas_for_the_next_frame_with_a_handle() {
    let font = HudFont::load(HUD_FONT_FAMILY).ok();
    let (tokyo, rose) = (atlas(TOKYO, &font), atlas(ROSE, &font));
    let atlas_bytes = u64::from(HANDLE_ATLAS_W) * u64::from(HANDLE_ATLAS_H) * 4;
    assert_eq!(tokyo.rgba.len() as u64, atlas_bytes);
    // One compositor at a time: each makes its context current only when created.
    let (with, again, rosy) = {
        let Some(mut own) = make(TOKYO, None) else {
            return;
        };
        // No handle drawn: nothing rendered or uploaded yet, the accent change waits for a frame that draws one.
        assert_eq!(draw(&mut own, false).1, 0);
        // The first frame with handles uploads the atlas it rendered.
        let (with, uploads, bytes) = draw(&mut own, true);
        assert_eq!((uploads, bytes), (1, atlas_bytes));
        // The same colour: nothing rendered again, whether set again or not.
        own.set_accent(TOKYO[0], TOKYO[1], TOKYO[2]);
        let (again, uploads, _) = draw(&mut own, true);
        assert_eq!(uploads, 0);
        // A theme change: the next frame with handles renders and uploads the new colour's atlas.
        own.set_accent(ROSE[0], ROSE[1], ROSE[2]);
        let (rosy, uploads, bytes) = draw(&mut own, true);
        assert_eq!((uploads, bytes), (1, atlas_bytes));
        (with, again, rosy)
    };
    // Exactly the atlas render_handle_atlas makes for each colour, drawn in place of the rendered one.
    let given = {
        let Some(mut gl) = make(TOKYO, Some(tokyo)) else {
            return;
        };
        draw(&mut gl, true).0
    };
    let (wrong, given_rose) = {
        let Some(mut gl) = make(TOKYO, Some(rose)) else {
            return;
        };
        let wrong = draw(&mut gl, true).0;
        gl.set_accent(ROSE[0], ROSE[1], ROSE[2]);
        (wrong, draw(&mut gl, true).0)
    };
    assert!(with == given && again == with);
    // ... and the frame does sample it: another colour's atlas under the same accent draws other pixels.
    assert!(wrong != with);
    assert!(rosy == given_rose && rosy != with);
}
