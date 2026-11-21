/* Real foreign payload: its address outlives every collector callback. */
typedef struct PyObject PyObject;
extern PyObject *py_cpy_handle_new(void *payload);
extern void py_cpy_handle_set_release_fn(void (*release)(void *));
extern void Py_INCREF(PyObject *);
extern void Py_DECREF(PyObject *);
extern long long pcc_capi_refcnt(PyObject *);

static int token;
static long long release_count;
static long long wrong_payload;
static PyObject *held_handle;

static void record_foreign_release(void *payload) {
    if (payload != &token) __atomic_fetch_add(&wrong_payload, 1, 0);
    __atomic_fetch_add(&release_count, 1, 0);
}

PyObject *fixture_new_foreign_handle(void) {
    py_cpy_handle_set_release_fn(record_foreign_release);
    /* Forward the constructor's new managed owner; the fixture keeps none. */
    return py_cpy_handle_new(&token);
}

long long fixture_foreign_release_count(void) {
    if (__atomic_load_n(&wrong_payload, 0)) return -1;
    return __atomic_load_n(&release_count, 0);
}

long long fixture_foreign_handle_refcount_delta(PyObject *obj) {
    long long before = pcc_capi_refcnt(obj);
    Py_INCREF(obj);
    long long incremented = pcc_capi_refcnt(obj);
    Py_DECREF(obj);
    long long after = pcc_capi_refcnt(obj);
    if (after != before) return -1;
    return incremented - before;
}

void fixture_foreign_handle_hold(PyObject *obj) {
    Py_INCREF(obj);
    held_handle = obj;
}

void fixture_foreign_handle_drop(void) {
    PyObject *obj = held_handle;
    held_handle = 0;
    Py_DECREF(obj);
}
