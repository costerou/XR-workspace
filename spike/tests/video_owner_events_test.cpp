#include "video_readback.hpp"
#include <iostream>
int main(){xr::VideoReadback r;
 r.reply(true,"");if(r.json()["deband_applied"]!=nullptr||r.json()["paused"]!=nullptr)return 1;
 r.reply(false,"native seek rejected");if(r.command_errors!=1||r.command_error!="native seek rejected")return 2;
 r.flag("deband",true);r.flag("pause",true);r.number("time-pos",12.5);r.number("duration",80);
 if(r.json()["deband_applied"]!=true||r.json()["paused"]!=true||r.json()["position_s"]!=12.5)return 3;
 auto before=r.revision;r.number("time-pos",__builtin_nan(""));if(r.revision!=before)return 4;
 r.reply(false,std::string(800,'x').c_str());if(r.command_error.size()!=256)return 5;
 r.restart();if(r.json()["paused"]!=nullptr||r.json()["position_s"]!=nullptr||r.json()["duration_s"]!=nullptr||r.json()["deband_applied"]!=true)return 6;
 std::cout<<"mpv owner event state: replies never fabricate property readback; errors and reload unknowns preserved\n";
}
