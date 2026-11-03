// Overview free pointer: the renderer side of tools/native_spike/free_pointer.py. Display only; offline.
//  * the `overview_pointer` control frame decodes strictly (shared fixture with the Python side), and a malformed payload
//    is the ignorable kind (dropped, never fatal);
//  * the `overview_geometry` status line is exactly what the supervisor's parser was tested against;
//  * the dot is a closed disc on the canvas plane at the requested pixel, the same every time (no drift or orbit), or, given
//    a direction (the supervisor always gives one: linear in the pointer, round you in anchor mode), the same disc turned
//    to that direction round you;
//  * its grey levels stay at or under the cards' 191/255 cap (micro-OLED image retention).
#include "xr/ipc.hpp"
#include "xr/view.hpp"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <tuple>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;

namespace {
std::string frame(const nlohmann::json& payload, std::uint64_t id = 7) {
    nlohmann::json j = {{"schema", 1}, {"request_id", id}, {"run_id", "run"}, {"session", "session"}, {"op", "overview_pointer"},
                        {"payload", payload}};
    return j.dump() + "\n";
}
// Canvas pixel of a point on the canvas plane (z = -1).
std::pair<float, float> pixel(const Canvas& c, const Vec3& v) {
    return {(v.x / c.half_w + 1) * c.view_w / 2, (1 - v.y / c.half_h) * c.view_h / 2};
}
}

