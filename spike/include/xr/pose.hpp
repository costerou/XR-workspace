#pragma once
// 3DoF head pose from XRLinuxDriver's SysV IPC feed (network-blocked driver; user grant 2026-10-31)
// and the world-anchored view math. Offline-testable: the IPC path prefix is injectable.
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace xr {
struct Quat { float x = 0, y = 0, z = 0, w = 1; };
Quat quat_mul(Quat a, Quat b);
Quat quat_conj(Quat q);
std::optional<Quat> quat_normalized(Quat q);  // nullopt if non-finite or near zero length
using Mat3 = std::array<float, 9>;            // row-major
Mat3 quat_to_mat3(Quat q);
// Driver frame (x forward, y left, z up) to GL eye frame (x right, y up, z backward).
Mat3 driver_to_gl(const Mat3& driver);
Mat3 transpose(const Mat3& m);
std::array<float, 3> apply(const Mat3& m, std::array<float, 3> v);

// Where a canvas point (NDC at the centre pose) lands after the head rotates by `view` (the
// transpose of the head's relative rotation in GL frame). hw/hh = tan(half FOV). nullopt = behind.
std::optional<std::array<float, 2>> project(const Mat3& view, float ndc_x, float ndc_y, float hw, float hh);

// orientation/device_ms: newest. older/older_ms: the driver's two previous samples (~16, ~32 ms),
// unvalidated here; prediction checks them (predict.hpp).
struct FeedSample { Quat orientation; float device_ms = 0; std::array<Quat, 2> older{}; std::array<float, 2> older_ms{}; };

class PoseFeed {
public:
    // prefix "/tmp/shader_runtime_" in production; tests pass a private prefix.
    static std::unique_ptr<PoseFeed> open(const std::string& prefix, std::string& why);
    ~PoseFeed();
    // Under the driver's process-shared (robust) mutex; nullopt if disabled/invalid. Waits at most
    // 2 ms for the lock; if the driver holds it longer (stopped) or died holding it, the newest sample
    // already read is returned again (same device timestamp, so it ages normally). After two timeouts
    // in a row, frames do not wait for 50 ms, then wait once more; a driver that still holds the lock
    // is retried less often, doubling up to once a second.
    std::optional<FeedSample> read();
    std::optional<float> display_fov() const;  // driver-published diagonal FOV (deg), if sane (10..120)
    std::optional<std::array<float, 4>> look_ahead_cfg() const;  // driver look-ahead config, raw; absent if not published
private:
    PoseFeed() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Tracking state: recenter, staleness (lost -> hold last good pose), view rotation for rendering.
// A USB drop of the glasses (docs/technical.md, Head tracking drops): the feed stops, the driver reconnects by itself, and
// it comes back on a new reference (driver_handle_pose: conj(reference) * pose, the reference taken as calibration ends), so
// the same head reads as a different pose. While it is gone the view is held. A feed silent for more than kReanchorAfterMs
// must then run unbroken for kResumeSettleMs before it is believed: the second disconnect of a pair can come seconds after
// the first, and a few samples between the two are no recovery. What it says then is taken to be the head where it was
// when the view was held, so nothing moves: the centre and the level are carried over into the new frame. If the head did
// turn meanwhile, the world turns with it, as with a recentre; that is the price of no jump. The driver publishes no pose
// until it has calibrated (outputs.c handle_imu_update), so a fresh sample is a calibrated one.
class PoseState {
    friend struct ViewContinuity;
public:
    static constexpr double kReanchorAfterMs = 500;  // a hiccup under this is the same feed, a moment late
    static constexpr double kResumeSettleMs = 250;
    static constexpr double kHintAfterMs = 1000;     // the hint is for a drop, not for a hiccup
    explicit PoseState(double stale_ms = 100.0) : stale_ms_(stale_ms) {}
    void update(const std::optional<FeedSample>& sample, double now_ms);
    void recenter() { recenter_pending_ = true; }
    // For a feed whose z is the world's up, which only the Pro 2's SDK pose is: XRLinuxDriver's has no gravity in it (below). The
    // head turns about gravity, but a glasses frame pitched against gravity at the recentre sees that turn with a roll in it, sin(pitch)
    // of the turn, so the picture leans against every turn (docs/pro2/smoothness.md). On: each recentre takes the fixed turn from the
    // head to a frame with the same heading and no pitch or roll, and everything after works in that frame, so a turn about gravity
    // stays a turn. Nothing here says the pitch is a mount offset rather than the wearer's head: it is whatever gravity says then.
    void level_reference(bool on) { level_reference_ = on; }
    float levelled_deg() const { return levelled_deg_; }  // what the last recentre took out
    Quat level_turn() const { return level_turn_; }       // head * this = the frame relative() is in; identity when off
    bool tracking() const { return tracking_; }
    // Tracking was working and has been gone for kHintAfterMs: "Reconnecting tracking...".
    bool reconnecting(double now_ms) const { return !tracking_ && center_ && now_ms - lost_at_ms_ >= kHintAfterMs; }
    Mat3 view() const { return view_; }  // identity until the first valid sample
    Quat relative() const { return relative_; }  // head vs recentre reference, driver frame
    // The recentre reference (the head's orientation when it was taken) in a frame whose z is the world's up as far as
    // that can be told (up_ below): what ties the canvas to level. nullopt before the first valid sample.
    std::optional<Quat> reference() const;
    std::uint64_t samples() const { return samples_; }
    std::uint64_t lost_events() const { return lost_events_; }
    std::uint64_t reanchors() const { return reanchors_; }  // drops recovered from
private:
    void reanchor(Quat head);
    void recentre_up(Quat head);
    void learn_up(Quat head, double ms);
    double stale_ms_;
    std::optional<Quat> center_;
    // The world's up in the feed's frame. The feed has no gravity in it: XRLinuxDriver publishes the pose relative to the one
    // it captured as calibration finished (src/driver.c driver_handle_pose: conj(reference) * pose), so its z axis is the
    // glasses' up at that moment, wherever they were: on your head, or upside down on the desk. Only the head is left to go
    // on, and two things hold for a head:
    //   - At a recentre the glasses are on it, so up is within a neck's reach of its own up (kUpReachDeg). Up stays if it is
    //     (a tipped recentre is exactly what the level bar is for). If not, the feed's z takes over if that is in reach
    //     (then it is exact, for glasses worn straight at the start), else the head's own up: the best there is.
    //   - It is held straight on average, so whichever way it turns its left-right axis stays level. A Kalman filter on that
    //     one fact (up_cov_) corrects a reference that only looked right and learns pitch as soon as the head has turned a
    //     little. Unlike averaging the head's own up it takes nothing from looking up or down at a desktop.
    // Glasses further than kUpWornDeg from up are not a head looking about (put down, on one arm): nothing is learned. So a
    // head held tipped for a minute or more is taken for how you sit, which is level as you see it.
    std::array<float, 3> up_{0, 0, 1};
    std::array<float, 9> up_cov_{};  // row-major, rad^2; zero until the first sample
    Quat last_{};
    float last_device_ms_ = -1;
    double last_change_ms_ = 0;
    double lost_at_ms_ = 0;         // when tracking_ last went false
    double settling_from_ms_ = -1;  // the unbroken run of fresh samples since a long silence; -1: not in one
    bool level_reference_ = false;
    Quat level_turn_{};     // head * level_turn_ = a frame level at the recentre
    float levelled_deg_ = 0;
    bool tracking_ = false, recenter_pending_ = true, dropped_ = false;  // dropped_: after a long silence, until reanchored
    Mat3 view_{1, 0, 0, 0, 1, 0, 0, 0, 1};
    Quat relative_{};
    std::uint64_t samples_ = 0, lost_events_ = 0, reanchors_ = 0;
};

Quat quat_slerp(Quat a, Quat b, float t);  // shortest arc
float quat_angle(Quat a, Quat b);          // radians between orientations
Quat level(Quat q);                        // same yaw/pitch of the forward axis, zero roll (driver frame)
Mat3 view_from(Quat head_relative, Quat anchor);  // render rotation for a canvas anchored at `anchor`
// The world's up (z of the frame `reference` is in) as a unit vector in the GL frame of a canvas anchored at `anchor`
// relative to the recentre `reference` (PoseState::reference, level): what view.hpp desktop_tilt measures a desktop's roll
// against.
std::array<float, 3> canvas_up(Quat reference, Quat anchor);
// The canvas turn (driver frame, relative to the canvas anchor) that brings canvas direction (az, el) straight ahead:
// az / el radians round the eye as view.hpp place_point turns a desktop (+az right, +el up). With a canvas anchored at
// head * aim_rotation(az, el), view_from puts the point in direction (az, el) on the view's centre, upright.
Quat aim_rotation(float az, float el);
// The view `turned` radians of the way from aim `from` to aim `to`, `way` radians in all (edge_pan.py way): one rotation from end to
// end, the short way round for a way under half a turn. At half a turn or past it, it goes round the way `side` pushes (view.hpp
// EdgePan::side), the long way if it must: a desktop dead opposite has no short way to go by. Exactly `from` at 0, `to` at `way`.
Quat aim_along(Quat from, Quat to, float way, float turned, std::uint8_t side);

// T2 viewing modes. Where the canvas sits, relative to the recentre reference (driver frame):
//   anchor: fixed in space (3DoF world-locked) where the canvas was when anchor mode was entered (un-maximizing: back
//           where the room was left, set_mode's restore); a recentre moves it straight ahead
//   locked: 0DoF, rigidly attached to the display (head-locked, includes roll)
//   follow: always following, no dead zone: a critically damped rotational spring pulls the
//           canvas toward the full head orientation (roll included). From rest it accelerates,
//           then decelerates smoothly into centre without overshoot (like the Beast's smooth follow)
// The aim (set_aim, view.hpp focus_direction / focus_weight) is where on the canvas the view centres: locked and follow
// put the canvas at head * aim (follow: the spring's head * aim), so the focused desktop sits straight ahead wherever it
// was dragged; anchor ignores it except that a recentre puts the aim straight ahead. Identity = the canvas centre.
// Mode changes blend smoothly from wherever the canvas currently is; steady states are exact.
// Maximize-follow (2026-11-03): the overview is always anchor and a maximized (zoomed-in) desktop follow or locked, so the
// supervisor changes the mode with every maximize and un-maximize. The room (pinned_: where anchor holds the canvas) is
// kept through a maximize, and un-maximizing goes back to it, so every desktop is in the overview exactly where it was.
enum class ViewMode : std::uint8_t { anchor = 0, locked = 1, follow = 2 };
class ModeController {
    friend struct ViewContinuity;
public:
    // follow_omega: spring natural frequency (rad/s); higher = snappier. 8 covers ~90% of a turn in 0.5 s.
    struct Tuning { float transition_ms = 300, follow_omega = 8.f; };
    // How a mode change starts (set_mode):
    //   blend (a manual change, Ctrl+Alt+V): from wherever the canvas is now; entering anchor pins the room there.
    //   maximize (anchor -> follow / locked, a desktop maximized from the overview): the room is kept and the canvas held
    //     exactly where it is until land_maximize() (call it once the zoom-in has landed, so the desktop grows in place);
    //     then the mode starts from there as a blend does (follow: follow_ * aim = here, nothing moves at all).
    //   restore (follow / locked -> anchor, un-maximized back to the overview): the canvas blends back to the room kept
    //     since the maximize instead of pinning where it is now. A recentre in between moved the room (recenter()).
    enum class Change : std::uint8_t { blend, maximize, restore };
    ModeController() = default;
    explicit ModeController(Tuning t) : tuning_(t) {}
    void set_mode(ViewMode mode, double now_ms, Change how = Change::blend);
    void land_maximize(double now_ms);  // a held maximize: its mode takes over from here (no-op unless holding())
    bool holding() const { return holding_; }
    void recenter();  // the reference moved to the current head: the aim (anchor: the canvas centre) is straight ahead
    void set_aim(Quat aim) { aim_ = aim; }  // per frame, before update()
    // Anchor: turn the world-locked canvas by `turn` (canvas frame: anchor' = anchor * turn), e.g. a landed desktop switch
    // (view.hpp ViewAnimator::take_landed_turn): conj(aim_rotation(from)) * aim_rotation(to) keeps the view where the
    // switch's camera turn (look_turn at 1) left it. Takes effect at once; a mode blend into anchor heads for the turned pose.
    void turn_anchor(Quat turn);
    Quat aim() const { return aim_; }
    ViewMode mode() const { return mode_; }
    // Advance with the head's relative orientation; returns the canvas anchor for this frame.
    Quat update(Quat head, double now_ms);
    bool settled() const;  // nothing animating (no mode blend or held maximize; follow spring at rest)
    Quat anchor() const { return anchor_; }  // the canvas anchor update() last returned
private:
    Quat target(Quat head) const;
    Tuning tuning_{};
    ViewMode mode_ = ViewMode::anchor;
    Quat anchor_{}, from_{}, follow_{}, pinned_{};  // pinned_: the world-locked anchor pose; follow_: the spring's head
    Quat held_{};  // a held maximize: where the canvas stays until land_maximize
    Quat aim_{};
    double started_ms_ = -1, last_ms_ = -1;
    std::array<float, 3> velocity_{};  // follow angular velocity (rad/s, canvas frame)
    bool transitioning_ = false, holding_ = false;
};
}
