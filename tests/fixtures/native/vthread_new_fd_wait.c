/* External C ABI reference: a running task introduces an I/O wait while a
 * persistent carrier still has a preselected compute batch to drain. */
#include "py_runtime.h"
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

enum { JOBS = 96 };
extern int64_t py_virtual_thread_carrier_failure_count(void);
static PyObject *io_owner;
static int pipe_fds[2];
static int started;
static int completed;
static int observed = -1;
static int failures;

static int64_t io_resume(PyObject *thread, PyObject *continuation) {
    (void)continuation;
    __atomic_store_n(&observed, __atomic_load_n(&completed, __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
    return py_virtual_thread_complete(thread, py_None);
}

static int64_t cpu_resume(PyObject *thread, PyObject *continuation) {
    (void)continuation;
    if (__atomic_fetch_add(&started, 1, __ATOMIC_ACQ_REL) == 0) {
        if (py_virtual_thread_block_on_fd(io_owner, pipe_fds[0], POLLIN, 1000) != 0
                || write(pipe_fds[1], "x", 1) != 1) {
            __atomic_store_n(&failures, 1, __ATOMIC_RELEASE);
            return -1;
        }
    }
    if (py_virtual_thread_complete(thread, py_None) != 0) return -1;
    __atomic_add_fetch(&completed, 1, __ATOMIC_RELEASE);
    return 0;
}

static PyObject *task(void *resume) {
    static const int32_t empty_map[1] = {0};
    PyObject *continuation = py_continuation_new_typed(empty_map, NULL, resume);
    if (!continuation) return NULL;
    PyObject *thread = py_virtual_thread_new(continuation);
    py_decref(continuation);
    return thread;
}

int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1])) != 0) return 2;
    if (pipe(pipe_fds)) return 3;
    void *root = pcc_gc_scheduler_root_register_handle(&io_owner);
    if (!root) return 4;
    PyObject *created = task((void *)&io_resume);
    if (!created) return 5;
    pcc_gc_store_root(&io_owner, created);
    py_decref(created);
    int64_t lease = pcc_gc_foreign_lease_acquire(&io_owner);
    if (lease < 0) return 6;
    for (int i = 0; i < JOBS; ++i) {
        PyObject *thread = task((void *)&cpu_resume);
        if (!thread || py_virtual_thread_start(thread) != 0) return 7;
        py_decref(thread);
    }
    if (py_virtual_thread_carrier_pool_start(1) != 1) return 8;
    for (int spin = 0; spin < 5000; ++spin) {
        if (__atomic_load_n(&completed, __ATOMIC_ACQUIRE) == JOBS
                && __atomic_load_n(&observed, __ATOMIC_ACQUIRE) >= 0) break;
        (void)poll(NULL, 0, 1);
    }
    if (py_virtual_thread_carrier_pool_stop() != 1) return 9;
    int seen = __atomic_load_n(&observed, __ATOMIC_ACQUIRE);
    if (__atomic_load_n(&failures, __ATOMIC_ACQUIRE)
            || __atomic_load_n(&completed, __ATOMIC_ACQUIRE) != JOBS
            || py_virtual_thread_carrier_failure_count() != 0) return 10;
    if (py_virtual_thread_state(io_owner) != 4) return 11;
    if (pcc_gc_foreign_lease_release(&io_owner, lease) != 0) return 12;
    pcc_gc_store_root(&io_owner, NULL);
    pcc_gc_scheduler_root_unregister_handle(root);
    close(pipe_fds[0]); close(pipe_fds[1]);
    if (py_virtual_thread_ready_count() || py_virtual_thread_io_wait_count()) return 13;
    int64_t cached = cached_pool_roots();
    if (cached < 0 || pcc_gc_scheduler_root_count() != cached) return 14;
    printf("IO_OBSERVED_AFTER_CPU_STEPS=%d\n", seen);
    return seen == 1 ? 0 : 15;
}
