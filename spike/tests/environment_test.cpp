// The TRON grid's pure parts (xr/environment.hpp) and its control frame (ipc.hpp EnvironmentPayload):
//   * the line colours: cyan, or the theme accent's hue as a neon, every one under the 191/255 OLED cap (its brightest
//     channel on it), a grey theme grey, odd input (black, NaN) still a valid colour;
//   * the fade: 0 until switched on, eased to 1 over kEnvironmentFadeMs, continuous when reversed mid-fade, a colour
//     crossfade over kEnvironmentColorMs, and nothing left to draw once faded out;
//   * the view: world-locked and level whatever the recentre reference was (the world's up is gravity's up, the heading
//     is the reference's), equal to the plain world view when the reference is level;
//   * the `environment` frame: every valid fixture payload decodes, every invalid one is dropped with
//     kInvalidEnvironmentCode, which the renderer ignores without stopping (main.cpp).
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

void colours() {
    for (const MdRgb accent : {MdRgb{0x7a / 255.f, 0xa2 / 255.f, 0xf7 / 255.f}, MdRgb{0.f, 0.f, 0.f}, MdRgb{1.f, 1.f, 1.f}})
        CHECK(environment_line_color(EnvironmentColor::cyan, accent) == kEnvironmentCyan);
    CHECK(near(most(kEnvironmentCyan), kEnvironmentCap) && kEnvironmentCyan[0] == 0.f && kEnvironmentCyan[2] > kEnvironmentCyan[1]);
    // Theme colours keep their hue, stay colourful, and sit on the cap.
    for (const MdRgb accent : {MdRgb{0x7a / 255.f, 0xa2 / 255.f, 0xf7 / 255.f}, MdRgb{0xf7 / 255.f, 0x76 / 255.f, 0x8e / 255.f},
                               MdRgb{0x9e / 255.f, 0xce / 255.f, 0x6a / 255.f}, MdRgb{0xe0 / 255.f, 0xaf / 255.f, 0x68 / 255.f},
                               MdRgb{0.25f, 0.1f, 0.4f}}) {
        const auto c = environment_line_color(EnvironmentColor::theme, accent);
        const auto a = md_hct(accent), h = md_hct(c);
        const float dh = std::fabs(std::remainder(a.hue - h.hue, 360.f));
        if (dh > 12.f) std::fprintf(stderr, "theme hue %.1f -> %.1f\n", a.hue, h.hue);
        CHECK(dh <= 12.f);
        CHECK(h.chroma >= 0.05f);  // still a colour (as much of its chroma as fits the gamut at the neon tone)
        CHECK(near(most(c), kEnvironmentCap, 1e-3f) && std::min({c[0], c[1], c[2]}) >= 0.f);
    }
    // A grey theme gives a grey grid, black a grey one too; garbage stays a valid colour.
    const auto grey = environment_line_color(EnvironmentColor::theme, {0x8d / 255.f, 0x8d / 255.f, 0x8d / 255.f});
    CHECK(near(grey[0], grey[1], 3e-3f) && near(grey[1], grey[2], 3e-3f) && near(most(grey), kEnvironmentCap, 1e-3f));
    const auto black = environment_line_color(EnvironmentColor::theme, {0, 0, 0});
    CHECK(near(most(black), kEnvironmentCap, 1e-3f));
    const float nan = std::numeric_limits<float>::quiet_NaN();
    const auto odd = environment_line_color(EnvironmentColor::theme, {nan, 2.f, -1.f});
    for (float v : odd) CHECK(std::isfinite(v) && v >= 0.f && v <= kEnvironmentCap + 1e-4f);
}

void fade() {
    EnvironmentFade f;
    CHECK(f.level(0) == 0.f && !f.animating(0) && !f.on());
    CHECK(f.set(false, kEnvironmentCyan, 0) == false);  // nothing changes
    CHECK(f.set(true, kEnvironmentCyan, 1000));
    CHECK(f.level(1000) == 0.f && f.animating(1000));
    CHECK(near(f.level(1000 + kEnvironmentFadeMs / 2), 0.5f));
    CHECK(f.level(1000 + kEnvironmentFadeMs) == 1.f && !f.animating(1000 + kEnvironmentFadeMs) && f.level(5000) == 1.f);
    float last = 0;
    for (double t = 1000; t <= 1000 + kEnvironmentFadeMs; t += 10) { CHECK(f.level(t) + 1e-6f >= last); last = f.level(t); }
    CHECK(!f.set(true, kEnvironmentCyan, 2000));  // already on
    // Off mid-fade-in: the fade turns round from where it was, with no jump.
    EnvironmentFade g;
    g.set(true, kEnvironmentCyan, 0);
    const float at = g.level(200);
    CHECK(g.set(false, kEnvironmentCyan, 200));
    CHECK(near(g.level(200), at) && g.level(200 + kEnvironmentFadeMs) == 0.f);
    CHECK(!g.animating(200 + kEnvironmentFadeMs));
    // Colour: the first one is taken as is; a change crossfades.
    EnvironmentFade h;
    const MdRgb red{kEnvironmentCap, 0, 0};
    h.set(true, red, 0);
    CHECK(h.color(0) == red);
    CHECK(h.set(true, kEnvironmentCyan, 1000));
    CHECK(h.color(1000) == red && h.color(1000 + kEnvironmentColorMs) == kEnvironmentCyan && h.animating(1000));
    const auto mid = h.color(1000 + kEnvironmentColorMs / 2);
    CHECK(near(mid[0], kEnvironmentCap / 2, 1e-3f) && near(mid[2], kEnvironmentCap / 2, 1e-3f));
    CHECK(!h.animating(1000 + kEnvironmentColorMs));
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
    auto frame = [](const nlohmann::json& payload) {
        return nlohmann::json{{"schema", 1}, {"request_id", 7}, {"run_id", "r1"}, {"session", "s1"}, {"op", "environment"},
                              {"payload", payload}}.dump() + "\n";
    };
    for (const auto& v : data["valid"]) {
        const auto decoded = decode_control(frame(v["payload"]));
        const auto* m = std::get_if<ControlMessage>(&decoded);
        CHECK(m && m->op == ControlOp::environment);
        if (!m) continue;
        const auto& p = std::get<EnvironmentPayload>(m->payload);
        CHECK(p.on == v["on"].get<bool>() && p.color == v["color"].get<int>());
    }
    for (const auto& v : data["invalid"]) {
        const auto decoded = decode_control(frame(v));
        const auto* e = std::get_if<Error>(&decoded);
        CHECK(e && e->code == kInvalidEnvironmentCode && ignorable_control_error(*e));
    }
    // Through the queue: a dropped payload is no replay and stops nothing; a good one after it is taken.
    ControlQueue q("r1", "s1");
    const auto bad = q.push(frame(nlohmann::json{{"on", 1}, {"color", "cyan"}}));
    CHECK(std::holds_alternative<Error>(bad));
    CHECK(std::holds_alternative<std::monostate>(q.push(frame(data["valid"][0]["payload"]))));
    const auto m = q.pop();
    CHECK(m && m->op == ControlOp::environment);
}
}  // namespace

int main(int argc, char** argv) {
    colours();
    fade();
    views();
    frames(argc > 1 ? argv[1] : "../tests/native_spike/fixtures/environment_payloads.json");
    if (failures) { std::fprintf(stderr, "%d failures\n", failures); return 1; }
    std::puts("environment test passed");
    return 0;
}