int main(int argc, char** argv) try {
    if (argc != 2) { std::cerr << "usage: free_pointer_test FIXTURE.json\n"; return 2; }
    std::ifstream in(argv[1]);
    if (!in) { std::cerr << "cannot open " << argv[1] << "\n"; return 2; }
    const auto cases = nlohmann::json::parse(in);
    std::uint64_t id = 1;
    for (const auto& p : cases.at("valid")) {
        auto d = decode_control(frame(p, id++));
        CHECK(std::holds_alternative<ControlMessage>(d));
        const auto& m = std::get<ControlMessage>(d);
        CHECK(m.op == ControlOp::overview_pointer);
        const auto* v = std::get_if<OverviewPointerPayload>(&m.payload);
        CHECK(v && v->visible == p.at("visible").get<bool>() && v->held == p.at("held").get<bool>() &&
              v->x == p.at("x").get<std::int32_t>() && v->y == p.at("y").get<std::int32_t>());
        CHECK(v->directed == p.contains("az"));
        if (v->directed) CHECK(v->az == p.at("az").get<std::int32_t>() && v->el == p.at("el").get<std::int32_t>());
    }
    for (const auto& c : cases.at("invalid")) {
        auto d = decode_control(frame(c.at("payload"), id++));
        if (!std::holds_alternative<Error>(d)) { std::cerr << "accepted invalid payload (" << c.at("why") << ")\n"; return 1; }
        if (!c.at("payload").is_object()) continue;  // the envelope itself requires an object: that rejection is fatal
        CHECK(ignorable_control_error(std::get<Error>(d)));
        CHECK(std::get<Error>(d).code == std::string(kInvalidOverviewPointerCode));
    }
    {  // Bad framing around the op is still fatal; a dropped frame does not use up its request id.
        std::string unframed = frame(cases.at("valid")[0]);
        unframed.pop_back();
        auto d = decode_control(unframed);
        CHECK(std::holds_alternative<Error>(d) && !ignorable_control_error(std::get<Error>(d)));
        ControlQueue q("run", "session");
        auto dropped = q.push(frame(cases.at("invalid")[0].at("payload"), 9));
        CHECK(std::holds_alternative<Error>(dropped) && ignorable_control_error(std::get<Error>(dropped)));
        CHECK(std::holds_alternative<std::monostate>(q.push(frame(cases.at("valid")[1], 9))));
        CHECK(q.pop().has_value());
    }

    // The status line the supervisor parses (free_pointer.parse_overview_geometry is tested on the same `line`).
    const auto& g = cases.at("geometry");
    std::vector<FRect> tiles;
    for (const auto& t : g.at("tiles")) tiles.push_back({t[0].get<float>(), t[1].get<float>(), t[2].get<float>(), t[3].get<float>()});
    const auto line = overview_geometry_json(tiles, g.at("w").get<std::uint32_t>(), g.at("h").get<std::uint32_t>(),
                                             g.at("half")[0].get<float>(), g.at("half")[1].get<float>());
    if (line != g.at("line").get<std::string>()) { std::cerr << "overview_geometry line differs:\n" << line << "\n"; return 1; }
    CHECK(nlohmann::json::parse(line).at("overview_geometry").at("tiles").size() == 3);
    // ... and it matches the overview layout the renderer draws (a 1920x1200 view, three 16:9 desktops, spacing 40).
    const Size sizes[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}};
    const auto layout = overview_layout(sizes, 1920, 1200, 40);
    CHECK(overview_geometry_json(layout, 1920, 1200, 0.36f, 0.225f) == line);
    // Unusable inputs give no line rather than a wrong one.
    CHECK(overview_geometry_json({}, 1920, 1200, 0.36f, 0.225f).empty());
    CHECK(overview_geometry_json(layout, 0, 1200, 0.36f, 0.225f).empty());
    CHECK(overview_geometry_json(layout, 1920, 1200, 0.f, 0.225f).empty());
    CHECK(overview_geometry_json(layout, 1920, 1200, NAN, 0.225f).empty());
    const FRect bad[] = {{0, 0, -1, 10}};
    CHECK(overview_geometry_json(bad, 1920, 1200, 0.36f, 0.225f).empty());
    // The settled spacing, not the eased one, defines the overview.
    ViewAnimator view;
    view.set_target(false, 0, 120, 0);
    CHECK(std::fabs(view.target_spacing() - 120.f) < 1e-4f);

    // The dot: a closed fan on the canvas plane, centred where asked, exactly the radius, identical on every call.
    const Canvas canvas{1920, 1200, 0.36f, 0.225f};
    for (const auto& [x, y] : {std::pair{960.f, 600.f}, std::pair{-40.f, -12.f}, std::pair{2100.f, 300.f}}) {
        const auto fan = canvas_disc(canvas, x, y, kFreePointerRadiusPx);
        CHECK(fan.size() == std::size_t(kFreePointerSegments) + 2);
        for (const auto& v : fan) CHECK(std::fabs(v.z + 1.f) < 1e-6f);
        const auto [cx, cy] = pixel(canvas, fan[0]);
        CHECK(std::fabs(cx - x) < 1e-2f && std::fabs(cy - y) < 1e-2f);
        for (std::size_t i = 1; i < fan.size(); ++i) {
            const auto [px, py] = pixel(canvas, fan[i]);
            CHECK(std::fabs(std::hypot(px - x, py - y) - kFreePointerRadiusPx) < 1e-2f);
        }
        CHECK(fan[1].x == fan.back().x && fan[1].y == fan.back().y);  // closed
        const auto again = canvas_disc(canvas, x, y, kFreePointerRadiusPx);
        for (std::size_t i = 0; i < fan.size(); ++i) CHECK(fan[i].x == again[i].x && fan[i].y == again[i].y);
    }
    CHECK(canvas_disc({0, 0, 0.36f, 0.225f}, 1, 1, 6).empty());
    CHECK(canvas_disc(canvas, 1, 1, 0).empty());
    CHECK(canvas_disc(canvas, 1, 1, 6, 2).empty());

    // Given a direction the dot is drawn there: centred on it (behind you, overhead, or inside the view, where the
    // supervisor's linear direction is a little off the plane point's own), the same rigid disc (every rim point as far
    // from the centre, as seen from the eye, as on the plane), upright, and exactly canvas_disc when the direction is the
    // plane point's own.
    auto dir = [](const Vec3& v) {
        return std::pair{std::atan2(double(v.x), -double(v.z)), std::atan2(double(v.y), std::hypot(double(v.x), double(v.z)))};
    };
    auto angle = [](const Vec3& a, const Vec3& b) {
        const double d = (double(a.x) * b.x + double(a.y) * b.y + double(a.z) * b.z) /
                         (std::sqrt(double(a.x) * a.x + double(a.y) * a.y + double(a.z) * a.z) * std::sqrt(double(b.x) * b.x + double(b.y) * b.y + double(b.z) * b.z));
        return std::acos(std::clamp(d, -1.0, 1.0));
    };
    const double pi = M_PI;
    for (const auto& [x, y, az, el] : {std::tuple{2880.f, 600.f, float(pi), 0.f}, std::tuple{2400.f, 100.f, 2.1f, 0.5f},
                                        std::tuple{-700.f, -500.f, -2.8f, 1.45f}, std::tuple{960.f, 1700.f, 0.f, -1.3f},
                                        // inside the view: orbit.pointer_direction's (u * half_w, v * half_h)
                                        std::tuple{1500.f, 400.f, 0.2025f, 0.075f}, std::tuple{100.f, 1150.f, -0.3225f, -0.20625f}}) {
        const auto fan = direction_disc(canvas, x, y, az, el, kFreePointerRadiusPx);
        CHECK(fan.size() == std::size_t(kFreePointerSegments) + 2);
        const auto [a, e] = dir(fan[0]);
        CHECK(std::fabs(std::remainder(a - double(az), 2 * pi)) < 1e-4 || std::fabs(double(e)) > 1.5);
        CHECK(std::fabs(e - double(el)) < 1e-4);
        const auto flat = canvas_disc(canvas, std::clamp(x, 0.f, 1920.f), std::clamp(y, 0.f, 1200.f), kFreePointerRadiusPx);
        for (std::size_t i = 1; i < fan.size(); ++i) CHECK(std::fabs(angle(fan[0], fan[i]) - angle(flat[0], flat[i])) < 1e-5);
        CHECK(fan[1].x == fan.back().x && fan[1].y == fan.back().y);  // closed
    }
    for (const auto& [x, y] : {std::pair{1920.f, 600.f}, std::pair{0.f, 0.f}, std::pair{700.f, 1200.f}}) {
        const double wx = (2.0 * x / 1920 - 1) * 0.36, wy = (1 - 2.0 * y / 1200) * 0.225;
        const auto at_edge = direction_disc(canvas, x, y, float(std::atan2(wx, 1.0)), float(std::atan2(wy, std::hypot(wx, 1.0))),
                                            kFreePointerRadiusPx);
        const auto flat = canvas_disc(canvas, x, y, kFreePointerRadiusPx);
        for (std::size_t i = 0; i < flat.size(); ++i)
            CHECK(std::fabs(at_edge[i].x - flat[i].x) < 1e-5f && std::fabs(at_edge[i].y - flat[i].y) < 1e-5f && std::fabs(at_edge[i].z - flat[i].z) < 1e-5f);
    }
    CHECK(direction_disc({0, 0, 0.36f, 0.225f}, 1, 1, 0, 0, 6).empty());

    // OLED care: dim greys at or under the cards' cap; the ring is black, so the dot is never surrounded by light.
    constexpr float cap = 191.f / 255.f;
    CHECK(kFreePointerLevel > 0.f && kFreePointerLevel <= cap);
    CHECK(kFreePointerHeldLevel >= kFreePointerLevel && kFreePointerHeldLevel <= cap);
    CHECK(kFreePointerRadiusPx > 0.f && kFreePointerRingPx > 0.f && kFreePointerRadiusPx + kFreePointerRingPx <= 12.f);
    std::cout << "free_pointer: " << cases.at("valid").size() << " valid + " << cases.at("invalid").size()
              << " invalid overview_pointer cases, geometry line, dot geometry and levels ok\n";
    return 0;
} catch (const std::exception& e) {
    std::cerr << "free_pointer_test failed: " << e.what() << "\n";
    return 1;
}
