// VR video, offline: projection maths (the GLSL in video_gl.cpp mirrors video_uv), output eyes, texture caps, the
// `video` control payload (shared fixture with tests/native_spike/test_video.py) and the controls card's OLED limits.
#include "xr/ipc.hpp"
#include "xr/video.hpp"
#include <nlohmann/json.hpp>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <string>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }
constexpr float kPi = 3.14159265358979f;
std::array<float, 3> dir(float yaw_deg, float pitch_deg) {  // yaw > 0 right, pitch > 0 up; GL frame (-z forward)
    const float y = yaw_deg * kPi / 180, p = pitch_deg * kPi / 180;
    return {std::sin(y) * std::cos(p), std::sin(p), -std::cos(y) * std::cos(p)};
}
bool uv_is(const VideoFormat& f, std::array<float, 3> d, int eye, float u, float v) {
    const auto r = video_uv(f, d, eye);
    if (!r) { std::fprintf(stderr, "  no uv (expected %.4f %.4f)\n", u, v); return false; }
    if (!near((*r)[0], u) || !near((*r)[1], v)) { std::fprintf(stderr, "  uv %.4f %.4f, expected %.4f %.4f\n", (*r)[0], (*r)[1], u, v); return false; }
    return true;
}
std::string frame(const nlohmann::json& payload, std::uint64_t id = 7) {
    return nlohmann::json{{"schema", 1}, {"request_id", id}, {"run_id", "run1"}, {"session", "s1"}, {"op", "video"}, {"payload", payload}}.dump() + "\n";
}

void projection() {
    const VideoFormat mono360{};
    CHECK(uv_is(mono360, dir(0, 0), 0, 0.5f, 0.5f));          // straight ahead: the middle of the frame
    CHECK(uv_is(mono360, dir(90, 0), 0, 0.75f, 0.5f));        // turn right: right of centre
    CHECK(uv_is(mono360, dir(-90, 0), 0, 0.25f, 0.5f));
    CHECK(uv_is(mono360, dir(0, 45), 0, 0.5f, 0.25f));        // look up: towards the top (v = 0 is the top)
    CHECK(uv_is(mono360, dir(0, -89), 0, 0.5f, 179.f / 180.f));
    const auto behind = video_uv(mono360, dir(179.9f, 0), 0);  // 360°: behind you is the frame's left/right edge
    CHECK(behind && ((*behind)[0] > 0.99f || (*behind)[0] < 0.01f));
    CHECK(!video_uv(mono360, {0, 0, 0}, 0));
    CHECK(!video_uv(mono360, {NAN, 0, -1}, 0));

    const VideoFormat tb{VideoProjection::equirect360, VideoStereo::tb, 180};
    CHECK(uv_is(tb, dir(0, 0), 0, 0.5f, 0.25f));             // left eye on top
    CHECK(uv_is(tb, dir(0, 0), 1, 0.5f, 0.75f));
    CHECK(uv_is(tb, dir(90, 0), 1, 0.75f, 0.75f));

    const VideoFormat sbs180{VideoProjection::equirect180, VideoStereo::sbs, 180};
    CHECK(uv_is(sbs180, dir(0, 0), 0, 0.25f, 0.5f));         // left eye on the left
    CHECK(uv_is(sbs180, dir(0, 0), 1, 0.75f, 0.5f));
    CHECK(uv_is(sbs180, dir(90, 0), 0, 0.5f, 0.5f));         // 180°: 90° right is the eye image's right edge
    CHECK(uv_is(sbs180, dir(-45, 0), 1, 0.5f + 0.125f, 0.5f));
    CHECK(!video_uv(sbs180, dir(120, 0), 0));                 // behind a 180° video: nothing
    CHECK(!video_uv(sbs180, dir(180, 0), 1));

    const VideoFormat fish{VideoProjection::fisheye, VideoStereo::sbs, 180};
    CHECK(uv_is(fish, dir(0, 0), 0, 0.25f, 0.5f));
    CHECK(uv_is(fish, dir(90, 0), 0, 0.5f, 0.5f));           // 90° off axis = the circle's edge (fov 180)
    CHECK(uv_is(fish, dir(0, 45), 0, 0.25f, 0.25f));         // equidistant: 45° up = half way to the top
    CHECK(!video_uv(fish, dir(100, 0), 0));
    const VideoFormat fish200{VideoProjection::fisheye, VideoStereo::mono, 200};
    CHECK(video_uv(fish200, dir(99, 0), 0).has_value());      // a 200° lens sees a little behind the side
    CHECK(uv_is(fish200, dir(100, 0), 0, 1.f, 0.5f));

    // view_ray inverts the head rotation: turning the head right by 90° looks at what was on the right.
    const float c = std::cos(kPi / 4), s = std::sin(kPi / 4);
    const Mat3 identity{1, 0, 0, 0, 1, 0, 0, 0, 1};
    const auto centre = view_ray(identity, 0, 0, 0.5f, 0.3f);
    CHECK(near(centre[0], 0) && near(centre[1], 0) && near(centre[2], -1));
    const auto corner = view_ray(identity, 1, 1, 0.5f, 0.3f);
    CHECK(near(corner[0], 0.5f) && near(corner[1], 0.3f));
    // A head turned 45° right: world -> eye view rotates world points left (the desktops' convention, pose.hpp).
    const Mat3 right45{c, 0, s, 0, 1, 0, -s, 0, c};
    const auto looking = view_ray(right45, 0, 0, 0.5f, 0.3f);
    CHECK(uv_is(mono360, looking, 0, 0.625f, 0.5f));
    (void)s;
}

