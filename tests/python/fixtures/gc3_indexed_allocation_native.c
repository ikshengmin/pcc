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
extern int64_t pcc_gc_try_init_ptr_locked(PyObject **, PyObject **);

static int32_t color(PyObject *object) {
    return py_header_flags_load(py_header(object)) & PY_FLAG_GC_COLOR_MASK;
}

static int fail(const char *where) {
    fprintf(stderr, "GC3_INDEXED_ALLOC_FAIL %s\n", where);
    return 1;
}

static int check_dictionary_first_store(void) {
    PyObject *dict = py_dict_new_presized(8);
    PyObject *key = py_str_new("gc3-first-store-key", sizeof("gc3-first-store-key") - 1);
    PyObject *value = py_list_new(0);
    PyObject *replacement = py_list_new(0);
    PyObject *control = py_list_new(0);
    PyObject *detached = py_list_new(0);
    PyObject *result = 0;
    if (!dict || !key || !value || !replacement || !control || !detached)
        return fail("dictionary test allocation");
    pcc_py_gc_minor_graph_lock();
    pcc_gc_set_mark_color(dict, PY_FLAG_GC_BLACK);
    pcc_gc_set_mark_color(key, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(value, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(replacement, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(control, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(detached, PY_FLAG_GC_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    int64_t accepted = pcc_gc_try_init_ptr_locked(&result, &detached);
    pcc_py_gc_minor_graph_unlock();
    if (!accepted || result != detached || color(detached) != PY_FLAG_GC_GRAY)
        return fail("ownerless initializer");

    py_dict_set(dict, key, value);
    if (color(key) != PY_FLAG_GC_GRAY || color(value) != PY_FLAG_GC_GRAY)
        return fail("dictionary first-store colors");
    if (pcc_gc_page_mark_test(key) != 1 || pcc_gc_page_mark_test(value) != 1)
        return fail("dictionary first-store page marks");
    if (py_dict_get(dict, key) != value) return fail("dictionary first-store identity");
    py_dict_set(dict, key, replacement);
    if (color(replacement) != PY_FLAG_GC_GRAY || py_dict_get(dict, key) != replacement)
        return fail("dictionary replace");
    if (color(control) != PY_FLAG_GC_WHITE || pcc_gc_page_mark_test(control) != 0)
        return fail("unwritten dictionary control");
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    puts("GC3_DICT_FIRST_STORE_OK");
    return 0;
}

static int check_generator_frame_store(void) {
    PyObject *frame = py_gen_frame_new(2);
    PyObject *value = py_list_new(0);
    PyObject *replacement = py_list_new(0);
    PyObject *other = py_list_new(0);
    if (!frame || !value || !replacement || !other) return fail("generator allocation");
    pcc_py_gc_minor_graph_lock();
    pcc_gc_set_mark_color(frame, PY_FLAG_GC_BLACK);
    pcc_gc_set_mark_color(value, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(replacement, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(other, PY_FLAG_GC_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    PyListObject *backing = (PyListObject *)frame;
    py_gen_frame_set(frame, 0, value);
    if (backing->items[0] != value || color(value) != PY_FLAG_GC_GRAY
        || pcc_gc_page_mark_test(value) != 1)
        return fail("generator first store");
    py_gen_frame_set(frame, 0, replacement);
    if (backing->items[0] != replacement || color(replacement) != PY_FLAG_GC_GRAY
        || pcc_gc_page_mark_test(replacement) != 1)
        return fail("generator replacement");
    py_gen_frame_set(frame, 0, NULL);
    if (backing->items[0] != NULL || color(other) != PY_FLAG_GC_WHITE
        || pcc_gc_page_mark_test(other) != 0)
        return fail("generator clear");
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    puts("GC3_GENERATOR_STORE_OK");
    return 0;
}

/* Call the actual fast helper and check its status: the public dict
 * adapter hides a fast-path refusal by running the rooted slow path. */
extern int64_t user_py_dict__dict_gc0_materialize(PyObject *, int64_t, PyObject **);
extern int32_t pcc_diagnostics_runtime_log_fast_state;

enum { DICT_MATERIALIZE_ITEMS = 2 };

static int check_dictionary_private_materialization(void) {
    PyObject *dict = py_dict_new_presized(8);
    PyObject *key = py_str_new("gc3-materialize-key", sizeof("gc3-materialize-key") - 1);
    PyObject *value = py_list_new(0);
    PyObject *control = py_list_new(0);
    if (!dict || !key || !value || !control)
        return fail("dictionary materialization input");
    py_dict_set(dict, key, value);

    pcc_py_gc_minor_graph_lock();
    pcc_gc_set_mark_color(dict, PY_FLAG_GC_BLACK);
    pcc_gc_set_mark_color(key, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(value, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(control, PY_FLAG_GC_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    PyObject *out = NULL;
    __atomic_store_n(&pcc_diagnostics_runtime_log_fast_state, 1, __ATOMIC_RELEASE);
    int64_t refused = user_py_dict__dict_gc0_materialize(dict, DICT_MATERIALIZE_ITEMS, &out);
    __atomic_store_n(&pcc_diagnostics_runtime_log_fast_state, 0, __ATOMIC_RELEASE);
    if (refused || out || color(key) != PY_FLAG_GC_WHITE || color(value) != PY_FLAG_GC_WHITE)
        return fail("refused materialization changed output or colors");

    int64_t accepted = user_py_dict__dict_gc0_materialize(dict, DICT_MATERIALIZE_ITEMS, &out);
    if (accepted != 1 || !out || color(out) != PY_FLAG_GC_BLACK
        || !pcc_gc_object_index_find(out) || pcc_gc_page_mark_test(out) != 1)
        return fail("published list is not indexed black");
    PyListObject *published = (PyListObject *)out;
    if (published->length != 1 || !published->items || !published->items[0])
        return fail("one committed pair");
    PyObject *pair = published->items[0];
    if (color(pair) != PY_FLAG_GC_BLACK || !pcc_gc_object_index_find(pair)
        || pcc_gc_page_mark_test(pair) != 1)
        return fail("published tuple is not indexed black");
    if (color(key) != PY_FLAG_GC_GRAY || color(value) != PY_FLAG_GC_GRAY
        || pcc_gc_page_mark_test(key) != 1 || pcc_gc_page_mark_test(value) != 1)
        return fail("published tuple children not shaded");
    if (py_tuple_getitem(pair, 0) != key || py_tuple_getitem(pair, 1) != value
        || color(control) != PY_FLAG_GC_WHITE || pcc_gc_page_mark_test(control) != 0)
        return fail("tuple identity or independent white control");

    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    puts("GC3_DICT_PRIVATE_MATERIALIZATION_OK");
    return 0;
}

static PyObject *registered_root;

static int check_managed_and_root_stores(void) {
    PyObject *owner = py_gen_frame_new(1);
    PyObject *child = py_list_new(0);
    PyObject *rooted = py_list_new(0);
    if (!owner || !child || !rooted) return fail("heap and root allocation");
    void *handle = pcc_gc_scheduler_root_register_handle(&registered_root);
    if (!handle) return fail("root registration");
    pcc_py_gc_minor_graph_lock();
    pcc_gc_set_mark_color(owner, PY_FLAG_GC_BLACK);
    pcc_gc_set_mark_color(child, PY_FLAG_GC_WHITE);
    pcc_gc_set_mark_color(rooted, PY_FLAG_GC_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    PyObject **field = &((PyListObject *)owner)->items[0];
    pcc_gc_store_ptr(owner, field, child);
    if (*field != child || color(child) != PY_FLAG_GC_GRAY
        || pcc_gc_page_mark_test(child) != 1)
        return fail("heap store publication");
    if (color(rooted) != PY_FLAG_GC_WHITE || pcc_gc_page_mark_test(rooted) != 0)
        return fail("separate root still white");
    pcc_gc_store_root(&registered_root, rooted);
    if (registered_root != rooted || color(rooted) != PY_FLAG_GC_GRAY
        || pcc_gc_page_mark_test(rooted) != 1)
        return fail("root store publication");

    pcc_gc_store_ptr(owner, field, NULL);
    if (*field || registered_root != rooted) return fail("heap clear touches root");
    pcc_gc_store_root(&registered_root, NULL);
    if (registered_root) return fail("root clear");
    pcc_gc_scheduler_root_unregister_handle(handle);
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    puts("GC3_MANAGED_AND_ROOT_STORES_OK");
    return 0;
}

int main(int argc, char **argv) {
    if (pcc_gc_backend() != 3) return fail("backend");
    py_gc_disable();
    if (argc == 2 && argv[1][0] == 'd') return check_dictionary_first_store();
    if (argc == 2 && argv[1][0] == 'm') return check_dictionary_private_materialization();
    if (argc == 2 && argv[1][0] == 'g') return check_generator_frame_store();
    if (argc == 2 && argv[1][0] == 's') return check_managed_and_root_stores();
    if (argc != 1) return fail("mode");
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
