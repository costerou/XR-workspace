//! The background behind the desktops (spike/include/xr/environment.hpp, spike/src/environment.cpp and
//! spike/src/environment_gl.cpp): its line colour, the levelled world-locked view, the on/off fade and the GL pass that
//! draws Grid or Smoke in place of the black clear. The GLSL is the C++'s, byte for byte (GLSL ES 1.00 with
//! OES_standard_derivatives, which every ES 3 context still takes). The table of backgrounds is [`crate::hud::BACKGROUNDS`].

use glow::HasContext;

use crate::gl::{Gl, compile_program};
use crate::hud::{BACKGROUND_COUNT, Background};
use crate::pose::{Mat3, Quat, apply, quat_conj, quat_mul, quat_normalized, quat_to_mat3, transpose, view_from};

/// The OLED cap: no channel of the pass is brighter.
pub const ENVIRONMENT_CAP: f32 = crate::ui_tokens::LEVEL_CAP_UNIT;
/// The brightest channel of a near-black theme colour is lifted to this.
pub const ENVIRONMENT_FLOOR: f32 = 72.0 / 255.0;
/// On / off fade.
pub const ENVIRONMENT_FADE_MS: f64 = 600.0;
/// The animation clock wraps every this many seconds: every Grid pulse's period and every Smoke fold's phase divides it
/// exactly, so the wrap never shows.
pub const ENVIRONMENT_LOOP_S: f64 = 512.0;

fn ease(t: f64) -> f32 {
    let s = t.clamp(0.0, 1.0) as f32;
    s * s * (3.0 - 2.0 * s)
}

/// The background's line colour from the theme accent: its hue, its brightest channel within
/// [ENVIRONMENT_FLOOR, ENVIRONMENT_CAP]; black (no hue to keep) is the floor's grey.
pub fn environment_line_color(mut accent: [f32; 3]) -> [f32; 3] {
    for v in &mut accent {
        *v = if v.is_finite() { v.clamp(0.0, 1.0) } else { 0.0 };
    }
    let most = accent[0].max(accent[1]).max(accent[2]);
    if most < 1e-3 {
        return [ENVIRONMENT_FLOOR; 3];
    }
    let scale = if most > ENVIRONMENT_CAP {
        ENVIRONMENT_CAP / most
    } else if most < ENVIRONMENT_FLOOR {
        ENVIRONMENT_FLOOR / most
    } else {
        1.0
    };
    accent.map(|v| v * scale)
}

/// The yaw of `q` alone (about the driver's z, up); identity looking straight up or down.
pub fn heading(q: Quat) -> Quat {
    let f = apply(&quat_to_mat3(q), [1.0, 0.0, 0.0]); // forward (driver x) in the level frame; z is up
    if f[0].hypot(f[1]) < 1e-4 {
        return Quat::default();
    }
    let yaw = f[1].atan2(f[0]);
    Quat { x: 0.0, y: 0.0, z: (yaw / 2.0).sin(), w: (yaw / 2.0).cos() }
}

/// World -> eye for the background: levelled (the horizon stays level whatever the recentre's pitch and roll) and
/// world-locked at the recentre heading. `head_relative`: the head relative to `reference` (the recentre pose); with no
/// reference, the head relative to identity.
pub fn environment_view(head_relative: Quat, reference: Option<Quat>) -> Mat3 {
    // The head relative to the levelled frame E = heading(reference) is E^-1 * reference * relative; view_from takes the
    // head relative to the recentre reference and an anchor A with A^-1 * relative equal to that: A = reference^-1 * E.
    let anchor = reference
        .and_then(|r| quat_normalized(quat_mul(quat_conj(r), heading(r))))
        .unwrap_or_default();
    view_from(head_relative, anchor)
}

/// The background's on/off fade: smoothstep over ENVIRONMENT_FADE_MS from wherever it was.
#[derive(Clone, Copy, Debug)]
pub struct EnvironmentFade {
    on: bool,
    from_level: f32,
    level_ms: f64,
}

