/* A controlled final-mark phase, without disposing any object. The current
   volatile C root was absent from the initial seed. The real final remark
   must prevent it from entering the disposal set. */
extern void *py_list_new(long);
extern long pcc_gc_backend(void);
extern void *pcc_gc_object_index_find(void *);
extern long pcc_stop_the_world(void);
extern void pcc_resume_world(void);
extern long pcc_gc_complete_claimed_tracing_cycle(long, long);
extern int pcc_gc_backend4_no_refcounts;
extern int pcc_gc_mark_active;
extern int pcc_gc_trace_extension_roots_pending;
extern long pcc_gc_tracing_cycle_epoch;
extern long pcc_gc_tracing_finish_claim_epoch;
extern long pcc_gc_tracing_finish_claim_backend;

long remark_raw_root_probe(void) {
    if (!pcc_gc_backend4_no_refcounts) return -1;
    void *volatile raw = py_list_new(0);
    if (!raw) return -2;
    if (pcc_stop_the_world()) return -3;
    void *node = pcc_gc_object_index_find((void *)raw);
    if (!node) { pcc_resume_world(); return -5; }
    /* Internal node gc_refs is the count-free cycle-local conservative pin. */
    *(long *)((char *)node + 56) = 0;
    int flags = *(int *)((char *)raw + 12);
    *(int *)((char *)raw + 12) = (flags & ~(56 | 16384 | 1024)) | 8;
    long epoch = pcc_gc_tracing_cycle_epoch + 1;
    long backend = pcc_gc_backend();
    pcc_gc_tracing_cycle_epoch = epoch;
    pcc_gc_tracing_finish_claim_epoch = epoch;
    pcc_gc_tracing_finish_claim_backend = backend;
    pcc_gc_trace_extension_roots_pending = 0;
    pcc_gc_mark_active = 1;
    long completed = pcc_gc_complete_claimed_tracing_cycle(epoch, backend);
    int after = *(int *)((char *)raw + 12);
    *(int *)((char *)raw + 12) = after & ~1024;
    pcc_resume_world();
    if (!completed) return -4;
    return (after & 1024) != 0;
}
