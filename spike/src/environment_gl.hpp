#pragma once
// The backgrounds' GL pass (xr/environment.hpp): one fullscreen triangle whose fragment shader ray-casts the view
// direction (the inverse view-projection, interpolated from the triangle's corners) into a procedural scene. No
// textures, no extra framebuffers: the cost is one shaded pixel per output pixel. GLES2 (+ OES_standard_derivatives).
#include "xr/environment.hpp"
#include <GLES2/gl2.h>
#include <array>
#include <memory>

namespace xr {
class EnvironmentPass {
public:
    // Context current. nullptr if the shaders do not compile on this GL (the renderer then never draws a background).
    static std::unique_ptr<EnvironmentPass> create();
    ~EnvironmentPass();
    EnvironmentPass(const EnvironmentPass&) = delete;
    EnvironmentPass& operator=(const EnvironmentPass&) = delete;
    // `background` over every pixel of the current viewport, opaque (no clear needed before it, no blending). `view`: world ->
    // eye, row-major (environment_view); half_w / half_h: tan of the half FOV of this viewport; `level` 0..1 the fade;
    // `gain` the brightness (1 = 100%); `line` the line colour (environment_line_color); `seconds` the animation clock,
    // 0..kEnvironmentLoopS. Background::none draws nothing. Leaves no vertex attribute array enabled and no buffer bound; the
    // program it used stays current (program()).
    void draw(Background background, const Mat3& view, float half_w, float half_h, float level, float gain, const MdRgb& line, float seconds);
    GLuint program(Background background) const { return programs_[std::size_t(background)].id; }
private:
    EnvironmentPass() = default;
    struct Program { GLuint id = 0; GLint pos = -1, view = -1, half_fov = -1, line = -1, level = -1, gain = -1, seconds = -1; };
    std::array<Program, kBackgroundCount> programs_{};  // one per background; None has none
    GLuint buffer_ = 0;
};
}
