//! GPU composition of the captured desktops (spike/src/gl.cpp), on GLES 3.0 through glow. The GLSL is the C++'s, byte
//! for byte (GLSL ES 1.00, which every ES 3 context still takes), and so is the order of every GL call a frame makes:
//! the goldens the C++ renders are this module's goldens.
//!
//! [`Gl`] wraps the context and counts the calls a frame makes in the C++ bench's categories
//! (spike/tests/gl_call_counts.hpp), so the two renderers' call counts compare directly.
//!
//! Here: the off-screen compositor (a surfaceless context drawing into its own RGBA8 framebuffer; "swap" is a glFlush)
//! with SHM uploads, the desktops, the highlight glow and border, the glass pills, panels and handles, the free pointer,
//! the window drag, the head-locked cards and global immersive 3D (side-by-side eyes on a 3:1 buffer, each desktop
//! warped by its depth map through [`crate::stereo_gl`]) and the background ([`crate::environment`], drawn in place of
//! the clear). Not yet: the VR video scene; its setters are absent rather than ignored.

use std::cell::Cell;
use std::ffi::c_void;
use std::rc::Rc;
use std::sync::OnceLock;
use std::time::Instant;

use glow::HasContext;

use crate::depth::DepthInput;
use crate::egl::{DmabufPlane, Headless, proc_address};

/// An EGLImage on the compositor's display (a dmabuf); destroy it with [`GlCompositor::destroy_image`].
pub struct GpuImage(khronos_egl::Image);

type ImageTarget = unsafe extern "system" fn(u32, *mut c_void);

fn image_entry(name: &std::ffi::CStr) -> Option<ImageTarget> {
    let p = proc_address(name);
    // SAFETY: the GL_OES_EGL_image entry points have this signature; null means absent.
    (!p.is_null()).then(|| unsafe { std::mem::transmute::<*mut c_void, ImageTarget>(p) })
}

const TIME_ELAPSED_EXT: u32 = 0x88BF;
const QUERY_RESULT_EXT: u32 = 0x8866;
const QUERY_RESULT_AVAILABLE_EXT: u32 = 0x8867;
const GPU_DISJOINT_EXT: u32 = 0x8FBB;

type GenQueries = unsafe extern "system" fn(i32, *mut u32);
type DeleteQueries = unsafe extern "system" fn(i32, *const u32);
type BeginQuery = unsafe extern "system" fn(u32, u32);
type EndQuery = unsafe extern "system" fn(u32);
type QueryU32 = unsafe extern "system" fn(u32, u32, *mut u32);
type QueryU64 = unsafe extern "system" fn(u32, u32, *mut u64);

/// GL_EXT_disjoint_timer_query's elapsed-time query around one frame at a time (the status dict's and exit report's
/// GPU time). None without the extension.
struct GpuTimer {
    delete: DeleteQueries,
    begin: BeginQuery,
    end: EndQuery,
    available: QueryU32,
    result: QueryU64,
    query: u32,
    /// Begun and not ended yet.
    running: bool,
    /// Ended, its result not read yet.
    pending: bool,
}

impl GpuTimer {
    fn load(raw: &glow::Context) -> Option<Self> {
        if !raw.supported_extensions().contains("GL_EXT_disjoint_timer_query") {
            return None;
        }
        let p = |n: &std::ffi::CStr| {
            let f = proc_address(n);
            (!f.is_null()).then_some(f)
        };
        let (g, d, b, e, a, r) = (
            p(c"glGenQueriesEXT")?,
            p(c"glDeleteQueriesEXT")?,
            p(c"glBeginQueryEXT")?,
            p(c"glEndQueryEXT")?,
            p(c"glGetQueryObjectuivEXT")?,
            p(c"glGetQueryObjectui64vEXT")?,
        );
        // SAFETY: each pointer is the named GL_EXT_disjoint_timer_query entry point, whose C signature the type
        // spells; the context that gave them is current on this thread, and `query` is a valid out pointer.
        unsafe {
            let gen_queries = std::mem::transmute::<*mut c_void, GenQueries>(g);
            let mut query = 0;
            gen_queries(1, &mut query);
            (query != 0).then(|| Self {
                delete: std::mem::transmute::<*mut c_void, DeleteQueries>(d),
                begin: std::mem::transmute::<*mut c_void, BeginQuery>(b),
                end: std::mem::transmute::<*mut c_void, EndQuery>(e),
                available: std::mem::transmute::<*mut c_void, QueryU32>(a),
                result: std::mem::transmute::<*mut c_void, QueryU64>(r),
                query,
                running: false,
                pending: false,
            })
        }
    }
}

static IMAGE_TARGET_TEXTURE: std::sync::LazyLock<Option<ImageTarget>> =
    std::sync::LazyLock::new(|| image_entry(c"glEGLImageTargetTexture2DOES"));
static IMAGE_TARGET_RENDERBUFFER: std::sync::LazyLock<Option<ImageTarget>> =
    std::sync::LazyLock::new(|| image_entry(c"glEGLImageTargetRenderbufferStorageOES"));
use crate::glass::{
    AtlasBox, CONTENT_FROM, GLASS_IDLE, GLASS_SHADOW_MARGIN, GLASS_STYLE_VECS, GRABBER_H,
    GRABBER_TOP, GRABBER_W, GlassFrame, GlassLook, LevelSegment, PANEL_RADIUS, corner_cap_box,
    corner_cap_rect, glass_draw_order, glass_fragment_shader, glass_look, glass_morph,
    glass_radius, glass_style, grabber_look, immersive_main_box, immersive_main_rect,
    immersive_tail_box, immersive_tail_rect, level_bar_box, level_bar_rect, level_bar_scale,
    options_gear_box, options_hint_box, options_icon_box, options_restore_box,
};
use crate::environment::{EnvironmentPass, environment_line_color};
use crate::glass_raster::render_handle_atlas;
use crate::hud::{Background, HUD_FONT_FAMILY, HudImage, HudTextMetrics};
use crate::hud_font::HudFont;
use crate::hud_gl::GlHud;
use crate::options_geometry::{
    options_gear, options_maximize, options_maximize_ray, options_mesh_ray, options_panel,
    options_restore_hit, options_restore_rect, options_step_scale,
};
use crate::pose::{IDENTITY as IDENTITY_M3, Mat3, apply, transpose};
use crate::stereo::{
    STEREO_HALF_BASELINE, STEREO_STALE_MS, StereoBudget, StereoFader, StereoParams, stereo_budget,
    stereo_clamped, stereo_infinity_shift, stereo_limits, stereo_plane_disparity, stereo_safe_baseline, stereo_side,
    stereo_tile_scale,
};
use crate::stereo_gl::StereoPass;
use crate::video::math::{EyeViewport, eye_viewports};
use crate::view::{
    BORDER_PX, Canvas, FREE_POINTER_HELD_LEVEL, FREE_POINTER_RADIUS_PX, FREE_POINTER_RING_PX,
    FREE_POINTER_SEGMENTS, FRect, GHOST_LEVEL, GHOST_THICKNESS_PX, PanLayers, Placement,
    PointerLook, Size, Vec3, canvas_disc, direction_disc, direction_marks, direction_outline,
    draw_order, drop_frame, glass_mesh, pan_draw_order, place_point, stereo_surface_hit,
    tile_mesh, zoom_draw_order,
};

// ---------------------------------------------------------------------------------------------------------------------
// Counted GL

/// GL calls by kind, as spike/tests/gl_call_counts.hpp counts them (`vertices`, `upload_bytes` and `buffer_bytes` are
/// amounts, not calls). Uncounted there, so here: blend equations, texture parameters, object creation and deletion,
/// shader and program setup, readback and glFinish.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub draws: u64,
    pub vertices: u64,
    pub programs: u64,
    pub uniforms: u64,
    pub textures: u64,
    pub attribs: u64,
    pub toggles: u64,
    pub uploads: u64,
    pub upload_bytes: u64,
    pub buffers: u64,
    pub buffer_bytes: u64,
    pub errors: u64,
    pub other: u64,
}

impl Counts {
    /// Every counted call (the C++ `calls()`).
    pub fn calls(&self) -> u64 {
        self.draws
            + self.programs
            + self.uniforms
            + self.textures
            + self.attribs
            + self.toggles
            + self.uploads
            + self.buffers
            + self.errors
            + self.other
    }
}

type VertexAttribPointer = unsafe extern "system" fn(u32, i32, u32, u8, i32, *const c_void);

/// The GL context with every per-frame call counted. Made only with a context current on this thread, and used only
/// while it stays current there (it is not Send): that is what makes its methods safe to call.
pub struct Gl {
    gl: Rc<glow::Context>,
    counts: Cell<Counts>,
    /// Wall time inside glFlush since the last reset (gl_call_counts.hpp `flush_ns`: the offscreen "swap", command
    /// submission to the kernel). Kept beside Counts, which holds calls and amounts only.
    flush_ns: Cell<u64>,
    /// glVertexAttribPointer itself: glow only takes buffer offsets, and the cards draw from client arrays.
    attrib_pointer: VertexAttribPointer,
}

macro_rules! count {
    ($self:ident, $field:ident) => {
        count!($self, $field, 1)
    };
    ($self:ident, $field:ident, $n:expr) => {{
        let mut c = $self.counts.get();
        c.$field += $n as u64;
        $self.counts.set(c);
    }};
}

impl Gl {
    /// # Safety
    /// `gl` is the context current on this thread, and stays current for as long as the returned Gl is used.
    pub unsafe fn new(gl: Rc<glow::Context>) -> Result<Self, String> {
        let p = proc_address(c"glVertexAttribPointer");
        if p.is_null() {
            return Err("no glVertexAttribPointer".into());
        }
        // SAFETY: the GLES2 entry point of exactly this signature.
        let attrib_pointer = unsafe { std::mem::transmute::<*mut c_void, VertexAttribPointer>(p) };
        Ok(Self {
            gl,
            counts: Cell::new(Counts::default()),
            flush_ns: Cell::new(0),
            attrib_pointer,
        })
    }

    /// The context, for calls the bench does not count (object setup, texture parameters, readback).
    pub fn raw(&self) -> &glow::Context {
        &self.gl
    }
    /// The context itself, shared: for GL users with their own objects (the VR video's player and sphere).
    pub fn context(&self) -> &Rc<glow::Context> {
        &self.gl
    }
    pub fn counts(&self) -> Counts {
        self.counts.get()
    }
    pub fn reset_counts(&self) {
        self.counts.set(Counts::default());
        self.flush_ns.set(0);
    }
    /// Nanoseconds spent inside glFlush since the last `reset_counts`.
    pub fn flush_ns(&self) -> u64 {
        self.flush_ns.get()
    }

