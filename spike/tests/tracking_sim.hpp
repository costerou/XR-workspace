#pragma once
// Offline head-tracking simulator: synthetic head motion -> an XRLinuxDriver-shaped feed -> the
// renderer's per-frame tracking step (the same calls, in the same order, as wayland.cpp's
// draw_test_pattern) -> presentation on a 120 Hz vsync grid. Measures how far the predicted
// orientation is from the true head orientation when the frame's light leaves the panel.
//
// Driver model (XRLinuxDriver src/buffer.c, src/devices/viture.c, src/outputs.c): the newest IMU
// orientation plus the samples N and 2N cycles older (N = rate / 60), each with a device
// timestamp truncated to whole milliseconds (uint32) and then stored as float. Software only: the
// panel delay is modelled as exactly the driver's constant + half its scanline adjustment, so the
// error measures the estimator (velocity, timing, age), not a guess at the optics.
#include "xr/predict.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <optional>
#include <random>
#include <vector>

namespace xr::sim {
constexpr double kPi = 3.14159265358979323846;
constexpr double kPxPerDeg = 46.5;  // Luma Ultra: 1920x1200 over ~46 deg diagonal, at screen centre

inline Quat axis_angle(double x, double y, double z, double rad) {
    const double s = std::sin(rad / 2);
    return {float(x * s), float(y * s), float(z * s), float(std::cos(rad / 2))};
}
// Rotation vector (axis * angle, rad) of conj(a) * b: the error of b seen from a, body frame.
inline std::array<double, 3> rotvec(Quat a, Quat b) {
    Quat d = quat_mul(quat_conj(a), b);
    if (d.w < 0) d = {-d.x, -d.y, -d.z, -d.w};
    const double s = std::sqrt(double(d.x) * d.x + double(d.y) * d.y + double(d.z) * d.z);
    if (s < 1e-12) return {0, 0, 0};
    const double k = 2 * std::atan2(s, double(d.w)) / s;
    return {d.x * k, d.y * k, d.z * k};
}

enum class Motion { rest, sine, turns, fast };
inline const char* name(Motion m) {
    switch (m) {
    case Motion::rest: return "rest";
    case Motion::sine: return "sine";
    case Motion::turns: return "turns";
    case Motion::fast: return "fast";
    }
    return "?";
}
// Minimum-jerk position profile, 0..1 over u in 0..1.
inline double min_jerk(double u) { u = std::clamp(u, 0.0, 1.0); return u * u * u * (10 - 15 * u + 6 * u * u); }

// True head orientation at host time t (s). Yaw about z, pitch about y (driver frame: x forward, z up).
inline Quat truth(Motion m, double t) {
    double yaw = 0, pitch = 0;
    switch (m) {
    case Motion::rest: break;
    case Motion::sine:  // ~75 deg/s peak yaw, slow pitch
        yaw = 20 * std::sin(2 * kPi * 0.6 * t); pitch = 8 * std::sin(2 * kPi * 0.37 * t + 1); break;
    case Motion::turns: {  // minimum-jerk head turns, up to ~225 deg/s, with holds
        static constexpr double amp[] = {30, -45, 60, -20, 40, -65}, dur[] = {0.35, 0.45, 0.5, 0.3, 0.4, 0.55};
        constexpr double hold = 0.4;
        double t0 = 0.5;
        for (int i = 0; t >= t0; i = (i + 1) % 6) {
            const double end = t0 + dur[i];
            yaw += amp[i] * (t < end ? min_jerk((t - t0) / dur[i]) : 1.0);
            t0 = end + hold;
        }
        pitch = 6 * std::sin(2 * kPi * 0.2 * t); break;
    }
    case Motion::fast: {  // 90 deg in 0.3 s and back (~560 deg/s peak; exercises the clamp)
        const double c = std::fmod(t, 1.6);
        yaw = c < 0.8 ? 90 * min_jerk((c - 0.2) / 0.3) : 90 * (1 - min_jerk((c - 1.0) / 0.3)); break;
    }
    }
    return quat_mul(axis_angle(0, 0, 1, yaw * kPi / 180), axis_angle(0, 1, 0, pitch * kPi / 180));
}

struct Config {
    Motion motion = Motion::turns;
    double imu_hz = 1000;        // device IMU rate; the driver keeps q1/q2 at N = rate/60 samples back
    double boot_ms = 1.0e6;      // device clock at host t=0; > 2^24 ms (~4.66 h) loses whole ms in float
    double drift_ppm = 0;        // device clock rate error against the host's CLOCK_MONOTONIC
    double noise_deg = 0.002;    // per-sample orientation noise of the driver's fusion output
    // Luma Ultra (Carina, viture.c): device_ms = (uint32)((ts - ts0) * 1000) on double seconds, so exact
    // ms timestamps truncate a ms low about half the time (t0 - t1 reads 15/16/17 for 16 ms).
    bool carina = false;
    double seconds = 20;
    double refresh_hz = 120;
    // Pose read after the frame callback: 1 ms + U(0, read_jitter_ms), and with probability late_p a
    // further U(0, late_ms) (capture upload or IPC handled first). Commit = read + 1.5..4 ms.
    double read_jitter_ms = 1.0, late_p = 0.1, late_ms = 2.5;
    std::uint32_t seed = 1;
};
struct Result {
    std::uint64_t frames = 0, predicted = 0;
    double err_rms = 0, err_p95 = 0, err_max = 0;  // predicted vs truth at photon time (deg)
    double raw_rms = 0;                            // unpredicted newest sample vs truth (deg)
    double jitter_rms = 0;                         // RMS second difference of the error vector (deg/frame^2)
    double horizon_mean = 0;                       // ms
};

// wayland.cpp calls expected_ms() microseconds after taking read_ns (CLOCK_MONOTONIC); simulated time
// passes the read time explicitly. Builds against libraries without the explicit form too (baseline).
template <class L> std::optional<double> expected(const L& latency, std::uint64_t read_ns) {
    if constexpr (requires { latency.expected_ms(read_ns); }) return latency.expected_ms(read_ns);
    else { (void)read_ns; return latency.expected_ms(); }
}

// The renderer's per-frame tracking step, exactly as wayland.cpp performs it (prediction on).
struct LibraryStep {
    PoseState pose;
    LatencyTracker latency;
    PoseAge age;
    std::optional<LookAhead> cfg = look_ahead_from({10, 0.6f, 10, 40});  // Luma Ultra
    float last_horizon = 0;
    bool predicted = false;
    LibraryStep() { latency.clock(1); }
    Quat frame(const std::optional<FeedSample>& sample, std::uint64_t read_ns, double now_ms, double frametime_ms) {
        pose.update(sample, now_ms);
        Quat relative = pose.relative();
        const auto a = sample ? age.observe(double(read_ns) / 1e6, sample->device_ms) : std::nullopt;
        predicted = false;
        if (pose.tracking()) {
            const auto omega = sample ? angular_velocity(history_of(*sample)) : std::nullopt;
            const auto horizon = prediction_horizon(cfg, expected(latency, read_ns), a.value_or(0.0), frametime_ms);
            if (omega && horizon) {
                relative = quat_mul(relative, prediction_delta(*omega, *horizon, 15.f));
                last_horizon = *horizon; predicted = true;
            }
        }
        return relative;
    }
    void read(std::uint64_t ticket, std::uint64_t read_ns) { latency.read(ticket, read_ns); }
    void presented(std::uint64_t ticket, std::uint64_t present_ns) { latency.presented(ticket, present_ns); }
};

template <class Step = LibraryStep> Result run(const Config& c, Step step = {}) {
    std::mt19937 rng(c.seed);
    std::normal_distribution<double> gauss(0, 1);
    std::uniform_real_distribution<double> unit(0, 1);
    const int n_back = std::max(1, int(c.imu_hz / 60));
    const double dt_s = 1 / c.imu_hz;
    struct Imu { Quat q; float device_ms; double published_ms; };
    std::vector<Imu> imu;
    double last_pub = 0;
    auto sample_at = [&](std::size_t n) -> const Imu& {
        while (imu.size() <= n) {
            const double t = double(imu.size()) * dt_s;
            const double sigma = c.noise_deg * kPi / 180;
            Quat q = truth(c.motion, t);
            const double nx = gauss(rng) * sigma, ny = gauss(rng) * sigma, nz = gauss(rng) * sigma;
            const double nn = std::sqrt(nx * nx + ny * ny + nz * nz);
            if (nn > 0) q = quat_mul(truth(c.motion, t), axis_angle(nx / nn, ny / nn, nz / nn, nn));
            const double device = c.boot_ms + t * 1000 * (1 + c.drift_ppm * 1e-6);
            float device_ms = float(std::uint32_t(device));  // driver: (uint32_t)(ns / 1e6), then float
            if (c.carina) {  // the SDK's IMU time in seconds (exact ms here), less the first, times 1000
                const double ts0 = 1234.567, ts = ts0 + std::round(device - c.boot_ms) / 1000.0;
                device_ms = float(std::uint32_t((ts - ts0) * 1000.0));
            }
            last_pub = std::max(last_pub, t * 1000 + 1.0 + std::fabs(gauss(rng)) * 0.2);  // USB + fusion
            imu.push_back({q, device_ms, last_pub});
        }
        return imu[n];
    };
    const double period = 1000 / c.refresh_hz;
    struct Frame { std::uint64_t ticket; double present_ms; Quat shown; };
    std::vector<Frame> inflight;
    std::vector<double> errs;
    std::vector<std::array<double, 3>> evec;
    double raw_sq = 0, horizon_sum = 0;
    Result r;
    std::size_t newest = 0;
    std::optional<Quat> center;
    const int frames = int(c.seconds * c.refresh_hz);
    for (int k = 2; k < frames; ++k) {
        const double vblank = k * period + 0.37;
        double read_ms = vblank + 1.0 + unit(rng) * c.read_jitter_ms;  // frame callback + pre-draw CPU
        if (unit(rng) < c.late_p) read_ms += unit(rng) * c.late_ms;
        for (auto it = inflight.begin(); it != inflight.end();) {  // feedback for frames already shown
            if (it->present_ms <= read_ms) { step.presented(it->ticket, std::uint64_t(it->present_ms * 1e6)); it = inflight.erase(it); }
            else ++it;
        }
        while (sample_at(newest + 1).published_ms <= read_ms) ++newest;
        std::optional<FeedSample> sample;
        if (int(newest) >= 2 * n_back && sample_at(newest).published_ms <= read_ms) {
            const auto& s0 = imu[newest];
            const auto& s1 = imu[newest - std::size_t(n_back)];
            const auto& s2 = imu[newest - std::size_t(2 * n_back)];
            sample = FeedSample{*quat_normalized(s0.q), s0.device_ms, {s1.q, s2.q}, {s1.device_ms, s2.device_ms}};
            if (!center) center = *quat_normalized(s0.q);
        }
        const auto read_ns = std::uint64_t(read_ms * 1e6);
        const Quat shown = step.frame(sample, read_ns, read_ms, period);
        const std::uint64_t ticket = std::uint64_t(k);
        step.read(ticket, read_ns);
        const double commit = read_ms + 1.5 + unit(rng) * 2.5;
        double present = std::ceil((commit + 0.5 - 0.37) / period) * period + 0.37 + period;  // latch, then compositor frame
        inflight.push_back({ticket, present, shown});
        if (!center || k < 120) continue;  // warm-up: EMA and age floor settle
        const Quat truth_rel = quat_mul(quat_conj(*center), truth(c.motion, (present + 10 + 5) / 1000));  // + constant + scanline/2
        const auto e = rotvec(truth_rel, shown);
        const double deg = std::sqrt(e[0] * e[0] + e[1] * e[1] + e[2] * e[2]) * 180 / kPi;
        errs.push_back(deg); evec.push_back(e);
        if (sample) {
            const auto raw = rotvec(truth_rel, quat_mul(quat_conj(*center), *quat_normalized(sample->orientation)));
            raw_sq += (raw[0] * raw[0] + raw[1] * raw[1] + raw[2] * raw[2]) * (180 / kPi) * (180 / kPi);
        }
        ++r.frames;
        if (step.predicted) { ++r.predicted; horizon_sum += step.last_horizon; }
    }
    if (!r.frames) return r;
    double sq = 0;
    for (double e : errs) sq += e * e;
    r.err_rms = std::sqrt(sq / double(errs.size()));
    r.raw_rms = std::sqrt(raw_sq / double(errs.size()));
    std::vector<double> sorted = errs;
    std::sort(sorted.begin(), sorted.end());
    r.err_p95 = sorted[std::size_t(0.95 * double(sorted.size() - 1))];
    r.err_max = sorted.back();
    double jsq = 0;
    for (std::size_t i = 2; i < evec.size(); ++i)
        for (int a = 0; a < 3; ++a) { const double d2 = evec[i][a] - 2 * evec[i - 1][a] + evec[i - 2][a]; jsq += d2 * d2; }
    r.jitter_rms = evec.size() > 2 ? std::sqrt(jsq / double(evec.size() - 2)) * 180 / kPi : 0;
    r.horizon_mean = r.predicted ? horizon_sum / double(r.predicted) : 0;
    return r;
}
}
