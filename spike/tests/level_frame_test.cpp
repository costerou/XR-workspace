// What "level" is measured against. XRLinuxDriver publishes the head's pose relative to the pose it captured as calibration
// finished (src/driver.c driver_handle_pose: feed = conj(reference) * pose), wherever the glasses were then. So the feed's z
// axis is the glasses' up at that moment, not gravity: PoseState::reference has to give the level bar (pose.hpp canvas_up,
// view.hpp desktop_tilt) and the background's floor (environment_view) a frame that is level anyway.
//
// `--dump` prints what the renderer would report for each scenario, for level_contract_test.py to press the real bar with.
#include "xr/environment.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
constexpr float kPi = 3.14159265358979f;
float rad(float deg) { return deg * kPi / 180.f; }
float deg(float r) { return r * 180.f / kPi; }
Quat axis_angle(float x, float y, float z, float degrees) {
    const float n = std::sqrt(x * x + y * y + z * z), h = rad(degrees) / 2, s = std::sin(h) / n;
    return {x * s, y * s, z * s, std::cos(h)};
}
// Driver frame: x forward, y left, z up. Pitch +down, tip +right (as worn).
Quat head_at(float yaw, float pitch, float tip) {
    return quat_mul(quat_mul(axis_angle(0, 0, 1, yaw), axis_angle(0, 1, 0, pitch)), axis_angle(1, 0, 0, tip));
}
// Someone at work: turns between desktops and looks a little up and down, the head never quite straight but straight on
// average. Deterministic, so a failure repeats.
Quat working(float seconds) {
    const float t = seconds;
    return head_at(25 * std::sin(0.21f * t) + 12 * std::sin(0.53f * t + 1), -3 + 6 * std::sin(0.33f * t),
                   2.5f * std::sin(0.7f * t) + 1.5f * std::sin(1.9f * t + 1));
}

// The driver: a gravity-aligned pose (the Carina's VIO) comes out relative to the reference it captured.
struct Run {
    Quat driver_reference;
    PoseState pose{};
    double now = 1000;
    float device = 0;
    explicit Run(Quat reference) : driver_reference(reference) {}
    void feed(Quat head) {
        now += 1000.0 / 120; device += 1000.f / 120;
        pose.update(FeedSample{quat_mul(quat_conj(driver_reference), head), device, {}, {}}, now);
    }
    void recentre(Quat head) { pose.recenter(); feed(head); }
    void hold(Quat head, float seconds) { for (float t = 0; t < seconds; t += 1.f / 120) feed(head); }
    void work(float seconds, float from = 0) { for (float t = 0; t < seconds; t += 1.f / 120) feed(working(from + t)); }
};

const Canvas kCanvas{1920, 1200, 0.41421356f, 0.25888f};
const Size kThree[] = {{1920, 1200}, {1920, 1200}, {1920, 1200}};
// Cody's desktops in the run that showed it (profiles.json, 2026-11-03).
const Placement kPlaced[] = {{rad(0.7f), rad(9.0f), 0.33f, 0, 0}, {rad(34.5f), rad(8.5f), 0.31f, 0, 0}, {rad(-5.2f), rad(-15.7f), 0.69f, 0, 0}};

