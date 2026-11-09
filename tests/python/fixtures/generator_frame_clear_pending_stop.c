/* A real pending stop parks inside clear_group after borrowed-root publication.
 * The worker makes no polling call between observing the request and entering
 * the helper. The stopped visitor identifies that worker's exact native frame.
 * GC4 forwarding, when requested, happens before the worker is started. The
 * stopped remap resolves that existing edge; no object moves at a runtime poll.
 * All old cells are NULL, the actual None singleton, or a real tagged integer.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

typedef struct NativeRange { PyObject **slots; int64_t count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    int64_t used, capacity, owner;
} NativeFrame;
typedef struct Shared {
    PyObject *input;
    int64_t worker_id, ready, call_started, completed, inspected;
    int64_t result, graph_after, no_park_after, failure;
} Shared;
typedef struct Visit {
    Shared *shared;
    PyObject *expected;
    int64_t frames, error;
} Visit;
extern int64_t pcc_thread_visit_native_root_frames(void *, void *);
extern int64_t pcc_gc_visit_native_root_frames(int64_t, int64_t);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int32_t pcc_thread_stop_requested;

static PyObject *original_cell(int index, PyObject *tagged) {
    if (index % 3 == 0) return 0;
    if (index % 3 == 1) return py_None;
    return tagged;
}

static void visit(void *raw_frame, void *raw_context) {
    NativeFrame *frame = (NativeFrame *)raw_frame;
    Visit *context = (Visit *)raw_context;
    if (frame->owner != context->shared->worker_id) return;
    context->frames++;
    if (frame->used != 1 || frame->capacity != 8 || !frame->ranges) {
        context->error = 1;
        return;
    }
    NativeRange *range = &frame->ranges[0];
    if (range->count != -1 || !range->slots
            || range->slots[0] != context->expected) context->error = 2;
    /* Keep the full range-table zeroing contract visible in this real frame. */
    for (int i = 1; i < 8; i++)
        if (frame->ranges[i].slots || frame->ranges[i].count) context->error = 3;
}

static void *clear_worker(void *argument) {
    Shared *shared = (Shared *)argument;
    shared->worker_id = pcc_current_thread_id();
    PyObject *input = shared->input; /* Main retains the authoritative owner. */
    __atomic_store_n(&shared->ready, 1, __ATOMIC_RELEASE);
    /* Read the actual generated-code poll flag, not world_stop (which is
     * published first). This avoids entering the helper before its exit
     * poll can see the request. The process-group watchdog bounds waiting. */
    while (!__atomic_load_n(&pcc_thread_stop_requested, __ATOMIC_ACQUIRE)) { }
    __atomic_store_n(&shared->call_started, 1, __ATOMIC_RELEASE);
    shared->result = py_gen_frame_clear_group(input, 1, 16);
    shared->graph_after = pcc_py_gc_minor_graph_lock_depth();
    shared->no_park_after = pcc_thread_no_park_depth();
    if (!__atomic_load_n(&shared->inspected, __ATOMIC_ACQUIRE)) shared->failure = 1;
    __atomic_store_n(&shared->completed, 1, __ATOMIC_RELEASE);
    /* Stay registered for a second real stop. It must find no helper native
     * frame, before the trampoline's unregister could conceal a leaked frame. */
    while (!__atomic_load_n(&pcc_thread_stop_requested, __ATOMIC_ACQUIRE)) { }
    pcc_thread_safepoint();
    return 0;
}

