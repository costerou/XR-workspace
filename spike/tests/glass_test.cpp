// Glass pills (glass.hpp): geometry, the morph, the look's OLED cap, the panel content and the expand animation.
#include "xr/glass.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <string>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
bool near(float a, float b, float eps = 1e-3f) { return std::fabs(a - b) <= eps; }
std::uint8_t brightest(const HudImage& im) {
    std::uint8_t top = 0;
    for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) top = std::max(top, im.rgba[i + std::size_t(c)]);
    return top;
}
bool premultiplied(const HudImage& im) {
    for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) if (im.rgba[i + std::size_t(c)] > im.rgba[i + 3]) return false;
    return true;
}
// Columns whose pixels differ between two same-sized images, as contiguous runs.
std::vector<std::pair<std::uint32_t, std::uint32_t>> changed_columns(const HudImage& a, const HudImage& b, std::uint32_t y0, std::uint32_t y1) {
    std::vector<std::pair<std::uint32_t, std::uint32_t>> runs;
    for (std::uint32_t x = 0; x < a.width; ++x) {
        bool diff = false;
        for (std::uint32_t y = y0; y < y1 && !diff; ++y)
            for (int c = 0; c < 4; ++c) diff |= a.rgba[(std::size_t(y) * a.width + x) * 4 + std::size_t(c)] != b.rgba[(std::size_t(y) * b.width + x) * 4 + std::size_t(c)];
        if (!diff) continue;
        if (!runs.empty() && runs.back().second + 1 == x) runs.back().second = x; else runs.push_back({x, x});
    }
    return runs;
}
}

