#pragma once
// State updated only by mpv property observations and command replies. CPU-testable
// event seam used directly by VideoPlayer::Impl, not an alternate player.
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <cmath>
namespace xr {
struct VideoReadback {
 std::optional<bool> paused, deband;
 std::optional<double> position, duration;
 std::uint64_t revision=0, command_replies=0, command_errors=0;
 std::string command_error;
 void flag(const std::string& name,bool value){if(name=="pause")paused=value;else if(name=="deband")deband=value;else return;++revision;}
 void number(const std::string& name,double value){if(!std::isfinite(value))return;if(name=="time-pos")position=std::max(0.,value);else if(name=="duration")duration=std::max(0.,value);else return;++revision;}
 void reply(bool success,const char* detail){++command_replies;if(success)return;++command_errors;command_error.clear();for(const unsigned char* p=reinterpret_cast<const unsigned char*>(detail?detail:"native command failed");*p && command_error.size()<256;++p)if(*p>=32&&*p<127)command_error+=char(*p);}
 void restart(){paused.reset();position.reset();duration.reset();++revision;}
 nlohmann::json json()const{auto optional=[](const auto& v)->nlohmann::json{return v?nlohmann::json(*v):nlohmann::json(nullptr);};return {{"paused",optional(paused)},{"position_s",optional(position)},{"duration_s",optional(duration)},{"deband_applied",optional(deband)},{"revision",revision},{"command_replies",command_replies},{"command_errors",command_errors},{"command_error",command_error}};}
};
}
