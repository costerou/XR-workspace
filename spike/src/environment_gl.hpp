#pragma once
// The TRON grid's GL pass (xr/environment.hpp): one fullscreen triangle whose fragment shader ray-casts the view
// direction (the inverse view-projection, interpolated from the triangle's corners) into a procedural scene. No
// textures, no extra framebuffers: the cost is one shaded pixel per output pixel. GLES2 (+ OES_standard_derivatives).
#include "xr/environment.hpp"
#include <GLES2/gl2.h>
#include <memory>

namespace xr {
class EnvironmentPass {
public:
    // Context current. nullptr if the shaders do not compile on this GL (the renderer then never draws the grid).
    static std::unique_ptr<EnvironmentPass> create();
    ~EnvironmentPass();
    EnvironmentPass(const EnvironmentPass&) = delete;
    EnvironmentPass& operator=(const EnvironmentPass&) = delete;
    // Every pixel of the current viewport, opaque (no clear needed before it, no blending). `view`: world -> eye,
    // row-major (environment_view); half_w / half_h: tan of the half FOV, as the desktops use; `level` 0..1 the fade;
    // `line` the grid colour (under kEnvironmentCap); `seconds` the animation clock, 0..kEnvironmentLoopS. Leaves no
    // vertex attribute array enabled and no buffer bound; the program it used stays current.
    void draw(const Mat3& view, float half_w, float half_h, float level, const MdRgb& line, float seconds);
    GLuint program() const { return program_; }
private:
    EnvironmentPass() = default;
    GLuint program_ = 0, buffer_ = 0;
    GLint pos_ = -1, view_ = -1, half_fov_ = -1, line_ = -1, level_ = -1, seconds_ = -1, cap_ = -1;
};
const char* environment_fragment_shader();  // the scene (tests compile it on their own)
}
