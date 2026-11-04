/* Independent synthetic SDK ABI. No vendor library, USB/HID or compositor deps.
 * Only execute this fixture from the QA harness inside private namespaces. */
#define _POSIX_C_SOURCE 200809L
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdatomic.h>

static void (*pose_cb)(float *, uint64_t);
static pthread_t owner, worker;
static int created, started_thread;
static atomic_int running;
static const char *scenario(void) { const char *s=getenv("PRO2_QA_CASE"); return s?s:"normal"; }
static int is(const char *s) { return strcmp(scenario(),s)==0; }
static void trace(const char *name) {
    const char *p=getenv("PRO2_QA_TRACE"); if(!p)return;
    FILE *f=fopen(p,"a"); if(!f)return;
    fprintf(f,"%s%s\n",name,created && !pthread_equal(owner,pthread_self())?":WRONG_OWNER":""); fclose(f);
}
__attribute__((constructor)) static void fixture_loaded(void) { trace("load"); }
static int result(const char *name) { trace(name); return is(name)?-37:0; }
static uint64_t now_ns(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts); return (uint64_t)ts.tv_sec*1000000000ULL+(uint64_t)ts.tv_nsec; }
static void emit(unsigned i, uint64_t origin) {
    float v[7]={0,0,0,0.9950041653f,0,0.0998334166f,0}; uint64_t stamp=origin+(uint64_t)i*16000000ULL;
    if(i>=4 && is("duplicate"))stamp=origin+3*16000000ULL;
    if(i>=4 && is("clock_reverse"))stamp=origin;
    if((i>=4 && is("nonfinite")) || (i==3 && is("ready_then_bad")))v[3]=NAN;
    if(i>=4 && is("bad_norm"))v[3]=2;
    if(pose_cb)pose_cb(is("null_callback")?NULL:v,stamp);
}
static void *produce(void *arg) {
    (void)arg; uint64_t origin=now_ns(); unsigned i=0;
    while(atomic_load(&running)) {
        if(!is("no_pose") && !(is("stale_after_ready") && i>=5) && !(is("two_pose") && i>=2))emit(i++,origin);
        struct timespec wait={0,16000000}; nanosleep(&wait,NULL);
    }
    return NULL;
}
void *xr_device_provider_create(int pid) { owner=pthread_self(); created=1; trace("create"); return is("null_create")||pid!=0x1301?NULL:&created; }
int xr_device_provider_is_product_id_valid(int pid) { trace("cap_pid"); return !is("unsupported_pid")&&pid==0x1301; }
int xr_device_provider_is_product_support_imu_frequency(int pid,int mode,int rate) { trace("cap_rate"); return !is("unsupported_rate")&&pid==0x1301&&mode==1&&rate==0; }
int xr_device_provider_get_device_type(void *h) { (void)h; trace("family"); return is("wrong_family")?2:1; }
int xr_device_provider_initialize(void *h,const char *cfg,const char *cache) { (void)h;(void)cfg;(void)cache;return result("initialize"); }
int xr_device_provider_start(void *h) { (void)h;return result("start"); }
int xr_device_provider_register_imu_pose_callback(void *h,void (*cb)(float *,uint64_t)) { (void)h;pose_cb=cb;return result("register"); }
int xr_device_provider_get_glasses_version(void *h,char *out,int *len) {
    (void)h; const char *version=is("wrong_firmware")?"unreviewed":"30.0.00.002_20260804";
    int n=(int)strlen(version)+1; trace("firmware"); if(*len<n)return -1;
    memcpy(out,version,(size_t)n);*len=is("firmware_length")?10000:(is("firmware_short_length")?1:n);return is("firmware_error")?-37:0;
}
int xr_device_provider_open_imu(void *h,uint8_t mode,uint8_t rate) {
    (void)h;trace("open");if(mode!=1||rate!=0)return -1;
    if(is("ready_then_bad")) { for(unsigned i=0;i<4;i++)emit(i,now_ns()); }
    else if(is("queue_overflow")) { for(unsigned i=0;i<1024;i++)emit(i,now_ns()); }
    else { atomic_store(&running,1); if(pthread_create(&worker,NULL,produce,NULL))return -1;started_thread=1; }
    return is("open")?-37:0;
}
int xr_device_provider_close_imu(void *h,uint8_t mode) { (void)h;(void)mode; if(is("callback_during_close"))emit(5,now_ns());return result("close"); }
int xr_device_provider_stop(void *h) { (void)h;atomic_store(&running,0);if(started_thread){pthread_join(worker,NULL);started_thread=0;}return result("stop"); }
int xr_device_provider_shutdown(void *h) { (void)h;return result("shutdown"); }
void xr_device_provider_destroy(void *h) { (void)h;trace("destroy");atomic_store(&running,0);if(started_thread){pthread_join(worker,NULL);started_thread=0;}pose_cb=NULL; }
