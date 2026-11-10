/* Contract: docs/project-intent.md, shared slot/root and buffer semantics.
 * Run with a source-matched owned runtime. No mock promotion or header writes.
 * Every mutator that observes managed addresses exits before main collects.
 * Main retains only this raw malloc context and registered root-cell addresses,
 * following native_frame_gc3_roles.c; its stack cannot accidentally pin the
 * exporter or make the uncached movement control pass without a real copy.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct NativeRange { void **slots; int64_t count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    int64_t used, capacity, owner;
} NativeFrame;

/* Public 80-byte Py_buffer ABI, without mixing CPython's object header into
 * the PCC runtime header. The owning allocation adds inline shape/stride. */
typedef struct Buffer {
    void *buf;
    PyObject *obj;
    int64_t len, itemsize;
    int32_t readonly, ndim;
    char *format;
    int64_t *shape, *strides, *suboffsets;
    void *internal;
} Buffer;
enum {
    EXPORTER, INNER, OUTER, SIBLING, CONTROL, CONTROL_VIEW, TARGET, ROOT_COUNT
};
enum { PAYLOAD_SIZE = 7, SMALL_STEPS = 96, RELEASE_COLLECTIONS = 8 };
typedef struct Fixture {
    PyObject *roots[ROOT_COUNT];
    PyObject *original_exporter, *original_control;
    Buffer *outer_buffer, *sibling_buffer;
    void *outer_internal, *sibling_internal;
    int64_t exporter_id, control_id;
    int64_t writable, checks, control_moved;
} Fixture;

extern int64_t pcc_gc_native_frame_enter(void *, void *, int64_t);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, int64_t);
extern void pcc_gc_native_root_unbind(void *);
extern int64_t pcc_gc_generational_step(int64_t, int64_t);
extern int64_t pcc_thread_start(void *, void *, void *);
extern int64_t pcc_thread_join(void *, void *);
extern int64_t pcc_thread_all_stopped_at_gates(void);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_object_is_address_pinned(PyObject *);
extern void *pcc_gc_forwarding_find(PyObject *);
extern int32_t pcc_thread_conservative_roots_enabled;
extern int32_t pcc_gc_backend4_no_refcounts;
extern Buffer *pcc_PyMemoryView_GET_BUFFER(PyObject *);

static const char payload[PAYLOAD_SIZE] = {'l', 'e', 'a', 's', 'e', 0, 'x'};

