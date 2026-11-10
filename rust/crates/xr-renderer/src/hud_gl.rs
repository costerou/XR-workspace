//! GLES drawing for the head-locked cards: status pop-ups, the help card, the key bind panel (spike/src/hud_gl.cpp).
//! Needs the compositor's context current for `new`, every call and drop. No head rotation is applied.
//!
//! The card is drawn from client-side vertex arrays (no buffer object bound), as the C++ does, so its GL calls are the
//! same ones in the same order.

use std::rc::Rc;

use glow::HasContext;

use crate::gl::{Gl, compile_program};
use crate::hud::{HudImage, centered_card_origin};

const HUD_VERTEX: &str = "attribute vec2 pos; attribute vec2 uv; varying vec2 v; void main() { v = uv; gl_Position = vec4(pos, 0.0, 1.0); }\n";
const HUD_FRAGMENT: &str = concat!(
    // premultiplied card, uniformly faded
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha;\n",
    "void main() { gl_FragColor = texture2D(tex, v) * alpha; }\n",
);
/// The card's bottom edge sits 10% above the bottom of the view.
const BOTTOM_FRACTION: f32 = 0.10;

pub struct GlHud {
    gl: Rc<Gl>,
    program: glow::Program,
    texture: glow::Texture,
    pos: i32,
    uv: i32,
    alpha: Option<glow::UniformLocation>,
    uploaded: u64,
    tex_w: u32,
    tex_h: u32,
}

impl GlHud {
    /// None if the shaders fail; the card is then skipped.
    pub fn new(gl: &Rc<Gl>) -> Option<Self> {
        let raw = gl.raw();
        let program = compile_program(raw, HUD_VERTEX, HUD_FRAGMENT).ok()?;
        // SAFETY: plain GL calls on the current context (Gl's contract); the texture is deleted in Drop.
        unsafe {
            let pos = raw
                .get_attrib_location(program, "pos")
                .map_or(-1, |l| l as i32);
            let uv = raw
                .get_attrib_location(program, "uv")
                .map_or(-1, |l| l as i32);
            let alpha = raw.get_uniform_location(program, "alpha");
            let Ok(texture) = raw.create_texture() else {
                raw.delete_program(program);
                return None;
            };
            let out = Self {
                gl: gl.clone(),
                program,
                texture,
                pos,
                uv,
                alpha,
                uploaded: 0,
                tex_w: 0,
                tex_h: 0,
            };
            raw.bind_texture(glow::TEXTURE_2D, Some(texture));
            for (p, v) in [
                (glow::TEXTURE_MIN_FILTER, glow::NEAREST),
                (glow::TEXTURE_MAG_FILTER, glow::NEAREST),
                (glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
                (glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
            ] {
                raw.tex_parameter_i32(glow::TEXTURE_2D, p, v as i32);
            }
            (raw.get_error() == glow::NO_ERROR).then_some(out)
        }
    }

    /// Draw `image` (re-uploaded when `generation` changes) centred horizontally near the bottom (or, with `centered`, in
    /// the middle) of a `width` x `height` viewport at `alpha`, moved down by `drop_px`, blended over what is drawn.
    #[allow(clippy::too_many_arguments)]
    pub fn draw(
        &mut self,
        image: &HudImage,
        generation: u64,
        alpha: f32,
        width: u32,
        height: u32,
        centered: bool,
        drop_px: f32,
    ) {
        if height == 0 {
            return;
        }
        // Bottom-anchored pop-ups: bottom edge BOTTOM_FRACTION up the view. Centred cards: the shared card origin.
        let h = image.height as f32;
        let origin = centered_card_origin(width, height, image.width, image.height, drop_px);
        let top = if centered {
            origin.y
        } else {
            height as f32 - (height as f32 * BOTTOM_FRACTION - drop_px).floor() - h
        };
        self.draw_at(image, generation, alpha, width, height, origin.x, top);
    }

    /// Draw `image` with its top-left corner at (`left`, `top`) output pixels, counted from the top-left of the view.
    #[allow(clippy::too_many_arguments)]
    pub fn draw_at(
        &mut self,
        image: &HudImage,
        generation: u64,
        alpha: f32,
        width: u32,
        height: u32,
        left: f32,
        top: f32,
    ) {
        if alpha <= 0.0 || width == 0 || height == 0 || !image.complete() {
            return;
        }
        let gl = &*self.gl;
        gl.active_texture(glow::TEXTURE0);
        gl.bind_texture(Some(self.texture));
        if generation != self.uploaded {
            gl.pixel_store(glow::UNPACK_ALIGNMENT, 4);
            if image.width == self.tex_w && image.height == self.tex_h {
                // same size (a slider value, a pop-up's text): keep the storage
                gl.tex_sub_image(0, image.width, image.height, &image.rgba);
            } else {
                gl.tex_image(image.width, image.height, Some(&image.rgba));
            }
            self.uploaded = generation;
            self.tex_w = image.width;
            self.tex_h = image.height;
        }
        let (w, h) = (self.tex_w as f32, self.tex_h as f32); // 1:1: the image is rasterised at output size
        let x0 = left.floor();
        let y_bottom = height as f32 - top.floor() - h;
        // Pixel rect -> NDC (y up). Whole-pixel placement keeps the pixel font crisp.
        let l = 2.0 * x0 / width as f32 - 1.0;
        let r = 2.0 * (x0 + w) / width as f32 - 1.0;
        let b = 2.0 * y_bottom / height as f32 - 1.0;
        let t = 2.0 * (y_bottom + h) / height as f32 - 1.0;
        let positions: [f32; 8] = [l, t, r, t, l, b, r, b];
        let uvs: [f32; 8] = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0]; // row 0 of the card is its top
        gl.enable(glow::BLEND);
        gl.blend_func(glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
        gl.use_program(Some(self.program));
        gl.uniform_1f(self.alpha.as_ref(), alpha.min(1.0)); // sampler `tex` = unit 0: its link-time value
        // SAFETY: no buffer is bound to ARRAY_BUFFER (the compositor unbinds it before any card), and both arrays live
        // until the draw below has read them.
        unsafe {
            gl.client_attrib(self.pos as u32, 2, &positions);
            gl.client_attrib(self.uv as u32, 2, &uvs);
        }
        gl.enable_attrib(self.pos as u32);
        gl.enable_attrib(self.uv as u32);
        gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
        gl.disable(glow::BLEND);
    }
}

impl Drop for GlHud {
    fn drop(&mut self) {
        let raw = self.gl.raw();
        // SAFETY: the compositor keeps its context current while it drops its cards.
        unsafe {
            raw.delete_texture(self.texture);
            raw.delete_program(self.program);
        }
    }
}
