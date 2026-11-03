#include "video_gl.hpp"
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace xr {
bool video_filter_parse(const char* name, VideoFilter& out) {
    if (!name) return false;
    if (!std::strcmp(name, "catmull-rom")) out = VideoFilter::catmull_rom;
    else if (!std::strcmp(name, "sharp")) out = VideoFilter::sharp;
    else if (!std::strcmp(name, "bilinear") || !std::strcmp(name, "linear")) out = VideoFilter::bilinear;
    else if (!std::strcmp(name, "nearest")) out = VideoFilter::nearest;
    else return false;
    return true;
}
VideoFilter video_filter_from(const char* name) {
    VideoFilter f = VideoFilter::catmull_rom;
    video_filter_parse(name, f);
    return f;
}
const char* video_filter_name(VideoFilter f) {
    switch (f) {
    case VideoFilter::sharp: return "sharp";
    case VideoFilter::bilinear: return "bilinear";
    case VideoFilter::nearest: return "nearest";
    default: return "catmull-rom";
    }
}
float video_filter_cubic_a(VideoFilter f) { return f == VideoFilter::catmull_rom ? -0.5f : f == VideoFilter::sharp ? -0.75f : 0.f; }

std::uint32_t video_texture_levels(std::uint32_t w, std::uint32_t h) {
    std::uint32_t side = std::max(w, h), levels = 0;
    while (side) { ++levels; side >>= 1; }
    return levels;
}

float video_texture_filtering() {
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    const char* ext = reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
    if (!ext || !std::strstr(ext, "GL_EXT_texture_filter_anisotropic")) return 1.f;
    GLfloat most = 1.f; glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT, &most);
    const float used = std::clamp(most, 1.f, 16.f);
    glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT, used);
    return used;
}

namespace {
const char* kVertex = "#version 300 es\nin vec2 pos; out vec2 ndc; void main() { ndc = pos; gl_Position = vec4(pos, 0.0, 1.0); }\n";
// Mirrors video.cpp video_uv. `view` is uploaded column-major from the row-major world -> eye matrix, so `e * view` is
// viewᵀ·e (eye -> world). highp: 8K frames need sub-texel angular precision.
// Sampling: magnified areas (most of the view) use a Keys cubic (`cubic` = a; 0 = plain bilinear) in 5 bilinear taps of
// mip 0, clamped half a texel inside the eye's rectangle so nothing of the other eye or the frame edge bleeds in.
// Minified areas use trilinear + anisotropic mipmaps with explicit gradients: at the 360° seam u jumps 1 -> 0 between
// neighbouring pixels, and implicit derivatives would pick the smallest mip there (a visible line), so each gradient
// is taken from whichever of u and the half-turned u is continuous at that pixel.
const char* kFragment =
    "#version 300 es\nprecision highp float;\n"
    "in vec2 ndc; out vec4 color; uniform highp sampler2D tex; uniform mat3 view; uniform vec2 half_fov; uniform float projection;\n"
    "uniform vec4 rect; uniform float fov; uniform float flip; uniform float cubic; uniform float behind;\n"
    "const float PI = 3.14159265358979;\n"
    "vec3 keys(vec2 t, vec2 size, vec2 lo, vec2 hi) {\n"
    "  vec2 p = t * size - 0.5; vec2 i = floor(p); vec2 f = p - i; float a = cubic;\n"
    "  vec2 w0 = a * f * (1.0 - f) * (1.0 - f), w3 = a * f * f * (1.0 - f);\n"
    "  vec2 w1 = (a + 2.0) * f * f * f - (a + 3.0) * f * f + 1.0, w2 = 1.0 - w0 - w1 - w3;\n"
    "  vec2 w12 = w1 + w2;\n"
    "  vec2 t0 = clamp((i - 0.5) / size, lo, hi), t3 = clamp((i + 2.5) / size, lo, hi);\n"
    "  vec2 t12 = clamp((i + 0.5 + w2 / w12) / size, lo, hi);\n"
    "  vec3 c = textureLod(tex, vec2(t12.x, t0.y), 0.0).rgb * (w12.x * w0.y) + textureLod(tex, vec2(t0.x, t12.y), 0.0).rgb * (w0.x * w12.y)\n"
    "         + textureLod(tex, t12, 0.0).rgb * (w12.x * w12.y)\n"
    "         + textureLod(tex, vec2(t3.x, t12.y), 0.0).rgb * (w3.x * w12.y) + textureLod(tex, vec2(t12.x, t3.y), 0.0).rgb * (w12.x * w3.y);\n"
    "  float w = w12.x * w0.y + w0.x * w12.y + w12.x * w12.y + w3.x * w12.y + w12.x * w3.y;\n"
    "  return clamp(c / w, 0.0, 1.0);\n"
    "}\n"
    "void main() {\n"
    "  vec3 d = normalize(vec3(ndc.x * half_fov.x, ndc.y * half_fov.y, -1.0) * view);\n"
    "  vec2 uv; bool inside = true;\n"
    "  if (projection > 1.5) {\n"
    "    float r = acos(clamp(-d.z, -1.0, 1.0)) / (0.5 * fov);\n"
    "    float l = length(d.xy); vec2 c = l > 1e-7 ? d.xy / l : vec2(0.0);\n"
    "    uv = vec2(0.5 + 0.5 * r * c.x, 0.5 - 0.5 * r * c.y); inside = r <= 1.0;\n"
    "  } else {\n"
    "    float lon = atan(d.x, -d.z); float lat = asin(clamp(d.y, -1.0, 1.0));\n"
    "    if (projection > 0.5) { uv = vec2(0.5 + lon / PI, 0.5 - lat / PI); inside = abs(lon) <= 0.5 * PI + 1e-6; }\n"
    "    else uv = vec2(fract(0.5 + lon / (2.0 * PI)), 0.5 - lat / PI);\n"
    "  }\n"
    "  vec2 t = rect.xy + clamp(uv, 0.0, 1.0) * rect.zw;\n"
    "  if (flip > 0.5) t.y = 1.0 - t.y;\n"
    "  float turned = rect.x + fract(clamp(uv.x, 0.0, 1.0) + 0.5) * rect.z;\n"
    "  vec2 dx = dFdx(t), dy = dFdy(t); float tx = dFdx(turned), ty = dFdy(turned);\n"
    "  if (projection < 0.5) { if (abs(tx) < abs(dx.x)) dx.x = tx; if (abs(ty) < abs(dy.x)) dy.x = ty; }\n"
    "  vec2 size = vec2(textureSize(tex, 0));\n"
    "  float texels = max(length(dx * size), length(dy * size));\n"
    "  vec3 c = textureGrad(tex, t, dx, dy).rgb;\n"
    "  if (cubic < 0.0 && texels < 1.0) {\n"
    "    vec2 a0 = rect.xy, a1 = rect.xy + rect.zw;\n"
    "    if (flip > 0.5) { a0.y = 1.0 - (rect.y + rect.w); a1.y = 1.0 - rect.y; }\n"
    "    c = mix(keys(t, size, a0 + 0.5 / size, a1 - 0.5 / size), c, smoothstep(0.75, 1.0, texels));\n"
    "  }\n"
    "  if (!inside && behind > 0.5) discard;\n"  // the background drawn before this pass shows there
    "  color = inside ? vec4(c, 1.0) : vec4(0.0, 0.0, 0.0, 1.0);\n"
    "}\n";
GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint ok = 0; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        if (std::getenv("XR_VIDEO_DEBUG")) { char log[2048] = {}; glGetShaderInfoLog(shader, sizeof log, nullptr, log); std::fputs(log, stderr); }
        glDeleteShader(shader); return 0;
    }
    return shader;
}
}

