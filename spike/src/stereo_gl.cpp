#include <GLES3/gl3.h>
#include "stereo_gl.hpp"
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace xr {
namespace {
const char* kQuadVertex = "#version 300 es\nin vec2 pos; out vec2 uv; void main() { uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }\n";
// Mirrors stereo.cpp stereo_text_mask.
const char* kTextFragment =
    "#version 300 es\nprecision highp float;\n"
    "in vec2 uv; out vec4 color; uniform sampler2D colour; uniform vec2 step_px;\n"
    "float luma(vec2 t) { vec3 c = texture(colour, t).rgb; return 0.25 * c.r + 0.5 * c.g + 0.25 * c.b; }\n"
    "void main() {\n"
    "  float jumps = 0.0;\n"
    "  for (int r = 0; r < 3; ++r) {\n"
    "    float y = uv.y + (float(r) - 1.0) * step_px.y; float before = 0.0, last = 0.0;\n"
    "    for (int c = 0; c < 8; ++c) {\n"
    "      float l = luma(vec2(uv.x + (float(c) - 3.5) * step_px.x, y));\n"
    "      float d = l - before, way = d > 0.0 ? 1.0 : -1.0;\n"
    "      if (c > 0 && abs(d) > 0.1 && way != last) { jumps += 1.0; last = way; }\n"
    "      before = l;\n"
    "    }\n"
    "  }\n"
    "  color = vec4(smoothstep(1.0, 2.0, jumps / 3.0));\n"
    "}\n";
// Mirrors stereo.cpp stereo_guide (stereo_v, stereo_edge_gain, stereo_temporal).
const char* kGuideFragment =
    "#version 300 es\nprecision highp float;\n"
    "in vec2 uv; out vec4 color; uniform highp sampler2D raw; uniform sampler2D text; uniform highp sampler2D previous;\n"
    "uniform vec2 range; uniform vec3 params; uniform float alpha; uniform float aspect; uniform float history;\n"
    "const float KNEE = 0.7, ROOM = 0.3, TAPER = 0.05;\n"
    "void main() {\n"
    "  ivec2 p = ivec2(gl_FragCoord.xy), top = textureSize(raw, 0) - 1;\n"
    "  float r = texelFetch(raw, p, 0).r, v = 0.0;\n"
    "  if (!isnan(r) && !isinf(r)) {\n"
    "    float span = range.y - range.x;\n"
    "    float n = clamp(abs(span) > 1e-6 ? (r - range.x) / span : 0.5, 0.0, 1.0);\n"
    "    float x = 2.0 * params.x * (n - params.y), a = abs(x);\n"
    "    v = a <= KNEE ? x : sign(x) * (KNEE + ROOM * tanh((a - KNEE) / ROOM));\n"
    "  }\n"
    "  float t = texelFetch(text, p, 0).r;\n"
    "  t = max(t, max(texelFetch(text, clamp(p + ivec2(-1, 0), ivec2(0), top), 0).r, texelFetch(text, clamp(p + ivec2(1, 0), ivec2(0), top), 0).r));\n"
    "  t = max(t, max(texelFetch(text, clamp(p + ivec2(0, -1), ivec2(0), top), 0).r, texelFetch(text, clamp(p + ivec2(0, 1), ivec2(0), top), 0).r));\n"
    "  v *= 1.0 + (params.z - 1.0) * t;\n"
    "  float e = min(min(uv.x, 1.0 - uv.x) / TAPER, min(uv.y, 1.0 - uv.y) / (TAPER * aspect));\n"
    "  v *= smoothstep(0.0, 1.0, e);\n"
    "  if (history > 0.5) {\n"
    "    float q = texelFetch(previous, p, 0).r, d = abs(v - q);\n"
    "    v = q + (v - q) * (alpha + (1.0 - alpha) * smoothstep(0.1, 0.35, d));\n"
    "  }\n"
    "  color = vec4(v, 0.0, 0.0, 1.0);\n"
    "}\n";
const char* kWarpVertex =
    "#version 300 es\nin vec3 pos; in vec2 uv; out vec2 v;\n" XR_PROJECT "void main() { v = uv; gl_Position = project(pos); }\n";
// Mirrors stereo.cpp stereo_warp. `dir` is how far the desktop's uv moves per eye pixel to the right, so the warp follows the
// eye's horizontal whatever the desktop's roll, size or curvature. Output pixel x shows the source point s with
// x = s + side g(s): the march goes from the near limit down, so the first crossing is the front-most surface.
const char* kWarpFragment =
    "#version 300 es\nprecision highp float;\n"
    "in vec2 v; out vec4 color; uniform sampler2D tex; uniform highp sampler2D guide; uniform float swizzle;\n"
    "uniform vec3 budget;\n"  // side, near half, far half
    "vec2 dir;\n"
    "float g_at(float o) { float d = textureLod(guide, v - budget.x * o * dir, 0.0).r; return d > 0.0 ? d * budget.y : d * budget.z; }\n"
    "void main() {\n"
    "  dir = dFdx(v); vec2 up = dFdy(v);\n"
    "  float side = budget.x, gn = budget.y, gf = budget.z, span = gn + gf;\n"
    "  float a = gn, ga = g_at(a), b = a, gb = ga, step_px = span / 12.0; bool hit = false;\n"
    "  for (int i = 1; i <= 12; ++i) {\n"
    "    b = gn - step_px * float(i); gb = g_at(b);\n"
    "    if (b - gb <= 0.0) { hit = true; break; }\n"
    "    a = b; ga = gb;\n"
    "  }\n"
    "  if (!hit) { b = -gf; gb = g_at(b); }\n"
    "  for (int i = 0; i < 3; ++i) {\n"
    "    float m = 0.5 * (a + b), gm = g_at(m);\n"
    "    if (m - gm > 0.0) { a = m; ga = gm; } else { b = m; gb = gm; }\n"
    "  }\n"
    "  float ha = a - ga, hb = b - gb, gap = a - b;\n"
    "  float o = gap > 1e-6 && ha - hb > 1e-6 ? a - gap * ha / (ha - hb) : b;\n"
    "  vec3 c = textureLod(tex, v - side * o * dir, 0.0).rgb;\n"
    "  float steep = gap > 1e-6 ? smoothstep(0.8, 1.6, (gb - ga) / gap) : 0.0;\n"
    "  if (steep > 0.0) {\n"
    "    float margin = 1.5 / (float(textureSize(guide, 0).x) * max(length(dir), 1e-7));\n"
    "    float q = o + margin, m = clamp(q - g_at(q), 0.0, span);\n"
    "    vec2 f = v - side * (q + m) * dir;\n"
    "    vec3 fill = textureLod(tex, f - 1.5 * up, 0.0).rgb + 2.0 * textureLod(tex, f, 0.0).rgb + textureLod(tex, f + 1.5 * up, 0.0).rgb;\n"
    "    c = mix(c, fill * 0.25, steep);\n"
    "  }\n"
    "  color = vec4(mix(c, c.bgr, swizzle), 1.0);\n"
    "}\n";
// The pointer sits on the surface under it. `lift`: x the eye's constant NDC shift; y, z NDC per unit of v, near and far.
const char* kPointerVertex =
    "#version 300 es\nin vec3 pos; uniform highp sampler2D guide; uniform vec2 surface; uniform vec3 lift;\n" XR_PROJECT
    "void main() { vec4 c = project(pos); float d = textureLod(guide, surface, 0.0).r;\n"
    "  c.x += (lift.x + (d > 0.0 ? d * lift.y : d * lift.z)) * c.w; gl_Position = c; }\n";
const char* kPointerFragment = "#version 300 es\nprecision mediump float; uniform vec4 color; out vec4 o; void main() { o = color; }\n";

GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint ok = 0; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        if (std::getenv("XR_STEREO_DEBUG")) { char log[2048] = {}; glGetShaderInfoLog(shader, sizeof log, nullptr, log); std::fputs(log, stderr); }
        glDeleteShader(shader); return 0;
    }
    return shader;
}
template <class F> GLuint link(const char* vertex, const char* fragment, F before_link) {
    GLuint v = compile(GL_VERTEX_SHADER, vertex), f = compile(GL_FRAGMENT_SHADER, fragment);
    if (!v || !f) { if (v) glDeleteShader(v); if (f) glDeleteShader(f); return 0; }
    GLuint p = glCreateProgram(); glAttachShader(p, v); glAttachShader(p, f); before_link(p); glLinkProgram(p);
    glDeleteShader(v); glDeleteShader(f);
    GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) { glDeleteProgram(p); return 0; }
    return p;
}
GLuint texture(GLenum internal, GLenum format, GLenum type, int w, int h, GLint filter) {
    GLuint t = 0; glGenTextures(1, &t); glBindTexture(GL_TEXTURE_2D, t);
    glTexImage2D(GL_TEXTURE_2D, 0, GLint(internal), w, h, 0, format, type, nullptr);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    return t;
}
GLuint framebuffer(GLuint tex) {
    GLuint f = 0; glGenFramebuffers(1, &f); glBindFramebuffer(GL_FRAMEBUFFER, f);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    return f;
}
}

