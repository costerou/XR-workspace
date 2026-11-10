//! Global immersive 3D, the GL side (spike/src/stereo_gl.cpp; [`crate::stereo`] has the maths, docs/stereo-render.md
//! the design). Per depth frame, per desktop: a text mask and the guide map (depth -> disparity, text kept near the
//! screen plane, edges tapered, filtered over time), both small. Per displayed frame: one fragment shader on the
//! desktop's own mesh that finds, for each eye pixel, the source pixel that lands there (depth-image-based rendering by
//! a march along the eye's horizontal). That keeps the cost to the pixels on screen, whatever the desktops' sizes, and
//! resamples the desktop once, so text stays as sharp as in 2D. Two further passes per depth update reconstruct a
//! source-size surface and remove isolated depth speckles; displayed frames and the pointer sample that owned surface,
//! so colour noise cannot change the geometry between depth updates.
//!
//! Every call needs the compositor's context current. Needs GLES 3 and a float colour buffer; [`StereoPass::create`]
//! fails without, and the desktops then stay flat. The GLSL is the C++'s, byte for byte. The pass owns GL objects but
//! not the context, so it has no Drop: the compositor calls [`StereoPass::destroy`] while its context is current.

use glow::HasContext;

use crate::depth::{DepthData, DepthInput, depth_input_valid};
use crate::gl::{Gl, compile_program_with, xr_project};
use crate::stereo::{
    STEREO_TEXT_COLS, STEREO_TEXT_ROWS, StereoBudget, StereoParams, stereo_alpha, stereo_clamped,
    stereo_resolve_alpha, stereo_side,
};

// Shaders (spike/src/stereo_gl.cpp), byte for byte.
const QUAD_VERTEX: &str =
    "#version 300 es\nin vec2 pos; out vec2 uv; void main() { uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }\n";
