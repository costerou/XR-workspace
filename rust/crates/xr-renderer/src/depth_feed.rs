//! Turns a desktop into what the depth engine reads: shrinks it on the GPU and reads it back without stalling
//! (spike/src/depth_feed.cpp, its GL_TEXTURE_2D path).
//!
//! A desktop is 1920x1200; the engine wants about 420x266. Shrinking where the texture already lives leaves under half a
//! megabyte per frame for shared memory. begin_texture() queues the work and returns at once; collect() returns the
//! pixels once the GPU is done and never waits for it, which is what lets this run inside a 120 Hz render loop.
//!
//! Not ported: the external-texture and dmabuf-import paths. The Rust compositor keeps every desktop as an SHM upload in a
//! GL_TEXTURE_2D, so only that path has a caller.
//!
//! Needs the compositor's context current, like the rest of gl.rs. Its GL objects are deleted in Drop, so drop it while
//! that context is still alive (before the compositor).

use std::rc::Rc;

use glow::HasContext;

use crate::gl::{Gl, compile_program};

const VERTEX: &str = "#version 300 es
out vec2 uv;
void main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); uv = p; gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }";

// Four by four taps across each output pixel: a 4.6x shrink with one bilinear tap would drop thin text and shimmer.
// uv.y grows upwards in the target and glReadPixels reads bottom first, so the source's top comes out as row 0.
const FRAGMENT_2D: &str = "#version 300 es
precision mediump float;
uniform sampler2D source;
uniform vec2 step_uv;
in vec2 uv;
out vec4 colour;
void main() {
    vec3 sum = vec3(0.0);
    for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x)
        sum += texture(source, uv + (vec2(x, y) - 1.5) * 0.25 * step_uv).rgb;
    colour = vec4(sum / 16.0, 1.0);
}";

pub struct DepthFeed {
    gl: Rc<Gl>,
    w: i32,
    h: i32,
    program: glow::Program,
    step: Option<glow::UniformLocation>,
    framebuffer: glow::Framebuffer,
    target: glow::Texture,
    vao: glow::VertexArray,
    pbo: glow::Buffer,
    fence: Option<glow::Fence>,
}

impl DepthFeed {
    /// Shrinks to w x h. None if the GL objects could not be made (GL errors are left for the caller to report).
    pub fn new(gl: &Rc<Gl>, w: i32, h: i32) -> Option<Self> {
        if w <= 0 || h <= 0 {
            return None;
        }
        let raw = gl.raw();
        let program = compile_program(raw, VERTEX, FRAGMENT_2D).ok()?;
        // SAFETY: plain GL calls on the current context (Gl's contract); every object is deleted in Drop or here.
        unsafe {
            let step = raw.get_uniform_location(program, "step_uv");
            let (Ok(target), Ok(framebuffer), Ok(vao), Ok(pbo)) = (
                raw.create_texture(),
                raw.create_framebuffer(),
                raw.create_vertex_array(),
                raw.create_buffer(),
            ) else {
                raw.delete_program(program);
                return None;
            };
            raw.bind_texture(glow::TEXTURE_2D, Some(target));
            raw.tex_storage_2d(glow::TEXTURE_2D, 1, glow::RGBA8, w, h);
            raw.bind_texture(glow::TEXTURE_2D, None);
            // The caller's framebuffer, which is not always 0: an off-screen compositor draws into its own.
            let previous = raw.get_parameter_framebuffer(glow::FRAMEBUFFER_BINDING);
            raw.bind_framebuffer(glow::FRAMEBUFFER, Some(framebuffer));
            raw.framebuffer_texture_2d(
                glow::FRAMEBUFFER,
                glow::COLOR_ATTACHMENT0,
                glow::TEXTURE_2D,
                Some(target),
                0,
            );
            let complete = raw.check_framebuffer_status(glow::FRAMEBUFFER) == glow::FRAMEBUFFER_COMPLETE;
            raw.bind_framebuffer(glow::FRAMEBUFFER, previous);
            raw.bind_buffer(glow::PIXEL_PACK_BUFFER, Some(pbo));
            raw.buffer_data_size(glow::PIXEL_PACK_BUFFER, w * h * 4, glow::STREAM_READ);
            raw.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
            let feed = Self {
                gl: gl.clone(),
                w,
                h,
                program,
                step,
                framebuffer,
                target,
                vao,
                pbo,
                fence: None,
            };
            complete.then_some(feed)
        }
    }

