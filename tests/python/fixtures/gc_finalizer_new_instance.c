extern void *py_class_new(const char *, void **, int, const char **, int);
extern void py_class_add_method(void *, const char *, void *);
extern void *py_instance_new(void *);
extern void py_instance_set_field(void *, int, void *);
extern void *py_int_from_i64(long);
extern void py_decref(void *);
extern void pcc_gc_store_root(void **, void *);
extern void pcc_gc_scheduler_root_register(void **);
extern void pcc_gc_scheduler_root_unregister(void **);

static void *temporary_roots[2];
static long finalizers;
static long created;
static long allocation_flags;

static void *new_during_finalizer(void *self) {
    finalizers++;
    if (finalizers == 1) {
        void *cls = *(void **)((char *)self + @CLS_OFFSET@);
        void *next = py_instance_new(cls);
        if (!next) return py_int_from_i64(0);
        py_instance_set_field(next, 0, next);
        created++;
        allocation_flags = *(int *)((char *)next + @FLAGS_OFFSET@);
        py_decref(next);
    }
    return py_int_from_i64(0);
}

long make_late_instance_graph(void) {
    for (int i = 0; i < 2; i++) pcc_gc_scheduler_root_register(&temporary_roots[i]);
    const char *fields[1] = {"link"};
    void *cls = py_class_new("LateInstanceClass", (void **)0, 0, fields, 1);
    if (!cls) return 1;
    pcc_gc_store_root(&temporary_roots[0], cls);
    py_class_add_method(cls, "__del__", (void *)new_during_finalizer);
    void *old = py_instance_new(cls);
    if (!old) return 2;
    pcc_gc_store_root(&temporary_roots[1], old);
    py_instance_set_field(old, 0, old);
    py_decref(old);
    py_decref(cls);
    for (int i = 0; i < 2; i++) {
        pcc_gc_store_root(&temporary_roots[i], (void *)0);
        pcc_gc_scheduler_root_unregister(&temporary_roots[i]);
    }
    return 0;
}

long clear_late_instance_stack(void) {
    volatile long words[4096];
    for (int i = 0; i < 4096; i++) words[i] = 0;
    return words[0];
}

long late_instance_finalizers(void) { return finalizers; }
long late_instance_created(void) { return created; }
long late_instance_allocation_flags(void) { return allocation_flags; }
