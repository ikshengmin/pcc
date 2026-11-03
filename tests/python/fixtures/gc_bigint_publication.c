extern void *py_int_new_heap(long);
extern void *py_bigint_alloc(long);
extern void *py_bigint_to_pyobject(void *);
extern void py_decref(void *);
extern long pcc_gc_pointer_is_managed(void *);
extern void *pcc_gc_object_index_find(void *);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern long pcc_gc_debt_bytes;

/* Observation-only addresses are deliberately not owning roots. No header
   is read through them after collection; only the allocator registry is queried. */
static void *observed[2];

long bigint_publication_probe(void) {
    long before = pcc_gc_debt_bytes;
    void *heap = py_int_new_heap(9223372036854775807L);
    if (!heap) return -1;
    void *scratch = py_bigint_alloc(16);
    if (!scratch) return -2;
    /* A larger private capacity normalized to the value 2**64 + 1. */
    *(int *)((char *)scratch + @SIGN@) = 1;
    *(int *)((char *)scratch + @NDIGITS@) = 3;
    *(int *)((char *)scratch + @DIGITS@) = 1;
    *(int *)((char *)scratch + @DIGITS@ + 4) = 0;
    *(int *)((char *)scratch + @DIGITS@ + 8) = 1;
    void *normalized = py_bigint_to_pyobject(scratch);
    if (!normalized) return -3;
    long charged = pcc_gc_debt_bytes > before;
    long result = 0;
    if (pcc_gc_pointer_is_managed(heap)) result |= 1;
    if (pcc_gc_pointer_is_managed(normalized)) result |= 4;
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_object_index_find(heap)) result |= 2;
    if (pcc_gc_object_index_find(normalized)) result |= 8;
    pcc_py_gc_minor_graph_unlock();
    if (charged) result |= 16;
    observed[0] = heap;
    observed[1] = normalized;
    py_decref(heap);
    py_decref(normalized);
    return result;
}

long bigint_remaining(void) {
    return pcc_gc_pointer_is_managed(observed[0]) +
           pcc_gc_pointer_is_managed(observed[1]);
}

long bigint_clear_dead_stack(void) {
    volatile long words[4096];
    for (int i = 0; i < 4096; ++i) words[i] = 0;
    return words[0];
}
