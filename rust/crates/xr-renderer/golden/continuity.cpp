// Continuity blobs from the C++ ViewContinuity (spike/include/xr/continuity.hpp), and the check that a Rust one restores.
// From the repository root, with BUILD a CMake build of spike/ that has built continuity_test:
//   g++ -std=c++20 -Ispike/include rust/crates/xr-renderer/golden/continuity.cpp BUILD/libxr_pose.a BUILD/libxr_view.a BUILD/libxr_luma_rt.a -o /tmp/continuity-golden
//   /tmp/continuity-golden write rust/crates/xr-renderer/golden          # writes continuity_mid.bin, continuity_end.bin
//   /tmp/continuity-golden read rust/crates/xr-renderer/golden           # restores continuity_{mid,end}_rust.bin
// tests/continuity.rs decodes the C++ blobs, and writes the *_rust.bin blobs (XR_WRITE_GOLDENS=1) and keeps them current.
// The scenario is continuity_test.cpp's, one snapshot mid edge pan and one at the end.
#include "xr/continuity.hpp"
#include <cstdio>
#include <fstream>
#include <iterator>
#include <sstream>
using namespace xr;

static void mid(ViewContinuity& a) {
    a.pose.update(FeedSample{Quat{}, 1}, 100);
    a.pose.update(FeedSample{aim_rotation(.4f, .1f), 2}, 120);
    a.modes.turn_anchor(aim_rotation(.2f, .05f));
    a.modes.update(a.pose.relative(), 120);
    a.modes.set_mode(ViewMode::follow, 130, ModeController::Change::maximize);
    a.maximized = true;
    a.view.set_target(true, 1, 70, 100);
    a.view.advance(240);
    a.rolls = {1250, -650, 20, 0};
    a.pose.recenter();
    const FeedSample head{aim_rotation(.7f, .2f), 3};
    a.pose.update(head, 250);
    a.modes.land_maximize(240);
    for (double t = 240; t <= 400; t += 8) a.modes.update(aim_rotation(.8f, .1f), t);
    a.view = ViewAnimator{};
    a.view.set_target(true, 0, 40, 0); a.view.advance(1000);
    EdgePan pan; pan.active = true; pan.from = 0; pan.to = 1; pan.side = 3;
    pan.exit = pan.entry = .3f; pan.peek = .7f; pan.cross = .3f;
    pan.turn = .4f; pan.way = 2.f; pan.zoom = .2f;
    a.view.set_pan(pan, 1000); a.view.advance(1020);
    a.last_drawn = {0, 1, 0, -1, 0, 0, 0, 0, 1};
    a.stabilization.strength(75);
    for (int i = 0; i < 50; ++i) a.stabilization.update(aim_rotation(float(i % 2) * .0001f, 0), 970 + i);
    a.saved_ms = 1020;
}

static void end(ViewContinuity& a) {
    mid(a);
    a.view.set_target(true, 1, 40, 1030, false, false, true);
    for (double t = 1030; t < 2200; t += 8) a.view.advance(t);
    a.view.set_target(true, 2, 40, 2200, true);
    a.view.advance(4000);
    a.view.set_target(false, 3, 55, 4000, true);
    a.view.advance(4100);  // a zoom under way, with a landed turn still queued
    a.modes.set_mode(ViewMode::anchor, 4000, ModeController::Change::restore);
    a.modes.update(aim_rotation(.9f, .1f), 4100);
    a.centre_valid = true; a.centre_serial = a.view.serial(); a.centre_dir = {.2f, .3f};
    a.saved_ms = 4100;
}

static std::string slurp(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), {}};
}

static bool same(Quat a, Quat b) { return a.x == b.x && a.y == b.y && a.z == b.z && a.w == b.w; }
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "%s: %s failed\n", name, #x); return false; } } while (0)

// b, restored from the Rust blob, carries on exactly as a does.
static bool restores(const char* name, ViewContinuity& a, const std::string& blob) {
    ViewContinuity b;
    CHECK(b.decode(blob));
    CHECK(b.maximized == a.maximized && b.rolls == a.rolls && b.last_drawn == a.last_drawn);
    CHECK(b.centre_valid == a.centre_valid && b.centre_serial == a.centre_serial && b.centre_dir == a.centre_dir);
    CHECK(b.saved_ms == a.saved_ms);
    CHECK(b.modes.holding() == a.modes.holding() && same(b.modes.anchor(), a.modes.anchor()));
    CHECK(b.pose.view() == a.pose.view() && bool(b.pose.reference()) == bool(a.pose.reference()));
    CHECK(!a.pose.reference() || same(*a.pose.reference(), *b.pose.reference()));
    const double t0 = a.saved_ms;
    for (int i = 0; i < 50; ++i) {
        const auto q = aim_rotation(float(i % 3) * .0001f, .002f);
        CHECK(same(a.stabilization.update(q, t0 + i), b.stabilization.update(q, t0 + i)));
        CHECK(a.stabilization.prediction_gain() == b.stabilization.prediction_gain());
    }
    for (int i = 1; i <= 40; ++i) {
        const FeedSample s{aim_rotation(.7f + i * .01f, .2f), float(10 + i)};
        a.pose.update(s, t0 + i * 8); b.pose.update(s, t0 + i * 8);
        CHECK(a.pose.view() == b.pose.view());
        CHECK(same(a.modes.update(a.pose.relative(), t0 + i * 8), b.modes.update(b.pose.relative(), t0 + i * 8)));
    }
    a.view.set_target(true, 1, 40, t0 + 10, false, false, true);
    b.view.set_target(true, 1, 40, t0 + 10, false, false, true);
    for (double t = t0 + 10; t < t0 + 1500; t += 8) {
        a.view.advance(t); b.view.advance(t);
        CHECK(a.view.zoom() == b.view.zoom() && a.view.turn() == b.view.turn() && a.view.spacing() == b.view.spacing());
        CHECK(a.view.pan_cross() == b.view.pan_cross() && a.view.pan_turn() == b.view.pan_turn());
        CHECK(a.view.pan_active() == b.view.pan_active() && a.view.focus() == b.view.focus());
    }
    const auto x = a.view.take_landed_turns(), y = b.view.take_landed_turns();
    CHECK(x.size() == y.size() && !x.empty());
    for (std::size_t i = 0; i < x.size(); ++i)
        CHECK(x[i].from == y[i].from && x[i].to == y[i].to && x[i].centred == y[i].centred && x[i].serial == y[i].serial);
    std::printf("%s: Rust blob restored, %zu bytes, same continuation\n", name, blob.size());
    return true;
}

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    const std::string mode = argv[1], dir = argv[2];
    ViewContinuity m, e;
    mid(m); end(e);
    if (mode == "write") {
        for (auto [name, c] : {std::pair{"mid", &m}, std::pair{"end", &e}}) {
            const auto bytes = c->encode();
            std::ofstream(dir + "/continuity_" + name + ".bin", std::ios::binary) << bytes;
            std::printf("continuity_%s.bin: %zu bytes\n", name, bytes.size());
        }
        return 0;
    }
    if (mode == "read")
        return restores("mid", m, slurp(dir + "/continuity_mid_rust.bin")) && restores("end", e, slurp(dir + "/continuity_end_rust.bin")) ? 0 : 1;
    return 2;
}
