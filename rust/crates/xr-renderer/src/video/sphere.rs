//! The projection pass: every output pixel re-projected from the decoded frame with the head rotation
//! (spike/src/video_gl.cpp). The fragment shader mirrors `math::video_uv`.

use std::rc::Rc;

use glow::HasContext;

use super::math::{Filter, Format, Mat3, eye_rect, transpose};

const MAX_TEXTURE_MAX_ANISOTROPY_EXT: u32 = 0x84FF;
const TEXTURE_MAX_ANISOTROPY_EXT: u32 = 0x84FE;

/// Trilinear mipmaps, clamped edges and the most anisotropy up to 16 on the bound 2D texture; the anisotropy used.
pub fn texture_filtering(gl: &glow::Context) -> f32 {
    // SAFETY: plain GL state calls on the current context, a texture bound to TEXTURE_2D.
    unsafe {
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_MIN_FILTER,
            glow::LINEAR_MIPMAP_LINEAR as i32,
        );
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_MAG_FILTER,
            glow::LINEAR as i32,
        );
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_WRAP_S,
            glow::CLAMP_TO_EDGE as i32,
        );
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_WRAP_T,
            glow::CLAMP_TO_EDGE as i32,
        );
        if !gl
            .supported_extensions()
            .contains("GL_EXT_texture_filter_anisotropic")
        {
            return 1.0;
        }
        let used = gl
            .get_parameter_f32(MAX_TEXTURE_MAX_ANISOTROPY_EXT)
            .clamp(1.0, 16.0);
        gl.tex_parameter_f32(glow::TEXTURE_2D, TEXTURE_MAX_ANISOTROPY_EXT, used);
        used
    }
}

const VERTEX: &str = "#version 300 es\nin vec2 pos; out vec2 ndc; void main() { ndc = pos; gl_Position = vec4(pos, 0.0, 1.0); }\n";
// `view` is uploaded column-major from the row-major world -> eye matrix, so `e * view` is viewᵀ·e (eye -> world).
// highp: 8K frames need sub-texel angular precision.
// Sampling: magnified areas (most of the view) use a Keys cubic (`cubic` = a; 0 = plain bilinear) in 5 bilinear taps of
// mip 0, clamped half a texel inside the eye's rectangle so nothing of the other eye or the frame edge bleeds in.
// Minified areas use trilinear + anisotropic mipmaps with explicit gradients: at the 360° seam u jumps 1 -> 0 between
// neighbouring pixels, and implicit derivatives would pick the smallest mip there (a visible line), so each gradient
// is taken from whichever of u and the half-turned u is continuous at that pixel.
const FRAGMENT: &str = concat!(
    "#version 300 es\nprecision highp float;\n",
    "in vec2 ndc; out vec4 color; uniform highp sampler2D tex; uniform mat3 view; uniform vec2 half_fov; uniform float projection;\n",
    "uniform vec4 rect; uniform float fov; uniform float flip; uniform float cubic; uniform float behind;\n",
    "const float PI = 3.14159265358979;\n",
    "vec3 keys(vec2 t, vec2 size, vec2 lo, vec2 hi) {\n",
    "  vec2 p = t * size - 0.5; vec2 i = floor(p); vec2 f = p - i; float a = cubic;\n",
    "  vec2 w0 = a * f * (1.0 - f) * (1.0 - f), w3 = a * f * f * (1.0 - f);\n",
    "  vec2 w1 = (a + 2.0) * f * f * f - (a + 3.0) * f * f + 1.0, w2 = 1.0 - w0 - w1 - w3;\n",
    "  vec2 w12 = w1 + w2;\n",
    "  vec2 t0 = clamp((i - 0.5) / size, lo, hi), t3 = clamp((i + 2.5) / size, lo, hi);\n",
    "  vec2 t12 = clamp((i + 0.5 + w2 / w12) / size, lo, hi);\n",
    "  vec3 c = textureLod(tex, vec2(t12.x, t0.y), 0.0).rgb * (w12.x * w0.y) + textureLod(tex, vec2(t0.x, t12.y), 0.0).rgb * (w0.x * w12.y)\n",
    "         + textureLod(tex, t12, 0.0).rgb * (w12.x * w12.y)\n",
    "         + textureLod(tex, vec2(t3.x, t12.y), 0.0).rgb * (w3.x * w12.y) + textureLod(tex, vec2(t12.x, t3.y), 0.0).rgb * (w12.x * w3.y);\n",
    "  float w = w12.x * w0.y + w0.x * w12.y + w12.x * w12.y + w3.x * w12.y + w12.x * w3.y;\n",
    "  return clamp(c / w, 0.0, 1.0);\n",
    "}\n",
    "void main() {\n",
    "  vec3 d = normalize(vec3(ndc.x * half_fov.x, ndc.y * half_fov.y, -1.0) * view);\n",
    "  vec2 uv; bool inside = true;\n",
    "  if (projection > 1.5) {\n",
    "    float r = acos(clamp(-d.z, -1.0, 1.0)) / (0.5 * fov);\n",
    "    float l = length(d.xy); vec2 c = l > 1e-7 ? d.xy / l : vec2(0.0);\n",
    "    uv = vec2(0.5 + 0.5 * r * c.x, 0.5 - 0.5 * r * c.y); inside = r <= 1.0;\n",
    "  } else {\n",
    "    float lon = atan(d.x, -d.z); float lat = asin(clamp(d.y, -1.0, 1.0));\n",
    "    if (projection > 0.5) { uv = vec2(0.5 + lon / PI, 0.5 - lat / PI); inside = abs(lon) <= 0.5 * PI + 1e-6; }\n",
    "    else uv = vec2(fract(0.5 + lon / (2.0 * PI)), 0.5 - lat / PI);\n",
    "  }\n",
    "  vec2 t = rect.xy + clamp(uv, 0.0, 1.0) * rect.zw;\n",
    "  if (flip > 0.5) t.y = 1.0 - t.y;\n",
    "  float turned = rect.x + fract(clamp(uv.x, 0.0, 1.0) + 0.5) * rect.z;\n",
    "  vec2 dx = dFdx(t), dy = dFdy(t); float tx = dFdx(turned), ty = dFdy(turned);\n",
    "  if (projection < 0.5) { if (abs(tx) < abs(dx.x)) dx.x = tx; if (abs(ty) < abs(dy.x)) dy.x = ty; }\n",
    "  vec2 size = vec2(textureSize(tex, 0));\n",
    "  float texels = max(length(dx * size), length(dy * size));\n",
    "  vec3 c = textureGrad(tex, t, dx, dy).rgb;\n",
    "  if (cubic < 0.0 && texels < 1.0) {\n",
    "    vec2 a0 = rect.xy, a1 = rect.xy + rect.zw;\n",
    "    if (flip > 0.5) { a0.y = 1.0 - (rect.y + rect.w); a1.y = 1.0 - rect.y; }\n",
    "    c = mix(keys(t, size, a0 + 0.5 / size, a1 - 0.5 / size), c, smoothstep(0.75, 1.0, texels));\n",
    "  }\n",
    // the background drawn before this pass shows there
    "  if (!inside && behind > 0.5) discard;\n",
    "  color = inside ? vec4(c, 1.0) : vec4(0.0, 0.0, 0.0, 1.0);\n",
    "}\n",
);

