// Immersive 3D's renderer side (tools/native_spike/immersive.py owns the feature):
//   * the `immersive` frame: every valid fixture payload decodes, every invalid one is dropped with kInvalidImmersiveCode,
//     which the renderer ignores without stopping (main.cpp), like a malformed `roll`;
//   * the 3D button's place (Connected Tail, option 02 of 3d-button-mockups): 2 px right of the level bar, a 40 px segment and
//     a 28 px tail, on neither content nor a corner, and absent on a desktop too narrow to hold it;
//   * its pixels: the mockup's M3 roles, four states of each control in the handle atlas, none over the OLED cap, nothing
//     leaking out of their cells, the small inner corners and the tail's round end.
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/ipc.hpp"
#include "xr/material_draw.hpp"
#include <nlohmann/json.hpp>
#include <cmath>
#include <cstdlib>
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
        CHECK(p.stereo == v["stereo"].get<bool>() && p.hover == v["hover"].get<int>() && p.tail == v["tail"].get<bool>() && p.global == v["every"].get<bool>());
    }
    for (const auto& v : data["invalid"]) {
        const auto decoded = decode_control(frame(v));
        const auto* e = std::get_if<Error>(&decoded);
        CHECK(e && e->code == kInvalidImmersiveCode && ignorable_control_error(*e));
    }
}

void place() {
    for (const FRect t : {FRect{0, 500, 1920, 1080}, FRect{100, 300, 586.67f, 330}, FRect{100, 300, 440, 247}, FRect{-200.5f, 410.25f, 812, 457}}) {
        const FRect m = immersive_main_rect(t), l = immersive_tail_rect(t), bar = level_bar_rect(t);
        const float s = level_bar_scale(t), pad = kLevelBarPad * s;
        CHECK(m.width > 0 && l.width > 0);
        CHECK(std::fabs(m.y - bar.y) < 1e-3f && std::fabs(m.height - bar.height) < 1e-3f && std::fabs(l.y - bar.y) < 1e-3f && std::fabs(l.height - bar.height) < 1e-3f);
        CHECK(std::fabs(m.x - (bar.x + bar.width + 2 * s)) < 1e-3f && std::fabs(m.width - 40 * s) < 1e-3f);
        CHECK(std::fabs(l.x - (m.x + m.width + 2 * s)) < 1e-3f && std::fabs(l.width - 28 * s) < 1e-3f);
        CHECK(l.x + l.width <= t.x + t.width - kCapInner * s + 1e-3f);     // short of the corner's cap
        CHECK(m.y + m.height + pad <= t.y - kLevelEdgeClear * s + 1e-3f);  // clear of the edge's band, so of content too
        const float cy = m.y + m.height / 2, gap1 = bar.x + bar.width + s, gap2 = m.x + m.width + s;
        CHECK(immersive_part(t, m.x + m.width / 2, cy) == ImmersivePart::main);
        CHECK(immersive_part(t, l.x + l.width / 2, cy) == ImmersivePart::tail);
        CHECK(immersive_part(t, gap1 + 0.1f * s, cy) == ImmersivePart::main && immersive_part(t, gap1 - 0.1f * s, cy) == ImmersivePart::none);
        CHECK(immersive_part(t, gap2 - 0.1f * s, cy) == ImmersivePart::main && immersive_part(t, gap2 + 0.1f * s, cy) == ImmersivePart::tail);
        CHECK(immersive_part(t, l.x + l.width + pad - 0.1f * s, cy) == ImmersivePart::tail && immersive_part(t, l.x + l.width + pad + 0.1f * s, cy) == ImmersivePart::none);
        CHECK(immersive_part(t, m.x + 1, m.y - pad + 0.1f * s) == ImmersivePart::main && immersive_part(t, m.x + 1, m.y - pad - 0.1f * s) == ImmersivePart::none);
        CHECK(immersive_part(t, m.x + m.width / 2, t.y + 5) == ImmersivePart::none);  // on the desktop: its content's
    }
    for (const FRect t : {FRect{10, 20, 120, 60}, FRect{5, 5, 40, 30}, FRect{0, 0, 0, 0}}) {
        CHECK(!(immersive_main_rect(t).width > 0) && !(immersive_tail_rect(t).width > 0));
        CHECK(immersive_part(t, t.x + t.width / 2, t.y - 20) == ImmersivePart::none);
    }
}

