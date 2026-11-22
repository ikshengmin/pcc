/* One leader and three PCC-owned workers remain alive through both markers. */
#include <stdint.h>
#include <unistd.h>
#include "py_runtime.h"

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { WORKERS = 3, CPU_WINDOW_US = 100000 };
static int started, go, done, release_workers;
static volatile uint64_t payload[WORKERS + 1];

static void burn(int index) {
    int64_t until = pcc_runtime_monotonic_us() + CPU_WINDOW_US;
    do {
        for (int i = 0; i < 4096; ++i) payload[index] = payload[index] * 17 + 1;
    } while (pcc_runtime_monotonic_us() < until);
}

static void *work(void *arg) {
    int index = *(int *)arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    __atomic_add_fetch(&started, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    burn(index);
    __atomic_add_fetch(&done, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&release_workers, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    return NULL;
}

int main(void) {
    void *handles[WORKERS];
    int indexes[WORKERS];
    for (int i = 0; i < WORKERS; ++i) {
        indexes[i] = i;
        if (pcc_thread_start(&handles[i], work, &indexes[i]) != 0 || !handles[i])
            return 1;
    }
    while (__atomic_load_n(&started, __ATOMIC_ACQUIRE) != WORKERS)
        pcc_thread_safepoint();
    char marker = 0;
    if (write(1, "READY\n", 6) != 6 || read(0, &marker, 1) != 1 || marker != 'G')
        return 2;
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    burn(WORKERS);
    while (__atomic_load_n(&done, __ATOMIC_ACQUIRE) != WORKERS)
        pcc_thread_safepoint();
    if (write(1, "DONE\n", 5) != 5 || read(0, &marker, 1) != 1 || marker != 'R')
        return 3;
    __atomic_store_n(&release_workers, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < WORKERS; ++i) {
        void *status = NULL;
        if (pcc_thread_join(handles[i], &status) != 0 || status)
            return 4;
    }
    return 0;
}
