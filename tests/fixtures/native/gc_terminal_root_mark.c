/* Synthetic exact index records exercise real GC3/4 terminal marking.
 * Terminal cases use index-only records; GC3 scan cases temporarily publish
 * a bounded synthetic list and restore it before resuming. STW is held throughout.
 * The graph lock protects publication, assertions and removal; marking also
 * exercises a scan under STW alone, without the graph lock.
 * This is a source-admitted runtime control, not relocation or throughput proof.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_current_thread_id(void);
extern int64_t pcc_stop_the_world(void);
extern int64_t pcc_resume_world(void);
extern int64_t pcc_thread_owns_stopped_world(void);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_object_index_insert(void *, void *);
extern void *pcc_gc_object_index_remove(void *);
extern int64_t pcc_gc_forwarding_index_insert(void *, void *);
extern void *pcc_gc_forwarding_index_remove(void *);
extern void pcc_gc_conservative_pin_owner_chain(void *, int64_t);
extern void pcc_gc_mark_canonical_root_gray_with_node(void *, void *);
extern void pcc_gc_trace_stack_begin(void);
extern void pcc_gc_trace_stack_end(void);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern void pcc_gc_gray_worklist_clear_stopped(void);
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_forwarding_population;
extern int32_t pcc_gc_mark_active;
extern void *pcc_gc_object_head;
extern int32_t pcc_gc_trace_stack_owner;
extern void pcc_gc_scan_conservative_thread_roots(int64_t);
extern int64_t pcc_gc_trace_stack_count;
extern void *pcc_gc_trace_stack[];

struct Object { int64_t refs; int32_t tag; int32_t flags; int64_t payload[6]; };
static struct Object objects[4];
static int64_t nodes[4][11];
static int64_t edges[3][2];

static int run_case(int count, int mark, int invalid) {
    int saved_population = pcc_gc_forwarding_population;
    memset(objects, 0, sizeof(objects));
    memset(nodes, 0, sizeof(nodes));
    memset(edges, 0, sizeof(edges));
    for (int i = 0; i < count; i++) {
        objects[i].refs = 1; objects[i].tag = 5; objects[i].flags = 8 | 1024;
        nodes[i][0] = (int64_t)&objects[i]; nodes[i][1] = sizeof(struct Object);
        nodes[i][7] = 4;
        if (pcc_gc_object_index_insert(&objects[i], nodes[i]) < 0) return 10;
        if (i + 1 < count) {
            edges[i][0] = (int64_t)&objects[i];
            edges[i][1] = (int64_t)&objects[i + 1];
            if (pcc_gc_forwarding_index_insert(&objects[i], edges[i]) < 0) return 11;
        }
    }
    pcc_gc_forwarding_population = saved_population + count - 1;
    if (invalid == 1) nodes[count - 1][4] = 1;
    if (invalid == 2) objects[count - 1].flags |= 524288;
    pcc_gc_trace_stack_begin();
    pcc_gc_conservative_pin_owner_chain(nodes[0], mark);
    int accepted = mark && invalid == 0;
    if (pcc_gc_trace_stack_count != accepted) return 12;
    if (accepted && pcc_gc_trace_stack[0] != &objects[count - 1]) return 13;
    if (pcc_gc_gray_count_load_acquire() != accepted) return 14;
    for (int i = 0; i < count; i++) {
        if (nodes[i][7] != 5) return 15;
        if (!!(objects[i].flags & 16) != (accepted && i == count - 1)) return 16;
    }
    if (accepted) {
        /* A repeated already-gray root must still push without double count. */
        pcc_gc_mark_canonical_root_gray_with_node(&objects[count - 1], nodes[count - 1]);
        if (pcc_gc_trace_stack_count != 2 || pcc_gc_gray_count_load_acquire() != 1) return 17;
        if (pcc_gc_trace_stack[1] != &objects[count - 1]) return 18;
    }
    pcc_gc_mark_canonical_root_gray_with_node(0, nodes[0]);
    pcc_gc_mark_canonical_root_gray_with_node((void *)3, nodes[0]);
    pcc_gc_mark_canonical_root_gray_with_node(&objects[0], 0);
    if (pcc_gc_trace_stack_count != (accepted ? 2 : 0)) return 19;
    pcc_gc_trace_stack_end();
    pcc_gc_gray_worklist_clear_stopped();
    pcc_gc_gray_count_store_release(0);
    for (int i = 0; i < count; i++) {
        if (i + 1 < count && pcc_gc_forwarding_index_remove(&objects[i]) != edges[i]) return 20;
        if (pcc_gc_object_index_remove(&objects[i]) != nodes[i]) return 21;
    }
    pcc_gc_forwarding_population = saved_population;
    return 0;
}

