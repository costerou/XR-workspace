#include "hud_gl.hpp"
#include <GLES2/gl2.h>
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
const char* kHudVertex = "attribute vec2 pos; attribute vec2 uv; varying vec2 v; void main() { v = uv; gl_Position = vec4(pos, 0.0, 1.0); }\n";
const char* kHudFragment =  // premultiplied card, uniformly faded
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha;\n"
    "void main() { gl_FragColor = texture2D(tex, v) * alpha; }\n";
GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint ok = 0; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) { glDeleteShader(shader); return 0; }
    return shader;
}
// The card's bottom edge sits 10% above the bottom of the view.
constexpr float kBottomFraction = 0.10f;
}

std::unique_ptr<GlHud> GlHud::create() {
    std::unique_ptr<GlHud> out(new GlHud());
    GLuint vs = compile(GL_VERTEX_SHADER, kHudVertex), fs = compile(GL_FRAGMENT_SHADER, kHudFragment);
    if (!vs || !fs) { if (vs) glDeleteShader(vs); if (fs) glDeleteShader(fs); return nullptr; }
    out->program_ = glCreateProgram(); glAttachShader(out->program_, vs); glAttachShader(out->program_, fs); glLinkProgram(out->program_);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint linked = 0; glGetProgramiv(out->program_, GL_LINK_STATUS, &linked);
    if (!linked) return nullptr;
    out->pos_ = glGetAttribLocation(out->program_, "pos"); out->uv_ = glGetAttribLocation(out->program_, "uv");
    out->sampler_ = glGetUniformLocation(out->program_, "tex"); out->alpha_ = glGetUniformLocation(out->program_, "alpha");
    GLuint tex = 0; glGenTextures(1, &tex); out->texture_ = tex;
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    if (glGetError() != GL_NO_ERROR) return nullptr;
    return out;
}

GlHud::~GlHud() {
    if (texture_) { GLuint t = texture_; glDeleteTextures(1, &t); }
    if (program_) glDeleteProgram(program_);
}

void GlHud::draw(const HudImage& image, std::uint64_t generation, float alpha, std::uint32_t width, std::uint32_t height,
                 bool centered, float drop_px) {
    if (alpha <= 0.f || !image.width || !image.height || !width || !height) return;
    if (image.rgba.size() != std::size_t(image.width) * image.height * 4) return;
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, texture_);
    if (generation != uploaded_) {
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, GLsizei(image.width), GLsizei(image.height), 0, GL_RGBA, GL_UNSIGNED_BYTE, image.rgba.data());
        uploaded_ = generation; tex_w_ = image.width; tex_h_ = image.height;
    }
    const float w = float(tex_w_), h = float(tex_h_);  // 1:1: the card is rasterised at output size
    const float x0 = std::floor((float(width) - w) / 2);
    const float y_bottom = std::floor((centered ? (float(height) - h) / 2 : float(height) * kBottomFraction) - drop_px);
    // Pixel rect -> NDC (y up). Whole-pixel placement keeps the pixel font crisp.
    const float l = 2 * x0 / float(width) - 1, r = 2 * (x0 + w) / float(width) - 1;
    const float b = 2 * y_bottom / float(height) - 1, t = 2 * (y_bottom + h) / float(height) - 1;
    const GLfloat positions[] = {l, t, r, t, l, b, r, b};
    const GLfloat uvs[] = {0, 0, 1, 0, 0, 1, 1, 1};  // row 0 of the card is its top
    glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    glUseProgram(program_); glUniform1i(sampler_, 0); glUniform1f(alpha_, std::min(alpha, 1.f));
    glVertexAttribPointer(GLuint(pos_), 2, GL_FLOAT, GL_FALSE, 0, positions);
    glVertexAttribPointer(GLuint(uv_), 2, GL_FLOAT, GL_FALSE, 0, uvs);
    glEnableVertexAttribArray(GLuint(pos_)); glEnableVertexAttribArray(GLuint(uv_));
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glDisable(GL_BLEND);
}
}
