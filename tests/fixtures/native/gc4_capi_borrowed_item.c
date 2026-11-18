/* Use the actual public forwarding installer and a managed container slot.
 * The GC owns all movement; the test never reads a forwarded source's body.
 * Each subprocess makes one independent borrow, with one frozen collector. */
#include "Python.h"
#include <stdint.h>
#include <stdio.h>

extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
static int address_pinned(PyObject *o) {
    pcc_py_gc_minor_graph_lock();
    int result = pcc_gc_object_is_address_pinned(o) != 0;
    pcc_py_gc_minor_graph_unlock();
    return result;
}

static PyObject *lookup(char kind, PyObject *owner, PyObject *key) {
    if (kind == 'l') return PyList_GetItem(owner, 0);
    if (kind == 't') return PyTuple_GetItem(owner, 0);
    if (kind == 'd') return PyDict_GetItem(owner, key);
    if (kind == 'e') return PyDict_GetItemWithError(owner, key);
    return PyDict_GetItemString(owner, "item");
}

static PyObject *equivalent_list(void) {
    PyObject *o = PyList_New(0);
    if (o == 0 || PyList_Append(o, (PyObject *)(uintptr_t)85) != 0) return 0;
    return o;
}

int main(int argc, char **argv) {
    if (argc != 4 || argv[1][1] || argv[2][1] || argv[3][1]) return 1;
    int gc = argv[1][0] - '0';
    char kind = argv[2][0], mode = argv[3][0];
    if (gc < 0 || gc > 4 || pcc_gc_set_backend(gc) != 0 || pcc_gc_backend() != gc) return 2;
    py_gc_disable();
    if (kind != 'l' && kind != 't' && kind != 'd' && kind != 'e' && kind != 's') return 3;
    if (mode != 'u' && mode != 'b' && mode != '1' && mode != '2') return 4;
    PyObject *from = equivalent_list(), *mid = equivalent_list();
    PyObject *terminal = equivalent_list(), *spare = equivalent_list();
    PyObject *key = PyUnicode_FromString("item");
    PyObject *owner = kind == 'l' ? PyList_New(0) : kind == 't' ? PyTuple_New(1) : PyDict_New();
    if (!from || !mid || !terminal || !spare || !owner || !key) return 5;
    pcc_gc_pin(owner);
    if (kind == 'l') { if (PyList_Append(owner, from) != 0) return 6; }
    else if (kind == 't') { Py_INCREF(from); if (PyTuple_SetItem(owner, 0, from) != 0) return 7; }
    else { if (PyDict_SetItem(owner, key, from) != 0) return 8; }
    PyObject *expected = from;
    if (address_pinned(from) || address_pinned(terminal)) return 9;
    if (mode == '1' || mode == '2') {
        if (gc != 4) return 10;
        if (pcc_gc_install_forwarding(from, mode == '1' ? terminal : mid) != 0) return 11;
        if (mode == '2' && pcc_gc_install_forwarding(mid, terminal) != 0) return 12;
        expected = terminal;
    }
    /* The non-moving reference count must be unchanged after a borrowed
       result. No GetItemRef/new-reference contract is used as an oracle. */
    Py_ssize_t before = Py_REFCNT(expected);
    PyObject *got = lookup(kind, owner, key);
    if (got != expected) return 21;
    if (PyList_Size(got) != 1 || Py_REFCNT(expected) != before) return 22;
    if (!address_pinned(expected)) return 23;
    if (gc == 4) {
        /* A clean target was not pinned in setup: the actual public commit
           must now refuse to move the returned address, not the old shell. */
        if (pcc_gc_install_forwarding(got, spare) != -2) return 24;
    }
    if (mode == 'b') {
        if (lookup(kind, owner, key) != got || Py_REFCNT(expected) != before) return 25;
    }
    if (PyErr_Occurred()) return 26;
    printf("BORROW_OK %d %c %c\n", gc, kind, mode);
    return 0;
}
