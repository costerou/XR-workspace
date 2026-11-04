#include "owner_readback.hpp"
#include <iostream>
int main(){using J=nlohmann::json;xr::OwnerReadbackStream stream;
 J s={{"video",nullptr},{"immersive",nullptr},{"outputs",J::array()}};
 auto first=stream.next(s,0);if(!first||J::parse(*first)["owner_readback"]!=s)return 1;
 if(stream.next(s,10)||stream.next(s,999))return 2;
 if(!stream.next(s,1000))return 3;
 s["outputs"]={J{{"name","XR-3"},{"ready",false},{"width",0},{"height",0}}};
 if(!stream.next(s,1050))return 4;
 s["video"]=std::string(16384,'x');if(stream.next(s,2000))return 5;
 stream.resend();s["video"]=nullptr;if(!stream.next(s,2001))return 6;
 std::cout<<"bounded native owner snapshot: change, refresh, retry and overlimit rejection passed\n";
}
