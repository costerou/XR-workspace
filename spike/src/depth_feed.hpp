#pragma once
// Turns a desktop into what the depth engine reads: shrinks it on the GPU and reads it back without stalling.
//
// A desktop is a 1920x1200 dmabuf, usually tiled, which only the GPU can read; the engine wants about 518x322. Shrinking
// where the texture already lives leaves 0.5 MB per frame for shared memory, so the copy costs next to nothing. begin()
// queues the work and returns at once; collect() returns the pixels once the GPU is done and never waits for it, which
// is what lets this run inside a 120 Hz render loop. Needs a current GLES 3 context, like the rest of gl.cpp.
#include <EGL/egl.h>
#include <GLES3/gl32.h>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace xr {
struct DepthDmabuf { int fd = -1; int w = 0, h = 0; std::uint32_t fourcc = 0, stride = 0, offset = 0; std::uint64_t modifier = 0; };

class DepthFeed {
public:
    DepthFeed() = default;
    ~DepthFeed();
    DepthFeed(const DepthFeed&) = delete;
    DepthFeed& operator=(const DepthFeed&) = delete;
    // Shrink to w x h. False if the GL objects could not be made (GL errors are left for the caller to report).
    bool init(int w, int h);
    // Queue one shrink of a GL_TEXTURE_2D, the kind gl.cpp keeps for every desktop (an imported dmabuf is bound to that target
    // there too). False while the previous one is still running.
    bool begin_texture(GLuint texture);
    // The same for a GL_TEXTURE_EXTERNAL_OES texture.
    bool begin_external(GLuint texture);
    // The same for a dmabuf this code has to import itself.
    bool begin_dmabuf(EGLDisplay display, const DepthDmabuf& buffer);
    // True once, when the GPU has finished: RGBA8, tightly packed, row 0 is the frame's top.
    bool collect(std::vector<std::byte>& rgba);
    bool busy() const { return fence_ != nullptr; }
private:
    bool draw(GLuint texture, GLenum target);
    int w_ = 0, h_ = 0;
    GLuint program_ = 0, program_2d_ = 0, framebuffer_ = 0, target_ = 0, vao_ = 0, pbo_ = 0, imported_ = 0;
    GLint step_ = -1, step_2d_ = -1;
    GLsync fence_ = nullptr;
};
}
