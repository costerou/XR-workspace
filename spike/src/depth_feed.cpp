#include "depth_feed.hpp"
#include <EGL/eglext.h>
#include <GLES2/gl2ext.h>
#include <cstring>
#include <string>

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
// The same taps, on the GL_TEXTURE_2D gl.cpp keeps for each desktop. Binding that texture to the external target would be a GL
// error, and an external sampler reading it would read nothing.
const std::string kFragment2d = [] {
    std::string s = kFragment;
    const std::string extension = "#extension GL_OES_EGL_image_external_essl3 : require\n", sampler = "samplerExternalOES";
    s.erase(s.find(extension), extension.size());
    s.replace(s.find(sampler), sampler.size(), "sampler2D");
    return s;
}();
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
    if (program_2d_) glDeleteProgram(program_2d_);
}

bool DepthFeed::init(int w, int h) {
    w_ = w; h_ = h;
    const auto link = [](const char* fragment) -> GLuint {
        const GLuint vs = compile(GL_VERTEX_SHADER, kVertex), fs = compile(GL_FRAGMENT_SHADER, fragment);
        if (!vs || !fs) { if (vs) glDeleteShader(vs); if (fs) glDeleteShader(fs); return 0; }
        const GLuint program = glCreateProgram();
        glAttachShader(program, vs);
        glAttachShader(program, fs);
        glLinkProgram(program);
        glDeleteShader(vs);
        glDeleteShader(fs);
        GLint ok = 0;
        glGetProgramiv(program, GL_LINK_STATUS, &ok);
        if (!ok) { glDeleteProgram(program); return 0; }
        return program;
    };
    program_2d_ = link(kFragment2d.c_str());
    if (!program_2d_) return false;
    step_2d_ = glGetUniformLocation(program_2d_, "step_uv");
    program_ = link(kFragment);  // optional: only a dmabuf this code imports itself needs it
    if (program_) step_ = glGetUniformLocation(program_, "step_uv");
    glGenTextures(1, &target_);
    glBindTexture(GL_TEXTURE_2D, target_);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, w, h);
    glBindTexture(GL_TEXTURE_2D, 0);
    GLint previous_fb = 0;  // the caller's, which is not always 0: an off-screen compositor draws into its own
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &previous_fb);
    glGenFramebuffers(1, &framebuffer_);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer_);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, target_, 0);
    const bool complete = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
    glBindFramebuffer(GL_FRAMEBUFFER, static_cast<GLuint>(previous_fb));
    glGenVertexArrays(1, &vao_);
    glGenBuffers(1, &pbo_);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo_);
    glBufferData(GL_PIXEL_PACK_BUFFER, static_cast<GLsizeiptr>(w) * h * 4, nullptr, GL_STREAM_READ);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    return complete;
}

bool DepthFeed::draw(GLuint texture, GLenum target) {
    const bool external = target == GL_TEXTURE_EXTERNAL_OES;
    if (external && !program_) return false;
    GLint previous_fb = 0, viewport[4]{};
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &previous_fb);
    glGetIntegerv(GL_VIEWPORT, viewport);
    const GLboolean blended = glIsEnabled(GL_BLEND), scissored = glIsEnabled(GL_SCISSOR_TEST);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer_);
    glViewport(0, 0, w_, h_);
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glUseProgram(external ? program_ : program_2d_);
    glUniform2f(external ? step_ : step_2d_, 1.0f / static_cast<float>(w_), 1.0f / static_cast<float>(h_));
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(target, texture);
    if (external) {
        glTexParameteri(target, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(target, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    }
    glBindVertexArray(vao_);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glBindVertexArray(0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo_);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, w_, h_, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);  // Into the buffer: this does not wait.
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    fence_ = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
    glFlush();
    glBindTexture(target, 0);
    glBindFramebuffer(GL_FRAMEBUFFER, static_cast<GLuint>(previous_fb));
    glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
    if (blended) glEnable(GL_BLEND);
    if (scissored) glEnable(GL_SCISSOR_TEST);
    return fence_ != nullptr;
}

bool DepthFeed::begin_texture(GLuint texture) {
    if (fence_) return false;
    return draw(texture, GL_TEXTURE_2D);
}

bool DepthFeed::begin_external(GLuint texture) {
    if (fence_) return false;
    return draw(texture, GL_TEXTURE_EXTERNAL_OES);
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
    const bool ok = glGetError() == GL_NO_ERROR && draw(imported_, GL_TEXTURE_EXTERNAL_OES);
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
