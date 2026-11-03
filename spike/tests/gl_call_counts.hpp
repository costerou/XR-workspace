#pragma once
// Test-only GL call counting for gl_bench: the bench compiles its own copies of src/gl.cpp and src/hud_gl.cpp
// (gl_bench_gl.cpp, gl_bench_hud_gl.cpp) with this header first, so every listed call in them goes through a macro
// that bumps a counter and then calls the real function unchanged. The renderer itself is never built this way.
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <cstdint>

struct GlCallCounts {
    std::uint64_t draws = 0, vertices = 0, programs = 0, uniforms = 0, textures = 0, attribs = 0, toggles = 0,
                  uploads = 0, upload_bytes = 0, buffers = 0, buffer_bytes = 0, errors = 0, other = 0;
    std::uint64_t calls() const { return draws + programs + uniforms + textures + attribs + toggles + uploads + buffers + errors + other; }
};
extern GlCallCounts g_gl_calls;

#ifndef XR_GL_COUNT_NO_MACROS  // gl_bench.cpp itself only needs the counters
#define XR_GL_COUNT(field, call) (++g_gl_calls.field, call)
#define glDrawArrays(mode, first, n) (++g_gl_calls.draws, g_gl_calls.vertices += std::uint64_t(n), glDrawArrays(mode, first, n))
#define glDrawElements(mode, n, type, p) (++g_gl_calls.draws, g_gl_calls.vertices += std::uint64_t(n), glDrawElements(mode, n, type, p))
#define glUseProgram(...) XR_GL_COUNT(programs, glUseProgram(__VA_ARGS__))
#define glUniform1f(...) XR_GL_COUNT(uniforms, glUniform1f(__VA_ARGS__))
#define glUniform1i(...) XR_GL_COUNT(uniforms, glUniform1i(__VA_ARGS__))
#define glUniform2f(...) XR_GL_COUNT(uniforms, glUniform2f(__VA_ARGS__))
#define glUniform3f(...) XR_GL_COUNT(uniforms, glUniform3f(__VA_ARGS__))
#define glUniform4f(...) XR_GL_COUNT(uniforms, glUniform4f(__VA_ARGS__))
#define glUniform2fv(...) XR_GL_COUNT(uniforms, glUniform2fv(__VA_ARGS__))
#define glUniform3fv(...) XR_GL_COUNT(uniforms, glUniform3fv(__VA_ARGS__))
#define glUniform4fv(...) XR_GL_COUNT(uniforms, glUniform4fv(__VA_ARGS__))
#define glUniformMatrix3fv(...) XR_GL_COUNT(uniforms, glUniformMatrix3fv(__VA_ARGS__))
#define glBindTexture(...) XR_GL_COUNT(textures, glBindTexture(__VA_ARGS__))
#define glActiveTexture(...) XR_GL_COUNT(textures, glActiveTexture(__VA_ARGS__))
#define glVertexAttribPointer(...) XR_GL_COUNT(attribs, glVertexAttribPointer(__VA_ARGS__))
#define glEnableVertexAttribArray(...) XR_GL_COUNT(attribs, glEnableVertexAttribArray(__VA_ARGS__))
#define glDisableVertexAttribArray(...) XR_GL_COUNT(attribs, glDisableVertexAttribArray(__VA_ARGS__))
#define glEnable(...) XR_GL_COUNT(toggles, glEnable(__VA_ARGS__))
#define glDisable(...) XR_GL_COUNT(toggles, glDisable(__VA_ARGS__))
#define glBlendFunc(...) XR_GL_COUNT(toggles, glBlendFunc(__VA_ARGS__))
#define glTexImage2D(target, level, internal, w, h, border, format, type, pixels) \
    (++g_gl_calls.uploads, g_gl_calls.upload_bytes += std::uint64_t(w) * std::uint64_t(h) * 4u,         \
     glTexImage2D(target, level, internal, w, h, border, format, type, pixels))
#define glTexSubImage2D(target, level, x, y, w, h, format, type, pixels) \
    (++g_gl_calls.uploads, g_gl_calls.upload_bytes += std::uint64_t(w) * std::uint64_t(h) * 4u, \
     glTexSubImage2D(target, level, x, y, w, h, format, type, pixels))
#define glBindBuffer(...) XR_GL_COUNT(buffers, glBindBuffer(__VA_ARGS__))
#define glBufferData(target, size, data, usage) \
    (++g_gl_calls.buffers, g_gl_calls.buffer_bytes += std::uint64_t(size), glBufferData(target, size, data, usage))
#define glBufferSubData(target, offset, size, data) \
    (++g_gl_calls.buffers, g_gl_calls.buffer_bytes += std::uint64_t(size), glBufferSubData(target, offset, size, data))
#define glGetError() XR_GL_COUNT(errors, glGetError())
#define glPixelStorei(...) XR_GL_COUNT(other, glPixelStorei(__VA_ARGS__))
#define glViewport(...) XR_GL_COUNT(other, glViewport(__VA_ARGS__))
#define glClear(...) XR_GL_COUNT(other, glClear(__VA_ARGS__))
#define glClearColor(...) XR_GL_COUNT(other, glClearColor(__VA_ARGS__))
#define glFlush() XR_GL_COUNT(other, glFlush())
#endif
