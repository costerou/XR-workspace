// Offline replay of a pose capture through the renderer's own per-frame pose path: the feed as PoseFeed serves it (three
// newest samples, history_step 1), then Stabilizer, PoseState, PoseAge, prediction_horizon and MotionSmoother, in the order of
// wayland.cpp's draw. No feed, no compositor, no GPU, no SDK.
// stdin: host_ms device_ms x y z w, one sample per line (device_ms from 0).
// stdout: first "center x y z w tx ty tz tw" (the recentre, and the level turn: relative = conj(center) * head * turn), then one line per frame: present_ms device_ms x y z w horizon_ms predicted_deg yaw_deg roll_deg
// (host clock; x y z w is what the frame shows, relative to the recentre). yaw_deg and roll_deg come from the view the renderer would
// draw with: how far right the view has turned, and how far a horizontal line on the canvas leans at the screen's centre, positive
// when it leans left (counter-clockwise).
#include "xr/motion_smooth.hpp"
#include "xr/predict.hpp"
#include "xr/stabilization.hpp"
#include "view_lean.hpp"
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <map>
#include <string>
#include <vector>
using namespace xr;
struct In { double host; float device; Quat q; };
int main(int argc, char** argv) {
    std::map<std::string, std::string> o{{"--mode", "smooth"}, {"--period", "8.3333"}, {"--r2p", "16.6"}, {"--phase", "0"},
        {"--stabilization", "25"}, {"--tau", "35"}, {"--cap", "40"}, {"--constant", "0"}, {"--publish-delay", "1"},
        {"--max-predict", "6"}, {"--snap", "30"}, {"--brake", "3"}, {"--brake-floor", "4"}, {"--read-jitter", "0"}, {"--level", "0"}};
    for (int i = 1; i + 1 < argc; i += 2) { if (!o.count(argv[i])) { std::cerr << "unknown " << argv[i] << '\n'; return 2; } o[argv[i]] = argv[i + 1]; }
    auto num = [&](const char* k) { return std::strtod(o[k].c_str(), nullptr); };
    std::vector<In> in;
    In s{};
    while (std::cin >> s.host >> s.device >> s.q.x >> s.q.y >> s.q.z >> s.q.w) in.push_back(s);
    if (in.size() < 4) return 2;
    const std::string mode = o["--mode"];
    if (mode != "hold" && mode != "legacy" && mode != "smooth") return 2;
    const double period = num("--period"), r2p = num("--r2p");
    Stabilizer stabilizer; stabilizer.strength(int(num("--stabilization")));
    PoseState pose; PoseAge age;
    pose.level_reference(num("--level") > 0);
    MotionSmoothParams params; params.tau_ms = float(num("--tau")); params.max_predict_deg = float(num("--max-predict"));
    params.snap_deg = float(num("--snap")); params.brake_power = float(num("--brake"));
    params.brake_floor_rad_per_ms = float(num("--brake-floor")) * 3.14159265f / 180000.f;
    MotionSmoother smoother(params);
    ModeController modes;
    const auto cfg = look_ahead_from({float(num("--constant")), 0.f, 0.f, float(num("--cap"))});
    if (mode != "hold" && !cfg) return 2;
    std::cout << std::setprecision(12);
    bool centred = false;
    std::size_t newest = 0, jitter_step = 0;
    float last_device = -1;
    const double start = in[3].host + period + num("--phase"), end = in.back().host;
    for (double grid = start; grid < end; grid += period) {
        // A read lands a little off the grid; the present does not (the phase-aware estimate absorbs it).
        const double read = grid + num("--read-jitter") * (((jitter_step++ * 2654435761u) >> 8 & 255) / 255.0 - 0.5);
        while (newest + 1 < in.size() && in[newest + 1].host + num("--publish-delay") <= read) ++newest;
        if (newest < 2) continue;
        FeedSample f{in[newest].q, in[newest].device, {in[newest - 1].q, in[newest - 2].q}, {in[newest - 1].device, in[newest - 2].device}};
        const float raw_ms = f.device_ms;
        auto stabilized = std::optional<FeedSample>(f);
        // The stabilizer steps per read like the renderer's; a repeated device time returns the same output.
        stabilized->orientation = stabilizer.update(stabilized->orientation, stabilized->device_ms);
        pose.update(stabilized, read);
        Quat relative = pose.relative();
        const auto a = age.observe(read, raw_ms);
        float horizon_shown = 0, predicted = 0;
        if (pose.tracking() && mode != "hold") {
            const auto horizon = prediction_horizon(cfg, std::optional<double>(r2p - (read - grid)), a.value_or(0.0), period);
            if (mode == "legacy") {
                const auto omega = angular_velocity(history_of(f));
                if (omega && horizon) {
                    const Quat delta = prediction_delta(*omega, *horizon * stabilizer.prediction_gain(), 15.f);
                    relative = quat_mul(relative, delta);
                    horizon_shown = *horizon; predicted = quat_angle(Quat{}, delta) * float(180 / M_PI);
                }
            } else if (horizon) {
                relative = smoother.update(relative, f.device_ms, *horizon, read);
                horizon_shown = smoother.last_horizon_ms(); predicted = smoother.last_predicted_deg();
            }
        }
        const xr::Shown view = xr::shown_by(view_from(relative, modes.update(relative, read)));
        if (!centred) {
            const Quat turn = pose.level_turn();
            const auto c = quat_mul(quat_mul(stabilized->orientation, turn), quat_conj(pose.relative()));
            std::cout << "center " << c.x << ' ' << c.y << ' ' << c.z << ' ' << c.w << ' ' << turn.x << ' ' << turn.y << ' ' << turn.z << ' ' << turn.w << '\n';
            centred = true;
        }
        std::cout << grid + r2p << ' ' << raw_ms << ' ' << relative.x << ' ' << relative.y << ' ' << relative.z << ' ' << relative.w << ' '
                  << horizon_shown << ' ' << predicted << ' ' << view.yaw_deg << ' ' << view.roll_deg << '\n';
        last_device = raw_ms;
    }
    (void)last_device;
    std::cerr << "snaps " << smoother.snaps() << " max_predicted_deg " << smoother.max_predicted_deg() << " peak_speed_dps " << smoother.peak_speed_dps()
              << " capped_frames " << smoother.capped_frames() << " turn_axis_forward_deg " << smoother.turn_axis_forward_deg()
              << " turn_axis_left_deg " << smoother.turn_axis_left_deg() << " turn_seconds " << smoother.turn_seconds() << '\n';
    return 0;
}
