extern long pcc_gc_user_finalizer_runs;
extern void py_user_del_dispatch(void *);
extern void py_gen_finalize(void *);
extern void *py_exc_new(long, const char *);
extern void py_raise_owned(void *);
extern void *py_current_exception(void);
extern void py_clear_exception(void);
long generator_finalizer_runs(void) { return pcc_gc_user_finalizer_runs; }
void generator_dispatch(void *object) { py_user_del_dispatch(object); }
void generator_finalize_direct(void *object) { py_gen_finalize(object); }
long generator_finalize_preserves_pending(void *object) {
    void *original = py_exc_new(3, "generator finalizer sentinel");
    if (!original) return -1;
    py_raise_owned(original);
    py_gen_finalize(object);
    long preserved = py_current_exception() == original;
    py_clear_exception();
    return preserved;
}
