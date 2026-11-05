/* Real managed lists and the actual scheduler, not a replacement collector.
 * This deterministic single-mutator probe controls mark colors while the
 * real worker is stopped. It proves whole-owner pacing/credit, not concurrency,
 * moving-GC lifetime, final remark, full collection or performance. */
typedef long long i64;
typedef struct NativeRange { void **slots; i64 count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    i64 used, capacity, owner;
} NativeFrame;
extern i64 pcc_gc_backend(void);
extern void py_gc_disable(void);
extern i64 pcc_gc_cms_stop_worker(void);
extern i64 pcc_current_thread_id(void);
extern i64 pcc_thread_live_count(void);
extern void pcc_thread_no_park_enter(void);
extern void pcc_thread_no_park_exit(void);
extern void pcc_thread_unregister_current(void);
extern i64 pcc_gc_native_frame_enter(void *, void *, i64);
extern i64 pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, i64);
extern void pcc_gc_native_root_unbind(void *);
extern void *py_list_new(i64);
extern void py_list_append(void *, void *);
extern i64 py_list_len(void *);
extern void py_decref(void *);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void *pcc_gc_object_index_find(void *);
extern void *pcc_gc_object_node_next(void *);
extern void pcc_gc_set_mark_color(void *, i64);
extern void pcc_gc_gray_count_store_release(i64);
extern i64 pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_trace_cursor_store(void *);
extern void *pcc_gc_trace_cursor_load(void);
extern i64 pcc_gc_tracing_step_cycle(i64);
extern i64 pcc_stop_the_world(void);
extern i64 pcc_resume_world(void);
extern i64 pcc_thread_owns_stopped_world(void);
extern int pcc_gc_mark_active;
extern i64 pcc_gc_tracing_finish_claim_epoch;
extern i64 pcc_gc_trace_stack_count;
extern int puts(const char *);

int main(int argc, char **argv) {
    NativeFrame frame = {0};
    NativeRange ranges[1];
    void *roots[5] = {0, 0, 0, 0, 0};
    void *nodes[5];
    void *handle;
    i64 backend;
    i64 processed;
    int i;
    if (argc != 2 || argv[1][0] < '0' || argv[1][0] > '4' || argv[1][1]) return 10;
    if (sizeof(void *) != 8 || sizeof(i64) != 8 || sizeof(NativeFrame) != 40 ||
        sizeof(NativeRange) != 16) return 11;
    backend = pcc_gc_backend();
    if (backend != argv[1][0] - '0') return 12;
    py_gc_disable();
    pcc_current_thread_id();
    if (pcc_gc_cms_stop_worker() != 0 || pcc_thread_live_count() != 1) return 13;
    pcc_thread_no_park_enter();
    if (pcc_gc_native_frame_enter(&frame, ranges, 1) != 0) return 14;
    handle = pcc_gc_native_root_bind_range(roots, 5);
    if (!handle) return 15;
    pcc_thread_no_park_exit();
    /* Two extra gray sentinels keep both phases open even on the original
     * object-budget baseline, whose first call can complete up to4 objects. */
    roots[3] = py_list_new(0);
    roots[4] = py_list_new(0);
    roots[0] = py_list_new(0);
    roots[1] = py_list_new(9);
    roots[2] = py_list_new(9);
    if (!roots[0] || !roots[1] || !roots[2] || !roots[3] || !roots[4]) return 16;
    for (i = 0; i < 9; i += 1) {
        py_list_append(roots[1], roots[0]);
        py_list_append(roots[2], roots[0]);
    }
    if (py_list_len(roots[0]) != 0 || py_list_len(roots[1]) != 9 ||
        py_list_len(roots[2]) != 9) return 17;

    /* GC0 keeps its original allocation/container path, never step_cycle. */
    if (backend != 0) {
        pcc_py_gc_minor_graph_lock();
        if (pcc_gc_mark_active || pcc_gc_tracing_finish_claim_epoch) return 18;
        for (i = 0; i < 5; i += 1) {
            nodes[i] = pcc_gc_object_index_find(roots[i]);
            if (!nodes[i]) return 19;
            pcc_gc_set_mark_color(roots[i], 16);
        }
        if (pcc_gc_object_node_next(nodes[2]) != nodes[1]) return 20;
        pcc_gc_gray_count_store_release(5);
        pcc_gc_trace_cursor_store(nodes[2]);
        pcc_gc_mark_active = 1;
        pcc_py_gc_minor_graph_unlock();
        if (pcc_thread_owns_stopped_world()) return 21;
        if (pcc_gc_tracing_step_cycle(4) != 1) return 22;
        if (pcc_gc_gray_count_load_acquire() != 4 || pcc_gc_trace_stack_count != 0 ||
            pcc_gc_trace_cursor_load() != nodes[1]) return 23;
        if ((((int *)roots[2])[3] & 56) != 32 ||
            (((int *)roots[1])[3] & 56) != 16) return 24;
        if (pcc_gc_tracing_step_cycle(4) != 1) return 25;
        if (pcc_gc_gray_count_load_acquire() != 3 || pcc_gc_trace_stack_count != 0 ||
            (((int *)roots[1])[3] & 56) != 32) return 26;

        /* No gray children remain in these owners; the separate sentinels
         * stay gray so no final remark is triggered. The stopped-world budget
         * must complete both nine-slot owners, rather than apply slot cost. */
        pcc_py_gc_minor_graph_lock();
        pcc_gc_set_mark_color(roots[0], 32);
        pcc_gc_set_mark_color(roots[1], 16);
        pcc_gc_set_mark_color(roots[2], 16);
        pcc_gc_gray_count_store_release(4);
        pcc_gc_trace_cursor_store(nodes[2]);
        pcc_py_gc_minor_graph_unlock();
        if (pcc_stop_the_world() != 0 || !pcc_thread_owns_stopped_world()) return 27;
        processed = pcc_gc_tracing_step_cycle(2);
        if (pcc_resume_world() != 0) return 28;
        if (processed != 2 || pcc_gc_gray_count_load_acquire() != 2) return 29;
        if ((((int *)roots[1])[3] & 56) != 32 ||
            (((int *)roots[2])[3] & 56) != 32) return 30;
        pcc_py_gc_minor_graph_lock();
        pcc_gc_set_mark_color(roots[3], 32);
        pcc_gc_set_mark_color(roots[4], 32);
        pcc_gc_gray_count_store_release(0);
        pcc_gc_mark_active = 0;
        pcc_gc_trace_cursor_store(0);
        pcc_py_gc_minor_graph_unlock();
    }
    pcc_gc_native_root_unbind(handle);
    if (pcc_gc_native_frame_leave(&frame) != 0) return 31;
    py_decref(roots[2]);
    py_decref(roots[1]);
    py_decref(roots[0]);
    py_decref(roots[3]);
    py_decref(roots[4]);
    pcc_thread_unregister_current();
    puts("gc-whole-owner-work-ok");
    return 0;
}