std::unique_ptr<StereoPass> StereoPass::create(GLint tile_pos, GLint tile_uv, GLint color_pos, std::string& why) {
    const char* version = reinterpret_cast<const char*>(glGetString(GL_VERSION));
    if (!version || std::strncmp(version, "OpenGL ES 3", 11) != 0) { why = "stereo needs GLES 3"; return nullptr; }
    std::unique_ptr<StereoPass> out(new StereoPass());
    auto& s = *out;
    s.text_.id = link(kQuadVertex, kTextFragment, [](GLuint) {});
    s.guide_.id = link(kQuadVertex, kGuideFragment, [](GLuint) {});
    s.warp_.id = link(kWarpVertex, kWarpFragment, [&](GLuint p) {
        if (tile_pos >= 0) glBindAttribLocation(p, GLuint(tile_pos), "pos");
        if (tile_uv >= 0) glBindAttribLocation(p, GLuint(tile_uv), "uv");
    });
    s.pointer_.id = link(kPointerVertex, kPointerFragment, [&](GLuint p) { if (color_pos >= 0) glBindAttribLocation(p, GLuint(color_pos), "pos"); });
    if (!s.text_.id || !s.guide_.id || !s.warp_.id || !s.pointer_.id) { why = "stereo shader compile"; return nullptr; }
    s.text_.pos = glGetAttribLocation(s.text_.id, "pos"); s.text_.colour = glGetUniformLocation(s.text_.id, "colour");
    s.text_.step = glGetUniformLocation(s.text_.id, "step_px");
    const GLuint g = s.guide_.id;
    s.guide_.pos = glGetAttribLocation(g, "pos"); s.guide_.raw = glGetUniformLocation(g, "raw"); s.guide_.text = glGetUniformLocation(g, "text");
    s.guide_.previous = glGetUniformLocation(g, "previous"); s.guide_.range = glGetUniformLocation(g, "range");
    s.guide_.params = glGetUniformLocation(g, "params"); s.guide_.alpha = glGetUniformLocation(g, "alpha");
    s.guide_.aspect = glGetUniformLocation(g, "aspect"); s.guide_.history = glGetUniformLocation(g, "history");
    const GLuint w = s.warp_.id;
    s.warp_.view = glGetUniformLocation(w, "view"); s.warp_.half_fov = glGetUniformLocation(w, "half_fov");
    s.warp_.stereo = glGetUniformLocation(w, "stereo"); s.warp_.swizzle = glGetUniformLocation(w, "swizzle");
    s.warp_.tex = glGetUniformLocation(w, "tex"); s.warp_.guide = glGetUniformLocation(w, "guide"); s.warp_.budget = glGetUniformLocation(w, "budget");
    const GLuint r = s.pointer_.id;
    s.pointer_.view = glGetUniformLocation(r, "view"); s.pointer_.half_fov = glGetUniformLocation(r, "half_fov");
    s.pointer_.stereo = glGetUniformLocation(r, "stereo"); s.pointer_.color = glGetUniformLocation(r, "color");
    s.pointer_.guide = glGetUniformLocation(r, "guide"); s.pointer_.surface = glGetUniformLocation(r, "surface"); s.pointer_.lift = glGetUniformLocation(r, "lift");
    glUseProgram(s.warp_.id); glUniform1i(s.warp_.tex, 0); glUniform1i(s.warp_.guide, 1);
    glUseProgram(s.pointer_.id); glUniform1i(s.pointer_.guide, 1);
    glUseProgram(s.text_.id); glUniform1i(s.text_.colour, 0);
    glUseProgram(s.guide_.id); glUniform1i(s.guide_.raw, 0); glUniform1i(s.guide_.text, 1); glUniform1i(s.guide_.previous, 2);
    glUseProgram(0);
    static const GLfloat quad[] = {-1, -1, 1, -1, -1, 1, 1, 1};
    glGenBuffers(1, &s.quad_); glBindBuffer(GL_ARRAY_BUFFER, s.quad_);
    glBufferData(GL_ARRAY_BUFFER, sizeof quad, quad, GL_STATIC_DRAW); glBindBuffer(GL_ARRAY_BUFFER, 0);
    // Rendering to R16F is an extension in ES 3.0: find out now, not on the first depth frame.
    GLint before = 0; glGetIntegerv(GL_FRAMEBUFFER_BINDING, &before);
    GLuint t = texture(GL_R16F, GL_RED, GL_HALF_FLOAT, 4, 4, GL_LINEAR), f = framebuffer(t);
    const bool complete = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
    glBindFramebuffer(GL_FRAMEBUFFER, GLuint(before)); glDeleteFramebuffers(1, &f); glDeleteTextures(1, &t);
    glBindTexture(GL_TEXTURE_2D, 0);
    if (!complete) { why = "stereo needs a float colour buffer"; return nullptr; }
    while (glGetError() != GL_NO_ERROR) {}
    return out;
}

