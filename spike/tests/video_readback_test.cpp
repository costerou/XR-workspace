// CPU-only: closed VideoMode has no player, sphere, GL context, device or media.
#include "video_mode.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
int main(){xr::VideoDefaults defaults;defaults.set({xr::VideoFilter::sharp,true});
 auto off=nlohmann::json::parse(defaults.report());
 if(off["state"]!="off"||off["quality_readback"]["filter_requested"]!="sharp"||off["quality_readback"]["deband_requested"]!=true||off["quality_readback"]["filter_applied"]!=nullptr||off["player"]!=nullptr)return 6;
 xr::VideoMode video;xr::VideoFormat f{xr::VideoProjection::fisheye,xr::VideoStereo::tb,205};video.set_format(f);video.set_quality({xr::VideoFilter::nearest,true});
 auto j=nlohmann::json::parse(video.report());
 if(!j.contains("format_state")||!j.contains("playback")||!j.contains("quality_readback")){std::cerr<<"native typed owner readback missing\n";return 1;}
 if(j["format_state"]!=nlohmann::json{{"projection","fisheye"},{"stereo","tb"},{"fov",205}})return 2;
 if(j["playback"]["state"]!="off"||j["playback"]["position_s"]!=nullptr||j["quality_readback"]["deband_applied"]!=nullptr||j["quality_readback"]["filter_applied"]!=nullptr)return 3;
 if(j["quality_readback"]["filter_requested"]!="nearest"||j["quality_readback"]["deband_requested"]!=true)return 4;
 video.close();if(nlohmann::json::parse(video.report())["playback"]["state"]!="off")return 5;
 std::cout<<"typed video state preserves unknown applied quality when no player exists\n";
}
