/* Real forwarding-edge publication, pointer healing and edge retirement.
   Fixture roots intentionally remain process-lifetime owners. No synthetic
   forwarding metadata or reference counts are written by this harness. */
extern int pcc_gc_read_barrier_enabled;
extern int pcc_gc_forwarding_population;
extern long long pcc_gc_backend(void);
extern long long pcc_gc_install_forwarding(void *, void *);
extern void pcc_gc_forwarding_remove(void *);
extern void *pcc_gc_load_ptr(void *, void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_gc_scheduler_root_register(void **);
extern void pcc_gc_store_root(void **, void *);
extern void *py_tuple_new(long long);
extern void *py_float_from_f64(double);
extern void py_tuple_set_item(void *, long long, void *);
extern void *py_int_from_i64(long long);
extern void py_incref(void *);
extern void py_decref(void *);
static void *roots[6];
static long long install_result;
static void **field(void *tuple, long long index) {
    return (void **)((char *)tuple + @TUPLE_ITEMS@ + index * 8);
}
static int prepare_pair(int base, void *trigger) {
    int scalar = pcc_gc_backend() == 3;
    void *old = scalar ? py_float_from_f64(17.25) : py_tuple_new(1);
    void *replacement = scalar ? py_float_from_f64(17.25) : py_tuple_new(1);
    void *owner = py_tuple_new(2);
    if (!old || !replacement || !owner) return 0;
    if (!scalar) {
        py_tuple_set_item(old, 0, py_int_from_i64(17));
        py_tuple_set_item(replacement, 0, py_int_from_i64(17));
    }
    py_tuple_set_item(owner, 0, trigger);
    py_tuple_set_item(owner, 1, old);
    pcc_gc_store_root(&roots[base], old);
    pcc_gc_store_root(&roots[base + 1], replacement);
    pcc_gc_store_root(&roots[base + 2], owner);
    py_decref(old);
    py_decref(replacement);
    py_decref(owner);
    return 1;
}
void *prepare_moving_readers(void *trigger) {
    for (int i = 0; i < 6; ++i) pcc_gc_scheduler_root_register(&roots[i]);
    if (!prepare_pair(0, trigger) || !prepare_pair(3, trigger)) return (void *)0;
    py_incref(roots[5]);
    return roots[5];
}
long long moving_read_roundtrip(void) {
    long long backend = pcc_gc_backend();
    if (pcc_gc_read_barrier_enabled != 0) return -1;
    long long installed = pcc_gc_install_forwarding(roots[0], roots[1]);
    if (backend < 3) {
        if (installed >= 0 || pcc_gc_read_barrier_enabled != 0) return -2;
        return pcc_gc_load_ptr(roots[2], field(roots[2], 1)) == roots[0];
    }
    if (installed != 0 || pcc_gc_read_barrier_enabled != 1) return -3;
    if (pcc_gc_load_ptr(roots[2], field(roots[2], 1)) != roots[1]) return -4;
    if (*field(roots[2], 1) != roots[1]) return -5;
    pcc_py_gc_minor_graph_lock();
    pcc_gc_forwarding_remove(roots[0]);
    pcc_py_gc_minor_graph_unlock();
    return pcc_gc_forwarding_population == 0 && pcc_gc_read_barrier_enabled == 0;
}
long long install_from_hash_callback(void) {
    if (pcc_gc_backend() < 3) return 0;
    install_result = pcc_gc_install_forwarding(roots[3], roots[4]);
    return install_result;
}
long long hash_reader_healed(void) {
    if (pcc_gc_backend() < 3) return *field(roots[5], 1) == roots[3];
    return install_result == 0 && *field(roots[5], 1) == roots[4]
        && pcc_gc_read_barrier_enabled == 1;
}
long long retire_hash_forwarding(void) {
    if (pcc_gc_backend() >= 3) {
        pcc_py_gc_minor_graph_lock();
        pcc_gc_forwarding_remove(roots[3]);
        pcc_py_gc_minor_graph_unlock();
    }
    return pcc_gc_forwarding_population == 0 && pcc_gc_read_barrier_enabled == 0;
}