std::vector<float> tilts(const Quat& frame) {  // radians; NaN where nothing is measured
    const auto tiles = overview_layout(kThree, 1920, 1200, 20);
    const auto up = canvas_up(frame, Quat{});
    std::vector<float> out;
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        const auto t = desktop_tilt(kCanvas, tiles[i], kPlaced[i], Vec3{up[0], up[1], up[2]});
        out.push_back(t ? *t : std::nanf(""));
    }
    return out;
}
// The worst error of the tilts the renderer would report against those true gravity gives, in degrees.
float worst_error(const Run& run, Quat true_reference) {
    const auto reference = run.pose.reference();
    if (!reference) return 1e9f;
    const auto got = tilts(*reference), want = tilts(true_reference);
    float worst = 0;
    for (std::size_t i = 0; i < got.size(); ++i) {
        const float d = std::remainder(got[i] - want[i], 2 * kPi);
        worst = std::max(worst, std::isnan(d) ? 1e9f : std::fabs(deg(d)));
    }
    return worst;
}
// The floor: the angle between the world's up as the background draws it and the true one.
float floor_error(const Run& run, Quat head) {
    const auto view = environment_view(run.pose.relative(), run.pose.reference());
    const auto got = apply(view, {0, 1, 0});
    const auto v = apply(transpose(quat_to_mat3(head)), {0, 0, 1});  // true up in the head's driver frame
    const std::array<float, 3> want{-v[1], v[2], -v[0]};  // driver -> GL
    const float cross = got[0] * want[0] + got[1] * want[1] + got[2] * want[2];
    return deg(std::acos(std::clamp(cross, -1.f, 1.f)));
}

// The reference the live feed implied at 2026-11-03 19:45 with the glasses on: feed (0.539, 0.826, 0.163, -0.014) x, y, z, w
// with the head taken as level. Calibration finished with them upside down, 165 degrees from how they were then worn.
const Quat kLive = quat_conj(Quat{0.539f, 0.826f, 0.163f, -0.014f});
struct Case { const char* name; Quat reference; };
const Case kOddReferences[] = {
    {"live", kLive},
    {"upside down on a desk", axis_angle(1, 0, 0, 180)},
    {"lying on one arm", axis_angle(1, 0, 0, 90)},
    {"hanging, lenses down", axis_angle(0, 1, 0, 88)},
    {"hanging, lenses up", axis_angle(0, 1, 0, -70)},
};

void the_feeds_z_is_not_up() {
    // The symptom. The glasses are worn level and the view recentred: every desktop is level, whatever the feed's frame.
    for (const Case& c : kOddReferences) {
        Run run(c.reference);
        const Quat level{};
        run.recentre(level);
        const float error = worst_error(run, level);
        if (!(error < 0.5f)) std::fprintf(stderr, "%s: tilts %.1f degrees wrong\n", c.name, error);
        CHECK(error < 0.5f);
    }
}

void a_recentre_with_the_head_tipped_is_still_found() {
    // The reason the bar exists, and what the driver's frame gave away free when the glasses were worn straight at the
    // start. Tipped or pitched at the recentre, then the head goes back to how it is held: the tilts come to the truth.
    for (const Case& c : kOddReferences)
        for (const float tip : {-20.f, 20.f, 8.f})
            for (const float pitch : {0.f, 12.f, -10.f}) {
                Run run(c.reference);
                const Quat tipped = head_at(5, pitch, tip);
                run.recentre(tipped);
                run.work(45);
                const float error = worst_error(run, tipped);
                if (!(error < 2.5f)) std::fprintf(stderr, "%s tip %.0f pitch %.0f: tilts %.1f degrees wrong\n", c.name, tip, pitch, error);
                CHECK(error < 2.5f);
            }
}

void a_recentre_keeps_what_was_learned() {
    // Up lives in the feed's frame, which a recentre does not move: after a minute of use a tipped recentre reads true at
    // once, however bad the driver's reference, with nothing to wait for.
    for (const Case& c : kOddReferences)
        for (const float tip : {-20.f, 20.f}) {
            Run run(c.reference);
            const Quat level{}, tipped = head_at(-15, 8, tip);
            run.recentre(level);
            run.work(60);
            run.recentre(tipped);
            const float error = worst_error(run, tipped);
            if (!(error < 2.5f)) std::fprintf(stderr, "%s, recentred tipped %.0f: tilts %.1f degrees wrong\n", c.name, tip, error);
            CHECK(error < 2.5f);
        }
    // A recentre further from up than a neck goes means up is not up any more (lying on your side): level as you see it.
    Run run(kLive);
    const Quat level{}, lying = head_at(0, 0, 80);
    run.recentre(level);
    run.work(60);
    run.recentre(lying);
    const auto reference = run.pose.reference();
    CHECK(reference);
    if (reference)
        for (const float t : tilts(*reference)) CHECK(std::fabs(deg(t)) < 2);
}

