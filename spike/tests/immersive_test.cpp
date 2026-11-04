// Immersive 3D's renderer side (tools/native_spike/immersive.py owns the feature):
//   * the `immersive` frame: every valid fixture payload decodes, every invalid one is dropped with kInvalidImmersiveCode,
//     which the renderer ignores without stopping (main.cpp), like a malformed `roll`;
//   * the 3D button's place: beside the level bar, never touching it, on neither content nor a corner, and absent on a
//     desktop too narrow to hold it;
//   * its pixels: four distinct states in the handle atlas, none over the OLED cap, nothing leaking out of its cells.
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/ipc.hpp"
#include <nlohmann/json.hpp>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <set>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

void frames(const char* fixture) {
    std::ifstream in(fixture);
    if (!in) { std::fprintf(stderr, "cannot read %s\n", fixture); ++failures; return; }
    const auto data = nlohmann::json::parse(in);
    auto frame = [](const nlohmann::json& payload) {
        return nlohmann::json{{"schema", 1}, {"request_id", 7}, {"run_id", "r1"}, {"session", "s1"}, {"op", "immersive"}, {"payload", payload}}.dump() + "\n";
    };
    for (const auto& v : data["valid"]) {
        const auto decoded = decode_control(frame(v["payload"]));
        const auto* m = std::get_if<ControlMessage>(&decoded);
        CHECK(m && m->op == ControlOp::immersive);
        if (!m) continue;
        const auto& p = std::get<ImmersivePayload>(m->payload);
        for (std::size_t i = 0; i < kMaxDesktops; ++i) CHECK(p.desktops[i] == v["payload"]["desktops"][i].get<bool>());
        CHECK(p.stereo == v["stereo"].get<bool>() && p.hover == v["hover"].get<int>());
    }
    for (const auto& v : data["invalid"]) {
        const auto decoded = decode_control(frame(v));
        const auto* e = std::get_if<Error>(&decoded);
        CHECK(e && e->code == kInvalidImmersiveCode && ignorable_control_error(*e));
    }
}

void place() {
    for (const FRect t : {FRect{0, 500, 1920, 1080}, FRect{100, 300, 586.67f, 330}, FRect{100, 300, 440, 247}, FRect{-200.5f, 410.25f, 812, 457}}) {
        const FRect b = immersive_button_rect(t), bar = level_bar_rect(t);
        const float s = level_bar_scale(t), pad = kLevelBarPad * s;
        CHECK(b.width > 0 && std::fabs(b.y - bar.y) < 1e-3f && std::fabs(b.height - bar.height) < 1e-3f);
        CHECK(std::fabs(b.x - (bar.x + bar.width + kImmersiveButtonGap * s)) < 1e-3f);
        CHECK(b.x - pad > bar.x + bar.width + pad);                        // the two hit regions never touch
        CHECK(b.x + b.width <= t.x + t.width - kCapInner * s + 1e-3f);     // short of the corner's cap
        CHECK(b.y + b.height + pad <= t.y - kLevelEdgeClear * s + 1e-3f);  // clear of the edge's band, so of content too
        CHECK(on_immersive_button(t, b.x + b.width / 2, b.y + b.height / 2));
        CHECK(!on_immersive_button(t, b.x + b.width / 2, t.y + 5));        // on the desktop: its content's
        CHECK(level_bar_segment(t, b.x + b.width / 2, b.y + b.height / 2) == LevelSegment::none);
    }
    for (const FRect t : {FRect{10, 20, 120, 60}, FRect{5, 5, 40, 30}, FRect{0, 0, 0, 0}}) {
        CHECK(!(immersive_button_rect(t).width > 0));
        CHECK(!on_immersive_button(t, t.x + t.width / 2, t.y - 20));
    }
}

void pixels() {
    const HudImage atlas = render_handle_atlas({0.48f, 0.64f, 0.97f});
    std::set<std::vector<std::uint8_t>> looks;
    for (const bool on : {false, true})
        for (const bool hot : {false, true}) {
            const AtlasBox box = immersive_button_box(on, hot);
            const auto x0 = std::uint32_t(std::lround(box.u0 * float(atlas.width))), y0 = std::uint32_t(std::lround(box.v0 * float(atlas.height)));
            const auto x1 = std::uint32_t(std::lround(box.u1 * float(atlas.width))), y1 = std::uint32_t(std::lround(box.v1 * float(atlas.height)));
            CHECK(x1 - x0 == std::uint32_t(kImmersiveButtonW) * kHandleAtlasScale && y1 - y0 == std::uint32_t(kLevelBarH) * kHandleAtlasScale);
            CHECK(x1 < atlas.width && y1 < atlas.height);
            std::vector<std::uint8_t> cell;
            for (std::uint32_t y = y0; y < y1; ++y)
                for (std::uint32_t x = x0; x < x1; ++x) {
                    const auto* p = &atlas.rgba[(std::size_t(y) * atlas.width + x) * 4];
                    for (int c = 0; c < 3; ++c) CHECK(p[c] <= 191);  // the OLED cap
                    cell.insert(cell.end(), p, p + 4);
                }
            for (std::uint32_t x = x0; x < x1; ++x)  // the gutter stays empty, so bilinear filtering stays in the cell
                CHECK(atlas.rgba[(std::size_t(y0 - 1) * atlas.width + x) * 4 + 3] == 0 && atlas.rgba[(std::size_t(y1) * atlas.width + x) * 4 + 3] == 0);
            looks.insert(cell);
        }
    CHECK(looks.size() == 4);
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) { std::fprintf(stderr, "usage: immersive_test FIXTURE\n"); return 2; }
    frames(argv[1]);
    place();
    pixels();
    return failures ? 1 : 0;
}
