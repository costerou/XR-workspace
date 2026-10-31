#pragma once
// GPU composition of captured managed outputs onto the presentation surface (milestone C2a).
// Frame pacing stays with the caller's frame callbacks and presentation feedback; this class
// only renders and swaps. No readback: pixels never return to the CPU.
#include "xr/capture.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct wl_display;
struct wl_surface;

namespace xr {
class GlCompositor {
public:
    // Returns nullptr (with `why`) if EGL/GLES are unavailable; the caller reports the fallback.
    static std::unique_ptr<GlCompositor> create(wl_display*, wl_surface*, std::uint32_t width, std::uint32_t height,
                                                std::size_t sources, std::string& why);
    ~GlCompositor();
    void resize(std::uint32_t width, std::uint32_t height);
    // Upload one source frame (32-bit wl_shm XRGB/ARGB, little-endian B,G,R,X bytes).
    bool upload(std::size_t source, const void* pixels, std::uint32_t width, std::uint32_t height, std::uint32_t stride);
    // Draw each uploaded source into its tile, then eglSwapBuffers (which attaches and commits).
    bool draw_and_swap(const std::vector<Rect>& tiles);
    std::string report() const;  // bounded JSON: renderer strings, timing; no pixel data
private:
    GlCompositor() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
