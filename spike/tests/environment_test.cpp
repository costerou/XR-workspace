// The background's pure parts (xr/environment.hpp) and its control frames (ipc.hpp EnvironmentPayload, PlacementMenuPayload):
//   * the backgrounds' table;
//   * the line colour: the theme's own, scaled only to stay under the 191/255 OLED cap or to show at all, any input valid;
//   * the fade: 0 until switched on, eased to 1 over kEnvironmentFadeMs, continuous when reversed mid-fade;
//   * the view: world-locked and level whatever the recentre reference was (the world's up is gravity's up, the heading
//     is the reference's), equal to the plain world view when the reference is level;
//   * the `environment` frame: every valid fixture payload decodes, every invalid one is dropped with
//     kInvalidEnvironmentCode, which the renderer ignores without stopping (main.cpp);
//   * the placement panel's background fields on a `placement_menu` frame.
#include "xr/environment.hpp"
#include "xr/ipc.hpp"
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <limits>
#include <string>
#include <nlohmann/json.hpp>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }
float most(const MdRgb& c) { return std::max({c[0], c[1], c[2]}); }
Quat axis_angle(float x, float y, float z, float deg) {
    const float h = deg * float(M_PI) / 360.f, n = std::sqrt(x * x + y * y + z * z);
    return Quat{x / n * std::sin(h), y / n * std::sin(h), z / n * std::sin(h), std::cos(h)};
}
// Driver frame (x forward, y left, z up) to GL (x right, y up, z back), as pose.cpp kDriverToGl.
std::array<float, 3> to_gl(std::array<float, 3> v) { return {-v[1], v[2], -v[0]}; }

void backgrounds() {
    CHECK(kBackgroundCount >= 2);
    CHECK(kBackgrounds[std::size_t(Background::none)].name == "none" && kBackgrounds[std::size_t(Background::grid)].name == "grid");
    CHECK(background_index("none") == 0 && background_index("grid") == std::size_t(Background::grid));
    CHECK(background_index("Grid") == kBackgroundCount && background_index("") == kBackgroundCount);
    for (const auto& b : kBackgrounds) CHECK(!b.label.empty() && b.label.size() <= 24);  // the quality menu's kMaxQualityText
    CHECK(kBrightnessMin < kBrightnessDefault && kBrightnessDefault < kBrightnessMax);
}

void colours() {
    // In range: the theme colour itself.
    for (const MdRgb accent : {MdRgb{0.4f, 0.5f, 0.6f}, MdRgb{kEnvironmentCap, 0.1f, 0.3f}, MdRgb{0.3f, kEnvironmentFloor, 0.f}})
        CHECK(environment_line_color(accent) == accent);
    // Over the cap: all three scale together, so hue and saturation stay; the brightest sits on the cap.
    for (const MdRgb accent : {MdRgb{0x7a / 255.f, 0xa2 / 255.f, 0xf7 / 255.f}, MdRgb{0xf7 / 255.f, 0x76 / 255.f, 0x8e / 255.f},
                               MdRgb{0x9e / 255.f, 0xce / 255.f, 0x6a / 255.f}, MdRgb{1.f, 1.f, 1.f}, MdRgb{0.f, 1.f, 0.f}}) {
        const auto c = environment_line_color(accent);
        CHECK(near(most(c), kEnvironmentCap, 1e-5f));
        const float k = kEnvironmentCap / most(accent);
        for (int i = 0; i < 3; ++i) CHECK(near(c[std::size_t(i)], accent[std::size_t(i)] * k, 1e-5f));
    }
    // A near-black theme colour would not show on the see-through panels: lifted, its hue kept; black is grey.
    const MdRgb dark{0.02f, 0.04f, 0.08f};
    const auto lifted = environment_line_color(dark);
    CHECK(near(most(lifted), kEnvironmentFloor, 1e-5f) && near(lifted[0] / lifted[2], dark[0] / dark[2], 1e-4f));
    const auto black = environment_line_color({0, 0, 0});
    CHECK(near(black[0], kEnvironmentFloor) && black[0] == black[1] && black[1] == black[2]);
    const float nan = std::numeric_limits<float>::quiet_NaN();
    for (float v : environment_line_color({nan, 2.f, -1.f})) CHECK(std::isfinite(v) && v >= 0.f && v <= kEnvironmentCap + 1e-4f);
}

