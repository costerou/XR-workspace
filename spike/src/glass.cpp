#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/options_geometry.hpp"
#include "xr/material_draw.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numbers>
#include <string>

namespace xr {
namespace {
float lerp(float a, float b, float t) { return a * (1 - t) + b * t; }  // exactly a at 0, b at 1
float clamp01(float v) { return std::isnan(v) ? 0.f : std::clamp(v, 0.f, 1.f); }
float smooth(float e0, float e1, float x) { const float t = clamp01((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t); }  // GLSL smoothstep
// A value per pill state (kGlassIdle / kGlassHover / kGlassActive), linear in between, held outside.
float by_state(float level, float idle, float hover, float active) {
    if (!(level > kGlassIdle)) return idle;
    if (level >= kGlassActive) return active;
    if (level <= kGlassHover) return lerp(idle, hover, (level - kGlassIdle) / (kGlassHover - kGlassIdle));
    return lerp(hover, active, (level - kGlassHover) / (kGlassActive - kGlassHover));
}

// The panel's Material parts, panel px. The selected row's band (an M3 active indicator: fully rounded) is inset
// kBandInset from the panel's sides and 2 px from its row; slider tracks are kTrackH tall, the inactive part the primary
// at kTrackAlpha (M3); the selected slider's handle has a focus halo of kHaloR. Buttons: M3 label large (bold) at
// kButtonLabelPx with a kButtonIconPx icon kButtonIconGap before it, when every button's icon and label fit inside
// kButtonPadMin of padding (with the pixel font they do not: labels only).
constexpr float kBandInset = 10.f, kTrackH = 4.f, kTrackAlpha = 0.24f, kHaloR = 2 * kPanelKnobR;
static_assert(kResListX <= kBandInset, "the open Resolution list hides the selected row's band under it");
constexpr std::uint32_t kButtonLabelPx = 18;
constexpr float kButtonIconPx = 16.f, kButtonIconGap = 6.f, kButtonPadMin = 8.f;
constexpr MdIcon kActionIcons[kPanelActions] = {MdIcon::width, MdIcon::add, MdIcon::remove};
// The Resolution and Scale dropdowns (M3 exposed dropdown menus): a filled text field (extra small corners; disabled:
// on-surface at kFieldDisabled) with its value kFieldPadX in and a kCaretPx drop-down arrow kCaretInset from its right end,
// its active indicator kIndicatorH tall (kIndicatorFocusH while its list is open); the lists are M3 menus, their items'
// text kFieldPadX in.
constexpr float kFieldDisabled = 0.04f, kFieldPadX = 12.f, kCaretPx = 24.f, kCaretInset = 16.f, kIndicatorH = 1.f, kIndicatorFocusH = 2.f;
// Short labels: the label column is kPanelTrackX0 - kPanelPad wide.
constexpr const char* kLabels[kPlacementMenuFields] = {"Left/right", "Down/up", "Distance", "Curvature"};
constexpr float kSwitchHandleOn = 9.f, kSwitchHandleOff = 6.f;  // the Background switch's handle radius (M3: larger while on)
static_assert(PlacementMenuState{}.brightness == kBrightnessDefault, "a panel with no brightness sent shows the default");

// Text at an em size: the system font, or the built-in pixel font (ASCII, zoom 2) without one.
std::uint32_t text_width(const HudFont* font, const std::string& s, std::uint32_t px, bool bold) {
    return font ? font->measure(s, px, bold) : hud_text_width(s, 2);
}
// Draw `s` with its left edge at `left`, vertically centred on `cy` (capitals centred), `alpha` opaque.
void text(HudImage& im, const HudFont* font, const std::string& s, std::uint32_t px, bool bold, float left, float cy, MdRgb c,
          float alpha = 1.f) {
    if (font) {
        const int baseline = int(std::lround(cy + 0.36f * float(px)));  // JetBrains Mono capitals: ~0.73 em
        font->draw(s, px, bold, int(std::lround(left)), baseline, [&](int x, int y, float cov) { md_over(im, x, y, c, cov * alpha); });
        return;
    }
    constexpr std::uint32_t z = 2;
    int x = int(std::lround(left));
    const int top = int(std::lround(cy - float(kGlyphH * z) / 2));
    for (char ch : s) {
        const auto& g = hud_glyph(ch);
        for (std::uint32_t row = 0; row < kGlyphH; ++row)
            for (std::uint32_t col = 0; col < kGlyphW; ++col)
                if (g[row] & (0x10u >> col))
                    for (std::uint32_t sy = 0; sy < z; ++sy)
                        for (std::uint32_t sx = 0; sx < z; ++sx) md_over(im, x + int(col * z + sx), top + int(row * z + sy), c, alpha);
        x += int((kGlyphW + 1) * z);
    }
}
// A panel row with an M3 exposed dropdown menu (the Resolution and Scale rows): the row's label, then a filled text field
// (surface container highest) at `box` showing `value` in label large (bold, `label_px`), a drop-down arrow (pointing up,
// in the primary, while its list is open) and an active indicator under it (on-surface-variant; 2 px of primary while
// open); M3 disabled (on-surface at 4% / 38%) unless `usable`.
void dropdown_box(HudImage& im, const HudFont* font, const MaterialScheme& m, const FRect& box, const std::string& value,
                  std::uint32_t label_px, bool open, bool usable, bool caret = true) {
    const float cy = box.y + box.height / 2;
    const float x1 = box.x + box.width, y1 = box.y + box.height;
    const float content_alpha = usable ? 1.f : kMdDisabledContent;
    md_fill_rounded(im, box.x, box.y, x1, y1, kMdShapeExtraSmall, usable ? m.surface_container_highest : m.on_surface,
                    usable ? 1.f : kFieldDisabled);
    const float indicator = open ? kIndicatorFocusH : kIndicatorH;
    md_fill_rounded(im, box.x, y1 - indicator, x1, y1, 0, open ? m.primary : usable ? m.on_surface_variant : m.on_surface, content_alpha);
    text(im, font, value, label_px, true, box.x + kFieldPadX, cy, m.on_surface, content_alpha);
    if (caret)
        md_icon(im, open ? MdIcon::arrow_drop_up : MdIcon::arrow_drop_down, x1 - kCaretInset, cy, kCaretPx,
                open ? m.primary : usable ? m.on_surface_variant : m.on_surface, content_alpha);
}
void dropdown_row(HudImage& im, const HudFont* font, const MaterialScheme& m, const char* label, float row_top, const FRect& box,
                  const std::string& value, std::uint32_t label_px, bool open, bool usable) {
    text(im, font, label, kPanelTextPx, false, kPanelPad, row_top + kPanelRowH / 2, m.on_surface);
    dropdown_box(im, font, m, box, value, label_px, open, usable);
}
// The parts the placement panel and the options menu share, so the two cannot drift apart.
// The header: the desktop on the left (title: on-surface, bold), an M3 divider under it. The drag handle is the GPU's.
constexpr float kHeaderCy = (kGrabberTop + kGrabberH + kPanelHeaderH) / 2;
void panel_header(HudImage& im, const HudFont* font, const MaterialScheme& m, const std::string& title, float width) {
    text(im, font, title, kPanelTextPx, true, kPanelPad, kHeaderCy, m.on_surface);
    for (int x = int(kPanelPad); x < int(width - kPanelPad); ++x) md_over(im, x, int(kPanelHeaderH) - 1, m.outline_variant, 1.f);
}
// The selected row (an M3 active indicator: secondary container, fully rounded).
void selected_band(HudImage& im, const MaterialScheme& m, float width, float top) {
    md_fill_rounded(im, kBandInset, top + 2, width - kBandInset, top + kPanelRowH - 2, (kPanelRowH - 4) / 2, m.secondary_container, 1.f);
}
// A slider: inactive track (primary at 24%), active track from `from` to `to` (px), handle (primary), the selected one's focus halo.
void slider_parts(HudImage& im, const MaterialScheme& m, float x0, float x1, float cy, float from, float to, bool selected, float alpha) {
    md_fill_rounded(im, x0, cy - kTrackH / 2, x1, cy + kTrackH / 2, kTrackH / 2, m.primary, kTrackAlpha * alpha);
    if (std::fabs(to - from) >= 1.f)
        md_fill_rounded(im, std::min(from, to), cy - kTrackH / 2, std::max(from, to), cy + kTrackH / 2, kTrackH / 2, m.primary, alpha);
    if (selected) md_disc(im, to, cy, kHaloR, m.primary, kMdStateFocus * alpha);  // the focused handle's state layer
    md_disc(im, to, cy, kPanelKnobR, m.primary, alpha);
}
// An M3 switch (on: the primary track and an on-primary handle at its end; off: an outlined track and a smaller outline handle).
void panel_switch(HudImage& im, const MaterialScheme& m, const FRect& sw, bool on, float alpha = 1.f) {
    const float x1 = sw.x + sw.width, y1 = sw.y + sw.height, cy = sw.y + sw.height / 2, end = sw.height / 2;
    md_fill_rounded(im, sw.x, sw.y, x1, y1, end, on ? m.primary : m.surface_container_highest, alpha);
    if (!on) md_stroke_rounded(im, sw.x, sw.y, x1, y1, end, 2.f, m.outline, alpha);
    md_disc(im, on ? x1 - end : sw.x + end, cy, on ? kSwitchHandleOn : kSwitchHandleOff, on ? m.on_primary : m.outline, alpha);
}
// A fully rounded M3 button, an icon (when `icon`) before its label, centred.
void panel_button(HudImage& im, const HudFont* font, const FRect& r, const std::string& label, const PanelButtonStyle& st, std::uint32_t label_px,
                  const MdIcon* icon) {
    md_fill_rounded(im, r.x, r.y, r.x + r.width, r.y + r.height, r.height / 2, st.container, st.container_alpha);
    const float w = float(text_width(font, label, label_px, true)), content = icon ? kButtonIconPx + kButtonIconGap + w : w;
    const float left = r.x + std::max(0.f, (r.width - content) / 2), cy = r.y + r.height / 2;
    if (icon) md_icon(im, *icon, left + kButtonIconPx / 2, cy, kButtonIconPx, st.content, st.content_alpha);
    text(im, font, label, label_px, true, icon ? left + kButtonIconPx + kButtonIconGap : left, cy, st.content, st.content_alpha);
}
// An M3 menu over what is under it (surface container, extra small corners, an outline-variant edge; opaque), and an item in label
// large: the current one on the secondary container, ones that cannot be picked M3 disabled.
void list_frame(HudImage& im, const MaterialScheme& m, float x0, float y0, float x1, float y1) {
    md_fill_rounded(im, x0, y0, x1, y1, kMdShapeExtraSmall, m.surface_container, 1.f);
    md_stroke_rounded(im, x0, y0, x1, y1, kMdShapeExtraSmall, 1.f, m.outline_variant, 1.f);
}
void list_item(HudImage& im, const HudFont* font, const MaterialScheme& m, const FRect& r, const std::string& label, std::uint32_t label_px, bool current, bool enabled) {
    if (current) md_fill_rounded(im, r.x + 2, r.y + 1, r.x + r.width - 2, r.y + r.height - 1, kMdShapeExtraSmall, m.secondary_container, 1.f);
    text(im, font, label, label_px, true, r.x + kFieldPadX, r.y + r.height / 2, current ? m.on_secondary_container : m.on_surface,
         enabled || current ? 1.f : kMdDisabledContent);
}
}

FRect glass_pill(const FRect& t) {
    if (t.width <= 0 || t.height <= 0) return {};
    const float w = std::min(std::clamp(kGlassPillShare * t.width, kGlassPillMinW, kGlassPillMaxW), t.width);
    return {t.x + (t.width - w) / 2, t.y + t.height + kGlassPillGap, w, kGlassPillH};
}

FRect glass_panel(const FRect& t) {
    if (t.width <= 0 || t.height <= 0) return {};
    return {t.x + (t.width - kPanelW) / 2, t.y + t.height + kGlassPillGap, kPanelW, kPanelH};
}

float glass_panel_shift(const Canvas& c, const FRect& tile, const Placement& on, bool options) {
    if (c.view_w <= 0 || c.view_h <= 0 || tile.width <= 0 || tile.height <= 0) return 0;
    const FRect panel = options ? options_panel(tile) : glass_panel(tile);
    auto at = [&](float gx) {  // the panel's bottom edge at glass x `gx`
        const auto [x, y] = glass_to_desktop(tile, on, gx, panel.y + panel.height);
        return place_point(c, tile, on, x, y);
    };
    const Vec3 centre = place_point(c, tile, on, tile.x + tile.width / 2, tile.y + tile.height / 2), bottom = at(tile.x + tile.width / 2);
    // Looking straight at the desktop: the angle from its centre down to the panel's bottom edge.
    const float dot = centre.x * bottom.x + centre.y * bottom.y + centre.z * bottom.z;
    const float ax = centre.y * bottom.z - centre.z * bottom.y, ay = centre.z * bottom.x - centre.x * bottom.z,
                az = centre.x * bottom.y - centre.y * bottom.x;
    float needed = std::atan2(std::sqrt(ax * ax + ay * ay + az * az), dot);
    // Level at the desktop's height, from straight ahead: a corner the view reaches, further away from the view's centre.
    const float length = std::sqrt(centre.x * centre.x + centre.y * centre.y + centre.z * centre.z);
    const float e = length > 0 ? std::asin(std::clamp(centre.y / length, -1.f, 1.f)) : 0.f, ce = std::cos(e), se = std::sin(e);
    for (const float gx : {panel.x, panel.x + panel.width}) {
        const Vec3 p = at(gx);
        const float y = ce * p.y + se * p.z, z = ce * p.z - se * p.y;
        if (z < 0 && std::fabs(p.x / z) <= c.half_w) needed = std::max(needed, std::atan2(-y, -z));
    }
    const float limit = std::atan(c.half_h * (1 - 2 * kPanelShiftMarginPx / c.view_h));
    return std::clamp(needed - limit, 0.f, kPanelShiftMax);
}

Mat3 glass_view_shift(const Mat3& v, float angle) {
    if (angle == 0.f) return v;
    const float s = std::sin(angle), k = std::cos(angle);  // about the eye's x axis: what is straight ahead moves up
    return {v[0], v[1], v[2], k * v[3] - s * v[6], k * v[4] - s * v[7], k * v[5] - s * v[8],
            s * v[3] + k * v[6], s * v[4] + k * v[7], s * v[5] + k * v[8]};
}

void RestoreStep::set(bool open, double now_ms) {
    const float to = open ? 1.f : 0.f;
    if (to == to_) return;
    from_ = value(now_ms); to_ = to; start_ms_ = now_ms;
}
float RestoreStep::value(double now_ms) const {
    const float t = clamp01(float((now_ms - start_ms_) / kRestoreStepMs));
    return from_ + (to_ - from_) * t * t * (3 - 2 * t);
}
bool RestoreStep::animating(double now_ms) const { return now_ms - start_ms_ < kRestoreStepMs; }

void PanelShift::set(float target, double now_ms) {
    if (target == to_) return;
    from_ = value(now_ms); to_ = target; start_ms_ = now_ms;
}

float PanelShift::value(double now_ms) const {
    const float t = clamp01(float((now_ms - start_ms_) / kPanelShiftMs));
    return from_ + (to_ - from_) * (t * t * (3 - 2 * t));
}

bool PanelShift::animating(double now_ms) const { return now_ms - start_ms_ < kPanelShiftMs; }

float level_bar_scale(const FRect& t) {
    if (!(t.width > 0) || !(t.height > 0)) return 0.f;
    return std::min({std::max(kLevelBarShare * t.width, kLevelBarMinW), kLevelBarW, t.width}) / kLevelBarW;
}

FRect level_bar_rect(const FRect& t) {
    const float s = level_bar_scale(t), w = kLevelBarW * s, h = kLevelBarH * s;
    if (!(s > 0)) return {};
    return {t.x + (t.width - w) / 2, t.y - kLevelBarGap * s - h, w, h};
}

LevelSegment level_bar_segment(const FRect& t, float px, float py) {
    const FRect r = level_bar_rect(t);
    const float pad = kLevelBarPad * level_bar_scale(t);
    if (!(r.width > 0) || px < r.x - pad || px > r.x + r.width + pad || py < r.y - pad || py > r.y + r.height + pad) return LevelSegment::none;
    const float third = r.width / float(kLevelSegments);
    if (px < r.x + third) return LevelSegment::ccw;
    return px < r.x + 2 * third ? LevelSegment::level : LevelSegment::cw;
}

FRect corner_cap_rect(const FRect& t, std::uint32_t corner) {
    const float s = level_bar_scale(t);
    if (!(s > 0) || corner > 3) return {};
    const bool right = corner == 1 || corner == 3, bottom = corner >= 2;
    const float size = (kCapOuter + kCapInner) * s;
    return {right ? t.x + t.width - kCapInner * s : t.x - kCapOuter * s, bottom ? t.y + t.height - kCapInner * s : t.y - kCapOuter * s, size, size};
}

namespace {
// Both controls' rects, or both empty on a desktop too narrow for them.
bool immersive_rects(const FRect& t, FRect& main, FRect& tail) {
    const float s = level_bar_scale(t);
    if (!(s > 0)) return false;
    const FRect bar = level_bar_rect(t);
    main = {bar.x + bar.width + kImmersiveGap * s, bar.y, kImmersiveMainW * s, bar.height};
    tail = {main.x + main.width + kImmersiveGap * s, bar.y, kImmersiveTailW * s, bar.height};
    return tail.x + tail.width <= t.x + t.width - kImmersiveClear * s;
}
}

FRect immersive_main_rect(const FRect& t) {
    FRect main, tail;
    return immersive_rects(t, main, tail) ? main : FRect{};
}

FRect immersive_tail_rect(const FRect& t) {
    FRect main, tail;
    return immersive_rects(t, main, tail) ? tail : FRect{};
}

ImmersivePart immersive_part(const FRect& t, float px, float py) {
    FRect main, tail;
    if (!immersive_rects(t, main, tail)) return ImmersivePart::none;
    const float s = level_bar_scale(t), pad = kLevelBarPad * s, half = kImmersiveGap * s / 2;
    if (py < main.y - pad || py > main.y + main.height + pad || px < main.x - half || px > tail.x + tail.width + pad) return ImmersivePart::none;
    return px < tail.x - half ? ImmersivePart::main : ImmersivePart::tail;
}

namespace {
// The atlas: seven bar cells down the left (idle, then one hot segment each hovered and dragged), and to their right the
// caps, a column per state and a row per corner, and right of those the 3D button: a column for its segment and one for its
// tail, four states each. Atlas px, kHandleAtlasGutter between cells and round the edge.
constexpr std::uint32_t kAtlasGap = kHandleAtlasGutter;
constexpr std::uint32_t kAtlasBarW = std::uint32_t(kLevelBarW) * kHandleAtlasScale, kAtlasBarH = std::uint32_t(kLevelBarH) * kHandleAtlasScale;
constexpr std::uint32_t kAtlasCap = std::uint32_t(kCapOuter + kCapInner) * kHandleAtlasScale;
constexpr std::uint32_t kBarCells = 1 + 2 * kLevelSegments, kCapStates = 3, kCapCorners = 4;
constexpr std::uint32_t kAtlasMainW = std::uint32_t(kImmersiveMainW) * kHandleAtlasScale, kAtlasTailW = std::uint32_t(kImmersiveTailW) * kHandleAtlasScale;
constexpr std::uint32_t kAtlasMainX = kAtlasGap + kAtlasBarW + kAtlasGap + kCapStates * (kAtlasCap + kAtlasGap), kAtlasTailX = kAtlasMainX + kAtlasMainW + kAtlasGap;
constexpr std::uint32_t kAtlasW = kAtlasTailX + kAtlasTailW + kAtlasGap;
constexpr std::uint32_t kAtlasOldH = kAtlasGap + std::max(kBarCells * (kAtlasBarH + kAtlasGap), kCapCorners * (kAtlasCap + kAtlasGap));
constexpr std::uint32_t kAtlasH = kAtlasOldH + 240;
constexpr float kAtlasPillW = kRestorePillW * float(kHandleAtlasScale), kAtlasPillY = float(kAtlasOldH) + 150.f;
constexpr float kAtlasMaximizeW = kMaximizeW * float(kHandleAtlasScale), kAtlasMaximizePitch = kAtlasMaximizeW + 4.f, kAtlasGearX = 2.f + 4.f * kAtlasMaximizePitch;
static_assert(kAtlasGearX + 42.f <= float(kAtlasW) && 2 * kAtlasPillW + 6.f <= float(kAtlasW), "the gear and the pills stay in the atlas");
struct AtlasCell { std::uint32_t x = 0, y = 0, w = 0, h = 0; };
AtlasCell bar_cell(std::uint32_t row) { return {kAtlasGap, kAtlasGap + row * (kAtlasBarH + kAtlasGap), kAtlasBarW, kAtlasBarH}; }
std::uint32_t bar_row(LevelSegment segment, bool active) {
    return segment == LevelSegment::none ? 0 : 1 + std::uint32_t(segment) + (active ? std::uint32_t(kLevelSegments) : 0);
}
AtlasCell cap_cell(std::uint32_t corner, std::uint32_t state) {
    return {kAtlasGap + kAtlasBarW + kAtlasGap + state * (kAtlasCap + kAtlasGap), kAtlasGap + corner * (kAtlasCap + kAtlasGap), kAtlasCap, kAtlasCap};
}
std::uint32_t button_row(bool lit, bool hot) { return kAtlasGap + (std::uint32_t(lit) * 2 + std::uint32_t(hot)) * (kAtlasBarH + kAtlasGap); }
AtlasCell main_cell(bool lit, bool hot) { return {kAtlasMainX, button_row(lit, hot), kAtlasMainW, kAtlasBarH}; }
AtlasCell tail_cell(bool lit, bool hot) { return {kAtlasTailX, button_row(lit, hot), kAtlasTailW, kAtlasBarH}; }
AtlasBox box_of(const AtlasCell& c) {
    const float w = float(kAtlasW), h = float(kAtlasH);
    return {float(c.x) / w, float(c.y) / h, float(c.x + c.w) / w, float(c.y + c.h) / h};
}
void draw_bar(HudImage& im, const AtlasCell& c, const MaterialScheme& m, LevelSegment hot, bool active) {
    const float k = kHandleAtlasScale, x = float(c.x), y = float(c.y), w = float(c.w), h = float(c.h), third = w / float(kLevelSegments);
    md_fill_rounded(im, x, y, x + w, y + h, h / 2, m.primary_container, 1.f);
    for (const float line : {x + third, x + 2 * third}) md_fill_rounded(im, line - k / 2, y + 7 * k, line + k / 2, y + h - 7 * k, k / 2, m.on_primary_container, 0.3f);
    if (hot != LevelSegment::none) {
        const int seg = int(hot);
        const float x0 = x + float(seg) * third + (seg == 0 ? 2 * k : k), x1 = x + float(seg + 1) * third - (seg == 2 ? 2 * k : k);
        md_fill_rounded(im, x0, y + 2 * k, x1, y + h - 2 * k, (h - 4 * k) / 2, active ? m.primary_bright : m.primary, 1.f);
    }
    static constexpr std::pair<MdIcon, float> kIcons[kLevelSegments] = {{MdIcon::rotate_left, 18.f}, {MdIcon::level, 23.f}, {MdIcon::rotate_right, 18.f}};
    for (int seg = 0; seg < int(kLevelSegments); ++seg)
        md_icon(im, kIcons[seg].first, x + (float(seg) + 0.5f) * third, y + h / 2, kIcons[seg].second * k,
                seg == int(hot) ? m.on_primary : m.on_primary_container);
}
// A 3D button control's container and ink: glass.hpp immersive_main_box.
std::pair<MdRgb, MdRgb> button_roles(const MaterialScheme& m, bool lit, bool hot) {
    if (lit) return {hot ? m.primary_bright : m.primary, m.on_primary};
    return hot ? std::pair{m.primary_container, m.on_primary_container} : std::pair{m.secondary_container, m.on_secondary_container};
}
void draw_button_main(HudImage& im, const AtlasCell& c, const MaterialScheme& m, const HudFont* font, bool lit, bool hot) {
    const float k = kHandleAtlasScale, x = float(c.x), y = float(c.y), w = float(c.w), h = float(c.h);
    const auto [fill, ink] = button_roles(m, lit, hot);
    md_fill_rounded(im, x, y, x + w, y + h, kImmersiveInnerR * k, fill, 1.f);
    if (!font) {  // the pixel font: drawn centred on its 5 x 7 cells
        constexpr std::uint32_t z = 5;
        const int px_w = int((2 * kGlyphW + 1) * z), left = int(std::lround(x + w / 2)) - px_w / 2, top = int(std::lround(y + h / 2)) - int(kGlyphH * z) / 2;
        for (int n = 0; n < 2; ++n) {
            const auto& g = hud_glyph("3D"[n]);
            for (std::uint32_t row = 0; row < kGlyphH * z; ++row)
                for (std::uint32_t col = 0; col < kGlyphW * z; ++col)
                    if (g[row / z] >> (kGlyphW - 1 - col / z) & 1) md_over(im, left + n * int((kGlyphW + 1) * z) + int(col), top + int(row), ink, 1.f);
        }
        return;
    }
    // Centred on its ink, not its advance, as the design is: where the glyphs really land, measured by drawing them.
    constexpr const char* label = "3D";
    const auto px = std::uint32_t(std::lround(kImmersiveTextPx * k));
    int x0 = 1 << 20, x1 = -(1 << 20), y0 = x0, y1 = x1;
    font->draw(label, px, true, 0, 0, [&](int gx, int gy, float coverage) {
        if (coverage > 0) { x0 = std::min(x0, gx); x1 = std::max(x1, gx); y0 = std::min(y0, gy); y1 = std::max(y1, gy); }
    });
    const int left = int(std::lround(x + w / 2 - float(x0 + x1 + 1) / 2)), baseline = int(std::lround(y + h / 2 - float(y0 + y1 + 1) / 2));
    font->draw(label, px, true, left, baseline, [&](int gx, int gy, float coverage) { md_over(im, gx, gy, ink, coverage); });
}
void draw_button_tail(HudImage& im, const AtlasCell& c, const MaterialScheme& m, bool lit, bool hot) {
    const float k = kHandleAtlasScale, x = float(c.x), y = float(c.y), w = float(c.w), h = float(c.h);
    const auto [fill, ink] = button_roles(m, lit, hot);
    // One shape: the small inner corners on its left half, the capsule's end on its right. Both are flat where they meet.
    const float cx = x + w / 2, cy0 = y + h / 2;
    for (std::uint32_t py = c.y; py < c.y + c.h; ++py)
        for (std::uint32_t px = c.x; px < c.x + c.w; ++px) {
            const float fx = float(px) + 0.5f, fy = float(py) + 0.5f;
            const float d = md_rounded_distance(fx, fy, cx, cy0, w / 2, h / 2, fx < cx ? kImmersiveInnerR * k : h / 2);
            const float cover = std::clamp(0.5f - d, 0.f, 1.f);
            if (cover > 0) md_over(im, int(px), int(py), fill, cover);
        }
    const float tw = kImmersiveTickW * k, th = kImmersiveTickH * k, gap = kImmersiveTickGap * k, cy = y + h / 2;
    const float left = x + w / 2 - 1 * k - 1.5f * tw - gap;  // a tick left of centre, for the round end's weight
    for (int i = 0; i < 3; ++i)
        md_fill_rounded(im, left + float(i) * (tw + gap), cy - th / 2, left + float(i) * (tw + gap) + tw, cy + th / 2, kImmersiveTickR * k, ink, 1.f);
}
void draw_cap(HudImage& im, const AtlasCell& c, std::uint32_t corner, float leg, float stroke, MdRgb colour) {
    const float k = kHandleAtlasScale, in = kCapInner * k;
    const bool right = corner == 1 || corner == 3, bottom = corner >= 2;
    for (std::uint32_t py = 0; py < c.h; ++py)
        for (std::uint32_t px = 0; px < c.w; ++px) {
            // The cell from its corner, x and y pointing away from the desktop: the same L in every corner.
            const float u = right ? float(px) + 0.5f - in : float(c.w) - in - float(px) - 0.5f;
            const float v = bottom ? float(py) + 0.5f - in : float(c.h) - in - float(py) - 0.5f;
            const float cov = clamp01(0.5f - md_corner_distance(u, v, kCapGap * k, leg * k, kCapBend * k, stroke * k));
            if (cov > 0) md_over(im, int(c.x + px), int(c.y + py), colour, cov);
        }
}
}

HudImage render_handle_atlas(std::array<float, 3> accent, const HudFont* font) {
    const auto& m = glass_theme_scheme(accent);
    HudImage im{kAtlasW, kAtlasH, std::vector<std::uint8_t>(std::size_t(kAtlasW) * kAtlasH * 4, 0)};
    draw_bar(im, bar_cell(bar_row(LevelSegment::none, false)), m, LevelSegment::none, false);
    for (const auto segment : {LevelSegment::ccw, LevelSegment::level, LevelSegment::cw})
        for (const bool active : {false, true}) draw_bar(im, bar_cell(bar_row(segment, active)), m, segment, active);
    for (std::uint32_t corner = 0; corner < kCapCorners; ++corner) {
        draw_cap(im, cap_cell(corner, 0), corner, kCapLeg, kCapStroke, m.primary);
        draw_cap(im, cap_cell(corner, 1), corner, kCapLegHot, kCapStrokeHot, m.primary_bright);
        draw_cap(im, cap_cell(corner, 2), corner, kCapLegHot, kCapStrokeHot, m.primary_bright);
    }
    for (const bool lit : {false, true})
        for (const bool hot : {false, true}) {
            draw_button_main(im, main_cell(lit, hot), m, font, lit, hot);
            draw_button_tail(im, tail_cell(lit, hot), m, lit, hot);
        }
    // Maximize and Restore: a capsule in the bar's colours with its icon centred; the Options pill's gear, on nothing (the glass is the pill).
    for(int state=0;state<4;++state) {
      const float x=2+float(state)*kAtlasMaximizePitch,y=float(kAtlasOldH)+2,w=kAtlasMaximizeW,cx=x+w/2,cy=y+39;
      const bool hot=state>=2;
      md_fill_rounded(im,x,y,x+w,y+78,39,hot?m.primary:m.primary_container,1);
      const MdRgb ink=hot?m.on_primary:m.on_primary_container;
      md_stroke_rounded(im,cx-17,cy-17,cx+17,cy+17,2,4,ink,1);  // one window
    }
    {float x=2,y=float(kAtlasOldH)+84;md_fill_rounded(im,x,y,x+250,y+60,30,m.surface_container_highest,1);md_stroke_rounded(im,x,y,x+250,y+60,30,3,m.outline_variant,1);text(im,font,"Maximize",40,true,x+24,y+30,m.on_surface);}
    // The band's Restore pill: two windows and the word, in the bar's colours, the primary under the dot.
    for(int state=0;state<2;++state){
      const float x=2+float(state)*(kAtlasPillW+4),y=kAtlasPillY,cy=y+39,gx=x+60;
      md_fill_rounded(im,x,y,x+kAtlasPillW,y+78,39,state?m.primary:m.primary_container,1);
      const MdRgb ink=state?m.on_primary:m.on_primary_container;
      md_stroke_rounded(im,gx-10,cy-19,gx+20,cy+10,2,4,ink,1);md_stroke_rounded(im,gx-20,cy-10,gx+10,cy+19,2,4,ink,1);
      text(im,font,"Restore",font?54u:0u,true,x+108,cy,ink);
    }
    const float gx=kAtlasGearX,gy=float(kAtlasOldH)+2;
    for(int tooth=0;tooth<8;++tooth){float a=float(tooth)*std::numbers::pi_v<float>/4;md_disc(im,gx+21+13*std::cos(a),gy+21+13*std::sin(a),4,m.on_primary_container,1);}
    md_disc(im,gx+21,gy+21,13,m.on_primary_container,1);md_disc(im,gx+21,gy+21,6,m.primary_container,1);
    return im;
}
AtlasBox options_gear_box(){return box_of({std::uint32_t(kAtlasGearX),kAtlasOldH+2,42,42});}
AtlasBox options_icon_box(bool hot){return box_of({2+2*std::uint32_t(hot)*std::uint32_t(kAtlasMaximizePitch),kAtlasOldH+2,std::uint32_t(kAtlasMaximizeW),78});}
AtlasBox options_hint_box(){return box_of({2,kAtlasOldH+84,250,60});}
AtlasBox options_restore_box(bool hot){return box_of({2+std::uint32_t(hot)*std::uint32_t(kAtlasPillW+4),std::uint32_t(kAtlasPillY),std::uint32_t(kAtlasPillW),78});}

AtlasBox level_bar_box(LevelSegment segment, bool active) { return box_of(bar_cell(bar_row(segment, active))); }
AtlasBox immersive_main_box(bool lit, bool hot) { return box_of(main_cell(lit, hot)); }
AtlasBox immersive_tail_box(bool lit, bool hot) { return box_of(tail_cell(lit, hot)); }
AtlasBox corner_cap_box(std::uint32_t corner, bool hot, bool active) { return box_of(cap_cell(std::min(corner, kCapCorners - 1), active ? 2 : hot ? 1 : 0)); }

FRect glass_panel_action(std::size_t action) {
    if (action >= kPanelActions) return {};
    return {kPanelPad + float(action) * (kPanelActionW + kPanelActionGap), kPanelActionsTop, kPanelActionW, kPanelActionH};
}

const char* glass_panel_action_label(std::size_t action, bool armed) {
    switch (action) {
    case 0: return "Ultrawide";
    case 1: return "Add new";
    case 2: return armed ? "Remove?" : "Remove";
    default: return "";
    }
}

bool glass_panel_action_enabled(std::size_t action, const PlacementMenuState& state) {
    if (state.busy || action >= kPanelActions) return false;
    if (action == 1) return state.count < kPlacementMenuDesktopCount;
    if (action == 2) return state.count > 1;
    return true;
}

FRect glass_scale_box() { return {kScaleBoxX, kScaleBoxY, kScaleBoxW, kScaleBoxH}; }

FRect glass_scale_item(std::size_t step) {
    if (step >= kScaleSteps) return {};
    const float top = kScaleBoxY - kScaleListGap - float(kScaleSteps) * kScaleItemH;
    return {kScaleBoxX, top + float(step) * kScaleItemH, kScaleBoxW, kScaleItemH};
}

bool glass_scale_enabled(std::size_t step, const PlacementMenuState& state) {
    return !state.busy && step < kScaleSteps && (state.scales >> step & 1u);
}

std::string glass_scale_label(std::uint16_t percent) { return percent ? std::to_string(unsigned(percent)) + "%" : "--"; }

FRect glass_background_box() { return {kBackgroundBoxX, kBackgroundBoxY, kBackgroundBoxW, kBackgroundBoxH}; }

FRect glass_background_switch() { return {kBackgroundSwitchX, kBackgroundBoxY, kBackgroundSwitchW, kBackgroundSwitchH}; }

FRect glass_background_item(std::size_t item) {
    if (item >= kBackgroundCount) return {};
    const float top = kBackgroundBoxY - kBackgroundListGap - float(kBackgroundCount) * kBackgroundItemH;
    return {kBackgroundBoxX, top + float(item) * kBackgroundItemH, kBackgroundBoxW, kBackgroundItemH};
}

FRect glass_resolution_box() { return {kResBoxX, kResBoxY, kResBoxW, kResBoxH}; }

FRect glass_resolution_list() { return {kResListX, kResListY, kResListW, kResListH}; }

FRect glass_resolution_heading(std::size_t group) {
    if (group >= kResolutionGroupCount) return {};
    const float w = kResListW / float(kResolutionGroupCount);
    return {kResListX + float(group) * w, kResListY, w, kResItemH};
}

FRect glass_resolution_item(std::size_t i) {
    if (i >= kResolutionCount) return {};
    std::size_t group = 0, row = i;
    while (row >= kResolutionGroups[group].count) row -= kResolutionGroups[group++].count;
    const FRect column = glass_resolution_heading(group);
    return {column.x, column.y + float(1 + row) * kResItemH, column.width, kResItemH};
}

bool glass_resolution_enabled(std::size_t i, const PlacementMenuState& state) { return !state.busy && i < kResolutionCount; }

std::string glass_resolution_label(std::uint16_t width, std::uint16_t height) {
    return width && height ? std::to_string(unsigned(width)) + "x" + std::to_string(unsigned(height)) : "--";
}

FRect glass_morph(const FRect& t, float k) {
    k = clamp01(k);
    const FRect a = glass_pill(t), b = glass_panel(t);
    const float w = lerp(a.width, b.width, k), h = lerp(a.height, b.height, k);
    return {t.x + (t.width - w) / 2, a.y, w, h};  // grows from the pill's top centre
}

float glass_radius(const FRect& t, float k) {
    const FRect m = glass_morph(t, k);
    return std::min(lerp(kGlassPillH / 2, kPanelRadius, clamp01(k)), std::min(m.width, m.height) / 2);
}

float glass_state_opacity(float level) { return by_state(level, 0.f, kMdStateHover, kMdStateDragged); }

GlassLook glass_look(std::array<float, 3> accent, float level, float t) {
    t = clamp01(t);
    const auto& m = glass_theme_scheme(accent);  // the Omarchy theme's M3 scheme (glass_theme.hpp)
    GlassLook k;
    for (std::size_t i = 0; i < 3; ++i) {
        k.container[i] = lerp(m.primary_container[i], m.surface_container_high[i], t);  // a tonal pill, then a dialog surface
        k.state[i] = lerp(m.on_primary_container[i], m.on_surface[i], t);              // each one's content colour
    }
    k.outline = m.outline_variant;
    k.outline_alpha = t;  // the open panel's outlined edge (the pill has none)
    k.fill = lerp(1.f, kPanelFill, t);
    k.state_alpha = glass_state_opacity(level);
    k.state_px = lerp(kGlassPillH, kPanelHeaderH, t);  // the whole pill, the open panel's header strip
    const float pill = by_state(level, kMdElevationPill, kMdElevationHover, kMdElevationDragged);
    const float panel = by_state(level, kMdElevationDialog, kMdElevationDialog, kMdElevationDragged);
    const MdShadow shadow = md_elevation(lerp(pill, panel, t));
    k.shadow = shadow.alpha; k.shadow_sigma = shadow.sigma; k.shadow_dy = shadow.dy;
    return k;
}

GlassLook grabber_look(float level) {
    GlassLook k;
    k.container = glass_theme_scheme(kGlassThemeDefault).on_surface_variant;  // a neutral grey: it reads on any theme
    k.fill = by_state(level, 0.40f, 0.55f, 0.70f);
    return k;
}

std::array<float, 4> glass_shade(const GlassLook& k, const FRect& rect, float radius, float px, float py, float opacity) {
    const float cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2, hw = rect.width / 2, hh = rect.height / 2;
    auto box = [&](float qx, float qy) {
        const float dx = std::fabs(qx) - (hw - radius), dy = std::fabs(qy) - (hh - radius);
        const float ox = std::max(dx, 0.f), oy = std::max(dy, 0.f);
        return std::sqrt(ox * ox + oy * oy) + std::min(std::max(dx, dy), 0.f) - radius;
    };
    const float d = box(px - cx, py - cy);
    const float inside = clamp01(0.5f - d);
    const float top = py - (cy - hh);
    const float st = k.state_alpha * clamp01(k.state_px - top + 0.5f);  // the state layer over the top state_px
    float a = k.fill;
    std::array<float, 3> c{};
    for (std::size_t i = 0; i < 3; ++i) c[i] = (k.container[i] * (1 - st) + k.state[i] * st) * a;
    const float b = clamp01(1.f - std::fabs(d + 0.5f)) * k.outline_alpha;  // 1 px just inside the edge
    for (std::size_t i = 0; i < 3; ++i) c[i] = c[i] * (1 - b) + k.outline[i] * b;
    a = a + b * (1 - a);
    for (auto& v : c) v *= inside;
    a *= inside;
    const float ds = std::max(box(px - cx, py - cy - k.shadow_dy), 0.f);
    const float sigma = std::max(k.shadow_sigma, 1e-3f);
    const float s = k.shadow * std::exp(-ds * ds / (2 * sigma * sigma)) * (1 - inside);
    a = a + s * (1 - a);
    return {std::min(c[0], kGlassCap) * opacity, std::min(c[1], kGlassCap) * opacity, std::min(c[2], kGlassCap) * opacity, a * opacity};
}

std::array<float, 4 * kGlassStyleVecs> glass_style(const GlassLook& k) {
    return {k.container[0], k.container[1], k.container[2], k.fill, k.outline[0], k.outline[1], k.outline[2], k.outline_alpha,
            k.state[0],     k.state[1],     k.state[2],     k.state_alpha, k.state_px, k.shadow, k.shadow_sigma, k.shadow_dy};
}

// Mirrors glass_shade() line for line; style = glass_style().
const char* glass_fragment_shader() {
    return "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n"
           "varying vec2 p; uniform vec4 rect; uniform float radius; uniform vec4 style[4]; uniform float opacity;\n"
           "const float kCap = 0.74901961;\n"
           "float box(vec2 q) { vec2 d = abs(q) - (rect.zw - vec2(radius));\n"
           "  return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - radius; }\n"
           "void main() {\n"
           "  float d = box(p - rect.xy);\n"
           "  float inside = clamp(0.5 - d, 0.0, 1.0);\n"
           "  float top = p.y - (rect.y - rect.w);\n"
           "  float st = style[2].a * clamp(style[3].x - top + 0.5, 0.0, 1.0);\n"
           "  float a = style[0].a;\n"
           "  vec3 c = (style[0].rgb * (1.0 - st) + style[2].rgb * st) * a;\n"
           "  float b = clamp(1.0 - abs(d + 0.5), 0.0, 1.0) * style[1].a;\n"
           "  c = c * (1.0 - b) + style[1].rgb * b; a = a + b * (1.0 - a);\n"
           "  c *= inside; a *= inside;\n"
           "  float ds = max(box(p - rect.xy - vec2(0.0, style[3].w)), 0.0);\n"
           "  float sigma = max(style[3].z, 0.001);\n"
           "  float s = style[3].y * exp(-ds * ds / (2.0 * sigma * sigma)) * (1.0 - inside);\n"
           "  a = a + s * (1.0 - a);\n"
           "  gl_FragColor = vec4(min(c, vec3(kCap)) * opacity, a * opacity);\n"
           "}\n";
}

std::string glass_panel_hint(bool ascii) {
    return ascii ? "U/D FIELD  L/R ADJUST  TAB  R RESET" : "\xe2\x86\x91\xe2\x86\x93 field  \xe2\x86\x90\xe2\x86\x92 adjust  Tab next  R reset";
}

PanelButtonStyle glass_panel_button_style(std::size_t action, const PlacementMenuState& state, std::array<float, 3> accent) {
    const auto& m = glass_theme_scheme(accent);
    if (!glass_panel_action_enabled(action, state))  // M3 disabled: on-surface at 12% / 38%
        return {m.on_surface, m.on_surface, kMdDisabledContainer, kMdDisabledContent};
    if (action == 2 && state.armed) return {m.error_container, m.on_error_container, 1.f, 1.f};  // asks before it removes
    if (action == 0 && state.wide) return {m.primary, m.on_primary, 1.f, 1.f};                    // a toggle that is on
    return {m.secondary_container, m.on_secondary_container, 1.f, 1.f};                            // filled tonal
}

namespace {
// `s` cut to `width` px: a value can be longer than any column. By UTF-8 characters, with an ellipsis (three dots in the pixel font).
std::string fit_text(const HudFont* font, std::string s, std::uint32_t px, bool bold, float width) {
    if (float(text_width(font, s, px, bold)) <= width) return s;
    const std::string more = font ? "\xe2\x80\xa6" : "...";
    while (!s.empty()) {
        do s.pop_back(); while (!s.empty() && (std::uint8_t(s.back()) & 0xc0) == 0x80);
        if (float(text_width(font, s + more, px, bold)) <= width) break;
    }
    return s + more;
}
const MdIcon* options_row_icon(const std::string& id) {
    static constexpr MdIcon add = MdIcon::add, remove = MdIcon::remove, width = MdIcon::width, left = MdIcon::rotate_left, right = MdIcon::rotate_right, level = MdIcon::level;
    return id == "add_monitor" ? &add : id == "remove_monitor" ? &remove : id == "ultrawide" ? &width : id == "roll_ccw" ? &left : id == "roll_cw" ? &right : id == "roll_level" ? &level : nullptr;
}
// A row's value as the placement panel prints it: hud.cpp placement_menu_rows's units.
std::string options_shown(const OptionsRow& r, double v) {
    const auto whole = [](double d) { return std::to_string(std::llround(d)); };
    if (r.unit == "0.1 deg") { const auto n = std::llround(std::fabs(v) / 10); return (v > 0 && n ? "+" : v < 0 && n ? "-" : "") + std::to_string(n) + "\xc2\xb0"; }
    if (r.unit == "deg") return whole(v) + "\xc2\xb0";
    if (r.unit == "%") return whole(v) + "%";
    return r.value + (r.unit.empty() ? "" : " " + r.unit);
}
// A choice as the placement panel prints it: Scale in percent.
std::string options_choice_label(const OptionsRow& r, const std::string& value) {
    if (r.id != "scale") return value;
    try { return glass_scale_label(std::uint16_t(std::lround(std::stod(value) * 100))); } catch (const std::exception&) { return value; }
}
constexpr std::uint32_t kConfirmPx = 14;  // a confirmation's sentence, on two lines of a row's button
void options_row(HudImage& im, const HudFont* font, const MaterialScheme& m, const OptionsRow& r, float top, bool focused, bool listed) {
    const float x0 = kOptionsPad, x1 = kOptionsW - kOptionsPad, cy = top + kOptionsRowH / 2, alpha = r.enabled ? 1.f : kMdDisabledContent;
    const auto label_px = font ? kButtonLabelPx : 0u;
    if (r.kind == "action") {
        const FRect b{x0, top + (kOptionsRowH - kPanelActionH) / 2, x1 - x0, kPanelActionH};
        const PanelButtonStyle st = !r.enabled ? PanelButtonStyle{m.on_surface, m.on_surface, kMdDisabledContainer, kMdDisabledContent}
                                  : r.id == "confirmation.apply" ? PanelButtonStyle{m.error_container, m.on_error_container, 1.f, 1.f}
                                                                 : PanelButtonStyle{m.secondary_container, m.on_secondary_container, 1.f, 1.f};
        const float room = b.width - 2 * kFieldPadX;
        if (float(text_width(font, r.label, label_px, true)) <= room) panel_button(im, font, b, r.label, st, label_px, options_row_icon(r.id));
        else {  // two lines at the nearest space to the middle, as much of them as fits
            md_fill_rounded(im, b.x, b.y, b.x + b.width, b.y + b.height, b.height / 2, st.container, st.container_alpha);
            std::size_t cut = r.label.size() / 2;
            for (std::size_t d = 0; d < r.label.size() / 2; ++d) {
                if (r.label[r.label.size() / 2 + d] == ' ') { cut = r.label.size() / 2 + d; break; }
                if (r.label[r.label.size() / 2 - d] == ' ') { cut = r.label.size() / 2 - d; break; }
            }
            const auto px = font ? kConfirmPx : 0u;
            for (int n = 0; n < 2; ++n) {
                const std::string line = fit_text(font, n ? r.label.substr(std::min(cut + 1, r.label.size())) : r.label.substr(0, cut), px, true, room);
                text(im, font, line, px, true, b.x + (b.width - float(text_width(font, line, px, true))) / 2, cy + (n ? 8.f : -8.f), st.content, st.content_alpha);
            }
        }
        if (focused) md_stroke_rounded(im, b.x, b.y, b.x + b.width, b.y + b.height, b.height / 2, 2.f, m.primary, 1.f);
        return;
    }
    if (focused) selected_band(im, m, kOptionsW, top);
    const MdRgb ink = focused ? m.on_secondary_container : m.on_surface, quiet = focused ? m.on_secondary_container : m.on_surface_variant;
    if (r.kind == "text") {  // a child's text field: a label in the button font, then the box with no arrow
        text(im, font, r.label, label_px, false, x0, cy, ink, alpha);
        const float bx = x0 + float(text_width(font, r.label, label_px, false)) + kFieldPadX;
        const FRect box{bx, top + (kOptionsRowH - kScaleBoxH) / 2, x1 - bx, kScaleBoxH};
        dropdown_box(im, font, m, box, fit_text(font, r.value, label_px, true, box.width - 2 * kFieldPadX), label_px, false, r.enabled, false);
        return;
    }
    const float column = r.kind == "readonly" ? (x1 - x0) * .62f : kOptionsTrackX0 - kFieldPadX - x0;
    text(im, font, fit_text(font, r.label, kPanelTextPx, focused, column), kPanelTextPx, focused, x0, cy, ink, alpha);
    if (r.kind == "range" && r.maximum > r.minimum) {
        double value = r.minimum;
        try { value = std::stod(r.value); } catch (const std::exception&) {}  // value is presentation text; never execute or change it
        const auto at = [&](double v) { return kOptionsKnobLo + (kOptionsKnobHi - kOptionsKnobLo) * float((std::clamp(v, r.minimum, r.maximum) - r.minimum) / (r.maximum - r.minimum)); };
        slider_parts(im, m, kOptionsTrackX0, kOptionsTrackX1, cy, at(r.id == "placement_distance" || r.id == "background_brightness" ? 100.0 : r.unit == "%" ? r.minimum : std::clamp(0.0, r.minimum, r.maximum)), at(value), focused, alpha);
        const std::string shown = options_shown(r, value);
        text(im, font, shown, kPanelTextPx, focused, x1 - float(text_width(font, shown, kPanelTextPx, focused)), cy, quiet, alpha);
    } else if (r.kind == "toggle") {
        panel_switch(im, m, {x1 - kBackgroundSwitchW, top + (kOptionsRowH - kBackgroundSwitchH) / 2, kBackgroundSwitchW, kBackgroundSwitchH}, r.value == "true", alpha);
    } else if (r.kind == "choice") {
        const FRect box{kOptionsTrackX0, top + (kOptionsRowH - kScaleBoxH) / 2, x1 - kOptionsTrackX0, kScaleBoxH};
        dropdown_box(im, font, m, box, fit_text(font, options_choice_label(r, r.value), label_px, true, box.width - kFieldPadX - kCaretInset - 10), label_px, listed, r.enabled);
    } else {
        const auto px = font ? kButtonLabelPx : 0u;
        const std::string value = fit_text(font, r.value.empty() ? (font ? "\xe2\x80\x94" : "-") : r.value + (r.unit.empty() ? "" : " " + r.unit), px, false, x1 - x0 - column - kFieldPadX);
        text(im, font, value, px, false, x1 - float(text_width(font, value, px, false)), cy, quiet, alpha);
    }
}
}

HudImage render_options_panel(const OptionsMenuState& state, const HudFont* font, std::array<float, 3> accent) {
    const auto& m = glass_theme_scheme(accent);
    HudImage im;
    im.width = std::uint32_t(kOptionsW); im.height = std::uint32_t(kOptionsH);
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);  // transparent: the glass under it is the placement panel's
    const auto label_px = font ? kButtonLabelPx : 0u;
    const float W = kOptionsW;
    panel_header(im, font, m, state.monitor, W);
    const FRect close = options_close_box();
    const std::string title = "Options";
    text(im, font, title, kPanelTextPx, false, close.x - kFieldPadX - float(text_width(font, title, kPanelTextPx, false)), kHeaderCy, m.on_surface_variant);
    panel_button(im, font, close, "Close", {m.secondary_container, m.on_secondary_container, 1.f, 1.f}, label_px, nullptr);
    if (state.focus == kOptionsFocusClose) md_stroke_rounded(im, close.x, close.y, close.x + close.width, close.y + close.height, close.height / 2, 2.f, m.primary, 1.f);
    // Section: the placement panel's dropdown row. Scope: the level bar as two segments, the shown one lit.
    const auto section = std::find_if(kOptionsSections.begin(), kOptionsSections.end(), [&](const OptionsSection& s) { return state.category == s.id; });
    const bool listed = state.focus == kOptionsFocusList;
    const FRect box = options_section_box();
    dropdown_row(im, font, m, "Section", kOptionsSectionTop, box, section == kOptionsSections.end() ? state.category : section->label, label_px, listed, true);
    if (state.focus == kOptionsFocusSection) md_stroke_rounded(im, box.x, box.y, box.x + box.width, box.y + box.height, kMdShapeExtraSmall, 2.f, m.primary, 1.f);
    text(im, font, "Scope", kPanelTextPx, false, kPanelPad, kOptionsScopeTop + kPanelRowH / 2, m.on_surface);
    const FRect scope = options_scope_box();
    md_fill_rounded(im, scope.x, scope.y, scope.x + scope.width, scope.y + scope.height, scope.height / 2, m.primary_container, 1.f);
    for (int part = 0; part < 2; ++part) {
        const bool lit = (state.scope == "monitor") == (part == 0);
        const float x0 = scope.x + float(part) * scope.width / 2, x1 = x0 + scope.width / 2;
        if (lit) md_fill_rounded(im, x0 + 2, scope.y + 2, x1 - 2, scope.y + scope.height - 2, (scope.height - 4) / 2, m.primary, 1.f);
        if (lit && state.focus == kOptionsFocusScope) md_stroke_rounded(im, x0, scope.y, x1, scope.y + scope.height, scope.height / 2, 2.f, m.primary_bright, 1.f);
        const std::string name = part ? "Workspace" : state.monitor;
        text(im, font, name, label_px, true, x0 + (x1 - x0 - float(text_width(font, name, label_px, true))) / 2, scope.y + scope.height / 2, lit ? m.on_primary : m.on_primary_container);
    }
    for (int x = int(kPanelPad); x < int(W - kPanelPad); ++x) md_over(im, x, int(kOptionsDividerY), m.outline_variant, 1.f);
    // The rows, scrolled inside their own strip: a row half way out is cut there, as the list's edge.
    HudImage body;
    body.width = im.width; body.height = std::uint32_t(kOptionsBodyH);
    body.rgba.assign(std::size_t(body.width) * body.height * 4, 0);
    for (std::size_t i = 0; i < state.rows.size(); ++i) {
        const float y = float(i) * kOptionsRowH - float(state.scroll);
        if (y + kOptionsRowH <= 0 || y >= kOptionsBodyH) continue;
        options_row(body, font, m, state.rows[i], y, int(i) == state.focus, int(i) == state.focus && state.list >= 0);
    }
    for (std::uint32_t y = 0; y < body.height; ++y)
        std::copy_n(body.rgba.begin() + std::ptrdiff_t(y) * std::ptrdiff_t(body.width) * 4, body.width * 4,
                    im.rgba.begin() + std::ptrdiff_t(y + std::uint32_t(kOptionsBodyTop)) * std::ptrdiff_t(im.width) * 4);
    const float content = float(state.rows.size()) * kOptionsRowH;
    if (content > kOptionsBodyH) {  // where the list is: a track in the sliders' colours, in the right margin
        const float x = W - kPanelPad + 6, thumb = std::max(kOptionsBodyH * kOptionsBodyH / content, 2 * kTrackH);
        const float top = kOptionsBodyTop + (kOptionsBodyH - thumb) * std::min(float(state.scroll) / (content - kOptionsBodyH), 1.f);
        md_fill_rounded(im, x, kOptionsBodyTop, x + kTrackH, kOptionsBodyBottom, kTrackH / 2, m.primary, kTrackAlpha);
        md_fill_rounded(im, x, top, x + kTrackH, top + thumb, kTrackH / 2, m.primary, 1.f);
    }
    // Footer, in the placement footer's size and place: the keys, or what the last change is doing, which needs the room more.
    const std::string hint = font ? "\xe2\x86\x91\xe2\x86\x93 row  \xe2\x86\x90\xe2\x86\x92 adjust  Tab next  Esc close" : "U/D ROW  L/R ADJUST  TAB  ESC";
    const auto hint_px = font ? kPanelHintPx : 0u;
    const float footer = kOptionsFooterTop + float(kPanelHintPx) / 2;
    const std::string said = state.status == "idle" ? "" : state.message.empty() ? state.status : state.message;
    if (said.empty()) text(im, font, hint, hint_px, false, W - kPanelPad - float(font ? font->measure(hint, hint_px, false) : hud_text_width(hint, 2)), footer, m.outline);
    else text(im, font, fit_text(font, said, hint_px, false, W - 2 * kPanelPad), hint_px, false, kPanelPad, footer, state.status == "error" ? m.on_surface : m.on_surface_variant);
    if (state.list >= 0 && state.focus >= 0 && std::size_t(state.focus) < state.rows.size()) {  // a row's open list, last: the Scale list's frame and items
        const auto& r = state.rows[std::size_t(state.focus)];
        const std::size_t n = r.choices.size();
        const float row_y = kOptionsBodyTop + float(state.focus) * kOptionsRowH - float(state.scroll);
        if (n) {
            const FRect first = options_choice_item(row_y, n, 0);
            list_frame(im, m, first.x, first.y - 1, kOptionsBoxX1, first.y + float(options_choice_rows(n)) * kScaleItemH + 1);
            for (std::size_t i = 0; i < n; ++i)
                list_item(im, font, m, options_choice_item(row_y, n, i), fit_text(font, options_choice_label(r, r.choices[i]), label_px, true, kOptionsChoiceItemW - 2 * kFieldPadX), label_px, int(i) == state.list, true);
        }
    }
    if (listed) {  // the open section list, last: the Resolution list's frame and items, over what is under it
        const FRect first = options_section_item(0);
        list_frame(im, m, first.x, first.y - 1, kOptionsBoxX1, first.y + float(kOptionsListRows) * kResItemH + 1);
        for (std::size_t i = 0; i < kOptionsSections.size(); ++i)
            list_item(im, font, m, options_section_item(i), kOptionsSections[i].label, label_px, state.category == kOptionsSections[i].id, true);
    }
    return im;
}

HudImage render_glass_panel(const PlacementMenuState& state, const HudFont* font, std::array<float, 3> accent) {
    const auto& m = glass_theme_scheme(accent);
    HudImage im;
    im.width = std::uint32_t(kPanelW); im.height = std::uint32_t(kPanelH);
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);
    const float W = kPanelW;
    const std::uint32_t desktop = std::min<std::uint32_t>(state.desktop, kPlacementMenuDesktopCount - 1);
    // Header: the desktop on the left, what this is on the right (on-surface-variant); the drag handle sits between them.
    panel_header(im, font, m, "XR-" + std::to_string(unsigned(state.numbers[desktop])), W);
    const std::string title = "Placement";
    text(im, font, title, kPanelTextPx, false, W - kPanelPad - float(text_width(font, title, kPanelTextPx, false)), kHeaderCy, m.on_surface_variant);
    // Rows: an M3 list of sliders. The selected row is the active indicator (secondary container, fully rounded) with its
    // text in on-secondary-container; the others' labels on-surface, their values on-surface-variant.
    // One slider row: `alpha` < 1 is M3 disabled.
    auto slider_row = [&](float top, const std::string& label, std::int32_t lo, std::int32_t hi, std::int32_t flat, std::int32_t value,
                          const std::string& shown, bool selected, float alpha) {
        const float cy = top + kPanelRowH / 2;
        if (selected) selected_band(im, m, W, top);
        text(im, font, label, kPanelTextPx, selected, kPanelPad, cy, selected ? m.on_secondary_container : m.on_surface, alpha);
        auto at = [&](std::int32_t v) { return kPanelKnobLo + (kPanelKnobHi - kPanelKnobLo) * float(std::clamp(v, lo, hi) - lo) / float(hi - lo); };
        slider_parts(im, m, kPanelTrackX0, kPanelTrackX1, cy, at(flat), at(value), selected, alpha);
        text(im, font, shown, kPanelTextPx, selected, W - kPanelPad - float(text_width(font, shown, kPanelTextPx, selected)), cy,
             selected ? m.on_secondary_container : m.on_surface_variant, alpha);
    };
    const auto rows = placement_menu_rows(state, font == nullptr);
    const auto bounds = placement_menu_bounds();
    const auto flat = placement_menu_flat();
    const auto& values = state.placements[desktop];
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i)
        slider_row(kPanelRowsTop + float(i) * kPanelRowH, kLabels[i], bounds[i].first, bounds[i].second, flat[i], values[i], rows[i].value,
                   rows[i].selected, 1.f);
    // Everything clickable under the sliders is in M3 label large (bold) at kButtonLabelPx: the Resolution and Scale
    // boxes, their lists and the desktop row's buttons.
    const auto label_px = font ? kButtonLabelPx : 0u;
    // The Resolution row, then the Scale row: M3 exposed dropdown menus (dropdown_row), each M3 disabled while its value is
    // unknown or a desktop change is busy.
    dropdown_row(im, font, m, "Resolution", kPanelResolutionTop, glass_resolution_box(),
                 glass_resolution_label(state.resolution_w, state.resolution_h), label_px, state.resolution_picker,
                 state.resolution_w != 0 && state.resolution_h != 0 && !state.busy);
    dropdown_row(im, font, m, "Scale", kPanelScaleTop, glass_scale_box(), glass_scale_label(state.scale), label_px, state.picker,
                 state.scale != 0 && !state.busy);
    // The background: its dropdown, an M3 switch beside it (on: the primary track and an on-primary handle at its end; off:
    // an outlined track and a smaller outline handle), then the Brightness slider, M3 disabled while there is no background.
    const auto background = std::min<std::size_t>(state.background, kBackgroundCount - 1);
    dropdown_row(im, font, m, "Background", kPanelBackgroundTop, glass_background_box(), std::string(kBackgrounds[background].label), label_px,
                 state.background_picker, true);
    panel_switch(im, m, glass_background_switch(), background != 0);
    slider_row(kPanelBrightnessTop, "Brightness", kBrightnessMin, kBrightnessMax, kBrightnessDefault, state.brightness,
               std::to_string(unsigned(state.brightness)) + "%", state.field == kPlacementMenuBrightness, background != 0 ? 1.f : kMdDisabledContent);
    // Desktop row: fully rounded M3 buttons (glass_panel_button_style), each an icon and its label (M3 label large) when
    // every button has room for both.
    bool icons = true;
    for (std::size_t a = 0; a < kPanelActions; ++a)
        icons = icons && kButtonIconPx + kButtonIconGap + float(text_width(font, glass_panel_action_label(a, state.armed), label_px, true)) +
                             2 * kButtonPadMin <= kPanelActionW;
    for (std::size_t a = 0; a < kPanelActions; ++a)
        panel_button(im, font, glass_panel_action(a), glass_panel_action_label(a, state.armed), glass_panel_button_style(a, state, accent), label_px, icons ? &kActionIcons[a] : nullptr);
    // Footer: the keys that work while a panel is open (Esc and the pill itself close it), in the outline colour.
    const std::string hint = glass_panel_hint(font == nullptr);
    const auto hint_px = font ? kPanelHintPx : 0u;
    const float hint_w = float(font ? font->measure(hint, hint_px, false) : hud_text_width(hint, 2));
    text(im, font, hint, hint_px, false, std::max(kPanelPad, (W - hint_w) / 2), kPanelFooterTop + float(kPanelHintPx) / 2, m.outline);
    // An open list, last: an M3 menu over the rows above its box (surface container, extra small corners, an outline-variant
    // edge; opaque, so what is under it does not show), one item per choice in label large: the current one on the secondary
    // container, ones that cannot be picked M3 disabled (on-surface at 38%).
    auto frame_list = [&](const FRect& first, const FRect& last) { list_frame(im, m, first.x, first.y - 1, first.x + first.width, last.y + last.height + 1); };
    auto item = [&](const FRect& r, const std::string& label, bool current, bool enabled) { list_item(im, font, m, r, label, label_px, current, enabled); };
    if (state.picker) {
        frame_list(glass_scale_item(0), glass_scale_item(kScaleSteps - 1));
        for (std::size_t i = 0; i < kScaleSteps; ++i)
            item(glass_scale_item(i), glass_scale_label(kScalePercents[i]), kScalePercents[i] == state.scale, glass_scale_enabled(i, state));
    }
    if (state.background_picker) {
        frame_list(glass_background_item(0), glass_background_item(kBackgroundCount - 1));
        for (std::size_t i = 0; i < kBackgroundCount; ++i)
            item(glass_background_item(i), std::string(kBackgrounds[i].label), i == background, true);
    }
    // The open Resolution list, likewise an M3 menu over the slider rows (and their selected band, kBandInset in): a
    // column per shape, its name on top (on-surface-variant, regular) over an outline-variant divider, then its sizes in
    // label large; the current size on the secondary container, every size M3 disabled while busy.
    if (state.resolution_picker) {
        const FRect list = glass_resolution_list();
        const float x0 = list.x, y0 = list.y - 1, x1 = list.x + list.width, y1 = list.y + list.height + 1;
        md_fill_rounded(im, x0, y0, x1, y1, kMdShapeExtraSmall, m.surface_container, 1.f);
        md_stroke_rounded(im, x0, y0, x1, y1, kMdShapeExtraSmall, 1.f, m.outline_variant, 1.f);
        for (std::size_t g = 0; g < kResolutionGroupCount; ++g) {
            const FRect h = glass_resolution_heading(g);
            text(im, font, kResolutionGroups[g].name, label_px, false, h.x + kFieldPadX, h.y + h.height / 2, m.on_surface_variant);
        }
        for (int x = int(x0 + kFieldPadX / 2); x < int(x1 - kFieldPadX / 2); ++x)
            md_over(im, x, int(list.y + kResItemH) - 1, m.outline_variant, 1.f);
        for (std::size_t i = 0; i < kResolutionCount; ++i) {
            const FRect r = glass_resolution_item(i);
            const bool current = kResolutions[i].width == state.resolution_w && kResolutions[i].height == state.resolution_h;
            const bool enabled = glass_resolution_enabled(i, state);
            if (current)
                md_fill_rounded(im, r.x + 2, r.y + 1, r.x + r.width - 2, r.y + r.height - 1, kMdShapeExtraSmall, m.secondary_container, 1.f);
            text(im, font, glass_resolution_label(kResolutions[i].width, kResolutions[i].height), label_px, true, r.x + kFieldPadX,
                 r.y + r.height / 2, current ? m.on_secondary_container : m.on_surface, enabled || current ? 1.f : kMdDisabledContent);
        }
    }
    return im;
}

