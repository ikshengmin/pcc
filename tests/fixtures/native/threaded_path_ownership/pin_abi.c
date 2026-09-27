/* Compiled by the explicitly supplied pcc1 with --freestanding-libc.
 * Includes the public header to test void(PyObject*) rather than duplicating
 * that prototype. The object is an actual py_list_new allocation; only its
 * existing header is read. No fake managed header or collector-state writes.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int32_t pcc_gc_metric_pin;

static int probe(void) {
    if (pcc_threads_enabled() != 1) return 10;
    if (pcc_gc_backend() != EXPECTED_BACKEND) return 11;
    PyObject *object = py_list_new(0);
    if (!object) return 12;
    uint32_t *flags = (uint32_t *)((unsigned char *)object + 12);
    uint32_t before_flags = *flags;
    int32_t before_metric = pcc_gc_metric_pin;
    if ((before_flags & 64) != 0) return 13;
    pcc_gc_pin(object);
    if (*flags != (before_flags | 64)) return 14;
    if (pcc_gc_metric_pin != before_metric + 1) return 15;
    pcc_gc_unpin(object);
    if (*flags != before_flags) return 16;
    if (pcc_gc_metric_pin != before_metric) return 17;
    pcc_gc_pin((PyObject *)0);
    pcc_gc_unpin((PyObject *)0);
    pcc_gc_pin((PyObject *)(uintptr_t)3);
    pcc_gc_unpin((PyObject *)(uintptr_t)3);
    if (pcc_gc_metric_pin != before_metric) return 18;
    int64_t references = *(int64_t *)object;
    PyObject *slot = object;
    pcc_gc_pin(object);
    PyObject *taken = pcc_gc_take_pinned_slot(&slot, 0);
    if (taken != object || slot != 0) return 20;
    if (*flags != before_flags || *(int64_t *)object != references) return 21;
    if (pcc_gc_metric_pin != before_metric) return 22;
    /* Preserve an outer caller's non-counted pin bit while balancing only
     * this transfer's additional pin/metric entry. */
    pcc_gc_pin(object);
    slot = object;
    pcc_gc_pin(object);
    taken = pcc_gc_take_pinned_slot(&slot, 64);
    if (taken != object || slot != 0) return 23;
    if (*flags != (before_flags | 64) || *(int64_t *)object != references) return 24;
    if (pcc_gc_metric_pin != before_metric + 1) return 25;
    pcc_gc_unpin(object);
    slot = (PyObject *)(uintptr_t)3;
    if (pcc_gc_take_pinned_slot(&slot, 64) != (PyObject *)(uintptr_t)3 || slot != 0) return 26;
    if (pcc_gc_take_pinned_slot(&slot, 0) != 0 || slot != 0) return 27;
    if (pcc_gc_take_pinned_slot((PyObject **)0, 64) != 0) return 28;
    if (pcc_gc_metric_pin != before_metric) return 29;
    py_decref(object);
    return 0;
}

int main(void) {
    int result = probe();
    if (result != 0) return result;
    puts("pin-public-abi-and-metric-ok");
    return 0;
}
