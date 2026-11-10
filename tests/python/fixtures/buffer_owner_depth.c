/* Owned C frontend fixture; public Py_buffer ABI only, no fake object headers.
 * source is a live managed c_obj argument retained by the Python caller.
 * The caller disables automatic GC. This is a depth/ABI regression, not a
 * demonstration of relocation or parking safety for arbitrary GetBuffer.
 */
typedef struct Buffer {
    void *buf;
    void *obj;
    long long len;
    long long itemsize;
    int readonly;
    int ndim;
    char *format;
    long long *shape;
    long long *strides;
    long long *suboffsets;
    void *internal;
} Buffer;
extern void *PyMemoryView_FromObject(void *);
extern int PyObject_CheckBuffer(void *);
extern int PyObject_GetBuffer(void *, Buffer *, int);
extern void PyBuffer_Release(Buffer *);
extern void py_decref(void *);

static int inspect(Buffer *view, void *outer, int writable) {
    char *data = (char *)view->buf;
    if (view->obj != outer || view->len != 7 || view->itemsize != 1) return 10;
    if (view->readonly != !writable || view->ndim != 1) return 11;
    if (!view->format || view->format[0] != 'B' || view->format[1]) return 12;
    if (!view->shape || *view->shape != 7) return 13;
    if (!view->strides || *view->strides != 1 || view->suboffsets) return 14;
    if (!view->internal || !data) return 15;
    if (data[0] != 'l' || data[1] != 'e' || data[2] != 'a' ||
        data[3] != 's' || data[4] != 'e' || data[5] != 0 || data[6] != 'x') return 16;
    return 0;
}

long long buffer_owner_depth(void *source, long long depth,
                             long long writable, long long reverse) {
    void *owned[64] = {0};
    void *outer = source;
    Buffer first = {0};
    Buffer second = {0};
    int created = 0;
    int result = 0;
    if (depth < 0 || depth > 64) return 1;
    while (created < depth) {
        void *next = PyMemoryView_FromObject(outer);
        if (!next) { result = 2; break; }
        owned[created] = next;
        created += 1;
        outer = next;
    }
    if (!result && PyObject_CheckBuffer(outer) != 1) result = 3;
    if (!result && PyObject_GetBuffer(outer, &first, 0x1c | writable) != 0) result = 4;
    if (!result) result = inspect(&first, outer, writable);
    if (!result && PyObject_GetBuffer(outer, &second, 0x1c | writable) != 0) result = 5;
    if (!result) result = inspect(&second, outer, writable);
    if (!result && (first.buf != second.buf || first.internal == second.internal)) result = 6;
    if (!result && writable) {
        ((char *)first.buf)[0] = 'L';
        if (((char *)second.buf)[0] != 'L') result = 7;
        ((char *)first.buf)[0] = 'l';
    }
    if (reverse) {
        PyBuffer_Release(&second);
        if (!result) result = inspect(&first, outer, writable);
        PyBuffer_Release(&first);
    } else {
        PyBuffer_Release(&first);
        if (!result) result = inspect(&second, outer, writable);
        PyBuffer_Release(&second);
    }
    if (!result && (first.obj || first.buf || first.internal ||
                    second.obj || second.buf || second.internal)) result = 8;
    PyBuffer_Release(&first);
    PyBuffer_Release(&second);
    while (created > 0) {
        created -= 1;
        void *view = owned[created];
        owned[created] = 0;
        py_decref(view);
    }
    return result;
}
