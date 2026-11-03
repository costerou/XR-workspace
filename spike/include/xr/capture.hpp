#pragma once
// Capture of the app's own managed outputs only (user grant 2026-10-31: XR-1..XR-3, in memory,
// never written anywhere, never the presentation or recovery outputs). Pure, offline-testable
// helpers; the Wayland client lives in wayland.cpp.
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <span>
#include <vector>
#include "xr/view.hpp"

namespace xr {
// "XR-1,XR-2,XR-3" -> ordered unique names. Rejects anything not exactly XR-[1-3], duplicates,
// empty entries, more than three, or the renderer's own output.
std::optional<std::vector<std::string>> parse_capture_list(const std::string& csv, const std::string& own_output);

constexpr std::uint32_t kShmArgb8888 = 0, kShmXrgb8888 = 1;  // wl_shm.format values
struct ShmPlan { std::uint32_t width = 0, height = 0, stride = 0, format = 0; std::size_t bytes = 0; };
// Choose a 32-bit SHM layout from the session's constraints; nothing else is accepted.
std::optional<ShmPlan> plan_capture_buffer(std::uint32_t width, std::uint32_t height, const std::vector<std::uint32_t>& formats);

// Zero-copy capture (C2b): DRM fourcc + modifier the compositor offers and our EGL can import.
constexpr std::uint32_t kDrmXrgb8888 = 0x34325258, kDrmArgb8888 = 0x34325241;  // 'XR24', 'AR24'
constexpr std::uint64_t kModLinear = 0, kModInvalid = 0x00ffffffffffffffULL;     // INVALID = implicit modifier
struct DmabufFormat { std::uint32_t fourcc = 0; std::vector<std::uint64_t> modifiers; };
struct DmabufChoice { std::uint32_t fourcc = 0; std::uint64_t modifier = 0; };
// Prefer XRGB then ARGB; among offered modifiers EGL can import, prefer LINEAR, then the first.
template <class Importable>
std::optional<DmabufChoice> choose_dmabuf(const std::vector<DmabufFormat>& offered, Importable importable) {
    for (auto fourcc : {kDrmXrgb8888, kDrmArgb8888})
        for (const auto& f : offered) if (f.fourcc == fourcc) {
            std::optional<std::uint64_t> first;
            for (auto m : f.modifiers) if (importable(fourcc, m)) {
                if (m == kModLinear) return DmabufChoice{fourcc, m};
                if (!first) first = m;
            }
            if (first) return DmabufChoice{fourcc, *first};
        }
    return std::nullopt;
}

// Session constraints arrive as any order of shm_format/buffer_size events, then done
// (Hyprland efb5099 sends formats first). Accumulate; reset only once a batch is complete.
class SessionConstraints {
public:
    void size(std::uint32_t width, std::uint32_t height) { width_ = width; height_ = height; }
    void format(std::uint32_t format) { formats_.push_back(format); }
    void dmabuf_device(std::uint64_t device) { device_ = device; }
    void dmabuf_format(std::uint32_t fourcc, std::vector<std::uint64_t> modifiers) {
        for (auto& f : dmabuf_) if (f.fourcc == fourcc) { f.modifiers.insert(f.modifiers.end(), modifiers.begin(), modifiers.end()); return; }
        dmabuf_.push_back({fourcc, std::move(modifiers)});
    }
    struct Batch { std::optional<ShmPlan> shm; std::optional<std::uint64_t> device; std::vector<DmabufFormat> dmabuf; std::uint32_t width = 0, height = 0; };
    Batch done_all() {
        Batch b{plan_capture_buffer(width_, height_, formats_), device_, dmabuf_, width_, height_};
        width_ = height_ = 0; formats_.clear(); device_.reset(); dmabuf_.clear();
        return b;
    }
    std::optional<ShmPlan> done() { return done_all().shm; }
private:
    std::uint32_t width_ = 0, height_ = 0;
    std::vector<std::uint32_t> formats_;
    std::optional<std::uint64_t> device_;
    std::vector<DmabufFormat> dmabuf_;
};

struct Rect { std::uint32_t x = 0, y = 0, width = 0, height = 0; };
// Tiles side by side at one shared height (the largest that fits), each keeping its source aspect
// (floored to whole pixels); leftover width is split into equal per-tile gaps. Centred vertically
// inside dst. Empty if there are no sources, dst is empty, or any source size is non-positive.
std::vector<Rect> side_by_side(std::span<const Size> sources, std::uint32_t dst_width, std::uint32_t dst_height);
// Clip-space quad (x0,y0,x1,y1 in [-1,1], y up) for a pixel rect in a w x h viewport (y down).
struct ClipQuad { float x0 = 0, y0 = 0, x1 = 0, y1 = 0; };
ClipQuad clip_quad(Rect rect, std::uint32_t width, std::uint32_t height);
// Nearest-neighbour scale of a 32-bit source into dst rect (clipped to dst); alpha forced opaque.
// Source column/row for dst pixel i of the rect: floor(i * src_size / rect_size). No per-pixel division.
void blit_nearest(const std::uint32_t* src, std::uint32_t src_width, std::uint32_t src_height, std::uint32_t src_stride_px,
                  std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px, Rect rect);
// One source of the CPU composition; null pixels (not captured yet) draw nothing.
struct TileSource { const std::uint32_t* pixels = nullptr; std::uint32_t width = 0, height = 0, stride_px = 0; };
// CPU composition frame (the GPU-less fallback): `background` in columns [0, dst_width) of every row, then sources[i]
// blit_nearest-ed into tiles[i] in order. Pixel-identical to filling and then blitting, but each pixel is written once:
// only what no blit covers gets the background.
void compose_tiles(std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px,
                   std::span<const TileSource> sources, std::span<const Rect> tiles, std::uint32_t background);
}
