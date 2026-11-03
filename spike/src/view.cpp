#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>

namespace xr {
namespace {
constexpr std::uint32_t kMaxIndex = 2;  // XR-1..XR-3
float clampf(float v, float lo, float hi) { return std::max(lo, std::min(hi, v)); }
float lerp(float a, float b, float t) { return a + (b - a) * t; }
bool valid(std::span<const Size> src, float view_w, float view_h) {
    return !src.empty() && view_w > 0 && view_h > 0 && std::all_of(src.begin(), src.end(), [](Size z) { return z.w > 0 && z.h > 0; });
}
// One row at a shared height h, `s` apart, first tile's left edge at x0, centred vertically.
std::vector<FRect> row(std::span<const Size> src, float view_h, float h, float s, float x0) {
    std::vector<FRect> out;
    for (auto z : src) { const float w = h * z.w / z.h; out.push_back({x0, (view_h - h) / 2, w, h}); x0 += w + s; }
    return out;
}
}
float smoothstep(float t) { t = clampf(t, 0, 1); return t * t * (3 - 2 * t); }
float smootherstep(float t) { t = clampf(t, 0, 1); return t * t * t * (t * (6 * t - 15) + 10); }

std::vector<FRect> overview_layout(std::span<const Size> src, float view_w, float view_h, float spacing) {
    if (!valid(src, view_w, view_h)) return {};
    const float s = clampf(spacing, 0, float(kMaxSpacing)), n = float(src.size());
    float aspects = 0; for (auto z : src) aspects += z.w / z.h;
    const float h = std::min(view_h, std::max(0.f, view_w - (n + 1) * s) / aspects);
    const float w = h * aspects;
    return row(src, view_h, h, s, (view_w - (w + (n - 1) * s)) / 2);  // centred; margin s when width-limited
}

std::vector<FRect> zoomed_layout(std::span<const Size> src, float view_w, float view_h, float spacing, float position) {
    if (!valid(src, view_w, view_h)) return {};
    const float s = clampf(spacing, 0, float(kMaxSpacing));
    auto fit_h = [&](Size z) { return std::min(view_h, view_w * z.h / z.w); };
    // Neighbouring pair around position (the end pair when outside the row; then t extrapolates).
    const std::size_t last = src.size() - 1;
    const std::size_t lo = std::size_t(clampf(std::floor(position), 0, float(last ? last - 1 : 0)));
    const std::size_t hi = std::min(lo + 1, last);
    const float t = position - float(lo);
    auto out = row(src, view_h, lerp(fit_h(src[lo]), fit_h(src[hi]), clampf(t, 0, 1)), s, 0);
    auto centre = [&](std::size_t i) { return out[i].x + out[i].width / 2; };
    const float target = lo == hi ? centre(lo) + t * (out[lo].width + s) : lerp(centre(lo), centre(hi), t);
    for (auto& r : out) r.x += view_w / 2 - target;
    return out;
}

std::vector<FRect> blended_layout(std::span<const Size> src, float view_w, float view_h, float spacing, float zoom, float position) {
    auto a = overview_layout(src, view_w, view_h, spacing);
    auto b = zoomed_layout(src, view_w, view_h, spacing, position);
    const float t = clampf(zoom, 0, 1);
    for (std::size_t i = 0; i < a.size() && i < b.size(); ++i)
        a[i] = {lerp(a[i].x, b[i].x, t), lerp(a[i].y, b[i].y, t), lerp(a[i].width, b[i].width, t), lerp(a[i].height, b[i].height, t)};
    return a;
}

namespace {
constexpr float kMinCurvature = 1e-4f;  // below this the arc is numerically flat
}
Vec3 place_point(const Canvas& c, const FRect& tile, const Placement& pl, float px, float py) {
    if (c.view_w <= 0 || c.view_h <= 0) return {};
    const float sx = 2 * c.half_w / c.view_w, sy = 2 * c.half_h / c.view_h;  // world units per canvas pixel
    const float cx = tile.x + tile.width / 2, cy = tile.y + tile.height / 2;
    const float tw = tile.width * sx;
    const float d = pl.distance > 0 ? pl.distance : 1.f;
    // The slot's centre on the canvas plane (z = -1): its direction (azimuth a0, elevation e0) and distance from the eye.
    const float wx = (2 * cx / c.view_w - 1) * c.half_w, wy = (1 - 2 * cy / c.view_h) * c.half_h;
    const float a0 = std::atan2(wx, 1.f), e0 = std::atan2(wy, std::sqrt(1 + wx * wx));
    const float r = std::sqrt(1 + wx * wx + wy * wy) * d;
    const float u = (px - cx) * sx, v = (cy - py) * sy;
    Vec3 local{u, v, 0};
    if (pl.curvature > kMinCurvature && tw > 0) {
        const float rc = tw / pl.curvature, phi = u / rc;
        local = {rc * std::sin(phi), v, rc * (1 - std::cos(phi))};  // toward the viewer at the edges
    }
    if (pl.roll != 0) {  // levelling roll about the line of sight (the z axis here)
        const float cr = std::cos(pl.roll), sr = std::sin(pl.roll);
        local = {cr * local.x - sr * local.y, sr * local.x + cr * local.y, local.z};
    }
    // Straight ahead, square to the line of sight; then up by e0 + y and round by a0 + x: it always faces the eye.
    Vec3 q{local.x, local.y, local.z - r};
    const float ce = std::cos(e0 + pl.y), se = std::sin(e0 + pl.y);
    q = {q.x, ce * q.y - se * q.z, se * q.y + ce * q.z};
    const float ca = std::cos(a0 + pl.x), sa = std::sin(a0 + pl.x);
    return {ca * q.x - sa * q.z, q.y, sa * q.x + ca * q.z};
}

std::vector<MeshVertex> tile_mesh(const Canvas& c, const FRect& tile, const Placement& pl, const FRect& rect) {
    if (tile.width <= 0 || tile.height <= 0 || rect.width <= 0 || rect.height <= 0) return {};
    // ~3 degrees of arc per column across the tile; the strip covers `rect` with the same density.
    const float span = rect.width / tile.width;
    const int columns = pl.curvature > kMinCurvature
        ? std::clamp(int(std::ceil(span * pl.curvature * 180.f / float(M_PI) / 3.f)), 4, 96) : 1;
    std::vector<MeshVertex> out;
    out.reserve(std::size_t(columns + 1) * 2);
    for (int i = 0; i <= columns; ++i) {
        const float px = rect.x + rect.width * float(i) / float(columns);
        for (const float py : {rect.y, rect.y + rect.height})
            out.push_back({place_point(c, tile, pl, px, py), (px - tile.x) / tile.width, (py - tile.y) / tile.height, px, py});
    }
    return out;
}

std::array<float, 2> glass_to_desktop(const FRect& tile, const Placement& pl, float gx, float gy) {
    const float k = pl.distance > 0 ? pl.distance : 1.f;  // as place_point reads it
    const float ax = tile.x + tile.width / 2, ay = tile.y + tile.height;
    return {ax + (gx - ax) * k, ay + (gy - ay) * k};
}

std::vector<MeshVertex> glass_mesh(const Canvas& c, const FRect& tile, const Placement& pl, const FRect& rect) {
    const float k = pl.distance > 0 ? pl.distance : 1.f;
    const auto [x, y] = glass_to_desktop(tile, pl, rect.x, rect.y);
    auto out = tile_mesh(c, tile, pl, {x, y, rect.width * k, rect.height * k});
    const float ax = tile.x + tile.width / 2, ay = tile.y + tile.height;
    for (auto& v : out) { v.px = ax + (v.px - ax) / k; v.py = ay + (v.py - ay) / k; }  // back to glass px
    return out;
}

float zoom_weight(float zoom, float position, std::size_t index) {
    return clampf(zoom, 0, 1) * std::max(0.f, 1 - std::abs(position - float(index)));
}

Placement faded(const Placement& p, float weight) {
    const float k = 1 - clampf(weight, 0, 1);
    return {p.x * k, p.y * k, lerp(1.f, p.distance, k), p.curvature, p.roll};
}

namespace {
// Direction (azimuth, elevation) of a slot's centre from the eye, as place_point turns it.
struct Dir { float az = 0, el = 0; };
Dir slot_direction(const Canvas& c, const FRect& t) {
    if (c.view_w <= 0 || c.view_h <= 0) return {};
    const float wx = (2 * (t.x + t.width / 2) / c.view_w - 1) * c.half_w, wy = (1 - 2 * (t.y + t.height / 2) / c.view_h) * c.half_h;
    return {std::atan2(wx, 1.f), std::atan2(wy, std::sqrt(1 + wx * wx))};
}
}

std::vector<Placement> zoomed_placements(const Canvas& c, std::span<const FRect> overview, std::span<const FRect> tiles,
                                         std::span<const Placement> placements, float zoom, float position) {
    const std::size_t n = tiles.size();
    std::vector<Placement> out(n);
    for (std::size_t i = 0; i < n && i < placements.size(); ++i) out[i] = placements[i];
    const float z = clampf(zoom, 0, 1);
    if (n == 0 || z == 0 || overview.size() != n || c.view_w <= 0 || c.view_h <= 0) return out;
    // Distance from the eye of a slot's centre on the canvas plane (place_point's r at distance 1).
    auto radius = [&](const FRect& t) {
        const float wx = (2 * (t.x + t.width / 2) / c.view_w - 1) * c.half_w, wy = (1 - 2 * (t.y + t.height / 2) / c.view_h) * c.half_h;
        return std::sqrt(1 + wx * wx + wy * wy);
    };
    for (std::size_t i = 0; i < n; ++i) {
        const Placement own = i < placements.size() ? placements[i] : Placement{};
        const FRect &was = overview[i], &now = tiles[i];
        if (!(was.height > 0) || !(now.height > 0)) continue;
        // Held still round you: the orbit absorbs the slot's move from the overview to `tiles`.
        const Dir ov = slot_direction(c, was), cur = slot_direction(c, now);
        // Held at its overview size: every length (tile, radius, curvature's arc) scales by k about the eye.
        const float d = own.distance > 0 ? own.distance : 1.f;
        const float rigid = d * (now.height / was.height) * radius(was) / radius(now);
        out[i] = {std::remainder(own.x + ov.az - cur.az, 2 * float(M_PI)), own.y + ov.el - cur.el,
                  lerp(rigid, 1.f, zoom_weight(z, position, i)), own.curvature, own.roll};
    }
    return out;
}

std::array<float, 2> focus_direction(const Canvas& c, std::span<const FRect> overview, std::span<const Placement> placements,
                                     std::size_t index) {
    if (index >= overview.size() || c.view_w <= 0 || c.view_h <= 0) return {0, 0};
    const Dir s = slot_direction(c, overview[index]);
    const Placement p = index < placements.size() ? placements[index] : Placement{};
    return {std::remainder(s.az + p.x, 2 * float(M_PI)), s.el + p.y};
}

float focus_weight(const ViewAnimator& view) { return clampf(view.zoom() + view.look(), 0, 1); }

std::array<float, 9> look_turn(const Canvas& c, std::span<const FRect> overview, std::span<const Placement> placements,
                               std::size_t from, std::size_t to, float look) {
    std::array<float, 9> m{1, 0, 0, 0, 1, 0, 0, 0, 1};
    const float u = clampf(look, 0, 1);
    if (!(u > 0) || from >= overview.size() || to >= overview.size() || c.view_w <= 0 || c.view_h <= 0) return m;
    auto placed = [&](std::size_t i) {
        const Dir s = slot_direction(c, overview[i]);
        const Placement p = i < placements.size() ? placements[i] : Placement{};
        return Dir{s.az + p.x, s.el + p.y};
    };
    const Dir a = placed(from), b = placed(to);
    const Dir l{a.az + u * std::remainder(b.az - a.az, 2 * float(M_PI)), lerp(a.el, b.el, u)};  // the short way round
    // R(d) = Az(d.az) . El(d.el) carries straight ahead to direction d, upright (place_point's turn). m = R(a) . R(l)^T.
    auto rot = [](Dir d) {
        const float ca = std::cos(d.az), sa = std::sin(d.az), ce = std::cos(d.el), se = std::sin(d.el);
        // Az: x' = ca x - sa z, z' = sa x + ca z. El: y' = ce y - se z, z' = se y + ce z.
        return std::array<float, 9>{ca, -sa * se, -sa * ce, 0, ce, -se, sa, ca * se, ca * ce};
    };
    const auto ra = rot(a), rl = rot(l);
    for (int r = 0; r < 3; ++r)
        for (int col = 0; col < 3; ++col) {
            float v = 0;
            for (int k = 0; k < 3; ++k) v += ra[r * 3 + k] * rl[col * 3 + k];  // R(l)^T[k][col] = R(l)[col][k]
            m[r * 3 + col] = v;
        }
    return m;
}

std::vector<std::size_t> zoom_draw_order(std::vector<std::size_t> order, float zoom, std::size_t target) {
    if (!(zoom >= 0.5f)) return order;
    const auto it = std::find(order.begin(), order.end(), target);
    if (it != order.end()) std::rotate(it, it + 1, order.end());
    return order;
}

std::vector<std::size_t> draw_order(const Canvas& c, std::span<const FRect> tiles, std::span<const Placement> placements) {
    std::vector<std::size_t> order(tiles.size());
    std::vector<float> depth(tiles.size());
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        order[i] = i;
        const auto& t = tiles[i];
        const Vec3 p = place_point(c, t, i < placements.size() ? placements[i] : Placement{}, t.x + t.width / 2, t.y + t.height / 2);
        depth[i] = p.x * p.x + p.y * p.y + p.z * p.z;
    }
    std::stable_sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) { return depth[a] > depth[b]; });
    return order;
}

