#pragma once
#include "xr/types.hpp"
#include "xr/metrics.hpp"
#include <memory>

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

class Presentation {
public:
    Presentation();
    ~Presentation();
    Presentation(const Presentation&) = delete;
    Presentation& operator=(const Presentation&) = delete;
    Status open(std::string output_name);
    Status draw_test_pattern();
    // Bounded dispatch; suitable for an independently supervised heartbeat loop.
    Status dispatch(int timeout_ms = 50);
    void close();
    std::string timing_report() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    Metrics final_metrics_;
};
}
