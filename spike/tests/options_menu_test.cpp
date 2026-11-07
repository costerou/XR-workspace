#include "xr/ipc.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
using J=nlohmann::json;
int main() {
 J p={{"open",true},{"namespace","session"},{"monitor","XR-2"},{"scope","monitor"},{"category","3d"},{"focus",-1},{"scroll",0},{"revision",1},{"status","pending"},{"message",""},{"rows",J::array()}};
 auto accepts=[&](J v){ auto r=xr::decode_control(J{{"schema",1},{"request_id",1},{"run_id","run"},{"session","session"},{"op","options_menu"},{"payload",v}}.dump()+"\n"); return std::holds_alternative<xr::ControlMessage>(r); };
 if(!accepts(p)){std::cerr<<"valid options snapshot rejected\n";return 1;}
 for(int f=-4;f<0;++f){auto q=p;q["focus"]=f;if(!accepts(q))return 7;}
 {auto q=p;q["focus"]=-5;if(accepts(q))return 8;}
 for(auto key:{"extra","focus","scroll","revision","monitor","category","namespace"}) {auto q=p; q[key]= key==std::string("extra")?J(true):J("invalid"); if(accepts(q))return 2;}
 p["rows"]={J{{"id","open"},{"label","Open editor"},{"scope","monitor"},{"kind","action"},{"value",""},{"enabled",true}}};
 if(!accepts(p)){std::cerr<<"minimal action row rejected\n";return 6;}
 auto row=J{{"id","distance"},{"label","Distance"},{"scope","monitor"},{"kind","range"},{"value","1.5"},{"minimum",.1},{"maximum",2},{"step",.1},{"unit","m"},{"enabled",true},{"choices",J::array()}};
 p["rows"]={row}; if(!accepts(p))return 3;
 p["rows"].push_back(row); if(accepts(p))return 4;
 p["rows"]=J::array();
 for(int i=0;i<64;++i){auto r=row;r["id"]=std::to_string(i);r["choices"]=std::vector<std::string>(32,std::string(96,'a'));p["rows"].push_back(r);}
 if(!accepts(p)){std::cerr<<"bounded maximum snapshot rejected\n";return 5;}
 // The Step-Back Band's frame: {"open":bool} and nothing else.
 auto band=[&](J v){auto r=xr::decode_control(J{{"schema",1},{"request_id",1},{"run_id","run"},{"session","session"},{"op","restore_band"},{"payload",v}}.dump()+"\n");return std::holds_alternative<xr::ControlMessage>(r)&&std::holds_alternative<xr::RestoreBandPayload>(std::get<xr::ControlMessage>(r).payload);};
 if(!band(J{{"open",true}})||!band(J{{"open",false}}))return 9;
 for(J bad:{J::object(),J{{"open",1}},J{{"open",true},{"x",1}}})if(band(bad))return 9;
 return 0;
}
