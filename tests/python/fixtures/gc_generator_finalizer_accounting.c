extern long pcc_gc_user_finalizer_runs;
extern void py_user_del_dispatch(void *);
extern void py_gen_finalize(void *);
extern void *py_exc_new(long, const char *);
extern void py_raise_owned(void *);
extern void *py_current_exception(void);
extern void py_clear_exception(void);
extern void pcc_gc_pin(void *);
extern void pcc_gc_unpin(void *);
/* Raw owned-C fixture: PyObjectHeader in runtime/include/py_runtime.h;
 * PY_FLAG_GC_PINNED in runtime/src/py_internal.h. Do not use the broader
 * address-pinned query: conservative roots/leases can hide a lost flag. */
enum { PYOBJECTHEADER_FLAGS_OFFSET = 12, PY_FLAG_GC_PINNED = 0x40 };
static int generator_pin_bit(void *object) {
    return *(int *)((char *)object + PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED;
}
long generator_finalizer_runs(void) { return pcc_gc_user_finalizer_runs; }
void generator_dispatch(void *object) { py_user_del_dispatch(object); }
void generator_finalize_direct(void *object) { py_gen_finalize(object); }
long generator_finalize_preserves_pending(void *object) {
    void *original = py_exc_new(3, "generator finalizer sentinel");
    if (!original) return -1;
    /* Borrow compiler-owned pins untouched. Each pin acquired here has one
     * matching unpin, even if the runtime under test loses its flag. */
    int own_object_pin = generator_pin_bit(object) == 0;
    int own_exception_pin = generator_pin_bit(original) == 0;
    if (own_object_pin) pcc_gc_pin(object);
    if (own_exception_pin) pcc_gc_pin(original);
    py_raise_owned(original);
    int object_pin_before = generator_pin_bit(object);
    int exception_pin_before = generator_pin_bit(original);
    py_gen_finalize(object);
    long preserved = py_current_exception() == original;
    preserved = preserved && generator_pin_bit(object) == object_pin_before;
    preserved = preserved && generator_pin_bit(original) == exception_pin_before;
    if (own_exception_pin) pcc_gc_unpin(original);
    if (own_object_pin) pcc_gc_unpin(object);
    py_clear_exception();
    return preserved;
}
