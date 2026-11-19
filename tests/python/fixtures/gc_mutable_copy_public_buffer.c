#include <Python.h>

/* The caller leases the live receiver for this C call. The public buffer
 * export is released before any source mutation, Python callback or return.
 * No access assumes the inline offset of a bytearray payload. */
long long fixture_buffer_copy_signature(PyObject *obj) {
    Py_buffer view;
    if (PyObject_GetBuffer(obj, &view, PyBUF_SIMPLE) != 0) return -1;
    long long n = view.len;
    long long result = -2;
    if (n >= 0 && n <= 16 && (n == 0 || view.buf != NULL) &&
            view.itemsize == 1 && (view.readonly == 0 || view.readonly == 1)) {
        const unsigned char *data = (const unsigned char *)view.buf;
        result = n * 1000000 + view.readonly * 100000;
        for (long long i = 0; i < n; i++) result += (i + 1) * data[i];
    }
    PyBuffer_Release(&view);
    return result;
}