impl Default for EnvironmentFade {
    fn default() -> Self {
        Self { on: false, from_level: 0.0, level_ms: -1e9 }
    }
}

impl EnvironmentFade {
    /// Turn it on or off at `now_ms`; false when that changes nothing.
    pub fn set(&mut self, on: bool, now_ms: f64) -> bool {
        if on == self.on {
            return false;
        }
        self.from_level = self.level(now_ms);
        self.level_ms = now_ms;
        self.on = on;
        true
    }
    pub fn level(&self, now_ms: f64) -> f32 {
        let target = if self.on { 1.0 } else { 0.0 };
        self.from_level + (target - self.from_level) * ease((now_ms - self.level_ms) / ENVIRONMENT_FADE_MS)
    }
    pub fn on(&self) -> bool {
        self.on
    }
    pub fn animating(&self, now_ms: f64) -> bool {
        now_ms - self.level_ms < ENVIRONMENT_FADE_MS
    }
}

// --- The GL pass (environment_gl.cpp) ----------------------------------------------------------------------------------

// The eye-space ray through each corner of a triangle covering the viewport, turned into the world: interpolated
// linearly in screen space it is exact for a perspective projection (the eye ray is linear in NDC). `view` is uploaded
// column-major from the row-major world -> eye matrix, so `e * view` is viewᵀ·e (eye -> world), as the video pass does.
const VERTEX: &str = concat!(
    "attribute vec2 pos; uniform mat3 view; uniform vec2 half_fov; uniform float eye; varying vec3 dir;\n",
    "void main() { dir = vec3((pos.x + eye) * half_fov.x, pos.y * half_fov.y, -1.0) * view; gl_Position = vec4(pos, 0.0, 1.0); }\n",
);