const QUAD: [f32; 8] = [-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0];

struct Uniforms {
    tex: Option<glow::UniformLocation>,
    view: Option<glow::UniformLocation>,
    half: Option<glow::UniformLocation>,
    projection: Option<glow::UniformLocation>,
    rect: Option<glow::UniformLocation>,
    fov: Option<glow::UniformLocation>,
    flip: Option<glow::UniformLocation>,
    cubic: Option<glow::UniformLocation>,
    behind: Option<glow::UniformLocation>,
}

pub struct Sphere {
    gl: Rc<glow::Context>,
    program: glow::Program,
    quad: glow::Buffer,
    pos: u32,
    u: Uniforms,
    filter: Filter,
    forced: bool,
}

fn compile(gl: &glow::Context, kind: u32, source: &str) -> Option<glow::Shader> {
    // SAFETY: plain GL calls on the current context.
    unsafe {
        let shader = gl.create_shader(kind).ok()?;
        gl.shader_source(shader, source);
        gl.compile_shader(shader);
        if gl.get_shader_compile_status(shader) {
            return Some(shader);
        }
        if std::env::var_os("XR_VIDEO_DEBUG").is_some() {
            eprint!("{}", gl.get_shader_info_log(shader));
        }
        gl.delete_shader(shader);
        None
    }
}

