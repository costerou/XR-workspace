//! The glass fragment shader (glass_fragment_shader) on a real GLES context matches its CPU mirror glass_shade() pixel
//! for pixel (within rounding) over black and over a bright (0.9) desktop, in every state (idle, hovered, dragged) and
//! along the morph; over black the glass alone, and the glass with the panel content composited on top, stays at or
//! under the 191/255 OLED cap. The same holds in Omarchy theme colours. Port of spike/tests/glass_render_test.cpp.
//!
//! The panel images are rendered by code not ported yet (the font): with XR_GL_CARDS set to a `gl_bench --dump`
//! directory the panel checks draw the C++ ones, else they are skipped (and said so). Exit 77 (skipped) without EGL/GLES.

#[path = "support/gl_scenarios.rs"]
mod gl_scenarios;

use std::path::Path;
use std::rc::Rc;
use std::sync::atomic::{AtomicUsize, Ordering};

use glow::HasContext;
use xr_renderer::egl::Headless;
use xr_renderer::gl::{Gl, compile_program};
use xr_renderer::glass::{
    GLASS_ACTIVE, GLASS_HOVER, GLASS_IDLE, GLASS_STYLE_VECS, GlassLook, PANEL_RADIUS,
    glass_fragment_shader, glass_look, glass_morph, glass_panel, glass_radius, glass_shade,
    glass_style,
};
use xr_renderer::hud::{CARD_MAX_LEVEL, HudImage};
use xr_renderer::view::FRect;

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

const W: i32 = 640;
const H: i32 = 480;
const CAP: i32 = CARD_MAX_LEVEL as i32;

struct Ctx {
    gl: Rc<glow::Context>,
    /// For client-side vertex arrays (glow takes a buffer offset, not a pointer).
    arrays: Gl,
    glass: glow::Program,
    flat: glow::Program,
    panel: glow::Program,
    pixels: Vec<u8>,
}

const FULL: [f32; 8] = [-1.0, 1.0, 1.0, 1.0, -1.0, -1.0, 1.0, -1.0];

impl Ctx {
    // SAFETY (all GL in this file): plain GL calls on the headless context, current on this thread; the client arrays
    // passed to vertex_attrib_pointer outlive the draw that reads them (no buffer is bound to ARRAY_BUFFER).
    fn attrib(&self, program: glow::Program, name: &str, data: &[f32]) -> u32 {
        unsafe {
            let loc = self.gl.get_attrib_location(program, name).expect(name);
            self.arrays.client_attrib(loc, 2, data);
            self.gl.enable_vertex_attrib_array(loc);
            loc
        }
    }

    fn read(&mut self) {
        unsafe {
            self.gl.finish();
            self.gl.read_pixels(
                0,
                0,
                W,
                H,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelPackData::Slice(Some(&mut self.pixels)),
            );
        }
    }

    /// Top-down pixel (x, y).
    fn at(&self, x: i32, y: i32) -> &[u8] {
        &self.pixels[(((H - 1 - y) * W + x) * 4) as usize..][..4]
    }

    fn background(&self, level: f32) {
        unsafe {
            self.gl.viewport(0, 0, W, H);
            self.gl.disable(glow::BLEND);
            self.gl.clear_color(0.0, 0.0, 0.0, 1.0);
            self.gl.clear(glow::COLOR_BUFFER_BIT);
            if level <= 0.0 {
                return;
            }
            self.gl.use_program(Some(self.flat));
            self.gl.uniform_4_f32(
                self.gl.get_uniform_location(self.flat, "c").as_ref(),
                level,
                level,
                level,
                1.0,
            );
            self.attrib(self.flat, "pos", &FULL);
            self.gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
        }
    }

