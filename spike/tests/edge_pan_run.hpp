#pragma once
// A run's view with a still head, for measuring and drawing the edge pan offline: what wayland.cpp's Impl keeps for its frame
// loop, for any desktops, placements, spacing and FOV. ViewAnimator, blended_layout, zoomed_placements, edge_pan_path, aim_along and
// ModeController are the real ones. focus_aim and pan_dot are wayland.cpp's, word for word (private to its Impl).
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <vector>
#include "xr/pose.hpp"
#include "xr/view.hpp"

namespace xr::pan_run {
struct Layout {
    Canvas canvas;
    float spacing = 0;
    std::vector<Size> sources;
    std::array<Placement, kMaxDesktops> placed{};
    ViewMode mode = ViewMode::follow;
    bool tracked = true;
};

struct Run {
    ViewAnimator view;
    ModeController modes;
    Layout l;
    Run(const Layout& layout, std::uint32_t selected) : l(layout) {
        view.set_target(false, selected, std::uint32_t(l.spacing), 0);
        view.advance(10000);
        modes.set_mode(l.mode, 10000, ModeController::Change::maximize);
        view.set_target(true, selected, std::uint32_t(l.spacing), 10000);
        view.advance(20000);
        modes.set_aim(focus_aim());
        modes.land_maximize(20000);
        for (double t = 20000; t <= 30000; t += 8) step(t);  // follow's spring brings the desktop in front of you, as it has by the time a hand arrives
    }
    std::vector<FRect> overview() const { return overview_layout(l.sources, l.canvas.view_w, l.canvas.view_h, view.spacing()); }
    std::span<const Placement> placements() const { return {l.placed.data(), l.sources.size()}; }
    Quat focus_aim() const {
        const float w = focus_weight(view);
        if (!(w > 0.f) && !view.pan_active()) return Quat{};
        const auto over = overview();
        const auto ps = placements();
        if (view.pan_active()) {
            const auto from = focus_direction(l.canvas, over, ps, view.pan().from), to = focus_direction(l.canvas, over, ps, view.pan().to);
            return aim_along(aim_rotation(from[0], from[1]), aim_rotation(to[0], to[1]), view.pan().way, view.pan_turn(), view.pan().side);
        }
        const auto d = focus_direction(l.canvas, over, ps, view.focus());
        return aim_rotation(w * d[0], w * d[1]);
    }
    std::optional<std::array<float, 2>> pan_dot(const std::vector<FRect>& tiles, const std::vector<Placement>& drawn) const {
        if (!view.pan_active() || view.pan_landing()) return std::nullopt;
        const auto over = overview();
        const auto path = edge_pan_path(l.canvas, l.sources, view.spacing(), over, placements(), view.pan());
        const auto& p = view.pan();
        if (!path || p.from >= tiles.size() || p.from >= drawn.size()) return std::nullopt;
        Vec3 start = path->from;
        if (view.zoom() > p.zoom) {
            const auto e = edge_point(tiles[p.from], p.side, p.exit);
            start = place_point(l.canvas, tiles[p.from], drawn[p.from], e[0], e[1]);
            const float len = std::sqrt(start.x * start.x + start.y * start.y + start.z * start.z);
            if (!(len > 0)) return std::nullopt;
            start = {start.x / len, start.y / len, start.z / len};
        }
        const Vec3 d = edge_pan_dot(*path, start, view.pan_cross());
        return std::array<float, 2>{std::atan2(d.x, -d.z), std::atan2(d.y, std::sqrt(d.x * d.x + d.z * d.z))};
    }
    Mat3 step(double t) {  // wayland.cpp's order: the view, its landed turns (anchor only), the aim, the modes
        view.advance(t);
        view.take_landed_turns();
        if (!l.tracked) return Mat3{1, 0, 0, 0, 1, 0, 0, 0, 1};
        modes.set_aim(focus_aim());
        return view_from(Quat{}, modes.update(Quat{}, t));
    }
    std::vector<FRect> tiles() const { return blended_layout(l.sources, l.canvas.view_w, l.canvas.view_h, view.spacing(), view.zoom(), view.position(), view.left_zoom(), view.left()); }
    std::vector<Placement> drawn(const std::vector<FRect>& t) const {  // wayland.cpp resolved_placements: follow or locked, so no keep_size
        return zoomed_placements(l.canvas, overview(), t, placements(), view.zoom(), view.position(), false, view.left_zoom(), view.left());
    }
};

// One line of the protocol below, whatever needs no drawing. False if it is not one of these.
//   layout <view w> <h> <half w> <half h> <spacing> <mode: 1 locked, 2 follow, 0 untracked> <n> { <source w> <h> <x> <y> <distance> <curvature> <roll> } x n
//   start <selected>
//   pan <from> <to> <side> <exit> <entry> <peek> <cross> <turn> <way> <zoom> <t ms>   ViewAnimator::set_pan, in EdgePan's units
//   off <t ms>                                                                    set_pan with no pan: the hand let go
//   target <zoomed> <selected> <center> <cut> <pan> <t ms>                        ViewAnimator::set_target
//   tick <t ms>                                                                   one step of everything
// Time here is the run's plus 30 s, so a pan always starts long after the maximize settled.
inline bool control(const std::string& op, std::istringstream& in, Layout& layout, std::unique_ptr<Run>& run, Mat3& view) {
    if (op == "layout") {
        int mode = 2; std::size_t n = 0;
        in >> layout.canvas.view_w >> layout.canvas.view_h >> layout.canvas.half_w >> layout.canvas.half_h >> layout.spacing >> mode >> n;
        layout.mode = mode == 1 ? ViewMode::locked : ViewMode::follow;
        layout.tracked = mode != 0;
        layout.sources.assign(n, Size{1, 1});
        for (std::size_t i = 0; i < n; ++i) {
            auto& p = layout.placed[i];
            in >> layout.sources[i].w >> layout.sources[i].h >> p.x >> p.y >> p.distance >> p.curvature >> p.roll;
        }
    } else if (op == "start") {
        std::uint32_t selected = 0;
        in >> selected;
        run = std::make_unique<Run>(layout, selected);
    } else if (op == "pan") {
        EdgePan p; p.active = true;
        int side = 0; double t = 0;
        in >> p.from >> p.to >> side >> p.exit >> p.entry >> p.peek >> p.cross >> p.turn >> p.way >> p.zoom >> t;
        p.side = std::uint8_t(side);
        run->view.set_pan(p, t + 30000);
    } else if (op == "off") {
        double t = 0;
        in >> t;
        run->view.set_pan(EdgePan{}, t + 30000);
    } else if (op == "target") {
        int zoomed = 1, center = 0, cut = 0, pan = 0; std::uint32_t selected = 0; double t = 0;
        in >> zoomed >> selected >> center >> cut >> pan >> t;
        run->view.set_target(zoomed != 0, selected, std::uint32_t(layout.spacing), t + 30000, center, cut, pan);
    } else if (op == "tick") {
        double t = 0;
        in >> t;
        view = run->step(t + 30000);
    } else return false;
    return true;
}
}
