//! A closed help card must leave nothing in the frame. Drives the real HelpOverlay fade and the real GlHud on a
//! surfaceless EGL context, composing each frame the way GlCompositor::draw_and_swap does: clear to opaque black, an
//! opaque desktop tile, then the help card at its fade alpha. After each close the read-back frame must equal the frame
//! from before anything opened. Port of spike/tests/overlay_residue_test.cpp.
//!
//! The card is the one the C++ HelpOverlay draws for these rows: render_help_card in the overlay's accent, unbounded, page
//! 1, in the installed HUD font (else the pixel font). Exit 77 (skipped) without EGL/GLES.

use std::rc::Rc;
use std::sync::atomic::{AtomicUsize, Ordering};

use glow::HasContext;
use xr_renderer::egl::Headless;
use xr_renderer::gl::{compile_program, Gl};
use xr_renderer::hud::{
    render_help_card, HelpOverlay, HelpRow, HELP_FADE_IN_MS, HELP_FADE_OUT_MS, HUD_FONT_FAMILY,
};
use xr_renderer::hud_font::HudFont;
use xr_renderer::hud_gl::GlHud;

static FAILURES: AtomicUsize = AtomicUsize::new(0);

macro_rules! check {
    ($c:expr) => {{
        let ok: bool = $c;
        if !ok {
            eprintln!("{}:{}: check!({})", file!(), line!(), stringify!($c));
            FAILURES.fetch_add(1, Ordering::Relaxed);
        }
    }};
}

const W: u32 = 1536; // the 1920x1200 @1.25 glasses surface
const H: u32 = 960;

