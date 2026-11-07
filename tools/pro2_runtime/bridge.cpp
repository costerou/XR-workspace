// PRO2 bounded supervised-test owner. SDK calls and Publisher writes on main thread only.
#include "luma/publish.hpp"
#include <openssl/evp.h>
#include <dlfcn.h>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <sys/stat.h>
#include <ifaddrs.h>
namespace fs=std::filesystem;
using Clock=std::chrono::steady_clock;
constexpr const char* kHash="8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e";
// XRLinuxDriver look_ahead_cfg: constant ms, frametime multiplier, scanline ms, cap ms. The cap bounds the prediction; an
// all-zero config has none, and the renderer refuses an unbounded one (predict.cpp look_ahead_from), which is why the first
// live test presented 10691 frames unpredicted with prediction enabled. No panel constant: the Pro 2's is unmeasured.
constexpr std::array<float,4> kLookAhead{0,0,0,40};
// viture_protocol_public.h VITURE_IMU_FREQUENCY_*: not USB command bytes. 60 Hz unless a run asks; each still has to pass the
// SDK's own capability query for the Pro 2 in pose mode, and a refusal stops the run rather than dropping to 60.
int rate_code(int hz){switch(hz){case 60:return 0;case 90:return 1;case 120:return 2;case 240:return 3;default:return -1;}}
volatile std::sig_atomic_t stopped=0;
void signal_stop(int){stopped=1;}
// Device-time gaps between samples, counted: under 7, 7..9.5, 9.5..13, 13..18, 18..25, 25 ms and over. What the desk test reads.
void note_gap(std::array<unsigned long long,6>& counts,double ms){int i=ms<7?0:ms<9.5?1:ms<13?2:ms<18?3:ms<25?4:5;++counts[i];}
constexpr double kRouteSeconds=12*3600.0;  // a panel session: Stop ends it first; the bound is the same fail-closed deadline
struct Sample {std::array<float,7> v;std::uint64_t ts;Clock::time_point host;};
std::array<Sample,256> ring;std::size_t head=0,tail=0,count=0;std::mutex mutex;
std::atomic<bool> accepting{false},overflow{false};
void callback(float* v,std::uint64_t ts){
 if(!accepting.load())return;
 std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
 if(!lock.owns_lock()||!v||count==ring.size()){overflow=true;return;}
 Sample s{};for(int i=0;i<7;++i)s.v[i]=v[i];s.ts=ts;s.host=Clock::now();
 ring[tail]=s;tail=(tail+1)%ring.size();++count;
}
std::string read(const fs::path& p){std::ifstream f(p);std::string s;std::getline(f,s);if(!f)throw std::runtime_error("identity-unreadable");return s;}
std::string identity(const std::string& root){
 std::string fingerprint;int found=0;
 for(const auto& d:fs::directory_iterator(root)){
  if(!fs::exists(d.path()/"idVendor"))continue;
  if(read(d.path()/"idVendor")!="35ca")continue;
  ++found;
  if(read(d.path()/"idProduct")!="1301"||read(d.path()/"product")!="VITURE PRO 2 XR Glasses")throw std::runtime_error("identity-mismatch");
  fingerprint=d.path().filename().string()+":"+read(d.path()/"busnum")+":"+read(d.path()/"devnum");
 }
 if(found!=1)throw std::runtime_error("exclusive-identity-required");return fingerprint;
}
std::string hash_fd(int fd){
 if(lseek(fd,0,SEEK_SET)<0)throw std::runtime_error("sdk-fd");
 EVP_MD_CTX* ctx=EVP_MD_CTX_new();if(!ctx)throw std::runtime_error("hash-init");EVP_DigestInit_ex(ctx,EVP_sha256(),nullptr);
 char buffer[8192];ssize_t n;while((n=::read(fd,buffer,sizeof buffer))>0)EVP_DigestUpdate(ctx,buffer,static_cast<std::size_t>(n));
 unsigned char result[32];unsigned length=0;EVP_DigestFinal_ex(ctx,result,&length);EVP_MD_CTX_free(ctx);
 if(n<0)throw std::runtime_error("hash-read");std::string hex;char b[3];for(unsigned i=0;i<length;++i){std::snprintf(b,sizeof b,"%02x",result[i]);hex+=b;}return hex;
}
struct Api {
 void* lib=nullptr;void* handle=nullptr;
 template<class T>T get(const char* name){auto p=dlsym(lib,(std::string("xr_device_provider_")+name).c_str());if(!p)throw std::runtime_error("sdk-symbol");return reinterpret_cast<T>(p);}
 using Call=int(*)(void*);
 // Do not dlclose on a failed stop: callbacks remain valid until process exit.
};
int main(int argc,char** argv){
 int sdk_fd=-1,status_fd=-1,pose_hz=60;double projection=0,seconds=0,max_seconds=300;bool approved=false;std::string prefix,root="/sys/bus/usb/devices",expected=kHash;
 try{
  for(int i=1;i<argc;++i){std::string a=argv[i];if(a=="--supervised-pro2-test"){approved=true;continue;}if(a=="--panel-route"){max_seconds=kRouteSeconds;continue;}if(i+1>=argc)throw std::runtime_error("arguments");std::string v=argv[++i];
   if(a=="--sdk-fd")sdk_fd=std::stoi(v);else if(a=="--status-fd")status_fd=std::stoi(v);else if(a=="--prefix")prefix=v;else if(a=="--projection")projection=std::stod(v);else if(a=="--seconds")seconds=std::stod(v);else if(a=="--pose-rate"){std::size_t used=0;pose_hz=std::stoi(v,&used);if(used!=v.size())throw std::runtime_error("arguments");}
#ifdef PRO2_SYNTHETIC
   else if(a=="--fixture-root")root=v;else if(a=="--fixture-hash")expected=v;
#endif
   else throw std::runtime_error("unknown-argument");
  }
  if(!approved||sdk_fd<3||status_fd<3||!std::isfinite(projection)||projection<44.5||projection>50||rate_code(pose_hz)<0||!std::isfinite(seconds)||seconds<=0||seconds>max_seconds||prefix.empty()||prefix[0]!='/'||prefix=="/tmp/shader_runtime_")throw std::runtime_error("explicit-supervised-scope-required");
  struct stat sdk_stat{},status_stat{};
  if(sdk_fd==status_fd||fstat(sdk_fd,&sdk_stat)||!S_ISREG(sdk_stat.st_mode)||fstat(status_fd,&status_stat)||(!S_ISFIFO(status_stat.st_mode)&&!S_ISREG(status_stat.st_mode)))throw std::runtime_error("invalid-descriptor-type");
 }catch(...){return 2;}
 std::signal(SIGINT,signal_stop);std::signal(SIGTERM,signal_stop);std::signal(SIGPIPE,SIG_IGN);
 const auto began=Clock::now();std::array<unsigned long long,6> gaps{};Api sdk;std::unique_ptr<luma::Publisher> publisher;bool created=false,open_attempt=false;int cleanup_errors=0;std::size_t samples=0;std::string error;bool ready=false;
 try{
  struct ifaddrs* interfaces=nullptr;
  if(getifaddrs(&interfaces))throw std::runtime_error("network-namespace");
  bool isolated=true;for(auto* p=interfaces;p;p=p->ifa_next)if(std::string(p->ifa_name)!="lo")isolated=false;
  freeifaddrs(interfaces);if(!isolated)throw std::runtime_error("network-not-isolated");
  const auto fingerprint=identity(root);if(hash_fd(sdk_fd)!=expected)throw std::runtime_error("sdk-hash");
  sdk.lib=dlopen(("/proc/self/fd/"+std::to_string(sdk_fd)).c_str(),RTLD_NOW|RTLD_LOCAL);if(!sdk.lib)throw std::runtime_error("sdk-load");
  // Resolve every lifecycle function before create so missing cleanup API cannot strand a handle.
  auto create=sdk.get<void*(*)(int)>("create");auto destroy=sdk.get<void(*)(void*)>("destroy");(void)destroy;
  for(auto name:{"start","stop","shutdown","get_device_type"})sdk.get<Api::Call>(name);
  auto initialize=sdk.get<int(*)(void*,const char*,const char*)>("initialize");
  auto reg=sdk.get<int(*)(void*,void(*)(float*,std::uint64_t))>("register_imu_pose_callback");
  auto open=sdk.get<int(*)(void*,std::uint8_t,std::uint8_t)>("open_imu");sdk.get<int(*)(void*,std::uint8_t)>("close_imu");
  auto firmware=sdk.get<int(*)(void*,char*,int*)>("get_glasses_version");
  if(sdk.get<int(*)(int)>("is_product_id_valid")(0x1301)!=1||sdk.get<int(*)(int,int,int)>("is_product_support_imu_frequency")(0x1301,1,rate_code(pose_hz))!=1)throw std::runtime_error("capability");
  if(identity(root)!=fingerprint)throw std::runtime_error("identity-changed");
  sdk.handle=create(0x1301);if(!sdk.handle)throw std::runtime_error("create");created=true;
  if(sdk.get<Api::Call>("get_device_type")(sdk.handle)!=1)throw std::runtime_error("family");
  if(initialize(sdk.handle,nullptr,nullptr))throw std::runtime_error("initialize");
  if(sdk.get<Api::Call>("start")(sdk.handle))throw std::runtime_error("start");
  if(reg(sdk.handle,callback))throw std::runtime_error("register");
  char version[256]{};int length=sizeof version;
  if(firmware(sdk.handle,version,&length)||length<=0||length>256||!memchr(version,0,sizeof version)||std::string(version)!="30.0.00.002_20260804"||(length!=int(std::strlen(version))&&length!=int(std::strlen(version)+1)))throw std::runtime_error("firmware");
  luma::PublisherOptions options;options.prefix=prefix;options.private_segments=true;options.history_step=luma::history_step_for(static_cast<std::uint32_t>(pose_hz));options.mount_pitch_deg=0;options.display_fov_deg=static_cast<float>(projection);options.look_ahead_cfg=kLookAhead;
  std::string why;publisher=luma::Publisher::open(options,why);if(!publisher)throw std::runtime_error("publisher-open");
  accepting=true;open_attempt=true;if(open(sdk.handle,1,static_cast<std::uint8_t>(rate_code(pose_hz))))throw std::runtime_error("open");
  const auto start=Clock::now();auto last=Clock::now();std::uint64_t prev=0;bool have=false;int valid=0;
  while(!stopped&&std::chrono::duration<double>(Clock::now()-start).count()<seconds){
   if(identity(root)!=fingerprint)throw std::runtime_error("identity-changed");if(overflow)throw std::runtime_error("queue-overflow-or-null");
   Sample s{};bool got=false;{std::lock_guard<std::mutex> lock(mutex);if(count){s=ring[head];head=(head+1)%ring.size();--count;got=true;}}
   if(!got){if(Clock::now()-last>std::chrono::milliseconds(ready?100:3000))throw std::runtime_error(ready?"stale":"no-pose");std::this_thread::sleep_for(std::chrono::milliseconds(2));continue;}
   last=s.host;if(Clock::now()-s.host>std::chrono::milliseconds(100))throw std::runtime_error("queued-stale");
   for(float v:s.v)if(!std::isfinite(v))throw std::runtime_error("nonfinite");
   if(have&&s.ts<=prev)throw std::runtime_error("clock-reset-wrap-duplicate");if(have)note_gap(gaps,(s.ts-prev)/1e6);prev=s.ts;have=true;
   const double norm=std::sqrt(double(s.v[3])*s.v[3]+double(s.v[4])*s.v[4]+double(s.v[5])*s.v[5]+double(s.v[6])*s.v[6]);if(std::fabs(norm-1)>.05)throw std::runtime_error("quaternion-norm");
   ++valid;if(valid<3)continue;
   luma::PoseOut pose{{float(s.v[3]/norm),float(-s.v[6]/norm),float(-s.v[4]/norm),float(s.v[5]/norm)},s.ts,static_cast<std::uint32_t>(samples),true};
   if(!publisher->publish(pose))throw std::runtime_error("publish");++samples;
   if(!ready){ready=true;dprintf(status_fd,"{\"event\":\"ready\",\"projection_diagonal_approx\":%.3f,\"pose_rate_hz\":%d}\n",projection,pose_hz);}
  }
  if(!ready&&!stopped)throw std::runtime_error("no-ready");
 }catch(const fs::filesystem_error&){error="identity-filesystem";}catch(const std::exception& e){error=e.what();}
 accepting=false;if(publisher)publisher->pause();
 if(created){
  if(open_attempt){try{if(sdk.get<int(*)(void*,std::uint8_t)>("close_imu")(sdk.handle,1))++cleanup_errors;}catch(...){++cleanup_errors;}}
  for(auto name:{"stop","shutdown"}){try{if(sdk.get<Api::Call>(name)(sdk.handle))++cleanup_errors;}catch(...){++cleanup_errors;}}
  try{sdk.get<void(*)(void*)>("destroy")(sdk.handle);}catch(...){++cleanup_errors;}
 }
 publisher.reset();dprintf(status_fd,"{\"event\":\"done\",\"ready\":%s,\"samples\":%zu,\"seconds\":%.3f,\"pose_rate_hz\":%d,\"gap_ms_counts\":[%llu,%llu,%llu,%llu,%llu,%llu],\"cleanup_errors\":%d,\"error\":\"%s\"}\n",ready?"true":"false",samples,std::chrono::duration<double>(Clock::now()-began).count(),pose_hz,gaps[0],gaps[1],gaps[2],gaps[3],gaps[4],gaps[5],cleanup_errors,error.empty()?"":error.c_str());
 return error.empty()&&!cleanup_errors?0:1;
}
