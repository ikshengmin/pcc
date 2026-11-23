"""Bounded terminal GC1/GC2 frees, handed to the existing CMS worker after resume.

These blocks have already left every managed-object index and completed their
typed destruction. Only the allocator's physical free remains. The queue borrows
payload word zero; word eight and the allocator prefix keep their original data.
This is not concurrent object sweeping or a finalizer queue.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i32,
    atomic_load_i64,
    atomic_store_i64,
    atomic_rmw_i64,
    define_global_i64,
    define_global_ptr_null,
    define_thread_local_i32,
    define_thread_local_ptr_null,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True

_MAX_BLOCKS: i64 = 4096
_MAX_USABLE_BYTES: i64 = 1048576
_NEXT: i64 = 0
_CANDIDATE: i64 = 1024
_DEALLOCATING: i64 = 524288
_EXCLUDED: i64 = 86081  # IMMORTAL | PINNED | MINOR | FRESH | ZPAGE

# All accesses stay in this compilation owner. The native TLS word primitive
# also holds integer words, as in the thread kernel's TLS state/epoch storage.
define_thread_local_i32("pcc_gc_deferred_free_active", 0)
define_thread_local_ptr_null("pcc_gc_deferred_free_head")
define_thread_local_ptr_null("pcc_gc_deferred_free_tail")
define_thread_local_ptr_null("pcc_gc_deferred_free_pending_count")
define_thread_local_ptr_null("pcc_gc_deferred_free_pending_bytes")
define_thread_local_ptr_null("pcc_gc_deferred_free_local_attempts")
define_thread_local_ptr_null("pcc_gc_deferred_free_local_fallbacks")

# Closed scopes publish once per field, not atomically on every free. Physical
# completion is asynchronous; these are not atomic multi-field snapshots.
define_global_i64("pcc_gc_deferred_free_attempts", 0)
define_global_i64("pcc_gc_deferred_free_enqueued", 0)
define_global_i64("pcc_gc_deferred_free_drained", 0)
define_global_i64("pcc_gc_deferred_free_cap_fallbacks", 0)
define_global_i64("pcc_gc_deferred_free_peak_bytes", 0)
define_global_i64("pcc_gc_deferred_free_active_drainers", 0)
define_global_i64("pcc_gc_deferred_free_handed_off", 0)
define_global_i64("pcc_gc_deferred_free_worker_drained", 0)
define_global_i64("pcc_gc_deferred_free_local_drained", 0)
define_global_i64("pcc_gc_deferred_free_worker_thread", 0)

# One bounded mailbox, including the worker's detached batch. The graph lock
# protects the head and state transitions; atomic count/bytes also serve raw
# diagnostic reads and the worker's empty fast path. count stays nonzero until
# physical release completes: READY has a head, CLAIMED has no shared head.
define_global_ptr_null("pcc_gc_deferred_free_worker_head")
define_global_i64("pcc_gc_deferred_free_worker_count", 0)
define_global_i64("pcc_gc_deferred_free_worker_bytes", 0)




@c_abi_export("pcc_gc_deferred_free_counter")
def pcc_gc_deferred_free_counter(metric: i64) -> i64:
    # 0/1 are this thread's open scope; 2/3/5/6 describe closed scopes.
    # Physical completion (4/9/10) may lag publication. Fields are not a snapshot.
    if metric == 0:
        return load_i64(global_addr("pcc_gc_deferred_free_pending_count"), 0)
    if metric == 1:
        return load_i64(global_addr("pcc_gc_deferred_free_pending_bytes"), 0)
    if metric == 2:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_attempts"), 0, "relaxed")
    if metric == 3:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_enqueued"), 0, "relaxed")
    if metric == 4:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_drained"), 0, "relaxed")
    if metric == 5:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_cap_fallbacks"), 0, "relaxed")
    if metric == 6:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_peak_bytes"), 0, "relaxed")
    if metric == 7:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_active_drainers"), 0, "acquire")
    if metric == 8:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_handed_off"), 0, "relaxed")
    if metric == 9:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_drained"), 0, "relaxed")
    if metric == 10:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_local_drained"), 0, "relaxed")
    if metric == 11:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_count"), 0, "acquire")
    if metric == 12:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_bytes"), 0, "acquire")
    if metric == 13:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_thread"), 0, "acquire")
    return -1