struct Cell { std::uint32_t x0, y0, x1, y1; };
Cell cell_of(const HudImage& atlas, const AtlasBox& box) {
    return {std::uint32_t(std::lround(box.u0 * float(atlas.width))), std::uint32_t(std::lround(box.v0 * float(atlas.height))),
            std::uint32_t(std::lround(box.u1 * float(atlas.width))), std::uint32_t(std::lround(box.v1 * float(atlas.height)))};
}
const std::uint8_t* at(const HudImage& im, std::uint32_t x, std::uint32_t y) { return &im.rgba[(std::size_t(y) * im.width + x) * 4]; }
// A pixel of the container: its colour exactly, as md_over rounds it.
bool is(const HudImage& im, std::uint32_t x, std::uint32_t y, MdRgb c) {
    const auto* p = at(im, x, y);
    return p[3] == 255 && std::abs(int(p[0]) - int(std::lround(c[0] * 255))) <= 1 && std::abs(int(p[1]) - int(std::lround(c[1] * 255))) <= 1 &&
           std::abs(int(p[2]) - int(std::lround(c[2] * 255))) <= 1;
}

void pixels() {
    const std::array<float, 3> accent{0.48f, 0.64f, 0.97f};
    const auto& m = glass_theme_scheme(accent);
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    const std::array<const HudFont*, 2> fonts{nullptr, font.get()};  // the pixel font, and the real one where it is installed
    for (const HudFont* f : fonts) {
        const HudImage atlas = render_handle_atlas(accent, f);
        std::set<std::vector<std::uint8_t>> looks;
        for (const bool tail : {false, true})
            for (const bool lit : {false, true})
                for (const bool hot : {false, true}) {
                    const Cell c = cell_of(atlas, tail ? immersive_tail_box(lit, hot) : immersive_main_box(lit, hot));
                    const std::uint32_t w = c.x1 - c.x0, h = c.y1 - c.y0;
                    CHECK(w == std::uint32_t(tail ? kImmersiveTailW : kImmersiveMainW) * kHandleAtlasScale && h == std::uint32_t(kLevelBarH) * kHandleAtlasScale);
                    CHECK(c.x1 < atlas.width && c.y1 < atlas.height);
                    std::vector<std::uint8_t> cell;
                    std::uint32_t ink = 0;
                    const MdRgb fill = lit ? (hot ? m.primary_bright : m.primary) : hot ? m.primary_container : m.secondary_container;
                    for (std::uint32_t y = c.y0; y < c.y1; ++y)
                        for (std::uint32_t x = c.x0; x < c.x1; ++x) {
                            const auto* p = at(atlas, x, y);
                            for (int n = 0; n < 3; ++n) CHECK(p[n] <= 191);  // the OLED cap
                            cell.insert(cell.end(), p, p + 4);
                            ink += p[3] == 255 && !is(atlas, x, y, fill);
                        }
                    CHECK(is(atlas, c.x0 + 6, c.y0 + h / 2, fill));  // the container's role, left of its content
                    CHECK(ink > 200);                                // the 3D, or the tail's three desktops, on it
                    for (std::uint32_t x = c.x0; x < c.x1; ++x)  // the gutter stays empty, so bilinear filtering stays in the cell
                        CHECK(at(atlas, x, c.y0 - 1)[3] == 0 && at(atlas, x, c.y1)[3] == 0);
                    // Small inner corners on the left; on the right the same for the segment, the capsule's end for the tail.
                    CHECK(at(atlas, c.x0, c.y0)[3] < 64 && at(atlas, c.x0 + 12, c.y0)[3] == 255 && at(atlas, c.x0, c.y0 + 12)[3] == 255);
                    for (std::uint32_t x = c.x0 + 12; tail && x < c.x0 + w / 2; ++x) CHECK(at(atlas, x, c.y0)[3] == 255);  // its top edge is unbroken
                    if (tail) CHECK(at(atlas, c.x1 - 12, c.y0)[3] < 64 && at(atlas, c.x1 - 1, c.y0 + h / 2)[3] >= 250);
                    else CHECK(at(atlas, c.x1 - 13, c.y0)[3] == 255);
                    looks.insert(cell);
                }
        CHECK(looks.size() == 8);
    }
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) { std::fprintf(stderr, "usage: immersive_test FIXTURE\n"); return 2; }
    frames(argv[1]);
    place();
    pixels();
    return failures ? 1 : 0;
}
