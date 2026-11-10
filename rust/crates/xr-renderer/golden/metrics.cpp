// Writes golden/metrics.txt from the C++ Metrics (spike/src/metrics.cpp). From the repository root:
//   g++ -std=c++20 -Ispike/include rust/crates/xr-renderer/golden/metrics.cpp spike/src/metrics.cpp -o /tmp/metrics-golden && /tmp/metrics-golden > rust/crates/xr-renderer/golden/metrics.txt
// metrics.rs replays the same scenarios and must print the same lines.
#include "xr/metrics.hpp"
#include <cstdio>
using namespace xr;
const std::uint64_t NS=1000000000ull;
void pres(Metrics& m,std::uint64_t t,std::uint64_t ns,std::uint64_t seq,bool sel=true){std::uint64_t s=ns/NS;m.presented(t,s>>32,std::uint32_t(s),std::uint32_t(ns%NS),8333333,seq>>32,std::uint32_t(seq),7,sel);}
int main(){
 {Metrics m; std::printf("%s\n",m.report().c_str());}
 {Metrics m; m.bind(2); m.clock(1); std::uint64_t st=100*NS; for(std::uint64_t i=0;i<121;i++){auto t=*m.submit();pres(m,t,st+i*8333333,i+1);} auto t=*m.submit(); m.discarded(t); m.submit(); m.abandon();
  std::printf("%s\n%s\n",m.report().c_str(),m.status_json(double(st+120*8333333)/1e6).c_str());}
 {Metrics m; m.bind(1); m.clock(1); std::uint64_t t=NS; auto f=*m.submit(); pres(m,f,t,1); for(std::uint64_t i=0;i<100;i++){t+=50000000+i; auto k=*m.submit(); pres(m,k,t,i+2);} 
  auto k=*m.submit(); pres(m,k,t+1000,200,false); k=*m.submit(); pres(m,k,t+2*NS,201); m.clock(4);
  std::printf("%s\n%s\n",m.report().c_str(),m.status_json(double(t)/1e6).c_str());}
}
