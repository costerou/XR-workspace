// Headless native acceptance probe only; fresh IPC namespace in test runner.
#include "xr/pose.hpp"
#include <chrono>
#include <thread>
#include <cmath>
#include <iostream>
int main(int argc,char**argv){
 if(argc!=2)return 2;using C=std::chrono::steady_clock;auto end=C::now()+std::chrono::seconds(3);
 std::string why;auto feed=xr::PoseFeed::open(argv[1],why);if(!feed)return 3;
 xr::PoseState state;std::size_t seen=0;double now=0;float stamp=-1;
 while(C::now()<end){auto sample=feed->read();if(!sample)break;
  if(sample->device_ms!=stamp){stamp=sample->device_ms;now+=16;state.update(sample,now);++seen;
   if(!state.tracking()||xr::quat_angle(sample->orientation,{0,0,.0998334166f,.9950041653f})>.001)return 4;
   for(auto v:state.view())if(!std::isfinite(v))return 5;
  }std::this_thread::sleep_for(std::chrono::milliseconds(2));
 }
 state.update(std::nullopt,now+101);if(state.tracking())return 6;
 std::cout<<"{\"native_pose_samples\":"<<seen<<",\"stale_hold\":true}\n";
 return seen>5?0:7;
}
