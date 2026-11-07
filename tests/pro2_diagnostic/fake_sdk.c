/* Synthetic public C ABI fixture. No USB, HID, SDK or device dependencies. */
#include <stdint.h>
#include <string.h>
static void (*pose)(float *,uint64_t);
static int stage;
void *xr_device_provider_create(int pid) { stage=1; return pid==0x1301 ? &stage : 0; }
int xr_device_provider_is_product_id_valid(int pid) { return pid==0x1301; }
int xr_device_provider_is_product_support_imu_frequency(int pid,int mode,int rate) { return pid==0x1301 && mode==1 && rate==0; }
int xr_device_provider_get_device_type(void *h) { return h==&stage ? 1 : -1; }
int xr_device_provider_initialize(void *h,const char *config,const char *cache) { (void)h; (void)config; (void)cache; if(stage!=1)return -10;stage=2;return 0; }
int xr_device_provider_start(void *h) { (void)h;if(stage!=2)return -10;stage=3;return 0; }
int xr_device_provider_register_imu_pose_callback(void *h,void (*cb)(float *,uint64_t)) { (void)h;pose=cb;return stage==3?0:-10; }
int xr_device_provider_get_glasses_version(void *h,char *out,int *len) { (void)h;if(*len<8)return -1;memcpy(out,"fixture",8);*len=8;return 0; }
int xr_device_provider_open_imu(void *h,uint8_t mode,uint8_t rate) { (void)h;if(stage!=3||!pose||mode!=1||rate!=0)return -10;stage=4;float v[7]={1,2,3,1,0,0,0};pose(v,1234567890123ULL);return 0; }
int xr_device_provider_close_imu(void *h,uint8_t mode) { (void)h;(void)mode;stage=5;return 0; }
int xr_device_provider_stop(void *h) { (void)h;stage=6;return 0; }
int xr_device_provider_shutdown(void *h) { (void)h;stage=7;return 0; }
void xr_device_provider_destroy(void *h) { (void)h;stage=8; }
int fixture_stage(void) { return stage; }