void fade() {
    EnvironmentFade f;
    CHECK(f.level(0) == 0.f && !f.animating(0) && !f.on());
    CHECK(f.set(false, 0) == false);  // nothing changes
    CHECK(f.set(true, 1000));
    CHECK(f.level(1000) == 0.f && f.animating(1000));
    CHECK(near(f.level(1000 + kEnvironmentFadeMs / 2), 0.5f));
    CHECK(f.level(1000 + kEnvironmentFadeMs) == 1.f && !f.animating(1000 + kEnvironmentFadeMs) && f.level(5000) == 1.f);
    float last = 0;
    for (double t = 1000; t <= 1000 + kEnvironmentFadeMs; t += 10) { CHECK(f.level(t) + 1e-6f >= last); last = f.level(t); }
    CHECK(!f.set(true, 2000));  // already on
    // Off mid-fade-in: the fade turns round from where it was, with no jump.
    EnvironmentFade g;
    g.set(true, 0);
    const float at = g.level(200);
    CHECK(g.set(false, 200));
    CHECK(near(g.level(200), at) && g.level(200 + kEnvironmentFadeMs) == 0.f);
    CHECK(!g.animating(200 + kEnvironmentFadeMs));
}

void views() {
    const Mat3 identity{1, 0, 0, 0, 1, 0, 0, 0, 1};
    auto same = [](const Mat3& a, const Mat3& b, float eps = 1e-5f) { for (int i = 0; i < 9; ++i) if (!near(a[i], b[i], eps)) return false; return true; };
    CHECK(same(environment_view(Quat{}, std::nullopt), identity));
    CHECK(same(environment_view(Quat{}, Quat{}), identity));
    // A level reference (any heading): the plain world view of the recentre reference.
    for (float yaw : {0.f, 30.f, -120.f, 179.f}) {
        const Quat ref = axis_angle(0, 0, 1, yaw);
        for (const Quat rel : {Quat{}, axis_angle(0, 0, 1, 40), axis_angle(0, 1, 0, -25), axis_angle(1, 2, 3, 70)})
            CHECK(same(environment_view(rel, ref), view_from(rel, Quat{})));
    }
    // Any reference: the world's up (GL y) is gravity's (driver z) as the head sees it, and the reference's own heading is
    // ahead: recentred with the head tipped, a level head sees a level floor.
    for (const Quat ref : {axis_angle(0, 1, 0, 20), axis_angle(1, 0, 0, -15), axis_angle(1, 1, 0.3f, 35), axis_angle(0.2f, -1, 0.5f, 80)})
        for (const Quat rel : {Quat{}, axis_angle(0, 0, 1, 60), axis_angle(0, 1, 0, -30), axis_angle(1, -1, 2, 50)}) {
            const Mat3 v = environment_view(rel, ref);
            const Quat head = quat_mul(ref, rel);  // absolute, driver frame
            const auto up = to_gl(apply(transpose(quat_to_mat3(head)), {0, 0, 1}));  // gravity up, in the head's frame
            const auto got = apply(v, {0, 1, 0});
            for (int i = 0; i < 3; ++i) CHECK(near(got[i], up[i], 1e-4f));
        }
    // Pitched (not turned) at recentre and still there: the view is that pitch alone, about the world's right axis.
    const Quat pitched = axis_angle(0, 1, 0, 25);
    const auto right = apply(environment_view(Quat{}, pitched), {1, 0, 0});
    CHECK(near(right[0], 1.f) && near(right[1], 0.f) && near(right[2], 0.f));
    // A heading: the forward axis's turn about gravity, nothing else; straight up has none.
    const Quat h = heading(quat_mul(axis_angle(0, 0, 1, 50), axis_angle(0, 1, 0, 30)));
    CHECK(near(h.x, 0.f) && near(h.y, 0.f) && near(std::atan2(h.z, h.w) * 2 * 180 / float(M_PI), 50.f, 1e-2f));
    const Quat up = heading(axis_angle(0, 1, 0, -90));
    CHECK(near(up.w, 1.f) && near(up.z, 0.f));
}

