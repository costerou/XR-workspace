#include "environment_gl.hpp"
#include <string>

namespace xr {
namespace {
// The eye-space ray through each corner of a triangle covering the viewport, turned into the world: interpolated
// linearly in screen space it is exact for a perspective projection (the eye ray is linear in NDC). `view` is uploaded
// column-major from the row-major world -> eye matrix, so `e * view` is viewᵀ·e (eye -> world), as the video pass does.
const char* kVertex =
    "attribute vec2 pos; uniform mat3 view; uniform vec2 half_fov; varying vec3 dir;\n"
    "void main() { dir = vec3(pos.x * half_fov.x, pos.y * half_fov.y, -1.0) * view; gl_Position = vec4(pos, 0.0, 1.0); }\n";

// Every background's fragment shader: this head, its scene(), the tail.
const char* kHead =
    "#extension GL_OES_standard_derivatives : enable\n"
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n"
    "varying vec3 dir; uniform vec3 line; uniform float level; uniform float gain; uniform float seconds; uniform float cap;\n";
// `gain` (the brightness) scales the scene, so black stays black. The result keeps its hue under `cap` (its brightest channel
// is scaled down to it), then the fade multiplies it. A small dither (interleaved gradient noise; not a sin() hash: past 2^16
// radeonsi's sin is NaN, which paints black) breaks up the banding of a dim scene, never on black.
const char* kTail =
    "void main() {\n"
    "  vec3 c = scene(normalize(dir)) * gain;\n"
    "  float most = max(max(c.r, c.g), c.b);\n"
    "  float n = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))) - 0.5;\n"
    "  c += vec3(n / 255.0) * smoothstep(0.0, 3.0 / 255.0, most);\n"
    "  most = max(max(c.r, c.g), c.b);\n"
    "  c *= min(1.0, cap / max(most, 1e-6));\n"
    "  gl_FragColor = vec4(max(c, 0.0) * level, 1.0);\n"
    "}\n";
// Grid. World (GL frame): y up, -z ahead at the recentre heading. The floor is a plane kEye below the eye. Everything is
// `line`, the theme colour, so every lit pixel has its hue.
//  * floor grid: thin lines every kCell, a brighter one every kMajor cells, anti-aliased from the screen derivatives (lines
//    finer than a pixel fade to their average instead of shimmering: Ben Golus' "pristine grid"), with a soft halo (the
//    bloom), fading with distance into the horizon haze;
//  * pulses: about one line in five carries a light-cycle trail running along it (8, 12 or 16 m/s, one every kPeriod m),
//    brighter than the line; seamless as `seconds` wraps (environment.hpp kEnvironmentLoopS);
//  * horizon: a thin bright line and a haze either side of it, gone to exactly black by the edge of its band;
//  * mountains: a ring of sharp ridges on the horizon (sums of triangle waves in azimuth, periodic, gaps between ranges),
//    black inside (they hide the haze behind them) with a glowing outline;
//  * the sky: black (see-through on the glasses).
// Derivatives are taken outside any branch (the branches below differ between neighbouring pixels only at their edges).
const char* kGrid =
    "const float kEye = 1.6, kCell = 2.0, kMajor = 5.0, kPeriod = 128.0, kTrail = 9.0, kFar = 42.0;\n"
    "const float kMinorWidth = 0.006, kMajorWidth = 0.0024, kHalo = 0.035;\n"
    // Per-axis coverage of lines `width` cells wide at `uv`; `deriv`: cells per pixel along each axis.
    "vec2 grid(vec2 uv, vec2 deriv, float width) {\n"
    "  vec2 draw = clamp(vec2(width), deriv, vec2(0.5));\n"
    "  vec2 aa = deriv * 1.5;\n"
    "  vec2 g = 1.0 - abs(fract(uv) * 2.0 - 1.0);\n"           // 2 x the distance to the nearest line, in cells
    "  vec2 l = 1.0 - smoothstep(draw - aa, draw + aa, g);\n"
    "  l *= clamp(vec2(width) / draw, 0.0, 1.0);\n"
    "  return mix(l, vec2(width), clamp(deriv * 2.0 - 1.0, 0.0, 1.0));\n"
    "}\n"
    // A light-cycle trail on line `index`, at `along` m along it: 1 at its head, fading behind it; 0 on most lines.
    "float pulse(float index, float along) {\n"
    "  float h = fract(index * 0.6180339887 + 0.37);\n"
    "  float speed = 8.0 + 4.0 * floor(h / 0.2 * 3.0);\n"         // h < 0.2 carries one: 8, 12 or 16 m/s
    "  float sgn = fract(index * 0.7548776662) < 0.5 ? -1.0 : 1.0;\n"
    "  float behind = mod(speed * seconds + fract(index * 0.5698402910) * kPeriod - along * sgn, kPeriod);\n"
    "  float trail = exp2(-behind / kTrail) * (1.0 - smoothstep(kPeriod - 0.6, kPeriod, behind));\n"
    "  return h < 0.2 ? trail : 0.0;\n"
    "}\n"
    "float tri(float x) { return 1.0 - abs(fract(x) * 2.0 - 1.0); }\n"
    "float ridge(float turns) {\n"
    "  float peaks = 0.55 * tri(turns * 11.0 + 0.10) + 0.30 * tri(turns * 23.0 + 0.35) + 0.15 * tri(turns * 47.0 + 0.80);\n"
    "  float range = tri(turns * 3.0 + 0.15) * 0.7 + tri(turns * 5.0 + 0.62) * 0.3;\n"  // ranges with gaps between them
    "  return max(peaks * range - 0.16, 0.0) * 0.14;\n"           // sin(elevation) of the ridge: at most ~5 degrees
    "}\n"
    "vec3 scene(vec3 d) {\n"
    "  float t = kEye / max(-d.y, 0.0015);\n"                     // to the floor (clamped near the horizon)
    "  vec2 uv = d.xz * (t / kCell) + kMajor * 0.5;\n"            // you stand in a cell, not on a line
    "  vec2 dx = dFdx(uv), dy = dFdy(uv);\n"
    "  vec2 deriv = vec2(length(vec2(dx.x, dy.x)), length(vec2(dx.y, dy.y)));\n"
    "  float wy = fwidth(d.y);\n"
    "  vec3 c = vec3(0.0);\n"
    "  if (d.y < 0.003) {\n"
    "    float floor_mask = smoothstep(0.0, 0.004, -d.y);\n"
    "    float near = exp2(-t / kFar);\n"
    "    vec2 minor = grid(uv, deriv, kMinorWidth);\n"
    "    vec2 major = grid(uv / kMajor, deriv / kMajor, kMajorWidth);\n"
    "    vec2 dist = abs(fract(uv + 0.5) - 0.5);\n"
    "    vec2 halo = exp2(-dist / kHalo);\n"
    "    float mean = 2.0 * kHalo * (1.0 - exp2(-0.5 / kHalo)) / 0.6931472;\n"
    "    halo = mix(halo, vec2(mean), clamp(deriv / kHalo - 0.5, 0.0, 1.0));\n"
    "    float lit = mix(minor.x, 1.0, minor.y) * 0.62 + mix(major.x, 1.0, major.y) * 0.38;\n"
    "    float glow = max(max(halo.x, halo.y) * 0.16 - 0.006, 0.0);\n"  // ends: the floor between the lines is exactly black
    "    vec2 p = uv * kCell;\n"                                  // m along the lines (any origin: only the pulses use it)
    "    float px = pulse(floor(uv.y + 0.5), p.x), pz = pulse(floor(uv.x + 0.5) + 1000.0, p.y);\n"
    "    float trails = px * (minor.y + 0.45 * halo.y) + pz * (minor.x + 0.45 * halo.x);\n"
    "    c = line * (lit + glow + trails * 1.6) * (near * floor_mask);\n"
    "  }\n"
    "  if (abs(d.y) < 0.14) {\n"
    "    float y = d.y;\n"
    "    float r = ridge(atan(d.x, -d.z) * 0.1591549431);\n"
    "    float e = y - r;\n"
    "    float w = wy * 1.6 + 0.0004;\n"
    "    float body = r > 0.0 ? smoothstep(-w, w, y) * (1.0 - smoothstep(-w, w, e)) : 0.0;\n"   // inside a mountain
    "    float haze = (exp2(-abs(y) / 0.010) * 0.42 + exp2(-abs(y) / 0.032) * 0.08) * (1.0 - smoothstep(0.09, 0.135, abs(y)));\n"
    "    float horizon = 1.0 - smoothstep(0.0, wy * 1.5 + 0.0005, abs(y));\n"
    "    float outline = r > 0.0 ? (1.0 - smoothstep(0.0, w, abs(e))) * 0.85 + exp2(-abs(e) / 0.004) * 0.22 : 0.0;\n"
    "    c = c * (1.0 - body) + line * (haze * (1.0 - 0.9 * body) + horizon * 0.9 + outline * smoothstep(0.0, 0.006, r));\n"
    "  }\n"
    "  return c;\n"
    "}\n";
// One scene per kBackgrounds row; nullptr draws nothing.
const char* scene_of(Background background) { return background == Background::grid ? kGrid : nullptr; }

GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint ok = 0; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) { glDeleteShader(shader); return 0; }
    return shader;
}
}

