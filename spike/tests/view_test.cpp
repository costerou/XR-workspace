#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
bool near(float a, float b, float eps = 0.5f) { return std::fabs(a - b) <= eps; }
float lerp_check(float a, float b, float t) { return a + (b - a) * t; }
bool same(const FRect& r, FRect e) { return near(r.x, e.x, 1e-3f) && near(r.y, e.y, 1e-3f) && near(r.width, e.width, 1e-3f) && near(r.height, e.height, 1e-3f); }

// Ctrl+Alt+R's tiles (TileEase): the same layouts the renderer draws, through a remove, a new aspect and an add.
void tile_ease_tests() {
    const Size four[] = {{2560, 1080}, {1920, 1080}, {1920, 1080}, {2560, 1080}}, three[] = {{2560, 1080}, {1920, 1080}, {1920, 1080}},
               even[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}}, two[] = {{1920, 1080}, {1920, 1080}};
    const auto from = overview_layout(four, 1920, 1200, 40), mid = overview_layout(three, 1920, 1200, 40), to = overview_layout(even, 1920, 1200, 40);
    const auto one = [&](const std::vector<FRect>& v, std::size_t i) { return v[i]; };

    {  // Unarmed: the layout itself, the very same vector, whatever changes (a pill's Add / Remove / Ultrawide jump as before).
        TileEase e;
        CHECK(&e.tiles(three, mid, true, 1000) == &mid);
        CHECK(&e.tiles(four, from, true, 1016) == &from);
        CHECK(!e.active() && !e.armed(1016));
    }
    {  // Armed, nothing changed yet: the layout as it is; a change after the arm has run out jumps.
        TileEase e;
        e.arm(three, mid, 1000);
        CHECK(e.armed(1000 + double(kTileEaseArmMs)) && !e.active());
        CHECK(&e.tiles(three, mid, true, 1100) == &mid);
        CHECK(&e.tiles(even, to, true, 1000 + double(kTileEaseArmMs) + 1) == &to);
        CHECK(!e.active());
    }
    {  // Remove the last of four: the three left go from their tiles to the wider ones, along one smootherstep.
        TileEase e;
        e.arm(four, from, 1000);
        e.reindex(std::array<int, 3>{0, 1, 2});
        const auto& start = e.tiles(three, mid, true, 2000);  // the first frame after the change: not moved at all
        CHECK(start.size() == 3 && &start != &mid && e.active());
        for (std::size_t i = 0; i < 3; ++i) CHECK(same(one(start, i), from[i]));
        const auto half = e.tiles(three, mid, true, 2000 + double(kTileEaseMs) / 2);  // t 0.5: halfway on the curve
        for (std::size_t i = 0; i < 3; ++i) {
            CHECK(near(half[i].x, (from[i].x + mid[i].x) / 2, 1e-2f) && near(half[i].width, (from[i].width + mid[i].width) / 2, 1e-2f));
            CHECK(near(half[i].y, (from[i].y + mid[i].y) / 2, 1e-2f) && near(half[i].height, (from[i].height + mid[i].height) / 2, 1e-2f));
        }
        const auto early = e.tiles(three, mid, true, 2000 + double(kTileEaseMs) * 0.1);  // eased in: still slow at 10%
        CHECK(std::fabs(early[0].width - from[0].width) < 0.02f * std::fabs(mid[0].width - from[0].width));
        CHECK(&e.tiles(three, mid, true, 2000 + double(kTileEaseMs)) == &mid);  // lands exactly, and stops asking for frames
        CHECK(!e.active());
    }
    {  // A second change on the way (a new frame's aspect) starts from where the tiles are, never from a jump.
        TileEase e;
        e.arm(four, from, 1000);
        e.reindex(std::array<int, 3>{0, 1, 2});
        e.tiles(three, mid, true, 2000);
        const auto at = e.tiles(three, mid, true, 2200);
        const auto again = e.tiles(even, to, true, 2200);
        for (std::size_t i = 0; i < 3; ++i) CHECK(same(again[i], at[i]));
        CHECK(e.active());
        CHECK(&e.tiles(even, to, true, 2200 + double(kTileEaseMs)) == &to);
    }
    {  // Add: a new desktop grows out of the middle of its slot; the others go from their tiles, by name, not by index.
        const auto before = overview_layout(two, 1920, 1200, 40);
        TileEase e;
        e.arm(two, before, 1000);
        e.reindex(std::array<int, 3>{-1, 0, 1});  // XR-1 is new: XR-2 and XR-3 were indices 0 and 1
        const auto start = e.tiles(even, to, true, 1100);
        CHECK(same(start[1], before[0]) && same(start[2], before[1]));
        CHECK(start[0].width > 0 && near(start[0].width, to[0].width * kTileEaseBornScale, 1e-2f));
        CHECK(near(start[0].x + start[0].width / 2, to[0].x + to[0].width / 2, 1e-2f));
        CHECK(near(start[0].y + start[0].height / 2, to[0].y + to[0].height / 2, 1e-2f));
    }
    {  // A frame re-arming mid-way keeps following the tiles (the removal's frame, then the finished change's).
        TileEase e;
        e.arm(four, from, 1000);
        e.reindex(std::array<int, 3>{0, 1, 2});
        e.tiles(three, mid, true, 1000);
        const auto at = e.tiles(three, mid, true, 1200);
        e.arm(three, mid, 1200);
        e.reindex(std::array<int, 3>{0, 1, 2});
        const auto on = e.tiles(three, mid, true, 1200);
        for (std::size_t i = 0; i < 3; ++i) CHECK(same(on[i], at[i]));
        // The same desktops again: the ease goes on on its own clock, it does not start over (no stutter mid-glide).
        CHECK(e.active());
        CHECK(&e.tiles(three, mid, true, 1000 + double(kTileEaseMs)) == &mid);
    }
    {  // Overview only: a zoom drops the ease and the arm.
        TileEase e;
        e.arm(four, from, 1000);
        e.reindex(std::array<int, 3>{0, 1, 2});
        e.tiles(three, mid, true, 1000);
        CHECK(&e.tiles(three, mid, false, 1100) == &mid);
        CHECK(!e.active() && !e.armed(1100));
    }
}

// Every desktop of every run maximizes: the view selects, zooms on, highlights and aims at the one the supervisor names, by
// its index in the run's desktops. Until 2026-11-03 the animator stopped at the third, so with four desktops the glasses
// maximized XR-3 while the keyboard and mouse went to XR-4.
void every_desktop_tests() {
    const Canvas canvas{1920, 1200, 0.8f, 0.5f};
    const std::uint32_t last = kMaxDesktops - 1;
    for (std::uint32_t n = 1; n <= kMaxDesktops; ++n) {
        const std::vector<Size> sizes(n, Size{1920, 1080});
        const auto overview = overview_layout(sizes, 1920, 1200, 40);
        for (std::uint32_t i = 0; i < n; ++i) {
            ViewAnimator v(200);
            v.set_target(false, i, 40, 0); v.advance(1000);  // selected in the overview: the highlight is on it
            CHECK(v.selected() == i && v.focus() == i && v.zoom() == 0);
            v.set_target(true, i, 40, 1000); v.advance(2000);  // Ctrl+Alt+Up
            CHECK(!v.animating() && v.zoom() == 1 && v.selected() == i && v.focus() == i && v.position() == float(i));
            const auto tiles = blended_layout(sizes, 1920, 1200, 40, v.zoom(), v.position());  // it fills the view
            CHECK(tiles.size() == n && near(tiles[i].x + tiles[i].width / 2, 960) && near(tiles[i].width, 1920));
            const auto placed = zoomed_placements(canvas, overview, tiles, {}, v.zoom(), v.position());
            CHECK(placed.size() == n && near(placed[i].distance, 1, 1e-4f));
            CHECK(zoom_weight(v.zoom(), v.position(), i) == 1 && focus_weight(v) == 1);
            // Follow and locked aim at it, and it is drawn on top of anything dragged nearer.
            const auto aim = focus_direction(canvas, overview, {}, v.focus());
            const float x = (2 * (overview[i].x + overview[i].width / 2) / 1920 - 1) * canvas.half_w;
            CHECK(near(aim[0], std::atan2(x, 1.f), 1e-5f) && near(aim[1], 0, 1e-5f));
            CHECK(zoom_draw_order(draw_order(canvas, tiles, placed), v.zoom(), v.focus()).back() == i);
        }
    }
    {  // Maximized, Ctrl+Alt+Left / Right's animated switch along every desktop and back, each landing where it was sent.
        ViewAnimator v(200);
        v.set_target(true, 0, 40, 0); v.advance(1000);
        double t = 1000;
        auto walk = [&](std::uint32_t to) {
            v.set_target(true, to, 40, t); CHECK(v.switching() && v.selected() == to);
            t += 2000; v.advance(t);
            CHECK(!v.switching() && v.zoom() == 1 && v.focus() == to && v.position() == float(to));
            const auto turn = v.take_landed_turn();
            CHECK(turn && (*turn)[1] == to);
        };
        for (std::uint32_t to = 1; to <= last; ++to) walk(to);
        for (std::uint32_t to = last; to-- > 0;) walk(to);
        // Sent on to the last one while zooming out of the first (a retarget), then while turning (it queues).
        v.set_target(true, 1, 40, t); v.advance(t + 50); CHECK(v.switching() && v.zoom() > 0);
        v.set_target(true, last, 40, t + 50); t += 2000; v.advance(t);
        CHECK(!v.switching() && v.focus() == last && v.zoom() == 1);
        v.set_target(true, 0, 40, t); v.advance(t + 300); CHECK(v.switching() && v.zoom() == 0);
        v.set_target(true, last, 40, t + 300); t += 4000; v.advance(t);
        CHECK(!v.switching() && v.focus() == last && v.zoom() == 1);
        // The cut (a maximized cursor switch or key: follow and locked): straight there, to the last one and from it.
        v.set_target(true, 0, 40, t, false, true); CHECK(!v.switching() && v.focus() == 0);
        v.set_target(true, last, 40, t, false, true); CHECK(!v.switching() && v.focus() == last && v.zoom() == 1);
    }
    {  // The selected last desktop removed: its `desktops` frame comes just before the `view` frame that moves the selection
        // (view.py), so for a moment the animator names a desktop that is gone. Nothing it feeds goes out of bounds.
        ViewAnimator v(200);
        v.set_target(true, last, 40, 0); v.advance(1000);
        const std::vector<Size> fewer(last, Size{1920, 1080});
        const auto overview = overview_layout(fewer, 1920, 1200, 40);
        const auto tiles = blended_layout(fewer, 1920, 1200, 40, v.zoom(), v.position());
        CHECK(tiles.size() == fewer.size() && std::all_of(tiles.begin(), tiles.end(), [](const FRect& r) { return std::isfinite(r.x) && r.width > 0; }));
        CHECK(zoomed_placements(canvas, overview, tiles, {}, v.zoom(), v.position()).size() == fewer.size());
        CHECK((focus_direction(canvas, overview, {}, v.focus()) == std::array<float, 2>{0, 0}));
        const std::vector<std::size_t> order{0, 1, 2};
        CHECK(zoom_draw_order(order, v.zoom(), v.focus()) == order);
        v.set_target(true, last - 1, 40, 1000, false, true); v.advance(2000);
        CHECK(v.selected() == last - 1 && v.focus() == last - 1 && v.zoom() == 1);
    }
}

