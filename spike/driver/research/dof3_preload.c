// Force the Luma Ultra into 3DoF for one XRLinuxDriver session, without rebuilding it. An experiment.
//
// XRLinuxDriver never calls xr_device_provider_set_dof_type_carina, and that function's header says
// the default is 6DOF and that it must be called between create and initialize. So this wraps
// initialize and makes the call first. It is the cameras-on / cameras-off test in
// docs/6dof-feasibility.md. Sources: XRLinuxDriver include/sdks/viture_device_carina.h,
// include/sdks/viture_glasses_provider.h, src/devices/viture.c.
//
// Build:  cc -O2 -Wall -shared -fPIC -o dof3_preload.so dof3_preload.c -ldl
// Proof it ran: $XDG_RUNTIME_DIR/xr-dof3-applied holds "dof_type=0 result=0".
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

#define CARINA 2

typedef void *handle_t;
typedef int (*initialize_t)(handle_t, const char *, const char *);
typedef int (*device_type_t)(handle_t);
typedef int (*set_dof_t)(handle_t, int);

static void note(int result) {
    const char *dir = getenv("XDG_RUNTIME_DIR");
    char path[512];
    snprintf(path, sizeof path, "%s/xr-dof3-applied", dir ? dir : "/tmp");
    FILE *f = fopen(path, "w");
    if (f) { fprintf(f, "dof_type=0 result=%d\n", result); fclose(f); }
}

int xr_device_provider_initialize(handle_t handle, const char *config, const char *cache_dir) {
    initialize_t initialize = (initialize_t)dlsym(RTLD_NEXT, "xr_device_provider_initialize");
    device_type_t device_type = (device_type_t)dlsym(RTLD_NEXT, "xr_device_provider_get_device_type");
    set_dof_t set_dof = (set_dof_t)dlsym(RTLD_NEXT, "xr_device_provider_set_dof_type_carina");
    if (!initialize) return -1;
    if (device_type && set_dof && device_type(handle) == CARINA) note(set_dof(handle, 0));
    return initialize(handle, config, cache_dir);
}
