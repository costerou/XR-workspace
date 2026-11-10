// Test helper: a reader holding the pose lock (as spike/src/pose.cpp does to copy a sample), for argv[2] ms.
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <time.h>
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char path[4096];
    snprintf(path, sizeof path, "%spose_orientation_mutex", argv[1]);
    const key_t key = ftok(path, 0);
    const int id = key == -1 ? -1 : shmget(key, 0, 0);
    pthread_mutex_t *mutex = id < 0 ? (void *)-1 : shmat(id, NULL, 0);
    if (mutex == (void *)-1) return 3;
    if (pthread_mutex_lock(mutex)) return 4;
    long ms = 0;
    sscanf(argv[2], "%ld", &ms);
    struct timespec hold = {ms / 1000, (ms % 1000) * 1000000};
    nanosleep(&hold, NULL);
    pthread_mutex_unlock(mutex);
    puts("held");
    return 0;
}
