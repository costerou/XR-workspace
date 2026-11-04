#include "xr/pose.hpp"
#include "xr/stabilization.hpp"
#include "xr/predict.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
static void check(bool ok, const char* why) { if (!ok) { std::fprintf(stderr,"FAIL: %s\n",why); std::exit(1); } }
static xr::Quat turn(double deg) { double a=deg*3.141592653589793/360; return {float(std::sin(a)*0.6),0,float(std::sin(a)*0.8),float(std::cos(a))}; }
static double angle(xr::Quat q) { return 2*std::atan2(q.x*0.6+q.z*0.8,q.w)*180/3.141592653589793; }
int main() {
    for (double dt : {1., 1000./120, 1000./60}) for (int strength : {25,60,100}) {
        xr::Stabilizer f; f.strength(strength);
        double raw=0, filtered=0;
        for (int i=0;i<60000/dt;++i) {
            double t=i*dt, a=.025*std::sin(t*.001*2*3.141592653589793*8)+.008*std::sin(t*.001*2*3.141592653589793*19);
            auto q=f.update(turn(a),t);
            if (t>1000) { raw+=a*a; filtered+=std::pow(angle(q),2); }
        }
        double reduction=1-std::sqrt(filtered/raw), required=strength==25?.25:strength==60?.45:.60;
        std::printf("dt %.3f strength %d jitter reduction %.2f%%\n",dt,strength,100*reduction);
        check(reduction>=required,"still tremor reduction");
        for (double speed : {30.,90.,300.}) for (double onset : {200.,200.1,203.,207.,211.}) {
            f.reset(); double worst=0;
            for(int i=0;i<1000/dt;++i) {
                double t=i*dt, a=speed*std::max(0.,std::min(t-onset,300.))/1000;
                auto q=f.update(turn(a),t); double out=angle(q);
                if(t>onset && t<=onset+300) worst=std::max(worst,std::abs(a-out)/speed*1000);
                check(out<=a+1e-4,"no overshoot");
                if(t>onset+300+dt && std::abs(out-a)>=1e-4) {
                    std::fprintf(stderr,"stop dt=%g strength=%d speed=%g onset=%g t=%g target=%g output=%.9g\n",dt,strength,speed,onset,t,a,out);
                    check(false,"no stop tail");
                }
            }
            check(worst<2.,"deliberate turn lag under 2 ms, including onset");
        }
        f.reset();
        for(int i=0;i<600000/dt;++i) {
            auto q=f.update(turn(23),i*dt);
            check(std::abs(angle(q)-23)<1e-4,"ten minute stationary drift");
        }
        f.reset(); double sum = 0; int count = 0;
        for(int i=0;i<600000/dt;++i) {
            const double t=i*dt;
            const auto q=f.update(turn(23+.025*std::sin(t*.001*2*3.141592653589793*8)), t);
            if(t>540000) { sum+=angle(q)-23; ++count; }
        }
        check(std::abs(sum/count)<.0001,"ten minute noisy still data adds no bias");
        // Recenter removes all filter history before the reference is taken.
        xr::PoseState pose;
        xr::FeedSample sample; sample.orientation=f.update(turn(24),600000); sample.device_ms=600000;
        pose.update(sample,600000); pose.recenter(); f.reset();
        sample.orientation=f.update(turn(25),600001); sample.device_ms=600001;
        pose.update(sample,600001);
        check(xr::quat_angle(pose.relative(),xr::Quat{})<1e-5,"recenter is immediate");
        f.strength(0); check(std::abs(angle(f.update(turn(42),600001))-42)<1e-4,"live Off is immediate");
        f.strength(100); check(std::abs(angle(f.update(turn(43),600002))-43)<1e-4,"live setting reset");
        auto q=f.update(turn(44),1); check(std::abs(angle(q)-44)<1e-4,"clock restart resets");
        q=f.update(turn(50),1); check(std::abs(angle(q)-44)<1e-4,"duplicates do not advance");
        q=turn(44); q={-q.x,-q.y,-q.z,-q.w};
        check(std::abs(std::abs(angle(f.update(q,2)))-44)<1e-4,"quaternion sign continuity");
    }
    for(int strength : {25,60,100}) {
        xr::Stabilizer f; f.strength(strength); double before=0,after=0;
        auto tremor=[](double ms) { return turn(.025*std::sin(ms*.001*2*3.141592653589793*8)); };
        for(int t=32;t<10000;++t) {
            xr::PoseHistory h{{tremor(t),tremor(t-16),tremor(t-32)},{float(t),float(t-16),float(t-32)}};
            auto q=f.update(h.q[0],t); const auto omega=xr::angular_velocity(h);
            check(bool(omega),"valid prediction history");
            auto raw=xr::quat_mul(h.q[0],xr::prediction_delta(*omega,12,5));
            auto filtered=xr::quat_mul(q,xr::prediction_delta(*omega,12*f.prediction_gain(),5));
            if(t>1000) { before+=std::pow(angle(raw),2); after+=std::pow(angle(filtered),2); }
        }
        check(after<before*.5,"prediction does not amplify stationary jitter back into the view");
        f.reset(); f.update(turn(0),0); f.update(turn(.03),1);
        check(f.prediction_gain()==1,"deliberate turn preserves prediction");
        f.update(turn(.03),2);
        check(f.prediction_gain()==0,"stop does not extrapolate old motion");
    }
}