    // SAFETY (every method below): plain GL calls on the context current on this thread (the type's contract). The
    // object handles come from that context; the slices are read before each call returns.
    pub fn draw_arrays(&self, mode: u32, first: i32, count: i32) {
        count!(self, draws);
        count!(self, vertices, count.max(0));
        unsafe { self.gl.draw_arrays(mode, first, count) }
    }
    pub fn use_program(&self, p: Option<glow::Program>) {
        count!(self, programs);
        unsafe { self.gl.use_program(p) }
    }
    pub fn uniform_1f(&self, l: Option<&glow::UniformLocation>, x: f32) {
        count!(self, uniforms);
        unsafe { self.gl.uniform_1_f32(l, x) }
    }
    pub fn uniform_2f(&self, l: Option<&glow::UniformLocation>, x: f32, y: f32) {
        count!(self, uniforms);
        unsafe { self.gl.uniform_2_f32(l, x, y) }
    }
    pub fn uniform_3f(&self, l: Option<&glow::UniformLocation>, x: f32, y: f32, z: f32) {
        count!(self, uniforms);
        unsafe { self.gl.uniform_3_f32(l, x, y, z) }
    }
    pub fn uniform_4f(&self, l: Option<&glow::UniformLocation>, x: f32, y: f32, z: f32, w: f32) {
        count!(self, uniforms);
        unsafe { self.gl.uniform_4_f32(l, x, y, z, w) }
    }
    pub fn uniform_4fv(&self, l: Option<&glow::UniformLocation>, v: &[f32]) {
        count!(self, uniforms);
        unsafe { self.gl.uniform_4_f32_slice(l, v) }
    }
    pub fn uniform_matrix_3(&self, l: Option<&glow::UniformLocation>, m: &Mat3) {
        count!(self, uniforms);
        unsafe { self.gl.uniform_matrix_3_f32_slice(l, false, m) }
    }
    pub fn bind_texture(&self, t: Option<glow::Texture>) {
        count!(self, textures);
        unsafe { self.gl.bind_texture(glow::TEXTURE_2D, t) }
    }
    pub fn active_texture(&self, unit: u32) {
        count!(self, textures);
        unsafe { self.gl.active_texture(unit) }
    }
    /// `size` floats at `offset` floats into vertices of `stride` floats, from the bound ARRAY_BUFFER.
    pub fn buffer_attrib(&self, loc: u32, size: i32, stride: i32, offset: i32) {
        count!(self, attribs);
        unsafe {
            self.gl
                .vertex_attrib_pointer_f32(loc, size, glow::FLOAT, false, stride * 4, offset * 4)
        }
    }
    /// Tightly packed `size`-float vertices straight from `data`.
    ///
    /// # Safety
    /// No buffer is bound to ARRAY_BUFFER, and `data` outlives every draw that reads this attribute.
    pub unsafe fn client_attrib(&self, loc: u32, size: i32, data: &[f32]) {
        count!(self, attribs);
        // SAFETY: the caller's contract; the pointer is client memory GL reads at draw time.
        unsafe { (self.attrib_pointer)(loc, size, glow::FLOAT, 0, 0, data.as_ptr().cast()) }
    }
    pub fn enable_attrib(&self, loc: u32) {
        count!(self, attribs);
        unsafe { self.gl.enable_vertex_attrib_array(loc) }
    }
    pub fn disable_attrib(&self, loc: u32) {
        count!(self, attribs);
        unsafe { self.gl.disable_vertex_attrib_array(loc) }
    }
    pub fn enable(&self, cap: u32) {
        count!(self, toggles);
        unsafe { self.gl.enable(cap) }
    }
    pub fn disable(&self, cap: u32) {
        count!(self, toggles);
        unsafe { self.gl.disable(cap) }
    }
    pub fn blend_func(&self, s: u32, d: u32) {
        count!(self, toggles);
        unsafe { self.gl.blend_func(s, d) }
    }
    pub fn blend_equation(&self, mode: u32) {
        count!(self, toggles);
        unsafe { self.gl.blend_equation(mode) }
    }
    /// glTexImage2D of a w x h RGBA8 image into the bound TEXTURE_2D (None: storage only).
    pub fn tex_image(&self, w: u32, h: u32, rgba: Option<&[u8]>) {
        count!(self, uploads);
        count!(self, upload_bytes, u64::from(w) * u64::from(h) * 4);
        unsafe {
            self.gl.tex_image_2d(
                glow::TEXTURE_2D,
                0,
                glow::RGBA as i32,
                w as i32,
                h as i32,
                0,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelUnpackData::Slice(rgba),
            )
        }
    }
    /// glTexSubImage2D of `w` x `h` RGBA8 rows at row `y` of the bound TEXTURE_2D.
    pub fn tex_sub_image(&self, y: u32, w: u32, h: u32, rgba: &[u8]) {
        count!(self, uploads);
        count!(self, upload_bytes, u64::from(w) * u64::from(h) * 4);
        unsafe {
            self.gl.tex_sub_image_2d(
                glow::TEXTURE_2D,
                0,
                0,
                y as i32,
                w as i32,
                h as i32,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelUnpackData::Slice(Some(rgba)),
            )
        }
    }
    pub fn bind_buffer(&self, b: Option<glow::Buffer>) {
        count!(self, buffers);
        unsafe { self.gl.bind_buffer(glow::ARRAY_BUFFER, b) }
    }
    pub fn buffer_data(&self, data: &[f32]) {
        count!(self, buffers);
        count!(self, buffer_bytes, std::mem::size_of_val(data));
        // SAFETY: f32 has no padding; the bytes are the floats.
        let bytes = unsafe {
            std::slice::from_raw_parts(data.as_ptr().cast::<u8>(), std::mem::size_of_val(data))
        };
        unsafe {
            self.gl
                .buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes, glow::DYNAMIC_DRAW)
        }
    }
    pub fn get_error(&self) -> u32 {
        count!(self, errors);
        unsafe { self.gl.get_error() }
    }
    pub fn pixel_store(&self, p: u32, v: i32) {
        count!(self, other);
        unsafe { self.gl.pixel_store_i32(p, v) }
    }
    pub fn viewport(&self, x: i32, y: i32, w: i32, h: i32) {
        count!(self, other);
        unsafe { self.gl.viewport(x, y, w, h) }
    }
    pub fn clear_color(&self, r: f32, g: f32, b: f32, a: f32) {
        count!(self, other);
        unsafe { self.gl.clear_color(r, g, b, a) }
    }
    pub fn clear(&self, mask: u32) {
        count!(self, other);
        unsafe { self.gl.clear(mask) }
    }
    pub fn flush(&self) {
        count!(self, other);
        let t = std::time::Instant::now();
        unsafe { self.gl.flush() }
        self.flush_ns.set(self.flush_ns.get() + t.elapsed().as_nanos() as u64);
    }
    pub fn scissor(&self, x: i32, y: i32, w: i32, h: i32) {
        count!(self, other);
        unsafe { self.gl.scissor(x, y, w, h) }
    }
}

/// Compile and link a program from GLSL sources; the error names the step that failed (the C++ `why`).
pub fn compile_program(
    gl: &glow::Context,
    vertex: &str,
    fragment: &str,
) -> Result<glow::Program, &'static str> {
    compile_program_with(gl, vertex, fragment, &[])
}