void outputs() {
    const auto mono = eye_viewports(1920, 1200);
    CHECK(mono.size() == 1 && mono[0].width == 1920 && mono[0].height == 1200 && mono[0].eye == 0);
    const auto scaled = eye_viewports(1536, 960);
    CHECK(scaled.size() == 1);
    const auto sbs = eye_viewports(3840, 1200);
    CHECK(sbs.size() == 2 && sbs[0].x == 0 && sbs[0].width == 1920 && sbs[0].eye == 0 && sbs[1].x == 1920 && sbs[1].width == 1920 && sbs[1].eye == 1);
    CHECK(eye_viewports(3840, 1080).size() == 2);
    CHECK(eye_viewports(0, 1080).empty());
    const auto h = half_fov_tan(46, 1920, 1200);  // same split as wayland.cpp update_fov
    CHECK(near(std::sqrt(h[0] * h[0] + h[1] * h[1]), std::tan(23 * kPi / 180)) && near(h[0] / h[1], 1.6f));

    CHECK((video_texture_size(7680, 3840) == std::array<std::uint32_t, 2>{7680, 3840}));
    CHECK((video_texture_size(11520, 5760) == std::array<std::uint32_t, 2>{8192, 4096}));
    CHECK((video_texture_size(8640, 8640) == std::array<std::uint32_t, 2>{8192, 8192}));
    CHECK((video_texture_size(4096, 4096, 2048) == std::array<std::uint32_t, 2>{2048, 2048}));
    CHECK((video_texture_size(0, 10) == std::array<std::uint32_t, 2>{0, 0}));
}

void labels() {
    CHECK(format_clock(0) == "0:00" && format_clock(7.9) == "0:07" && format_clock(245) == "4:05" && format_clock(3723) == "1:02:03");
    CHECK(format_clock(-3) == "0:00" && format_clock(NAN) == "0:00");
    CHECK(video_format_label({}, true) == "360 deg mono");
    CHECK(video_format_label({VideoProjection::equirect180, VideoStereo::sbs, 180}) == "180° side-by-side 3D");
    CHECK(video_format_label({VideoProjection::fisheye, VideoStereo::tb, 190}, true) == "Fisheye 190 deg top-bottom 3D");
    for (auto s : {VideoState::off, VideoState::loading, VideoState::playing, VideoState::paused, VideoState::ended, VideoState::error})
        CHECK(std::string(video_state_name(s)).size() >= 3);
}

