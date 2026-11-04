#include "depth_feed.hpp"
#include <EGL/eglext.h>
#include <GLES2/gl2ext.h>
#include <cstring>

namespace xr {
namespace {
const char* kVertex = R"(#version 300 es
out vec2 uv;
void main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); uv = p; gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); })";
// Four by four taps across each output pixel: a 3.7x shrink with one bilinear tap would drop thin text and shimmer.
// uv.y grows upwards in the target and glReadPixels reads bottom first, so the source's top comes out as row 0.
const char* kFragment = R"(#version 300 es
#extension GL_OES_EGL_image_external_essl3 : require
precision mediump float;
uniform samplerExternalOES source;
uniform vec2 step_uv;
in vec2 uv;
out vec4 colour;
void main() {
    vec3 sum = vec3(0.0);
    for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x)
        sum += texture(source, uv + (vec2(x, y) - 1.5) * 0.25 * step_uv).rgb;
    colour = vec4(sum / 16.0, 1.0);
})";
GLuint compile(GLenum kind, const char* text) {
    GLuint s = glCreateShader(kind);
    glShaderSource(s, 1, &text, nullptr);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { glDeleteShader(s); return 0; }
    return s;
}
}

DepthFeed::~DepthFeed() {
    if (fence_) glDeleteSync(fence_);
    if (imported_) glDeleteTextures(1, &imported_);
    if (pbo_) glDeleteBuffers(1, &pbo_);
    if (target_) glDeleteTextures(1, &target_);
    if (framebuffer_) glDeleteFramebuffers(1, &framebuffer_);
    if (vao_) glDeleteVertexArrays(1, &vao_);
    if (program_) glDeleteProgram(program_);
}

bool DepthFeed::init(int w, int h) {
    w_ = w; h_ = h;
    const GLuint vs = compile(GL_VERTEX_SHADER, kVertex), fs = compile(GL_FRAGMENT_SHADER, kFragment);
    if (!vs || !fs) return false;
    program_ = glCreateProgram();
    glAttachShader(program_, vs);
    glAttachShader(program_, fs);
    glLinkProgram(program_);
    glDeleteShader(vs);
    glDeleteShader(fs);
    GLint ok = 0;
    glGetProgramiv(program_, GL_LINK_STATUS, &ok);
    if (!ok) return false;
    step_ = glGetUniformLocation(program_, "step_uv");
    glGenTextures(1, &target_);
    glBindTexture(GL_TEXTURE_2D, target_);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, w, h);
    glBindTexture(GL_TEXTURE_2D, 0);
    glGenFramebuffers(1, &framebuffer_);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer_);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, target_, 0);
    const bool complete = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glGenVertexArrays(1, &vao_);
    glGenBuffers(1, &pbo_);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo_);
    glBufferData(GL_PIXEL_PACK_BUFFER, static_cast<GLsizeiptr>(w) * h * 4, nullptr, GL_STREAM_READ);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    return complete;
}

bool DepthFeed::draw(GLuint texture) {
    GLint previous_fb = 0, viewport[4]{};
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &previous_fb);
    glGetIntegerv(GL_VIEWPORT, viewport);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer_);
    glViewport(0, 0, w_, h_);
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glUseProgram(program_);
    glUniform2f(step_, 1.0f / static_cast<float>(w_), 1.0f / static_cast<float>(h_));
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, texture);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glBindVertexArray(vao_);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glBindVertexArray(0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo_);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, w_, h_, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);  // Into the buffer: this does not wait.
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    fence_ = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
    glFlush();
    glBindFramebuffer(GL_FRAMEBUFFER, static_cast<GLuint>(previous_fb));
    glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
    return fence_ != nullptr;
}

bool DepthFeed::begin_external(GLuint texture) {
    if (fence_) return false;
    return draw(texture);
}

bool DepthFeed::begin_dmabuf(EGLDisplay display, const DepthDmabuf& b) {
    if (fence_) return false;
    static const auto create = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
    static const auto destroy = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
    static const auto bind = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(eglGetProcAddress("glEGLImageTargetTexture2DOES"));
    if (!create || !destroy || !bind) return false;
    const EGLint attrs[] = {EGL_WIDTH, b.w, EGL_HEIGHT, b.h, EGL_LINUX_DRM_FOURCC_EXT, static_cast<EGLint>(b.fourcc),
        EGL_DMA_BUF_PLANE0_FD_EXT, b.fd, EGL_DMA_BUF_PLANE0_OFFSET_EXT, static_cast<EGLint>(b.offset), EGL_DMA_BUF_PLANE0_PITCH_EXT, static_cast<EGLint>(b.stride),
        EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, static_cast<EGLint>(b.modifier & 0xffffffffu), EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, static_cast<EGLint>(b.modifier >> 32), EGL_NONE};
    EGLImageKHR image = create(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr, attrs);
    if (image == EGL_NO_IMAGE_KHR) return false;
    if (!imported_) glGenTextures(1, &imported_);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, imported_);
    bind(GL_TEXTURE_EXTERNAL_OES, image);
    const bool ok = glGetError() == GL_NO_ERROR && draw(imported_);
    destroy(display, image);  // The texture keeps the storage alive.
    return ok;
}

bool DepthFeed::collect(std::vector<std::byte>& rgba) {
    if (!fence_) return false;
    GLint status = GL_UNSIGNALED;
    glGetSynciv(fence_, GL_SYNC_STATUS, 1, nullptr, &status);
    if (status != GL_SIGNALED) return false;
    glDeleteSync(fence_);
    fence_ = nullptr;
    const std::size_t bytes = static_cast<std::size_t>(w_) * h_ * 4;
    rgba.resize(bytes);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo_);
    const void* p = glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, static_cast<GLsizeiptr>(bytes), GL_MAP_READ_BIT);
    if (p) std::memcpy(rgba.data(), p, bytes);
    glUnmapBuffer(GL_PIXEL_PACK_BUFFER);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    return p != nullptr;
}
}