CornerHandle corner_handle(const FRect& t, std::uint32_t corner) {
    if (t.width <= 0 || t.height <= 0 || corner > 3) return {};
    const float arm = clampf(kHandleArmShare * std::min(t.width, t.height), kHandleMinArmPx, kHandleMaxArmPx);
    const bool right = corner == 1 || corner == 3, bottom = corner >= 2;
    const float x0 = right ? t.x + t.width - kHandleInsetPx - arm : t.x + kHandleInsetPx;          // horizontal arm's left
    const float y0 = bottom ? t.y + t.height - kHandleInsetPx - arm : t.y + kHandleInsetPx;        // vertical arm's top
    const float hy = bottom ? t.y + t.height - kHandleInsetPx - kHandleThicknessPx : t.y + kHandleInsetPx;
    const float vx = right ? t.x + t.width - kHandleInsetPx - kHandleThicknessPx : t.x + kHandleInsetPx;
    return {{x0, hy, arm, kHandleThicknessPx}, {vx, y0, kHandleThicknessPx, arm}};
}

std::vector<Vec3> canvas_disc(const Canvas& c, float x, float y, float radius, int segments) {
    if (c.view_w <= 0 || c.view_h <= 0 || !(radius > 0) || segments < 3) return {};
    auto at = [&](float px, float py) { return Vec3{(2 * px / c.view_w - 1) * c.half_w, (1 - 2 * py / c.view_h) * c.half_h, -1.f}; };
    std::vector<Vec3> out{at(x, y)};
    for (int i = 0; i <= segments; ++i) {
        const float a = 2.f * float(M_PI) * float(i % segments) / float(segments);
        out.push_back(at(x + radius * std::cos(a), y + radius * std::sin(a)));
    }
    return out;
}