std::unique_ptr<EnvironmentPass> EnvironmentPass::create() {
    std::unique_ptr<EnvironmentPass> out(new EnvironmentPass());
    auto& s = *out;
    for (std::size_t i = 0; i < kBackgroundCount; ++i) {
        const char* scene = scene_of(Background(i));
        if (!scene) continue;
        const std::string fragment = std::string(kHead) + scene + kTail;
        GLuint v = compile(GL_VERTEX_SHADER, kVertex), f = compile(GL_FRAGMENT_SHADER, fragment.c_str());
        if (!v || !f) { if (v) glDeleteShader(v); if (f) glDeleteShader(f); return nullptr; }
        auto& p = s.programs_[i];
        p.id = glCreateProgram(); glAttachShader(p.id, v); glAttachShader(p.id, f); glLinkProgram(p.id);
        glDeleteShader(v); glDeleteShader(f);
        GLint linked = 0; glGetProgramiv(p.id, GL_LINK_STATUS, &linked);
        if (!linked) return nullptr;  // the destructor deletes the programs
        p.pos = glGetAttribLocation(p.id, "pos");
        p.view = glGetUniformLocation(p.id, "view"); p.half_fov = glGetUniformLocation(p.id, "half_fov");
        p.line = glGetUniformLocation(p.id, "line"); p.level = glGetUniformLocation(p.id, "level");
        p.gain = glGetUniformLocation(p.id, "gain"); p.seconds = glGetUniformLocation(p.id, "seconds");
        if (p.pos < 0) return nullptr;
        glUseProgram(p.id);
        glUniform1f(glGetUniformLocation(p.id, "cap"), kEnvironmentCap);
        glUseProgram(0);
    }
    const GLfloat corners[] = {-1, -1, 3, -1, -1, 3};  // one triangle covering the viewport
    glGenBuffers(1, &s.buffer_); glBindBuffer(GL_ARRAY_BUFFER, s.buffer_);
    glBufferData(GL_ARRAY_BUFFER, sizeof corners, corners, GL_STATIC_DRAW);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    if (glGetError() != GL_NO_ERROR) return nullptr;
    return out;
}