static void require(int condition, int code, const char *where) {
    if (!condition) {
        fprintf(stderr, "GC3_BUFFER_FAIL code=%d where=%s\n", code, where);
        _Exit(code);
    }
}
static PyObject *base(PyObject *view) {
    return *(PyObject **)((char *)view + 16);
}
static Buffer *cache(PyObject *view) {
    return *(Buffer **)((char *)view + 24);
}
static void check_payload(PyObject *object, int writable) {
    require(object != 0, 20, "live_exporter");
    require(*(int64_t *)((char *)object + 16) == PAYLOAD_SIZE,
            21, "exporter_length");
    char *bytes = (char *)object + 24;
    for (int index = 0; index < PAYLOAD_SIZE; index++) {
        char expected = index == 0 && writable ? 'L' : payload[index];
        require(bytes[index] == expected, 22, "exporter_payload");
    }
}
static void check_buffer(Buffer *buffer, PyObject *owner, PyObject *exporter,
                         void *internal, int writable) {
    /* Deliberately read the originally returned pointer. Do not call either
     * GET_BUFFER or GET_BASE here: refreshing would conceal stale aliases. */
    require(buffer != 0 && buffer->buf == (char *)exporter + 24,
            23, "retained_buf_address");
    require(buffer->obj == owner, 24, "retained_obj_identity");
    require(buffer->len == PAYLOAD_SIZE && buffer->itemsize == 1,
            25, "retained_size");
    require(buffer->readonly == !writable && buffer->ndim == 1,
            26, "retained_flags");
    require(buffer->format && buffer->format[0] == 'B' && !buffer->format[1],
            27, "retained_format");
    require(buffer->shape == (int64_t *)((char *)buffer + 80) &&
            buffer->strides == (int64_t *)((char *)buffer + 88),
            28, "inline_metadata_addresses");
    require(*buffer->shape == PAYLOAD_SIZE && *buffer->strides == 1 &&
            !buffer->suboffsets, 29, "inline_metadata_values");
    require(internal && buffer->internal == internal, 30, "opaque_owner_survives");
    for (int index = 0; index < PAYLOAD_SIZE; index++) {
        char expected = index == 0 && writable ? 'L' : payload[index];
        require(((char *)buffer->buf)[index] == expected, 31, "retained_payload");
    }
}
static void put_new(Fixture *fixture, int index, PyObject *object) {
    require(object != 0, 32, "constructor");
    pcc_gc_store_root_take(&fixture->roots[index], object);
}
static PyObject *new_exporter(int writable) {
    PyObject *bytes = py_bytes_new(payload, PAYLOAD_SIZE);
    require(bytes != 0, 33, "bytes_constructor");
    if (!writable) return bytes;
    PyObject *array = py_bytearray_from_obj(bytes);
    py_decref(bytes);
    return array;
}
static void *produce(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    pcc_thread_no_park_enter();
    require(pcc_gc_foreign_lease_count_quiescent() == 0, 34, "initial_leases");
    put_new(fixture, EXPORTER, new_exporter(fixture->writable));
    put_new(fixture, INNER, py_memoryview_new(fixture->roots[EXPORTER]));
    put_new(fixture, OUTER, py_memoryview_new(fixture->roots[INNER]));
    put_new(fixture, SIBLING, py_memoryview_new(fixture->roots[EXPORTER]));
    put_new(fixture, CONTROL, new_exporter(fixture->writable));
    put_new(fixture, CONTROL_VIEW, py_memoryview_new(fixture->roots[CONTROL]));
    put_new(fixture, TARGET, new_exporter(fixture->writable));
    fixture->original_exporter = fixture->roots[EXPORTER];
    fixture->original_control = fixture->roots[CONTROL];
    fixture->exporter_id = pcc_gc_object_id(fixture->roots[EXPORTER]);
    fixture->control_id = pcc_gc_object_id(fixture->roots[CONTROL]);
    require(fixture->exporter_id > 0 && fixture->control_id > 0,
            35, "initial_identity");
    require((((int32_t *)fixture->roots[CONTROL])[3] & 128) != 0 &&
            pcc_gc_object_is_address_pinned(fixture->roots[CONTROL]) == 0,
            36, "uncached_control_is_movable_young");
    require(!cache(fixture->roots[CONTROL_VIEW]), 37, "uncached_control");
    int64_t scheduler_before = pcc_gc_scheduler_root_count();
    fixture->outer_buffer = pcc_PyMemoryView_GET_BUFFER(fixture->roots[OUTER]);
    fixture->sibling_buffer = pcc_PyMemoryView_GET_BUFFER(fixture->roots[SIBLING]);
    require(fixture->outer_buffer && fixture->sibling_buffer &&
            fixture->outer_buffer != fixture->sibling_buffer,
            38, "two_independent_caches");
    fixture->outer_internal = fixture->outer_buffer->internal;
    fixture->sibling_internal = fixture->sibling_buffer->internal;
    require(fixture->outer_internal != fixture->sibling_internal,
            39, "two_independent_owners");
    require(pcc_gc_foreign_lease_count_quiescent() == 2 &&
            pcc_gc_scheduler_root_count() == scheduler_before + 2,
            40, "two_counted_leases_and_registered_owners");
    if (fixture->writable) {
        ((char *)fixture->outer_buffer->buf)[0] = 'L';
        ((char *)fixture->roots[TARGET] + 24)[0] = 'L';
    }
    /* The exporter has no direct fixture root during the retention phase. */
    pcc_gc_store_root(&fixture->roots[EXPORTER], 0);
    pcc_thread_no_park_exit();
    return 0;
}
static void *check_retained(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    pcc_thread_no_park_enter();
    PyObject *exporter = base(fixture->roots[INNER]);
    require(exporter == fixture->original_exporter &&
            pcc_gc_object_id(exporter) == fixture->exporter_id,
            41, "leased_exporter_identity");
    require(!pcc_gc_forwarding_find(exporter), 42, "no_leased_forwarding");
    require(pcc_gc_object_is_address_pinned(exporter) == 1,
            43, "lease_pins_exporter");
    require(pcc_gc_install_forwarding(exporter, fixture->roots[TARGET]) == -2,
            44, "public_forwarding_rejected_while_leased");
    require(!pcc_gc_forwarding_find(exporter), 45, "rejected_install_is_atomic");
    check_payload(exporter, fixture->writable);
    require(cache(fixture->roots[SIBLING]) == fixture->sibling_buffer,
            46, "sibling_cache_identity");
    check_buffer(fixture->sibling_buffer, exporter, exporter,
                 fixture->sibling_internal, fixture->writable);
    if (fixture->outer_buffer) {
        require(cache(fixture->roots[OUTER]) == fixture->outer_buffer,
                47, "outer_cache_identity");
        check_buffer(fixture->outer_buffer, fixture->roots[INNER], exporter,
                     fixture->outer_internal, fixture->writable);
    }
    if (fixture->roots[CONTROL] != fixture->original_control) {
        require(pcc_gc_object_id(fixture->roots[CONTROL]) == fixture->control_id,
                49, "uncached_identity_preserved");
        require((((int32_t *)fixture->roots[CONTROL])[3] & 384) == 256,
                50, "uncached_copy_is_old");
        check_payload(fixture->roots[CONTROL], 0);
        fixture->control_moved = 1;
    }
    require(!cache(fixture->roots[CONTROL_VIEW]), 51, "control_still_has_no_cache");
    fixture->checks++;
    pcc_thread_no_park_exit();
    return 0;
}
static void *drop_outer(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    pcc_thread_no_park_enter();
    require(fixture->control_moved == 1, 52, "real_uncached_copy_required");
    /* A budget-one cut may copy the root before reaching the view's slot.
     * Require final convergence, not an unpromised atomic multi-root scan. */
    require(base(fixture->roots[CONTROL_VIEW]) == fixture->roots[CONTROL],
            48, "uncached_view_base_healed");
    fixture->outer_buffer = 0;
    fixture->outer_internal = 0;
    pcc_gc_store_root(&fixture->roots[OUTER], 0);
    pcc_thread_no_park_exit();
    return 0;
}
static void *drop_last_cache(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    pcc_thread_no_park_enter();
    /* Keep the terminal alive after all views die, to test lease release
     * rather than confusing a freed exporter with forwarding rejection. */
    pcc_gc_store_root(&fixture->roots[EXPORTER], base(fixture->roots[INNER]));
    fixture->sibling_buffer = 0;
    fixture->sibling_internal = 0;
    pcc_gc_store_root(&fixture->roots[SIBLING], 0);
    pcc_gc_store_root(&fixture->roots[INNER], 0);
    pcc_thread_no_park_exit();
    return 0;
}
static void *check_released(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    pcc_thread_no_park_enter();
    PyObject *exporter = fixture->roots[EXPORTER];
    require(exporter == fixture->original_exporter, 53, "released_exporter_live");
    require(pcc_gc_object_is_address_pinned(exporter) == 0,
            54, "last_cache_unpins_exporter");
    require(pcc_gc_install_forwarding(exporter, fixture->roots[TARGET]) == 0,
            55, "public_forwarding_readmitted_after_release");
    void *edge = pcc_gc_forwarding_find(exporter);
    require(edge && ((PyObject **)edge)[1] == fixture->roots[TARGET],
            56, "real_public_forwarding_edge");
    require(pcc_gc_object_id(fixture->roots[TARGET]) == fixture->exporter_id,
            57, "public_forwarding_identity_preserved");
    require(pcc_gc_load_ptr(0, &fixture->roots[EXPORTER]) == fixture->roots[TARGET],
            58, "released_root_heals");
    check_payload(fixture->roots[TARGET], fixture->writable);
    pcc_thread_no_park_exit();
    return 0;
}
static void worker(void *function, Fixture *fixture) {
    void *thread = 0;
    void *result = 0;
    require(pcc_thread_start(&thread, function, fixture) == 0, 60, "thread_start");
    require(pcc_thread_join(thread, &result) == 0 && !result, 61, "thread_join");
    require(pcc_thread_live_count() == 1, 62, "observer_snapshot_removed");
}
static void collect_until_leases(int64_t expected) {
    for (int index = 0; index < RELEASE_COLLECTIONS; index++) {
        pcc_gc_collect(0);
        if (pcc_gc_foreign_lease_count_quiescent() == expected) return;
    }
    require(0, 63, "unreachable_view_releases_lease_without_owner_cycle");
}
static void check_reset_rejected(int64_t expected) {
    /* Call from main without graph/no-park/STW ownership. Otherwise the
     * setter could reject before reaching its foreign-lease admission gate. */
    require(pcc_thread_no_park_depth() == 0 &&
            pcc_gc_foreign_lease_count_quiescent() == expected,
            59, "reset_preconditions");
    require(pcc_gc_set_backend(3) == -1 &&
            pcc_gc_foreign_lease_count_quiescent() == expected,
            64, "same_backend_reset_rejected_without_losing_leases");
}
int main(int argc, char **argv) {
    require(argc == 2, 1, "usage");
    require(sizeof(Buffer) == 80, 2, "public_buffer_abi");
    require(pcc_gc_set_backend(3) == 0, 3, "select_gc3");
    pcc_current_thread_id();
    require(pcc_gc_cms_stop_worker() == 0 && pcc_thread_live_count() == 1,
            4, "sequential_runtime");
    /* Current supported GC3 configuration is count-free. Historical tracing
     * environment flags do not select a counted GC3 execution lane. */
    require(pcc_thread_conservative_roots_enabled == 1 &&
            pcc_gc_backend4_no_refcounts == 1, 5, "actual_count_free_mode");
    Fixture *fixture = (Fixture *)calloc(1, sizeof(Fixture));
    require(fixture != 0, 6, "fixture_allocation");
    fixture->writable = argv[1][0] == '1';
    NativeFrame frame = {0};
    NativeRange ranges[1];
    pcc_thread_no_park_enter();
    require(pcc_gc_native_frame_enter(&frame, ranges, 1) == 0, 7, "frame_enter");
    void *token = pcc_gc_native_root_bind_range(fixture->roots, ROOT_COUNT);
    require(token != 0, 8, "register_owning_roots");
    pcc_thread_no_park_exit();
    worker((void *)produce, fixture);
    check_reset_rejected(2);
    for (int index = 0; index < SMALL_STEPS; index++) {
        require(pcc_stop_the_world() == 0, 9, "stop_world");
        require(pcc_thread_all_stopped_at_gates() == 1, 10, "complete_stop");
        /* Main has not loaded any managed root value, even for a check. */
        pcc_gc_generational_step(1, 0);
        require(pcc_resume_world() == 0, 11, "resume_world");
        worker((void *)check_retained, fixture);
    }
    require(pcc_gc_foreign_lease_count_quiescent() == 2, 12, "both_caches_live");
    worker((void *)drop_outer, fixture);
    collect_until_leases(1);
    check_reset_rejected(1);
    worker((void *)check_retained, fixture);
    worker((void *)drop_last_cache, fixture);
    collect_until_leases(0);
    worker((void *)check_released, fixture);
    pcc_thread_no_park_enter();
    for (int index = 0; index < ROOT_COUNT; index++)
        pcc_gc_store_root(&fixture->roots[index], 0);
    pcc_gc_native_root_unbind(token);
    require(pcc_gc_native_frame_leave(&frame) == 0, 13, "frame_leave");
    pcc_thread_no_park_exit();
    free(fixture);
    pcc_thread_unregister_current();
    puts("GC3_CACHED_MEMORYVIEW_BUFFER_OK");
    return 0;
}
