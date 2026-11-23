#include "py_runtime.h"
#include <stdint.h>

extern int64_t pcc_gc_backend0_full_begin(void);
extern int64_t pcc_gc_backend0_full_step(void);
extern int64_t pcc_gc_backend0_full_counter(int64_t);
extern int64_t pcc_gc_backend0_full_set_slice_us(int64_t);
extern int32_t pcc_gc_backend0_young_request;

static PyObject *self_cycle(void) {
    PyObject *list = py_list_new(0);
    if (list) py_list_append(list, list);
    return list;
}

int main(void) {
    const int fanout = 32768;
    py_gc_init();
    PyObject *owner = py_list_new(0);
    PyObject *child = self_cycle();
    PyObject *dead = self_cycle();
    if (!owner || !child || !dead) return 10;
    for (int i = 0; i < fanout; i++) py_list_append(owner, child);
    for (int i = 0; i < 8192; i++) py_list_append(dead, dead);
    py_gc_collect();  /* All three objects are old. */
    py_decref(child); /* Now kept alive only by its self edge and owner. */
    py_decref(dead);  /* Now unreachable. */

    pcc_gc_backend0_full_set_slice_us(0);
    if (!pcc_gc_backend0_full_begin()) return 11;
    int subtract = 0, mark = 0;
    PyObject *replacement = 0;
    for (int i = 0; i < 3000 && pcc_gc_backend0_full_counter(3) != 0; i++) {
        int64_t phase = pcc_gc_backend0_full_counter(3);
        pcc_gc_backend0_full_step();
        if (phase == 2) {
            subtract++;
            if (subtract == 2) {
                PyObject *shifted = py_list_pop(owner, 0);
                if (shifted != child) return 21;
                py_list_append(owner, shifted);
                py_decref(shifted);
            }
        }
        if (phase == 3) {
            mark++;
            if (mark == 2) {
                /* Shift the existing backing slots and grow the list while
                 * a mark cursor may be mid-owner. The replacement becomes
                 * reachable only from this container. */
                PyObject *removed = py_list_pop(owner, 0);
                if (removed != child) return 12;
                py_decref(removed);
                replacement = self_cycle();
                if (!replacement) return 13;
                for (int j = 0; j < 8192; j++)
                    py_list_append(owner, replacement);
                py_decref(replacement);
            }
        }
    }
    if (pcc_gc_backend0_full_counter(0) != 1) return 14;
    if (subtract < fanout / 256 || mark < fanout / 256) return 15;
    if (!replacement) return 16;
    pcc_gc_backend0_young_request = 1;
    int64_t collected = pcc_gc_collect(0);
    pcc_gc_backend0_young_request = 0;
    if (collected != 1) return 17;
    PyObject *last = py_list_get(owner, fanout + 8191 - 1);
    if (last != replacement) return 18;
    py_decref(last);
    PyObject *first = py_list_get(owner, 0);
    if (first != child) return 19;
    py_decref(first);
    py_decref(owner);
    if (py_gc_collect() != 2) return 20;
    return 0;
}