    fn shape(&self, k: &GlassLook, r: &FRect, radius: f32, opacity: f32) {
        let px: [f32; 8] = [0.0, 0.0, W as f32, 0.0, 0.0, H as f32, W as f32, H as f32];
        unsafe {
            let gl = &self.gl;
            gl.enable(glow::BLEND);
            gl.blend_func(glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
            gl.use_program(Some(self.glass));
            let u = |name: &str| gl.get_uniform_location(self.glass, name);
            gl.uniform_4_f32(
                u("rect").as_ref(),
                r.x + r.width / 2.0,
                r.y + r.height / 2.0,
                r.width / 2.0,
                r.height / 2.0,
            );
            gl.uniform_1_f32(u("radius").as_ref(), radius);
            let style = glass_style(k);
            gl.uniform_4_f32_slice(u("style").as_ref(), &style[..4 * GLASS_STYLE_VECS]); // as GlCompositor does
            gl.uniform_1_f32(u("opacity").as_ref(), opacity);
            self.attrib(self.glass, "pos", &FULL);
            let pxl = self.attrib(self.glass, "px", &px);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
            gl.disable_vertex_attrib_array(pxl);
        }
    }

    /// The panel content over the open glass at `r`, `filter` its sampling.
    fn panel(&self, im: &HudImage, r: &FRect, filter: u32) {
        let (l, rr) = (
            2.0 * r.x / W as f32 - 1.0,
            2.0 * (r.x + r.width) / W as f32 - 1.0,
        );
        let (t, b) = (
            1.0 - 2.0 * r.y / H as f32,
            1.0 - 2.0 * (r.y + r.height) / H as f32,
        );
        let quad = [l, t, rr, t, l, b, rr, b];
        let uv = [0.0f32, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0];
        unsafe {
            let gl = &self.gl;
            let tex = gl.create_texture().unwrap();
            gl.bind_texture(glow::TEXTURE_2D, Some(tex));
            for (p, v) in [
                (glow::TEXTURE_MIN_FILTER, filter),
                (glow::TEXTURE_MAG_FILTER, filter),
                (glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
                (glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
            ] {
                gl.tex_parameter_i32(glow::TEXTURE_2D, p, v as i32);
            }
            gl.tex_image_2d(
                glow::TEXTURE_2D,
                0,
                glow::RGBA as i32,
                im.width as i32,
                im.height as i32,
                0,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelUnpackData::Slice(Some(&im.rgba)),
            );
            gl.use_program(Some(self.panel));
            gl.uniform_1_i32(gl.get_uniform_location(self.panel, "tex").as_ref(), 0);
            gl.uniform_1_f32(gl.get_uniform_location(self.panel, "alpha").as_ref(), 1.0);
            self.attrib(self.panel, "pos", &quad);
            let uvl = self.attrib(self.panel, "uv", &uv);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
            gl.disable_vertex_attrib_array(uvl);
            gl.delete_texture(tex);
        }
    }

    fn brightest(&self) -> i32 {
        self.pixels
            .chunks_exact(4)
            .flat_map(|p| [p[0], p[1], p[2]])
            .map(i32::from)
            .max()
            .unwrap_or(0)
    }

    /// The worst channel difference from glass_shade over a `bg` background, and the brightest channel.
    fn compare(&self, look: &GlassLook, r: &FRect, radius: f32, bg: f32) -> (i32, i32) {
        let (mut worst, mut brightest) = (0, 0);
        for y in 0..H {
            for x in 0..W {
                let c = glass_shade(look, r, radius, x as f32 + 0.5, y as f32 + 0.5, 1.0);
                let p = self.at(x, y);
                for i in 0..3 {
                    let want = c[i] + bg * (1.0 - c[3]); // premultiplied over the background
                    worst = worst.max((f32::from(p[i]) - want * 255.0).abs().round() as i32);
                    brightest = brightest.max(i32::from(p[i]));
                }
            }
        }
        (worst, brightest)
    }
}

fn main() {
    let headless = match Headless::new(None) {
        Ok(h) => h,
        Err(why) => {
            println!("SKIP: no surfaceless EGL / GLES context: {why}");
            std::process::exit(77);
        }
    };
    let gl = headless.gl.clone();
    // SAFETY: as in Ctx.
    unsafe {
        let fb = gl.create_framebuffer().unwrap();
        gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
        let rb = gl.create_renderbuffer().unwrap();
        gl.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
        gl.renderbuffer_storage(glow::RENDERBUFFER, glow::RGBA8, W, H);
        gl.framebuffer_renderbuffer(
            glow::FRAMEBUFFER,
            glow::COLOR_ATTACHMENT0,
            glow::RENDERBUFFER,
            Some(rb),
        );
        if gl.check_framebuffer_status(glow::FRAMEBUFFER) != glow::FRAMEBUFFER_COMPLETE {
            println!("SKIP: no RGBA8 framebuffer");
            std::process::exit(77);
        }
    }
    // Canvas px = framebuffer px here (top-down), so the shader sees exactly the pixel centres glass_shade() gets.
    let glass = compile_program(
        &gl,
        "attribute vec2 pos; attribute vec2 px; varying vec2 p; void main() { p = px; gl_Position = vec4(pos, 0.0, 1.0); }\n",
        glass_fragment_shader(),
    );
    let flat = compile_program(
        &gl,
        "attribute vec2 pos; void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n",
        "precision mediump float; uniform vec4 c; void main() { gl_FragColor = c; }\n",
    );
    let panel = compile_program(
        &gl,
        "attribute vec2 pos; attribute vec2 uv; varying vec2 v; void main() { v = uv; gl_Position = vec4(pos, 0.0, 1.0); }\n",
        "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha;\nvoid main() { gl_FragColor = texture2D(tex, v) * alpha; }\n",
    );
    let (Ok(glass), Ok(flat), Ok(panel)) = (glass, flat, panel) else {
        eprintln!("a program failed to build");
        std::process::exit(1);
    };
    // SAFETY: the headless context is current on this thread for the whole test.
    let arrays = unsafe { Gl::new(gl.clone()) }.expect("glVertexAttribPointer");
    let mut ctx = Ctx {
        gl,
        arrays,
        glass,
        flat,
        panel,
        pixels: vec![0; (W * H * 4) as usize],
    };
    let cards = std::env::var_os("XR_GL_CARDS");
    let card = |name: &str| {
        cards.as_ref().map(|d| {
            gl_scenarios::read_image(&Path::new(d).join(format!("{name}.rgba"))).expect(name)
        })
    };

    let tile = FRect {
        x: 80.0,
        y: 20.0,
        width: 480.0,
        height: 120.0,
    };
    for t in [0.0, 0.5, 1.0] {
        for level in [GLASS_IDLE, GLASS_HOVER, GLASS_ACTIVE] {
            for bg in [0.0, 0.9] {
                let look = glass_look([0.55, 0.6, 0.9], level, t);
                let r = glass_morph(&tile, t);
                let radius = glass_radius(&tile, t);
                ctx.background(bg);
                ctx.shape(&look, &r, radius, 1.0);
                ctx.read();
                let (worst, brightest) = ctx.compare(&look, &r, radius, bg);
                if worst > 3 {
                    eprintln!(
                        "t {t:.1} level {level:.1} bg {bg:.1}: shader differs from glass_shade by {worst}/255"
                    );
                }
                check!(worst <= 3);
                if bg == 0.0 {
                    check!(brightest <= CAP);
                    check!(brightest > 40); // really drawn
                }
            }
        }
    }
    // The panel content over the open glass: still under the cap, and it shows.
    let panel_rect = glass_panel(&FRect {
        x: 80.0,
        y: -120.0,
        width: 480.0,
        height: 120.0,
    }); // at the top of the framebuffer
    let mut panels = 0;
    if let Some(im) = card("glass_panel") {
        ctx.background(0.0);
        ctx.shape(
            &glass_look([1.0, 1.0, 1.0], GLASS_ACTIVE, 1.0),
            &panel_rect,
            PANEL_RADIUS,
            1.0,
        );
        ctx.panel(&im, &panel_rect, glow::LINEAR);
        ctx.read();
        let b = ctx.brightest();
        check!((140..=CAP).contains(&b));
        panels += 1;
    }
    // Theme colours (glass_theme): saturated, pale and deep Omarchy borders, pure blue, white and vantablack grey,
    // dragged (the brightest state): the shader still matches glass_shade, and over black the glass, and the open panel
    // with its themed highlights on it, stay under the cap.
    for hex in [
        0xe68e0du32,
        0x1e66f5,
        0xf2fcff,
        0x26a269,
        0x0000ff,
        0xffffff,
        0x8d8d8d,
    ] {
        let accent = [
            (hex >> 16) as f32 / 255.0,
            ((hex >> 8) & 255) as f32 / 255.0,
            (hex & 255) as f32 / 255.0,
        ];
        for t in [0.0, 1.0] {
            let look = glass_look(accent, GLASS_ACTIVE, t);
            let r = glass_morph(&tile, t);
            let radius = glass_radius(&tile, t);
            ctx.background(0.0);
            ctx.shape(&look, &r, radius, 1.0);
            ctx.read();
            let (worst, brightest) = ctx.compare(&look, &r, radius, 0.0);
            if worst > 3 {
                eprintln!(
                    "theme {hex:06x} t {t:.1}: shader differs from glass_shade by {worst}/255"
                );
            }
            check!(worst <= 3 && brightest <= CAP && brightest > 40);
        }
        if let Some(im) = card(&format!("glass_panel_{hex:06x}")) {
            ctx.background(0.0);
            ctx.shape(
                &glass_look(accent, GLASS_ACTIVE, 1.0),
                &panel_rect,
                PANEL_RADIUS,
                1.0,
            );
            ctx.panel(&im, &panel_rect, glow::NEAREST);
            ctx.read();
            let b = ctx.brightest();
            if b > CAP {
                eprintln!("theme {hex:06x}: the open panel reaches {b}/255");
            }
            check!((140..=CAP).contains(&b));
            panels += 1;
        }
    }
    // SAFETY: as in Ctx.
    check!(unsafe { ctx.gl.get_error() } == glow::NO_ERROR);
    drop(headless);
    let failures = FAILURES.load(Ordering::Relaxed);
    if panels == 0 {
        println!(
            "note: XR_GL_CARDS not set, the open-panel checks were skipped (no panel renderer in Rust yet)"
        );
    }
    println!(
        "{}",
        if failures > 0 {
            "glass render test FAILED"
        } else {
            "glass render test passed"
        }
    );
    std::process::exit(i32::from(failures > 0));
}