std::vector<Vec3> direction_disc(const Canvas& c, float x, float y, float az, float el, float radius, int segments) {
    const float cx = std::clamp(x, 0.f, std::max(c.view_w, 0.f)), cy = std::clamp(y, 0.f, std::max(c.view_h, 0.f));
    auto fan = canvas_disc(c, cx, cy, radius, segments);
    if (fan.empty()) return fan;
    const double wx = (2.0 * cx / c.view_w - 1) * c.half_w, wy = (1 - 2.0 * cy / c.view_h) * c.half_h;
    const double a = std::atan2(wx, 1.0), e = std::atan2(wy, std::hypot(wx, 1.0));
    const double ca = std::cos(-a), sa = std::sin(-a), ce = std::cos(el - e), se = std::sin(el - e), cz = std::cos(az), sz = std::sin(az);
    for (auto& v : fan) {
        double px = ca * v.x - sa * v.z, py = v.y, pz = sa * v.x + ca * v.z;            // Az(-a)
        const double qy = ce * py - se * pz, qz = se * py + ce * pz;                     // El(el - e)
        py = qy; pz = qz;
        v = {float(cz * px - sz * pz), float(py), float(sz * px + cz * pz)};            // Az(az)
    }
    return fan;
}

std::string overview_geometry_json(std::span<const FRect> tiles, std::uint32_t width, std::uint32_t height, float half_w, float half_h) {
    if (tiles.empty() || tiles.size() > 3 || !width || !height || width > 8192 || height > 8192 || !(half_w > 0) || !(half_h > 0) ||
        !std::isfinite(half_w) || !std::isfinite(half_h)) return {};
    char buf[96];
    std::snprintf(buf, sizeof buf, "{\"overview_geometry\":{\"w\":%u,\"h\":%u,\"half\":[%.5f,%.5f],\"tiles\":[", width, height,
                  double(half_w), double(half_h));
    std::string out = buf;
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        const auto& t = tiles[i];
        if (!std::isfinite(t.x) || !std::isfinite(t.y) || !(t.width > 0) || !(t.height > 0) || !std::isfinite(t.width) ||
            !std::isfinite(t.height) || std::fabs(t.x) > 8192 || std::fabs(t.y) > 8192 || t.width > 8192 || t.height > 8192) return {};
        std::snprintf(buf, sizeof buf, "%s[%.2f,%.2f,%.2f,%.2f]", i ? "," : "", double(t.x), double(t.y), double(t.width), double(t.height));
        out += buf;
    }
    return out + "]}}";
}

