#pragma once
// Capture of the app's own managed outputs only (user grant 2026-10-31: the run's XR-n desktops, in memory,
// never written anywhere, never the presentation or recovery outputs; XR-1..XR-kMaxDesktops since desktops can be
// added and removed during a run). Pure, offline-testable
// helpers; the Wayland client lives in wayland.cpp.
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <span>
#include <vector>
#include "xr/view.hpp"

namespace xr {
// "XR-1,XR-2,XR-3" -> ordered unique names. Rejects anything not exactly XR-1..XR-kMaxDesktops, duplicates,
// empty entries, more than kMaxDesktops, or the renderer's own output.
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

// Capture buffer lifecycle of one session (pure; wayland.cpp does what it decides). The compositor resends constraints
// whenever the output's mode is touched: Hyprland sends a batch on every monitor modeChanged, a workspace layout change
// (hl.workspace_rule) re-checks every monitor rule, and a config reload puts each XR-n output through the default rule
// and straight back to its own. So:
// - buffers are re-created only for a batch that differs (size or format) from what they were made for, and a batch
//   that matches them again (a size that changed and changed back) cancels a pending re-creation;
// - re-creation waits until the events have settled (settle(), called once a dispatch is done) and no frame is in
//   flight, so several batches in one dispatch re-create at most once;
// - a buffer-constraints failure waits for the next batch with the buffers it has (it never forgets their plan);
// - layout_size() is the size of the frame on screen (the last delivered one), so the overview layout changes when
//   the new content appears, not when constraints arrive. The renderer keeps showing that frame meanwhile.
class CaptureBuffers {
public:
    void constraints(const ShmPlan& plan) { ++batches_; target_ = plan; waiting_ = false; }
    void constraints_failed() { waiting_ = true; }
    // The session was ended and a new one is starting (an output reshaped and back): its first batch re-creates the
    // buffers whatever its size; the layout keeps the shown frame's size until a new frame lands.
    void restart() { target_.reset(); allocated_.reset(); waiting_ = false; }
    bool reallocation_pending() const { return target_ && (!allocated_ || !same_buffers(*target_, *allocated_)); }
    // A frame may be requested into the current buffers.
    bool may_capture() const { return allocated_ && !waiting_ && !reallocation_pending(); }
    // True: re-create the buffers for allocated() now (they are recorded as made for it).
    bool settle(bool frame_in_flight) {
        if (frame_in_flight || !reallocation_pending()) return false;
        allocated_ = target_; ++reallocations_; return true;
    }
    // A frame landed in the current buffers.
    void delivered() { if (allocated_) shown_ = Size{float(allocated_->width), float(allocated_->height)}; }
    std::optional<Size> layout_size() const {
        if (shown_) return shown_;
        const auto& p = allocated_ ? allocated_ : target_;
        if (p && p->width && p->height) return Size{float(p->width), float(p->height)};
        return std::nullopt;
    }
    const std::optional<ShmPlan>& target() const { return target_; }        // latest batch
    const std::optional<ShmPlan>& allocated() const { return allocated_; }   // what the buffers are made for
    std::uint64_t batches() const { return batches_; }
    std::uint64_t reallocations() const { return reallocations_; }
    static bool same_buffers(const ShmPlan& a, const ShmPlan& b) {
        return a.width == b.width && a.height == b.height && a.stride == b.stride && a.format == b.format;
    }
private:
    std::optional<ShmPlan> target_, allocated_;
    std::optional<Size> shown_;
    bool waiting_ = false;
    std::uint64_t batches_ = 0, reallocations_ = 0;
};
// A capture whose output was resized live (a glass pill's Resolution dropdown or Ultrawide: tools/native_spike/desktops.py)
// re-creates its buffers kResizeSettleMs after the new size was first seen, not at once (wayland.cpp settle_captures).
// Meanwhile the last frame stays on screen at its old size and nothing is captured, while that output's own clients
// (wallpaper, bar, windows) draw themselves at the new size: the first frame shown at the new size has them done, never
// half drawn with black (see-through on the glasses) where a layer has not caught up. Only a pending re-creation to a
// size other than the one on screen waits: a capture's first buffers, a format change and a size that comes back
// (a config reload's bounce) do not.
constexpr std::uint64_t kResizeSettleMs = 100;
class ResizeSettle {
public:
    // Whether `buffers`' pending re-creation waits at `now_ms`.
    bool hold(const CaptureBuffers& buffers, std::uint64_t now_ms) {
        const auto& target = buffers.target();
        const auto shown = buffers.layout_size();
        if (!buffers.reallocation_pending() || !target || !shown || (float(target->width) == shown->w && float(target->height) == shown->h)) {
            since_.reset();
            return false;
        }
        if (!since_) { since_ = now_ms; ++settles_; }
        return now_ms < *since_ + kResizeSettleMs;
    }
    std::uint64_t settles() const { return settles_; }  // size changes that waited (diagnostic)
private:
    std::optional<std::uint64_t> since_;
    std::uint64_t settles_ = 0;
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
