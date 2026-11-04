#pragma once
#include "xr/pose.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
// One shared, causal SO(3) low-pass. No Euler angles, integration, or extrapolation.
// Motion opens the cutoff immediately; above 3 deg/s a 0.045 degree residual cap bounds extra
// lag to 1.5 ms at 30 deg/s even on a partially sampled onset. At >=20 deg/s the
// pose and existing prediction pass through exactly. Device time, not frame time.
class Stabilizer {
public:
    void strength(int value) {
        value = std::clamp(value, 0, 100);
        if (value != strength_) { strength_ = value; reset(); }
    }
    void reset() { valid_ = false; prediction_gain_ = 1; }
    float prediction_gain() const { return prediction_gain_; }
    Quat update(Quat input, double ms) {
        const auto normalized = quat_normalized(input);
        if (!normalized || !std::isfinite(ms)) return valid_ ? output_ : Quat{};
        Quat q = *normalized;
        if (valid_ && q.x*previous_.x + q.y*previous_.y + q.z*previous_.z + q.w*previous_.w < 0)
            q = {-q.x, -q.y, -q.z, -q.w};
        const double dt = (ms - ms_) / 1000.;
        if (!valid_ || dt < 0 || dt > .1) {
            valid_ = true; ms_ = ms; previous_ = output_ = remember(q); speed_ = 0;
            prediction_gain_ = 1;
            return strength_ == 0 ? input : output_;
        }
        if (dt == 0) return strength_ == 0 ? input : output_;
        double speed = distance(previous_, q) / dt;
        // Acceleration only opens a moving signal: noisy near-rest derivatives
        // must not disable smoothing. There is no delayed velocity low-pass.
        double acceleration = std::max(0., (speed - speed_) / dt);
        double motion = std::clamp((speed - 3.) / 17., 0., 1.);
        if (speed > 3.) motion = std::max(motion, std::clamp(acceleration / 4000., 0., 1.));
        const double amount = std::min(1., double(strength_) / 25.);
        prediction_gain_ = float(1. - amount * (1. - motion));
        double cutoff = 2. * std::pow(2., -double(strength_) / 25.);
        double alpha = 1. - std::exp(-2. * 3.141592653589793 * cutoff * dt);
        alpha += (1-alpha)*motion;
        alpha = 1. - amount * (1. - alpha);
        double residual = distance(output_, q);
        // A still head may wander inside a small 0.2-degree envelope. Once
        // movement begins, shrink that envelope immediately to the lag bound.
        const double cap = speed > 3. ? .045 : .2;
        if (residual > cap) alpha = std::max(alpha, 1. - cap/residual);
        // Exact rest after a deliberate turn: no trailing filter memory.
        // Re-interpolating an already identical float quaternion can accumulate
        // rounding error at tiny alpha. Keep exact rest exact, including after stops.
        if (residual < 1e-6 || (speed < 1e-5 && speed_ > .05)) alpha = 1;
        output_ = strength_ == 0 || alpha >= 1 ? remember(q) : blend(q, alpha);
        previous_ = q; ms_ = ms; speed_ = speed;
        return strength_ == 0 ? input : output_;
    }
private:
    Quat remember(Quat q) {
        state_ = {q.x,q.y,q.z,q.w};
        const double n=std::hypot(std::hypot(state_[0],state_[1]),std::hypot(state_[2],state_[3]));
        for(auto& v:state_) v/=n;
        return q;
    }
    Quat blend(Quat q, double alpha) {
        // Keep the accumulator in double precision. A float accumulator at a
        // 1 kHz input and very low cutoff can otherwise acquire rounding bias.
        std::array<double,4> target{q.x,q.y,q.z,q.w};
        double n=0, dot=0;
        for(auto v:target) n+=v*v;
        n=std::sqrt(n);
        for(int i=0;i<4;++i) { target[i]/=n; dot+=state_[i]*target[i]; }
        if(dot<0) { for(auto& v:target) v=-v; dot=-dot; }
        double a=1-alpha,b=alpha;
        if(dot<.9995) {
            const double theta=std::acos(std::clamp(dot,0.,1.)), sine=std::sin(theta);
            a=std::sin((1-alpha)*theta)/sine; b=std::sin(alpha*theta)/sine;
        }
        n=0;
        for(int i=0;i<4;++i) { state_[i]=a*state_[i]+b*target[i]; n+=state_[i]*state_[i]; }
        n=std::sqrt(n);
        for(auto& v:state_) v/=n;
        return {float(state_[0]),float(state_[1]),float(state_[2]),float(state_[3])};
    }
    static double distance(Quat a, Quat b) {
        // atan2 retains micro-angle precision; acos(float dot) rounds tremor to zero.
        const auto d = quat_mul(quat_conj(a), b);
        return 2.*std::atan2(std::hypot(double(d.x),double(d.y),double(d.z)),std::abs(double(d.w)))*180./3.141592653589793;
    }
    int strength_ = 25;
    bool valid_ = false;
    double ms_ = 0, speed_ = 0;
    float prediction_gain_ = 1;
    Quat previous_{}, output_{};
    std::array<double,4> state_{0,0,0,1};
};
}