const TEXT_FRAGMENT: &str = concat!(
    "#version 300 es\nprecision highp float;\n",
    "in vec2 uv; out vec4 color; uniform sampler2D colour; uniform vec2 step_px;\n",
    "float luma(vec2 t) { vec3 c = texture(colour, t).rgb; return 0.25 * c.r + 0.5 * c.g + 0.25 * c.b; }\n",
    "void main() {\n",
    "  float jumps = 0.0;\n",
    "  for (int r = 0; r < 3; ++r) {\n",
    "    float y = uv.y + (float(r) - 1.0) * step_px.y; float before = 0.0, last = 0.0;\n",
    "    for (int c = 0; c < 8; ++c) {\n",
    "      float l = luma(vec2(uv.x + (float(c) - 3.5) * step_px.x, y));\n",
    "      float d = l - before, way = d > 0.0 ? 1.0 : -1.0;\n",
    "      if (c > 0 && abs(d) > 0.1 && way != last) { jumps += 1.0; last = way; }\n",
    "      before = l;\n",
    "    }\n",
    "  }\n",
    "  color = vec4(smoothstep(0.75, 1.5, jumps / 3.0));\n",
    "}\n"
);
const GUIDE_FRAGMENT: &str = concat!(
    "#version 300 es\nprecision highp float;\n",
    "in vec2 uv; out vec4 color; uniform highp sampler2D raw; uniform sampler2D text; uniform highp sampler2D previous;\n",
    "uniform vec2 range; uniform vec3 params; uniform float alpha; uniform float aspect; uniform float history;\n",
    "const float KNEE = 0.7, ROOM = 0.3, TAPER = 0.05;\n",
    "void main() {\n",
    "  ivec2 p = ivec2(gl_FragCoord.xy), top = textureSize(raw, 0) - 1;\n",
    "  float r = texelFetch(raw, p, 0).r, v = 0.0;\n",
    "  if (!isnan(r) && !isinf(r)) {\n",
    "    float span = range.y - range.x;\n",
    "    float n = clamp(abs(span) > 1e-6 ? (r - range.x) / span : 0.5, 0.0, 1.0);\n",
    "    float x = 2.0 * params.x * (n - params.y), a = abs(x);\n",
    "    v = a <= KNEE ? x : sign(x) * (KNEE + ROOM * tanh((a - KNEE) / ROOM));\n",
    "  }\n",
    "  float t = 0.0;\n",
    "  for (int j = -3; j <= 3; ++j) for (int i = -2; i <= 2; ++i) {\n",
    "    float w = (abs(i) < 2 ? 1.0 : 0.5) * (abs(j) < 3 ? 1.0 : 0.5);\n",
    "    t = max(t, texelFetch(text, clamp(p + ivec2(i,j), ivec2(0), top), 0).r * w);\n",
    "  }\n",
    "  float protection = 1.0 + (params.z - 1.0) * t;\n",
    "  v *= protection;\n",
    "  float e = min(uv.x, 1.0 - uv.x) / TAPER;\n",
    "  float edge = smoothstep(0.0, 1.0, e) * smoothstep(0.0, 1.0, min(uv.y, 1.0 - uv.y) / (TAPER * aspect));\n",
    "  v *= edge; protection *= edge;\n",
    "  if (history > 0.5) {\n",
    "    float q = texelFetch(previous, p, 0).r, d = abs(v - q);\n",
    "    v = q + (v - q) * (alpha + (1.0 - alpha) * smoothstep(0.1, 0.35, d));\n",
    "  }\n",
    "  color = vec4(v, protection, 0.0, 1.0);\n",
    "}\n"
);
const WARP_VERTEX: &str = concat!(
    "#version 300 es\nin vec3 pos; in vec2 uv; out vec2 v;\n",
    xr_project!(),
    "void main() { v = uv; gl_Position = project(pos); }\n"
);
macro_rules! sample_guide {
    () => {
        concat!(
            "float sample_guide(vec2 t) {\n",
            "  vec2 center = textureLod(guide, t, 0.0).rg; float original = center.r;\n",
            "  if (center.g < 0.999 || abs(original) < 1e-6) return original;\n",
            "  vec2 size = vec2(textureSize(guide, 0)), pixel = 1.0 / size;\n",
            "  float aspect = size.x / size.y;\n",
            "  float border = smoothstep(0.05, 0.075, min(t.x, 1.0 - t.x)) *\n",
            "                 smoothstep(0.05, 0.075, min(t.y, 1.0 - t.y) / aspect);\n",
            "  if (border <= 0.0) return original;\n",
            "  vec2 gradient = vec2(textureLod(guide, t + vec2(pixel.x, 0.0), 0.0).r - textureLod(guide, t - vec2(pixel.x, 0.0), 0.0).r,\n",
            "                       textureLod(guide, t + vec2(0.0, pixel.y), 0.0).r - textureLod(guide, t - vec2(0.0, pixel.y), 0.0).r);\n",
            "  float magnitude = length(gradient);\n",
            "  if (magnitude < 1e-6) return original;\n",
            "  vec2 offset = 2.5 * gradient / (magnitude * size);\n",
            "  vec2 surface_a = textureLod(guide, t - offset, 0.0).rg, surface_b = textureLod(guide, t + offset, 0.0).rg;\n",
            "  if (surface_a.g < 0.999 || surface_b.g < 0.999) return original;\n",
            "  float ga = surface_a.r, gb = surface_b.r;\n",
            "  float depth_weight = smoothstep(0.08, 0.3, abs(ga - gb));\n",
            "  if (depth_weight <= 0.0) return original;\n",
            "  vec3 c = textureLod(tex, t, 0.0).rgb, a = textureLod(tex, t - offset, 0.0).rgb, b = textureLod(tex, t + offset, 0.0).rgb;\n",
            "  vec3 da = c - a, db = c - b;\n",
            "  float wa = exp(-dot(da, da) / 0.01), wb = exp(-dot(db, db) / 0.01);\n",
            "  float resolved = (original * 0.025 + ga * wa + gb * wb) / (0.025 + wa + wb);\n",
            "  return mix(original, resolved, border * depth_weight);\n",
            "}\n"
        )
    };
}
const RECONSTRUCT_FRAGMENT: &str = concat!(
    "#version 300 es\nprecision highp float;\n",
    "in vec2 uv; out vec4 color; uniform sampler2D tex; uniform highp sampler2D guide;\n",
    sample_guide!(),
    "void main() { color = vec4(sample_guide(uv), 0.0, 0.0, 1.0); }\n"
);
const RESOLVE_FRAGMENT: &str = concat!(
    "#version 300 es\nprecision highp float;\n",
    "in vec2 uv; out vec4 color; uniform highp sampler2D guide; uniform float aspect;\n",
    "uniform highp sampler2D previous, base, previous_base; uniform float alpha, history;\n",
    "void main() {\n",
    "  ivec2 p = ivec2(gl_FragCoord.xy), top = textureSize(guide, 0) - 1;\n",
    "  vec2 current_surface = textureLod(base, uv, 0.0).rg;\n",
    "  if (current_surface.g < 0.999) { color = vec4(current_surface.r, 0.0, 0.0, 1.0); return; }\n",
    "  float center = texelFetch(guide, p, 0).r;\n",
    "  if (abs(center) < 1e-6 || min(uv.x, 1.0 - uv.x) <= 0.05 || min(uv.y, 1.0 - uv.y) / aspect <= 0.05) {\n",
    "    color = vec4(center, 0.0, 0.0, 1.0); return;\n",
    "  }\n",
    "  float values[9];\n",
    "  for (int j = -1; j <= 1; ++j) for (int i = -1; i <= 1; ++i)\n",
    "    values[(j + 1) * 3 + i + 1] = texelFetch(guide, clamp(p + ivec2(i, j), ivec2(0), top), 0).r;\n",
    "  for (int i = 0; i < 5; ++i) for (int j = i + 1; j < 9; ++j) {\n",
    "    float low = min(values[i], values[j]); values[j] = max(values[i], values[j]); values[i] = low;\n",
    "  }\n",
    "  float resolved = values[4];\n",
    "  if (history > 0.5) {\n",
    "    float current_base = current_surface.r;\n",
    "    float correction = resolved - current_base;\n",
    "    float prior = textureLod(previous, uv, 0.0).r - textureLod(previous_base, uv, 0.0).r;\n",
    "    float keep = alpha + (1.0 - alpha) * smoothstep(0.1, 0.35, abs(correction - prior));\n",
    "    resolved = clamp(current_base + mix(prior, correction, keep), -1.0, 1.0);\n",
    "  }\n",
    "  color = vec4(resolved, 0.0, 0.0, 1.0);\n",
    "}\n"
);
const WARP_FRAGMENT: &str = concat!(
    "#version 300 es\nprecision highp float;\n",
    "in vec2 v; out vec4 color; uniform sampler2D tex; uniform highp sampler2D guide; uniform float swizzle;\n",
    "uniform vec3 budget; uniform float model_width;\n",
    "vec2 dir;\n",
    "float g_at(float o) { float d = textureLod(guide, v - budget.x * o * dir, 0.0).r; return d > 0.0 ? d * budget.y : d * budget.z; }\n",
    "void main() {\n",
    "  dir = dFdx(v); vec2 up = dFdy(v);\n",
    "  float side = budget.x, gn = budget.y, gf = budget.z, span = gn + gf;\n",
    "  float a = gn, ga = g_at(a), b = a, gb = ga, step_px = span / 12.0; bool hit = false;\n",
    "  for (int i = 1; i <= 12; ++i) {\n",
    "    b = gn - step_px * float(i); gb = g_at(b);\n",
    "    if (b - gb <= 0.0) { hit = true; break; }\n",
    "    a = b; ga = gb;\n",
    "  }\n",
    "  if (!hit) { b = -gf; gb = g_at(b); }\n",
    "  for (int i = 0; i < 3; ++i) {\n",
    "    float m = 0.5 * (a + b), gm = g_at(m);\n",
    "    if (m - gm > 0.0) { a = m; ga = gm; } else { b = m; gb = gm; }\n",
    "  }\n",
    "  float ha = a - ga, hb = b - gb, gap = a - b;\n",
    "  float o = gap > 1e-6 && ha - hb > 1e-6 ? a - gap * ha / (ha - hb) : b;\n",
    "  vec3 c = textureLod(tex, v - side * o * dir, 0.0).rgb;\n",
    "  float steep = gap > 1e-6 ? smoothstep(0.8, 1.6, (gb - ga) / gap) : 0.0;\n",
    "  if (steep > 0.0) {\n",
    "    float margin = 1.5 / (model_width * max(length(dir), 1e-7));\n",
    "    float q = o + margin, far = g_at(q);\n",
    "    float candidate = o - margin, g = g_at(candidate);\n",
    "    if (g < far) { q = candidate; far = g; }\n",
    "    if (ga < far) { q = a; far = ga; }\n",
    "    if (gb < far) { q = b; far = gb; }\n",
    "    vec2 f = v - side * q * dir; vec3 fill = vec3(0.0); float weight = 0.0;\n",
    "    for (int j = -1; j <= 1; ++j) {\n",
    "      vec2 t = f + float(j) * 1.5 * up; float d = textureLod(guide, t, 0.0).r;\n",
    "      float g = d > 0.0 ? d * gn : d * gf;\n",
    "      float w = (j == 0 ? 2.0 : 1.0) * (1.0 - smoothstep(0.02 * span, 0.06 * span, g - far));\n",
    "      fill += textureLod(tex, t, 0.0).rgb * w; weight += w;\n",
    "    }\n",
    "    float band = steep * smoothstep(0.0, 1.0, abs(g_at(o - margin) - g_at(o + margin)));\n",
    "    c = mix(c, fill / max(weight, 1e-6), band);\n",
    "  }\n",
    "  color = vec4(mix(c, c.bgr, swizzle), 1.0);\n",
    "}\n"
);
const POINTER_VERTEX: &str = concat!(
    "#version 300 es\nin vec3 pos; uniform highp sampler2D guide; uniform vec2 surface; uniform vec3 lift;\n",
    xr_project!(),
    "void main() { vec4 c = project(pos); float d = max(abs(lift.y), abs(lift.z)) > 0.0 ? textureLod(guide, surface, 0.0).r : 0.0;\n",
    "  c.x += (lift.x + (d > 0.0 ? d * lift.y : d * lift.z)) * c.w; gl_Position = c; }\n"
);
const POINTER_FRAGMENT: &str =
    "#version 300 es\nprecision mediump float; uniform vec4 color; out vec4 o; void main() { o = color; }\n";