    pub fn busy(&self) -> bool {
        self.fence.is_some()
    }

    /// Queues one shrink of a GL_TEXTURE_2D, the kind gl.rs keeps for every desktop. False while the previous one is
    /// still running.
    pub fn begin_texture(&mut self, texture: glow::Texture) -> bool {
        if self.fence.is_some() {
            return false;
        }
        let raw = self.gl.raw();
        // SAFETY: plain GL calls on the current context; the caller's bindings and state are put back.
        unsafe {
            let previous = raw.get_parameter_framebuffer(glow::FRAMEBUFFER_BINDING);
            let mut viewport = [0i32; 4];
            raw.get_parameter_i32_slice(glow::VIEWPORT, &mut viewport);
            let (blended, scissored) = (raw.is_enabled(glow::BLEND), raw.is_enabled(glow::SCISSOR_TEST));
            raw.bind_framebuffer(glow::FRAMEBUFFER, Some(self.framebuffer));
            raw.viewport(0, 0, self.w, self.h);
            raw.disable(glow::BLEND);
            raw.disable(glow::SCISSOR_TEST);
            raw.use_program(Some(self.program));
            raw.uniform_2_f32(self.step.as_ref(), 1.0 / self.w as f32, 1.0 / self.h as f32);
            raw.active_texture(glow::TEXTURE0);
            raw.bind_texture(glow::TEXTURE_2D, Some(texture));
            raw.bind_vertex_array(Some(self.vao));
            raw.draw_arrays(glow::TRIANGLES, 0, 3);
            raw.bind_vertex_array(None);
            raw.bind_buffer(glow::PIXEL_PACK_BUFFER, Some(self.pbo));
            raw.pixel_store_i32(glow::PACK_ALIGNMENT, 1);
            // Into the buffer: this does not wait.
            raw.read_pixels(
                0,
                0,
                self.w,
                self.h,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelPackData::BufferOffset(0),
            );
            raw.pixel_store_i32(glow::PACK_ALIGNMENT, 4);
            raw.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
            self.fence = raw.fence_sync(glow::SYNC_GPU_COMMANDS_COMPLETE, 0).ok();
            raw.flush();
            raw.bind_texture(glow::TEXTURE_2D, None);
            raw.bind_framebuffer(glow::FRAMEBUFFER, previous);
            raw.viewport(viewport[0], viewport[1], viewport[2], viewport[3]);
            if blended {
                raw.enable(glow::BLEND);
            }
            if scissored {
                raw.enable(glow::SCISSOR_TEST);
            }
        }
        self.fence.is_some()
    }

    /// True once, when the GPU has finished: RGBA8, tightly packed, row 0 is the frame's top.
    pub fn collect(&mut self, rgba: &mut Vec<u8>) -> bool {
        let Some(fence) = self.fence else {
            return false;
        };
        let raw = self.gl.raw();
        let bytes = (self.w * self.h * 4) as usize;
        // SAFETY: plain GL calls on the current context; the mapping is `bytes` long (the buffer's size) and read before
        // it is unmapped.
        unsafe {
            if raw.get_sync_status(fence) != glow::SIGNALED {
                return false;
            }
            raw.delete_sync(fence);
            self.fence = None;
            rgba.resize(bytes, 0);
            raw.bind_buffer(glow::PIXEL_PACK_BUFFER, Some(self.pbo));
            let p = raw.map_buffer_range(glow::PIXEL_PACK_BUFFER, 0, bytes as i32, glow::MAP_READ_BIT);
            if !p.is_null() {
                std::ptr::copy_nonoverlapping(p, rgba.as_mut_ptr(), bytes);
                raw.unmap_buffer(glow::PIXEL_PACK_BUFFER);
            }
            raw.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
            !p.is_null()
        }
    }
}

impl Drop for DepthFeed {
    fn drop(&mut self) {
        let raw = self.gl.raw();
        // SAFETY: the objects were made on this context in new(); see the module docs on when to drop.
        unsafe {
            if let Some(f) = self.fence.take() {
                raw.delete_sync(f);
            }
            raw.delete_buffer(self.pbo);
            raw.delete_texture(self.target);
            raw.delete_framebuffer(self.framebuffer);
            raw.delete_vertex_array(self.vao);
            raw.delete_program(self.program);
        }
    }
}