// The hand's pan past a maximized desktop's edge (view.hpp EdgePan, tools/native_spike/edge_pan.py).
namespace {
EdgePan pan_frame(float peek, float cross = 0, float turn = 0, float zoom = 0.2f, std::uint32_t from = 0, std::uint32_t to = 1) {
    EdgePan p; p.active = true; p.from = from; p.to = to; p.exit = 0.3f; p.entry = 0.3f; p.peek = peek; p.cross = cross; p.turn = turn; p.zoom = zoom;
    return p;
}
ViewAnimator maximized_on(std::uint32_t desktop) {
    ViewAnimator v; v.set_target(false, desktop, 40, 0); v.set_target(true, desktop, 40, 0); v.advance(1000);
    CHECK(v.zoom() == 1 && !v.animating() && v.focus() == desktop);
    return v;
}
}
void edge_pan_tests() {
    {  // The hand draws it: the zoom falls with the peek and comes back with it, the desktop never changes.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1), 1000);
        CHECK(v.pan_active() && v.switching() && v.animating());
        for (double t = 1000; t < 1600; t += 8) v.advance(t);
        CHECK(near(v.zoom(), 0.2f, 1e-3f) && v.focus() == 0 && v.selected() == 0);
        v.set_pan(pan_frame(0.5f, 0, 0.1f), 1600);
        for (double t = 1600; t < 2100; t += 8) v.advance(t);
        CHECK(near(v.zoom(), lerp_check(1.f, 0.2f, smoothstep(0.5f)), 1e-2f));
        CHECK(near(v.pan_turn(), 0.1f, 1e-2f));
        v.set_pan(EdgePan{}, 2100);  // pulled back
        double last = v.zoom();
        for (double t = 2100; t < 2800; t += 8) { v.advance(t); CHECK(v.zoom() >= last - 1e-5); last = v.zoom(); }
        CHECK(v.zoom() == 1 && !v.pan_active() && !v.switching() && !v.animating() && v.focus() == 0);
        CHECK(v.take_landed_turns().empty());
    }
    {  // The crossing takes the zoom on to 0; a jump of the hand reaches the screen eased, at most the whole zoom in kViewEaseMs.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1, 1, 0.3f), 1000);
        float was = v.zoom();
        for (double t = 1008; t < 1100; t += 8) { v.advance(t); CHECK(was - v.zoom() <= 8 / kViewEaseMs + 1e-4f); was = v.zoom(); }
        CHECK(v.zoom() > 0.4f);
        for (double t = 1100; t < 1800; t += 8) v.advance(t);
        CHECK(v.zoom() == 0 && near(v.pan_cross(), 1, 1e-3f) && near(v.pan_turn(), 0.3f, 1e-3f));
    }
    {  // Landing from the end of the way: the turn and the zoom in, about 0.52 s, no jump in zoom, on the target at the end.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1, 1, 0.3f), 1000);
        for (double t = 1000; t < 1800; t += 8) v.advance(t);
        CHECK(v.zoom() == 0);
        v.set_target(true, 1, 40, 1800, false, false, true);
        CHECK(v.pan_landing() && v.selected() == 1 && v.switching());
        float z = v.zoom(), turned = 0;
        double landed = 0;
        for (double t = 1804; t < 2600; t += 4) {
            v.advance(t);
            CHECK(std::fabs(v.zoom() - z) < 0.04f);
            z = v.zoom();
            if (v.pan_landing()) { CHECK(v.turn() >= turned); turned = v.turn(); }
            if (!v.pan_landing() && !landed) landed = t;
        }
        CHECK(landed >= 1800 + 500 && landed <= 1800 + 540);
        CHECK(v.zoom() == 1 && v.focus() == 1 && !v.pan_active() && !v.animating());
    }
    {  // A flick: the hand arrives before the zoom has come down. Still whole, and within the 0.74 s that the cooldown covers.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1, 1, 0.3f), 1000);
        v.advance(1016);
        v.set_target(true, 1, 40, 1016, false, false, true);
        double landed = 0;
        for (double t = 1020; t < 2000 && !landed; t += 4) { v.advance(t); if (!v.pan_landing()) landed = t; }
        CHECK(landed && landed - 1016 <= 745);
        CHECK(v.focus() == 1 && v.zoom() == 1);
    }
    {  // A key during a pan is a cut: straight there, the pan gone.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1), 1000);
        for (double t = 1000; t < 1300; t += 8) v.advance(t);
        v.set_target(true, 2, 40, 1300, false, true);
        CHECK(!v.pan_active() && v.zoom() == 1 && v.focus() == 2 && !v.switching());
    }
    {  // Un-maximized during a pan: the zoom carries on from where the pan had it.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1), 1000);
        for (double t = 1000; t < 1300; t += 8) v.advance(t);
        const float z = v.zoom();
        v.set_target(false, 0, 40, 1300);
        CHECK(!v.pan_active() && near(v.zoom(), z, 1e-4f));
        v.advance(1700);
        CHECK(v.zoom() == 0);
    }
    {  // Frames that do not fit are ignored: the overview, another desktop, mid switch; an unchanged view frame changes nothing.
        ViewAnimator o; o.set_target(false, 0, 40, 0); o.advance(500);
        o.set_pan(pan_frame(1), 500);
        CHECK(!o.pan_active());
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1, 0, 0, 0.2f, 1, 2), 1000);
        CHECK(!v.pan_active());
        v.set_pan(pan_frame(1), 1000);
        v.set_target(true, 0, 40, 1050);
        CHECK(v.pan_active());
    }
    {  // `pan` with no pan under way is a cut, and an end of pan after the landing is nothing.
        auto v = maximized_on(0);
        v.set_target(true, 1, 40, 1000, false, false, true);
        CHECK(v.zoom() == 1 && v.focus() == 1 && !v.switching());
        v.set_pan(EdgePan{}, 1010);
        CHECK(!v.pan_active());
    }
    {  // The supervisor stopped mid-pan: the view does not stay turned.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1, 0, 0.2f), 1000);
        for (double t = 1000; t < 1000 + double(kPanStaleMs) + 900; t += 16) v.advance(t);
        CHECK(!v.pan_active() && v.zoom() == 1);
    }
    {  // Rapid pushes: a new pan takes over from the eased-back one with no jump; none can start while one lands.
        auto v = maximized_on(0);
        v.set_pan(pan_frame(1, 0, 0.2f), 1000);
        for (double t = 1000; t < 1200; t += 8) v.advance(t);
        v.set_pan(EdgePan{}, 1200);
        v.advance(1216);
        const float z = v.zoom();
        auto other = pan_frame(0.5f, 0, 0.05f, 0.2f, 0, 2);
        v.set_pan(other, 1216);
        CHECK(near(v.zoom(), z, 1e-4f) && v.pan().to == 2);
        for (double t = 1216; t < 1600; t += 8) v.advance(t);
        v.set_pan(pan_frame(1, 1, 0.2f, 0.2f, 0, 2), 1600);
        for (double t = 1600; t < 2200; t += 8) v.advance(t);
        v.set_target(true, 2, 40, 2200, false, false, true);
        v.set_pan(pan_frame(1, 0, 0.1f, 0.2f, 2, 1), 2210);
        CHECK(v.pan_landing() && v.pan().to == 2);
    }
    // The way across, in every direction, on three desktops: left, middle, right. Unit vectors.
    const Size sizes[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}};
    const Canvas c{1920, 1200, 0.42f, 0.27f};
    const auto overview = overview_layout(sizes, 1920, 1200, 40);
    const auto rad = [](float deg) { return deg * float(M_PI) / 180.f; };
    const auto way = [&](std::span<const Placement> placed, std::uint32_t from, std::uint32_t to, std::uint8_t side) {
        auto p = pan_frame(1, 0, 0, 0, from, to); p.side = side; p.exit = p.entry = 0.5f;
        return edge_pan_path(c, sizes, 40, overview, placed, p);
    };
    const auto dir_of = [](const Vec3& v) { return std::array<float, 2>{std::atan2(v.x, -v.z), std::atan2(v.y, std::hypot(v.x, v.z))}; };
    {  // Right and left along a row. The gap is tiny, so the axis comes from the far edge: the same way.
        const std::vector<Placement> flat(3);
        const auto r = way(flat, 0, 1, 0), l = way(flat, 1, 0, 1);
        CHECK(r && l && r->angle < kPanAxisMinRad);
        CHECK(near(r->axis.y, -1, 1e-3f));  // turning right: about -y
        CHECK(near(l->axis.y, 1, 1e-3f));
        const auto r2 = way(flat, 1, 2, 0);
        CHECK(r2 && near(r2->axis.y, -1, 1e-3f));
    }
    {  // Up and down, to a desktop lifted over its slot; a diagonal between them; far round behind you, the way of the push.
        const std::vector<Placement> up{{}, {rad(-8), rad(40), 1, 0, 0}, {rad(90), 0, 1, 0, 0}};
        const auto u = way(up, 0, 1, 2);
        CHECK(u && u->angle >= kPanAxisMinRad && dir_of(u->to)[1] > dir_of(u->from)[1]);
        const std::vector<Placement> behind{{}, {rad(150), 0, 1, 0, 0}, {rad(-160), 0, 1, 0, 0}};
        const auto b = way(behind, 0, 1, 0), bl = way(behind, 0, 2, 1);
        CHECK(b && b->axis.y < -0.9f);      // right: the desktop far round to the right
        CHECK(bl && bl->axis.y > 0.9f);     // left: the one far round to the left
        const std::vector<Placement> diag{{}, {rad(30), rad(20), 1, 0, 0}, {}};
        const auto d = way(diag, 0, 1, 0);
        CHECK(d && d->axis.y < 0 && d->axis.x > 0);  // right and up
    }
    {  // The dot goes straight from E to P, each share of the way that share of the angle.
        const std::vector<Placement> far{{}, {rad(60), 0, 1, 0, 0}, {}};
        const auto w = way(far, 0, 1, 0);
        CHECK(w && w->angle > 0.5f);
        const Vec3 start = edge_pan_dot(*w, w->from, 0), end = edge_pan_dot(*w, w->from, 1);
        CHECK(near(start.x, w->from.x, 1e-4f) && near(end.x, w->to.x, 1e-6f));
        for (const float s : {0.25f, 0.5f, 0.9f}) {
            const Vec3 q = edge_pan_dot(*w, w->from, s);
            const float a = std::acos(std::clamp(q.x * w->from.x + q.y * w->from.y + q.z * w->from.z, -1.f, 1.f));
            CHECK(near(a, w->angle * s, 2e-3f));
            CHECK(std::fabs(q.x * w->axis.x + q.y * w->axis.y + q.z * w->axis.z) < 2e-3f);
        }
    }
    {  // Edges, and what cannot be drawn.
        const FRect t{10, 20, 100, 50};
        const auto a = edge_point(t, 0, 0.3f), b = edge_point(t, 1, 0.3f), u = edge_point(t, 2, 0.3f), d = edge_point(t, 3, 2.f);
        CHECK(near(a[0], 110, 1e-3f) && near(a[1], 35, 1e-3f) && near(b[0], 10, 1e-3f) && near(u[0], 40, 1e-3f) && near(u[1], 20, 1e-3f));
        CHECK(near(d[0], 110, 1e-3f) && near(d[1], 70, 1e-3f));
        const std::vector<Placement> flat(3);
        CHECK(!way(flat, 0, 0, 0) && !way(flat, 0, 7, 0));
    }
    std::cout << "edge pan passed\n";
}