EnvironmentPass::~EnvironmentPass() {
    if (buffer_) glDeleteBuffers(1, &buffer_);
    for (const auto& p : programs_) if (p.id) glDeleteProgram(p.id);
}

void EnvironmentPass::draw(Background background, const Mat3& view, float half_w, float half_h, float level, float gain, const MdRgb& line,
                           float seconds) {
    const auto& p = programs_[std::size_t(background)];
    if (!p.id) return;
    const Mat3 column_major = transpose(view);  // GLES2 has no transpose flag
    glUseProgram(p.id);
    glUniformMatrix3fv(p.view, 1, GL_FALSE, column_major.data());
    glUniform2f(p.half_fov, half_w, half_h);
    glUniform3f(p.line, line[0], line[1], line[2]);
    glUniform1f(p.level, level);
    glUniform1f(p.gain, gain);
    glUniform1f(p.seconds, seconds);
    glBindBuffer(GL_ARRAY_BUFFER, buffer_);
    glVertexAttribPointer(GLuint(p.pos), 2, GL_FLOAT, GL_FALSE, 0, nullptr);
    glEnableVertexAttribArray(GLuint(p.pos));
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glDisableVertexAttribArray(GLuint(p.pos));
    glBindBuffer(GL_ARRAY_BUFFER, 0);
}
}
