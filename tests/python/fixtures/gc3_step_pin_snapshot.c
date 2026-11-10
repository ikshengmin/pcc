/* Finite stopped-word/native kernel probe, not a complete collector runtime. */
extern void pcc_gc_conservative_step_pin_begin(void *);
extern void pcc_gc_conservative_step_pin_end(void *);
extern long pcc_gc_generational_object_is_address_pinned(void *);
extern void pcc_gc_pin_conservative_thread_roots(void);
int pcc_gc_backend_selected = 3;
int pcc_gc_backend4_no_refcounts = 1;
int pcc_gc_forwarding_population = 0;
void *pcc_gc_backend3_step_pin_snapshot;
void *pcc_gc_object_head;
static long objects[2][4], nodes[2][11], payload[4], owner[9], span[6];
static long storage[4096], used, freed, locks, snapshots;
static void *allocations[16];
static long allocation_count;
static long candidates[8], candidate_count;
static void *reverse_target;
void *malloc(long size) {
    long words = (size + 7) / 8;
    if (used + words > 4096 || allocation_count == 16) return (void *)0;
    long *result = storage + used;
    used += words;
    allocations[allocation_count++] = result;
    return result;
}
void free(void *value) {
    if (value == (void *)0) return;
    for (long i = 0; i < allocation_count; i++) {
        if (allocations[i] == value) {
            allocations[i] = (void *)0;
            freed++;
            return;
        }
    }
    /* Unknown pointers and double-free are not successful releases. */
    __builtin_trap();
}
void pcc_platform_abort(void) { __builtin_trap(); }
void pcc_py_gc_minor_graph_lock(void) { locks++; }
void pcc_py_gc_minor_graph_unlock(void) { locks--; }
long pcc_thread_world_stop_epoch_held(void) { return 1; }
long pcc_thread_snapshot_stopped_world_owner(void) { snapshots++; return 1; }
long pcc_thread_visit_conservative_words(void *callback, void *context) {
    for (long i = 0; i < candidate_count; i++)
        ((void (*)(void *, void *))callback)((void *)candidates[i], context);
    return candidate_count;
}
void *pcc_gc_object_index_find(void *object) {
    if (object == objects[0]) return nodes[0];
    if (object == objects[1]) return nodes[1];
    return (void *)0;
}
void *pcc_gc_forwarding_index_find(void *object) { return (void *)0; }
void *pcc_gc_forwarding_target_index_find(void *object) {
    return object == reverse_target ? nodes[1] : (void *)0;
}
long pcc_gc_static_literal_contains(void *object) { return 0; }
long pcc_gc_object_node_is_active(void *node) { return ((long *)node)[4] == 0; }
long pcc_gc_object_is_address_pinned(void *object) {
    long *node = pcc_gc_object_index_find(object);
    return node != (void *)0 && ((node[7] & 1) != 0 || node[10] != 0);
}
long pcc_gc_object_node_is_address_pinned(void *node, void *object) {
    /* Finite cross-object boundary: both listed nodes are indexed, with no
     * forwarding, static literals or header pins in this kernel fixture. */
    if (node != pcc_gc_object_index_find(object)) __builtin_trap();
    return pcc_gc_object_is_address_pinned(object);
}
void pcc_gc_mark_root_gray_if_known(void *object) { __builtin_trap(); }
void pcc_gc_shade_gray_atomic(void *object, long allow_black) { __builtin_trap(); }
void pcc_gc_trace_stack_push(void *object) { __builtin_trap(); }

int snapshot_probe(void) {
    long outer[4], inner[4];
    nodes[0][0] = (long)objects[0]; nodes[0][1] = 32;
    nodes[0][2] = (long)nodes[1];
    nodes[1][0] = (long)objects[1]; nodes[1][1] = 32;
    pcc_gc_object_head = nodes[0];
    candidate_count = 1; candidates[0] = (long)objects[0] + 9;
    pcc_gc_conservative_step_pin_begin(outer);
    if (pcc_gc_generational_object_is_address_pinned(objects[0]) != 1) return 1;
    if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 0) return 2;
    if (nodes[0][7] != 0 || nodes[1][7] != 0) return 3;
    candidates[0] = (long)objects[1] + 17;
    pcc_gc_conservative_step_pin_begin(inner);
    if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 1) return 4;
    pcc_gc_conservative_step_pin_end(inner);
    if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 1) return 5;
    pcc_gc_conservative_step_pin_end(outer);
    if (pcc_gc_backend3_step_pin_snapshot != (void *)0 || locks != 0) return 6;
    /* A fresh pause discards the invalidated scope and stale materialized pin. */
    candidate_count = 0; nodes[0][7] = 1;
    pcc_gc_conservative_step_pin_begin(outer);
    if (pcc_gc_generational_object_is_address_pinned(objects[0]) != 0) return 7;
    nodes[0][10] = 1;
    if (pcc_gc_generational_object_is_address_pinned(objects[0]) != 1) return 8;
    nodes[0][10] = 0; reverse_target = objects[1];
    if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 1) return 9;
    pcc_gc_conservative_step_pin_end(outer);
    reverse_target = (void *)0;
    nodes[0][6] = (long)owner; owner[8] = (long)span;
    span[1] = (long)payload; span[2] = 32;
    candidate_count = 1; candidates[0] = (long)payload + 7;
    pcc_gc_conservative_step_pin_begin(outer);
    if (pcc_gc_generational_object_is_address_pinned(objects[0]) != 1) return 10;
    if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 0) return 11;
    candidates[0] = (long)objects[1] + 9;
    pcc_gc_pin_conservative_thread_roots();
    if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 1) return 12;
    pcc_gc_conservative_step_pin_end(outer);
    /* Full scans also own a word-page table. Check every actual allocation,
     * rather than assuming the five capture buffers are the only storage. */
    if (allocation_count < 5 || freed != allocation_count || snapshots != 5 || locks != 0) return 13;
    /* The no-snapshot fallback preserves each collector's existing query. */
    for (int backend = 0; backend < 5; backend++) {
        pcc_gc_backend_selected = backend;
        nodes[0][7] = 1; nodes[1][7] = 0;
        if (pcc_gc_generational_object_is_address_pinned(objects[0]) != 1) return 14;
        if (pcc_gc_generational_object_is_address_pinned(objects[1]) != 0) return 15;
    }
    return 0;
}
