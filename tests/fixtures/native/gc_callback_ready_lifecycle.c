/* The stopped owner can acquire readiness via park, blocking-enter, or
 * unregister. Peers waiting for the world itself still need final resume. */
#include "py_internal.h"
#include <stdint.h>
#include <string.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_thread_stop_requested_acquire(void);
extern int64_t pcc_thread_blocking_enter(void *);
extern void pcc_thread_blocking_leave(int64_t);
extern void pcc_gc_cms_stop_worker(void);
extern int32_t pcc_gc_mark_active;
extern void *pcc_world_active_ready_cond_py;

enum { PEERS = 4, EPOCHS = 16, BLOCKER = 0, DEPARTING = 1, CONTENDER = 4 };
static int started, leave_wait, entered_wait, left_wait, bad;

static void *peer(void *arg) {
    int role = (int)(uintptr_t)arg;
    if (pcc_current_thread_id() <= 0) {
        __atomic_store_n(&bad, 1, __ATOMIC_RELEASE);
        return NULL;
    }
    __atomic_add_fetch(&started, 1, __ATOMIC_RELEASE);
    if (role == BLOCKER || role == DEPARTING || role == CONTENDER) {
        while (!pcc_thread_stop_requested_acquire()) {}
        if (role == DEPARTING) return NULL;
        if (role == CONTENDER) {
            if (pcc_stop_the_world() != 0 || pcc_thread_world_stop_epoch_held() < 0
                    || pcc_resume_world() != 0)
                __atomic_store_n(&bad, 1, __ATOMIC_RELEASE);
            return NULL;
        }
        int64_t entered = pcc_thread_blocking_enter(__builtin_frame_address(0));
        if (!entered) __atomic_store_n(&bad, 1, __ATOMIC_RELEASE);
        __atomic_store_n(&entered_wait, 1, __ATOMIC_RELEASE);
        while (!__atomic_load_n(&leave_wait, __ATOMIC_ACQUIRE)) {}
        pcc_thread_blocking_leave(entered);
        __atomic_store_n(&left_wait, 1, __ATOMIC_RELEASE);
    } else {
        while (!__atomic_load_n(&leave_wait, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    }
    return NULL;
}

int main(int argc, char **argv) {
    int callback = argc == 2 && strcmp(argv[1], "callback") == 0;
    if (pcc_current_thread_id() <= 0) return 1;
    pcc_gc_cms_stop_worker();
    for (int i = 0; i < EPOCHS; ++i) {
        started = leave_wait = entered_wait = left_wait = bad = 0;
        void *peers[PEERS];
        for (int j = 0; j < PEERS; ++j) {
            int role = (i % 2 != 0 && j == PEERS - 1) ? CONTENDER : j;
            if (pcc_thread_start(&peers[j], peer, (void *)(uintptr_t)role)) return 2;
        }
        while (__atomic_load_n(&started, __ATOMIC_ACQUIRE) != PEERS) {
            if (__atomic_load_n(&bad, __ATOMIC_ACQUIRE)) return 3;
            pcc_thread_safepoint();
        }
        /* In production the #9 layer sets the callback-mark window before
         * reacquiring the world. This test supplies that same stop mode. */
        __atomic_store_n(&pcc_gc_mark_active, callback ? 2 : 0, __ATOMIC_RELEASE);
        if (pcc_stop_the_world() != 0) return 4;
        int64_t epoch = pcc_thread_world_stop_epoch_held();
        if (epoch < 0 || (pcc_world_active_ready_cond_py != NULL) != callback) return 5;
        if (pcc_stop_the_world() != 0 || pcc_thread_world_stop_epoch_held() != epoch) return 6;
        if (pcc_resume_world() != 0 || pcc_thread_world_stop_epoch_held() != epoch) return 7;
        __atomic_store_n(&leave_wait, 1, __ATOMIC_RELEASE);
        if (__atomic_load_n(&left_wait, __ATOMIC_ACQUIRE)) return 8;
        if (pcc_resume_world() != 0 || pcc_thread_world_stop_epoch_held() >= 0) return 9;
        for (int j = 0; j < PEERS; ++j)
            if (pcc_thread_join(peers[j], NULL)) return 10;
        __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
        if (!entered_wait || !left_wait || bad || pcc_world_active_ready_cond_py != NULL)
            return 11;
    }
    return 0;
}