// Every background's fragment shader: this head, its scene(), the tail.
const HEAD: &str = concat!(
    "#extension GL_OES_standard_derivatives : enable\n",
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n",
    "varying vec3 dir; uniform vec3 line; uniform float level; uniform float gain; uniform float seconds; uniform float cap;\n",
);
// `gain` (the brightness) scales the scene, so black stays black. The result keeps its hue under `cap` (its brightest channel
// is scaled down to it), then the fade multiplies it. A small dither (interleaved gradient noise; not a sin() hash: past 2^16
// radeonsi's sin is NaN, which paints black) breaks up the banding of a dim scene, never on black.
const TAIL: &str = concat!(
    "void main() {\n",
    "  vec3 c = scene(normalize(dir)) * gain;\n",
    "  float most = max(max(c.r, c.g), c.b);\n",
    "  float n = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))) - 0.5;\n",
    "  c += vec3(n / 255.0) * smoothstep(0.0, 3.0 / 255.0, most);\n",
    "  most = max(max(c.r, c.g), c.b);\n",
    "  c *= min(1.0, cap / max(most, 1e-6));\n",
    "  gl_FragColor = vec4(max(c, 0.0) * level, 1.0);\n",
    "}\n",
);
// Grid (environment_gl.cpp kGrid, where each part is described): floor grid with pulses, horizon haze, mountains, black sky.
const GRID: &str = concat!(
    "const float kEye = 1.6, kCell = 2.0, kMajor = 5.0, kPeriod = 128.0, kTrail = 9.0, kFar = 42.0;\n",
    "const float kMinorWidth = 0.006, kMajorWidth = 0.0024, kHalo = 0.035;\n",
    "vec2 grid(vec2 uv, vec2 deriv, float width) {\n",
    "  vec2 draw = clamp(vec2(width), deriv, vec2(0.5));\n",
    "  vec2 aa = deriv * 1.5;\n",
    "  vec2 g = 1.0 - abs(fract(uv) * 2.0 - 1.0);\n",
    "  vec2 l = 1.0 - smoothstep(draw - aa, draw + aa, g);\n",
    "  l *= clamp(vec2(width) / draw, 0.0, 1.0);\n",
    "  return mix(l, vec2(width), clamp(deriv * 2.0 - 1.0, 0.0, 1.0));\n",
    "}\n",
    "float pulse(float index, float along) {\n",
    "  float h = fract(index * 0.6180339887 + 0.37);\n",
    "  float speed = 8.0 + 4.0 * floor(h / 0.2 * 3.0);\n",
    "  float sgn = fract(index * 0.7548776662) < 0.5 ? -1.0 : 1.0;\n",
    "  float behind = mod(speed * seconds + fract(index * 0.5698402910) * kPeriod - along * sgn, kPeriod);\n",
    "  float trail = exp2(-behind / kTrail) * (1.0 - smoothstep(kPeriod - 0.6, kPeriod, behind));\n",
    "  return h < 0.2 ? trail : 0.0;\n",
    "}\n",
    "float tri(float x) { return 1.0 - abs(fract(x) * 2.0 - 1.0); }\n",
    "float ridge(float turns) {\n",
    "  float peaks = 0.55 * tri(turns * 11.0 + 0.10) + 0.30 * tri(turns * 23.0 + 0.35) + 0.15 * tri(turns * 47.0 + 0.80);\n",
    "  float range = tri(turns * 3.0 + 0.15) * 0.7 + tri(turns * 5.0 + 0.62) * 0.3;\n",
    "  return max(peaks * range - 0.16, 0.0) * 0.14;\n",
    "}\n",
    "vec3 scene(vec3 d) {\n",
    "  float t = kEye / max(-d.y, 0.0015);\n",
    "  vec2 uv = d.xz * (t / kCell) + kMajor * 0.5;\n",
    "  vec2 dx = dFdx(uv), dy = dFdy(uv);\n",
    "  vec2 deriv = vec2(length(vec2(dx.x, dy.x)), length(vec2(dx.y, dy.y)));\n",
    "  float wy = fwidth(d.y);\n",
    "  vec3 c = vec3(0.0);\n",
    "  if (d.y < 0.003) {\n",
    "    float floor_mask = smoothstep(0.0, 0.004, -d.y);\n",
    "    float near = exp2(-t / kFar);\n",
    "    vec2 minor = grid(uv, deriv, kMinorWidth);\n",
    "    vec2 major = grid(uv / kMajor, deriv / kMajor, kMajorWidth);\n",
    "    vec2 dist = abs(fract(uv + 0.5) - 0.5);\n",
    "    vec2 halo = exp2(-dist / kHalo);\n",
    "    float mean = 2.0 * kHalo * (1.0 - exp2(-0.5 / kHalo)) / 0.6931472;\n",
    "    halo = mix(halo, vec2(mean), clamp(deriv / kHalo - 0.5, 0.0, 1.0));\n",
    "    float lit = mix(minor.x, 1.0, minor.y) * 0.62 + mix(major.x, 1.0, major.y) * 0.38;\n",
    "    float glow = max(max(halo.x, halo.y) * 0.16 - 0.006, 0.0);\n",
    "    vec2 p = uv * kCell;\n",
    "    float px = pulse(floor(uv.y + 0.5), p.x), pz = pulse(floor(uv.x + 0.5) + 1000.0, p.y);\n",
    "    float trails = px * (minor.y + 0.45 * halo.y) + pz * (minor.x + 0.45 * halo.x);\n",
    "    c = line * (lit + glow + trails * 1.6) * (near * floor_mask);\n",
    "  }\n",
    "  if (abs(d.y) < 0.14) {\n",
    "    float y = d.y;\n",
    "    float r = ridge(atan(d.x, -d.z) * 0.1591549431);\n",
    "    float e = y - r;\n",
    "    float w = wy * 1.6 + 0.0004;\n",
    "    float body = r > 0.0 ? smoothstep(-w, w, y) * (1.0 - smoothstep(-w, w, e)) : 0.0;\n",
    "    float haze = (exp2(-abs(y) / 0.010) * 0.42 + exp2(-abs(y) / 0.032) * 0.08) * (1.0 - smoothstep(0.09, 0.135, abs(y)));\n",
    "    float horizon = 1.0 - smoothstep(0.0, wy * 1.5 + 0.0005, abs(y));\n",
    "    float outline = r > 0.0 ? (1.0 - smoothstep(0.0, w, abs(e))) * 0.85 + exp2(-abs(e) / 0.004) * 0.22 : 0.0;\n",
    "    c = c * (1.0 - body) + line * (haze * (1.0 - 0.9 * body) + horizon * 0.9 + outline * smoothstep(0.0, 0.006, r));\n",
    "  }\n",
    "  return c;\n",
    "}\n",
);
// Smoke (environment_gl.cpp kSmoke): the view direction folded by sines of itself, read through three gyroids.
const SMOKE: &str = concat!(
    "const float kTurn = 6.2831853 / LOOP_S;\n",
    "float gyroid(vec3 p) { return dot(sin(p), cos(p.yzx)) / 1.5; }\n",
    "vec3 scene(vec3 d) {\n",
    "  float t = seconds * kTurn;\n",
    "  vec3 p = vec3(d.x, d.y * 2.0, d.z) * 13.0;\n",
    "  p += 1.7 * sin(p.zxy * 0.41 + vec3(1.3, 4.1, 2.2) + t * vec3(2.0, -3.0, 1.0));\n",
    "  p += 0.95 * sin(p.yzx * 0.83 + vec3(5.2, 0.7, 3.3) + t * vec3(-5.0, 4.0, 3.0));\n",
    "  p += 0.5 * sin(p.zxy * 1.67 + vec3(2.9, 6.1, 1.1) + t * vec3(7.0, 6.0, -8.0));\n",
    "  p += 0.26 * sin(p.yzx * 3.31 + vec3(0.4, 3.7, 5.6) + t * vec3(-9.0, 11.0, 10.0));\n",
    "  float bank = smoothstep(-0.55, 0.85, gyroid(p * 0.42));\n",
    "  float a = 1.0 - abs(gyroid(p * 0.91 + vec3(1.7, 0.3, 2.9)));\n",
    "  float b = 1.0 - abs(gyroid(p * 1.93 + vec3(4.1, 2.3, 0.6)));\n",
    "  float wisp = a * a * (0.45 + 0.55 * b);\n",
    "  float density = max(bank * bank * (0.13 + 0.72 * wisp) - 0.012, 0.0);\n",
    "  float band = 1.0 - smoothstep(0.1, 0.95, abs(d.y + 0.08));\n",
    "  return line * density * (0.3 + 0.7 * band);\n",
    "}\n",
);