int main(int argc, char **argv) {
    (void)argv;
    int backend = pcc_gc_backend();
    int forwarded = argc > 1;
    if ((backend != 3 && backend != 4) || (forwarded && backend != 4)) return 1;
    if (pcc_threads_enabled() != 1 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    if (pcc_gc_cms_stop_worker() != 0 || pcc_thread_live_count() != 1) return 3;
    PyObject *roots[2] = {0};
    int32_t map[1] = {2};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(18);
    roots[1] = py_int_from_i64(42);
    if (!roots[0] || !roots[1] || (((uintptr_t)roots[1] & 1) == 0)) return 4;
    for (int i = 0; i < 18; i++)
        py_gen_frame_set(roots[0], i, original_cell(i, roots[1]));
    PyObject *source = roots[0];
    pcc_gc_unpin(source);
    if (forwarded) {
        pcc_gc_reset_relocation_set();
        if (pcc_gc_select_relocation_set(128) <= 0
                || !pcc_gc_relocation_set_contains(source)) return 5;
        PyObject *moved = pcc_gc_relocate_copy(source, sizeof(PyListObject));
        if (!moved || moved == source || pcc_gc_load_ptr(0, &roots[0]) != moved) return 6;
    }
    PyObject *canonical = pcc_gc_load_ptr(0, &roots[0]);
    int prior_pin = py_header(canonical)->flags & PY_FLAG_GC_PINNED;
    Shared shared = {0};
    shared.input = source;
    PccThreadHandle *thread = 0;
    if (pcc_thread_start(&thread, clear_worker, &shared) != 0) return 7;
    while (!__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    if (pcc_stop_the_world() != 0) return 8;
    /* If entry polled before publishing, or the helper declined, the exact
     * borrowed frame below is absent. Neither can become a successful test. */
    int failure = 0;
    if (!__atomic_load_n(&shared.call_started, __ATOMIC_ACQUIRE)
            || __atomic_load_n(&shared.completed, __ATOMIC_ACQUIRE)
            || pcc_thread_all_stopped_at_gates() != 0) failure = 9;
    Visit before = {&shared, source, 0, 0};
    if (pcc_thread_visit_native_root_frames((void *)visit, &before) < 1
            || before.frames != 1 || before.error) failure = 10;
    pcc_py_gc_minor_graph_lock();
    /* Production root remap, not a fixture-written replacement pointer. */
    int64_t rewritten = pcc_gc_visit_native_root_frames(3, 1);
    if ((forwarded && rewritten < 1) || (!forwarded && rewritten != 0)) failure = 11;
    canonical = pcc_gc_load_ptr(0, &roots[0]);
    for (int i = 0; i < 18; i++)
        if (((PyListObject *)canonical)->items[i] != original_cell(i, roots[1])) failure = 12;
    pcc_py_gc_minor_graph_unlock();
    Visit after = {&shared, canonical, 0, 0};
    if (pcc_thread_visit_native_root_frames((void *)visit, &after) < 1
            || after.frames != 1 || after.error) failure = 13;
    __atomic_store_n(&shared.inspected, 1, __ATOMIC_RELEASE);
    if (pcc_resume_world() != 0) return 14;
    if (failure) return failure;
    while (!__atomic_load_n(&shared.completed, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    if (shared.result != 1 || shared.failure || shared.graph_after || shared.no_park_after) return 15;
    if (pcc_stop_the_world() != 0) return 16;
    Visit empty = {&shared, canonical, 0, 0};
    if (pcc_thread_visit_native_root_frames((void *)visit, &empty) < 0
            || empty.frames || empty.error) failure = 17;
    pcc_py_gc_minor_graph_lock();
    canonical = pcc_gc_load_ptr(0, &roots[0]);
    for (int i = 0; i < 18; i++) {
        PyObject *expected = i >= 1 && i < 17 ? py_None : original_cell(i, roots[1]);
        if (((PyListObject *)canonical)->items[i] != expected) failure = 18;
    }
    if ((py_header(canonical)->flags & PY_FLAG_GC_PINNED) != prior_pin) failure = 19;
    pcc_py_gc_minor_graph_unlock();
    if (pcc_resume_world() != 0) return 20;
    void *result = 0;
    if (pcc_thread_join(thread, &result) != 0 || result) return 21;
    if (failure) return failure;
    if (pcc_thread_live_count() != 1 || pcc_thread_no_park_depth()
            || pcc_py_gc_minor_graph_lock_depth()) return 22;
    pcc_gc_store_root(&roots[1], 0);
    pcc_gc_store_root(&roots[0], 0);
    pcc_gc_frame_leave(roots);
    printf("FRAME_CLEAR_PENDING_STOP_OK gc=%d forwarded=%d\n", backend, forwarded);
    return 0;
}
