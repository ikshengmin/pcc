/* Exact GC3 owning/borrowed healing, including a previously forwarded value.
 * C carries no managed stackmap for these cells; native range traversal must
 * actually rewrite them. This is a PCC-owned C test, not a host-cc oracle.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

typedef struct NativeRange { void **slots; int64_t count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    int64_t used, capacity, owner;
} NativeFrame;
extern int64_t pcc_gc_native_frame_enter(void *, void *, int64_t);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, int64_t);
extern void pcc_gc_native_root_unbind(void *);
extern int64_t pcc_gc_visit_native_root_frames(int64_t, int64_t);
extern int32_t pcc_thread_conservative_roots_enabled;
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_object_is_address_pinned(PyObject *);
extern void *pcc_gc_forwarding_find(PyObject *);
extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);

int main(void) {
    if (pcc_gc_set_backend(3) != 0) return 1;
    pcc_current_thread_id();
    /* This closed copy test owns one mutator. Quiesce the real background
     * worker through its lifecycle API, rather than overriding the gate. */
    if (pcc_gc_cms_stop_worker() != 0) return 13;
    if (pcc_thread_live_count() != 1) return 14;
    if (pcc_thread_conservative_roots_enabled != 0) return 2;
    NativeFrame frame = {0};
    NativeRange ranges[2];
    PyObject *owner = 0, *borrowed = 0;
    pcc_thread_no_park_enter();
    if (pcc_gc_native_frame_enter(&frame, ranges, 2) != 0) return 3;
    /* Borrowed first deliberately exercises alias ordering. */
    void *borrowed_token = pcc_gc_native_root_bind_range(&borrowed, -1);
    void *owned_token = pcc_gc_native_root_bind_range(&owner, 1);
    pcc_thread_no_park_exit();
    owner = py_str_new("native-root", 11);
    if (!owner) return 4;
    borrowed = owner;
    /* This separate C owner deliberately remains on the old shell; the
     * native visitor transfers only `owner`, never this keeper reference. */
    py_incref(owner);
    PyObject *keeper = owner;
    PyObject *before = owner;
    int64_t source_owners = *((int64_t *)owner);
    if (source_owners != 2) return 17;
    /* The configured nursery must really classify this 52-byte string as
     * young; a nonmoving allocation cannot establish a copy/healing result. */
    if ((((int32_t *)owner)[3] & 128) == 0) return 15;
    if (pcc_gc_object_is_address_pinned(owner) != 0) return 16;
    if (pcc_gc_visit_native_root_frames(2, 0) != 2) return 5;
    if (owner == before || borrowed != owner) return 6;
    /* Oldify resets copy RC to zero. The forwarding edge retains once;
     * the single owning slot transfers once; the borrowed alias adds zero. */
    int64_t owners = 2;  /* one forwarding owner + one visited owning cell */
    if (*((int64_t *)owner) != owners) return 18;
    if (py_str_len(owner) != 11) return 7;
    if (pcc_gc_visit_native_root_frames(2, 0) != 2) return 8;
    if (*((int64_t *)owner) != owners) return 9;
    /* Seed/remark may encounter a late borrowed alias to the old shell. */
    borrowed = keeper;
    void *edge = pcc_gc_forwarding_find(keeper);
    int64_t source_after = *((int64_t *)keeper);
    int edge_matches = edge != 0 && ((void **)edge)[1] == (void *)owner;
    printf("gc3-before-gray source_rc=%lld target_rc=%lld known=%lld managed=%lld edge=%d slot_old=%d\n",
           (long long)source_after, (long long)*((int64_t *)owner),
           (long long)pcc_gc_object_is_known_no_lock(keeper),
           (long long)pcc_gc_pointer_is_managed(keeper),
           edge_matches, borrowed == before);
    if (source_after != 1 || !edge_matches) return 19;
    if (pcc_gc_visit_native_root_frames(1, 1) != 2) return 10;
    if (borrowed != owner) return 11;
    if (*((int64_t *)owner) != owners) return 12;
    borrowed = 0;
    py_decref(keeper);
    keeper = 0;
    pcc_gc_native_root_unbind(borrowed_token);
    pcc_gc_native_root_unbind(owned_token);
    pcc_gc_native_frame_leave(&frame);
    py_decref(owner);
    pcc_thread_unregister_current();
    puts("native-frame-gc3-roles-ok");
    return 0;
}
