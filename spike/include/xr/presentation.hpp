#pragma once
#include "xr/types.hpp"
#include "xr/metrics.hpp"
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
    std::string capture_report() const;  // bounded JSON; never contains pixel data
    // Animate the glasses view (overview / zoom into `selected`, gap `spacing`); capture mode only.
    // Shows a short pop-up for the viewing mode (first call, and on change) and cursor follow (on change).
    void set_view(bool zoomed, std::uint32_t selected, std::uint32_t spacing, std::uint8_t mode = 0, bool cursor_follow = false);
    // World-anchored 3DoF from the pose feed at `prefix` (GPU composition only); recenter on request.
    void enable_tracking(std::string prefix);
    void recenter();
    void set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b);  // theme border colour
    // Keyboard help card (Ctrl+Alt+H): fades in/out in the middle of the view; rows are (keys, action).
    void set_help(bool visible, const std::vector<std::pair<std::string, std::string>>& rows);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    Metrics final_metrics_;
    std::string final_capture_ = "{\"kind\":\"managed_output_capture\",\"outputs\":[]}";
};
}