int main() {
    edge_pan_tests();
    tile_ease_tests();
    every_desktop_tests();
    const Size eq[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}};
    // Overview: 3 x 16:9 desktops in 1920x1200 with 40 px gaps and margins.
    auto o = overview_layout(eq, 1920, 1200, 40);
    CHECK(o.size() == 3);
    const float w = (1920.f - 4 * 40.f) / 3.f;
    CHECK(near(o[0].x, 40) && near(o[0].width, w) && near(o[0].height, w * 1080.f / 1920.f));
    CHECK(near(o[1].x, 40 + w + 40) && near(o[2].x + o[2].width, 1880));
    CHECK(near(o[0].y, (1200 - o[0].height) / 2));
    auto wide = overview_layout(eq, 1920, 1200, 0);
    CHECK(near(wide[0].x, 0) && near(wide[0].width, 640));
    // Spacing is bounded; absurd spacing never yields negative tiles.
    for (auto& r : overview_layout(eq, 1920, 1200, 5000)) CHECK(r.width >= 0 && r.height >= 0);

    // Zoomed: selected fills (aspect-fit), neighbours offset by one view width plus spacing.
    auto z = zoomed_layout(eq, 1920, 1200, 40, 1);
    CHECK(near(z[1].x, 0) && near(z[1].width, 1920) && near(z[1].height, 1080) && near(z[1].y, 60));
    CHECK(near(z[0].x, -1960) && near(z[2].x, 1960));
    auto mid = zoomed_layout(eq, 1920, 1200, 40, 1.5f);  // sliding between 1 and 2
    CHECK(near(mid[1].x, -980) && near(mid[2].x, 980));

    // Blend endpoints and monotone easing.
    auto b0 = blended_layout(eq, 1920, 1200, 40, 0, 1), b1 = blended_layout(eq, 1920, 1200, 40, 1, 1);
    CHECK(near(b0[1].x, o[1].x) && near(b1[1].width, 1920));

    // three equal sources match old layout (outputs captured from the pre-span implementation).
    const FRect ov[] = {{40, 435, 586.6667f, 330}, {666.6667f, 435, 586.6667f, 330}, {1293.3334f, 435, 586.6667f, 330}};
    const struct { float pos; FRect r[3]; } zoomed_old[] = {
        {0, {{0, 60, 1920, 1080}, {1960, 60, 1920, 1080}, {3920, 60, 1920, 1080}}},
        {1, {{-1960, 60, 1920, 1080}, {0, 60, 1920, 1080}, {1960, 60, 1920, 1080}}},
        {1.5f, {{-2940, 60, 1920, 1080}, {-980.0001f, 60, 1920, 1080}, {980, 60, 1920, 1080}}}};
    for (const auto& c : zoomed_old) {
        auto z0 = blended_layout(eq, 1920, 1200, 40, 0, c.pos), z1 = blended_layout(eq, 1920, 1200, 40, 1, c.pos);
        CHECK(z0.size() == 3 && z1.size() == 3);
        for (int i = 0; i < 3; ++i) CHECK(same(z0[i], ov[i]) && same(z1[i], c.r[i]));
    }

    // mixed overview shares height: 16:9, 32:9 and 3440x1440 side by side within the view.
    const Size mixed[] = {{1920, 1080}, {3840, 1080}, {3440, 1440}};
    auto mo = blended_layout(mixed, 1920, 1200, 40, 0, 0);
    CHECK(mo.size() == 3 && mo[0].height > 0);
    for (int i = 0; i < 3; ++i) CHECK(near(mo[i].height, mo[0].height, 1e-3f) && near(mo[i].width, mo[i].height * mixed[i].w / mixed[i].h));
    CHECK(near(mo[1].width, 2 * mo[0].width) && mo[0].x >= 0 && mo[2].x + mo[2].width <= 1920 + 1e-3f);
    CHECK(mo[0].x + mo[0].width <= mo[1].x && mo[1].x + mo[1].width <= mo[2].x);
    float total = 0; for (auto& r : mo) total += r.width;
    CHECK(total <= 1920);

    // zoomed mixed centres selected: tile 1 (32:9) centred and fitted; neighbours share its height.
    auto mz = blended_layout(mixed, 1920, 1200, 40, 1, 1);
    CHECK(mz.size() == 3 && near(mz[1].x + mz[1].width / 2, 960));
    CHECK(mz[1].x >= -0.5f && mz[1].x + mz[1].width <= 1920.5f && mz[1].y >= -0.5f && mz[1].y + mz[1].height <= 1200.5f);
    CHECK(near(mz[1].width, 1920) && near(mz[0].height, mz[1].height, 1e-3f) && near(mz[2].height, mz[1].height, 1e-3f));
    CHECK(near(mz[0].x + mz[0].width + 40, mz[1].x) && near(mz[1].x + mz[1].width + 40, mz[2].x));
    // Fractional position: the lerp of the two centres sits at the view centre.
    auto mf = zoomed_layout(mixed, 1920, 1200, 40, 1.25f);
    const float c1 = mf[1].x + mf[1].width / 2, c2 = mf[2].x + mf[2].width / 2;
    CHECK(near(c1 + (c2 - c1) * 0.25f, 960));
    CHECK(overview_layout(std::span<const Size>{}, 1920, 1200, 40).empty());
    const Size bad[] = {{1920, 1080}, {0, 1080}};
    CHECK(blended_layout(bad, 1920, 1200, 40, 0.5f, 0).empty());

    CHECK(smoothstep(0) == 0 && smoothstep(1) == 1 && near(smoothstep(0.5f), 0.5f, 1e-3f) && smoothstep(-1) == 0 && smoothstep(2) == 1);
    for (float t = 0; t < 1; t += 0.1f) CHECK(smoothstep(t + 0.1f) >= smoothstep(t));

    // Animator: time-based, reaches target exactly, retargets from the current state.
    ViewAnimator a(200);
    a.set_target(false, 0, 40, 0); a.advance(1000);
    CHECK(!a.animating() && a.zoom() == 0 && a.position() == 0);
    a.set_target(true, 2, 40, 1000);
    CHECK(a.position() == 2);  // zooming in from the overview: on the selection at once; the zoom eases
    a.advance(1100); CHECK(a.animating() && a.zoom() > 0 && a.zoom() < 1 && a.position() == 2);
    a.advance(1200); CHECK(!a.animating() && a.zoom() == 1 && a.position() == 2 && a.selected() == 2);
    a.set_target(true, 1, 120, 1200); a.advance(1300);  // zoomed: the switch animates (zooming out of 2 first)
    CHECK(a.switching() && a.position() == 2 && a.selected() == 1 && a.zoom() < 1 && a.spacing() > 40 && a.spacing() < 120);
    a.advance(2000); CHECK(a.zoom() == 1 && a.position() == 1 && a.spacing() == 120);
    a.set_target(false, 1, 120, 2000); a.advance(2100); CHECK(a.zoom() > 0 && a.zoom() < 1 && a.position() == 1);
    a.advance(3000); CHECK(a.zoom() == 0 && !a.animating());
    a.set_target(true, 9, 9999, 3000); a.advance(4000);  // selection and spacing are clamped
    CHECK(a.selected() == kMaxDesktops - 1 && a.spacing() == float(kMaxSpacing));

    // Cursor switch with its animation off (anchor mode, 2026-11-03): zoomed in, a cut shows the target at once at its
    // placement: no zoom out, no turn, nothing landed for anchor mode's room. A later plain frame changes nothing.
    {
        ViewAnimator v;
        v.set_target(false, 0, 40, 0); v.advance(1000);
        v.set_target(true, 0, 40, 1000); v.advance(2000);
        v.set_target(true, 2, 40, 2000, false, true);
        CHECK(!v.switching() && !v.animating() && v.zoom() == 1 && v.look() == 0 && v.turn() == 0 && v.position() == 2 &&
              v.focus() == 2 && v.selected() == 2 && v.take_landed_turns().empty());
        for (double t = 2000; t <= 3000; t += 10) {
            v.advance(t); CHECK(v.zoom() == 1 && v.look() == 0 && v.turn() == 0 && v.focus() == 2 && !v.switching());
            // Follow / locked switch with a cut too (overview-anchor-only): their aim stays fully on the desktop, never
            // easing to the overview's centre on the way (focus_weight 1 throughout).
            CHECK(focus_weight(v) == 1);
        }
        v.set_target(true, 2, 40, 3000); v.advance(3010); CHECK(!v.animating() && v.focus() == 2);
        // A key switch in flight (mid-turn) is dropped by a cut, unlanded, which lands on its own target.
        v.set_target(true, 0, 40, 4000); v.advance(4300); CHECK(v.switching() && v.zoom() == 0 && v.turn() > 0);
        v.set_target(true, 1, 40, 4300, false, true);
        CHECK(!v.switching() && v.zoom() == 1 && v.look() == 0 && v.turn() == 0 && v.focus() == 1 && !v.turn_centred());
        v.advance(5000); CHECK(!v.animating() && v.focus() == 1 && v.take_landed_turns().empty());
        // The keys still animate after a cut, and in the overview a cut is a plain selection.
        v.set_target(true, 2, 40, 6000); v.advance(6100); CHECK(v.switching());
        v.advance(8000); CHECK(!v.switching() && v.focus() == 2);
        v.set_target(false, 2, 40, 9000); v.advance(10000);
        v.set_target(false, 0, 40, 10000, false, true); CHECK(v.zoom() == 0 && v.position() == 0 && !v.switching());
    }
    // Regression (2026-11-02, animations back): zoom in and out ease over the duration and never snap; in the overview
    // Ctrl+Alt+Left / Right just moves the selection (nothing zoomed to animate).
    CHECK(smootherstep(0) == 0 && smootherstep(1) == 1 && near(smootherstep(0.5f), 0.5f, 1e-3f));
    {
        ViewAnimator v;  // defaults: 220 ms zoom, 300 ms turn
        v.set_target(false, 0, 40, 0); v.advance(1000);
        v.set_target(true, 0, 40, 1000);
        float last = 0;
        for (double t = 1000; t <= 1220; t += 10) {
            v.advance(t); CHECK(v.zoom() >= last && v.zoom() - last < 0.12f && v.look() == 0 && !v.switching()); last = v.zoom();
        }
        CHECK(v.zoom() == 1 && !v.animating());
        v.set_target(false, 0, 40, 2000); v.advance(2110); CHECK(v.zoom() > 0.3f && v.zoom() < 0.7f);
        v.advance(2220); CHECK(v.zoom() == 0 && !v.animating());
        for (const std::uint32_t to : {1u, 2u, 1u, 0u}) {
            v.set_target(false, to, 40, 3000); CHECK(v.position() == float(to) && !v.switching() && v.zoom() == 0);
        }
    }
    // Ctrl+Alt+Left / Right zoomed in (every viewing mode: the animator has no mode): zoom out of the current desktop, turn
    // the view toward the target, zoom into it. Continuous throughout; at rest the view is never turned.
    {
        ViewAnimator v(220, 300);
        v.set_target(true, 1, 40, 0); v.advance(1000);
        CHECK(v.zoom() == 1 && v.position() == 1 && !v.animating());
        v.set_target(true, 2, 40, 1000);
        CHECK(v.switching() && v.selected() == 2 && v.position() == 1 && v.zoom() == 1 && v.look() == 0);
        CHECK(v.look_from() == 1 && v.look_to() == 2);
        float pz = 1, pl = 0;
        int phase = 1;
        for (double t = 1000; t <= 1730; t += 5) {
            v.advance(t);
            const float z = v.zoom(), l = v.look();
            CHECK(std::fabs(z - pz) < 0.06f && std::fabs(l - pl) < 0.06f);  // no jumps
            if (t < 1220) { CHECK(phase == 1 && v.position() == 1 && l == 0 && z <= pz); }                // 1: zoom out of 1
            else if (t < 1520) { phase = 2; CHECK(v.position() == 2 && z == 0 && l >= pl && l <= 1); }  // 2: turn toward 2
            else if (t < 1740) { phase = 3; CHECK(v.position() == 2 && z >= pz && l <= pl + 1e-3f); }   // 3: zoom into 2
            if (t == 1370) CHECK(l > 0.3f && l < 0.7f);  // mid-turn
            pz = z; pl = l;
        }
        v.advance(1740); CHECK(!v.switching() && !v.animating() && v.zoom() == 1 && v.look() == 0 && v.position() == 2);
        // Retargeted while still zooming out: aims the same switch elsewhere.
        v.set_target(true, 1, 40, 2000); v.advance(2100);
        v.set_target(true, 0, 40, 2100); CHECK(v.look_from() == 2 && v.look_to() == 0);
        v.advance(2900); CHECK(!v.switching() && v.position() == 0 && v.zoom() == 1);
        // Back to where it came from before the turn: it zooms straight back in.
        v.set_target(true, 1, 40, 3000); v.advance(3100);
        const float back = v.zoom();
        v.set_target(true, 0, 40, 3100); CHECK(!v.switching() && v.zoom() == back && v.position() == 0 && v.look() == 0);
        v.advance(3400); CHECK(v.zoom() == 1 && !v.animating());
        // A press mid-turn queues: the switch lands, then the next runs from there.
        v.set_target(true, 1, 40, 4000); v.advance(4300);
        v.set_target(true, 2, 40, 4300); CHECK(v.switching() && v.look_to() == 1);
        v.advance(4800); CHECK(v.switching() && v.look_from() == 1 && v.look_to() == 2);
        v.advance(5500); CHECK(!v.switching() && v.position() == 2 && v.zoom() == 1 && v.look() == 0);
        // Zooming out mid-turn: zoom and turn ease back to the plain overview from where they are.
        v.set_target(true, 0, 40, 6000); v.advance(6350);
        const float turn = v.look();
        CHECK(turn > 0 && v.zoom() == 0);
        v.set_target(false, 0, 40, 6350); CHECK(!v.switching() && v.look() == turn && v.zoom() == 0);
        v.advance(6460); CHECK(v.look() > 0 && v.look() < turn);
        v.advance(6600); CHECK(v.look() == 0 && v.zoom() == 0 && !v.animating() && v.position() == 0);
        // Zooming out mid-zoom-out of a switch keeps the zoom on the desktop being left (no jump), then shows the selection.
        v.set_target(true, 1, 40, 7000); v.advance(7500);
        v.set_target(true, 2, 40, 8000); v.advance(8100);
        const float leaving = v.zoom();
        v.set_target(false, 2, 40, 8100); CHECK(v.position() == 1 && v.zoom() == leaving);
        v.advance(8400); CHECK(v.zoom() == 0 && v.position() == 2);
    }

    {  // Per-desktop placement (orbit round you, distance, curvature). Every desktop always faces you.
    const Canvas cv{1920, 1200, 0.5f, 0.3125f};  // square canvas pixels: half_w/half_h == width/height
    const FRect centred{480, 330, 960, 540};      // its centre is the canvas centre: straight ahead
    const FRect tile{240, 300, 960, 540};         // left of centre and a little up
    auto plane = [&](float px, float py) { return Vec3{(2 * px / 1920 - 1) * 0.5f, (1 - 2 * py / 1200) * 0.3125f, -1}; };
    auto close3 = [](Vec3 a, Vec3 b, float eps = 1e-5f) { return near(a.x, b.x, eps) && near(a.y, b.y, eps) && near(a.z, b.z, eps); };
    auto sub = [](Vec3 a, Vec3 b) { return Vec3{a.x - b.x, a.y - b.y, a.z - b.z}; };
    auto dot = [](Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; };
    auto cross = [](Vec3 a, Vec3 b) { return Vec3{a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; };
    auto len = [&](Vec3 a) { return std::sqrt(dot(a, a)); };
    auto unit = [&](Vec3 a) { const float l = len(a); return Vec3{a.x / l, a.y / l, a.z / l}; };
    auto dist = [&](Vec3 a, Vec3 b) { return len(sub(a, b)); };
    auto azimuth = [](Vec3 a) { return std::atan2(a.x, -a.z); };
    auto elevation = [](Vec3 a) { return std::atan2(a.y, std::sqrt(a.x * a.x + a.z * a.z)); };
    const float px_world = 2 * 0.5f / 1920;  // world units per canvas pixel
    const float half_pi = float(M_PI) / 2;
    // A centred desktop with the default placement is the old flat canvas plane, exactly (same vertices as the
    // pre-placement quads), one column; so is anything past its edge (its frame / halo).
    for (float px : {480.f, 960.f, 1440.f, 100.f}) for (float py : {330.f, 870.f, 10.f})
        CHECK(close3(place_point(cv, centred, {}, px, py), plane(px, py), 1e-6f));
    auto flat_mesh = tile_mesh(cv, centred, {}, centred);
    CHECK(flat_mesh.size() == 4 && close3(flat_mesh[0].world, plane(480, 330)) && close3(flat_mesh[3].world, plane(1440, 870)));
    CHECK(near(flat_mesh[0].u, 0, 1e-6f) && near(flat_mesh[0].v, 0, 1e-6f) && near(flat_mesh[3].u, 1, 1e-6f) && near(flat_mesh[3].v, 1, 1e-6f));
    CHECK(near(flat_mesh[1].py, 870, 1e-4f) && near(flat_mesh[2].px, 1440, 1e-4f));  // top, bottom per column
    // The billboard: wherever it is, a desktop is square to the line of sight at its centre (its normal points at the
    // eye) and upright along its meridian (its right edge level and heading round you, its top up toward the zenith), its
    // centre in its slot's direction turned by the orbit, at the slot centre's distance times `distance`. Its shape never
    // changes: the corners keep their distances from its centre.
    const float tile_diagonal = dist(plane(240, 300), plane(1200, 840));
    auto check_facing = [&](const FRect& t, const Placement& p) {
        const float cx = t.x + t.width / 2, cy = t.y + t.height / 2;
        const float wx = (2 * cx / cv.view_w - 1) * cv.half_w, wy = (1 - 2 * cy / cv.view_h) * cv.half_h;
        const float A = std::atan2(wx, 1.f) + p.x, E = std::atan2(wy, std::sqrt(1 + wx * wx)) + p.y;
        const float r = std::sqrt(1 + wx * wx + wy * wy) * p.distance;
        const Vec3 c = place_point(cv, t, p, cx, cy);
        CHECK(close3(c, Vec3{r * std::cos(E) * std::sin(A), r * std::sin(E), -r * std::cos(E) * std::cos(A)}, 1e-5f * r));
        // Tangents from points symmetric about the centre (on a cylinder their chord is parallel to the centre's tangent),
        // far apart for precision: the surface goes on past the tile's edges.
        const float du = p.curvature > 0 ? t.width / 2 : 400, dv = 400;
        const Vec3 right = unit(sub(place_point(cv, t, p, cx + du, cy), place_point(cv, t, p, cx - du, cy)));
        const Vec3 up = unit(sub(place_point(cv, t, p, cx, cy - dv), place_point(cv, t, p, cx, cy + dv)));
        const Vec3 n = cross(right, up), eye = unit(Vec3{-c.x, -c.y, -c.z});
        CHECK(len(cross(n, eye)) < 3e-5f && dot(n, eye) > 0);                                          // faces the eye
        CHECK(close3(right, Vec3{std::cos(A), 0, std::sin(A)}, 3e-5f));                                 // level: never rolled
        CHECK(close3(up, Vec3{-std::sin(A) * std::sin(E), std::cos(E), std::cos(A) * std::sin(E)}, 3e-5f));  // its meridian
        if (p.curvature == 0) {
            CHECK(near(dist(place_point(cv, t, p, t.x, t.y), place_point(cv, t, p, t.x + t.width, t.y + t.height)),
                       std::hypot(t.width, t.height) * px_world, 1e-5f * r));
            for (const auto& [px, py] : {std::pair{t.x, t.y}, std::pair{t.x + t.width, t.y + t.height}, std::pair{t.x - 40, cy}})
                CHECK(near(dot(sub(place_point(cv, t, p, px, py), c), eye), 0, 1e-5f * r));  // flat: one plane, square to the eye
        } else {  // curved: the edges bend toward you, symmetrically
            const float l = dot(sub(place_point(cv, t, p, t.x, cy), c), eye), rr = dot(sub(place_point(cv, t, p, t.x + t.width, cy), c), eye);
            CHECK(l > 0 && near(l, rr, 1e-5f * r));
        }
    };
    const FRect tiles[] = {centred, tile, {1300.5f, 700.25f, 512.3f, 288.7f}, {0, 0, 300, 200}, {1700, 1100, 220, 100},
                           {-1960, 60, 1920, 1080}, {3900.75f, -400.5f, 640, 360}};
    const float pi = float(M_PI);
    const Placement placements[] = {{}, {pi, 0, 1, 0}, {-pi, 0, 1, 0}, {half_pi, 0, 1, 0}, {-2.2f, 0, 1, 0},
                                    {0, half_pi, 1, 0}, {0, -half_pi, 1, 0}, {0, 0.7f, 1, 0}, {pi, half_pi, 1, 0},
                                    {-0.7f, 0.3f, 1.6f, 1.f}, {2.9f, -1.2f, 0.5f, 2 * pi / 3}, {0.05f, -0.02f, 1.37f, 0.3f},
                                    {-pi, -0.6f, 2.f, 0}, {1.f, 1.4f, 0.75f, half_pi}};
    for (const FRect& t : tiles) for (const Placement& p : placements) check_facing(t, p);
    // An off-centre desktop at its slot already faces you: its centre stays where the plane put it (the same direction
    // and distance) and it turns toward you about it. This one is left of centre and a little up, so in depth its left
    // edge comes forward and its right edge goes back, and it tips to face down at you (top forward, bottom back).
    const Vec3 c0 = place_point(cv, tile, {}, 720, 570);
    CHECK(close3(c0, plane(720, 570)));
    CHECK(place_point(cv, tile, {}, 240, 570).z > -1 && place_point(cv, tile, {}, 1200, 570).z < -1);
    CHECK(place_point(cv, tile, {}, 720, 300).z > -1 && place_point(cv, tile, {}, 720, 840).z < -1);
    CHECK(!close3(place_point(cv, tile, {}, 240, 300), plane(240, 300), 1e-3f));  // so no longer on the canvas plane
    // Curvature zero is flat; any curvature keeps the centre and its tangent frame in place.
    Placement curved; curved.curvature = half_pi;
    const Vec3 k0 = place_point(cv, centred, curved, 960, 600);
    CHECK(close3(k0, plane(960, 600)));
    const Vec3 k1 = place_point(cv, centred, curved, 960.01f, 600);
    CHECK(near((k1.x - k0.x) / (0.01f * px_world), 1, 1e-2f) && near(k1.z - k0.z, 0, 1e-6f));  // tangent along +x
    CHECK(close3(place_point(cv, tile, curved, 720, 570), c0));  // off-centre too
    // Edges bend toward the viewer symmetrically, aspect not stretched: arc length == flat width, height unchanged.
    const Vec3 left = place_point(cv, centred, curved, 480, 600), right = place_point(cv, centred, curved, 1440, 600);
    CHECK(left.z > -1 && near(left.z, right.z, 1e-6f) && near(left.x - k0.x, -(right.x - k0.x), 1e-6f));
    const float tw = 960 * px_world;
    CHECK(near(right.x - left.x, 2 * (tw / half_pi) * std::sin(half_pi / 2), 1e-5f));  // chord of a 90 degree arc
    for (const FRect& t : {centred, tile}) {
        auto mesh = tile_mesh(cv, t, curved, t);
        CHECK(mesh.size() > 4 && mesh.size() % 2 == 0);
        float arc = 0;
        for (std::size_t i = 2; i < mesh.size(); i += 2) arc += dist(mesh[i].world, mesh[i - 2].world);
        CHECK(near(arc, tw, tw * 2e-3f));
        CHECK(near(dist(mesh[0].world, mesh[1].world), 540 * px_world, 1e-5f));  // vertical edges stay straight and full height
        for (std::size_t i = 2; i < mesh.size(); i += 2) CHECK(mesh[i].u > mesh[i - 2].u);  // texture runs monotonically along the arc
    }
    // A frame/halo rect past the tile continues on the same cylinder.
    const Vec3 beyond = place_point(cv, centred, curved, 1480, 600);
    CHECK(beyond.z > right.z && beyond.x > right.x);
    // Position: x / y orbit the desktop round the eye (a rigid rotation about it): x turns it round you about the
    // vertical, y lifts it along its own meridian; the shape and the distance never change.
    const float a0 = azimuth(c0);  // the slot's own azimuth (this tile is left of centre)
    CHECK(a0 < 0);
    Placement round; round.x = half_pi;  // a quarter turn to the right
    const Vec3 r0 = place_point(cv, tile, round, 720, 570);
    CHECK(near(azimuth(r0), a0 + half_pi, 1e-5f) && near(r0.y, c0.y, 1e-6f) && near(len(r0), len(c0), 1e-6f));
    CHECK(near(dist(place_point(cv, tile, round, 240, 300), place_point(cv, tile, round, 1200, 840)), tile_diagonal, 1e-5f));
    // It keeps facing you as at its slot: the angle between the view ray and the desktop's edges is unchanged.
    auto angle = [&](Vec3 a, Vec3 b) { return std::acos(std::clamp(dot(a, b) / (len(a) * len(b)), -1.f, 1.f)); };
    CHECK(near(angle(r0, place_point(cv, tile, round, 240, 570)), angle(c0, place_point(cv, tile, {}, 240, 570)), 1e-5f));
    Placement behind; behind.x = pi;  // directly behind you, whatever the slot
    CHECK(near(std::fabs(azimuth(place_point(cv, tile, behind, 720, 570))), pi - std::fabs(a0), 1e-5f) &&
          place_point(cv, tile, behind, 720, 570).z > 0);
    Placement lifted; lifted.y = pi / 4;  // up along its own meridian
    const Vec3 l0 = place_point(cv, tile, lifted, 720, 570);
    CHECK(near(elevation(l0), elevation(c0) + pi / 4, 1e-5f) && near(azimuth(l0), a0, 1e-5f) && near(len(l0), len(c0), 1e-6f));
    Placement overhead; overhead.y = half_pi - elevation(c0);  // straight up: still facing you (down at you), still level
    const Vec3 o0 = place_point(cv, tile, overhead, 720, 570);
    CHECK(near(o0.x, 0, 1e-5f) && near(o0.z, 0, 1e-5f) && near(o0.y, len(c0), 1e-5f));
    Placement both{-0.7f, 0.3f, 1, 1.f};  // the orbit is a rigid rotation about the eye of the whole (curved) desktop
    const Placement unmoved{0, 0, 1, 1.f};
    for (float px : {240.f, 720.f, 1200.f}) {
        const Vec3 a = place_point(cv, tile, unmoved, px, 300), b = place_point(cv, tile, both, px, 300);
        CHECK(near(len(a), len(b), 1e-5f));
        CHECK(near(dist(a, place_point(cv, tile, unmoved, 1200 - (px - 240), 840)), dist(b, place_point(cv, tile, both, 1200 - (px - 240), 840)), 1e-5f));
    }
    // Distance: twice as far along the same direction, same physical size -> half the apparent size.
    Placement far; far.distance = 2;
    const Vec3 f0 = place_point(cv, tile, far, 720, 570);
    CHECK(close3(f0, Vec3{2 * c0.x, 2 * c0.y, -2}));
    CHECK(near(dist(place_point(cv, tile, far, 240, 300), place_point(cv, tile, far, 1200, 840)), tile_diagonal, 1e-5f));
    const Vec3 g0 = place_point(cv, centred, far, 960, 600), gr = place_point(cv, centred, far, 1440, 600);
    CHECK(close3(g0, Vec3{0, 0, -2}) && near(gr.z, -2, 1e-6f));
    CHECK(near((gr.x / -gr.z) - (g0.x / -g0.z), 0.5f * (plane(1440, 600).x - plane(960, 600).x), 1e-6f));
    // Zooming into a desktop fades its placement to flat-in-front, keeping curvature; neighbours unaffected.
    CHECK(near(zoom_weight(1, 1, 1), 1, 1e-6f) && near(zoom_weight(1, 1, 0), 0, 1e-6f) && near(zoom_weight(0, 1, 1), 0, 1e-6f));
    CHECK(near(zoom_weight(1, 1.25f, 1), 0.75f, 1e-6f) && near(zoom_weight(0.5f, 1, 1), 0.5f, 1e-6f));
    Placement mixed{0.4f, -0.2f, 1.6f, 1.f};
    const Placement gone = faded(mixed, 1), half = faded(mixed, 0.5f), kept = faded(mixed, 0);
    CHECK(gone.x == 0 && gone.y == 0 && gone.distance == 1 && gone.curvature == 1.f);
    CHECK(near(half.x, 0.2f, 1e-6f) && near(half.y, -0.1f, 1e-6f) && near(half.distance, 1.3f, 1e-6f) && half.curvature == 1.f);
    CHECK(same_placement(kept, mixed));
    // Drawn while zooming (regression, 2026-11-02: zooming in from the overview and back out must keep every desktop where
    // it was dragged, in every viewing mode; zoomed_placements takes no mode). However far a desktop was dragged (beside
    // you, overhead, behind you), at every zoom: every desktop keeps its overview direction round you; the zoomed one comes
    // to distance 1 there (filling the view, curvature kept); every other one is drawn exactly as in the overview. Nothing
    // goes back to its default slot or snaps straight ahead.
    {
        const auto ov = overview_layout(eq, 1920, 1200, 40);
        auto centre_of = [&](const FRect& t, const Placement& p) { return place_point(cv, t, p, t.x + t.width / 2, t.y + t.height / 2); };
        const Placement other{-0.4f, 0.1f, 1.3f, 0.5f};
        {
            const std::vector<Placement> own{other, Placement{0.5f, 0.2f, 1.6f, 1.f}, Placement{}};
            CHECK(zoomed_placements(cv, ov, ov, own, 0, 1).size() == 3);
            for (std::size_t i = 0; i < 3; ++i) {
                CHECK(same_placement(zoomed_placements(cv, ov, ov, own, 0, 1)[i], own[i]));      // overview: unchanged
                CHECK(same_placement(zoomed_placements(cv, ov, ov, own, -1, 0.4f)[i], own[i]));  // clamped
            }
            CHECK(zoomed_placements(cv, ov, ov, std::span<const Placement>(own).first(1), 1, 1).size() == 3);  // short list
        }
        // Dragged near ahead, beside you (past the old 60 deg cutoff), overhead, behind you, and to the far side; with the
        // undragged default in the mix (it must not move either: the regression that reset the others to their slots).
        const Placement drags[] = {{0.5f, 0.2f, 1.6f, 1.f}, {1.2f, 0.3f, 1.6f, 1.f}, {0.2f, 1.3f, 1.f, 0.f},
                                   {3.0f, 0.f, 1.f, 0.f}, {-1.4f, -0.5f, 0.5f, 0.3f}, Placement{}};
        for (std::size_t target = 0; target < 3; ++target)
            for (const Placement& drag : drags) {
                std::vector<Placement> own{other, Placement{0.3f, -0.2f, 0.5f, 0.f}, Placement{-0.9f, 0.4f, 1.2f, 0.f}};
                own[target] = drag;
                for (float z : {0.f, 0.25f, 0.5f, 0.75f, 1.f}) {
                    const auto tl = blended_layout(eq, 1920, 1200, 40, z, float(target));
                    const auto pl = zoomed_placements(cv, ov, tl, own, z, float(target));
                    for (std::size_t i = 0; i < 3; ++i) {
                        const Vec3 was = centre_of(ov[i], own[i]), now = centre_of(tl[i], pl[i]);
                        CHECK(near(std::remainder(azimuth(now) - azimuth(was), 2 * pi), 0, 1e-4f) || near(std::fabs(now.y), len(now), 1e-4f));
                        CHECK(near(elevation(now), elevation(was), 1e-4f));  // never moves round you
                        CHECK(pl[i].curvature == own[i].curvature);
                        check_facing(tl[i], pl[i]);
                        if (i == target) continue;
                        // Not zoomed: seen exactly as in the overview (every corner's direction from the eye, so size and
                        // curvature too); only scaled about the eye, so it is never nearer than it was.
                        for (const auto& [u, v] : {std::pair{0.f, 0.f}, std::pair{1.f, 0.f}, std::pair{0.5f, 1.f}, std::pair{1.f, 1.f}})
                            CHECK(close3(unit(place_point(cv, tl[i], pl[i], tl[i].x + u * tl[i].width, tl[i].y + v * tl[i].height)),
                                         unit(place_point(cv, ov[i], own[i], ov[i].x + u * ov[i].width, ov[i].y + v * ov[i].height)), 1e-4f));
                        CHECK(len(now) >= len(was) * (1 - 1e-5f));
                    }
                }
                // Full zoom: the zoomed one is at distance 1 where it was dragged (not straight ahead unless it was), the
                // full view's size; zooming back out gives every desktop its own placement back.
                const auto zl = zoomed_layout(eq, 1920, 1200, 40, float(target));
                const auto in = zoomed_placements(cv, ov, zl, own, 1, float(target));
                CHECK(in[target].distance == 1.f && near(len(centre_of(zl[target], in[target])), 1, 1e-5f));
                if (!same_placement(drag, Placement{}) || target != 1)
                    CHECK(!close3(centre_of(zl[target], in[target]), Vec3{0, 0, -1}, 1e-2f));
                Placement flat = in[target]; flat.curvature = 0;
                const FRect& t = zl[target];
                CHECK(near(dist(place_point(cv, t, flat, t.x, t.y), place_point(cv, t, flat, t.x + t.width, t.y + t.height)),
                           dist(plane(t.x, t.y), plane(t.x + t.width, t.y + t.height)), 1e-5f));
                const auto out = zoomed_placements(cv, ov, ov, own, 0, float(target));
                for (std::size_t i = 0; i < 3; ++i) CHECK(same_placement(out[i], own[i]));
            }
        // Zoomed in, the zoomed desktop is drawn on top of one dragged nearer; the overview keeps back-to-front order.
        const std::vector<std::size_t> back_to_front{2, 1, 0};
        CHECK(zoom_draw_order(back_to_front, 1, 1) == (std::vector<std::size_t>{2, 0, 1}));
        CHECK(zoom_draw_order(back_to_front, 0.5f, 2) == (std::vector<std::size_t>{1, 0, 2}));
        CHECK(zoom_draw_order(back_to_front, 0.49f, 1) == back_to_front && zoom_draw_order(back_to_front, 0, 2) == back_to_front);
        CHECK(zoom_draw_order(back_to_front, 1, 7) == back_to_front);
    }
    // Anchor mode keeps a resized desktop's size (regression, 2026-11-03: a desktop resized bigger than the zoom-fit size
    // shrank back to it on zoom-in). keep_size: a zoomed desktop seen bigger than the fit is seen exactly as in the overview
    // at every zoom (each corner's direction from the eye), like every other desktop. Without it (follow / locked) it fits.
    // Zooming in must still zoom (regression, 2026-11-03: keeping every size made overview -> zoom a no-op in anchor mode):
    // a desktop at its default size, or resized but still smaller than the fit, grows to fill the view (distance 1).
    {
        const auto ov = overview_layout(eq, 1920, 1200, 40);
        auto width_deg = [&](const FRect& t, const Placement& p) {
            const float cy = t.y + t.height / 2;
            const Vec3 l = unit(place_point(cv, t, p, t.x, cy)), r = unit(place_point(cv, t, p, t.x + t.width, cy));
            return std::acos(std::clamp(dot(l, r), -1.f, 1.f)) * 180.f / pi;
        };
        for (std::size_t target = 0; target < 3; ++target)
            for (const float d : {0.2f, 0.25f, 0.5f, 1.f, 1.6f}) {  // bigger than the fit (0.2, 0.25); smaller (0.5, 1, 1.6)
                const bool bigger = d < 0.3f;
                std::vector<Placement> own{Placement{-0.4f, 0.1f, 1.3f, 0.f}, Placement{}, Placement{0.3f, -0.1f, 0.8f, 0.f}};
                own[target] = Placement{0.2f, 0.1f, d, 0.f};
                for (float z : {0.f, 0.5f, 1.f}) {
                    const auto tl = blended_layout(eq, 1920, 1200, 40, z, float(target));
                    const auto kept = zoomed_placements(cv, ov, tl, own, z, float(target), true);
                    for (std::size_t i = 0; i < 3; ++i) {
                        if (i == target && !bigger && z > 0) continue;  // it grows (below)
                        for (const auto& [u, v] : {std::pair{0.f, 0.f}, std::pair{1.f, 0.f}, std::pair{0.5f, 0.5f}, std::pair{1.f, 1.f}})
                            CHECK(close3(unit(place_point(cv, tl[i], kept[i], tl[i].x + u * tl[i].width, tl[i].y + v * tl[i].height)),
                                         unit(place_point(cv, ov[i], own[i], ov[i].x + u * ov[i].width, ov[i].y + v * ov[i].height)), 1e-4f));
                    }
                }
                const auto zl = zoomed_layout(eq, 1920, 1200, 40, float(target));
                const auto kept = zoomed_placements(cv, ov, zl, own, 1, float(target), true);
                const auto fit = zoomed_placements(cv, ov, zl, own, 1, float(target));
                const float was = width_deg(ov[target], own[target]);
                CHECK(fit[target].distance == 1.f);
                if (bigger) {
                    CHECK(kept[target].distance < 1.f && near(width_deg(zl[target], kept[target]), was, 1e-2f));
                    CHECK(width_deg(zl[target], fit[target]) < was - 1.f);  // fitting would have shrunk it
                } else {
                    CHECK(same_placement(kept[target], fit[target]));  // zoomed in, it fills the view
                    CHECK(width_deg(zl[target], kept[target]) > was + 1.f);  // it grew: the zoom zooms
                }
                // Halfway in, it is on its way: never smaller than in the overview, never past the fit.
                const auto hl = blended_layout(eq, 1920, 1200, 40, 0.5f, float(target));
                const auto half = zoomed_placements(cv, ov, hl, own, 0.5f, float(target), true);
                const float h = width_deg(hl[target], half[target]);
                CHECK(h > was - 1e-2f && (bigger || h < width_deg(zl[target], kept[target]) + 1e-2f));
                for (std::size_t i = 0; i < 3; ++i) {
                    if (i == target) continue;
                    CHECK(same_placement(kept[i], fit[i]));  // the others are the same in every mode
                }
            }
    }
    // The switch's view turn (look_turn) is a camera rotation only: identity at rest, a proper rotation, and at look 1 the
    // target's direction (slot plus where it was dragged) comes exactly where the desktop being left was, upright.
    {
        const auto ov = overview_layout(eq, 1920, 1200, 40);
        auto centre_of = [&](const FRect& t, const Placement& p) { return place_point(cv, t, p, t.x + t.width / 2, t.y + t.height / 2); };
        auto mul = [](const std::array<float, 9>& m, Vec3 p) {
            return Vec3{m[0] * p.x + m[1] * p.y + m[2] * p.z, m[3] * p.x + m[4] * p.y + m[5] * p.z, m[6] * p.x + m[7] * p.y + m[8] * p.z};
        };
        const std::array<float, 9> identity{1, 0, 0, 0, 1, 0, 0, 0, 1};
        const std::vector<Placement> own{Placement{-0.4f, 0.1f, 1.3f, 0.5f}, Placement{0.2f, -0.1f, 1.f, 0.f}, Placement{1.2f, 0.3f, 1.6f, 1.f}};
        CHECK(look_turn(cv, ov, own, 1, 2, 0) == identity && look_turn(cv, ov, own, 1, 7, 1) == identity);
        CHECK(look_turn(Canvas{}, ov, own, 1, 2, 1) == identity);
        for (const auto& [from, to] : {std::pair{1u, 2u}, std::pair{2u, 0u}, std::pair{0u, 1u}})
            for (float u : {0.25f, 0.5f, 1.f}) {
                const auto m = look_turn(cv, ov, own, from, to, u);
                const Vec3 ex = mul(m, {1, 0, 0}), ey = mul(m, {0, 1, 0}), ez = mul(m, {0, 0, 1});
                CHECK(near(len(ex), 1, 1e-5f) && near(len(ey), 1, 1e-5f) && near(dot(ex, ey), 0, 1e-5f) && near(dot(ex, ez), 0, 1e-5f));
                CHECK(close3(cross(ex, ey), ez, 1e-5f));  // no mirror
                if (u < 1) continue;
                const Vec3 a = unit(centre_of(ov[from], own[from])), b = unit(centre_of(ov[to], own[to]));
                CHECK(close3(mul(m, b), a, 1e-4f));
                // Upright: the target's up (toward the zenith along its meridian) lands on the left desktop's up.
                auto up = [&](Vec3 d) { return unit(sub(Vec3{0, 1, 0}, Vec3{d.x * d.y, d.y * d.y, d.z * d.y})); };
                CHECK(close3(mul(m, up(b)), up(a), 1e-4f));
            }
        // Every frame of a switch draws every desktop where it was dragged (the turn is the camera's, never a placement):
        // the placements the renderer resolves keep each desktop's overview direction and land back on its own placement.
        ViewAnimator v(220, 300);
        v.set_target(true, 0, 40, 0); v.advance(1000); v.set_target(true, 2, 40, 1000);
        for (double t = 1000; t <= 1750; t += 25) {
            v.advance(t);
            const auto tl = blended_layout(eq, 1920, 1200, 40, v.zoom(), v.position());
            const auto pl = zoomed_placements(cv, ov, tl, own, v.zoom(), v.position());
            for (std::size_t i = 0; i < 3; ++i) {
                const Vec3 was = unit(centre_of(ov[i], own[i])), now = unit(centre_of(tl[i], pl[i]));
                CHECK(close3(was, now, 1e-4f));
            }
            if (v.zoom() == 0) for (std::size_t i = 0; i < 3; ++i) CHECK(same_placement(pl[i], own[i]));
        }
        CHECK(!v.switching() && look_turn(cv, ov, own, v.look_from(), v.look_to(), v.look()) == identity);
    }
    // The orbit's x eases the short way round: a desktop crossing behind you (+-pi) stays behind you.
    Placement from_behind; from_behind.x = 3.1f;
    Placement to_behind; to_behind.x = -3.1f;
    const Placement mid = ease_toward(from_behind, to_behind, kPlacementEaseMs, kPlacementEaseMs);
    CHECK(mid.x < -3.1f && mid.x > -3.1f - 0.1f);  // past -pi from below: the 0.08 rad gap, not the 6.2 rad way round
    CHECK(place_point(cv, tile, mid, 720, 570).z > 0);
    CHECK(ease_toward(from_behind, to_behind, 100 * kPlacementEaseMs, kPlacementEaseMs).x == -3.1f);  // lands exactly
    Placement near_front; near_front.x = 0.3f;
    CHECK(near(ease_toward(near_front, {}, kPlacementEaseMs, kPlacementEaseMs).x, 0.3f * std::exp(-1.f), 1e-6f));  // as before
    {  // Every field eases (and lands); same_placement compares all four.
        const Placement to{-0.4f, 0.2f, 1.5f, 0.8f};
        const Placement e = ease_toward({}, to, kPlacementEaseMs, kPlacementEaseMs);
        const float k = 1 - std::exp(-1.f);
        CHECK(near(e.x, -0.4f * k, 1e-6f) && near(e.y, 0.2f * k, 1e-6f) && near(e.distance, 1 + 0.5f * k, 1e-6f) && near(e.curvature, 0.8f * k, 1e-6f));
        CHECK(same_placement(ease_toward(e, to, 1e9f, kPlacementEaseMs), to));
        for (int f = 0; f < 4; ++f) {
            Placement other = to;
            (f == 0 ? other.x : f == 1 ? other.y : f == 2 ? other.distance : other.curvature) += 0.01f;
            CHECK(!same_placement(other, to));
        }
    }
    {  // A glide (bumping, view.py GLIDE_MS): one smootherstep curve from where a desktop was drawn to its new placement.
        const Placement from{-0.2f, 0.1f, 1.f, 0.f, 0.f}, to{0.4f, -0.3f, 0.5f, 1.f, 0.1f};
        CHECK(same_placement(glide_toward(from, to, 0), from));  // starts where it was drawn: no jump
        CHECK(same_placement(glide_toward(from, to, 1), to) && same_placement(glide_toward(from, to, 7), to));  // lands exactly
        CHECK(same_placement(glide_toward(from, to, -1), from));
        const Placement half = glide_toward(from, to, 0.5f);
        CHECK(near(half.x, 0.1f, 1e-6f) && near(half.y, -0.1f, 1e-6f) && near(half.distance, 0.75f, 1e-6f) &&
              near(half.curvature, 0.5f, 1e-6f) && near(half.roll, 0.05f, 1e-6f));  // halfway at half time
        float last = from.x;
        for (int i = 1; i <= 100; ++i) {  // every field moves one way only, never past the end (no overshoot)
            const Placement p = glide_toward(from, to, i / 100.f);
            CHECK(p.x >= last - 1e-7f && p.x <= to.x + 1e-7f && p.y <= from.y + 1e-7f && p.y >= to.y - 1e-7f);
            last = p.x;
        }
        // Eased in and out: it starts and lands with no jump in speed (smootherstep's slope is 0 at both ends).
        CHECK(glide_toward(from, to, 0.01f).x - from.x < 0.01f * (to.x - from.x) * 0.01f);
        CHECK(to.x - glide_toward(from, to, 0.99f).x < 0.01f * (to.x - from.x) * 0.01f);
        // Every desktop of the frame takes the same t, so the pair's configuration moves along one straight line between
        // two that were apart: a pushed desktop and the one pushing it slide out of each other's way together.
        const Placement a0{0, 0, 1, 0}, a1{0.3f, 0, 1, 0}, b0{0.25f, 0, 1, 0}, b1{0.55f, 0, 1, 0};
        for (int i = 0; i <= 10; ++i) CHECK(glide_toward(b0, b1, i / 10.f).x - glide_toward(a0, a1, i / 10.f).x >= 0.25f - 1e-6f);
        // The short way round, as ease_toward: behind you it stays behind you, and lands on the exact value.
        Placement behind_a; behind_a.x = 3.1f;
        Placement behind_b; behind_b.x = -3.1f;
        CHECK(glide_toward(behind_a, behind_b, 0.5f).x > 3.1f && glide_toward(behind_a, behind_b, 1).x == -3.1f);
    }
    {  // The renderer's per-frame step (wayland.cpp ease_placements): a glide frame takes every desktop along one curve,
       // over exactly its time, then easing goes on as before; a frame without one eases on from wherever the glide got to.
        PlacementGlide glide;
        std::vector<Placement> drawn{{0, 0, 1, 0}, {0.25f, 0, 1, 0}};
        const std::vector<Placement> pushed{{0.3f, 0, 1, 0}, {0.55f, 0, 1, 0}};  // the mover and the desktop it bumped
        CHECK(!start_glide(glide, {}, 0, 240) && glide.ms == 0);  // nothing drawn yet: nothing to glide from
        CHECK(!start_glide(glide, drawn, 1000, 0) && glide.ms == 0);
        CHECK(start_glide(glide, drawn, 1000, 240) && glide.ms == 240);
        step_placements(drawn, pushed, 8, 1120, glide);  // half the time: half the way, both together
        CHECK(near(drawn[0].x, 0.15f, 1e-5f) && near(drawn[1].x, 0.4f, 1e-5f) && glide.ms == 240);
        step_placements(drawn, pushed, 8, 1008, glide);  // early on it has barely started: eased in, not a jump
        CHECK(drawn[0].x < 0.3f * 0.01f);
        step_placements(drawn, pushed, 8, 1240, glide);  // its time is up: exactly there, and the glide is over
        CHECK(same_placement(drawn[0], pushed[0]) && same_placement(drawn[1], pushed[1]) && glide.ms == 0);
        const std::vector<Placement> next{{0.4f, 0, 1, 0}, {0.65f, 0, 1, 0}};
        step_placements(drawn, next, kPlacementEaseMs, 1300, glide);  // no glide: the quick exponential easing again
        CHECK(near(drawn[0].x, 0.4f - 0.1f * std::exp(-1.f), 1e-5f));
        CHECK(start_glide(glide, drawn, 2000, 240));  // a glide that a plain frame interrupts: eased on from where it is
        step_placements(drawn, next, 8, 2060, glide);
        const Placement at = drawn[0];
        glide.ms = 0;  // (set_placements without a glide, a roll change or a desktop change end it)
        step_placements(drawn, next, 8, 2068, glide);
        CHECK(drawn[0].x >= at.x && drawn[0].x <= next[0].x);  // on toward the target, from there: no jump back
        std::vector<Placement> grown{{0.4f, 0, 1, 0}};
        step_placements(grown, next, 8, 2100, glide);  // a desktop added meanwhile starts at its target
        CHECK(grown.size() == 2 && same_placement(grown[1], next[1]));
    }
    // Draw order: farthest first; equal-distance ties keep layout order; missing placements are flat.
    const FRect row3[] = {{0, 330, 600, 540}, {660, 330, 600, 540}, {1320, 330, 600, 540}};
    const Placement pushed[] = {{}, {0, 0, 1.8f, 0}};
    auto order = draw_order(cv, row3, pushed);
    CHECK(order.size() == 3 && order[0] == 1);
    const FRect same[] = {{660, 330, 600, 540}, {660, 330, 600, 540}};
    auto tied = draw_order(cv, same, std::span<const Placement>{});
    CHECK(tied[0] == 0 && tied[1] == 1);
    // Degenerate input yields nothing rather than NaNs.
    CHECK(tile_mesh(cv, {0, 0, 0, 10}, {}, tile).empty() && tile_mesh(cv, tile, {}, {0, 0, 0, 0}).empty());
    for (const auto& m : tile_mesh(cv, tile, Placement{0, 0, 1, float(M_PI) * 2 / 3}, {190, 250, 1060, 640}))
        CHECK(std::isfinite(m.world.x) && std::isfinite(m.world.y) && std::isfinite(m.world.z) && m.world.z < 0);
    CHECK(close3(place_point(cv, centred, Placement{0, 0, 0, 0}, 480, 330), plane(480, 330), 1e-6f));  // distance <= 0 reads as 1
    {  // Huge: the distance floor (wire 10, factor 0.1) shows a desktop ten times its size, far past the view.
        const Placement huge{0, 0, 0.1f, 0};
        auto seen = [](Vec3 a) { return std::pair{a.x / -a.z, a.y / -a.z}; };  // on the canvas plane, as projected
        for (const auto& [px, py] : {std::pair{480.f, 330.f}, std::pair{1440.f, 870.f}, std::pair{960.f, 600.f}}) {
            const auto [x, y] = seen(place_point(cv, centred, huge, px, py));
            const Vec3 flat = plane(px, py);
            CHECK(near(x, 10 * flat.x, 1e-4f) && near(y, 10 * flat.y, 1e-4f));
        }
        CHECK(near(seen(place_point(cv, centred, huge, 1440, 870)).first, 2.5f, 1e-4f));  // its right edge: 5x the view's
        check_facing(centred, huge);
        check_facing(tile, Placement{0.4f, -0.2f, 0.1f, 0});
        check_facing(tile, Placement{-2.f, 0.3f, 0.1f, float(M_PI) / 3});
        // The curved, closest desktop wraps round the eye (its edges may pass behind you: GL clips them); the mesh stays
        // finite and dense enough, and its centre stays straight ahead.
        const auto mesh = tile_mesh(cv, tile, Placement{0, 0, 0.1f, float(M_PI) * 2 / 3}, {190, 250, 1060, 640});
        CHECK(mesh.size() >= 10);
        for (const auto& m : mesh) CHECK(std::isfinite(m.world.x) && std::isfinite(m.world.y) && std::isfinite(m.world.z));
        CHECK(place_point(cv, centred, Placement{0, 0, 0.1f, float(M_PI) * 2 / 3}, 960, 600).z < 0);
        // The nearest is drawn last (over the others); zooming in still brings it to distance 1.
        const Placement mixed[] = {{}, huge, {0, 0, 2, 0}};
        const auto o = draw_order(cv, row3, mixed);
        CHECK(o.size() == 3 && o[0] == 2 && o[2] == 1);
        CHECK(near(faded(huge, 1).distance, 1, 1e-6f) && near(faded(huge, 0).distance, 0.1f, 1e-6f));
    }
    }
    {  // A desktop changing shape live (SizeEase): the tile eases with the same smoothstep over the same time as zoom and
       // spacing, and the layout built from it slides the neighbours along.
        CHECK(kViewEaseMs == 220.f);
        ViewAnimator v;  // zoom goes the same way over the same time
        v.set_target(true, 0, 40, 1000); v.advance(1000);
        const float d = kViewEaseMs;
        const Size fhd{1920, 1080}, uw{2560, 1080}, uhd{3840, 2160};
        SizeEase e;
        CHECK(!e.value() && !e.animating() && e.eases() == 0);
        e.advance(fhd, 1000, false);  // its first size just applies
        CHECK(e.value()->w == 1920 && e.value()->h == 1080 && !e.animating());
        e.advance(fhd, 1100, true);   // no change: nothing to do
        CHECK(!e.animating() && e.eases() == 0);
        e.advance(uhd, 1200, true);   // 16:9 to 16:9: same shape, nothing moves
        CHECK(e.value()->w == 3840 && e.value()->h == 2160 && !e.animating() && e.eases() == 0);
        e.advance(fhd, 1300, true);
        CHECK(!e.animating() && e.eases() == 0);
        e.advance(uw, 2000, true);    // to 21:9: starts from where it is
        CHECK(e.animating() && e.eases() == 1 && near(e.value()->w, 1920, 1e-3f) && e.value()->h == 1080);
        const auto shape_at = [&](double t) { e.advance(uw, t, true); return e.value()->w / e.value()->h; };
        const float a0 = 1920.f / 1080, a1 = 2560.f / 1080;
        float last = a0;
        for (double t = 2010; t < 2000 + d; t += 10) {
            const float a = shape_at(t);
            CHECK(a > last && a < a1);  // always on its way, never past it
            CHECK(near(a, a0 + (a1 - a0) * smoothstep(float((t - 2000) / d)), 1e-4f));
            v.advance(1000 + (t - 2000));
            CHECK(near((a - a0) / (a1 - a0), v.zoom(), 1e-3f));  // in step with a zoom started at the same moment
            last = a;
        }
        shape_at(2000 + d);
        CHECK(!e.animating() && e.value()->w == 2560 && e.value()->h == 1080);  // lands exactly on the size
        // A change mid-way carries on from what is drawn, with no jump.
        e.advance(fhd, 3000, true);
        e.advance(fhd, 3000 + d / 2, true);
        const float mid = e.value()->w / e.value()->h;
        CHECK(near(mid, (a0 + a1) / 2, 1e-4f));
        e.advance(uw, 3000 + d / 2, true);
        CHECK(e.animating() && e.eases() == 3 && near(e.value()->w / e.value()->h, mid, 1e-4f));
        e.advance(uw, 3000 + 2 * d, true);
        CHECK(!e.animating() && e.value()->w == 2560);
        // animate false: the new shape applies at once. A zero duration never eases.
        e.advance(fhd, 4000, false);
        CHECK(!e.animating() && e.value()->w == 1920 && e.eases() == 3);
        SizeEase none(0);
        none.advance(fhd, 0, true); none.advance(uw, 1, true);
        CHECK(!none.animating() && none.value()->w == 2560);
        // The middle desktop widening: the row is laid out from the eased sizes, so every drawn frame lies between the two
        // layouts: the tile widens, the right neighbour slides right, and (the row being limited by the view's width) all
        // three share a lower height, each step the eased share of the whole move.
        SizeEase m;
        m.advance(fhd, 0, false);
        const auto layout = [&](Size mid_size) {
            const Size row3[] = {fhd, mid_size, fhd};
            return overview_layout(row3, 1920, 1200, 40);
        };
        const auto a = layout(fhd), b = layout(uw);
        CHECK(b[1].width > a[1].width && b[2].x > a[2].x && b[0].height < a[0].height);
        for (double t = 0; t <= d; t += 22) {
            m.advance(uw, t, true);
            const auto now = layout(*m.value());
            const float ws = (now[1].width / now[1].height - a[1].width / a[1].height) /
                             (b[1].width / b[1].height - a[1].width / a[1].height);
            CHECK(ws >= -1e-4f && ws <= 1 + 1e-4f);
            for (std::size_t i = 0; i < 3; ++i) {
                CHECK(now[i].x >= std::min(a[i].x, b[i].x) - 0.5f && now[i].x <= std::max(a[i].x, b[i].x) + 0.5f);
                CHECK(now[i].width >= std::min(a[i].width, b[i].width) - 0.5f && now[i].width <= std::max(a[i].width, b[i].width) + 0.5f);
            }
            if (t + 22 <= d) CHECK(near(ws, smoothstep(float(t / d)), 1e-3f));
        }
    }
    std::cout << "view layout passed\n";
}
