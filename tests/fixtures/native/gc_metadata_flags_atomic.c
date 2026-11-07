#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
extern void pcc_gc_immortalize(PyObject *);
extern int64_t pcc_gc_metric_pin_balance(void);
extern PyObject *py_coroutine_class(void);
extern PyObject *py_continuation_class(void);

int main(void) {
    PyObject *object = (PyObject *)py_list_new(0);
    if (!object) return 1;
    PyObjectHeader *header = (PyObjectHeader *)object;
    /* Reserved high metadata bit; leave the real collector color alone.
       There is no concurrent mutation in this native ABI witness. */
    uint32_t prior = header->flags;
    header->flags = prior | 0x80000000u;
    int64_t balance = pcc_gc_metric_pin_balance();
    int64_t refs = header->refcount;
    pcc_gc_immortalize(object);
    if (header->flags != (prior | 0x80000000u | 65u)) return 2;
    if (header->refcount != refs) return 3;
    if (pcc_gc_metric_pin_balance() != balance + 1) return 4;
    pcc_gc_immortalize(object);
    if (header->flags != (prior | 0x80000000u | 65u)) return 5;
    if (pcc_gc_metric_pin_balance() != balance + 2) return 6;
    header->flags = prior | 65u;
    PyObject *coroutine = py_coroutine_class();
    PyObject *continuation = py_continuation_class();
    if (!coroutine || !continuation) return 7;
    if ((((PyObjectHeader *)coroutine)->flags & 1u) == 0) return 8;
    if ((((PyObjectHeader *)continuation)->flags & 1u) == 0) return 9;
    if (py_coroutine_class() != coroutine || py_continuation_class() != continuation) return 10;
    puts("METADATA_FLAGS_ATOMIC_OK");
    return 0;
}
