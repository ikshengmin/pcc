/* The collector's active mark is held open at a controlled boundary. The
 * indexed allocation, page record and list stores are real runtime paths.
 * This isolates the publication/color contract, not stop-world rendezvous.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern int32_t pcc_gc_mark_active;
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int64_t pcc_gc_page_mark_test(PyObject *);

static int32_t color(PyObject *object) {
    return py_header_flags_load(py_header(object)) & PY_FLAG_GC_COLOR_MASK;
}

static int fail(const char *where) {
    fprintf(stderr, "GC3_INDEXED_ALLOC_FAIL %s\n", where);
    return 1;
}

int main(void) {
    if (pcc_gc_backend() != 3) return fail("backend");
    py_gc_disable();
    PyObject *child_growth = py_list_new(0);
    PyObject *child_spare = py_list_new(0);
    PyObject *unwritten = py_list_new(0);
    if (!child_growth || !child_spare || !unwritten) return fail("old allocation");

    pcc_py_gc_minor_graph_lock();
    pcc_gc_set_mark_color(child_growth, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(child_spare, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(unwritten, PY_FLAG_GC_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    PyObject *growth = py_list_new(0);
    PyObject *spare = py_list_new(2);
    if (!growth || !spare) return fail("active allocation");
    void *growth_record = pcc_gc_object_index_find(growth);
    void *spare_record = pcc_gc_object_index_find(spare);
    if (!growth_record || !spare_record || growth_record == spare_record)
        return fail("index publication");
    if (color(growth) != PY_FLAG_GC_BLACK || color(spare) != PY_FLAG_GC_BLACK)
        return fail("header color");
    if (pcc_gc_page_mark_test(growth) != 1 || pcc_gc_page_mark_test(spare) != 1)
        return fail("page color");

    py_list_append(growth, child_growth);
    py_list_append(spare, child_spare);
    if (pcc_gc_object_index_find(growth) != growth_record ||
        pcc_gc_object_index_find(spare) != spare_record)
        return fail("record changed after store");
    if (color(child_growth) != PY_FLAG_GC_GRAY) return fail("growth child not shaded");
    if (color(child_spare) != PY_FLAG_GC_GRAY) return fail("spare child not shaded");
    if (pcc_gc_page_mark_test(child_growth) != 1
        || pcc_gc_page_mark_test(child_spare) != 1)
        return fail("child page color");
    if (color(unwritten) != PY_FLAG_GC_WHITE || pcc_gc_page_mark_test(unwritten) != 0)
        return fail("unwritten child shaded");
    if (py_list_get(growth, 0) != child_growth || py_list_get(spare, 0) != child_spare)
        return fail("stored identity");

    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    puts("GC3_INDEXED_ALLOC_OK");
    return 0;
}
