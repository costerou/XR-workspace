#pragma once
// Global immersive 3D, the GL side (xr/stereo.hpp has the maths, docs/stereo-render.md the design). Per depth frame, per desktop:
// a text mask and the guide map (depth -> disparity, text kept near the screen plane, edges tapered, filtered over time), both
// small. Per displayed frame: one fragment shader on the desktop's own mesh that finds, for each eye pixel, the source pixel that
// lands there (depth-image-based rendering by a march along the eye's horizontal). That keeps the cost to the pixels on screen,
// whatever the desktops' sizes, and resamples the desktop once, so text stays as sharp as in 2D. All calls need the
// compositor's context current. Needs GLES 3 and a float colour buffer; create() fails without, and the desktops then stay flat.
#include "xr/stereo.hpp"
#include <GLES2/gl2.h>
#include <memory>
#include <string>
#include <vector>

// World point -> head rotation -> perspective, for every program that draws in the world. `stereo.x`: this eye's offset,
// converged on the plane z = -1 (stereo_plane_disparity); `stereo.y`: a constant shift in NDC. Both 0 (a uniform's initial
// value) in 2D: exactly the mono projection.
#define XR_PROJECT \
    "uniform mat3 view; uniform vec2 half_fov; uniform vec2 stereo;\n" \
    "vec4 project(vec3 p) { vec3 q = view * p;\n" \
    "  return vec4((q.x + stereo.x * (1.0 + q.z)) / half_fov.x + stereo.y * -q.z, q.y / half_fov.y, 0.0, -q.z); }\n"

namespace xr {
class StereoPass {
public:
    // `tile_pos`, `tile_uv`, `color_pos`: the attribute locations of the compositor's tile and colour programs, which the
    // warp and pointer programs take over, so the two kinds of program swap without touching its vertex arrays.
    static std::unique_ptr<StereoPass> create(GLint tile_pos, GLint tile_uv, GLint color_pos, std::string& why);
    ~StereoPass();
    StereoPass(const StereoPass&) = delete;
    StereoPass& operator=(const StereoPass&) = delete;
    void resize_sources(std::size_t n);
    void insert_source(std::size_t at);
    void remove_source(std::size_t at);
    // A new depth frame of desktop `source`, whose current picture is `colour`. `dt_ms` since the last one (0 or less:
    // a first frame, no history). `colour_w`: its width in pixels. Keeps framebuffer and viewport. False: an invalid frame, nothing changed.
    bool update(std::size_t source, const DepthInput& frame, GLuint colour, std::uint32_t colour_w, const StereoParams& params, float aspect, float dt_ms);
    bool ready(std::size_t source) const;
    std::optional<DepthInput> cached_frame(std::size_t source) const;
    GLuint guide(std::size_t source) const;
    std::uint64_t seq(std::size_t source) const;
    // The tile program in use, its uniforms by name for the compositor's projection cache.
    GLuint warp_program() const { return warp_.id; }
    GLint warp_view() const { return warp_.view; }
    GLint warp_half_fov() const { return warp_.half_fov; }
    GLint warp_stereo() const { return warp_.stereo; }
    GLint warp_swizzle() const { return warp_.swizzle; }
    // This eye's warp of a desktop: its guide on unit 1 (the colour stays on unit 0, which stays active).
    void warp_uniforms(std::size_t source, int eye, const StereoBudget& budget);
    GLuint pointer_program() const { return pointer_.id; }
    GLint pointer_view() const { return pointer_.view; }
    GLint pointer_half_fov() const { return pointer_.half_fov; }
    GLint pointer_stereo() const { return pointer_.stereo; }
    GLint pointer_color() const { return pointer_.color; }
    // Pointer: `shift_ndc` this eye's constant shift; on a desktop with a guide, plus the disparity under it at (u, v).
    void pointer_uniforms(int source, float u, float v, int eye, const StereoBudget& budget, float eye_w, float shift_ndc);
    std::uint64_t updates() const { return updates_; }
private:
    StereoPass() = default;
    struct Source { DepthInput cached; GLuint raw = 0, raw_half = 0, text = 0, guide[2] = {0, 0}, fb[2] = {0, 0}, text_fb = 0; int w = 0, h = 0, current = -1; std::uint64_t seq = 0; };
    void drop(Source& s);
    bool storage(Source& s, int w, int h);
    std::vector<Source> sources_;
    struct { GLuint id = 0; GLint pos = -1, colour = -1, step = -1; } text_;
    struct { GLuint id = 0; GLint pos = -1, raw = -1, text = -1, previous = -1, range = -1, params = -1, alpha = -1, aspect = -1, history = -1; } guide_;
    struct { GLuint id = 0; GLint view = -1, half_fov = -1, stereo = -1, swizzle = -1, tex = -1, guide = -1, budget = -1; } warp_;
    struct { GLuint id = 0; GLint view = -1, half_fov = -1, stereo = -1, color = -1, guide = -1, surface = -1, lift = -1; } pointer_;
    GLuint quad_ = 0;
    std::uint64_t updates_ = 0;
};
}