/// [`compile_program`], with attributes bound to locations before the link (negative locations are skipped).
pub fn compile_program_with(
    gl: &glow::Context,
    vertex: &str,
    fragment: &str,
    binds: &[(i32, &str)],
) -> Result<glow::Program, &'static str> {
    // SAFETY: plain GL calls on the current context; every shader made here is deleted here.
    unsafe {
        let compile = |kind, source: &str| -> Option<glow::Shader> {
            let s = gl.create_shader(kind).ok()?;
            gl.shader_source(s, source);
            gl.compile_shader(s);
            if gl.get_shader_compile_status(s) {
                Some(s)
            } else {
                gl.delete_shader(s);
                None
            }
        };
        let (v, f) = (
            compile(glow::VERTEX_SHADER, vertex),
            compile(glow::FRAGMENT_SHADER, fragment),
        );
        let (Some(v), Some(f)) = (v, f) else {
            for s in [v, f].into_iter().flatten() {
                gl.delete_shader(s);
            }
            return Err("shader compile");
        };
        let Ok(p) = gl.create_program() else {
            gl.delete_shader(v);
            gl.delete_shader(f);
            return Err("shader link");
        };
        gl.attach_shader(p, v);
        gl.attach_shader(p, f);
        for &(loc, name) in binds {
            if loc >= 0 {
                gl.bind_attrib_location(p, loc as u32, name);
            }
        }
        gl.link_program(p);
        gl.delete_shader(v);
        gl.delete_shader(f);
        if gl.get_program_link_status(p) {
            Ok(p)
        } else {
            gl.delete_program(p);
            Err("shader link")
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Shaders (spike/src/gl.cpp, spike/src/stereo_gl.hpp XR_PROJECT)

// World point (view.rs place_point: canvas plane at z=-1 spanning the FOV, each desktop turned to face the eye, then
// per-desktop placement) -> head rotation -> perspective.
macro_rules! xr_project {
    () => {
        concat!(
            "uniform mat3 view; uniform vec2 half_fov; uniform vec2 stereo;\n",
            "vec4 project(vec3 p) { vec3 q = view * p;\n",
            "  return vec4((q.x + stereo.x * (1.0 + q.z)) / half_fov.x + stereo.y * -q.z, q.y / half_fov.y, 0.0, -q.z); }\n"
        )
    };
}
pub(crate) use xr_project;
const VERTEX: &str = concat!(
    "attribute vec3 pos; attribute vec2 uv; varying vec2 v;\n",
    xr_project!(),
    "void main() { v = uv; gl_Position = project(pos); }\n"
);
// SHM XRGB bytes upload as RGBA and need a B/R swap; imported dmabufs are sampled natively.
const FRAGMENT: &str = concat!(
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float swizzle;\n",
    "void main() { vec4 c = texture2D(tex, v); gl_FragColor = vec4(mix(c.rgb, c.bgr, swizzle), 1.0); }\n"
);
const COLOR_VERTEX: &str = concat!(
    "attribute vec3 pos;\n",
    xr_project!(),
    "void main() { gl_Position = project(pos); }\n"
);
const COLOR_FRAGMENT: &str =
    "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }\n";
// Ambient glow: canvas-pixel coordinates are interpolated perspective-correctly, so the falloff stays even when the head
// rotation tilts the plane. Gaussian falloff outside the framed rect.
const GLOW_VERTEX: &str = concat!(
    "attribute vec3 pos; attribute vec2 px; varying vec2 p;\n",
    xr_project!(),
    "void main() { p = px; gl_Position = project(pos); }\n"
);
const GLOW_FRAGMENT: &str = concat!(
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n",
    "varying vec2 p; uniform vec4 rect; uniform vec3 color; uniform vec2 falloff;\n",
    "void main() { vec2 d2 = max(abs(p - rect.xy) - rect.zw, 0.0); float d = length(d2);\n",
    "  float a = falloff.x * exp(-d * d / (2.0 * falloff.y * falloff.y));\n",
    "  gl_FragColor = vec4(color * a, 1.0); }\n"
);
// Glass pill content (glass.rs render_glass_panel): a premultiplied image on the panel's own surface, faded uniformly.
const PANEL_FRAGMENT: &str = concat!(
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha;\n",
    "void main() { gl_FragColor = texture2D(tex, v) * alpha; }\n"
);
const GLOW_RADIUS_PX: f32 = 40.0;
const GLOW_SIGMA_PX: f32 = 12.0;
const GLOW_INTENSITY: f32 = 0.18;
const RGBA8: u32 = 0x8058;

// ---------------------------------------------------------------------------------------------------------------------
// Programs, meshes, textures

/// The head rotation and FOV a program was last given (uniforms keep their values per program).
#[derive(Clone, Copy, Default)]
struct Projection {
    set: bool,
    view: [u32; 9],
    half: [u32; 2],
    stereo: f32,
}

/// A program drawing in the world (XR_PROJECT): its projection uniforms and what it was last given.
struct Prog {
    id: glow::Program,
    view: Option<glow::UniformLocation>,
    half_fov: Option<glow::UniformLocation>,
    stereo: Option<glow::UniformLocation>,
    last: Projection,
}

impl Prog {
    fn new(gl: &glow::Context, id: glow::Program) -> Self {
        // SAFETY: plain GL queries on the current context.
        unsafe {
            Self {
                id,
                view: gl.get_uniform_location(id, "view"),
                half_fov: gl.get_uniform_location(id, "half_fov"),
                stereo: gl.get_uniform_location(id, "stereo"),
                last: Projection::default(),
            }
        }
    }
}

fn attrib_loc(gl: &glow::Context, p: glow::Program, name: &str) -> i32 {
    // SAFETY: a plain GL query on the current context.
    unsafe { gl.get_attrib_location(p, name) }.map_or(-1, |l| l as i32)
}
fn uniform_loc(gl: &glow::Context, p: glow::Program, name: &str) -> Option<glow::UniformLocation> {
    // SAFETY: a plain GL query on the current context.
    unsafe { gl.get_uniform_location(p, name) }
}

/// This frame's projection: what `program` gives a program that does not have it yet.
struct FrameProjection {
    column_major: Mat3,
    half: [f32; 2],
    eye_offset: f32,
}

fn bits<const N: usize>(v: &[f32; N]) -> [u32; N] {
    v.map(f32::to_bits)
}

/// Use program `p` (unless current); give it this frame's head rotation and FOV unless it still has them.
fn program(gl: &Gl, current: &mut Option<glow::Program>, p: &mut Prog, f: &FrameProjection) {
    if *current != Some(p.id) {
        gl.use_program(Some(p.id));
        *current = Some(p.id);
    }
    if p.last.stereo != f.eye_offset {
        gl.uniform_2f(p.stereo.as_ref(), f.eye_offset, 0.0);
        p.last.stereo = f.eye_offset;
    }
    let (view, half) = (bits(&f.column_major), bits(&f.half));
    if p.last.set && p.last.view == view && p.last.half == half {
        return;
    }
    gl.uniform_matrix_3(p.view.as_ref(), &f.column_major);
    gl.uniform_2f(p.half_fov.as_ref(), f.half[0], f.half[1]);
    p.last.set = true;
    p.last.view = view;
    p.last.half = half;
}

/// What a mesh's second pair of floats per vertex is: (u, v) on the tile, canvas (px, py), or (u, v) on the rect.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Attr {
    TileUv = 0,
    CanvasPx = 1,
    RectUv = 2,
    Disc = 3,
}

/// One strip in its own buffer object, rebuilt only when the inputs it was built from change (bitwise).
#[derive(Default)]
struct Mesh {
    buffer: Option<glow::Buffer>,
    count: i32,
    built: bool,
    key: [u32; 21],
}

/// Bind `m`'s buffer (made on first use); true if it already holds the mesh for `key`.
fn bind_mesh(gl: &Gl, m: &mut Mesh, key: &[f32; 21]) -> bool {
    if m.buffer.is_none() {
        // SAFETY: plain GL call on the current context; deleted in drop_mesh.
        m.buffer = unsafe { gl.raw().create_buffer() }.ok();
    }
    gl.bind_buffer(m.buffer);
    m.built && m.key == bits(key)
}

fn fill_mesh(gl: &Gl, m: &mut Mesh, key: &[f32; 21], data: &[f32], count: usize) -> i32 {
    gl.buffer_data(data);
    m.key = bits(key);
    m.count = count as i32;
    m.built = true;
    m.count
}

fn drop_mesh(gl: &glow::Context, m: &mut Mesh) {
    if let Some(b) = m.buffer.take() {
        // SAFETY: a buffer of the current context.
        unsafe { gl.delete_buffer(b) };
    }
    *m = Mesh::default();
}

/// The mesh drawing canvas rect `rect` on desktop `tile`'s surface (glass: glass px, view.rs glass_mesh), interleaved
/// world xyz then the `attr` pair; `bx`: with RectUv, the part of the image the rect shows (an atlas cell).
#[allow(clippy::too_many_arguments)]
fn build(
    gl: &Gl,
    scratch: &mut Vec<f32>,
    m: &mut Mesh,
    c: &Canvas,
    tile: &FRect,
    on: &Placement,
    rect: &FRect,
    attr: Attr,
    glass: bool,
    bx: Option<&AtlasBox>,
) -> i32 {
    let key = [
        c.view_w,
        c.view_h,
        c.half_w,
        c.half_h,
        tile.x,
        tile.y,
        tile.width,
        tile.height,
        on.x,
        on.y,
        on.distance,
        on.curvature,
        rect.x,
        rect.y,
        rect.width,
        rect.height,
        attr as i32 as f32,
        on.roll,
        if glass { 1.0 } else { 0.0 },
        bx.map_or(-1.0, |b| b.u0),
        bx.map_or(-1.0, |b| b.v0), // a cell, by its corner
    ];
    if bind_mesh(gl, m, &key) {
        return m.count;
    }
    let vertices = if glass {
        glass_mesh(c, tile, on, rect)
    } else {
        tile_mesh(c, tile, on, rect)
    };
    scratch.clear();
    for v in &vertices {
        let (mut a, mut b) = (v.u, v.v);
        if attr == Attr::CanvasPx {
            (a, b) = (v.px, v.py);
        } else if attr == Attr::RectUv {
            a = (v.px - rect.x) / rect.width;
            b = (v.py - rect.y) / rect.height;
            if let Some(bx) = bx {
                a = bx.u0 + a * (bx.u1 - bx.u0);
                b = bx.v0 + b * (bx.v1 - bx.v0);
            }
        }
        scratch.extend_from_slice(&[v.world.x, v.world.y, v.world.z, a, b]);
    }
    fill_mesh(gl, m, &key, scratch, vertices.len())
}

fn build_points(
    gl: &Gl,
    scratch: &mut Vec<f32>,
    m: &mut Mesh,
    key: &[f32; 21],
    points: impl FnOnce() -> Vec<Vec3>,
) -> i32 {
    if bind_mesh(gl, m, key) {
        return m.count;
    }
    let points = points();
    scratch.clear();
    for v in &points {
        scratch.extend_from_slice(&[v.x, v.y, v.z]);
    }
    fill_mesh(gl, m, key, scratch, points.len())
}

const DEG: f32 = std::f32::consts::PI / 180.0;

/// The dot's direction in radians. Undirected: its own plane point's (direction_disc is then canvas_disc exactly).
fn dot_direction(c: &Canvas, x: f32, y: f32, directed: bool, az: f32, el: f32) -> (f32, f32) {
    let (mut a, mut e) = (az * DEG, el * DEG);
    if !directed && c.view_w > 0.0 && c.view_h > 0.0 {
        let wx = (2.0 * f64::from(x.clamp(0.0, c.view_w)) / f64::from(c.view_w) - 1.0)
            * f64::from(c.half_w);
        let wy = (1.0 - 2.0 * f64::from(y.clamp(0.0, c.view_h)) / f64::from(c.view_h))
            * f64::from(c.half_h);
        a = wx.atan2(1.0) as f32;
        e = wy.atan2(wx.hypot(1.0)) as f32;
    }
    (a, e)
}

fn dot_ray(c: &Canvas, p: &FreePointer) -> Vec3 {
    let (a, e) = dot_direction(c, p.x, p.y, p.directed, p.az, p.el);
    Vec3 {
        x: e.cos() * a.sin(),
        y: e.sin(),
        z: -e.cos() * a.cos(),
    }
}

/// A texture fed from a HudImage that uploads only the rows that changed since the last upload.
#[derive(Default)]
struct PanelTexture {
    id: Option<glow::Texture>,
    uploaded: u64,
    pixels: Vec<u8>,
    w: u32,
    h: u32,
}

/// Bind `t` on unit 0 (sampler `tex`'s link-time value); upload `im` if `generation` is new. A slider drag re-renders
/// the panel up to once per frame but changes one row of it: only the rows that differ are uploaded.
fn upload_panel(gl: &Gl, t: &mut PanelTexture, im: &HudImage, generation: u64) {
    gl.active_texture(glow::TEXTURE0);
    gl.bind_texture(t.id);
    if generation == t.uploaded {
        return;
    }
    gl.pixel_store(glow::UNPACK_ALIGNMENT, 4);
    let row = im.width as usize * 4;
    if im.width == t.w && im.height == t.h && t.pixels.len() == im.rgba.len() {
        let (mut top, mut bottom) = (0usize, im.height as usize); // the changed rows: [top, bottom)
        let same = |r: usize| im.rgba[r * row..(r + 1) * row] == t.pixels[r * row..(r + 1) * row];
        while top < bottom && same(top) {
            top += 1;
        }
        while bottom > top && same(bottom - 1) {
            bottom -= 1;
        }
        if top < bottom {
            let span = top * row..bottom * row;
            gl.tex_sub_image(
                top as u32,
                im.width,
                (bottom - top) as u32,
                &im.rgba[span.clone()],
            );
            t.pixels[span.clone()].copy_from_slice(&im.rgba[span]);
        }
    } else {
        gl.tex_image(im.width, im.height, Some(&im.rgba));
        t.pixels = im.rgba.clone();
        t.w = im.width;
        t.h = im.height;
    }
    t.uploaded = generation;
}

struct SourceTexture {
    id: glow::Texture,
    width: u32,
    height: u32,
    valid: bool,
    /// wl_shm bytes (B/R swapped in the shader); false for an imported dmabuf, which samples true RGB.
    swizzle: bool,
}

/// A head-locked card and what it shows.
#[derive(Default)]
struct Card {
    image: Option<Rc<HudImage>>,
    generation: u64,
    alpha: f32,
    drop: f32,
}

#[derive(Clone, Copy, Default)]
struct FreePointer {
    visible: bool,
    x: f32,
    y: f32,
    level: f32,
    directed: bool,
    az: f32,
    el: f32,
    look: PointerLook,
}

/// Every strip a frame draws, each in its own buffer (per desktop where there is one per desktop).
#[derive(Default)]
struct Meshes {
    glow: Mesh,
    border: Mesh,
    disc: [Mesh; 2],
    mark: [Mesh; 2],
    ghost: Mesh,
    drop: [Mesh; 4],
    tile: Vec<Mesh>,
    glass: Vec<Mesh>,
    grab: Vec<Mesh>,
    panel: Vec<Mesh>,
    bar: Vec<Mesh>,
    cap: Vec<Mesh>,    // 4 * desktop + corner
    button: Vec<Mesh>, // 2 * desktop + part
    gear: Vec<Mesh>,
    gear_grab: Vec<Mesh>,
    gear_glyph: Vec<Mesh>,
    options: Vec<Mesh>,
}

impl Meshes {
    fn each(&mut self, mut f: impl FnMut(&mut Mesh)) {
        for m in [&mut self.glow, &mut self.border, &mut self.ghost] {
            f(m);
        }
        self.disc
            .iter_mut()
            .chain(&mut self.mark)
            .chain(&mut self.drop)
            .for_each(&mut f);
        for v in [
            &mut self.tile,
            &mut self.glass,
            &mut self.grab,
            &mut self.panel,
            &mut self.bar,
            &mut self.cap,
            &mut self.button,
            &mut self.gear,
            &mut self.gear_grab,
            &mut self.gear_glyph,
            &mut self.options,
        ] {
            v.iter_mut().for_each(&mut f);
        }
    }
    fn grow(&mut self, n: usize) {
        for v in [
            &mut self.tile,
            &mut self.glass,
            &mut self.grab,
            &mut self.panel,
            &mut self.gear,
            &mut self.gear_grab,
            &mut self.gear_glyph,
            &mut self.options,
            &mut self.bar,
        ] {
            if v.len() < n {
                v.resize_with(n, Mesh::default);
            }
        }
        if self.button.len() < 2 * n {
            self.button.resize_with(2 * n, Mesh::default);
        }
        if self.cap.len() < 4 * n {
            self.cap.resize_with(4 * n, Mesh::default);
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// The compositor

/// The tile, colour, glow, glass and panel programs and their locations.
struct Programs {
    tile: Prog,
    tile_pos: i32,
    tile_uv: i32,
    swizzle: Option<glow::UniformLocation>,
    color: Prog,
    color_pos: i32,
    color_value: Option<glow::UniformLocation>,
    glow: Prog,
    glow_pos: i32,
    glow_px: i32,
    glow_rect: Option<glow::UniformLocation>,
    glow_color: Option<glow::UniformLocation>,
    glow_falloff: Option<glow::UniformLocation>,
    glass: Option<GlassProgram>,
    panel: Option<PanelProgram>,
}

struct GlassProgram {
    p: Prog,
    pos: i32,
    px: i32,
    rect: Option<glow::UniformLocation>,
    radius: Option<glow::UniformLocation>,
    style: Option<glow::UniformLocation>,
    opacity: Option<glow::UniformLocation>,
}

struct PanelProgram {
    p: Prog,
    pos: i32,
    uv: i32,
    alpha: Option<glow::UniformLocation>,
}

/// What global immersive 3D does (the C++ StereoSettings): on or off, its look, per-desktop strength (percent, -1: the
/// global one) and whether each desktop takes part (missing: yes).
#[derive(Clone, Debug, PartialEq)]
pub struct StereoSettings {
    pub enabled: bool,
    pub params: StereoParams,
    pub strengths: Vec<i32>,
    pub desktops: Vec<bool>,
    /// Of the desktops' real depth (their Placement distance); 0: all on the screen plane.
    pub half_baseline: f32,
}

impl Default for StereoSettings {
    fn default() -> Self {
        Self {
            enabled: false,
            params: StereoParams::default(),
            strengths: Vec::new(),
            desktops: Vec::new(),
            half_baseline: STEREO_HALF_BASELINE,
        }
    }
}

/// A desktop's 3D fade and the time of its last depth frame (-1: none yet).
#[derive(Clone, Copy)]
struct StereoSource {
    fader: StereoFader,
    gain: f32,
    depth_ms: f64,
}

impl Default for StereoSource {
    fn default() -> Self {
        Self {
            fader: StereoFader::default(),
            gain: 0.0,
            depth_ms: -1.0,
        }
    }
}

/// Global immersive 3D, all empty until set_stereo first turns it on: a 2D run never compiles its shaders.
#[derive(Default)]
struct Stereo {
    pass: Option<StereoPass>,
    /// The warp program's projection cache (the pass's warp program).
    warp: Option<Prog>,
    why: String,
    set: StereoSettings,
    sources: Vec<StereoSource>,
    scene: StereoFader,
    /// This frame's half baseline.
    b: f32,
    frames: u64,
}

/// The background as last set (gl.cpp env_*), and this frame's eye shift.
#[derive(Clone, Copy)]
struct EnvironmentSet {
    level: f32,
    background: Background,
    gain: f32,
    seconds: f32,
    view: Mat3,
    eye: f32,
}

impl Default for EnvironmentSet {
    fn default() -> Self {
        Self { level: 0.0, background: Background::None, gain: 1.0, seconds: 0.0, view: IDENTITY_M3, eye: 0.0 }
    }
}

/// Milliseconds on a monotonic clock (the C++ now_us() / 1000).
fn now_ms() -> f64 {
    static EPOCH: OnceLock<Instant> = OnceLock::new();
    EPOCH.get_or_init(Instant::now).elapsed().as_secs_f64() * 1000.0
}

impl Stereo {
    /// The 3D budget of desktop `i` this eye: its plane's disparity, its size and its fade.
    fn tile_budget(
        &self,
        i: usize,
        canvas: &Canvas,
        tile: &FRect,
        placement: &Placement,
        rotation: &Mat3,
        eye_w: f32,
    ) -> StereoBudget {
        let c = place_point(canvas, tile, placement, tile.x + tile.width / 2.0, tile.y + tile.height / 2.0);
        let plane = stereo_plane_disparity(self.b, apply(rotation, [c.x, c.y, c.z])[2], eye_w, canvas.half_w);
        let gain = self.sources.get(i).map_or(0.0, |s| s.gain);
        stereo_budget(
            &stereo_limits(&self.set.params, eye_w, canvas.half_w),
            plane,
            stereo_tile_scale(tile.width, canvas.view_w, placement.distance),
            gain,
        )
    }
}

pub struct GlCompositor {
    gl: Rc<Gl>,
    progs: Programs,
    /// Highlight border + glow colour (Hyprland's active border, set by the supervisor); this default until then.
    accent: [f32; 3],
    textures: Vec<SourceTexture>,
    panel_tex: PanelTexture,
    options_tex: PanelTexture,
    /// The level bars', corner caps', gear's and Restore pill's pixels (glass render_handle_atlas): one image per theme
    /// colour, rendered for `handle_accent` (-1: none yet) with `handle_font`, looked up once (none: the pixel font).
    handle_texture: Option<glow::Texture>,
    handle_accent: [f32; 3],
    handle_font: Option<HudFont>,
    handle_font_tried: bool,
    /// set_handle_atlas: an injected atlas drawn in place of the rendered one, and whether it is on the GPU yet.
    handle_atlas: Option<Rc<HudImage>>,
    handle_uploaded: bool,
    width: u32,
    height: u32,
    px_w: u32,
    px_h: u32,
    hud: Option<GlHud>,
    popup: Card,
    help_hud: Option<GlHud>,
    help: Card,
    binds_hud: Option<GlHud>,
    binds: Card,
    /// The VR video's controls card (draw_scene_and_swap), made on its first draw.
    video_hud: Option<GlHud>,
    glass: GlassFrame,
    glass_image: Option<Rc<HudImage>>,
    options_image: Option<Rc<HudImage>>,
    free: FreePointer,
    ghost_w: f32,
    ghost_h: f32,
    drop_target: i32,
    meshes: Meshes,
    scratch: Vec<f32>,
    resolved: Vec<Placement>,
    /// The program in use (None: not known, e.g. after a GlHud draw).
    current: Option<glow::Program>,
    framebuffer: Option<(glow::Framebuffer, glow::Renderbuffer)>,
    vendor: String,
    renderer: String,
    version: String,
    stereo: Stereo,
    /// The background: compiled at setup (not on the first frame it shows); None if its shaders failed.
    environment: Option<EnvironmentPass>,
    env: EnvironmentSet,
    /// GL_MAX blending (ES 3 or EXT_blend_minmax): the highlight's glow over the grid stays under the cap.
    blend_max: bool,
    /// The frame's GPU time (begin_gpu_time / take_gpu_ns); None without GL_EXT_disjoint_timer_query.
    timer: Option<GpuTimer>,
    // Last: the context outlives every object above (Drop deletes them first).
    headless: Headless,
}

impl GlCompositor {
    /// The compositor without a window (tests and benches): a surfaceless GLES 3 context drawing into its own
    /// width x height RGBA8 framebuffer, left bound and current; "swap" is a glFlush. Err (the C++ `why`) without one.
    pub fn create_offscreen(width: u32, height: u32, sources: usize) -> Result<Self, String> {
        Self::create_on(None, width, height, sources)
    }

    /// As [`Self::create_offscreen`], on the GPU of render node `gpu` (None: Mesa's surfaceless choice). The renderer
    /// draws on the compositor's main device so captured and presented dmabufs need no copy.
    pub fn create_on(gpu: Option<&str>, width: u32, height: u32, sources: usize) -> Result<Self, String> {
        let headless = Headless::new(gpu)?;
        // SAFETY: Headless made its context current on this thread; it is dropped after every user of `gl`.
        let gl = Rc::new(unsafe { Gl::new(headless.gl.clone()) }?);
        let raw = gl.raw();
        // SAFETY: plain GL calls on the current context; deleted in Drop.
        let framebuffer = unsafe {
            let fb = raw.create_framebuffer()?;
            raw.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
            let rb = raw.create_renderbuffer()?;
            raw.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
            raw.renderbuffer_storage(glow::RENDERBUFFER, RGBA8, width as i32, height as i32);
            raw.framebuffer_renderbuffer(
                glow::FRAMEBUFFER,
                glow::COLOR_ATTACHMENT0,
                glow::RENDERBUFFER,
                Some(rb),
            );
            if raw.check_framebuffer_status(glow::FRAMEBUFFER) != glow::FRAMEBUFFER_COMPLETE {
                raw.delete_framebuffer(fb);
                raw.delete_renderbuffer(rb);
                return Err("framebuffer".into());
            }
            (fb, rb)
        };
        Self::setup(headless, gl, width, height, sources, Some(framebuffer))
    }

    fn setup(
        headless: Headless,
        gl: Rc<Gl>,
        width: u32,
        height: u32,
        sources: usize,
        framebuffer: Option<(glow::Framebuffer, glow::Renderbuffer)>,
    ) -> Result<Self, String> {
        let raw = gl.raw();
        let string = |name| {
            // SAFETY: a plain GL query on the current context.
            let s = unsafe { raw.get_parameter_string(name) };
            s.chars()
                .filter(|c| (' '..'\x7f').contains(c) && *c != '"' && *c != '\\')
                .take(120)
                .collect::<String>()
        };
        let (vendor, renderer, version) = (
            string(glow::VENDOR),
            string(glow::RENDERER),
            string(glow::VERSION),
        );
        let tile = compile_program(raw, VERTEX, FRAGMENT)?;
        let color = compile_program(raw, COLOR_VERTEX, COLOR_FRAGMENT)?;
        let glow_p = compile_program(raw, GLOW_VERTEX, GLOW_FRAGMENT)?;
        // Glass pills and panels: optional (composition works without them).
        let glass = compile_program(raw, GLOW_VERTEX, glass_fragment_shader())
            .ok()
            .map(|p| GlassProgram {
                p: Prog::new(raw, p),
                pos: attrib_loc(raw, p, "pos"),
                px: attrib_loc(raw, p, "px"),
                rect: uniform_loc(raw, p, "rect"),
                radius: uniform_loc(raw, p, "radius"),
                style: uniform_loc(raw, p, "style"), // glass_style()
                opacity: uniform_loc(raw, p, "opacity"),
            });
        let panel = compile_program(raw, VERTEX, PANEL_FRAGMENT)
            .ok()
            .map(|p| PanelProgram {
                p: Prog::new(raw, p),
                pos: attrib_loc(raw, p, "pos"),
                uv: attrib_loc(raw, p, "uv"),
                alpha: uniform_loc(raw, p, "alpha"),
            });
        let progs = Programs {
            tile: Prog::new(raw, tile),
            tile_pos: attrib_loc(raw, tile, "pos"),
            tile_uv: attrib_loc(raw, tile, "uv"),
            swizzle: uniform_loc(raw, tile, "swizzle"),
            color: Prog::new(raw, color),
            color_pos: attrib_loc(raw, color, "pos"),
            color_value: uniform_loc(raw, color, "color"),
            glow: Prog::new(raw, glow_p),
            glow_pos: attrib_loc(raw, glow_p, "pos"),
            glow_px: attrib_loc(raw, glow_p, "px"),
            glow_rect: uniform_loc(raw, glow_p, "rect"),
            glow_color: uniform_loc(raw, glow_p, "color"),
            glow_falloff: uniform_loc(raw, glow_p, "falloff"),
            glass,
            panel,
        };
        // Optional: composition works without the cards.
        let (hud, help_hud, binds_hud) = (GlHud::new(&gl), GlHud::new(&gl), GlHud::new(&gl));
        let linear_texture = || -> Option<glow::Texture> {
            // SAFETY: plain GL calls on the current context; deleted in Drop.
            unsafe {
                let t = raw.create_texture().ok()?;
                raw.bind_texture(glow::TEXTURE_2D, Some(t));
                for (p, v) in [
                    (glow::TEXTURE_MIN_FILTER, glow::LINEAR),
                    (glow::TEXTURE_MAG_FILTER, glow::LINEAR),
                    (glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
                    (glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
                ] {
                    raw.tex_parameter_i32(glow::TEXTURE_2D, p, v as i32);
                }
                Some(t)
            }
        };
        let (mut panel_tex, mut options_tex, mut handle_texture) =
            (PanelTexture::default(), PanelTexture::default(), None);
        if progs.panel.is_some() {
            panel_tex.id = linear_texture();
            options_tex.id = linear_texture();
            handle_texture = linear_texture();
        }
        let mut textures = Vec::with_capacity(sources);
        for _ in 0..sources {
            let id = linear_texture().ok_or("gl setup")?;
            textures.push(SourceTexture {
                id,
                width: 0,
                height: 0,
                valid: false,
                swizzle: true,
            });
        }
        // optional, and now for the same reason as the cards; the run works without the grid
        let environment = EnvironmentPass::create(raw);
        let blend_max = version.starts_with("OpenGL ES 3")
            || raw.supported_extensions().contains("GL_EXT_blend_minmax");
        let out = Self {
            gl: gl.clone(),
            progs,
            accent: [0x8d as f32 / 255.0; 3],
            textures,
            panel_tex,
            options_tex,
            handle_texture,
            handle_accent: [-1.0; 3],
            handle_font: None,
            handle_font_tried: false,
            handle_atlas: None,
            handle_uploaded: false,
            width,
            height,
            px_w: width,
            px_h: height,
            hud,
            popup: Card::default(),
            help_hud,
            help: Card::default(),
            binds_hud,
            binds: Card::default(),
            video_hud: None,
            glass: GlassFrame::default(),
            glass_image: None,
            options_image: None,
            free: FreePointer::default(),
            ghost_w: 0.0,
            ghost_h: 0.0,
            drop_target: -1,
            meshes: Meshes::default(),
            scratch: Vec::new(),
            resolved: Vec::new(),
            current: None,
            framebuffer,
            vendor,
            renderer,
            version,
            stereo: Stereo::default(),
            environment,
            env: EnvironmentSet::default(),
            blend_max,
            timer: GpuTimer::load(raw),
            headless,
        };
        // SAFETY: a plain GL query on the current context.
        if unsafe { raw.get_error() } != glow::NO_ERROR {
            return Err("gl setup".into());
        }
        Ok(out)
    }

    /// The counted context (call counts, readback).
    pub fn gl(&self) -> &Gl {
        &self.gl
    }

    /// The same context, shared: for GL objects kept outside the compositor (the depth link's shrinks).
    pub fn shared_gl(&self) -> &Rc<Gl> {
        &self.gl
    }

    /// The GL strings, as the C++ report() gives them.
    pub fn strings(&self) -> (&str, &str, &str) {
        (&self.vendor, &self.renderer, &self.version)
    }

    /// `width` x `height`: the canvas every placement, card and tile is laid out in. The frame is drawn into a
    /// px_width x px_height buffer (0: the canvas size): the layout never changes with the scale.
    pub fn resize(&mut self, width: u32, height: u32, px_width: u32, px_height: u32) {
        let (px_width, px_height) = if px_width == 0 || px_height == 0 {
            (width, height)
        } else {
            (px_width, px_height)
        };
        self.width = width;
        self.height = height;
        if px_width == self.px_w && px_height == self.px_h {
            return;
        }
        self.px_w = px_width;
        self.px_h = px_height;
        if let Some((_, rb)) = self.framebuffer {
            let raw = self.gl.raw();
            // SAFETY: plain GL calls on the current context.
            unsafe {
                raw.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
                raw.renderbuffer_storage(
                    glow::RENDERBUFFER,
                    RGBA8,
                    px_width as i32,
                    px_height as i32,
                );
            }
        }
    }

    /// Upload one source frame (32-bit wl_shm XRGB/ARGB, little-endian B,G,R,X bytes, `stride` = width * 4).
    pub fn upload(
        &mut self,
        source: usize,
        pixels: &[u8],
        width: u32,
        height: u32,
        stride: u32,
    ) -> bool {
        let Some(t) = self.textures.get_mut(source) else {
            return false;
        };
        if stride != width.wrapping_mul(4) || pixels.len() < stride as usize * height as usize {
            return false;
        }
        let pixels = &pixels[..stride as usize * height as usize];
        let gl = &*self.gl;
        gl.bind_texture(Some(t.id));
        gl.pixel_store(glow::UNPACK_ALIGNMENT, 4);
        if t.width != width || t.height != height || !t.swizzle {
            gl.tex_image(width, height, Some(pixels));
            t.width = width;
            t.height = height;
        } else {
            gl.tex_sub_image(0, width, height, pixels);
        }
        t.valid = gl.get_error() == glow::NO_ERROR;
        t.swizzle = true;
        t.valid
    }

    /// Whether this context's EGL samples a `fourcc` dmabuf with `modifier` (MOD_INVALID: implicit).
    pub fn dmabuf_importable(&self, fourcc: u32, modifier: u64) -> bool {
        self.headless.dmabuf_importable(fourcc, modifier)
    }

    /// An EGLImage over a dmabuf on this context's display; None if EGL refuses it.
    pub fn import_dmabuf(&self, plane: &DmabufPlane) -> Option<GpuImage> {
        self.headless.import_dmabuf(plane).map(GpuImage)
    }

    pub fn destroy_image(&self, image: GpuImage) {
        self.headless.destroy_image(image.0);
    }

    /// Source `source` samples `image` (an imported capture dmabuf, no copy) until the next upload or bind.
    pub fn bind_source_image(&mut self, source: usize, image: &GpuImage, width: u32, height: u32) -> bool {
        let Some(target) = *IMAGE_TARGET_TEXTURE else {
            return false;
        };
        let Some(t) = self.textures.get_mut(source) else {
            return false;
        };
        let gl = &*self.gl;
        gl.bind_texture(Some(t.id));
        // SAFETY: the texture is bound on the current context; the image is live on this context's display.
        unsafe { target(glow::TEXTURE_2D, image.0.as_ptr()) };
        t.valid = gl.get_error() == glow::NO_ERROR;
        // The storage is the image now (swizzle false): the next SHM upload respecifies, never writes into it.
        (t.width, t.height) = if t.valid { (width, height) } else { (0, 0) };
        t.swizzle = false;
        t.valid
    }

    /// A renderbuffer whose storage is `image` (a swapchain buffer the compositor shows).
    pub fn image_renderbuffer(&self, image: &GpuImage) -> Option<glow::Renderbuffer> {
        let storage = (*IMAGE_TARGET_RENDERBUFFER)?;
        let raw = self.gl.raw();
        // SAFETY: plain GL calls on the current context; the image is live on this context's display.
        unsafe {
            let rb = raw.create_renderbuffer().ok()?;
            raw.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
            storage(glow::RENDERBUFFER, image.0.as_ptr());
            if raw.get_error() != glow::NO_ERROR {
                raw.delete_renderbuffer(rb);
                return None;
            }
            Some(rb)
        }
    }

    pub fn delete_renderbuffer(&self, rb: glow::Renderbuffer) {
        // SAFETY: a renderbuffer made by image_renderbuffer on this context.
        unsafe { self.gl.raw().delete_renderbuffer(rb) };
    }

    /// Copy the drawn frame into `rb` (a swapchain buffer the compositor shows). The copy is flipped: the
    /// framebuffer's first row is the bottom one (GL), a dmabuf's first row is the top one the compositor reads.
    pub fn present_into(&self, rb: glow::Renderbuffer) -> bool {
        let Some((fb, _)) = self.framebuffer else {
            return false;
        };
        let (w, h) = (self.px_w as i32, self.px_h as i32);
        let raw = self.gl.raw();
        // SAFETY: plain GL calls on the current context; `rb` is a live renderbuffer of this context.
        unsafe {
            let Ok(dst) = raw.create_framebuffer() else {
                return false;
            };
            raw.bind_framebuffer(glow::DRAW_FRAMEBUFFER, Some(dst));
            raw.framebuffer_renderbuffer(glow::DRAW_FRAMEBUFFER, glow::COLOR_ATTACHMENT0, glow::RENDERBUFFER, Some(rb));
            let complete = raw.check_framebuffer_status(glow::DRAW_FRAMEBUFFER) == glow::FRAMEBUFFER_COMPLETE;
            if complete {
                raw.bind_framebuffer(glow::READ_FRAMEBUFFER, Some(fb));
                raw.disable(glow::SCISSOR_TEST);
                raw.blit_framebuffer(0, 0, w, h, 0, h, w, 0, glow::COLOR_BUFFER_BIT, glow::NEAREST);
            }
            raw.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
            raw.delete_framebuffer(dst);
            complete && raw.get_error() == glow::NO_ERROR
        }
    }

    /// Wait for the GPU (the offline checks, before a readback). The present does not: it flushes, and the dmabuf's
    /// implicit fence orders the compositor's read after the draw.
    pub fn finish(&self) {
        // SAFETY: a plain GL call on the current context.
        unsafe { self.gl.raw().finish() };
    }

    /// Whether the context has GL_EXT_disjoint_timer_query (the frame's GPU time).
    pub fn gpu_timer(&self) -> bool {
        self.timer.is_some()
    }

    /// Starts timing the GPU work of a frame. Does nothing without the extension, or while the last frame's result
    /// is still to be read (one query, one frame at a time).
    pub fn begin_gpu_time(&mut self) {
        if let Some(t) = self.timer.as_mut().filter(|t| !t.running && !t.pending) {
            // SAFETY: the query made in GpuTimer::load, on the context still current on this thread. Reading the
            // disjoint flag clears it, so a disjoint event seen at take_gpu_ns happened during this query.
            unsafe {
                let _ = self.gl.raw().get_parameter_i32(GPU_DISJOINT_EXT);
                (t.begin)(TIME_ELAPSED_EXT, t.query);
            }
            t.running = true;
        }
    }

    /// Ends the frame begun with [`Self::begin_gpu_time`].
    pub fn end_gpu_time(&mut self) {
        if let Some(t) = self.timer.as_mut().filter(|t| t.running) {
            // SAFETY: as begin_gpu_time.
            unsafe { (t.end)(TIME_ELAPSED_EXT) };
            t.running = false;
            t.pending = true;
        }
    }

    /// The timed frame's GPU time in ns once the GPU has it (after [`Self::finish`] it has; after a flush, a frame or
    /// so later); None while it is not ready, without a timed frame, or when the GPU's timer was disjoint meanwhile (the result is not trusted).
    pub fn take_gpu_ns(&mut self) -> Option<u64> {
        let t = self.timer.as_mut().filter(|t| t.pending)?;
        let mut available = 0u32;
        // SAFETY: as begin_gpu_time; `available` is a valid out pointer.
        unsafe { (t.available)(t.query, QUERY_RESULT_AVAILABLE_EXT, &mut available) };
        if available == 0 {
            return None;
        }
        t.pending = false;
        let mut ns = 0u64;
        // SAFETY: as begin_gpu_time; `ns` is a valid out pointer.
        let disjoint = unsafe {
            (t.result)(t.query, QUERY_RESULT_EXT, &mut ns);
            self.gl.raw().get_parameter_i32(GPU_DISJOINT_EXT) != 0
        };
        (!disjoint).then_some(ns)
    }

    /// A desktop added during the run: a new, empty source at `at` (later sources move up one).
    pub fn insert_source(&mut self, at: usize) {
        let at = at.min(self.textures.len());
        let raw = self.gl.raw();
        // SAFETY: plain GL calls on the current context; deleted in remove_source or Drop.
        let Ok(id) = (unsafe { raw.create_texture() }) else {
            return;
        };
        // SAFETY: as above.
        unsafe {
            raw.bind_texture(glow::TEXTURE_2D, Some(id));
            for (p, v) in [
                (glow::TEXTURE_MIN_FILTER, glow::LINEAR),
                (glow::TEXTURE_MAG_FILTER, glow::LINEAR),
                (glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
                (glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
            ] {
                raw.tex_parameter_i32(glow::TEXTURE_2D, p, v as i32);
            }
        }
        self.textures.insert(
            at,
            SourceTexture {
                id,
                width: 0,
                height: 0,
                valid: false,
                swizzle: true,
            },
        );
        if let Some(pass) = &mut self.stereo.pass {
            pass.insert_source(at);
        }
        let sources = &mut self.stereo.sources;
        sources.insert(at.min(sources.len()), StereoSource::default());
    }

    /// Source `at` dropped with its texture (later sources move down).
    pub fn remove_source(&mut self, at: usize) {
        if at < self.textures.len() {
            let t = self.textures.remove(at);
            // SAFETY: a texture of the current context.
            unsafe { self.gl.raw().delete_texture(t.id) };
        }
        if let Some(pass) = &mut self.stereo.pass {
            pass.remove_source(self.gl.raw(), at);
        }
        if at < self.stereo.sources.len() {
            self.stereo.sources.remove(at);
        }
    }

    pub fn sources(&self) -> usize {
        self.textures.len()
    }

    /// Turn global immersive 3D on or off and set its look. The first time it is on, its shaders are compiled (now, not
    /// on a later frame); if that fails, [`GlCompositor::stereo_readback`] says why and the desktops stay flat. A new
    /// strength rebuilds every desktop's guide from its last depth frame at once, keeping that frame's age.
    pub fn set_stereo(&mut self, settings: &StereoSettings) {
        let clamped = stereo_clamped(&settings.params);
        let st = &mut self.stereo;
        let strength_changed =
            st.set.params.strength != clamped.strength || st.set.strengths != settings.strengths;
        st.set = settings.clone();
        st.set.params = clamped;
        st.set.half_baseline = if settings.half_baseline.is_finite() {
            settings.half_baseline.clamp(0.0, 2.0 * STEREO_HALF_BASELINE)
        } else {
            STEREO_HALF_BASELINE
        };
        if settings.enabled && strength_changed && st.pass.is_some() {
            for i in 0..st.sources.len() {
                let cached = self.stereo.pass.as_ref().and_then(|p| p.cached_frame(i));
                if let Some(mut frame) = cached {
                    // A still desktop produces no new inference. Rebuild from its owned raw upload immediately,
                    // without granting stale depth a new freshness timestamp.
                    let age = self.stereo.sources[i].depth_ms;
                    frame.reset_history = true;
                    self.set_depth(i, &frame);
                    self.stereo.sources[i].depth_ms = age;
                }
            }
        }
        let st = &mut self.stereo;
        if !settings.enabled || st.pass.is_some() || !st.why.is_empty() {
            return;
        }
        let raw = self.gl.raw();
        let p = &self.progs;
        match StereoPass::create(raw, p.tile_pos, p.tile_uv, p.color_pos) {
            Ok(mut pass) => {
                pass.resize_sources(raw, self.textures.len());
                st.warp = Some(Prog::new(raw, pass.warp_program()));
                st.pass = Some(pass);
            }
            Err(why) => st.why = why,
        }
        self.current = None;
    }

    /// The 3D shaders compiled (set_stereo has turned 3D on at least once, on a context that has what they need).
    pub fn stereo_available(&self) -> bool {
        self.stereo.pass.is_some()
    }

    /// A depth frame of desktop `source`, for its current picture. False: no 3D, no picture yet, or an invalid frame.
    pub fn set_depth(&mut self, source: usize, frame: &DepthInput) -> bool {
        let Some(t) = self.textures.get(source) else {
            return false;
        };
        let st = &mut self.stereo;
        let Some(pass) = &mut st.pass else {
            return false;
        };
        if st.sources.len() < self.textures.len() {
            st.sources.resize(self.textures.len(), StereoSource::default());
        }
        let (w, h) = (t.width, t.height);
        if !t.valid || w == 0 || h == 0 {
            return false;
        }
        let state = &mut st.sources[source];
        let now = now_ms();
        let mut params = st.set.params;
        if let Some(&percent) = st.set.strengths.get(source)
            && percent >= 0
        {
            params.strength = percent as f32 / 100.0;
        }
        let dt = if !frame.reset_history && state.depth_ms >= 0.0 {
            (now - state.depth_ms) as f32
        } else {
            0.0
        };
        let ok = pass.update(
            self.gl.raw(),
            source,
            frame,
            t.id,
            w,
            h,
            &params,
            w as f32 / h as f32,
            dt,
        );
        if ok {
            state.depth_ms = now;
        }
        self.current = None;
        ok
    }

    /// Desktop `source` did not change, so its depth frame stays fresh without a new one.
    pub fn keep_depth(&mut self, source: usize) {
        if let Some(s) = self.stereo.sources.get_mut(source)
            && s.depth_ms >= 0.0
        {
            s.depth_ms = now_ms();
        }
    }

    /// Desktop `source`'s current picture: its texture and size (SHM bytes, so B/R swapped). None before its first frame.
    pub fn source_texture(&self, source: usize) -> Option<(glow::Texture, u32, u32)> {
        self.textures
            .get(source)
            .filter(|t| t.valid)
            .map(|t| (t.id, t.width, t.height))
    }

    /// The two eye viewports; none in 2D or until the buffer is 3:1 wide.
    fn stereo_eyes(&self) -> Vec<EyeViewport> {
        if self.px_w < 3 * self.px_h {
            return Vec::new(); // every 2D frame ends here
        }
        let mut eyes = eye_viewports(self.px_w, self.px_h);
        if eyes.len() != 2 {
            eyes.clear();
        }
        eyes
    }

    /// Once per frame, before the eyes: the fades, then the baseline that keeps every desktop's plane inside the limits.
    fn stereo_frame(&mut self, tiles: &[FRect], rotation: &Mat3, canvas: &Canvas, eye_w: f32) {
        let st = &mut self.stereo;
        if st.sources.len() < self.textures.len() {
            st.sources.resize(self.textures.len(), StereoSource::default());
        }
        let now = now_ms();
        for (i, state) in st.sources.iter_mut().enumerate() {
            let wanted = st.set.enabled && st.set.desktops.get(i).copied().unwrap_or(true);
            let fresh = st.pass.as_ref().is_some_and(|p| p.ready(i))
                && state.depth_ms >= 0.0
                && now - state.depth_ms < STEREO_STALE_MS;
            state.gain = state.fader.update(now, wanted && fresh);
        }
        let scene = st.scene.update(now, st.set.enabled);
        let limits = stereo_limits(&st.set.params, eye_w, canvas.half_w);
        let depths: Vec<f32> = tiles
            .iter()
            .zip(&self.resolved)
            .map(|(t, pl)| {
                let c = place_point(canvas, t, pl, t.x + t.width / 2.0, t.y + t.height / 2.0);
                apply(rotation, [c.x, c.y, c.z])[2]
            })
            .collect();
        st.b = stereo_safe_baseline(
            scene * st.set.half_baseline,
            &depths,
            &limits,
            eye_w,
            canvas.half_w,
        );
        st.frames += 1;
    }

    /// The composition report's `stereo` object (gl.cpp report()), once 3D was turned on or failed to: None before.
    pub fn stereo_report(&self) -> Option<String> {
        let st = &self.stereo;
        if st.pass.is_none() && st.why.is_empty() {
            return None;
        }
        Some(
            serde_json::json!({
                "available": st.pass.is_some(),
                "why": st.why,
                "frames": st.frames,
                "depth_updates": st.pass.as_ref().map_or(0, |p| p.updates()),
            })
            .to_string(),
        )
    }

    /// The C++ stereo_readback(): what 3D is doing, as JSON.
    pub fn stereo_readback(&self) -> String {
        let st = &self.stereo;
        let p = &st.set.params;
        let sbs = !self.stereo_eyes().is_empty();
        let desktops: Vec<serde_json::Value> = (0..self.textures.len())
            .map(|i| {
                let configured = st.set.enabled && st.set.desktops.get(i).copied().unwrap_or(true);
                let ready = st.pass.as_ref().is_some_and(|s| s.ready(i));
                let gain = st.sources.get(i).map_or(0.0, |s| s.gain);
                let strength = match st.set.strengths.get(i) {
                    Some(&percent) if percent >= 0 => percent as f32 / 100.0,
                    _ => p.strength,
                };
                serde_json::json!({
                    "configured": configured,
                    "depth_ready": ready,
                    "gain": gain,
                    "drawing": sbs && st.pass.is_some() && ready && gain > 0.0,
                    "strength": strength,
                })
            })
            .collect();
        serde_json::json!({
            "available": st.pass.is_some(),
            "enabled": st.set.enabled,
            "physical_sbs": sbs,
            "error": st.why.chars().take(256).collect::<String>(),
            "frames": st.frames,
            "depth_updates": st.pass.as_ref().map_or(0, |s| s.updates()),
            "params": {
                "strength": p.strength,
                "convergence": p.convergence,
                "near_deg": p.near_deg,
                "far_deg": p.far_deg,
                "text_keep": p.text_keep,
            },
            "desktops": desktops,
        })
        .to_string()
    }

    /// Status pop-up drawn head-locked over the next frames (None or alpha 0: none); re-uploaded when `generation` changes.
    pub fn set_popup(&mut self, image: Option<Rc<HudImage>>, generation: u64, alpha: f32) {
        self.popup = Card {
            image,
            generation,
            alpha,
            drop: 0.0,
        };
    }
    /// Keyboard help card, head-locked in the middle of the view, under the pop-ups; `drop_px` moves it down.
    pub fn set_help(
        &mut self,
        image: Option<Rc<HudImage>>,
        generation: u64,
        alpha: f32,
        drop_px: f32,
    ) {
        self.help = Card {
            image,
            generation,
            alpha,
            drop: drop_px,
        };
    }
    /// Key bind panel, head-locked in the middle of the view over the help card; same rules.
    pub fn set_binds(
        &mut self,
        image: Option<Rc<HudImage>>,
        generation: u64,
        alpha: f32,
        drop_px: f32,
    ) {
        self.binds = Card {
            image,
            generation,
            alpha,
            drop: drop_px,
        };
    }
    /// Glass pills, level bars, caps and the open panels. `image`: the placement panel's content (re-uploaded when
    /// frame.generation changes); `options_image`: the options panel's (frame.options_generation). Nothing: a default frame.
    pub fn set_glass(
        &mut self,
        frame: &GlassFrame,
        image: Option<Rc<HudImage>>,
        options_image: Option<Rc<HudImage>>,
    ) {
        self.glass = frame.clone();
        self.glass_image = image;
        self.options_image = options_image;
    }
    /// Overview free pointer: a grey dot of `level` (capped at FREE_POINTER_HELD_LEVEL) in a black ring at canvas (x, y),
    /// or in direction (az, el) degrees when `directed`; `look`: the marks round it (text: the Serif Beam, no disc).
    #[allow(clippy::too_many_arguments)]
    pub fn set_free_pointer(
        &mut self,
        visible: bool,
        x: f32,
        y: f32,
        level: f32,
        directed: bool,
        az: f32,
        el: f32,
        look: PointerLook,
    ) {
        self.free = FreePointer {
            visible,
            x,
            y,
            level: level.min(FREE_POINTER_HELD_LEVEL),
            directed,
            az,
            el,
            look,
        };
    }
    /// Window drag: a w x h canvas px ghost round the free pointer and an accent frame on desktop `target` (< 0: none).
    pub fn set_window_drag(&mut self, w: f32, h: f32, target: i32) {
        self.ghost_w = w;
        self.ghost_h = h;
        self.drop_target = target;
    }
    /// Highlight border + glow, and the M3 scheme's seed.
    /// The background (environment.rs): `background` drawn first, behind everything, in place of the black clear,
    /// world-locked by `world` (environment_view; world -> eye, row-major), at fade `level` (<= 0 or Background::None:
    /// not drawn), `brightness` in percent, `seconds` on the clock that wraps at ENVIRONMENT_LOOP_S. False: no pass
    /// (its shaders failed): nothing is drawn then. draw_and_swap draws it by itself.
    pub fn set_environment(
        &mut self,
        level: f32,
        background: Background,
        brightness: f32,
        seconds: f32,
        world: &Mat3,
    ) -> bool {
        self.env = EnvironmentSet {
            level: if level.is_finite() { level.min(1.0) } else { 0.0 },
            background,
            gain: brightness / 100.0,
            seconds,
            view: *world,
            eye: self.env.eye,
        };
        self.environment.is_some()
    }

    /// Draw the background set by [`GlCompositor::set_environment`] over the viewport; false when there is none to draw.
    pub fn draw_environment(&mut self, half_w: f32, half_h: f32) -> bool {
        let e = self.env;
        let Some(pass) = self.environment.as_mut() else { return false };
        if e.level <= 0.0 || e.background == Background::None {
            return false;
        }
        pass.draw(
            &self.gl,
            e.background,
            &e.view,
            half_w,
            half_h,
            e.level,
            e.gain,
            environment_line_color(self.accent),
            e.seconds,
            e.eye,
        );
        self.current = pass.program(e.background);
        true
    }

    pub fn set_accent(&mut self, r: u8, g: u8, b: u8) {
        self.accent = [
            f32::from(r) / 255.0,
            f32::from(g) / 255.0,
            f32::from(b) / 255.0,
        ];
    }
    /// An atlas drawn in place of the one the compositor renders for the accent (glass render_handle_atlas), from now
    /// on whatever the accent: uploaded before the next frame that draws a handle. Tests use it to draw the C++ one.
    pub fn set_handle_atlas(&mut self, atlas: Rc<HudImage>) {
        self.handle_atlas = Some(atlas);
        self.handle_uploaded = false;
    }

    /// The handle atlas for the live accent, rendered and uploaded; a no-op for the same colour (C++ upload_handles).
    /// The font is looked up on the first one only: a theme change never looks it up again. An injected atlas
    /// (set_handle_atlas) is uploaded once instead.
    fn upload_handles(&mut self) {
        let atlas = if let Some(injected) = &self.handle_atlas {
            if self.handle_uploaded {
                return;
            }
            self.handle_uploaded = true;
            if !injected.complete() {
                return;
            }
            injected.clone()
        } else {
            if self.accent == self.handle_accent {
                return;
            }
            self.handle_accent = self.accent;
            if !self.handle_font_tried {
                self.handle_font_tried = true;
                self.handle_font = HudFont::load(HUD_FONT_FAMILY).ok();
            }
            let font = self.handle_font.as_ref().map(|f| f as &dyn HudTextMetrics);
            Rc::new(render_handle_atlas(self.accent, font))
        };
        let gl = &self.gl;
        gl.active_texture(glow::TEXTURE0);
        gl.bind_texture(self.handle_texture);
        gl.pixel_store(glow::UNPACK_ALIGNMENT, 4);
        gl.tex_image(atlas.width, atlas.height, Some(&atlas.rgba));
    }

    /// Draw every uploaded source into its rect, placed in space by `placements[i]` (missing: flat), back to front, with
    /// the highlight frame behind `highlight` at `highlight_alpha`, then "swap" (glFlush). `view` rotates the canvas
    /// (identity = head-locked); half_w/h = tan(half FOV). `pan`: an edge pan's layers.
    #[allow(clippy::too_many_arguments)]
    pub fn draw_and_swap(
        &mut self,
        tiles: &[FRect],
        placements: &[Placement],
        highlight: i32,
        highlight_alpha: f32,
        view: &Mat3,
        half_w: f32,
        half_h: f32,
        pan: Option<&PanLayers>,
    ) -> bool {
        self.bind_target();
        let eyes = self.stereo_eyes();
        if eyes.is_empty() {
            self.env.eye = 0.0;
            self.draw_view(
                tiles,
                placements,
                highlight,
                highlight_alpha,
                view,
                half_w,
                half_h,
                None,
                pan,
            );
        } else {
            self.gl.enable(glow::SCISSOR_TEST);
            for e in &eyes {
                self.gl
                    .scissor(e.x as i32, e.y as i32, e.width as i32, e.height as i32);
                self.draw_view(
                    tiles,
                    placements,
                    highlight,
                    highlight_alpha,
                    view,
                    half_w,
                    half_h,
                    Some(e),
                    pan,
                );
            }
            self.gl.disable(glow::SCISSOR_TEST);
            self.gl.viewport(0, 0, self.px_w as i32, self.px_h as i32);
        }
        let ok = self.gl.get_error() == glow::NO_ERROR;
        ok && {
            self.gl.flush();
            true
        }
    }

    /// Draw a scene of its own in place of the desktops (`scene` gets the compositor and the buffer's pixel size; its
    /// viewports are buffer pixels), then `card` head-locked near the bottom at `card_alpha` (re-uploaded when
    /// `card_generation` changes) and the status pop-up over it, in each eye, then "swap" (glFlush). The VR video.
    pub fn draw_scene_and_swap(
        &mut self,
        scene: impl FnOnce(&mut Self, u32, u32),
        card: Option<&HudImage>,
        card_generation: u64,
        card_alpha: f32,
    ) -> bool {
        self.bind_target(); // whatever ran since the last frame (mpv) may have bound another framebuffer
        self.env.eye = 0.0;
        scene(self, self.px_w, self.px_h);
        self.current = None; // the scene used programs of its own
        self.gl.bind_buffer(None); // GlHud draws from client arrays
        let card = card.filter(|_| card_alpha > 0.0);
        if card.is_some() && self.video_hud.is_none() {
            self.video_hud = GlHud::new(&self.gl);
        }
        let (width, height) = (self.width, self.height);
        let overlays = |c: &mut Self| {
            if let (Some(hud), Some(image)) = (&mut c.video_hud, card) {
                hud.draw(image, card_generation, card_alpha, width, height, false, 0.0);
            }
            if let (Some(hud), Some(image)) = (&mut c.hud, &c.popup.image) {
                hud.draw(image, c.popup.generation, c.popup.alpha, width, height, false, 0.0);
            }
        };
        let eyes = self.stereo_eyes();
        if eyes.is_empty() {
            self.gl.viewport(0, 0, self.px_w as i32, self.px_h as i32);
            overlays(self);
        } else {
            // the same picture in each eye: on the screen plane
            for e in &eyes {
                self.gl.viewport(e.x as i32, e.y as i32, e.width as i32, e.height as i32);
                overlays(self);
            }
            self.gl.viewport(0, 0, self.px_w as i32, self.px_h as i32);
        }
        let ok = self.gl.get_error() == glow::NO_ERROR;
        ok && {
            self.gl.flush();
            true
        }
    }

    /// Bind the buffer the frame is drawn into.
    fn bind_target(&self) {
        let raw = self.gl.raw();
        // SAFETY: a plain GL call on the current context; the framebuffer is this compositor's own.
        unsafe { raw.bind_framebuffer(glow::FRAMEBUFFER, self.framebuffer.map(|(fb, _)| fb)) };
    }

    /// The drawn buffer's RGB, bottom row first (glReadPixels order), 3 bytes per pixel.
    pub fn read_rgb(&self) -> Vec<u8> {
        let (w, h) = (self.px_w as usize, self.px_h as usize);
        let mut rgba = vec![0u8; w * h * 4];
        let raw = self.gl.raw();
        // SAFETY: rgba holds w x h RGBA8 pixels; the framebuffer is bound.
        unsafe {
            raw.pixel_store_i32(glow::PACK_ALIGNMENT, 4);
            raw.read_pixels(
                0,
                0,
                w as i32,
                h as i32,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelPackData::Slice(Some(&mut rgba)),
            );
        }
        rgba.chunks_exact(4)
            .flat_map(|p| [p[0], p[1], p[2]])
            .collect()
    }

    #[allow(clippy::too_many_arguments)]
    fn draw_view(
        &mut self,
        tiles: &[FRect],
        placements: &[Placement],
        highlight: i32,
        highlight_alpha: f32,
        view: &Mat3,
        half_w: f32,
        half_h: f32,
        eye: Option<&EyeViewport>,
        pan: Option<&PanLayers>,
    ) {
        let gl = self.gl.clone();
        let gl = &*gl;
        // eye_offset: this eye's stereo.x; 0 in 2D, and no program then gets a call for it
        let mut fp = FrameProjection {
            column_major: transpose(view),
            half: [half_w, half_h],
            eye_offset: 0.0,
        };
        self.current = None; // whatever ran since the last frame (a GlHud) may have changed the program
        let canvas = Canvas {
            view_w: self.width as f32,
            view_h: self.height as f32,
            half_w,
            half_h,
        };
        self.resolved.clear();
        self.resolved
            .extend((0..tiles.len()).map(|i| placements.get(i).copied().unwrap_or_default()));
        let eye_w = eye.map_or(self.px_w, |e| e.width) as f32;
        if let Some(e) = eye {
            if e.eye == 0 {
                self.stereo_frame(tiles, view, &canvas, eye_w);
            }
            fp.eye_offset = stereo_side(e.eye as u32) * self.stereo.b;
            self.env.eye = stereo_side(e.eye as u32)
                * stereo_infinity_shift(
                    self.stereo.b,
                    &stereo_limits(&self.stereo.set.params, eye_w, half_w),
                    eye_w,
                    half_w,
                );
        }
        self.meshes.grow(tiles.len());
        let attrib = |loc: i32, size: i32, stride: i32, offset: i32| {
            gl.buffer_attrib(loc as u32, size, stride, offset);
            gl.enable_attrib(loc as u32);
        };
        match eye {
            Some(e) => gl.viewport(e.x as i32, e.y as i32, e.width as i32, e.height as i32),
            None => gl.viewport(0, 0, self.px_w as i32, self.px_h as i32),
        }
        let complete = |im: &Option<Rc<HudImage>>| im.clone().filter(|im| im.complete());
        let has_glass_panel = self.progs.glass.is_some() && self.progs.panel.is_some();
        // A new panel image is uploaded before anything is drawn (the driver flushes queued work before a large upload
        // from client memory), under exactly the conditions on which the glass loop below draws it.
        if has_glass_panel
            && self.glass.content >= 0
            && self.glass_image.is_some()
            && self.glass.generation != self.panel_tex.uploaded
        {
            let g = &self.glass;
            let c = g.content as usize;
            if let Some(im) = complete(&self.glass_image)
                && c < tiles.len()
                && c < self.textures.len()
                && c < g.expand.len()
                && g.expand[c].clamp(0.0, 1.0) > 0.0
                && g.content_alpha > 0.0
            {
                upload_panel(gl, &mut self.panel_tex, &im, g.generation);
            }
        }
        if has_glass_panel
            && self.glass.options
            && self.options_image.is_some()
            && self.glass.options_generation != self.options_tex.uploaded
        {
            // The options panel's image, uploaded early for the same reason, under the conditions the gear morph draws it.
            let g = &self.glass;
            if let Some(im) = complete(&self.options_image)
                && g.options_desktop >= 0
                && (g.options_desktop as usize) < tiles.len()
                && (g.options_desktop as usize) < self.textures.len()
                && g.options_expand > 0.0
            {
                upload_panel(gl, &mut self.options_tex, &im, g.options_generation);
            }
        }
        let accent = self.accent;
        let handles = (self.glass.pills || highlight_alpha < 0.5)
            && self.progs.panel.is_some()
            && self.handle_texture.is_some();
        if handles {
            self.upload_handles(); // (the same reason: only on a theme change)
        }
        // The background covers every pixel in place of the clear; with none (or level 0) the frame is drawn exactly as
        // without it.
        let environment = self.draw_environment(half_w, half_h);
        if !environment {
            gl.clear_color(0.0, 0.0, 0.0, 1.0);
            gl.clear(glow::COLOR_BUFFER_BIT);
        }
        let blend_max = self.blend_max;

        let p = &mut self.progs;
        let m = &mut self.meshes;
        let scratch = &mut self.scratch;
        let current = &mut self.current;
        let resolved = &self.resolved;
        // Drawn first over black, so no blending is needed: glow, then border, then the tiles (back to front) cover the
        // interior. The frame lies on the highlighted desktop's own surface (same placement and curvature). Over the
        // background the glow is the brighter of the two and the border is laid over it (premultiplied), so neither
        // blacks out a halo.
        let mut frame_of = |h: usize, alpha: f32| {
            let t = &tiles[h];
            let framed = FRect {
                x: t.x - BORDER_PX,
                y: t.y - BORDER_PX,
                width: t.width + 2.0 * BORDER_PX,
                height: t.height + 2.0 * BORDER_PX,
            };
            let halo = FRect {
                x: framed.x - GLOW_RADIUS_PX,
                y: framed.y - GLOW_RADIUS_PX,
                width: framed.width + 2.0 * GLOW_RADIUS_PX,
                height: framed.height + 2.0 * GLOW_RADIUS_PX,
            };
            if environment {
                // the glow over the grid: the brighter of the two (else added)
                gl.enable(glow::BLEND);
                gl.blend_func(glow::ONE, glow::ONE);
                if blend_max {
                    gl.blend_equation(glow::MAX);
                }
            }
            program(gl, current, &mut p.glow, &fp);
            gl.uniform_4f(
                p.glow_rect.as_ref(),
                framed.x + framed.width / 2.0,
                framed.y + framed.height / 2.0,
                framed.width / 2.0,
                framed.height / 2.0,
            );
            gl.uniform_3f(p.glow_color.as_ref(), accent[0], accent[1], accent[2]);
            gl.uniform_2f(
                p.glow_falloff.as_ref(),
                GLOW_INTENSITY * alpha,
                GLOW_SIGMA_PX,
            );
            let n = build(
                gl,
                scratch,
                &mut m.glow,
                &canvas,
                t,
                &resolved[h],
                &halo,
                Attr::CanvasPx,
                false,
                None,
            );
            attrib(p.glow_pos, 3, 5, 0);
            attrib(p.glow_px, 2, 5, 3);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
            gl.disable_attrib(p.glow_px as u32);
            program(gl, current, &mut p.color, &fp);
            if environment {
                if blend_max {
                    gl.blend_equation(glow::FUNC_ADD);
                }
                gl.blend_func(glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
            }
            gl.uniform_4f(
                p.color_value.as_ref(),
                accent[0] * alpha,
                accent[1] * alpha,
                accent[2] * alpha,
                if environment { alpha } else { 1.0 },
            ); // over black (or premultiplied over the grid)
            let n = build(
                gl,
                scratch,
                &mut m.border,
                &canvas,
                t,
                &resolved[h],
                &framed,
                Attr::TileUv,
                false,
                None,
            );
            attrib(p.color_pos, 3, 5, 0);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
            if environment {
                gl.disable(glow::BLEND);
            }
        };
        if let Some(pan) = pan
            && pan.left < tiles.len()
            && pan.left_alpha > 0.0
        {
            frame_of(pan.left, pan.left_alpha);
        }
        if highlight >= 0 && (highlight as usize) < tiles.len() && highlight_alpha > 0.0 {
            frame_of(highlight as usize, highlight_alpha);
        }
        // Zoomed in (highlight_alpha = 1 - zoom), the zoomed desktop is drawn on top of any desktop dragged nearer. An
        // edge pan's two are on top throughout.
        let back_to_front = draw_order(&canvas, tiles, resolved);
        let order = match pan {
            Some(pan) => pan_draw_order(back_to_front, pan),
            None if highlight >= 0 => {
                zoom_draw_order(back_to_front, 1.0 - highlight_alpha, highlight as usize)
            }
            None => back_to_front,
        };
        let g = &self.glass;
        let free = self.free;
        let textures = &self.textures;
        let st = &mut self.stereo;
        let handle_texture = self.handle_texture;
        if handles && let Some(panel) = &mut p.panel {
            // Level bars and corner caps: drawn before every desktop (over the border and glow, back to front), so any
            // desktop's content covers them and the glass goes over them: they show only where nothing else is. Tile px
            // on the desktop's own live surface. In the theme's M3 colours (the atlas), blended for their anti-aliased
            // edges; only what the dot is on or holds lights up.
            if p.tile_uv >= 0 {
                gl.disable_attrib(p.tile_uv as u32);
            }
            gl.enable(glow::BLEND);
            gl.blend_func(glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
            program(gl, current, &mut panel.p, &fp);
            gl.uniform_1f(panel.alpha.as_ref(), 1.0);
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(handle_texture); // sampler `tex` = unit 0 (link-time value)
            let (pos, uv) = (panel.pos, panel.uv);
            let image =
                |mesh: &mut Mesh, scratch: &mut Vec<f32>, i: usize, r: &FRect, bx: &AtlasBox| {
                    let n = build(
                        gl,
                        scratch,
                        mesh,
                        &canvas,
                        &tiles[i],
                        &resolved[i],
                        r,
                        Attr::RectUv,
                        false,
                        Some(bx),
                    );
                    attrib(pos, 3, 5, 0);
                    attrib(uv, 2, 5, 3);
                    gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
                };
            for &i in &order {
                if i >= textures.len() || i >= g.bars.len() {
                    continue;
                }
                if !g.pills || !g.bars[i] {
                    continue;
                }
                // The maximize icon (and its hint while the dot is on it), through the bar's mesh as the C++ draws it.
                let r = options_maximize(&tiles[i]);
                let mut hot = false;
                if free.visible {
                    let ray = dot_ray(&canvas, &free);
                    let hit = options_maximize_ray(&canvas, &tiles[i], &resolved[i], &ray);
                    hot = hit.is_some();
                    if let Some(hit) = hit {
                        for j in 0..tiles.len() {
                            if let Some(block) =
                                stereo_surface_hit(&canvas, &tiles[j], &resolved[j], &ray)
                            {
                                let w = block.world;
                                if (w.x * w.x + w.y * w.y + w.z * w.z).sqrt() < hit {
                                    hot = false;
                                }
                            }
                        }
                    }
                }
                image(&mut m.bar[i], scratch, i, &r, &options_icon_box(hot));
                if hot {
                    let scale = level_bar_scale(&tiles[i]);
                    let hint = FRect {
                        x: r.x,
                        y: r.y - 24.0 * scale,
                        width: 84.0 * scale,
                        height: 20.0 * scale,
                    };
                    image(&mut m.bar[i], scratch, i, &hint, &options_hint_box());
                }
                for corner in 0..4u32 {
                    let hot = i as i32 == g.cap_hover && corner == g.cap_corner;
                    let bx = corner_cap_box(corner, hot, hot && g.cap_active);
                    image(
                        &mut m.cap[4 * i + corner as usize],
                        scratch,
                        i,
                        &corner_cap_rect(&tiles[i], corner),
                        &bx,
                    );
                }
                let hot = i as i32 == g.bar_hover;
                let bx = level_bar_box(
                    if hot {
                        g.bar_segment
                    } else {
                        LevelSegment::None
                    },
                    hot && g.bar_active,
                );
                image(&mut m.bar[i], scratch, i, &level_bar_rect(&tiles[i]), &bx);
                // the 3D button, joined to the bar
                let (main, tail) = (
                    immersive_main_rect(&tiles[i]),
                    immersive_tail_rect(&tiles[i]),
                );
                let on_button = i as i32 == g.immersive_hover;
                if main.width > 0.0 {
                    let lit = i < g.immersive.len() && g.immersive[i];
                    image(
                        &mut m.button[2 * i],
                        scratch,
                        i,
                        &main,
                        &immersive_main_box(lit, on_button && !g.immersive_tail),
                    );
                    let bx = immersive_tail_box(g.immersive_global, on_button && g.immersive_tail);
                    image(&mut m.button[2 * i + 1], scratch, i, &tail, &bx);
                }
            }
            gl.disable_attrib(panel.uv as u32);
            gl.disable(glow::BLEND);
        }
        program(gl, current, &mut p.tile, &fp);
        gl.active_texture(glow::TEXTURE0); // sampler `tex` = unit 0 (its link-time value)
        let mut swizzle = -1.0; // the value set this frame (-1: none yet)
        let mut warping = false; // in 3D: this tile goes through the warp program (same attribute locations)
        for &i in &order {
            let Some(t) = textures.get(i) else { continue };
            if !t.valid {
                continue; // off-screen tiles are clipped by GL (may rotate into view)
            }
            let warp = match (eye, &st.pass) {
                (Some(e), Some(pass)) if pass.ready(i) && st.sources.get(i).is_some_and(|s| s.gain > 0.0) => {
                    Some((e, pass))
                }
                _ => None,
            };
            if warp.is_some() != warping {
                warping = warp.is_some();
                swizzle = -1.0;
                match &mut st.warp {
                    Some(w) if warping => program(gl, current, w, &fp),
                    _ => program(gl, current, &mut p.tile, &fp),
                }
            }
            let want = if t.swizzle { 1.0 } else { 0.0 }; // SHM bytes: B/R swapped; imported dmabufs are RGB
            if swizzle != want {
                gl.uniform_1f(warp.map_or(p.swizzle.as_ref(), |(_, pass)| pass.warp_swizzle()), want);
                swizzle = want;
            }
            if let Some((e, pass)) = warp {
                let budget = st.tile_budget(i, &canvas, &tiles[i], &resolved[i], view, eye_w);
                pass.warp_uniforms(gl, i, e.eye as u32, &budget);
            }
            gl.bind_texture(Some(t.id));
            let n = build(
                gl,
                scratch,
                &mut m.tile[i],
                &canvas,
                &tiles[i],
                &resolved[i],
                &tiles[i],
                Attr::TileUv,
                false,
                None,
            );
            attrib(p.tile_pos, 3, 5, 0);
            attrib(p.tile_uv, 2, 5, 3);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
        }
        // The Step-Back Band's Restore pill: above the picture, on its stepped surface, fading in with the step.
        if handles
            && g.restore_alpha > 0.0
            && highlight >= 0
            && (highlight as usize) < tiles.len()
            && let Some(panel) = &mut p.panel
        {
            let i = highlight as usize;
            let scale = options_step_scale(&tiles[i]);
            let r = options_restore_rect(&tiles[i], scale);
            let hot = free.visible
                && options_mesh_ray(
                    &tile_mesh(
                        &canvas,
                        &tiles[i],
                        &resolved[i],
                        &options_restore_hit(&r, scale),
                    ),
                    &dot_ray(&canvas, &free),
                )
                .is_some();
            if p.tile_uv >= 0 {
                gl.disable_attrib(p.tile_uv as u32);
            }
            gl.enable(glow::BLEND);
            gl.blend_func(glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
            program(gl, current, &mut panel.p, &fp);
            gl.uniform_1f(panel.alpha.as_ref(), g.restore_alpha);
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(handle_texture);
            let bx = options_restore_box(hot);
            let n = build(
                gl,
                scratch,
                &mut m.bar[i],
                &canvas,
                &tiles[i],
                &resolved[i],
                &r,
                Attr::RectUv,
                false,
                Some(&bx),
            );
            attrib(panel.pos, 3, 5, 0);
            attrib(panel.uv, 2, 5, 3);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
            gl.disable_attrib(panel.uv as u32);
            gl.disable(glow::BLEND);
        }
        if let Some(glass) = &mut p.glass {
            // Glass pills: blended over the desktops, each on its own desktop's surface, back to front, except that the
            // open panel's desktop comes last (glass_draw_order). The open one morphs into its placement panel; its
            // content fades in over the glass. While a slider is held the open panel stays on its press-time surface
            // (GlassFrame::surface). Glass keeps its size whatever the desktop's (view.rs glass_mesh).
            if p.tile_uv >= 0 {
                gl.disable_attrib(p.tile_uv as u32);
            }
            gl.enable(glow::BLEND);
            gl.blend_func(glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
            let glass_on = |i: usize| -> Placement {
                match g.surface {
                    Some(s) if i as i32 == g.surface_desktop => s,
                    _ => resolved[i],
                }
            };
            let glass_image = complete(&self.glass_image);
            let options_image = complete(&self.options_image);
            for i in glass_draw_order(order, g.top) {
                // the tiles are drawn: hand their order over
                let mut ctx = GlassCtx {
                    gl,
                    current: &mut *current,
                    glass: &mut *glass,
                    panel: p.panel.as_mut(),
                    fp: &fp,
                    canvas: &canvas,
                    tiles,
                    scratch: &mut *scratch,
                    on: glass_on(i),
                    i,
                };
                if i < textures.len() && i < g.expand.len() {
                    ctx.pill(g, &accent, m, glass_image.as_deref(), &mut self.panel_tex);
                }
                if i < textures.len() {
                    ctx.gear(
                        g,
                        &accent,
                        m,
                        options_image.as_deref(),
                        &mut self.options_tex,
                        handle_texture,
                    );
                }
            }
            gl.disable_attrib(glass.px as u32);
            gl.disable(glow::BLEND);
        }
        if free.visible && self.drop_target >= 0 && (self.drop_target as usize) < tiles.len() {
            // Window drag: the desktop the window would land on, framed in the accent on its own surface.
            let d = self.drop_target as usize;
            if p.tile_uv >= 0 {
                gl.disable_attrib(p.tile_uv as u32);
            }
            program(gl, current, &mut p.color, &fp);
            gl.uniform_4f(p.color_value.as_ref(), accent[0], accent[1], accent[2], 1.0);
            for (k, band) in drop_frame(&tiles[d]).iter().enumerate() {
                let n = build(
                    gl,
                    scratch,
                    &mut m.drop[k],
                    &canvas,
                    &tiles[d],
                    &resolved[d],
                    band,
                    Attr::TileUv,
                    false,
                    None,
                );
                attrib(p.color_pos, 3, 5, 0);
                gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
            }
        }
        gl.bind_buffer(None); // GlHud draws from client arrays
        let (width, height) = (self.width, self.height);
        // The help card, then the free pointer on top of it: the pointer clicks the card's page arrows.
        for (hud, card) in [
            (&mut self.help_hud, &self.help),
            (&mut self.binds_hud, &self.binds),
        ] {
            // (the supervisor never shows both cards)
            if let (Some(hud), Some(image)) = (hud, &card.image)
                && card.alpha > 0.0
            {
                hud.draw(
                    image,
                    card.generation,
                    card.alpha,
                    width,
                    height,
                    true,
                    card.drop,
                );
                *current = None;
            }
        }
        if free.visible && free.level > 0.0 {
            // Overview free pointer: a black ring, then the grey dot, on the canvas plane, or turned to its direction
            // round you when one is given (the supervisor always gives one).
            if p.tile_uv >= 0 {
                gl.disable_attrib(p.tile_uv as u32);
            }
            let mut dot_color = p.color_value.as_ref();
            if let (Some(e), Some(pass)) = (eye, &st.pass) {
                // In 3D the pointer sits on what it points at, a pixel in front of it: it gets the disparity of the
                // surface under it, the desktop's plane plus the depth under the dot, and none of its own (it is drawn
                // at radius 1).
                let ray = dot_ray(&canvas, &free);
                let (mut on, mut nearest, mut u, mut v, mut z) = (-1i32, 1e9f32, 0.0, 0.0, -1.0);
                for i in 0..tiles.len() {
                    let Some(hit) = stereo_surface_hit(&canvas, &tiles[i], &resolved[i], &ray) else {
                        continue;
                    };
                    let q = apply(view, [hit.world.x, hit.world.y, hit.world.z]);
                    if -q[2] < nearest {
                        nearest = -q[2];
                        on = i as i32;
                        u = hit.u;
                        v = hit.v;
                        z = q[2];
                    }
                }
                let budget = if on >= 0 {
                    let o = on as usize;
                    st.tile_budget(o, &canvas, &tiles[o], &resolved[o], view, eye_w)
                } else {
                    StereoBudget::default()
                };
                let side = stereo_side(e.eye as u32);
                let shift = side * stereo_plane_disparity(st.b, z, eye_w, half_w) / eye_w + side / eye_w;
                if *current != Some(pass.pointer_program()) {
                    gl.use_program(Some(pass.pointer_program()));
                    *current = Some(pass.pointer_program());
                }
                gl.uniform_matrix_3(pass.pointer_view(), &fp.column_major);
                gl.uniform_2f(pass.pointer_half_fov(), half_w, half_h);
                pass.pointer_uniforms(gl, on, u, v, e.eye as u32, &budget, eye_w, shift);
                dot_color = pass.pointer_color();
            } else {
                program(gl, current, &mut p.color, &fp);
            }
            let (x, y, directed, az, el) = (free.x, free.y, free.directed, free.az, free.el);
            if self.ghost_w > 0.0 && self.ghost_h > 0.0 {
                // window drag: the window's ghost round the dot, under it
                let (w, h) = (self.ghost_w, self.ghost_h);
                let key = pad([
                    canvas.view_w,
                    canvas.view_h,
                    canvas.half_w,
                    canvas.half_h,
                    x,
                    y,
                    w,
                    h,
                    11.0,
                    flag(directed),
                    az,
                    el,
                ]);
                let n = build_points(gl, scratch, &mut m.ghost, &key, || {
                    let (a, e) = dot_direction(&canvas, x, y, directed, az, el);
                    direction_outline(&canvas, x, y, a, e, Size { w, h }, GHOST_THICKNESS_PX)
                });
                gl.uniform_4f(dot_color, GHOST_LEVEL, GHOST_LEVEL, GHOST_LEVEL, 1.0);
                attrib(p.color_pos, 3, 3, 0);
                gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
            }
            if free.look != PointerLook::Plain {
                for k in 0..2 {
                    // what a press would do: black outlines, then grey
                    let level = if k == 1 { free.level } else { 0.0 };
                    let grow = if k == 1 { 0.0 } else { FREE_POINTER_RING_PX };
                    let key = pad([
                        canvas.view_w,
                        canvas.view_h,
                        canvas.half_w,
                        canvas.half_h,
                        x,
                        y,
                        free.look as u8 as f32,
                        grow,
                        13.0,
                        flag(directed),
                        az,
                        el,
                    ]);
                    let n = build_points(gl, scratch, &mut m.mark[k], &key, || {
                        let (a, e) = dot_direction(&canvas, x, y, directed, az, el);
                        direction_marks(&canvas, x, y, a, e, free.look, grow)
                    });
                    gl.uniform_4f(dot_color, level, level, level, 1.0);
                    attrib(p.color_pos, 3, 3, 0);
                    gl.draw_arrays(glow::TRIANGLES, 0, n);
                }
            }
            if free.look != PointerLook::Text {
                for k in 0..2 {
                    // the Serif Beam stands in for the disc
                    let radius = if k == 1 {
                        FREE_POINTER_RADIUS_PX
                    } else {
                        FREE_POINTER_RADIUS_PX + FREE_POINTER_RING_PX
                    };
                    let level = if k == 1 { free.level } else { 0.0 };
                    let key = pad([
                        canvas.view_w,
                        canvas.view_h,
                        canvas.half_w,
                        canvas.half_h,
                        x,
                        y,
                        radius,
                        Attr::Disc as i32 as f32,
                        flag(directed),
                        az,
                        el,
                    ]);
                    let n = build_points(gl, scratch, &mut m.disc[k], &key, || {
                        if directed {
                            direction_disc(
                                &canvas,
                                x,
                                y,
                                az * DEG,
                                el * DEG,
                                radius,
                                FREE_POINTER_SEGMENTS,
                            )
                        } else {
                            canvas_disc(&canvas, x, y, radius, FREE_POINTER_SEGMENTS)
                        }
                    });
                    gl.uniform_4f(dot_color, level, level, level, 1.0);
                    attrib(p.color_pos, 3, 3, 0);
                    gl.draw_arrays(glow::TRIANGLE_FAN, 0, n);
                }
            }
            gl.bind_buffer(None);
        }
        if let (Some(hud), Some(image)) = (&mut self.hud, &self.popup.image) {
            // head-locked, on top
            hud.draw(
                image,
                self.popup.generation,
                self.popup.alpha,
                width,
                height,
                false,
                0.0,
            );
        }
    }
}

fn flag(b: bool) -> f32 {
    if b { 1.0 } else { 0.0 }
}

/// A mesh key from its leading values (the rest zero).
fn pad<const N: usize>(v: [f32; N]) -> [f32; 21] {
    let mut out = [0.0; 21];
    out[..N].copy_from_slice(&v);
    out
}

/// What the glass loop's pill and gear draws share, for desktop `i` on surface `on`.
struct GlassCtx<'a> {
    gl: &'a Gl,
    current: &'a mut Option<glow::Program>,
    glass: &'a mut GlassProgram,
    panel: Option<&'a mut PanelProgram>,
    fp: &'a FrameProjection,
    canvas: &'a Canvas,
    tiles: &'a [FRect],
    scratch: &'a mut Vec<f32>,
    on: Placement,
    i: usize,
}

impl GlassCtx<'_> {
    /// One glass capsule: `r` with corner `radius`, in `look`, at `opacity`, its mesh `margin` px bigger for the shadow.
    fn shape(
        &mut self,
        mesh: &mut Mesh,
        r: &FRect,
        radius: f32,
        look: &GlassLook,
        opacity: f32,
        margin: f32,
    ) {
        let (gl, g) = (self.gl, &mut *self.glass);
        program(gl, self.current, &mut g.p, self.fp);
        gl.uniform_4f(
            g.rect.as_ref(),
            r.x + r.width / 2.0,
            r.y + r.height / 2.0,
            r.width / 2.0,
            r.height / 2.0,
        );
        gl.uniform_1f(g.radius.as_ref(), radius);
        let style = glass_style(look);
        debug_assert_eq!(style.len(), 4 * GLASS_STYLE_VECS);
        gl.uniform_4fv(g.style.as_ref(), &style); // every M3 style input at once
        gl.uniform_1f(g.opacity.as_ref(), opacity);
        let rect = FRect {
            x: r.x - margin,
            y: r.y - margin,
            width: r.width + 2.0 * margin,
            height: r.height + 2.0 * margin,
        };
        // fixed size: a resize never scales the glass
        let n = build(
            gl,
            self.scratch,
            mesh,
            self.canvas,
            &self.tiles[self.i],
            &self.on,
            &rect,
            Attr::CanvasPx,
            true,
            None,
        );
        gl.buffer_attrib(g.pos as u32, 3, 5, 0);
        gl.enable_attrib(g.pos as u32);
        gl.buffer_attrib(g.px as u32, 2, 5, 3);
        gl.enable_attrib(g.px as u32);
        gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
    }

    /// An image on the panel program over `rect` on the glass surface (`bx`: its atlas cell), at `alpha`; the texture
    /// is bound by the caller.
    fn image(
        &mut self,
        mesh: &mut Mesh,
        rect: &FRect,
        bx: Option<&AtlasBox>,
        alpha: f32,
        before_draw: impl FnOnce(&Gl),
    ) {
        let gl = self.gl;
        let Some(panel) = self.panel.as_deref_mut() else {
            return;
        };
        gl.disable_attrib(self.glass.px as u32);
        program(gl, self.current, &mut panel.p, self.fp);
        before_draw(gl);
        gl.uniform_1f(panel.alpha.as_ref(), alpha);
        let n = build(
            gl,
            self.scratch,
            mesh,
            self.canvas,
            &self.tiles[self.i],
            &self.on,
            rect,
            Attr::RectUv,
            true,
            bx,
        );
        gl.buffer_attrib(panel.pos as u32, 3, 5, 0);
        gl.enable_attrib(panel.pos as u32);
        gl.buffer_attrib(panel.uv as u32, 2, 5, 3);
        gl.enable_attrib(panel.uv as u32);
        gl.draw_arrays(glow::TRIANGLE_STRIP, 0, n);
        gl.disable_attrib(panel.uv as u32);
    }

    /// A placement pill and, when it opens, its morph into the placement panel with the content fading in over it.
    fn pill(
        &mut self,
        g: &GlassFrame,
        accent: &[f32; 3],
        m: &mut Meshes,
        image: Option<&HudImage>,
        tex: &mut PanelTexture,
    ) {
        let i = self.i;
        let e = g.expand[i].clamp(0.0, 1.0);
        if !g.pills && e <= 0.0 {
            return;
        }
        let level = g.pill_levels.get(i).copied().unwrap_or(GLASS_IDLE);
        let r = glass_morph(&self.tiles[i], e);
        self.shape(
            &mut m.glass[i],
            &r,
            glass_radius(&self.tiles[i], e),
            &glass_look(*accent, level, e),
            1.0,
            GLASS_SHADOW_MARGIN,
        );
        if e <= 0.0 {
            return;
        }
        // The grabber where the pill was, fading in with the panel.
        let grab = FRect {
            x: r.x + (r.width - GRABBER_W) / 2.0,
            y: r.y + GRABBER_TOP,
            width: GRABBER_W,
            height: GRABBER_H,
        };
        let fade = ((e - 0.5) * 2.0).clamp(0.0, 1.0);
        self.shape(
            &mut m.grab[i],
            &grab,
            GRABBER_H / 2.0,
            &grabber_look(level),
            fade,
            2.0,
        );
        let Some(im) = image else { return };
        if i as i32 != g.content || g.content_alpha <= 0.0 || self.panel.is_none() {
            return;
        }
        // The content rides on the morphing shape (it only shows over the last quarter, when it is near full size).
        let generation = g.generation;
        self.image(&mut m.panel[i], &r, None, g.content_alpha.min(1.0), |gl| {
            upload_panel(gl, tex, im, generation)
        });
    }

    /// The options gear: the same glass capsule as a placement pill, morphing the same way from the gear into the
    /// options panel. The gear glyph fades out as the capsule starts to grow.
    fn gear(
        &mut self,
        g: &GlassFrame,
        accent: &[f32; 3],
        m: &mut Meshes,
        image: Option<&HudImage>,
        tex: &mut PanelTexture,
        handle_texture: Option<glow::Texture>,
    ) {
        let i = self.i;
        let open = g.options && i as i32 == g.options_desktop;
        let e = if open {
            g.options_expand.clamp(0.0, 1.0)
        } else {
            0.0
        };
        if e <= 0.0 && !(g.pills && i < g.bars.len() && g.bars[i]) {
            return;
        }
        let (a, b) = (options_gear(&self.tiles[i]), options_panel(&self.tiles[i]));
        let mix = |x: f32, y: f32| x + (y - x) * e;
        let r = FRect {
            x: mix(a.x, b.x),
            y: mix(a.y, b.y),
            width: mix(a.width, b.width),
            height: mix(a.height, b.height),
        };
        let radius = mix(a.height / 2.0, PANEL_RADIUS).min(r.width.min(r.height) / 2.0);
        let level = g.gear_levels.get(i).copied().unwrap_or(GLASS_IDLE);
        self.shape(
            &mut m.gear[i],
            &r,
            radius,
            &glass_look(*accent, level, e),
            1.0,
            GLASS_SHADOW_MARGIN,
        );
        if e > 0.0 {
            let grab = FRect {
                x: r.x + (r.width - GRABBER_W) / 2.0,
                y: r.y + GRABBER_TOP,
                width: GRABBER_W,
                height: GRABBER_H,
            };
            let fade = ((e - 0.5) * 2.0).clamp(0.0, 1.0);
            self.shape(
                &mut m.gear_grab[i],
                &grab,
                GRABBER_H / 2.0,
                &grabber_look(level),
                fade,
                2.0,
            );
        }
        if self.panel.is_none() {
            return;
        }
        let glyph = 1.0 - (e * 4.0).clamp(0.0, 1.0);
        if glyph > 0.0 && handle_texture.is_some() {
            let bx = options_gear_box();
            self.gl.active_texture(glow::TEXTURE0);
            self.gl.bind_texture(handle_texture);
            self.image(&mut m.gear_glyph[i], &a, Some(&bx), glyph, |_| {});
        }
        let Some(im) = image else { return };
        if !open {
            return;
        }
        let t = ((e - CONTENT_FROM) / (1.0 - CONTENT_FROM)).clamp(0.0, 1.0); // smoothstep(CONTENT_FROM, 1, e)
        if t <= 0.0 {
            return;
        }
        // already uploaded before the first draw: binds it
        upload_panel(self.gl, tex, im, g.options_generation);
        self.image(&mut m.options[i], &r, None, t * t * (3.0 - 2.0 * t), |_| {});
    }
}

impl Drop for GlCompositor {
    fn drop(&mut self) {
        let raw = self.gl.raw();
        // Cards first (they delete their own objects), then everything made in setup. The context is still current.
        self.hud = None;
        self.help_hud = None;
        self.binds_hud = None;
        self.video_hud = None;
        self.meshes.each(|m| drop_mesh(raw, m));
        if let Some(pass) = self.stereo.pass.take() {
            pass.destroy(raw);
        }
        if let Some(pass) = self.environment.take() {
            pass.destroy(raw);
        }
        // SAFETY: every object below was made on this context, which is current until `headless` drops after this.
        unsafe {
            if let Some((fb, rb)) = self.framebuffer.take() {
                raw.delete_framebuffer(fb);
                raw.delete_renderbuffer(rb);
            }
            for t in self.textures.drain(..) {
                raw.delete_texture(t.id);
            }
            for t in [
                self.panel_tex.id.take(),
                self.options_tex.id.take(),
                self.handle_texture.take(),
            ]
            .into_iter()
            .flatten()
            {
                raw.delete_texture(t);
            }
            let p = &self.progs;
            for id in [p.tile.id, p.color.id, p.glow.id] {
                raw.delete_program(id);
            }
            if let Some(g) = &p.glass {
                raw.delete_program(g.p.id);
            }
            if let Some(g) = &p.panel {
                raw.delete_program(g.p.id);
            }
        }
        if let Some(t) = self.timer.take() {
            // SAFETY: the query made in GpuTimer::load on this context, still current.
            unsafe { (t.delete)(1, &t.query) };
        }
        let _ = &self.headless;
    }
}