type Loc = Option<glow::UniformLocation>;
type Tex = Option<glow::Texture>;
type Fb = Option<glow::Framebuffer>;

/// One desktop's depth state: the uploads, the mask, the two guides (current and history) and the source-size surface.
struct Source {
    /// The last frame, when its raw values are an upload this pass owns (an external texture may be released).
    cached: Option<DepthInput<'static>>,
    raw: Tex,
    raw_half: Tex,
    text: Tex,
    guide: [Tex; 2],
    fb: [Fb; 2],
    text_fb: Fb,
    reconstructed: Tex,
    resolved: [Tex; 2],
    reconstructed_fb: Fb,
    resolved_fb: [Fb; 2],
    w: i32,
    h: i32,
    colour_w: i32,
    colour_h: i32,
    current: i32,
    surface_current: i32,
    seq: u64,
}

impl Default for Source {
    fn default() -> Self {
        Self {
            cached: None,
            raw: None,
            raw_half: None,
            text: None,
            guide: [None; 2],
            fb: [None; 2],
            text_fb: None,
            reconstructed: None,
            resolved: [None; 2],
            reconstructed_fb: None,
            resolved_fb: [None; 2],
            w: 0,
            h: 0,
            colour_w: 0,
            colour_h: 0,
            current: -1,
            surface_current: -1,
            seq: 0,
        }
    }
}

