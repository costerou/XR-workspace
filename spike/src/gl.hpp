#pragma once
// GPU composition of captured managed outputs onto the presentation surface (milestone C2a).
// Frame pacing stays with the caller's frame callbacks and presentation feedback; this class
// only renders and swaps. No readback: pixels never return to the CPU.
#include "xr/capture.hpp"
#include "xr/view.hpp"
#include "xr/pose.hpp"
#include "xr/hud.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct wl_display;
struct wl_surface;

namespace xr {
struct DmabufBuffer;
class GlCompositor {
public:
    // Returns nullptr (with `why`) if EGL/GLES are unavailable; the caller reports the fallback.
    static std::unique_ptr<GlCompositor> create(wl_display*, wl_surface*, std::uint32_t width, std::uint32_t height,
                                                std::size_t sources, std::string& why);
    ~GlCompositor();
    void resize(std::uint32_t width, std::uint32_t height);
    // Upload one source frame (32-bit wl_shm XRGB/ARGB, little-endian B,G,R,X bytes).
    bool upload(std::size_t source, const void* pixels, std::uint32_t width, std::uint32_t height, std::uint32_t stride);
    // Draw each uploaded source into its (possibly off-screen) rect, placed in space by `placements[i]`
    // (view.hpp; missing entries are flat), back to front, with an optional highlight frame behind
    // `highlight` with `highlight_alpha`, then eglSwapBuffers (which attaches and commits).
    // `view` rotates the world-anchored canvas (identity = head-locked); half_w/h = tan(half FOV).
    bool draw_and_swap(const std::vector<FRect>& tiles, const std::vector<Placement>& placements, int highlight,
                       float highlight_alpha, const Mat3& view, float half_w, float half_h);
    // Zero-copy (C2b): can EGL import this fourcc/modifier; import a capture slot's buffer once;
    // mark slot as the source's current frame (sampled without B/R swap); drop on reallocation.
    bool dmabuf_importable(std::uint32_t fourcc, std::uint64_t modifier) const;
    bool import_dmabuf(std::size_t source, std::size_t slot, const DmabufBuffer&);
    void present_dmabuf(std::size_t source, int slot);
    void drop_dmabuf(std::size_t source);
    // Status pop-up drawn head-locked over the next frames (nullptr or alpha 0: none). `image`
    // must outlive the calls; it is re-uploaded only when `generation` changes.
    void set_popup(const HudImage* image, std::uint64_t generation, float alpha);
    // Keyboard help card, head-locked in the middle of the view, under the pop-ups; `drop_px`
    // moves it down (fade-in rise). Same lifetime/upload rules as set_popup.
    void set_help(const HudImage* image, std::uint64_t generation, float alpha, float drop_px);
    // Placement menu card: same place and rules as the help card, drawn over it; while the menu
    // shows, the help card fades out by the menu's opacity so the two never stack.
    void set_menu(const HudImage* image, std::uint64_t generation, float alpha, float drop_px);
    void set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b);  // highlight border + glow (theme)
    std::string report() const;  // bounded JSON: renderer strings, timing; no pixel data
private:
    GlCompositor() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