void glasses_worn_straight_at_the_start_keep_exact_gravity() {
    // Where the driver's frame is good it is used from the first sample, with no time to learn: a level reference under
    // any heading, the head tipped or pitched at the recentre.
    for (const float heading_deg : {0.f, 70.f, -130.f})
        for (const float tip : {-20.f, 0.f, 20.f})
            for (const float pitch : {0.f, 25.f, -20.f}) {
                Run run(axis_angle(0, 0, 1, heading_deg));
                const Quat tipped = head_at(-30, pitch, tip);
                run.recentre(tipped);
                CHECK(worst_error(run, tipped) < 0.05f);
            }
}

void a_reference_that_looks_plausible_is_corrected_in_use() {
    // In a hand at the start, 30 degrees off: near enough to be believed, so wrong at first; straight on average, the head
    // shows it up.
    for (const Quat reference : {axis_angle(1, 0, 0, 30), axis_angle(1, 0, 0, -35), head_at(0, 20, 25)}) {
        Run run(reference);
        const Quat level{};
        run.recentre(level);
        run.work(45);
        const float error = worst_error(run, level);
        if (!(error < 2.5f)) std::fprintf(stderr, "plausible reference: tilts %.1f degrees wrong\n", error);
        CHECK(error < 2.5f);
    }
}

void normal_use_does_not_wander() {
    // Ten minutes. A start on the feed's z holds from the first moment. One on the head alone has only the head to go on
    // and little turning seen yet: up to 6 degrees for its first half minute, then the same.
    for (const Quat reference : {Quat{}, kLive}) {
        Run run(reference);
        const Quat level{};
        run.recentre(level);
        for (int i = 0; i < 40; ++i) {
            run.work(15, float(i) * 15);
            const float error = worst_error(run, level), limit = reference.w == 1 || i >= 2 ? 2.5f : 6.f;
            if (!(error < limit)) std::fprintf(stderr, "after %d s: tilts %.1f degrees wrong\n", (i + 1) * 15, error);
            CHECK(error < limit);
        }
    }
}

void putting_the_glasses_down_teaches_nothing() {
    // Off the head for a minute, in any position, and back on with no recentre: nothing learned from a desk.
    for (const Quat desk : {axis_angle(1, 0, 0, 180), axis_angle(1, 0, 0, 90), axis_angle(0, 1, 0, 80), axis_angle(1, 0, 0, -100)}) {
        Run run(kLive);
        const Quat level{};
        run.recentre(level);
        run.work(30);
        run.hold(desk, 60);
        run.work(2, 30);
        const float error = worst_error(run, level);
        if (!(error < 2.5f)) std::fprintf(stderr, "put down: tilts %.1f degrees wrong\n", error);
        CHECK(error < 2.5f);
    }
}

void a_head_held_tipped_is_the_new_level() {
    // No gravity to go on, so a tilt held for minutes is taken as how you sit: "level" is level as you see it.
    Run run(kLive);
    const Quat level{}, leaning = head_at(0, 0, 15);
    run.recentre(level);
    run.work(30);
    run.hold(leaning, 300);
    const auto reference = run.pose.reference();
    CHECK(reference);
    if (reference) {
        const auto up = canvas_up(*reference, Quat{});
        const std::array<float, 3> head{std::sin(rad(15)), std::cos(rad(15)), 0};  // the head's up in the canvas: tipped right
        CHECK(std::fabs(up[0] * head[0] + up[1] * head[1] + up[2] * head[2]) > std::cos(rad(1.5f)));
    }
}

