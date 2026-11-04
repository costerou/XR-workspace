// Preloaded into the installed XRLinuxDriver (/usr/bin/xrDriver), so that it asks less of the glasses. docs/usb-drops.md has
// the measurements behind each of the three; tools/native_spike/tracking.py sets them, one environment variable each, and
// with none set this changes nothing.
//
//   XR_QUIET_DOF=3                 No SLAM. The SDK defaults to 6DoF: a visual-inertial tracker and two mappers, 38 threads
//                                  and a third of a core, for a pose whose position nothing here reads. Asked for through
//                                  the SDK's own switch, which XRLinuxDriver never calls.
//   XR_QUIET_POLLS=1               The display mode goes to the glasses once per connection. XRLinuxDriver's state loop asks
//                                  every second, for side-by-side state nobody here uses.
//   XR_QUIET_RECONNECT_HOLD_MS=n   A reconnect waits this long before it touches the glasses. Of 160 fresh starts none was
//                                  followed by a drop within 13 s; of 20 reconnects right after a drop, 12 were, and that
//                                  second drop is the one that takes the display with it.
//
// Nothing here links the SDK: the symbols are found at run time in the library XRLinuxDriver already loaded.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static long env_long(const char* name) {
    const char* value = getenv(name);
    return value ? strtol(value, NULL, 10) : 0;
}

static void* next_symbol(const char* name) {
    void* symbol = dlsym(RTLD_NEXT, name);
    if (!symbol) fprintf(stderr, "[quiet-driver] %s not found\n", name);
    return symbol;
}

static atomic_int started;
static atomic_int cached_mode = -1;  // the last good answer; -1: ask

int xr_device_provider_initialize(void* handle, const char* custom_config, const char* cache_dir) {
    static int (*real)(void*, const char*, const char*);
    if (!real) real = next_symbol("xr_device_provider_initialize");
    if (!real) return -1;
    if (env_long("XR_QUIET_DOF") == 3) {
        // After create, before initialize: viture_device_carina.h. An unknown build keeps its default and still tracks.
        int (*set_dof)(void*, int) = next_symbol("xr_device_provider_set_dof_type_carina");
        if (set_dof) fprintf(stderr, "[quiet-driver] 3DoF: %d\n", set_dof(handle, 0));
    }
    return real(handle, custom_config, cache_dir);
}

int xr_device_provider_start(void* handle) {
    static int (*real)(void*);
    if (!real) real = next_symbol("xr_device_provider_start");
    if (!real) return -1;
    const long hold_ms = env_long("XR_QUIET_RECONNECT_HOLD_MS");
    atomic_store(&cached_mode, -1);
    if (atomic_fetch_add(&started, 1) > 0 && hold_ms > 0) {
        fprintf(stderr, "[quiet-driver] reconnect held %ld ms\n", hold_ms);
        struct timespec wait = {hold_ms / 1000, (hold_ms % 1000) * 1000000L};
        while (nanosleep(&wait, &wait) != 0) {}
    }
    return real(handle);
}

int xr_device_provider_get_display_mode(void* handle) {
    static int (*real)(void*);
    if (!real) real = next_symbol("xr_device_provider_get_display_mode");
    if (!real) return -1;
    if (env_long("XR_QUIET_POLLS") != 1) return real(handle);
    const int cached = atomic_load(&cached_mode);
    if (cached >= 0) return cached;
    const int mode = real(handle);
    if (mode >= 0) atomic_store(&cached_mode, mode);  // a failure is not an answer
    return mode;
}