int main() {
    // Geometry: the pill is centred kGlassPillGap under the desktop, a share of its width within bounds, never wider
    // than it; the panel shares the pill's top and centre; the morph runs from one to the other.
    const FRect tile{100, 300, 459, 258};
    const FRect pill = glass_pill(tile);
    CHECK(near(pill.y, tile.y + tile.height + kGlassPillGap) && near(pill.height, kGlassPillH));
    CHECK(near(pill.x + pill.width / 2, tile.x + tile.width / 2) && near(pill.width, kGlassPillShare * tile.width));
    CHECK(near(glass_pill({0, 0, 4000, 2000}).width, kGlassPillMaxW) && near(glass_pill({0, 0, 120, 60}).width, kGlassPillMinW));
    CHECK(glass_pill({0, 0, 40, 30}).width <= 40.f && glass_pill({0, 0, 0, 30}).width == 0.f);
    const FRect panel = glass_panel(tile);
    CHECK(near(panel.width, kPanelW) && near(panel.height, kPanelH) && near(panel.y, pill.y) &&
          near(panel.x + panel.width / 2, pill.x + pill.width / 2));
    for (float t : {0.f, 0.25f, 0.5f, 1.f}) {
        const FRect m = glass_morph(tile, t);
        CHECK(near(m.y, pill.y) && near(m.x + m.width / 2, pill.x + pill.width / 2));
        CHECK(glass_radius(tile, t) <= std::min(m.width, m.height) / 2 + 1e-4f);
        if (t > 0) CHECK(m.width > glass_morph(tile, t / 2).width && m.height > glass_morph(tile, t / 2).height);
    }
    CHECK(near(glass_morph(tile, 0).width, pill.width) && near(glass_morph(tile, 1).height, kPanelH));
    CHECK(near(glass_radius(tile, 0), kGlassPillH / 2) && near(glass_radius(tile, 1), kPanelRadius));
    CHECK(near(glass_morph(tile, -1).width, pill.width) && near(glass_morph(tile, 2).width, kPanelW));  // clamped
    // Panel layout: rows below the header, sliders between the label and value columns, footer inside.
    CHECK(kPanelRowsTop >= kPanelHeaderH && kPanelKnobLo < kPanelKnobHi && kPanelTrackX0 > kPanelPad && kPanelTrackX1 < kPanelW - kPanelPad);
    CHECK(kPanelFooterTop + float(kPanelHintPx) <= kPanelH && kGrabberTop + kGrabberH < kPanelHeaderH);
    // Four rows (left / right, down / up, distance, curvature: every desktop faces you, so there is no facing setting).
    static_assert(kPlacementMenuFields == 4);
    // Then the Scale row (one row: label and dropdown box), then the desktop row (Ultrawide / Add / Remove) between it
    // and the footer, inside the paddings.
    CHECK(kPanelScaleTop == kPanelRowsTop + 4 * kPanelRowH && kPanelActionsTop == kPanelScaleTop + kPanelRowH + 6 &&
          kPanelFooterTop == kPanelActionsTop + kPanelActionH + 8);
    CHECK(kPanelFooterTop == 262.f && kPanelH == 292.f);
    {  // the Scale box sits in its row, right of the label column up to the padding; its list above it, under the header
        const FRect box = glass_scale_box();
        CHECK(box.x == kPanelTrackX0 && near(box.x + box.width, kPanelW - kPanelPad) && box.y >= kPanelScaleTop &&
              box.y + box.height <= kPanelScaleTop + kPanelRowH);
        for (std::size_t i = 0; i < kScaleSteps; ++i) {
            const FRect r = glass_scale_item(i);
            CHECK(r.x == box.x && r.width == box.width && r.height == kScaleItemH && r.y >= kPanelHeaderH);
            if (i) CHECK(near(r.y, glass_scale_item(i - 1).y + kScaleItemH));
        }
        CHECK(near(glass_scale_item(kScaleSteps - 1).y + kScaleItemH + kScaleListGap, box.y));
        CHECK(glass_scale_item(kScaleSteps).width == 0.f);
        // 100..200% in quarter steps: the broker's own bounds (spike/ownership/registry.cpp validDesktop).
        CHECK((kScalePercents == std::array<std::uint16_t, kScaleSteps>{100, 125, 150, 175, 200}));
        PlacementMenuState st; st.scale = 100; st.scales = 0b10111;  // 1920x1080: 175% does not divide it
        CHECK(glass_scale_enabled(0, st) && glass_scale_enabled(2, st) && !glass_scale_enabled(3, st) && glass_scale_enabled(4, st));
        CHECK(!glass_scale_enabled(kScaleSteps, st));
        st.busy = true; for (std::size_t i = 0; i < kScaleSteps; ++i) CHECK(!glass_scale_enabled(i, st));
        CHECK(glass_scale_label(125) == "125%" && glass_scale_label(0) == "--");
    }
    for (std::size_t a = 0; a < kPanelActions; ++a) {
        const FRect r = glass_panel_action(a);
        CHECK(r.x >= kPanelPad && r.x + r.width <= kPanelW - kPanelPad + 1e-3f && r.y == kPanelActionsTop && r.height == kPanelActionH);
        if (a) CHECK(near(r.x, glass_panel_action(a - 1).x + kPanelActionW + kPanelActionGap));
    }
    CHECK(glass_panel_action(kPanelActions).width == 0.f);
    {  // which buttons do anything: Add stops at kMaxDesktops, Remove keeps one desktop, nothing while busy
        PlacementMenuState st; st.count = 3;
        CHECK(glass_panel_action_enabled(0, st) && glass_panel_action_enabled(1, st) && glass_panel_action_enabled(2, st));
        st.count = std::uint32_t(kMaxDesktops); CHECK(!glass_panel_action_enabled(1, st) && glass_panel_action_enabled(2, st));
        st.count = 1; CHECK(glass_panel_action_enabled(1, st) && !glass_panel_action_enabled(2, st));
        st.busy = true; for (std::size_t a = 0; a < kPanelActions; ++a) CHECK(!glass_panel_action_enabled(a, st));
        CHECK(std::string(glass_panel_action_label(2, true)) == "Remove?" && std::string(glass_panel_action_label(2, false)) == "Remove");
        for (std::size_t a = 0; a < kPanelActions; ++a)
            for (unsigned char c : std::string(glass_panel_action_label(a, false))) CHECK(c >= 0x20 && c < 0x7f);  // pixel font: ASCII
    }

    // Look: every pixel of every state stays at or under the cards' 191/255 cap, premultiplied, with any accent.
    for (const auto& accent : {std::array<float, 3>{1, 1, 1}, std::array<float, 3>{0.55f, 0.55f, 0.55f}, std::array<float, 3>{1, 0, 0.3f}})
        for (float level : {kGlassIdle, kGlassHover, kGlassActive, 5.f})
            for (float t : {0.f, 0.3f, 1.f}) {
                const auto look = glass_look(accent, level, t);
                const FRect r = glass_morph(tile, t);
                const float radius = glass_radius(tile, t);
                float top = 0, alpha = 0;
                for (float y = r.y - kGlassShadowMargin; y < r.y + r.height + kGlassShadowMargin; y += 1.f)
                    for (float x = r.x - 10; x < r.x + r.width + 10; x += 3.f) {
                        const auto c = glass_shade(look, r, radius, x + 0.5f, y + 0.5f, 1.f);
                        for (int i = 0; i < 3; ++i) { top = std::max(top, c[std::size_t(i)]); CHECK(c[std::size_t(i)] <= c[3] + 1e-5f); }
                        alpha = std::max(alpha, c[3]);
                        CHECK(c[3] <= 1.f + 1e-5f && c[3] >= 0.f);
                    }
                CHECK(top <= kGlassCap + 1e-5f && top > 0.15f && alpha > 0.8f);
            }
    CHECK(near(kGlassCap * 255.f, float(kCardMaxLevel), 0.01f));
    {
        const auto look = glass_look({0.5f, 0.5f, 0.5f}, kGlassIdle, 1.f);
        const FRect r = panel; const float radius = kPanelRadius;
        const float cx = r.x + r.width / 2, cy = r.y + r.height / 2;
        const auto mid = glass_shade(look, r, radius, cx, cy, 1.f);
        CHECK(near(mid[3], look.fill, 1e-3f) && mid[0] < 0.25f);  // translucent and dark behind the text
        const auto high = glass_shade(look, r, radius, cx, r.y + 2.f, 1.f), low = glass_shade(look, r, radius, cx, r.y + r.height - 4.f, 1.f);
        CHECK(high[0] > mid[0] && mid[0] > low[0]);  // gloss at the top, lighter toward the top
        const auto rim = glass_shade(look, r, radius, cx, r.y + 0.5f, 1.f);
        CHECK(rim[0] > high[0]);  // the hairline
        const auto corner = glass_shade(look, r, radius, r.x + 1.f, r.y + 1.f, 1.f);
        CHECK(corner[3] < 0.5f);  // rounded corner: mostly outside (only shadow)
        const auto shadow = glass_shade(look, r, radius, cx, r.y + r.height + 4.f, 1.f), far = glass_shade(look, r, radius, cx, r.y + r.height + 200.f, 1.f);
        CHECK(shadow[3] > 0.1f && shadow[0] == 0.f && far[3] < 1e-4f);  // a black soft shadow below, nothing far away
        CHECK(glass_shade(look, r, radius, cx, cy, 0.5f)[3] == mid[3] * 0.5f);
        const auto hover = glass_shade(glass_look({0.5f, 0.5f, 0.5f}, kGlassHover, 1.f), r, radius, cx, r.y + 0.5f, 1.f);
        CHECK(hover[0] > rim[0]);  // hover brightens the rim
        const auto g = grabber_look(kGlassIdle);
        CHECK(g.shadow == 0.f && g.gloss == 0.f);
    }
    const std::string shader = glass_fragment_shader();
    CHECK(shader.find("0.74901961") != std::string::npos && shader.find("gl_FragColor") != std::string::npos);

    // Panel content: kPanelW x kPanelH, premultiplied, transparent between its parts, under the cap but readable; the
    // selected row has a band; a knob at a field's bounds sits on kPanelKnobLo / kPanelKnobHi.
    std::string why;
    const auto real = HudFont::load(kHudFontFamily, why);
    std::vector<const HudFont*> fonts{nullptr};
    if (real) fonts.push_back(real.get());
    const auto bounds = placement_menu_bounds();
    for (const HudFont* f : fonts) {
        PlacementMenuState st; st.open = true; st.desktop = 1; st.field = 3;
        for (auto& p : st.placements) p = placement_menu_flat();
        st.placements[1] = {125, -70, 150, 90};
        const auto im = render_glass_panel(st, f);
        CHECK(im.width == std::uint32_t(kPanelW) && im.height == std::uint32_t(kPanelH) && premultiplied(im));
        CHECK(brightest(im) <= kCardMaxLevel && brightest(im) >= 140);
        CHECK(im.rgba[3] == 0 && im.rgba[im.rgba.size() - 1] == 0);  // corners: the glass shows through
        auto alpha_at = [&](float x, float y) { return im.rgba[(std::size_t(y) * im.width + std::size_t(x)) * 4 + 3]; };
        const float band_y = kPanelRowsTop + 3 * kPanelRowH + 3;
        CHECK(alpha_at(kPanelTrackX0 - 4, band_y) > 0 && alpha_at(kPanelTrackX0 - 4, kPanelRowsTop + 3) == 0);  // only row 3 has the band
        PlacementMenuState other = st; other.desktop = 2;
        CHECK(render_glass_panel(other, f).rgba != im.rgba);  // another desktop: its own header and values
        {  // the desktop row: its buttons show, the header names the real output, and each state draws differently
            const auto row_top = std::uint32_t(kPanelActionsTop), row_bottom = std::uint32_t(kPanelActionsTop + kPanelActionH);
            CHECK(alpha_at(glass_panel_action(1).x + 4, kPanelActionsTop + kPanelActionH / 2) > 0);
            PlacementMenuState renamed = st; renamed.numbers = {1, 3, 4, 4};
            CHECK(render_glass_panel(renamed, f).rgba != im.rgba);  // "XR-3", not "XR-2"
            for (auto change : {+[](PlacementMenuState& m) { m.wide = true; }, +[](PlacementMenuState& m) { m.armed = true; },
                                +[](PlacementMenuState& m) { m.busy = true; }, +[](PlacementMenuState& m) { m.count = 1; },
                                +[](PlacementMenuState& m) { m.count = std::uint32_t(kMaxDesktops); }}) {
                PlacementMenuState changed = st; change(changed);
                const auto other_im = render_glass_panel(changed, f);
                CHECK(premultiplied(other_im) && brightest(other_im) <= kCardMaxLevel);
                CHECK(!changed_columns(im, other_im, row_top, row_bottom).empty());   // the row shows it
                CHECK(changed_columns(im, other_im, 0, row_top).empty());             // nothing above the row moves
            }
            if (f) for (std::size_t a = 0; a < kPanelActions; ++a)
                for (bool armed : {false, true})  // every label fits its button
                    CHECK(f->measure(glass_panel_action_label(a, armed), kPanelActionPx, true) + 8 <= std::uint32_t(kPanelActionW));
            for (std::size_t a = 0; a < kPanelActions; ++a) CHECK(hud_text_width(glass_panel_action_label(a, true), 2) + 8 <= std::uint32_t(kPanelActionW));
        }
        {  // the Scale row: the box shows the scale; the open list covers the sliders and shows every step, under the cap
            const auto row_top = std::uint32_t(kPanelScaleTop), row_bottom = std::uint32_t(kPanelScaleTop + kPanelRowH);
            PlacementMenuState a = st; a.scale = 100; a.scales = 0b10111;
            PlacementMenuState b = a; b.scale = 150;
            const auto ia = render_glass_panel(a, f), ib = render_glass_panel(b, f);
            CHECK(!changed_columns(ia, ib, row_top, row_bottom).empty());   // "100%" vs "150%"
            CHECK(changed_columns(ia, ib, 0, row_top).empty() && changed_columns(ia, ib, row_bottom, std::uint32_t(kPanelH)).empty());
            CHECK(alpha_at(glass_scale_box().x + 4, kScaleBoxY + kScaleBoxH / 2) > 0);
            PlacementMenuState open = a; open.picker = true;
            const auto io = render_glass_panel(open, f);
            CHECK(premultiplied(io) && brightest(io) <= kCardMaxLevel);
            const auto list_top = std::uint32_t(glass_scale_item(0).y), list_bottom = std::uint32_t(kScaleBoxY);
            CHECK(!changed_columns(ia, io, list_top, list_bottom).empty());              // the list is drawn over the sliders
            CHECK(changed_columns(ia, io, std::uint32_t(kPanelActionsTop), std::uint32_t(kPanelH)).empty());  // not below
            PlacementMenuState moved = open; moved.scale = 200;
            CHECK(!changed_columns(io, render_glass_panel(moved, f), list_top, list_bottom).empty());  // the band follows
            PlacementMenuState none = open; none.scales = 0b00001;
            CHECK(!changed_columns(io, render_glass_panel(none, f), list_top, list_bottom).empty());   // dimmed steps
            if (f) CHECK(f->measure("200%", kPanelActionPx, true) + 40 <= std::uint32_t(kScaleBoxW));
        }
        for (std::uint32_t field = 0; field < kPlacementMenuFields; ++field) {
            PlacementMenuState lo = st, hi = st;
            lo.field = hi.field = 9;  // no band
            lo.placements[1][field] = bounds[field].first; hi.placements[1][field] = bounds[field].second;
            const auto top = std::uint32_t(kPanelRowsTop + float(field) * kPanelRowH), bottom = std::uint32_t(float(top) + kPanelRowH);
            const auto runs = changed_columns(render_glass_panel(lo, f), render_glass_panel(hi, f), top, bottom);
            CHECK(runs.size() >= 2);  // knob travel and fill, then the value text
            if (runs.size() < 2) continue;
            const float left_centre = float(runs[0].first) + kPanelKnobR, right_centre = float(runs[0].second) + 1 - kPanelKnobR;
            CHECK(std::fabs(left_centre - kPanelKnobLo) <= 1.5f && std::fabs(right_centre - kPanelKnobHi) <= 1.5f);
            CHECK(runs[1].first >= std::uint32_t(kPanelTrackX1));  // the value text starts right of the slider track
            CHECK(runs.back().second < std::uint32_t(kPanelW - kPanelPad) + 2);
        }
        // Every field's value, at both bounds and one step in from the low one, in both weights (the selected row is bold), is
        // inked right of the slider track: drawn right-aligned at kPanelW - kPanelPad as render_glass_panel does, its leftmost
        // pixel is past kPanelTrackX1. (The orbit's tenths show as whole degrees for this: "-179.9°" would be ~101 px against
        // the 84 px value column.)
        for (std::uint32_t field = 0; field < kPlacementMenuFields; ++field)
            for (const std::int32_t v : {bounds[field].first, bounds[field].second, std::int32_t(bounds[field].first + 1)})
                for (const bool bold : {false, true}) {
                    PlacementMenuState at = st;
                    at.placements[1][field] = v;
                    const std::string value = placement_menu_rows(at, f == nullptr)[field].value;
                    const float width = float(f ? f->measure(value, kPanelTextPx, bold) : hud_text_width(value, 2));
                    const float left = kPanelW - kPanelPad - width;
                    CHECK(left >= kPanelTrackX1);
                    if (!f) continue;  // the pixel font inks from its left edge
                    int ink = int(kPanelW);
                    f->draw(value, kPanelTextPx, bold, int(std::lround(left)), 100, [&](int x, int, float cov) { if (cov > 0) ink = std::min(ink, x); });
                    CHECK(ink >= int(kPanelTrackX1) && ink < int(kPanelW));
                }
    }
    CHECK(glass_panel_hint(true).find("TAB") != std::string::npos && glass_panel_hint(false).find("Tab") != std::string::npos);
    for (unsigned char c : glass_panel_hint(true)) CHECK(c >= 0x20 && c < 0x7f);
    if (real) CHECK(real->measure(glass_panel_hint(false), kPanelHintPx, false) <= std::uint32_t(kPanelW - 2 * kPanelPad));
    CHECK(hud_text_width(glass_panel_hint(true), 2) <= std::uint32_t(kPanelW - 2 * kPanelPad));

    // Animation: one open at a time; opening another collapses the first; the content fades in over the last part of
    // the expansion; value changes re-render lazily (once per frame however many arrive) without restarting anything.
    {
        GlassPills g;
        CHECK(!g.any_expanded(0) && !g.animating(0) && g.content_desktop() == -1 && g.content_alpha(0) == 0);
        PlacementMenuState st; st.open = true; st.desktop = 1;
        CHECK(g.set(st, 1000) && g.opened() == 1 && g.animating(1000) && g.expand(1, 1000) == 0);
        CHECK(g.expand(1, 1000 + kGlassExpandMs) == 1 && !g.animating(1000 + kGlassExpandMs) && g.expand(0, 2000) == 0);
        CHECK(g.content_desktop() == 1 && g.content_alpha(1000 + kGlassExpandMs) == 1 && g.content_alpha(1000 + kGlassExpandMs * 0.3) == 0);
        for (double t = 1000; t < 1000 + kGlassExpandMs; t += 10) CHECK(g.expand(1, t + 10) >= g.expand(1, t));
        CHECK(g.image().width == std::uint32_t(kPanelW) && g.renders() == 1);
        CHECK(!g.set(st, 1500) && g.renders() == 1);
        for (int v = 10; v < 20; ++v) { st.placements[1][3] = v; CHECK(g.set(st, 1500)); }
        CHECK(g.renders() == 1); g.image(); CHECK(g.renders() == 2);  // ten values, one render
        PlacementMenuState next = st; next.desktop = 2;
        CHECK(g.set(next, 2000) && g.content_desktop() == 2 && g.opened() == 2 && g.closed() == 1);
        CHECK(g.expand(1, 2000 + kGlassCollapseMs) == 0 && g.expand(2, 2000 + kGlassExpandMs) == 1);
        PlacementMenuState shut = next; shut.open = false;
        CHECK(g.set(shut, 3000) && !g.open() && g.closed() == 2 && g.content_desktop() == 2);  // the content stays for the collapse
        CHECK(g.content_alpha(3000) == 1 && g.content_alpha(3000 + kGlassCollapseMs) == 0 && !g.any_expanded(3000 + kGlassCollapseMs));
        CHECK(!g.set(shut, 3100));
        // Reversing mid-way does not jump.
        CHECK(g.set(next, 4000));
        const float mid = g.expand(2, 4000 + kGlassExpandMs / 2);
        CHECK(mid > 0.2f && mid < 0.8f && g.set(shut, 4000 + kGlassExpandMs / 2) && g.expand(2, 4000 + kGlassExpandMs / 2) == mid);
    }
    // CardFade::hold: an unseen fade does not run; a toggle after `since` holds at the toggle's own level.
    {
        CardFade f{100, 100};
        f.set(true, 0);
        CHECK(near(f.level(50), 0.5f));
        f.hold(50, 1000);  // nothing ran from 50 to 1000
        CHECK(near(f.level(1000), 0.5f) && near(f.level(1050), 1.f) && f.animating(1000));
        CardFade g{100, 100};
        g.hold(-5000, 10);  // a stale `since` from long before the toggle...
        g.set(true, 20); g.hold(-5000, 900);
        CHECK(g.level(900) == 0.f && near(g.level(950), 0.5f));  // ...holds at the toggle's level, never stalls past it
        g.hold(950, 950); CHECK(near(g.level(950), 0.5f));  // no time passed: no change
    }

    // Opening a panel from a zoomed-in view (Ctrl+Alt+L zooms out in the same write): the morph waits for the overview,
    // so the first frame that can show it starts near the pill, and the whole expansion is seen.
    for (const double idle_before : {0.0, 5000.0}) {  // frames right up to the open, or none for a while (stale hidden time)
        GlassPills g;
        ViewAnimator view{220.f};
        view.set_target(true, 1, 40, 0);  // zoomed in on XR-2
        const double frame_ms = 1000.0 / 120;
        double t = 0;
        for (; t < 400; t += frame_ms) { view.advance(t); g.frame(view.zoom() < 0.01f, t); }  // settled, zoomed: unseen frames
        const double open_at = t + idle_before;
        PlacementMenuState st; st.open = true; st.desktop = 1;
        view.set_target(false, 1, 40, open_at);
        CHECK(g.set(st, open_at));
        GlassFrame first; double first_at = -1;
        for (t = open_at + 1; t < open_at + 1000; t += frame_ms) {
            view.advance(t);
            const GlassFrame f = g.frame(view.zoom() < 0.01f, t);
            if (view.zoom() >= 0.01f) {
                CHECK(f.expand[1] == 0.f && f.content_alpha == 0.f && f.content == -1 && f.top == -1);  // nothing drawn yet
                continue;
            }
            first = f; first_at = t;
            break;
        }
        CHECK(first_at > open_at + 150);  // the zoom-out takes most of 220 ms
        CHECK(first.expand[1] < 0.05f && first.content_alpha == 0.f && first.top == 1);  // starts as (nearly) the pill
        CHECK(g.animating(first_at) && near(g.expand(1, first_at + kGlassExpandMs), 1.f));  // and runs its full length
        CHECK(g.frame(true, first_at + kGlassExpandMs / 2).expand[1] > 0.2f);
        CHECK(near(g.frame(true, first_at + kGlassExpandMs + 1).content_alpha, 1.f));
    }
    {   // In the overview nothing is held, and a collapse while zooming in just runs out (it is not saved for later).
        GlassPills g;
        PlacementMenuState st; st.open = true; st.desktop = 0;
        g.set(st, 0);
        CHECK(near(g.frame(true, 130).expand[0], g.expand(0, 130)) && near(g.expand(0, 260), 1.f));
        st.open = false; g.set(st, 300);
        for (double t = 300; t < 600; t += 8) g.frame(false, t);
        CHECK(g.expand(0, 600) == 0.f && !g.any_expanded(600) && !g.animating(600));
    }

    // Draw order: the open panel's desktop last; while collapsing, the most expanded one; otherwise unchanged.
    CHECK((glass_draw_order({2, 0, 1}, 0) == std::vector<std::size_t>{2, 1, 0}));
    CHECK((glass_draw_order({2, 0, 1}, 1) == std::vector<std::size_t>{2, 0, 1}));
    CHECK((glass_draw_order({2, 0, 1}, -1) == std::vector<std::size_t>{2, 0, 1}));
    CHECK((glass_draw_order({2, 0}, 1) == std::vector<std::size_t>{2, 0}));
    {
        GlassPills g;
        CHECK(g.frame(true, 0).top == -1);
        PlacementMenuState st; st.open = true; st.desktop = 0;
        g.set(st, 0);
        CHECK(g.frame(true, 0).top == 0);  // open, even before it has grown
        st.desktop = 2; g.set(st, 1000);
        CHECK(g.frame(true, 1000).top == 2);  // Tab: the new one is on top of the one collapsing
        st.open = false; g.set(st, 2000);
        CHECK(g.frame(true, 2050).top == 2 && g.frame(true, 2000 + kGlassCollapseMs).top == -1);
    }

    // A resize never changes the glass's size: from half to ten times the desktop's size (distance 2 .. 0.1, flat or
    // curved, at its slot or turned round you) the pill and the panel appear exactly as big as at distance 1, and the
    // pill stays kGlassPillGap (as seen) under the desktop's bottom edge, which moves with the resize.
    {
        const Canvas cv{1920, 1200, 0.36f, 0.225f};
        const FRect tile{666.67f, 435, 586.67f, 330};
        auto seen = [&](const Placement& p, std::array<float, 2> q) {  // tile surface px -> where it appears, canvas px
            const Vec3 w = place_point(cv, tile, p, q[0], q[1]);
            return std::array<float, 2>{(w.x / -w.z / cv.half_w + 1) * cv.view_w / 2, (1 - w.y / -w.z / cv.half_h) * cv.view_h / 2};
        };
        // Apparent size: a glass rect's world width and height (through its centre lines) over the desktop centre's
        // distance from the eye; on the centred, flat desktop also exactly its size on the canvas.
        auto size_of = [&](const Placement& p, const FRect& r) {
            const float cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            auto world = [&](float gx, float gy) { const auto q = glass_to_desktop(tile, p, gx, gy); return place_point(cv, tile, p, q[0], q[1]); };
            auto dist = [](Vec3 a, Vec3 b) { return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z)); };
            const Vec3 c = place_point(cv, tile, p, tile.x + tile.width / 2, tile.y + tile.height / 2);
            const float eye = std::sqrt(c.x * c.x + c.y * c.y + c.z * c.z);
            return std::array<float, 2>{dist(world(r.x, cy), world(r.x + r.width, cy)) / eye, dist(world(cx, r.y), world(cx, r.y + r.height)) / eye};
        };
        auto canvas_size = [&](const Placement& p, const FRect& r) {
            const auto a = seen(p, glass_to_desktop(tile, p, r.x, r.y)), b = seen(p, glass_to_desktop(tile, p, r.x + r.width, r.y + r.height));
            return std::array<float, 2>{b[0] - a[0], b[1] - a[1]};
        };
        const FRect pill = glass_pill(tile), panel = glass_panel(tile);
        const auto pill1 = canvas_size({}, pill);
        CHECK(near(pill1[0], pill.width, 0.05f) && near(pill1[1], kGlassPillH, 0.05f));  // the default: as laid out
        for (const float d : {0.1f, 0.25f, 0.5f, 1.5f, 2.f})
            for (const Placement p : {Placement{0, 0, d, 0}, Placement{0, 0, d, 1.f}, Placement{0.3f, -0.1f, d, 0}}) {
                const Placement at1{p.x, p.y, 1, p.curvature};
                const auto a = size_of(p, pill), a1 = size_of(at1, pill);
                CHECK(near(a[0], a1[0], 0.02f * a1[0]) && near(a[1], a1[1], 0.02f * a1[1]));
                if (p.curvature == 0) {  // the panel is wide: on a curved desktop its chord shrinks a little as it wraps
                    const auto b = size_of(p, panel), b1 = size_of(at1, panel);
                    CHECK(near(b[0], b1[0], 1e-3f * b1[0]) && near(b[1], b1[1], 1e-3f * b1[1]));
                }
                if (p.x == 0 && p.curvature == 0) {  // flat at its slot: exactly the same on the canvas, and the gap under
                    const auto c = canvas_size(p, pill), e = canvas_size(p, panel), e1 = canvas_size({}, panel);  // the (resized) bottom edge too
                    CHECK(near(c[0], pill1[0], 0.05f) && near(c[1], pill1[1], 0.05f) && near(e[0], e1[0], 0.1f) && near(e[1], e1[1], 0.1f));
                    const float bottom = seen(p, {tile.x + tile.width / 2, tile.y + tile.height})[1];
                    CHECK(near(seen(p, glass_to_desktop(tile, p, pill.x + pill.width / 2, pill.y))[1] - bottom, kGlassPillGap, 0.05f));
                }
            }
        // The desktop itself does scale (ten times at the floor) while its glass does not.
        const float w1 = seen({}, {tile.x + tile.width, 600})[0] - seen({}, {tile.x, 600})[0];
        const Placement huge{0, 0, 0.1f, 0};
        CHECK(near(seen(huge, {tile.x + tile.width, 600})[0] - seen(huge, {tile.x, 600})[0], 10 * w1, 0.01f * w1));
        // The mesh the compositor draws: glass px kept for the shader, world points through glass_to_desktop.
        for (const auto& v : glass_mesh(cv, tile, huge, pill)) {
            const auto at = glass_to_desktop(tile, huge, v.px, v.py);
            const Vec3 w = place_point(cv, tile, huge, at[0], at[1]);
            CHECK(near(v.world.x, w.x, 1e-5f) && near(v.world.y, w.y, 1e-5f) && near(v.world.z, w.z, 1e-5f));
            CHECK(near(v.px, pill.x, 1e-2f) || near(v.px, pill.x + pill.width, 1e-2f));
        }
        CHECK(glass_to_desktop(tile, {}, 700, 800) == (std::array<float, 2>{700, 800}));  // distance 1: the tile's own px
    }

    // A held slider: the open panel is drawn on the press-time surface, the desktop's own placement is untouched.
    {
        const MenuPlacement wire{20, -10, 150, 60};
        const Placement p = placement_from_wire(wire);
        const float rad = 3.14159265f / 180.f;
        CHECK(near(p.x, 2 * rad) && near(p.y, -1 * rad) && near(p.distance, 1.5f) && near(p.curvature, 60 * rad));
        const Placement edge = placement_from_wire({-1800, 900, 50, 120});
        CHECK(near(edge.x, -180 * rad) && near(edge.y, 90 * rad) && near(edge.distance, 0.5f) && near(edge.curvature, 120 * rad));
        CHECK(near(placement_from_wire({0, 0, 10, 0}).distance, 0.1f));  // the distance floor: ten times the size
        GlassPills g;
        PlacementMenuState st; st.open = true; st.desktop = 1;
        CHECK(g.set(st, 0) && !g.frame(true, 300).surface && g.frame(true, 300).surface_desktop == -1);
        const auto generation = g.generation(), renders = g.renders();
        CHECK(g.set(st, 400, wire));
        const GlassFrame held = g.frame(true, 400);
        CHECK(g.generation() == generation && g.renders() == renders);  // the surface alone does not re-render the content
        CHECK(held.surface && held.surface_desktop == 1 && near(held.surface->x, 2 * rad) && near(held.surface->distance, 1.5f));
        CHECK(!g.set(st, 410, wire));
        CHECK(g.set(st, 500) && !g.frame(true, 500).surface && !g.surface());  // released: back on the live placement
        PlacementMenuState shut = st; shut.open = false;
        g.set(st, 600, wire);
        CHECK(g.set(shut, 700, wire) && !g.surface() && !g.frame(true, 700).surface);  // never without an open panel
    }
    std::cout << (failures ? "glass FAILED\n" : "glass ok\n");
    return failures ? 1 : 0;
}