StereoPass::~StereoPass() {
    for (auto& s : sources_) drop(s);
    for (const GLuint p : {text_.id, guide_.id, warp_.id, pointer_.id}) if (p) glDeleteProgram(p);
    if (quad_) glDeleteBuffers(1, &quad_);
}

void StereoPass::drop(Source& s) {
    for (const GLuint t : {s.raw, s.raw_half, s.text, s.guide[0], s.guide[1]}) if (t) glDeleteTextures(1, &t);
    for (const GLuint f : {s.fb[0], s.fb[1], s.text_fb}) if (f) glDeleteFramebuffers(1, &f);
    s = {};
}

void StereoPass::resize_sources(std::size_t n) {
    while (sources_.size() > n) { drop(sources_.back()); sources_.pop_back(); }
    sources_.resize(n);
}
void StereoPass::insert_source(std::size_t at) { sources_.insert(sources_.begin() + std::ptrdiff_t(std::min(at, sources_.size())), Source{}); }
void StereoPass::remove_source(std::size_t at) {
    if (at >= sources_.size()) return;
    drop(sources_[at]); sources_.erase(sources_.begin() + std::ptrdiff_t(at));
}

bool StereoPass::storage(Source& s, int w, int h) {
    if (s.w == w && s.h == h) return true;
    drop(s);
    s.raw = texture(GL_R32F, GL_RED, GL_FLOAT, w, h, GL_NEAREST);
    s.text = texture(GL_R8, GL_RED, GL_UNSIGNED_BYTE, w, h, GL_NEAREST);
    s.text_fb = framebuffer(s.text);
    for (int i = 0; i < 2; ++i) {
        s.guide[i] = texture(GL_R16F, GL_RED, GL_HALF_FLOAT, w, h, GL_LINEAR);
        s.fb[i] = framebuffer(s.guide[i]);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { drop(s); return false; }
    }
    s.w = w; s.h = h;
    return true;
}

