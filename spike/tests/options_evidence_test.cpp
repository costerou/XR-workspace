#include "options_evidence.hpp"
#include "xr/ipc.hpp"
#include <iostream>
int main(){using J=nlohmann::json;xr::OptionsEvidence evidence;xr::OptionsMenuState native;
 xr::ControlStream stream("run","session",true,[&](xr::ControlMessage& m){if(m.op==xr::ControlOp::options_menu){native=std::get<xr::OptionsMenuState>(m.payload);evidence.accepted();}},[](const xr::ControlRefusal&){});
 J p={{"open",true},{"namespace","session"},{"monitor","XR-2"},{"scope","monitor"},{"category","placement"},{"focus",0},{"list",-1},{"scroll",38},{"revision",9},{"status","pending"},{"message","private value must not be logged"},{"rows",J::array({J{{"id","editor"},{"label","private label"},{"scope","monitor"},{"kind","text"},{"value","private typed value"},{"enabled",true}}})}};
 auto frame=[](const J& p,unsigned id){return J{{"schema",1},{"run_id","run"},{"session","session"},{"request_id",id},{"op","options_menu"},{"payload",p}}.dump()+"\n";};
 if(!stream.feed(frame(p,1)))return 1;
 auto report=evidence.report(native);if(report["updates"]!=1||report["drawn_frames"]!=0||!report["last_drawn"].is_null())return 2;
 // The stream rejects malformed display frames without replacing the accepted snapshot.
 auto invalid=p;invalid["unknown"]=true;if(!stream.feed(frame(invalid,2))||stream.refused()!=1)return 3;
 if(evidence.report(native)["updates"]!=1||native.revision!=9)return 4;
 evidence.drawn(native);p["open"]=false;p["revision"]=10;if(!stream.feed(frame(p,3)))return 5;
 report=evidence.report(native);if(report["updates"]!=2||report["open"]!=false||report["last_drawn"]["revision"]!=9||report["drawn_frames"]!=1)return 6;
 if(report.dump().find("private")!=std::string::npos)return 7;
 std::cout<<"options diagnostics: real decoder isolation, receipt distinct from draw, no user text logged\n";
}