/// One scene per BACKGROUNDS row; None draws nothing.
fn scene_of(b: Background) -> Option<&'static str> {
    match b {
        Background::Grid => Some(GRID),
        Background::Smoke => Some(SMOKE),
        Background::None => None,
    }
}

fn background_of(i: usize) -> Background {
    match i {
        1 => Background::Grid,
        2 => Background::Smoke,
        _ => Background::None,
    }
}

#[derive(Default)]
struct Program {
    id: Option<glow::Program>,
    pos: u32,
    view: Option<glow::UniformLocation>,
    half_fov: Option<glow::UniformLocation>,
    eye: Option<glow::UniformLocation>,
    line: Option<glow::UniformLocation>,
    level: Option<glow::UniformLocation>,
    gain: Option<glow::UniformLocation>,
    seconds: Option<glow::UniformLocation>,
    eye_set: f32,
}

/// The background's GL objects: one program per scene and the triangle covering the viewport.
pub struct EnvironmentPass {
    programs: [Program; BACKGROUND_COUNT],
    buffer: glow::Buffer,
}

impl EnvironmentPass {
    /// Compile every scene now (not on the first frame it shows). None: a shader failed (the run works without it).
    pub fn create(gl: &glow::Context) -> Option<Self> {
        let mut programs: [Program; BACKGROUND_COUNT] = Default::default();
        let mut ok = true;
        for (i, p) in programs.iter_mut().enumerate() {
            let Some(scene) = scene_of(background_of(i)) else { continue };
            // std::to_string(512.0) in the C++: six decimals.
            let fragment = format!("{HEAD}#define LOOP_S {ENVIRONMENT_LOOP_S:.6}\n{scene}{TAIL}");
            let Ok(id) = compile_program(gl, VERTEX, &fragment) else {
                ok = false;
                break;
            };
            p.id = Some(id);
            // SAFETY: plain GL queries and uniform sets on the current context, on the program just linked.
            unsafe {
                let Some(pos) = gl.get_attrib_location(id, "pos") else {
                    ok = false;
                    break;
                };
                p.pos = pos;
                let u = |name| gl.get_uniform_location(id, name);
                (p.view, p.half_fov, p.eye) = (u("view"), u("half_fov"), u("eye"));
                (p.line, p.level, p.gain, p.seconds) = (u("line"), u("level"), u("gain"), u("seconds"));
                gl.use_program(Some(id));
                gl.uniform_1_f32(gl.get_uniform_location(id, "cap").as_ref(), ENVIRONMENT_CAP);
                gl.use_program(None);
            }
        }
        let delete = |programs: &[Program]| {
            for id in programs.iter().filter_map(|p| p.id) {
                // SAFETY: made on this context above.
                unsafe { gl.delete_program(id) };
            }
        };
        if !ok {
            delete(&programs);
            return None;
        }
        let corners: [f32; 6] = [-1.0, -1.0, 3.0, -1.0, -1.0, 3.0]; // one triangle covering the viewport
        // SAFETY: plain GL calls on the current context; the bytes are the floats (f32 has no padding).
        unsafe {
            let Ok(buffer) = gl.create_buffer() else {
                delete(&programs);
                return None;
            };
            gl.bind_buffer(glow::ARRAY_BUFFER, Some(buffer));
            let bytes = std::slice::from_raw_parts(corners.as_ptr().cast::<u8>(), size_of_val(&corners));
            gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes, glow::STATIC_DRAW);
            gl.bind_buffer(glow::ARRAY_BUFFER, None);
            if gl.get_error() != glow::NO_ERROR {
                gl.delete_buffer(buffer);
                delete(&programs);
                return None;
            }
            Some(Self { programs, buffer })
        }
    }

    /// The program that draws `b` (None for Background::None).
    pub fn program(&self, b: Background) -> Option<glow::Program> {
        self.programs[b as usize].id
    }

    /// Draw `b` over the whole viewport. `view`: world -> eye, row-major; `eye_shift`: this eye's shift in NDC (0 in 2D).
    #[allow(clippy::too_many_arguments)]
    pub fn draw(
        &mut self,
        gl: &Gl,
        b: Background,
        view: &Mat3,
        half_w: f32,
        half_h: f32,
        level: f32,
        gain: f32,
        line: [f32; 3],
        seconds: f32,
        eye_shift: f32,
    ) {
        let p = &mut self.programs[b as usize];
        let Some(id) = p.id else { return };
        let column_major = transpose(view); // GLES2 has no transpose flag
        gl.use_program(Some(id));
        gl.uniform_matrix_3(p.view.as_ref(), &column_major);
        gl.uniform_2f(p.half_fov.as_ref(), half_w, half_h);
        gl.uniform_3f(p.line.as_ref(), line[0], line[1], line[2]);
        gl.uniform_1f(p.level.as_ref(), level);
        gl.uniform_1f(p.gain.as_ref(), gain);
        gl.uniform_1f(p.seconds.as_ref(), seconds);
        if p.eye_set != eye_shift {
            gl.uniform_1f(p.eye.as_ref(), eye_shift);
            p.eye_set = eye_shift;
        }
        gl.bind_buffer(Some(self.buffer));
        gl.buffer_attrib(p.pos, 2, 0, 0);
        gl.enable_attrib(p.pos);
        gl.draw_arrays(glow::TRIANGLES, 0, 3);
        gl.disable_attrib(p.pos);
        gl.bind_buffer(None);
    }

    /// Delete the GL objects (the context is current).
    pub fn destroy(self, gl: &glow::Context) {
        // SAFETY: made on this context in create.
        unsafe {
            gl.delete_buffer(self.buffer);
            for id in self.programs.iter().filter_map(|p| p.id) {
                gl.delete_program(id);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_color_keeps_the_hue_within_floor_and_cap() {
        let tokyo = [0x7a as f32 / 255.0, 0xa2 as f32 / 255.0, 0xf7 as f32 / 255.0];
        let c = environment_line_color(tokyo);
        assert!((c[2] - ENVIRONMENT_CAP).abs() < 1e-6);
        assert!((c[0] / c[2] - tokyo[0] / tokyo[2]).abs() < 1e-6);
        assert_eq!(environment_line_color([0.0; 3]), [ENVIRONMENT_FLOOR; 3]);
        assert_eq!(environment_line_color([f32::NAN, 0.0, 0.0]), [ENVIRONMENT_FLOOR; 3]);
        let dim = environment_line_color([0.1, 0.05, 0.0]);
        assert!((dim[0] - ENVIRONMENT_FLOOR).abs() < 1e-6 && (dim[1] - ENVIRONMENT_FLOOR / 2.0).abs() < 1e-6);
        let mid = [0.5, 0.4, 0.3];
        assert_eq!(environment_line_color(mid), mid);
    }

    #[test]
    fn fade_eases_both_ways_from_where_it_was() {
        let mut f = EnvironmentFade::default();
        assert_eq!(f.level(0.0), 0.0);
        assert!(!f.animating(0.0));
        assert!(!f.set(false, 0.0));
        assert!(f.set(true, 1000.0));
        assert!(f.animating(1300.0) && !f.animating(1600.0));
        assert!((f.level(1300.0) - 0.5).abs() < 1e-6);
        assert_eq!(f.level(1600.0), 1.0);
        assert!(f.set(false, 1300.0) && f.on() == false);
        // From 1 (the fade had ended), half way down at 300 ms.
        let mut g = EnvironmentFade::default();
        g.set(true, 0.0);
        g.set(false, 600.0);
        assert!((g.level(900.0) - 0.5).abs() < 1e-6);
        assert_eq!(g.level(1200.0), 0.0);
    }

    #[test]
    fn view_is_levelled_and_locked_at_the_recentre_heading() {
        // A recentre pitched down 20 degrees and turned 30: looking along it, the background's horizon is 20 degrees up.
        let rot = |axis: [f32; 3], deg: f32| {
            let h = deg.to_radians() / 2.0;
            Quat { x: axis[0] * h.sin(), y: axis[1] * h.sin(), z: axis[2] * h.sin(), w: h.cos() }
        };
        let reference = quat_mul(rot([0.0, 0.0, 1.0], 30.0), rot([0.0, 1.0, 0.0], 20.0));
        let h = heading(reference);
        let yaw = 2.0 * h.z.atan2(h.w);
        assert!((yaw.to_degrees() - 30.0).abs() < 1e-3);
        let v = environment_view(Quat::default(), Some(reference));
        let none = environment_view(Quat::default(), None);
        assert_ne!(v, none);
        // Rows are orthonormal.
        for r in 0..3 {
            let n = (0..3).map(|c| v[r * 3 + c] * v[r * 3 + c]).sum::<f32>();
            assert!((n - 1.0).abs() < 1e-4);
        }
        assert_eq!(heading(rot([0.0, 1.0, 0.0], 90.0)), Quat::default());
    }
}
