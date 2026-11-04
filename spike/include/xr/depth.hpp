#pragma once
// The depth engine -> renderer contract of global immersive 3D (docs/stereo-render.md): one depth map per desktop. The
// engine is a separate GPU process or an in-renderer compute stage; the renderer only sees DepthFrame.
#include <cstddef>
#include <cstdint>

namespace xr {
// A relative depth map of one desktop's capture, at least 256 px tall, at any aspect ratio. Values are "nearness": larger is
// nearer (the output of a monocular depth network is inverse depth, which already is). `lo` / `hi` are the values of the
// farthest and nearest thing in this frame, so a network's arbitrary scale needs no conversion here. NaN and infinity are
// allowed and are drawn flat. Exactly one of `pixels` and `texture` is set:
//   pixels   shared memory: `h` rows of `w` floats (R32F), top row first, `stride` bytes apart (0: tightly packed). Read
//            only during StereoPass::update / GlCompositor::set_depth.
//   texture  a GL texture name in the renderer's context (an imported dmabuf, R16F or R32F), row 0 the top row.
struct DepthFrame {
    int w = 0, h = 0;
    std::uint64_t source_seq = 0;      // the capture frame this was estimated from
    const float* pixels = nullptr;
    std::size_t stride = 0;
    unsigned texture = 0;
    float lo = 0.f, hi = 1.f;
};
inline constexpr int kDepthMinHeight = 256;
inline bool depth_frame_valid(const DepthFrame& f) {
    if (f.w <= 0 || f.h < kDepthMinHeight || (!f.pixels && !f.texture)) return false;
    return !f.pixels || !f.stride || f.stride >= std::size_t(f.w) * sizeof(float);
}
}
