// Guest-only test fixture standing in for XRLinuxDriver's SysV IPC layout (no glasses in the VM).
// Writes a yaw sweep (+-20 deg about the driver's z/up axis) at ~1 kHz for argv[1] seconds.
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <time.h>
#include <unistd.h>
static void* make(const char* name, size_t size) {
    char path[128]; snprintf(path, sizeof path, "/tmp/shader_runtime_%s", name);
    FILE* f = fopen(path, "w"); if (f) fclose(f);
    int id = shmget(ftok(path, 0), size, 0666 | IPC_CREAT);
    if (id < 0) { perror("shmget"); exit(3); }
    return shmat(id, NULL, 0);
}
int main(int argc, char** argv) {
    if (argc != 2 || strcmp(getenv("XR_GUEST_ONLY") ? getenv("XR_GUEST_ONLY") : "", "pluginfree")) return 90;
    double seconds = atof(argv[1]);
    float* pose = make("pose_orientation", sizeof(float) * 16);
    pthread_mutex_t* mutex = make("pose_orientation_mutex", sizeof(pthread_mutex_t));
    _Bool* disabled = make("disabled", sizeof(_Bool));
    float* fov = make("display_fov", sizeof(float));
    pthread_mutexattr_t attr; pthread_mutexattr_init(&attr); pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
    pthread_mutex_init(mutex, &attr);
    *disabled = 0; *fov = 52.f;
    struct timespec t0, now; clock_gettime(CLOCK_MONOTONIC, &t0);
    unsigned long n = 0;
    for (;;) {
        clock_gettime(CLOCK_MONOTONIC, &now);
        double t = (now.tv_sec - t0.tv_sec) + (now.tv_nsec - t0.tv_nsec) / 1e9;
        if (t > seconds) break;
        double yaw = 20.0 * sin(t * 2.0) * M_PI / 180.0;
        float q[4] = {0, 0, (float)sin(yaw / 2), (float)cos(yaw / 2)}, ms = (float)(t * 1000.0);
        pthread_mutex_lock(mutex);
        for (int i = 0; i < 3; ++i) memcpy(pose + 4 * i, q, sizeof q);
        pose[12] = ms; pose[13] = ms - 16; pose[14] = ms - 32; pose[15] = 0;
        pthread_mutex_unlock(mutex);
        ++n; usleep(1000);
    }
    printf("FAKE_FEED samples=%lu\n", n);
    return 0;
}
