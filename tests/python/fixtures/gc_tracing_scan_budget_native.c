/* Real managed objects and the real owned runtime tracing-step ABI.
 * Automatic collection is disabled; explicit slices remain legal. The probe
 * changes mark colors under the graph lock to make the changed scan shape
 * deterministic. It does not substitute objects/nodes or the step algorithm.
 * This is a node-bound probe, not a bound on scanning one object's references.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_thread_owns_stopped_world(void);
extern int64_t pcc_gc_tracing_step_cycle(int64_t);
extern void *pcc_gc_object_list_head(void);
extern void *pcc_gc_object_node_next(void *);
extern void *pcc_gc_trace_cursor_load(void);
extern void pcc_gc_trace_cursor_store(void *);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int64_t pcc_gc_tracing_finish_claim_epoch;

#define PREFIX 97
static PyObject *objects[PREFIX + 2];
static void *nodes[PREFIX + 2];

static int fail(int code, const char *where, int64_t got) {
    fprintf(stderr, "SCAN_BUDGET_FAIL code=%d where=%s got=%lld\n",
            code, where, (long long)got);
    return code;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    if (backend < 1 || backend > 4) return fail(1, "backend", backend);
    py_gc_disable();

    /* Allocation prepends nodes. Tail first, gray target next, then prefix. */
    for (int i = 0; i < PREFIX + 2; ++i) {
        objects[i] = py_list_new(0);
        if (!objects[i]) return fail(2, "allocation", i);
    }
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_mark_active != 0 || pcc_gc_tracing_finish_claim_epoch != 0) {
        pcc_py_gc_minor_graph_unlock();
        return fail(3, "unexpected_open_cycle", pcc_gc_mark_active);
    }
    for (int i = 0; i < PREFIX + 2; ++i) {
        nodes[i] = pcc_gc_object_index_find(objects[i]);
        if (!nodes[i]) {
            pcc_py_gc_minor_graph_unlock();
            return fail(4, "missing_real_node", i);
        }
        pcc_gc_set_mark_color(objects[i], 32);
    }
    if (pcc_gc_object_list_head() != nodes[PREFIX + 1]) {
        pcc_py_gc_minor_graph_unlock();
        return fail(5, "unexpected_head", 0);
    }
    for (int i = PREFIX + 1; i > 0; --i) {
        if (pcc_gc_object_node_next(nodes[i]) != nodes[i - 1]) {
            pcc_py_gc_minor_graph_unlock();
            return fail(6, "unexpected_object_order", i);
        }
    }
    pcc_gc_set_mark_color(objects[1], 16);
    pcc_gc_gray_count_store_release(1);
    pcc_gc_trace_cursor_store(nodes[PREFIX + 1]);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    if (pcc_thread_owns_stopped_world() != 0) return fail(7, "unexpected_stw", 1);
    for (int i = PREFIX + 1; i >= 2; --i) {
        int64_t processed = pcc_gc_tracing_step_cycle(1);
        if (processed != 0) return fail(8, "black_node_received_credit", processed);
        if (pcc_gc_trace_cursor_load() != nodes[i - 1]) return fail(9, "cursor_did_not_advance_one", i);
        if (pcc_gc_gray_count_load_acquire() != 1) return fail(10, "gray_changed_before_target", i);
    }
    if (pcc_gc_tracing_step_cycle(1) != 1) return fail(11, "target_not_traced", 0);
    if (pcc_gc_trace_cursor_load() != nodes[0]) return fail(12, "target_cursor", 0);
    if (pcc_gc_gray_count_load_acquire() != 0) return fail(13, "target_gray_count", 1);
    if ((py_header_flags_load(py_header(objects[1])) & 56) != 32) return fail(19, "target_color", 0);

    /* Explicit stopped-world draining must not hit the four-zero-progress
     * gc.collect backstop just because a gray node has a long black prefix. */
    pcc_py_gc_minor_graph_lock();
    pcc_gc_set_mark_color(objects[1], 16);
    pcc_gc_gray_count_store_release(1);
    pcc_gc_trace_cursor_store(nodes[PREFIX + 1]);
    pcc_py_gc_minor_graph_unlock();
    if (pcc_stop_the_world() != 0) return fail(14, "stop_world", 0);
    if (pcc_thread_owns_stopped_world() == 0) {
        pcc_resume_world();
        return fail(15, "missing_stw_owner", 0);
    }
    int64_t stopped_processed = pcc_gc_tracing_step_cycle(1);
    int stopped_cursor_ok = pcc_gc_trace_cursor_load() == nodes[0];
    int64_t stopped_gray = pcc_gc_gray_count_load_acquire();
    if (pcc_resume_world() != 0) return fail(16, "resume_world", 0);
    if (stopped_processed != 1) return fail(17, "stw_no_progress", stopped_processed);
    if (!stopped_cursor_ok || stopped_gray != 0) return fail(18, "stw_cursor_or_gray", stopped_gray);

    pcc_py_gc_minor_graph_lock();
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_gc_trace_cursor_store(0);
    pcc_gc_gray_count_store_release(0);
    pcc_py_gc_minor_graph_unlock();
    for (int i = 0; i < PREFIX + 2; ++i) py_decref(objects[i]);
    printf("SCAN_BUDGET_NATIVE_OK backend=%lld tracing=%d\n",
           (long long)backend, pcc_gc_backend4_no_refcounts != 0);
    return 0;
}

