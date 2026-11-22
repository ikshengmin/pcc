/* Compare the view's normal base owner with its optional cached-buffer root.
 * Py_buffer.obj is a borrowed alias on this API; it is not a GC registration.
 */
#include "py_internal.h"
#include <Python.h>
#include <stdio.h>

extern int64_t pcc_gc_slot_is_runtime_root(void *slot);
extern void *pcc_capi_buffer_lease_head;

typedef struct {
    PyObject *exporter;
    int64_t lease_token;
    void *registration;
} CachedExporterRoot;

static int content_ok(const unsigned char *data, Py_ssize_t size) {
    return size == 4 && data && data[0] == 94 && data[1] == 20
        && data[2] == 96 && data[3] == 3;
}

static int check_cached_export(void) {
    PyObject *view_root = NULL;
    void *root = pcc_gc_scheduler_root_register_handle(&view_root);
    if (!root) return 2;
    const char payload[] = {94, 20, 96, 3};
    PyObject *exporter = py_bytes_new(payload, sizeof(payload));
    if (!exporter) return 3;
    PyObject *view = py_memoryview_new(exporter);
    if (!view) return 4;
    pcc_gc_store_root(&view_root, view);
    Py_buffer *buffer = PyMemoryView_GET_BUFFER(view_root);
    if (!buffer || buffer->obj != exporter || !content_ok(buffer->buf, buffer->len))
        return 5;
    const unsigned char *original_address = buffer->buf;
    Py_ssize_t original_size = buffer->len;
    int dedicated_root = 0;
    if (buffer->internal) {
        CachedExporterRoot *meta = buffer->internal;
        if (!meta->registration || meta->exporter != exporter) return 6;
        if (!pcc_gc_slot_is_runtime_root(&meta->exporter)) return 7;
        dedicated_root = 1;
    }
    py_decref(exporter);
    pcc_gc_collect(0);
    pcc_gc_collect(0);
    /* Read the original address before a cache refresh can rewrite buf. */
    if (!content_ok(original_address, original_size)) return 8;
    if (PyMemoryView_GET_BUFFER(view_root) != buffer || buffer->buf != original_address)
        return 9;
    pcc_gc_store_root(&view_root, NULL);
    pcc_gc_scheduler_root_unregister_handle(root);
    printf("GC3_MEMORYVIEW_EXPORT_OK dedicated-root=%d\n", dedicated_root);
    return 0;
}

static int explicitly_pinned(PyObject *object) {
    return (py_header_flags_load(py_header(object)) & PY_FLAG_GC_PINNED) != 0;
}

static int check_explicit_export(int release_view_first) {
    const char payload[] = {94, 20, 96, 3};
    PyObject *exporter = py_bytes_new(payload, sizeof(payload));
    if (!exporter || explicitly_pinned(exporter)) return 10;
    PyObject *view = py_memoryview_new(exporter);
    if (!view || explicitly_pinned(view)) return 11;
    if (pcc_capi_buffer_lease_head != NULL) return 24;
    Py_buffer direct, indirect;
    if (PyObject_GetBuffer(exporter, &direct, PyBUF_SIMPLE) != 0) return 12;
    if (PyObject_GetBuffer(view, &indirect, PyBUF_SIMPLE) != 0) return 13;
    if (!direct.internal || !indirect.internal || direct.internal == indirect.internal)
        return 14;
    if (direct.obj != exporter || indirect.obj != view || direct.buf != indirect.buf)
        return 15;
    const unsigned char *original = direct.buf;
    Py_ssize_t size = direct.len;
    if (!explicitly_pinned(exporter) || !explicitly_pinned(view)) return 16;
    py_decref(exporter);
    py_decref(view);

    pcc_gc_collect(0);
    if (!content_ok(original, size)) return 17;
    Py_buffer *first = release_view_first ? &indirect : &direct;
    Py_buffer *last = release_view_first ? &direct : &indirect;
    PyBuffer_Release(first);
    if (first->obj || first->internal || first->buf || !explicitly_pinned(exporter)) return 18;
    if (explicitly_pinned(view) == release_view_first || last->buf != original) return 19;
    if (pcc_capi_buffer_lease_head != last->internal) return 25;
    pcc_gc_collect(0);
    if (!content_ok(original, size)) return 20;
    PyBuffer_Release(last);
    if (last->obj || last->internal || last->buf) return 21;
    // At this point the pin contract, not time to reclamation, is observable.
    if (pcc_capi_buffer_lease_head || explicitly_pinned(exporter)) return 22;
    puts("GC3_EXPLICIT_BUFFER_PIN_OK");
    return 0;
}

int main(int argc, char **argv) {
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
    if (argc == 1) return check_cached_export();
    if (argc == 2) return check_explicit_export(argv[1][0] == 'v');
    return 23;
}
