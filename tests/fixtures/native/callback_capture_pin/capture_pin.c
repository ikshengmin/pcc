/* Owned native PyFunc entry/header ABI, not a pure Python callback.
 * py_runtime.h exposes PyObjectHeader.flags and the constructors/call APIs.
 * It does not define the two flag names below. The harness verifies these
 * fixture values against py/py_abi_constants.py before compiling this file.
 * The existing exported i32 metric is a read-only diagnostic, never reset.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

enum {
    PROBE_PINNED = 64,
    PROBE_CONTINUATION_FACTORY = 67108864
};
extern int32_t pcc_gc_metric_pin;
static int32_t capture_frame_map[1] = {4};

static PyObject *return_captures(PyObject *captures, PyObject *args) {
    (void)args;
    py_incref(captures);
    return captures;
}

static PyObject *completed_captures(PyObject *captures, PyObject *args) {
    (void)args;
    return py_gen_completed(captures);
}

int main(int argc, char **argv) {
    if (argc != 4) return 90;
    int backend = atoi(argv[1]);
    int prior_pin = atoi(argv[2]);
    int factory = atoi(argv[3]);
    if (pcc_gc_set_backend(backend) != 0) return 91;
    PyObject *roots[4] = {NULL, NULL, NULL, NULL};
    pcc_gc_frame_enter(capture_frame_map, roots);

    PyObject *value = py_tuple_new(0);
    if (value == NULL) return 92;
    pcc_gc_pin(value);
    pcc_gc_store_root_take(&roots[0], value);
    pcc_gc_unpin(roots[0]);
    value = py_tuple_new(0);
    if (value == NULL) return 93;
    pcc_gc_pin(value);
    pcc_gc_store_root_take(&roots[1], value);
    if (factory) {
        value = py_func_new((void *)completed_captures, roots[0]);
    } else {
        value = py_func_new((void *)return_captures, roots[0]);
    }
    if (value == NULL) return 94;
    pcc_gc_pin(value);
    pcc_gc_store_root_take(&roots[2], value);
    if (factory) {
        ((PyObjectHeader *)roots[2])->flags |= PROBE_CONTINUATION_FACTORY;
    }
    if (prior_pin) pcc_gc_pin(roots[0]);
    int before_pin = ((PyObjectHeader *)roots[0])->flags & PROBE_PINNED;
    int before_metric = pcc_gc_metric_pin;
    value = py_func_call_kwargs(roots[2], roots[1], NULL);
    /* No helper, allocation, poll or new pin between the call and snapshot. */
    int after_metric = pcc_gc_metric_pin;
    int same = value == roots[0];
    int after_pin = value == NULL ? -1 : (((PyObjectHeader *)value)->flags & PROBE_PINNED);
    int metric_delta = after_metric - before_metric;
    int rc = 0;
    if (!same) rc = 30;
    else if (after_pin != before_pin) rc = 31;
    else if (metric_delta != 0) rc = 32;

    /* Root the returned owner before logging. Preserve its observed pin state
     * when releasing only this additional temporary lease during cleanup. */
    if (value != NULL) {
        pcc_gc_pin(value);
        pcc_gc_store_root_take(&roots[3], value);
    }
    printf("factory=%d prior=%d before=%d after=%d identity=%d metric_delta=%d\n",
           factory, prior_pin, before_pin, after_pin, same, metric_delta);
    if (roots[3] != NULL) {
        pcc_gc_unpin(roots[3]);
        if (after_pin == PROBE_PINNED) {
            ((PyObjectHeader *)roots[3])->flags |= PROBE_PINNED;
        }
        pcc_gc_store_root(&roots[3], NULL);
    }
    pcc_gc_unpin(roots[2]);
    pcc_gc_store_root(&roots[2], NULL);
    pcc_gc_unpin(roots[1]);
    pcc_gc_store_root(&roots[1], NULL);
    if (prior_pin) pcc_gc_unpin(roots[0]);
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    return rc;
}
