/* Test input for the owned C frontend, not a production runtime fallback.
 * Keep pending TLS manipulation in C so Python error-edge lowering cannot
 * return before the real weakref invalidation/callback boundary is reached.
 */
extern void *py_exc_new(long long, const char *);
extern void py_raise_owned(void *);
extern void *py_current_exception(void);
extern void py_clear_exception(void);
extern void py_weakref_invalidate(void *);

long long weakref_callback_tls_is_clear(void) {
    return py_current_exception() == 0;
}

long long weakref_invalidate_preserves_pending(void *object) {
    void *original = py_exc_new(3, "weakref pending exception sentinel");
    if (!original) return -1;
    py_raise_owned(original);
    original = py_current_exception();
    if (!original) return -2;
    py_weakref_invalidate(object);
    long long preserved = py_current_exception() == original;
    /* The helper must return with clean TLS even when the assertion fails. */
    py_clear_exception();
    return preserved;
}
