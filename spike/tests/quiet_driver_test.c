// The quiet driver shim against a fake SDK: what reaches the "glasses" and when. Needs no hardware.
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int xr_device_provider_initialize(void*, const char*, const char*);
int xr_device_provider_start(void*);
int xr_device_provider_get_display_mode(void*);
// The fake's own record (quiet_driver_fake.c).
extern int fake_dof_calls, fake_last_dof, fake_init_calls, fake_start_calls, fake_mode_calls, fake_mode_value, fake_dof_before_init;

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

int main(void) {
    int h = 0;
    // 3DoF is asked for before initialize, because the SDK reads it there.
    assert(xr_device_provider_initialize(&h, 0, 0) == 0);
    assert(fake_init_calls == 1 && fake_dof_calls == 1 && fake_last_dof == 0 && fake_dof_before_init == 1);

    // The first start of a process does not wait: a fresh start has never been followed by a drop.
    double t = now();
    assert(xr_device_provider_start(&h) == 0);
    assert(now() - t < 0.1 && fake_start_calls == 1);

    // The display mode reaches the glasses once, however often it is asked.
    fake_mode_value = 7;
    for (int i = 0; i < 5; ++i) assert(xr_device_provider_get_display_mode(&h) == 7);
    assert(fake_mode_calls == 1);

    // A failure is not remembered: the next ask goes to the glasses.
    fake_mode_value = -7;
    int before = fake_mode_calls;
    xr_device_provider_start(&h);  // a reconnect forgets the cached mode
    assert(xr_device_provider_get_display_mode(&h) == -7);
    fake_mode_value = 3;
    assert(xr_device_provider_get_display_mode(&h) == 3);
    assert(fake_mode_calls == before + 2);

    // A reconnect waits for the hold before it touches the glasses (XR_QUIET_RECONNECT_HOLD_MS, set by CTest).
    t = now();
    int starts = fake_start_calls;
    assert(xr_device_provider_start(&h) == 0);
    assert(now() - t >= 0.12 && fake_start_calls == starts + 1);
    puts("quiet driver: ok");
    return 0;
}
