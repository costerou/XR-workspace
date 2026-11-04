// Preloaded into xrDriver by tools/usb_probe/probe.py: counts what its usbfs calls move, per endpoint, once a second, to a file.
// Measurement only, and never part of a run. Build: gcc -shared -fPIC -O2 -o usb_count.so usb_count.c -ldl -lpthread
#define _GNU_SOURCE
#include <dlfcn.h>
#include <linux/usbdevice_fs.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static unsigned long long submitted[256], reaped[256], bytes[256], errors[256];
static int (*real_ioctl)(int, unsigned long, void*);

static void* dump(void* path_) {
    const char* path = path_;
    char tmp[1024];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    for (;;) {
        sleep(1);
        FILE* f = fopen(tmp, "w");
        if (!f) continue;
        for (int i = 0; i < 256; ++i)
            if (submitted[i] || reaped[i]) fprintf(f, "%02x %llu %llu %llu %llu\n", i, submitted[i], reaped[i], bytes[i], errors[i]);
        fclose(f);
        rename(tmp, path);
    }
    return NULL;
}

__attribute__((constructor)) static void start(void) {
    real_ioctl = dlsym(RTLD_NEXT, "ioctl");
    const char* path = getenv("XR_USB_COUNT");
    char comm[32] = "";
    FILE* c = fopen("/proc/self/comm", "r");
    if (c) { if (!fgets(comm, sizeof comm, c)) comm[0] = 0; fclose(c); }
    // Only in the driver: `unshare`, on the way to it, refuses to run with a second thread.
    if (path && !strncmp(comm, "xrDriver", 8)) { pthread_t thread; pthread_create(&thread, NULL, dump, (void*)path); }
}

int ioctl(int fd, unsigned long request, ...) {
    va_list args;
    va_start(args, request);
    void* arg = va_arg(args, void*);
    va_end(args);
    if (!real_ioctl) real_ioctl = dlsym(RTLD_NEXT, "ioctl");
    const int result = real_ioctl(fd, request, arg);
    if (result == 0 && request == USBDEVFS_SUBMITURB) {
        __atomic_add_fetch(&submitted[((struct usbdevfs_urb*)arg)->endpoint], 1, __ATOMIC_RELAXED);
    } else if (result == 0 && (request == USBDEVFS_REAPURBNDELAY || request == USBDEVFS_REAPURB)) {
        struct usbdevfs_urb* urb = *(struct usbdevfs_urb**)arg;
        if (urb) {
            __atomic_add_fetch(&reaped[urb->endpoint], 1, __ATOMIC_RELAXED);
            __atomic_add_fetch(&bytes[urb->endpoint], urb->actual_length, __ATOMIC_RELAXED);
            if (urb->status) __atomic_add_fetch(&errors[urb->endpoint], 1, __ATOMIC_RELAXED);
        }
    }
    return result;
}