struct TextProgram {
    id: glow::Program,
    pos: u32,
    step: Loc,
}
struct GuideProgram {
    id: glow::Program,
    pos: u32,
    range: Loc,
    params: Loc,
    alpha: Loc,
    aspect: Loc,
    history: Loc,
}
struct ReconstructProgram {
    id: glow::Program,
    pos: u32,
}
struct ResolveProgram {
    id: glow::Program,
    pos: u32,
    aspect: Loc,
    alpha: Loc,
    history: Loc,
}
struct WarpProgram {
    id: glow::Program,
    swizzle: Loc,
    budget: Loc,
    model_width: Loc,
}
struct PointerProgram {
    id: glow::Program,
    view: Loc,
    half_fov: Loc,
    color: Loc,
    surface: Loc,
    lift: Loc,
}

pub struct StereoPass {
    sources: Vec<Source>,
    text: TextProgram,
    guide: GuideProgram,
    reconstruct: ReconstructProgram,
    resolve: ResolveProgram,
    warp: WarpProgram,
    pointer: PointerProgram,
    quad: glow::Buffer,
    updates: u64,
}

// SAFETY (every unsafe block below): plain GL calls on the context current on this thread (the module's contract);
// every object handle comes from that context, and every slice is read before its call returns.

unsafe fn texture(gl: &glow::Context, internal: u32, format: u32, ty: u32, w: i32, h: i32, filter: u32) -> Tex {
    unsafe {
        let t = gl.create_texture().ok()?;
        gl.bind_texture(glow::TEXTURE_2D, Some(t));
        gl.tex_image_2d(
            glow::TEXTURE_2D,
            0,
            internal as i32,
            w,
            h,
            0,
            format,
            ty,
            glow::PixelUnpackData::Slice(None),
        );
        for (p, v) in [
            (glow::TEXTURE_MIN_FILTER, filter),
            (glow::TEXTURE_MAG_FILTER, filter),
            (glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
            (glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
        ] {
            gl.tex_parameter_i32(glow::TEXTURE_2D, p, v as i32);
        }
        Some(t)
    }
}

/// A framebuffer drawing into `tex`, left bound. None (nothing bound) when either is missing.
unsafe fn framebuffer(gl: &glow::Context, tex: Tex) -> Fb {
    unsafe {
        tex?;
        let f = gl.create_framebuffer().ok()?;
        gl.bind_framebuffer(glow::FRAMEBUFFER, Some(f));
        gl.framebuffer_texture_2d(glow::FRAMEBUFFER, glow::COLOR_ATTACHMENT0, glow::TEXTURE_2D, tex, 0);
        Some(f)
    }
}

/// The framebuffer just made by [`framebuffer`] exists and is complete.
unsafe fn complete(gl: &glow::Context, fb: Fb) -> bool {
    fb.is_some() && unsafe { gl.check_framebuffer_status(glow::FRAMEBUFFER) } == glow::FRAMEBUFFER_COMPLETE
}

unsafe fn delete(gl: &glow::Context, textures: &[Tex], framebuffers: &[Fb]) {
    unsafe {
        for t in textures.iter().flatten() {
            gl.delete_texture(*t);
        }
        for f in framebuffers.iter().flatten() {
            gl.delete_framebuffer(*f);
        }
    }
}

fn bytes<T>(v: &[T]) -> &[u8] {
    // SAFETY: f32 and u16 have no padding; the bytes are the values.
    unsafe { std::slice::from_raw_parts(v.as_ptr().cast::<u8>(), std::mem::size_of_val(v)) }
}

impl StereoPass {
    /// `tile_pos`, `tile_uv`, `color_pos`: the attribute locations of the compositor's tile and colour programs, which
    /// the warp and pointer programs take over, so the two kinds of program swap without touching its vertex arrays.
    /// Err: why 3D is unavailable (the C++ `why`).
    pub fn create(gl: &glow::Context, tile_pos: i32, tile_uv: i32, color_pos: i32) -> Result<Self, String> {
        // SAFETY: the module's contract.
        let version = unsafe { gl.get_parameter_string(glow::VERSION) };
        if !version.starts_with("OpenGL ES 3") {
            return Err("stereo needs GLES 3".into());
        }
        let programs = [
            compile_program_with(gl, QUAD_VERTEX, TEXT_FRAGMENT, &[]),
            compile_program_with(gl, QUAD_VERTEX, GUIDE_FRAGMENT, &[]),
            compile_program_with(gl, QUAD_VERTEX, RECONSTRUCT_FRAGMENT, &[]),
            compile_program_with(gl, QUAD_VERTEX, RESOLVE_FRAGMENT, &[]),
            compile_program_with(gl, WARP_VERTEX, WARP_FRAGMENT, &[(tile_pos, "pos"), (tile_uv, "uv")]),
            compile_program_with(gl, POINTER_VERTEX, POINTER_FRAGMENT, &[(color_pos, "pos")]),
        ];
        if programs.iter().any(Result::is_err) {
            for p in programs.into_iter().flatten() {
                // SAFETY: the module's contract.
                unsafe { gl.delete_program(p) };
            }
            return Err("stereo shader compile".into());
        }
        let [text, guide, reconstruct, resolve, warp, pointer] = programs.map(Result::unwrap);
        // SAFETY: the module's contract.
        unsafe {
            let u = |p, name| gl.get_uniform_location(p, name);
            let a = |p, name| gl.get_attrib_location(p, name).unwrap_or(0);
            let units = |p, names: &[&str]| {
                gl.use_program(Some(p));
                for (unit, name) in names.iter().enumerate() {
                    gl.uniform_1_i32(gl.get_uniform_location(p, name).as_ref(), unit as i32);
                }
            };
            units(warp, &["tex", "guide"]);
            gl.use_program(Some(pointer));
            gl.uniform_1_i32(u(pointer, "guide").as_ref(), 1);
            units(reconstruct, &["tex", "guide"]);
            units(resolve, &["guide", "previous", "base", "previous_base"]);
            units(text, &["colour"]);
            units(guide, &["raw", "text", "previous"]);
            gl.use_program(None);
            let Ok(quad) = gl.create_buffer() else {
                for p in [text, guide, reconstruct, resolve, warp, pointer] {
                    gl.delete_program(p);
                }
                return Err("stereo shader compile".into());
            };
            let corners: [f32; 8] = [-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0];
            gl.bind_buffer(glow::ARRAY_BUFFER, Some(quad));
            gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes(&corners), glow::STATIC_DRAW);
            gl.bind_buffer(glow::ARRAY_BUFFER, None);
            let out = Self {
                sources: Vec::new(),
                text: TextProgram { id: text, pos: a(text, "pos"), step: u(text, "step_px") },
                guide: GuideProgram {
                    id: guide,
                    pos: a(guide, "pos"),
                    range: u(guide, "range"),
                    params: u(guide, "params"),
                    alpha: u(guide, "alpha"),
                    aspect: u(guide, "aspect"),
                    history: u(guide, "history"),
                },
                reconstruct: ReconstructProgram { id: reconstruct, pos: a(reconstruct, "pos") },
                resolve: ResolveProgram {
                    id: resolve,
                    pos: a(resolve, "pos"),
                    aspect: u(resolve, "aspect"),
                    alpha: u(resolve, "alpha"),
                    history: u(resolve, "history"),
                },
                warp: WarpProgram {
                    id: warp,
                    swizzle: u(warp, "swizzle"),
                    budget: u(warp, "budget"),
                    model_width: u(warp, "model_width"),
                },
                pointer: PointerProgram {
                    id: pointer,
                    view: u(pointer, "view"),
                    half_fov: u(pointer, "half_fov"),
                    color: u(pointer, "color"),
                    surface: u(pointer, "surface"),
                    lift: u(pointer, "lift"),
                },
                quad,
                updates: 0,
            };
            // Rendering to R16F is an extension in ES 3.0: find out now, not on the first depth frame. The
            // low-resolution guide also needs RG16F for its current protection gain.
            let before = gl.get_parameter_framebuffer(glow::FRAMEBUFFER_BINDING);
            let mut ok = true;
            for (internal, format) in [(glow::R16F, glow::RED), (glow::RG16F, glow::RG)] {
                let t = texture(gl, internal, format, glow::HALF_FLOAT, 4, 4, glow::LINEAR);
                let f = framebuffer(gl, t);
                ok = ok && complete(gl, f);
                gl.bind_framebuffer(glow::FRAMEBUFFER, before);
                delete(gl, &[t], &[f]);
            }
            gl.bind_texture(glow::TEXTURE_2D, None);
            if !ok {
                out.destroy(gl);
                return Err("stereo needs a float colour buffer".into());
            }
            while gl.get_error() != glow::NO_ERROR {}
            Ok(out)
        }
    }

    /// Delete every object this pass made (the C++ destructor).
    pub fn destroy(mut self, gl: &glow::Context) {
        for s in &mut self.sources {
            Self::drop_source(gl, s);
        }
        // SAFETY: the module's contract.
        unsafe {
            for p in [self.text.id, self.guide.id, self.reconstruct.id, self.resolve.id, self.warp.id, self.pointer.id] {
                gl.delete_program(p);
            }
            gl.delete_buffer(self.quad);
        }
    }

    fn drop_source(gl: &glow::Context, s: &mut Source) {
        Self::drop_surface(gl, s);
        // SAFETY: the module's contract.
        unsafe {
            delete(
                gl,
                &[s.raw, s.raw_half, s.text, s.guide[0], s.guide[1]],
                &[s.fb[0], s.fb[1], s.text_fb],
            )
        };
        *s = Source::default();
    }

    fn drop_surface(gl: &glow::Context, s: &mut Source) {
        // SAFETY: the module's contract.
        unsafe {
            delete(
                gl,
                &[s.reconstructed, s.resolved[0], s.resolved[1]],
                &[s.reconstructed_fb, s.resolved_fb[0], s.resolved_fb[1]],
            )
        };
        s.reconstructed = None;
        s.reconstructed_fb = None;
        s.resolved = [None; 2];
        s.resolved_fb = [None; 2];
        s.colour_w = 0;
        s.colour_h = 0;
        s.surface_current = -1;
    }

    pub fn resize_sources(&mut self, gl: &glow::Context, n: usize) {
        while self.sources.len() > n {
            let mut s = self.sources.pop().expect("longer than n");
            Self::drop_source(gl, &mut s);
        }
        self.sources.resize_with(n, Source::default);
    }
    pub fn insert_source(&mut self, at: usize) {
        self.sources.insert(at.min(self.sources.len()), Source::default());
    }
    pub fn remove_source(&mut self, gl: &glow::Context, at: usize) {
        if at < self.sources.len() {
            let mut s = self.sources.remove(at);
            Self::drop_source(gl, &mut s);
        }
    }

    fn storage(gl: &glow::Context, s: &mut Source, w: i32, h: i32, colour_w: i32, colour_h: i32) -> bool {
        if s.w == w && s.h == h && s.colour_w == colour_w && s.colour_h == colour_h {
            return true;
        }
        // SAFETY: the module's contract.
        unsafe {
            if s.w != w || s.h != h {
                Self::drop_source(gl, s);
                s.raw = texture(gl, glow::R32F, glow::RED, glow::FLOAT, w, h, glow::NEAREST);
                s.text = texture(gl, glow::R8, glow::RED, glow::UNSIGNED_BYTE, w, h, glow::NEAREST);
                s.text_fb = framebuffer(gl, s.text);
                for i in 0..2 {
                    s.guide[i] = texture(gl, glow::RG16F, glow::RG, glow::HALF_FLOAT, w, h, glow::LINEAR);
                    s.fb[i] = framebuffer(gl, s.guide[i]);
                    if !complete(gl, s.fb[i]) {
                        Self::drop_source(gl, s);
                        return false;
                    }
                }
                s.w = w;
                s.h = h;
            }
            // A colour-only resize may be rebuilding from cached_frame(). Its raw upload remains owned and alive; only
            // source-resolution buffers need replacement.
            Self::drop_surface(gl, s);
            s.reconstructed = texture(gl, glow::R16F, glow::RED, glow::HALF_FLOAT, colour_w, colour_h, glow::NEAREST);
            s.reconstructed_fb = framebuffer(gl, s.reconstructed);
            if !complete(gl, s.reconstructed_fb) {
                Self::drop_surface(gl, s);
                return false;
            }
            for i in 0..2 {
                s.resolved[i] = texture(gl, glow::R16F, glow::RED, glow::HALF_FLOAT, colour_w, colour_h, glow::LINEAR);
                s.resolved_fb[i] = framebuffer(gl, s.resolved[i]);
                if !complete(gl, s.resolved_fb[i]) {
                    Self::drop_surface(gl, s);
                    return false;
                }
            }
        }
        s.colour_w = colour_w;
        s.colour_h = colour_h;
        true
    }

    /// A new depth frame of desktop `source`, whose current picture is `colour` (colour_w x colour_h pixels). `dt_ms`
    /// since the last one (0 or less: a first frame, no history). Keeps framebuffer, viewport, blend and scissor.
    /// False: an invalid frame, nothing changed.
    #[allow(clippy::too_many_arguments)]
    pub fn update(
        &mut self,
        gl: &glow::Context,
        source: usize,
        f: &DepthInput,
        colour: glow::Texture,
        colour_w: u32,
        colour_h: u32,
        params: &StereoParams,
        aspect: f32,
        dt_ms: f32,
    ) -> bool {
        if source >= self.sources.len() || !depth_input_valid(f) || colour_w == 0 || colour_h == 0 {
            return false;
        }
        // SAFETY: the module's contract; depth_input_valid checked that the slices hold every row they claim.
        unsafe {
            let max_size = gl.get_parameter_i32(glow::MAX_TEXTURE_SIZE);
            if max_size <= 0 || colour_w > max_size as u32 || colour_h > max_size as u32 {
                return false;
            }
            let frame = gl.get_parameter_framebuffer(glow::FRAMEBUFFER_BINDING);
            let mut viewport = [0i32; 4];
            gl.get_parameter_i32_slice(glow::VIEWPORT, &mut viewport);
            let (cw, ch) = (colour_w as i32, colour_h as i32);
            let s = &mut self.sources[source];
            let had = s.w == f.w && s.h == f.h && s.colour_w == cw && s.colour_h == ch && s.current >= 0;
            if !Self::storage(gl, s, f.w, f.h, cw, ch) {
                gl.bind_framebuffer(glow::FRAMEBUFFER, frame);
                return false;
            }
            let blended = gl.is_enabled(glow::BLEND);
            let scissored = gl.is_enabled(glow::SCISSOR_TEST);
            gl.disable(glow::BLEND);
            gl.disable(glow::SCISSOR_TEST);
            gl.viewport(0, 0, f.w, f.h);
            gl.bind_buffer(glow::ARRAY_BUFFER, Some(self.quad));
            let quad = |pos: u32| {
                gl.vertex_attrib_pointer_f32(pos, 2, glow::FLOAT, false, 0, 0);
                gl.enable_vertex_attrib_array(pos);
                gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
                gl.disable_vertex_attrib_array(pos);
            };
            // The small passes: the mask, then the guide into the other of the two textures, the current one being its
            // history.
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(glow::TEXTURE_2D, Some(colour));
            gl.bind_framebuffer(glow::FRAMEBUFFER, s.text_fb);
            gl.use_program(Some(self.text.id));
            gl.uniform_2_f32(
                self.text.step.as_ref(),
                (1.0 / colour_w as f32).max(1.0 / (f.w as f32 * STEREO_TEXT_COLS as f32)),
                1.0 / (f.h as f32 * STEREO_TEXT_ROWS as f32),
            );
            quad(self.text.pos);
            let row_length = |size: usize| if f.stride != 0 { (f.stride / size) as i32 } else { 0 };
            let raw = match f.data {
                DepthData::Float(values) => {
                    gl.bind_texture(glow::TEXTURE_2D, s.raw);
                    gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 4);
                    gl.pixel_store_i32(glow::UNPACK_ROW_LENGTH, row_length(4));
                    gl.tex_sub_image_2d(
                        glow::TEXTURE_2D,
                        0,
                        0,
                        0,
                        f.w,
                        f.h,
                        glow::RED,
                        glow::FLOAT,
                        glow::PixelUnpackData::Slice(Some(bytes(values))),
                    );
                    gl.pixel_store_i32(glow::UNPACK_ROW_LENGTH, 0);
                    s.raw
                }
                DepthData::Half(values) => {
                    // R32F takes GL_FLOAT only, so half floats get their own texture, made on first use
                    if s.raw_half.is_none() {
                        s.raw_half = texture(gl, glow::R16F, glow::RED, glow::HALF_FLOAT, f.w, f.h, glow::NEAREST);
                    }
                    gl.bind_texture(glow::TEXTURE_2D, s.raw_half);
                    gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 2);
                    gl.pixel_store_i32(glow::UNPACK_ROW_LENGTH, row_length(2));
                    gl.tex_sub_image_2d(
                        glow::TEXTURE_2D,
                        0,
                        0,
                        0,
                        f.w,
                        f.h,
                        glow::RED,
                        glow::HALF_FLOAT,
                        glow::PixelUnpackData::Slice(Some(bytes(values))),
                    );
                    gl.pixel_store_i32(glow::UNPACK_ROW_LENGTH, 0);
                    gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 4);
                    s.raw_half
                }
                DepthData::Texture(t) => Some(t),
            };
            let next = if s.current == 0 { 1 } else { 0 };
            let p = stereo_clamped(params);
            gl.bind_framebuffer(glow::FRAMEBUFFER, s.fb[next]);
            gl.use_program(Some(self.guide.id));
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(glow::TEXTURE_2D, raw);
            gl.active_texture(glow::TEXTURE1);
            gl.bind_texture(glow::TEXTURE_2D, s.text);
            gl.active_texture(glow::TEXTURE2);
            gl.bind_texture(glow::TEXTURE_2D, if had { s.guide[s.current as usize] } else { None });
            let g = &self.guide;
            gl.uniform_2_f32(g.range.as_ref(), f.lo, f.hi);
            let convergence = if f.convergence >= 0.0 { f.convergence.min(1.0) } else { p.convergence };
            gl.uniform_3_f32(g.params.as_ref(), p.strength, convergence, p.text_keep);
            gl.uniform_1_f32(g.alpha.as_ref(), stereo_alpha(dt_ms));
            gl.uniform_1_f32(g.aspect.as_ref(), aspect);
            gl.uniform_1_f32(g.history.as_ref(), if had && dt_ms > 0.0 { 1.0 } else { 0.0 });
            quad(g.pos);
            // Resolve colour-aligned geometry at source resolution once per depth frame. The median removes small
            // disconnected depth islands; correction history suppresses colour noise without re-filtering the model's
            // base surface.
            gl.viewport(0, 0, s.colour_w, s.colour_h);
            gl.bind_framebuffer(glow::FRAMEBUFFER, s.reconstructed_fb);
            gl.use_program(Some(self.reconstruct.id));
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(glow::TEXTURE_2D, Some(colour));
            gl.active_texture(glow::TEXTURE1);
            gl.bind_texture(glow::TEXTURE_2D, s.guide[next]);
            quad(self.reconstruct.pos);
            gl.bind_framebuffer(glow::FRAMEBUFFER, s.resolved_fb[next]);
            gl.use_program(Some(self.resolve.id));
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(glow::TEXTURE_2D, s.reconstructed);
            let surface_history = had && dt_ms > 0.0 && s.surface_current >= 0;
            gl.active_texture(glow::TEXTURE1);
            gl.bind_texture(
                glow::TEXTURE_2D,
                if surface_history { s.resolved[s.surface_current as usize] } else { None },
            );
            gl.active_texture(glow::TEXTURE2);
            gl.bind_texture(glow::TEXTURE_2D, s.guide[next]);
            gl.active_texture(glow::TEXTURE3);
            gl.bind_texture(
                glow::TEXTURE_2D,
                if surface_history { s.guide[s.current as usize] } else { None },
            );
            let r = &self.resolve;
            gl.uniform_1_f32(r.aspect.as_ref(), s.w as f32 / s.h as f32);
            gl.uniform_1_f32(r.alpha.as_ref(), stereo_resolve_alpha(dt_ms));
            gl.uniform_1_f32(r.history.as_ref(), if surface_history { 1.0 } else { 0.0 });
            quad(r.pos);
            s.current = next as i32;
            s.surface_current = next as i32;
            s.seq = f.source_seq;
            // Only retain owned uploads: an external texture may be released by its producer.
            let owned = match f.data {
                DepthData::Float(_) | DepthData::Half(_) => true,
                DepthData::Texture(t) => Some(t) == s.raw || Some(t) == s.raw_half,
            };
            s.cached = match (owned, raw) {
                (true, Some(t)) => Some(DepthInput { data: DepthData::Texture(t), stride: 0, ..*f }),
                _ => None,
            };
            self.updates += 1;
            gl.bind_texture(glow::TEXTURE_2D, None);
            gl.active_texture(glow::TEXTURE2);
            gl.bind_texture(glow::TEXTURE_2D, None);
            gl.active_texture(glow::TEXTURE1);
            gl.bind_texture(glow::TEXTURE_2D, None);
            gl.active_texture(glow::TEXTURE0);
            gl.bind_buffer(glow::ARRAY_BUFFER, None);
            gl.bind_framebuffer(glow::FRAMEBUFFER, frame);
            gl.viewport(viewport[0], viewport[1], viewport[2], viewport[3]);
            if blended {
                gl.enable(glow::BLEND);
            }
            if scissored {
                gl.enable(glow::SCISSOR_TEST);
            }
        }
        true
    }

    /// The last frame of `source`, re-uploadable through [`StereoPass::update`] while this pass owns its raw values.
    pub fn cached_frame(&self, source: usize) -> Option<DepthInput<'static>> {
        self.sources.get(source)?.cached
    }

    pub fn ready(&self, source: usize) -> bool {
        self.sources
            .get(source)
            .is_some_and(|s| s.current >= 0 && s.surface_current >= 0 && s.resolved[s.surface_current as usize].is_some())
    }
    /// Low-resolution RG guide: final disparity fraction in R, current text/frame protection gain in G.
    pub fn guide(&self, source: usize) -> Tex {
        if self.ready(source) { self.sources[source].guide[self.sources[source].current as usize] } else { None }
    }
    fn surface_guide(&self, source: usize) -> Tex {
        if self.ready(source) {
            self.sources[source].resolved[self.sources[source].surface_current as usize]
        } else {
            None
        }
    }
    pub fn seq(&self, source: usize) -> u64 {
        self.sources.get(source).map_or(0, |s| s.seq)
    }
    pub fn updates(&self) -> u64 {
        self.updates
    }

    /// The tile program in 3D; its projection uniforms are found by name (the compositor's projection cache).
    pub fn warp_program(&self) -> glow::Program {
        self.warp.id
    }
    pub fn warp_swizzle(&self) -> Option<&glow::UniformLocation> {
        self.warp.swizzle.as_ref()
    }
    /// This eye's warp of a desktop: its surface on unit 1 (the colour stays on unit 0, which stays active).
    pub fn warp_uniforms(&self, gl: &Gl, source: usize, eye: u32, b: &StereoBudget) {
        gl.active_texture(glow::TEXTURE1);
        gl.bind_texture(self.surface_guide(source));
        gl.active_texture(glow::TEXTURE0);
        gl.uniform_3f(self.warp.budget.as_ref(), stereo_side(eye), b.near_half, b.far_half);
        let width = if self.ready(source) { self.sources[source].w as f32 } else { 1.0 };
        gl.uniform_1f(self.warp.model_width.as_ref(), width);
    }

    pub fn pointer_program(&self) -> glow::Program {
        self.pointer.id
    }
    pub fn pointer_view(&self) -> Option<&glow::UniformLocation> {
        self.pointer.view.as_ref()
    }
    pub fn pointer_half_fov(&self) -> Option<&glow::UniformLocation> {
        self.pointer.half_fov.as_ref()
    }
    pub fn pointer_color(&self) -> Option<&glow::UniformLocation> {
        self.pointer.color.as_ref()
    }
    /// Pointer: `shift_ndc` this eye's constant shift; on a desktop with a surface (`source` >= 0), plus the disparity
    /// under it at (u, v).
    #[allow(clippy::too_many_arguments)]
    pub fn pointer_uniforms(
        &self,
        gl: &Gl,
        source: i32,
        u: f32,
        v: f32,
        eye: u32,
        b: &StereoBudget,
        eye_w: f32,
        shift_ndc: f32,
    ) {
        let on = source >= 0 && self.ready(source as usize);
        gl.active_texture(glow::TEXTURE1);
        gl.bind_texture(if on { self.surface_guide(source as usize) } else { None });
        gl.active_texture(glow::TEXTURE0);
        let k = if on && eye_w > 0.0 { stereo_side(eye) * 2.0 / eye_w } else { 0.0 };
        gl.uniform_2f(self.pointer.surface.as_ref(), u, v);
        gl.uniform_3f(self.pointer.lift.as_ref(), shift_ndc, k * b.near_half, k * b.far_half);
    }
}