fn main() {
    let headless = match Headless::new(None) {
        Ok(h) => h,
        Err(why) => {
            println!("SKIP: no surfaceless EGL / GLES context: {why}");
            std::process::exit(77);
        }
    };
    let raw = headless.gl.clone();
    // SAFETY (all GL in this file): plain GL calls on the headless context, current on this thread for the whole test.
    unsafe {
        let fb = raw.create_framebuffer().unwrap();
        raw.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
        let rb = raw.create_renderbuffer().unwrap();
        raw.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
        raw.renderbuffer_storage(glow::RENDERBUFFER, glow::RGBA8, W as i32, H as i32);
        raw.framebuffer_renderbuffer(
            glow::FRAMEBUFFER,
            glow::COLOR_ATTACHMENT0,
            glow::RENDERBUFFER,
            Some(rb),
        );
        if raw.check_framebuffer_status(glow::FRAMEBUFFER) != glow::FRAMEBUFFER_COMPLETE {
            println!("SKIP: no RGBA8 framebuffer");
            std::process::exit(77);
        }
    }
    let gl = Rc::new(unsafe { Gl::new(raw.clone()) }.expect("glVertexAttribPointer"));
    let tile = compile_program(
        &raw,
        "attribute vec2 pos; void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n",
        "precision mediump float; void main() { gl_FragColor = vec4(0.25, 0.25, 0.25, 1.0); }\n",
    );
    let help_gl = GlHud::new(&gl);
    let (Ok(tile), Some(mut help_gl)) = (tile, help_gl) else {
        eprintln!("the tile program or the GlHud failed to build");
        std::process::exit(1);
    };
    let tile_pos = unsafe { raw.get_attrib_location(tile, "pos") }.expect("pos");

    let mut help = HelpOverlay::new();
    help.set_font(
        HudFont::load(HUD_FONT_FAMILY)
            .ok()
            .map(|f| Box::new(f) as _),
    );
    let rows = [
        HelpRow::new("Ctrl+Alt+H", "Show / hide this help"),
        HelpRow::new("Ctrl+Alt+L", "Placement pill of the selected desktop"),
    ];
    // HelpOverlay::image in the C++: the rows never change and no viewport is set, so one card for the whole test.
    let font = HudFont::load(HUD_FONT_FAMILY).ok();
    let card = render_help_card(&rows, help.accent(), font.as_ref().map(|f| f as _), 0, 0, 0).0;
    let mut pixels = vec![0u8; (W * H * 4) as usize];
    let mut before: Vec<u8>;

    let frame = |help: &HelpOverlay, help_gl: &mut GlHud, pixels: &mut Vec<u8>, now: f64| {
        gl.viewport(0, 0, W as i32, H as i32);
        gl.clear_color(0.0, 0.0, 0.0, 1.0);
        gl.clear(glow::COLOR_BUFFER_BIT);
        let quad = [-0.9f32, 0.9, 0.9, 0.9, -0.9, -0.9, 0.9, -0.9]; // desktop tile; black border like the gaps
        gl.use_program(Some(tile));
        // SAFETY: no buffer is bound to ARRAY_BUFFER; `quad` outlives the draw.
        unsafe { gl.client_attrib(tile_pos, 2, &quad) };
        gl.enable_attrib(tile_pos);
        gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
        // Same selection as GlCompositor: an image only while its alpha is > 0.
        let a = help.alpha(now);
        if a > 0.0 {
            help_gl.draw(&card, help.generation(), a, W, H, true, help.offset_px(now));
        }
        unsafe {
            raw.finish();
            raw.read_pixels(
                0,
                0,
                W as i32,
                H as i32,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelPackData::Slice(Some(pixels)),
            );
        }
        check!(gl.get_error() == glow::NO_ERROR);
    };
    let changed = |a: &[u8], b: &[u8]| a.iter().zip(b).filter(|(x, y)| x != y).count();

    let mut now = 1000.0; // one monotonic clock for both passes, like monotonic_ms() in the renderer
    for hz in [120.0, 60.0] {
        let dt = 1000.0 / hz;
        let run = |help: &HelpOverlay,
                   help_gl: &mut GlHud,
                   pixels: &mut Vec<u8>,
                   now: &mut f64,
                   ms: f64| {
            let mut e = 0.0;
            while e < ms {
                frame(help, help_gl, pixels, *now);
                e += dt;
                *now += dt;
            }
        };
        frame(&help, &mut help_gl, &mut pixels, now);
        before = pixels.clone(); // nothing ever opened
        let closed_clean = |pixels: &[u8], before: &[u8], what: &str| {
            let n = changed(pixels, before);
            if n > 0 {
                eprintln!("{hz:.0} Hz, {what}: {n} bytes differ from the never-opened frame");
            }
            check!(n == 0);
        };
        let settle = HELP_FADE_OUT_MS + 2.0 * dt; // the fade-out plus the frame that clears it

        help.set(true, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, 1000.0);
        check!(changed(&pixels, &before) > 0); // the card is really drawn
        help.set(false, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, settle);
        closed_clean(&pixels, &before, "help card closed");

        help.set(true, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, 60.0);
        help.set(false, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, settle);
        closed_clean(&pixels, &before, "help card closed mid fade-in");

        help.set(true, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, 200.0);
        help.set(false, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, 80.0);
        help.set(true, &rows, now, 0); // reversed mid fade
        run(&help, &mut help_gl, &mut pixels, &mut now, 80.0);
        help.set(false, &rows, now, 0);
        run(&help, &mut help_gl, &mut pixels, &mut now, settle);
        closed_clean(&pixels, &before, "help card reversed twice, then closed");
    }
    // Once faded in, an open card stays perfectly still: frames a minute apart are identical.
    {
        frame(&help, &mut help_gl, &mut pixels, now);
        let nothing = pixels.clone(); // nothing open
        help.set(true, &rows, now, 0);
        frame(
            &help,
            &mut help_gl,
            &mut pixels,
            now + HELP_FADE_IN_MS + 1.0,
        );
        check!(changed(&pixels, &nothing) > 0); // the card is really drawn
        let open = pixels.clone();
        for later in [2000.0, 7000.0, 31000.0, 60000.0] {
            frame(
                &help,
                &mut help_gl,
                &mut pixels,
                now + HELP_FADE_IN_MS + 1.0 + later,
            );
            let n = changed(&pixels, &open);
            if n > 0 {
                eprintln!("open card moved after {later:.0} ms: {n} bytes differ");
            }
            check!(n == 0);
        }
        help.set(false, &rows, now, 0);
    }
    drop(help_gl);
    drop(headless);
    let failures = FAILURES.load(Ordering::Relaxed);
    println!(
        "{}",
        if failures > 0 {
            "overlay residue test FAILED"
        } else {
            "overlay residue test passed"
        }
    );
    std::process::exit(i32::from(failures > 0));
}