impl Sphere {
    /// Err with the reason if the shader does not build. XR_VIDEO_FILTER names a filter that wins over the setting.
    pub fn new(gl: Rc<glow::Context>) -> Result<Self, String> {
        let (vs, fs) = (
            compile(&gl, glow::VERTEX_SHADER, VERTEX),
            compile(&gl, glow::FRAGMENT_SHADER, FRAGMENT),
        );
        // SAFETY: plain GL calls on the current context; every object made here is deleted here or in Drop.
        unsafe {
            let (Some(vs), Some(fs)) = (vs, fs) else {
                vs.into_iter().chain(fs).for_each(|s| gl.delete_shader(s));
                return Err("video shader compile".into());
            };
            let program = gl.create_program().map_err(|_| "video shader link")?;
            gl.attach_shader(program, vs);
            gl.attach_shader(program, fs);
            gl.link_program(program);
            gl.delete_shader(vs);
            gl.delete_shader(fs);
            let pos = gl.get_attrib_location(program, "pos");
            let (true, Some(pos)) = (gl.get_program_link_status(program), pos) else {
                gl.delete_program(program);
                return Err("video shader link".into());
            };
            let Ok(quad) = gl.create_buffer() else {
                gl.delete_program(program);
                return Err("video vertex buffer".into());
            };
            // A buffer, not a client array (glow has none); bound only while drawing.
            gl.bind_buffer(glow::ARRAY_BUFFER, Some(quad));
            let bytes: Vec<u8> = QUAD.iter().flat_map(|f| f.to_ne_bytes()).collect();
            gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, &bytes, glow::STATIC_DRAW);
            gl.bind_buffer(glow::ARRAY_BUFFER, None);
            let at = |name| gl.get_uniform_location(program, name);
            let u = Uniforms {
                tex: at("tex"),
                view: at("view"),
                half: at("half_fov"),
                projection: at("projection"),
                rect: at("rect"),
                fov: at("fov"),
                flip: at("flip"),
                cubic: at("cubic"),
                behind: at("behind"),
            };
            let forced = std::env::var("XR_VIDEO_FILTER")
                .ok()
                .and_then(|n| Filter::parse(&n));
            Ok(Self {
                gl,
                program,
                quad,
                pos,
                u,
                filter: forced.unwrap_or_default(),
                forced: forced.is_some(),
            })
        }
    }

    pub fn set_filter(&mut self, f: Filter) {
        self.filter = f;
    }

    pub fn filter(&self) -> Filter {
        self.filter
    }

    /// XR_VIDEO_FILTER named a filter: it wins over the quality setting.
    pub fn forced(&self) -> bool {
        self.forced
    }

    /// Draw `eye` of the frame in `texture` into the current viewport. `behind`: leave pixels outside the picture
    /// untouched (the background drawn first shows there) instead of black.
    #[allow(clippy::too_many_arguments)]
    pub fn draw(
        &self,
        texture: glow::Texture,
        flip_y: bool,
        format: &Format,
        eye: usize,
        view: &Mat3,
        half_w: f32,
        half_h: f32,
        behind: bool,
    ) {
        let gl = &self.gl;
        let r = eye_rect(format.stereo, eye);
        let fov = format.clamped_fov_deg() as f32 * std::f32::consts::PI / 180.0;
        // SAFETY: plain GL calls on the current context with objects this sphere owns.
        unsafe {
            gl.disable(glow::BLEND);
            gl.use_program(Some(self.program));
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(glow::TEXTURE_2D, Some(texture));
            gl.uniform_1_i32(self.u.tex.as_ref(), 0);
            gl.uniform_matrix_3_f32_slice(self.u.view.as_ref(), false, &transpose(view));
            gl.uniform_2_f32(self.u.half.as_ref(), half_w, half_h);
            gl.uniform_1_f32(self.u.projection.as_ref(), format.projection as i32 as f32);
            gl.uniform_4_f32(self.u.rect.as_ref(), r.x, r.y, r.w, r.h);
            gl.uniform_1_f32(self.u.fov.as_ref(), fov);
            gl.uniform_1_f32(self.u.flip.as_ref(), if flip_y { 1.0 } else { 0.0 });
            gl.uniform_1_f32(self.u.cubic.as_ref(), self.filter.cubic_a());
            gl.uniform_1_f32(self.u.behind.as_ref(), if behind { 1.0 } else { 0.0 });
            gl.bind_buffer(glow::ARRAY_BUFFER, Some(self.quad));
            gl.vertex_attrib_pointer_f32(self.pos, 2, glow::FLOAT, false, 0, 0);
            gl.enable_vertex_attrib_array(self.pos);
            gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
            gl.disable_vertex_attrib_array(self.pos);
            gl.bind_buffer(glow::ARRAY_BUFFER, None);
        }
    }
}

impl Drop for Sphere {
    fn drop(&mut self) {
        // SAFETY: objects this sphere created on the context, still current (the caller's contract).
        unsafe {
            self.gl.delete_buffer(self.quad);
            self.gl.delete_program(self.program);
        }
    }
}