void payloads(const char* fixture) {
    std::ifstream in(fixture);
    CHECK(in.good());
    if (!in.good()) return;
    const auto data = nlohmann::json::parse(in);
    std::uint64_t id = 1;
    for (const auto& p : data["valid"]) {
        const auto decoded = decode_control(frame(p, id++));
        const auto* m = std::get_if<ControlMessage>(&decoded);
        CHECK(m && m->op == ControlOp::video);
        if (!m) { std::fprintf(stderr, "  rejected valid %s\n", p.dump().c_str()); continue; }
        const auto& v = std::get<VideoPayload>(m->payload);
        const auto action = p["action"].get<std::string>();
        if (action == "open") CHECK(v.action == VideoAction::open && v.title == p["title"].get<std::string>() && v.format.fov_deg == p["fov"].get<std::uint32_t>());
        if (action == "seek") CHECK(v.action == VideoAction::seek && v.seconds == p["seconds"].get<std::int32_t>());
        if (action == "controls") CHECK(v.action == VideoAction::controls && v.visible == p["visible"].get<bool>());
    }
    for (const auto& p : data["invalid"]) {
        const auto decoded = decode_control(frame(p, id++));
        const auto* e = std::get_if<Error>(&decoded);
        CHECK(e && !ignorable_control_error(*e));  // a malformed video frame is never silently dropped
        if (!e) std::fprintf(stderr, "  accepted invalid %s\n", p.dump().c_str());
    }
    // Specific decodes.
    const auto open = decode_control(frame({{"action", "open"}, {"projection", "fisheye"}, {"stereo", "tb"}, {"fov", 200}, {"title", "t"}}));
    const auto& o = std::get<VideoPayload>(std::get<ControlMessage>(open).payload);
    CHECK(o.format == (VideoFormat{VideoProjection::fisheye, VideoStereo::tb, 200}));
}

void card() {
    VideoCardState s{"beach_360.mp4", {VideoProjection::equirect180, VideoStereo::sbs, 180}, VideoState::playing, 75, 300, ""};
    for (int pass = 0; pass < 2; ++pass) {
        std::string why;
        auto font = pass ? HudFont::load(kHudFontFamily, why) : nullptr;
        for (auto state : {VideoState::loading, VideoState::playing, VideoState::paused, VideoState::ended, VideoState::error}) {
            s.state = state; s.detail = state == VideoState::error ? "unrecognized file format" : "";
            const auto im = render_video_card(s, {}, font.get());
            CHECK(im.width > 300 && im.height > 100 && im.rgba.size() == std::size_t(im.width) * im.height * 4);
            std::uint8_t brightest = 0;
            for (std::size_t i = 0; i < im.rgba.size(); i += 4)
                for (int c = 0; c < 3; ++c) brightest = std::max(brightest, im.rgba[i + std::size_t(c)]);
            CHECK(brightest <= kCardMaxLevel);  // micro-OLED rule: dimmed like the help card
            CHECK(brightest > 80);              // and still readable
        }
    }
    VideoControlsOverlay overlay;
    CHECK(overlay.alpha(0) == 0.f);
    CHECK(overlay.set_visible(true, 0));
    CHECK(overlay.generation() == 1 && overlay.shown_count() == 1);
    CHECK(!overlay.set_visible(true, 10));
    s.state = VideoState::playing; s.detail.clear();
    CHECK(overlay.update(s, 20));                 // new state: re-rendered
    const auto g = overlay.generation();
    CHECK(!overlay.update(s, 30) && overlay.generation() == g);  // same second: no re-render
    s.position_s += 1;
    CHECK(overlay.update(s, 1030) && overlay.generation() == g + 1);
    CHECK(overlay.alpha(5000) > 0.99f);
    CHECK(overlay.set_visible(false, 6000));
    s.position_s += 1;
    CHECK(!overlay.update(s, 7000));              // hidden: nothing rendered
    CHECK(overlay.alpha(7000) == 0.f && !overlay.animating(7000));
}
}

int main(int argc, char** argv) {
    projection();
    outputs();
    labels();
    payloads(argc > 1 ? argv[1] : "tests/native_spike/fixtures/video_payloads.json");
    card();
    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("video: ok");
    return 0;
}
