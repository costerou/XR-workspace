#pragma once
// Capture of the app's own managed outputs only (user grant 2026-10-31: XR-1..XR-3, in memory,
// never written anywhere, never the presentation or recovery outputs). Pure, offline-testable
// helpers; the Wayland client lives in wayland.cpp.
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace xr {
// "XR-1,XR-2,XR-3" -> ordered unique names. Rejects anything not exactly XR-[1-3], duplicates,
// empty entries, more than three, or the renderer's own output.
std::optional<std::vector<std::string>> parse_capture_list(const std::string& csv, const std::string& own_output);

constexpr std::uint32_t kShmArgb8888 = 0, kShmXrgb8888 = 1;  // wl_shm.format values
struct ShmPlan { std::uint32_t width = 0, height = 0, stride = 0, format = 0; std::size_t bytes = 0; };
// Choose a 32-bit SHM layout from the session's constraints; nothing else is accepted.
std::optional<ShmPlan> plan_capture_buffer(std::uint32_t width, std::uint32_t height, const std::vector<std::uint32_t>& formats);

// Session constraints arrive as any order of shm_format/buffer_size events, then done
// (Hyprland efb5099 sends formats first). Accumulate; reset only once a batch is complete.
class SessionConstraints {
public:
    void size(std::uint32_t width, std::uint32_t height) { width_ = width; height_ = height; }
    void format(std::uint32_t format) { formats_.push_back(format); }
    std::optional<ShmPlan> done() {
        auto plan = plan_capture_buffer(width_, height_, formats_);
        width_ = height_ = 0; formats_.clear();
        return plan;
    }
private:
    std::uint32_t width_ = 0, height_ = 0;
    std::vector<std::uint32_t> formats_;
};

struct Rect { std::uint32_t x = 0, y = 0, width = 0, height = 0; };
// n equal tiles side by side, aspect-preserving, centred vertically, inside dst.
std::vector<Rect> side_by_side(std::size_t count, std::uint32_t dst_width, std::uint32_t dst_height,
                               std::uint32_t src_width, std::uint32_t src_height);
// Nearest-neighbour scale of a 32-bit source into dst rect (clipped to dst); alpha forced opaque.
void blit_nearest(const std::uint32_t* src, std::uint32_t src_width, std::uint32_t src_height, std::uint32_t src_stride_px,
                  std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px, Rect rect);
}