/* Execute the changed zero-edge scan, including the STW-only seed boundary.
 * Explicit address pins make positive roots deterministic despite real owner
 * register/stack capture. Absence of a stale raw root is proved by models,
 * not this native fixture: its C temporaries may themselves retain addresses.
 * No callbacks, collection, retirement, or world resume occurs while the
 * synthetic list is installed. The ordinary object index remains available.
 */
static int run_scan_case(int mark, int keep_graph_lock) {
    if (pcc_gc_forwarding_population != 0 || pcc_gc_trace_stack_owner != 0 ||
            pcc_gc_trace_stack_count != 0 || pcc_gc_gray_count_load_acquire() != 0) return 30;
    void *saved_head = pcc_gc_object_head;
    void *saved_trace0 = pcc_gc_trace_stack[0];
    void *saved_trace1 = pcc_gc_trace_stack[1];
    memset(objects, 0, sizeof(objects));
    memset(nodes, 0, sizeof(nodes));
    for (int i = 0; i < 2; i++) {
        objects[i].refs = 1; objects[i].tag = 5;
        objects[i].flags = 8 | 64 | 1024; /* explicit PINNED plus white */
        nodes[i][0] = (int64_t)&objects[i]; nodes[i][1] = sizeof(struct Object);
        nodes[i][2] = i == 0 ? (int64_t)nodes[1] : 0;
        nodes[i][5] = i == 1 ? (int64_t)nodes[0] : 0;
        nodes[i][7] = 5; /* stale cycle pin plus independent metadata */
        if (pcc_gc_object_index_insert(&objects[i], nodes[i]) < 0) return 31;
    }
    pcc_gc_object_head = nodes[0];
    pcc_gc_trace_stack_begin();
    if (!keep_graph_lock) pcc_py_gc_minor_graph_unlock();
    pcc_gc_scan_conservative_thread_roots(mark);
    if (!keep_graph_lock) pcc_py_gc_minor_graph_lock();
    int result = 0;
    if (!pcc_thread_owns_stopped_world()) result = 32;
    if (pcc_gc_trace_stack_count != (mark ? 2 : 0) ||
            pcc_gc_gray_count_load_acquire() != (mark ? 2 : 0)) result = 33;
    for (int i = 0; i < 2; i++) {
        if (nodes[i][7] != 5 || !!(objects[i].flags & 16) != mark ||
                !(objects[i].flags & 64)) result = 34;
        if (mark && pcc_gc_trace_stack[i] != &objects[i]) result = 35;
    }
    /* Restore every published scan structure before exposing the world. */
    pcc_gc_object_head = saved_head;
    pcc_gc_trace_stack_end();
    pcc_gc_trace_stack[0] = saved_trace0;
    pcc_gc_trace_stack[1] = saved_trace1;
    pcc_gc_gray_count_store_release(0);
    for (int i = 0; i < 2; i++) {
        if (pcc_gc_object_index_remove(&objects[i]) != nodes[i]) result = 36;
    }
    return result;
}

int main(void) {
    int backend = pcc_gc_backend();
    if (backend != 3 && backend != 4) return 1;
    pcc_gc_cms_stop_worker();
    if (pcc_current_thread_id() <= 0 || pcc_stop_the_world() != 0) return 2;
    if (!pcc_thread_owns_stopped_world()) return 3;
    pcc_py_gc_minor_graph_lock();
    if (!pcc_gc_backend4_no_refcounts || pcc_gc_mark_active || pcc_gc_gray_count_load_acquire()) return 4;
    for (int count = 1; count <= 4; count++) {
        for (int mark = 0; mark <= 1; mark++) {
            for (int invalid = 0; invalid <= 2; invalid++) {
                int result = run_case(count, mark, invalid);
                if (result) return result;
            }
        }
    }
    if (backend == 3) {
        for (int mark = 0; mark <= 1; mark++) {
            for (int locked = 0; locked <= 1; locked++) {
                int result = run_scan_case(mark, locked);
                if (result) return result;
            }
        }
    }
    pcc_py_gc_minor_graph_unlock();
    pcc_resume_world();
    if (backend == 3) printf("ZERO_EDGE_SCAN_OK cases=4\n");
    printf("TERMINAL_ROOT_OK gc=%d cases=24\n", backend);
    return 0;
}
