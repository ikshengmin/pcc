extern long pcc_gc_pressure_counter(long);
extern void pcc_thread_no_park_enter(void);
extern void pcc_thread_no_park_exit(void);
extern void pcc_gc_safepoint(void);
extern void *py_list_new(long);
extern void *py_bigint_alloc(long);
extern void py_decref(void *);
extern void free(void *);

long pressure_no_park_probe(void) {
    long interval = pcc_gc_pressure_counter(7);
    if (interval < 1 || interval > 1024) return -1;
    pcc_thread_no_park_enter();
    long before = pcc_gc_pressure_counter(2);
    long deferred = pcc_gc_pressure_counter(4);
    for (long i = 0; i < interval; ++i) {
        void *value = py_list_new(0);
        if (!value) { pcc_thread_no_park_exit(); return -2; }
        py_decref(value);
    }
    pcc_gc_safepoint();
    long valid = pcc_gc_pressure_counter(2) == before &&
                 pcc_gc_pressure_counter(4) > deferred &&
                 pcc_gc_pressure_counter(5) > 0;
    pcc_thread_no_park_exit();
    return valid;
}

long pressure_private_scratch_probe(void) {
    long before = pcc_gc_pressure_counter(0);
    void *scratch = py_bigint_alloc(32);
    if (!scratch) return -1;
    long unchanged = pcc_gc_pressure_counter(0) == before;
    free(scratch);
    return unchanged;
}