void frames(const char* fixture) {
    std::ifstream in(fixture);
    if (!in) { std::fprintf(stderr, "cannot read %s\n", fixture); ++failures; return; }
    const auto data = nlohmann::json::parse(in);
    auto frame = [](const char* op, const nlohmann::json& payload) {
        return nlohmann::json{{"schema", 1}, {"request_id", 7}, {"run_id", "r1"}, {"session", "s1"}, {"op", op}, {"payload", payload}}.dump() + "\n";
    };
    for (const auto& v : data["valid"]) {
        const auto decoded = decode_control(frame("environment", v["payload"]));
        const auto* m = std::get_if<ControlMessage>(&decoded);
        CHECK(m && m->op == ControlOp::environment);
        if (!m) continue;
        const auto& p = std::get<EnvironmentPayload>(m->payload);
        CHECK(p.background == v["background"].get<int>() && p.brightness == v["brightness"].get<int>());
    }
    for (const auto& v : data["invalid"]) {
        const auto decoded = decode_control(frame("environment", v));
        const auto* e = std::get_if<Error>(&decoded);
        CHECK(e && e->code == kInvalidEnvironmentCode && ignorable_control_error(*e));
    }
    // Through the queue: a dropped payload is no replay and stops nothing; a good one after it is taken.
    ControlQueue q("r1", "s1");
    const auto bad = q.push(frame("environment", nlohmann::json{{"background", 1}, {"brightness", 100}}));
    CHECK(std::holds_alternative<Error>(bad));
    CHECK(std::holds_alternative<std::monostate>(q.push(frame("environment", data["valid"][0]["payload"]))));
    const auto m = q.pop();
    CHECK(m && m->op == ControlOp::environment);
    // The placement panel's fields.
    const nlohmann::json placement{{"x", 0}, {"y", 0}, {"distance", 100}, {"curvature", 0}};
    auto menu = [&](nlohmann::json extra) {
        nlohmann::json p{{"open", true}, {"desktop", 0}, {"field", "x"}, {"placements", nlohmann::json::array({placement})}};
        for (const auto& [k, v] : extra.items()) p[k] = v;
        return decode_control(frame("placement_menu", p));
    };
    auto payload = [](const Result<ControlMessage>& r) { return std::get<PlacementMenuPayload>(std::get<ControlMessage>(r).payload); };
    const auto plain = menu(nlohmann::json::object());
    CHECK(payload(plain).background == 0 && payload(plain).brightness == kBrightnessDefault && !payload(plain).background_picker);
    const auto full = menu({{"background", "grid"}, {"brightness", 40}, {"background_picker", true}, {"field", "brightness"}});
    CHECK(payload(full).background == 1 && payload(full).brightness == 40 && payload(full).background_picker &&
          payload(full).field == kPlacementMenuBrightness);
    for (const auto& bad_field : {nlohmann::json{{"background", "nope"}}, nlohmann::json{{"brightness", 5}}, nlohmann::json{{"brightness", "40"}},
                                  nlohmann::json{{"background_picker", 1}}, nlohmann::json{{"background_picker", true}, {"picker", true}},
                                  nlohmann::json{{"background_picker", true}, {"open", false}}, nlohmann::json{{"field", "gain"}}}) {
        const auto e = menu(bad_field);
        CHECK(std::holds_alternative<Error>(e) && std::get<Error>(e).code == kInvalidMenuCode);
    }
}
}  // namespace

int main(int argc, char** argv) {
    backgrounds();
    colours();
    fade();
    views();
    frames(argc > 1 ? argv[1] : "../tests/native_spike/fixtures/environment_payloads.json");
    if (failures) { std::fprintf(stderr, "%d failures\n", failures); return 1; }
    std::puts("environment test passed");
    return 0;
}
