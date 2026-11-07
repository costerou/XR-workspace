// Independent reader. Runs only under QA private namespace harness; no SDK/GPU.
#include "xr/pose.hpp"
#include <chrono>
#include <cmath>
#include <iostream>
#include <memory>
#include <thread>
int main(int argc,char** argv) {
 if(argc!=2)return 2;
 using C=std::chrono::steady_clock;auto end=C::now()+std::chrono::seconds(4);
 std::unique_ptr<xr::PoseFeed> feed; bool seen=false,disabled=false,fov=false;std::string why;
 while(C::now()<end){
  if(!feed)feed=xr::PoseFeed::open(argv[1],why);
  if(feed){auto f=feed->display_fov();fov=f && std::fabs(*f-44.5)<.001;
   auto sample=feed->read();if(sample){seen=true; if(!std::isfinite(sample->device_ms))return 3;
    if(xr::quat_angle(sample->orientation,{0,0,.0998334166f,.9950041653f})>.001)return 4;}
   else if(seen){disabled=true;break;}
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
 }
 std::cout<<"{\"seen\":"<<(seen?"true":"false")<<",\"disabled_after\":"<<(disabled?"true":"false")<<",\"explicit_fov\":"<<(fov?"true":"false")<<"}\n";
 return seen && disabled && fov?0:1;
}
