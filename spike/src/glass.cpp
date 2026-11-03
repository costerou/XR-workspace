#include "xr/glass.hpp"
#include <algorithm>
#include <cmath>
#include <numbers>
#include <string>

namespace xr {
namespace {
float lerp(float a, float b, float t) { return a + (b - a) * t; }
float clamp01(float v) { return std::clamp(v, 0.f, 1.f); }
float smooth(float e0, float e1, float x) { const float t = clamp01((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t); }  // GLSL smoothstep

// Dimmed card palette (hud.cpp kCardText / kCardDim; the highlights are the theme's, glass_theme_highlight): the panel
// text never passes kCardMaxLevel.
constexpr std::array<float, 3> kText{0.70f, 0.70f, 0.70f}, kMuted{0.56f, 0.56f, 0.56f}, kDim{0.46f, 0.46f, 0.46f};
constexpr std::array<float, 3> kRule{0.80f, 0.80f, 0.80f};
constexpr float kBandAlpha = 0.20f, kRuleAlpha = 0.10f, kTrackAlpha = 0.55f, kFillAlpha = 0.9f, kTrackH = 4.f, kBandRadius = 9.f;
// Short labels: the label column is kPanelTrackX0 - kPanelPad wide.
constexpr const char* kLabels[kPlacementMenuFields] = {"Left/right", "Down/up", "Distance", "Curvature"};

// Premultiplied "over" of colour c with coverage a.
void over(HudImage& im, int x, int y, std::array<float, 3> c, float a) {
    if (x < 0 || y < 0 || std::uint32_t(x) >= im.width || std::uint32_t(y) >= im.height || a <= 0) return;
    a = std::min(a, 1.f);
    auto* p = &im.rgba[(std::size_t(y) * im.width + std::size_t(x)) * 4];
    for (int i = 0; i < 3; ++i) p[i] = std::uint8_t(std::lround(std::clamp(c[std::size_t(i)] * a * 255 + p[i] * (1 - a), 0.f, 255.f)));
    p[3] = std::uint8_t(std::lround(std::clamp(a * 255 + p[3] * (1 - a), 0.f, 255.f)));
}
// Signed distance from (px, py) to a rounded rectangle centred at (cx, cy) with half sizes hw, hh (negative inside).
float rounded(float px, float py, float cx, float cy, float hw, float hh, float r) {
    r = std::min({r, hw, hh});
    const float dx = std::fabs(px - cx) - (hw - r), dy = std::fabs(py - cy) - (hh - r);
    const float ox = std::max(dx, 0.f), oy = std::max(dy, 0.f);
    return std::sqrt(ox * ox + oy * oy) + std::min(std::max(dx, dy), 0.f) - r;
}
// Anti-aliased rounded rect [x0, x1) x [y0, y1).
void fill_rounded(HudImage& im, float x0, float y0, float x1, float y1, float r, std::array<float, 3> c, float alpha) {
    const float cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hw = (x1 - x0) / 2, hh = (y1 - y0) / 2;
    if (hw <= 0 || hh <= 0) return;
    for (int y = int(std::floor(y0)) - 1; y <= int(std::ceil(y1)); ++y)
        for (int x = int(std::floor(x0)) - 1; x <= int(std::ceil(x1)); ++x) {
            const float cov = clamp01(0.5f - rounded(float(x) + 0.5f, float(y) + 0.5f, cx, cy, hw, hh, r));
            if (cov > 0) over(im, x, y, c, cov * alpha);
        }
}
void fill_disc(HudImage& im, float cx, float cy, float r, std::array<float, 3> c) { fill_rounded(im, cx - r, cy - r, cx + r, cy + r, r, c, 1.f); }

// Text at an em size: the system font, or the built-in pixel font (ASCII, zoom 2) without one.
std::uint32_t text_width(const HudFont* font, const std::string& s, std::uint32_t px, bool bold) {
    return font ? font->measure(s, px, bold) : hud_text_width(s, 2);
}
// Draw `s` with its left edge at `left`, vertically centred on `cy` (capitals centred).
void text(HudImage& im, const HudFont* font, const std::string& s, std::uint32_t px, bool bold, float left, float cy, std::array<float, 3> c) {
    if (font) {
        const int baseline = int(std::lround(cy + 0.36f * float(px)));  // JetBrains Mono capitals: ~0.73 em
        font->draw(s, px, bold, int(std::lround(left)), baseline, [&](int x, int y, float cov) { over(im, x, y, c, cov); });
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
                        for (std::uint32_t sx = 0; sx < z; ++sx) over(im, x + int(col * z + sx), top + int(row * z + sy), c, 1.f);
        x += int((kGlyphW + 1) * z);
    }
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

GlassLook glass_look(std::array<float, 3> accent, float level, float t) {
    t = clamp01(t);
    GlassLook k;
    const auto tint = glass_theme_tint(accent, t);  // the theme's hue at one brightness (glass_theme.hpp)
    const float bright = lerp(1.3f, 0.8f, t);
    for (std::size_t i = 0; i < 3; ++i) k.tint[i] = tint[i] * bright;
    k.fill = lerp(0.9f, 0.82f, t);
    k.gloss = lerp(0.22f, 0.10f, t);
    k.gloss_px = lerp(kGlassPillH * 0.6f, 30.f, t);
    k.level = std::clamp(level, kGlassIdle, kGlassActive);
    k.border_top = 0.40f; k.border_bottom = 0.12f;
    k.shadow = 0.55f; k.shadow_sigma = lerp(3.5f, 10.f, t); k.shadow_dy = lerp(1.5f, 5.f, t);
    return k;
}

GlassLook grabber_look(float level) {
    GlassLook k;
    k.tint = {0.62f, 0.62f, 0.64f};
    k.fill = 0.55f;
    k.level = std::clamp(level, kGlassIdle, kGlassActive);
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
    const float t = clamp01((py - cy + hh) / std::max(2 * hh, 1.f));
    const float top = py - (cy - hh);
    const float lift = 1.f + 0.25f * (k.level - 1.f);
    std::array<float, 3> c{};
    float a = k.fill;
    for (std::size_t i = 0; i < 3; ++i) c[i] = k.tint[i] * (1.25f - 0.5f * t) * lift * a;
    const float g = std::min(k.gloss * k.level, 1.f) * (1.f - smooth(0.f, std::max(k.gloss_px, 1e-3f), top));
    for (auto& v : c) v = v * (1 - g) + kGlossLevel * g;
    a = a + g * (1 - a);
    const float b = clamp01(1.f - std::fabs(d + 0.5f)) * std::min(lerp(k.border_top, k.border_bottom, t) * k.level, 1.f);
    for (auto& v : c) v = v * (1 - b) + kHairLevel * b;
    a = a + b * (1 - a);
    for (auto& v : c) v *= inside;
    a *= inside;
    const float ds = std::max(box(px - cx, py - cy - k.shadow_dy), 0.f);
    const float sigma = std::max(k.shadow_sigma, 1e-3f);
    const float s = k.shadow * std::exp(-ds * ds / (2 * sigma * sigma)) * (1 - inside);
    a = a + s * (1 - a);
    return {std::min(c[0], kGlassCap) * opacity, std::min(c[1], kGlassCap) * opacity, std::min(c[2], kGlassCap) * opacity, a * opacity};
}

// Mirrors glass_shade() line for line.
const char* glass_fragment_shader() {
    return "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n"
           "varying vec2 p; uniform vec4 rect; uniform float radius; uniform vec3 tint; uniform vec4 look; uniform vec4 edge;\n"
           "uniform float shadow_dy; uniform float opacity;\n"
           "const float kCap = 0.74901961; const float kGloss = 0.86; const float kHair = 0.88;\n"
           "float box(vec2 q) { vec2 d = abs(q) - (rect.zw - vec2(radius));\n"
           "  return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - radius; }\n"
           "void main() {\n"
           "  float d = box(p - rect.xy);\n"
           "  float inside = clamp(0.5 - d, 0.0, 1.0);\n"
           "  float t = clamp((p.y - rect.y + rect.w) / max(2.0 * rect.w, 1.0), 0.0, 1.0);\n"
           "  float top = p.y - (rect.y - rect.w);\n"
           "  float lift = 1.0 + 0.25 * (look.w - 1.0);\n"
           "  float a = look.x;\n"
           "  vec3 c = tint * (1.25 - 0.5 * t) * lift * a;\n"
           "  float g = min(look.y * look.w, 1.0) * (1.0 - smoothstep(0.0, max(look.z, 0.001), top));\n"
           "  c = c * (1.0 - g) + vec3(kGloss) * g; a = a + g * (1.0 - a);\n"
           "  float b = clamp(1.0 - abs(d + 0.5), 0.0, 1.0) * min(mix(edge.x, edge.y, t) * look.w, 1.0);\n"
           "  c = c * (1.0 - b) + vec3(kHair) * b; a = a + b * (1.0 - a);\n"
           "  c *= inside; a *= inside;\n"
           "  float ds = max(box(p - rect.xy - vec2(0.0, shadow_dy)), 0.0);\n"
           "  float sigma = max(edge.w, 0.001);\n"
           "  float s = edge.z * exp(-ds * ds / (2.0 * sigma * sigma)) * (1.0 - inside);\n"
           "  a = a + s * (1.0 - a);\n"
           "  gl_FragColor = vec4(min(c, vec3(kCap)) * opacity, a * opacity);\n"
           "}\n";
}

std::string glass_panel_hint(bool ascii) {
    return ascii ? "UP/DN FIELD  L/R ADJUST  TAB  R FLAT" : "\xe2\x86\x91\xe2\x86\x93 field  \xe2\x86\x90\xe2\x86\x92 adjust  Tab next  R flat";
}

HudImage render_glass_panel(const PlacementMenuState& state, const HudFont* font, std::array<float, 3> accent) {
    const auto highlight = glass_theme_highlight(accent);
    HudImage im;
    im.width = std::uint32_t(kPanelW); im.height = std::uint32_t(kPanelH);
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);
    const float W = kPanelW;
    const std::uint32_t desktop = std::min<std::uint32_t>(state.desktop, kPlacementMenuDesktopCount - 1);
    // Header: the desktop on the left, what this is on the right; the grabber (drawn by the GPU) sits between them.
    const float header_cy = (kGrabberTop + kGrabberH + kPanelHeaderH) / 2;
    text(im, font, "XR-" + std::to_string(unsigned(state.numbers[desktop])), kPanelTextPx, true, kPanelPad, header_cy, kText);
    const std::string title = "Placement";
    text(im, font, title, kPanelTextPx, false, W - kPanelPad - float(text_width(font, title, kPanelTextPx, false)), header_cy, kMuted);
    for (int x = int(kPanelPad); x < int(W - kPanelPad); ++x) over(im, x, int(kPanelHeaderH) - 1, kRule, kRuleAlpha);
    // Rows.
    const auto rows = placement_menu_rows(state, font == nullptr);
    const auto bounds = placement_menu_bounds();
    const auto flat = placement_menu_flat();
    const auto& values = state.placements[desktop];
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) {
        const float top = kPanelRowsTop + float(i) * kPanelRowH, cy = top + kPanelRowH / 2;
        const bool selected = rows[i].selected;
        if (selected) fill_rounded(im, 10, top + 2, W - 10, top + kPanelRowH - 2, kBandRadius, highlight, kBandAlpha);
        text(im, font, kLabels[i], kPanelTextPx, selected, kPanelPad, cy, selected ? kText : kMuted);
        const auto [lo, hi] = bounds[i];
        auto at = [&](std::int32_t v) { return kPanelKnobLo + (kPanelKnobHi - kPanelKnobLo) * float(std::clamp(v, lo, hi) - lo) / float(hi - lo); };
        fill_rounded(im, kPanelTrackX0, cy - kTrackH / 2, kPanelTrackX1, cy + kTrackH / 2, kTrackH / 2, kDim, kTrackAlpha);
        const float a = at(flat[i]), b = at(values[i]);
        if (std::fabs(b - a) >= 1.f) fill_rounded(im, std::min(a, b), cy - kTrackH / 2, std::max(a, b), cy + kTrackH / 2, kTrackH / 2, highlight, kFillAlpha);
        fill_disc(im, b, cy, kPanelKnobR, kText);
        const auto& value = rows[i].value;
        text(im, font, value, kPanelTextPx, selected, W - kPanelPad - float(text_width(font, value, kPanelTextPx, selected)), cy, selected ? kText : kMuted);
    }
    // Desktop row: a faint button each; lit (the theme's band) while on (Ultrawide) or armed (Remove); dimmed when it can
    // do nothing now.
    for (std::size_t a = 0; a < kPanelActions; ++a) {
        const FRect r = glass_panel_action(a);
        const bool lit = (a == 0 && state.wide) || (a == 2 && state.armed);
        const bool enabled = glass_panel_action_enabled(a, state);
        fill_rounded(im, r.x, r.y, r.x + r.width, r.y + r.height, kBandRadius, lit && enabled ? highlight : kDim,
                     lit && enabled ? kBandAlpha * 2 : (enabled ? kBandAlpha : kBandAlpha / 2));
        const std::string label = glass_panel_action_label(a, state.armed);
        const auto px = font ? kPanelActionPx : 0u;
        const float w = float(text_width(font, label, px, lit));
        text(im, font, label, px, lit, r.x + std::max(0.f, (r.width - w) / 2), r.y + r.height / 2, enabled ? (lit ? kText : kMuted) : kDim);
    }
    // Footer: the keys that work while a panel is open (Esc and the pill itself close it).
    const std::string hint = glass_panel_hint(font == nullptr);
    const auto hint_px = font ? kPanelHintPx : 0u;
    const float hint_w = float(font ? font->measure(hint, hint_px, false) : hud_text_width(hint, 2));
    text(im, font, hint, hint_px, false, std::max(kPanelPad, (W - hint_w) / 2), kPanelFooterTop + float(kPanelHintPx) / 2, kDim);
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
