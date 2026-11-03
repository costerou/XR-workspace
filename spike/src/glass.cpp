#include "xr/glass.hpp"
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
void dropdown_row(HudImage& im, const HudFont* font, const MaterialScheme& m, const char* label, float row_top, const FRect& box,
                  const std::string& value, std::uint32_t label_px, bool open, bool usable) {
    const float cy = row_top + kPanelRowH / 2;
    const float x1 = box.x + box.width, y1 = box.y + box.height;
    const float content_alpha = usable ? 1.f : kMdDisabledContent;
    text(im, font, label, kPanelTextPx, false, kPanelPad, cy, m.on_surface);
    md_fill_rounded(im, box.x, box.y, x1, y1, kMdShapeExtraSmall, usable ? m.surface_container_highest : m.on_surface,
                    usable ? 1.f : kFieldDisabled);
    const float indicator = open ? kIndicatorFocusH : kIndicatorH;
    md_fill_rounded(im, box.x, y1 - indicator, x1, y1, 0, open ? m.primary : usable ? m.on_surface_variant : m.on_surface, content_alpha);
    text(im, font, value, label_px, true, box.x + kFieldPadX, cy, m.on_surface, content_alpha);
    md_icon(im, open ? MdIcon::arrow_drop_up : MdIcon::arrow_drop_down, x1 - kCaretInset, cy, kCaretPx,
            open ? m.primary : usable ? m.on_surface_variant : m.on_surface, content_alpha);
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
// The atlas: seven bar cells down the left (idle, then one hot segment each hovered and dragged), and to their right the
// caps, a column per state and a row per corner. Atlas px, kHandleAtlasGutter between cells and round the edge.
constexpr std::uint32_t kAtlasGap = kHandleAtlasGutter;
constexpr std::uint32_t kAtlasBarW = std::uint32_t(kLevelBarW) * kHandleAtlasScale, kAtlasBarH = std::uint32_t(kLevelBarH) * kHandleAtlasScale;
constexpr std::uint32_t kAtlasCap = std::uint32_t(kCapOuter + kCapInner) * kHandleAtlasScale;
constexpr std::uint32_t kBarCells = 1 + 2 * kLevelSegments, kCapStates = 3, kCapCorners = 4;
constexpr std::uint32_t kAtlasW = kAtlasGap + kAtlasBarW + kAtlasGap + kCapStates * (kAtlasCap + kAtlasGap);
constexpr std::uint32_t kAtlasH = kAtlasGap + std::max(kBarCells * (kAtlasBarH + kAtlasGap), kCapCorners * (kAtlasCap + kAtlasGap));
struct AtlasCell { std::uint32_t x = 0, y = 0, w = 0, h = 0; };
AtlasCell bar_cell(std::uint32_t row) { return {kAtlasGap, kAtlasGap + row * (kAtlasBarH + kAtlasGap), kAtlasBarW, kAtlasBarH}; }
std::uint32_t bar_row(LevelSegment segment, bool active) {
    return segment == LevelSegment::none ? 0 : 1 + std::uint32_t(segment) + (active ? std::uint32_t(kLevelSegments) : 0);
}
AtlasCell cap_cell(std::uint32_t corner, std::uint32_t state) {
    return {kAtlasGap + kAtlasBarW + kAtlasGap + state * (kAtlasCap + kAtlasGap), kAtlasGap + corner * (kAtlasCap + kAtlasGap), kAtlasCap, kAtlasCap};
}
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

HudImage render_handle_atlas(std::array<float, 3> accent) {
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
    return im;
}

AtlasBox level_bar_box(LevelSegment segment, bool active) { return box_of(bar_cell(bar_row(segment, active))); }
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

HudImage render_glass_panel(const PlacementMenuState& state, const HudFont* font, std::array<float, 3> accent) {
    const auto& m = glass_theme_scheme(accent);
    HudImage im;
    im.width = std::uint32_t(kPanelW); im.height = std::uint32_t(kPanelH);
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);
    const float W = kPanelW;
    const std::uint32_t desktop = std::min<std::uint32_t>(state.desktop, kPlacementMenuDesktopCount - 1);
    // Header: the desktop on the left (title: on-surface, bold), what this is on the right (on-surface-variant); the drag
    // handle (drawn by the GPU) sits between them, an M3 divider (outline variant) under them.
    const float header_cy = (kGrabberTop + kGrabberH + kPanelHeaderH) / 2;
    text(im, font, "XR-" + std::to_string(unsigned(state.numbers[desktop])), kPanelTextPx, true, kPanelPad, header_cy, m.on_surface);
    const std::string title = "Placement";
    text(im, font, title, kPanelTextPx, false, W - kPanelPad - float(text_width(font, title, kPanelTextPx, false)), header_cy,
         m.on_surface_variant);
    for (int x = int(kPanelPad); x < int(W - kPanelPad); ++x) md_over(im, x, int(kPanelHeaderH) - 1, m.outline_variant, 1.f);
    // Rows: an M3 list of sliders. The selected row is the active indicator (secondary container, fully rounded) with its
    // text in on-secondary-container; the others' labels on-surface, their values on-surface-variant.
    const auto rows = placement_menu_rows(state, font == nullptr);
    const auto bounds = placement_menu_bounds();
    const auto flat = placement_menu_flat();
    const auto& values = state.placements[desktop];
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) {
        const float top = kPanelRowsTop + float(i) * kPanelRowH, cy = top + kPanelRowH / 2;
        const bool selected = rows[i].selected;
        if (selected)
            md_fill_rounded(im, kBandInset, top + 2, W - kBandInset, top + kPanelRowH - 2, (kPanelRowH - 4) / 2, m.secondary_container, 1.f);
        text(im, font, kLabels[i], kPanelTextPx, selected, kPanelPad, cy, selected ? m.on_secondary_container : m.on_surface);
        // The slider: inactive track (primary at 24%), active track from the flat value to the value, handle (primary).
        const auto [lo, hi] = bounds[i];
        auto at = [&](std::int32_t v) { return kPanelKnobLo + (kPanelKnobHi - kPanelKnobLo) * float(std::clamp(v, lo, hi) - lo) / float(hi - lo); };
        md_fill_rounded(im, kPanelTrackX0, cy - kTrackH / 2, kPanelTrackX1, cy + kTrackH / 2, kTrackH / 2, m.primary, kTrackAlpha);
        const float a = at(flat[i]), b = at(values[i]);
        if (std::fabs(b - a) >= 1.f)
            md_fill_rounded(im, std::min(a, b), cy - kTrackH / 2, std::max(a, b), cy + kTrackH / 2, kTrackH / 2, m.primary, 1.f);
        if (selected) md_disc(im, b, cy, kHaloR, m.primary, kMdStateFocus);  // the focused handle's state layer
        md_disc(im, b, cy, kPanelKnobR, m.primary, 1.f);
        const auto& value = rows[i].value;
        text(im, font, value, kPanelTextPx, selected, W - kPanelPad - float(text_width(font, value, kPanelTextPx, selected)), cy,
             selected ? m.on_secondary_container : m.on_surface_variant);
    }
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
    // Desktop row: fully rounded M3 buttons (glass_panel_button_style), each an icon and its label (M3 label large) when
    // every button has room for both.
    bool icons = true;
    for (std::size_t a = 0; a < kPanelActions; ++a)
        icons = icons && kButtonIconPx + kButtonIconGap + float(text_width(font, glass_panel_action_label(a, state.armed), label_px, true)) +
                             2 * kButtonPadMin <= kPanelActionW;
    for (std::size_t a = 0; a < kPanelActions; ++a) {
        const FRect r = glass_panel_action(a);
        const PanelButtonStyle st = glass_panel_button_style(a, state, accent);
        md_fill_rounded(im, r.x, r.y, r.x + r.width, r.y + r.height, r.height / 2, st.container, st.container_alpha);
        const std::string label = glass_panel_action_label(a, state.armed);
        const float w = float(text_width(font, label, label_px, true)), content = icons ? kButtonIconPx + kButtonIconGap + w : w;
        const float left = r.x + std::max(0.f, (r.width - content) / 2), cy = r.y + r.height / 2;
        if (icons) md_icon(im, kActionIcons[a], left + kButtonIconPx / 2, cy, kButtonIconPx, st.content, st.content_alpha);
        text(im, font, label, label_px, true, icons ? left + kButtonIconPx + kButtonIconGap : left, cy, st.content, st.content_alpha);
    }
    // Footer: the keys that work while a panel is open (Esc and the pill itself close it), in the outline colour.
    const std::string hint = glass_panel_hint(font == nullptr);
    const auto hint_px = font ? kPanelHintPx : 0u;
    const float hint_w = float(font ? font->measure(hint, hint_px, false) : hud_text_width(hint, 2));
    text(im, font, hint, hint_px, false, std::max(kPanelPad, (W - hint_w) / 2), kPanelFooterTop + float(kPanelHintPx) / 2, m.outline);
    // The open Scale list, last: an M3 menu over the slider rows (surface container, extra small corners, an
    // outline-variant edge; opaque, so the sliders under it do not show), one item per step in label large: the current
    // one on the secondary container, steps that cannot be picked M3 disabled (on-surface at 38%).
    if (state.picker) {
        const FRect first = glass_scale_item(0), last = glass_scale_item(kScaleSteps - 1);
        const float x0 = first.x, y0 = first.y - 1, x1 = first.x + first.width, y1 = last.y + last.height + 1;
        md_fill_rounded(im, x0, y0, x1, y1, kMdShapeExtraSmall, m.surface_container, 1.f);
        md_stroke_rounded(im, x0, y0, x1, y1, kMdShapeExtraSmall, 1.f, m.outline_variant, 1.f);
        for (std::size_t i = 0; i < kScaleSteps; ++i) {
            const FRect r = glass_scale_item(i);
            const bool current = kScalePercents[i] == state.scale, enabled = glass_scale_enabled(i, state);
            if (current)
                md_fill_rounded(im, r.x + 2, r.y + 1, r.x + r.width - 2, r.y + r.height - 1, kMdShapeExtraSmall, m.secondary_container, 1.f);
            text(im, font, glass_scale_label(kScalePercents[i]), label_px, true, r.x + kFieldPadX, r.y + r.height / 2,
                 current ? m.on_secondary_container : m.on_surface, enabled || current ? 1.f : kMdDisabledContent);
        }
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
