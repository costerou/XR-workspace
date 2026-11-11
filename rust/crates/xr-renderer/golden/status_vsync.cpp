// Writes golden/status_vsync.txt: the status datagram's vsync_error_ms and sensor_to_vsync_ms, built from the C++
// VsyncMeter (spike/src/predict.cpp) exactly as Presentation::status_json (spike/src/wayland.cpp) appends them. From the
// repository root:
//   g++ -std=c++20 -Ispike/include -Ispike/driver/include rust/crates/xr-renderer/golden/status_vsync.cpp spike/src/predict.cpp spike/src/pose.cpp spike/driver/src/rt.cpp -o /tmp/status-vsync-golden && /tmp/status-vsync-golden > rust/crates/xr-renderer/golden/status_vsync.txt
// shell/tracking.rs replays the same scenarios through Tracking::status and ipc::status_dict and must print the same lines.
#include "xr/predict.hpp"
#include <cmath>
#include <cstdio>
#include <string>
using namespace xr;
static std::string status(const VsyncMeter& m) {
    std::string raw = "{}";
    const auto add = [&raw](const std::string& field) {  // wayland.cpp's `add`
        raw.pop_back();
        if (raw.size() > 1) raw += ",";
        raw += field + "}";
    };
    if (const VsyncSummary v = m.summary(); v.count) {
        add("\"vsync_error_ms\":" + std::to_string(v.error_p95_ms));
        if (v.sensor_count) add("\"sensor_to_vsync_ms\":" + std::to_string(v.sensor_p50_ms));
    }
    return raw;
}
// n presents at 90 Hz from t0, each `late_us[i % 5]` after its scan-out; the sample drawn `age_ms` before the scan-out
// (negative age: a sample newer than the scan-out, a clock offset), or none when `sample` is false.
static void run(VsyncMeter& m, int n, double age_ms, bool sample, double feed_ms = 500.0) {
    VsyncSnapshot v;
    v.anchor_host_ns = 1000000000ull;
    v.period_ns = 1e9 / 90.0;
    v.anchor_feed_ms = feed_ms;
    const double late_us[5] = {2000, 2150, 1900, 2600, 2050};
    for (int k = 0; k < n; k++) {
        const std::uint64_t at = v.anchor_host_ns + std::uint64_t(k * v.period_ns + late_us[k % 5] * 1e3);
        m.presented(v, at, sample ? float(500.0 + k * v.period_ns / 1e6 - age_ms) : -1.0f);
    }
}
int main() {
    { VsyncMeter m; std::printf("%s\n", status(m).c_str()); }                                  // nothing presented
    { VsyncMeter m; m.presented(std::nullopt, 5, 1.0f); std::printf("%s\n", status(m).c_str()); }  // skipped only
    { VsyncMeter m; run(m, 20, 0, false); std::printf("%s\n", status(m).c_str()); }            // no sample drawn
    { VsyncMeter m; run(m, 40, 12.25, true); std::printf("%s\n", status(m).c_str()); }
    { VsyncMeter m; run(m, 40, -3.5, true); std::printf("%s\n", status(m).c_str()); }          // negative age
    { VsyncMeter m; run(m, 10, 7.0, true, NAN); std::printf("%s\n", status(m).c_str()); }      // no feed anchor yet
    { VsyncMeter m; run(m, 1, 30.0, true); std::printf("%s\n", status(m).c_str()); }
}
