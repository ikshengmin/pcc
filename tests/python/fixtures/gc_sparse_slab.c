#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_gc_granule_span(void *);
extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_trim_objects(void);
extern int64_t pcc_allocator_object_slab_counter(int64_t);

enum { OBJECTS = 20000 };
static PyObject *objects[OBJECTS];
static PyObject *survivors[OBJECTS];
static void *spans[OBJECTS];

static int crossing_payload(int whole_page) {
    enum { COUNT = 160, SIZE = 4096, UNIT = 16384 };
    unsigned char *cells[COUNT];
    unsigned char *keep = NULL;
    for (int i = 0; i < COUNT; i++) {
        cells[i] = pcc_allocator_alloc_object(SIZE);
        if (!cells[i]) return 11;
        void **span = pcc_gc_granule_span(cells[i]);
        if (!span) return 12;
        uintptr_t offset = (uintptr_t)cells[i] - (uintptr_t)span[2];
        if (!keep && offset / UNIT != (offset + SIZE - 1) / UNIT) keep = cells[i];
    }
    if (!keep) return 13;
    for (int i = 0; i < SIZE; i++) keep[i] = (unsigned char)(1 + i % 251);
    for (int i = 0; i < COUNT; i++) if (cells[i] != keep) free(cells[i]);
    pcc_allocator_trim_objects();
    int64_t partial = pcc_allocator_object_slab_counter(3);
    if (whole_page ? partial != 0 : partial != 2 * UNIT) return 14;
    for (int i = 0; i < COUNT; i++) {
        cells[i] = pcc_allocator_alloc_object(SIZE);
        if (!cells[i] || cells[i] == keep) return 15;
        cells[i][SIZE - 1] = 42;
    }
    for (int i = 0; i < SIZE; i++) if (keep[i] != (unsigned char)(1 + i % 251)) return 16;
    for (int i = 0; i < COUNT; i++) free(cells[i]);
    free(keep);
    return 0;
}

int main(int argc, char **argv) {
    int whole_page = argc > 1 && atoi(argv[1]) >= 65536;
    int span_count = 0;
    py_gc_init();
    py_gc_disable();
    if (argc == 3) return crossing_payload(whole_page);
    for (int i = 0; i < OBJECTS; i++) objects[i] = py_list_new(0);
    for (int i = 0; i < OBJECTS; i++) {
        void *span = pcc_gc_granule_span(objects[i]);
        if (!span) return 1;
        int j = 0;
        while (j < span_count && spans[j] != span) j++;
        if (j == span_count) {
            spans[j] = span;
            survivors[j] = objects[i];
            py_list_append(survivors[j], py_None);
            span_count++;
        } else py_decref(objects[i]);
    }
    pcc_allocator_trim_objects();
    int64_t partial = pcc_allocator_object_slab_counter(3);
    if (span_count < 4 || (whole_page ? partial != 0 : partial <= 0)) return 2;
    pcc_allocator_trim_objects();
    if (pcc_allocator_object_slab_counter(3) != partial) return 3;
    for (int i = 0; i < OBJECTS; i++) {
        objects[i] = py_list_new(0);
        py_list_append(objects[i], objects[i]);
    }
    if (!whole_page && pcc_allocator_object_slab_counter(3) >= partial) return 4;
    for (int j = 0; j < span_count; j++) {
        if (pcc_gc_granule_span(survivors[j]) != spans[j] || py_list_len(survivors[j]) != 1) return 5;
        PyObject *item = py_list_get(survivors[j], 0);
        if (item != py_None) return 6;
        py_decref(item);
        py_decref(survivors[j]);
    }
    for (int i = 0; i < OBJECTS; i++) {
        PyObject *item = py_list_get(objects[i], 0);
        if (item != objects[i]) return 7;
        py_decref(item);
        py_decref(objects[i]);
    }
    if (py_gc_collect() != OBJECTS) return 8;
    pcc_allocator_trim_objects();
    printf("spans %d partial bytes %lld whole slabs returned %lld\n",
           span_count, (long long)partial, (long long)pcc_allocator_object_slab_counter(0));
    return pcc_allocator_object_slab_counter(0) < span_count-1;
}
