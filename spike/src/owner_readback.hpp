#pragma once
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
namespace xr {
// Bounded status transport, independent of owner state. Receipt of this message
// is not command completion; callers compare typed authoritative values.
class OwnerReadbackStream {
 std::string last_;double sent_ms_=-1e9;
public:
 static constexpr std::size_t limit=16384;
 std::optional<std::string> next(const nlohmann::json& snapshot,double now){
  std::string line=nlohmann::json{{"owner_readback",snapshot}}.dump();
  if(line.size()+1>limit)return std::nullopt;
  if(now-sent_ms_<50 || (line==last_ && now-sent_ms_<1000))return std::nullopt;
  last_=line;sent_ms_=now;return line;
 }
 bool due(double now)const{return now-sent_ms_>=50;}
 void resend(){last_.clear();sent_ms_=-1e9;}
};
}