bool StereoPass::update(std::size_t source, const DepthInput& f, GLuint colour, std::uint32_t colour_w, const StereoParams& params, float aspect, float dt_ms) {
    if (source >= sources_.size() || !depth_input_valid(f) || !colour || !colour_w) return false;
    GLint frame = 0, viewport[4] = {}; glGetIntegerv(GL_FRAMEBUFFER_BINDING, &frame); glGetIntegerv(GL_VIEWPORT, viewport);
    auto& s = sources_[source];
    const bool had = s.w == f.w && s.h == f.h && s.current >= 0;
    if (!storage(s, f.w, f.h)) { glBindFramebuffer(GL_FRAMEBUFFER, GLuint(frame)); return false; }
    const GLboolean blended = glIsEnabled(GL_BLEND), scissored = glIsEnabled(GL_SCISSOR_TEST);
    glDisable(GL_BLEND); glDisable(GL_SCISSOR_TEST);
    glViewport(0, 0, f.w, f.h);
    glBindBuffer(GL_ARRAY_BUFFER, quad_);
    const auto quad = [](GLint pos) {
        glVertexAttribPointer(GLuint(pos), 2, GL_FLOAT, GL_FALSE, 0, nullptr);
        glEnableVertexAttribArray(GLuint(pos));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        glDisableVertexAttribArray(GLuint(pos));
    };
    // The small passes: the mask, then the guide into the other of the two textures, the current one being its history.
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, colour);
    glBindFramebuffer(GL_FRAMEBUFFER, s.text_fb);
    glUseProgram(text_.id);
    glUniform2f(text_.step, std::max(1.f / float(colour_w), 1.f / (float(f.w) * kStereoTextCols)), 1.f / (float(f.h) * kStereoTextRows));
    quad(text_.pos);
    GLuint raw = f.texture;
    if (f.pixels) {
        glBindTexture(GL_TEXTURE_2D, s.raw);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glPixelStorei(GL_UNPACK_ROW_LENGTH, f.stride ? GLint(f.stride / sizeof(float)) : 0);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, f.w, f.h, GL_RED, GL_FLOAT, f.pixels);
        glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
        raw = s.raw;
    } else if (f.half) {  // R32F takes GL_FLOAT only, so half floats get their own texture, made on first use
        if (!s.raw_half) s.raw_half = texture(GL_R16F, GL_RED, GL_HALF_FLOAT, f.w, f.h, GL_NEAREST);
        glBindTexture(GL_TEXTURE_2D, s.raw_half);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 2);
        glPixelStorei(GL_UNPACK_ROW_LENGTH, f.stride ? GLint(f.stride / sizeof(std::uint16_t)) : 0);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, f.w, f.h, GL_RED, GL_HALF_FLOAT, f.half);
        glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        raw = s.raw_half;
    }
    const int next = s.current == 0 ? 1 : 0;
    const auto p = stereo_clamped(params);
    glBindFramebuffer(GL_FRAMEBUFFER, s.fb[next]);
    glUseProgram(guide_.id);
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, raw);
    glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, s.text);
    glActiveTexture(GL_TEXTURE2); glBindTexture(GL_TEXTURE_2D, had ? s.guide[s.current] : 0);
    glUniform2f(guide_.range, f.lo, f.hi);
    glUniform3f(guide_.params, p.strength, f.convergence >= 0.f ? std::min(f.convergence, 1.f) : p.convergence, p.text_keep);
    glUniform1f(guide_.alpha, stereo_alpha(dt_ms));
    glUniform1f(guide_.aspect, aspect);
    glUniform1f(guide_.history, had && dt_ms > 0 ? 1.f : 0.f);
    quad(guide_.pos);
    s.current = next; s.seq = f.source_seq;
    ++updates_;
    glBindTexture(GL_TEXTURE_2D, 0);
    glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, 0);
    glActiveTexture(GL_TEXTURE0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindFramebuffer(GL_FRAMEBUFFER, GLuint(frame));
    glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
    if (blended) glEnable(GL_BLEND);
    if (scissored) glEnable(GL_SCISSOR_TEST);
    return true;
}

bool StereoPass::ready(std::size_t source) const { return source < sources_.size() && sources_[source].current >= 0; }
GLuint StereoPass::guide(std::size_t source) const { return ready(source) ? sources_[source].guide[sources_[source].current] : 0; }
std::uint64_t StereoPass::seq(std::size_t source) const { return source < sources_.size() ? sources_[source].seq : 0; }

void StereoPass::warp_uniforms(std::size_t source, int eye, const StereoBudget& b) {
    glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, guide(source)); glActiveTexture(GL_TEXTURE0);
    glUniform3f(warp_.budget, stereo_side(eye), b.near_half, b.far_half);
}

void StereoPass::pointer_uniforms(int source, float u, float v, int eye, const StereoBudget& b, float eye_w, float shift_ndc) {
    const bool on = source >= 0 && ready(std::size_t(source));
    glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, on ? guide(std::size_t(source)) : 0); glActiveTexture(GL_TEXTURE0);
    const float k = on && eye_w > 0 ? stereo_side(eye) * 2.f / eye_w : 0.f;
    glUniform2f(pointer_.surface, u, v);
    glUniform3f(pointer_.lift, shift_ndc, k * b.near_half, k * b.far_half);
}
}
