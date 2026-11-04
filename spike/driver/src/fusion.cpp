#include "luma/fusion.hpp"
#include <algorithm>
#include <cmath>

// Double precision throughout: at 1 kHz a sample's rotation is around 1e-5 rad, which single
// precision quaternion arithmetic rounds into a drift of its own.
namespace luma {
namespace {
using V = std::array<double, 3>;
struct Q { double w = 1, x = 0, y = 0, z = 0; };

constexpr double kG = 9.80665;
constexpr double kBlockS = 0.1;      // Stillness is judged over blocks of this length.
constexpr int kMinQuietBlocks = 5;   // Fewer than this in start-up: no bias guess is better than one.
constexpr int kStillRunBlocks = 5;   // Half a second before a pause counts as still.
constexpr std::size_t kMaxStartBlocks = 64;

V operator+(V a, V b) { return {a[0] + b[0], a[1] + b[1], a[2] + b[2]}; }
V operator-(V a, V b) { return {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }
V operator*(V a, double k) { return {a[0] * k, a[1] * k, a[2] * k}; }
double dot(V a, V b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
double norm(V a) { return std::sqrt(dot(a, a)); }
V cross(V a, V b) { return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]}; }
V to_v(const std::array<float, 3>& a) { return {a[0], a[1], a[2]}; }
std::array<float, 3> to_f(V a) { return {float(a[0]), float(a[1]), float(a[2])}; }
bool finite(const std::array<float, 3>& a) { return std::isfinite(a[0]) && std::isfinite(a[1]) && std::isfinite(a[2]); }

Q mul(Q a, Q b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
Q normalized(Q q) {
    const double n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    return {q.w / n, q.x / n, q.y / n, q.z / n};
}
Q exp_map(V phi) {
    const double a = norm(phi);
    const double k = a < 1e-8 ? 0.5 - a * a / 48 : std::sin(a / 2) / a;
    return {std::cos(a / 2), phi[0] * k, phi[1] * k, phi[2] * k};
}
// World up in the body frame: third row of the rotation matrix.
V up_in_body(Q q) {
    return {2 * (q.x * q.z - q.w * q.y), 2 * (q.y * q.z + q.w * q.x), 1 - 2 * (q.x * q.x + q.y * q.y)};
}
V to_world(Q q, V v) {
    const V u{q.x, q.y, q.z};
    return v + cross(u, cross(u, v) + v * q.w) * 2;
}

// Attitude with `up` (unit, body frame) as world up and body forward facing world x. With the nose
// straight up or down, forward is the direction the top of the glasses points.
Q attitude_from_up(V up) {
    V fwd{1, 0, 0};
    if (std::fabs(up[0]) > 0.99) fwd = {0, 0, up[0] > 0 ? -1.0 : 1.0};
    V x = fwd - up * dot(fwd, up);
    x = x * (1 / norm(x));
    const V y = cross(up, x);
    // Rotation matrix rows x, y, up (Shepperd's conversion).
    const double m00 = x[0], m01 = x[1], m02 = x[2], m10 = y[0], m11 = y[1], m12 = y[2], m20 = up[0], m21 = up[1], m22 = up[2];
    const double t = m00 + m11 + m22;
    Q q;
    if (t > 0) {
        const double s = std::sqrt(t + 1) * 2;
        q = {s / 4, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s};
    } else if (m00 >= m11 && m00 >= m22) {
        const double s = std::sqrt(1 + m00 - m11 - m22) * 2;
        q = {(m21 - m12) / s, s / 4, (m01 + m10) / s, (m02 + m20) / s};
    } else if (m11 >= m22) {
        const double s = std::sqrt(1 + m11 - m00 - m22) * 2;
        q = {(m02 - m20) / s, (m01 + m10) / s, s / 4, (m12 + m21) / s};
    } else {
        const double s = std::sqrt(1 + m22 - m00 - m11) * 2;
        q = {(m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, s / 4};
    }
    return normalized(q);
}
double ramp_down(double x, double zero_at) { return std::clamp(1 - x / zero_at, 0.0, 1.0); }
double wrap_pi(double a) {
    constexpr double pi = 3.14159265358979323846;
    while (a > pi) a -= 2 * pi;
    while (a < -pi) a += 2 * pi;
    return a;
}
}  // namespace

struct Fusion::State {
    FusionConfig c;
    Q q;
    V bias{}, omega{}, last_gyro{};
    std::uint64_t last_t = 0;
    std::uint32_t last_seq = 0;
    bool have_last = false;
    double period = 1e-3;
    double stream_s = 0, calibrated_after = -1;
    FusionQuality base = FusionQuality::Uncalibrated;
    bool recovering = false;
    double recover_left = 0;
    V recover_error{};
    double g_ref = kG;
    // Stillness: the block being filled, and the block before it.
    double block_s = 0;
    int block_n = 0;
    V g_sum{}, g_sq{}, a_sum{}, a_sq{}, prev_g_mean{};
    bool have_prev_block = false;
    int run = 0;  // Unbroken still blocks.
    V run_gyro{}, run_accel{};
    int quiet_n = 0;
    V quiet_sum{};
    double quiet_total_s = 0;
    bool quiet_disagreed = false;
    // Quiet block means seen in start-up: their median is the Coarse bias.
    std::array<V, kMaxStartBlocks> start_blocks{};
    std::size_t start_n = 0;
    bool have_mag_ref = false;
    double mag_heading_ref = 0, mag_norm_ref = 0;
    PoseOut out{};
    std::uint64_t samples = 0, rejected = 0, gaps = 0, time_jumps = 0;

    explicit State(const FusionConfig& config) : c(config) {
        if (c.warm_start_bias) {
            bias = clamp_bias(to_v(*c.warm_start_bias));
            base = FusionQuality::Coarse;
        }
    }
    V clamp_bias(V b) const {
        const double n = norm(b);
        return n > c.max_bias ? b * (c.max_bias / n) : b;
    }
    void new_block() {
        block_s = 0;
        block_n = 0;
        g_sum = g_sq = a_sum = a_sq = V{};
    }
    void restart_stillness() {
        new_block();
        have_prev_block = false;
        run = 0;
        run_gyro = run_accel = V{};
        quiet_n = 0;
        quiet_sum = V{};
    }
    void mark_calibrated(FusionQuality to) {
        base = to;
        if (calibrated_after < 0) calibrated_after = stream_s;
    }
    void start_recovering() {
        recovering = true;
        recover_left = c.recover_s;
        recover_error = {1, 0, 0};
        restart_stillness();
    }
    bool still() const { return run >= kStillRunBlocks; }

    // How long since the last sample, in seconds; 0 = do not integrate. Sequence numbers settle
    // what a bad timestamp cannot: a jump with no samples missing was only the clock.
    double step_time(const ImuSample& s) {
        const std::uint32_t seq_step = s.seq - last_seq;
        const double dt = double(std::int64_t(s.t_ns - last_t)) * 1e-9;
        if (dt > 0 && dt <= c.max_gap_s) {
            if (seq_step == 1 && dt > period * 0.2 && dt < period * 5) period += (dt - period) * 0.02;
            return dt;
        }
        const double by_count = seq_step * period;
        if (seq_step >= 1 && by_count <= c.max_gap_s) {
            ++time_jumps;
            start_recovering();
            return by_count;
        }
        ++gaps;
        start_recovering();
        return 0;
    }

    void integrate(V gyro, double dt) {
        const V a = (last_gyro - bias) * dt, b = (gyro - bias) * dt;
        q = normalized(mul(q, exp_map((a + b) * 0.5 + cross(a, b) * (1.0 / 12))));
    }

    void correct_gravity(V accel, double dt) {
        const double n = norm(accel);
        if (n < 0.5 * kG || n > 1.5 * kG) return;
        const bool fast = base == FusionQuality::Uncalibrated || recovering;
        const double gate = ramp_down(std::fabs(n / g_ref - 1), fast ? 0.5 : double(c.accel_norm_gate)) *
                            (fast ? 1.0 : ramp_down(norm(omega), c.accel_rate_gate));
        if (gate <= 0) return;
        const V measured = accel * (1 / n), estimated = up_in_body(q);
        V e = cross(measured, estimated);
        // Near a half turn the cross product vanishes though the error is at its largest.
        const double sine = norm(e);
        if (dot(measured, estimated) < 0) {
            e = sine > 1e-6 ? e * (1 / sine) : cross(measured, std::fabs(measured[0]) < 0.9 ? V{1, 0, 0} : V{0, 1, 0});
            if (sine <= 1e-6) e = e * (1 / norm(e));
        }
        const double kp = fast ? c.kp_recover : still() ? c.kp_still : c.kp_moving;
        q = normalized(mul(q, exp_map(e * (kp * gate * dt))));
        if (!fast) bias = clamp_bias(bias - e * (c.ki * gate * dt));
        // One sample's accelerometer noise can dip under the limit: judge the error smoothed.
        recover_error = recover_error + (e - recover_error) * std::min(1.0, dt / 0.05);
        if (recovering && recover_left <= 0 && (norm(recover_error) < c.recover_tilt_rad || recover_left <= -c.recover_max_s)) recovering = false;
    }

    void correct_heading(const std::array<float, 3>& mag, double dt) {
        const V m = to_world(q, to_v(mag));
        const double horizontal = std::hypot(m[0], m[1]), n = norm(m);
        if (horizontal < 0.2 * n || n <= 0) return;
        if (!have_mag_ref) {
            if (base == FusionQuality::Uncalibrated) return;
            have_mag_ref = true;
            mag_heading_ref = std::atan2(m[1], m[0]);
            mag_norm_ref = n;
            return;
        }
        if (std::fabs(n / mag_norm_ref - 1) > c.mag_gate) return;
        const double error = wrap_pi(mag_heading_ref - std::atan2(m[1], m[0]));
        const V e = up_in_body(q) * error;  // World-up axis, body frame.
        q = normalized(mul(q, exp_map(e * (c.kp_mag * dt))));
        bias = clamp_bias(bias - e * (c.ki * dt));
    }

    void finish_calibration() {
        const V g = run_gyro * (1.0 / run), a = run_accel * (1.0 / run);
        bias = clamp_bias(g);
        g_ref = norm(a);
        // The first calibration sets the heading origin: world x is where the glasses face now.
        if (base == FusionQuality::Uncalibrated) q = attitude_from_up(a * (1 / g_ref));
        mark_calibrated(FusionQuality::Good);
    }
    void go_coarse() {
        if (start_n >= kMinQuietBlocks) {
            V median;
            std::array<double, kMaxStartBlocks> axis{};
            for (std::size_t i = 0; i < 3; ++i) {
                for (std::size_t k = 0; k < start_n; ++k) axis[k] = start_blocks[k][i];
                std::nth_element(axis.begin(), axis.begin() + std::ptrdiff_t(start_n / 2), axis.begin() + std::ptrdiff_t(start_n));
                median[i] = axis[start_n / 2];
            }
            bias = clamp_bias(median);
        }
        mark_calibrated(FusionQuality::Coarse);
    }

    void close_block() {
        const double n = block_n;
        const V g = g_sum * (1 / n), a = a_sum * (1 / n);
        const auto std3 = [n](V sum, V sq) {
            double v = 0;
            for (std::size_t i = 0; i < 3; ++i) v += std::max(0.0, sq[i] / n - sum[i] * sum[i] / (n * n));
            return std::sqrt(v);
        };
        const double gyro_std = std3(g_sum, g_sq), accel_std = std3(a_sum, a_sq), an = norm(a);
        const bool level_force = std::fabs(an / kG - 1) < 0.15;
        // A desk's block means differ only by noise. A resting head's wander, however smooth: this
        // is what tells them apart, so the step is judged against what noise alone would give.
        const double noise_step = 2 * std::sqrt(2.0) * gyro_std / std::sqrt(n);
        const bool calm = !have_prev_block || norm(g - prev_g_mean) < c.still_mean_step + noise_step;
        const bool slow = norm(g) < c.cal_max_rate;
        const bool is_still = gyro_std < c.still_gyro_std && accel_std < c.still_accel_std && level_force && calm && slow;
        const bool is_quiet = gyro_std < c.quiet_gyro_std && accel_std < c.quiet_accel_std && level_force && slow;
        prev_g_mean = g;
        have_prev_block = true;
        new_block();

        if (is_still) {
            ++run;
            run_gyro = run_gyro + g;
            run_accel = run_accel + a;
        } else {
            run = 0;
            run_gyro = run_accel = V{};
        }
        const int cal_blocks = std::max(1, int(std::lround(c.cal_still_s / kBlockS)));
        const int relearn_blocks = int(std::lround(c.still_relearn_s / kBlockS));
        if (base != FusionQuality::Good) {
            if (run >= cal_blocks) { finish_calibration(); return; }
            if (base == FusionQuality::Uncalibrated) {
                if (is_quiet && start_n < kMaxStartBlocks) start_blocks[start_n++] = g;
                if (stream_s >= c.coarse_timeout_s) go_coarse();
                return;
            }
        } else if (still()) {
            const V d = g - bias;
            if (norm(d) < c.still_track_limit) bias = clamp_bias(bias + d * (kBlockS / c.still_tau_s));
            else if (run >= relearn_blocks && norm(d) < c.still_relearn_limit) bias = clamp_bias(run_gyro * (1.0 / run));
            quiet_n = 0;
            quiet_sum = V{};
            return;
        }
        // Worn. Turns stay out (is_quiet), the rests between them count however they are spaced:
        // a resting head does not keep turning one way, so the long mean is the bias.
        // The turn-rate limit leans on the bias, so a Coarse one must not use it: it would keep only
        // the blocks that agree with the wrong bias and never learn the right one.
        const bool coarse = base == FusionQuality::Coarse;
        if (!is_quiet || (!coarse && norm(g - bias) >= c.quiet_max_rate)) return;
        quiet_sum = quiet_sum + g;
        ++quiet_n;
        const double window_s = quiet_n * kBlockS;
        if (window_s < (coarse ? c.quiet_window_coarse_s : c.quiet_window_s) - 1e-6) return;
        const V d = quiet_sum * (1.0 / quiet_n) - bias;
        quiet_n = 0;
        quiet_sum = V{};
        const bool agrees = norm(d) < c.quiet_track_limit;
        if (coarse) {
            if (norm(d) >= c.quiet_limit_coarse) return;
            bias = clamp_bias(bias + d * (window_s / c.quiet_tau_coarse_s));
            quiet_total_s = agrees ? quiet_total_s + window_s : 0;
            if (quiet_total_s >= c.coarse_to_good_s) mark_calibrated(FusionQuality::Good);
            return;
        }
        if (agrees) {
            quiet_disagreed = false;
            bias = clamp_bias(bias + d * (window_s / c.quiet_tau_s));
        } else if (quiet_disagreed) {
            // A minute of rests saying the same: no head does that, the bias is wrong. Own up.
            quiet_disagreed = false;
            quiet_total_s = 0;
            base = FusionQuality::Coarse;
        } else {
            quiet_disagreed = true;
        }
    }

    void observe(V gyro, V accel, double dt) {
        block_s += dt;
        ++block_n;
        g_sum = g_sum + gyro;
        a_sum = a_sum + accel;
        for (std::size_t i = 0; i < 3; ++i) {
            g_sq[i] += gyro[i] * gyro[i];
            a_sq[i] += accel[i] * accel[i];
        }
        if (block_s >= kBlockS - 1e-9) close_block();
    }

    PoseOut update(const ImuSample& s) {
        ++samples;
        out.seq = s.seq;
        if (!finite(s.gyro_rad_s) || !finite(s.accel_m_s2)) {
            ++rejected;
            return out;
        }
        const V gyro = to_v(s.gyro_rad_s), accel = to_v(s.accel_m_s2);
        if (have_last && s.t_ns == last_t && s.seq == last_seq) {
            ++rejected;
            return out;
        }
        double dt = 0;
        if (!have_last) {
            last_gyro = gyro;
            const double n = norm(accel);
            if (n > 0.5 * kG && n < 1.5 * kG) q = attitude_from_up(accel * (1 / n));
            if (base == FusionQuality::Coarse) calibrated_after = 0;
        } else {
            dt = step_time(s);
        }
        have_last = true;
        last_t = s.t_ns;
        last_seq = s.seq;
        omega = gyro - bias;
        if (dt > 0) {
            stream_s += dt;
            if (recovering) recover_left -= dt;
            integrate(gyro, dt);
            correct_gravity(accel, dt);
            if (c.use_mag && s.mag && finite(*s.mag)) correct_heading(*s.mag, dt);
            observe(gyro, accel, dt);
        } else if (recovering) {
            correct_gravity(accel, period);
        }
        last_gyro = gyro;
        out.q = {float(q.w), float(q.x), float(q.y), float(q.z)};
        out.t_ns = s.t_ns;
        out.calibrated = base != FusionQuality::Uncalibrated;
        return out;
    }

    FusionStatus status() const {
        FusionStatus st;
        st.quality = recovering && base != FusionQuality::Uncalibrated ? FusionQuality::Recovering : base;
        st.still = still();
        st.gyro_bias_rad_s = to_f(bias);
        st.omega_body_rad_s = to_f(omega);
        st.omega_world_rad_s = to_f(to_world(q, omega));
        st.up_body = to_f(up_in_body(q));
        st.calibrated_after_s = float(calibrated_after);
        st.samples = samples;
        st.rejected = rejected;
        st.gaps = gaps;
        st.time_jumps = time_jumps;
        return st;
    }
    Quatf predict(double horizon) const {
        const Q p = normalized(mul(q, exp_map(omega * horizon)));
        return {float(p.w), float(p.x), float(p.y), float(p.z)};
    }
};

Fusion::Fusion(const FusionConfig& config) : s_(std::make_unique<State>(config)) {}
Fusion::~Fusion() = default;
Fusion::Fusion(Fusion&&) noexcept = default;
Fusion& Fusion::operator=(Fusion&&) noexcept = default;
PoseOut Fusion::update(const ImuSample& sample) { return s_->update(sample); }
FusionStatus Fusion::status() const { return s_->status(); }
Quatf Fusion::predict(float horizon_s) const {
    if (horizon_s == 0) return s_->out.q;
    return s_->predict(horizon_s);
}
void Fusion::reset() { s_ = std::make_unique<State>(s_->c); }

}  // namespace luma
