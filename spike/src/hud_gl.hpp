#pragma once
// GLES2 drawing for the status pop-up card (xr/hud.hpp). Needs the compositor's EGL context
// current for create(), every call, and destruction. Head-locked: no head rotation is applied.
#include "xr/hud.hpp"
#include <cstdint>
#include <memory>

namespace xr {
class GlHud {
public:
    static std::unique_ptr<GlHud> create();  // nullptr if the shaders fail; the pop-up is then skipped
    ~GlHud();
    // Draw `image` (re-uploaded when `generation` changes) centred horizontally near the bottom
    // (or, with `centered`, in the middle) of a `width` x `height` viewport at `alpha`, moved
    // down by `drop_px` and shifted by whole pixels (`dx` right, `dy` up; the pixel orbit), blended over what is drawn.
    void draw(const HudImage& image, std::uint64_t generation, float alpha, std::uint32_t width, std::uint32_t height,
              bool centered = false, float drop_px = 0, int dx = 0, int dy = 0);
private:
    GlHud() = default;
    unsigned program_ = 0, texture_ = 0;
    int pos_ = -1, uv_ = -1, sampler_ = -1, alpha_ = -1;
    std::uint64_t uploaded_ = 0;
    std::uint32_t tex_w_ = 0, tex_h_ = 0;
};
}
