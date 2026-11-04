// OFFLINE ONLY. Private IPC namespace required by test wrapper; no SDK/device/compositor.
#include "xr/pose.hpp"
#include "luma/publish.hpp"
#include <array>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <unistd.h>
#define REQUIRE(x) do {if (!(x)) throw std::runtime_error(#x);} while(0)
int main() {
    char dir[]="/tmp/pro2_native_XXXXXX"; REQUIRE(mkdtemp(dir));
    struct Cleanup { const char* dir; ~Cleanup(){std::filesystem::remove_all(dir);} } cleanup{dir};
    luma::PublisherOptions opt;
    opt.private_segments=true; opt.history_step=1; opt.mount_pitch_deg=0;
    opt.display_fov_deg=std::numeric_limits<float>::quiet_NaN(); // unavailable, NOT Pro2 optics
    opt.look_ahead_cfg={0,0,0,0}; // no prediction claim
    opt.prefix=std::string(dir)+"/fixture_";
    std::string why; auto publisher=luma::Publisher::open(opt,why); REQUIRE(publisher);
    auto reader=xr::PoseFeed::open(opt.prefix,why); REQUIRE(reader);
    REQUIRE(!reader->display_fov()); REQUIRE(!reader->read());
    xr::PoseState state; std::array<float,16> packet{}; double host=0; std::size_t count=0;
    std::optional<xr::FeedSample> last;
    while(std::cin.read(reinterpret_cast<char*>(packet.data()),sizeof(packet))) {
        REQUIRE(std::cin.read(reinterpret_cast<char*>(&host),sizeof(host)));
        const luma::PoseOut pose{{packet[3],packet[0],packet[1],packet[2]},
            static_cast<std::uint64_t>(double(packet[12])*1e6),static_cast<std::uint32_t>(count),true};
        REQUIRE(publisher->publish(pose)); last=reader->read(); REQUIRE(last);
        REQUIRE(xr::quat_angle(last->orientation,{packet[0],packet[1],packet[2],packet[3]})<.001f);
        REQUIRE(std::fabs(last->device_ms-packet[12])<.005f);
        for(int i=0;i<2;++i) {
            REQUIRE(xr::quat_angle(last->older[i],{packet[4*(i+1)],packet[4*(i+1)+1],packet[4*(i+1)+2],packet[4*(i+1)+3]})<.001f);
            REQUIRE(std::fabs(last->older_ms[i]-packet[13+i])<.005f);
        }
        state.update(last,host); REQUIRE(state.tracking());
        for(float v:state.view()) REQUIRE(std::isfinite(v));
        ++count;
    }
    REQUIRE(count>10 && last);
    state.recenter(); last->device_ms+=16; host+=16; state.update(last,host);
    REQUIRE(xr::quat_angle(state.relative(),xr::Quat{})<.001f);
    const auto held=state.view(); state.update(std::nullopt,host+101);
    REQUIRE(!state.tracking() && state.view()==held);
    // Reader disabled/history contracts are independent of calibrated hardware readiness.
    publisher->pause(); REQUIRE(!reader->read());
    publisher.reset(); REQUIRE(!reader->read());
    std::cout<<"{\"native_samples\":"<<count<<",\"unknown_fov_unavailable\":true,\"recenter\":true,\"stale_hold\":true,\"private_teardown\":true}\n";
}