void the_floor_is_down() {
    // Same frame, same bug: a floor levelled to the feed's z is on the ceiling when calibration finished upside down.
    for (const Case& c : kOddReferences) {
        Run run(c.reference);
        const Quat level{};
        run.recentre(level);
        const float error = floor_error(run, level);
        if (!(error < 0.5f)) std::fprintf(stderr, "%s: floor %.1f degrees off\n", c.name, error);
        CHECK(error < 0.5f);
        run.work(30);
        const float settled = floor_error(run, working(30));
        if (!(settled < 2.5f)) std::fprintf(stderr, "%s: floor %.1f degrees off in use\n", c.name, settled);
        CHECK(settled < 2.5f);
    }
    Run good(Quat{});
    const Quat tipped = head_at(10, 12, 20);
    good.recentre(tipped);
    CHECK(floor_error(good, tipped) < 0.05f);  // ... and level under a tipped recentre, as it always was
}

void reference_stays_a_rotation() {
    // Exactly opposite the feed's z (the shortest turn has no one axis), every case stays a unit quaternion that puts up up.
    for (const Quat reference : {axis_angle(1, 0, 0, 180), axis_angle(0, 1, 0, 180), axis_angle(1, 1, 0, 180), axis_angle(1, 0, 0, 179.99f)}) {
        Run run(reference);
        const Quat level{};
        run.recentre(level);
        const auto r = run.pose.reference();
        CHECK(r);
        if (!r) continue;
        CHECK(std::fabs(r->x * r->x + r->y * r->y + r->z * r->z + r->w * r->w - 1) < 1e-4f);
        const auto up = canvas_up(*r, Quat{});
        CHECK(std::fabs(up[0]) < 1e-3f && std::fabs(up[1] - 1) < 1e-3f && std::fabs(up[2]) < 1e-3f);
    }
    PoseState none;
    CHECK(!none.reference());  // before the first sample there is none, as before
}

int dump() {
    // The tilts the renderer would report (wayland.cpp take_roll_geometry), in hundredths of a degree.
    auto line = [](const std::vector<float>& t) {
        std::string s = "[";
        for (std::size_t i = 0; i < t.size(); ++i)
            s += (i ? "," : "") + (std::isnan(t[i]) ? std::string("null") : std::to_string(std::clamp(int(std::lround(t[i] * 180.f / kPi * 100.f)), -18000, 18000)));
        return s + "]";
    };
    std::printf("{");
    bool first = true;
    auto scenario = [&](const char* name, const Run& run, Quat truth) {
        std::printf("%s\"%s\":{\"tilt\":%s,\"truth\":%s}", first ? "" : ",", name, line(tilts(*run.pose.reference())).c_str(), line(tilts(truth)).c_str());
        first = false;
    };
    const Quat level{}, tipped = head_at(5, 0, 12);
    { Run run(kLive); run.recentre(level); scenario("live, worn level", run, level); }
    { Run run(kLive); run.recentre(tipped); run.work(45); scenario("live, tipped recentre", run, tipped); }
    { Run run(axis_angle(1, 0, 0, 90)); run.recentre(level); scenario("lying on one arm", run, level); }
    { Run run(Quat{}); run.recentre(tipped); scenario("worn straight, tipped recentre", run, tipped); }
    std::printf("}\n");
    return 0;
}
}

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--dump") == 0) return dump();
    the_feeds_z_is_not_up();
    a_recentre_with_the_head_tipped_is_still_found();
    a_recentre_keeps_what_was_learned();
    glasses_worn_straight_at_the_start_keep_exact_gravity();
    a_reference_that_looks_plausible_is_corrected_in_use();
    normal_use_does_not_wander();
    putting_the_glasses_down_teaches_nothing();
    a_head_held_tipped_is_the_new_level();
    the_floor_is_down();
    reference_stays_a_rotation();
    if (failures) { std::fprintf(stderr, "level_frame: %d failures\n", failures); return 1; }
    std::puts("level_frame: ok");
}