Placement ease_toward(const Placement& from, const Placement& to, float dt_ms, float tau_ms) {
    if (tau_ms <= 0) return to;
    if (!(dt_ms > 0)) return from;
    const float k = std::exp(-dt_ms / tau_ms);
    auto step = [&](float a, float b) { const float v = b + (a - b) * k; return std::fabs(v - b) < 2e-4f ? b : v; };
    // The orbit's turn round you: the short way (a desktop crossing behind you stays behind you).
    const float turn = std::remainder(from.x - to.x, 2 * float(M_PI));
    return {step(to.x + turn, to.x), step(from.y, to.y), step(from.distance, to.distance), step(from.curvature, to.curvature),
            step(from.roll, to.roll)};
}

bool same_placement(const Placement& a, const Placement& b) {
    return a.x == b.x && a.y == b.y && a.distance == b.distance && a.curvature == b.curvature && a.roll == b.roll;
}

std::optional<float> desktop_tilt(const Canvas& c, const FRect& t, const Placement& pl, const Vec3& up) {
    if (c.view_w <= 0 || c.view_h <= 0 || !(t.width > 0) || !(t.height > 0)) return std::nullopt;
    const float cx = t.x + t.width / 2, cy = t.y + t.height / 2;
    const Vec3 o = place_point(c, t, pl, cx, cy), top = place_point(c, t, pl, cx, cy - 1), right = place_point(c, t, pl, cx + 1, cy);
    auto sub = [](Vec3 a, Vec3 b) { return Vec3{a.x - b.x, a.y - b.y, a.z - b.z}; };
    auto dot = [](Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; };
    const float on = std::sqrt(dot(o, o)), un = std::sqrt(dot(up, up));
    if (!(on > 0) || !(un > 0) || !std::isfinite(on) || !std::isfinite(un)) return std::nullopt;
    const Vec3 n{o.x / on, o.y / on, o.z / on}, g{up.x / un, up.y / un, up.z / un};
    const float along = dot(g, n);
    const Vec3 flat{g.x - along * n.x, g.y - along * n.y, g.z - along * n.z};  // up, on the surface
    if (dot(flat, flat) < std::pow(std::sin(kTiltMinDeg * float(M_PI) / 180.f), 2.f)) return std::nullopt;
    // A desktop turned counter-clockwise by r sees the world's up turned clockwise by r in its own frame: toward its right.
    const Vec3 r = sub(right, o), u = sub(top, o);
    const float rn = std::sqrt(dot(r, r)), uln = std::sqrt(dot(u, u));
    if (!(rn > 0) || !(uln > 0)) return std::nullopt;
    const float a = std::atan2(dot(flat, r) / rn, dot(flat, u) / uln);
    return std::isfinite(a) ? std::optional<float>(a) : std::nullopt;
}

