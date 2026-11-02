#pragma once
#include "xr/types.hpp"
#include "xr/metrics.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace xr {
// Narrow request boundary: intentionally no seat, focus, keyboard or injection API.
struct SurfaceRequests {
    virtual ~SurfaceRequests() = default;
    virtual void keyboard_interactivity(std::uint32_t) = 0;
    virtual void exclusive_zone(std::int32_t) = 0;
    virtual void empty_input_region() = 0;
    virtual void commit() = 0;
};
void apply_presentation_policy(SurfaceRequests&);
std::uint32_t layer_version(std::uint32_t advertised);
bool valid_surface_size(std::uint32_t width, std::uint32_t height);
std::uint32_t pattern_pixel(std::uint32_t x, std::uint32_t y, std::uint32_t width, std::uint32_t height);

// Pure callback/ownership gate used by the real adapter and offline tests.
std::optional<std::uint32_t> parse_pattern_duration(const std::string&);
class PatternCadence {
public:
    explicit PatternCadence(std::uint32_t duration_ms = 0, std::uint64_t start_ms = 0)
        : duration_(duration_ms), start_(start_ms) {}
    bool finished(std::uint64_t now) const { return stopped_ || (duration_ && now >= start_ && now - start_ >= duration_); }
    bool ready(std::uint64_t now, bool buffer_available, bool feedback_available) const {
        return !finished(now) && dirty_ && !waiting_ && buffer_available && feedback_available;
    }
    void committed() { dirty_ = false; waiting_ = true; if (duration_) ++phase_; }
    void frame_done() { if (waiting_) { waiting_ = false; if (duration_) dirty_ = true; } }
    void invalidate() { dirty_ = true; }
    void stop() { stopped_ = true; }
    std::uint64_t phase() const { return phase_; }
private:
    std::uint32_t duration_;
    std::uint64_t start_, phase_ = 0;
    bool dirty_ = true, waiting_ = false, stopped_ = false;
};
std::uint32_t animated_pattern_pixel(std::uint32_t x, std::uint32_t y, std::uint32_t width,
                                     std::uint32_t height, std::uint64_t phase);

class Presentation {
public:
    Presentation();
    ~Presentation();
    Presentation(const Presentation&) = delete;
    Presentation& operator=(const Presentation&) = delete;
    // `capture` is an already-validated XR-1..XR-3 allowlist (parse_capture_list); empty = no capture.
    Status open(std::string output_name, std::vector<std::string> capture = {});
    Status animate_pattern(std::uint32_t duration_ms);
    bool finished() const;
    Status draw_test_pattern();
    // Bounded dispatch; suitable for an independently supervised heartbeat loop.
    Status dispatch(int timeout_ms = 50);
    void close();
    std::string timing_report() const;
    std::string status_json(double now_ms) const;
    std::string capture_report() const;  // bounded JSON; never contains pixel data
    // Animate the glasses view (overview / zoom into `selected`, gap `spacing`); capture mode only.
    // Shows a short pop-up for the viewing mode (first call, and on change) and cursor follow (on change).
    void set_view(bool zoomed, std::uint32_t selected, std::uint32_t spacing, std::uint8_t mode = 0, bool cursor_follow = false,
                  bool prediction = false);
    // World-anchored 3DoF from the pose feed at `prefix` (GPU composition only); recenter on request.
    void enable_tracking(std::string prefix);
    void recenter();
    // Per-desktop placement in capture order, each {x, y, distance, yaw, pitch, roll, curvature} in the validated wire
    // units of ipc.hpp WirePlacement; entries beyond the captured outputs are ignored. GPU composition only.
    void set_placements(const std::vector<std::array<std::int32_t, 7>>& placements);
    void set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b);  // theme border colour
    // Keyboard help card (Ctrl+Alt+H): fades in/out in the middle of the view; rows are (keys, action).
    void set_help(bool visible, const std::vector<std::pair<std::string, std::string>>& rows);
    // Keyboard placement menu (display only; placements are applied by set_placements): fades in/out in the
    // middle of the view like the help card, which it replaces while shown. `desktop` 0..2, `field` 0..6 in
    // WirePlacement order; `placements` for all three desktops in the same units as set_placements. Mouse mode:
    // `pointer` with its row (-1 desktop selector, 0..6 fields), per-mille position along the slider and drag state.
    void set_placement_menu(bool open, std::uint32_t desktop, std::uint32_t field,
                            const std::array<std::array<std::int32_t, 7>, 3>& placements, bool pointer = false,
                            std::int32_t pointer_row = 0, std::uint32_t pointer_x = 0, bool pointer_drag = false);
    // Overview corner-resize handle (display only; the supervisor owns hover/drag and applies sizes with set_placements):
    // a dim bracket in corner `corner` (0 tl, 1 tr, 2 bl, 3 br) of desktop `desktop`, brighter while `active` (dragging).
    // Drawn only in the overview (hidden while zoomed or zooming), never orbiting or drifting. GPU composition only.
    void set_resize_handle(bool visible, std::uint32_t desktop, std::uint32_t corner, bool active);
    // Overview grab pills (display only; the supervisor owns hover/drag and moves desktops with set_placements): a dim
    // bar above every desktop, brighter over `desktop` when `hover`, brightest when `active` (dragging). Overview only,
    // never moving on its own. GPU composition only.
    void set_move_handle(bool visible, std::uint32_t desktop, bool hover, bool active);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    Metrics final_metrics_;
    std::string final_capture_ = "{\"kind\":\"managed_output_capture\",\"outputs\":[]}";
};
}