Placement placement_from_wire(const MenuPlacement& w) {
    constexpr float rad = std::numbers::pi_v<float> / 180.f;  // x / y (the orbit) are in tenths of a degree
    return {float(w[0]) * rad / 10.f, float(w[1]) * rad / 10.f, float(w[2]) / 100.f, float(w[3]) * rad};
}

std::vector<std::size_t> glass_draw_order(std::vector<std::size_t> order, int top) {
    const auto it = std::find(order.begin(), order.end(), std::size_t(top));
    if (top >= 0 && it != order.end()) std::rotate(it, it + 1, order.end());
    return order;
}

bool GlassPills::set(const PlacementMenuState& state, double now_ms, const std::optional<MenuPlacement>& surface) {
    bool changed = false;
    if (state.open && (!generation_ || !(state == rendered_))) {
        rendered_ = state;
        dirty_ = true; ++generation_; changed = true;
    } else if (!state.open && rendered_.open) {
        rendered_.open = false;  // keep the last content for the collapse
        changed = true;
    }
    const auto held = state.open ? surface : std::nullopt;
    if (held != surface_) { surface_ = held; changed = true; }
    for (std::uint32_t d = 0; d < kPlacementMenuDesktopCount; ++d) {
        const bool want = state.open && state.desktop == d;
        if (fades_[d].set(want, now_ms)) {
            if (want) ++opened_; else ++closed_;
            changed = true;
        }
    }
    return changed;
}
void GlassPills::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); dirty_ = generation_ != 0; }
bool GlassPills::set_accent(std::array<float, 3> accent) {
    if (accent == accent_) return false;
    accent_ = accent;
    if (!generation_) return false;  // nothing rendered yet: the first render uses it
    dirty_ = true; ++generation_;    // a new generation: the compositor uploads the re-rendered content
    return true;
}
void GlassPills::ensure() const {
    if (!dirty_) return;
    dirty_ = false;
    image_ = render_glass_panel(rendered_, font_.get(), accent_);
    ++renders_;
}
GlassFrame GlassPills::frame(bool overview, double now_ms) {
    GlassFrame out;
    if (!overview) {
        // Unseen: an expanding pill keeps its whole morph for when the overview is reached (a collapse just runs out).
        if (hidden_ms_ >= 0)
            for (auto& f : fades_) if (f.visible()) f.hold(hidden_ms_, now_ms);
        hidden_ms_ = now_ms;
        return out;
    }
    hidden_ms_ = -1;
    float most = 0;
    for (std::size_t i = 0; i < out.expand.size(); ++i) {
        out.expand[i] = expand(i, now_ms);
        if (out.expand[i] > most) { most = out.expand[i]; out.top = int(i); }
    }
    if (rendered_.open) out.top = int(rendered_.desktop);
    out.content_alpha = content_alpha(now_ms);
    if (out.content_alpha > 0.f) { out.content = content_desktop(); out.image = &image(); out.generation = generation_; }
    if (rendered_.open && surface_) { out.surface_desktop = int(rendered_.desktop); out.surface = placement_from_wire(*surface_); }
    return out;
}
float GlassPills::expand(std::size_t desktop, double now_ms) const {
    return desktop < fades_.size() ? fades_[desktop].alpha(now_ms) : 0.f;
}
bool GlassPills::animating(double now_ms) const {
    return std::any_of(fades_.begin(), fades_.end(), [&](const CardFade& f) { return f.animating(now_ms); });
}
bool GlassPills::any_expanded(double now_ms) const {
    return std::any_of(fades_.begin(), fades_.end(), [&](const CardFade& f) { return f.level(now_ms) > 0.f; });
}
float GlassPills::content_alpha(double now_ms) const {
    const int d = content_desktop();
    if (d < 0) return 0.f;
    return smooth(kContentFrom, 1.f, expand(std::size_t(d), now_ms));
}
}