void ViewAnimator::start_switch(std::uint32_t from, std::uint32_t to, float zoom, double now_ms) {
    switch_ = {};
    switch_.active = true; switch_.start_ms = now_ms; switch_.zoom = clampf(zoom, 0, 1); switch_.out_ms = duration_ * switch_.zoom;
    switch_.from = from; switch_.to = to; switch_.zoom_now = switch_.zoom;
    shown_ = from; look_from_ = from; look_to_ = to; look_t_ = 1.f; from_look_ = 0;
    from_zoom_ = to_zoom_ = 1.f; t_ = 1.f;  // the zoom tween waits at 1 for the switch to land
}

void ViewAnimator::set_target(bool zoomed, std::uint32_t selected, std::uint32_t spacing, double now_ms) {
    advance(now_ms);
    selected = std::min(selected, kMaxIndex);
    const float spacing_target = float(std::min(spacing, kMaxSpacing));
    if (spacing_target != to_spacing_) {
        from_spacing_ = this->spacing(); to_spacing_ = spacing_target; spacing_start_ms_ = now_ms; spacing_t_ = 0.f;
    }
    const float z = zoom();
    auto tween_zoom = [&](float to) {
        const float look_now = look();
        switch_.active = switch_.queued = false;
        if (look_now > 0) { from_look_ = look_now; look_start_ms_ = now_ms; look_t_ = 0.f; }  // ease any turn back
        from_zoom_ = z; to_zoom_ = to; start_ms_ = now_ms; t_ = 0.f;
    };
    if (switch_.active) {
        const bool zooming_out = now_ms - switch_.start_ms < switch_.out_ms;
        if (!zoomed) {
            tween_zoom(0.f);  // zoom out from here; shown_ keeps the desktop on screen until the overview
        } else if (selected == switch_.to) {
            switch_.queued = false;
        } else if (zooming_out && selected == switch_.from) {
            tween_zoom(1.f);  // back before the turn began: zoom straight back in
        } else if (zooming_out) {
            switch_.to = look_to_ = selected;  // the turn has not started: just aim it elsewhere
        } else {
            switch_.queued = true; switch_.next = selected;  // runs once this switch lands
        }
    } else if (zoomed && selected != shown_ && (to_zoom_ == 1.f || z > 0.f)) {  // zoomed (or partly): animate the switch
        start_switch(shown_, selected, z, now_ms);
    } else {
        if (float(zoomed) != to_zoom_) tween_zoom(zoomed ? 1.f : 0.f);
        // Zooming out, the desktop being left stays the zoom's until the overview; otherwise the selection is shown at once.
        if (!(to_zoom_ == 0.f && z > 0.f)) shown_ = selected;
    }
    to_selected_ = selected;
}
void ViewAnimator::advance(double now_ms) {
    if (t_ < 1.f) t_ = duration_ > 0 ? clampf(float((now_ms - start_ms_) / duration_), 0, 1) : 1.f;
    if (spacing_t_ < 1.f) spacing_t_ = duration_ > 0 ? clampf(float((now_ms - spacing_start_ms_) / duration_), 0, 1) : 1.f;
    if (look_t_ < 1.f) look_t_ = duration_ > 0 ? clampf(float((now_ms - look_start_ms_) / duration_), 0, 1) : 1.f;
    if (!switch_.active && t_ >= 1.f) shown_ = to_selected_;
    while (switch_.active) {
        auto& w = switch_;
        const double e = now_ms - w.start_ms, out = w.out_ms, turn = out + turn_, in = turn + duration_;
        if (e < out) { w.zoom_now = w.zoom * (1 - smoothstep(float(e / out))); w.look_now = 0; shown_ = w.from; return; }
        shown_ = w.to;
        if (e < turn) { w.zoom_now = 0; w.look_now = turn_ > 0 ? smootherstep(float((e - out) / turn_)) : 1.f; return; }
        if (e < in) { const float s = smoothstep(float((e - turn) / duration_)); w.zoom_now = s; w.look_now = 1 - s; return; }
        // Landed: zoomed into the target, the view untouched. A queued switch starts where this one ended.
        const bool again = w.queued && w.next != w.to;
        const std::uint32_t from = w.to, next = w.next;
        w.active = false; w.zoom_now = 1; w.look_now = 0;
        if (!again) { shown_ = from; break; }
        start_switch(from, next, 1.f, w.start_ms + in);
    }
}
float ViewAnimator::zoom() const { return switch_.active ? switch_.zoom_now : lerp(from_zoom_, to_zoom_, smoothstep(t_)); }
float ViewAnimator::look() const { return switch_.active ? switch_.look_now : from_look_ * (1 - smoothstep(look_t_)); }
float ViewAnimator::spacing() const { return lerp(from_spacing_, to_spacing_, smoothstep(spacing_t_)); }
}
