#pragma once
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include "xr/desktops.hpp"
#include <string>
#include <cstring>
#include <type_traits>
#include <stdexcept>
#include <cstdint>
#include <cmath>

namespace xr::continuity_detail {
struct Writer {
    std::string bytes;
    template<class T> void one(T& v) {
        static_assert(std::is_trivially_copyable_v<T>);
        bytes.append(reinterpret_cast<const char*>(&v), sizeof(v));
    }
    template<class T> void one(std::vector<T>& v) {
        auto n = std::uint32_t(v.size());
        if (n > 64) throw std::runtime_error("continuity vector limit");
        one(n); for (auto& x : v) one(x);
    }
    template<class... T> void operator()(T&... v) { (one(v), ...); }
};
struct Reader {
    const std::string& bytes; std::size_t offset = 0;
    template<class T> void one(T& v) {
        static_assert(std::is_trivially_copyable_v<T>);
        if (offset + sizeof(v) > bytes.size()) throw std::runtime_error("short continuity");
        std::memcpy(&v, bytes.data() + offset, sizeof(v)); offset += sizeof(v);
    }
    template<class T> void one(std::vector<T>& v) {
        std::uint32_t n; one(n);
        if (n > 64) throw std::runtime_error("continuity vector limit");
        v.resize(n); for (auto& x : v) one(x);
    }
    template<class... T> void operator()(T&... v) { (one(v), ...); }
};
inline std::uint64_t hash(const std::string& s) {
    std::uint64_t h = 14695981039346656037ull;
    for (unsigned char c : s) { h ^= c; h *= 1099511628211ull; }
    return h;
}
}
namespace xr {
// Private, same-build renderer handover. Never persisted or accepted from IPC.
struct ViewContinuity {
    PoseState pose;
    ModeController modes;
    ViewAnimator view;
    bool maximized = false;
    std::array<std::int32_t, kMaxDesktops> rolls{};
    Mat3 last_drawn{1,0,0,0,1,0,0,0,1};
    bool centre_valid = false;
    std::uint32_t centre_serial = 0;
    std::array<float, 2> centre_dir{};
    double saved_ms = 0;
    template<class Archive> void checkpoint(Archive& a) {
        a(pose, modes, maximized, rolls, last_drawn, centre_valid, centre_serial, centre_dir, saved_ms);
        view.checkpoint(a);
    }
    std::string encode() {
        continuity_detail::Writer a; checkpoint(a);
        auto hash = continuity_detail::hash(a.bytes);
        return std::string("XRCONT01") + std::string(reinterpret_cast<char*>(&hash), sizeof(hash)) + a.bytes;
    }
    bool decode(const std::string& bytes) {
        if (bytes.size() < 16 || bytes.size() > 65536 || bytes.substr(0, 8) != "XRCONT01") return false;
        std::uint64_t hash; std::memcpy(&hash, bytes.data() + 8, sizeof(hash));
        const auto payload = bytes.substr(16);
        if (hash != continuity_detail::hash(payload)) return false;
        try {
            ViewContinuity next;
            continuity_detail::Reader a{payload}; next.checkpoint(a);
            if (a.offset != payload.size() || !std::isfinite(next.saved_ms)) return false;
            *this = std::move(next); return true;
        } catch (const std::runtime_error&) { return false; }
    }
    void resume(double now_ms) {
        const double gap = std::max(0.0, now_ms - saved_ms);
        view.pause_clock(gap);
        if (modes.started_ms_ >= 0) modes.started_ms_ += gap;
        if (modes.last_ms_ >= 0) modes.last_ms_ += gap;
        // This is a display-only switch with the same driver/reference. Do not
        // interpret time spent with no renderer as a tracking recalibration.
        pose.last_change_ms_ += gap;
        pose.lost_at_ms_ += gap;
        if (pose.settling_from_ms_ >= 0) pose.settling_from_ms_ += gap;
        saved_ms = now_ms;
    }
};
}
