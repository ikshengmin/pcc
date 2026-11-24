#ifndef PCC_TEST_NATIVE_SCHEDULER_ROOTS_H
#define PCC_TEST_NATIVE_SCHEDULER_ROOTS_H

#include "py_runtime.h"

/* The registry counts addresses, including empty ready-pool slots and roots
 * owned by the caller. Scheduler effects count the live queue edges instead.
 * The collection and dequeue below also exercise those edges in the runtime. */
static int64_t active_vthread_roots(void) {
    int64_t live = 0;
    if (py_virtual_thread_effect_dropped() != 0) return -1;
    int64_t n = py_virtual_thread_effect_count();
    for (int64_t i = 0; i < n; ++i) {
        live += py_virtual_thread_effect_root_delta_at(i);
        if (live < 0) return -1;
    }
    return live;
}

#endif