std::unique_ptr<VideoSphere> VideoSphere::create(std::string& why) {
    std::unique_ptr<VideoSphere> out(new VideoSphere());
    GLuint vs = compile(GL_VERTEX_SHADER, kVertex), fs = compile(GL_FRAGMENT_SHADER, kFragment);
    if (!vs || !fs) { if (vs) glDeleteShader(vs); if (fs) glDeleteShader(fs); why = "video shader compile"; return nullptr; }
    out->program_ = glCreateProgram(); glAttachShader(out->program_, vs); glAttachShader(out->program_, fs); glLinkProgram(out->program_);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint linked = 0; glGetProgramiv(out->program_, GL_LINK_STATUS, &linked);
    if (!linked) { why = "video shader link"; return nullptr; }
    const GLuint p = out->program_;
    out->pos_ = glGetAttribLocation(p, "pos"); out->tex_ = glGetUniformLocation(p, "tex"); out->view_ = glGetUniformLocation(p, "view");
    out->half_ = glGetUniformLocation(p, "half_fov"); out->projection_ = glGetUniformLocation(p, "projection");
    out->rect_ = glGetUniformLocation(p, "rect"); out->fov_ = glGetUniformLocation(p, "fov"); out->flip_ = glGetUniformLocation(p, "flip");
    out->cubic_ = glGetUniformLocation(p, "cubic"); out->behind_ = glGetUniformLocation(p, "behind");
    out->forced_ = video_filter_parse(std::getenv("XR_VIDEO_FILTER"), out->filter_);
    return out;
}

VideoSphere::~VideoSphere() { if (program_) glDeleteProgram(program_); }

void VideoSphere::draw(unsigned texture, bool flip_y, const VideoFormat& format, int eye, const Mat3& view, float half_w, float half_h, bool behind) {
    const Mat3 column_major = transpose(view);
    const auto r = eye_rect(format.stereo, eye);
    const float fov = float(format.fov_deg < kFisheyeFovMinDeg ? kFisheyeFovMinDeg : format.fov_deg > kFisheyeFovMaxDeg ? kFisheyeFovMaxDeg
                                                                                                                         : format.fov_deg) * 3.14159265f / 180.f;
    static const GLfloat quad[] = {-1, -1, 1, -1, -1, 1, 1, 1};
    glDisable(GL_BLEND);
    glUseProgram(program_);
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, texture);
    glUniform1i(tex_, 0);
    glUniformMatrix3fv(view_, 1, GL_FALSE, column_major.data());
    glUniform2f(half_, half_w, half_h);
    glUniform1f(projection_, float(static_cast<int>(format.projection)));
    glUniform4f(rect_, r.x, r.y, r.w, r.h);
    glUniform1f(fov_, fov);
    glUniform1f(flip_, flip_y ? 1.f : 0.f);
    glUniform1f(cubic_, video_filter_cubic_a(filter_));
    glUniform1f(behind_, behind ? 1.f : 0.f);
    glVertexAttribPointer(GLuint(pos_), 2, GL_FLOAT, GL_FALSE, 0, quad);
    glEnableVertexAttribArray(GLuint(pos_));
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glDisableVertexAttribArray(GLuint(pos_));
}
}
