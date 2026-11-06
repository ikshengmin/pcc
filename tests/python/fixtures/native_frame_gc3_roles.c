/* Exact count-free GC3 native-root movement and late borrowed healing.
 * Contract: docs/project-intent.md, shared five-GC slot/root semantics.
 * The producer exits before the production conservative-pin/promotion cut:
 * main has only raw storage addresses, never an unrooted managed stack alias.
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
typedef struct Roots {
    PyObject *owned, *borrowed, *old_shell;
} Roots;
extern int64_t pcc_gc_native_frame_enter(void *, void *, int64_t);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, int64_t);
extern void pcc_gc_native_root_unbind(void *);
extern int64_t pcc_gc_visit_native_root_frames(int64_t, int64_t);
extern int64_t pcc_gc_generational_step(int64_t, int64_t);
extern int64_t pcc_thread_start(void *, void *, void *);
extern int64_t pcc_thread_join(void *, void *);
extern int64_t pcc_thread_all_stopped_at_gates(void);
extern int32_t pcc_thread_conservative_roots_enabled;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_object_is_address_pinned(PyObject *);
extern void *pcc_gc_forwarding_find(PyObject *);
extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);

static void *produce(void *argument) {
    Roots *roots = (Roots *)argument;
    pcc_thread_no_park_enter();
    PyObject *value = py_str_new("native-root", 11);
    int64_t status = 0;
    if (!value) status = 4;
    else if ((((int32_t *)value)[3] & 128) == 0) status = 15;
    else if (pcc_gc_object_is_address_pinned(value) != 0) status = 16;
    else if (*((int64_t *)value) != 1) status = 17;
    if (status == 0) {
        /* Both operations are inert in GC3, not a counted keeper owner. */
        py_incref(value);
        py_decref(value);
        if (*((int64_t *)value) != 1) status = 18;
        roots->owned = value;
        roots->borrowed = value;
        roots->old_shell = value;
    }
    pcc_thread_no_park_exit();
    return (void *)status; /* Trampoline unregisters and frees its snapshot. */
}

int main(void) {
    if (pcc_gc_set_backend(3) != 0) return 1;
    pcc_current_thread_id();
    if (pcc_gc_cms_stop_worker() != 0) return 13;
    if (pcc_thread_live_count() != 1) return 14;
    if (pcc_thread_conservative_roots_enabled != 1 ||
        pcc_gc_backend4_no_refcounts != 1) return 2;
    Roots *roots = (Roots *)calloc(1, sizeof(Roots));
    if (!roots) return 20;
    NativeFrame frame = {0};
    NativeRange ranges[2];
    pcc_thread_no_park_enter();
    if (pcc_gc_native_frame_enter(&frame, ranges, 2) != 0) return 3;
    /* Borrowed first deliberately exercises alias ordering. */
    void *borrowed_token = pcc_gc_native_root_bind_range(&roots->borrowed, -1);
    void *owned_token = pcc_gc_native_root_bind_range(&roots->owned, 1);
    if (!borrowed_token || !owned_token) return 21;
    pcc_thread_no_park_exit();
    void *thread = 0;
    if (pcc_thread_start(&thread, (void *)produce, roots) != 0) return 22;
    void *result = 0;
    if (pcc_thread_join(thread, &result) != 0) return 23;
    if (result != 0) return (int)(intptr_t)result;
    if (pcc_thread_live_count() != 1) return 24;
    /* Do not read roots' managed pointers before this real pinning cut.
     * Unlike the old fixture's naked native visit, generational_step runs
     * conservative pinning before reaching native-root promotion. It does
     * not retire the old shell, so late aliases can be checked in this cut. */
    if (pcc_stop_the_world() != 0) return 25;
    if (pcc_thread_all_stopped_at_gates() != 1) return 26;
    pcc_gc_generational_step(16, 0);
    if (roots->owned == roots->old_shell || roots->borrowed != roots->owned) return 6;
    if (py_str_len(roots->owned) != 11) return 7;
    if ((((int32_t *)roots->owned)[3] & 384) != 256) return 27;
    if (*((int64_t *)roots->old_shell) != 1 ||
        *((int64_t *)roots->owned) != 0) return 18;
    void *edge = pcc_gc_forwarding_find(roots->old_shell);
    if (!edge || ((void **)edge)[1] != (void *)roots->owned) return 19;
    if (pcc_gc_object_is_known_no_lock(roots->old_shell) != 0 ||
        pcc_gc_pointer_is_managed(roots->old_shell) != 1) return 28;
    if (pcc_gc_visit_native_root_frames(2, 0) != 2) return 8;
    if (*((int64_t *)roots->owned) != 0) return 9;
    /* The raw observer is valid only until this cut ends, not an RC keeper. */
    roots->borrowed = roots->old_shell;
    if (pcc_gc_visit_native_root_frames(1, 1) != 2) return 10;
    if (roots->borrowed != roots->owned) return 11;
    if (*((int64_t *)roots->owned) != 0 ||
        *((int64_t *)roots->old_shell) != 1) return 12;
    py_incref(roots->owned);
    py_decref(roots->owned);
    if (*((int64_t *)roots->owned) != 0) return 29;
    roots->borrowed = 0;
    roots->owned = 0;
    roots->old_shell = 0;
    pcc_gc_native_root_unbind(borrowed_token);
    pcc_gc_native_root_unbind(owned_token);
    if (pcc_gc_native_frame_leave(&frame) != 0) return 30;
    if (pcc_resume_world() != 0) return 31;
    free(roots);
    pcc_thread_unregister_current();
    puts("native-frame-gc3-count-free-healing-ok");
    return 0;
}
