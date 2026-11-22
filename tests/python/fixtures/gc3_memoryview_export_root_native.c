/* Compare the view's normal base owner with its optional cached-buffer root.
 * Py_buffer.obj is a borrowed alias on this API; it is not a GC registration.
 */
#include <Python.h>
#include <stdio.h>

extern int64_t pcc_gc_slot_is_runtime_root(void *slot);

typedef struct {
    PyObject *exporter;
    int64_t lease_token;
    void *registration;
} CachedExporterRoot;

static int content_ok(const unsigned char *data, Py_ssize_t size) {
    return size == 4 && data && data[0] == 94 && data[1] == 20
        && data[2] == 96 && data[3] == 3;
}

int main(void) {
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
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
